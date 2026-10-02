# -*- coding: utf-8 -*-
"""BidSection 显式模型（Wave 0 · 0.1）：把「一个章节到底有哪些字段」写下来。

为什么需要它：
  `bidDocument.plan[]` 的章节是纯字典，字段随实现演进一路追加
  （derived* 四个字段、requirementResponseRows、sourceSectionId、writingProfile…），
  没有任何地方能回答「一个章节最少要有什么」。并行改造时，前端、导出、审查
  各自假设不同的字段集，容易出现「某个会话新增的字段被另一个会话的归一化吃掉」。

设计（遵循方案 §3.1 的容错原则）：
  - 显式声明**核心字段**（下游真正依赖的），其余一律允许（``extra="allow"``）；
  - 不做值域转换、不做默认值注入：``build_section_model`` → ``to_section``
    必须**逐字段原样往返**，只用于读、不用于改；
  - 缺字段用 ``missing_required_fields`` 报出来，由调用方决定是补默认值还是记账，
    不在模型层静默补齐（静默补齐正是历史上标题/字段被覆盖那类 bug 的温床）。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: 下游真正依赖的字段：前端渲染、导出、审查、正文生成都会读
SECTION_REQUIRED_FIELDS: tuple[str, ...] = (
    "sectionId",
    "title",
    "source",
    "kind",
    "parentSectionId",
    "level",
    "order",
    "required",
    "responseType",
)


class BidSectionModel(BaseModel):
    """一个目录节点的显式模型（容错：未知字段保留）。"""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    # sectionId 也给默认值：读模型遇到脏数据/半成品段落时**不得抛异常**，
    # 缺什么由 missing_required_fields() 报出来。
    sectionId: str = ""
    title: str = ""
    source: str = "ai"
    kind: str = "technical"
    parentSectionId: str = ""
    level: int = 1
    order: int = 0
    required: bool = True
    responseType: str = "chapter_text"

    # 撰写相关（生成后才有）
    content: str = ""
    status: str = "pending"
    writingProfile: str = ""
    # 集合类字段一律 list[Any]：这个模型只负责「读得下来」，
    # 严格的类型/取值校验属于 validation.py，读模型绝不因为脏数据而拒绝真实数据。
    materialScopes: list[Any] = Field(default_factory=list)
    requirementIds: list[Any] = Field(default_factory=list)
    evaluationIds: list[Any] = Field(default_factory=list)
    sourceRefs: list[Any] = Field(default_factory=list)
    templateId: str = ""
    sourceSectionId: str = ""
    sourceLocator: str = ""

    # 智能体新增章节（B 类：投标人主动发挥）
    derived: bool = False
    derivedOrigin: str = ""
    derivedArchetype: str = ""
    derivedBasis: str = ""

    def to_section(self, *, drop_unset: bool = True) -> dict[str, Any]:
        """回到字典。

        ``drop_unset=True``（默认）：**只输出实际存在的字段**——原字典里没有的
        可选字段不会被补成空串，这样「读一遍再写回」才可能逐字段相等
        （历史上正是这类顺手补默认值/覆盖字段的写法造成过 bug）。
        ``drop_unset=False``：输出完整字段集，用于**新建**章节时取全量默认值。
        """

        return self.model_dump(exclude_unset=drop_unset)


def build_section_model(section: dict[str, Any]) -> BidSectionModel:
    """从章节字典构造模型。缺字段走模型默认值，但**不修改**原字典。"""

    return BidSectionModel(**dict(section or {}))


def missing_required_fields(section: dict[str, Any]) -> list[str]:
    """列出缺失的核心字段（只报缺失，不改数据）。"""

    data = section or {}
    return [key for key in SECTION_REQUIRED_FIELDS if key not in data]


def round_trip(section: dict[str, Any]) -> dict[str, Any]:
    """读一遍再写回，用于保真测试：结果必须与原字典完全相等。"""

    return build_section_model(section).to_section()


__all__ = [
    "SECTION_REQUIRED_FIELDS",
    "BidSectionModel",
    "build_section_model",
    "missing_required_fields",
    "round_trip",
]
