# -*- coding: utf-8 -*-
"""技术标书逐条响应子模块（防幻觉核心）。

对 planning.py 产出的 plan 中 kind=="technical" 的章节（t-01..t-07）生成正文，
逐条依据「招标要求原文 + 命中知识库对象」响应，每段带 sourceRefs 溯源，
绝不凭空编造参数/业绩/人员/证书。

逐条响应 6 步流程（见 generate_technical_sections）：
  1. 遍历 plan，筛出 kind == SECTION_KIND_TECHNICAL 的章节，读取其 requirementIds。
  2. 对每条 requirementId 到 fine_review["rows"] 反查 comparison row，
     取 row["requirement"](原文) + row["evidence"](证据摘要) + row["matchedObjectIds"]。
  3. 用 matchedObjectIds 到 knowledge 各列表（products/capabilities/parameters/
     credentials/qualifications/projectReferences/personnel/financialRecords）反查记录，
     拼成「可用企业资料」上下文。
  4. 调 call_openai_compatible 逐条生成响应段落（system 提示只依据资料作答）。
  5. 命中知识对象 → 生成段落 + sourceRefs（build_source_ref）；资料缺失 → 输出
     “【缺失：需人工补充】”占位。
  6. t-01/t-04/t-06/t-07 这类 requirementIds 为空的章节：基于 project 标题/范围 +
     knowledge 里 products/capabilities 生成通用章节（同样只依据资料），带 sourceRefs。

防幻觉三层约束：
  1. 输入白名单：prompt 只注入「要求原文 + 该条 matchedObjectIds 命中的知识对象」，
     不注入 fineReview 其它行、不注入招标全文，避免模型照抄/联想无关内容。
  2. 提示硬约束：system 要求“只依据【招标要求】/【项目信息】与【企业资料】作答；
     未覆盖项写 __MISSING__ 并说明缺什么；严禁编造参数/业绩/人员/证书”。
  3. 输出溯源：命中对象必须回填 sourceRefs（build_source_ref），缺失对象输出
     “【缺失：需人工补充】”占位；LLM 不可用（ai 为 None 或 success=False）时回退为
     “evidence 摘要 + 知识库记录描述”的确定性拼装，并在 content 末尾标注
     “[LLM 不可用，已按资料库摘要生成，需人工复核]”。

Token 约束：每个 section 独立调用、每条要求独立调用，绝不一次全量；正文/要求原文
在拼入 prompt 前按长度截断，避免单次调用超限。
"""
from __future__ import annotations

from typing import Any

from ..tender_matching.eligibility import call_openai_compatible
from .schemas import SECTION_KIND_TECHNICAL, build_source_ref

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# 无 requirementIds 的通用技术章节 → 生成主题（只依据项目信息 + 企业产品/能力）
_GENERIC_SECTION_THEME: dict[str, str] = {
    "t-01": "项目理解",
    "t-04": "质量保证措施",
    "t-06": "培训方案",
    "t-07": "应急保障方案",
}

_MISSING_PLACEHOLDER = "【缺失：需人工补充】"
_FALLBACK_NOTE = "[LLM 不可用，已按资料库摘要生成，需人工复核]"

# 单次 LLM 调用的输出上限（逐条/逐章节调用，避免一次全量导致 token 超限）
_MAX_TOKENS_REQUIREMENT = 800
_MAX_TOKENS_GENERIC = 1200

# 要求原文 / 证据摘要拼入 prompt 前的截断长度（防超限，且不损失溯源信息）
_REQUIREMENT_TEXT_LIMIT = 1200
_EVIDENCE_LIMIT = 600

# 通用章节最多带入的企业资料对象条数
_GENERIC_RECORDS_LIMIT = 16

_SYSTEM_PROMPT = (
    "你是技术标书撰写助手。你只依据下面提供的【招标要求】和【企业资料】作答，"
    "不得使用任何外部知识或经验补充内容。资料未覆盖的项，明确写出 __MISSING__ "
    "并说明缺什么；严禁编造任何参数、业绩、人员、证书、数字或案例。"
    "只输出该条要求的响应段落（Markdown 正文），不要输出标题或解释。"
)

_GENERIC_SYSTEM_PROMPT = (
    "你是技术标书撰写助手。你只依据下面提供的【项目信息】和【企业资料】撰写通用章节，"
    "不得使用任何外部知识或经验补充内容。资料未覆盖的项，明确写出 __MISSING__ "
    "并说明缺什么；严禁编造任何参数、业绩、人员、证书、数字或案例。"
    "只输出该章节正文（Markdown），不要输出章节标题。"
)


# ---------------------------------------------------------------------------
# 基础工具（纯读、确定性）
# ---------------------------------------------------------------------------

def _clip(value: Any, limit: int) -> str:
    """把文本压到 limit 字符内，超长时保留头尾，避免单次调用 token 超限。"""
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.72))
    tail = max(1, limit - head)
    return f"{text[:head]}\n…[原文中间已压缩]…\n{text[-tail:]}"


def _rows(fine_review: dict[str, Any]) -> list[dict[str, Any]]:
    """提取 fineReview.rows 中所有 dict 行。"""
    if not isinstance(fine_review, dict):
        return []
    rows = fine_review.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _requirement_ids(section: dict[str, Any]) -> list[str]:
    """读取并去重 section 的 requirementIds（保持顺序）。"""
    ids = section.get("requirementIds")
    if not isinstance(ids, list):
        return []
    result: list[str] = []
    for rid in ids:
        value = str(rid or "").strip()
        if value and value not in result:
            result.append(value)
    return result


def _row_by_id(rows: list[dict[str, Any]], requirement_id: str) -> dict[str, Any] | None:
    """按 requirementId 反查 comparison row（兼容 requirementId 或 id 字段）。"""
    target = str(requirement_id or "").strip()
    if not target:
        return None
    for row in rows:
        rid = str(row.get("requirementId") or row.get("id") or "").strip()
        if rid == target:
            return row
    return None


def _index_knowledge(knowledge: dict[str, Any]) -> dict[str, tuple[dict[str, Any], str]]:
    """把 knowledge 各列表记录按 id 建索引 → {id: (record, 列表键)}。

    build_eligibility_knowledge 的记录含 id/name/description/evidencePath/factKey，
    本索引把 row["matchedObjectIds"] 里的 id 反查回具体记录与其来源列表键。
    遍历所有“值为 dict 列表”的键，兼容 products/capabilities/parameters/credentials 等
    任意键名（parameters 在部分口径下可能独立存在）。
    """
    index: dict[str, tuple[dict[str, Any], str]] = {}
    if not isinstance(knowledge, dict):
        return index
    for list_key, value in knowledge.items():
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            record_id = str(item.get("id") or "").strip()
            if record_id and record_id not in index:
                index[record_id] = (item, str(list_key))
    return index


def _resolve_matched_objects(
    matched_object_ids: Any,
    knowledge_index: dict[str, tuple[dict[str, Any], str]],
) -> list[tuple[dict[str, Any], str]]:
    """把 row 的 matchedObjectIds 反查为 (record, 列表键) 列表，去重保序。"""
    resolved: list[tuple[dict[str, Any], str]] = []
    if not isinstance(matched_object_ids, (list, tuple)):
        return resolved
    seen: set[str] = set()
    for object_id in matched_object_ids:
        key = str(object_id or "").strip()
        if not key or key in seen:
            continue
        hit = knowledge_index.get(key)
        if hit:
            resolved.append(hit)
            seen.add(key)
    return resolved


def _source_ref_for(record: dict[str, Any], list_key: str) -> dict[str, Any]:
    """由知识库记录构造溯源引用（factKey 优先取记录自带 factKey，否则用列表键）。"""
    return build_source_ref(
        fact_key=str(record.get("factKey") or list_key or ""),
        fact_id=str(record.get("id") or ""),
        name=str(record.get("name") or ""),
        evidence_path=str(record.get("evidencePath") or ""),
    )


def _record_context_line(record: dict[str, Any], list_key: str) -> str:
    """把一条知识记录压缩成 prompt 里的一行「企业资料」上下文。"""
    name = str(record.get("name") or "").strip() or "（未命名）"
    description = str(record.get("description") or "").strip()
    evidence_path = str(record.get("evidencePath") or "").strip()
    fields = [f"名称：{name}"]
    if description:
        fields.append(f"说明：{description}")
    fields.append(f"来源：{list_key}")
    if evidence_path:
        fields.append(f"证据路径：{evidence_path}")
    return "；".join(fields)


def _records_context(records: list[tuple[dict[str, Any], str]]) -> str:
    """把命中记录拼成 prompt 里的「可用企业资料」块。"""
    if not records:
        return "（无可用企业资料）"
    return "\n".join(f"- {_record_context_line(record, list_key)}" for record, list_key in records)


def _trace_line(records: list[tuple[dict[str, Any], str]]) -> str:
    """生成正文内的「溯源」行（确定性生成，LLM 无法伪造来源）。"""
    parts: list[str] = []
    for record, _list_key in records:
        name = str(record.get("name") or "").strip() or "（未命名）"
        evidence_path = str(record.get("evidencePath") or "").strip()
        parts.append(f"{name}（{evidence_path or '无证据路径'}）")
    return "；".join(parts) if parts else "无命中企业资料对象"


def _dedupe_source_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """同一记录被多条要求命中时，section 级 sourceRefs 去重。"""
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for ref in refs:
        key = (
            ref.get("factKey"),
            ref.get("factId"),
            ref.get("name"),
            ref.get("evidencePath"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(ref)
    return result


def _project_context(project: dict[str, Any]) -> str:
    """提取项目标题/范围（title + parsed 的关键字段），用于通用章节。"""
    if not isinstance(project, dict):
        return ""
    parts: list[str] = []
    title = str(project.get("title") or "").strip()
    parsed = project.get("parsed")
    if isinstance(parsed, dict):
        for key in ("projectName", "serviceType", "serviceLocation"):
            value = str(parsed.get(key) or "").strip()
            if value:
                parts.append(f"{key}：{value}")
    if title:
        parts.append(f"项目名称：{title}")
    return "；".join(parts)


def _company_context(company: dict[str, Any]) -> str:
    """提取企业画像里的概况（名称/简介/能力标签），供通用章节作为事实依据。

    这些字段来自 build_profile（派生自 facts），不是编造；但画像无证据路径，
    因此不生成 sourceRefs（sourceRefs 只由知识库对象产生）。
    """
    if not isinstance(company, dict):
        return ""
    parts: list[str] = []
    name = str(company.get("company") or "").strip()
    description = str(company.get("description") or "").strip()
    capabilities = company.get("capabilities")
    if name:
        parts.append(f"企业名称：{name}")
    if description:
        parts.append(f"企业简介：{description}")
    if isinstance(capabilities, list):
        caps = [str(c).strip() for c in capabilities if str(c).strip()]
        if caps:
            parts.append(f"能力标签：{'、'.join(caps)}")
    return "；".join(parts)


def _generic_context_records(knowledge: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """通用章节的企业资料上下文：优先 products，再 capabilities，去重并限条数。"""
    records: list[tuple[dict[str, Any], str]] = []
    if not isinstance(knowledge, dict):
        return records
    seen: set[str] = set()
    for list_key in ("products", "capabilities"):
        value = knowledge.get(list_key)
        if not isinstance(value, list):
            continue
        for item in value:
            if not isinstance(item, dict):
                continue
            record_id = str(item.get("id") or "").strip()
            name = str(item.get("name") or "").strip()
            key = f"{list_key}:{record_id or name}"
            if key in seen:
                continue
            seen.add(key)
            records.append((item, list_key))
            if len(records) >= _GENERIC_RECORDS_LIMIT:
                return records
    return records


# ---------------------------------------------------------------------------
# LLM 调用与确定性回退
# ---------------------------------------------------------------------------

def _llm_or_fallback(
    ai: dict[str, Any] | None,
    system_prompt: str,
    user_prompt: str,
    fallback_body: str,
    *,
    max_tokens: int,
) -> tuple[str, bool]:
    """调用 LLM 生成正文；ai 为 None 或返回 success=False 时回退确定性正文。

    返回 (body, llm_used)。回退正文不含人工复核标注——标注由调用方在 section
    末尾统一追加，避免多条要求各自重复标注。
    """
    if isinstance(ai, dict):
        try:
            result = call_openai_compatible(
                ai,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
            )
        except Exception:
            result = {"success": False}
        if isinstance(result, dict) and result.get("success"):
            content = str(result.get("content") or "").strip()
            if content:
                return content, True
    return fallback_body, False


def _requirement_user_prompt(
    requirement_text: str,
    evidence: str,
    records: list[tuple[dict[str, Any], str]],
) -> str:
    """逐条响应的 user 提示：要求原文 + 证据摘要 + 命中企业资料。"""
    lines = ["【招标要求】", _clip(requirement_text, _REQUIREMENT_TEXT_LIMIT)]
    if evidence:
        lines += ["", "【已有证据摘要】", _clip(evidence, _EVIDENCE_LIMIT)]
    lines += ["", "【可用企业资料】", _records_context(records)]
    lines += ["", "请基于以上材料逐条响应上面的招标要求；资料未覆盖之处写 __MISSING__ 并说明缺什么。"]
    return "\n".join(lines)


def _fallback_requirement_body(
    requirement_text: str,
    evidence: str,
    records: list[tuple[dict[str, Any], str]],
) -> str:
    """逐条响应的确定性回退：evidence 摘要 + 知识库记录描述，不调用 LLM。"""
    lines = ["**响应（按资料库摘要拼装）**：本企业针对该条要求的可用资料如下。"]
    lines.append("")
    lines.append("**已关联企业资料**：")
    for record, list_key in records:
        lines.append(f"- {_record_context_line(record, list_key)}")
    lines.append("")
    lines.append(f"**证据摘要**：{evidence or '资料库未提供可直接核验的证据。'}")
    return "\n".join(lines)


def _respond_requirement(
    requirement_id: str,
    row: dict[str, Any],
    knowledge_index: dict[str, tuple[dict[str, Any], str]],
    ai: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]], bool]:
    """针对一条要求生成响应块。返回 (markdown 块, sourceRefs, 是否用了回退)。"""
    requirement_text = str(row.get("requirement") or row.get("rawText") or "").strip()
    evidence = str(row.get("evidence") or "").strip()
    records = _resolve_matched_objects(row.get("matchedObjectIds"), knowledge_index)
    source_refs = [_source_ref_for(record, list_key) for record, list_key in records]

    heading = f"#### 响应：{requirement_id}"
    quote = f"> **招标要求**：{requirement_text}" if requirement_text else "> **招标要求**：（原文缺失）"

    # 要求原文缺失：无法逐条响应，直接占位（不调 LLM，避免无依据生成）。
    if not requirement_text:
        body = f"{_MISSING_PLACEHOLDER} 该条要求原文缺失，需人工补充要求内容后重新生成。"
        return "\n\n".join([heading, quote, body]), source_refs, False

    # 资料未命中：直接输出缺失占位，不调 LLM，避免无依据编造。
    if not records:
        body = (
            f"{_MISSING_PLACEHOLDER} 本条要求“{requirement_text}”在资料库中未命中"
            "可支撑的产品/能力/参数/证书/业绩/人员对象，需人工补充证明材料与响应内容。"
        )
        return "\n\n".join([heading, quote, body]), source_refs, False

    user_prompt = _requirement_user_prompt(requirement_text, evidence, records)
    fallback_body = _fallback_requirement_body(requirement_text, evidence, records)
    body, llm_used = _llm_or_fallback(
        ai,
        _SYSTEM_PROMPT,
        user_prompt,
        fallback_body,
        max_tokens=_MAX_TOKENS_REQUIREMENT,
    )

    block = "\n\n".join([heading, quote, body, f"**溯源**：{_trace_line(records)}"])
    return block, source_refs, not llm_used


def _generic_user_prompt(
    theme: str,
    project_context: str,
    company_context: str,
    records: list[tuple[dict[str, Any], str]],
) -> str:
    """通用章节的 user 提示：主题 + 项目信息 + 企业概况 + 产品/能力资料。"""
    lines = ["【章节主题】", theme]
    if project_context:
        lines += ["", "【项目信息】", project_context]
    if company_context:
        lines += ["", "【企业概况（画像）】", company_context]
    lines += ["", "【可用企业资料】", _records_context(records)]
    lines += ["", "请基于以上材料撰写该章节正文；资料未覆盖之处写 __MISSING__ 并说明缺什么，不要编造。"]
    return "\n".join(lines)


def _fallback_generic_body(
    theme: str,
    project_context: str,
    records: list[tuple[dict[str, Any], str]],
) -> str:
    """通用章节的确定性回退：项目信息 + 产品/能力记录描述，不调用 LLM。"""
    lines = [f"**{theme}（按资料库摘要拼装）**"]
    if project_context:
        lines.append(f"项目依据：{project_context}")
    if records:
        lines.append("")
        lines.append("**企业相关能力/产品**：")
        for record, list_key in records:
            lines.append(f"- {_record_context_line(record, list_key)}")
    else:
        lines.append("")
        lines.append(f"{_MISSING_PLACEHOLDER} 资料库缺少产品/能力对象，无法生成通用章节，需人工补充。")
    return "\n".join(lines)


def _respond_generic_section(
    section_id: str,
    title: str,
    project_context: str,
    company_context: str,
    knowledge: dict[str, Any],
    ai: dict[str, Any] | None,
) -> tuple[str, list[dict[str, Any]], bool, bool]:
    """生成 requirementIds 为空的通用章节。返回 (markdown 块, sourceRefs, 是否回退, 是否产出实质内容)。"""
    theme = _GENERIC_SECTION_THEME.get(section_id, title)
    records = _generic_context_records(knowledge)
    source_refs = [_source_ref_for(record, list_key) for record, list_key in records]
    has_basis = bool(records or project_context or company_context)

    if not has_basis:
        body = f"{_MISSING_PLACEHOLDER} 缺少项目信息与企业产品/能力资料，无法生成“{theme}”章节，需人工补充。"
        return body, source_refs, False, False

    user_prompt = _generic_user_prompt(theme, project_context, company_context, records)
    fallback_body = _fallback_generic_body(theme, project_context, records)
    body, llm_used = _llm_or_fallback(
        ai,
        _GENERIC_SYSTEM_PROMPT,
        user_prompt,
        fallback_body,
        max_tokens=_MAX_TOKENS_GENERIC,
    )
    block = "\n\n".join([body, f"**溯源**：{_trace_line(records)}"])
    return block, source_refs, not llm_used, True


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------

def generate_technical_sections(
    plan: list[dict],
    fine_review: dict,
    project: dict,
    company: dict,
    knowledge: dict,
    ai: dict | None,
) -> dict[str, dict]:
    """为 plan 中 kind=="technical" 的章节生成技术标书正文。

    返回 {sectionId: {"content": Markdown, "sourceRefs": list, "status": "generated"/"missing"}}。
    - status="generated"：生成了至少一个实质响应块（逐条响应命中行，或通用章节有项目/资料依据）。
    - status="missing"：要求行全部未找到，或通用章节既无项目信息又无企业资料，无法生成。

    防幻觉与溯源约束见模块 docstring。
    """
    result: dict[str, dict] = {}
    if not isinstance(plan, list):
        return result

    rows = _rows(fine_review)
    knowledge_index = _index_knowledge(knowledge)
    project_context = _project_context(project)
    company_context = _company_context(company)

    for section in plan:
        if not isinstance(section, dict):
            continue
        if section.get("kind") != SECTION_KIND_TECHNICAL:
            continue
        section_id = str(section.get("sectionId") or "").strip()
        if not section_id:
            continue
        title = str(section.get("title") or "").strip() or section_id
        requirement_ids = _requirement_ids(section)

        parts: list[str] = [f"## {title}"]
        source_refs: list[dict[str, Any]] = []
        used_fallback = False
        produced = False

        if requirement_ids:
            for requirement_id in requirement_ids:
                row = _row_by_id(rows, requirement_id)
                if row is None:
                    parts.append(
                        f"#### 响应：{requirement_id}\n\n"
                        f"> {_MISSING_PLACEHOLDER} 未在 fineReview.rows 中找到该要求原文，需人工补充。"
                    )
                    continue
                produced = True
                block, refs, fallback_used = _respond_requirement(
                    requirement_id, row, knowledge_index, ai
                )
                parts.append(block)
                source_refs.extend(refs)
                used_fallback = used_fallback or fallback_used
        else:
            block, refs, fallback_used, generated = _respond_generic_section(
                section_id, title, project_context, company_context, knowledge, ai
            )
            parts.append(block)
            source_refs.extend(refs)
            used_fallback = used_fallback or fallback_used
            produced = generated

        content = "\n\n".join(part for part in parts if part)
        if used_fallback:
            content += f"\n\n{_FALLBACK_NOTE}"

        result[section_id] = {
            "content": content,
            "sourceRefs": _dedupe_source_refs(source_refs),
            "status": "generated" if produced else "missing",
        }

    return result
