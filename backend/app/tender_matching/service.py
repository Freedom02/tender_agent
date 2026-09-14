from __future__ import annotations

import json
import re
from typing import Any


RELEVANCE_VERSION = "relevance-v2"
ANNOUNCEMENT_MATCH_THRESHOLD = 70
BROAD_TERMS = {
    "项目", "建设项目", "工程", "服务", "采购", "平台", "系统", "软件", "设备",
    "信息化", "技术", "管理", "方案", "数据", "应用", "相关", "其他",
}


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


def _notice_zones(item: dict[str, Any]) -> tuple[str, str, str]:
    """Return title, structured fields and body separately for weighted matching."""
    title = str(item.get("projectName") or item.get("title") or "").strip()
    if not item.get("projectName"):
        title = re.sub(r"^.+?(?:有限责任公司|股份有限公司|有限公司|公司)\s*关于\s*", "", title)
    structured = " ".join(
        str(item.get(field, ""))
        for field in (
            "category", "serviceType", "region", "serviceLocation", "procurementMethod",
        )
    )
    body = str(item.get("analysisText", ""))
    return title.lower(), structured.lower(), body.lower()


def _primary_notice_text(item: dict[str, Any]) -> str:
    title, structured, _ = _notice_zones(item)
    return f"{title} {structured}"


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
        if (
            normalized_term
            and normalized_term not in {_normalized(value) for value in BROAD_TERMS}
            and len(normalized_term) >= 2
            and normalized_term in normalized_text
        ):
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


def _dimension_match(
    values: list[dict[str, Any]],
    fields: tuple[str, ...],
    title_text: str,
    structured_text: str,
    body_text: str,
    max_points: int,
) -> dict[str, Any]:
    names: list[str] = []
    terms: list[str] = []
    evidence: list[str] = []
    best_zone = 0
    for value in values:
        candidate_terms = _semantic_terms(value, *fields)
        title_hits = _term_matches(title_text, candidate_terms)
        structured_hits = _term_matches(structured_text, candidate_terms)
        body_hits = _term_matches(body_text, candidate_terms)
        if title_hits:
            zone = 3
            zone_name = "标题/项目名称"
            hits = title_hits
        elif structured_hits:
            zone = 2
            zone_name = "结构化字段"
            hits = structured_hits
        elif body_hits:
            zone = 1
            zone_name = "正文弱命中"
            hits = body_hits
        else:
            continue
        name = str(value.get("name") or value.get("id") or "").strip()
        if name:
            names.append(name)
        terms.extend(hits)
        evidence.append(f"{zone_name}命中“{name or hits[0]}”：{', '.join(hits[:3])}")
        best_zone = max(best_zone, zone)

    points = {
        3: max_points,
        2: round(max_points * 0.75),
        1: round(max_points * 0.35),
    }.get(best_zone, 0)
    return {
        "points": points,
        "maxPoints": max_points,
        "matched": list(dict.fromkeys(names)),
        "terms": list(dict.fromkeys(terms)),
        "evidence": list(dict.fromkeys(evidence)),
    }


def _config_match(
    terms: list[str],
    title_text: str,
    structured_text: str,
    body_text: str,
    max_points: int = 10,
) -> dict[str, Any]:
    title_hits = _term_matches(title_text, terms)
    structured_hits = _term_matches(structured_text, terms)
    body_hits = _term_matches(body_text, terms)
    if title_hits:
        points = max_points
        evidence = [f"标题/项目名称命中搜索词：{', '.join(title_hits[:5])}"]
    elif structured_hits:
        points = round(max_points * 0.7)
        evidence = [f"结构化字段命中搜索词：{', '.join(structured_hits[:5])}"]
    elif body_hits:
        points = round(max_points * 0.3)
        evidence = [f"正文弱命中搜索词：{', '.join(body_hits[:5])}"]
    else:
        points = 0
        evidence = []
    return {
        "points": points,
        "maxPoints": max_points,
        "matched": list(dict.fromkeys([*title_hits, *structured_hits, *body_hits])),
        "evidence": evidence,
    }


def _procurement_match(item: dict[str, Any], target_types: list[str], body_text: str) -> tuple[bool, str]:
    """Match procurement type from structured fields before reading the body."""
    if not target_types:
        return True, ""

    method = _normalized(item.get("procurementMethod"))
    notice_type = _normalized(item.get("noticeType"))
    for target in target_types:
        normalized_target = _normalized(target)
        if normalized_target and normalized_target in method:
            return True, "procurementMethod"
        if normalized_target and normalized_target in notice_type:
            return True, "noticeType"

    if method:
        return False, "procurementMethod"

    # When no structured method is available, accept only an explicit labeled
    # field instead of arbitrary mentions in requirement prose.
    labeled_match = re.search(
        r"(?:采购方式|招标方式|采购组织形式)\s*(?:[:：]|为)\s*([^\r\n；;。]+)",
        body_text,
        flags=re.IGNORECASE,
    )
    labeled_value = _normalized(labeled_match.group(1)) if labeled_match else ""
    if labeled_value:
        return (
            any(_normalized(target) in labeled_value for target in target_types),
            "labeledBody",
        )
    return False, "missing"


def _base_relevance_result(
    *,
    score: int,
    matched: bool,
    directions: list[str],
    scenarios: list[str],
    products: list[str],
    capabilities: list[str],
    keywords: list[str],
    reasons: list[str],
    exclude_reason: str,
    next_stage: str,
    should_analyze: bool,
    score_breakdown: dict[str, Any],
    evidence: list[str],
) -> dict[str, Any]:
    return {
        "matched": matched,
        "score": max(0, min(int(score), 100)),
        "matchedBusinessDirections": list(dict.fromkeys(directions)),
        "matchedScenarios": list(dict.fromkeys(scenarios)),
        "matchedProducts": list(dict.fromkeys(products)),
        "matchedCapabilities": list(dict.fromkeys(capabilities)),
        "matchedKeywords": list(dict.fromkeys(keywords)),
        "reasons": list(dict.fromkeys(reasons)),
        "matchEvidence": list(dict.fromkeys(evidence)),
        "excludeReason": exclude_reason,
        "nextStage": next_stage,
        "shouldAnalyze": should_analyze,
        "scoreBreakdown": score_breakdown,
        "scoreVersion": RELEVANCE_VERSION,
    }


def evaluate_company_relevance(
    item: dict[str, Any],
    company_knowledge: dict[str, Any],
    monitoring: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate discovery relevance using weighted title/field/body evidence."""
    title_text, structured_text, body_text = _notice_zones(item)
    primary_text = f"{title_text} {structured_text}"
    text = f"{primary_text} {body_text}"
    directions = _selected_knowledge_values(company_knowledge, "businessDirections", monitoring.get("targetDirections"))
    scenarios = _selected_knowledge_values(company_knowledge, "industryScenarios", monitoring.get("targetScenarios"))
    products = _selected_knowledge_values(company_knowledge, "products", monitoring.get("targetProducts"))
    capabilities = _selected_knowledge_values(company_knowledge, "capabilities", monitoring.get("targetCapabilities"))

    direction_match = _dimension_match(directions, ("name", "description"), title_text, structured_text, body_text, 25)
    scenario_match = _dimension_match(scenarios, ("name", "description"), title_text, structured_text, body_text, 20)
    product_match = _dimension_match(products, ("name", "aliases", "category"), title_text, structured_text, body_text, 30)
    capability_match = _dimension_match(capabilities, ("name", "description", "category"), title_text, structured_text, body_text, 15)

    include_keywords = _keywords(monitoring.get("includeKeywords"))
    service_keywords = [
        keyword
        for keyword in _keywords(monitoring.get("serviceType"))
        if keyword not in {"全国", "不限"}
    ]
    synonyms = _keywords(monitoring.get("synonyms"))
    positive_keywords = list(dict.fromkeys([*service_keywords, *include_keywords, *synonyms]))
    config_match = _config_match(positive_keywords, title_text, structured_text, body_text)
    matched_config_keywords = config_match["matched"]
    exclude_keywords = _keywords(monitoring.get("excludeKeywords"))
    exclude_scope = f"{primary_text} {item.get('noticeType', '')}".lower()
    exclude_hits = [keyword for keyword in exclude_keywords if _normalized(keyword) and _normalized(keyword) in _normalized(exclude_scope)]

    regions = _keywords(monitoring.get("region"))
    region = str(monitoring.get("region", "")).strip()
    region_required = bool(region and _normalized(region) not in {"全国", "不限"})
    region_text = " ".join(
        str(item.get(field, ""))
        for field in ("region", "serviceLocation", "province", "city")
    )
    region_hit = not region_required or any(_normalized(value) in _normalized(region_text) for value in regions)
    procurement_types = _keywords(monitoring.get("targetProcurementTypes"))
    procurement_hit, procurement_source = _procurement_match(item, procurement_types, body_text)

    score_breakdown: dict[str, Any] = {
        "version": RELEVANCE_VERSION,
        "dimensions": {
            "businessDirection": direction_match,
            "industryScenario": scenario_match,
            "product": product_match,
            "capability": capability_match,
            "configuredKeywords": config_match,
        },
        "hardFilters": {
            "exclude": {"passed": not exclude_hits, "hits": exclude_hits},
            "region": {"passed": region_hit, "target": region, "matchedText": region_text if region_hit else ""},
            "procurementType": {"passed": procurement_hit, "target": procurement_types, "source": procurement_source},
        },
    }

    direction_hits = direction_match["matched"]
    scenario_hits = scenario_match["matched"]
    product_hits = product_match["matched"]
    capability_hits = capability_match["matched"]
    matched_terms = [
        *direction_match["terms"], *scenario_match["terms"], *product_match["terms"],
        *capability_match["terms"], *matched_config_keywords,
    ]
    evidence = [
        *direction_match["evidence"], *scenario_match["evidence"], *product_match["evidence"],
        *capability_match["evidence"], *config_match["evidence"],
    ]

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
        return _base_relevance_result(score=0, matched=False, directions=direction_hits, scenarios=scenario_hits,
                                      products=product_hits, capabilities=capability_hits, keywords=matched_terms,
                                      reasons=reasons, exclude_reason=f"命中排除词：{', '.join(exclude_hits)}",
                                      next_stage="不进入后续匹配", should_analyze=False,
                                      score_breakdown=score_breakdown, evidence=evidence)

    if not region_hit:
        return _base_relevance_result(score=0, matched=False, directions=direction_hits, scenarios=scenario_hits,
                                      products=product_hits, capabilities=capability_hits, keywords=matched_terms,
                                      reasons=reasons, exclude_reason=f"地区未命中：{region}",
                                      next_stage="不进入后续匹配", should_analyze=False,
                                      score_breakdown=score_breakdown, evidence=evidence)

    if not procurement_hit:
        return _base_relevance_result(score=0, matched=False, directions=direction_hits, scenarios=scenario_hits,
                                      products=product_hits, capabilities=capability_hits, keywords=matched_terms,
                                      reasons=reasons, exclude_reason=f"采购方式未命中：{', '.join(procurement_types)}",
                                      next_stage="不进入后续匹配", should_analyze=False,
                                      score_breakdown=score_breakdown, evidence=evidence)

    score = sum(
        component["points"]
        for component in (direction_match, scenario_match, product_match, capability_match, config_match)
    )
    has_ontology_hit = bool(direction_hits or scenario_hits or product_hits or capability_hits)
    has_any_hit = bool(has_ontology_hit or matched_config_keywords)
    if not has_any_hit:
        return _base_relevance_result(score=0, matched=False, directions=[], scenarios=[], products=[], capabilities=[],
                                      keywords=[], reasons=[], exclude_reason="未命中公司本体对象或搜索画像关键词",
                                      next_stage="不进入后续匹配", should_analyze=False,
                                      score_breakdown=score_breakdown, evidence=[])

    notice_type = str(item.get("noticeType", ""))
    if any(word in notice_type for word in ("结果", "成交", "中标", "废标", "流标", "终止")):
        score = max(score - 25, 0)
        reasons.append(f"历史/结果类公告降权：{notice_type}")

    score_breakdown["total"] = max(0, min(score, 100))
    should_analyze = bool(has_ontology_hit and score >= ANNOUNCEMENT_MATCH_THRESHOLD)
    next_stage = "公告资格粗匹配" if should_analyze else "人工复核"
    return _base_relevance_result(score=score, matched=True, directions=direction_hits, scenarios=scenario_hits,
                                  products=product_hits, capabilities=capability_hits, keywords=matched_terms,
                                  reasons=reasons, exclude_reason="", next_stage=next_stage,
                                  should_analyze=should_analyze, score_breakdown=score_breakdown, evidence=evidence)


def evaluate_monitoring_profile(item: dict[str, Any], monitoring: dict[str, Any]) -> dict[str, Any]:
    title_text, structured_text, body_text = _notice_zones(item)
    service_keywords = [keyword for keyword in _keywords(monitoring.get("serviceType")) if keyword not in {"全国", "不限"}]
    include_keywords = _keywords(monitoring.get("includeKeywords"))
    positive_keywords = list(dict.fromkeys([*service_keywords, *include_keywords, *_keywords(monitoring.get("synonyms"))]))
    config_match = _config_match(positive_keywords, title_text, structured_text, body_text)
    region = str(monitoring.get("region", "")).strip()
    region_values = _keywords(region)
    region_text = " ".join(str(item.get(field, "")) for field in ("region", "serviceLocation", "province", "city"))
    region_required = bool(region and _normalized(region) not in {"全国", "不限"})
    region_hit = not region_required or any(_normalized(value) in _normalized(region_text) for value in region_values)
    exclude_keywords = _keywords(monitoring.get("excludeKeywords"))
    exclude_scope = f"{title_text} {item.get('noticeType', '')}".lower()
    exclude_hits = [keyword for keyword in exclude_keywords if _normalized(keyword) in _normalized(exclude_scope)]
    reasons: list[str] = []
    if config_match["matched"]:
        reasons.append(f"搜索词命中：{', '.join(config_match['matched'][:5])}")
    if region_required and region_hit:
        reasons.append(f"地区命中：{region}")
    if exclude_hits:
        return _base_relevance_result(score=0, matched=False, directions=[], scenarios=[], products=[], capabilities=[],
                                      keywords=config_match["matched"], reasons=reasons,
                                      exclude_reason=f"命中排除词：{', '.join(exclude_hits)}",
                                      next_stage="不进入后续匹配", should_analyze=False,
                                      score_breakdown={"version": RELEVANCE_VERSION, "configuredKeywords": config_match,
                                                       "hardFilters": {"exclude": {"passed": False, "hits": exclude_hits},
                                                                        "region": {"passed": region_hit, "target": region}}},
                                      evidence=config_match["evidence"])
    if not region_hit:
        return _base_relevance_result(score=0, matched=False, directions=[], scenarios=[], products=[], capabilities=[],
                                      keywords=config_match["matched"], reasons=reasons,
                                      exclude_reason=f"地区未命中：{region}", next_stage="不进入后续匹配",
                                      should_analyze=False,
                                      score_breakdown={"version": RELEVANCE_VERSION, "configuredKeywords": config_match,
                                                       "hardFilters": {"exclude": {"passed": True, "hits": []},
                                                                        "region": {"passed": False, "target": region}}},
                                      evidence=config_match["evidence"])
    if not config_match["matched"]:
        return _base_relevance_result(score=0, matched=False, directions=[], scenarios=[], products=[], capabilities=[],
                                      keywords=[], reasons=[], exclude_reason="未命中搜索画像关键词",
                                      next_stage="不进入后续匹配", should_analyze=False,
                                      score_breakdown={"version": RELEVANCE_VERSION, "configuredKeywords": config_match},
                                      evidence=[])
    score = config_match["points"]
    notice_type = str(item.get("noticeType", ""))
    if any(word in notice_type for word in ("结果", "成交", "中标", "废标", "流标", "终止")):
        score = max(score - 25, 0)
        reasons.append(f"历史/结果类公告降权：{notice_type}")
    should_analyze = score >= ANNOUNCEMENT_MATCH_THRESHOLD
    return _base_relevance_result(score=score, matched=True, directions=[], scenarios=[], products=[], capabilities=[],
                                  keywords=config_match["matched"], reasons=reasons, exclude_reason="",
                                  next_stage="公告资格粗匹配" if should_analyze else "人工复核",
                                  should_analyze=should_analyze,
                                  score_breakdown={"version": RELEVANCE_VERSION, "configuredKeywords": config_match,
                                                   "total": score}, evidence=config_match["evidence"])


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
