"""Coverage helpers for validating a DocumentMap against DocumentIR indexes."""
from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any, Iterable

from .schemas import DocumentMap, DocumentMapCoverage


def _ids(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _anchor_segment_ids(anchor: Any) -> set[str]:
    if anchor is None:
        return set()
    values = {
        *[str(value) for value in getattr(anchor, "segment_ids", []) if str(value).strip()],
    }
    segment_id = str(getattr(anchor, "segment_id", "") or "").strip()
    if segment_id:
        values.add(segment_id)
    return values


def _anchor_section_ids(anchor: Any) -> set[str]:
    if anchor is None:
        return set()
    values = {
        str(value)
        for value in getattr(anchor, "section_ids", []) or []
        if str(value).strip()
    }
    section_id = str(getattr(anchor, "section_id", "") or "").strip()
    if section_id:
        values.add(section_id)
    return values


def _record_value(record: Any, key: str) -> Any:
    if isinstance(record, Mapping):
        return record.get(key)
    attribute = {
        "sectionId": "section_id",
        "segmentIds": "segment_ids",
    }.get(key, key)
    return getattr(record, attribute, None)


def _section_segment_index(sections: Iterable[Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for section in sections:
        section_id = str(
            _record_value(section, "sectionId")
            or _record_value(section, "id")
            or ""
        ).strip()
        if not section_id:
            continue
        segment_ids = {
            str(value)
            for value in (_record_value(section, "segmentIds") or [])
            if str(value).strip()
        }
        if segment_ids:
            result.setdefault(section_id, set()).update(segment_ids)
    return result


def _expand_section_coverage(
    covered: set[str],
    section_ids: Iterable[Any],
    *,
    section_segments: Mapping[str, set[str]],
    valid_segments: set[str],
) -> None:
    for section_id in section_ids:
        covered.update(
            segment_id
            for segment_id in section_segments.get(str(section_id), set())
            if segment_id in valid_segments
        )


def build_document_map_coverage(
    document_map: DocumentMap,
    *,
    segment_ids: Iterable[Any],
    sections: Iterable[Any] = (),
    high_risk_segment_ids: Iterable[Any] = (),
) -> DocumentMapCoverage:
    valid_segments = set(_ids(segment_ids))
    section_segments = _section_segment_index(sections)
    covered: set[str] = set()
    domain_counts: Counter[str] = Counter()

    for item in document_map.sections:
        domain_counts[item.domain] += 1
        covered.update(
            segment_id
            for segment_id in item.source_segment_ids
            if segment_id in valid_segments
        )
        _expand_section_coverage(
            covered,
            item.section_ids,
            section_segments=section_segments,
            valid_segments=valid_segments,
        )
        _expand_section_coverage(
            covered,
            _anchor_section_ids(item.source),
            section_segments=section_segments,
            valid_segments=valid_segments,
        )
        covered.update(
            segment_id
            for segment_id in _anchor_segment_ids(item.source)
            if segment_id in valid_segments
        )
        for item_range in item.ranges:
            covered.update(
                segment_id
                for segment_id in item_range.segment_ids
                if segment_id in valid_segments
            )
            if item_range.start_segment_id in valid_segments:
                covered.add(item_range.start_segment_id)
            if item_range.end_segment_id in valid_segments:
                covered.add(item_range.end_segment_id)
            _expand_section_coverage(
                covered,
                item_range.section_ids,
                section_segments=section_segments,
                valid_segments=valid_segments,
            )
            _expand_section_coverage(
                covered,
                _anchor_section_ids(item_range.source),
                section_segments=section_segments,
                valid_segments=valid_segments,
            )
            covered.update(
                segment_id
                for segment_id in _anchor_segment_ids(item_range.source)
                if segment_id in valid_segments
            )
        if item.template_locator is not None:
            _expand_section_coverage(
                covered,
                item.template_locator.section_ids,
                section_segments=section_segments,
                valid_segments=valid_segments,
            )
            covered.update(
                segment_id
                for segment_id in item.template_locator.segment_ids
                if segment_id in valid_segments
            )

    for item in document_map.ignored_sections:
        domain_counts[item.domain] += 1
        covered.update(
            segment_id
            for segment_id in item.source_segment_ids
            if segment_id in valid_segments
        )
        _expand_section_coverage(
            covered,
            item.section_ids,
            section_segments=section_segments,
            valid_segments=valid_segments,
        )
        _expand_section_coverage(
            covered,
            _anchor_section_ids(item.source),
            section_segments=section_segments,
            valid_segments=valid_segments,
        )
        covered.update(
            segment_id
            for segment_id in _anchor_segment_ids(item.source)
            if segment_id in valid_segments
        )
        for item_range in item.ranges:
            covered.update(
                segment_id
                for segment_id in item_range.segment_ids
                if segment_id in valid_segments
            )
            covered.update(
                segment_id
                for segment_id in _anchor_segment_ids(item_range.source)
                if segment_id in valid_segments
            )
            _expand_section_coverage(
                covered,
                item_range.section_ids,
                section_segments=section_segments,
                valid_segments=valid_segments,
            )
            _expand_section_coverage(
                covered,
                _anchor_section_ids(item_range.source),
                section_segments=section_segments,
                valid_segments=valid_segments,
            )

    high_risk = set(_ids(high_risk_segment_ids)) & valid_segments
    return DocumentMapCoverage(
        coveredSegmentIds=sorted(covered),
        unclassifiedSegmentIds=sorted(valid_segments - covered),
        highRiskHitCount=len(high_risk),
        highRiskCoveredCount=len(high_risk & covered),
        domainCounts=dict(domain_counts),
    )
