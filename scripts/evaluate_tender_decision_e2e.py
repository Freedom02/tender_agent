r"""Run a repeatable end-to-end decision-summary check on real tender files.

The evaluation is intentionally offline:

    .\backend\.venv\Scripts\python.exe scripts\evaluate_tender_decision_e2e.py

It reads ``demo-data`` files, disables external LLM and WorkBuddy calls, uses
a fixed company-ledger fixture, and writes a compact report without mutating
runtime projects or workspaces.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.company_facts import build_company_ledger  # noqa: E402
from backend.app.tender_matching.decision_summary import (  # noqa: E402
    build_workspace_decision_summary,
)
from backend.app.tender_matching.document_matching import evaluate_tender_document  # noqa: E402
from backend.app.tender_matching.document_parser import (  # noqa: E402
    parse_doc,
    parse_docx,
    parse_pdf,
    parse_plain_text,
    parse_xlsx,
)
from backend.app.tender_matching.document_structure import enrich_document_structure  # noqa: E402


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".txt", ".md"}
DEFAULT_REPORT = REPO_ROOT / "evaluation" / "tender_decision_e2e_report.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        parsed = parse_pdf(content)
    elif suffix == ".docx":
        parsed = parse_docx(content)
    elif suffix == ".doc":
        parsed = parse_doc(content)
    elif suffix == ".xlsx":
        parsed = parse_xlsx(content)
    else:
        parsed = parse_plain_text(content)
    return enrich_document_structure(parsed)


def _company_ledger() -> dict[str, Any]:
    """Small deterministic fixture representing the current target business."""

    return build_company_ledger(
        {
            "organization": {
                "organizationId": "ORG-EVAL",
                "organizationName": "评测企业",
                "capabilities": (
                    "数字孪生平台建设、三维可视化、数据接入、低代码开发、"
                    "项目实施、系统集成、培训、运维服务"
                ),
            },
            "technical_product": [
                {
                    "productId": "PRD-DT-001",
                    "productName": "数字孪生可视化平台",
                    "description": (
                        "支持三维场景搭建、数字孪生可视化、物联网数据接入、"
                        "低代码开发、权限管理和国产化环境部署"
                    ),
                    "evidencePath": "evaluation/company/product/digital-twin-platform.pdf",
                }
            ],
            "technical_parameter": [
                {
                    "parameterId": "PAR-DT-001",
                    "productId": "PRD-DT-001",
                    "description": (
                        "支持三维模型加载、实时数据接入、可视化大屏、开放接口、"
                        "并发访问和系统集成"
                    ),
                    "evidencePath": "evaluation/company/parameter/digital-twin-parameters.pdf",
                }
            ],
            "performance_case": [
                {
                    "performanceId": "PERF-DT-001",
                    "caseName": "数字孪生平台建设项目",
                    "description": "完成数字孪生可视化平台建设、实施、培训和验收",
                    "evidencePath": "evaluation/company/performance/digital-twin-case.pdf",
                }
            ],
        }
    )


def _sample_paths(input_root: Path, limit: int) -> list[Path]:
    paths = sorted(
        (
            path
            for path in input_root.iterdir()
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and path.name.lower() != "readme.md"
            and "响应文件" not in path.name
        ),
        key=lambda path: path.name.lower(),
    )
    return paths[:limit] if limit > 0 else paths


def evaluate_sample(path: Path, company_ledger: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    sample_hash = _sha256(path)
    sample_id = f"E2E-{sample_hash[:12]}"
    parsed = _parse(path)
    project = {
        "id": sample_id,
        "title": path.stem,
        "projectName": path.stem,
        "document": {
            "id": sample_id,
            "kind": "tender_document",
            "contentHash": sample_hash,
        },
    }
    fine_review = evaluate_tender_document(
        str(parsed.get("text") or ""),
        parsed.get("segments") if isinstance(parsed.get("segments"), list) else [],
        company_ledger,
        {
            "enabled": False,
            "extractionMode": "builtin",
        },
        sample_id,
        parsed.get("sections") if isinstance(parsed.get("sections"), list) else [],
        parsed.get("tables") if isinstance(parsed.get("tables"), list) else [],
        project_id=sample_id,
        document_meta={
            "name": path.name,
            "fileName": path.name,
            "projectId": sample_id,
            "projectName": path.stem,
            "documentKind": "tender_document",
            "contentHash": sample_hash,
            "parseMethod": str(parsed.get("parseMethod") or ""),
            "projectFields": {"projectName": path.stem},
        },
    )
    decision = build_workspace_decision_summary(
        project=project,
        fine_review=fine_review,
        company_knowledge=company_ledger,
        today=date.today(),
    ).model_dump(by_alias=True)
    extraction = fine_review.get("extraction") if isinstance(fine_review.get("extraction"), dict) else {}
    technical = decision.get("technicalMatch") if isinstance(decision.get("technicalMatch"), dict) else {}
    technical_items = [
        item for item in (technical.get("items") or [])
        if isinstance(item, dict)
    ]
    return {
        "sampleId": sample_id,
        "path": path.resolve().relative_to(REPO_ROOT.resolve()).as_posix(),
        "fileName": path.name,
        "format": path.suffix.lower().lstrip("."),
        "fileSize": path.stat().st_size,
        "parseMethod": str(parsed.get("parseMethod") or ""),
        "textChars": len(str(parsed.get("text") or "")),
        "segmentCount": len(parsed.get("segments") or []),
        "sectionCount": len(parsed.get("sections") or []),
        "tableCount": len(parsed.get("tables") or []),
        "requirementCount": int(fine_review.get("clauseCount") or 0),
        "fineReviewScore": int(fine_review.get("score") or 0),
        "fineReviewRecommendation": str(fine_review.get("recommendation") or ""),
        "decisionRecommendation": str(decision.get("recommendation") or ""),
        "decisionHeadline": str(decision.get("headline") or ""),
        "technicalItemCount": len(technical.get("items") or []),
        "technicalStatusCounts": technical.get("statusCounts") or {},
        "technicalCoverage": technical.get("coverage") or 0,
        "hardBlockerCount": len(decision.get("hardBlockers") or []),
        "technicalGapCount": len(decision.get("technicalGaps") or []),
        "materialGapCount": len(decision.get("materialGaps") or []),
        "qualificationRiskCount": len(decision.get("qualificationRisks") or []),
        "commercialRiskCount": len(decision.get("commercialRisks") or []),
        "scheduleRiskCount": len(decision.get("scheduleRisks") or []),
        "needsHumanReviewCount": int(decision.get("needsHumanReviewCount") or 0),
        "extractionMethod": str(extraction.get("method") or ""),
        "extractionWarnings": extraction.get("warnings") or [],
        "decisionWarnings": decision.get("warnings") or [],
        "topHardBlockers": [
            {
                "requirementId": item.get("requirementId"),
                "requirementText": item.get("requirementText"),
                "status": item.get("status"),
                "reason": item.get("reason"),
            }
            for item in technical_items
            if item.get("hardBlocker")
        ][:10],
        "topTechnicalGaps": [
            {
                "requirementId": item.get("requirementId"),
                "requirementText": item.get("requirementText"),
                "status": item.get("status"),
                "reason": item.get("reason"),
                "missing": item.get("missing") or [],
            }
            for item in technical_items
            if item.get("status") in {"部分满足", "待澄清"}
        ][:10],
        "elapsedSeconds": round(time.perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=REPO_ROOT / "demo-data")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    records: list[dict[str, Any]] = []
    for path in _sample_paths(args.input_root, args.limit):
        try:
            records.append(evaluate_sample(path, _company_ledger()))
        except Exception as exc:
            records.append(
                {
                    "path": path.resolve().relative_to(REPO_ROOT.resolve()).as_posix(),
                    "fileName": path.name,
                    "format": path.suffix.lower().lstrip("."),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
    report = {
        "schemaVersion": "tender-decision-e2e-v1",
        "generatedAt": date.today().isoformat(),
        "environment": {
            "requiredEnvironment": "project-venv",
            "pythonExecutable": sys.executable,
            "externalLlmEnabled": False,
            "workbuddyEnabled": False,
            "companyLedger": "deterministic evaluation fixture",
        },
        "sampleCount": len(records),
        "successCount": sum(1 for item in records if not item.get("error")),
        "failureCount": sum(1 for item in records if item.get("error")),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "tender decision E2E: "
        f"samples={report['sampleCount']} "
        f"success={report['successCount']} "
        f"failure={report['failureCount']} "
        f"report={args.output}"
    )
    return 0 if report["failureCount"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
