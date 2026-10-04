from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ..agent_runtime.contracts import build_agent_result, now_iso
from ..agent_runtime.submission_structure import (
    extract_submission_format_text,
    merge_submission_structures,
    parse_submission_format_tree,
)
from ..tender_matching.document_fine_matching import extract_tender_requirements_by_rule
from .prompts.parse import requirement_evaluation_prompt, structure_meta_prompt


class PiParseOrchestrationError(RuntimeError):
    """Pi 解析编排无法产生任何有效子智能体结果。"""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _clip_text(value: Any, limit: int = 120000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.72)
    tail = limit - head
    return f"{text[:head]}\n...[中间内容已截断]...\n{text[-tail:]}"


def _strings(value: Any) -> list[str]:
    return [_text(item, 500) for item in _as_list(value) if _text(item, 500)]


def _chunk_document_text(
    text: str,
    *,
    max_chars: int = 18000,
    overlap_chars: int = 1000,
) -> list[str]:
    value = str(text or "").strip()
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]
    chunks: list[str] = []
    start = 0
    while start < len(value):
        target_end = min(start + max_chars, len(value))
        end = target_end
        if target_end < len(value):
            newline = value.rfind("\n", start + max_chars // 2, target_end)
            if newline > start:
                end = newline
        chunk = value[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(value):
            break
        start = max(end - overlap_chars, start + 1)
    return chunks


def _template_relevant_text(text: str, *, max_chars: int = 8000) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    composition_markers = (
        "投标文件的组成",
        "投标文件组成",
        "响应文件的组成",
        "响应文件组成",
        "分别编制",
        "分别提交",
        "分册",
    )
    composition_positions = [
        value.find(marker) for marker in composition_markers
        if value.find(marker) >= 0
    ]
    chapter_markers = (
        "第七章电子投标文件格式",
        "第七章 电子投标文件格式",
        "第七章投标文件格式",
        "第七章 投标文件格式",
        "电子投标文件格式",
        "投标文件格式",
        "响应文件格式",
    )
    chapter_positions = [
        value.rfind(marker)
        for marker in chapter_markers
        if value.rfind(marker) >= 0
    ]
    if chapter_positions and composition_positions:
        first = min(composition_positions)
        last = max(chapter_positions)
        front_start = max(first - 400, 0)
        back_start = max(last - 400, 0)
        if back_start - front_start > max_chars:
            front_size = max_chars // 2
            return (
                value[front_start : front_start + front_size]
                + "\n\n[中间内容省略]\n\n"
                + value[back_start : back_start + max_chars - front_size]
            )
    if chapter_positions:
        start = max(max(chapter_positions) - 500, 0)
        return value[start : start + max_chars]
    keywords = (
        "投标文件的组成",
        "投标文件组成",
        "投标文件应包括",
        "分别编制",
        "分别提交",
        "分册",
        "投标文件格式",
        "响应文件格式",
        "投标函",
        "报价表",
        "偏离表",
        "承诺函",
        "资格及资信证明",
    )
    positions = [
        index
        for keyword in keywords
        for index in [value.rfind(keyword)]
        if index >= 0
    ]
    if positions:
        anchor = max(positions)
        start = max(anchor - 3000, 0)
        return value[start : start + max_chars]
    return value[-max_chars:]


def _unwrap(value: Any, keys: tuple[str, ...]) -> dict[str, Any]:
    payload = _as_dict(value)
    for key in keys:
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            return candidate
    for key in ("result", "data", "output"):
        candidate = payload.get(key)
        if isinstance(candidate, dict):
            nested = _unwrap(candidate, keys)
            if nested:
                return nested
            return candidate
    return payload


def _local_document_payload(parsed: dict[str, Any], text: str) -> dict[str, Any]:
    raw_segments = parsed.get("segments") if isinstance(parsed.get("segments"), list) else []
    heading_candidates: list[dict[str, Any]] = []
    seen_headings: set[tuple[str, str]] = set()
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        value = _text(item.get("text"), 240)
        if not value or len(value) > 160:
            continue
        style = item.get("style") if isinstance(item.get("style"), dict) else {}
        outline_level = _text(style.get("outlineLevel"), 20)
        font_sizes = [
            float(size)
            for size in style.get("fontSizes", [])
            if isinstance(size, (int, float))
        ]
        heading_like = bool(
            outline_level
            or style.get("bold")
            or (font_sizes and max(font_sizes) >= 14)
        )
        if not heading_like:
            continue
        source_locator = _text(item.get("sourceLocator"), 120)
        key = (value, source_locator)
        if key in seen_headings:
            continue
        seen_headings.add(key)
        heading_candidates.append(
            {
                "id": _text(item.get("id"), 80),
                "text": value,
                "sourceLocator": source_locator,
                "order": item.get("order"),
                "outlineLevel": outline_level,
                "bold": bool(style.get("bold")),
                "fontSizes": font_sizes[:4],
            }
        )
        if len(heading_candidates) >= 320:
            break

    raw_tables = parsed.get("tables") if isinstance(parsed.get("tables"), list) else []
    table_summaries: list[dict[str, Any]] = []
    for table in raw_tables[:50]:
        if not isinstance(table, dict):
            continue
        rows = table.get("rows") if isinstance(table.get("rows"), list) else []
        preview_rows = []
        for row in rows[:4]:
            if isinstance(row, dict):
                preview_rows.append(_text(row.get("text") or row, 500))
            else:
                preview_rows.append(_text(row, 500))
        table_summaries.append(
            {
                "tableId": _text(table.get("tableId") or table.get("id"), 80),
                "sourceLocator": _text(table.get("sourceLocator"), 160),
                "rowCount": len(rows),
                "previewRows": [row for row in preview_rows if row],
            }
        )

    return {
        "textLength": len(str(text or "")),
        "blockCount": len(parsed.get("units") or []),
        "segmentCount": len(raw_segments),
        "tableCount": len(raw_tables),
        "evidenceBlocks": _persisted_evidence_blocks(parsed),
        "headingCandidates": heading_candidates,
        "tableSummaries": table_summaries,
        "images": parsed.get("images", []),
        "sections": parsed.get("sections", []),
        "metadata": parsed.get("metadata", {}),
        "structureWarnings": parsed.get("structureWarnings", []),
    }


def _persisted_evidence_blocks(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep ordered source excerpts in ParseBundle for later chapter writing."""

    raw_units = parsed.get("units")
    if not isinstance(raw_units, list) or not raw_units:
        raw_units = parsed.get("segments")
    if not isinstance(raw_units, list):
        return []

    blocks: list[dict[str, Any]] = []
    for item in raw_units[:20000]:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        block = {
            "id": _text(item.get("id"), 100),
            "sourceLocator": _text(item.get("sourceLocator"), 180),
            "contentType": _text(item.get("contentType"), 50),
            "order": item.get("order") if isinstance(item.get("order"), int) else None,
            "text": text[:2400],
        }
        table_id = _text(item.get("tableId"), 100)
        if table_id:
            block["tableId"] = table_id
        cells = item.get("cells")
        if isinstance(cells, list):
            block["cells"] = [
                str(cell.get("text") or "").strip()[:500]
                for cell in cells[:16]
                if isinstance(cell, dict) and str(cell.get("text") or "").strip()
            ]
        blocks.append(block)
    return blocks


def _local_structure(parsed: dict[str, Any]) -> dict[str, Any]:
    sections = parsed.get("sections")
    if not isinstance(sections, list):
        sections = []
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(sections, start=1):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or item.get("name"), 240)
        if not title:
            continue
        normalized.append(
            {
                **item,
                "sectionId": _text(item.get("sectionId") or item.get("id"), 80) or f"doc-sec-{index:03d}",
                "title": title,
                "level": item.get("level") if isinstance(item.get("level"), int) else 1,
                "sourceLocator": _text(item.get("sourceLocator") or title, 240),
            }
        )
    if normalized:
        return {
            "sections": normalized,
            "warnings": _strings(parsed.get("structureWarnings")),
        }

    # Some DOCX files expose heading-like runs but do not populate a
    # document-level sections array. Preserve those runs as a deterministic
    # local structure so a slow or unavailable Pi structure call has a useful
    # fallback.
    seen: set[str] = set()
    segments = parsed.get("segments") if isinstance(parsed.get("segments"), list) else []
    for item in segments:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("text"), 240)
        if not title or len(title) > 160:
            continue
        style = item.get("style") if isinstance(item.get("style"), dict) else {}
        outline_level = _text(style.get("outlineLevel"), 20)
        font_sizes = [
            float(size)
            for size in style.get("fontSizes", [])
            if isinstance(size, (int, float))
        ]
        heading_like = bool(
            outline_level
            or style.get("bold")
            or (font_sizes and max(font_sizes) >= 14)
        )
        key = " ".join(title.split()).lower()
        if not heading_like or not key or key in seen:
            continue
        seen.add(key)
        try:
            level = int(outline_level)
        except (TypeError, ValueError):
            level = 1
        normalized.append(
            {
                "sectionId": _text(item.get("id"), 80) or f"doc-sec-{len(normalized) + 1:03d}",
                "title": title,
                "level": max(1, min(level, 6)),
                "sourceLocator": _text(item.get("sourceLocator") or title, 240),
                "blockIds": [_text(item.get("id"), 80)] if _text(item.get("id"), 80) else [],
                "sectionType": "other",
            }
        )
    return {
        "sections": normalized,
        "warnings": _strings(parsed.get("structureWarnings"))
        + (["本地解析器未提供章节数组，已根据标题样式重建章节结构。"] if normalized else []),
    }


def _structure_agent_payload(
    document_payload: dict[str, Any],
    local_structure: dict[str, Any],
) -> dict[str, Any]:
    """Keep the structure agent's first call small and deterministic."""
    local_sections = [
        item
        for item in (local_structure.get("sections") or [])
        if isinstance(item, dict)
    ]
    if len(local_sections) > 3:
        local_sections = local_sections[:2] + local_sections[-1:]
    return {
        "localSections": [
            {
                "sectionId": _text(item.get("sectionId"), 80),
                "title": _text(item.get("title"), 240),
                "level": item.get("level") if isinstance(item.get("level"), int) else 1,
                "sourceLocator": _text(item.get("sourceLocator"), 180),
            }
            for item in local_sections
        ],
        "headingCandidates": [
            item
            for item in (document_payload.get("headingCandidates") or [])[:3]
            if isinstance(item, dict)
        ],
        "tableSummaries": [
            item
            for item in (document_payload.get("tableSummaries") or [])[:1]
            if isinstance(item, dict)
        ],
    }


def _structure_text_evidence(text: str, limit: int = 1600) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    head = int(limit * 0.65)
    return f"{value[:head]}\n...[中间文本省略]...\n{value[-(limit - head):]}"


def _merge_structures(
    local_structure: dict[str, Any],
    ai_structure: dict[str, Any],
) -> dict[str, Any]:
    local_sections = [
        item for item in (local_structure.get("sections") or []) if isinstance(item, dict)
    ]
    ai_sections = [
        item for item in (ai_structure.get("sections") or []) if isinstance(item, dict)
    ]
    if not local_sections:
        return ai_structure
    by_title = {
        " ".join(_text(item.get("title"), 240).split()).lower(): item
        for item in ai_sections
        if _text(item.get("title"), 240)
    }
    merged: list[dict[str, Any]] = []
    for item in local_sections:
        key = " ".join(_text(item.get("title"), 240).split()).lower()
        enrichment = by_title.get(key) or {}
        merged.append(
            {
                **item,
                "sectionType": _text(enrichment.get("sectionType"), 40)
                or item.get("sectionType")
                or "other",
            }
        )
    return {
        "sections": merged,
        "warnings": list(
            dict.fromkeys(
                _strings(local_structure.get("warnings"))
                + _strings(ai_structure.get("warnings"))
            )
        ),
    }


def _compact_structure_for_agent(
    structure: dict[str, Any],
    *,
    limit: int = 24,
) -> dict[str, Any]:
    sections = [
        item for item in (structure.get("sections") or []) if isinstance(item, dict)
    ]
    if len(sections) > limit:
        head = max(limit - 6, 1)
        sections = sections[:head] + sections[-(limit - head):]
    return {
        "sections": [
            {
                "sectionId": _text(item.get("sectionId"), 80),
                "title": _text(item.get("title"), 240),
                "level": item.get("level") if isinstance(item.get("level"), int) else 1,
                "sectionType": _text(item.get("sectionType"), 40) or "other",
                "sourceLocator": _text(item.get("sourceLocator"), 180),
            }
            for item in sections
        ],
        "warnings": _strings(structure.get("warnings")),
    }


def _rule_requirements_for_bundle(
    parsed: dict[str, Any],
    document_text: str,
) -> list[dict[str, Any]]:
    result = extract_tender_requirements_by_rule(
        document_text,
        parsed.get("segments") if isinstance(parsed.get("segments"), list) else [],
    )
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(result.get("requirements") or [], start=1):
        if not isinstance(item, dict):
            continue
        requirement = _text(item.get("text") or item.get("requirement"), 1600)
        if not requirement:
            continue
        normalized.append(
            {
                "id": f"rule-{index:03d}",
                "category": _text(item.get("category"), 60) or "other",
                "requirement": requirement,
                "mandatory": bool(item.get("mandatory", True)),
                "isRejection": bool(item.get("isRejection")),
                "sourceLocator": _text(item.get("sourceLocator"), 240),
                "sourceSection": _text(item.get("section"), 160),
                "sourceText": requirement,
                "status": "待核验",
                "extractionSource": "rule",
            }
        )
    return normalized


def _requirement_agent_sample(
    requirements: list[dict[str, Any]],
    *,
    limit: int = 6000,
) -> str:
    prioritized = sorted(
        requirements,
        key=lambda item: (
            not bool(item.get("isRejection")),
            not bool(item.get("mandatory")),
        ),
    )
    lines: list[str] = []
    size = 0
    for item in prioritized:
        line = (
            f"[{_text(item.get('id'), 40)}] "
            f"[{_text(item.get('category'), 40) or 'other'}] "
            f"{_text(item.get('requirement'), 1000)} "
            f"(来源：{_text(item.get('sourceLocator'), 160)})"
        ).strip()
        if not line or size + len(line) + 1 > limit:
            continue
        lines.append(line)
        size += len(line) + 1
        if len(lines) >= 24:
            break
    return "\n".join(lines)


def _evaluation_agent_sample(text: str, *, limit: int = 8000) -> str:
    """保留评分办法附近的原文，供条款+评分合并节点一次读取。"""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    markers = ("评标办法", "评分标准", "评分表", "评分项", "评审因素")
    positions = [value.find(marker) for marker in markers if value.find(marker) >= 0]
    if not positions:
        return _clip_text(value, limit)
    anchor = min(positions)
    start = max(anchor - 1200, 0)
    return value[start : start + limit]


def _merge_requirement_outputs(
    model_requirements: list[dict[str, Any]],
    rule_requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in [*model_requirements, *rule_requirements]:
        if not isinstance(item, dict):
            continue
        requirement = _text(item.get("requirement"), 1600)
        key = " ".join(requirement.lower().split())
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append({**item, "requirement": requirement})
    for index, item in enumerate(merged, start=1):
        item["id"] = f"req-{index:03d}"
    return merged


def _compact_requirements_for_agent(
    requirements: list[dict[str, Any]],
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    prioritized = sorted(
        [item for item in requirements if isinstance(item, dict)],
        key=lambda item: (
            not bool(item.get("isRejection")),
            not bool(item.get("mandatory")),
        ),
    )
    return [
        {
            "id": _text(item.get("id"), 80),
            "category": _text(item.get("category"), 60) or "other",
            "requirement": _text(item.get("requirement"), 1200),
            "mandatory": bool(item.get("mandatory")),
            "isRejection": bool(item.get("isRejection")),
            "sourceLocator": _text(item.get("sourceLocator"), 180),
        }
        for item in prioritized[:limit]
        if _text(item.get("requirement"), 1200)
    ]


def _normalize_structure(value: Any, parsed: dict[str, Any]) -> dict[str, Any]:
    payload = _unwrap(value, ("documentStructure", "structure"))
    raw_sections = payload.get("sections")
    if not isinstance(raw_sections, list):
        return _local_structure(parsed)
    sections: list[dict[str, Any]] = []
    for index, item in enumerate(raw_sections, start=1):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or item.get("name"), 240)
        if not title:
            continue
        sections.append(
            {
                **item,
                "sectionId": _text(item.get("sectionId") or item.get("id"), 80) or f"doc-sec-{index:03d}",
                "title": title,
                "level": item.get("level") if isinstance(item.get("level"), int) else 1,
                "sourceLocator": _text(item.get("sourceLocator") or title, 240),
                "blockIds": item.get("blockIds") if isinstance(item.get("blockIds"), list) else [],
            }
        )
    return {
        "sections": sections,
        "warnings": _strings(payload.get("warnings")),
    }


def _normalize_project_meta(value: Any) -> dict[str, Any]:
    payload = _unwrap(value, ("projectMeta", "meta"))
    fields = (
        "projectName",
        "projectCode",
        "tendererName",
        "agencyName",
        "budget",
        "deadline",
        "deliveryPeriod",
        "serviceLocation",
    )
    result = {field: _text(payload.get(field), 500) for field in fields}
    for key, item in payload.items():
        if key not in result and isinstance(item, (str, int, float, bool)):
            result[key] = item
    return result


def _normalize_requirements(value: Any) -> list[dict[str, Any]]:
    payload = _unwrap(value, ("requirements", "items"))
    raw_items = payload.get("requirements") if isinstance(payload.get("requirements"), list) else payload.get("items")
    if not isinstance(raw_items, list) and _text(payload.get("requirement"), 1600):
        raw_items = [payload]
    if not isinstance(raw_items, list):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        requirement = _text(item.get("requirement") or item.get("text") or item.get("title"), 1600)
        if not requirement:
            continue
        requirement_id = _text(item.get("id") or item.get("requirementId"), 80) or f"req-{index:03d}"
        if requirement_id in seen:
            requirement_id = f"req-{index:03d}"
        seen.add(requirement_id)
        result.append(
            {
                **item,
                "id": requirement_id,
                "requirement": requirement,
                "category": _text(item.get("category"), 80) or "other",
                "mandatory": bool(item.get("mandatory")),
                "isRejection": bool(item.get("isRejection")),
                "importance": _text(item.get("importance"), 40) or "medium",
                "responseType": _text(item.get("responseType"), 60) or "other",
                "sourceLocator": _text(item.get("sourceLocator"), 240),
                "sourceSection": _text(item.get("sourceSection"), 160),
                "sourceText": _text(item.get("sourceText") or requirement, 1600),
                "suggestedEvidence": item.get("suggestedEvidence") if isinstance(item.get("suggestedEvidence"), list) else [],
                "status": _text(item.get("status"), 40) or "待核验",
            }
        )
    return result


def _normalize_evaluations(value: Any, requirements: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = _unwrap(value, ("evaluationItems", "evaluations", "items"))
    raw_items = payload.get("evaluationItems")
    if not isinstance(raw_items, list):
        raw_items = payload.get("evaluations")
    if not isinstance(raw_items, list):
        raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return []
    valid_ids = {str(item.get("id")) for item in requirements if item.get("id")}
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        criterion = _text(item.get("criterion") or item.get("item") or item.get("criteria"), 1200)
        if not criterion:
            continue
        related = [
            _text(value, 80)
            for value in (item.get("relatedRequirementIds") or [])
            if _text(value, 80) in valid_ids
        ]
        result.append(
            {
                **item,
                "id": _text(item.get("id") or item.get("evaluationId"), 80) or f"eval-{index:03d}",
                "category": _text(item.get("category"), 80) or "other",
                "criterion": criterion,
                "criteria": _text(item.get("criteria") or criterion, 1200),
                "score": item.get("score") if isinstance(item.get("score"), (int, float)) else None,
                "writingFocus": _text(item.get("writingFocus"), 1200),
                "sourceLocator": _text(item.get("sourceLocator"), 240),
                "relatedRequirementIds": related,
            }
        )
    return result


def _normalize_template(value: Any) -> dict[str, Any]:
    from ..agent_runtime.submission_structure import normalize_submission_structure

    payload = _unwrap(value, ("bidFormatSpec", "templateSpec", "templates"))
    if not payload:
        return {
            "hasExplicitFormat": False,
            "formatSourceSection": "",
            "requiredOutline": [],
            "templates": [],
            "requiredTables": [],
            "submissionStructure": normalize_submission_structure({}),
            "warnings": ["Pi 未识别到明确的投标文件模板。"],
        }
    required_outline = payload.get("requiredOutline")
    if not isinstance(required_outline, list):
        required_outline = payload.get("outline") if isinstance(payload.get("outline"), list) else []
    if not required_outline and _text(payload.get("formatItemId") or payload.get("title"), 240):
        required_outline = [payload]
    templates = payload.get("templates") if isinstance(payload.get("templates"), list) else []
    return {
        **payload,
        "hasExplicitFormat": bool(payload.get("hasExplicitFormat") or required_outline or templates),
        "requiredOutline": [item for item in required_outline if isinstance(item, dict)],
        "templates": [item for item in templates if isinstance(item, dict)],
        "requiredTables": payload.get("requiredTables") if isinstance(payload.get("requiredTables"), list) else [],
        "submissionStructure": normalize_submission_structure(payload.get("submissionStructure")),
        "warnings": _strings(payload.get("warnings")),
    }


def _merge_template_spec_with_local_structure(
    pi_spec: dict[str, Any],
    local_structure: dict[str, Any],
) -> dict[str, Any]:
    """以本地原文结构为准，保留 Pi 的模板属性和字段描述。"""

    from ..agent_runtime.submission_structure import _normalized_title

    if not local_structure.get("volumes"):
        return pi_spec
    merged_structure = merge_submission_structures(
        local_structure,
        pi_spec.get("submissionStructure"),
    )
    pi_outline = [
        item for item in pi_spec.get("requiredOutline", [])
        if isinstance(item, dict)
    ]
    pi_by_title = {
        _normalized_title(item.get("title")): item
        for item in pi_outline
        if _normalized_title(item.get("title"))
    }
    required_outline: list[dict[str, Any]] = []
    for item in merged_structure.get("requiredOutline", []):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        supplement = pi_by_title.get(_normalized_title(row.get("title"))) or {}
        for key in (
            "type",
            "responseType",
            "sourceLocator",
            "templateType",
            "templateId",
        ):
            if row.get(key) in ("", None) and supplement.get(key) not in ("", None):
                row[key] = supplement[key]
        required_outline.append(row)

    pi_templates = [
        item for item in pi_spec.get("templates", [])
        if isinstance(item, dict)
    ]
    template_by_title = {
        _normalized_title(item.get("title")): item
        for item in pi_templates
        if _normalized_title(item.get("title"))
    }
    templates: list[dict[str, Any]] = []
    for item in required_outline:
        template_id = str(item.get("templateId") or "").strip()
        if not template_id:
            continue
        supplement = template_by_title.get(_normalized_title(item.get("title"))) or {}
        template = dict(supplement)
        template["templateId"] = template_id
        template.setdefault("title", item.get("title", ""))
        template.setdefault("templateType", item.get("templateType") or "fixed_template")
        template.setdefault("sourceLocator", item.get("sourceLocator", ""))
        template["formatItemId"] = item.get("formatItemId", "")
        template["volumeId"] = item.get("volumeId", "")
        templates.append(template)

    warnings = list(
        dict.fromkeys(
            [
                *[
                    str(item).strip()
                    for item in pi_spec.get("warnings", [])
                    if str(item).strip()
                ],
                *[
                    str(item).strip()
                    for item in merged_structure.get("warnings", [])
                    if str(item).strip()
                ],
            ]
        )
    )
    return {
        **pi_spec,
        "hasExplicitFormat": True,
        "formatSourceSection": (
            str(pi_spec.get("formatSourceSection") or "").strip()
            or str(merged_structure.get("sourceLocator") or "")
        ),
        "submissionStructure": merged_structure,
        "requiredOutline": required_outline,
        "templates": templates,
        "warnings": warnings,
    }


def _normalize_review(value: Any, warnings: list[str], risk_items: list[dict[str, Any]]) -> dict[str, Any]:
    payload = _unwrap(value, ("parseReviewReport", "review"))
    if not payload:
        return {
            "status": "failed",
            "confidence": 0,
            "missingFields": [],
            "warnings": warnings,
            "riskItems": risk_items,
        }
    return {
        **payload,
        "status": _text(payload.get("status"), 40) or "pass_with_warnings",
        "confidence": payload.get("confidence") if isinstance(payload.get("confidence"), (int, float)) else 0,
        "missingFields": payload.get("missingFields") if isinstance(payload.get("missingFields"), list) else [],
        "warnings": _strings(payload.get("warnings")),
        "riskItems": [
            item for item in (payload.get("riskItems") or risk_items)
            if isinstance(item, dict)
        ],
    }


def _derive_outline(bid_format_spec: dict[str, Any]) -> list[dict[str, Any]]:
    raw_items = bid_format_spec.get("requiredOutline")
    if not isinstance(raw_items, list):
        return []
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or item.get("name"), 240)
        if not title:
            continue
        item_type = _text(item.get("type"), 60)
        kind = "technical" if item_type in {"free_text", "chapter_skeleton"} else "commercial"
        result.append(
            {
                "sectionId": _text(item.get("sectionId"), 80) or f"custom-{index:02d}",
                "title": title,
                "kind": kind,
                "source": "copied_template",
                "requirementIds": item.get("requirementIds") if isinstance(item.get("requirementIds"), list) else [],
                "templateId": _text(item.get("templateId"), 80),
            }
        )
    return result


class PiParseOrchestrator:
    """使用 Pi 逐节点执行解析阶段的 4 个子智能体。"""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.provider = "pi"
        self.model = str(getattr(getattr(client, "config", None), "model", "") or "")

    def parse_tender(
        self,
        path: Path,
        *,
        workspace_id: str,
        project_id: str,
        project_context: dict[str, Any],
        progress_callback: Callable[[str, str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        parsed = self.client._parse_file(path)
        document_text = str(parsed.get("text") or "")
        parser_warnings = _strings(parsed.get("warnings"))
        document_payload = _local_document_payload(parsed, document_text)
        local_structure = _local_structure(parsed)
        structure_agent_payload = _structure_agent_payload(
            document_payload,
            local_structure,
        )
        structure_agent_text = (
            ""
            if local_structure.get("sections")
            else _structure_text_evidence(document_text)
        )
        legacy_mode = not callable(getattr(self.client, "run_json", None))
        total_agents = 6 if legacy_mode else 4
        agent_results: list[dict[str, Any]] = []
        all_warnings = list(parser_warnings)
        successful_agents = 0

        def is_retryable_error(exc: Exception) -> bool:
            return isinstance(exc, TimeoutError) or exc.__class__.__name__ == "PiError"

        def invoke_with_retry(
            label: str,
            fn: Callable[[], dict[str, Any]],
        ) -> tuple[dict[str, Any], list[str]]:
            retry_warnings: list[str] = []
            for attempt in range(2):
                try:
                    return fn(), retry_warnings
                except Exception as exc:
                    if attempt > 0 or not is_retryable_error(exc):
                        raise
                    retry_warnings.append(
                        f"{label} 首次调用失败，已自动重试：{str(exc)[:300]}"
                    )
            raise RuntimeError(f"{label} 重试流程异常")

        def report_progress(
            agent_id: str,
            status: str,
            completed: int,
        ) -> None:
            if not progress_callback:
                return
            try:
                progress_callback(agent_id, status, completed, total_agents)
            except Exception:
                # Progress persistence must never turn a successful model call
                # into a failed parsing node.
                return

        def run_agent(
            agent_id: str,
            fn: Callable[[], dict[str, Any]],
            input_refs: list[str],
            agent_index: int,
        ) -> dict[str, Any]:
            nonlocal successful_agents
            started = now_iso()
            report_progress(agent_id, "running", agent_index)
            try:
                raw, retry_warnings = invoke_with_retry(agent_id, fn)
                if not isinstance(raw, dict) or not raw:
                    raise ValueError("Pi 子智能体返回为空")
                successful_agents += 1
                agent_warnings = list(
                    dict.fromkeys(retry_warnings + _strings(raw.get("warnings")))
                )
                all_warnings.extend(agent_warnings)
                agent_results.append(
                    build_agent_result(
                        agent_id=agent_id,
                        provider=self.provider,
                        model=self.model,
                        status="success",
                        input_refs=input_refs,
                        output=raw,
                        source_refs=raw.get("sourceRefs") if isinstance(raw.get("sourceRefs"), list) else [],
                        warnings=agent_warnings,
                        run_id=f"{project_id or 'project'}-{agent_id}",
                        created_at=started,
                        finished_at=now_iso(),
                    )
                )
                report_progress(agent_id, "success", agent_index + 1)
                return raw
            except Exception as exc:
                message = f"{agent_id} 执行失败：{str(exc)[:500]}"
                all_warnings.append(message)
                agent_results.append(
                    build_agent_result(
                        agent_id=agent_id,
                        provider=self.provider,
                        model=self.model,
                        status="failed",
                        input_refs=input_refs,
                        output={},
                        warnings=[message],
                        run_id=f"{project_id or 'project'}-{agent_id}",
                        created_at=started,
                        finished_at=now_iso(),
                    )
                )
                report_progress(agent_id, "failed", agent_index + 1)
                return {}

        def extract_requirements_in_batches() -> dict[str, Any]:
            rule_requirements = _rule_requirements_for_bundle(parsed, document_text)
            sample_text = _requirement_agent_sample(rule_requirements)
            warnings: list[str] = []
            model_requirements: list[dict[str, Any]] = []
            requirement_agent_id = "RequirementExtractAgent"
            report_progress(requirement_agent_id, "running:1/1", 2 if legacy_mode else 1)
            # The requirement node must still run for short documents where
            # deterministic extraction found no candidates. Long documents use
            # the focused rule-candidate sample to keep the Pi input bounded;
            # short documents are sent through unchanged so the node is
            # observable and can discover requirements on its own.
            evaluation_text = _evaluation_agent_sample(document_text)
            if sample_text and evaluation_text != document_text:
                request_text = f"【条款候选】\n{sample_text}\n\n【评分相关原文】\n{evaluation_text}"
            else:
                request_text = sample_text or document_text
            request_filename = (
                f"{path.name}（规则候选样本）"
                if sample_text
                else f"{path.name}（短文档完整文本）"
            )
            raw: dict[str, Any] = {}
            try:
                if legacy_mode:
                    raw = self.client.extract_requirements(
                        document_text=request_text,
                        document_structure=agent_document_structure,
                        project_meta=project_meta,
                        filename=request_filename,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        timeout_seconds=90,
                    )
                else:
                    merged_method = getattr(self.client, "extract_requirement_evaluations", None)
                    if callable(merged_method):
                        raw = merged_method(
                            document_text=request_text,
                            document_structure=agent_document_structure,
                            project_meta=project_meta,
                            filename=request_filename,
                            workspace_id=workspace_id,
                            project_id=project_id,
                            timeout_seconds=90,
                        )
                    else:
                        raw = self.client.run_json(
                            requirement_evaluation_prompt(
                                document_text=request_text,
                                document_structure=agent_document_structure,
                                project_meta=project_meta,
                                filename=request_filename,
                                workspace_id=workspace_id,
                                project_id=project_id,
                            ),
                            timeout_seconds=90,
                        )
                model_requirements = _normalize_requirements(raw)
            except Exception as exc:
                warnings.append(
                    (
                        "Pi 要求抽取样本失败，已保留规则候选结果："
                        if sample_text
                        else "Pi 要求抽取失败："
                    )
                    + str(exc)[:300]
                )

            merged = _merge_requirement_outputs(model_requirements, rule_requirements)
            if not merged:
                detail = "；".join(warnings[-3:])
                raise ValueError(detail or "未返回可用的招标要求")
            if model_requirements:
                warnings.append(
                    f"Pi 已对 {len(model_requirements)} 条高优先级候选要求完成语义校正，"
                    f"并与 {len(rule_requirements)} 条规则候选合并。"
                )
            else:
                warnings.append(
                    f"长文档要求已由规则解析保留 {len(rule_requirements)} 条候选，"
                    "Pi 校正失败的条款需人工复核。"
                )
            return {
                "requirements": merged,
                **({} if legacy_mode else {"evaluationItems": _normalize_evaluations(raw, merged)}),
                "warnings": list(dict.fromkeys(warnings)),
            }

        def detect_templates_from_relevant_text() -> dict[str, Any]:
            focused_text = _template_relevant_text(document_text)
            local_structure = parse_submission_format_tree(document_text)
            template_document_structure = dict(agent_document_structure)
            template_document_structure["submissionStructure"] = local_structure
            raw = self.client.detect_templates(
                document_text=focused_text,
                document_structure=template_document_structure,
                filename=path.name,
                workspace_id=workspace_id,
                project_id=project_id,
                timeout_seconds=90,
            )
            required_root_keys = {
                "hasExplicitFormat",
                "requiredOutline",
                "templates",
                "requiredTables",
            }
            if not required_root_keys.issubset(raw):
                missing = "、".join(sorted(required_root_keys.difference(raw)))
                raise ValueError(f"Pi 投标模板结果缺少根字段：{missing}")
            pi_spec = _normalize_template(raw)
            spec = _merge_template_spec_with_local_structure(
                pi_spec,
                local_structure,
            )
            if len(focused_text) < len(document_text):
                spec["warnings"] = list(
                    dict.fromkeys(
                        _strings(spec.get("warnings"))
                        + [
                            "长文档模板识别已聚焦“投标文件格式”等相关内容片段；"
                            "卷和卷内文件条目以本地原文结构提取结果为准。"
                        ]
                    )
                )
            return spec

        if legacy_mode:
            structure_raw = run_agent(
                "DocumentStructureAgent",
                lambda: self.client.parse_document_structure(
                    document_text=structure_agent_text,
                    document_payload=structure_agent_payload,
                    filename=path.name,
                    parser_warnings=parser_warnings,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    project_context=project_context,
                ),
                ["artifact:sourceFile", "parse:documentBlocks"],
                0,
            )
            document_structure = _merge_structures(
                local_structure,
                _normalize_structure(structure_raw, parsed),
            )
            agent_document_structure = _compact_structure_for_agent(document_structure)
            project_meta_raw = run_agent(
                "ProjectMetaAgent",
                lambda: self.client.extract_project_meta(
                    document_text=document_text,
                    document_structure=agent_document_structure,
                    filename=path.name,
                    workspace_id=workspace_id,
                    project_id=project_id,
                    project_context=project_context,
                ),
                ["parse:documentBlocks", "parse:documentStructure"],
                1,
            )
            project_meta = _normalize_project_meta(project_meta_raw)
            requirements_raw = run_agent(
                "RequirementExtractAgent",
                extract_requirements_in_batches,
                ["parse:documentBlocks", "parse:documentStructure", "parse:projectMeta"],
                2,
            )
            requirements = _normalize_requirements(requirements_raw)
            agent_requirements = _compact_requirements_for_agent(requirements)
            evaluation_raw = run_agent(
                "EvaluationExtractAgent",
                lambda: self.client.extract_evaluations(
                    document_text=document_text,
                    document_structure=agent_document_structure,
                    requirements=agent_requirements,
                    filename=path.name,
                    workspace_id=workspace_id,
                    project_id=project_id,
                ),
                ["parse:documentBlocks", "parse:documentStructure", "parse:requirements"],
                3,
            )
            evaluation_items = _normalize_evaluations(evaluation_raw, requirements)
        else:
            structure_meta_raw = run_agent(
                "DocumentStructureAgent",
                lambda: (
                    getattr(self.client, "parse_document_structure_meta", None)(
                        document_text=document_text,
                        document_payload=structure_agent_payload,
                        filename=path.name,
                        parser_warnings=parser_warnings,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        project_context=project_context,
                    )
                    if callable(getattr(self.client, "parse_document_structure_meta", None))
                    else self.client.run_json(
                        structure_meta_prompt(
                            document_text=document_text,
                            document_payload=structure_agent_payload,
                            filename=path.name,
                            parser_warnings=parser_warnings,
                            workspace_id=workspace_id,
                            project_id=project_id,
                            project_context=project_context,
                        )
                    )
                ),
                ["artifact:sourceFile", "parse:documentBlocks"],
                0,
            )
            structure_raw = _as_dict(structure_meta_raw.get("documentStructure"))
            document_structure = _merge_structures(
                local_structure,
                _normalize_structure(structure_raw, parsed),
            )
            agent_document_structure = _compact_structure_for_agent(document_structure)
            project_meta = _normalize_project_meta(structure_meta_raw)
            requirements_raw = run_agent(
                "RequirementExtractAgent",
                extract_requirements_in_batches,
                ["parse:documentBlocks", "parse:documentStructure", "parse:projectMeta"],
                1,
            )
            requirements = _normalize_requirements(requirements_raw)
            agent_requirements = _compact_requirements_for_agent(requirements)
            evaluation_items = _normalize_evaluations(requirements_raw, requirements)

        template_raw = run_agent(
            "TemplateDetectAgent",
            detect_templates_from_relevant_text,
            ["parse:documentBlocks", "parse:documentStructure", "parse:submissionStructure"],
            4 if legacy_mode else 2,
        )
        bid_format_spec = _normalize_template(template_raw)

        review_raw = run_agent(
            "ParseReviewAgent",
            lambda: self.client.review_parse(
                project_meta=project_meta,
                document_structure=agent_document_structure,
                requirements=agent_requirements,
                evaluation_items=evaluation_items,
                bid_format_spec=bid_format_spec,
                filename=path.name,
                workspace_id=workspace_id,
                project_id=project_id,
                timeout_seconds=90,
            ),
            [
                "parse:projectMeta",
                "parse:documentStructure",
                "parse:requirements",
                "parse:evaluationItems",
                "parse:bidFormatSpec",
            ],
            5 if legacy_mode else 3,
        )
        review = _normalize_review(review_raw, all_warnings, [])
        risk_items = [
            item for item in review.get("riskItems", [])
            if isinstance(item, dict)
        ]
        outline = _derive_outline(bid_format_spec)
        if not outline:
            all_warnings.append("解析阶段未发现可直接复用的投标目录，后续由 OutlinePlanAgent 生成目录。")

        deduped_warnings = list(dict.fromkeys(item for item in all_warnings if item))
        review["warnings"] = list(dict.fromkeys(_strings(review.get("warnings")) + deduped_warnings))
        if successful_agents == 0:
            failures = []
            for result in agent_results:
                if not isinstance(result, dict) or result.get("status") != "failed":
                    continue
                agent_id = str(result.get("agentId") or "unknown-agent")
                warning = next(
                    (
                        str(item).strip()
                        for item in result.get("warnings") or []
                        if str(item).strip()
                    ),
                    "未知错误",
                )
                failures.append(f"{agent_id}：{warning}")
            detail = "；".join(failures[:3])
            suffix = f"；失败详情：{detail}" if detail else ""
            raise PiParseOrchestrationError(
                f"Pi 解析编排没有任何子智能体成功{suffix}"
            )

        return {
            "projectMeta": project_meta,
            "outline": outline,
            "requirements": requirements,
            "evaluationItems": evaluation_items,
            "riskItems": risk_items,
            "warnings": deduped_warnings,
            "documentBlocks": document_payload,
            "documentStructure": document_structure,
            "bidFormatSpec": bid_format_spec,
            "submissionStructure": bid_format_spec["submissionStructure"],
            "parseReviewReport": review,
            "agentResults": agent_results,
            "provider": self.provider,
        }
