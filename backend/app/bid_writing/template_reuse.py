# -*- coding: utf-8 -*-
"""模板复用：招标文件给了完整格式文书的，直接复制原文并填空。

适用章节（source 升级为 copied_template）：投标函/承诺书/身份证明/授权委托书/
保证金说明/声明/企业简介/开标一览表 等格式类文书。

只复制招标文件原文，绝不编造；空位用 organization + project.parsed 结构化字段
替换，替换不到的保留占位并记入 blanksToFill，提醒人工填写。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

# sectionId -> 在招标文件里找"格式"章节时用的标题关键词
_TEMPLATE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "c-01": ("投标函", "响应函", "投标书", "响应书"),
    "c-02": ("承诺书", "承诺函", "承诺"),
    "c-03": ("法定代表人身份证明", "法人身份证明", "身份证明"),
    "c-04": ("授权委托书", "授权书", "委托书"),
    "c-05": ("保证金",),
    "c-06": ("声明",),
    "c-07": ("企业简介", "公司简介", "基本情况"),
    "p-01": ("开标一览", "报价明细", "分项报价", "报价表", "报价单"),
}

# 填空字段：label -> context 键（按 label 长度降序替换，避免前缀误吃）
_FILL_FIELDS: tuple[tuple[str, str], ...] = (
    ("统一社会信用代码", "creditCode"),
    ("法定代表人", "legalRepresentative"),
    ("法定代表", "legalRepresentative"),
    ("采购人名称", "tendererName"),
    ("招标人名称", "tendererName"),
    ("投标人名称", "companyName"),
    ("供应商名称", "companyName"),
    ("项目名称", "projectName"),
    ("项目编号", "projectCode"),
    ("注册地址", "registeredAddress"),
    ("采购人", "tendererName"),
    ("招标人", "tendererName"),
    ("投标人", "companyName"),
    ("供应商", "companyName"),
    ("注册地", "registeredAddress"),
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _build_context(project: dict, organization: dict | None, company: dict | None) -> dict[str, str]:
    org = organization if isinstance(organization, dict) else {}
    profile = company if isinstance(company, dict) else {}
    project = project if isinstance(project, dict) else {}
    parsed = project.get("parsed") if isinstance(project.get("parsed"), dict) else {}

    def pick(*vals: Any) -> str:
        for v in vals:
            if isinstance(v, (list, tuple)):
                v = "、".join(str(x).strip() for x in v if str(x).strip())
            if v not in (None, ""):
                return str(v).strip()
        return ""

    return {
        "projectName": pick(parsed.get("projectName"), project.get("title")),
        "projectCode": pick(parsed.get("projectCode")),
        "tendererName": pick(parsed.get("tendererName")),
        "companyName": pick(org.get("organizationName"), profile.get("company")),
        "legalRepresentative": pick(org.get("legalRepresentative"), profile.get("legalRepresentative")),
        "creditCode": pick(org.get("unifiedSocialCreditCode"), profile.get("unifiedSocialCreditCode")),
        "registeredAddress": pick(org.get("registeredAddress"), profile.get("registeredAddress")),
    }


def find_template_section(sections: list[Any], section_id: str) -> dict[str, Any] | None:
    """在招标文件章节里找给定章节的"格式"模板（标题命中关键词 + 含格式/附件/模板/范本）。"""
    keywords = _TEMPLATE_KEYWORDS.get(section_id, ())
    if not keywords:
        return None
    candidates: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        sec_title = _norm(str(section.get("title") or ""))
        if not sec_title:
            continue
        if not any(_norm(kw) in sec_title for kw in keywords):
            continue
        if any(term in sec_title for term in ("格式", "附件", "模板", "附表", "范本", "样式")):
            candidates.append(section)
    if not candidates:
        # 放宽：命中关键词即可（模板可能直接内嵌在章节里）
        for section in sections:
            if not isinstance(section, dict):
                continue
            sec_title = _norm(str(section.get("title") or ""))
            if any(_norm(kw) in sec_title for kw in keywords):
                candidates.append(section)
                break
    return max(candidates, key=lambda s: len(str(s.get("text") or ""))) if candidates else None


def attach_templates(plan: list[Any], sections: list[Any]) -> list[Any]:
    """给 plan 里"招标文件已提供完整格式"的章节挂模板文本，source 升级为 copied_template。"""
    for section in plan:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("sectionId") or "")
        if section_id not in _TEMPLATE_KEYWORDS:
            continue
        template_section = find_template_section(sections, section_id)
        if not template_section:
            continue
        text = str(template_section.get("text") or "").strip()
        if not text:
            continue
        section["source"] = "copied_template"
        section["templateOrigin"] = str(
            template_section.get("sourceLocator") or template_section.get("title") or ""
        )
        section["templateText"] = text
    return plan


def fill_section_template(section: dict, project: dict, organization: dict | None, company: dict | None) -> dict:
    text = str(section.get("templateText") or "").strip()
    if not text:
        return {"content": "", "blanksToFill": ["模板原文缺失"], "status": "missing"}
    filled = fill_template_text(text, project, organization, company)
    return {"content": filled["content"], "blanksToFill": filled["blanksToFill"], "status": "generated"}


def fill_template_text(text: str, project: dict, organization: dict | None, company: dict | None) -> dict:
    ctx = _build_context(project, organization, company)
    content = text

    for label, ctx_key in sorted(_FILL_FIELDS, key=lambda p: -len(p[0])):
        value = ctx.get(ctx_key)
        if not value:
            continue
        # （label）/ (label)
        content = re.sub(rf"[（(]\s*{re.escape(label)}\s*[）)]", f"（{value}）", content)
        # label：____ 或 label：　　 （冒号后仅空白/下划线）
        content = re.sub(rf"{re.escape(label)}\s*[:：]\s*[_＿\u3000 ]+", f"{label}：{value}", content)
        # label____ / label＿＿
        content = re.sub(rf"{re.escape(label)}\s*[_＿]{{1,}}", f"{label}{value}", content)

    # 落款日期：____年__月__日 -> 今天
    today = date.today()
    content = re.sub(
        r"[_＿]{1,}\s*年\s*[_＿]{1,}\s*月\s*[_＿]{1,}\s*日",
        f"{today.year}年{today.month}月{today.day}日",
        content,
    )

    return {"content": content, "blanksToFill": _detect_blanks(content)}


def _detect_blanks(content: str) -> list[str]:
    blanks: list[str] = []
    if re.search(r"[_＿]{2,}", content):
        blanks.append("下划线空位")
    if "【待填写" in content:
        blanks.append("【待填写】字段")
    for label in ("报价", "金额", "大写", "开户银行", "银行账号", "被授权人", "签字", "盖章"):
        if label in content and label not in blanks:
            blanks.append(label)
    return blanks
