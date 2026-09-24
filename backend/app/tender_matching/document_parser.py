from __future__ import annotations

import re
import struct
import zipfile
from io import BytesIO
from typing import TYPE_CHECKING, Any
from xml.etree import ElementTree as ET

if TYPE_CHECKING:
    from .document_models import DocumentIR


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
W = f"{{{W_NS}}}"
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _w_attr(element: ET.Element | None, name: str) -> str:
    if element is None:
        return ""
    return str(element.attrib.get(f"{W}{name}", ""))


def _clean_text(value: Any, limit: int = 12000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


# ── .doc field-code / TOC noise strip ──────────────────────────────
# legacy-doc (antiword) leaks Word field instruction codes as
# body text, especially in the table-of-contents area.  Strip them
# before any segment or field extraction sees them.
_FIELD_CODE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"PAGEREF\s+_Toc\d+\s+\\h(?:\s*\d+)?", re.I),
    re.compile(r"HYPERLINK\s+\\l\s+[\"']?_Toc\d+[\"']?", re.I),
    re.compile(r"_Toc\d+", re.I),
)


def _strip_doc_field_codes(text: str) -> str:
    for pattern in _FIELD_CODE_PATTERNS:
        text = pattern.sub(" ", text)
    return text


def _element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    parts: list[str] = []
    for node in element.iter():
        local_name = node.tag.rsplit("}", 1)[-1]
        if local_name == "t" and node.text:
            parts.append(node.text)
        elif local_name == "tab":
            parts.append("\t")
        elif local_name in {"br", "cr"}:
            parts.append("\n")
    return _clean_text("".join(parts))


def _paragraph_style(paragraph: ET.Element) -> dict[str, Any]:
    ppr = paragraph.find(f"{W}pPr")
    style_node = ppr.find(f"{W}pStyle") if ppr is not None else None
    outline_node = ppr.find(f"{W}outlineLvl") if ppr is not None else None
    spacing_node = ppr.find(f"{W}spacing") if ppr is not None else None
    indent_node = ppr.find(f"{W}ind") if ppr is not None else None
    return {
        "styleId": _w_attr(style_node, "val"),
        "outlineLevel": _w_attr(outline_node, "val"),
        "alignment": _w_attr(ppr.find(f"{W}jc") if ppr is not None else None, "val"),
        "lineSpacing": _w_attr(spacing_node, "line"),
        "leftIndent": _w_attr(indent_node, "left"),
        "firstLineIndent": _w_attr(indent_node, "firstLine"),
    }


def _paragraph_run_style(paragraph: ET.Element) -> dict[str, Any]:
    fonts: list[str] = []
    sizes: list[float] = []
    bold = False
    italic = False
    for run in paragraph.findall(f".//{W}r"):
        rpr = run.find(f"{W}rPr")
        if rpr is None:
            continue
        font_node = rpr.find(f"{W}rFonts")
        for key in ("eastAsia", "ascii", "hAnsi"):
            value = _w_attr(font_node, key)
            if value:
                fonts.append(value)
        size_node = rpr.find(f"{W}sz")
        size = _w_attr(size_node, "val")
        if size:
            try:
                sizes.append(round(int(size) / 2, 1))
            except ValueError:
                pass
        bold = bold or rpr.find(f"{W}b") is not None
        italic = italic or rpr.find(f"{W}i") is not None
    return {
        "fontNames": list(dict.fromkeys(fonts)),
        "fontSizes": list(dict.fromkeys(sizes)),
        "bold": bold,
        "italic": italic,
    }


def _paragraph_record(
    paragraph: ET.Element,
    *,
    segment_id: str,
    order: int,
    paragraph_index: int,
    source_locator: str,
    table_id: str = "",
    row_index: int | None = None,
    cell_index: int | None = None,
) -> dict[str, Any] | None:
    text = _element_text(paragraph)
    if not text:
        return None
    return {
        "id": segment_id,
        "sourceLocator": source_locator,
        "text": text,
        "contentType": "table_cell_paragraph" if table_id else "paragraph",
        "order": order,
        "paragraphIndex": paragraph_index,
        "tableId": table_id,
        "rowIndex": row_index,
        "cellIndex": cell_index,
        "style": {
            **_paragraph_style(paragraph),
            **_paragraph_run_style(paragraph),
        },
    }


def _cell_record(
    cell: ET.Element,
    *,
    table_id: str,
    row_index: int,
    cell_index: int,
    order: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    paragraphs = [
        paragraph
        for paragraph in cell.findall(f".//{W}p")
        if _element_text(paragraph)
    ]
    paragraph_records: list[dict[str, Any]] = []
    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        record = _paragraph_record(
            paragraph,
            segment_id=f"{table_id}-R{row_index:03d}-C{cell_index:03d}-P{paragraph_index:03d}",
            order=order,
            paragraph_index=paragraph_index,
            source_locator=f"{table_id}第{row_index}行第{cell_index}列",
            table_id=table_id,
            row_index=row_index,
            cell_index=cell_index,
        )
        if record:
            paragraph_records.append(record)
    text = _clean_text("\n".join(item["text"] for item in paragraph_records))
    return (
        {
            "id": f"{table_id}-R{row_index:03d}-C{cell_index:03d}",
            "sourceLocator": f"{table_id}第{row_index}行第{cell_index}列",
            "text": text,
            "contentType": "table_cell",
            "order": order,
            "tableId": table_id,
            "rowIndex": row_index,
            "cellIndex": cell_index,
            "paragraphs": paragraph_records,
            "gridSpan": _w_attr(cell.find(f"{W}tcPr/{W}gridSpan"), "val") or "1",
            "vMerge": _w_attr(cell.find(f"{W}tcPr/{W}vMerge"), "val"),
        },
        paragraph_records,
    )


def _table_record(
    table: ET.Element,
    *,
    table_id: str,
    order: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    cell_units: list[dict[str, Any]] = []
    for row_index, row in enumerate(table.findall(f"{W}tr"), start=1):
        cells: list[dict[str, Any]] = []
        for cell_index, cell in enumerate(row.findall(f"{W}tc"), start=1):
            cell_record, paragraph_records = _cell_record(
                cell,
                table_id=table_id,
                row_index=row_index,
                cell_index=cell_index,
                order=order,
            )
            cells.append(cell_record)
            cell_units.extend([cell_record, *paragraph_records])
        if not any(cell.get("text") for cell in cells):
            continue
        rows.append(
            {
                "rowIndex": row_index,
                "sourceLocator": f"{table_id}第{row_index}行",
                "cells": cells,
                "text": " | ".join(cell["text"] for cell in cells),
            }
        )
    grid_widths = [
        _w_attr(grid_col, "w")
        for grid_col in table.findall(f"{W}tblGrid/{W}gridCol")
        if _w_attr(grid_col, "w")
    ]
    return (
        {
            "id": table_id,
            "tableId": table_id,
            "contentType": "table",
            "order": order,
            "rows": rows,
            "rowCount": len(rows),
            "columnCount": max((len(row["cells"]) for row in rows), default=0),
            "gridWidths": grid_widths,
        },
        cell_units,
    )


def _header_footer_texts(archive: zipfile.ZipFile) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {"headers": [], "footers": []}
    for name in archive.namelist():
        if not (name.startswith("word/header") or name.startswith("word/footer")):
            continue
        try:
            root = ET.fromstring(archive.read(name))
        except ET.ParseError:
            continue
        values = [
            _element_text(paragraph)
            for paragraph in root.findall(f".//{W}p")
            if _element_text(paragraph)
        ]
        if not values:
            continue
        key = "headers" if "/header" in name else "footers"
        result[key].extend(values)
    result["headers"] = list(dict.fromkeys(result["headers"]))
    result["footers"] = list(dict.fromkeys(result["footers"]))
    return result


def _docx_layout(root: ET.Element) -> dict[str, Any]:
    sect = root.find(f".//{W}body/{W}sectPr")
    page_size = sect.find(f"{W}pgSz") if sect is not None else None
    page_margin = sect.find(f"{W}pgMar") if sect is not None else None
    return {
        "pageWidthTwips": _w_attr(page_size, "w"),
        "pageHeightTwips": _w_attr(page_size, "h"),
        "marginTopTwips": _w_attr(page_margin, "top"),
        "marginRightTwips": _w_attr(page_margin, "right"),
        "marginBottomTwips": _w_attr(page_margin, "bottom"),
        "marginLeftTwips": _w_attr(page_margin, "left"),
    }


def parse_docx(content: bytes) -> dict[str, Any]:
    try:
        archive = zipfile.ZipFile(BytesIO(content))
        xml = archive.read("word/document.xml")
    except (KeyError, zipfile.BadZipFile):
        return {
            "text": "",
            "segments": [],
            "units": [],
            "tables": [],
            "metadata": {},
            "parseMethod": "docx_xml",
            "warnings": ["无法读取 DOCX 正文。"],
        }
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        archive.close()
        return {
            "text": "",
            "segments": [],
            "units": [],
            "tables": [],
            "metadata": {},
            "parseMethod": "docx_xml",
            "warnings": ["DOCX 正文 XML 解析失败。"],
        }

    body = root.find(f"{W}body")
    segments: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    paragraph_index = 0
    xml_paragraph_index = 0
    table_index = 0
    order = 0
    if body is not None:
        for child in body:
            local_name = child.tag.rsplit("}", 1)[-1]
            if local_name == "p":
                xml_paragraph_index += 1
                record = _paragraph_record(
                    child,
                    segment_id=f"SEG-{len(segments) + 1:06d}",
                    order=order,
                    paragraph_index=paragraph_index + 1,
                    source_locator=f"第{paragraph_index + 1}段",
                )
                order += 1
                if record:
                    paragraph_index += 1
                    segments.append(record)
                    units.append(record)
            elif local_name == "tbl":
                table_index += 1
                table_id = f"TABLE-{table_index:03d}"
                table, table_units = _table_record(child, table_id=table_id, order=order)
                tables.append(table)
                for row in table["rows"]:
                    row_record = {
                        "id": f"SEG-{len(segments) + 1:06d}",
                        "sourceLocator": row["sourceLocator"],
                        "text": row["text"],
                        "contentType": "table_row",
                        "order": order,
                        "tableId": table_id,
                        "rowIndex": row["rowIndex"],
                        "cells": row["cells"],
                        "style": {},
                    }
                    segments.append(row_record)
                    units.append(row_record)
                    units.extend(
                        item for item in table_units
                        if item.get("rowIndex") == row["rowIndex"]
                    )
                    order += 1

    warnings: list[str] = []
    if not tables:
        warnings.append("未发现 DOCX 表格。")
    headers_footers = _header_footer_texts(archive)
    archive.close()
    return {
        "text": "\n".join(segment["text"] for segment in segments),
        "segments": segments,
        "units": units,
        "tables": tables,
        "metadata": {
            "layout": _docx_layout(root),
            "headers": headers_footers["headers"],
            "footers": headers_footers["footers"],
            "paragraphCount": paragraph_index,
            "xmlParagraphCount": xml_paragraph_index,
            "tableCount": len(tables),
        },
        "parseMethod": "docx_xml_structured",
        "warnings": warnings,
    }


def _legacy_doc_empty_result(warnings: list[str]) -> dict[str, Any]:
    return {
        "text": "",
        "segments": [],
        "units": [],
        "tables": [],
        "metadata": {},
        "parseMethod": "legacy_doc_failed",
        "warnings": warnings,
    }


def _pad_legacy_doc_to_sector_boundary(content: bytes) -> tuple[bytes, bool]:
    """Pad a truncated OLE tail without changing the document payload.

    Some government procurement sites append a short tail to an old .doc file
    or omit the final padding bytes of the last OLE sector.  The document can
    still be read safely when that missing sector padding is restored with
    zeroes.  We only do this for a valid OLE header and never for arbitrary
    binary/text uploads.
    """
    if len(content) < 32 or content[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return content, False
    try:
        sector_shift = struct.unpack_from("<H", content, 0x1E)[0]
    except struct.error:
        return content, False
    if sector_shift not in {9, 12}:
        return content, False
    sector_size = 1 << sector_shift
    remainder = len(content) % sector_size
    if remainder == 0:
        return content, False
    return content + (b"\x00" * (sector_size - remainder)), True


def _bbox_overlaps(left: Any, right: Any) -> bool:
    if not isinstance(left, (list, tuple)) or len(left) != 4:
        return False
    if not isinstance(right, (list, tuple)) or len(right) != 4:
        return False
    try:
        lx0, ly0, lx1, ly1 = (float(value) for value in left)
        rx0, ry0, rx1, ry1 = (float(value) for value in right)
    except (TypeError, ValueError):
        return False
    return lx0 < rx1 and lx1 > rx0 and ly0 < ry1 and ly1 > ry0


def _normalise_pdf_lines(value: str) -> list[str]:
    """Keep useful line boundaries for the typed fallback parser.

    The compatibility ``parse_pdf`` API intentionally stays page-oriented.
    DocumentIR needs smaller units so heading detection and section routing can
    still work when PyMuPDF is unavailable.
    """

    lines: list[str] = []
    for raw_line in str(value or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = re.sub(r"[ \t]+", " ", raw_line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)

    # Some PDFs expose the same visual line twice, then expose narrow glyph
    # fragments one by one.  Remove only adjacent duplicates and join short
    # fragments conservatively; paragraph-sized lines remain untouched.
    compact: list[str] = []
    for line in lines:
        if len(line) <= 2 and compact:
            previous = compact[-1]
            if (
                len(previous) <= 4
                or previous[-1:] in "-—"
                or (previous[-1:].isdigit() and line in "年月日时分秒页")
            ):
                compact[-1] = previous + line
                continue
        compact.append(line)
    return compact


def _parse_pdf_with_pypdf_structured(content: bytes) -> dict[str, Any]:
    """Build a useful page/line IR when PyMuPDF is not installed."""

    try:
        from pypdf import PdfReader
    except Exception:
        return {
            "text": "",
            "segments": [],
            "units": [],
            "tables": [],
            "pages": [],
            "metadata": {"coordinatesAvailable": False},
            "parseMethod": "pypdf_unavailable",
            "warnings": ["未安装 PDF 文本解析依赖。"],
        }

    try:
        reader = PdfReader(BytesIO(content))
    except Exception as exc:
        return {
            "text": "",
            "segments": [],
            "units": [],
            "tables": [],
            "pages": [],
            "metadata": {"coordinatesAvailable": False},
            "parseMethod": "pypdf_failed",
            "warnings": [f"PDF 文件无法读取：{type(exc).__name__}。"],
        }

    segments: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    warnings: list[str] = [
        "PyMuPDF 不可用，PDF 已降级为页/行级解析；缺少 bbox 和 PDF 表格定位。"
    ]
    for page_number, page in enumerate(reader.pages, start=1):
        try:
            page_text = str(page.extract_text() or "")
        except Exception:
            page_text = ""
        lines = _normalise_pdf_lines(page_text)
        page_segment_ids: list[str] = []
        for line_number, line in enumerate(lines, start=1):
            segment_id = f"SEG-{len(segments) + 1:06d}"
            segments.append(
                {
                    "id": segment_id,
                    "sourceLocator": f"page {page_number}, line {line_number}",
                    "page": page_number,
                    "lineIndex": line_number,
                    "text": line,
                    "contentType": "pdf_text_line",
                    "order": len(segments),
                    "style": {
                        "lineCount": 1,
                        "coordinatesAvailable": False,
                    },
                }
            )
            page_segment_ids.append(segment_id)
        has_text = bool(lines)
        if not has_text:
            warnings.append(
                f"page {page_number} has no extractable text; OCR is required"
            )
        pages.append(
            {
                "page": page_number,
                "text": "\n".join(lines),
                "segmentIds": page_segment_ids,
                "sourceLocator": f"page {page_number}",
                "hasText": has_text,
                "isScan": not has_text,
                "ocrStatus": "required" if not has_text else "not_needed",
            }
        )

    return {
        "text": "\n".join(segment["text"] for segment in segments),
        "segments": segments,
        "units": segments.copy(),
        "tables": [],
        "pages": pages,
        "metadata": {
            "pageCount": len(pages),
            "extractedPageCount": sum(1 for page in pages if page["hasText"]),
            "lineCount": len(segments),
            "ocrPages": sum(1 for page in pages if page["isScan"]),
            "coordinatesAvailable": False,
            "tableExtractionAvailable": False,
        },
        "parseMethod": "pypdf_lines_structured",
        "warnings": warnings,
    }


def _extract_pymupdf_tables(
    page: Any,
    *,
    page_number: int,
    table_index_start: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Best-effort extraction of PDF tables without making it mandatory."""

    find_tables = getattr(page, "find_tables", None)
    if not callable(find_tables):
        return [], []
    try:
        found = find_tables()
        candidates = getattr(found, "tables", None)
        if candidates is None:
            candidates = found if isinstance(found, (list, tuple)) else []
    except Exception:
        return [], []

    tables: list[dict[str, Any]] = []
    row_segments: list[dict[str, Any]] = []
    for table_offset, table in enumerate(candidates, start=1):
        try:
            extracted = table.extract()
        except Exception:
            continue
        if not extracted:
            continue
        raw_bbox = getattr(table, "bbox", None)
        bbox = (
            [round(float(value), 3) for value in raw_bbox]
            if isinstance(raw_bbox, (list, tuple)) and len(raw_bbox) == 4
            else None
        )
        raw_cells = list(getattr(table, "cells", None) or [])
        try:
            row_count = int(getattr(table, "row_count", 0) or 0)
            column_count = int(getattr(table, "col_count", 0) or 0)
        except (TypeError, ValueError):
            row_count = 0
            column_count = 0

        def cell_bbox(row_index: int, cell_index: int) -> list[float] | None:
            if not raw_cells or row_count <= 0 or column_count <= 0:
                return bbox
            # PyMuPDF exposes table.cells in column-major order.
            offset = (cell_index - 1) * row_count + (row_index - 1)
            if offset >= len(raw_cells):
                return bbox
            value = raw_cells[offset]
            if not isinstance(value, (list, tuple)) or len(value) != 4:
                return bbox
            try:
                return [round(float(item), 3) for item in value]
            except (TypeError, ValueError):
                return bbox

        def row_bbox(row_index: int, cell_count: int) -> list[float] | None:
            boxes = [
                cell_bbox(row_index, cell_index)
                for cell_index in range(1, cell_count + 1)
            ]
            boxes = [item for item in boxes if item and len(item) == 4]
            if not boxes:
                return bbox
            return [
                min(item[0] for item in boxes),
                min(item[1] for item in boxes),
                max(item[2] for item in boxes),
                max(item[3] for item in boxes),
            ]

        table_id = f"TABLE-P{page_number:03d}-{table_index_start + table_offset - 1:03d}"
        rows: list[dict[str, Any]] = []
        max_columns = 0
        for row_index, raw_row in enumerate(extracted, start=1):
            if not isinstance(raw_row, (list, tuple)):
                continue
            cells: list[dict[str, Any]] = []
            for cell_index, raw_cell in enumerate(raw_row, start=1):
                cell_text = _clean_text(raw_cell)
                current_cell_bbox = cell_bbox(row_index, cell_index)
                cells.append(
                    {
                        "id": f"{table_id}-R{row_index:03d}-C{cell_index:03d}",
                        "sourceLocator": (
                            f"page {page_number}, {table_id}, "
                            f"row {row_index}, cell {cell_index}"
                        ),
                        "text": cell_text,
                        "contentType": "table_cell",
                        "order": 0,
                        "tableId": table_id,
                        "rowIndex": row_index,
                        "cellIndex": cell_index,
                        "page": page_number,
                        "bbox": current_cell_bbox,
                    }
                )
            if not any(cell["text"] for cell in cells):
                continue
            max_columns = max(max_columns, len(cells))
            row_text = " | ".join(cell["text"] for cell in cells)
            row_source = f"page {page_number}, {table_id}, row {row_index}"
            current_row_bbox = row_bbox(row_index, len(cells))
            rows.append(
                {
                    "rowIndex": row_index,
                    "sourceLocator": row_source,
                    "text": row_text,
                    "page": page_number,
                    "bbox": current_row_bbox,
                    "cells": cells,
                }
            )
            row_segments.append(
                {
                    "sourceLocator": row_source,
                    "page": page_number,
                    "bbox": current_row_bbox,
                    "text": row_text,
                    "contentType": "pdf_table_row",
                    "tableId": table_id,
                    "rowIndex": row_index,
                    "cells": cells,
                    "style": {
                        "coordinatesAvailable": bool(bbox),
                        "tableRow": True,
                    },
                    "_sortY": (
                        (bbox[1] if bbox else 0.0)
                        + (row_index * 0.001)
                    ),
                }
            )
        if rows:
            tables.append(
                {
                    "id": table_id,
                    "tableId": table_id,
                    "contentType": "table",
                    "order": table_index_start + table_offset - 1,
                    "page": page_number,
                    "bbox": bbox,
                    "rows": rows,
                    "rowCount": len(rows),
                    "columnCount": max_columns,
                    "gridWidths": [],
                }
            )
    return tables, row_segments


def parse_doc(content: bytes) -> dict[str, Any]:
    """Parse a binary Word 97-2003 document into the common text structure.

    A legacy .doc is an OLE compound binary file, not UTF-8 text.  Keeping a
    separate parser is important: falling through to ``parse_plain_text``
    produces apparently valid but unusable mojibake and pollutes matching.
    """
    # Optional high-fidelity path: convert .doc -> .docx so Word tables survive
    # (Word COM on Windows, LibreOffice headless otherwise).  Any failure falls
    # back to the text-only legacy_doc path below.
    try:
        from .doc_converter import convert_doc_to_docx

        docx_content, convert_method = convert_doc_to_docx(content)
        if docx_content:
            parsed_docx = parse_docx(docx_content)
            if parsed_docx.get("text"):
                parsed_docx["parseMethod"] = f"docx_via_{convert_method}"
                parsed_docx["metadata"] = {
                    **(parsed_docx.get("metadata") or {}),
                    "docConvertedFrom": "doc",
                    "docConvertMethod": convert_method,
                }
                parsed_docx["warnings"] = [
                    *(parsed_docx.get("warnings") or []),
                    f"已将 DOC 转换为 DOCX 解析（{convert_method}），表格结构已保留。",
                ]
                return parsed_docx
    except Exception:
        pass

    try:
        from legacy_doc import extract_text
    except Exception:
        return _legacy_doc_empty_result([
            "未安装旧版 DOC 解析依赖，请安装 legacy-doc 后重试。",
        ])

    padded_content, padded = _pad_legacy_doc_to_sector_boundary(content)
    # ``_pad_legacy_doc_to_sector_boundary`` transparently restores padding;
    # the recovery is recorded in metadata.paddedToSectorBoundary below and
    # must not count as a parser warning (the parse succeeds after the fix).
    warnings: list[str] = []

    try:
        extraction = extract_text(padded_content)
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        return _legacy_doc_empty_result([
            f"旧版 DOC 文件解析失败：{detail}。请用 Word 打开后另存为 DOCX，再重新上传。",
            *warnings,
        ])

    text = str(getattr(extraction, "text", "") or "").strip()
    if not text:
        return _legacy_doc_empty_result([
            "旧版 DOC 未提取到正文文字，请用 Word 打开后另存为 DOCX，再重新上传。",
            *warnings,
        ])

    segments: list[dict[str, Any]] = []
    for index, line in enumerate(text.splitlines(), start=1):
        value = _clean_text(_strip_doc_field_codes(line))
        if not value:
            continue
        record = {
            "id": f"SEG-{len(segments) + 1:06d}",
            "sourceLocator": f"第{index}段",
            "text": value,
            "contentType": "paragraph",
            "order": len(segments),
            "paragraphIndex": index,
            "style": {},
        }
        segments.append(record)

    extraction_warnings = getattr(extraction, "warnings", ()) or ()
    warnings.extend(str(item) for item in extraction_warnings if str(item).strip())
    metadata = dict(getattr(extraction, "metadata", {}) or {})
    metadata["paragraphCount"] = len(segments)
    metadata["paddedToSectorBoundary"] = padded
    return {
        "text": "\n".join(item["text"] for item in segments),
        "segments": segments,
        "units": segments.copy(),
        "tables": [],
        "metadata": metadata,
        "parseMethod": "legacy_doc_structured",
        "warnings": warnings,
    }


def _parse_pdf_with_pymupdf(content: bytes) -> dict[str, Any] | None:
    """Parse text PDFs into ordered blocks with page coordinates.

    ``pypdf`` remains the compatibility parser below.  This adapter is used
    by the typed DocumentIR entry point because block-level coordinates are
    needed later for heading detection and evidence recovery.
    """

    try:
        try:
            import pymupdf as pdf_library
        except ImportError:
            import fitz as pdf_library
    except ImportError:
        return None

    try:
        document = pdf_library.open(stream=content, filetype="pdf")
    except Exception:
        return None

    segments: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    tables: list[dict[str, Any]] = []
    warnings: list[str] = []
    table_finder_available = False
    try:
        for page_index in range(document.page_count):
            page = document[page_index]
            page_number = page_index + 1
            page_width = float(page.rect.width)
            page_height = float(page.rect.height)
            page_tables, page_table_rows = _extract_pymupdf_tables(
                page,
                page_number=page_number,
                table_index_start=len(tables) + 1,
            )
            tables.extend(page_tables)
            table_finder_available = table_finder_available or bool(
                callable(getattr(page, "find_tables", None))
            )
            table_bboxes = [
                table["bbox"]
                for table in page_tables
                if table.get("bbox")
            ]
            try:
                page_data = page.get_text("dict", sort=True)
            except TypeError:
                page_data = page.get_text("dict")
            blocks = page_data.get("blocks", []) if isinstance(page_data, dict) else []
            page_items: list[dict[str, Any]] = []
            block_index = 0
            for block in blocks:
                if not isinstance(block, dict) or block.get("type") != 0:
                    continue
                if any(_bbox_overlaps(block.get("bbox"), bbox) for bbox in table_bboxes):
                    continue
                lines = block.get("lines")
                if not isinstance(lines, list):
                    continue
                line_texts: list[str] = []
                font_names: list[str] = []
                font_sizes: list[float] = []
                bold = False
                for line in lines:
                    spans = line.get("spans") if isinstance(line, dict) else None
                    if not isinstance(spans, list):
                        continue
                    span_text = "".join(
                        str(span.get("text") or "")
                        for span in spans
                        if isinstance(span, dict)
                    )
                    if span_text.strip():
                        line_texts.append(span_text)
                    for span in spans:
                        if not isinstance(span, dict):
                            continue
                        font_name = str(span.get("font") or "")
                        if font_name:
                            font_names.append(font_name)
                        try:
                            font_sizes.append(round(float(span.get("size")), 1))
                        except (TypeError, ValueError):
                            pass
                        flags = int(span.get("flags") or 0)
                        bold = bold or bool(flags & 16)
                text = _clean_text("\n".join(line_texts))
                if not text:
                    continue
                bbox = block.get("bbox")
                normalized_bbox = (
                    [round(float(value), 3) for value in bbox]
                    if isinstance(bbox, (list, tuple)) and len(bbox) == 4
                    else None
                )
                block_index += 1
                page_items.append(
                    {
                        "_sortY": (
                            normalized_bbox[1]
                            if normalized_bbox
                            else float(block_index)
                        ),
                        "record": {
                            "id": "",
                            "sourceLocator": f"page {page_number}, block {block_index}",
                            "page": page_number,
                            "bbox": normalized_bbox,
                            "text": text,
                            "contentType": "pdf_text_block",
                            "blockIndex": block_index,
                            "style": {
                                "fontNames": list(dict.fromkeys(font_names)),
                                "fontSizes": list(dict.fromkeys(font_sizes)),
                                "bold": bold,
                                "lineCount": len(line_texts),
                                "pageWidth": page_width,
                                "pageHeight": page_height,
                                "coordinatesAvailable": bool(normalized_bbox),
                            },
                        },
                    }
                )

            page_items.extend(
                {"_sortY": item.pop("_sortY", 0.0), "record": item}
                for item in page_table_rows
            )
            page_items.sort(key=lambda item: float(item["_sortY"]))
            page_segment_ids: list[str] = []
            page_texts: list[str] = []
            for item in page_items:
                record = item["record"]
                record["id"] = f"SEG-{len(segments) + 1:06d}"
                record["order"] = len(segments)
                record.pop("_sortY", None)
                segments.append(record)
                units.append(record)
                page_segment_ids.append(record["id"])
                page_texts.append(record["text"])

            page_text = "\n".join(page_texts)
            has_text = bool(page_text.strip())
            if not has_text:
                warnings.append(
                    f"page {page_number} has no extractable text; OCR is required"
                )
            pages.append(
                {
                    "page": page_number,
                    "text": page_text,
                    "segmentIds": page_segment_ids,
                    "sourceLocator": f"page {page_number}",
                    "hasText": has_text,
                    "width": page_width,
                    "height": page_height,
                    "textQuality": round(min(len(page_text) / 120.0, 1.0), 3),
                    "isScan": not has_text,
                    "ocrStatus": "required" if not has_text else "not_needed",
                }
            )
    except Exception:
        document.close()
        return None
    finally:
        if not document.is_closed:
            document.close()

    return {
        "text": "\n".join(segment["text"] for segment in segments),
        "segments": segments,
        "units": units,
        "tables": tables,
        "pages": pages,
        "metadata": {
            "pageCount": len(pages),
            "extractedPageCount": sum(1 for page in pages if page["hasText"]),
            "blockCount": len(segments),
            "ocrPages": sum(1 for page in pages if page["isScan"]),
            "tableCount": len(tables),
            "tableExtractionAvailable": table_finder_available,
            "coordinatesAvailable": True,
        },
        "parseMethod": "pymupdf_blocks_structured",
        "warnings": warnings,
    }


def parse_pdf(content: bytes) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except Exception:
        return {
            "text": "",
            "segments": [],
            "units": [],
            "tables": [],
            "metadata": {},
            "parseMethod": "pypdf",
            "warnings": ["未安装 PDF 文字解析依赖。"],
        }
    try:
        reader = PdfReader(BytesIO(content))
    except Exception:
        return {
            "text": "",
            "segments": [],
            "units": [],
            "tables": [],
            "metadata": {},
            "parseMethod": "pypdf",
            "warnings": ["PDF 文件无法读取。"],
        }
    segments: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []
    warnings: list[str] = []
    for page_index, page in enumerate(reader.pages, start=1):
        try:
            page_text = _clean_text(page.extract_text() or "")
        except Exception:
            page_text = ""
        if page_text:
            record = {
                "id": f"SEG-{page_index:06d}",
                "sourceLocator": f"第{page_index}页",
                "page": page_index,
                "text": page_text,
                "contentType": "page",
                "order": page_index - 1,
                "style": {},
            }
            segments.append(record)
            units.append(record)
        else:
            warnings.append(f"第{page_index}页未提取到文字，扫描件需后续接入 OCR。")
    return {
        "text": "\n".join(segment["text"] for segment in segments),
        "segments": segments,
        "units": units,
        "tables": [],
        "metadata": {"pageCount": len(reader.pages)},
        "parseMethod": "pypdf_structured",
        "warnings": warnings,
    }


def parse_xlsx(content: bytes) -> dict[str, Any]:
    namespace = {"main": MAIN_NS}
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            shared_strings: list[str] = []
            if "xl/sharedStrings.xml" in archive.namelist():
                shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
                shared_strings = [
                    "".join(node.text or "" for node in item.findall(".//main:t", namespace))
                    for item in shared_root.findall(".//main:si", namespace)
                ]
            sheets = sorted(
                name for name in archive.namelist()
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            )
            segments: list[dict[str, Any]] = []
            for sheet_index, sheet_name in enumerate(sheets, start=1):
                root = ET.fromstring(archive.read(sheet_name))
                rows = root.findall(".//main:sheetData/main:row", namespace)
                for row_index, row in enumerate(rows, start=1):
                    cells: list[dict[str, Any]] = []
                    for cell_index, cell in enumerate(row.findall("main:c", namespace), start=1):
                        value_node = cell.find("main:v", namespace)
                        inline_node = cell.find("main:is/main:t", namespace)
                        value = (
                            value_node.text
                            if value_node is not None
                            else (inline_node.text if inline_node is not None else "")
                        )
                        if cell.get("t") == "s" and value and value.isdigit():
                            shared_index = int(value)
                            value = shared_strings[shared_index] if shared_index < len(shared_strings) else ""
                        cells.append(
                            {
                                "cellIndex": cell_index,
                                "column": cell.get("r", ""),
                                "text": _clean_text(value),
                            }
                        )
                    if not any(cell["text"] for cell in cells):
                        continue
                    text = " | ".join(cell["text"] for cell in cells if cell["text"])
                    record = {
                        "id": f"SEG-{len(segments) + 1:06d}",
                        "sourceLocator": f"sheet{sheet_index}第{row_index}行",
                        "text": text,
                        "contentType": "spreadsheet_row",
                        "order": len(segments),
                        "sheetIndex": sheet_index,
                        "rowIndex": row_index,
                        "cells": cells,
                        "style": {},
                    }
                    segments.append(record)
    except (zipfile.BadZipFile, ET.ParseError, KeyError):
        return {
            "text": "",
            "segments": [],
            "units": [],
            "tables": [],
            "metadata": {},
            "parseMethod": "xlsx_xml",
            "warnings": ["XLSX 文件解析失败。"],
        }
    return {
        "text": "\n".join(segment["text"] for segment in segments),
        "segments": segments,
        "units": segments.copy(),
        "tables": [],
        "metadata": {"sheetCount": len(sheets)},
        "parseMethod": "xlsx_xml_structured",
        "warnings": [],
    }


def parse_plain_text(content: bytes) -> dict[str, Any]:
    text = content.decode("utf-8", errors="ignore")
    segments = [
        {
            "id": f"SEG-{index + 1:06d}",
            "sourceLocator": f"第{index + 1}行",
            "text": line.strip(),
            "contentType": "line",
            "order": index,
            "style": {},
        }
        for index, line in enumerate(text.splitlines())
        if line.strip()
    ]
    return {
        "text": "\n".join(item["text"] for item in segments),
        "segments": segments,
        "units": segments.copy(),
        "tables": [],
        "metadata": {},
        "parseMethod": "plain_text_structured",
        "warnings": [],
    }


def parse_document_ir(
    content: bytes,
    *,
    file_name: str = "",
    mime_type: str = "",
    document_id: str = "",
    document_version_id: str = "",
) -> "DocumentIR":
    """Parse one file into the typed ``DocumentIR`` boundary.

    The legacy ``parse_*`` functions remain dictionary-based because existing
    Store and matching code still consumes that shape.  New workflow stages
    should call this entry point so structure enrichment and Pydantic
    validation happen exactly once at the parser boundary.
    """

    from .document_models import DocumentIR, document_ir_from_parsed
    from .document_structure import enrich_document_structure

    lower = file_name.lower()
    if lower.endswith(".doc") or "msword" in mime_type.lower():
        parsed = parse_doc(content)
    elif lower.endswith(".docx") or "wordprocessingml" in mime_type:
        parsed = parse_docx(content)
    elif lower.endswith(".pdf") or "pdf" in mime_type.lower():
        parsed = _parse_pdf_with_pymupdf(content) or _parse_pdf_with_pypdf_structured(content)
    elif lower.endswith(".xlsx") or "spreadsheetml" in mime_type:
        parsed = parse_xlsx(content)
    else:
        parsed = parse_plain_text(content)

    enriched = enrich_document_structure(parsed)
    document = document_ir_from_parsed(
        enriched,
        file_name=file_name,
        content=content,
        mime_type=mime_type,
        document_id=document_id,
        document_version_id=document_version_id,
    )
    if not isinstance(document, DocumentIR):
        raise TypeError("document parser produced an invalid DocumentIR")
    return document
