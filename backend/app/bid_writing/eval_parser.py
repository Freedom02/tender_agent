# -*- coding: utf-8 -*-
"""评分要素解析器（eval_items）：从招标文件「评标办法 / 评分标准」章节提取评分点与分值。

这是标书撰写模块上游缺失的唯一产物。document_structure 已把「评标办法 / 评分标准 /
评分细则」识别为 sectionType == "evaluation"，但 requirement_scope 的抽取白名单刻意
排除了它（避免把评标流程性文本灌进需求对标），因此 fineReview.rows 里没有评分点。

本模块把 evaluation 章节重新解析为结构化评分要素，供标书撰写做两件事：
  1. 正文章节规划 —— 评分点即「要写哪些技术章节、每章多深」（见 planning.py）；
  2. 评标索引页 —— 评分因素/评分点/分值 → 对应页码（见 assemble.py 预留位）。

输入：document.sections（sectionType == "evaluation"）与 document.tables。
输出：{"items": [{"factor","item","score","criteria"}], "total": int, "method": str}。

纯解析，不调用 LLM；解析不清时整段保留原文（rawText），供上层兜底。
"""
from __future__ import annotations

import re
from typing import Any

# 评审办法关键词（用于从 method 文本里稳健抽「综合评分法 / 最低价法」）
_METHOD_TERMS = (
    ("综合评分", "综合评分法"),
    ("综合评价", "综合评价法"),
    ("最低价", "最低价法"),
    ("低价优先", "低价优先法"),
    ("性价比", "性价比法"),
)

# 评分因素列的候选词（表格列头归位用）
_FACTOR_HEADERS = ("评分因素", "评审因素", "评分项目", "评价因素", "因素")
_ITEM_HEADERS = ("评分点", "评审点", "评分项", "评审项", "项目", "内容")
_SCORE_HEADERS = ("分值", "分数", "满分", "得分", "权重")
_CRITERIA_HEADERS = ("评分标准", "评审标准", "评分办法", "评审办法", "评分细则", "标准", "说明")


def _norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _parse_score(text: str) -> float | None:
    """从「28分」「42 分」「12.5分」里抽分值；抽不到返回 None。"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*分", _clean(text))
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*分值", _clean(text))
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _detect_method(text: str) -> str:
    for kw, label in _METHOD_TERMS:
        if _norm(kw) in _norm(text):
            return label
    return ""


def _column_index(headers: list[str], key_terms: tuple[str, ...]) -> int:
    """在表头里找评分因素/评分点/分值/标准四列的列号；找不到返回 -1。"""
    for i, h in enumerate(headers):
        hnorm = _norm(h)
        for term in key_terms:
            if _norm(term) in hnorm and hnorm:
                return i
    return -1


def _pick(text: str, idx: int, extra: str = "") -> str:
    value = _clean(text)
    if not value or not extra:
        return value
    return value or extra


def _parse_eval_table(headers: list[str], rows: list[list[str]]) -> list[dict[str, Any]]:
    """按「评分因素/评分点/分值/评分标准」四列归位解析评分表。

    列头可能不齐（合并单元格、缺列），这里做稳健归位：
      - 找不到明确列头时，退化为「按行 + 分值正则」解析。
    """
    factor_col = _column_index(headers, _FACTOR_HEADERS)
    item_col = _column_index(headers, _ITEM_HEADERS)
    score_col = _column_index(headers, _SCORE_HEADERS)
    criteria_col = _column_index(headers, _CRITERIA_HEADERS)

    items: list[dict[str, Any]] = []
    if factor_col < 0 and item_col < 0 and score_col < 0:
        # 完全无列头信号 → 逐行正则兜底
        items = _parse_rows_fallback(rows)
        return items

    last_factor = ""
    for row in rows:
        if not any(_clean(c) for c in row):
            continue
        def cell(col: int) -> str:
            if 0 <= col < len(row):
                return _clean(row[col])
            return ""

        factor = cell(factor_col) if factor_col >= 0 else ""
        item = cell(item_col) if item_col >= 0 else ""
        score_text = cell(score_col) if score_col >= 0 else ""
        criteria = cell(criteria_col) if criteria_col >= 0 else ""

        # 评分因素列常被合并（同一因素占多行，只首行有值），子行继承。
        if factor:
            last_factor = factor
        else:
            factor = last_factor

        score = _parse_score(score_text)
        # 无独立 item 列时，从 criteria 或 factor 里兜底抽 item 标题
        if not item and criteria:
            item = criteria.split("。")[0][:30] if criteria else ""
        if not item and factor:
            item = factor

        # 跳过「合计 / 100分」这类汇总行
        if _norm(factor) in ("合计", "总计", "满分") and not criteria:
            continue

        entry = {
            "factor": factor or item or "未分类",
            "item": item or factor or "未命名评分点",
            "score": score,
            "criteria": criteria,
        }
        items.append(entry)

    return items


def _parse_rows_fallback(rows: list[list[str]]) -> list[dict[str, Any]]:
    """无表头时的兜底：把每行文本拼接，按「名称 + 分值」正则抽。"""
    items: list[dict[str, Any]] = []
    for row in rows:
        joined = " ".join(_clean(c) for c in row if _clean(c))
        if not joined:
            continue
        score = _parse_score(joined)
        if _norm(joined) in ("合计100分", "合计满分100分"):
            continue
        if score is None and len(joined) > 40:
            # 无分值的长文本，可能是评分标准正文，归为无分值的说明项
            items.append({"factor": "评分说明", "item": joined[:30], "score": None, "criteria": joined})
            continue
        items.append({"factor": "", "item": joined[:40], "score": score, "criteria": joined})
    return items


def _flatten_table_rows(table: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    """把 table dict 展平成 (headers, rows)。兼容两种结构：
      - 表格用 cells（document_structure 里 table_row 的 cell 列表）
      - 表格用 rows（[{cells:[{text}]}]）
    """
    headers: list[str] = []
    rows: list[list[str]] = []
    raw_rows = table.get("rows") or table.get("tableRows") or []
    if not isinstance(raw_rows, list):
        return headers, rows
    for r_idx, r in enumerate(raw_rows):
        if not isinstance(r, dict):
            continue
        cells = r.get("cells")
        if not isinstance(cells, list):
            continue
        line = []
        for c in cells:
            if isinstance(c, dict):
                line.append(_clean(c.get("text")))
            else:
                line.append(_clean(c))
        if r_idx == 0:
            headers = line
        else:
            rows.append(line)
    return headers, rows


def parse_eval_items(sections: list[Any], tables: list[Any] | None = None) -> dict[str, Any]:
    """从 evaluation 章节解析评分要素。

    优先从 tables 里找「评分因素/评分点/分值/标准」结构的表格；找不到再退化到
    section.text 的文本行解析。

    返回 {"items","total","method"}。items 元素 {"factor","item","score","criteria"}。
    """
    eval_sections = [s for s in (sections or []) if isinstance(s, dict) and s.get("sectionType") == "evaluation"]
    if not eval_sections:
        # sectionType 可能未命中但标题含评分词，兜底按标题抓
        eval_sections = [
            s for s in (sections or [])
            if isinstance(s, dict) and any(
                _norm(kw) in _norm(s.get("heading"))
                for kw in ("评分", "评标办法", "评审办法", "评标方法", "评分标准")
            )
        ]

    method = ""
    items: list[dict[str, Any]] = []
    raw_joined = ""

    # 1) 先尝试从结构化 tables 解析
    if isinstance(tables, list):
        for table in tables:
            if not isinstance(table, dict):
                continue
            headers, rows = _flatten_table_rows(table)
            if not rows:
                continue
            hnorm = " ".join(headers)
            is_eval_table = any(
                _norm(kw) in _norm(hnorm)
                for kw in ("评分因素", "评审因素", "评分标准", "评审标准", "分值", "评分点")
            )
            if not is_eval_table:
                continue
            parsed = _parse_eval_table(headers, rows)
            if parsed:
                items.extend(parsed)

    # 2) 退化：从 eval 章节正文文本解析
    if not items:
        for sec in eval_sections:
            raw_joined += "\n" + _clean(sec.get("text") or "")
        items = _parse_rows_fallback([line.split("|") for line in raw_joined.splitlines() if line.strip()])

    # 3) 汇总 method / total
    if not method:
        for sec in eval_sections:
            method = _detect_method(sec.get("text") or "")
            if method:
                break
    if not method:
        method = _detect_method(raw_joined)

    total = 0.0
    for it in items:
        if it.get("score") and float(it["score"]) > 0:
            total += float(it["score"])

    return {
        "items": items,
        "total": round(total, 2) if total else None,
        "method": method,
        "rawText": raw_joined.strip()[:4000] if raw_joined.strip() else "",
    }
