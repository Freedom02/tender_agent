"""Shared table semantics used by requirement extraction and routing.

The parser keeps table rows source-addressable, but a requirement often lives
in a different cell from the product name.  This module resolves that
relationship once so every downstream path can preserve the same metadata.
"""
from __future__ import annotations

import re
from typing import Any, Mapping


PRODUCT_HEADER_TERMS = (
    "品目名称",
    "产品名称",
    "标的名称",
    "货物名称",
    "投标标的",
    "商品名称",
    "报价内容",
    "模块名称",
)

GENERIC_PRODUCT_HEADER_TERMS = ("名称",)


def _text(value: Any, limit: int = 240) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _table_id(table: Mapping[str, Any]) -> str:
    return str(table.get("tableId") or table.get("id") or "").strip()


def _row_cells(row: Mapping[str, Any]) -> list[str]:
    values = row.get("cells")
    if not isinstance(values, list):
        return []
    return [
        _text(cell.get("text"), 20000)
        for cell in values
        if isinstance(cell, Mapping)
    ]


def _header_row(table: Mapping[str, Any]) -> tuple[int, list[str]]:
    rows = [
        row
        for row in table.get("rows") or []
        if isinstance(row, Mapping)
    ]
    for row in rows[:4]:
        cells = _row_cells(row)
        if any(
            any(term in _compact(cell) for term in PRODUCT_HEADER_TERMS)
            for cell in cells
        ):
            return int(row.get("rowIndex") or 1), cells
    if rows:
        first = rows[0]
        return int(first.get("rowIndex") or 1), _row_cells(first)
    return 1, []


def _product_column(header_cells: list[str]) -> tuple[int | None, str]:
    # 先做「精确列名」匹配：避免「品目名称」作为子串误命中「采购品目名称」，
    # 把品目类别（如 A02102100 教学仪器）当成产品名，而漏掉真正的「标的名称」列。
    for index, cell in enumerate(header_cells):
        compact = _compact(cell)
        for term in PRODUCT_HEADER_TERMS:
            if _compact(term) == compact:
                return index, _text(cell, 80)
    # 再回退到「子串」匹配：处理「采购品目名称」这类带前缀、且没有更好列名的情况。
    for index, cell in enumerate(header_cells):
        compact = _compact(cell)
        for term in PRODUCT_HEADER_TERMS:
            if term in compact:
                return index, _text(cell, 80)
    for index, cell in enumerate(header_cells):
        compact = _compact(cell)
        if compact in {_compact(term) for term in GENERIC_PRODUCT_HEADER_TERMS}:
            return index, _text(cell, 80)
    return None, ""


def _clean_product_name(value: Any) -> str:
    text = _text(value, 240)
    if not text:
        return ""
    text = re.sub(r"^[★☆◆◇▲△●○※\s]+", "", text)
    text = re.sub(
        r"\s*[（(]\s*(?:核心产品|核心标的|主产品|主要产品)\s*[）)]\s*$",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s+", " ", text).strip(" ：:;；")
    if not text or text in {"-", "—", "…", "……", "{供应商响应}"}:
        return ""
    return text[:240]


def _target_type(source_column: str) -> str:
    compact = _compact(source_column)
    if "模块" in compact:
        return "module"
    if any(term in compact for term in ("服务", "方案")):
        return "service"
    if any(term in compact for term in ("产品", "品目", "标的", "货物", "商品")):
        return "product"
    return "item"


def build_table_row_semantics(
    tables: list[Mapping[str, Any]] | None,
) -> dict[tuple[str, int], dict[str, str]]:
    """Return product metadata for every addressable table row.

    Product names are carried forward within a table because technical tables
    commonly merge the product cell across several continuation rows.
    """

    result: dict[tuple[str, int], dict[str, str]] = {}
    for table in tables or []:
        if not isinstance(table, Mapping):
            continue
        table_id = _table_id(table)
        if not table_id:
            continue
        header_row_index, headers = _header_row(table)
        product_index, source_column = _product_column(headers)
        if product_index is None:
            continue
        target_type = _target_type(source_column)
        current_product = ""
        rows = [
            row
            for row in table.get("rows") or []
            if isinstance(row, Mapping)
        ]
        for row in rows:
            row_index = int(row.get("rowIndex") or 0)
            if not row_index:
                continue
            cells = _row_cells(row)
            candidate = ""
            if row_index != header_row_index and product_index < len(cells):
                candidate = _clean_product_name(cells[product_index])
            if candidate:
                current_product = candidate
            if current_product:
                result[(table_id, row_index)] = {
                    "productName": current_product,
                    "targetType": target_type,
                    "sourceColumn": source_column,
                }
    return result


def semantics_for_segment(
    segment: Mapping[str, Any],
    row_semantics: Mapping[tuple[str, int], Mapping[str, str]],
) -> dict[str, str]:
    table_id = str(segment.get("tableId") or "").strip()
    if not table_id:
        return {}
    try:
        row_index = int(segment.get("rowIndex") or 0)
    except (TypeError, ValueError):
        row_index = 0
    values = row_semantics.get((table_id, row_index))
    return dict(values) if isinstance(values, Mapping) else {}


def product_context_from_segments(
    segments: list[Mapping[str, Any]] | None,
    row_semantics: Mapping[tuple[str, int], Mapping[str, str]],
) -> dict[str, str]:
    """Pick the first product context represented by a source segment list."""

    for segment in segments or []:
        values = semantics_for_segment(segment, row_semantics)
        if values.get("productName"):
            return values
    return {}
