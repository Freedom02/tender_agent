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
SECTION_SOURCE_COPIED_TEMPLATE = "copied_template"  # 从招标文件复制的完整格式（填空）

# 章节大类
SECTION_KIND_COMMERCIAL = "commercial"   # 商务标书
SECTION_KIND_TECHNICAL = "technical"     # 技术标书
SECTION_KIND_DEVIATION = "deviation"     # 偏离表
SECTION_KIND_ATTACHMENT = "attachment"   # 附件

# 撰写画像：同一套编排接口下，按章节领域切换提示词和材料范围。
WRITING_PROFILE_TECHNICAL = "technical"
WRITING_PROFILE_COMMERCIAL = "commercial"
WRITING_PROFILE_TEMPLATE = "template"
WRITING_PROFILE_ATTACHMENT = "attachment"

# knowledge 中允许注入各类撰写智能体的材料分组。
# 业绩、人员、资质等可以被多个画像引用，但不会复制底层材料。
WRITING_PROFILE_MATERIAL_KEYS: dict[str, tuple[str, ...]] = {
    WRITING_PROFILE_TECHNICAL: (
        "products",
        "capabilities",
        "qualifications",
        "credentials",
        "projectReferences",
        "personnel",
    ),
    WRITING_PROFILE_COMMERCIAL: (
        "qualifications",
        "credentials",
        "projectReferences",
        "personnel",
        "financialRecords",
    ),
    WRITING_PROFILE_TEMPLATE: (
        "products",
        "capabilities",
        "qualifications",
        "credentials",
        "projectReferences",
        "personnel",
        "financialRecords",
    ),
    WRITING_PROFILE_ATTACHMENT: (
        "qualifications",
        "credentials",
        "projectReferences",
        "personnel",
        "financialRecords",
        "products",
        "capabilities",
    ),
}


def resolve_writing_profile(section: dict[str, Any] | None) -> str:
    """根据章节来源和类型选择撰写画像。"""

    value = section if isinstance(section, dict) else {}
    source = str(value.get("source") or "").strip()
    kind = str(value.get("kind") or "").strip()
    response_type = str(value.get("responseType") or "").strip()
    if kind == SECTION_KIND_ATTACHMENT or source == SECTION_SOURCE_ATTACHMENT:
        return WRITING_PROFILE_ATTACHMENT
    if (
        kind == SECTION_KIND_DEVIATION
        or source in {SECTION_SOURCE_TEMPLATE, SECTION_SOURCE_COPIED_TEMPLATE, SECTION_SOURCE_TABLE}
        or response_type in {"fixed_form", "table", "deviation", "attachment"}
    ):
        return WRITING_PROFILE_TEMPLATE
    if kind == SECTION_KIND_COMMERCIAL:
        return WRITING_PROFILE_COMMERCIAL
    return WRITING_PROFILE_TECHNICAL


def material_keys_for_writing_profile(profile: str) -> tuple[str, ...]:
    """返回画像允许使用的 knowledge 分组。"""

    return WRITING_PROFILE_MATERIAL_KEYS.get(
        str(profile or "").strip(),
        WRITING_PROFILE_MATERIAL_KEYS[WRITING_PROFILE_TECHNICAL],
    )

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


_REQUIREMENT_DOMAINS = {
    "qualification",
    "performance",
    "personnel",
    "financial",
    "credit",
    "region",
    "compliance",
    "technical",
    "service_plan",
    "commercial",
    "document",
    "pricing",
    "contract",
    "rejection",
    "evaluation",
    "project_info",
    "template",
    "other",
}


def requirement_domain(row: dict[str, Any] | None) -> str:
    """Read the legacy category or the target-oriented matching domain."""
    value = row if isinstance(row, dict) else {}
    candidates = [
        str(value.get(key) or "").strip().lower()
        for key in ("category", "targetModule", "matchDomain")
    ]
    for candidate in candidates:
        if candidate in _REQUIREMENT_DOMAINS - {"other"}:
            return candidate
    return next((candidate for candidate in candidates if candidate), "other")


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
#: 模型常把 itemType 词表写进 responseType，这里统一收口，避免下游/前端认不出。
_RESPONSE_TYPE_ALIASES = {
    "narrative": "chapter_text",
    "text": "chapter_text",
    "chapter": "chapter_text",
    "section": "chapter_text",
    "paragraph": "chapter_text",
    "fixed": "fixed_form",
    "form": "fixed_form",
    "fixed_table": "fixed_form",
    "table_response": "table",
    "response_table": "table",
    "evidence": "attachment",
    "material": "attachment",
    "table_deviation": "deviation",
    "group": "section_group",
}


def normalize_response_type(value: Any) -> str:
    """把模型输出的响应类型收敛到系统词表。

    合法值：``section_group`` / ``chapter_text`` / ``fixed_form`` / ``table`` /
    ``attachment`` / ``deviation``。未知值原样返回（由调用方决定如何兜底）。
    """

    text = value.strip() if isinstance(value, str) else ""
    if not text:
        return ""
    key = text.lower().replace("-", "_").replace(" ", "_")
    return _RESPONSE_TYPE_ALIASES.get(key, text)


def build_section(
    section_id: str,
    title: str,
    source: str,
    *,
    kind: str = SECTION_KIND_TECHNICAL,
    requirement_ids: list[str] | None = None,
    parent_section_id: str = "",
    level: int = 1,
    order: int = 0,
    required: bool = True,
    response_type: str = "",
    content: str = "",
    source_refs: list[dict[str, Any]] | None = None,
    status: str = "pending",
) -> dict[str, Any]:
    normalized_level = max(int(level or 1), 1)
    normalized_order = max(int(order or 0), 0)
    normalized_response_type = normalize_response_type(response_type)
    if not normalized_response_type:
        normalized_response_type = (
            "fixed_form"
            if source in {SECTION_SOURCE_TEMPLATE, SECTION_SOURCE_COPIED_TEMPLATE}
            else "table"
            if source in {SECTION_SOURCE_TABLE, "deviation"} or kind == SECTION_KIND_DEVIATION
            else "attachment"
            if source == SECTION_SOURCE_ATTACHMENT or kind == SECTION_KIND_ATTACHMENT
            else "chapter_text"
        )
    writing_profile = resolve_writing_profile(
        {
            "source": source,
            "kind": kind,
            "responseType": normalized_response_type,
        }
    )
    return {
        "sectionId": section_id,
        "title": title,
        "source": source,
        "kind": kind,
        "writingProfile": writing_profile,
        "materialScopes": list(material_keys_for_writing_profile(writing_profile)),
        "parentSectionId": str(parent_section_id or ""),
        "level": normalized_level,
        "order": normalized_order,
        "required": bool(required),
        "responseType": normalized_response_type,
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
    category = requirement_domain(comparison_row)
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
        "planSource": "unplanned",
        "planSourceDetail": "",
        "planningSource": "unplanned",
        "planningSourceDetail": "",
        "parseProvider": "",
        "parseSourceDetail": "",
        "planningProvider": "",
        "planningStatus": "",
        "planningModel": "",
        "planningFallbackReason": "",
        "plan": [],
        "requirementCoverage": [],
        "uncoveredRequirements": [],
        "duplicatedRequirements": [],
        "requirementMappingStatus": "pending",
        "requirementMapBatchCount": 0,
        "requirementMappingWarnings": [],
        "agentResults": [],
        "templateFragments": [],
        "templateFragmentsArtifact": "",
        "deviationTables": {
            DEVIATION_TABLE_TECHNICAL: [],
            DEVIATION_TABLE_COMMERCIAL: [],
        },
        "proofread": {
            "report": "",
            "riskItems": [],
        },
    }
