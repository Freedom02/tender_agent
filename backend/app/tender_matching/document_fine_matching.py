from __future__ import annotations

import json
import math
import re
from typing import Any

from .contracts import build_comparison, normalize_evidence, normalize_requirement, summarize_comparisons
from .eligibility import _is_gateway_timeout, call_openai_compatible
from .requirement_scope import select_requirement_scope


ALLOWED_CATEGORIES = {
    "qualification",
    "performance",
    "personnel",
    "compliance",
    "technical",
    "service_plan",
    "commercial",
    "document",
    "pricing",
    "contract",
    "rejection",
    "other",
}

CATEGORY_LABELS = {
    "qualification": "资格审查",
    "performance": "业绩要求",
    "personnel": "人员要求",
    "compliance": "符合性审查",
    "technical": "技术要求",
    "service_plan": "服务方案要求",
    "commercial": "商务要求",
    "document": "文件格式",
    "pricing": "报价要求",
    "contract": "合同条款",
    "rejection": "否决条款",
    "other": "其他要求",
}

SCORE_BY_STATUS = {
    "满足": 100,
    "部分满足": 78,
    "待补材料": 28,
    "待澄清": 42,
    "不满足": 0,
    "不适用": 100,
}

WEIGHTS = {
    "qualification": 18,
    "performance": 14,
    "personnel": 12,
    "compliance": 12,
    "technical": 22,
    "service_plan": 14,
    "commercial": 10,
    "document": 8,
    "pricing": 6,
    "contract": 4,
    "rejection": 6,
    "other": 6,
}

VALID_SCOPE_SECTION_TYPES = {
    "qualification",
    "compliance",
    "technical",
    "service_plan",
    "commercial",
    "acceptance",
    "evaluation",
    "pricing",
    "document_format",
    "rejection",
    "personnel",
    "performance",
}

NON_REQUIREMENT_SCOPE_TERMS = (
    "投标邀请书",
    "投标邀请",
    "采购邀请",
    "响应邀请",
    "邀请函",
    "项目名称：",
    "项目编号：",
    "采购项目编号：",
    "招标编号：",
    "采购人：",
    "招标人：",
    "代理机构：",
    "联系方式：",
    "联系电话：",
    "项目概况",
    "项目基本情况",
    "采购项目概述",
    "采购范围",
)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _safe_text(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]


def _source_locator(item: dict[str, Any], index: int) -> str:
    return _safe_text(item.get("sourceLocator"), 80) or f"第{index + 1}段"


def _category_for(text: str) -> str:
    value = _compact(text)
    if any(term in value for term in ("无效投标", "投标无效", "否决", "废标", "拒绝", "不予受理", "不得参加")):
        return "rejection"
    if any(
        term in value
        for term in (
            "符合性审查",
            "符合性检查",
            "符合性评审",
            "实质性响应",
            "实质性要求",
            "实质性条款",
            "负偏离",
            "重大偏离",
            "响应招标文件要求",
        )
    ):
        return "compliance"
    if any(term in value for term in ("报价", "最高限价", "控制价", "下浮", "折扣", "费用")):
        return "pricing"
    if any(term in value for term in ("合同", "付款", "结算", "履约", "违约", "验收")):
        return "contract"
    if any(term in value for term in ("投标文件", "响应文件", "签字", "盖章", "格式", "密封", "装订")):
        return "document"
    if any(term in value for term in ("项目经理", "项目负责人", "驻场", "人员", "团队", "工程师")):
        return "personnel"
    if any(term in value for term in ("类似业绩", "类似项目", "项目业绩", "合同业绩", "案例")):
        return "performance"
    if any(term in value for term in ("资质", "证书", "认证", "许可证", "营业执照", "iso", "itss", "cmmi")):
        return "qualification"
    if any(term in value for term in ("商务", "交付期", "工期", "售后", "质保", "服务期")):
        return "commercial"
    if any(
        term in value
        for term in (
            "服务方案",
            "项目实施方案",
            "实施方案",
            "服务实施",
            "项目理解",
            "组织方案",
            "质量保障",
            "应急预案",
            "培训方案",
            "运维方案",
            "实施计划",
            "保障措施",
        )
    ):
        return "service_plan"
    if any(
        term in value
        for term in (
            "技术",
            "功能",
            "性能",
        "系统",
        "平台",
        "接口",
        "数据",
        "参数",
        "配置",
        "服务器",
        "规格",
        "性能指标",
        "三维",
            "数字孪生",
            "可视化",
            "智能体",
            "实施方案",
        )
    ):
        return "technical"
    return "other"


def _is_heading(text: str) -> bool:
    value = _safe_text(text, 200)
    return len(value) <= 42 and bool(
        re.match(r"^(?:第?[一二三四五六七八九十]+[章节、.]|\d+(?:\.\d+)*[、.]|[（(]\d+[)）])", value)
    )


def _is_requirement(text: str) -> bool:
    value = _compact(text)
    if len(value) < 8:
        return False
    terms = (
        "应",
        "须",
        "必须",
        "不得",
        "提供",
        "具备",
        "具有",
        "不少于",
        "不低于",
        "至少",
        "投标人",
        "供应商",
        "响应文件",
        "报价",
        "合同",
        "服务期",
        "交付",
        "验收",
        "方案",
        "计划",
        "预案",
        "保障",
        "偏离",
        "无效投标",
        "投标无效",
        "否决",
    )
    return any(term in value for term in terms) or bool(
        re.match(r"^(?:\d+(?:\.\d+)*[、.]|[（(]\d+[)）])", _safe_text(text, 100))
    )


COMPOUND_MARKER_PATTERN = re.compile(
    r"(?:[（(]\s*[一二三四五六七八九十百千万零〇两0-9]+\s*[）)]|[①②③④⑤⑥⑦⑧⑨⑩]|\d+[、.．)])"
)


def _split_requirement_text(text: str) -> list[str]:
    clean = _safe_text(text, 1200)
    if not clean:
        return []

    markers = list(COMPOUND_MARKER_PATTERN.finditer(clean))
    if len(markers) >= 2:
        prefix = clean[:markers[0].start()].strip(" ：:；;")
        clauses = [
            clean[marker.start() : markers[index + 1].start() if index + 1 < len(markers) else len(clean)]
            .strip(" ；;。")
            for index, marker in enumerate(markers)
        ]
        clauses = [clause for clause in clauses if clause]
        if len(clauses) >= 2:
            if prefix and _is_requirement(prefix):
                clauses.insert(0, prefix)
            return clauses

    semicolon_clauses = [item.strip(" ；;。") for item in re.split(r"[；;]", clean)]
    semicolon_clauses = [item for item in semicolon_clauses if item]
    if len(semicolon_clauses) >= 2 and sum(_is_requirement(item) for item in semicolon_clauses) >= 2:
        return semicolon_clauses
    return [clean]


def _is_table_requirement_row(source: dict[str, Any], text: str) -> bool:
    if not source.get("tableId") or "|" not in text or _is_table_header_row(source, text):
        return False
    cells = [item.strip() for item in text.split("|") if item.strip()]
    if len(cells) < 2 or len(_compact(text)) < 8:
        return False
    return True


def _is_table_header_row(source: dict[str, Any], text: str) -> bool:
    if not source.get("tableId") or "|" not in text:
        return False
    cells = [item.strip() for item in text.split("|") if item.strip()]
    if len(cells) < 2:
        return False
    header_cells = {
        "序号",
        "编号",
        "条款号",
        "技术参数",
        "技术要求",
        "采购需求",
        "投标响应",
        "是否偏离",
        "偏离情况",
        "备注",
    }
    normalized_headers = {_compact(item) for item in header_cells}
    return all(_compact(cell) in normalized_headers for cell in cells)


def _is_non_requirement_scope_text(text: str) -> bool:
    value = _safe_text(text, 1200)
    compact = _compact(value)
    if not value:
        return True
    if any(_compact(term) in compact for term in NON_REQUIREMENT_SCOPE_TERMS):
        return True
    return False


def _is_eligible_scoped_segment(segment: dict[str, Any]) -> bool:
    text = _safe_text(segment.get("text"), 1200)
    if not text or _is_non_requirement_scope_text(text):
        return False

    scope_types = {
        str(item).strip()
        for item in segment.get("scopeSectionTypes") or []
        if str(item).strip()
    }
    if scope_types and scope_types.isdisjoint(VALID_SCOPE_SECTION_TYPES):
        return False

    # Table rows selected by the scope selector may not carry a section type;
    # the table itself has already been classified as a target requirement table.
    return bool(scope_types & VALID_SCOPE_SECTION_TYPES) or bool(segment.get("tableId"))


def _eligible_scope_segments(
    scoped_segments: list[dict[str, Any]],
    fallback_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = [segment for segment in scoped_segments if _is_eligible_scoped_segment(segment)]
    if selected:
        return selected
    return [
        segment
        for segment in fallback_segments
        if _is_requirement(_safe_text(segment.get("text"), 1200))
        and not _is_non_requirement_scope_text(_safe_text(segment.get("text"), 1200))
    ]


def _section_for(text: str, current: str) -> str:
    label = _category_for(text)
    if _is_heading(text) and label != "other":
        return _safe_text(text, 120)
    return current


def _requirement_from_segment(
    source: dict[str, Any],
    index: int,
    section: str,
) -> list[dict[str, Any]]:
    text = _safe_text(source.get("text"), 1200)
    if _is_table_header_row(source, text):
        return []
    clauses = _split_requirement_text(text)
    if not _is_requirement(text) and not _is_table_requirement_row(source, text):
        clauses = [clause for clause in clauses if _is_requirement(clause)]
    if not clauses:
        return []
    compound = len(clauses) > 1
    parent_id = f"rule-{index + 1:03d}"
    source_id = str(source.get("id") or "")
    return [
        {
            "id": f"{parent_id}-{clause_index:02d}" if compound else parent_id,
            "category": _category_for(clause),
            "text": clause,
            "section": section,
            "sourceLocator": _source_locator(source, index),
            "mandatory": True,
            "isRejection": _category_for(clause) == "rejection",
            "confidence": 0.64,
            "parentRequirementId": parent_id if compound else "",
            "clauseIndex": clause_index if compound else 1,
            "clauseCount": len(clauses),
            "sourceSegmentId": source_id,
        }
        for clause_index, clause in enumerate(clauses, start=1)
    ]
    category = _category_for(text)
    return {
        "id": f"rule-{index + 1:03d}",
        "category": category,
        "text": text,
        "section": section,
        "sourceLocator": _source_locator(source, index),
        "mandatory": True,
        "isRejection": category == "rejection",
        "confidence": 0.64,
    }


def extract_tender_requirements_by_rule(text: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    prepared = segments or [
        {"text": line, "sourceLocator": f"第{index + 1}行"}
        for index, line in enumerate(text.splitlines())
        if line.strip()
    ]
    requirements: list[dict[str, Any]] = []
    current_section = ""
    for index, segment in enumerate(prepared):
        segment_text = _safe_text(segment.get("text"), 1200)
        if not segment_text:
            continue
        current_section = _section_for(segment_text, current_section)
        requirements.extend(_requirement_from_segment(segment, len(requirements), current_section))
        if len(requirements) >= 80:
            break

    return {
        "requirements": requirements,
        "warnings": [] if requirements else ["规则未识别到可逐条核验的招标要求，请人工检查原始文件。"],
    }


def _json_from_model(content: str) -> dict[str, Any] | None:
    clean = re.sub(r"^```(?:json)?\s*", "", str(content or "").strip(), flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            result = json.loads(clean[start : end + 1])
        except json.JSONDecodeError:
            return None
    return result if isinstance(result, dict) else None


def _normalize_model_extraction(
    value: dict[str, Any],
    available_locators: set[str],
) -> dict[str, Any] | None:
    raw_requirements = value.get("requirements")
    if not isinstance(raw_requirements, list):
        return None
    requirements: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_requirements[:100]):
        if not isinstance(raw, dict):
            continue
        text = _safe_text(raw.get("text"), 1200)
        if not text:
            continue
        category = str(raw.get("category") or _category_for(text)).lower()
        if category not in ALLOWED_CATEGORIES:
            category = _category_for(text)
        locator = _safe_text(raw.get("sourceLocator"), 80)
        if locator not in available_locators:
            locator = ""
        confidence = raw.get("confidence", 0.72)
        try:
            confidence = min(max(float(confidence), 0), 1)
        except (ValueError, TypeError):
            confidence = 0.72
        requirements.append(
            {
                "id": str(raw.get("id") or f"llm-{index + 1:03d}"),
                "category": category,
                "text": text,
                "section": _safe_text(raw.get("section"), 120) or CATEGORY_LABELS[category],
                "sourceLocator": locator,
                "mandatory": bool(raw.get("mandatory", True)),
                "isRejection": bool(raw.get("isRejection", category == "rejection")),
                "confidence": confidence,
            }
        )
    if not requirements:
        return None
    return {
        "requirements": requirements,
        "warnings": [_safe_text(item, 240) for item in value.get("warnings", []) if _safe_text(item, 240)][:8],
    }


def _model_input_batches(segments: list[dict[str, Any]]) -> list[str]:
    candidates = [
        f"[{_source_locator(segment, index)}] {_safe_text(segment.get('text'), 600)}"
        for index, segment in enumerate(segments)
        if (
            _safe_text(segment.get("text"), 600)
            and not _is_table_header_row(segment, _safe_text(segment.get("text"), 600))
            and (_is_requirement(_safe_text(segment.get("text"), 600)) or _is_heading(_safe_text(segment.get("text"), 600)))
        )
    ]
    if not candidates:
        candidates = [
            f"[{_source_locator(segment, index)}] {_safe_text(segment.get('text'), 600)}"
            for index, segment in enumerate(segments[:80])
            if _safe_text(segment.get("text"), 600)
        ]
    if not candidates:
        return []

    batch_count = min(8, max(1, math.ceil(sum(len(item) + 1 for item in candidates) / 2500)))
    batches: list[str] = []
    for batch_index in range(batch_count):
        start = batch_index * len(candidates) // batch_count
        end = (batch_index + 1) * len(candidates) // batch_count
        selected: list[str] = []
        length = 0
        for item in candidates[start:end]:
            if selected and length + len(item) + 1 > 2500:
                break
            selected.append(item)
            length += len(item) + 1
        if selected:
            batches.append("\n".join(selected))
    return batches


def extract_tender_requirements(
    text: str,
    segments: list[dict[str, Any]],
    ai: dict[str, Any],
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scope = select_requirement_scope(segments, sections, tables)
    selected_scope_segments = scope["selectedSegments"]
    scoped_segments = _eligible_scope_segments(selected_scope_segments, segments)
    scoped_text = "\n".join(
        _safe_text(segment.get("text"), 12000)
        for segment in scoped_segments
        if _safe_text(segment.get("text"), 12000)
    )
    if not scoped_text and not selected_scope_segments:
        scoped_text = text
    rule_result = extract_tender_requirements_by_rule(scoped_text, scoped_segments)
    base_url = str(ai.get("baseUrl") or "").strip()
    api_key = str(ai.get("apiKey") or "").strip()
    model = str(ai.get("model") or "").strip()
    if ai.get("enabled") is False or not (base_url and api_key and model):
        return {
            **rule_result,
            "scope": scope,
            "extractionMethod": "rule",
            "modelUsed": "",
            "extractionWarning": "未配置大模型，已使用规则拆解条款。",
        }

    prompt = (
        "你是招标文件条款拆解助手。仅依据给定原文，拆成可以逐条核验的要求。"
        "不得判断企业是否满足，不得补写原文没有的内容。"
        "只提取三类要求：资格审查(qualification/compliance/rejection)、商务与服务(commercial/pricing/contract/service_plan/performance/document)、技术(technical)。"
        "不要提取人员安排(personnel)、质疑投诉、监督、评标流程、中标人确定方式等流程性内容。"
        "每条要求必须独立成条、只写一个可核验要求，不要把多条合并成一条，保持原文顺序。"
        "输出严格 JSON，不使用 Markdown："
        '{"requirements":[{"id":"req-001","category":"qualification|compliance|rejection|commercial|pricing|contract|service_plan|performance|document|technical",'
        '"text":"原文要求","section":"所在章节","sourceLocator":"必须原样引用输入方括号中的定位",'
        '"mandatory":true,"isRejection":false,"confidence":0.0}],"warnings":[]}'
        "。每批最多输出 8 条最关键要求。"
    )
    available_locators = {
        _source_locator(item, index)
        for index, item in enumerate(scoped_segments)
    }
    extracted: list[dict[str, Any]] = []
    warnings: list[str] = []
    model_used = ""

    for index, batch in enumerate(_model_input_batches(scoped_segments), start=1):
        result = call_openai_compatible(
            ai,
            [
                {"role": "system", "content": prompt},
                {"role": "user", "content": f"招标文件片段（第 {index} 批）如下：\n{batch}"},
            ],
            max_tokens=2500,
            temperature=0,
        )
        if _is_gateway_timeout(result):
            result = call_openai_compatible(
                ai,
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": f"招标文件片段（第 {index} 批，已压缩）如下：\n{batch[:2200]}",
                    },
                ],
                max_tokens=1200,
                temperature=0,
            )
        if not result.get("success"):
            warnings.append(f"第 {index} 批模型调用失败：{result.get('error', '未知错误')}")
            continue

        normalized = _normalize_model_extraction(_json_from_model(result.get("content", "")) or {}, available_locators)
        if not normalized:
            warnings.append(f"第 {index} 批模型未返回可用 JSON。")
            continue
        extracted.extend(normalized["requirements"])
        warnings.extend(normalized["warnings"])
        model_used = str(result.get("model", ""))

    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for requirement in extracted:
        key = (str(requirement.get("sourceLocator", "")), str(requirement.get("text", "")))
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(requirement)

    # 批次失败时，用规则拆解结果补齐被丢弃的条款，避免静默丢失。
    if rule_result.get("requirements"):
        rule_texts = {re.sub(r"\s+", "", str(r.get("text", ""))) for r in deduplicated}
        for requirement in rule_result["requirements"]:
            text_key = re.sub(r"\s+", "", str(requirement.get("text", "")))
            if not text_key or text_key in rule_texts:
                continue
            rule_texts.add(text_key)
            deduplicated.append(requirement)

    if deduplicated:
        return {
            "requirements": deduplicated,
            "warnings": warnings[:8],
            "scope": scope,
            "extractionMethod": "llm",
            "modelUsed": model_used,
            "extractionWarning": "部分条款批次未完成，已用规则拆解补齐缺失条款。" if warnings else "",
        }

    warning = "；".join(warnings[:3]) or "模型未返回可用 JSON"
    return {
        **rule_result,
        "scope": scope,
        "extractionMethod": "rule",
        "modelUsed": "",
        "extractionWarning": f"{warning}，已回退到规则拆解。",
    }


def _record_terms(record: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("name", "aliases", "industry", "description", "category", "keywordIds"):
        value = record.get(key)
        values = value if isinstance(value, list) else [value]
        for entry in values:
            cleaned = _compact(entry)
            if len(cleaned) >= 3:
                terms.append(cleaned)
            for part in re.split(r"[/、，,;；|与和及()（）\-]+", str(entry or "")):
                part = _compact(part)
                if len(part) >= 3:
                    terms.append(part)
    return list(dict.fromkeys(terms))


def _matching_records(requirement: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = _compact(requirement)
    matches: list[dict[str, Any]] = []
    for record in records:
        if any(term in text for term in _record_terms(record)):
            matches.append(record)
    return matches


def _candidate_records(category: str, requirement: str, knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    qualifications = _list_of_dicts(knowledge.get("qualifications"))
    references = _list_of_dicts(knowledge.get("projectReferences"))
    products = _list_of_dicts(knowledge.get("products"))
    capabilities = _list_of_dicts(knowledge.get("capabilities"))
    scenarios = _list_of_dicts(knowledge.get("industryScenarios"))
    directions = _list_of_dicts(knowledge.get("businessDirections"))
    if category == "qualification":
        return _matching_records(requirement, qualifications)
    if category == "performance":
        return _matching_records(requirement, [*references, *scenarios])
    if category in {"technical", "commercial", "service_plan"}:
        return _matching_records(requirement, [*products, *capabilities, *scenarios, *directions])
    if category == "compliance":
        return _matching_records(
            requirement,
            [*qualifications, *products, *capabilities, *references, *scenarios, *directions],
        )
    return []


def _evidence_for_records(records: list[dict[str, Any]], knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("evidences"))}
    material_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("materials"))}
    source_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("sourceFiles"))}
    evidence_material_map: dict[str, str] = {}
    for material_id, material in material_map.items():
        for evidence_id in material.get("evidenceIds") or []:
            evidence_material_map.setdefault(str(evidence_id), material_id)
    resolved: list[dict[str, Any]] = []
    for record in records:
        evidence_ids = list(record.get("evidenceIds") or [])
        for material_id in record.get("materialIds") or []:
            evidence_ids.extend(material_map.get(str(material_id), {}).get("evidenceIds") or [])
        if not evidence_ids:
            resolved.append(
                {
                    "id": str(record.get("id", "")),
                    "name": str(record.get("name", "")),
                    "status": str(record.get("status", "资料已建模")),
                    "level": str(record.get("evidenceLevel", "")),
                    "locator": "",
                    "sourceType": "knowledge_object",
                    "materialId": str((record.get("materialIds") or [""])[0]),
                    "sourceFiles": [],
                }
            )
            continue
        for evidence_id in evidence_ids:
            evidence = evidence_map.get(str(evidence_id))
            if not evidence:
                continue
            resolved.append(
                {
                    "id": str(evidence.get("id", "")),
                    "name": str(evidence.get("name", "")),
                    "status": str(evidence.get("status", "")),
                    "level": str(evidence.get("level", "")),
                    "locator": str(evidence.get("locator", "")),
                    "sourceType": "evidence",
                    "materialId": evidence_material_map.get(str(evidence.get("id", "")), ""),
                    "sourceFileIds": [str(source_id) for source_id in evidence.get("sourceIds", [])],
                    "sourceFiles": [
                        str(source_map.get(str(source_id), {}).get("name", source_id))
                        for source_id in evidence.get("sourceIds", [])
                    ],
                    "excerpt": str(evidence.get("excerpt", "")),
                    "validFrom": str(evidence.get("validFrom", "")),
                    "validUntil": str(evidence.get("validUntil", "")),
                }
            )
    unique = {item["id"] or item["name"]: item for item in resolved}
    return list(unique.values())[:6]


def _evidence_summary(evidences: list[dict[str, Any]]) -> str:
    if not evidences:
        return "企业资料库未找到可直接关联的证据"
    parts = []
    for item in evidences:
        locator = f"；{item['locator']}" if item.get("locator") else ""
        parts.append(f"{item['name']}（{item.get('status') or '状态待核验'}{locator}）")
    return "；".join(parts)


def _status_from_evidence(
    category: str,
    matched: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
) -> str:
    if category in {"document", "pricing", "contract", "rejection"}:
        return "待澄清"
    if not matched:
        if category in {
            "qualification",
            "performance",
            "personnel",
            "technical",
            "service_plan",
            "commercial",
        }:
            return "待补材料"
        return "待澄清"
    direct_evidences = [
        normalize_evidence(item)
        for item in evidences
        if item.get("sourceType") == "evidence"
    ]
    if any(item.get("isVerified") for item in direct_evidences):
        return "满足"
    if direct_evidences and all(item.get("isExpired") for item in direct_evidences):
        return "不满足"
    return "待补材料" if direct_evidences or matched else "待澄清"


def _suggestion_for_status(status: str, category: str) -> str:
    if category == "rejection":
        return "请人工核对否决条件、格式附件、签章和递交要求，当前不自动给出通过结论。"
    if category == "compliance":
        return "请逐项确认是否实质性响应招标文件，并核对偏离表、承诺函和对应支撑材料。"
    if category == "service_plan":
        return "请编制项目理解、实施计划、服务保障、培训或应急方案，并关联可复核的同类交付材料。"
    if category == "document":
        return "按招标文件逐项核验格式、签章、密封、附件和递交要求。"
    if category == "pricing":
        return "请商务人员核对最高限价、报价口径、税率和报价表要求。"
    if category == "contract":
        return "请业务和法务确认交付、验收、付款、违约及履约责任。"
    return {
        "满足": "已找到已核验的关联证据，递交前仍应复核原件及适用性。",
        "部分满足": "已找到关联本体对象和资料来源，请复核项目适用性与证据原件。",
        "待补材料": "资料库暂未形成可直接核验的材料，请补充材料或确认是否尚未录入。",
        "待澄清": "当前要求与本体对象无法形成明确对应，请阅读上下文后人工澄清。",
        "不满足": "当前未发现可以支持该要求的有效证据，请复核资料库和招标要求。",
        "不适用": "该要求已确认不适用于当前投标主体或本次响应。",
    }[status]


def _risk_for_status(status: str) -> str:
    if status in {"满足", "不适用"}:
        return "低"
    if status in {"部分满足", "待澄清"}:
        return "中"
    return "高"


def summarize_fine_review(rows: list[dict[str, Any]], extraction: dict[str, Any]) -> dict[str, Any]:
    weighted_score = 0.0
    total_weight = 0
    unresolved_mandatory = []
    for row in rows:
        category = str(row.get("category") or "other")
        status = str(row.get("status") or "待澄清")
        weight = WEIGHTS.get(category, WEIGHTS["other"])
        total_weight += weight
        weighted_score += weight * SCORE_BY_STATUS.get(status, 35)
        if row.get("mandatory") and status in {"部分满足", "待补材料", "待澄清", "不满足"}:
            unresolved_mandatory.append(row)

    score = round(weighted_score / total_weight) if total_weight else 0
    rejection_risks = [
        row for row in rows
        if row.get("isRejection") and row.get("status") not in {"满足", "不适用"}
    ]
    hard_blockers = [
        {
            "requirementId": str(row.get("id", "")),
            "title": f"{row.get('kind', CATEGORY_LABELS['other'])}：{row.get('status', '待澄清')}",
            "detail": str(row.get("requirement", "")),
            "sourceLocator": str(row.get("sourceLocator", "")),
        }
        for row in rejection_risks
        if row.get("status") == "不满足"
    ]
    if hard_blockers:
        recommendation = "不建议参与"
    elif rejection_risks:
        recommendation = "人工重点评估"
    elif score >= 85 and not unresolved_mandatory:
        recommendation = "建议进入正式投标准备"
    elif score >= 70:
        recommendation = "有条件参与，先补材料"
    elif score >= 55:
        recommendation = "人工重点评估"
    else:
        recommendation = "暂不建议参与"

    risks = [
        {
            "level": str(row.get("risk", "中")),
            "title": f"{row.get('kind', CATEGORY_LABELS['other'])}：{row.get('status', '待澄清')}",
            "detail": f"{row.get('requirement', '')}。{row.get('suggestion', '')}",
            "sourceLocator": str(row.get("sourceLocator", "")),
        }
        for row in rows
        if row.get("risk") != "低"
    ][:30]
    summary = summarize_comparisons(rows)

    return {
        "rows": rows,
        "risks": risks,
        "score": score,
        "decision": recommendation,
        "recommendation": recommendation,
        "clauseCount": len(rows),
        "statusCounts": summary["statusCounts"],
        "summary": summary,
        "hardBlockers": hard_blockers,
        "rejectionRisks": [
            {
                "requirementId": str(row.get("requirementId", "")),
                "title": f"{row.get('kind', CATEGORY_LABELS['other'])}：{row.get('status', '待澄清')}",
                "detail": str(row.get("rawText", "")),
                "sourceLocator": str(row.get("sourceLocator", "")),
            }
            for row in rejection_risks
        ],
        "unresolvedMandatory": [
            {
                "requirementId": str(row.get("id", "")),
                "title": str(row.get("kind", CATEGORY_LABELS["other"])),
                "detail": str(row.get("requirement", "")),
                "sourceLocator": str(row.get("sourceLocator", "")),
            }
            for row in unresolved_mandatory
        ],
        "extraction": extraction,
    }


def update_fine_review_status(
    fine_review: dict[str, Any],
    row_id: str,
    status: str,
) -> dict[str, Any] | None:
    if status not in SCORE_BY_STATUS:
        raise ValueError(f"不支持的匹配状态：{status}")
    rows = fine_review.get("rows")
    if not isinstance(rows, list):
        return None
    target = next(
        (row for row in rows if isinstance(row, dict) and str(row.get("id", "")) == row_id),
        None,
    )
    if target is None:
        return None
    target["status"] = status
    target["risk"] = _risk_for_status(status)
    target["riskLevel"] = target["risk"]
    target["suggestion"] = _suggestion_for_status(status, str(target.get("category") or "other"))
    target["manualStatus"] = status
    target["statusSource"] = "人工审核"
    target["reviewStatus"] = "已人工复核"
    target["reason"] = f"人工复核已将匹配状态更新为“{status}”。"
    extraction = fine_review.get("extraction")
    if not isinstance(extraction, dict):
        extraction = {}
    return summarize_fine_review(rows, extraction)


def evaluate_tender_document(
    text: str,
    segments: list[dict[str, Any]],
    company_knowledge: dict[str, Any],
    ai: dict[str, Any],
    source_file_id: str = "",
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    extraction = extract_tender_requirements(
        text,
        segments,
        ai,
        sections,
        tables,
    )
    rows: list[dict[str, Any]] = []

    for index, requirement in enumerate(extraction["requirements"], start=1):
        category = str(requirement.get("category") or "other")
        kind = CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"])
        requirement_contract = normalize_requirement(
            {**requirement, "requirementId": f"req-{index:03d}"},
            source_stage="tender_document",
            layer=3,
            kind=kind,
            source_file_id=source_file_id,
        )
        requirement_text = str(requirement_contract["rawText"])
        matched = _candidate_records(category, requirement_text, company_knowledge)
        evidences = _evidence_for_records(matched, company_knowledge)
        status = _status_from_evidence(category, matched, evidences)
        reason = {
            "满足": "已找到经过核验且当前有效的直接企业证据。",
            "部分满足": "已找到相关企业对象或证据，但尚不能证明全部条件。",
            "待补材料": "已找到相关企业对象，但直接证明材料尚未核验或未完整入库。",
            "待澄清": "该要求需要结合上下文、响应文件编制规则或人工判断确认。",
            "不满足": "现有直接证据显示关键条件已失效或明确不符合。",
            "不适用": "该要求经人工确认不适用于本次响应。",
        }[status]
        row = build_comparison(
            requirement_contract,
            status=status,
            reason=reason,
            matched_objects=matched,
            evidences=evidences,
            risk=_risk_for_status(status),
            suggestion=_suggestion_for_status(status, category),
        )
        row["evidence"] = _evidence_summary(row["evidenceItems"])
        rows.append(row)
    return summarize_fine_review(
        rows,
        {
            "method": extraction["extractionMethod"],
            "modelUsed": extraction["modelUsed"],
            "warning": extraction["extractionWarning"],
            "warnings": extraction.get("warnings", []),
            "scope": extraction.get("scope", {}),
        },
    )
