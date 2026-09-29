"""Real-document regression: builtin vs workbuddy_agent_mcp extraction modes.

Run from the repository root with the project's configured Python environment:

    python scripts/evaluate_extraction_modes.py --input-dir demo-data --limit 3

Compares the two extraction modes on the same parsed DocumentIR:

  * builtin            : rule + optional LLM target map + clause extraction
                         (``extract_builtin_target_document`` / ``evaluate_tender_document``)
  * workbuddy_agent_mcp: autonomous WorkBuddy agent -> DocumentMap ->
                         scope processing + target requirement extraction
                         (``evaluate_tender_document_with_workbuddy_agent``)

Metrics (documented per route):

  1. map coverage           covered segments / total segments
  2. source accuracy        valid source segment refs / total source refs
  3. rejection recognition  rejection signals that overlap the deterministic
                            high-risk scan / high-risk scan hits
  4. unresolved rate        needs-human-review signals / total sections
  5. elapsed seconds        wall-clock extraction time
  6. cost usd               SDK ``totalCostUsd`` (workbuddy) / LLM usage (builtin)

The workbuddy route needs CodeBuddy auth + model access and Windows named-pipe
subprocess spawning, so it must run outside the DSH workspace sandbox (the
same ``danger-full-access`` requirement as the smoke test).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402
from backend.app.tender_matching.builtin_target_processing import (  # noqa: E402
    extract_builtin_target_document,
)
from backend.app.workbuddy_agent.workflow.integration import (  # noqa: E402
    evaluate_tender_document_with_workbuddy_agent,
)
from backend.app.workbuddy_agent.workflow.runtime import _high_risk_segment_ids  # noqa: E402

MIME_BY_SUFFIX = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".txt": "text/plain",
}
DOC_SUFFIXES = tuple(MIME_BY_SUFFIX)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mime(path: Path) -> str:
    return MIME_BY_SUFFIX.get(path.suffix.lower(), "text/plain")


def _segments(document: Any) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json", by_alias=True) for item in document.segments]


def _sections(document: Any) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json", by_alias=True) for item in document.sections]


def _tables(document: Any) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json", by_alias=True) for item in document.tables]


def _ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _load_ai_config() -> dict[str, Any]:
    path = REPO_ROOT / "agent-config.json"
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8")).get("ai", {})
        except (OSError, json.JSONDecodeError):
            pass
    return {}


# ---------------------------------------------------------------------------
# Builtin route
# ---------------------------------------------------------------------------
def _run_builtin(
    document: Any,
    segments: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    tables: list[dict[str, Any]],
    ai: dict[str, Any],
    source_file_id: str,
    content_hash: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    extraction = extract_builtin_target_document(
        text=document.text,
        segments=segments,
        sections=sections,
        tables=tables,
        ai=ai,
        source_file_id=source_file_id,
        project_id="real-tender-regression",
        document_meta={"fileName": source_file_id, "contentHash": content_hash},
    )
    elapsed = round(time.perf_counter() - started, 3)
    targets = {
        str(item.get("targetModule")): item
        for item in (extraction.get("targetModules") or [])
        if isinstance(item, dict) and item.get("targetModule")
    }
    total_segments = len(document.segments) or 1
    segment_ids = {str(item.id) for item in document.segments if item.id}
    covered: set[str] = set()
    total_refs = 0
    valid_refs = 0
    for target_key, target in targets.items():
        if target_key == "project_info":
            continue
        for segment_id in _ids(target.get("segmentIds")):
            total_refs += 1
            if segment_id in segment_ids:
                valid_refs += 1
                covered.add(segment_id)
        for item in (target.get("items") or []):
            if not isinstance(item, dict):
                continue
            for segment_id in _ids(
                item.get("sourceSegmentIds")
                or item.get("segmentIds")
                or item.get("sourceSegmentId")
            ):
                total_refs += 1
                if segment_id in segment_ids:
                    valid_refs += 1
                    covered.add(segment_id)
    rejection = targets.get("rejection") or {}
    rejection_ids = set(_ids(rejection.get("segmentIds")))
    for item in (rejection.get("items") or []):
        if isinstance(item, dict):
            rejection_ids.update(
                _ids(
                    item.get("sourceSegmentIds")
                    or item.get("segmentIds")
                    or item.get("sourceSegmentId")
                )
            )
    item_count = sum(
        len(target.get("items") or [])
        for target in targets.values()
        if isinstance(target, dict)
    )
    unresolved = sum(
        1
        for target in targets.values()
        if isinstance(target, dict)
        for item in (target.get("items") or [])
        if isinstance(item, dict)
        and (item.get("reviewRequired") or item.get("needsHumanReview"))
    )
    return {
        "elapsed": elapsed,
        "mapCoverage": round(len(covered) / total_segments, 4),
        "sourceAccuracy": round(valid_refs / total_refs, 4) if total_refs else 1.0,
        "totalRefs": total_refs,
        "validRefs": valid_refs,
        "coveredSegments": len(covered),
        "rejectionSegments": sorted(rejection_ids),
        "rejectionCount": len(rejection_ids),
        "itemCount": item_count,
        "unresolved": unresolved,
        "targetModuleCounts": {
            key: len(target.get("items") or []) if isinstance(target, dict) else 0
            for key, target in targets.items()
        },
    }


# ---------------------------------------------------------------------------
# WorkBuddy agent route
# ---------------------------------------------------------------------------
def _run_workbuddy(
    document: Any,
    ai: dict[str, Any],
    source_file_id: str,
    content_hash: str,
) -> dict[str, Any]:
    payload = document.model_dump(mode="json", by_alias=True)
    wb = dict(ai.get("workbuddyAgentMcp") or {})
    wb.setdefault("runtimeDir", "runtime/evaluate_extraction_modes")
    # Real tender documents (hundreds of segments) exceed the conservative
    # smoke-test budget. Lock these explicitly so a concurrent config save
    # cannot silently reset them mid-run.
    wb["maxTurns"] = 100
    wb["maxToolCalls"] = 200
    wb["timeoutSeconds"] = 1800
    # Retry once with a fresh MCP server: large docs can make the stdio server
    # answer tools/list too late, which surfaces as a transient
    # "Tool inspect_document not found" flake (~25%).
    wb["maxRetries"] = 1
    # Lock the UINO route: deepseek-v3 via token.uino.com (env-overridable).
    # Force these regardless of a concurrent config save, which otherwise
    # resets the model to CodeBuddy.cn's exhausted-quota account.
    wb["model"] = os.getenv("WORKBUDDY_MODEL", "deepseek-v3")
    wb["settingSources"] = ["user"]
    # deepseek-v4-* are reasoning models: UINO returns reasoning_content and
    # demands it back on the next turn, but the CodeBuddy CLI's chat/completions
    # path does not round-trip it. Route v4 through a local proxy that injects
    # reasoning_effort:"none" so the model answers in plain mode.
    use_proxy = wb["model"].startswith("deepseek-v4")
    wb["thinkingMode"] = "disabled"  # reasoning is suppressed at the gateway
    proxy_url = os.getenv("UINO_PROXY", "http://127.0.0.1:18800")
    base_url = proxy_url if use_proxy else (ai.get("baseUrl") or "")
    workbuddy_ai = {
        "model": ai.get("model") or "",
        "baseUrl": base_url,
        "apiKey": ai.get("apiKey") or "",
        "workbuddyAgentMcp": wb,
    }
    started = time.perf_counter()
    result = evaluate_tender_document_with_workbuddy_agent(
        text=document.text,
        document=payload,
        company_knowledge={},
        ai=workbuddy_ai,
        source_file_id=source_file_id,
        project_id="real-tender-regression",
        document_meta={"fileName": source_file_id, "contentHash": content_hash},
    )
    elapsed = round(time.perf_counter() - started, 3)
    document_map = result.get("documentMap") or {}
    total_segments = len(document.segments) or 1
    segment_ids = {str(item.id) for item in document.segments if item.id}

    coverage = document_map.get("coverage") or {}
    covered = set(_ids(coverage.get("coveredSegmentIds")))
    if not covered:
        covered = {
            segment_id
            for section in document_map.get("sections") or []
            for segment_id in _ids(section.get("sourceSegmentIds"))
        }
    total_refs = 0
    valid_refs = 0
    for section in document_map.get("sections") or []:
        for segment_id in _ids(section.get("sourceSegmentIds")):
            total_refs += 1
            if segment_id in segment_ids:
                valid_refs += 1
        for rng in section.get("ranges") or []:
            for segment_id in _ids(rng.get("segmentIds")):
                total_refs += 1
                if segment_id in segment_ids:
                    valid_refs += 1
    rejection_ids: set[str] = set()
    for candidate in document_map.get("rejectionCandidates") or []:
        source = candidate.get("source") or {}
        rejection_ids.update(_ids(source.get("segmentIds")))
        rejection_ids.update(_ids(candidate.get("contextSegmentIds")))
    unresolved_sections = _ids(document_map.get("unresolvedSections"))
    unresolved_issues = document_map.get("unresolvedIssues") or []
    runtime = result.get("runtime") or {}
    sdk = (runtime.get("output") or {}).get("sdk") or {}
    cost_usd = sdk.get("totalCostUsd")
    duration_api_ms = sdk.get("durationApiMs") or 0
    return {
        "elapsed": elapsed,
        "mapCoverage": round(len(covered) / total_segments, 4),
        "sourceAccuracy": round(valid_refs / total_refs, 4) if total_refs else 1.0,
        "totalRefs": total_refs,
        "validRefs": valid_refs,
        "coveredSegments": len(covered),
        "rejectionSegments": sorted(rejection_ids),
        "rejectionCount": len(rejection_ids),
        "itemCount": (
            (result.get("targetProcessing") or {}).get("extraction") or {}
        ).get("requirementCount")
        or len((result.get("targetProcessing") or {}).get("extraction", {}).get("requirements") or []),
        "unresolved": len(unresolved_sections) + len(unresolved_issues),
        "unresolvedSections": len(unresolved_sections),
        "unresolvedIssues": len(unresolved_issues),
        "targetModuleCounts": {
            key: (document_map.get("coverage") or {}).get("domainCounts", {}).get(key, 0)
            for key in ("technical", "qualification", "commercial", "evaluation", "rejection", "template", "contract", "project_info")
        },
        "costUsd": cost_usd,
        "durationApiMs": duration_api_ms,
        "agentStatus": result.get("fineReview", {}).get("extraction", {}).get("agentStatus") or "",
        "mapId": document_map.get("mapId") or "",
    }


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------
def _rejection_recognition(route: dict[str, Any], high_risk: set[str]) -> float:
    if not high_risk:
        return 1.0
    overlap = high_risk & set(route["rejectionSegments"])
    return round(len(overlap) / len(high_risk), 4)


def _evaluate(path: Path, ai: dict[str, Any], builtin_llm: bool, mode: str) -> dict[str, Any]:
    content = path.read_bytes()
    document = parse_document_ir(
        content,
        file_name=path.name,
        mime_type=_mime(path),
    )
    content_hash = hashlib.sha256(content).hexdigest()
    segments = _segments(document)
    sections = _sections(document)
    tables = _tables(document)
    high_risk = set(_high_risk_segment_ids(document))
    source_file_id = path.name

    result: dict[str, Any] = {
        "file": str(path),
        "fileName": path.name,
        "totalSegments": len(document.segments),
        "totalSections": len(document.sections),
        "totalTables": len(document.tables),
        "highRiskSegmentCount": len(high_risk),
        "highRiskSegments": sorted(high_risk),
    }
    total_sections = len(document.sections) or 1

    if mode in {"builtin", "both"}:
        builtin_ai = {**ai, "enabled": True} if builtin_llm else {"enabled": False}
        builtin = _run_builtin(
            document, segments, sections, tables, builtin_ai, source_file_id, content_hash
        )
        builtin["rejectionRecognition"] = _rejection_recognition(builtin, high_risk)
        builtin["unresolvedRate"] = round(builtin["unresolved"] / total_sections, 4)
        result["builtin"] = builtin

    if mode in {"workbuddy", "both"}:
        workbuddy = _run_workbuddy(document, ai, source_file_id, content_hash)
        workbuddy["rejectionRecognition"] = _rejection_recognition(workbuddy, high_risk)
        workbuddy["unresolvedRate"] = round(workbuddy["unresolved"] / total_sections, 4)
        result["workbuddy"] = workbuddy

    return result


def _round(value: Any, digits: int = 3) -> Any:
    if isinstance(value, float):
        return round(value, digits)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="builtin vs workbuddy_agent_mcp regression")
    parser.add_argument("--input-dir", default=str(REPO_ROOT / "demo-data"))
    parser.add_argument("--docs", nargs="*", default=None, help="specific files to evaluate")
    parser.add_argument("--limit", type=int, default=0, help="max documents to evaluate (0 = all)")
    parser.add_argument("--output-dir", default=str(REPO_ROOT / "evaluation"))
    parser.add_argument("--builtin-llm", action="store_true", help="enable LLM in the builtin route")
    parser.add_argument(
        "--mode",
        choices=["both", "builtin", "workbuddy"],
        default="both",
        help="which routes to run (default: both)",
    )
    args = parser.parse_args()

    ai = _load_ai_config()
    input_dir = Path(args.input_dir)
    if args.docs:
        paths = [input_dir / name if not Path(name).is_absolute() else Path(name) for name in args.docs]
    else:
        paths = sorted(
            path for path in input_dir.rglob("*")
            if path.suffix.lower() in DOC_SUFFIXES
            and not path.name.startswith("~$")
        )
    paths = [path for path in paths if path.is_file()]
    if args.limit:
        paths = paths[: args.limit]

    if not paths:
        print(f"No documents found under {input_dir}")
        return 2

    samples: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, path in enumerate(paths, start=1):
        print(f"[{index}/{len(paths)}] {path.name}", flush=True)
        try:
            samples.append(_evaluate(path, ai, args.builtin_llm, args.mode))
        except Exception as exc:
            failures.append({"file": str(path), "error": f"{type(exc).__name__}: {exc}"})
            print(f"  FAILED: {type(exc).__name__}: {exc}", flush=True)

    report = {
        "schemaVersion": "extraction-modes-regression/v1",
        "generatedAt": _now_iso(),
        "pythonExecutable": sys.executable,
        "inputDir": str(input_dir),
        "sampleCount": len(samples),
        "failureCount": len(failures),
        "failures": failures,
        "samples": samples,
        "aggregate": _aggregate(samples),
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"extraction_modes_regression_{stamp}.json"
    md_path = output_dir / f"extraction_modes_regression_{stamp}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(report), encoding="utf-8")
    print(f"\nJSON: {json_path}")
    print(f"MD:   {md_path}")
    print(json.dumps(report["aggregate"], ensure_ascii=False, indent=2))
    return 1 if failures else 0


def _aggregate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    if not samples:
        return {}
    metric_keys = [
        "mapCoverage",
        "sourceAccuracy",
        "rejectionRecognition",
        "unresolvedRate",
        "elapsed",
        "costUsd",
    ]

    def _avg(key: str, route: str) -> float | None:
        values = [
            sample.get(route, {}).get(key)
            for sample in samples
            if isinstance(sample.get(route, {}).get(key), (int, float))
        ]
        return round(sum(values) / len(values), 4) if values else None

    result: dict[str, Any] = {}
    for key in metric_keys:
        result[f"builtin_{key}"] = _avg(key, "builtin")
        result[f"workbuddy_{key}"] = _avg(key, "workbuddy")
    return result


def _markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# builtin vs workbuddy_agent_mcp 真实文档回归评测\n")
    lines.append(f"> 生成时间：{report['generatedAt']}")
    lines.append(f"> Python：`{report['pythonExecutable']}`")
    lines.append(f"> 样本：{report['sampleCount']} 份，失败 {report['failureCount']} 份\n")
    agg = report["aggregate"]
    lines.append("## 汇总（平均值）\n")
    lines.append("| 指标 | builtin | workbuddy_agent_mcp |")
    lines.append("|---|---:|---:|")
    labels = {
        "mapCoverage": "地图覆盖率",
        "sourceAccuracy": "来源准确率",
        "rejectionRecognition": "废标识别率",
        "unresolvedRate": "未解决问题率",
        "elapsed": "耗时(秒)",
        "costUsd": "成本(USD)",
    }
    for key, label in labels.items():
        b = agg.get(f"builtin_{key}")
        w = agg.get(f"workbuddy_{key}")
        lines.append(f"| {label} | {_fmt(b)} | {_fmt(w)} |")
    lines.append("")
    lines.append("## 文档明细\n")
    for sample in report["samples"]:
        lines.append(f"### {sample['fileName']}\n")
        lines.append(f"- 片段 {sample['totalSegments']} / 章节 {sample['totalSections']} / 表格 {sample['totalTables']}；确定性高危片段 {sample['highRiskSegmentCount']}\n")
        lines.append("| 指标 | builtin | workbuddy_agent_mcp |")
        lines.append("|---|---:|---:|")
        b = sample.get("builtin") or {}
        w = sample.get("workbuddy") or {}
        rows = [
            ("地图覆盖率", b.get("mapCoverage"), w.get("mapCoverage")),
            ("来源准确率", b.get("sourceAccuracy"), w.get("sourceAccuracy")),
            ("废标识别率", b.get("rejectionRecognition"), w.get("rejectionRecognition")),
            ("废标片段数", b.get("rejectionCount"), w.get("rejectionCount")),
            ("未解决问题率", b.get("unresolvedRate"), w.get("unresolvedRate")),
            ("未解决信号数", b.get("unresolved"), w.get("unresolved")),
            ("耗时(秒)", b.get("elapsed"), w.get("elapsed")),
            ("成本(USD)", "n/a", w.get("costUsd")),
        ]
        for label, bv, wv in rows:
            lines.append(f"| {label} | {_fmt(bv)} | {_fmt(wv)} |")
        lines.append("")
    if report["failures"]:
        lines.append("## 失败\n")
        for failure in report["failures"]:
            lines.append(f"- `{failure['file']}`: {failure['error']}")
        lines.append("")
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
