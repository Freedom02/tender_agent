# -*- coding: utf-8 -*-
"""Pi 传输层：进程启动、RPC 事件流、超时与 JSON 解析。

由原 `client.py` 机械拆分而来（Wave 0 · 0.3），代码一字未改。
本模块**不含任何 agent 级方法**；那些在 `agents.py`，二者由 `client.PiClient` 组合。

Wave 1 · A 在 `run_json` 这一层接上了两件「只记录、不阻断」的事：
  1. 产物容错校验（`agent_runtime.validation`）：智能体返回不符合示例 schema 时，
     写一条人读告警进 `warnings`、并把 `validation` 段附进返回结构；
  2. 运行记账（`agent_runtime.run_state`）：先在 plan_outline 一条链路上记
     `begin_node` / `mark_succeeded` / `mark_failed(FailureKind)`，只附进返回结构
     （`runState` 段），**不落盘**。

硬约束（有测试守着）：
  - 原实现整体保留在 `_run_json_raw` 里，argv、超时、异常类型一字未改；
  - `_command()` 的签名与 argv 语义不受影响（校验接线不碰启动参数）；
  - 校验失败**不抛异常**，不新增任何默认阻断分支。
"""
from __future__ import annotations

import json
import hashlib
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from ..agent_runtime.run_state import (
    ArtifactStatus,
    FailureKind,
    NodeRun,
    NodeStatus,
    RunState,
    input_digest,
)
from ..agent_runtime.specs import resolve_spec
from ..agent_runtime.validation import (
    VALIDATION_SECTION,
    shape_for_agent,
    validate_payload,
    validation_section,
    validation_warnings,
)
from .config import PiConfig


class PiError(RuntimeError):
    """A safe, user-facing error from the local Pi adapter.

    ``failure_kind``/``retryable`` are optional metadata for callers that can
    make a fallback decision.  The legacy string exception contract remains
    unchanged for ordinary transport errors.
    """

    def __init__(
        self,
        message: str,
        *,
        failure_kind: FailureKind | str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(str(message))
        self.failure_kind = failure_kind
        self.retryable = retryable


_POLICY_MARKERS = re.compile(
    r"(?:content[\s_-]*(?:safety|policy|moderation)|safety[\s_-]*policy|"
    r"(?:content|safety)[\s_-]*(?:filter|blocked?|rejected?|refus(?:ed|al)?|violation)|"
    r"policy[\s_-]*(?:blocked?|rejected?|refus(?:ed|al)?|violation|disallow|denied?|error)|"
    r"prompt[\s_-]*(?:blocked?|rejected?|refus(?:ed|al)?)|"
    r"input[\s_-]*(?:blocked?|rejected?|refus(?:ed|al)?|policy|safety|not[\s_-]*allowed)|"
    r"content[\s_-]*(?:blocked?|rejected?|refus(?:ed|al)?)|"
    r"guardrail|moderation|"
    r"内容(?:安全|审核|策略|违规|拦截|拒绝)|"
    r"输入(?:安全|策略|违规|拦截|拒绝)|"
    r"请求(?:被)?(?:安全|策略|内容).*?(?:拦截|拒绝|阻止)|"
    r"(?:内容安全|输入策略|安全策略|内容审核).*?(?:拦截|拒绝|阻止)|"
    r"policy[\s_-]*blocked?)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)\b)"
    r"\s*[:=]\s*[^\s,;]+"
)
_JSON_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|auth[_-]?token|secret|password)[\"']?"
    r"\s*[:=]\s*)[\"']?[^\s,;}\"']+[\"']?"
)
_BEARER_SECRET = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_KEY_LIKE = re.compile(r"\b(?:sk|pk)-[A-Za-z0-9_-]{8,}\b")
_SENSITIVE_WORDS = re.compile(
    r"(?i)(?:prompt|input|document|tender|content|text|message|招标|投标|原文|条款|要求)"
)


def _diagnostic_text(value: Any) -> str:
    """Extract a small diagnostic surface without serializing provider payloads."""

    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("code", "type", "status", "reason", "error", "message"):
            item = value.get(key)
            if item not in (None, ""):
                item_text = _diagnostic_text(item) if isinstance(item, (dict, list, tuple)) else str(item)
                if item_text:
                    parts.append(f"{key}={item_text}")
        return "; ".join(parts)
    if isinstance(value, (list, tuple)):
        return "; ".join(_diagnostic_text(item) for item in value[:3])
    return str(value or "")


def _redact_diagnostic(value: Any, *, policy: bool = False) -> str:
    """Return a bounded diagnostic; never retain prompt/key material in errors."""

    raw = _diagnostic_text(value).strip()
    if not raw:
        return ""
    if policy or _POLICY_MARKERS.search(raw):
        code_match = re.search(r"(?i)\b(?:code|type|reason)=([A-Za-z0-9_.-]{2,80})", raw)
        suffix = f"（code={code_match.group(1)}）" if code_match else ""
        return f"provider policy blocked request{suffix}"
    redacted = _JSON_SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", raw)
    redacted = _SECRET_ASSIGNMENT.sub(r"\1=[REDACTED]", redacted)
    redacted = _BEARER_SECRET.sub("Bearer [REDACTED]", redacted)
    redacted = _KEY_LIKE.sub("[REDACTED_KEY]", redacted)
    redacted = re.sub(r"\s+", " ", redacted).strip()
    if not redacted:
        return ""
    if len(redacted) > 160 or _SENSITIVE_WORDS.search(redacted) or "\n" in raw:
        digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:10]
        return f"provider diagnostic redacted (sha256:{digest})"
    return redacted[:160]


class PolicyBlockedError(PiError):
    """Non-retryable provider/input policy rejection.

    The distinct class name is intentional: legacy orchestrators retry only
    exceptions whose exact class name is ``PiError``.  Policy rejection must
    reach their fallback path without a pointless second request.
    """

    def __init__(self, detail: Any = "") -> None:
        safe_detail = _redact_diagnostic(detail, policy=True)
        message = "Pi 请求被输入策略拦截（policy_blocked）"
        if safe_detail:
            message = f"{message}：{safe_detail}"
        super().__init__(
            message,
            failure_kind=FailureKind.POLICY_BLOCKED,
            retryable=False,
        )


def _clip(value: Any, limit: int = 120000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    head = int(limit * 0.72)
    tail = limit - head
    return f"{text[:head]}\n...[中间内容已截断]...\n{text[-tail:]}"


def parse_json_text(value: str) -> dict[str, Any]:
    """Parse JSON returned by an LLM, tolerating a code fence or short prose."""
    text = str(value or "").strip()
    if not text:
        raise PiError("Pi 没有返回文本")
    text = text.lstrip("\ufeff")
    text = re.sub(r"^\s*```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\s*$", "", text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None
        decoder = json.JSONDecoder()
        match = re.search(r"\{", text)
        if match:
            try:
                candidate, _ = decoder.raw_decode(text[match.start() :])
            except json.JSONDecodeError:
                candidate = None
            if isinstance(candidate, dict):
                parsed = candidate
        if parsed is None:
            if _POLICY_MARKERS.search(text):
                raise PolicyBlockedError(text)
            preview = _redact_diagnostic(text)
            suffix = f"：{preview}" if preview else ""
            raise PiError(f"Pi 返回内容不是合法 JSON{suffix}") from None
    if not isinstance(parsed, dict):
        raise PiError("Pi 返回的 JSON 根节点不是对象")
    return parsed


def _extract_text_from_message(message: Any) -> str:
    if not isinstance(message, dict):
        return ""
    # RPC 会同时发送 user 和 assistant 的 message_end。只有 assistant
    # 消息才是模型输出，不能把用户提示词当作答案再次解析。
    if str(message.get("role") or "").strip().lower() != "assistant":
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "".join(parts)


# ── Wave 1 · A：校验与记账接线的辅助（都不改变原有行为）──────────────────

#: 关掉校验接线（排查/对照用）：`PI_AGENT_VALIDATION=0|false|off|no`
VALIDATION_ENV = "PI_AGENT_VALIDATION"

#: 返回结构里承载运行记账的那一段（同样避开 result/data/output 这些外壳名）
RUN_STATE_SECTION = "runState"

_FALSY = {"0", "false", "off", "no"}

#: 各智能体提示词都以「你是 XxxAgent，……」开头，用它把返回归因到具体智能体，
#: 从而不必改动编排层就能在真实调用点上接校验。
_AGENT_DECLARATION = re.compile(r"你是\s*([A-Za-z][A-Za-z0-9]*Agent)")

#: 先只在 plan_outline 一条链路上做运行记账（架构改造方案 §2-A 第 3 条）。
#: 这条链路的真实入口是 `store.py` 的 `PiClient().plan_outline(...)`：
#: VolumePlanAgent → OutlinePlanAgent（目录质量不过关时还有一次「质量修正」重试），
#: 一个 PiClient 实例只跑这一条链路，所以运行态挂在实例上。
TRACKED_CHAIN_AGENTS: tuple[str, ...] = ("VolumePlanAgent", "OutlinePlanAgent")


def validation_enabled(explicit: bool | None = None) -> bool:
    """校验接线是否生效：显式参数优先，其次看 `PI_AGENT_VALIDATION`。"""

    if explicit is not None:
        return bool(explicit)
    return str(os.getenv(VALIDATION_ENV, "")).strip().lower() not in _FALSY


def agent_id_from_prompt(prompt: str) -> str:
    """从提示词的「你是 XxxAgent」声明解析出已登记的 agent_id（解析不到返回空串）。

    解析不到、或该名字未在 `specs.AGENT_SPECS` 登记时一律返回空串，
    调用方据此跳过校验——宁可少校验，也不制造假问题。
    """

    match = _AGENT_DECLARATION.search(str(prompt or ""))
    if not match:
        return ""
    agent_id = match.group(1)
    return agent_id if resolve_spec(agent_id) is not None else ""


def classify_failure(exc: BaseException) -> FailureKind:
    """把传输层异常归类成 `FailureKind`（只用于记账，异常本身原样抛出）。"""

    declared = getattr(exc, "failure_kind", None)
    if declared:
        try:
            return declared if isinstance(declared, FailureKind) else FailureKind(str(declared))
        except ValueError:
            pass
    text = str(exc or "")
    if _POLICY_MARKERS.search(text):
        return FailureKind.POLICY_BLOCKED
    if isinstance(exc, subprocess.TimeoutExpired) or "超时" in text:
        return FailureKind.TIMEOUT
    if "不是合法 JSON" in text or "根节点不是对象" in text:
        return FailureKind.SCHEMA_INVALID
    if "没有返回文本" in text or "执行失败" in text or "已退出" in text or "启动 Pi 失败" in text:
        return FailureKind.PROVIDER_ERROR
    return FailureKind.UNKNOWN


def artifact_status_for(payload: Any, report: Any = None) -> ArtifactStatus:
    """按「产物事实」给节点定产物状态：执行成功 ≠ 产物完整。"""

    if report is not None and report.issues:
        return ArtifactStatus.PARTIAL
    if not payload:
        return ArtifactStatus.MISSING
    return ArtifactStatus.COMPLETE


class PiTransport:
    """与 Pi 进程通信的传输能力（原 PiClient 的传输部分）。"""

    def __init__(self, config: PiConfig | None = None) -> None:
        self.config = config or PiConfig.from_env()
        #: plan_outline 链路的运行态（Wave 1 只挂在实例上、只进返回结构，不落盘）
        self._run_state: RunState | None = None
        self._run_state_lock = threading.Lock()


    def _resolve_command(self) -> str:
        if not self.config.enabled:
            raise PiError("Pi 智能体未启用，请配置 PI_ENABLED=true")
        command = self.config.command
        command_path = Path(command)
        if command_path.is_absolute():
            return command
        try:
            if command_path.is_file():
                return command
        except OSError:
            return command
        resolved = shutil.which(command) or shutil.which(f"{command}.cmd")
        if not resolved:
            raise PiError(
                f"未找到 Pi 命令：{command}。请先安装 "
                "@earendil-works/pi-coding-agent 并确认 pi 在 PATH 中。"
            )
        return resolved


    def _command(
        self,
        *,
        tools: str = "",
        extensions: tuple[str, ...] = (),
    ) -> list[str]:
        """组装 Pi 启动参数。

        默认（``tools`` 为空）沿用 ``--no-tools``：解析类等不需要工具的智能体保持最小能力面。
        传入 ``tools`` 时改为「只放行白名单工具」：``--no-builtin-tools`` 关掉内置的
        read/bash/write/edit/powershell，再用 ``--tools`` 精确放行扩展注册的只读工具。
        """

        resolved = self._resolve_command()
        command_path = Path(resolved)
        launch_args = [
            "--mode",
            "rpc",
            "--no-session",
        ]
        if tools:
            launch_args.extend(["--no-builtin-tools", "--tools", tools])
        else:
            launch_args.append("--no-tools")
        for extension in extensions:
            launch_args.extend(["--extension", str(extension)])
        if self.config.provider:
            launch_args.extend(["--provider", self.config.provider])
        if self.config.model:
            launch_args.extend(["--model", self.config.model])
        if self.config.api_key and self.config.provider != "company-ai":
            launch_args.extend(["--api-key", self.config.api_key])

        # Windows does not allow CreateProcess to execute .cmd/.bat/.ps1
        # files directly. PowerShell resolves these shims for an interactive
        # user, but the backend subprocess must provide the launcher itself.
        suffix = command_path.suffix.lower()
        if suffix in {".cmd", ".bat"}:
            return [
                os.environ.get("COMSPEC", "cmd.exe"),
                "/d",
                "/c",
                resolved,
                *launch_args,
            ]
        if suffix == ".ps1":
            return [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                resolved,
                *launch_args,
            ]
        return [resolved, *launch_args]


    def run_json(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
        tools: str = "",
        extensions: tuple[str, ...] = (),
        env_extra: dict[str, str] | None = None,
        agent_id: str = "",
        validate: bool | None = None,
    ) -> dict[str, Any]:
        """调用 Pi 并解析 JSON；返回前补上「只记录不阻断」的产物校验与运行记账。

        `agent_id` 不传时从提示词的「你是 XxxAgent」声明推断（各智能体提示词都这么写），
        因此编排层不改一行也能在真实调用点上接上校验；推断不到就不校验。
        返回结构与 `_run_json_raw` 相比**只多不少**：多出 `validation` 段、
        plan_outline 链路上的 `runState` 段，以及（有问题时）追加进 `warnings` 的人读告警。
        失败时异常类型、消息、argv 与改造前完全一致，只是多记一条失败节点。
        """

        resolved_agent = str(agent_id or "") or agent_id_from_prompt(prompt)
        node = self._begin_tracked_node(resolved_agent, prompt=prompt)
        try:
            payload = self._run_json_raw(
                prompt,
                timeout_seconds=timeout_seconds,
                tools=tools,
                extensions=extensions,
                env_extra=env_extra,
            )
        except BaseException as exc:  # noqa: BLE001 - 只记一笔账，异常原样抛出
            self._mark_tracked_failure(node, exc)
            raise
        if not isinstance(payload, dict):
            self._mark_tracked_success(node, payload, None)
            return payload
        report = self._attach_validation(payload, resolved_agent) if validation_enabled(validate) else None
        self._mark_tracked_success(node, payload, report)
        return payload

    def _attach_validation(self, payload: dict[str, Any], agent_id: str):
        """把一个智能体的返回接到形态表上：记录问题，绝不抛异常、绝不改分支。"""

        if not agent_id:
            return None
        shape = shape_for_agent(agent_id)
        if shape is None:
            return None
        report = validate_payload(payload, shape)
        payload[VALIDATION_SECTION] = validation_section(agent_id, report)
        messages = validation_warnings(agent_id, report)
        if not messages:
            return report
        existing = payload.get("warnings")
        if isinstance(existing, list):
            payload["warnings"] = [*existing, *messages]
        elif "warnings" not in payload:
            # 模型没给 warnings 时补一个，好让编排层把它记进 agentResult.warnings
            payload["warnings"] = list(messages)
        return report

    # ── Wave 1 · A：运行记账（只在返回结构里，不落盘）──────────────────

    def begin_run(
        self,
        *,
        project_id: str = "",
        workspace_id: str = "",
        stage: str = "",
    ) -> RunState:
        """显式开一次运行（Wave 2 的编排层用它注入身份）。

        不调用也可以：第一条被记账的调用会隐式建一个空身份的运行态。
        """

        with self._run_state_lock:
            self._run_state = RunState(
                projectId=str(project_id or ""),
                workspaceId=str(workspace_id or ""),
                stage=str(stage or ""),
            )
            return self._run_state

    @property
    def current_run_state(self) -> RunState | None:
        """当前运行态快照（只读；Wave 1 不落盘、不进 store）。"""

        return self._run_state

    def _begin_tracked_node(self, agent_id: str, *, prompt: str) -> NodeRun | None:
        """只对 plan_outline 链路记账：其它智能体暂不记（先试一条链路）。"""

        if agent_id not in TRACKED_CHAIN_AGENTS:
            return None
        spec = resolve_spec(agent_id)
        with self._run_state_lock:
            if self._run_state is None:
                self._run_state = RunState(stage=spec.stage if spec is not None else "")
            state = self._run_state
            # 有节点在跑，运行态就不能停在 pending（快照要能自证）
            state.status = NodeStatus.RUNNING
        return state.begin_node(
            node_id=agent_id,
            agent_id=agent_id,
            stage=spec.stage if spec is not None else "",
            # 传输层只拿得到提示词，故输入摘要用提示词摘要近似（Wave 2 由编排层
            # 传入 ParseBundle 级摘要，那时幂等键才真正可用）。
            input_digest=input_digest(prompt),
        )

    def _mark_tracked_failure(self, node: NodeRun | None, exc: BaseException) -> None:
        if node is None:
            return
        node.mark_failed(
            classify_failure(exc),
            _redact_diagnostic(str(exc)),
            artifact_status=ArtifactStatus.MISSING,
        )

    def _mark_tracked_success(
        self,
        node: NodeRun | None,
        payload: Any,
        report: Any,
    ) -> None:
        if node is None:
            return
        node.mark_succeeded(artifact_status=artifact_status_for(payload, report))
        if isinstance(payload, dict):
            payload[RUN_STATE_SECTION] = self.run_state_section(node)

    def run_state_section(self, node: NodeRun) -> dict[str, Any]:
        """返回结构里的 `runState` 段（JSON 可序列化；`persisted` 明确写 false）。"""

        state = self._run_state
        summary = state.to_summary() if state is not None else {}
        return {
            "runId": str(summary.get("runId") or ""),
            "node": node.model_dump(mode="json"),
            "run": summary,
            # 只是当下的快照：链路还没走完时 status 仍是 running/pending
            "persisted": False,
        }

    def _run_json_raw(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
        tools: str = "",
        extensions: tuple[str, ...] = (),
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Wave 0 · 0.3 的原始实现（进程启动、RPC、超时、JSON 解析），一字未改。"""

        runtime_env = {
            **os.environ,
            "PI_OFFLINE": os.getenv("PI_OFFLINE", "0"),
        }
        if env_extra:
            runtime_env.update({str(key): str(value) for key, value in env_extra.items() if value})
        if self.config.provider == "company-ai" and self.config.api_key:
            # ~/.pi/agent/models.json defines company-ai's apiKey as
            # $COMPANY_LLM_API_KEY. Keep the secret out of the prompt and
            # provide it only to the child process.
            runtime_env["COMPANY_LLM_API_KEY"] = self.config.api_key
        try:
            process = subprocess.Popen(
                self._command(tools=tools, extensions=extensions),
                cwd=self.config.working_dir or None,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=runtime_env,
            )
        except OSError as exc:
            raise PiError(f"启动 Pi 失败：{exc}") from exc

        events: queue.Queue[tuple[str, str]] = queue.Queue()

        def read_stream(stream: Any, kind: str) -> None:
            try:
                for line in stream:
                    events.put((kind, line.rstrip("\r\n")))
            finally:
                events.put((f"{kind}_eof", ""))

        stdout_thread = threading.Thread(
            target=read_stream,
            args=(process.stdout, "stdout"),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=read_stream,
            args=(process.stderr, "stderr"),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()

        def finish_after_failure() -> tuple[int | None, list[str]]:
            """Stop a failed child and collect the diagnostic it already emitted."""
            if process.poll() is None:
                try:
                    process.kill()
                except OSError:
                    pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except OSError:
                    pass
                process.wait(timeout=3)
            stderr_thread.join(timeout=1)
            stdout_thread.join(timeout=1)
            diagnostics: list[str] = []
            while True:
                try:
                    kind, line = events.get_nowait()
                except queue.Empty:
                    break
                if kind == "stderr" and line.strip():
                    diagnostics.append(_redact_diagnostic(line.strip()))
            return process.returncode, diagnostics

        try:
            assert process.stdin is not None
            if process.poll() is not None:
                return_code, diagnostics = finish_after_failure()
                detail = "；".join(dict.fromkeys(diagnostics[-5:]))
                detail = detail or "子进程在发送请求前已经退出"
                if _POLICY_MARKERS.search(detail):
                    raise PolicyBlockedError(detail)
                raise PiError(
                    f"无法向 Pi 发送请求：Pi 子进程已退出(code={return_code})：{detail}"
                )
            process.stdin.write(
                json.dumps(
                    {"id": "tender-agent-request", "type": "prompt", "message": prompt},
                    ensure_ascii=False,
                )
                + "\n"
            )
            process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            return_code, diagnostics = finish_after_failure()
            detail = "；".join(dict.fromkeys(diagnostics[-5:]))
            suffix = f"；Pi 子进程退出码={return_code}"
            if detail:
                suffix += f"：{detail}"
            if _POLICY_MARKERS.search(detail):
                raise PolicyBlockedError(detail) from exc
            raise PiError(f"无法向 Pi 发送请求：{exc}{suffix}") from exc

        text_parts: list[str] = []
        message_text_parts: list[str] = []
        errors: list[str] = []
        assistant_errors: list[str] = []
        policy_blocked = False
        settled = False
        effective_timeout = self.config.timeout_seconds
        if timeout_seconds is not None:
            try:
                effective_timeout = max(float(timeout_seconds), 1.0)
            except (TypeError, ValueError):
                effective_timeout = self.config.timeout_seconds
        deadline = time.monotonic() + effective_timeout
        while time.monotonic() < deadline:
            try:
                kind, line = events.get(timeout=0.2)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if kind == "stderr" and line.strip():
                safe_line = _redact_diagnostic(line.strip())
                policy_blocked = policy_blocked or bool(_POLICY_MARKERS.search(line))
                if safe_line:
                    errors.append(safe_line)
                continue
            if kind != "stdout" or not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "response" and event.get("command") == "prompt" and not event.get("success"):
                raw_error = event.get("error") or "Pi 拒绝了提示词"
                policy_blocked = policy_blocked or bool(_POLICY_MARKERS.search(_diagnostic_text(raw_error)))
                errors.append(_redact_diagnostic(raw_error))
                settled = True
                break
            if event_type == "message_update":
                delta = event.get("assistantMessageEvent")
                if isinstance(delta, dict) and delta.get("type") == "text_delta":
                    text_parts.append(str(delta.get("delta") or ""))
            elif event_type == "message_end":
                message = event.get("message")
                message_text = _extract_text_from_message(message)
                if message_text:
                    message_text_parts.append(message_text)
                if isinstance(message, dict):
                    error_message = str(message.get("errorMessage") or "").strip()
                    if error_message and str(message.get("role") or "").lower() == "assistant":
                        policy_blocked = policy_blocked or bool(_POLICY_MARKERS.search(error_message))
                        assistant_errors.append(_redact_diagnostic(error_message))
            elif event_type == "turn_end":
                message = event.get("message")
                if isinstance(message, dict):
                    error_message = str(message.get("errorMessage") or "").strip()
                    if error_message and str(message.get("role") or "").lower() == "assistant":
                        policy_blocked = policy_blocked or bool(_POLICY_MARKERS.search(error_message))
                        assistant_errors.append(_redact_diagnostic(error_message))
            elif event_type == "agent_end":
                for message in event.get("messages") or []:
                    if not isinstance(message, dict):
                        continue
                    error_message = str(message.get("errorMessage") or "").strip()
                    if error_message and str(message.get("role") or "").lower() == "assistant":
                        policy_blocked = policy_blocked or bool(_POLICY_MARKERS.search(error_message))
                        assistant_errors.append(_redact_diagnostic(error_message))
                if event.get("willRetry") is False:
                    settled = True
                    break
            elif event_type == "agent_settled":
                settled = True
                break

        if not settled and time.monotonic() >= deadline:
            process.kill()
            raise PiError(
                f"Pi 智能体执行超时（provider={self.config.provider or 'default'}，"
                f"model={self.config.model or 'default'}）"
            )

        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
        if not text_parts and message_text_parts:
            text_parts = message_text_parts
        text = "".join(text_parts).strip()
        if not text:
            detail = "；".join(dict.fromkeys(errors[-3:] + assistant_errors[-3:]))
            detail = detail or "没有返回 assistant 文本"
            if policy_blocked:
                raise PolicyBlockedError(detail)
            raise PiError(
                f"Pi 执行失败（provider={self.config.provider or 'default'}，"
                f"model={self.config.model or 'default'}）：{detail}"
            )
        return parse_json_text(text)
