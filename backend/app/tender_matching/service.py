from __future__ import annotations

import json
import re
from typing import Any


def _keywords(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip().lower() for item in re.split(r"[,，;；、/|\n\r]+", value) if item.strip()]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_keywords(item))
        return result
    return []


def _notice_text(item: dict[str, Any]) -> str:
    fields = [
        item.get("title", ""),
        item.get("category", ""),
        item.get("serviceType", ""),
        item.get("region", ""),
        item.get("serviceLocation", ""),
        item.get("noticeType", ""),
        item.get("analysisText", ""),
    ]
    return " ".join(str(value) for value in fields).lower()


def _primary_notice_text(item: dict[str, Any]) -> str:
    project_name = str(item.get("projectName", "")).strip()
    title = project_name or str(item.get("title", "")).strip()
    if not project_name:
        title = re.sub(r"^.+?(?:有限责任公司|股份有限公司|有限公司|公司)\s*关于\s*", "", title)
    fields = [
        title,
        item.get("category", ""),
        item.get("serviceType", ""),
    ]
    return " ".join(str(value) for value in fields).lower()


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _knowledge_values(company_knowledge: dict[str, Any], key: str) -> list[dict[str, Any]]:
    values = company_knowledge.get(key, [])
    return [item for item in values if isinstance(item, dict)]


def _selected_knowledge_values(
    company_knowledge: dict[str, Any],
    key: str,
    selections: Any,
) -> list[dict[str, Any]]:
    values = _knowledge_values(company_knowledge, key)
    selected = {_normalized(item) for item in _keywords(selections)}
    if not selected:
        return values
    return [
        item
        for item in values
        if _normalized(item.get("id")) in selected
        or _normalized(item.get("name")) in selected
    ]


def _term_matches(text: str, terms: list[str]) -> list[str]:
    normalized_text = _normalized(text)
    hits: list[str] = []
    for term in terms:
        normalized_term = _normalized(term)
        if normalized_term and normalized_term in normalized_text:
            hits.append(str(term).strip())
    return list(dict.fromkeys(hits))


def _object_terms(item: dict[str, Any], *fields: str) -> list[str]:
    terms: list[str] = []
    for field in fields:
        value = item.get(field)
        if isinstance(value, list):
            terms.extend(str(entry).strip() for entry in value if str(entry).strip())
        elif value:
            terms.append(str(value).strip())
    return list(dict.fromkeys(terms))


def _semantic_terms(item: dict[str, Any], *fields: str) -> list[str]:
    terms = _object_terms(item, *fields)
    expanded = list(terms)
    for term in terms:
        expanded.extend(
            part.strip()
            for part in re.split(r"[与和及、/&+]|平台|系统", term)
            if part.strip() and len(part.strip()) >= 2
        )
    return list(dict.fromkeys(expanded))


def evaluate_company_relevance(
    item: dict[str, Any],
    company_knowledge: dict[str, Any],
    monitoring: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate discovery relevance with the company ontology as the vocabulary."""
    if not company_knowledge:
        legacy = evaluate_monitoring_profile(item, monitoring)
        return {
            **legacy,
            "matchedBusinessDirections": [],
            "matchedScenarios": [],
            "matchedProducts": [],
            "matchedCapabilities": [],
            "nextStage": "公告资格粗匹配" if legacy["matched"] else "不进入后续匹配",
            "shouldAnalyze": bool(legacy["matched"]),
        }

    text = _notice_text(item)
    primary_text = _primary_notice_text(item)
    directions = _selected_knowledge_values(company_knowledge, "businessDirections", monitoring.get("targetDirections"))
    scenarios = _selected_knowledge_values(company_knowledge, "industryScenarios", monitoring.get("targetScenarios"))
    products = _selected_knowledge_values(company_knowledge, "products", monitoring.get("targetProducts"))
    capabilities = _selected_knowledge_values(company_knowledge, "capabilities", monitoring.get("targetCapabilities"))

    direction_hits: list[str] = []
    scenario_hits: list[str] = []
    product_hits: list[str] = []
    capability_hits: list[str] = []
    matched_terms: list[str] = []

    for direction in directions:
        terms = _object_terms(direction, "name", "description", "keywordIds")
        hits = _term_matches(text, terms)
        if hits:
            direction_hits.append(str(direction.get("name", direction.get("id", ""))))
            matched_terms.extend(hits)

    for scenario in scenarios:
        hits = _term_matches(text, _semantic_terms(scenario, "name"))
        if hits:
            scenario_hits.append(str(scenario.get("name", scenario.get("id", ""))))
            matched_terms.extend(hits)

    for product in products:
        terms = _object_terms(product, "name", "aliases")
        hits = _term_matches(primary_text, terms) or _term_matches(text, terms)
        if hits:
            product_hits.append(str(product.get("name", product.get("id", ""))))
            matched_terms.extend(hits)

    for capability in capabilities:
        terms = _semantic_terms(capability, "name", "description", "category")
        hits = _term_matches(text, terms)
        if hits:
            capability_hits.append(str(capability.get("name", capability.get("id", ""))))
            matched_terms.extend(hits)

    include_keywords = _keywords(monitoring.get("includeKeywords"))
    service_keywords = [
        keyword
        for keyword in _keywords(monitoring.get("serviceType"))
        if keyword not in {"全国", "不限"}
    ]
    synonyms = _keywords(monitoring.get("synonyms"))
    positive_keywords = list(dict.fromkeys([*service_keywords, *include_keywords, *synonyms]))
    matched_config_keywords = _term_matches(text, positive_keywords)
    exclude_keywords = _keywords(monitoring.get("excludeKeywords"))
    exclude_scope = f"{primary_text} {item.get('noticeType', '')}".lower()
    exclude_hits = [keyword for keyword in exclude_keywords if keyword in exclude_scope]

    regions = _keywords(monitoring.get("region"))
    region = str(monitoring.get("region", "")).strip()
    region_required = bool(region and _normalized(region) not in {"全国", "不限"})
    region_hit = not region_required or any(_normalized(value) in _normalized(text) for value in regions)
    procurement_types = _keywords(monitoring.get("targetProcurementTypes"))
    procurement_hit = not procurement_types or any(
        keyword in _normalized(f"{item.get('noticeType', '')} {text}")
        for keyword in procurement_types
    )

    reasons: list[str] = []
    if direction_hits:
        reasons.append(f"业务方向命中：{', '.join(dict.fromkeys(direction_hits))}")
    if scenario_hits:
        reasons.append(f"行业场景命中：{', '.join(dict.fromkeys(scenario_hits))}")
    if product_hits:
        reasons.append(f"产品或服务命中：{', '.join(dict.fromkeys(product_hits))}")
    if capability_hits:
        reasons.append(f"技术能力命中：{', '.join(dict.fromkeys(capability_hits))}")
    if matched_config_keywords:
        reasons.append(f"搜索词命中：{', '.join(matched_config_keywords)}")
    if region_required and region_hit:
        reasons.append(f"地区命中：{region}")
    if procurement_types and procurement_hit:
        reasons.append(f"采购方式命中：{', '.join(procurement_types)}")

    if exclude_hits:
        return {
            "matched": False,
            "score": 0,
            "matchedBusinessDirections": list(dict.fromkeys(direction_hits)),
            "matchedScenarios": list(dict.fromkeys(scenario_hits)),
            "matchedProducts": list(dict.fromkeys(product_hits)),
            "matchedCapabilities": list(dict.fromkeys(capability_hits)),
            "matchedKeywords": list(dict.fromkeys([*matched_terms, *matched_config_keywords])),
            "reasons": reasons,
            "excludeReason": f"命中排除词：{', '.join(exclude_hits)}",
            "nextStage": "不进入后续匹配",
            "shouldAnalyze": False,
        }

    if not region_hit:
        return {
            "matched": False,
            "score": 0,
            "matchedBusinessDirections": list(dict.fromkeys(direction_hits)),
            "matchedScenarios": list(dict.fromkeys(scenario_hits)),
            "matchedProducts": list(dict.fromkeys(product_hits)),
            "matchedCapabilities": list(dict.fromkeys(capability_hits)),
            "matchedKeywords": list(dict.fromkeys([*matched_terms, *matched_config_keywords])),
            "reasons": reasons,
            "excludeReason": f"地区未命中：{region}",
            "nextStage": "不进入后续匹配",
            "shouldAnalyze": False,
        }

    if not procurement_hit:
        return {
            "matched": False,
            "score": 0,
            "matchedBusinessDirections": list(dict.fromkeys(direction_hits)),
            "matchedScenarios": list(dict.fromkeys(scenario_hits)),
            "matchedProducts": list(dict.fromkeys(product_hits)),
            "matchedCapabilities": list(dict.fromkeys(capability_hits)),
            "matchedKeywords": list(dict.fromkeys([*matched_terms, *matched_config_keywords])),
            "reasons": reasons,
            "excludeReason": f"采购方式未命中：{', '.join(procurement_types)}",
            "nextStage": "不进入后续匹配",
            "shouldAnalyze": False,
        }

    score = (
        (25 if direction_hits else 0)
        + (20 if scenario_hits else 0)
        + (25 if product_hits else 0)
        + (20 if capability_hits else 0)
        + (10 if matched_config_keywords else 0)
    )
    has_ontology_hit = bool(direction_hits or scenario_hits or product_hits or capability_hits)
    has_any_hit = bool(has_ontology_hit or matched_config_keywords)
    if not has_any_hit:
        return {
            "matched": False,
            "score": 0,
            "matchedBusinessDirections": [],
            "matchedScenarios": [],
            "matchedProducts": [],
            "matchedCapabilities": [],
            "matchedKeywords": [],
            "reasons": [],
            "excludeReason": "未命中公司本体对象或搜索画像关键词",
            "nextStage": "不进入后续匹配",
            "shouldAnalyze": False,
        }

    notice_type = str(item.get("noticeType", ""))
    if any(word in notice_type for word in ("结果", "成交", "中标", "废标", "流标", "终止")):
        score = max(score - 25, 0)
        reasons.append(f"历史/结果类公告降权：{notice_type}")

    should_analyze = bool(has_ontology_hit and score >= 70)
    next_stage = "公告资格粗匹配" if should_analyze else "人工复核"
    return {
        "matched": True,
        "score": min(score, 100),
        "matchedBusinessDirections": list(dict.fromkeys(direction_hits)),
        "matchedScenarios": list(dict.fromkeys(scenario_hits)),
        "matchedProducts": list(dict.fromkeys(product_hits)),
        "matchedCapabilities": list(dict.fromkeys(capability_hits)),
        "matchedKeywords": list(dict.fromkeys([*matched_terms, *matched_config_keywords])),
        "reasons": reasons,
        "excludeReason": "",
        "nextStage": next_stage,
        "shouldAnalyze": should_analyze,
    }


def evaluate_monitoring_profile(item: dict[str, Any], monitoring: dict[str, Any]) -> dict[str, Any]:
    text = _notice_text(item)
    primary_text = _primary_notice_text(item)
    service_keywords = [keyword for keyword in _keywords(monitoring.get("serviceType")) if keyword not in {"全国", "不限"}]
    region = str(monitoring.get("region", "")).strip().lower()
    include_keywords = _keywords(monitoring.get("includeKeywords"))
    exclude_keywords = _keywords(monitoring.get("excludeKeywords"))
    positive_keywords = list(dict.fromkeys([*service_keywords, *include_keywords]))
    primary_hits = [keyword for keyword in positive_keywords if keyword in primary_text]
    body_hits = [keyword for keyword in positive_keywords if keyword in text]
    broad_keywords = {"工程", "建设项目", "建筑", "服务", "采购", "项目", "平台", "系统", "设备", "软件"}
    specific_body_hits = [keyword for keyword in body_hits if keyword not in broad_keywords and len(keyword) >= 3]
    matched_keywords = list(dict.fromkeys([*primary_hits, *specific_body_hits]))
    matched_include_keywords = [keyword for keyword in include_keywords if keyword in matched_keywords]
    matched_service_keywords = [keyword for keyword in service_keywords if keyword in matched_keywords]
    exclude_scope = f"{primary_text} {item.get('noticeType', '')}".lower()
    exclude_hits = [keyword for keyword in exclude_keywords if keyword in exclude_scope]
    reasons: list[str] = []
    score = 0

    if exclude_hits:
        return {
            "matched": False,
            "score": 0,
            "matchedKeywords": matched_keywords,
            "reasons": reasons,
            "excludeReason": f"命中排除词：{', '.join(exclude_hits)}",
        }

    if region and region not in {"全国", "不限"}:
        if region not in text:
            return {
                "matched": False,
                "score": 0,
                "matchedKeywords": matched_keywords,
                "reasons": reasons,
                "excludeReason": f"地区未命中：{region}",
            }
        score += 15
        reasons.append(f"地区命中：{region}")

    if positive_keywords:
        if not matched_keywords:
            return {
                "matched": False,
                "score": 0,
                "matchedKeywords": [],
                "reasons": reasons,
                "excludeReason": "项目名称未命中业务词，正文也未命中特定关键词",
            }

        if matched_service_keywords:
            score += 25
            reasons.append(f"业务类型命中：{', '.join(matched_service_keywords)}")
        elif service_keywords:
            reasons.append(f"业务类型未精确命中：{', '.join(service_keywords)}，已按包含关键词命中保留")

        score += 40 if primary_hits else 25
        if matched_include_keywords:
            reasons.append(f"包含关键词命中：{', '.join(matched_include_keywords)}")
        elif matched_keywords:
            reasons.append(f"正向关键词命中：{', '.join(matched_keywords)}")

    notice_type = str(item.get("noticeType", ""))
    if any(word in notice_type for word in ("招标", "采购", "磋商", "谈判", "比选", "询价")):
        score += 10
        reasons.append(f"公告类型：{notice_type}")
    if any(word in notice_type for word in ("结果", "成交", "中标", "废标", "流标")):
        score = max(score - 25, 0)
        reasons.append(f"历史/结果类公告降权：{notice_type}")

    return {
        "matched": True,
        "score": min(score or 50, 100),
        "matchedKeywords": matched_keywords,
        "reasons": reasons,
        "excludeReason": "",
    }


def matches_monitoring_profile(item: dict[str, Any], monitoring: dict[str, Any]) -> bool:
    return bool(evaluate_monitoring_profile(item, monitoring)["matched"])


def filter_catalog_by_monitoring(catalog: list[dict[str, Any]], monitoring: dict[str, Any]) -> list[dict[str, Any]]:
    if monitoring.get("enabled") is False:
        return []
    return [item for item in catalog if matches_monitoring_profile(item, monitoring)]


def matches_manual_search(item: dict[str, Any], payload: dict[str, Any]) -> bool:
    text = json.dumps(item, ensure_ascii=False).lower()
    query = str(payload.get("query", "")).strip().lower()
    region = str(payload.get("region", "")).strip().lower()
    service_type = str(payload.get("type", "")).strip().lower()
    if query and query not in text:
        return False
    if region and region not in f"{item.get('region', '')}{item.get('serviceLocation', '')}".lower():
        return False
    if service_type and service_type not in f"{item.get('category', '')}{item.get('serviceType', '')}".lower():
        return False
    return True
