"""Tender analysis projections shared by extraction, matching and bid writing.

The document parser remains the single source of structural truth.  This module
only turns parser/extractor output into stable, consumer-oriented projections:

* nine business-facing modules;
* handling modes (display, database match, risk check, ...);
* source anchors that can be resolved back to the original document;
* an on-demand template index.

The projections are intentionally additive.  Existing ``requirements`` and
``fineReview.rows`` contracts remain valid while newer consumers can use
``TenderAnalysisPack``.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from typing import Any

from .contracts import coerce_requirement_category, normalize_route_hints, requirement_class_for_route
from ..workbuddy_agent.document_map.projections import attach_document_map_to_analysis_pack
from ..workbuddy_agent.document_map.schemas import DocumentMap
from .routing_contracts import unified_route_fields


ANALYSIS_PACK_SCHEMA_VERSION = "tender-analysis/1.0"

HANDLING_MODES = (
    "display",
    "database_match",
    "risk_check",
    "writing_task",
    "manual_confirm",
    "manual_fill",
    "checklist",
)

TEMPLATE_TYPES = (
    "bid_letter",
    "commitment",
    "legal_representative",
    "authorization",
    "guarantee",
    "declaration",
    "price_schedule",
    "performance_table",
    "personnel_table",
    "qualification_table",
    "technical_deviation",
    "commercial_deviation",
    "other",
)

# 说明：主线分类是 8 域（builtin_target_map.TARGET_MODULES），统一前端契约为
# reviewOutput.modules（8 域）。`build_analysis_pack` 仍按 10 模块输出，但仅作为
# WorkBuddy 路线的分析包投影使用，模块键在其函数体内按需硬编码，不再维护独立常量。


def _safe_text(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "是", "有"}:
            return True
        if lowered in {"false", "0", "no", "n", "否", "无"}:
            return False
    return default if value is None else bool(value)


def _confidence(value: Any, default: float = 0.8) -> float:
    try:
        return min(max(float(value), 0.0), 1.0)
    except (TypeError, ValueError):
        return default


def _source_ids(raw: dict[str, Any]) -> list[str]:
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    values = (
        raw.get("sourceSegmentIds")
        or raw.get("segmentIds")
        or source.get("segmentIds")
        or [raw.get("sourceSegmentId") or raw.get("segmentId") or source.get("segmentId")]
    )
    if not isinstance(values, list):
        values = [values]
    result: list[str] = []
    for value in values:
        token = _safe_text(value, 120)
        if token and token not in result:
            result.append(token)
    return result


def _section_by_id(
    sections: list[dict[str, Any]],
    section_id: str,
) -> dict[str, Any] | None:
    if not section_id:
        return None
    return next(
        (
            section
            for section in sections
            if str(section.get("sectionId") or section.get("id") or "") == section_id
        ),
        None,
    )


def _best_section(
    primary_id: str,
    sections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates = [
        section
        for section in sections
        if primary_id in {str(value) for value in section.get("segmentIds") or []}
    ]
    return min(candidates, key=lambda section: len(section.get("segmentIds") or []), default=None)


def _page_number(segment: dict[str, Any]) -> int | None:
    value = segment.get("page")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_source_anchor(
    raw: dict[str, Any],
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve a raw item to a validated parser-level source anchor.

    ``sourceSegmentIds`` are filtered against the parsed document.  For a
    template item that only supplies ``sectionId``, the section span is used as
    a fallback.  No page number is invented for DOCX/plain text documents.
    """
    segment_list = [item for item in (segments or []) if isinstance(item, dict)]
    section_list = [item for item in (sections or []) if isinstance(item, dict)]
    segment_map = {
        str(segment.get("id")): segment
        for segment in segment_list
        if str(segment.get("id") or "")
    }
    valid_ids = set(segment_map)
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    source_ids = [value for value in _source_ids(raw) if value in valid_ids]

    section_id = _safe_text(
        raw.get("sectionId") or source.get("sectionId") or source.get("sourceSectionId"),
        100,
    )
    section = _section_by_id(section_list, section_id)
    if not source_ids and section is not None:
        source_ids = [
            str(value)
            for value in section.get("segmentIds") or []
            if str(value) in valid_ids
        ]

    ordered_ids = {
        str(segment.get("id")): index
        for index, segment in enumerate(segment_list)
        if str(segment.get("id") or "")
    }
    source_ids.sort(key=lambda value: ordered_ids.get(value, 10**9))
    primary_id = source_ids[0] if source_ids else ""
    primary = segment_map.get(primary_id, {})
    best_section = section or _best_section(primary_id, section_list)
    section_path = [
        str(value)
        for value in (
            raw.get("sectionPath")
            or source.get("sectionPath")
            or (best_section or {}).get("sectionPath")
            or []
        )
        if str(value).strip()
    ]
    resolved_section_id = _safe_text(
        section_id
        or (best_section or {}).get("sectionId")
        or (best_section or {}).get("id"),
        100,
    )
    source_locator = _safe_text(
        raw.get("sourceLocator")
        or source.get("sourceLocator")
        or primary.get("sourceLocator")
        or (best_section or {}).get("startLocator"),
        200,
    )

    pages = sorted(
        {
            page
            for segment_id in source_ids
            for page in [_page_number(segment_map[segment_id])]
            if page is not None
        }
    )
    table_ids = sorted(
        {
            str(segment_map[segment_id].get("tableId"))
            for segment_id in source_ids
            if str(segment_map[segment_id].get("tableId") or "")
        }
    )
    if not table_ids and isinstance(source.get("tableIds"), list):
        table_ids = [
            token
            for token in (_safe_text(value, 100) for value in source["tableIds"])
            if token
        ]
    result = {
        "segmentId": primary_id,
        "segmentIds": source_ids,
        "sourceLocator": source_locator,
        "sectionId": resolved_section_id,
        "sectionPath": section_path,
        "tableIds": table_ids,
    }
    if pages:
        result["pageStart"] = pages[0]
        result["pageEnd"] = pages[-1]
    return result


def _item_text(raw: dict[str, Any]) -> str:
    return _safe_text(
        raw.get("normalizedText")
        or raw.get("normalizedRequirement")
        or raw.get("text")
        or raw.get("rawText")
        or raw.get("requirement")
        or raw.get("title")
        or raw.get("name")
        or raw.get("value")
    )


def _generic_item(
    raw: dict[str, Any],
    *,
    item_id: str,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
    default_mode: str = "display",
) -> dict[str, Any] | None:
    text = _item_text(raw)
    anchor = build_source_anchor(raw, segments, sections, tables)
    if not text or not anchor["segmentIds"]:
        return None
    mode = _safe_text(raw.get("handlingMode") or default_mode, 40)
    if mode not in HANDLING_MODES:
        mode = default_mode
    result = {
        "id": _safe_text(raw.get("id") or raw.get("itemId") or item_id, 100),
        "title": _safe_text(raw.get("title") or raw.get("name") or text, 240),
        "text": text,
        "rawText": _safe_text(raw.get("rawText") or raw.get("text") or text),
        "normalizedText": text,
        "handlingMode": mode,
        "mandatory": _as_bool(raw.get("mandatory"), False),
        "confidence": _confidence(raw.get("confidence")),
        "source": anchor,
    }
    for key in (
        "field",
        "label",
        "value",
        "category",
        "type",
        "status",
        "requiredEvidence",
        "responseType",
        "evidenceNeed",
        "notes",
    ):
        if raw.get(key) not in (None, "", [], {}):
            result[key] = raw[key]
    return result


def _normalize_external_items(
    raw: dict[str, Any] | None,
    key: str,
    *,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    if not isinstance(raw, dict):
        return [], []
    values = raw.get(key)
    if not isinstance(values, list):
        return [], []
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    for index, item in enumerate(values, start=1):
        if not isinstance(item, dict):
            continue
        normalized_item = _generic_item(
            item,
            item_id=f"{key}-{index:03d}",
            segments=segments,
            sections=sections,
            tables=tables,
        )
        if normalized_item is None:
            warnings.append(f"{key} 第 {index} 条缺少有效原文定位，已跳过。")
            continue
        normalized.append(normalized_item)
    return normalized, warnings


def _normalize_criterion(
    raw: dict[str, Any],
    index: int,
    *,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    item = _generic_item(
        raw,
        item_id=f"criterion-{index:03d}",
        segments=segments,
        sections=sections,
        tables=tables,
        default_mode="database_match",
    )
    if item is None:
        return None
    item.update(
        {
            "criterionId": _safe_text(raw.get("criterionId") or item["id"], 100),
            "criterionNo": _safe_text(raw.get("criterionNo") or raw.get("itemNo"), 80),
            "criterionName": _safe_text(
                raw.get("criterionName") or raw.get("title") or raw.get("name") or item["text"],
                240,
            ),
            "criterionType": _safe_text(raw.get("criterionType") or raw.get("type"), 60),
            "maxScore": raw.get("maxScore"),
            "weight": raw.get("weight"),
            "scoringRule": _safe_text(raw.get("scoringRule") or raw.get("rule"), 1400),
            "requiredEvidence": _safe_text(raw.get("requiredEvidence"), 600),
        }
    )
    return item


def _normalize_submission_item(
    raw: dict[str, Any],
    index: int,
    *,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    item = _generic_item(
        raw,
        item_id=f"submission-{index:03d}",
        segments=segments,
        sections=sections,
        tables=tables,
        default_mode="checklist",
    )
    if item is None:
        return None
    item.update(
        {
            "submissionItemId": _safe_text(raw.get("submissionItemId") or item["id"], 100),
            "itemNo": _safe_text(raw.get("itemNo") or raw.get("criterionNo"), 80),
            "itemName": _safe_text(
                raw.get("itemName") or raw.get("title") or raw.get("name") or item["text"],
                240,
            ),
            "itemType": _safe_text(raw.get("itemType") or raw.get("type"), 60),
            "required": _as_bool(raw.get("required"), True),
            "signatureRequired": _as_bool(raw.get("signatureRequired"), False),
            "sealRequired": _as_bool(raw.get("sealRequired"), False),
            "formatRequirement": _safe_text(raw.get("formatRequirement"), 800),
            "copyRequirement": _safe_text(raw.get("copyRequirement"), 160),
            "templateId": _safe_text(raw.get("templateId"), 100),
        }
    )
    return item


def _normalize_template(
    raw: dict[str, Any],
    index: int,
    *,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    item = _generic_item(
        raw,
        item_id=f"tpl-agent-{index:03d}",
        segments=segments,
        sections=sections,
        tables=tables,
        default_mode="display",
    )
    if item is None:
        return None
    template_type = _safe_text(raw.get("templateType") or raw.get("type") or "other", 60)
    if template_type not in TEMPLATE_TYPES:
        template_type = "other"
    item.update(
        {
            "templateId": _safe_text(raw.get("templateId") or item["id"], 100),
            "templateType": template_type,
            "extractMode": "on_demand",
            "previewMode": "source_page_or_segment",
            "sourceFileId": _safe_text(raw.get("sourceFileId"), 160),
        }
    )
    return item


def normalize_analysis_payload(
    raw: dict[str, Any] | None,
    *,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Normalize optional WorkBuddy analysis fields without requiring them."""
    payload = raw if isinstance(raw, dict) else {}
    nested = payload.get("analysis")
    if isinstance(nested, dict):
        payload = nested
    warnings: list[str] = []
    result: dict[str, Any] = {}

    project_info, item_warnings = _normalize_external_items(
        payload, "projectInfo", segments=segments, sections=sections, tables=tables
    )
    result["projectInfo"] = project_info
    warnings.extend(item_warnings)

    commercial_terms, item_warnings = _normalize_external_items(
        payload,
        "commercialTerms",
        segments=segments,
        sections=sections,
        tables=tables,
    )
    result["commercialTerms"] = commercial_terms
    warnings.extend(item_warnings)

    criteria: list[dict[str, Any]] = []
    raw_criteria = payload.get("evaluationCriteria")
    if isinstance(raw_criteria, list):
        for index, item in enumerate(raw_criteria, start=1):
            if not isinstance(item, dict):
                continue
            normalized = _normalize_criterion(
                item,
                index,
                segments=segments,
                sections=sections,
                tables=tables,
            )
            if normalized is None:
                warnings.append(f"evaluationCriteria 第 {index} 条缺少有效原文定位，已跳过。")
            else:
                criteria.append(normalized)
    result["evaluationCriteria"] = criteria

    submissions: list[dict[str, Any]] = []
    raw_submissions = payload.get("submissionItems")
    if isinstance(raw_submissions, list):
        for index, item in enumerate(raw_submissions, start=1):
            if not isinstance(item, dict):
                continue
            normalized = _normalize_submission_item(
                item,
                index,
                segments=segments,
                sections=sections,
                tables=tables,
            )
            if normalized is None:
                warnings.append(f"submissionItems 第 {index} 条缺少有效原文定位，已跳过。")
            else:
                submissions.append(normalized)
    result["submissionItems"] = submissions

    for key in ("milestones", "feesAndGuarantees"):
        values, item_warnings = _normalize_external_items(
            payload, key, segments=segments, sections=sections, tables=tables
        )
        result[key] = values
        warnings.extend(item_warnings)

    templates: list[dict[str, Any]] = []
    raw_templates = payload.get("templateIndex")
    if isinstance(raw_templates, list):
        for index, item in enumerate(raw_templates, start=1):
            if not isinstance(item, dict):
                continue
            normalized = _normalize_template(
                item,
                index,
                segments=segments,
                sections=sections,
                tables=tables,
            )
            if normalized is None:
                warnings.append(f"templateIndex 第 {index} 条缺少有效原文定位，已跳过。")
            else:
                templates.append(normalized)
    result["templateIndex"] = templates
    result["warnings"] = list(
        dict.fromkeys(
            [
                *(_safe_text(value, 240) for value in payload.get("warnings", []) if _safe_text(value, 240)),
                *warnings,
            ]
        )
    )
    return result


_PROJECT_FIELD_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("projectName", ("项目名称", "采购项目名称", "招标项目名称")),
    ("projectCode", ("项目编号", "采购项目编号", "招标项目编号", "招标编号", "采购编号")),
    ("tendererName", ("招标人", "招标人名称", "采购人", "采购人名称", "采购单位")),
    ("agencyName", ("招标代理机构", "代理机构", "代理机构名称")),
    ("procurementMethod", ("采购方式", "招标方式")),
    ("fundingSource", ("资金来源",)),
    ("budgetAmount", ("预算金额", "项目预算")),
    ("maxPrice", ("最高限价", "最高投标限价", "控制价")),
    ("serviceLocation", ("项目地点", "实施地点", "服务地点", "交货地点")),
    ("projectDuration", ("工期", "项目工期")),
    ("servicePeriod", ("服务期", "服务期限",)),
    ("deliveryPeriod", ("交货期", "交付期限",)),
    ("sectionDivision", ("标段划分", "包号", "标包")),
    ("scope", ("招标范围", "采购范围", "服务范围")),
)

_MILESTONE_FIELD_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("documentDownload", ("招标文件获取时间", "采购文件获取时间", "获取招标文件", "获取采购文件", "文件下载")),
    ("clarificationDeadline", ("澄清截止时间", "答疑截止时间", "质疑截止时间")),
    ("guaranteeDeadline", ("投标保证金缴纳截止", "保证金缴纳截止")),
    ("bidDeadline", ("投标截止时间", "响应文件提交截止", "递交响应文件截止")),
    ("openingTime", ("开标时间", "开启时间")),
    ("openingLocation", ("开标地点", "开启地点")),
)

_FEE_FIELD_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bidGuarantee", ("投标保证金", "响应保证金")),
    ("performanceGuarantee", ("履约保证金",)),
    ("documentFee", ("标书费", "采购文件费用", "文件费")),
    ("agencyFee", ("代理服务费", "招标代理服务费")),
)


def _metadata_fields(document_meta: dict[str, Any] | None) -> dict[str, Any]:
    meta = document_meta if isinstance(document_meta, dict) else {}
    fields = meta.get("projectFields")
    if isinstance(fields, dict):
        return fields
    parsed = meta.get("parsed")
    if isinstance(parsed, dict):
        return parsed
    return {
        key: meta.get(key)
        for key, _labels in _PROJECT_FIELD_LABELS
        if meta.get(key) not in (None, "")
    }


_DOCUMENT_META_KEYS = (
    "name",
    "fileName",
    "title",
    "projectId",
    "projectName",
    "documentKind",
    "contentHash",
    "mimeType",
    "parseMethod",
    "pageCount",
    "sectionCount",
    "headingCount",
)

_PROJECT_FIELD_KEYS = {key for key, _labels in _PROJECT_FIELD_LABELS}


def _document_meta_projection(
    document_meta: dict[str, Any] | None,
    *,
    source_file_id: str,
    source_file_hash: str,
) -> dict[str, Any]:
    """Keep analysis source metadata small and free of document body copies."""
    raw = document_meta if isinstance(document_meta, dict) else {}
    result: dict[str, Any] = {}
    for key in _DOCUMENT_META_KEYS:
        value = raw.get(key)
        if value not in (None, "", [], {}):
            result[key] = _safe_text(value, 240)

    if source_file_id and "sourceFileId" not in result:
        result["sourceFileId"] = _safe_text(source_file_id, 160)
    if source_file_hash and "contentHash" not in result:
        result["contentHash"] = _safe_text(source_file_hash, 160)

    fields = raw.get("projectFields")
    if not isinstance(fields, dict):
        parsed = raw.get("parsed")
        fields = parsed if isinstance(parsed, dict) else {}
    field_projection: dict[str, Any] = {}
    for key in _PROJECT_FIELD_KEYS:
        value = fields.get(key) if isinstance(fields, dict) else None
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (str, int, float, bool)):
            field_projection[key] = value
    if field_projection:
        result["projectFields"] = field_projection

    return result


def _value_from_segment(
    segments: list[dict[str, Any]],
    labels: tuple[str, ...],
    value: Any = None,
) -> tuple[str, dict[str, Any] | None]:
    expected = _compact(value)
    for segment in segments:
        text = _safe_text(segment.get("text"), 1200)
        compact_text = _compact(text)
        if not text:
            continue
        if expected and expected in compact_text:
            return text, segment
        for label in labels:
            if _compact(label) in compact_text:
                match = re.search(
                    rf"{re.escape(label)}\s*[:：]\s*(.+)$",
                    text,
                    flags=re.IGNORECASE,
                )
                return (match.group(0).strip() if match else text), segment
    return "", None


def _display_value_for_field(field: str, value: Any, text: str) -> str:
    display = str(value or "").strip()
    compact = _compact(display or text)
    if field in {"bidDeadline", "openingTime", "documentDownload", "clarificationDeadline", "guaranteeDeadline"}:
        if any(term in compact for term in ("详见招标公告", "详见更正公告", "以更正公告为准")):
            return "待从招标公告/更正公告核实"
        if field in {"bidDeadline", "openingTime"} and not re.search(r"20\d{2}年|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[:：]\d{2}", display):
            if "时间" in compact and len(compact) <= 30:
                return "待从招标公告/更正公告核实"
    return display


def _metadata_analysis_payload(
    document_meta: dict[str, Any] | None,
    segments: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build a small deterministic fallback for display-only fields.

    WorkBuddy remains the preferred extractor.  This fallback makes the
    analysis pack useful when the bridge/model is unavailable, while keeping
    every emitted item tied to a parser segment whenever possible.
    """
    segment_list = [item for item in (segments or []) if isinstance(item, dict)]
    fields = _metadata_fields(document_meta)
    payload: dict[str, Any] = {
        "projectInfo": [],
        "milestones": [],
        "feesAndGuarantees": [],
        "submissionItems": [],
        "evaluationCriteria": [],
        "templateIndex": [],
    }

    def add_item(
        bucket: str,
        field: str,
        labels: tuple[str, ...],
        *,
        mode: str = "display",
    ) -> None:
        value = fields.get(field)
        text, segment = _value_from_segment(segment_list, labels, value)
        if not text or segment is None:
            return
        display_value = _display_value_for_field(field, value, text)
        payload[bucket].append(
            {
                "id": f"local-{bucket}-{field}",
                "field": field,
                "label": labels[0],
                "title": labels[0],
                "name": labels[0],
                "text": text,
                "rawText": text,
                "value": display_value,
                "handlingMode": mode,
                "sourceSegmentId": str(segment.get("id") or ""),
                "sourceLocator": str(segment.get("sourceLocator") or ""),
                "mandatory": False,
                "confidence": 0.62,
            }
        )

    for field, labels in _PROJECT_FIELD_LABELS:
        add_item("projectInfo", field, labels)
    for field, labels in _MILESTONE_FIELD_LABELS:
        add_item("milestones", field, labels, mode="checklist")
    for field, labels in _FEE_FIELD_LABELS:
        add_item("feesAndGuarantees", field, labels, mode="manual_confirm")
    return payload


_SCORE_SECTION_TERMS = (
    "评标办法",
    "评审标准",
    "评分标准",
    "综合评分",
    "技术项",
    "商务项",
    "价格项",
)

_SCORE_HEADER_TERMS = (
    "评分因素",
    "评分项",
    "评审项目",
    "分值",
    "满分",
    "评分标准",
)


def _local_evaluation_criteria(
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Deterministically index scoring rows when WorkBuddy did not return them."""
    segment_list = [item for item in (segments or []) if isinstance(item, dict)]
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, segment in enumerate(segment_list, start=1):
        text = _safe_text(segment.get("text"), 2000)
        compact = _compact(text)
        if not text or len(compact) < 8:
            continue
        section_path = " / ".join(
            str(value)
            for value in (segment.get("sectionPath") or [])
            if str(value).strip()
        )
        compact_section = _compact(section_path)
        in_score_section = any(_compact(term) in compact_section for term in _SCORE_SECTION_TERMS)
        has_score_signal = any(_compact(term) in compact for term in _SCORE_HEADER_TERMS)
        has_points = bool(re.search(r"\d+(?:\.\d+)?\s*分", text))
        if not ((in_score_section and has_points) or (has_score_signal and has_points)):
            continue
        cells = [cell.strip() for cell in text.split("|") if cell.strip()]
        if len(cells) >= 2 and all(_compact(cell) in {_compact(t) for t in _SCORE_HEADER_TERMS} for cell in cells):
            continue
        title = next(
            (
                cell
                for cell in cells
                if cell and not re.fullmatch(r"\d+(?:\.\d+)?\s*分?", cell)
                and not any(_compact(term) == _compact(cell) for term in _SCORE_HEADER_TERMS)
            ),
            text[:80],
        )
        score_match = re.search(r"(\d+(?:\.\d+)?)\s*分", text)
        key = _compact(f"{segment.get('id')}|{title}|{score_match.group(1) if score_match else ''}")
        if key in seen:
            continue
        seen.add(key)
        raw = {
            "id": f"criterion-local-{index:03d}",
            "criterionId": f"criterion-local-{index:03d}",
            "criterionName": title,
            "title": title,
            "text": text,
            "rawText": text,
            "scoringRule": text,
            "maxScore": score_match.group(1) if score_match else "",
            "requiredEvidence": "按评分标准提交对应证明材料" if any(
                term in compact for term in ("证明", "证书", "业绩", "人员", "截图", "材料")
            ) else "",
            "handlingMode": "database_match" if any(
                term in compact for term in ("证明", "证书", "业绩", "人员", "材料")
            ) else "display",
            "sourceSegmentId": str(segment.get("id") or ""),
            "sourceSegmentIds": [str(segment.get("id") or "")],
            "sourceLocator": str(segment.get("sourceLocator") or ""),
        }
        item = _normalize_criterion(
            raw,
            len(result) + 1,
            segments=segments,
            sections=sections,
            tables=tables,
        )
        if item is not None:
            result.append(item)
    return result


_FORMAT_TERMS = (
    "投标文件格式",
    "响应文件格式",
    "投标文件组成",
    "响应文件组成",
    "投标文件编制",
    "响应文件编制",
    "文件编制要求",
    "投标文件目录",
    "响应文件目录",
)

_TEMPLATE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bid_letter", ("投标函", "响应函", "投标书", "响应书")),
    ("commitment", ("承诺书", "承诺函")),
    ("legal_representative", ("法定代表人身份证明", "法人身份证明")),
    ("authorization", ("授权委托书", "授权书", "委托书")),
    ("guarantee", ("保证金说明", "投标保证金", "履约保证金")),
    ("declaration", ("声明", "资格审查声明")),
    ("price_schedule", ("开标一览", "报价明细", "分项报价", "报价表", "报价单")),
    ("performance_table", ("业绩一览表", "类似业绩", "项目业绩", "业绩表")),
    ("personnel_table", ("人员配置表", "人员一览表", "项目人员", "人员表")),
    ("qualification_table", ("企业资信一览表", "资格证明", "资质一览", "资格表")),
    ("technical_deviation", ("技术偏离表", "技术规格偏离", "技术条款偏离")),
    ("commercial_deviation", ("商务偏离表", "商务条款偏离")),
)

_TEMPLATE_REJECT_TERMS = (
    "要求",
    "情形",
    "不予退还",
    "账户",
    "规定",
    "办理",
    "缴纳",
    "获取",
    "时间",
    "地点",
    "须知",
    "办法",
    "评分",
    "合同条款",
)

_FORMAT_SECTION_TERMS = (
    "投标文件格式",
    "响应文件格式",
    "电子投标文件格式",
    "附件",
)


def _template_type_for_title(title: str) -> str:
    compact = _compact(title)
    for template_type, keywords in _TEMPLATE_RULES:
        if any(_compact(keyword) in compact for keyword in keywords):
            return template_type
    return ""


def build_template_index(
    sections: list[dict[str, Any]] | None,
    segments: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Find template locations deterministically; do not extract template body."""
    section_list = [item for item in (sections or []) if isinstance(item, dict)]
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    generic_format_titles = {_compact(value) for value in _FORMAT_TERMS}

    for section in section_list:
        title = _safe_text(section.get("heading") or section.get("title"), 240)
        if not title:
            continue
        template_type = _template_type_for_title(title)
        if not template_type:
            continue
        compact_title = _compact(title)
        if compact_title in generic_format_titles:
            continue
        path = " / ".join(str(value) for value in (section.get("sectionPath") or []) if str(value).strip())
        compact_path = _compact(path)
        if any(_compact(term) in compact_title for term in _TEMPLATE_REJECT_TERMS):
            continue
        if not (
            any(_compact(term) in compact_path for term in _FORMAT_SECTION_TERMS)
            or any(_compact(term) in compact_title for term in ("投标函", "响应函", "授权", "承诺", "报价表", "报价明细表", "偏离表", "声明函", "业绩表", "人员表"))
        ):
            continue
        section_id = _safe_text(section.get("sectionId") or section.get("id"), 100)
        key = (template_type, compact_title)
        if key in seen:
            continue
        seen.add(key)
        anchor = build_source_anchor(
            {
                "sectionId": section_id,
                "sourceSegmentIds": section.get("segmentIds") or [],
                "sourceLocator": section.get("startLocator") or "",
                "sectionPath": section.get("sectionPath") or [],
            },
            segments,
            section_list,
            tables,
        )
        if not anchor["segmentIds"]:
            continue
        digest = hashlib.sha1(f"{template_type}|{section_id}|{compact_title}".encode("utf-8")).hexdigest()[:10]
        result.append(
            {
                "id": f"tpl-{digest}",
                "templateId": f"tpl-{template_type}-{section_id or digest}",
                "templateType": template_type,
                "title": title,
                "text": title,
                "rawText": title,
                "normalizedText": title,
                "handlingMode": "display",
                "mandatory": False,
                "confidence": 0.86,
                "extractMode": "on_demand",
                "previewMode": "source_page_or_segment",
                "source": anchor,
            }
        )

    result.sort(
        key=lambda item: (
            item.get("source", {}).get("pageStart", 10**9),
            item.get("source", {}).get("segmentId", ""),
        )
    )
    return result


def _merge_by_identity(
    left: list[dict[str, Any]],
    right: list[dict[str, Any]],
    *,
    identity_keys: tuple[str, ...] = ("templateId", "id"),
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*left, *right]:
        if not isinstance(item, dict):
            continue
        identity = next(
            (_safe_text(item.get(key), 160) for key in identity_keys if _safe_text(item.get(key), 160)),
            "",
        )
        if not identity:
            identity = _compact(item.get("title") or item.get("text"))
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def _merge_analysis_items(
    primary: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
    *,
    identity_keys: tuple[str, ...] = ("field", "id"),
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*primary, *fallback]:
        if not isinstance(item, dict):
            continue
        identity = next(
            (
                _safe_text(item.get(key), 160)
                for key in identity_keys
                if _safe_text(item.get(key), 160)
            ),
            "",
        )
        if not identity:
            source = item.get("source") if isinstance(item.get("source"), dict) else {}
            identity = (
                _safe_text(source.get("sectionId"), 160)
                or _compact(item.get("title") or item.get("text"))
            )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def _merge_template_items(
    primary: list[dict[str, Any]],
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*primary, *fallback]:
        if not isinstance(item, dict):
            continue
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        identity = (
            _safe_text(source.get("sectionId"), 160)
            or f"{_safe_text(item.get('templateType'), 80)}|{_compact(item.get('title') or item.get('text'))}"
        )
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def _handling_mode_for_requirement(requirement: dict[str, Any]) -> str:
    category = coerce_requirement_category(
        _safe_text(requirement.get("category") or "other", 40),
        _item_text(requirement),
    )
    requirement_class = requirement_class_for_route(
        category,
        _item_text(requirement),
        requirement.get("routeHints"),
    )
    if requirement_class == "evidence":
        return "database_match"
    if requirement_class == "rejection":
        return "risk_check"
    if requirement_class == "compliance":
        return "checklist"
    if requirement_class == "fill":
        return "manual_fill"
    explicit = _safe_text(requirement.get("handlingMode"), 40)
    if explicit in HANDLING_MODES:
        return explicit
    return "writing_task"


def _requirement_projection(
    requirement: dict[str, Any],
    index: int,
    *,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    text = _item_text(requirement)
    anchor = build_source_anchor(requirement, segments, sections, tables)
    if not text or not anchor["segmentIds"]:
        return None
    category = coerce_requirement_category(
        _safe_text(requirement.get("category") or "other", 40),
        text,
    )
    route_hints = normalize_route_hints(category, text, requirement.get("routeHints"))
    unified_route = unified_route_fields(
        category,
        type_hits=[
            str(item)
            for item in (
                requirement.get("typeHits")
                or requirement.get("categoryHits")
                or [category]
            )
            if str(item)
        ],
        text=text,
        confidence=_confidence(requirement.get("confidence"), 0.7),
        needs_human_review=_as_bool(requirement.get("needsHumanReview"), False),
        mandatory=_as_bool(requirement.get("mandatory"), True),
        is_rejection=_as_bool(requirement.get("isRejection"), category == "rejection"),
        section_level=str(requirement.get("granularity") or "") == "section",
        table_row=bool(requirement.get("tableId") or requirement.get("tableIds")),
        classification_source=_safe_text(
            requirement.get("classificationSource") or "rule",
            40,
        ),
    )
    return {
        "requirementId": _safe_text(
            requirement.get("requirementId") or requirement.get("id") or f"req-{index:03d}",
            100,
        ),
        "title": _safe_text(requirement.get("title") or text, 240),
        "category": category,
        "requirementClass": unified_route["legacyRequirementClass"],
        "contentType": unified_route["contentType"],
        "directTypeHits": [
            str(item)
            for item in (requirement.get("directTypeHits") or [])
            if str(item)
        ],
        "inheritedType": _safe_text(requirement.get("inheritedType"), 40) or "other",
        "typeHits": unified_route["typeHits"],
        "primaryRoute": unified_route["primaryRoute"],
        "secondaryRoutes": unified_route["secondaryRoutes"],
        "matchDomain": unified_route["matchDomain"],
        "decisionImpact": unified_route["decisionImpact"],
        "granularity": unified_route["granularity"],
        "reviewRequired": unified_route["reviewRequired"],
        "reviewMode": unified_route["reviewMode"],
        "reviewReason": unified_route["reviewReason"],
        "classificationSource": unified_route["classificationSource"],
        "productName": _safe_text(
            requirement.get("productName")
            or requirement.get("itemName")
            or requirement.get("targetName"),
            240,
        ),
        "targetType": _safe_text(requirement.get("targetType"), 40),
        "sourceColumn": _safe_text(requirement.get("sourceColumn"), 80),
        "rawText": _safe_text(requirement.get("rawText") or requirement.get("text") or text),
        "normalizedText": text,
        "mandatory": _as_bool(requirement.get("mandatory"), True),
        "isRejection": _as_bool(requirement.get("isRejection"), category == "rejection"),
        "handlingMode": _handling_mode_for_requirement(requirement),
        "evidenceNeed": _safe_text(requirement.get("evidenceNeed"), 80),
        "responseType": _safe_text(requirement.get("responseType"), 80),
        "displayGroup": _safe_text(requirement.get("displayGroup"), 80),
        "routeHints": route_hints,
        "confidence": _confidence(requirement.get("confidence"), 0.7),
        "source": anchor,
    }


def _module_for_requirement(category: str) -> str:
    if category in {"qualification", "performance", "personnel", "financial", "credit", "region"}:
        return "qualificationRequirements"
    if category in {"technical", "service_plan"}:
        return "technicalRequirements"
    if category in {"commercial", "pricing", "contract", "other"}:
        return "commercialTerms"
    if category in {"compliance", "document"}:
        return "submissionRequirements"
    if category == "rejection":
        return "disqualificationRules"
    return "commercialTerms"


def build_analysis_pack(
    *,
    requirements: list[dict[str, Any]] | None,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
    agent_analysis: dict[str, Any] | None = None,
    source_file_id: str = "",
    source_file_hash: str = "",
    document_meta: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    stats: dict[str, Any] | None = None,
    document_map: DocumentMap | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the additive TenderAnalysisPack consumed by newer UI/workflows."""
    normalized_agent = normalize_analysis_payload(
        agent_analysis,
        segments=segments,
        sections=sections,
        tables=tables,
    )
    normalized_local = normalize_analysis_payload(
        _metadata_analysis_payload(document_meta, segments),
        segments=segments,
        sections=sections,
        tables=tables,
    )
    local_templates = build_template_index(sections, segments, tables)
    templates = _merge_template_items(
        normalized_agent["templateIndex"],
        local_templates,
    )
    for template in templates:
        if isinstance(template, dict):
            template.setdefault("sourceFileId", _safe_text(source_file_id, 160))

    normalized_requirements: list[dict[str, Any]] = []
    module_items: dict[str, list[dict[str, Any]]] = {
        key: [] for key in (
            "qualificationRequirements",
            "technicalRequirements",
            "commercialTerms",
            "submissionRequirements",
            "disqualificationRules",
        )
    }
    for index, requirement in enumerate(requirements or [], start=1):
        if not isinstance(requirement, dict):
            continue
        item = _requirement_projection(
            requirement,
            index,
            segments=segments,
            sections=sections,
            tables=tables,
        )
        if item is None:
            continue
        normalized_requirements.append(item)
        module_items[_module_for_requirement(item["category"])].append(item)

    submission_items = _merge_analysis_items(
        normalized_agent["submissionItems"],
        normalized_local["submissionItems"],
        identity_keys=("submissionItemId", "field", "id"),
    )
    project_info = _merge_analysis_items(
        normalized_agent["projectInfo"],
        normalized_local["projectInfo"],
        identity_keys=("field", "id"),
    )
    milestones = _merge_analysis_items(
        normalized_agent["milestones"],
        normalized_local["milestones"],
        identity_keys=("field", "id"),
    )
    fees_and_guarantees = _merge_analysis_items(
        normalized_agent["feesAndGuarantees"],
        normalized_local["feesAndGuarantees"],
        identity_keys=("field", "id"),
    )
    evaluation_criteria = _merge_analysis_items(
        normalized_agent["evaluationCriteria"],
        _local_evaluation_criteria(segments, sections, tables),
        identity_keys=("criterionId", "id"),
    )
    commercial_terms = _merge_analysis_items(
        normalized_agent["commercialTerms"],
        module_items["commercialTerms"],
        identity_keys=("field", "itemId", "id"),
    )
    known_submission_titles = {
        _compact(item.get("itemName") or item.get("title"))
        for item in submission_items
    }
    for template in templates:
        title_key = _compact(template.get("title"))
        if title_key and title_key in known_submission_titles:
            continue
        submission_items.append(
            {
                "id": f"submission-{template['templateId']}",
                "submissionItemId": f"submission-{template['templateId']}",
                "itemName": template["title"],
                "title": template["title"],
                "text": template["text"],
                "rawText": template["rawText"],
                "normalizedText": template["normalizedText"],
                "itemType": "template",
                "required": False,
                "signatureRequired": template["templateType"]
                in {"bid_letter", "commitment", "legal_representative", "authorization"},
                "sealRequired": template["templateType"]
                in {"bid_letter", "commitment", "declaration"},
                "templateId": template["templateId"],
                "handlingMode": "checklist",
                "mandatory": False,
                "confidence": template["confidence"],
                "source": template["source"],
            }
        )

    pack_warnings = list(
        dict.fromkeys(
            [
                *(str(value) for value in (warnings or []) if str(value).strip()),
                *normalized_agent.get("warnings", []),
            ]
        )
    )
    source = {
        "sourceFileId": _safe_text(source_file_id, 160),
        "sourceFileHash": _safe_text(source_file_hash, 160),
        "document": _document_meta_projection(
            document_meta,
            source_file_id=source_file_id,
            source_file_hash=source_file_hash,
        ),
    }
    pack = {
        "schemaVersion": ANALYSIS_PACK_SCHEMA_VERSION,
        "source": source,
        "projectInfo": project_info,
        "qualificationRequirements": module_items["qualificationRequirements"],
        "evaluationCriteria": evaluation_criteria,
        "technicalRequirements": module_items["technicalRequirements"],
        "commercialTerms": commercial_terms,
        "submissionRequirements": [
            *module_items["submissionRequirements"],
            *submission_items,
        ],
        "milestones": milestones,
        "feesAndGuarantees": fees_and_guarantees,
        "disqualificationRules": module_items["disqualificationRules"],
        "templateIndex": templates,
        "requirements": normalized_requirements,
        "moduleCounts": {
            "projectInfo": len(project_info),
            "qualificationRequirements": len(module_items["qualificationRequirements"]),
            "evaluationCriteria": len(evaluation_criteria),
            "technicalRequirements": len(module_items["technicalRequirements"]),
            "commercialTerms": len(commercial_terms),
            "submissionRequirements": len(module_items["submissionRequirements"]) + len(submission_items),
            "milestones": len(milestones),
            "feesAndGuarantees": len(fees_and_guarantees),
            "disqualificationRules": len(module_items["disqualificationRules"]),
            "templateIndex": len(templates),
        },
        "handlingModeCounts": {
            mode: sum(
                1
                for item in normalized_requirements
                if item.get("handlingMode") == mode
            )
            for mode in HANDLING_MODES
        },
        "warnings": pack_warnings,
        "stats": dict(stats or {}),
        "writingProjection": {
            "templateIndex": templates,
            "submissionItems": submission_items,
            "requirementIds": [
                item["requirementId"]
                for item in normalized_requirements
                if item.get("requirementId")
            ],
            "templateBodyMode": "on_demand",
        },
    }
    if document_map is not None:
        pack = attach_document_map_to_analysis_pack(pack, document_map)
    return pack


def build_analysis_output_schema() -> dict[str, Any]:
    """JSON schema fragment accepted in WorkBuddy batch responses."""
    anchor_fields = {
        "sourceSegmentId": {"type": "string"},
        "sourceSegmentIds": {"type": "array", "items": {"type": "string"}},
        "sourceLocator": {"type": "string"},
        "sectionId": {"type": "string"},
        "sectionPath": {"type": "array", "items": {"type": "string"}},
        "pageStart": {"type": ["number", "null"]},
        "pageEnd": {"type": ["number", "null"]},
        "tableIds": {"type": "array", "items": {"type": "string"}},
    }
    common = {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "name": {"type": "string"},
        "text": {"type": "string"},
        "rawText": {"type": "string"},
        "field": {"type": "string"},
        "label": {"type": "string"},
        "value": {"type": "string"},
        "type": {"type": "string"},
        "handlingMode": {"type": "string", "enum": list(HANDLING_MODES)},
        "mandatory": {"type": "boolean"},
        "confidence": {"type": "number"},
        **anchor_fields,
    }

    def item_schema(extra: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {**common, **(extra or {})},
        }

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "projectInfo": {
                "type": "array",
                "items": item_schema(),
            },
            "evaluationCriteria": {
                "type": "array",
                "items": item_schema(
                    {
                        "criterionId": {"type": "string"},
                        "criterionNo": {"type": "string"},
                        "criterionName": {"type": "string"},
                        "criterionType": {"type": "string"},
                        "maxScore": {"type": ["number", "null"]},
                        "weight": {"type": ["number", "null"]},
                        "scoringRule": {"type": "string"},
                        "requiredEvidence": {"type": "string"},
                    }
                ),
            },
            "submissionItems": {
                "type": "array",
                "items": item_schema(
                    {
                        "submissionItemId": {"type": "string"},
                        "itemNo": {"type": "string"},
                        "itemName": {"type": "string"},
                        "itemType": {"type": "string"},
                        "required": {"type": "boolean"},
                        "signatureRequired": {"type": "boolean"},
                        "sealRequired": {"type": "boolean"},
                        "formatRequirement": {"type": "string"},
                        "copyRequirement": {"type": "string"},
                        "templateId": {"type": "string"},
                    }
                ),
            },
            "milestones": {
                "type": "array",
                "items": item_schema(),
            },
            "feesAndGuarantees": {
                "type": "array",
                "items": item_schema(),
            },
            "templateIndex": {
                "type": "array",
                "items": item_schema(
                    {
                        "templateId": {"type": "string"},
                        "templateType": {"type": "string", "enum": list(TEMPLATE_TYPES)},
                        "extractMode": {"type": "string"},
                        "previewMode": {"type": "string"},
                    }
                ),
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
    }
