from __future__ import annotations

import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from typing import Any, Callable

from ..agent_runtime.contracts import build_agent_result, now_iso
from ..bid_writing.schemas import (
    material_keys_for_writing_profile,
    normalize_response_type,
    resolve_writing_profile,
)
from .outline_agent_context import (
    OUTLINE_TOOL_ALLOWLIST,
    build_context,
    extension_path,
    resolve_policy,
    write_context,
)


class PiWritingOrchestrationError(RuntimeError):
    """Pi 撰写阶段编排无法生成有效目录。"""


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _collect_material_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key in ("id", "factId", "materialId", "evidenceId"):
            candidate = _text(value.get(key), 160)
            if candidate:
                found.add(candidate)
        for child in value.values():
            found.update(_collect_material_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_collect_material_ids(child))
    return found


def _scope_knowledge(
    knowledge: dict[str, Any],
    *,
    writing_profile: str,
    max_items_per_group: int = 40,
) -> dict[str, Any]:
    """只把当前撰写画像需要的材料分组交给 Pi。"""

    payload = knowledge if isinstance(knowledge, dict) else {}
    allowed_keys = set(material_keys_for_writing_profile(writing_profile))
    scoped: dict[str, Any] = {}
    for key, value in payload.items():
        if key not in allowed_keys:
            continue
        if isinstance(value, list):
            scoped[key] = [
                item
                for item in value[:max_items_per_group]
                if isinstance(item, dict)
            ]
        elif isinstance(value, dict):
            scoped[key] = value
    return scoped


def _text(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _nested_payload(value: Any) -> dict[str, Any]:
    payload = _as_dict(value)
    for key in ("result", "data", "output"):
        nested = payload.get(key)
        if isinstance(nested, dict):
            return _nested_payload(nested)
    return payload


def _flatten_plan_items(
    items: list[Any],
    *,
    parent_id: str = "",
    parent_level: int = 0,
) -> list[dict[str, Any]]:
    """兼容模型偶尔返回 children 的情况，统一转换为扁平章节数组。"""

    flattened: list[dict[str, Any]] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        explicit_parent = _text(
            row.get("parentSectionId") or row.get("parentId"),
            80,
        )
        inherited_parent = explicit_parent or parent_id
        if inherited_parent and not explicit_parent:
            row["_parentSectionId"] = inherited_parent
        try:
            raw_level = int(row.get("level") or 0)
        except (TypeError, ValueError):
            raw_level = 0
        row["_level"] = max(raw_level, parent_level + 1, 1)
        try:
            raw_order = int(row.get("order") or 0)
        except (TypeError, ValueError):
            raw_order = 0
        row["_order"] = raw_order if raw_order > 0 else index
        flattened.append(row)

        children = row.get("children")
        if isinstance(children, list):
            child_parent_id = _text(row.get("sectionId") or row.get("id"), 80)
            flattened.extend(
                _flatten_plan_items(
                    children,
                    parent_id=child_parent_id,
                    parent_level=row["_level"],
                )
            )
    return flattened


def _dropped_derived_titles(
    plan: list[dict[str, Any]],
    outline_raw: dict[str, Any],
) -> list[str]:
    """找出智能体声明新增、但没能在放置后留在目录里的章节标题。

    放置规则对「挂错父级」的章节是丢弃处理；静默丢弃会让人完全看不出
    智能体到底做没做事，所以这里把差异显式报出来。
    """

    kept = {
        _text(item.get("title"), 240)
        for item in plan
        if isinstance(item, dict) and item.get("derived")
    }
    titles: list[str] = []
    raw_plan = outline_raw.get("plan") if isinstance(outline_raw, dict) else None
    for item in _as_list(raw_plan):
        if not isinstance(item, dict) or not item.get("derived"):
            continue
        title = _text(item.get("title"), 240)
        if title and title not in kept and title not in titles:
            titles.append(title)
    return titles


def _normalize_plan(
    value: Any,
    *,
    valid_requirement_ids: set[str] | None = None,
    valid_evaluation_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    payload = _nested_payload(value)
    raw_plan = payload.get("plan")
    if not isinstance(raw_plan, list):
        raw_plan = payload.get("outline")
    if not isinstance(raw_plan, list):
        raw_plan = payload.get("sections")
    if not isinstance(raw_plan, list):
        return []

    raw_items = _flatten_plan_items(raw_plan)
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    assigned_ids: set[str] = set()
    source_id_map: dict[str, str] = {}
    assigned_rows: list[tuple[dict[str, Any], str, str]] = []
    custom_index = 1
    for item in raw_items:
        title = _text(item.get("title") or item.get("name"), 240)
        if not title:
            continue
        source_section_id = _text(item.get("sectionId") or item.get("id"), 80)
        section_id = source_section_id
        if not section_id or section_id in assigned_ids:
            section_id = f"custom-{custom_index:02d}"
            custom_index += 1
            while section_id in assigned_ids:
                section_id = f"custom-{custom_index:02d}"
                custom_index += 1
        assigned_ids.add(section_id)
        if source_section_id and source_section_id not in source_id_map:
            source_id_map[source_section_id] = section_id
        assigned_rows.append((item, section_id, source_section_id))

    levels_by_id: dict[str, int] = {}
    sibling_orders: dict[str, int] = {}
    for item, section_id, source_section_id in assigned_rows:
        # 每个节点必须重新取自己的标题。此处若沿用上一个循环残留的 title，
        # 会把所有节点（含卷与格式条目）的标题覆盖成最后一项的标题。
        title = _text(item.get("title") or item.get("name"), 240)
        if not title:
            continue
        requirement_ids = [
            _text(entry, 80)
            for entry in _as_list(item.get("requirementIds"))
            if _text(entry, 80)
            and (
                valid_requirement_ids is None
                or _text(entry, 80) in valid_requirement_ids
            )
        ]
        evaluation_ids = [
            _text(entry, 80)
            for entry in _as_list(item.get("evaluationIds"))
            if _text(entry, 80)
            and (
                valid_evaluation_ids is None
                or _text(entry, 80) in valid_evaluation_ids
            )
        ]
        parent_source_id = _text(
            item.get("parentSectionId")
            or item.get("parentId")
            or item.get("_parentSectionId"),
            80,
        )
        parent_section_id = source_id_map.get(parent_source_id, "")
        try:
            level = max(int(item.get("level") or item.get("_level") or 1), 1)
        except (TypeError, ValueError):
            level = 1
        if parent_section_id:
            level = max(level, levels_by_id.get(parent_section_id, level - 1) + 1)
        sibling_key = parent_section_id
        try:
            order = int(item.get("order") or item.get("_order") or 0)
        except (TypeError, ValueError):
            order = 0
        if order <= 0:
            order = sibling_orders.get(sibling_key, 0) + 1
        sibling_orders[sibling_key] = max(sibling_orders.get(sibling_key, 0), order)
        source = _text(item.get("source"), 60) or "ai"
        kind = _text(item.get("kind"), 60) or "technical"
        response_type = normalize_response_type(_text(item.get("responseType"), 60))
        if not response_type:
            response_type = (
                "fixed_form"
                if source in {"template", "copied_template"}
                else "table"
                if source in {"table", "deviation"} or kind == "deviation"
                else "attachment"
                if source == "attachment" or kind == "attachment"
                else "chapter_text"
            )
        normalized = {
            **item,
            "sectionId": section_id,
            "title": title,
            "source": source,
            "kind": kind,
            "parentSectionId": parent_section_id,
            "level": level,
            "order": order,
            "required": bool(item.get("required", True)),
            "responseType": response_type,
            "requirementIds": list(dict.fromkeys(requirement_ids)),
            "evaluationIds": list(dict.fromkeys(evaluation_ids)),
            "templateId": _text(item.get("templateId"), 100),
        }
        writing_profile = resolve_writing_profile(normalized)
        normalized["writingProfile"] = writing_profile
        normalized["materialScopes"] = list(
            material_keys_for_writing_profile(writing_profile)
        )
        normalized.pop("children", None)
        normalized.pop("_parentSectionId", None)
        normalized.pop("_level", None)
        normalized.pop("_order", None)
        if source_section_id and source_section_id != section_id:
            normalized["sourceSectionId"] = source_section_id
        levels_by_id[section_id] = level
        result.append(normalized)
    return result


def _outline_quality_issues(plan: list[dict[str, Any]]) -> list[str]:
    generated = [
        item
        for item in plan
        if item.get("nodeType") not in {"volume", "format_item"}
        and item.get("structureSource") != "招标文件原文"
    ]
    issues: list[str] = []
    roots = [item for item in plan if not item.get("parentSectionId")]
    root_volumes = [
        item
        for item in roots
        if item.get("nodeType") == "volume"
        and item.get("responseType") == "section_group"
    ]
    if roots and len(root_volumes) != len(roots):
        issues.append("目录顶层存在非卷节点，文件骨架必须先按卷组织")

    sentence_terms = (
        "我单位",
        "本人",
        "具有",
        "不存在",
        "请投标人",
        "请供应商",
        "投标人为",
        "应提供",
        "否则",
        "资格承诺的",
    )
    suspicious_volumes: list[str] = []
    for item in root_volumes:
        title = _text(item.get("title"), 240)
        compact = re.sub(r"\s+", "", title)
        if (
            len(compact) > 80
            or any(term in compact for term in sentence_terms)
            or compact.endswith((":", "：", ";", "；", ".", "。"))
        ):
            suspicious_volumes.append(title)
    if suspicious_volumes:
        issues.append(
            "顶层卷标题疑似资格条件或说明句："
            + "、".join(dict.fromkeys(suspicious_volumes[:3]))
        )

    if len(generated) > 160:
        issues.append(f"模型扩写了 {len(generated)} 个目录项，超过 160 项的质量上限")

    max_level = max((int(item.get("level") or 1) for item in plan), default=1)
    if max_level > 9:
        issues.append(f"目录深度达到 {max_level} 级，超过 Word 可用的 9 级")

    titles_by_parent: dict[str, set[str]] = {}
    duplicate_titles: list[str] = []
    children_by_parent: dict[str, int] = {}
    for item in generated:
        parent_id = str(item.get("parentSectionId") or "")
        children_by_parent[parent_id] = children_by_parent.get(parent_id, 0) + 1
        title = re.sub(r"[\s\W_]+", "", str(item.get("title") or "")).lower()
        if not title:
            continue
        sibling_titles = titles_by_parent.setdefault(parent_id, set())
        if title in sibling_titles:
            duplicate_titles.append(str(item.get("title") or ""))
        sibling_titles.add(title)

    if duplicate_titles:
        examples = "、".join(dict.fromkeys(duplicate_titles[:3]))
        issues.append(f"同级存在重复目录标题：{examples}")
    oversized_groups = [
        count for count in children_by_parent.values() if count > 80
    ]
    if oversized_groups:
        issues.append("单个父级下存在超过 80 个子项，疑似把原子要求逐条展开为目录")
    return issues


def _normalize_coverage(
    value: Any,
    *,
    section_ids: set[str],
    requirement_ids: set[str],
    evaluation_ids: set[str],
) -> dict[str, Any]:
    payload = _nested_payload(value)
    coverage: list[dict[str, Any]] = []
    for item in _as_list(payload.get("coverage")):
        if not isinstance(item, dict):
            continue
        requirement_id = _text(item.get("requirementId") or item.get("id"), 80)
        section_id = _text(item.get("sectionId"), 80)
        if not requirement_id or requirement_id not in requirement_ids:
            continue
        if not section_id or section_id not in section_ids:
            continue
        related_evaluations = [
            _text(entry, 80)
            for entry in _as_list(item.get("evaluationIds"))
            if _text(entry, 80) in evaluation_ids
        ]
        coverage.append(
            {
                **item,
                "requirementId": requirement_id,
                "sectionId": section_id,
                "coverageType": _text(item.get("coverageType"), 60) or "chapter_text",
                "status": _text(item.get("status"), 40) or "needs_review",
                "evaluationIds": list(dict.fromkeys(related_evaluations)),
                "reason": _text(item.get("reason"), 1200),
            }
        )

    def normalize_issue(item: Any) -> dict[str, Any] | None:
        if isinstance(item, str):
            requirement_id = _text(item, 80)
            if requirement_id in requirement_ids:
                return {"requirementId": requirement_id, "reason": ""}
            return None
        if not isinstance(item, dict):
            return None
        requirement_id = _text(item.get("requirementId") or item.get("id"), 80)
        if requirement_id not in requirement_ids:
            return None
        return {
            **item,
            "requirementId": requirement_id,
            "reason": _text(item.get("reason"), 1200),
        }

    uncovered = [
        issue
        for issue in (normalize_issue(item) for item in _as_list(payload.get("uncoveredRequirements")))
        if issue
    ]
    duplicated = [
        issue
        for issue in (normalize_issue(item) for item in _as_list(payload.get("duplicatedRequirements")))
        if issue
    ]
    return {
        "coverage": coverage,
        "uncoveredRequirements": uncovered,
        "duplicatedRequirements": duplicated,
        "warnings": [
            _text(item, 500)
            for item in _as_list(payload.get("warnings"))
            if _text(item, 500)
        ],
    }


def _normalize_material_matches(
    value: Any,
    *,
    requirement_ids: set[str],
    allowed_material_ids: set[str],
) -> dict[str, Any]:
    payload = _nested_payload(value)
    raw_matches = payload.get("matches")
    if not isinstance(raw_matches, list):
        raw_matches = payload.get("materialMatches")
    matches: list[dict[str, Any]] = []
    matched_ids: set[str] = set()
    for item in _as_list(raw_matches):
        if not isinstance(item, dict):
            continue
        requirement_id = _text(item.get("requirementId") or item.get("id"), 80)
        if not requirement_id or requirement_id not in requirement_ids:
            continue
        material_ids = [
            _text(entry, 120)
            for entry in _as_list(item.get("materialIds"))
            if _text(entry, 120) in allowed_material_ids
        ]
        material_refs = [
            ref
            for ref in _as_list(item.get("materialRefs"))
            if isinstance(ref, dict)
            and (
                not _text(ref.get("materialId") or ref.get("factId") or ref.get("evidenceId"), 160)
                or _text(
                    ref.get("materialId") or ref.get("factId") or ref.get("evidenceId"),
                    160,
                )
                in allowed_material_ids
            )
        ]
        status = _text(item.get("matchStatus") or item.get("status"), 40) or (
            "matched" if material_ids or material_refs else "unmatched"
        )
        matches.append(
            {
                **item,
                "requirementId": requirement_id,
                "materialIds": list(dict.fromkeys(material_ids)),
                "materialRefs": material_refs,
                "matchStatus": status,
                "evidenceSummary": _text(item.get("evidenceSummary"), 1200),
                "missingReason": _text(item.get("missingReason"), 1200),
            }
        )
        if status == "matched":
            matched_ids.add(requirement_id)

    unmatched_ids = [
        _text(entry, 80)
        for entry in _as_list(payload.get("unmatchedRequirementIds"))
        if _text(entry, 80) in requirement_ids
    ]
    for requirement_id in sorted(requirement_ids - matched_ids):
        if requirement_id not in unmatched_ids and not any(
            row.get("requirementId") == requirement_id
            and row.get("matchStatus") in {"partial", "needs_review"}
            for row in matches
        ):
            unmatched_ids.append(requirement_id)

    return {
        "matches": matches,
        "unmatchedRequirementIds": list(dict.fromkeys(unmatched_ids)),
        "warnings": [
            _text(item, 500)
            for item in _as_list(payload.get("warnings"))
            if _text(item, 500)
        ],
    }


def _normalize_section_plan(
    value: Any,
    section: dict[str, Any],
    *,
    requirement_ids: set[str],
    evaluation_ids: set[str],
    material_ids: set[str],
) -> dict[str, Any]:
    payload = _nested_payload(value)
    raw_plan = payload.get("writingPlan")
    if not isinstance(raw_plan, dict):
        raw_plan = payload
    warnings = [
        _text(item, 500)
        for item in _as_list(raw_plan.get("warnings"))
        if _text(item, 500)
    ]
    requirement_responses: list[dict[str, Any]] = []
    seen_requirement_ids: set[str] = set()
    for item in _as_list(raw_plan.get("requirementResponses")):
        if not isinstance(item, dict):
            continue
        requirement_id = _text(item.get("requirementId"), 80)
        if requirement_id not in requirement_ids or requirement_id in seen_requirement_ids:
            continue
        seen_requirement_ids.add(requirement_id)
        requirement_responses.append(
            {
                **item,
                "requirementId": requirement_id,
                "materialIds": [
                    _text(entry, 120)
                    for entry in _as_list(item.get("materialIds"))
                    if _text(entry, 120) in material_ids
                ],
            }
        )
    missing_requirement_ids = sorted(requirement_ids - seen_requirement_ids)
    for requirement_id in missing_requirement_ids:
        requirement_responses.append(
            {
                "requirementId": requirement_id,
                "responseStrategy": "needs_review",
                "materialIds": [],
                "reason": "SectionPlanAgent 未返回该条款的响应安排，需人工补充或确认。",
            }
        )
    if missing_requirement_ids:
        warnings.append(
            f"SectionPlanAgent 未覆盖 {len(missing_requirement_ids)} 条当前章节要求，"
            "编排器已补入待复核占位项。"
        )

    evaluation_responses: list[dict[str, Any]] = []
    seen_evaluation_ids: set[str] = set()
    for item in _as_list(raw_plan.get("evaluationResponses")):
        if not isinstance(item, dict):
            continue
        evaluation_id = _text(item.get("evaluationId"), 80)
        if evaluation_id not in evaluation_ids or evaluation_id in seen_evaluation_ids:
            continue
        seen_evaluation_ids.add(evaluation_id)
        evaluation_responses.append(
            {
                **item,
                "evaluationId": evaluation_id,
                "evidenceIds": [
                    _text(entry, 120)
                    for entry in _as_list(item.get("evidenceIds"))
                    if _text(entry, 120) in material_ids
                ],
            }
        )
    missing_evaluation_ids = sorted(evaluation_ids - seen_evaluation_ids)
    for evaluation_id in missing_evaluation_ids:
        evaluation_responses.append(
            {
                "evaluationId": evaluation_id,
                "responseStrategy": "needs_review",
                "evidenceIds": [],
                "reason": "SectionPlanAgent 未返回该评分项的响应安排，需人工补充或确认。",
            }
        )
    if missing_evaluation_ids:
        warnings.append(
            f"SectionPlanAgent 未覆盖 {len(missing_evaluation_ids)} 个当前章节评分项，"
            "编排器已补入待复核占位项。"
        )

    outline: list[dict[str, Any]] = []
    for item in _as_list(raw_plan.get("outline")):
        if not isinstance(item, dict):
            continue
        outline.append(
            {
                **item,
                "requirementIds": [
                    _text(entry, 80)
                    for entry in _as_list(item.get("requirementIds"))
                    if _text(entry, 80) in requirement_ids
                ],
                "evaluationIds": [
                    _text(entry, 80)
                    for entry in _as_list(item.get("evaluationIds"))
                    if _text(entry, 80) in evaluation_ids
                ],
            }
        )
    return {
        **raw_plan,
        "sectionId": _text(section.get("sectionId"), 80),
        "title": _text(section.get("title"), 240),
        "objective": _text(raw_plan.get("objective"), 1600),
        "requirementResponses": requirement_responses,
        "evaluationResponses": evaluation_responses,
        "outline": outline,
        "templateInstructions": [
            _text(item, 1200)
            for item in _as_list(raw_plan.get("templateInstructions"))
            if _text(item, 1200)
        ],
        "warnings": list(dict.fromkeys(warnings)),
    }


def _normalize_section_draft(
    value: Any,
    section: dict[str, Any],
    *,
    material_ids: set[str],
) -> dict[str, Any]:
    payload = _nested_payload(value)
    content = ""
    for key in ("content", "markdown", "draft", "sectionContent", "text", "answer"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate.strip():
            content = candidate.strip()
            break
    status = _text(payload.get("status"), 40) or ("generated" if content else "missing")
    if status not in {"generated", "missing", "needs_review"}:
        status = "needs_review"
    source_refs = [
        item
        for item in _as_list(payload.get("sourceRefs") or payload.get("source_refs"))
        if isinstance(item, dict)
        and (
            not _text(
                item.get("materialId") or item.get("factId") or item.get("evidenceId"),
                160,
            )
            or _text(
                item.get("materialId") or item.get("factId") or item.get("evidenceId"),
                160,
            )
            in material_ids
        )
    ]
    return {
        "sectionId": _text(section.get("sectionId"), 80),
        "title": _text(section.get("title"), 240),
        "content": content,
        "sourceRefs": source_refs,
        "status": status,
        "warnings": [
            _text(item, 500)
            for item in _as_list(payload.get("warnings"))
            if _text(item, 500)
        ],
    }


def _normalize_template_fill(value: Any, fragment: dict[str, Any], section: dict[str, Any]) -> dict[str, Any]:
    payload = _nested_payload(value)
    field_values = [
        item
        for item in _as_list(payload.get("fieldValues") or payload.get("field_values"))
        if isinstance(item, dict)
    ]
    cell_values = [
        item
        for item in _as_list(payload.get("cellValues") or payload.get("cell_values"))
        if isinstance(item, dict)
    ]
    normalized_fields: list[dict[str, Any]] = []
    for item in field_values:
        status = _text(item.get("status"), 40) or ("filled" if _text(item.get("value"), 1200) else "missing")
        if status not in {"filled", "missing", "locked"}:
            status = "missing"
        normalized_fields.append(
            {
                **item,
                "fieldId": _text(item.get("fieldId") or item.get("id"), 100),
                "label": _text(item.get("label") or item.get("fieldName") or item.get("name"), 160),
                "sourceKey": _text(item.get("sourceKey") or item.get("source"), 160),
                "value": _text(item.get("value"), 1600),
                "status": status,
                "sourceRefs": [
                    ref
                    for ref in _as_list(item.get("sourceRefs") or item.get("source_refs"))
                    if isinstance(ref, dict)
                ],
            }
        )
    normalized_cells: list[dict[str, Any]] = []
    for item in cell_values:
        status = _text(item.get("status"), 40) or ("filled" if _text(item.get("value"), 1200) else "missing")
        if status not in {"filled", "missing", "locked"}:
            status = "missing"
        normalized_cells.append(
            {
                **item,
                "tableId": _text(item.get("tableId") or item.get("table_id"), 100),
                "rowIndex": item.get("rowIndex"),
                "cellIndex": item.get("cellIndex"),
                "label": _text(item.get("label"), 160),
                "value": _text(item.get("value"), 1600),
                "status": status,
                "sourceRefs": [
                    ref
                    for ref in _as_list(item.get("sourceRefs") or item.get("source_refs"))
                    if isinstance(ref, dict)
                ],
            }
        )
    return {
        "templateId": _text(payload.get("templateId"), 100)
        or _text(fragment.get("templateId"), 100),
        "sectionId": _text(payload.get("sectionId"), 80)
        or _text(section.get("sectionId"), 80),
        "status": _text(payload.get("status"), 40) or "completed",
        "fieldValues": normalized_fields,
        "cellValues": normalized_cells,
        "blanksToFill": [
            _text(item, 500)
            for item in _as_list(payload.get("blanksToFill") or payload.get("blanks_to_fill"))
            if _text(item, 500)
        ],
        "warnings": [
            _text(item, 500)
            for item in _as_list(payload.get("warnings"))
            if _text(item, 500)
        ],
    }


def _long_text(value: Any, limit: int = 120000) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _normalize_section_rewrite(
    value: Any,
    section: dict[str, Any],
    operation: str,
) -> dict[str, Any]:
    payload = _nested_payload(value)
    normalized_operation = _text(payload.get("operation"), 40) or operation
    if normalized_operation in {"replace", "selection", "replaceSelection"}:
        normalized_operation = "replace_selection"
    if normalized_operation not in {"append", "rewrite", "replace_selection"}:
        normalized_operation = operation if operation in {"append", "rewrite", "replace_selection"} else "append"
    source_refs = [
        item
        for item in _as_list(payload.get("sourceRefs") or payload.get("source_refs"))
        if isinstance(item, dict)
    ]
    content = _long_text(payload.get("content"))
    replacement = _long_text(payload.get("replacement") or payload.get("text"))
    changed_value = payload.get("changed")
    changed = bool(changed_value) if isinstance(changed_value, bool) else bool(content or replacement)
    return {
        "sectionId": _text(payload.get("sectionId"), 80)
        or _text(section.get("sectionId"), 80),
        "operation": normalized_operation,
        "content": content,
        "replacement": replacement,
        "sourceRefs": source_refs,
        "changed": changed,
        "blanksToFill": [
            _text(item, 500)
            for item in _as_list(payload.get("blanksToFill") or payload.get("blanks_to_fill"))
            if _text(item, 500)
        ],
        "warnings": [
            _text(item, 500)
            for item in _as_list(payload.get("warnings"))
            if _text(item, 500)
        ],
    }


def _normalize_compliance_review(
    value: Any,
    *,
    section_ids: set[str],
    requirement_ids: set[str],
    evaluation_ids: set[str],
) -> dict[str, Any]:
    payload = _nested_payload(value)

    def integer(value: Any) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0

    raw_coverage = payload.get("coverage")
    coverage = raw_coverage if isinstance(raw_coverage, dict) else {}
    uncovered_requirements = [
        _text(item, 80)
        for item in _as_list(coverage.get("uncoveredRequirementIds"))
        if _text(item, 80) in requirement_ids
    ]
    uncovered_evaluations = [
        _text(item, 80)
        for item in _as_list(coverage.get("uncoveredEvaluationIds"))
        if _text(item, 80) in evaluation_ids
    ]

    risk_items: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(payload.get("riskItems")), start=1):
        if not isinstance(item, dict):
            continue
        section_id = _text(item.get("sectionId"), 80)
        requirement_id = _text(item.get("requirementId"), 80)
        evaluation_id = _text(item.get("evaluationId"), 80)
        risk_items.append(
            {
                **item,
                "riskId": _text(item.get("riskId"), 100) or f"risk-{index:03d}",
                "level": _text(item.get("level"), 20) or "中",
                "category": _text(item.get("category"), 80) or "其他",
                "title": _text(item.get("title"), 240) or "待人工复核",
                "detail": _text(item.get("detail"), 2000),
                "sectionId": section_id if section_id in section_ids else "",
                "requirementId": requirement_id if requirement_id in requirement_ids else "",
                "evaluationId": evaluation_id if evaluation_id in evaluation_ids else "",
                "sourceLocator": _text(item.get("sourceLocator"), 300),
                "suggestion": _text(item.get("suggestion"), 1200),
                "sourceRefs": [
                    ref
                    for ref in _as_list(item.get("sourceRefs") or item.get("source_refs"))
                    if isinstance(ref, dict)
                ],
            }
        )
    status = _text(payload.get("status"), 40) or ("needs_review" if risk_items else "pass")
    if status not in {"pass", "needs_review", "failed"}:
        status = "needs_review"
    return {
        "status": status,
        "summary": _text(payload.get("summary"), 4000),
        "coverage": {
            "requirementTotal": integer(coverage.get("requirementTotal")),
            "requirementCovered": integer(coverage.get("requirementCovered")),
            "mandatoryTotal": integer(coverage.get("mandatoryTotal")),
            "mandatoryCovered": integer(coverage.get("mandatoryCovered")),
            "evaluationTotal": integer(coverage.get("evaluationTotal")),
            "evaluationCovered": integer(coverage.get("evaluationCovered")),
            "uncoveredRequirementIds": list(dict.fromkeys(uncovered_requirements)),
            "uncoveredEvaluationIds": list(dict.fromkeys(uncovered_evaluations)),
        },
        "riskItems": risk_items,
        "warnings": [
            _text(item, 500)
            for item in _as_list(payload.get("warnings"))
            if _text(item, 500)
        ],
    }


class PiWritingOrchestrator:
    """使用 Pi 编排目录规划，并在正文阶段按需执行条款覆盖映射。"""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.provider = "pi"
        self.model = str(getattr(getattr(client, "config", None), "model", "") or "")

    def plan_outline(
        self,
        *,
        parse_bundle: dict[str, Any],
        existing_plan: list[dict[str, Any]],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        agent_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        successful_agents = 0
        result_lock = Lock()

        def run_agent(
            agent_id: str,
            fn: Callable[[], dict[str, Any]],
            input_refs: list[str],
        ) -> dict[str, Any]:
            nonlocal successful_agents
            started = now_iso()
            try:
                raw = fn()
                if not isinstance(raw, dict) or not raw:
                    raise ValueError("Pi 子智能体返回为空")
                with result_lock:
                    successful_agents += 1
                agent_warnings = [
                    _text(item, 500)
                    for item in _as_list(raw.get("warnings"))
                    if _text(item, 500)
                ]
                result = build_agent_result(
                    agent_id=agent_id,
                    provider=self.provider,
                    model=self.model,
                    status="success",
                    input_refs=input_refs,
                    output=raw,
                    source_refs=raw.get("sourceRefs")
                    if isinstance(raw.get("sourceRefs"), list)
                    else [],
                    warnings=agent_warnings,
                    run_id=f"{project_id or 'project'}-{agent_id}",
                    created_at=started,
                    finished_at=now_iso(),
                )
                with result_lock:
                    warnings.extend(agent_warnings)
                    agent_results.append(result)
                return raw
            except Exception as exc:
                message = f"{agent_id} 执行失败：{str(exc)[:500]}"
                result = build_agent_result(
                    agent_id=agent_id,
                    provider=self.provider,
                    model=self.model,
                    status="failed",
                    input_refs=input_refs,
                    output={},
                    warnings=[message],
                    run_id=f"{project_id or 'project'}-{agent_id}",
                    created_at=started,
                    finished_at=now_iso(),
                )
                with result_lock:
                    warnings.append(message)
                    agent_results.append(result)
                return {}

        from ..agent_runtime.submission_structure import normalize_submission_structure

        parsed_structure = normalize_submission_structure(
            parse_bundle.get("submissionStructure")
        )
        volume_raw = run_agent(
            "VolumePlanAgent",
            lambda: self.client.plan_volumes_agent(
                parse_bundle=parse_bundle,
                existing_plan=existing_plan,
                workspace_id=workspace_id,
                project_id=project_id,
            ),
            ["parse:submissionStructure", "parse:bidFormatSpec"],
        )
        proposed = normalize_submission_structure({
            "volumes": volume_raw.get("volumes"),
        })["volumes"]
        planned_volumes = parsed_structure["volumes"] or proposed
        if not planned_volumes:
            planned_volumes = [{
                "volumeId": "volume-main",
                "title": "投标响应文件",
                "order": 1,
                "required": True,
                "sourceLocator": "",
                "formatItemIds": [],
            }]
            warnings.append("卷规划未返回有效卷，已使用单卷逻辑分组。")
        outline_bundle = dict(parse_bundle)
        outline_bundle["plannedVolumes"] = planned_volumes

        policy = resolve_policy()
        outline_tools = ""
        outline_extensions: tuple[str, ...] = ()
        outline_env: dict[str, str] = {}
        if policy["enabled"]:
            extension = extension_path()
            if extension.is_file():
                context_path = write_context(
                    build_context(
                        outline_bundle,
                        existing_plan,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        policy=policy,
                    )
                )
                outline_tools = OUTLINE_TOOL_ALLOWLIST
                outline_extensions = (str(extension),)
                outline_env = {"BID_OUTLINE_CONTEXT": str(context_path)}
            else:
                warnings.append(
                    "未找到目录规划工具扩展，本次智能体不启用工具取证（按提示词中的解析视图作答）。"
                )
        outline_raw = run_agent(
            "OutlinePlanAgent",
            lambda: self.client.plan_outline_agent(
                parse_bundle=outline_bundle,
                existing_plan=existing_plan,
                workspace_id=workspace_id,
                project_id=project_id,
                tools=outline_tools,
                extensions=outline_extensions,
                env_extra=outline_env,
            ),
            ["parse:ParseBundle", "writing:plannedVolumes", "workspace:existingPlan"],
        )
        requirement_ids = {
            _text(item.get("id"), 80)
            for item in _as_list(parse_bundle.get("requirements"))
            if isinstance(item, dict) and _text(item.get("id"), 80)
        }
        evaluation_ids = {
            _text(item.get("id") or item.get("evaluationId"), 80)
            for item in _as_list(parse_bundle.get("evaluationItems"))
            if isinstance(item, dict)
            and _text(item.get("id") or item.get("evaluationId"), 80)
        }
        plan = _normalize_plan(
            outline_raw,
            valid_requirement_ids=requirement_ids,
            valid_evaluation_ids=evaluation_ids,
        )
        if not plan:
            raise PiWritingOrchestrationError(
                "Pi OutlinePlanAgent 没有返回有效目录"
            )
        from ..bid_writing.volumes import (
            arrange_volumes,
            reconcile_plan_with_submission_structure,
        )

        plan = reconcile_plan_with_submission_structure(
            plan,
            {**parsed_structure, "volumes": planned_volumes},
            keep_derived=policy["enabled"],
        )
        if policy["enabled"]:
            dropped = _dropped_derived_titles(plan, outline_raw)
            if dropped:
                warnings.append(
                    "目录智能体新增的 %d 个章节未通过放置规则被丢弃（%s）："
                    "它们必须挂在既有卷下或可承载正文的文件项下。"
                    % (len(dropped), "、".join(dropped[:6]))
                )
        if not plan:
            raise PiWritingOrchestrationError(
                "明确的投标文件格式未能生成有效卷内目录"
            )
        plan, volume_warnings = arrange_volumes(
            plan, {**parsed_structure, "volumes": planned_volumes}
        )
        warnings.extend(volume_warnings)
        has_narrative_item = any(
            item.get("level") == 2
            and (
                item.get("itemType") == "narrative"
                or item.get("responseType") == "chapter_text"
            )
            for item in plan
        )
        # 智能体据证据新增的叙述章节本身就直接挂在卷下（level=2），也算“卷内成章”。
        has_derived_section = any(item.get("derived") for item in plan)
        if (
            len(requirement_ids) > 1
            and has_narrative_item
            and not has_derived_section
            and not any(
                item.get("level", 1) >= 3
                and item.get("responseType") != "section_group"
                and item.get("kind") != "deviation"
                for item in plan
            )
        ):
            raise PiWritingOrchestrationError(
                "Pi OutlinePlanAgent 未生成卷内多级章节"
            )

        quality_issues = _outline_quality_issues(plan)
        if quality_issues:
            repair_bundle = {
                **outline_bundle,
                "outlineRepairFeedback": (
                    "上一次目录未通过质量检查："
                    + "；".join(quality_issues)
                    + "。请压缩为最多 100 个模型生成的目录项；保留招标文件原文明确列出的卷和文件项，"
                    "把多条原子要求合并到主题章节，不要为单条要求单独造节点，并确保同级标题唯一。"
                ),
            }
            retry_raw = run_agent(
                "OutlinePlanAgent[质量修正]",
                lambda: self.client.plan_outline_agent(
                    parse_bundle=repair_bundle,
                    existing_plan=[],
                    workspace_id=workspace_id,
                    project_id=project_id,
                    tools=outline_tools,
                    extensions=outline_extensions,
                    env_extra=outline_env,
                ),
                ["parse:outlineInstructionContext", "writing:outlineQualityFeedback"],
            )
            retry_plan = _normalize_plan(
                retry_raw,
                valid_requirement_ids=requirement_ids,
                valid_evaluation_ids=evaluation_ids,
            )
            if retry_plan:
                retry_plan = reconcile_plan_with_submission_structure(
                    retry_plan,
                    {**parsed_structure, "volumes": planned_volumes},
                    keep_derived=policy["enabled"],
                )
                retry_plan, retry_volume_warnings = arrange_volumes(
                    retry_plan, {**parsed_structure, "volumes": planned_volumes}
                )
                retry_issues = _outline_quality_issues(retry_plan)
                if not retry_issues:
                    plan = retry_plan
                    warnings.append(
                        "首次目录结果存在重复或过度展开，已由目录智能体自动压缩修正。"
                    )
                    warnings.extend(retry_volume_warnings)
                else:
                    quality_issues = retry_issues
            if _outline_quality_issues(plan):
                raise PiWritingOrchestrationError(
                    "目录结果未通过质量检查：" + "；".join(quality_issues)
                )

        if successful_agents == 0:
            raise PiWritingOrchestrationError("Pi 撰写编排没有任何子智能体成功")

        derived_sections = [item for item in plan if item.get("derived")]
        if outline_env.get("BID_OUTLINE_CONTEXT"):
            shutil.rmtree(
                Path(outline_env["BID_OUTLINE_CONTEXT"]).parent,
                ignore_errors=True,
            )
        if derived_sections:
            warnings.append(
                "目录智能体新增 %d 个章节（%s）。"
                % (
                    len(derived_sections),
                    "、".join(
                        str(item.get("title") or "") for item in derived_sections[:8]
                    ),
                )
            )
        return {
            "plan": plan,
            "coverage": [],
            "uncoveredRequirements": [],
            "duplicatedRequirements": [],
            "warnings": list(dict.fromkeys(item for item in warnings if item)),
            "agentResults": agent_results,
            "provider": self.provider,
            "phase": "outline",
            "requirementMappingStatus": "pending",
            "requirementMapBatchCount": 0,
            "derivedSections": [
                {
                    "sectionId": str(item.get("sectionId") or ""),
                    "title": str(item.get("title") or ""),
                    "origin": str(item.get("derivedOrigin") or ""),
                    "archetype": str(item.get("derivedArchetype") or ""),
                    "basis": str(item.get("derivedBasis") or ""),
                }
                for item in derived_sections
            ],
        }

    def map_requirement_coverage(
        self,
        *,
        parse_bundle: dict[str, Any],
        outline_plan: list[dict[str, Any]],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """在正文阶段按需执行全量要求到目录章节的映射。"""

        agent_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        successful_agents = 0
        result_lock = Lock()

        def run_agent(
            agent_id: str,
            fn: Callable[[], dict[str, Any]],
            input_refs: list[str],
        ) -> dict[str, Any]:
            nonlocal successful_agents
            started = now_iso()
            try:
                raw = fn()
                if not isinstance(raw, dict) or not raw:
                    raise ValueError("Pi 子智能体返回为空")
                with result_lock:
                    successful_agents += 1
                agent_warnings = [
                    _text(item, 500)
                    for item in _as_list(raw.get("warnings"))
                    if _text(item, 500)
                ]
                result = build_agent_result(
                    agent_id=agent_id,
                    provider=self.provider,
                    model=self.model,
                    status="success",
                    input_refs=input_refs,
                    output=raw,
                    source_refs=raw.get("sourceRefs")
                    if isinstance(raw.get("sourceRefs"), list)
                    else [],
                    warnings=agent_warnings,
                    run_id=f"{project_id or 'project'}-{agent_id}",
                    created_at=started,
                    finished_at=now_iso(),
                )
                with result_lock:
                    warnings.extend(agent_warnings)
                    agent_results.append(result)
                return raw
            except Exception as exc:
                message = f"{agent_id} 执行失败：{str(exc)[:500]}"
                result = build_agent_result(
                    agent_id=agent_id,
                    provider=self.provider,
                    model=self.model,
                    status="failed",
                    input_refs=input_refs,
                    output={},
                    warnings=[message],
                    run_id=f"{project_id or 'project'}-{agent_id}",
                    created_at=started,
                    finished_at=now_iso(),
                )
                with result_lock:
                    warnings.append(message)
                    agent_results.append(result)
                return {}

        section_ids = {
            _text(item.get("sectionId"), 80)
            for item in outline_plan
            if isinstance(item, dict) and _text(item.get("sectionId"), 80)
        }
        requirement_items = [
            item
            for item in _as_list(parse_bundle.get("requirements"))
            if isinstance(item, dict)
            and _text(item.get("id") or item.get("requirementId"), 80)
        ]
        requirement_ids = {
            _text(item.get("id") or item.get("requirementId"), 80)
            for item in requirement_items
        }
        evaluation_ids = {
            _text(item.get("id") or item.get("evaluationId"), 80)
            for item in _as_list(parse_bundle.get("evaluationItems"))
            if isinstance(item, dict)
            and _text(item.get("id") or item.get("evaluationId"), 80)
        }
        batch_size = 80
        requirement_batches = [
            requirement_items[index : index + batch_size]
            for index in range(0, len(requirement_items), batch_size)
        ] or [[]]

        def map_batch(batch_index: int, batch: list[dict[str, Any]]) -> dict[str, Any]:
            batch_bundle = dict(parse_bundle)
            batch_bundle["requirements"] = batch
            batch_label = (
                "RequirementMapAgent"
                if len(requirement_batches) == 1
                else f"RequirementMapAgent[{batch_index}/{len(requirement_batches)}]"
            )
            coverage_raw = run_agent(
                batch_label,
                lambda batch_bundle=batch_bundle: self.client.map_requirements(
                    parse_bundle=batch_bundle,
                    outline_plan=outline_plan,
                    workspace_id=workspace_id,
                    project_id=project_id,
                ),
                [
                    "parse:ParseBundle",
                    "writing:outlinePlan",
                    f"requirementBatch:{batch_index}/{len(requirement_batches)}",
                ],
            )
            return _normalize_coverage(
                coverage_raw,
                section_ids=section_ids,
                requirement_ids=requirement_ids,
                evaluation_ids=evaluation_ids,
            )

        indexed_batches = list(enumerate(requirement_batches, start=1))
        if len(indexed_batches) > 1:
            with ThreadPoolExecutor(max_workers=min(3, len(indexed_batches))) as executor:
                mapped_batches = list(
                    executor.map(
                        lambda pair: map_batch(pair[0], pair[1]),
                        indexed_batches,
                    )
                )
        else:
            mapped_batches = [map_batch(indexed_batches[0][0], indexed_batches[0][1])]

        coverage = {
            "coverage": [],
            "uncoveredRequirements": [],
            "duplicatedRequirements": [],
            "warnings": [],
        }
        seen_coverage: dict[tuple[str, str], dict[str, Any]] = {}
        seen_uncovered: set[str] = set()
        seen_duplicated: set[tuple[str, str]] = set()
        for batch_coverage in mapped_batches:
            coverage["warnings"].extend(batch_coverage["warnings"])
            for item in batch_coverage["coverage"]:
                key = (
                    _text(item.get("requirementId"), 80),
                    _text(item.get("sectionId"), 80),
                )
                if not key[0] or not key[1]:
                    continue
                existing = seen_coverage.get(key)
                if existing is None:
                    seen_coverage[key] = dict(item)
                    coverage["coverage"].append(seen_coverage[key])
                else:
                    existing["evaluationIds"] = list(
                        dict.fromkeys(
                            _as_list(existing.get("evaluationIds"))
                            + _as_list(item.get("evaluationIds"))
                        )
                    )
            for item in batch_coverage["uncoveredRequirements"]:
                requirement_id = _text(item.get("requirementId"), 80)
                if requirement_id and requirement_id not in seen_uncovered:
                    seen_uncovered.add(requirement_id)
                    coverage["uncoveredRequirements"].append(item)
            for item in batch_coverage["duplicatedRequirements"]:
                requirement_id = _text(item.get("requirementId"), 80)
                reason = _text(item.get("reason"), 240)
                key = (requirement_id, reason)
                if requirement_id and key not in seen_duplicated:
                    seen_duplicated.add(key)
                    coverage["duplicatedRequirements"].append(item)

        coverage["warnings"] = list(
            dict.fromkeys(item for item in coverage["warnings"] if item)
        )
        warnings.extend(coverage["warnings"])
        covered_requirement_ids = {
            _text(item.get("requirementId"), 80)
            for item in coverage["coverage"]
            if isinstance(item, dict) and _text(item.get("requirementId"), 80)
        }
        reported_uncovered_ids = {
            _text(item.get("requirementId"), 80)
            for item in coverage["uncoveredRequirements"]
            if isinstance(item, dict) and _text(item.get("requirementId"), 80)
        }
        missing_requirement_ids = sorted(
            requirement_ids - covered_requirement_ids - reported_uncovered_ids
        )
        if missing_requirement_ids:
            coverage["uncoveredRequirements"].extend(
                {
                    "requirementId": requirement_id,
                    "reason": (
                        "RequirementMapAgent 未返回该要求的有效章节覆盖关系，"
                        "需人工确认应由哪个目录章节响应。"
                    ),
                }
                for requirement_id in missing_requirement_ids
            )
            warnings.append(
                f"正文阶段的条款映射缺少 {len(missing_requirement_ids)} 条要求，"
                "已标记为未覆盖。"
            )
        if not coverage["coverage"] and requirement_ids:
            warnings.append("RequirementMapAgent 未返回有效条款覆盖关系，请人工复核正文。")
        if successful_agents == 0 and requirement_items:
            raise PiWritingOrchestrationError("Pi 条款映射没有任何子智能体成功")

        return {
            "coverage": coverage["coverage"],
            "uncoveredRequirements": coverage["uncoveredRequirements"],
            "duplicatedRequirements": coverage["duplicatedRequirements"],
            "warnings": list(dict.fromkeys(item for item in warnings if item)),
            "agentResults": agent_results,
            "provider": self.provider,
            "phase": "requirement_mapping",
            "requirementMappingStatus": "completed",
            "requirementMapBatchCount": len(requirement_batches),
        }

    def draft_section(
        self,
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
    ) -> dict[str, Any]:
        """按材料匹配 -> 写作计划 -> 正文生成顺序编排单个章节。"""

        agent_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        successful_agents = 0
        section_id = _text(section.get("sectionId"), 80)
        writing_profile = resolve_writing_profile(section)
        scoped_knowledge = _scope_knowledge(
            knowledge,
            writing_profile=writing_profile,
        )
        valid_requirement_ids = {
            _text(item.get("id") or item.get("requirementId"), 80)
            for item in _as_list(parse_bundle.get("requirements"))
            if isinstance(item, dict)
            and _text(item.get("id") or item.get("requirementId"), 80)
        }
        valid_evaluation_ids = {
            _text(item.get("id") or item.get("evaluationId"), 80)
            for item in _as_list(parse_bundle.get("evaluationItems"))
            if isinstance(item, dict)
            and _text(item.get("id") or item.get("evaluationId"), 80)
        }
        known_material_ids = _collect_material_ids(scoped_knowledge)
        section_coverage = [
            item
            for item in coverage
            if isinstance(item, dict)
            and (
                not _text(item.get("sectionId"), 80)
                or _text(item.get("sectionId"), 80) == section_id
            )
            and (
                not _text(item.get("requirementId") or item.get("id"), 80)
                or _text(item.get("requirementId") or item.get("id"), 80)
                in valid_requirement_ids
            )
        ]
        requirement_ids = {
            _text(item.get("requirementId") or item.get("id"), 80)
            for item in section_coverage
            if isinstance(item, dict)
            and _text(item.get("requirementId") or item.get("id"), 80)
        }
        requirement_ids.update(
            _text(item, 80)
            for item in _as_list(section.get("requirementIds"))
            if _text(item, 80) in valid_requirement_ids
        )
        section_evaluation_ids = {
            _text(item, 80)
            for item in _as_list(section.get("evaluationIds"))
            if _text(item, 80) in valid_evaluation_ids
        }
        for item in section_coverage:
            section_evaluation_ids.update(
                _text(value, 80)
                for value in _as_list(item.get("evaluationIds"))
                if _text(value, 80) in valid_evaluation_ids
            )

        def run_agent(
            agent_id: str,
            fn: Callable[[], dict[str, Any]],
            input_refs: list[str],
        ) -> dict[str, Any]:
            nonlocal successful_agents
            started = now_iso()
            try:
                raw = fn()
                if not isinstance(raw, dict) or not raw:
                    raise ValueError("Pi 子智能体返回为空")
                successful_agents += 1
                agent_warnings = [
                    _text(item, 500)
                    for item in _as_list(raw.get("warnings"))
                    if _text(item, 500)
                ]
                warnings.extend(agent_warnings)
                agent_results.append(
                    build_agent_result(
                        agent_id=agent_id,
                        provider=self.provider,
                        model=self.model,
                        status="success",
                        input_refs=input_refs + [f"writingProfile:{writing_profile}"],
                        output=raw,
                        source_refs=raw.get("sourceRefs")
                        if isinstance(raw.get("sourceRefs"), list)
                        else [],
                        warnings=agent_warnings,
                        run_id=f"{project_id or 'project'}-{section_id or 'section'}-{agent_id}",
                        created_at=started,
                        finished_at=now_iso(),
                    )
                )
                agent_results[-1]["writingProfile"] = writing_profile
                return raw
            except Exception as exc:
                message = f"{agent_id} 执行失败：{str(exc)[:500]}"
                warnings.append(message)
                agent_results.append(
                    build_agent_result(
                        agent_id=agent_id,
                        provider=self.provider,
                        model=self.model,
                        status="failed",
                        input_refs=input_refs + [f"writingProfile:{writing_profile}"],
                        output={},
                        warnings=[message],
                        run_id=f"{project_id or 'project'}-{section_id or 'section'}-{agent_id}",
                        created_at=started,
                        finished_at=now_iso(),
                    )
                )
                agent_results[-1]["writingProfile"] = writing_profile
                return {}

        material_raw = run_agent(
            "MaterialMatchAgent",
            lambda: self.client.match_materials(
                parse_bundle=parse_bundle,
                coverage=section_coverage,
                section=section,
                project=project,
                company=company,
                knowledge=scoped_knowledge,
                mode=mode,
                writing_profile=writing_profile,
                workspace_id=workspace_id,
                project_id=project_id,
            ),
            ["parse:ParseBundle", "writing:RequirementCoverageMap", "library:companyMaterials"],
        )
        material_matches = _normalize_material_matches(
            material_raw,
            requirement_ids=requirement_ids,
            allowed_material_ids=known_material_ids,
        )
        warnings.extend(material_matches["warnings"])

        writing_plan_raw = run_agent(
            "SectionPlanAgent",
            lambda: self.client.plan_section(
                parse_bundle=parse_bundle,
                coverage=section_coverage,
                section=section,
                material_matches=material_matches,
                project=project,
                company=company,
                knowledge=scoped_knowledge,
                mode=mode,
                writing_profile=writing_profile,
                workspace_id=workspace_id,
                project_id=project_id,
            ),
            ["parse:ParseBundle", "writing:RequirementCoverageMap", "writing:MaterialMatchResult"],
        )
        writing_plan = _normalize_section_plan(
            writing_plan_raw,
            section,
            requirement_ids=requirement_ids,
            evaluation_ids=section_evaluation_ids,
            material_ids=known_material_ids,
        )
        warnings.extend(writing_plan["warnings"])

        draft_raw = run_agent(
            "SectionDraftAgent",
            lambda: self.client.draft_section_agent(
                parse_bundle=parse_bundle,
                coverage=section_coverage,
                section=section,
                writing_plan=writing_plan,
                material_matches=material_matches,
                project=project,
                company=company,
                knowledge=scoped_knowledge,
                mode=mode,
                writing_profile=writing_profile,
                workspace_id=workspace_id,
                project_id=project_id,
            ),
            ["parse:ParseBundle", "writing:SectionWritingPlan", "writing:MaterialMatchResult"],
        )
        draft = _normalize_section_draft(
            draft_raw,
            section,
            material_ids=known_material_ids,
        )
        warnings.extend(draft["warnings"])
        incomplete_material_ids = set(material_matches["unmatchedRequirementIds"])
        incomplete_material_ids.update(
            _text(item.get("requirementId"), 80)
            for item in material_matches["matches"]
            if isinstance(item, dict)
            and _text(item.get("requirementId"), 80)
            and _text(item.get("matchStatus"), 40) in {"partial", "unmatched", "needs_review"}
        )
        if incomplete_material_ids and "【需人工补充：" not in draft["content"]:
            draft["content"] = (
                f'{draft["content"].rstrip()}\n\n'
                "【需人工补充：缺少具体资料】"
            ).strip()
            draft["status"] = "needs_review"
            draft["warnings"] = list(
                dict.fromkeys(
                    [
                        *draft["warnings"],
                        "当前章节存在未匹配或部分匹配的企业材料，正文已追加人工补充标记。",
                    ]
                )
            )
            warnings.extend(draft["warnings"])
        if not draft["content"].strip():
            raise PiWritingOrchestrationError(
                f"Pi SectionDraftAgent 没有返回有效正文：{section_id or 'unknown-section'}"
            )
        if successful_agents == 0:
            raise PiWritingOrchestrationError("Pi 章节编排没有任何子智能体成功")

        return {
            **draft,
            "materialMatches": material_matches["matches"],
            "unmatchedMaterialRequirementIds": material_matches["unmatchedRequirementIds"],
            "writingPlan": writing_plan,
            "agentResults": agent_results,
            "warnings": list(dict.fromkeys(item for item in warnings if item)),
            "provider": self.provider,
            "phase": "section_draft",
            "writingProfile": writing_profile,
            "materialScopes": list(material_keys_for_writing_profile(writing_profile)),
        }

    def rewrite_section(
        self,
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
    ) -> dict[str, Any]:
        """编排单章局部补写/重写；模板保护由 Store 在应用结果前执行。"""

        agent_id = "SectionRewriteAgent"
        writing_profile = resolve_writing_profile(section)
        scoped_knowledge = _scope_knowledge(
            knowledge,
            writing_profile=writing_profile,
        )
        started = now_iso()
        try:
            raw = self.client.rewrite_section(
                parse_bundle=parse_bundle,
                coverage=coverage,
                section=section,
                current_content=current_content,
                instruction=instruction,
                operation=operation,
                selected_text=selected_text,
                project=project,
                company=company,
                knowledge=scoped_knowledge,
                mode=mode,
                writing_profile=writing_profile,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if not isinstance(raw, dict) or not raw:
                raise ValueError("Pi 子智能体返回为空")
            normalized = _normalize_section_rewrite(raw, section, operation)
            if not normalized["changed"]:
                normalized["warnings"] = list(
                    dict.fromkeys(
                        normalized["warnings"] + ["Pi 未返回可应用的章节修改内容。"]
                    )
                )
            normalized["agentResults"] = [
                build_agent_result(
                    agent_id=agent_id,
                    provider=self.provider,
                    model=self.model,
                    status="success",
                    input_refs=[
                        "parse:ParseBundle",
                        "writing:RequirementCoverageMap",
                        "writing:BidSection",
                        "writing:CurrentSectionContent",
                        "library:companyMaterials",
                        f"writingProfile:{writing_profile}",
                    ],
                    output=normalized,
                    source_refs=normalized["sourceRefs"],
                    warnings=normalized["warnings"],
                    run_id=f"{project_id or 'project'}-{section.get('sectionId') or 'section'}-{agent_id}",
                    created_at=started,
                    finished_at=now_iso(),
                )
            ]
            normalized["provider"] = self.provider
            normalized["phase"] = "section_rewrite"
            normalized["writingProfile"] = writing_profile
            normalized["materialScopes"] = list(
                material_keys_for_writing_profile(writing_profile)
            )
            return normalized
        except Exception as exc:
            message = f"{agent_id} 执行失败：{str(exc)[:500]}"
            raise PiWritingOrchestrationError(message) from exc

    def review_compliance(
        self,
        *,
        parse_bundle: dict[str, Any],
        bid_document: dict[str, Any],
        fine_review: dict[str, Any],
        baseline_review: dict[str, Any],
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """调用 ComplianceReviewAgent，返回可定位的合规审查结果。"""

        section_ids = {
            _text(item.get("sectionId"), 80)
            for item in _as_list(bid_document.get("plan"))
            if isinstance(item, dict) and _text(item.get("sectionId"), 80)
        }
        requirement_ids = {
            _text(item.get("id") or item.get("requirementId"), 80)
            for item in _as_list(parse_bundle.get("requirements"))
            if isinstance(item, dict)
            and _text(item.get("id") or item.get("requirementId"), 80)
        }
        evaluation_ids = {
            _text(item.get("id") or item.get("evaluationId"), 80)
            for item in _as_list(parse_bundle.get("evaluationItems"))
            if isinstance(item, dict)
            and _text(item.get("id") or item.get("evaluationId"), 80)
        }
        agent_id = "ComplianceReviewAgent"
        started = now_iso()
        try:
            raw = self.client.review_compliance(
                parse_bundle=parse_bundle,
                bid_document=bid_document,
                fine_review=fine_review,
                baseline_review=baseline_review,
                project=project,
                company=company,
                knowledge=knowledge,
                allowed_section_ids=sorted(section_ids),
                allowed_requirement_ids=sorted(requirement_ids),
                allowed_evaluation_ids=sorted(evaluation_ids),
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if not isinstance(raw, dict) or not raw:
                raise ValueError("Pi 子智能体返回为空")
            normalized = _normalize_compliance_review(
                raw,
                section_ids=section_ids,
                requirement_ids=requirement_ids,
                evaluation_ids=evaluation_ids,
            )
            normalized["agentResults"] = [
                build_agent_result(
                    agent_id=agent_id,
                    provider=self.provider,
                    model=self.model,
                    status="success",
                    input_refs=[
                        "parse:ParseBundle",
                        "writing:BidDocument",
                        "matching:fineReview",
                        "review:DeterministicProofread",
                    ],
                    output=normalized,
                    source_refs=[
                        ref
                        for item in normalized["riskItems"]
                        for ref in item.get("sourceRefs", [])
                        if isinstance(ref, dict)
                    ],
                    warnings=normalized["warnings"],
                    run_id=f"{project_id or 'project'}-{agent_id}",
                    created_at=started,
                    finished_at=now_iso(),
                )
            ]
            normalized["provider"] = self.provider
            normalized["phase"] = "compliance_review"
            return normalized
        except Exception as exc:
            message = f"{agent_id} 执行失败：{str(exc)[:500]}"
            raise PiWritingOrchestrationError(message) from exc

    def fill_template_fragment(
        self,
        *,
        parse_bundle: dict[str, Any],
        fragment: dict[str, Any],
        section: dict[str, Any],
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """为固定模板/固定表格编排 TableFormFillAgent。"""

        agent_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        started = now_iso()
        agent_id = "TableFormFillAgent"
        try:
            raw = self.client.fill_template_fields(
                parse_bundle=parse_bundle,
                fragment=fragment,
                section=section,
                project=project,
                company=company,
                knowledge=knowledge,
                workspace_id=workspace_id,
                project_id=project_id,
            )
            if not isinstance(raw, dict) or not raw:
                raise ValueError("Pi 子智能体返回为空")
            normalized = _normalize_template_fill(raw, fragment, section)
            agent_warnings = normalized.get("warnings") or []
            warnings.extend(agent_warnings)
            agent_results.append(
                build_agent_result(
                    agent_id=agent_id,
                    provider=self.provider,
                    model=self.model,
                    status="success",
                    input_refs=[
                        "parse:ParseBundle",
                        "parse:TemplateFragment",
                        "library:companyMaterials",
                    ],
                    output=normalized,
                    source_refs=[
                        ref
                        for ref in (
                            [
                                item
                                for field in normalized.get("fieldValues", [])
                                if isinstance(field, dict)
                                for item in field.get("sourceRefs", [])
                                if isinstance(item, dict)
                            ]
                            + [
                                item
                                for cell in normalized.get("cellValues", [])
                                if isinstance(cell, dict)
                                for item in cell.get("sourceRefs", [])
                                if isinstance(item, dict)
                            ]
                        )
                        if isinstance(ref, dict)
                    ],
                    warnings=agent_warnings,
                    run_id=f"{project_id or 'project'}-{section.get('sectionId') or 'section'}-{agent_id}",
                    created_at=started,
                    finished_at=now_iso(),
                )
            )
            normalized["agentResults"] = agent_results
            normalized["provider"] = self.provider
            normalized["phase"] = "template_fill"
            normalized["warnings"] = list(dict.fromkeys(warnings))
            return normalized
        except Exception as exc:
            message = f"{agent_id} 执行失败：{str(exc)[:500]}"
            agent_results.append(
                build_agent_result(
                    agent_id=agent_id,
                    provider=self.provider,
                    model=self.model,
                    status="failed",
                    input_refs=["parse:ParseBundle", "parse:TemplateFragment"],
                    output={},
                    warnings=[message],
                    run_id=f"{project_id or 'project'}-{section.get('sectionId') or 'section'}-{agent_id}",
                    created_at=started,
                    finished_at=now_iso(),
                )
            )
            raise PiWritingOrchestrationError(message) from exc
