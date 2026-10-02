# -*- coding: utf-8 -*-
"""提示词公共底座：常量、schema 文本与上下文压缩视图。

本模块由原 `prompts.py` 机械拆分而来（Wave 0 · 0.2），**不承载任何提示词正文**：
各阶段提示词见 `parse.py` / `plan.py` / `draft.py` / `fill.py` / `review.py`，
统一由 `pi_agent.prompts` 包对外导出，旧 import 路径不变。

拆分原则：按「一个智能体阶段一个文件」归位，便于多会话并行修改互不冲突；
本文件是所有阶段共享的常量与视图函数，改动会影响全部提示词——基线护栏见
`backend/tests/baselines/generate_prompt_baseline.py`。
"""
from __future__ import annotations
import json
import re
from typing import Any
from ...bid_writing.schemas import material_keys_for_writing_profile, resolve_writing_profile
from ..outline_agent_context import OUTLINE_TOOL_NAMES, skill_body




_WRITING_PROFILE_SPECS: dict[str, dict[str, Any]] = {
    "technical": {
        "label": "技术/服务标",
        "categories": ("technical", "service_plan", "performance", "personnel"),
        "focus": "技术响应、实施组织、质量保证、服务保障、培训和应急安排。",
    },
    "commercial": {
        "label": "商务/资格标",
        "categories": (
            "qualification",
            "commercial",
            "performance",
            "personnel",
            "financial",
            "credit",
            "contract",
            "pricing",
        ),
        "focus": "资格合规、企业能力、商务条件、合同响应和可核验的证明材料。",
    },
    "template": {
        "label": "固定模板/表格",
        "categories": (),
        "focus": "只填充模板允许编辑的字段或单元格，不改固定结构。",
    },
    "attachment": {
        "label": "证明附件",
        "categories": (),
        "focus": "只引用已有证明材料和证据路径，不生成新的企业事实。",
    },
}


# 评分深度档位 → 篇幅要求（取值来自 evaluation_strategy.depth_for_weight）
_DEPTH_GUIDANCE: dict[str, str] = {
    "primary": "重点章：分值占比高，必须写深写足，逐条响应本章评分点，不省略论证与佐证。",
    "regular": "常规章：评分点覆盖到位、论证清楚即可，不做冗余铺陈。",
    "minor": "次要章：覆盖评分点后即收束，不再扩写。",
}




















_OUTLINE_SOURCE_TERMS = (
    "投标文件的编制",
    "投标文件编制要求",
    "投标文件编制",
    "响应文件编制要求",
    "响应文件编制",
    "投标文件的组成",
    "响应文件的组成",
    "投标文件组成",
    "响应文件组成",
    "投标文件的构成",
    "响应文件的构成",
    "投标文件格式",
    "响应文件格式",
    "电子投标文件格式",
    "投标文件提交",
    "响应文件提交",
    "投标文件应包括",
    "响应文件应包括",
)


def _dump(value: Any, limit: int = 60000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[内容已截断]..."


def _writing_profile_spec(profile: str) -> dict[str, Any]:
    return _WRITING_PROFILE_SPECS.get(
        str(profile or "").strip(),
        _WRITING_PROFILE_SPECS["technical"],
    )


def _profile_block(profile: str) -> str:
    spec = _writing_profile_spec(profile)
    categories = "、".join(str(item) for item in spec["categories"]) or "按模板字段处理"
    return (
        f"撰写画像：{spec['label']}\n"
        f"本类重点：{spec['focus']}\n"
        f"优先处理的要求类别：{categories}"
    )


def _section_guidance_block(section: dict[str, Any]) -> str:
    """章节蓝图 + 评分深度 → 写法与篇幅指引（招标原文始终优先于蓝图范式）。"""
    value = section if isinstance(section, dict) else {}
    blueprint = value.get("sectionBlueprint")
    blueprint = blueprint if isinstance(blueprint, dict) else {}

    lines: list[str] = []
    name = str(blueprint.get("name") or "").strip()
    if name:
        lines.append(f"本章范式：{name}（通用范式；与招标原文冲突时以招标原文为准）")
    focus = str(blueprint.get("focus") or "").strip()
    if focus:
        lines.append(f"写法要点：{focus}")
    skeleton = blueprint.get("skeleton")
    if isinstance(skeleton, list) and skeleton:
        lines.append("建议骨架：" + " → ".join(str(item) for item in skeleton))
    pitfalls = blueprint.get("pitfalls")
    if isinstance(pitfalls, list) and pitfalls:
        lines.append("本章陷阱：" + "；".join(str(item) for item in pitfalls))

    depth = str(value.get("evaluationDepth") or "").strip()
    weight = value.get("evaluationWeight")
    if depth in _DEPTH_GUIDANCE:
        suffix = f"（本章分值权重 {weight}）" if weight not in (None, "") else ""
        lines.append(f"篇幅档位：{_DEPTH_GUIDANCE[depth]}{suffix}")
    elif blueprint.get("lengthPolicy"):
        lines.append(f"篇幅档位：{blueprint['lengthPolicy']}")
    return "\n".join(lines)


def _section_prompt_view(section: dict[str, Any], profile: str = "") -> dict[str, Any]:
    value = section if isinstance(section, dict) else {}
    resolved_profile = profile or resolve_writing_profile(value)
    fields = (
        "sectionId",
        "title",
        "source",
        "kind",
        "writingProfile",
        "materialScopes",
        "parentSectionId",
        "level",
        "order",
        "required",
        "responseType",
        "requirementIds",
        "evaluationIds",
        "evaluationScore",
        "evaluationWeight",
        "evaluationDepth",
        "sectionBlueprint",
        "templateId",
    )
    result = {
        key: value.get(key)
        for key in fields
        if key in value and value.get(key) not in (None, "", [])
    }
    result["writingProfile"] = resolved_profile
    result["materialScopes"] = list(material_keys_for_writing_profile(resolved_profile))
    return result


def _compact_project(value: dict[str, Any]) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    parsed = payload.get("parsed")
    parsed = parsed if isinstance(parsed, dict) else {}
    keys = (
        "id",
        "projectId",
        "title",
        "projectName",
        "projectCode",
        "tendererName",
        "agencyName",
        "budget",
        "deadline",
        "deliveryPeriod",
        "serviceLocation",
    )
    result: dict[str, Any] = {}
    for key in keys:
        item = payload.get(key) or parsed.get(key)
        if item not in (None, ""):
            result[key] = str(item)[:240]
    return result


def _compact_company(value: dict[str, Any]) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    keys = (
        "name",
        "company",
        "organizationName",
        "legalRepresentative",
        "unifiedSocialCreditCode",
        "registeredAddress",
        "enterpriseSize",
        "employeeCount",
        "description",
        "capabilities",
        "service_regions",
        "service_types",
        "service_industries",
    )
    result = {
        key: str(payload.get(key))[:320]
        for key in keys
        if payload.get(key) not in (None, "", [])
    }
    organization = payload.get("organization")
    if isinstance(organization, dict):
        result["organization"] = {
            key: str(organization.get(key))[:240]
            for key in (
                "organizationName",
                "legalRepresentative",
                "unifiedSocialCreditCode",
                "registeredAddress",
                "contactInfo",
            )
            if organization.get(key) not in (None, "", [])
        }
    return result


def _compact_material_record(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    fields = (
        "id",
        "factId",
        "materialId",
        "evidenceId",
        "factKey",
        "name",
        "description",
        "aliases",
        "category",
        "status",
        "verificationStatus",
        "validUntil",
        "evidencePath",
        "scope",
        "materialType",
    )
    result = {
        key: str(value.get(key))[:420]
        for key in fields
        if value.get(key) not in (None, "", [])
    }
    return result or None


def _compact_parse_bundle_for_review(value: dict[str, Any]) -> dict[str, Any]:
    """保留审查所需的 ID、状态和短文本，不把解析原文重复带入审查提示词。"""

    payload = value if isinstance(value, dict) else {}
    requirements = []
    for item in payload.get("requirements", []):
        if not isinstance(item, dict):
            continue
        requirement_id = str(item.get("id") or item.get("requirementId") or "").strip()
        if not requirement_id:
            continue
        requirements.append(
            {
                "id": requirement_id,
                "category": str(item.get("category") or "")[:40],
                "requirement": str(
                    item.get("requirement")
                    or item.get("normalizedRequirement")
                    or item.get("sourceText")
                    or ""
                )[:140],
                "mandatory": bool(item.get("mandatory")),
                "isRejection": bool(item.get("isRejection")),
                "responseType": str(item.get("responseType") or "")[:40],
            }
        )
    evaluations = []
    for item in payload.get("evaluationItems", []):
        if not isinstance(item, dict):
            continue
        evaluation_id = str(item.get("id") or item.get("evaluationId") or "").strip()
        if not evaluation_id:
            continue
        evaluations.append(
            {
                "id": evaluation_id,
                "criterion": str(item.get("criterion") or item.get("item") or "")[:160],
                "score": item.get("score"),
                "writingFocus": str(item.get("writingFocus") or "")[:140],
            }
        )
    structure = payload.get("documentStructure")
    structure = structure if isinstance(structure, dict) else {}
    sections = [
        {
            "sectionId": str(item.get("sectionId") or item.get("id") or "")[:80],
            "title": str(item.get("title") or item.get("name") or "")[:180],
            "level": item.get("level"),
            "sectionType": str(item.get("sectionType") or "")[:40],
        }
        for item in structure.get("sections", [])
        if isinstance(item, dict)
    ][:80]
    format_spec = payload.get("bidFormatSpec")
    format_spec = format_spec if isinstance(format_spec, dict) else {}
    required_outline = [
        {
            "formatItemId": str(item.get("formatItemId") or item.get("id") or "")[:80],
            "title": str(item.get("title") or item.get("name") or "")[:180],
            "type": str(item.get("type") or "")[:40],
            "templateId": str(item.get("templateId") or "")[:80],
        }
        for item in format_spec.get("requiredOutline", [])
        if isinstance(item, dict)
    ][:60]
    return {
        "parseBundleId": str(payload.get("parseBundleId") or "")[:100],
        "projectMeta": _compact_project(
            payload.get("projectMeta")
            if isinstance(payload.get("projectMeta"), dict)
            else {}
        ),
        "documentStructure": {"sections": sections},
        "requirements": requirements,
        "evaluationItems": evaluations[:100],
        "bidFormatSpec": {
            "hasExplicitFormat": bool(format_spec.get("hasExplicitFormat")),
            "formatSourceSection": str(format_spec.get("formatSourceSection") or "")[:180],
            "requiredOutline": required_outline,
        },
        "riskItems": [
            {
                "level": str(item.get("level") or "")[:20],
                "title": str(item.get("title") or "")[:120],
                "detail": str(item.get("detail") or "")[:240],
                "sourceLocator": str(item.get("sourceLocator") or "")[:100],
            }
            for item in payload.get("riskItems", [])
            if isinstance(item, dict)
        ][:30],
    }


def _compact_fine_review(value: dict[str, Any]) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    rows = []
    for item in payload.get("rows", []):
        if not isinstance(item, dict):
            continue
        requirement_id = str(item.get("requirementId") or item.get("id") or "").strip()
        if not requirement_id:
            continue
        rows.append(
            {
                "requirementId": requirement_id,
                "category": str(item.get("category") or "")[:40],
                "status": str(item.get("status") or "")[:30],
                "mandatory": bool(item.get("mandatory")),
                "isRejection": bool(item.get("isRejection")),
                "riskLevel": str(item.get("riskLevel") or item.get("risk") or "")[:20],
                "evidenceCoverageStatus": str(
                    item.get("evidenceCoverageStatus") or ""
                )[:40],
                "evidence": str(item.get("evidence") or "")[:160],
                "matchedMaterialIds": [
                    str(entry)[:100]
                    for entry in item.get("matchedMaterialIds", [])
                    if str(entry).strip()
                ][:12],
            }
        )
    result = {
        "rows": rows,
        "hardBlockers": [str(item)[:180] for item in payload.get("hardBlockers", [])][:20],
        "rejectionRisks": [str(item)[:180] for item in payload.get("rejectionRisks", [])][:20],
        "unresolvedMandatory": [
            str(item)[:180] for item in payload.get("unresolvedMandatory", [])
        ][:20],
    }
    return result


def _compact_bid_document_for_review(value: dict[str, Any]) -> dict[str, Any]:
    payload = value if isinstance(value, dict) else {}
    plan = []
    for item in payload.get("plan", []):
        if not isinstance(item, dict):
            continue
        refs = []
        for ref in item.get("sourceRefs", []):
            if not isinstance(ref, dict):
                continue
            refs.append(
                {
                    key: str(ref.get(key) or "")[:160]
                    for key in ("factKey", "factId", "materialId", "name")
                    if ref.get(key)
                }
            )
        plan.append(
            {
                "sectionId": str(item.get("sectionId") or "")[:80],
                "title": str(item.get("title") or "")[:180],
                "kind": str(item.get("kind") or "")[:40],
                "writingProfile": str(item.get("writingProfile") or "")[:40],
                "source": str(item.get("source") or "")[:40],
                "status": str(item.get("status") or "")[:30],
                "requirementIds": [
                    str(entry)[:80] for entry in item.get("requirementIds", []) if str(entry).strip()
                ][:50],
                "evaluationIds": [
                    str(entry)[:80] for entry in item.get("evaluationIds", []) if str(entry).strip()
                ][:50],
                "content": str(item.get("content") or "")[:1800],
                "sourceRefs": refs[:20],
                "blanksToFill": [
                    str(entry)[:180] for entry in item.get("blanksToFill", []) if str(entry).strip()
                ][:20],
            }
        )
    coverage = []
    for item in payload.get("requirementCoverage", []):
        if not isinstance(item, dict):
            continue
        coverage.append(
            {
                "requirementId": str(item.get("requirementId") or "")[:80],
                "sectionId": str(item.get("sectionId") or "")[:80],
                "status": str(item.get("status") or "")[:30],
                "coverageType": str(item.get("coverageType") or "")[:40],
                "evaluationIds": [
                    str(entry)[:80] for entry in item.get("evaluationIds", []) if str(entry).strip()
                ][:12],
            }
        )
    return {
        "status": str(payload.get("status") or "")[:30],
        "version": payload.get("version"),
        "mode": str(payload.get("mode") or "")[:20],
        "plan": plan[:80],
        "requirementCoverage": coverage,
        "complianceReview": {
            "status": str(
                (payload.get("complianceReview") or {}).get("status")
                if isinstance(payload.get("complianceReview"), dict)
                else ""
            )[:30]
        },
    }


def _agent_context(
    *,
    filename: str,
    workspace_id: str,
    project_id: str,
    project_context: dict[str, Any],
) -> str:
    return f"""
文件名：{filename}
工作区：{workspace_id}
项目 ID：{project_id}
项目上下文：
{_dump(project_context, 5000)}
""".strip()


def _writing_parse_bundle_view(parse_bundle: dict[str, Any]) -> dict[str, Any]:
    """为目录和条款映射提供精简上下文，避免 documentBlocks 挤掉要求清单。"""

    payload = parse_bundle if isinstance(parse_bundle, dict) else {}

    def compact_requirement(item: Any) -> dict[str, Any]:
        value = item if isinstance(item, dict) else {}
        return {
            "id": str(value.get("id") or value.get("requirementId") or ""),
            "category": str(value.get("category") or ""),
            "requirement": str(
                value.get("requirement")
                or value.get("normalizedRequirement")
                or value.get("sourceText")
                or ""
            )[:280],
            "mandatory": bool(value.get("mandatory")),
            "isRejection": bool(value.get("isRejection")),
            "importance": str(value.get("importance") or ""),
            "responseType": str(value.get("responseType") or ""),
            "sourceLocator": str(value.get("sourceLocator") or "")[:160],
            "sourceSection": str(value.get("sourceSection") or value.get("section") or "")[:100],
        }

    def compact_evaluation(item: Any) -> dict[str, Any]:
        value = item if isinstance(item, dict) else {}
        return {
            "id": str(value.get("id") or value.get("evaluationId") or ""),
            "category": str(value.get("category") or ""),
            "score": value.get("score"),
            "criterion": str(value.get("criterion") or value.get("item") or "")[:420],
            "criteria": str(value.get("criteria") or "")[:520],
            "writingFocus": str(value.get("writingFocus") or "")[:420],
            "sourceLocator": str(value.get("sourceLocator") or "")[:160],
            "relatedRequirementIds": [
                str(entry)
                for entry in value.get("relatedRequirementIds", [])
                if str(entry).strip()
            ][:20],
        }

    format_spec = payload.get("bidFormatSpec")
    format_spec = format_spec if isinstance(format_spec, dict) else {}
    compact_format = {
        "hasExplicitFormat": bool(format_spec.get("hasExplicitFormat")),
        "formatSourceSection": str(format_spec.get("formatSourceSection") or "")[:240],
        "requiredOutline": [
            item
            for item in format_spec.get("requiredOutline", [])
            if isinstance(item, dict)
        ][:40],
        "templates": [
            {
                "templateId": str(item.get("templateId") or ""),
                "title": str(item.get("title") or "")[:240],
                "templateType": str(item.get("templateType") or item.get("type") or ""),
                "sourceLocator": str(item.get("sourceLocator") or "")[:240],
                "editableFields": [
                    field
                    for field in item.get("editableFields", [])
                    if isinstance(field, dict)
                ][:16],
                "lockedBlocks": [
                    block
                    for block in item.get("lockedBlocks", [])
                    if isinstance(block, dict)
                ][:16],
            }
            for item in format_spec.get("templates", [])
            if isinstance(item, dict)
        ][:20],
        "requiredTables": [
            item
            for item in format_spec.get("requiredTables", [])
            if isinstance(item, dict)
        ][:30],
    }

    document_structure = payload.get("documentStructure")
    document_structure = (
        document_structure if isinstance(document_structure, dict) else {}
    )
    compact_structure = {
        "sections": [
            {
                "sectionId": str(item.get("sectionId") or item.get("id") or ""),
                "title": str(item.get("title") or item.get("name") or "")[:240],
                "level": item.get("level"),
                "sectionType": str(item.get("sectionType") or ""),
                "sourceLocator": str(item.get("sourceLocator") or "")[:240],
            }
            for item in document_structure.get("sections", [])
            if isinstance(item, dict)
        ][:100],
        "warnings": [
            str(item)
            for item in document_structure.get("warnings", [])
            if str(item).strip()
        ][:20],
    }

    return {
        "parseBundleId": str(payload.get("parseBundleId") or ""),
        "schemaVersion": str(payload.get("schemaVersion") or "1.0"),
        "provider": str(payload.get("provider") or ""),
        "projectId": str(payload.get("projectId") or ""),
        "workspaceId": str(payload.get("workspaceId") or ""),
        "projectMeta": payload.get("projectMeta")
        if isinstance(payload.get("projectMeta"), dict)
        else {},
        "documentStructure": compact_structure,
        "requirements": [
            compact_requirement(item)
            for item in payload.get("requirements", [])
            if isinstance(item, dict)
        ],
        "evaluationItems": [
            compact_evaluation(item)
            for item in payload.get("evaluationItems", [])
            if isinstance(item, dict)
        ][:120],
        "bidFormatSpec": compact_format,
        "rawOutline": [
            item
            for item in payload.get("rawOutline", [])
            if isinstance(item, dict)
        ][:60],
        "riskItems": [
            item
            for item in payload.get("riskItems", [])
            if isinstance(item, dict)
        ][:40],
        "parseReviewReport": payload.get("parseReviewReport")
        if isinstance(payload.get("parseReviewReport"), dict)
        else {},
        "warnings": [
            str(item)
            for item in payload.get("warnings", [])
            if str(item).strip()
        ][:40],
    }


def _outline_instruction_context(parse_bundle: dict[str, Any]) -> dict[str, Any]:
    """从文档章节和已解析要求中提取直接影响目录的编制依据。"""

    payload = parse_bundle if isinstance(parse_bundle, dict) else {}
    document_structure = payload.get("documentStructure")
    document_structure = document_structure if isinstance(document_structure, dict) else {}
    sections = payload.get("outlineSourceSections")
    if not isinstance(sections, list) or not sections:
        sections = document_structure.get("sections")
    sections = sections if isinstance(sections, list) else []

    def text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]

    normalized_terms = tuple(re.sub(r"\s+", "", term) for term in _OUTLINE_SOURCE_TERMS)

    def matches(value: Any) -> bool:
        compact = re.sub(r"\s+", "", str(value or ""))
        return any(term in compact for term in normalized_terms)

    source_sections: list[dict[str, Any]] = []
    seen_sections: set[str] = set()
    total_chars = 0
    for item in sections:
        if not isinstance(item, dict):
            continue
        heading = text(item.get("heading") or item.get("title") or item.get("name"), 240)
        body = str(
            item.get("text")
            or item.get("content")
            or item.get("body")
            or item.get("rawText")
            or ""
        ).strip()
        section_type = text(item.get("sectionType"), 40)
        title_match = matches(heading) or section_type in {
            "document_format",
            "submission",
            "submission_format",
        }
        body_match = matches(body)
        if not title_match and not body_match:
            continue
        section_id = text(item.get("sectionId") or item.get("id"), 80)
        locator = text(
            item.get("sourceLocator")
            or item.get("startLocator")
            or item.get("sectionPath"),
            180,
        )
        identity = section_id or locator or heading
        if not identity or identity in seen_sections:
            continue
        seen_sections.add(identity)
        remaining = 20000 - total_chars
        if remaining <= 0:
            break
        excerpt = re.sub(r"\s+", " ", body).strip()
        excerpt = excerpt[: min(5000, remaining)]
        row = {
            "sectionId": section_id,
            "heading": heading,
            "sourceLocator": locator,
            "selectionReason": "章节标题直接命中编制/组成/格式要求"
            if title_match
            else "章节正文包含编制/组成/格式要求",
        }
        if excerpt:
            row["text"] = excerpt
            total_chars += len(excerpt)
        source_sections.append(row)
        if len(source_sections) >= 16:
            break

    requirement_rows: list[dict[str, Any]] = []
    for item in payload.get("requirements", []):
        if not isinstance(item, dict):
            continue
        requirement = text(
            item.get("requirement")
            or item.get("normalizedRequirement")
            or item.get("sourceText"),
            520,
        )
        source_section = text(item.get("sourceSection") or item.get("section"), 180)
        locator = text(item.get("sourceLocator"), 180)
        if not (matches(source_section) or matches(locator) or matches(requirement)):
            continue
        requirement_rows.append(
            {
                "id": text(item.get("id") or item.get("requirementId"), 80),
                "requirement": requirement,
                "category": text(item.get("category"), 32),
                "mandatory": bool(item.get("mandatory")),
                "isRejection": bool(item.get("isRejection")),
                "sourceSection": source_section,
                "sourceLocator": locator,
            }
        )
        if len(requirement_rows) >= 60:
            break

    return {
        "sourceSections": source_sections,
        "requirements": requirement_rows,
        "sourceSectionCount": len(source_sections),
        "matchedRequirementCount": len(requirement_rows),
    }


def _planning_parse_bundle_view(
    parse_bundle: dict[str, Any],
    *,
    purpose: str = "outline",
) -> dict[str, Any]:
    """为目录规划/条款映射提供更紧凑但完整的 ParseBundle 视图。

    目录规划需要保留全部 requirement ID，不能简单截断长文档的要求清单；
    但 documentBlocks、风险详情和重复定位信息会显著放大提示词，且不影响
    章节安排。这里只保留规划决策所需的字段，避免 Pi 因输入过大超时。
    """

    payload = parse_bundle if isinstance(parse_bundle, dict) else {}

    def text(value: Any, limit: int) -> str:
        return " ".join(str(value or "").split())[:limit]

    project_meta = payload.get("projectMeta")
    project_meta = project_meta if isinstance(project_meta, dict) else {}
    compact_meta = {
        key: text(project_meta.get(key), 180)
        for key in (
            "projectName",
            "projectCode",
            "tendererName",
            "agencyName",
            "budget",
            "deadline",
            "deliveryPeriod",
            "serviceLocation",
        )
        if text(project_meta.get(key), 180)
    }

    is_outline = purpose == "outline"
    requirements: list[dict[str, Any]] = []
    requirement_category_counts: dict[str, int] = {}
    requirement_response_counts: dict[str, int] = {}
    for item in payload.get("requirements", []):
        if not isinstance(item, dict):
            continue
        requirement_id = text(item.get("id") or item.get("requirementId"), 80)
        requirement = text(
            item.get("requirement")
            or item.get("normalizedRequirement")
            or item.get("sourceText"),
            110 if is_outline else 160,
        )
        if not requirement_id and not requirement:
            continue
        item_value = {
            "id": requirement_id,
            "category": text(item.get("category"), 32),
            "text": requirement[:80 if is_outline else 120],
        }
        if bool(item.get("mandatory")):
            item_value["m"] = True
        if bool(item.get("isRejection")):
            item_value["r"] = True
        response_type = text(item.get("responseType"), 32)
        if response_type:
            item_value["response"] = response_type
        category = str(item_value.get("category") or "other")
        requirement_category_counts[category] = (
            requirement_category_counts.get(category, 0) + 1
        )
        if response_type:
            requirement_response_counts[response_type] = (
                requirement_response_counts.get(response_type, 0) + 1
            )
        requirements.append(item_value)

    evaluations: list[dict[str, Any]] = []
    for item in payload.get("evaluationItems", []):
        if not isinstance(item, dict):
            continue
        evaluation_value = {
            "id": text(item.get("id") or item.get("evaluationId"), 80),
            "score": item.get("score"),
            "criterion": text(item.get("criterion") or item.get("item"), 130 if is_outline else 170),
            "focus": text(item.get("writingFocus"), 120 if is_outline else 160),
            "relatedRequirementIds": [
                text(entry, 80)
                for entry in item.get("relatedRequirementIds", [])
                if text(entry, 80)
            ][:12],
        }
        if not is_outline:
            evaluation_value["category"] = text(item.get("category"), 32)
            evaluation_value["criteria"] = text(item.get("criteria"), 180)
            evaluation_value["sourceLocator"] = text(item.get("sourceLocator"), 80)
        evaluations.append(evaluation_value)

    document_structure = payload.get("documentStructure")
    document_structure = (
        document_structure if isinstance(document_structure, dict) else {}
    )
    raw_sections = payload.get("outlineSourceSections")
    if not isinstance(raw_sections, list) or not raw_sections:
        raw_sections = document_structure.get("sections", [])
    sections: list[dict[str, Any]] = []
    for item in raw_sections:
        if not isinstance(item, dict):
            continue
        section_value = {
            "sectionId": text(item.get("sectionId") or item.get("id"), 80),
            "title": text(item.get("heading") or item.get("title") or item.get("name"), 140),
            "level": item.get("level") if isinstance(item.get("level"), int) else 1,
            "sectionType": text(item.get("sectionType"), 40),
        }
        if not is_outline:
            section_value["sourceLocator"] = text(
                item.get("sourceLocator") or item.get("startLocator"), 80
            )
        sections.append(section_value)

    format_spec = payload.get("bidFormatSpec")
    format_spec = format_spec if isinstance(format_spec, dict) else {}
    required_outline = [
        {
            "formatItemId": text(item.get("formatItemId") or item.get("id"), 80),
            "title": text(item.get("title") or item.get("name"), 140),
            "type": text(item.get("type"), 40),
            "templateId": text(item.get("templateId"), 80),
            "sourceLocator": text(item.get("sourceLocator"), 100),
        }
        for item in format_spec.get("requiredOutline", [])
        if isinstance(item, dict)
    ][:60]
    required_tables = [
        {
            "tableId": text(item.get("tableId") or item.get("id"), 80),
            "title": text(item.get("title") or item.get("name"), 140),
            "sourceLocator": text(item.get("sourceLocator"), 100),
        }
        for item in format_spec.get("requiredTables", [])
        if isinstance(item, dict)
    ][:40]

    all_requirement_ids = [
        str(item.get("id") or "")
        for item in requirements
        if str(item.get("id") or "").strip()
    ]
    if is_outline:
        critical = [
            item
            for item in requirements
            if item.get("m") or item.get("r")
        ]
        representatives: list[dict[str, Any]] = []
        seen_categories: set[str] = set()
        for item in requirements:
            category = str(item.get("category") or "other")
            if category in seen_categories:
                continue
            representatives.append(item)
            seen_categories.add(category)
        selected_requirements: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for item in critical + representatives[:12]:
            item_id = str(item.get("id") or "")
            if item_id and item_id not in seen_ids:
                selected_requirements.append(item)
                seen_ids.add(item_id)
        requirements = selected_requirements

    result = {
        "parseBundleId": text(payload.get("parseBundleId"), 100),
        "schemaVersion": text(payload.get("schemaVersion"), 30) or "1.0",
        "provider": text(payload.get("provider"), 30),
        "projectId": text(payload.get("projectId"), 100),
        "workspaceId": text(payload.get("workspaceId"), 100),
        "projectMeta": compact_meta,
        "submissionStructure": payload.get("submissionStructure") or format_spec.get("submissionStructure") or {},
        "documentStructure": {
            "sections": sections[:50 if is_outline else 80],
            "warnings": [text(item, 180) for item in document_structure.get("warnings", []) if text(item, 180)][:8],
        },
        "requirements": requirements,
        "evaluationItems": evaluations[:80 if is_outline else 100],
        "bidFormatSpec": {
            "hasExplicitFormat": bool(format_spec.get("hasExplicitFormat")),
            "formatSourceSection": text(format_spec.get("formatSourceSection"), 160),
            "requiredOutline": required_outline[:50],
            "requiredTables": required_tables[:25],
            "templates": [
                {
                    "templateId": text(item.get("templateId"), 80),
                    "title": text(item.get("title"), 140),
                    "templateType": text(item.get("templateType") or item.get("type"), 50),
                    "sourceLocator": text(item.get("sourceLocator"), 100),
            }
            for item in format_spec.get("templates", [])
            if isinstance(item, dict)
        ][:20],
        },
        "rawOutline": [
            {
                "sectionId": text(item.get("sectionId") or item.get("id"), 80),
                "title": text(item.get("title") or item.get("name"), 140),
                "level": item.get("level") if isinstance(item.get("level"), int) else 1,
                "sourceLocator": text(item.get("sourceLocator"), 100),
            }
            for item in payload.get("rawOutline", [])
            if isinstance(item, dict)
        ][:80],
        "warnings": [text(item, 300) for item in payload.get("warnings", []) if text(item, 300)][:20],
    }
    if is_outline:
        result["allRequirementIds"] = all_requirement_ids
        result["requirementSummary"] = {
            "total": len(all_requirement_ids),
            "mandatory": sum(1 for item in payload.get("requirements", []) if isinstance(item, dict) and item.get("mandatory")),
            "rejection": sum(1 for item in payload.get("requirements", []) if isinstance(item, dict) and item.get("isRejection")),
            "byCategory": requirement_category_counts,
            "byResponseType": requirement_response_counts,
        }
        result["outlineInstructionContext"] = _outline_instruction_context(payload)
    if purpose == "map":
        result["documentStructure"] = {"sections": []}
        result["bidFormatSpec"] = {
            "requiredOutline": required_outline[:30],
            "requiredTables": required_tables[:15],
        }
        result["rawOutline"] = result["rawOutline"][:30]
    return result


def _section_parse_bundle_view(
    parse_bundle: dict[str, Any],
    *,
    section: dict[str, Any],
    coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    """为单章节编排裁剪解析包，只保留当前章节所需条款和模板信息。"""

    base = _writing_parse_bundle_view(parse_bundle)
    section_value = section if isinstance(section, dict) else {}
    section_id = str(section_value.get("sectionId") or "")
    requirement_ids = {
        str(item.get("requirementId") or item.get("id") or "").strip()
        for item in coverage
        if isinstance(item, dict)
        and str(item.get("sectionId") or "").strip() in {"", section_id}
        and str(item.get("requirementId") or item.get("id") or "").strip()
    }
    requirement_ids.update(
        str(item).strip()
        for item in section_value.get("requirementIds", [])
        if str(item).strip()
    )
    evaluation_ids = {
        str(item).strip()
        for item in section_value.get("evaluationIds", [])
        if str(item).strip()
    }
    for item in coverage:
        if not isinstance(item, dict):
            continue
        if str(item.get("sectionId") or "").strip() not in {"", section_id}:
            continue
        evaluation_ids.update(
            str(value).strip()
            for value in item.get("evaluationIds", [])
            if str(value).strip()
        )

    base["requirements"] = [
        item
        for item in base.get("requirements", [])
        if isinstance(item, dict)
        and str(item.get("id") or "").strip() in requirement_ids
    ]
    raw_requirements = [
        item
        for item in parse_bundle.get("requirements", [])
        if isinstance(item, dict)
        and str(item.get("id") or item.get("requirementId") or "").strip()
        in requirement_ids
    ]
    source_text_by_id = {
        str(item.get("id") or item.get("requirementId") or "").strip():
        str(item.get("sourceText") or "")[:900]
        for item in raw_requirements
        if str(item.get("sourceText") or "").strip()
    }
    for item in base["requirements"]:
        if isinstance(item, dict):
            source_text = source_text_by_id.get(str(item.get("id") or "").strip())
            if source_text:
                item["sourceText"] = source_text
    base["evaluationItems"] = [
        item
        for item in base.get("evaluationItems", [])
        if isinstance(item, dict)
        and (
            not evaluation_ids
            or str(item.get("id") or "").strip() in evaluation_ids
            or requirement_ids.intersection(
                set(str(value).strip() for value in item.get("relatedRequirementIds", []))
            )
        )
    ]
    raw_evaluations = [
        item
        for item in parse_bundle.get("evaluationItems", [])
        if isinstance(item, dict)
        and (
            str(item.get("id") or item.get("evaluationId") or "").strip()
            in evaluation_ids
            or requirement_ids.intersection(
                str(value).strip()
                for value in item.get("relatedRequirementIds", [])
                if str(value).strip()
            )
        )
    ]
    template_id = str(
        section_value.get("templateId")
        or section_value.get("templateFragmentId")
        or ""
    ).strip()
    if template_id:
        base["bidFormatSpec"]["templates"] = [
            item
            for item in base.get("bidFormatSpec", {}).get("templates", [])
            if isinstance(item, dict)
            and str(item.get("templateId") or "").strip() == template_id
        ]
    else:
        base["bidFormatSpec"]["templates"] = []
    source_locators = {
        " ".join(str(item.get("sourceLocator") or "").split())
        for item in [*raw_requirements, *raw_evaluations]
        if str(item.get("sourceLocator") or "").strip()
    }
    target_block_ids = {
        str(value).strip()
        for value in section_value.get("blockIds", [])
        if str(value).strip()
    }
    document_structure_source = parse_bundle.get("documentStructure")
    document_structure_source = (
        document_structure_source if isinstance(document_structure_source, dict) else {}
    )
    target_title = " ".join(str(section_value.get("title") or "").split())
    for item in document_structure_source.get("sections", []):
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or item.get("heading") or "").split())
        if (
            str(item.get("sectionId") or "").strip() == section_id
            or (target_title and title == target_title)
        ):
            locator = " ".join(
                str(item.get("sourceLocator") or item.get("startLocator") or "").split()
            )
            if locator:
                source_locators.add(locator)
            target_block_ids.update(
                str(value).strip()
                for value in item.get("blockIds", item.get("segmentIds", []))
                if str(value).strip()
            )
    if template_id:
        raw_format_spec = parse_bundle.get("bidFormatSpec")
        raw_format_spec = raw_format_spec if isinstance(raw_format_spec, dict) else {}
        template = next(
            (
                item
                for item in raw_format_spec.get("templates", [])
                if isinstance(item, dict)
                and str(item.get("templateId") or "").strip() == template_id
            ),
            {},
        )
        locator = " ".join(str(template.get("sourceLocator") or "").split())
        if locator:
            source_locators.add(locator)
        target_block_ids.update(
            str(value).strip()
            for value in template.get("blockIds", [])
            if str(value).strip()
        )

    document_blocks = parse_bundle.get("documentBlocks")
    document_blocks = document_blocks if isinstance(document_blocks, dict) else {}
    evidence_blocks = document_blocks.get("evidenceBlocks")
    if not isinstance(evidence_blocks, list) or not evidence_blocks:
        evidence_blocks = document_blocks.get("units")
    if not isinstance(evidence_blocks, list) or not evidence_blocks:
        evidence_blocks = document_blocks.get("segments")
    evidence_blocks = evidence_blocks if isinstance(evidence_blocks, list) else []
    selected_evidence: list[dict[str, Any]] = []
    seen_evidence_ids: set[str] = set()
    evidence_char_count = 0
    normalized_locators = {
        re.sub(r"\s+", "", locator)
        for locator in source_locators
        if locator
    }
    for block in evidence_blocks:
        if not isinstance(block, dict):
            continue
        block_id = str(block.get("id") or "").strip()
        locator = " ".join(str(block.get("sourceLocator") or "").split())
        normalized_locator = re.sub(r"\s+", "", locator)
        matched_locator = any(
            source_locator == normalized_locator
            or (
                min(len(source_locator), len(normalized_locator)) >= 3
                and (
                    source_locator in normalized_locator
                    or normalized_locator in source_locator
                )
            )
            for source_locator in normalized_locators
        )
        block_text = str(block.get("text") or "").strip()
        matched_heading = bool(
            target_title
            and block_text == target_title
            and str(block.get("contentType") or "").lower()
            in {"heading", "paragraph", ""}
        )
        if not (block_id in target_block_ids or matched_locator or matched_heading):
            continue
        if block_id and block_id in seen_evidence_ids:
            continue
        evidence = {
            "blockId": block_id,
            "sourceLocator": locator,
            "contentType": str(block.get("contentType") or ""),
            "text": block_text[:700],
        }
        if isinstance(block.get("cells"), list):
            evidence["cells"] = [
                str(value)[:120]
                for value in block["cells"][:4]
                if str(value).strip()
            ]
        evidence_size = len(json.dumps(evidence, ensure_ascii=False))
        if evidence_char_count + evidence_size > 9000:
            break
        selected_evidence.append(evidence)
        evidence_char_count += evidence_size
        if block_id:
            seen_evidence_ids.add(block_id)
        if len(selected_evidence) >= 24:
            break

    selected_source_texts = [
        re.sub(r"\s+", "", str(item.get("text") or ""))
        for item in selected_evidence
        if str(item.get("text") or "").strip()
    ]
    exact_requirement_evidence = []
    for item in raw_requirements:
        source_text = str(item.get("sourceText") or "").strip()
        normalized_source_text = re.sub(r"\s+", "", source_text)
        if not normalized_source_text or not any(
            normalized_source_text in selected_text
            for selected_text in selected_source_texts
        ):
            continue
        exact_requirement_evidence.append(
            {
                "requirementId": str(item.get("id") or item.get("requirementId") or ""),
                "sourceLocator": str(item.get("sourceLocator") or "")[:160],
                "text": source_text[:600],
            }
        )
        if len(exact_requirement_evidence) >= 12:
            break
    if selected_evidence or exact_requirement_evidence:
        base["sourceEvidence"] = {
            "blocks": selected_evidence,
            "requirementExcerpts": exact_requirement_evidence,
        }
    base["rawOutline"] = [
        item
        for item in base.get("rawOutline", [])
        if isinstance(item, dict)
        and (
            str(item.get("sectionId") or "").strip() == section_id
            or str(item.get("title") or "").strip()
            == str(section_value.get("title") or "").strip()
        )
    ]
    document_structure = base.get("documentStructure")
    if isinstance(document_structure, dict):
        document_structure["sections"] = [
            item
            for item in document_structure.get("sections", [])
            if isinstance(item, dict)
            and (
                str(item.get("sectionId") or "").strip() == section_id
                or str(item.get("title") or "").strip()
                == str(section_value.get("title") or "").strip()
            )
        ][:8]
        document_structure["warnings"] = document_structure.get("warnings", [])[:8]
    base["riskItems"] = [
        item
        for item in base.get("riskItems", [])
        if isinstance(item, dict)
    ][:8]
    base["warnings"] = [
        str(item)
        for item in base.get("warnings", [])
        if str(item).strip()
    ][:10]
    base["sectionContext"] = {
        "sectionId": section_id,
        "title": str(section_value.get("title") or "")[:240],
        "requirementIds": sorted(requirement_ids),
        "evaluationIds": sorted(evaluation_ids),
    }
    return base


def _section_knowledge_view(
    knowledge: dict[str, Any],
    *,
    material_ids: set[str] | None = None,
    writing_profile: str = "technical",
    max_items_per_group: int = 24,
) -> dict[str, Any]:
    """按撰写画像和匹配结果裁剪企业材料，避免重复注入完整知识库。"""

    payload = knowledge if isinstance(knowledge, dict) else {}
    allowed_keys = set(material_keys_for_writing_profile(writing_profile))

    def keep(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        ids = {
            str(value.get(key) or "").strip()
            for key in ("id", "factId", "materialId", "evidenceId")
            if str(value.get(key) or "").strip()
        }
        return bool(ids & material_ids)

    result: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in allowed_keys:
            continue
        if isinstance(value, list):
            compact_values = [
                _compact_material_record(item)
                for item in value
                if isinstance(item, dict)
                and (not material_ids or keep(item))
            ]
            result[key] = [
                item for item in compact_values if isinstance(item, dict)
            ][:max_items_per_group]
        elif isinstance(value, dict):
            if not material_ids or keep(value):
                result[key] = _compact_material_record(value) or value
    return result
