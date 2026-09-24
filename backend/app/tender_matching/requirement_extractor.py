"""Focused first-pass extraction of decision-relevant tender requirements."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .document_chunking import chunk_document
from .document_models import DocumentChunk, DocumentIR, DocumentSection
from .fact_models import (
    EvidenceFact,
    ExtractedFieldFact,
    RequirementExtractionResult,
    RequirementFact,
    evidence_from_segment,
)
from .section_classifier import (
    SectionClassification,
    classify_clause,
    classify_document,
)
from .table_semantics import build_table_row_semantics, product_context_from_segments


LLM_REQUIREMENT_EXTRACTION_PROMPT = """\
你是招标文件要求抽取器。只抽取可以回到原文的项目基本信息、技术/产品/服务要求、
资质审查要求、商务/递交要求、时间节点和废标风险。每条独立可检查的要求输出一条。
必须保留 sourceSegmentIds 和 sourceQuote；没有原文证据不得输出确定事实。
技术产品服务要求优先，背景说明和重复标题不要输出为 Requirement。
"""

REQUIREMENT_HINTS = (
    "必须",
    "须",
    "应当",
    "应",
    "不得",
    "不低于",
    "不少于",
    "至少",
    "提供",
    "具备",
    "要求",
    "需满足",
)

STRONG_REQUIREMENT_HINTS = (
    "必须",
    "须",
    "应当",
    "不得",
    "不低于",
    "不少于",
    "至少",
    "需满足",
    "应满足",
    "应提供",
    "须提供",
    "需提供",
)

REJECTION_HINTS = ("废标", "无效投标", "无效响应", "否决", "实质性偏离")

DETAILED_REQUIREMENT_CATEGORIES = {
    "technical",
    "qualification",
    "performance",
    "personnel",
    "financial",
    "commercial",
    "pricing",
    "schedule",
    "rejection",
}

SKIP_FINE_SECTION_CATEGORIES = {
    "document_format",
    "contract",
    "attachment",
    "evaluation",
    "service_plan",
}

BROAD_CONTEXT_CATEGORIES = {
    "submission",
    "other",
}

FIELD_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("projectName", ("项目名称", "采购项目名称", "招标项目名称")),
    ("projectCode", ("项目编号", "采购项目编号", "招标编号")),
    ("tendererName", ("采购人", "招标人")),
    ("agencyName", ("代理机构", "采购代理机构")),
    ("budgetAmount", ("预算金额", "最高限价", "采购预算")),
    ("deadline", ("投标截止时间", "响应文件提交截止时间")),
    ("openingTime", ("开标时间", "开启时间")),
    ("servicePeriod", ("服务期限", "交付期限", "工期")),
)


def _clean(text: Any, limit: int = 2400) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _stable_id(prefix: str, *values: str) -> str:
    digest = hashlib.sha256("\n".join(values).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


COMPOUND_MARKER_PATTERN = re.compile(
    r"(?:[（(]\s*[一二三四五六七八九十百千万零〇两0-9]+\s*[）)]"
    r"|[①②③④⑤⑥⑦⑧⑨⑩]|\d+[、．)]|\d+\.(?!\d))"
)


def _clauses(content: str) -> list[str]:
    lines: list[str] = []
    for raw_line in str(content or "").splitlines():
        # Split BEFORE the per-fragment length cap. A dense spec-table cell can
        # be thousands of chars ("1.支持… 2.支持…"), and capping the whole line
        # first silently dropped everything past 2400 chars.
        parts = re.split(r"(?<=[。！？；;])\s*|[；;]", raw_line)
        for part in parts:
            line = _clean(part, 2400)
            stripped = line.strip(" \t-")
            if stripped:
                lines.append(stripped)
    # Second pass: split numbered compound clauses ("1.… 2.… 3.…",
    # "（1）…（2）…", "①…②…") into individual requirements, mirroring the
    # fine-matching rule channel.
    expanded: list[str] = []
    for line in lines:
        markers = list(COMPOUND_MARKER_PATTERN.finditer(line))
        if len(markers) >= 2:
            clauses = [
                line[
                    marker.start() : markers[index + 1].start()
                    if index + 1 < len(markers)
                    else len(line)
                ].strip(" ；;。")
                for index, marker in enumerate(markers)
            ]
            clauses = [clause for clause in clauses if clause]
            if len(clauses) >= 2:
                expanded.extend(clauses)
                continue
        expanded.append(line)
    return list(dict.fromkeys(expanded))


def _has_requirement_signal(text: str) -> bool:
    if any(term in text for term in STRONG_REQUIREMENT_HINTS):
        return True
    if re.search(r"(?<!不)应(?:支持|提供|满足|具备|达到|完成|提交|遵守|按照)", text):
        return True
    if "具备" in text or "要求" in text:
        return True
    # Technical spec signals: capability ("支持…"), a bounded parameter
    # ("≥/≤/大于/小于/不低于/…"), fine-matching signal terms, or a leading
    # numbered marker ("1.…"/"（1）…"/"①…").  These dominate procurement spec
    # tables and were previously dropped, under-extracting technical
    # requirements.
    if (
        "支持" in text
        or re.search(r"[≥≤]|不小于|不大于|不低于|不超过|大于|小于", text)
        or any(
            term in text
            for term in (
                "兼容", "满足", "符合", "参数", "性能", "指标", "规格",
                "精度", "范围", "并发", "吞吐", "具有", "交付", "验收",
            )
        )
    ):
        return True
    if re.match(
        r"^(?:\d+(?:\.\d+)*[、.．)]|[（(]\d+[)）]|[①②③④⑤⑥⑦⑧⑨⑩])",
        text,
    ):
        return True
    if "提供" not in text:
        return False
    if re.match(r"^(本项目|本次采购|本招标项目|项目)[^。；;]{0,12}提供", text):
        return False
    # A bare "提供" in project background or an introduction is not enough.
    # Keep it when the sentence names a responsible party or a concrete
    # deliverable that can be checked independently.
    return bool(
        re.search(r"(投标人|供应商|报价人|乙方|承包人|采购人)[^。；;]{0,18}提供", text)
        or re.search(
            r"提供[^。；;]{0,24}(证明|材料|文件|方案|服务|设备|证书|报告|响应|承诺|清单|参数)",
            text,
        )
    )


def _heading_variants(document: DocumentIR) -> set[str]:
    variants: set[str] = set()
    for section in document.sections:
        heading = _clean(section.heading, 240)
        if not heading:
            continue
        variants.add(heading)
        variants.add(re.sub(r"\s*[-—–]\s*\d+\s*[-—–]?\s*$", "", heading).strip())
        variants.add(re.sub(r"\s+\d+\s*$", "", heading).strip())
    return {item for item in variants if item}


def _is_non_requirement_clause(clause: str, heading_variants: set[str]) -> bool:
    clean = _clean(clause, 2400)
    if not clean:
        return True
    without_page = re.sub(r"\s*[-—–]\s*\d+\s*[-—–]?\s*$", "", clean).strip()
    without_page = re.sub(r"\s+\d+\s*$", "", without_page).strip()
    if clean in heading_variants or without_page in heading_variants:
        return True
    compact = re.sub(r"\s+", "", clean)
    heading_compacts = {
        re.sub(r"\s+", "", item)
        for item in heading_variants
        if item
    }
    if any(compact.startswith(item) for item in heading_compacts):
        remainder = next(
            (
                compact[len(item):]
                for item in heading_compacts
                if compact.startswith(item)
            ),
            "",
        )
        if not remainder or any(
            term in remainder
            for term in ("序号", "参数性质", "采购包", "响应情况", "评审点")
        ):
            return True
    if "|" in clean and any(
        term in clean
        for term in ("序号", "项目名称", "评审点", "响应情况", "采购内容", "技术参数", "报价")
    ):
        return True
    # A numbered title such as "三、商务要求（...）" or "（1）报价要求："
    # is context, not a standalone requirement. Keep it only when the
    # remainder contains an actionable requirement.
    title_like = re.match(
        r"^(?:第[一二三四五六七八九十百]+章|"
        r"[一二三四五六七八九十]+、|"
        r"[（(]\d+[）)])"
        r"\s*[^。；;:：]{1,90}"
        r"(?:[：:]|$)",
        clean,
    )
    if title_like:
        tail = clean[title_like.end():]
        if not any(
            term in tail
            for term in (
                "应",
                "须",
                "必须",
                "不得",
                "提供",
                "支持",
                "具备",
                "完成",
                "交付",
                "支付",
                "报价",
                "质保",
            )
        ):
            return True
    if re.search(r"(?:[-—–]\s*\d+\s*[-—–]?)$", clean) and not any(
        term in clean for term in STRONG_REQUIREMENT_HINTS
    ):
        return True
    return False


def _category(classification: SectionClassification | None) -> str:
    if classification is None:
        return "other"
    return classification.primary_type


def _should_keep_requirement(
    *,
    section_category: str,
    requirement_category: str,
) -> bool:
    """Decide whether this clause should enter fine-grained requirement rows."""

    # Templates and broad service chapters stay section-level by default, but
    # real technical rows and hard rejection clauses still need downstream
    # matching/risk checks.
    if section_category == "document_format":
        # 模板本体只保留结构、字段和表格索引。只有模板中确实出现了
        # 可独立核验的业务要求，才允许进入对应的细粒度路由；普通填写、
        # 签章、格式和证明材料说明不在这里生成 Requirement。
        return requirement_category in {
            "technical",
            "qualification",
            "performance",
            "personnel",
            "financial",
            "credit",
            "region",
            "rejection",
        }
    if section_category == "service_plan":
        return requirement_category in {"technical", "rejection"}
    if section_category in SKIP_FINE_SECTION_CATEGORIES:
        return False
    if requirement_category not in DETAILED_REQUIREMENT_CATEGORIES:
        return False
    if section_category in BROAD_CONTEXT_CATEGORIES:
        return requirement_category not in BROAD_CONTEXT_CATEGORIES
    return True


def _needs_requirement_review(
    *,
    section_category: str,
    requirement_category: str,
    section_review: bool,
    clause_review: bool,
) -> bool:
    if clause_review:
        return True
    if section_category in BROAD_CONTEXT_CATEGORIES:
        return section_review
    return False


def _importance(category: str, text: str) -> str:
    if category in {"technical", "service_plan", "rejection", "qualification"}:
        return "high"
    if any(term in text for term in ("必须", "不得", "不低于", "不少于", "至少")):
        return "high"
    return "medium"


def _field_candidates(document: DocumentIR) -> list[tuple[str, str, str]]:
    candidates: list[tuple[str, str, str]] = []
    for segment in document.segments:
        text = segment.text
        for field_name, labels in FIELD_PATTERNS:
            for label in labels:
                match = re.search(rf"{re.escape(label)}\s*[:：]\s*(.+)", text)
                if match:
                    value = _clean(match.group(1), 300)
                    if value:
                        candidates.append((field_name, value, segment.id))
                        break
    return candidates


def extract_requirements(
    document: DocumentIR,
    *,
    chunks: list[DocumentChunk] | None = None,
    classifications: list[SectionClassification] | None = None,
    max_requirements: int = 2000,
) -> RequirementExtractionResult:
    """Extract the high-value first-pass requirement set without an LLM."""

    if max_requirements <= 0:
        raise ValueError("max_requirements must be positive")
    selected_chunks = chunks if chunks is not None else chunk_document(document)
    section_classes = {
        item.section_id: item
        for item in (classifications if classifications is not None else classify_document(document))
    }
    segment_by_id = {
        segment.id: segment
        for segment in document.segments
        if segment.id
    }
    row_semantics = build_table_row_semantics(
        [
            table.model_dump(mode="json", by_alias=True)
            for table in document.tables
        ]
    )
    evidences: list[EvidenceFact] = []
    requirements: list[RequirementFact] = []
    seen: set[str] = set()
    warnings: list[str] = []
    clause_category_counts: dict[str, int] = {}
    skipped_category_counts: dict[str, int] = {}
    skipped_clause_count = 0
    clause_review_count = 0
    clause_multi_type_count = 0
    heading_variants = _heading_variants(document)

    for chunk in selected_chunks:
        classification = section_classes.get(chunk.section_id)
        section_category = _category(classification)
        if section_category in {"other", "attachment"} and not any(
            term in chunk.content for term in (*REQUIREMENT_HINTS, *REJECTION_HINTS)
        ):
            continue
        for clause in _clauses(chunk.content):
            if len(clause) < 6 or not _has_requirement_signal(clause):
                continue
            if _is_non_requirement_clause(clause, heading_variants):
                continue
            if clause in seen:
                continue
            seen.add(clause)
            if len(requirements) >= max_requirements:
                warnings.append("requirement extraction reached max_requirements")
                break
            clause_classification = classify_clause(
                clause,
                section=next(
                    (
                        item
                        for item in document.sections
                        if (item.section_id or item.id) == chunk.section_id
                    ),
                    DocumentSection(
                        id=chunk.section_id,
                        sectionId=chunk.section_id,
                        heading=chunk.heading,
                        sectionPath=chunk.section_path,
                        sectionType=section_category,
                    ),
                ),
                section_classification=classification,
            )
            category = clause_classification.primary_type
            if category == "other" and classification is not None:
                category = classification.primary_type
            if not _should_keep_requirement(
                section_category=section_category,
                requirement_category=category,
            ):
                skipped_clause_count += 1
                skipped_category_counts[category] = skipped_category_counts.get(category, 0) + 1
                continue
            category_hits = list(clause_classification.type_hits or [category])
            clause_category_counts[category] = clause_category_counts.get(category, 0) + 1
            needs_review = _needs_requirement_review(
                section_category=section_category,
                requirement_category=category,
                section_review=(
                    classification.needs_human_review
                    if classification
                    else True
                ),
                clause_review=clause_classification.needs_human_review,
            )
            clause_review_count += int(needs_review)
            clause_multi_type_count += int(len(category_hits) > 1)
            evidence_id = _stable_id("EVD", document.document_version_id, chunk.chunk_id, clause)
            requirement_id = _stable_id("REQ", document.document_version_id, chunk.chunk_id, clause)
            source_segments = [
                segment_by_id[segment_id]
                for segment_id in chunk.segment_ids
                if segment_id in segment_by_id
            ]
            product_context = product_context_from_segments(
                [
                    segment.model_dump(mode="json", by_alias=True)
                    for segment in source_segments
                ],
                row_semantics,
            )
            evidence = evidence_from_segment(
                document,
                evidence_id=evidence_id,
                segment_ids=chunk.segment_ids,
                section_id=chunk.section_id,
                section_path=chunk.section_path,
                source_quote=clause,
                confidence=0.82,
            )
            evidence.source_locator = chunk.start_locator
            evidence.page_start = chunk.page_start
            evidence.page_end = chunk.page_end
            evidence.table_ids = chunk.table_ids
            evidences.append(evidence)
            is_rejection = (
                category == "rejection"
                or "rejection" in category_hits
                or any(term in clause for term in REJECTION_HINTS)
            )
            product_context = (
                product_context
                if category in {"technical", "commercial", "pricing", "scope"}
                else {}
            )
            mandatory = any(
                term in clause
                for term in ("必须", "须", "应当", "不得", "不低于", "不少于", "至少")
            )
            requirements.append(
                RequirementFact(
                    requirementId=requirement_id,
                    documentVersionId=document.document_version_id,
                    sectionId=chunk.section_id,
                    sectionPath=chunk.section_path,
                    category=category,
                    categoryHits=category_hits,
                    contentType=category,
                    directTypeHits=clause_classification.direct_type_hits,
                    inheritedType=clause_classification.inherited_type,
                    typeHits=category_hits,
                    primaryRoute=clause_classification.primary_route,
                    secondaryRoutes=clause_classification.secondary_routes,
                    matchDomain=clause_classification.match_domain,
                    decisionImpact=clause_classification.decision_impact,
                    granularity=clause_classification.granularity,
                    routeTags=clause_classification.usage_tags,
                    rawText=clause,
                    normalizedRequirement=clause,
                    mandatory=mandatory,
                    isRejection=is_rejection,
                    importance=_importance(category, clause),
                    sourceSegmentIds=chunk.segment_ids,
                    evidenceIds=[evidence_id],
                    sourceQuote=clause,
                    extractionMethod="rule",
                    confidence=clause_classification.confidence,
                    needsHumanReview=needs_review,
                    reviewRequired=clause_classification.review_required,
                    reviewMode=clause_classification.review_mode,
                    reviewReason=clause_classification.review_reason,
                    classificationSource=clause_classification.classification_source,
                    productName=product_context.get("productName", ""),
                    targetType=product_context.get("targetType", ""),
                    sourceColumn=product_context.get("sourceColumn", ""),
                )
            )
        if len(requirements) >= max_requirements:
            break

    project_fields: list[ExtractedFieldFact] = []
    for field_name, value, segment_id in _field_candidates(document):
        evidence_id = _stable_id("EVD", document.document_version_id, field_name, value, segment_id)
        evidence = evidence_from_segment(
            document,
            evidence_id=evidence_id,
            segment_ids=[segment_id],
            source_quote=value,
            confidence=0.9,
        )
        evidences.append(evidence)
        project_fields.append(
            ExtractedFieldFact(
                fieldName=field_name,
                value=value,
                evidenceId=evidence_id,
                confidence=0.9,
            )
        )

    return RequirementExtractionResult(
        documentVersionId=document.document_version_id,
        projectFields=project_fields,
        requirements=requirements,
        evidences=evidences,
        warnings=list(dict.fromkeys(warnings)),
        stats={
            "chunkCount": len(selected_chunks),
            "requirementCount": len(requirements),
            "evidenceCount": len(evidences),
            "technicalCount": sum(1 for item in requirements if item.category == "technical"),
            "qualificationCount": sum(1 for item in requirements if item.category == "qualification"),
            "commercialCount": sum(1 for item in requirements if item.category in {"commercial", "pricing"}),
            "clauseCategoryCounts": clause_category_counts,
            "clauseReviewCount": clause_review_count,
            "clauseMultiTypeCount": clause_multi_type_count,
            "skippedClauseCount": skipped_clause_count,
            "skippedCategoryCounts": skipped_category_counts,
            "servicePlanSkippedCount": skipped_category_counts.get("service_plan", 0),
            "productNameCount": sum(
                1 for item in requirements if item.product_name
            ),
            "technicalProductNameCount": sum(
                1
                for item in requirements
                if item.category == "technical" and item.product_name
            ),
            "technicalTableCount": sum(
                1
                for item in requirements
                if item.category == "technical"
                and any(
                    segment_by_id.get(segment_id)
                    and segment_by_id[segment_id].table_id
                    for segment_id in item.source_segment_ids
                )
            ),
            "technicalProductColumnCount": sum(
                1
                for item in requirements
                if item.category == "technical" and item.source_column
            ),
            "technicalTableProductNameCount": sum(
                1
                for item in requirements
                if item.category == "technical"
                and item.product_name
                and item.source_column
            ),
        },
    )
