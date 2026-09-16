# -*- coding: utf-8 -*-
"""标书章节规划器（纯读、确定性、无 LLM）。

输入 fineReview.rows（逐条对标结果），产出标书章节树（plan）。
本模块只做「结构规划 + 要求→章节归属」，不生成正文（正文由 technical.py / commercial.py 负责）。

归属规则（一对一，避免同一要求落入多个章节）：
  technical    → 技术方案
  service_plan → 实施方案
  commercial   → 售后服务方案
  其余类别（资质/业绩/人员/财务/信用/符合性/否决/格式/报价/合同…）
    → 由商务模板 + 商务偏离表覆盖，不进技术叙述章节。
"""
from __future__ import annotations

from typing import Any

from .schemas import (
    SECTION_KIND_COMMERCIAL,
    SECTION_KIND_DEVIATION,
    SECTION_KIND_TECHNICAL,
    SECTION_SOURCE_AI,
    SECTION_SOURCE_TABLE,
    SECTION_SOURCE_TEMPLATE,
    build_section,
)

# 商务标书固定文书（模板驱动，正文由 commercial.py 填）
COMMERCIAL_TEMPLATE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("c-01", "投标函"),
    ("c-02", "投标承诺书"),
    ("c-03", "法定代表人身份证明"),
    ("c-04", "授权委托书"),
    ("c-05", "投标保证金说明"),
    ("c-06", "资格审查声明"),
    ("c-07", "企业简介"),
)

# 商务标书事实表格化章节（由 commercial.py 从 facts 生成）
COMMERCIAL_TABLE_SECTIONS: tuple[tuple[str, str], ...] = (
    ("c-08", "企业资信一览表"),
    ("c-09", "业绩一览表"),
    ("c-10", "人员配置表"),
)

# 技术标书 AI 章节：(sectionId, title, 消费的 category)
# 一对一归属：同一 category 只出现在一个章节。
TECHNICAL_AI_SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("t-01", "项目理解", ""),
    ("t-02", "技术方案", "technical"),
    ("t-03", "实施方案", "service_plan"),
    ("t-04", "质量保证措施", ""),
    ("t-05", "售后服务方案", "commercial"),
    ("t-06", "培训方案", ""),
    ("t-07", "应急保障方案", ""),
)

# category → 技术章节 sectionId（一对一）
CATEGORY_TO_SECTION: dict[str, str] = {
    category: section_id
    for section_id, _title, category in TECHNICAL_AI_SECTIONS
    if category
}


def _rows(fine_review: dict[str, Any]) -> list[dict[str, Any]]:
    rows = fine_review.get("rows") if isinstance(fine_review, dict) else None
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def assign_requirements(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """按 category 把 requirementId 归属到技术章节。返回 {sectionId: [requirementId, ...]}。"""
    assigned: dict[str, list[str]] = {}
    for row in rows:
        category = str(row.get("category") or "other")
        section_id = CATEGORY_TO_SECTION.get(category)
        if not section_id:
            continue
        requirement_id = str(row.get("requirementId") or row.get("id") or "")
        if not requirement_id:
            continue
        assigned.setdefault(section_id, [])
        if requirement_id not in assigned[section_id]:
            assigned[section_id].append(requirement_id)
    return assigned


def plan_bid_document(fine_review: dict[str, Any]) -> list[dict[str, Any]]:
    """产出完整章节树：商务(固定文书+表格) + 技术(AI 章节) + 偏离表。"""
    rows = _rows(fine_review)
    assignment = assign_requirements(rows)

    sections: list[dict[str, Any]] = []

    # 1) 商务标书：固定文书 + 事实表格
    for section_id, title in COMMERCIAL_TEMPLATE_SECTIONS:
        sections.append(build_section(section_id, title, SECTION_SOURCE_TEMPLATE, kind=SECTION_KIND_COMMERCIAL))
    for section_id, title in COMMERCIAL_TABLE_SECTIONS:
        sections.append(build_section(section_id, title, SECTION_SOURCE_TABLE, kind=SECTION_KIND_COMMERCIAL))

    # 2) 技术标书：AI 章节，挂 requirementIds
    for section_id, title, _category in TECHNICAL_AI_SECTIONS:
        sections.append(
            build_section(
                section_id,
                title,
                SECTION_SOURCE_AI,
                kind=SECTION_KIND_TECHNICAL,
                requirement_ids=assignment.get(section_id, []),
            )
        )

    # 3) 偏离表（source=table，行数据由 deviations.py 填 bidDocument.deviationTables）
    sections.append(build_section("d-01", "技术规格偏离表", SECTION_SOURCE_TABLE, kind=SECTION_KIND_DEVIATION))
    sections.append(build_section("d-02", "商务偏离表", SECTION_SOURCE_TABLE, kind=SECTION_KIND_DEVIATION))

    return sections


def plan_summary(fine_review: dict[str, Any]) -> dict[str, Any]:
    """规划后的覆盖摘要：每条要求进入技术叙述章节还是仅进偏离表。"""
    rows = _rows(fine_review)
    assignment = assign_requirements(rows)
    narrative_ids = {rid for ids in assignment.values() for rid in ids}
    return {
        "totalRequirements": len(rows),
        "narrativeAssigned": len(narrative_ids),
        "deviationOnly": len(rows) - len(narrative_ids),
        "bySection": assignment,
    }
