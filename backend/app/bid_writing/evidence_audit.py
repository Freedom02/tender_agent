# -*- coding: utf-8 -*-
"""生成质量审计（写作阶段）：防幻觉 + 跨章一致性的确定性校验。

撰写阶段提示词已要求「企业事实必须挂 sourceRefs」「资料缺失写【需人工补充】」，
本模块把这两条约束升级为可复算的检查，供校对/合规审查直接消费：

  1) 无溯源断言：技术/商务叙述章节正文出现可核验的企业事实表述（资质/认证/业绩/
     人员/专利/金额等），但本章 sourceRefs 为空 → 疑似无来源断言，标「高」；
  2) 证据路径缺失：sourceRefs 未带 evidencePath → 无法回溯到材料原件，标「中」；
  3) 能力缺口：正文保留【需人工补充】/【待补充】等占位 → 证据槽位确无材料，标「中」；
  4) 承诺值跨章不一致：同一承诺概念（质保期/响应时限/工期）在不同章节出现多个取值
     → 矛盾承诺会被评审质疑，标「中」。

只读 bid_document，不调用 LLM，不修改正文。关键词与正则命中都是启发式判断，风险
文案已注明需人工核验，避免把启发式结果当成结论。

与 tender_matching.evidence_validation（解析阶段校验抽取引文是否被原文支撑）互补：
那是「招标要求抽取对不对」，这里是「投标正文的事实有没有来源、承诺有没有自相矛盾」。
"""
from __future__ import annotations

import re
from typing import Any

# 可核验的企业事实断言关键词（资质/业绩/人员/知识产权/金额五类，聚焦高频幻觉来源）。
FACT_KEYWORDS: tuple[str, ...] = (
    "资质", "认证", "证书",
    "业绩", "中标", "承建", "合同额",
    "职称", "项目经理", "技术负责人", "注册",
    "专利", "软件著作权", "著作权",
    "万元",
)

# 资料缺失占位符（撰写阶段按约定写入，命中说明证据槽位无材料）。
GAP_MARKERS: tuple[str, ...] = (
    "【需人工补充", "【待补充", "【需补充", "待补充",
)

# 承诺值概念 → 提取正则（刻意收紧：仅「概念 + 短距离内 数字+单位」，降低误报）。
_COMMITMENT_PATTERNS: tuple[tuple[str, str], ...] = (
    ("质保期", r"质保期[^。；;\n]{0,10}?(\d+(?:\.\d+)?)\s*(个月|年)"),
    (
        "响应时限",
        r"响应(?:时间|时限)[^。；;\n]{0,10}?(\d+(?:\.\d+)?)\s*(个工作日|个?小时|分钟|天)",
    ),
    ("工期/交货期", r"(?:工期|交货期|实施周期)[^。；;\n]{0,10}?(\d+(?:\.\d+)?)\s*(个?月|天|周)"),
)

# 命中这些标记的句子视为「招标要求引用」，不计入投标人承诺（避免要求 vs 承诺误判）。
_REQUIREMENT_MARKERS: tuple[str, ...] = (
    "不少于", "不低于", "不高于", "应满足", "招标文件要求", "采购人要求", "须满足",
)

# 固定文书/附件/偏离表不逐条溯源（投标函、承诺书、偏离表等本身不挂 sourceRefs）。
_SKIP_SOURCES = {"template", "copied_template", "attachment", "deviation"}
_SKIP_KINDS = {"deviation", "attachment"}


def _sections(bid_document: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(bid_document, dict):
        return []
    plan = bid_document.get("plan")
    return [section for section in plan if isinstance(section, dict)] if isinstance(plan, list) else []


def _is_evidence_scoped(section: dict[str, Any]) -> bool:
    """是否属于需要逐条溯源的叙述/事实章节（排除固定文书、附件、偏离表）。"""
    kind = str(section.get("kind") or "").strip()
    source = str(section.get("source") or "").strip()
    if kind in _SKIP_KINDS or source in _SKIP_SOURCES:
        return False
    return True


def _label(section: dict[str, Any]) -> str:
    return (
        str(section.get("sectionId") or "").strip()
        or str(section.get("title") or "").strip()
        or "未命名章节"
    )


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[。；;\n]", text) if part.strip()]


def audit_commitment_consistency(bid_document: dict[str, Any] | None) -> dict[str, Any]:
    """跨章承诺一致性：同一承诺概念出现多个取值时报「中」。

    返回 {"riskItems": [...], "conflicts": [{concept, values}]}。
    仅统计非「招标要求引用」的句子，且仅在同一概念出现 ≥2 个不同取值时才报。
    """
    counter: dict[str, dict[str, list[str]]] = {}
    for section in _sections(bid_document):
        content = str(section.get("content") or "").strip()
        if not content or not _is_evidence_scoped(section):
            continue
        label = _label(section)
        for sentence in _sentences(content):
            if any(marker in sentence for marker in _REQUIREMENT_MARKERS):
                continue
            for concept, pattern in _COMMITMENT_PATTERNS:
                for match in re.finditer(pattern, sentence):
                    value = f"{match.group(1)}{match.group(2)}"
                    labels = counter.setdefault(concept, {}).setdefault(value, [])
                    if label not in labels:
                        labels.append(label)

    risk_items: list[dict[str, str]] = []
    conflicts: list[dict[str, Any]] = []
    for concept in sorted(counter):
        values = counter[concept]
        if len(values) < 2:
            continue
        conflicts.append({"concept": concept, "values": sorted(values)})
        detail_bits = "；".join(
            f"{value}（{'、'.join(sorted(labels))}）"
            for value, labels in sorted(values.items())
        )
        risk_items.append(
            {
                "level": "中",
                "title": "承诺值跨章不一致",
                "detail": (
                    f"「{concept}」在不同章节出现多个取值：{detail_bits}；"
                    f"自相矛盾的承诺在评审中会被质疑，请核对后统一口径"
                    f"（简单正则提取，请人工核验）。"
                ),
            }
        )
    return {"riskItems": risk_items, "conflicts": conflicts}


def audit_evidence(bid_document: dict[str, Any] | None) -> dict[str, Any]:
    """对已生成正文做证据溯源 + 跨章承诺一致性审计。

    返回 {"riskItems": [{level,title,detail}], "summary": {...}}，
    riskItems 复用 proofread 的约定（level ∈ 高/中/低）。
    """
    sections = _sections(bid_document)
    risk_items: list[dict[str, str]] = []
    no_trace: list[str] = []
    missing_path: list[str] = []
    gaps: list[dict[str, Any]] = []

    content_sections = 0
    for section in sections:
        content = str(section.get("content") or "").strip()
        if not content:
            continue
        if not _is_evidence_scoped(section):
            continue
        content_sections += 1
        label = _label(section)

        refs = [
            ref for ref in (section.get("sourceRefs") or []) if isinstance(ref, dict)
        ]
        gap_hits = [marker for marker in GAP_MARKERS if marker in content]
        fact_hits = [keyword for keyword in FACT_KEYWORDS if keyword in content]

        # 1) 无溯源断言：有事实关键词、整章无 sourceRefs、且未声明为缺口。
        if fact_hits and not refs and not gap_hits:
            no_trace.append(label)
            risk_items.append(
                {
                    "level": "高",
                    "title": "无溯源断言",
                    "detail": (
                        f"章节「{label}」正文出现可核验的企业事实表述"
                        f"（命中关键词：{'、'.join(fact_hits)}），但本章没有任何 sourceRefs "
                        f"溯源引用，存在无来源断言/幻觉风险；请补材料来源或改为【需人工补充】"
                        f"（简单关键词检查，请人工核验）。"
                    ),
                }
            )

        # 2) 证据路径缺失：sourceRefs 未带 evidencePath。
        bad_refs = [
            str(ref.get("name") or ref.get("factKey") or "").strip()
            for ref in refs
            if not str(ref.get("evidencePath") or "").strip()
        ]
        if bad_refs:
            missing_path.append(label)
            names = "、".join(item for item in bad_refs if item) or "未命名引用"
            risk_items.append(
                {
                    "level": "中",
                    "title": "证据路径缺失",
                    "detail": (
                        f"章节「{label}」有 {len(bad_refs)} 条溯源引用未带 evidencePath"
                        f"（{names}），无法回溯到材料原件，请补证据路径后再导出。"
                    ),
                }
            )

        # 3) 能力缺口：占位符残留。
        if gap_hits:
            gap_count = sum(content.count(marker) for marker in set(gap_hits))
            gaps.append({"sectionId": label, "markerCount": gap_count})
            risk_items.append(
                {
                    "level": "中",
                    "title": "材料缺口未补齐",
                    "detail": (
                        f"章节「{label}」正文保留了 {gap_count} 处待补充标记"
                        f"（{'、'.join(sorted(set(gap_hits)))}），说明企业材料或证据不足；"
                        f"请在投标前补齐材料并重写该段。"
                    ),
                }
            )

    # 4) 承诺值跨章不一致。
    consistency = audit_commitment_consistency(bid_document)
    risk_items.extend(consistency["riskItems"])

    return {
        "riskItems": risk_items,
        "summary": {
            "contentSectionTotal": content_sections,
            "noTraceSectionIds": no_trace,
            "missingPathSectionIds": missing_path,
            "gapSectionIds": [item["sectionId"] for item in gaps],
            "gapMarkerCount": sum(item["markerCount"] for item in gaps),
            "commitmentConflicts": consistency["conflicts"],
        },
    }
