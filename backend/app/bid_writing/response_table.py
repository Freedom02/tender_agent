"""Technical/service response-table requirement selection and plan binding."""
from __future__ import annotations

import re
from typing import Any

from .schemas import requirement_domain


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def response_table_domains(section: dict[str, Any]) -> set[str]:
    """Return the requirement domains represented by a fixed response table."""

    title = _compact(section.get("title"))
    if not title or any(term in title for term in ("偏离", "差异", "商务", "报价")):
        return set()

    is_table = (
        str(section.get("responseType") or "").strip() in {"table", "fixed_form"}
        or str(section.get("itemType") or "").strip() in {"fixed_table", "fixed_form"}
        or "表" in title
    )
    is_response = "响应" in title or "应答" in title
    has_technical = any(term in title for term in ("技术", "参数", "规格"))
    has_service = any(term in title for term in ("服务", "实施", "售后"))
    if not is_table or not is_response or not (has_technical or has_service):
        return set()

    domains: set[str] = set()
    if has_technical:
        domains.add("technical")
    if has_service:
        domains.add("service_plan")
    return domains


def _as_values(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def technical_service_requirements(
    fine_review: dict[str, Any] | None,
    *,
    domains: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Select existing fine-review rows for a technical/service response table."""

    payload = fine_review if isinstance(fine_review, dict) else {}
    rows = payload.get("rows")
    if not isinstance(rows, list):
        return []
    allowed_domains = domains or {"technical", "service_plan"}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        category = requirement_domain(row)
        requirement_id = str(row.get("requirementId") or row.get("id") or "").strip()
        requirement = str(
            row.get("requirement")
            or row.get("rawText")
            or row.get("sourceText")
            or ""
        ).strip()
        if (
            category not in allowed_domains
            or not requirement_id
            or not requirement
            or requirement_id in seen
        ):
            continue
        selected.append(row)
        seen.add(requirement_id)
    return selected


def prepare_response_table_inputs(
    section: dict[str, Any],
    fine_review: dict[str, Any] | None,
    knowledge: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build row-scoped writer inputs from saved matches and M3 objects."""

    from .technical import (
        _index_knowledge,
        _resolve_matched_objects,
        _source_ref_for,
    )

    domains = response_table_domains(section)
    rows = technical_service_requirements(fine_review, domains=domains)
    requested_ids = {
        str(value).strip()
        for value in (section.get("requirementIds") or [])
        if str(value).strip()
    }
    if requested_ids:
        rows = [
            row
            for row in rows
            if str(row.get("requirementId") or row.get("id") or "").strip() in requested_ids
        ]

    index = _index_knowledge(knowledge if isinstance(knowledge, dict) else {})
    prepared: list[dict[str, Any]] = []
    for row in rows:
        requirement_id = str(row.get("requirementId") or row.get("id") or "").strip()
        requirement = str(
            row.get("requirement")
            or row.get("rawText")
            or row.get("sourceText")
            or ""
        ).strip()
        alignment = row.get("technicalAlignment")
        alignment_units = (
            alignment.get("matchedUnits")
            if isinstance(alignment, dict)
            else []
        )
        alignment_ids = [
            str(value).strip()
            for unit in alignment_units
            if isinstance(unit, dict)
            for value in (
                unit.get("productId"),
                *_as_values(unit.get("matchedParams")),
            )
            if str(value or "").strip()
        ]
        evidence_item_ids = [
            str(item.get(key)).strip()
            for item in _as_values(row.get("evidenceItems"))
            if isinstance(item, dict)
            for key in ("id", "factId", "materialId", "objectId")
            if item.get(key)
        ]
        match_id_values = [
            value
            for key in ("matchedObjectIds", "matchedMaterialIds", "evidenceIds")
            for value in _as_values(row.get(key))
        ]
        matched_ids = list(
            dict.fromkeys(
                str(value).strip()
                for value in [*match_id_values, *alignment_ids, *evidence_item_ids]
                if str(value).strip()
            )
        )
        records = _resolve_matched_objects(matched_ids, index)
        materials = []
        source_refs = []
        for record, list_key in records:
            material = {
                "materialId": str(record.get("id") or "").strip(),
                "name": str(record.get("name") or "").strip(),
                "description": str(record.get("description") or "").strip(),
                "evidencePath": str(record.get("evidencePath") or "").strip(),
                "sourceGroup": list_key,
                "verificationStatus": str(
                    record.get("verificationStatus")
                    or record.get("status")
                    or ""
                ).strip(),
            }
            if material["materialId"] or material["name"] or material["description"]:
                materials.append(material)
                source_refs.append(_source_ref_for(record, list_key))
        prepared.append(
            {
                "requirementId": requirement_id,
                "requirement": requirement,
                "category": requirement_domain(row),
                "matchStatus": str(row.get("status") or "").strip(),
                "matchReason": str(row.get("reason") or "").strip(),
                "gaps": [
                    str(value).strip()
                    for value in (row.get("gaps") or [])
                    if str(value).strip()
                ],
                "materials": materials,
                "sourceRefs": source_refs,
            }
        )
    return prepared


def _deviation_label(status: str) -> str:
    labels = {
        "满足": "无偏离",
        "部分满足": "部分响应，待核验",
        "不满足": "负偏离",
        "待补材料": "待补充材料",
        "待澄清": "待人工确认",
        "不适用": "不适用",
    }
    return labels.get(status, "待人工确认")


def _cell_text(value: Any) -> str:
    return str(value or "").replace("|", r"\|").replace("\r", " ").replace("\n", "<br>").strip()


def replace_first_markdown_table(content: str, table_markdown: str) -> str:
    """Replace the first Markdown table while preserving surrounding template text."""

    lines = str(content or "").splitlines()
    start = next((index for index, line in enumerate(lines) if line.lstrip().startswith("|")), -1)
    if start < 0:
        return f"{str(content or '').rstrip()}\n\n{table_markdown}".strip()
    end = start
    while end + 1 < len(lines):
        candidate = lines[end + 1].strip()
        if not candidate or candidate.startswith("|"):
            end += 1
            continue
        break
    return "\n".join([*lines[:start], table_markdown, *lines[end + 1 :]]).strip()


def build_response_table_result(
    section: dict[str, Any],
    prepared_rows: list[dict[str, Any]],
    responses: dict[str, str] | None = None,
    *,
    agent_results: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Create editable table content and trusted per-row source references."""

    response_map = responses if isinstance(responses, dict) else {}
    output_rows: list[dict[str, Any]] = []
    source_refs: list[dict[str, Any]] = []
    blanks: list[str] = []
    for index, item in enumerate(prepared_rows, start=1):
        requirement_id = str(item.get("requirementId") or "").strip()
        materials = item.get("materials") if isinstance(item.get("materials"), list) else []
        response = str(response_map.get(requirement_id) or "").strip()
        if not materials:
            response = "【需人工补充：未在 M3 中命中可支撑本条要求的材料】"
            blanks.append(f"{requirement_id}：未命中可用企业材料")
        elif not response:
            material_names = "、".join(
                str(material.get("name") or "").strip()
                for material in materials
                if isinstance(material, dict) and str(material.get("name") or "").strip()
            )
            response = (
                f"已关联企业材料{f'（{material_names}）' if material_names else ''}，"
                "具体参数、范围及符合性需人工核验后确认。"
            )
            blanks.append(f"{requirement_id}：智能体未生成有效响应，需人工核验")

        status = str(item.get("matchStatus") or "").strip()
        row_refs = [
            ref for ref in (item.get("sourceRefs") or [])
            if isinstance(ref, dict)
        ]
        source_refs.extend(row_refs)
        needs_review = not materials or status not in {"满足", "已编制"}
        if needs_review and materials:
            blanks.append(f"{requirement_id}：匹配状态为“{status or '待确认'}”，需人工核验")
        remark = str(item.get("matchReason") or "").strip()
        if item.get("gaps"):
            gap_text = "；".join(str(value) for value in item["gaps"] if str(value).strip())
            remark = f"{remark}；待补：{gap_text}".strip("；")
        deviation = _deviation_label(status)
        if not materials and status in {"满足", "部分满足", "不满足"}:
            deviation = "待人工确认"
        output_rows.append(
            {
                "sequence": index,
                "requirementId": requirement_id,
                "requirement": str(item.get("requirement") or "").strip(),
                "response": response,
                "deviation": deviation,
                "remark": remark,
                "matchStatus": status,
                "needsReview": needs_review,
                "sourceRefs": row_refs,
                "materials": materials,
            }
        )

    headers = ["序号", "招标文件要求", "投标应答", "偏离情况", "备注"]
    markdown = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in output_rows:
        markdown.append(
            "| "
            + " | ".join(
                _cell_text(row[key])
                for key in ("sequence", "requirement", "response", "deviation", "remark")
            )
            + " |"
        )
    title = str(section.get("title") or "技术和服务要求响应表").strip()
    table_markdown = "\n".join(markdown)
    content = f"# {title}\n\n{table_markdown}"
    warning_list = list(dict.fromkeys(str(value).strip() for value in (warnings or []) if str(value).strip()))
    if not output_rows:
        blanks.append("未找到可用于技术/服务响应表的细评审条款")
    return {
        "content": content,
        "tableMarkdown": table_markdown,
        "status": "generated" if output_rows else "missing",
        "requirementResponseRows": output_rows,
        "sourceRefs": source_refs,
        "blanksToFill": list(dict.fromkeys(blanks)),
        "agentResults": list(agent_results or []),
        "warnings": warning_list,
    }


def build_template_response_table_spec(
    fragment: dict[str, Any] | None,
    response_rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Map generated response rows to a template table using its parsed headers."""

    fragment_data = fragment if isinstance(fragment, dict) else {}
    candidates: list[tuple[int, dict[str, Any], dict[str, int], int]] = []
    for table in (fragment_data.get("tables") or []):
        if not isinstance(table, dict):
            continue
        rows = [
            row for row in (table.get("rows") or [])
            if isinstance(row, dict)
        ]
        if not rows:
            continue
        roles: dict[str, int] = {}
        header_end = 0
        for row in rows[:5]:
            row_index = int(row.get("rowIndex") or 0)
            for cell in row.get("cells") or []:
                if not isinstance(cell, dict):
                    continue
                cell_index = int(cell.get("cellIndex") or 0)
                text = _compact(cell.get("text"))
                role = ""
                if any(term in text for term in ("序号", "编号", "项次")):
                    role = "sequence"
                elif any(term in text for term in ("投标人响应", "投标响应", "投标应答", "供应商响应", "响应内容", "响应情况", "应答内容")):
                    role = "response"
                elif "偏离" in text:
                    role = "deviation"
                elif any(term in text for term in ("招标要求", "采购要求", "技术要求", "服务要求", "技术指标", "技术参数", "需求内容", "项目需求", "条款要求")):
                    role = "requirement"
                elif any(term in text for term in ("备注", "说明", "依据", "证明材料")):
                    role = "remark"
                if role and cell_index:
                    roles.setdefault(role, cell_index)
                    header_end = max(header_end, row_index)
        if "requirement" not in roles or "response" not in roles:
            continue
        score = 10 + len(roles) * 2
        candidates.append((score, table, roles, header_end or 1))
    if not candidates:
        return None

    _, table, columns, header_row_count = max(candidates, key=lambda item: item[0])
    table_id = str(table.get("tableId") or table.get("id") or "").strip()
    if not table_id:
        return None
    source_rows = [
        row
        for row in (table.get("rows") or [])
        if isinstance(row, dict)
        and int(row.get("rowIndex") or 0) > header_row_count
    ]

    def cell_text(row: dict[str, Any], cell_index: int) -> str:
        for cell in row.get("cells") or []:
            if isinstance(cell, dict) and int(cell.get("cellIndex") or 0) == cell_index:
                return str(cell.get("text") or "").strip()
        return ""

    def is_blank_slot(value: str) -> bool:
        compact = _compact(value)
        return (
            not compact
            or any(term in compact for term in ("待填写", "投标人填写", "供应商填写", "待补充"))
            or bool(re.fullmatch(r"[_＿—\-□☐（）()：:]*", value.strip()))
        )

    unused_rows = {int(row.get("rowIndex") or 0) for row in source_rows}
    mappings: list[dict[str, Any]] = []
    append_rows: list[dict[str, Any]] = []
    last_used_row = header_row_count

    for response_row in response_rows:
        if not isinstance(response_row, dict):
            continue
        requirement = str(response_row.get("requirement") or "").strip()
        normalized_requirement = _compact(requirement)
        matched_row_index = 0
        for row in source_rows:
            row_index = int(row.get("rowIndex") or 0)
            if row_index not in unused_rows:
                continue
            existing_requirement = _compact(cell_text(row, columns["requirement"]))
            if (
                normalized_requirement
                and existing_requirement
                and (
                    normalized_requirement in existing_requirement
                    or existing_requirement in normalized_requirement
                )
            ):
                matched_row_index = row_index
                break
        if not matched_row_index:
            for row in source_rows:
                row_index = int(row.get("rowIndex") or 0)
                if row_index not in unused_rows:
                    continue
                existing_requirement = cell_text(row, columns["requirement"])
                existing_response = cell_text(row, columns["response"])
                if is_blank_slot(existing_requirement) and is_blank_slot(existing_response):
                    matched_row_index = row_index
                    break

        values = {
            "sequence": str(response_row.get("sequence") or ""),
            "requirement": requirement,
            "response": str(response_row.get("response") or ""),
            "deviation": str(response_row.get("deviation") or ""),
            "remark": str(response_row.get("remark") or ""),
        }
        cell_values = [
            {"cellIndex": cell_index, "value": values[role]}
            for role, cell_index in columns.items()
            if values.get(role)
        ]
        entry = {
            "requirementId": str(response_row.get("requirementId") or ""),
            "rowIndex": matched_row_index or None,
            "cells": cell_values,
        }
        if matched_row_index:
            unused_rows.discard(matched_row_index)
            last_used_row = max(last_used_row, matched_row_index)
            existing_requirement = next(
                (
                    cell_text(row, columns["requirement"])
                    for row in source_rows
                    if int(row.get("rowIndex") or 0) == matched_row_index
                ),
                "",
            )
            if not is_blank_slot(existing_requirement):
                entry["cells"] = [
                    cell for cell in cell_values
                    if int(cell.get("cellIndex") or 0) != columns["requirement"]
                ]
            mappings.append(entry)
        else:
            append_rows.append(entry)

    data_start = header_row_count + 1
    return {
        "tableId": table_id,
        "mapped": True,
        "headerRowCount": header_row_count,
        "columns": columns,
        "dataStartRowIndex": data_start,
        "prototypeRowIndex": data_start if data_start <= max(
            (int(row.get("rowIndex") or 0) for row in (table.get("rows") or []) if isinstance(row, dict)),
            default=header_row_count,
        ) else header_row_count,
        "insertAfterRowIndex": last_used_row,
        "rows": mappings,
        "appendRows": append_rows,
    }


def bind_response_table_requirements(
    plan: list[dict[str, Any]] | None,
    fine_review: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Bind parsed technical/service clauses to matching response-table nodes."""

    result = [dict(section) for section in (plan or []) if isinstance(section, dict)]
    tables = [section for section in result if response_table_domains(section)]
    if not tables:
        return result

    assigned: set[str] = set()
    for section in tables:
        domains = response_table_domains(section)
        rows = technical_service_requirements(fine_review, domains=domains)
        requirement_ids = list(
            dict.fromkeys(
                str(value).strip()
                for value in (section.get("requirementIds") or [])
                if str(value).strip()
            )
        )
        for row in rows:
            requirement_id = str(row.get("requirementId") or row.get("id") or "").strip()
            if requirement_id and requirement_id not in requirement_ids and requirement_id not in assigned:
                requirement_ids.append(requirement_id)
        section["requirementIds"] = requirement_ids
        section["requirementMappingSource"] = "fineReview:technical_service_response_table"
        assigned.update(requirement_ids)
    return result
