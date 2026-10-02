# -*- coding: utf-8 -*-
"""正文阶段：素材匹配、本章提纲、正文起草、按指令改写。

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




# ── 本阶段示例 schema（给模型看的输出样例，不是 JSON Schema）──────
MATERIAL_MATCH_SCHEMA = {
    "matches": [
        {
            "requirementId": "req-001",
            "materialIds": ["qualification-001"],
            "materialRefs": [
                {
                    "materialId": "qualification-001",
                    "factKey": "qualifications",
                    "name": "",
                    "evidencePath": "",
                }
            ],
            "matchStatus": "matched",
            "evidenceSummary": "",
            "missingReason": "",
        }
    ],
    "unmatchedRequirementIds": [],
    "warnings": [],
}

SECTION_DRAFT_SCHEMA = {
    "sectionId": "t-02",
    "title": "技术方案",
    "content": "Markdown 正文",
    "sourceRefs": [
        {
            "factKey": "",
            "factId": "",
            "name": "",
            "evidencePath": "",
        }
    ],
    "status": "generated",
    "warnings": [],
}

SECTION_PLAN_SCHEMA = {
    "sectionId": "t-02",
    "title": "技术方案",
    "objective": "",
    "requirementResponses": [
        {
            "requirementId": "req-001",
            "responseStrategy": "",
            "mustInclude": [],
            "materialIds": [],
            "risk": "",
        }
    ],
    "evaluationResponses": [
        {
            "evaluationId": "eval-001",
            "focus": "",
            "evidenceIds": [],
        }
    ],
    "outline": [
        {
            "heading": "1.1 响应说明",
            "purpose": "",
            "requirementIds": [],
        }
    ],
    "templateInstructions": [],
    "warnings": [],
}

SECTION_REWRITE_SCHEMA = {
    "sectionId": "t-02",
    "operation": "append",
    "content": "",
    "replacement": "新增或替换的 Markdown 片段",
    "sourceRefs": [
        {
            "factKey": "",
            "factId": "",
            "name": "",
            "evidencePath": "",
        }
    ],
    "changed": True,
    "blanksToFill": [],
    "warnings": [],
}



def material_match_prompt(
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
    writing_profile: str = "technical",
) -> str:
    profile = writing_profile or resolve_writing_profile(section)
    return f"""
你是 MaterialMatchAgent，负责为一个投标章节匹配企业材料和可引用证据。

只做材料匹配，不写正文，不把招标要求当成企业能力。
只引用下方已提供且真实存在的材料；找不到材料就返回 unmatched。
只输出合法 JSON，不要输出 Markdown、解释或额外文字。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}
{_profile_block(profile)}

【章节解析上下文】
{_dump(_section_parse_bundle_view(parse_bundle, section=section, coverage=coverage), 24000)}

【当前章节】
{_dump(_section_prompt_view(section, profile), 5000)}

【本章节要求覆盖关系】
{_dump(coverage, 14000)}

【项目关键字段】
{_dump(_compact_project(project), 4000)}

【企业关键字段】
{_dump(_compact_company(company), 6000)}

【可用企业材料（已按画像筛选）】
{_dump(_section_knowledge_view(knowledge, writing_profile=profile, max_items_per_group=32), 24000)}

输出结构：
{_dump(MATERIAL_MATCH_SCHEMA, 6000)}

匹配规则：
1. 只处理当前章节的 requirementIds 和 coverage。
2. materialIds 只能引用已提供材料中的 id、factId、materialId 或 evidenceId。
3. 每个匹配保留 factKey、name、evidencePath；没有证据路径不要猜。
4. matchStatus 只能是 matched、partial、unmatched、needs_review。
5. 不匹配时写入 unmatchedRequirementIds 和 missingReason。
6. mode=secret 时不输出企业名称、人员姓名、地址等敏感信息。
7. sourceEvidence 是招标文件原文证据，仅用于理解本章要求，不得将招标人的要求表述成投标人的既有能力。
""".strip()


def section_plan_prompt(
    *,
    parse_bundle: dict[str, Any],
    coverage: list[dict[str, Any]],
    section: dict[str, Any],
    material_matches: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    mode: str,
    workspace_id: str,
    project_id: str,
    writing_profile: str = "technical",
) -> str:
    profile = writing_profile or resolve_writing_profile(section)
    return f"""
你是 SectionPlanAgent，负责为一个投标章节制定可审核的写作计划。

只做写作计划，不写正文，不编造企业承诺，不修改招标要求。
计划必须让下游正文智能体逐条响应要求并引用真实材料。
只输出合法 JSON，不要输出 Markdown、解释或额外文字。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}
{_profile_block(profile)}

【本章蓝图与篇幅】
{_section_guidance_block(section) or "（无特定范式，按画像与本类重点撰写）"}

【章节解析上下文】
{_dump(_section_parse_bundle_view(parse_bundle, section=section, coverage=coverage), 22000)}

【当前章节】
{_dump(_section_prompt_view(section, profile), 5000)}

【条款覆盖关系】
{_dump(coverage, 12000)}

【材料匹配结果】
{_dump(material_matches, 14000)}

【项目关键字段】
{_dump(_compact_project(project), 3500)}

【企业关键字段】
{_dump(_compact_company(company), 5000)}

【企业材料】
{_dump(_section_knowledge_view(knowledge, material_ids={
    str(
        item.get("materialId")
        or item.get("factId")
        or item.get("evidenceId")
        or ""
    ).strip()
    for match in material_matches.get("matches", [])
    if isinstance(match, dict)
    for item in match.get("materialRefs", [])
    if isinstance(item, dict)
    and str(
        item.get("materialId")
        or item.get("factId")
        or item.get("evidenceId")
        or ""
    ).strip()
}, writing_profile=profile, max_items_per_group=16), 12000)}

输出结构：
{_dump(SECTION_PLAN_SCHEMA, 7000)}

规划规则：
1. sectionId/title 必须与当前章节一致。
2. requirementResponses 覆盖当前章节的所有要求；强制/否决条款必须给出策略。
3. evaluationIds 和 materialIds 只能引用输入中存在的 ID。
4. outline 只输出小节提纲，每个小节绑定 requirementIds 或 evaluationIds。
5. 资料不足时写入 risk 和人工补充点；不要假设企业能力。
6. mode=secret 时不安排企业名称、人员姓名、地址等敏感内容。
7. sourceEvidence 中的招标文件原文优先于概括性摘要；严格按其编制要求组织响应，不擅自扩展强制格式。
""".strip()


def section_draft_prompt(
    *,
    parse_bundle: dict[str, Any],
    coverage: list[dict[str, Any]],
    section: dict[str, Any],
    writing_plan: dict[str, Any],
    material_matches: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    mode: str,
    workspace_id: str,
    project_id: str,
    writing_profile: str = "technical",
) -> str:
    profile = writing_profile or resolve_writing_profile(section)
    return f"""
你是 SectionDraftAgent，负责根据已确认的写作计划生成一个投标章节正文。

只依据输入的章节要求、写作计划、项目字段和已匹配材料撰写。
严禁编造资质、证书、业绩、人员、金额、日期、参数、案例和承诺。
资料不足时写“【需人工补充：缺少具体资料】”，并在 warnings 说明。
企业事实必须有真实 sourceRefs；只输出合法 JSON，不要输出解释或代码块。
sourceEvidence 中的招标文件原文是本章节响应依据；应逐项落实其编制、格式、表格和提交要求，不得把招标要求改写成企业事实或承诺。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}
{_profile_block(profile)}

【本章蓝图与篇幅】
{_section_guidance_block(section) or "（无特定范式，按画像与本类重点撰写）"}

【章节解析上下文】
{_dump(_section_parse_bundle_view(parse_bundle, section=section, coverage=coverage), 22000)}

【当前章节】
{_dump(_section_prompt_view(section, profile), 5000)}

【条款覆盖关系】
{_dump(coverage, 12000)}

【写作计划】
{_dump(writing_plan, 16000)}

【材料匹配结果】
{_dump(material_matches, 12000)}

【项目关键字段】
{_dump(_compact_project(project), 3500)}

【企业关键字段】
{_dump(_compact_company(company), 5000)}

【企业材料】
{_dump(_section_knowledge_view(knowledge, material_ids={
    str(
        item.get("materialId")
        or item.get("factId")
        or item.get("evidenceId")
        or ""
    ).strip()
    for match in material_matches.get("matches", [])
    if isinstance(match, dict)
    for item in match.get("materialRefs", [])
    if isinstance(item, dict)
    and str(
        item.get("materialId")
        or item.get("factId")
        or item.get("evidenceId")
        or ""
    ).strip()
}, writing_profile=profile, max_items_per_group=16), 12000)}

输出结构：
{_dump(SECTION_DRAFT_SCHEMA, 5500)}

撰写规则：
1. sectionId/title 必须与当前章节一致。
2. 按 outline 顺序输出 Markdown 标题、段落、列表或表格。
3. 每条 requirementResponse 都要有明确响应，不用“完全满足”代替内容。
4. 评分项只呈现输入中的 focus，不新增评分条件。
5. 每个企业事实在 sourceRefs 中给出 materialId/factId、factKey、name、evidencePath。
6. status 只能是 generated、missing 或 needs_review。
""".strip()


def section_rewrite_prompt(
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
    writing_profile: str = "technical",
) -> str:
    profile = writing_profile or resolve_writing_profile(section)
    return f"""
你是 SectionRewriteAgent，负责对一个投标章节执行局部补写或重写。

只依据当前章节、现有正文、对应要求和已提供材料修改。
不得编造资质、证书、业绩、人员、金额、日期、参数、案例或承诺。
固定模板、表头、锁定文字和签字盖章位置不可修改；资料不足保留人工补充标记。
只输出合法 JSON，不要输出 Markdown、解释或额外文字。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}
操作：{operation}
{_profile_block(profile)}
用户修改要求：{instruction}
选中的正文片段（可能为空）：
{(selected_text or "（未提供选中文本）")[:8000]}

【本章蓝图与篇幅】
{_section_guidance_block(section) or "（无特定范式，按画像与本类重点撰写）"}

【当前章节解析上下文】
{_dump(_section_parse_bundle_view(parse_bundle, section=section, coverage=coverage), 18000)}

【当前章节】
{_dump(_section_prompt_view(section, profile), 5000)}

【当前章节正文】
{_dump(str(current_content or "")[:18000], 18000)}

【条款覆盖关系】
{_dump(coverage, 10000)}

【项目关键字段】
{_dump(_compact_project(project), 3500)}

【企业关键字段】
{_dump(_compact_company(company), 5000)}

【可用企业材料】
{_dump(_section_knowledge_view(knowledge, writing_profile=profile, max_items_per_group=20), 16000)}

输出结构：
{_dump(SECTION_REWRITE_SCHEMA, 6500)}

输出规则：
1. sectionId 必须与当前章节一致。
2. append 只返回追加片段；replace_selection 只返回选区替换片段；rewrite 才返回完整正文。
3. 不要输出章节标题，除非原正文将标题作为正文内容。
4. 新增企业事实必须有 sourceRefs；没有依据就保留人工补充标记。
5. 用户要求与招标文件或材料冲突时，保留合规内容并在 warnings 说明。
6. 无法安全修改时返回 changed=false、空 replacement/content 并说明原因。
""".strip()


def section_prompt(
    *,
    section: dict[str, Any],
    fine_review: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    mode: str,
    workspace_id: str,
    project_id: str,
) -> str:
    schema = {
        "sectionId": str(section.get("sectionId") or ""),
        "title": str(section.get("title") or ""),
        "content": "Markdown 正文",
        "sourceRefs": [
            {
                "factKey": "",
                "factId": "",
                "name": "",
                "evidencePath": "",
            }
        ],
        "status": "generated",
        "warnings": [],
    }
    return f"""
你是投标文件章节撰写智能体。

必须只输出一个合法 JSON 对象，不要输出 Markdown 代码块、解释或额外文字。
只能依据输入的章节、招标条款、项目信息、企业画像和企业知识资料撰写。
严禁编造资质、证书、业绩、人员、金额、日期、参数、案例和承诺。
资料不足时必须在 content 中写“【需人工补充：缺少具体资料】”，并在 warnings 中说明。
sourceRefs 只能引用输入 knowledge 或 fine_review 中真实存在的对象。
如果 mode 为 secret，不得出现公司名称、品牌、人员姓名、地址等暗标敏感信息。

工作区：{workspace_id}
项目：{project_id}
模式：{mode}

【section】
{_dump(section, 18000)}

【fine_review】
{_dump(fine_review, 42000)}

【project】
{_dump(project, 12000)}

【company】
{_dump(company, 12000)}

【knowledge】
{_dump(knowledge, 30000)}

输出结构：
{_dump(schema, 6000)}

规则：
1. sectionId 必须等于 section.sectionId。
2. title 必须等于 section.title。
3. content 使用 Markdown，围绕本章节完成可审核的响应正文。
4. section.requirementIds 不为空时，优先逐条响应对应 fine_review.rows。
5. 强制项必须明确回应；无法回应的地方不得假装满足。
6. status 只能使用 generated 或 missing。
""".strip()
