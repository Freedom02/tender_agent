"""Range-level processors for the WorkBuddy + Agent MCP document map.

The map is deliberately processed at two different granularities:

* project information, rejection candidates and templates stay range-level;
* only technical and qualification ranges are handed to fine extraction.

This module contains no WorkBuddy bridge code and never reads files directly.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field

from ...tender_matching.document_models import DocumentIR
from ..document_reading import DocumentReadService
from .schemas import (
    DocumentMap,
    DocumentMapSection,
    DocumentRange,
    DocumentSourceAnchor,
    RejectionCandidate,
    TemplateLocator,
)


PROJECT_INFO_FIELD_DEFINITIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("projectName", ("项目名称", "采购项目名称", "招标项目名称")),
    ("projectCode", ("项目编号", "采购项目编号", "招标编号")),
    ("purchaser", ("采购人", "招标人")),
    ("agency", ("代理机构", "采购代理机构")),
    ("budgetAmount", ("预算金额", "采购预算", "采购包预算金额")),
    ("maxPrice", ("最高限价", "最高控制价", "采购包最高限价")),
    ("bidDeadline", ("投标截止时间", "响应文件提交截止时间")),
    ("openingTime", ("开标时间", "开启时间")),
    ("bidBond", ("投标保证金", "投标保证金金额", "采购包保证金金额")),
    ("performanceBond", ("履约保证金", "履约保证金金额")),
    ("servicePeriod", ("服务期限", "工期")),
    ("deliveryDeadline", ("交付期限", "交货期", "交货时间")),
    ("deliveryPlace", ("交付地点", "交货地点")),
    ("consortium", ("是否接受联合体投标", "是否允许联合体投标", "是否接受联合体", "是否允许联合体")),
    ("subcontracting", ("是否允许分包", "是否接受分包")),
    ("electronicBid", ("投标文件递交方式", "电子投标方式")),
)

_FIELD_LABELS = {
    label: field_name
    for field_name, labels in PROJECT_INFO_FIELD_DEFINITIONS
    for label in labels
}
_VALUE_ONLY_NOISE = {"", "、", "账户", "文件", "及地点", "是/否", "是 否", "投标", "或", "由", "文件格式", "托管银行"}
_PLACEHOLDER_VALUE = re.compile(r"^(?:[_＿\-\s（）()：:]+|（?由投标人填写）?|待填写|请填写)$")
_FIELD_VALUE_LIMITS = {
    "projectName": 120,
    "projectCode": 100,
    "purchaser": 160,
    "agency": 160,
    "budgetAmount": 180,
    "maxPrice": 180,
    "bidDeadline": 220,
    "openingTime": 220,
    "bidBond": 220,
    "performanceBond": 220,
    "servicePeriod": 220,
    "deliveryDeadline": 220,
    "deliveryPlace": 220,
    "consortium": 100,
    "subcontracting": 100,
    "electronicBid": 220,
}


class ProjectInfoField(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    field_id: str = Field(..., alias="fieldId")
    field_name: str = Field(..., alias="fieldName")
    label: str
    value: str
    source_segment_ids: list[str] = Field(default_factory=list, alias="sourceSegmentIds")
    source_quote: str = Field("", alias="sourceQuote")
    source: DocumentSourceAnchor = Field(default_factory=DocumentSourceAnchor)
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class ProjectInfoOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document_version_id: str = Field(..., alias="documentVersionId")
    fields: list[ProjectInfoField] = Field(default_factory=list)
    source_segment_ids: list[str] = Field(default_factory=list, alias="sourceSegmentIds")
    warnings: list[str] = Field(default_factory=list)


class ScopeContentItem(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    item_id: str = Field(..., alias="itemId")
    title: str = ""
    text: str = ""
    raw_text: str = Field("", alias="rawText")
    source_segment_ids: list[str] = Field(default_factory=list, alias="sourceSegmentIds")
    source: DocumentSourceAnchor = Field(default_factory=DocumentSourceAnchor)
    handling_mode: str = Field("display", alias="handlingMode")
    mandatory: bool = False
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class EvaluationCriterionOutput(ScopeContentItem):
    criterion_id: str = Field("", alias="criterionId")
    criterion_name: str = Field("", alias="criterionName")
    criterion_type: str = Field("", alias="criterionType")
    max_score: float | None = Field(default=None, alias="maxScore")
    scoring_rule: str = Field("", alias="scoringRule")
    required_evidence: str = Field("", alias="requiredEvidence")


class RejectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document_version_id: str = Field(..., alias="documentVersionId")
    overlay_map_id: str = Field("", alias="overlayMapId")
    candidates: list[RejectionCandidate] = Field(default_factory=list)
    hard_risk_count: int = Field(0, alias="hardRiskCount", ge=0)
    source_segment_ids: list[str] = Field(default_factory=list, alias="sourceSegmentIds")


class TemplateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document_version_id: str = Field(..., alias="documentVersionId")
    locators: list[TemplateLocator] = Field(default_factory=list)
    readable_template_ids: list[str] = Field(default_factory=list, alias="readableTemplateIds")
    warnings: list[str] = Field(default_factory=list)


class ContractIgnoreOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document_version_id: str = Field(..., alias="documentVersionId")
    ignored_sections: list[DocumentMapSection] = Field(default_factory=list, alias="ignoredSections")
    ignored_segment_ids: list[str] = Field(default_factory=list, alias="ignoredSegmentIds")


class ScopeProcessingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document_version_id: str = Field(..., alias="documentVersionId")
    project_info: ProjectInfoOutput = Field(..., alias="projectInfo")
    commercial_terms: list[ScopeContentItem] = Field(
        default_factory=list,
        alias="commercialTerms",
    )
    evaluation_criteria: list[EvaluationCriterionOutput] = Field(
        default_factory=list,
        alias="evaluationCriteria",
    )
    rejection: RejectionOutput
    templates: TemplateOutput
    contracts: ContractIgnoreOutput


def _as_document(value: DocumentIR | Mapping[str, Any]) -> DocumentIR:
    return value if isinstance(value, DocumentIR) else DocumentIR.model_validate(value)


def _as_map(value: DocumentMap | Mapping[str, Any]) -> DocumentMap:
    return value if isinstance(value, DocumentMap) else DocumentMap.model_validate(value)


def _ordered_ids(document: DocumentIR) -> tuple[list[str], dict[str, int]]:
    ids = [segment.id for segment in document.segments if segment.id]
    return ids, {segment_id: index for index, segment_id in enumerate(ids)}


def _map_segment_ids(document: DocumentIR, item: DocumentMapSection) -> list[str]:
    """Resolve a map section to its exact parser segment span."""

    segment_map = {segment.id: segment for segment in document.segments if segment.id}
    ordered_ids, positions = _ordered_ids(document)
    selected: set[str] = {
        segment_id
        for segment_id in [*item.source_segment_ids, *item.source.segment_ids]
        if segment_id in segment_map
    }
    section_map = {
        str(section.section_id or section.id): section
        for section in document.sections
        if str(section.section_id or section.id)
    }
    for item_range in _document_ranges(item):
        explicit = {
            *item_range.segment_ids,
            *item_range.source.segment_ids,
        }
        if explicit:
            selected.update(segment_id for segment_id in explicit if segment_id in segment_map)
            continue
        start = positions.get(item_range.start_segment_id)
        end = positions.get(item_range.end_segment_id)
        if start is not None and end is not None:
            left, right = sorted((start, end))
            selected.update(ordered_ids[left : right + 1])
        for section_id in [*item_range.section_ids, *item_range.source.section_ids]:
            section = section_map.get(section_id)
            if section is not None:
                selected.update(
                    segment_id
                    for segment_id in section.segment_ids
                    if segment_id in segment_map
                )

    section_ids = list(
        dict.fromkeys(
            [
                *item.section_ids,
                item.owner_section_id,
                *item.source.section_ids,
            ]
        )
    )
    if not selected:
        for section_id in section_ids:
            section = section_map.get(section_id)
            if section is not None:
                selected.update(
                    segment_id
                    for segment_id in section.segment_ids
                    if segment_id in segment_map
                )

    table_ids = {
        *item.table_ids,
        *item.source.table_ids,
            *[
            table_id
            for item_range in _document_ranges(item)
            for table_id in [*item_range.table_ids, *item_range.source.table_ids]
        ],
    }
    if table_ids:
        selected.update(
            segment.id
            for segment in document.segments
            if segment.id and segment.table_id in table_ids
        )
    return sorted(selected, key=lambda segment_id: positions.get(segment_id, 10**9))


def _mapped_sections(
    document: DocumentIR,
    document_map: DocumentMap,
    domains: Iterable[str],
) -> list[tuple[DocumentMapSection, list[str]]]:
    target_domains = set(domains)
    result: list[tuple[DocumentMapSection, list[str]]] = []
    for item in document_map.sections:
        if item.domain not in target_domains and not target_domains.intersection(item.secondary_domains):
            continue
        segment_ids = _map_segment_ids(document, item)
        if segment_ids:
            result.append((item, segment_ids))
    return result


def _anchor_for_segment(document: DocumentIR, segment_id: str) -> DocumentSourceAnchor:
    segment = next((item for item in document.segments if item.id == segment_id), None)
    section = next(
        (item for item in document.sections if segment_id in item.segment_ids),
        None,
    )
    pages = [segment.page] if segment and segment.page is not None else []
    table_ids = [segment.table_id] if segment and segment.table_id else []
    source_format = document.original_format or "unknown"
    if source_format not in {"docx", "pdf", "xlsx", "text", "doc", "unknown"}:
        source_format = "text"
    return DocumentSourceAnchor(
        documentId=document.document_id,
        documentVersionId=document.document_version_id,
        sourceFormat=source_format,
        sectionId=(section.section_id or section.id) if section else "",
        sectionIds=[section.section_id or section.id] if section else [],
        segmentId=segment_id,
        segmentIds=[segment_id],
        tableId=table_ids[0] if table_ids else "",
        tableIds=table_ids,
        sourceLocator=segment.source_locator if segment else "",
        pageStart=pages[0] if pages else None,
        pageEnd=pages[-1] if pages else None,
    )


def _document_ranges(item: DocumentMapSection) -> list[DocumentRange]:
    return [
        item_range
        if isinstance(item_range, DocumentRange)
        else DocumentRange.model_validate(item_range)
        for item_range in item.ranges
    ]


def _clean_value(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip(" \t\r\n:：;；,，")
    value = value.split("|", 1)[0].strip()
    return value[:240]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _valid_project_field_value(field_name: str, value: str) -> bool:
    normalized = _clean_value(value)
    if not normalized or normalized in _VALUE_ONLY_NOISE or _PLACEHOLDER_VALUE.fullmatch(normalized):
        return False
    if normalized.endswith(("：", ":")):
        return False
    if field_name in {"consortium", "subcontracting"}:
        return any(term in normalized for term in ("接受", "不接受", "允许", "不允许", "是", "否"))
    if field_name in {"budgetAmount", "maxPrice", "bidBond", "performanceBond"}:
        return bool(re.search(r"(?:\d[\d,，]*(?:\.\d+)?|详见|见|以|不收取|无需)", normalized))
    return len(normalized) <= _FIELD_VALUE_LIMITS.get(field_name, 220)


def _structured_label_pattern() -> re.Pattern[str]:
    labels = sorted(_FIELD_LABELS, key=len, reverse=True)
    return re.compile(
        rf"(?P<label>{'|'.join(re.escape(label) for label in labels)})\s*[:：]",
    )


def _table_key_value_pairs(segment: Any) -> list[tuple[str, str]]:
    cells = getattr(segment, "cells", None) or []
    values = [
        _clean_value(
            getattr(cell, "text", "")
            or (cell.get("text", "") if isinstance(cell, Mapping) else "")
        )
        for cell in cells
    ]
    pairs: list[tuple[str, str]] = []
    for index in range(0, len(values) - 1, 2):
        label = values[index]
        field_name = _FIELD_LABELS.get(label)
        if field_name and _valid_project_field_value(field_name, values[index + 1]):
            pairs.append((label, values[index + 1]))
    return pairs


def _excluded_project_info_segments(mapped: DocumentMap) -> set[str]:
    excluded: set[str] = set()
    for item in [*mapped.ignored_sections, *mapped.sections]:
        if item.domain in {"template", "contract"}:
            excluded.update(item.source_segment_ids)
            excluded.update(item.source.segment_ids)
            for item_range in _document_ranges(item):
                excluded.update(item_range.segment_ids)
                excluded.update(item_range.source.segment_ids)
    return excluded


def extract_project_info(
    document: DocumentIR | Mapping[str, Any],
    document_map: DocumentMap | Mapping[str, Any],
) -> ProjectInfoOutput:
    parsed = _as_document(document)
    mapped = _as_map(document_map)
    fields: list[ProjectInfoField] = []
    source_ids: list[str] = []
    excluded = _excluded_project_info_segments(mapped)
    project_scopes = _mapped_sections(parsed, mapped, {"project_info"})
    allowed_ids = {
        segment_id
        for _, segment_ids in project_scopes
        for segment_id in segment_ids
    }
    candidate_segments = [
        segment
        for segment in parsed.segments
        if segment.id
        and (not allowed_ids or segment.id in allowed_ids)
        and segment.id not in excluded
    ]
    pattern = _structured_label_pattern()

    for segment_index, segment in enumerate(candidate_segments):
        if not segment.id or not str(segment.text or "").strip():
            continue
        text = str(segment.text or "")
        candidates: list[tuple[str, str, float]] = [
            (match.group("label"), text[match.end():].split("|", 1)[0], 0.88)
            for match in pattern.finditer(text)
        ]
        candidates.extend((label, value, 0.96) for label, value in _table_key_value_pairs(segment))
        matches = list(pattern.finditer(text))
        for index, (label, raw_value, confidence) in enumerate(candidates):
            field_name = _FIELD_LABELS[label]
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            if index < len(matches):
                raw_value = text[matches[index].end():end]
            value = _clean_value(raw_value)
            if not value and index + 1 < len(parsed.segments):
                next_segment = (
                    candidate_segments[segment_index + 1]
                    if segment_index + 1 < len(candidate_segments)
                    else None
                )
                if next_segment is None:
                    continue
                if next_segment.id not in excluded and re.match(r"^\s*[^：:]{0,20}[:：]", next_segment.text or ""):
                    value = _clean_value(re.split(r"[:：]", next_segment.text, maxsplit=1)[-1])
                    confidence = 0.96
            if not _valid_project_field_value(field_name, value):
                continue
            field_id = f"FIELD-{field_name}-{segment.id}-{len(fields)}"
            fields.append(
                ProjectInfoField(
                    fieldId=field_id,
                    fieldName=field_name,
                    label=label,
                    value=value,
                    sourceSegmentIds=[segment.id],
                    sourceQuote=text[:2000],
                    source=_anchor_for_segment(parsed, segment.id),
                    confidence=confidence,
                )
            )
            source_ids.append(segment.id)

    deduped: list[ProjectInfoField] = []
    seen: set[tuple[str, str]] = set()
    for field in fields:
        identity = (field.field_name, field.value)
        if identity not in seen:
            seen.add(identity)
            deduped.append(field)
    warnings: list[str] = []
    if not any(item.domain == "project_info" for item in mapped.sections):
        warnings.append("project_info_overlay_missing")
    return ProjectInfoOutput(
        documentVersionId=parsed.document_version_id,
        fields=deduped,
        sourceSegmentIds=list(dict.fromkeys(source_ids)),
        warnings=warnings,
    )


def _content_for_segments(document: DocumentIR, segment_ids: list[str]) -> str:
    segment_map = {segment.id: segment for segment in document.segments if segment.id}
    return "\n".join(
        str(segment_map[segment_id].text or "").strip()
        for segment_id in segment_ids
        if segment_id in segment_map and str(segment_map[segment_id].text or "").strip()
    )


def _scope_anchor(document: DocumentIR, segment_ids: list[str]) -> DocumentSourceAnchor:
    return _anchor_for_segment(document, segment_ids[0]) if segment_ids else DocumentSourceAnchor()


def _build_commercial_terms(
    document: DocumentIR,
    mapped: DocumentMap,
) -> list[ScopeContentItem]:
    items: list[ScopeContentItem] = []
    for item, segment_ids in _mapped_sections(document, mapped, {"commercial"}):
        text = _content_for_segments(document, segment_ids)
        if not text:
            continue
        items.append(
            ScopeContentItem(
                itemId=f"COMMERCIAL-{item.map_id}",
                title=item.title or "商务要求",
                text=text[:6000],
                rawText=text[:6000],
                sourceSegmentIds=segment_ids,
                source=_scope_anchor(document, segment_ids),
                handlingMode="display",
                confidence=item.confidence,
            )
        )
    return items


_SCORE_VALUE_RE = re.compile(r"(?P<score>\d+(?:\.\d+)?)\s*分")


def _build_evaluation_criteria(
    document: DocumentIR,
    mapped: DocumentMap,
) -> list[EvaluationCriterionOutput]:
    segment_map = {segment.id: segment for segment in document.segments if segment.id}
    result: list[EvaluationCriterionOutput] = []
    seen: set[tuple[str, str]] = set()
    for item, segment_ids in _mapped_sections(document, mapped, {"evaluation"}):
        for ordinal, segment_id in enumerate(segment_ids, start=1):
            segment = segment_map.get(segment_id)
            text = str(segment.text or "").strip() if segment else ""
            if not text:
                continue
            scores = [float(match.group("score")) for match in _SCORE_VALUE_RE.finditer(text)]
            cells = [cell.strip() for cell in text.split("|") if cell.strip()]
            if not scores and len(cells) < 2:
                continue
            if cells and all(_compact(cell) in {"评分因素", "评分项", "评审项目", "分值", "评分标准"} for cell in cells):
                continue
            title = next(
                (
                    cell
                    for cell in cells
                    if not _SCORE_VALUE_RE.fullmatch(cell)
                    and "评分标准" not in cell
                ),
                item.title or "评分项",
            )
            identity = (segment_id, title)
            if identity in seen:
                continue
            seen.add(identity)
            result.append(
                EvaluationCriterionOutput(
                    itemId=f"EVAL-{item.map_id}-{ordinal}",
                    criterionId=f"EVAL-{item.map_id}-{ordinal}",
                    criterionName=title[:240],
                    title=title[:240],
                    text=text[:4000],
                    rawText=text[:4000],
                    criterionType="scoring",
                    maxScore=max(scores) if scores else None,
                    scoringRule=text[:2000],
                    sourceSegmentIds=[segment_id],
                    source=_anchor_for_segment(document, segment_id),
                    handlingMode="database_match",
                    confidence=item.confidence,
                )
            )
    return result


def build_scope_processing_output(
    document: DocumentIR | Mapping[str, Any],
    document_map: DocumentMap | Mapping[str, Any],
) -> ScopeProcessingOutput:
    parsed = _as_document(document)
    mapped = _as_map(document_map)
    rejection_overlay = next((item for item in mapped.sections if item.domain == "rejection"), None)
    rejection_candidates = list(mapped.rejection_candidates)
    rejection = RejectionOutput(
        documentVersionId=parsed.document_version_id,
        overlayMapId=rejection_overlay.map_id if rejection_overlay else "",
        candidates=rejection_candidates,
        hardRiskCount=sum(1 for item in rejection_candidates if item.is_hard_risk),
        sourceSegmentIds=list(
            dict.fromkeys(
                segment_id
                for item in rejection_candidates
                for segment_id in item.source.segment_ids
            )
        ),
    )
    locators = [
        item.template_locator
        for item in mapped.sections
        if item.domain == "template" and item.template_locator is not None
    ]
    readable: list[str] = []
    warnings: list[str] = []
    service = DocumentReadService(parsed)
    for locator in locators:
        try:
            service.read_template_range(locator)
            readable.append(locator.template_id)
        except Exception as error:
            warnings.append(f"template_unreadable:{locator.template_id}:{type(error).__name__}")
    templates = TemplateOutput(
        documentVersionId=parsed.document_version_id,
        locators=locators,
        readableTemplateIds=readable,
        warnings=warnings,
    )
    ignored = [
        *mapped.ignored_sections,
        *[
            item
            for item in mapped.sections
            if item.domain == "contract" and item not in mapped.ignored_sections
        ],
    ]
    contracts = ContractIgnoreOutput(
        documentVersionId=parsed.document_version_id,
        ignoredSections=ignored,
        ignoredSegmentIds=list(
            dict.fromkeys(
                segment_id
                for item in ignored
                for segment_id in _map_segment_ids(parsed, item)
            )
        ),
    )
    return ScopeProcessingOutput(
        documentVersionId=parsed.document_version_id,
        projectInfo=extract_project_info(parsed, mapped),
        commercialTerms=_build_commercial_terms(parsed, mapped),
        evaluationCriteria=_build_evaluation_criteria(parsed, mapped),
        rejection=rejection,
        templates=templates,
        contracts=contracts,
    )


__all__ = [
    "PROJECT_INFO_FIELD_DEFINITIONS",
    "ProjectInfoField",
    "ProjectInfoOutput",
    "ScopeContentItem",
    "EvaluationCriterionOutput",
    "RejectionOutput",
    "TemplateOutput",
    "ContractIgnoreOutput",
    "ScopeProcessingOutput",
    "extract_project_info",
    "build_scope_processing_output",
]
