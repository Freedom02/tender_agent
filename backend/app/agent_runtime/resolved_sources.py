"""Index persisted document blocks without searching unrelated document text."""
from __future__ import annotations

from typing import Any


def normalized_quote(text: str) -> str:
    return "".join(text.split())


def source_index(document_blocks: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}

    def add(block: dict[str, Any], context: dict[str, Any]) -> None:
        block_id = str(block.get("id") or block.get("segmentId") or "")
        if not block_id:
            return
        record = {**context, **{key: value for key, value in block.items() if value not in (None, "") or key not in context}}
        previous = index.get(block_id, {})
        for key in previous.keys() & record.keys():
            if previous[key] not in (None, "") and record[key] not in (None, "") and previous[key] != record[key]:
                raise ValueError(f"ambiguous document block id: {block_id}")
        index[block_id] = {**previous, **{key: value for key, value in record.items() if value not in (None, "") or key not in previous}}

    for unit in document_blocks.get("units") or []:
        if isinstance(unit, dict):
            add(unit, {})
    for table in document_blocks.get("tables") or []:
        if not isinstance(table, dict):
            continue
        table_id = str(table.get("id") or table.get("tableId") or "")
        context = {"tableId": table_id, "bodyChildIndex": table.get("bodyChildIndex")}
        add({**table, "id": table_id}, context)
        for row in table.get("rows") or []:
            if not isinstance(row, dict):
                continue
            row_context = {**context, "rowIndex": row.get("rowIndex")}
            add(row, row_context)
            for cell in row.get("cells") or []:
                if not isinstance(cell, dict):
                    continue
                cell_context = {**row_context, "cellIndex": cell.get("cellIndex")}
                add(cell, cell_context)
                for paragraph in cell.get("paragraphs") or []:
                    if isinstance(paragraph, dict):
                        add(paragraph, cell_context)
    return index
