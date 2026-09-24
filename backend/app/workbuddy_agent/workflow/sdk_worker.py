"""Killable worker entry point for the CodeBuddy Python Agent SDK."""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from .runtime import (
    DocumentMapAgentTask,
    DocumentMapRuntimeConfig,
    WorkBuddySdkExecutor,
)
from .schemas import AgentRun
from ...tender_matching.document_models import DocumentIR


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SDK worker request must be a JSON object.")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    temporary.replace(path)


def _task_from_payload(payload: dict[str, Any]) -> DocumentMapAgentTask:
    return DocumentMapAgentTask(
        run=AgentRun.model_validate(payload["run"]),
        document=DocumentIR.model_validate(payload["document"]),
        # The external MCP process owns the observable tool audit.  The
        # inline executor only needs an empty local audit collection.
        server=SimpleNamespace(tool_calls=[]),
        config=DocumentMapRuntimeConfig.model_validate(payload["config"]),
        skill_dir=Path(str(payload["skillDir"])),
        mcp_config_path=Path(str(payload["mcpConfigPath"])),
        bootstrap_path=Path(str(payload["bootstrapPath"])),
        system_prompt_path=Path(str(payload["systemPromptPath"])),
        run_dir=Path(str(payload["runDir"])),
        system_prompt=str(payload.get("systemPrompt") or ""),
        user_prompt=str(payload.get("userPrompt") or ""),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", required=True)
    parser.add_argument("--result", required=True)
    args = parser.parse_args(argv)
    result_path = Path(args.result)
    os.environ["WORKBUDDY_SDK_WORKER"] = "1"
    try:
        payload = _read_json(Path(args.request))
        task = _task_from_payload(payload)
        result = WorkBuddySdkExecutor(
            config_dir=Path(str(payload["configDir"])),
        )._execute_inline(task)
        output = dict(result or {})
        output.setdefault("output", {})
        if isinstance(output["output"], dict):
            output["output"]["sdkWorkerPid"] = os.getpid()
        _write_json(result_path, output)
        return 0
    except Exception as error:
        error_payload = {
            "documentMap": None,
            "toolCalls": [],
            "unresolvedIssues": [],
            "candidateFindings": [],
            "turnCount": 0,
            "warnings": [],
            "errors": [f"{type(error).__name__}: {error}"],
            "output": {
                "executor": "codebuddy_agent_sdk",
                "isolatedWorker": True,
            },
        }
        _write_json(result_path, error_payload)
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
