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

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document

from .schemas import (
    BID_MODE_OPEN,
    DEVIATION_TABLE_COMMERCIAL,
    DEVIATION_TABLE_TECHNICAL,
    DEVIATION_TABLE_TITLES,
    SECTION_KIND_DEVIATION,
)

FALLBACK_TITLE = "投标响应文件"
DEVIATION_COLUMNS: tuple[str, ...] = ("序号", "招标要求", "偏离情况", "响应说明", "证据引用")

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
            level = min(len(heading.group(1)), 4)
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
def _append_evidence_placeholder(document: Document, path: str, seen: set[str]) -> None:
    """追加一条证据占位（去重），不真正插图。"""
    if not path or path in seen:
        return
    seen.add(path)
    document.add_paragraph(f"【附件：证据路径 {path}】")


def _add_evidence_placeholders(document: Document, section: dict[str, Any]) -> None:
    """为章节溯源引用里带 evidencePath 的项输出占位段落。"""
    seen: set[str] = set()

    direct = section.get("evidencePath")
    if direct:
        _append_evidence_placeholder(document, str(direct), seen)

    refs = section.get("sourceRefs") or []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        path = ref.get("evidencePath") or ""
        if path:
            _append_evidence_placeholder(document, str(path), seen)


def _render_section(document: Document, section: dict[str, Any]) -> None:
    """渲染单个章节：标题 + Markdown 正文 + 证据占位。"""
    title = str(section.get("title") or "").strip() or "（未命名章节）"
    content = section.get("content") or ""

    document.add_heading(title, level=1)

    if not str(content).strip():
        document.add_paragraph("（本章待生成）")
    else:
        markdown_to_docx(document, str(content))

    _add_evidence_placeholders(document, section)


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


def _render_deviation_table(document: Document, title: str, rows: list[Any]) -> None:
    """把一组偏离行渲染成一张 Word 表格（序号/招标要求/偏离情况/响应说明/证据引用）。"""
    document.add_heading(title, level=1)

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


def _render_toc(document: Document, plan: list[Any]) -> None:
    """简单列出章节标题作为目录占位（真实目录需在 Word 中刷新域）。"""
    listed: set[str] = set()
    for section in plan:
        if not isinstance(section, dict):
            continue
        title = str(section.get("title") or "").strip()
        if title:
            document.add_paragraph(title, style="List Bullet")
            listed.add(title)
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
) -> dict[str, Any]:
    """把 bid_document 组装成一个 .docx 文件并落盘。

    Args:
        bid_document: 标书文档 dict（plan + deviationTables + mode）。
        output_dir: 输出目录（Path 或 str，不存在则创建）。
        template_path: 可选 .docx 模板路径；未提供或文件不存在则用默认模板。

    Returns:
        ``{"fileName", "path", "mode"}``，mode 取 ``bid_document["mode"]``。
    """
    mode = str(bid_document.get("mode") or BID_MODE_OPEN)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if template_path and Path(template_path).exists():
        document = Document(str(template_path))
    else:
        document = Document()

    plan = bid_document.get("plan") or []
    deviation_tables = bid_document.get("deviationTables") or {}

    # ① 封面/标题页
    title = _extract_title(bid_document)
    document.add_heading(title, level=0)
    if title != FALLBACK_TITLE:
        document.add_paragraph(FALLBACK_TITLE)
    document.add_paragraph(f"生成时间：{datetime.now():%Y-%m-%d %H:%M:%S}")

    # ② 目录
    document.add_page_break()
    document.add_heading("目录", level=1)
    _render_toc(document, plan)
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
            _render_deviation_table(document, DEVIATION_TABLE_TITLES.get(key, key), rows)
            rendered_deviation_keys.add(key)
        else:
            _render_section(document, section)

    # ④ 偏离表兜底：plan 未覆盖的表仍要输出
    for key in (DEVIATION_TABLE_TECHNICAL, DEVIATION_TABLE_COMMERCIAL):
        if key in rendered_deviation_keys:
            continue
        rows = deviation_tables.get(key) or []
        _render_deviation_table(document, DEVIATION_TABLE_TITLES.get(key, key), rows)

    filename = _build_filename(bid_document)
    path = output_dir / filename
    document.save(str(path))

    return {"fileName": filename, "path": str(path), "mode": mode}
