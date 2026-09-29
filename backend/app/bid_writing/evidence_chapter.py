# -*- coding: utf-8 -*-
"""技术参数证明材料章节：按条复用细评审匹配结果，逐条列出可插图的证明材料。

设计要点（与标书撰写既有约定的关系）：
- **不重新检索**：完全复用 ``fine_review.rows`` 上已有的匹配结果
  （``matchedObjectIds`` / ``evidenceItems`` / ``technicalAlignment``），
  与响应表保持同一数据源。
- **不调 LLM**：本模块是确定性模块，只做「要求 → 命中 M3 材料」的搬运与渲染。
- **不改防幻觉红线**：只输出 M3 中真实存在且带 ``evidencePath`` 的记录；未命中
  一律落占位并计入 ``blanksToFill``，不生成任何企业事实。
- **逐条插图**：产出 ``evidenceGroups`` 供 assemble 层按"要求 → 本条证据图"
  的顺序交错插入，而不是把证据堆在章节末尾。
- **控量**：匹配数据里 ``evidenceItems`` 是**产品级**完整证据集（一个产品几十条
  参数会整批挂在每条命中的要求下），因此按 ``matchedUnits`` 做文本对齐只取对口
  证据，并在整章范围内对同一证据只插图一次（后续条目改为交叉引用）。
"""
from __future__ import annotations

import re
from typing import Any

# 章节标识（稳定 ID，便于导出与再次插入时定位同一章节）
EVIDENCE_SECTION_ID = "evidence-01"
EVIDENCE_SECTION_TITLE = "技术参数证明材料"
EVIDENCE_SECTION_GROUP_ID = "attachment-root"
EVIDENCE_SECTION_GROUP_TITLE = "偏离与附件"

# 单条要求最多挂几个证明材料（产品本体 + 对口参数证据）
MAX_REFS_PER_REQUIREMENT = 3

# 用于复用响应表材料解析的桩章节：标题决定领域为 technical + service_plan，
# 与「技术和服务要求响应表」一致；不设置 requirementIds 表示不限条款。
_RESPONSE_TABLE_STUB: dict[str, Any] = {
    "title": "技术和服务要求响应表",
    "responseType": "table",
    "itemType": "fixed_table",
    "requirementIds": [],
}

# 证明材料目录名 → M3 factKey（用于把细评审行里的 locator 翻译成可读溯源分组）
_FACT_KEY_BY_FOLDER: tuple[tuple[str, str], ...] = (
    ("技术产品", "products"),
    ("技术参数", "capabilities"),
    ("类似业绩", "projectReferences"),
    ("证照凭证", "credentials"),
    ("财务证明", "financialRecords"),
    ("资质认证", "qualifications"),
    ("项目人员", "personnel"),
)

# 产品本体证据所在的目录（作为每条要求的兜底证明材料）
_PRODUCT_FOLDER = "技术产品"


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def _fact_key_for_path(path: str) -> str:
    for folder, key in _FACT_KEY_BY_FOLDER:
        if folder in path:
            return key
    return "evidence"


def _normalize(text: Any) -> str:
    """归一化文本用于对齐比较：去空白/标点、统一全半角数字。"""

    value = str(text or "")
    value = value.translate(
        str.maketrans("０１２３４５６７８９．，、；：（）", "0123456789.,,;:()")
    )
    return re.sub(r"[\s、．。,，;；:：()（）\[\]【】\"'“”‘’\-—_/]+", "", value)


def _harvest_evidence_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    """从细评审行直接收割证据地址（``evidenceItems[].locator``）。

    匹配阶段已把命中材料的证据文件夹写进 evidenceItems；即便 M3 暂时不可用、
    索引为空，这条链路仍能稳定拿到证明材料地址。
    """

    from .schemas import build_source_ref

    refs: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in _as_list(row.get("evidenceItems")):
        if not isinstance(item, dict):
            continue
        path = str(item.get("locator") or item.get("evidencePath") or "").strip()
        if not path or path in seen:
            continue
        seen.add(path)
        refs.append(
            build_source_ref(
                _fact_key_for_path(path),
                str(
                    item.get("id")
                    or item.get("factId")
                    or item.get("materialId")
                    or ""
                ).strip(),
                str(item.get("name") or "").strip(),
                path,
            )
        )
    return refs


def _alignment_targets(row: dict[str, Any]) -> list[str]:
    """本条要求真正命中的文本片段（来自 technicalAlignment）。"""

    alignment = row.get("technicalAlignment")
    if not isinstance(alignment, dict):
        return []
    targets: list[str] = []
    for unit in _as_list(alignment.get("matchedUnits")):
        if not isinstance(unit, dict):
            continue
        fragment = str(unit.get("matchedFragment") or "").strip()
        if fragment:
            targets.append(fragment)
    for point in _as_list(alignment.get("coveredPoints")):
        text = str(point or "").strip()
        if text:
            targets.append(text)
    return list(dict.fromkeys(targets))


def _matches_target(name: str, targets: list[str]) -> bool:
    """证据条目名与命中片段做前 24 字归一化对齐判断。"""

    key = _normalize(name)
    if not key:
        return False
    short = key[:24]
    for target in targets:
        normalized = _normalize(target)
        if not normalized:
            continue
        if short and (short in normalized or normalized[:24] in key):
            return True
    return False


def _select_evidence_refs(
    row: dict[str, Any],
    refs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从命中产品的**产品级**证据集中挑出真正支撑本条要求的证明材料。

    匹配结果里 ``evidenceItems`` 是产品级完整证据集（例如某产品全部 47 条参数
    证据会整批挂在每条命中的要求下）。这里用 ``matchedUnits[].matchedFragment`` /
    ``coveredPoints`` 与证据条目名做文本对齐，只取对口的若干条，并以产品本体证据
    兜底；全部对不上时退回产品本体，避免把整个产品证据库重复插十几次。
    """

    if not refs:
        return []

    targets = _alignment_targets(row)
    product_refs = [
        ref for ref in refs if _PRODUCT_FOLDER in str(ref.get("evidencePath") or "")
    ]

    matched: list[dict[str, Any]] = []
    if targets:
        for ref in refs:
            if ref in product_refs:
                continue
            if _matches_target(str(ref.get("name") or ""), targets):
                matched.append(ref)

    if matched:
        ordered = [*product_refs[:1], *matched]
    elif product_refs:
        ordered = product_refs[:1]
    else:
        ordered = refs[: MAX_REFS_PER_REQUIREMENT * 2]

    # 去重（同一 evidencePath 只留一次）并按 evidencePath 排序保证输出稳定
    unique: dict[str, dict[str, Any]] = {}
    for ref in ordered:
        path = str(ref.get("evidencePath") or "").strip()
        if path and path not in unique:
            unique[path] = ref
    return sorted(unique.values(), key=lambda r: str(r.get("evidencePath") or ""))[
        :MAX_REFS_PER_REQUIREMENT
    ]


def _materials_from_refs(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "materialId": str(ref.get("factId") or "").strip(),
            "name": str(ref.get("name") or "").strip(),
            "description": "",
            "evidencePath": str(ref.get("evidencePath") or "").strip(),
            "sourceGroup": str(ref.get("factKey") or "").strip(),
            "verificationStatus": "",
        }
        for ref in refs
    ]


def build_evidence_entries(
    fine_review: dict[str, Any] | None,
    knowledge: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """逐条技术要求 → 命中的 M3 材料与溯源引用（复用现有匹配结果）。

    证据地址有两个来源，按优先级取用：
    1. **M3 解析**：``prepare_response_table_inputs`` 把匹配到的对象 ID 反查 M3，
       得到记录上的 ``evidencePath``（信息最全，含分组与核验状态）；
    2. **细评审行直取**：匹配阶段已把命中材料的证据文件夹写进
       ``fineReview.rows[].evidenceItems[].locator``。M3 暂时不可用（离线）时
       索引为空，此时直接收割该字段，再按命中片段对齐筛选，保证既拿得到地址、
       又不会把产品级证据整批重复插入。
    """

    from .response_table import (
        prepare_response_table_inputs,
        requirement_domain,
        technical_service_requirements,
    )

    prepared = {
        str(row.get("requirementId") or "").strip(): row
        for row in prepare_response_table_inputs(
            dict(_RESPONSE_TABLE_STUB),
            fine_review,
            knowledge,
        )
    }
    raw_rows = technical_service_requirements(fine_review, domains=None)

    entries: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            continue
        requirement_id = str(raw.get("requirementId") or raw.get("id") or "").strip()
        requirement = str(
            raw.get("requirement") or raw.get("rawText") or raw.get("sourceText") or ""
        ).strip()
        matched = prepared.get(requirement_id) or {}

        materials = [
            material
            for material in (matched.get("materials") or [])
            if isinstance(material, dict)
        ]
        evidence_refs = [
            ref
            for ref in (matched.get("sourceRefs") or [])
            if isinstance(ref, dict) and str(ref.get("evidencePath") or "").strip()
        ]

        if not evidence_refs:
            harvested = _harvest_evidence_items(raw)
            evidence_refs = _select_evidence_refs(raw, harvested)
            if evidence_refs:
                materials = _materials_from_refs(evidence_refs)

        entries.append(
            {
                "sequence": index,
                "requirementId": requirement_id,
                "requirement": requirement,
                "category": str(
                    matched.get("category") or requirement_domain(raw)
                ).strip(),
                "matchStatus": str(raw.get("status") or "").strip(),
                "matchReason": str(raw.get("reason") or "").strip(),
                "gaps": [
                    str(value).strip()
                    for value in (raw.get("gaps") or [])
                    if str(value).strip()
                ],
                "materials": materials,
                "sourceRefs": evidence_refs,
                "status": "ready" if evidence_refs else "missing",
            }
        )
    return entries


def evidence_entries_summary(entries: list[dict[str, Any]]) -> dict[str, int]:
    """条目统计，供前端与日志展示。"""

    total = len(entries or [])
    ready = sum(1 for item in (entries or []) if item.get("status") == "ready")
    return {
        "total": total,
        "ready": ready,
        "missing": total - ready,
    }


def _material_names(entry: dict[str, Any]) -> str:
    names = [
        str(material.get("name") or "").strip()
        for material in (entry.get("materials") or [])
        if isinstance(material, dict) and str(material.get("name") or "").strip()
    ]
    return "、".join(dict.fromkeys(names))


def _evidence_paths(entry: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            str(ref.get("evidencePath") or "").strip()
            for ref in (entry.get("sourceRefs") or [])
            if str(ref.get("evidencePath") or "").strip()
        )
    )


def build_evidence_chapter_result(
    section: dict[str, Any] | None,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """把逐条证据条目渲染为章节内容，并产出导出层需要的插图分组。

    ``evidenceGroups[].insertRefs`` 是本条**应当实际插图**的证据（整章内同一证据
    只插一次），``sourceRefs`` 是本条对应的全部证据（用于正文交叉引用）。
    """

    section_data = section if isinstance(section, dict) else {}
    title = str(section_data.get("title") or "").strip() or EVIDENCE_SECTION_TITLE

    lines: list[str] = [
        f"# {title}",
        "",
        "本节按「技术和服务要求响应表」逐条列出对应证明材料，材料原件随附于本条之后；",
        "后文再次引用同一证明材料时不再重复插页，仅标注见前文；",
        "未命中的条目已标注占位，须人工补充后再行投标。",
        "",
    ]
    groups: list[dict[str, Any]] = []
    source_refs: list[dict[str, Any]] = []
    blanks: list[str] = []
    inserted: dict[str, int] = {}  # evidencePath → 首次插入的条目序号

    for entry in entries:
        sequence = entry.get("sequence")
        requirement = str(entry.get("requirement") or "").strip()
        requirement_id = str(entry.get("requirementId") or "").strip()
        refs = [
            ref for ref in (entry.get("sourceRefs") or []) if isinstance(ref, dict)
        ]
        insert_refs: list[dict[str, Any]] = []
        reused: list[dict[str, Any]] = []
        for ref in refs:
            path = str(ref.get("evidencePath") or "").strip()
            if not path:
                continue
            if path in inserted:
                reused.append(ref)
            else:
                inserted[path] = int(sequence or 0)
                insert_refs.append(ref)

        lines.append(f"## {sequence}. {requirement or '（未命名要求）'}")
        lines.append("")
        if requirement_id:
            lines.append(f"- 对应条款：{requirement_id}")
        names = _material_names(entry)
        if refs:
            lines.append(f"- 响应材料：{names or '（见下列证据原件）'}")
            for path in _evidence_paths(entry):
                first = inserted.get(path, 0)
                if first and first != int(sequence or 0):
                    lines.append(f"  - 证据路径：{path}（原件见第 {first} 条）")
                else:
                    lines.append(f"  - 证据路径：{path}")
        else:
            lines.append("- 响应材料：【需人工补充：未在 M3 中命中可支撑本条要求的证明材料】")
            blanks.append(
                f"{requirement_id or f'第{sequence}条'}：未命中可用企业证明材料"
            )
        reason = str(entry.get("matchReason") or "").strip()
        if reason:
            lines.append(f"- 匹配说明：{reason}")
        if entry.get("gaps"):
            gap_text = "；".join(
                str(value).strip() for value in entry["gaps"] if str(value).strip()
            )
            if gap_text:
                lines.append(f"- 待补：{gap_text}")
        lines.append("")
        source_refs.extend(refs)
        groups.append(
            {
                "sequence": sequence,
                "requirementId": requirement_id,
                "requirement": requirement,
                "sourceRefs": refs,
                "insertRefs": insert_refs,
                "reusedRefs": reused,
                "status": str(entry.get("status") or "missing"),
            }
        )

    if not entries:
        lines.append("（未在细评审结果中找到技术/服务条款，请先完成招标文件匹配评估。）")
        lines.append("")
        blanks.append("未找到可用于证明材料章节的技术/服务条款")

    deduped_refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in source_refs:
        key = (
            str(ref.get("evidencePath") or "").strip(),
            str(ref.get("factId") or ref.get("materialId") or "").strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped_refs.append(ref)

    summary = evidence_entries_summary(entries)
    summary["distinctEvidence"] = len(inserted)
    return {
        "content": "\n".join(lines).strip(),
        "evidenceEntries": entries,
        "evidenceGroups": groups,
        "sourceRefs": deduped_refs,
        "blanksToFill": list(dict.fromkeys(blanks)),
        "status": "generated" if entries else "missing",
        "summary": summary,
    }


def build_evidence_chapter_section(
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造（或就地更新）「技术参数证明材料」章节节点。"""

    from .schemas import (
        SECTION_KIND_ATTACHMENT,
        SECTION_SOURCE_ATTACHMENT,
        build_section,
    )

    if isinstance(existing, dict) and existing:
        section = dict(existing)
        section.setdefault("title", EVIDENCE_SECTION_TITLE)
        return section
    return build_section(
        EVIDENCE_SECTION_ID,
        EVIDENCE_SECTION_TITLE,
        SECTION_SOURCE_ATTACHMENT,
        kind=SECTION_KIND_ATTACHMENT,
        parent_section_id=EVIDENCE_SECTION_GROUP_ID,
        level=2,
        order=0,
        required=False,
        response_type="attachment",
        status="planned",
    )


def build_evidence_group_section() -> dict[str, Any]:
    """构造承载证明材料章节的分组节点（偏离与附件）。"""

    from .schemas import (
        SECTION_KIND_ATTACHMENT,
        SECTION_SOURCE_ATTACHMENT,
        build_section,
    )

    return build_section(
        EVIDENCE_SECTION_GROUP_ID,
        EVIDENCE_SECTION_GROUP_TITLE,
        SECTION_SOURCE_ATTACHMENT,
        kind=SECTION_KIND_ATTACHMENT,
        level=1,
        order=99,
        response_type="section_group",
        status="planned",
    )
