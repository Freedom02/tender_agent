from __future__ import annotations

from collections import Counter
from copy import deepcopy
from typing import Any

from pydantic import Field, ValidationError

from .parse_semantics import BlockAssessment, hydrate_source_traces, validate_clause_batch
from .resolved_sources import normalized_quote
from .resolved_tender import EvidenceRequirement, TenderContract, WritingRequirement


class RequirementReplacement(TenderContract):
    targetId: str = Field(min_length=1)
    value: WritingRequirement


class EvidenceReplacement(TenderContract):
    targetId: str = Field(min_length=1)
    value: EvidenceRequirement


class AssessmentReplacement(TenderContract):
    targetId: str = Field(min_length=1)
    value: BlockAssessment


class ClauseRepair(TenderContract):
    requirementReplacements: list[RequirementReplacement] = Field(default_factory=list)
    evidenceReplacements: list[EvidenceReplacement] = Field(default_factory=list)
    assessmentReplacements: list[AssessmentReplacement] = Field(default_factory=list)
    addedRequirements: list[WritingRequirement] = Field(default_factory=list)
    addedEvidenceRequirements: list[EvidenceRequirement] = Field(default_factory=list)
    addedAssessments: list[BlockAssessment] = Field(default_factory=list)
    removeAssessmentTargets: list[str] = Field(default_factory=list)


def build_clause_repair_request(
    payload: Any, batch: dict[str, Any], *, document_blocks: dict[str, Any], source_file: dict[str, Any],
) -> dict[str, Any] | None:
    groups = {"requirements": WritingRequirement, "evidenceRequirements": EvidenceRequirement, "assessments": BlockAssessment}
    if not isinstance(payload, dict) or set(payload) - {*groups, "warnings"}:
        return None
    if any(not isinstance(payload.get(key), list) or any(not isinstance(item, dict) for item in payload[key]) for key in groups):
        return None
    if "warnings" in payload and (not isinstance(payload["warnings"], list) or any(not isinstance(item, str) for item in payload["warnings"])):
        return None
    for collection, identifier in (("requirements", "requirementId"), ("evidenceRequirements", "evidenceRequirementId")):
        ids = [item.get(identifier) for item in payload[collection]]
        if any(not isinstance(item_id, str) or not item_id for item_id in ids) or len(ids) != len(set(ids)):
            return None
    targets = {f"{collection}:{position}": {"targetId": f"{collection}:{position}", "collection": collection, "value": item}
               for collection in groups for position, item in enumerate(payload[collection])}
    issues: list[dict[str, Any]] = []
    affected: set[str] = set()
    view_ids: set[str] = set()
    removable: set[str] = set()

    def traces_for(item: dict[str, Any]) -> list[dict[str, Any]]:
        traces = item.get("sourceTrace")
        return [trace for trace in traces if isinstance(trace, dict)] if isinstance(traces, list) else []

    def issue(code: str, message: str, *target_ids: str, path: str = "", view_id: str = "") -> None:
        affected.update(target_id for target_id in target_ids if target_id in targets)
        if view_id:
            view_ids.add(view_id)
        issues.append({"code": code, "path": path, "message": message, "targetIds": list(target_ids), "viewId": view_id})

    for target_id, target in targets.items():
        item = target["value"]
        collection = target["collection"]
        try:
            hydrate_source_traces({collection: [item]}, document_blocks=document_blocks, source_file=source_file, views=batch["views"])
        except (ValueError, TypeError, AttributeError) as exc:
            issue("source_invalid", str(exc), target_id, path=f"{target_id}.sourceTrace")
        try:
            parsed = groups[collection].model_validate(item)
        except ValidationError as exc:
            for error in exc.errors(include_input=False):
                issue("schema_invalid", error["msg"], target_id, path=f"{target_id}." + ".".join(map(str, error["loc"])))
            continue
        if collection != "assessments":
            if not parsed.sourceTrace:
                issue("source_missing", "sourceTrace must be nonempty", target_id)
            if collection == "requirements":
                for position, parameter in enumerate(parsed.parameters):
                    if not parameter.sourceTrace:
                        issue("source_missing", "every parameter needs its own sourced quote", target_id, path=f"{target_id}.parameters.{position}")
                if parsed.templateFragmentIds or parsed.formatItemIds:
                    issue("premature_binding", "templateFragmentIds and formatItemIds must remain empty in clause extraction", target_id)

    requirements = {item["requirementId"]: (f"requirements:{position}", item) for position, item in enumerate(payload["requirements"])}
    evidence = {item["evidenceRequirementId"]: (f"evidenceRequirements:{position}", item) for position, item in enumerate(payload["evidenceRequirements"])}
    expected = {view["viewId"]: view for view in batch["views"]}
    assessed = Counter(item.get("viewId") for item in payload["assessments"] if isinstance(item.get("viewId"), str))
    for view_id in expected.keys() - assessed.keys():
        issue("coverage_missing", "source view needs an assessment; extract any overlooked obligations before classifying it", view_id=view_id)
    cited: set[str] = set()
    for position, assessment in enumerate(payload["assessments"]):
        target_id = f"assessments:{position}"
        view_id = assessment.get("viewId")
        if not isinstance(view_id, str):
            continue
        if view_id not in expected or assessed[view_id] > 1:
            removable.add(target_id)
            issue("coverage_invalid", "assessment viewId must occur exactly once in supplied views", target_id, view_id=view_id)
        references = assessment.get("requirementIds", [])
        if not isinstance(references, list) or any(not isinstance(reference, str) for reference in references):
            continue
        if (assessment.get("disposition") == "extracted") != bool(references):
            issue("assessment_links_invalid", "only extracted assessments must contain requirementIds", target_id, view_id=view_id)
        for requirement_id in references:
            if requirement_id not in requirements:
                issue("assessment_unknown_requirement", "assessment refers to an unknown requirement", target_id, view_id=view_id)
                continue
            cited.add(requirement_id)
            requirement_target, requirement = requirements[requirement_id]
            view = expected.get(view_id)
            traces = requirement.get("sourceTrace", [])
            if view and isinstance(traces, list) and not any(
                isinstance(trace, dict) and trace.get("segmentId") == view["segmentId"] and
                isinstance(trace.get("quote"), str) and bool(normalized_quote(trace["quote"])) and
                normalized_quote(trace["quote"]) in normalized_quote(view["text"]) for trace in traces
            ):
                issue("assessment_source_mismatch", "requirement must cite this view or assessment must correct its link", target_id, requirement_target, view_id=view_id)
    for requirement_id in requirements.keys() - cited:
        requirement_target, requirement = requirements[requirement_id]
        matching = [view["viewId"] for view in batch["views"] if any(
            isinstance(trace, dict) and trace.get("segmentId") == view["segmentId"] and isinstance(trace.get("quote"), str) and
            bool(normalized_quote(trace["quote"])) and normalized_quote(trace["quote"]) in normalized_quote(view["text"])
            for trace in traces_for(requirement))]
        linked_targets = [f"assessments:{position}" for position, item in enumerate(payload["assessments"]) if item.get("viewId") in matching]
        issue("requirement_unlinked", f"requirement {requirement_id} needs a matching source view assessment", requirement_target, *linked_targets)
        view_ids.update(matching)
    for evidence_id, (target_id, item) in evidence.items():
        parent = requirements.get(item.get("requirementId")) if isinstance(item.get("requirementId"), str) else None
        parent_references = parent[1].get("evidenceRequirementIds") if parent else None
        if parent is None or not isinstance(parent_references, list) or evidence_id not in parent_references:
            issue("evidence_link_invalid", "evidence must have a reciprocal requirement reference", target_id, *([parent[0]] if parent else []))
    for requirement_id, (target_id, item) in requirements.items():
        references = item.get("evidenceRequirementIds", [])
        if not isinstance(references, list):
            continue
        for evidence_id in references:
            child = evidence.get(evidence_id) if isinstance(evidence_id, str) else None
            if child is None or child[1].get("requirementId") != requirement_id:
                issue("evidence_link_invalid", "invalid evidenceRequirementIds reference", target_id, *([child[0]] if child else []))
    if not issues:
        return None
    segment_ids: set[str] = set()

    def collect_sources(value: Any) -> None:
        if isinstance(value, dict):
            if isinstance(value.get("segmentId"), str):
                segment_ids.add(value["segmentId"])
            if isinstance(value.get("viewId"), str):
                segment_ids.add(value["viewId"].split("@", 1)[0])
            for child in value.values():
                collect_sources(child)
        elif isinstance(value, list):
            for child in value:
                collect_sources(child)

    for target_id in affected:
        collect_sources(targets[target_id]["value"])
    selected = [view for view in batch["views"] if view["viewId"] in view_ids or view["segmentId"] in segment_ids]
    if not selected or any(targets[target_id]["collection"] != "assessments" and not targets[target_id]["value"].get("sourceTrace") for target_id in affected):
        selected = batch["views"]
    return {
        "batchId": batch["batchId"], "issues": issues,
        "targets": [deepcopy(target) for target_id, target in targets.items() if target_id in affected],
        "views": deepcopy(selected), "allowedViewIds": list(expected),
        "missingViewIds": [view_id for view_id in expected if view_id not in assessed],
        "removableAssessmentTargets": sorted(removable),
        "knownRequirements": [{"requirementId": requirement_id, "evidenceRequirementIds": item.get("evidenceRequirementIds", []),
                               "sourceSegmentIds": sorted({trace["segmentId"] for trace in traces_for(item)
                                                            if isinstance(trace, dict) and isinstance(trace.get("segmentId"), str)})}
                              for requirement_id, (_, item) in requirements.items()],
        "knownEvidence": [{"evidenceRequirementId": evidence_id, "requirementId": item.get("requirementId")}
                          for evidence_id, (_, item) in evidence.items()],
    }


def apply_clause_repair(
    payload: dict[str, Any], patch: dict[str, Any], request: dict[str, Any], batch: dict[str, Any], *,
    document_blocks: dict[str, Any], source_file: dict[str, Any], validate: bool = True,
) -> dict[str, Any]:
    repair = ClauseRepair.model_validate(patch)
    allowed = {target["targetId"]: target for target in request["targets"]}
    result = deepcopy(payload)
    touched: set[str] = set()
    for field, collection, identifier in (
        ("requirementReplacements", "requirements", "requirementId"),
        ("evidenceReplacements", "evidenceRequirements", "evidenceRequirementId"),
        ("assessmentReplacements", "assessments", "viewId"),
    ):
        for replacement in getattr(repair, field):
            target = allowed.get(replacement.targetId)
            if target is None or target["collection"] != collection or replacement.targetId in touched:
                raise ValueError("repair target must be a unique supplied object of the matching collection")
            value = replacement.value.model_dump(mode="json", exclude_defaults=True)
            if collection == "assessments" and value[identifier] not in {view["viewId"] for view in request["views"]}:
                raise ValueError("repaired assessments must refer to a supplied repair view")
            if collection != "assessments" and value[identifier] != target["value"][identifier]:
                raise ValueError("repair cannot change an existing requirement or evidence ID")
            position = int(replacement.targetId.split(":", 1)[1])
            result[collection][position] = value
            touched.add(replacement.targetId)
    removed: set[int] = set()
    for target_id in repair.removeAssessmentTargets:
        if target_id not in request["removableAssessmentTargets"] or target_id in touched:
            raise ValueError("repair may remove only supplied duplicate or extra assessments")
        removed.add(int(target_id.split(":", 1)[1]))
        touched.add(target_id)
    result["assessments"] = [item for position, item in enumerate(result["assessments"]) if position not in removed]
    result["requirements"].extend(item.model_dump(mode="json", exclude_defaults=True) for item in repair.addedRequirements)
    result["evidenceRequirements"].extend(item.model_dump(mode="json", exclude_defaults=True) for item in repair.addedEvidenceRequirements)
    if any(item.viewId not in {view["viewId"] for view in request["views"]} for item in repair.addedAssessments):
        raise ValueError("added assessments must refer to a supplied repair view")
    result["assessments"].extend(item.model_dump(mode="json", exclude_defaults=True) for item in repair.addedAssessments)
    hydrate_source_traces(patch, document_blocks=document_blocks, source_file=source_file, views=request["views"])
    if validate:
        hydrated = hydrate_source_traces(result, document_blocks=document_blocks, source_file=source_file, views=batch["views"])
        validate_clause_batch(hydrated, batch)
    return result
