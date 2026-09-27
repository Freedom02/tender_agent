# -*- coding: utf-8 -*-
"""解析产物聚合层（ResolvedTender）——标书撰写的唯一输入契约。

把上游已产出的解析产物统一成**一个**结构化对象，喂给标书撰写的各工序
（目录生成 / 信息填充 / 正文编辑 / 偏离表 / 评标索引 / 校对）。

设计意图：撰写层不再直接散读 workspace["reasoning"]["fineReview"]、
store["project"]["document"]["sections"] 等深层字段，而是消费一个语义清晰、
字段稳定的 ResolvedTender。这样三层解析产物（编制骨架 / 需求条款 / 评分要素）
在进入撰写前被显式归一，任何一处上游口径变化只改这里一处。

对应三层解析产物：
  ① 编制骨架 composition   ← outline.extract_response_outline（已存在）
  ② 需求条款库 requirements ← fineReview.rows（已存在，comparison rows）
  ③ 评分要素 eval_items     ← eval_parser.parse_eval_items（本包新增）

外加两个撰写必需的非需求输入：
  ④ 项目元信息 project_meta ← project.parsed（extract_project_fields）
  ⑤ 投标人资产 company / organization / knowledge（M3 知识库已存在）

本模块不做任何解析，只做「聚合 + 轻量归一」，保证下游写法统一。
"""
from __future__ import annotations

from typing import Any

from .eval_parser import parse_eval_items
from .outline import extract_response_outline


# ---------------------------------------------------------------------------
# ResolvedTender 构建
# ---------------------------------------------------------------------------
def resolve_tender(
    fine_review: dict[str, Any] | None,
    project: dict[str, Any] | None,
    *,
    company: dict[str, Any] | None = None,
    organization: dict[str, Any] | None = None,
    knowledge: dict[str, Any] | None = None,
    ai: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把上游各散落产物归一成一个 ResolvedTender dict。

    ResolvedTender 结构：
      {
        "composition":  { "items": [...], "sourceDetail": str, "source": "tender_document"/"builtin_template" },
        "requirements": [ comparison row, ... ],         # = fineReview.rows
        "eval":         { "items": [...], "total": float|None, "method": str },
        "projectMeta":  { "projectName", "projectCode", "tendererName", ... },
        "company":      { ... },                          # 企业画像
        "organization": { ... },                          # 原始组织 fact
        "knowledge":    { ... },                          # M3 知识库
        "risk":         { "hardBlockers":[...], "rejectionRisks":[...], "unresolvedMandatory":[...] },
        "summary":      { ... },                          # fineReview.summary
      }
    """
    fine_review = fine_review if isinstance(fine_review, dict) else {}
    project = project if isinstance(project, dict) else {}

    rows = fine_review.get("rows")
    rows = rows if isinstance(rows, list) else []

    # ① 编制骨架（composition）：从招标文件「投标文件格式」抽取，失败回退固定模板
    document = project.get("document") if isinstance(project.get("document"), dict) else {}
    sections = document.get("sections") if isinstance(document.get("sections"), list) else []
    tables = document.get("tables") if isinstance(document.get("tables"), list) else []

    outline = None
    if sections:
        try:
            outline = extract_response_outline(sections, ai or {})
        except Exception:
            outline = None

    composition: dict[str, Any] = {
        "items": (outline or {}).get("items") or [],
        "sourceDetail": (outline or {}).get("sourceDetail") or "",
        "source": "tender_document" if outline and outline.get("items") else "builtin_template",
    }

    # ③ 评分要素（eval_items）：本包新增，从 evaluation 章节解析
    try:
        eval_result = parse_eval_items(sections, tables)
    except Exception:
        eval_result = {"items": [], "total": None, "method": "", "rawText": ""}

    # ④ 项目元信息：project.parsed 归一到 projectMeta
    parsed = project.get("parsed") if isinstance(project.get("parsed"), dict) else {}
    project_meta = {
        "projectName": _pick(parsed.get("projectName"), project.get("title")),
        "projectCode": _pick(parsed.get("projectCode")),
        "tendererName": _pick(parsed.get("tendererName")),
        "agencyName": _pick(parsed.get("agencyName")),
        "budget": _pick(parsed.get("budget")),
        "title": _pick(project.get("title")),
    }

    # 风险信号（proofread 直接读）
    risk = {
        "hardBlockers": fine_review.get("hardBlockers") or [],
        "rejectionRisks": fine_review.get("rejectionRisks") or [],
        "unresolvedMandatory": fine_review.get("unresolvedMandatory") or [],
    }

    return {
        "composition": composition,
        "requirements": rows,
        "eval": eval_result,
        "projectMeta": project_meta,
        "company": company if isinstance(company, dict) else {},
        "organization": organization if isinstance(organization, dict) else {},
        "knowledge": knowledge if isinstance(knowledge, dict) else {},
        "risk": risk,
        "summary": fine_review.get("summary") or {},
    }


def _pick(*values: Any) -> str:
    for v in values:
        if isinstance(v, (list, tuple)):
            v = "、".join(str(x).strip() for x in v if str(x).strip())
        if v not in (None, ""):
            return str(v).strip()
    return ""
