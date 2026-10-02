# -*- coding: utf-8 -*-
"""智能体规格表（AgentSpec）：把一个智能体「是什么、吃什么、吐什么、能用什么工具」固化下来。

为什么需要它（架构改造方案 Wave 0 · 0.1）：
  目前 16 个智能体的这些信息散落在三处——提示词函数名、`PiClient` 方法名、
  编排代码里写死的 `agent_id` 字符串与「取哪个字段」的兜底逻辑。
  任何一次并行改造（换模型、加工具、改产物结构、做可恢复运行）都要先在脑子里
  把这些关系重建一遍，而且没有任何地方能回答「改这个 agent 会影响谁」。

这里只做**登记与自检**，不改变任何运行时行为：
  - 编排层将来在记账、重试、幂等、可恢复运行时读它；
  - 测试 `test_agent_runtime_contracts.py` 用它做交叉自检（client 方法必须存在、
    工具必须在白名单内、workflow 必须是已定义工序）。

维护约定：新增智能体时必须同时登记 spec，否则自检会失败。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: 工序模板：一个智能体属于哪一类「工序」，决定它需要哪些护栏。
WORKFLOW_TEMPLATES: dict[str, str] = {
    "extract": "从招标文件原文抽取结构化事实（只允许引用原文，不允许编造）",
    "route": "从既有集合中选择/归并/组织（不做新抽取，只做取舍与层级）",
    "map": "把集合 A 逐项映射到集合 B（条款→章节、要求→响应行）",
    "draft_critic": "先起草再自检（正文类，允许创作但必须可核验）",
    "fill": "结构化填充（模板字段、响应表单元格，值必须可溯源）",
    "review": "审查判定（只输出问题与依据，不产出正文）",
}

#: 产物类型：决定下游怎么消费、以及是否需要保真基线。
ARTIFACT_KINDS: dict[str, str] = {
    "structure": "结构骨架（卷/章节/格式条目层级）",
    "facts": "事实集合（条款、评分项、元信息）",
    "plan": "计划（目录、撰写提纲）",
    "text": "正文文本",
    "table": "表格数据",
    "report": "审查/质检报告",
}


class AgentSpec(BaseModel):
    """一个智能体的静态规格。"""

    model_config = ConfigDict(frozen=True, extra="allow")

    agent_id: str
    title: str
    stage: str
    workflow: str
    artifact_kind: str
    #: 规范化产物名（落进 bidDocument / ParseBundle 的字段名）
    artifact: str
    #: 对应 `PiClient` 上的方法名（自检用：必须真实存在）
    client_method: str
    #: 提示词构造函数名（`pi_agent.prompts` 上的名字，自检用：必须真实存在）
    prompt_builder: str
    #: 该智能体的主要输入（人读的描述，供方案与前端展示）
    inputs: tuple[str, ...] = Field(default_factory=tuple)
    #: 允许挂载的工具（必须落在 `outline_agent_context.OUTLINE_TOOL_ALLOWLIST` 内）
    tools: tuple[str, ...] = Field(default_factory=tuple)
    #: 是否会写入 bidDocument / ParseBundle（决定是否进入持久化关切）
    writes_artifact: bool = True
    #: 智能体不可用时的确定性兜底（方案要求：任何 agent 都必须有兜底，不能只报错）
    fallback: str = ""
    #: 备注：已知风险或待收敛行为
    notes: str = ""


AGENT_SPECS: tuple[AgentSpec, ...] = (
    # ── 解析阶段 ────────────────────────────────────────────────────
    AgentSpec(
        agent_id="DocumentStructureAgent",
        title="文档结构解析",
        stage="parse",
        workflow="extract",
        artifact_kind="structure",
        artifact="documentStructure",
        client_method="parse_document_structure",
        prompt_builder="document_structure_prompt",
        inputs=("招标文件原文",),
        fallback="解析器给出的章节树",
    ),
    AgentSpec(
        agent_id="ProjectMetaAgent",
        title="项目元信息抽取",
        stage="parse",
        workflow="extract",
        artifact_kind="facts",
        artifact="projectMeta",
        client_method="extract_project_meta",
        prompt_builder="project_meta_prompt",
        inputs=("招标文件原文", "文档结构"),
        fallback="正则/解析器提取的项目名称与编号",
    ),
    AgentSpec(
        agent_id="RequirementExtractAgent",
        title="条款抽取",
        stage="parse",
        workflow="extract",
        artifact_kind="facts",
        artifact="requirements",
        client_method="extract_requirements",
        prompt_builder="requirement_extract_prompt",
        inputs=("招标文件原文", "文档结构", "项目元信息"),
        fallback="章节级条款兜底抽取",
    ),
    AgentSpec(
        agent_id="EvaluationExtractAgent",
        title="评分项抽取",
        stage="parse",
        workflow="extract",
        artifact_kind="facts",
        artifact="evaluationItems",
        client_method="extract_evaluations",
        prompt_builder="evaluation_extract_prompt",
        inputs=("招标文件原文", "文档结构", "条款集合"),
        fallback="评分表解析失败时产出空集并告警",
    ),
    AgentSpec(
        agent_id="TemplateDetectAgent",
        title="投标格式识别",
        stage="parse",
        workflow="extract",
        artifact_kind="structure",
        artifact="bidFormatSpec",
        client_method="detect_templates",
        prompt_builder="template_detect_prompt",
        inputs=("招标文件原文", "文档结构"),
        fallback="提交结构（submissionStructure）推导的格式清单",
    ),
    AgentSpec(
        agent_id="ParseReviewAgent",
        title="解析质检",
        stage="parse",
        workflow="review",
        artifact_kind="report",
        artifact="parseReviewReport",
        client_method="review_parse",
        prompt_builder="parse_review_prompt",
        inputs=("项目元信息", "文档结构", "条款集合", "评分项", "格式清单"),
        fallback="输出空报告，不阻断解析落库",
    ),
    # ── 规划阶段 ────────────────────────────────────────────────────
    AgentSpec(
        agent_id="VolumePlanAgent",
        title="分册规划",
        stage="plan",
        workflow="route",
        artifact_kind="structure",
        artifact="volumePlan",
        client_method="plan_volumes_agent",
        prompt_builder="volume_plan_prompt",
        inputs=("ParseBundle", "既有目录"),
        fallback="按 submissionStructure 直接成卷",
        notes="卷集合来自招标文件原文，智能体只做归并，不得新增/删除卷",
    ),
    AgentSpec(
        agent_id="OutlinePlanAgent",
        title="目录生成",
        stage="plan",
        workflow="route",
        artifact_kind="plan",
        artifact="outlinePlan",
        client_method="plan_outline",
        prompt_builder="outline_plan_prompt",
        inputs=("ParseBundle", "既有目录"),
        tools=("bid_outline_signals", "search_requirements", "get_evaluation_points",
               "get_submission_structure", "validate_outline_candidate"),
        fallback="原文骨架目录（keep_derived=False）",
        notes="唯一被细化的智能体：可用只读工具自查；新增章节（derived）ID 由 Python 稳定生成",
    ),
    AgentSpec(
        agent_id="RequirementMapAgent",
        title="条款-章节映射",
        stage="plan",
        workflow="map",
        artifact_kind="plan",
        artifact="requirementCoverage",
        client_method="map_requirements",
        prompt_builder="requirement_map_prompt",
        inputs=("ParseBundle", "目录"),
        fallback="按章节标题关键词匹配",
        notes="按条款分批并行调用，agent_id 会带 [i/n] 后缀，resolve_spec 会归一",
    ),
    # ── 正文阶段 ────────────────────────────────────────────────────
    AgentSpec(
        agent_id="MaterialMatchAgent",
        title="素材匹配",
        stage="draft",
        workflow="route",
        artifact_kind="plan",
        artifact="materialMatches",
        client_method="match_materials",
        prompt_builder="material_match_prompt",
        inputs=("ParseBundle", "章节", "公司资料", "知识库"),
        fallback="按 requirementId 直接命中 known 链路（fineReview）",
    ),
    AgentSpec(
        agent_id="SectionPlanAgent",
        title="本章提纲",
        stage="draft",
        workflow="draft_critic",
        artifact_kind="plan",
        artifact="writingPlan",
        client_method="plan_section",
        prompt_builder="section_plan_prompt",
        inputs=("ParseBundle", "章节", "素材匹配结果"),
        fallback="按条款顺序生成默认提纲",
    ),
    AgentSpec(
        agent_id="SectionDraftAgent",
        title="正文起草",
        stage="draft",
        workflow="draft_critic",
        artifact_kind="text",
        artifact="sectionContent",
        client_method="draft_section_agent",
        prompt_builder="section_draft_prompt",
        inputs=("ParseBundle", "章节", "本章提纲", "素材匹配结果"),
        fallback="结构化占位（标注待补），不静默产出空正文",
    ),
    AgentSpec(
        agent_id="SectionRewriteAgent",
        title="按指令改写",
        stage="draft",
        workflow="draft_critic",
        artifact_kind="text",
        artifact="sectionContent",
        client_method="rewrite_section",
        prompt_builder="section_rewrite_prompt",
        inputs=("ParseBundle", "章节", "当前正文", "改写指令", "选中文本"),
        fallback="保留原文并回报失败原因",
    ),
    # ── 填充阶段 ────────────────────────────────────────────────────
    AgentSpec(
        agent_id="TableFormFillAgent",
        title="模板字段填充",
        stage="fill",
        workflow="fill",
        artifact_kind="table",
        artifact="templateFragmentFields",
        client_method="fill_template_fields",
        prompt_builder="table_form_fill_prompt",
        inputs=("ParseBundle", "模板片段", "章节", "公司资料"),
        fallback="已知字段用知识库直填，未知字段留空并标记",
        notes="模板保真红线：只填值，不得增删段落与表格结构",
    ),
    AgentSpec(
        agent_id="RequirementResponseTableAgent",
        title="要求逐条响应表",
        stage="fill",
        workflow="fill",
        artifact_kind="table",
        artifact="requirementResponseRows",
        client_method="draft_requirement_responses",
        prompt_builder="requirement_response_table_prompt",
        inputs=("章节", "条款集合"),
        fallback="未命中素材的行标记为需人工确认",
    ),
    # ── 审查阶段 ────────────────────────────────────────────────────
    AgentSpec(
        agent_id="ComplianceReviewAgent",
        title="合规审查",
        stage="review",
        workflow="review",
        artifact_kind="report",
        artifact="complianceFindings",
        client_method="review_compliance",
        prompt_builder="compliance_review_prompt",
        inputs=("ParseBundle", "投标文件", "fineReview", "基线审查"),
        fallback="只报可定位的问题，不产出正文",
    ),
)

_SPEC_BY_ID: dict[str, AgentSpec] = {spec.agent_id: spec for spec in AGENT_SPECS}


def normalize_agent_id(agent_id: str) -> str:
    """去掉运行期标签后缀：``RequirementMapAgent[1/3]`` → ``RequirementMapAgent``。"""

    text = str(agent_id or "").strip()
    if "[" in text:
        text = text.split("[", 1)[0]
    return text


def resolve_spec(agent_id: str) -> AgentSpec | None:
    """按 agent_id（允许带 ``[i/n]``、``[质量修正]`` 等后缀）取规格。"""

    return _SPEC_BY_ID.get(normalize_agent_id(agent_id))


def get_spec(agent_id: str) -> AgentSpec:
    """取规格，取不到直接报错——登记缺失必须显式暴露，不能静默降级。"""

    spec = resolve_spec(agent_id)
    if spec is None:
        raise KeyError(f"未登记的智能体：{agent_id}")
    return spec


def all_specs() -> tuple[AgentSpec, ...]:
    return AGENT_SPECS


def specs_for_stage(stage: str) -> tuple[AgentSpec, ...]:
    return tuple(spec for spec in AGENT_SPECS if spec.stage == stage)


def spec_ids() -> tuple[str, ...]:
    return tuple(spec.agent_id for spec in AGENT_SPECS)


def spec_summary() -> list[dict[str, Any]]:
    """给前端/方案展示用的摘要（不含提示词正文）。"""

    return [
        {
            "agentId": spec.agent_id,
            "title": spec.title,
            "stage": spec.stage,
            "workflow": spec.workflow,
            "workflowLabel": WORKFLOW_TEMPLATES.get(spec.workflow, spec.workflow),
            "artifactKind": spec.artifact_kind,
            "artifact": spec.artifact,
            "tools": list(spec.tools),
            "writesArtifact": spec.writes_artifact,
            "fallback": spec.fallback,
            "notes": spec.notes,
        }
        for spec in AGENT_SPECS
    ]


# ── Wave 1 · A 追加（只增不改：上面所有字段的语义保持不变）──────────────

#: 产物 → 该产物「示例 schema」的出处（提示词里的 `*_SCHEMA` 常量，或提示词内联样例）。
#: 只做登记，供偏差扫描与文档引用，不参与任何运行时判定。
#: 注意 `volumePlan` 没有独立常量（样例内联在提示词正文里），这是偏差扫描发现的
#: 契约缺口之一，登记在此以便 Wave 2 收敛。
ARTIFACT_SCHEMA_SOURCES: dict[str, str] = {
    "documentStructure": "prompts.parse.DOCUMENT_STRUCTURE_SCHEMA",
    "projectMeta": "prompts.parse.PROJECT_META_SCHEMA",
    "requirements": "prompts.parse.REQUIREMENT_SCHEMA",
    "evaluationItems": "prompts.parse.EVALUATION_SCHEMA",
    "bidFormatSpec": "prompts.parse.TEMPLATE_SCHEMA",
    "parseReviewReport": "prompts.parse.PARSE_REVIEW_SCHEMA",
    "volumePlan": "prompts.plan.volume_plan_prompt 内联样例（plan.py:135）",
    "outlinePlan": "prompts.plan.OUTLINE_PLAN_SCHEMA",
    "requirementCoverage": "prompts.plan.REQUIREMENT_MAP_SCHEMA",
    "materialMatches": "prompts.draft.MATERIAL_MATCH_SCHEMA",
    "writingPlan": "prompts.draft.SECTION_PLAN_SCHEMA",
    "sectionContent": "prompts.draft.SECTION_DRAFT_SCHEMA / SECTION_REWRITE_SCHEMA",
    "templateFragmentFields": "prompts.fill.TABLE_FORM_FILL_SCHEMA",
    "requirementResponseRows": "prompts.fill.REQUIREMENT_RESPONSE_TABLE_SCHEMA",
    "complianceFindings": "prompts.review.COMPLIANCE_REVIEW_SCHEMA",
}


def artifact_for(agent_id: str) -> str:
    """取智能体的规范化产物名；未登记时返回空串（不抛错，供只读场景使用）。"""

    spec = resolve_spec(agent_id)
    return spec.artifact if spec is not None else ""


def agents_for_artifact(artifact: str) -> tuple[str, ...]:
    """反查：某个产物由哪些智能体写（改产物结构时用来回答「会影响谁」）。"""

    name = str(artifact or "")
    return tuple(spec.agent_id for spec in AGENT_SPECS if spec.artifact == name)


__all__ = [
    "AGENT_SPECS",
    "ARTIFACT_KINDS",
    "ARTIFACT_SCHEMA_SOURCES",
    "WORKFLOW_TEMPLATES",
    "AgentSpec",
    "agents_for_artifact",
    "all_specs",
    "artifact_for",
    "get_spec",
    "normalize_agent_id",
    "resolve_spec",
    "spec_ids",
    "spec_summary",
    "specs_for_stage",
]
