"""Versioned Pydantic fact contracts for extraction, matching and writing."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .document_models import DocumentChunk, DocumentIR, DocumentSection


FACT_SCHEMA_VERSION = "tender-facts/v1"
ARTIFACT_SCHEMA_VERSION = "tender-analysis-artifact/v1"
RUN_SCHEMA_VERSION = "tender-analysis-run/v1"


class FactModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class EvidenceFact(FactModel):
    evidence_id: str = Field(..., alias="evidenceId", min_length=1)
    document_version_id: str = Field(..., alias="documentVersionId", min_length=1)
    source_type: Literal["tender_document", "response_document", "company_material", "other"] = Field(
        "tender_document",
        alias="sourceType",
    )
    source_segment_ids: list[str] = Field(default_factory=list, alias="sourceSegmentIds")
    section_id: str = Field("", alias="sectionId")
    section_path: list[str] = Field(default_factory=list, alias="sectionPath")
    source_locator: str = Field("", alias="sourceLocator")
    page_start: int | None = Field(default=None, alias="pageStart", ge=1)
    page_end: int | None = Field(default=None, alias="pageEnd", ge=1)
    source_quote: str = Field("", alias="sourceQuote")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    verification_status: Literal["unverified", "verified", "needs_review", "invalid"] = Field(
        "unverified",
        alias="verificationStatus",
    )
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")


class RequirementFact(FactModel):
    requirement_id: str = Field(..., alias="requirementId", min_length=1)
    document_version_id: str = Field(..., alias="documentVersionId", min_length=1)
    section_id: str = Field("", alias="sectionId")
    section_path: list[str] = Field(default_factory=list, alias="sectionPath")
    category: str = "other"
    category_hits: list[str] = Field(default_factory=list, alias="categoryHits")
    content_type: str = Field("other", alias="contentType")
    direct_type_hits: list[str] = Field(default_factory=list, alias="directTypeHits")
    inherited_type: str = Field("other", alias="inheritedType")
    type_hits: list[str] = Field(default_factory=list, alias="typeHits")
    primary_route: str = Field("display_only", alias="primaryRoute")
    secondary_routes: list[str] = Field(default_factory=list, alias="secondaryRoutes")
    match_domain: str = Field("", alias="matchDomain")
    decision_impact: str = Field("normal", alias="decisionImpact")
    granularity: str = Field("clause", alias="granularity")
    route_tags: list[str] = Field(default_factory=list, alias="routeTags")
    sub_category: str = Field("", alias="subCategory")
    raw_text: str = Field("", alias="rawText")
    normalized_requirement: str = Field("", alias="normalizedRequirement")
    mandatory: bool = True
    is_rejection: bool = Field(False, alias="isRejection")
    importance: Literal["high", "medium", "low"] = "medium"
    requirement_class: str = Field("", alias="requirementClass")
    response_type: str = Field("", alias="responseType")
    evidence_need: str = Field("", alias="evidenceNeed")
    source_segment_ids: list[str] = Field(default_factory=list, alias="sourceSegmentIds")
    evidence_ids: list[str] = Field(default_factory=list, alias="evidenceIds")
    source_quote: str = Field("", alias="sourceQuote")
    extraction_method: Literal["rule", "llm", "hybrid", "manual"] = Field(
        "rule",
        alias="extractionMethod",
    )
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    needs_human_review: bool = Field(False, alias="needsHumanReview")
    review_required: bool = Field(False, alias="reviewRequired")
    review_mode: str = Field("none", alias="reviewMode")
    review_reason: str = Field("", alias="reviewReason")
    classification_source: str = Field("rule", alias="classificationSource")
    product_name: str = Field("", alias="productName")
    target_type: str = Field("", alias="targetType")
    source_column: str = Field("", alias="sourceColumn")
    prompt_version: str = Field("", alias="promptVersion")


class FactRecord(FactModel):
    fact_id: str = Field(..., alias="factId", min_length=1)
    fact_type: str = Field(..., alias="factType", min_length=1)
    value: Any = None
    source_evidence_ids: list[str] = Field(default_factory=list, alias="sourceEvidenceIds")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExtractedFieldFact(FactModel):
    field_name: str = Field(..., alias="fieldName", min_length=1)
    value: str = ""
    evidence_id: str = Field("", alias="evidenceId")
    confidence: float = Field(0.0, ge=0.0, le=1.0)


class RequirementExtractionResult(FactModel):
    document_version_id: str = Field(..., alias="documentVersionId", min_length=1)
    project_fields: list[ExtractedFieldFact] = Field(default_factory=list, alias="projectFields")
    requirements: list[RequirementFact] = Field(default_factory=list)
    evidences: list[EvidenceFact] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class AnalysisStage(FactModel):
    """One observable stage in a document analysis run."""

    stage: str = Field(..., min_length=1)
    status: Literal["pending", "running", "completed", "partial", "failed", "skipped"] = "completed"
    started_at: str = Field("", alias="startedAt")
    finished_at: str = Field("", alias="finishedAt")
    elapsed_ms: int = Field(0, alias="elapsedMs", ge=0)
    retry_count: int = Field(0, alias="retryCount", ge=0)
    input_artifact_paths: list[str] = Field(default_factory=list, alias="inputArtifactPaths")
    output_artifact_paths: list[str] = Field(default_factory=list, alias="outputArtifactPaths")
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)


class AnalysisRun(FactModel):
    run_id: str = Field(..., alias="runId", min_length=1)
    project_id: str = Field("", alias="projectId")
    document_version_id: str = Field(..., alias="documentVersionId", min_length=1)
    stage: str = "analysis"
    status: Literal["running", "completed", "partial", "failed"] = "completed"
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(), alias="createdAt")
    started_at: str = Field("", alias="startedAt")
    finished_at: str = Field("", alias="finishedAt")
    elapsed_ms: int = Field(0, alias="elapsedMs", ge=0)
    run_schema_version: str = Field(RUN_SCHEMA_VERSION, alias="runSchemaVersion")
    parser_name: str = Field("", alias="parserName")
    parser_version: str = Field("", alias="parserVersion")
    schema_version: str = Field(FACT_SCHEMA_VERSION, alias="schemaVersion")
    prompt_version: str = Field("", alias="promptVersion")
    llm_model: str = Field("", alias="llmModel")
    llm_parameters: dict[str, Any] = Field(default_factory=dict, alias="llmParameters")
    llm_call_count: int = Field(0, alias="llmCallCount", ge=0)
    llm_success_count: int = Field(0, alias="llmSuccessCount", ge=0)
    llm_failure_count: int = Field(0, alias="llmFailureCount", ge=0)
    llm_input_tokens: int = Field(0, alias="llmInputTokens", ge=0)
    llm_output_tokens: int = Field(0, alias="llmOutputTokens", ge=0)
    manual_change_count: int = Field(0, alias="manualChangeCount", ge=0)
    input_artifact_paths: list[str] = Field(default_factory=list, alias="inputArtifactPaths")
    output_artifact_paths: list[str] = Field(default_factory=list, alias="outputArtifactPaths")
    stages: list[AnalysisStage] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class AnalysisArtifact(FactModel):
    artifact_id: str = Field(..., alias="artifactId", min_length=1)
    project_id: str = Field(..., alias="projectId", min_length=1)
    document_version_id: str = Field(..., alias="documentVersionId", min_length=1)
    schema_version: str = Field(ARTIFACT_SCHEMA_VERSION, alias="schemaVersion")
    source_file_hash: str = Field("", alias="sourceFileHash")
    sections: list[DocumentSection] = Field(default_factory=list)
    chunks: list[DocumentChunk] = Field(default_factory=list)
    requirements: list[RequirementFact] = Field(default_factory=list)
    evidences: list[EvidenceFact] = Field(default_factory=list)
    facts: list[FactRecord] = Field(default_factory=list)
    run: AnalysisRun
    warnings: list[str] = Field(default_factory=list)


def evidence_from_segment(
    document: DocumentIR,
    *,
    evidence_id: str,
    segment_ids: list[str],
    section_id: str = "",
    section_path: list[str] | None = None,
    source_quote: str = "",
    confidence: float = 0.0,
) -> EvidenceFact:
    """Build an evidence record only from source IDs present in DocumentIR."""

    segment_map = {segment.id: segment for segment in document.segments if segment.id}
    valid_ids = list(dict.fromkeys(item for item in segment_ids if item in segment_map))
    if not valid_ids:
        raise ValueError("evidence must reference at least one existing segment")
    segments = [segment_map[item] for item in valid_ids]
    pages = sorted({segment.page for segment in segments if segment.page is not None})
    table_ids = list(dict.fromkeys(segment.table_id for segment in segments if segment.table_id))
    quote = source_quote.strip() or "\n".join(segment.text for segment in segments)
    return EvidenceFact(
        evidenceId=evidence_id,
        documentVersionId=document.document_version_id,
        sourceType="tender_document",
        sourceSegmentIds=valid_ids,
        sectionId=section_id,
        sectionPath=section_path or [],
        sourceLocator=segments[0].source_locator,
        pageStart=pages[0] if pages else None,
        pageEnd=pages[-1] if pages else None,
        sourceQuote=quote[:4000],
        confidence=confidence,
        tableIds=table_ids,
    )


def requirement_from_legacy(
    raw: Mapping[str, Any],
    *,
    document: DocumentIR,
    evidence: EvidenceFact | None = None,
    extraction_method: Literal["rule", "llm", "hybrid", "manual"] = "rule",
) -> RequirementFact:
    """Map a historical requirement dictionary into the strict fact model."""

    source_ids = [
        str(item)
        for item in (raw.get("sourceSegmentIds") or [])
        if str(item)
    ]
    if not source_ids and raw.get("sourceSegmentId"):
        source_ids = [str(raw["sourceSegmentId"])]
    evidence_ids = [evidence.evidence_id] if evidence else [
        str(item) for item in (raw.get("evidenceIds") or []) if str(item)
    ]
    importance = str(raw.get("importance") or "medium")
    if importance not in {"high", "medium", "low"}:
        importance = "medium"
    return RequirementFact(
        requirementId=str(raw.get("requirementId") or raw.get("id") or "REQ-UNNAMED"),
        documentVersionId=document.document_version_id,
        sectionId=str(raw.get("sectionId") or ""),
        sectionPath=[str(item) for item in (raw.get("sectionPath") or []) if str(item)],
        category=str(raw.get("category") or "other"),
        categoryHits=[
            str(item)
            for item in (raw.get("categoryHits") or [])
            if str(item)
        ],
        contentType=str(raw.get("contentType") or raw.get("category") or "other"),
        directTypeHits=[
            str(item)
            for item in (raw.get("directTypeHits") or [])
            if str(item)
        ],
        inheritedType=str(raw.get("inheritedType") or "other"),
        typeHits=[
            str(item)
            for item in (raw.get("typeHits") or raw.get("categoryHits") or [])
            if str(item)
        ],
        primaryRoute=str(raw.get("primaryRoute") or "display_only"),
        secondaryRoutes=[
            str(item)
            for item in (raw.get("secondaryRoutes") or [])
            if str(item)
        ],
        matchDomain=str(raw.get("matchDomain") or ""),
        decisionImpact=str(raw.get("decisionImpact") or "normal"),
        granularity=str(raw.get("granularity") or "clause"),
        routeTags=[
            str(item)
            for item in (raw.get("routeTags") or [])
            if str(item)
        ],
        subCategory=str(raw.get("subCategory") or ""),
        rawText=str(raw.get("rawText") or raw.get("text") or raw.get("requirement") or ""),
        normalizedRequirement=str(
            raw.get("normalizedRequirement")
            or raw.get("rawText")
            or raw.get("text")
            or raw.get("requirement")
            or ""
        ),
        mandatory=bool(raw.get("mandatory", True)),
        isRejection=bool(raw.get("isRejection", False)),
        importance=importance,
        requirementClass=str(raw.get("requirementClass") or ""),
        responseType=str(raw.get("responseType") or ""),
        evidenceNeed=str(raw.get("evidenceNeed") or ""),
        sourceSegmentIds=source_ids,
        evidenceIds=evidence_ids,
        sourceQuote=str(raw.get("sourceQuote") or raw.get("rawText") or ""),
        extractionMethod=extraction_method,
        confidence=float(raw.get("confidence") or 0.0),
        needsHumanReview=bool(raw.get("needsHumanReview", False)),
        reviewRequired=bool(raw.get("reviewRequired", raw.get("needsHumanReview", False))),
        reviewMode=str(raw.get("reviewMode") or "none"),
        reviewReason=str(raw.get("reviewReason") or ""),
        classificationSource=str(raw.get("classificationSource") or extraction_method),
        productName=str(raw.get("productName") or raw.get("itemName") or raw.get("targetName") or ""),
        targetType=str(raw.get("targetType") or ""),
        sourceColumn=str(raw.get("sourceColumn") or ""),
        promptVersion=str(raw.get("promptVersion") or ""),
    )


def build_analysis_artifact(
    *,
    project_id: str,
    document: DocumentIR,
    run_id: str,
    sections: list[DocumentSection] | None = None,
    chunks: list[DocumentChunk] | None = None,
    requirements: list[RequirementFact] | None = None,
    evidences: list[EvidenceFact] | None = None,
    facts: list[FactRecord] | None = None,
    warnings: list[str] | None = None,
) -> AnalysisArtifact:
    artifact_seed = f"{project_id}:{document.document_version_id}:{run_id}"
    artifact_id = f"ART-{hashlib.sha256(artifact_seed.encode()).hexdigest()[:16]}"
    run = AnalysisRun(
        runId=run_id,
        projectId=project_id,
        documentVersionId=document.document_version_id,
        parserName=document.parse_method,
        parserVersion=document.metadata.get("parserVersion", ""),
        warnings=[*document.warnings, *(warnings or [])],
    )
    return AnalysisArtifact(
        artifactId=artifact_id,
        projectId=project_id,
        documentVersionId=document.document_version_id,
        sourceFileHash=document.file_hash,
        sections=sections if sections is not None else document.sections,
        chunks=chunks or [],
        requirements=requirements or [],
        evidences=evidences or [],
        facts=facts or [],
        run=run,
        warnings=warnings or [],
    )


class FactArtifactStore:
    """Filesystem artifact store with strict validation on both write and read."""

    def __init__(self, runtime_root: str | Path):
        self.runtime_root = Path(runtime_root)

    @staticmethod
    def _safe(value: str) -> str:
        token = str(value or "").strip()
        if not token or token in {".", ".."} or "/" in token or "\\" in token:
            raise ValueError("artifact path component is unsafe")
        return token

    def parsed_path(self, project_id: str, document_version_id: str) -> Path:
        return (
            self.runtime_root
            / "projects"
            / self._safe(project_id)
            / "parsed"
            / f"{self._safe(document_version_id)}.json"
        )

    def facts_path(self, project_id: str, document_version_id: str) -> Path:
        return (
            self.runtime_root
            / "projects"
            / self._safe(project_id)
            / "facts"
            / f"{self._safe(document_version_id)}.json"
        )

    def run_path(self, project_id: str, run_id: str) -> Path:
        return (
            self.runtime_root
            / "projects"
            / self._safe(project_id)
            / "runs"
            / f"{self._safe(run_id)}.json"
        )

    def save_document(self, project_id: str, document: DocumentIR) -> Path:
        path = self.parsed_path(project_id, document.document_version_id)
        self._write(path, document.model_dump(mode="json", by_alias=True))
        return path

    def load_document(self, project_id: str, document_version_id: str) -> DocumentIR:
        return DocumentIR.model_validate(self._read(self.parsed_path(project_id, document_version_id)))

    def save_artifact(self, artifact: AnalysisArtifact) -> Path:
        path = self.facts_path(artifact.project_id, artifact.document_version_id)
        self._write(path, artifact.model_dump(mode="json", by_alias=True))
        return path

    def load_artifact(self, project_id: str, document_version_id: str) -> AnalysisArtifact:
        return AnalysisArtifact.model_validate(
            self._read(self.facts_path(project_id, document_version_id))
        )

    def save_run(self, project_id: str, run: AnalysisRun) -> Path:
        path = self.run_path(project_id, run.run_id)
        self._write(path, run.model_dump(mode="json", by_alias=True))
        return path

    def load_run(self, project_id: str, run_id: str) -> AnalysisRun:
        return AnalysisRun.model_validate(self._read(self.run_path(project_id, run_id)))

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise FileNotFoundError(f"artifact not found: {path}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"artifact root must be an object: {path}")
        return value

    @staticmethod
    def _write(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
