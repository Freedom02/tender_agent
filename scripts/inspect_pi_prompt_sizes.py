from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.pi_agent.orchestrator import (  # noqa: E402
    _compact_requirements_for_agent,
    _compact_structure_for_agent,
    _requirement_agent_sample,
    _template_relevant_text,
)
from backend.app.pi_agent.prompts import (  # noqa: E402
    compliance_review_prompt,
    document_structure_prompt,
    evaluation_extract_prompt,
    material_match_prompt,
    outline_plan_prompt,
    parse_review_prompt,
    project_meta_prompt,
    requirement_extract_prompt,
    requirement_map_prompt,
    section_draft_prompt,
    section_plan_prompt,
    section_rewrite_prompt,
    table_form_fill_prompt,
    template_detect_prompt,
)
from backend.app.pi_agent.client import _clip  # noqa: E402
from backend.app.bid_writing.schemas import resolve_writing_profile  # noqa: E402
from backend.app.store import Store  # noqa: E402


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def text(value: Any, default: str = "") -> str:
    return str(value or default)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="统计并导出当前 Pi 智能体的实际渲染提示词长度。"
    )
    parser.add_argument(
        "--workspace-id",
        default="workspace-721e4c0cfc",
        help="工作区 ID，默认使用当前问题工作区。",
    )
    parser.add_argument(
        "--section-id",
        default="",
        help="撰写阶段的章节 ID；不传时取第一个非偏离章节。",
    )
    parser.add_argument(
        "--dump",
        action="store_true",
        help="同时把每个提示词写入 runtime/pi-prompt-inspection/。",
    )
    args = parser.parse_args()

    workspaces = load_json(ROOT / "runtime" / "workspaces.json", {})
    workspace = workspaces.get(args.workspace_id) if isinstance(workspaces, dict) else None
    if not isinstance(workspace, dict):
        print(f"未找到工作区：{args.workspace_id}", file=sys.stderr)
        return 1

    live_store: Store | None = None
    store_warning = ""
    try:
        live_store = Store(ROOT)
        live_store.initialize()
        workspace = live_store.workspaces.get(args.workspace_id) or workspace
    except Exception as exc:
        store_warning = f"M3/运行时画像读取失败，撰写阶段将按空材料估算：{exc}"

    bid_document = workspace.get("bidDocument")
    bid_document = bid_document if isinstance(bid_document, dict) else {}
    parse_ref = workspace.get("parseBundleRef")
    parse_ref = parse_ref if isinstance(parse_ref, dict) else {}
    artifact = ROOT / text(parse_ref.get("artifact")).replace("/", "\\")
    parse_bundle = load_json(artifact, {})
    if not isinstance(parse_bundle, dict):
        parse_bundle = {}

    workspace_id = text(workspace.get("id")) or args.workspace_id
    project_id = text(workspace.get("projectId"))
    project = workspace.get("project")
    project = project if isinstance(project, dict) else {}
    document_blocks = parse_bundle.get("documentBlocks")
    document_blocks = document_blocks if isinstance(document_blocks, dict) else {}
    document_text = text(document_blocks.get("text") or project.get("analysisText"))
    document_structure = parse_bundle.get("documentStructure")
    document_structure = document_structure if isinstance(document_structure, dict) else {}
    compact_structure = _compact_structure_for_agent(document_structure)
    project_meta = parse_bundle.get("projectMeta")
    project_meta = project_meta if isinstance(project_meta, dict) else {}
    requirements = parse_bundle.get("requirements")
    requirements = requirements if isinstance(requirements, list) else []
    evaluations = parse_bundle.get("evaluationItems")
    evaluations = evaluations if isinstance(evaluations, list) else []
    bid_format_spec = parse_bundle.get("bidFormatSpec")
    bid_format_spec = bid_format_spec if isinstance(bid_format_spec, dict) else {}
    filename = text(
        (parse_bundle.get("sourceFile") or {}).get("originalFilename")
        if isinstance(parse_bundle.get("sourceFile"), dict)
        else ""
    ) or f"{text(project.get('title')) or '招标文件'}.pdf"
    project_context = {"source": "local_upload"}
    compact_requirements = _compact_requirements_for_agent(requirements)
    existing_plan = bid_document.get("plan")
    existing_plan = existing_plan if isinstance(existing_plan, list) else []
    coverage = bid_document.get("requirementCoverage")
    coverage = coverage if isinstance(coverage, list) else []

    prompts: list[tuple[str, str, str]] = []

    def add(name: str, stage: str, value: str) -> None:
        prompts.append((stage, name, value))

    add(
        "DocumentStructureAgent",
        "parse",
        document_structure_prompt(
            document_text=_clip(document_text),
            document_payload=document_blocks,
            filename=filename,
            parser_warnings=parse_bundle.get("warnings") or [],
            workspace_id=workspace_id,
            project_id=project_id,
            project_context=project_context,
        ),
    )
    add(
        "ProjectMetaAgent",
        "parse",
        project_meta_prompt(
            document_text=_clip(document_text),
            document_structure=compact_structure,
            filename=filename,
            workspace_id=workspace_id,
            project_id=project_id,
            project_context=project_context,
        ),
    )
    requirement_sample = _requirement_agent_sample(requirements)
    add(
        "RequirementExtractAgent",
        "parse",
        requirement_extract_prompt(
            document_text=_clip(requirement_sample or document_text),
            document_structure=compact_structure,
            project_meta=project_meta,
            filename=filename,
            workspace_id=workspace_id,
            project_id=project_id,
        ),
    )
    add(
        "EvaluationExtractAgent",
        "parse",
        evaluation_extract_prompt(
            document_text=_clip(document_text),
            document_structure=compact_structure,
            requirements=compact_requirements,
            filename=filename,
            workspace_id=workspace_id,
            project_id=project_id,
        ),
    )
    add(
        "TemplateDetectAgent",
        "parse",
        template_detect_prompt(
            document_text=_clip(_template_relevant_text(document_text)),
            document_structure=compact_structure,
            filename=filename,
            workspace_id=workspace_id,
            project_id=project_id,
        ),
    )
    add(
        "ParseReviewAgent",
        "parse",
        parse_review_prompt(
            project_meta=project_meta,
            document_structure=compact_structure,
            requirements=compact_requirements,
            evaluation_items=evaluations,
            bid_format_spec=bid_format_spec,
            filename=filename,
            workspace_id=workspace_id,
            project_id=project_id,
        ),
    )
    add(
        "OutlinePlanAgent",
        "planning",
        outline_plan_prompt(
            parse_bundle=parse_bundle,
            existing_plan=existing_plan,
            workspace_id=workspace_id,
            project_id=project_id,
        ),
    )
    add(
        "RequirementMapAgent",
        "planning",
        requirement_map_prompt(
            parse_bundle=parse_bundle,
            outline_plan=existing_plan,
            workspace_id=workspace_id,
            project_id=project_id,
        ),
    )

    section = None
    if args.section_id:
        section = next(
            (
                item
                for item in existing_plan
                if isinstance(item, dict) and text(item.get("sectionId")) == args.section_id
            ),
            None,
        )
    if section is None:
        section = next(
            (
                item
                for item in existing_plan
                if isinstance(item, dict)
                and text(item.get("kind")) != "deviation"
                and text(item.get("source")) == "ai"
            ),
            {"sectionId": "sample-section", "title": "示例章节", "requirementIds": []},
        )
    section_id = text(section.get("sectionId")) or "sample-section"
    writing_profile = resolve_writing_profile(section)
    section_coverage = [
        item
        for item in coverage
        if isinstance(item, dict)
        and text(item.get("sectionId")) in {"", section_id}
    ]
    empty_material_matches = {"matches": [], "unmatchedRequirementIds": [], "warnings": []}
    empty_writing_plan = {"sectionId": section_id, "outline": [], "requirementResponses": []}
    company = live_store.company if live_store is not None else {}
    knowledge = live_store.company_knowledge if live_store is not None else {}
    if not isinstance(company, dict):
        company = {}
    if not isinstance(knowledge, dict):
        knowledge = {}
    fragments = parse_bundle.get("templateFragments")
    fragments = fragments if isinstance(fragments, list) else []
    template_fragment = next(
        (item for item in fragments if isinstance(item, dict)),
        {},
    )

    add(
        "MaterialMatchAgent",
        "writing",
        material_match_prompt(
            parse_bundle=parse_bundle,
            coverage=section_coverage,
            section=section,
            project=project,
            company=company,
            knowledge=knowledge,
            mode=text(bid_document.get("mode")) or "open",
            workspace_id=workspace_id,
            project_id=project_id,
            writing_profile=writing_profile,
        ),
    )
    add(
        "SectionPlanAgent",
        "writing",
        section_plan_prompt(
            parse_bundle=parse_bundle,
            coverage=section_coverage,
            section=section,
            material_matches=empty_material_matches,
            project=project,
            company=company,
            knowledge=knowledge,
            mode=text(bid_document.get("mode")) or "open",
            workspace_id=workspace_id,
            project_id=project_id,
            writing_profile=writing_profile,
        ),
    )
    add(
        "SectionDraftAgent",
        "writing",
        section_draft_prompt(
            parse_bundle=parse_bundle,
            coverage=section_coverage,
            section=section,
            writing_plan=empty_writing_plan,
            material_matches=empty_material_matches,
            project=project,
            company=company,
            knowledge=knowledge,
            mode=text(bid_document.get("mode")) or "open",
            workspace_id=workspace_id,
            project_id=project_id,
            writing_profile=writing_profile,
        ),
    )
    add(
        "SectionRewriteAgent",
        "writing",
        section_rewrite_prompt(
            parse_bundle=parse_bundle,
            coverage=section_coverage,
            section=section,
            current_content=text(section.get("content")),
            instruction="补充本章节内容",
            operation="append",
            selected_text="",
            project=project,
            company=company,
            knowledge=knowledge,
            mode=text(bid_document.get("mode")) or "open",
            workspace_id=workspace_id,
            project_id=project_id,
            writing_profile=writing_profile,
        ),
    )
    add(
        "TableFormFillAgent",
        "writing",
        table_form_fill_prompt(
            parse_bundle=parse_bundle,
            fragment=template_fragment,
            section=section,
            project=project,
            company=company,
            knowledge=knowledge,
            workspace_id=workspace_id,
            project_id=project_id,
        ),
    )
    add(
        "ComplianceReviewAgent",
        "review",
        compliance_review_prompt(
            parse_bundle=parse_bundle,
            bid_document=bid_document,
            fine_review=(workspace.get("reasoning") or {}).get("fineReview", {}),
            baseline_review={},
            project=project,
            company=company,
            knowledge=knowledge,
            allowed_section_ids=[
                text(item.get("sectionId"))
                for item in existing_plan
                if isinstance(item, dict) and text(item.get("sectionId"))
            ],
            allowed_requirement_ids=[
                text(item.get("id") or item.get("requirementId"))
                for item in requirements
                if isinstance(item, dict) and text(item.get("id") or item.get("requirementId"))
            ],
            allowed_evaluation_ids=[
                text(item.get("id") or item.get("evaluationId"))
                for item in evaluations
                if isinstance(item, dict) and text(item.get("id") or item.get("evaluationId"))
            ],
            workspace_id=workspace_id,
            project_id=project_id,
        ),
    )

    output_dir = ROOT / "runtime" / "pi-prompt-inspection"
    if args.dump:
        output_dir.mkdir(parents=True, exist_ok=True)

    print(f"workspace: {workspace_id}")
    print(f"section sample: {section_id}")
    print(f"writing profile: {writing_profile}")
    print(f"requirements: {len(requirements)}")
    print(f"document text chars: {len(document_text)}")
    print(f"company fields: {len(company)}")
    print(f"knowledge groups: {len(knowledge)}")
    if store_warning:
        print(f"warning: {store_warning}")
    print()
    print(f"{'阶段':<10} {'智能体':<28} {'字符数':>10} {'文件':<60}")
    print("-" * 115)
    for stage, name, prompt in prompts:
        path_text = ""
        if args.dump:
            target = output_dir / f"{stage}-{name}.txt"
            target.write_text(prompt, encoding="utf-8")
            path_text = str(target)
        print(f"{stage:<10} {name:<28} {len(prompt):>10} {path_text}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
