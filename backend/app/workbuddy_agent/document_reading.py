"""Controlled, source-addressable reads over the canonical ``DocumentIR``.

This module is deliberately narrower than the parser.  It exposes document
content to an agent without exposing filesystem paths or parser internals.
Every returned record carries the document identity and enough source
coordinates to resolve it back to the parsed artifact.
"""
from __future__ import annotations

import base64
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..tender_matching.document_models import (
    DocumentIR,
    DocumentSection,
    DocumentSegment,
    DocumentTable,
    DocumentTableRow,
)
from .document_map.schemas import TemplateLocator


DOCUMENT_READ_SCHEMA_VERSION = "tender-document-read/v1"
DEFAULT_PAGE_CHARS = 100000
DEFAULT_MAX_ITEMS = 1000
MAX_PAGE_CHARS = 200000
MAX_ITEMS = 1000


class DocumentReadContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DocumentReadError(ValueError):
    """A safe, structured error that can be returned by a future MCP layer."""

    def __init__(self, code: str, message: str, *, details: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = dict(details or {})

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class SourceAnchor(DocumentReadContract):
    """Format-neutral coordinates for one parsed source record."""

    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")
    source_file_id: str = Field("", alias="sourceFileId")
    source_file_hash: str = Field("", alias="sourceFileHash")
    source_format: str = Field("", alias="sourceFormat")
    content_type: str = Field("", alias="contentType")
    section_id: str = Field("", alias="sectionId")
    section_path: list[str] = Field(default_factory=list, alias="sectionPath")
    segment_id: str = Field("", alias="segmentId")
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    table_id: str = Field("", alias="tableId")
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")
    page_start: int | None = Field(default=None, alias="pageStart")
    page_end: int | None = Field(default=None, alias="pageEnd")
    paragraph_start: int | None = Field(default=None, alias="paragraphStart")
    paragraph_end: int | None = Field(default=None, alias="paragraphEnd")
    line_start: int | None = Field(default=None, alias="lineStart")
    line_end: int | None = Field(default=None, alias="lineEnd")
    row_start: int | None = Field(default=None, alias="rowStart")
    row_end: int | None = Field(default=None, alias="rowEnd")
    cell_index: int | None = Field(default=None, alias="cellIndex")
    sheet_index: int | None = Field(default=None, alias="sheetIndex")
    bbox: list[float] | None = None
    source_locator: str = Field("", alias="sourceLocator")
    part: str = ""


class DocumentReadRecord(DocumentReadContract):
    record_id: str = Field(..., alias="recordId", min_length=1)
    record_type: Literal["section", "segment", "table", "table_row", "table_cell"] = Field(
        ...,
        alias="recordType",
    )
    text: str = ""
    heading: str = ""
    source: SourceAnchor = Field(default_factory=SourceAnchor)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentIdentity(DocumentReadContract):
    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")
    file_name: str = Field("", alias="fileName")
    file_hash: str = Field("", alias="fileHash")
    source_file_id: str = Field("", alias="sourceFileId")
    original_format: str = Field("", alias="originalFormat")
    mime_type: str = Field("", alias="mimeType")


class DocumentReadResponse(DocumentReadContract):
    schema_version: str = Field(DOCUMENT_READ_SCHEMA_VERSION, alias="schemaVersion")
    operation: str
    document: DocumentIdentity
    records: list[DocumentReadRecord] = Field(default_factory=list)
    next_cursor: str = Field("", alias="nextCursor")
    has_more: bool = Field(False, alias="hasMore")
    truncated: bool = False
    char_count: int = Field(0, alias="charCount", ge=0)
    record_count: int = Field(0, alias="recordCount", ge=0)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentReadService:
    """Read-only view over one already parsed document.

    The service is bound to one ``DocumentIR`` at construction time.  Public
    methods may still receive document IDs so an adapter can enforce that an
    agent is reading the document attached to its current run.
    """

    def __init__(self, document: DocumentIR | Mapping[str, Any]):
        self.document = (
            document
            if isinstance(document, DocumentIR)
            else DocumentIR.model_validate(document)
        )
        self._segments = sorted(
            list(self.document.segments or self.document.units),
            key=lambda item: (item.order, item.id),
        )
        self._segment_by_id = {
            item.id: item for item in self._segments if item.id
        }
        self._sections = list(self.document.sections)
        self._section_by_id: dict[str, DocumentSection] = {}
        for section in self._sections:
            for key in (section.id, section.section_id):
                if key:
                    self._section_by_id[key] = section
        self._table_by_id: dict[str, DocumentTable] = {}
        for table in self.document.tables:
            for key in (table.id, table.table_id):
                if key:
                    self._table_by_id[key] = table
        self._owners = self._build_segment_owners()

    def _build_segment_owners(self) -> dict[str, DocumentSection]:
        candidates: dict[str, list[DocumentSection]] = {}
        for section in self._sections:
            for segment_id in section.segment_ids:
                if segment_id:
                    candidates.setdefault(segment_id, []).append(section)
        owners: dict[str, DocumentSection] = {}
        for segment_id, values in candidates.items():
            owners[segment_id] = min(
                values,
                key=lambda item: (len(item.segment_ids), -item.level),
            )
        return owners

    def _identity(self) -> DocumentIdentity:
        file_id = self.document.file_hash or self.document.file_name
        return DocumentIdentity(
            documentId=self.document.document_id,
            documentVersionId=self.document.document_version_id,
            fileName=self.document.file_name,
            fileHash=self.document.file_hash,
            sourceFileId=file_id,
            originalFormat=self.document.original_format,
            mimeType=self.document.mime_type,
        )

    def _assert_identity(
        self,
        document_id: str = "",
        document_version_id: str = "",
    ) -> None:
        if document_id and document_id != self.document.document_id:
            raise DocumentReadError(
                "document_id_mismatch",
                "The requested documentId is not bound to this read service.",
                details={"expected": self.document.document_id, "actual": document_id},
            )
        if (
            document_version_id
            and document_version_id != self.document.document_version_id
        ):
            raise DocumentReadError(
                "document_version_mismatch",
                "The requested documentVersionId is not bound to this read service.",
                details={
                    "expected": self.document.document_version_id,
                    "actual": document_version_id,
                },
            )

    @staticmethod
    def _ids(values: Iterable[Any] | Any) -> list[str]:
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, Iterable):
            return []
        return list(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))

    @staticmethod
    def _limit(value: int | None, default: int, maximum: int) -> int:
        resolved = default if value is None else int(value)
        if resolved <= 0:
            raise DocumentReadError("invalid_limit", "Read limits must be positive.")
        return min(resolved, maximum)

    def _source_for_segment(
        self,
        segment: DocumentSegment,
        *,
        segment_ids: Iterable[str] = (),
        table_ids: Iterable[str] = (),
        row_start: int | None = None,
        row_end: int | None = None,
        cell_index: int | None = None,
    ) -> SourceAnchor:
        owner = self._owners.get(segment.id)
        section_id = (
            segment.locator.section_id
            or (owner.section_id if owner else "")
            or (owner.id if owner else "")
        )
        section_path = list(
            segment.locator.section_path
            or (owner.section_path if owner else [])
        )
        page = segment.page or segment.locator.page
        source_table_id = segment.table_id or segment.locator.table_id
        resolved_segment_ids = self._ids(segment_ids) or ([segment.id] if segment.id else [])
        resolved_table_ids = self._ids(table_ids) or (
            [source_table_id] if source_table_id else []
        )
        paragraph = segment.paragraph_index
        row = segment.row_index
        cell = segment.cell_index if segment.cell_index is not None else cell_index
        return SourceAnchor(
            documentId=self.document.document_id,
            documentVersionId=self.document.document_version_id,
            sourceFileId=self.document.file_hash or self.document.file_name,
            sourceFileHash=self.document.file_hash,
            sourceFormat=self.document.original_format,
            contentType=segment.content_type,
            sectionId=section_id,
            sectionPath=section_path,
            segmentId=segment.id,
            segmentIds=resolved_segment_ids,
            tableId=source_table_id,
            tableIds=resolved_table_ids,
            pageStart=page,
            pageEnd=page,
            paragraphStart=paragraph,
            paragraphEnd=paragraph,
            rowStart=row_start if row_start is not None else row,
            rowEnd=row_end if row_end is not None else row,
            cellIndex=cell,
            sheetIndex=segment.sheet_index or segment.locator.sheet_index,
            bbox=segment.bbox or segment.locator.bbox,
            sourceLocator=segment.source_locator or segment.locator.source_locator,
            part=segment.content_type,
        )

    def _source_for_section(self, section: DocumentSection) -> SourceAnchor:
        segment_ids = self._ids(section.segment_ids)
        segments = [self._segment_by_id[item] for item in segment_ids if item in self._segment_by_id]
        pages = [item.page for item in segments if item.page is not None]
        tables = self._ids(
            segment.table_id
            for segment in segments
            if segment.table_id
        )
        return SourceAnchor(
            documentId=self.document.document_id,
            documentVersionId=self.document.document_version_id,
            sourceFileId=self.document.file_hash or self.document.file_name,
            sourceFileHash=self.document.file_hash,
            sourceFormat=self.document.original_format,
            contentType="section",
            sectionId=section.section_id or section.id,
            sectionPath=list(section.section_path),
            segmentId=segment_ids[0] if len(segment_ids) == 1 else "",
            segmentIds=segment_ids,
            tableId=tables[0] if len(tables) == 1 else "",
            tableIds=tables,
            pageStart=min(pages) if pages else None,
            pageEnd=max(pages) if pages else None,
            sourceLocator=section.start_locator or section.end_locator,
            part=section.heading,
        )

    def _segment_record(self, segment: DocumentSegment) -> DocumentReadRecord:
        owner = self._owners.get(segment.id)
        return DocumentReadRecord(
            recordId=segment.id,
            recordType="segment",
            text=segment.text,
            heading=owner.heading if owner else "",
            source=self._source_for_segment(segment),
            metadata={
                "order": segment.order,
                "contentType": segment.content_type,
                "paragraphs": list(segment.paragraphs),
                "cells": [cell.model_dump(mode="json", by_alias=True) for cell in segment.cells],
            },
        )

    def _section_record(self, section: DocumentSection) -> DocumentReadRecord:
        return DocumentReadRecord(
            recordId=section.section_id or section.id,
            recordType="section",
            text=section.heading,
            heading=section.heading,
            source=self._source_for_section(section),
            metadata={
                "level": section.level,
                "parentId": section.parent_id,
                "sectionType": section.section_type,
                "isMajor": section.is_major,
                "isMixed": section.is_mixed,
                "segmentCount": len(section.segment_ids),
                "startLocator": section.start_locator,
                "endLocator": section.end_locator,
            },
        )

    def _table_record(self, table: DocumentTable) -> DocumentReadRecord:
        table_id = table.table_id or table.id
        text = "\n".join(row.text for row in table.rows if row.text)
        return DocumentReadRecord(
            recordId=table_id,
            recordType="table",
            text=text,
            source=SourceAnchor(
                documentId=self.document.document_id,
                documentVersionId=self.document.document_version_id,
                sourceFileId=self.document.file_hash or self.document.file_name,
                sourceFileHash=self.document.file_hash,
                sourceFormat=self.document.original_format,
                contentType="table",
                tableId=table_id,
                tableIds=[table_id] if table_id else [],
                pageStart=table.page,
                pageEnd=table.page,
                bbox=table.bbox,
                part="table",
            ),
            metadata={
                "rowCount": table.row_count or len(table.rows),
                "columnCount": table.column_count,
                "gridWidths": list(table.grid_widths),
            },
        )

    def _table_row_record(
        self,
        table: DocumentTable,
        row: DocumentTableRow,
    ) -> DocumentReadRecord:
        table_id = table.table_id or table.id
        cells = [cell.model_dump(mode="json", by_alias=True) for cell in row.cells]
        text = row.text or " | ".join(
            str(cell.get("text") or "") for cell in cells if str(cell.get("text") or "")
        )
        return DocumentReadRecord(
            recordId=f"{table_id}:row:{row.row_index}",
            recordType="table_row",
            text=text,
            source=SourceAnchor(
                documentId=self.document.document_id,
                documentVersionId=self.document.document_version_id,
                sourceFileId=self.document.file_hash or self.document.file_name,
                sourceFileHash=self.document.file_hash,
                sourceFormat=self.document.original_format,
                contentType="table_row",
                tableId=table_id,
                tableIds=[table_id] if table_id else [],
                pageStart=row.page,
                pageEnd=row.page,
                rowStart=row.row_index,
                rowEnd=row.row_index,
                bbox=row.bbox,
                sourceLocator=row.source_locator,
                part="table_row",
            ),
            metadata={
                "rowIndex": row.row_index,
                "cells": cells,
            },
        )

    def _synthetic_text_record(self) -> list[DocumentReadRecord]:
        if not self.document.text:
            return []
        return [
            DocumentReadRecord(
                recordId=f"{self.document.document_id}:text:1",
                recordType="segment",
                text=self.document.text,
                source=SourceAnchor(
                    documentId=self.document.document_id,
                    documentVersionId=self.document.document_version_id,
                    sourceFileId=self.document.file_hash or self.document.file_name,
                    sourceFileHash=self.document.file_hash,
                    sourceFormat=self.document.original_format,
                    contentType="document_text",
                    sourceLocator="document.text",
                    part="document_text",
                ),
                metadata={"synthetic": True},
            )
        ]

    def _encode_cursor(self, operation: str, record_index: int, text_offset: int) -> str:
        payload = {
            "operation": operation,
            "documentVersionId": self.document.document_version_id,
            "recordIndex": record_index,
            "textOffset": text_offset,
        }
        raw = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def _decode_cursor(
        self,
        cursor: str,
        operation: str,
    ) -> tuple[int, int]:
        if not cursor:
            return 0, 0
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded).decode())
            if payload.get("operation") != operation:
                raise ValueError("operation")
            if payload.get("documentVersionId") != self.document.document_version_id:
                raise ValueError("documentVersionId")
            record_index = int(payload.get("recordIndex"))
            text_offset = int(payload.get("textOffset") or 0)
            if record_index < 0 or text_offset < 0:
                raise ValueError("negative cursor")
            return record_index, text_offset
        except (ValueError, TypeError, KeyError, json.JSONDecodeError, UnicodeError):
            raise DocumentReadError(
                "invalid_cursor",
                "The cursor is invalid or belongs to another document read.",
            ) from None

    def _paginate(
        self,
        records: list[DocumentReadRecord],
        *,
        operation: str,
        max_chars: int | None,
        max_items: int | None,
        cursor: str,
    ) -> tuple[list[DocumentReadRecord], str, bool, bool]:
        char_limit = self._limit(max_chars, DEFAULT_PAGE_CHARS, MAX_PAGE_CHARS)
        item_limit = self._limit(max_items, DEFAULT_MAX_ITEMS, MAX_ITEMS)
        index, text_offset = self._decode_cursor(cursor, operation)
        if index > len(records):
            raise DocumentReadError("invalid_cursor", "The cursor is past the end of the read.")

        output: list[DocumentReadRecord] = []
        used_chars = 0
        truncated = False
        while index < len(records) and len(output) < item_limit:
            record = records[index]
            remaining = record.text[text_offset:] if text_offset else record.text
            available = char_limit - used_chars
            if not remaining:
                index += 1
                text_offset = 0
                continue
            if available <= 0:
                break
            if len(remaining) > available:
                piece = remaining[:available]
                output.append(
                    record.model_copy(
                        update={
                            "text": piece,
                            "metadata": {
                                **record.metadata,
                                "textOffset": text_offset,
                                "textLength": len(piece),
                                "textPart": True,
                            },
                        }
                    )
                )
                used_chars += len(piece)
                text_offset += len(piece)
                truncated = True
                break
            output.append(
                record.model_copy(
                    update={
                        "text": remaining,
                        "metadata": (
                            {
                                **record.metadata,
                                "textOffset": text_offset,
                            }
                            if text_offset
                            else record.metadata
                        ),
                    }
                )
            )
            used_chars += len(remaining)
            index += 1
            text_offset = 0

        has_more = index < len(records) or text_offset > 0
        next_cursor = (
            self._encode_cursor(operation, index, text_offset)
            if has_more
            else ""
        )
        return output, next_cursor, has_more, truncated

    def _response(
        self,
        operation: str,
        records: list[DocumentReadRecord],
        *,
        next_cursor: str = "",
        has_more: bool = False,
        truncated: bool = False,
        warnings: Iterable[str] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        result = DocumentReadResponse(
            operation=operation,
            document=self._identity(),
            records=records,
            nextCursor=next_cursor,
            hasMore=has_more,
            truncated=truncated,
            charCount=sum(len(item.text) for item in records),
            recordCount=len(records),
            warnings=list(dict.fromkeys(str(item) for item in warnings if str(item).strip())),
            metadata=dict(metadata or {}),
        )
        return result.model_dump(mode="json", by_alias=True)

    def inspect_document(
        self,
        *,
        document_id: str = "",
        document_version_id: str = "",
    ) -> dict[str, Any]:
        self._assert_identity(document_id, document_version_id)
        headings = [
            {
                "sectionId": section.section_id or section.id,
                "heading": section.heading,
                "level": section.level,
                "path": list(section.section_path),
                "segmentCount": len(section.segment_ids),
            }
            for section in self._sections
        ]
        metadata_parts = self.document.metadata.get("parts", [])
        parts = (
            [dict(item) for item in metadata_parts if isinstance(item, Mapping)]
            if isinstance(metadata_parts, list)
            else []
        )
        return {
            "schemaVersion": DOCUMENT_READ_SCHEMA_VERSION,
            "operation": "inspect_document",
            "document": self._identity().model_dump(mode="json", by_alias=True),
            "characterCount": len(self.document.text or ""),
            "segmentCount": len(self._segments),
            "sectionCount": len(self._sections),
            "tableCount": len(self._table_by_id),
            "pageCount": len(self.document.pages),
            "hasTemplateSection": any(
                any(term in section.heading for term in ("模板", "格式", "响应表", "投标函"))
                for section in self._sections
            ),
            "hasEvaluationSection": any(
                any(term in section.heading for term in ("评分", "评标"))
                for section in self._sections
            ),
            "parserWarnings": list(
                dict.fromkeys([*self.document.warnings, *self.document.structure_warnings])
            ),
            "diagnostics": self.document.diagnostics.model_dump(mode="json", by_alias=True),
            "sections": headings,
            "parts": parts,
        }

    def list_section_candidates(
        self,
        *,
        document_id: str = "",
        document_version_id: str = "",
        max_chars: int | None = None,
        max_items: int | None = None,
        cursor: str = "",
    ) -> dict[str, Any]:
        self._assert_identity(document_id, document_version_id)
        records = [self._section_record(section) for section in self._sections]
        page, next_cursor, has_more, truncated = self._paginate(
            records,
            operation="list_section_candidates",
            max_chars=max_chars,
            max_items=max_items,
            cursor=cursor,
        )
        return self._response(
            "list_section_candidates",
            page,
            next_cursor=next_cursor,
            has_more=has_more,
            truncated=truncated,
            metadata={"totalSectionCount": len(records)},
        )

    def read_document_full(
        self,
        *,
        document_id: str = "",
        document_version_id: str = "",
        max_chars: int | None = None,
        max_items: int | None = None,
        cursor: str = "",
    ) -> dict[str, Any]:
        self._assert_identity(document_id, document_version_id)
        records = [self._segment_record(segment) for segment in self._segments]
        if not records:
            records = self._synthetic_text_record()
        page, next_cursor, has_more, truncated = self._paginate(
            records,
            operation="read_document_full",
            max_chars=max_chars,
            max_items=max_items,
            cursor=cursor,
        )
        warnings = ["read_document_full is paginated"] if has_more else []
        return self._response(
            "read_document_full",
            page,
            next_cursor=next_cursor,
            has_more=has_more,
            truncated=truncated,
            warnings=warnings,
            metadata={"totalSegmentCount": len(records)},
        )

    def _selected_segments(
        self,
        *,
        section_id: str = "",
        section_ids: Iterable[str] = (),
        segment_ids: Iterable[str] = (),
        start_segment_id: str = "",
        end_segment_id: str = "",
        table_ids: Iterable[str] = (),
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> list[DocumentSegment]:
        requested_sections = self._ids(section_ids)
        if section_id:
            requested_sections.append(section_id)
        requested_sections = self._ids(requested_sections)
        requested_segments = self._ids(segment_ids)
        requested_tables = set(self._ids(table_ids))
        if requested_sections:
            unknown_sections = [
                value for value in requested_sections if value not in self._section_by_id
            ]
            if unknown_sections:
                raise DocumentReadError(
                    "section_not_found",
                    "One or more section IDs were not found.",
                    details={"sectionIds": unknown_sections},
                )
            for section_key in requested_sections:
                section = self._section_by_id[section_key]
                requested_segments.extend(section.segment_ids)

        if start_segment_id or end_segment_id:
            if start_segment_id not in self._segment_by_id or end_segment_id not in self._segment_by_id:
                raise DocumentReadError(
                    "segment_not_found",
                    "The requested range boundary was not found.",
                    details={
                        "startSegmentId": start_segment_id,
                        "endSegmentId": end_segment_id,
                    },
                )
            start_index = next(
                index for index, item in enumerate(self._segments)
                if item.id == start_segment_id
            )
            end_index = next(
                index for index, item in enumerate(self._segments)
                if item.id == end_segment_id
            )
            if start_index > end_index:
                raise DocumentReadError(
                    "invalid_range",
                    "The range start must not follow the range end.",
                )
            requested_segments.extend(
                item.id for item in self._segments[start_index:end_index + 1]
            )

        if not requested_segments and not requested_tables and page_start is None and page_end is None:
            raise DocumentReadError(
                "empty_range",
                "At least one section, segment, table, page, or range boundary is required.",
            )

        selected_ids = set(self._ids(requested_segments))
        unknown_segments = sorted(selected_ids - set(self._segment_by_id))
        if unknown_segments:
            raise DocumentReadError(
                "segment_not_found",
                "One or more segment IDs were not found.",
                details={"segmentIds": unknown_segments},
            )
        result: list[DocumentSegment] = []
        for segment in self._segments:
            selected = (
                segment.id in selected_ids
                if selected_ids
                else False
            )
            if requested_tables and segment.table_id in requested_tables:
                selected = True
            if page_start is not None and (segment.page or 0) < page_start:
                selected = False
            if page_end is not None and (segment.page or 0) > page_end:
                selected = False
            if selected:
                result.append(segment)
        if requested_tables:
            unknown_tables = sorted(
                requested_tables - set(self._table_by_id) - {
                    segment.table_id for segment in self._segments if segment.table_id
                }
            )
            if unknown_tables:
                raise DocumentReadError(
                    "table_not_found",
                    "One or more table IDs were not found.",
                    details={"tableIds": unknown_tables},
                )
        return result

    def read_document_range(
        self,
        *,
        document_id: str = "",
        document_version_id: str = "",
        section_id: str = "",
        section_ids: Iterable[str] = (),
        segment_ids: Iterable[str] = (),
        start_segment_id: str = "",
        end_segment_id: str = "",
        table_ids: Iterable[str] = (),
        page_start: int | None = None,
        page_end: int | None = None,
        max_chars: int | None = None,
        max_items: int | None = None,
        cursor: str = "",
    ) -> dict[str, Any]:
        self._assert_identity(document_id, document_version_id)
        selected = self._selected_segments(
            section_id=section_id,
            section_ids=section_ids,
            segment_ids=segment_ids,
            start_segment_id=start_segment_id,
            end_segment_id=end_segment_id,
            table_ids=table_ids,
            page_start=page_start,
            page_end=page_end,
        )
        records = [self._segment_record(segment) for segment in selected]
        page, next_cursor, has_more, truncated = self._paginate(
            records,
            operation="read_document_range",
            max_chars=max_chars,
            max_items=max_items,
            cursor=cursor,
        )
        return self._response(
            "read_document_range",
            page,
            next_cursor=next_cursor,
            has_more=has_more,
            truncated=truncated,
            metadata={
                "selectedSegmentCount": len(records),
                "sectionId": section_id,
                "startSegmentId": start_segment_id,
                "endSegmentId": end_segment_id,
            },
        )

    def read_section(
        self,
        section_id: str,
        *,
        document_id: str = "",
        document_version_id: str = "",
        max_chars: int | None = None,
        max_items: int | None = None,
        cursor: str = "",
    ) -> dict[str, Any]:
        self._assert_identity(document_id, document_version_id)
        section = self._section_by_id.get(section_id)
        if section is None:
            raise DocumentReadError(
                "section_not_found",
                "The requested section was not found.",
                details={"sectionId": section_id},
            )
        records = [self._section_record(section)]
        records.extend(
            self._segment_record(self._segment_by_id[item])
            for item in section.segment_ids
            if item in self._segment_by_id
        )
        page, next_cursor, has_more, truncated = self._paginate(
            records,
            operation="read_section",
            max_chars=max_chars,
            max_items=max_items,
            cursor=cursor,
        )
        return self._response(
            "read_section",
            page,
            next_cursor=next_cursor,
            has_more=has_more,
            truncated=truncated,
            metadata={"sectionId": section_id},
        )

    def search_document(
        self,
        query: str,
        *,
        document_id: str = "",
        document_version_id: str = "",
        case_sensitive: bool = False,
        use_regex: bool = False,
        max_results: int = 50,
        context_before: int = 1,
        context_after: int = 1,
    ) -> dict[str, Any]:
        self._assert_identity(document_id, document_version_id)
        query = str(query or "").strip()
        if not query:
            raise DocumentReadError("empty_query", "Search query must not be empty.")
        max_results = self._limit(max_results, 50, MAX_ITEMS)
        if context_before < 0 or context_after < 0:
            raise DocumentReadError(
                "invalid_context",
                "Search context lengths must not be negative.",
            )
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            matcher = re.compile(query if use_regex else re.escape(query), flags)
        except re.error as exc:
            raise DocumentReadError(
                "invalid_query",
                "The search pattern is not valid.",
                details={"error": str(exc)},
            ) from None
        matches: list[dict[str, Any]] = []
        for index, segment in enumerate(self._segments):
            match = matcher.search(segment.text or "")
            if match is None:
                continue
            start = max(0, index - context_before)
            end = min(len(self._segments), index + context_after + 1)
            context_segments = self._segments[start:end]
            matches.append(
                {
                    "matchId": f"MATCH-{segment.id}-{len(matches) + 1}",
                    "matchedText": match.group(0),
                    "text": segment.text,
                    "contextText": "\n".join(item.text for item in context_segments if item.text),
                    "contextSegmentIds": [item.id for item in context_segments if item.id],
                    "source": self._source_for_segment(segment).model_dump(
                        mode="json",
                        by_alias=True,
                    ),
                }
            )
            if len(matches) >= max_results:
                break
        return {
            "schemaVersion": DOCUMENT_READ_SCHEMA_VERSION,
            "operation": "search_document",
            "document": self._identity().model_dump(mode="json", by_alias=True),
            "query": query,
            "matches": matches,
            "matchCount": len(matches),
            "truncated": len(matches) >= max_results,
        }

    def read_table_range(
        self,
        table_id: str,
        *,
        document_id: str = "",
        document_version_id: str = "",
        row_start: int | None = None,
        row_end: int | None = None,
        include_table_record: bool = True,
        max_chars: int | None = None,
        max_items: int | None = None,
        cursor: str = "",
    ) -> dict[str, Any]:
        self._assert_identity(document_id, document_version_id)
        table = self._table_by_id.get(table_id)
        if table is None:
            rows = [
                segment
                for segment in self._segments
                if segment.table_id == table_id
            ]
            if not rows:
                raise DocumentReadError(
                    "table_not_found",
                    "The requested table was not found.",
                    details={"tableId": table_id},
                )
            table = DocumentTable(
                id=table_id,
                tableId=table_id,
                rows=[
                    DocumentTableRow(
                        rowIndex=segment.row_index if segment.row_index is not None else index,
                        sourceLocator=segment.source_locator,
                        text=segment.text,
                        cells=segment.cells,
                        page=segment.page,
                        bbox=segment.bbox,
                    )
                    for index, segment in enumerate(rows)
                ],
            )
        rows = sorted(table.rows, key=lambda item: item.row_index)
        if row_start is not None:
            rows = [row for row in rows if row.row_index >= row_start]
        if row_end is not None:
            rows = [row for row in rows if row.row_index <= row_end]
        records = ([self._table_record(table)] if include_table_record else [])
        records.extend(self._table_row_record(table, row) for row in rows)
        page, next_cursor, has_more, truncated = self._paginate(
            records,
            operation="read_table_range",
            max_chars=max_chars,
            max_items=max_items,
            cursor=cursor,
        )
        return self._response(
            "read_table_range",
            page,
            next_cursor=next_cursor,
            has_more=has_more,
            truncated=truncated,
            metadata={
                "tableId": table_id,
                "rowStart": row_start,
                "rowEnd": row_end,
                "totalRowCount": len(rows),
            },
        )

    def read_template_range(
        self,
        template_locator: TemplateLocator | Mapping[str, Any],
        *,
        document_id: str = "",
        document_version_id: str = "",
        fidelity: str = "",
        max_chars: int | None = None,
        max_items: int | None = None,
        cursor: str = "",
    ) -> dict[str, Any]:
        self._assert_identity(document_id, document_version_id)
        locator = (
            template_locator
            if isinstance(template_locator, TemplateLocator)
            else TemplateLocator.model_validate(template_locator)
        )
        selected = self._selected_segments(
            section_ids=locator.section_ids,
            segment_ids=locator.segment_ids,
            table_ids=locator.table_ids,
        )
        if not selected and locator.source_locator:
            selected = [
                segment
                for segment in self._segments
                if locator.source_locator in segment.source_locator
            ]
        if not selected:
            raise DocumentReadError(
                "template_source_not_found",
                "The template locator did not resolve to any parsed source records.",
                details={"templateId": locator.template_id},
            )
        records = [self._segment_record(segment) for segment in selected]
        requested_fidelity = fidelity or locator.fidelity_level
        warnings: list[str] = []
        provided_fidelity = "text_and_table_structure"
        if requested_fidelity == "high_fidelity":
            warnings.append(
                "B02 returns source text and table structure; OOXML high-fidelity copy is deferred."
            )
        page, next_cursor, has_more, truncated = self._paginate(
            records,
            operation="read_template_range",
            max_chars=max_chars,
            max_items=max_items,
            cursor=cursor,
        )
        return self._response(
            "read_template_range",
            page,
            next_cursor=next_cursor,
            has_more=has_more,
            truncated=truncated,
            warnings=warnings,
            metadata={
                "templateId": locator.template_id,
                "templateType": locator.template_type,
                "requestedFidelity": requested_fidelity,
                "providedFidelity": provided_fidelity,
                "downloadable": locator.downloadable,
                "writingAgentUsable": locator.writing_agent_usable,
            },
        )


__all__ = [
    "DEFAULT_MAX_ITEMS",
    "DEFAULT_PAGE_CHARS",
    "DOCUMENT_READ_SCHEMA_VERSION",
    "DocumentIdentity",
    "DocumentReadContract",
    "DocumentReadError",
    "DocumentReadRecord",
    "DocumentReadResponse",
    "DocumentReadService",
    "SourceAnchor",
]
