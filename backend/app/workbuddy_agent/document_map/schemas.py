"""Strict contracts for the document-map-first tender review workflow."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


DOCUMENT_MAP_SCHEMA_VERSION = "tender-document-map/v1"
CANDIDATE_FINDING_SCHEMA_VERSION = "tender-candidate-finding/v1"

DOCUMENT_MAP_DOMAINS = (
    "technical",
    "qualification",
    "commercial",
    "evaluation",
    "contract",
    "template",
    "project_info",
    "rejection",
)

DOCUMENT_MAP_ROUTES = (
    "technical_processor",
    "qualification_processor",
    "display_processor",
    "evaluation_processor",
    "template_processor",
    "rejection_processor",
    "ignored_contract",
)

ISSUE_TYPES = (
    "classification_ambiguity",
    "template_boundary",
    "contract_boundary",
    "missing_source",
    "conflicting_clause",
    "rejection_context",
    "evaluation_interpretation",
    "project_field_conflict",
    "format_requirement",
    "other",
)

RETENTION_SCOPES = (
    "run_only",
    "project_candidate",
    "tenant_candidate",
    "skill_candidate",
)


class DocumentMapContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


MapDomain = Literal[
    "technical",
    "qualification",
    "commercial",
    "evaluation",
    "contract",
    "template",
    "project_info",
    "rejection",
]
MapRoute = Literal[
    "technical_processor",
    "qualification_processor",
    "display_processor",
    "evaluation_processor",
    "template_processor",
    "rejection_processor",
    "ignored_contract",
]
MapPriority = Literal["high", "medium", "low"]
MapRole = Literal["container", "leaf", "overlay"]
ProcessingMode = Literal[
    "display",
    "fine_extract",
    "template_extract",
    "full_scan",
    "ignore",
]
IssueStatus = Literal["open", "needs_user", "answered", "resolved", "dismissed"]
CandidateFindingStatus = Literal["pending_review", "accepted", "rejected", "promoted"]
RetentionScope = Literal[
    "run_only",
    "project_candidate",
    "tenant_candidate",
    "skill_candidate",
]
RejectionStatus = Literal["candidate", "confirmed", "rejected", "needs_review"]
ValidationSeverity = Literal["critical", "warning", "info"]


class DocumentSourceAnchor(DocumentMapContract):
    """Format-neutral source coordinates that can be resolved to a document."""

    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")
    source_file_id: str = Field("", alias="sourceFileId")
    source_format: Literal["docx", "pdf", "xlsx", "text", "doc", "unknown"] = Field(
        "unknown",
        alias="sourceFormat",
    )
    section_id: str = Field("", alias="sectionId")
    section_ids: list[str] = Field(default_factory=list, alias="sectionIds")
    segment_id: str = Field("", alias="segmentId")
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    table_id: str = Field("", alias="tableId")
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")
    source_locator: str = Field("", alias="sourceLocator")
    page_start: int | None = Field(default=None, alias="pageStart", ge=1)
    page_end: int | None = Field(default=None, alias="pageEnd", ge=1)
    paragraph_start: int | None = Field(default=None, alias="paragraphStart", ge=1)
    paragraph_end: int | None = Field(default=None, alias="paragraphEnd", ge=1)
    line_start: int | None = Field(default=None, alias="lineStart", ge=1)
    line_end: int | None = Field(default=None, alias="lineEnd", ge=1)
    row_start: int | None = Field(default=None, alias="rowStart", ge=1)
    row_end: int | None = Field(default=None, alias="rowEnd", ge=1)
    cell_index: int | None = Field(default=None, alias="cellIndex", ge=1)
    part: str = ""


class DocumentRange(DocumentMapContract):
    range_id: str = Field(..., alias="rangeId", min_length=1)
    start_segment_id: str = Field("", alias="startSegmentId")
    end_segment_id: str = Field("", alias="endSegmentId")
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    section_ids: list[str] = Field(default_factory=list, alias="sectionIds")
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")
    source: DocumentSourceAnchor = Field(default_factory=DocumentSourceAnchor)
    content_summary: str = Field("", alias="contentSummary")

    @model_validator(mode="after")
    def require_range_reference(self) -> "DocumentRange":
        if not (
            self.start_segment_id
            or self.end_segment_id
            or self.segment_ids
            or self.section_ids
            or self.table_ids
            or self.source.segment_ids
            or self.source.section_ids
        ):
            raise ValueError("document range must contain a source reference")
        return self


class TemplateLocator(DocumentMapContract):
    template_id: str = Field(..., alias="templateId", min_length=1)
    template_type: str = Field("other", alias="templateType")
    title: str = ""
    source_file_id: str = Field("", alias="sourceFileId")
    source_format: Literal["docx", "pdf", "xlsx", "text", "doc", "unknown"] = Field(
        "unknown",
        alias="sourceFormat",
    )
    section_ids: list[str] = Field(default_factory=list, alias="sectionIds")
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")
    source_locator: str = Field("", alias="sourceLocator")
    extract_mode: Literal["on_demand", "text", "table_structure", "high_fidelity"] = Field(
        "on_demand",
        alias="extractMode",
    )
    downloadable: bool = True
    writing_agent_usable: bool = Field(True, alias="writingAgentUsable")
    fidelity_level: Literal[
        "locator_only",
        "text",
        "text_and_table_structure",
        "high_fidelity",
    ] = Field("locator_only", alias="fidelityLevel")
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_template_reference(self) -> "TemplateLocator":
        if not (self.section_ids or self.segment_ids or self.table_ids or self.source_locator):
            raise ValueError("template locator must contain a source reference")
        return self


class DocumentMapSection(DocumentMapContract):
    map_id: str = Field(..., alias="mapId", min_length=1)
    run_id: str = Field("", alias="runId")
    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")
    domain: MapDomain
    secondary_domains: list[MapDomain] = Field(
        default_factory=list,
        alias="secondaryDomains",
    )
    role: MapRole = "leaf"
    processing_mode: ProcessingMode = Field("display", alias="processingMode")
    owner_section_id: str = Field("", alias="ownerSectionId")
    context_section_ids: list[str] = Field(
        default_factory=list,
        alias="contextSectionIds",
    )
    title: str = ""
    description: str = ""
    section_ids: list[str] = Field(default_factory=list, alias="sectionIds")
    source_segment_ids: list[str] = Field(
        default_factory=list,
        alias="sourceSegmentIds",
    )
    ranges: list[DocumentRange] = Field(default_factory=list)
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")
    primary_route: MapRoute = Field("display_processor", alias="primaryRoute")
    priority: MapPriority = "medium"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    needs_human_review: bool = Field(False, alias="needsHumanReview")
    review_status: Literal["not_required", "pending", "confirmed", "rejected"] = Field(
        "not_required",
        alias="reviewStatus",
    )
    ignored: bool = False
    reason: str = ""
    classification_source: Literal["agent", "rule", "manual", "hybrid"] = Field(
        "agent",
        alias="classificationSource",
    )
    source: DocumentSourceAnchor = Field(default_factory=DocumentSourceAnchor)
    template_locator: TemplateLocator | None = Field(
        default=None,
        alias="templateLocator",
    )

    @model_validator(mode="after")
    def validate_route_shape(self) -> "DocumentMapSection":
        if self.domain == "contract":
            if not self.ignored or self.primary_route != "ignored_contract":
                raise ValueError("contract map sections must be ignored_contract")
        elif self.ignored:
            raise ValueError("only contract map sections may be ignored")
        if self.domain == "template" and self.template_locator is None:
            raise ValueError("template map sections require templateLocator")
        if self.domain == "rejection" and self.primary_route != "rejection_processor":
            raise ValueError("rejection map sections must use rejection_processor")
        return self


class RejectionCandidate(DocumentMapContract):
    candidate_id: str = Field(..., alias="candidateId", min_length=1)
    run_id: str = Field("", alias="runId")
    document_id: str = Field("", alias="documentId")
    matched_term: str = Field(..., alias="matchedTerm", min_length=1)
    text: str = ""
    context_text: str = Field("", alias="contextText")
    source: DocumentSourceAnchor = Field(default_factory=DocumentSourceAnchor)
    context_segment_ids: list[str] = Field(
        default_factory=list,
        alias="contextSegmentIds",
    )
    status: RejectionStatus = "candidate"
    is_hard_risk: bool = Field(False, alias="isHardRisk")
    reason: str = ""


class IssueOption(DocumentMapContract):
    value: str = Field(..., min_length=1)
    label: str = Field(..., min_length=1)


class UnresolvedIssue(DocumentMapContract):
    issue_id: str = Field(..., alias="issueId", min_length=1)
    run_id: str = Field("", alias="runId")
    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")
    type: str = Field("other", min_length=1)
    question: str = Field(..., min_length=1)
    why_unclear: str = Field("", alias="whyUnclear")
    options: list[IssueOption] = Field(default_factory=list)
    suggested_action: str = Field("", alias="suggestedAction")
    impact: str = ""
    source_refs: DocumentSourceAnchor = Field(
        default_factory=DocumentSourceAnchor,
        alias="sourceRefs",
    )
    status: IssueStatus = "needs_user"
    retention: RetentionScope = "run_only"
    promote_to_skill_candidate: bool = Field(
        False,
        alias="promoteToSkillCandidate",
    )
    answer: str = ""
    resolution: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class CandidateFinding(DocumentMapContract):
    finding_id: str = Field(..., alias="findingId", min_length=1)
    run_id: str = Field("", alias="runId")
    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")
    summary: str = Field(..., min_length=1)
    why_useful: str = Field("", alias="whyUseful")
    source_refs: DocumentSourceAnchor = Field(
        default_factory=DocumentSourceAnchor,
        alias="sourceRefs",
    )
    retention: RetentionScope = "run_only"
    status: CandidateFindingStatus = "pending_review"
    promote_to_skill_candidate: bool = Field(
        False,
        alias="promoteToSkillCandidate",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentMapCoverage(DocumentMapContract):
    covered_segment_ids: list[str] = Field(
        default_factory=list,
        alias="coveredSegmentIds",
    )
    unclassified_segment_ids: list[str] = Field(
        default_factory=list,
        alias="unclassifiedSegmentIds",
    )
    high_risk_hit_count: int = Field(0, alias="highRiskHitCount", ge=0)
    high_risk_covered_count: int = Field(0, alias="highRiskCoveredCount", ge=0)
    domain_counts: dict[str, int] = Field(
        default_factory=dict,
        alias="domainCounts",
    )


class DocumentMapAgentSummary(DocumentMapContract):
    source: str = "workbuddy-document-map"
    reason: str = ""
    model: str = ""
    skill_version: str = Field("", alias="skillVersion")
    tool_policy_version: str = Field("", alias="toolPolicyVersion")
    turn_count: int = Field(0, alias="turnCount", ge=0)
    tool_call_count: int = Field(0, alias="toolCallCount", ge=0)


class DocumentMap(DocumentMapContract):
    schema_version: str = Field(
        DOCUMENT_MAP_SCHEMA_VERSION,
        alias="schemaVersion",
    )
    map_id: str = Field(..., alias="mapId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    document_id: str = Field(..., alias="documentId", min_length=1)
    document_version_id: str = Field(..., alias="documentVersionId", min_length=1)
    document_type: str = Field("", alias="documentType")
    sections: list[DocumentMapSection] = Field(default_factory=list)
    ignored_sections: list[DocumentMapSection] = Field(
        default_factory=list,
        alias="ignoredSections",
    )
    rejection_candidates: list[RejectionCandidate] = Field(
        default_factory=list,
        alias="rejectionCandidates",
    )
    unresolved_sections: list[str] = Field(
        default_factory=list,
        alias="unresolvedSections",
    )
    unresolved_issues: list[UnresolvedIssue] = Field(
        default_factory=list,
        alias="unresolvedIssues",
    )
    candidate_findings: list[CandidateFinding] = Field(
        default_factory=list,
        alias="candidateFindings",
    )
    coverage: DocumentMapCoverage = Field(default_factory=DocumentMapCoverage)
    warnings: list[str] = Field(default_factory=list)
    agent_summary: DocumentMapAgentSummary = Field(
        default_factory=DocumentMapAgentSummary,
        alias="agentSummary",
    )

    @model_validator(mode="after")
    def validate_document_identity(self) -> "DocumentMap":
        for item in [*self.sections, *self.ignored_sections]:
            if item.document_id and item.document_id != self.document_id:
                raise ValueError(f"map section {item.map_id} belongs to another document")
            if item.document_version_id and item.document_version_id != self.document_version_id:
                raise ValueError(f"map section {item.map_id} belongs to another document version")
        return self
