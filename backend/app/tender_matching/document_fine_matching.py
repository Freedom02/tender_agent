from __future__ import annotations

import json
import re
from typing import Any

from .eligibility import call_openai_compatible


ALLOWED_CATEGORIES = {
    "qualification",
    "performance",
    "personnel",
    "technical",
    "commercial",
    "document",
    "pricing",
    "contract",
    "rejection",
    "other",
}

CATEGORY_LABELS = {
    "qualification": "资格审查",
    "performance": "业绩要求",
    "personnel": "人员要求",
    "technical": "技术要求",
    "commercial": "商务要求",
    "document": "文件格式",
    "pricing": "报价要求",
    "contract": "合同条款",
    "rejection": "否决条款",
    "other": "其他要求",
}

SCORE_BY_STATUS = {
    "满足": 100,
    "部分满足": 78,
    "待确认": 52,
    "待补材料": 28,
    "待澄清": 42,
    "不满足": 0,
}

WEIGHTS = {
    "qualification": 18,
    "performance": 14,
    "personnel": 12,
    "technical": 22,
    "commercial": 10,
    "document": 8,
    "pricing": 6,
    "contract": 4,
    "rejection": 6,
    "other": 6,
}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _safe_text(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]


def _source_locator(item: dict[str, Any], index: int) -> str:
    return _safe_text(item.get("sourceLocator"), 80) or f"第{index + 1}段"


def _category_for(text: str) -> str:
    value = _compact(text)
    if any(term in value for term in ("无效投标", "否决", "废标", "拒绝", "不予受理", "不得参加")):
        return "rejection"
    if any(term in value for term in ("报价", "最高限价", "控制价", "下浮", "折扣", "费用")):
        return "pricing"
    if any(term in value for term in ("合同", "付款", "结算", "履约", "违约", "验收")):
        return "contract"
    if any(term in value for term in ("投标文件", "响应文件", "签字", "盖章", "格式", "密封", "装订")):
        return "document"
    if any(term in value for term in ("项目经理", "项目负责人", "驻场", "人员", "团队", "工程师")):
        return "personnel"
    if any(term in value for term in ("类似业绩", "类似项目", "项目业绩", "合同业绩", "案例")):
        return "performance"
    if any(term in value for term in ("资质", "证书", "认证", "许可证", "营业执照", "iso", "itss", "cmmi")):
        return "qualification"
    if any(term in value for term in ("商务", "交付期", "工期", "售后", "质保", "服务期")):
        return "commercial"
    if any(
        term in value
        for term in (
            "技术",
            "功能",
            "性能",
            "系统",
            "平台",
            "接口",
            "数据",
            "三维",
            "数字孪生",
            "可视化",
            "智能体",
            "实施方案",
        )
    ):
        return "technical"
    return "other"


def _is_heading(text: str) -> bool:
    value = _safe_text(text, 200)
    return len(value) <= 42 and bool(
        re.match(r"^(?:第?[一二三四五六七八九十]+[章节、.]|\d+(?:\.\d+)*[、.]|[（(]\d+[)）])", value)
    )


def _is_requirement(text: str) -> bool:
    value = _compact(text)
    if len(value) < 8:
        return False
    terms = (
        "应",
        "须",
        "必须",
        "不得",
        "提供",
        "具备",
        "具有",
        "不少于",
        "不低于",
        "至少",
        "投标人",
        "供应商",
        "响应文件",
        "报价",
        "合同",
        "服务期",
        "交付",
        "验收",
        "无效投标",
        "否决",
    )
    return any(term in value for term in terms) or bool(
        re.match(r"^(?:\d+(?:\.\d+)*[、.]|[（(]\d+[)）])", _safe_text(text, 100))
    )


def _section_for(text: str, current: str) -> str:
    label = _category_for(text)
    if _is_heading(text) and label != "other":
        return _safe_text(text, 120)
    return current


def _requirement_from_segment(
    source: dict[str, Any],
    index: int,
    section: str,
) -> dict[str, Any] | None:
    text = _safe_text(source.get("text"), 1200)
    if not _is_requirement(text):
        return None
    category = _category_for(text)
    return {
        "id": f"rule-{index + 1:03d}",
        "category": category,
        "text": text,
        "section": section,
        "sourceLocator": _source_locator(source, index),
        "mandatory": True,
        "isRejection": category == "rejection",
        "confidence": 0.64,
    }


def extract_tender_requirements_by_rule(text: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    prepared = segments or [
        {"text": line, "sourceLocator": f"第{index + 1}行"}
        for index, line in enumerate(text.splitlines())
        if line.strip()
    ]
    requirements: list[dict[str, Any]] = []
    current_section = ""
    for index, segment in enumerate(prepared):
        segment_text = _safe_text(segment.get("text"), 1200)
        if not segment_text:
            continue
        current_section = _section_for(segment_text, current_section)
        requirement = _requirement_from_segment(segment, len(requirements), current_section)
        if requirement:
            requirements.append(requirement)
        if len(requirements) >= 80:
            break

    return {
        "requirements": requirements,
        "warnings": [] if requirements else ["规则未识别到可逐条核验的招标要求，请人工检查原始文件。"],
    }


def _json_from_model(content: str) -> dict[str, Any] | None:
    clean = re.sub(r"^```(?:json)?\s*", "", str(content or "").strip(), flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            result = json.loads(clean[start : end + 1])
        except json.JSONDecodeError:
            return None
    return result if isinstance(result, dict) else None


def _normalize_model_extraction(
    value: dict[str, Any],
    available_locators: set[str],
) -> dict[str, Any] | None:
    raw_requirements = value.get("requirements")
    if not isinstance(raw_requirements, list):
        return None
    requirements: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_requirements[:100]):
        if not isinstance(raw, dict):
            continue
        text = _safe_text(raw.get("text"), 1200)
        if not text:
            continue
        category = str(raw.get("category") or _category_for(text)).lower()
        if category not in ALLOWED_CATEGORIES:
            category = _category_for(text)
        locator = _safe_text(raw.get("sourceLocator"), 80)
        if locator not in available_locators:
            locator = ""
        confidence = raw.get("confidence", 0.72)
        try:
            confidence = min(max(float(confidence), 0), 1)
        except (ValueError, TypeError):
            confidence = 0.72
        requirements.append(
            {
                "id": str(raw.get("id") or f"llm-{index + 1:03d}"),
                "category": category,
                "text": text,
                "section": _safe_text(raw.get("section"), 120) or CATEGORY_LABELS[category],
                "sourceLocator": locator,
                "mandatory": bool(raw.get("mandatory", True)),
                "isRejection": bool(raw.get("isRejection", category == "rejection")),
                "confidence": confidence,
            }
        )
    if not requirements:
        return None
    return {
        "requirements": requirements,
        "warnings": [_safe_text(item, 240) for item in value.get("warnings", []) if _safe_text(item, 240)][:8],
    }


def extract_tender_requirements(
    text: str,
    segments: list[dict[str, Any]],
    ai: dict[str, Any],
) -> dict[str, Any]:
    rule_result = extract_tender_requirements_by_rule(text, segments)
    base_url = str(ai.get("baseUrl") or "").strip()
    api_key = str(ai.get("apiKey") or "").strip()
    model = str(ai.get("model") or "").strip()
    if ai.get("enabled") is False or not (base_url and api_key and model):
        return {
            **rule_result,
            "extractionMethod": "rule",
            "modelUsed": "",
            "extractionWarning": "未配置大模型，已使用规则拆解条款。",
        }

    compact_segments = []
    for index, segment in enumerate(segments[:500]):
        content = _safe_text(segment.get("text"), 800)
        if content:
            compact_segments.append(f"[{_source_locator(segment, index)}] {content}")
    prompt = (
        "你是招标文件条款拆解助手。仅依据给定原文，拆成可以逐条核验的要求。"
        "不得判断企业是否满足，不得补写原文没有的内容。"
        "输出严格 JSON，不使用 Markdown："
        '{"requirements":[{"id":"req-001","category":"qualification|performance|personnel|technical|commercial|document|pricing|contract|rejection|other",'
        '"text":"原文要求","section":"所在章节","sourceLocator":"必须原样引用输入方括号中的定位",'
        '"mandatory":true,"isRejection":false,"confidence":0.0}],"warnings":[]}'
        "。复合条款拆成多个独立 requirement。"
    )
    result = call_openai_compatible(
        ai,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "招标文件片段如下：\n" + "\n".join(compact_segments)[:50000]},
        ],
        max_tokens=5000,
        temperature=0,
    )
    if result.get("success"):
        available_locators = {_source_locator(item, index) for index, item in enumerate(segments)}
        normalized = _normalize_model_extraction(_json_from_model(result.get("content", "")) or {}, available_locators)
        if normalized:
            return {
                **normalized,
                "extractionMethod": "llm",
                "modelUsed": result.get("model", ""),
                "extractionWarning": "",
            }
        warning = "模型未返回可用 JSON，已回退到规则拆解。"
    else:
        warning = f"{result.get('error', '模型调用失败')}，已回退到规则拆解。"
    return {
        **rule_result,
        "extractionMethod": "rule",
        "modelUsed": "",
        "extractionWarning": warning,
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
    if category in {"technical", "commercial"}:
        return _matching_records(requirement, [*products, *capabilities, *scenarios, *directions])
    return []


def _evidence_for_records(records: list[dict[str, Any]], knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("evidences"))}
    material_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("materials"))}
    source_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("sourceFiles"))}
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
                    "sourceFiles": [
                        str(source_map.get(str(source_id), {}).get("name", source_id))
                        for source_id in evidence.get("sourceIds", [])
                    ],
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
        return "待确认"
    if not matched:
        if category in {"qualification", "performance", "personnel"}:
            return "待补材料"
        return "待澄清"
    state_text = " ".join(
        [str(item.get("status", "")) for item in matched]
        + [str(item.get("status", "")) for item in evidences]
    )
    if any(word in state_text for word in ("待核验", "待补", "历史", "不能替代", "待确认")):
        return "待确认"
    if any(word in state_text for word in ("已核验", "有效", "已上传原件")):
        return "满足"
    return "部分满足"


def _suggestion_for_status(status: str, category: str) -> str:
    if category == "rejection":
        return "请人工核对否决条件、格式附件、签章和递交要求，当前不自动给出通过结论。"
    if category == "document":
        return "按招标文件逐项核验格式、签章、密封、附件和递交要求。"
    if category == "pricing":
        return "请商务人员核对最高限价、报价口径、税率和报价表要求。"
    if category == "contract":
        return "请业务和法务确认交付、验收、付款、违约及履约责任。"
    return {
        "满足": "已找到已核验的关联证据，递交前仍应复核原件及适用性。",
        "部分满足": "已找到关联本体对象和资料来源，请复核项目适用性与证据原件。",
        "待确认": "存在关联资料，但有效期、版本、项目适用性或原件状态仍需人工核验。",
        "待补材料": "资料库暂未形成可直接核验的材料，请补充材料或确认是否尚未录入。",
        "待澄清": "当前要求与本体对象无法形成明确对应，请阅读上下文后人工澄清。",
        "不满足": "当前未发现可以支持该要求的有效证据，请复核资料库和招标要求。",
    }[status]


def _risk_for_status(status: str) -> str:
    if status == "满足":
        return "低"
    if status in {"部分满足", "待确认"}:
        return "中"
    return "高"


def evaluate_tender_document(
    text: str,
    segments: list[dict[str, Any]],
    company_knowledge: dict[str, Any],
    ai: dict[str, Any],
) -> dict[str, Any]:
    extraction = extract_tender_requirements(text, segments, ai)
    rows: list[dict[str, Any]] = []
    weighted_score = 0.0
    total_weight = 0
    unresolved_mandatory: list[dict[str, Any]] = []

    for requirement in extraction["requirements"]:
        category = str(requirement.get("category") or "other")
        matched = _candidate_records(category, str(requirement.get("text", "")), company_knowledge)
        evidences = _evidence_for_records(matched, company_knowledge)
        status = _status_from_evidence(category, matched, evidences)
        row = {
            "id": str(requirement.get("id", "")),
            "kind": CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"]),
            "category": category,
            "section": str(requirement.get("section", "")),
            "sourceLocator": str(requirement.get("sourceLocator", "")),
            "requirement": str(requirement.get("text", "")),
            "sourceText": str(requirement.get("text", "")),
            "mandatory": bool(requirement.get("mandatory", True)),
            "isRejection": bool(requirement.get("isRejection", False)),
            "status": status,
            "evidence": _evidence_summary(evidences),
            "evidenceItems": evidences,
            "matchedObjectIds": [str(item.get("id", "")) for item in matched],
            "risk": _risk_for_status(status),
            "suggestion": _suggestion_for_status(status, category),
            "confidence": requirement.get("confidence", 0),
        }
        rows.append(row)
        weight = WEIGHTS.get(category, WEIGHTS["other"])
        total_weight += weight
        weighted_score += weight * SCORE_BY_STATUS.get(status, 35)
        if row["mandatory"] and status in {"待补材料", "待澄清", "不满足"}:
            unresolved_mandatory.append(row)

    score = round(weighted_score / total_weight) if total_weight else 0
    rejection_risks = [row for row in rows if row["category"] == "rejection" and row["status"] != "满足"]
    if score >= 85 and not unresolved_mandatory and not rejection_risks:
        recommendation = "建议进入正式投标准备"
    elif score >= 70:
        recommendation = "有条件参与，先补材料"
    elif score >= 55:
        recommendation = "人工重点评估"
    else:
        recommendation = "暂不建议参与"

    risks = [
        {
            "level": row["risk"],
            "title": f"{row['kind']}：{row['status']}",
            "detail": f"{row['requirement']}。{row['suggestion']}",
            "sourceLocator": row["sourceLocator"],
        }
        for row in rows
        if row["risk"] != "低"
    ][:30]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1

    return {
        "rows": rows,
        "risks": risks,
        "score": score,
        "decision": recommendation,
        "recommendation": recommendation,
        "clauseCount": len(rows),
        "statusCounts": counts,
        "unresolvedMandatory": [
            {
                "requirementId": row["id"],
                "title": row["kind"],
                "detail": row["requirement"],
                "sourceLocator": row["sourceLocator"],
            }
            for row in unresolved_mandatory
        ],
        "extraction": {
            "method": extraction["extractionMethod"],
            "modelUsed": extraction["modelUsed"],
            "warning": extraction["extractionWarning"],
            "warnings": extraction.get("warnings", []),
        },
    }
