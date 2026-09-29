"""Builtin extraction artifact persistence.

This is deliberately independent from WorkBuddy.  The builtin channel may
persist its source-backed analysis pack, but it does not enqueue or consume an
external agent job.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PACK_SCHEMA_VERSION = "tender-pack/1.0"
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _safe_text(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_dir_name(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(
        r"[^0-9A-Za-z._\u4e00-\u9fff-]+",
        "-",
        str(value or ""),
    ).strip("-. ")
    return cleaned[:80] or fallback


def _compact_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(scope, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in ("stats", "warnings"):
        if scope.get(key) not in (None, "", [], {}):
            compact[key] = scope[key]
    for key in (
        "selectedSections",
        "selectedTables",
        "scopeItems",
        "references",
        "unresolvedReferences",
        "candidateSections",
    ):
        value = scope.get(key)
        if isinstance(value, list):
            compact[f"{key}Count"] = len(value)
    selected = scope.get("selectedSegments") or []
    compact["selectedSegmentCount"] = len(selected)
    compact["selectedSegmentIds"] = [
        str(item.get("id"))
        for item in selected
        if isinstance(item, dict) and item.get("id")
    ][:4000]
    compact["llmSectionSource"] = _safe_text(scope.get("llmSectionSource"), 40)
    compact["llmSectionPickedCount"] = int(scope.get("llmSectionPickedCount") or 0)
    compact["llmSections"] = [
        {
            "sectionId": str(item.get("sectionId") or ""),
            "heading": _safe_text(item.get("heading"), 200),
        }
        for item in (scope.get("llmSections") or [])
        if isinstance(item, dict)
    ][:200]
    targets = scope.get("targets")
    if isinstance(targets, list):
        compact["targetRangeCount"] = len(targets)
        compact["targetRanges"] = [
            {
                "targetModule": _safe_text(item.get("targetModule"), 40),
                "selectionSource": _safe_text(item.get("selectionSource"), 40),
                "sectionIds": list(item.get("sectionIds") or [])[:200],
                "segmentIds": list(item.get("segmentIds") or [])[:4000],
                "tableIds": list(item.get("tableIds") or [])[:200],
                "ranges": list(item.get("ranges") or [])[:200],
            }
            for item in targets
            if isinstance(item, dict)
        ]
    return compact


def _pack_dir(pack_dir: str | Path | None, project_id: str) -> Path:
    raw = Path(str(pack_dir or "runtime/projects"))
    root = raw if raw.is_absolute() else PROJECT_ROOT / raw
    return root / _safe_dir_name(project_id, "unassigned") / "pack"


def write_tender_pack(
    *,
    project_id: str,
    source_file_id: str,
    requirements: list[dict[str, Any]] | None = None,
    scope: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    extraction_method: str = "",
    model_used: str = "",
    document_meta: dict[str, Any] | None = None,
    analysis_pack: dict[str, Any] | None = None,
    pack_dir: str | Path | None = None,
    job_id: str = "",
) -> Path | None:
    requirements = [item for item in (requirements or []) if isinstance(item, dict)]
    analysis_pack = analysis_pack if isinstance(analysis_pack, dict) else {}
    # 8 域目标分析包（target-oriented）键；10 模块键已随主线收敛移除。
    # `templateIndex` 是 `templates` 的写作侧别名，仍由目标路由产出，保留。
    keys = (
        "targetModules",
        "targetRanges",
        "targetModuleMap",
        "projectInfo",
        "templates",
        "templateIndex",
        "matchStageRequirements",
        "enterpriseMatchRequirements",
        "riskCheckRequirements",
    )
    if not requirements and not any(
        isinstance(analysis_pack.get(key), list) and analysis_pack.get(key)
        for key in keys
    ):
        return None

    packed: list[dict[str, Any]] = []
    for index, item in enumerate(requirements, start=1):
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        source_ids = [
            str(value)
            for value in (
                item.get("sourceSegmentIds")
                or [item.get("sourceSegmentId")]
                or source.get("segmentIds")
            )
            if str(value)
        ]
        packed.append(
            {
                "requirementId": _safe_text(
                    item.get("id") or item.get("requirementId") or f"req-{index:03d}",
                    100,
                ),
                "category": _safe_text(item.get("category") or "other", 40),
                "requirement": _safe_text(
                    item.get("normalizedRequirement")
                    or item.get("rawText")
                    or item.get("text"),
                ),
                "rawText": _safe_text(item.get("rawText") or item.get("text")),
                "mandatory": bool(item.get("mandatory", True)),
                "isRejection": bool(item.get("isRejection")),
                "responseType": _safe_text(item.get("responseType"), 80),
                "evidenceNeed": _safe_text(item.get("evidenceNeed"), 80),
                "handlingMode": _safe_text(item.get("handlingMode"), 40),
                "confidence": item.get("confidence"),
                "source": {
                    "segmentId": source_ids[0] if source_ids else "",
                    "segmentIds": source_ids,
                    "sourceLocator": _safe_text(
                        item.get("sourceLocator") or source.get("sourceLocator"),
                        200,
                    ),
                    "sectionId": _safe_text(
                        item.get("sectionId") or source.get("sectionId"),
                        100,
                    ),
                    "sectionPath": list(
                        item.get("sectionPath") or source.get("sectionPath") or []
                    ),
                    "tableIds": list(
                        item.get("tableIds") or source.get("tableIds") or []
                    ),
                },
            }
        )

    safe_meta = {
        key: value
        for key, value in (document_meta or {}).items()
        if key not in {"parsed", "rawText", "text", "segments", "sections", "tables"}
    }
    stats = dict(stats or {})
    pack = {
        "schemaVersion": PACK_SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": {
            "projectId": project_id,
            "sourceFileId": source_file_id,
            "jobId": job_id,
            "document": safe_meta,
        },
        "extraction": {
            "method": extraction_method,
            "provider": stats.get("extractionProvider") or "builtin",
            "modelUsed": model_used,
            "requirementCount": len(packed),
            "warnings": warnings or [],
            "stats": stats,
        },
        "scope": _compact_scope(scope),
        "requirements": packed,
        "targetModules": analysis_pack.get("targetModules") or [],
        "targetRanges": analysis_pack.get("targetRanges") or [],
        "projectInfo": analysis_pack.get("projectInfo") or [],
        "templates": analysis_pack.get("templates") or analysis_pack.get("templateIndex") or [],
        "analysisPack": analysis_pack,
    }
    directory = _pack_dir(pack_dir, project_id)
    directory.mkdir(parents=True, exist_ok=True)
    latest = directory / "tender_pack.json"
    temporary = latest.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(pack, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(latest)
    if job_id:
        archive = directory / "archive"
        archive.mkdir(parents=True, exist_ok=True)
        archived = archive / f"tender_pack.{_safe_dir_name(job_id)}.json"
        if not archived.exists():
            archived.write_text(
                json.dumps(pack, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    return latest


__all__ = ["PACK_SCHEMA_VERSION", "write_tender_pack"]
