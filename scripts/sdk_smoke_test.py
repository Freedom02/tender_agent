"""Real CodeBuddy Agent SDK smoke test for the WorkBuddy document-map agent.

Run from the project root with the same Python environment that serves FastAPI:

    python scripts/sdk_smoke_test.py

Phases (each runs independently; one failure does not block the rest):

  1. SDK import + version
  2. Authentication state
  3. Model access (minimal real query)
  4. Tender Review MCP stdio startup (JSON-RPC handshake)
  5. save_document_map (direct MCP server, source validation)
  6. Full document-map runtime (real SDK, small desensitized document)
  7. Timeout recycling (tiny timeout -> process-tree termination)

Exit code 0 means every *required* phase passed.  Optional flags:

  --skip-model      skip phases 3 and 6 (no model spend / no network round-trip)
  --model NAME      override the WorkBuddy model name (default: from config / auto)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

RESULTS: list[tuple[str, str, str]] = []  # (phase, status, detail)


def record(phase: str, status: str, detail: str) -> None:
    RESULTS.append((phase, status, detail))
    print(f"[{status:^6}] {phase}: {detail}", flush=True)


def _load_workbuddy_model() -> str:
    config_path = PROJECT_ROOT / "agent-config.json"
    if config_path.is_file():
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            model = (
                payload.get("ai", {})
                .get("workbuddyAgentMcp", {})
                .get("model", "")
            )
            if model and model != "auto":
                return str(model)
        except (OSError, json.JSONDecodeError):
            pass
    return os.environ.get("WORKBUDDY_MODEL", "").strip() or "auto"


def _run_dir(name: str) -> Path:
    """Create a persistent, inspectable run directory under runtime/.

    Persistent dirs (rather than tempfile) avoid Windows cleanup races with
    still-running SDK/MCP subprocess trees and let the smoke-test artifacts be
    inspected afterwards.
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = PROJECT_ROOT / "runtime" / "sdk_smoke_test" / f"{stamp}-{name}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _small_document() -> "object":
    from backend.app.tender_matching.document_models import (
        DocumentIR,
        DocumentSection,
        DocumentSegment,
    )

    texts = [
        "第一章 技术要求",
        "平台应支持统一身份认证，具备高可用部署能力。",
        "第二章 资格要求",
        "供应商应具备 ISO9001 质量管理体系认证。",
        "投标无效：未按采购文件要求提交响应文件。",
    ]
    segments = [
        DocumentSegment(
            id=f"SEG-{index:03d}",
            text=text,
            order=index,
            sourceLocator=f"第{index}页",
        )
        for index, text in enumerate(texts, start=1)
    ]
    return DocumentIR(
        documentId="DOC-SMOKE-001",
        documentVersionId="DOC-SMOKE-001-V001",
        fileName="smoke-test.docx",
        originalFormat="docx",
        text="\n".join(texts),
        segments=segments,
        sections=[
            DocumentSection(
                id="SEC-TECH",
                sectionId="SEC-TECH",
                heading="第一章 技术要求",
                sectionPath=["第一章 技术要求"],
                segmentIds=["SEG-001", "SEG-002"],
            ),
            DocumentSection(
                id="SEC-QUAL",
                sectionId="SEC-QUAL",
                heading="第二章 资格要求",
                sectionPath=["第二章 资格要求"],
                segmentIds=["SEG-003", "SEG-004"],
            ),
        ],
    )


# ---------------------------------------------------------------------------
# Phase 1: SDK import + version
# ---------------------------------------------------------------------------
def phase_1_sdk_version() -> None:
    try:
        import codebuddy_agent_sdk

        version = getattr(codebuddy_agent_sdk, "__version__", "unknown")
        if version != "0.3.263":
            record("1.SDK version", "WARN", f"version={version}, expected 0.3.263")
        else:
            record("1.SDK version", "PASS", f"codebuddy-agent-sdk=={version}")
    except Exception as exc:
        record("1.SDK version", "FAIL", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Phase 2: Authentication state
# ---------------------------------------------------------------------------
def phase_2_auth() -> None:
    try:
        from codebuddy_agent_sdk import authenticate

        async def check() -> tuple[bool, str]:
            flow = await authenticate(timeout=30)
            if flow.auth_url:
                await flow.cancel()
                return False, "not signed in (auth URL returned, browser login required)"
            result = await flow
            name = getattr(result.userinfo, "user_name", "") or "unknown"
            return True, f"authenticated as '{name}'"

        ok, detail = asyncio.run(check())
        record("2.Authentication", "PASS" if ok else "FAIL", detail)
    except Exception as exc:
        record("2.Authentication", "FAIL", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Phase 3: Model access (minimal real query)
# ---------------------------------------------------------------------------
def phase_3_model_access(model: str) -> None:
    try:
        from codebuddy_agent_sdk import CodeBuddyAgentOptions, query

        async def run() -> tuple[bool, str]:
            from codebuddy_agent_sdk import ResultMessage

            options = CodeBuddyAgentOptions(
                model=model,
                max_turns=1,
                thinking={"type": "disabled"},
                persist_session=False,
            )
            text: list[str] = []
            async for message in query(
                prompt="Reply with exactly the two letters: OK",
                options=options,
            ):
                if type(message).__name__ == "ResultMessage":
                    if getattr(message, "is_error", False):
                        errs = [str(e) for e in (getattr(message, "errors", None) or [])]
                        return False, "result is_error: " + ("; ".join(errs) or "unknown")
                content = getattr(message, "content", None)
                if isinstance(content, list):
                    for block in content:
                        if getattr(block, "text", None):
                            text.append(str(block.text))
            if any("OK" in t for t in text):
                return True, f"model '{model}' responded"
            return False, f"model '{model}' returned no OK (got {text[:1]})"

        ok, detail = asyncio.run(run())
        record("3.Model access", "PASS" if ok else "FAIL", detail)
    except Exception as exc:
        record("3.Model access", "FAIL", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Phase 4: Tender Review MCP stdio startup
# ---------------------------------------------------------------------------
def _mcp_jsonrpc(proc: subprocess.Popen, request_id: int, method: str, params) -> dict:
    proc.stdin.write(
        json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        + "\n"
    )
    proc.stdin.flush()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


def phase_4_mcp_stdio() -> None:
    proc = None
    root = _run_dir("mcp-stdio")
    try:
        script = PROJECT_ROOT / "scripts" / "tender_review_mcp.py"
        bootstrap_path = root / "bootstrap.json"
        artifact_path = root / "mcp-audit.json"
        document = _small_document()
        bootstrap_path.write_text(
            json.dumps(
                {
                    "document": document.model_dump(mode="json", by_alias=True),
                    "run": {
                        "runId": "AR-MCP-SMOKE",
                        "documentId": document.document_id,
                        "documentVersionId": document.document_version_id,
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["TENDER_REVIEW_BOOTSTRAP_PATH"] = str(bootstrap_path)
        env["TENDER_REVIEW_ARTIFACT_PATH"] = str(artifact_path)
        env["PYTHONPATH"] = str(PROJECT_ROOT)
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        init_resp = _mcp_jsonrpc(proc, 1, "initialize", {})
        name = init_resp.get("result", {}).get("serverInfo", {}).get("name", "")
        if name != "tender-review":
            record("4.MCP stdio", "FAIL", f"initialize -> {init_resp!r}")
            return
        tools_resp = _mcp_jsonrpc(proc, 2, "tools/list", {})
        tools = tools_resp.get("result", {}).get("tools", [])
        tool_names = sorted(str(t.get("name", "")) for t in tools)
        expected = {
            "inspect_document",
            "list_section_candidates",
            "read_document_full",
            "read_document_range",
            "read_section",
            "search_document",
            "read_table_range",
            "read_template_range",
            "save_document_map",
            "record_unresolved_issue",
            "record_candidate_finding",
        }
        missing = sorted(expected - set(tool_names))
        if missing:
            record("4.MCP stdio", "FAIL", f"missing tools: {missing}")
            return
        ping_resp = _mcp_jsonrpc(proc, 3, "ping", {})
        if "result" not in ping_resp:
            record("4.MCP stdio", "FAIL", f"ping -> {ping_resp!r}")
            return
        record(
            "4.MCP stdio",
            "PASS",
            f"initialize+tools/list({len(tools)} tools)+ping OK",
        )
    except Exception as exc:
        record("4.MCP stdio", "FAIL", f"{type(exc).__name__}: {exc}")
    finally:
        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------------------
# Phase 5: save_document_map (direct MCP server)
# ---------------------------------------------------------------------------
def phase_5_save_document_map() -> None:
    try:
        from backend.app.workbuddy_agent.tender_review_mcp import TenderReviewMCPServer

        document = _small_document()
        server = TenderReviewMCPServer(document)
        inspected = server.call_tool(
            "inspect_document",
            {
                "documentId": document.document_id,
                "documentVersionId": document.document_version_id,
            },
        )
        if "error" in inspected:
            record("5.save_document_map", "FAIL", f"inspect -> {inspected['error']}")
            return
        document_map = {
            "schemaVersion": "tender-document-map/v1",
            "mapId": "MAP-SMOKE-001",
            "runId": server.run.run_id,
            "documentId": document.document_id,
            "documentVersionId": document.document_version_id,
            "documentType": "government_tender",
            "sections": [
                {
                    "mapId": "MSEC-SMOKE-TECH",
                    "runId": server.run.run_id,
                    "documentId": document.document_id,
                    "documentVersionId": document.document_version_id,
                    "domain": "technical",
                    "role": "leaf",
                    "processingMode": "fine_extract",
                    "title": "技术要求",
                    "sectionIds": ["SEC-TECH"],
                    "sourceSegmentIds": ["SEG-001", "SEG-002"],
                    "ranges": [
                        {
                            "rangeId": "RNG-SMOKE-TECH",
                            "segmentIds": ["SEG-001", "SEG-002"],
                            "contentSummary": "技术要求正文",
                        }
                    ],
                    "primaryRoute": "technical_processor",
                    "priority": "high",
                    "confidence": 0.95,
                    "classificationSource": "agent",
                },
                {
                    "mapId": "MSEC-SMOKE-QUAL",
                    "runId": server.run.run_id,
                    "documentId": document.document_id,
                    "documentVersionId": document.document_version_id,
                    "domain": "qualification",
                    "role": "leaf",
                    "processingMode": "fine_extract",
                    "title": "资格要求",
                    "sectionIds": ["SEC-QUAL"],
                    "sourceSegmentIds": ["SEG-003", "SEG-004"],
                    "ranges": [
                        {
                            "rangeId": "RNG-SMOKE-QUAL",
                            "segmentIds": ["SEG-003", "SEG-004"],
                            "contentSummary": "资格要求正文",
                        }
                    ],
                    "primaryRoute": "qualification_processor",
                    "priority": "high",
                    "confidence": 0.95,
                    "classificationSource": "agent",
                },
                {
                    "mapId": "MSEC-SMOKE-REJECT",
                    "runId": server.run.run_id,
                    "documentId": document.document_id,
                    "documentVersionId": document.document_version_id,
                    "domain": "rejection",
                    "role": "overlay",
                    "processingMode": "full_scan",
                    "title": "废标风险",
                    "sectionIds": [],
                    "sourceSegmentIds": ["SEG-005"],
                    "ranges": [
                        {
                            "rangeId": "RNG-SMOKE-REJECT",
                            "segmentIds": ["SEG-005"],
                            "contentSummary": "废标风险信号",
                        }
                    ],
                    "primaryRoute": "rejection_processor",
                    "priority": "high",
                    "confidence": 0.9,
                    "classificationSource": "agent",
                },
            ],
            "ignoredSections": [],
            "rejectionCandidates": [
                {
                    "candidateId": "RC-SMOKE-001",
                    "runId": server.run.run_id,
                    "documentId": document.document_id,
                    "matchedTerm": "投标无效",
                    "text": "投标无效：未按采购文件要求提交响应文件。",
                    "contextText": "投标无效：未按采购文件要求提交响应文件。",
                    "source": {"sourceFormat": "docx", "segmentIds": ["SEG-005"]},
                    "contextSegmentIds": ["SEG-005"],
                    "status": "candidate",
                    "isHardRisk": True,
                    "reason": "需要上下文确认",
                }
            ],
            "unresolvedSections": [],
            "unresolvedIssues": [],
            "candidateFindings": [],
            "warnings": [],
        }
        saved = server.call_tool(
            "save_document_map",
            {
                "documentId": document.document_id,
                "documentVersionId": document.document_version_id,
                "documentMap": document_map,
            },
        )
        if "error" in saved:
            record("5.save_document_map", "FAIL", f"save -> {saved['error']}")
            return
        record(
            "5.save_document_map",
            "PASS",
            f"saved mapId={saved.get('mapId')} sections={saved.get('sectionCount')}",
        )
    except Exception as exc:
        record("5.save_document_map", "FAIL", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Phase 6: Full document-map runtime (real SDK)
# ---------------------------------------------------------------------------
def phase_6_full_runtime(model: str) -> None:
    try:
        from backend.app.workbuddy_agent.workflow.runtime import (
            DocumentMapAgentRuntime,
            DocumentMapRuntimeConfig,
        )

        document = _small_document()
        config = DocumentMapRuntimeConfig(
            runtimeDir=str(_run_dir("full-runtime")),
            model=model,
            timeoutSeconds=150,
            maxRetries=0,
        )
        runtime = DocumentMapAgentRuntime(document, config=config)
        result = runtime.run()
        status = result.get("status")
        errors = result.get("errors") or []
        if status in {"completed", "needs_review"}:
            record(
                "6.Full runtime",
                "PASS",
                f"status={status} mapId={result.get('documentMap', {}).get('mapId')}",
            )
        elif errors:
            record("6.Full runtime", "FAIL", f"status={status} errors={errors[:2]}")
        else:
            record("6.Full runtime", "FAIL", f"status={status}")
    except Exception as exc:
        record("6.Full runtime", "FAIL", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Phase 7: Timeout recycling
# ---------------------------------------------------------------------------
def _headless_process_count() -> int:
    """Count lingering codebuddy-headless.exe processes (the SDK child)."""
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq codebuddy-headless.exe", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return sum(1 for line in out.stdout.splitlines() if "codebuddy-headless" in line)
    except (OSError, subprocess.SubprocessError):
        return -1


def phase_7_timeout(model: str) -> None:
    try:
        from backend.app.workbuddy_agent.workflow.runtime import (
            DocumentMapAgentRuntime,
            DocumentMapRuntimeConfig,
        )

        document = _small_document()
        before = _headless_process_count()
        started = time.monotonic()
        config = DocumentMapRuntimeConfig(
            runtimeDir=str(_run_dir("timeout")),
            model=model,
            timeoutSeconds=5,
            maxRetries=0,
        )
        runtime = DocumentMapAgentRuntime(document, config=config)
        result = runtime.run()
        elapsed = time.monotonic() - started
        after = _headless_process_count()
        errors = [str(e) for e in (result.get("errors") or [])]
        timed_out = any("exceeded" in e and "terminated" in e for e in errors)
        bounded = elapsed < 90
        if result.get("status") == "failed" and (timed_out or bounded):
            record(
                "7.Timeout recycle",
                "PASS",
                f"bounded return in {round(elapsed, 1)}s; "
                f"headless before={before} after={after}; err={errors[:1]}",
            )
        else:
            record(
                "7.Timeout recycle",
                "FAIL",
                f"status={result.get('status')} errors={errors[:2]} "
                f"elapsed={round(elapsed,1)}s headless before={before} after={after}",
            )
    except Exception as exc:
        record("7.Timeout recycle", "FAIL", f"{type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="CodeBuddy SDK smoke test")
    parser.add_argument("--skip-model", action="store_true", help="skip model phases 3/6")
    parser.add_argument("--model", default=None, help="override WorkBuddy model name")
    args = parser.parse_args()

    model = args.model or _load_workbuddy_model()
    print(f"=== SDK smoke test | python={sys.executable} | model={model} ===", flush=True)

    phase_1_sdk_version()
    phase_2_auth()
    if args.skip_model:
        record("3.Model access", "SKIP", "--skip-model")
        record("6.Full runtime", "SKIP", "--skip-model")
    else:
        phase_3_model_access(model)
        phase_6_full_runtime(model)
    phase_4_mcp_stdio()
    phase_5_save_document_map()
    phase_7_timeout(model)

    print("\n=== Summary ===", flush=True)
    failed = 0
    for phase, status, detail in RESULTS:
        print(f"  {status:^6} {phase}: {detail}", flush=True)
        if status == "FAIL":
            failed += 1
    print(f"\n{len(RESULTS)} phases, {failed} failed.", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
