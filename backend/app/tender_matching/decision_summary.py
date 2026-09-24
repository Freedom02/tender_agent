"""Small decision-oriented projection for the tender review first screen."""
from __future__ import annotations

import re
from collections import Counter
from datetime import date
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .fact_models import ExtractedFieldFact, RequirementFact
from .technical_service_matching import (
    TechnicalMatchSummary,
    match_technical_service_requirements,
)


DecisionLevel = Literal["high", "medium", "low"]


class DecisionRisk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    level: DecisionLevel = "medium"
    title: str
    detail: str = ""
    requirement_id: str = Field("", alias="requirementId")
    status: str = ""
    source_locator: str = Field("", alias="sourceLocator")


class DecisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendation: Literal["建议继续", "谨慎继续", "不建议继续", "待澄清"] = "待澄清"
    headline: str = ""
    project_fields: dict[str, str] = Field(default_factory=dict, alias="projectFields")
    technical_match: TechnicalMatchSummary = Field(alias="technicalMatch")
    hard_blockers: list[DecisionRisk] = Field(default_factory=list, alias="hardBlockers")
    technical_gaps: list[DecisionRisk] = Field(default_factory=list, alias="technicalGaps")
    material_gaps: list[DecisionRisk] = Field(default_factory=list, alias="materialGaps")
    qualification_risks: list[DecisionRisk] = Field(
        default_factory=list,
        alias="qualificationRisks",
    )
    commercial_risks: list[DecisionRisk] = Field(
        default_factory=list,
        alias="commercialRisks",
    )
    schedule_risks: list[DecisionRisk] = Field(
        default_factory=list,
        alias="scheduleRisks",
    )
    needs_human_review_count: int = Field(0, alias="needsHumanReviewCount", ge=0)
    risk_count: int = Field(0, alias="riskCount", ge=0)
    warnings: list[str] = Field(default_factory=list)


def _value(item: Any, *names: str) -> str:
    if isinstance(item, BaseModel):
        for name in names:
            value = getattr(item, name, None)
            if value not in (None, ""):
                return str(value)
    elif isinstance(item, Mapping):
        for name in names:
            value = item.get(name)
            if value not in (None, ""):
                return str(value)
    return ""


def _bool_value(item: Any, *names: str) -> bool:
    if isinstance(item, BaseModel):
        return any(bool(getattr(item, name, False)) for name in names)
    if isinstance(item, Mapping):
        return any(bool(item.get(name)) for name in names)
    return False


def _requirement_id(item: Any) -> str:
    return _value(item, "requirement_id", "requirementId", "id")


def _text(item: Any) -> str:
    return _value(
        item,
        "raw_text",
        "rawText",
        "normalized_requirement",
        "normalizedRequirement",
        "text",
        "requirement",
        "sourceText",
    )


def _project_fields(fields: Iterable[Any]) -> dict[str, str]:
    if isinstance(fields, Mapping):
        if any(key in fields for key in ("fieldName", "field_name")):
            fields = [fields]
        else:
            return {
                str(name): str(value)
                for name, value in fields.items()
                if str(name).strip() and value not in (None, "")
            }
    result: dict[str, str] = {}
    for field in fields:
        name = _value(field, "field_name", "fieldName", "name")
        value = _value(field, "value", "text")
        if name and value and name not in result:
            result[name] = value
    return result


def _risk_from_requirement(item: Any, *, category: str, level: DecisionLevel) -> DecisionRisk:
    return DecisionRisk(
        category=category,
        level=level,
        title=_value(item, "sub_category", "subCategory", "requirementClass") or category,
        detail=_text(item),
        requirementId=_requirement_id(item),
        status=_value(item, "status") or ("需人工确认" if _bool_value(item, "needs_human_review", "needsHumanReview") else ""),
        sourceLocator=_value(item, "source_locator", "sourceLocator"),
    )


def _parse_date(value: str) -> date | None:
    match = re.search(r"(20\d{2})\D{0,3}(\d{1,2})\D{0,3}(\d{1,2})", value)
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def _needs_decision_review(item: Any, category: str, status: str) -> bool:
    """Count only pending items that can change the bid/no-bid decision."""

    pending = (
        _bool_value(item, "needs_human_review", "needsHumanReview")
        or status in {
            "部分满足",
            "待补材料",
            "不满足",
            "待核查",
            "有风险",
            "待确认",
            "待填写",
        }
    )
    if not pending:
        return False
    if category in {
        "qualification",
        "performance",
        "personnel",
        "financial",
        "rejection",
        "schedule",
    }:
        return True
    if category in {"commercial", "pricing", "contract"}:
        return (
            _bool_value(item, "mandatory", "is_rejection", "isRejection")
            or status in {"不满足", "有风险", "待核查"}
        )
    return False


def _technical_risks(summary: TechnicalMatchSummary) -> tuple[list[DecisionRisk], list[DecisionRisk], list[DecisionRisk]]:
    blockers: list[DecisionRisk] = []
    gaps: list[DecisionRisk] = []
    materials: list[DecisionRisk] = []
    for item in summary.items:
        risk = DecisionRisk(
            category=item.category,
            level=item.risk_level,
            title=item.requirement_text[:80] or item.requirement_id,
            detail=item.reason or "技术/产品/服务匹配需要进一步确认",
            requirementId=item.requirement_id,
            status=item.status,
        )
        if item.hard_blocker:
            blockers.append(risk)
        elif item.status in {"部分满足", "待澄清"}:
            gaps.append(risk)
        elif item.status == "待补材料":
            materials.append(risk)
    return blockers, gaps, materials


def collect_decision_project_fields(
    project: Mapping[str, Any] | None,
    fine_review: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Collect the small project field set used by the first-screen summary."""

    source = project or {}
    fields: dict[str, str] = {}
    keys = (
        "projectName",
        "projectCode",
        "tendererName",
        "agencyName",
        "procurementMethod",
        "budgetAmount",
        "maxPrice",
        "serviceLocation",
        "projectDuration",
        "servicePeriod",
        "deliveryPeriod",
        "bidDeadline",
        "deadline",
        "openingTime",
        "openingLocation",
    )
    parsed = source.get("parsed") if isinstance(source.get("parsed"), Mapping) else {}
    for key in keys:
        value = parsed.get(key) if isinstance(parsed, Mapping) else None
        if value in (None, ""):
            value = source.get(key)
        if value not in (None, ""):
            fields[key] = str(value).strip()
    if not fields.get("projectName"):
        title = str(source.get("title") or "").strip()
        if title:
            fields["projectName"] = title

    review = fine_review or {}
    analysis_pack = review.get("analysisPack")
    if not isinstance(analysis_pack, Mapping):
        extraction = review.get("extraction")
        analysis_pack = extraction.get("analysisPack") if isinstance(extraction, Mapping) else None
    if isinstance(analysis_pack, Mapping):
        for group in ("projectInfo", "milestones", "feesAndGuarantees"):
            items = analysis_pack.get(group)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                name = _value(item, "field_name", "fieldName", "field", "name")
                value = _value(item, "value", "text", "normalizedText", "rawText")
                if name and value:
                    fields[name] = value.strip()
    return fields


def _technical_summary_with_manual_reviews(
    summary: TechnicalMatchSummary,
    requirements: Iterable[Any],
) -> TechnicalMatchSummary:
    """Apply explicit human review without allowing unsupported satisfaction."""

    rows_by_id = {
        _requirement_id(item): item
        for item in requirements
        if _requirement_id(item)
    }
    warnings = list(summary.warnings)
    items = []
    valid_statuses = {"满足", "部分满足", "不满足", "待补材料", "待澄清", "不适用"}
    for item in summary.items:
        row = rows_by_id.get(item.requirement_id)
        status_source = _value(row, "status_source", "statusSource") if row is not None else ""
        manual_status = _value(row, "manual_status", "manualStatus", "status") if row is not None else ""
        if status_source != "人工审核" or manual_status not in valid_statuses:
            items.append(item)
            continue

        status = manual_status
        reason = _value(row, "reason", "uncertaintyReason") or f"人工审核结论：{manual_status}"
        if status == "满足" and not item.evidence_items:
            status = "待补材料"
            reason = "人工标记为满足，但系统未找到可核验企业证据，仍按待补材料处理。"
            warnings.append(f"{item.requirement_id} 人工标记满足但缺少企业证据")
        hard_blocker = item.hard_blocker or (
            status == "不满足"
            and _bool_value(row, "mandatory", "is_rejection", "isRejection")
        )
        items.append(
            item.model_copy(
                update={
                    "status": status,
                    "reason": reason,
                    "risk_level": "high" if hard_blocker or status == "不满足" else (
                        "low" if status == "满足" else "medium"
                    ),
                    "needs_human_review": status != "满足",
                    "hard_blocker": hard_blocker,
                    "status_source": "manual_review",
                }
            )
        )

    counts = Counter(item.status for item in items)
    hard_blockers = [item for item in items if item.hard_blocker]
    technical_gaps = [
        item for item in items
        if item.status in {"部分满足", "不满足", "待澄清"}
    ]
    material_gaps = [item for item in items if item.status == "待补材料"]
    covered = sum(1 for item in items if item.status == "满足")
    coverage = covered / len(items) if items else 0.0
    if not items:
        recommendation = "待澄清"
    elif hard_blockers:
        recommendation = "不建议继续"
    elif technical_gaps or material_gaps:
        recommendation = "谨慎继续"
    else:
        recommendation = "建议继续"
    return TechnicalMatchSummary(
        items=items,
        statusCounts=dict(counts),
        hardBlockers=hard_blockers,
        technicalGaps=technical_gaps,
        materialGaps=material_gaps,
        coverage=coverage,
        recommendation=recommendation,
        warnings=list(dict.fromkeys(warnings)),
    )


def build_decision_summary(
    *,
    project_fields: Iterable[ExtractedFieldFact | Mapping[str, Any]] = (),
    requirements: Iterable[RequirementFact | Mapping[str, Any]] = (),
    technical_match: TechnicalMatchSummary,
    today: date | None = None,
) -> DecisionSummary:
    """Build the compact first-screen projection from shared facts."""

    fields = _project_fields(project_fields)
    qualification_risks: list[DecisionRisk] = []
    commercial_risks: list[DecisionRisk] = []
    schedule_risks: list[DecisionRisk] = []
    review_count = sum(
        1
        for item in technical_match.items
        if item.hard_blocker or item.status in {"部分满足", "不满足", "待澄清"}
    )

    for item in requirements:
        category = _value(item, "category", "targetModule") or "other"
        if category in {"technical", "service_plan"}:
            continue
        status = _value(item, "status")
        if status in {"满足", "已确认安全", "已完成", "已编制"}:
            continue
        if _needs_decision_review(item, category, status):
            review_count += 1
        if category in {"qualification", "performance", "personnel", "financial", "rejection"}:
            level: DecisionLevel = "high" if (
                category == "rejection"
                or _bool_value(item, "mandatory")
            ) else "medium"
            qualification_risks.append(
                _risk_from_requirement(item, category=category, level=level)
            )
        elif category in {"commercial", "pricing", "contract", "submission", "document_format"}:
            commercial_risks.append(
                _risk_from_requirement(item, category=category, level="medium")
            )
        elif category == "schedule":
            schedule_risks.append(
                _risk_from_requirement(item, category=category, level="high")
            )

    today = today or date.today()
    schedule_labels = {
        "deadline": "投标截止时间",
        "bidDeadline": "投标截止时间",
        "openingTime": "开标时间",
    }
    for field_name, title in schedule_labels.items():
        parsed = _parse_date(fields.get(field_name, ""))
        if parsed is None:
            continue
        days = (parsed - today).days
        if days < 0:
            level: DecisionLevel = "high"
            detail = f"{field_name} 已经过期"
        elif days <= 7:
            level = "high"
            detail = f"{field_name} 距今 {days} 天"
        elif days <= 30:
            level = "medium"
            detail = f"{field_name} 距今 {days} 天"
        else:
            continue
        schedule_risks.append(
            DecisionRisk(
                category="schedule",
                level=level,
                title=title,
                detail=detail,
                status="需关注",
            )
        )

    hard_blockers, technical_gaps, material_gaps = _technical_risks(technical_match)
    if hard_blockers:
        recommendation = "不建议继续"
        headline = "存在硬性技术或服务不匹配"
    elif technical_match.recommendation == "待澄清" and not technical_match.items:
        recommendation = "待澄清"
        headline = "尚未识别出可供判断的技术或服务要求"
    elif any(item.level == "high" for item in (*qualification_risks, *schedule_risks)):
        recommendation = "谨慎继续"
        headline = "技术能力基本可评估，但存在高优先级风险"
    elif technical_match.recommendation == "建议继续":
        recommendation = "建议继续"
        headline = "技术、产品和服务能力暂未发现硬性障碍"
    else:
        recommendation = "谨慎继续"
        headline = "存在技术缺口或企业材料缺口，需要补充确认"

    return DecisionSummary(
        recommendation=recommendation,
        headline=headline,
        projectFields=fields,
        technicalMatch=technical_match,
        hardBlockers=hard_blockers,
        technicalGaps=technical_gaps,
        materialGaps=material_gaps,
        qualificationRisks=qualification_risks[:30],
        commercialRisks=commercial_risks[:30],
        scheduleRisks=schedule_risks[:30],
        needsHumanReviewCount=review_count,
        riskCount=(
            len(hard_blockers)
            + len(technical_gaps)
            + len(material_gaps)
            + len(qualification_risks)
            + len(commercial_risks)
            + len(schedule_risks)
        ),
        warnings=list(technical_match.warnings),
    )


def build_workspace_decision_summary(
    *,
    project: Mapping[str, Any] | None,
    fine_review: Mapping[str, Any],
    company_knowledge: Mapping[str, Any],
    ai: Mapping[str, Any] | None = None,
    today: date | None = None,
) -> DecisionSummary:
    """Build the persisted workspace projection from the current fine review."""

    rows = [
        item
        for item in (fine_review.get("rows") or [])
        if isinstance(item, Mapping)
    ]
    technical_rows = [
        {
            **dict(row),
            "category": "technical",
        }
        for row in rows
        if _value(row, "category", "targetModule") in {"technical", "service_plan"}
    ]
    technical_match = match_technical_service_requirements(
        technical_rows,
        company_knowledge,
        ai=ai,
    )
    technical_match = _technical_summary_with_manual_reviews(technical_match, rows)
    return build_decision_summary(
        project_fields=collect_decision_project_fields(project, fine_review),
        requirements=rows,
        technical_match=technical_match,
        today=today,
    )
