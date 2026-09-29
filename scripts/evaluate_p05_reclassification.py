"""Evaluate P05 section and clause routing on one tender document.

This lane intentionally stops at P05/P07-local classification.  It does not
call enterprise matching, persistence, or bid-writing, so classification
changes can be rechecked quickly without mixing in P06-P09 latency.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.document_chunking import chunk_document  # noqa: E402
from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402
from backend.app.tender_matching.requirement_extractor import extract_requirements  # noqa: E402
from backend.app.tender_matching.section_classifier import classify_document  # noqa: E402


def _mime_type(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
    }.get(path.suffix.lower(), "")


def _route_contract_valid(classifications: list[Any]) -> bool:
    for item in classifications:
        route = item.route.model_dump()
        tags = set(item.usage_tags)
        if any(bool(enabled) != (key in tags) for key, enabled in route.items()):
            return False
        if item.primary_route not in {
            "project_info",
            "company_match",
            "rejection_check",
            "compliance_confirm",
            "fill",
            "writing_context",
            "display_only",
        }:
            return False
        if (
            item.primary_type in {"document_format", "rejection"}
            and item.primary_route == "company_match"
        ):
            return False
    return True


def _samples_by_category(requirements: list[Any], limit: int = 3) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for item in requirements:
        grouped[item.category].append(item)
    return {
        category: [
            {
                "categoryHits": item.category_hits,
                "routeTags": item.route_tags,
                "primaryRoute": item.primary_route,
                "matchDomain": item.match_domain,
                "reviewMode": item.review_mode,
                "productName": item.product_name,
                "needsHumanReview": item.needs_human_review,
                "text": item.raw_text[:220],
            }
            for item in values[:limit]
        ]
        for category, values in sorted(grouped.items())
    }


def evaluate(path: Path) -> dict[str, Any]:
    document = parse_document_ir(
        path.read_bytes(),
        file_name=path.name,
        mime_type=_mime_type(path),
    )
    classifications = classify_document(document)
    chunks = chunk_document(document)
    extraction = extract_requirements(
        document,
        chunks=chunks,
        classifications=classifications,
    )
    selected_ids = {
        "SEC-0007",
        "SEC-0010",
        "SEC-0016",
        "SEC-0029",
        "SEC-0030",
        "SEC-0038",
        "SEC-0074",
        "SEC-0078",
        "SEC-0082",
        "SEC-0132",
        "SEC-0147",
    }
    section_by_id = {
        section.section_id or section.id: section
        for section in document.sections
    }
    selected = []
    for item in classifications:
        section = section_by_id.get(item.section_id)
        if item.section_id not in selected_ids and not (
            section
            and any(term in section.heading for term in ("响应表", "最高限价", "投标无效"))
        ):
            continue
        selected.append(
            {
                "sectionId": item.section_id,
                "heading": section.heading if section else "",
                "originalSectionType": item.original_section_type,
                "headingType": item.heading_type,
                "containerType": item.container_type,
                "primaryType": item.primary_type,
                "typeHits": item.type_hits,
                "primaryRoute": item.primary_route,
                "matchDomain": item.match_domain,
                "decisionImpact": item.decision_impact,
                "granularity": item.granularity,
                "reviewMode": item.review_mode,
                "needsHumanReview": item.needs_human_review,
                "source": item.classification_source,
            }
        )

    section_review_count = sum(item.needs_human_review for item in classifications)
    clause_review_count = sum(item.needs_human_review for item in extraction.requirements)
    clause_multi_count = sum(len(item.category_hits) > 1 for item in extraction.requirements)
    skipped_clause_count = int(extraction.stats.get("skippedClauseCount") or 0)
    return {
        "schemaVersion": "p05-reclassification-evaluation-v1",
        "generatedAt": date.today().isoformat(),
        "source": str(path),
        "fileName": path.name,
        "parse": {
            "parseMethod": document.parse_method,
            "segmentCount": len(document.segments),
            "sectionCount": len(document.sections),
            "warningCount": len(document.warnings),
        },
        "sectionClassification": {
            "classificationCount": len(classifications),
            "primaryCounts": dict(Counter(item.primary_type for item in classifications)),
            "sourceCounts": dict(Counter(item.classification_source for item in classifications)),
            "humanReviewCount": section_review_count,
            "humanReviewRatio": round(section_review_count / len(classifications), 4)
            if classifications
            else 0,
            "multiTypeCount": sum(len(item.type_hits) > 1 for item in classifications),
            "routeCounts": dict(Counter(item.primary_route for item in classifications)),
            "reviewModeCounts": dict(Counter(item.review_mode for item in classifications)),
            "routeContractValid": _route_contract_valid(classifications),
            "selectedSections": selected,
        },
        "clauseClassification": {
            "requirementCount": len(extraction.requirements),
            "categoryCounts": dict(Counter(item.category for item in extraction.requirements)),
            "reviewCount": clause_review_count,
            "reviewRatio": round(clause_review_count / len(extraction.requirements), 4)
            if extraction.requirements
            else 0,
            "multiTypeCount": clause_multi_count,
            "multiTypeRatio": round(clause_multi_count / len(extraction.requirements), 4)
            if extraction.requirements
            else 0,
            "matchingCandidateCount": sum(
                item.primary_route == "company_match"
                for item in extraction.requirements
            ),
            "routeCounts": dict(Counter(item.primary_route for item in extraction.requirements)),
            "reviewModeCounts": dict(Counter(item.review_mode for item in extraction.requirements)),
            "technicalTableCount": int(
                extraction.stats.get("technicalTableCount") or 0
            ),
            "technicalProductColumnCount": int(
                extraction.stats.get("technicalProductColumnCount") or 0
            ),
            "technicalTableProductNameCount": int(
                extraction.stats.get("technicalTableProductNameCount") or 0
            ),
            "technicalTableProductNameCoverage": round(
                (
                    int(extraction.stats.get("technicalTableProductNameCount") or 0)
                    / int(extraction.stats.get("technicalProductColumnCount") or 0)
                ),
                4,
            )
            if int(extraction.stats.get("technicalProductColumnCount") or 0)
            else 0,
            "technicalProductNameCount": int(
                extraction.stats.get("technicalProductNameCount") or 0
            ),
            "technicalProductNameCoverage": round(
                (
                    int(extraction.stats.get("technicalProductNameCount") or 0)
                    / sum(item.category == "technical" for item in extraction.requirements)
                ),
                4,
            )
            if any(item.category == "technical" for item in extraction.requirements)
            else 0,
            "technicalCount": sum(
                item.category == "technical"
                for item in extraction.requirements
            ),
            "servicePlanSkippedCount": int(
                extraction.stats.get("servicePlanSkippedCount") or 0
            ),
            "formatOrRejectionMatchingLeaks": sum(
                (
                    item.content_type in {"document_format", "rejection"}
                    or item.category in {"document_format", "rejection"}
                )
                and item.primary_route == "company_match"
                for item in extraction.requirements
            ),
            "skippedClauseCount": skipped_clause_count,
            "skippedCategoryCounts": extraction.stats.get("skippedCategoryCounts") or {},
            "samplesByCategory": _samples_by_category(extraction.requirements),
        },
        "assessment": {
            "status": (
                "suitable_with_review"
                if _route_contract_valid(classifications)
                and extraction.requirements
                and sum(
                    (
                        item.content_type in {"document_format", "rejection"}
                        or item.category in {"document_format", "rejection"}
                    )
                    and item.primary_route == "company_match"
                    for item in extraction.requirements
                )
                == 0
                else "needs_fix"
            ),
            "strengths": [
                "章节分类与 SectionTree 一一对应，不改变切分边界。",
                "正文条款会重新分类，不再简单继承大章节类别。",
                "模板、合同范本、普通投标须知和评标流程说明不再逐条进入人工队列。",
                "模板和否决条款有路由护栏，不进入企业能力匹配。",
                "技术表格条款保留产品名、目标类型和来源列，支持后续按产品聚合匹配。",
                "保留 headingType/containerType/categoryHits/routeTags，支持多分类和追溯。",
            ],
            "weaknesses": [
                "尚未有人工 gold，不能宣称语义准确率或 Macro-F1。",
                "部分长条款混合政策、提交和业务要求，仍需人工复核。",
                "条款级分类仍以确定性规则为主，复杂混合条款需要 LLM 兜底或人工确认。",
            ],
            "todos": [
                "为高频样本文档补人工 gold，分别评估章节主类、条款主类和路由召回。",
                "改进 P07 条款切句，把一个长段落中的多个要求拆成独立条款。",
                "接入条款级 LLM 兜底，只处理多类型、低置信度和长混合条款。",
            ],
        },
    }


def _write_markdown(report: dict[str, Any], output: Path) -> None:
    section = report["sectionClassification"]
    clause = report["clauseClassification"]
    lines = [
        "# P05 重新分类评测",
        "",
        f"- 文件：`{report['fileName']}`",
        f"- 生成日期：{report['generatedAt']}",
        f"- 结论：`{report['assessment']['status']}`",
        "",
        "## 章节分类",
        "",
        f"- 章节数：{section['classificationCount']}",
        f"- 人工复核：{section['humanReviewCount']}（{section['humanReviewRatio']:.2%}）",
        f"- 多类型章节：{section['multiTypeCount']}",
        f"- 主路由分布：`{json.dumps(section['routeCounts'], ensure_ascii=False)}`",
        f"- 章节复核模式：`{json.dumps(section['reviewModeCounts'], ensure_ascii=False)}`",
        f"- 路由契约：{'通过' if section['routeContractValid'] else '失败'}",
        f"- 主类分布：`{json.dumps(section['primaryCounts'], ensure_ascii=False)}`",
        "",
        "## 条款分类",
        "",
        f"- 抽取要求：{clause['requirementCount']}",
        f"- 人工复核：{clause['reviewCount']}（{clause['reviewRatio']:.2%}）",
        f"- 多类型条款：{clause['multiTypeCount']}（{clause['multiTypeRatio']:.2%}）",
        f"- 技术候选：{clause['technicalCount']}",
        f"- 技术表格要求：{clause['technicalTableCount']} 条，其中带产品列 {clause['technicalProductColumnCount']} 条",
        f"- 技术表格产品名：{clause['technicalTableProductNameCount']}/{clause['technicalProductColumnCount']}（覆盖率 {clause['technicalTableProductNameCoverage']:.2%}）",
        f"- 全部技术条款产品名：{clause['technicalProductNameCount']}/{clause['technicalCount']}（参考覆盖率 {clause['technicalProductNameCoverage']:.2%}）",
        f"- 条款主路由：`{json.dumps(clause['routeCounts'], ensure_ascii=False)}`",
        f"- 条款复核模式：`{json.dumps(clause['reviewModeCounts'], ensure_ascii=False)}`",
        f"- 跳过服务方案条款：{clause['servicePlanSkippedCount']}",
        f"- 跳过非精细条款：{clause['skippedClauseCount']}",
        f"- 模板/否决误入匹配：{clause['formatOrRejectionMatchingLeaks']}",
        f"- 类别分布：`{json.dumps(clause['categoryCounts'], ensure_ascii=False)}`",
        "",
        "## 重点章节",
        "",
        "| ID | 标题 | 主类 | 主路由 | 匹配领域 | 复核模式 |",
        "|---|---|---|---|---|---|",
    ]
    for item in section["selectedSections"][:40]:
        lines.append(
            f"| {item['sectionId']} | {str(item['heading']).replace('|', '\\|')} | "
            f"{item['primaryType']} | {item['primaryRoute']} | "
            f"{item['matchDomain'] or '-'} | {item['reviewMode']} |"
        )
    lines.extend([
        "",
        "## 优点",
        "",
        *[f"- {item}" for item in report["assessment"]["strengths"]],
        "",
        "## 缺点",
        "",
        *[f"- {item}" for item in report["assessment"]["weaknesses"]],
        "",
        "## 待办",
        "",
        *[f"- {item}" for item in report["assessment"]["todos"]],
        "",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=REPO_ROOT / "evaluation" / "p05_reclassification_report.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPO_ROOT / "docs" / "P05-reclassification-evaluation.md",
    )
    args = parser.parse_args()

    report = evaluate(args.input.resolve())
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(report, args.markdown_output.resolve())
    print(json.dumps(report["assessment"], ensure_ascii=False, indent=2))
    return 0 if report["assessment"]["status"] != "needs_fix" else 1


if __name__ == "__main__":
    raise SystemExit(main())
