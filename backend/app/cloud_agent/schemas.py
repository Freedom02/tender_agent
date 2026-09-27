from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


RunStatus = Literal[
    "created",
    "uploading",
    "uploaded",
    "parsing",
    "outline_ready",
    "waiting_outline_confirmation",
    "generating_sections",
    "reviewing",
    "waiting_export_confirmation",
    "completed",
    "failed",
    "cancelled",
]


class ProjectMeta(BaseModel):
    model_config = ConfigDict(extra="allow")

    projectName: str = ""
    projectCode: str = ""
    tendererName: str = ""
    agencyName: str = ""
    budget: str = ""
    deadline: str = ""


class OutlineItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    sectionId: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: str = "technical"
    source: str = "generated"
    requirementIds: list[str] = Field(default_factory=list)


class RequirementItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = Field(min_length=1)
    category: str = ""
    requirement: str = Field(min_length=1)
    mandatory: bool = False
    sourceLocator: str = ""
    status: str = "待核验"


class ParseResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    projectMeta: ProjectMeta = Field(default_factory=ProjectMeta)
    outline: list[OutlineItem] = Field(default_factory=list)
    requirements: list[RequirementItem] = Field(default_factory=list)
    evaluationItems: list[dict[str, Any]] = Field(default_factory=list)
    riskItems: list[dict[str, Any]] = Field(default_factory=list)
    documentBlocks: dict[str, Any] = Field(default_factory=dict)
    documentStructure: dict[str, Any] = Field(default_factory=dict)
    bidFormatSpec: dict[str, Any] = Field(default_factory=dict)
    templateFragments: list[dict[str, Any]] = Field(default_factory=list)
    templateFragmentsArtifact: str = ""
    parseReviewReport: dict[str, Any] = Field(default_factory=dict)
    agentResults: list[dict[str, Any]] = Field(default_factory=list)
    provider: str = "unknown"
    warnings: list[str] = Field(default_factory=list)


class SectionDraftResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    sectionId: str = ""
    title: str = ""
    content: str = ""
    sourceRefs: list[dict[str, Any]] = Field(default_factory=list)
    materialMatches: list[dict[str, Any]] = Field(default_factory=list)
    unmatchedMaterialRequirementIds: list[str] = Field(default_factory=list)
    writingPlan: dict[str, Any] = Field(default_factory=dict)
    agentResults: list[dict[str, Any]] = Field(default_factory=list)
    provider: str = "unknown"
    phase: str = "section_draft"
    status: str = "generated"
    warnings: list[str] = Field(default_factory=list)


class BidRun(BaseModel):
    model_config = ConfigDict(extra="allow")

    runId: str
    workspaceId: str = ""
    projectId: str = ""
    provider: str = "unknown"
    status: RunStatus = "created"
    phase: str = "parse"
    progress: int = Field(default=0, ge=0, le=100)
    currentAgent: str = ""
    currentAgentStatus: str = ""
    createdAt: str
    updatedAt: str
    difyTaskId: str = ""
    difyWorkflowRunId: str = ""
    workflowType: str = "parse"
    workflowVersion: str = ""
    model: str = ""
    parseBundleId: str = ""
    parseBundleArtifact: str = ""
    inputArtifact: str = ""
    outputArtifact: str = ""
    originalFilename: str = ""
    error: str | None = None
    result: ParseResult | None = None
