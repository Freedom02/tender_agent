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
        return self.run_json(
            requirement_map_prompt(
                parse_bundle=parse_bundle,
                outline_plan=outline_plan,
                workspace_id=workspace_id,
                project_id=project_id,
            )
        )
