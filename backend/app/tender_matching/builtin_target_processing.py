"""Processing and matching for the builtin target-oriented extraction route."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, Mapping

from .builtin_target_map import (
    ENTERPRISE_MATCH_TARGETS,
    MATCH_STAGE_TARGETS,
    RISK_CHECK_TARGETS,
    TARGET_META,
    TARGET_MAP_SCHEMA_VERSION,
    TARGET_MODULES,
    _is_target_noise,
    _owner_section,
    _ranges_from_segments,
    _section_context_target_types,
    _segment_table_type,
    _table_type_hits,
    _strong_body_hits,
    discover_target_ranges,
    project_field_rules,
    rejection_terms,
)
from .contracts import (
    build_comparison,
    summarize_comparisons,
)
from .deterministic import deterministic_match
from .semantic_judge import judge_technical_requirements
from .tech_units import build_tech_units


TARGET_PROCESSING_SCHEMA_VERSION = "tender-targets/1.0"
SOURCE_VIEW_SCHEMA_VERSION = "tender-source-view/1.0"
SOURCE_VIEW_TARGETS = {"commercial", "evaluation", "template"}

# Rejection terms that are context-dependent — they only signal a real
# invalidity risk when the surrounding clause is actually about a bid /
# response, not about a clarifying question, complaint, or procedure.
_WEAK_REJECTION_TERMS: tuple[str, ...] = ("不接受", "不予受理", "不得参加")
_REJECTION_PROCEDURE_TERMS: tuple[str, ...] = (
    "澄清", "质疑", "投诉", "异议", "询问",
)


def _text(value: Any, limit: int = 1600) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _id(value: Any) -> str:
    return _text(value, 120)


def _segment_id(segment: Mapping[str, Any]) -> str:
    return _id(segment.get("id") or segment.get("segmentId"))


def _section_id(section: Mapping[str, Any]) -> str:
    return _id(section.get("sectionId") or section.get("id"))


def _table_id(table: Mapping[str, Any]) -> str:
    return _id(table.get("tableId") or table.get("id"))


def _source_anchor(
    segment: Mapping[str, Any],
    indexes: Mapping[str, Any],
    *,
    section_id: str = "",
) -> dict[str, Any]:
    segment_id = _segment_id(segment)
    section_map = indexes.get("sectionMap", {})
    owners = indexes.get("owners", {}).get(segment_id) or []
    if section_id and section_id in section_map:
        section = section_map[section_id]
    else:
        candidates = [section_map[value] for value in owners if value in section_map]
        section = min(
            candidates,
            key=lambda item: (
                len(item.get("segmentIds") or []),
                -int(item.get("level") or 0),
            ),
            default={},
        )
    table_id = _id(segment.get("tableId"))
    pages = []
    if segment.get("page") is not None:
        try:
            pages.append(int(segment.get("page")))
        except (TypeError, ValueError):
            pass
    anchor: dict[str, Any] = {
        "segmentId": segment_id,
        "segmentIds": [segment_id] if segment_id else [],
        "sourceLocator": _text(segment.get("sourceLocator"), 200),
        "sectionId": _section_id(section),
        "sectionPath": [
            _text(value, 160)
            for value in (section.get("sectionPath") or [])
            if _text(value, 160)
        ],
        "tableIds": [table_id] if table_id else [],
    }
    if pages:
        anchor["pageStart"] = pages[0]
        anchor["pageEnd"] = pages[-1]
    return anchor


def _table_cells(segment: Mapping[str, Any]) -> list[str]:
    raw_cells = segment.get("cells")
    if isinstance(raw_cells, list):
        result = []
        for cell in raw_cells:
            if isinstance(cell, dict):
                # A single technical-parameter cell may contain over one
                # hundred numbered requirements. Do not truncate it before
                # the technical clause splitter runs.
                value = _text(cell.get("text"), 24000)
            else:
                value = _text(cell, 24000)
            if value:
                result.append(value)
        if result:
            return result
    text = _text(segment.get("text"), 24000)
    return [part.strip() for part in text.split("|") if part.strip()]


def _is_section_heading_segment(
    segment_id: str,
    indexes: Mapping[str, Any],
) -> bool:
    """Exclude structural heading paragraphs from extracted requirement rows."""

    return any(
        _id(indexes.get("sectionMap", {}).get(section_id, {}).get("headingSegmentId"))
        == segment_id
        for section_id in indexes.get("owners", {}).get(segment_id, [])
    )


def _is_heading_or_header(text: str) -> bool:
    clean = _text(text, 260)
    if not clean:
        return True
    if "|" in clean:
        cells = [_compact(value) for value in clean.split("|") if _compact(value)]
        headers = {
            "序号",
            "编号",
            "品目号",
            "标的名称",
            "货物名称",
            "技术要求",
            "技术参数",
            "简要技术指标",
            "数量",
            "单位",
            "投标响应",
            "是否偏离",
            "参数性质",
            "类型",
            "要求",
            "资格审查要求概况",
            "评审点具体描述",
            "项目",
            "描述",
            "评分标准",
            "评审因素",
            "分值",
        }
        if len([value for value in cells if value in headers]) >= 2:
            return True
        if (
            cells
            and cells[0] in {"序号", "编号", "项目", "条款号"}
            and any(
                value in headers
                for value in cells[1:]
            )
        ):
            return True
        account_fields = {
            "投标保证金账户",
            "开户名称",
            "开户银行",
            "银行账号",
        }
        if len([value for value in cells if value in account_fields]) >= 2:
            return True
        if (
            "条款号" in cells
            and any(value in cells for value in ("招标文件合同条款", "合同条款"))
            and not any(
                marker in clean
                for marker in ("应", "须", "必须", "提供", "提交", "缴纳", "不得")
            )
        ):
            return True
    without_no = re.sub(
        r"^(?:第?[一二三四五六七八九十百]+[章节、.]|\d+(?:\.\d+)*[、.]|[（(]\d+[)）])\s*",
        "",
        clean,
    )
    return len(_compact(without_no)) < 8 and not any(
        marker in clean for marker in ("。", "；", "：", ":", "，", ",")
    )


def _split_clauses(text: str) -> list[str]:
    raw = str(text or "").replace("\r", "\n")
    lines = [line.strip() for line in raw.split("\n") if line.strip()]
    if len(lines) <= 1:
        return [_text(raw, 6000)] if _text(raw, 6000) else []
    clauses: list[str] = []
    for line in lines:
        if _is_heading_or_header(line) and not any(
            term in line for term in ("要求", "应", "须", "提供", "支持", "参数", "数量")
        ):
            continue
        clauses.append(_text(line, 6000))
    return clauses or [_text(raw, 6000)]


# ── 技术单元格拆解：接回旧路径的「段落级 _CELL_*_RE + 字符串级 _split_requirement_text」──
# 旧实现 `_TECHNICAL_NUMBERED_CLAUSE_RE` 只认「1~3 位数字 + 分隔符」，会漏掉
# 「一、/（一）/（1）/①/数字紧贴中文」等招标文件常见编号格式，导致换一份文件就粘成一片。
# 这里复用 document_fine_matching 里成熟的分句逻辑。

_CELL_SECTION_RE = re.compile(r"^([一二三四五六七八九十百]+)[、.．]\s*(.*)$")
_CELL_SUBSECTION_RE = re.compile(r"^（([一二三四五六七八九十百]+)）\s*(.*)$")
_CELL_ITEM_RE = re.compile(r"^(\d+)\s*[、.．)]\s*(.*)$")
_CELL_ITEM_NOSEP_RE = re.compile(r"^(\d+)(?=[\u4e00-\u9fff])(.*)$")
_CELL_SUBITEM_RE = re.compile(r"^（(\d+)）\s*(.*)$")

# 复合编号标记：`（一）/(一)/（1）/(1)`、`①`、`1、/1．/1)`、`1.`（非小数）。
COMPOUND_MARKER_PATTERN = re.compile(
    r"(?:[（(]\s*[一二三四五六七八九十百千万零〇两0-9]+\s*[）)]|[①②③④⑤⑥⑦⑧⑨⑩]|\d+[、．)]|\d+\.(?!\d))"
)


def _is_heading(text: str) -> bool:
    value = _text(text, 200)
    return len(value) <= 42 and bool(
        re.match(
            r"^(?:第?[一二三四五六七八九十]+[章节、.]|\d+(?:\.\d+)*[、.]|[（(]\d+[)）])",
            value,
        )
    )


def _has_obligation_signal(text: str) -> bool:
    """判断是否真正出现义务表达，避免把「供应商」中的「应」误判为要求。"""
    value = _compact(text)
    if not value:
        return False
    if any(
        term in value
        for term in (
            "必须", "须", "不得", "应当", "应在", "应按", "应按照",
            "应提供", "应提交", "应具有", "应具备", "应完成", "应满足",
            "应符合", "应缴", "应于", "应以", "应由", "应将",
        )
    ):
        return True
    return bool(re.search(r"(?<!供)应(?!商)", value))


def _is_requirement(text: str) -> bool:
    value = _compact(text)
    if len(value) < 8:
        return False
    terms = (
        "不得", "提供", "具备", "具有", "不少于", "不低于", "不超过",
        "不大于", "不小于", "至少", "响应文件", "报价", "合同", "服务期",
        "交付", "验收", "方案", "计划", "预案", "保障", "偏离", "无效投标",
        "投标无效", "否决", "支持", "兼容", "满足", "符合", "参数", "性能",
        "指标", "规格", "精度", "范围", "响应时间", "并发", "吞吐",
        "投标有效期", "交货期", "交付期", "履约期限", "注册资本", "成立年限",
        "联合体", "质保", "售后", "保证金", "数量", "金额", "期限", "日历日",
        "工作日", "个月", "元", "万元", "国产", "进口", "≥", "≤",
    )
    has_requirement_signal = (
        _has_obligation_signal(text)
        or any(term in value for term in terms)
        or bool(re.search(r"(?:\d+(?:\.\d+)?\s*(?:日历日|工作日|天|年|个月|元|万元|%))", text))
        or bool(re.match(r"^(?:\d+(?:\.\d+)*[、.]|[（(]\d+[)）])", _text(text, 100)))
    )
    if (
        _is_heading(text)
        and not has_requirement_signal
        and not any(marker in text for marker in ("★", "▲", "※"))
    ):
        return False
    return has_requirement_signal


def _split_requirement_text(text: str, max_length: int = 1200) -> list[str]:
    clean = _text(text, max_length)
    if not clean:
        return []

    markers = list(COMPOUND_MARKER_PATTERN.finditer(clean))
    if len(markers) >= 2:
        prefix = clean[: markers[0].start()].strip(" ：:；;")
        clauses = [
            clean[
                marker.start() : markers[index + 1].start()
                if index + 1 < len(markers)
                else len(clean)
            ].strip(" ；;。")
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


def _classify_cell_paragraph(text: str) -> tuple[str, str, str, str]:
    """段落层级分类，返回 (kind, num, title, tail)；kind ∈ {大项, 子项, 要求, 子条目, 说明}。"""
    s = text.strip()
    if not s:
        return ("说明", "", "", "")
    match = _CELL_SECTION_RE.match(s)
    if match:
        num, rest = match.group(1), match.group(2).strip()
        title, tail = rest, ""
        for sep in ("：", ":"):
            if sep in rest:
                title, tail = rest.split(sep, 1)
                break
        return ("大项", num, title.strip().strip("：:"), tail.strip().strip("：:"))
    match = _CELL_SUBSECTION_RE.match(s)
    if match:
        return ("子项", match.group(1), match.group(2).strip(), "")
    match = _CELL_ITEM_RE.match(s)
    if match:
        return ("要求", match.group(1), match.group(2).strip(), "")
    match = _CELL_ITEM_NOSEP_RE.match(s)
    if match:
        return ("要求", match.group(1), match.group(2).strip(), "")
    match = _CELL_SUBITEM_RE.match(s)
    if match:
        return ("子条目", match.group(1), match.group(2).strip(), "")
    return ("说明", "", s, "")


def _technical_clause_payload(
    segment: Mapping[str, Any],
    cells: list[str],
) -> str:
    """返回技术参数列文本（字符串级回退用）。"""
    if (
        segment.get("tableId")
        and isinstance(segment.get("cells"), list)
        and len(cells) >= 3
    ):
        parameter_cell = _text(cells[2], 24000)
        if parameter_cell:
            return parameter_cell
        candidates = [
            _text(value, 24000)
            for value in cells[2:-2] or cells[2:]
            if _text(value, 24000)
        ]
        if candidates:
            return max(candidates, key=len)
    return _text(segment.get("text") or " | ".join(cells), 24000)


def _technical_parameter_cell(
    segment: Mapping[str, Any],
) -> dict[str, Any] | None:
    """返回技术参数单元格 dict（含 paragraphs），供段落级拆解使用。"""
    raw_cells = segment.get("cells")
    if not isinstance(raw_cells, list) or not raw_cells:
        return None
    # 与 _technical_clause_payload 一致的列定位：优先第 3 列（序号|名称|参数）。
    if len(raw_cells) >= 3:
        candidate = raw_cells[2]
        if isinstance(candidate, dict) and _text(candidate.get("text"), 24000):
            return candidate
    best: dict[str, Any] | None = None
    best_len = -1
    for cell in raw_cells:
        if not isinstance(cell, dict):
            continue
        length = len(_text(cell.get("text"), 24000))
        if length > best_len:
            best_len = length
            best = cell
    return best


def _technical_cell_paragraph_clauses(
    segment: Mapping[str, Any],
) -> list[str]:
    """按单元格段落逐条拆解（_CELL_*_RE 层级），段落数不足 3 时返回空回退字符串级。"""
    cell = _technical_parameter_cell(segment)
    if not cell:
        return []
    paragraphs = cell.get("paragraphs")
    if not isinstance(paragraphs, list) or len(paragraphs) < 3:
        return []
    numbered = 0
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        kind, _, _, _ = _classify_cell_paragraph(_text(paragraph.get("text"), 4000))
        if kind in {"要求", "子条目"}:
            numbered += 1
    if numbered < 3:
        return []

    clauses: list[str] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        ptext = _text(paragraph.get("text"), 4000)
        if not ptext:
            continue
        kind, _num, _title, tail = _classify_cell_paragraph(ptext)
        if kind == "大项":
            # 大项标题尾随的「投标人须提供…截图」属于实质要求，需保留。
            if tail and _is_requirement(tail):
                clauses.append(tail)
            continue
        if kind == "子项":
            continue
        if kind in {"要求", "子条目"}:
            clauses.append(ptext)
            continue
        if _is_requirement(ptext) or len(_compact(ptext)) >= 40:
            clauses.append(ptext)
    return clauses


def _numbered_clauses(clauses: list[str]) -> list[tuple[str, str]]:
    """把带编号的条款文本转成 [(编号, 正文)]，编号从原文前缀提取。"""
    result: list[tuple[str, str]] = []
    for clause in clauses:
        text = _text(clause, 6000)
        if not _compact(text):
            continue
        kind, num, _title, _tail = _classify_cell_paragraph(text)
        number = num if kind in {"大项", "子项", "要求", "子条目"} else ""
        result.append((number, text))
    return result


def _split_technical_clauses(
    segment: Mapping[str, Any],
    cells: list[str],
) -> list[tuple[str, str]]:
    """拆解技术参数单元格：优先段落级（_CELL_*_RE），回退字符串级（_split_requirement_text）。"""
    paragraph_clauses = _technical_cell_paragraph_clauses(segment)
    if paragraph_clauses:
        return _numbered_clauses(paragraph_clauses)

    payload = _technical_clause_payload(segment, cells)
    text_clauses = _split_requirement_text(payload, max_length=24000)
    return _numbered_clauses(text_clauses)


def _target_item_signal(
    target_key: str,
    text: str,
    segment: Mapping[str, Any],
    indexes: Mapping[str, Any],
    *,
    preserve_full_section: bool = False,
) -> bool:
    """Keep processors aligned with target-specific range semantics."""

    table_type = _segment_table_type(segment, indexes)
    if preserve_full_section:
        if table_type and not _table_type_hits(target_key, segment, indexes):
            return bool(
                target_key == "template"
                and segment.get("tableId")
                and "template" in _section_context_target_types(
                    _owner_section(_segment_id(segment), indexes),
                    indexes,
                )
            )
        return True
    if table_type == target_key:
        return True
    if target_key == "template" and segment.get("tableId"):
        owner = _owner_section(_segment_id(segment), indexes)
        if "template" in _section_context_target_types(owner, indexes):
            return True
    return bool(_strong_body_hits(target_key, _compact(text)))


def _quantity(text: str) -> tuple[str, str]:
    match = re.search(
        r"(?:数量|不少于|至少|采购数量|数\s*量)\s*[:：]?\s*([0-9]+(?:\.[0-9]+)?)\s*([^\s，,；;|]+)?",
        str(text or ""),
    )
    if not match:
        return "", ""
    return _text(match.group(1), 40), _text(match.group(2), 40)


def _parameter_text(segment: Mapping[str, Any], cells: list[str]) -> str:
    table_type = _text(segment.get("tableRequirementType"), 60)
    preferred = []
    for value in cells:
        if any(term in value for term in ("技术", "参数", "指标", "规格", "功能", "性能", "服务要求")):
            preferred.append(value)
    if preferred:
        return _text("；".join(preferred), 2400)
    if table_type == "technical" and len(cells) >= 3:
        return _text(cells[2], 2400)
    return _text(segment.get("text"), 2400)


def _product_fields(segment: Mapping[str, Any], cells: list[str]) -> dict[str, str]:
    product_name = _text(
        segment.get("productName")
        or segment.get("targetName")
        or (cells[1] if len(cells) > 1 else ""),
        240,
    )
    target_type = _text(segment.get("targetType"), 80)
    if not target_type:
        target_type = "服务" if any(term in product_name for term in ("服务", "实施", "运维", "咨询")) else "产品/标的"
    source_column = _text(segment.get("sourceColumn"), 100)
    return {
        "productName": product_name,
        "targetType": target_type,
        "sourceColumn": source_column,
    }


def _base_item(
    *,
    item_id: str,
    target_key: str,
    text: str,
    raw_text: str,
    segment: Mapping[str, Any],
    indexes: Mapping[str, Any],
    confidence: float,
) -> dict[str, Any]:
    anchor = _source_anchor(segment, indexes)
    return {
        "id": item_id,
        "targetModule": target_key,
        "title": _text(text.split("：", 1)[0] if "：" in text else text, 240),
        "text": _text(text, 6000),
        "rawText": _text(raw_text, 12000),
        "normalizedText": _text(text, 6000),
        "mandatory": True,
        "confidence": max(0.0, min(float(confidence), 1.0)),
        "handlingMode": TARGET_META[target_key]["processingMode"],
        "source": anchor,
        "sourceSegmentId": anchor["segmentId"],
        "sourceSegmentIds": anchor["segmentIds"],
        "sourceLocator": anchor["sourceLocator"],
        "sectionId": anchor["sectionId"],
        "sectionPath": anchor["sectionPath"],
        "tableIds": anchor["tableIds"],
    }


def _technical_items(
    segment_ids: list[str],
    indexes: Mapping[str, Any],
    preserve_segment_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for segment_id in segment_ids:
        segment = indexes["segmentMap"].get(segment_id)
        if not segment:
            continue
        source_text = _text(segment.get("text"), 12000)
        if (
            not source_text
            or _is_section_heading_segment(segment_id, indexes)
            or _is_heading_or_header(source_text)
            or _is_target_noise("technical", segment)
            or not _target_item_signal(
                "technical",
                source_text,
                segment,
                indexes,
                preserve_full_section=segment_id in (preserve_segment_ids or set()),
            )
        ):
            continue
        cells = _table_cells(segment)
        fields = _product_fields(segment, cells)
        quantity, unit = _quantity(source_text)
        if not quantity and len(cells) >= 4:
            quantity, unit = _text(cells[-2], 40), _text(cells[-1], 40)
        clauses = _split_technical_clauses(segment, cells)
        for clause_index, (requirement_number, clause) in enumerate(
            clauses,
            start=1,
        ):
            item = _base_item(
                item_id=f"technical-{len(items) + 1:04d}",
                target_key="technical",
                text=clause,
                raw_text=source_text,
                segment=segment,
                indexes=indexes,
                confidence=0.78 if segment.get("tableId") else 0.68,
            )
            item.update(
                {
                    **fields,
                    "quantity": quantity,
                    "unit": unit,
                    "parameters": _text(clause, 2400),
                    "requirementNumber": requirement_number,
                    "clauseIndex": clause_index,
                    "clauseCount": len(clauses),
                    "matchDomain": "technical",
                }
            )
            items.append(item)
    return items


def _qualification_items(
    segment_ids: list[str],
    indexes: Mapping[str, Any],
    preserve_segment_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for segment_id in segment_ids:
        segment = indexes["segmentMap"].get(segment_id)
        if not segment:
            continue
        source_text = _text(segment.get("text"), 12000)
        if (
            not source_text
            or _is_section_heading_segment(segment_id, indexes)
            or _is_heading_or_header(source_text)
            or _is_target_noise("qualification", segment)
            or not _target_item_signal(
                "qualification",
                source_text,
                segment,
                indexes,
                preserve_full_section=segment_id in (preserve_segment_ids or set()),
            )
        ):
            continue
        for clause in _split_clauses(source_text):
            if len(_compact(clause)) < 8:
                continue
            item = _base_item(
                item_id=f"qualification-{len(items) + 1:04d}",
                target_key="qualification",
                text=clause,
                raw_text=source_text,
                segment=segment,
                indexes=indexes,
                confidence=0.76 if segment.get("tableId") else 0.66,
            )
            item.update(
                {
                    "matchDomain": "qualification",
                    "evidenceNeed": "企业资格/证明材料",
                }
            )
            items.append(item)
    return items


def _display_items(
    target_key: str,
    segment_ids: list[str],
    indexes: Mapping[str, Any],
    preserve_segment_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for segment_id in segment_ids:
        segment = indexes["segmentMap"].get(segment_id)
        if not segment:
            continue
        raw_text = _text(segment.get("text"), 12000)
        if (
            not raw_text
            or _is_section_heading_segment(segment_id, indexes)
            or _is_heading_or_header(raw_text)
            or _is_target_noise(target_key, segment)
            or not _target_item_signal(
                target_key,
                raw_text,
                segment,
                indexes,
                preserve_full_section=segment_id in (preserve_segment_ids or set()),
            )
        ):
            continue
        clauses = _split_clauses(raw_text)
        for clause_index, clause in enumerate(clauses, start=1):
            item = _base_item(
                item_id=f"{target_key}-{len(items) + 1:04d}",
                target_key=target_key,
                text=clause,
                raw_text=raw_text,
                segment=segment,
                indexes=indexes,
                confidence=0.7 if segment.get("tableId") else 0.62,
            )
            item.update(
                {
                    "mandatory": False,
                    "handlingMode": "display",
                    "clauseIndex": clause_index,
                    "clauseCount": len(clauses),
                }
            )
            if target_key == "evaluation":
                score = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*分", clause)
                item.update(
                    {
                        "criterionName": _text(clause.split("：", 1)[0], 240),
                        "scoringRule": clause,
                        "maxScore": float(score.group(1)) if score else None,
                        "requiredEvidence": "；".join(
                            value
                            for value in ("证明材料", "响应文件", "业绩", "证书")
                            if value in clause
                        ),
                    }
                )
            items.append(item)
    return items


def _template_type(text: str) -> str:
    value = _compact(text)
    if "投标函" in value:
        return "bid_letter"
    if "承诺函" in value:
        return "commitment"
    if "授权委托" in value or "法定代表人" in value:
        return "authorization"
    if "报价表" in value or "分项报价" in value:
        return "price_schedule"
    if "技术偏离" in value:
        return "technical_deviation"
    if "商务偏离" in value:
        return "commercial_deviation"
    if "资格" in value or "资质" in value:
        return "qualification_table"
    if "人员" in value:
        return "personnel_table"
    return "other"


def _template_items(
    segment_ids: list[str],
    indexes: Mapping[str, Any],
    preserve_segment_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for segment_id in segment_ids:
        segment = indexes["segmentMap"].get(segment_id)
        if not segment:
            continue
        raw_text = _text(segment.get("text"), 16000)
        key = _compact(raw_text)
        if (
            not raw_text
            or key in seen
            or _is_section_heading_segment(segment_id, indexes)
            or _is_heading_or_header(raw_text)
            or _is_target_noise("template", segment)
            or not _target_item_signal(
                "template",
                raw_text,
                segment,
                indexes,
                preserve_full_section=segment_id in (preserve_segment_ids or set()),
            )
        ):
            continue
        seen.add(key)
        item = _base_item(
            item_id=f"template-{len(items) + 1:04d}",
            target_key="template",
            text=raw_text,
            raw_text=raw_text,
            segment=segment,
            indexes=indexes,
            confidence=0.75 if segment.get("tableId") else 0.65,
        )
        template_id = f"TPL-{len(items) + 1:04d}"
        item.update(
            {
                "templateId": template_id,
                "templateType": _template_type(raw_text),
                "extractMode": "source_range",
                "previewMode": "原文预览",
                "downloadMode": "source_document_range",
                "downloadText": raw_text,
                "writingReady": True,
                "mandatory": False,
                "handlingMode": "writing_task",
            }
        )
        items.append(item)
    return items


def _template_range_items(
    target: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Project template source ranges into the public low-cardinality items."""

    positions = indexes.get("positions", {})
    segment_map = indexes.get("segmentMap", {})
    ranges = [
        item
        for item in (target.get("ranges") or [])
        if isinstance(item, Mapping)
    ]
    if not ranges:
        selected_ids = [
            _id(value)
            for value in target.get("segmentIds") or []
            if _id(value) in segment_map
        ]
        if selected_ids:
            ranges = [
                {
                    "segmentIds": sorted(
                        set(selected_ids),
                        key=positions.get,
                    ),
                    "sectionId": _section_id(
                        _owner_section(selected_ids[0], indexes) or {}
                    ),
                }
            ]

    expanded_section_ids = [
        _id(value)
        for value in target.get("expandedSectionIds") or []
        if _id(value) in indexes.get("sectionMap", {})
    ]
    items: list[dict[str, Any]] = []
    for index, source_range in enumerate(ranges, start=1):
        segment_ids = sorted(
            {
                _id(value)
                for value in source_range.get("segmentIds") or []
                if _id(value) in segment_map
            },
            key=positions.get,
        )
        if not segment_ids:
            continue
        segment = segment_map[segment_ids[0]]
        range_set = set(segment_ids)
        section_id = ""
        for candidate_id in expanded_section_ids:
            candidate = indexes["sectionMap"].get(candidate_id) or {}
            candidate_segments = {
                _id(value)
                for value in candidate.get("segmentIds") or []
            }
            if range_set.intersection(candidate_segments):
                section_id = candidate_id
                break
        section_id = (
            section_id
            or _id(source_range.get("sectionId"))
            or _section_id(_owner_section(segment_ids[0], indexes) or {})
        )
        section = indexes.get("sectionMap", {}).get(section_id) or {}
        title = _text(
            section.get("heading")
            or section.get("title")
            or segment.get("text")
            or "响应模板",
            240,
        )
        section_path = [
            _text(value, 200)
            for value in (section.get("sectionPath") or [])
            if _text(value, 200)
        ]
        table_ids = list(
            dict.fromkeys(
                _id(segment_map[segment_id].get("tableId"))
                for segment_id in segment_ids
                if _id(segment_map[segment_id].get("tableId"))
            )
        )
        last_segment = segment_map[segment_ids[-1]]
        anchor = _source_anchor(
            segment,
            indexes,
            section_id=section_id,
        )
        anchor.update(
            {
                "segmentIds": segment_ids,
                "tableIds": table_ids,
                "sectionId": section_id,
                "sectionPath": section_path,
                "startLocator": _text(
                    segment.get("sourceLocator"),
                    200,
                ),
                "endLocator": _text(
                    last_segment.get("sourceLocator"),
                    200,
                ),
            }
        )
        item = _base_item(
            item_id=f"template-{index:04d}",
            target_key="template",
            text=title,
            raw_text=title,
            segment=segment,
            indexes=indexes,
            confidence=0.86,
        )
        item.update(
            {
                "title": title,
                "text": title,
                "rawText": title,
                "normalizedText": title,
                "templateId": f"TPL-{index:04d}",
                "templateType": (
                    "document_format"
                    if any(
                        marker in _compact(title)
                        for marker in (
                            "电子投标文件格式",
                            "投标文件格式",
                            "响应文件格式",
                            "投标文件组成",
                        )
                    )
                    else _template_type(" / ".join([*section_path, title]))
                ),
                "extractMode": "source_range",
                "previewMode": "structured",
                "downloadMode": "source_document_range",
                "writingReady": True,
                "mandatory": False,
                "handlingMode": "writing_task",
                "granularity": "chapter",
                "sourceSectionId": section_id,
                "sourceSectionIds": [section_id] if section_id else [],
                "sourceSegmentId": segment_ids[0],
                "sourceSegmentIds": segment_ids,
                "sectionId": section_id,
                "sectionPath": section_path,
                "tableIds": table_ids,
                "sourceLocator": _text(segment.get("sourceLocator"), 200),
                "startLocator": _text(segment.get("sourceLocator"), 200),
                "endLocator": _text(last_segment.get("sourceLocator"), 200),
                "source": anchor,
            }
        )
        items.append(item)
    return items


def _section_sort_key(
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> tuple[int, int, str]:
    segment_positions = [
        indexes.get("positions", {}).get(_id(segment_id), 10**9)
        for segment_id in section.get("segmentIds") or []
        if _id(segment_id) in indexes.get("positions", {})
    ]
    return (
        min(segment_positions, default=10**9),
        int(section.get("level") or 0),
        _section_id(section),
    )


def _source_section_locator(
    section: Mapping[str, Any],
    indexes: Mapping[str, Any],
    segment_ids: list[str],
) -> dict[str, Any]:
    ordered_segments = [
        indexes.get("segmentMap", {}).get(segment_id)
        for segment_id in segment_ids
    ]
    ordered_segments = [
        segment for segment in ordered_segments if isinstance(segment, Mapping)
    ]
    first = ordered_segments[0] if ordered_segments else {}
    last = ordered_segments[-1] if ordered_segments else {}
    return {
        "sourceLocator": _text(
            section.get("startLocator")
            or first.get("sourceLocator")
            or section.get("endLocator"),
            200,
        ),
        "startLocator": _text(
            section.get("startLocator") or first.get("sourceLocator"),
            200,
        ),
        "endLocator": _text(
            section.get("endLocator") or last.get("sourceLocator"),
            200,
        ),
    }


def _source_table_row(
    table: Mapping[str, Any],
    row: Mapping[str, Any],
    row_segment_ids: list[str],
) -> dict[str, Any]:
    cells: list[dict[str, Any]] = []
    for cell in row.get("cells") or []:
        if isinstance(cell, Mapping):
            cells.append(
                {
                    "cellId": _id(cell.get("id")),
                    "text": _text(cell.get("text"), 24000),
                    "cellIndex": int(cell.get("cellIndex") or 0),
                    "gridSpan": _text(cell.get("gridSpan"), 20),
                    "vMerge": _text(cell.get("vMerge"), 20),
                    "sourceLocator": _text(cell.get("sourceLocator"), 200),
                }
            )
        else:
            cells.append(
                {
                    "cellId": "",
                    "text": _text(cell, 24000),
                    "cellIndex": len(cells) + 1,
                    "gridSpan": "",
                    "vMerge": "",
                    "sourceLocator": "",
                }
            )
    return {
        "rowIndex": int(row.get("rowIndex") or 0),
        "sourceLocator": _text(row.get("sourceLocator"), 200),
        "sourceSegmentIds": list(dict.fromkeys(row_segment_ids)),
        "cells": cells,
    }


def _source_view_blocks(
    segment_ids: list[str],
    indexes: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Build ordered paragraphs and whole table rows from parser segments."""

    blocks: list[dict[str, Any]] = []
    table_ids: list[str] = []
    seen_table_rows: set[tuple[str, int]] = set()
    table_blocks: dict[str, dict[str, Any]] = {}
    ordered_ids = sorted(
        set(segment_ids),
        key=indexes.get("positions", {}).get,
    )
    for segment_id in ordered_ids:
        segment = indexes.get("segmentMap", {}).get(segment_id)
        if not isinstance(segment, Mapping):
            continue
        table_id = _id(segment.get("tableId"))
        if table_id:
            try:
                row_index = int(segment.get("rowIndex") or 0)
            except (TypeError, ValueError):
                row_index = 0
            row_key = (table_id, row_index)
            if row_key in seen_table_rows:
                continue
            table = indexes.get("tableMap", {}).get(table_id)
            row = next(
                (
                    candidate
                    for candidate in (table.get("rows") or [])
                    if isinstance(candidate, Mapping)
                    and int(candidate.get("rowIndex") or 0) == row_index
                ),
                None,
            ) if isinstance(table, Mapping) else None
            if not isinstance(row, Mapping):
                continue
            row_segment_ids = [
                candidate_id
                for candidate_id in ordered_ids
                if (
                    _id(
                        indexes.get("segmentMap", {})
                        .get(candidate_id, {})
                        .get("tableId")
                    )
                    == table_id
                    and int(
                        indexes.get("segmentMap", {})
                        .get(candidate_id, {})
                        .get("rowIndex")
                        or 0
                    )
                    == row_index
                )
            ]
            table_row = _source_table_row(table or {}, row, row_segment_ids)
            table_block = table_blocks.get(table_id)
            if table_block is None:
                table_block = {
                    "type": "table",
                    "tableId": table_id,
                    "sourceSegmentIds": [],
                    "sourceLocator": _text(
                        row.get("sourceLocator") or segment.get("sourceLocator"),
                        200,
                    ),
                    "columnCount": int(
                        table.get("columnCount") or len(row.get("cells") or [])
                    ) if isinstance(table, Mapping) else len(row.get("cells") or []),
                    "gridWidths": list(table.get("gridWidths") or [])
                    if isinstance(table, Mapping)
                    else [],
                    "rows": [],
                }
                table_blocks[table_id] = table_block
                blocks.append(table_block)
            table_block["sourceSegmentIds"] = list(
                dict.fromkeys(
                    [
                        *(table_block.get("sourceSegmentIds") or []),
                        *row_segment_ids,
                    ]
                )
            )
            table_block["rows"].append(table_row)
            table_ids.append(table_id)
            seen_table_rows.add(row_key)
            continue
        text = _text(segment.get("text"), 24000)
        if not text:
            continue
        blocks.append(
            {
                "type": "paragraph",
                "text": text,
                "sourceSegmentIds": [segment_id],
                "sourceLocator": _text(segment.get("sourceLocator"), 200),
                "contentType": _text(segment.get("contentType"), 60) or "paragraph",
            }
        )
    return blocks, list(dict.fromkeys(table_ids))


def _source_view_section_ids(
    target: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> list[str]:
    section_map = indexes.get("sectionMap", {})
    candidate_ids = [
        _id(value)
        for value in (
            target.get("sectionIds")
            or target.get("expandedSectionIds")
            or []
        )
        if _id(value) in section_map
    ]
    return list(
        dict.fromkeys(
            sorted(
                candidate_ids,
                key=lambda section_id: _section_sort_key(
                    section_map[section_id],
                    indexes,
                ),
            )
        )
    )


def _template_display_segment_ids(
    target: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> list[str]:
    """Return the complete selected template source range.

    A template chapter is a reusable source artifact, not a list of
    requirements.  Paragraphs that look like an invitation, a qualification
    clause, a technical response table, or a commercial response table are
    still part of the template when they occur inside the selected template
    chapter.  Filtering them here produces a damaged download and makes
    otherwise identical forms appear to have different boundaries.
    """

    selected_ids = {
        _id(value)
        for value in target.get("segmentIds") or []
        if _id(value) in indexes.get("segmentMap", {})
    }
    return sorted(
        selected_ids,
        key=indexes.get("positions", {}).get,
    )


def _source_view_node(
    section: Mapping[str, Any],
    *,
    selected_segment_ids: set[str],
    selected_section_ids: set[str],
    children_by_parent: Mapping[str, list[str]],
    indexes: Mapping[str, Any],
    target_key: str,
) -> dict[str, Any]:
    section_id = _section_id(section)
    section_segment_ids = [
        _id(value)
        for value in section.get("segmentIds") or []
        if _id(value) in selected_segment_ids
    ]
    descendant_ids: set[str] = set()
    pending = list(children_by_parent.get(section_id) or [])
    while pending:
        child_id = pending.pop()
        descendant = indexes.get("sectionMap", {}).get(child_id)
        if not isinstance(descendant, Mapping):
            continue
        descendant_ids.update(
            _id(value)
            for value in descendant.get("segmentIds") or []
            if _id(value) in selected_segment_ids
        )
        pending.extend(children_by_parent.get(child_id) or [])
    own_segment_ids = [
        segment_id
        for segment_id in section_segment_ids
        if segment_id not in descendant_ids
        and segment_id != _id(section.get("headingSegmentId"))
    ]
    blocks, own_table_ids = _source_view_blocks(own_segment_ids, indexes)
    child_nodes = [
        _source_view_node(
            indexes["sectionMap"][child_id],
            selected_segment_ids=selected_segment_ids,
            selected_section_ids=selected_section_ids,
            children_by_parent=children_by_parent,
            indexes=indexes,
            target_key=target_key,
        )
        for child_id in children_by_parent.get(section_id) or []
        if child_id in selected_section_ids
    ]
    child_nodes.sort(
        key=lambda node: (
            int(node.get("position") or 10**9),
            int(node.get("level") or 0),
            str(node.get("sectionId") or ""),
        )
    )
    all_segment_ids = list(
        dict.fromkeys(
            [
                *section_segment_ids,
                *[
                    segment_id
                    for child in child_nodes
                    for segment_id in child.get("sourceSegmentIds") or []
                ],
            ]
        )
    )
    all_table_ids = list(
        dict.fromkeys(
            [
                *own_table_ids,
                *[
                    table_id
                    for child in child_nodes
                    for table_id in child.get("tableIds") or []
                ],
            ]
        )
    )
    locator = _source_section_locator(section, indexes, all_segment_ids)
    source_path = [
        _text(value, 200)
        for value in (section.get("sectionPath") or [])
        if _text(value, 200)
    ]
    return {
        "sectionId": section_id,
        "title": _text(section.get("heading") or section.get("title"), 240),
        "level": int(section.get("level") or 0),
        "parentId": _id(section.get("parentId")),
        "sectionPath": source_path,
        "position": min(
            (
                indexes.get("positions", {}).get(segment_id, 10**9)
                for segment_id in all_segment_ids
            ),
            default=10**9,
        ),
        "sourceSegmentIds": all_segment_ids,
        "contentSegmentIds": own_segment_ids,
        "tableIds": all_table_ids,
        "sourceLocator": locator["sourceLocator"],
        "startLocator": locator["startLocator"],
        "endLocator": locator["endLocator"],
        "blocks": blocks,
        "children": child_nodes,
        "displayTarget": target_key,
        "templateId": "",
    }


def _build_source_view(
    target_key: str,
    target: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> dict[str, Any]:
    """Project selected parser ranges into a display-safe section tree."""

    section_ids = _source_view_section_ids(target, indexes)
    selected_segment_ids = (
        set(_template_display_segment_ids(target, indexes))
        if target_key == "template"
        else {
            _id(value)
            for value in target.get("segmentIds") or []
            if _id(value) in indexes.get("segmentMap", {})
        }
    )
    selected_section_ids = set(section_ids)
    children_by_parent: dict[str, list[str]] = defaultdict(list)
    roots: list[str] = []
    for section_id in section_ids:
        section = indexes["sectionMap"][section_id]
        parent_id = _id(section.get("parentId"))
        nearest_parent = parent_id
        visited: set[str] = set()
        while nearest_parent and nearest_parent not in selected_section_ids:
            if nearest_parent in visited:
                nearest_parent = ""
                break
            visited.add(nearest_parent)
            nearest_parent = _id(
                indexes.get("sectionMap", {})
                .get(nearest_parent, {})
                .get("parentId")
            )
        if nearest_parent:
            children_by_parent[nearest_parent].append(section_id)
        else:
            roots.append(section_id)
    for parent_id in children_by_parent:
        children_by_parent[parent_id].sort(
            key=lambda value: _section_sort_key(
                indexes["sectionMap"][value],
                indexes,
            )
        )
    roots.sort(
        key=lambda value: _section_sort_key(indexes["sectionMap"][value], indexes)
    )
    nodes = [
        _source_view_node(
            indexes["sectionMap"][section_id],
            selected_segment_ids=selected_segment_ids,
            selected_section_ids=selected_section_ids,
            children_by_parent=children_by_parent,
            indexes=indexes,
            target_key=target_key,
        )
        for section_id in roots
    ]
    if not nodes and selected_segment_ids:
        blocks, table_ids = _source_view_blocks(
            sorted(
                selected_segment_ids,
                key=indexes.get("positions", {}).get,
            ),
            indexes,
        )
        nodes = [
            {
                "sectionId": f"SRC-{target_key.upper()}-ROOT",
                "title": TARGET_META[target_key]["label"],
                "level": 0,
                "parentId": "",
                "sectionPath": [TARGET_META[target_key]["label"]],
                "position": min(
                    (
                        indexes.get("positions", {}).get(segment_id, 10**9)
                        for segment_id in selected_segment_ids
                    ),
                    default=10**9,
                ),
                "sourceSegmentIds": sorted(
                    selected_segment_ids,
                    key=indexes.get("positions", {}).get,
                ),
                "contentSegmentIds": sorted(
                    selected_segment_ids,
                    key=indexes.get("positions", {}).get,
                ),
                "tableIds": table_ids,
                "sourceLocator": _text(
                    (indexes["segmentMap"][next(iter(selected_segment_ids))]).get(
                        "sourceLocator"
                    ),
                    200,
                ) if selected_segment_ids else "",
                "startLocator": "",
                "endLocator": "",
                "blocks": blocks,
                "children": [],
                "displayTarget": target_key,
                "templateId": "",
            }
        ]
    view_nodes = _flatten_source_view_nodes(nodes)
    segment_count = len(
        {
            str(segment_id)
            for node in view_nodes
            for segment_id in node.get("sourceSegmentIds") or []
            if str(segment_id)
        }
    )
    table_ids = list(
        dict.fromkeys(
            table_id
            for node in view_nodes
            for table_id in node.get("tableIds") or []
        )
    )
    block_count = sum(
        len(node.get("blocks") or [])
        for node in view_nodes
    )
    return {
        "schemaVersion": SOURCE_VIEW_SCHEMA_VERSION,
        "mode": "section_tree",
        "displayMode": "source_sections",
        "sectionCount": len(view_nodes),
        "rootSectionCount": len(nodes),
        "segmentCount": segment_count,
        "tableCount": len(table_ids),
        "blockCount": block_count,
        "tableIds": table_ids,
        "sections": nodes,
    }


def _flatten_source_view_nodes(
    nodes: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        result.append(node)
        result.extend(_flatten_source_view_nodes(node.get("children")))
    return result


def _template_index_from_source_view(
    source_view: Mapping[str, Any],
    source_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create low-cardinality writing/download units from source roots.

    The source tree already contains the meaningful template structure.  Its
    child headings are useful for navigation, but they are not independent
    files: a single tender template chapter can contain an index, repeated
    form names, typed technical/commercial response tables, and explanatory
    paragraphs.  Treating every child as a download unit cuts one source
    artifact into misleading fragments.

    A root template chapter therefore becomes one main unit.  If a parser
    returns several independent template roots, each root remains a unit so
    genuinely separate source ranges are not merged.
    """

    roots = [
        node
        for node in (source_view.get("sections") or [])
        if isinstance(node, dict)
    ]
    grouped: list[dict[str, Any]] = []
    for index, node in enumerate(roots, start=1):
        segment_ids = list(
            dict.fromkeys(
                str(value)
                for value in (node.get("sourceSegmentIds") or [])
                if str(value)
            )
        )
        if not segment_ids:
            continue
        section_id = _text(node.get("sectionId"), 100)
        section_path = [
            _text(value, 200)
            for value in (node.get("sectionPath") or [])
            if _text(value, 200)
        ]
        child_nodes = [
            child
            for child in _flatten_source_view_nodes(node.get("children"))
            if isinstance(child, Mapping)
        ]
        source_section_ids = list(
            dict.fromkeys(
                [
                    section_id
                    for section_id in (
                        [_text(node.get("sectionId"), 100)]
                        + [
                            _text(child.get("sectionId"), 100)
                            for child in child_nodes
                        ]
                    )
                    if section_id
                ]
            )
        )
        source_item_ids = [
            str(item.get("id"))
            for item in source_items
            if isinstance(item, Mapping)
            and set(segment_ids).intersection(
                str(value)
                for value in (
                    item.get("sourceSegmentIds")
                    or item.get("segmentIds")
                    or []
                )
                if str(value)
            )
        ]
        title = _text(node.get("title") or "响应模板", 240)
        root_title = _compact(title)
        template_type = (
            "document_format"
            if any(
                marker in root_title
                for marker in (
                    "电子投标文件格式",
                    "投标文件格式",
                    "响应文件格式",
                    "投标文件组成",
                )
            )
            else _template_type(" / ".join([*section_path, title]))
        )
        grouped.append(
            {
                "id": f"template-{index:04d}",
                "templateId": f"TPL-{index:04d}",
                "targetModule": "template",
                "title": title,
                "text": title,
                "rawText": title,
                "normalizedText": title,
                "templateType": template_type,
                "extractMode": "source_range",
                "previewMode": "structured",
                "downloadMode": "source_document_range",
                "writingReady": True,
                "mandatory": False,
                "handlingMode": "writing_task",
                "granularity": "chapter",
                "sourceSectionId": section_id,
                "sourceSectionIds": source_section_ids,
                "childSectionCount": len(child_nodes),
                "sourceSegmentId": segment_ids[0],
                "sourceSegmentIds": segment_ids,
                "sectionId": section_id,
                "sectionPath": section_path,
                "sourceLocator": _text(node.get("sourceLocator"), 200),
                "startLocator": _text(node.get("startLocator"), 200),
                "endLocator": _text(node.get("endLocator"), 200),
                "tableIds": list(node.get("tableIds") or []),
                "sourceItemIds": list(dict.fromkeys(source_item_ids)),
                "source": {
                    "sectionId": section_id,
                    "sectionPath": section_path,
                    "segmentId": segment_ids[0],
                    "segmentIds": segment_ids,
                    "sourceLocator": _text(node.get("sourceLocator"), 200),
                    "startLocator": _text(node.get("startLocator"), 200),
                    "endLocator": _text(node.get("endLocator"), 200),
                    "tableIds": list(node.get("tableIds") or []),
                },
            }
        )
    return grouped


def _template_sections_from_source_view(
    source_view: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Keep child headings as internal navigation metadata.

    ``templateIndex`` is deliberately chapter-level.  This projection keeps
    the parser's child-section boundaries available to the writing workflow
    without turning them into separate download cards.
    """

    sections: list[dict[str, Any]] = []
    for node in _flatten_source_view_nodes(source_view.get("sections")):
        if not isinstance(node, Mapping):
            continue
        sections.append(
            {
                "sectionId": _text(node.get("sectionId"), 100),
                "title": _text(node.get("title"), 240),
                "level": int(node.get("level") or 0),
                "parentId": _text(node.get("parentId"), 100),
                "sectionPath": [
                    _text(value, 200)
                    for value in (node.get("sectionPath") or [])
                    if _text(value, 200)
                ],
                "sourceSegmentIds": list(
                    dict.fromkeys(
                        str(value)
                        for value in (node.get("sourceSegmentIds") or [])
                        if str(value)
                    )
                ),
                "tableIds": list(
                    dict.fromkeys(
                        str(value)
                        for value in (node.get("tableIds") or [])
                        if str(value)
                    )
                ),
                "sourceLocator": _text(node.get("sourceLocator"), 200),
                "startLocator": _text(node.get("startLocator"), 200),
                "endLocator": _text(node.get("endLocator"), 200),
            }
        )
    return sections


_FIELD_PREFIX_RE = (
    r"(?:\d+(?:\.\d+)*[、.．)]?|[一二三四五六七八九十百]+[、.．]|"
    r"[（(][一二三四五六七八九十百\d]+[)）])?\s*"
)


def _clean_field_value(value: Any) -> str:
    clean = _text(value, 1000)
    clean = re.sub(r"^[|｜:：\s]+", "", clean)
    clean = re.sub(r"[；;。|｜]+$", "", clean).strip()
    if not clean:
        return ""
    if re.fullmatch(r"[_＿.\-—…\s]+", clean):
        return ""
    if any(marker in clean for marker in ("待填写", "请填写", "自行填写")) and len(
        _compact(clean)
    ) < 20:
        return ""
    return clean


def _field_values_from_segment(
    raw_text: str,
    cells: list[str],
    aliases: tuple[str, ...],
) -> list[str]:
    values: list[str] = []
    for alias in aliases:
        escaped = re.escape(alias)
        match = re.search(
            rf"(?:^|[\s|｜\n；;]){_FIELD_PREFIX_RE}{escaped}"
            rf"\s*[:：]\s*([^|｜\n；;]+)",
            raw_text,
            flags=re.I,
        )
        if match:
            value = _clean_field_value(match.group(1))
            if value:
                values.append(value)

        for index, cell in enumerate(cells):
            clean_cell = _text(cell, 1200)
            if not clean_cell:
                continue
            inline = re.match(
                rf"^{_FIELD_PREFIX_RE}{escaped}\s*[:：]\s*(.+)$",
                clean_cell,
                flags=re.I,
            )
            if inline:
                value = _clean_field_value(inline.group(1))
                if value:
                    values.append(value)
                continue

            label_cell = re.sub(
                r"^[\s\d.、．)）(（]+|[★☆※*]+$",
                "",
                clean_cell,
            ).strip()
            if _compact(label_cell) != _compact(alias):
                continue
            for next_cell in cells[index + 1 :]:
                value = _clean_field_value(next_cell)
                if value and _compact(value) != _compact(alias):
                    values.append(value)
                    break
    return list(dict.fromkeys(values))


def _project_info_items(
    segment_ids: list[str],
    indexes: Mapping[str, Any],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    rules = project_field_rules()
    for segment_id in segment_ids:
        segment = indexes["segmentMap"].get(segment_id)
        if not segment:
            continue
        raw_text = _text(segment.get("text"), 12000)
        if not raw_text:
            continue
        cells = _table_cells(segment)
        for field, label, aliases in rules:
            values = _field_values_from_segment(raw_text, cells, aliases)
            for value in values:
                if len(_compact(value)) < 2:
                    continue
                identity = (field, _compact(value))
                if identity in seen:
                    continue
                seen.add(identity)
                item = _base_item(
                    item_id=f"project-info-{len(items) + 1:04d}",
                    target_key="project_info",
                    text=value,
                    raw_text=raw_text,
                    segment=segment,
                    indexes=indexes,
                    confidence=0.84,
                )
                item.update(
                    {
                        "field": field,
                        "label": label,
                        "value": value,
                        "mandatory": False,
                        "handlingMode": "display",
                    }
                )
                items.append(item)
    return items


_REJECTION_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。；;！？\n])")


def _rejection_candidate_texts(segment: Mapping[str, Any]) -> list[str]:
    """表格行拆到单元格，避免整行被单个词带成一条废标。"""
    cells = segment.get("cells") or segment.get("tableCells") or []
    texts = [
        str(cell.get("text") or "")
        for cell in cells
        if str(cell.get("text") or "")
    ]
    if texts and len(cells) > 1:
        return texts
    return [str(segment.get("text") or "")]


def _rejection_focus(text: str, hits: list[str]) -> str:
    """只保留含触发词的句子，去掉同一行/单元格里的无关条款。"""
    parts = _REJECTION_SENTENCE_SPLIT_RE.split(text)
    focused = [
        part
        for part in parts
        if part.strip() and any(_compact(term) in _compact(part) for term in hits)
    ]
    joined = "".join(focused).strip()
    return joined or text


def _rejection_dedup_key(text: str, compact: str) -> str:
    """语义去重：不接受联合体的多种表述折叠为一条。"""
    if "联合体" in text and any(
        term in text for term in ("不接受", "不予接受", "不允许")
    ):
        return "不接受联合体"
    return compact


def _rejection_items(
    segments: list[dict[str, Any]],
    indexes: Mapping[str, Any],
    selected_segment_ids: list[str] | None = None,
    reviewed_by_llm: bool = False,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    terms = rejection_terms()
    selected_ids = (
        {str(value) for value in selected_segment_ids if str(value)}
        if selected_segment_ids is not None
        else None
    )
    seen: set[str] = set()
    for segment in segments:
        segment_id = _segment_id(segment)
        if not segment_id:
            continue
        if selected_ids is not None and segment_id not in selected_ids:
            continue
        for cand_text in _rejection_candidate_texts(segment):
            raw_text = _text(cand_text, 12000)
            compact = _compact(raw_text)
            hits = [term for term in terms if _compact(term) in compact]
            if not raw_text:
                continue
            if not hits and not reviewed_by_llm:
                continue
            if not hits:
                hits = ["LLM复核"]
            # 聚焦到含触发词的句子，去掉同单元格里的无关条款。
            if hits:
                raw_text = _rejection_focus(raw_text, hits)
                compact = _compact(raw_text)
            # 流程性说明过滤：只命中弱词（不接受/不予受理/不得参加）且句子涉及
            # 澄清/质疑/投诉/异议/询问时，是流程说明而非废标条件。
            if hits and all(term in _WEAK_REJECTION_TERMS for term in hits):
                if any(term in raw_text for term in _REJECTION_PROCEDURE_TERMS):
                    continue
            key = _rejection_dedup_key(raw_text, compact)
            if key in seen:
                continue
            seen.add(key)
            item = _base_item(
                item_id=f"rejection-{len(items) + 1:04d}",
                target_key="rejection",
                text=raw_text,
                raw_text=raw_text,
                segment=segment,
                indexes=indexes,
                confidence=0.94,
            )
            item.update(
                {
                    "triggerTerms": hits,
                    "riskType": "废标/无效/否决风险",
                    "mandatory": True,
                    "handlingMode": "risk_check",
                    "isRejection": True,
                }
            )
            items.append(item)
    return items


def _module_items(
    target_key: str,
    target: Mapping[str, Any],
    indexes: Mapping[str, Any],
) -> list[dict[str, Any]]:
    ids = [str(value) for value in target.get("segmentIds") or [] if str(value)]
    expanded_ids = {
        str(value)
        for value in target.get("expandedSegmentIds") or []
        if str(value)
    }
    if target_key == "technical":
        return _technical_items(ids, indexes, expanded_ids)
    if target_key == "qualification":
        return _qualification_items(ids, indexes, expanded_ids)
    if target_key in {"commercial", "evaluation"}:
        return _display_items(target_key, ids, indexes, expanded_ids)
    if target_key == "template":
        return _template_range_items(target, indexes)
    if target_key == "project_info":
        return _project_info_items(ids, indexes)
    return []


def _compact_range(target: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "targetModule": target.get("targetModule"),
        "label": target.get("label"),
        "description": target.get("description"),
        "processingMode": target.get("processingMode"),
        "selectionSource": target.get("selectionSource") or "rule",
        "confidence": target.get("confidence", 0),
        "sectionIds": list(target.get("sectionIds") or []),
        "segmentIds": list(target.get("segmentIds") or []),
        "tableIds": list(target.get("tableIds") or []),
        "expandedSectionIds": list(target.get("expandedSectionIds") or []),
        "expandedSegmentIds": list(target.get("expandedSegmentIds") or []),
        "reviewSectionIds": list(target.get("reviewSectionIds") or []),
        "reviewSegmentIds": list(target.get("reviewSegmentIds") or []),
        "ranges": list(target.get("ranges") or []),
        "selectedSegmentCount": int(target.get("selectedSegmentCount") or 0),
        "selectedSectionCount": int(target.get("selectedSectionCount") or 0),
        "selectedTableCount": int(target.get("selectedTableCount") or 0),
        "reviewedByLlm": bool(target.get("reviewedByLlm")),
        "llmExcludedSegmentIds": list(
            target.get("llmExcludedSegmentIds") or []
        ),
        "reason": _text(target.get("reason"), 300),
    }


def build_target_analysis_pack(
    *,
    target_map: Mapping[str, Any],
    indexes: Mapping[str, Any],
    source_file_id: str = "",
    source_file_hash: str = "",
    document_meta: Mapping[str, Any] | None = None,
    stats: Mapping[str, Any] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    modules: list[dict[str, Any]] = []
    template_index: list[dict[str, Any]] = []
    for target in target_map.get("targets") or []:
        key = str(target.get("targetModule") or "")
        if key not in TARGET_META:
            continue
        items = target.get("items") if isinstance(target.get("items"), list) else []
        module = {
            **{name: value for name, value in TARGET_META[key].items()},
            **_compact_range(target),
            "items": [item for item in items if isinstance(item, dict)],
            "ignored": key == "contract",
        }
        if key in SOURCE_VIEW_TARGETS:
            source_view = _build_source_view(key, target, indexes)
            module["displayMode"] = "source_sections"
            module["sourceView"] = source_view
            if key == "template":
                source_items = _template_items(
                    [
                        _id(value)
                        for value in target.get("segmentIds") or []
                        if _id(value) in indexes.get("segmentMap", {})
                    ],
                    indexes,
                    {
                        _id(value)
                        for value in target.get("expandedSegmentIds") or []
                        if _id(value) in indexes.get("segmentMap", {})
                    },
                )
                template_index = _template_index_from_source_view(
                    source_view,
                    source_items,
                )
                module["templateCount"] = len(template_index)
                module["templateSections"] = _template_sections_from_source_view(
                    source_view,
                )
                # Keep fine-grained paragraph/table traces for audit, while
                # exposing only chapter-level units as the public module
                # items. The UI and writing workflow use sourceView and
                # templateIndex; no paragraph is presented as a standalone
                # template.
                module["sourceItems"] = source_items
                module["sourceItemCount"] = len(source_items)
                template_ids_by_section = {
                    str(item.get("sourceSectionId")): str(item.get("templateId"))
                    for item in template_index
                    if item.get("sourceSectionId") and item.get("templateId")
                }
                for node in _flatten_source_view_nodes(
                    source_view.get("sections")
                ):
                    section_id = str(node.get("sectionId") or "")
                    if section_id in template_ids_by_section:
                        node["templateId"] = template_ids_by_section[section_id]
                module["items"] = template_index
            else:
                module["sourceItemCount"] = len(module["items"])
        else:
            module["displayMode"] = (
                "ignored"
                if key == "contract"
                else (
                    "match_items"
                    if key in MATCH_STAGE_TARGETS
                    else "risk_items"
                )
            )
        if key == "contract":
            module["items"] = []
            module["ignoredRanges"] = list(target.get("ranges") or [])
        modules.append(module)

    by_key = {str(item.get("targetModule")): item for item in modules}
    project_items = list(by_key.get("project_info", {}).get("items") or [])
    templates = list(template_index)
    match_stage_requirements = [
        item
        for key in MATCH_STAGE_TARGETS
        for item in (by_key.get(key, {}).get("items") or [])
        if isinstance(item, dict)
    ]
    enterprise_match_requirements = [
        item
        for key in ENTERPRISE_MATCH_TARGETS
        for item in (by_key.get(key, {}).get("items") or [])
        if isinstance(item, dict)
    ]
    risk_check_requirements = [
        item
        for key in RISK_CHECK_TARGETS
        for item in (by_key.get(key, {}).get("items") or [])
        if isinstance(item, dict)
    ]
    safe_document = {
        key: value
        for key, value in (document_meta or {}).items()
        if key not in {"parsed", "rawText", "text", "segments", "sections", "tables"}
    }
    module_counts = {
        key: len(by_key.get(key, {}).get("items") or [])
        for key in TARGET_META
    }
    module_display_counts = {
        key: int(
            by_key.get(key, {}).get("templateCount")
            or by_key.get(key, {}).get("sourceView", {}).get("sectionCount")
            or module_counts.get(key)
            or 0
        )
        for key in TARGET_META
    }
    module_counts["contract"] = int(
        by_key.get("contract", {}).get("selectedSegmentCount") or 0
    )
    return {
        "schemaVersion": TARGET_PROCESSING_SCHEMA_VERSION,
        "source": {
            "sourceFileId": _text(source_file_id, 160),
            "sourceFileHash": _text(source_file_hash, 160),
            "document": safe_document,
        },
        "targetModules": modules,
        "targetModuleMap": by_key,
        "targetRanges": [
            _compact_range(target)
            for target in target_map.get("targets") or []
            if isinstance(target, dict)
        ],
        "projectInfo": project_items,
        "templates": templates,
        # Keep the writing consumer's name, but its contents are target
        # items, never the former category-based requirement projection.
        "templateIndex": templates,
        # The matching stage receives technical, qualification and rejection
        # together. Only the enterprise subset may query company evidence.
        "matchStageTargets": list(MATCH_STAGE_TARGETS),
        "enterpriseMatchTargets": list(ENTERPRISE_MATCH_TARGETS),
        "riskCheckTargets": list(RISK_CHECK_TARGETS),
        "matchStageRequirements": match_stage_requirements,
        "enterpriseMatchRequirements": enterprise_match_requirements,
        "riskCheckRequirements": risk_check_requirements,
        "moduleCounts": module_counts,
        "moduleDisplayCounts": module_display_counts,
        "warnings": list(dict.fromkeys(str(value) for value in (warnings or []) if str(value))),
        "stats": dict(stats or {}),
        "writingProjection": {
            "templates": templates,
            "templateIndex": templates,
            "matchStageRequirementIds": [
                str(item.get("id"))
                for item in match_stage_requirements
                if item.get("id")
            ],
            "enterpriseMatchRequirementIds": [
                str(item.get("id"))
                for item in enterprise_match_requirements
                if item.get("id")
            ],
            "riskCheckRequirementIds": [
                str(item.get("id"))
                for item in risk_check_requirements
                if item.get("id")
            ],
            "templateBodyMode": "source_range",
        },
    }


def extract_builtin_target_document(
    *,
    text: str,
    segments: list[dict[str, Any]] | None,
    sections: list[dict[str, Any]] | None,
    tables: list[dict[str, Any]] | None,
    ai: Mapping[str, Any] | None = None,
    source_file_id: str = "",
    project_id: str = "",
    document_meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    target_map = discover_target_ranges(
        segments=segments,
        sections=sections,
        tables=tables,
        ai=ai,
    )
    indexes = target_map["indexes"]
    warnings: list[str] = []
    diagnostics = dict(target_map.get("diagnostics") or {})
    if diagnostics.get("llmCalled") and not diagnostics.get("llmSuccess"):
        warnings.append(
            "目标范围 LLM 未返回有效结果，已使用规则候选范围继续提取。"
        )
    for target in target_map.get("targets") or []:
        key = str(target.get("targetModule") or "")
        target["items"] = (
            _module_items(key, target, indexes)
            if key != "rejection"
            else _rejection_items(
                indexes["segments"],
                indexes,
                target.get("segmentIds"),
                bool(target.get("reviewedByLlm")),
            )
        )
        if key == "contract":
            target["ignored"] = True
            target["items"] = []
        if key == "rejection":
            rejection_ids = [
                str(item.get("sourceSegmentId"))
                for item in (target.get("items") or [])
                if str(item.get("sourceSegmentId") or "")
            ]
            target["segmentIds"] = list(dict.fromkeys(rejection_ids))
            target["sectionIds"] = list(
                dict.fromkeys(
                    str(item.get("sectionId"))
                    for item in (target.get("items") or [])
                    if str(item.get("sectionId") or "")
                )
            )
            target["tableIds"] = list(
                dict.fromkeys(
                    table_id
                    for item in (target.get("items") or [])
                    for table_id in (item.get("tableIds") or [])
                    if str(table_id)
                )
            )
            target["ranges"] = _ranges_from_segments(
                target["segmentIds"],
                indexes,
            )
            target["selectionSource"] = "regex"
            target["selectedSegmentCount"] = len(target["segmentIds"])
            target["selectedSectionCount"] = len(target["sectionIds"])
            target["selectedTableCount"] = len(target["tableIds"])

    all_rejection_items = next(
        (
            target.get("items") or []
            for target in target_map.get("targets") or []
            if target.get("targetModule") == "rejection"
        ),
        [],
    )
    if not all_rejection_items:
        warnings.append("全文正则未命中废标、无效、否决或不接受条款。")
    if not any(
        target.get("items")
        for target in target_map.get("targets") or []
        if target.get("targetModule") in {"technical", "qualification"}
    ):
        warnings.append("未找到可用于技术或资格匹配的目标条目，请人工查看原文范围。")

    stats = {
        "targetMapSchemaVersion": target_map.get("schemaVersion"),
        "targetProcessingSchemaVersion": TARGET_PROCESSING_SCHEMA_VERSION,
        "targetMap": diagnostics,
        "targetRangeCount": len(target_map.get("targets") or []),
        "matchStageRequirementCount": sum(
            len(target.get("items") or [])
            for target in target_map.get("targets") or []
            if target.get("targetModule") in MATCH_STAGE_TARGETS
        ),
        "enterpriseMatchRequirementCount": sum(
            len(target.get("items") or [])
            for target in target_map.get("targets") or []
            if target.get("targetModule") in ENTERPRISE_MATCH_TARGETS
        ),
        "rejectionScanRequirementCount": len(all_rejection_items),
        "sourceFileId": source_file_id,
        "projectId": project_id,
        "extractionProvider": "builtin-target",
        "extractionMethod": (
            "target-llm+rule+review"
            if diagnostics.get("llmReviewTargetCount")
            else (
                "target-llm+rule"
                if diagnostics.get("llmSuccess")
                else "target-rule"
            )
        ),
    }
    analysis_pack = build_target_analysis_pack(
        target_map=target_map,
        indexes=indexes,
        source_file_id=source_file_id,
        source_file_hash=str((document_meta or {}).get("contentHash") or ""),
        document_meta=document_meta,
        stats=stats,
        warnings=warnings,
    )
    requirements = [
        item
        for key in MATCH_STAGE_TARGETS
        for target in target_map.get("targets") or []
        if target.get("targetModule") == key
        for item in (target.get("items") or [])
    ]
    return {
        "requirements": requirements,
        "targetModules": target_map.get("targets") or [],
        "targetRanges": target_map.get("targets") or [],
        "extractionMethod": stats["extractionMethod"],
        "extractionProvider": "builtin-target",
        "method": stats["extractionMethod"],
        "provider": "builtin-target",
        "modelUsed": str((ai or {}).get("model") or "") if diagnostics.get("llmSuccess") else "",
        "warnings": warnings,
        "extractionWarning": "；".join(warnings[:3]),
        "scope": {
            "schemaVersion": TARGET_MAP_SCHEMA_VERSION,
            "targets": [
                _compact_range(target)
                for target in target_map.get("targets") or []
            ],
        },
        "stats": stats,
        "analysisPack": analysis_pack,
        "packPath": "",
    }


def _fact_id(fact: Mapping[str, Any]) -> str:
    for key in (
        "qualificationId",
        "credentialId",
        "performanceId",
        "staffId",
        "financialEvidenceId",
        "parameterId",
        "productId",
        "organizationId",
        "id",
    ):
        if fact.get(key):
            return str(fact[key])
    return ""


def _fact_name(fact: Mapping[str, Any]) -> str:
    return str(
        fact.get("qualificationName")
        or fact.get("credentialName")
        or fact.get("caseName")
        or fact.get("name")
        or fact.get("productName")
        or fact.get("description")
        or _fact_id(fact)
        or ""
    )


def _facts_by_ids(company: Mapping[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    wanted = {str(value) for value in ids if str(value)}
    found: list[dict[str, Any]] = []
    for key in (
        "certificates",
        "personnel",
        "projects",
        "finance",
        "products",
        "parameters",
        "capabilities",
    ):
        for fact in company.get(key, []) or []:
            if isinstance(fact, dict) and _fact_id(fact) in wanted:
                found.append(fact)
    organization = company.get("organization")
    if isinstance(organization, dict) and _fact_id(organization) in wanted:
        found.append(organization)
    return found


def _evidence(fact: Mapping[str, Any]) -> dict[str, Any]:
    status = str(
        fact.get("verificationStatus")
        or fact.get("currentStatus")
        or fact.get("status")
        or ""
    )
    locator = str(
        fact.get("evidencePath")
        or fact.get("sourceLocator")
        or fact.get("filePath")
        or ""
    )
    return {
        "id": _fact_id(fact),
        "name": _fact_name(fact),
        "status": status,
        "verificationStatus": status,
        "sourceLocator": locator,
        "sourceType": "evidence" if locator else "knowledge_object",
        "excerpt": str(
            fact.get("description")
            or fact.get("caseName")
            or fact.get("scope")
            or ""
        ),
        "sourceFiles": [],
        "sourceFileIds": [],
        "materialId": "",
    }


def _match_kind(text: str) -> str:
    value = _compact(text)
    if any(term in value for term in ("类似业绩", "项目业绩", "案例", "合同业绩")):
        return "performance"
    if any(term in value for term in ("人员", "项目负责人", "技术负责人", "社保")):
        return "personnel"
    if any(term in value for term in ("财务", "审计报告", "营业收入", "资产总额", "净资产")):
        return "financial"
    if any(term in value for term in ("注册地", "本地企业", "服务区域", "地域")):
        return "region"
    return "qualification"


_TARGET_LEGACY_FIELDS = {
    "category",
    "module",
    "contentType",
    "typeHits",
    "directTypeHits",
    "inheritedType",
    "classificationSource",
    "requirementGroup",
    "routeHints",
    "routeTags",
}


def _target_requirement_contract(
    item: Mapping[str, Any],
    *,
    target_key: str,
    source_file_id: str,
) -> dict[str, Any]:
    """Adapt one target item to the comparison contract without old taxonomy."""

    target_item = {
        key: value
        for key, value in dict(item).items()
        if key not in _TARGET_LEGACY_FIELDS
    }
    requirement_id = _text(
        target_item.get("requirementId") or target_item.get("id"),
        100,
    )
    # 切分后的单条正文优先作为「要求」；rawText 是整段单元格原文（可能含上百条
    # 编号子句），只保留为回源参考，不能当作单条要求正文展示。
    requirement_text = _text(
        target_item.get("text")
        or target_item.get("requirement")
        or target_item.get("rawText"),
        6000,
    )
    raw_text = _text(
        target_item.get("rawText")
        or requirement_text,
        12000,
    )
    source_ids = [
        _text(value, 120)
        for value in (
            target_item.get("sourceSegmentIds")
            or target_item.get("segmentIds")
            or [target_item.get("sourceSegmentId")]
        )
        if _text(value, 120)
    ]
    is_rejection = target_key == "rejection"
    requirement_class = "rejection" if is_rejection else "evidence"
    target_label = TARGET_META[target_key]["label"]
    source = (
        dict(target_item.get("source"))
        if isinstance(target_item.get("source"), Mapping)
        else {}
    )
    source.setdefault("segmentIds", source_ids)
    source.setdefault("sourceLocator", _text(target_item.get("sourceLocator"), 200))
    source.setdefault("sectionId", _text(target_item.get("sectionId"), 100))
    source.setdefault(
        "sectionPath",
        [
            _text(value, 160)
            for value in (target_item.get("sectionPath") or [])
            if _text(value, 160)
        ],
    )
    source.setdefault(
        "tableIds",
        [
            _text(value, 100)
            for value in (target_item.get("tableIds") or [])
            if _text(value, 100)
        ],
    )
    return {
        **target_item,
        "id": requirement_id,
        "kind": target_label,
        "title": _text(target_item.get("title") or requirement_text, 240),
        "requirement": requirement_text,
        "sourceText": requirement_text,
        "requirementId": requirement_id,
        "targetModule": target_key,
        "targetModuleLabel": target_label,
        "primaryRoute": "rejection_check" if is_rejection else "company_match",
        "matchDomain": target_key,
        "requirementClass": requirement_class,
        "decisionImpact": "critical" if is_rejection else "high",
        "granularity": _text(target_item.get("granularity"), 40) or "clause",
        "reviewRequired": True,
        "reviewMode": "manual" if is_rejection else "assisted",
        "reviewReason": (
            "全文正则命中风险词，需要人工核对适用条件。"
            if is_rejection
            else "目标范围已定位，需要核验企业材料。"
        ),
        "rawText": raw_text,
        "normalizedRequirement": requirement_text,
        "mandatory": bool(target_item.get("mandatory", True)),
        "isRejection": is_rejection,
        "importance": "critical" if is_rejection else "high",
        "evidenceNeed": (
            "compliance_confirmation"
            if is_rejection
            else (
                "product_or_capability"
                if target_key == "technical"
                else "qualification_certificate"
            )
        ),
        "responseType": (
            "mandatory_confirmation"
            if is_rejection
            else (
                "technical_response"
                if target_key == "technical"
                else "evidence"
            )
        ),
        "handlingMode": "risk_check" if is_rejection else "database_match",
        "sourceStage": "tender_document",
        "layer": 3,
        "sourceFileId": _text(
            target_item.get("sourceFileId") or source_file_id,
            160,
        ),
        "sourceSegmentId": source_ids[0] if source_ids else "",
        "sourceSegmentIds": source_ids,
        "sourceLocator": _text(
            target_item.get("sourceLocator") or source.get("sourceLocator"),
            200,
        ),
        "sectionId": _text(
            target_item.get("sectionId") or source.get("sectionId"),
            100,
        ),
        "sectionPath": [
            _text(value, 160)
            for value in (
                target_item.get("sectionPath")
                or source.get("sectionPath")
                or []
            )
            if _text(value, 160)
        ],
        "tableIds": [
            _text(value, 100)
            for value in (
                target_item.get("tableIds")
                or source.get("tableIds")
                or []
            )
            if _text(value, 100)
        ],
        "source": source,
        "confidence": max(
            0.0,
            min(float(target_item.get("confidence") or 0.7), 1.0),
        ),
    }


def _status_result(
    result: Mapping[str, Any] | None,
    *,
    company: Mapping[str, Any],
) -> tuple[str, list[str], str, list[dict[str, Any]], dict[str, Any]]:
    raw = result or {}
    status = str(raw.get("status") or "待补材料")
    if status not in {"满足", "部分满足", "不满足", "待补材料", "待澄清"}:
        status = "待补材料"
    ids = [str(value) for value in raw.get("matchedFactIds") or [] if str(value)]
    facts = _facts_by_ids(company, ids)
    evidence = [_evidence(fact) for fact in facts]
    reason = str(raw.get("reason") or "")
    extras = {
        key: value
        for key, value in raw.items()
        if key in {"technicalAlignment", "gaps", "confidence"}
    }
    return status, ids, reason, evidence, extras


def _public_row(row: dict[str, Any], target_key: str) -> dict[str, Any]:
    row["targetModule"] = target_key
    row["targetModuleLabel"] = TARGET_META[target_key]["label"]
    row["matchDomain"] = target_key
    row["kind"] = TARGET_META[target_key]["label"]
    # The old ontology is an implementation detail of the comparison helpers.
    # Builtin output is target-oriented and must not expose it to API clients.
    for key in (
        "category",
        "module",
        "contentType",
        "typeHits",
        "directTypeHits",
        "inheritedType",
        "classificationSource",
        "requirementGroup",
        "routeHints",
        "routeTags",
    ):
        row.pop(key, None)
    return row


def _target_summary(
    rows: list[dict[str, Any]],
    extraction: Mapping[str, Any],
) -> dict[str, Any]:
    status_counts = Counter(str(row.get("status") or "待补材料") for row in rows)
    rejection_rows = [
        row for row in rows
        if row.get("targetModule") == "rejection"
    ]
    rejection_risks = [
        row for row in rejection_rows
        if row.get("status") == "有风险"
    ]
    pending = [
        row for row in rows
        if row.get("requirementClass") == "evidence"
        and row.get("status") in {"待补材料", "部分满足", "不满足"}
    ]
    hard_blockers = [
        {
            "requirementId": str(row.get("requirementId") or row.get("id") or ""),
            "title": str(row.get("kind") or "废标项"),
            "detail": str(row.get("requirement") or row.get("rawText") or ""),
            "sourceLocator": str(row.get("sourceLocator") or ""),
        }
        for row in rejection_risks
    ]
    total_match = sum(
        1
        for row in rows
        if row.get("targetModule") in {"technical", "qualification"}
    )
    satisfied = sum(
        1
        for row in rows
        if row.get("targetModule") in {"technical", "qualification"}
        and row.get("status") == "满足"
    )
    score = round(satisfied * 100 / total_match) if total_match else 0
    if hard_blockers:
        decision = "不建议参与"
    elif pending:
        decision = "人工重点评估"
    elif total_match and score >= 85:
        decision = "建议进入正式投标准备"
    elif total_match and score >= 60:
        decision = "有条件参与，先补材料"
    else:
        decision = "待澄清"
    return {
        "rows": rows,
        "risks": [
            {
                "level": str(row.get("risk") or "中"),
                "title": str(row.get("kind") or "目标条目"),
                "detail": f"{row.get('requirement', '')}。{row.get('suggestion', '')}",
                "sourceLocator": str(row.get("sourceLocator") or ""),
            }
            for row in rows
            if row.get("risk") not in {"低", ""}
        ][:50],
        "score": score,
        "decision": decision,
        "recommendation": decision,
        "clauseCount": len(rows),
        "statusCounts": dict(status_counts),
        "classCounts": {
            "evidence": sum(1 for row in rows if row.get("requirementClass") == "evidence"),
            "rejection": sum(1 for row in rows if row.get("requirementClass") == "rejection"),
        },
        "summary": {
            **summarize_comparisons(rows),
            "totalRequirementCount": len(rows),
            "statusCounts": dict(status_counts),
        },
        "hardBlockers": hard_blockers,
        "rejectionRisks": [
            {
                "requirementId": str(row.get("requirementId") or row.get("id") or ""),
                "title": str(row.get("kind") or "废标项"),
                "detail": str(row.get("requirement") or ""),
                "sourceLocator": str(row.get("sourceLocator") or ""),
            }
            for row in rejection_rows
        ],
        "unresolvedMandatory": [
            {
                "requirementId": str(row.get("requirementId") or row.get("id") or ""),
                "title": str(row.get("kind") or "目标条目"),
                "detail": str(row.get("requirement") or ""),
                "sourceLocator": str(row.get("sourceLocator") or ""),
            }
            for row in pending
        ],
        "pendingMaterialList": [
            {
                "requirementId": str(row.get("requirementId") or row.get("id") or ""),
                "title": str(row.get("kind") or "目标条目"),
                "detail": str(row.get("requirement") or ""),
                "sourceLocator": str(row.get("sourceLocator") or ""),
                "importance": str(row.get("importance") or "high"),
                "evidenceNeed": str(row.get("evidenceNeed") or ""),
            }
            for row in pending
        ],
        "analysisPack": extraction.get("analysisPack"),
        "extraction": dict(extraction),
    }


def build_target_fine_review(
    *,
    extraction: Mapping[str, Any],
    company_knowledge: Mapping[str, Any],
    ai: Mapping[str, Any] | None,
    source_file_id: str = "",
) -> dict[str, Any]:
    """Process the three matching-stage targets.

    Technical and qualification items query enterprise evidence. Rejection
    items stay in the same stage envelope but only become manual risk rows.
    """

    target_modules = extraction.get("targetModules") or []
    match_stage_items = [
        item
        for target in target_modules
        if isinstance(target, dict)
        and target.get("targetModule") in MATCH_STAGE_TARGETS
        for item in (target.get("items") or [])
        if isinstance(item, dict)
    ]
    requirements = [
        item
        for item in match_stage_items
        if item.get("targetModule") in ENTERPRISE_MATCH_TARGETS
    ]
    technical = [item for item in requirements if item.get("targetModule") == "technical"]
    technical_results: dict[str, Any] = {}
    if technical:
        # 技术类走「拆可核验技术点 → 逐点对齐 → 覆盖率」判定，产出
        # technicalAlignment（coverage / matchedUnits / matchedParameterDetails /
        # gaps），前端据此展示「对应企业产品与参数」和覆盖率。
        tech_units = build_tech_units(company_knowledge)
        tech_req_items: list[dict[str, Any]] = []
        for index, item in enumerate(technical):
            req_text = str(item.get("text") or item.get("rawText") or "").strip()
            if req_text:
                tech_req_items.append({"index": index, "req_text": req_text})

        if tech_req_items and tech_units:
            judged = judge_technical_requirements(tech_req_items, tech_units, ai)
            for index, item in enumerate(technical):
                result = judged.get(index)
                technical_results[str(item.get("id") or "")] = result or {
                    "status": "待补材料",
                    "matchedFactIds": [],
                    "reason": "技术对齐判定不可用，待人工复核",
                    "statusSource": "规则兜底",
                }
        else:
            for item in technical:
                technical_results[str(item.get("id") or "")] = {
                    "status": "待补材料",
                    "matchedFactIds": [],
                    "reason": (
                        "企业未维护技术产品/参数，待人工补充后复核"
                        if not tech_units
                        else "缺少可匹配的技术要求原文"
                    ),
                    "statusSource": "规则兜底",
                }

    legacy_rows: list[dict[str, Any]] = []
    for item in requirements:
        target_key = str(item.get("targetModule") or "")
        contract = _target_requirement_contract(
            item,
            target_key=target_key,
            source_file_id=source_file_id,
        )
        text = str(item.get("text") or item.get("rawText") or "")
        if target_key == "technical":
            raw_match = technical_results.get(str(item.get("id") or ""), {})
            status, matched_ids, reason, evidences, extras = _status_result(
                raw_match,
                company=company_knowledge,
            )
            status_source = str(raw_match.get("statusSource") or "目标匹配")
        else:
            strategy = _match_kind(text)
            raw = deterministic_match(text, strategy, dict(company_knowledge))
            status, matched_ids, reason, evidences, extras = _status_result(
                raw,
                company=company_knowledge,
            )
            if raw is None:
                status = "待补材料"
                reason = "资格审查目标已定位，但企业资料中暂未找到可直接核验的证据。"
            status_source = "目标规则匹配"
        facts = _facts_by_ids(company_knowledge, matched_ids)
        matched_objects = [
            {"id": _fact_id(fact), "name": _fact_name(fact)}
            for fact in facts
        ]
        requirement_class = "evidence"
        row = build_comparison(
            contract,
            status=status,
            reason=reason or "请结合企业资料和原文人工核验。",
            matched_objects=matched_objects,
            evidences=evidences,
            risk=(
                "低"
                if status == "满足"
                else ("高" if status == "不满足" else "中")
            ),
            suggestion=(
                "已找到对应企业证据，请复核原件和适用范围。"
                if status == "满足"
                else "请补充或核验企业材料后再确认。"
            ),
            status_source=status_source,
        )
        row["targetModule"] = target_key
        row["requirementClass"] = requirement_class
        row["primaryRoute"] = "company_match"
        row["reviewMode"] = "assisted" if status != "满足" else "auto"
        row["reviewRequired"] = status != "满足"
        row.update(extras)
        if "technicalAlignment" in extras:
            row["technicalAlignment"] = extras["technicalAlignment"]
        legacy_rows.append(row)

    # Rejection items are display/risk data.  They are projected into rows only
    # for the existing risk summary and manual status endpoint; no company
    # matching is performed for them.
    rejection_items = [
        item
        for item in match_stage_items
        if item.get("targetModule") in RISK_CHECK_TARGETS
    ]
    for item in rejection_items:
        contract = _target_requirement_contract(
            item,
            target_key="rejection",
            source_file_id=source_file_id,
        )
        row = build_comparison(
            contract,
            status="待核查",
            reason="全文规则命中废标/无效/否决风险词，需人工核对原文适用条件。",
            matched_objects=[],
            evidences=[],
            risk="中",
            suggestion="请确认该条款是否对本项目投标方式和企业主体实际触发。",
            status_source="全文正则",
        )
        row["targetModule"] = "rejection"
        row["requirementClass"] = "rejection"
        row["primaryRoute"] = "rejection_check"
        row["reviewMode"] = "manual"
        row["reviewRequired"] = True
        legacy_rows.append(row)

    review = _target_summary(legacy_rows, extraction)
    review["rows"] = [
        _public_row(row, str(row.get("targetModule") or ("rejection" if row.get("isRejection") else "qualification")))
        for row in legacy_rows
    ]
    # Rebuild row-derived lists with the public target rows.  The summary
    # values above are stable, but returning rows with the same shape keeps
    # manual status updates and frontend detail views consistent.
    public_rows = review["rows"]
    review["risks"] = [
        {
            "level": str(row.get("risk") or "中"),
            "title": str(row.get("kind") or "目标条目"),
            "detail": f"{row.get('requirement', '')}。{row.get('suggestion', '')}",
            "sourceLocator": str(row.get("sourceLocator") or ""),
        }
        for row in public_rows
        if row.get("risk") not in {"低", ""}
    ][:50]
    review["extraction"] = dict(extraction)
    review["analysisPack"] = extraction.get("analysisPack")
    review["matchStage"] = {
        "targetModules": list(MATCH_STAGE_TARGETS),
        "enterpriseMatchTargets": list(ENTERPRISE_MATCH_TARGETS),
        "riskCheckTargets": list(RISK_CHECK_TARGETS),
        "enterpriseMatchCount": sum(
            row.get("targetModule") in ENTERPRISE_MATCH_TARGETS
            for row in public_rows
        ),
        "riskCheckCount": sum(
            row.get("targetModule") in RISK_CHECK_TARGETS
            for row in public_rows
        ),
    }
    return review


__all__ = [
    "TARGET_PROCESSING_SCHEMA_VERSION",
    "build_target_analysis_pack",
    "build_target_fine_review",
    "extract_builtin_target_document",
]
