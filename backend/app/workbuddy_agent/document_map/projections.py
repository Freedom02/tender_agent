"""Stable projections from DocumentMap into existing analysis outputs."""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from .schemas import DocumentMap


def _as_map(document_map: DocumentMap | Mapping[str, Any]) -> DocumentMap:
    return (
        document_map
        if isinstance(document_map, DocumentMap)
        else DocumentMap.model_validate(document_map)
    )


def project_document_map(document_map: DocumentMap | Mapping[str, Any]) -> dict[str, Any]:
    """Return a compact, frontend-safe map projection.

    Full text and tool traces stay outside this projection.  Source IDs and
    concise descriptions are enough for later range reads and audit views.
    """
    normalized = _as_map(document_map)
    all_sections = [*normalized.sections, *normalized.ignored_sections]
    domain_counts = Counter(item.domain for item in all_sections)
    sections = []
    for item in normalized.sections:
        sections.append(
            {
                "mapId": item.map_id,
                "domain": item.domain,
                "secondaryDomains": list(item.secondary_domains),
                "role": item.role,
                "processingMode": item.processing_mode,
                "ownerSectionId": item.owner_section_id,
                "contextSectionIds": list(item.context_section_ids),
                "title": item.title,
                "description": item.description,
                "sectionIds": list(item.section_ids),
                "sourceSegmentIds": list(item.source_segment_ids),
                "ranges": [
                    {
                        "rangeId": item_range.range_id,
                        "startSegmentId": item_range.start_segment_id,
                        "endSegmentId": item_range.end_segment_id,
                        "segmentIds": list(item_range.segment_ids),
                        "sectionIds": list(item_range.section_ids),
                        "tableIds": list(item_range.table_ids),
                        "source": item_range.source.model_dump(mode="json", by_alias=True),
                    }
                    for item_range in item.ranges
                ],
                "tableIds": list(item.table_ids),
                "primaryRoute": item.primary_route,
                "priority": item.priority,
                "confidence": item.confidence,
                "needsHumanReview": item.needs_human_review,
                "reviewStatus": item.review_status,
                "ignored": item.ignored,
                "reason": item.reason,
                "classificationSource": item.classification_source,
                "source": item.source.model_dump(mode="json", by_alias=True),
                "templateLocator": (
                    item.template_locator.model_dump(mode="json", by_alias=True)
                    if item.template_locator
                    else None
                ),
            }
        )
    ignored = [
        {
            "mapId": item.map_id,
            "domain": item.domain,
            "role": item.role,
            "processingMode": item.processing_mode,
            "ownerSectionId": item.owner_section_id,
            "contextSectionIds": list(item.context_section_ids),
            "title": item.title,
            "description": item.description,
            "sectionIds": list(item.section_ids),
            "sourceSegmentIds": list(item.source_segment_ids),
            "source": item.source.model_dump(mode="json", by_alias=True),
            "ignored": True,
            "reason": item.reason,
        }
        for item in normalized.ignored_sections
    ]
    return {
        "schemaVersion": normalized.schema_version,
        "mapId": normalized.map_id,
        "runId": normalized.run_id,
        "documentId": normalized.document_id,
        "documentVersionId": normalized.document_version_id,
        "documentType": normalized.document_type,
        "sections": sections,
        "ignoredSections": ignored,
        "rejectionCandidates": [
            candidate.model_dump(mode="json", by_alias=True)
            for candidate in normalized.rejection_candidates
        ],
        "unresolvedSections": list(normalized.unresolved_sections),
        "unresolvedIssues": [
            issue.model_dump(mode="json", by_alias=True)
            for issue in normalized.unresolved_issues
        ],
        "candidateFindings": [
            finding.model_dump(mode="json", by_alias=True)
            for finding in normalized.candidate_findings
        ],
        "coverage": normalized.coverage.model_dump(mode="json", by_alias=True),
        "warnings": list(normalized.warnings),
        "agentSummary": normalized.agent_summary.model_dump(mode="json", by_alias=True),
        "mapStats": {
            "sectionCount": len(normalized.sections),
            "ignoredSectionCount": len(normalized.ignored_sections),
            "rejectionCandidateCount": len(normalized.rejection_candidates),
            "unresolvedIssueCount": len(normalized.unresolved_issues),
            "candidateFindingCount": len(normalized.candidate_findings),
            "domainCounts": dict(domain_counts),
        },
    }


def attach_document_map_to_analysis_pack(
    analysis_pack: Mapping[str, Any] | None,
    document_map: DocumentMap | Mapping[str, Any],
) -> dict[str, Any]:
    """Add the map projection without changing existing pack fields."""
    result = dict(analysis_pack or {})
    projection = project_document_map(document_map)
    result["documentMap"] = projection
    result["mapStats"] = projection["mapStats"]
    result["unresolvedIssues"] = projection["unresolvedIssues"]
    result["candidateFindings"] = projection["candidateFindings"]
    return result
