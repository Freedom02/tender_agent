"""Run one real tender document through the typed P02-P09 pipeline.

The command is intentionally offline.  It writes a browsable run bundle:

    runtime/demo-pipeline/projects/<project>/runs/<run>/README.md
    runtime/demo-pipeline/projects/<project>/runs/<run>/manifest.json
    runtime/demo-pipeline/projects/<project>/runs/<run>/stages/*.json

The run record is also persisted through ``AnalysisRunRecorder`` so the same
status model can later be exposed by the API or a node-status UI.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.company_facts import build_company_ledger  # noqa: E402
from backend.app.tender_matching.document_chunking import chunk_document  # noqa: E402
from backend.app.tender_matching.document_models import DocumentIR  # noqa: E402
from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402
from backend.app.tender_matching.evidence_validation import (  # noqa: E402
    validate_extraction_result,
)
from backend.app.tender_matching.fact_models import (  # noqa: E402
    FactArtifactStore,
    RequirementExtractionResult,
    build_analysis_artifact,
)
from backend.app.tender_matching.observability import (  # noqa: E402
    AnalysisRunRecorder,
    utc_now,
)
from backend.app.tender_matching.requirement_extractor import (  # noqa: E402
    extract_requirements,
)
from backend.app.tender_matching.section_chunk_quality import (  # noqa: E402
    evaluate_section_and_chunking,
)
from backend.app.tender_matching.section_classifier import (  # noqa: E402
    classify_document,
)
from backend.app.tender_matching.technical_service_matching import (  # noqa: E402
    match_technical_service_requirements,
)


PROJECT_ID = "DEMO-PIPELINE"
DEFAULT_RUNTIME_ROOT = REPO_ROOT / "runtime" / "demo-pipeline"
NODE_DEPENDENCIES = {
    "p02_parse_document": [],
    "p03_section_tree": ["p02_parse_document"],
    "p04_chunking": ["p03_section_tree"],
    "p05_classification": ["p03_section_tree"],
    "p07_requirement_extraction": ["p04_chunking", "p05_classification"],
    "p08_evidence_validation": ["p02_parse_document", "p07_requirement_extraction"],
    "p06_fact_persistence": ["p08_evidence_validation"],
    "p09_technical_service_matching": ["p06_fact_persistence"],
}


def _mime(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(path.suffix.lower(), "")


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path.as_posix()


def _dump_model(path: Path, value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    return _write_json(path, value)


def _relative(path: Path, root: Path) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _default_input() -> Path:
    candidates = sorted(
        (
            path
            for path in (REPO_ROOT / "demo-data").glob("*")
            if path.is_file() and path.suffix.lower() in {".docx", ".pdf"}
        ),
        key=lambda item: item.name.lower(),
    )
    preferred = [path for path in candidates if "三峡" in path.name]
    if preferred:
        return preferred[0]
    if not candidates:
        raise FileNotFoundError(
            "没有找到本地演示文档。请使用 --input <PDF/DOC/DOCX 文件路径> 指定用户上传的文档。"
        )
    return candidates[0]


def _demo_company_ledger() -> dict[str, Any]:
    """Use the same explicitly synthetic ledger as the offline P09 contract tests."""

    return build_company_ledger(
        {
            "organization": {
                "organizationId": "ORG-DEMO",
                "organizationName": "演示企业（合成台账）",
                "capabilities": "数字孪生平台建设、三维可视化、数据接入、项目实施、系统集成、培训、运维服务",
            },
            "technical_product": [
                {
                    "productId": "PRD-DEMO",
                    "productName": "数字孪生可视化平台",
                    "description": "支持三维场景搭建、数字孪生可视化、物联网数据接入、低代码开发、权限管理和国产化环境部署",
                    "evidencePath": "demo-company/product/platform.pdf",
                }
            ],
            "technical_parameter": [
                {
                    "parameterId": "PAR-DEMO",
                    "productId": "PRD-DEMO",
                    "description": "支持三维模型加载、实时数据接入、可视化大屏、开放接口和系统集成",
                    "evidencePath": "demo-company/parameter/platform.pdf",
                }
            ],
        }
    )


def _stage_metrics(document: DocumentIR) -> dict[str, Any]:
    diagnostics = document.diagnostics
    return {
        "parseMethod": document.parse_method,
        "format": document.original_format,
        "textChars": len(document.text),
        "pageCount": len(document.pages),
        "segmentCount": len(document.segments),
        "unitCount": len(document.units),
        "sectionCount": len(document.sections),
        "tableCount": len(document.tables),
        "warningCount": len(document.warnings) + len(document.structure_warnings),
        "diagnosticStatus": diagnostics.status,
    }


def _run_node(
    recorder: AnalysisRunRecorder,
    *,
    stage: str,
    callback: Callable[[], tuple[Any, dict[str, Any], list[str]]],
    input_paths: list[str],
    output_paths: list[str],
) -> Any:
    started_at = utc_now()
    started_perf = time.perf_counter()
    try:
        value, metrics, warnings = callback()
    except Exception as exc:
        recorder.record_stage(
            stage,
            status="failed",
            started_at=started_at,
            finished_at=utc_now(),
            elapsed_ms=round((time.perf_counter() - started_perf) * 1000),
            input_paths=input_paths,
            output_paths=output_paths,
            errors=[f"{type(exc).__name__}: {exc}"],
        )
        recorder.fail(f"{stage}: {type(exc).__name__}: {exc}", stage=stage)
        raise
    recorder.record_stage(
        stage,
        status="completed",
        started_at=started_at,
        finished_at=utc_now(),
        elapsed_ms=round((time.perf_counter() - started_perf) * 1000),
        input_paths=input_paths,
        output_paths=output_paths,
        warnings=warnings,
        metrics=metrics,
    )
    return value


def _write_bundle_readme(
    bundle_root: Path,
    *,
    input_path: Path,
    document: DocumentIR,
    run_id: str,
    stage_rows: list[dict[str, Any]],
    final_match: Any,
) -> str:
    lines = [
        "# Document Pipeline Demo Run",
        "",
        f"- runId: `{run_id}`",
        f"- source: `{input_path}`",
        f"- documentVersionId: `{document.document_version_id}`",
        f"- parseMethod: `{document.parse_method}`",
        "- mode: offline, deterministic rules, synthetic company ledger for P09",
        "",
        "## Stage Timeline",
        "",
        "| node | execution | quality | elapsed | key metrics | outputs |",
        "|---|---|---|---:|---|---|",
    ]
    for row in stage_rows:
        metrics = ", ".join(
            f"{key}={value}"
            for key, value in row["metrics"].items()
            if value not in (None, "", [], {})
        )
        outputs = ", ".join(f"`{item}`" for item in row["outputs"])
        lines.append(
            f"| `{row['stage']}` | **{row['status']}** | "
            f"`{row['qualityStatus']}`"
            f"{' / review' if row['reviewRequired'] else ''} | "
            f"{row['elapsedMs']} ms | {metrics} | {outputs} |"
        )
    lines.extend(
        [
            "",
            "## How To Read",
            "",
            "1. Start with `manifest.json` for the dependency graph and artifact index.",
            "2. Open `run.json` for the persisted node status, warnings, errors, and run-level counters.",
            "3. Open files under `stages/` to inspect the exact typed output of each node.",
            "4. Every requirement and evidence item keeps source segment IDs and quote text so it can be traced back.",
            "",
            "## Final Matching Snapshot",
            "",
            f"- technical/service items: `{len(final_match.items)}`",
            f"- coverage: `{final_match.coverage:.1%}`",
            f"- recommendation: `{final_match.recommendation}`",
            f"- status counts: `{dict(final_match.status_counts)}`",
            "",
            "P09 uses an explicitly synthetic enterprise ledger. Its result demonstrates the contract and artifact flow, not real enterprise matching accuracy.",
        ]
    )
    path = bundle_root / "README.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _relative(path, bundle_root)


def run_pipeline(
    input_path: Path,
    *,
    runtime_root: Path = DEFAULT_RUNTIME_ROOT,
    project_id: str = PROJECT_ID,
    run_id: str = "",
) -> dict[str, Any]:
    input_path = input_path.resolve()
    content = input_path.read_bytes()
    run_id = run_id or f"RUN-DEMO-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    recorder: AnalysisRunRecorder | None = None
    document: DocumentIR | None = None
    bundle_root: Path | None = None
    stage_rows: list[dict[str, Any]] = []

    try:
        parse_started_perf = time.perf_counter()
        document = parse_document_ir(
            content,
            file_name=input_path.name,
            mime_type=_mime(input_path),
        )
        parse_elapsed_ms = round((time.perf_counter() - parse_started_perf) * 1000)
        recorder = AnalysisRunRecorder(
            runtime_root,
            project_id=project_id,
            document_version_id=document.document_version_id,
            run_id=run_id,
            parser_name=document.parse_method,
            parser_version_value=str(document.metadata.get("parserVersion") or ""),
            input_artifact_paths=[str(input_path)],
        )
        bundle_root = runtime_root / "projects" / project_id / "runs" / run_id
        stages_root = bundle_root / "stages"
        source_rel = str(input_path)
        parsed_path = bundle_root / "stages" / "p02_parse_document" / "document_ir.json"
        _dump_model(parsed_path, document)
        stage_rows.append(
            {
                "stage": "p02_parse_document",
                "status": "completed",
                "qualityStatus": (
                    "ready_for_next_stage"
                    if document.diagnostics.status == "parsed"
                    and not document.warnings
                    and not document.structure_warnings
                    else "usable_with_review"
                ),
                "reviewRequired": bool(document.warnings or document.structure_warnings),
                "elapsedMs": 0,
                "metrics": _stage_metrics(document),
                "inputs": [source_rel],
                "outputs": [_relative(parsed_path, bundle_root)],
            }
        )
        recorder.record_stage(
            "p02_parse_document",
            elapsed_ms=parse_elapsed_ms,
            input_paths=[source_rel],
            output_paths=[_relative(parsed_path, bundle_root)],
            metrics=_stage_metrics(document),
            warnings=[*document.warnings, *document.structure_warnings],
        )

        tree_started_perf = time.perf_counter()
        tree_path = stages_root / "p03_section_tree" / "section_tree.json"
        tree_quality = evaluate_section_and_chunking(document)["sectionTree"]
        _write_json(
            tree_path,
            {
                "schemaVersion": "section-tree-demo/v1",
                "documentVersionId": document.document_version_id,
                "sections": [item.model_dump(mode="json", by_alias=True) for item in document.sections],
                "quality": tree_quality,
            },
        )
        stage_rows.append(
            {
                "stage": "p03_section_tree",
                "status": "completed",
                "qualityStatus": tree_quality["readiness"],
                "reviewRequired": tree_quality["readiness"] != "ready_for_next_stage",
                "elapsedMs": 0,
                "metrics": {
                    "sectionCount": len(document.sections),
                    "score": tree_quality["score"],
                    "readiness": tree_quality["readiness"],
                },
                "inputs": [_relative(parsed_path, bundle_root)],
                "outputs": [_relative(tree_path, bundle_root)],
            }
        )
        recorder.record_stage(
            "p03_section_tree",
            elapsed_ms=round((time.perf_counter() - tree_started_perf) * 1000),
            input_paths=[_relative(parsed_path, bundle_root)],
            output_paths=[_relative(tree_path, bundle_root)],
            metrics=stage_rows[-1]["metrics"],
            warnings=[item["message"] for item in tree_quality.get("issues", [])],
        )

        chunk_started_perf = time.perf_counter()
        chunks = chunk_document(document, max_tokens=800)
        chunk_path = stages_root / "p04_chunking" / "chunks.json"
        chunk_quality = evaluate_section_and_chunking(document)["chunking"]
        _write_json(
            chunk_path,
            {
                "schemaVersion": "document-chunks-demo/v1",
                "documentVersionId": document.document_version_id,
                "maxTokens": 800,
                "chunks": [item.model_dump(mode="json", by_alias=True) for item in chunks],
                "quality": chunk_quality,
            },
        )
        stage_rows.append(
            {
                "stage": "p04_chunking",
                "status": "completed",
                "qualityStatus": chunk_quality["readiness"],
                "reviewRequired": chunk_quality["readiness"] != "ready_for_next_stage",
                "elapsedMs": 0,
                "metrics": {
                    "chunkCount": len(chunks),
                    "maxObservedTokens": chunk_quality["maxObservedTokens"],
                    "sourceCoverage": chunk_quality["sourceCoverage"],
                    "readiness": chunk_quality["readiness"],
                },
                "inputs": [_relative(tree_path, bundle_root)],
                "outputs": [_relative(chunk_path, bundle_root)],
            }
        )
        recorder.record_stage(
            "p04_chunking",
            elapsed_ms=round((time.perf_counter() - chunk_started_perf) * 1000),
            input_paths=[_relative(tree_path, bundle_root)],
            output_paths=[_relative(chunk_path, bundle_root)],
            metrics=stage_rows[-1]["metrics"],
            warnings=[item["message"] for item in chunk_quality.get("issues", [])],
        )

        classification_started_perf = time.perf_counter()
        classifications = classify_document(document)
        classification_path = stages_root / "p05_classification" / "classifications.json"
        review_count = sum(item.needs_human_review for item in classifications)
        classification_metrics = {
            "sectionCount": len(document.sections),
            "classificationCount": len(classifications),
            "humanReviewCount": review_count,
            "humanReviewRatio": round(review_count / len(classifications), 4)
            if classifications
            else 0.0,
            "typeCounts": dict(Counter(item.primary_type for item in classifications)),
        }
        _write_json(
            classification_path,
            {
                "schemaVersion": "section-classification-demo/v1",
                "documentVersionId": document.document_version_id,
                "classifications": [
                    item.model_dump(mode="json", by_alias=True)
                    for item in classifications
                ],
                "metrics": classification_metrics,
            },
        )
        stage_rows.append(
            {
                "stage": "p05_classification",
                "status": "completed",
                "qualityStatus": (
                    "usable_with_review"
                    if classification_metrics["humanReviewRatio"] > 0.6
                    else "ready_for_next_stage"
                ),
                "reviewRequired": review_count > 0,
                "elapsedMs": 0,
                "metrics": classification_metrics,
                "inputs": [_relative(tree_path, bundle_root)],
                "outputs": [_relative(classification_path, bundle_root)],
            }
        )
        recorder.record_stage(
            "p05_classification",
            elapsed_ms=round((time.perf_counter() - classification_started_perf) * 1000),
            input_paths=[_relative(tree_path, bundle_root)],
            output_paths=[_relative(classification_path, bundle_root)],
            metrics=classification_metrics,
        )

        extraction_started_perf = time.perf_counter()
        extraction = extract_requirements(
            document,
            chunks=chunks,
            classifications=classifications,
        )
        extraction_path = stages_root / "p07_requirement_extraction" / "extraction.json"
        _dump_model(extraction_path, extraction)
        extraction_metrics = {
            **extraction.stats,
            "projectFieldCount": len(extraction.project_fields),
            "reviewCount": sum(item.needs_human_review for item in extraction.requirements),
        }
        stage_rows.append(
            {
                "stage": "p07_requirement_extraction",
                "status": "completed",
                "qualityStatus": "ready_for_validation",
                "reviewRequired": extraction_metrics["reviewCount"] > 0,
                "elapsedMs": 0,
                "metrics": extraction_metrics,
                "inputs": [
                    _relative(chunk_path, bundle_root),
                    _relative(classification_path, bundle_root),
                ],
                "outputs": [_relative(extraction_path, bundle_root)],
            }
        )
        recorder.record_stage(
            "p07_requirement_extraction",
            elapsed_ms=round((time.perf_counter() - extraction_started_perf) * 1000),
            input_paths=stage_rows[-1]["inputs"],
            output_paths=stage_rows[-1]["outputs"],
            metrics=extraction_metrics,
            warnings=extraction.warnings,
        )

        validation_started_perf = time.perf_counter()
        validation = validate_extraction_result(document, extraction)
        validation_path = stages_root / "p08_evidence_validation" / "validation.json"
        _dump_model(validation_path, validation)
        validation_metrics = {
            **validation.stats,
            "valid": validation.valid,
            "issueCount": len(validation.issues),
        }
        stage_rows.append(
            {
                "stage": "p08_evidence_validation",
                "status": "completed" if validation.valid else "partial",
                "qualityStatus": (
                    "ready_for_next_stage"
                    if validation.valid
                    else "usable_with_review"
                ),
                "reviewRequired": (
                    not validation.valid
                    or validation.stats.get("warningCount", 0) > 0
                ),
                "elapsedMs": 0,
                "metrics": validation_metrics,
                "inputs": [_relative(extraction_path, bundle_root), _relative(parsed_path, bundle_root)],
                "outputs": [_relative(validation_path, bundle_root)],
            }
        )
        recorder.record_stage(
            "p08_evidence_validation",
            elapsed_ms=round((time.perf_counter() - validation_started_perf) * 1000),
            status="completed" if validation.valid else "partial",
            input_paths=stage_rows[-1]["inputs"],
            output_paths=stage_rows[-1]["outputs"],
            metrics=validation_metrics,
            warnings=[
                issue.message
                for issue in validation.issues
                if issue.severity == "warning"
            ],
            errors=[
                issue.message
                for issue in validation.issues
                if issue.severity == "error"
            ],
        )

        persistence_started_perf = time.perf_counter()
        artifact = build_analysis_artifact(
            project_id=project_id,
            document=document,
            run_id=run_id,
            sections=document.sections,
            chunks=chunks,
            requirements=validation.requirements,
            evidences=validation.evidences,
            warnings=[issue.message for issue in validation.issues],
        )
        artifact_path = stages_root / "p06_fact_persistence" / "analysis_artifact.json"
        _dump_model(artifact_path, artifact)
        artifact_store = FactArtifactStore(runtime_root)
        persisted_path = artifact_store.save_artifact(artifact)
        artifact_metrics = {
            "sectionCount": len(artifact.sections),
            "chunkCount": len(artifact.chunks),
            "requirementCount": len(artifact.requirements),
            "evidenceCount": len(artifact.evidences),
            "artifactId": artifact.artifact_id,
            "persistedPath": _relative(persisted_path, bundle_root),
        }
        stage_rows.append(
            {
                "stage": "p06_fact_persistence",
                "status": "completed",
                "qualityStatus": "ready_for_next_stage",
                "reviewRequired": False,
                "elapsedMs": 0,
                "metrics": artifact_metrics,
                "inputs": [_relative(validation_path, bundle_root)],
                "outputs": [
                    _relative(artifact_path, bundle_root),
                    _relative(persisted_path, bundle_root),
                ],
            }
        )
        recorder.record_stage(
            "p06_fact_persistence",
            elapsed_ms=round((time.perf_counter() - persistence_started_perf) * 1000),
            input_paths=stage_rows[-1]["inputs"],
            output_paths=stage_rows[-1]["outputs"],
            metrics=artifact_metrics,
        )

        matching_started_perf = time.perf_counter()
        company_ledger = _demo_company_ledger()
        match = match_technical_service_requirements(
            validation.requirements,
            company_ledger,
        )
        match_path = stages_root / "p09_technical_service_matching" / "matching.json"
        _dump_model(match_path, match)
        match_metrics = {
            "itemCount": len(match.items),
            "coverage": match.coverage,
            "statusCounts": match.status_counts,
            "hardBlockerCount": len(match.hard_blockers),
            "recommendation": match.recommendation,
            "companyLedgerMode": "synthetic_demo",
        }
        stage_rows.append(
            {
                "stage": "p09_technical_service_matching",
                "status": "completed",
                "qualityStatus": "usable_with_review",
                "reviewRequired": True,
                "elapsedMs": 0,
                "metrics": match_metrics,
                "inputs": [_relative(artifact_path, bundle_root)],
                "outputs": [_relative(match_path, bundle_root)],
            }
        )
        recorder.record_stage(
            "p09_technical_service_matching",
            elapsed_ms=round((time.perf_counter() - matching_started_perf) * 1000),
            input_paths=stage_rows[-1]["inputs"],
            output_paths=stage_rows[-1]["outputs"],
            metrics=match_metrics,
            warnings=match.warnings,
        )

        recorder.set_metrics(
            {
                "sourceFile": str(input_path),
                "documentVersionId": document.document_version_id,
                "stageCount": len(stage_rows),
                "requirementCount": len(validation.requirements),
                "evidenceCount": len(validation.evidences),
                "technicalMatchCount": len(match.items),
                "readiness": {
                    "p03": tree_quality["readiness"],
                    "p04": chunk_quality["readiness"],
                    "p08": "ready_for_next_stage" if validation.valid else "usable_with_review",
                    "p09": "usable_with_review",
                },
            }
        )
        recorder.add_artifacts(
            output_paths=[
                _relative(parsed_path, bundle_root),
                *[
                    output
                    for row in stage_rows
                    for output in row["outputs"]
                ],
            ]
        )
        run = recorder.finish("completed" if validation.valid else "partial")
        run_path = recorder.path
        run_stages = {item.stage: item for item in run.stages}
        for row in stage_rows:
            persisted_stage = run_stages.get(row["stage"])
            if persisted_stage is None:
                continue
            row["status"] = persisted_stage.status
            row["elapsedMs"] = persisted_stage.elapsed_ms
        _dump_model(bundle_root / "run.json", run)

        manifest = {
            "schemaVersion": "document-pipeline-demo/v1",
            "runId": run_id,
            "projectId": project_id,
            "documentVersionId": document.document_version_id,
            "source": {
                "path": str(input_path),
                "fileName": input_path.name,
                "fileHash": document.file_hash,
            },
            "mode": {
                "network": "offline",
                "llm": "disabled",
                "companyLedger": "synthetic_demo",
            },
            "nodes": [
                {
                    "nodeId": row["stage"],
                    "dependsOn": NODE_DEPENDENCIES[row["stage"]],
                    "inputs": row["inputs"],
                    "status": row["status"],
                    "executionStatus": row["status"],
                    "qualityStatus": row["qualityStatus"],
                    "reviewRequired": row["reviewRequired"],
                    "outputs": row["outputs"],
                    "metrics": row["metrics"],
                }
                for row in stage_rows
            ],
            "runRecord": _relative(run_path, bundle_root),
            "readme": "README.md",
        }
        _write_json(bundle_root / "manifest.json", manifest)
        _write_bundle_readme(
            bundle_root,
            input_path=input_path,
            document=document,
            run_id=run_id,
            stage_rows=stage_rows,
            final_match=match,
        )
        return {
            "runId": run_id,
            "projectId": project_id,
            "documentVersionId": document.document_version_id,
            "source": str(input_path),
            "bundleRoot": str(bundle_root),
            "readme": str(bundle_root / "README.md"),
            "manifest": str(bundle_root / "manifest.json"),
            "run": str(bundle_root / "run.json"),
            "stages": stage_rows,
            "final": {
                "technicalMatchCount": len(match.items),
                "coverage": match.coverage,
                "recommendation": match.recommendation,
                "statusCounts": match.status_counts,
            },
        }
    except Exception:
        if recorder is not None and not recorder.run.finished_at:
            recorder.finish("failed")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="要演示的 PDF/DOC/DOCX 文件；未提供时仅尝试读取本地 demo-data。",
    )
    parser.add_argument(
        "--runtime-root",
        type=Path,
        default=DEFAULT_RUNTIME_ROOT,
    )
    parser.add_argument("--project-id", default=PROJECT_ID)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()
    result = run_pipeline(
        args.input.resolve() if args.input else _default_input(),
        runtime_root=args.runtime_root.resolve(),
        project_id=args.project_id,
        run_id=args.run_id,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
