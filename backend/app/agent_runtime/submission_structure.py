"""投标文件组成、卷内条目和分册规则的确定性契约。

招标文件明确列出的“经济文件/技术文件/商务文件”等卷及其卷内条目，
属于解析结果的权威结构。Pi 只能在此结构上补充模板类型、条件和字段，
不能重新编造、删除或改写这些标题。
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any


_CHAPTER_RE = re.compile(
    r"^\s*第\s*([一二三四五六七八九十百千万零〇\d]+)\s*"
    r"(篇|章|部分|节)\s*(.*?)\s*$"
)
_VOLUME_RE = re.compile(
    r"^\s*([一二三四五六七八九十百千万零〇\d]+)\s*[、.．]\s*(.*?)\s*$"
)
_ITEM_RE = re.compile(
    r"^\s*[（(]\s*([一二三四五六七八九十百千万零〇\d]+)\s*[）)]\s*(.*?)\s*$"
)
_ASCII_ITEM_RE = re.compile(
    r"^\s*(\d+)\s*[、.．)]\s*(.*?)\s*$"
)
_COMPOSITION_VOLUME_RE = re.compile(
    r"^\s*[（(]\s*(\d+)\s*[）)]\s*(.*?)\s*$"
)
_CIRCLED_ITEM_RE = re.compile(
    r"^\s*([①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳])\s*(.*?)\s*$"
)
_CLAUSE_RE = re.compile(r"^\s*\d+(?:\.\d+)+\s*(?:[、.)．])?\s*(.*?)\s*$")
_PAGE_SUFFIX_RE = re.compile(r"\s*[-—–]\s*\d+\s*[-—–]\s*$")

_FORMAT_TERMS = (
    "投标文件格式",
    "电子投标文件格式",
    "响应文件格式",
    "投标文件组成",
    "响应文件组成",
    "投标文件的组成",
    "响应文件的组成",
    "投标文件的构成",
    "响应文件的构成",
    "投标文件目录",
    "响应文件目录",
)

_COMPOSITION_TERMS = (
    "电子投标文件由下述部分组成",
    "电子投标文件由以下部分组成",
    "电子投标文件组成",
    "投标文件由下述部分组成",
    "投标文件由以下部分组成",
    "响应文件由下述部分组成",
    "响应文件由以下部分组成",
    "投标文件应包括",
    "响应文件应包括",
)

_UNTRUSTWORTHY_VOLUME_TERMS = (
    "我单位",
    "本人",
    "具有",
    "不存在",
    "请投标人",
    "请供应商",
    "投标人为",
    "供应商为",
    "应提供",
    "应当",
    "否则",
    "资格承诺的",
    "在投标",
    "在响应",
)

_CONDITIONAL_TERMS = ("如有", "如果有", "若有", "可提供", "适用时")


def _text(value: Any, limit: int = 1200) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip()


def _dedupe(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _clean_line(value: Any) -> str:
    line = _text(value, 600)
    line = _PAGE_SUFFIX_RE.sub("", line).strip()
    if re.fullmatch(r"[-—–]?\s*\d+\s*[-—–]?", line):
        return ""
    return line


def _normalized_title(value: Any) -> str:
    return _compact(value).strip("：:;；。.")


def _contains_format_term(value: Any) -> bool:
    compact = _compact(value)
    return any(term in compact for term in _FORMAT_TERMS)


def _contains_composition_term(value: Any) -> bool:
    compact = _compact(value)
    return any(term in compact for term in _COMPOSITION_TERMS)


def _volume_title_issue(value: Any) -> str:
    """返回不适合作为投标文件卷标题的原因，空字符串表示通过。"""
    title = _text(value, 240)
    compact_raw = re.sub(r"\s+", "", title)
    compact = _normalized_title(title)
    if not compact:
        return "标题为空"
    if len(compact) > 80:
        return "标题过长，疑似正文句子"
    if any(term in compact for term in _UNTRUSTWORTHY_VOLUME_TERMS):
        return "标题包含资格条件或操作说明句式"
    if compact_raw.endswith((":", "：", ";", "；", ".", "。")):
        return "标题以句末标点结束，疑似正文句子"
    if any(mark in compact for mark in ("复印件", "证明材料", "情形之一", "规定的条件")):
        return "标题包含证明/条件正文"
    return ""


def is_reliable_submission_structure(value: Any) -> bool:
    """判断结构是否足以作为目录骨架，而不是普通要求条款。"""
    structure = normalize_submission_structure(value)
    volumes = structure.get("volumes") or []
    if not volumes:
        return False
    if structure.get("confidence") not in {"explicit", "inferred"}:
        return False
    return all(
        not _volume_title_issue(volume.get("title"))
        and isinstance(volume.get("items"), list)
        for volume in volumes
        if isinstance(volume, dict)
    )


def _slug(value: str) -> str:
    compact = _normalized_title(value)
    replacements = (
        ("经济", "economic"),
        ("技术", "technical"),
        ("质量", "quality"),
        ("商务", "commercial"),
        ("其他", "other"),
        ("资格", "qualification"),
        ("报价", "pricing"),
        ("附件", "attachment"),
    )
    for needle, result in replacements:
        if needle in compact:
            return result
    ascii_value = re.sub(r"[^A-Za-z0-9]+", "-", value.lower()).strip("-")
    return ascii_value[:48] or "volume"


def _unique_id(base: str, used: set[str], fallback: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_-]+", "-", str(base or "")).strip("-_") or fallback
    candidate = candidate[:80]
    if candidate not in used:
        used.add(candidate)
        return candidate
    index = 2
    while f"{candidate}-{index}" in used:
        index += 1
    candidate = f"{candidate}-{index}"
    used.add(candidate)
    return candidate


def _item_metadata(title: str) -> dict[str, Any]:
    compact = _normalized_title(title)
    conditional = any(term in compact for term in _CONDITIONAL_TERMS)
    if any(
        term in compact
        for term in (
            "开标一览",
            "报价明细",
            "分项报价",
            "报价表",
            "偏离表",
            "差异表",
            "一览表",
            "明细表",
            "响应表",
        )
    ):
        item_type = "fixed_table"
        response_type = "table"
    elif any(term in compact for term in ("投标函", "响应函", "授权委托书", "法定代表人身份证明", "承诺函", "声明函")):
        item_type = "fixed_form"
        response_type = "fixed_form"
    elif any(term in compact for term in ("其他技术", "技术方案", "技术响应", "其他商务", "商务资料", "其他项目")):
        item_type = "narrative"
        response_type = "chapter_text"
    elif any(term in compact for term in ("营业执照", "证明文件", "证书", "资质", "资料")):
        item_type = "evidence"
        response_type = "attachment"
    else:
        item_type = "attachment"
        response_type = "attachment"

    template_type = ""
    if item_type in {"fixed_form", "fixed_table"}:
        template_type = "fixed_template" if "参考" not in compact else "reference_example"
    return {
        "itemType": item_type,
        "responseType": response_type,
        "conditional": conditional,
        "conditionText": (
            "招标文件标注为条件提交项，满足条件时提交。"
            if conditional
            else ""
        ),
        "templateType": template_type,
    }


def _extract_format_range(text: str) -> tuple[list[str], int, int, str, str]:
    lines = str(text or "").splitlines()
    composition_candidates = [
        index
        for index, raw in enumerate(lines)
        if _contains_composition_term(_clean_line(raw))
    ]
    if composition_candidates:
        # 优先选择后续确实出现“（1）部分 + ①文件项”的锚点，避免
        # 资格审查正文中的“投标文件格式二-2-1”抢占起点。
        def composition_score(index: int) -> tuple[int, int]:
            window = [_clean_line(item) for item in lines[index + 1 : index + 40]]
            volume_hits = sum(bool(_COMPOSITION_VOLUME_RE.match(item)) for item in window)
            item_hits = sum(bool(_CIRCLED_ITEM_RE.match(item)) for item in window)
            return volume_hits + item_hits, -index

        start = max(composition_candidates, key=composition_score)
        chapter_title = _clean_line(lines[start]) or "电子投标文件组成"
        end = len(lines)
        for index in range(start + 1, len(lines)):
            line = _clean_line(lines[index])
            if index > start + 1 and _CLAUSE_RE.match(line):
                end = index
                break
            match = _CHAPTER_RE.match(line)
            if match and not _contains_format_term(line) and not _contains_composition_term(line):
                end = index
                break
        return lines, start, end, chapter_title, "composition"

    candidates = [
        index
        for index, raw in enumerate(lines)
        if _contains_format_term(_clean_line(raw))
    ]
    if not candidates:
        return lines, -1, len(lines), "", "unknown"

    chapter_candidates = [
        index
        for index in candidates
        if _CHAPTER_RE.match(_clean_line(lines[index]))
    ]
    start = (chapter_candidates or candidates)[-1]
    chapter_match = _CHAPTER_RE.match(_clean_line(lines[start]))
    chapter_title = (
        _text(chapter_match.group(3), 240)
        if chapter_match
        else _clean_line(lines[start])
    )
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = _clean_line(lines[index])
        match = _CHAPTER_RE.match(line)
        if not match:
            continue
        if _contains_format_term(line):
            continue
        end = index
        break
    return lines, start, end, chapter_title or "投标文件格式", "format"


def extract_submission_format_text(text: str, *, max_chars: int = 30000) -> str:
    """截取完整“投标文件格式”章节供 Pi 和确定性解析共同使用。"""

    lines, start, end, _, _ = _extract_format_range(text)
    if start < 0:
        return str(text or "").strip()[:max_chars]
    focused = "\n".join(lines[start:end]).strip()
    if len(focused) <= max_chars:
        return focused
    head = int(max_chars * 0.72)
    tail = max_chars - head
    return f"{focused[:head]}\n...[格式章节中间内容省略]...\n{focused[-tail:]}"


def _parse_format_lines(
    lines: list[str],
    start: int,
    end: int,
    chapter_title: str,
    source_kind: str = "format",
) -> tuple[list[dict[str, Any]], list[str]]:
    volumes: list[dict[str, Any]] = []
    warnings: list[str] = []
    current: dict[str, Any] | None = None
    seen_volume_titles: set[str] = set()
    used_volume_ids: set[str] = set()

    for line_index in range(max(start + 1, 0), min(end, len(lines))):
        line = _clean_line(lines[line_index])
        if not line:
            continue
        if source_kind == "composition":
            volume_match = _COMPOSITION_VOLUME_RE.match(line)
            item_match = _CIRCLED_ITEM_RE.match(line)
        else:
            volume_match = _VOLUME_RE.match(line)
            item_match = _ITEM_RE.match(line) or _ASCII_ITEM_RE.match(line)
        if volume_match:
            title = _text(volume_match.group(2), 240)
            if not title:
                continue
            title_issue = _volume_title_issue(title)
            if title_issue:
                warnings.append(f"疑似非目录卷标题“{title}”，已忽略：{title_issue}。")
                continue
            normalized = _normalized_title(title)
            # “第七篇”后面经常紧跟各模板正文，正文中可能再次出现
            # “一、经济文件”。重复卷标题表示目录清单已经结束。
            if normalized in seen_volume_titles:
                break
            volume_id = _unique_id(
                _slug(title),
                used_volume_ids,
                f"volume-{len(volumes) + 1:02d}",
            )
            current = {
                "volumeId": volume_id,
                "title": title,
                "order": len(volumes) + 1,
                "required": True,
                "sourceLocator": f"{chapter_title} / {line}",
                "items": [],
            }
            volumes.append(current)
            seen_volume_titles.add(normalized)
            continue
        if item_match and current is not None:
            title = _text(item_match.group(2), 600)
            if not title:
                continue
            metadata = _item_metadata(title)
            item_order = len(current["items"]) + 1
            format_item_id = f"{current['volumeId']}-{item_order:02d}"
            item = {
                "formatItemId": format_item_id,
                "title": title,
                "order": item_order,
                "required": not metadata["conditional"],
                "sourceLocator": (
                    f"{current['sourceLocator']} / {line}"
                )[:240],
                **metadata,
            }
            if item["itemType"] in {"fixed_form", "fixed_table"}:
                item["templateId"] = f"tpl-{format_item_id}"
            else:
                item["templateId"] = ""
            current["items"].append(item)
            continue

        # 目录条目通常是连续的编号行。遇到模板正文后停止继续把正文
        # 误判成卷内条目；空行和页码已经在 _clean_line 中过滤。
        if current and current["items"]:
            break

    if source_kind == "composition":
        # 组成条款必须同时具备多个部分和卷内文件项，才能作为目录骨架。
        # 否则很可能只是某个资格承诺/格式说明中的编号正文。
        item_count = sum(len(volume.get("items") or []) for volume in volumes)
        if len(volumes) < 2 or item_count < 2:
            warnings.append("明确组成锚点下未识别到足够的“部分—文件项”结构，已忽略为目录依据。")
            volumes = []

    for volume in volumes:
        if not volume["items"]:
            warnings.append(f"卷“{volume['title']}”未识别到卷内文件条目。")
        volume["formatItemIds"] = [
            str(item.get("formatItemId") or "")
            for item in volume["items"]
            if str(item.get("formatItemId") or "")
        ]
    if not volumes:
        warnings.append("未在“投标文件格式”章节中识别到卷标题。")
    return volumes, warnings


def parse_submission_format_tree(text: str) -> dict[str, Any]:
    """从完整文本中确定性提取“卷 → 文件条目”。

    该函数不判断卷是否必须独立提交。只要原文明确列出了组成结构，
    就记录为 ``confidence=explicit``；是否拆分导出仍由 ``mode`` 和原文
    的独立提交表述共同决定。
    """

    lines, start, end, chapter_title, source_kind = _extract_format_range(text)
    if start < 0:
        return normalize_submission_structure(
            {
                "mode": "unspecified",
                "confidence": "unknown",
                "volumes": [],
                "warnings": ["未定位到“投标文件格式”或“响应文件格式”章节。"],
            }
        )
    volumes, warnings = _parse_format_lines(
        lines, start, end, chapter_title, source_kind
    )
    source_text = "\n".join(lines[start : min(end, start + 24)])
    compact_text = _compact(source_text)
    mode = "unspecified"
    if any(term in compact_text for term in ("分别编制", "分别提交", "独立成册", "分册提交", "分别装订")):
        mode = "separate"
    elif any(term in compact_text for term in ("合并编制", "合并成册", "合并提交")):
        mode = "single"

    return normalize_submission_structure(
        {
            "mode": mode,
            "confidence": "explicit" if volumes else "unknown",
            "structureStatus": "valid" if volumes else "invalid",
            "sourceLocator": chapter_title,
            "volumes": volumes,
            "warnings": warnings,
        }
    )


def _normalize_item(
    item: Any,
    *,
    volume_id: str,
    order: int,
    used_ids: set[str],
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    title = _text(item.get("title") or item.get("name"), 600)
    if not title:
        return None
    format_item_id = _text(
        item.get("formatItemId") or item.get("id"),
        100,
    ) or f"{volume_id}-{order:02d}"
    format_item_id = _unique_id(format_item_id, used_ids, f"{volume_id}-{order:02d}")
    metadata = _item_metadata(title)
    item_type = _text(item.get("itemType") or item.get("type"), 60) or metadata["itemType"]
    response_type = _text(item.get("responseType"), 60) or metadata["responseType"]
    conditional = bool(item.get("conditional", metadata["conditional"]))
    template_type = _text(item.get("templateType"), 80) or metadata["templateType"]
    template_id = _text(item.get("templateId"), 100)
    if not template_id and item_type in {"fixed_form", "fixed_table"}:
        template_id = f"tpl-{format_item_id}"
    return {
        **item,
        "formatItemId": format_item_id,
        "title": title,
        "order": order,
        "required": bool(item.get("required", not conditional)),
        "conditional": conditional,
        "conditionText": _text(
            item.get("conditionText")
            or (
                "招标文件标注为条件提交项，满足条件时提交。"
                if conditional
                else ""
            ),
            300,
        ),
        "itemType": item_type,
        "responseType": response_type,
        "templateType": template_type,
        "templateId": template_id,
        "sourceLocator": _text(item.get("sourceLocator"), 240),
        "sourceSectionId": _text(item.get("sourceSectionId"), 100),
    }


def _required_outline(volumes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for volume in volumes:
        for item in volume.get("items") or []:
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "formatItemId": item.get("formatItemId", ""),
                    "volumeId": volume.get("volumeId", ""),
                    "title": item.get("title", ""),
                    "type": item.get("itemType", ""),
                    "responseType": item.get("responseType", ""),
                    "required": bool(item.get("required", True)),
                    "conditional": bool(item.get("conditional", False)),
                    "conditionText": item.get("conditionText", ""),
                    "sourceLocator": item.get("sourceLocator", ""),
                    "templateId": item.get("templateId", ""),
                    "templateType": item.get("templateType", ""),
                    "order": item.get("order", 0),
                }
            )
    return result


def normalize_submission_structure(value: Any) -> dict[str, Any]:
    """把新旧 submissionStructure 收敛为稳定结构。"""

    raw = value if isinstance(value, dict) else {}
    warnings = [
        _text(item, 300)
        for item in raw.get("warnings", [])
        if _text(item, 300)
    ] if isinstance(raw.get("warnings"), list) else []
    volumes: list[dict[str, Any]] = []
    used_volume_ids: set[str] = set()
    listed = raw.get("volumes") if isinstance(raw.get("volumes"), list) else []
    ordered = sorted(
        enumerate(listed),
        key=lambda entry: (
            entry[1].get("order")
            if isinstance(entry[1], dict)
            and isinstance(entry[1].get("order"), int)
            and entry[1]["order"] > 0
            else 10**9,
            entry[0],
        ),
    )
    for index, (_, item) in enumerate(ordered, start=1):
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title") or item.get("name"), 240)
        if not title:
            continue
        title_issue = _volume_title_issue(title)
        if title_issue:
            warnings.append(f"疑似非目录卷标题“{title}”，已忽略：{title_issue}。")
            continue
        volume_id = _text(item.get("volumeId"), 80) or _slug(title)
        volume_id = _unique_id(volume_id, used_volume_ids, f"volume-{index:02d}")
        raw_items = item.get("items") if isinstance(item.get("items"), list) else []
        item_ids = {
            _text(entry, 100)
            for entry in item.get("formatItemIds", [])
            if _text(entry, 100)
        } if isinstance(item.get("formatItemIds"), list) else set()
        normalized_items: list[dict[str, Any]] = []
        used_item_ids: set[str] = set()
        for item_index, raw_item in enumerate(raw_items, start=1):
            normalized_item = _normalize_item(
                raw_item,
                volume_id=volume_id,
                order=item_index,
                used_ids=used_item_ids,
            )
            if normalized_item is None:
                continue
            normalized_items.append(normalized_item)
            item_ids.add(normalized_item["formatItemId"])
        volumes.append(
            {
                "volumeId": volume_id,
                "title": title,
                "order": len(volumes) + 1,
                "required": bool(item.get("required", True)),
                "kind": _text(item.get("kind"), 60),
                "sourceLocator": _text(item.get("sourceLocator"), 240),
                "items": normalized_items,
                "formatItemIds": list(item_ids),
            }
        )

    mode = _text(raw.get("mode"), 40).lower() or "unspecified"
    if mode not in {"single", "separate", "unspecified"}:
        mode = "unspecified"
    confidence = _text(raw.get("confidence"), 40).lower() or "unknown"
    if confidence not in {"explicit", "inferred", "unknown"}:
        confidence = "unknown"
    locator = _text(raw.get("sourceLocator"), 240)
    structure_status = _text(raw.get("structureStatus"), 40).lower()
    if structure_status not in {"valid", "invalid", "unknown"}:
        structure_status = "valid" if volumes else "unknown"
    if confidence == "explicit" and not volumes:
        confidence = "unknown"
        structure_status = "invalid"
        warnings.append("明确格式结构未通过目录骨架校验，已降级为无可靠分卷。")
    if mode == "separate" and (confidence != "explicit" or not locator or len(volumes) < 2):
        warnings.append("独立分册缺少明确原文定位或完整卷清单，暂按逻辑卷处理，需人工核实。")
        mode = "unspecified"
    if mode == "single" and (confidence != "explicit" or not locator):
        mode = "unspecified"
    outline = raw.get("requiredOutline")
    if not isinstance(outline, list):
        outline = _required_outline(volumes)
    else:
        outline = [
            dict(item)
            for item in outline
            if isinstance(item, dict) and _text(item.get("title"), 600)
        ]
    return {
        "mode": mode,
        "sourceLocator": locator,
        "confidence": confidence,
        "structureStatus": structure_status,
        "hasExplicitFormat": bool(
            volumes and confidence == "explicit" and structure_status != "invalid"
        ),
        "volumes": volumes,
        "requiredOutline": outline,
        "warnings": _dedupe(warnings),
    }


def merge_submission_structures(
    authoritative: Any,
    supplement: Any,
) -> dict[str, Any]:
    """以本地确定性结构为主，合并 Pi 的属性补充。"""

    base = normalize_submission_structure(authoritative)
    extra = normalize_submission_structure(supplement)
    if not base["volumes"]:
        return extra
    result = deepcopy(base)
    extra_by_volume = {
        _normalized_title(volume.get("title")): volume
        for volume in extra["volumes"]
        if isinstance(volume, dict)
    }
    warnings = [*base["warnings"], *extra["warnings"]]
    for volume in result["volumes"]:
        match = extra_by_volume.get(_normalized_title(volume["title"]))
        if not match:
            continue
        if not volume.get("sourceLocator"):
            volume["sourceLocator"] = match.get("sourceLocator", "")
        extra_by_item = {
            _normalized_title(item.get("title")): item
            for item in match.get("items") or []
            if isinstance(item, dict)
        }
        for item in volume.get("items") or []:
            supplement_item = extra_by_item.get(_normalized_title(item["title"]))
            if not supplement_item:
                continue
            for key in (
                "itemType",
                "responseType",
                "conditional",
                "conditionText",
                "templateType",
                "sourceSectionId",
            ):
                if item.get(key) in ("", None, False) and supplement_item.get(key) not in ("", None):
                    item[key] = supplement_item[key]
            if not item.get("sourceLocator"):
                item["sourceLocator"] = supplement_item.get("sourceLocator", "")
        known_titles = {
            _normalized_title(item.get("title"))
            for item in volume.get("items") or []
            if isinstance(item, dict)
        }
        for supplement_item in match.get("items") or []:
            title = _normalized_title(supplement_item.get("title"))
            if not title or title in known_titles:
                continue
            if not _text(supplement_item.get("sourceLocator"), 240):
                warnings.append(
                    f"Pi 补充条目“{supplement_item.get('title', '')}”缺少原文定位，已忽略。"
                )
                continue
            normalized = _normalize_item(
                supplement_item,
                volume_id=volume["volumeId"],
                order=len(volume["items"]) + 1,
                used_ids={
                    _text(item.get("formatItemId"), 100)
                    for item in volume.get("items") or []
                    if _text(item.get("formatItemId"), 100)
                },
            )
            if normalized:
                normalized["source"] = "pi_supplement"
                volume["items"].append(normalized)
                known_titles.add(title)
                warnings.append(
                    f"Pi 补充了原文解析未覆盖的卷内条目“{normalized['title']}”，请人工核对来源。"
                )
    result["requiredOutline"] = _required_outline(result["volumes"])
    result["warnings"] = _dedupe(warnings)
    return result
