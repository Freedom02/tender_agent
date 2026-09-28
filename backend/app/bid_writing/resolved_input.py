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
    parse_bundle: dict[str, Any] | None = None,
    company: dict[str, Any] | None = None,
    organization: dict[str, Any] | None = None,
    knowledge: dict[str, Any] | None = None,
    ai: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把上游各散落产物归一成一个 ResolvedTender dict。

    ResolvedTender 是标书撰写的统一输入契约，覆盖两类输入：
      - 语义归一字段：composition / requirements / eval / projectMeta / company /
        organization / knowledge / risk / summary，供下游直接消费
      - bundle 透传：完整 ParseBundle 原文（documentBlocks / submissionStructure /
        templateFragments / rawOutline / riskItems …），供需要原始解析产物的工序读取

    字段来源优先级（解析定稿优先，缺失时回退规则重抽）：
      - requirements: fineReview.rows（撰写对标行）→ parse_bundle.requirements
      - eval.items:    parse_bundle.evaluationItems → eval_parser.parse_eval_items
      - projectMeta:   project.parsed 上叠加 parse_bundle.projectMeta 的非空字段
      - composition:   仍由 project.document.sections 抽取；rawOutline 是另一类产物，
                       由 planning 的目录回退分支单独消费，此处不混入
      - risk:          fineReview 的风险信号 + parse_bundle.riskItems
    每个字段的最终来源记录在 sources 里，便于定位口径漂移。
    """
    fine_review = fine_review if isinstance(fine_review, dict) else {}
    project = project if isinstance(project, dict) else {}
    bundle = parse_bundle if isinstance(parse_bundle, dict) else {}

    sources: dict[str, str] = {}

    # ② 需求条款库：撰写对标用比较行优先，缺失回退解析阶段原始条款
    rows = fine_review.get("rows")
    rows = rows if isinstance(rows, list) else []
    bundle_requirements = bundle.get("requirements")
    bundle_requirements = (
        bundle_requirements if isinstance(bundle_requirements, list) else []
    )
    if rows:
        requirements = rows
        sources["requirements"] = "fineReview"
    elif bundle_requirements:
        requirements = bundle_requirements
        sources["requirements"] = "parse_bundle"
    else:
        requirements = []
        sources["requirements"] = "empty"

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
    sources["composition"] = (
        "extract_response_outline" if outline and outline.get("items") else "empty"
    )

    # ③ 评分要素（eval_items）：解析定稿优先，缺失回退规则重抽
    try:
        rule_eval = parse_eval_items(sections, tables)
    except Exception:
        rule_eval = {"items": [], "total": None, "method": "", "rawText": ""}
    bundle_eval = bundle.get("evaluationItems")
    bundle_eval = bundle_eval if isinstance(bundle_eval, list) else []
    if bundle_eval:
        eval_result: dict[str, Any] = {
            "items": bundle_eval,
            "total": _sum_scores(bundle_eval),
            "method": str((rule_eval or {}).get("method") or ""),
            "source": "parse_bundle",
            "rawText": (rule_eval or {}).get("rawText") or "",
        }
    else:
        eval_result = {
            "items": rule_eval.get("items") or [],
            "total": rule_eval.get("total"),
            "method": str((rule_eval or {}).get("method") or ""),
            "source": "rules" if rule_eval.get("items") else "empty",
            "rawText": (rule_eval or {}).get("rawText") or "",
        }
    sources["eval"] = eval_result["source"]

    # ④ 项目元信息：project.parsed 归一到 projectMeta，叠加 parse_bundle.projectMeta 非空字段
    parsed = project.get("parsed") if isinstance(project.get("parsed"), dict) else {}
    project_meta = {
        "projectName": _pick(parsed.get("projectName"), project.get("title")),
        "projectCode": _pick(parsed.get("projectCode")),
        "tendererName": _pick(parsed.get("tendererName")),
        "agencyName": _pick(parsed.get("agencyName")),
        "budget": _pick(parsed.get("budget")),
        "title": _pick(project.get("title")),
    }
    bundle_meta = bundle.get("projectMeta") if isinstance(bundle.get("projectMeta"), dict) else {}
    overlaid = False
    for key in ("projectName", "projectCode", "tendererName", "agencyName", "budget"):
        if bundle_meta.get(key):
            project_meta[key] = str(bundle_meta[key]).strip()
            overlaid = True
    sources["projectMeta"] = "parse_bundle" if overlaid else "project"

    # 风险信号（proofread 直接读）：fineReview 三类信号 + 解析阶段 riskItems
    risk = {
        "hardBlockers": fine_review.get("hardBlockers") or [],
        "rejectionRisks": fine_review.get("rejectionRisks") or [],
        "unresolvedMandatory": fine_review.get("unresolvedMandatory") or [],
        "riskItems": bundle.get("riskItems") if isinstance(bundle.get("riskItems"), list) else [],
    }
    sources["risk"] = "fineReview+bundle"

    return {
        "composition": composition,
        "requirements": requirements,
        "eval": eval_result,
        "projectMeta": project_meta,
        "company": company if isinstance(company, dict) else {},
        "organization": organization if isinstance(organization, dict) else {},
        "knowledge": knowledge if isinstance(knowledge, dict) else {},
        "risk": risk,
        "summary": fine_review.get("summary") or {},
        "bundle": bundle,
        "sources": sources,
    }


def _sum_scores(items: list[Any]) -> float | None:
    total = 0.0
    for item in items:
        if isinstance(item, dict):
            try:
                total += float(item.get("score") or 0)
            except (TypeError, ValueError):
                continue
    return round(total, 2) if total else None


def _pick(*values: Any) -> str:
    for v in values:
        if isinstance(v, (list, tuple)):
            v = "、".join(str(x).strip() for x in v if str(x).strip())
        if v not in (None, ""):
            return str(v).strip()
    return ""
