from __future__ import annotations

import json
import re
from typing import Any

from ..bid_writing.schemas import (
    material_keys_for_writing_profile,
    resolve_writing_profile,
)


def _dump(value: Any, limit: int = 60000) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[内容已截断]..."


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


# 评分深度档位 → 篇幅要求（取值来自 evaluation_strategy.depth_for_weight）
_DEPTH_GUIDANCE: dict[str, str] = {
    "primary": "重点章：分值占比高，必须写深写足，逐条响应本章评分点，不省略论证与佐证。",
    "regular": "常规章：评分点覆盖到位、论证清楚即可，不做冗余铺陈。",
    "minor": "次要章：覆盖评分点后即收束，不再扩写。",
}


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


PARSE_SCHEMA = {
    "projectMeta": {
        "projectName": "",
        "projectCode": "",
        "tendererName": "",
        "agencyName": "",
        "budget": "",
        "deadline": "",
    },
    "outline": [
        {
            "sectionId": "t-01",
            "title": "项目理解",
            "kind": "technical",
            "source": "ai",
            "requirementIds": [],
        }
    ],
    "requirements": [
        {
            "id": "req-001",
            "category": "technical",
            "requirement": "",
            "mandatory": False,
            "isRejection": False,
            "sourceLocator": "",
            "status": "待核验",
            "rawText": "",
            "section": "",
        }
    ],
    "evaluationItems": [
        {
            "factor": "",
            "item": "",
            "score": None,
            "criteria": "",
            "sourceLocator": "",
        }
    ],
    "riskItems": [
        {
            "level": "高",
            "title": "",
            "detail": "",
            "sourceLocator": "",
        }
    ],
    "warnings": [],
}


DOCUMENT_STRUCTURE_SCHEMA = {
    "sections": [
        {
            "sectionId": "doc-sec-001",
            "title": "第三章 评标办法",
            "level": 1,
            "startPage": 1,
            "endPage": 1,
            "sourceLocator": "第三章 评标办法",
            "blockIds": [],
            "sectionType": "evaluation",
        }
    ],
    "warnings": [],
}

PROJECT_META_SCHEMA = {
    "projectMeta": {
        "projectName": "",
        "projectCode": "",
        "tendererName": "",
        "agencyName": "",
        "budget": "",
        "deadline": "",
        "deliveryPeriod": "",
        "serviceLocation": "",
    },
    "sourceRefs": [],
    "warnings": [],
}

REQUIREMENT_SCHEMA = {
    "requirements": [
        {
            "id": "req-001",
            "category": "technical",
            "requirement": "",
            "mandatory": False,
            "isRejection": False,
            "importance": "medium",
            "responseType": "chapter_text",
            "sourceLocator": "",
            "sourceSection": "",
            "sourceText": "",
            "suggestedEvidence": [],
        }
    ],
    "warnings": [],
}

EVALUATION_SCHEMA = {
    "evaluationItems": [
        {
            "id": "eval-001",
            "category": "technical",
            "score": None,
            "criterion": "",
            "criteria": "",
            "writingFocus": "",
            "sourceLocator": "",
            "relatedRequirementIds": [],
        }
    ],
    "warnings": [],
}

TEMPLATE_SCHEMA = {
    "hasExplicitFormat": False,
    "formatSourceSection": "",
    "submissionStructure": {
        "mode": "unspecified",
        "confidence": "unknown",
        "sourceLocator": "",
        "volumes": [
            {
                "volumeId": "volume-01",
                "title": "技术文件",
                "order": 1,
                "required": True,
                "sourceLocator": "",
                "formatItemIds": [],
                "items": [
                    {
                        "formatItemId": "volume-01-01",
                        "title": "",
                        "order": 1,
                        "required": True,
                        "conditional": False,
                        "conditionText": "",
                        "itemType": "fixed_form",
                        "responseType": "fixed_form",
                        "templateType": "fixed_template",
                        "templateId": "tpl-volume-01-01",
                        "sourceLocator": "",
                    }
                ],
            }
        ],
        "warnings": [],
    },
    "requiredOutline": [
        {
            "formatItemId": "fmt-001",
            "volumeId": "volume-01",
            "title": "",
            "type": "free_text",
            "responseType": "chapter_text",
            "required": True,
            "conditional": False,
            "conditionText": "",
            "sourceLocator": "",
            "templateId": "",
            "templateType": "",
        }
    ],
    "templates": [
        {
            "templateId": "tpl-001",
            "title": "",
            "templateType": "fixed_template",
            "sourceLocator": "",
            "editableFields": [],
            "lockedBlocks": [],
            "blocks": [],
        }
    ],
    "requiredTables": [],
    "warnings": [],
}

PARSE_REVIEW_SCHEMA = {
    "status": "pass_with_warnings",
    "confidence": 0.0,
    "missingFields": [],
    "warnings": [],
    "riskItems": [
        {
            "level": "medium",
            "title": "",
            "detail": "",
            "sourceLocator": "",
        }
    ],
}

OUTLINE_PLAN_SCHEMA = {
    "plan": [
        {
            "sectionId": "volume-technical",
            "volumeId": "volume-technical",
            "nodeType": "volume",
            "parentSectionId": "",
            "level": 1,
            "order": 1,
            "title": "技术文件",
            "source": "template",
            "kind": "technical",
            "required": True,
            "responseType": "section_group",
            "requirementIds": ["req-001"],
            "evaluationIds": ["eval-001"],
            "templateId": "",
            "sourceSectionId": "",
            "sourceLocator": "",
        },
        {
            "sectionId": "t-01",
            "parentSectionId": "volume-technical",
            "level": 2,
            "order": 1,
            "title": "项目理解",
            "source": "ai",
            "kind": "technical",
            "required": True,
            "responseType": "chapter_text",
            "requirementIds": ["req-001"],
            "evaluationIds": [],
            "templateId": "",
        },
        {
            "sectionId": "t-01-01",
            "parentSectionId": "t-01",
            "level": 3,
            "order": 1,
            "title": "项目需求理解",
            "source": "ai",
            "kind": "technical",
            "responseType": "chapter_text",
            "requirementIds": [],
            "evaluationIds": [],
            "templateId": "",
        },
    ],
    "warnings": [],
}

REQUIREMENT_MAP_SCHEMA = {
    "coverage": [
        {
            "requirementId": "req-001",
            "sectionId": "t-01",
            "coverageType": "chapter_text",
            "status": "planned",
            "evaluationIds": ["eval-001"],
            "reason": "",
        }
    ],
    "uncoveredRequirements": [],
    "duplicatedRequirements": [],
    "warnings": [],
}

MATERIAL_MATCH_SCHEMA = {
    "matches": [
        {
            "requirementId": "req-001",
            "materialIds": ["qualification-001"],
            "materialRefs": [
                {
                    "materialId": "qualification-001",
                    "factKey": "qualifications",
                    "name": "",
                    "evidencePath": "",
                }
            ],
            "matchStatus": "matched",
            "evidenceSummary": "",
            "missingReason": "",
        }
    ],
    "unmatchedRequirementIds": [],
    "warnings": [],
}

SECTION_PLAN_SCHEMA = {
    "sectionId": "t-02",
    "title": "技术方案",
    "objective": "",
    "requirementResponses": [
        {
            "requirementId": "req-001",
            "responseStrategy": "",
            "mustInclude": [],
            "materialIds": [],
            "risk": "",
        }
    ],
    "evaluationResponses": [
        {
            "evaluationId": "eval-001",
            "focus": "",
            "evidenceIds": [],
        }
    ],
    "outline": [
        {
            "heading": "1.1 响应说明",
            "purpose": "",
            "requirementIds": [],
        }
    ],
    "templateInstructions": [],
    "warnings": [],
}

SECTION_DRAFT_SCHEMA = {
    "sectionId": "t-02",
    "title": "技术方案",
    "content": "Markdown 正文",
    "sourceRefs": [
        {
            "factKey": "",
            "factId": "",
            "name": "",
            "evidencePath": "",
        }
    ],
    "status": "generated",
    "warnings": [],
}

TABLE_FORM_FILL_SCHEMA = {
    "templateId": "tpl-001",
    "sectionId": "c-01",
    "status": "completed",
    "fieldValues": [
        {
            "fieldId": "field-001",
            "label": "项目名称",
            "sourceKey": "projectName",
            "value": "",
            "status": "filled",
            "sourceRefs": [],
        }
    ],
    "cellValues": [
        {
            "tableId": "TABLE-001",
            "rowIndex": 1,
            "cellIndex": 2,
            "label": "",
            "value": "",
            "status": "filled",
            "sourceRefs": [],
        }
    ],
    "blanksToFill": [],
    "warnings": [],
}

REQUIREMENT_RESPONSE_TABLE_SCHEMA = {
    "rows": [
        {
            "requirementId": "req-001",
            "response": "逐条、简洁、可核验的投标响应",
        }
    ],
    "warnings": [],
}

SECTION_REWRITE_SCHEMA = {
    "sectionId": "t-02",
    "operation": "append",
    "content": "",
    "replacement": "新增或替换的 Markdown 片段",
    "sourceRefs": [
        {
            "factKey": "",
            "factId": "",
            "name": "",
            "evidencePath": "",
        }
    ],
    "changed": True,
    "blanksToFill": [],
    "warnings": [],
}

COMPLIANCE_REVIEW_SCHEMA = {
    "status": "needs_review",
    "summary": "需要人工复核的风险摘要",
    "coverage": {
        "requirementTotal": 0,
        "requirementCovered": 0,
        "mandatoryTotal": 0,
        "mandatoryCovered": 0,
        "evaluationTotal": 0,
        "evaluationCovered": 0,
        "uncoveredRequirementIds": [],
        "uncoveredEvaluationIds": [],
    },
    "riskItems": [
        {
            "riskId": "risk-001",
            "level": "高",
            "category": "漏响应",
            "title": "风险标题",
            "detail": "风险说明",
            "sectionId": "",
            "requirementId": "",
            "evaluationId": "",
            "sourceLocator": "",
            "suggestion": "处理建议",
            "sourceRefs": [],
        }
    ],
    "warnings": [],
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


def document_structure_prompt(
    *,
    document_text: str,
    document_payload: dict[str, Any],
    filename: str,
    parser_warnings: list[str],
    workspace_id: str,
    project_id: str,
    project_context: dict[str, Any],
) -> str:
    return f"""
你是 DocumentStructureAgent，负责识别招标文件的章节结构。

只依据输入的招标文件内容和本地解析块，不得使用外部知识，不得编造章节。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
保留原文顺序；标题不确定时放入 warnings，不要猜测页码。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context=project_context)}

【本地解析器提示】
{_dump(parser_warnings, 5000)}

【本地结构化解析结果】
{_dump(document_payload, 16000)}

【招标文件文本】
{_dump(document_text, 10000)}

输出结构：
{_dump(DOCUMENT_STRUCTURE_SCHEMA, 8000)}

识别规则：
1. 识别招标公告、投标人须知、资格要求、评标办法、技术需求、商务要求、合同条款、投标文件格式、附件等章节。
2. level 使用 1、2、3 等整数；无法确认层级时使用 1。
3. sourceLocator 尽量保留原文中的章节、页码、表格名称。
4. sectionType 使用 announcement、qualification、evaluation、technical、commercial、contract、document_format、appendix 或 other。
5. 不要把普通长段落、表格行或联系人信息误判成章节。
6. 最多返回 80 个关键章节；优先保留一级、二级章节和业务章节，合并重复标题。
""".strip()


def project_meta_prompt(
    *,
    document_text: str,
    document_structure: dict[str, Any],
    filename: str,
    workspace_id: str,
    project_id: str,
    project_context: dict[str, Any],
) -> str:
    return f"""
你是 ProjectMetaAgent，负责从招标文件中抽取项目基础信息。

只依据输入内容，不得补全未知信息，不得使用外部知识。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
字段缺失使用空字符串；每个已填写字段尽量提供 sourceRefs。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context=project_context)}

【文档结构】
{_dump(document_structure, 24000)}

【招标文件文本】
{_dump(document_text, 24000)}

输出结构：
{_dump(PROJECT_META_SCHEMA, 7000)}

重点抽取：
1. 项目名称、项目编号、采购人/招标人、代理机构。
2. 预算或最高限价、投标截止时间、交付/服务期限、服务地点。
3. 不要把供应商资格条件误写成项目基础信息。
4. sourceRefs 中记录 sourceLocator、原文片段或章节。
""".strip()


def requirement_extract_prompt(
    *,
    document_text: str,
    document_structure: dict[str, Any],
    project_meta: dict[str, Any],
    filename: str,
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 RequirementExtractAgent，负责抽取所有需要投标方响应的原子要求。

只依据招标文件，不得使用外部知识，不得编造。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
每条要求必须是可执行的原子条款，不能把整章原文复制进 requirement。
每条要求必须有唯一 id，格式为 req-001、req-002……

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context=project_meta)}

【项目基础信息】
{_dump(project_meta, 10000)}

【文档结构】
{_dump(document_structure, 30000)}

【招标文件文本】
{_dump(document_text, 36000)}

输出结构：
{_dump(REQUIREMENT_SCHEMA, 10000)}

分类规则：
1. category 使用 qualification、technical、commercial、service、delivery、personnel、performance、financial、credit、pricing、contract、document、rejection 或 other。
2. mandatory：必须、应当、不得、否则无效、废标、否决、实质性要求等按语义判断。
3. isRejection：废标、无效投标、否决、实质性不响应等条款设为 true。
4. responseType 使用 chapter_text、table、attachment、commitment、fixed_form 或 other。
5. sourceLocator 和 sourceSection 必须尽量定位原文。
6. 不要把项目名称、联系人、公告时间等普通信息当成投标要求。
7. 当前输入可能是长文档的一个内容分片。最多返回 25 条原子要求；优先保留强制条款、废标条款、评分相关条款和明确交付要求，合并重复要求。
8. 根节点必须严格使用 requirements 和 warnings；即使只识别到一条，也必须放入 requirements 数组并闭合完整 JSON。
""".strip()


def evaluation_extract_prompt(
    *,
    document_text: str,
    document_structure: dict[str, Any],
    requirements: list[dict[str, Any]],
    filename: str,
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 EvaluationExtractAgent，负责抽取评标办法和评分细则。

只依据招标文件，不得使用外部知识，不得猜测分值。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
如果招标文件没有明确评分信息，返回空数组并在 warnings 中说明。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context={})}

【文档结构】
{_dump(document_structure, 26000)}

【已抽取要求，可用于关联】
{_dump(requirements, 36000)}

【招标文件文本】
{_dump(document_text, 18000)}

输出结构：
{_dump(EVALUATION_SCHEMA, 8000)}

规则：
1. score 必须来自原文；无法确认时使用 null。
2. criterion/criteria 保留评分标准的核心语义。
3. writingFocus 描述投标文件需要重点呈现的内容，但不能创造招标文件没有的评分条件。
4. relatedRequirementIds 只能引用输入 requirements 中已经存在的 id。
5. sourceLocator 必须标记评标办法、评分表、页码或表格位置。
6. 最多返回 100 个评分项；同一评分点的分档标准合并到 criteria，不要逐档拆成多条。
""".strip()


def template_detect_prompt(
    *,
    document_text: str,
    document_structure: dict[str, Any],
    filename: str,
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 TemplateDetectAgent，负责识别投标文件组成、分册规则、模板和固定表格。

只依据招标文件，不得把普通说明文字当作投标模板。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context={})}

【文档结构】
{_dump(document_structure, 30000)}

【招标文件文本】
{_dump(document_text, 30000)}

输出结构：
{_dump(TEMPLATE_SCHEMA, 12000)}

模板判断规则：
1. “投标文件格式”“响应文件组成”“投标函格式”“偏离表”“报价表”“承诺函”等优先判断为模板线索。
2. fixed_template：原样保留格式，只允许填充字段。
3. chapter_skeleton：保留章节标题、层级、表格和提示结构，正文可由智能体填充。
4. reference_example：只能作为写作参考，不得直接复制为企业承诺。
5. editableFields 记录字段名称、类型和建议来源，例如 projectMeta.projectName、company.name。
6. lockedBlocks 记录必须保留的固定文字、签字盖章位置、表头和格式要求。
7. requiredOutline 中的 templateId 必须引用 templates 中已存在的 templateId。
8. 最多返回 24 个 requiredOutline 项、12 个 templates 和 20 个 requiredTables，重复模板合并；每个模板的 editableFields、lockedBlocks 和 blocks 都只保留关键项，避免复制大段原文。
9. submissionStructure 中先列明招标文件要求的各卷/文件组成及顺序，再判断 mode：明确要求分别编制或独立提交才使用 separate；明确要求合卷才使用 single；没有原文依据用 unspecified。separate/single 必须有精确 sourceLocator，不能把普通的商务/技术章节标题当成独立分册要求。每卷记录来源位置、items 和关联的 formatItemIds；items 中保留招标文件原文列出的每一份文件。
10. 根节点必须包含 hasExplicitFormat、submissionStructure、requiredOutline、templates、requiredTables 和 warnings；未识别到分册规则时可以返回 mode=unspecified，但如果原文明确列出卷和卷内文件，必须保留 volumes[].items，不能因为无法判断是否独立提交而清空卷清单。
11. 如果输入的 submissionStructure 已有明确卷名、文件条目、顺序和 formatItemId，这些字段是权威值；不得改名、删除、合并或重复生成。只能补充 itemType、responseType、conditional、templateType、templateId 等空属性。
""".strip()


def parse_review_prompt(
    *,
    project_meta: dict[str, Any],
    document_structure: dict[str, Any],
    requirements: list[dict[str, Any]],
    evaluation_items: list[dict[str, Any]],
    bid_format_spec: dict[str, Any],
    filename: str,
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 ParseReviewAgent，负责检查前面解析智能体的结果质量。

只做解析质检，不要重新编写完整解析结果，不得使用外部知识。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
发现不确定内容必须报告，不要用猜测掩盖缺失。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context={})}

【项目基础信息】
{_dump(project_meta, 12000)}

【文档结构】
{_dump(document_structure, 30000)}

【招标要求】
{_dump(requirements, 65000)}

【评分项】
{_dump(evaluation_items, 30000)}

【投标模板】
{_dump(bid_format_spec, 36000)}

输出结构：
{_dump(PARSE_REVIEW_SCHEMA, 9000)}

检查内容：
1. 项目名称是否缺失或与正文冲突。
2. 是否抽取到资格、技术、商务、服务、废标/否决要求。
3. 评分项是否有分值、来源位置或明显缺失。
4. 投标文件格式是否识别到固定模板和表格。
5. requirements 是否存在重复、空内容、无来源位置或错误分类。
6. riskItems 只记录会影响后续目录、撰写或投标合规的风险。
""".strip()


def volume_plan_prompt(
    *,
    parse_bundle: dict[str, Any],
    existing_plan: list[dict[str, Any]],
    workspace_id: str,
    project_id: str,
) -> str:
    spec = parse_bundle.get("bidFormatSpec") or {}
    structure = parse_bundle.get("submissionStructure") or {}
    sections = (parse_bundle.get("documentStructure") or {}).get("sections") or []
    return f"""
你是 VolumePlanAgent，只规划投标文件的卷，不规划章、节或正文。
只输出合法 JSON 对象：{{"volumes":[{{"volumeId":"volume-01","title":"文件名称","order":1,"kind":"commercial","sourceLocator":"原文定位"}}],"warnings":[]}}。
工作区：{workspace_id}；项目：{project_id}
【解析确定的文件组成与分册规则】{_dump(structure, 5000)}
【明确格式目录】{_dump(spec.get("requiredOutline") or [], 10000)}
【投标文件编制依据】{_dump(_planning_parse_bundle_view(parse_bundle, purpose="outline").get("outlineInstructionContext", {}), 18000)}
【招标文件章节线索】{_dump(sections[:45], 12000)}
【已有目录顶层】{_dump([item for item in existing_plan if isinstance(item, dict) and not item.get("parentSectionId")][:12], 3500)}
规则：
1. 只有 confidence=explicit、structureStatus=valid，且来源定位位于“电子投标文件组成/投标文件格式/响应文件组成”的 submissionStructure，才是可信文件骨架。
2. 可信 submissionStructure 中的 volumeId、title、order 必须原样保留，不得把 requirements 或资格条件句改造成卷。
3. 如果 submissionStructure 的卷名是完整句子、资格条件、操作说明或没有卷内文件项，不得使用这些卷；应改用可信的原文格式章节，无法确认时使用“投标响应文件”单卷逻辑分组，并在 warnings 说明依据不足。
4. requirements、evaluationItems 只用于后续正文响应映射，不得在本阶段生成技术参数、评分点或资格条款卷。
5. 没有明确卷时可以根据可信格式线索规划逻辑卷，但不宣称需要独立提交。没有足够依据时返回一个“投标响应文件”逻辑卷。最多 6 卷；不要生成章、节、正文或新的分册规则。
""".strip()


def outline_plan_prompt(
    *,
    parse_bundle: dict[str, Any],
    existing_plan: list[dict[str, Any]],
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 OutlinePlanAgent，负责为一份招标文件规划可执行的投标文件目录。

你只负责生成章节目录和章节元数据，不撰写任何正文，不生成企业承诺。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或额外文字。
只能依据输入的 ParseBundle 和当前已有目录，不得使用外部知识，不得编造招标文件没有的强制格式。

工作区：{workspace_id}
项目：{project_id}

【目录规划用 ParseBundle 精简视图】
        {_dump(_planning_parse_bundle_view(parse_bundle, purpose="outline"), 90000)}
【目录修正要求（如有）】
{str(parse_bundle.get("outlineRepairFeedback") or "无")[:2000]}
【上游 VolumePlanAgent 确认的卷】
{_dump(parse_bundle.get("plannedVolumes") or [], 5000)}
字段约定：outlineInstructionContext.sourceSections 是投标文件编制、组成、格式或提交要求的原文片段；其中的来源定位优先于通用目录经验。其 requirements 是从这些章节定位到的要求。
requirements.text 是其他重点要求摘要，m=true 表示强制，r=true 表示废标/否决，response 是响应类型。
allRequirementIds 是完整要求 ID 清单；requirementSummary 是完整分类统计。
正文阶段会由 RequirementMapAgent 再逐条做覆盖核验，目录阶段不要为了覆盖每条要求而机械扩展节点。

【当前已有目录】
{_dump(existing_plan, 18000)}

输出结构：
{_dump(OUTLINE_PLAN_SCHEMA, 7000)}

规划规则：
0. 先原样使用上游 plannedVolumes 的 volumeId、title、order 建立卷，再在每卷下生成文件项。`plan` 必须非空；即使无法建立条款关联，也要先返回可执行的文件骨架并说明。
1. 目录依据优先级：可信 submissionStructure 的卷/文件项 > outlineInstructionContext.sourceSections 原文 > bidFormatSpec.requiredOutline/requiredTables > 有来源定位的 rawOutline。requirements 和 evaluationItems 只做后续映射，不能覆盖明确格式。
2. 当 submissionStructure.confidence=explicit 且 structureStatus=valid 时，必须原样保留全部卷、卷内文件项、顺序和固定表格，不得改写标题、合并卷或增加未有来源的顶级卷。
3. 如果 submissionStructure 的卷名是“具有……”“我单位……”“请投标人……”等完整资格/说明句，或卷全部没有卷内文件项，视为不可信；不得把它们输出为目录，需在 warnings 标记结构异常。
4. 只生成文件骨架：卷、招标文件明确列出的文件/表格、以及有明确原文依据的文件层级。不得把 requirements 中的“具有……能力”“应提供……”“否则……”直接作为卷、章或节。
5. 具体技术参数、资格条件、评分标准和否决条款在正文阶段通过 requirementIds/evaluationIds 绑定到已有响应文件节点；目录阶段不得按每条要求单独创建一个目录节点，也不要为了覆盖条款制造大量节点。
6. sourceSectionId/sourceLocator 尽量回填对应的原文章节 ID 和定位；推导出的分组标题可标记覆盖关系，但不得伪造原文定位。
7. 固定投标函、承诺函、资格表、报价表、响应表、偏离表等只有在招标文件明确要求或解析结构支持时才加入；固定表格、fixed_form、attachment 默认不生成无依据的正文子章节。
8. 对 narrative/chapter_text 文件项，只有当招标文件明确允许在该文件项下组织正文时，才生成少量必要的撰写节点；不能把每个技术参数单独生成一个节点。
9. `source` 只能使用 template、table、ai、deviation、attachment 或 copied_template；`kind` 只能使用 commercial、technical、deviation 或 attachment。
10. mandatory=true、isRejection=true 和评分项只需绑定到合适的现有响应节点，要求 ID/evaluation ID 必须来自输入，不得因此改变文件组成骨架。
11. 可以合并语义相近的派生撰写章节，但不能删除招标文件明确要求的文件项。
12. `plan` 是扁平数组；顶层只能是 `nodeType=volume`、`responseType=section_group` 的卷，`level=1`。每个非顶层节点都必须填写 parentSectionId，且 level 必须等于父级 level + 1。默认最多 5 级。
13. 输出顺序必须是先父后子；同一父级下的 `order` 从 1 连续编号。
14. 无明确格式时，按文档中明确的提交文件线索生成最小可用单卷骨架；没有依据时在 warnings 明确指出，不要猜测通用商务/技术目录。
15. 对目录修正要求，优先合并重复同级标题、删除仅为承载单条要求而生成的冗余末级节点；不能删除原文明确要求的文件项。
16. `required` 表示该章节是否为招标文件要求或完成投标文件所必需；`responseType` 使用 `section_group`、`chapter_text`、`fixed_form`、`table`、`attachment` 或 `deviation`。
17. 不要输出 content、正文、企业名称、人员、金额、日期或任何未在输入中出现的承诺。
""".strip()


def requirement_map_prompt(
    *,
    parse_bundle: dict[str, Any],
    outline_plan: list[dict[str, Any]],
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 RequirementMapAgent，负责建立招标要求、评分项与投标目录之间的覆盖关系。

你只负责映射和覆盖检查，不撰写正文，不修改招标原文，不编造企业能力。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或额外文字。
所有 ID 必须来自输入的 ParseBundle 或已给出的目录。

工作区：{workspace_id}
项目：{project_id}

【条款映射用 ParseBundle 精简视图】
        {_dump(_planning_parse_bundle_view(parse_bundle, purpose="map"), 90000)}
字段约定：requirements.text 是要求摘要，m=true 表示强制，r=true 表示废标/否决，response 是响应类型。

【OutlinePlanAgent 生成的目录】
{_dump(outline_plan, 30000)}

输出结构：
{_dump(REQUIREMENT_MAP_SCHEMA, 8000)}

映射规则：
0. 即使要求或评分项数量为 0，也必须返回合法的空数组；如果输入中存在要求，必须逐条输出 coverage 或 uncoveredRequirements。
1. 每一条 requirement 都要判断是否被目录覆盖；mandatory 或 isRejection 条款不能无理由遗漏。
2. coverageType 使用 chapter_text、table、attachment、commitment、fixed_form 或 other。
3. status 使用 planned、covered、uncovered 或 needs_review。
4. evaluationIds 只能引用 ParseBundle.evaluationItems 中存在的 id。
5. sectionId 只能引用目录中存在的 sectionId。
6. 同一条款确实需要多个章节响应时才放入 duplicatedRequirements，否则只保留一个主响应章节。
7. uncoveredRequirements 和 duplicatedRequirements 使用对象数组，至少包含 requirementId、reason。
8. reason 必须说明映射依据或风险，不得用空泛表述掩盖没有来源的判断。
""".strip()


def material_match_prompt(
    *,
    parse_bundle: dict[str, Any],
    coverage: list[dict[str, Any]],
    section: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    mode: str,
    workspace_id: str,
    project_id: str,
    writing_profile: str = "technical",
) -> str:
    profile = writing_profile or resolve_writing_profile(section)
    return f"""
你是 MaterialMatchAgent，负责为一个投标章节匹配企业材料和可引用证据。

只做材料匹配，不写正文，不把招标要求当成企业能力。
只引用下方已提供且真实存在的材料；找不到材料就返回 unmatched。
只输出合法 JSON，不要输出 Markdown、解释或额外文字。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}
{_profile_block(profile)}

【章节解析上下文】
{_dump(_section_parse_bundle_view(parse_bundle, section=section, coverage=coverage), 24000)}

【当前章节】
{_dump(_section_prompt_view(section, profile), 5000)}

【本章节要求覆盖关系】
{_dump(coverage, 14000)}

【项目关键字段】
{_dump(_compact_project(project), 4000)}

【企业关键字段】
{_dump(_compact_company(company), 6000)}

【可用企业材料（已按画像筛选）】
{_dump(_section_knowledge_view(knowledge, writing_profile=profile, max_items_per_group=32), 24000)}

输出结构：
{_dump(MATERIAL_MATCH_SCHEMA, 6000)}

匹配规则：
1. 只处理当前章节的 requirementIds 和 coverage。
2. materialIds 只能引用已提供材料中的 id、factId、materialId 或 evidenceId。
3. 每个匹配保留 factKey、name、evidencePath；没有证据路径不要猜。
4. matchStatus 只能是 matched、partial、unmatched、needs_review。
5. 不匹配时写入 unmatchedRequirementIds 和 missingReason。
6. mode=secret 时不输出企业名称、人员姓名、地址等敏感信息。
7. sourceEvidence 是招标文件原文证据，仅用于理解本章要求，不得将招标人的要求表述成投标人的既有能力。
""".strip()


def section_plan_prompt(
    *,
    parse_bundle: dict[str, Any],
    coverage: list[dict[str, Any]],
    section: dict[str, Any],
    material_matches: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    mode: str,
    workspace_id: str,
    project_id: str,
    writing_profile: str = "technical",
) -> str:
    profile = writing_profile or resolve_writing_profile(section)
    return f"""
你是 SectionPlanAgent，负责为一个投标章节制定可审核的写作计划。

只做写作计划，不写正文，不编造企业承诺，不修改招标要求。
计划必须让下游正文智能体逐条响应要求并引用真实材料。
只输出合法 JSON，不要输出 Markdown、解释或额外文字。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}
{_profile_block(profile)}

【本章蓝图与篇幅】
{_section_guidance_block(section) or "（无特定范式，按画像与本类重点撰写）"}

【章节解析上下文】
{_dump(_section_parse_bundle_view(parse_bundle, section=section, coverage=coverage), 22000)}

【当前章节】
{_dump(_section_prompt_view(section, profile), 5000)}

【条款覆盖关系】
{_dump(coverage, 12000)}

【材料匹配结果】
{_dump(material_matches, 14000)}

【项目关键字段】
{_dump(_compact_project(project), 3500)}

【企业关键字段】
{_dump(_compact_company(company), 5000)}

【企业材料】
{_dump(_section_knowledge_view(knowledge, material_ids={
    str(
        item.get("materialId")
        or item.get("factId")
        or item.get("evidenceId")
        or ""
    ).strip()
    for match in material_matches.get("matches", [])
    if isinstance(match, dict)
    for item in match.get("materialRefs", [])
    if isinstance(item, dict)
    and str(
        item.get("materialId")
        or item.get("factId")
        or item.get("evidenceId")
        or ""
    ).strip()
}, writing_profile=profile, max_items_per_group=16), 12000)}

输出结构：
{_dump(SECTION_PLAN_SCHEMA, 7000)}

规划规则：
1. sectionId/title 必须与当前章节一致。
2. requirementResponses 覆盖当前章节的所有要求；强制/否决条款必须给出策略。
3. evaluationIds 和 materialIds 只能引用输入中存在的 ID。
4. outline 只输出小节提纲，每个小节绑定 requirementIds 或 evaluationIds。
5. 资料不足时写入 risk 和人工补充点；不要假设企业能力。
6. mode=secret 时不安排企业名称、人员姓名、地址等敏感内容。
7. sourceEvidence 中的招标文件原文优先于概括性摘要；严格按其编制要求组织响应，不擅自扩展强制格式。
""".strip()


def section_draft_prompt(
    *,
    parse_bundle: dict[str, Any],
    coverage: list[dict[str, Any]],
    section: dict[str, Any],
    writing_plan: dict[str, Any],
    material_matches: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    mode: str,
    workspace_id: str,
    project_id: str,
    writing_profile: str = "technical",
) -> str:
    profile = writing_profile or resolve_writing_profile(section)
    return f"""
你是 SectionDraftAgent，负责根据已确认的写作计划生成一个投标章节正文。

只依据输入的章节要求、写作计划、项目字段和已匹配材料撰写。
严禁编造资质、证书、业绩、人员、金额、日期、参数、案例和承诺。
资料不足时写“【需人工补充：缺少具体资料】”，并在 warnings 说明。
企业事实必须有真实 sourceRefs；只输出合法 JSON，不要输出解释或代码块。
sourceEvidence 中的招标文件原文是本章节响应依据；应逐项落实其编制、格式、表格和提交要求，不得把招标要求改写成企业事实或承诺。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}
{_profile_block(profile)}

【本章蓝图与篇幅】
{_section_guidance_block(section) or "（无特定范式，按画像与本类重点撰写）"}

【章节解析上下文】
{_dump(_section_parse_bundle_view(parse_bundle, section=section, coverage=coverage), 22000)}

【当前章节】
{_dump(_section_prompt_view(section, profile), 5000)}

【条款覆盖关系】
{_dump(coverage, 12000)}

【写作计划】
{_dump(writing_plan, 16000)}

【材料匹配结果】
{_dump(material_matches, 12000)}

【项目关键字段】
{_dump(_compact_project(project), 3500)}

【企业关键字段】
{_dump(_compact_company(company), 5000)}

【企业材料】
{_dump(_section_knowledge_view(knowledge, material_ids={
    str(
        item.get("materialId")
        or item.get("factId")
        or item.get("evidenceId")
        or ""
    ).strip()
    for match in material_matches.get("matches", [])
    if isinstance(match, dict)
    for item in match.get("materialRefs", [])
    if isinstance(item, dict)
    and str(
        item.get("materialId")
        or item.get("factId")
        or item.get("evidenceId")
        or ""
    ).strip()
}, writing_profile=profile, max_items_per_group=16), 12000)}

输出结构：
{_dump(SECTION_DRAFT_SCHEMA, 5500)}

撰写规则：
1. sectionId/title 必须与当前章节一致。
2. 按 outline 顺序输出 Markdown 标题、段落、列表或表格。
3. 每条 requirementResponse 都要有明确响应，不用“完全满足”代替内容。
4. 评分项只呈现输入中的 focus，不新增评分条件。
5. 每个企业事实在 sourceRefs 中给出 materialId/factId、factKey、name、evidencePath。
6. status 只能是 generated、missing 或 needs_review。
""".strip()


def table_form_fill_prompt(
    *,
    parse_bundle: dict[str, Any],
    fragment: dict[str, Any],
    section: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    workspace_id: str,
    project_id: str,
) -> str:
    profile = resolve_writing_profile(section)
    return f"""
你是 TableFormFillAgent，负责给招标文件已经提供的固定模板和固定表格规划字段填充值。

只允许填写 editableFields 和模板表格中明确可编辑的单元格。
lockedBlocks、固定文字、表头、签字盖章位置、格式和原始顺序不能修改。
只依据模板、项目字段和真实企业材料填写。
不得编造企业名称、证书、业绩、人员、金额、日期、参数或承诺。
没有可靠来源时必须返回 missing 或 needs_confirmation，并把字段加入 blanksToFill。
只输出合法 JSON，不要输出 Markdown、解释或额外文字。

工作区：{workspace_id}
项目：{project_id}
{_profile_block(profile)}

【当前章节解析上下文】
{_dump(_section_parse_bundle_view(parse_bundle, section=section, coverage=[]), 16000)}

【当前章节】
{_dump(_section_prompt_view(section, profile), 5000)}

【模板片段】
{_dump(fragment, 24000)}

【项目资料】
{_dump(_compact_project(project), 4000)}

【企业画像】
{_dump(_compact_company(company), 6000)}

【企业材料与证据】
{_dump(_section_knowledge_view(knowledge, writing_profile=profile, max_items_per_group=24), 20000)}

输出结构：
{_dump(TABLE_FORM_FILL_SCHEMA, 7000)}

填充规则：
1. templateId 和 sectionId 必须与输入模板片段、当前章节保持一致。
2. fieldValues 只能引用 editableFields 中存在的 fieldId 或 label。
3. cellValues 只能引用模板片段中存在的 tableId、rowIndex、cellIndex；只填写明确可编辑的空位。
4. status 只能使用 filled、missing、needs_confirmation、locked；locked 字段不得给出替换值。
5. 每个 filled 值必须给出 sourceRefs，sourceRefs 只能引用输入中真实存在的材料或项目字段。
6. 项目名称、项目编号、招标人等项目字段可以引用 project 或 ParseBundle.projectMeta；招标文件中的预算、服务地点、服务期限不能直接当作投标人的报价或履约承诺。
7. 企业名称、统一社会信用代码、法定代表人、资质、业绩、人员、账户等只能引用 company、knowledge 或组织资料。
8. 对报价、银行账号、签字、盖章、日期、服务期限、服务地点和承诺等没有投标人明确确认的字段，返回 needs_confirmation，保留空位并加入 blanksToFill。
9. 不要输出 content，不要重写模板正文，不要创建模板中不存在的字段。
""".strip()


def requirement_response_table_prompt(
    *,
    section: dict[str, Any],
    requirements: list[dict[str, Any]],
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 RequirementResponseTableAgent，负责撰写技术和服务要求响应表中的“投标应答”单元格。

本次输入中的招标条款和企业材料已经由上游解析、匹配完成。不得重新解析招标文件、重新检索材料库或补充任何外部知识。
每条要求只能使用该条 requirements[*].materials 中给出的 M3 企业材料，不得借用其他条款的材料。
匹配状态和差距仅用于判断是否需要谨慎表述，不能代替材料事实，也不能据此宣称材料未明确证明的指标已经满足。
不得编造型号、参数、数量、日期、服务承诺或证据。材料没有明确写出的数字和边界条件不得补写。
响应应直接对应原要求，写成可放入表格的简洁完整句，不要重复整段招标原文，不要只写“满足”。
材料不足以支撑要求时，明确说明已有材料能证明的范围，并写“具体参数/范围待核验”或“需补充材料”；不要伪称完全响应。

工作区：{workspace_id}
项目：{project_id}
章节：{_dump({"sectionId": section.get("sectionId"), "title": section.get("title")}, 1000)}

【已匹配条款及其专属 M3 材料】
{_dump(requirements, 18000)}

输出结构：
{_dump(REQUIREMENT_RESPONSE_TABLE_SCHEMA, 3000)}

输出规则：
1. 每条输入 requirementId 恰好返回一行，不得新增、删除或改写 ID。
2. response 只填写投标应答正文；偏离状态和来源由系统根据既有匹配结果确定。
3. 若材料不支持具体响应，直接说明待补内容，不得用空泛承诺掩盖。
4. 只输出合法 JSON，不要输出 Markdown、代码块或解释。
""".strip()


def section_rewrite_prompt(
    *,
    parse_bundle: dict[str, Any],
    coverage: list[dict[str, Any]],
    section: dict[str, Any],
    current_content: str,
    instruction: str,
    operation: str,
    selected_text: str,
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    mode: str,
    workspace_id: str,
    project_id: str,
    writing_profile: str = "technical",
) -> str:
    profile = writing_profile or resolve_writing_profile(section)
    return f"""
你是 SectionRewriteAgent，负责对一个投标章节执行局部补写或重写。

只依据当前章节、现有正文、对应要求和已提供材料修改。
不得编造资质、证书、业绩、人员、金额、日期、参数、案例或承诺。
固定模板、表头、锁定文字和签字盖章位置不可修改；资料不足保留人工补充标记。
只输出合法 JSON，不要输出 Markdown、解释或额外文字。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}
操作：{operation}
{_profile_block(profile)}
用户修改要求：{instruction}
选中的正文片段（可能为空）：
{(selected_text or "（未提供选中文本）")[:8000]}

【本章蓝图与篇幅】
{_section_guidance_block(section) or "（无特定范式，按画像与本类重点撰写）"}

【当前章节解析上下文】
{_dump(_section_parse_bundle_view(parse_bundle, section=section, coverage=coverage), 18000)}

【当前章节】
{_dump(_section_prompt_view(section, profile), 5000)}

【当前章节正文】
{_dump(str(current_content or "")[:18000], 18000)}

【条款覆盖关系】
{_dump(coverage, 10000)}

【项目关键字段】
{_dump(_compact_project(project), 3500)}

【企业关键字段】
{_dump(_compact_company(company), 5000)}

【可用企业材料】
{_dump(_section_knowledge_view(knowledge, writing_profile=profile, max_items_per_group=20), 16000)}

输出结构：
{_dump(SECTION_REWRITE_SCHEMA, 6500)}

输出规则：
1. sectionId 必须与当前章节一致。
2. append 只返回追加片段；replace_selection 只返回选区替换片段；rewrite 才返回完整正文。
3. 不要输出章节标题，除非原正文将标题作为正文内容。
4. 新增企业事实必须有 sourceRefs；没有依据就保留人工补充标记。
5. 用户要求与招标文件或材料冲突时，保留合规内容并在 warnings 说明。
6. 无法安全修改时返回 changed=false、空 replacement/content 并说明原因。
""".strip()


def compliance_review_prompt(
    *,
    parse_bundle: dict[str, Any],
    bid_document: dict[str, Any],
    fine_review: dict[str, Any],
    baseline_review: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    allowed_section_ids: list[str],
    allowed_requirement_ids: list[str],
    allowed_evaluation_ids: list[str],
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 ComplianceReviewAgent，负责审查一份投标响应文件是否覆盖招标要求、评分点和模板约束。

只做审查，不修改正文、不生成替代正文、不填充模板。
只依据下面的压缩数据和允许 ID；不得引入外部要求或把推测写成事实。
固定模板、表头、签字盖章位置和锁定文字不视为可修改内容。
只输出合法 JSON，不要输出 Markdown、解释或额外文字。

工作区：{workspace_id}
项目：{project_id}

【允许的章节 ID】
{_dump(allowed_section_ids, 5000)}

【允许的要求 ID】
{_dump(allowed_requirement_ids, 7000)}

【允许的评分项 ID】
{_dump(allowed_evaluation_ids, 7000)}

【解析摘要】
{_dump(_compact_parse_bundle_for_review(parse_bundle), 42000)}

【投标文件摘要】
{_dump(_compact_bid_document_for_review(bid_document), 48000)}

【要求核验摘要】
{_dump(_compact_fine_review(fine_review), 36000)}

【确定性基础校对结果】
{_dump(baseline_review, 12000)}

【项目关键字段】
{_dump(_compact_project(project), 3500)}

【企业关键字段】
{_dump(_compact_company(company), 5000)}

【企业材料】
{_dump(_section_knowledge_view(knowledge, writing_profile="commercial", max_items_per_group=12), 14000)}

输出结构：
{_dump(COMPLIANCE_REVIEW_SCHEMA, 9000)}

审查规则：
1. 只有目录存在且章节有实际正文/表格响应，才能计入 requirementCovered。
2. mandatoryCovered 只统计 mandatory=true 且已有实际响应的要求。
3. evaluationCovered 只统计正文或表格明确关联 evaluationId 的评分项。
4. 对漏响应、强制/废标风险、负偏离、待补材料、模板待填和关键信息不一致分别列风险。
5. 每条风险包含 level、category、title、detail、可定位 ID 和 suggestion。
6. 没有 sourceRefs 的企业事实仍需人工核验。
7. 存在高风险或未覆盖强制项时，status 不能为 pass。
""".strip()


def parse_prompt(
    *,
    document_text: str,
    filename: str,
    parser_warnings: list[str],
    workspace_id: str,
    project_id: str,
    project_context: dict[str, Any],
) -> str:
    return f"""
你是招投标文件结构化解析智能体。

只依据下面提供的招标文件文本进行抽取，不得使用外部知识，不得编造。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
缺失字段使用空字符串、空数组或 null。
每条 requirements 必须有唯一 id，例如 req-001。
outline.requirementIds 只能引用 requirements 中已经存在的 id。

任务：
1. 抽取项目基本信息。
2. 从“投标文件格式”“响应文件组成”“投标文件编制要求”等内容抽取响应目录。
3. 抽取关键要求、强制要求、技术要求、商务要求、资格要求和废标/否决项。
4. 抽取评分办法、评分因素、评分点、分值和评分标准。
5. 抽取无法确认、可能导致废标或需要人工核验的风险。

强制规则：
- ★、必须、应当、不得、否则无效、废标、否决、实质性不响应等内容，按实际语义标记 mandatory 或 isRejection。
- 资质、业绩、人员、财务、信用、区域、技术、服务方案、商务、文件格式、报价、合同分别归入合适 category。
- sourceLocator 必须尽量写章节、页码、表格名称或原文位置。
- 不要把整段招标文件复制进结果，只保留可执行的原子要求。

文件名：
{filename}

工作区：
{workspace_id}

项目：
{project_id}

项目上下文：
{_dump(project_context, 4000)}

本地解析器提示：
{_dump(parser_warnings, 4000)}

招标文件文本：
{document_text}

输出结构示例：
{_dump(PARSE_SCHEMA, 12000)}
""".strip()


def section_prompt(
    *,
    section: dict[str, Any],
    fine_review: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    mode: str,
    workspace_id: str,
    project_id: str,
) -> str:
    schema = {
        "sectionId": str(section.get("sectionId") or ""),
        "title": str(section.get("title") or ""),
        "content": "Markdown 正文",
        "sourceRefs": [
            {
                "factKey": "",
                "factId": "",
                "name": "",
                "evidencePath": "",
            }
        ],
        "status": "generated",
        "warnings": [],
    }
    return f"""
你是投标文件章节撰写智能体。

必须只输出一个合法 JSON 对象，不要输出 Markdown 代码块、解释或额外文字。
只能依据输入的章节、招标条款、项目信息、企业画像和企业知识资料撰写。
严禁编造资质、证书、业绩、人员、金额、日期、参数、案例和承诺。
资料不足时必须在 content 中写“【需人工补充：缺少具体资料】”，并在 warnings 中说明。
sourceRefs 只能引用输入 knowledge 或 fine_review 中真实存在的对象。
如果 mode 为 secret，不得出现公司名称、品牌、人员姓名、地址等暗标敏感信息。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}

【section】
{_dump(section, 18000)}

【fine_review】
{_dump(fine_review, 42000)}

【project】
{_dump(project, 12000)}

【company】
{_dump(company, 12000)}

【knowledge】
{_dump(knowledge, 30000)}

输出结构：
{_dump(schema, 6000)}

规则：
1. sectionId 必须等于 section.sectionId。
2. title 必须等于 section.title。
3. content 使用 Markdown，围绕本章节完成可审核的响应正文。
4. section.requirementIds 不为空时，优先逐条响应对应 fine_review.rows。
5. 强制项必须明确回应；无法回应的地方不得假装满足。
6. status 只能使用 generated 或 missing。
""".strip()


def plan_prompt(
    *,
    project: dict[str, Any],
    fine_review: dict[str, Any],
    cloud_parse: dict[str, Any],
    document_sections: list[dict[str, Any]],
    existing_plan: list[dict[str, Any]],
    workspace_id: str,
    project_id: str,
) -> str:
    schema = {
        "plan": [
            {
                "sectionId": "t-02",
                "title": "技术方案",
                "source": "ai",
                "kind": "technical",
                "requirementIds": ["req-001"],
            }
        ],
        "warnings": [],
    }
    return f"""
你是投标文件目录规划智能体。

你只负责根据招标文件要求生成“投标文件目录/章节计划”，不要撰写正文。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或额外文字。
只能依据输入的项目信息、招标文件解析结果、条款库和原始目录线索规划目录，不得编造招标文件不存在的强制格式要求。

工作区：{workspace_id}
项目：{project_id}

【项目信息 project】
{_dump(project, 12000)}

【条款库 fine_review】
{_dump(fine_review, 42000)}

【云端/历史解析结果 cloud_parse】
{_dump(cloud_parse, 30000)}

【招标文件章节线索 document_sections】
{_dump(document_sections, 26000)}

【当前已有目录 existing_plan】
{_dump(existing_plan, 16000)}

输出结构：
{_dump(schema, 5000)}

目录规划规则：
1. 优先遵循招标文件中的“投标文件格式”“响应文件组成”“投标文件编制要求”“评分办法”。
2. 必须覆盖所有强制条款、废标/否决条款、技术服务要求和评分相关要求。
3. 商务固定文书使用 kind="commercial"，source="template" 或 "table"。
4. 技术/服务方案章节使用 kind="technical"，source="ai"。
5. 偏离表使用 kind="deviation"，source="table"。
6. 附件/证明材料使用 kind="attachment"，source="attachment"。
7. requirementIds 只能引用 fine_review.rows 中存在的 requirementId 或 id。
8. sectionId 可复用既有 c-xx/t-xx/d-xx/p-xx/a-xx；新增章节使用 custom-xx。
9. 不要生成正文内容，不要输出 content 字段。
10. 如果招标文件没有明确要求，也要给出可投递的基础目录，并在 warnings 中说明。
""".strip()
