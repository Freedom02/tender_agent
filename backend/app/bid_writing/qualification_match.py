# -*- coding: utf-8 -*-
"""资质审核匹配契约（标书撰写侧对齐基准，与 param_match.py 同构）。

把「匹配层已经算好的资格匹配结果」投影成标书可直接消费的结构。
与 param_match 的唯一区别：这里只做纯投影、不补企业台账、不调 LLM，
因为资格行在 fineReview.rows 里已带 matchedObjectIds 与 evidenceItems（含证明材料位置）。

数据来源（只读）：
  - fineReview.rows ← tender_matching/ 的 comparison rows
    每条 qualification 域行上的 matchedObjectIds / evidenceItems[].sourceLocator
    已含「命中事实 ID」与「证明材料路径」，本模块只做规整。

产出 qualificationMatches[]，字段：
    requirementId / requirement / category      招标要求侧（ID、原文、分类）
    status / reason                             匹配结论（状态、判定理由）
    matchedObjectIds[]                          命中的企业事实 ID
    evidenceItems[]                             证明材料（id/name/status/sourceLocator/excerpt）
    sourceLocator                               招标文件溯源
"""
from __future__ import annotations

from typing import Any

# 资格域：targetModule=qualification，或 15 类兼容层的资格相关 category。
# 与 integration._row_matches_domain(row, "qualification") 保持一致。
_QUALIFICATION_CATEGORIES = {
    "qualification",
    "performance",
    "personnel",
    "financial",
    "credit",
    "region",
    "eligibility",
}


def _text(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _list(value: Any) -> list[Any]:
    return [item for item in value] if isinstance(value, (list, tuple)) else []


def _rows(fine_review: Any) -> list[dict[str, Any]]:
    if not isinstance(fine_review, dict):
        return []
    rows = fine_review.get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _row_domain(row: dict[str, Any]) -> str:
    """返回行的域键：优先 targetModule，其次 category（兼容 15 类路径）。"""
    return str(row.get("targetModule") or row.get("category") or "other")


def _is_qualification_row(row: dict[str, Any]) -> bool:
    """是否为「资格审查域」行（资质/业绩/人员/财务/信用/地域）。"""
    target = str(row.get("targetModule") or row.get("domain") or "").lower()
    category = str(
        row.get("category") or row.get("contentType") or row.get("matchDomain") or ""
    ).lower()
    if target == "qualification" or category == "qualification":
        return True
    return category in _QUALIFICATION_CATEGORIES


def _evidence_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    """把 row.evidenceItems 投影成精简的证明材料清单（保留证明材料位置）。"""
    result: list[dict[str, Any]] = []
    for item in _list(row.get("evidenceItems")):
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("id") or item.get("evidenceId") or "").strip()
        name = str(item.get("name") or "").strip()
        if not evidence_id and not name:
            continue
        result.append(
            {
                "id": evidence_id,
                "name": name,
                "status": _text(item.get("verificationStatus") or item.get("status"), 100),
                "sourceLocator": _text(item.get("sourceLocator") or item.get("locator"), 500),
                "excerpt": _text(item.get("excerpt"), 600),
            }
        )
    return result


def build_qualification_match(row: dict[str, Any]) -> dict[str, Any]:
    """把一条资格域 comparison row 规整成一条 QualificationMatch 记录。"""
    return {
        "requirementId": _text(row.get("requirementId") or row.get("id"), 100),
        "requirement": _text(row.get("requirement") or row.get("rawText"), 4000),
        "category": _row_domain(row),
        "status": _text(row.get("status") or "待补材料", 40),
        "reason": _text(row.get("reason"), 2000),
        "matchedObjectIds": [
            _text(value, 100)
            for value in _list(row.get("matchedObjectIds"))
            if _text(value, 100)
        ],
        "evidenceItems": _evidence_items(row),
        "sourceLocator": _text(row.get("sourceLocator"), 500),
    }


def build_qualification_matches(fine_review: Any) -> list[dict[str, Any]]:
    """把 fineReview.rows 中的资格域行规整成 QualificationMatch[] 清单。

    纯函数、确定性、只读输入；无资格域行时返回空列表。
    """
    return [
        build_qualification_match(row)
        for row in _rows(fine_review)
        if _is_qualification_row(row)
    ]
