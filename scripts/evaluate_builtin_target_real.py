"""Evaluate builtin target-oriented extraction on local real tender files.

Run from the repository root:

    python scripts/evaluate_builtin_target_real.py

The default sample set uses five files from ``demo-data``.  The report is
written as UTF-8 JSON so Chinese source excerpts remain inspectable.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.builtin_target_processing import (  # noqa: E402
    _is_heading_or_header,
    extract_builtin_target_document,
)
from backend.app.tender_matching.builtin_target_map import (  # noqa: E402
    ENTERPRISE_MATCH_TARGETS,
    MATCH_STAGE_TARGETS,
    RISK_CHECK_TARGETS,
    _is_attachment_reference,
    _is_contract_reference_heading,
    _is_directory_line,
    _section_heading_blob,
)
from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402


DEFAULT_INDICES = (0, 1, 2, 8, 9)
SUPPORTED_EXTENSIONS = {".pdf", ".doc", ".docx"}
TARGET_KEYS = (
    "technical",
    "qualification",
    "commercial",
    "evaluation",
    "contract",
    "template",
    "project_info",
    "rejection",
)
MATCH_TARGETS = set(ENTERPRISE_MATCH_TARGETS)
MATCH_STAGE_TARGET_SET = set(MATCH_STAGE_TARGETS)
RISK_CHECK_TARGET_SET = set(RISK_CHECK_TARGETS)
NON_FULL_SCAN_TARGETS = {
    "technical",
    "qualification",
    "commercial",
    "evaluation",
    "contract",
    "template",
}

STRUCTURAL_TEMPLATE_TERMS = (
    "\u6295\u6807\u51fd",
    "\u54cd\u5e94\u8868",
    "\u504f\u79bb\u8868",
    "\u6295\u6807\u6587\u4ef6\u683c\u5f0f",
    "\u627f\u8bfa\u51fd",
    "\u6388\u6743\u59d4\u6258\u4e66",
    "\u6cd5\u5b9a\u4ee3\u8868\u4eba\u8eab\u4efd\u8bc1\u660e",
    "\u7531\u6295\u6807\u4eba\u586b\u5199",
    "\u8bf7\u6309\u672c\u7ae0\u683c\u5f0f\u586b\u5199",
    "\u7b7e\u5b57\u76d6\u7ae0",
)

NOTICE_TERMS = (
    "\u62db\u6807\u516c\u544a",
    "\u91c7\u8d2d\u516c\u544a",
    "\u6295\u6807\u9080\u8bf7",
    "\u6295\u6807\u9080\u8bf7\u4e66",
    "\u4e2d\u6807\u7ed3\u679c\u516c\u544a",
    "\u6210\u4ea4\u516c\u544a",
    "\u66f4\u6b63\u516c\u544a",
)

CONTRACT_HEADING_TERMS = (
    "\u653f\u5e9c\u91c7\u8d2d\u5408\u540c",
    "\u5408\u540c\u6587\u672c",
    "\u5408\u540c\u683c\u5f0f",
    "\u5408\u540c\u4e3b\u8981\u6761\u6b3e",
    "\u5408\u540c\u534f\u8bae\u4e66",
    "\u62df\u7b7e\u8ba2\u5408\u540c\u6587\u672c",
    "\u5c65\u7ea6\u5408\u540c",
)

EVALUATION_PROCESS_TERMS = (
    "\u8bc4\u6807\u59d4\u5458\u4f1a",
    "\u8bc4\u5ba1\u7a0b\u5e8f",
    "\u8bc4\u6807\u7a0b\u5e8f",
    "\u8bc4\u5ba1\u7eaa\u5f8b",
    "\u8bc4\u6807\u7eaa\u5f8b",
    "\u8bc4\u6807\u590d\u6838",
    "\u505c\u6b62\u8bc4\u6807",
)

EVALUATION_RULE_TERMS = (
    "\u8bc4\u5206\u6807\u51c6",
    "\u8bc4\u5206\u529e\u6cd5",
    "\u8bc4\u5206\u7ec6\u5219",
    "\u8bc4\u5ba1\u56e0\u7d20",
    "\u5206\u503c",
    "\u5f97\u5206",
    "\u6743\u91cd",
)


def _mime(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": (
            "application/"
            "vnd.openxmlformats-officedocument.wordprocessingml.document"
        ),
    }.get(path.suffix.lower(), "")


def _text(value: Any, limit: int = 600) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _ids(value: Any) -> list[str]:
    if isinstance(value, list):
        return list(dict.fromkeys(str(item) for item in value if str(item)))
    return [str(value)] if str(value) else []


def _source_id(item: dict[str, Any]) -> str:
    return str(
        item.get("sourceSegmentId")
        or next(iter(_ids(item.get("sourceSegmentIds"))), "")
        or next(iter(_ids(item.get("segmentIds"))), "")
    )


def _all_demo_files() -> list[Path]:
    return [
        path
        for path in sorted(
            (REPO_ROOT / "demo-data").iterdir(),
            key=lambda item: item.name.lower(),
        )
        if (
            path.is_file()
            and not path.name.startswith("~$")
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    ]


def _selected_files(indices: tuple[int, ...]) -> list[Path]:
    files = _all_demo_files()
    selected: list[Path] = []
    for index in indices:
        if index < 0 or index >= len(files):
            raise IndexError(
                f"demo-data file index {index} is out of range; "
                f"available indexes: 0..{len(files) - 1}"
            )
        selected.append(files[index])
    return selected


def _section_index(sections: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for section in sections:
        section_id = str(section.get("sectionId") or section.get("id") or "")
        if section_id:
            result[section_id] = section
    return result


def _segment_owner_map(
    sections: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    owners: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for section in sections:
        for segment_id in _ids(
            section.get("segmentIds") or section.get("sourceSegmentIds")
        ):
            owners[segment_id].append(section)
    return owners


def _owner_summary(
    segment_id: str,
    owners: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    section = _best_owner(segment_id, owners)
    return {
        "sectionId": str(section.get("sectionId") or section.get("id") or ""),
        "sectionPath": [
            _text(value, 180)
            for value in section.get("sectionPath") or []
            if _text(value, 180)
        ],
    }


def _best_owner(
    segment_id: str,
    owners: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    candidates = owners.get(segment_id) or []
    return min(
        candidates,
        key=lambda item: (
            len(_ids(item.get("segmentIds") or item.get("sourceSegmentIds"))),
            -int(item.get("level") or 0),
        ),
        default={},
    )


def _owner_heading(section: dict[str, Any]) -> str:
    return " ".join(
        [
            str(section.get("heading") or ""),
            str(section.get("title") or ""),
        ]
    )


def _owner_has_contract_context(section: dict[str, Any]) -> bool:
    section_type = str(section.get("sectionType") or "").strip().lower()
    if section_type == "contract":
        return True
    heading = _section_heading_blob(section)
    return bool(
        any(term in heading for term in CONTRACT_HEADING_TERMS)
        and not _is_contract_reference_heading(heading)
    )


def _structural_template_hit(text: str) -> str:
    compact = "".join(str(text or "").split())
    return next(
        (
            term
            for term in STRUCTURAL_TEMPLATE_TERMS
            if term in compact
        ),
        "",
    )


def _attachment_reference_only(text: str) -> bool:
    clean = " ".join(str(text or "").split()).strip()
    if _is_attachment_reference(clean):
        return True
    return bool(
        re.match(
            r"^(?:\u8be6\u89c1|\u53c2\u89c1|\u89c1)\s*"
            r"(?:\u9644\u4ef6)?\s*[:\uff1a]",
            clean,
        )
    )


def _notice_hit(text: str, section: dict[str, Any]) -> str:
    del section
    blob = str(text or "")
    return next(
        (term for term in NOTICE_TERMS if term in blob),
        "",
    )


def _evaluation_process_hit(text: str, section: dict[str, Any]) -> str:
    heading = _owner_heading(section)
    if any(term in heading for term in EVALUATION_PROCESS_TERMS):
        return next(
            (term for term in EVALUATION_PROCESS_TERMS if term in heading),
            "\u8bc4\u5ba1\u6d41\u7a0b",
        )
    compact = "".join(str(text or "").split())
    if "\u8bc4\u6807\u59d4\u5458\u4f1a" in compact and not any(
        term in compact for term in EVALUATION_RULE_TERMS
    ):
        return "\u8bc4\u6807\u59d4\u5458\u4f1a\u6d41\u7a0b"
    return ""


def _noise_flags(
    target_key: str,
    segment_ids: list[str],
    segment_map: dict[str, dict[str, Any]],
    owners: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    if target_key not in NON_FULL_SCAN_TARGETS or target_key == "contract":
        return []
    results: list[dict[str, Any]] = []
    for segment_id in segment_ids:
        segment = segment_map.get(segment_id) or {}
        text = _text(segment.get("text"), 800)
        owner = _best_owner(segment_id, owners)
        hits: list[str] = []
        if _is_directory_line(text):
            hits.append("directory")
        if _notice_hit(text, owner):
            hits.append("notice")
        if target_key not in {"template", "contract"}:
            if _attachment_reference_only(text):
                hits.append("attachment")
            if _structural_template_hit(text):
                hits.append("template")
        if target_key != "contract" and _owner_has_contract_context(owner):
            hits.append("contract")
        if target_key == "evaluation":
            process_hit = _evaluation_process_hit(text, owner)
            if process_hit:
                hits.append("evaluation_process")
        if (
            target_key not in {"template", "contract"}
            and segment.get("tableId")
            and _is_heading_or_header(text)
        ):
            hits.append("table_header")
        if hits:
            results.append(
                {
                    "segmentId": segment_id,
                    "sourceLocator": _text(segment.get("sourceLocator"), 200),
                    "hits": list(dict.fromkeys(hits)),
                    "text": text,
                }
            )
    return results


def _sample_items(target: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": item.get("id"),
            "sourceSegmentId": _source_id(item),
            "sourceLocator": item.get("sourceLocator"),
            "sectionPath": item.get("sectionPath") or [],
            "tableIds": item.get("tableIds") or [],
            "productName": item.get("productName"),
            "targetType": item.get("targetType"),
            "quantity": item.get("quantity"),
            "unit": item.get("unit"),
            "parameters": _text(item.get("parameters"), 300),
            "text": _text(item.get("text"), 600),
        }
        for item in (target.get("items") or [])[:8]
        if isinstance(item, dict)
    ]


def _sample_segments(
    target: dict[str, Any],
    segment_map: dict[str, dict[str, Any]],
    owners: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for segment_id in _ids(target.get("segmentIds"))[:8]:
        segment = segment_map.get(segment_id) or {}
        samples.append(
            {
                "segmentId": segment_id,
                "sourceLocator": _text(segment.get("sourceLocator"), 200),
                "owner": _owner_summary(segment_id, owners),
                "tableId": str(segment.get("tableId") or ""),
                "contentType": segment.get("contentType"),
                "text": _text(segment.get("text"), 600),
            }
        )
    return samples


def _target_overlaps(
    targets: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    keys = list(targets)
    for index, left_key in enumerate(keys):
        left_ids = set(_ids(targets[left_key].get("segmentIds")))
        for right_key in keys[index + 1 :]:
            right_ids = set(_ids(targets[right_key].get("segmentIds")))
            common = sorted(left_ids & right_ids)
            if common:
                result[f"{left_key}__{right_key}"] = {
                    "count": len(common),
                    "sampleSegmentIds": common[:30],
                }
    return result


def _mixed_sections(
    targets: dict[str, dict[str, Any]],
    owners: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    section_targets: dict[str, set[str]] = defaultdict(set)
    for target_key, target in targets.items():
        if target_key in {"project_info", "rejection"}:
            continue
        for segment_id in _ids(target.get("segmentIds")):
            for section in owners.get(segment_id) or []:
                section_id = str(section.get("sectionId") or section.get("id") or "")
                if section_id:
                    section_targets[section_id].add(target_key)
    return [
        {
            "sectionId": section_id,
            "targetModules": sorted(target_keys),
        }
        for section_id, target_keys in sorted(section_targets.items())
        if len(target_keys) > 1
    ]


def _evaluate(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    document = parse_document_ir(
        content,
        file_name=path.name,
        mime_type=_mime(path),
    )
    segments = [
        item.model_dump(mode="json", by_alias=True)
        for item in document.segments
    ]
    sections = [
        item.model_dump(mode="json", by_alias=True)
        for item in document.sections
    ]
    tables = [
        item.model_dump(mode="json", by_alias=True)
        for item in document.tables
    ]
    extraction = extract_builtin_target_document(
        text=document.text,
        segments=segments,
        sections=sections,
        tables=tables,
        ai={"enabled": False},
        source_file_id=path.name,
        project_id="real-tender-acceptance",
        document_meta={
            "fileName": path.name,
            "contentHash": hashlib.sha256(content).hexdigest(),
        },
    )
    target_list = [
        item
        for item in extraction.get("targetModules") or []
        if isinstance(item, dict)
    ]
    targets = {
        str(item.get("targetModule")): item
        for item in target_list
        if str(item.get("targetModule") or "")
    }
    segment_map = {
        str(item.get("id")): item
        for item in segments
        if str(item.get("id") or "")
    }
    owners = _segment_owner_map(sections)
    source_ids = set(segment_map)

    target_reports: dict[str, dict[str, Any]] = {}
    invalid_source_ids: list[dict[str, Any]] = []
    for target_key in TARGET_KEYS:
        target = targets.get(target_key) or {
            "targetModule": target_key,
            "segmentIds": [],
            "sectionIds": [],
            "tableIds": [],
            "ranges": [],
            "items": [],
        }
        selected_segment_ids = _ids(target.get("segmentIds"))
        selected_section_ids = _ids(target.get("sectionIds"))
        selected_table_ids = _ids(target.get("tableIds"))
        items = [
            item
            for item in target.get("items") or []
            if isinstance(item, dict)
        ]
        item_segment_ids: list[str] = []
        for item in items:
            item_source_ids = _ids(
                item.get("sourceSegmentIds")
                or item.get("segmentIds")
                or item.get("sourceSegmentId")
            )
            item_segment_ids.extend(item_source_ids)
            unknown = sorted(set(item_source_ids) - source_ids)
            if unknown:
                invalid_source_ids.append(
                    {
                        "targetModule": target_key,
                        "itemId": item.get("id"),
                        "unknownSourceSegmentIds": unknown,
                    }
                )
        table_item_count = sum(
            bool(_ids(item.get("tableIds")))
            for item in items
        )
        target_reports[target_key] = {
            "selectionSource": target.get("selectionSource"),
            "confidence": target.get("confidence"),
            "selectedSegmentCount": len(selected_segment_ids),
            "selectedSectionCount": len(selected_section_ids),
            "selectedTableCount": len(selected_table_ids),
            "rangeCount": len(target.get("ranges") or []),
            "itemCount": len(items),
            "tableItemCount": table_item_count,
            "sampleItems": _sample_items(target),
            "sampleSegments": _sample_segments(target, segment_map, owners),
            "noiseFlags": _noise_flags(
                target_key,
                selected_segment_ids,
                segment_map,
                owners,
            ),
            "itemNoiseFlags": _noise_flags(
                target_key,
                list(dict.fromkeys(item_segment_ids)),
                segment_map,
                owners,
            ),
        }
        target_reports[target_key]["itemNoiseFlagCount"] = len(
            target_reports[target_key]["itemNoiseFlags"]
        )
        target_reports[target_key]["tableHeaderItemCount"] = sum(
            "table_header" in item["hits"]
            for item in target_reports[target_key]["itemNoiseFlags"]
        )

    project_info_items = [
        item
        for item in (targets.get("project_info") or {}).get("items") or []
        if isinstance(item, dict)
    ]
    rejection_items = [
        item
        for item in (targets.get("rejection") or {}).get("items") or []
        if isinstance(item, dict)
    ]
    contract_items = [
        item
        for item in (targets.get("contract") or {}).get("items") or []
        if isinstance(item, dict)
    ]
    match_stage_requirements = [
        item
        for target_key in MATCH_STAGE_TARGET_SET
        for item in (targets.get(target_key) or {}).get("items") or []
        if isinstance(item, dict)
    ]
    match_requirements = [
        item
        for target_key in MATCH_TARGETS
        for item in (targets.get(target_key) or {}).get("items") or []
        if isinstance(item, dict)
    ]
    checks = {
        "parsed": document.diagnostics.status == "parsed",
        "noParserWarnings": not document.warnings and not document.structure_warnings,
        "allItemSourcesExist": not invalid_source_ids,
        "contractHasNoItems": not contract_items,
        "matchRequirementsOnlyUseMatchTargets": all(
            item.get("targetModule") in MATCH_TARGETS
            for item in match_requirements
        ),
        "matchStageRequirementsOnlyUseThreeTargets": all(
            item.get("targetModule") in MATCH_STAGE_TARGET_SET
            for item in match_stage_requirements
        ),
        "rejectionItemsAreRiskOnly": all(
            item.get("targetModule") in RISK_CHECK_TARGET_SET
            and bool(item.get("isRejection"))
            and not item.get("matchedFactIds")
            and not item.get("matchedObjectIds")
            and not item.get("evidenceIds")
            for item in (
                targets.get("rejection") or {}
            ).get("items") or []
            if isinstance(item, dict)
        ),
        "projectInfoExtracted": bool(project_info_items),
        "rejectionItemsHaveSources": all(
            bool(_ids(item.get("sourceSegmentIds") or item.get("sourceSegmentId")))
            for item in rejection_items
        ),
        "templateItemsHaveSources": all(
            bool(_ids(item.get("sourceSegmentIds") or item.get("sourceSegmentId")))
            for item in (
                targets.get("template") or {}
            ).get("items") or []
            if isinstance(item, dict)
        ),
    }
    hard_failures = [
        key
        for key in (
            "parsed",
            "noParserWarnings",
            "allItemSourcesExist",
            "contractHasNoItems",
            "matchRequirementsOnlyUseMatchTargets",
            "matchStageRequirementsOnlyUseThreeTargets",
            "rejectionItemsAreRiskOnly",
        )
        if not checks[key]
    ]
    review_flags = {
        "noiseFlagCount": sum(
            len(item["noiseFlags"])
            for item in target_reports.values()
        ),
        "itemNoiseFlagCount": sum(
            item["itemNoiseFlagCount"]
            for item in target_reports.values()
        ),
        "tableHeaderItemCount": sum(
            item["tableHeaderItemCount"]
            for item in target_reports.values()
        ),
        "mixedSectionCount": len(_mixed_sections(targets, owners)),
        "nonEmptyMatchTargetCount": sum(
            bool(target_reports[key]["itemCount"])
            for key in MATCH_TARGETS
        ),
        "nonEmptyTemplateCount": target_reports["template"]["itemCount"],
        "nonEmptyEvaluationCount": target_reports["evaluation"]["itemCount"],
        "nonEmptyRejectionCount": len(rejection_items),
    }
    return {
        "fileName": path.name,
        "path": str(path.resolve()),
        "mode": "rule-only",
        "parse": {
            "status": document.diagnostics.status,
            "method": document.parse_method,
            "format": document.original_format,
            "textChars": len(document.text),
            "pageCount": len(document.pages),
            "segmentCount": len(segments),
            "sectionCount": len(sections),
            "tableCount": len(tables),
            "warnings": list(document.warnings),
            "structureWarnings": list(document.structure_warnings),
        },
        "targets": target_reports,
        "matching": {
            "targetModules": list(MATCH_STAGE_TARGETS),
            "enterpriseMatchTargets": list(ENTERPRISE_MATCH_TARGETS),
            "riskCheckTargets": list(RISK_CHECK_TARGETS),
            "matchStageRequirementCount": len(match_stage_requirements),
            "enterpriseMatchRequirementCount": len(match_requirements),
            "riskCheckRequirementCount": len(rejection_items),
        },
        "checks": checks,
        "hardFailures": hard_failures,
        "reviewFlags": review_flags,
        "invalidSourceIds": invalid_source_ids,
        "overlaps": _target_overlaps(targets),
        "mixedSections": _mixed_sections(targets, owners),
        "diagnostics": (extraction.get("stats") or {}).get("targetMap") or {},
        "extractionWarnings": extraction.get("warnings") or [],
    }


def _report(samples: list[dict[str, Any]], indices: tuple[int, ...]) -> dict[str, Any]:
    return {
        "schemaVersion": "builtin-target-real-acceptance/1.0",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "sourceDirectory": str((REPO_ROOT / "demo-data").resolve()),
        "mode": "rule-only",
        "selectedIndices": list(indices),
        "summary": {
            "sampleCount": len(samples),
            "passedHardChecks": sum(not item["hardFailures"] for item in samples),
            "totalHardFailures": sum(
                len(item["hardFailures"])
                for item in samples
            ),
            "totalNoiseFlags": sum(
                item["reviewFlags"]["noiseFlagCount"]
                for item in samples
            ),
            "totalItemNoiseFlags": sum(
                item["reviewFlags"]["itemNoiseFlagCount"]
                for item in samples
            ),
            "totalMixedSections": sum(
                item["reviewFlags"]["mixedSectionCount"]
                for item in samples
            ),
        },
        "samples": samples,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--indices",
        default=",".join(str(item) for item in DEFAULT_INDICES),
        help="Comma-separated sorted demo-data indexes.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="UTF-8 JSON output path.",
    )
    args = parser.parse_args()
    indices = tuple(
        int(value.strip())
        for value in str(args.indices).split(",")
        if value.strip()
    )
    samples = [_evaluate(path) for path in _selected_files(indices)]
    report = _report(samples, indices)
    output = (
        Path(args.output)
        if args.output
        else (
            REPO_ROOT
            / "runtime"
            / "real-tender-acceptance"
            / f"builtin-target-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        )
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output": str(output.resolve()),
                "sampleCount": report["summary"]["sampleCount"],
                "passedHardChecks": report["summary"]["passedHardChecks"],
                "totalHardFailures": report["summary"]["totalHardFailures"],
                "totalNoiseFlags": report["summary"]["totalNoiseFlags"],
                "totalItemNoiseFlags": report["summary"]["totalItemNoiseFlags"],
                "totalMixedSections": report["summary"]["totalMixedSections"],
            },
            ensure_ascii=True,
        )
    )
    return 0 if report["summary"]["totalHardFailures"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
