# -*- coding: utf-8 -*-
"""结构化填充：模板字段填充、要求逐条响应表。

由原 `prompts.py` 机械拆分而来（Wave 0 · 0.2），提示词文本一字未改。
"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403  （常量、schema 与公共导入）
from ._shared import (  # noqa: F401  （star import 不带下划线前缀的私有名）
    _DEPTH_GUIDANCE,
    _OUTLINE_SOURCE_TERMS,
    _WRITING_PROFILE_SPECS,
    _agent_context,
    _compact_bid_document_for_review,
    _compact_company,
    _compact_fine_review,
    _compact_material_record,
    _compact_parse_bundle_for_review,
    _compact_project,
    _dump,
    _outline_instruction_context,
    _planning_parse_bundle_view,
    _profile_block,
    _section_guidance_block,
    _section_knowledge_view,
    _section_parse_bundle_view,
    _section_prompt_view,
    _writing_parse_bundle_view,
    _writing_profile_spec,
)




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
