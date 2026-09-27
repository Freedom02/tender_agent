from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from .submission_structure import (
    is_reliable_submission_structure,
    merge_submission_structures,
    normalize_submission_structure,
    parse_submission_format_tree,
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:12]}"


class AgentResultEnvelope(BaseModel):
    """所有智能体共用的结果外壳。

    不同智能体的业务结果放在 ``output`` 中；外层统一记录来源、状态、
    输入引用、告警和置信度，方便编排层、前端和审计使用。
    """

    model_config = ConfigDict(extra="allow")

    runId: str
    agentId: str
    provider: str = "unknown"
    model: str = ""
    status: str = "success"
    inputRefs: list[str] = Field(default_factory=list)
    output: Any = Field(default_factory=dict)
    sourceRefs: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    confidence: float | None = Field(default=None, ge=0, le=1)
    createdAt: str = ""
    finishedAt: str = ""


class ParseBundle(BaseModel):
    """一次招标文件解析后的标准数据包。

    解析供应方可以是 local、workbuddy、pi 或 dify，但下游目录、撰写和
    审查流程只读取这个统一结构，不直接依赖供应方返回格式。
    """

    model_config = ConfigDict(extra="allow")

    parseBundleId: str
    schemaVersion: str = "1.0"
    provider: str = "unknown"
    status: str = "completed"
    projectId: str = ""
    workspaceId: str = ""
    createdAt: str = ""
    updatedAt: str = ""
    artifactPath: str = ""

    sourceFile: dict[str, Any] = Field(default_factory=dict)
    documentBlocks: dict[str, Any] = Field(default_factory=dict)
    documentStructure: dict[str, Any] = Field(default_factory=dict)
    projectMeta: dict[str, Any] = Field(default_factory=dict)
    requirements: list[dict[str, Any]] = Field(default_factory=list)
    evaluationItems: list[dict[str, Any]] = Field(default_factory=list)
    bidFormatSpec: dict[str, Any] = Field(default_factory=dict)
    submissionStructure: dict[str, Any] = Field(default_factory=lambda: normalize_submission_structure({}))
    templateFragments: list[dict[str, Any]] = Field(default_factory=list)
    templateFragmentsArtifact: str = ""
    rawOutline: list[dict[str, Any]] = Field(default_factory=list)
    riskItems: list[dict[str, Any]] = Field(default_factory=list)
    parseReviewReport: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    agentResults: list[dict[str, Any]] = Field(default_factory=list)
    rawProviderResult: dict[str, Any] = Field(default_factory=dict)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _structure_text(result: dict[str, Any], document_structure: dict[str, Any]) -> str:
    direct = str(
        result.get("documentText")
        or result.get("text")
        or result.get("analysisText")
        or ""
    ).strip()
    if direct:
        return direct
    sections = document_structure.get("sections")
    if not isinstance(sections, list):
        return ""
    parts: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        heading = str(section.get("heading") or section.get("title") or "").strip()
        body = str(section.get("text") or "").strip()
        if heading:
            parts.append(heading)
        if body:
            parts.append(body)
    return "\n".join(parts)


def _provider_from(run: dict[str, Any], provider: str) -> str:
    return str(
        provider
        or run.get("provider")
        or run.get("agentProvider")
        or "unknown"
    ).strip().lower() or "unknown"


def _source_file(
    run: dict[str, Any],
    *,
    root: Path | None = None,
    source_file: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = dict(source_file or {})
    filename = str(value.get("filename") or run.get("originalFilename") or "")
    input_artifact = str(value.get("storagePath") or run.get("inputArtifact") or "")
    if root and input_artifact:
        path = root / input_artifact
        try:
            if path.is_file():
                import hashlib

                value.setdefault("sha256", hashlib.sha256(path.read_bytes()).hexdigest())
                value.setdefault("sizeBytes", path.stat().st_size)
        except OSError:
            pass
    value.setdefault("fileId", str(run.get("sourceFileId") or ""))
    value.setdefault("filename", filename)
    value.setdefault("fileType", Path(filename).suffix.lower().lstrip("."))
    value.setdefault("mimeType", str(run.get("mimeType") or ""))
    value.setdefault("storagePath", input_artifact)
    value.setdefault("pageCount", run.get("pageCount"))
    return value


def build_agent_result(
    *,
    agent_id: str,
    output: Any,
    provider: str = "unknown",
    model: str = "",
    status: str = "success",
    input_refs: list[str] | None = None,
    source_refs: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    confidence: float | None = None,
    run_id: str = "",
    created_at: str = "",
    finished_at: str = "",
) -> dict[str, Any]:
    created = created_at or now_iso()
    result = AgentResultEnvelope(
        runId=run_id or new_id("agent-run"),
        agentId=agent_id,
        provider=provider or "unknown",
        model=model or "",
        status=status,
        inputRefs=list(input_refs or []),
        output=output,
        sourceRefs=list(source_refs or []),
        warnings=[str(item) for item in (warnings or []) if str(item).strip()],
        confidence=confidence,
        createdAt=created,
        finishedAt=finished_at or (created if status in {"success", "failed", "skipped"} else ""),
    )
    return result.model_dump(mode="json")


def build_parse_bundle(
    *,
    run: dict[str, Any] | None = None,
    result: dict[str, Any] | None = None,
    provider: str = "",
    project_id: str = "",
    workspace_id: str = "",
    root: Path | None = None,
    source_file: dict[str, Any] | None = None,
    document_blocks: dict[str, Any] | None = None,
    document_structure: dict[str, Any] | None = None,
    bid_format_spec: dict[str, Any] | None = None,
    parse_review_report: dict[str, Any] | None = None,
    agent_results: list[dict[str, Any]] | None = None,
    parse_bundle_id: str = "",
    status: str = "completed",
    raw_provider_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把任意已归一化的解析结果包装成标准 ParseBundle。"""

    run_value = _as_dict(run)
    result_value = _as_dict(result)
    resolved_provider = _provider_from(run_value, provider)
    resolved_project_id = str(project_id or run_value.get("projectId") or "")
    resolved_workspace_id = str(workspace_id or run_value.get("workspaceId") or "")
    resolved_run_id = str(run_value.get("runId") or "")
    bundle_id = str(
        parse_bundle_id
        or run_value.get("parseBundleId")
        or (f"parse-{resolved_run_id}" if resolved_run_id else new_id("parse"))
    )
    created = str(run_value.get("createdAt") or now_iso())
    updated = str(run_value.get("updatedAt") or now_iso())
    warnings = [
        str(item)
        for item in _as_list(result_value.get("warnings"))
        if str(item).strip()
    ]

    normalized_agent_results = list(agent_results or [])
    if not normalized_agent_results:
        normalized_agent_results.append(
            build_agent_result(
                run_id=resolved_run_id,
                agent_id="TenderParseAgent",
                provider=resolved_provider,
                model=str(run_value.get("model") or ""),
                output=result_value,
                input_refs=["artifact:sourceFile"],
                warnings=warnings,
                created_at=created,
                finished_at=updated if status in {"completed", "failed"} else "",
            )
        )

    resolved_document_blocks = _as_dict(
        document_blocks
        or result_value.get("documentBlocks")
    )
    resolved_document_structure = _as_dict(
        document_structure
        or result_value.get("documentStructure")
    )
    resolved_bid_format_spec = _as_dict(
        bid_format_spec
        or result_value.get("bidFormatSpec")
        or result_value.get("templateSpec")
    )
    provided_submission_structure = normalize_submission_structure(
        result_value.get("submissionStructure")
        or resolved_bid_format_spec.get("submissionStructure")
    )
    derived_submission_structure = parse_submission_format_tree(
        _structure_text(result_value, resolved_document_structure)
    )
    if is_reliable_submission_structure(derived_submission_structure):
        # 本地确定性解析能定位到“电子投标文件组成”时，以它作为骨架；
        # Pi/云端结果只补充属性，不能用一份低质量结构覆盖它。
        submission_structure = merge_submission_structures(
            derived_submission_structure,
            provided_submission_structure,
        )
        if not submission_structure.get("sourceLocator"):
            submission_structure["sourceLocator"] = (
                derived_submission_structure.get("sourceLocator") or ""
            )
    else:
        submission_structure = provided_submission_structure
    resolved_parse_review_report = _as_dict(
        parse_review_report
        or result_value.get("parseReviewReport")
    )
    resolved_agent_results = list(
        agent_results
        or result_value.get("agentResults")
        or []
    )
    if not resolved_agent_results:
        resolved_agent_results = normalized_agent_results

    bundle = ParseBundle(
        parseBundleId=bundle_id,
        schemaVersion="1.0",
        provider=resolved_provider,
        status=status,
        projectId=resolved_project_id,
        workspaceId=resolved_workspace_id,
        createdAt=created,
        updatedAt=updated,
        sourceFile=_source_file(run_value, root=root, source_file=source_file),
        documentBlocks=resolved_document_blocks,
        documentStructure=resolved_document_structure,
        projectMeta=_as_dict(result_value.get("projectMeta")),
        requirements=[
            item for item in _as_list(result_value.get("requirements"))
            if isinstance(item, dict)
        ],
        evaluationItems=[
            item for item in _as_list(result_value.get("evaluationItems"))
            if isinstance(item, dict)
        ],
        bidFormatSpec=resolved_bid_format_spec,
        submissionStructure=submission_structure,
        templateFragments=[
            item for item in _as_list(
                result_value.get("templateFragments")
            )
            if isinstance(item, dict)
        ],
        templateFragmentsArtifact=str(
            result_value.get("templateFragmentsArtifact") or ""
        ),
        rawOutline=[
            item for item in _as_list(result_value.get("outline"))
            if isinstance(item, dict)
        ],
        riskItems=[
            item for item in _as_list(result_value.get("riskItems"))
            if isinstance(item, dict)
        ],
        parseReviewReport=resolved_parse_review_report
        or {
            "status": "not_run",
            "warnings": warnings,
        },
        artifacts=[
            item for item in _as_list(result_value.get("artifacts"))
            if isinstance(item, dict)
        ],
        warnings=warnings,
        agentResults=resolved_agent_results,
        rawProviderResult=_as_dict(raw_provider_result) or result_value,
    )
    return bundle.model_dump(mode="json")


def validate_parse_bundle(value: Any) -> dict[str, Any]:
    """校验并重新序列化 ParseBundle，作为持久化边界。"""

    return ParseBundle.model_validate(value).model_dump(mode="json")
