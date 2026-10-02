# -*- coding: utf-8 -*-
"""解析阶段：结构、项目元信息、条款、评分项、模板、解析质检。

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
DOCUMENT_STRUCTURE_SCHEMA = {
    "sections": [
        {
            "sectionId": "doc-sec-001",
            "title": "第三章 评标办法",
            "level": 1,
            "startPage": 1,
            "endPage": 1,
            "sourceLocator": "第三章 评标办法",
            "blockIds": [],
            "sectionType": "evaluation",
        }
    ],
    "warnings": [],
}

EVALUATION_SCHEMA = {
    "evaluationItems": [
        {
            "id": "eval-001",
            "category": "technical",
            "score": None,
            "criterion": "",
            "criteria": "",
            "writingFocus": "",
            "sourceLocator": "",
            "relatedRequirementIds": [],
        }
    ],
    "warnings": [],
}

PARSE_REVIEW_SCHEMA = {
    "status": "pass_with_warnings",
    "confidence": 0.0,
    "missingFields": [],
    "warnings": [],
    "riskItems": [
        {
            "level": "medium",
            "title": "",
            "detail": "",
            "sourceLocator": "",
        }
    ],
}

PARSE_SCHEMA = {
    "projectMeta": {
        "projectName": "",
        "projectCode": "",
        "tendererName": "",
        "agencyName": "",
        "budget": "",
        "deadline": "",
    },
    "outline": [
        {
            "sectionId": "t-01",
            "title": "项目理解",
            "kind": "technical",
            "source": "ai",
            "requirementIds": [],
        }
    ],
    "requirements": [
        {
            "id": "req-001",
            "category": "technical",
            "requirement": "",
            "mandatory": False,
            "isRejection": False,
            "sourceLocator": "",
            "status": "待核验",
            "rawText": "",
            "section": "",
        }
    ],
    "evaluationItems": [
        {
            "factor": "",
            "item": "",
            "score": None,
            "criteria": "",
            "sourceLocator": "",
        }
    ],
    "riskItems": [
        {
            "level": "高",
            "title": "",
            "detail": "",
            "sourceLocator": "",
        }
    ],
    "warnings": [],
}

PROJECT_META_SCHEMA = {
    "projectMeta": {
        "projectName": "",
        "projectCode": "",
        "tendererName": "",
        "agencyName": "",
        "budget": "",
        "deadline": "",
        "deliveryPeriod": "",
        "serviceLocation": "",
    },
    "sourceRefs": [],
    "warnings": [],
}

REQUIREMENT_SCHEMA = {
    "requirements": [
        {
            "id": "req-001",
            "category": "technical",
            "requirement": "",
            "mandatory": False,
            "isRejection": False,
            "importance": "medium",
            "responseType": "chapter_text",
            "sourceLocator": "",
            "sourceSection": "",
            "sourceText": "",
            "suggestedEvidence": [],
        }
    ],
    "warnings": [],
}

TEMPLATE_SCHEMA = {
    "hasExplicitFormat": False,
    "formatSourceSection": "",
    "submissionStructure": {
        "mode": "unspecified",
        "confidence": "unknown",
        "sourceLocator": "",
        "volumes": [
            {
                "volumeId": "volume-01",
                "title": "技术文件",
                "order": 1,
                "required": True,
                "sourceLocator": "",
                "formatItemIds": [],
                "items": [
                    {
                        "formatItemId": "volume-01-01",
                        "title": "",
                        "order": 1,
                        "required": True,
                        "conditional": False,
                        "conditionText": "",
                        "itemType": "fixed_form",
                        "responseType": "fixed_form",
                        "templateType": "fixed_template",
                        "templateId": "tpl-volume-01-01",
                        "sourceLocator": "",
                    }
                ],
            }
        ],
        "warnings": [],
    },
    "requiredOutline": [
        {
            "formatItemId": "fmt-001",
            "volumeId": "volume-01",
            "title": "",
            "type": "free_text",
            "responseType": "chapter_text",
            "required": True,
            "conditional": False,
            "conditionText": "",
            "sourceLocator": "",
            "templateId": "",
            "templateType": "",
        }
    ],
    "templates": [
        {
            "templateId": "tpl-001",
            "title": "",
            "templateType": "fixed_template",
            "sourceLocator": "",
            "editableFields": [],
            "lockedBlocks": [],
            "blocks": [],
        }
    ],
    "requiredTables": [],
    "warnings": [],
}



def document_structure_prompt(
    *,
    document_text: str,
    document_payload: dict[str, Any],
    filename: str,
    parser_warnings: list[str],
    workspace_id: str,
    project_id: str,
    project_context: dict[str, Any],
) -> str:
    return f"""
你是 DocumentStructureAgent，负责识别招标文件的章节结构。

只依据输入的招标文件内容和本地解析块，不得使用外部知识，不得编造章节。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
保留原文顺序；标题不确定时放入 warnings，不要猜测页码。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context=project_context)}

【本地解析器提示】
{_dump(parser_warnings, 5000)}

【本地结构化解析结果】
{_dump(document_payload, 16000)}

【招标文件文本】
{_dump(document_text, 10000)}

输出结构：
{_dump(DOCUMENT_STRUCTURE_SCHEMA, 8000)}

识别规则：
1. 识别招标公告、投标人须知、资格要求、评标办法、技术需求、商务要求、合同条款、投标文件格式、附件等章节。
2. level 使用 1、2、3 等整数；无法确认层级时使用 1。
3. sourceLocator 尽量保留原文中的章节、页码、表格名称。
4. sectionType 使用 announcement、qualification、evaluation、technical、commercial、contract、document_format、appendix 或 other。
5. 不要把普通长段落、表格行或联系人信息误判成章节。
6. 最多返回 80 个关键章节；优先保留一级、二级章节和业务章节，合并重复标题。
""".strip()


def project_meta_prompt(
    *,
    document_text: str,
    document_structure: dict[str, Any],
    filename: str,
    workspace_id: str,
    project_id: str,
    project_context: dict[str, Any],
) -> str:
    return f"""
你是 ProjectMetaAgent，负责从招标文件中抽取项目基础信息。

只依据输入内容，不得补全未知信息，不得使用外部知识。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
字段缺失使用空字符串；每个已填写字段尽量提供 sourceRefs。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context=project_context)}

【文档结构】
{_dump(document_structure, 24000)}

【招标文件文本】
{_dump(document_text, 24000)}

输出结构：
{_dump(PROJECT_META_SCHEMA, 7000)}

重点抽取：
1. 项目名称、项目编号、采购人/招标人、代理机构。
2. 预算或最高限价、投标截止时间、交付/服务期限、服务地点。
3. 不要把供应商资格条件误写成项目基础信息。
4. sourceRefs 中记录 sourceLocator、原文片段或章节。
""".strip()


def requirement_extract_prompt(
    *,
    document_text: str,
    document_structure: dict[str, Any],
    project_meta: dict[str, Any],
    filename: str,
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 RequirementExtractAgent，负责抽取所有需要投标方响应的原子要求。

只依据招标文件，不得使用外部知识，不得编造。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
每条要求必须是可执行的原子条款，不能把整章原文复制进 requirement。
每条要求必须有唯一 id，格式为 req-001、req-002……

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context=project_meta)}

【项目基础信息】
{_dump(project_meta, 10000)}

【文档结构】
{_dump(document_structure, 30000)}

【招标文件文本】
{_dump(document_text, 36000)}

输出结构：
{_dump(REQUIREMENT_SCHEMA, 10000)}

分类规则：
1. category 使用 qualification、technical、commercial、service、delivery、personnel、performance、financial、credit、pricing、contract、document、rejection 或 other。
2. mandatory：必须、应当、不得、否则无效、废标、否决、实质性要求等按语义判断。
3. isRejection：废标、无效投标、否决、实质性不响应等条款设为 true。
4. responseType 使用 chapter_text、table、attachment、commitment、fixed_form 或 other。
5. sourceLocator 和 sourceSection 必须尽量定位原文。
6. 不要把项目名称、联系人、公告时间等普通信息当成投标要求。
7. 当前输入可能是长文档的一个内容分片。最多返回 25 条原子要求；优先保留强制条款、废标条款、评分相关条款和明确交付要求，合并重复要求。
8. 根节点必须严格使用 requirements 和 warnings；即使只识别到一条，也必须放入 requirements 数组并闭合完整 JSON。
""".strip()


def evaluation_extract_prompt(
    *,
    document_text: str,
    document_structure: dict[str, Any],
    requirements: list[dict[str, Any]],
    filename: str,
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 EvaluationExtractAgent，负责抽取评标办法和评分细则。

只依据招标文件，不得使用外部知识，不得猜测分值。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
如果招标文件没有明确评分信息，返回空数组并在 warnings 中说明。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context={})}

【文档结构】
{_dump(document_structure, 26000)}

【已抽取要求，可用于关联】
{_dump(requirements, 36000)}

【招标文件文本】
{_dump(document_text, 18000)}

输出结构：
{_dump(EVALUATION_SCHEMA, 8000)}

规则：
1. score 必须来自原文；无法确认时使用 null。
2. criterion/criteria 保留评分标准的核心语义。
3. writingFocus 描述投标文件需要重点呈现的内容，但不能创造招标文件没有的评分条件。
4. relatedRequirementIds 只能引用输入 requirements 中已经存在的 id。
5. sourceLocator 必须标记评标办法、评分表、页码或表格位置。
6. 最多返回 100 个评分项；同一评分点的分档标准合并到 criteria，不要逐档拆成多条。
""".strip()


def template_detect_prompt(
    *,
    document_text: str,
    document_structure: dict[str, Any],
    filename: str,
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 TemplateDetectAgent，负责识别投标文件组成、分册规则、模板和固定表格。

只依据招标文件，不得把普通说明文字当作投标模板。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context={})}

【文档结构】
{_dump(document_structure, 30000)}

【招标文件文本】
{_dump(document_text, 30000)}

输出结构：
{_dump(TEMPLATE_SCHEMA, 12000)}

模板判断规则：
1. “投标文件格式”“响应文件组成”“投标函格式”“偏离表”“报价表”“承诺函”等优先判断为模板线索。
2. fixed_template：原样保留格式，只允许填充字段。
3. chapter_skeleton：保留章节标题、层级、表格和提示结构，正文可由智能体填充。
4. reference_example：只能作为写作参考，不得直接复制为企业承诺。
5. editableFields 记录字段名称、类型和建议来源，例如 projectMeta.projectName、company.name。
6. lockedBlocks 记录必须保留的固定文字、签字盖章位置、表头和格式要求。
7. requiredOutline 中的 templateId 必须引用 templates 中已存在的 templateId。
8. 最多返回 24 个 requiredOutline 项、12 个 templates 和 20 个 requiredTables，重复模板合并；每个模板的 editableFields、lockedBlocks 和 blocks 都只保留关键项，避免复制大段原文。
9. submissionStructure 中先列明招标文件要求的各卷/文件组成及顺序，再判断 mode：明确要求分别编制或独立提交才使用 separate；明确要求合卷才使用 single；没有原文依据用 unspecified。separate/single 必须有精确 sourceLocator，不能把普通的商务/技术章节标题当成独立分册要求。每卷记录来源位置、items 和关联的 formatItemIds；items 中保留招标文件原文列出的每一份文件。
10. 根节点必须包含 hasExplicitFormat、submissionStructure、requiredOutline、templates、requiredTables 和 warnings；未识别到分册规则时可以返回 mode=unspecified，但如果原文明确列出卷和卷内文件，必须保留 volumes[].items，不能因为无法判断是否独立提交而清空卷清单。
11. 如果输入的 submissionStructure 已有明确卷名、文件条目、顺序和 formatItemId，这些字段是权威值；不得改名、删除、合并或重复生成。只能补充 itemType、responseType、conditional、templateType、templateId 等空属性。
""".strip()


def parse_review_prompt(
    *,
    project_meta: dict[str, Any],
    document_structure: dict[str, Any],
    requirements: list[dict[str, Any]],
    evaluation_items: list[dict[str, Any]],
    bid_format_spec: dict[str, Any],
    filename: str,
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 ParseReviewAgent，负责检查前面解析智能体的结果质量。

只做解析质检，不要重新编写完整解析结果，不得使用外部知识。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
发现不确定内容必须报告，不要用猜测掩盖缺失。

{_agent_context(filename=filename, workspace_id=workspace_id, project_id=project_id, project_context={})}

【项目基础信息】
{_dump(project_meta, 12000)}

【文档结构】
{_dump(document_structure, 30000)}

【招标要求】
{_dump(requirements, 65000)}

【评分项】
{_dump(evaluation_items, 30000)}

【投标模板】
{_dump(bid_format_spec, 36000)}

输出结构：
{_dump(PARSE_REVIEW_SCHEMA, 9000)}

检查内容：
1. 项目名称是否缺失或与正文冲突。
2. 是否抽取到资格、技术、商务、服务、废标/否决要求。
3. 评分项是否有分值、来源位置或明显缺失。
4. 投标文件格式是否识别到固定模板和表格。
5. requirements 是否存在重复、空内容、无来源位置或错误分类。
6. riskItems 只记录会影响后续目录、撰写或投标合规的风险。
""".strip()


def parse_prompt(
    *,
    document_text: str,
    filename: str,
    parser_warnings: list[str],
    workspace_id: str,
    project_id: str,
    project_context: dict[str, Any],
) -> str:
    return f"""
你是招投标文件结构化解析智能体。

只依据下面提供的招标文件文本进行抽取，不得使用外部知识，不得编造。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或前后缀。
缺失字段使用空字符串、空数组或 null。
每条 requirements 必须有唯一 id，例如 req-001。
outline.requirementIds 只能引用 requirements 中已经存在的 id。

任务：
1. 抽取项目基本信息。
2. 从“投标文件格式”“响应文件组成”“投标文件编制要求”等内容抽取响应目录。
3. 抽取关键要求、强制要求、技术要求、商务要求、资格要求和废标/否决项。
4. 抽取评分办法、评分因素、评分点、分值和评分标准。
5. 抽取无法确认、可能导致废标或需要人工核验的风险。

强制规则：
- ★、必须、应当、不得、否则无效、废标、否决、实质性不响应等内容，按实际语义标记 mandatory 或 isRejection。
- 资质、业绩、人员、财务、信用、区域、技术、服务方案、商务、文件格式、报价、合同分别归入合适 category。
- sourceLocator 必须尽量写章节、页码、表格名称或原文位置。
- 不要把整段招标文件复制进结果，只保留可执行的原子要求。

文件名：
{filename}

工作区：
{workspace_id}

项目：
{project_id}

项目上下文：
{_dump(project_context, 4000)}

本地解析器提示：
{_dump(parser_warnings, 4000)}

招标文件文本：
{document_text}

输出结构示例：
{_dump(PARSE_SCHEMA, 12000)}
""".strip()
