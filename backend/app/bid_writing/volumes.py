"""目录卷节点与分册导出的归属判定。"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..agent_runtime.submission_structure import normalize_submission_structure
from .plan_tree import normalize_plan_tree
from .schemas import (
    SECTION_KIND_ATTACHMENT,
    SECTION_KIND_COMMERCIAL,
    SECTION_KIND_DEVIATION,
    SECTION_KIND_TECHNICAL,
    SECTION_SOURCE_AI,
    SECTION_SOURCE_ATTACHMENT,
    SECTION_SOURCE_COPIED_TEMPLATE,
    SECTION_SOURCE_TABLE,
    SECTION_SOURCE_TEMPLATE,
    build_section,
)


def _volume(section: dict[str, Any]) -> bool:
    return section.get("nodeType") == "volume"


def _norm_title(value: Any) -> str:
    return "".join(str(value or "").split()).strip().lower()


def _volume_kind(title: str) -> str:
    compact = _norm_title(title)
    if "技术" in compact or "质量" in compact:
        return SECTION_KIND_TECHNICAL
    if "其他" in compact:
        return SECTION_KIND_ATTACHMENT
    return SECTION_KIND_COMMERCIAL


def _item_kind(volume_title: str, item_title: str, item_type: str) -> str:
    compact_volume = _norm_title(volume_title)
    compact_item = _norm_title(item_title)
    if "偏离" in compact_item or "差异表" in compact_item:
        return SECTION_KIND_DEVIATION
    if "技术" in compact_volume or "质量" in compact_volume or "技术" in compact_item:
        return SECTION_KIND_TECHNICAL
    if "其他" in compact_volume or item_type in {"evidence", "attachment"}:
        return SECTION_KIND_ATTACHMENT
    return SECTION_KIND_COMMERCIAL


def _item_source(item: dict[str, Any]) -> str:
    item_type = str(item.get("itemType") or "").strip()
    response_type = str(item.get("responseType") or "").strip()
    if item_type in {"fixed_form", "fixed_table"} or response_type in {"fixed_form", "table"}:
        return SECTION_SOURCE_TEMPLATE
    if item_type == "narrative" or response_type == "chapter_text":
        return SECTION_SOURCE_AI
    if item_type == "evidence" or response_type == "attachment":
        return SECTION_SOURCE_ATTACHMENT
    if response_type == "deviation":
        return SECTION_SOURCE_TABLE
    return SECTION_SOURCE_ATTACHMENT


def _item_supports_children(item: dict[str, Any]) -> bool:
    return (
        str(item.get("itemType") or "").strip() == "narrative"
        or str(item.get("responseType") or "").strip() == "chapter_text"
    )


#: 允许出现「智能体新增叙述章节」的来源标记（对应 outline_diagnosis 的两类产出线）。
DERIVED_ORIGINS = ("required_by_tender", "ai_initiative")

#: 智能体新增章节可用的响应类型：只允许承载正文/材料，不允许变成新的卷或格式条目。
DERIVED_RESPONSE_TYPES = ("", "chapter_text", "attachment", "table")


def _is_derived_narrative(row: dict[str, Any]) -> bool:
    """判断一行是否为「智能体新增、且带依据」的叙述章节。

    这是放置层的护栏而不是业务判断：只有同时满足
    ① 显式 ``derived=true``；② ``derivedOrigin`` 属于两类产出线；
    ③ ``derivedBasis`` 非空（真的写明了依据）；④ 不是卷/格式条目
    的节点，才会被容许挂到卷下。任何一条不满足都按原有规则丢弃。
    """

    if not bool(row.get("derived")):
        return False
    if str(row.get("derivedOrigin") or "").strip() not in DERIVED_ORIGINS:
        return False
    if not str(row.get("derivedBasis") or "").strip():
        return False
    if str(row.get("nodeType") or "").strip() in {"volume", "format_item"}:
        return False
    return str(row.get("responseType") or "").strip() in DERIVED_RESPONSE_TYPES


def plan_from_submission_structure(
    submission_structure: Any,
    fine_review: dict[str, Any] | None = None,
    generated_plan: list[dict[str, Any]] | None = None,
    *,
    keep_derived: bool = False,
) -> list[dict[str, Any]]:
    """严格按招标文件卷内条目生成目录骨架。

    卷和文件条目来自招标文件原文，不能被 Pi 的通用模板替换。若传入
    ``generated_plan``，只吸收挂在“叙述型文件条目”下面的 Pi 子章节，
    其余没有原文父级的通用章节全部丢弃。

    ``keep_derived=True`` 时额外保留 ``_is_derived_narrative`` 认可的
    「智能体新增叙述章节」：原文只列了表格、卷内没有任何可承载正文的条目时，
    它允许这类章节直接挂在卷下（以及挂在另一个已被保留的新增章节下）。
    """

    structure = normalize_submission_structure(submission_structure)
    specs = structure.get("volumes") or []
    if not any(spec.get("items") for spec in specs if isinstance(spec, dict)):
        return []

    rows = (
        fine_review.get("rows")
        if isinstance(fine_review, dict)
        and isinstance(fine_review.get("rows"), list)
        else []
    )
    requirement_ids = {
        str(row.get("requirementId") or row.get("id") or "").strip()
        for row in rows
        if isinstance(row, dict)
        and str(row.get("requirementId") or row.get("id") or "").strip()
    }
    candidates = normalize_plan_tree(generated_plan or [])
    candidate_by_id = {
        str(row.get("sectionId") or ""): row
        for row in candidates
        if str(row.get("sectionId") or "")
    }
    target: list[dict[str, Any]] = []
    target_ids: set[str] = set()
    candidate_to_target: dict[str, str] = {}
    item_targets: dict[str, dict[str, Any]] = {}
    volume_targets: dict[str, dict[str, Any]] = {}
    generated_target_ids: set[str] = set()

    for volume_spec in specs:
        if not isinstance(volume_spec, dict):
            continue
        volume_id = str(volume_spec.get("volumeId") or "").strip()
        title = str(volume_spec.get("title") or "").strip()
        if not volume_id or not title:
            continue
        volume = build_section(
            volume_id,
            title,
            SECTION_SOURCE_TEMPLATE,
            kind=_volume_kind(title),
            level=1,
            order=int(volume_spec.get("order") or len(volume_targets) + 1),
            required=bool(volume_spec.get("required", True)),
            response_type="section_group",
            status="planned",
        )
        volume["nodeType"] = "volume"
        volume["volumeId"] = volume_id
        volume["sourceLocator"] = str(volume_spec.get("sourceLocator") or "")
        volume["structureSource"] = "招标文件原文"
        target.append(volume)
        target_ids.add(volume_id)
        volume_targets[volume_id] = volume

        for item in volume_spec.get("items") or []:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("formatItemId") or "").strip()
            item_title = str(item.get("title") or "").strip()
            if not item_id or not item_title:
                continue
            kind = _item_kind(
                title,
                item_title,
                str(item.get("itemType") or ""),
            )
            source = _item_source(item)
            if kind == SECTION_KIND_DEVIATION:
                source = SECTION_SOURCE_TABLE
            section = build_section(
                item_id,
                item_title,
                source,
                kind=kind,
                parent_section_id=volume_id,
                level=2,
                order=int(item.get("order") or len(item_targets) + 1),
                required=bool(item.get("required", True)),
                response_type=str(item.get("responseType") or ""),
                status="planned",
            )
            section["nodeType"] = "format_item"
            section["volumeId"] = volume_id
            section["formatItemId"] = item_id
            section["structureSource"] = "招标文件原文"
            section["sourceLocator"] = str(item.get("sourceLocator") or "")
            section["itemType"] = str(item.get("itemType") or "")
            section["conditional"] = bool(item.get("conditional", False))
            section["conditionText"] = str(item.get("conditionText") or "")
            section["templateId"] = str(item.get("templateId") or "")
            section["templateType"] = str(item.get("templateType") or "")
            if section["conditional"]:
                section["required"] = False
            section["requirementIds"] = [
                value for value in section.get("requirementIds", [])
                if value in requirement_ids
            ]
            target.append(section)
            target_ids.add(item_id)
            item_targets[item_id] = section

    if not candidates:
        return normalize_plan_tree(target)

    def candidate_volume_id(row: dict[str, Any]) -> str:
        value = str(row.get("volumeId") or "").strip()
        if value in volume_targets:
            return value
        title = _norm_title(row.get("title"))
        for volume_id, volume in volume_targets.items():
            if title == _norm_title(volume.get("title")):
                return volume_id
        parent_id = str(row.get("parentSectionId") or "").strip()
        parent = candidate_by_id.get(parent_id)
        if parent:
            return candidate_volume_id(parent)
        return ""

    def match_item(row: dict[str, Any]) -> dict[str, Any] | None:
        format_item_id = str(row.get("formatItemId") or "").strip()
        if format_item_id in item_targets:
            return item_targets[format_item_id]
        template_id = str(row.get("templateId") or "").strip()
        for item in item_targets.values():
            if template_id and template_id == str(item.get("templateId") or ""):
                return item
        title = _norm_title(row.get("title"))
        volume_id = candidate_volume_id(row)
        for item in item_targets.values():
            if volume_id and item.get("volumeId") != volume_id:
                continue
            if title and title == _norm_title(item.get("title")):
                return item
        return None

    for row in candidates:
        candidate_id = str(row.get("sectionId") or "").strip()
        if not candidate_id:
            continue
        matched_item = match_item(row)
        if matched_item is not None:
            candidate_to_target[candidate_id] = str(matched_item["sectionId"])
            matched_item["requirementIds"] = list(
                dict.fromkeys(
                    [
                        *matched_item.get("requirementIds", []),
                        *[
                            str(value).strip()
                            for value in row.get("requirementIds", [])
                            if str(value).strip() in requirement_ids
                        ],
                    ]
                )
            )
            matched_item["evaluationIds"] = list(
                dict.fromkeys(
                    str(value).strip()
                    for value in row.get("evaluationIds", [])
                    if str(value).strip()
                )
            )

    target_by_id = {
        str(row.get("sectionId") or ""): row
        for row in target
        if str(row.get("sectionId") or "")
    }

    for row in candidates:
        candidate_id = str(row.get("sectionId") or "").strip()
        parent_id = str(row.get("parentSectionId") or "").strip()
        if not candidate_id or candidate_id in candidate_to_target:
            continue
        mapped_parent = candidate_to_target.get(parent_id)
        parent_is_volume = False
        if not mapped_parent:
            if keep_derived and _is_derived_narrative(row) and parent_id in volume_targets:
                # 智能体据证据新增的叙述章节：原文只列了响应表/附件，卷内没有任何
                # 可承载正文的条目，此时允许它挂在卷下（仍受策略上限与质量检查约束）。
                mapped_parent = parent_id
                parent_is_volume = True
            else:
                # Pi 直接挂在卷下的“技术方案/商务响应”等通用节点没有招标
                # 文件条目父级，不能混入明确格式目录。
                continue
        parent_item = target_by_id.get(mapped_parent)
        if not parent_item:
            continue
        if (
            not parent_is_volume
            and mapped_parent in item_targets
            and not _item_supports_children(parent_item)
        ):
            continue
        if (
            not parent_is_volume
            and mapped_parent not in item_targets
            and mapped_parent not in generated_target_ids
        ):
            continue
        child_id = candidate_id
        suffix = 1
        while child_id in target_ids:
            child_id = f"{candidate_id}-ai-{suffix}"
            suffix += 1
        child = dict(row)
        child["sectionId"] = child_id
        child["sourceSectionId"] = str(row.get("sectionId") or "")
        child["parentSectionId"] = mapped_parent
        child["volumeId"] = parent_item.get("volumeId", "")
        child["level"] = int(parent_item.get("level") or 1) + 1
        child["order"] = len(
            [
                item for item in target
                if item.get("parentSectionId") == mapped_parent
            ]
        ) + 1
        child["structureSource"] = "Pi 子章节补充"
        child["required"] = bool(row.get("required", True))
        target.append(child)
        target_ids.add(child_id)
        target_by_id[child_id] = child
        generated_target_ids.add(child_id)
        candidate_to_target[candidate_id] = child_id

    return normalize_plan_tree(target)


def reconcile_plan_with_submission_structure(
    plan: list[dict[str, Any]],
    submission_structure: Any,
    *,
    keep_derived: bool = False,
) -> list[dict[str, Any]]:
    """把已有 Pi/历史目录收敛到明确的卷和卷内文件条目。"""

    structure = normalize_submission_structure(submission_structure)
    if not any(spec.get("items") for spec in structure.get("volumes", [])):
        return normalize_plan_tree(plan)
    return plan_from_submission_structure(
        structure,
        generated_plan=plan,
        keep_derived=keep_derived,
    )


def arrange_volumes(
    plan: list[dict[str, Any]],
    submission_structure: Any,
) -> tuple[list[dict[str, Any]], list[str]]:
    """将新旧目录收敛为卷→章→节；保留原章节 ID 和全部业务字段。"""
    structure = normalize_submission_structure(submission_structure)
    items = normalize_plan_tree(plan)
    warnings: list[str] = []
    if not items:
        return [], warnings
    declared = structure["volumes"]
    roots = [row for row in items if not row.get("parentSectionId")]
    existing_volumes = [row for row in roots if _volume(row)]
    if not declared:
        if existing_volumes:
            return items, warnings
        group_roots = [row for row in roots if row.get("responseType") == "section_group"]
        if group_roots:
            for row in group_roots:
                row["nodeType"] = "volume"
            for row in roots:
                if row in group_roots:
                    continue
                candidate = next(
                    (group for group in group_roots if group.get("kind") == row.get("kind")),
                    None,
                )
                row["parentSectionId"] = (candidate or group_roots[0])["sectionId"]
            return normalize_plan_tree(items), warnings
        volume_id = "volume-main"
        used_ids = {row["sectionId"] for row in items}
        while volume_id in used_ids:
            volume_id += "-1"
        volume = build_section(
            volume_id, "投标响应文件", "template",
            response_type="section_group", status="planned",
        )
        volume["nodeType"] = "volume"
        for row in roots:
            row["parentSectionId"] = volume["sectionId"]
        return normalize_plan_tree([volume, *items]), warnings

    existing_by_id = {row["sectionId"]: row for row in existing_volumes}
    used_ids = {row["sectionId"] for row in items}
    volumes: list[dict[str, Any]] = []
    for spec in declared:
        volume_id = spec["volumeId"]
        row = existing_by_id.get(volume_id)
        if row is None:
            row = build_section(
                volume_id if volume_id not in used_ids else f"volume-{volume_id}",
                spec["title"], "template", response_type="section_group",
                status="planned", order=spec["order"],
            )
        row["nodeType"] = "volume"
        row["volumeId"] = volume_id
        row["title"] = spec["title"]
        row["sourceLocator"] = spec["sourceLocator"]
        volumes.append(row)
        used_ids.add(row["sectionId"])
    declared_ids = {row["sectionId"] for row in volumes}
    assigned: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for row in roots:
        if row["sectionId"] in declared_ids:
            continue
        selected = next(
            (
                volume for volume, spec in zip(volumes, declared)
                if row.get("volumeId") == spec["volumeId"]
                or row["title"] == spec["title"]
                or (
                    row.get("formatItemId")
                    and row["formatItemId"] in spec["formatItemIds"]
                )
            ),
            None,
        )
        if selected is None and len(volumes) == 1:
            selected = volumes[0]
        if selected is None:
            title = str(row.get("title") or "")
            kind = str(row.get("kind") or "")
            candidates = [
                volume for volume in volumes
                if (
                    (kind == "technical" and "技术" in volume["title"])
                    or (kind == "commercial" and any(word in volume["title"] for word in ("商务", "资格", "报价")))
                    or (kind == "deviation" and "技术" in title and "技术" in volume["title"])
                    or (kind == "deviation" and "商务" in title and "商务" in volume["title"])
                    or ("技术" in title and "技术" in volume["title"])
                    or ("商务" in title and "商务" in volume["title"])
                )
            ]
            if len(candidates) == 1:
                selected = candidates[0]
        if selected is None:
            unresolved.append(row)
        else:
            row["parentSectionId"] = selected["sectionId"]
        assigned.append(row)

    if unresolved:
        fallback_id = "volume-unassigned"
        while fallback_id in used_ids or fallback_id in declared_ids:
            fallback_id += "-1"
        fallback = build_section(
            fallback_id, "待确认归属", "template",
            response_type="section_group", status="planned",
        )
        fallback["nodeType"] = "volume"
        volumes.append(fallback)
        for row in unresolved:
            row["parentSectionId"] = fallback_id
        warnings.append(f"{len(unresolved)} 个顶级章节未能归属招标文件规定的卷，需在目录中调整后才能分卷导出。")
    children = [row for row in items if row not in roots]
    return normalize_plan_tree([*volumes, *assigned, *children]), warnings


def separate_volume_plans(
    plan: list[dict[str, Any]], submission_structure: Any,
) -> tuple[list[tuple[dict[str, Any], list[dict[str, Any]]]], str]:
    """仅明确要求分册且每卷都可归属时拆分；否则返回空及原因。"""
    structure = normalize_submission_structure(submission_structure)
    if structure["mode"] != "separate":
        return [], ""
    items = normalize_plan_tree(plan)
    specs = structure["volumes"]
    roots = [item for item in items if not item.get("parentSectionId")]
    if len(roots) != len(specs) or any(
        row.get("nodeType") != "volume"
        or row.get("volumeId", row["sectionId"]) != spec["volumeId"]
        for row, spec in zip(roots, specs)
    ):
        return [], "目录卷与招标文件要求的分册不一致，已改为单份导出；请核对卷归属。"
    grouped: dict[str, list[dict[str, Any]]] = {row["sectionId"]: [row] for row in roots}
    owner: dict[str, str] = {row["sectionId"]: row["sectionId"] for row in roots}
    for row in items:
        if row["sectionId"] in owner:
            continue
        parent_id = str(row.get("parentSectionId") or "")
        volume_id = owner.get(parent_id)
        if not volume_id:
            return [], "目录中存在卷外章节，已改为单份导出；请核对卷归属。"
        owner[row["sectionId"]] = volume_id
        grouped[volume_id].append(row)
    if any(len(grouped[row["sectionId"]]) < 2 for row in roots):
        return [], "至少一卷没有章节内容，已改为单份导出；请补齐目录后重试。"
    return [
        (deepcopy(spec), grouped[root["sectionId"]])
        for spec, root in zip(specs, roots)
    ], ""
