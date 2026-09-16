# -*- coding: utf-8 -*-
"""标书撰写模块的共享契约（唯一对齐基准）。

并行开发的各子模块（偏离表/商务/技术/组装/暗标/校对）只依赖这里定义的结构，
不得各自另造一套字段，否则组装层无法对接。

输入侧全部复用现有层（不做零开发）：
  - fineReview.rows  ← tender_matching/document_matching.py 的 comparison rows
  - project          ← store 的 CatalogItem（parse_tender / extract_project_fields 字段）
  - company          ← document_recognition/storage.py build_profile 的企业画像
  - knowledge        ← storage.py build_eligibility_knowledge / build_company_knowledge
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# 状态与枚举
# ---------------------------------------------------------------------------
BID_DOC_STATUS = ("planned", "generating", "generated", "exported")

BID_MODE_OPEN = "open"
BID_MODE_SECRET = "secret"

SECTION_STATUS = ("pending", "generating", "generated", "missing")

# 章节内容来源
SECTION_SOURCE_TEMPLATE = "template"     # 固定文书（投标函/承诺书等）
SECTION_SOURCE_AI = "ai"                 # LLM 逐条响应撰写
SECTION_SOURCE_TABLE = "table"           # 从 facts 表格化（一览表/配置表/偏离表）
SECTION_SOURCE_ATTACHMENT = "attachment" # 附件/证明材料插页

# 章节大类
SECTION_KIND_COMMERCIAL = "commercial"   # 商务标书
SECTION_KIND_TECHNICAL = "technical"     # 技术标书
SECTION_KIND_DEVIATION = "deviation"     # 偏离表
SECTION_KIND_ATTACHMENT = "attachment"   # 附件

# 偏离类型（五个取值，偏离表与校对共用）
DEVIATION_NONE = "无偏离"
DEVIATION_POSITIVE = "正偏离"
DEVIATION_NEGATIVE = "负偏离"
DEVIATION_NA = "不适用"
DEVIATION_PENDING = "待人工确认"

# fineReview 匹配状态 → 偏离类型（唯一映射，禁止各模块另写）
STATUS_TO_DEVIATION: dict[str, str] = {
    "满足": DEVIATION_NONE,
    "部分满足": DEVIATION_NEGATIVE,
    "不满足": DEVIATION_NEGATIVE,
    "待补材料": DEVIATION_NEGATIVE,
    "待澄清": DEVIATION_PENDING,
    "不适用": DEVIATION_NA,
}

# 偏离表分组
DEVIATION_TABLE_TECHNICAL = "technical"
DEVIATION_TABLE_COMMERCIAL = "commercial"

DEVIATION_TABLE_TITLES: dict[str, str] = {
    DEVIATION_TABLE_TECHNICAL: "技术规格偏离表",
    DEVIATION_TABLE_COMMERCIAL: "商务偏离表",
}


def deviation_table_for(category: str) -> str:
    """category → 偏离表分组键。技术类进技术表，其余一律进商务表。"""
    return DEVIATION_TABLE_TECHNICAL if category == "technical" else DEVIATION_TABLE_COMMERCIAL


# ---------------------------------------------------------------------------
# 溯源引用（防幻觉：每段生成内容必须带 sourceRefs）
# ---------------------------------------------------------------------------
def build_source_ref(
    fact_key: str,
    fact_id: str = "",
    name: str = "",
    evidence_path: str = "",
) -> dict[str, Any]:
    return {
        "factKey": fact_key,
        "factId": fact_id,
        "name": name,
        "evidencePath": evidence_path,
    }


# ---------------------------------------------------------------------------
# 章节（BidSection）
# ---------------------------------------------------------------------------
def build_section(
    section_id: str,
    title: str,
    source: str,
    *,
    kind: str = SECTION_KIND_TECHNICAL,
    requirement_ids: list[str] | None = None,
    content: str = "",
    source_refs: list[dict[str, Any]] | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    return {
        "sectionId": section_id,
        "title": title,
        "source": source,
        "kind": kind,
        "requirementIds": list(requirement_ids or []),
        "content": content,
        "sourceRefs": list(source_refs or []),
        "status": status,
    }


# ---------------------------------------------------------------------------
# 偏离表行（DeviationRow）
# ---------------------------------------------------------------------------
def build_deviation_row(comparison_row: dict[str, Any]) -> dict[str, Any]:
    """由 comparison row（fineReview.rows 的元素）派生偏离表行。

    偏离判断只读 comparison 的 status/reason/evidence，不重新生成、不调用 LLM。
    """
    status = str(comparison_row.get("status") or "待澄清")
    category = str(comparison_row.get("category") or "other")
    return {
        "requirementId": str(comparison_row.get("requirementId") or comparison_row.get("id") or ""),
        "category": category,
        "requirement": str(comparison_row.get("requirement") or comparison_row.get("rawText") or ""),
        "deviation": STATUS_TO_DEVIATION.get(status, DEVIATION_PENDING),
        "response": str(comparison_row.get("reason") or ""),
        "evidence": str(comparison_row.get("evidence") or ""),
        "suggestion": str(comparison_row.get("suggestion") or ""),
        "mandatory": bool(comparison_row.get("mandatory")),
        "isRejection": bool(comparison_row.get("isRejection")),
        "sourceLocator": str(comparison_row.get("sourceLocator") or ""),
    }


# ---------------------------------------------------------------------------
# 标书文档（BidDocument）
# ---------------------------------------------------------------------------
def build_bid_document(
    workspace_id: str,
    project_id: str,
    *,
    mode: str = BID_MODE_OPEN,
) -> dict[str, Any]:
    return {
        "workspaceId": workspace_id,
        "projectId": project_id,
        "mode": mode,
        "status": "planned",
        "version": 1,
        "generatedAt": "",
        "plan": [],
        "deviationTables": {
            DEVIATION_TABLE_TECHNICAL: [],
            DEVIATION_TABLE_COMMERCIAL: [],
        },
        "proofread": {
            "report": "",
            "riskItems": [],
        },
    }
