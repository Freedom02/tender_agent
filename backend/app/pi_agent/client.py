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

from ..tender_matching.document_parser import (
    parse_doc,
    parse_docx,
    parse_pdf,
    parse_plain_text,
    parse_xlsx,
)
from .config import PiConfig
from .prompts import (
    document_structure_prompt,
    evaluation_extract_prompt,
    material_match_prompt,
    outline_plan_prompt,
    volume_plan_prompt,
    parse_review_prompt,
    project_meta_prompt,
    requirement_extract_prompt,
    requirement_map_prompt,
    requirement_response_table_prompt,
    compliance_review_prompt,
    section_draft_prompt,
    section_rewrite_prompt,
    section_plan_prompt,
    table_form_fill_prompt,
    template_detect_prompt,
)


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


class PiClient:
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

    def _command(self) -> list[str]:
        resolved = self._resolve_command()
        command_path = Path(resolved)
        launch_args = [
            "--mode",
            "rpc",
            "--no-session",
            "--no-tools",
        ]
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
    ) -> dict[str, Any]:
        runtime_env = {
            **os.environ,
            "PI_OFFLINE": os.getenv("PI_OFFLINE", "0"),
        }
        if self.config.provider == "company-ai" and self.config.api_key:
            # ~/.pi/agent/models.json defines company-ai's apiKey as
            # $COMPANY_LLM_API_KEY. Keep the secret out of the prompt and
            # provide it only to the child process.
            runtime_env["COMPANY_LLM_API_KEY"] = self.config.api_key
        try:
            process = subprocess.Popen(
                self._command(),
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

    @staticmethod
    def _parse_file(path: Path) -> dict[str, Any]:
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise PiError(f"无法读取招标文件：{exc}") from exc
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            return parse_pdf(content)
        if suffix == ".docx":
            return parse_docx(content)
        if suffix == ".doc":
            return parse_doc(content)
        if suffix in {".xlsx", ".xls"}:
            return parse_xlsx(content)
        return parse_plain_text(content)

    def parse_tender(
        self,
        path: Path,
        *,
        workspace_id: str,
        project_id: str,
        project_context: dict[str, Any] | None = None,
        progress_callback: Callable[[str, str, int, int], None] | None = None,
    ) -> dict[str, Any]:
        from .orchestrator import PiParseOrchestrator

        return PiParseOrchestrator(self).parse_tender(
            path,
            workspace_id=workspace_id,
            project_id=project_id,
            project_context=project_context or {"source": "local_upload"},
            progress_callback=progress_callback,
        )

    def parse_document_structure(
        self,
        *,
        document_text: str,
        document_payload: dict[str, Any],
        filename: str,
        parser_warnings: list[str],
        workspace_id: str,
        project_id: str,
        project_context: dict[str, Any],
    ) -> dict[str, Any]:
        return self.run_json(
            document_structure_prompt(
                document_text=_clip(document_text),
                document_payload=document_payload,
                filename=filename,
                parser_warnings=parser_warnings,
                workspace_id=workspace_id,
                project_id=project_id,
                project_context=project_context,
            )
        )

    def extract_project_meta(
        self,
        *,
        document_text: str,
        document_structure: dict[str, Any],
        filename: str,
        workspace_id: str,
        project_id: str,
        project_context: dict[str, Any],
    ) -> dict[str, Any]:
        return self.run_json(
            project_meta_prompt(
                document_text=_clip(document_text),
                document_structure=document_structure,
                filename=filename,
                workspace_id=workspace_id,
                project_id=project_id,
                project_context=project_context,
            )
        )

    def extract_requirements(
        self,
        *,
        document_text: str,
        document_structure: dict[str, Any],
        project_meta: dict[str, Any],
        filename: str,
        workspace_id: str,
        project_id: str,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self.run_json(
            requirement_extract_prompt(
                document_text=_clip(document_text),
                document_structure=document_structure,
                project_meta=project_meta,
                filename=filename,
                workspace_id=workspace_id,
                project_id=project_id,
            ),
            timeout_seconds=timeout_seconds,
        )

    def extract_evaluations(
        self,
        *,
        document_text: str,
        document_structure: dict[str, Any],
        requirements: list[dict[str, Any]],
        filename: str,
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        return self.run_json(
            evaluation_extract_prompt(
                document_text=_clip(document_text),
                document_structure=document_structure,
                requirements=requirements,
                filename=filename,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )

    def detect_templates(
        self,
        *,
        document_text: str,
        document_structure: dict[str, Any],
        filename: str,
        workspace_id: str,
        project_id: str,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self.run_json(
            template_detect_prompt(
                document_text=_clip(document_text),
                document_structure=document_structure,
                filename=filename,
                workspace_id=workspace_id,
                project_id=project_id,
            ),
            timeout_seconds=timeout_seconds,
        )

    def review_parse(
        self,
        *,
        project_meta: dict[str, Any],
        document_structure: dict[str, Any],
        requirements: list[dict[str, Any]],
        evaluation_items: list[dict[str, Any]],
        bid_format_spec: dict[str, Any],
        filename: str,
        workspace_id: str,
        project_id: str,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self.run_json(
            parse_review_prompt(
                project_meta=project_meta,
                document_structure=document_structure,
                requirements=requirements,
                evaluation_items=evaluation_items,
                bid_format_spec=bid_format_spec,
                filename=filename,
                workspace_id=workspace_id,
                project_id=project_id,
            ),
            timeout_seconds=timeout_seconds,
        )

    def draft_section(
        self,
        *,
        workspace_id: str,
        project_id: str,
        section: dict[str, Any],
        fine_review: dict[str, Any],
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        mode: str = "open",
        parse_bundle: dict[str, Any] | None = None,
        coverage: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        from .writing_orchestrator import PiWritingOrchestrator

        resolved_bundle = parse_bundle or {
            "schemaVersion": "1.0",
            "provider": "pi",
            "workspaceId": workspace_id,
            "projectId": project_id,
            "projectMeta": (project or {}).get("parsed") or {},
            "documentStructure": {
                "sections": (
                    (project or {}).get("document", {}).get("sections", [])
                    if isinstance((project or {}).get("document"), dict)
                    else []
                )
            },
            "requirements": (fine_review or {}).get("rows") or [],
            "evaluationItems": [],
            "bidFormatSpec": {},
            "rawOutline": [],
            "warnings": [],
        }
        resolved_coverage = coverage or [
            {
                "requirementId": str(row.get("requirementId") or row.get("id") or ""),
                "sectionId": str(section.get("sectionId") or ""),
                "status": "planned",
                "reason": "来自当前章节 requirementIds",
            }
            for row in (fine_review or {}).get("rows", [])
            if isinstance(row, dict)
            and str(row.get("requirementId") or row.get("id") or "")
            in {
                str(value)
                for value in (section.get("requirementIds") or [])
                if str(value).strip()
            }
        ]
        return PiWritingOrchestrator(self).draft_section(
            parse_bundle=resolved_bundle,
            coverage=resolved_coverage,
            section=section,
            project=project,
            company=company,
            knowledge=knowledge,
            mode=mode,
            workspace_id=workspace_id,
            project_id=project_id,
        )

    def match_materials(
        self,
        *,
        parse_bundle: dict[str, Any],
        coverage: list[dict[str, Any]],
        section: dict[str, Any],
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        mode: str,
        workspace_id: str,
        project_id: str,
        writing_profile: str = "technical",
    ) -> dict[str, Any]:
        return self.run_json(
            material_match_prompt(
                parse_bundle=parse_bundle,
                coverage=coverage,
                section=section,
                project=project,
                company=company,
                knowledge=knowledge,
                mode=mode,
                workspace_id=workspace_id,
                project_id=project_id,
                writing_profile=writing_profile,
            )
        )

    def plan_section(
        self,
        *,
        parse_bundle: dict[str, Any],
        coverage: list[dict[str, Any]],
        section: dict[str, Any],
        material_matches: dict[str, Any],
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        mode: str,
        workspace_id: str,
        project_id: str,
        writing_profile: str = "technical",
    ) -> dict[str, Any]:
        return self.run_json(
            section_plan_prompt(
                parse_bundle=parse_bundle,
                coverage=coverage,
                section=section,
                material_matches=material_matches,
                project=project,
                company=company,
                knowledge=knowledge,
                mode=mode,
                workspace_id=workspace_id,
                project_id=project_id,
                writing_profile=writing_profile,
            )
        )

    def draft_section_agent(
        self,
        *,
        parse_bundle: dict[str, Any],
        coverage: list[dict[str, Any]],
        section: dict[str, Any],
        writing_plan: dict[str, Any],
        material_matches: dict[str, Any],
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        mode: str,
        workspace_id: str,
        project_id: str,
        writing_profile: str = "technical",
    ) -> dict[str, Any]:
        return self.run_json(
            section_draft_prompt(
                parse_bundle=parse_bundle,
                coverage=coverage,
                section=section,
                writing_plan=writing_plan,
                material_matches=material_matches,
                project=project,
                company=company,
                knowledge=knowledge,
                mode=mode,
                workspace_id=workspace_id,
                project_id=project_id,
                writing_profile=writing_profile,
            )
        )

    def rewrite_section(
        self,
        *,
        parse_bundle: dict[str, Any],
        coverage: list[dict[str, Any]],
        section: dict[str, Any],
        current_content: str,
        instruction: str,
        operation: str,
        selected_text: str,
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        mode: str,
        workspace_id: str,
        project_id: str,
        writing_profile: str = "technical",
    ) -> dict[str, Any]:
        return self.run_json(
            section_rewrite_prompt(
                parse_bundle=parse_bundle,
                coverage=coverage,
                section=section,
                current_content=current_content,
                instruction=instruction,
                operation=operation,
                selected_text=selected_text,
                project=project,
                company=company,
                knowledge=knowledge,
                mode=mode,
                workspace_id=workspace_id,
                project_id=project_id,
                writing_profile=writing_profile,
            )
        )

    def review_compliance(
        self,
        *,
        parse_bundle: dict[str, Any],
        bid_document: dict[str, Any],
        fine_review: dict[str, Any],
        baseline_review: dict[str, Any],
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        allowed_section_ids: list[str],
        allowed_requirement_ids: list[str],
        allowed_evaluation_ids: list[str],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        return self.run_json(
            compliance_review_prompt(
                parse_bundle=parse_bundle,
                bid_document=bid_document,
                fine_review=fine_review,
                baseline_review=baseline_review,
                project=project,
                company=company,
                knowledge=knowledge,
                allowed_section_ids=allowed_section_ids,
                allowed_requirement_ids=allowed_requirement_ids,
                allowed_evaluation_ids=allowed_evaluation_ids,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )

    def fill_template_fields(
        self,
        *,
        parse_bundle: dict[str, Any],
        fragment: dict[str, Any],
        section: dict[str, Any],
        project: dict[str, Any],
        company: dict[str, Any],
        knowledge: dict[str, Any],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        return self.run_json(
            table_form_fill_prompt(
                parse_bundle=parse_bundle,
                fragment=fragment,
                section=section,
                project=project,
                company=company,
                knowledge=knowledge,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )

    def draft_requirement_responses(
        self,
        *,
        section: dict[str, Any],
        requirements: list[dict[str, Any]],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        return self.run_json(
            requirement_response_table_prompt(
                section=section,
                requirements=requirements,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )

    def plan_outline(
        self,
        *,
        workspace_id: str,
        project_id: str,
        project: dict[str, Any] | None = None,
        fine_review: dict[str, Any] | None = None,
        cloud_parse: dict[str, Any] | None = None,
        document_sections: list[dict[str, Any]] | None = None,
        existing_plan: list[dict[str, Any]] | None = None,
        parse_bundle: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        from .writing_orchestrator import PiWritingOrchestrator

        resolved_bundle = parse_bundle or {
            "schemaVersion": "1.0",
            "provider": "pi",
            "workspaceId": workspace_id,
            "projectId": project_id,
            "projectMeta": (cloud_parse or {}).get("projectMeta") or (project or {}).get("parsed") or {},
            "documentStructure": {"sections": document_sections or []},
            "requirements": (cloud_parse or {}).get("requirements") or (fine_review or {}).get("rows") or [],
            "evaluationItems": (cloud_parse or {}).get("evaluationItems") or [],
            "bidFormatSpec": (cloud_parse or {}).get("bidFormatSpec") or {},
            "submissionStructure": (cloud_parse or {}).get("submissionStructure") or {},
            "rawOutline": (cloud_parse or {}).get("outline") or [],
            "warnings": (cloud_parse or {}).get("warnings") or [],
        }
        if document_sections:
            outline_sections = [
                dict(item) for item in document_sections if isinstance(item, dict)
            ]
            seen_section_keys = {
                str(item.get("sectionId") or item.get("id") or "").strip()
                for item in outline_sections
                if str(item.get("sectionId") or item.get("id") or "").strip()
            }
            seen_titles = {
                " ".join(
                    str(item.get("heading") or item.get("title") or item.get("name") or "").split()
                )
                for item in outline_sections
            }
            parsed_sections = (
                (resolved_bundle.get("documentStructure") or {}).get("sections")
                if isinstance(resolved_bundle.get("documentStructure"), dict)
                else []
            )
            for item in parsed_sections if isinstance(parsed_sections, list) else []:
                if not isinstance(item, dict):
                    continue
                section_id = str(item.get("sectionId") or item.get("id") or "").strip()
                title = " ".join(
                    str(item.get("heading") or item.get("title") or item.get("name") or "").split()
                )
                if (section_id and section_id in seen_section_keys) or (title and title in seen_titles):
                    continue
                outline_sections.append(dict(item))
                if section_id:
                    seen_section_keys.add(section_id)
                if title:
                    seen_titles.add(title)
            resolved_bundle = {
                **resolved_bundle,
                "outlineSourceSections": outline_sections,
            }
        return PiWritingOrchestrator(self).plan_outline(
            parse_bundle=resolved_bundle,
            existing_plan=existing_plan or [],
            workspace_id=workspace_id,
            project_id=project_id,
        )

    def plan_volumes_agent(
        self,
        *,
        parse_bundle: dict[str, Any],
        existing_plan: list[dict[str, Any]],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        return self.run_json(volume_plan_prompt(
            parse_bundle=parse_bundle,
            existing_plan=existing_plan,
            workspace_id=workspace_id,
            project_id=project_id,
        ))

    def plan_outline_agent(
        self,
        *,
        parse_bundle: dict[str, Any],
        existing_plan: list[dict[str, Any]],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        return self.run_json(
            outline_plan_prompt(
                parse_bundle=parse_bundle,
                existing_plan=existing_plan,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )

    def map_requirements(
        self,
        *,
        parse_bundle: dict[str, Any],
        outline_plan: list[dict[str, Any]],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        return self.run_json(
            requirement_map_prompt(
                parse_bundle=parse_bundle,
                outline_plan=outline_plan,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )
