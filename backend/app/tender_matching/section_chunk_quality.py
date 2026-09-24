"""Quality evaluation for SectionTree and derived document chunks."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any

from .document_chunking import chunk_document
from .document_models import DocumentIR, DocumentSection


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "")).strip()


def _score(maximum: int, checks: list[tuple[bool, float]]) -> int:
    return round(max(0.0, min(float(maximum), sum(weight for ok, weight in checks if ok))))


def _coverage(found: int, total: int) -> float:
    return round(found / total, 4) if total else 1.0


def _major_signal(section: DocumentSection) -> bool:
    heading = _clean(section.heading)
    if not heading or section.implicit:
        return False
    if re.match(r"^第[一二三四五六七八九十百千万零〇0-9]+[篇章部分卷]", heading):
        return True
    if re.match(r"^第[一二三四五六七八九十百千万零〇0-9]+\s+", heading):
        return True
    return False


def _section_order_map(document: DocumentIR) -> dict[str, int]:
    return {
        segment.id: index
        for index, segment in enumerate(
            sorted(document.segments, key=lambda item: (item.order, item.id))
        )
        if segment.id
    }


def _ancestor_ids(
    section_id: str,
    sections_by_id: dict[str, DocumentSection],
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    current = sections_by_id.get(section_id)
    while current and current.parent_id and current.parent_id not in seen:
        seen.add(current.parent_id)
        result.append(current.parent_id)
        current = sections_by_id.get(current.parent_id)
    return result


def evaluate_section_tree(document: DocumentIR) -> dict[str, Any]:
    """Assess SectionTree consistency and major-heading detection."""

    issues: list[dict[str, str]] = []
    sections = document.sections
    segments_by_id = {segment.id: segment for segment in document.segments if segment.id}
    order_map = _section_order_map(document)
    sections_by_id = {
        (section.section_id or section.id): section
        for section in sections
        if section.section_id or section.id
    }
    section_ids = list(sections_by_id)
    ids_unique = len(section_ids) == len(set(section_ids)) and all(section_ids)
    references_valid = True
    heading_refs_valid = True
    parent_refs_valid = True
    parent_containment = True
    level_consistent = True
    path_consistent = True
    locator_complete = True
    contiguous_spans = True
    covered_segment_ids: set[str] = set()
    orphan_candidates: list[str] = []
    actual_major_ids: list[str] = []
    expected_major_ids: list[str] = []

    for section in sections:
        section_id = section.section_id or section.id
        section_segment_ids = [item for item in section.segment_ids if item]
        covered_segment_ids.update(section_segment_ids)
        references_valid = references_valid and all(
            segment_id in segments_by_id for segment_id in section_segment_ids
        )
        if section.heading_segment_id:
            heading_refs_valid = heading_refs_valid and section.heading_segment_id in segments_by_id
        parent = sections_by_id.get(section.parent_id) if section.parent_id else None
        parent_refs_valid = parent_refs_valid and (
            not section.parent_id or parent is not None
        )
        if parent:
            parent_ids = set(parent.segment_ids)
            parent_containment = parent_containment and set(section_segment_ids).issubset(parent_ids)
            level_consistent = level_consistent and parent.level < section.level
            expected_path = [*parent.section_path, section.heading]
            path_consistent = path_consistent and section.section_path == expected_path
        elif section.level >= 2 and not section.implicit:
            orphan_candidates.append(section_id)
        locator_complete = locator_complete and bool(
            section.start_locator and section.end_locator
        )
        positions = sorted(
            order_map[segment_id]
            for segment_id in section_segment_ids
            if segment_id in order_map
        )
        if positions:
            contiguous_spans = contiguous_spans and positions == list(
                range(positions[0], positions[-1] + 1)
            )
        if section.is_major and not section.implicit:
            actual_major_ids.append(section_id)
        if _major_signal(section):
            expected_major_ids.append(section_id)

    total_segments = len(segments_by_id)
    segment_coverage = _coverage(len(covered_segment_ids & set(segments_by_id)), total_segments)
    major_recall = _coverage(
        len(set(expected_major_ids) & set(actual_major_ids)),
        len(expected_major_ids),
    )
    major_precision = _coverage(
        len(set(expected_major_ids) & set(actual_major_ids)),
        len(actual_major_ids),
    )
    orphan_ratio = _coverage(len(orphan_candidates), len(sections)) if sections else 0.0

    if not ids_unique:
        issues.append({"severity": "critical", "code": "section_ids", "message": "Section IDs are empty or duplicated"})
    if not references_valid:
        issues.append({"severity": "critical", "code": "section_segment_refs", "message": "A section references an unknown segment"})
    if not heading_refs_valid:
        issues.append({"severity": "critical", "code": "heading_segment_refs", "message": "A heading references an unknown segment"})
    if not parent_refs_valid:
        issues.append({"severity": "critical", "code": "parent_refs", "message": "A section references an unknown parent"})
    if segment_coverage < 1.0:
        issues.append({"severity": "warning", "code": "section_coverage", "message": "Some segments are not covered by any section"})
    if not parent_containment:
        issues.append({"severity": "warning", "code": "parent_containment", "message": "Some child sections are not contained by their parent span"})
    if not level_consistent:
        issues.append({"severity": "warning", "code": "level_consistency", "message": "A child section does not have a deeper level than its parent"})
    if not path_consistent:
        issues.append({"severity": "warning", "code": "section_path", "message": "Some section paths do not match their parent chain"})
    if not locator_complete:
        issues.append({"severity": "warning", "code": "section_locators", "message": "Some sections lack start or end locators"})
    if not contiguous_spans:
        issues.append({"severity": "warning", "code": "section_span", "message": "Some section segment spans are not contiguous"})
    if expected_major_ids and major_recall < 1.0:
        issues.append({
            "severity": "warning",
            "code": "major_heading_recall",
            "message": f"Only {major_recall:.0%} of high-confidence major headings are marked isMajor",
        })
    if actual_major_ids and major_precision < 0.8:
        issues.append({
            "severity": "warning",
            "code": "major_heading_precision",
            "message": f"Only {major_precision:.0%} of isMajor sections match high-confidence major-heading rules",
        })
    if orphan_ratio > 0.25:
        issues.append({
            "severity": "warning",
            "code": "orphan_section_ratio",
            "message": f"{orphan_ratio:.0%} of explicit sections have no parent despite level >= 2",
        })

    score = sum(
        [
            _score(10, [(ids_unique, 3), (references_valid, 4), (heading_refs_valid, 3)]),
            _score(10, [(segment_coverage == 1.0, 5), (contiguous_spans, 5)]),
            _score(10, [(parent_refs_valid, 3), (parent_containment, 3), (level_consistent, 2), (path_consistent, 2)]),
            _score(15, [(major_recall >= 1.0, 10), (major_precision >= 0.8, 5)]),
            _score(5, [(locator_complete, 2), (orphan_ratio <= 0.25, 3)]),
        ]
    )
    critical = any(item["severity"] == "critical" for item in issues)
    normalized_score = score / 50 * 100
    readiness = "not_ready" if critical or normalized_score < 70 else (
        "usable_with_review"
        if (
            normalized_score < 85
            or major_recall < 1.0
            or major_precision < 0.8
            or orphan_ratio > 0.25
        )
        else "ready_for_next_stage"
    )
    unexpected_major_ids = sorted(set(actual_major_ids) - set(expected_major_ids))
    return {
        "score": score,
        "maxScore": 50,
        "readiness": readiness,
        "hardFailure": critical,
        "sectionCount": len(sections),
        "explicitSectionCount": sum(not section.implicit for section in sections),
        "segmentCount": total_segments,
        "segmentCoverage": segment_coverage,
        "sectionIdsUnique": ids_unique,
        "segmentReferencesValid": references_valid,
        "headingReferencesValid": heading_refs_valid,
        "parentReferencesValid": parent_refs_valid,
        "parentContainment": parent_containment,
        "levelConsistency": level_consistent,
        "pathConsistency": path_consistent,
        "locatorCompleteness": locator_complete,
        "contiguousSpans": contiguous_spans,
        "actualMajorCount": len(actual_major_ids),
        "expectedMajorCount": len(expected_major_ids),
        "majorRecall": major_recall,
        "majorPrecision": major_precision,
        "orphanSectionRatio": orphan_ratio,
        "majorHeadings": [
            {
                "sectionId": section_id,
                "heading": sections_by_id[section_id].heading,
                "level": sections_by_id[section_id].level,
                "isMajor": sections_by_id[section_id].is_major,
            }
            for section_id in expected_major_ids
        ],
        "unexpectedMajorHeadings": [
            {
                "sectionId": section_id,
                "heading": sections_by_id[section_id].heading,
                "level": sections_by_id[section_id].level,
                "isMajor": sections_by_id[section_id].is_major,
            }
            for section_id in unexpected_major_ids
        ],
        "issues": issues,
    }


def _chunk_order(chunk: Any, order_map: dict[str, int]) -> int:
    positions = [order_map[item] for item in chunk.segment_ids if item in order_map]
    return min(positions) if positions else 10**9


def evaluate_chunking(
    document: DocumentIR,
    *,
    max_tokens: int = 800,
) -> dict[str, Any]:
    """Assess chunk coverage, source order, budget, and table integrity."""

    chunks = chunk_document(document, max_tokens=max_tokens)
    issues: list[dict[str, str]] = []
    segments_by_id = {segment.id: segment for segment in document.segments if segment.id}
    sections_by_id = {
        (section.section_id or section.id): section
        for section in document.sections
        if section.section_id or section.id
    }
    order_map = _section_order_map(document)
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    ids_unique = len(chunk_ids) == len(set(chunk_ids)) and all(chunk_ids)
    non_empty = all(bool(chunk.content.strip()) for chunk in chunks)
    references_valid = all(
        chunk.section_id in sections_by_id
        and all(segment_id in segments_by_id for segment_id in chunk.segment_ids)
        for chunk in chunks
    )
    budget_valid = all(chunk.token_count <= max_tokens for chunk in chunks)
    ordered = all(
        _chunk_order(left, order_map) <= _chunk_order(right, order_map)
        for left, right in zip(chunks, chunks[1:])
    )
    context_valid = all(
        bool(chunk.context_prefix)
        and bool(chunk.section_path)
        and bool(chunk.start_locator)
        and bool(chunk.end_locator)
        for chunk in chunks
    )

    segment_occurrences: Counter[str] = Counter(
        segment_id
        for chunk in chunks
        for segment_id in chunk.segment_ids
    )
    covered_segment_ids = set(segment_occurrences)
    source_coverage = _coverage(
        len(covered_segment_ids & set(segments_by_id)),
        len(segments_by_id),
    )
    duplicate_segments = {
        segment_id: count
        for segment_id, count in segment_occurrences.items()
        if count > 1
    }
    duplicate_allowed = True
    for segment_id in duplicate_segments:
        related = [
            chunk for chunk in chunks if segment_id in chunk.segment_ids
        ]
        duplicate_allowed = duplicate_allowed and all(
            chunk.split_reason in {"segment_overflow", "table_atom", "section_overflow"}
            for chunk in related
        )

    table_segments = {
        segment.id
        for segment in document.segments
        if segment.content_type.startswith("table")
    }
    table_ids_valid = all(
        all(
            segment_id not in table_segments
            or any(
                table_id == segments_by_id[segment_id].table_id
                for table_id in chunk.table_ids
            )
            for segment_id in chunk.segment_ids
        )
        for chunk in chunks
    )
    table_duplicate_rows = {
        segment_id: count
        for segment_id, count in duplicate_segments.items()
        if segment_id in table_segments
    }
    table_atomic = all(
        count == 1
        or all(
            chunk.split_reason == "table_atom"
            for chunk in chunks
            if segment_id in chunk.segment_ids
        )
        for segment_id, count in table_duplicate_rows.items()
    )

    section_chunk_counts = Counter(chunk.section_id for chunk in chunks)
    section_descendants: dict[str, set[str]] = defaultdict(set)
    for section_id in sections_by_id:
        section_descendants[section_id].add(section_id)
        section_descendants[section_id].update(
            descendant_id
            for descendant_id in sections_by_id
            if section_id in _ancestor_ids(descendant_id, sections_by_id)
        )
    split_reasons = Counter(chunk.split_reason for chunk in chunks)
    oversized_sections: list[dict[str, Any]] = []
    for section in document.sections:
        section_id = section.section_id or section.id
        section_chars = sum(
            len(segments_by_id[segment_id].text)
            for segment_id in section.segment_ids
            if segment_id in segments_by_id
        )
        expected_chunks = sum(
            section_chunk_counts.get(descendant_id, 0)
            for descendant_id in section_descendants[section_id]
        )
        if section_chars > max_tokens * 4 and expected_chunks <= 1:
            oversized_sections.append({
                "sectionId": section_id,
                "heading": section.heading,
                "chars": section_chars,
                "chunkCount": expected_chunks,
            })

    if not ids_unique:
        issues.append({"severity": "critical", "code": "chunk_ids", "message": "Chunk IDs are empty or duplicated"})
    if not non_empty:
        issues.append({"severity": "critical", "code": "empty_chunks", "message": "A chunk has empty content"})
    if not references_valid:
        issues.append({"severity": "critical", "code": "chunk_refs", "message": "A chunk references an unknown section or segment"})
    if source_coverage < 1.0:
        issues.append({"severity": "critical", "code": "chunk_coverage", "message": "Some source segments are missing from chunks"})
    if not budget_valid:
        issues.append({"severity": "warning", "code": "chunk_budget", "message": "A chunk exceeds the configured token budget"})
    if not ordered:
        issues.append({"severity": "warning", "code": "chunk_order", "message": "Chunks are not ordered by source segment order"})
    if not context_valid:
        issues.append({"severity": "warning", "code": "chunk_context", "message": "A chunk lacks source context or locators"})
    if not duplicate_allowed:
        issues.append({"severity": "warning", "code": "duplicate_segments", "message": "Repeated segment IDs are not explained by an allowed split"})
    if not table_ids_valid:
        issues.append({"severity": "critical", "code": "table_ids", "message": "Table segments are not represented in chunk tableIds"})
    if not table_atomic:
        issues.append({"severity": "warning", "code": "table_atomicity", "message": "A table row is split without table_atom semantics"})
    if oversized_sections:
        issues.append({
            "severity": "warning",
            "code": "oversized_section",
            "message": f"{len(oversized_sections)} sections exceed the budget but have one chunk",
        })

    score = sum(
        [
            _score(15, [(source_coverage == 1.0, 8), (references_valid, 4), (non_empty, 3)]),
            _score(10, [(ids_unique, 4), (ordered, 4), (context_valid, 2)]),
            _score(10, [(budget_valid, 10)]),
            _score(10, [(table_ids_valid, 5), (table_atomic, 5)]),
            _score(5, [(not oversized_sections, 5)]),
        ]
    )
    critical = any(item["severity"] == "critical" for item in issues)
    normalized_score = score / 50 * 100
    readiness = "not_ready" if critical or normalized_score < 70 else (
        "usable_with_review" if normalized_score < 85 or oversized_sections else
        "ready_for_next_stage"
    )
    return {
        "score": score,
        "maxScore": 50,
        "readiness": readiness,
        "hardFailure": critical,
        "maxTokens": max_tokens,
        "chunkCount": len(chunks),
        "sectionCountWithChunks": len(section_chunk_counts),
        "sourceCoverage": source_coverage,
        "chunkIdsUnique": ids_unique,
        "nonEmptyChunks": non_empty,
        "chunkReferencesValid": references_valid,
        "budgetValid": budget_valid,
        "sourceOrderValid": ordered,
        "contextValid": context_valid,
        "duplicateSegments": duplicate_segments,
        "duplicateSegmentsAllowed": duplicate_allowed,
        "tableIdsValid": table_ids_valid,
        "tableAtomicity": table_atomic,
        "oversizedSectionCount": len(oversized_sections),
        "oversizedSections": oversized_sections[:20],
        "splitReasons": dict(sorted(split_reasons.items())),
        "maxObservedTokens": max((chunk.token_count for chunk in chunks), default=0),
        "issues": issues,
    }


def evaluate_section_and_chunking(
    document: DocumentIR,
    *,
    max_tokens: int = 800,
) -> dict[str, Any]:
    section_tree = evaluate_section_tree(document)
    chunking = evaluate_chunking(document, max_tokens=max_tokens)
    critical = section_tree["hardFailure"] or chunking["hardFailure"]
    score = section_tree["score"] + chunking["score"]
    if critical or score < 70:
        readiness = "not_ready"
    elif section_tree["readiness"] != "ready_for_next_stage" or chunking["readiness"] != "ready_for_next_stage":
        readiness = "usable_with_review"
    else:
        readiness = "ready_for_next_stage"
    return {
        "score": score,
        "maxScore": 100,
        "readiness": readiness,
        "sectionTree": section_tree,
        "chunking": chunking,
        "issues": [
            *section_tree["issues"],
            *chunking["issues"],
        ],
    }
