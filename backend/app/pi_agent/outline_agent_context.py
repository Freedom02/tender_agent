# -*- coding: utf-8 -*-
"""目录规划智能体的工具上下文（M2′）。

把「智能体需要用工具去查的东西」一次性快照成一个小 JSON 文件，路径经环境变量
``BID_OUTLINE_CONTEXT`` 传给 Pi 子进程，由扩展
``backend/app/pi_agent/extensions/bid-outline-tools.ts`` 里注册的**只读工具**按需查询。

设计取舍：
- 智能体**按需取证**，不必把两百多条条款全塞进提示词（提示词只给摘要 + 工具说明）；
- 数据快照在 Python 侧生成（单一事实源），工具只做检索与自检，不重复实现业务判断；
- 不开放通用 ``read``/``bash``/``write``/``edit`` 工具：工具面最小、只读，招标文件里的
  提示词注入拿不到本机文件（例如 ``agent-config.json`` 里的 API Key）。

技能正文（``skills/bid-outline/SKILL.md``）由 Python 读出来注入提示词，而不是让模型用
``read`` 按需加载 —— 这是「不开放 read」的代价，换来的是工具面最小；技能文件本身仍是
标准 Agent Skills 布局，改技能 = 改 markdown，不动 Python。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from ..bid_writing.outline_diagnosis import (
    diagnose,
    index_plan_bindings,
    requirement_domain_of,
    requirement_id_of,
    requirement_text_of,
)

#: 暴露给目录智能体的只读工具（与 extensions/bid-outline-tools.ts 中的 name 一一对应）
OUTLINE_TOOL_NAMES: tuple[str, ...] = (
    "bid_outline_signals",
    "search_requirements",
    "get_evaluation_points",
    "get_submission_structure",
    "validate_outline_candidate",
)

#: 只有这些工具可用：不放开 read/bash/write/edit/powershell。
OUTLINE_TOOL_ALLOWLIST = ",".join(OUTLINE_TOOL_NAMES)

#: 策略档位：每卷 / 全篇的补充章节上限，以及是否允许「自由发挥」类章节。
RESPONSE_SECTION_POLICIES: dict[str, dict[str, Any]] = {
    "strict": {"maxPerVolume": 0, "maxTotal": 0, "allowInitiative": False},
    "balanced": {"maxPerVolume": 4, "maxTotal": 12, "allowInitiative": True},
    "rich": {"maxPerVolume": 6, "maxTotal": 24, "allowInitiative": True},
}

_MAX_REQUIREMENTS = 400
_REQUIREMENT_TEXT_LIMIT = 160


def skill_dir() -> Path:
    return Path(__file__).resolve().parent / "skills" / "bid-outline"


def extension_path() -> Path:
    return Path(__file__).resolve().parent / "extensions" / "bid-outline-tools.ts"


def skill_body() -> str:
    """技能正文（去掉 frontmatter），供提示词内联。"""

    path = skill_dir() / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    body = re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.S)
    return body.strip()


def resolve_policy(config: dict[str, Any] | None = None, override: str = "") -> dict[str, Any]:
    """解析补充章节策略：显式入参 > 环境变量 > agent-config.json > balanced。"""

    name = str(override or "").strip().lower()
    if not name:
        name = os.getenv("BID_RESPONSE_SECTION_POLICY", "").strip().lower()
    if not name:
        payload = config if isinstance(config, dict) else _project_config()
        bid_writing = payload.get("bidWriting") if isinstance(payload, dict) else None
        if isinstance(bid_writing, dict):
            name = str(bid_writing.get("responseSectionPolicy") or "").strip().lower()
    if name not in RESPONSE_SECTION_POLICIES:
        name = "balanced"
    spec = dict(RESPONSE_SECTION_POLICIES[name])
    spec["name"] = name
    spec["enabled"] = int(spec["maxTotal"]) > 0
    return spec


def _project_config() -> dict[str, Any]:
    """读取项目根的 agent-config.json（与 PiConfig 的查找口径一致），失败则返回空。"""

    candidates = []
    working_dir = os.getenv("PI_WORKING_DIR", "").strip()
    if working_dir:
        candidates.append(Path(working_dir) / "agent-config.json")
    candidates.append(Path.cwd() / "agent-config.json")
    for path in candidates:
        try:
            if not path.is_file():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def _skeleton_nodes(
    bundle: dict[str, Any],
    plan_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """可挂载的原文骨架：卷来自 submissionStructure，文件条目优先取当前目录。

    首次规划时目录还是空的，此时至少要把原文里的卷和格式条目 ID 交给智能体，
    否则它无从知道能挂在哪些节点下。
    """

    def _node(
        section_id: str,
        title: str,
        *,
        parent: str = "",
        level: int = 1,
        order: int = 0,
        node_type: str = "",
        response_type: str = "",
        source: str = "招标文件原文",
    ) -> dict[str, Any]:
        return {
            "sectionId": section_id,
            "title": title[:160],
            "parentSectionId": parent,
            "level": level,
            "order": order,
            "nodeType": node_type,
            "responseType": response_type,
            "source": source,
            "kind": "",
            "required": True,
        }

    plan_volumes = [row for row in plan_rows if str(row.get("nodeType") or "") == "volume"]
    plan_items = [row for row in plan_rows if str(row.get("nodeType") or "") == "format_item"]
    volumes = [
        _node(
            str(row.get("sectionId") or ""),
            str(row.get("title") or ""),
            order=int(row.get("order") or 0),
            node_type="volume",
            response_type=str(row.get("responseType") or "section_group"),
            source=str(row.get("source") or "template"),
        )
        for row in plan_volumes
    ]
    known_volume_ids = {row["sectionId"] for row in volumes}
    known_item_ids = {str(row.get("sectionId") or "") for row in plan_items}
    structure = bundle.get("submissionStructure") if isinstance(bundle, dict) else None
    specs = structure.get("volumes") if isinstance(structure, dict) else None
    for index, spec in enumerate(specs or []):
        if not isinstance(spec, dict):
            continue
        volume_id = str(spec.get("volumeId") or "").strip()
        if not volume_id:
            continue
        if volume_id not in known_volume_ids:
            volumes.append(
                _node(
                    volume_id,
                    str(spec.get("title") or volume_id),
                    order=int(spec.get("order") or index + 1),
                    node_type="volume",
                    response_type="section_group",
                )
            )
            known_volume_ids.add(volume_id)
        for item_index, item_id in enumerate(spec.get("formatItemIds") or []):
            item_key = str(item_id or "").strip()
            if not item_key or item_key in known_item_ids:
                continue
            known_item_ids.add(item_key)
            plan_items.append(
                _node(
                    item_key,
                    "",
                    parent=volume_id,
                    level=2,
                    order=item_index + 1,
                    node_type="format_item",
                )
            )
    return volumes, plan_items


def build_context(
    parse_bundle: dict[str, Any] | None,
    plan: list[dict[str, Any]] | None,
    *,
    workspace_id: str = "",
    project_id: str = "",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成给工具用的数据快照（纯函数，便于单测）。"""

    bundle = parse_bundle if isinstance(parse_bundle, dict) else {}
    plan_rows = [row for row in (plan or []) if isinstance(row, dict)]
    effective_policy = policy if isinstance(policy, dict) else resolve_policy()
    diagnosis = diagnose(bundle, plan_rows, workspace_id=workspace_id)
    bindings = index_plan_bindings(plan_rows)
    bound_narrative: set[str] = bindings["narrative"]
    bound_table: set[str] = bindings["table"]

    requirements: list[dict[str, Any]] = []
    for item in bundle.get("requirements") or []:
        if not isinstance(item, dict):
            continue
        requirement_id = requirement_id_of(item)
        if not requirement_id:
            continue
        if requirement_id in bound_narrative:
            bound_to = "narrative"
        elif requirement_id in bound_table:
            bound_to = "table"
        else:
            bound_to = "none"
        requirements.append(
            {
                "id": requirement_id,
                "domain": requirement_domain_of(item),
                "responseType": str(item.get("responseType") or ""),
                "mandatory": bool(item.get("mandatory")),
                "isRejection": bool(item.get("isRejection")),
                "boundTo": bound_to,
                "text": requirement_text_of(item)[:_REQUIREMENT_TEXT_LIMIT],
            }
        )
        if len(requirements) >= _MAX_REQUIREMENTS:
            break

    evaluation_items: list[dict[str, Any]] = []
    for item in bundle.get("evaluationItems") or []:
        if not isinstance(item, dict):
            continue
        evaluation_items.append(
            {
                "id": str(item.get("id") or item.get("evaluationId") or ""),
                "factor": str(item.get("factor") or "")[:120],
                "item": str(item.get("item") or "")[:120],
                "criteria": str(item.get("criteria") or "")[:240],
                "score": item.get("score"),
            }
        )

    def _plan_node(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "sectionId": str(row.get("sectionId") or ""),
            "title": str(row.get("title") or "")[:160],
            "parentSectionId": str(row.get("parentSectionId") or ""),
            "level": int(row.get("level") or 1),
            "order": int(row.get("order") or 0),
            "nodeType": str(row.get("nodeType") or ""),
            "responseType": str(row.get("responseType") or ""),
            "source": str(row.get("source") or ""),
            "kind": str(row.get("kind") or ""),
            "required": bool(row.get("required", True)),
        }

    volumes, format_items = _skeleton_nodes(bundle, plan_rows)
    existing_sections = [
        _plan_node(row)
        for row in plan_rows
        if str(row.get("nodeType") or "") not in {"volume", "format_item"}
    ]

    return {
        "workspaceId": workspace_id,
        "projectId": project_id,
        "policy": effective_policy,
        "summary": diagnosis["summary"],
        "signals": {
            "requiredGaps": diagnosis["requiredGaps"],
            "initiatives": diagnosis["initiatives"],
            "notes": diagnosis["notes"],
            "warnings": diagnosis["warnings"],
        },
        "volumes": volumes,
        "formatItems": format_items,
        "existingSections": existing_sections,
        "requirements": requirements,
        "evaluationItems": evaluation_items,
    }


def write_context(context: dict[str, Any]) -> Path:
    """把上下文写到临时文件，返回路径（调用方负责清理所在目录）。"""

    directory = Path(tempfile.mkdtemp(prefix="bid-outline-context-"))
    path = directory / "outline-context.json"
    path.write_text(json.dumps(context, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
