"""Lossless, opt-in projection of legacy writing inputs to ResolvedTender v2."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
from typing import Any

from ..agent_runtime.resolved_sources import normalized_quote, source_index
from ..agent_runtime.resolved_tender import (
    RESOLVED_TENDER_V2,
    EvaluationRule,
    ResolvedTenderV2,
    SourceTrace,
    WritingRequirement,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(row: dict[str, Any]) -> str:
    return str(row.get("text") or row.get("requirement") or row.get("criteria") or row.get("title") or "").strip()


def _traces(
    row: dict[str, Any],
    blocks: dict[str, dict[str, Any]],
    source_file: dict[str, Any],
    warnings: list[str],
    item_id: str,
) -> list[SourceTrace]:
    explicit = row.get("sourceTrace")
    if isinstance(explicit, list) and explicit:
        return [SourceTrace.model_validate(item) for item in explicit]
    source = _dict(row.get("source"))
    refs: list[str] = []
    for owner in (row, source):
        for key in ("sourceSegmentIds", "segmentIds"):
            value = owner.get(key)
            if isinstance(value, list):
                refs.extend(str(item) for item in value if item)
        for key in ("sourceSegmentId", "segmentId"):
            if owner.get(key):
                refs.append(str(owner[key]))
    traces: list[SourceTrace] = []
    for block_id in dict.fromkeys(refs):
        block = blocks.get(block_id)
        if block is None:
            warnings.append(f"{item_id}: source block {block_id} is missing")
            continue
        block_text = str(block.get("text") or "")
        if not block_text.strip():
            warnings.append(f"{item_id}: source block {block_id} has no text")
            continue
        requested_quote = str(row.get("sourceQuote") or source.get("quote") or _text(row))
        quote = requested_quote if requested_quote.strip() and normalized_quote(requested_quote) in normalized_quote(block_text) else block_text
        if quote != requested_quote:
            warnings.append(f"{item_id}: legacy clause text is not a verbatim source quote; retained full source block")
        traces.append(SourceTrace(
            segmentId=block_id,
            fileId=str(row.get("sourceFileId") or source.get("fileId") or source_file.get("fileId") or ""),
            documentVersionId=str(source_file.get("documentVersionId") or source_file.get("sha256") or ""),
            quote=quote,
            sourceLocator=str(block.get("sourceLocator") or ""),
            **{key: block.get(key) for key in ("page", "paragraphIndex", "bodyChildIndex", "rowIndex", "cellIndex")},
            tableId=str(block.get("tableId") or ""),
        ))
    if not traces:
        warnings.append(f"{item_id}: no verifiable source trace; re-extraction required")
    return traces


def migrate_resolved_tender(data: dict[str, Any]) -> dict[str, Any]:
    """Return a new snapshot; never mutate input or infer legacy responsibility."""
    if data.get("schemaVersion") == RESOLVED_TENDER_V2:
        return ResolvedTenderV2.model_validate(deepcopy(data)).model_dump(mode="json")
    if data.get("schemaVersion") not in (None, "1.0"):
        raise ValueError(f"unsupported tender schema version: {data.get('schemaVersion')}")
    snapshot = deepcopy(data)
    bundle = _dict(snapshot.get("bundle")) if "bundle" in snapshot else snapshot
    if bundle.get("schemaVersion") == RESOLVED_TENDER_V2:
        raise ValueError("native v2 must be supplied directly, not embedded in legacy inputs")
    if bundle.get("schemaVersion") not in (None, "1.0"):
        raise ValueError(f"unsupported parse bundle version: {bundle.get('schemaVersion')}")
    warnings: list[str] = []
    document_blocks = _dict(bundle.get("documentBlocks"))
    blocks = source_index(document_blocks)
    source_file = _dict(bundle.get("sourceFile"))
    bundle_rows = _rows(bundle.get("requirements"))
    by_id = {str(row.get("requirementId") or row.get("id") or ""): row for row in bundle_rows}
    rows = _rows(snapshot.get("requirements")) or bundle_rows
    requirements: list[WritingRequirement] = []
    for position, row in enumerate(rows, 1):
        item_id = str(row.get("requirementId") or row.get("id") or f"legacy-requirement-{position:04d}")
        merged = {**by_id.get(item_id, {}), **row}
        text = _text(merged)
        if not text:
            warnings.append(f"{item_id}: missing clause text; original row retained in legacy only")
            continue
        requirements.append(WritingRequirement(
            requirementId=item_id,
            text=text,
            sourceTrace=_traces(merged, blocks, source_file, warnings, item_id),
        ))
    if requirements:
        warnings.append("Legacy responsibility, response mode, applicability, parameters and evidence obligations require semantic review; comparison statuses are preserved only in legacy")
    evaluation = _dict(snapshot.get("eval"))
    eval_rows = _rows(evaluation.get("items")) or _rows(bundle.get("evaluationItems"))
    rules: list[EvaluationRule] = []
    for position, row in enumerate(eval_rows, 1):
        item_id = str(row.get("evaluationRuleId") or row.get("evaluationItemId") or row.get("id") or f"legacy-evaluation-{position:04d}")
        text = _text(row) or str(row.get("item") or row.get("factor") or "").strip()
        if not text:
            warnings.append(f"{item_id}: missing evaluation text; retained in legacy only")
            continue
        rules.append(EvaluationRule(
            evaluationRuleId=item_id,
            title=str(row.get("title") or row.get("item") or row.get("factor") or item_id),
            text=text,
            sourceTrace=_traces(row, blocks, source_file, warnings, item_id),
        ))
        if row.get("score") is not None or row.get("maxScore") is not None:
            warnings.append(f"{item_id}: legacy score is unverified and retained in legacy only")
    project_meta = {**_dict(snapshot.get("projectMeta")), **_dict(bundle.get("projectMeta"))}
    nested_meta = project_meta.pop("projectMeta", None)
    if isinstance(nested_meta, dict):
        project_meta = {**project_meta, **nested_meta}
        warnings.append("Flattened legacy projectMeta.projectMeta; original preserved in legacy")
    elif nested_meta is not None:
        project_meta["projectMeta"] = nested_meta
    bundle_id = str(bundle.get("parseBundleId") or "")
    if not bundle_id:
        fingerprint = sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:16]
        bundle_id = f"legacy-{fingerprint}"
        warnings.append("Missing parseBundleId; assigned deterministic legacy snapshot id")
    tender = ResolvedTenderV2(
        parseBundleId=bundle_id,
        projectId=str(bundle.get("projectId") or ""),
        workspaceId=str(bundle.get("workspaceId") or ""),
        sourceFile=source_file,
        projectMeta=project_meta,
        documentBlocks=document_blocks,
        submissionStructure=_dict(bundle.get("submissionStructure")) or _dict(_dict(bundle.get("bidFormatSpec")).get("submissionStructure")),
        templateFragments=_rows(bundle.get("templateFragments")),
        templateFragmentsArtifact=str(bundle.get("templateFragmentsArtifact") or ""),
        requirements=requirements,
        evaluationRules=rules,
        company=_dict(snapshot.get("company")),
        organization=_dict(snapshot.get("organization")),
        knowledge=_dict(snapshot.get("knowledge")),
        risk=_dict(snapshot.get("risk")),
        legacy=snapshot,
        migrationWarnings=warnings,
        origin="legacy_migrated",
    )
    return tender.model_dump(mode="json")
