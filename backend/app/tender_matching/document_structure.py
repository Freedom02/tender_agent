from __future__ import annotations

import copy
import re
from typing import Any


SECTION_TYPE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "rejection",
        (
            "无效投标条款",
            "无效标条款",
            "废标条款",
            "否决条款",
            "无效投标",
            "投标无效",
        ),
    ),
    (
        "compliance",
        (
            "符合性审查",
            "符合性检查",
            "符合性评审",
            "初步审查",
            "初步审查表",
        ),
    ),
    (
        "qualification",
        (
            "资格性审查",
            "投标人资格要求",
            "投标人资格条件",
            "供应商资格要求",
            "供应商资格条件",
            "供应商的资格要求",
            "投标供应商资格条件",
            "资格审查要求",
            "资格审查",
            "合格投标人",
            "特定资格要求",
        ),
    ),
    (
        "procurement",
        (
            "获取招标文件",
            "获取采购文件",
            "招标文件的获取",
            "采购文件的获取",
            "报名",
        ),
    ),
    (
        "evaluation",
        (
            "评标办法",
            "评审办法",
            "评标方法",
            "评分标准",
            "评审标准",
            "评审方法",
            "评标委员会",
        ),
    ),
    (
        "technical",
        (
            "项目技术需求",
            "项目技术（质量）需求",
            "技术（质量）需求",
            "项目技术",
            "技术需求",
            "技术参数",
            "技术要求",
            "功能需求",
            "采购标的技术",
            "技术和服务要求",
        ),
    ),
    (
        "service_plan",
        (
            "服务方案",
            "项目实施方案",
            "实施方案",
            "服务实施方案",
            "项目理解",
            "组织实施方案",
            "质量保障方案",
            "应急服务方案",
            "培训方案",
            "运维方案",
        ),
    ),
    (
        "acceptance",
        (
            "验收标准",
            "验收方式",
            "项目验收",
            "验收要求",
            "交付及验收",
        ),
    ),
    (
        "commercial",
        (
            "项目商务需求",
            "商务需求",
            "商务要求",
            "商务条件",
            "服务内容及商务要求",
            "交货期",
            "服务期限",
            "售后服务",
        ),
    ),
    (
        "pricing",
        (
            "报价要求",
            "投标报价",
            "报价文件",
            "分项报价",
            "报价明细",
        ),
    ),
    (
        "document_format",
        (
            "投标文件格式",
            "响应文件格式",
            "投标文件编制",
            "响应文件编制",
            "投标文件组成",
            "响应文件组成",
            "文件编制要求",
            "签署与封装",
            "签字与盖章",
            "装订要求",
        ),
    ),
    (
        "personnel",
        (
            "人员要求",
            "项目人员",
            "项目团队",
            "项目经理",
            "项目负责人",
        ),
    ),
    (
        "performance",
        (
            "业绩要求",
            "类似业绩",
            "项目业绩",
            "业绩证明",
        ),
    ),
    (
        "contract",
        (
            "合同条款",
            "合同主要条款",
            "采购合同",
            "服务合同",
            "合同范本",
        ),
    ),
    (
        "basic",
        (
            "项目基本情况",
            "项目概况",
            "采购项目概述",
            "招标项目内容",
            "采购范围",
            "项目要求",
            "采购需求",
        ),
    ),
    (
        "invitation",
        (
            "投标邀请书",
            "投标邀请",
            "采购邀请",
            "响应邀请",
            "邀请函",
        ),
    ),
    (
        "instructions",
        (
            "投标人须知",
            "供应商须知",
            "投标人须知前附表",
            "响应文件须知",
            "投标有关说明",
        ),
    ),
    (
        "appendix",
        (
            "附件",
            "附录",
        ),
    ),
)


CHINESE_NUMERAL = "一二三四五六七八九十百千万零〇两"
MARKER_PREFIX = r"[★▲※○●]?\s*"
HEADING_PREFIXES = (
    re.compile(rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+(?:篇|章|部|部分|册|卷).*"),
    re.compile(rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+节.*"),
    re.compile(rf"^{MARKER_PREFIX}[{CHINESE_NUMERAL}]+[、.．].*"),
    re.compile(rf"^{MARKER_PREFIX}[（(][{CHINESE_NUMERAL}0-9]+[）)].*"),
    re.compile(rf"^{MARKER_PREFIX}\d+(?:\.\d+){{1,4}}(?:\s|[、.．)]|$).*"),
    re.compile(rf"^{MARKER_PREFIX}附件(?:[一二三四五六七八九十0-9]+|[（(][一二三四五六七八九十0-9]+[）)]).*"),
)

TOP_LEVEL_PATTERN = re.compile(
    rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+(?:篇|章|部|部分|册|卷).*"
)
CHAPTER_PATTERN = re.compile(rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+章.*")
SECTION_PATTERN = re.compile(rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+节.*")
CHINESE_ITEM_PATTERN = re.compile(rf"^{MARKER_PREFIX}[{CHINESE_NUMERAL}]+[、.．].*")
PAREN_ITEM_PATTERN = re.compile(rf"^{MARKER_PREFIX}[（(][{CHINESE_NUMERAL}0-9]+[）)].*")
DECIMAL_PATTERN = re.compile(rf"^{MARKER_PREFIX}\d+(?:\.\d+){{1,4}}(?:\s|[、.．)]|$)")
APPENDIX_PATTERN = re.compile(rf"^{MARKER_PREFIX}附件(?:[一二三四五六七八九十0-9]+|[（(][一二三四五六七八九十0-9]+[）)]).*")

REQUIREMENT_HINTS = (
    "须",
    "必须",
    "应当",
    "提供",
    "具有",
    "具备",
    "不得",
    "不少于",
    "不低于",
    "至少",
    "否则",
    "无效",
)


def _clean_text(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _clean_heading(value: Any) -> str:
    text = _clean_text(value, 240)
    text = re.sub(r"\bHYPERLINK\s+\\l\s+\"[^\"]*\"\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPAGEREF\s+[^ ]+(?:\s+\\h)?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bTOC\b.*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+[-－—]\s*\d+\s*[-－—]\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -－—")
    return text[:160]


def _style_has_heading_signal(segment: dict[str, Any]) -> bool:
    style = segment.get("style")
    if not isinstance(style, dict):
        return False
    style_id = str(style.get("styleId") or "").lower()
    return (
        style_id.startswith("heading")
        or "标题" in style_id
    )


def _heading_source_text(segment: dict[str, Any]) -> str:
    content_type = str(segment.get("contentType") or "")
    if content_type == "table_row":
        cells = segment.get("cells")
        if isinstance(cells, list):
            values = [
                _clean_text(cell.get("text"), 160)
                for cell in cells
                if isinstance(cell, dict) and _clean_text(cell.get("text"), 160)
            ]
            if len(values) == 1:
                return values[0]
    return _clean_heading(segment.get("text"))


def _looks_like_heading(segment: dict[str, Any]) -> bool:
    raw_text = _clean_text(segment.get("text"), 240)
    if _looks_like_toc_entry(raw_text):
        return False
    text = _heading_source_text(segment)
    if not text or text in {"目录", "目 录", "索引"}:
        return False
    compact = re.sub(r"\s+", "", text).upper()
    if "HYPERLINK" in compact or "PAGEREF" in compact or compact.startswith("TOC"):
        return False

    style_signal = _style_has_heading_signal(segment)
    content_type = str(segment.get("contentType") or "")
    if content_type == "table_row" and "|" in str(segment.get("text") or ""):
        return False
    if not style_signal and len(text) > (84 if content_type == "table_row" else 120):
        return False

    prefix_signal = any(pattern.match(text) for pattern in HEADING_PREFIXES)
    term_signal = _section_type_for_heading(text) != "other"
    if not style_signal and not prefix_signal and not term_signal:
        return False

    if prefix_signal and not style_signal:
        tail = re.sub(
            r"^(?:第[{0}0-9]+(?:篇|章|部|部分|册|卷)|第[{0}0-9]+节|[{0}]+|附件.*?)[、.．:\s：)]*".format(
                CHINESE_NUMERAL
            ),
            "",
            text,
        )
        if any(hint in tail[:24] for hint in REQUIREMENT_HINTS) and len(text) > 24:
            return False
    if not style_signal and re.search(r"[。！？；]$", text):
        return False
    return True


def _looks_like_toc_entry(text: str) -> bool:
    clean = _clean_text(text, 240)
    if not clean:
        return False
    if re.search(r"\b(?:TOC|HYPERLINK|PAGEREF)\b", clean, flags=re.IGNORECASE):
        return True
    if not any(pattern.match(clean) for pattern in HEADING_PREFIXES):
        return False
    if re.search(r"(?:\s|[.·…])[-－]?\s*\d+\s*[-－]?$", clean):
        return True
    return bool(re.search(r"\s+\d+$", clean)) and len(clean) <= 80


def _section_type_for_heading(heading: str) -> str:
    compact = re.sub(r"\s+", "", heading)
    for section_type, terms in SECTION_TYPE_TERMS:
        if any(term in compact for term in terms):
            return section_type
    return "other"


def _level_for_heading(heading: str, segment: dict[str, Any]) -> int:
    style = segment.get("style") if isinstance(segment.get("style"), dict) else {}
    outline_level = str(style.get("outlineLevel") or "").strip()
    if outline_level.isdigit():
        return min(int(outline_level) + 1, 8)
    if TOP_LEVEL_PATTERN.match(heading):
        return 1
    if SECTION_PATTERN.match(heading):
        return 2
    if CHAPTER_PATTERN.match(heading):
        return 1
    if APPENDIX_PATTERN.match(heading):
        return 1
    if CHINESE_ITEM_PATTERN.match(heading):
        return 2
    if PAREN_ITEM_PATTERN.match(heading):
        return 3
    if DECIMAL_PATTERN.match(heading):
        digits = re.match(r"^\d+(?:\.\d+)*", heading)
        return min((digits.group(0).count(".") + 2) if digits else 3, 8)
    return 2


def _segment_locator(segment: dict[str, Any], fallback_index: int) -> str:
    return _clean_text(segment.get("sourceLocator"), 160) or f"第{fallback_index + 1}个内容单元"


def _section_text(segments: list[dict[str, Any]], start: int, end: int) -> str:
    return "\n".join(
        _clean_text(segment.get("text"), 12000)
        for segment in segments[start : end + 1]
        if _clean_text(segment.get("text"), 12000)
    )


def build_section_tree(segments: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_segments = [
        segment for segment in segments
        if isinstance(segment, dict) and _clean_text(segment.get("text"), 12000)
    ]
    headings: list[dict[str, Any]] = []
    for position, segment in enumerate(normalized_segments):
        heading = _heading_source_text(segment)
        if not _looks_like_heading(segment):
            continue
        headings.append(
            {
                "position": position,
                "heading": heading,
                "sourceSegmentId": str(segment.get("id") or ""),
                "sourceLocator": _segment_locator(segment, position),
                "level": _level_for_heading(heading, segment),
                "sectionType": _section_type_for_heading(heading),
            }
        )

    sections: list[dict[str, Any]] = []
    for index, candidate in enumerate(headings):
        end_position = len(normalized_segments) - 1
        for next_candidate in headings[index + 1 :]:
            if int(next_candidate["level"]) <= int(candidate["level"]):
                end_position = int(next_candidate["position"]) - 1
                break

        parent = next(
            (
                section for section in reversed(sections)
                if int(section["level"]) < int(candidate["level"])
                and int(section["startPosition"]) < int(candidate["position"])
            ),
            None,
        )
        section_type = candidate["sectionType"]
        if section_type == "other" and parent and parent.get("sectionType") not in {"other", "basic"}:
            section_type = str(parent["sectionType"])
        section_path = [
            *(parent.get("sectionPath", []) if parent else []),
            candidate["heading"],
        ]
        section_id = f"SEC-{index + 1:04d}"
        covered_segments = normalized_segments[
            int(candidate["position"]) : end_position + 1
        ]
        sections.append(
            {
                "id": section_id,
                "sectionId": section_id,
                "sectionType": section_type,
                "heading": candidate["heading"],
                "level": candidate["level"],
                "parentId": parent["id"] if parent else "",
                "sectionPath": section_path,
                "headingSegmentId": candidate["sourceSegmentId"],
                "startPosition": candidate["position"],
                "endPosition": end_position,
                "startLocator": candidate["sourceLocator"],
                "endLocator": _segment_locator(
                    normalized_segments[end_position],
                    end_position,
                ),
                "segmentIds": [
                    str(segment.get("id") or "")
                    for segment in covered_segments
                    if str(segment.get("id") or "")
                ],
                "text": _section_text(
                    normalized_segments,
                    int(candidate["position"]),
                    end_position,
                ),
            }
        )

    if headings:
        first_heading_position = int(headings[0]["position"])
        if first_heading_position > 0:
            prefix_segments = normalized_segments[:first_heading_position]
            sections.insert(
                0,
                {
                    "id": "SEC-0000",
                    "sectionId": "SEC-0000",
                    "sectionType": "other",
                    "heading": "前置内容",
                    "level": 0,
                    "parentId": "",
                    "sectionPath": ["前置内容"],
                    "headingSegmentId": "",
                    "startPosition": 0,
                    "endPosition": first_heading_position - 1,
                    "startLocator": _segment_locator(prefix_segments[0], 0),
                    "endLocator": _segment_locator(
                        prefix_segments[-1],
                        first_heading_position - 1,
                    ),
                    "segmentIds": [
                        str(segment.get("id") or "")
                        for segment in prefix_segments
                        if str(segment.get("id") or "")
                    ],
                    "text": _section_text(
                        normalized_segments,
                        0,
                        first_heading_position - 1,
                    ),
                    "implicit": True,
                },
            )
    elif normalized_segments:
        sections = [
            {
                "id": "SEC-0000",
                "sectionId": "SEC-0000",
                "sectionType": "other",
                "heading": "未分类内容",
                "level": 0,
                "parentId": "",
                "sectionPath": ["未分类内容"],
                "headingSegmentId": "",
                "startPosition": 0,
                "endPosition": len(normalized_segments) - 1,
                "startLocator": _segment_locator(normalized_segments[0], 0),
                "endLocator": _segment_locator(
                    normalized_segments[-1],
                    len(normalized_segments) - 1,
                ),
                "segmentIds": [
                    str(segment.get("id") or "")
                    for segment in normalized_segments
                    if str(segment.get("id") or "")
                ],
                "text": _section_text(
                    normalized_segments,
                    0,
                    len(normalized_segments) - 1,
                ),
                "implicit": True,
            }
        ]

    for section in sections:
        section.pop("startPosition", None)
        section.pop("endPosition", None)

    return {
        "sections": sections,
        "sectionCount": len(sections),
        "headingCount": len(headings),
        "warnings": (
            []
            if headings
            else ["未识别到明确章节标题，已将内容归入未分类章节。"]
            if normalized_segments
            else ["文档没有可用于建立章节树的内容单元。"]
        ),
    }


def enrich_document_structure(parsed: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(parsed)
    structure = build_section_tree(result.get("segments", []))
    result["sections"] = structure["sections"]
    result["structureWarnings"] = structure["warnings"]
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["sectionCount"] = structure["sectionCount"]
    metadata["headingCount"] = structure["headingCount"]
    result["metadata"] = metadata
    result["warnings"] = list(
        dict.fromkeys(
            [
                *(
                    item for item in result.get("warnings", [])
                    if isinstance(item, str)
                ),
                *structure["warnings"],
            ]
        )
    )
    return result
