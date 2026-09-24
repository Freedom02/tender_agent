from __future__ import annotations

import re
from datetime import date
from typing import Any

from .routing_contracts import has_personnel_signal, unified_route_fields

# 旧 4 态（保留用于兼容读取历史工作区；新逻辑不再产出「不确定」）。
MATCH_STATUSES = (
    "满足",
    "部分满足",
    "不满足",
    "待补材料",
)

# ═══════ 条款性质（RequirementClass）：决定「该怎么响应」，而非「满不满足」═══════
# ⚠ LEGACY（兼容层）：由 15 类 category 派生。当前主线由统一路由层
# routing_contracts.resolve_routing() 产出 primaryRoute/matchDomain，
# legacyRequirementClass 已改为由 primaryRoute 派生（见 routing_contracts.legacy_requirement_class）。
REQUIREMENT_CLASS = {
    "qualification": "evidence",
    "performance": "evidence",
    "personnel": "evidence",
    "financial": "evidence",
    "credit": "evidence",
    "region": "evidence",
    "technical": "evidence",
    "rejection": "rejection",
    "service_plan": "writing",
    "compliance": "compliance",
    "document": "compliance",
    "commercial": "fill",
    "pricing": "fill",
    "contract": "fill",
    "other": "writing",
}

# 非证据匹配区的默认工作流状态（不查库，给工作流默认态）。
CLASS_DEFAULT_STATUS = {
    "rejection": "待核查",
    "writing": "待编制",
    "compliance": "待确认",
    "fill": "待填写",
}

# 各性质区的合法状态词表（用于人工改状态时的校验与汇总）。
CLASS_STATUSES = {
    "evidence": ("满足", "部分满足", "不满足", "待补材料"),
    "rejection": ("待核查", "已确认安全", "有风险"),
    "writing": ("待编制", "已编制"),
    "compliance": ("待确认", "已完成"),
    "fill": ("待填写", "已完成"),
}

# 所有分区状态的大集合（用于前端过滤器与汇总计数）。
ALL_STATUSES = frozenset(
    CLASS_STATUSES["evidence"]
    + CLASS_STATUSES["rejection"]
    + CLASS_STATUSES["writing"]
    + CLASS_STATUSES["compliance"]
    + CLASS_STATUSES["fill"]
)


def requirement_class_of(row: dict[str, Any]) -> str:
    """返回条款的响应性质分区；缺省按 category 推断，兜底 writing。"""
    return str(
        row.get("requirementClass")
        or REQUIREMENT_CLASS.get(str(row.get("category") or "other"), "writing")
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


EVIDENCE_CATEGORIES = {
    category
    for category, requirement_class in REQUIREMENT_CLASS.items()
    if requirement_class == "evidence"
}

ONTOLOGY_CLASSES = frozenset(
    {
        "organization",
        "qualification_certificate",
        "credential",
        "performance_case",
        "staff_member",
        "financial_evidence",
        "technical_product",
        "technical_parameter",
    }
)

FACT_KEYS_BY_CATEGORY = {
    "qualification": ("organization", "qualification_certificate", "credential"),
    "performance": ("performance_case",),
    "personnel": ("staff_member",),
    "financial": ("financial_evidence",),
    "region": ("organization",),
    "technical": ("technical_product", "technical_parameter", "credential"),
}

MATCH_STRATEGIES = frozenset({"exact", "deterministic", "semantic", "vector", "llm_judge"})


def _route_values(raw: Any, key: str) -> list[str]:
    if not isinstance(raw, dict):
        return []
    values = raw.get(key)
    if not isinstance(values, list):
        values = [values] if values not in (None, "") else []
    result: list[str] = []
    for value in values:
        token = safe_text(value, 80).lower()
        if token and token not in result:
            result.append(token)
    return result


def normalize_route_hints(
    category: str,
    text: str,
    raw_hints: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate WorkBuddy route hints against the actual enterprise ontology.

    WorkBuddy may suggest where a requirement should be searched, but the backend
    owns the final gate.  Only the eight ontology fact classes are accepted; an
    evidence category with no valid route stays out of enterprise material
    matching.
    """
    resolved_category = coerce_requirement_category(category, text)
    allowed = set(FACT_KEYS_BY_CATEGORY.get(resolved_category, ()))
    hinted_classes = set(_route_values(raw_hints, "ontologyClasses"))
    hinted_fact_keys = set(_route_values(raw_hints, "factKeys"))
    hinted = (hinted_classes | hinted_fact_keys) & ONTOLOGY_CLASSES
    invalid = sorted((hinted_classes | hinted_fact_keys) - ONTOLOGY_CLASSES)
    ontology_classes = sorted(hinted & allowed) if hinted else sorted(allowed)
    strategies = [
        value
        for value in _route_values(raw_hints, "matchStrategy")
        if value in MATCH_STRATEGIES
    ]
    if not strategies and ontology_classes:
        strategies = ["deterministic", "semantic"]
    return {
        "ontologyClasses": ontology_classes,
        "factKeys": ontology_classes,
        "matchStrategy": strategies,
        "valid": bool(ontology_classes),
        "invalidHints": invalid,
    }


def requirement_class_for_route(
    category: str,
    text: str,
    raw_hints: dict[str, Any] | None = None,
) -> str:
    resolved_category = coerce_requirement_category(category, text)
    base_class = REQUIREMENT_CLASS.get(resolved_category, "writing")
    if base_class != "evidence":
        return base_class
    if normalize_route_hints(resolved_category, text, raw_hints).get("valid"):
        return "evidence"
    if resolved_category == "credit":
        return "compliance"
    return "writing"


def infer_evidence_category(text: str) -> str:
    """Infer hard-evidence categories from the requirement text.

    This is a defensive guardrail for external/model extraction.  The upstream
    extractor may label "提供审计报告/资信证明" as a writing task, but matching
    must still route it to the company evidence library.
    """
    value = re.sub(r"\s+", "", str(text or "")).lower()
    if not value:
        return ""
    if _manual_only_category(value):
        return ""
    if any(
        term in value
        for term in (
            "财务报告",
            "财务审计",
            "审计报告",
            "财务报表",
            "财务会计制度",
            "会计师事务所",
            "资信证明",
            "银行资信",
            "银行出具",
            "商业信誉",
            "资产负债",
            "注册资本",
            "营业收入",
            "资产总额",
        )
    ):
        return "financial"
    if any(term in value for term in ("类似业绩", "类似项目", "项目业绩", "合同业绩", "业绩合同", "案例")):
        return "performance"
    if has_personnel_signal(value):
        return "personnel"
    if any(
        term in value
        for term in (
            "依法缴纳税收",
            "税收缴纳",
            "缴纳社会保障资金",
            "社会保障资金",
            "纳税证明",
            "社保缴纳",
        )
    ):
        return "qualification"
    if any(term in value for term in ("信用中国", "失信被执行人", "重大税收违法", "严重违法失信", "重大违法记录")):
        return "credit"
    if any(term in value for term in ("资质", "证书", "认证", "许可证", "营业执照", "iso", "itss", "cmmi")):
        return "qualification"
    if any(term in value for term in ("注册地", "本地企业", "福建省内", "属地", "地域")):
        return "region"
    return ""


def _manual_only_category(value: str) -> str:
    """Return a non-evidence category for procedural/template-only clauses."""
    if not value:
        return ""
    if any(
        term in value
        for term in (
            "质疑人",
            "投诉人",
            "质疑函",
            "投诉书",
            "质疑与投诉",
            "质疑投诉",
            "提出质疑",
            "提出投诉",
            "接收质疑",
        )
    ):
        return "document"
    if any(
        term in value
        for term in (
            "资格承诺函",
            "承诺函",
            "本承诺函",
            "可不提供",
            "无需提供",
            "免于提供",
            "自行选择是否提供",
            "视为满足招标文件的资格要求",
            "允许供应商采用资格承诺制",
        )
    ):
        return "document"
    if re.fullmatch(r"(?:[（(]?\d+[）)]?)?(?:资格及资信证明部分|投标人的资格及资信证明文件)", value):
        return "document"
    if any(term in value for term in ("资格条件证明材料指", "资格证明材料指")):
        return "document"
    if any(term in value for term in ("中小企业声明函", "中小企业扶持政策", "价格扣除")):
        return "document"
    if any(term in value for term in ("节能产品", "环境标志产品", "政府强制采购")):
        return "document"
    if any(
        term in value
        for term in (
            "联合体协议",
            "联合体各方",
            "联合体中",
            "如本项目接受联合体",
            "若接受联合体",
        )
    ):
        return "document"
    if any(
        term in value
        for term in (
            "如涉及资质要求",
            "若涉及资质要求",
            "如涉及资质",
            "若涉及资质",
            "相应资质条件（若有）",
            "相应资质条件(若有)",
            "资质要求（若有）",
            "资质要求(若有)",
        )
    ):
        return "compliance"
    if any(term in value for term in ("翻译资质", "翻译机构", "翻译人员")):
        return "document"
    if any(
        term in value
        for term in (
            "依法应终止采购程序",
            "应重新采购",
            "投诉应有明确的请求",
            "必要的证明材料",
        )
    ):
        return "document"
    if "不同投标人" in value and any(term in value for term in ("同一人", "同一单位", "同一ip", "同一mac")):
        return "rejection"
    if any(term in value for term in ("不得出现报价", "报价信息", "投标报价信息")) and any(
        term in value for term in ("资格及资信", "资格证明", "商务部分", "技术部分")
    ):
        return "rejection"
    if any(
        term in value
        for term in (
            "投标人的资格及资信证明文件",
            "资格及资信证明部分",
            "单位授权书",
            "本授权书",
            "可不填写本授权书",
            "具备履行合同所必需设备和专业技术能力的声明函",
            "未要求投标人提供",
            "内容完整、清晰、整洁",
            "加盖其单位公章",
        )
    ):
        return "document"
    if any(
        term in value
        for term in (
            "投标人为事业单位",
            "投标人为社会团体",
            "投标人为合伙企业",
            "投标人为非企业",
            "投标人为自然人",
            "其他投标人应按照",
        )
    ):
        # 多主体"择一"条款：整段同时含"投标人为企业"与其它主体分支时，
        # 不应吞成 document，应交由主体类型判定 + 证照匹配决定是否满足
        # （例如"投标人为企业的提供营业执照；投标人为事业单位的提供…"）。
        if "投标人为企业" not in value:
            return "document"
    if "中小企业" in value:
        return "document"
    if any(term in value for term in ("系指", "所称", "是指", "专指")) and not any(
        term in value for term in ("须提供", "应提供", "必须提供")
    ):
        return "other"
    if any(term in value for term in ("分包承担主体", "中标后进行分包", "将本项目的非主体")):
        return "compliance"
    return ""


def coerce_requirement_category(category: str, text: str) -> str:
    category = safe_text(category or "other", 40).lower() or "other"
    if category == "rejection":
        return category
    manual_category = _manual_only_category(re.sub(r"\s+", "", str(text or "")).lower())
    if manual_category:
        return manual_category
    inferred = infer_evidence_category(text)
    if inferred:
        return inferred
    if category == "personnel" and not has_personnel_signal(text):
        return "other"
    return category if category in REQUIREMENT_CLASS else "other"


# 分类 -> 前端三大模块映射。未知要求也保留在商务和服务模块，避免被静默隐藏。
CATEGORY_MODULES = {
    "qualification": "资格审查要求",
    "compliance": "资格审查要求",
    "personnel": "资格审查要求",
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
    "other": "商务和服务要求",
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

POSITIVE_VERIFICATION_TERMS = ("已核验", "核验通过", "当前有效", "已上传原件", "有效")
PENDING_TERMS = ("待核验", "待补", "待确认", "待上传", "来源已登记", "资料已建模", "已建模")
HISTORICAL_TERMS = ("历史", "不能替代")
INVALID_TERMS = ("过期", "失效", "作废", "已撤销")


def safe_text(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


# --------------------------------------------------------------------------- #
# 章节判断的**单源契约**（P0 并轨）
#
# 「哪些章是要求章」这件事有两个判断者：本地 LLM（第一跳）与外部智能体（第二跳）。
# 如果两侧各写一套菜单格式与判据，必然漂移 —— 本项目已有教训：同一个分类器同时是
# 标题判据，改一处必须同步另一处。这里把**菜单格式**与**判据文字**都收敛到本模块，
# 两个判断者都从这里取，改一处即两侧同步。
# --------------------------------------------------------------------------- #
SECTION_JUDGE_INCLUDE = "技术/功能/性能/参数、商务、资格、验收、服务、交付、售后等"
SECTION_JUDGE_EXCLUDE = (
    "投标邀请、投标人须知、评标办法、评分标准、合同格式、质疑投诉、中标人确定、开标程序、监督"
)


def build_section_menu(sections: list[dict[str, Any]] | None) -> list[str]:
    """把章节树编成"章节菜单"行；本地 LLM 与智能体任务包**共用这一个构造**。

    行格式：``[SEC-0001] 第一章 投标邀请 / 7、招标文件的获取 | 段落数 38``

    · 用**完整章节路径**而非单层标题：只喂一层标题时模型看不到层级归属，
      例如「二、平台建设内容、技术要求：」的父级是「第二部分 前附表及投标人须知」，
      而判据要求排除投标人须知 ⇒ 整块技术要求被连带排除（D1 实测）。
    · `段落数` 是给判断者的规模信号（纯容器章 vs 实体要求章）。
    """
    lines: list[str] = []
    for section in sections or []:
        if not isinstance(section, dict):
            continue
        section_id = safe_text(section.get("sectionId"), 40)
        if not section_id:
            continue
        path = [str(item) for item in (section.get("sectionPath") or []) if str(item).strip()]
        label = safe_text(" / ".join(path) or section.get("heading"), 260)
        lines.append(f"[{section_id}] {label} | 段落数 {len(section.get('segmentIds') or [])}")
    return lines


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
    category = coerce_requirement_category(raw.get("category") or "other", raw_text)
    type_hits = [
        str(item)
        for item in (
            raw.get("typeHits")
            or raw.get("categoryHits")
            or [category]
        )
        if str(item)
    ]
    mandatory = bool(raw.get("mandatory", True))
    is_rejection = bool(raw.get("isRejection", category == "rejection"))
    unified_route = unified_route_fields(
        category,
        type_hits=type_hits,
        text=raw_text,
        confidence=float(raw.get("confidence") or 0.0) or 0.7,
        needs_human_review=bool(raw.get("needsHumanReview", False)),
        mandatory=mandatory,
        is_rejection=is_rejection,
        section_level=str(raw.get("granularity") or "") == "section",
        table_row=bool(raw.get("tableId") or raw.get("tableIds")),
        classification_source=str(raw.get("classificationSource") or "rule"),
    )
    route_hints = normalize_route_hints(category, raw_text, raw.get("routeHints"))
    requirement_class = safe_text(raw.get("requirementClass"), 30) or unified_route[
        "legacyRequirementClass"
    ]
    expected_class = unified_route["legacyRequirementClass"]
    if category in EVIDENCE_CATEGORIES or requirement_class not in CLASS_STATUSES:
        requirement_class = expected_class
    requirement_id = safe_text(
        raw.get("requirementId") or raw.get("id") or f"{source_stage}-requirement",
        100,
    )
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
        "requirementClass": requirement_class,
        "contentType": unified_route["contentType"],
        "directTypeHits": [
            str(item)
            for item in (raw.get("directTypeHits") or [])
            if str(item)
        ],
        "inheritedType": safe_text(raw.get("inheritedType"), 40) or "other",
        "typeHits": unified_route["typeHits"],
        "primaryRoute": unified_route["primaryRoute"],
        "secondaryRoutes": unified_route["secondaryRoutes"],
        "matchDomain": unified_route["matchDomain"],
        "decisionImpact": unified_route["decisionImpact"],
        "granularity": unified_route["granularity"],
        "reviewRequired": unified_route["reviewRequired"],
        "reviewMode": unified_route["reviewMode"],
        "reviewReason": unified_route["reviewReason"],
        "classificationSource": unified_route["classificationSource"],
        "productName": safe_text(
            raw.get("productName")
            or raw.get("itemName")
            or raw.get("targetName"),
            240,
        ),
        "targetType": safe_text(raw.get("targetType"), 40),
        "sourceColumn": safe_text(raw.get("sourceColumn"), 80),
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
        "displayGroup": safe_text(raw.get("displayGroup"), 80),
        "handlingMode": safe_text(raw.get("handlingMode"), 40),
        "routeHints": route_hints,
        "routeTags": unified_route["legacyUsageTags"],
        "sourceFileId": safe_text(raw.get("sourceFileId") or source_file_id, 160),
        "sourceLocator": safe_text(raw.get("sourceLocator"), 120),
        "parentRequirementId": safe_text(raw.get("parentRequirementId"), 100),
        "clauseIndex": raw.get("clauseIndex"),
        "clauseCount": raw.get("clauseCount"),
        "sourceSegmentId": safe_text(raw.get("sourceSegmentId"), 120),
        "sourceSegmentIds": [
            safe_text(value, 120)
            for value in (raw.get("sourceSegmentIds") or [])
            if safe_text(value, 120)
        ],
        "quantity": raw.get("quantity"),
        "timeLimit": safe_text(raw.get("timeLimit"), 160),
        "confidence": unified_route["confidence"],
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


def _normalized_row_status(row: dict[str, Any]) -> str:
    """把旧词表状态归一化到新分区词表，保证历史/公告数据与细匹配共用一套汇总。

    - evidence 区旧「不确定」→「待补材料」；
    - rejection 区旧「满足/不满足/不确定」→「已确认安全/有风险/待核查」。
    """
    status = str(row.get("status") or "待补材料")
    requirement_class = requirement_class_of(row)
    if status == "不确定":
        return "待补材料" if requirement_class == "evidence" else CLASS_DEFAULT_STATUS.get(requirement_class, "待编制")
    if requirement_class == "rejection":
        if status == "满足":
            return "已确认安全"
        if status == "不满足":
            return "有风险"
        if status == "部分满足":
            return "待核查"
    return status


def summarize_comparisons(rows: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {}
    class_counts: dict[str, int] = {}
    for row in rows:
        status = _normalized_row_status(row)
        status_counts[status] = status_counts.get(status, 0) + 1
        class_counts[requirement_class_of(row)] = class_counts.get(requirement_class_of(row), 0) + 1

    evidence_rows = [row for row in rows if requirement_class_of(row) == "evidence"]
    evidence_required = [
        row for row in evidence_rows
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
        if row.get("status") == "满足"
    ]
    rejection_rows = [
        row for row in rows
        if row.get("isRejection") or requirement_class_of(row) == "rejection"
    ]

    def _rejection_status(row: dict[str, Any]) -> str:
        return _normalized_row_status(row)

    rejection_risks = [
        row for row in rejection_rows
        if _rejection_status(row) == "有风险"
    ]
    rejection_pending = [
        row for row in rejection_rows
        if _rejection_status(row) not in {"已确认安全", "有风险"}
    ]
    hard_blockers = [
        row for row in rejection_rows
        if _rejection_status(row) == "有风险"
    ]

    def percentage(numerator: int, denominator: int) -> int:
        return round(numerator * 100 / denominator) if denominator else 0

    def completion(class_name: str, done_statuses: tuple[str, ...]) -> dict[str, int]:
        rows_in_class = [row for row in rows if requirement_class_of(row) == class_name]
        done = sum(1 for row in rows_in_class if _normalized_row_status(row) in done_statuses)
        return {"total": len(rows_in_class), "done": done, "rate": percentage(done, len(rows_in_class))}

    return {
        "totalRequirementCount": len(rows),
        "statusCounts": status_counts,
        # 分区计数（四区）
        "classCounts": class_counts,
        "evidenceStatusCounts": {
            status: sum(1 for row in evidence_rows if _normalized_row_status(row) == status)
            for status in CLASS_STATUSES["evidence"]
        },
        # 否决红线区
        "rejectionCount": len(rejection_rows),
        "rejectionPendingCount": len(rejection_pending),
        "rejectionRiskCount": len(rejection_risks),
        "rejectionSafeCount": sum(1 for row in rejection_rows if _rejection_status(row) == "已确认安全"),
        "hardBlockerCount": len(hard_blockers),
        # 待补材料（evidence 区缺数据）
        "pendingMaterialCount": sum(1 for row in evidence_rows if _normalized_row_status(row) == "待补材料"),
        # ③④ 区完成率
        "writing": completion("writing", ("已编制",)),
        "compliance": completion("compliance", ("已完成",)),
        "fill": completion("fill", ("已完成",)),
        # 兼容旧字段
        "mandatoryCount": len(mandatory_rows),
        "mandatoryClosedCount": len(closed_mandatory),
        "mandatoryClosureRate": percentage(len(closed_mandatory), len(mandatory_rows)),
        "evidenceRequiredCount": len(evidence_required),
        "evidenceCoveredCount": len(evidence_covered),
        "verifiedEvidenceCount": len(verified_evidence),
        "evidenceCoverageRate": percentage(len(evidence_covered), len(evidence_required)),
        "uncertainCount": status_counts.get("待补材料", 0),
        "notSatisfiedCount": status_counts.get("不满足", 0),
    }
