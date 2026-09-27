# -*- coding: utf-8 -*-
"""从招标文件「投标文件格式/响应文件组成」章节抽取响应目录并做确定性分类。

输入 document.sections（document_structure.build_section_tree 产物，每项含
sectionType / heading / text / startLocator）。若招标文件给出了明确的响应文件
目录/格式，则该目录是标书章节规划的权威来源；否则上层回退到固定模板。

职责边界：
  - LLM 只负责「抽取目录条目」，不分类、不编条目；
  - 分类是纯规则（确定性、可测试）。
"""
from __future__ import annotations

import json
import re
from typing import Any

DOCUMENT_FORMAT_TYPE = "document_format"

# 标题命中这些词的章节也视为"投标文件格式"类（即便 sectionType 未命中）
_FORMAT_TITLE_TERMS = (
    "投标文件格式", "响应文件格式", "投标文件组成", "响应文件组成",
    "投标文件编制", "响应文件编制", "文件编制要求", "投标文件目录",
    "响应文件目录", "投标文件的构成", "响应文件的构成",
)

# 格式模板子章节名：这些是投标人要提交的"文书/表格"名称。文档结构分类器（document_structure）
# 目前只把"投标文件格式/响应文件格式"这类**章标题**判成 document_format，而章内的
# 模板子章节（投标函/授权书/响应表/一览表…）常被误判成 other/commercial/technical，
# 导致响应目录抽取漏项。这里按标题关键词兜底收集，与 sectionType 无关。
_FORMAT_ITEM_TERMS = (
    "投标函", "响应函", "投标书", "响应书",
    "承诺书", "承诺函",
    "授权委托书", "授权书", "委托书", "单位授权书",
    "法定代表人身份证明", "法定代表人资格证明",
    "投标保证金", "保证金",
    "资格及资信", "资信证明",
    "响应表", "一览表", "明细表", "报价表", "报价单",
    "偏离表", "封面格式", "索引",
)

# title 关键词 -> (sectionId, source, kind)。顺序敏感：先特后泛。
_CLASSIFY_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    # 偏离表（须最先，避免被技术/商务泛词误吃）
    (("技术偏离", "技术规格偏离", "技术条款偏离"), "d-01", "deviation", "deviation"),
    (("商务偏离", "商务条款偏离"), "d-02", "deviation", "deviation"),
    (("偏离表", "偏离说明"), "d-02", "deviation", "deviation"),
    # 报价 / 开标一览
    (("开标一览", "报价明细", "分项报价", "报价表", "报价单"), "p-01", "template", "commercial"),
    (("报价", "开标"), "p-01", "template", "commercial"),
    # 技术叙述（AI）——须先于商务模板泛词（如"承诺"），否则"售后服务承诺"会被误判成承诺书
    (("项目理解", "总体理解", "需求理解", "方案概述"), "t-01", "ai", "technical"),
    (("技术方案", "技术响应", "技术规格响应", "技术参数响应", "技术需求响应"), "t-02", "ai", "technical"),
    (("实施方案", "服务方案", "组织实施方案", "项目实施方案", "实施计划"), "t-03", "ai", "technical"),
    (("质量保证", "质量保障", "质保", "质量管理", "质量控制"), "t-04", "ai", "technical"),
    (("售后", "服务承诺", "售后服务", "维护服务", "运维"), "t-05", "ai", "technical"),
    (("培训",), "t-06", "ai", "technical"),
    (("应急", "预案", "保障方案"), "t-07", "ai", "technical"),
    # 固定文书（商务模板）
    (("投标函", "响应函", "投标书", "响应书"), "c-01", "template", "commercial"),
    (("承诺书", "承诺函", "承诺"), "c-02", "template", "commercial"),
    (("法定代表人身份证明", "法人身份证明", "法定代表人资格证明"), "c-03", "template", "commercial"),
    (("授权委托书", "授权书", "委托书"), "c-04", "template", "commercial"),
    (("保证金",), "c-05", "template", "commercial"),
    (("声明",), "c-06", "template", "commercial"),
    (("企业简介", "公司简介", "基本情况", "投标人情况"), "c-07", "template", "commercial"),
    # 事实表格
    (("业绩", "类似项目", "案例", "中标"), "c-09", "table", "commercial"),
    (("人员", "团队", "负责人", "项目组", "拟投入"), "c-10", "table", "commercial"),
    (("资格", "资质", "财务", "审计", "纳税", "社保", "信用"), "c-08", "table", "commercial"),
)

_DEFAULT_KIND = ("a-01", "attachment", "attachment")


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _find_format_sections(sections: list[Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        if section.get("sectionType") == DOCUMENT_FORMAT_TYPE:
            found.append(section)
            continue
        title = _norm(str(section.get("heading") or ""))
        if any(_norm(term) in title for term in _FORMAT_TITLE_TERMS):
            found.append(section)
            continue
        if any(_norm(term) in title for term in _FORMAT_ITEM_TERMS):
            found.append(section)
    return found


def _classify_title(title: str) -> tuple[str, str, str]:
    value = _norm(title)
    for keywords, section_id, source, kind in _CLASSIFY_RULES:
        if any(_norm(kw) in value for kw in keywords):
            return section_id, source, kind
    return _DEFAULT_KIND


def extract_response_outline(sections: list[Any], ai: dict[str, Any]) -> dict[str, Any] | None:
    """抽取响应目录。

    返回 {"items": [{"sectionId","title","source","kind"}], "sourceDetail": str}；
    无格式章节或抽取失败返回 None（上层回退固定模板）。
    """
    format_sections = _find_format_sections(sections)
    if not format_sections:
        return None
    source_detail = " / ".join(
        str(s.get("heading") or s.get("startLocator") or "投标文件格式") for s in format_sections[:3]
    )
    raw_text = "\n\n".join(str(s.get("text") or "") for s in format_sections).strip()
    if not raw_text:
        return None

    items: list[dict[str, Any]] = []
    if ai.get("enabled") is not False and ai.get("baseUrl"):
        items = _extract_via_llm(raw_text, ai)
    if not items:
        items = _extract_via_rules(raw_text)
    if not items:
        return None

    outline_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        section_id, source, kind = _classify_title(title)
        if section_id in seen:
            continue
        seen.add(section_id)
        outline_items.append({"sectionId": section_id, "title": title, "source": source, "kind": kind})

    return {"items": outline_items, "sourceDetail": source_detail}


def _extract_via_llm(raw_text: str, ai: dict[str, Any]) -> list[dict[str, Any]]:
    from ..tender_matching.eligibility import call_openai_compatible

    system = (
        "你是招标文件解析助手。请从「投标文件格式/响应文件组成」内容中，"
        "抽取投标人需要提交的响应文件目录条目（章节标题）。"
        "只抽取文书/表格类标题，例如：投标函、承诺书、授权委托书、"
        "法定代表人身份证明、资格及资信证明文件、投标保证金、开标一览表、"
        "报价明细表、标的说明一览表、技术和服务要求响应表、商务条件响应表、"
        "偏离表、封面、索引等。不要抽取正文句子、编制说明条款或签署盖章规则。"
        "只抽取原文明确列出的条目，不补充、不合并、不编造。"
        '输出 JSON 数组，每项形如 {"title":"章节标题"}。'
    )
    user = f"「投标文件格式/组成」原文：\n\n{raw_text[:20000]}"
    result = call_openai_compatible(
        ai,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=1200,
        temperature=0,
    )
    if not result.get("success"):
        return []
    return _parse_items(str(result.get("content") or ""))


def _parse_items(content: str) -> list[dict[str, Any]]:
    content = (content or "").strip()
    if not content:
        return []
    for candidate in _json_candidates(content):
        try:
            parsed = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        items: list[dict[str, Any]] = []
        if isinstance(parsed, list):
            for entry in parsed:
                if isinstance(entry, dict) and entry.get("title"):
                    items.append({"title": str(entry["title"]).strip()})
                elif isinstance(entry, str) and entry.strip():
                    items.append({"title": entry.strip()})
        elif isinstance(parsed, dict) and parsed.get("title"):
            items.append({"title": str(parsed["title"]).strip()})
        if items:
            return items
    # LLM 可能返回非 JSON 的逐行文本，兜底按行解析
    return _extract_via_rules(content)


def _json_candidates(content: str):
    yield content
    start = content.find("[")
    end = content.rfind("]")
    if start != -1 and end != -1 and end > start:
        yield content[start:end + 1]
    start = content.find("{")
    end = content.rfind("}")
    if start != -1 and end != -1 and end > start:
        yield content[start:end + 1]


def _extract_via_rules(raw_text: str) -> list[dict[str, Any]]:
    """规则兜底：抓「序号/编号 + 短标题」行。"""
    items: list[dict[str, Any]] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line or len(line) > 60:
            continue
        m = re.match(
            r"^\s*(?:第[一二三四五六七八九十\d]+[章篇部分节]|[\(\[]?\d+[\)\]、.．]\s*|[\(\[]?[一二三四五六七八九十]+[\)\]、.．]\s*|[-—–*·•]\s*)+(.+)$",
            line,
        )
        if not m:
            continue
        title = m.group(1).strip()
        title = re.sub(r"[（(].*[）)]", "", title)
        title = title.strip("：:、.． ")
        if 2 <= len(title) <= 30:
            items.append({"title": title})
    return items
