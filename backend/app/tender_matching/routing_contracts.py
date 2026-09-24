"""Unified classification, workflow routing and chunk-granularity contracts.

The project historically exposed two partially overlapping routing systems:
``usageTags/route`` in the DocumentIR pipeline and ``requirementClass`` in the
legacy fine-review pipeline.  This module is the single source of truth for
the new fields.  The old fields are derived for compatibility.
"""
from __future__ import annotations

import re
from typing import Any


CONTENT_TYPES = (
    "project_info",
    "scope",
    "technical",
    "service_plan",
    "qualification",
    "performance",
    "personnel",
    "financial",
    "commercial",
    "compliance",
    "evaluation",
    "submission",
    "document_format",
    "rejection",
    "schedule",
    "pricing",
    "contract",
    "attachment",
    "other",
)

PRIMARY_ROUTES = (
    "project_info",
    "company_match",
    "rejection_check",
    "compliance_confirm",
    "fill",
    "writing_context",
    "display_only",
)

MATCH_DOMAINS = (
    "technical",
    "qualification",
    "performance",
    "personnel",
    "financial",
    "credit",
    "region",
)

DECISION_IMPACTS = ("hard_gate", "important", "normal", "context")
GRANULARITIES = ("section", "clause", "table_row", "field")
REVIEW_MODES = ("none", "auto", "assisted", "manual")

_EVIDENCE_DOMAINS = {
    "technical",
    "qualification",
    "performance",
    "personnel",
    "financial",
    "credit",
    "region",
}

SUBMISSION_SIGNAL_TERMS = (
    "提交投标文件",
    "提交响应文件",
    "提交技术响应文件",
    "提交商务响应文件",
    "递交投标文件",
    "递交响应文件",
    "递交技术响应文件",
    "递交商务响应文件",
    "投标文件提交",
    "响应文件提交",
    "投标文件递交",
    "响应文件递交",
    "电子投标文件提交",
    "电子投标文件递交",
    "提交报价文件",
    "递交报价文件",
    "投标文件截止",
    "响应文件截止",
    "文件提交截止",
    "文件递交截止",
)

PACKAGING_SIGNAL_TERMS = (
    "密封要求",
    "封装要求",
    "封套要求",
    "装订要求",
    "签署与封装",
    "签字与盖章",
    "签字盖章",
    "签署盖章",
    "加盖公章",
    "投标文件密封",
    "响应文件密封",
    "投标文件封装",
    "响应文件封装",
    "文件密封",
    "文件封装",
)

DOCUMENT_FORMAT_SIGNAL_TERMS = (
    "投标文件格式",
    "响应文件格式",
    "电子投标文件格式",
    "投标文件组成",
    "响应文件组成",
    "投标文件目录",
    "响应文件目录",
)

PRICING_SIGNAL_TERMS = (
    "投标报价",
    "报价要求",
    "报价金额",
    "报价明细",
    "分项报价",
    "最高限价",
    "预算金额",
    "控制价",
    "投标保证金",
    "履约保证金",
    "代理服务费",
    "招标代理服务费",
    "采购文件费",
    "标书费",
    "文件费",
    "费用明细",
    "费用标准",
    "收费标准",
    "费用由",
    "承担费用",
    "支付费用",
    "服务费",
)

QUALIFICATION_EVIDENCE_SIGNAL_TERMS = (
    "营业执照",
    "资质证书",
    "资格证书",
    "许可证",
    "认证证书",
    "专业技术能力",
    "财务报告",
    "财务状况报告",
    "审计报告",
    "资信证明",
    "纳税证明",
    "社会保障资金",
    "社保缴纳",
    "类似业绩",
    "项目业绩",
    "业绩证明",
    "信用中国",
    "失信被执行人",
    "重大税收违法",
)

CONFIRMATION_ONLY_SIGNAL_TERMS = (
    "资格承诺函",
    "资格承诺制",
    "承诺函",
    "承诺书",
    "声明函",
    "可不提供",
    "无需提供",
    "无须提供",
    "加盖公章",
    "加盖其单位公章",
    "单位公章",
    "签字盖章",
    "签署盖章",
    "电子投标文件",
    "电子响应文件",
    "按规定编制",
    "按规定提交",
    "评审节点",
    "单位授权书",
    "授权委托书",
    "授权书",
    "本授权书",
    "授权的委托代理人",
    "联合体协议",
    "分包意向协议",
    "提交相应的全部资料",
    "提交全部资料",
    "其他证明材料或资料",
    "上述资格要求",
    "格式可参考",
    "澄清或修改",
    "招标文件载明",
    "澄清内容",
    "以澄清内容为准",
    "法律法规",
    "政府采购法",
    "真实性",
    "合法性及有效性",
    "审查小组",
    "资格审查与评标",
    "资格审查工作的组织",
    "评标委员会",
    "评标工作的组织",
    "评标报告",
    "质疑人",
    "投诉人",
    "质疑处理",
    "投诉处理",
    "质疑函",
    "投诉函",
    "提出质疑",
    "提出投诉",
    "资格审查情况",
    "政府采购节能产品",
    "环境标志产品",
    "品目清单",
    "优先采购",
    "强制采购",
    "认证机构",
)

PERSONNEL_SIGNAL_TERMS = (
    "项目经理",
    "项目负责人",
    "技术负责人",
    "项目人员",
    "拟派人员",
    "项目团队",
    "人员配置",
    "人员配备",
    "人员要求",
    "驻场人员",
    "项目组成员",
    "工程师职称",
    "工程师证书",
    "注册工程师",
    "高级工程师",
    "中级工程师",
)

_BROAD_PERSONNEL_TERMS = (
    "工作人员",
    "专业人员",
    "技术人员",
)

_PROCEDURAL_CONTAINER_TERMS = (
    "资格审查与评标",
    "资格审查小组",
    "资格审查工作的组织",
    "评标委员会",
    "评标工作的组织",
    "评标报告",
    "开标会",
    "开标流程",
    "进口产品",
    "非本国产品",
    "节能产品",
    "环境标志产品",
)

_CONTEXT_ONLY_CLAUSE_TERMS = (
    "具体见附件",
    "详见附件",
    "参见附件",
    "附件：",
    "附件:",
)

ACCEPTANCE_SIGNAL_TERMS = (
    "验收标准",
    "验收方式",
    "验收要求",
    "验收条件",
    "验收报告",
    "验收合格",
    "交付及验收",
    "项目验收",
    "初验收",
    "终验收",
    "履约验收",
    "验收数据",
)

SERVICE_PLAN_SIGNAL_TERMS = (
    "服务方案",
    "项目实施",
    "项目实施方案",
    "实施方案",
    "服务实施方案",
    "实施服务",
    "项目实施服务",
    "项目理解",
    "组织实施方案",
    "组织方案",
    "质量保障方案",
    "应急服务方案",
    "应急预案",
    "培训方案",
    "培训服务",
    "运维方案",
    "运维服务",
    "售后服务方案",
    "售后服务",
    "质保服务",
    "服务保障",
    "服务响应",
    "实施计划",
)

_STRONG_TECHNICAL_TERMS = (
    "技术参数",
    "简要技术指标",
    "技术指标",
    "技术要求",
    "功能要求",
    "性能指标",
    "配置要求",
    "接口要求",
    "产品参数",
    "设备参数",
    "平台应",
    "系统应",
    "设备应",
    "必须支持",
    "需支持",
    "应支持",
    "支持导入",
    "支持加载",
    "支持渲染",
    "支持部署",
    "支持接入",
    "支持集成",
    "三维",
    "数字孪生",
    "可视化",
    "数据接入",
    "数据治理",
    "并发",
    "分辨率",
    "内存",
    "处理器",
    "协议",
    "接口",
)

_REJECTION_TERMS = (
    "废标",
    "投标无效",
    "响应无效",
    "无效投标",
    "否决",
    "不予受理",
    "视为无效",
    "不得参加",
)

_COMPLIANCE_TERMS = (
    "实质性响应",
    "不得负偏离",
    "签章",
    "承诺函",
    "承诺书",
    "声明函",
    "按格式",
    "按规定提交",
    *PACKAGING_SIGNAL_TERMS,
)

_FILL_TERMS = (
    "报价",
    "投标报价",
    "单价",
    "总价",
    "质保期",
    "服务期",
    "交付期",
    "工期",
    "付款方式",
    "付款条件",
    "响应情况",
    "偏离情况",
    "投标人填写",
    "请填写",
    "填写报价",
    "填写响应",
    "填写偏离",
)

_PROJECT_INFO_TERMS = (
    "项目名称",
    "项目编号",
    "采购人",
    "代理机构",
    "投标截止时间",
    "开标时间",
    "预算金额",
    "最高限价",
)

_TEMPLATE_TERMS = (
    "投标函",
    "响应表",
    "技术响应表",
    "商务响应表",
    "偏离及说明",
    "投标文件格式",
    "响应文件格式",
    "电子投标文件格式",
    "投标人填写",
    "报价明细表",
    "开标(报价)一览表",
    "签字盖章",
)


def compact_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _contains(value: str, terms: tuple[str, ...]) -> bool:
    return any(term.lower() in value for term in terms)


def has_explicit_technical_signal(text: str, type_hits: list[str] | None = None) -> bool:
    value = compact_text(text)
    return "technical" in set(type_hits or []) or _contains(value, _STRONG_TECHNICAL_TERMS)


def has_rejection_signal(text: str) -> bool:
    return _contains(compact_text(text), _REJECTION_TERMS)


def has_compliance_signal(text: str) -> bool:
    return _contains(compact_text(text), _COMPLIANCE_TERMS)


def has_submission_signal(text: str) -> bool:
    return _contains(compact_text(text), SUBMISSION_SIGNAL_TERMS)


def has_packaging_signal(text: str) -> bool:
    return _contains(compact_text(text), PACKAGING_SIGNAL_TERMS)


def has_pricing_signal(text: str) -> bool:
    return _contains(compact_text(text), PRICING_SIGNAL_TERMS)


def has_qualification_evidence_signal(text: str) -> bool:
    return _contains(compact_text(text), QUALIFICATION_EVIDENCE_SIGNAL_TERMS)


def is_confirmation_only_text(text: str, route_type: str) -> bool:
    """Return whether an evidence-looking clause is really a bid confirmation.

    Qualification and financial sections contain both enterprise facts and
    procedural instructions.  The latter must be confirmed in the bid file,
    not matched against the enterprise ledger.  A concrete credential keeps
    the evidence route unless the clause explicitly describes a commitment or
    an alternative submission path.
    """

    normalized_route = compact_text(route_type)
    if normalized_route not in {"qualification", "financial"}:
        return False
    value = compact_text(text)
    if re.match(r"^[（(][）)]", value) and len(value) <= 120:
        # Checkbox rows such as “（ ）提供财务报告的” are form fields,
        # not evidence that can be verified against the enterprise ledger.
        return True
    if _contains(
        value,
        (
            "我方具备",
            "我司具备",
            "本公司具备",
            "郑重声明",
            "本公司（联合体）",
            "本公司(联合体)",
        ),
    ):
        return True
    if _contains(value, CONFIRMATION_ONLY_SIGNAL_TERMS[:7]):
        return True
    if _contains(
        value,
        (
            "政府采购节能产品",
            "环境标志产品",
            "品目清单",
            "优先采购",
            "强制采购",
            "认证机构",
        ),
    ):
        return True
    if _contains(
        value,
        (
            "质疑人",
            "投诉人",
            "质疑处理",
            "投诉处理",
            "质疑函",
            "投诉函",
            "提出质疑",
            "提出投诉",
            "资格审查情况",
        ),
    ):
        return True
    if _contains(
        value,
        (
            "联合体各方不得再单独参加",
            "与其他供应商另外组成联合体",
            "联合体中有同类资质",
            "按照联合体分工承担相同工作",
        ),
    ) and not has_qualification_evidence_signal(value):
        # These are consortium participation rules, not facts that can be
        # verified from one bidder's enterprise ledger.
        return True
    generic_document_label = _contains(
        value,
        (
            "资格及资信证明文件",
            "资格及资信证明部分",
            "资格条件证明材料",
            "资格证明文件",
        ),
    )
    if generic_document_label:
        stripped = value
        for term in (
            "资格及资信证明文件",
            "资格及资信证明部分",
            "资格条件证明材料",
            "资格证明文件",
        ):
            stripped = stripped.replace(term, "")
        if not has_qualification_evidence_signal(stripped):
            return True
    if not _contains(value, CONFIRMATION_ONLY_SIGNAL_TERMS[7:]):
        return False
    return not has_qualification_evidence_signal(value)


def has_personnel_signal(text: str) -> bool:
    value = compact_text(text)
    if _contains(value, PERSONNEL_SIGNAL_TERMS):
        return True
    if _contains(value, _BROAD_PERSONNEL_TERMS) and _contains(
        value,
        (
            "配备",
            "配置",
            "岗位",
            "数量",
            "拟派",
            "驻场",
            "项目组",
            "人员名单",
            "人员要求",
        ),
    ):
        return True
    return "工程师" in value and any(
        term in value
        for term in (
            "职称",
            "证书",
            "资格",
            "资质",
            "配备",
            "配置",
            "拟派",
            "驻场",
            "技术工程师",
            "项目工程师",
            "专业工程师",
        )
    )


def has_acceptance_signal(text: str) -> bool:
    return _contains(compact_text(text), ACCEPTANCE_SIGNAL_TERMS)


def has_service_plan_signal(text: str) -> bool:
    return _contains(compact_text(text), SERVICE_PLAN_SIGNAL_TERMS)


def has_fill_signal(text: str) -> bool:
    return _contains(compact_text(text), _FILL_TERMS)


def has_template_signal(text: str) -> bool:
    return _contains(compact_text(text), _TEMPLATE_TERMS)


def _normal_type(value: str) -> str:
    token = compact_text(value)
    aliases = {
        "basic": "project_info",
        "invitation": "project_info",
        "procurement": "scope",
        "instructions": "submission",
        "document": "document_format",
        "acceptance": "technical",
        "appendix": "attachment",
    }
    token = aliases.get(token, token)
    return token if token in CONTENT_TYPES else "other"


def _candidate_route_type(
    content_type: str,
    type_hits: list[str],
    text: str,
    *,
    section_level: bool,
    is_rejection: bool,
    container_type: str = "",
) -> str:
    raw_primary = compact_text(content_type)
    primary = _normal_type(content_type)
    hits = [_normal_type(item) for item in type_hits]
    hit_set = set(hits)

    # A container chapter often mentions rejection rules in its descendants.
    # Keep those as multi-type hits, but do not let one descendant phrase
    # hijack the whole chapter's primary workflow.
    direct_rejection = (
        primary == "rejection"
        or is_rejection
        or (has_rejection_signal(text) and not section_level)
    )
    if direct_rejection:
        return "rejection"

    if (
        not section_level
        and (
            primary in {"technical", "commercial", "scope", "attachment", "other"}
            or bool({"technical", "commercial"} & hit_set)
        )
        and _is_context_only_clause(text)
    ):
        # A cross-reference such as “技术要求具体见附件” supplies context
        # only.  The referenced attachment owns the actual requirements.
        return "scope"

    if section_level and "document_format" in hit_set and has_template_signal(text):
        # A template section may also contain technical words.  Keep both
        # type hits, but route the section to response filling/confirmation.
        return "document_format"

    # A response template remains a fill workflow even when its title also
    # contains technical words.  Actual technical rows are re-routed later
    # by table-row extraction.
    if primary == "document_format":
        return "document_format"

    # Basic/instruction chapters are containers.  Their summaries commonly
    # mention qualification, pricing, schedule, and rejection rules, but the
    # detailed clauses below must own those routes.  Do not send the whole
    # container to enterprise matching.
    if section_level and primary in {"project_info", "scope", "submission"}:
        return primary

    if section_level and primary in _EVIDENCE_DOMAINS and _contains(
        compact_text(text),
        _PROCEDURAL_CONTAINER_TERMS,
    ):
        # 资格审查、评标、开标和政府采购政策章节是流程/政策容器。
        # 其中的具体资质或技术条款由子章节和条款级分类处理。
        return "evaluation"

    if (
        section_level
        and _normal_type(container_type) == "contract"
        and primary in _EVIDENCE_DOMAINS
    ):
        # 合同范本下的技术/附件词通常是合同字段或合同组成文件，
        # 不应把整个合同子章节送入企业能力匹配。
        return "contract"

    if primary == "service_plan":
        # 服务方案章节是方案上下文容器；其中的明确技术要求由条款级
        # 分类单独进入 technical/company_match，不能让整个章节变成匹配队列。
        if section_level:
            return "service_plan"
        return "technical" if has_explicit_technical_signal(text, hits) else "service_plan"

    if section_level and primary == "scope":
        # 采购范围/招标内容章节可能包含技术子章节，但章节本身只做
        # 导航和上下文展示，避免把整章当成一条企业能力要求。
        return "scope"

    effective_hit_set = (
        hit_set - {"rejection"}
        if section_level and primary != "rejection" and not is_rejection
        else hit_set
    )
    if raw_primary in {"basic", "scope", "submission", "other"} or primary in {
        "project_info",
        "scope",
        "submission",
        "other",
    }:
        for candidate in (
            "technical",
            "qualification",
            "performance",
            "personnel",
            "financial",
            "rejection",
            "pricing",
            "commercial",
        ):
            if candidate in effective_hit_set:
                return candidate
        if primary == "project_info":
            return "project_info"

    if primary == "project_info":
        return "project_info"

    return primary


def _is_context_only_clause(text: str) -> bool:
    value = compact_text(text)
    if not value or not _contains(value, _CONTEXT_ONLY_CLAUSE_TERMS):
        return False
    if not _contains(value, ("技术要求", "商务要求", "采购标的", "具体标的")):
        return False
    return not _contains(
        value,
        (
            "必须支持",
            "需支持",
            "应支持",
            "不少于",
            "不低于",
            "应提供",
            "须提供",
            "应当提供",
            "投标人应",
            "投标人须",
            "投标人必须",
        ),
    )


def _match_domain(content_type: str, route_type: str, text: str) -> str:
    if route_type in {
        "contract",
        "evaluation",
        "scope",
        "submission",
        "project_info",
        "document_format",
        "attachment",
    }:
        return ""
    if route_type == "technical" or content_type == "technical":
        return "technical"
    if route_type in _EVIDENCE_DOMAINS:
        if is_confirmation_only_text(text, route_type):
            return ""
        return route_type
    value = compact_text(text)
    if route_type == "company_match" and _contains(value, ("信用中国", "失信被执行人")):
        return "credit"
    return ""


def _route_for_type(route_type: str, text: str) -> str:
    if route_type == "project_info":
        return "project_info"
    if route_type == "compliance":
        return "compliance_confirm"
    if route_type in _EVIDENCE_DOMAINS:
        if is_confirmation_only_text(text, route_type):
            return "compliance_confirm"
        return "company_match"
    if route_type == "rejection":
        return "rejection_check"
    if route_type in {"document_format", "pricing", "commercial", "schedule", "contract"}:
        return "compliance_confirm" if has_compliance_signal(text) else "fill"
    if route_type == "submission":
        return "compliance_confirm" if has_compliance_signal(text) else "display_only"
    if route_type == "service_plan":
        return "writing_context"
    if route_type in {"evaluation", "attachment", "scope", "other"}:
        return "display_only"
    return "display_only"


def _decision_impact(
    content_type: str,
    route: str,
    text: str,
    *,
    mandatory: bool,
    is_rejection: bool,
) -> str:
    value = compact_text(text)
    if is_rejection or route == "rejection_check":
        return "hard_gate"
    if route == "company_match" and content_type == "qualification" and mandatory:
        return "hard_gate"
    if _contains(value, ("超出最高限价", "不得超过最高限价", "投标截止", "不得迟延")):
        return "hard_gate"
    if route in {"company_match", "rejection_check"} and mandatory:
        return "important"
    if route in {"fill", "compliance_confirm"} and mandatory:
        return "important"
    if route in {"writing_context", "display_only"}:
        return "context"
    return "normal"


def _granularity(
    route: str,
    *,
    section_level: bool,
    table_row: bool,
    template: bool,
) -> str:
    if route == "project_info":
        return "field"
    if route == "company_match":
        return "table_row" if table_row else "clause"
    if route == "rejection_check":
        return "clause"
    if route == "compliance_confirm":
        return "field" if template else "clause"
    if route == "fill":
        return "table_row" if table_row else "field"
    if route in {"writing_context", "display_only"}:
        return "section" if section_level else "clause"
    return "section"


def _review(
    *,
    content_type: str,
    type_hits: list[str],
    route: str,
    confidence: float,
    needs_human_review: bool,
    text: str,
) -> tuple[bool, str, str]:
    conflict_pairs = (
        {"technical", "document_format"},
        {"service_plan", "technical"},
        {"rejection", "qualification"},
        {"rejection", "pricing"},
        {"rejection", "document_format"},
    )
    conflict = any(pair.issubset(set(type_hits)) for pair in conflict_pairs)
    mixed_template = has_template_signal(text) and any(
        item in type_hits
        for item in (
            "technical",
            "qualification",
            "performance",
            "personnel",
            "financial",
        )
    )
    mixed_service = content_type == "service_plan" and has_explicit_technical_signal(text, type_hits)
    if route == "rejection_check":
        return True, "manual", "否决或无效投标风险必须人工确认"
    if mixed_template:
        return True, "manual", "模板章节同时包含技术或响应内容"
    if mixed_service:
        return True, "assisted", "服务方案中包含可独立核验的技术要求"
    if needs_human_review or confidence < 0.62:
        return True, "assisted", "分类置信度不足"
    if conflict:
        return True, "assisted", "章节或条款同时命中多个内容类型"
    return False, "none", ""


def resolve_routing(
    content_type: str,
    *,
    type_hits: list[str] | None = None,
    text: str = "",
    confidence: float = 1.0,
    needs_human_review: bool = False,
    mandatory: bool = True,
    is_rejection: bool = False,
    section_level: bool = False,
    table_row: bool = False,
    container_type: str = "",
    classification_source: str = "rule",
) -> dict[str, Any]:
    """Resolve the unified route from a classified content type.

    This function is deliberately deterministic.  An LLM can propose
    ``contentType/typeHits``; it cannot bypass these workflow and safety
    guards.
    """

    normalized_content_type = _normal_type(content_type)
    normalized_hits = []
    for item in [normalized_content_type, *(type_hits or [])]:
        item = _normal_type(item)
        if item not in normalized_hits:
            normalized_hits.append(item)

    route_type = _candidate_route_type(
        normalized_content_type,
        normalized_hits,
        text,
        section_level=section_level,
        is_rejection=is_rejection,
        container_type=container_type,
    )
    primary_route = _route_for_type(route_type, text)
    domain = _match_domain(normalized_content_type, route_type, text)
    template = has_template_signal(text) or normalized_content_type == "document_format"
    secondary_routes: list[str] = []

    if primary_route == "company_match" and (
        normalized_content_type == "technical" or "technical" in normalized_hits
    ):
        secondary_routes.append("writing_context")
    if primary_route == "fill" and template:
        secondary_routes.append("writing_context")
    if primary_route == "project_info":
        secondary_routes.append("display_only")

    impact = _decision_impact(
        normalized_content_type,
        primary_route,
        text,
        mandatory=mandatory,
        is_rejection=is_rejection,
    )
    granularity = _granularity(
        primary_route,
        section_level=section_level,
        table_row=table_row,
        template=template,
    )
    review_required, review_mode, review_reason = _review(
        content_type=normalized_content_type,
        type_hits=normalized_hits,
        route=primary_route,
        confidence=confidence,
        needs_human_review=needs_human_review,
        text=text,
    )
    legacy_policy = legacy_usage_policy(
        primary_route=primary_route,
        secondary_routes=secondary_routes,
        review_required=review_required,
        review_mode=review_mode,
        decision_impact=impact,
    )
    # Historical consumers treated procurement-scope chapters as a matching
    # context.  Keep that compatibility bit while the new frontend uses the
    # explicit ``primaryRoute=display_only`` contract.
    if normalized_content_type == "scope" and primary_route == "display_only":
        legacy_policy["matching"] = True

    return {
        "contentType": normalized_content_type,
        "typeHits": normalized_hits,
        "primaryRoute": primary_route,
        "secondaryRoutes": secondary_routes,
        "matchDomain": domain,
        "decisionImpact": impact,
        "granularity": granularity,
        "reviewRequired": review_required,
        "reviewMode": review_mode,
        "reviewReason": review_reason,
        "classificationSource": classification_source,
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "legacyUsageTags": [
            key for key, enabled in legacy_policy.items() if enabled
        ],
        "legacyRoutePolicy": legacy_policy,
    }


def legacy_usage_policy(
    *,
    primary_route: str,
    secondary_routes: list[str],
    review_required: bool,
    review_mode: str,
    decision_impact: str,
) -> dict[str, bool]:
    """Derive the historical six-boolean route policy."""

    routes = {primary_route, *secondary_routes}
    return {
        "screening": decision_impact in {"hard_gate", "important"},
        "matching": primary_route == "company_match",
        "display": bool({"project_info", "display_only"} & routes),
        "writing": bool({"fill", "writing_context"} & routes),
        "review": review_required,
        "manual_review": review_mode in {"assisted", "manual"},
    }


def legacy_requirement_class(primary_route: str) -> str:
    return {
        "company_match": "evidence",
        "rejection_check": "rejection",
        "compliance_confirm": "compliance",
        "fill": "fill",
        "writing_context": "writing",
        "project_info": "writing",
        "display_only": "writing",
    }.get(primary_route, "writing")


def unified_route_fields(
    category: str,
    *,
    type_hits: list[str] | None = None,
    text: str = "",
    confidence: float = 1.0,
    needs_human_review: bool = False,
    mandatory: bool = True,
    is_rejection: bool = False,
    section_level: bool = False,
    table_row: bool = False,
    classification_source: str = "rule",
) -> dict[str, Any]:
    """Return unified fields plus legacy fields for a requirement/row."""

    decision = resolve_routing(
        category,
        type_hits=type_hits,
        text=text,
        confidence=confidence,
        needs_human_review=needs_human_review,
        mandatory=mandatory,
        is_rejection=is_rejection,
        section_level=section_level,
        table_row=table_row,
        classification_source=classification_source,
    )
    policy = legacy_usage_policy(
        primary_route=decision["primaryRoute"],
        secondary_routes=decision["secondaryRoutes"],
        review_required=decision["reviewRequired"],
        review_mode=decision["reviewMode"],
        decision_impact=decision["decisionImpact"],
    )
    type_hit_values: list[str] = []
    for item in [category, *(type_hits or [])]:
        normalized = _normal_type(item)
        if normalized not in type_hit_values:
            type_hit_values.append(normalized)
    return {
        "contentType": _normal_type(category),
        "typeHits": type_hit_values,
        **decision,
        "legacyRequirementClass": legacy_requirement_class(decision["primaryRoute"]),
        "legacyUsageTags": [
            key for key, enabled in policy.items() if enabled
        ],
        "legacyRoutePolicy": policy,
    }
