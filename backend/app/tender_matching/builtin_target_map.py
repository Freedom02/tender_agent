"""Target-oriented discovery for the builtin tender extraction route.

The builtin route used to classify every sentence into a large category
ontology before deciding what the sentence was for.  That made mixed chapters
fragile: one broad chapter could contain technical, commercial and template
content, while the classifier had to pick exactly one category.

This module reverses that dependency.  It first recalls likely source ranges
for a small, stable set of user-facing targets, then lets the configured LLM
choose valid section/segment/table ids from that candidate set.  The returned
ids are always validated against DocumentIR, so the processor can safely
project the result back to the original document.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from typing import Any, Mapping

from .eligibility import call_openai_compatible
from .requirement_scope import is_form_title, is_placeholder_row
from .target_stage_contract import (
    ENTERPRISE_MATCH_TARGETS,
    MATCH_STAGE_TARGETS,
    RISK_CHECK_TARGETS,
)


TARGET_MAP_SCHEMA_VERSION = "tender-target-map/1.1"

TARGET_MODULES: tuple[dict[str, Any], ...] = (
    {
        "key": "technical",
        "label": "技术部分",
        "description": "技术要求、服务要求、采购内容；提取产品、服务、数量和技术参数，后续与企业产品/能力匹配。",
        "processingMode": "match",
        "matchable": True,
    },
    {
        "key": "qualification",
        "label": "资格审查部分",
        "description": "投标人资格、资质、业绩、人员、财务、信用及联合体条件，后续与企业资料匹配。",
        "processingMode": "match",
        "matchable": True,
    },
    {
        "key": "commercial",
        "label": "商务要求部分",
        "description": "报价、付款、交付、质保、服务期限、履约和递交等商务条件，直接展示。",
        "processingMode": "display",
        "matchable": False,
    },
    {
        "key": "evaluation",
        "label": "评分标准部分",
        "description": "评审因素、评分办法、分值、评分规则和所需证明材料，直接展示。",
        "processingMode": "display",
        "matchable": False,
    },
    {
        "key": "contract",
        "label": "中标后合同部分",
        "description": "中标后合同正文、合同条款和履约合同附件，当前只定位并忽略处理。",
        "processingMode": "ignore",
        "matchable": False,
    },
    {
        "key": "template",
        "label": "模板部分",
        "description": "投标文件格式、响应表、偏离表、承诺函和可复用模板，提取原文供下载和写标。",
        "processingMode": "download",
        "matchable": False,
    },
    {
        "key": "project_info",
        "label": "项目关键基础信息",
        "description": "从全文提炼项目身份、采购范围、报价费用、保证金、时间、交付、递交和投标方式等基础信息。",
        "processingMode": "display",
        "matchable": False,
    },
    {
        "key": "rejection",
        "label": "废标项",
        "description": "全文扫描废标、无效、否决、不接受、拒绝、不予受理等风险原文，直接展示，不参与匹配。",
        "processingMode": "regex",
        "matchable": False,
    },
)

TARGET_KEYS = tuple(item["key"] for item in TARGET_MODULES)
TARGET_META = {item["key"]: item for item in TARGET_MODULES}

_TARGET_RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "technical": {
        "heading": (
            "技术要求",
            "技术参数",
            "技术指标",
            "技术和服务要求",
            "服务要求",
            "采购内容",
            "采购需求",
            "货物需求",
            "服务内容",
            "功能要求",
            "性能要求",
            "规格参数",
            "产品参数",
            "配置要求",
            "建设内容",
            "实施内容",
        ),
        "body": (
            "技术参数",
            "技术指标",
            "功能要求",
            "性能指标",
            "服务要求",
            "采购内容",
            "采购范围",
            "产品名称",
            "货物名称",
            "服务内容",
            "规格",
            "型号",
            "数量",
            "单位",
            "支持",
            "兼容",
            "接口",
            "精度",
            "并发",
            "吞吐",
            "响应时间",
            "交付成果",
        ),
        "exclude": ("评分标准", "评标办法", "合同条款", "投标文件格式"),
    },
    "qualification": {
        "heading": (
            "资格审查",
            "资格条件",
            "资格要求",
            "资格及资信",
            "投标人资格",
            "供应商资格",
            "资质要求",
            "业绩要求",
            "人员要求",
        ),
        "body": (
            "营业执照",
            "统一社会信用代码",
            "资质",
            "资格",
            "认证",
            "许可证",
            "证书",
            "类似业绩",
            "项目业绩",
            "人员",
            "项目负责人",
            "财务报告",
            "审计报告",
            "信用中国",
            "失信被执行人",
            "联合体",
            "纳税",
            "社会保障",
            "社保",
            "注册资本",
            "成立年限",
        ),
        "exclude": ("评分标准", "评标办法", "合同条款", "投标文件格式"),
    },
    "commercial": {
        "heading": (
            "商务要求",
            "商务条件",
            "商务条款",
            "投标须知",
            "报价要求",
            "价格",
            "费用",
            "交付",
            "履约",
            "付款",
            "质保",
            "售后",
            "服务期限",
            "工期",
            "递交要求",
        ),
        "body": (
            "报价",
            "最高限价",
            "预算金额",
            "投标保证金",
            "履约保证金",
            "费用",
            "付款方式",
            "付款条件",
            "交付期",
            "交货期",
            "工期",
            "质保期",
            "服务期",
            "投标有效期",
            "投标截止时间",
            "递交截止时间",
            "响应文件递交截止时间",
            "开标时间",
            "文件获取时间",
            "报名时间",
            "递交",
            "签字",
            "盖章",
            "电子投标",
            "开标",
            "联合体",
        ),
        "exclude": ("评分标准", "评标办法", "合同正文"),
    },
    "evaluation": {
        "heading": (
            "评分标准",
            "评分办法",
            "评分细则",
            "评标细则",
            "评标办法",
            "评标方法",
            "评审标准",
            "评审因素",
            "综合评分",
            "技术评分",
            "商务评分",
        ),
        "body": (
            "评分",
            "分值",
            "得分",
            "评审因素",
            "评审标准",
            "评分标准",
            "技术响应",
            "商务响应",
            "证明材料",
            "优",
            "良",
            "一般",
        ),
        "exclude": (),
    },
    "contract": {
        "heading": (
            "合同条款",
            "合同文本",
            "合同格式",
            "合同主要条款",
            "合同附件",
            "履约合同",
        ),
        "body": (
            "甲方",
            "乙方",
            "合同价款",
            "合同金额",
            "合同签订",
            "违约责任",
            "合同解除",
            "合同变更",
            "验收",
            "结算",
        ),
        "exclude": (),
    },
    "template": {
        "heading": (
            "投标文件格式",
            "响应文件格式",
            "投标文件组成",
            "响应表",
            "偏离表",
            "投标函",
            "承诺函",
            "资格证明文件",
            "格式文件",
            "附件格式",
        ),
        "body": (
            "投标函",
            "响应表",
            "技术偏离表",
            "商务偏离表",
            "分项报价表",
            "报价表",
            "承诺函",
            "法定代表人",
            "授权委托书",
            "声明函",
            "投标文件应包括",
        ),
        "exclude": (),
    },
}


# These terms are intentionally stricter than ``_TARGET_RULES``.  The rule
# map is used to build an LLM candidate set, while these signals decide
# whether a mixed section may be expanded or a standalone segment is useful.
# A generic word such as "评审", "格式", or "模板" is not enough to identify
# the corresponding user-facing target.
_STRONG_HEADING_TERMS: dict[str, tuple[str, ...]] = {
    "technical": (
        "技术要求",
        "技术需求",
        "技术参数",
        "技术指标",
        "技术和服务要求",
        "技术、服务及其他要求",
        "项目技术需求",
        "采购标的技术要求",
        "功能要求",
        "性能要求",
        "规格参数",
        "产品参数",
        "服务要求",
        "服务内容及商务要求",
        "招标内容及要求",
    ),
    "qualification": (
        "资格审查",
        "资格条件",
        "资格要求",
        "资格及资信",
        "投标人资格",
        "供应商资格",
        "资质要求",
        "业绩要求",
        "人员要求",
        "财务要求",
    ),
    "commercial": (
        "商务要求",
        "商务条件",
        "商务条款",
        "报价要求",
        "投标保证金",
        "履约保证金",
        "付款方式",
        "交付要求",
        "交货期",
        "服务期限",
        "质保及售后",
        "售后服务",
        "递交要求",
    ),
    "evaluation": (
        "评分标准",
        "评分办法",
        "评分细则",
        "评标细则",
        "评分因素",
        "评审因素",
        "评审标准",
        "评标办法",
        "评标方法和标准",
        "评标方法",
        "综合评分",
        "技术评分",
        "商务评分",
        "价格评分",
    ),
    "contract": (
        "政府采购合同",
        "合同文本",
        "合同格式",
        "合同主要条款",
        "合同条款",
        "合同协议书",
        "政府采购货物买卖合同",
        "政府采购合同参考文本",
        "拟签订合同文本",
        "拟签订政府采购合同",
        "合同范本",
        "履约合同",
        "合同附件",
    ),
    "template": (
        "投标文件格式",
        "响应文件格式",
        "投标文件组成",
        "响应表",
        "偏离表",
        "投标函",
        "承诺函",
        "声明函",
        "授权委托书",
        "资格证明文件",
        "报价表",
        "分项报价表",
        "技术和服务要求响应表",
        "商务条件响应表",
    ),
}

_STRONG_BODY_TERMS: dict[str, tuple[str, ...]] = {
    "technical": (
        "技术参数",
        "技术指标",
        "功能要求",
        "性能指标",
        "规格型号",
        "产品名称",
        "货物名称",
        "服务要求",
        "支持",
        "兼容",
        "接口",
        "精度",
        "并发",
        "吞吐",
        "响应时间",
        "交付成果",
        "配置要求",
    ),
    "qualification": (
        "营业执照",
        "统一社会信用代码",
        "资质证书",
        "资质要求",
        "资格要求",
        "资格条件",
        "许可证",
        "认证",
        "类似业绩",
        "项目业绩",
        "业绩证明",
        "项目负责人",
        "人员要求",
        "财务报告",
        "审计报告",
        "信用中国",
        "失信被执行人",
        "纳税证明",
        "社会保障",
        "社会保障资金",
        "联合体投标",
        "是否接受联合体",
    ),
    "commercial": (
        "报价",
        "最高限价",
        "预算金额",
        "投标保证金",
        "履约保证金",
        "付款方式",
        "付款条件",
        "交付期",
        "交货期",
        "交货地点",
        "工期",
        "质保期",
        "服务期限",
        "售后服务",
        "投标有效期",
        "投标截止时间",
        "递交截止时间",
        "响应文件递交截止时间",
        "开标时间",
        "获取文件时间",
        "报名时间",
        "递交方式",
        "电子投标",
        "电子响应",
        "是否接受联合体",
        "联合体投标",
    ),
    "evaluation": (
        "分值",
        "评审因素",
        "评审标准",
        "评分标准",
        "技术评分",
        "商务评分",
        "价格评分",
        "评标基准价",
        "评分细则",
        "评分法",
        "评标方法和标准",
        "分项得分",
        "权重",
        "满分",
    ),
    "template": (
        "投标函",
        "响应表",
        "技术偏离表",
        "商务偏离表",
        "分项报价表",
        "报价明细表",
        "开标（报价）一览表",
        "开标(报价)一览表",
        "承诺函",
        "授权委托书",
        "声明函",
        "法定代表人身份证明",
        "投标文件应包括",
        "由投标人填写",
        "投标人填写",
        "签字盖章",
        "签署盖章",
        "请按本章格式填写",
    ),
}

_BROAD_CONTAINER_HEADING_TERMS = (
    "招标内容及要求",
    "技术、服务及其他要求",
    "服务内容及商务要求",
    "采购内容及要求",
    "项目内容及要求",
    "资格审查与评标",
    "资格审查、评标",
    "开标、资格审查、评标",
    "资格审查及评标",
    "评标办法与资格审查",
)

_CONTRACT_REFERENCE_HEADING_TERMS = (
    "合同签订",
    "合同备案",
    "合同公告",
    "公告、备案",
    "中标与政府采购合同",
    "合同履行及验收",
    "合同分包和转包",
    "履约验收",
)

_EVALUATION_PROCESS_HEADING_TERMS = (
    "评标委员会",
    "评审程序",
    "评标程序",
    "评审纪律",
    "评标纪律",
    "停止评标",
    "评标复核",
    "评标委员会义务",
    "评标委员会成员工作纪律",
)

_EVALUATION_PROCESS_TEXT_TERMS = (
    "评标委员会各成员",
    "评标委员会成员对",
    "评标委员会应当启动",
    "评标委员会启动",
    "评标委员会依据",
    "评标委员会认为",
    "评标委员会认定",
    "评标委员会基于",
    "评标过程中发现",
    "评审现场",
    "异常低价",
    "报价合理性",
    "书面说明",
    "复核过程中",
    "不得离开评审现场",
    "作为无效投标处理",
    "评分不一致",
    "独立评标",
    "正式评审前",
    "熟悉和理解招标文件",
    "评审结果",
    "重新评审",
    "评标复核",
    "确定中标候选人名单",
    "推荐中标候选人",
)

_HARD_CONTEXT_CONFLICTS = {
    "template",
    "template-type",
    "template-heading",
    "contract",
    "contract-type",
    "contract-heading",
}

_DIRECTORY_LINE_RE = re.compile(
    r"^(?:第?[一二三四五六七八九十百]+[章节篇、.]|\d+(?:\.\d+)*[、.]|"
    r"[（(][一二三四五六七八九十百\d]+[)）])?.{1,80}"
    r"(?:[-－]?\s*\d+\s*[-－]?|\s+\d+)$"
)

# 无页码的目录/提纲短行，如「7）合理化建议」——数字枚举 + 短短语，没有标点和要求信号。
_BARE_ENUMERATION_RE = re.compile(r"^[（(]?\d+[)）、.．]\s*.{0,20}$")
_REQUIREMENT_SIGNAL_TERMS: tuple[str, ...] = (
    "必须", "须", "应当", "不得", "不低于", "不少于", "至少",
    "提供", "具备", "要求", "提交", "满足", "完成", "承担",
)


_PROJECT_FIELD_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("projectName", "项目名称", ("项目名称", "采购项目名称", "招标项目名称")),
    ("projectCode", "项目编号", ("项目编号", "采购项目编号", "招标编号", "项目代码")),
    ("tendererName", "采购人", ("采购人", "招标人", "采购单位")),
    (
        "agencyName",
        "代理机构",
        ("代理机构", "采购代理机构", "招标代理机构", "招标代理"),
    ),
    ("procurementMethod", "采购方式", ("采购方式", "招标方式", "采购组织形式")),
    (
        "procurementScope",
        "采购范围",
        (
            "采购范围",
            "采购标的概览",
            "采购标的",
            "项目概况",
            "项目基本情况",
            "建设内容",
            "服务内容及范围",
        ),
    ),
    (
        "budgetAmount",
        "预算金额",
        ("预算金额", "项目预算", "预算价", "采购预算", "采购预算及最高限价"),
    ),
    ("maxPrice", "最高限价", ("最高限价", "最高控制价", "控制价")),
    ("bidBond", "投标保证金", ("投标保证金", "投标保证金金额", "保证金")),
    ("performanceBond", "履约保证金", ("履约保证金", "履约担保")),
    (
        "fee",
        "费用",
        (
            "招标文件费",
            "采购文件费",
            "代理服务费",
            "采购代理服务费",
            "交易服务费",
            "服务费",
        ),
    ),
    ("registrationPeriod", "文件获取/报名时间", ("获取文件时间", "招标文件获取期限", "报名时间", "获取期限")),
    ("bidDeadline", "投标截止时间", ("投标截止时间", "响应文件递交截止时间", "递交截止时间")),
    ("openingTime", "开标时间", ("开标时间", "开启时间")),
    ("deliveryPeriod", "交付/工期", ("交付期", "交货期", "交付时间", "工期", "服务期限", "履约期限")),
    ("deliveryLocation", "交付地点", ("交货地点", "交付地点", "项目地点", "服务地点")),
    ("bidValidity", "投标有效期", ("投标有效期", "响应有效期")),
    ("submissionMethod", "递交方式", ("递交方式", "投标文件递交", "电子投标", "电子响应")),
    ("jointVenture", "联合体要求", ("联合体投标", "是否接受联合体", "联合体要求")),
    ("subcontracting", "分包要求", ("分包要求", "分包", "转包")),
    ("warranty", "质保/售后", ("质保期", "质量保证期", "保修期", "售后服务")),
    ("contact", "联系方式", ("联系人", "联系电话", "联系方式")),
)


_REJECTION_TERMS: tuple[str, ...] = (
    "废标",
    "无效投标",
    "投标无效",
    "响应无效",
    "无效响应",
    "否决",
    "不接受",
    "不予受理",
    "拒绝其投标",
    "拒绝响应",
    "不得参加",
    "取消投标资格",
    "取消中标资格",
    "不合格",
    "作无效处理",
    "视为无效",
)
_REJECTION_CANDIDATE_TERMS: tuple[str, ...] = tuple(
    dict.fromkeys(
        [
            *_REJECTION_TERMS,
            "不符合",
            "未按",
            "未提供",
            "未提交",
            "否则",
            "禁止",
            "拒收",
            "取消",
        ]
    )
)

_NON_MATCHING_TARGETS = {"technical", "qualification", "commercial", "evaluation"}
_INVITATION_NOISE_TERMS = (
    "投标邀请书",
    "投标邀请",
    "采购邀请",
    "响应邀请",
    "邀请函",
    "招标公告",
    "采购公告",
    "中标结果公告",
    "成交公告",
    "采购结果公告",
    "更正公告",
    "中标通知书",
)
_TEMPLATE_PROSE_TERMS = (
    "投标文件格式",
    "响应表",
    "偏离表",
    "投标函",
    "承诺函",
    "请按本章格式填写",
    "不作为企业能力判断",
    "逐项填写",
    "填写响应",
    "签字盖章",
    "签署盖章",
    "由投标人填写",
)
_BASIC_FIELD_LABELS = (
    "项目名称",
    "项目编号",
    "采购项目编号",
    "招标编号",
    "采购人",
    "招标人",
    "代理机构",
    "联系方式",
    "联系电话",
    "联系人",
    "采购方式",
    "项目概况",
    "项目基本情况",
    "采购项目概述",
    "采购范围",
)


def _text(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _id(value: Any) -> str:
    return _text(value, 120)


def _module(key: str) -> dict[str, Any]:
    return dict(TARGET_META[key])


def _section_id(section: Mapping[str, Any]) -> str:
    return _id(section.get("sectionId") or section.get("id"))


def _segment_id(segment: Mapping[str, Any]) -> str:
    return _id(segment.get("id") or segment.get("segmentId"))


def _table_id(table: Mapping[str, Any]) -> str:
    return _id(table.get("tableId") or table.get("id"))


def _section_blob(section: Mapping[str, Any]) -> str:
    return _compact(
        " ".join(
            [
                str(section.get("heading") or ""),
                " / ".join(str(value) for value in section.get("sectionPath") or []),
                str(section.get("title") or ""),
            ]
        )
    )


def _section_heading_blob(section: Mapping[str, Any]) -> str:
    """Return only the section's own heading.

    ``sectionPath`` is useful for display, but it is context rather than a
    local signal.  Using it for scoring makes every child of a technical or
    evaluation chapter look like a direct member of that target.
    """

    return _compact(
        " ".join(
            [
                str(section.get("heading") or ""),
                str(section.get("title") or ""),
            ]
        )
    )


_TARGET_SECTION_TYPE_ALIASES = {
    # Acceptance is a fulfilment/commercial concern here. It must not turn a
    # generic acceptance block into technical matching input.
    "technical": {"technical", "service_plan", "scope"},
    "qualification": {"qualification", "performance", "personnel", "financial"},
    "commercial": {"commercial", "pricing", "submission", "schedule"},
    "evaluation": {"evaluation"},
    "contract": {"contract"},
    "template": {"document_format", "attachment", "template"},
}


def _raw_section_type_values(
    section: Mapping[str, Any],
    *,
    direct_only: bool = False,
) -> list[str]:
    if direct_only and isinstance(section.get("sectionTypeHits"), list):
        values = section.get("sectionTypeHits") or []
    else:
        values = [
            section.get("sectionType"),
            *(section.get("sectionTypes") or []),
        ]
    return [
        _compact(value)
        for value in values
        if _compact(value)
    ]


def _section_type_hits(
    target_key: str,
    section: Mapping[str, Any],
    *,
    direct_only: bool = False,
) -> list[str]:
    aliases = _TARGET_SECTION_TYPE_ALIASES
    return [
        value
        for value in _raw_section_type_values(section, direct_only=direct_only)
        if value in aliases.get(target_key, set())
    ]


def _direct_heading_hits(
    target_key: str,
    section: Mapping[str, Any],
) -> list[str]:
    return _term_hits(
        _section_heading_blob(section),
        _STRONG_HEADING_TERMS.get(target_key, ()),
    )


def _strong_body_hits(
    target_key: str,
    blob: str,
) -> list[str]:
    hits = _term_hits(blob, _STRONG_BODY_TERMS.get(target_key, ()))
    if target_key == "evaluation":
        if re.search(r"(?:评审|评分).{0,8}(?:因素|标准|分值|得分|权重)", blob):
            hits.append("评分规则组合信号")
    return list(dict.fromkeys(hits))


def _evaluation_process_text_hits(blob: str) -> list[str]:
    compact = _compact(blob)
    return [
        term
        for term in _EVALUATION_PROCESS_TEXT_TERMS
        if _compact(term) in compact
    ]


def _direct_target_types(section: Mapping[str, Any]) -> set[str]:
    result: set[str] = set()
    for target_key in _TARGET_RULES:
        if _section_type_hits(target_key, section, direct_only=True):
            result.add(target_key)
        if _direct_heading_hits(target_key, section):
            result.add(target_key)
    return result


def _section_context_target_types(
    section: Mapping[str, Any] | None,
    indexes: Mapping[str, Any],
) -> set[str]:
    result: set[str] = set()
    current_id = _section_id(section or {})
    section_map = indexes.get("sectionMap", {})
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        current = section_map.get(current_id) or {}
        if not _section_is_locally_mixed(current):
            result.update(_direct_target_types(current))
        current_id = _id(current.get("parentId"))
    return result


def _section_context_conflict(
    target_key: str,
    section: Mapping[str, Any] | None,
    indexes: Mapping[str, Any],
) -> bool:
    context_types = _section_context_target_types(section, indexes)
    if target_key == "template" and "template" in context_types:
        context_types = {
            value
            for value in context_types
            if value in {"template", "contract"}
        }
    if target_key == "contract" and "contract" in context_types:
        context_types = {
            value
            for value in context_types
            if value in {"contract", "template"}
        }
        # Some parsers classify a standalone contract chapter as
        # ``document_format`` because it is introduced from an attachment or
        # form-like page.  A strong local contract heading is authoritative in
        # that case; the inherited template type must not suppress it.
        if (
            _direct_heading_hits("contract", section or {})
            and not _is_contract_reference_heading(
                _section_heading_blob(section or {})
            )
        ):
            context_types.discard("template")
    return any(value != target_key for value in context_types)


def _hard_context_conflict(
    target_key: str,
    section: Mapping[str, Any] | None,
    indexes: Mapping[str, Any],
) -> bool:
    direct_conflicts = _section_conflict_hits(target_key, section or {})
    if any(hit in _HARD_CONTEXT_CONFLICTS for hit in direct_conflicts):
        return True
    context_types = _section_context_target_types(section, indexes)
    authoritative_contract_heading = (
        target_key == "contract"
        and _direct_heading_hits("contract", section or {})
        and not _is_contract_reference_heading(
            _section_heading_blob(section or {})
        )
    )
    if (
        target_key != "template"
        and "template" in context_types
        and not authoritative_contract_heading
    ):
        return True
    if target_key != "contract" and "contract" in context_types:
        return True
    if target_key != "evaluation" and "evaluation" in context_types:
        return True
    return False


def _section_conflict_hits(
    target_key: str,
    section: Mapping[str, Any],
) -> list[str]:
    """Find direct signals that make whole-section expansion unsafe."""

    direct_types = _direct_target_types(section)
    if target_key == "template" and "template" in direct_types:
        direct_types = {
            value
            for value in direct_types
            if value in {"template", "contract"}
        }
    if target_key == "contract" and "contract" in direct_types:
        direct_types = {"contract"}
    conflicts = sorted(
        value
        for value in direct_types
        if value != target_key
    )
    heading = _section_heading_blob(section)
    if target_key != "template" and _direct_heading_hits("template", section):
        conflicts.append("template-heading")
    if target_key != "contract" and _direct_heading_hits("contract", section):
        conflicts.append("contract-heading")
    if target_key != "contract" and "contract" in direct_types:
        conflicts.append("contract-type")
    if target_key != "template" and "template" in direct_types:
        conflicts.append("template-type")
    if target_key != "evaluation" and _direct_heading_hits("evaluation", section):
        conflicts.append("evaluation-heading")
    if target_key != "evaluation" and "evaluation" in direct_types:
        conflicts.append("evaluation-type")
    if target_key != "technical" and _direct_heading_hits("technical", section):
        # Technical response tables are templates as well, but their own
        # technical heading must not make a commercial/evaluation section
        # expand wholesale.
        conflicts.append("technical-heading")
    if target_key != "qualification" and "qualification" in direct_types:
        conflicts.append("qualification-type")
    if target_key != "commercial" and "commercial" in direct_types:
        conflicts.append("commercial-type")
    if target_key != "contract" and any(
        term in heading
        for term in (
            "政府采购合同",
            "合同文本",
            "合同条款",
            "合同协议书",
        )
    ):
        conflicts.append("contract-heading")
    return list(dict.fromkeys(conflicts))


def _is_descendant_section(
    section: Mapping[str, Any],
    ancestor_id: str,
    indexes: Mapping[str, Any],
) -> bool:
    current_id = _section_id(section)
    visited: set[str] = set()
    section_map = indexes.get("sectionMap", {})
    while current_id and current_id not in visited:
        visited.add(current_id)
        current = section_map.get(current_id) or {}
        parent_id = _id(current.get("parentId"))
        if parent_id == ancestor_id:
            return True
        current_id = parent_id
    return False


def _section_has_descendant_conflict(
    target_key: str,
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> bool:
    section_id = _section_id(section)
    if not section_id:
        return False
    for descendant in indexes.get("sectionMap", {}).values():
        if not _is_descendant_section(descendant, section_id, indexes):
            continue
        descendant_types = _direct_target_types(descendant)
        if target_key == "template" and "template" in descendant_types:
            # A response table can also carry technical/commercial words.
            descendant_types = {"template"}
        if target_key == "contract" and "contract" in descendant_types:
            descendant_types = {"contract"}
        if any(value != target_key for value in descendant_types):
            return True
    return False


def _section_is_broad_container(section: Mapping[str, Any]) -> bool:
    heading = _section_heading_blob(section)
    return any(_compact(term) in heading for term in _BROAD_CONTAINER_HEADING_TERMS)


def _section_is_locally_mixed(section: Mapping[str, Any]) -> bool:
    """Whether this section itself mixes business targets.

    An explicitly typed child under a mixed parent is still a usable pure
    range.  The old ancestor-aware check treated every such child as mixed,
    which made a normal ``技术要求`` child lose its whole-section semantics.
    """

    if section.get("isMixed") or _section_is_broad_container(section):
        return True
    return len(_direct_target_types(section)) > 1


def _section_has_mixed_context(
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> bool:
    if section.get("isMixed") or _section_is_broad_container(section):
        return True
    section_map = indexes.get("sectionMap", {})
    current_id = _id(section.get("parentId"))
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        parent = section_map.get(current_id) or {}
        if parent.get("isMixed") or _section_is_broad_container(parent):
            return True
        current_id = _id(parent.get("parentId"))
    return False


def _section_can_expand(
    target_key: str,
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
    score: float,
) -> bool:
    if score < 7:
        return False
    if _section_is_locally_mixed(section):
        return False
    if _hard_context_conflict(target_key, section, indexes):
        return False
    if _section_conflict_hits(target_key, section):
        return False
    if _section_has_descendant_conflict(target_key, section, indexes):
        return False
    heading_hits = _direct_heading_hits(target_key, section)
    direct_type_hits = _section_type_hits(
        target_key,
        section,
        direct_only=True,
    )
    if target_key == "evaluation":
        # "评标办法" chapters often contain committee procedure, opening
        # procedure and invalid-bid clauses.  Scoring segments are recalled
        # below; only an explicitly named scoring section may be expanded.
        allowed = {
            "评分标准",
            "评分办法",
            "评分细则",
            "评分因素",
            "评审因素",
            "评审标准",
            "评标方法和标准",
            "综合评分",
            "技术评分",
            "商务评分",
            "价格评分",
        }
        return any(_compact(term) in _section_heading_blob(section) for term in allowed)
    if target_key == "contract":
        heading = _section_heading_blob(section)
        if _is_contract_reference_heading(heading):
            return False
        return bool(heading_hits) and bool(
            any(
                term in heading
                for term in (
                    "政府采购合同",
                    "合同文本",
                    "合同格式",
                    "合同条款",
                    "合同协议书",
                )
            )
        )
    if target_key == "template":
        return bool(heading_hits) or bool(
            value in {"document_format", "template"}
            for value in direct_type_hits
        )
    if target_key == "technical" and "scope" in direct_type_hits:
        # Scope is a container for project overview, not automatically a
        # technical requirement section.
        return bool(heading_hits)
    return bool(heading_hits or direct_type_hits)


def _is_directory_line(text: str, strict: bool = True) -> bool:
    clean = _text(text, 160)
    if not clean or len(clean) > 120:
        return False
    if any(mark in clean for mark in ("。", "；", "：", ":", "，", ",")):
        return False
    if _DIRECTORY_LINE_RE.match(clean):
        return True
    # 仅模板目标对无页码的短枚举行做过滤（如「7）合理化建议」是提纲而非要求）。
    # 非模板目标（技术/商务/评分）不启用，以免误删真要求。
    if strict is False:
        if _BARE_ENUMERATION_RE.match(clean):
            return not any(term in clean for term in _REQUIREMENT_SIGNAL_TERMS)
    return False


def _is_attachment_reference(text: str) -> bool:
    compact = _compact(text)
    return bool(
        re.match(r"^(?:详见|参见|见)?附件(?:[:：]|[一二三四五六七八九十\d])", compact)
    )


def _section_has_contract_context(
    section: Mapping[str, Any] | None,
    indexes: Mapping[str, Any],
) -> bool:
    current_id = _section_id(section or {})
    section_map = indexes.get("sectionMap", {})
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        current = section_map.get(current_id) or {}
        heading = _section_heading_blob(current)
        if _direct_heading_hits("contract", current) and not _is_contract_reference_heading(
            heading
        ):
            return True
        current_id = _id(current.get("parentId"))
    return False


def _is_contract_reference_heading(heading: str) -> bool:
    return any(
        _compact(term) in heading
        for term in _CONTRACT_REFERENCE_HEADING_TERMS
    ) or any(
        marker in heading
        for marker in (
            "公告",
            "备案",
            "中标与",
            "中标后",
            "拒不",
            "不与",
            "未按照",
            "合同签订",
            "转包",
            "变更、中止",
        )
    )


def _table_type_hits(
    target_key: str,
    segment: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> list[str]:
    aliases = {
        "technical": {"technical", "service_plan", "scope"},
        "qualification": {"qualification", "performance", "personnel", "financial"},
        "commercial": {"commercial", "pricing", "submission", "schedule"},
        "evaluation": {"evaluation"},
        "contract": {"contract"},
        "template": {"document_format", "attachment", "template"},
    }
    table_type = _segment_table_type(segment, indexes)
    return [table_type] if table_type in aliases.get(target_key, set()) else []


def _segment_table_type(
    segment: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> str:
    table_id = _id(segment.get("tableId"))
    return _text(
        segment.get("tableRequirementType")
        or (indexes.get("tableTypes", {}).get(table_id) if table_id else ""),
        60,
    )


def _infer_table_type(table: Mapping[str, Any]) -> str:
    """Infer a coarse business target for tables without parser metadata."""

    explicit = _compact(table.get("tableRequirementType"))
    if explicit in {
        "technical",
        "qualification",
        "commercial",
        "evaluation",
        "contract",
        "template",
    }:
        return explicit
    rows = table.get("rows") or []
    blob = _compact(
        "\n".join(
            str(row.get("text") or "")
            for row in rows
            if isinstance(row, Mapping)
        )
    )
    rules = (
        (
            "template",
            (
                "投标文件格式",
                "响应表",
                "偏离表",
                "投标函",
                "承诺函",
                "授权委托书",
            ),
        ),
        (
            "contract",
            (
                "合同条款",
                "合同正文",
                "合同履行",
                "合同价款",
                "甲方",
                "乙方",
            ),
        ),
        (
            "evaluation",
            ("评分标准", "评审因素", "评分办法", "评标办法", "分值"),
        ),
        (
            "qualification",
            (
                "资格审查",
                "资格性审查",
                "营业执照",
                "资质要求",
                "资格要求",
            ),
        ),
        (
            "technical",
            (
                "技术要求",
                "技术和服务要求",
                "技术服务要求",
                "技术参数",
                "技术参数与性能指标",
                "技术指标",
                "简要技术指标",
                "技术响应",
                "功能要求",
                "性能指标",
            ),
        ),
        (
            "commercial",
            (
                "报价",
                "最高限价",
                "投标保证金",
                "履约保证金",
                "交付期",
                "交货期",
                "付款方式",
                "质保期",
                "服务期限",
            ),
        ),
    )
    for target_key, terms in rules:
        if any(_compact(term) in blob for term in terms):
            return target_key
    return ""


def _segment_blob(segment: Mapping[str, Any]) -> str:
    return _compact(
        " ".join(
            [
                str(segment.get("text") or ""),
                str(segment.get("tableRequirementType") or ""),
                " ".join(str(value) for value in segment.get("scopeSectionTypes") or []),
            ]
        )
    )


def _is_target_noise(target_key: str, segment: Mapping[str, Any]) -> bool:
    """Return whether a source segment is context/template noise for matching.

    The target map is intentionally broad enough to recall a mixed chapter.
    Basic project metadata and invitation prose still need to be removed before
    that recall becomes a technical/qualification/commercial/evaluation item.
    They remain available to the project-info full-document pass and to the
    template source-range pass.
    """

    is_template = target_key == "template"
    if target_key not in _NON_MATCHING_TARGETS and not is_template:
        return False
    text = _text(segment.get("text"), 12000)
    if not text:
        return True
    if is_template:
        if _is_directory_line(text, strict=False):
            return True
        if any(term in text for term in _INVITATION_NOISE_TERMS):
            return True
        if _is_attachment_reference(text) and not _strong_body_hits(
            target_key,
            _segment_blob(segment),
        ):
            return True
        return False
    if is_form_title(text) or is_placeholder_row(text):
        return True
    if _is_directory_line(text):
        return True
    if target_key == "evaluation":
        if _evaluation_process_text_hits(text):
            return True
        # Generic committee/process prose is not a scoring criterion. Keep a
        # sentence mentioning the committee only when it also carries an
        # explicit scoring signal.
        if "评标委员会" in text and not any(
            term in text
            for term in _EVALUATION_SCORE_BODY_TERMS
        ):
            return True
    if any(term in text for term in _INVITATION_NOISE_TERMS):
        return True
    if any(term in text for term in _TEMPLATE_PROSE_TERMS):
        return True
    if _is_attachment_reference(text) and not _strong_body_hits(
        target_key,
        _segment_blob(segment),
    ):
        return True
    basic_field_hits = sum(
        1
        for label in _BASIC_FIELD_LABELS
        if re.search(rf"{re.escape(label)}\s*[:：]", text)
    )
    if basic_field_hits >= 2:
        return True
    if basic_field_hits and not any(
        term in text
        for term in (
            "技术参数",
            "技术指标",
            "功能要求",
            "性能要求",
            "服务要求",
            "资格要求",
            "资格条件",
            "营业执照",
            "资质",
            "证书",
            "业绩",
            "人员",
            "财务",
            "报价",
            "交付",
            "质保",
            "评分",
        )
    ):
        return True
    return False


def _term_hits(blob: str, terms: tuple[str, ...]) -> list[str]:
    return [term for term in terms if _compact(term) in blob]


def _build_indexes(
    segments: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    tables: list[dict[str, Any]],
) -> dict[str, Any]:
    ordered_segments = [
        segment
        for segment in segments
        if _segment_id(segment) and _text(segment.get("text"), 20000)
    ]
    segment_map = {_segment_id(item): item for item in ordered_segments}
    section_map = {_section_id(item): item for item in sections if _section_id(item)}
    table_map = {_table_id(item): item for item in tables if _table_id(item)}
    table_types = {
        table_id: _infer_table_type(table)
        for table_id, table in table_map.items()
    }
    positions = {
        _segment_id(item): index
        for index, item in enumerate(ordered_segments)
    }
    owners: dict[str, list[str]] = defaultdict(list)
    for section in sections:
        sid = _section_id(section)
        if not sid:
            continue
        for segment_id in section.get("segmentIds") or []:
            token = _id(segment_id)
            if token and token in segment_map and sid not in owners[token]:
                owners[token].append(sid)
    return {
        "segments": ordered_segments,
        "segmentMap": segment_map,
        "sectionMap": section_map,
        "tableMap": table_map,
        "tableTypes": table_types,
        "positions": positions,
        "owners": owners,
    }


def _owner_section(segment_id: str, indexes: Mapping[str, Any]) -> dict[str, Any] | None:
    owners = indexes.get("owners", {}).get(segment_id) or []
    section_map = indexes.get("sectionMap", {})
    candidates = [section_map[sid] for sid in owners if sid in section_map]
    return min(
        candidates,
        key=lambda section: (
            len(section.get("segmentIds") or []),
            -int(section.get("level") or 0),
        ),
        default=None,
    )


def _section_score(
    target_key: str,
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> tuple[float, list[str]]:
    rules = _TARGET_RULES.get(target_key, {})
    heading_blob = _section_heading_blob(section)
    heading_hits = _direct_heading_hits(target_key, section)
    type_hits = _section_type_hits(target_key, section, direct_only=True)
    body_hits: list[str] = []
    for segment_id in section.get("segmentIds") or []:
        segment = indexes.get("segmentMap", {}).get(_id(segment_id))
        if segment and not _is_target_noise(target_key, segment):
            body_hits.extend(
                _strong_body_hits(target_key, _segment_blob(segment))
            )
    body_hits = list(dict.fromkeys(body_hits))
    exclude_hits = _term_hits(heading_blob, rules.get("exclude", ()))
    score = len(heading_hits) * 8 + len(type_hits) * 7 + min(len(body_hits), 8) * 1.4
    if exclude_hits:
        score -= len(exclude_hits) * 5
    if any(
        target_key in {
            str(value)
            for value in segment.get("scopeSectionTypes") or []
        }
        for segment_id in section.get("segmentIds") or []
        for segment in [
            indexes.get("segmentMap", {}).get(_id(segment_id), {})
        ]
    ):
        score += 3
    return max(score, 0.0), list(dict.fromkeys([*type_hits, *heading_hits, *body_hits]))


def _segment_score(
    target_key: str,
    segment: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> tuple[float, list[str]]:
    if _is_target_noise(target_key, segment):
        return 0.0, []
    table_type = _segment_table_type(segment, indexes)
    owner = _owner_section(_segment_id(segment), indexes)
    template_context = (
        target_key == "template"
        and "template" in _section_context_target_types(owner, indexes)
    )
    if table_type and table_type != target_key and table_type in {
        "technical",
        "qualification",
        "commercial",
        "evaluation",
        "contract",
        "template",
    } and not template_context:
        return 0.0, []
    rules = _TARGET_RULES.get(target_key, {})
    blob = _segment_blob(segment)
    body_hits = _term_hits(blob, rules.get("body", ()))
    strong_body_hits = _strong_body_hits(target_key, blob)
    heading_hits = _direct_heading_hits(target_key, owner or {})
    direct_type_hits = _section_type_hits(
        target_key,
        owner or {},
        direct_only=True,
    )
    context_type_hits = _section_type_hits(target_key, owner or {})
    table_type_hits = _table_type_hits(target_key, segment, indexes)
    exclude_hits = _term_hits(
        _section_heading_blob(owner or {}),
        rules.get("exclude", ()),
    )
    mixed_context = _section_has_mixed_context(owner or {}, indexes)
    conflict_hits = _section_conflict_hits(target_key, owner or {})
    if any(hit in _HARD_CONTEXT_CONFLICTS for hit in conflict_hits):
        return 0.0, []
    if conflict_hits and not mixed_context:
        return 0.0, []
    if _section_context_conflict(target_key, owner, indexes):
        return 0.0, []
    if mixed_context and not (
        strong_body_hits
        or table_type_hits
        or (
            target_key == "contract"
            and bool(_direct_heading_hits("contract", owner or {}))
        )
    ):
        return 0.0, []
    if target_key == "evaluation":
        owner_heading = _section_heading_blob(owner or {})
        if any(
            _compact(term) in owner_heading
            for term in _EVALUATION_PROCESS_HEADING_TERMS
        ) and not table_type_hits:
            return 0.0, []
        if _evaluation_process_text_hits(blob) and not table_type_hits:
            return 0.0, []
    if target_key == "contract":
        if not _section_has_contract_context(owner, indexes):
            return 0.0, []
        if not (
            table_type_hits
            or strong_body_hits
            or _direct_heading_hits("contract", owner or {})
        ):
            return 0.0, []
    elif target_key == "evaluation":
        if not (strong_body_hits or table_type_hits):
            return 0.0, []
    elif target_key == "template":
        if not (strong_body_hits or table_type_hits or template_context):
            return 0.0, []
    elif not (
        strong_body_hits
        or table_type_hits
        or heading_hits
        or direct_type_hits
    ):
        return 0.0, []
    score = (
        len(strong_body_hits) * 2.0
        + min(len(body_hits), 4) * 0.35
        + min(len(heading_hits), 3) * 1.2
        + len(direct_type_hits) * 1.5
        + min(len(context_type_hits), 1) * 0.35
        + len(table_type_hits) * 3.0
    )
    rejection_hits = _term_hits(blob, _REJECTION_TERMS)
    if target_key != "rejection" and rejection_hits:
        # A full-document rejection scan owns these phrases.  Keep a mixed
        # technical sentence if it has strong technical evidence, but do not
        # let a bare "递交...投标无效" sentence become a commercial item.
        score -= len(rejection_hits) * 5
    if exclude_hits and not body_hits:
        score -= len(exclude_hits) * 3
    return max(score, 0.0), list(
        dict.fromkeys(
            [
                *direct_type_hits,
                *table_type_hits,
                *strong_body_hits,
                *body_hits,
                *heading_hits,
            ]
        )
    )


def _group_contiguous(
    ids: list[str],
    positions: Mapping[str, int],
    *,
    max_gap: int = 2,
) -> list[list[str]]:
    ordered = sorted({value for value in ids if value in positions}, key=positions.get)
    groups: list[list[str]] = []
    for value in ordered:
        if not groups or positions[value] - positions[groups[-1][-1]] > max_gap:
            groups.append([value])
        else:
            groups[-1].append(value)
    return groups


def _section_segment_ids(
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> list[str]:
    return list(
        dict.fromkeys(
            _id(segment_id)
            for segment_id in section.get("segmentIds") or []
            if _id(segment_id) in indexes.get("segmentMap", {})
        )
    )


_STRUCTURAL_ROOT_TERMS: dict[str, tuple[str, ...]] = {
    "content": (
        "招标内容及要求",
        "采购内容及要求",
        "项目内容及要求",
        "技术、服务及其他要求",
        "服务内容及商务要求",
        "采购需求",
    ),
    "qualification": (
        "资格审查与评标",
        "资格审查、评标",
        "资格审查及评标",
        "开标、资格审查、评标",
    ),
    "contract": (
        "政府采购合同",
        "合同主要条款",
        "合同文本",
        "合同条款和格式合同",
    ),
    "template": (
        "电子投标文件格式",
        "投标文件格式",
        "响应文件格式",
    ),
}

_STRUCTURAL_HEADING_TERMS: dict[str, tuple[str, ...]] = {
    "technical": (
        "技术和服务要求",
        "技术服务要求",
        "项目技术需求",
        "项目技术(质量)需求",
        "技术(质量)需求",
        "技术要求",
        "技术需求",
        "技术参数",
        "技术指标",
        "功能要求",
        "性能要求",
        "采购标的技术要求",
        "服务要求",
    ),
    "qualification": (
        "资格审查",
        "资格及资信",
        "资格要求",
        "资格条件",
        "合格投标人",
        "投标人资格",
        "供应商资格",
        "资质要求",
    ),
    "commercial": (
        "商务要求",
        "商务条件",
        "商务条款",
        "报价要求",
        "验收标准",
        "验收要求",
        "售后服务",
        "付款方式",
        "交付要求",
        "交货期",
        "服务期限",
        "质保",
    ),
    "evaluation": (
        "评分标准",
        "评分办法",
        "评分细则",
        "评标细则",
        "评审因素",
        "评标方法和标准",
        "评标标准",
        "评标方法",
    ),
}

_TEMPLATE_RESPONSE_HEADING_TERMS = (
    "投标文件格式",
    "电子投标文件格式",
    "响应文件格式",
    "响应表",
    "偏离表",
    "投标函",
    "承诺函",
    "授权委托书",
    "资格证明文件",
)

_REJECTION_HEADING_TERMS = (
    "废标",
    "无效投标",
    "投标无效",
    "否决",
    "不合格项",
    "审查不合格",
)

_EVALUATION_SCORE_BODY_TERMS = (
    "评分",
    "分值",
    "得分",
    "评审因素",
    "评审标准",
    "价格扣除",
    "评标价",
    "报价最低",
    "异常低价",
)

_EVALUATION_PROCESS_BODY_TERMS = (
    "评标委员会",
    "评审程序",
    "评标程序",
    "评审纪律",
    "评标纪律",
    "澄清",
    "补正",
)


def _section_parent_chain(
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return the current section and its ancestors, nearest first."""

    section_map = indexes.get("sectionMap", {})
    current_id = _section_id(section)
    result: list[dict[str, Any]] = []
    visited: set[str] = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        current = section_map.get(current_id)
        if not current:
            break
        result.append(current)
        current_id = _id(current.get("parentId"))
    return result


def _section_root(
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> dict[str, Any] | None:
    chain = _section_parent_chain(section, indexes)
    return chain[-1] if chain else None


def _section_is_under_root(
    section: Mapping[str, Any],
    root_id: str,
    indexes: Mapping[str, Any],
) -> bool:
    return any(
        _section_id(item) == root_id
        for item in _section_parent_chain(section, indexes)
    )


def _structural_root_kind(section: Mapping[str, Any]) -> str:
    heading = _section_heading_blob(section)
    for kind, terms in _STRUCTURAL_ROOT_TERMS.items():
        if any(_compact(term) in heading for term in terms):
            return kind
    return ""


def _structural_root_ids(
    indexes: Mapping[str, Any],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for section in indexes.get("sectionMap", {}).values():
        if int(section.get("level") or 0) > 1:
            continue
        kind = _structural_root_kind(section)
        section_id = _section_id(section)
        if kind == "contract" and _is_contract_reference_heading(
            _section_heading_blob(section)
        ):
            continue
        if kind and section_id:
            result[kind].append(section_id)
    return result


def _is_template_response_heading(section: Mapping[str, Any]) -> bool:
    heading = _section_heading_blob(section)
    return any(
        _compact(term) in heading
        for term in _TEMPLATE_RESPONSE_HEADING_TERMS
    )


def _is_rejection_heading(section: Mapping[str, Any]) -> bool:
    heading = _section_heading_blob(section)
    return any(
        _compact(term) in heading
        for term in _REJECTION_HEADING_TERMS
    )


def _structural_excluded_roots(
    target_key: str,
    indexes: Mapping[str, Any],
) -> set[str]:
    roots = _structural_root_ids(indexes)
    excluded: set[str] = set()
    for kind in ("contract", "template"):
        if target_key != kind:
            excluded.update(roots.get(kind, []))
    return excluded


def _structural_section_text(
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> str:
    values: list[str] = []
    heading_id = _id(section.get("headingSegmentId"))
    for segment_id in _section_segment_ids(section, indexes):
        if segment_id == heading_id:
            continue
        segment = indexes.get("segmentMap", {}).get(segment_id)
        if segment:
            values.append(str(segment.get("text") or ""))
    return _compact("\n".join(values))


def _evaluation_section_is_usable(
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> bool:
    heading = _section_heading_blob(section)
    if any(
        _compact(term) in heading
        for term in (
            "评标委员会",
            "评审程序",
            "评标程序",
            "评审纪律",
            "评标纪律",
            "符合性审查",
            "澄清有关问题",
        )
    ):
        return False

    body = _structural_section_text(section, indexes)
    score_signal = any(_compact(term) in body for term in _EVALUATION_SCORE_BODY_TERMS)
    process_signal = any(
        _compact(term) in body
        for term in _EVALUATION_PROCESS_BODY_TERMS
    )
    if score_signal and process_signal:
        # Numbered "7、评标方法和标准" blocks are the document's score
        # block even when they also explain the price-review procedure.
        if not re.search(r"^(?:7|七)", heading):
            return False
    if process_signal and not score_signal:
        return False
    return score_signal or bool(
        re.search(r"^(?:7|七)", heading)
    )


def _append_structural_section(
    selected: list[dict[str, Any]],
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> None:
    section_id = _section_id(section)
    if not section_id:
        return
    selected_ids = {_section_id(item) for item in selected}
    if section_id in selected_ids:
        return
    # Selecting a parent already covers its descendants because SectionTree
    # stores the complete source span on every section node.
    if any(
        _is_descendant_section(section, existing_id, indexes)
        for existing_id in selected_ids
    ):
        return
    selected[:] = [
        item
        for item in selected
        if not _is_descendant_section(
            item,
            section_id,
            indexes,
        )
    ]
    selected.append(dict(section))


def _structural_sections_for_target(
    target_key: str,
    indexes: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Find authoritative same-level blocks before keyword fallback.

    A tender normally has one or two document-level roots and then target
    blocks below them.  This function deliberately prefers those blocks over
    isolated sentence hits.  It returns an empty list when the tree is too
    ambiguous; the caller can then use the older segment-level fallback and,
    if configured, the bounded LLM review.
    """

    section_map = indexes.get("sectionMap", {})
    roots = _structural_root_ids(indexes)
    excluded_root_ids = _structural_excluded_roots(target_key, indexes)
    selected: list[dict[str, Any]] = []
    qualification_root_ids = set(roots.get("qualification", []))

    if target_key in {"template", "contract"}:
        for root_id in roots.get(target_key, []):
            root = section_map.get(root_id)
            if root:
                _append_structural_section(selected, root, indexes)
        if selected:
            return selected

        # The document may use a non-standard root heading. In that case
        # recover explicit template/contract sections instead of falling back
        # to sentence-level keyword recall.
        for section in section_map.values():
            if _section_is_broad_container(section):
                continue
            # Some parsers classify an attachment-like contract chapter as
            # ``document_format``.  A direct contract heading is stronger
            # than that inherited type and must not enter the template range.
            if (
                target_key == "template"
                and _direct_heading_hits("contract", section)
                and not _is_contract_reference_heading(
                    _section_heading_blob(section)
                )
            ):
                continue
            direct_type = bool(
                _section_type_hits(
                    target_key,
                    section,
                    direct_only=True,
                )
            )
            direct_heading = (
                _is_template_response_heading(section)
                if target_key == "template"
                else bool(_direct_heading_hits("contract", section))
            )
            if direct_type or direct_heading:
                _append_structural_section(selected, section, indexes)
        return selected

    for section in section_map.values():
        section_id = _section_id(section)
        if not section_id:
            continue
        if any(
            _section_is_under_root(section, root_id, indexes)
            for root_id in excluded_root_ids
        ):
            continue
        heading = _section_heading_blob(section)

        if target_key == "technical":
            if _is_template_response_heading(section):
                continue
            if _section_is_broad_container(section):
                continue
            direct_heading = any(
                _compact(term) in heading
                for term in _STRUCTURAL_HEADING_TERMS["technical"]
            )
            direct_types = set(
                _section_type_hits(
                    target_key,
                    section,
                    direct_only=True,
                )
            )
            typed_block = bool(
                direct_types.intersection({"technical", "service_plan"})
                or (
                    "scope" in direct_types
                    and direct_heading
                )
            )
            if direct_heading or typed_block:
                if not _section_is_locally_mixed(section):
                    _append_structural_section(selected, section, indexes)

        elif target_key == "qualification":
            if _is_rejection_heading(section) or _is_template_response_heading(section):
                continue
            direct_heading = any(
                _compact(term) in heading
                for term in _STRUCTURAL_HEADING_TERMS["qualification"]
            )
            typed_block = bool(
                _section_type_hits(
                    target_key,
                    section,
                    direct_only=True,
                )
            )
            if direct_heading or typed_block:
                if not _section_is_broad_container(section):
                    _append_structural_section(selected, section, indexes)

        elif target_key == "commercial":
            direct_heading = any(
                _compact(term) in heading
                for term in _STRUCTURAL_HEADING_TERMS["commercial"]
            )
            typed_block = bool(
                _section_type_hits(
                    target_key,
                    section,
                    direct_only=True,
                )
            )
            if direct_heading or typed_block:
                if not _section_is_broad_container(section):
                    _append_structural_section(selected, section, indexes)

        elif target_key == "evaluation":
            direct_heading = any(
                _compact(term) in heading
                for term in _STRUCTURAL_HEADING_TERMS["evaluation"]
            )
            typed_block = bool(
                _section_type_hits(target_key, section, direct_only=True)
            )
            if (direct_heading or typed_block) and _evaluation_section_is_usable(
                section,
                indexes,
            ):
                if not _section_is_broad_container(section):
                    _append_structural_section(selected, section, indexes)

    if target_key == "qualification" and qualification_root_ids:
        # Prefer the authoritative "资格审查" chapter when it exists.  The
        # announcement and bidder-instruction chapters often repeat the same
        # eligibility prose, but they are not the source range used for the
        # qualification matching handoff.
        qualification_scoped = [
            section
            for section in selected
            if any(
                _section_is_under_root(section, root_id, indexes)
                for root_id in qualification_root_ids
            )
        ]
        if qualification_scoped:
            selected = qualification_scoped

    return selected


def _structural_candidates(
    target_key: str,
    indexes: Mapping[str, Any],
) -> dict[str, Any]:
    structural_sections = _structural_sections_for_target(target_key, indexes)
    if not structural_sections:
        return {}

    selected_ids: list[str] = []
    expanded_ids: list[str] = []
    for section in structural_sections:
        section_id = _section_id(section)
        expanded_ids.append(section_id)
        selected_ids.extend(_section_segment_ids(section, indexes))
    selected_ids = sorted(
        set(selected_ids),
        key=indexes["positions"].get,
    )
    selected_section_ids = list(dict.fromkeys(expanded_ids))
    review_section_ids: list[str] = []
    review_segment_ids: list[str] = []
    mixed_candidate_segments: list[dict[str, Any]] = []
    selected_section_set = set(selected_section_ids)
    selected_root_ids = {
        _section_id(_section_root(section, indexes) or {})
        for section in structural_sections
        if _section_id(_section_root(section, indexes) or {})
    }
    review_root_kinds = {
        "technical": {"content"},
        "commercial": {"content"},
        "qualification": {"qualification"},
        "evaluation": {"qualification"},
    }
    # Template and contract are document-level source ranges. Once their
    # authoritative root exists, another chapter must never supplement it.
    root_only_target = target_key in {"template", "contract"}
    for section in indexes.get("sectionMap", {}).values():
        section_id = _section_id(section)
        if not section_id or section_id in selected_section_set:
            continue
        if not (
            section.get("isMixed")
            or _section_is_broad_container(section)
        ):
            continue
        if _is_rejection_heading(section):
            continue
        section_root_id = _section_id(
            _section_root(section, indexes) or {}
        )
        if root_only_target:
            continue
        section_root_kind = _structural_root_kind(
            _section_root(section, indexes) or {}
        )
        if (
            section_root_kind
            and section_root_kind not in review_root_kinds.get(target_key, set())
        ):
            continue
        # If this target already has a precise subsection in the same
        # document root, the broad parent is not an unresolved mixed range.
        # Adding it would pull in unrelated qualification/evaluation or
        # technical/commercial material.
        if section_root_id and section_root_id in selected_root_ids:
            continue
        if any(
            _is_descendant_section(
                selected_section,
                section_id,
                indexes,
            )
            for selected_section in structural_sections
        ):
            continue
        candidate_ids: list[str] = []
        for segment_id in _section_segment_ids(section, indexes):
            segment = indexes.get("segmentMap", {}).get(segment_id)
            if not segment:
                continue
            score, hits = _segment_score(target_key, segment, indexes)
            if score < 2:
                continue
            candidate_ids.append(segment_id)
            mixed_candidate_segments.append(
                {
                    "segmentId": segment_id,
                    "text": _text(segment.get("text"), 260),
                    "score": round(score, 2),
                    "hits": hits[:10],
                    "sectionId": section_id,
                    "tableId": _id(segment.get("tableId")),
                }
            )
        if candidate_ids:
            review_section_ids.append(section_id)
            review_segment_ids.extend(candidate_ids)
            selected_ids.extend(candidate_ids)
    selected_ids = sorted(
        set(selected_ids),
        key=indexes["positions"].get,
    )
    selected_section_ids.extend(
        section_id
        for section_id in review_section_ids
        if section_id not in selected_section_set
    )
    selected_table_ids = list(
        dict.fromkeys(
            _id(indexes["segmentMap"][segment_id].get("tableId"))
            for segment_id in selected_ids
            if _id(indexes["segmentMap"][segment_id].get("tableId"))
        )
    )
    return {
        "sectionIds": selected_section_ids,
        "selectedSectionIds": selected_section_ids,
        "segmentIds": selected_ids,
        "tableIds": selected_table_ids,
        # A review section is a bounded candidate, not a pure expanded range.
        "expandedSectionIds": list(dict.fromkeys(expanded_ids)),
        "expandedSegmentIds": [
            segment_id
            for segment_id in selected_ids
            if segment_id not in set(review_segment_ids)
        ],
        "reviewSectionIds": list(dict.fromkeys(review_section_ids)),
        "reviewSegmentIds": list(dict.fromkeys(review_segment_ids)),
        "hits": [
            _text(section.get("heading"), 160)
            for section in structural_sections
        ],
        "confidence": 0.98,
        "structuralBlocks": [
            {
                "sectionId": _section_id(section),
                "heading": _text(section.get("heading"), 180),
                "level": int(section.get("level") or 0),
                "segmentCount": len(section.get("segmentIds") or []),
                "sectionPath": list(section.get("sectionPath") or []),
            }
            for section in structural_sections
        ],
        "candidateSections": [
            {
                "sectionId": _section_id(section),
                "heading": _text(section.get("heading"), 180),
                "score": 100.0,
                "hits": [
                    _text(section.get("heading"), 160),
                ],
                "segmentCount": len(section.get("segmentIds") or []),
            }
            for section in structural_sections
        ],
        "candidateSegments": mixed_candidate_segments,
        "structuralSelection": True,
    }


def _rejection_rule_candidates(indexes: Mapping[str, Any]) -> dict[str, Any]:
    scored: list[tuple[float, str, list[str]]] = []
    for segment in indexes["segments"]:
        segment_id = _segment_id(segment)
        blob = _compact(segment.get("text"))
        if not segment_id or not blob:
            continue
        hits = [
            term
            for term in _REJECTION_CANDIDATE_TERMS
            if _compact(term) in blob
        ]
        if not hits:
            continue
        scored.append((len(hits), segment_id, hits))
    scored.sort(
        key=lambda item: (
            -item[0],
            indexes["positions"].get(item[1], 10**9),
        )
    )
    # Rejection is the deliberate full-document regex layer.  Do not cap the
    # selected ids before processing: a large file can contain more than 500
    # independent invalid-bid conditions.
    selected_ids = [segment_id for _score, segment_id, _hits in scored]
    section_ids = list(
        dict.fromkeys(
            _section_id(_owner_section(segment_id, indexes) or {})
            for segment_id in selected_ids
            if _section_id(_owner_section(segment_id, indexes) or {})
        )
    )
    table_ids = list(
        dict.fromkeys(
            _id(indexes["segmentMap"][segment_id].get("tableId"))
            for segment_id in selected_ids
            if _id(indexes["segmentMap"][segment_id].get("tableId"))
        )
    )
    hits = list(
        dict.fromkeys(
            hit
            for _score, _segment_id_value, segment_hits in scored[:80]
            for hit in segment_hits
        )
    )[:30]
    return {
        "sectionIds": section_ids,
        "selectedSectionIds": section_ids,
        "segmentIds": selected_ids,
        "tableIds": table_ids,
        "hits": hits,
        "confidence": min(0.9, 0.4 + min(len(selected_ids), 30) / 100),
        "candidateSections": [
            {
                "sectionId": section_id,
                "heading": _text(
                    indexes["sectionMap"][section_id].get("heading"),
                    180,
                ),
                "score": 1.0,
                "hits": [],
                "segmentCount": len(
                    indexes["sectionMap"][section_id].get("segmentIds") or []
                ),
            }
            for section_id in section_ids[:80]
            if section_id in indexes["sectionMap"]
        ],
        "candidateSegments": [
            {
                "segmentId": segment_id,
                "text": _text(
                    indexes["segmentMap"][segment_id].get("text"),
                    260,
                ),
                "score": round(score, 2),
                "hits": segment_hits[:10],
                "sectionId": _section_id(
                    _owner_section(segment_id, indexes) or {}
                ),
                "tableId": _id(
                    indexes["segmentMap"][segment_id].get("tableId")
                ),
            }
            for score, segment_id, segment_hits in scored
        ],
    }


def _rule_candidates(target_key: str, indexes: Mapping[str, Any]) -> dict[str, Any]:
    if target_key == "rejection":
        return _rejection_rule_candidates(indexes)
    if target_key == "project_info":
        return {
            "sectionIds": [],
            "segmentIds": [
                _segment_id(segment)
                for segment in indexes["segments"]
            ],
            "tableIds": [
                _table_id(table)
                for table in indexes["tableMap"].values()
            ],
            "hits": ["全文"],
            "confidence": 0.65,
            "expandedSectionIds": [],
            "expandedSegmentIds": [],
            "reviewSectionIds": [],
            "reviewSegmentIds": [],
        }

    structural = _structural_candidates(target_key, indexes)
    if structural:
        return structural

    section_scores: list[tuple[float, str, list[str]]] = []
    for section in indexes["sectionMap"].values():
        score, hits = _section_score(target_key, section, indexes)
        if score > 0:
            section_scores.append((score, _section_id(section), hits))
    section_scores.sort(key=lambda item: (-item[0], item[1]))

    segment_scores: list[tuple[float, str, list[str]]] = []
    for segment in indexes["segments"]:
        score, hits = _segment_score(target_key, segment, indexes)
        if score > 0:
            segment_scores.append((score, _segment_id(segment), hits))
    segment_scores.sort(key=lambda item: (-item[0], indexes["positions"].get(item[1], 10**9)))
    segment_score_map = {
        segment_id: (score, hits)
        for score, segment_id, hits in segment_scores
    }

    candidate_section_ids = [
        sid
        for score, sid, _hits in section_scores
        if score >= 3
    ][:80]
    selected_ids: list[str] = []
    expanded_section_ids: list[str] = []
    expanded_segment_ids: list[str] = []
    for score, sid, _hits in section_scores[:80]:
        # A broad parent chapter can contain several target modules.  Only
        # expand a section wholesale when its heading/type is a strong signal;
        # otherwise keep the segment-level recall so mixed chapters can be
        # split later by the LLM or by the rule fallback.
        section = indexes["sectionMap"].get(sid) or {}
        if not _section_can_expand(target_key, section, indexes, score):
            continue
        expanded_section_ids.append(sid)
        section_segment_ids = _section_segment_ids(section, indexes)
        expanded_segment_ids.extend(section_segment_ids)
        # A pure target chapter is a protected source range.  Item-level
        # processors still remove headings/placeholders, but range discovery
        # must not discard low-keyword正文 or table rows here.
        selected_ids.extend(section_segment_ids)
    selected_ids.extend(
        segment_id
        for score, segment_id, _hits in segment_scores
        if score >= 2
    )
    selected_ids = list(dict.fromkeys(selected_ids))
    expanded_section_ids = list(dict.fromkeys(expanded_section_ids))
    expanded_segment_ids = sorted(
        set(expanded_segment_ids),
        key=indexes["positions"].get,
    )
    expanded_segment_set = set(expanded_segment_ids)

    # Only the unresolved part of a candidate range is eligible for LLM
    # boundary review.  Pure sections remain outside this universe even when
    # they sit below a mixed parent chapter.
    mutable_selected_ids = [
        segment_id
        for segment_id in selected_ids
        if segment_id not in expanded_segment_set
    ]
    review_section_ids: list[str] = []
    review_segment_ids: list[str] = []
    section_score_map = {
        section_id: (score, section_hits)
        for score, section_id, section_hits in section_scores
    }
    mutable_set = set(mutable_selected_ids)
    for _score, sid, _hits in section_scores[:80]:
        if sid in expanded_section_ids:
            continue
        section = indexes["sectionMap"].get(sid) or {}
        section_ids = _section_segment_ids(section, indexes)
        if not mutable_set.intersection(section_ids):
            continue
        review_section_ids.append(sid)
        if _section_is_locally_mixed(section):
            review_segment_ids.extend(section_ids)
    review_segment_ids.extend(mutable_selected_ids)
    review_segment_ids = [
        segment_id
        for segment_id in dict.fromkeys(review_segment_ids)
        if segment_id not in expanded_segment_set
    ]
    review_section_ids = list(dict.fromkeys(review_section_ids))
    for segment_id in review_segment_ids:
        owner_id = _section_id(_owner_section(segment_id, indexes) or {})
        if owner_id and owner_id not in review_section_ids:
            review_section_ids.append(owner_id)

    selected_section_ids = list(
        dict.fromkeys(
            [
                *expanded_section_ids,
                *[
                    _section_id(_owner_section(segment_id, indexes) or {})
                    for segment_id in selected_ids
                    if _section_id(_owner_section(segment_id, indexes) or {})
                ],
            ]
        )
    )
    selected_table_ids = list(
        dict.fromkeys(
            _id(indexes["segmentMap"][segment_id].get("tableId"))
            for segment_id in selected_ids
            if _id(indexes["segmentMap"][segment_id].get("tableId"))
        )
    )
    hits = list(
        dict.fromkeys(
            hit
            for _score, _sid, section_hits in section_scores[:20]
            for hit in section_hits
        )
    )[:30]
    review_candidate_sections = [
        {
            "sectionId": sid,
            "heading": _text(
                indexes["sectionMap"][sid].get("heading"),
                180,
            ),
            "score": round(section_score_map.get(sid, (0.0, []))[0], 2),
            "hits": section_score_map.get(sid, (0.0, []))[1][:12],
            "segmentCount": len(
                indexes["sectionMap"][sid].get("segmentIds") or []
            ),
        }
        for sid in review_section_ids
        if sid in indexes["sectionMap"]
    ]
    review_candidate_segments = [
        {
            "segmentId": segment_id,
            "text": _text(
                indexes["segmentMap"][segment_id].get("text"),
                260,
            ),
            "score": round(segment_score_map.get(segment_id, (0.0, []))[0], 2),
            "hits": segment_score_map.get(segment_id, (0.0, []))[1][:10],
            "sectionId": _section_id(
                _owner_section(segment_id, indexes) or {}
            ),
            "tableId": _id(
                indexes["segmentMap"][segment_id].get("tableId")
            ),
        }
        for segment_id in review_segment_ids
    ]
    return {
        # ``sectionIds`` is the candidate universe exposed to the LLM.
        # ``selectedSectionIds`` is the narrower rule-only selection returned
        # when the LLM is disabled or fails.
        "sectionIds": list(
            dict.fromkeys([*candidate_section_ids, *selected_section_ids])
        ),
        "selectedSectionIds": selected_section_ids,
        "segmentIds": selected_ids,
        "tableIds": selected_table_ids,
        "expandedSectionIds": expanded_section_ids,
        "expandedSegmentIds": expanded_segment_ids,
        "reviewSectionIds": review_section_ids,
        "reviewSegmentIds": review_segment_ids,
        "hits": hits,
        "confidence": min(0.94, 0.35 + min(len(selected_ids), 30) / 100),
        "candidateSections": [
            {
                "sectionId": sid,
                "heading": _text(indexes["sectionMap"][sid].get("heading"), 180),
                "score": round(score, 2),
                "hits": hits[:12],
                "segmentCount": len(indexes["sectionMap"][sid].get("segmentIds") or []),
            }
            for score, sid, hits in section_scores[:80]
        ],
        "candidateSegments": [
            {
                "segmentId": segment_id,
                "text": _text(indexes["segmentMap"][segment_id].get("text"), 260),
                "score": round(score, 2),
                "hits": hits[:10],
                "sectionId": _section_id(_owner_section(segment_id, indexes) or {}),
                "tableId": _id(indexes["segmentMap"][segment_id].get("tableId")),
            }
            for score, segment_id, hits in segment_scores[:500]
        ],
        "reviewCandidateSections": review_candidate_sections,
        "reviewCandidateSegments": review_candidate_segments,
    }


def _json_object(content: str) -> dict[str, Any] | None:
    clean = re.sub(r"^```(?:json)?\s*", "", str(content or "").strip(), flags=re.I)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(clean[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _llm_ready(ai: Mapping[str, Any] | None) -> bool:
    config = ai or {}
    return config.get("enabled") is not False and bool(
        str(config.get("baseUrl") or "").strip()
        and str(config.get("apiKey") or "").strip()
        and str(config.get("model") or "").strip()
    )


def _selected_review_segment_ids(
    target_key: str,
    selected: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> list[str]:
    """Build the full-text review universe for a target.

    The first LLM pass is intentionally compact.  This second pass reviews the
    selected ranges with fuller source text, and may include sibling segments
    from selected sections so a heading-only selection can recover body rows
    that did not contain obvious keywords.
    """

    valid_segments = set(indexes["segmentMap"])
    explicit_review_ids = selected.get("reviewSegmentIds")
    if isinstance(explicit_review_ids, list):
        return sorted(
            {
                _id(value)
                for value in explicit_review_ids
                if _id(value) in valid_segments
            },
            key=indexes["positions"].get,
        )
    selected_ids = [
        _id(value)
        for value in selected.get("segmentIds") or []
        if _id(value) in valid_segments
    ]
    selected_section_ids = [
        _id(value)
        for value in selected.get("sectionIds") or []
        if _id(value) in indexes["sectionMap"]
    ]
    for section_id in selected_section_ids:
        section = indexes["sectionMap"].get(section_id) or {}
        section_ids = [
            _id(value)
            for value in section.get("segmentIds") or []
            if _id(value) in valid_segments
        ]
        # Do not expand a broad parent chapter wholesale.  Its selected leaf
        # segments are still reviewed, and the leaf owner expansion below can
        # recover nearby rows without sending an entire tender document to the
        # model.
        if (
            len(section_ids) > 80
            or _section_has_mixed_context(section, indexes)
            or _section_is_broad_container(section)
        ):
            continue
        for segment_id in section.get("segmentIds") or []:
            sid = _id(segment_id)
            if not sid or sid not in valid_segments:
                continue
            segment = indexes["segmentMap"][sid]
            owner = _owner_section(sid, indexes)
            if _is_target_noise(target_key, segment):
                continue
            if _hard_context_conflict(target_key, owner, indexes):
                continue
            selected_ids.append(sid)
    # A selected segment may belong to a smaller leaf section than the
    # selected parent section.  Expand that leaf when it is bounded; this is
    # the main path by which a rule miss can be recovered by the reviewer.
    for segment_id in list(selected_ids):
        owner = _owner_section(segment_id, indexes)
        if not owner:
            continue
        owner_ids = [
            _id(value)
            for value in owner.get("segmentIds") or []
            if _id(value) in valid_segments
        ]
        if (
            not owner_ids
            or len(owner_ids) > 80
            or _section_has_mixed_context(owner, indexes)
            or _section_is_broad_container(owner)
        ):
            continue
        for owner_segment_id in owner_ids:
            owner_segment = indexes["segmentMap"][owner_segment_id]
            if _is_target_noise(target_key, owner_segment):
                continue
            if _hard_context_conflict(target_key, owner, indexes):
                continue
            selected_ids.append(owner_segment_id)
    return sorted(set(selected_ids), key=indexes["positions"].get)


def _review_prompt(
    selections: Mapping[str, dict[str, Any]],
    indexes: Mapping[str, Any],
) -> tuple[str, str, dict[str, set[str]]]:
    selected_keys = set(selections)
    target_lines = "\n".join(
        f"- {item['key']}: {item['label']}；{item['description']}"
        for item in TARGET_MODULES
        if item["key"] in selected_keys
    )
    allowed: dict[str, set[str]] = {}
    blocks: list[str] = []
    for key in TARGET_KEYS:
        if key in {"project_info", "rejection"}:
            continue
        selected = selections.get(key) or {}
        if not selected.get("segmentIds") and not selected.get("sectionIds"):
            continue
        segment_ids = _selected_review_segment_ids(
            key,
            selected,
            indexes,
        )
        if not segment_ids:
            continue
        allowed[key] = set(segment_ids)
        lines: list[str] = []
        for segment_id in segment_ids:
            segment = indexes["segmentMap"].get(segment_id) or {}
            owner = _owner_section(segment_id, indexes) or {}
            section_path = " / ".join(
                _text(value, 120)
                for value in owner.get("sectionPath") or []
                if _text(value, 120)
            )
            table_id = _id(segment.get("tableId"))
            lines.append(
                "\n".join(
                    [
                        f"[{segment_id}]",
                        f"section={_section_id(owner) or '-'}",
                        f"path={section_path or _text(owner.get('heading'), 180) or '-'}",
                        f"table={table_id or '-'}",
                        f"text={_text(segment.get('text'), 8000)}",
                    ]
                )
            )
        blocks.append(f"目标 {key} 待复核候选:\n" + "\n---\n".join(lines))
    system = (
        "你是招标文件目标范围复核器。上一阶段已经粗定位了目标范围，"
        "现在请阅读每个候选 segment 的较完整原文，判断它是否真正属于对应目标。"
        "正则命中可能错也可能漏；你要保留真实目标内容，剔除目录、公告、附件引用、表头、"
        "评审流程、合同后置流程、模板占位说明等噪声。"
        "如果一个章节混合了技术、商务、评分、模板，只保留属于当前目标的 segmentId。"
        "模板章节里的技术/资格响应表只属于 template，不进入 technical 或 qualification。"
        "contract 只保留真正的中标后合同正文或合同文本，不要保留普通合同签订流程。"
        "rejection 只保留会导致投标无效、被否决、不予受理或资格被取消的真实条件；"
        "普通的项目限制、一般的“不接受联合体”说明、公告引用和合同流程不要直接判为废标。"
        "只能从输入中复制真实 ID，不能编造 ID。"
        f"\n业务目标:\n{target_lines}\n"
        f'严格输出 JSON：{{"targets":[{{"targetModule":"{next(iter(selections), "technical")}","keepSegmentIds":[],"excludeSegmentIds":[],"confidence":0.0,"reason":""}}]}}'
    )
    return system, "\n\n".join(blocks), allowed


def _bounded_ai_int(
    ai: Mapping[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        value = int(ai.get(key, default))
    except (TypeError, ValueError):
        value = default
    return min(max(value, minimum), maximum)


def _target_review_limits(ai: Mapping[str, Any]) -> dict[str, int]:
    """Return conservative limits for the only LLM phase in this route.

    These are request budgets rather than document limits.  A mixed range can
    still contain thousands of source characters; it is split into several
    independent requests so one slow provider call cannot hold the whole
    document hostage.
    """

    generic_timeout = _bounded_ai_int(ai, "timeoutSeconds", 120, 15, 300)
    return {
        "batchSize": _bounded_ai_int(
            ai,
            "targetReviewBatchSize",
            24,
            8,
            64,
        ),
        "maxInputChars": _bounded_ai_int(
            ai,
            "targetReviewMaxInputChars",
            16000,
            6000,
            60000,
        ),
        "maxTokens": _bounded_ai_int(
            ai,
            "targetReviewMaxTokens",
            1800,
            600,
            8000,
        ),
        "timeoutSeconds": _bounded_ai_int(
            ai,
            "targetReviewTimeoutSeconds",
            min(generic_timeout, 45),
            15,
            180,
        ),
        # Two attempts preserve the existing unversioned-base-url -> /v1
        # fallback while keeping a timed-out batch bounded.
        "maxAttempts": _bounded_ai_int(
            ai,
            "targetReviewMaxAttempts",
            2,
            1,
            3,
        ),
        "maxSplitDepth": _bounded_ai_int(
            ai,
            "targetReviewMaxSplitDepth",
            3,
            0,
            6,
        ),
        "minSplitSegments": 4,
    }


def _review_prompt_for_batch(
    target_key: str,
    segment_ids: list[str],
    indexes: Mapping[str, Any],
) -> tuple[str, str, dict[str, set[str]]]:
    return _review_prompt(
        {
            target_key: {
                "segmentIds": segment_ids,
                "reviewSegmentIds": segment_ids,
            }
        },
        indexes,
    )


def _review_prompt_chars(system: str, user: str) -> int:
    return len(str(system or "")) + len(str(user or ""))


def _review_source_chars(
    segment_ids: list[str],
    indexes: Mapping[str, Any],
) -> int:
    return sum(
        len(
            str(
                (indexes["segmentMap"].get(segment_id) or {}).get("text")
                or ""
            )
        )
        for segment_id in segment_ids
    )


def _build_review_batches(
    target_key: str,
    segment_ids: list[str],
    indexes: Mapping[str, Any],
    *,
    max_segments: int,
    max_chars: int,
) -> list[list[str]]:
    """Build batches using the rendered prompt size, not raw source size."""

    batches: list[list[str]] = []
    current: list[str] = []
    for segment_id in segment_ids:
        trial = [*current, segment_id]
        system, user, allowed = _review_prompt_for_batch(
            target_key,
            trial,
            indexes,
        )
        if target_key not in allowed:
            continue
        prompt_chars = _review_prompt_chars(system, user)
        if current and (
            len(trial) > max_segments
            or prompt_chars > max_chars
        ):
            batches.append(current)
            current = [segment_id]
        else:
            current = trial
    if current:
        batches.append(current)
    return batches


def _split_review_batch(
    segment_ids: list[str],
    *,
    depth: int,
    limits: Mapping[str, int],
) -> tuple[list[str], list[str]] | None:
    if (
        depth >= int(limits["maxSplitDepth"])
        or len(segment_ids) <= int(limits["minSplitSegments"])
    ):
        return None
    midpoint = len(segment_ids) // 2
    if midpoint <= 0 or midpoint >= len(segment_ids):
        return None
    return segment_ids[:midpoint], segment_ids[midpoint:]


def _review_failure_can_split(
    result: Mapping[str, Any],
    failure_kind: str,
) -> bool:
    if failure_kind in {"length", "invalid_json", "coverage", "empty"}:
        return True
    error_text = str(
        result.get("error")
        or result.get("message")
        or ""
    ).lower()
    return any(
        marker in error_text
        for marker in (
            "超时",
            "响应超时",
            "timeout",
            "timed out",
            "连接中断",
            "连接失败",
            "无法连接",
            "http 429",
            "http 502",
            "http 503",
            "http 504",
            "context length",
            "maximum context",
            "上下文",
            "输入过长",
            "token 上限",
            "非 json",
        )
    )


def _llm_review_ranges(
    selections: Mapping[str, dict[str, Any]],
    indexes: Mapping[str, Any],
    ai: Mapping[str, Any],
    diagnostics: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    diagnostics["llmReviewCalled"] = False
    diagnostics["llmReviewSuccess"] = False
    diagnostics["llmReviewTargetCount"] = 0
    limits = _target_review_limits(ai)
    diagnostics["llmReviewLimits"] = dict(limits)
    result_map: dict[str, dict[str, Any]] = {}
    attempted = 0
    successes = 0
    errors: list[str] = []
    output_chars = 0
    split_count = 0
    fallback_segment_ids: list[str] = []
    batch_events: list[dict[str, Any]] = []

    for key in TARGET_KEYS:
        if key in {"project_info", "rejection"}:
            continue
        selected = selections.get(key) or {}
        # A structurally identified template root is already an authoritative
        # source range.  Sending it through sentence-level review lets the
        # model remove form instructions, repeated headings, or typed
        # technical/commercial response tables that must remain in the
        # downloadable artifact.
        if key == "template" and selected.get("expandedSectionIds"):
            continue
        if not selected.get("reviewSegmentIds"):
            continue
        _system, user, allowed = _review_prompt({key: selected}, indexes)
        if not user.strip() or key not in allowed:
            continue
        all_segment_ids = sorted(
            allowed[key],
            key=indexes["positions"].get,
        )
        batches = _build_review_batches(
            key,
            all_segment_ids,
            indexes,
            max_segments=limits["batchSize"],
            max_chars=limits["maxInputChars"],
        )
        queue: list[tuple[list[str], int]] = [
            (batch, 0)
            for batch in batches
            if batch
        ]
        target_keep: list[str] = []
        target_exclude: list[str] = []
        target_reviewed: list[str] = []
        target_confidences: list[float] = []
        target_reasons: list[str] = []
        target_fallback: list[str] = []

        while queue:
            batch_ids, split_depth = queue.pop(0)
            batch_system, batch_user, batch_allowed = _review_prompt_for_batch(
                key,
                batch_ids,
                indexes,
            )
            if not batch_user.strip() or key not in batch_allowed:
                target_fallback.extend(batch_ids)
                continue
            attempted += 1
            prompt_chars = _review_prompt_chars(batch_system, batch_user)
            source_chars = _review_source_chars(batch_ids, indexes)
            event: dict[str, Any] = {
                "targetModule": key,
                "segmentCount": len(batch_ids),
                "sourceChars": source_chars,
                "promptChars": prompt_chars,
                "maxTokens": limits["maxTokens"],
                "timeoutSeconds": limits["timeoutSeconds"],
                "maxAttempts": limits["maxAttempts"],
                "splitDepth": split_depth,
            }
            started = time.perf_counter()
            result: dict[str, Any]
            try:
                result = call_openai_compatible(
                    {
                        **dict(ai),
                        "timeoutSeconds": limits["timeoutSeconds"],
                    },
                    [
                        {"role": "system", "content": batch_system},
                        {"role": "user", "content": batch_user},
                    ],
                    max_tokens=limits["maxTokens"],
                    temperature=0,
                    max_attempts=limits["maxAttempts"],
                )
            except Exception as error:  # pragma: no cover - provider guard
                result = {
                    "success": False,
                    "error": _text(error, 240),
                }
            elapsed_ms = round((time.perf_counter() - started) * 1000)
            output_chars += len(str(result.get("content") or ""))
            event["elapsedMs"] = elapsed_ms
            event["success"] = bool(result.get("success"))
            event["outputChars"] = len(str(result.get("content") or ""))

            failure_kind = ""
            failure_message = ""
            if not result.get("success"):
                failure_kind = "provider"
                failure_message = _text(
                    result.get("error") or result.get("message"),
                    240,
                )
            elif str(result.get("finishReason") or "").lower() in {
                "length",
                "max_tokens",
            }:
                failure_kind = "length"
                failure_message = "返回达到 token 上限"
            else:
                payload = _json_object(str(result.get("content") or ""))
                if not payload:
                    failure_kind = "invalid_json"
                    failure_message = "返回不是有效 JSON"
                else:
                    raw_targets = payload.get("targets")
                    if not isinstance(raw_targets, list):
                        raw_targets = (
                            payload.get("targetRanges")
                            or payload.get("results")
                            or payload.get("items")
                            or []
                        )
                    parsed = False
                    for raw in raw_targets:
                        if not isinstance(raw, dict):
                            continue
                        raw_key = _compact(
                            raw.get("targetModule") or raw.get("target")
                        )
                        key_aliases = {
                            _compact(key),
                            _compact(TARGET_META[key]["label"]),
                        }
                        if raw_key not in key_aliases:
                            continue
                        keep_ids = [
                            _id(value)
                            for value in (
                                raw.get("keepSegmentIds")
                                or raw.get("selectedSegmentIds")
                                or raw.get("segmentIds")
                                or raw.get("keepIds")
                                or []
                            )
                            if _id(value) in batch_allowed[key]
                        ]
                        exclude_ids = [
                            _id(value)
                            for value in (
                                raw.get("excludeSegmentIds")
                                or raw.get("excludedSegmentIds")
                                or raw.get("excludeIds")
                                or []
                            )
                            if _id(value) in batch_allowed[key]
                        ]
                        if not keep_ids and not exclude_ids:
                            continue
                        covered_ids = set(keep_ids) | set(exclude_ids)
                        if covered_ids != set(batch_allowed[key]):
                            failure_kind = "coverage"
                            failure_message = (
                                "复核结果未覆盖该批次全部 segment"
                            )
                            continue
                        parsed = True
                        target_keep.extend(keep_ids)
                        target_exclude.extend(exclude_ids)
                        target_reviewed.extend(batch_allowed[key])
                        try:
                            confidence = float(raw.get("confidence"))
                            if 0 <= confidence <= 1:
                                target_confidences.append(confidence)
                        except (TypeError, ValueError):
                            pass
                        reason = _text(raw.get("reason"), 300)
                        if reason:
                            target_reasons.append(reason)
                        break
                    if not parsed and not failure_kind:
                        failure_kind = "empty"
                        failure_message = "未返回有效目标复核结果"

            if failure_kind:
                should_split = _review_failure_can_split(
                    result,
                    failure_kind,
                )
                split = (
                    _split_review_batch(
                        batch_ids,
                        depth=split_depth,
                        limits=limits,
                    )
                    if should_split
                    else None
                )
                if split:
                    split_count += 1
                    event["action"] = "split"
                    event["failure"] = failure_message
                    batch_events.append(event)
                    queue[0:0] = [
                        (split[0], split_depth + 1),
                        (split[1], split_depth + 1),
                    ]
                    continue
                event["action"] = "rule_fallback"
                event["failure"] = failure_message
                target_fallback.extend(batch_ids)
                errors.append(f"{key}: {failure_message}")
                batch_events.append(event)
                continue

            successes += 1
            event["action"] = "applied"
            batch_events.append(event)

        fallback_segment_ids.extend(target_fallback)
        if target_reviewed:
            result_map[key] = {
                "targetModule": key,
                "keepSegmentIds": list(dict.fromkeys(target_keep)),
                "excludeSegmentIds": list(dict.fromkeys(target_exclude)),
                "reviewedSegmentIds": sorted(
                    set(target_reviewed),
                    key=indexes["positions"].get,
                ),
                "fallbackSegmentIds": sorted(
                    set(target_fallback),
                    key=indexes["positions"].get,
                ),
                "confidence": (
                    sum(target_confidences) / len(target_confidences)
                    if target_confidences
                    else None
                ),
                "reason": "；".join(dict.fromkeys(target_reasons))[:300],
            }

    diagnostics["llmReviewCalled"] = attempted > 0
    diagnostics["llmReviewSuccess"] = successes > 0
    diagnostics["llmReviewCallCount"] = attempted
    diagnostics["llmReviewSuccessCount"] = successes
    diagnostics["llmReviewOutputChars"] = output_chars
    diagnostics["llmReviewSplitCount"] = split_count
    diagnostics["llmReviewFallbackToRule"] = bool(fallback_segment_ids)
    diagnostics["llmReviewFallbackSegmentCount"] = len(
        set(fallback_segment_ids)
    )
    diagnostics["llmReviewPartialFailure"] = bool(errors and successes)
    diagnostics["llmReviewBatches"] = batch_events[:200]
    if batch_events:
        diagnostics["llmReviewMaxPromptChars"] = max(
            int(event.get("promptChars") or 0)
            for event in batch_events
        )
        diagnostics["llmReviewMaxSourceChars"] = max(
            int(event.get("sourceChars") or 0)
            for event in batch_events
        )
    if errors:
        diagnostics["llmReviewError"] = "；".join(errors[:5])
    diagnostics["llmReviewTargetCount"] = len(result_map)
    return result_map


def _apply_review(
    selected: dict[str, Any],
    reviewed: Mapping[str, Any] | None,
    indexes: Mapping[str, Any],
) -> dict[str, Any]:
    if not reviewed:
        return selected
    keep_ids = [
        _id(value)
        for value in reviewed.get("keepSegmentIds") or []
        if _id(value) in indexes["segmentMap"]
    ]
    reviewed_ids = {
        _id(value)
        for value in reviewed.get("reviewedSegmentIds") or []
        if _id(value) in indexes["segmentMap"]
    }
    exclude_ids = {
        _id(value)
        for value in reviewed.get("excludeSegmentIds") or []
        if _id(value) in indexes["segmentMap"]
    }
    fallback_ids = {
        _id(value)
        for value in reviewed.get("fallbackSegmentIds") or []
        if _id(value) in indexes["segmentMap"]
    }
    original_ids = [
        _id(value)
        for value in selected.get("segmentIds") or []
        if _id(value) in indexes["segmentMap"]
    ]
    if reviewed_ids:
        segment_ids = [
            value
            for value in original_ids
            if value not in reviewed_ids and value not in exclude_ids
        ]
        segment_ids.extend(keep_ids)
    else:
        segment_ids = [
            value
            for value in original_ids
            if value not in exclude_ids
        ]
    segment_ids = sorted(dict.fromkeys(segment_ids), key=indexes["positions"].get)
    table_ids: list[str] = []
    section_ids: list[str] = []
    for segment_id in segment_ids:
        segment = indexes["segmentMap"].get(segment_id) or {}
        table_id = _id(segment.get("tableId"))
        if table_id and table_id in indexes["tableMap"]:
            table_ids.append(table_id)
        owner = _owner_section(segment_id, indexes)
        owner_id = _section_id(owner or {})
        if owner_id:
            section_ids.append(owner_id)
    confidence = selected.get("confidence", 0.0)
    try:
        if reviewed.get("confidence") is not None:
            confidence = min(max(float(reviewed.get("confidence")), 0), 1)
    except (TypeError, ValueError):
        pass
    return {
        **selected,
        "segmentIds": segment_ids,
        "sectionIds": list(dict.fromkeys(section_ids)),
        "tableIds": list(dict.fromkeys(table_ids)),
        "confidence": confidence,
        "reason": _text(reviewed.get("reason") or selected.get("reason"), 300),
        "reviewedByLlm": True,
        "llmExcludedSegmentIds": sorted(exclude_ids, key=indexes["positions"].get),
        "llmFallbackSegmentIds": sorted(
            fallback_ids,
            key=indexes["positions"].get,
        ),
    }


def _validate_selection(
    target_key: str,
    raw: Mapping[str, Any] | None,
    candidate: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> dict[str, Any]:
    is_rule_selection = raw is None
    raw = raw or {}
    valid_sections = set(indexes["sectionMap"])
    valid_segments = set(indexes["segmentMap"])
    valid_tables = set(indexes["tableMap"])
    section_ids = [
        _id(value)
        for value in (
            (
                candidate.get("selectedSectionIds")
                if is_rule_selection
                else candidate.get("sectionIds")
            )
            or []
            if is_rule_selection
            else raw.get("sectionIds") or []
        )
        if _id(value) in valid_sections
    ]
    segment_ids = [
        _id(value)
        for value in (
            candidate.get("segmentIds") or []
            if is_rule_selection
            else raw.get("segmentIds") or []
        )
        if _id(value) in valid_segments
    ]
    table_ids = [
        _id(value)
        for value in (
            candidate.get("tableIds") or []
            if is_rule_selection
            else raw.get("tableIds") or []
        )
        if _id(value) in valid_tables
    ]
    candidate_segment_ids = set(candidate.get("segmentIds") or [])
    candidate_section_ids = set(candidate.get("sectionIds") or [])
    # The LLM is a selector, not a source of arbitrary document ids.  Restrict
    # its output to the rule-recalled candidate universe before processing.
    segment_ids = [value for value in segment_ids if value in candidate_segment_ids]
    section_ids = [value for value in section_ids if value in candidate_section_ids]
    for section_id in section_ids:
        segment_ids.extend(
            _id(value)
            for value in indexes["sectionMap"][section_id].get("segmentIds") or []
            if _id(value) in valid_segments and _id(value) in candidate_segment_ids
        )
    segment_ids = list(dict.fromkeys(segment_ids))
    for segment_id in segment_ids:
        table_id = _id(indexes["segmentMap"][segment_id].get("tableId"))
        if table_id and table_id in valid_tables:
            table_ids.append(table_id)
        owner = _owner_section(segment_id, indexes)
        owner_id = _section_id(owner or {})
        if owner_id:
            section_ids.append(owner_id)
    section_ids = list(dict.fromkeys(section_ids))
    table_ids = list(dict.fromkeys(table_ids))
    confidence = candidate.get("confidence", 0.0)
    try:
        confidence = min(
            max(
                float(
                    candidate.get("confidence", confidence)
                    if is_rule_selection
                    else raw.get("confidence", confidence)
                ),
                0,
            ),
            1,
        )
    except (TypeError, ValueError):
        confidence = float(candidate.get("confidence") or 0.0)
    return {
        "targetModule": target_key,
        "sectionIds": section_ids,
        "segmentIds": sorted(segment_ids, key=indexes["positions"].get),
        "tableIds": table_ids,
        "confidence": confidence,
        "reason": _text(raw.get("reason"), 300),
    }


def _ranges_from_segments(
    segment_ids: list[str],
    indexes: Mapping[str, Any],
) -> list[dict[str, Any]]:
    ranges: list[dict[str, Any]] = []
    for range_index, group in enumerate(
        _group_contiguous(segment_ids, indexes["positions"], max_gap=2),
        start=1,
    ):
        if not group:
            continue
        first = indexes["segmentMap"][group[0]]
        last = indexes["segmentMap"][group[-1]]
        section = _owner_section(group[0], indexes)
        ranges.append(
            {
                "rangeId": f"range-{range_index:03d}",
                "startSegmentId": group[0],
                "endSegmentId": group[-1],
                "segmentIds": group,
                "sectionId": _section_id(section or {}),
                "sourceLocator": _text(
                    first.get("sourceLocator") or last.get("sourceLocator"),
                    200,
                ),
            }
        )
    return ranges


def _attach_candidate_scope(
    selection: dict[str, Any],
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    """Carry pure-range and mixed-range boundaries into the selected target."""

    return {
        **selection,
        "expandedSectionIds": list(candidate.get("expandedSectionIds") or []),
        "expandedSegmentIds": list(candidate.get("expandedSegmentIds") or []),
        "reviewSectionIds": list(candidate.get("reviewSectionIds") or []),
        "reviewSegmentIds": list(candidate.get("reviewSegmentIds") or []),
    }


# ── target ownership + contiguity repair ──────────────────────────
# Content targets (technical/qualification/commercial/evaluation/template)
# describe contiguous chapters.  Two deterministic passes clean up the two
# failure modes observed on real documents:
#   1. 唯一归属 — a segment claimed by several targets keeps only the
#      highest-priority owner (commercial pricing leaks into technical,
#      technical parameter rows leak into evaluation).
#   2. 连续性硬修复 — small (<=2 segment) runs far from a target's dominant
#      runs and carrying no mandatory-word signal are treated as noise.
# ``rejection`` (full-document regex) and ``project_info`` (scattered fields)
# are intentionally excluded.
_CONTENT_TARGET_PRIORITY: tuple[str, ...] = (
    "template",
    "qualification",
    "commercial",
    "technical",
    "evaluation",
)

_MANDATORY_SIGNAL_TERMS: tuple[str, ...] = (
    "必须",
    "须",
    "应当",
    "不得",
    "不低于",
    "不少于",
    "至少",
    "禁止",
)

_CONTIGUITY_SMALL_RUN_MAX = 2
_CONTIGUITY_ANCHOR_RATIO = 0.8
_CONTIGUITY_FAR_GAP_FLOOR = 30
_CONTIGUITY_FAR_GAP_RATIO = 0.05


def _refresh_target_scope(
    target: dict[str, Any],
    indexes: Mapping[str, Any],
) -> dict[str, Any]:
    """Recompute section/table ids, ranges and counts from segmentIds."""
    positions = indexes.get("positions", {})
    segment_map = indexes.get("segmentMap", {})
    segment_ids = sorted(
        {
            _id(value)
            for value in target.get("segmentIds") or []
            if _id(value) in segment_map
        },
        key=positions.get,
    )
    section_ids: list[str] = []
    table_ids: list[str] = []
    for segment_id in segment_ids:
        owner = _owner_section(segment_id, indexes) or {}
        owner_id = _section_id(owner)
        if owner_id and owner_id not in section_ids:
            section_ids.append(owner_id)
        table_id = _id(segment_map[segment_id].get("tableId"))
        if table_id and table_id not in table_ids:
            table_ids.append(table_id)
    target["segmentIds"] = segment_ids
    target["sectionIds"] = section_ids
    target["tableIds"] = table_ids
    target["ranges"] = _ranges_from_segments(segment_ids, indexes)
    target["selectedSegmentCount"] = len(segment_ids)
    target["selectedSectionCount"] = len(section_ids)
    target["selectedTableCount"] = len(table_ids)
    return target


def _enforce_target_ownership_and_contiguity(
    targets: list[dict[str, Any]],
    indexes: Mapping[str, Any],
) -> dict[str, int]:
    positions = indexes.get("positions", {})
    segment_map = indexes.get("segmentMap", {})
    target_by_key = {
        _id(target.get("targetModule")): target
        for target in targets
        if isinstance(target, dict) and target.get("targetModule")
    }
    content_keys = [
        key for key in _CONTENT_TARGET_PRIORITY if key in target_by_key
    ]
    summary: dict[str, int] = {"ownershipRemoved": 0, "contiguityRemoved": 0}
    if not content_keys:
        return summary

    # A selected template root is a protected source artifact.  The explicit
    # set is intentional even though template currently has the highest
    # priority: it prevents a later priority/order change from allowing a
    # technical or commercial keyword hit to claim a paragraph from the
    # template chapter.
    protected_template_ids = {
        _id(value)
        for value in (
            target_by_key.get("template", {}).get("segmentIds") or []
        )
        if _id(value) in segment_map
    }

    # Pass 1: exclusive ownership (higher priority wins)
    owned: dict[str, str] = {}
    for key in _CONTENT_TARGET_PRIORITY:
        target = target_by_key.get(key)
        if not target:
            continue
        kept: list[str] = []
        removed = 0
        for segment_id in target.get("segmentIds") or []:
            sid = _id(segment_id)
            if not sid or sid not in segment_map:
                continue
            if key != "template" and sid in protected_template_ids:
                removed += 1
                continue
            if sid in owned and owned[sid] != key:
                removed += 1
                continue
            owned.setdefault(sid, key)
            kept.append(sid)
        target["segmentIds"] = list(dict.fromkeys(kept))
        target["ownershipRemovedSegmentCount"] = removed
        summary["ownershipRemoved"] += removed
        _refresh_target_scope(target, indexes)

    # Pass 2: contiguity repair (only for targets with enough material)
    doc_size = len(positions)
    far_gap = max(_CONTIGUITY_FAR_GAP_FLOOR, int(doc_size * _CONTIGUITY_FAR_GAP_RATIO))
    for key in content_keys:
        target = target_by_key[key]
        segment_ids = target.get("segmentIds") or []
        if len(segment_ids) <= _CONTIGUITY_SMALL_RUN_MAX:
            continue
        runs = _group_contiguous(segment_ids, positions, max_gap=2)
        if len(runs) <= 1:
            continue
        run_size_by_id: dict[str, int] = {}
        for run in runs:
            for sid in run:
                run_size_by_id[sid] = len(run)

        ordered_runs = sorted(runs, key=len, reverse=True)
        total = len(segment_ids)
        anchor_ids: set[str] = set()
        acc = 0
        for run in ordered_runs:
            anchor_ids.update(run)
            acc += len(run)
            if acc >= total * _CONTIGUITY_ANCHOR_RATIO:
                break
        anchor_roots = {
            _section_id(_section_root(_owner_section(sid, indexes), indexes))
            for sid in anchor_ids
            if _section_id(_section_root(_owner_section(sid, indexes), indexes))
        }
        anchor_positions = [
            positions[sid] for sid in anchor_ids if sid in positions
        ]
        if not anchor_positions:
            continue
        anchor_min = min(anchor_positions)
        anchor_max = max(anchor_positions)

        kept_ids: list[str] = []
        removed_ids: list[str] = []
        for sid in segment_ids:
            pos = positions.get(sid)
            if sid in anchor_ids or pos is None:
                kept_ids.append(sid)
                continue
            if run_size_by_id.get(sid, 1) > _CONTIGUITY_SMALL_RUN_MAX:
                kept_ids.append(sid)
                continue
            segment = segment_map.get(sid) or {}
            text = _compact(segment.get("text"))
            if any(term in text for term in _MANDATORY_SIGNAL_TERMS):
                kept_ids.append(sid)
                continue
            seg_root = _section_id(
                _section_root(_owner_section(sid, indexes), indexes)
            )
            section_far = bool(seg_root and anchor_roots and seg_root not in anchor_roots)
            position_far = pos < anchor_min - far_gap or pos > anchor_max + far_gap
            if section_far or position_far:
                removed_ids.append(sid)
            else:
                kept_ids.append(sid)

        if removed_ids:
            target["segmentIds"] = kept_ids
            target["llmExcludedSegmentIds"] = list(
                dict.fromkeys(
                    [
                        *(target.get("llmExcludedSegmentIds") or []),
                        *removed_ids,
                    ]
                )
            )
            target["contiguityRemovedSegmentCount"] = len(removed_ids)
            summary["contiguityRemoved"] += len(removed_ids)
            _refresh_target_scope(target, indexes)

    return summary


def discover_target_ranges(
    *,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
    ai: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Recall and validate source ranges for the eight business targets."""

    segment_list = [item for item in (segments or []) if isinstance(item, dict)]
    section_list = [item for item in (sections or []) if isinstance(item, dict)]
    table_list = [item for item in (tables or []) if isinstance(item, dict)]
    indexes = _build_indexes(segment_list, section_list, table_list)
    rule_candidates = {
        key: _rule_candidates(key, indexes)
        for key in TARGET_KEYS
    }
    diagnostics: dict[str, Any] = {
        "schemaVersion": TARGET_MAP_SCHEMA_VERSION,
        "segmentCount": len(indexes["segments"]),
        "sectionCount": len(indexes["sectionMap"]),
        "tableCount": len(indexes["tableMap"]),
        "llmCalled": False,
        "llmSuccess": False,
    }
    preliminary_targets: list[dict[str, Any]] = []
    preliminary_by_key: dict[str, dict[str, Any]] = {}
    for meta in TARGET_MODULES:
        key = meta["key"]
        candidate = rule_candidates.get(key, {})
        if key == "rejection":
            selected = _attach_candidate_scope(
                {
                    "targetModule": key,
                    "sectionIds": list(
                        candidate.get("selectedSectionIds") or []
                    ),
                    "segmentIds": list(candidate.get("segmentIds") or []),
                    "tableIds": list(candidate.get("tableIds") or []),
                    "confidence": candidate.get("confidence", 0.8),
                    "reason": "全文正则扫描",
                    "selectionSource": "regex",
                },
                candidate,
            )
        elif key == "project_info":
            selected = _attach_candidate_scope(
                {
                    "targetModule": key,
                    "sectionIds": [],
                    "segmentIds": list(candidate.get("segmentIds") or []),
                    "tableIds": list(candidate.get("tableIds") or []),
                    "confidence": candidate.get("confidence", 0.65),
                    "reason": "全文抽取",
                },
                candidate,
            )
        else:
            rule_selected = _validate_selection(key, None, candidate, indexes)
            selected = _attach_candidate_scope(rule_selected, candidate)
            selected["selectionSource"] = "rule"
        preliminary_by_key[key] = selected
        preliminary_targets.append({**_module(key), **selected})

    review_map = (
        _llm_review_ranges(
            preliminary_by_key,
            indexes,
            ai or {},
            diagnostics,
        )
        if _llm_ready(ai)
        else {}
    )
    diagnostics["llmCalled"] = bool(diagnostics.get("llmReviewCalled"))
    diagnostics["llmSuccess"] = bool(diagnostics.get("llmReviewSuccess"))

    targets: list[dict[str, Any]] = []
    for target in preliminary_targets:
        key = str(target.get("targetModule") or "")
        if key != "project_info" and key in review_map:
            reviewed = _apply_review(target, review_map.get(key), indexes)
            base_source = str(reviewed.get("selectionSource") or "rule")
            reviewed["selectionSource"] = (
                base_source
                if base_source.endswith("+review")
                else f"{base_source}+review"
            )
            target = reviewed
        target["ranges"] = _ranges_from_segments(target.get("segmentIds") or [], indexes)
        target["candidateSections"] = (
            rule_candidates.get(key, {}).get("candidateSections") or []
        )
        target["candidateSegmentCount"] = len(
            rule_candidates.get(key, {}).get("candidateSegments") or []
        )
        target["selectedSegmentCount"] = len(target.get("segmentIds") or [])
        target["selectedSectionCount"] = len(target.get("sectionIds") or [])
        target["selectedTableCount"] = len(target.get("tableIds") or [])
        targets.append(target)

    repair_summary = _enforce_target_ownership_and_contiguity(targets, indexes)
    diagnostics["ownershipAndContiguity"] = repair_summary
    diagnostics["targetCounts"] = {
        item["targetModule"]: item["selectedSegmentCount"]
        for item in targets
    }
    return {
        "schemaVersion": TARGET_MAP_SCHEMA_VERSION,
        "targets": targets,
        "indexes": indexes,
        "diagnostics": diagnostics,
    }


def rejection_terms() -> tuple[str, ...]:
    return _REJECTION_TERMS


def project_field_rules() -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    return _PROJECT_FIELD_RULES


__all__ = [
    "ENTERPRISE_MATCH_TARGETS",
    "MATCH_STAGE_TARGETS",
    "RISK_CHECK_TARGETS",
    "TARGET_KEYS",
    "TARGET_MAP_SCHEMA_VERSION",
    "TARGET_META",
    "TARGET_MODULES",
    "discover_target_ranges",
    "project_field_rules",
    "rejection_terms",
]
