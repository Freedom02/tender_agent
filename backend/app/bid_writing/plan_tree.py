"""标书目录树的统一规范化。

目录对外仍使用扁平数组，父子关系由 parentSectionId 表达。这个模块负责
把来自 Pi、历史数据和前端编辑器的不同写法收敛成稳定的树序列：

* 父级一定排在子级前面；
* 无效父级、自己引用自己和循环引用会提升为一级；
* level 与真实父级保持一致；
* order 只在同一父级下重新编号。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any


def _text(value: Any, limit: int = 120) -> str:
    return str(value or "").strip()[:limit]


def _flatten_children(items: list[Any], parent_id: str = "") -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = deepcopy(item)
        explicit_parent = _text(row.get("parentSectionId") or row.get("parentId"))
        inherited_parent = explicit_parent or parent_id
        if inherited_parent and not explicit_parent:
            row["parentSectionId"] = inherited_parent
        flattened.append(row)
        children = row.get("children")
        if isinstance(children, list):
            child_id = _text(row.get("sectionId") or row.get("id"))
            flattened.extend(_flatten_children(children, child_id))
        row.pop("children", None)
    return flattened


def normalize_plan_tree(plan: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """规范化扁平或 children 目录，并保持原字段。"""

    raw_items = _flatten_children(plan or [])
    if not raw_items:
        return []

    entries: list[dict[str, Any]] = []
    aliases: dict[str, str] = {}
    used_ids: set[str] = set()
    custom_index = 1

    for index, raw in enumerate(raw_items):
        row = deepcopy(raw)
        title = _text(row.get("title") or row.get("name"), 240)
        if not title:
            continue
        source_id = _text(row.get("sectionId") or row.get("id"))
        section_id = source_id
        if not section_id or section_id in used_ids:
            section_id = f"custom-{custom_index:02d}"
            custom_index += 1
            while section_id in used_ids:
                section_id = f"custom-{custom_index:02d}"
                custom_index += 1
        used_ids.add(section_id)
        if source_id and source_id not in aliases:
            aliases[source_id] = section_id
        source_section_id = _text(row.get("sourceSectionId"))
        if source_section_id and source_section_id not in aliases:
            aliases[source_section_id] = section_id
        try:
            requested_level = max(int(row.get("level") or 1), 1)
        except (TypeError, ValueError):
            requested_level = 1
        try:
            requested_order = max(int(row.get("order") or 0), 0)
        except (TypeError, ValueError):
            requested_order = 0
        entries.append(
            {
                "row": row,
                "sectionId": section_id,
                "sourceId": source_id,
                "index": index,
                "requestedLevel": requested_level,
                "requestedOrder": requested_order,
            }
        )

    by_id = {entry["sectionId"]: entry for entry in entries}
    by_source = {key: value for key, value in aliases.items() if value in by_id}
    parent_by_id: dict[str, str] = {}

    for position, entry in enumerate(entries):
        row = entry["row"]
        raw_parent = _text(
            row.get("parentSectionId")
            or row.get("parentId")
            or row.get("_parentSectionId")
        )
        parent_id = by_source.get(raw_parent, raw_parent if raw_parent in by_id else "")

        # 对只返回 level、没有返回 parentSectionId 的模型结果，尝试关联到
        # 最近的前置低一级节点；无法推断时按一级处理。
        if not parent_id and entry["requestedLevel"] > 1:
            for previous in reversed(entries[:position]):
                if previous["requestedLevel"] == entry["requestedLevel"] - 1:
                    parent_id = previous["sectionId"]
                    break

        if parent_id == entry["sectionId"]:
            parent_id = ""
        parent_by_id[entry["sectionId"]] = parent_id

    def creates_cycle(section_id: str, candidate_parent: str) -> bool:
        seen: set[str] = set()
        current = candidate_parent
        while current:
            if current == section_id or current in seen:
                return True
            seen.add(current)
            current = parent_by_id.get(current, "")
        return False

    for section_id, parent_id in list(parent_by_id.items()):
        if parent_id not in by_id or creates_cycle(section_id, parent_id):
            parent_by_id[section_id] = ""

    children_by_parent: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        parent_id = parent_by_id.get(entry["sectionId"], "")
        children_by_parent.setdefault(parent_id, []).append(entry)

    for siblings in children_by_parent.values():
        siblings.sort(
            key=lambda item: (
                item["requestedOrder"] if item["requestedOrder"] > 0 else 10**9,
                item["index"],
            )
        )

    ordered: list[dict[str, Any]] = []
    visited: set[str] = set()

    def visit(parent_id: str, level: int) -> None:
        for order, entry in enumerate(children_by_parent.get(parent_id, []), start=1):
            section_id = entry["sectionId"]
            if section_id in visited:
                continue
            visited.add(section_id)
            row = entry["row"]
            row["sectionId"] = section_id
            row["title"] = _text(row.get("title") or row.get("name"), 240)
            row["parentSectionId"] = parent_id
            row["level"] = level
            row["order"] = order
            row.pop("id", None)
            row.pop("parentId", None)
            row.pop("children", None)
            row.pop("_parentSectionId", None)
            if entry["sourceId"] and entry["sourceId"] != section_id:
                row["sourceSectionId"] = entry["sourceId"]
            ordered.append(row)
            visit(section_id, level + 1)

    visit("", 1)
    # 任何极端脏数据都不能从保存结果中消失，作为一级节点保留。
    for entry in entries:
        if entry["sectionId"] in visited:
            continue
        row = entry["row"]
        row["sectionId"] = entry["sectionId"]
        row["parentSectionId"] = ""
        row["level"] = 1
        row["order"] = len([item for item in ordered if not item.get("parentSectionId")]) + 1
        row.pop("id", None)
        row.pop("parentId", None)
        row.pop("children", None)
        ordered.append(row)
        visited.add(entry["sectionId"])

    return ordered
