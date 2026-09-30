"""Evaluate the map-first target discovery and structural chunking pipeline.

Run from the repository root:

    python scripts/evaluate_document_map.py
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.workbuddy_agent.document_map import (  # noqa: E402
    build_structural_blocks,
    discover_document_map,
)
from backend.app.workbuddy_agent.document_map.validator import (  # noqa: E402
    validate_document_map,
)
from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402


MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FINE_DOMAINS = {"technical", "qualification"}
DISPLAY_DOMAINS = {"commercial", "evaluation"}


def _sample_score(sample: dict[str, Any]) -> int:
    checks = sample["checks"]
    score = 0
    score += 25 if checks["mapValid"] else 0
    score += 20 if checks["fineBlocksTraceable"] else 0
    score += 15 if checks["fineBlocksOnlyTechnicalOrQualification"] else 0
    score += 10 if checks["displayDomainsNotFineSplit"] else 0
    score += 10 if checks["templatesHaveLocatorAndNoMatchRoute"] else 0
    score += 10 if checks["contractsIgnored"] else 0
    score += 5 if checks["rejectionOverlayCompact"] else 0
    score += 5 if checks["projectInfoNotMatchRoute"] else 0
    return score


def _evaluate(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    document = parse_document_ir(
        content,
        file_name=path.name,
        mime_type=MIME_DOCX,
    )
    document_map = discover_document_map(document)
    validation = validate_document_map(
        document_map,
        sections=document.sections,
        segments=document.segments,
        tables=document.tables,
        document_id=document.document_id,
        document_version_id=document.document_version_id,
    )
    blocks = build_structural_blocks(document, document_map)
    fine_blocks = [item for item in blocks.blocks if item.block_type == "fine"]
    table_blocks = [item for item in blocks.blocks if item.block_type == "table"]
    bad_fine_blocks = [
        item
        for item in fine_blocks
        if item.domain not in FINE_DOMAINS
    ]
    display_fine_blocks = [
        item
        for item in fine_blocks
        if item.domain in DISPLAY_DOMAINS
    ]
    templates = [
        item for item in document_map.sections if item.domain == "template"
    ]
    contracts = list(document_map.ignored_sections)
    project_info = [
        item for item in document_map.sections if item.domain == "project_info"
    ]
    rejection_overlays = [
        item for item in document_map.sections if item.domain == "rejection"
    ]
    all_source_ids = {item.id for item in document.segments if item.id}
    fine_source_rate = (
        sum(bool(item.segment_ids) for item in fine_blocks) / len(fine_blocks)
        if fine_blocks
        else 1.0
    )
    checks = {
        "mapValid": validation.valid,
        "fineBlocksTraceable": fine_source_rate == 1.0,
        "fineBlocksOnlyTechnicalOrQualification": not bad_fine_blocks,
        "displayDomainsNotFineSplit": not display_fine_blocks,
        "templatesHaveLocatorAndNoMatchRoute": all(
            item.template_locator is not None
            and item.primary_route == "template_processor"
            and item.domain not in {"technical", "qualification"}
            for item in templates
        ),
        "contractsIgnored": all(
            item.domain == "contract"
            and item.ignored
            and item.primary_route == "ignored_contract"
            and item.processing_mode == "ignore"
            for item in contracts
        ),
        "rejectionOverlayCompact": len(rejection_overlays) <= 1,
        "projectInfoNotMatchRoute": all(
            item.primary_route not in {
                "technical_processor",
                "qualification_processor",
            }
            for item in project_info
        ),
        "allFineSourcesExist": all(
            set(item.segment_ids) <= all_source_ids for item in fine_blocks
        ),
        "allTableBlocksHaveSource": all(
            bool(item.table_ids and item.segment_ids) for item in table_blocks
        ),
    }
    sample = {
        "fileName": path.name,
        "path": path.as_posix(),
        "parse": {
            "status": document.diagnostics.status,
            "method": document.parse_method,
            "segmentCount": len(document.segments),
            "sectionCount": len(document.sections),
            "tableCount": len(document.tables),
        },
        "map": {
            "activeSectionCount": len(document_map.sections),
            "ignoredSectionCount": len(document_map.ignored_sections),
            "rejectionCandidateCount": len(document_map.rejection_candidates),
            "unresolvedSectionCount": len(document_map.unresolved_sections),
            "domainCounts": dict(
                Counter(
                    item.domain
                    for item in [
                        *document_map.sections,
                        *document_map.ignored_sections,
                    ]
                )
            ),
            "modeCounts": dict(
                Counter(item.processing_mode for item in document_map.sections)
            ),
        },
        "blocks": {
            "stats": blocks.stats,
            "domainCounts": dict(Counter(item.domain for item in blocks.blocks)),
            "fineDomainCounts": dict(
                Counter(item.domain for item in fine_blocks)
            ),
            "fineSourceRate": round(fine_source_rate, 4),
            "tableBlockCount": len(table_blocks),
            "badFineBlocks": [
                {
                    "blockId": item.block_id,
                    "domain": item.domain,
                    "title": item.title,
                }
                for item in bad_fine_blocks
            ],
        },
        "checks": checks,
        "validation": {
            "valid": validation.valid,
            "issues": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "objectId": item.object_id,
                    "sourceIds": item.source_ids,
                }
                for item in validation.issues
            ],
            "coverage": validation.coverage,
        },
    }
    sample["score"] = _sample_score(sample)
    return sample


def _write_markdown(report: dict[str, Any], output: Path) -> None:
    summary = report["summary"]
    lines = [
        "# DocumentMap 目标范围与结构分块评测",
        "",
        f"> 生成日期：{report['generatedAt']}",
        f"> 样本数：{summary['sampleCount']}",
        f"> 平均分：{summary['averageScore']}/100",
        f"> 通过样本：{summary['passedCount']}/{summary['sampleCount']}",
        "",
        "## 评测标准",
        "",
        "- 25 分：DocumentMap 通过来源、路由和合同/模板约束校验。",
        "- 20 分：技术/资格细块全部有来源 segmentId。",
        "- 15 分：普通细块只能来自技术或资格范围。",
        "- 10 分：商务和评分不生成普通细块。",
        "- 10 分：模板有 TemplateLocator，且不走企业匹配路由。",
        "- 10 分：合同进入 ignoredSections，且 processingMode 为 ignore。",
        "- 5 分：废标候选使用一个全文覆盖层承载。",
        "- 5 分：项目基础信息不走企业匹配路由。",
        "",
        "## 汇总",
        "",
        "| 文件 | 分数 | Map | 章节/表格/细块 | 技术细块 | 资格细块 | 合同忽略 | 废标候选 |",
        "|---|---:|---|---:|---:|---:|---|---:|",
    ]
    for sample in report["samples"]:
        stats = sample["blocks"]["stats"]
        fine = sample["blocks"]["fineDomainCounts"]
        lines.append(
            f"| {sample['fileName'].replace('|', '\\|')} | "
            f"{sample['score']} | "
            f"{'通过' if sample['validation']['valid'] else '失败'} | "
            f"{stats.get('sectionBlockCount', 0)}/"
            f"{stats.get('tableBlockCount', 0)}/"
            f"{stats.get('fineBlockCount', 0)} | "
            f"{fine.get('technical', 0)} | {fine.get('qualification', 0)} | "
            f"{'是' if sample['checks']['contractsIgnored'] else '否'} | "
            f"{sample['map']['rejectionCandidateCount']} |"
        )
    lines.extend(["", "## 失败或待观察项", ""])
    has_issue = False
    for sample in report["samples"]:
        failed = [
            key for key, value in sample["checks"].items() if not value
        ]
        if not failed and not sample["validation"]["issues"]:
            continue
        has_issue = True
        lines.append(f"### {sample['fileName']}")
        if failed:
            lines.append(f"- 未通过检查：`{', '.join(failed)}`")
        for issue in sample["validation"]["issues"]:
            lines.append(
                f"- `{issue['severity']}` `{issue['code']}`："
                f"{issue['objectId'] or 'map'}"
            )
        if sample["map"]["unresolvedSectionCount"]:
            lines.append(
                f"- 规则分数接近的章节："
                f"{sample['map']['unresolvedSectionCount']} 个，待 LLM 范围级复核。"
            )
        lines.append("")
    if not has_issue:
        lines.append("- 所有样本均通过当前结构验收。")
    lines.extend(
        [
            "",
            "## 结论",
            "",
            "当前结果只证明来源、粒度和路由边界满足契约，不等同于人工语义准确率。",
            "后续 B11 仍需为低置信度和多候选章节接入范围级 LLM 复核，"
            "但不能让 LLM 修改章节边界或扩大企业匹配范围。",
        ]
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=REPO_ROOT / "demo-data")
    parser.add_argument(
        "--json-output",
        type=Path,
        default=REPO_ROOT / "evaluation" / "document-map-evaluation.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPO_ROOT / "docs" / "P04-P05-document-map-evaluation.md",
    )
    args = parser.parse_args()
    paths = sorted(
        (
            path
            for path in args.input.resolve().glob("*.docx")
            if path.is_file()
        ),
        key=lambda path: path.name.lower(),
    )
    samples = [_evaluate(path) for path in paths]
    scores = [sample["score"] for sample in samples]
    report = {
        "schemaVersion": "document-map-evaluation/v1",
        "generatedAt": date.today().isoformat(),
        "criteria": {
            "fineDomains": sorted(FINE_DOMAINS),
            "displayDomains": sorted(DISPLAY_DOMAINS),
            "maxScore": 100,
        },
        "summary": {
            "sampleCount": len(samples),
            "averageScore": round(sum(scores) / len(scores), 2) if scores else 0,
            "minimumScore": min(scores) if scores else 0,
            "maximumScore": max(scores) if scores else 0,
            "passedCount": sum(sample["score"] == 100 for sample in samples),
        },
        "samples": samples,
    }
    args.json_output.resolve().parent.mkdir(parents=True, exist_ok=True)
    args.json_output.resolve().write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(report, args.markdown_output.resolve())
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
