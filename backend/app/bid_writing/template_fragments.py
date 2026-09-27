# -*- coding: utf-8 -*-
"""招标文件模板片段的标准化绑定与落盘。

模板片段只描述招标文件中已经存在的结构，不负责生成新的投标内容。
Pi 只消费这里的元数据来决定字段映射；真正的 Word 复制和填充由确定性
代码完成，避免智能体改写锁定文字、表头、表格结构或签章位置。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


TEMPLATE_FRAGMENT_SCHEMA_VERSION = "1.0"

_FIELD_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("统一社会信用代码", "creditCode", "text"),
    ("法定代表人", "legalRepresentative", "text"),
    ("法定代表", "legalRepresentative", "text"),
    ("采购人名称", "tendererName", "text"),
    ("招标人名称", "tendererName", "text"),
    ("投标人名称", "companyName", "text"),
    ("供应商名称", "companyName", "text"),
    ("投标人", "companyName", "text"),
    ("供应商", "companyName", "text"),
    ("项目名称", "projectName", "text"),
    ("项目编号", "projectCode", "text"),
    ("注册地址", "registeredAddress", "text"),
    ("联系电话", "phone", "text"),
    ("联系地址", "address", "text"),
    ("开户银行", "bankName", "text"),
    ("银行账号", "bankAccount", "text"),
    ("被授权人", "authorizedPerson", "text"),
    ("投标报价", "bidPrice", "money"),
    ("报价金额", "bidPrice", "money"),
    ("服务期限", "servicePeriod", "text"),
    ("服务地点", "serviceLocation", "text"),
    ("日期", "date", "date"),
)

_CONFIRMATION_REQUIRED_SOURCES = {
    "bidPrice",
    "date",
    "servicePeriod",
    "serviceLocation",
}


class TemplateFragment(BaseModel):
    """ParseBundle.templateFragments 中的单个模板片段。"""

    model_config = ConfigDict(extra="allow")

    schemaVersion: str = TEMPLATE_FRAGMENT_SCHEMA_VERSION
    templateId: str = ""
    formatItemId: str = ""
    volumeId: str = ""
    sectionId: str = ""
    title: str = ""
    fragmentType: str = "fixed_template"
    templateType: str = "fixed_template"
    sourceLocator: str = ""
    sourceSectionId: str = ""
    sourceFile: dict[str, Any] = Field(default_factory=dict)
    sourceFileArtifact: str = ""
    fragmentArtifact: str = ""
    bindingStatus: str = "unresolved"
    copyMode: str = "text_fallback"
    bodyChildStart: int | None = None
    bodyChildEnd: int | None = None
    blockIds: list[str] = Field(default_factory=list)
    blocks: list[dict[str, Any]] = Field(default_factory=list)
    tables: list[dict[str, Any]] = Field(default_factory=list)
    editableFields: list[dict[str, Any]] = Field(default_factory=list)
    lockedBlocks: list[dict[str, Any]] = Field(default_factory=list)
    style: dict[str, Any] = Field(default_factory=dict)
    templateText: str = ""
    warnings: list[str] = Field(default_factory=list)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _canonical_title(value: Any) -> str:
    title = _norm(value).strip("：:;；。.")
    title = re.sub(
        r"^(?:第[一二三四五六七八九十百千万零〇\d]+[篇章节部分])",
        "",
        title,
    )
    title = re.sub(
        r"^(?:[（(][一二三四五六七八九十百千万零〇\d]+[）)]|[一二三四五六七八九十百千万零〇\d]+[、.．)])",
        "",
        title,
    )
    suffix = re.compile(r"(?:[（(]?(?:格式|模板|范本|样式)[）)]?)$")
    while suffix.search(title):
        title = suffix.sub("", title).strip("：:;；。.")
    return title


def _safe_name(value: str, fallback: str = "template") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return cleaned[:120] or fallback


def _source_file(
    source_file: Any,
    source_path: Path | None,
    root: Path | None = None,
) -> dict[str, Any]:
    value = dict(_as_dict(source_file))
    if source_path is not None:
        value.setdefault("filename", source_path.name)
        value.setdefault("fileType", source_path.suffix.lower().lstrip("."))
        if root is not None:
            try:
                resolved_source = source_path.resolve()
                relative_source = resolved_source.relative_to(root.resolve())
                if resolved_source.is_file():
                    value["storagePath"] = relative_source.as_posix()
            except (OSError, ValueError):
                pass
    value.setdefault("filename", "")
    value.setdefault("storagePath", "")
    value.setdefault("fileType", Path(str(value.get("filename") or "")).suffix.lower().lstrip("."))
    return value


def _document_blocks(document_blocks: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("blocks", "units", "segments"):
        values = document_blocks.get(key)
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
    return []


def _body_child_index(block: dict[str, Any]) -> int | None:
    value = block.get("bodyChildIndex")
    return value if isinstance(value, int) else None


def _style(block: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(block.get("style"))


def _outline_level(block: dict[str, Any]) -> int | None:
    raw = _style(block).get("outlineLevel")
    if raw in (None, ""):
        style_id = str(_style(block).get("styleId") or "")
        match = re.search(r"heading\s*([1-9])", style_id, re.IGNORECASE)
        if match:
            return max(int(match.group(1)) - 1, 0)
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _is_paragraph(block: dict[str, Any]) -> bool:
    content_type = str(block.get("contentType") or "").lower()
    return not content_type or content_type == "paragraph"


def _looks_like_format_root(block: dict[str, Any]) -> bool:
    text = _norm(block.get("text") or block.get("heading"))
    if not text:
        return False
    if "投标文件格式" not in text and "响应文件格式" not in text and "电子投标文件格式" not in text:
        return False
    return bool(
        _style(block).get("bold")
        or _outline_level(block) is not None
        or "第" in text and "章" in text
    )


def _format_region_bounds(blocks: list[dict[str, Any]]) -> tuple[int | None, int | None]:
    ordered_roots = sorted(
        (
            (index, block)
            for block in blocks
            if (index := _body_child_index(block)) is not None
            and _looks_like_format_root(block)
        ),
        key=lambda item: item[0],
    )
    if not ordered_roots:
        return None, None
    start, root = ordered_roots[-1]
    root_level = _outline_level(root)
    end: int | None = None
    for index, block in sorted(
        (
            (idx, item)
            for item in blocks
            if (idx := _body_child_index(item)) is not None and idx > start
        ),
        key=lambda item: item[0],
    ):
        level = _outline_level(block)
        if level is not None and root_level is not None and level <= root_level:
            end = index
            break
        if level == 0 and _style(block).get("bold") and index > start:
            text = _norm(block.get("text") or block.get("heading"))
            if re.match(r"第[一二三四五六七八九十百千万零〇\d]+章", text):
                end = index
                break
    return start, end


def _in_range(block: dict[str, Any], start: int | None, end: int | None) -> bool:
    index = _body_child_index(block)
    if index is None:
        return False
    if start is not None and index < start:
        return False
    if end is not None and index >= end:
        return False
    return True


def _has_ordinal_prefix(text: Any) -> bool:
    value = str(text or "").strip()
    return bool(
        re.match(
            r"^(?:第[一二三四五六七八九十百千万零〇\d]+[章节部分]|"
            r"[一二三四五六七八九十百千万零〇\d]+[、.．]|"
            r"[（(][一二三四五六七八九十百千万零〇\d]+[）)]|"
            r"[①②③④⑤⑥⑦⑧⑨⑩])",
            value,
        )
    )


def _is_template_heading(block: dict[str, Any]) -> bool:
    if not _is_paragraph(block):
        return False
    text = _text(block.get("text") or block.get("heading"), 300)
    if not text or len(text) > 80:
        return False
    style = _style(block)
    level = _outline_level(block)
    return bool(
        style.get("bold")
        or level is not None
        or style.get("alignment") == "center"
    )


def _is_boundary_heading(block: dict[str, Any], start_level: int | None) -> bool:
    if not _is_template_heading(block):
        return False
    level = _outline_level(block)
    if start_level is not None and level is not None:
        return level <= start_level
    return True


def _section_candidates(document_blocks: dict[str, Any]) -> list[dict[str, Any]]:
    sections = document_blocks.get("sections")
    return [item for item in sections if isinstance(item, dict)] if isinstance(sections, list) else []


def _template_score(template: dict[str, Any], section: dict[str, Any]) -> int:
    template_title = _canonical_title(template.get("title"))
    heading = _canonical_title(section.get("heading") or section.get("title"))
    template_section_id = _text(template.get("sourceSectionId"), 100)
    section_id = _text(section.get("sectionId") or section.get("id"), 100)
    if template_section_id and section_id == template_section_id:
        return 200
    score = 0
    if template_title and template_title == heading:
        score += 100
    locator = _norm(template.get("sourceLocator") or template.get("sourceSection"))
    section_locator = _norm(section.get("startLocator") or section.get("sourceLocator"))
    if locator and section_locator and locator == section_locator:
        score += 80
    template_block_ids = {
        _text(item, 120)
        for item in _as_list(template.get("blockIds"))
        if _text(item, 120)
    }
    section_block_ids = {
        _text(item, 120)
        for item in _as_list(section.get("segmentIds") or section.get("blockIds"))
        if _text(item, 120)
    }
    score += 20 * len(template_block_ids & section_block_ids)
    return score


def _find_source_section(
    template: dict[str, Any],
    sections: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> dict[str, Any]:
    if not sections:
        return {}
    source_section_id = _text(template.get("sourceSectionId"), 100)
    if source_section_id:
        exact_id_matches = [
            section
            for section in sections
            if _text(section.get("sectionId") or section.get("id"), 100)
            == source_section_id
        ]
        if len(exact_id_matches) == 1:
            return exact_id_matches[0]

    anchor = _canonical_title(template.get("title"))
    anchor_block_ids = {
        _text(block.get("id"), 120)
        for block in blocks
        if anchor
        and _canonical_title(block.get("text") or block.get("heading")) == anchor
        and _text(block.get("id"), 120)
    }
    scored = [(candidate, _template_score(template, candidate)) for candidate in sections]
    if anchor_block_ids:
        scored = [
            (
                candidate,
                score
                + (
                    60
                    if anchor_block_ids
                    & {
                        _text(value, 120)
                        for value in _as_list(
                            candidate.get("segmentIds") or candidate.get("blockIds")
                        )
                    }
                    else 0
                ),
            )
            for candidate, score in scored
        ]
    scored.sort(key=lambda item: item[1], reverse=True)
    best, score = scored[0]
    if score <= 0:
        return {}
    if len(scored) > 1 and scored[1][1] == score:
        return {}
    return best


def _selected_blocks(
    template: dict[str, Any],
    section: dict[str, Any],
    blocks: list[dict[str, Any]],
    template_titles: set[str],
) -> list[dict[str, Any]]:
    explicit_ids = {
        _text(item, 120)
        for item in _as_list(template.get("blockIds"))
        if _text(item, 120)
    }
    section_ids = {
        _text(item, 120)
        for item in _as_list(section.get("segmentIds") or section.get("blockIds"))
        if _text(item, 120)
    }
    anchor = _canonical_title(template.get("title"))
    if not anchor or not (
        _text(template.get("templateId"), 100)
        or _text(template.get("formatItemId"), 100)
    ):
        return []
    scoped_blocks = blocks
    if section_ids:
        scoped_blocks = [
            block for block in blocks
            if _text(block.get("id"), 120) in section_ids
        ]
    ordered = sorted(
        (
            (int(block["bodyChildIndex"]), block)
            for block in scoped_blocks
            if isinstance(block.get("bodyChildIndex"), int)
        ),
        key=lambda item: item[0],
    )
    if not ordered:
        return []
    format_start, format_end = _format_region_bounds(blocks)
    anchors: list[tuple[int, int]] = []
    for position, (_, block) in enumerate(ordered):
        if (
            _canonical_title(block.get("text") or block.get("heading")) != anchor
            or not _is_paragraph(block)
        ):
            continue
        score = 100
        if _in_range(block, format_start, format_end):
            score += 80
        if _is_template_heading(block):
            score += 60
        if _has_ordinal_prefix(block.get("text") or block.get("heading")):
            score += 20
        anchors.append((position, score))
    if not anchors:
        return []
    if len(anchors) > 1 and re.search(
        r"(格式|模板|范本|样式)",
        _text(template.get("title") or template.get("name"), 120),
    ):
        return []
    anchors.sort(key=lambda item: item[1], reverse=True)
    top_score = anchors[0][1]
    top_anchors = [position for position, score in anchors if score == top_score]
    if len(top_anchors) != 1:
        return []
    raw_anchor_text = _text(
        ordered[top_anchors[0]][1].get("text")
        or ordered[top_anchors[0]][1].get("heading"),
        120,
    )
    if (
        len(anchors) > 1
        and _is_template_heading(ordered[top_anchors[0]][1])
        and re.search(r"(格式|模板|范本|样式)", raw_anchor_text)
    ):
        return []
    start = top_anchors[0]
    if explicit_ids:
        anchor_block_id = _text(ordered[start][1].get("id"), 120)
        if anchor_block_id not in explicit_ids:
            return []
    end = len(ordered)
    start_level = _outline_level(ordered[start][1])
    for index in range(start + 1, len(ordered)):
        block = ordered[index][1]
        text = _canonical_title(block.get("text") or block.get("heading"))
        is_next_template = text in template_titles and _is_paragraph(block)
        is_next_boundary = _is_boundary_heading(block, start_level)
        if is_next_boundary and text != anchor:
            end = index
            break
        if is_next_template and _is_template_heading(block) and text != anchor:
            end = index
            break
    bounded = [block for _, block in ordered[start:end]]
    if explicit_ids:
        bounded = [
            block for block in bounded
            if _text(block.get("id"), 120) in explicit_ids
        ]
        if not bounded or _text(bounded[0].get("id"), 120) != _text(ordered[start][1].get("id"), 120):
            return []
    return bounded


def _selected_tables(
    template: dict[str, Any],
    tables: list[dict[str, Any]],
    selected_blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    body_indexes = {
        int(block["bodyChildIndex"])
        for block in selected_blocks
        if isinstance(block.get("bodyChildIndex"), int)
    }
    if not body_indexes:
        return []
    first_index = min(body_indexes)
    last_index = max(body_indexes)
    table_ids = _table_ids(selected_blocks, template)
    return [
        table for table in tables
        if isinstance(table.get("bodyChildIndex"), int)
        and first_index <= int(table["bodyChildIndex"]) <= last_index
        and (
            not table_ids
            or _text(table.get("tableId") or table.get("id"), 120) in table_ids
        )
    ]


def _table_ids(blocks: list[dict[str, Any]], template: dict[str, Any]) -> set[str]:
    result = {
        _text(block.get("tableId"), 120)
        for block in blocks
        if _text(block.get("tableId"), 120)
    }
    result.update(
        _text(item.get("tableId") or item.get("id"), 120)
        for item in _as_list(template.get("tables"))
        if isinstance(item, dict)
        and _text(item.get("tableId") or item.get("id"), 120)
    )
    result.update(
        _text(item, 120)
        for item in _as_list(template.get("tableIds"))
        if _text(item, 120)
    )
    return result


def _normalize_editable_field(item: dict[str, Any], index: int) -> dict[str, Any]:
    label = _text(item.get("label") or item.get("name") or item.get("fieldName"), 160)
    field_id = _text(item.get("fieldId") or item.get("id"), 100) or f"field-{index:03d}"
    source_key = _text(
        item.get("sourceKey")
        or item.get("source")
        or item.get("suggestedSource"),
        160,
    )
    if not source_key and label:
        label_norm = _norm(label)
        source_key = next(
            (
                key
                for known_label, key, _ in _FIELD_SOURCES
                if _norm(known_label) == label_norm
            ),
            "",
        )
    return {
        **item,
        "fieldId": field_id,
        "label": label,
        "fieldName": _text(item.get("fieldName") or label, 160),
        "sourceKey": source_key,
        "valueType": _text(item.get("valueType") or item.get("type"), 60) or "text",
        "required": bool(item.get("required", True)),
        "editable": bool(item.get("editable", True)),
    }


def _looks_like_blank(text: str, label: str) -> bool:
    compact = str(text or "")
    if label not in compact:
        return False
    for match in re.finditer(re.escape(label), compact):
        tail = compact[match.end(): match.end() + 80]
        if (
            re.search(r"[_＿]{2,}", tail)
            or re.search(r"[（(]\s*[）)]", tail)
            or "【待填写" in tail
            or "填写" in tail
            or re.search(r"[:：]\s*$", tail)
        ):
            return True
    return False


def _requires_confirmation(field: dict[str, Any]) -> bool:
    source_key = _text(field.get("sourceKey"), 160)
    label = _text(
        field.get("label") or field.get("fieldName") or field.get("name"),
        160,
    )
    return bool(
        field.get("requiresConfirmation")
        or source_key in _CONFIRMATION_REQUIRED_SOURCES
        or any(
            term in label
            for term in ("报价", "服务期限", "服务地点", "工期", "交付期限", "承诺")
        )
    )


def _derive_editable_fields(
    template: dict[str, Any],
    blocks: list[dict[str, Any]],
    tables: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    specified = [
        {
            **_normalize_editable_field(item, index),
            "requiresConfirmation": _requires_confirmation(
                _normalize_editable_field(item, index)
            ),
        }
        for index, item in enumerate(_as_list(template.get("editableFields")), start=1)
        if isinstance(item, dict)
    ]
    if specified:
        return specified

    searchable = "\n".join(
        [
            _text(block.get("text"), 12000)
            for block in blocks
            if _text(block.get("text"), 12000)
        ]
        + [
            _text(cell.get("text"), 4000)
            for table in tables
            if isinstance(table, dict)
            for row in _as_list(table.get("rows"))
            if isinstance(row, dict)
            for cell in _as_list(row.get("cells"))
            if isinstance(cell, dict)
            and _text(cell.get("text"), 4000)
        ]
    )
    fields: list[dict[str, Any]] = []
    for label, source_key, value_type in _FIELD_SOURCES:
        if not _looks_like_blank(searchable, label):
            continue
        fields.append(
            {
                "fieldId": f"field-{len(fields) + 1:03d}",
                "label": label,
                "fieldName": label,
                "sourceKey": source_key,
                "valueType": value_type,
                "required": True,
                "editable": True,
                "requiresConfirmation": _requires_confirmation(
                    {"sourceKey": source_key, "label": label}
                ),
            }
        )
    return fields


def _template_text(blocks: list[dict[str, Any]], tables: list[dict[str, Any]]) -> str:
    values = [
        _text(block.get("text"), 12000)
        for block in blocks
        if _text(block.get("text"), 12000)
    ]
    for table in tables:
        for row in _as_list(table.get("rows")):
            if not isinstance(row, dict):
                continue
            cells = [
                _text(cell.get("text"), 4000)
                for cell in _as_list(row.get("cells"))
                if isinstance(cell, dict)
            ]
            if any(cells):
                values.append(" | ".join(cells))
    return "\n".join(values)[:120000]


def _fragment_for_template(
    template: dict[str, Any],
    *,
    document_blocks: dict[str, Any],
    source_file: dict[str, Any],
    source_path: Path | None,
) -> TemplateFragment:
    blocks = _document_blocks(document_blocks)
    sections = _section_candidates(document_blocks)
    source_section = _find_source_section(template, sections, blocks)
    template_titles = {
        _canonical_title(item)
        for item in _as_list(template.get("_allTemplateTitles"))
        if _canonical_title(item)
    }
    selected_blocks = _selected_blocks(
        template,
        source_section,
        blocks,
        template_titles,
    )
    document_tables = [
        item for item in _as_list(document_blocks.get("tables"))
        if isinstance(item, dict)
    ]
    explicit_table_ids = _table_ids([], template)
    explicit_tables = [
        table
        for table in document_tables
        if _text(table.get("tableId") or table.get("id"), 120) in explicit_table_ids
    ]
    all_tables = explicit_tables or _selected_tables(
        template,
        document_tables,
        selected_blocks,
    )
    body_indexes = [
        int(item["bodyChildIndex"])
        for item in [*selected_blocks, *all_tables]
        if isinstance(item.get("bodyChildIndex"), int)
    ]
    template_type = _text(
        template.get("templateType") or template.get("type"),
        80,
    ) or "fixed_template"
    fragment_type = (
        "fixed_template"
        if template_type in {"fixed_template", "fixed_form", "table", "fixed_table"}
        else template_type
    )
    source_locator = _text(
        template.get("sourceLocator")
        or source_section.get("startLocator")
        or source_section.get("sourceLocator")
        or source_section.get("heading"),
        240,
    )
    source_section_id = _text(
        template.get("sourceSectionId")
        or source_section.get("sectionId")
        or source_section.get("id"),
        100,
    )
    fragment = TemplateFragment(
        templateId=_text(template.get("templateId"), 100),
        formatItemId=_text(template.get("formatItemId"), 100),
        volumeId=_text(template.get("volumeId"), 100),
        sectionId=_text(template.get("sectionId") or source_section_id, 100),
        title=_text(template.get("title") or source_section.get("heading"), 240),
        fragmentType=fragment_type,
        templateType=template_type,
        sourceLocator=source_locator,
        sourceSectionId=source_section_id,
        sourceFile=source_file,
        sourceFileArtifact=_text(source_file.get("storagePath"), 400),
        copyMode=(
            "ooxml_body_range"
            if source_path is not None
            and source_path.suffix.lower() == ".docx"
            and source_path.is_file()
            and body_indexes
            else "text_fallback"
        ),
        bodyChildStart=min(body_indexes) if body_indexes else None,
        bodyChildEnd=max(body_indexes) if body_indexes else None,
        blockIds=[
            _text(item.get("id"), 120)
            for item in selected_blocks
            if _text(item.get("id"), 120)
        ],
        blocks=selected_blocks,
        tables=all_tables,
        editableFields=_derive_editable_fields(template, selected_blocks, all_tables),
        lockedBlocks=[
            {
                "blockId": _text(item.get("id"), 120),
                "sourceLocator": _text(item.get("sourceLocator"), 240),
                "text": _text(item.get("text"), 12000),
                "locked": True,
            }
            for item in selected_blocks
            if _text(item.get("id"), 120)
        ],
        style={
            "layout": _as_dict(document_blocks.get("metadata")).get("layout", {}),
            "sourceSectionStyle": _as_dict(source_section.get("style")),
        },
        templateText=_template_text(selected_blocks, all_tables),
        bindingStatus="ready" if selected_blocks or all_tables else "unresolved",
        warnings=[],
    )
    if not fragment.templateId:
        fragment.warnings.append("模板缺少 templateId，已使用临时片段记录。")
    if not selected_blocks and not all_tables:
        fragment.warnings.append(
            "未找到唯一且可验证的模板标题边界，未绑定原文范围；导出时将回退为文本。"
        )
    if source_path is None or not source_path.is_file():
        fragment.warnings.append("原始 DOCX 不可用；可编辑解析文本保留，但无法复制原 Word 格式。")
    if fragment.templateType == "reference_example":
        fragment.warnings.append("该模板被标记为参考示例，不应直接复制为企业承诺。")
    return fragment


def _is_template_candidate(item: dict[str, Any]) -> bool:
    item_type = _text(item.get("itemType") or item.get("type"), 80).lower()
    response_type = _text(item.get("responseType"), 80).lower()
    template_type = _text(item.get("templateType"), 80).lower()
    return (
        item_type in {"fixed_form", "fixed_table", "fixed_template", "fixed_form_template", "table"}
        or response_type in {"fixed_form", "table"}
        or template_type in {
            "fixed_template",
            "fixed_form",
            "fixed_table",
            "table",
            "reference_example",
        }
    )


def _template_candidates(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    spec = _as_dict(bundle.get("bidFormatSpec"))
    structure = _as_dict(bundle.get("submissionStructure"))
    sources: list[dict[str, Any]] = []

    for volume in _as_list(structure.get("volumes")):
        if not isinstance(volume, dict):
            continue
        for item in _as_list(volume.get("items")):
            if not isinstance(item, dict) or not _is_template_candidate(item):
                continue
            sources.append(
                {
                    **item,
                    "volumeId": item.get("volumeId") or volume.get("volumeId"),
                    "_candidateSource": "submissionStructure",
                }
            )

    for item in _as_list(spec.get("requiredOutline")):
        if not isinstance(item, dict):
            continue
        candidate = dict(item)
        candidate.setdefault("itemType", candidate.get("type"))
        if _is_template_candidate(candidate):
            candidate["_candidateSource"] = "requiredOutline"
            sources.append(candidate)

    for item in _as_list(spec.get("requiredTables")):
        if not isinstance(item, dict):
            continue
        candidate = {
            **item,
            "itemType": item.get("itemType") or "fixed_table",
            "responseType": item.get("responseType") or "table",
            "templateType": item.get("templateType") or "fixed_table",
            "_candidateSource": "requiredTables",
        }
        sources.append(candidate)

    sources.extend(
        {
            **item,
            "_candidateSource": "templates",
        }
        for item in _as_list(spec.get("templates"))
        if isinstance(item, dict)
    )

    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for index, candidate in enumerate(sources, start=1):
        title = _text(candidate.get("title") or candidate.get("name"), 240)
        if not title:
            continue
        format_item_id = _text(candidate.get("formatItemId"), 100)
        volume_id = _text(candidate.get("volumeId"), 100)
        key = (
            f"format:{format_item_id}"
            if format_item_id
            else f"title:{volume_id}:{_canonical_title(title)}:"
            f"{_text(candidate.get('order') or candidate.get('sourceLocator'), 100)}"
        )
        if key not in merged:
            order.append(key)
            merged[key] = {}
        merged[key].update(candidate)
        merged[key]["title"] = title
        if format_item_id:
            merged[key]["formatItemId"] = format_item_id
        if volume_id:
            merged[key]["volumeId"] = volume_id
        template_id = _text(candidate.get("templateId"), 100)
        if not template_id and format_item_id:
            template_id = f"tpl-{format_item_id}"
        if not template_id:
            template_id = f"tpl-{index:03d}"
        merged[key]["templateId"] = template_id

    return [merged[key] for key in order]


def attach_template_fragments_to_bundle(
    bundle: dict[str, Any],
    *,
    root: Path | None = None,
    source_path: Path | None = None,
) -> dict[str, Any]:
    """把格式条目绑定到 documentBlocks，并写入可追溯的模板片段产物。"""

    result = dict(bundle)
    document_blocks = _as_dict(result.get("documentBlocks"))
    source_file = _source_file(result.get("sourceFile"), source_path, root)
    result["sourceFile"] = source_file
    raw_templates = _template_candidates(result)
    existing_fragments = [
        dict(item)
        for item in _as_list(result.get("templateFragments"))
        if isinstance(item, dict) and _text(item.get("templateId"), 100)
    ]
    if not raw_templates and existing_fragments:
        for fragment in existing_fragments:
            if not _as_dict(fragment.get("sourceFile")):
                fragment["sourceFile"] = source_file
            if (
                source_path is not None
                and source_path.is_file()
                and source_path.suffix.lower() == ".docx"
                and isinstance(fragment.get("bodyChildStart"), int)
                and isinstance(fragment.get("bodyChildEnd"), int)
            ):
                fragment["copyMode"] = "ooxml_body_range"
                fragment["sourceFileArtifact"] = _text(
                    source_file.get("storagePath"),
                    400,
                )
            fragment.setdefault("bindingStatus", "ready")
        result["templateFragments"] = existing_fragments
        result["templateFragmentCount"] = len(existing_fragments)
        return result
    template_titles = {
        _canonical_title(item.get("title"))
        for item in raw_templates
        if _canonical_title(item.get("title"))
    }
    fragments: list[dict[str, Any]] = []
    for index, template in enumerate(raw_templates, start=1):
        normalized_template = dict(template)
        normalized_template.setdefault("templateId", f"tpl-{index:03d}")
        normalized_template["_allTemplateTitles"] = list(template_titles)
        fragment = _fragment_for_template(
            normalized_template,
            document_blocks=document_blocks,
            source_file=source_file,
            source_path=source_path,
        )
        fragments.append(fragment.model_dump(mode="json"))

    project_id = _text(result.get("projectId"), 120) or "unassigned"
    if root is not None and fragments:
        safe_project = _safe_name(project_id, "unassigned")
        fragment_dir = root / "runtime" / "projects" / safe_project / "parse" / "template-fragments"
        fragment_dir.mkdir(parents=True, exist_ok=True)
        for fragment in fragments:
            template_id = _safe_name(str(fragment.get("templateId") or "template"))
            artifact = fragment_dir / f"{template_id}.json"
            fragment["fragmentArtifact"] = str(artifact.relative_to(root)).replace("\\", "/")
            artifact.write_text(
                json.dumps(fragment, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        index_path = fragment_dir / "index.json"
        index_artifact = str(index_path.relative_to(root)).replace("\\", "/")
        index_path.write_text(
            json.dumps(
                {
                    "schemaVersion": TEMPLATE_FRAGMENT_SCHEMA_VERSION,
                    "projectId": project_id,
                    "sourceFile": source_file,
                    "templateFragments": fragments,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        result["templateFragmentsArtifact"] = index_artifact

    result["templateFragments"] = fragments
    result["templateFragmentCount"] = len(fragments)
    return result


def _fragment_matches_section(
    fragment: dict[str, Any],
    section: dict[str, Any],
) -> bool:
    format_item_id = _text(section.get("formatItemId"), 100)
    fragment_format_item_id = _text(fragment.get("formatItemId"), 100)
    template_id = _text(section.get("templateId"), 100)
    fragment_template_id = _text(fragment.get("templateId"), 100)
    if format_item_id:
        return bool(fragment_format_item_id and format_item_id == fragment_format_item_id)
    if template_id:
        return bool(fragment_template_id and template_id == fragment_template_id)

    section_volume_id = _text(section.get("volumeId"), 100)
    fragment_volume_id = _text(fragment.get("volumeId"), 100)
    if section_volume_id and fragment_volume_id and section_volume_id != fragment_volume_id:
        return False
    source_section_id = _text(section.get("sourceSectionId"), 100)
    fragment_section_id = _text(fragment.get("sourceSectionId"), 100)
    if source_section_id and fragment_section_id:
        return source_section_id == fragment_section_id
    title = _canonical_title(section.get("title"))
    fragment_title = _canonical_title(fragment.get("title"))
    return bool(title and fragment_title and title == fragment_title)


def match_template_fragment(
    section: dict[str, Any],
    fragments: list[Any],
) -> dict[str, Any]:
    """Return a single content-bearing fragment only when the binding is unique."""

    matches = [
        item
        for item in fragments
        if isinstance(item, dict)
        and _fragment_matches_section(item, section)
        and (
            item.get("bindingStatus") == "ready"
            or item.get("templateText")
            or _as_list(item.get("tables"))
            or _as_list(item.get("blocks"))
        )
    ]
    return matches[0] if len(matches) == 1 else {}


def attach_template_fragments_to_plan(
    plan: list[Any],
    fragments: list[Any],
) -> list[dict[str, Any]]:
    """给目录章节挂载模板片段引用和前端可读摘要。"""

    normalized_fragments = [
        item for item in fragments if isinstance(item, dict) and _text(item.get("templateId"), 100)
    ]
    output: list[dict[str, Any]] = []
    used_fragment_ids: set[str] = set()
    for raw_section in plan:
        if not isinstance(raw_section, dict):
            continue
        section = dict(raw_section)
        fragment = match_template_fragment(section, normalized_fragments)
        fragment_id = _text(fragment.get("templateId"), 100)
        if not fragment or fragment_id in used_fragment_ids:
            if section.get("templateId") or section.get("formatItemId"):
                section["templateStatus"] = "unresolved"
            output.append(section)
            continue
        used_fragment_ids.add(fragment_id)
        fragment_type = _text(fragment.get("fragmentType") or fragment.get("templateType"), 80)
        section["templateId"] = _text(fragment.get("templateId"), 100)
        section["templateFragmentId"] = _text(fragment.get("templateId"), 100)
        section["templateOrigin"] = _text(
            fragment.get("sourceLocator") or fragment.get("sourceFileArtifact"),
            240,
        )
        section["templateType"] = fragment_type
        section["templateCopyMode"] = _text(fragment.get("copyMode"), 80)
        section["templateStatus"] = _text(fragment.get("bindingStatus"), 40) or "bound"
        section["templateFieldCount"] = len(_as_list(fragment.get("editableFields")))
        section["templateLockedBlockCount"] = len(_as_list(fragment.get("lockedBlocks")))
        section["templateTableCount"] = len(_as_list(fragment.get("tables")))
        section["templateSourceFile"] = _as_dict(fragment.get("sourceFile"))
        section["templateBlocks"] = [
            dict(item)
            for item in _as_list(fragment.get("blocks"))
            if isinstance(item, dict)
        ]
        section["templateTables"] = [
            dict(item)
            for item in _as_list(fragment.get("tables"))
            if isinstance(item, dict)
        ]
        section["templateStyle"] = _as_dict(fragment.get("style"))
        if _text(fragment.get("templateText"), 12000):
            section["templateText"] = _text(fragment.get("templateText"), 12000)
        if fragment_type == "fixed_template":
            section["source"] = "copied_template"
        output.append(section)
    return output


def template_fragment_map(fragments: list[Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(item.get("templateId"), 100): item
        for item in fragments
        if isinstance(item, dict) and _text(item.get("templateId"), 100)
    }


def template_fragment_summaries(fragments: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "templateId": _text(item.get("templateId"), 100),
            "title": _text(item.get("title"), 240),
            "fragmentType": _text(item.get("fragmentType"), 80),
            "bindingStatus": _text(item.get("bindingStatus"), 40) or "unresolved",
            "sourceLocator": _text(item.get("sourceLocator"), 240),
            "copyMode": _text(item.get("copyMode"), 80),
            "editableFieldCount": len(_as_list(item.get("editableFields"))),
            "lockedBlockCount": len(_as_list(item.get("lockedBlocks"))),
            "tableCount": len(_as_list(item.get("tables"))),
            "fragmentArtifact": _text(item.get("fragmentArtifact"), 400),
            "warnings": [
                _text(value, 500)
                for value in _as_list(item.get("warnings"))
                if _text(value, 500)
            ],
        }
        for item in fragments
        if isinstance(item, dict) and _text(item.get("templateId"), 100)
    ]
