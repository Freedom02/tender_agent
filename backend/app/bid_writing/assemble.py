# -*- coding: utf-8 -*-
"""标书文档组装与 Word 导出（纯函数、无 LLM、仅 python-docx）。

职责：把 `bid_document`（plan + deviationTables）组装成单个 .docx 文件。
不碰 FastAPI、不修改已有文件；图片不在此层插图（交给后续暗标/插图层），
仅输出占位文本，避免读取不到图片文件时报错。

组装顺序：
  1. 封面/标题页（项目标题，取不到写「投标响应文件」）
  2. 目录（用 add_paragraph 简单列出章节标题，标注需在 Word 中刷新域）
  3. 按 plan 顺序逐 section 渲染（Markdown → Word 段落/表格）
  4. 偏离表（technical / commercial 各一张 Word 表格）

Markdown → Word 规则（markdown_to_docx）：
  # / ## / ### → 标题（level 1/2/3）
  - / * → 无序列表
  1. / 2. → 有序列表
  | 分隔的表格（含分隔行）→ Word 表格
  其余非空行 → 普通段落
"""
from __future__ import annotations

from copy import deepcopy
from io import BytesIO
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml import OxmlElement
from docx.opc.constants import RELATIONSHIP_TYPE as RELATIONSHIP_TYPE
from docx.parts.image import ImagePart
from docx.shared import Cm
from docx.oxml.ns import qn

from .schemas import (
    BID_MODE_OPEN,
    DEVIATION_TABLE_COMMERCIAL,
    DEVIATION_TABLE_TECHNICAL,
    DEVIATION_TABLE_TITLES,
    SECTION_KIND_DEVIATION,
)

FALLBACK_TITLE = "投标响应文件"
DEVIATION_COLUMNS: tuple[str, ...] = ("序号", "招标要求", "偏离情况", "响应说明", "证据引用")
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

__all__ = ("assemble_docx", "markdown_to_docx")


# ---------------------------------------------------------------------------
# 元信息与文件名
# ---------------------------------------------------------------------------
def _extract_title(bid_document: dict[str, Any]) -> str:
    """从 bid_document 尽力取项目标题，取不到返回默认标题。"""
    def _clean(value: Any) -> str:
        return str(value).strip() if value else ""

    for key in ("title", "projectTitle", "projectName"):
        text = _clean(bid_document.get(key))
        if text:
            return text

    project = bid_document.get("project")
    if isinstance(project, dict):
        for key in ("title", "projectName", "name"):
            text = _clean(project.get(key))
            if text:
                return text
        parsed = project.get("parsed")
        if isinstance(parsed, dict):
            for key in ("projectName", "title", "name"):
                text = _clean(parsed.get(key))
                if text:
                    return text

    return FALLBACK_TITLE


def _build_filename(bid_document: dict[str, Any]) -> str:
    """生成安全的输出文件名（标题 + 时间戳）。"""
    title = _extract_title(bid_document)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", title)
    safe = re.sub(r"\s+", "_", safe).strip("_")
    if not safe:
        safe = FALLBACK_TITLE
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{safe}_{timestamp}.docx"


# ---------------------------------------------------------------------------
# Markdown → Word
# ---------------------------------------------------------------------------
def _is_table_separator(line: str) -> bool:
    """判断是否为 Markdown 表格分隔行（如 ``| --- | :---: |``）。"""
    stripped = line.strip()
    if "|" not in stripped:
        return False
    return re.sub(r"[|\s:\-]", "", stripped) == ""


def _parse_table_row(line: str) -> list[str]:
    """把 ``|`` 分隔的一行拆成单元格，去掉首尾空单元。"""
    cells = line.strip().split("|")
    if cells and cells[0].strip() == "":
        cells = cells[1:]
    if cells and cells[-1].strip() == "":
        cells = cells[:-1]
    return [cell.strip() for cell in cells]


def _apply_table_grid(table: Any) -> None:
    """给表格套上网格样式；模板缺该样式时静默忽略。"""
    try:
        table.style = "Table Grid"
    except Exception:
        pass


def _render_markdown_table(document: Document, table_lines: list[str]) -> None:
    """把若干行 ``| 表格`` 渲染成 Word 表格（首行为表头，加粗）。"""
    header = _parse_table_row(table_lines[0])
    ncols = len(header)
    if ncols == 0:
        return

    body_rows: list[list[str]] = []
    for line in table_lines[1:]:
        cells = _parse_table_row(line)
        while len(cells) < ncols:
            cells.append("")
        body_rows.append(cells[:ncols])

    table = document.add_table(rows=1, cols=ncols)
    _apply_table_grid(table)
    for j, text in enumerate(header):
        cell = table.rows[0].cells[j]
        cell.text = text
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True

    for row in body_rows:
        cells = table.add_row().cells
        for j, text in enumerate(row):
            cells[j].text = text


def markdown_to_docx(document: Document, markdown_text: str) -> None:
    """把 Markdown 文本渲染进 Word 文档。

    支持 ``#``/``##``/``###`` 标题、``-``/``*`` 无序列表、``1.`` 有序列表、
    ``|`` 分隔表格（带分隔行）、普通段落；空文本不产生任何输出。
    """
    if not markdown_text or not markdown_text.strip():
        return

    lines = markdown_text.splitlines()
    total = len(lines)
    i = 0
    while i < total:
        stripped = lines[i].strip()

        # 表格：当前行以 | 开头，且下一行是分隔行
        if stripped.startswith("|") and i + 1 < total and _is_table_separator(lines[i + 1]):
            table_lines = [stripped]
            i += 2  # 跳过表头行 + 分隔行
            while i < total and lines[i].strip().startswith("|"):
                table_lines.append(lines[i].strip())
                i += 1
            _render_markdown_table(document, table_lines)
            continue

        # 标题（# / ## / ### ...）
        heading = re.match(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", stripped)
        if heading:
            level = min(len(heading.group(1)), 9)
            document.add_heading(heading.group(2), level=level)
            i += 1
            continue

        # 无序列表
        bullet = re.match(r"^[-*+][ \t]+(.+)$", stripped)
        if bullet:
            document.add_paragraph(bullet.group(1), style="List Bullet")
            i += 1
            continue

        # 有序列表
        numbered = re.match(r"^\d+[.、)][ \t]+(.+)$", stripped)
        if numbered:
            document.add_paragraph(numbered.group(1), style="List Number")
            i += 1
            continue

        # 普通段落（跳过空行）
        if stripped:
            document.add_paragraph(stripped)
        i += 1


# ---------------------------------------------------------------------------
# 章节 / 偏离表渲染
# ---------------------------------------------------------------------------
def _insert_asset(document: Document, asset: dict[str, Any], key: str) -> None:
    """插入一条图文资产：图片直接插图 + 图注；否则输出附件占位。"""
    kind = str(asset.get("kind") or "")
    caption = str(asset.get("caption") or "").strip() or "证明材料"
    local_path = str(asset.get("local_path") or "").strip()
    page_count = int(asset.get("page_count") or 0)
    width_cm = float(asset.get("width_cm") or 14.0)

    if kind == "image" and local_path and Path(local_path).exists():
        try:
            document.add_picture(local_path, width=Cm(width_cm))
            document.add_paragraph(f"图：{caption}", style="Caption")
            return
        except Exception:
            pass  # 图片无法插入时回退为附件占位

    document.add_paragraph(f"【附件：{caption}（{key}，共 {page_count} 页）】")


def _add_evidence_assets(document: Document, section: dict[str, Any], assets: dict[str, Any] | None) -> None:
    """为章节溯源引用插入真实图文；有资产则插图，无资产/暗标则输出占位。"""
    refs: list[dict[str, Any]] = []
    direct = section.get("evidencePath")
    if direct:
        refs.append({"evidencePath": str(direct), "name": ""})
    refs.extend(r for r in (section.get("sourceRefs") or []) if isinstance(r, dict))

    seen: set[str] = set()
    for ref in refs:
        key = str(ref.get("evidencePath") or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        asset = (assets or {}).get(key)
        if asset:
            _insert_asset(document, asset, key)
        else:
            document.add_paragraph(f"【附件：证据路径 {key}】")


def _resolve_template_source(
    fragment: dict[str, Any],
    root: Path | None,
) -> Path | None:
    source_file = fragment.get("sourceFile")
    source = source_file if isinstance(source_file, dict) else {}
    raw_path = str(
        fragment.get("sourceFilePath")
        or source.get("storagePath")
        or ""
    ).strip()
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute() and root is not None:
        candidate = root / candidate
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def _add_preview_bookmark(element: Any, name: str, bookmark_id: int) -> None:
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bookmark_id))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bookmark_id))

    properties = element.find(qn("w:pPr"))
    element.insert(1 if properties is not None else 0, start)
    element.append(end)


def render_template_preview_docx(
    fragment: dict[str, Any],
    section: dict[str, Any],
    *,
    root: Path | None = None,
) -> tuple[bytes, dict[str, Any]] | None:
    """Render an editable preview DOCX from the original package and template range."""

    source_path = _resolve_template_source(fragment, root)
    start = fragment.get("bodyChildStart")
    end = fragment.get("bodyChildEnd")
    if (
        source_path is None
        or str(fragment.get("copyMode") or "") != "ooxml_body_range"
        or not isinstance(start, int)
        or not isinstance(end, int)
        or end < start
    ):
        return None

    try:
        document = Document(str(source_path))
        body = document._element.body
        original_children = list(body)
        source_blocks_by_index = {
            int(block["bodyChildIndex"]): block
            for block in (fragment.get("blocks") or [])
            if isinstance(block, dict)
            and isinstance(block.get("bodyChildIndex"), int)
            and not str(block.get("contentType") or "").startswith("table")
            and not block.get("tableId")
        }
        edited_blocks_by_index = {
            int(block["bodyChildIndex"]): block
            for block in (section.get("templateBlocks") or [])
            if isinstance(block, dict)
            and isinstance(block.get("bodyChildIndex"), int)
            and not block.get("tableId")
        }
        source_tables_by_index = {
            int(table["bodyChildIndex"]): table
            for table in (fragment.get("tables") or [])
            if isinstance(table, dict)
            and isinstance(table.get("bodyChildIndex"), int)
        }
        edited_tables_by_id = {
            str(table.get("tableId") or table.get("id") or ""): table
            for table in (section.get("templateTables") or [])
            if isinstance(table, dict)
        }
        cell_values = section.get("templateCellValues")
        if not isinstance(cell_values, list):
            cell_values = []
        replacements = _template_replacements(section, fragment)
        bookmark_id = 0
        paragraph_bookmarks: list[dict[str, Any]] = []
        table_bookmarks: list[dict[str, Any]] = []
        table_ordinal = 0

        for body_index, child in enumerate(original_children):
            if child.tag == qn("w:sectPr"):
                continue
            if body_index < start or body_index > end:
                body.remove(child)
                continue

            source_block = source_blocks_by_index.get(body_index)
            edited_block = edited_blocks_by_index.get(body_index)
            if child.tag == qn("w:p"):
                if (
                    source_block is not None
                    and edited_block is not None
                    and str(edited_block.get("text") or "")
                    != str(source_block.get("text") or "")
                ):
                    _set_paragraph_xml_text(child, str(edited_block.get("text") or ""))
                else:
                    _replace_paragraph_xml_text(child, replacements)
                if source_block is not None:
                    bookmark_id += 1
                    name = f"codex_tpl_p_{body_index}"
                    _add_preview_bookmark(child, name, bookmark_id)
                    paragraph_bookmarks.append(
                        {"name": name, "bodyChildIndex": body_index}
                    )
            elif child.tag == qn("w:tbl"):
                table = source_tables_by_index.get(body_index)
                if table is None:
                    continue
                table_id = str(table.get("tableId") or table.get("id") or "")
                table_ordinal += 1
                _replace_template_table_cells(
                    child,
                    table_id=table_id,
                    cell_values=cell_values,
                    replacements=replacements,
                )
                _apply_response_table_rows(
                    child,
                    table_id=table_id,
                    section=section,
                )
                edited_table = edited_tables_by_id.get(table_id)
                if edited_table:
                    source_cells = {
                        (
                            int(row.get("rowIndex") or 0),
                            int(cell.get("cellIndex") or 0),
                        ): str(cell.get("text") or "")
                        for row in (table.get("rows") or [])
                        if isinstance(row, dict)
                        for cell in (row.get("cells") or [])
                        if isinstance(cell, dict)
                    }
                    has_edits = any(
                        str(
                            cell.get("filledText")
                            if cell.get("filledText") is not None
                            else cell.get("text") or ""
                        )
                        != source_cells.get(
                            (
                                int(row.get("rowIndex") or 0),
                                int(cell.get("cellIndex") or 0),
                            ),
                            "",
                        )
                        for row in (edited_table.get("rows") or [])
                        if isinstance(row, dict)
                        for cell in (row.get("cells") or [])
                        if isinstance(cell, dict)
                    )
                    if has_edits:
                        _apply_template_table_preview(child, edited_table)

                bookmark_rows: list[dict[str, Any]] = []
                for row_index, row in enumerate(child.findall(qn("w:tr")), start=1):
                    bookmark_cells: list[dict[str, Any]] = []
                    for cell_index, cell in enumerate(
                        row.findall(qn("w:tc")),
                        start=1,
                    ):
                        paragraphs = cell.findall(qn("w:p"))
                        if not paragraphs:
                            continue
                        bookmark_id += 1
                        name = (
                            f"codex_tpl_t_{table_ordinal}_"
                            f"r_{row_index}_c_{cell_index}"
                        )
                        _add_preview_bookmark(paragraphs[0], name, bookmark_id)
                        bookmark_cells.append(
                            {"cellIndex": cell_index, "name": name}
                        )
                    bookmark_rows.append(
                        {"rowIndex": row_index, "cells": bookmark_cells}
                    )
                table_bookmarks.append(
                    {
                        "tableId": table_id,
                        "tableOrdinal": table_ordinal,
                        "rows": bookmark_rows,
                    }
                )

        buffer = BytesIO()
        document.save(buffer)
        return buffer.getvalue(), {
            "paragraphs": paragraph_bookmarks,
            "tables": table_bookmarks,
        }
    except Exception:
        return None


def _copy_image_relationship(
    source_rel: Any,
    target_document: Document,
) -> str:
    source_part = source_rel.target_part
    target_package = target_document.part.package
    source_ext = str(getattr(source_part.partname, "ext", "") or ".bin")
    if not source_ext.startswith("."):
        source_ext = f".{source_ext}"
    partname = target_package.next_partname(f"/word/media/image%d{source_ext}")
    image_part = ImagePart(
        partname,
        source_part.content_type,
        source_part.blob,
        package=target_package,
    )
    try:
        target_package.image_parts.append(image_part)
    except Exception:
        pass
    return target_document.part.relate_to(image_part, source_rel.reltype)


def _remap_template_relationships(
    element: Any,
    source_document: Document,
    target_document: Document,
) -> None:
    """复制片段中的图片/外链关系，避免 raw XML 搬运后 rId 失效。"""

    source_part = source_document.part
    target_part = target_document.part
    cache: dict[str, str] = {}
    for node in element.iter():
        for attribute, value in list(node.attrib.items()):
            if not attribute.startswith(f"{{{REL_NS}}}"):
                continue
            source_rid = str(value or "")
            if not source_rid:
                continue
            if source_rid in cache:
                node.attrib[attribute] = cache[source_rid]
                continue
            try:
                source_rel = source_part.rels[source_rid]
            except KeyError:
                node.attrib.pop(attribute, None)
                continue
            try:
                if source_rel.is_external:
                    target_rid = target_part.relate_to(
                        source_rel.target_ref,
                        source_rel.reltype,
                        is_external=True,
                    )
                elif source_rel.reltype == RELATIONSHIP_TYPE.IMAGE:
                    target_rid = _copy_image_relationship(source_rel, target_document)
                else:
                    # 文本、表格和常见图片模板不需要把脚注、批注等外围
                    # 关系一并复制；删除无法解析的关系比留下失效 rId 更安全。
                    node.attrib.pop(attribute, None)
                    continue
            except Exception:
                node.attrib.pop(attribute, None)
                continue
            cache[source_rid] = target_rid
            node.attrib[attribute] = target_rid


def _replace_template_text(text: str, replacements: dict[str, str]) -> str:
    result = str(text or "")
    for label, value in replacements.items():
        label = str(label or "").strip()
        value = str(value or "").strip()
        if not label or not value:
            continue
        escaped = re.escape(label)
        result = re.sub(r"\{\{\s*" + escaped + r"\s*\}\}", value, result)
        result = re.sub(r"\[\[\s*" + escaped + r"\s*\]\]", value, result)
        result = re.sub(
            rf"{escaped}\s*[:：]\s*[（(][^）)]*填写[^）)]*[）)]",
            f"{label}：{value}",
            result,
        )
        result = re.sub(
            rf"[（(]\s*(?:由?投标人)?填写[“”\"']?{escaped}[“”\"']?\s*[）)]",
            f"（{value}）",
            result,
        )
        result = re.sub(rf"[（(]\s*{escaped}\s*[）)]", f"（{value}）", result)
        result = re.sub(
            rf"{escaped}\s*[:：]\s*[_＿\u3000 ]+",
            f"{label}：{value}",
            result,
        )
        result = re.sub(rf"{escaped}\s*[_＿]{{1,}}", f"{label}{value}", result)
    return result


def _replace_paragraph_xml_text(paragraph: Any, replacements: dict[str, str]) -> None:
    text_nodes = list(paragraph.iter(qn("w:t")))
    if not text_nodes:
        return
    original = "".join(str(node.text or "") for node in text_nodes)
    replaced = _replace_template_text(original, replacements)
    if replaced == original:
        return
    text_nodes[0].text = replaced
    for node in text_nodes[1:]:
        node.text = ""


def _set_paragraph_xml_text(paragraph: Any, text: str) -> None:
    text_nodes = list(paragraph.iter(qn("w:t")))
    if not text_nodes:
        run = OxmlElement("w:r")
        text_node = OxmlElement("w:t")
        run.append(text_node)
        paragraph.append(run)
        text_nodes = [text_node]
    text_nodes[0].text = str(text or "")
    if text[:1].isspace() or text[-1:].isspace():
        text_nodes[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for node in text_nodes[1:]:
        node.text = ""


def _apply_template_table_preview(
    table_element: Any,
    preview_table: dict[str, Any],
) -> None:
    xml_rows = list(table_element.findall(qn("w:tr")))
    for row in preview_table.get("rows") or []:
        if not isinstance(row, dict):
            continue
        row_index = int(row.get("rowIndex") or 0)
        if row_index < 1 or row_index > len(xml_rows):
            continue
        xml_cells = list(xml_rows[row_index - 1].findall(qn("w:tc")))
        for cell in row.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            cell_index = int(cell.get("cellIndex") or 0)
            if cell_index < 1 or cell_index > len(xml_cells):
                continue
            value = cell.get("filledText")
            if value is None:
                value = cell.get("text")
            _set_xml_cell_text(xml_cells[cell_index - 1], str(value or ""))


def _template_replacements(
    section: dict[str, Any],
    fragment: dict[str, Any],
) -> dict[str, str]:
    replacements: dict[str, str] = {}
    fields = section.get("templateFieldValues")
    if not isinstance(fields, list):
        fields = fragment.get("editableFields")
    for field in fields or []:
        if not isinstance(field, dict):
            continue
        if str(field.get("status") or "filled") in {"locked", "missing"}:
            continue
        label = str(
            field.get("label")
            or field.get("fieldName")
            or field.get("name")
            or ""
        ).strip()
        value = str(field.get("value") or "").strip()
        if label and value:
            replacements[label] = value
    return replacements


def _replace_template_table_cells(
    table_element: Any,
    *,
    table_id: str,
    cell_values: list[dict[str, Any]],
    replacements: dict[str, str],
) -> None:
    values = {
        (
            str(item.get("tableId") or ""),
            str(item.get("rowIndex") or ""),
            str(item.get("cellIndex") or ""),
        ): item
        for item in cell_values
        if isinstance(item, dict)
    }
    rows = list(table_element.iter(qn("w:tr")))
    for row_index, row in enumerate(rows, start=1):
        cells = list(row.findall(qn("w:tc")))
        for cell_index, cell in enumerate(cells, start=1):
            item = values.get((table_id, str(row_index), str(cell_index)))
            paragraphs = list(cell.iter(qn("w:p")))
            for paragraph in paragraphs:
                _replace_paragraph_xml_text(paragraph, replacements)
            if not item or str(item.get("status") or "filled") in {"locked", "missing"}:
                continue
            value = str(item.get("value") or "").strip()
            if not value:
                continue
            label = str(item.get("label") or "").strip()
            if label:
                for paragraph in paragraphs:
                    _replace_paragraph_xml_text(paragraph, {label: value})
            elif paragraphs:
                text_nodes = list(paragraphs[0].iter(qn("w:t")))
                if text_nodes:
                    text_nodes[0].text = value
                    for node in text_nodes[1:]:
                        node.text = ""


def _xml_cell_text(cell: Any) -> str:
    return "".join(str(node.text or "") for node in cell.iter(qn("w:t"))).strip()


def _set_xml_cell_text(cell: Any, value: str) -> None:
    text = str(value or "")
    text_nodes = list(cell.iter(qn("w:t")))
    if not text_nodes:
        paragraph = cell.find(qn("w:p"))
        if paragraph is None:
            paragraph = OxmlElement("w:p")
            cell.append(paragraph)
        run = OxmlElement("w:r")
        text_node = OxmlElement("w:t")
        run.append(text_node)
        paragraph.append(run)
        text_nodes = [text_node]
    text_nodes[0].text = text
    if text[:1].isspace() or text[-1:].isspace():
        text_nodes[0].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    for node in text_nodes[1:]:
        node.text = ""


def _response_table_row_values(entry: dict[str, Any]) -> dict[int, str]:
    return {
        int(item.get("cellIndex") or 0): str(item.get("value") or "")
        for item in (entry.get("cells") or [])
        if isinstance(item, dict) and int(item.get("cellIndex") or 0) > 0
    }


def _apply_response_table_rows(
    table_element: Any,
    *,
    table_id: str,
    section: dict[str, Any],
) -> bool:
    spec = section.get("templateResponseTable")
    if (
        not isinstance(spec, dict)
        or not spec.get("mapped")
        or str(spec.get("tableId") or "") != table_id
    ):
        return False

    rows = list(table_element.findall(qn("w:tr")))
    if not rows:
        return False
    columns = spec.get("columns") if isinstance(spec.get("columns"), dict) else {}
    requirement_column = int(columns.get("requirement") or 0)
    response_column = int(columns.get("response") or 0)
    if not requirement_column or not response_column:
        return False

    used_row_indexes: set[int] = set()
    applied = 0

    def fill_row(row_index: int, entry: dict[str, Any], *, clear: bool = False) -> bool:
        nonlocal applied
        if row_index < 1 or row_index > len(rows):
            return False
        row = rows[row_index - 1]
        cells = list(row.findall(qn("w:tc")))
        if clear:
            for cell in cells:
                _set_xml_cell_text(cell, "")
        for cell_index, value in _response_table_row_values(entry).items():
            if cell_index <= len(cells):
                _set_xml_cell_text(cells[cell_index - 1], value)
        used_row_indexes.add(row_index)
        applied += 1
        return True

    for entry in (spec.get("rows") or []):
        if not isinstance(entry, dict):
            continue
        row_index = int(entry.get("rowIndex") or 0)
        if row_index:
            fill_row(row_index, entry)

    def blank_slot(row: Any) -> bool:
        cells = list(row.findall(qn("w:tc")))
        if max(requirement_column, response_column) > len(cells):
            return False

        def blank(value: str) -> bool:
            compact = re.sub(r"\s+", "", value).strip()
            return (
                not compact
                or any(term in compact for term in ("待填写", "投标人填写", "供应商填写", "待补充"))
                or bool(re.fullmatch(r"[_＿—\-□☐（）()：:]*", value.strip()))
            )

        return blank(_xml_cell_text(cells[requirement_column - 1])) and blank(
            _xml_cell_text(cells[response_column - 1])
        )

    pending = [
        entry
        for entry in (spec.get("appendRows") or [])
        if isinstance(entry, dict)
    ]
    data_start = max(int(spec.get("dataStartRowIndex") or 1), 1)
    for row_index in range(data_start, len(rows) + 1):
        if not pending:
            break
        if row_index in used_row_indexes or not blank_slot(rows[row_index - 1]):
            continue
        entry = pending.pop(0)
        fill_row(row_index, entry, clear=True)

    prototype_row_index = int(spec.get("prototypeRowIndex") or data_start)
    insert_after = max(
        int(spec.get("insertAfterRowIndex") or spec.get("headerRowCount") or 1),
        int(spec.get("headerRowCount") or 1),
    )
    for entry in pending:
        rows = list(table_element.findall(qn("w:tr")))
        if not rows:
            break
        prototype_index = min(max(prototype_row_index, 1), len(rows))
        prototype = rows[prototype_index - 1]
        clone = deepcopy(prototype)
        clear = prototype_index <= int(spec.get("headerRowCount") or 1)
        if clear:
            for cell in clone.findall(qn("w:tc")):
                _set_xml_cell_text(cell, "")
        if 1 <= insert_after <= len(rows):
            rows[insert_after - 1].addnext(clone)
        else:
            table_element.append(clone)
        new_row_index = insert_after + 1
        rows = list(table_element.findall(qn("w:tr")))
        fill_row(new_row_index, entry)
        insert_after = new_row_index

    return applied > 0


def _render_response_table_fallback(document: Document, section: dict[str, Any]) -> None:
    response_rows = section.get("requirementResponseRows")
    if not isinstance(response_rows, list) or not response_rows:
        return
    headers = ("序号", "招标文件要求", "投标应答", "偏离情况", "备注")
    table = document.add_table(rows=1, cols=len(headers))
    _apply_table_grid(table)
    for index, header in enumerate(headers):
        table.rows[0].cells[index].text = header
    for row in response_rows:
        if not isinstance(row, dict):
            continue
        cells = table.add_row().cells
        values = (
            row.get("sequence"),
            row.get("requirement"),
            row.get("response"),
            row.get("deviation"),
            row.get("remark"),
        )
        for index, value in enumerate(values):
            cells[index].text = str(value or "")


def _copy_template_fragment(
    document: Document,
    fragment: dict[str, Any],
    section: dict[str, Any],
    *,
    root: Path | None,
) -> bool:
    section["templateResponseTableApplied"] = False
    section["templateResponseTableFallback"] = False
    source_path = _resolve_template_source(fragment, root)
    if source_path is None or str(fragment.get("copyMode") or "") != "ooxml_body_range":
        return False
    start = fragment.get("bodyChildStart")
    end = fragment.get("bodyChildEnd")
    if not isinstance(start, int) or not isinstance(end, int) or end < start:
        return False
    try:
        source_document = Document(str(source_path))
        source_body = source_document._element.body
        target_body = document._element.body
        replacements = _template_replacements(section, fragment)
        cell_values = section.get("templateCellValues")
        if not isinstance(cell_values, list):
            cell_values = []
        source_tables = [
            item
            for item in (fragment.get("tables") or [])
            if isinstance(item, dict)
        ]
        source_table_ids = [
            str(item.get("tableId") or item.get("id") or "")
            for item in source_tables
        ]
        source_blocks_by_index = {
            int(block["bodyChildIndex"]): block
            for block in (fragment.get("blocks") or [])
            if isinstance(block, dict)
            and isinstance(block.get("bodyChildIndex"), int)
        }
        edited_blocks_by_index = {
            int(block["bodyChildIndex"]): block
            for block in (section.get("templateBlocks") or [])
            if isinstance(block, dict)
            and isinstance(block.get("bodyChildIndex"), int)
        }
        source_tables_by_id = {
            str(table.get("tableId") or table.get("id") or ""): table
            for table in source_tables
        }
        edited_tables_by_id = {
            str(table.get("tableId") or table.get("id") or ""): table
            for table in (section.get("templateTables") or [])
            if isinstance(table, dict)
        }
        source_table_index = 0
        for body_index, source_child in enumerate(list(source_body), start=0):
            if body_index < start or body_index > end:
                continue
            if source_child.tag == qn("w:sectPr"):
                continue
            clone = deepcopy(source_child)
            _remap_template_relationships(clone, source_document, document)
            edited_block = edited_blocks_by_index.get(body_index)
            source_block = source_blocks_by_index.get(body_index)
            if (
                source_child.tag == qn("w:p")
                and edited_block is not None
                and source_block is not None
                and str(edited_block.get("text") or "")
                != str(source_block.get("text") or "")
            ):
                _set_paragraph_xml_text(clone, str(edited_block.get("text") or ""))
            else:
                for paragraph in clone.iter(qn("w:p")):
                    _replace_paragraph_xml_text(paragraph, replacements)
            for table in clone.iter(qn("w:tbl")):
                table_id = (
                    source_table_ids[source_table_index]
                    if source_table_index < len(source_table_ids)
                    else ""
                )
                _replace_template_table_cells(
                    table,
                    table_id=table_id,
                    cell_values=cell_values,
                    replacements=replacements,
                )
                if _apply_response_table_rows(
                    table,
                    table_id=table_id,
                    section=section,
                ):
                    section["templateResponseTableApplied"] = True
                edited_table = edited_tables_by_id.get(table_id)
                source_table = source_tables_by_id.get(table_id)
                if edited_table and source_table:
                    source_cells = {
                        (
                            int(row.get("rowIndex") or 0),
                            int(cell.get("cellIndex") or 0),
                        ): str(cell.get("text") or "")
                        for row in (source_table.get("rows") or [])
                        if isinstance(row, dict)
                        for cell in (row.get("cells") or [])
                        if isinstance(cell, dict)
                    }
                    has_edits = any(
                        str(
                            cell.get("filledText")
                            if cell.get("filledText") is not None
                            else cell.get("text") or ""
                        )
                        != source_cells.get(
                            (int(row.get("rowIndex") or 0), int(cell.get("cellIndex") or 0)),
                            "",
                        )
                        for row in (edited_table.get("rows") or [])
                        if isinstance(row, dict)
                        for cell in (row.get("cells") or [])
                        if isinstance(cell, dict)
                    )
                    if has_edits:
                        _apply_template_table_preview(table, edited_table)
                source_table_index += 1
            insert_index = len(target_body)
            if target_body.sectPr is not None:
                insert_index -= 1
            target_body.insert(insert_index, clone)
        return True
    except Exception:
        return False


def _render_template_section(
    document: Document,
    section: dict[str, Any],
    fragment: dict[str, Any],
    *,
    root: Path | None,
    assets: dict[str, Any] | None,
) -> bool:
    if (
        section.get("contentMatchesTemplate") is False
        and str(section.get("content") or "").strip()
    ):
        _render_section(document, section, assets)
        section["templateEditFallback"] = True
        return False
    if _copy_template_fragment(document, fragment, section, root=root):
        if (
            section.get("requirementResponseRows")
            and not section.get("templateResponseTableApplied")
        ):
            _render_response_table_fallback(document, section)
            section["templateResponseTableFallback"] = True
        return True
    _render_section(document, section, assets)
    return False


def _render_section(document: Document, section: dict[str, Any], assets: dict[str, Any] | None = None) -> None:
    """渲染单个章节：标题 + Markdown 正文 + 证据插图/占位。"""
    title = str(section.get("title") or "").strip() or "（未命名章节）"
    content = section.get("content") or ""

    document.add_heading(title, level=min(max(int(section.get("level") or 1), 1), 9))

    if not str(content).strip():
        if section.get("responseType") != "section_group":
            document.add_paragraph("（本章待生成）")
    else:
        markdown_to_docx(document, str(content))

    _add_evidence_assets(document, section, assets)


def _cell_text(value: Any) -> str:
    """把单元格取值安全转成字符串。"""
    return str(value).strip() if value else ""


def _evidence_cell(row: dict[str, Any]) -> str:
    """证据引用列：优先 evidencePath（占位），其次 evidence 文本。"""
    path = row.get("evidencePath") or ""
    if path:
        return f"【附件：证据路径 {str(path).strip()}】"
    evidence = row.get("evidence") or ""
    if evidence:
        return str(evidence).strip()
    return "—"


def _render_deviation_table(document: Document, title: str, rows: list[Any], *, level: int = 1) -> None:
    """把一组偏离行渲染成一张 Word 表格（序号/招标要求/偏离情况/响应说明/证据引用）。"""
    document.add_heading(title, level=min(max(level, 1), 9))

    table = document.add_table(rows=1, cols=len(DEVIATION_COLUMNS))
    _apply_table_grid(table)
    for j, col_name in enumerate(DEVIATION_COLUMNS):
        cell = table.rows[0].cells[j]
        cell.text = col_name
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True

    if not rows:
        cells = table.add_row().cells
        cells[1].text = "（无偏离项）"
        return

    for idx, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            continue
        cells = table.add_row().cells
        cells[0].text = str(idx)
        cells[1].text = _cell_text(row.get("requirement") or row.get("requirementId"))
        cells[2].text = _cell_text(row.get("deviation"))
        cells[3].text = _cell_text(row.get("response") or row.get("suggestion"))
        cells[4].text = _evidence_cell(row)


def _deviation_key_for_section(section: dict[str, Any]) -> str:
    """章节属于偏离表时返回其分组键（technical/commercial），否则返回空串。"""
    if str(section.get("kind") or "") != SECTION_KIND_DEVIATION:
        return ""
    title = str(section.get("title") or "")
    if "商务" in title:
        return DEVIATION_TABLE_COMMERCIAL
    return DEVIATION_TABLE_TECHNICAL


def _render_toc(document: Document, plan: list[Any], *, include_unlisted_deviations: bool = True) -> None:
    """简单列出章节标题作为目录占位（真实目录需在 Word 中刷新域）。"""
    listed: set[str] = set()
    for section in plan:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        if title:
            paragraph = document.add_paragraph(title, style="List Bullet")
            paragraph.paragraph_format.left_indent = Cm(
                min(max(int(section.get("level") or 1) - 1, 0), 8) * 0.45
            )
            listed.add(title)
    if not include_unlisted_deviations:
        return
    for key in (DEVIATION_TABLE_TECHNICAL, DEVIATION_TABLE_COMMERCIAL):
        name = DEVIATION_TABLE_TITLES.get(key, key)
        if name not in listed:
            document.add_paragraph(name, style="List Bullet")


# ---------------------------------------------------------------------------
# 组装入口
# ---------------------------------------------------------------------------
def assemble_docx(
    bid_document: dict[str, Any],
    output_dir: str | Path,
    template_path: str | None = None,
    assets: dict[str, Any] | None = None,
    *,
    root: str | Path | None = None,
    template_fragments: list[dict[str, Any]] | dict[str, dict[str, Any]] | None = None,
    include_unlisted_deviations: bool = True,
) -> dict[str, Any]:
    """把 bid_document 组装成一个 .docx 文件并落盘。

    Args:
        bid_document: 标书文档 dict（plan + deviationTables + mode）。
        output_dir: 输出目录（Path 或 str，不存在则创建）。
        template_path: 可选 .docx 模板路径；未提供或文件不存在则用默认模板。
        assets: 可选 {evidencePath: asset-dict}，用于把证据图文插入章节（暗标传 None）。
        root: 项目根目录，用于解析招标文件原始 DOCX 的相对路径。
        template_fragments: ParseBundle 中的完整模板片段集合。

    Returns:
        ``{"fileName", "path", "mode"}``，mode 取 ``bid_document["mode"]``。
    """
    mode = str(bid_document.get("mode") or BID_MODE_OPEN)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    root_path = Path(root) if root is not None else None

    if template_path and Path(template_path).exists():
        document = Document(str(template_path))
    else:
        document = Document()

    plan = bid_document.get("plan") or []
    deviation_tables = bid_document.get("deviationTables") or {}
    if isinstance(template_fragments, dict):
        fragment_map = {
            str(key): value
            for key, value in template_fragments.items()
            if isinstance(value, dict)
        }
    else:
        fragment_map = {
            str(item.get("templateId") or ""): item
            for item in (template_fragments or [])
            if isinstance(item, dict) and str(item.get("templateId") or "")
        }
    export_warnings: list[str] = []
    copied_template_count = 0

    # ① 封面/标题页
    title = _extract_title(bid_document)
    document.add_heading(title, level=0)
    if title != FALLBACK_TITLE:
        document.add_paragraph(FALLBACK_TITLE)
    document.add_paragraph(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")

    # ② 目录
    document.add_page_break()
    document.add_heading("目录", level=1)
    _render_toc(document, plan, include_unlisted_deviations=include_unlisted_deviations)
    document.add_paragraph("（目录需在 Word 中刷新域）")

    # ③ 按 plan 顺序渲染章节；偏离表章节在 plan 内原位渲染成表格
    document.add_page_break()
    rendered_deviation_keys: set[str] = set()
    for section in plan:
        if not isinstance(section, dict):
            continue
        key = _deviation_key_for_section(section)
        if key:
            rows = deviation_tables.get(key) or []
            _render_deviation_table(
                document, DEVIATION_TABLE_TITLES.get(key, key), rows,
                level=int(section.get("level") or 1),
            )
            rendered_deviation_keys.add(key)
        else:
            fragment_id = str(
                section.get("templateFragmentId")
                or section.get("templateId")
                or ""
            ).strip()
            fragment = fragment_map.get(fragment_id)
            fragment_type = str(
                fragment.get("fragmentType") or fragment.get("templateType") or ""
            ) if isinstance(fragment, dict) else ""
            if fragment_type in {"fixed_template", "fixed_form", "fixed_table", "table"}:
                copied = _render_template_section(
                    document,
                    section,
                    fragment,
                    root=root_path,
                    assets=assets,
                )
                if copied:
                    copied_template_count += 1
                    if section.get("templateResponseTableFallback"):
                        export_warnings.append(
                            f"章节“{section.get('title') or section.get('sectionId') or '未命名'}”"
                            "的原始表格列无法可靠识别，已在模板后追加标准响应表。"
                        )
                else:
                    if section.get("templateEditFallback"):
                        export_warnings.append(
                            f"章节“{section.get('title') or section.get('sectionId') or '未命名'}”"
                            "已按正文编辑内容导出，未强行合并原始 Word 模板格式。"
                        )
                        continue
                    export_warnings.append(
                        f"章节“{section.get('title') or section.get('sectionId') or '未命名'}”"
                        "未能按原始 DOCX 片段复制，已回退为文本渲染。"
                    )
            else:
                _render_section(document, section, assets)

    # ④ 偏离表兜底：plan 未覆盖的表仍要输出
    if include_unlisted_deviations:
        for key in (DEVIATION_TABLE_TECHNICAL, DEVIATION_TABLE_COMMERCIAL):
            if key in rendered_deviation_keys:
                continue
            rows = deviation_tables.get(key) or []
            _render_deviation_table(document, DEVIATION_TABLE_TITLES.get(key, key), rows)

    filename = _build_filename(bid_document)
    path = output_dir / filename
    document.save(str(path))

    return {
        "fileName": filename,
        "path": str(path),
        "mode": mode,
        "copiedTemplateCount": copied_template_count,
        "exportWarnings": export_warnings,
    }
