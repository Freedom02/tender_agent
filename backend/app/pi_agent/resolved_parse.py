"""Native v2 parsing with bounded source batches and independent review."""
from __future__ import annotations

from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import hashlib
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable

from pydantic import ValidationError

from ..agent_runtime.contracts import build_agent_result, new_id, now_iso
from ..agent_runtime.clause_repair import apply_clause_repair, build_clause_repair_request
from ..agent_runtime.parse_semantics import (
    ClauseExtraction, EvaluationExtraction, SemanticReview, TableBinding,
    build_source_batches, hydrate_source_traces, namespace_clause_batch, validate_clause_batch,
)
from ..agent_runtime.resolved_audit import audit_resolved_tender
from ..agent_runtime.resolved_sources import source_index
from ..agent_runtime.resolved_tender import ResolvedTenderV2
from ..agent_runtime.submission_structure import parse_submission_format_tree
from ..bid_writing.template_fragments import attach_template_fragments_to_bundle
from .orchestrator import (
    PiParseOrchestrationError, _compact_structure_for_agent, _derive_outline,
    _local_document_payload, _local_structure, _merge_structures,
    _merge_template_spec_with_local_structure, _normalize_project_meta,
    _normalize_structure, _normalize_template, _structure_agent_payload,
    _template_relevant_text,
)
from .prompts.parse import structure_meta_prompt
from .transport import PolicyBlockedError, _redact_diagnostic


def _dump(value: Any) -> Any:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else value


def _agent_dump(value: Any) -> Any:
    payload = value.model_dump(mode="json", exclude_defaults=True) if hasattr(value, "model_dump") else value
    if isinstance(payload, list):
        return [_agent_dump(item) for item in payload]
    if isinstance(payload, dict):
        return {key: [{"segmentId": trace["segmentId"], "quote": trace["quote"]} for trace in item]
                if key in {"sourceTrace", "evaluationMethodSourceTrace"} else _agent_dump(item)
                for key, item in payload.items()}
    return payload


def _error_detail(exc: Exception, *, contract: bool = False) -> str:
    if isinstance(exc, ValidationError):
        grouped: dict[str, list[str]] = {}
        for item in exc.errors(include_input=False):
            grouped.setdefault(item["msg"], []).append(".".join(map(str, item["loc"])))
        return _redact_diagnostic("; ".join(
            f"{', '.join(locations[:3])}: {message} (共{len(locations)}处，均需修正)"
            for message, locations in list(grouped.items())[:12]
        ), contract=True)[:1600]
    return _redact_diagnostic(str(exc), contract=contract)[:1600 if contract else 600]


def compatibility_projection(tender: ResolvedTenderV2) -> dict[str, Any]:
    categories = {
        "qualification": "qualification", "compliance": "qualification",
        "technical_parameter": "technical", "delivery": "technical", "training": "technical",
        "acceptance": "technical", "service": "technical", "commercial_condition": "commercial",
        "contract": "commercial", "pricing": "commercial", "price_policy": "commercial",
        "format_form": "other", "evidence": "other",
    }
    requirements = []
    for item in tender.requirements:
        if item.responseRequired is not True or item.responseStage != "bid_submission":
            continue
        requirements.append({
            **item.model_dump(mode="json"), "id": item.requirementId, "requirement": item.text,
            "semanticCategory": item.category, "category": categories.get(item.category, "other"),
            "semanticMandatory": item.mandatory, "mandatory": item.mandatory is True,
            "sourceLocator": item.sourceTrace[0].sourceLocator if item.sourceTrace else "",
            "sourceText": "\n".join(trace.quote for trace in item.sourceTrace),
            "responseType": "response_table" if "response_table" in item.responseMode else "chapter_text",
            "status": "待核验", "extractionSource": "pi_v2",
        })
    evaluations = [{
        "id": rule.evaluationRuleId, "criterion": rule.title, "criteria": rule.text,
        "score": rule.maxScore.value, "relatedRequirementIds": rule.requirementIds,
        "sourceLocator": rule.sourceTrace[0].sourceLocator if rule.sourceTrace else "",
        "sourceTrace": [trace.model_dump(mode="json") for trace in rule.sourceTrace],
    } for rule in tender.evaluationRules if rule.maxScore is not None and tender.evaluationMethod == "comprehensive_scoring"]
    return {"requirements": requirements, "evaluationItems": evaluations}


class PiResolvedParseOrchestrator:
    def __init__(self, client: Any, *, batch_chars: int = 14000, max_workers: int | None = None,
                 timeout_seconds: float | None = None) -> None:
        self.client = client
        self.batch_chars = batch_chars
        try:
            self.max_workers = min(6, max(1, int(max_workers if max_workers is not None else os.getenv("PI_PARSE_WORKERS", "3"))))
            self.timeout_seconds = max(1.0, float(timeout_seconds if timeout_seconds is not None else os.getenv("PI_PARSE_TIMEOUT", "1200")))
        except (TypeError, ValueError):
            raise ValueError("PI_PARSE_WORKERS 和 PI_PARSE_TIMEOUT 必须是有效数字") from None

    def parse_tender(
        self, path: Path, *, workspace_id: str, project_id: str,
        project_context: dict[str, Any],
        progress_callback: Callable[[str, str, int, int], None] | None = None,
        node_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        parse_started = time.monotonic()
        deadline = parse_started + self.timeout_seconds
        state_lock = threading.RLock()
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        parsed = self.client._parse_file(path)
        if hashlib.sha256(path.read_bytes()).hexdigest() != file_hash:
            raise PiParseOrchestrationError("源文件在解析期间发生变化，请重新解析")
        document_blocks = deepcopy(parsed)
        if not document_blocks.get("units"):
            document_blocks["units"] = deepcopy(parsed.get("segments") or [])
        source_file = {
            "fileId": str(project_context.get("sourceFileId") or f"file-{file_hash[:20]}"),
            "filename": path.name, "fileType": path.suffix.lower().lstrip("."),
            "sha256": file_hash, "documentVersionId": file_hash, "sizeBytes": path.stat().st_size,
        }
        batches = build_source_batches(document_blocks, max_chars=self.batch_chars)
        if not batches:
            raise PiParseOrchestrationError("没有可定位的原文块，不能生成已解析的投标要求")
        indexed = source_index(document_blocks)
        parse_bundle_id = new_id("parse")
        agent_results: list[dict[str, Any]] = []
        issues: list[dict[str, str]] = []
        warnings = [str(item) for item in parsed.get("warnings") or []]
        completed = 0
        total = 2 + len(batches) * 4
        successes = 0

        def issue(code: str, message: str, issue_path: str = "", severity: str = "review") -> None:
            issues.append({"code": code, "severity": severity, "path": issue_path, "message": message})

        def progress(agent_id: str, status: str) -> None:
            if progress_callback:
                try:
                    progress_callback(agent_id, status, completed, total)
                except Exception:
                    return

        def checkpoint(agent_id: str, label: str, status: str, **details: Any) -> None:
            if node_callback:
                try:
                    node_callback({"agentId": agent_id, "label": label, "status": status,
                                   "timestamp": now_iso(), "elapsedSeconds": round(time.monotonic() - parse_started, 2),
                                   "completed": completed, "total": total, **details})
                except Exception as exc:
                    warnings.append(f"节点记录保存失败：{_error_detail(exc)}")

        def check_deadline() -> None:
            if time.monotonic() >= deadline:
                raise PiParseOrchestrationError(f"解析达到总时限 {self.timeout_seconds:g} 秒；已完成 {completed}/{total} 节点，请查看节点记录定位失败后重试")

        def parallel_map(invoke: Callable[[Any], Any], items: list[Any]) -> list[Any]:
            with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="tender-parse") as executor:
                return list(executor.map(invoke, items))

        def run(
            agent_id: str, label: str, invoke: Callable[[str], Any], validate: Callable[[Any], Any],
            repair: Callable[[Any, str, dict[str, Any]], Any] | None = None,
        ) -> Any:
            nonlocal completed, successes
            check_deadline()
            started = now_iso()
            node_started = time.monotonic()
            correction = ""
            attempts: list[dict[str, Any]] = []
            failed_output: Any = None
            with state_lock:
                progress(agent_id, f"running:{label}")
                checkpoint(agent_id, label, "running", attempt=1)
            for attempt in range(2):
                check_deadline()
                validation_started = False
                repair_details: dict[str, Any] = {}
                try:
                    scope = self.client.request_deadline(deadline) if hasattr(self.client, "request_deadline") else nullcontext()
                    with scope:
                        if attempt and repair is not None and failed_output is not None:
                            raw = repair(failed_output, correction, repair_details)
                        else:
                            raw = invoke(correction)
                    attempts.append({"attempt": attempt + 1, "output": deepcopy(raw), **repair_details})
                    validation_started = True
                    result = validate(raw)
                    agent_warnings = list(getattr(result, "warnings", []) or (raw.get("warnings", []) if isinstance(raw, dict) else []))
                    record = build_agent_result(
                        agent_id=agent_id, run_id=f"{parse_bundle_id}:{agent_id}:{label}", provider="pi",
                        model=str(getattr(getattr(self.client, "config", None), "model", "")),
                        input_refs=["parse:documentBlocks", label],
                        output={"result": _dump(result), "attempts": attempts}, warnings=agent_warnings,
                        created_at=started, finished_at=now_iso(),
                    )
                    with state_lock:
                        successes += 1
                        warnings.extend(agent_warnings)
                        if correction:
                            warnings.append(f"{agent_id}[{label}] 经一次修正或重试通过")
                        agent_results.append(record)
                        completed += 1
                        checkpoint(agent_id, label, "success", attempt=attempt + 1,
                                   durationSeconds=round(time.monotonic() - node_started, 2), agentResult=record)
                        progress(agent_id, f"success:{label}")
                    return result
                except Exception as exc:
                    correction = _error_detail(exc, contract=validation_started or "repairResult" in repair_details)
                    if validation_started:
                        failed_output = deepcopy(raw)
                    if attempts and attempts[-1]["attempt"] == attempt + 1:
                        attempts[-1]["error"] = correction
                    else:
                        attempts.append({"attempt": attempt + 1, "error": correction, **repair_details})
                    with state_lock:
                        checkpoint(agent_id, label, "attempt_failed", attempt=attempt + 1, error=correction,
                                   attemptResult=attempts[-1], durationSeconds=round(time.monotonic() - node_started, 2))
                        if attempt == 0 and not isinstance(exc, PolicyBlockedError):
                            progress(agent_id, f"retrying:{label}")
                    if isinstance(exc, PolicyBlockedError):
                        break
            message = f"{agent_id}[{label}] 未通过：{correction}"
            record = build_agent_result(
                agent_id=agent_id, run_id=f"{parse_bundle_id}:{agent_id}:{label}", provider="pi", status="failed",
                input_refs=["parse:documentBlocks", label], output={"attempts": attempts}, warnings=[message],
                created_at=started, finished_at=now_iso(),
            )
            with state_lock:
                issue("parse_node_failed", message, label)
                warnings.append(message)
                agent_results.append(record)
                completed += 1
                checkpoint(agent_id, label, "failed", durationSeconds=round(time.monotonic() - node_started, 2), agentResult=record)
                progress(agent_id, f"failed:{label}")
            return None

        local_structure = _local_structure(parsed)
        structure_payload = _structure_agent_payload(_local_document_payload(parsed, str(parsed.get("text") or "")), local_structure)
        structure_raw = run(
            "DocumentStructureAgent", "structure-meta",
            lambda correction: self.client.run_json(structure_meta_prompt(
                document_text=str(parsed.get("text") or "")[:12000], document_payload=structure_payload,
                filename=path.name, parser_warnings=warnings[:20], workspace_id=workspace_id,
                project_id=project_id, project_context=project_context,
            ) + (f"\n上次校验反馈：{correction}" if correction else "")),
            self._validate_structure,
        ) or {}
        structure = _merge_structures(local_structure, _normalize_structure(structure_raw.get("documentStructure"), parsed))
        project_meta = _normalize_project_meta(structure_raw)
        context = {"projectMeta": project_meta, "documentStructure": _compact_structure_for_agent(structure)}
        def extract_batch(batch: dict[str, Any]) -> tuple[ClauseExtraction, EvaluationExtraction]:
            inputs = {**context, **batch}

            def validate_clauses(raw: Any) -> ClauseExtraction:
                hydrated = hydrate_source_traces(raw, document_blocks=document_blocks, source_file=source_file, views=batch["views"])
                return validate_clause_batch(hydrated, batch)

            def repair_clauses(raw: Any, correction: str, details: dict[str, Any]) -> Any:
                request = build_clause_repair_request(raw, batch, document_blocks=document_blocks, source_file=source_file)
                if request is None or not callable(getattr(self.client, "repair_tender_clauses", None)):
                    details["mode"] = "full_retry"
                    return self.client.extract_tender_clauses(inputs=inputs, correction=correction)
                details.update(mode="partial_repair", repairTargetCount=len(request["targets"]), repairViewCount=len(request["views"]))
                patch = self.client.repair_tender_clauses(inputs=request, correction=correction)
                details["repairResult"] = deepcopy(patch)
                return apply_clause_repair(raw, patch, request, batch, document_blocks=document_blocks, source_file=source_file, validate=False)

            clauses = run(
                "TenderClauseAgent", batch["batchId"],
                lambda correction: self.client.extract_tender_clauses(inputs=inputs, correction=correction),
                validate_clauses,
                repair=repair_clauses,
            )
            if clauses is None:
                clauses = self._unresolved_batch(batch, document_blocks, source_file)
            clauses = namespace_clause_batch(clauses, batch["batchId"])
            for assessment in clauses.assessments:
                if assessment.disposition == "review":
                    with state_lock:
                        issue("source_view_unresolved", assessment.reason, assessment.viewId)
            evaluation_inputs = {**inputs, "requirements": [_agent_dump(item) for item in clauses.requirements]}

            def validate_evaluation(raw: Any) -> EvaluationExtraction:
                hydrated = hydrate_source_traces(raw, document_blocks=document_blocks, source_file=source_file, views=batch["views"])
                result = EvaluationExtraction.model_validate(hydrated)
                if result.evaluationMethod != "unknown" and not result.evaluationMethodSourceTrace:
                    raise ValueError("evaluation method requires sourceTrace")
                for rule in result.evaluationRules:
                    if not rule.sourceTrace or any(not parameter.sourceTrace for parameter in rule.parameters):
                        raise ValueError("evaluation rules and parameters require sourceTrace")
                candidate = ResolvedTenderV2(
                    parseBundleId="evaluation-batch", requirements=clauses.requirements,
                    evidenceRequirements=clauses.evidenceRequirements, evaluationMethod=result.evaluationMethod,
                    evaluationMethodSourceTrace=result.evaluationMethodSourceTrace, evaluationRules=result.evaluationRules,
                    sourceFile=source_file, documentBlocks=document_blocks,
                )
                invalid = [finding.code for finding in audit_resolved_tender(candidate).issues
                           if finding.code in {"score_value_unverified", "evaluation_method_unverified"}]
                if invalid:
                    raise ValueError("; ".join(invalid))
                return result

            evaluation = run(
                "TenderEvaluationAgent", batch["batchId"],
                lambda correction: self.client.extract_tender_evaluation(inputs=evaluation_inputs, correction=correction),
                validate_evaluation,
            ) or EvaluationExtraction(evaluationRules=[])
            for rule in evaluation.evaluationRules:
                rule.evaluationRuleId = f"{batch['batchId']}:{rule.evaluationRuleId}"
            return clauses, evaluation

        extracted = parallel_map(extract_batch, batches)
        clause_results = [item[0] for item in extracted]
        evaluation_results = [item[1] for item in extracted]

        def validate_template(raw: Any) -> dict[str, Any]:
            if not isinstance(raw, dict) or not {"hasExplicitFormat", "requiredOutline", "templates", "requiredTables"}.issubset(raw):
                raise ValueError("template result is missing required root fields")
            return _merge_template_spec_with_local_structure(_normalize_template(raw), parse_submission_format_tree(str(parsed.get("text") or "")))

        template_spec = run(
            "TemplateDetectAgent", "templates",
            lambda correction: self.client.detect_templates(
                document_text=_template_relevant_text(str(parsed.get("text") or ""), max_chars=24000),
                document_structure={**context["documentStructure"], "submissionStructure": parse_submission_format_tree(str(parsed.get("text") or ""))},
                filename=path.name, workspace_id=workspace_id, project_id=project_id, timeout_seconds=180,
                correction=correction,
            ), validate_template,
        ) or _merge_template_spec_with_local_structure(_normalize_template({}), parse_submission_format_tree(str(parsed.get("text") or "")))
        fragment_bundle = attach_template_fragments_to_bundle({
            "projectId": project_id, "sourceFile": source_file, "documentBlocks": document_blocks,
            "bidFormatSpec": template_spec, "submissionStructure": template_spec["submissionStructure"],
        }, source_path=path)
        methods = {item.evaluationMethod for item in evaluation_results if item.evaluationMethod != "unknown"}
        method = next(iter(methods)) if len(methods) == 1 else "unknown"
        if len(methods) > 1:
            issue("evaluation_method_conflict", "不同原文批次识别出冲突的评审方法，需按采购包核实")
        rules = [rule for result in evaluation_results for rule in result.evaluationRules]
        if method == "lowest_evaluated_price" and any(rule.maxScore is not None for rule in rules):
            issue("evaluation_score_conflict", "最低评标价法与其他批次的评分规则冲突，分值暂不进入写作输入", severity="error")
            rules = [rule for rule in rules if rule.maxScore is None]
        tender = ResolvedTenderV2(
            parseBundleId=parse_bundle_id, projectId=project_id, workspaceId=workspace_id,
            sourceFile=source_file, documentBlocks=document_blocks, projectMeta=project_meta,
            submissionStructure=template_spec["submissionStructure"], templateFragments=fragment_bundle["templateFragments"],
            requirements=[item for result in clause_results for item in result.requirements],
            evidenceRequirements=[item for result in clause_results for item in result.evidenceRequirements],
            evaluationMethod=method,
            evaluationMethodSourceTrace=[trace for result in evaluation_results if result.evaluationMethod == method
                                        for trace in result.evaluationMethodSourceTrace], evaluationRules=rules,
        )
        original_tables = self._original_response_tables(tender, indexed)
        tables_by_key: dict[str, Any] = {}
        def review_batch(items: tuple[dict[str, Any], ClauseExtraction, EvaluationExtraction]) -> TableBinding:
            nonlocal completed
            batch, clauses, evaluation = items
            response_ids = {item.requirementId for item in clauses.requirements if "response_table" in item.responseMode}
            table_inputs = {
                "requirements": [_agent_dump(item) for item in clauses.requirements if item.requirementId in response_ids],
                "originalTables": original_tables,
                "templateFragments": [{"templateId": fragment.get("templateId"), "title": fragment.get("title"),
                                       "tableIds": [table.get("id") or table.get("tableId") for table in fragment.get("tables") or []]}
                                      for fragment in tender.templateFragments],
            }

            def validate_tables(raw: Any) -> TableBinding:
                supplied_views = [view for table in original_tables for view in table["sourceViews"]] + batch["views"]
                hydrated = hydrate_source_traces(raw, document_blocks=document_blocks, source_file=source_file, views=supplied_views)
                result = TableBinding.model_validate(hydrated)
                available_tables = {table["sourceTableId"]: table for table in original_tables}
                for table in result.responseTableSchemas:
                    original = available_tables.get(table.sourceTableId)
                    if original is None or [column.label for column in table.columns] != original["columns"]:
                        raise ValueError("response table must retain exactly the supplied original columns")
                    if [column.key for column in table.columns] != [f"column-{position}" for position in range(1, len(table.columns) + 1)]:
                        raise ValueError("response column keys must follow original column order")
                    if not table.sourceTrace or any(not binding.sourceTrace for binding in table.rowBindings):
                        raise ValueError("response tables and bindings require sourceTrace")
                    if not table.preserveOriginalFormat or any(binding.targetRowIndex is not None for binding in table.rowBindings):
                        raise ValueError("original format and unresolved target row must be preserved")
                    if any(binding.requirementId not in response_ids for binding in table.rowBindings):
                        raise ValueError("table binding refers to a requirement outside this batch")
                candidate = tender.model_dump(mode="json")
                candidate["responseTableSchemas"] = [_dump(table) for table in result.responseTableSchemas]
                candidate_tender = ResolvedTenderV2.model_validate(candidate)
                report = audit_resolved_tender(candidate_tender)
                errors = [item.code for item in report.issues if item.severity == "error"]
                if errors:
                    raise ValueError("; ".join(errors))
                return result

            if response_ids and original_tables:
                binding = run(
                    "TenderTableBindingAgent", batch["batchId"],
                    lambda correction: self.client.bind_tender_response_tables(inputs=table_inputs, correction=correction),
                    validate_tables,
                ) or TableBinding(responseTableSchemas=[])
            else:
                binding = TableBinding(responseTableSchemas=[])
                with state_lock:
                    completed += 1
                    checkpoint("TenderTableBindingAgent", batch["batchId"], "skipped")
                    progress("TenderTableBindingAgent", f"skipped:{batch['batchId']}")
            review_inputs = {
                **batch, "requirements": [_agent_dump(item) for item in clauses.requirements],
                "evidenceRequirements": [_agent_dump(item) for item in clauses.evidenceRequirements],
                "assessments": [_agent_dump(item) for item in clauses.assessments],
                "evaluation": _agent_dump(evaluation),
                "confirmedEvaluationMethod": method, "responseTableSchemas": [_agent_dump(table) for table in binding.responseTableSchemas],
            }

            def validate_review(raw: Any) -> SemanticReview:
                review = SemanticReview.model_validate(raw)
                for actual, expected in (
                    (review.reviewedViewIds, {view["viewId"] for view in batch["views"]}),
                    (review.reviewedRequirementIds, {item.requirementId for item in clauses.requirements}),
                ):
                    if len(actual) != len(set(actual)) or set(actual) != expected:
                        raise ValueError("semantic review must cover exactly the supplied views and requirements")
                return review

            review = run(
                "TenderSemanticReviewAgent", batch["batchId"],
                lambda correction: self.client.review_tender_semantics(inputs=review_inputs, correction=correction),
                validate_review,
            )
            if review is not None:
                with state_lock:
                    issues.extend(_dump(finding) for finding in review.findings)
            return binding

        bindings = parallel_map(review_batch, list(zip(batches, clause_results, evaluation_results)))
        for binding in bindings:
            for table in binding.responseTableSchemas:
                key = f"{table.templateFragmentId}:{table.sourceTableId}"
                previous = tables_by_key.get(key)
                if previous is None:
                    table.responseTableId = f"response:{table.sourceTableId}:{table.templateFragmentId}"
                    tables_by_key[key] = table
                elif [column.model_dump() for column in previous.columns] != [column.model_dump() for column in table.columns] or previous.domain != table.domain:
                    issue("response_table_conflict", "不同批次对同一原表的列属性或用途判断冲突", key, "error")
                else:
                    previous.rowBindings.extend(table.rowBindings)
        tender.responseTableSchemas = list(tables_by_key.values())
        check_deadline()
        if not successes:
            raise PiParseOrchestrationError("Pi v2 解析没有任何节点成功；" + "；".join(warnings[-3:]))
        if hashlib.sha256(path.read_bytes()).hexdigest() != file_hash:
            raise PiParseOrchestrationError("源文件在智能体解析期间发生变化，请重新解析")
        tender.risk["parseIssues"] = issues
        tender = ResolvedTenderV2.model_validate(tender.model_dump(mode="json"))
        readiness = audit_resolved_tender(tender).model_dump(mode="json")
        coverage = {
            "batchCount": len(batches), "sourceViewCount": sum(len(batch["views"]) for batch in batches),
            "assessedViewCount": sum(len(result.assessments) for result in clause_results),
            "unresolvedViewCount": sum(item.disposition == "review" for result in clause_results for item in result.assessments),
            "elapsedSeconds": round(time.monotonic() - parse_started, 2), "maxWorkers": self.max_workers,
        }
        return {
            **compatibility_projection(tender), "provider": "pi", "parseBundleId": tender.parseBundleId,
            "sourceFile": source_file, "documentBlocks": document_blocks, "documentStructure": structure,
            "projectMeta": project_meta, "bidFormatSpec": template_spec,
            "submissionStructure": tender.submissionStructure, "templateFragments": tender.templateFragments,
            "outline": _derive_outline(template_spec), "warnings": list(dict.fromkeys(warnings)),
            "riskItems": [], "agentResults": agent_results,
            "resolvedTenderV2": tender.model_dump(mode="json"),
            "parseReviewReport": {**readiness, "status": "pass" if readiness["readyForWriting"] else "review_required", "coverage": coverage},
        }

    @staticmethod
    def _validate_structure(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict) or not isinstance(raw.get("documentStructure"), dict) or not isinstance(raw.get("projectMeta"), dict):
            raise ValueError("structure/meta requires documentStructure and projectMeta objects")
        return raw

    @staticmethod
    def _unresolved_batch(batch: dict[str, Any], blocks: dict[str, Any], source_file: dict[str, Any]) -> ClauseExtraction:
        payload = {
            "requirements": [{"requirementId": f"unresolved-{position}", "text": view["text"],
                              "sourceTrace": [{"segmentId": view["segmentId"], "quote": view["text"]}]}
                             for position, view in enumerate(batch["views"], 1)],
            "evidenceRequirements": [],
            "assessments": [{"viewId": view["viewId"], "disposition": "review", "reason": "抽取失败，仅保留原文，未确认任何投标义务"}
                            for view in batch["views"]],
        }
        return ClauseExtraction.model_validate(hydrate_source_traces(payload, document_blocks=blocks, source_file=source_file))

    @staticmethod
    def _original_response_tables(tender: ResolvedTenderV2, indexed: dict[str, Any]) -> list[dict[str, Any]]:
        tables = []
        seen: set[str] = set()
        for fragment in tender.templateFragments:
            for table in fragment.get("tables") or []:
                table_id = str(table.get("id") or table.get("tableId") or "")
                original = indexed.get(table_id, {})
                rows = original.get("rows") or []
                if not rows or table_id in seen:
                    continue
                seen.add(table_id)
                headers = []
                for cell in rows[0].get("cells") or []:
                    cell_id = str(cell.get("id") or "")
                    if cell_id and cell_id in indexed and str(cell.get("text") or "").strip():
                        headers.append({"segmentId": cell_id, "text": cell["text"]})
                if headers:
                    tables.append({"sourceTableId": table_id, "title": fragment.get("title"),
                                   "columns": [str(cell.get("text") or "") for cell in rows[0].get("cells") or []], "sourceViews": headers})
        return tables
