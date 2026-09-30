"""Integration boundary for the WorkBuddy + Agent matching route.

The builtin route stays in ``tender_matching.document_matching``.  This
module is the only adapter that turns a WorkBuddy DocumentMap run into the
existing matching input shape.  WorkBuddy classifies tender content; the
backend still owns enterprise evidence matching and final status calculation.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from ...tender_matching.analysis_contracts import build_analysis_pack
from ...tender_matching.document_fine_matching import (
    _deterministic_rejection_segments,
    _rejection_requirements_from_segments,
)
from ...tender_matching.document_matching import evaluate_tender_document
from ...tender_matching.document_models import DocumentIR, document_ir_from_parsed
from ...tender_matching.fact_models import RequirementExtractionResult
from ...tender_matching.target_stage_contract import (
    ENTERPRISE_MATCH_TARGETS,
    MATCH_STAGE_TARGETS,
    RISK_CHECK_TARGETS,
)
from ..document_map import (
    DocumentMap,
    attach_document_map_to_analysis_pack,
    build_scope_processing_output,
    extract_target_requirements,
    validate_document_map,
)
from .runtime import (
    CODEBUDDY_SDK_DEFAULT_MODEL,
    DocumentMapAgentRuntime,
    DocumentMapRuntimeConfig,
    _high_risk_segment_ids,
)


class WorkBuddyAgentMatchingError(RuntimeError):
    """A failed WorkBuddy run that can be surfaced or explicitly fallen back."""

    def __init__(self, message: str, *, runtime_result: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.runtime_result = dict(runtime_result or {})


REVIEW_OUTPUT_SCHEMA_VERSION = "tender-review-output/v1"


def _text(value: Any, limit: int = 1200) -> str:
    return str(value or "").strip()[:limit]


def _document_ir(
    text: str,
    document: Mapping[str, Any],
    *,
    source_file_id: str,
) -> DocumentIR:
    file_name = _text(
        document.get("fileName")
        or document.get("name")
        or f"{source_file_id}.txt",
        240,
    )
    content_hash = _text(document.get("contentHash"), 160)
    identity_seed = content_hash or source_file_id or file_name
    digest = hashlib.sha256(identity_seed.encode("utf-8")).hexdigest()[:12]
    document_id = _text(document.get("documentId"), 120) or (
        f"DOC-{content_hash[:12]}"
        if content_hash
        else _text(source_file_id, 120) or f"DOC-{digest}"
    )
    document_version_id = (
        _text(document.get("documentVersionId"), 140)
        or f"{document_id}-V001"
    )
    parsed = dict(document)
    parsed["text"] = text
    parsed["fileName"] = file_name
    parsed["fileHash"] = content_hash
    parsed["documentId"] = document_id
    parsed["documentVersionId"] = document_version_id
    parsed.setdefault("units", parsed.get("segments") or [])
    parsed.setdefault("parseMethod", parsed.get("parseMethod") or "store_document")
    parsed.setdefault("metadata", {})
    return document_ir_from_parsed(
        parsed,
        file_name=file_name,
        mime_type=_text(document.get("mimeType"), 160),
        document_id=document_id,
        document_version_id=document_version_id,
    )


def _source_ids(raw: Mapping[str, Any]) -> list[str]:
    values = raw.get("sourceSegmentIds") or raw.get("segmentIds") or []
    if not isinstance(values, list):
        values = [values]
    return list(dict.fromkeys(_text(value, 120) for value in values if _text(value, 120)))


def _requirement_payload(
    extraction: RequirementExtractionResult,
    document: DocumentIR,
    *,
    source_file_id: str,
) -> list[dict[str, Any]]:
    segment_map = {segment.id: segment for segment in document.segments if segment.id}
    result: list[dict[str, Any]] = []
    for requirement in extraction.requirements:
        raw = requirement.model_dump(mode="json", by_alias=True)
        # The target processor has already restricted this extraction to the
        # DocumentMap's technical/qualification ranges.  Keep that domain in
        # dedicated fields because the legacy matching projection mutates
        # ``category`` while normalizing fineReview rows.
        target_domain = _text(requirement.category, 80).lower()
        if target_domain in {"technical", "qualification"}:
            raw["targetModule"] = target_domain
            raw["mapDomain"] = target_domain
        source_ids = _source_ids(raw)
        first_segment = next(
            (segment_map[item] for item in source_ids if item in segment_map),
            None,
        )
        raw["sourceFileId"] = source_file_id
        raw["sourceSegmentIds"] = source_ids
        raw["sourceSegmentId"] = source_ids[0] if source_ids else ""
        raw["sourceLocator"] = _text(
            raw.get("sourceLocator")
            or (first_segment.source_locator if first_segment else ""),
            200,
        )
        raw["section"] = _text(
            raw.get("section")
            or " / ".join(raw.get("sectionPath") or []),
            240,
        )
        result.append(raw)
    return result


def _scope_agent_analysis(scope: Mapping[str, Any]) -> dict[str, Any]:
    project_info = scope.get("projectInfo")
    project_items: list[dict[str, Any]] = []
    if isinstance(project_info, Mapping):
        for field in project_info.get("fields") or []:
            if not isinstance(field, Mapping):
                continue
            source = field.get("source") if isinstance(field.get("source"), Mapping) else {}
            project_items.append(
                {
                    "id": field.get("fieldId") or field.get("fieldName"),
                    "field": field.get("fieldName") or "",
                    "label": field.get("label") or field.get("fieldName") or "",
                    "title": field.get("label") or field.get("fieldName") or "",
                    "text": field.get("value") or "",
                    "rawText": field.get("sourceQuote") or field.get("value") or "",
                    "value": field.get("value") or "",
                    "sourceSegmentIds": list(
                        field.get("sourceSegmentIds")
                        or source.get("segmentIds")
                        or []
                    ),
                    "sourceLocator": source.get("sourceLocator") or "",
                    "sectionId": source.get("sectionId") or "",
                    "handlingMode": "display",
                    "confidence": field.get("confidence", 0.0),
                }
            )

    templates = scope.get("templates")
    template_items: list[dict[str, Any]] = []
    if isinstance(templates, Mapping):
        for locator in templates.get("locators") or []:
            if not isinstance(locator, Mapping):
                continue
            template_items.append(
                {
                    "templateId": locator.get("templateId") or "",
                    "title": locator.get("title") or locator.get("templateId") or "",
                    "text": locator.get("title") or locator.get("templateId") or "",
                    "rawText": locator.get("title") or locator.get("templateId") or "",
                    "templateType": locator.get("templateType") or "other",
                    "sectionIds": list(locator.get("sectionIds") or []),
                    "sourceSegmentIds": list(locator.get("segmentIds") or []),
                    "sourceLocator": locator.get("sourceLocator") or "",
                    "handlingMode": "display",
                    "confidence": 0.9,
                }
            )
    commercial_terms = [
        dict(item)
        for item in (scope.get("commercialTerms") or [])
        if isinstance(item, Mapping)
    ]
    evaluation_criteria = [
        dict(item)
        for item in (scope.get("evaluationCriteria") or [])
        if isinstance(item, Mapping)
    ]
    return {
        "projectInfo": project_items,
        "commercialTerms": commercial_terms,
        "evaluationCriteria": evaluation_criteria,
        "templateIndex": template_items,
    }


def _rejection_requirements(document_map: DocumentMap) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for candidate in document_map.rejection_candidates:
        source_ids = list(
            dict.fromkeys(
                [
                    *candidate.source.segment_ids,
                    *candidate.context_segment_ids,
                ]
            )
        )
        text = _text(candidate.context_text or candidate.text or candidate.matched_term)
        if not text or not source_ids:
            continue
        result.append(
            {
                "requirementId": f"REJECTION-{candidate.candidate_id}",
                "category": "rejection",
                "rawText": text,
                "normalizedRequirement": text,
                "sourceSegmentIds": source_ids,
                "sourceSegmentId": source_ids[0],
                "sourceLocator": candidate.source.source_locator,
                "mandatory": True,
                "isRejection": True,
                "importance": "critical",
                "confidence": 0.9 if candidate.is_hard_risk else 0.7,
                "classificationSource": "workbuddy-document-map",
            }
        )
    return result


def _builtin_rejection_requirements(document: DocumentIR) -> list[dict[str, Any]]:
    """Keep the existing full-document rejection scan in the Agent route."""

    segments = [
        item.model_dump(mode="json", by_alias=True)
        for item in document.segments
    ]
    sections = [
        item.model_dump(mode="json", by_alias=True)
        for item in document.sections
    ]
    candidates = _deterministic_rejection_segments(segments, sections)
    return _rejection_requirements_from_segments(candidates, sections)


def _merge_rejection_requirements(
    mapped: list[dict[str, Any]],
    scanned: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    seen_text: set[str] = set()
    for item in [*mapped, *scanned]:
        source_ids = _source_ids(item)
        text = _text(
            item.get("normalizedRequirement")
            or item.get("rawText")
            or item.get("text")
        )
        source_key = source_ids[0] if source_ids else ""
        text_key = "".join(text.split())
        if (source_key and source_key in seen_sources) or (
            text_key and text_key in seen_text
        ):
            continue
        if source_key:
            seen_sources.add(source_key)
        if text_key:
            seen_text.add(text_key)
        result.append(item)
    return result


def _row_matches_domain(row: Mapping[str, Any], domain: str) -> bool:
    target = _text(
        row.get("targetModule")
        or row.get("targetModuleKey")
        or row.get("domain"),
        80,
    ).lower()
    category = _text(
        row.get("category")
        or row.get("contentType")
        or row.get("matchDomain"),
        80,
    ).lower()
    if target == domain or category == domain:
        return True
    if domain == "technical":
        return category in {"service_plan", "product", "service"}
    if domain == "qualification":
        return category in {
            "performance",
            "personnel",
            "financial",
            "credit",
            "region",
            "eligibility",
        }
    if domain == "rejection":
        return bool(
            row.get("isRejection")
            or row.get("requirementClass") == "rejection"
            or row.get("primaryRoute") == "rejection_check"
        )
    return False


def _canonical_module(
    domain: str,
    items: list[Mapping[str, Any]],
    *,
    route: str,
    handling_mode: str,
    ignored: bool = False,
) -> dict[str, Any]:
    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        value = dict(item)
        value.setdefault("domain", domain)
        value.setdefault("targetModule", domain)
        value.setdefault("primaryRoute", route)
        value.setdefault("handlingMode", handling_mode)
        normalized.append(value)
    return {
        "domain": domain,
        "primaryRoute": route,
        "handlingMode": handling_mode,
        "ignored": ignored,
        "count": len(normalized),
        "items": normalized,
    }


def _target_module_items(
    pack: Mapping[str, Any],
    target_module: str,
    *,
    route: str,
    handling_mode: str,
) -> list[dict[str, Any]]:
    """Adapt builtin target-oriented items into the shared UI contract."""

    raw_modules = pack.get("targetModules")
    if isinstance(raw_modules, Mapping):
        raw_target = raw_modules.get(target_module)
        raw_items = raw_target.get("items") if isinstance(raw_target, Mapping) else []
    elif isinstance(raw_modules, list):
        raw_target = next(
            (
                item
                for item in raw_modules
                if isinstance(item, Mapping)
                and str(item.get("targetModule") or "") == target_module
            ),
            {},
        )
        raw_items = raw_target.get("items") if isinstance(raw_target, Mapping) else []
    else:
        raw_items = []

    result: list[dict[str, Any]] = []
    for item in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(item, Mapping):
            continue
        value = dict(item)
        value.setdefault("domain", target_module)
        value.setdefault("targetModule", target_module)
        value.setdefault("primaryRoute", route)
        # Builtin uses the compact internal value "match"; the frontend
        # contract uses the explicit handling mode shared with WorkBuddy.
        if str(value.get("handlingMode") or "") in {"", "match"}:
            value["handlingMode"] = handling_mode
        result.append(value)
    return result


def _item_match_keys(item: Mapping[str, Any]) -> set[str]:
    source = item.get("source") if isinstance(item.get("source"), Mapping) else {}
    values = [
        item.get("id"),
        item.get("requirementId"),
        item.get("sourceSegmentId"),
        *(item.get("sourceSegmentIds") or []),
        *(item.get("segmentIds") or []),
        source.get("segmentId"),
        *(source.get("segmentIds") or []),
        item.get("normalizedRequirement"),
        item.get("requirement"),
        item.get("rawText"),
        item.get("text"),
    ]
    return {
        "".join(str(value).split()).lower()
        for value in values
        if str(value or "").strip()
    }


def _target_rows_for_domain(
    domain: str,
    targets: list[Mapping[str, Any]],
    fine_rows: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Keep the DocumentMap target domain authoritative over legacy row labels."""

    result: list[dict[str, Any]] = []
    used: set[int] = set()
    for target in targets:
        target_keys = _item_match_keys(target)
        match_index = next(
            (
                index
                for index, row in enumerate(fine_rows)
                if index not in used and target_keys.intersection(_item_match_keys(row))
            ),
            None,
        )
        value = dict(fine_rows[match_index]) if match_index is not None else dict(target)
        if match_index is not None:
            used.add(match_index)
        value["domain"] = domain
        value["targetModule"] = domain
        value.setdefault("primaryRoute", "company_match")
        value.setdefault("handlingMode", "database_match")
        result.append(value)
    return result


def _build_review_output(
    *,
    document_map: DocumentMap,
    scope_output: Any,
    target_requirements: list[dict[str, Any]],
    fine_review: Mapping[str, Any],
    runtime_result: Mapping[str, Any],
    status: str,
    fallback_mode: str = "",
) -> dict[str, Any]:
    """Build the frontend-facing eight-domain output contract.

    This is intentionally producer-neutral at the outer shape.  A future
    extraction method can provide the same ``modules`` and ``source`` fields
    without recreating the old classifier/fineReview projection.
    """

    fine_rows = [
        dict(item)
        for item in (fine_review.get("rows") or [])
        if isinstance(item, Mapping)
    ]
    target_by_domain = {
        domain: [
            dict(item)
            for item in target_requirements
            if isinstance(item, Mapping) and _row_matches_domain(item, domain)
        ]
        for domain in ("technical", "qualification")
    }
    matched_by_domain = {
        domain: _target_rows_for_domain(
            domain,
            target_by_domain[domain],
            fine_rows,
        )
        for domain in ("technical", "qualification")
    }
    matched_by_domain["rejection"] = [
        item for item in fine_rows if _row_matches_domain(item, "rejection")
    ]

    rejection_items = matched_by_domain["rejection"] or _rejection_requirements(document_map)
    project_items = [
        item.model_dump(mode="json", by_alias=True)
        for item in scope_output.project_info.fields
    ]
    commercial_items = [
        item.model_dump(mode="json", by_alias=True)
        for item in scope_output.commercial_terms
    ]
    evaluation_items = [
        item.model_dump(mode="json", by_alias=True)
        for item in scope_output.evaluation_criteria
    ]
    template_items = [
        item.model_dump(mode="json", by_alias=True)
        for item in scope_output.templates.locators
    ]
    contract_items = [
        item.model_dump(mode="json", by_alias=True)
        for item in scope_output.contracts.ignored_sections
    ]
    modules = {
        "technical": _canonical_module(
            "technical",
            matched_by_domain["technical"],
            route="company_match",
            handling_mode="database_match",
        ),
        "qualification": _canonical_module(
            "qualification",
            matched_by_domain["qualification"],
            route="company_match",
            handling_mode="database_match",
        ),
        "commercial": _canonical_module(
            "commercial",
            commercial_items,
            route="display",
            handling_mode="manual_confirm",
        ),
        "evaluation": _canonical_module(
            "evaluation",
            evaluation_items,
            route="display",
            handling_mode="display",
        ),
        "contract": _canonical_module(
            "contract",
            contract_items,
            route="ignored_contract",
            handling_mode="display",
            ignored=True,
        ),
        "template": _canonical_module(
            "template",
            template_items,
            route="writing_task",
            handling_mode="writing_task",
        ),
        "project_info": _canonical_module(
            "project_info",
            project_items,
            route="display",
            handling_mode="display",
        ),
        "rejection": _canonical_module(
            "rejection",
            rejection_items,
            route="rejection_check",
            handling_mode="risk_check",
        ),
    }
    run = runtime_result.get("run") if isinstance(runtime_result, Mapping) else {}
    run = run if isinstance(run, Mapping) else {}
    return {
        "schemaVersion": REVIEW_OUTPUT_SCHEMA_VERSION,
        "producer": "workbuddy_agent_mcp",
        "source": {
            "provider": "workbuddy-agent-mcp",
            "agentStatus": status,
            "fallbackMode": fallback_mode,
            "runId": _text(run.get("runId"), 120),
            "model": _text(run.get("model"), 160),
        },
        "document": {
            "documentId": document_map.document_id,
            "documentVersionId": document_map.document_version_id,
            "mapId": document_map.map_id,
        },
        "modules": modules,
        "summary": {
            "technicalCount": modules["technical"]["count"],
            "qualificationCount": modules["qualification"]["count"],
            "commercialCount": modules["commercial"]["count"],
            "evaluationCount": modules["evaluation"]["count"],
            "contractIgnoredCount": modules["contract"]["count"],
            "templateCount": modules["template"]["count"],
            "projectInfoCount": modules["project_info"]["count"],
            "rejectionCount": modules["rejection"]["count"],
        },
        "matchStage": {
            "targetModules": list(MATCH_STAGE_TARGETS),
            "enterpriseMatchTargets": list(ENTERPRISE_MATCH_TARGETS),
            "riskCheckTargets": list(RISK_CHECK_TARGETS),
            "enterpriseMatchCount": (
                modules["technical"]["count"]
                + modules["qualification"]["count"]
            ),
            "riskCheckCount": modules["rejection"]["count"],
        },
    }


def _pack_list(pack: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = pack.get(key)
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _scope_list(pack: Mapping[str, Any], *path: str) -> list[dict[str, Any]]:
    value: Any = pack.get("scopeProcessing")
    for key in path:
        if not isinstance(value, Mapping):
            return []
        value = value.get(key)
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def build_review_output_from_analysis_pack(
    fine_review: Mapping[str, Any],
    *,
    producer: str = "",
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project any matching result into the eight-module frontend contract.

    WorkBuddy owns the strongest DocumentMap path, but the UI should not need
    to know whether the producer was WorkBuddy, builtin, or a future extractor.
    This adapter keeps ``fineReview.rows`` as compatibility data while making
    ``reviewOutput.modules`` the display contract.
    """

    extraction = (
        fine_review.get("extraction")
        if isinstance(fine_review.get("extraction"), Mapping)
        else {}
    )
    pack = fine_review.get("analysisPack") or extraction.get("analysisPack")
    pack = pack if isinstance(pack, Mapping) else {}

    for candidate in (
        fine_review.get("reviewOutput"),
        extraction.get("reviewOutput"),
        pack.get("reviewOutput"),
    ):
        if (
            isinstance(candidate, Mapping)
            and isinstance(candidate.get("modules"), Mapping)
        ):
            return dict(candidate)

    fine_rows = [
        dict(item)
        for item in (fine_review.get("rows") or [])
        if isinstance(item, Mapping)
    ]
    target_items = {
        "technical": _target_module_items(
            pack,
            "technical",
            route="company_match",
            handling_mode="database_match",
        ),
        "qualification": _target_module_items(
            pack,
            "qualification",
            route="company_match",
            handling_mode="database_match",
        ),
        "commercial": _target_module_items(
            pack,
            "commercial",
            route="display",
            handling_mode="manual_confirm",
        ),
        "evaluation": _target_module_items(
            pack,
            "evaluation",
            route="display",
            handling_mode="display",
        ),
        "template": _target_module_items(
            pack,
            "template",
            route="writing_task",
            handling_mode="writing_task",
        ),
        "project_info": _target_module_items(
            pack,
            "project_info",
            route="display",
            handling_mode="display",
        ),
        "rejection": _target_module_items(
            pack,
            "rejection",
            route="rejection_check",
            handling_mode="risk_check",
        ),
    }
    technical_items = _pack_list(pack, "technicalRequirements") or [
        row for row in fine_rows if _row_matches_domain(row, "technical")
    ] or target_items["technical"]
    qualification_items = _pack_list(pack, "qualificationRequirements") or [
        row for row in fine_rows if _row_matches_domain(row, "qualification")
    ] or target_items["qualification"]
    commercial_items = [
        *_pack_list(pack, "commercialTerms"),
        *_pack_list(pack, "submissionRequirements"),
        *_pack_list(pack, "milestones"),
        *_pack_list(pack, "feesAndGuarantees"),
    ] or target_items["commercial"] or _scope_list(pack, "commercialTerms")
    evaluation_items = (
        _pack_list(pack, "evaluationCriteria")
        or target_items["evaluation"]
        or _scope_list(pack, "evaluationCriteria")
    )
    template_items = _pack_list(pack, "templateIndex") or _scope_list(
        pack,
        "templates",
        "locators",
    ) or target_items["template"]
    project_items = _pack_list(pack, "projectInfo") or _scope_list(
        pack,
        "projectInfo",
        "fields",
    ) or target_items["project_info"]
    contract_items = _scope_list(pack, "contracts", "ignoredSections")
    rejection_items = _pack_list(pack, "disqualificationRules") or target_items["rejection"] or [
        row for row in fine_rows if _row_matches_domain(row, "rejection")
    ]
    if not rejection_items:
        rejection_items = [
            {
                "id": item.get("requirementId") or f"rejection-{index + 1}",
                "requirementId": item.get("requirementId") or f"rejection-{index + 1}",
                "title": item.get("title") or "",
                "text": item.get("detail") or item.get("title") or "",
                "detail": item.get("detail") or "",
                "sourceLocator": item.get("sourceLocator") or "",
            }
            for index, item in enumerate(fine_review.get("rejectionRisks") or [])
            if isinstance(item, Mapping)
        ]

    modules = {
        "technical": _canonical_module(
            "technical",
            technical_items,
            route="company_match",
            handling_mode="database_match",
        ),
        "qualification": _canonical_module(
            "qualification",
            qualification_items,
            route="company_match",
            handling_mode="database_match",
        ),
        "commercial": _canonical_module(
            "commercial",
            commercial_items,
            route="display",
            handling_mode="manual_confirm",
        ),
        "evaluation": _canonical_module(
            "evaluation",
            evaluation_items,
            route="display",
            handling_mode="display",
        ),
        "contract": _canonical_module(
            "contract",
            contract_items,
            route="ignored_contract",
            handling_mode="display",
            ignored=True,
        ),
        "template": _canonical_module(
            "template",
            template_items,
            route="writing_task",
            handling_mode="writing_task",
        ),
        "project_info": _canonical_module(
            "project_info",
            project_items,
            route="display",
            handling_mode="display",
        ),
        "rejection": _canonical_module(
            "rejection",
            rejection_items,
            route="rejection_check",
            handling_mode="risk_check",
        ),
    }
    source_payload = {
        "provider": _text(extraction.get("provider") or producer or "builtin", 120),
        "method": _text(extraction.get("method"), 120),
        "agentStatus": _text(extraction.get("agentStatus"), 40),
        "fallbackMode": _text(extraction.get("fallbackMode"), 40),
        "model": _text(extraction.get("modelUsed"), 160),
    }
    if source:
        source_payload.update(
            {
                str(key): value
                for key, value in source.items()
                if value not in (None, "", [], {})
            }
        )
    document = pack.get("document") if isinstance(pack.get("document"), Mapping) else {}
    pack_source = pack.get("source") if isinstance(pack.get("source"), Mapping) else {}
    return {
        "schemaVersion": REVIEW_OUTPUT_SCHEMA_VERSION,
        "producer": source_payload.get("provider") or producer or "builtin",
        "source": source_payload,
        "document": {
            "documentId": _text(
                pack.get("documentId")
                or document.get("documentId")
                or pack_source.get("documentId"),
                120,
            ),
            "documentVersionId": _text(
                pack.get("documentVersionId")
                or document.get("documentVersionId")
                or pack_source.get("documentVersionId"),
                140,
            ),
            "mapId": _text((pack.get("documentMap") or {}).get("mapId"), 120)
            if isinstance(pack.get("documentMap"), Mapping)
            else "",
        },
        "modules": modules,
        "summary": {
            "technicalCount": modules["technical"]["count"],
            "qualificationCount": modules["qualification"]["count"],
            "commercialCount": modules["commercial"]["count"],
            "evaluationCount": modules["evaluation"]["count"],
            "contractIgnoredCount": modules["contract"]["count"],
            "templateCount": modules["template"]["count"],
            "projectInfoCount": modules["project_info"]["count"],
            "rejectionCount": modules["rejection"]["count"],
        },
        "matchStage": {
            "targetModules": list(MATCH_STAGE_TARGETS),
            "enterpriseMatchTargets": list(ENTERPRISE_MATCH_TARGETS),
            "riskCheckTargets": list(RISK_CHECK_TARGETS),
            "enterpriseMatchCount": (
                modules["technical"]["count"]
                + modules["qualification"]["count"]
            ),
            "riskCheckCount": modules["rejection"]["count"],
        },
    }


def _runtime_config(ai: Mapping[str, Any]) -> DocumentMapRuntimeConfig:
    raw = ai.get("workbuddyAgentMcp")
    config = dict(raw) if isinstance(raw, Mapping) else {}
    config.pop("executor", None)
    config.pop("mcpConfigFlag", None)
    config["model"] = (
        _text(config.get("model"), 160)
        or CODEBUDDY_SDK_DEFAULT_MODEL
    )
    # The document-map task supplies its single Tender Review server explicitly.
    # Still load the user scope by default so models.json (the UINO custom model
    # registration) is available; deployments that must not inherit user
    # MCP/settings can explicitly set settingSources=[].
    config["settingSources"] = list(config.get("settingSources") or ["user"])
    # Propagate the deployment's OpenAI-compatible endpoint (UINO) so the SDK
    # can inject env vars and bypass the CodeBuddy.cn model-name collision.
    config["llmBaseUrl"] = _text(ai.get("baseUrl"), 240)
    config["llmApiKey"] = _text(ai.get("apiKey"), 240)
    return DocumentMapRuntimeConfig.model_validate(config)


def evaluate_tender_document_with_workbuddy_agent(
    *,
    text: str,
    document: Mapping[str, Any],
    company_knowledge: dict[str, Any],
    ai: Mapping[str, Any],
    source_file_id: str = "",
    project_id: str = "",
    document_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the isolated WorkBuddy map route and project into fineReview."""

    parsed = _document_ir(text, document, source_file_id=source_file_id)
    config = _runtime_config(ai)
    runtime = DocumentMapAgentRuntime(parsed, config=config)
    runtime_result = runtime.run()
    status = _text(runtime_result.get("status"), 40)
    raw_map = runtime_result.get("documentMap")
    if status not in {"completed", "needs_review"} or not isinstance(raw_map, Mapping):
        errors = runtime_result.get("errors") or ["WorkBuddy DocumentMap run failed."]
        raise WorkBuddyAgentMatchingError(
            str(errors[0]),
            runtime_result=runtime_result,
        )

    document_map = (
        raw_map
        if isinstance(raw_map, DocumentMap)
        else DocumentMap.model_validate(raw_map)
    )
    high_risk_ids = (
        _high_risk_segment_ids(parsed)
        if config.require_rejection_scan
        else []
    )
    validation = validate_document_map(
        document_map,
        sections=parsed.sections,
        segments=parsed.segments,
        tables=parsed.tables,
        document_id=parsed.document_id,
        document_version_id=parsed.document_version_id,
        high_risk_segment_ids=high_risk_ids,
    )
    if not validation.valid:
        details = "; ".join(
            f"{issue.code}: {issue.message}"
            for issue in validation.issues
            if issue.severity == "critical"
        )
        raise WorkBuddyAgentMatchingError(
            details or "WorkBuddy DocumentMap validation failed.",
            runtime_result=runtime_result,
        )
    document_map = validation.normalized_map
    scope_output = build_scope_processing_output(parsed, document_map)
    target_output = extract_target_requirements(parsed, document_map)
    target_extraction = target_output.extraction
    target_requirements = _requirement_payload(
        target_extraction,
        parsed,
        source_file_id=source_file_id,
    )
    rejection_requirements = _merge_rejection_requirements(
        _rejection_requirements(document_map),
        _builtin_rejection_requirements(parsed),
    )
    requirements = [*target_requirements, *rejection_requirements]

    warnings = list(
        dict.fromkeys(
            [
                *[str(item) for item in runtime_result.get("warnings") or []],
                *[
                    str(item)
                    for item in (
                        [
                            *scope_output.project_info.warnings,
                            *scope_output.templates.warnings,
                        ]
                    )
                    if str(item).strip()
                ],
                *[str(item) for item in target_output.warnings],
                *(
                    ["workbuddy_agent_needs_review"]
                    if status == "needs_review"
                    else []
                ),
            ]
        )
    )
    stats = {
        **target_output.stats,
        "runtimeStatus": status,
        "runtimeAttempt": int((runtime_result.get("metadata") or {}).get("attempt") or 1),
        "runtimeToolCallCount": len(runtime_result.get("toolCalls") or []),
        "runtimeTurnCount": int((runtime_result.get("run") or {}).get("turnCount") or 0),
        "extractionProvider": "workbuddy-agent-mcp",
        "extractionMethod": "workbuddy_agent_mcp",
        "sourceDocumentId": parsed.document_id,
        "sourceDocumentVersionId": parsed.document_version_id,
        "rejectionScanRequirementCount": len(rejection_requirements),
        "documentMapValidationWarningCount": len(validation.warnings),
    }
    agent_analysis = _scope_agent_analysis(
        scope_output.model_dump(mode="json", by_alias=True)
    )
    analysis_pack = build_analysis_pack(
        requirements=requirements,
        segments=[item.model_dump(mode="json", by_alias=True) for item in parsed.segments],
        sections=[item.model_dump(mode="json", by_alias=True) for item in parsed.sections],
        tables=[item.model_dump(mode="json", by_alias=True) for item in parsed.tables],
        agent_analysis=agent_analysis,
        source_file_id=source_file_id,
        source_file_hash=_text(document.get("contentHash"), 160),
        document_meta=document_meta,
        warnings=warnings,
        stats=stats,
        document_map=document_map,
    )
    analysis_pack["matchStageTargets"] = list(MATCH_STAGE_TARGETS)
    analysis_pack["enterpriseMatchTargets"] = list(ENTERPRISE_MATCH_TARGETS)
    analysis_pack["riskCheckTargets"] = list(RISK_CHECK_TARGETS)
    analysis_pack["matchStageRequirements"] = [*target_requirements, *rejection_requirements]
    analysis_pack["enterpriseMatchRequirements"] = list(target_requirements)
    analysis_pack["riskCheckRequirements"] = list(rejection_requirements)
    stats["matchStageRequirementCount"] = len(target_requirements) + len(
        rejection_requirements
    )
    stats["enterpriseMatchRequirementCount"] = len(target_requirements)
    analysis_pack["scopeProcessing"] = scope_output.model_dump(mode="json", by_alias=True)
    analysis_pack["agentRun"] = {
        "runId": (runtime_result.get("run") or {}).get("runId", ""),
        "status": status,
        "attempt": stats["runtimeAttempt"],
        "toolCallCount": stats["runtimeToolCallCount"],
        "turnCount": stats["runtimeTurnCount"],
    }
    analysis_pack = attach_document_map_to_analysis_pack(analysis_pack, document_map)

    extraction = {
        "requirements": requirements,
        "scope": scope_output.model_dump(mode="json", by_alias=True),
        "method": "workbuddy_agent_mcp",
        "provider": "workbuddy-agent-mcp",
        "extractionMethod": "workbuddy_agent_mcp",
        "extractionProvider": "workbuddy-agent-mcp",
        "modelUsed": _text((runtime_result.get("run") or {}).get("model"), 160),
        "warnings": warnings,
        "warning": "",
        "extractionWarning": "",
        "stats": stats,
        "analysisPack": analysis_pack,
        "packPath": "",
        "agentStatus": status,
        "agentRun": runtime_result.get("run") or {},
        "documentMap": document_map.model_dump(mode="json", by_alias=True),
    }
    fine_review = evaluate_tender_document(
        text,
        [item.model_dump(mode="json", by_alias=True) for item in parsed.segments],
        company_knowledge,
        {
            "enabled": False,
            "model": "",
            "auditEnabled": False,
        },
        source_file_id=source_file_id,
        sections=[item.model_dump(mode="json", by_alias=True) for item in parsed.sections],
        tables=[item.model_dump(mode="json", by_alias=True) for item in parsed.tables],
        project_id=project_id,
        document_meta=document_meta,
        extraction_override=extraction,
        allow_direct_llm_matching=False,
    )
    review_output = _build_review_output(
        document_map=document_map,
        scope_output=scope_output,
        target_requirements=target_requirements,
        fine_review=fine_review,
        runtime_result=runtime_result,
        status=status,
    )
    analysis_pack["reviewOutput"] = review_output
    fine_review["reviewOutput"] = review_output
    fine_review["extraction"] = extraction
    fine_review["analysisPack"] = analysis_pack
    extraction["reviewOutput"] = review_output
    return {
        "fineReview": fine_review,
        "extraction": extraction,
        "documentMap": document_map.model_dump(mode="json", by_alias=True),
        "runtime": runtime_result,
        "reviewOutput": review_output,
        "scopeProcessing": scope_output.model_dump(mode="json", by_alias=True),
        "targetProcessing": target_output.model_dump(mode="json", by_alias=True),
    }


__all__ = [
    "REVIEW_OUTPUT_SCHEMA_VERSION",
    "WorkBuddyAgentMatchingError",
    "build_review_output_from_analysis_pack",
    "evaluate_tender_document_with_workbuddy_agent",
]
