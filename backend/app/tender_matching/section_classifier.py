"""Section classification and workflow routing.

Classification is a projection over the already-built SectionTree.  It never
changes section boundaries or source text.  A caller may provide an optional
LLM callback for low-confidence sections; the default path is deterministic
and does not depend on WorkBuddy or a particular model provider.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .document_models import DocumentIR, DocumentSection
from .routing_contracts import (
    ACCEPTANCE_SIGNAL_TERMS,
    PACKAGING_SIGNAL_TERMS,
    PERSONNEL_SIGNAL_TERMS,
    PRICING_SIGNAL_TERMS,
    SERVICE_PLAN_SIGNAL_TERMS,
    SUBMISSION_SIGNAL_TERMS,
    has_pricing_signal,
    has_submission_signal,
    resolve_routing,
)


SECTION_TYPES = (
    "basic",
    "scope",
    "technical",
    "service_plan",
    "qualification",
    "performance",
    "personnel",
    "financial",
    "commercial",
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

USAGE_TAGS = (
    "screening",
    "matching",
    "display",
    "writing",
    "review",
    "manual_review",
)

CLASSIFICATION_SOURCES = ("rule", "content_rule", "llm", "fallback")

LLM_SECTION_CLASSIFICATION_PROMPT = """\
你是招标文件章节分类与路由器。只根据给定的章节标题、父章节路径、表头和正文摘要分类，不改写原文，不创建或删除章节。
一个章节可以有多个 typeHits，但只能有一个 primaryType。primaryType 表示主要消费流程，typeHits 表示本章节实际涉及的全部内容类型。
先判断内容类型，再由后端统一路由表决定 primaryRoute、matchDomain、decisionImpact 和 granularity，不要自行发明路由名称。
必须优先按内容和下游用途判断：
- 电子投标文件格式、投标函、响应表、报价表等需要投标人填写的模板，primaryType=document_format；
- 投标无效、废标、否决、不合格、超过最高限价、未签章等否决性情形，primaryType=rejection；
- 真实产品功能、技术指标、服务响应要求，primaryType=technical 或 service_plan；
- 付款、交货、质保、商务条件，primaryType=commercial；
保留多类型 typeHits；technical 与 document_format 同时命中时不要删除 technical。
返回 JSON。无法确定时 primaryType=other、confidence<0.6、needsHumanReview=true。
"""


class RoutePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screening: bool = False
    matching: bool = False
    display: bool = False
    writing: bool = False
    review: bool = False
    manual_review: bool = False


class SectionClassificationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(..., alias="sectionId")
    heading: str
    section_path: list[str] = Field(default_factory=list, alias="sectionPath")
    parent_type: str = Field("", alias="parentType")
    container_type: str = Field("", alias="containerType")
    child_headings: list[str] = Field(default_factory=list, alias="childHeadings")
    sibling_headings: list[str] = Field(default_factory=list, alias="siblingHeadings")
    text_excerpt: str = Field("", alias="textExcerpt")
    table_headers: list[str] = Field(default_factory=list, alias="tableHeaders")


class LLMSectionClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field("", alias="sectionId")
    primary_type: Literal[SECTION_TYPES] = Field("other", alias="primaryType")
    type_hits: list[Literal[SECTION_TYPES]] = Field(default_factory=list, alias="typeHits")
    content_type: str = Field("other", alias="contentType")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    rationale: str = ""
    needs_human_review: bool = Field(True, alias="needsHumanReview")

    @field_validator("primary_type", mode="before")
    @classmethod
    def normalize_primary_type(cls, value: Any) -> str:
        token = str(value or "").strip()
        normalized = _canonical(token)
        if normalized == "other" and token not in {"", "other"} and token not in _TYPE_ALIASES:
            return token
        return normalized

    @field_validator("type_hits", mode="before")
    @classmethod
    def normalize_type_hits(cls, value: Any) -> list[str]:
        if isinstance(value, str):
            value = re.split(r"[,，、;；\s]+", value)
        if not isinstance(value, (list, tuple, set)):
            value = []
        result: list[str] = []
        for item in value:
            normalized = _canonical(item)
            if normalized in SECTION_TYPES and normalized not in result:
                result.append(normalized)
        return result


class SectionClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    section_id: str = Field(..., alias="sectionId")
    original_section_type: str = Field("other", alias="originalSectionType")
    heading_type: Literal[SECTION_TYPES] = Field("other", alias="headingType")
    direct_type_hits: list[Literal[SECTION_TYPES]] = Field(
        default_factory=list,
        alias="directTypeHits",
    )
    inherited_type: Literal[SECTION_TYPES] = Field("other", alias="inheritedType")
    container_type: Literal[SECTION_TYPES] = Field("other", alias="containerType")
    descendant_type_hits: list[Literal[SECTION_TYPES]] = Field(
        default_factory=list,
        alias="descendantTypeHits",
    )
    primary_type: Literal[SECTION_TYPES] = Field("other", alias="primaryType")
    type_hits: list[Literal[SECTION_TYPES]] = Field(default_factory=list, alias="typeHits")
    content_type: str = Field("other", alias="contentType")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    classification_source: Literal[CLASSIFICATION_SOURCES] = Field(
        "fallback",
        alias="classificationSource",
    )
    usage_tags: list[Literal[USAGE_TAGS]] = Field(default_factory=list, alias="usageTags")
    route: RoutePolicy = Field(default_factory=RoutePolicy)
    needs_human_review: bool = Field(False, alias="needsHumanReview")
    primary_route: str = Field("display_only", alias="primaryRoute")
    secondary_routes: list[str] = Field(default_factory=list, alias="secondaryRoutes")
    match_domain: str = Field("", alias="matchDomain")
    decision_impact: str = Field("normal", alias="decisionImpact")
    granularity: str = Field("section", alias="granularity")
    review_required: bool = Field(False, alias="reviewRequired")
    review_mode: str = Field("none", alias="reviewMode")
    review_reason: str = Field("", alias="reviewReason")
    rationale: str = ""


class ClauseClassification(BaseModel):
    """Classification for one requirement-like sentence inside a section."""

    model_config = ConfigDict(extra="forbid")

    primary_type: Literal[SECTION_TYPES] = Field("other", alias="primaryType")
    direct_type_hits: list[Literal[SECTION_TYPES]] = Field(
        default_factory=list,
        alias="directTypeHits",
    )
    inherited_type: Literal[SECTION_TYPES] = Field("other", alias="inheritedType")
    type_hits: list[Literal[SECTION_TYPES]] = Field(default_factory=list, alias="typeHits")
    content_type: str = Field("other", alias="contentType")
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    classification_source: Literal["rule", "inherited", "fallback"] = Field(
        "fallback",
        alias="classificationSource",
    )
    usage_tags: list[Literal[USAGE_TAGS]] = Field(default_factory=list, alias="usageTags")
    route: RoutePolicy = Field(default_factory=RoutePolicy)
    needs_human_review: bool = Field(True, alias="needsHumanReview")
    primary_route: str = Field("display_only", alias="primaryRoute")
    secondary_routes: list[str] = Field(default_factory=list, alias="secondaryRoutes")
    match_domain: str = Field("", alias="matchDomain")
    decision_impact: str = Field("normal", alias="decisionImpact")
    granularity: str = Field("clause", alias="granularity")
    review_required: bool = Field(True, alias="reviewRequired")
    review_mode: str = Field("assisted", alias="reviewMode")
    review_reason: str = Field("", alias="reviewReason")
    rationale: str = ""


ROUTE_POLICIES: dict[str, RoutePolicy] = {
    "basic": RoutePolicy(screening=True, display=True, writing=True),
    "scope": RoutePolicy(screening=True, matching=True, display=True, writing=True),
    "technical": RoutePolicy(
        screening=True,
        matching=True,
        display=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "service_plan": RoutePolicy(
        screening=True,
        matching=True,
        display=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "qualification": RoutePolicy(
        screening=True,
        matching=True,
        display=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "performance": RoutePolicy(
        screening=True,
        matching=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "personnel": RoutePolicy(
        screening=True,
        matching=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "financial": RoutePolicy(
        screening=True,
        matching=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "commercial": RoutePolicy(
        screening=True,
        matching=True,
        display=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "evaluation": RoutePolicy(
        screening=True,
        display=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "submission": RoutePolicy(display=True, writing=True, review=True, manual_review=True),
    "document_format": RoutePolicy(display=True, writing=True, review=True, manual_review=True),
    "rejection": RoutePolicy(
        screening=True,
        display=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "schedule": RoutePolicy(screening=True, display=True, writing=True),
    "pricing": RoutePolicy(
        screening=True,
        display=True,
        writing=True,
        review=True,
        manual_review=True,
    ),
    "contract": RoutePolicy(display=True, writing=True, review=True, manual_review=True),
    "attachment": RoutePolicy(writing=True, review=True, manual_review=True),
    "other": RoutePolicy(display=True, manual_review=True),
}

_TYPE_ALIASES = {
    "invitation": "basic",
    "instructions": "submission",
    "procurement": "scope",
    "compliance": "qualification",
    "acceptance": "technical",
    "appendix": "attachment",
}

_CONTENT_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("technical", ("技术要求", "技术参数", "功能要求", "性能指标", "配置要求", "接口要求")),
    ("service_plan", SERVICE_PLAN_SIGNAL_TERMS),
    ("qualification", ("资格审查", "资质要求", "资格条件", "认证", "营业执照")),
    ("performance", ("业绩要求", "类似业绩", "项目业绩", "业绩证明")),
    ("personnel", PERSONNEL_SIGNAL_TERMS),
    ("financial", ("财务状况", "财务要求", "审计报告", "纳税")),
    ("commercial", ("商务要求", "商务条款", "交付期限", "质保", "付款")),
    ("evaluation", ("评标办法", "评审因素", "评分标准", "评标标准")),
    (
        "submission",
        (
            "投标文件组成",
            "响应文件组成",
            "投标人须知",
            "供应商须知",
            "响应文件须知",
            *SUBMISSION_SIGNAL_TERMS,
        ),
    ),
    (
        "document_format",
        (
            "投标文件格式",
            "响应文件格式",
            "电子投标文件格式",
            *PACKAGING_SIGNAL_TERMS,
        ),
    ),
    ("rejection", ("废标", "无效投标", "否决", "无效响应")),
    ("schedule", ("时间安排", "截止时间", "开标时间", "工期", "服务期限")),
    (
        "pricing",
        (
            "报价",
            "投标报价",
            "报价要求",
            "分项报价",
            "预算",
            "最高限价",
            "投标保证金",
            "履约保证金",
            "代理服务费",
            "采购文件费",
        ),
    ),
    (
        "contract",
        (
            "合同条款",
            "合同格式",
            "违约责任",
            "合同履行",
            "履约期限",
            "履约责任",
            "履约义务",
            "履约要求",
            *ACCEPTANCE_SIGNAL_TERMS,
        ),
    ),
    ("attachment", ("附件", "附录", "响应表", "承诺函")),
    ("scope", ("项目概况", "采购范围", "建设内容", "采购内容")),
)

_CLAUSE_TYPE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "rejection",
        (
            "投标无效",
            "无效投标",
            "废标",
            "否决",
            "审查不合格",
            "不予退还",
            "超过最高限价",
            "超过预算金额",
            "不能接受的附加条件",
        ),
    ),
    (
        "document_format",
        (
            "响应表",
            "投标函",
            "报价明细表",
            "开标(报价)一览表",
            "偏离及说明",
            *PACKAGING_SIGNAL_TERMS,
            "投标人填写",
        ),
    ),
    (
        "qualification",
        (
            "资格要求",
            "资格条件",
            "资格审查",
            "营业执照",
            "资质证书",
            "许可证",
            "授权书",
            "授权委托书",
            "身份证明",
            "身份证件",
            "信用中国",
            "联合体",
            "社会保障资金",
            "依法缴纳税收",
            "纳税证明",
            "专业技术能力",
        ),
    ),
    ("performance", ("类似业绩", "项目业绩", "业绩证明", "案例证明")),
    ("personnel", PERSONNEL_SIGNAL_TERMS),
    ("financial", ("财务报告", "审计报告", "财务状况", "纳税证明", "资信证明")),
    (
        "technical",
        (
            "技术要求",
            "技术参数",
            "功能要求",
            "性能指标",
            "配置要求",
            "接口要求",
            "必须支持",
            "需支持",
            "应支持",
            "支持导入",
            "支持加载",
            "支持渲染",
            "支持部署",
            "支持接入",
            "支持集成",
            "支持配置",
            "支持管理",
            "平台应",
            "系统应",
            "设备应",
            "模型",
            "渲染",
        ),
    ),
    ("service_plan", SERVICE_PLAN_SIGNAL_TERMS),
    (
        "commercial",
        (
            "商务条件",
            "商务要求",
            "交付期限",
            "交货期",
            "交货地点",
            "交货条件",
            "合同签订后",
            "付款方式",
            "付款条件",
            "质量保证期",
            "质保期",
            *ACCEPTANCE_SIGNAL_TERMS,
        ),
    ),
    (
        "pricing",
        (
            *PRICING_SIGNAL_TERMS,
            "预算金额",
        ),
    ),
    ("schedule", ("截止时间", "开标时间", "服务期限", "合同履行期限", "工期")),
    ("evaluation", ("评标办法", "评分标准", "评审因素", "评标委员会")),
    (
        "contract",
        (
            "合同条款",
            "合同主要条款",
            "采购合同",
            "服务合同",
            "合同范本",
            "合同履行",
            "合同义务",
            "合同价款",
            "合同金额",
            "合同变更",
            "合同解除",
            "违约责任",
            "违约",
            "履约期限",
            "履约责任",
            "履约义务",
            "履约要求",
            *ACCEPTANCE_SIGNAL_TERMS,
        ),
    ),
    ("submission", SUBMISSION_SIGNAL_TERMS),
    (
        "scope",
        (
            "采购范围",
            "采购内容",
            "招标内容及要求",
            "建设内容",
            "采购标的",
            "项目概况",
        ),
    ),
)

_EMBEDDED_REQUIREMENT_TYPES = {
    "technical",
    "qualification",
    "performance",
    "personnel",
    "financial",
    "credit",
    "region",
    "rejection",
}


_TEMPLATE_CONTEXT_TERMS = (
    "\u7535\u5b50\u6295\u6807\u6587\u4ef6\u683c\u5f0f",
    "\u6295\u6807\u6587\u4ef6\u683c\u5f0f",
    "\u6295\u6807\u6587\u4ef6\u7ec4\u6210",
)
_TEMPLATE_HEADING_TERMS = (
    "\u54cd\u5e94\u8868",
    "\u6295\u6807\u51fd",
    "\u62a5\u4ef7\u660e\u7ec6\u8868",
    "\u5f00\u6807(\u62a5\u4ef7)\u4e00\u89c8\u8868",
    "\u4e00\u89c8\u8868",
    "\u8d44\u683c\u53ca\u8d44\u4fe1\u8bc1\u660e\u90e8\u5206",
    "\u9879\u76ee\u5185\u5bb9",
)
_REJECTION_TERMS = (
    "\u6295\u6807\u65e0\u6548",
    "\u65e0\u6548\u6295\u6807",
    "\u5e9f\u6807",
    "\u5426\u51b3",
    "\u4e0d\u5408\u683c\u9879",
    "\u5ba1\u67e5\u4e0d\u5408\u683c",
    "\u4e0d\u4e88\u9000\u8fd8",
    "\u4e0d\u80fd\u63a5\u53d7\u7684\u9644\u52a0\u6761\u4ef6",
    "\u4e0d\u7b26\u5408\u62db\u6807\u6587\u4ef6\u4e2d\u89c4\u5b9a\u7684\u8d44\u683c\u8981\u6c42",
    "\u8d85\u8fc7\u62db\u6807\u6587\u4ef6\u4e2d\u89c4\u5b9a\u7684\u9884\u7b97\u91d1\u989d",
    "\u8d85\u8fc7\u62db\u6807\u6587\u4ef6\u4e2d\u89c4\u5b9a\u7684\u6700\u9ad8\u9650\u4ef7",
    "\u672a\u6309\u7167\u62db\u6807\u6587\u4ef6\u8981\u6c42\u7b7e\u7f72",
)
_PRIMARY_REJECTION_TERMS = (
    "\u6295\u6807\u65e0\u6548",
    "\u65e0\u6548\u6295\u6807",
    "\u5e9f\u6807",
    "\u5426\u51b3",
    "\u4e0d\u5408\u683c\u9879",
    "\u5ba1\u67e5\u4e0d\u5408\u683c",
    "\u4e0d\u4e88\u9000\u8fd8",
    "\u4e0d\u80fd\u63a5\u53d7\u7684\u9644\u52a0\u6761\u4ef6",
    "\u4e0d\u7b26\u5408\u62db\u6807\u6587\u4ef6\u4e2d\u89c4\u5b9a\u7684\u8d44\u683c\u8981\u6c42",
    "\u8d85\u8fc7\u62db\u6807\u6587\u4ef6\u4e2d\u89c4\u5b9a\u7684\u9884\u7b97\u91d1\u989d",
    "\u8d85\u8fc7\u62db\u6807\u6587\u4ef6\u4e2d\u89c4\u5b9a\u7684\u6700\u9ad8\u9650\u4ef7",
    "\u672a\u6309\u7167\u62db\u6807\u6587\u4ef6\u8981\u6c42\u7b7e\u7f72",
)
_PROCUREMENT_REQUIREMENT_CHAPTER = "\u7b2c\u4e94\u7ae0 \u62db\u6807\u5185\u5bb9\u53ca\u8981\u6c42"


LLMSectionClassifier = Callable[
    [SectionClassificationInput],
    Mapping[str, Any] | LLMSectionClassification,
]


def _canonical(value: Any) -> str:
    token = str(value or "").strip()
    return _TYPE_ALIASES.get(token, token if token in SECTION_TYPES else "other")


def _clean(value: Any, limit: int = 1400) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _compact_text(*values: Any, limit: int = 4000) -> str:
    return re.sub(r"\s+", "", " ".join(str(value or "") for value in values))[:limit]


def _section_path_text(section: DocumentSection) -> str:
    return " ".join(section.section_path or [])


def _is_template_section(section: DocumentSection) -> bool:
    heading = str(section.heading or "")
    path_text = _section_path_text(section)
    compact = _compact_text(heading, path_text, section.text[:600])
    if any(term in path_text for term in _TEMPLATE_CONTEXT_TERMS):
        return True
    if "响应表" in heading:
        return True
    if "资格及资信证明部分" in heading:
        return True
    if "项目内容" in heading and any(
        term in compact
        for term in (
            "采购标的及数量",
            "规格型号",
            "关键部件",
            "品牌",
            "报价部分",
            "___",
        )
    ):
        return True
    if any(term in heading for term in _TEMPLATE_HEADING_TERMS):
        if (
            "填" in compact
            or "投标响应" in compact
            or "偏离" in compact
            or "加盖" in compact
            or "格式" in compact
            or not str(section.text or "").strip()
        ):
            return True
    return False


def _has_rejection_signal(section: DocumentSection) -> bool:
    text = _compact_text(section.heading, _section_path_text(section), section.text[:1200])
    return any(term in text for term in _REJECTION_TERMS)


def _is_primary_rejection_section(section: DocumentSection) -> bool:
    heading = _compact_text(section.heading)
    if any(term in heading for term in _PRIMARY_REJECTION_TERMS):
        return True
    excerpt = _compact_text(section.text[:260])
    return any(term in excerpt for term in _PRIMARY_REJECTION_TERMS) and not section.is_major


def _is_procurement_requirement_overview(section: DocumentSection) -> bool:
    heading = str(section.heading or "").strip()
    text = _compact_text(section.text[:1800])
    return heading == _PROCUREMENT_REQUIREMENT_CHAPTER and (
        "\u6280\u672f\u548c\u670d\u52a1\u8981\u6c42" in text
        or "\u5546\u52a1\u8981\u6c42" in text
        or "\u91c7\u8d2d\u6807\u7684" in text
    )


def _unique_types(*groups: Any) -> list[str]:
    result: list[str] = []
    for group in groups:
        values = group if isinstance(group, (list, tuple, set)) else [group]
        for value in values:
            normalized = _canonical(value)
            if normalized in SECTION_TYPES and normalized not in result:
                result.append(normalized)
    return result


def _section(value: DocumentSection | Mapping[str, Any]) -> DocumentSection:
    return value if isinstance(value, DocumentSection) else DocumentSection.model_validate(value)


def _input_for_section(
    section: DocumentSection,
    *,
    text_override: str | None = None,
    parent_type: str = "",
    container_type: str = "",
    child_headings: list[str] | None = None,
    sibling_headings: list[str] | None = None,
) -> SectionClassificationInput:
    return SectionClassificationInput(
        sectionId=section.section_id or section.id,
        heading=section.heading,
        sectionPath=section.section_path,
        parentType=parent_type,
        containerType=container_type,
        childHeadings=child_headings or [],
        siblingHeadings=sibling_headings or [],
        textExcerpt=_clean(
            section.text if text_override is None else text_override,
            1200,
        ),
        tableHeaders=[],
    )


def _content_hits(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    return [
        section_type
        for section_type, terms in _CONTENT_TERMS
        if any(term in compact for term in terms)
    ]


def _clause_hits(text: str) -> list[str]:
    compact = re.sub(r"\s+", "", str(text or ""))
    hits = [
        section_type
        for section_type, terms in _CLAUSE_TYPE_TERMS
        if any(term in compact for term in terms)
    ]
    # "支持" is useful for product clauses, but it also appears in policy
    # prose such as "中小企业扶持政策". Do not classify policy paragraphs as
    # technical unless they contain a concrete product/engineering signal.
    policy_only = (
        any(term in compact for term in ("中小企业", "扶持政策", "财政部", "残疾人福利"))
        and not any(
            term in compact
            for term in (
                "平台",
                "系统",
                "设备",
                "引擎",
                "功能",
                "参数",
                "场景",
                "渲染",
                "接口",
            )
        )
    )
    if policy_only:
        hits = [item for item in hits if item != "technical"]
    return hits


def _explicit_heading_types(section: DocumentSection) -> list[str]:
    """Return types supported by the heading itself, excluding inherited types.

    ``build_section_tree`` may copy a parent's ``sectionType`` onto a child
    whose own heading was not classified.  That value is useful as context,
    but it must not be treated as a direct heading signal.
    """

    return _unique_types(
        _content_hits(section.heading),
        _clause_hits(section.heading),
        section.section_type_hits,
    )


def _clause_context_types(
    context: SectionClassification | None,
) -> list[str]:
    """Keep only local heading context for clause-level multi-label output."""

    if context is None:
        return []
    context_only_types = {
        "basic",
        "scope",
        "submission",
        "evaluation",
        "attachment",
        "other",
    }
    return [
        item
        for item in _unique_types(
            context.primary_type,
            context.heading_type,
        )
        if item not in context_only_types
    ]


def _usage_tags(policy: RoutePolicy) -> list[str]:
    return [
        key
        for key, enabled in policy.model_dump().items()
        if enabled
    ]


def _merge_route_policies(types: list[str]) -> RoutePolicy:
    data = {key: False for key in USAGE_TAGS}
    for section_type in types:
        policy = ROUTE_POLICIES.get(section_type)
        if not policy:
            continue
        for key, enabled in policy.model_dump().items():
            data[key] = bool(data[key] or enabled)
    return RoutePolicy(**data)


def _route_types(primary_type: str, hits: list[str]) -> list[str]:
    primary_type = _canonical(primary_type)
    if primary_type in {"document_format", "rejection"}:
        return [primary_type]
    values = _unique_types([primary_type], hits)
    values = [item for item in values if item not in {"other", "attachment"}]
    return values or [primary_type or "other"]


def _apply_guardrails(
    section: DocumentSection,
    *,
    primary_type: str,
    hits: list[str],
    confidence: float,
    rationale: str,
    needs_review: bool,
) -> tuple[str, list[str], float, str, bool]:
    if _is_template_section(section):
        guarded_hits = _unique_types("document_format", hits)
        return (
            "document_format",
            guarded_hits,
            max(confidence, 0.9),
            f"{rationale}; response/template section routed as document_format",
            False,
        )
    if _is_primary_rejection_section(section):
        guarded_hits = _unique_types("rejection", hits)
        return (
            "rejection",
            guarded_hits,
            max(confidence, 0.86),
            f"{rationale}; invalid-bid/fail-review clause routed as rejection",
            False,
        )
    if _has_rejection_signal(section):
        guarded_hits = _unique_types(primary_type, hits, "rejection")
        return (
            primary_type,
            guarded_hits,
            confidence,
            f"{rationale}; contains invalid-bid/fail-review signals in secondary content",
            True,
        )
    if _is_procurement_requirement_overview(section):
        guarded_hits = _unique_types("scope", "technical", hits)
        return (
            "scope",
            guarded_hits,
            max(confidence, 0.72),
            f"{rationale}; procurement requirement overview keeps multi-type hits",
            True,
        )
    return primary_type, _unique_types(primary_type, hits), confidence, rationale, needs_review


def _should_request_llm_review(
    section: DocumentSection,
    *,
    primary_type: str,
    hits: list[str],
    confidence: float,
    needs_review: bool,
    confidence_threshold: float,
) -> bool:
    if needs_review or confidence < confidence_threshold or len(hits) > 1:
        return True
    if _is_template_section(section) or _has_rejection_signal(section):
        return True
    if _is_procurement_requirement_overview(section):
        return True
    return _canonical(primary_type) == "other"


def needs_llm_classification_review(
    section: DocumentSection | Mapping[str, Any],
    classification: SectionClassification | Mapping[str, Any] | None = None,
    *,
    confidence_threshold: float = 0.62,
) -> bool:
    """Return whether a section is worth sending to the optional LLM reviewer."""

    current = _section(section)
    if classification is None:
        return (
            _is_template_section(current)
            or _has_rejection_signal(current)
            or _is_procurement_requirement_overview(current)
        )
    item = (
        classification
        if isinstance(classification, SectionClassification)
        else SectionClassification.model_validate(classification)
    )
    return _should_request_llm_review(
        current,
        primary_type=item.primary_type,
        hits=item.type_hits,
        confidence=item.confidence,
        needs_review=item.needs_human_review,
        confidence_threshold=confidence_threshold,
    )


def classify_clause(
    text: str,
    *,
    section: DocumentSection | Mapping[str, Any],
    section_classification: SectionClassification | Mapping[str, Any] | None = None,
) -> ClauseClassification:
    """Classify one requirement-like clause using local text plus section context.

    The clause is the final routing unit for P07.  A section provides context,
    but an explicit clause signal wins over a broad parent category.
    """

    current = _section(section)
    context = (
        section_classification
        if isinstance(section_classification, SectionClassification)
        else (
            SectionClassification.model_validate(section_classification)
            if section_classification is not None
            else None
        )
    )
    text_value = _clean(text, 4000)
    direct_hits = _clause_hits(text_value)
    context_primary = _canonical(context.primary_type) if context else "other"
    context_hits = _clause_context_types(context)
    inherited_type = context_primary if context_primary != "other" else "other"
    compact_clause = _compact_text(text_value)
    embedded_hits = [
        item for item in direct_hits
        if item in _EMBEDDED_REQUIREMENT_TYPES
    ]
    has_template_clause_signal = any(
        term in compact_clause
        for term in (
            "响应表",
            "投标函",
            "偏离及说明",
            "投标人填写",
            "签字盖章",
            "签署盖章",
            "由投标人填写",
        )
    )

    if _is_template_section(current) and (
        has_template_clause_signal
        or (context_primary == "document_format" and not embedded_hits)
    ):
        primary_type = "document_format"
        hits = _unique_types("document_format", *direct_hits)
        confidence = 0.91
        source = "rule"
        rationale = "模板/响应文件上下文优先于章节中的业务主题词"
    elif "rejection" in direct_hits:
        primary_type = "rejection"
        hits = _unique_types("rejection", *direct_hits)
        confidence = 0.92
        source = "rule"
        rationale = "正文包含否决、无效或废标条件"
    elif direct_hits:
        # 模板章节里的真实技术/资格要求仍需进入对应匹配路由；只有
        # “响应表/投标函/填写/签章”这类格式动作才由上面的模板分支接管。
        if "pricing" in direct_hits and has_pricing_signal(text_value):
            primary_type = "pricing"
            rationale = "明确报价或保证金信号优先于联合体等上下文词"
        else:
            primary_type = embedded_hits[0] if embedded_hits else direct_hits[0]
            rationale = "正文要求信号覆盖局部章节上下文"
        hits = _unique_types(direct_hits, context_hits)
        confidence = 0.82 if len(direct_hits) == 1 else 0.72
        source = "rule"
    elif context_primary not in {"other", "basic", "submission"}:
        primary_type = context_primary
        hits = [context_primary]
        confidence = 0.64
        source = "inherited"
        rationale = "正文信号不足，继承最近局部章节类别"
    elif "submission" in context_hits and has_submission_signal(text_value):
        primary_type = "submission"
        hits = _unique_types("submission", context_hits)
        confidence = 0.58
        source = "inherited"
        rationale = "正文为投标提交类要求，未命中更具体业务类别"
    else:
        primary_type = context_primary if context_primary != "other" else "other"
        hits = _unique_types(primary_type, context_hits) or ["other"]
        confidence = 0.35
        source = "fallback"
        rationale = "正文和章节上下文均不足以稳定细分"

    route_decision = resolve_routing(
        primary_type,
        type_hits=hits,
        text=text_value,
        confidence=confidence,
        needs_human_review=confidence < 0.62 or len(hits) > 1,
        classification_source=source,
    )
    policy = RoutePolicy(**route_decision["legacyRoutePolicy"])
    clause_needs_review = confidence < 0.62 or len(hits) > 1
    return ClauseClassification(
        primaryType=primary_type,
        directTypeHits=_unique_types(direct_hits),
        inheritedType=inherited_type,
        typeHits=hits,
        contentType=route_decision["contentType"],
        confidence=confidence,
        classificationSource=source,
        usageTags=_usage_tags(policy),
        route=policy,
        needsHumanReview=clause_needs_review,
        primaryRoute=route_decision["primaryRoute"],
        secondaryRoutes=route_decision["secondaryRoutes"],
        matchDomain=route_decision["matchDomain"],
        decisionImpact=route_decision["decisionImpact"],
        granularity=route_decision["granularity"],
        reviewRequired=route_decision["reviewRequired"],
        reviewMode=route_decision["reviewMode"],
        reviewReason=route_decision["reviewReason"],
        rationale=rationale,
    )


def classify_free_clause(text: str) -> ClauseClassification:
    """Classify a standalone legacy clause through the shared classifier.

    The historical fine-review path often has only a sentence and no typed
    SectionTree node.  Give it a neutral synthetic section so it still uses
    the same clause signals and routing guards as the DocumentIR path.
    """

    section = DocumentSection(
        id="SYNTHETIC-CLAUSE",
        sectionId="SYNTHETIC-CLAUSE",
        sectionType="other",
        heading="",
        sectionPath=[],
    )
    return classify_clause(text, section=section)


def classify_section(
    section: DocumentSection | Mapping[str, Any],
    *,
    parent_type: str = "",
    llm_classifier: LLMSectionClassifier | None = None,
    confidence_threshold: float = 0.62,
    text_override: str | None = None,
    container_type: str = "",
    child_headings: list[str] | None = None,
    sibling_headings: list[str] | None = None,
) -> SectionClassification:
    current = _section(section)
    section_id = current.section_id or current.id
    original_type = current.section_type or "other"
    title_hits = _explicit_heading_types(current)
    content_hits = _content_hits(
        " ".join([
            current.heading,
            (current.text if text_override is None else text_override)[:1600],
        ])
    )
    direct_type_hits = list(dict.fromkeys([*title_hits, *content_hits]))
    inherited_type = "other"
    hits = list(direct_type_hits)

    if title_hits:
        primary_type = title_hits[0]
        confidence = 0.92 if len(title_hits) == 1 else 0.72
        source = "rule"
        rationale = "标题或章节结构信号命中"
    elif content_hits:
        primary_type = content_hits[0]
        confidence = 0.68
        source = "content_rule"
        rationale = "章节正文摘要信号命中"
    elif _canonical(original_type) not in {"", "other"}:
        # DocumentStructure 已经根据标题给出过一次章节类型时，把它作为
        # 低置信度上下文兜底。它不能冒充标题命中，仍需保留复核/LLM 机会。
        primary_type = _canonical(original_type)
        hits = [primary_type]
        inherited_type = primary_type
        confidence = 0.55
        source = "content_rule"
        rationale = "解析阶段已有章节类型作为低置信度兜底"
    elif parent_type and _canonical(parent_type) != "other":
        primary_type = _canonical(parent_type)
        hits = [primary_type]
        inherited_type = primary_type
        confidence = 0.58
        source = "content_rule"
        rationale = "继承父章节主类，标题和正文信号不足"
    else:
        primary_type = "other"
        confidence = 0.25
        source = "fallback"
        rationale = "规则无法稳定归类"

    needs_review = confidence < confidence_threshold or len(hits) > 1
    primary_type, hits, confidence, rationale, needs_review = _apply_guardrails(
        current,
        primary_type=primary_type,
        hits=hits,
        confidence=confidence,
        rationale=rationale,
        needs_review=needs_review,
    )
    llm_review_needed = _should_request_llm_review(
        current,
        primary_type=primary_type,
        hits=hits,
        confidence=confidence,
        needs_review=needs_review,
        confidence_threshold=confidence_threshold,
    )
    rule_snapshot = (primary_type, list(hits), confidence, source, rationale, needs_review)
    if llm_review_needed and llm_classifier is not None:
        request = _input_for_section(
            current,
            text_override=text_override,
            parent_type=_canonical(parent_type),
            container_type=_canonical(container_type),
            child_headings=child_headings,
            sibling_headings=sibling_headings,
        )
        try:
            response = llm_classifier(request)
            candidate = (
                response
                if isinstance(response, LLMSectionClassification)
                else LLMSectionClassification.model_validate(response)
            )
            primary_type = candidate.primary_type
            hits = list(dict.fromkeys([candidate.primary_type, *candidate.type_hits]))
            confidence = candidate.confidence
            source = "llm"
            rationale = candidate.rationale or "LLM 低置信度兜底分类"
            needs_review = candidate.needs_human_review
            primary_type, hits, confidence, rationale, needs_review = _apply_guardrails(
                current,
                primary_type=primary_type,
                hits=hits,
                confidence=confidence,
                rationale=rationale,
                needs_review=needs_review,
            )
        except Exception as exc:
            (
                primary_type,
                hits,
                confidence,
                source,
                rationale,
                needs_review,
            ) = rule_snapshot
            if primary_type == "other" and confidence < confidence_threshold:
                hits = ["other"]
                confidence = 0.0
                source = "fallback"
                rationale = f"LLM 分类失败，已回退 other: {exc.__class__.__name__}"
            else:
                rationale = f"{rationale}; LLM 分类失败，保留规则/护栏结果: {exc.__class__.__name__}"
            needs_review = rule_snapshot[-1]

    route_decision = resolve_routing(
        primary_type,
        type_hits=hits,
        text=f"{current.heading}\n{current.text}",
        confidence=confidence,
        needs_human_review=needs_review,
        section_level=True,
        container_type=_canonical(container_type),
        classification_source=source,
    )
    policy = RoutePolicy(**route_decision["legacyRoutePolicy"])
    legacy_needs_review = needs_review
    return SectionClassification(
        sectionId=section_id,
        originalSectionType=original_type,
        headingType=(
            title_hits[0] if title_hits else "other"
        ),
        directTypeHits=direct_type_hits,
        inheritedType=inherited_type,
        containerType=_canonical(container_type) or "other",
        primaryType=primary_type,
        typeHits=hits or ["other"],
        contentType=route_decision["contentType"],
        confidence=confidence,
        classificationSource=source,
        usageTags=_usage_tags(policy),
        route=policy,
        needsHumanReview=legacy_needs_review,
        primaryRoute=route_decision["primaryRoute"],
        secondaryRoutes=route_decision["secondaryRoutes"],
        matchDomain=route_decision["matchDomain"],
        decisionImpact=route_decision["decisionImpact"],
        granularity=route_decision["granularity"],
        reviewRequired=route_decision["reviewRequired"],
        reviewMode=route_decision["reviewMode"],
        reviewReason=route_decision["reviewReason"],
        rationale=rationale,
    )


def classify_sections(
    sections: list[DocumentSection | Mapping[str, Any]],
    *,
    segments: list[Any] | None = None,
    llm_classifier: LLMSectionClassifier | None = None,
    confidence_threshold: float = 0.62,
) -> list[SectionClassification]:
    """Classify sections in document order without mutating the SectionTree."""

    normalized_sections = [_section(raw) for raw in sections]
    section_by_id = {
        item.section_id or item.id: item
        for item in normalized_sections
        if item.section_id or item.id
    }
    children_by_parent: dict[str, list[DocumentSection]] = {}
    for item in normalized_sections:
        if item.parent_id:
            children_by_parent.setdefault(item.parent_id, []).append(item)

    descendants_by_id: dict[str, list[DocumentSection]] = {}

    def descendants(section_id: str) -> list[DocumentSection]:
        if section_id in descendants_by_id:
            return descendants_by_id[section_id]
        result: list[DocumentSection] = []
        for child in children_by_parent.get(section_id, []):
            child_id = child.section_id or child.id
            result.append(child)
            result.extend(descendants(child_id))
        descendants_by_id[section_id] = result
        return result

    segment_map = {
        str(getattr(item, "id", "") or item.get("id", "")): item
        for item in (segments or [])
        if str(getattr(item, "id", "") or item.get("id", ""))
    }

    def direct_text(section: DocumentSection) -> str:
        section_id = section.section_id or section.id
        descendant_segment_ids = {
            segment_id
            for child in descendants(section_id)
            for segment_id in child.segment_ids
            if segment_id
        }
        if not segment_map or not section.segment_ids:
            return section.text
        values: list[str] = []
        for segment_id in section.segment_ids:
            if segment_id in descendant_segment_ids:
                continue
            segment = segment_map.get(segment_id)
            if segment is None:
                continue
            value = getattr(segment, "text", None)
            if value is None and isinstance(segment, Mapping):
                value = segment.get("text")
            if str(value or "").strip():
                values.append(str(value))
        return "\n".join(values) or section.text

    results: list[SectionClassification] = []
    by_id: dict[str, SectionClassification] = {}
    for section in normalized_sections:
        section_id = section.section_id or section.id
        children = children_by_parent.get(section_id, [])
        siblings = [
            item
            for item in children_by_parent.get(section.parent_id, [])
            if (item.section_id or item.id) != section_id
        ]
        parent_result = by_id.get(section.parent_id)
        parent_type = parent_result.primary_type if parent_result else ""
        container_type = (
            parent_result.container_type
            if parent_result and parent_result.container_type != "other"
            else (parent_result.primary_type if parent_result else "")
        )
        result = classify_section(
            section,
            parent_type=parent_type,
            container_type=container_type,
            text_override=direct_text(section),
            child_headings=[item.heading for item in children if item.heading],
            sibling_headings=[item.heading for item in siblings if item.heading],
            llm_classifier=llm_classifier,
            confidence_threshold=confidence_threshold,
        )
        results.append(result)
        by_id[section_id] = result

    finalized: list[SectionClassification] = []
    for result in results:
        section = section_by_id[result.section_id]
        section_id = result.section_id
        descendant_results = [
            by_id[item.section_id or item.id]
            for item in descendants(section_id)
            if (item.section_id or item.id) in by_id
        ]
        descendant_hits = _unique_types(
            [
                item.primary_type
                for item in descendant_results
            ],
            *[
                item.type_hits
                for item in descendant_results
            ],
        )
        if section.is_major or section.level <= 1:
            container_type = result.primary_type
        else:
            parent_result = by_id.get(section.parent_id)
            container_type = (
                parent_result.container_type
                if parent_result and parent_result.container_type != "other"
                else (parent_result.primary_type if parent_result else "other")
            )
        finalized.append(
            result.model_copy(
                update={
                    "container_type": _canonical(container_type),
                    "descendant_type_hits": descendant_hits,
                }
            )
        )
    return finalized


def classify_document(
    document: DocumentIR | Mapping[str, Any],
    *,
    llm_classifier: LLMSectionClassifier | None = None,
    confidence_threshold: float = 0.62,
) -> list[SectionClassification]:
    parsed = document if isinstance(document, DocumentIR) else DocumentIR.model_validate(document)
    return classify_sections(
        parsed.sections,
        segments=parsed.segments,
        llm_classifier=llm_classifier,
        confidence_threshold=confidence_threshold,
    )
