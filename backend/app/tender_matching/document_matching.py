from __future__ import annotations

import re
from typing import Any

from .contracts import (
    build_comparison,
    normalize_evidence,
    normalize_requirement,
    summarize_comparisons,
)
from .document_fine_matching import (
    CATEGORY_LABELS,
    extract_tender_requirements,
    _compact,
    _list_of_dicts,
)

SCORE_BY_STATUS = {
    "满足": 100,
    "部分满足": 78,
    "待补材料": 28,
    "待澄清": 42,
    "不满足": 0,
    "不适用": 100,
}

WEIGHTS = {
    "qualification": 18,
    "performance": 14,
    "personnel": 12,
    "financial": 10,
    "credit": 10,
    "region": 8,
    "compliance": 12,
    "technical": 22,
    "service_plan": 14,
    "commercial": 10,
    "document": 8,
    "pricing": 6,
    "contract": 4,
    "rejection": 6,
    "other": 6,
}


def _record_terms(record: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("name", "aliases", "industry", "description", "category", "keywordIds"):
        value = record.get(key)
        values = value if isinstance(value, list) else [value]
        for entry in values:
            cleaned = _compact(entry)
            if len(cleaned) >= 3:
                terms.append(cleaned)
            for part in re.split(r"[/、，,;；|与和及()（）\-]+", str(entry or "")):
                part = _compact(part)
                if len(part) >= 3:
                    terms.append(part)
    return list(dict.fromkeys(terms))


def _matching_records(requirement: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = _compact(requirement)
    matches: list[dict[str, Any]] = []
    for record in records:
        if any(term in text for term in _record_terms(record)):
            matches.append(record)
    return matches


def _candidate_records(category: str, requirement: str, knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    qualifications = _list_of_dicts(knowledge.get("qualifications"))
    references = _list_of_dicts(knowledge.get("projectReferences"))
    products = _list_of_dicts(knowledge.get("products"))
    capabilities = _list_of_dicts(knowledge.get("capabilities"))
    scenarios = _list_of_dicts(knowledge.get("industryScenarios"))
    directions = _list_of_dicts(knowledge.get("businessDirections"))
    if category == "qualification":
        return _matching_records(requirement, qualifications)
    if category == "performance":
        return _matching_records(requirement, [*references, *scenarios])
    if category in {"financial", "credit", "region"}:
        return _matching_records(requirement, [*qualifications, *capabilities, *directions])
    if category in {"technical", "commercial", "service_plan"}:
        return _matching_records(requirement, [*products, *capabilities, *scenarios, *directions])
    if category == "compliance":
        return _matching_records(
            requirement,
            [*qualifications, *products, *capabilities, *references, *scenarios, *directions],
        )
    return []


def _evidence_for_records(records: list[dict[str, Any]], knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("evidences"))}
    material_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("materials"))}
    source_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("sourceFiles"))}
    evidence_material_map: dict[str, str] = {}
    for material_id, material in material_map.items():
        for evidence_id in material.get("evidenceIds") or []:
            evidence_material_map.setdefault(str(evidence_id), material_id)
    resolved: list[dict[str, Any]] = []
    for record in records:
        evidence_ids = list(record.get("evidenceIds") or [])
        for material_id in record.get("materialIds") or []:
            evidence_ids.extend(material_map.get(str(material_id), {}).get("evidenceIds") or [])
        if not evidence_ids:
            resolved.append(
                {
                    "id": str(record.get("id", "")),
                    "name": str(record.get("name", "")),
                    "status": str(record.get("status", "资料已建模")),
                    "level": str(record.get("evidenceLevel", "")),
                    "locator": "",
                    "sourceType": "knowledge_object",
                    "materialId": str((record.get("materialIds") or [""])[0]),
                    "sourceFiles": [],
                }
            )
            continue
        for evidence_id in evidence_ids:
            evidence = evidence_map.get(str(evidence_id))
            if not evidence:
                continue
            resolved.append(
                {
                    "id": str(evidence.get("id", "")),
                    "name": str(evidence.get("name", "")),
                    "status": str(evidence.get("status", "")),
                    "level": str(evidence.get("level", "")),
                    "locator": str(evidence.get("locator", "")),
                    "sourceType": "evidence",
                    "materialId": evidence_material_map.get(str(evidence.get("id", "")), ""),
                    "sourceFileIds": [str(source_id) for source_id in evidence.get("sourceIds", [])],
                    "sourceFiles": [
                        str(source_map.get(str(source_id), {}).get("name", source_id))
                        for source_id in evidence.get("sourceIds", [])
                    ],
                    "excerpt": str(evidence.get("excerpt", "")),
                    "validFrom": str(evidence.get("validFrom", "")),
                    "validUntil": str(evidence.get("validUntil", "")),
                }
            )
    unique = {item["id"] or item["name"]: item for item in resolved}
    return list(unique.values())[:6]


def _evidence_summary(evidences: list[dict[str, Any]]) -> str:
    if not evidences:
        return "企业资料库未找到可直接关联的证据"
    parts = []
    for item in evidences:
        locator = f"；{item['locator']}" if item.get("locator") else ""
        parts.append(f"{item['name']}（{item.get('status') or '状态待核验'}{locator}）")
    return "；".join(parts)


def _status_from_evidence(
    category: str,
    matched: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
) -> str:
    if category in {"document", "pricing", "contract", "rejection"}:
        return "待澄清"
    if not matched:
        if category in {
            "qualification",
            "performance",
            "personnel",
            "financial",
            "credit",
            "region",
            "technical",
            "service_plan",
            "commercial",
        }:
            return "待补材料"
        return "待澄清"
    direct_evidences = [
        normalize_evidence(item)
        for item in evidences
        if item.get("sourceType") == "evidence"
    ]
    if any(item.get("isVerified") for item in direct_evidences):
        return "满足"
    if direct_evidences and all(item.get("isExpired") for item in direct_evidences):
        return "不满足"
    return "待补材料" if direct_evidences or matched else "待澄清"


def _suggestion_for_status(status: str, category: str) -> str:
    if category == "rejection":
        return "请人工核对否决条件、格式附件、签章和递交要求，当前不自动给出通过结论。"
    if category == "compliance":
        return "请逐项确认是否实质性响应招标文件，并核对偏离表、承诺函和对应支撑材料。"
    if category == "service_plan":
        return "请编制项目理解、实施计划、服务保障、培训或应急方案，并关联可复核的同类交付材料。"
    if category == "document":
        return "按招标文件逐项核验格式、签章、密封、附件和递交要求。"
    if category == "pricing":
        return "请商务人员核对最高限价、报价口径、税率和报价表要求。"
    if category == "contract":
        return "请业务和法务确认交付、验收、付款、违约及履约责任。"
    return {
        "满足": "已找到已核验的关联证据，递交前仍应复核原件及适用性。",
        "部分满足": "已找到关联本体对象和资料来源，请复核项目适用性与证据原件。",
        "待补材料": "资料库暂未形成可直接核验的材料，请补充材料或确认是否尚未录入。",
        "待澄清": "当前要求与本体对象无法形成明确对应，请阅读上下文后人工澄清。",
        "不满足": "当前未发现可以支持该要求的有效证据，请复核资料库和招标要求。",
        "不适用": "该要求已确认不适用于当前投标主体或本次响应。",
    }[status]


def _risk_for_status(status: str) -> str:
    if status in {"满足", "不适用"}:
        return "低"
    if status in {"部分满足", "待澄清"}:
        return "中"
    return "高"


def summarize_fine_review(rows: list[dict[str, Any]], extraction: dict[str, Any]) -> dict[str, Any]:
    weighted_score = 0.0
    total_weight = 0
    unresolved_mandatory = []
    for row in rows:
        category = str(row.get("category") or "other")
        status = str(row.get("status") or "待澄清")
        weight = WEIGHTS.get(category, WEIGHTS["other"])
        total_weight += weight
        weighted_score += weight * SCORE_BY_STATUS.get(status, 35)
        if row.get("mandatory") and status in {"部分满足", "待补材料", "待澄清", "不满足"}:
            unresolved_mandatory.append(row)

    score = round(weighted_score / total_weight) if total_weight else 0
    rejection_risks = [
        row for row in rows
        if row.get("isRejection") and row.get("status") not in {"满足", "不适用"}
    ]
    hard_blockers = [
        {
            "requirementId": str(row.get("id", "")),
            "title": f"{row.get('kind', CATEGORY_LABELS['other'])}：{row.get('status', '待澄清')}",
            "detail": str(row.get("requirement", "")),
            "sourceLocator": str(row.get("sourceLocator", "")),
        }
        for row in rejection_risks
        if row.get("status") == "不满足"
    ]
    if hard_blockers:
        recommendation = "不建议参与"
    elif rejection_risks:
        recommendation = "人工重点评估"
    elif score >= 85 and not unresolved_mandatory:
        recommendation = "建议进入正式投标准备"
    elif score >= 70:
        recommendation = "有条件参与，先补材料"
    elif score >= 55:
        recommendation = "人工重点评估"
    else:
        recommendation = "暂不建议参与"

    risks = [
        {
            "level": str(row.get("risk", "中")),
            "title": f"{row.get('kind', CATEGORY_LABELS['other'])}：{row.get('status', '待澄清')}",
            "detail": f"{row.get('requirement', '')}。{row.get('suggestion', '')}",
            "sourceLocator": str(row.get("sourceLocator", "")),
        }
        for row in rows
        if row.get("risk") != "低"
    ][:30]
    summary = summarize_comparisons(rows)

    return {
        "rows": rows,
        "risks": risks,
        "score": score,
        "decision": recommendation,
        "recommendation": recommendation,
        "clauseCount": len(rows),
        "statusCounts": summary["statusCounts"],
        "summary": summary,
        "hardBlockers": hard_blockers,
        "rejectionRisks": [
            {
                "requirementId": str(row.get("requirementId", "")),
                "title": f"{row.get('kind', CATEGORY_LABELS['other'])}：{row.get('status', '待澄清')}",
                "detail": str(row.get("rawText", "")),
                "sourceLocator": str(row.get("sourceLocator", "")),
            }
            for row in rejection_risks
        ],
        "unresolvedMandatory": [
            {
                "requirementId": str(row.get("id", "")),
                "title": str(row.get("kind", CATEGORY_LABELS["other"])),
                "detail": str(row.get("requirement", "")),
                "sourceLocator": str(row.get("sourceLocator", "")),
            }
            for row in unresolved_mandatory
        ],
        "extraction": extraction,
    }


def update_fine_review_status(
    fine_review: dict[str, Any],
    row_id: str,
    status: str,
) -> dict[str, Any] | None:
    if status not in SCORE_BY_STATUS:
        raise ValueError(f"不支持的匹配状态：{status}")
    rows = fine_review.get("rows")
    if not isinstance(rows, list):
        return None
    target = next(
        (row for row in rows if isinstance(row, dict) and str(row.get("id", "")) == row_id),
        None,
    )
    if target is None:
        return None
    target["status"] = status
    target["risk"] = _risk_for_status(status)
    target["riskLevel"] = target["risk"]
    target["suggestion"] = _suggestion_for_status(status, str(target.get("category") or "other"))
    target["manualStatus"] = status
    target["statusSource"] = "人工审核"
    target["reviewStatus"] = "已人工复核"
    target["reason"] = f"人工复核已将匹配状态更新为“{status}”。"
    extraction = fine_review.get("extraction")
    if not isinstance(extraction, dict):
        extraction = {}
    return summarize_fine_review(rows, extraction)


def evaluate_tender_document(
    text: str,
    segments: list[dict[str, Any]],
    company_knowledge: dict[str, Any],
    ai: dict[str, Any],
    source_file_id: str = "",
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    extraction = extract_tender_requirements(
        text,
        segments,
        ai,
        sections,
        tables,
    )
    rows: list[dict[str, Any]] = []

    for index, requirement in enumerate(extraction["requirements"], start=1):
        category = str(requirement.get("category") or "other")
        kind = CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"])
        requirement_contract = normalize_requirement(
            {**requirement, "requirementId": f"req-{index:03d}"},
            source_stage="tender_document",
            layer=3,
            kind=kind,
            source_file_id=source_file_id,
        )
        requirement_text = str(requirement_contract["rawText"])
        matched = _candidate_records(category, requirement_text, company_knowledge)
        evidences = _evidence_for_records(matched, company_knowledge)
        status = _status_from_evidence(category, matched, evidences)
        reason = {
            "满足": "已找到经过核验且当前有效的直接企业证据。",
            "部分满足": "已找到相关企业对象或证据，但尚不能证明全部条件。",
            "待补材料": "已找到相关企业对象，但直接证明材料尚未核验或未完整入库。",
            "待澄清": "该要求需要结合上下文、响应文件编制规则或人工判断确认。",
            "不满足": "现有直接证据显示关键条件已失效或明确不符合。",
            "不适用": "该要求经人工确认不适用于本次响应。",
        }[status]
        row = build_comparison(
            requirement_contract,
            status=status,
            reason=reason,
            matched_objects=matched,
            evidences=evidences,
            risk=_risk_for_status(status),
            suggestion=_suggestion_for_status(status, category),
        )
        row["evidence"] = _evidence_summary(row["evidenceItems"])
        rows.append(row)
    return summarize_fine_review(
        rows,
        {
            "method": extraction["extractionMethod"],
            "modelUsed": extraction["modelUsed"],
            "warning": extraction["extractionWarning"],
            "warnings": extraction.get("warnings", []),
            "scope": extraction.get("scope", {}),
        },
    )
