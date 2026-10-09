"""Strict intermediate artifacts and source views for native tender parsing."""
from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, Literal

from pydantic import Field, model_validator

from .resolved_sources import normalized_quote, source_index
from .resolved_tender import (
    EvidenceRequirement, EvaluationRule, ResolvedTenderV2, ResponseTableSchema,
    SourceTrace, TenderContract, WritingRequirement,
)


class BlockAssessment(TenderContract):
    viewId: str = Field(min_length=1)
    disposition: Literal["extracted", "context", "review"]
    requirementIds: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)


class ClauseExtraction(TenderContract):
    requirements: list[WritingRequirement]
    evidenceRequirements: list[EvidenceRequirement]
    assessments: list[BlockAssessment]
    warnings: list[str] = Field(default_factory=list)


class EvaluationExtraction(TenderContract):
    evaluationMethod: Literal["lowest_evaluated_price", "comprehensive_scoring", "unknown"] = "unknown"
    evaluationMethodSourceTrace: list[SourceTrace] = Field(default_factory=list)
    evaluationRules: list[EvaluationRule]
    warnings: list[str] = Field(default_factory=list)


class TableBinding(TenderContract):
    responseTableSchemas: list[ResponseTableSchema]
    warnings: list[str] = Field(default_factory=list)


class SemanticFinding(TenderContract):
    code: str = Field(min_length=1)
    severity: Literal["error", "review", "warning"]
    path: str = ""
    message: str = Field(min_length=1)


class SemanticReview(TenderContract):
    status: Literal["pass", "review"]
    reviewedViewIds: list[str]
    reviewedRequirementIds: list[str]
    findings: list[SemanticFinding]
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_status(self) -> SemanticReview:
        if self.status == "pass" and any(item.severity != "warning" for item in self.findings):
            raise ValueError("pass cannot contain unresolved semantic findings")
        if self.status == "review" and not any(item.severity != "warning" for item in self.findings):
            raise ValueError("review requires a concrete unresolved finding")
        return self


def build_source_batches(document_blocks: dict[str, Any], *, max_chars: int = 14000) -> list[dict[str, Any]]:
    """Cover full source text, retaining row atoms and splitting oversized blocks."""
    if max_chars < 3000:
        raise ValueError("source batch budget must be at least 3000 characters")
    indexed = source_index(document_blocks)
    raw_units = document_blocks.get("units") or document_blocks.get("segments") or []
    selected: list[dict[str, Any]] = []
    covered_rows: set[tuple[str, Any]] = set()
    for unit in raw_units:
        if not isinstance(unit, dict):
            continue
        block = indexed.get(str(unit.get("id") or unit.get("segmentId") or ""), {})
        if not str(block.get("text") or "").strip():
            continue
        if block.get("contentType") == "table_row":
            covered_rows.add((str(block.get("tableId") or ""), block.get("rowIndex")))
            selected.append(block)
        elif not block.get("tableId"):
            selected.append(block)
    selected_ids = {str(block.get("id") or block.get("segmentId") or "") for block in selected}
    for block_id, block in indexed.items():
        if block_id in selected_ids or not str(block.get("text") or "").strip():
            continue
        if isinstance(block.get("rows"), list) or isinstance(block.get("paragraphs"), list) and not block.get("tableId"):
            continue
        if block.get("tableId"):
            if (str(block["tableId"]), block.get("rowIndex")) in covered_rows:
                continue
            if block.get("paragraphIndex") is not None or block.get("contentType") == "table_cell_paragraph":
                continue
        selected.append(block)
        selected_ids.add(block_id)
    selected.sort(key=lambda block: (block.get("order") or block.get("bodyChildIndex") or 0, str(block.get("id") or "")))
    views: list[dict[str, Any]] = []
    text_budget = max_chars // 3
    for block in selected:
        block_id = str(block.get("id") or block.get("segmentId") or "")
        text = str(block.get("text") or "")
        start = 0
        while start < len(text):
            end = min(start + text_budget, len(text))
            if end < len(text):
                newline = text.rfind("\n", start + text_budget // 2, end)
                if newline > start:
                    end = newline + 1
            view = {key: block[key] for key in (
                "sourceLocator", "contentType", "tableId", "rowIndex", "cellIndex", "paragraphIndex", "bodyChildIndex", "page",
            ) if block.get(key) not in (None, "")}
            view.update(viewId=f"{block_id}@{start}:{end}", segmentId=block_id, text=text[start:end])
            table = indexed.get(str(block.get("tableId") or ""), {})
            rows = table.get("rows") or []
            if rows:
                view["tableHeaders"] = [str(cell.get("text") or "") for cell in rows[0].get("cells") or []]
            views.append(view)
            start = end
    batches: list[dict[str, Any]] = []
    current: list[dict[str, Any]] = []
    for view in views:
        if current and len(json.dumps([*current, view], ensure_ascii=False)) > max_chars - 64:
            batches.append({"batchId": f"batch-{len(batches) + 1:04d}", "views": current})
            current = []
        if len(json.dumps([view], ensure_ascii=False)) > max_chars - 64:
            raise ValueError(f"source view exceeds batch budget: {view['viewId']}")
        current.append(view)
    if current:
        batches.append({"batchId": f"batch-{len(batches) + 1:04d}", "views": current})
    for position, batch in enumerate(batches):
        neighbors = []
        if position:
            neighbors.append(batches[position - 1]["views"][-1])
        if position + 1 < len(batches):
            neighbors.append(batches[position + 1]["views"][0])
        batch["contextViews"] = [
            {"segmentId": view["segmentId"], "text": view["text"][-600:] if neighbor_index == 0 and position else view["text"][:600]}
            for neighbor_index, view in enumerate(neighbors)
        ]
    return batches


def hydrate_source_traces(
    payload: dict[str, Any], *, document_blocks: dict[str, Any], source_file: dict[str, Any],
    views: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve only supplied block IDs; reject invented quotes, identities or anchors."""
    indexed = source_index(document_blocks)
    allowed: dict[str, list[str]] = {}
    if views is not None:
        for view in views:
            allowed.setdefault(view["segmentId"], []).append(view["text"])
    result = deepcopy(payload)

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in {"sourceTrace", "evaluationMethodSourceTrace"}:
                    if not isinstance(item, list):
                        raise ValueError("sourceTrace must be a list")
                    for position, raw_trace in enumerate(item):
                        trace = SourceTrace.model_validate(raw_trace)
                        block = indexed.get(trace.segmentId)
                        if block is None:
                            raise ValueError(f"source_block_missing: {trace.segmentId}")
                        quote = normalized_quote(trace.quote)
                        texts = allowed.get(trace.segmentId, []) if views is not None else [str(block.get("text") or "")]
                        if not quote or not any(quote in normalized_quote(text) for text in texts):
                            raise ValueError(f"source_quote_outside_view: {trace.segmentId}")
                        expected = {
                            "fileId": str(source_file.get("fileId") or ""),
                            "documentVersionId": str(source_file.get("documentVersionId") or source_file.get("sha256") or ""),
                            **{anchor: block.get(anchor) for anchor in (
                                "sourceLocator", "page", "paragraphIndex", "bodyChildIndex", "tableId", "rowIndex", "cellIndex",
                            )},
                        }
                        hydrated = trace.model_dump(mode="json")
                        for anchor, actual in expected.items():
                            supplied = getattr(trace, anchor)
                            if supplied not in (None, "") and supplied != actual:
                                raise ValueError(f"source_anchor_mismatch: {trace.segmentId}.{anchor}")
                            if actual is not None:
                                hydrated[anchor] = actual
                        item[position] = SourceTrace.model_validate(hydrated).model_dump(mode="json")
                else:
                    visit(item)

    visit(result)
    return result


def validate_clause_batch(payload: dict[str, Any], batch: dict[str, Any]) -> ClauseExtraction:
    result = ClauseExtraction.model_validate(payload)
    requirement_ids = {item.requirementId for item in result.requirements}
    expected = {view["viewId"] for view in batch["views"]}
    assessed = [item.viewId for item in result.assessments]
    if len(assessed) != len(set(assessed)) or set(assessed) != expected:
        missing = sorted(expected - set(assessed))
        extra = sorted(set(assessed) - expected)
        duplicates = sorted({view_id for view_id in assessed if assessed.count(view_id) > 1})
        raise ValueError(f"coverage_incomplete: missing={missing[:12]}, extra={extra[:12]}, duplicate={duplicates[:12]}; every source view needs exactly one assessment")
    ResolvedTenderV2(parseBundleId="batch-validation", requirements=result.requirements, evidenceRequirements=result.evidenceRequirements)
    cited_ids: set[str] = set()
    views_by_id = {view["viewId"]: view for view in batch["views"]}
    requirements = {item.requirementId: item for item in result.requirements}
    for assessment in result.assessments:
        if any(item_id not in requirement_ids for item_id in assessment.requirementIds):
            raise ValueError("assessment refers to an unknown requirement")
        if assessment.disposition == "extracted" and not assessment.requirementIds:
            raise ValueError("extracted source view requires requirementIds")
        if assessment.disposition != "extracted" and assessment.requirementIds:
            raise ValueError("non-extracted view cannot claim extracted requirements")
        view = views_by_id[assessment.viewId]
        for requirement_id in assessment.requirementIds:
            requirement = requirements[requirement_id]
            if not any(trace.segmentId == view["segmentId"] and normalized_quote(trace.quote) in normalized_quote(view["text"])
                       for trace in requirement.sourceTrace):
                raise ValueError(f"assessment[{assessment.viewId}].requirementIds[{requirement_id}]: assessment requirement does not cite this source view; add its own sourced quote or correct the assessment link")
        cited_ids.update(assessment.requirementIds)
    if cited_ids != requirement_ids:
        raise ValueError(f"every requirement must be linked to a source view assessment: unlinked={sorted(requirement_ids - cited_ids)}")
    for requirement in result.requirements:
        if not requirement.sourceTrace:
            raise ValueError(f"requirements[{requirement.requirementId}].sourceTrace must be nonempty")
        missing = [parameter.name for parameter in requirement.parameters if not parameter.sourceTrace]
        if missing:
            raise ValueError(f"requirements[{requirement.requirementId}].parameters sourceTrace missing: {missing}; every parameter needs its own sourced quote")
    missing_evidence = [item.evidenceRequirementId for item in result.evidenceRequirements if not item.sourceTrace]
    if missing_evidence:
        raise ValueError(f"evidence requirements require sourceTrace: {missing_evidence}")
    return result


def namespace_clause_batch(result: ClauseExtraction, batch_id: str) -> ClauseExtraction:
    payload = result.model_dump(mode="json")
    requirement_ids = {item.requirementId: f"{batch_id}:{item.requirementId}" for item in result.requirements}
    evidence_ids = {item.evidenceRequirementId: f"{batch_id}:{item.evidenceRequirementId}" for item in result.evidenceRequirements}
    alternative_ids = {item.alternativeGroupId: f"{batch_id}:{item.alternativeGroupId}" for item in result.evidenceRequirements if item.alternativeGroupId}
    for item in payload["requirements"]:
        item["requirementId"] = requirement_ids[item["requirementId"]]
        item["evidenceRequirementIds"] = [evidence_ids[item_id] for item_id in item["evidenceRequirementIds"]]
    for item in payload["evidenceRequirements"]:
        item["evidenceRequirementId"] = evidence_ids[item["evidenceRequirementId"]]
        item["requirementId"] = requirement_ids[item["requirementId"]]
        if item["alternativeGroupId"]:
            item["alternativeGroupId"] = alternative_ids[item["alternativeGroupId"]]
    for item in payload["assessments"]:
        item["requirementIds"] = [requirement_ids[item_id] for item_id in item["requirementIds"]]
    return ClauseExtraction.model_validate(payload)
