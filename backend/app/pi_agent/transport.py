# -*- coding: utf-8 -*-
"""Pi 传输层：进程启动、RPC 事件流、超时与 JSON 解析。

由原 `client.py` 机械拆分而来（Wave 0 · 0.3），代码一字未改。
本模块**不含任何 agent 级方法**；那些在 `agents.py`，二者由 `client.PiClient` 组合。

Wave 1 · A 在 `run_json` 这一层接上了「产物容错校验」（`agent_runtime.validation`）：
智能体返回不符合示例 schema 时，写一条人读告警进 `warnings`、并把 `validation`
段附进返回结构。**只记录、不阻断。**

硬约束（有测试守着）：
  - 原实现整体保留在 `_run_json_raw` 里，argv、超时、异常类型一字未改；
  - `_command()` 的签名与 argv 语义不受影响（校验接线不碰启动参数）；
  - 校验失败**不抛异常**，不新增任何默认阻断分支。
"""
from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
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
    """A safe, user-facing error from the local Pi adapter."""


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
            preview = re.sub(r"\s+", " ", text)[:240]
            raise PiError(f"Pi 返回内容不是合法 JSON：{preview}") from None
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


# ── Wave 1 · A：校验接线的辅助（不改变原有行为）──────────────────────────

#: 关掉校验接线（排查/对照用）：`PI_AGENT_VALIDATION=0|false|off|no`
VALIDATION_ENV = "PI_AGENT_VALIDATION"

_FALSY = {"0", "false", "off", "no"}

#: 各智能体提示词都以「你是 XxxAgent，……」开头，用它把返回归因到具体智能体，
#: 从而不必改动编排层就能在真实调用点上接校验。
_AGENT_DECLARATION = re.compile(r"你是\s*([A-Za-z][A-Za-z0-9]*Agent)")


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


class PiTransport:
    """与 Pi 进程通信的传输能力（原 PiClient 的传输部分）。"""

    def __init__(self, config: PiConfig | None = None) -> None:
        self.config = config or PiConfig.from_env()


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
        """调用 Pi 并解析 JSON；返回前补上「只记录不阻断」的产物校验。

        `agent_id` 不传时从提示词的「你是 XxxAgent」声明推断（各智能体提示词都这么写），
        因此编排层不改一行也能在真实调用点上接上校验；推断不到就不校验。
        返回结构与 `_run_json_raw` 相比**只多不少**：多出 `validation` 段，
        以及（有问题时）人读告警追加进 `warnings`。
        """

        payload = self._run_json_raw(
            prompt,
            timeout_seconds=timeout_seconds,
            tools=tools,
            extensions=extensions,
            env_extra=env_extra,
        )
        if not isinstance(payload, dict) or not validation_enabled(validate):
            return payload
        resolved_agent = str(agent_id or "") or agent_id_from_prompt(prompt)
        self._attach_validation(payload, resolved_agent)
        return payload

    def _attach_validation(self, payload: dict[str, Any], agent_id: str) -> None:
        """把一个智能体的返回接到形态表上：记录问题，绝不抛异常、绝不改分支。"""

        if not agent_id:
            return
        shape = shape_for_agent(agent_id)
        if shape is None:
            return
        report = validate_payload(payload, shape)
        payload[VALIDATION_SECTION] = validation_section(agent_id, report)
        messages = validation_warnings(agent_id, report)
        if not messages:
            return
        existing = payload.get("warnings")
        if isinstance(existing, list):
            payload["warnings"] = [*existing, *messages]
        elif "warnings" not in payload:
            # 模型没给 warnings 时补一个，好让编排层把它记进 agentResult.warnings
            payload["warnings"] = list(messages)

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
                    diagnostics.append(line.strip())
            return process.returncode, diagnostics

        try:
            assert process.stdin is not None
            if process.poll() is not None:
                return_code, diagnostics = finish_after_failure()
                detail = "；".join(dict.fromkeys(diagnostics[-5:]))
                detail = detail or "子进程在发送请求前已经退出"
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
            raise PiError(f"无法向 Pi 发送请求：{exc}{suffix}") from exc

        text_parts: list[str] = []
        message_text_parts: list[str] = []
        errors: list[str] = []
        assistant_errors: list[str] = []
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
                errors.append(line.strip())
                continue
            if kind != "stdout" or not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            event_type = event.get("type")
            if event_type == "response" and event.get("command") == "prompt" and not event.get("success"):
                errors.append(str(event.get("error") or "Pi 拒绝了提示词"))
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
                        assistant_errors.append(error_message)
            elif event_type == "turn_end":
                message = event.get("message")
                if isinstance(message, dict):
                    error_message = str(message.get("errorMessage") or "").strip()
                    if error_message and str(message.get("role") or "").lower() == "assistant":
                        assistant_errors.append(error_message)
            elif event_type == "agent_end":
                for message in event.get("messages") or []:
                    if not isinstance(message, dict):
                        continue
                    error_message = str(message.get("errorMessage") or "").strip()
                    if error_message and str(message.get("role") or "").lower() == "assistant":
                        assistant_errors.append(error_message)
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
            raise PiError(
                f"Pi 执行失败（provider={self.config.provider or 'default'}，"
                f"model={self.config.model or 'default'}）：{detail}"
            )
        return parse_json_text(text)
