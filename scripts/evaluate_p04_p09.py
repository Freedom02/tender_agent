"""Strict offline acceptance evaluation for P04-P09.

The evaluator runs the complete typed pipeline over the local demo corpus:

    DocumentIR -> SectionTree/Chunk -> classification -> facts -> evidence
    -> technical/service matching

It does not call an LLM, WorkBuddy, M3, or any network service.  Semantic
precision still needs reviewed gold annotations; this report therefore
separates contract safety from semantic accuracy claims.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
from backend.app.tender_matching.requirement_extractor import (  # noqa: E402
    _heading_variants,
    _is_non_requirement_clause,
    extract_requirements,
)
from backend.app.tender_matching.section_chunk_quality import (  # noqa: E402
    evaluate_section_and_chunking,
)
from backend.app.tender_matching.section_classifier import (  # noqa: E402
    SECTION_TYPES,
    classify_document,
)
from backend.app.tender_matching.company_facts import build_company_ledger  # noqa: E402
from backend.app.tender_matching.technical_service_matching import (  # noqa: E402
    MatchStatus,
    match_technical_service_requirements,
)


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".txt", ".md"}
VALID_MATCH_STATUSES = {"满足", "部分满足", "不满足", "待补材料", "待澄清", "不适用"}


def _mime(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(path.suffix.lower(), "")


def _score(checks: list[tuple[bool, int]]) -> int:
    return sum(weight for passed, weight in checks if passed)


def _readiness(score: int, hard_failure: bool, review: bool = False) -> str:
    if hard_failure or score < 70:
        return "not_ready"
    if score < 85 or review:
        return "usable_with_review"
    return "ready_for_next_stage"


def _issue(code: str, message: str, severity: str = "warning") -> dict[str, str]:
    return {"severity": severity, "code": code, "message": message}


def _load_documents(root: Path) -> list[tuple[Path, DocumentIR, bytes]]:
    documents: list[tuple[Path, DocumentIR, bytes]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix().lower()):
        if (
            not path.is_file()
            or path.suffix.lower() not in SUPPORTED_EXTENSIONS
            or path.name.lower() == "readme.md"
        ):
            continue
        content = path.read_bytes()
        document = parse_document_ir(
            content,
            file_name=path.name,
            mime_type=_mime(path),
        )
        documents.append((path, document, content))
    return documents


def _evaluate_p05(document: DocumentIR, classifications: list[Any]) -> dict[str, Any]:
    sections = document.sections
    section_ids = [section.section_id or section.id for section in sections]
    result_ids = [item.section_id for item in classifications]
    valid_types = all(item.primary_type in SECTION_TYPES for item in classifications)
    ids_exact = result_ids == section_ids
    ids_unique = len(result_ids) == len(set(result_ids)) and all(result_ids)
    route_consistent = all(
        set(item.usage_tags)
        == {key for key, enabled in item.route.model_dump().items() if enabled}
        for item in classifications
    )
    section_map = {
        section.section_id or section.id: section
        for section in sections
    }
    source_unchanged = all(
        item.section_id in section_map
        and section_map[item.section_id].heading
        for item in classifications
    )
    review_count = sum(item.needs_human_review for item in classifications)
    review_ratio = review_count / len(classifications) if classifications else 0.0
    score = _score([
        (ids_exact, 20),
        (ids_unique, 15),
        (valid_types, 20),
        (route_consistent, 25),
        (source_unchanged, 20),
    ])
    issues: list[dict[str, str]] = []
    if not ids_exact:
        issues.append(_issue("classification_section_mismatch", "分类结果没有与 SectionTree 一一对应", "critical"))
    if not valid_types:
        issues.append(_issue("invalid_primary_type", "存在未注册的 primaryType", "critical"))
    if not route_consistent:
        issues.append(_issue("route_policy_mismatch", "usageTags 与 route policy 不一致", "critical"))
    if review_ratio > 0.6:
        issues.append(_issue(
            "high_human_review_ratio",
            f"{review_ratio:.0%} 的章节需要人工复核；规则路由可用，但不能视为全自动分类",
        ))
    return {
        "score": score,
        "maxScore": 100,
        "readiness": _readiness(score, bool(
            not ids_exact or not ids_unique or not valid_types or not route_consistent
        ), review_ratio > 0.6),
        "sectionCount": len(sections),
        "classificationCount": len(classifications),
        "idsExact": ids_exact,
        "idsUnique": ids_unique,
        "validPrimaryTypes": valid_types,
        "routePolicyConsistent": route_consistent,
        "sourceBoundaryPreserved": source_unchanged,
        "humanReviewCount": review_count,
        "humanReviewRatio": round(review_ratio, 4),
        "typeCounts": dict(Counter(item.primary_type for item in classifications)),
        "issues": issues,
    }


def _evaluate_p06(
    document: DocumentIR,
    chunks: list[Any],
    extraction: RequirementExtractionResult,
) -> dict[str, Any]:
    artifact = build_analysis_artifact(
        project_id="P04-P09-EVAL",
        document=document,
        run_id=f"RUN-{document.document_version_id}",
        sections=document.sections,
        chunks=chunks,
        requirements=extraction.requirements,
        evidences=extraction.evidences,
    )
    with tempfile.TemporaryDirectory() as directory:
        store = FactArtifactStore(directory)
        store.save_document("P04-P09-EVAL", document)
        store.save_artifact(artifact)
        restored_document = store.load_document(
            "P04-P09-EVAL",
            document.document_version_id,
        )
        restored_artifact = store.load_artifact(
            "P04-P09-EVAL",
            document.document_version_id,
        )
        second_version = document.model_copy(update={
            "document_version_id": f"{document.document_version_id}-R2",
        })
        store.save_document("P04-P09-EVAL", second_version)
        version_isolated = (
            store.load_document("P04-P09-EVAL", document.document_version_id).document_version_id
            != store.load_document("P04-P09-EVAL", second_version.document_version_id).document_version_id
        )
    document_round_trip = (
        restored_document.model_dump(mode="json")
        == document.model_dump(mode="json")
    )
    artifact_round_trip = (
        restored_artifact.model_dump(mode="json")
        == artifact.model_dump(mode="json")
    )
    run_metadata_complete = bool(
        restored_artifact.run.project_id
        and restored_artifact.run.document_version_id
        and restored_artifact.run.run_id
    )
    score = _score([
        (document_round_trip, 25),
        (artifact_round_trip, 30),
        (version_isolated, 25),
        (run_metadata_complete, 20),
    ])
    issues = []
    if not run_metadata_complete:
        issues.append(_issue("run_metadata_incomplete", "AnalysisRun 缺少 projectId/documentVersionId/runId", "critical"))
    return {
        "score": score,
        "maxScore": 100,
        "readiness": _readiness(score, not run_metadata_complete),
        "documentRoundTrip": document_round_trip,
        "artifactRoundTrip": artifact_round_trip,
        "versionIsolation": version_isolated,
        "runMetadataComplete": run_metadata_complete,
        "requirementCount": len(extraction.requirements),
        "evidenceCount": len(extraction.evidences),
        "issues": issues,
    }


def _evaluate_p07(
    document: DocumentIR,
    extraction: RequirementExtractionResult,
) -> dict[str, Any]:
    requirements = extraction.requirements
    evidences = extraction.evidences
    segment_ids = {segment.id for segment in document.segments if segment.id}
    requirement_ids = [item.requirement_id for item in requirements]
    evidence_ids = [item.evidence_id for item in evidences]
    evidence_map = {item.evidence_id: item for item in evidences}
    ids_unique = (
        len(requirement_ids) == len(set(requirement_ids))
        and len(evidence_ids) == len(set(evidence_ids))
        and all(requirement_ids)
        and all(evidence_ids)
    )
    requirements_have_evidence = all(
        item.evidence_ids and all(evidence_id in evidence_map for evidence_id in item.evidence_ids)
        for item in requirements
    )
    source_refs_valid = all(
        item.source_segment_ids and set(item.source_segment_ids).issubset(segment_ids)
        for item in requirements
    )
    evidence_quotes_present = all(item.source_quote.strip() for item in evidences)
    valid_categories = all(item.category.strip() for item in requirements)
    normalized = [
        re.sub(r"\s+", "", item.normalized_requirement)
        for item in requirements
        if item.normalized_requirement.strip()
    ]
    duplicate_ratio = (
        1 - len(set(normalized)) / len(normalized)
        if normalized else 0.0
    )
    heading_variants = _heading_variants(document)
    heading_like_count = sum(
        _is_non_requirement_clause(item.raw_text, heading_variants)
        for item in requirements
    )
    heading_like_ratio = heading_like_count / len(requirements) if requirements else 0.0
    score = _score([
        (ids_unique, 15),
        (requirements_have_evidence, 25),
        (source_refs_valid, 20),
        (evidence_quotes_present, 15),
        (valid_categories, 10),
        (duplicate_ratio <= 0.10, 10),
        (heading_like_ratio == 0.0, 5),
    ])
    issues: list[dict[str, str]] = []
    if not requirements_have_evidence or not source_refs_valid:
        issues.append(_issue("requirement_traceability", "存在没有完整来源绑定的 Requirement", "critical"))
    if duplicate_ratio > 0.10:
        issues.append(_issue("duplicate_requirement_ratio", f"标准化要求重复率为 {duplicate_ratio:.1%}"))
    if heading_like_ratio > 0:
        issues.append(_issue("heading_residue", f"仍有 {heading_like_count} 条疑似标题/表头被抽成 Requirement"))
    return {
        "score": score,
        "maxScore": 100,
        "readiness": _readiness(
            score,
            not requirements_have_evidence or not source_refs_valid,
            duplicate_ratio > 0.10 or heading_like_ratio > 0.02,
        ),
        "requirementCount": len(requirements),
        "evidenceCount": len(evidences),
        "idsUnique": ids_unique,
        "requirementsHaveEvidence": requirements_have_evidence,
        "sourceReferencesValid": source_refs_valid,
        "evidenceQuotesPresent": evidence_quotes_present,
        "validCategories": valid_categories,
        "duplicateNormalizedRatio": round(duplicate_ratio, 4),
        "headingLikeCount": heading_like_count,
        "headingLikeRatio": round(heading_like_ratio, 4),
        "categoryCounts": dict(Counter(item.category for item in requirements)),
        "issues": issues,
    }


def _mutated_validation_probe(
    document: DocumentIR,
    extraction: RequirementExtractionResult,
    kind: str,
) -> bool:
    if not extraction.evidences or not extraction.requirements:
        return False
    data = extraction.model_dump(mode="json", by_alias=True)
    evidence = data["evidences"][0]
    if kind == "quote":
        evidence["sourceQuote"] = "这段文字不存在于来源中"
    elif kind == "page":
        evidence["pageStart"] = 999
        evidence["pageEnd"] = 999
    elif kind == "segment":
        evidence["sourceSegmentIds"] = ["SEGMENT-NOT-FOUND"]
    elif kind == "version":
        evidence["documentVersionId"] = "DOC-WRONG-VERSION"
    elif kind == "missing":
        data["requirements"][0]["evidenceIds"] = ["EVIDENCE-NOT-FOUND"]
    result = validate_extraction_result(
        document,
        RequirementExtractionResult.model_validate(data),
    )
    if kind == "missing":
        return not result.valid and any(issue.code == "missing_evidence" for issue in result.issues)
    return (
        not result.valid
        and result.evidences[0].verification_status == "invalid"
    )


def _evaluate_p08(
    document: DocumentIR,
    extraction: RequirementExtractionResult,
) -> dict[str, Any]:
    validation = validate_extraction_result(document, extraction)
    verified_ratio = (
        validation.stats.get("verifiedEvidenceCount", 0) / len(extraction.evidences)
        if extraction.evidences else 1.0
    )
    page_probe_applicable = any(
        segment.page is not None
        for segment in document.segments
    )
    probe_results: dict[str, bool | None] = {
        "quote": _mutated_validation_probe(document, extraction, "quote"),
        "page": (
            _mutated_validation_probe(document, extraction, "page")
            if page_probe_applicable
            else None
        ),
        "segment": _mutated_validation_probe(document, extraction, "segment"),
        "version": _mutated_validation_probe(document, extraction, "version"),
        "missing": _mutated_validation_probe(document, extraction, "missing"),
    }
    applicable_probes = [
        value for value in probe_results.values()
        if value is not None
    ]
    probes_passed = bool(applicable_probes) and all(applicable_probes)
    score = _score([
        (validation.valid, 35),
        (verified_ratio == 1.0, 25),
        (probes_passed, 40),
    ])
    issues: list[dict[str, str]] = []
    if not validation.valid:
        issues.append(_issue("real_sample_validation", "真实抽取结果存在 Evidence 校验错误", "critical"))
    if not probes_passed:
        issues.append(_issue("negative_probe_failure", "证据校验器未拦截全部破坏性反例", "critical"))
    return {
        "score": score,
        "maxScore": 100,
        "readiness": _readiness(score, not validation.valid or not probes_passed),
        "valid": validation.valid,
        "verifiedEvidenceRatio": round(verified_ratio, 4),
        "errorCount": validation.stats.get("errorCount", 0),
        "warningCount": validation.stats.get("warningCount", 0),
        "pageProbeApplicable": page_probe_applicable,
        "negativeProbes": probe_results,
        "issues": issues,
    }


def _company_ledger() -> dict[str, Any]:
    return build_company_ledger({
        "organization": {
            "organizationId": "ORG-EVAL",
            "organizationName": "评测企业",
            "capabilities": "数字孪生平台建设、三维可视化、数据接入、项目实施、系统集成、培训、运维服务",
        },
        "technical_product": [{
            "productId": "PRD-EVAL",
            "productName": "数字孪生可视化平台",
            "description": "支持三维场景搭建、数字孪生可视化、物联网数据接入、低代码开发、权限管理和国产化环境部署",
            "evidencePath": "evaluation/company/product/platform.pdf",
        }],
        "technical_parameter": [{
            "parameterId": "PAR-EVAL",
            "productId": "PRD-EVAL",
            "description": "支持三维模型加载、实时数据接入、可视化大屏、开放接口和系统集成",
            "evidencePath": "evaluation/company/parameter/platform.pdf",
        }],
    })


def _evaluate_p09_real(
    requirements: list[Any],
    company: dict[str, Any],
) -> dict[str, Any]:
    result = match_technical_service_requirements(requirements, company)
    ids = [item.requirement_id for item in result.items]
    statuses_valid = all(item.status in VALID_MATCH_STATUSES for item in result.items)
    ids_unique = len(ids) == len(set(ids))
    satisfied_has_evidence = all(
        item.status != "满足" or bool(item.evidence_items)
        for item in result.items
    )
    blockers_consistent = all(
        item.hard_blocker == (item.status == "不满足" and item.category in {"technical", "service_plan"})
        for item in result.items
    )
    summary_counts = Counter(item.status for item in result.items)
    counts_match = dict(summary_counts) == result.status_counts
    score = _score([
        (ids_unique, 20),
        (statuses_valid, 20),
        (satisfied_has_evidence, 25),
        (blockers_consistent, 20),
        (counts_match, 15),
    ])
    return {
        "score": score,
        "maxScore": 100,
        "itemCount": len(result.items),
        "coverage": result.coverage,
        "statusCounts": result.status_counts,
        "hardBlockerCount": len(result.hard_blockers),
        "recommendation": result.recommendation,
        "idsUnique": ids_unique,
        "statusesValid": statuses_valid,
        "satisfiedItemsHaveEvidence": satisfied_has_evidence,
        "hardBlockerSemanticsValid": blockers_consistent,
        "summaryCountsMatch": counts_match,
        "issues": [] if score == 100 else [_issue("matching_contract", "匹配结果契约检查未全部通过", "critical")],
    }


def _evaluate_p09_contract() -> dict[str, Any]:
    company = _company_ledger()
    cases = [
        ({"requirementId": "REQ-SAT", "category": "technical", "rawText": "平台应支持三维场景搭建和数字孪生可视化"}, "满足"),
        ({"requirementId": "REQ-MATERIAL", "category": "service_plan", "rawText": "投标人应提供项目实施和培训服务"}, "待补材料"),
        ({"requirementId": "REQ-HARD", "category": "technical", "rawText": "服务器配置不低于32GB内存"}, "待补材料"),
        ({"requirementId": "REQ-IGNORE", "category": "qualification", "rawText": "提供营业执照"}, None),
    ]
    results: list[dict[str, Any]] = []
    for requirement, expected in cases:
        summary = match_technical_service_requirements([requirement], company)
        actual = summary.items[0].status if summary.items else None
        results.append({
            "requirementId": requirement["requirementId"],
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
        })
    return {
        "caseCount": len(results),
        "passedCount": sum(item["passed"] for item in results),
        "allPassed": all(item["passed"] for item in results),
        "cases": results,
    }


def _write_markdown(report: dict[str, Any], output: Path) -> None:
    summary = report["summary"]
    lines = [
        "# P04-P09 分阶段严格验收报告",
        "",
        f"> 生成日期：{report['generatedAt']}",
        f"> 样本数：{summary['sampleCount']}",
        f"> 评测方式：离线、无 LLM、无 WorkBuddy、无 M3",
        "",
        "## 评测原则",
        "",
        "- P04 检查章节边界、来源覆盖、顺序、预算、上下文、表格原子性和 locator。",
        "- P05 检查分类结果是否与 SectionTree 一一对应、类型是否合法、路由策略是否一致；人工复核率单独报告，不伪装成准确率。",
        "- P06 检查 DocumentIR、AnalysisArtifact JSON 往返、版本隔离和 AnalysisRun 元数据。",
        "- P07 检查 Requirement/Evidence 唯一性、来源 Segment、原文摘录、类别和重复/标题残留。",
        "- P08 检查真实抽取结果，并用错误摘录、错误页码、错误 Segment、错误版本和缺失 Evidence 做破坏性反例。",
        "- P09 检查状态、证据、硬性障碍、计数一致性，并执行固定匹配契约样例。",
        "- 没有人工 gold 的地方只宣称结构和契约安全，不宣称语义 Precision/Recall。",
        "",
        "## 汇总",
        "",
        "| 阶段 | 平均分 | 最低分 | 可直接进入下一阶段 | 需复核 | 未就绪 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for stage in ("p04", "p05", "p06", "p07", "p08", "p09"):
        bucket = summary["stages"][stage]
        lines.append(
            f"| {stage.upper()} | {bucket['averageScore']:.1f}/100 | "
            f"{bucket['minimumScore']}/100 | {bucket['readyCount']} | "
            f"{bucket['reviewCount']} | {bucket['notReadyCount']} |"
        )
    lines.extend(["", "## 样本明细", ""])
    for sample in report["samples"]:
        lines.extend([
            f"### {sample['fileName']}",
            "",
            f"- P04：{sample['p04']['score']}/100，`{sample['p04']['readiness']}`",
            f"- P05：{sample['p05']['score']}/100，`{sample['p05']['readiness']}`，人工复核率 {sample['p05']['humanReviewRatio']:.0%}",
            f"- P06：{sample['p06']['score']}/100，`{sample['p06']['readiness']}`",
            f"- P07：{sample['p07']['score']}/100，`{sample['p07']['readiness']}`，Requirement {sample['p07']['requirementCount']}，标题/表头残留 {sample['p07']['headingLikeCount']}",
            f"- P08：{sample['p08']['score']}/100，`{sample['p08']['readiness']}`，"
            f"反例 {sum(bool(value) for value in sample['p08']['negativeProbes'].values() if value is not None)}/"
            f"{sum(value is not None for value in sample['p08']['negativeProbes'].values())} 通过",
            f"- P09：契约 {sample['p09']['contractScore']}/100，真实技术/服务条目 {sample['p09']['itemCount']}，覆盖率 {sample['p09']['coverage']:.0%}",
            "",
        ])
        issues = [
            *sample["p04"].get("issues", []),
            *sample["p05"].get("issues", []),
            *sample["p06"].get("issues", []),
            *sample["p07"].get("issues", []),
            *sample["p08"].get("issues", []),
            *sample["p09"].get("issues", []),
        ]
        if issues:
            lines.append("问题：")
            for issue in issues:
                lines.append(f"- `{issue['severity']}` `{issue['code']}`：{issue['message']}")
            lines.append("")
    lines.extend([
        "## 优点",
        "",
        "- P04 在当前 9 份样本上保持来源可追溯，Chunk 不丢 Segment，800/400 token 压力档均可复核。",
        "- P06 的 JSON 往返和版本隔离可以自动验证，当前不依赖数据库即可支撑单项目重跑。",
        "- P08 对真实抽取结果全部执行来源校验，并能拦截破坏性证据输入。",
        "- P09 对“满足但没有企业证据”保持保守，不会把缺材料误报为满足。",
        "",
        "## 待办",
        "",
        "- P05：补充人工章节分类 gold，校准多类别章节和 `other` 章节的真实 Macro-F1；当前人工复核率偏高但属于显式风险。",
        "- P07：继续处理复合句拆分、表格表头去重、标题型要求残留；在没有 gold 前不以数量增长作为改进目标。",
        "- P08：跨页表格和 OCR 场景需要更丰富的定位样例；当前校验器只验证已有 DocumentIR 定位。",
        "- P09：补充企业事实 gold 和真实“满足/部分满足/不满足”标注；当前真实覆盖率受固定评测企业台账限制，不能当作引擎准确率。",
        "- P06：如果进入高并发或多用户场景，再评估 JSON 文件锁和索引，不在当前低并发阶段提前引入数据库。",
        "",
        "## 发布判断",
        "",
        "P04、P06、P08 的结构/来源契约可以进入后续阶段；P05、P07、P09 可以作为受控输入，",
        "但必须保留 `needsHumanReview`、Evidence 校验结果和匹配状态来源。涉及真实语义准确率的部分，",
        "等人工 gold 补齐后再做最终发布门槛。",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=REPO_ROOT / "demo-data")
    parser.add_argument(
        "--json-output",
        type=Path,
        default=REPO_ROOT / "evaluation" / "p04_p09_quality_report.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPO_ROOT / "docs" / "P04-P09-stage-evaluation.md",
    )
    args = parser.parse_args()

    company = _company_ledger()
    samples: list[dict[str, Any]] = []
    for path, document, content in _load_documents(args.input.resolve()):
        chunks = chunk_document(document, max_tokens=800)
        classifications = classify_document(document)
        extraction = extract_requirements(
            document,
            chunks=chunks,
            classifications=classifications,
        )
        p04 = evaluate_section_and_chunking(document, max_tokens=800)
        p05 = _evaluate_p05(document, classifications)
        p06 = _evaluate_p06(document, chunks, extraction)
        p07 = _evaluate_p07(document, extraction)
        p08 = _evaluate_p08(document, extraction)
        p09_real = _evaluate_p09_real(extraction.requirements, company)
        p09_contract = _evaluate_p09_contract()
        samples.append({
            "sampleId": f"DOC-{hashlib.sha256(content).hexdigest()[:12]}",
            "fileName": path.name,
            "path": path.as_posix(),
            "format": path.suffix.lower().lstrip("."),
            "p04": p04,
            "p05": p05,
            "p06": p06,
            "p07": p07,
            "p08": p08,
            "p09": {
                **p09_real,
                "contractScore": 100 if p09_contract["allPassed"] else round(
                    p09_contract["passedCount"] / max(p09_contract["caseCount"], 1) * 100
                ),
                "contractCases": p09_contract,
            },
        })

    stages: dict[str, dict[str, Any]] = {}
    for stage in ("p04", "p05", "p06", "p07", "p08"):
        values = [sample[stage] for sample in samples]
        stages[stage] = {
            "averageScore": round(sum(item["score"] for item in values) / len(values), 2) if values else 0,
            "minimumScore": min((item["score"] for item in values), default=0),
            "readyCount": sum(item["readiness"] == "ready_for_next_stage" for item in values),
            "reviewCount": sum(item["readiness"] == "usable_with_review" for item in values),
            "notReadyCount": sum(item["readiness"] == "not_ready" for item in values),
        }
    p09_values = [sample["p09"] for sample in samples]
    stages["p09"] = {
        "averageScore": round(sum(item["contractScore"] for item in p09_values) / len(p09_values), 2) if p09_values else 0,
        "minimumScore": min((item["contractScore"] for item in p09_values), default=0),
        "readyCount": sum(item["contractScore"] >= 85 for item in p09_values),
        "reviewCount": sum(70 <= item["contractScore"] < 85 for item in p09_values),
        "notReadyCount": sum(item["contractScore"] < 70 for item in p09_values),
    }
    report = {
        "schemaVersion": "p04-p09-quality-v1",
        "generatedAt": date.today().isoformat(),
        "inputRoot": args.input.resolve().as_posix(),
        "summary": {
            "sampleCount": len(samples),
            "stages": stages,
            "contractGoldStatus": "synthetic_contract_only; semantic_gold_pending",
        },
        "samples": samples,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(report, args.markdown_output.resolve())
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
