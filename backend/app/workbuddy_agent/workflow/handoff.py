"""Adapters between the autonomous-agent contracts and existing projections."""
from __future__ import annotations

import hashlib
from typing import Any, Mapping

from ...tender_matching.contracts import (
    CLASS_DEFAULT_STATUS,
    requirement_class_for_route,
    build_comparison,
    normalize_requirement,
)
from ...tender_matching.document_fine_matching import CATEGORY_LABELS
from ...tender_matching.document_matching import summarize_fine_review
from .schemas import (
    AGENT_OUTPUT_CONTRACT_VERSION,
    AgentRun,
    WritingBrief,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any, limit: int = 1200) -> str:
    return str(value or "").strip()[:limit]


def _unique_dicts(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        identity = _text(item.get(key), 160)
        if not identity:
            identity = _text(item.get("id") or item.get("title") or item.get("text"), 240)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(item)
    return result


def _brief_id(
    *,
    brief_id: str,
    project_id: str,
    document_id: str,
    document_version_id: str,
) -> str:
    if brief_id.strip():
        return brief_id.strip()
    seed = "|".join((project_id, document_id, document_version_id, "writing-brief"))
    return f"WBR-{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


def _agent_requirement_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    """Keep only the stable requirement and match context needed by an agent."""
    text = _text(row.get("requirement") or row.get("rawText") or row.get("text"))
    return {
        "requirementId": _text(row.get("requirementId") or row.get("id"), 100),
        "title": _text(row.get("title") or row.get("kind") or text, 240),
        "text": text,
        "rawText": _text(row.get("rawText") or text),
        "category": _text(row.get("category") or "other", 60),
        "requirementClass": _text(row.get("requirementClass"), 40),
        "primaryRoute": _text(row.get("primaryRoute"), 60),
        "secondaryRoutes": [
            _text(item, 60)
            for item in (row.get("secondaryRoutes") or [])
            if _text(item, 60)
        ],
        "handlingMode": _text(row.get("handlingMode"), 40),
        "mandatory": bool(row.get("mandatory", True)),
        "isRejection": bool(row.get("isRejection")),
        "evidenceNeed": _text(row.get("evidenceNeed"), 100),
        "responseType": _text(row.get("responseType"), 100),
        "confidence": row.get("confidence"),
        "source": {
            "sourceSegmentId": _text(row.get("sourceSegmentId"), 120),
            "sourceSegmentIds": [
                _text(item, 120)
                for item in (row.get("sourceSegmentIds") or [])
                if _text(item, 120)
            ],
            "sourceLocator": _text(row.get("sourceLocator"), 200),
            "section": _text(row.get("section"), 240),
        },
        "match": {
            "status": _text(row.get("status"), 80),
            "reason": _text(row.get("reason"), 600),
            "matchedObjectIds": [
                _text(item, 120)
                for item in (row.get("matchedObjectIds") or [])
                if _text(item, 120)
            ],
            "matchedMaterialIds": [
                _text(item, 120)
                for item in (row.get("matchedMaterialIds") or [])
                if _text(item, 120)
            ],
            "evidenceIds": [
                _text(item, 120)
                for item in (row.get("evidenceIds") or [])
                if _text(item, 120)
            ],
            "evidenceCoverageStatus": _text(row.get("evidenceCoverageStatus"), 80),
        },
    }


def build_agent_review_input(
    *,
    run: AgentRun | Mapping[str, Any] | None = None,
    requirements: list[dict[str, Any]] | None = None,
    fine_review: dict[str, Any] | None = None,
    analysis_pack: dict[str, Any] | None = None,
    document_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a small, stable input envelope for a review or re-check agent.

    Full source text remains behind domain tools.  This envelope carries only
    the current facts, source IDs, and already-known match context.
    """
    run_model = (
        run
        if isinstance(run, AgentRun)
        else AgentRun.model_validate(run)
        if isinstance(run, Mapping)
        else None
    )
    review = _dict(fine_review)
    rows = _dicts(review.get("rows"))
    raw_requirements = rows or _dicts(requirements)
    pack = _dict(analysis_pack) or _dict(review.get("analysisPack"))
    pack_projection = {
        "schemaVersion": _text(pack.get("schemaVersion"), 80),
        "projectInfo": _dicts(pack.get("projectInfo")),
        "evaluationCriteria": _dicts(pack.get("evaluationCriteria")),
        "submissionItems": _dicts(pack.get("submissionItems")),
        "milestones": _dicts(pack.get("milestones")),
        "feesAndGuarantees": _dicts(pack.get("feesAndGuarantees")),
        "templateIndex": _dicts(pack.get("templateIndex")),
    }
    envelope = {
        "contractVersion": AGENT_OUTPUT_CONTRACT_VERSION,
        "run": run_model.model_dump(mode="json", by_alias=True) if run_model else {},
        "documentSummary": dict(document_summary or {}),
        "requirements": [_agent_requirement_projection(item) for item in raw_requirements],
        "analysisPack": pack_projection,
    }
    return envelope


def _response_task(requirement: Mapping[str, Any]) -> str:
    handling_mode = _text(requirement.get("handlingMode"), 40)
    if handling_mode == "database_match":
        return "准备并引用企业证据，完成响应中的能力或资格证明。"
    if handling_mode == "risk_check" or requirement.get("isRejection"):
        return "核查红线条件，并在递交前完成风险确认。"
    if handling_mode == "checklist":
        return "按原文清单完成文件、格式、签章或提交要求。"
    if handling_mode == "manual_fill":
        return "补充商务、报价或合同响应内容。"
    if handling_mode == "display":
        return "作为项目背景和写作上下文保留。"
    return "形成与招标要求对应的响应章节或说明。"


def build_writing_brief(
    analysis_pack: dict[str, Any] | None,
    *,
    fine_review: dict[str, Any] | None = None,
    project: dict[str, Any] | None = None,
    project_id: str = "",
    document_id: str = "",
    document_version_id: str = "",
    brief_id: str = "",
) -> WritingBrief:
    """Project the additive analysis pack into the writing-agent handoff."""
    pack = _dict(analysis_pack)
    review = _dict(fine_review)
    source = _dict(pack.get("source"))
    document_source = _dict(source.get("document"))
    project_info = _dicts(pack.get("projectInfo"))
    requirements = _dicts(pack.get("requirements"))
    if not requirements:
        requirements = _dicts(_dict(pack.get("writingProjection")).get("requirements"))

    rows = _dicts(review.get("rows"))
    by_requirement_id = {
        _text(row.get("requirementId") or row.get("id"), 100): row
        for row in rows
        if _text(row.get("requirementId") or row.get("id"), 100)
    }
    if not requirements:
        requirements = [
            {
                "requirementId": _text(row.get("requirementId") or row.get("id"), 100),
                "title": _text(row.get("title") or row.get("kind") or row.get("requirement"), 240),
                "normalizedText": _text(row.get("requirement") or row.get("rawText")),
                "rawText": _text(row.get("rawText") or row.get("requirement")),
                "category": _text(row.get("category") or "other", 60),
                "requirementClass": _text(row.get("requirementClass"), 40),
                "handlingMode": _text(row.get("handlingMode"), 40),
                "mandatory": bool(row.get("mandatory", True)),
                "isRejection": bool(row.get("isRejection")),
                "source": {
                    "segmentIds": [
                        _text(value, 120)
                        for value in (row.get("sourceSegmentIds") or [])
                        if _text(value, 120)
                    ],
                    "sourceLocator": _text(row.get("sourceLocator"), 200),
                },
            }
            for row in rows
        ]

    response_plan: list[dict[str, Any]] = []
    enterprise_evidence: list[dict[str, Any]] = []
    for item in requirements:
        requirement_id = _text(item.get("requirementId") or item.get("id"), 100)
        row = by_requirement_id.get(requirement_id, {})
        merged = {**item, **row}
        evidence_items = _dicts(row.get("evidenceItems"))
        for evidence in evidence_items:
            enterprise_evidence.append(
                {
                    "requirementId": requirement_id,
                    "evidenceId": _text(evidence.get("evidenceId") or evidence.get("id"), 120),
                    "name": _text(evidence.get("name"), 240),
                    "status": _text(evidence.get("status") or evidence.get("verificationStatus"), 100),
                    "sourceLocator": _text(
                        evidence.get("sourceLocator") or evidence.get("locator"),
                        200,
                    ),
                    "excerpt": _text(evidence.get("excerpt"), 600),
                }
            )
        response_plan.append(
            {
                "requirementId": requirement_id,
                "title": _text(merged.get("title") or merged.get("kind") or merged.get("requirement"), 240),
                "category": _text(merged.get("category") or "other", 60),
                "task": _response_task(merged),
                "status": _text(merged.get("status"), 80),
                "required": bool(merged.get("mandatory", True)),
                "source": _dict(merged.get("source"))
                or {
                    "sourceSegmentIds": [
                        _text(value, 120)
                        for value in (merged.get("sourceSegmentIds") or [])
                        if _text(value, 120)
                    ],
                    "sourceLocator": _text(merged.get("sourceLocator"), 200),
                },
                "evidenceIds": [
                    _text(value, 120)
                    for value in (merged.get("evidenceIds") or [])
                    if _text(value, 120)
                ],
            }
        )

    submission_items = _dicts(pack.get("submissionItems"))
    if not submission_items:
        submission_items = _dicts(pack.get("submissionRequirements"))
    templates = _dicts(pack.get("templateIndex"))
    format_constraints = [
        item
        for item in submission_items
        if item.get("handlingMode") in {"checklist", "manual_confirm"}
        or item.get("itemType") in {"format", "template"}
    ]
    rejection_constraints = [
        *(_dicts(pack.get("disqualificationRules"))),
        *[
            {
                "requirementId": _text(row.get("requirementId") or row.get("id"), 100),
                "text": _text(row.get("requirement") or row.get("rawText")),
                "sourceLocator": _text(row.get("sourceLocator"), 200),
                "status": _text(row.get("status"), 80),
            }
            for row in rows
            if row.get("isRejection")
        ],
    ]
    unresolved_items = [
        *(_dicts(review.get("unresolvedMandatory"))),
        *(_dicts(review.get("pendingMaterialList"))),
    ]
    warnings = [
        _text(value, 240)
        for value in (
            pack.get("warnings") or []
        )
        if _text(value, 240)
    ]
    project_payload = dict(project or {})
    if not project_payload:
        project_payload = {"items": project_info}

    brief = WritingBrief(
        briefId=_brief_id(
            brief_id=brief_id,
            project_id=project_id or _text(source.get("projectId"), 160),
            document_id=document_id or _text(document_source.get("documentId"), 160),
            document_version_id=document_version_id
            or _text(document_source.get("documentVersionId"), 160),
        ),
        projectId=project_id or _text(source.get("projectId"), 160),
        documentId=document_id or _text(document_source.get("documentId"), 160),
        documentVersionId=document_version_id
        or _text(document_source.get("documentVersionId"), 160),
        sourceFileId=_text(source.get("sourceFileId"), 160),
        sourceFileHash=_text(source.get("sourceFileHash"), 160),
        source={
            "sourceFileId": _text(source.get("sourceFileId"), 160),
            "sourceFileHash": _text(source.get("sourceFileHash"), 160),
            "analysisPackSchemaVersion": _text(pack.get("schemaVersion"), 80),
        },
        project=project_payload,
        requirements=requirements,
        evaluationCriteria=_dicts(pack.get("evaluationCriteria")),
        submissionItems=submission_items,
        templates=templates,
        enterpriseEvidence=_unique_dicts(enterprise_evidence, "evidenceId"),
        responsePlan=response_plan,
        formatConstraints=format_constraints,
        rejectionConstraints=_unique_dicts(rejection_constraints, "requirementId"),
        timelineConstraints=_dicts(pack.get("milestones")),
        unresolvedItems=unresolved_items,
        warnings=list(dict.fromkeys(warnings)),
        metadata={
            "sourceRequirementCount": len(requirements),
            "responsePlanCount": len(response_plan),
            "templateCount": len(templates),
            "containsHiddenReasoning": False,
        },
    )
    return brief


def _default_risk(status: str) -> str:
    if status in {"不满足", "有风险"}:
        return "高"
    if status in {"部分满足", "待补材料", "待核查", "待确认"}:
        return "中"
    return "低"


def _default_suggestion(status: str, requirement_class: str) -> str:
    if status in {"不满足", "有风险"}:
        return "请在递交前完成整改或由负责人确认是否放弃。"
    if status in {"待补材料", "待核查", "待确认"}:
        return "请补充证据并进行人工复核。"
    if requirement_class == "writing":
        return "请在标书响应章节中落实。"
    return "请按原文要求完成。"


def project_agent_result_to_fine_review(
    agent_result: dict[str, Any] | None,
    *,
    source_file_id: str = "",
    extraction: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Project Agent requirement drafts into the existing ``fineReview`` shape."""
    result = _dict(agent_result)
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(_dicts(result.get("requirements")), start=1):
        text = _text(raw.get("rawText") or raw.get("text") or raw.get("requirement"))
        if not text:
            continue
        normalized = normalize_requirement(
            {
                **raw,
                "id": raw.get("requirementId") or raw.get("id") or f"agent-req-{index:03d}",
                "requirementId": raw.get("requirementId") or raw.get("id"),
                "sourceFileId": source_file_id,
            },
            source_stage="tender_document",
            layer=3,
            kind=CATEGORY_LABELS.get(str(raw.get("category") or "other"), CATEGORY_LABELS["other"]),
            source_file_id=source_file_id,
        )
        requirement_class = requirement_class_for_route(
            str(normalized.get("category") or "other"),
            str(normalized.get("rawText") or normalized.get("requirement") or ""),
            normalized.get("routeHints"),
        )
        normalized["requirementClass"] = requirement_class
        status = _text(
            raw.get("status"),
            80,
        ) or CLASS_DEFAULT_STATUS.get(requirement_class, "待编制")
        evidence_items = _dicts(raw.get("evidenceItems"))
        matched_objects = [
            {
                "id": _text(item.get("id"), 120),
                "name": _text(item.get("name"), 240),
                "materialIds": [
                    _text(value, 120)
                    for value in (item.get("materialIds") or [])
                    if _text(value, 120)
                ],
            }
            for item in _dicts(raw.get("matchedObjects"))
        ]
        row = build_comparison(
            normalized,
            status=status,
            reason=_text(raw.get("reason"), 600)
            or "Agent 已完成条款分类，企业匹配结果待后续工具或人工确认。",
            matched_objects=matched_objects,
            evidences=evidence_items,
            risk=_text(raw.get("risk"), 20) or _default_risk(status),
            suggestion=_text(raw.get("suggestion"), 600)
            or _default_suggestion(status, requirement_class),
            status_source=_text(raw.get("statusSource"), 80) or "workbuddy-agent",
        )
        rows.append(row)

    extraction_payload = {
        "method": "workbuddy_agent",
        "provider": "workbuddy-agent",
        "modelUsed": _text(result.get("modelUsed"), 120),
        "warnings": [
            _text(value, 240)
            for value in (result.get("warnings") or [])
            if _text(value, 240)
        ],
        "analysisPack": _dict(result.get("analysisPack")),
        **_dict(extraction),
    }
    return summarize_fine_review(rows, extraction_payload)


__all__ = [
    "build_agent_review_input",
    "build_writing_brief",
    "project_agent_result_to_fine_review",
]
