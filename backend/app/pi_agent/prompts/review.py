# -*- coding: utf-8 -*-
"""审查阶段：合规审查。

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
COMPLIANCE_REVIEW_SCHEMA = {
    "status": "needs_review",
    "summary": "需要人工复核的风险摘要",
    "coverage": {
        "requirementTotal": 0,
        "requirementCovered": 0,
        "mandatoryTotal": 0,
        "mandatoryCovered": 0,
        "evaluationTotal": 0,
        "evaluationCovered": 0,
        "uncoveredRequirementIds": [],
        "uncoveredEvaluationIds": [],
    },
    "riskItems": [
        {
            "riskId": "risk-001",
            "level": "高",
            "category": "漏响应",
            "title": "风险标题",
            "detail": "风险说明",
            "sectionId": "",
            "requirementId": "",
            "evaluationId": "",
            "sourceLocator": "",
            "suggestion": "处理建议",
            "sourceRefs": [],
        }
    ],
    "warnings": [],
}



def compliance_review_prompt(
    *,
    parse_bundle: dict[str, Any],
    bid_document: dict[str, Any],
    fine_review: dict[str, Any],
    baseline_review: dict[str, Any],
    project: dict[str, Any],
    company: dict[str, Any],
    knowledge: dict[str, Any],
    allowed_section_ids: list[str],
    allowed_requirement_ids: list[str],
    allowed_evaluation_ids: list[str],
    workspace_id: str,
    project_id: str,
) -> str:
    return f"""
你是 ComplianceReviewAgent，负责审查一份投标响应文件是否覆盖招标要求、评分点和模板约束。

只做审查，不修改正文、不生成替代正文、不填充模板。
只依据下面的压缩数据和允许 ID；不得引入外部要求或把推测写成事实。
固定模板、表头、签字盖章位置和锁定文字不视为可修改内容。
只输出合法 JSON，不要输出 Markdown、解释或额外文字。

工作区：{workspace_id}
项目：{project_id}

【允许的章节 ID】
{_dump(allowed_section_ids, 5000)}

【允许的要求 ID】
{_dump(allowed_requirement_ids, 7000)}

【允许的评分项 ID】
{_dump(allowed_evaluation_ids, 7000)}

【解析摘要】
{_dump(_compact_parse_bundle_for_review(parse_bundle), 42000)}

【投标文件摘要】
{_dump(_compact_bid_document_for_review(bid_document), 48000)}

【要求核验摘要】
{_dump(_compact_fine_review(fine_review), 36000)}

【确定性基础校对结果】
{_dump(baseline_review, 12000)}

【项目关键字段】
{_dump(_compact_project(project), 3500)}

【企业关键字段】
{_dump(_compact_company(company), 5000)}

【企业材料】
{_dump(_section_knowledge_view(knowledge, writing_profile="commercial", max_items_per_group=12), 14000)}

输出结构：
{_dump(COMPLIANCE_REVIEW_SCHEMA, 9000)}

审查规则：
1. 只有目录存在且章节有实际正文/表格响应，才能计入 requirementCovered。
2. mandatoryCovered 只统计 mandatory=true 且已有实际响应的要求。
3. evaluationCovered 只统计正文或表格明确关联 evaluationId 的评分项。
4. 对漏响应、强制/废标风险、负偏离、待补材料、模板待填和关键信息不一致分别列风险。
5. 每条风险包含 level、category、title、detail、可定位 ID 和 suggestion。
6. 没有 sourceRefs 的企业事实仍需人工核验。
7. 存在高风险或未覆盖强制项时，status 不能为 pass。
""".strip()
