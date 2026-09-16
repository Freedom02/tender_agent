# -*- coding: utf-8 -*-
"""标书撰写模块 —— 智能校对与风险自查（纯函数、确定性、无 LLM）。

职责：对标书文档（bid_document）做五类检查，产出 Markdown 报告与风险项列表：
  1) 漏响应检测      —— fineReview.rows 的每条 requirementId 是否都进入两张偏离表；
  2) 废标风险        —— 直接读取 fineReview 的 hardBlockers / rejectionRisks / unresolvedMandatory；
  3) 负偏离/待澄清   —— 偏离表内 deviation ∈ {负偏离, 待人工确认} 的行；
  4) 关键字段一致性  —— project 的项目名/编号、企业名缺失或占位，及正文项目名不一致；
  5) 敏感词          —— 正文命中禁用/敏感小词表。

硬性约束：只读入参，不调用 LLM，不修改任何已有文件；复用 schemas.py 的
偏离类型与偏离表分组键，复用 fineReview 已有的废标/否决/未解决强制项，
不新造数据。riskItems 的 level 取值 ∈ {高, 中, 低}。
"""
from __future__ import annotations

from typing import Any

from .schemas import (
    DEVIATION_NEGATIVE,
    DEVIATION_PENDING,
    DEVIATION_TABLE_COMMERCIAL,
    DEVIATION_TABLE_TECHNICAL,
)

# 敏感/禁用表述小词表（命中记为提示级「低」）
SENSITIVE_TERMS: tuple[str, ...] = (
    "最",
    "第一",
    "唯一",
    "绝对",
    "保证中标",
)

# 关键字段占位/缺失标记（命中记为「关键信息缺失」）
PLACEHOLDER_MARKERS: tuple[str, ...] = (
    "【待填写",
    "待填写",
    "待补充",
    "TODO",
    "TBD",
)

# riskItems 的 level 取值约束
RISK_LEVELS = ("高", "中", "低")


# ---------------------------------------------------------------------------
# 纯读工具
# ---------------------------------------------------------------------------
def _list(value: Any) -> list[dict[str, Any]]:
    """把任意值规约为 dict 列表（丢弃非 dict 元素）。"""
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value: Any) -> str:
    """规约文本：None → ''，其余转字符串并去首尾空白。"""
    if value is None:
        return ""
    return str(value).strip()


def _is_placeholder(text: str) -> bool:
    """关键字段是否缺失或含占位符。"""
    if not text:
        return True
    return any(marker in text for marker in PLACEHOLDER_MARKERS)


def _row_id(row: dict[str, Any]) -> str:
    """comparison row / 偏离表行的 requirementId（兼容 id 字段）。"""
    return _text(row.get("requirementId") or row.get("id"))


def _fine_rows(fine_review: dict[str, Any]) -> list[dict[str, Any]]:
    """fineReview.rows 规约为 dict 列表。"""
    if not isinstance(fine_review, dict):
        return []
    return _list(fine_review.get("rows"))


def _sections(bid_document: dict[str, Any]) -> list[dict[str, Any]]:
    """bid_document.plan 里的章节列表。"""
    if not isinstance(bid_document, dict):
        return []
    return _list(bid_document.get("plan"))


def _deviation_rows(bid_document: dict[str, Any]) -> list[dict[str, Any]]:
    """两张偏离表（technical + commercial）合并后的行列表。"""
    if not isinstance(bid_document, dict):
        return []
    tables = bid_document.get("deviationTables")
    if not isinstance(tables, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in (DEVIATION_TABLE_TECHNICAL, DEVIATION_TABLE_COMMERCIAL):
        rows.extend(_list(tables.get(key)))
    return rows


def _project_field(project: dict[str, Any], key: str) -> str:
    """读取项目字段：优先 project.parsed.{key}，其次 project.{key}。"""
    if not isinstance(project, dict):
        return ""
    parsed = project.get("parsed")
    if isinstance(parsed, dict):
        value = _text(parsed.get(key))
        if value:
            return value
    return _text(project.get(key))


def _company_name(company: dict[str, Any] | None) -> str:
    """企业名：company['company']。"""
    if not isinstance(company, dict):
        return ""
    return _text(company.get("company"))


# ---------------------------------------------------------------------------
# 五类检查
# ---------------------------------------------------------------------------
def _check_missed_responses(
    bid_document: dict[str, Any],
    fine_review: dict[str, Any],
    add,
) -> None:
    """1) 漏响应检测：rows 的每条 requirementId 是否都出现在两张偏离表中。"""
    covered_ids = {_row_id(row) for row in _deviation_rows(bid_document) if _row_id(row)}
    for row in _fine_rows(fine_review):
        rid = _row_id(row)
        if not rid or rid in covered_ids:
            continue
        requirement = _text(row.get("requirement") or row.get("rawText"))
        add(
            "漏响应",
            "高",
            "漏响应",
            f"[{rid}] {requirement or '（无要求文本）'}：未出现在技术/商务偏离表，"
            f"存在漏响应风险，请补齐响应或确认是否豁免。",
        )


def _check_rejection(fine_review: dict[str, Any], add) -> None:
    """2) 废标风险：直接读取 hardBlockers / rejectionRisks / unresolvedMandatory。"""
    if not isinstance(fine_review, dict):
        return
    for key, label in (
        ("hardBlockers", "硬性否决"),
        ("rejectionRisks", "否决风险"),
        ("unresolvedMandatory", "未解决强制项"),
    ):
        for entry in _list(fine_review.get(key)):
            rid = _text(entry.get("requirementId") or entry.get("id"))
            detail = _text(entry.get("detail")) or _text(entry.get("title"))
            prefix = f"[{rid}] " if rid else ""
            add(
                "废标风险",
                "高",
                f"废标风险（{label}）",
                f"{prefix}{detail or '（无说明）'}",
            )


def _check_deviations(bid_document: dict[str, Any], add) -> None:
    """3) 负偏离/待澄清未处理：偏离表中 deviation ∈ {负偏离, 待人工确认} 的行。"""
    for row in _deviation_rows(bid_document):
        deviation = _text(row.get("deviation"))
        if deviation not in (DEVIATION_NEGATIVE, DEVIATION_PENDING):
            continue
        level = "高" if deviation == DEVIATION_NEGATIVE else "中"
        title = "负偏离未处理" if deviation == DEVIATION_NEGATIVE else "待人工确认未处理"
        rid = _row_id(row)
        requirement = _text(row.get("requirement"))
        suggestion = _text(row.get("suggestion"))
        bits = [f"[{rid}]" if rid else "", f"偏离类型「{deviation}」"]
        if requirement:
            bits.append(f"要求「{requirement}」")
        if suggestion:
            bits.append(f"建议「{suggestion}」")
        detail = " ".join(bit for bit in bits if bit)
        add(level, title, detail + "，请在投标前处理或确认。", count_key="负偏离")


def _check_key_fields(
    bid_document: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any] | None,
    add,
) -> None:
    """4) 关键字段一致性：项目名/编号、企业名缺失占位，及正文项目名不一致。"""
    project_name = _project_field(project, "projectName")
    project_code = _project_field(project, "projectCode")
    company_name = _company_name(company)

    if _is_placeholder(project_name):
        add(
            "关键信息",
            "高",
            "关键信息缺失",
            "项目名称（projectName）缺失或含占位符，请补充招标项目名称。",
        )
    if _is_placeholder(project_code):
        add(
            "关键信息",
            "高",
            "关键信息缺失",
            "项目编号（projectCode）缺失或含占位符，请补充招标项目编号。",
        )
    if _is_placeholder(company_name):
        add(
            "关键信息",
            "高",
            "关键信息缺失",
            "企业名称（company.company）缺失或含占位符，请补充投标主体名称。",
        )

    # 项目名一致性：projectName 非空时，检查正文是否出现该名称（简单子串检查）。
    if project_name and not _is_placeholder(project_name):
        combined = "\n".join(_text(section.get("content")) for section in _sections(bid_document))
        if project_name not in combined:
            add(
                "关键信息",
                "中",
                "项目名不一致",
                f"正文各章节未检索到招标项目名「{project_name}」（简单子串检查），"
                f"请核对是否使用了与招标文件不一致的项目名称。",
            )


def _check_sensitive_words(bid_document: dict[str, Any], add) -> None:
    """5) 敏感词：扫描各 section.content 中的禁用/敏感表述。"""
    hits: dict[str, list[str]] = {}
    for section in _sections(bid_document):
        content = _text(section.get("content"))
        if not content:
            continue
        section_name = _text(section.get("sectionId")) or _text(section.get("title")) or "未命名章节"
        for term in SENSITIVE_TERMS:
            if term in content and section_name not in hits.setdefault(term, []):
                hits[term].append(section_name)
    for term, section_names in hits.items():
        add(
            "敏感词",
            "低",
            "敏感词",
            f"正文命中敏感/禁用表述「{term}」（章节：{'、'.join(section_names)}），"
            f"请斟酌表述，避免绝对化或夸大承诺。",
        )


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def proofread(
    bid_document: dict[str, Any],
    fine_review: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """对标书文档做智能校对与风险自查，返回 {"report": Markdown, "riskItems": [...]}。

    只读入参，不调用 LLM，不修改已有文件。riskItems 元素形如
    {"level": "高|中|低", "title": str, "detail": str}。
    """
    bid_document = bid_document if isinstance(bid_document, dict) else {}
    fine_review = fine_review if isinstance(fine_review, dict) else {}
    project = project if isinstance(project, dict) else {}

    risk_items: list[dict[str, str]] = []
    counts: dict[str, int] = {"漏响应": 0, "废标风险": 0, "负偏离": 0, "敏感词": 0, "关键信息": 0}

    def add(level: str, title: str, detail: str, count_key: str | None = None) -> None:
        """追加一条风险项并累加对应计数。"""
        if level not in RISK_LEVELS:
            level = "中"
        risk_items.append({"level": level, "title": title, "detail": detail})
        bucket = count_key if count_key in counts else title
        counts[bucket] = counts.get(bucket, 0) + 1

    _check_missed_responses(bid_document, fine_review, add)
    _check_rejection(fine_review, add)
    _check_deviations(bid_document, add)
    _check_key_fields(bid_document, project, company, add)
    _check_sensitive_words(bid_document, add)

    lines = [
        "# 标书校对与风险自查报告",
        "",
        "## 汇总",
        f"- 风险总条数：**{len(risk_items)}**",
        f"- 漏响应：**{counts['漏响应']}**",
        f"- 废标项：**{counts['废标风险']}**",
        f"- 负偏离/待澄清：**{counts['负偏离']}**",
        f"- 敏感词：**{counts['敏感词']}**",
        "",
        "## 风险明细",
    ]
    if not risk_items:
        lines.append("未发现风险项。")
    else:
        for index, item in enumerate(risk_items, start=1):
            lines.append(f"{index}. **[{item['level']}] {item['title']}**：{item['detail']}")

    return {"report": "\n".join(lines), "riskItems": risk_items}
