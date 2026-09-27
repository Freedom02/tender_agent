# -*- coding: utf-8 -*-
"""合规审查的确定性基础指标。

该模块只根据已经落盘的 ParseBundle、目录和章节内容计算覆盖率，不调用模型，
不修改正文。Pi ComplianceReviewAgent 在此基础上补充风险解释和可定位建议。
"""
from __future__ import annotations

from typing import Any


def _items(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _id(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def _has_content(section: dict[str, Any]) -> bool:
    if str(section.get("content") or "").strip():
        return True
    if section.get("templateFieldValues") or section.get("templateCellValues"):
        return True
    return False


def build_coverage_summary(
    parse_bundle: dict[str, Any] | None,
    bid_document: dict[str, Any] | None,
) -> dict[str, Any]:
    """计算要求/强制项/评分项的基础覆盖率。"""

    bundle = parse_bundle if isinstance(parse_bundle, dict) else {}
    document = bid_document if isinstance(bid_document, dict) else {}
    sections = _items(document.get("plan"))
    valid_sections = {
        _id(section, "sectionId")
        for section in sections
        if _id(section, "sectionId")
    }
    content_sections = {
        _id(section, "sectionId")
        for section in sections
        if _id(section, "sectionId") and _has_content(section)
    }

    requirements = _items(bundle.get("requirements"))
    requirement_ids = [_id(item, "id", "requirementId") for item in requirements]
    requirement_ids = [item for item in requirement_ids if item]
    mandatory_ids = {
        _id(item, "id", "requirementId")
        for item in requirements
        if _id(item, "id", "requirementId")
        and bool(item.get("mandatory") or item.get("isRejection"))
    }

    coverage_rows = _items(document.get("requirementCoverage"))
    covered_by_requirement: dict[str, set[str]] = {}
    for row in coverage_rows:
        requirement_id = _id(row, "requirementId", "id")
        section_id = _id(row, "sectionId")
        if not requirement_id or section_id not in content_sections:
            continue
        status = str(row.get("status") or "").strip().lower()
        if status in {"uncovered", "needs_review"}:
            continue
        covered_by_requirement.setdefault(requirement_id, set()).add(section_id)

    for section in sections:
        if not _has_content(section):
            continue
        for requirement_id in section.get("requirementIds") or []:
            value = str(requirement_id or "").strip()
            if value:
                covered_by_requirement.setdefault(value, set()).add(
                    _id(section, "sectionId")
                )

    covered_requirement_ids = {
        requirement_id
        for requirement_id in requirement_ids
        if covered_by_requirement.get(requirement_id)
    }

    evaluations = _items(bundle.get("evaluationItems"))
    evaluation_ids = [_id(item, "id", "evaluationId") for item in evaluations]
    evaluation_ids = [item for item in evaluation_ids if item]
    covered_evaluation_ids: set[str] = set()
    for section in sections:
        if not _has_content(section):
            continue
        for evaluation_id in section.get("evaluationIds") or []:
            value = str(evaluation_id or "").strip()
            if value:
                covered_evaluation_ids.add(value)
    for row in coverage_rows:
        if _id(row, "sectionId") not in content_sections:
            continue
        for evaluation_id in row.get("evaluationIds") or []:
            value = str(evaluation_id or "").strip()
            if value:
                covered_evaluation_ids.add(value)

    uncovered_requirement_ids = [
        requirement_id
        for requirement_id in requirement_ids
        if requirement_id not in covered_requirement_ids
    ]
    uncovered_evaluation_ids = [
        evaluation_id
        for evaluation_id in evaluation_ids
        if evaluation_id not in covered_evaluation_ids
    ]
    return {
        "requirementTotal": len(requirement_ids),
        "requirementCovered": len(covered_requirement_ids),
        "mandatoryTotal": len(mandatory_ids),
        "mandatoryCovered": len(mandatory_ids & covered_requirement_ids),
        "evaluationTotal": len(evaluation_ids),
        "evaluationCovered": len(set(evaluation_ids) & covered_evaluation_ids),
        "uncoveredRequirementIds": uncovered_requirement_ids,
        "uncoveredEvaluationIds": uncovered_evaluation_ids,
        "contentSectionIds": sorted(content_sections & valid_sections),
    }
