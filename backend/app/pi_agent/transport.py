# -*- coding: utf-8 -*-
"""Pi 传输层：进程启动、RPC 事件流、超时与 JSON 解析。

由原 `client.py` 机械拆分而来（Wave 0 · 0.3），代码一字未改。
本模块**不含任何 agent 级方法**；那些在 `agents.py`，二者由 `client.PiClient` 组合。
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
    ) -> dict[str, Any]:
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
