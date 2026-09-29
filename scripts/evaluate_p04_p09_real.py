"""Run a real P04-P09 evaluation with the configured LLM and M3 services.

This evaluator intentionally keeps the offline evaluator unchanged.  It is a
second lane for acceptance testing:

    python scripts/evaluate_p04_p09_real.py

The report records provider/model/source telemetry and treats silent fallback
as a failed online check.  It does not write business facts to M3; it only
reads the configured enterprise ledger.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.config.service import ensure_config  # noqa: E402
from backend.app.m3.client import M3Client  # noqa: E402
from backend.app.m3.sync import read_facts_from_m3  # noqa: E402
from backend.app.tender_matching.company_facts import build_company_ledger  # noqa: E402
from backend.app.tender_matching.document_chunking import chunk_document  # noqa: E402
from backend.app.tender_matching.document_matching import evaluate_tender_document  # noqa: E402
from backend.app.tender_matching.document_models import document_ir_to_json  # noqa: E402
from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402
from backend.app.tender_matching.eligibility import call_openai_compatible  # noqa: E402
from backend.app.tender_matching.fact_models import (  # noqa: E402
    FactArtifactStore,
    RequirementExtractionResult,
    build_analysis_artifact,
)
from backend.app.tender_matching.evidence_validation import (  # noqa: E402
    validate_extraction_result,
)
from backend.app.tender_matching.section_classifier import (  # noqa: E402
    LLM_SECTION_CLASSIFICATION_PROMPT,
    LLMSectionClassification,
    SECTION_TYPES,
    classify_document,
    needs_llm_classification_review,
)
from backend.app.tender_matching.section_chunk_quality import (  # noqa: E402
    evaluate_section_and_chunking,
)
from backend.app.tender_matching.technical_service_matching import (  # noqa: E402
    match_technical_service_requirements,
)

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".txt", ".md"}
DEFAULT_REPORT = REPO_ROOT / "evaluation" / "p04_p09_real_quality_report.json"
DEFAULT_MARKDOWN = REPO_ROOT / "docs" / "P04-P09-real-evaluation.md"


def _load_env() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def _config() -> dict[str, Any]:
    raw: dict[str, Any] = {}
    config_path = REPO_ROOT / "agent-config.json"
    if config_path.exists():
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    result = ensure_config(raw)
    ai = result["ai"]
    ai["enabled"] = True
    ai["baseUrl"] = str(ai.get("baseUrl") or os.getenv("AI_BASE_URL", "")).strip()
    ai["apiKey"] = str(ai.get("apiKey") or os.getenv("AI_API_KEY", "")).strip()
    ai["organizationId"] = str(
        ai.get("organizationId") or os.getenv("AI_ORGANIZATION", "")
    ).strip()
    ai["model"] = str(ai.get("model") or os.getenv("AI_MODEL", "")).strip()
    ai["extractionModel"] = str(ai.get("extractionModel") or ai["model"]).strip()
    ai["matchingModel"] = str(ai.get("matchingModel") or ai["model"]).strip()
    # This lane tests the builtin direct configured LLM. The independent
    # WorkBuddy + Agent MCP lane is evaluated by its own document-map tests.
    ai["extractionMode"] = "builtin"
    return result


def _m3_client(config: dict[str, Any]) -> M3Client:
    m3 = config.get("m3") or {}
    return M3Client(
        host=str(m3.get("host") or os.getenv("M3_HOST", "")),
        company=str(m3.get("company") or os.getenv("M3_COMPANY", "")),
        username=str(m3.get("username") or os.getenv("M3_USER", "")),
        password=str(m3.get("password") or os.getenv("M3_PASSWORD", "")),
        namespace=str(m3.get("namespace") or "tenderhub"),
    )


def _paths(root: Path, limit: int) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED_EXTENSIONS else []
    paths = sorted(
        (
            item for item in root.rglob("*")
            if item.is_file()
            and item.suffix.lower() in SUPPORTED_EXTENSIONS
            and item.name.lower() != "readme.md"
        ),
        key=lambda item: item.as_posix().lower(),
    )
    return paths[:limit] if limit > 0 else paths


def _json_object(content: str) -> dict[str, Any]:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            return {}


def _json_value(content: str) -> Any:
    text = str(content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"(\{.*\}|\[.*\])", text, flags=re.DOTALL)
        if not match:
            return None
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            return None


def _section_classifier_reason(section: Any, classification: Any) -> str:
    reasons: list[str] = []
    if bool(getattr(classification, "needs_human_review", False)):
        reasons.append("needs_human_review")
    if len(getattr(classification, "type_hits", []) or []) > 1:
        reasons.append("multi_type_hits")
    if needs_llm_classification_review(section, classification):
        reasons.append("risk_or_low_confidence")
    return "+".join(dict.fromkeys(reasons)) or "review"


def _classification_values(body: dict[str, Any]) -> list[Any]:
    values = body.get("classifications")
    if isinstance(values, list):
        return values
    for key in ("items", "results", "data"):
        fallback = body.get(key)
        if isinstance(fallback, list):
            return fallback
    return []


def _classification_values_from_content(content: str) -> list[Any]:
    value = _json_value(content)
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return _classification_values(value)
    return []


def _classify_document_with_batched_llm(document: Any, ai: dict[str, Any]) -> tuple[list[Any], dict[str, Any]]:
    baseline = classify_document(document)
    section_by_id = {
        section.section_id or section.id: section
        for section in document.sections
    }
    pending = [
        item for item in baseline
        if item.section_id in section_by_id
        and needs_llm_classification_review(section_by_id[item.section_id], item)
    ]
    telemetry: dict[str, Any] = {
        "candidateSectionCount": len(pending),
        "batchCount": 0,
        "successBatchCount": 0,
        "failureBatchCount": 0,
        "classifiedSectionCount": 0,
        "candidateReasonCounts": dict(Counter(
            _section_classifier_reason(section_by_id[item.section_id], item)
            for item in pending
        )),
        "lastError": "",
    }
    resolved: dict[str, dict[str, Any]] = {}
    valid_type_text = "、".join(SECTION_TYPES)
    batch_size = 18
    for offset in range(0, len(pending), batch_size):
        batch = pending[offset : offset + batch_size]
        telemetry["batchCount"] += 1
        payload = []
        for item in batch:
            section = section_by_id[item.section_id]
            payload.append({
                "sectionId": item.section_id,
                "heading": section.heading,
                "sectionPath": section.section_path,
                "rulePrimaryType": item.primary_type,
                "ruleTypeHits": item.type_hits,
                "ruleConfidence": item.confidence,
                "reviewReason": _section_classifier_reason(section, item),
                "textExcerpt": re.sub(r"\s+", " ", section.text).strip()[:900],
            })
        result = call_openai_compatible(
            {**ai, "model": ai.get("extractionModel") or ai.get("model")},
            [
                {
                    "role": "system",
                    "content": (
                        f"{LLM_SECTION_CLASSIFICATION_PROMPT}\n"
                        "这是下游路由分类，不是单纯关键词打标。一个章节可以有多个 typeHits，"
                        "但 primaryType 必须选择最适合下游消费的主路由。\n"
                        f"合法类型只能从以下集合选择：{valid_type_text}。\n"
                        "特别注意：\n"
                        "1. 位于电子投标文件格式、投标函、响应表、报价明细表的章节，即使提到技术/商务，"
                        "primaryType 通常仍是 document_format，technical/commercial 只能作为 typeHits。\n"
                        "2. 投标无效、废标、否决、不合格、超过预算/最高限价、未签章等条款，"
                        "primaryType 通常是 rejection。\n"
                        "3. 第五章/采购内容及要求的总览章节可 primaryType=scope，同时保留 technical、"
                        "commercial、qualification 等 typeHits，并 needsHumanReview=true。\n"
                        "4. 不要新增、删除或改写 sectionId。\n"
                        "只返回 JSON 对象，格式为："
                        "{\"classifications\":[{\"sectionId\":\"...\",\"primaryType\":\"technical\","
                        "\"typeHits\":[\"technical\"],\"confidence\":0.8,"
                        "\"rationale\":\"简短理由\",\"needsHumanReview\":false}]}。"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            max_tokens=3600,
            temperature=0,
            response_format={"type": "json_object"},
        )
        if not result.get("success"):
            telemetry["failureBatchCount"] += 1
            telemetry["lastError"] = str(result.get("error") or "batch classification failed")
            continue
        values = _classification_values_from_content(str(result.get("content") or ""))
        if not values:
            telemetry["failureBatchCount"] += 1
            telemetry["lastError"] = "batch classifier response has no classifications array"
            continue
        valid_ids = {item.section_id for item in batch}
        accepted = 0
        for value in values:
            if not isinstance(value, dict):
                continue
            section_id = str(value.get("sectionId") or "")
            if section_id not in valid_ids:
                continue
            try:
                parsed = LLMSectionClassification.model_validate(value)
            except Exception:
                continue
            resolved[section_id] = parsed.model_dump(by_alias=True)
            accepted += 1
        if accepted:
            telemetry["successBatchCount"] += 1
            telemetry["classifiedSectionCount"] += accepted
        else:
            telemetry["failureBatchCount"] += 1
            telemetry["lastError"] = "batch classifier returned no valid section results"

    def lookup(request: Any) -> dict[str, Any]:
        value = resolved.get(request.section_id)
        if value is None:
            raise LookupError("no batched LLM classification available")
        return value

    return classify_document(document, llm_classifier=lookup), telemetry


def _online_status(fine_review: dict[str, Any]) -> dict[str, Any]:
    extraction = fine_review.get("extraction")
    extraction = extraction if isinstance(extraction, dict) else {}
    stats = extraction.get("stats")
    stats = stats if isinstance(stats, dict) else {}
    provider = str(extraction.get("provider") or stats.get("extractionProvider") or "")
    method = str(extraction.get("method") or "")
    model = str(extraction.get("modelUsed") or "")
    warnings = list(extraction.get("warnings") or [])
    llm_used = provider == "builtin-llm" and bool(model)
    return {
        "provider": provider,
        "method": method,
        "model": model,
        "llmUsed": llm_used,
        "llmModelPresent": bool(model),
        "silentRuleFallback": provider == "builtin-rule" and not warnings,
        "explicitRuleFallback": provider == "builtin-rule" and bool(warnings),
        "warnings": warnings[:8],
        "stats": stats,
    }


def _evaluate_p06(document: Any, chunks: list[Any], fine_review: dict[str, Any]) -> dict[str, Any]:
    extraction = RequirementExtractionResult(
        documentVersionId=document.document_version_id,
        requirements=[],
        evidences=[],
    )
    artifact = build_analysis_artifact(
        project_id="REAL-P04-P09",
        document=document,
        run_id=f"RUN-{document.document_version_id}",
        sections=document.sections,
        chunks=chunks,
        requirements=extraction.requirements,
        evidences=extraction.evidences,
    )
    with __import__("tempfile").TemporaryDirectory() as directory:
        store = FactArtifactStore(directory)
        store.save_document("REAL-P04-P09", document)
        store.save_artifact(artifact)
        restored = store.load_artifact("REAL-P04-P09", document.document_version_id)
    return {
        "score": 100 if restored.model_dump(mode="json") == artifact.model_dump(mode="json") else 0,
        "maxScore": 100,
        "artifactRoundTrip": restored.model_dump(mode="json") == artifact.model_dump(mode="json"),
        "realFineReviewRequirementCount": int(fine_review.get("clauseCount") or 0),
        "issues": [] if restored.model_dump(mode="json") == artifact.model_dump(mode="json") else [
            {"severity": "critical", "code": "artifact_round_trip", "message": "事实层产物往返不一致"}
        ],
    }


def _evaluate_p07_p08(document: Any, fine_review: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = [
        item for item in (fine_review.get("rows") or [])
        if isinstance(item, dict)
    ]
    nonempty = [item for item in rows if str(item.get("rawText") or item.get("requirement") or "").strip()]
    source_count = sum(
        bool(item.get("sourceSegmentId") or item.get("sourceSegmentIds") or item.get("sourceLocator"))
        for item in nonempty
    )
    evidence_count = sum(bool(item.get("evidenceItems")) for item in nonempty)
    ids = [str(item.get("id") or item.get("requirementId") or "") for item in nonempty]
    ids_unique = len(ids) == len(set(ids)) and all(ids)
    online = _online_status(fine_review)
    structural_score = round(
        (30 if ids_unique else 0)
        + (30 if nonempty else 0)
        + (25 * source_count / len(nonempty) if nonempty else 0)
        + (15 * evidence_count / len(nonempty) if nonempty else 0)
    )
    p07_score = structural_score if online["llmUsed"] else min(structural_score, 45)
    p07 = {
        "score": p07_score,
        "maxScore": 100,
        "requirementCount": len(nonempty),
        "idsUnique": ids_unique,
        "sourceTraceabilityRatio": round(source_count / len(nonempty), 4) if nonempty else 0,
        "evidenceAttachedRatio": round(evidence_count / len(nonempty), 4) if nonempty else 0,
        "extraction": online,
        "issues": (
            []
            if p07_score >= 85
            else [{
                "severity": "critical" if not online["llmUsed"] else "warning",
                "code": "real_extraction_quality",
                "message": (
                    "LLM 未产生有效结构化抽取，当前结果主要来自规则回退"
                    if not online["llmUsed"]
                    else "真实 LLM 提取结果仍需人工 gold 校准"
                ),
            }]
        ),
    }
    valid_rows = sum(
        bool(item.get("evidenceItems") or item.get("matchedFactIds") or item.get("sourceLocator"))
        for item in nonempty
    )
    p08_score = round(50 * valid_rows / len(nonempty), 0) + (50 if rows or not document.text else 0)
    p08 = {
        "score": int(p08_score),
        "maxScore": 100,
        "validatedRows": valid_rows,
        "rowCount": len(nonempty),
        "sourceAnchorRatio": round(valid_rows / len(nonempty), 4) if nonempty else 0,
        "issues": [] if p08_score >= 85 else [
            {"severity": "warning", "code": "evidence_anchor_gap", "message": "真实匹配结果中仍有条目缺少可回源锚点或企业证据"}
        ],
    }
    return p07, p08


def evaluate_one(path: Path, ai: dict[str, Any], company: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    content = path.read_bytes()
    document = parse_document_ir(
        content,
        file_name=path.name,
        mime_type={
            ".pdf": "application/pdf",
            ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".doc": "application/msword",
        }.get(path.suffix.lower(), ""),
    )
    chunks = chunk_document(document, max_tokens=800)
    classifications, classifier_calls = _classify_document_with_batched_llm(document, ai)
    fine_review = evaluate_tender_document(
        document.text,
        [item.model_dump(by_alias=True) for item in document.segments],
        company,
        ai,
        document.document_id,
        [item.model_dump(by_alias=True) for item in document.sections],
        [item.model_dump(by_alias=True) for item in document.tables],
        project_id=document.document_id,
        document_meta={
            "name": path.name,
            "fileName": path.name,
            "projectId": document.document_id,
            "projectName": path.stem,
            "documentKind": "tender_document",
            "contentHash": document.file_hash,
            "parseMethod": document.parse_method,
        },
    )
    online = _online_status(fine_review)
    p04 = evaluate_section_and_chunking(document, max_tokens=800)
    route_valid = all(item.route for item in classifications)
    p05 = {
        "score": 100 if route_valid and classifier_calls["failureBatchCount"] == 0 else 70,
        "maxScore": 100,
        "sectionCount": len(document.sections),
        "llmFallbackCallCount": classifier_calls["batchCount"],
        "llmFallbackSuccessCount": classifier_calls["successBatchCount"],
        "llmFallbackFailureCount": classifier_calls["failureBatchCount"],
        "llmFallbackCalls": classifier_calls,
        "humanReviewCount": sum(item.needs_human_review for item in classifications),
        "sourceCounts": dict(Counter(item.classification_source for item in classifications)),
        "routeContractsValid": route_valid,
        "issues": [] if classifier_calls["failureBatchCount"] == 0 else [
            {"severity": "critical", "code": "llm_section_classifier_failure", "message": "章节低置信度 LLM 分类调用失败"}
        ],
    }
    p06 = _evaluate_p06(document, chunks, fine_review)
    p07, p08 = _evaluate_p07_p08(document, fine_review)
    p09_summary = match_technical_service_requirements(
        [
            item for item in (fine_review.get("rows") or [])
            if isinstance(item, dict)
            and item.get("category") in {"technical", "service_plan"}
        ],
        company,
        ai=ai,
    )
    p09_source_counts = dict(Counter(item.status_source for item in p09_summary.items))
    p09_evidence_ratio = (
        sum(bool(item.evidence_items) for item in p09_summary.items) / len(p09_summary.items)
        if p09_summary.items else 0.0
    )
    m3_fact_total = sum(
        len(company.get(key) or [])
        for key in ("products", "parameters", "projects", "certificates", "personnel", "finance")
    )
    p09_score = (
        (35 if m3_fact_total > 0 else 0)
        + (20 if p09_summary.items else 0)
        + round(25 * p09_evidence_ratio)
        + (20 if online["llmUsed"] else 0)
    )
    p09 = {
        "score": p09_score,
        "maxScore": 100,
        "itemCount": len(p09_summary.items),
        "coverage": p09_summary.coverage,
        "statusCounts": p09_summary.status_counts,
        "recommendation": p09_summary.recommendation,
        "matchingWarnings": p09_summary.warnings,
        "statusSourceCounts": p09_source_counts,
        "evidenceAttachedRatio": round(p09_evidence_ratio, 4),
        "m3FactCounts": {
            "products": len(company.get("products") or []),
            "parameters": len(company.get("parameters") or []),
            "projects": len(company.get("projects") or []),
            "certificates": len(company.get("certificates") or []),
        },
        "issues": (
            []
            if p09_score >= 85
            else [{
                "severity": "critical" if m3_fact_total == 0 else "warning",
                "code": "m3_matching_quality",
                "message": (
                    "M3 企业事实未进入匹配层"
                    if m3_fact_total == 0
                    else "真实 M3 匹配的企业证据挂接率偏低，仍需人工复核"
                ),
            }]
        ),
    }
    return {
        "sampleId": document.document_id,
        "fileName": path.name,
        "path": path.resolve().relative_to(REPO_ROOT.resolve()).as_posix(),
        "format": path.suffix.lower().lstrip("."),
        "parseMethod": document.parse_method,
        "textChars": len(document.text),
        "segmentCount": len(document.segments),
        "sectionCount": len(document.sections),
        "chunkCount": len(chunks),
        "p04": p04,
        "p05": p05,
        "p06": p06,
        "p07": p07,
        "p08": p08,
        "p09": p09,
        "online": online,
        "fineReview": {
            "clauseCount": fine_review.get("clauseCount", 0),
            "score": fine_review.get("score", 0),
            "recommendation": fine_review.get("recommendation", ""),
        },
        "elapsedSeconds": round(time.perf_counter() - started, 2),
    }


def _write_markdown(report: dict[str, Any], output: Path) -> None:
    lines = [
        "# P04-P09 真实链路评测",
        "",
        f"> 生成日期：{report['generatedAt']}",
        "> 评测方式：真实 LLM + 真实 M3 读取；不写入 M3",
        "",
        "## 验收门槛",
        "",
        "- 真实 LLM：必须看到 `builtin-llm` 或实际 LLM provider、非空 modelUsed，静默规则回退不得算通过。",
        "- 真实 M3：必须成功读取企业事实，并记录各事实类数量；空台账只能判为未完成真实匹配验收。",
        "- P04/P06：可做自动契约校验，但不把结构正确性冒充语义准确率。",
        "- P05/P07/P08/P09：没有人工 gold 的指标只报告可观测质量与风险，不宣称 precision/recall。",
        "",
        "## 汇总",
        "",
        "| 阶段 | 平均分 | 最低分 | LLM/M3 证据 |",
        "|---|---:|---:|---|",
    ]
    for stage, data in report["summary"]["stages"].items():
        lines.append(
            f"| {stage.upper()} | {data['averageScore']} | {data['minimumScore']} | "
            f"{data.get('evidenceStatus', '')} |"
        )
    lines.extend(["", "## 样本明细", "", "| 文件 | P04 | P05 | P06 | P07 | P08 | P09 | LLM | M3 事实 |", "|---|---:|---:|---:|---:|---:|---:|---|---|"])
    for item in report["samples"]:
        if item.get("error"):
            lines.append(
                f"| {item['fileName'].replace('|', '\\|')} | ERROR | ERROR | ERROR | "
                f"ERROR | ERROR | ERROR | {item['error']} | - |"
            )
            continue
        counts = item["p09"]["m3FactCounts"]
        lines.append(
            f"| {item['fileName'].replace('|', '\\|')} | "
            f"{item['p04']['score']} | {item['p05']['score']} | {item['p06']['score']} | "
            f"{item['p07']['score']} | {item['p08']['score']} | {item['p09']['score']} | "
            f"{item['online']['provider']}/{item['online']['model'] or 'none'} | "
            f"products={counts['products']}, parameters={counts['parameters']}, projects={counts['projects']} |"
        )
    lines.extend(["", "## 解释", "", "- 这份报告和离线报告必须并列看：离线报告回答“规则和契约是否稳定”，本报告回答“真实模型和企业事实是否进入并影响结果”。", "- 真实语义准确率仍需要人工 gold；否则只能判断来源、覆盖、可追溯性和风险，不应把模型输出数量当准确率。"])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=REPO_ROOT / "demo-data")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json-output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args()

    _load_env()
    config = _config()
    ai = config["ai"]
    m3 = _m3_client(config)
    facts = read_facts_from_m3(m3)
    company = build_company_ledger(facts)
    fact_counts = {
        key: len(value or []) if isinstance(value, list) else int(bool(value))
        for key, value in facts.items()
    }
    samples: list[dict[str, Any]] = []
    for path in _paths(args.input.resolve(), args.limit):
        try:
            samples.append(evaluate_one(path, ai, company))
        except Exception as exc:  # keep all-file evaluation running
            samples.append({
                "fileName": path.name,
                "path": path.resolve().relative_to(REPO_ROOT.resolve()).as_posix(),
                "error": f"{type(exc).__name__}: {exc}",
            })

    stages: dict[str, dict[str, Any]] = {}
    for stage in ("p04", "p05", "p06", "p07", "p08", "p09"):
        values = [item[stage]["score"] for item in samples if stage in item]
        stages[stage] = {
            "averageScore": round(sum(values) / len(values), 2) if values else 0,
            "minimumScore": min(values) if values else 0,
            "sampleCount": len(values),
            "evidenceStatus": (
                "real_llm_and_m3"
                if stage in {"p05", "p07", "p08", "p09"}
                else "structural_or_persistence"
            ),
        }
    report = {
        "schemaVersion": "p04-p09-real-v1",
        "generatedAt": date.today().isoformat(),
        "mode": {
            "network": "enabled",
            "llm": {
                "enabled": True,
                "baseUrl": re.sub(r"(?<=://).*(?=/v1)", "***", str(ai.get("baseUrl") or "")),
                "model": ai.get("model", ""),
            },
            "m3": {"enabled": True, "factCounts": fact_counts},
        },
        "summary": {
            "sampleCount": len(samples),
            "successCount": sum(1 for item in samples if "error" not in item),
            "failureCount": sum(1 for item in samples if "error" in item),
            "stages": stages,
            "llmSectionClassifierCalls": {
                "batchCount": sum(
                    int(item.get("p05", {}).get("llmFallbackCallCount") or 0)
                    for item in samples
                ),
                "successBatchCount": sum(
                    int(item.get("p05", {}).get("llmFallbackSuccessCount") or 0)
                    for item in samples
                ),
                "failureBatchCount": sum(
                    int(item.get("p05", {}).get("llmFallbackFailureCount") or 0)
                    for item in samples
                ),
            },
            "m3FactCounts": fact_counts,
        },
        "samples": samples,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_markdown(report, args.markdown_output.resolve())
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["failureCount"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
