from __future__ import annotations

import re
from datetime import date
from typing import Any


MATCH_STATUSES = (
    "满足",
    "部分满足",
    "不满足",
    "待补材料",
    "待澄清",
    "不适用",
)

EVIDENCE_NEEDS = {
    "qualification": "qualification_certificate",
    "performance": "project_performance",
    "personnel": "personnel_record",
    "financial": "financial_record",
    "region": "service_region",
    "credit": "credit_record",
    "compliance": "compliance_confirmation",
    "technical": "product_or_capability",
    "service_plan": "service_plan_reference",
    "commercial": "company_commitment",
    "document": "response_document",
    "pricing": "commercial_confirmation",
    "contract": "legal_confirmation",
    "rejection": "compliance_confirmation",
    "other": "supporting_material",
}

RESPONSE_TYPES = {
    "qualification": "evidence",
    "performance": "evidence",
    "personnel": "evidence",
    "financial": "evidence",
    "region": "evidence",
    "credit": "evidence",
    "compliance": "mandatory_confirmation",
    "technical": "technical_response",
    "service_plan": "service_plan_response",
    "commercial": "commercial_response",
    "document": "document_compliance",
    "pricing": "pricing_response",
    "contract": "manual_confirmation",
    "rejection": "mandatory_confirmation",
    "other": "manual_confirmation",
}

# 分类 -> 前端四大模块映射（人员、其他/流程类暂不展示，返回空串由前端过滤）
CATEGORY_MODULES = {
    "qualification": "资格审查要求",
    "compliance": "资格审查要求",
    "credit": "资格审查要求",
    "financial": "资格审查要求",
    "region": "资格审查要求",
    "rejection": "资格审查要求",
    "commercial": "商务和服务要求",
    "pricing": "商务和服务要求",
    "contract": "商务和服务要求",
    "service_plan": "商务和服务要求",
    "performance": "商务和服务要求",
    "document": "商务和服务要求",
    "technical": "技术要求",
}

COMPANY_EVIDENCE_NEEDS = {
    "qualification_certificate",
    "project_performance",
    "personnel_record",
    "financial_record",
    "service_region",
    "credit_record",
    "product_or_capability",
    "service_plan_reference",
    "company_commitment",
    "supporting_material",
}

POSITIVE_VERIFICATION_TERMS = ("已核验", "核验通过", "当前有效", "已上传原件")
PENDING_TERMS = ("待核验", "待补", "待确认", "待上传", "来源已登记", "资料已建模", "已建模")
HISTORICAL_TERMS = ("历史", "不能替代")
INVALID_TERMS = ("过期", "失效", "作废", "已撤销")


def safe_text(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def requirement_importance(category: str, mandatory: bool, is_rejection: bool, text: str) -> str:
    if is_rejection or any(term in text for term in ("★", "废标", "无效投标", "投标无效", "否决")):
        return "critical"
    if mandatory and category in {"qualification", "technical", "document", "pricing"}:
        return "high"
    if mandatory:
        return "medium"
    return "normal"


def normalize_requirement(
    raw: dict[str, Any],
    *,
    source_stage: str,
    layer: int,
    kind: str,
    source_file_id: str = "",
) -> dict[str, Any]:
    raw_text = safe_text(
        raw.get("rawText")
        or raw.get("text")
        or raw.get("requirement")
        or raw.get("sourceText")
    )
    category = safe_text(raw.get("category") or "other", 40).lower() or "other"
    requirement_id = safe_text(
        raw.get("requirementId") or raw.get("id") or f"{source_stage}-requirement",
        100,
    )
    mandatory = bool(raw.get("mandatory", True))
    is_rejection = bool(raw.get("isRejection", category == "rejection"))
    importance = safe_text(raw.get("importance"), 30) or requirement_importance(
        category,
        mandatory,
        is_rejection,
        raw_text,
    )
    normalized_requirement = safe_text(raw.get("normalizedRequirement") or raw_text)
    return {
        # Compatibility fields retained while existing workspaces are migrated.
        "id": requirement_id,
        "kind": kind,
        "requirement": raw_text,
        "sourceText": raw_text,
        # Unified requirement contract.
        "requirementId": requirement_id,
        "layer": layer,
        "sourceStage": source_stage,
        "category": category,
        "module": CATEGORY_MODULES.get(category, ""),
        "section": safe_text(raw.get("section") or kind, 160),
        "rawText": raw_text,
        "normalizedRequirement": normalized_requirement,
        "importance": importance,
        "mandatory": mandatory,
        "isRejection": is_rejection,
        "requirementGroup": safe_text(raw.get("requirementGroup") or category, 80),
        "evidenceNeed": safe_text(raw.get("evidenceNeed") or EVIDENCE_NEEDS.get(category, "supporting_material"), 80),
        "responseType": safe_text(raw.get("responseType") or RESPONSE_TYPES.get(category, "manual_confirmation"), 80),
        "sourceFileId": safe_text(raw.get("sourceFileId") or source_file_id, 160),
        "sourceLocator": safe_text(raw.get("sourceLocator"), 120),
        "parentRequirementId": safe_text(raw.get("parentRequirementId"), 100),
        "clauseIndex": raw.get("clauseIndex"),
        "clauseCount": raw.get("clauseCount"),
        "sourceSegmentId": safe_text(raw.get("sourceSegmentId"), 120),
        "quantity": raw.get("quantity"),
        "timeLimit": safe_text(raw.get("timeLimit"), 160),
        "confidence": raw.get("confidence", 0),
    }


def _date_has_expired(value: Any) -> bool:
    text = safe_text(value, 40)
    if not text:
        return False
    try:
        return date.fromisoformat(text[:10]) < date.today()
    except ValueError:
        return False


def normalize_evidence(item: dict[str, Any]) -> dict[str, Any]:
    status = safe_text(item.get("verificationStatus") or item.get("status"), 100)
    evidence_id = safe_text(item.get("evidenceId") or item.get("id"), 100)
    level = safe_text(item.get("evidenceLevel") or item.get("level"), 40)
    source_file_ids = [
        safe_text(value, 120)
        for value in (item.get("sourceFileIds") or [])
        if safe_text(value, 120)
    ]
    source_files = [
        safe_text(value, 240)
        for value in (item.get("sourceFiles") or [])
        if safe_text(value, 240)
    ]
    is_historical = bool(item.get("isHistorical")) or any(term in status for term in HISTORICAL_TERMS)
    is_expired = bool(item.get("isExpired")) or _date_has_expired(item.get("validUntil")) or any(
        term in status for term in INVALID_TERMS
    )
    is_verified = (
        not is_historical
        and not is_expired
        and any(term in status for term in POSITIVE_VERIFICATION_TERMS)
        and not any(term in status for term in PENDING_TERMS)
    )
    source_type = safe_text(item.get("sourceType"), 40) or (
        "evidence" if evidence_id.upper().startswith("EVD-") else "knowledge_object"
    )
    return {
        # Compatibility fields.
        "id": evidence_id,
        "level": level,
        "locator": safe_text(item.get("sourceLocator") or item.get("locator"), 160),
        # Unified evidence contract.
        "evidenceId": evidence_id,
        "materialId": safe_text(item.get("materialId"), 100),
        "name": safe_text(item.get("name"), 300),
        "sourceType": source_type,
        "status": status,
        "verificationStatus": status,
        "evidenceLevel": level,
        "validFrom": safe_text(item.get("validFrom"), 40),
        "validUntil": safe_text(item.get("validUntil"), 40),
        "sourceFileIds": source_file_ids,
        "sourceFiles": source_files,
        "sourceLocator": safe_text(item.get("sourceLocator") or item.get("locator"), 160),
        "excerpt": safe_text(item.get("excerpt"), 600),
        "isHistorical": is_historical,
        "isExpired": is_expired,
        "isVerified": is_verified,
    }


def evidence_coverage_status(evidences: list[dict[str, Any]]) -> str:
    direct = [item for item in evidences if item.get("sourceType") == "evidence"]
    if any(item.get("isVerified") for item in direct):
        return "已核验证据"
    if direct:
        return "待核验证据"
    if evidences:
        return "仅对象关联"
    return "未覆盖"


def build_comparison(
    requirement: dict[str, Any],
    *,
    status: str,
    reason: str,
    matched_objects: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
    risk: str,
    suggestion: str,
    status_source: str = "模型/规则",
) -> dict[str, Any]:
    normalized_evidences = [normalize_evidence(item) for item in evidences]
    matched_material_ids = list(
        dict.fromkeys(
            safe_text(material_id, 100)
            for item in matched_objects
            for material_id in (item.get("materialIds") or [])
            if safe_text(material_id, 100)
        )
    )
    evidence_ids = list(
        dict.fromkeys(
            str(item.get("evidenceId", ""))
            for item in normalized_evidences
            if item.get("sourceType") == "evidence" and item.get("evidenceId")
        )
    )
    requirement_id = str(requirement.get("requirementId") or requirement.get("id") or "")
    row = {
        **requirement,
        "comparisonId": f"CMP-{requirement_id}",
        "requirementId": requirement_id,
        "status": status,
        "reason": reason,
        "matchedObjectIds": [
            safe_text(item.get("id"), 100)
            for item in matched_objects
            if safe_text(item.get("id"), 100)
        ],
        "matchedMaterialIds": matched_material_ids,
        "evidenceIds": evidence_ids,
        "evidenceItems": normalized_evidences,
        "evidenceCoverageStatus": evidence_coverage_status(normalized_evidences),
        "risk": risk,
        "riskLevel": risk,
        "suggestion": suggestion,
        "reviewStatus": "已人工复核" if status_source == "人工审核" else "待人工复核",
        "statusSource": status_source,
    }
    return row


def summarize_comparisons(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = {status: 0 for status in MATCH_STATUSES}
    for row in rows:
        status = str(row.get("status") or "待澄清")
        status_counts[status] = status_counts.get(status, 0) + 1

    evidence_required = [
        row for row in rows
        if row.get("evidenceNeed") in COMPANY_EVIDENCE_NEEDS
    ]
    evidence_covered = [
        row for row in evidence_required
        if row.get("evidenceCoverageStatus") in {"待核验证据", "已核验证据"}
    ]
    verified_evidence = [
        row for row in evidence_required
        if row.get("evidenceCoverageStatus") == "已核验证据"
    ]
    mandatory_rows = [row for row in rows if row.get("mandatory")]
    closed_mandatory = [
        row for row in mandatory_rows
        if row.get("status") in {"满足", "不适用"}
    ]
    rejection_rows = [row for row in rows if row.get("isRejection")]
    rejection_risks = [
        row for row in rejection_rows
        if row.get("status") not in {"满足", "不适用"}
    ]
    hard_blockers = [
        row for row in rejection_rows
        if row.get("status") == "不满足"
    ]

    def percentage(numerator: int, denominator: int) -> int:
        return round(numerator * 100 / denominator) if denominator else 0

    return {
        "totalRequirementCount": len(rows),
        "statusCounts": status_counts,
        "mandatoryCount": len(mandatory_rows),
        "mandatoryClosedCount": len(closed_mandatory),
        "mandatoryClosureRate": percentage(len(closed_mandatory), len(mandatory_rows)),
        "evidenceRequiredCount": len(evidence_required),
        "evidenceCoveredCount": len(evidence_covered),
        "verifiedEvidenceCount": len(verified_evidence),
        "evidenceCoverageRate": percentage(len(evidence_covered), len(evidence_required)),
        "rejectionCount": len(rejection_rows),
        "rejectionRiskCount": len(rejection_risks),
        "hardBlockerCount": len(hard_blockers),
        "pendingMaterialCount": status_counts.get("待补材料", 0),
        "pendingClarificationCount": status_counts.get("待澄清", 0),
        "notSatisfiedCount": status_counts.get("不满足", 0),
    }
