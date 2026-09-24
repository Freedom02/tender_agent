"""Fine-grained processing for technical and qualification map ranges."""
from __future__ import annotations

from collections import Counter
from typing import Mapping

from pydantic import BaseModel, ConfigDict, Field

from ...tender_matching.document_models import DocumentChunk, DocumentIR
from ...tender_matching.fact_models import RequirementExtractionResult
from ...tender_matching.requirement_extractor import extract_requirements
from ...tender_matching.section_classifier import SectionClassification, classify_document
from .schemas import DocumentMap, DocumentMapSection
from .structural_chunker import StructuralBlock, StructuralBlockSet, build_structural_blocks


FINE_TARGETS = {"technical", "qualification"}


class TargetRequirementProcessingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    document_version_id: str = Field(..., alias="documentVersionId")
    extraction: RequirementExtractionResult
    included_map_ids: list[str] = Field(default_factory=list, alias="includedMapIds")
    included_block_ids: list[str] = Field(default_factory=list, alias="includedBlockIds")
    excluded_map_ids: list[str] = Field(default_factory=list, alias="excludedMapIds")
    warnings: list[str] = Field(default_factory=list)
    stats: dict[str, int] = Field(default_factory=dict)


def _as_map(value: DocumentMap | Mapping[str, object]) -> DocumentMap:
    return value if isinstance(value, DocumentMap) else DocumentMap.model_validate(value)


def _chunk_from_block(document: DocumentIR, block: StructuralBlock, ordinal: int) -> DocumentChunk:
    segment_map = {segment.id: segment for segment in document.segments if segment.id}
    source_segments = [segment_map[item] for item in block.segment_ids if item in segment_map]
    pages = sorted({segment.page for segment in source_segments if segment.page is not None})
    return DocumentChunk(
        chunkId=f"{document.document_version_id}-TARGET-{ordinal:06d}",
        documentVersionId=document.document_version_id,
        sectionId=block.section_id,
        sectionPath=block.section_path,
        heading=block.title,
        contextPrefix=" / ".join(block.context_section_ids),
        content=block.content,
        segmentIds=block.segment_ids,
        tableIds=block.table_ids,
        startLocator=block.source_locator,
        endLocator=block.source_locator,
        pageStart=pages[0] if pages else None,
        pageEnd=pages[-1] if pages else None,
        tokenCount=block.token_count,
        fingerprint=block.block_id,
        splitReason=(
            "table_atom"
            if block.block_type == "table"
            else "segment_overflow"
            if "overflow" in block.split_reason
            else "section_fit"
        ),
        isHeadingIncluded=bool(block.title),
    )


def _target_classifications(
    document: DocumentIR,
    mapped_sections: list[DocumentMapSection],
) -> list[SectionClassification]:
    classifications = {item.section_id: item for item in classify_document(document)}
    result: dict[str, SectionClassification] = {}
    for item in mapped_sections:
        section_ids = [
            item.owner_section_id or (item.section_ids[0] if item.section_ids else ""),
            item.map_id,
        ]
        for section_id in [value for value in section_ids if value]:
            existing = classifications.get(section_id)
            if existing is not None and existing.primary_type == item.domain:
                result[section_id] = existing
                continue
            result[section_id] = SectionClassification(
                sectionId=section_id,
                originalSectionType=(existing.original_section_type if existing else item.domain),
                headingType=item.domain,
                directTypeHits=[item.domain],
                inheritedType=(existing.inherited_type if existing else "other"),
                containerType=(existing.container_type if existing else "other"),
                descendantTypeHits=(existing.descendant_type_hits if existing else []),
                primaryType=item.domain,
                typeHits=[item.domain],
                contentType=item.domain,
                confidence=max(item.confidence, 0.82),
                classificationSource="rule",
                usageTags=["matching", "display"],
                needsHumanReview=item.needs_human_review,
                primaryRoute=item.primary_route,
                secondaryRoutes=[],
                matchDomain=item.domain,
                decisionImpact="normal",
                granularity="clause",
                reviewRequired=item.needs_human_review,
                reviewMode="assisted" if item.needs_human_review else "none",
                reviewReason=item.reason,
                rationale=item.reason,
            )
    return list(result.values())


def extract_target_requirements(
    document: DocumentIR | Mapping[str, object],
    document_map: DocumentMap | Mapping[str, object],
    *,
    blocks: StructuralBlockSet | None = None,
    max_tokens: int = 800,
    max_requirements: int = 2000,
) -> TargetRequirementProcessingResult:
    parsed = document if isinstance(document, DocumentIR) else DocumentIR.model_validate(document)
    mapped = _as_map(document_map)
    block_set = blocks or build_structural_blocks(parsed, mapped, max_tokens=max_tokens)
    fine_map_sections = [
        item
        for item in mapped.sections
        if item.domain in FINE_TARGETS
        and item.processing_mode == "fine_extract"
        and item.role == "leaf"
    ]
    fine_map_ids = {item.map_id for item in fine_map_sections}
    section_to_map_ids: dict[str, set[str]] = {}
    for item in fine_map_sections:
        for section_id in item.section_ids:
            section_to_map_ids.setdefault(section_id, set()).add(item.map_id)
    selected_blocks = [
        item
        for item in block_set.blocks
        if item.domain in FINE_TARGETS
        and item.block_type in {"fine", "table"}
        and item.section_id in section_to_map_ids
        and item.content.strip()
    ]
    selected_blocks.sort(
        key=lambda item: min(
            (
                index
                for segment_id in item.segment_ids
                for index, segment in enumerate(parsed.segments)
                if segment.id == segment_id
            ),
            default=10**9,
        )
    )
    chunks = [
        _chunk_from_block(parsed, block, ordinal)
        for ordinal, block in enumerate(selected_blocks, start=1)
    ]
    extraction = extract_requirements(
        parsed,
        chunks=chunks,
        classifications=_target_classifications(parsed, fine_map_sections),
        max_requirements=max_requirements,
    )
    kept = [item for item in extraction.requirements if item.category in FINE_TARGETS]
    kept_evidence_ids = {
        evidence_id
        for item in kept
        for evidence_id in item.evidence_ids
    }
    extraction = extraction.model_copy(
        update={
            "requirements": kept,
            "evidences": [
                item for item in extraction.evidences
                if item.evidence_id in kept_evidence_ids
            ],
            "stats": {
                **extraction.stats,
                "requirementCount": len(kept),
                "evidenceCount": len(kept_evidence_ids),
                "technicalCount": sum(item.category == "technical" for item in kept),
                "qualificationCount": sum(item.category == "qualification" for item in kept),
                "excludedRejectionCount": extraction.stats.get("excludedRejectionCount", 0),
            },
        }
    )
    excluded_map_ids = [item.map_id for item in mapped.sections if item.map_id not in fine_map_ids]
    category_counts = Counter(item.category for item in extraction.requirements)
    warnings = list(
        dict.fromkeys(
            [
                *block_set.warnings,
                *extraction.warnings,
                *([] if selected_blocks else ["no_target_blocks"]),
            ]
        )
    )
    return TargetRequirementProcessingResult(
        documentVersionId=parsed.document_version_id,
        extraction=extraction,
        includedMapIds=[item.map_id for item in fine_map_sections],
        includedBlockIds=[item.block_id for item in selected_blocks],
        excludedMapIds=excluded_map_ids,
        warnings=warnings,
        stats={
            "includedMapCount": len(fine_map_sections),
            "includedBlockCount": len(selected_blocks),
            "fineBlockCount": sum(item.block_type == "fine" for item in selected_blocks),
            "tableBlockCount": sum(item.block_type == "table" for item in selected_blocks),
            "requirementCount": len(kept),
            "technicalCount": category_counts.get("technical", 0),
            "qualificationCount": category_counts.get("qualification", 0),
            "nonTargetRequirementCount": len(extraction.requirements) - len(kept),
            "excludedRejectionCount": extraction.stats.get("excludedRejectionCount", 0),
        },
    )


__all__ = [
    "FINE_TARGETS",
    "TargetRequirementProcessingResult",
    "extract_target_requirements",
]
