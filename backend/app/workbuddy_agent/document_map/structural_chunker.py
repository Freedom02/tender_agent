"""Target-aware structural blocks for the DocumentMap workflow.

The legacy chunker remains available for compatibility.  This module is the
new map-first boundary: only technical and qualification leaf ranges produce
fine blocks; display and template ranges retain chapter/table structure.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from ...tender_matching.document_models import (
    DocumentIR,
    DocumentSection,
    DocumentSegment,
    DocumentTable,
)
from .schemas import DocumentMap, DocumentMapSection, DocumentRange


CHARS_PER_TOKEN = 4


class StructuralBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    block_id: str = Field(..., alias="blockId")
    block_type: Literal["section", "table", "fine"] = Field(..., alias="blockType")
    domain: str
    processing_mode: str = Field(..., alias="processingMode")
    section_id: str = Field("", alias="sectionId")
    section_path: list[str] = Field(default_factory=list, alias="sectionPath")
    title: str = ""
    content: str = ""
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")
    context_section_ids: list[str] = Field(
        default_factory=list,
        alias="contextSectionIds",
    )
    source_locator: str = Field("", alias="sourceLocator")
    parent_block_id: str = Field("", alias="parentBlockId")
    token_count: int = Field(0, alias="tokenCount", ge=0)
    split_reason: str = Field("section", alias="splitReason")


class SectionBlock(StructuralBlock):
    block_type: Literal["section"] = Field("section", alias="blockType")


class TableBlock(StructuralBlock):
    block_type: Literal["table"] = Field("table", alias="blockType")
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")


class FineBlock(StructuralBlock):
    block_type: Literal["fine"] = Field("fine", alias="blockType")
    split_reason: str = Field("fine_split", alias="splitReason")


class StructuralBlockSet(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = Field(
        "tender-structural-blocks/v1",
        alias="schemaVersion",
    )
    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")
    blocks: list[StructuralBlock] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)


def estimate_block_tokens(text: str) -> int:
    return max(1, (len(text or "") + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def _as_document(value: DocumentIR | Mapping[str, object]) -> DocumentIR:
    return value if isinstance(value, DocumentIR) else DocumentIR.model_validate(value)


def _as_map(value: DocumentMap | Mapping[str, object]) -> DocumentMap:
    return value if isinstance(value, DocumentMap) else DocumentMap.model_validate(value)


def _section_id(section: DocumentSection) -> str:
    return str(section.section_id or section.id or "")


def _compact(text: object) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    remaining = text.strip()
    separators = re.compile(r"(?<=[。！？；.!?;])\s*|(?<=\n)")
    while len(remaining) > max_chars:
        candidates = [
            match.start()
            for match in separators.finditer(remaining[: max_chars + 1])
        ]
        cut = max(candidates, default=max_chars)
        if cut <= 0:
            cut = max_chars
        piece = remaining[:cut].strip()
        if piece:
            pieces.append(piece)
        remaining = remaining[cut:].strip()
    if remaining:
        pieces.append(remaining)
    return pieces


def _descendant_ids(
    sections: list[DocumentSection],
) -> dict[str, set[str]]:
    children: dict[str, list[str]] = defaultdict(list)
    for section in sections:
        section_id = _section_id(section)
        if section_id and section.parent_id:
            children[section.parent_id].append(section_id)

    descendants: dict[str, set[str]] = defaultdict(set)
    for section_id in children:
        pending = list(children[section_id])
        while pending:
            child = pending.pop()
            if child in descendants[section_id]:
                continue
            descendants[section_id].add(child)
            pending.extend(children.get(child, []))
    return descendants


def _direct_segment_ids(
    section: DocumentSection,
    *,
    sections_by_id: dict[str, DocumentSection],
    descendants: dict[str, set[str]],
) -> list[str]:
    section_id = _section_id(section)
    descendant_segments = {
        segment_id
        for child_id in descendants.get(section_id, set())
        for segment_id in sections_by_id[child_id].segment_ids
    }
    return [
        segment_id
        for segment_id in section.segment_ids
        if segment_id and segment_id not in descendant_segments
    ]


def _source_locator(segments: list[DocumentSegment], section: DocumentSection) -> str:
    if segments:
        return segments[0].source_locator or segments[-1].source_locator
    return section.start_locator or section.end_locator


def _ordered_segment_ids(
    segment_ids: set[str],
    *,
    ordered: dict[str, int],
) -> list[str]:
    return sorted(
        (segment_id for segment_id in segment_ids if segment_id in ordered),
        key=lambda segment_id: ordered[segment_id],
    )


def _range_segment_ids(
    item: DocumentMapSection,
    *,
    ordered_ids: list[str],
    ordered: dict[str, int],
) -> set[str]:
    """Resolve explicit and inclusive map ranges without widening other scopes."""

    selected: set[str] = set()
    for item_range in _document_ranges(item):
        explicit = {
            *item_range.segment_ids,
            *item_range.source.segment_ids,
        }
        if explicit:
            selected.update(explicit)
            continue
        start = ordered.get(item_range.start_segment_id)
        end = ordered.get(item_range.end_segment_id)
        if start is not None and end is not None:
            left, right = sorted((start, end))
            selected.update(ordered_ids[left : right + 1])
        else:
            selected.update(item_range.source.segment_ids)
    return selected


def _document_ranges(item: DocumentMapSection) -> list[DocumentRange]:
    return [
        item_range
        if isinstance(item_range, DocumentRange)
        else DocumentRange.model_validate(item_range)
        for item_range in item.ranges
    ]


def _mapped_segment_ids(
    item: DocumentMapSection,
    *,
    sections_by_id: dict[str, DocumentSection],
    descendants: dict[str, set[str]],
    segments_by_id: dict[str, DocumentSegment],
    tables_by_id: dict[str, DocumentTable],
    ordered_ids: list[str],
    ordered: dict[str, int],
) -> list[str]:
    """Resolve the map's actual source span, including disjoint ranges."""

    selected: set[str] = {
        segment_id
        for segment_id in [
            *item.source_segment_ids,
            *item.source.segment_ids,
        ]
        if segment_id in segments_by_id
    }
    selected.update(
        segment_id
        for segment_id in _range_segment_ids(
            item,
            ordered_ids=ordered_ids,
            ordered=ordered,
        )
        if segment_id in segments_by_id
    )

    referenced_section_ids = list(
        dict.fromkeys(
            [
                *item.section_ids,
                item.owner_section_id,
                *item.source.section_ids,
                *[
                    section_id
                    for item_range in _document_ranges(item)
                    for section_id in item_range.section_ids
                ],
                *[
                    section_id
                    for item_range in _document_ranges(item)
                    for section_id in item_range.source.section_ids
                ],
            ]
        )
    )
    if not selected:
        for section_id in referenced_section_ids:
            section = sections_by_id.get(section_id)
            if section is None:
                continue
            section_id = _section_id(section)
            ids = (
                list(section.segment_ids)
                if item.role == "container"
                else _direct_segment_ids(
                    section,
                    sections_by_id=sections_by_id,
                    descendants=descendants,
                )
            )
            selected.update(item_id for item_id in ids if item_id in segments_by_id)

    table_ids = set(item.table_ids) | set(item.source.table_ids)
    table_ids.update(
        table_id
        for item_range in _document_ranges(item)
        for table_id in [*item_range.table_ids, *item_range.source.table_ids]
    )
    if table_ids:
        selected.update(
            segment.id
            for segment in segments_by_id.values()
            if segment.table_id in table_ids
        )
    return _ordered_segment_ids(selected, ordered=ordered)


def _fingerprint(block_type: str, section_id: str, content: str, ids: list[str]) -> str:
    raw = f"{block_type}|{section_id}|{'|'.join(ids)}|{content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _table_text(table: DocumentTable) -> str:
    return "\n".join(
        _compact(row.text or " | ".join(cell.text for cell in row.cells))
        for row in table.rows
        if _compact(row.text or " | ".join(cell.text for cell in row.cells))
    )


def _base_kwargs(
    *,
    item: DocumentMapSection,
    section: DocumentSection,
    block_type: str,
    content: str,
    segment_ids: list[str],
    table_ids: list[str],
    source_locator: str,
    parent_block_id: str = "",
    split_reason: str,
) -> dict[str, object]:
    block_id = _fingerprint(block_type, _section_id(section), content, segment_ids)
    path = list(section.section_path or ([section.heading] if section.heading else []))
    return {
        "blockId": f"BLK-{block_id}",
        "blockType": block_type,
        "domain": item.domain,
        "processingMode": item.processing_mode,
        "sectionId": _section_id(section),
        "sectionPath": path,
        "title": section.heading,
        "content": content,
        "segmentIds": list(dict.fromkeys(segment_ids)),
        "tableIds": list(dict.fromkeys(table_ids)),
        "contextSectionIds": list(item.context_section_ids),
        "sourceLocator": source_locator,
        "parentBlockId": parent_block_id,
        "tokenCount": estimate_block_tokens(content),
        "splitReason": split_reason,
    }


def build_structural_blocks(
    document: DocumentIR | Mapping[str, object],
    document_map: DocumentMap | Mapping[str, object],
    *,
    max_tokens: int = 800,
) -> StructuralBlockSet:
    """Build source-addressable blocks according to target processing modes."""

    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    parsed = _as_document(document)
    mapped = _as_map(document_map)
    sections = [_section for _section in parsed.sections if _section_id(_section)]
    sections_by_id = {_section_id(section): section for section in sections}
    descendants = _descendant_ids(sections)
    segments_by_id = {segment.id: segment for segment in parsed.segments if segment.id}
    tables_by_id = {
        str(table.table_id or table.id): table
        for table in parsed.tables
        if str(table.table_id or table.id)
    }
    ordered = {segment.id: index for index, segment in enumerate(parsed.segments)}
    blocks: list[StructuralBlock] = []
    warnings: list[str] = []
    map_sections = [
        item
        for item in mapped.sections
        if item.processing_mode in {"display", "fine_extract", "template_extract"}
        and item.domain not in {"rejection", "project_info"}
    ]
    map_sections.sort(
        key=lambda item: min(
            (ordered.get(segment_id, 10**9) for segment_id in item.source_segment_ids),
            default=10**9,
        )
    )

    for item in map_sections:
        section_id = item.owner_section_id or (item.section_ids[0] if item.section_ids else "")
        section = sections_by_id.get(section_id)
        mapped_segment_ids = _mapped_segment_ids(
            item,
            sections_by_id=sections_by_id,
            descendants=descendants,
            segments_by_id=segments_by_id,
            tables_by_id=tables_by_id,
            ordered_ids=list(ordered),
            ordered=ordered,
        )
        if section is None:
            inferred_id = section_id or item.map_id
            inferred_segments = [
                segments_by_id[segment_id]
                for segment_id in mapped_segment_ids
                if segment_id in segments_by_id
            ]
            section = DocumentSection(
                id=inferred_id,
                sectionId=inferred_id,
                heading=item.title,
                sectionPath=[item.title] if item.title else [],
                segmentIds=mapped_segment_ids,
                startLocator=_source_locator(inferred_segments, DocumentSection()),
            )
            warnings.append(f"inferred_section:{item.map_id}:{inferred_id}")
        if not mapped_segment_ids:
            warnings.append(f"missing_source_segments:{item.map_id}")
            continue
        direct_segments = [
            segments_by_id[segment_id]
            for segment_id in mapped_segment_ids
            if segment_id in segments_by_id
        ]
        table_ids = list(
            dict.fromkeys(
                segment.table_id
                for segment in direct_segments
                if segment.table_id
            )
        )
        table_ids = list(dict.fromkeys([*item.table_ids, *table_ids]))
        non_table_segments = [
            segment for segment in direct_segments if not segment.table_id
        ]
        non_table_text = "\n".join(
            _compact(segment.text)
            for segment in non_table_segments
            if _compact(segment.text)
        )
        section_block = SectionBlock(
            **_base_kwargs(
                item=item,
                section=section,
                block_type="section",
                content=non_table_text,
                segment_ids=[segment.id for segment in non_table_segments],
                table_ids=table_ids,
                source_locator=_source_locator(direct_segments, section),
                split_reason="section",
            )
        )
        blocks.append(section_block)

        for table_id in table_ids:
            table = tables_by_id.get(table_id)
            table_segments = [
                segment for segment in direct_segments if segment.table_id == table_id
            ]
            content = _table_text(table) if table is not None else "\n".join(
                _compact(segment.text) for segment in table_segments
            )
            if not content:
                content = "\n".join(
                    _compact(segment.text) for segment in table_segments
                )
            blocks.append(
                TableBlock(
                    **_base_kwargs(
                        item=item,
                        section=section,
                        block_type="table",
                        content=content,
                        segment_ids=[segment.id for segment in table_segments],
                        table_ids=[table_id],
                        source_locator=_source_locator(table_segments, section),
                        parent_block_id=section_block.block_id,
                        split_reason="table_atom",
                    )
                )
            )

        if item.processing_mode != "fine_extract":
            continue
        max_chars = max_tokens * CHARS_PER_TOKEN
        current_ids: list[str] = []
        current_text = ""
        fine_ordinal = 0
        for segment in non_table_segments:
            text = _compact(segment.text)
            if not text:
                continue
            candidate = f"{current_text}\n{text}".strip() if current_text else text
            if current_text and len(candidate) > max_chars:
                blocks.append(
                    FineBlock(
                        **_base_kwargs(
                            item=item,
                            section=section,
                            block_type="fine",
                            content=current_text,
                            segment_ids=current_ids,
                            table_ids=[],
                            source_locator=_source_locator(
                                [segments_by_id[value] for value in current_ids],
                                section,
                            ),
                            parent_block_id=section_block.block_id,
                            split_reason="fine_overflow",
                        )
                    )
                )
                fine_ordinal += 1
                current_ids = []
                current_text = ""
                candidate = text
            if len(candidate) <= max_chars:
                current_ids.append(segment.id)
                current_text = candidate
                continue
            pieces = _split_text(text, max_chars)
            for piece in pieces:
                blocks.append(
                    FineBlock(
                        **_base_kwargs(
                            item=item,
                            section=section,
                            block_type="fine",
                            content=piece,
                            segment_ids=[segment.id],
                            table_ids=[],
                            source_locator=segment.source_locator,
                            parent_block_id=section_block.block_id,
                            split_reason="fine_segment_overflow",
                        )
                    )
                )
                fine_ordinal += 1
            current_ids = []
            current_text = ""
        if current_text:
            blocks.append(
                FineBlock(
                    **_base_kwargs(
                        item=item,
                        section=section,
                        block_type="fine",
                        content=current_text,
                        segment_ids=current_ids,
                        table_ids=[],
                        source_locator=_source_locator(
                            [segments_by_id[value] for value in current_ids],
                            section,
                        ),
                        parent_block_id=section_block.block_id,
                        split_reason="fine_section",
                    )
                )
            )

    return StructuralBlockSet(
        documentId=parsed.document_id,
        documentVersionId=parsed.document_version_id,
        blocks=blocks,
        warnings=list(dict.fromkeys(warnings)),
        stats={
            "blockCount": len(blocks),
            "sectionBlockCount": sum(item.block_type == "section" for item in blocks),
            "tableBlockCount": sum(item.block_type == "table" for item in blocks),
            "fineBlockCount": sum(item.block_type == "fine" for item in blocks),
        },
    )
