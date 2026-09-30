"""Runtime boundary for the WorkBuddy document-map agent.

This module owns the independent map-first WorkBuddy + Agent MCP run.  The
locked builtin rules + LLM path is the only fallback; the former extraction
bridge and Sidecar are deleted and are not compatibility entry points.  The
runtime gives WorkBuddy a skill, one strict Tender Review MCP server, and a
small task envelope.  The agent reads the document through MCP and must save a
source-backed ``DocumentMap`` before the run can complete.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Mapping, Protocol

from pydantic import BaseModel, ConfigDict, Field

from ..config import (
    WORKBUDDY_AGENT_MCP_CONFIG_PATH,
    WORKBUDDY_AGENT_RUNTIME_DIR,
    WORKBUDDY_AGENT_SKILL_DIR,
)
from ..document_map import (
    DocumentMap,
    DocumentMapValidation,
    validate_document_map,
)
from ...tender_matching.document_models import DocumentIR
from .schemas import AgentCheckpoint, AgentRun, ToolCall

if TYPE_CHECKING:
    from ..tender_review_mcp import TenderReviewMCPServer


AGENT_RUNTIME_VERSION = "workbuddy-document-map-runtime/v1"
CODEBUDDY_SDK_DEFAULT_MODEL = "auto"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
HIGH_RISK_PATTERN = re.compile(
    r"废标|投标无效|响应无效|否决(?:投标|响应)?|无效响应|不予评审|取消资格|不接受",
)


class DocumentMapRuntimeConfig(BaseModel):
    """Bounded runtime settings for one document-map run."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    skill_dir: str = Field(
        WORKBUDDY_AGENT_SKILL_DIR,
        alias="skillDir",
    )
    mcp_config_path: str = Field(
        WORKBUDDY_AGENT_MCP_CONFIG_PATH,
        alias="mcpConfigPath",
    )
    runtime_dir: str = Field(
        WORKBUDDY_AGENT_RUNTIME_DIR,
        alias="runtimeDir",
    )
    model: str = ""
    codebuddy_code_path: str = Field("", alias="codebuddyCodePath")
    setting_sources: list[str] = Field(
        default_factory=list,
        alias="settingSources",
    )
    permission_mode: str = Field("default", alias="permissionMode")
    max_turns: int = Field(40, alias="maxTurns", ge=1, le=100)
    max_tool_calls: int = Field(40, alias="maxToolCalls", ge=1, le=500)
    max_save_attempts: int = Field(3, alias="maxSaveAttempts", ge=1, le=10)
    timeout_seconds: int = Field(600, alias="timeoutSeconds", ge=5, le=3600)
    max_retries: int = Field(0, alias="maxRetries", ge=0, le=3)
    require_map_validation: bool = Field(True, alias="requireMapValidation")
    require_rejection_scan: bool = Field(True, alias="requireRejectionScan")
    fallback_mode: str = Field("builtin", alias="fallbackMode")
    # OpenAI-compatible endpoint override (UINO's token.uino.com). When set,
    # the SDK injects CODEBUDDY_BASE_URL / OPENAI_BASE_URL + API key so the
    # CodeBuddy CLI routes LLM calls to this endpoint instead of resolving the
    # model name against CodeBuddy.cn's built-in model list (which collides
    # with UINO model names such as deepseek-v4-flash).
    llm_base_url: str = Field("", alias="llmBaseUrl")
    llm_api_key: str = Field("", alias="llmApiKey")
    # Reasoning models (e.g. UINO's deepseek-v4-flash) return reasoning_content
    # that must be round-tripped to the API. "adaptive"/"enabled" lets the SDK
    # handle that; keep "disabled" for non-reasoning models to save tokens.
    thinking_mode: str = Field("disabled", alias="thinkingMode")


class AgentExecutionEnvelope(BaseModel):
    """Observable output returned by a local or external WorkBuddy executor."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    document_map: dict[str, Any] | None = Field(
        default=None,
        alias="documentMap",
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="toolCalls",
    )
    unresolved_issues: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="unresolvedIssues",
    )
    candidate_findings: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="candidateFindings",
    )
    turn_count: int = Field(0, alias="turnCount", ge=0)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)


class DocumentMapRunResult(BaseModel):
    """Stable result returned to the backend orchestration layer."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: str = Field(
        "tender-document-map-runtime/v1",
        alias="schemaVersion",
    )
    status: str
    run: dict[str, Any]
    document_map: dict[str, Any] | None = Field(
        default=None,
        alias="documentMap",
    )
    tool_calls: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="toolCalls",
    )
    unresolved_issues: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="unresolvedIssues",
    )
    candidate_findings: list[dict[str, Any]] = Field(
        default_factory=list,
        alias="candidateFindings",
    )
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)
    validation: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    fallback_available: bool = Field(True, alias="fallbackAvailable")
    metadata: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class DocumentMapAgentTask:
    """The non-secret task envelope passed to an executor."""

    run: AgentRun
    document: DocumentIR
    server: TenderReviewMCPServer
    config: DocumentMapRuntimeConfig
    skill_dir: Path
    mcp_config_path: Path
    bootstrap_path: Path
    system_prompt_path: Path
    run_dir: Path
    system_prompt: str
    user_prompt: str


class DocumentMapExecutor(Protocol):
    def execute(self, task: DocumentMapAgentTask) -> Mapping[str, Any] | None:
        """Run WorkBuddy and return only observable handoff data."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _effective_agent_model(config: DocumentMapRuntimeConfig) -> str:
    return (
        str(config.model or "").strip()
        or os.environ.get("WORKBUDDY_MODEL", "").strip()
        or CODEBUDDY_SDK_DEFAULT_MODEL
    )


def _contained_path(value: str, *, label: str) -> Path:
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(PROJECT_ROOT.resolve())
    except ValueError:
        raise ValueError(f"{label} must stay inside the project root.") from None
    return resolved


def _extract_json_object(text: str) -> dict[str, Any] | None:
    candidates = [str(text or "").strip()]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*(.*?)```",
            str(text or ""),
            re.IGNORECASE | re.DOTALL,
        )
    )
    start = -1
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(str(text or "")):
        if start < 0:
            if char == "{":
                start = index
                depth = 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidates.append(str(text)[start : index + 1])
                start = -1
    for candidate in candidates:
        if not candidate:
            continue
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _high_risk_segment_ids(document: DocumentIR) -> list[str]:
    return [
        segment.id
        for segment in (document.segments or document.units)
        if segment.id and HIGH_RISK_PATTERN.search(segment.text or "")
    ]


def _document_identity(document: DocumentIR) -> dict[str, Any]:
    return {
        "documentId": document.document_id,
        "documentVersionId": document.document_version_id,
        "fileName": document.file_name,
        "fileHash": document.file_hash,
        "originalFormat": document.original_format,
    }


def _new_run(
    document: DocumentIR,
    config: DocumentMapRuntimeConfig,
    run: AgentRun | Mapping[str, Any] | None,
) -> AgentRun:
    if isinstance(run, AgentRun):
        resolved = run
    elif isinstance(run, Mapping):
        resolved = AgentRun.model_validate(run)
    else:
        resolved = AgentRun(
            runId=f"AR-MAP-{uuid.uuid4().hex[:12]}",
            documentId=document.document_id,
            documentVersionId=document.document_version_id,
        )
    if resolved.document_id != document.document_id:
        raise ValueError("AgentRun documentId does not match the document.")
    if resolved.document_version_id != document.document_version_id:
        raise ValueError("AgentRun documentVersionId does not match the document.")
    return resolved.model_copy(
        update={
            "status": "planning",
            "phase": "document_map",
            "model": _effective_agent_model(config) or resolved.model,
            "agent_runtime_version": AGENT_RUNTIME_VERSION,
            "max_turns": config.max_turns,
            "max_tool_calls": config.max_tool_calls,
            "started_at": resolved.started_at or _now_iso(),
            "updated_at": _now_iso(),
        }
    )


def _skill_prompt(skill_dir: Path) -> str:
    files = ("SKILL.md", "domain-guide.md", "boundary-policy.md")
    sections: list[str] = []
    for name in files:
        path = skill_dir / name
        if not path.is_file():
            raise ValueError(f"Skill file is missing: {path}")
        sections.append(f"===== {name} =====\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(sections)


def _user_prompt(run: AgentRun, document: DocumentIR) -> str:
    return json.dumps(
        {
            "task": "Build and save a validated DocumentMap for this tender document.",
            "run": run.model_dump(mode="json", by_alias=True),
            "document": _document_identity(document),
            "requirements": {
                "readFullDocument": True,
                "domains": [
                    "technical",
                    "qualification",
                    "commercial",
                    "evaluation",
                    "contract",
                    "template",
                    "project_info",
                    "rejection",
                ],
                "mustSaveWithTool": "save_document_map",
                "doNotReturnHiddenReasoning": True,
            },
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _document_map_contract_prompt(run: AgentRun, document: DocumentIR) -> str:
    """Give the model the strict save shape without exposing hidden internals."""

    section_example = json.dumps(
        {
            "mapId": "MSEC-EXAMPLE-001",
            "runId": run.run_id,
            "documentId": document.document_id,
            "documentVersionId": document.document_version_id,
            "domain": "technical",
            "role": "leaf",
            "processingMode": "fine_extract",
            "title": "Technical requirement range",
            "sectionIds": ["SEC-REAL"],
            "sourceSegmentIds": ["SEG-REAL"],
            "ranges": [
                {
                    "rangeId": "RNG-EXAMPLE-001",
                    "segmentIds": ["SEG-REAL"],
                    "contentSummary": "Source-backed requirement range",
                }
            ],
            "primaryRoute": "technical_processor",
            "priority": "high",
            "confidence": 0.9,
            "needsHumanReview": False,
            "classificationSource": "agent",
        },
        ensure_ascii=False,
        indent=2,
    )
    ignored_section_example = json.dumps(
        {
            "mapId": "MSEC-EXAMPLE-CONTRACT",
            "runId": run.run_id,
            "documentId": document.document_id,
            "documentVersionId": document.document_version_id,
            "domain": "contract",
            "role": "leaf",
            "processingMode": "ignore",
            "title": "Post-award contract terms",
            "sectionIds": ["SEC-REAL"],
            "sourceSegmentIds": ["SEG-REAL"],
            "primaryRoute": "ignored_contract",
            "ignored": True,
            "reason": "Post-award contract content is out of current review scope.",
            "classificationSource": "agent",
        },
        ensure_ascii=False,
        indent=2,
    )
    template_locator_example = json.dumps(
        {
            "templateId": "TPL-EXAMPLE-001",
            "templateType": "response_form",
            "title": "Response form",
            "sourceFormat": "docx",
            "sectionIds": ["SEC-REAL"],
            "segmentIds": ["SEG-REAL"],
            "extractMode": "on_demand",
            "downloadable": True,
            "writingAgentUsable": True,
            "fidelityLevel": "locator_only",
        },
        ensure_ascii=False,
        indent=2,
    )
    rejection_candidate_example = json.dumps(
        {
            "candidateId": "RC-EXAMPLE-001",
            "runId": run.run_id,
            "documentId": document.document_id,
            "matchedTerm": "invalid",
            "text": "Source text containing the rejection signal.",
            "contextText": "The surrounding source context.",
            "source": {
                "sourceFormat": "docx",
                "sectionIds": ["SEC-REAL"],
                "segmentIds": ["SEG-REAL"],
            },
            "contextSegmentIds": ["SEG-REAL"],
            "status": "candidate",
            "isHardRisk": True,
            "reason": "Requires context confirmation.",
        },
        ensure_ascii=False,
        indent=2,
    )
    issue_example = json.dumps(
        {
            "issueId": "ISS-EXAMPLE-001",
            "runId": run.run_id,
            "documentId": document.document_id,
            "documentVersionId": document.document_version_id,
            "type": "classification_ambiguity",
            "question": "Which domain owns this mixed range?",
            "whyUnclear": "The source contains two business purposes.",
            "sourceRefs": {"sectionIds": ["SEC-REAL"], "segmentIds": ["SEG-REAL"]},
            "status": "needs_user",
            "retention": "run_only",
        },
        ensure_ascii=False,
        indent=2,
    )
    finding_example = json.dumps(
        {
            "findingId": "FND-EXAMPLE-001",
            "runId": run.run_id,
            "documentId": document.document_id,
            "documentVersionId": document.document_version_id,
            "summary": "A useful source-backed observation.",
            "sourceRefs": {"sectionIds": ["SEC-REAL"], "segmentIds": ["SEG-REAL"]},
            "retention": "run_only",
            "status": "pending_review",
        },
        ensure_ascii=False,
        indent=2,
    )

    return f"""
===== save_document_map contract =====
The value passed as ``documentMap`` must be one JSON object with this shape:
{{
  "schemaVersion": "tender-document-map/v1",
  "mapId": "MAP-{run.run_id}-001",
  "runId": "{run.run_id}",
  "documentId": "{document.document_id}",
  "documentVersionId": "{document.document_version_id}",
  "documentType": "government_tender",
  "sections": [],
  "ignoredSections": [],
  "rejectionCandidates": [],
  "unresolvedSections": [],
  "unresolvedIssues": [],
  "candidateFindings": [],
  "coverage": {{
    "coveredSegmentIds": [],
    "unclassifiedSegmentIds": [],
    "highRiskHitCount": 0,
    "highRiskCoveredCount": 0,
    "domainCounts": {{}}
  }},
  "warnings": [],
  "agentSummary": {{
    "source": "workbuddy-document-map",
    "reason": "",
    "model": "",
    "skillVersion": "tender-review/v1",
    "toolPolicyVersion": "tender-review-tools/v1",
    "turnCount": 0,
    "toolCallCount": 0
  }}
}}

Use only the declared camelCase keys from the DocumentMap contract. These are
the exact object patterns; replace every ``SEC-REAL`` and ``SEG-REAL`` with
IDs returned by the MCP tools:

Map section example:
{section_example}

Ignored contract section example (put this object in ``ignoredSections``, not
``sections``):
{ignored_section_example}

Template locator example:
{template_locator_example}

Rejection candidate example:
{rejection_candidate_example}

Unresolved issue example:
{issue_example}

Candidate finding example:
{finding_example}

Do not use ``sectionId`` or ``sectionTitle`` on a map section; use
``mapId`` and ``title``. Do not use ``sourceType`` or ``sourceLocator`` on a
range unless it is inside the ``source`` anchor. Do not add ``mapId``,
``sourceSegmentIds``, ``stage``, ``trigger``, or other invented keys to a
rejection candidate. Every section object needs its own unique ``mapId``.
All fields declared as arrays must always be JSON arrays. When there are no
items, write ``[]`` exactly; never write an empty string ``""``, ``null``, or
an object for ``sections``, ``ignoredSections``, ``rejectionCandidates``,
``unresolvedSections``, ``unresolvedIssues``, ``candidateFindings``,
``sectionIds``, ``sourceSegmentIds``, ``tableIds``, ``ranges``,
``contextSegmentIds``, ``secondaryDomains``, or ``warnings``.
An array value must be written directly as ``[...]``. Never wrap it as
``{{"item": [...]}}`` or ``{{"item": {{...}}}}``; the MCP validator receives
the JSON object exactly as written.

Every map section must have a real source reference in ``sectionIds``,
``sourceSegmentIds``, ``tableIds``, or ``ranges``. Use these exact route shapes:
- technical: role=leaf, processingMode=fine_extract,
  primaryRoute=technical_processor
- qualification: role=leaf, processingMode=fine_extract,
  primaryRoute=qualification_processor
- commercial: processingMode=display, primaryRoute=display_processor
- evaluation: processingMode=display, primaryRoute=evaluation_processor
- project_info: role=overlay, processingMode=full_scan,
  primaryRoute=display_processor
- rejection: role=overlay, processingMode=full_scan,
  primaryRoute=rejection_processor
- template: processingMode=template_extract,
  primaryRoute=template_processor, and a non-null templateLocator
- contract: put the section in ignoredSections with
  processingMode=ignore, primaryRoute=ignored_contract, ignored=true

For ``templateLocator``, include at least ``templateId`` and one real
``sectionIds`` or ``segmentIds`` value. For ``rejectionCandidates``, use
``candidateId``, ``matchedTerm``, ``contextSegmentIds`` and a source anchor.
For ``unresolvedIssues`` and ``candidateFindings``, use one source anchor
object in ``sourceRefs`` (not a list). Do not include invented keys such as
``parts``, ``characterCount``, ``sectionCount``, ``fileName`` or
``hasTemplateSection``. After validation errors, repair the same map using
the exact source IDs and the returned error details, then call
``save_document_map`` again.
"""


def _strict_mcp_config(
    source_path: Path,
    *,
    bootstrap_path: Path,
    artifact_path: Path,
) -> dict[str, Any]:
    try:
        payload = json.loads(source_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read MCP config: {error}") from None
    servers = payload.get("mcpServers")
    if not isinstance(servers, dict) or set(servers) != {"tender-review"}:
        raise ValueError(
            "The document-map runtime requires exactly one MCP server: tender-review."
        )
    server = servers["tender-review"]
    if not isinstance(server, dict):
        raise ValueError("The tender-review MCP configuration must be an object.")
    normalized = json.loads(json.dumps(payload))
    env = normalized["mcpServers"]["tender-review"].setdefault("env", {})
    env["TENDER_REVIEW_BOOTSTRAP_PATH"] = str(bootstrap_path)
    env["TENDER_REVIEW_ARTIFACT_PATH"] = str(artifact_path)
    return normalized


def _sdk_mcp_config(
    source_path: Path,
    *,
    bootstrap_path: Path,
    artifact_path: Path,
) -> dict[str, Any]:
    """Create an SDK-safe MCP config using the active Python interpreter."""

    payload = _strict_mcp_config(
        source_path,
        bootstrap_path=bootstrap_path,
        artifact_path=artifact_path,
    )
    server = payload["mcpServers"]["tender-review"]
    server["command"] = sys.executable
    server["args"] = [
        str(PROJECT_ROOT / "scripts" / "tender_review_mcp.py"),
    ]
    current_python_path = str(server.get("env", {}).get("PYTHONPATH") or "").strip()
    python_paths = [str(PROJECT_ROOT)]
    if current_python_path:
        python_paths.append(current_python_path)
    server.setdefault("env", {})["PYTHONPATH"] = os.pathsep.join(python_paths)
    return payload


def _run_async(factory: Callable[[], Any]) -> Any:
    """Run one SDK coroutine from both sync routes and async callers."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(factory())

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(factory())).result()


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate a worker and any CodeBuddy/MCP descendants it owns."""

    if process.poll() is not None:
        return
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=8,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:
            import signal

            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.wait(timeout=3)


def _read_runtime_artifact(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _sdk_message_text(message: Any) -> list[str]:
    """Extract observable assistant/result text without persisting reasoning."""

    message_type = type(message).__name__
    if message_type == "ResultMessage":
        value = getattr(message, "result", None)
        return [str(value)] if value else []
    if message_type != "AssistantMessage":
        return []
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return []
    return [
        str(getattr(block, "text", ""))
        for block in content
        if getattr(block, "text", None)
    ]


def _sdk_tool_short_name(tool_name: Any) -> str:
    return str(tool_name or "").rsplit("__", 1)[-1]


class WorkBuddySdkExecutor:
    """Run the document-map agent through the CodeBuddy Python Agent SDK.

    The SDK owns the CodeBuddy headless process and stream protocol.  The
    application still owns the MCP config, artifact boundary and validation
    gate, so switching SDK versions does not change the business contract.
    """

    def __init__(self, *, config_dir: Path | None = None):
        self.config_dir = config_dir or (Path.home() / ".workbuddy")

    def execute(self, task: DocumentMapAgentTask) -> Mapping[str, Any] | None:
        if os.environ.get("WORKBUDDY_SDK_WORKER") == "1":
            return self._execute_inline(task)
        return self._execute_isolated(task)

    def _execute_isolated(
        self,
        task: DocumentMapAgentTask,
    ) -> Mapping[str, Any] | None:
        """Run SDK code in a killable process.

        The SDK owns a nested CodeBuddy process and an MCP process.  A thread
        timeout cannot reliably reap that tree on Windows, so the application
        boundary is a short-lived Python worker process instead.
        """

        request_path = task.run_dir / "sdk-worker-request.json"
        result_path = task.run_dir / "sdk-worker-result.json"
        stdout_path = task.run_dir / "sdk-worker.stdout.log"
        stderr_path = task.run_dir / "sdk-worker.stderr.log"
        request_path.write_text(
            json.dumps(
                {
                    "artifactPath": str(task.run_dir / "mcp-audit.json"),
                    "bootstrapPath": str(task.bootstrap_path),
                    "config": task.config.model_dump(mode="json", by_alias=True),
                    "configDir": str(self.config_dir),
                    "document": task.document.model_dump(mode="json", by_alias=True),
                    "mcpConfigPath": str(task.mcp_config_path),
                    "run": task.run.model_dump(mode="json", by_alias=True),
                    "runDir": str(task.run_dir),
                    "skillDir": str(task.skill_dir),
                    "systemPrompt": task.system_prompt,
                    "systemPromptPath": str(task.system_prompt_path),
                    "userPrompt": task.user_prompt,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["WORKBUDDY_SDK_WORKER"] = "1"
        env["CODEBUDDY_CONFIG_DIR"] = str(self.config_dir)
        existing_python_path = env.get("PYTHONPATH", "").strip()
        env["PYTHONPATH"] = os.pathsep.join(
            value
            for value in (str(PROJECT_ROOT), existing_python_path)
            if value
        )
        command = [
            sys.executable,
            "-m",
            "backend.app.workbuddy_agent.workflow.sdk_worker",
            "--request",
            str(request_path),
            "--result",
            str(result_path),
        ]
        started = time.monotonic()
        timed_out = False
        with (
            stdout_path.open("w", encoding="utf-8") as stdout,
            stderr_path.open("w", encoding="utf-8") as stderr,
        ):
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
            try:
                process.wait(timeout=task.config.timeout_seconds + 8)
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_tree(process)

        result = _read_runtime_artifact(result_path)
        errors = [
            str(item)
            for item in result.get("errors") or []
            if str(item).strip()
        ]
        warnings = [
            str(item)
            for item in result.get("warnings") or []
            if str(item).strip()
        ]
        if timed_out:
            errors.append(
                "CodeBuddy SDK worker exceeded "
                f"{task.config.timeout_seconds} seconds; its process tree was terminated."
            )
        elif process.returncode not in (0, None):
            stderr_text = ""
            with contextlib.suppress(OSError):
                stderr_text = stderr_path.read_text(encoding="utf-8").strip()
            detail = stderr_text[-2000:] if stderr_text else ""
            errors.append(
                "CodeBuddy SDK worker exited with code "
                f"{process.returncode}."
                + (f" {detail}" if detail else "")
            )
        warnings.append(
            "CodeBuddy Python Agent SDK worker elapsed "
            f"{round(time.monotonic() - started, 2)} seconds."
        )
        result["warnings"] = list(dict.fromkeys(warnings))
        result["errors"] = list(dict.fromkeys(errors))
        output = result.get("output")
        output = dict(output) if isinstance(output, Mapping) else {}
        output.update(
            {
                "executor": "codebuddy_agent_sdk",
                "isolatedWorker": True,
                "workerReturnCode": process.returncode,
            }
        )
        result["output"] = output
        return result

    def _execute_inline(self, task: DocumentMapAgentTask) -> Mapping[str, Any] | None:
        artifact_path = task.run_dir / "mcp-audit.json"
        mcp_payload = _sdk_mcp_config(
            task.mcp_config_path,
            bootstrap_path=task.bootstrap_path,
            artifact_path=artifact_path,
        )
        run_mcp_config = task.run_dir / "mcp.json"
        run_mcp_config.write_text(
            json.dumps(mcp_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        started = time.monotonic()
        assistant_text: list[str] = []
        sdk_metadata: dict[str, Any] = {}
        errors: list[str] = []
        turn_count = 0

        async def run_query() -> None:
            nonlocal turn_count
            try:
                from codebuddy_agent_sdk import (
                    CodeBuddyAgentOptions,
                    PermissionResultAllow,
                    PermissionResultDeny,
                    query,
                )
            except ModuleNotFoundError as error:
                raise RuntimeError(
                    "CodeBuddy Agent SDK is not installed in the active Python "
                    "environment. Install codebuddy-agent-sdk==0.3.263."
                ) from error

            async def can_use_tool(
                tool_name: str,
                input_data: dict[str, Any],
                _options: Any,
            ) -> Any:
                """Keep the model's read/search loop bounded before saving."""

                short_name = _sdk_tool_short_name(tool_name)

                if short_name == "Read":
                    return PermissionResultDeny(
                        "Read is disabled for this document-bound agent. "
                        "Read document content only through the tender-review "
                        "MCP tools: read_document_full, read_document_range, "
                        "read_section. If a read returned hasMore=true, call "
                        "the same tool again with its nextCursor to read the "
                        "next page, then build the map and call save_document_map."
                    )

                def audit_calls() -> list[Any]:
                    local_calls: list[Any] = list(
                        getattr(task.server, "tool_calls", []) or []
                    )
                    artifact_calls = _read_runtime_artifact(artifact_path).get("toolCalls")
                    if isinstance(artifact_calls, list):
                        local_calls.extend(
                            item for item in artifact_calls if isinstance(item, Mapping)
                        )
                    return local_calls

                def call_name(item: Any) -> str:
                    if isinstance(item, Mapping):
                        return _sdk_tool_short_name(
                            item.get("toolName") or item.get("tool_name")
                        )
                    return _sdk_tool_short_name(getattr(item, "tool_name", ""))

                def call_status(item: Any) -> str:
                    if isinstance(item, Mapping):
                        return str(item.get("status") or "")
                    return str(getattr(item, "status", "") or "")

                def result_summary(item: Any) -> Mapping[str, Any]:
                    if isinstance(item, Mapping):
                        value = item.get("resultSummary") or item.get("result_summary")
                    else:
                        value = getattr(item, "result_summary", {})
                    return value if isinstance(value, Mapping) else {}

                calls = audit_calls()
                successful_calls = [
                    item for item in calls if call_status(item) == "succeeded"
                ]
                if len(calls) >= task.config.max_tool_calls:
                    return PermissionResultDeny(
                        "The Tender Review tool budget is exhausted. "
                        "Use the evidence already read and call save_document_map now."
                    )

                if short_name == "save_document_map":
                    save_attempts = sum(
                        call_name(item) == "save_document_map"
                        for item in calls
                    )
                    if save_attempts >= task.config.max_save_attempts:
                        return PermissionResultDeny(
                            "The save_document_map attempt budget is exhausted. "
                            "Return a concise failure summary without calling more tools."
                        )
                    return PermissionResultAllow()

                if short_name in {"inspect_document", "list_section_candidates"}:
                    if any(
                        call_name(item) == short_name
                        for item in successful_calls
                    ):
                        return PermissionResultDeny(
                            f"{short_name} already succeeded. Do not repeat it; "
                            "continue with the map and save_document_map."
                        )

                if short_name == "read_document_full":
                    full_reads = [
                        item
                        for item in successful_calls
                        if call_name(item) == "read_document_full"
                    ]
                    cursor = str(input_data.get("cursor") or "").strip()
                    if full_reads and not cursor:
                        return PermissionResultDeny(
                            "read_document_full already started. Use its nextCursor "
                            "when hasMore=true, or move to save_document_map."
                        )
                    if len(full_reads) >= 12:
                        return PermissionResultDeny(
                            "The full-document read budget is exhausted. "
                            "Use the source IDs already read and save the map."
                        )

                if short_name == "search_document":
                    full_reads = [
                        item
                        for item in successful_calls
                        if call_name(item) == "read_document_full"
                    ]
                    if full_reads and any(
                        bool(result_summary(item).get("hasMore"))
                        for item in full_reads[-1:]
                    ):
                        return PermissionResultDeny(
                            "The full document has more pages. Follow nextCursor "
                            "before searching, then save the map."
                        )
                    search_count = sum(
                        call_name(item) == "search_document"
                        for item in successful_calls
                    )
                    if search_count >= 2:
                        return PermissionResultDeny(
                            "The search budget is exhausted. Do not search again; "
                            "use the evidence already read and call save_document_map."
                        )

                return PermissionResultAllow()

            runtime_env = {
                "CODEBUDDY_CONFIG_DIR": str(self.config_dir),
                "CODEBUDDY_DISABLE_AUTO_MEMORY": "1",
            }
            if task.config.llm_base_url:
                endpoint = task.config.llm_base_url.rstrip("/")
                if not endpoint.endswith("/v1"):
                    endpoint += "/v1"
                # Force the CLI to route LLM calls through this OpenAI-compatible
                # endpoint rather than CodeBuddy.cn. Both CODEBUDDY_* and
                # OPENAI_* are set so whichever env contract the CLI honors wins.
                runtime_env["CODEBUDDY_BASE_URL"] = endpoint
                runtime_env["OPENAI_BASE_URL"] = endpoint
                if task.config.llm_api_key:
                    runtime_env["CODEBUDDY_API_KEY"] = task.config.llm_api_key
                    runtime_env["OPENAI_API_KEY"] = task.config.llm_api_key

            options = CodeBuddyAgentOptions(
                # Keep MCP tools available to the model while denying the
                # general-purpose tools that could escape the document-bound
                # Tender Review boundary.
                tools=None,
                disallowed_tools=[
                    "Bash",
                    "Write",
                    "Edit",
                    "MultiEdit",
                    "Glob",
                    "Grep",
                    "NotebookEdit",
                    "WebFetch",
                    "WebSearch",
                    "Task",
                    "TodoWrite",
                    "Skill",
                ],
                mcp_servers=str(run_mcp_config),
                system_prompt=task.system_prompt,
                permission_mode=task.config.permission_mode,
                max_turns=task.config.max_turns,
                model=(
                    task.config.model
                    or os.environ.get("WORKBUDDY_MODEL", "").strip()
                    or CODEBUDDY_SDK_DEFAULT_MODEL
                ),
                cwd=str(PROJECT_ROOT),
                codebuddy_code_path=(
                    task.config.codebuddy_code_path.strip()
                    or os.environ.get("CODEBUDDY_CODE_PATH", "").strip()
                    or None
                ),
                setting_sources=task.config.setting_sources,
                persist_session=False,
                request_timeout_ms=task.config.timeout_seconds * 1000,
                thinking=(
                    {"type": "adaptive"}
                    if task.config.thinking_mode == "adaptive"
                    else {"type": "enabled", "budget_tokens": 8000}
                    if task.config.thinking_mode == "enabled"
                    else {"type": "disabled"}
                ),
                can_use_tool=can_use_tool,
                env=runtime_env,
            )

            async for message in query(
                prompt=task.user_prompt,
                options=options,
            ):
                assistant_text.extend(_sdk_message_text(message))
                message_type = type(message).__name__
                if message_type == "ResultMessage":
                    turn_count = int(getattr(message, "num_turns", 0) or 0)
                    sdk_metadata.update(
                        {
                            "reportedNumTurns": turn_count,
                            "sessionId": str(getattr(message, "session_id", "") or ""),
                            "durationMs": int(getattr(message, "duration_ms", 0) or 0),
                            "durationApiMs": int(
                                getattr(message, "duration_api_ms", 0) or 0
                            ),
                            "totalCostUsd": getattr(message, "total_cost_usd", None),
                            "stopReason": str(
                                getattr(message, "stop_reason", "") or ""
                            ),
                        }
                    )
                    if getattr(message, "is_error", False):
                        errors.extend(
                            str(item)
                            for item in (getattr(message, "errors", None) or [])
                            if str(item).strip()
                        )
                elif message_type == "SystemMessage":
                    data = getattr(message, "data", {})
                    if isinstance(data, Mapping) and data.get("subtype") == "init":
                        sdk_metadata["mcpServers"] = data.get("mcp_servers") or []

        try:
            _run_async(
                lambda: asyncio.wait_for(
                    run_query(),
                    timeout=task.config.timeout_seconds,
                )
            )
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")

        artifact = _read_runtime_artifact(artifact_path)
        maps = artifact.get("documentMaps")
        saved_map_ids = {
            str(value)
            for value in (artifact.get("savedMapIds") or [])
            if str(value).strip()
        }
        document_map = (
            next(reversed(maps.values()))
            if isinstance(maps, dict) and maps
            else None
        )
        output = _extract_json_object("\n".join(assistant_text))
        if document_map is None and isinstance(output, dict):
            document_map = output.get("documentMap")
        if not document_map:
            errors.append("CodeBuddy SDK did not save a DocumentMap through MCP.")
        elif not saved_map_ids:
            errors.append(
                "CodeBuddy SDK returned a map without saving it through "
                "save_document_map."
            )

        map_summary = (
            document_map.get("agentSummary")
            if isinstance(document_map, Mapping)
            and isinstance(document_map.get("agentSummary"), Mapping)
            else {}
        )
        reported_turn_count = int(
            map_summary.get("turnCount")
            or (output or {}).get("turnCount")
            or turn_count
            or 0
        )

        for status in sdk_metadata.get("mcpServers") or []:
            if isinstance(status, Mapping) and status.get("status") != "connected":
                errors.append(
                    "Tender Review MCP connection was not confirmed: "
                    f"{status.get('name') or 'tender-review'}="
                    f"{status.get('status') or 'unknown'}."
                )

        return {
            "documentMap": document_map,
            "toolCalls": artifact.get("toolCalls") or [],
            "unresolvedIssues": artifact.get("unresolvedIssues") or [],
            "candidateFindings": artifact.get("candidateFindings") or [],
            "turnCount": reported_turn_count,
            "warnings": [
                "CodeBuddy Python Agent SDK executor elapsed "
                f"{round(time.monotonic() - started, 2)} seconds."
            ],
            "errors": list(dict.fromkeys(errors)),
            "output": {
                **(output or {}),
                "executor": "codebuddy_agent_sdk",
                "sdk": sdk_metadata,
            },
        }


class DocumentMapAgentRuntime:
    """Run a bounded, source-safe WorkBuddy document-map task."""

    def __init__(
        self,
        document: DocumentIR | Mapping[str, Any],
        *,
        config: DocumentMapRuntimeConfig | Mapping[str, Any] | None = None,
        run: AgentRun | Mapping[str, Any] | None = None,
        executor: DocumentMapExecutor | Callable[[DocumentMapAgentTask], Mapping[str, Any] | None] | None = None,
    ):
        self.document = (
            document
            if isinstance(document, DocumentIR)
            else DocumentIR.model_validate(document)
        )
        self.config = (
            config
            if isinstance(config, DocumentMapRuntimeConfig)
            else DocumentMapRuntimeConfig.model_validate(config or {})
        )
        self.agent_run = _new_run(self.document, self.config, run)
        if executor is not None:
            self.executor = executor
        else:
            config_dir = Path(
                os.environ.get(
                    "CODEBUDDY_CONFIG_DIR",
                    str(Path.home() / ".workbuddy"),
                )
            )
            self.executor = WorkBuddySdkExecutor(config_dir=config_dir)

    def _new_server(self, run: AgentRun) -> TenderReviewMCPServer:
        from ..tender_review_mcp import TenderReviewMCPServer

        return TenderReviewMCPServer(self.document, run=run)

    def _prepare_task(
        self,
        run: AgentRun,
        server: TenderReviewMCPServer,
    ) -> DocumentMapAgentTask:
        skill_dir = _contained_path(self.config.skill_dir, label="skillDir")
        mcp_config_path = _contained_path(
            self.config.mcp_config_path,
            label="mcpConfigPath",
        )
        runtime_dir = _contained_path(self.config.runtime_dir, label="runtimeDir")
        run_dir = runtime_dir / run.run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        bootstrap_path = run_dir / "bootstrap.json"
        bootstrap_path.write_text(
            json.dumps(
                {
                    "document": self.document.model_dump(mode="json", by_alias=True),
                    "run": run.model_dump(mode="json", by_alias=True),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        system_prompt = (
            "You are the WorkBuddy tender document-map agent. "
            "Use only the tender-review MCP server for document content. "
            "Read the full normalized document, inspect boundaries, record "
            "unresolved issues when necessary, and save exactly one validated "
            "DocumentMap with save_document_map. Do not use shell, arbitrary "
            "filesystem paths, enterprise matching, or hidden reasoning.\n\n"
            + _skill_prompt(skill_dir)
            + "\n\n"
            + f"""
===== bounded execution budget =====
Use this order. The runtime blocks tool calls that arrive out of order.

READ THE WHOLE DOCUMENT (do not stop early):
1. inspect_document once.
2. list_section_candidates once.
3. read_document_full with maxChars=30000. Check the response: if hasMore is
   true, call read_document_full AGAIN with the returned nextCursor. Repeat
   until a response returns hasMore=false. You have NOT seen the whole
   document until hasMore=false; a map built from a partial read misses
   sections and fails the coverage gate. The runtime blocks search and save
   while hasMore is still true, so paginate to the end first.

SEARCH REJECTION AND COVER EVERY HIT (do not under-collect):
4. Call search_document with this EXACT regex (useRegex=true) and nothing
   narrower:
   {HIGH_RISK_PATTERN.pattern}
   The response lists matching segments. Count them. Put EVERY matching
   segment ID into the rejection section's sourceSegmentIds (or a range on
   that section) so the coverage gate counts it, and record the significant
   ones as rejection candidates. Before save_document_map, re-check that every
   segment the search returned appears in rejection sourceSegmentIds — missing
   even one invalidates the map. Never drop a hit as a "benign mention", a
   record, or a post-award consequence. Use at most two search_document calls;
   use the second only when a domain boundary is still unclear.

SAVE:
5. Build the source-backed map and call save_document_map. If a tool reports
   its budget is exhausted, save what you have immediately.
"""
            + _document_map_contract_prompt(run, self.document)
        )
        system_prompt_path = run_dir / "system-prompt.md"
        system_prompt_path.write_text(system_prompt, encoding="utf-8")
        return DocumentMapAgentTask(
            run=run,
            document=self.document,
            server=server,
            config=self.config,
            skill_dir=skill_dir,
            mcp_config_path=mcp_config_path,
            bootstrap_path=bootstrap_path,
            system_prompt_path=system_prompt_path,
            run_dir=run_dir,
            system_prompt=system_prompt,
            user_prompt=_user_prompt(run, self.document),
        )

    @staticmethod
    def _execute(
        executor: DocumentMapExecutor | Callable[..., Mapping[str, Any] | None],
        task: DocumentMapAgentTask,
    ) -> Mapping[str, Any] | None:
        method = getattr(executor, "execute", None)
        if callable(method):
            return method(task)
        if callable(executor):
            return executor(task)
        raise TypeError("DocumentMap executor must be callable or expose execute().")

    def _result(
        self,
        *,
        status: str,
        run: AgentRun,
        document_map: DocumentMap | None,
        tool_calls: list[ToolCall],
        unresolved_issues: list[dict[str, Any]],
        candidate_findings: list[dict[str, Any]],
        checkpoints: list[AgentCheckpoint],
        validation: DocumentMapValidation | None,
        warnings: list[str],
        errors: list[str],
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        final_status = (
            "needs_review"
            if status == "completed"
            and (
                unresolved_issues
                or any(item.needs_human_review for item in (document_map.sections if document_map else []))
            )
            else status
        )
        final_run_status = (
            "completed"
            if final_status == "completed"
            else "needs_review"
            if final_status == "needs_review"
            else "failed"
        )
        final_run = run.model_copy(
            update={
                "status": final_run_status,
                "phase": "validation" if final_status != "failed" else "document_map",
                "turn_count": int(run.turn_count),
                "tool_call_count": len(tool_calls),
                "updated_at": _now_iso(),
                "finished_at": _now_iso(),
                "checkpoint_id": checkpoints[-1].checkpoint_id if checkpoints else "",
                "checkpoints": checkpoints,
                "warnings": list(dict.fromkeys([*run.warnings, *warnings])),
                "errors": list(dict.fromkeys([*run.errors, *errors])),
                "failure_reason": errors[0] if errors else "",
            }
        )
        payload = DocumentMapRunResult(
            status=final_status,
            run=final_run.model_dump(mode="json", by_alias=True),
            documentMap=(
                document_map.model_dump(mode="json", by_alias=True)
                if document_map
                else None
            ),
            toolCalls=[
                item.model_dump(mode="json", by_alias=True)
                for item in tool_calls
            ],
            unresolvedIssues=unresolved_issues,
            candidateFindings=candidate_findings,
            checkpoints=[
                item.model_dump(mode="json", by_alias=True)
                for item in checkpoints
            ],
            validation=(
                validation.model_dump(mode="json", by_alias=True)
                if validation
                else {}
            ),
            warnings=list(dict.fromkeys(warnings)),
            errors=list(dict.fromkeys(errors)),
            metadata=dict(metadata or {}),
        )
        return payload.model_dump(mode="json", by_alias=True)

    def run_once(self) -> dict[str, Any]:
        run = self.agent_run.model_copy(
            update={"status": "running", "updated_at": _now_iso()}
        )
        server = self._new_server(run)
        checkpoints = [
            AgentCheckpoint(
                checkpointId=f"{run.run_id}-planning",
                runId=run.run_id,
                phase="document_map",
                summary="Runtime prepared the Skill, document identity and strict MCP boundary.",
                createdAt=_now_iso(),
            )
        ]
        warnings: list[str] = []
        errors: list[str] = []
        try:
            task = self._prepare_task(run, server)
            raw = self._execute(self.executor, task)
            envelope = AgentExecutionEnvelope.model_validate(raw or {})
        except Exception as error:
            return self._result(
                status="failed",
                run=run,
                document_map=None,
                tool_calls=server.tool_calls,
                unresolved_issues=[],
                candidate_findings=[],
                checkpoints=checkpoints,
                validation=None,
                warnings=[],
                errors=[str(error)],
                metadata={"fallbackMode": self.config.fallback_mode},
            )

        if envelope.document_map is not None:
            save_result = server.call_tool(
                "save_document_map",
                {
                    "documentId": self.document.document_id,
                    "documentVersionId": self.document.document_version_id,
                    "documentMap": envelope.document_map,
                },
            )
            if "error" in save_result:
                error_payload = save_result.get("error") or {}
                detail_payload = error_payload.get("details") or {}
                detail_message = (
                    detail_payload.get("message")
                    if isinstance(detail_payload, Mapping)
                    else ""
                )
                issue_details: list[str] = []
                if isinstance(detail_payload, Mapping):
                    issues = detail_payload.get("issues") or []
                    if isinstance(issues, list):
                        for issue in issues:
                            if not isinstance(issue, Mapping):
                                continue
                            code = str(issue.get("code") or "").strip()
                            message = str(issue.get("message") or "").strip()
                            source_ids = issue.get("sourceIds") or []
                            source_text = (
                                ",".join(str(value) for value in source_ids)
                                if isinstance(source_ids, list)
                                else str(source_ids)
                            )
                            detail = ": ".join(
                                part
                                for part in (code, message)
                                if part
                            )
                            if source_text:
                                detail = f"{detail} [{source_text}]"
                            if detail:
                                issue_details.append(detail)
                errors.append(
                    " ".join(
                        part
                        for part in (
                            str(error_payload.get("message") or "DocumentMap save failed."),
                            str(detail_message or ""),
                            *issue_details,
                        )
                        if part
                    )
                )

        document_map = next(reversed(server.document_maps.values()), None)
        if document_map is None:
            errors.append("No DocumentMap was saved by the WorkBuddy agent.")

        tool_calls = list(server.tool_calls)
        remote_tool_calls: list[ToolCall] = []
        for item in envelope.tool_calls:
            try:
                remote_tool_calls.append(ToolCall.model_validate(item))
            except Exception:
                warnings.append("One external MCP audit record could not be normalized.")
        if remote_tool_calls:
            tool_calls = [*remote_tool_calls, *tool_calls]
        if len(tool_calls) > self.config.max_tool_calls:
            errors.append(
                f"Agent tool-call limit exceeded: {len(tool_calls)} > {self.config.max_tool_calls}."
            )
        if envelope.turn_count > self.config.max_turns:
            errors.append(
                f"Agent turn limit exceeded: {envelope.turn_count} > {self.config.max_turns}."
            )

        unresolved = list(envelope.unresolved_issues)
        findings = list(envelope.candidate_findings)
        if document_map is not None:
            high_risk_ids = (
                _high_risk_segment_ids(self.document)
                if self.config.require_rejection_scan
                else []
            )
            validation = validate_document_map(
                document_map,
                sections=self.document.sections,
                segments=self.document.segments or self.document.units,
                tables=self.document.tables,
                document_id=self.document.document_id,
                document_version_id=self.document.document_version_id,
                high_risk_segment_ids=high_risk_ids,
                require_active_source=self.config.require_map_validation,
            )
            if self.config.require_map_validation and not validation.valid:
                errors.extend(item.message for item in validation.issues if item.severity == "critical")
            warnings.extend(validation.warnings)
            checkpoints.append(
                AgentCheckpoint(
                    checkpointId=f"{run.run_id}-validated",
                    runId=run.run_id,
                    phase="validation",
                    artifactIds=[document_map.map_id],
                    summary="DocumentMap passed the runtime validation gate.",
                    createdAt=_now_iso(),
                )
            )
        else:
            validation = None

        warnings.extend(envelope.warnings)
        errors.extend(envelope.errors)
        status = "failed" if errors else "completed"
        return self._result(
            status=status,
            run=run.model_copy(update={"turn_count": envelope.turn_count}),
            document_map=document_map,
            tool_calls=tool_calls,
            unresolved_issues=unresolved,
            candidate_findings=findings,
            checkpoints=checkpoints,
            validation=validation,
            warnings=warnings,
            errors=list(dict.fromkeys(errors)),
            metadata={
                "runtimeVersion": AGENT_RUNTIME_VERSION,
                "executor": "codebuddy_agent_sdk",
                "skillDir": self.config.skill_dir,
                "mcpConfigPath": self.config.mcp_config_path,
                "fallbackMode": self.config.fallback_mode,
            },
        )

    def run(self) -> dict[str, Any]:
        """Run with bounded retries; each retry gets a fresh MCP server."""
        last: dict[str, Any] | None = None
        for attempt in range(self.config.max_retries + 1):
            last = self.run_once()
            if last.get("status") in {"completed", "needs_review"}:
                last.setdefault("metadata", {})["attempt"] = attempt + 1
                return last
        assert last is not None
        last.setdefault("metadata", {})["attempt"] = self.config.max_retries + 1
        return last


__all__ = [
    "AGENT_RUNTIME_VERSION",
    "AgentExecutionEnvelope",
    "DocumentMapAgentRuntime",
    "DocumentMapAgentTask",
    "DocumentMapExecutor",
    "DocumentMapRunResult",
    "DocumentMapRuntimeConfig",
    "WorkBuddySdkExecutor",
]
