"""Decision-focused matching for technical, product and service requirements.

This module is intentionally independent from the legacy row projection.  It
consumes the shared RequirementFact shape (or compatible dictionaries), uses
the company ledger as the only source of enterprise facts, and returns a
small, auditable result for the bid/no-bid review stage.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .deterministic import deterministic_match
from .semantic_judge import recall_candidates, semantic_judge


MatchStatus = Literal["满足", "部分满足", "不满足", "待补材料", "待澄清", "不适用"]
RiskLevel = Literal["low", "medium", "high"]


class CompanyEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(..., alias="factId")
    name: str = ""
    evidence_path: str = Field("", alias="evidencePath")
    source_type: str = Field("company_fact", alias="sourceType")
    excerpt: str = ""


class TechnicalMatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requirement_id: str = Field(..., alias="requirementId")
    category: str = "technical"
    requirement_text: str = Field("", alias="requirementText")
    status: MatchStatus = "待补材料"
    matched_fact_ids: list[str] = Field(default_factory=list, alias="matchedFactIds")
    evidence_items: list[CompanyEvidenceItem] = Field(
        default_factory=list,
        alias="evidenceItems",
    )
    reason: str = ""
    missing: list[str] = Field(default_factory=list)
    risk_level: RiskLevel = Field("medium", alias="riskLevel")
    needs_human_review: bool = Field(True, alias="needsHumanReview")
    hard_blocker: bool = Field(False, alias="hardBlocker")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    status_source: str = Field("rule_fallback", alias="statusSource")


class TechnicalMatchSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TechnicalMatchItem] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict, alias="statusCounts")
    hard_blockers: list[TechnicalMatchItem] = Field(
        default_factory=list,
        alias="hardBlockers",
    )
    technical_gaps: list[TechnicalMatchItem] = Field(
        default_factory=list,
        alias="technicalGaps",
    )
    material_gaps: list[TechnicalMatchItem] = Field(
        default_factory=list,
        alias="materialGaps",
    )
    coverage: float = Field(0.0, ge=0.0, le=1.0)
    recommendation: Literal["建议继续", "谨慎继续", "不建议继续", "待澄清"] = "待澄清"
    warnings: list[str] = Field(default_factory=list)


def _value(item: Any, *names: str) -> str:
    if isinstance(item, BaseModel):
        for name in names:
            value = getattr(item, name, None)
            if value not in (None, ""):
                return str(value)
        return ""
    if isinstance(item, Mapping):
        for name in names:
            value = item.get(name)
            if value not in (None, ""):
                return str(value)
    return ""


def _bool_value(item: Any, *names: str, default: bool = False) -> bool:
    if isinstance(item, BaseModel):
        for name in names:
            value = getattr(item, name, None)
            if value is not None:
                return bool(value)
    elif isinstance(item, Mapping):
        for name in names:
            if name in item:
                return bool(item[name])
    return default


def _requirement_id(item: Any, index: int) -> str:
    return _value(item, "requirement_id", "requirementId", "id") or f"REQ-{index:04d}"


def _fact_id(fact: Mapping[str, Any]) -> str:
    for key in (
        "qualificationId",
        "credentialId",
        "performanceId",
        "staffId",
        "financialEvidenceId",
        "parameterId",
        "productId",
        "organizationId",
        "id",
    ):
        if fact.get(key):
            return str(fact[key])
    return ""


def _fact_name(fact: Mapping[str, Any]) -> str:
    return str(
        fact.get("productName")
        or fact.get("name")
        or fact.get("description")
        or fact.get("organizationName")
        or _fact_id(fact)
        or ""
    )


def _fact_evidence_path(fact: Mapping[str, Any]) -> str:
    return str(
        fact.get("evidencePath")
        or fact.get("sourceLocator")
        or fact.get("filePath")
        or fact.get("materialPath")
        or ""
    ).strip()


def _fact_excerpt(fact: Mapping[str, Any]) -> str:
    fields = (
        "description",
        "applicableScenario",
        "scope",
        "aliases",
        "professionalCertificates",
    )
    return " ".join(str(fact.get(key) or "").strip() for key in fields).strip()[:500]


def _evidence_item(fact: Mapping[str, Any]) -> CompanyEvidenceItem:
    return CompanyEvidenceItem(
        factId=_fact_id(fact),
        name=_fact_name(fact),
        evidencePath=_fact_evidence_path(fact),
        sourceType="company_fact",
        excerpt=_fact_excerpt(fact),
    )


def _fact_blob(fact: Mapping[str, Any]) -> str:
    return re.sub(r"\s+", "", " ".join(str(value or "") for value in fact.values()))


def _normal_text(text: str) -> str:
    value = re.sub(
        r"[\s，。；、：:（）()【】\[\]“”\"'‘’<>《》和]",
        "",
        str(text or ""),
    )
    return value.lower()


def _terms(text: str) -> list[str]:
    normalized = _normal_text(text)
    ignored = {
        "投标人",
        "供应商",
        "应当",
        "应",
        "必须",
        "提供",
        "具备",
        "要求",
        "支持",
        "完成",
        "项目",
        "服务",
        "平台",
        "和",
    }
    terms: list[str] = list(
        dict.fromkeys(re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", normalized))
    )
    for run in re.findall(r"[\u4e00-\u9fff]+", normalized):
        cleaned = run
        for word in sorted(ignored, key=len, reverse=True):
            cleaned = cleaned.replace(word, "")
        if len(cleaned) >= 2:
            terms.append(cleaned)
    return [term for term in dict.fromkeys(terms) if len(term) >= 2]


def _known_facts(company: Mapping[str, Any], category: str) -> list[dict[str, Any]]:
    keys = ("products", "parameters", "capabilities") if category == "technical" else (
        "capabilities",
        "products",
        "projects",
    )
    result: list[dict[str, Any]] = []
    for key in keys:
        for item in company.get(key, []) or []:
            if isinstance(item, dict) and _fact_id(item):
                result.append(item)
    return result


def _lexical_candidates(
    requirement_text: str,
    facts: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    terms = _terms(requirement_text)
    if not terms:
        return [], []
    scored: list[tuple[int, dict[str, Any]]] = []
    for fact in facts:
        blob = _normal_text(_fact_blob(fact))
        hits = [term for term in terms if term in blob]
        if hits:
            scored.append((len(hits), fact))
    scored.sort(key=lambda value: (-value[0], _fact_id(value[1])))
    return [fact for _, fact in scored], terms


def _result_from_rule(
    requirement_id: str,
    category: str,
    requirement_text: str,
    result: Mapping[str, Any],
    facts_by_id: Mapping[str, dict[str, Any]],
) -> TechnicalMatchItem:
    matched_ids = [
        str(value)
        for value in result.get("matchedFactIds", [])
        if str(value) in facts_by_id
    ]
    evidence_items = [
        _evidence_item(facts_by_id[item])
        for item in matched_ids
        if _fact_evidence_path(facts_by_id[item])
    ]
    raw_status = str(result.get("status") or "待补材料")
    status: MatchStatus = raw_status if raw_status in {
        "满足",
        "部分满足",
        "不满足",
        "待补材料",
        "待澄清",
        "不适用",
    } else "待补材料"
    if status == "满足" and not evidence_items:
        status = "待补材料"
    missing = [str(value) for value in result.get("gaps", []) if str(value).strip()]
    hard_blocker = status == "不满足" and category in {"technical", "service_plan"}
    if status == "不满足" and not missing:
        missing = ["企业事实未覆盖该要求"]
    return TechnicalMatchItem(
        requirementId=requirement_id,
        category=category,
        requirementText=requirement_text,
        status=status,
        matchedFactIds=matched_ids,
        evidenceItems=evidence_items,
        reason=str(result.get("reason") or ""),
        missing=missing,
        riskLevel="high" if hard_blocker else ("medium" if status != "满足" else "low"),
        needsHumanReview=status not in {"满足", "不满足"} or not evidence_items,
        hardBlocker=hard_blocker,
        confidence=float(result.get("confidence") or (0.95 if status in {"满足", "不满足"} else 0.6)),
        statusSource=str(result.get("statusSource") or "rule"),
    )


def _lexical_result(
    requirement_id: str,
    category: str,
    requirement_text: str,
    candidates: list[dict[str, Any]],
    terms: list[str],
) -> TechnicalMatchItem:
    if not candidates:
        return TechnicalMatchItem(
            requirementId=requirement_id,
            category=category,
            requirementText=requirement_text,
            status="待补材料",
            reason="企业台账中没有找到可供核验的产品、参数或服务能力",
            missing=["企业产品/能力事实"],
            riskLevel="medium",
            needsHumanReview=True,
            statusSource="rule_fallback",
        )
    matched = candidates[:5]
    matched_ids = [_fact_id(item) for item in matched]
    evidence_items = [
        _evidence_item(item)
        for item in matched
        if _fact_evidence_path(item)
    ]
    covered_terms = {
        term
        for term in terms
        if any(term in _normal_text(_fact_blob(item)) for item in matched)
    }
    ratio = len(covered_terms) / max(len(terms), 1)
    if ratio >= 0.8 and evidence_items:
        status: MatchStatus = "满足"
    elif ratio > 0:
        status = "部分满足" if evidence_items else "待补材料"
    else:
        status = "待补材料"
    missing = [] if status == "满足" else ["要求的完整技术/服务覆盖证明"]
    return TechnicalMatchItem(
        requirementId=requirement_id,
        category=category,
        requirementText=requirement_text,
        status=status,
        matchedFactIds=matched_ids,
        evidenceItems=evidence_items,
        reason=(
            f"企业台账命中 {len(matched)} 条相关事实，"
            f"关键词覆盖约 {ratio:.0%}"
        ),
        missing=missing,
        riskLevel="low" if status == "满足" else "medium",
        needsHumanReview=status != "满足",
        confidence=min(0.9, 0.45 + ratio * 0.45),
        statusSource="lexical_rule",
    )


def _llm_enabled(ai: Mapping[str, Any] | None) -> bool:
    config = ai or {}
    return bool(config.get("enabled")) and bool(
        str(config.get("model") or config.get("matchingModel") or "").strip()
    )


def _semantic_items(
    pending: list[tuple[int, str, str, str, list[dict[str, Any]]]],
    ai: Mapping[str, Any],
    facts_by_id: Mapping[str, dict[str, Any]],
) -> dict[int, TechnicalMatchItem]:
    if not pending:
        return {}
    output: dict[int, TechnicalMatchItem] = {}
    for index, requirement_id, category, text, candidates in pending:
        raw = semantic_judge(text, category, candidates, dict(ai))
        if not raw:
            continue
        output[index] = _result_from_rule(
            requirement_id,
            category,
            text,
            raw,
            facts_by_id,
        )
    return output


def match_technical_service_requirements(
    requirements: Iterable[Any],
    company_knowledge: Mapping[str, Any],
    *,
    ai: Mapping[str, Any] | None = None,
) -> TechnicalMatchSummary:
    """Match technical/product/service requirements against company facts.

    The function deliberately does not turn an absent fact into "不满足".
    Missing enterprise data becomes "待补材料"; only explicit deterministic
    hard mismatches become "不满足" and are marked as blockers.
    """

    facts_by_id = {
        _fact_id(fact): fact
        for category in ("products", "parameters", "capabilities", "projects")
        for fact in (company_knowledge.get(category, []) or [])
        if isinstance(fact, dict) and _fact_id(fact)
    }
    items: list[TechnicalMatchItem] = []
    pending_semantic: list[tuple[int, str, str, str, list[dict[str, Any]]]] = []

    for index, requirement in enumerate(requirements, start=1):
        category = _value(requirement, "category") or "technical"
        if category not in {"technical", "service_plan"}:
            continue
        requirement_id = _requirement_id(requirement, index)
        text = _value(
            requirement,
            "raw_text",
            "rawText",
            "normalized_requirement",
            "normalizedRequirement",
            "text",
            "requirement",
            "sourceText",
        )
        if not text:
            items.append(TechnicalMatchItem(
                requirementId=requirement_id,
                category=category,
                status="待澄清",
                reason="Requirement 缺少可匹配的原文",
                missing=["要求原文"],
                riskLevel="medium",
                needsHumanReview=True,
                statusSource="input_validation",
            ))
            continue

        rule_result = deterministic_match(text, "technical", dict(company_knowledge)) if category == "technical" else None
        if rule_result and str(rule_result.get("status")) == "不满足":
            items.append(_result_from_rule(requirement_id, category, text, rule_result, facts_by_id))
            continue

        candidates, terms = _lexical_candidates(text, _known_facts(company_knowledge, category))
        lexical = _lexical_result(requirement_id, category, text, candidates, terms)
        if lexical.status in {"满足", "部分满足"}:
            items.append(lexical)
            continue

        if rule_result:
            items.append(_result_from_rule(requirement_id, category, text, rule_result, facts_by_id))
            continue

        if _llm_enabled(ai):
            semantic_candidates = candidates or recall_candidates(
                category,
                dict(company_knowledge),
                top_k=8,
                req_text=text,
            )
            pending_semantic.append((
                len(items),
                requirement_id,
                category,
                text,
                semantic_candidates,
            ))
            items.append(lexical)
        else:
            items.append(lexical)

    if pending_semantic and ai:
        semantic_items = _semantic_items(pending_semantic, ai, facts_by_id)
        for index, result in semantic_items.items():
            items[index] = result

    counts = Counter(item.status for item in items)
    hard_blockers = [item for item in items if item.hard_blocker]
    technical_gaps = [
        item for item in items
        if item.status in {"部分满足", "不满足", "待澄清"}
    ]
    material_gaps = [item for item in items if item.status == "待补材料"]
    covered = sum(1 for item in items if item.status == "满足")
    coverage = covered / len(items) if items else 0.0

    if not items:
        recommendation: Literal["建议继续", "谨慎继续", "不建议继续", "待澄清"] = "待澄清"
        warnings = ["没有找到技术、产品或服务要求"]
    elif hard_blockers:
        recommendation = "不建议继续"
        warnings = ["存在硬性技术/服务不匹配"]
    elif technical_gaps or material_gaps:
        recommendation = "谨慎继续"
        warnings = []
    else:
        recommendation = "建议继续"
        warnings = []

    return TechnicalMatchSummary(
        items=items,
        statusCounts=dict(counts),
        hardBlockers=hard_blockers,
        technicalGaps=technical_gaps,
        materialGaps=material_gaps,
        coverage=coverage,
        recommendation=recommendation,
        warnings=warnings,
    )
