from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from .schemas import ParseResult, SectionDraftResult


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def extract_workflow_outputs(payload: Any) -> dict[str, Any]:
    """Extract outputs from both blocking and loosely wrapped Dify responses."""
    root = _as_dict(payload)
    data = _as_dict(root.get("data"))
    for candidate in (
        data.get("outputs"),
        root.get("outputs"),
        data.get("output"),
        root.get("output"),
        data,
        root,
    ):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _parse_json_string(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    import json

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def normalize_parse_result(payload: Any) -> ParseResult:
    outputs = extract_workflow_outputs(payload)
    for key in ("result", "parse_result", "parseResult", "data"):
        nested = _parse_json_string(outputs.get(key))
        if isinstance(nested, dict):
            outputs = nested
            break

    try:
        return ParseResult.model_validate(outputs)
    except ValidationError as exc:
        raise ValueError(f"Dify 解析结果不符合约定格式: {exc}") from exc


def normalize_section_draft_result(payload: Any, *, section: dict[str, Any] | None = None) -> SectionDraftResult:
    outputs = extract_workflow_outputs(payload)
    for key in ("result", "draft_result", "draftResult", "section", "data"):
        nested = _parse_json_string(outputs.get(key))
        if isinstance(nested, dict):
            outputs = nested
            break

    content = ""
    for key in ("content", "markdown", "draft", "sectionContent", "text", "answer"):
        value = outputs.get(key)
        if isinstance(value, str) and value.strip():
            content = value.strip()
            break

    section = section if isinstance(section, dict) else {}
    merged = {
        "sectionId": str(outputs.get("sectionId") or outputs.get("section_id") or section.get("sectionId") or ""),
        "title": str(outputs.get("title") or section.get("title") or ""),
        "content": content,
        "sourceRefs": outputs.get("sourceRefs") or outputs.get("source_refs") or [],
        "materialMatches": outputs.get("materialMatches") or outputs.get("material_matches") or [],
        "unmatchedMaterialRequirementIds": (
            outputs.get("unmatchedMaterialRequirementIds")
            or outputs.get("unmatched_material_requirement_ids")
            or []
        ),
        "writingPlan": outputs.get("writingPlan") or outputs.get("writing_plan") or {},
        "agentResults": outputs.get("agentResults") or outputs.get("agent_results") or [],
        "provider": str(outputs.get("provider") or "unknown"),
        "phase": str(outputs.get("phase") or "section_draft"),
        "status": str(outputs.get("status") or ("generated" if content else "missing")),
        "warnings": outputs.get("warnings") or [],
    }
    try:
        result = SectionDraftResult.model_validate(merged)
    except ValidationError as exc:
        raise ValueError(f"Dify 章节撰写结果不符合约定格式: {exc}") from exc
    if not result.content.strip():
        raise ValueError("Dify 章节撰写结果缺少 content")
    return result
