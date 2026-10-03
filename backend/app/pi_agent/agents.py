# -*- coding: utf-8 -*-
"""Pi 智能体方法层：19 个 agent 级调用（一个智能体一个方法）。

由原 `client.py` 机械拆分而来（Wave 0 · 0.3），代码一字未改；
传输能力来自 `transport.PiTransport`，由 `client.PiClient` 组合。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
from ..tender_matching.document_parser import (
    parse_doc,
    parse_docx,
    parse_pdf,
    parse_plain_text,
    parse_xlsx,
)
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
from .transport import (  # noqa: F401
    PiError,
    _clip,
)


class PiAgentMethods:
    """各智能体的调用方法（依赖 `PiTransport.run_json`）。"""

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
        tools: str = "",
        extensions: tuple[str, ...] = (),
        env_extra: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self.run_json(
            outline_plan_prompt(
                parse_bundle=parse_bundle,
                existing_plan=existing_plan,
                workspace_id=workspace_id,
                project_id=project_id,
            ),
            tools=tools,
            extensions=extensions,
            env_extra=env_extra,
            timeout_seconds=timeout_seconds,
        )


    def map_requirements(
        self,
        *,
        parse_bundle: dict[str, Any],
        outline_plan: list[dict[str, Any]],
        workspace_id: str,
        project_id: str,
    ) -> dict[str, Any]:
        """条款-章节映射：短任务说明 + 只读工具按需取数。

        改造前这里把「条款映射用 ParseBundle 精简视图」整块内联进提示词（本机实测
        单批约 66 KB），而映射真正需要的是**本批条款原文**与**可挂载的目录章节**。
        现在只给：任务说明 + 本批条款 ID 清单 + 章节 ID 清单 + 工具使用约定 + 输出
        schema + 映射规则；条款原文由 `search_requirements` 取、章节细节由
        `list_chapters` 取、候选由 `validate_coverage_candidate` 自检。

        输出 JSON 的字段名与旧提示词完全一致（`REQUIREMENT_MAP_SCHEMA` 原样内联）。
        工具扩展缺失或策略关掉时，退回**逐字节相同**的旧提示词（`requirement_map_prompt`），
        保证任何环境都不会因为这次改造而失去可用路径。
        """

        import json
        import logging
        import time

        from .agent_context import (
            MAP_TOOL_ALLOWLIST,
            build_context,
            extension_path,
            resolve_policy,
            write_context,
        )
        from .prompts import REQUIREMENT_MAP_SCHEMA

        logger = logging.getLogger(__name__)
        policy = resolve_policy()
        extension = extension_path()
        context_path: Path | None = None
        tools = ""
        extensions: tuple[str, ...] = ()
        env_extra: dict[str, str] = {}
        fallback_warning = ""

        def legacy_prompt() -> str:
            return requirement_map_prompt(
                parse_bundle=parse_bundle,
                outline_plan=outline_plan,
                workspace_id=workspace_id,
                project_id=project_id,
            )

        def is_policy_blocked(value: Any) -> bool:
            if isinstance(value, dict):
                return any(is_policy_blocked(item) for item in value.values())
            if isinstance(value, (list, tuple, set)):
                return any(is_policy_blocked(item) for item in value)
            return "policy_blocked" in str(value or "").lower()

        def add_warning(raw: Any, message: str) -> dict[str, Any]:
            result = dict(raw) if isinstance(raw, dict) else {}
            warnings = result.get("warnings")
            result["warnings"] = [
                *(warnings if isinstance(warnings, list) else []),
                message,
            ]
            return result

        if policy["enabled"] and not extension.is_file():
            fallback_warning = "未找到条款映射工具扩展，本次智能体不启用工具取证（按提示词中的解析视图作答）。"
        elif policy["enabled"]:
            try:
                context_path = write_context(
                    build_context(
                        parse_bundle,
                        outline_plan,
                        workspace_id=workspace_id,
                        project_id=project_id,
                        policy=policy,
                    )
                )
                # The extension runs in a child process. Verify the injected file
                # is present and readable before advertising the tool path.
                if not context_path.is_file():
                    raise OSError(f"上下文文件不存在：{context_path}")
                with context_path.open("rb") as context_file:
                    context_file.read(1)
            except OSError as exc:
                fallback_warning = f"条款映射工具上下文不可读，已回退 legacy 路径：{exc}"
                tools = ""
                extensions = ()
                env_extra = {}
            else:
                tools = MAP_TOOL_ALLOWLIST
                extensions = (str(extension),)
                env_extra = {"BID_MAP_CONTEXT": str(context_path)}

        if tools:

            requirement_lines: list[str] = []
            for item in parse_bundle.get("requirements") or []:
                if not isinstance(item, dict):
                    continue
                requirement_id = str(item.get("id") or item.get("requirementId") or "").strip()
                if not requirement_id:
                    continue
                flags = "".join(
                    flag
                    for flag, active in (
                        ("m", bool(item.get("mandatory"))),
                        ("r", bool(item.get("isRejection"))),
                    )
                    if active
                )
                requirement_lines.append(
                    f"{requirement_id}{' [' + flags + ']' if flags else ''}"
                )
            chapter_lines: list[str] = []
            for row in outline_plan or []:
                if not isinstance(row, dict):
                    continue
                section_id = str(row.get("sectionId") or "").strip()
                if not section_id:
                    continue
                chapter_lines.append(
                    " | ".join(
                        part
                        for part in (
                            section_id,
                            " ".join(str(row.get("title") or "").split())[:60],
                            f"L{row.get('level') or 1}",
                            str(row.get("responseType") or ""),
                        )
                        if part
                    )
                )
            evaluation_lines = [
                str(item.get("id") or "").strip()
                for item in parse_bundle.get("evaluationItems") or []
                if isinstance(item, dict) and str(item.get("id") or "").strip()
            ]
            prompt = f"""
你是 RequirementMapAgent，负责建立招标要求、评分项与投标目录之间的覆盖关系。

你只负责映射和覆盖检查，不撰写正文，不修改招标原文，不编造企业能力。
必须只输出一个合法 JSON 对象，不要输出 Markdown、代码块、解释或额外文字。
所有 ID 必须来自本批条款清单、目录章节清单或工具返回结果；不得自行编造 ID。

工作区：{workspace_id}
项目：{project_id}

【本批条款待办清单】（共 {len(requirement_lines)} 条；格式：条款ID [m=强制, r=废标]）
{chr(10).join(requirement_lines)}

【可引用的目录章节】（格式：sectionId | 标题 | 层级 | responseType）
{chr(10).join(chapter_lines)}

【可引用的评分项 ID】{", ".join(evaluation_lines) or "（无）"}

【可用工具（只读，按需调用）】
1. get_requirement_batch 一次取全本批（limit 直接写 80），不要分页反复调用。
2. 只有当摘要不足以判断该条款由哪个章节响应时，才用 search_requirements 读原文；
   一次查询问一个主题（例如“质保 售后 培训”），一次覆盖一批条款，不要逐条查询，最多 3 次。
3. 用 list_chapters 取章节细节（层级、responseType、已有 requirementIds/evaluationIds），最多 1 次；
   sectionId 只能取自它返回的节点（或上面章节清单）。
4. 定稿前必须调用 validate_coverage_candidate 自检，直到它返回 ok=true 再输出；
   它会指出不存在的 sectionId/requirementId、非法取值与漏掉的条款。
条款原文不在提示词里；但请把工具往返控制在 6 次以内，凑齐判断依据后立即输出结果。

【输出结构】
{json.dumps(REQUIREMENT_MAP_SCHEMA, ensure_ascii=False)}

映射规则：
0. 即使要求或评分项数量为 0，也必须返回合法的空数组；本批条款清单中的每一条都必须出现，要么在 coverage 里，要么在 uncoveredRequirements 里。
1. 每一条 requirement 都要判断是否被目录覆盖；mandatory 或 isRejection 条款不能无理由遗漏。
2. coverageType 使用 chapter_text、table、attachment、commitment、fixed_form 或 other。
3. status 使用 planned、covered、uncovered 或 needs_review。
4. evaluationIds 只能引用上面评分项 ID 清单中存在的 id。
5. sectionId 只能引用目录章节清单（或 list_chapters 返回）中存在的 sectionId。
6. 同一条款确实需要多个章节响应时才放入 duplicatedRequirements，否则只保留一个主响应章节。
7. uncoveredRequirements 和 duplicatedRequirements 使用对象数组，至少包含 requirementId、reason。
8. reason 必须说明映射依据或风险，不得用空泛表述掩盖没有来源的判断；每条控制在一句话内（不超过 60 字），
   保证整批输出能在一次回复里写完，不被截断。
""".strip()
        else:
            prompt = legacy_prompt()

        context_bytes = 0
        if context_path is not None and tools:
            try:
                context_bytes = context_path.stat().st_size
            except OSError:
                context_bytes = 0

        def run_and_record(call_prompt: str, *, call_tools: str, call_extensions: tuple[str, ...], call_env: dict[str, str]) -> dict[str, Any]:
            started = time.perf_counter()
            try:
                return self.run_json(
                    call_prompt,
                    tools=call_tools,
                    extensions=call_extensions,
                    env_extra=call_env,
                )
            finally:
                logger.info(
                    "requirement_map_call",
                    extra={
                        "prompt_bytes": len(call_prompt.encode("utf-8")),
                        "tools": call_tools,
                        "extensions": call_extensions,
                        "env_keys": sorted(call_env),
                        "context_bytes": context_bytes if call_tools else 0,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
                    },
                )

        try:
            try:
                raw = run_and_record(
                    prompt,
                    call_tools=tools,
                    call_extensions=extensions,
                    call_env=env_extra,
                )
            except PiError as exc:
                if not tools:
                    raise
                if "policy_blocked" in str(exc).lower():
                    fallback_warning = "模型拒绝了只读工具策略（policy_blocked），已回退 legacy 路径。"
                else:
                    fallback_warning = "只读工具调用不可用，已回退 legacy 路径。"
                raw = run_and_record(
                    legacy_prompt(),
                    call_tools="",
                    call_extensions=(),
                    call_env={},
                )
            if tools and is_policy_blocked(raw):
                fallback_warning = "模型返回 policy_blocked，已回退 legacy 路径。"
                raw = run_and_record(
                    legacy_prompt(),
                    call_tools="",
                    call_extensions=(),
                    call_env={},
                )
        finally:
            if context_path is not None:
                import shutil

                context_dir = Path(context_path).parent
                if context_dir.name.startswith("bid-map-context-"):
                    shutil.rmtree(context_dir, ignore_errors=True)
                else:
                    try:
                        context_path.unlink(missing_ok=True)
                    except OSError:
                        pass
        if fallback_warning:
            return add_warning(raw, fallback_warning)
        return raw
