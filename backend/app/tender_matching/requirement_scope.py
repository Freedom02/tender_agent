from __future__ import annotations

import re
from typing import Any


PRIMARY_SECTION_TYPES = {
    "qualification",
    "compliance",
    "technical",
    "service_plan",
    "commercial",
    "acceptance",
    "pricing",
    "rejection",
    "personnel",
    "performance",
}

TABLE_TARGET_TERMS = {
    "qualification": ("资格审查", "资格性审查", "资格条件", "审查内容"),
    "compliance": ("符合性审查", "符合性检查", "实质性响应", "偏离"),
    "technical": ("技术参数", "技术要求", "技术和服务要求", "配置要求", "投标响应", "差异说明"),
    "service_plan": (
        "服务方案",
        "实施方案",
        "项目理解",
        "组织方案",
        "质量保障",
        "应急预案",
        "培训方案",
        "运维方案",
    ),
    "commercial": ("商务要求", "商务响应", "服务期限", "交付", "售后", "质保"),
    "acceptance": ("验收标准", "验收要求", "交付及验收"),
    "pricing": ("报价", "分项报价", "单价", "合价", "总价"),
    "document_format": ("投标文件格式", "响应文件格式", "签字", "盖章", "密封", "装订"),
    "rejection": ("无效投标", "投标无效", "废标", "否决"),
    "performance": ("业绩", "类似项目", "案例"),
    "personnel": ("人员", "项目经理", "项目负责人", "团队"),
}

REFERENCE_PATTERNS = (
    re.compile(r"(?:详见|参见|具体(?:内容|要求)?见|见)\s*([^。；;\n\r]{2,80})"),
    re.compile(r"(?:以|按|按照)\s*([^。；;\n\r]{2,80}?)(?:为准|执行)"),
)

REFERENCE_TARGET_STOP = re.compile(
    r"(?:，|,|、且|且|并|。|；|;|$)"
)

BASIC_FIELD_PATTERN = re.compile(
    r"^(?:\d+[、.．)]\s*)?(?:项目名称|项目编号|采购项目编号|招标编号|采购人|招标人|代理机构|"
    r"采购方式|预算金额|最高限价|联系方式|联系人|联系电话|地址|邮箱|开户|账号)[:：]"
)

REQUIREMENT_HINTS = (
    "须",
    "必须",
    "应",
    "应当",
    "不得",
    "提供",
    "具有",
    "具备",
    "不少于",
    "不低于",
    "至少",
    "无效投标",
    "投标无效",
    "废标",
    "否决",
    "★",
    "▲",
    "※",
)

FORM_TITLE_PATTERNS = (
    "技术和服务要求响应表",
    "商务和技术偏离表",
    "技术规格偏离表",
    "技术响应表",
    "商务响应表",
    "响应表",
    "偏离表",
    "投标报价表",
    "分项报价表",
)

PLACEHOLDER_FIELD_PATTERN = (
    r"(?:统一社会信用代码|单位名称|投标人名称|供应商名称|地址|联系人|联系电话|"
    r"电子邮箱|邮箱|邮编|开户行|账号|国别|品牌|规格型号|制造商|型号|产地)"
)
PLACEHOLDER_VALUE_PATTERN = r"(?:[_＿\-—]{2,}|\.{2,}|。{2,}|…{2,}|空白|待填写|请填写)"


def _safe_text(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _strip_title_marker(value: str) -> str:
    return re.sub(
        r"^(?:[①②③④⑤⑥⑦⑧⑨⑩]|[一二三四五六七八九十百]+[、.．]|"
        r"\d+(?:[、.．])?|[（(][一二三四五六七八九十百\d]+[)）])\s*",
        "",
        value,
    ).strip()


def is_form_title(text: str) -> bool:
    value = _safe_text(text, 120)
    if not value or len(value) > 80:
        return False
    title = _strip_title_marker(value)
    return any(title == pattern or title.endswith(pattern) for pattern in FORM_TITLE_PATTERNS)


def is_placeholder_row(text: str) -> bool:
    value = _safe_text(text, 1200)
    if not value:
        return False
    matches = re.findall(
        rf"{PLACEHOLDER_FIELD_PATTERN}\s*[:：]\s*{PLACEHOLDER_VALUE_PATTERN}",
        value,
        flags=re.IGNORECASE,
    )
    return len(matches) >= 2


def _segment_id(segment: dict[str, Any]) -> str:
    return str(segment.get("id") or "")


def _section_id(section: dict[str, Any]) -> str:
    return str(section.get("sectionId") or section.get("id") or "")


def _table_id(table: dict[str, Any]) -> str:
    return str(table.get("tableId") or table.get("id") or "")


def _segment_locator(segment: dict[str, Any], fallback_index: int = 0) -> str:
    return _safe_text(segment.get("sourceLocator"), 160) or f"第{fallback_index + 1}个内容单元"


def _row_texts(table: dict[str, Any]) -> list[str]:
    rows = table.get("rows")
    if not isinstance(rows, list):
        return []
    return [
        _safe_text(row.get("text"), 1200)
        for row in rows
        if isinstance(row, dict) and _safe_text(row.get("text"), 1200)
    ]


def _table_text(table: dict[str, Any]) -> str:
    return "\n".join(_row_texts(table))


def _table_ids_from_segments(segments: list[dict[str, Any]], segment_ids: set[str]) -> set[str]:
    result: set[str] = set()
    for segment in segments:
        if _segment_id(segment) in segment_ids and segment.get("tableId"):
            result.add(str(segment["tableId"]))
    return result


def _is_requirement_like(text: str) -> bool:
    clean = _safe_text(text, 1200)
    if (
        not clean
        or BASIC_FIELD_PATTERN.match(clean)
        or is_form_title(clean)
        or is_placeholder_row(clean)
    ):
        return False
    compact = _compact(clean)
    return len(compact) >= 8 and any(term in compact or term in clean for term in REQUIREMENT_HINTS)


def _table_requirement_type(table: dict[str, Any]) -> str:
    compact = _compact(_table_text(table))
    for requirement_type, terms in TABLE_TARGET_TERMS.items():
        if any(_compact(term) in compact for term in terms):
            return requirement_type
    return ""


def _match_target_sections(target: str, sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact_target = _compact(target)
    if not compact_target:
        return []
    normalized_target = re.sub(r"^(?:招标文件|采购文件|本文件|本项目)", "", target).strip(" ：:")
    compact_normalized_target = _compact(normalized_target)
    target_number_refs = re.findall(r"第[一二三四五六七八九十百千万零〇两0-9]+(?:篇|章|节|部|部分|册|卷)", target)
    target_appendix_refs = re.findall(
        r"附件(?:[一二三四五六七八九十百千万零〇两0-9]+|[（(][一二三四五六七八九十百千万零〇两0-9]+[）)])?",
        target,
    )
    matches: list[dict[str, Any]] = []
    for section in sections:
        section_type = str(section.get("sectionType") or "")
        if section_type not in PRIMARY_SECTION_TYPES and section_type != "appendix":
            continue
        heading = _safe_text(section.get("heading"), 240)
        path = " ".join(str(item) for item in section.get("sectionPath", []) if str(item).strip())
        haystack = _compact(f"{heading} {path}")
        if compact_target and (compact_target in haystack or haystack in compact_target):
            matches.append(section)
            continue
        if compact_normalized_target and (
            compact_normalized_target in haystack or haystack in compact_normalized_target
        ):
            matches.append(section)
            continue
        if target_number_refs and any(_compact(ref) in haystack for ref in target_number_refs):
            matches.append(section)
            continue
        if target_appendix_refs and any(_compact(ref) in haystack for ref in target_appendix_refs):
            matches.append(section)
            continue
        for requirement_type, terms in TABLE_TARGET_TERMS.items():
            if section.get("sectionType") == requirement_type and any(_compact(term) in compact_target for term in terms):
                matches.append(section)
                break
    return matches


def _match_target_tables(target: str, tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact_target = _compact(target)
    if not compact_target:
        return []
    normalized_target = re.sub(r"^(?:招标文件|采购文件|本文件|本项目)", "", target).strip(" ：:")
    compact_normalized_target = _compact(normalized_target)
    matches: list[dict[str, Any]] = []
    for table in tables:
        table_compact = _compact(_table_text(table))
        if compact_target in table_compact or (
            compact_normalized_target and compact_normalized_target in table_compact
        ):
            matches.append(table)
            continue
        for terms in TABLE_TARGET_TERMS.values():
            if any(_compact(term) in compact_target for term in terms) and any(_compact(term) in table_compact for term in terms):
                matches.append(table)
                break
    return matches


def _section_is_requirement_candidate(section: dict[str, Any]) -> bool:
    section_type = str(section.get("sectionType") or "")
    if section_type in PRIMARY_SECTION_TYPES or section_type == "appendix":
        return True
    heading = _compact(section.get("heading"))
    if any(_compact(term) in heading for terms in TABLE_TARGET_TERMS.values() for term in terms):
        return True
    return any(
        _is_requirement_like(_safe_text(segment_text, 1200))
        for segment_text in (section.get("texts") or [])
    )


def _extract_reference_target(raw_target: str) -> str:
    target = _safe_text(raw_target, 100)
    target = REFERENCE_TARGET_STOP.split(target, maxsplit=1)[0]
    target = re.sub(r"^(?:本项目|采购需求|要求|内容)", "", target).strip(" ：:")
    return target[:80]


def _reference_targets(segment: dict[str, Any]) -> list[tuple[str, str]]:
    text = _safe_text(segment.get("text"), 1200)
    targets: list[tuple[str, str]] = []
    for pattern in REFERENCE_PATTERNS:
        for match in pattern.finditer(text):
            target = _extract_reference_target(match.group(1))
            if target:
                targets.append((target, _safe_text(match.group(0), 140)))
    return targets


def select_requirement_scope(
    segments: list[dict[str, Any]],
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    section_list = [section for section in sections or [] if isinstance(section, dict)]
    table_list = [table for table in tables or [] if isinstance(table, dict)]
    segment_list = [segment for segment in segments or [] if isinstance(segment, dict)]

    selected_section_ids: set[str] = set()
    selected_segment_ids: set[str] = set()
    selected_table_ids: set[str] = set()
    section_reasons: dict[str, str] = {}
    table_reasons: dict[str, str] = {}
    segment_reasons: dict[str, str] = {}
    section_reference_from: dict[str, str] = {}
    table_reference_from: dict[str, str] = {}
    references: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for section in section_list:
        if not _section_is_requirement_candidate(section):
            continue
        section_id = _section_id(section)
        if section_id:
            selected_section_ids.add(section_id)
            section_reasons.setdefault(section_id, "目标章节")
        for segment_id in section.get("segmentIds") or []:
            selected_segment_ids.add(str(segment_id))
            segment_reasons.setdefault(str(segment_id), f"{section.get('heading', '')}章节")

    for table in table_list:
        table_type = _table_requirement_type(table)
        table_id = _table_id(table)
        if not table_id or not _table_text(table).strip():
            continue
        selected_table_ids.add(table_id)
        if table_type:
            table_reasons.setdefault(table_id, f"{TABLE_TARGET_TERMS[table_type][0]}表格")
        else:
            table_reasons.setdefault(table_id, "非空表格候选")

    selected_table_ids.update(_table_ids_from_segments(segment_list, selected_segment_ids))

    for index, segment in enumerate(segment_list):
        source_locator = _segment_locator(segment, index)
        for target, reference_text in _reference_targets(segment):
            matched_sections: list[dict[str, Any]] = []
            matched_tables: list[dict[str, Any]] = []
            if _compact(target) == _compact("本表") and segment.get("tableId"):
                matched_tables = [
                    table for table in table_list
                    if _table_id(table) == str(segment.get("tableId"))
                ]
            else:
                matched_sections = _match_target_sections(target, section_list)
                matched_tables = _match_target_tables(target, table_list)

            section_ids = [_section_id(section) for section in matched_sections if _section_id(section)]
            table_ids = [_table_id(table) for table in matched_tables if _table_id(table)]
            reference = {
                "referenceId": f"REF-{len(references) + 1:04d}",
                "referenceText": reference_text,
                "referenceFromSegmentId": _segment_id(segment),
                "referenceFrom": source_locator,
                "targetText": target,
                "resolved": bool(section_ids or table_ids),
                "targetSectionIds": section_ids,
                "targetTableIds": table_ids,
            }
            if reference["resolved"]:
                references.append(reference)
            else:
                unresolved.append(reference)
                source_id = _segment_id(segment)
                if source_id:
                    selected_segment_ids.add(source_id)
                    segment_reasons.setdefault(source_id, "未解析引用来源")
                if segment.get("tableId"):
                    table_id = str(segment.get("tableId"))
                    if any(_table_id(table) == table_id for table in table_list):
                        selected_table_ids.add(table_id)
                        table_reasons.setdefault(table_id, "未解析引用关联表格")
                if index > 0:
                    previous_id = _segment_id(segment_list[index - 1])
                    if previous_id:
                        selected_segment_ids.add(previous_id)
                        segment_reasons.setdefault(previous_id, "未解析引用邻近内容")
                if index + 1 < len(segment_list):
                    next_id = _segment_id(segment_list[index + 1])
                    if next_id:
                        selected_segment_ids.add(next_id)
                        segment_reasons.setdefault(next_id, "未解析引用邻近内容")
                continue

            for section in matched_sections:
                section_id = _section_id(section)
                selected_section_ids.add(section_id)
                section_reasons.setdefault(section_id, f"由{source_locator}引用")
                section_reference_from.setdefault(section_id, source_locator)
                for segment_id in section.get("segmentIds") or []:
                    selected_segment_ids.add(str(segment_id))
                    segment_reasons.setdefault(str(segment_id), f"由{source_locator}引用")
            for table in matched_tables:
                table_id = _table_id(table)
                selected_table_ids.add(table_id)
                table_reasons[table_id] = f"由{source_locator}引用"
                if (
                    table_id not in table_reference_from
                    or not str(table_reference_from[table_id]).startswith("第")
                ) and not source_locator.startswith("TABLE"):
                    table_reference_from[table_id] = source_locator

    for segment in segment_list:
        if str(segment.get("tableId") or "") in selected_table_ids and _segment_id(segment):
            selected_segment_ids.add(_segment_id(segment))
            segment_reasons.setdefault(_segment_id(segment), "目标表格行")

    if not selected_segment_ids:
        for segment in segment_list:
            segment_id = _segment_id(segment)
            text = _safe_text(segment.get("text"), 1200)
            if segment_id and _is_requirement_like(text):
                selected_segment_ids.add(segment_id)
                segment_reasons.setdefault(segment_id, "未识别章节时按条件句兜底")
                if segment.get("tableId"):
                    selected_table_ids.add(str(segment["tableId"]))

    selected_sections = [
        {**section, "scopeReason": section_reasons.get(_section_id(section), "")}
        for section in section_list
        if _section_id(section) in selected_section_ids
    ]
    section_types_by_segment: dict[str, set[str]] = {}
    for section in selected_sections:
        section_type = str(section.get("sectionType") or "")
        for segment_id in section.get("segmentIds") or []:
            section_types_by_segment.setdefault(str(segment_id), set()).add(section_type)
    selected_tables = [
        {**table, "scopeReason": table_reasons.get(_table_id(table), "目标表格")}
        for table in table_list
        if _table_id(table) in selected_table_ids
    ]
    selected_segments = [
        {
            **segment,
            "scopeReason": segment_reasons.get(_segment_id(segment), "目标条件"),
            "scopeSectionTypes": sorted(section_types_by_segment.get(_segment_id(segment), set())),
        }
        for segment in segment_list
        if _segment_id(segment) in selected_segment_ids
    ]

    scope_items: list[dict[str, Any]] = []
    for section in selected_sections:
        scope_items.append(
            {
                "scopeItemId": f"SCOPE-{len(scope_items) + 1:04d}",
                "scopeType": "section",
                "sectionId": _section_id(section),
                "tableId": "",
                "segmentId": "",
                "sectionType": str(section.get("sectionType") or ""),
                "reason": str(section.get("scopeReason") or "目标章节"),
                "sourceLocator": _safe_text(section.get("startLocator"), 160),
                "referenceFrom": section_reference_from.get(_section_id(section), ""),
                "priority": (
                    "referenced"
                    if _section_id(section) in section_reference_from
                    else "primary"
                ),
            }
        )
    for table in selected_tables:
        scope_items.append(
            {
                "scopeItemId": f"SCOPE-{len(scope_items) + 1:04d}",
                "scopeType": "table",
                "sectionId": "",
                "tableId": _table_id(table),
                "segmentId": "",
                "sectionType": "",
                "reason": str(table.get("scopeReason") or "目标表格"),
                "sourceLocator": _safe_text((table.get("rows") or [{}])[0].get("sourceLocator") if table.get("rows") else "", 160),
                "referenceFrom": table_reference_from.get(_table_id(table), ""),
                "priority": "referenced" if _table_id(table) in table_reference_from else "primary",
            }
        )
    for segment in selected_segments:
        if not segment.get("tableId"):
            continue
        scope_items.append(
            {
                "scopeItemId": f"SCOPE-{len(scope_items) + 1:04d}",
                "scopeType": "segment",
                "sectionId": "",
                "tableId": str(segment.get("tableId") or ""),
                "segmentId": _segment_id(segment),
                "sectionType": "",
                "reason": str(segment.get("scopeReason") or "目标条件"),
                "sourceLocator": _safe_text(segment.get("sourceLocator"), 160),
                "referenceFrom": "",
                "priority": "supporting",
            }
        )

    warnings: list[str] = []
    if not selected_sections and not selected_tables and selected_segments:
        warnings.append("未识别到明确招标条件章节，已按条件关键词选择疑似条款。")
    if unresolved:
        warnings.append(f"发现 {len(unresolved)} 处交叉引用未定位到目标章节或表格，请人工核对。")

    return {
        "selectedSegments": selected_segments,
        "selectedSections": selected_sections,
        "selectedTables": selected_tables,
        "scopeItems": scope_items,
        "references": references,
        "unresolvedReferences": unresolved,
        "warnings": warnings,
        "stats": {
            "inputSegmentCount": len(segment_list),
            "inputSectionCount": len(section_list),
            "inputTableCount": len(table_list),
            "selectedSegmentCount": len(selected_segments),
            "selectedSectionCount": len(selected_sections),
            "selectedTableCount": len(selected_tables),
            "resolvedReferenceCount": len(references),
            "unresolvedReferenceCount": len(unresolved),
        },
    }
