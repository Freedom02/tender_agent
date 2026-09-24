"""Validation rules for agent-produced DocumentMap artifacts."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .coverage import build_document_map_coverage
from .schemas import (
    DOCUMENT_MAP_DOMAINS,
    DocumentMap,
    DocumentMapSection,
    DocumentSourceAnchor,
)


class MapValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    code: str = Field(..., min_length=1)
    severity: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    object_id: str = Field("", alias="objectId")
    source_ids: list[str] = Field(default_factory=list, alias="sourceIds")


class DocumentMapValidation(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    valid: bool
    normalized_map: DocumentMap = Field(alias="normalizedMap")
    issues: list[MapValidationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)


def _as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="python", by_alias=True)
    return dict(value) if isinstance(value, Mapping) else {}


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python", by_alias=True)
    if not isinstance(value, list):
        return []
    return [_as_dict(item) for item in value if isinstance(item, (Mapping, BaseModel))]


def _record_ids(records: Iterable[dict[str, Any]], *keys: str) -> set[str]:
    result: set[str] = set()
    for record in records:
        for key in keys:
            value = str(record.get(key) or "").strip()
            if value:
                result.add(value)
    return result


def _anchor_ids(anchor: DocumentSourceAnchor) -> set[str]:
    return {
        *[str(value) for value in anchor.section_ids if str(value)],
        *[str(value) for value in anchor.segment_ids if str(value)],
        *[str(value) for value in anchor.table_ids if str(value)],
        *([anchor.section_id] if anchor.section_id else []),
        *([anchor.segment_id] if anchor.segment_id else []),
        *([anchor.table_id] if anchor.table_id else []),
    }


def _unknown_anchor_ids(
    anchor: DocumentSourceAnchor,
    *,
    valid_section_ids: set[str],
    valid_segment_ids: set[str],
    valid_table_ids: set[str],
) -> tuple[set[str], set[str], set[str]]:
    section_values = {
        *[str(value) for value in anchor.section_ids if str(value)],
        *([anchor.section_id] if anchor.section_id else []),
    }
    segment_values = {
        *[str(value) for value in anchor.segment_ids if str(value)],
        *([anchor.segment_id] if anchor.segment_id else []),
    }
    table_values = {
        *[str(value) for value in anchor.table_ids if str(value)],
        *([anchor.table_id] if anchor.table_id else []),
    }
    return (
        section_values - valid_section_ids,
        segment_values - valid_segment_ids,
        table_values - valid_table_ids,
    )


def _has_anchor_reference(anchor: DocumentSourceAnchor) -> bool:
    return bool(
        _anchor_ids(anchor)
        or anchor.source_locator
        or anchor.page_start
        or anchor.paragraph_start
        or anchor.line_start
        or anchor.row_start
    )


def _section_source_ids(item: DocumentMapSection) -> set[str]:
    values = set(item.source_segment_ids)
    values.update(item.section_ids)
    values.update(item.table_ids)
    values.update(_anchor_ids(item.source))
    for item_range in item.ranges:
        values.update(item_range.segment_ids)
        values.update(item_range.section_ids)
        values.update(item_range.table_ids)
        values.update(
            value
            for value in (
                item_range.start_segment_id,
                item_range.end_segment_id,
            )
            if value
        )
        values.update(_anchor_ids(item_range.source))
    if item.template_locator is not None:
        values.update(item.template_locator.section_ids)
        values.update(item.template_locator.segment_ids)
        values.update(item.template_locator.table_ids)
    return {str(value) for value in values if str(value)}


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    object_id: str = "",
    source_ids: Iterable[str] = (),
) -> MapValidationIssue:
    return MapValidationIssue(
        code=code,
        severity=severity,
        message=message,
        objectId=object_id,
        sourceIds=list(dict.fromkeys(str(value) for value in source_ids if str(value))),
    )


def validate_document_map(
    document_map: DocumentMap | Mapping[str, Any],
    *,
    sections: Iterable[Any] = (),
    segments: Iterable[Any] = (),
    tables: Iterable[Any] = (),
    document_id: str = "",
    document_version_id: str = "",
    high_risk_segment_ids: Iterable[Any] = (),
    require_active_source: bool = True,
) -> DocumentMapValidation:
    """Validate references and routing without mutating the source map."""

    normalized = (
        document_map
        if isinstance(document_map, DocumentMap)
        else DocumentMap.model_validate(document_map)
    )
    section_records = _records(sections)
    segment_records = _records(segments)
    table_records = _records(tables)
    valid_section_ids = _record_ids(section_records, "sectionId", "id")
    valid_segment_ids = _record_ids(segment_records, "id", "segmentId")
    valid_table_ids = _record_ids(table_records, "tableId", "id")
    high_risk_ids = list(high_risk_segment_ids)
    issues: list[MapValidationIssue] = []

    if document_id and normalized.document_id != document_id:
        issues.append(
            _issue(
                "document_id_mismatch",
                "critical",
                "DocumentMap documentId does not match the current document.",
                object_id=normalized.map_id,
            )
        )
    if document_version_id and normalized.document_version_id != document_version_id:
        issues.append(
            _issue(
                "document_version_mismatch",
                "critical",
                "DocumentMap documentVersionId does not match the current document.",
                object_id=normalized.map_id,
            )
        )

    all_map_sections = [*normalized.sections, *normalized.ignored_sections]
    seen_map_ids: set[str] = set()
    covered_segment_ids: set[str] = set()
    for item in all_map_sections:
        if item.map_id in seen_map_ids:
            issues.append(
                _issue(
                    "duplicate_map_id",
                    "critical",
                    f"Duplicate mapId: {item.map_id}.",
                    object_id=item.map_id,
                )
            )
        seen_map_ids.add(item.map_id)

        source_ids = _section_source_ids(item)
        if require_active_source and not source_ids:
            issues.append(
                _issue(
                    "missing_source",
                    "critical",
                    "Map section has no section, segment, table or range source.",
                    object_id=item.map_id,
                )
            )

        unknown_sections = set(item.section_ids) - valid_section_ids
        unknown_segments = set(item.source_segment_ids) - valid_segment_ids
        unknown_tables = set(item.table_ids) - valid_table_ids
        for item_range in item.ranges:
            unknown_segments.update(
                set(item_range.segment_ids)
                - valid_segment_ids
            )
            unknown_segments.update(
                value
                for value in (
                    item_range.start_segment_id,
                    item_range.end_segment_id,
                )
                if value and value not in valid_segment_ids
            )
            unknown_sections.update(set(item_range.section_ids) - valid_section_ids)
            unknown_tables.update(set(item_range.table_ids) - valid_table_ids)
            unknown_sections.update(
                value
                for value in item_range.source.section_ids
                if value not in valid_section_ids
            )
            unknown_segments.update(
                value
                for value in item_range.source.segment_ids
                if value not in valid_segment_ids
            )
            unknown_tables.update(
                value
                for value in item_range.source.table_ids
                if value not in valid_table_ids
            )
            range_unknown_sections, range_unknown_segments, range_unknown_tables = (
                _unknown_anchor_ids(
                    item_range.source,
                    valid_section_ids=valid_section_ids,
                    valid_segment_ids=valid_segment_ids,
                    valid_table_ids=valid_table_ids,
                )
            )
            unknown_sections.update(range_unknown_sections)
            unknown_segments.update(range_unknown_segments)
            unknown_tables.update(range_unknown_tables)
        unknown_sections.update(
            value
            for value in item.source.section_ids
            if value not in valid_section_ids
        )
        unknown_segments.update(
            value
            for value in item.source.segment_ids
            if value not in valid_segment_ids
        )
        unknown_tables.update(
            value
            for value in item.source.table_ids
            if value not in valid_table_ids
        )
        source_unknown_sections, source_unknown_segments, source_unknown_tables = (
            _unknown_anchor_ids(
                item.source,
                valid_section_ids=valid_section_ids,
                valid_segment_ids=valid_segment_ids,
                valid_table_ids=valid_table_ids,
            )
        )
        unknown_sections.update(source_unknown_sections)
        unknown_segments.update(source_unknown_segments)
        unknown_tables.update(source_unknown_tables)
        if item.template_locator is not None:
            unknown_sections.update(
                set(item.template_locator.section_ids) - valid_section_ids
            )
            unknown_segments.update(
                set(item.template_locator.segment_ids) - valid_segment_ids
            )
            unknown_tables.update(
                set(item.template_locator.table_ids) - valid_table_ids
            )

        if unknown_sections:
            issues.append(
                _issue(
                    "unknown_section_id",
                    "critical",
                    "Map section references unknown section IDs.",
                    object_id=item.map_id,
                    source_ids=unknown_sections,
                )
            )
        if unknown_segments:
            issues.append(
                _issue(
                    "unknown_segment_id",
                    "critical",
                    "Map section references unknown segment IDs.",
                    object_id=item.map_id,
                    source_ids=unknown_segments,
                )
            )
        if unknown_tables:
            issues.append(
                _issue(
                    "unknown_table_id",
                    "critical",
                    "Map section references unknown table IDs.",
                    object_id=item.map_id,
                    source_ids=unknown_tables,
                )
            )

        if item.domain == "contract" and (
            not item.ignored or item.primary_route != "ignored_contract"
        ):
            issues.append(
                _issue(
                    "contract_not_ignored",
                    "critical",
                    "Contract ranges must be ignored_contract and excluded from active processing.",
                    object_id=item.map_id,
                )
            )
        if item.domain != "contract" and item.ignored:
            issues.append(
                _issue(
                    "non_contract_ignored",
                    "critical",
                    "Only contract ranges may be marked ignored.",
                    object_id=item.map_id,
                )
            )
        if item.domain == "template" and item.template_locator is None:
            issues.append(
                _issue(
                    "template_locator_missing",
                    "critical",
                    "Template range is missing its TemplateLocator.",
                    object_id=item.map_id,
                )
            )
        if item.domain == "project_info" and item.primary_route in {
            "technical_processor",
            "qualification_processor",
        }:
            issues.append(
                _issue(
                    "project_info_enterprise_route",
                    "critical",
                    "Project information cannot be routed to enterprise matching.",
                    object_id=item.map_id,
                )
            )
        if item.domain == "rejection" and item.primary_route != "rejection_processor":
            issues.append(
                _issue(
                    "rejection_route_invalid",
                    "critical",
                    "Rejection ranges must use rejection_processor.",
                    object_id=item.map_id,
                )
            )
        if (
            item.domain == "technical"
            and item.role != "container"
            and item.primary_route != "technical_processor"
        ):
            issues.append(
                _issue(
                    "technical_route_invalid",
                    "critical",
                    "Technical leaf ranges must use technical_processor.",
                    object_id=item.map_id,
                )
            )
        if (
            item.domain == "qualification"
            and item.role != "container"
            and item.primary_route != "qualification_processor"
        ):
            issues.append(
                _issue(
                    "qualification_route_invalid",
                    "critical",
                    "Qualification leaf ranges must use qualification_processor.",
                    object_id=item.map_id,
                )
            )
        if item.domain == "rejection" and not item.source_segment_ids and not item.ranges:
            issues.append(
                _issue(
                    "rejection_source_missing",
                    "critical",
                    "A rejection range must reference the matched source context.",
                    object_id=item.map_id,
                )
            )
        covered_segment_ids.update(
            segment_id
            for segment_id in source_ids
            if segment_id in valid_segment_ids
        )

    for candidate in normalized.rejection_candidates:
        candidate_source_ids = _anchor_ids(candidate.source)
        candidate_source_ids.update(
            str(value)
            for value in candidate.context_segment_ids
            if str(value).strip()
        )
        candidate_unknown_sections, candidate_unknown_segments, candidate_unknown_tables = (
            _unknown_anchor_ids(
                candidate.source,
                valid_section_ids=valid_section_ids,
                valid_segment_ids=valid_segment_ids,
                valid_table_ids=valid_table_ids,
            )
        )
        unknown_candidate_segments = {
            value
            for value in candidate.source.segment_ids
            if value not in valid_segment_ids
        }
        unknown_candidate_segments.update(candidate_unknown_segments)
        unknown_candidate_segments.update(
            value
            for value in candidate.context_segment_ids
            if value not in valid_segment_ids
        )
        unknown_candidate_sections = {
            value
            for value in candidate.source.section_ids
            if value not in valid_section_ids
        }
        unknown_candidate_sections.update(candidate_unknown_sections)
        unknown_candidate_tables = {
            value
            for value in candidate.source.table_ids
            if value not in valid_table_ids
        }
        unknown_candidate_tables.update(candidate_unknown_tables)
        if not candidate_source_ids and not _has_anchor_reference(candidate.source):
            issues.append(
                _issue(
                    "rejection_candidate_source_missing",
                    "critical",
                    "Rejection candidate has no source reference.",
                    object_id=candidate.candidate_id,
                )
            )
        if unknown_candidate_segments or unknown_candidate_sections or unknown_candidate_tables:
            issues.append(
                _issue(
                    "rejection_candidate_source_invalid",
                    "critical",
                    "Rejection candidate references an unknown source.",
                    object_id=candidate.candidate_id,
                    source_ids=[
                        *unknown_candidate_segments,
                        *unknown_candidate_sections,
                        *unknown_candidate_tables,
                    ],
                )
            )
        if candidate.is_hard_risk and candidate.status == "rejected":
            issues.append(
                _issue(
                    "hard_rejection_downgraded",
                    "critical",
                    "A hard rejection signal cannot be rejected without manual review.",
                    object_id=candidate.candidate_id,
                )
            )

    computed_coverage = build_document_map_coverage(
        normalized,
        segment_ids=valid_segment_ids,
        sections=section_records,
        high_risk_segment_ids=high_risk_ids,
    )
    if normalized.coverage.covered_segment_ids:
        declared_coverage_ids = set(normalized.coverage.covered_segment_ids)
        computed_coverage_ids = set(computed_coverage.covered_segment_ids)
        if computed_coverage_ids - declared_coverage_ids:
            # Section references are valid source coverage even when an SDK
            # omits the section heading segments from its metadata list.
            normalized = normalized.model_copy(
                update={"coverage": computed_coverage}
            )
    if computed_coverage.high_risk_covered_count < computed_coverage.high_risk_hit_count:
        issues.append(
            _issue(
                "high_risk_coverage_gap",
                "critical",
                "Some high-risk source hits are not covered by the document map.",
                object_id=normalized.map_id,
                source_ids=(
                    set(str(value) for value in high_risk_ids)
                    & set(computed_coverage.unclassified_segment_ids)
                ),
            )
        )
    if normalized.coverage.covered_segment_ids:
        missing_from_declared = set(computed_coverage.covered_segment_ids) - set(
            normalized.coverage.covered_segment_ids
        )
        if missing_from_declared:
            issues.append(
                _issue(
                    "coverage_mismatch",
                    "warning",
                    "Declared coverage is smaller than the references in the map.",
                    object_id=normalized.map_id,
                    source_ids=missing_from_declared,
                )
            )

    # Coverage is derived from validated source references, not trusted from
    # the SDK payload.  Always expose the same computed coverage to downstream
    # projections, including when the agent omitted the coverage object.
    if normalized.coverage != computed_coverage:
        normalized = normalized.model_copy(update={"coverage": computed_coverage})

    warnings = [
        item.message
        for item in issues
        if item.severity == "warning"
    ]
    has_critical = any(item.severity == "critical" for item in issues)
    return DocumentMapValidation(
        valid=not has_critical,
        normalizedMap=normalized,
        issues=issues,
        warnings=warnings,
        coverage=computed_coverage.model_dump(mode="json", by_alias=True),
    )


def assert_valid_document_map(
    document_map: DocumentMap | Mapping[str, Any],
    **kwargs: Any,
) -> DocumentMap:
    result = validate_document_map(document_map, **kwargs)
    if not result.valid:
        details = "; ".join(
            f"{item.code}: {item.message}"
            for item in result.issues
            if item.severity == "critical"
        )
        raise ValueError(details or "document map validation failed")
    return result.normalized_map
