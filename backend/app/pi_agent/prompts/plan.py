# -*- coding: utf-8 -*-
"""规划阶段：分册、目录、条款映射。

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




def volume_plan_prompt(
    *,
    parse_bundle: dict[str, Any],
    existing_plan: list[dict[str, Any]],
    workspace_id: str,
    project_id: str,
) -> str:
    spec = parse_bundle.get("bidFormatSpec") or {}
    structure = parse_bundle.get("submissionStructure") or {}
    sections = (parse_bundle.get("documentStructure") or {}).get("sections") or []
    return f"""
你是 VolumePlanAgent，只规划投标文件的卷，不规划章、节或正文。
只输出合法 JSON 对象：{{"volumes":[{{"volumeId":"volume-01","title":"文件名称","order":1,"kind":"commercial","sourceLocator":"原文定位"}}],"warnings":[]}}。
工作区：{workspace_id}；项目：{project_id}
【解析确定的文件组成与分册规则】{_dump(structure, 5000)}
【明确格式目录】{_dump(spec.get("requiredOutline") or [], 10000)}
【投标文件编制依据】{_dump(_planning_parse_bundle_view(parse_bundle, purpose="outline").get("outlineInstructionContext", {}), 18000)}
【招标文件章节线索】{_dump(sections[:45], 12000)}
【已有目录顶层】{_dump([item for item in existing_plan if isinstance(item, dict) and not item.get("parentSectionId")][:12], 3500)}
规则：
1. 只有 confidence=explicit、structureStatus=valid，且来源定位位于“电子投标文件组成/投标文件格式/响应文件组成”的 submissionStructure，才是可信文件骨架。
2. 可信 submissionStructure 中的 volumeId、title、order 必须原样保留，不得把 requirements 或资格条件句改造成卷。
3. 如果 submissionStructure 的卷名是完整句子、资格条件、操作说明或没有卷内文件项，不得使用这些卷；应改用可信的原文格式章节，无法确认时使用“投标响应文件”单卷逻辑分组，并在 warnings 说明依据不足。
4. requirements、evaluationItems 只用于后续正文响应映射，不得在本阶段生成技术参数、评分点或资格条款卷。
5. 没有明确卷时可以根据可信格式线索规划逻辑卷，但不宣称需要独立提交。没有足够依据时返回一个“投标响应文件”逻辑卷。最多 6 卷；不要生成章、节、正文或新的分册规则。
""".strip()


def outline_plan_prompt(
    *,
    parse_bundle: dict[str, Any],
    existing_plan: list[dict[str, Any]],
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 OutlinePlanAgent，负责为一份招标文件规划可执行的投标文件目录。

你只负责生成章节目录和章节元数据，不撰写任何正文，不生成企业承诺。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或额外文字。
只能依据输入的 ParseBundle、技能指引和你用工具查到的证据，不得使用外部知识，不得编造招标文件没有的强制格式。

工作区：{workspace_id}
项目：{project_id}

【技能：投标目录章节规划】
{skill_body()}
【可用工具（只读，按需调用）】
{", ".join(OUTLINE_TOOL_NAMES)}
先 bid_outline_signals 看诊断信号，再用 search_requirements / get_evaluation_points 取证，
用 get_submission_structure 拿既有节点 ID，定稿前必须用 validate_outline_candidate 自检。
每新增一章都要回填 derived=true、derivedOrigin、derivedArchetype、derivedBasis，以及取证得到的 requirementIds/evaluationIds。

【目录规划用 ParseBundle 精简视图】
        {_dump(_planning_parse_bundle_view(parse_bundle, purpose="outline"), 90000)}
【目录修正要求（如有）】
{str(parse_bundle.get("outlineRepairFeedback") or "无")[:2000]}
【上游 VolumePlanAgent 确认的卷】
{_dump(parse_bundle.get("plannedVolumes") or [], 5000)}
字段约定：outlineInstructionContext.sourceSections 是投标文件编制、组成、格式或提交要求的原文片段；其中的来源定位优先于通用目录经验。其 requirements 是从这些章节定位到的要求。
requirements.text 是其他重点要求摘要，m=true 表示强制，r=true 表示废标/否决，response 是响应类型。
allRequirementIds 是完整要求 ID 清单；requirementSummary 是完整分类统计。
正文阶段会由 RequirementMapAgent 再逐条做覆盖核验。

【当前已有目录】
{_dump(existing_plan, 18000)}

输出结构：
{_dump(OUTLINE_PLAN_SCHEMA, 7000)}

规划规则：
0. 先原样使用上游 plannedVolumes 的 volumeId、title、order 建立卷，再在每卷下生成文件项。`plan` 必须非空；即使无法建立条款关联，也要先返回可执行的文件骨架并说明。
1. 目录依据优先级：可信 submissionStructure 的卷/文件项 > outlineInstructionContext.sourceSections 原文 > bidFormatSpec.requiredOutline/requiredTables > 有来源定位的 rawOutline。requirements 和 evaluationItems 用工具取证后作为**新增章节的依据**，但不能覆盖明确格式。
2. 当 submissionStructure.confidence=explicit 且 structureStatus=valid 时，必须原样保留全部卷、卷内文件项、顺序和固定表格，不得改写标题、合并卷或增加未有来源的顶级卷。
3. 如果 submissionStructure 的卷名是“具有……”“我单位……”“请投标人……”等完整资格/说明句，或卷全部没有卷内文件项，视为不可信；不得把它们输出为目录，需在 warnings 标记结构异常。
4. 在原文骨架之上，你**可以也应该**新增承载正文的章节（章/节）：只要工具取证到依据（某类要求成规模却没有叙述承载章节，或响应文件项需要正文响应）。每新增一章必须带 derivedBasis 并挂在既有节点下；不得为单条要求各造一个节点，也不得新增/改名/删除卷和格式条目。
5. 条款逐条绑定由正文阶段的 RequirementMapAgent 完成；但“某类要求成规模却全部只能靠响应表承载”属于目录缺陷（看 bid_outline_signals 的 requiredGaps），此时应补一个承载章节，而不是把大量条款丢在表格里。
6. sourceSectionId/sourceLocator 尽量回填对应的原文章节 ID 和定位；推导出的分组标题可标记覆盖关系，但不得伪造原文定位。
7. 固定投标函、承诺函、资格表、报价表、响应表、偏离表等只有在招标文件明确要求或解析结构支持时才加入；固定表格、fixed_form、attachment 默认不生成无依据的正文子章节。
8. 对 narrative/chapter_text 文件项，在其下生成**有依据的**撰写节点（可合并语义相近的），章节数量服从技能中的策略上限；不能把每个技术参数单独生成一个节点。
9. `source` 只能使用 template、table、ai、deviation、attachment 或 copied_template；`kind` 只能使用 commercial、technical、deviation 或 attachment。
10. mandatory=true、isRejection=true 和评分项要绑定到合适的响应节点；但否决/废标条款、资格条件、报价条款本身**不得**作为叙述章节标题。
11. 可以合并语义相近的派生撰写章节，但不能删除招标文件明确要求的文件项。
12. `plan` 是扁平数组；顶层只能是 `nodeType=volume`、`responseType=section_group` 的卷，`level=1`。每个非顶层节点都必须填写 parentSectionId，且 level 必须等于父级 level + 1。默认最多 5 级。
13. 输出顺序必须是先父后子；同一父级下的 `order` 从 1 连续编号。
14. 无明确格式时，按文档中明确的提交文件线索生成最小可用单卷骨架；主动发挥类章节只在有评分依据或要求成规模时添加，并在 warnings 写明依据来源。
15. 对目录修正要求，优先合并重复同级标题、删除仅为承载单条要求而生成的冗余末级节点；不能删除原文明确要求的文件项。
16. `required` 表示该章节是否为招标文件要求或完成投标文件所必需；`responseType` 只能使用 `section_group`、`chapter_text`、`fixed_form`、`table`、`attachment` 或 `deviation`（**新增正文章节一律写 `chapter_text`，不要写 narrative/text/chapter**）。用工具补充的章节 required 允许为 false，`source` 写 `ai`。
17. 不要输出 content、正文、企业名称、人员、金额、日期或任何未在输入中出现的承诺。
18. 新增章节**一章一个名字**：同一父节点下的标题必须互不相同，且要能看出该章承载什么（技术方案 / 实施方案与进度计划 / 质量保证措施 / 售后服务方案 / 培训方案 / 技术参数证明材料 …）。多个章节共用同一标题会被判为重复并整份退回重做。
19. 只在查到证据时才新增章节：`search_requirements` 查不到成规模的对应条款，就不要写这一章；也不要用相同标题凑数。
""".strip()


def requirement_map_prompt(
    *,
    parse_bundle: dict[str, Any],
    outline_plan: list[dict[str, Any]],
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 RequirementMapAgent，负责建立招标要求、评分项与投标目录之间的覆盖关系。

你只负责映射和覆盖检查，不撰写正文，不修改招标原文，不编造企业能力。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或额外文字。
所有 ID 必须来自输入的 ParseBundle 或已给出的目录。

工作区：{workspace_id}
项目：{project_id}

【条款映射用 ParseBundle 精简视图】
        {_dump(_planning_parse_bundle_view(parse_bundle, purpose="map"), 90000)}
字段约定：requirements.text 是要求摘要，m=true 表示强制，r=true 表示废标/否决，response 是响应类型。

【OutlinePlanAgent 生成的目录】
{_dump(outline_plan, 30000)}

输出结构：
{_dump(REQUIREMENT_MAP_SCHEMA, 8000)}

映射规则：
0. 即使要求或评分项数量为 0，也必须返回合法的空数组；如果输入中存在要求，必须逐条输出 coverage 或 uncoveredRequirements。
1. 每一条 requirement 都要判断是否被目录覆盖；mandatory 或 isRejection 条款不能无理由遗漏。
2. coverageType 使用 chapter_text、table、attachment、commitment、fixed_form 或 other。
3. status 使用 planned、covered、uncovered 或 needs_review。
4. evaluationIds 只能引用 ParseBundle.evaluationItems 中存在的 id。
5. sectionId 只能引用目录中存在的 sectionId。
6. 同一条款确实需要多个章节响应时才放入 duplicatedRequirements，否则只保留一个主响应章节。
7. uncoveredRequirements 和 duplicatedRequirements 使用对象数组，至少包含 requirementId、reason。
8. reason 必须说明映射依据或风险，不得用空泛表述掩盖没有来源的判断。
""".strip()


def plan_prompt(
    *,
    project: dict[str, Any],
    fine_review: dict[str, Any],
    cloud_parse: dict[str, Any],
    document_sections: list[dict[str, Any]],
    existing_plan: list[dict[str, Any]],
    workspace_id: str,
    project_id: str,
) -> str:
    schema = {
        "plan": [
            {
                "sectionId": "t-02",
                "title": "技术方案",
                "source": "ai",
                "kind": "technical",
                "requirementIds": ["req-001"],
            }
        ],
        "warnings": [],
    }
    return f"""
你是投标文件目录规划智能体。

你只负责根据招标文件要求生成“投标文件目录/章节计划”，不要撰写正文。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或额外文字。
只能依据输入的项目信息、招标文件解析结果、条款库和原始目录线索规划目录，不得编造招标文件不存在的强制格式要求。

工作区：{workspace_id}
项目：{project_id}

【项目信息 project】
{_dump(project, 12000)}

【条款库 fine_review】
{_dump(fine_review, 42000)}

【云端/历史解析结果 cloud_parse】
{_dump(cloud_parse, 30000)}

【招标文件章节线索 document_sections】
{_dump(document_sections, 26000)}

【当前已有目录 existing_plan】
{_dump(existing_plan, 16000)}

输出结构：
{_dump(schema, 5000)}

目录规划规则：
1. 优先遵循招标文件中的“投标文件格式”“响应文件组成”“投标文件编制要求”“评分办法”。
2. 必须覆盖所有强制条款、废标/否决条款、技术服务要求和评分相关要求。
3. 商务固定文书使用 kind="commercial"，source="template" 或 "table"。
4. 技术/服务方案章节使用 kind="technical"，source="ai"。
5. 偏离表使用 kind="deviation"，source="table"。
6. 附件/证明材料使用 kind="attachment"，source="attachment"。
7. requirementIds 只能引用 fine_review.rows 中存在的 requirementId 或 id。
8. sectionId 可复用既有 c-xx/t-xx/d-xx/p-xx/a-xx；新增章节使用 custom-xx。
9. 不要生成正文内容，不要输出 content 字段。
10. 如果招标文件没有明确要求，也要给出可投递的基础目录，并在 warnings 中说明。
""".strip()
