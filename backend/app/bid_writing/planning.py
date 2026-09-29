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
    SECTION_KIND_ATTACHMENT,
    SECTION_KIND_COMMERCIAL,
    SECTION_KIND_DEVIATION,
    SECTION_KIND_TECHNICAL,
    SECTION_SOURCE_AI,
    SECTION_SOURCE_ATTACHMENT,
    SECTION_SOURCE_TABLE,
    SECTION_SOURCE_TEMPLATE,
    build_section,
)
from .plan_tree import normalize_plan_tree

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


def _safe_level(value: Any) -> int:
    try:
        return max(int(value or 1), 1)
    except (TypeError, ValueError):
        return 1


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


def plan_from_outline(items: list[dict[str, Any]], fine_review: dict[str, Any]) -> list[dict[str, Any]]:
    """由招标文件响应目录生成章节树。

    items 来自 outline.extract_response_outline（[{sectionId,title,source,kind}]）。
    章节标题跟随招标文件，sectionId 复用固定池（c-XX/t-XX/d-XX）以对接
    commercial.py / technical.py；技术章节的 requirementIds 按 category 归属（同固定模板）。
    """
    rows = _rows(fine_review)
    assignment = assign_requirements(rows)

    sections: list[dict[str, Any]] = []
    seen: set[str] = set()
    has_explicit_hierarchy = any(
        isinstance(item, dict)
        and (
            str(item.get("parentSectionId") or "").strip()
            or _safe_level(item.get("level")) > 1
        )
        for item in items
    )
    group_ids = {
        SECTION_KIND_COMMERCIAL: "commercial-root",
        SECTION_KIND_TECHNICAL: "technical-root",
        SECTION_KIND_ATTACHMENT: "attachment-root",
        SECTION_KIND_DEVIATION: "attachment-root",
    }
    group_titles = {
        "commercial-root": "商务响应文件",
        "technical-root": "技术与服务响应文件",
        "attachment-root": "偏离与附件",
    }
    if not has_explicit_hierarchy:
        present_groups: list[tuple[str, str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or SECTION_KIND_TECHNICAL)
            group_id = group_ids.get(kind, "attachment-root")
            if any(existing[0] == group_id for existing in present_groups):
                continue
            source = (
                SECTION_SOURCE_AI
                if group_id == "technical-root"
                else SECTION_SOURCE_TEMPLATE
                if group_id == "commercial-root"
                else SECTION_SOURCE_ATTACHMENT
            )
            present_groups.append((group_id, group_titles[group_id], source))
        for order, (group_id, title, source) in enumerate(present_groups, start=1):
            group_kind = (
                SECTION_KIND_TECHNICAL
                if group_id == "technical-root"
                else SECTION_KIND_COMMERCIAL
                if group_id == "commercial-root"
                else SECTION_KIND_ATTACHMENT
            )
            sections.append(
                build_section(
                    group_id,
                    title,
                    source,
                    kind=group_kind,
                    level=1,
                    order=order,
                    response_type="section_group",
                    status="planned",
                )
            )
            seen.add(group_id)
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        section_id = str(item.get("sectionId") or "")
        title = str(item.get("title") or "").strip()
        if not section_id or not title or section_id in seen:
            continue
        seen.add(section_id)
        kind = str(item.get("kind") or SECTION_KIND_TECHNICAL)
        parent_section_id = str(item.get("parentSectionId") or "")
        level = _safe_level(item.get("level"))
        if not has_explicit_hierarchy:
            parent_section_id = group_ids.get(kind, "attachment-root")
            level = 2
        sections.append(
            build_section(
                section_id,
                title,
                str(item.get("source") or SECTION_SOURCE_AI),
                kind=kind,
                requirement_ids=assignment.get(section_id, []),
                parent_section_id=parent_section_id,
                level=level,
                order=int(item.get("order") or index),
                required=bool(item.get("required", True)),
                response_type=str(item.get("responseType") or ""),
            )
        )

    # 偏离表兜底：招标文件未列偏离表时也保证输出
    for section_id, title in (("d-01", "技术规格偏离表"), ("d-02", "商务偏离表")):
        if section_id not in seen:
            if "attachment-root" not in seen:
                sections.append(
                    build_section(
                        "attachment-root",
                        "偏离与附件",
                        SECTION_SOURCE_ATTACHMENT,
                        kind=SECTION_KIND_ATTACHMENT,
                        level=1,
                        order=len(
                            [item for item in sections if not item.get("parentSectionId")]
                        )
                        + 1,
                        response_type="section_group",
                        status="planned",
                    )
                )
                seen.add("attachment-root")
            sections.append(
                build_section(
                    section_id,
                    title,
                    SECTION_SOURCE_TABLE,
                    kind=SECTION_KIND_DEVIATION,
                    parent_section_id="attachment-root",
                    level=2,
                    order=len(
                        [
                            item
                            for item in sections
                            if item.get("parentSectionId") == "attachment-root"
                        ]
                    )
                    + 1,
                )
            )

    return normalize_plan_tree(sections)


def plan_bid_document(fine_review: dict[str, Any]) -> list[dict[str, Any]]:
    """产出完整章节树：商务(固定文书+表格) + 技术(AI 章节) + 偏离表。"""
    rows = _rows(fine_review)
    assignment = assign_requirements(rows)

    sections: list[dict[str, Any]] = []

    # 1) 商务标书：固定文书 + 事实表格
    sections.append(
        build_section(
            "commercial-root",
            "商务响应文件",
            SECTION_SOURCE_TEMPLATE,
            kind=SECTION_KIND_COMMERCIAL,
            level=1,
            order=1,
            response_type="section_group",
            status="planned",
        )
    )
    for section_id, title in COMMERCIAL_TEMPLATE_SECTIONS:
        sections.append(
            build_section(
                section_id,
                title,
                SECTION_SOURCE_TEMPLATE,
                kind=SECTION_KIND_COMMERCIAL,
                parent_section_id="commercial-root",
                level=2,
                order=len([item for item in sections if item.get("parentSectionId") == "commercial-root"]) + 1,
            )
        )
    for section_id, title in COMMERCIAL_TABLE_SECTIONS:
        sections.append(
            build_section(
                section_id,
                title,
                SECTION_SOURCE_TABLE,
                kind=SECTION_KIND_COMMERCIAL,
                parent_section_id="commercial-root",
                level=2,
                order=len([item for item in sections if item.get("parentSectionId") == "commercial-root"]) + 1,
            )
        )

    # 2) 技术标书：AI 章节，挂 requirementIds
    sections.append(
        build_section(
            "technical-root",
            "技术与服务响应文件",
            SECTION_SOURCE_AI,
            kind=SECTION_KIND_TECHNICAL,
            level=1,
            order=2,
            response_type="section_group",
            status="planned",
        )
    )
    for section_id, title, _category in TECHNICAL_AI_SECTIONS:
        sections.append(
            build_section(
                section_id,
                title,
                SECTION_SOURCE_AI,
                kind=SECTION_KIND_TECHNICAL,
                requirement_ids=assignment.get(section_id, []),
                parent_section_id="technical-root",
                level=2,
                order=len([item for item in sections if item.get("parentSectionId") == "technical-root"]) + 1,
            )
        )
    sections.extend(
        [
            build_section(
                "t-02-01",
                "系统架构与总体设计",
                SECTION_SOURCE_AI,
                kind=SECTION_KIND_TECHNICAL,
                parent_section_id="t-02",
                level=3,
                order=1,
                response_type="chapter_text",
            ),
            build_section(
                "t-02-02",
                "关键技术路线与功能响应",
                SECTION_SOURCE_AI,
                kind=SECTION_KIND_TECHNICAL,
                parent_section_id="t-02",
                level=3,
                order=2,
                response_type="chapter_text",
            ),
            build_section(
                "t-03-01",
                "实施组织与人员安排",
                SECTION_SOURCE_AI,
                kind=SECTION_KIND_TECHNICAL,
                parent_section_id="t-03",
                level=3,
                order=1,
                response_type="chapter_text",
            ),
            build_section(
                "t-03-02",
                "进度计划与交付保障",
                SECTION_SOURCE_AI,
                kind=SECTION_KIND_TECHNICAL,
                parent_section_id="t-03",
                level=3,
                order=2,
                response_type="chapter_text",
            ),
        ]
    )

    # 3) 偏离表（source=table，行数据由 deviations.py 填 bidDocument.deviationTables）
    sections.append(
        build_section(
            "deviation-root",
            "偏离与附件",
            SECTION_SOURCE_ATTACHMENT,
            kind=SECTION_KIND_ATTACHMENT,
            level=1,
            order=3,
            response_type="section_group",
            status="planned",
        )
    )
    sections.append(
        build_section(
            "d-01",
            "技术规格偏离表",
            SECTION_SOURCE_TABLE,
            kind=SECTION_KIND_DEVIATION,
            parent_section_id="deviation-root",
            level=2,
            order=1,
        )
    )
    sections.append(
        build_section(
            "d-02",
            "商务偏离表",
            SECTION_SOURCE_TABLE,
            kind=SECTION_KIND_DEVIATION,
            parent_section_id="deviation-root",
            level=2,
            order=2,
        )
    )

    return normalize_plan_tree(sections)


def complete_fallback_plan(
    plan: list[dict[str, Any]], fine_review: dict[str, Any],
) -> list[dict[str, Any]]:
    """解析格式草案仅有平级条目时补齐基础章节，保留原始格式条目。"""
    current = normalize_plan_tree(plan)
    if any(item["level"] >= 3 for item in current):
        return current
    baseline = plan_bid_document(fine_review)
    resolved_ids: dict[str, str] = {}
    used_ids = {item["sectionId"] for item in current}
    for item in baseline:
        found = next(
            (row for row in current if row["title"] == item["title"]),
            None,
        )
        if found:
            resolved_ids[item["sectionId"]] = found["sectionId"]
            continue
        row = dict(item)
        source_id = row["sectionId"]
        section_id = source_id
        suffix = 1
        while section_id in used_ids:
            section_id = f"{source_id}-fallback-{suffix}"
            suffix += 1
        row["sectionId"] = section_id
        row["parentSectionId"] = resolved_ids.get(
            str(item.get("parentSectionId") or ""), "",
        )
        row["order"] = len(current) + 1
        used_ids.add(section_id)
        resolved_ids[source_id] = section_id
        current.append(row)
    return normalize_plan_tree(current)


def minimal_bid_skeleton() -> list[dict[str, Any]]:
    """无可靠格式目录时使用的最小文件骨架。

    这里不展开资格条件、技术参数或评分点；这些内容在正文阶段再绑定到
    响应章节，避免目录回退时凭通用模板制造一整套正文目录。
    """
    root = build_section(
        "volume-main",
        "投标响应文件",
        SECTION_SOURCE_TEMPLATE,
        kind=SECTION_KIND_COMMERCIAL,
        level=1,
        order=1,
        response_type="section_group",
        status="planned",
    )
    root["nodeType"] = "volume"
    root["structureSource"] = "逻辑分组"
    children = [
        build_section(
            "skeleton-qualification",
            "资格及资信证明文件",
            SECTION_SOURCE_ATTACHMENT,
            kind=SECTION_KIND_ATTACHMENT,
            parent_section_id="volume-main",
            level=2,
            order=1,
            response_type="attachment",
            status="planned",
        ),
        build_section(
            "skeleton-pricing",
            "报价文件",
            SECTION_SOURCE_TABLE,
            kind=SECTION_KIND_COMMERCIAL,
            parent_section_id="volume-main",
            level=2,
            order=2,
            response_type="table",
            status="planned",
        ),
        build_section(
            "skeleton-technical",
            "技术商务响应文件",
            SECTION_SOURCE_AI,
            kind=SECTION_KIND_TECHNICAL,
            parent_section_id="volume-main",
            level=2,
            order=3,
            response_type="chapter_text",
            status="planned",
        ),
    ]
    return normalize_plan_tree([root, *children])


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


# ---------------------------------------------------------------------------
# 评分驱动章节规划（eval_items → 技术章节 + 分值）
# ---------------------------------------------------------------------------
# 评分点名称 → 技术章节 sectionId 的（泛化）映射。评分点的措辞千变万化，这里按
# 语义关键词归类到既有 t-01..t-07 固定池，复用 technical.py 的生成逻辑。
_EVAL_TO_SECTION: tuple[tuple[tuple[str, ...], str], ...] = (
    (("技术响应", "技术方案", "技术规格响应", "总体设计", "关键功能", "功能设计", "技术"), "t-02"),
    (("项目理解", "总体理解", "需求理解", "方案概述", "整体理解"), "t-01"),
    (("实施", "进度", "工期", "计划", "交付"), "t-03"),
    (("质量", "质保", "验收"), "t-04"),
    (("售后", "服务承诺", "运维", "维护", "服务"), "t-05"),
    (("培训",), "t-06"),
    (("应急", "预案", "保障", "合理化建议"), "t-07"),
)


def match_eval_section(name: str) -> str:
    """评分点/章节名称 → 技术章节固定池 sectionId；无关键词命中返回空串（不套默认值）。"""
    n = name
    for keywords, section_id in _EVAL_TO_SECTION:
        if any(kw in n for kw in keywords):
            return section_id
    return ""


def _eval_section_id(name: str) -> str:
    return match_eval_section(name) or "t-02"


def plan_from_eval(eval_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """由评分要素派生评分点 → 技术章节映射（附分值）。

    输入 eval_items（eval_parser.parse_eval_items 的 items，元素含 factor/item/score/criteria）。
    输出 [{sectionId, evalItem, score, criteria}]，供组装层生成「评标索引」页、
    供 technical.py 按评分点（而非仅 category）定向生成正文。

    同一章节聚合多个评分点时只保留一条，score 累加。
    """
    agg: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in eval_items or []:
        if not isinstance(item, dict):
            continue
        factor = str(item.get("factor") or "")
        item_name = str(item.get("item") or "")
        criteria = str(item.get("criteria") or "")
        score = item.get("score")
        # 跳过无实质内容的行
        if not item_name and not factor and not criteria:
            continue
        label = item_name or factor or "评分点"
        section_id = _eval_section_id(f"{factor}{item_name}{criteria}")

        if section_id not in agg:
            agg[section_id] = {
                "sectionId": section_id,
                "evalItem": label,
                "score": 0.0,
                "criteria": [],
            }
            order.append(section_id)
        entry = agg[section_id]
        if criteria and criteria not in entry["criteria"]:
            entry["criteria"].append(criteria)
        if score and float(score) > 0:
            entry["score"] = round(float(entry["score"]) + float(score), 2)

    result: list[dict[str, Any]] = []
    for section_id in order:
        entry = agg[section_id]
        result.append(
            {
                "sectionId": section_id,
                "evalItem": entry["evalItem"],
                "score": entry["score"] or None,
                "criteria": "；".join(entry["criteria"]) if entry["criteria"] else "",
            }
        )
    return result
