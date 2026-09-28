# -*- coding: utf-8 -*-
"""评分驱动规划（EvaluationStrategy）：把招标文件评分点分值折算成章节的分值权重与深度档位。

上游 eval_parser.parse_eval_items 抽出的评分点（factor/item/score/criteria）只说明
「评标时这些点给分」；本模块把每个评分点归位到规划章节，算出每章的分值权重与深度
档位，供正文规划与生成按分值倾斜篇幅，而不是所有章节等量输出。

评分点 → 章节的归位口径与 planning.plan_from_eval 完全一致（复用
planning._eval_section_id 的语义关键词表，映射到 t-01..t-07 固定池），保证
evaluationPlan 与 evaluationStrategy 两个视图对同一评分点的章节归属不打架。
"""
from __future__ import annotations

from typing import Any

# 分值权重阈值：≥25% 为重点章（写深写足）、≥8% 为常规章、其余为次要章。
DEPTH_PRIMARY_WEIGHT = 0.25
DEPTH_REGULAR_WEIGHT = 0.08


def _score_of(item: dict[str, Any]) -> float:
    score = item.get("score")
    if score in (None, ""):
        return 0.0
    try:
        return float(score)
    except (TypeError, ValueError):
        return 0.0


def depth_for_weight(weight: float) -> str:
    """分值权重 → 篇幅深度档位。"""
    if weight >= DEPTH_PRIMARY_WEIGHT:
        return "primary"
    if weight >= DEPTH_REGULAR_WEIGHT:
        return "regular"
    return "minor"


def build_evaluation_strategy(
    eval_items: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    *,
    method: str = "",
) -> dict[str, Any]:
    """把评分点归位到章节，产出每章分值权重/深度档位与未归位评分点。

    返回：
      {
        "method": str,
        "total": float | None,               # 评分点分值合计
        "bySection": { sectionId: {          # 已归位章节
            "sectionId", "score", "weight", "depth", "points": [...]
        }},
        "unmapped": [ {evalId,factor,item,score,criteria,targetSectionId}, ... ],
        "depthPolicy": { sectionId: depth },
      }
    """
    from .planning import _eval_section_id

    section_ids = {
        str(section.get("sectionId") or ""): section
        for section in (sections or [])
        if isinstance(section, dict) and str(section.get("sectionId") or "")
    }
    # 章节标题 → 固定池 sectionId 的备选归位：评分点落在「技术方案/售后…」等标题上时，
    # 即使规划章节用了自定义 sectionId 也能命中。
    title_targets: dict[str, str] = {}
    for section_id, section in section_ids.items():
        title = str(section.get("title") or "")
        if title:
            title_targets.setdefault(_eval_section_id(title), section_id)

    by_section: dict[str, dict[str, Any]] = {}
    unmapped: list[dict[str, Any]] = []
    total = 0.0
    index = 0

    for item in eval_items or []:
        if not isinstance(item, dict):
            continue
        factor = str(item.get("factor") or "")
        item_name = str(item.get("item") or "")
        criteria = str(item.get("criteria") or "")
        if not (factor or item_name or criteria):
            continue
        index += 1
        score = _score_of(item)
        total += score
        label = item_name or factor or "评分点"
        target = _eval_section_id(f"{factor}{item_name}{criteria}")

        section_id = target if target in section_ids else title_targets.get(target, "")
        point = {
            "evalId": f"eval-{index:03d}",
            "factor": factor,
            "item": label,
            "score": score or None,
            "criteria": criteria,
        }
        if not section_id:
            unmapped.append({**point, "targetSectionId": target})
            continue
        entry = by_section.setdefault(
            section_id,
            {"sectionId": section_id, "score": 0.0, "points": []},
        )
        entry["score"] = round(entry["score"] + score, 2)
        entry["points"].append(point)

    for entry in by_section.values():
        weight = round(entry["score"] / total, 4) if total else 0.0
        entry["weight"] = weight
        entry["depth"] = depth_for_weight(weight)

    return {
        "method": method,
        "total": round(total, 2) if total else None,
        "bySection": by_section,
        "unmapped": unmapped,
        "depthPolicy": {
            section_id: entry["depth"] for section_id, entry in by_section.items()
        },
    }


def attach_evaluation_strategy(
    bid_document: dict[str, Any],
    eval_items: list[dict[str, Any]],
    *,
    method: str = "",
) -> dict[str, Any]:
    """把评分策略回写到 bidDocument：给规划章节打评分注解 + 挂 evaluationStrategy。

    章节新增字段（可缺省，向后兼容）：
      evaluationIds    评分点 evalId 列表（Pi 的 _section_prompt_view 已透传该字段）
      evaluationScore  本章评分点分值合计
      evaluationWeight 本章分值权重（score/total，0~1）
      evaluationDepth  篇幅深度档位（primary/regular/minor）
    """
    plan = bid_document.get("plan")
    plan = plan if isinstance(plan, list) else []
    strategy = build_evaluation_strategy(eval_items, plan, method=method)

    by_section = strategy["bySection"]
    for section in plan:
        if not isinstance(section, dict):
            continue
        entry = by_section.get(str(section.get("sectionId") or ""))
        if not entry:
            continue
        section["evaluationScore"] = entry["score"] or None
        section["evaluationWeight"] = entry["weight"]
        section["evaluationDepth"] = entry["depth"]
        section["evaluationIds"] = [point["evalId"] for point in entry["points"]]

    bid_document["evaluationStrategy"] = strategy
    return strategy
