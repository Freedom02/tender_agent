# -*- coding: utf-8 -*-
"""章节写作蓝图（SectionBlueprint）：告诉智能体「这一类章节该怎么写」。

画像（profile）只解决语气与材料范围的粗粒度差异；本模块补上章节级范式：
每个技术章节对应一份「骨架 + 写作要点 + 证据槽位 + 篇幅策略 + 陷阱」的蓝图。
蓝图是通用范式（scope），不含任何企业事实——正文里的企业名称/金额/人员/业绩/参数
必须由下游智能体引用真实材料并挂 sourceRefs，证据槽位查无材料时写【待补充】。

蓝图归位口径复用 planning.match_eval_section 的关键词表，与 evaluationStrategy /
plan_from_eval 对同一章节的归属保持一致。
"""
from __future__ import annotations

from typing import Any

# 蓝图结构：blueprintId 由 dict 的 key 决定，不写在 value 里。
SECTION_BLUEPRINTS: dict[str, dict[str, Any]] = {
    "t-01": {
        "name": "项目理解",
        "focus": "复述采购需求 → 提炼建设目标与重难点 → 给出总体应对思路；只做理解与呼应，不展开具体技术实现。",
        "skeleton": ["需求理解与建设目标", "项目重难点分析", "总体应对思路"],
        "evidenceSlots": ["capabilities"],
        "pitfalls": [
            "不得整段照抄招标原文，需转述为投标人视角",
            "能力断言必须来自已提供材料，无材料写【待补充】",
        ],
        "lengthPolicy": "常规章：简明扼要，呼应需求即可",
    },
    "t-02": {
        "name": "技术方案",
        "focus": "总体技术架构 + 功能实现 + 关键技术路线 + 与招标要求的逐项响应；通常是分值最高章，写深写足。",
        "skeleton": [
            "总体设计思路",
            "总体技术架构",
            "功能实现方案",
            "关键技术路线",
            "与招标要求的逐项响应",
        ],
        "evidenceSlots": ["technical_product", "capabilities", "technical_parameter", "credentials"],
        "pitfalls": [
            "不要逐字复述参数表，改为能力化表达并挂 sourceRefs",
            "每个技术论断必须引用真实材料，无材料写【待补充】",
        ],
        "lengthPolicy": "重点章：按评分点分值倾斜篇幅，逐条响应",
    },
    "t-03": {
        "name": "实施方案",
        "focus": "实施组织、进度计划、交付保障；用里程碑与角色分工体现可落地性。",
        "skeleton": ["实施组织与人员安排", "进度计划与里程碑", "交付保障措施"],
        "evidenceSlots": ["personnel", "projectReferences"],
        "pitfalls": [
            "工期/进度承诺不得超出招标要求",
            "人员必须来自已提供人员库，不得虚构",
        ],
        "lengthPolicy": "常规章：里程碑清晰、分工明确",
    },
    "t-04": {
        "name": "质量保证措施",
        "focus": "质量控制体系、过程质量控制、验收配合与质保承诺；体现过程可管可控。",
        "skeleton": ["质量管理体系", "过程质量控制", "验收配合与质保承诺"],
        "evidenceSlots": ["qualifications", "credentials"],
        "pitfalls": [
            "质保期不得低于招标要求",
            "不得虚构认证/资质，须来自材料",
        ],
        "lengthPolicy": "常规章：措施可执行、承诺可兑现",
    },
    "t-05": {
        "name": "售后服务方案",
        "focus": "服务承诺、响应时限、服务流程、备品备件、培训与考核；承诺值须与招标要求一致或更优。",
        "skeleton": [
            "服务承诺",
            "服务响应时限",
            "服务流程",
            "备品备件",
            "培训与知识转移",
            "考核与违约承诺",
        ],
        "evidenceSlots": ["capabilities", "technical_product"],
        "pitfalls": [
            "响应时限等承诺值须≥招标要求",
            "处罚条款不得与合同/招标文件冲突",
        ],
        "lengthPolicy": "中等章：按评分点决定是否扩展",
    },
    "t-06": {
        "name": "培训方案",
        "focus": "培训对象、内容、频次与考核；体现交付后的能力转移。",
        "skeleton": ["培训目标与对象", "培训内容", "培训安排与考核"],
        "evidenceSlots": ["personnel"],
        "pitfalls": ["培训次数/时长不得低于招标要求"],
        "lengthPolicy": "次要章：点到为止",
    },
    "t-07": {
        "name": "应急保障方案",
        "focus": "风险识别、应急组织、应急预案与响应机制；体现突发情况兜底能力。",
        "skeleton": ["风险识别与分级", "应急组织与职责", "应急预案与响应流程"],
        "evidenceSlots": ["capabilities"],
        "pitfalls": ["应急承诺不得与实际能力脱节"],
        "lengthPolicy": "次要章：点到为止",
    },
}

# 无具体蓝图命中的技术论证章兜底。
GENERIC_TECHNICAL_BLUEPRINT: dict[str, Any] = {
    "name": "技术论证章",
    "focus": "围绕本章要求给出论证性响应，逐条对应要求并引用真实材料。",
    "skeleton": ["总体说明", "具体方案", "与招标要求的对应关系"],
    "evidenceSlots": ["capabilities"],
    "pitfalls": ["不得编造企业事实，逐条响应要求", "无材料写【待补充】"],
    "lengthPolicy": "常规章",
}


def resolve_section_blueprint(section: dict[str, Any] | None) -> dict[str, Any]:
    """解析章节对应的写作蓝图。命中返回蓝图 dict（含 blueprintId），否则返回 {}。

    归位顺序：sectionId 精确命中固定池 → 标题关键词命中 → 技术章节兜底。
    """
    value = section if isinstance(section, dict) else {}
    section_id = str(value.get("sectionId") or "")

    if section_id in SECTION_BLUEPRINTS:
        return {"blueprintId": section_id, **SECTION_BLUEPRINTS[section_id]}

    title = str(value.get("title") or "")
    if title:
        from .planning import match_eval_section

        target = match_eval_section(title)
        if target and target in SECTION_BLUEPRINTS:
            return {"blueprintId": target, **SECTION_BLUEPRINTS[target]}

    if str(value.get("kind") or "").strip() == "technical":
        return {"blueprintId": "generic", **GENERIC_TECHNICAL_BLUEPRINT}

    return {}


def attach_section_blueprints(bid_document: dict[str, Any]) -> dict[str, Any]:
    """给规划章节打写作蓝图注解 + 在 bidDocument 挂 sectionBlueprints 摘要。

    章节新增字段：
      sectionBlueprint: {blueprintId, name, focus, skeleton, pitfalls, lengthPolicy}
      （evidenceSlots 供确定性材料注入使用，不进入 prompt 视图）
    """
    plan = bid_document.get("plan")
    plan = plan if isinstance(plan, list) else []

    summary: dict[str, dict[str, Any]] = {}
    for section in plan:
        if not isinstance(section, dict):
            continue
        blueprint = resolve_section_blueprint(section)
        if not blueprint:
            continue
        section_id = str(section.get("sectionId") or "")
        section["sectionBlueprint"] = {
            key: blueprint[key]
            for key in (
                "blueprintId",
                "name",
                "focus",
                "skeleton",
                "pitfalls",
                "lengthPolicy",
            )
            if key in blueprint
        }
        summary[section_id] = {
            "blueprintId": blueprint["blueprintId"],
            "name": blueprint["name"],
            "depthPolicy": str(section.get("evaluationDepth") or ""),
        }

    bid_document["sectionBlueprints"] = summary
    return summary
