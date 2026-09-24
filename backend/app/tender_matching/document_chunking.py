"""Deterministic, source-addressable chunking for parsed tender documents."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Mapping

from .document_models import DocumentChunk, DocumentIR, DocumentSection, DocumentSegment


DEFAULT_MAX_TOKENS = 800
CHARS_PER_TOKEN = 4


def estimate_tokens(text: str) -> int:
    """Stable budget estimate used for chunk sizing, not billing."""

    return max(1, (len(text or "") + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def _document(value: DocumentIR | Mapping[str, Any]) -> DocumentIR:
    if isinstance(value, DocumentIR):
        return value
    return DocumentIR.model_validate(value)


def _section_id(section: DocumentSection) -> str:
    return section.section_id or section.id


def _section_owner_map(
    sections: list[DocumentSection],
) -> dict[str, DocumentSection]:
    """Assign each segment to the most specific section containing it.

    ``SectionTree`` deliberately stores parent spans as inclusive ranges.
    Choosing the shortest span (then deepest level) turns those overlapping
    ranges into one deterministic ownership map for chunking.
    """

    candidates: dict[str, list[DocumentSection]] = defaultdict(list)
    for section in sections:
        section_id = _section_id(section)
        if not section_id:
            continue
        for segment_id in section.segment_ids:
            if segment_id:
                candidates[segment_id].append(section)

    owners: dict[str, DocumentSection] = {}
    for segment_id, values in candidates.items():
        owners[segment_id] = min(
            values,
            key=lambda section: (
                len(section.segment_ids),
                -section.level,
                sections.index(section),
            ),
        )
    return owners


def _split_text(text: str, max_chars: int) -> list[str]:
    if len(text) <= max_chars:
        return [text]
    pieces: list[str] = []
    remaining = text.strip()
    separators = re.compile(r"(?<=[。！？；.!?;])\s*|(?<=\n)")
    while len(remaining) > max_chars:
        candidates = [match.start() for match in separators.finditer(remaining[: max_chars + 1])]
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


def _page_range(segments: list[DocumentSegment]) -> tuple[int | None, int | None]:
    pages = sorted({segment.page for segment in segments if segment.page is not None})
    return (pages[0], pages[-1]) if pages else (None, None)


def _fingerprint(content: str, segment_ids: list[str], ordinal: int) -> str:
    payload = f"{ordinal}\n" + "\n".join(segment_ids) + "\n" + content
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _make_chunk(
    *,
    document: DocumentIR,
    section: DocumentSection,
    segments: list[DocumentSegment],
    content: str,
    split_reason: str,
    ordinal: int,
    is_heading_included: bool = False,
) -> DocumentChunk:
    section_id = _section_id(section)
    segment_ids = list(dict.fromkeys(segment.id for segment in segments if segment.id))
    table_ids = list(
        dict.fromkeys(
            table_id
            for segment in segments
            for table_id in [segment.table_id]
            if table_id
        )
    )
    page_start, page_end = _page_range(segments)
    path = list(section.section_path or ([section.heading] if section.heading else []))
    prefix = " > ".join(path)
    return DocumentChunk(
        chunkId=f"{document.document_version_id or document.document_id}-CH{ordinal:06d}",
        documentVersionId=document.document_version_id,
        sectionId=section_id,
        sectionPath=path,
        heading=section.heading,
        contextPrefix=prefix,
        content=content.strip(),
        segmentIds=segment_ids,
        tableIds=table_ids,
        startLocator=segments[0].source_locator if segments else section.start_locator,
        endLocator=segments[-1].source_locator if segments else section.end_locator,
        pageStart=page_start,
        pageEnd=page_end,
        tokenCount=estimate_tokens(content),
        fingerprint=_fingerprint(content, segment_ids, ordinal),
        splitReason=split_reason,
        isHeadingIncluded=is_heading_included,
    )


def chunk_document(
    document: DocumentIR | Mapping[str, Any],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> list[DocumentChunk]:
    """Create ordered chunks without losing section context or source IDs."""

    parsed = _document(document)
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    max_chars = max_tokens * CHARS_PER_TOKEN
    owners = _section_owner_map(parsed.sections)
    fallback = next(
        (section for section in parsed.sections if section.implicit and section.level == 0),
        DocumentSection(
            id="SEC-0000",
            sectionId="SEC-0000",
            heading="未分类内容",
            sectionPath=["未分类内容"],
            sectionType="other",
            isMajor=True,
        ),
    )

    ordered_groups: list[tuple[DocumentSection, list[DocumentSegment]]] = []
    current_section: DocumentSection | None = None
    current_segments: list[DocumentSegment] = []
    for segment in sorted(parsed.segments, key=lambda item: (item.order, item.id)):
        section = owners.get(segment.id, fallback)
        if current_section is None or _section_id(section) == _section_id(current_section):
            current_section = section
            current_segments.append(segment)
            continue
        ordered_groups.append((current_section, current_segments))
        current_section = section
        current_segments = [segment]
    if current_section is not None and current_segments:
        ordered_groups.append((current_section, current_segments))

    chunks: list[DocumentChunk] = []
    ordinal = 1
    for section, group in ordered_groups:
        current_segments: list[DocumentSegment] = []
        current_text = ""
        for segment in group:
            text = segment.text.strip()
            if not text:
                continue
            is_table = segment.content_type.startswith("table")
            separator = "\n" if current_text else ""
            candidate = f"{current_text}{separator}{text}"
            if current_text and len(candidate) > max_chars:
                chunks.append(
                    _make_chunk(
                        document=parsed,
                        section=section,
                        segments=current_segments,
                        content=current_text,
                        split_reason="section_overflow",
                        ordinal=ordinal,
                    )
                )
                ordinal += 1
                current_segments = []
                current_text = ""
                candidate = text

            if len(candidate) <= max_chars:
                current_segments.append(segment)
                current_text = candidate
                continue

            # A single oversized paragraph is split only at sentence/line
            # boundaries. Tables remain atomic unless a single row itself is
            # larger than the configured budget.
            pieces = _split_text(text, max_chars)
            for piece_index, piece in enumerate(pieces):
                if current_text:
                    chunks.append(
                        _make_chunk(
                            document=parsed,
                            section=section,
                            segments=current_segments,
                            content=current_text,
                            split_reason="segment_overflow",
                            ordinal=ordinal,
                        )
                    )
                    ordinal += 1
                    current_segments = []
                    current_text = ""
                chunks.append(
                    _make_chunk(
                        document=parsed,
                        section=section,
                        segments=[segment],
                        content=piece,
                        split_reason="table_atom" if is_table else "segment_overflow",
                        ordinal=ordinal,
                    )
                )
                ordinal += 1

        if current_text:
            reason = "section_fit" if len(group) == len(current_segments) else "section_overflow"
            chunks.append(
                _make_chunk(
                    document=parsed,
                    section=section,
                    segments=current_segments,
                    content=current_text,
                    split_reason=reason,
                    ordinal=ordinal,
                )
            )
            ordinal += 1
    return chunks
