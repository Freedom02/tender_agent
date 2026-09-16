# -*- coding: utf-8 -*-
"""暗标身份信息屏蔽（纯文本层面 + 明确告警）。

本模块只做「文本层面的身份词替换 + 告警提示」，不声称能清除图片标识：
证据图片内的 LOGO / 水印 / 印章无法可靠抹除，必须人工复核。

硬性约束：
  - 纯函数，不依赖 FastAPI，不修改任何既有文件；
  - 深拷贝产出暗标版 bid_document，绝不改动原始对象；
  - 只做文本替换，覆盖正文 / 标题 / 页眉页脚 / 图注等占位文本与 sourceRefs.name。

复用契约见 schemas.py（唯一对齐基准）；输入 company / organization 来自
document_recognition/storage.py 的 build_profile 产物。
"""
from __future__ import annotations

import copy
from typing import Any

from .schemas import BID_MODE_SECRET

# ---------------------------------------------------------------------------
# 可调参数（调用方可扩展）
# ---------------------------------------------------------------------------
# 通用身份词：企业简称 / 别称无法穷举，这里只给基础集合，调用方可自行追加
# （例如 company 画像外的历史曾用名、品牌名等）。
GENERIC_IDENTITY_TERMS: list[str] = [
    "本公司",
    "我公司",
    "我司",
    "本单位",
    "我方",
]

# 命中身份词后的占位符（如需「某公司 / 某人员」等语义占位，可替换此常量）。
ANONYMIZE_PLACEHOLDER: str = "***"

# 暗标处理边界告警（追加进 proofread 并新增字段，供校对模块 / 前端展示）。
ANONYMIZE_NOTICE: str = (
    "暗标处理仅辅助筛查，图片内 LOGO 无法自动抹除，务必人工复核"
)

# 章节内需要做身份词替换的文本字段（页眉/页脚/图注等占位文本一并处理）。
_SECTION_TEXT_KEYS: tuple[str, ...] = (
    "content",
    "title",
    "header",
    "footer",
    "caption",
    "pageHeader",
    "pageFooter",
    "figureCaption",
    "figureNote",
)


# ---------------------------------------------------------------------------
# 身份词表构造
# ---------------------------------------------------------------------------
def build_identity_terms(
    company: dict[str, Any],
    organization: dict[str, Any] | None = None,
) -> list[str]:
    """从企业画像与组织信息提取「去重后」的身份词表。

    身份词来源：
      - company["company"]                  企业名
      - company["qualifications"][*]        资质名
      - company["performances"][*]["name"]  业绩案例名
      - company["personnel"][*]             人员 name（缺省回退 role）
      - organization["legalRepresentative"] 法定代表人
    并追加 GENERIC_IDENTITY_TERMS 通用身份词（企业简称无法穷举，调用方可扩展）。

    返回值为普通 list[str]，调用方可直接 `terms + [...]` 追加自定义词。
    """
    terms: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip()
        # 过滤空值与单字词，避免误伤（单字替换会波及大量无关文本）。
        if len(text) >= 2 and text not in terms:
            terms.append(text)

    company = company or {}
    add(company.get("company"))

    for qual in company.get("qualifications") or []:
        add(qual)

    for perf in company.get("performances") or []:
        if isinstance(perf, dict):
            add(perf.get("name"))

    for person in company.get("personnel") or []:
        if not isinstance(person, dict):
            continue
        # build_profile 的 personnel 仅存 role（内部已回退 name）；此处兼容
        # 直接传入含 name 的原始结构：优先 name，其次 role。
        add(person.get("name"))
        add(person.get("role"))

    if organization:
        add(organization.get("legalRepresentative"))

    for term in GENERIC_IDENTITY_TERMS:
        add(term)

    return terms


# ---------------------------------------------------------------------------
# 单段文本替换（供外部复用）
# ---------------------------------------------------------------------------
def anonymize_text(text: str, terms: list[str]) -> str:
    """对单段文本做身份词替换（供外部复用）。

    按词长降序替换，避免「简称」先命中后破坏「全称」
    （例如简称先被替换，导致全称残留半截）。命中即替换为占位符。
    """
    if not text or not terms:
        return text

    result = text
    for term in sorted({t for t in terms if t}, key=len, reverse=True):
        result = result.replace(term, ANONYMIZE_PLACEHOLDER)
    return result


# ---------------------------------------------------------------------------
# 文档级屏蔽
# ---------------------------------------------------------------------------
def anonymize_document(
    bid_document: dict[str, Any],
    company: dict[str, Any],
    organization: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """返回暗标版 bid_document（深拷贝，mode 改为 secret，不改原对象）。

    处理范围：
      - 遍历 plan 每个 section 的正文 / 标题 / 页眉页脚 / 图注等占位文本，
        把命中的身份词替换为占位符；
      - sourceRefs[*]["name"] 含身份词时一并替换；
      - 在 proofread.riskItems 追加、并新增顶层 anonymizeNotice 字段提示：
        「暗标处理仅辅助筛查，图片内 LOGO 无法自动抹除，务必人工复核」。

    边界：仅做文本屏蔽，不处理证据图片内的 LOGO / 水印 / 印章。
    """
    doc = copy.deepcopy(bid_document)
    terms = build_identity_terms(company, organization)

    doc["mode"] = BID_MODE_SECRET

    # 遍历章节：正文 + 页眉页脚/图注类占位文本
    for section in doc.get("plan") or []:
        if not isinstance(section, dict):
            continue
        for key in _SECTION_TEXT_KEYS:
            value = section.get(key)
            if isinstance(value, str):
                section[key] = anonymize_text(value, terms)
        # 溯源引用的 name（通常为命中对象的名称，可能含企业/资质/业绩名）
        for ref in section.get("sourceRefs") or []:
            if isinstance(ref, dict) and isinstance(ref.get("name"), str):
                ref["name"] = anonymize_text(ref["name"], terms)

    # 追加暗标处理边界提示：proofread.riskItems + 新增字段
    proofread = doc.setdefault("proofread", {})
    risk_items = proofread.setdefault("riskItems", [])
    if not isinstance(risk_items, list):
        risk_items = []
        proofread["riskItems"] = risk_items
    risk_items.append({
        "level": "warning",
        "type": "anonymize",
        "message": ANONYMIZE_NOTICE,
    })
    doc["anonymizeNotice"] = ANONYMIZE_NOTICE

    return doc