"""Read-only diagnostics for writing input readiness, not a production gate."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from .resolved_sources import normalized_quote, source_index
from .resolved_tender import ResolvedTenderV2, SourceTrace, TenderContract


class TenderDiagnostic(TenderContract):
    code: str
    severity: Literal["error", "review", "warning"]
    path: str
    message: str


class TenderReadinessReport(TenderContract):
    schemaVersion: Literal["resolved-tender-audit/1.0"] = "resolved-tender-audit/1.0"
    parseBundleId: str
    readyForWriting: bool = False
    issues: list[TenderDiagnostic] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)


def audit_resolved_tender(data: ResolvedTenderV2 | dict[str, Any]) -> TenderReadinessReport:
    tender = ResolvedTenderV2.model_validate(data)
    issues: list[TenderDiagnostic] = []

    def issue(code: str, severity: Literal["error", "review", "warning"], path: str, message: str) -> None:
        issues.append(TenderDiagnostic(code=code, severity=severity, path=path, message=message))

    try:
        blocks = source_index(tender.documentBlocks)
    except ValueError as exc:
        blocks = {}
        issue("ambiguous_document_blocks", "error", "documentBlocks", str(exc))
    file_id = str(tender.sourceFile.get("fileId") or "")
    version_id = str(tender.sourceFile.get("documentVersionId") or tender.sourceFile.get("sha256") or "")
    if not file_id or not version_id:
        issue("source_identity_incomplete", "review", "sourceFile", "Source file ID and immutable document version are required")

    def verify_traces(traces: list[SourceTrace], path: str) -> None:
        if not traces:
            issue("source_trace_missing", "review", path, "No source trace; do not treat this item as verified")
        for position, trace in enumerate(traces):
            trace_path = f"{path}[{position}]"
            block = blocks.get(trace.segmentId)
            if block is None:
                issue("source_block_missing", "error", trace_path, f"Unknown source block: {trace.segmentId}")
                continue
            if not normalized_quote(trace.quote) or normalized_quote(trace.quote) not in normalized_quote(str(block.get("text") or "")):
                issue("source_quote_mismatch", "error", trace_path, "Quote does not occur in the referenced block")
            for key in ("sourceLocator", "page", "paragraphIndex", "bodyChildIndex", "tableId", "rowIndex", "cellIndex"):
                actual = getattr(trace, key)
                if actual not in (None, "") and actual != block.get(key):
                    issue("source_anchor_mismatch", "error", f"{trace_path}.{key}", "Anchor does not match the referenced block")
            for key, expected in (("fileId", file_id), ("documentVersionId", version_id)):
                actual = getattr(trace, key)
                if not actual:
                    issue("source_identity_missing", "review", f"{trace_path}.{key}", "Source identity was not recorded")
                elif expected and actual != expected:
                    issue("source_identity_mismatch", "error", f"{trace_path}.{key}", "Source identity differs from this tender")

    if not tender.requirements:
        issue("requirements_empty", "review", "requirements", "No reviewed writing requirements")
    for finding in tender.risk.get("parseIssues") or []:
        if isinstance(finding, dict):
            diagnostic = TenderDiagnostic.model_validate(finding)
            issues.append(diagnostic)
    for position, requirement in enumerate(tender.requirements):
        path = f"requirements[{position}]"
        verify_traces(requirement.sourceTrace, f"{path}.sourceTrace")
        for key in ("actor", "category", "applicability"):
            if getattr(requirement, key) == "unknown":
                issue("requirement_semantics_unknown", "review", f"{path}.{key}", "Legacy semantics need review or re-extraction")
        if requirement.responseRequired is None:
            issue("response_obligation_unknown", "review", f"{path}.responseRequired", "Bidder response obligation has not been confirmed")
        if requirement.responseRequired is True and requirement.responseStage == "unknown":
            issue("response_stage_unknown", "review", f"{path}.responseStage", "Distinguish submission, clarification and contract performance duties")
        if requirement.parseConfidence is None or requirement.parseConfidence < 0.8:
            issue("requirement_confidence_unverified", "review", f"{path}.parseConfidence", "Confidence is missing or below 0.8")
        for parameter_index, parameter in enumerate(requirement.parameters):
            verify_traces(parameter.sourceTrace, f"{path}.parameters[{parameter_index}].sourceTrace")
        if "evidence_attachment" in requirement.responseMode and not requirement.evidenceRequirementIds:
            issue("evidence_obligation_missing", "review", f"{path}.evidenceRequirementIds", "Attachment response requires a structured evidence obligation")
        if "fixed_template" in requirement.responseMode and not requirement.templateFragmentIds:
            issue("fixed_template_binding_missing", "review", f"{path}.templateFragmentIds", "Fixed-form response requires an original template binding")
    for position, evidence in enumerate(tender.evidenceRequirements):
        path = f"evidenceRequirements[{position}]"
        verify_traces(evidence.sourceTrace, f"{path}.sourceTrace")
        if evidence.type == "unknown" or evidence.required is None or evidence.applicability == "unknown" or evidence.acquisition == "unknown":
            issue("evidence_semantics_unknown", "review", path, "Evidence type, applicability and acquisition require confirmation")

    if tender.evaluationMethod == "unknown":
        issue("evaluation_method_unknown", "review", "evaluationMethod", "Evaluation method must be confirmed, not inferred from extracted scores")
    else:
        verify_traces(tender.evaluationMethodSourceTrace, "evaluationMethodSourceTrace")
        method_label = {"lowest_evaluated_price": "最低评标价法", "comprehensive_scoring": "综合评分法"}[tender.evaluationMethod]
        if not any(method_label in normalized_quote(trace.quote) for trace in tender.evaluationMethodSourceTrace):
            issue("evaluation_method_unverified", "review", "evaluationMethod", "Evaluation method is not explicit in its source quote")
    for position, rule in enumerate(tender.evaluationRules):
        path = f"evaluationRules[{position}]"
        verify_traces(rule.sourceTrace, f"{path}.sourceTrace")
        if rule.ruleType == "unknown":
            issue("evaluation_rule_unknown", "review", f"{path}.ruleType", "Review rule has not been classified")
        for parameter_index, parameter in enumerate(rule.parameters):
            verify_traces(parameter.sourceTrace, f"{path}.parameters[{parameter_index}].sourceTrace")
        if rule.maxScore is not None:
            verify_traces(rule.maxScore.sourceTrace, f"{path}.maxScore.sourceTrace")
            number = re.escape(f"{rule.maxScore.value:g}")
            pattern = rf"(?<![\d.]){number}(?:\.0+)?\s*分(?!钟)"
            if not any(re.search(pattern, trace.quote) for trace in rule.maxScore.sourceTrace):
                issue("score_value_unverified", "review", f"{path}.maxScore", "A numeric time or percentage is not an explicit score in points")

    if not tender.submissionStructure.get("volumes"):
        issue("submission_structure_missing", "review", "submissionStructure", "Original submission volumes have not been recorded")
    fragments = {str(fragment.get("templateId") or fragment.get("templateFragmentId") or fragment.get("id") or ""): fragment for fragment in tender.templateFragments}
    bound_ids: set[str] = set()
    for position, table in enumerate(tender.responseTableSchemas):
        path = f"responseTableSchemas[{position}]"
        verify_traces(table.sourceTrace, f"{path}.sourceTrace")
        source_table = blocks.get(table.sourceTableId)
        if source_table is None or not isinstance(source_table.get("rows"), list):
            issue("response_table_source_missing", "error", f"{path}.sourceTableId", "Original response table is absent from documentBlocks")
        elif source_table["rows"]:
            header = source_table["rows"][0]
            labels = [str(cell.get("text") or "") for cell in header.get("cells") or [] if isinstance(cell, dict)]
            if [normalized_quote(column.label) for column in table.columns] != [normalized_quote(label) for label in labels]:
                issue("response_table_columns_mismatch", "error", f"{path}.columns", "Column labels or order differ from the original table")
        if not table.templateFragmentId:
            issue("response_template_missing", "review", f"{path}.templateFragmentId", "No reusable template fragment is bound")
        else:
            fragment = fragments[table.templateFragmentId]
            table_ids = {str(item.get("id") or item.get("tableId") or "") for item in fragment.get("tables") or [] if isinstance(item, dict)}
            if table.sourceTableId not in table_ids:
                issue("response_template_table_mismatch", "error", f"{path}.templateFragmentId", "Bound source table is not in this template fragment")
        for binding_index, binding in enumerate(table.rowBindings):
            bound_ids.add(binding.requirementId)
            verify_traces(binding.sourceTrace, f"{path}.rowBindings[{binding_index}].sourceTrace")
    for position, requirement in enumerate(tender.requirements):
        if "response_table" in requirement.responseMode and requirement.requirementId not in bound_ids:
            issue("response_table_binding_missing", "review", f"requirements[{position}]", "Confirmed table response has no requirement-to-row binding")
    counts = {severity: sum(item.severity == severity for item in issues) for severity in ("error", "review", "warning")}
    return TenderReadinessReport(
        parseBundleId=tender.parseBundleId,
        readyForWriting=not counts["error"] and not counts["review"],
        issues=issues,
        counts=counts,
    )
