from __future__ import annotations

import re
import zipfile
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET


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
