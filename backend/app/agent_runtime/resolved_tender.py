"""Versioned, source-addressable inputs for bid writing."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RESOLVED_TENDER_V2 = "resolved-tender/2.0"
Actor = Literal["bidder", "purchaser", "agency", "evaluator", "background", "unknown"]
Applicability = Literal["required", "conditional", "optional", "not_applicable", "unknown"]
ResponseMode = Literal[
    "chapter_text", "response_table", "fixed_template", "evidence_attachment",
    "pricing_document", "manual_confirmation", "no_response",
]
RequirementCategory = Literal[
    "qualification", "compliance", "technical_parameter", "commercial_condition",
    "pricing", "format_form", "evidence", "delivery", "training", "acceptance",
    "service", "contract", "subjective_scoring", "objective_scoring", "price_policy", "procurement_process",
    "review_process", "background", "unknown",
]
EvaluationRuleType = Literal[
    "subjective_scoring", "objective_scoring", "price_scoring", "qualification_gate", "compliance_gate", "price_rule",
    "internal_review", "unknown",
]


class TenderContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_default=True, revalidate_instances="always")


class SourceTrace(TenderContract):
    segmentId: str = ""
    fileId: str = ""
    documentVersionId: str = ""
    sourceLocator: str = ""
    quote: str = Field(min_length=1)
    page: int | None = Field(default=None, ge=1)
    paragraphIndex: int | None = Field(default=None, ge=0)
    bodyChildIndex: int | None = Field(default=None, ge=0)
    tableId: str = ""
    rowIndex: int | None = Field(default=None, ge=0)
    cellIndex: int | None = Field(default=None, ge=0)


class ParameterConstraint(TenderContract):
    name: str = Field(min_length=1)
    operator: Literal[">=", ">", "<=", "<", "=", "between", "includes", "supports"]
    value: str = Field(min_length=1)
    upperValue: str | None = None
    unit: str = ""
    sourceTrace: list[SourceTrace] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_interval(self) -> ParameterConstraint:
        if self.operator == "between" and not self.upperValue:
            raise ValueError("between requires upperValue")
        return self


class WritingRequirement(TenderContract):
    requirementId: str = Field(min_length=1)
    text: str = Field(min_length=1)
    actor: Actor = "unknown"
    responseRequired: bool | None = None
    responseStage: Literal["bid_submission", "evaluation_clarification", "contract_performance", "not_applicable", "unknown"] = "unknown"
    applicability: Applicability = "unknown"
    condition: str = ""
    category: RequirementCategory = "unknown"
    responseMode: list[ResponseMode] = Field(default_factory=list)
    mandatory: bool | None = None
    isRejection: bool | None = None
    procurementPackageId: str = ""
    deviationPolicy: Literal["no_negative", "allowed", "not_applicable", "unknown"] = "unknown"
    parameters: list[ParameterConstraint] = Field(default_factory=list)
    evidenceRequirementIds: list[str] = Field(default_factory=list)
    formatItemIds: list[str] = Field(default_factory=list)
    templateFragmentIds: list[str] = Field(default_factory=list)
    sourceTrace: list[SourceTrace] = Field(default_factory=list)
    parseConfidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def check_obligation(self) -> WritingRequirement:
        if len(self.responseMode) != len(set(self.responseMode)):
            raise ValueError("responseMode must not contain duplicates")
        if self.responseRequired is True:
            if self.actor != "bidder" or self.applicability not in {"required", "conditional", "optional"}:
                raise ValueError("responseRequired requires a known bidder obligation")
            if not self.responseMode or "no_response" in self.responseMode:
                raise ValueError("bidder obligations require actionable responseMode")
            if self.responseStage == "not_applicable":
                raise ValueError("response duties require an applicable response stage")
        if self.responseRequired is False and self.responseMode != ["no_response"]:
            raise ValueError("non-response items must use no_response only")
        if self.applicability == "conditional" and not self.condition.strip():
            raise ValueError("conditional obligations require condition")
        if self.actor not in {"bidder", "unknown"} and self.responseRequired is not False:
            raise ValueError("non-bidder items must explicitly require no response")
        if self.responseRequired is None and "no_response" in self.responseMode:
            raise ValueError("unknown obligations cannot be marked no_response")
        if self.applicability == "not_applicable" and self.responseRequired is not False:
            raise ValueError("not_applicable items cannot require a response")
        return self


class EvidenceRequirement(TenderContract):
    evidenceRequirementId: str = Field(min_length=1)
    requirementId: str = Field(min_length=1)
    type: Literal[
        "screenshot", "certificate", "test_report", "commitment", "license",
        "financial_report", "contract", "identity", "course_resource", "other", "unknown",
    ] = "unknown"
    description: str = Field(min_length=1)
    required: bool | None = None
    applicability: Applicability = "unknown"
    condition: str = ""
    minimumQuantity: int | None = Field(default=None, ge=1)
    verificationRules: list[str] = Field(default_factory=list)
    acquisition: Literal["company_library", "human_upload", "either", "unknown"] = "unknown"
    alternativeGroupId: str = ""
    sourceTrace: list[SourceTrace] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_condition(self) -> EvidenceRequirement:
        if self.applicability == "conditional" and not self.condition.strip():
            raise ValueError("conditional evidence requires condition")
        return self


class ScoreDefinition(TenderContract):
    value: float = Field(ge=0)
    sourceTrace: list[SourceTrace] = Field(min_length=1)


class EvaluationRule(TenderContract):
    evaluationRuleId: str = Field(min_length=1)
    title: str = Field(min_length=1)
    text: str = Field(min_length=1)
    ruleType: EvaluationRuleType = "unknown"
    procurementPackageId: str = ""
    requirementIds: list[str] = Field(default_factory=list)
    maxScore: ScoreDefinition | None = None
    parameters: list[ParameterConstraint] = Field(default_factory=list)
    sourceTrace: list[SourceTrace] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_score(self) -> EvaluationRule:
        scoring_types = {"subjective_scoring", "objective_scoring", "price_scoring"}
        if self.maxScore is not None and self.ruleType not in scoring_types:
            raise ValueError("only explicit scoring rules may carry maxScore")
        if self.ruleType in scoring_types and self.maxScore is None:
            raise ValueError("scoring rules require an explicit sourced maxScore")
        return self


class ResponseColumn(TenderContract):
    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    writable: bool = False


class ResponseRowBinding(TenderContract):
    requirementId: str = Field(min_length=1)
    targetRowIndex: int | None = Field(default=None, ge=1)
    sourceTrace: list[SourceTrace] = Field(default_factory=list)


class ResponseTableSchema(TenderContract):
    responseTableId: str = Field(min_length=1)
    title: str = Field(min_length=1)
    templateFragmentId: str = ""
    sourceTableId: str = Field(min_length=1)
    domain: Literal["technical", "commercial", "pricing", "unknown"] = "unknown"
    columns: list[ResponseColumn] = Field(min_length=1)
    rowBindings: list[ResponseRowBinding] = Field(default_factory=list)
    preserveOriginalFormat: bool = True
    sourceTrace: list[SourceTrace] = Field(default_factory=list)


class ResolvedTenderV2(TenderContract):
    schemaVersion: Literal["resolved-tender/2.0"] = RESOLVED_TENDER_V2
    parseBundleId: str = Field(min_length=1)
    projectId: str = ""
    workspaceId: str = ""
    sourceFile: dict[str, Any] = Field(default_factory=dict)
    projectMeta: dict[str, Any] = Field(default_factory=dict)
    documentBlocks: dict[str, Any] = Field(default_factory=dict)
    submissionStructure: dict[str, Any] = Field(default_factory=dict)
    templateFragments: list[dict[str, Any]] = Field(default_factory=list)
    templateFragmentsArtifact: str = ""
    requirements: list[WritingRequirement] = Field(default_factory=list)
    evidenceRequirements: list[EvidenceRequirement] = Field(default_factory=list)
    evaluationMethod: Literal["lowest_evaluated_price", "comprehensive_scoring", "unknown"] = "unknown"
    evaluationMethodSourceTrace: list[SourceTrace] = Field(default_factory=list)
    evaluationRules: list[EvaluationRule] = Field(default_factory=list)
    responseTableSchemas: list[ResponseTableSchema] = Field(default_factory=list)
    company: dict[str, Any] = Field(default_factory=dict)
    organization: dict[str, Any] = Field(default_factory=dict)
    knowledge: dict[str, Any] = Field(default_factory=dict)
    risk: dict[str, Any] = Field(default_factory=dict)
    legacy: dict[str, Any] = Field(default_factory=dict)
    migrationWarnings: list[str] = Field(default_factory=list)
    origin: Literal["native", "legacy_migrated"] = "native"

    @model_validator(mode="after")
    def check_references(self) -> ResolvedTenderV2:
        groups = (
            ("requirementId", self.requirements),
            ("evidenceRequirementId", self.evidenceRequirements),
            ("evaluationRuleId", self.evaluationRules),
            ("responseTableId", self.responseTableSchemas),
        )
        for field, items in groups:
            ids = [getattr(item, field) for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {field}")
        requirements = {item.requirementId: item for item in self.requirements}
        evidence = {item.evidenceRequirementId: item for item in self.evidenceRequirements}
        for item in self.evidenceRequirements:
            parent = requirements.get(item.requirementId)
            if parent is None or item.evidenceRequirementId not in parent.evidenceRequirementIds:
                raise ValueError("evidence must have a reciprocal requirement reference")
        for item in self.requirements:
            for evidence_id in item.evidenceRequirementIds:
                if evidence_id not in evidence or evidence[evidence_id].requirementId != item.requirementId:
                    raise ValueError("invalid evidenceRequirementIds reference")
        for rule in self.evaluationRules:
            if any(item_id not in requirements for item_id in rule.requirementIds):
                raise ValueError("evaluation rule refers to an unknown requirement")
            if self.evaluationMethod == "lowest_evaluated_price" and rule.maxScore is not None:
                raise ValueError("lowest_evaluated_price cannot have scoring rules")
        fragments = {str(item.get("templateId") or item.get("templateFragmentId") or item.get("id") or "") for item in self.templateFragments}
        format_ids: set[str] = set()
        pending = list(self.submissionStructure.get("volumes") or [])
        while pending:
            entry = pending.pop()
            if isinstance(entry, dict):
                if entry.get("formatItemId"):
                    format_ids.add(str(entry["formatItemId"]))
                pending.extend(entry.get("items") or [])
                pending.extend(entry.get("children") or [])
        for requirement in self.requirements:
            if any(item_id not in fragments for item_id in requirement.templateFragmentIds):
                raise ValueError("requirement refers to an unknown template fragment")
            if any(item_id not in format_ids for item_id in requirement.formatItemIds):
                raise ValueError("requirement refers to an unknown submission format item")
        for table in self.responseTableSchemas:
            if table.templateFragmentId and table.templateFragmentId not in fragments:
                raise ValueError("response table refers to an unknown template fragment")
            column_keys = [column.key for column in table.columns]
            if len(column_keys) != len(set(column_keys)):
                raise ValueError("duplicate response table column key")
            bound_ids = [binding.requirementId for binding in table.rowBindings]
            if len(bound_ids) != len(set(bound_ids)):
                raise ValueError("duplicate requirement binding within a response table")
            for binding in table.rowBindings:
                requirement = requirements.get(binding.requirementId)
                if requirement is None or "response_table" not in requirement.responseMode:
                    raise ValueError("table row must refer to a response_table requirement")
        return self


def response_obligations(tender: ResolvedTenderV2) -> list[WritingRequirement]:
    """Return confirmed submission duties; conditional duties still need applicability checks."""
    return [item for item in tender.requirements if item.responseRequired is True and item.responseStage == "bid_submission"]
