"""Deterministic first-pass discovery of business ranges in a tender document.

This module deliberately finds *where* each business target lives.  It does
not split requirements or decide enterprise-match status; those remain
domain-specific downstream processors.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from ...tender_matching.document_models import DocumentIR, DocumentSection, DocumentTable
from .schemas import (
    DocumentMap,
    DocumentMapSection,
    DocumentRange,
    DocumentSourceAnchor,
    RejectionCandidate,
    TemplateLocator,
)


@dataclass(frozen=True)
class TargetDefinition:
    target: str
    label: str
    heading_terms: tuple[str, ...]
    path_terms: tuple[str, ...] = ()
    body_terms: tuple[str, ...] = ()
    table_terms: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    description: str = ""


TARGET_DEFINITIONS: tuple[TargetDefinition, ...] = (
    TargetDefinition(
        "technical",
        "技术产品服务",
        (
            "技术要求",
            "技术需求",
            "技术参数",
            "技术和服务要求",
            "功能要求",
            "功能需求",
            "性能指标",
            "采购需求",
            "采购内容",
            "招标内容及要求",
            "采购标的",
            "服务要求",
            "服务内容",
            "实施要求",
            "交付要求",
        ),
        body_terms=(
            "技术参数",
            "功能要求",
            "性能指标",
            "必须支持",
            "需支持",
            "应支持",
            "产品名称",
            "设备参数",
            "技术指标",
        ),
        table_terms=(
            "技术要求",
            "技术参数",
            "参数与性能指标",
            "产品名称",
            "品目名称",
            "标的名称",
            "响应情况",
        ),
        exclude_terms=(
            "合同范本",
            "合同条款",
            "评分标准",
            "投标函",
            "中小企业",
        ),
        description="产品、系统、服务能力以及可逐条核验的技术参数范围",
    ),
    TargetDefinition(
        "qualification",
        "资格审查",
        (
            "资格审查",
            "资格条件",
            "资格要求",
            "合格投标人",
            "资质要求",
            "资格及资信证明",
            "资格证明",
            "申请人的资格",
            "供应商资格",
        ),
        body_terms=(
            "营业执照",
            "资质证书",
            "许可证",
            "财务报告",
            "审计报告",
            "纳税证明",
            "社会保障资金",
            "类似业绩",
            "项目负责人",
            "信用中国",
            "联合体",
        ),
        table_terms=(
            "资格审查",
            "资格条件",
            "证明材料",
            "营业执照",
            "资质证书",
            "业绩",
            "人员",
        ),
        exclude_terms=("评标委员会", "评标办法", "合同范本"),
        description="投标人主体资格、资质、业绩、人员、财务和信用要求",
    ),
    TargetDefinition(
        "commercial",
        "商务要求",
        (
            "商务要求",
            "商务条件",
            "商务条款",
            "服务内容及商务要求",
            "交付要求",
            "交货要求",
            "售后服务",
            "付款方式",
        ),
        body_terms=(
            "交付期限",
            "交货期",
            "交货地点",
            "付款方式",
            "付款条件",
            "质保期",
            "售后服务",
            "服务期限",
            "商务响应",
        ),
        table_terms=("商务要求", "商务条件", "交付期限", "质保期", "付款方式"),
        exclude_terms=("合同范本", "评分标准"),
        description="报价、付款、交付、质保、售后和其他商务条件",
    ),
    TargetDefinition(
        "evaluation",
        "评分标准",
        (
            "评分标准",
            "评审标准",
            "评标办法",
            "评标方法和标准",
            "评标方法",
            "评审办法",
            "评审因素",
            "评分因素",
            "评标细则",
            "评分细则",
        ),
        body_terms=("评分标准", "评审因素", "分值", "得分", "评标委员会"),
        table_terms=("评分项目", "评审因素", "分值", "评分标准", "得分"),
        exclude_terms=("合同范本",),
        description="评分项目、分值、评分条件和证明材料要求",
    ),
    TargetDefinition(
        "template",
        "投标文件模板",
        (
            "投标文件格式",
            "响应文件格式",
            "投标函",
            "响应函",
            "响应表",
            "技术响应表",
            "商务响应表",
            "报价明细表",
            "偏离表",
            "承诺函",
            "声明函",
            "授权委托书",
            "资格及资信证明文件",
        ),
        body_terms=("投标人填写", "响应情况", "偏离情况", "签字盖章", "加盖公章"),
        table_terms=("投标响应", "响应情况", "偏离情况", "投标人填写", "备注"),
        exclude_terms=(),
        description="需要原样填写、签章、下载或交给写标书流程的模板",
    ),
    TargetDefinition(
        "contract",
        "中标后合同部分",
        (
            "合同条款",
            "合同主要条款",
            "采购合同",
            "服务合同",
            "合同范本",
            "合同文本",
        ),
        body_terms=("合同签订后", "违约责任", "合同履行", "合同解除", "争议解决"),
        table_terms=("合同条款", "合同内容", "违约责任"),
        description="中标后的合同履行、违约和争议处理内容",
    ),
)

REJECTION_TERMS: tuple[str, ...] = (
    "投标无效",
    "无效投标",
    "响应无效",
    "无效响应",
    "废标",
    "否决投标",
    "否决响应",
    "不予受理",
    "不得参加",
    "取消资格",
    "超过最高限价",
    "超过预算金额",
    "实质性偏离",
    "未按要求签字盖章",
)

PROJECT_INFO_TERMS: tuple[str, ...] = (
    "项目名称",
    "项目编号",
    "采购人",
    "招标人",
    "代理机构",
    "预算金额",
    "最高限价",
    "投标截止时间",
    "开标时间",
    "投标保证金",
    "履约保证金",
    "服务期限",
    "交付期限",
    "是否接受联合体",
    "是否允许分包",
    "电子投标",
)

_DEFINITION_BY_TARGET = {item.target: item for item in TARGET_DEFINITIONS}
_CONTAINER_HEADINGS = (
    "招标内容及要求",
    "采购内容",
    "采购需求",
    "采购项目",
    "项目需求",
)


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _navigation_only(section: DocumentSection, *, table_text: str) -> bool:
    body = _compact(section.text)
    heading = _compact(section.heading)
    if not body or not heading or table_text:
        return False
    return body == heading or len(body) <= len(heading) + 12


def _section_id(section: DocumentSection) -> str:
    return str(section.section_id or section.id or "")


def _table_id(table: DocumentTable) -> str:
    return str(table.table_id or table.id or "")


def _table_headers(table: DocumentTable) -> list[str]:
    if not table.rows:
        return []
    return [
        str(cell.text or "").strip()
        for cell in table.rows[0].cells
        if str(cell.text or "").strip()
    ]


def _section_table_ids(
    section: DocumentSection,
    segment_by_id: dict[str, object],
) -> list[str]:
    return list(
        dict.fromkeys(
            str(getattr(segment_by_id[item], "table_id", "") or "")
            for item in section.segment_ids
            if item in segment_by_id
            and str(getattr(segment_by_id[item], "table_id", "") or "")
        )
    )


def _score_definition(
    definition: TargetDefinition,
    *,
    heading: str,
    path: str,
    body: str,
    table_text: str,
    has_children: bool,
) -> tuple[int, list[str]]:
    heading_value = _compact(heading)
    path_value = _compact(path)
    body_value = _compact(body)
    table_value = _compact(table_text)
    score = 0
    evidence: list[str] = []
    for term in definition.heading_terms:
        if _compact(term) in heading_value:
            score += 10
            evidence.append(f"标题:{term}")
    for term in definition.path_terms:
        if _compact(term) in path_value:
            score += 4
            evidence.append(f"路径:{term}")
    for term in definition.table_terms:
        if _compact(term) in table_value:
            score += 5
            evidence.append(f"表头:{term}")
    for term in definition.body_terms:
        if _compact(term) in body_value:
            score += 2
            evidence.append(f"正文:{term}")
    for term in definition.exclude_terms:
        if _compact(term) in heading_value or _compact(term) in path_value:
            score -= 12
            evidence.append(f"排除:{term}")
    if has_children and heading_value in {_compact(item) for item in _CONTAINER_HEADINGS}:
        score -= 3
        evidence.append("容器标题")
    return score, list(dict.fromkeys(evidence))


def _best_target(
    section: DocumentSection,
    *,
    table_text: str,
    has_children: bool,
) -> tuple[str, int, list[str], list[tuple[str, int]]]:
    path = " ".join(section.section_path or [])
    body = str(section.text or "")[:2400]
    scored = [
        (
            definition.target,
            *_score_definition(
                definition,
                heading=section.heading,
                path=path,
                body=body,
                table_text=table_text,
                has_children=has_children,
            ),
        )
        for definition in TARGET_DEFINITIONS
    ]
    heading_value = _compact(section.heading)
    table_value = _compact(table_text)
    if has_children and any(
        _compact(item) in heading_value for item in _CONTAINER_HEADINGS
    ):
        # A broad chapter such as "第五章 招标内容及要求" is a structural
        # parent. Its child ranges carry the actual business destination.
        scored = [
            (target, min(score, 7), evidence)
            for target, score, evidence in scored
        ]
    template_score = next(
        (item[1] for item in scored if item[0] == "template"),
        0,
    )
    template_heading_hit = any(
        _compact(term) in heading_value
        for term in _DEFINITION_BY_TARGET["template"].heading_terms
    )
    template_table_hit = any(
        _compact(term) in table_value
        for term in _DEFINITION_BY_TARGET["template"].table_terms
    )
    if template_score >= 10 and (template_heading_hit or template_table_hit):
        bonus = 18 if template_heading_hit or template_table_hit else 8
        scored = [
            (target, score + bonus if target == "template" else score, evidence)
            for target, score, evidence in scored
        ]
    # Body-only hits are useful for leaf discovery, but they are too weak to
    # turn broad chapters such as "投标邀请" or "招标内容及要求" into a
    # business scope. Containers need an explicit title/path/table signal.
    if has_children:
        explicit_targets = {
            target
            for target, _score, evidence in scored
            if any(marker.startswith(("标题:", "路径:")) for marker in evidence)
        }
        scored = [
            (target, score if target in explicit_targets else min(score, 7), evidence)
            for target, score, evidence in scored
        ]
    scored.sort(key=lambda item: item[1], reverse=True)
    target, score, evidence = scored[0]
    return target, score, evidence, [(item[0], item[1]) for item in scored]


def _anchor(
    document: DocumentIR,
    section: DocumentSection,
    *,
    segment_ids: Iterable[str] | None = None,
    table_ids: Iterable[str] | None = None,
) -> DocumentSourceAnchor:
    segments = list(segment_ids or section.segment_ids)
    tables = list(table_ids or [])
    return DocumentSourceAnchor(
        documentId=document.document_id,
        documentVersionId=document.document_version_id,
        sourceFileId=document.file_hash or document.file_name,
        sourceFormat=document.original_format or "unknown",
        sectionId=_section_id(section),
        sectionIds=[_section_id(section)],
        segmentId=segments[0] if len(segments) == 1 else "",
        segmentIds=list(dict.fromkeys(item for item in segments if item)),
        tableIds=list(dict.fromkeys(item for item in tables if item)),
        sourceLocator=section.start_locator or section.end_locator,
        part=section.heading,
    )


def _map_range(
    document: DocumentIR,
    section: DocumentSection,
    *,
    table_ids: list[str],
    summary: str,
) -> DocumentRange:
    segment_ids = list(dict.fromkeys(section.segment_ids))
    return DocumentRange(
        rangeId=f"RANGE-{_section_id(section)}",
        startSegmentId=segment_ids[0] if segment_ids else "",
        endSegmentId=segment_ids[-1] if segment_ids else "",
        segmentIds=segment_ids,
        sectionIds=[_section_id(section)],
        tableIds=table_ids,
        source=_anchor(document, section, table_ids=table_ids),
        contentSummary=summary[:300],
    )


def _map_section(
    document: DocumentIR,
    section: DocumentSection,
    *,
    target: str,
    score: int,
    evidence: list[str],
    context_section_ids: list[str],
    table_ids: list[str],
    has_children: bool,
) -> DocumentMapSection | None:
    if score < 8:
        return None
    definition = _DEFINITION_BY_TARGET[target]
    section_id = _section_id(section)
    role = "container" if has_children else "leaf"
    if target in {"technical", "qualification"} and role == "container":
        processing_mode = "display"
        route = "display_processor"
    elif target in {"technical", "qualification"}:
        processing_mode = "fine_extract"
        route = f"{target}_processor"
    elif target == "template":
        processing_mode = "template_extract"
        route = "template_processor"
    elif target == "contract":
        processing_mode = "ignore"
        route = "ignored_contract"
    else:
        processing_mode = "display"
        route = (
            "evaluation_processor"
            if target == "evaluation"
            else "display_processor"
        )
    confidence = min(0.99, max(0.5, 0.5 + score / 40))
    source = _anchor(document, section, table_ids=table_ids)
    locator = None
    if target == "template":
        locator = TemplateLocator(
            templateId=f"TPL-{section_id}",
            templateType="response_template",
            title=section.heading,
            sourceFileId=document.file_hash or document.file_name,
            sourceFormat=document.original_format or "unknown",
            sectionIds=[section_id],
            segmentIds=list(section.segment_ids),
            tableIds=table_ids,
            sourceLocator=source.source_locator,
            extractMode="on_demand",
            downloadable=True,
            writingAgentUsable=True,
        )
    return DocumentMapSection(
        mapId=f"MAP-{target}-{section_id}",
        documentId=document.document_id,
        documentVersionId=document.document_version_id,
        domain=target,
        secondaryDomains=(
            ["technical"]
            if target == "template"
            and any("技术" in item or "参数" in item for item in evidence)
            else []
        ),
        role=role,
        processingMode=processing_mode,
        ownerSectionId=section_id if role == "leaf" else "",
        contextSectionIds=context_section_ids,
        title=section.heading,
        description=definition.description,
        sectionIds=[section_id],
        sourceSegmentIds=list(section.segment_ids),
        ranges=[
            _map_range(
                document,
                section,
                table_ids=table_ids,
                summary=definition.description,
            )
        ],
        tableIds=table_ids,
        primaryRoute=route,
        priority="high" if target in {"technical", "qualification", "rejection"} else "medium",
        confidence=confidence,
        needsHumanReview=score < 14,
        reviewStatus="pending" if score < 14 else "not_required",
        ignored=target == "contract",
        reason="；".join(evidence[:6]),
        classificationSource="rule",
        source=source,
        templateLocator=locator,
    )


def _rejection_candidates(document: DocumentIR) -> list[RejectionCandidate]:
    result: list[RejectionCandidate] = []
    for section in document.sections:
        for segment_id in section.segment_ids:
            segment = next(
                (item for item in document.segments if item.id == segment_id),
                None,
            )
            if segment is None:
                continue
            text = str(segment.text or "")
            for term in REJECTION_TERMS:
                if term not in text:
                    continue
                result.append(
                    RejectionCandidate(
                        candidateId=f"REJ-{segment.id}-{term}",
                        documentId=document.document_id,
                        matchedTerm=term,
                        text=text[:1200],
                        contextText=text[:1600],
                        source=_anchor(document, section, segment_ids=[segment.id]),
                        contextSegmentIds=[segment.id],
                        status="candidate",
                        isHardRisk=term in {
                            "投标无效",
                            "无效投标",
                            "响应无效",
                            "废标",
                            "否决投标",
                        },
                        reason="全文否决/无效信号召回，待上下文确认",
                    )
                )
    return result


def discover_document_map(
    document: DocumentIR,
    *,
    run_id: str = "RUN-TARGET-DISCOVERY",
) -> DocumentMap:
    """Build a source-addressable first-pass business map from DocumentIR."""

    segment_by_id = {item.id: item for item in document.segments if item.id}
    table_by_id = {_table_id(item): item for item in document.tables if _table_id(item)}
    children_by_parent: dict[str, list[DocumentSection]] = defaultdict(list)
    for section in document.sections:
        if section.parent_id:
            children_by_parent[section.parent_id].append(section)

    active: list[DocumentMapSection] = []
    ignored: list[DocumentMapSection] = []
    unresolved: list[str] = []
    for section in document.sections:
        section_id = _section_id(section)
        if not section_id:
            continue
        table_ids = _section_table_ids(section, segment_by_id)
        table_text = " ".join(
            " ".join(_table_headers(table_by_id[item]))
            for item in table_ids
            if item in table_by_id
        )
        if _navigation_only(section, table_text=table_text):
            continue
        target, score, evidence, scored = _best_target(
            section,
            table_text=table_text,
            has_children=bool(children_by_parent.get(section_id)),
        )
        if score < 8:
            continue
        context_ids = [section.parent_id] if section.parent_id else []
        mapped = _map_section(
            document,
            section,
            target=target,
            score=score,
            evidence=evidence,
            context_section_ids=[item for item in context_ids if item],
            table_ids=table_ids,
            has_children=bool(children_by_parent.get(section_id)),
        )
        if mapped is None:
            continue
        if target == "contract":
            ignored.append(mapped)
        else:
            active.append(mapped)
        if len(scored) > 1 and scored[0][1] - scored[1][1] <= 2:
            unresolved.append(section_id)

    rejection_candidates = _rejection_candidates(document)
    if rejection_candidates:
        source_section_ids = list(
            dict.fromkeys(
                candidate.source.section_id
                for candidate in rejection_candidates
                if candidate.source.section_id
            )
        )
        source_segment_ids = list(
            dict.fromkeys(
                segment_id
                for candidate in rejection_candidates
                for segment_id in candidate.source.segment_ids
                if segment_id
            )
        )
        first_section = next(
            (
                item
                for item in document.sections
                if _section_id(item) in source_section_ids
            ),
            document.sections[0] if document.sections else DocumentSection(),
        )
        anchor = _anchor(
            document,
            first_section,
            segment_ids=source_segment_ids,
        ).model_copy(
            update={
                "section_id": source_section_ids[0] if source_section_ids else "",
                "section_ids": source_section_ids,
            }
        )
        active.append(
            DocumentMapSection(
                mapId="MAP-rejection-overlay",
                documentId=document.document_id,
                documentVersionId=document.document_version_id,
                domain="rejection",
                role="overlay",
                processingMode="full_scan",
                ownerSectionId="",
                title="废标/无效/否决项",
                description="全文正则召回的否决风险候选",
                sectionIds=source_section_ids,
                sourceSegmentIds=source_segment_ids,
                ranges=[
                    DocumentRange(
                        rangeId="RANGE-rejection-overlay",
                        segmentIds=source_segment_ids,
                        sectionIds=source_section_ids,
                        source=anchor,
                        contentSummary="；".join(
                            dict.fromkeys(
                                item.matched_term
                                for item in rejection_candidates
                            )
                        ),
                    )
                ],
                primaryRoute="rejection_processor",
                priority="high",
                confidence=0.99,
                needsHumanReview=True,
                reviewStatus="pending",
                reason="全文否决信号覆盖层",
                classificationSource="rule",
                source=anchor,
            )
        )

    all_segment_ids = [item.id for item in document.segments if item.id]
    project_hits = [
        item
        for item in document.segments
        if any(term in str(item.text or "") for term in PROJECT_INFO_TERMS)
    ]
    if project_hits:
        project_section = project_hits[0]
        section = next(
            (
                item
                for item in document.sections
                if project_section.id in item.segment_ids
            ),
            document.sections[0] if document.sections else DocumentSection(),
        )
        anchor = _anchor(document, section, segment_ids=all_segment_ids)
        active.append(
            DocumentMapSection(
                mapId="MAP-project_info-overlay",
                documentId=document.document_id,
                documentVersionId=document.document_version_id,
                domain="project_info",
                role="overlay",
                processingMode="full_scan",
                title="项目关键基础信息",
                description="全文抽取项目身份、范围、报价、费用、时间和递交方式",
                sectionIds=list(dict.fromkeys(item.section_id for item in document.sections if item.section_id)),
                sourceSegmentIds=all_segment_ids,
                primaryRoute="display_processor",
                priority="high",
                confidence=0.8,
                needsHumanReview=False,
                reason="全文字段抽取覆盖层",
                classificationSource="rule",
                source=anchor,
            )
        )

    return DocumentMap(
        mapId=f"DM-{document.document_version_id or document.document_id}",
        runId=run_id,
        documentId=document.document_id,
        documentVersionId=document.document_version_id,
        documentType="tender_document",
        sections=active,
        ignoredSections=ignored,
        rejectionCandidates=rejection_candidates,
        unresolvedSections=list(dict.fromkeys(unresolved)),
        warnings=[],
    )
