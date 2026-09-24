"""Evidence validation before requirements enter matching or writing."""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .document_models import DocumentIR
from .fact_models import (
    EvidenceFact,
    RequirementExtractionResult,
    RequirementFact,
)


class EvidenceIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str = "error"
    message: str
    requirement_id: str = ""
    evidence_id: str = ""


class EvidenceValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool = False
    requirements: list[RequirementFact] = Field(default_factory=list)
    evidences: list[EvidenceFact] = Field(default_factory=list)
    issues: list[EvidenceIssue] = Field(default_factory=list)
    retry_budget: dict[str, int] = Field(default_factory=lambda: {
        "network": 3,
        "json_repair": 2,
        "evidence_relocate": 1,
    })
    stats: dict[str, int] = Field(default_factory=dict)


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip()


def _source_text(document: DocumentIR, segment_ids: list[str]) -> str:
    segment_map = {segment.id: segment for segment in document.segments}
    return "\n".join(segment_map[item].text for item in segment_ids if item in segment_map)


def _validate_evidence(
    evidence: EvidenceFact,
    *,
    document: DocumentIR,
    sections: dict[str, Any],
) -> list[EvidenceIssue]:
    issues: list[EvidenceIssue] = []
    if evidence.document_version_id != document.document_version_id:
        issues.append(EvidenceIssue(
            code="document_version_mismatch",
            message="evidence documentVersionId does not match the source document",
            evidence_id=evidence.evidence_id,
        ))
    segment_map = {segment.id: segment for segment in document.segments if segment.id}
    missing_ids = [item for item in evidence.source_segment_ids if item not in segment_map]
    if missing_ids:
        issues.append(EvidenceIssue(
            code="missing_segment",
            message=f"source segments do not exist: {missing_ids}",
            evidence_id=evidence.evidence_id,
        ))
    valid_segments = [segment_map[item] for item in evidence.source_segment_ids if item in segment_map]
    if not valid_segments:
        return issues
    source_text = _compact(_source_text(document, evidence.source_segment_ids))
    quote = _compact(evidence.source_quote)
    if quote and quote not in source_text:
        issues.append(EvidenceIssue(
            code="quote_not_supported",
            message="sourceQuote was not found in the referenced source segments",
            evidence_id=evidence.evidence_id,
        ))
    if evidence.section_id:
        section = sections.get(evidence.section_id)
        if section is None:
            issues.append(EvidenceIssue(
                code="missing_section",
                message="sectionId does not exist in the source document",
                evidence_id=evidence.evidence_id,
            ))
        elif not set(evidence.source_segment_ids).intersection(section.segment_ids):
            issues.append(EvidenceIssue(
                code="section_segment_mismatch",
                message="evidence segments are outside the referenced section",
                evidence_id=evidence.evidence_id,
            ))
    pages = sorted({segment.page for segment in valid_segments if segment.page is not None})
    if pages and (evidence.page_start != pages[0] or evidence.page_end != pages[-1]):
        issues.append(EvidenceIssue(
            code="page_mismatch",
            message="page range does not match referenced segments",
            evidence_id=evidence.evidence_id,
        ))
    actual_table_ids = {segment.table_id for segment in valid_segments if segment.table_id}
    if not set(evidence.table_ids).issubset(actual_table_ids):
        issues.append(EvidenceIssue(
            code="table_mismatch",
            message="tableIds do not match referenced table segments",
            evidence_id=evidence.evidence_id,
        ))
    return issues


def validate_extraction_result(
    document: DocumentIR,
    result: RequirementExtractionResult,
) -> EvidenceValidationResult:
    """Validate structure, source IDs, quotes, section paths and consistency."""

    issues: list[EvidenceIssue] = []
    if result.document_version_id != document.document_version_id:
        issues.append(EvidenceIssue(
            code="result_document_version_mismatch",
            message="extraction result belongs to another document version",
        ))
    sections = {
        section.section_id or section.id: section
        for section in document.sections
        if section.section_id or section.id
    }
    evidences = {evidence.evidence_id: evidence for evidence in result.evidences}
    evidence_issues: dict[str, list[EvidenceIssue]] = defaultdict(list)
    for evidence in result.evidences:
        current_issues = _validate_evidence(evidence, document=document, sections=sections)
        evidence_issues[evidence.evidence_id].extend(current_issues)
        issues.extend(current_issues)

    validated_requirements: list[RequirementFact] = []
    for requirement in result.requirements:
        requirement_issues = 0
        for evidence_id in requirement.evidence_ids:
            if evidence_id not in evidences:
                requirement_issues += 1
                issues.append(EvidenceIssue(
                    code="missing_evidence",
                    message="requirement references an evidence ID that is absent",
                    requirement_id=requirement.requirement_id,
                    evidence_id=evidence_id,
                ))
            elif evidence_issues.get(evidence_id):
                requirement_issues += 1
                issues.append(EvidenceIssue(
                    code="requirement_invalid_evidence",
                    message="requirement references evidence that failed source validation",
                    requirement_id=requirement.requirement_id,
                    evidence_id=evidence_id,
                ))
        if not requirement.evidence_ids:
            requirement_issues += 1
            issues.append(EvidenceIssue(
                code="requirement_without_evidence",
                severity="warning" if not requirement.mandatory else "error",
                message="requirement has no evidence binding",
                requirement_id=requirement.requirement_id,
            ))
        if requirement.source_segment_ids:
            source_ids = {
                segment.id
                for segment in document.segments
                if segment.id in requirement.source_segment_ids
            }
            if len(source_ids) != len(set(requirement.source_segment_ids)):
                requirement_issues += 1
                issues.append(EvidenceIssue(
                    code="requirement_missing_segment",
                    message="requirement sourceSegmentIds contain unknown IDs",
                    requirement_id=requirement.requirement_id,
                ))
        validated_requirements.append(
            requirement.model_copy(update={
                "needs_human_review": requirement.needs_human_review or requirement_issues > 0,
            })
        )

    # Same normalized requirement text with different source quotes is a
    # review signal, not an automatic merge or deletion.
    grouped: dict[tuple[str, str], list[RequirementFact]] = defaultdict(list)
    for requirement in validated_requirements:
        key = (requirement.category, _compact(requirement.normalized_requirement))
        if key[1]:
            grouped[key].append(requirement)
    for values in grouped.values():
        quotes = {_compact(item.source_quote) for item in values if item.source_quote}
        if len(values) > 1 and len(quotes) > 1:
            for item in values:
                issues.append(EvidenceIssue(
                    code="conflicting_requirement_sources",
                    severity="warning",
                    message="same normalized requirement has different source quotes",
                    requirement_id=item.requirement_id,
                ))

    invalid_evidence_ids = {
        issue.evidence_id
        for issue in issues
        if issue.evidence_id
    }
    validated_evidences = [
        evidence.model_copy(update={
            "verification_status": (
                "invalid"
                if evidence.evidence_id in invalid_evidence_ids
                else "verified"
            ),
        })
        for evidence in result.evidences
    ]
    error_count = sum(1 for issue in issues if issue.severity == "error")
    return EvidenceValidationResult(
        valid=error_count == 0,
        requirements=validated_requirements,
        evidences=validated_evidences,
        issues=issues,
        stats={
            "requirementCount": len(validated_requirements),
            "evidenceCount": len(validated_evidences),
            "errorCount": error_count,
            "warningCount": sum(1 for issue in issues if issue.severity == "warning"),
            "verifiedEvidenceCount": sum(
                1 for evidence in validated_evidences
                if evidence.verification_status == "verified"
            ),
        },
    )
