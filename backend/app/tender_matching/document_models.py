"""Typed document representation shared by parsing and later workflow stages.

The existing parser functions intentionally keep returning dictionaries for
backward compatibility.  This module is the stable boundary for new callers:
third-party parser output is normalized once into ``DocumentIR`` and can then
be serialized, validated, and consumed by sectioning, extraction, matching,
and bid-writing views.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field


DOCUMENT_IR_SCHEMA_VERSION = "document-ir/v1"


class DocumentModel(BaseModel):
    """Base configuration for parser artifacts.

    Parser libraries and historical runtime artifacts have format-specific
    fields.  We therefore allow extra fields at this boundary while typing the
    fields that every downstream stage needs.
    """

    model_config = ConfigDict(
        extra="allow",
        populate_by_name=True,
        validate_assignment=True,
    )


class SourceLocator(DocumentModel):
    """A source anchor that can be resolved back to the parsed document."""

    source_locator: str = Field("", alias="sourceLocator")
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None
    page_width: float | None = Field(default=None, alias="pageWidth")
    page_height: float | None = Field(default=None, alias="pageHeight")
    section_id: str = Field("", alias="sectionId")
    section_path: list[str] = Field(default_factory=list, alias="sectionPath")
    table_id: str = Field("", alias="tableId")
    row_index: int | None = Field(default=None, alias="rowIndex")
    cell_index: int | None = Field(default=None, alias="cellIndex")
    sheet_index: int | None = Field(default=None, alias="sheetIndex")


class DocumentPage(DocumentModel):
    """A physical page when the source format exposes page boundaries."""

    page_number: int = Field(..., alias="page")
    text: str = ""
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    source_locator: str = Field("", alias="sourceLocator")
    has_text: bool = Field(False, alias="hasText")
    width: float | None = None
    height: float | None = None
    text_quality: float | None = Field(default=None, alias="textQuality")
    is_scan: bool = Field(False, alias="isScan")
    ocr_status: str = Field("not_run", alias="ocrStatus")


class DocumentTableCell(DocumentModel):
    id: str = ""
    source_locator: str = Field("", alias="sourceLocator")
    text: str = ""
    content_type: str = Field("table_cell", alias="contentType")
    order: int = 0
    table_id: str = Field("", alias="tableId")
    row_index: int | None = Field(default=None, alias="rowIndex")
    cell_index: int | None = Field(default=None, alias="cellIndex")
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None
    paragraphs: list[dict[str, Any]] = Field(default_factory=list)
    grid_span: str = Field("1", alias="gridSpan")
    v_merge: str = Field("", alias="vMerge")


class DocumentTableRow(DocumentModel):
    row_index: int = Field(..., alias="rowIndex")
    source_locator: str = Field("", alias="sourceLocator")
    text: str = ""
    cells: list[DocumentTableCell] = Field(default_factory=list)
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None


class DocumentTable(DocumentModel):
    id: str = ""
    table_id: str = Field("", alias="tableId")
    content_type: str = Field("table", alias="contentType")
    order: int = 0
    rows: list[DocumentTableRow] = Field(default_factory=list)
    row_count: int = Field(0, alias="rowCount", ge=0)
    column_count: int = Field(0, alias="columnCount", ge=0)
    grid_widths: list[str] = Field(default_factory=list, alias="gridWidths")
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None


class DocumentSegment(DocumentModel):
    """Smallest ordered content unit emitted by the parser."""

    id: str = ""
    source_locator: str = Field("", alias="sourceLocator")
    text: str = ""
    content_type: str = Field("paragraph", alias="contentType")
    order: int = 0
    page: int | None = Field(default=None, ge=1)
    bbox: list[float] | None = None
    paragraph_index: int | None = Field(default=None, alias="paragraphIndex")
    table_id: str = Field("", alias="tableId")
    row_index: int | None = Field(default=None, alias="rowIndex")
    cell_index: int | None = Field(default=None, alias="cellIndex")
    sheet_index: int | None = Field(default=None, alias="sheetIndex")
    cells: list[DocumentTableCell] = Field(default_factory=list)
    paragraphs: list[dict[str, Any]] = Field(default_factory=list)
    style: dict[str, Any] = Field(default_factory=dict)
    locator: SourceLocator = Field(default_factory=SourceLocator)


class DocumentSection(DocumentModel):
    """A section-tree node produced from ordered segments."""

    id: str = ""
    section_id: str = Field("", alias="sectionId")
    section_type: str = Field("other", alias="sectionType")
    section_type_hits: list[str] = Field(default_factory=list, alias="sectionTypeHits")
    is_mixed: bool = Field(False, alias="isMixed")
    heading: str = ""
    level: int = 0
    is_major: bool = Field(False, alias="isMajor")
    parent_id: str = Field("", alias="parentId")
    section_path: list[str] = Field(default_factory=list, alias="sectionPath")
    heading_segment_id: str = Field("", alias="headingSegmentId")
    start_locator: str = Field("", alias="startLocator")
    end_locator: str = Field("", alias="endLocator")
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    text: str = ""
    implicit: bool = False


class DocumentChunk(DocumentModel):
    """A task-sized, source-addressable view of a section span."""

    chunk_id: str = Field("", alias="chunkId")
    document_version_id: str = Field("", alias="documentVersionId")
    section_id: str = Field("", alias="sectionId")
    section_path: list[str] = Field(default_factory=list, alias="sectionPath")
    heading: str = ""
    context_prefix: str = Field("", alias="contextPrefix")
    content: str = ""
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")
    start_locator: str = Field("", alias="startLocator")
    end_locator: str = Field("", alias="endLocator")
    page_start: int | None = Field(default=None, alias="pageStart", ge=1)
    page_end: int | None = Field(default=None, alias="pageEnd", ge=1)
    token_count: int = Field(0, alias="tokenCount", ge=0)
    fingerprint: str = ""
    split_reason: Literal[
        "section_fit",
        "section_overflow",
        "segment_overflow",
        "table_atom",
        "prefix_content",
    ] = Field("section_fit", alias="splitReason")
    is_heading_included: bool = Field(False, alias="isHeadingIncluded")


class ParseDiagnostics(DocumentModel):
    """Machine-readable diagnostics for one parser execution."""

    status: Literal["parsed", "partial", "empty", "failed"] = "parsed"
    parse_method: str = Field("", alias="parseMethod")
    warning_count: int = Field(0, alias="warningCount", ge=0)
    error_count: int = Field(0, alias="errorCount", ge=0)
    page_count: int = Field(0, alias="pageCount", ge=0)
    segment_count: int = Field(0, alias="segmentCount", ge=0)
    unit_count: int = Field(0, alias="unitCount", ge=0)
    table_count: int = Field(0, alias="tableCount", ge=0)
    section_count: int = Field(0, alias="sectionCount", ge=0)
    heading_count: int = Field(0, alias="headingCount", ge=0)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class DocumentIR(DocumentModel):
    """Canonical parser output shared by all downstream views."""

    schema_version: str = Field(DOCUMENT_IR_SCHEMA_VERSION, alias="schemaVersion")
    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")
    file_name: str = Field("", alias="fileName")
    file_hash: str = Field("", alias="fileHash")
    original_format: str = Field("", alias="originalFormat")
    mime_type: str = Field("", alias="mimeType")
    text: str = ""
    pages: list[DocumentPage] = Field(default_factory=list)
    segments: list[DocumentSegment] = Field(default_factory=list)
    units: list[DocumentSegment] = Field(default_factory=list)
    tables: list[DocumentTable] = Field(default_factory=list)
    sections: list[DocumentSection] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    structure_warnings: list[str] = Field(default_factory=list, alias="structureWarnings")
    parse_method: str = Field("", alias="parseMethod")
    diagnostics: ParseDiagnostics = Field(default_factory=ParseDiagnostics)


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _clean_text(value: Any, limit: int = 12000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _format_from_name(file_name: str, parse_method: str) -> str:
    suffix = Path(file_name).suffix.lower().lstrip(".")
    if suffix:
        return suffix
    if "pdf" in parse_method:
        return "pdf"
    if "docx" in parse_method:
        return "docx"
    if "doc" in parse_method:
        return "doc"
    if "xlsx" in parse_method:
        return "xlsx"
    return "text"


def _identity(file_name: str, content: bytes | None) -> tuple[str, str]:
    digest = hashlib.sha256(content).hexdigest() if content is not None else ""
    if not digest:
        digest = hashlib.sha256(file_name.encode("utf-8", errors="ignore")).hexdigest()
    document_id = f"DOC-{digest[:12]}"
    return document_id, f"{document_id}-V001"


def _source_locator(raw: Mapping[str, Any]) -> SourceLocator:
    style = raw.get("style")
    style = style if isinstance(style, Mapping) else {}
    values = {
        "sourceLocator": str(raw.get("sourceLocator") or ""),
        "page": raw.get("page"),
        "bbox": raw.get("bbox"),
        "pageWidth": raw.get("pageWidth") or style.get("pageWidth"),
        "pageHeight": raw.get("pageHeight") or style.get("pageHeight"),
        "tableId": str(raw.get("tableId") or ""),
        "rowIndex": raw.get("rowIndex"),
        "cellIndex": raw.get("cellIndex"),
        "sheetIndex": raw.get("sheetIndex"),
    }
    return SourceLocator.model_validate(values)


def _segment(raw: Any) -> DocumentSegment:
    values = _as_dict(raw)
    values.setdefault("locator", _source_locator(values))
    return DocumentSegment.model_validate(values)


def _page_records(
    segments: list[DocumentSegment],
    metadata: Mapping[str, Any],
    raw_pages: Any,
) -> list[DocumentPage]:
    if isinstance(raw_pages, list) and raw_pages:
        return [DocumentPage.model_validate(item) for item in raw_pages if isinstance(item, Mapping)]

    page_count = 0
    try:
        page_count = max(0, int(metadata.get("pageCount") or 0))
    except (TypeError, ValueError):
        page_count = 0
    page_count = max(page_count, max((segment.page or 0 for segment in segments), default=0))
    pages: list[DocumentPage] = []
    for page_number in range(1, page_count + 1):
        page_segments = [segment for segment in segments if segment.page == page_number]
        pages.append(
            DocumentPage(
                page=page_number,
                text="\n".join(segment.text for segment in page_segments if segment.text),
                segmentIds=[segment.id for segment in page_segments if segment.id],
                sourceLocator=(page_segments[0].source_locator if page_segments else f"page {page_number}"),
                hasText=bool(page_segments),
            )
        )
    return pages


def _status(parse_method: str, text: str, segments: list[DocumentSegment]) -> str:
    if parse_method.endswith("_failed"):
        return "failed"
    if text or segments:
        return "parsed"
    return "empty"


# documentBlocks 只持久化 units（叶子单元）。segments 是 units 中“非表格单元格”的
# 子集，该包含关系由 document_parser 各分支的构造保证（docx 为
# units = segments ∪ 表格单元格单元，pdf/文本为 units = segments.copy()），
# 因此可以精确还原，不需要重复落盘。
_TABLE_CELL_CONTENT_MARKER = "cell"


def _is_table_cell_unit(unit: Mapping[str, Any]) -> bool:
    return _TABLE_CELL_CONTENT_MARKER in str(unit.get("contentType") or "").lower()


def derive_segments_from_units(units: Any) -> list[dict[str, Any]]:
    """从叶子单元还原逻辑段落/表格行（过滤掉表格单元格单元）。"""

    return [
        dict(item)
        for item in _as_list(units)
        if isinstance(item, Mapping) and not _is_table_cell_unit(item)
    ]


def document_segments(document_blocks: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """读取 documentBlocks 的 segments：旧数据直接读，新数据由 units 还原。"""

    blocks = _as_dict(document_blocks)
    persisted = [
        dict(item)
        for item in _as_list(blocks.get("segments"))
        if isinstance(item, Mapping)
    ]
    if persisted:
        return persisted
    return derive_segments_from_units(blocks.get("units"))


def strip_derivable_segments(document_blocks: Mapping[str, Any] | None) -> dict[str, Any]:
    """落盘前去掉可由 units 还原的 segments，避免段落/表格行重复存储。

    仅在 units 非空时剥离：units 为空说明该解析分支没有产出叶子单元，
    此时保留 segments 以免丢失正文。
    """

    blocks = _as_dict(document_blocks)
    if _as_list(blocks.get("units")):
        blocks.pop("segments", None)
    return blocks


def document_ir_from_parsed(
    parsed: Mapping[str, Any],
    *,
    file_name: str = "",
    content: bytes | None = None,
    mime_type: str = "",
    document_id: str = "",
    document_version_id: str = "",
) -> DocumentIR:
    """Convert one legacy parser dictionary into the typed document contract."""

    raw = _as_dict(parsed)
    parse_method = str(raw.get("parseMethod") or "")
    metadata = _as_dict(raw.get("metadata"))
    actual_name = str(file_name or raw.get("fileName") or metadata.get("fileName") or "")
    generated_id, generated_version = _identity(actual_name, content)
    resolved_document_id = str(document_id or raw.get("documentId") or generated_id)
    resolved_version_id = str(
        document_version_id
        or raw.get("documentVersionId")
        or f"{resolved_document_id}-V001"
        or generated_version
    )

    segments = [_segment(item) for item in document_segments(raw) if isinstance(item, Mapping)]
    units = [_segment(item) for item in _as_list(raw.get("units")) if isinstance(item, Mapping)]
    tables = [
        DocumentTable.model_validate(item)
        for item in _as_list(raw.get("tables"))
        if isinstance(item, Mapping)
    ]
    sections = [
        DocumentSection.model_validate(item)
        for item in _as_list(raw.get("sections"))
        if isinstance(item, Mapping)
    ]
    warnings = [str(item) for item in _as_list(raw.get("warnings")) if str(item).strip()]
    structure_warnings = [
        str(item) for item in _as_list(raw.get("structureWarnings")) if str(item).strip()
    ]
    all_warnings = list(dict.fromkeys([*warnings, *structure_warnings]))
    metadata.setdefault("documentId", resolved_document_id)
    metadata.setdefault("documentVersionId", resolved_version_id)
    metadata.setdefault("schemaVersion", DOCUMENT_IR_SCHEMA_VERSION)
    metadata.setdefault("sourceFileName", actual_name)

    page_count = max(
        int(metadata.get("pageCount") or 0) if str(metadata.get("pageCount") or "0").isdigit() else 0,
        len(_as_list(raw.get("pages"))),
    )
    diagnostics = ParseDiagnostics(
        status=_status(parse_method, str(raw.get("text") or ""), segments),
        parseMethod=parse_method,
        warningCount=len(all_warnings),
        pageCount=page_count,
        segmentCount=len(segments),
        unitCount=len(units),
        tableCount=len(tables),
        sectionCount=len(sections),
        headingCount=int(metadata.get("headingCount") or 0) if str(metadata.get("headingCount") or "0").isdigit() else 0,
        warnings=all_warnings,
    )

    return DocumentIR(
        schemaVersion=DOCUMENT_IR_SCHEMA_VERSION,
        documentId=resolved_document_id,
        documentVersionId=resolved_version_id,
        fileName=actual_name,
        fileHash=(hashlib.sha256(content).hexdigest() if content is not None else str(raw.get("fileHash") or "")),
        originalFormat=_format_from_name(actual_name, parse_method),
        mimeType=mime_type,
        text=str(raw.get("text") or ""),
        pages=_page_records(segments, metadata, raw.get("pages")),
        segments=segments,
        units=units,
        tables=tables,
        sections=sections,
        metadata=metadata,
        warnings=warnings,
        structureWarnings=structure_warnings,
        parseMethod=parse_method,
        diagnostics=diagnostics,
    )


def document_ir_to_json(document: DocumentIR) -> dict[str, Any]:
    """Serialize a validated document artifact using the public JSON names."""

    return document.model_dump(mode="json", by_alias=True, exclude_none=False)
