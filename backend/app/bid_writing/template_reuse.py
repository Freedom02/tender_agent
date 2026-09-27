# -*- coding: utf-8 -*-
"""模板复用：招标文件给了完整格式文书的，直接复制原文并填空。

适用章节（source 升级为 copied_template）：投标函/承诺书/身份证明/授权委托书/
保证金说明/声明/企业简介/开标一览表 等格式类文书。

只复制招标文件原文，绝不编造；空位用 organization + project.parsed 结构化字段
替换，替换不到的保留占位并记入 blanksToFill，提醒人工填写。
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

# sectionId -> 在招标文件里找"格式"章节时用的标题关键词
_TEMPLATE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "c-01": ("投标函", "响应函", "投标书", "响应书"),
    "c-02": ("承诺书", "承诺函", "承诺"),
    "c-03": ("法定代表人身份证明", "法人身份证明", "身份证明"),
    "c-04": ("授权委托书", "授权书", "委托书"),
    "c-05": ("保证金",),
    "c-06": ("声明",),
    "c-07": ("企业简介", "公司简介", "基本情况"),
    "p-01": ("开标一览", "报价明细", "分项报价", "报价表", "报价单"),
}

_PLAN_TEMPLATE_TYPES: dict[str, str] = {
    "c-01": "bid_letter",
    "c-02": "commitment",
    "c-03": "legal_representative",
    "c-04": "authorization",
    "c-05": "guarantee",
    "c-06": "declaration",
    "c-07": "other",
    "p-01": "price_schedule",
}

# 填空字段：label -> context 键（按 label 长度降序替换，避免前缀误吃）
_FILL_FIELDS: tuple[tuple[str, str], ...] = (
    ("统一社会信用代码", "creditCode"),
    ("法定代表人", "legalRepresentative"),
    ("法定代表", "legalRepresentative"),
    ("采购人名称", "tendererName"),
    ("招标人名称", "tendererName"),
    ("投标人名称", "companyName"),
    ("供应商名称", "companyName"),
    ("项目名称", "projectName"),
    ("项目编号", "projectCode"),
    ("注册地址", "registeredAddress"),
    ("采购人", "tendererName"),
    ("招标人", "tendererName"),
    ("投标人", "companyName"),
    ("供应商", "companyName"),
    ("注册地", "registeredAddress"),
    ("联系电话", "phone"),
    ("联系地址", "address"),
    ("开户银行", "bankName"),
    ("银行账号", "bankAccount"),
    ("被授权人", "authorizedPerson"),
    ("投标报价", "bidPrice"),
    ("报价金额", "bidPrice"),
    ("服务期限", "servicePeriod"),
    ("服务地点", "serviceLocation"),
)


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _canonical_title(text: Any) -> str:
    title = _norm(str(text or "")).strip("：:;；。.")
    title = re.sub(
        r"^(?:第[一二三四五六七八九十百千万零〇\d]+[篇章节部分])",
        "",
        title,
    )
    title = re.sub(
        r"^(?:[（(][一二三四五六七八九十百千万零〇\d]+[）)]|[一二三四五六七八九十百千万零〇\d]+[、.．)])",
        "",
        title,
    )
    return re.sub(r"(?:[（(]?(?:格式|模板|范本|样式)[）)]?)$", "", title).strip("：:;；。.")


def _build_context(project: dict, organization: dict | None, company: dict | None) -> dict[str, str]:
    org = organization if isinstance(organization, dict) else {}
    profile = company if isinstance(company, dict) else {}
    project = project if isinstance(project, dict) else {}
    parsed = project.get("parsed") if isinstance(project.get("parsed"), dict) else {}

    def pick(*vals: Any) -> str:
        for v in vals:
            if isinstance(v, (list, tuple)):
                v = "、".join(str(x).strip() for x in v if str(x).strip())
            if v not in (None, ""):
                return str(v).strip()
        return ""

    return {
        "projectName": pick(parsed.get("projectName"), project.get("title")),
        "projectCode": pick(parsed.get("projectCode")),
        "tendererName": pick(parsed.get("tendererName")),
        "companyName": pick(org.get("organizationName"), profile.get("company")),
        "legalRepresentative": pick(org.get("legalRepresentative"), profile.get("legalRepresentative")),
        "creditCode": pick(org.get("unifiedSocialCreditCode"), profile.get("unifiedSocialCreditCode")),
        "registeredAddress": pick(org.get("registeredAddress"), profile.get("registeredAddress")),
        "phone": pick(org.get("phone"), org.get("contactPhone"), profile.get("phone")),
        "address": pick(org.get("address"), org.get("registeredAddress"), profile.get("address")),
        "bankName": pick(org.get("bankName"), profile.get("bankName")),
        "bankAccount": pick(org.get("bankAccount"), profile.get("bankAccount")),
        "authorizedPerson": pick(org.get("authorizedPerson"), profile.get("authorizedPerson")),
        # Tender-side budget and delivery requirements are not bidder commitments.
        "bidPrice": "",
        "servicePeriod": "",
        "serviceLocation": "",
        "date": "",
    }


def find_template_section(sections: list[Any], section_id: str) -> dict[str, Any] | None:
    """在招标文件章节里找给定章节的"格式"模板（标题命中关键词 + 含格式/附件/模板/范本）。"""
    keywords = _TEMPLATE_KEYWORDS.get(section_id, ())
    if not keywords:
        return None
    exact_candidates: list[dict[str, Any]] = []
    marked_candidates: list[dict[str, Any]] = []
    fallback_candidates: list[dict[str, Any]] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        sec_title = _norm(str(section.get("heading") or section.get("title") or ""))
        if not sec_title:
            continue
        if not any(_norm(kw) in sec_title for kw in keywords):
            continue
        if any(_canonical_title(sec_title) == _canonical_title(keyword) for keyword in keywords):
            exact_candidates.append(section)
        if any(term in sec_title for term in ("格式", "附件", "模板", "附表", "范本", "样式")):
            marked_candidates.append(section)
        text = str(section.get("text") or "")
        if re.search(r"[_＿]{2,}|【待填写|投标人名称|项目名称|签字|盖章", text):
            fallback_candidates.append(section)
    candidates = exact_candidates or marked_candidates
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        # A repeated title without a stable template ID is ambiguous.
        return None
    if len(fallback_candidates) == 1:
        return fallback_candidates[0]
    return None


def _source_reference(
    source: dict[str, Any],
    *,
    template_id: str = "",
    template_type: str = "",
    title: str = "",
) -> dict[str, Any]:
    reference: dict[str, Any] = {}
    for key in (
        "sectionId",
        "segmentId",
        "segmentIds",
        "sourceLocator",
        "sectionPath",
        "pageStart",
        "pageEnd",
        "tableIds",
    ):
        value = source.get(key)
        if value not in (None, "", [], {}):
            reference[key] = value
    if template_id:
        reference["templateId"] = template_id
    if template_type:
        reference["templateType"] = template_type
    if title:
        reference["title"] = title
    return reference


def _template_ref_from_index(section_id: str, template_index: list[Any] | None) -> dict[str, Any] | None:
    expected_type = _PLAN_TEMPLATE_TYPES.get(section_id, "")
    candidates = [
        item
        for item in (template_index or [])
        if isinstance(item, dict)
        and (not expected_type or str(item.get("templateType") or "") == expected_type)
        and isinstance(item.get("source"), dict)
    ]
    if not candidates:
        return None
    item = sorted(
        candidates,
        key=lambda value: (
            (value.get("source") or {}).get("pageStart", 10**9),
            (value.get("source") or {}).get("sourceLocator", ""),
        ),
    )[0]
    return _source_reference(
        item["source"],
        template_id=str(item.get("templateId") or item.get("id") or ""),
        template_type=str(item.get("templateType") or expected_type),
        title=str(item.get("title") or ""),
    )


def _template_ref_from_section(template_section: dict[str, Any], section_id: str) -> dict[str, Any]:
    return _source_reference(
        {
            "sectionId": template_section.get("sectionId") or template_section.get("id"),
            "segmentIds": template_section.get("segmentIds") or [],
            "sourceLocator": template_section.get("startLocator") or template_section.get("sourceLocator"),
            "sectionPath": template_section.get("sectionPath") or [],
        },
        template_id=f"source-{section_id}",
        template_type=_PLAN_TEMPLATE_TYPES.get(section_id, "other"),
        title=str(template_section.get("heading") or template_section.get("title") or ""),
    )


def attach_templates(
    plan: list[Any],
    sections: list[Any],
    template_index: list[Any] | None = None,
) -> list[Any]:
    """给计划章节挂原文模板定位，不在目录阶段复制模板正文。"""
    for section in plan:
        if not isinstance(section, dict):
            continue
        section_id = str(section.get("sectionId") or "")
        if section_id not in _TEMPLATE_KEYWORDS:
            continue
        template_ref = _template_ref_from_index(section_id, template_index)
        template_section = None
        if template_ref is None:
            template_section = find_template_section(sections, section_id)
            if template_section:
                template_ref = _template_ref_from_section(template_section, section_id)
        if not template_ref:
            continue
        section["source"] = "copied_template"
        section["templateOrigin"] = str(
            template_ref.get("sourceLocator")
            or template_ref.get("title")
            or (template_section or {}).get("sourceLocator")
            or ""
        )
        section["templateRef"] = template_ref
        section.pop("templateText", None)
    return plan


def _resolve_template_text(
    template_ref: dict[str, Any],
    sections: list[Any] | None,
    segments: list[Any] | None,
) -> str:
    source = template_ref.get("source") if isinstance(template_ref.get("source"), dict) else template_ref
    section_id = str(source.get("sectionId") or "")
    section = next(
        (
            item
            for item in (sections or [])
            if isinstance(item, dict)
            and str(item.get("sectionId") or item.get("id") or "") == section_id
        ),
        None,
    )
    if section and str(section.get("text") or "").strip():
        return str(section["text"]).strip()
    wanted_ids = [
        str(value)
        for value in (source.get("segmentIds") or [source.get("segmentId")])
        if str(value)
    ]
    segment_map = {
        str(item.get("id")): item
        for item in (segments or [])
        if isinstance(item, dict) and str(item.get("id") or "")
    }
    return "\n".join(
        str(segment_map[item_id].get("text") or "").strip()
        for item_id in wanted_ids
        if item_id in segment_map and str(segment_map[item_id].get("text") or "").strip()
    ).strip()


def fill_section_template(
    section: dict,
    project: dict,
    organization: dict | None,
    company: dict | None,
    *,
    fragment: dict[str, Any] | None = None,
    agent_result: dict[str, Any] | None = None,
    sections: list[Any] | None = None,
    segments: list[Any] | None = None,
) -> dict:
    template_ref = section.get("templateRef") if isinstance(section.get("templateRef"), dict) else {}
    if isinstance(fragment, dict) and fragment:
        return fill_template_fragment(
            section,
            fragment,
            project,
            organization,
            company,
            agent_result=agent_result,
        )
    text = str(section.get("templateText") or "").strip()
    if not text and template_ref:
        text = _resolve_template_text(template_ref, sections, segments)
    if not text:
        return {"content": "", "blanksToFill": ["模板原文缺失"], "status": "missing"}
    filled = fill_template_text(text, project, organization, company)
    return {"content": filled["content"], "blanksToFill": filled["blanksToFill"], "status": "generated"}


def _table_rows(table: dict[str, Any]) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in table.get("rows") or []:
        if not isinstance(row, dict):
            continue
        cells: list[str] = []
        for cell in row.get("cells") or []:
            if not isinstance(cell, dict):
                continue
            cells.append(str(cell.get("text") or cell.get("value") or "").strip())
        if any(cells):
            rows.append(cells)
    return rows


def _table_markdown(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(cell.replace("|", r"\|") for cell in normalized[0]) + " |",
        "| " + " | ".join("---" for _ in range(width)) + " |",
    ]
    lines.extend(
        "| " + " | ".join(cell.replace("|", r"\|") for cell in row) + " |"
        for row in normalized[1:]
    )
    return "\n".join(lines)


def source_table_for_section(
    section: dict[str, Any],
    tables: list[Any] | None,
    document_blocks: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """只选择结构或紧邻标题能够确认属于当前格式项的原始表格。"""

    title = _canonical_title(section.get("title"))
    if not title:
        return None
    source_refs = section.get("templateRef")
    source_refs = source_refs if isinstance(source_refs, dict) else {}
    explicit_table_ids = {
        str(value).strip()
        for value in (
            section.get("tableIds")
            or section.get("templateTableIds")
            or source_refs.get("tableIds")
            or []
        )
        if str(value).strip()
    }
    blocks = document_blocks if isinstance(document_blocks, dict) else {}
    source_blocks = next(
        (
            blocks.get(key)
            for key in ("blocks", "units", "segments")
            if isinstance(blocks.get(key), list)
        ),
        [],
    )
    preceding_titles: dict[str, str] = {}
    if isinstance(source_blocks, list):
        paragraph_blocks = [
            block
            for block in source_blocks
            if isinstance(block, dict)
            and isinstance(block.get("bodyChildIndex"), int)
            and not str(block.get("contentType") or "").startswith("table")
            and _canonical_title(block.get("text") or block.get("heading"))
        ]
        for table in tables or []:
            if not isinstance(table, dict) or not isinstance(table.get("bodyChildIndex"), int):
                continue
            table_index = int(table["bodyChildIndex"])
            previous = [
                block
                for block in paragraph_blocks
                if int(block["bodyChildIndex"]) < table_index
            ]
            if previous:
                nearest_index = max(int(block["bodyChildIndex"]) for block in previous)
                nearest = [
                    block for block in previous
                    if int(block["bodyChildIndex"]) == nearest_index
                ]
                if len(nearest) == 1:
                    preceding_titles[
                        str(table.get("tableId") or table.get("id") or "")
                    ] = _canonical_title(nearest[0].get("text") or nearest[0].get("heading"))

    is_technical_service_response = (
        ("技术" in title or "服务" in title or "实施" in title)
        and ("响应" in title or "应答" in title)
        and not any(term in title for term in ("偏离", "差异", "商务", "报价"))
    )
    keywords: tuple[tuple[str, ...], ...]
    if is_technical_service_response:
        keywords = ()
    elif "开标一览" in title:
        keywords = (("投标人名称", "投标报价"), ("包号及名称", "最高限价"))
    elif "分项报价" in title or "报价明细" in title:
        keywords = (("商品名称", "综合单价"), ("序号", "单价", "合计"))
    elif "技术" in title and ("差异" in title or "偏离" in title):
        keywords = (("招标要求", "投标应答"), ("技术参数",))
    elif "商务" in title and ("差异" in title or "偏离" in title):
        keywords = (("招标商务要求", "投标商务应答"), ("商务要求",))
    else:
        keywords = ()

    candidates: list[tuple[int, dict[str, Any]]] = []
    for table in tables or []:
        if not isinstance(table, dict):
            continue
        table_id = str(table.get("tableId") or table.get("id") or "").strip()
        table_title = _canonical_title(
            table.get("title") or table.get("heading") or table.get("name")
        )
        if explicit_table_ids and table_id in explicit_table_ids:
            candidates.append((1000, table))
            continue
        if table_title and table_title == title:
            candidates.append((500, table))
            continue
        if preceding_titles.get(table_id) == title:
            candidates.append((400, table))
            continue
        rows = _table_rows(table)
        if not rows:
            continue
        header_text = _norm(" ".join(" ".join(row) for row in rows[:4]))
        if is_technical_service_response:
            requirement_headers = (
                "招标文件要求",
                "招标要求",
                "采购要求",
                "技术要求",
                "服务要求",
                "技术参数",
                "技术指标",
                "规格要求",
                "需求内容",
                "项目需求",
                "条款要求",
                "要求内容",
            )
            response_headers = (
                "投标人响应",
                "投标响应",
                "投标应答",
                "供应商响应",
                "响应内容",
                "响应情况",
                "响应说明",
                "应答内容",
                "应答情况",
                "偏离说明",
            )
            has_requirement_column = any(
                _norm(keyword) in header_text for keyword in requirement_headers
            )
            has_response_column = any(
                _norm(keyword) in header_text for keyword in response_headers
            )
            if has_requirement_column and has_response_column:
                technical_service_terms = (
                    "技术",
                    "参数",
                    "规格",
                    "服务",
                    "实施",
                    "培训",
                    "售后",
                )
                relevance = sum(
                    1 for keyword in technical_service_terms if _norm(keyword) in header_text
                )
                candidates.append((30 + relevance, table))
            continue

        if not keywords:
            continue
        score = 0
        for group_index, group in enumerate(keywords):
            hits = sum(1 for keyword in group if _norm(keyword) in header_text)
            if hits:
                score = max(score, hits * 10 - group_index)
        if score:
            candidates.append((score, table))
    if not candidates:
        return None
    best_score = max(score for score, _ in candidates)
    best = [table for score, table in candidates if score == best_score]
    return best[0] if len(best) == 1 else None


def fill_format_item_without_template(
    section: dict[str, Any],
    project: dict[str, Any],
    organization: dict[str, Any] | None,
    company: dict[str, Any] | None,
    *,
    tables: list[Any] | None = None,
    document_blocks: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """为没有提取到 Word 模板片段的固定格式条目提供可编辑底稿。

    匹配评估复用场景通常只有结构化原始表格，没有 templateFragments。
    这里优先复制命中的原表格；找不到时生成明确的人工填写骨架，避免
    “请求成功但章节正文为空”。
    """

    title = str(section.get("title") or "未命名格式条目").strip()
    response_type = str(section.get("responseType") or "").strip()
    item_type = str(section.get("itemType") or "").strip()
    table = source_table_for_section(section, tables, document_blocks)
    if table:
        rows = _table_rows(table)
        content = f"# {title}\n\n{_table_markdown(rows)}"
        return {
            "content": content,
            "blanksToFill": ["表格中的空白响应项需结合企业资料和最终报价补充"],
            "status": "missing",
            "templateFillStatus": "source_table",
            "filledTables": [table],
        }

    context = _build_context(project, organization, company)
    if response_type == "table" or item_type == "fixed_table":
        content = (
            f"# {title}\n\n"
            "| 序号 | 招标文件要求 | 投标应答 | 备注 |\n"
            "| --- | --- | --- | --- |\n"
            "| 1 | 【按招标文件原表填写】 | 【需人工填写】 | 【需人工填写】 |"
        )
        blanks = ["未提取到招标文件原始表格，请按原表核对并补充"]
    else:
        content = (
            f"# {title}\n\n"
            f"项目名称：{context.get('projectName') or '【需人工填写】'}\n\n"
            f"投标人名称：{context.get('companyName') or '【需人工填写】'}\n\n"
            "【请依据招标文件固定格式补充正文、签字并盖章】"
        )
        blanks = ["未提取到招标文件固定格式原文，请按原格式核对并补充"]
    return {
        "content": content,
        "blanksToFill": blanks,
        "status": "missing",
        "templateFillStatus": "scaffold",
        "filledTables": [],
    }


def _replace_field_markers(text: str, label: str, value: str) -> str:
    if not label or not value:
        return text
    escaped = re.escape(label)
    content = str(text or "")
    content = re.sub(r"\{\{\s*" + escaped + r"\s*\}\}", value, content)
    content = re.sub(r"\[\[\s*" + escaped + r"\s*\]\]", value, content)
    content = re.sub(
        rf"{escaped}\s*[:：]\s*[（(][^）)]*填写[^）)]*[）)]",
        f"{label}：{value}",
        content,
    )
    content = re.sub(
        rf"[（(]\s*(?:由?投标人)?填写[“”\"']?{escaped}[“”\"']?\s*[）)]",
        f"（{value}）",
        content,
    )
    content = re.sub(rf"[（(]\s*{escaped}\s*[）)]", f"（{value}）", content)
    content = re.sub(
        rf"{escaped}\s*[:：]\s*[_＿\u3000 ]+",
        f"{label}：{value}",
        content,
    )
    content = re.sub(rf"{escaped}\s*[_＿]{{1,}}", f"{label}{value}", content)
    return content


def _agent_field_values(agent_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = agent_result if isinstance(agent_result, dict) else {}
    values = result.get("fieldValues") or result.get("field_values") or []
    return [item for item in values if isinstance(item, dict)]


def _agent_cell_values(agent_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    result = agent_result if isinstance(agent_result, dict) else {}
    values = result.get("cellValues") or result.get("cell_values") or []
    return [item for item in values if isinstance(item, dict)]


def _apply_agent_fields(
    text: str,
    field_values: list[dict[str, Any]],
) -> str:
    result = str(text or "")
    for field in field_values:
        status = str(field.get("status") or "filled")
        label = str(
            field.get("label")
            or field.get("fieldName")
            or field.get("name")
            or ""
        ).strip()
        value = str(field.get("value") or "").strip()
        if status in {"locked", "missing"} or not label or not value:
            continue
        result = _replace_field_markers(result, label, value)
    return result


def _filled_template_blocks(
    blocks: list[Any],
    fields: list[dict[str, Any]],
    project: dict,
    organization: dict | None,
    company: dict | None,
) -> list[dict[str, Any]]:
    """返回供正文页渲染的模板段落，保留原始段落样式和定位字段。"""

    filled: list[dict[str, Any]] = []
    for raw_block in blocks:
        if not isinstance(raw_block, dict):
            continue
        block = deepcopy(raw_block)
        text = str(block.get("text") or "")
        if text:
            text = _apply_agent_fields(text, fields)
            text = fill_template_text(
                text,
                project,
                organization,
                company,
            )["content"]
            block["text"] = text
        filled.append(block)
    return filled


def _fill_fragment_tables(
    fragment: dict[str, Any],
    cell_values: list[dict[str, Any]],
    field_values: list[dict[str, Any]],
    known_values: set[str],
) -> list[dict[str, Any]]:
    tables = deepcopy(fragment.get("tables") or [])
    cell_map = {
        (
            str(item.get("tableId") or ""),
            str(item.get("rowIndex") or ""),
            str(item.get("cellIndex") or ""),
        ): item
        for item in cell_values
        if isinstance(item, dict)
    }
    for table in tables:
        if not isinstance(table, dict):
            continue
        table_id = str(table.get("tableId") or table.get("id") or "")
        for row in table.get("rows") or []:
            if not isinstance(row, dict):
                continue
            row_index = str(row.get("rowIndex") or "")
            for cell in row.get("cells") or []:
                if not isinstance(cell, dict):
                    continue
                cell_index = str(cell.get("cellIndex") or "")
                original = str(cell.get("text") or "")
                filled = _apply_agent_fields(original, field_values)
                agent_cell = cell_map.get((table_id, row_index, cell_index))
                if agent_cell and str(agent_cell.get("status") or "filled") not in {"locked", "missing"}:
                    value = str(agent_cell.get("value") or "").strip()
                    if value and value in known_values and agent_cell.get("sourceRefs"):
                        label = str(agent_cell.get("label") or "").strip()
                        filled = _replace_field_markers(filled, label, value) if label else value
                cell["filledText"] = filled
    return tables


def _tables_to_markdown(tables: list[dict[str, Any]]) -> str:
    rendered: list[str] = []
    for table in tables:
        if not isinstance(table, dict):
            continue
        rows = table.get("rows") or []
        rows = [row for row in rows if isinstance(row, dict)]
        if not rows:
            continue
        matrix: list[list[str]] = []
        for row in rows:
            cells = [
                str(cell.get("filledText") or cell.get("text") or "").replace("|", r"\|")
                for cell in (row.get("cells") or [])
                if isinstance(cell, dict)
            ]
            if cells:
                matrix.append(cells)
        if not matrix:
            continue
        width = max(len(row) for row in matrix)
        matrix = [row + [""] * (width - len(row)) for row in matrix]
        rendered.append("| " + " | ".join(matrix[0]) + " |")
        rendered.append("| " + " | ".join("---" for _ in range(width)) + " |")
        rendered.extend("| " + " | ".join(row) + " |" for row in matrix[1:])
    return "\n".join(rendered)


def fill_template_fragment(
    section: dict[str, Any],
    fragment: dict[str, Any],
    project: dict,
    organization: dict | None,
    company: dict | None,
    *,
    agent_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """填充模板片段的可编辑字段，锁定块和未识别字段保持原样。"""

    template_text = str(
        fragment.get("templateText")
        or section.get("templateText")
        or ""
    ).strip()
    if not template_text:
        return {
            "content": "",
            "blanksToFill": ["模板片段正文缺失"],
            "status": "missing",
            "templateFillStatus": "missing",
            "filledTables": [],
        }

    context = _build_context(project, organization, company)
    known_values = {value for value in context.values() if value}
    fields_by_key: dict[str, dict[str, Any]] = {}
    for field in fragment.get("editableFields") or []:
        if not isinstance(field, dict):
            continue
        field_id = str(field.get("fieldId") or field.get("id") or "").strip()
        label = str(
            field.get("label")
            or field.get("fieldName")
            or field.get("name")
            or ""
        ).strip()
        source_key = str(field.get("sourceKey") or "").strip()
        confirmation_required = bool(
            field.get("requiresConfirmation")
            or source_key in {"bidPrice", "date", "servicePeriod", "serviceLocation"}
            or any(
                term in label
                for term in ("报价", "服务期限", "服务地点", "工期", "交付期限", "承诺")
            )
        )
        value = "" if confirmation_required else str(context.get(source_key) or "").strip()
        status = (
            "filled"
            if value
            else "needs_confirmation"
            if confirmation_required
            else "missing"
        )
        fields_by_key[field_id or label] = {
            **field,
            "fieldId": field_id,
            "label": label,
            "sourceKey": source_key,
            "value": value,
            "status": status,
            "sourceRefs": (
                [{"factKey": source_key, "name": label, "source": "project_or_m3"}]
                if value and source_key
                else []
            ),
            "requiresConfirmation": confirmation_required,
        }
    for field in _agent_field_values(agent_result):
        key = str(field.get("fieldId") or field.get("label") or "").strip()
        label = str(
            field.get("label")
            or field.get("fieldName")
            or field.get("name")
            or ""
        ).strip()
        value = str(field.get("value") or "").strip()
        source_refs = field.get("sourceRefs")
        if (
            not key
            or not value
            or not isinstance(source_refs, list)
            or not source_refs
        ):
            continue
        existing_key = next(
            (
                field_key
                for field_key, existing in fields_by_key.items()
                if (
                    (key and str(existing.get("fieldId") or "") == key)
                    or (label and str(existing.get("label") or "").strip() == label)
                )
            ),
            "",
        )
        if not existing_key:
            continue
        existing = fields_by_key[existing_key]
        if existing.get("requiresConfirmation") or existing.get("editable") is False:
            continue
        existing.update(
            {
                "value": value,
                "status": "filled",
                "sourceRefs": source_refs,
            }
        )
    fields = list(fields_by_key.values())
    content = _apply_agent_fields(template_text, fields)
    content = fill_template_text(content, project, organization, company)["content"]
    tables = _fill_fragment_tables(
        fragment,
        _agent_cell_values(agent_result),
        fields,
        known_values,
    )
    visual_blocks = _filled_template_blocks(
        _as_list(fragment.get("blocks")),
        fields,
        project,
        organization,
        company,
    )
    table_markdown = _tables_to_markdown(tables)
    if table_markdown and table_markdown not in content:
        content = f"{content}\n\n{table_markdown}".strip()

    blanks = list(fill_template_text(content, project, organization, company)["blanksToFill"])
    sensitive_blanks: list[str] = []
    for field in fields:
        if not field.get("required", True) or field.get("value"):
            continue
        label = str(field.get("label") or field.get("fieldName") or "").strip()
        if label:
            if label not in blanks:
                blanks.append(label)
            if field.get("requiresConfirmation"):
                sensitive_blanks.append(label)
    agent_blanks = (
        (agent_result or {}).get("blanksToFill", [])
        if isinstance(agent_result, dict)
        else []
    )
    for item in agent_blanks:
        value = str(item).strip()
        if value and value not in blanks:
            blanks.append(value)
    warnings = [
        str(item).strip()
        for item in (agent_result or {}).get("warnings", [])
        if str(item).strip()
    ] if isinstance(agent_result, dict) and isinstance((agent_result or {}).get("warnings"), list) else []
    if sensitive_blanks:
        warnings.append(
            "报价、履约承诺及签署日期未自动代填，请由投标人核实并确认。"
        )
    return {
        "content": content,
        "blanksToFill": blanks,
        "status": "generated" if content else "missing",
        "contentMatchesTemplate": True,
        "templateFillStatus": (
            "needs_confirmation"
            if sensitive_blanks
            else str((agent_result or {}).get("status") or "completed")
        ),
        "templateFieldValues": fields,
        "templateCellValues": _agent_cell_values(agent_result),
        "templateBlocks": visual_blocks,
        "templateTables": tables,
        "templateStyle": fragment.get("style") if isinstance(fragment.get("style"), dict) else {},
        "filledTables": tables,
        "templateFillAgentResults": (agent_result or {}).get("agentResults", [])
        if isinstance(agent_result, dict)
        else [],
        "templateFillWarnings": list(dict.fromkeys(warnings)),
    }


def fill_template_text(text: str, project: dict, organization: dict | None, company: dict | None) -> dict:
    ctx = _build_context(project, organization, company)
    content = text

    for label, ctx_key in sorted(_FILL_FIELDS, key=lambda p: -len(p[0])):
        value = ctx.get(ctx_key)
        if not value:
            continue
        # 项目名称：（由投标人填写）/ 投标人：（填写“全称”）
        content = re.sub(
            rf"{re.escape(label)}\s*[:：]\s*[（(][^）)]*填写[^）)]*[）)]",
            f"{label}：{value}",
            content,
        )
        # （填写“项目名称”）/ (填写"项目编号")
        content = re.sub(
            rf"[（(]\s*(?:由?投标人)?填写[“”\"']?{re.escape(label)}[“”\"']?\s*[）)]",
            f"（{value}）",
            content,
        )
        # （label）/ (label)
        content = re.sub(rf"[（(]\s*{re.escape(label)}\s*[）)]", f"（{value}）", content)
        # label：____ 或 label：　　 （冒号后仅空白/下划线）
        content = re.sub(rf"{re.escape(label)}\s*[:：]\s*[_＿\u3000 ]+", f"{label}：{value}", content)
        # label____ / label＿＿
        content = re.sub(rf"{re.escape(label)}\s*[_＿]{{1,}}", f"{label}{value}", content)

    return {"content": content, "blanksToFill": _detect_blanks(content)}


def _detect_blanks(content: str) -> list[str]:
    blanks: list[str] = []
    if re.search(r"[_＿]{2,}", content):
        blanks.append("下划线空位")
    if re.search(r"【(?:待填写|需人工填写|需确认)[^】]*】", content):
        blanks.append("【待填写】字段")
    if re.search(r"[_＿]{1,}\s*年\s*[_＿]{1,}\s*月\s*[_＿]{1,}\s*日", content):
        blanks.append("落款日期")
    for label in ("投标报价", "报价金额", "大写金额", "开户银行", "银行账号", "被授权人", "签字", "盖章"):
        if (
            re.search(rf"{re.escape(label)}\s*[:：]?\s*[_＿（）()]+", content)
            and label not in blanks
        ):
            blanks.append(label)
    return blanks
