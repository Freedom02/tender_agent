# -*- coding: utf-8 -*-
"""标书偏离表子模块（纯函数、确定性、无 LLM）。

输入 fineReview.rows（comparison rows），产出：
  1) 技术/商务两张偏离表（复用 schemas.build_deviation_row 派生偏离行）；
  2) 偏离风险清单（供总览/校对使用，level/title/detail 与现有风险结构一致）。

偏离判断只读 comparison 的 status/reason/evidence（见 schemas.STATUS_TO_DEVIATION），
不重新生成、不调用 LLM、不新造字段名。
"""
from __future__ import annotations

from typing import Any

from .schemas import (
    DEVIATION_NEGATIVE,
    DEVIATION_PENDING,
    DEVIATION_TABLE_COMMERCIAL,
    DEVIATION_TABLE_TECHNICAL,
    DEVIATION_TABLE_TITLES,
    build_deviation_row,
    deviation_table_for,
    requirement_domain,
)

# 风险清单 detail 的最大字符数（requirement + suggestion 截断长度）
_DETAIL_LIMIT = 120

# 判定为「高」风险的原始匹配状态
_HIGH_RISK_STATUS = "不满足"


def _rows(fine_review: dict[str, Any]) -> list[dict[str, Any]]:
    """安全取出 fineReview.rows；rows 缺失/非 list 时返回空列表。"""
    rows = fine_review.get("rows") if isinstance(fine_review, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _evidence_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def build_deviation_tables(fine_review: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """把 fineReview.rows 派生为技术/商务两张偏离表。

    每条 comparison row 用 schemas.build_deviation_row 派生偏离行，并按 category
    分到 {"technical": [...], "commercial": [...]} 两组，保持原文顺序；
    rows 缺失/非 list 时返回两张空表。
    """
    tables: dict[str, list[dict[str, Any]]] = {
        DEVIATION_TABLE_TECHNICAL: [],
        DEVIATION_TABLE_COMMERCIAL: [],
    }
    for row in _rows(fine_review):
        deviation_row = build_deviation_row(row)
        category = requirement_domain(row)
        # 保留原始匹配状态：风险等级需区分「不满足」与「部分满足/待补材料」，
        # 二者均映射为同一偏离值「负偏离」，仅靠 deviation 无法区分。
        deviation_row["status"] = str(row.get("status") or "待澄清")
        has_evidence_refs = any(
            any(value not in (None, "") for value in _evidence_values(row.get(key)))
            for key in (
                "matchedObjectIds",
                "matchedMaterialIds",
                "evidenceIds",
                "evidenceItems",
                "matchedObjects",
            )
        )
        if deviation_row["status"] == "满足" and not has_evidence_refs:
            deviation_row["deviation"] = DEVIATION_PENDING
            deviation_row["suggestion"] = (
                "匹配结果缺少可追溯的 M3 材料，请重新核验后再确认无偏离。"
            )
        table = deviation_table_for(category)
        tables[table].append(deviation_row)
    return tables


def deviation_risk_items(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """从两张偏离表筛出「负偏离」「待人工确认」行，产出风险清单。

    每条输出为 {level, title, detail}：
      level   isRejection 或原始 status 为「不满足」→ "高"，其余 → "中"；
      title   「技术规格偏离表/商务偏离表 · 第N条」；
      detail  requirement + suggestion 前若干字（截断）。
    """
    items: list[dict[str, Any]] = []
    if not isinstance(tables, dict):
        return items
    for table_key in (DEVIATION_TABLE_TECHNICAL, DEVIATION_TABLE_COMMERCIAL):
        rows = tables.get(table_key)
        if not isinstance(rows, list):
            continue
        title_prefix = DEVIATION_TABLE_TITLES.get(table_key, table_key)
        for index, row in enumerate(rows, start=1):
            if not isinstance(row, dict):
                continue
            if row.get("deviation") not in (DEVIATION_NEGATIVE, DEVIATION_PENDING):
                continue
            is_high = bool(row.get("isRejection")) or (
                str(row.get("status") or "") == _HIGH_RISK_STATUS
            )
            level = "高" if is_high else "中"
            requirement = str(row.get("requirement") or "")
            suggestion = str(row.get("suggestion") or "")
            detail = f"{requirement} {suggestion}".strip()[:_DETAIL_LIMIT]
            items.append(
                {
                    "level": level,
                    "title": f"{title_prefix} · 第{index}条",
                    "detail": detail,
                }
            )
    return items
