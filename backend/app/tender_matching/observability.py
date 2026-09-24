"""Run-level observability for tender analysis.

The matching pipeline remains synchronous and filesystem-backed.  This module
adds a small, typed run record around it without changing the business result
contract or persisting prompts, API keys, or full document text.
"""
from __future__ import annotations

import contextvars
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any, Callable, TypeVar

from .fact_models import AnalysisRun, AnalysisStage, FactArtifactStore


PROMPT_VERSION = "builtin-prompts/v1"
PARSER_VERSION = "builtin-parser/v1"

_T = TypeVar("_T")
_ACTIVE_TELEMETRY: contextvars.ContextVar["LLMCallTelemetry | None"] = (
    contextvars.ContextVar("tender_llm_telemetry", default=None)
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def document_version_id(
    document: dict[str, Any] | None,
    *,
    source_file_id: str = "",
) -> str:
    values = document or {}
    explicit = str(values.get("documentVersionId") or "").strip()
    if explicit:
        return explicit
    content_hash = str(values.get("contentHash") or "").strip()
    if content_hash:
        return f"DOC-{content_hash[:12]}-V001"
    source = str(source_file_id or values.get("id") or "document").strip()
    return f"{source}-V001"


def prompt_version(ai: dict[str, Any] | None) -> str:
    return str((ai or {}).get("promptVersion") or PROMPT_VERSION).strip()


def parser_version(document: dict[str, Any] | None) -> str:
    metadata = (document or {}).get("metadata")
    if isinstance(metadata, dict) and metadata.get("parserVersion"):
        return str(metadata["parserVersion"])
    return PARSER_VERSION


@dataclass
class LLMCallTelemetry:
    """Compact per-run LLM counters; prompts and response bodies are excluded."""

    calls: list[dict[str, Any]] = field(default_factory=list)

    def record(self, result: dict[str, Any] | None) -> None:
        payload = result if isinstance(result, dict) else {}
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        success = bool(payload.get("success"))
        self.calls.append(
            {
                "kind": "llm",
                "success": success,
                "model": str(payload.get("model") or ""),
                "error": str(payload.get("error") or "")[:300],
                "finishReason": str(payload.get("finishReason") or ""),
                "inputTokens": int(
                    usage.get("prompt_tokens")
                    or usage.get("input_tokens")
                    or 0
                ),
                "outputTokens": int(
                    usage.get("completion_tokens")
                    or usage.get("output_tokens")
                    or 0
                ),
            }
        )

    def record_external(self, *, model: str = "", success: bool = True) -> None:
        """Count an external agent/provider call not routed through HTTP chat."""

        self.calls.append(
            {
                "kind": "external_agent",
                "success": bool(success),
                "model": model,
                "error": "",
                "finishReason": "",
                "inputTokens": 0,
                "outputTokens": 0,
            }
        )

    def summary(self) -> dict[str, int]:
        return {
            "llmCallCount": len(self.calls),
            "llmSuccessCount": sum(1 for call in self.calls if call["success"]),
            "llmFailureCount": sum(1 for call in self.calls if not call["success"]),
            "llmInputTokens": sum(int(call["inputTokens"]) for call in self.calls),
            "llmOutputTokens": sum(int(call["outputTokens"]) for call in self.calls),
            "externalAgentCallCount": sum(
                1 for call in self.calls if call["kind"] == "external_agent"
            ),
        }


def observe_llm_call(function: Callable[..., _T]) -> Callable[..., _T]:
    """Decorate an LLM gateway so active analysis runs count calls centrally."""

    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> _T:
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            telemetry = _ACTIVE_TELEMETRY.get()
            if telemetry is not None:
                telemetry.record({"success": False, "error": str(exc)})
            raise
        telemetry = _ACTIVE_TELEMETRY.get()
        if telemetry is not None:
            telemetry.record(result if isinstance(result, dict) else {})
        return result

    return wrapped


class AnalysisRunRecorder:
    """Create and persist one observable analysis run."""

    def __init__(
        self,
        runtime_root: str | Path,
        *,
        project_id: str,
        document_version_id: str,
        run_id: str,
        parser_name: str = "",
        parser_version_value: str = PARSER_VERSION,
        prompt_version_value: str = PROMPT_VERSION,
        llm_model: str = "",
        llm_parameters: dict[str, Any] | None = None,
        input_artifact_paths: list[str] | None = None,
    ) -> None:
        self.project_id = str(project_id or "")
        self._store = FactArtifactStore(runtime_root)
        self._started_perf = time.perf_counter()
        self._telemetry = LLMCallTelemetry()
        self._telemetry_token = _ACTIVE_TELEMETRY.set(self._telemetry)
        self._finished = False
        started = utc_now()
        self.run = AnalysisRun(
            runId=run_id,
            projectId=self.project_id,
            documentVersionId=document_version_id,
            status="running",
            createdAt=started,
            startedAt=started,
            parserName=parser_name,
            parserVersion=parser_version_value,
            promptVersion=prompt_version_value,
            llmModel=llm_model,
            llmParameters=llm_parameters or {},
            inputArtifactPaths=list(dict.fromkeys(input_artifact_paths or [])),
        )
        self.save()

    @property
    def run_id(self) -> str:
        return self.run.run_id

    @property
    def path(self) -> Path:
        return self._store.run_path(self.project_id, self.run.run_id)

    def save(self) -> Path:
        return self._store.save_run(self.project_id, self.run)

    def add_warning(self, warning: Any) -> None:
        text = str(warning or "").strip()
        if text and text not in self.run.warnings:
            self.run.warnings.append(text)

    def add_error(self, error: Any) -> None:
        text = str(error or "").strip()
        if text and text not in self.run.errors:
            self.run.errors.append(text)

    def add_artifacts(
        self,
        *,
        input_paths: list[str] | None = None,
        output_paths: list[str] | None = None,
    ) -> None:
        for target, values in (
            (self.run.input_artifact_paths, input_paths or []),
            (self.run.output_artifact_paths, output_paths or []),
        ):
            for value in values:
                text = str(value or "").strip()
                if text and text not in target:
                    target.append(text)

    def record_stage(
        self,
        stage: str,
        *,
        status: str = "completed",
        started_at: str = "",
        finished_at: str = "",
        elapsed_ms: int = 0,
        retry_count: int = 0,
        input_paths: list[str] | None = None,
        output_paths: list[str] | None = None,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> AnalysisStage:
        record = AnalysisStage(
            stage=stage,
            status=status,
            startedAt=started_at or utc_now(),
            finishedAt=finished_at or utc_now(),
            elapsedMs=max(0, int(elapsed_ms)),
            retryCount=max(0, int(retry_count)),
            inputArtifactPaths=list(dict.fromkeys(input_paths or [])),
            outputArtifactPaths=list(dict.fromkeys(output_paths or [])),
            warnings=list(dict.fromkeys(str(item) for item in (warnings or []) if str(item).strip())),
            errors=list(dict.fromkeys(str(item) for item in (errors or []) if str(item).strip())),
            metrics=metrics or {},
        )
        self.run.stages.append(record)
        self.add_artifacts(input_paths=input_paths, output_paths=output_paths)
        for warning in record.warnings:
            self.add_warning(warning)
        for error in record.errors:
            self.add_error(error)
        return record

    def set_metrics(self, values: dict[str, Any] | None = None) -> None:
        if values:
            self.run.metrics.update(values)

    def set_manual_change_count(self, count: int) -> None:
        self.run.manual_change_count = max(0, int(count))

    def record_external_model_call(self, *, model: str = "", success: bool = True) -> None:
        self._telemetry.record_external(model=model, success=success)

    def finish(self, status: str = "completed") -> AnalysisRun:
        if self._finished:
            return self.run
        telemetry = self._telemetry.summary()
        self.run.status = status
        self.run.finished_at = utc_now()
        self.run.elapsed_ms = max(0, round((time.perf_counter() - self._started_perf) * 1000))
        self.run.llm_call_count = telemetry["llmCallCount"]
        self.run.llm_success_count = telemetry["llmSuccessCount"]
        self.run.llm_failure_count = telemetry["llmFailureCount"]
        self.run.llm_input_tokens = telemetry["llmInputTokens"]
        self.run.llm_output_tokens = telemetry["llmOutputTokens"]
        self.run.metrics.update(telemetry)
        self._finished = True
        try:
            _ACTIVE_TELEMETRY.reset(self._telemetry_token)
        finally:
            self.save()
        return self.run

    def fail(self, error: Any, *, stage: str = "analysis") -> AnalysisRun:
        self.add_error(error)
        if not any(item.stage == stage and item.status == "failed" for item in self.run.stages):
            self.record_stage(
                stage,
                status="failed",
                errors=[str(error)],
            )
        return self.finish("failed")
