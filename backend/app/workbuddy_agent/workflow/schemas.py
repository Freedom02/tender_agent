"""Versioned contracts for the autonomous tender-review workflow.

The models in this module describe observable agent state and handoff data.
They intentionally do not contain hidden model reasoning or raw tool logs.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


AGENT_RUN_SCHEMA_VERSION = "tender-agent-run/v1"
AGENT_TOOL_POLICY_VERSION = "tender-review-tools/v1"
AGENT_SKILL_VERSION = "tender-review/v1"
WRITING_BRIEF_SCHEMA_VERSION = "writing-brief/v1"
AGENT_OUTPUT_CONTRACT_VERSION = "agent-review/v1"


class AgentContract(BaseModel):
    """Shared strict configuration for persisted agent contracts."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


AgentRunStatus = Literal[
    "planning",
    "running",
    "validating",
    "completed",
    "failed",
    "needs_review",
]
AgentPhase = Literal[
    "document_map",
    "section_review",
    "requirement_extraction",
    "matching",
    "validation",
    "handoff",
]
ToolCallStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "skipped",
    "denied",
]
ToolErrorCode = Literal[
    "",
    "invalid_arguments",
    "not_found",
    "permission_denied",
    "source_invalid",
    "timeout",
    "rate_limited",
    "upstream_unavailable",
    "validation_failed",
    "internal_error",
]
ReviewStatus = Literal[
    "not_required",
    "pending",
    "confirmed",
    "rejected",
]
FeedbackObjectType = Literal[
    "section",
    "requirement",
    "match",
    "template",
    "source",
    "writing_brief",
    "other",
]
FeedbackScope = Literal["case", "tenant", "global"]


class AgentCheckpoint(AgentContract):
    checkpoint_id: str = Field(..., alias="checkpointId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    phase: AgentPhase
    artifact_ids: list[str] = Field(default_factory=list, alias="artifactIds")
    summary: str = ""
    created_at: str = Field("", alias="createdAt")


class AgentRun(AgentContract):
    """Observable state for one autonomous review run."""

    run_id: str = Field(..., alias="runId", min_length=1)
    document_id: str = Field(..., alias="documentId", min_length=1)
    document_version_id: str = Field(..., alias="documentVersionId", min_length=1)
    tenant_id: str = Field("", alias="tenantId")
    project_id: str = Field("", alias="projectId")
    status: AgentRunStatus = "planning"
    phase: AgentPhase = "document_map"
    model: str = ""
    agent_runtime_version: str = Field("", alias="agentRuntimeVersion")
    skill_version: str = Field(AGENT_SKILL_VERSION, alias="skillVersion")
    taxonomy_version: str = Field("classification/v1", alias="taxonomyVersion")
    tool_policy_version: str = Field(
        AGENT_TOOL_POLICY_VERSION,
        alias="toolPolicyVersion",
    )
    output_contract_version: str = Field(
        AGENT_OUTPUT_CONTRACT_VERSION,
        alias="outputContractVersion",
    )
    started_at: str = Field("", alias="startedAt")
    updated_at: str = Field("", alias="updatedAt")
    finished_at: str = Field("", alias="finishedAt")
    turn_count: int = Field(0, alias="turnCount", ge=0)
    tool_call_count: int = Field(0, alias="toolCallCount", ge=0)
    max_turns: int = Field(40, alias="maxTurns", ge=1)
    max_tool_calls: int = Field(80, alias="maxToolCalls", ge=1)
    checkpoint_id: str = Field("", alias="checkpointId")
    checkpoints: list[AgentCheckpoint] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    failure_reason: str = Field("", alias="failureReason")
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(AgentContract):
    """Auditable summary of one domain-tool invocation."""

    call_id: str = Field(..., alias="callId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    tool_name: str = Field(..., alias="toolName", min_length=1)
    status: ToolCallStatus = "pending"
    parameters_summary: dict[str, Any] = Field(
        default_factory=dict,
        alias="parametersSummary",
    )
    result_summary: dict[str, Any] = Field(
        default_factory=dict,
        alias="resultSummary",
    )
    duration_ms: int = Field(0, alias="durationMs", ge=0)
    retry_count: int = Field(0, alias="retryCount", ge=0)
    error_code: ToolErrorCode = Field("", alias="errorCode")
    error_message: str = Field("", alias="errorMessage")
    source_ids: list[str] = Field(default_factory=list, alias="sourceIds")
    started_at: str = Field("", alias="startedAt")
    finished_at: str = Field("", alias="finishedAt")


class SectionOverlay(AgentContract):
    """Agent judgment layered over the parser-produced section tree."""

    section_id: str = Field(..., alias="sectionId", min_length=1)
    document_id: str = Field("", alias="documentId")
    run_id: str = Field("", alias="runId")
    heading: str = ""
    section_path: list[str] = Field(default_factory=list, alias="sectionPath")
    source_segment_ids: list[str] = Field(
        default_factory=list,
        alias="sourceSegmentIds",
    )
    primary_type: str = Field("other", alias="primaryType", min_length=1)
    type_hits: list[str] = Field(default_factory=list, alias="typeHits")
    primary_route: str = Field("display_only", alias="primaryRoute", min_length=1)
    secondary_routes: list[str] = Field(
        default_factory=list,
        alias="secondaryRoutes",
    )
    priority: Literal["high", "medium", "low"] = "medium"
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    needs_human_review: bool = Field(False, alias="needsHumanReview")
    review_status: ReviewStatus = Field("not_required", alias="reviewStatus")
    reason: str = ""


class FeedbackEvent(AgentContract):
    """Human correction that may later enter the controlled learning queue."""

    feedback_id: str = Field(..., alias="feedbackId", min_length=1)
    run_id: str = Field(..., alias="runId", min_length=1)
    document_id: str = Field(..., alias="documentId", min_length=1)
    object_type: FeedbackObjectType = Field(..., alias="objectType")
    object_id: str = Field(..., alias="objectId", min_length=1)
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    reviewer: str = ""
    reviewed_at: str = Field("", alias="reviewedAt")
    scope: FeedbackScope = "case"
    approved_for_learning: bool = Field(False, alias="approvedForLearning")
    metadata: dict[str, Any] = Field(default_factory=dict)


class WritingBrief(AgentContract):
    """Stable handoff from review to a later writing agent.

    The nested business projections stay deliberately flexible because the
    analysis pack already owns their detailed schemas.  The outer contract
    fixes the handoff vocabulary and prevents accidental leakage of run logs or
    hidden reasoning into the writing workflow.
    """

    schema_version: str = Field(
        WRITING_BRIEF_SCHEMA_VERSION,
        alias="schemaVersion",
    )
    brief_id: str = Field(..., alias="briefId", min_length=1)
    project_id: str = Field("", alias="projectId")
    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")
    source_file_id: str = Field("", alias="sourceFileId")
    source_file_hash: str = Field("", alias="sourceFileHash")
    source: dict[str, Any] = Field(default_factory=dict)
    project: dict[str, Any] = Field(default_factory=dict)
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    evaluation_criteria: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="evaluationCriteria",
    )
    submission_items: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="submissionItems",
    )
    templates: list[dict[str, Any]] = Field(default_factory=list)
    enterprise_evidence: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="enterpriseEvidence",
    )
    response_plan: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="responsePlan",
    )
    format_constraints: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="formatConstraints",
    )
    rejection_constraints: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="rejectionConstraints",
    )
    timeline_constraints: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="timelineConstraints",
    )
    unresolved_items: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="unresolvedItems",
    )
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
