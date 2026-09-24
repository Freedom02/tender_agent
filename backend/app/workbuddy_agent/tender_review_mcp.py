"""Strict MCP-shaped adapter for the tender document domain tools.

The adapter intentionally has no MCP package dependency.  It implements the
small JSON-RPC surface needed by a stdio MCP client and delegates all document
selection, pagination, and source validation to ``DocumentReadService``.
The later WorkBuddy runtime can construct this server with the current
``DocumentIR`` and ``AgentRun``; this module never opens a file path.
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .workflow.schemas import AgentRun, ToolCall
from .document_map import (
    CandidateFinding,
    DocumentMap,
    UnresolvedIssue,
    validate_document_map,
)
from ..tender_matching.document_models import DocumentIR
from .document_reading import DocumentReadError, DocumentReadService


MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_VERSION = "tender-review-mcp/v1"
MCP_TOOL_POLICY_VERSION = "tender-review-tools/v1"


class MCPArgumentContract(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DocumentIdentityArguments(MCPArgumentContract):
    document_id: str = Field("", alias="documentId")
    document_version_id: str = Field("", alias="documentVersionId")


class InspectArguments(DocumentIdentityArguments):
    pass


class ListSectionArguments(DocumentIdentityArguments):
    max_chars: int | None = Field(default=None, alias="maxChars", gt=0)
    max_items: int | None = Field(default=None, alias="maxItems", gt=0)
    cursor: str = ""


class ReadFullArguments(ListSectionArguments):
    pass


class ReadRangeArguments(ListSectionArguments):
    section_id: str = Field("", alias="sectionId")
    section_ids: list[str] = Field(default_factory=list, alias="sectionIds")
    segment_ids: list[str] = Field(default_factory=list, alias="segmentIds")
    start_segment_id: str = Field("", alias="startSegmentId")
    end_segment_id: str = Field("", alias="endSegmentId")
    table_ids: list[str] = Field(default_factory=list, alias="tableIds")
    page_start: int | None = Field(default=None, alias="pageStart", ge=1)
    page_end: int | None = Field(default=None, alias="pageEnd", ge=1)


class ReadSectionArguments(ListSectionArguments):
    section_id: str = Field(..., alias="sectionId", min_length=1)


class SearchArguments(DocumentIdentityArguments):
    query: str = Field(..., min_length=1)
    case_sensitive: bool = Field(False, alias="caseSensitive")
    use_regex: bool = Field(False, alias="useRegex")
    max_results: int = Field(50, alias="maxResults", gt=0)
    context_before: int = Field(1, alias="contextBefore", ge=0)
    context_after: int = Field(1, alias="contextAfter", ge=0)


class TableArguments(ListSectionArguments):
    table_id: str = Field(..., alias="tableId", min_length=1)
    row_start: int | None = Field(default=None, alias="rowStart", ge=0)
    row_end: int | None = Field(default=None, alias="rowEnd", ge=0)
    include_table_record: bool = Field(True, alias="includeTableRecord")


class TemplateArguments(ListSectionArguments):
    template_locator: dict[str, Any] = Field(..., alias="templateLocator")
    fidelity: str = ""


class SaveMapArguments(DocumentIdentityArguments):
    document_map: dict[str, Any] = Field(..., alias="documentMap")


_DOCUMENT_MAP_ARRAY_FIELDS = frozenset(
    {
        "sections",
        "ignoredSections",
        "rejectionCandidates",
        "unresolvedSections",
        "unresolvedIssues",
        "candidateFindings",
        "warnings",
        "secondaryDomains",
        "contextSectionIds",
        "sectionIds",
        "sourceSegmentIds",
        "segmentIds",
        "tableIds",
        "ranges",
        "contextSegmentIds",
        "options",
        "coveredSegmentIds",
        "unclassifiedSegmentIds",
    }
)

_DOCUMENT_MAP_STRING_LIST_FIELDS = frozenset(
    {
        "warnings",
        "unresolvedSections",
        "secondaryDomains",
        "contextSectionIds",
        "sectionIds",
        "sourceSegmentIds",
        "segmentIds",
        "tableIds",
        "contextSegmentIds",
        "coveredSegmentIds",
        "unclassifiedSegmentIds",
    }
)

_DOCUMENT_MAP_SOURCE_FORMATS = frozenset(
    {"docx", "pdf", "xlsx", "text", "doc", "unknown"}
)


def _normalize_document_map_contract(
    value: Any,
    *,
    field_name: str = "",
) -> tuple[Any, list[str]]:
    """Repair harmless SDK serialization drift before strict validation.

    The CodeBuddy SDK can emit empty strings for optional arrays, ``txt`` for
    the contract's ``text`` format, and unpaired UTF-16 surrogates in strings
    returned by a model.  These are transport-shape problems, not permission
    to accept unknown source IDs or invalid routing, so the normalized payload
    still goes through the full DocumentMap validator below.
    """

    warnings: list[str] = []

    if isinstance(value, str):
        normalized = value.encode("utf-8", "replace").decode("utf-8")
        if normalized != value:
            warnings.append("mcp_normalized_invalid_unicode")
        if field_name == "sourceFormat":
            format_value = normalized.strip().lower()
            aliases = {
                "txt": "text",
                "md": "text",
                "markdown": "text",
            }
            normalized = aliases.get(format_value, format_value)
            if normalized not in _DOCUMENT_MAP_SOURCE_FORMATS:
                normalized = "unknown"
            if normalized != value:
                warnings.append("mcp_normalized_source_format")
        return normalized, warnings

    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            normalized, item_warnings = _normalize_document_map_contract(item)
            result.append(normalized)
            warnings.extend(item_warnings)
        return result, warnings

    if not isinstance(value, Mapping):
        return value, warnings

    result: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text in _DOCUMENT_MAP_ARRAY_FIELDS and isinstance(item, str):
            if not item.strip():
                result[key_text] = []
                warnings.append(f"mcp_normalized_empty_array:{key_text}")
                continue
            if key_text in _DOCUMENT_MAP_STRING_LIST_FIELDS:
                result[key_text] = [item]
                warnings.append(f"mcp_normalized_scalar_array:{key_text}")
                continue
            # Structured arrays such as sections, ranges, issues and findings
            # cannot be recovered from a scalar without inventing source data.
            result[key_text] = []
            warnings.append(f"mcp_dropped_invalid_array:{key_text}")
            continue

        normalized, item_warnings = _normalize_document_map_contract(
            item,
            field_name=key_text,
        )
        result[key_text] = normalized
        warnings.extend(item_warnings)
    return result, warnings


def _autofill_template_locators(value: Any) -> list[str]:
    """Auto-fill a minimal TemplateLocator for template-domain sections the
    agent forgot to annotate.

    ``DocumentMapSection`` requires a non-null ``templateLocator`` whenever
    ``domain == "template"`` (schemas.py:239), but a single omitted field should
    not reject an otherwise complete map.  The locator only points back at the
    section's own source IDs, so deriving it is lossless and records a warning
    instead of failing validation.
    """
    warnings: list[str] = []
    if not isinstance(value, Mapping):
        return warnings
    sections = value.get("sections")
    if not isinstance(sections, list):
        return warnings
    for index, section in enumerate(sections):
        if not isinstance(section, Mapping):
            continue
        if section.get("domain") != "template" or section.get("templateLocator"):
            continue
        map_id = str(section.get("mapId") or "").strip()

        def _as_str_list(field: str) -> list[str]:
            raw = section.get(field) or []
            if not isinstance(raw, list):
                raw = [raw] if raw else []
            return [str(item) for item in raw if str(item).strip()]

        section_ids = _as_str_list("sectionIds")
        segment_ids = _as_str_list("sourceSegmentIds")
        table_ids = _as_str_list("tableIds")
        if not (section_ids or segment_ids or table_ids):
            if map_id:
                section_ids = [map_id]
            else:
                section_ids = [f"template-section-{index}"]

        locator: dict[str, Any] = {
            "templateId": f"TPL-{map_id}" if map_id else f"TPL-{index}",
            "templateType": "other",
            "fidelityLevel": "locator_only",
            "extractMode": "on_demand",
        }
        if section_ids:
            locator["sectionIds"] = section_ids
        if segment_ids:
            locator["segmentIds"] = segment_ids
        if table_ids:
            locator["tableIds"] = table_ids
        section["templateLocator"] = locator
        warnings.append(f"mcp_autofilled_template_locator:{map_id or index}")
    return warnings


def _unwrap_document_map_array_wrappers(
    value: Any,
    *,
    field_name: str = "",
) -> tuple[Any, bool]:
    """Accept one known SDK serialization wrapper before strict validation."""

    changed = False
    if isinstance(value, list):
        result = []
        for item in value:
            normalized, item_changed = _unwrap_document_map_array_wrappers(item)
            result.append(normalized)
            changed = changed or item_changed
        return result, changed

    if isinstance(value, Mapping):
        if (
            field_name in _DOCUMENT_MAP_ARRAY_FIELDS
            and set(value) == {"item"}
        ):
            item = value["item"]
            if isinstance(item, list):
                normalized, _ = _unwrap_document_map_array_wrappers(item)
                return normalized, True
            if item is None:
                return [], True
            normalized, _ = _unwrap_document_map_array_wrappers(item)
            return [normalized], True

        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized, item_changed = _unwrap_document_map_array_wrappers(
                item,
                field_name=str(key),
            )
            result[str(key)] = normalized
            changed = changed or item_changed
        return result, changed

    return value, False


class RecordIssueArguments(DocumentIdentityArguments):
    issue: dict[str, Any] = Field(..., min_length=1)


class RecordFindingArguments(DocumentIdentityArguments):
    finding: dict[str, Any] = Field(..., min_length=1)


class ToolDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str
    description: str
    input_schema: dict[str, Any] = Field(alias="inputSchema")


class TenderReviewMCPServer:
    """A document-bound, read-only-by-default MCP tool server."""

    def __init__(
        self,
        document: DocumentIR | Mapping[str, Any],
        *,
        run: AgentRun | Mapping[str, Any] | None = None,
        artifact_path: str | Path | None = None,
    ):
        self.document = (
            document
            if isinstance(document, DocumentIR)
            else DocumentIR.model_validate(document)
        )
        self.service = DocumentReadService(self.document)
        self.run = self._resolve_run(run)
        self.document_maps: dict[str, DocumentMap] = {}
        self.unresolved_issues: list[UnresolvedIssue] = []
        self.candidate_findings: list[CandidateFinding] = []
        self.tool_calls: list[ToolCall] = []
        self.artifact_path = Path(artifact_path) if artifact_path else None
        self._tool_counter = 0
        self._dispatch: dict[str, tuple[type[BaseModel], Callable[..., Any]]] = {
            "inspect_document": (InspectArguments, self._inspect_document),
            "list_section_candidates": (
                ListSectionArguments,
                self._list_section_candidates,
            ),
            "read_document_full": (ReadFullArguments, self._read_document_full),
            "read_document_range": (ReadRangeArguments, self._read_document_range),
            "read_section": (ReadSectionArguments, self._read_section),
            "search_document": (SearchArguments, self._search_document),
            "read_table_range": (TableArguments, self._read_table_range),
            "read_template_range": (
                TemplateArguments,
                self._read_template_range,
            ),
            "save_document_map": (SaveMapArguments, self._save_document_map),
            "record_unresolved_issue": (
                RecordIssueArguments,
                self._record_unresolved_issue,
            ),
            "record_candidate_finding": (
                RecordFindingArguments,
                self._record_candidate_finding,
            ),
        }

    def _resolve_run(
        self,
        run: AgentRun | Mapping[str, Any] | None,
    ) -> AgentRun:
        if isinstance(run, AgentRun):
            resolved = run
        elif isinstance(run, Mapping):
            resolved = AgentRun.model_validate(run)
        else:
            resolved = AgentRun(
                runId=f"MCP-{self.document.document_version_id or self.document.document_id}",
                documentId=self.document.document_id,
                documentVersionId=self.document.document_version_id,
                status="running",
                phase="document_map",
                toolPolicyVersion=MCP_TOOL_POLICY_VERSION,
            )
        if resolved.document_id != self.document.document_id:
            raise DocumentReadError(
                "document_id_mismatch",
                "AgentRun documentId does not match the bound document.",
            )
        if resolved.document_version_id != self.document.document_version_id:
            raise DocumentReadError(
                "document_version_mismatch",
                "AgentRun documentVersionId does not match the bound document.",
            )
        return resolved

    @staticmethod
    def _schema(model: type[BaseModel]) -> dict[str, Any]:
        return model.model_json_schema(by_alias=True)

    def tool_definitions(self) -> list[dict[str, Any]]:
        descriptions = {
            "inspect_document": "Inspect the current parsed document without returning full text.",
            "list_section_candidates": "List source-addressable section candidates.",
            "read_document_full": "Read the current parsed document in bounded pages.",
            "read_document_range": "Read a bounded section, segment, page, table, or segment range.",
            "read_section": "Read one section heading and its source segments.",
            "search_document": "Search the current parsed document and return source context.",
            "read_table_range": "Read a table summary, rows, cells, and coordinates.",
            "read_template_range": "Read a template locator from the current parsed document.",
            "save_document_map": "Validate and save a DocumentMap for the current run.",
            "record_unresolved_issue": "Record one unresolved classification or boundary issue.",
            "record_candidate_finding": "Record one useful observation for this run.",
        }
        return [
            ToolDefinition(
                name=name,
                description=descriptions[name],
                inputSchema=self._schema(model),
            ).model_dump(mode="json", by_alias=True)
            for name, (model, _) in self._dispatch.items()
        ]

    def _parameters_summary(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        for key, value in payload.items():
            if key in {"documentMap", "issue", "finding", "templateLocator"}:
                if isinstance(value, Mapping):
                    summary[key] = {
                        "keys": sorted(str(item) for item in value.keys())[:40],
                    }
                else:
                    summary[key] = {"present": bool(value)}
            elif isinstance(value, list):
                summary[key] = {"count": len(value)}
            elif isinstance(value, str) and len(value) > 160:
                summary[key] = value[:160]
            else:
                summary[key] = value
        return summary

    def _source_ids(self, value: Any) -> list[str]:
        found: list[str] = []
        if isinstance(value, Mapping):
            for key, item in value.items():
                if key.endswith("Id") or key.endswith("Ids"):
                    if isinstance(item, list):
                        found.extend(str(entry) for entry in item if str(entry).strip())
                    elif str(item).strip():
                        found.append(str(item))
                found.extend(self._source_ids(item))
        elif isinstance(value, list):
            for item in value:
                found.extend(self._source_ids(item))
        return list(dict.fromkeys(found))[:100]

    def _result_summary(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {"type": type(value).__name__}
        summary: dict[str, Any] = {}
        for key in (
            "operation",
            "recordCount",
            "matchCount",
            "hasMore",
            "truncated",
            "mapId",
            "issueId",
            "findingId",
        ):
            if key in value:
                summary[key] = value[key]
        if "records" in value and isinstance(value["records"], list):
            summary["recordCount"] = len(value["records"])
        if "matches" in value and isinstance(value["matches"], list):
            summary["matchCount"] = len(value["matches"])
        return summary

    def _tool_error_code(self, error: Exception) -> str:
        if isinstance(error, DocumentReadError):
            return {
                "invalid_cursor": "invalid_arguments",
                "invalid_limit": "invalid_arguments",
                "empty_query": "invalid_arguments",
                "invalid_query": "invalid_arguments",
                "invalid_range": "invalid_arguments",
                "empty_range": "invalid_arguments",
                "document_id_mismatch": "permission_denied",
                "document_version_mismatch": "permission_denied",
                "segment_not_found": "not_found",
                "section_not_found": "not_found",
                "table_not_found": "not_found",
                "template_source_not_found": "source_invalid",
                "validation_failed": "validation_failed",
            }.get(error.code, "internal_error")
        if isinstance(error, ValidationError):
            return "invalid_arguments"
        return "internal_error"

    def _record_tool_call(
        self,
        *,
        name: str,
        payload: Mapping[str, Any],
        result: Mapping[str, Any] | None = None,
        error: Exception | None = None,
        started: float,
    ) -> None:
        self._tool_counter += 1
        status = "failed" if error else "succeeded"
        error_code = self._tool_error_code(error) if error else ""
        error_message = str(error) if error else ""
        self.tool_calls.append(
            ToolCall(
                callId=f"CALL-{self._tool_counter:06d}",
                runId=self.run.run_id,
                toolName=name,
                status=status,
                parametersSummary=self._parameters_summary(payload),
                resultSummary=self._result_summary(result or {}),
                durationMs=max(0, int((time.perf_counter() - started) * 1000)),
                errorCode=error_code,
                errorMessage=error_message[:500],
                sourceIds=self._source_ids(result or payload),
            )
        )
        self.run = self.run.model_copy(
            update={"tool_call_count": len(self.tool_calls)}
        )
        self._write_artifact()

    def _write_artifact(self) -> None:
        """Persist the run-scoped audit artifact for an external MCP client.

        The CodeBuddy Agent SDK starts the MCP process separately from the backend
        runtime.  A run-scoped artifact is therefore the narrow handoff for
        the validated map and observable audit data; it is not a general file
        tool exposed to the agent.
        """
        if self.artifact_path is None:
            return
        payload = self.audit_snapshot()
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.artifact_path.with_suffix(
            self.artifact_path.suffix + ".tmp"
        )
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.artifact_path)

    def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if name not in self._dispatch:
            error = DocumentReadError(
                "tool_not_found",
                "The requested tool is not in the tender-review allowlist.",
            )
            return {"error": error.as_dict()}
        payload = dict(arguments or {})
        model, handler = self._dispatch[name]
        started = time.perf_counter()
        try:
            request = model.model_validate(payload)
            result = handler(request)
            if not isinstance(result, Mapping):
                result = {"result": result}
            normalized = dict(result)
            self._record_tool_call(
                name=name,
                payload=payload,
                result=normalized,
                started=started,
            )
            return normalized
        except (DocumentReadError, ValidationError, ValueError) as error:
            self._record_tool_call(
                name=name,
                payload=payload,
                error=error,
                started=started,
            )
            if isinstance(error, DocumentReadError):
                details = error.as_dict()
            elif isinstance(error, ValidationError):
                details = {
                    "code": "invalid_arguments",
                    "message": "Tool arguments failed schema validation.",
                    "details": error.errors(include_url=False),
                }
            else:
                details = {
                    "code": "internal_error",
                    "message": str(error),
                    "details": {},
                }
            return {"error": details}

    def _identity_kwargs(self, request: DocumentIdentityArguments) -> dict[str, str]:
        return {
            "document_id": request.document_id,
            "document_version_id": request.document_version_id,
        }

    def _inspect_document(self, request: InspectArguments) -> dict[str, Any]:
        return self.service.inspect_document(**self._identity_kwargs(request))

    def _list_section_candidates(self, request: ListSectionArguments) -> dict[str, Any]:
        return self.service.list_section_candidates(
            **self._identity_kwargs(request),
            max_chars=request.max_chars,
            max_items=request.max_items,
            cursor=request.cursor,
        )

    def _read_document_full(self, request: ReadFullArguments) -> dict[str, Any]:
        return self.service.read_document_full(
            **self._identity_kwargs(request),
            max_chars=request.max_chars,
            max_items=request.max_items,
            cursor=request.cursor,
        )

    def _read_document_range(self, request: ReadRangeArguments) -> dict[str, Any]:
        return self.service.read_document_range(
            **self._identity_kwargs(request),
            section_id=request.section_id,
            section_ids=request.section_ids,
            segment_ids=request.segment_ids,
            start_segment_id=request.start_segment_id,
            end_segment_id=request.end_segment_id,
            table_ids=request.table_ids,
            page_start=request.page_start,
            page_end=request.page_end,
            max_chars=request.max_chars,
            max_items=request.max_items,
            cursor=request.cursor,
        )

    def _read_section(self, request: ReadSectionArguments) -> dict[str, Any]:
        return self.service.read_section(
            request.section_id,
            **self._identity_kwargs(request),
            max_chars=request.max_chars,
            max_items=request.max_items,
            cursor=request.cursor,
        )

    def _search_document(self, request: SearchArguments) -> dict[str, Any]:
        return self.service.search_document(
            request.query,
            **self._identity_kwargs(request),
            case_sensitive=request.case_sensitive,
            use_regex=request.use_regex,
            max_results=request.max_results,
            context_before=request.context_before,
            context_after=request.context_after,
        )

    def _read_table_range(self, request: TableArguments) -> dict[str, Any]:
        return self.service.read_table_range(
            request.table_id,
            **self._identity_kwargs(request),
            row_start=request.row_start,
            row_end=request.row_end,
            include_table_record=request.include_table_record,
            max_chars=request.max_chars,
            max_items=request.max_items,
            cursor=request.cursor,
        )

    def _read_template_range(self, request: TemplateArguments) -> dict[str, Any]:
        return self.service.read_template_range(
            request.template_locator,
            **self._identity_kwargs(request),
            fidelity=request.fidelity,
            max_chars=request.max_chars,
            max_items=request.max_items,
            cursor=request.cursor,
        )

    def _save_document_map(self, request: SaveMapArguments) -> dict[str, Any]:
        self.service._assert_identity(
            request.document_id,
            request.document_version_id,
        )
        document_map_payload, normalized_wrappers = (
            _unwrap_document_map_array_wrappers(request.document_map)
        )
        document_map_payload, contract_warnings = _normalize_document_map_contract(
            document_map_payload
        )
        contract_warnings.extend(_autofill_template_locators(document_map_payload))
        validation = validate_document_map(
            document_map_payload,
            sections=self.document.sections,
            segments=self.document.segments or self.document.units,
            tables=self.document.tables,
            document_id=self.document.document_id,
            document_version_id=self.document.document_version_id,
        )
        if not validation.valid:
            raise DocumentReadError(
                "validation_failed",
                "DocumentMap validation failed.",
                details={
                    "issues": [
                        item.model_dump(mode="json", by_alias=True)
                        for item in validation.issues
                        if item.severity == "critical"
                    ],
                    "warnings": list(validation.warnings),
                    "coverage": dict(validation.coverage),
                },
            ) from None
        normalized = validation.normalized_map
        normalized_warnings = [
            *contract_warnings,
            *(
                ["mcp_normalized_array_item_wrappers"]
                if normalized_wrappers
                else []
            ),
        ]
        if normalized_warnings:
            normalized = normalized.model_copy(
                update={
                    "warnings": [
                        *normalized.warnings,
                        *normalized_warnings,
                    ]
                }
            )
        self.document_maps[normalized.map_id] = normalized
        return {
            "operation": "save_document_map",
            "mapId": normalized.map_id,
            "runId": normalized.run_id,
            "documentId": normalized.document_id,
            "documentVersionId": normalized.document_version_id,
            "sectionCount": len(normalized.sections),
            "ignoredSectionCount": len(normalized.ignored_sections),
            "rejectionCandidateCount": len(normalized.rejection_candidates),
            "warnings": normalized_warnings,
        }

    def _source_anchor_is_current(self, value: Mapping[str, Any]) -> None:
        source = value.get("sourceRefs") or value.get("source") or {}
        if not isinstance(source, Mapping):
            return
        segment_ids = [
            *(source.get("segmentIds") or []),
            *([source["segmentId"]] if source.get("segmentId") else []),
        ]
        section_ids = [
            *(source.get("sectionIds") or []),
            *([source["sectionId"]] if source.get("sectionId") else []),
        ]
        table_ids = [
            *(source.get("tableIds") or []),
            *([source["tableId"]] if source.get("tableId") else []),
        ]
        unknown = {
            "segmentIds": sorted(
                set(str(item) for item in segment_ids)
                - set(self.service._segment_by_id)
            ),
            "sectionIds": sorted(
                set(str(item) for item in section_ids)
                - set(self.service._section_by_id)
            ),
            "tableIds": sorted(
                set(str(item) for item in table_ids)
                - set(self.service._table_by_id)
            ),
        }
        if any(unknown.values()):
            raise DocumentReadError(
                "source_invalid",
                "The submitted finding references a source outside the current document.",
                details=unknown,
            )

    def _record_unresolved_issue(self, request: RecordIssueArguments) -> dict[str, Any]:
        self.service._assert_identity(
            request.document_id,
            request.document_version_id,
        )
        payload = dict(request.issue)
        payload.setdefault("runId", self.run.run_id)
        payload.setdefault("documentId", self.document.document_id)
        payload.setdefault("documentVersionId", self.document.document_version_id)
        self._source_anchor_is_current(payload)
        issue = UnresolvedIssue.model_validate(payload)
        self.unresolved_issues.append(issue)
        return {
            "operation": "record_unresolved_issue",
            "issueId": issue.issue_id,
            "retention": issue.retention,
            "status": issue.status,
        }

    def _record_candidate_finding(self, request: RecordFindingArguments) -> dict[str, Any]:
        self.service._assert_identity(
            request.document_id,
            request.document_version_id,
        )
        payload = dict(request.finding)
        payload.setdefault("runId", self.run.run_id)
        payload.setdefault("documentId", self.document.document_id)
        payload.setdefault("documentVersionId", self.document.document_version_id)
        self._source_anchor_is_current(payload)
        finding = CandidateFinding.model_validate(payload)
        self.candidate_findings.append(finding)
        return {
            "operation": "record_candidate_finding",
            "findingId": finding.finding_id,
            "retention": finding.retention,
            "status": finding.status,
        }

    def handle_message(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        method = str(message.get("method") or "")
        request_id = message.get("id")
        if method in {"notifications/initialized", "notifications/cancelled"}:
            return None
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "tender-review",
                        "version": MCP_SERVER_VERSION,
                    },
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": self.tool_definitions()},
            }
        if method == "tools/call":
            params = message.get("params")
            params = params if isinstance(params, Mapping) else {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments")
            result = self.call_tool(
                name,
                arguments if isinstance(arguments, Mapping) else {},
            )
            is_error = "error" in result
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False),
                        }
                    ],
                    "structuredContent": result,
                    "isError": is_error,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": -32601,
                "message": "Method not found.",
                "data": {"method": method},
            },
        }

    def audit_snapshot(self) -> dict[str, Any]:
        return {
            "schemaVersion": MCP_SERVER_VERSION,
            "toolPolicyVersion": MCP_TOOL_POLICY_VERSION,
            "run": self.run.model_dump(mode="json", by_alias=True),
            "toolCalls": [
                item.model_dump(mode="json", by_alias=True)
                for item in self.tool_calls
            ],
            "savedMapIds": sorted(self.document_maps),
            "documentMaps": {
                map_id: item.model_dump(mode="json", by_alias=True)
                for map_id, item in self.document_maps.items()
            },
            "unresolvedIssueIds": [item.issue_id for item in self.unresolved_issues],
            "unresolvedIssues": [
                item.model_dump(mode="json", by_alias=True)
                for item in self.unresolved_issues
            ],
            "candidateFindingIds": [
                item.finding_id for item in self.candidate_findings
            ],
            "candidateFindings": [
                item.model_dump(mode="json", by_alias=True)
                for item in self.candidate_findings
            ],
        }

    def write_audit_artifact(self) -> None:
        """Flush the current run audit for an external MCP process."""
        self._write_artifact()


def serve_stdio(
    server: TenderReviewMCPServer,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
) -> None:
    """Serve newline-delimited JSON-RPC without writing logs to stdout."""

    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, Mapping):
                raise ValueError("JSON-RPC message must be an object")
            response = server.handle_message(message)
            if response is not None:
                # Escape non-ASCII on the wire.  Windows CodeBuddy subprocesses
                # otherwise may decode UTF-8 source text as question marks.
                output_stream.write(json.dumps(response, ensure_ascii=True) + "\n")
                output_stream.flush()
        except (json.JSONDecodeError, ValueError) as error:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32700,
                    "message": "Invalid JSON-RPC message.",
                    "data": {"error": str(error)},
                },
            }
            output_stream.write(json.dumps(response, ensure_ascii=True) + "\n")
            output_stream.flush()


__all__ = [
    "MCP_PROTOCOL_VERSION",
    "MCP_SERVER_VERSION",
    "MCP_TOOL_POLICY_VERSION",
    "TenderReviewMCPServer",
    "serve_stdio",
]
