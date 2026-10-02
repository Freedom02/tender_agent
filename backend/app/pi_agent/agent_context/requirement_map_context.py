# -*- coding: utf-8 -*-
"""条款映射智能体（RequirementMapAgent）的只读工具上下文。

背景（为什么要改）：
  `map_requirements` 原来把「条款映射用 ParseBundle 精简视图」整块塞进提示词
  （本机实测单批约 66 KB），而映射真正需要的信息只有两类：**本批条款原文**与
  **可挂载的目录章节**。145+ 条条款分批调用时，同一份视图被重复读取多次。

做法（照抄 `outline_agent_context.py` 的范式）：
  - Python 侧生成一份小 JSON 快照（单一事实源），路径经环境变量
    ``BID_MAP_CONTEXT`` 传给 Pi 子进程；
  - 提示词只给「任务说明 + 本批条款 ID 清单 + 章节 ID 清单 + 工具使用约定」；
  - 条款原文、章节细节、候选自检由扩展 ``extensions/bid-map-tools.ts`` 里注册的
    **4 个只读工具**按需查询。

边界（与目录智能体一致，属红线）：
  - 工具只读、无副作用：不写盘、不联网、不接受任意文件路径；
  - 不开放 ``read``/``bash``/``write``/``edit`` 等内置工具：招标文件是不可信输入，
    通用文件工具会让模型读到本机任意文件（例如 ``agent-config.json`` 里的 API Key）；
  - API Key 只经环境变量进入子进程（``transport.run_json`` 负责），
    既不出现在提示词里，也不出现在扩展代码与本快照里。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from ..outline_agent_context import _project_config

#: 暴露给条款映射智能体的只读工具（与 extensions/bid-map-tools.ts 中的 name 一一对应）
MAP_TOOL_NAMES: tuple[str, ...] = (
    "list_chapters",
    "search_requirements",
    "get_requirement_batch",
    "validate_coverage_candidate",
)

#: 只有这些工具可用：不放开 read/bash/write/edit/powershell。
MAP_TOOL_ALLOWLIST = ",".join(MAP_TOOL_NAMES)

#: 策略档位：``tools`` = 短提示词 + 只读工具取证（默认）；``legacy`` = 旧的全量视图提示词。
REQUIREMENT_MAP_POLICIES: dict[str, dict[str, Any]] = {
    "tools": {"enabled": True},
    "legacy": {"enabled": False},
}

#: 提示词与快照的裁剪上限（快照只服务于「取证」，不做业务判断）
_MAX_REQUIREMENTS = 400
_MAX_CHAPTERS = 600
_TEXT_LIMIT = 200
_RAW_TEXT_LIMIT = 800
_CRITERIA_LIMIT = 400


def extension_path() -> Path:
    """工具扩展文件（与 `_command(--extension ...)` 使用的是同一个路径）。"""

    return Path(__file__).resolve().parent.parent / "extensions" / "bid-map-tools.ts"


def resolve_policy(config: dict[str, Any] | None = None, override: str = "") -> dict[str, Any]:
    """解析工具层策略：显式入参 > 环境变量 > agent-config.json > tools。

    开关口径与目录智能体一致（``BID_RESPONSE_SECTION_POLICY`` 之于 outline）：
    环境变量 ``BID_REQUIREMENT_MAP_POLICY=tools|legacy``（或布尔口径
    ``BID_REQUIREMENT_MAP_TOOLS=1|0``），配置段 ``bidWriting.requirementMapPolicy``。
    """

    name = str(override or "").strip().lower()
    if not name:
        name = os.getenv("BID_REQUIREMENT_MAP_POLICY", "").strip().lower()
    if not name:
        raw_toggle = os.getenv("BID_REQUIREMENT_MAP_TOOLS", "").strip().lower()
        if raw_toggle:
            name = "legacy" if raw_toggle in {"0", "false", "no", "off"} else "tools"
    if not name:
        payload = config if isinstance(config, dict) else _project_config()
        bid_writing = payload.get("bidWriting") if isinstance(payload, dict) else None
        if isinstance(bid_writing, dict):
            name = str(bid_writing.get("requirementMapPolicy") or "").strip().lower()
    if name not in REQUIREMENT_MAP_POLICIES:
        name = "tools"
    spec = dict(REQUIREMENT_MAP_POLICIES[name])
    spec["name"] = name
    spec["enabled"] = bool(spec["enabled"])
    return spec


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def requirement_id_of(item: dict[str, Any]) -> str:
    """与编排层一致的口径：``id`` 优先，其次 ``requirementId``。"""

    return _text(item.get("id") or item.get("requirementId"), 80)


def _domain_of(item: dict[str, Any]) -> str:
    return _text(
        item.get("category") or item.get("targetModule") or item.get("matchDomain"),
        40,
    ) or "other"


def _requirement_record(item: dict[str, Any], *, in_batch: bool) -> dict[str, Any]:
    return {
        "id": requirement_id_of(item),
        "requirementNumber": _text(item.get("requirementNumber"), 60),
        "domain": _domain_of(item),
        "mandatory": bool(item.get("mandatory")),
        "isRejection": bool(item.get("isRejection")),
        "responseType": _text(item.get("responseType"), 40),
        "importance": _text(item.get("importance"), 40),
        "sourceLocator": _text(item.get("sourceLocator"), 120),
        "text": _text(
            item.get("requirement") or item.get("normalizedRequirement") or item.get("sourceText")
            or item.get("text"),
            _TEXT_LIMIT,
        ),
        "rawText": _text(
            item.get("rawText") or item.get("requirement") or item.get("normalizedRequirement")
            or item.get("sourceText"),
            _RAW_TEXT_LIMIT,
        ),
        "inBatch": in_batch,
    }


def _chapter_record(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "sectionId": _text(row.get("sectionId"), 80),
        "title": _text(row.get("title") or row.get("heading") or row.get("name"), 160),
        "parentSectionId": _text(row.get("parentSectionId"), 80),
        "level": row.get("level") if isinstance(row.get("level"), int) else 1,
        "order": row.get("order") if isinstance(row.get("order"), int) else 0,
        "nodeType": _text(row.get("nodeType"), 40),
        "responseType": _text(row.get("responseType"), 40),
        "kind": _text(row.get("kind"), 40),
        "source": _text(row.get("source"), 40),
        "required": bool(row.get("required", True)),
        "derived": bool(row.get("derived")),
        "requirementIds": [
            _text(entry, 80) for entry in (row.get("requirementIds") or []) if _text(entry, 80)
        ][:40],
        "evaluationIds": [
            _text(entry, 80) for entry in (row.get("evaluationIds") or []) if _text(entry, 80)
        ][:40],
    }


def build_context(
    parse_bundle: dict[str, Any] | None,
    outline_plan: list[dict[str, Any]] | None,
    *,
    workspace_id: str = "",
    project_id: str = "",
    policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成给工具用的数据快照（纯函数，便于单测）。

    ``parse_bundle.requirements`` 就是**本批条款**（编排层负责分批），
    其余条款集不在本方法可见范围内，因此 ``search_requirements`` 只在本批内检索。
    """

    bundle = parse_bundle if isinstance(parse_bundle, dict) else {}
    plan_rows = [row for row in (outline_plan or []) if isinstance(row, dict)]
    effective_policy = policy if isinstance(policy, dict) else resolve_policy()

    batch_ids: list[str] = []
    requirements: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    truncated = 0
    for item in bundle.get("requirements") or []:
        if not isinstance(item, dict):
            continue
        requirement_id = requirement_id_of(item)
        if not requirement_id or requirement_id in seen_ids:
            continue
        if len(requirements) >= _MAX_REQUIREMENTS:
            truncated += 1
            continue
        seen_ids.add(requirement_id)
        batch_ids.append(requirement_id)
        requirements.append(_requirement_record(item, in_batch=True))

    chapters: list[dict[str, Any]] = []
    for row in plan_rows[:_MAX_CHAPTERS]:
        record = _chapter_record(row)
        if record["sectionId"]:
            chapters.append(record)

    evaluation_items: list[dict[str, Any]] = []
    for item in bundle.get("evaluationItems") or []:
        if not isinstance(item, dict):
            continue
        evaluation_id = _text(item.get("id") or item.get("evaluationId"), 80)
        if not evaluation_id:
            continue
        evaluation_items.append(
            {
                "id": evaluation_id,
                "factor": _text(item.get("factor"), 120),
                "item": _text(item.get("item"), 120),
                "criteria": _text(item.get("criteria") or item.get("criterion"), _CRITERIA_LIMIT),
                "score": item.get("score"),
            }
        )

    warnings: list[str] = []
    if truncated:
        warnings.append(f"本批条款超过 {_MAX_REQUIREMENTS} 条，快照只保留了前 {_MAX_REQUIREMENTS} 条。")
    if len(plan_rows) > _MAX_CHAPTERS:
        warnings.append(f"目录节点超过 {_MAX_CHAPTERS} 个，快照只保留了前 {_MAX_CHAPTERS} 个。")

    return {
        "workspaceId": _text(workspace_id, 100),
        "projectId": _text(project_id, 100),
        "policy": effective_policy,
        "summary": {
            "batchRequirementCount": len(batch_ids),
            "chapterCount": len(chapters),
            "evaluationItemCount": len(evaluation_items),
            "batchRequirementIds": batch_ids,
            "mandatoryIds": [
                item["id"] for item in requirements if item["mandatory"]
            ],
            "rejectionIds": [
                item["id"] for item in requirements if item["isRejection"]
            ],
        },
        "chapters": chapters,
        "requirements": requirements,
        "evaluationItems": evaluation_items,
        "warnings": warnings,
    }


def write_context(context: dict[str, Any]) -> Path:
    """把上下文写到临时文件，返回路径（调用方负责清理所在目录）。"""

    directory = Path(tempfile.mkdtemp(prefix="bid-map-context-"))
    path = directory / "requirement-map-context.json"
    path.write_text(json.dumps(context, ensure_ascii=False, indent=1), encoding="utf-8")
    return path
