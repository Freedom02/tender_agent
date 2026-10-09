from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .config import DifyConfig
from .dify_client import DifyClient
from .normalizers import normalize_parse_result
from .run_repository import RunRepository, create_run_repository
from ..agent_runtime.contracts import build_parse_bundle
from ..bid_writing.template_fragments import attach_template_fragments_to_bundle
from ..pi_agent.client import PiClient
from ..runtime_lock import runtime_file_lock


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n]', "_", name).strip()
    return cleaned[:160] or "tender-document"


class CloudRunService:
    def __init__(
        self,
        root: Path,
        *,
        repository: RunRepository | None = None,
        client: DifyClient | None = None,
        pi_client: PiClient | None = None,
        agent_provider: str | None = None,
    ) -> None:
        self.root = root
        self.runtime_root = root / "runtime"
        self.repository = repository or create_run_repository(self.runtime_root)
        self.client = client or DifyClient()
        self.pi_client = pi_client or PiClient()
        self._agent_provider_override = (
            agent_provider.strip().lower() if isinstance(agent_provider, str) and agent_provider.strip() else None
        )
        self.agent_provider = self._current_provider()
        self._processing: set[str] = set()
        self._processing_lock = threading.Lock()

    def _current_provider(self) -> str:
        """Read the provider at run time so a long-lived API process cannot
        silently keep an obsolete provider captured during import."""
        return (
            self._agent_provider_override
            or DifyConfig.from_env().provider
            or "dify"
        ).strip().lower()

    def _run_dir(self, project_id: str, run_id: str) -> Path:
        return self.runtime_root / "projects" / (project_id or "unassigned") / "runs" / run_id

    def create_run(
        self,
        *,
        workspace_id: str,
        project_id: str,
        filename: str,
        content: bytes,
        mime_type: str = "",
    ) -> dict[str, Any]:
        provider = self._current_provider()
        request_key = hashlib.sha256(json.dumps(
            [workspace_id, project_id, filename, provider, hashlib.sha256(content).hexdigest()],
            ensure_ascii=False,
        ).encode("utf-8")).hexdigest()
        request_dir = self.runtime_root / "parse-requests"
        request_dir.mkdir(parents=True, exist_ok=True)
        request_path = request_dir / f"{request_key}.json"
        with runtime_file_lock(request_dir / f"{request_key}.lock"):
            try:
                previous_id = json.loads(request_path.read_text(encoding="utf-8")).get("runId", "")
            except (OSError, ValueError, AttributeError):
                previous_id = ""
            previous = self.repository.get(previous_id) if previous_id else None
            if (previous and previous.get("backendPid") == os.getpid()
                    and previous.get("status") in {"created", "uploading", "uploaded", "parsing"}):
                return previous
            run = self._create_run(workspace_id=workspace_id, project_id=project_id, filename=filename,
                                   content=content, mime_type=mime_type)
            request_path.write_text(json.dumps({"runId": run["runId"]}), encoding="utf-8")
            return run

    def _create_run(
        self, *, workspace_id: str, project_id: str, filename: str, content: bytes, mime_type: str,
    ) -> dict[str, Any]:
        run_id = f"bid-run-{uuid4().hex[:12]}"
        created = now_iso()
        provider = self._current_provider()
        run_dir = self._run_dir(project_id, run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        target = run_dir / safe_filename(filename)
        target.write_bytes(content)
        run = {
            "runId": run_id,
            "workspaceId": workspace_id,
            "projectId": project_id,
            "provider": provider,
            "providerSource": "explicit_override" if self._agent_provider_override else "AGENT_PROVIDER",
            "backendPid": os.getpid(),
            "status": "created",
            "phase": "parse",
            "progress": 0,
            "currentAgent": "",
            "currentAgentStatus": "",
            "createdAt": created,
            "updatedAt": created,
            "difyTaskId": "",
            "difyWorkflowRunId": "",
            "workflowType": "parse",
            "workflowVersion": "",
            "model": "",
            "parseBundleId": "",
            "parseBundleArtifact": "",
            "inputArtifact": str(target.relative_to(self.root)).replace("\\", "/"),
            "outputArtifact": "",
            "originalFilename": filename,
            "mimeType": mime_type,
            "error": None,
        }
        return self.repository.create(run)

    def process_run(self, run_id: str) -> dict[str, Any] | None:
        with self._processing_lock:
            if run_id in self._processing:
                return self.repository.get(run_id)
            current = self.repository.get(run_id)
            if not current or current.get("status") not in {"created", "uploading", "uploaded", "parsing"}:
                return current
            self._processing.add(run_id)
        try:
            return self._process_run(run_id)
        finally:
            with self._processing_lock:
                self._processing.discard(run_id)

    def _process_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.repository.get(run_id)
        if not run:
            return None
        project_id = str(run.get("projectId") or "")
        input_path = self.root / str(run.get("inputArtifact") or "")
        started = time.monotonic()
        checkpoint_path = input_path.parent / "parse-nodes.jsonl"
        checkpoint_lock = threading.RLock()
        active_nodes: dict[str, dict[str, Any]] = {}
        failed_nodes = 0
        try:
            provider = self._current_provider()
            self.repository.update(
                run_id,
                provider=provider,
                providerSource="explicit_override" if self._agent_provider_override else "AGENT_PROVIDER",
                backendPid=os.getpid(),
                status="uploading",
                startedAt=now_iso(),
                progress=10,
                currentAgent="",
                currentAgentStatus="",
                error=None,
                updatedAt=now_iso(),
            )
            self.repository.update(run_id, status="uploaded", progress=25, updatedAt=now_iso())
            self.repository.update(run_id, status="parsing", progress=35, updatedAt=now_iso())
            parse_kwargs = {
                "workspace_id": str(run.get("workspaceId") or ""),
                "project_id": project_id,
                "project_context": {
                    "title": str(run.get("originalFilename") or ""),
                    "source": "local_upload",
                    "sourceFileId": str(run.get("sourceFileId") or ""),
                },
            }
            if provider == "pi":
                def update_pi_progress(
                    agent_id: str,
                    status: str,
                    completed: int,
                    total: int,
                ) -> None:
                    safe_completed = min(max(completed, 0), max(total, 1))
                    self.repository.update(
                        run_id,
                        status="parsing",
                        progress=35 + int(61 * safe_completed / max(total, 1)),
                        currentAgent=agent_id,
                        currentAgentStatus=status,
                        elapsedSeconds=round(time.monotonic() - started, 1),
                        completedNodes=safe_completed,
                        totalNodes=total,
                        updatedAt=now_iso(),
                    )

                def save_pi_node(event: dict[str, Any]) -> None:
                    nonlocal failed_nodes
                    node_key = f"{event['agentId']}:{event['label']}"
                    with checkpoint_lock:
                        with checkpoint_path.open("a", encoding="utf-8") as stream:
                            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
                        if event["status"] == "running":
                            active_nodes[node_key] = {key: event[key] for key in ("agentId", "label", "timestamp", "attempt")}
                        elif event["status"] in {"success", "failed", "skipped"}:
                            active_nodes.pop(node_key, None)
                        elif event["status"] == "attempt_failed" and node_key in active_nodes:
                            active_nodes[node_key]["attempt"] = event["attempt"] + 1
                        if event["status"] == "failed":
                            failed_nodes += 1
                        self.repository.update(
                            run_id, nodeArtifact=str(checkpoint_path.relative_to(self.root)).replace("\\", "/"),
                            activeNodes=list(active_nodes.values()), failedNodes=failed_nodes,
                            lastNodeError=event.get("error") or "", elapsedSeconds=event["elapsedSeconds"],
                            updatedAt=now_iso(),
                        )

                parse_kwargs["progress_callback"] = update_pi_progress
                parse_kwargs["node_callback"] = save_pi_node
                response = self.pi_client.parse_tender(input_path, **parse_kwargs)
            else:
                response = self.client.parse_tender(input_path, **parse_kwargs)
            result = normalize_parse_result(response)
            self.repository.update(
                run_id,
                status="parsing",
                progress=98,
                currentAgent="ParseBundleAssembler",
                currentAgentStatus="running",
                updatedAt=now_iso(),
            )
            run_dir = self._run_dir(project_id, run_id)
            output_path = run_dir / "parse-result.json"
            result_payload = result.model_dump(mode="json")
            parse_bundle = build_parse_bundle(
                run=run,
                result=result_payload,
                provider=provider,
                root=self.root,
                status="completed",
                raw_provider_result=response if isinstance(response, dict) else {},
            )
            parse_bundle_payload = attach_template_fragments_to_bundle(
                parse_bundle,
                root=self.root,
                source_path=input_path,
            )
            if "resolvedTenderV2" in parse_bundle_payload:
                from ..agent_runtime.parse_artifact import finalize_native_parse_bundle

                parse_bundle_payload = finalize_native_parse_bundle(parse_bundle_payload)
                result_payload["resolvedTenderV2"] = parse_bundle_payload["resolvedTenderV2"]
                result_payload["parseReviewReport"] = parse_bundle_payload["parseReviewReport"]
            result_payload = {
                **result_payload,
                "templateFragments": parse_bundle_payload.get("templateFragments") or [],
                "templateFragmentsArtifact": str(
                    parse_bundle_payload.get("templateFragmentsArtifact") or ""
                ),
            }
            parse_bundle_payload = dict(parse_bundle_payload)
            if project_id:
                parse_dir = self.runtime_root / "projects" / project_id / "parse"
                parse_dir.mkdir(parents=True, exist_ok=True)
                parse_bundle_path = parse_dir / "parse-bundle.json"
            else:
                # Unassigned uploads have no stable project namespace yet.
                # Keep their ParseBundle run-scoped so concurrent uploads do
                # not overwrite one another before materialization.
                parse_bundle_path = run_dir / "parse-bundle.json"
            parse_bundle_artifact = str(parse_bundle_path.relative_to(self.root)).replace("\\", "/")
            parse_bundle_payload["artifactPath"] = parse_bundle_artifact
            parse_bundle_path.write_text(
                json.dumps(parse_bundle_payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            run_parse_bundle_path = run_dir / "parse-bundle.json"
            if run_parse_bundle_path != parse_bundle_path:
                run_parse_bundle_path.write_text(
                    json.dumps(parse_bundle_payload, ensure_ascii=False, separators=(",", ":")),
                    encoding="utf-8",
                )
            output_path.write_text(
                json.dumps(result_payload, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            self.repository.update(
                run_id,
                status="outline_ready",
                phase="parse",
                progress=100,
                currentAgent="",
                currentAgentStatus="completed",
                provider=provider,
                providerSource="explicit_override" if self._agent_provider_override else "AGENT_PROVIDER",
                backendPid=os.getpid(),
                updatedAt=now_iso(),
                difyTaskId=str(response.get("task_id") or ""),
                difyWorkflowRunId=str(response.get("workflow_run_id") or ""),
                outputArtifact=str(output_path.relative_to(self.root)).replace("\\", "/"),
                parseBundleId=str(parse_bundle_payload.get("parseBundleId") or ""),
                parseBundleArtifact=parse_bundle_artifact,
                result=result_payload,
                error=None,
                elapsedSeconds=round(time.monotonic() - started, 1),
                activeNodes=[],
            )
        except Exception as exc:
            self.repository.update(
                run_id,
                status="failed",
                progress=100,
                currentAgentStatus="failed",
                updatedAt=now_iso(),
                error=str(exc)[:2000],
                elapsedSeconds=round(time.monotonic() - started, 1),
                activeNodes=[],
            )
        return self.repository.get(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        run = self.repository.get(run_id)
        if run and run.get("status") in {"uploading", "uploaded", "parsing"}:
            try:
                started = datetime.fromisoformat(run.get("startedAt") or run["createdAt"])
                run["elapsedSeconds"] = max(0, round((datetime.now().astimezone() - started).total_seconds(), 1))
            except (ValueError, TypeError, KeyError):
                pass
        return run
