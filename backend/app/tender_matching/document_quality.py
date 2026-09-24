"""Quality checks for the typed DocumentIR parser boundary.

The evaluator is deliberately independent from business extraction gold data.
It measures whether a parsed document is structurally safe for later section,
requirement, evidence, and matching stages.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from .document_models import DocumentIR, document_ir_to_json


DIMENSION_MAX = {
    "parse": 20,
    "text": 15,
    "segments": 15,
    "provenance": 15,
    "pages": 10,
    "tables": 10,
    "sections": 10,
    "diagnostics": 5,
}


def _issue(
    issues: list[dict[str, str]],
    severity: str,
    code: str,
    message: str,
) -> None:
    issues.append({"severity": severity, "code": code, "message": message})


def _coverage(found: int, total: int) -> float:
    return round(found / total, 4) if total else 1.0


def _score_from_checks(
    maximum: int,
    checks: Iterable[tuple[bool, float]],
) -> int:
    score = sum(weight for passed, weight in checks if passed)
    return round(max(0.0, min(float(maximum), score)))


def _grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 80:
        return "B"
    if score >= 70:
        return "C"
    if score >= 60:
        return "D"
    return "F"


def _readiness(
    score: int,
    hard_failure: bool,
    *,
    needs_structural_review: bool = False,
) -> str:
    if hard_failure or score < 70:
        return "not_ready"
    if score < 85 or needs_structural_review:
        return "usable_with_review"
    return "ready_for_next_stage"


def _valid_bbox(bbox: list[float] | None) -> bool:
    if not isinstance(bbox, list) or len(bbox) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(item) for item in bbox)
    except (TypeError, ValueError):
        return False
    return x1 > x0 and y1 > y0


def _check_segment_integrity(
    document: DocumentIR,
    issues: list[dict[str, str]],
) -> tuple[int, dict[str, Any]]:
    segments = document.segments
    units = document.units
    non_empty = [segment for segment in segments if segment.text.strip()]
    segment_ids = [segment.id for segment in segments]
    unit_ids = [unit.id for unit in units]
    actual_orders = [segment.order for segment in segments]
    reconstructed = "\n".join(segment.text for segment in segments)
    text_reconstructed = reconstructed == document.text
    ids_unique = len(segment_ids) == len(set(segment_ids)) and all(segment_ids)
    unit_ids_unique = len(unit_ids) == len(set(unit_ids)) and all(unit_ids)
    orders_monotonic = all(
        left < right
        for left, right in zip(actual_orders, actual_orders[1:])
    )

    if not segments and document.text.strip():
        _issue(issues, "critical", "text_without_segments", "DocumentIR has text but no segments")
    if not ids_unique:
        _issue(issues, "critical", "duplicate_segment_ids", "Segment IDs are empty or duplicated")
    if not orders_monotonic:
        _issue(issues, "warning", "segment_order", "Segment order is not strictly increasing")
    if not text_reconstructed:
        _issue(issues, "warning", "text_reconstruction", "Document text differs from ordered segment text")
    if not unit_ids_unique:
        _issue(issues, "warning", "duplicate_unit_ids", "Unit IDs are empty or duplicated")

    score = _score_from_checks(
        DIMENSION_MAX["segments"],
        (
            (bool(non_empty), 4),
            (ids_unique, 4),
            (orders_monotonic, 3),
            (text_reconstructed, 3),
            (unit_ids_unique, 1),
        ),
    )
    return score, {
        "segmentCount": len(segments),
        "nonEmptySegmentCount": len(non_empty),
        "unitCount": len(units),
        "segmentIdCoverage": _coverage(sum(bool(item) for item in segment_ids), len(segment_ids)),
        "sourceTextReconstruction": text_reconstructed,
        "segmentIdsUnique": ids_unique,
        "unitIdsUnique": unit_ids_unique,
        "ordersMonotonic": orders_monotonic,
    }


def _check_pages(
    document: DocumentIR,
    issues: list[dict[str, str]],
) -> tuple[int, dict[str, Any]]:
    pages = document.pages
    segments = document.segments
    is_pdf = document.original_format.lower() == "pdf"

    if not is_pdf:
        return DIMENSION_MAX["pages"], {
            "applicable": False,
            "pageCount": len(pages),
            "reason": "physical page coordinates are format-dependent",
        }

    page_numbers = [page.page_number for page in pages]
    contiguous = page_numbers == list(range(1, len(pages) + 1))
    segment_ids = {segment.id for segment in segments}
    page_segment_ids = [
        segment_id
        for page in pages
        for segment_id in page.segment_ids
    ]
    page_segment_refs_valid = all(item in segment_ids for item in page_segment_ids)
    page_refs = [segment for segment in segments if segment.page is not None]
    page_ref_coverage = _coverage(len(page_refs), len(segments))
    bbox_segments = [segment for segment in segments if _valid_bbox(segment.bbox)]
    bbox_coverage = _coverage(len(bbox_segments), len(segments))
    dimensions_valid = all(
        (page.width or 0) > 0 and (page.height or 0) > 0
        for page in pages
        if page.has_text
    )

    if not pages:
        _issue(issues, "critical", "pdf_pages_missing", "PDF DocumentIR has no page records")
    if not contiguous:
        _issue(issues, "warning", "page_sequence", "PDF page numbers are not contiguous")
    if not page_segment_refs_valid:
        _issue(issues, "critical", "page_segment_reference", "Page references an unknown segment")
    if page_ref_coverage < 1.0:
        _issue(issues, "warning", "segment_page_coverage", "Some PDF segments have no page reference")
    if bbox_coverage < 1.0:
        _issue(
            issues,
            "warning",
            "pdf_bbox_missing",
            "PDF segments do not all have block coordinates; evidence highlighting is incomplete",
        )

    score = _score_from_checks(
        DIMENSION_MAX["pages"],
        (
            (bool(pages), 2),
            (contiguous, 1),
            (page_segment_refs_valid, 2),
            (page_ref_coverage == 1.0, 2),
            (bbox_coverage == 1.0, 2),
            (dimensions_valid, 1),
        ),
    )
    return score, {
        "applicable": True,
        "pageCount": len(pages),
        "pageSequenceValid": contiguous,
        "pageSegmentReferencesValid": page_segment_refs_valid,
        "segmentPageCoverage": page_ref_coverage,
        "bboxCoverage": bbox_coverage,
        "pageDimensionsValid": dimensions_valid,
        "scanPageCount": sum(page.is_scan for page in pages),
    }


def _check_tables(
    document: DocumentIR,
    issues: list[dict[str, str]],
) -> tuple[int, dict[str, Any]]:
    tables = document.tables
    segments = document.segments
    if not tables:
        return DIMENSION_MAX["tables"], {
            "tableCount": 0,
            "applicable": False,
            "reason": "document has no extracted tables",
        }

    table_ids = [table.table_id or table.id for table in tables]
    table_ids_unique = len(table_ids) == len(set(table_ids)) and all(table_ids)
    row_count_valid = True
    column_count_valid = True
    cell_refs_valid = True
    non_empty_rows = 0
    cell_count = 0
    for table in tables:
        rows = table.rows
        row_count_valid = row_count_valid and table.row_count == len(rows)
        column_count_valid = column_count_valid and table.column_count == max(
            (len(row.cells) for row in rows),
            default=0,
        )
        for row in rows:
            if row.text.strip():
                non_empty_rows += 1
            for cell in row.cells:
                cell_count += 1
                cell_refs_valid = cell_refs_valid and (
                    cell.table_id in {"", table.table_id, table.id}
                    and cell.row_index in {None, row.row_index}
                )

    segment_table_refs_valid = all(
        not segment.table_id or segment.table_id in set(table_ids)
        for segment in segments
    )
    cell_refs_valid = cell_refs_valid and segment_table_refs_valid

    if not table_ids_unique:
        _issue(issues, "critical", "duplicate_table_ids", "Table IDs are empty or duplicated")
    if not row_count_valid:
        _issue(issues, "warning", "table_row_count", "A table rowCount does not match its rows")
    if not column_count_valid:
        _issue(issues, "warning", "table_column_count", "A table columnCount does not match its cells")
    if not cell_refs_valid:
        _issue(issues, "critical", "table_cell_reference", "A table cell or segment points to an unknown table row")

    score = _score_from_checks(
        DIMENSION_MAX["tables"],
        (
            (table_ids_unique, 2),
            (row_count_valid, 2),
            (column_count_valid, 2),
            (cell_refs_valid, 3),
            (non_empty_rows > 0, 1),
        ),
    )
    return score, {
        "applicable": True,
        "tableCount": len(tables),
        "rowCount": sum(len(table.rows) for table in tables),
        "cellCount": cell_count,
        "tableIdsUnique": table_ids_unique,
        "rowCountsValid": row_count_valid,
        "columnCountsValid": column_count_valid,
        "cellReferencesValid": cell_refs_valid,
        "nonEmptyRowCount": non_empty_rows,
    }


def _check_sections(
    document: DocumentIR,
    issues: list[dict[str, str]],
) -> tuple[int, dict[str, Any]]:
    sections = document.sections
    segment_ids = {segment.id for segment in document.segments}
    section_ids = [section.section_id or section.id for section in sections]
    ids_unique = len(section_ids) == len(set(section_ids)) and all(section_ids)
    references_valid = True
    parents_valid = True
    locator_coverage = 0
    explicit_heading_count = 0
    section_id_set = set(section_ids)

    for section in sections:
        references_valid = references_valid and all(
            segment_id in segment_ids for segment_id in section.segment_ids
        )
        if section.heading_segment_id:
            explicit_heading_count += 1
            references_valid = references_valid and section.heading_segment_id in segment_ids
        parents_valid = parents_valid and (
            not section.parent_id or section.parent_id in section_id_set
        )
        locator_coverage += bool(section.start_locator) and bool(section.end_locator)

    locator_ratio = _coverage(locator_coverage, len(sections))
    has_structure = bool(sections)
    has_explicit_headings = explicit_heading_count > 0
    if not has_structure and document.text.strip():
        _issue(issues, "critical", "sections_missing", "Non-empty DocumentIR has no sections")
    if not ids_unique:
        _issue(issues, "critical", "duplicate_section_ids", "Section IDs are empty or duplicated")
    if not references_valid:
        _issue(issues, "critical", "section_segment_reference", "Section references an unknown segment")
    if not parents_valid:
        _issue(issues, "warning", "section_parent_reference", "Section references an unknown parent")
    if locator_ratio < 1.0:
        _issue(issues, "warning", "section_locator_coverage", "Some sections lack start or end locators")
    if has_structure and not has_explicit_headings:
        _issue(
            issues,
            "warning",
            "implicit_section_only",
            "Only an implicit section was produced; chapter-level downstream routing needs review",
        )

    score = _score_from_checks(
        DIMENSION_MAX["sections"],
        (
            (has_structure, 2),
            (ids_unique, 2),
            (references_valid, 2),
            (parents_valid, 1),
            (locator_ratio == 1.0, 1),
            (has_explicit_headings, 2),
        ),
    )
    return score, {
        "sectionCount": len(sections),
        "sectionIdsUnique": ids_unique,
        "segmentReferencesValid": references_valid,
        "parentReferencesValid": parents_valid,
        "locatorCoverage": locator_ratio,
        "explicitHeadingSectionCount": explicit_heading_count,
        "implicitOnly": bool(sections) and not has_explicit_headings,
    }


def _check_provenance(
    document: DocumentIR,
    source_content: bytes | None,
    expected_file_name: str | None,
    issues: list[dict[str, str]],
) -> tuple[int, dict[str, Any]]:
    segments = document.segments
    located = sum(bool(segment.source_locator.strip()) for segment in segments)
    locator_coverage = _coverage(located, len(segments))
    hash_valid = True
    name_valid = True
    if source_content is not None:
        expected_hash = hashlib.sha256(source_content).hexdigest()
        hash_valid = document.file_hash == expected_hash
        if not hash_valid:
            _issue(issues, "critical", "file_hash", "DocumentIR fileHash does not match source bytes")
    if expected_file_name:
        name_valid = document.file_name == expected_file_name
        if not name_valid:
            _issue(issues, "warning", "file_name", "DocumentIR fileName does not match input file")
    if locator_coverage < 1.0:
        _issue(issues, "warning", "segment_locator_coverage", "Some segments have no source locator")
    if not document.document_id or not document.document_version_id:
        _issue(issues, "critical", "document_identity", "DocumentIR has no stable document identity")

    score = _score_from_checks(
        DIMENSION_MAX["provenance"],
        (
            (locator_coverage == 1.0, 6),
            (hash_valid, 4),
            (name_valid, 2),
            (bool(document.document_id), 1),
            (bool(document.document_version_id), 1),
            (bool(document.schema_version), 1),
        ),
    )
    return score, {
        "segmentLocatorCoverage": locator_coverage,
        "fileHashValid": hash_valid,
        "fileNameValid": name_valid,
        "documentIdPresent": bool(document.document_id),
        "documentVersionIdPresent": bool(document.document_version_id),
        "schemaVersion": document.schema_version,
    }


def _check_text(
    document: DocumentIR,
    source_content: bytes | None,
    issues: list[dict[str, str]],
) -> tuple[int, dict[str, Any]]:
    text = document.text
    non_whitespace = "".join(text.split())
    replacement_count = text.count("\ufffd")
    source_non_empty = source_content is None or bool(source_content.strip())
    has_text = bool(text.strip())
    replacement_ratio = _coverage(replacement_count, len(non_whitespace))
    if source_non_empty and not has_text:
        _issue(issues, "critical", "empty_text", "Non-empty source produced no text")
    if replacement_count:
        _issue(issues, "warning", "replacement_characters", "Extracted text contains Unicode replacement characters")
    score = _score_from_checks(
        DIMENSION_MAX["text"],
        (
            (has_text or not source_non_empty, 8),
            (replacement_count == 0, 5),
            (len(non_whitespace) > 20 or not has_text, 2),
        ),
    )
    return score, {
        "textChars": len(text),
        "textNonWhitespaceChars": len(non_whitespace),
        "hasText": has_text,
        "replacementCharacterCount": replacement_count,
        "replacementCharacterRatio": replacement_ratio,
    }


def _check_diagnostics(
    document: DocumentIR,
    issues: list[dict[str, str]],
) -> tuple[int, dict[str, Any]]:
    diagnostics = document.diagnostics
    expected_warning_count = len(
        set(document.warnings).union(document.structure_warnings)
    )
    counts_match = (
        diagnostics.page_count == len(document.pages)
        and diagnostics.segment_count == len(document.segments)
        and diagnostics.unit_count == len(document.units)
        and diagnostics.table_count == len(document.tables)
        and diagnostics.section_count == len(document.sections)
        and diagnostics.warning_count == expected_warning_count
    )
    method_match = diagnostics.parse_method == document.parse_method
    status_match = (
        (diagnostics.status == "failed") == document.parse_method.endswith("_failed")
    )
    round_trip = False
    try:
        restored = DocumentIR.model_validate(document_ir_to_json(document))
        round_trip = document_ir_to_json(restored) == document_ir_to_json(document)
    except Exception as exc:
        _issue(issues, "critical", "json_round_trip", f"DocumentIR JSON round-trip failed: {type(exc).__name__}")

    if not counts_match:
        _issue(issues, "warning", "diagnostic_counts", "Parse diagnostics counts do not match DocumentIR collections")
    if not method_match or not status_match:
        _issue(issues, "warning", "diagnostic_identity", "Parse diagnostics do not match parser identity/status")
    if not round_trip:
        _issue(issues, "critical", "json_round_trip", "DocumentIR cannot round-trip through public JSON")

    score = _score_from_checks(
        DIMENSION_MAX["diagnostics"],
        (
            (counts_match, 2),
            (method_match and status_match, 1),
            (round_trip, 2),
        ),
    )
    return score, {
        "diagnosticCountsMatch": counts_match,
        "diagnosticIdentityMatch": method_match and status_match,
        "jsonRoundTrip": round_trip,
        "status": diagnostics.status,
        "parseMethod": diagnostics.parse_method,
        "warningCount": diagnostics.warning_count,
    }


def evaluate_document_ir(
    document: DocumentIR,
    *,
    source_content: bytes | None = None,
    expected_file_name: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic, source-independent quality report for one IR."""

    issues: list[dict[str, str]] = []
    parse_ok = document.diagnostics.status == "parsed" and bool(
        document.text.strip() and document.segments
    )
    if document.diagnostics.status == "failed" or document.parse_method.endswith("_failed"):
        _issue(issues, "critical", "parse_failed", "Parser returned a failed DocumentIR")
    elif not parse_ok:
        _issue(issues, "critical", "parse_incomplete", "DocumentIR is empty or partial")

    parse_score = DIMENSION_MAX["parse"] if parse_ok else 0
    text_score, text_metrics = _check_text(document, source_content, issues)
    segment_score, segment_metrics = _check_segment_integrity(document, issues)
    provenance_score, provenance_metrics = _check_provenance(
        document,
        source_content,
        expected_file_name,
        issues,
    )
    page_score, page_metrics = _check_pages(document, issues)
    table_score, table_metrics = _check_tables(document, issues)
    section_score, section_metrics = _check_sections(document, issues)
    diagnostics_score, diagnostics_metrics = _check_diagnostics(document, issues)

    dimensions = {
        "parse": {"score": parse_score, "max": DIMENSION_MAX["parse"]},
        "text": {"score": text_score, "max": DIMENSION_MAX["text"], **text_metrics},
        "segments": {"score": segment_score, "max": DIMENSION_MAX["segments"], **segment_metrics},
        "provenance": {"score": provenance_score, "max": DIMENSION_MAX["provenance"], **provenance_metrics},
        "pages": {"score": page_score, "max": DIMENSION_MAX["pages"], **page_metrics},
        "tables": {"score": table_score, "max": DIMENSION_MAX["tables"], **table_metrics},
        "sections": {"score": section_score, "max": DIMENSION_MAX["sections"], **section_metrics},
        "diagnostics": {"score": diagnostics_score, "max": DIMENSION_MAX["diagnostics"], **diagnostics_metrics},
    }
    score = sum(int(item["score"]) for item in dimensions.values())
    hard_failure = any(item["severity"] == "critical" for item in issues)
    warning_count = sum(item["severity"] == "warning" for item in issues)
    needs_structural_review = bool(
        dimensions["sections"].get("implicitOnly")
        or (
            dimensions["pages"].get("applicable")
            and dimensions["pages"].get("bboxCoverage", 1.0) < 1.0
        )
    )
    capabilities = {
        "textReady": not hard_failure and text_metrics["hasText"],
        "documentIRReady": not hard_failure and score >= 85,
        "sectionReady": (
            not hard_failure
            and not dimensions["sections"].get("implicitOnly", False)
        ),
        "evidenceReady": (
            not hard_failure
            and (
                not dimensions["pages"].get("applicable", False)
                or dimensions["pages"].get("bboxCoverage", 1.0) == 1.0
            )
        ),
    }
    return {
        "score": score,
        "maxScore": sum(DIMENSION_MAX.values()),
        "grade": _grade(score),
        "readiness": _readiness(
            score,
            hard_failure,
            needs_structural_review=needs_structural_review,
        ),
        "hardFailure": hard_failure,
        "needsStructuralReview": needs_structural_review,
        "warningCount": warning_count,
        "criticalIssueCount": sum(item["severity"] == "critical" for item in issues),
        "capabilities": capabilities,
        "dimensions": dimensions,
        "issues": issues,
    }


def _mime_type(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".md": "text/markdown",
        ".txt": "text/plain",
    }.get(path.suffix.lower(), "")


def evaluate_file(path: Path) -> dict[str, Any]:
    """Parse and evaluate one file without allowing one bad sample to stop a corpus run."""

    content = path.read_bytes()
    sample_id = f"DOC-{hashlib.sha256(content).hexdigest()[:12]}"
    record: dict[str, Any] = {
        "sampleId": sample_id,
        "path": path.as_posix(),
        "fileName": path.name,
        "format": path.suffix.lower().lstrip("."),
        "fileSize": len(content),
    }
    try:
        from .document_parser import parse_document_ir

        document = parse_document_ir(
            content,
            file_name=path.name,
            mime_type=_mime_type(path),
        )
        record["quality"] = evaluate_document_ir(
            document,
            source_content=content,
            expected_file_name=path.name,
        )
        record["document"] = {
            "documentId": document.document_id,
            "documentVersionId": document.document_version_id,
            "parseMethod": document.parse_method,
            "status": document.diagnostics.status,
            "textChars": len(document.text),
            "pageCount": len(document.pages),
            "segmentCount": len(document.segments),
            "unitCount": len(document.units),
            "tableCount": len(document.tables),
            "sectionCount": len(document.sections),
            "warningCount": len(document.warnings) + len(document.structure_warnings),
        }
    except Exception as exc:
        record["quality"] = {
            "score": 0,
            "maxScore": sum(DIMENSION_MAX.values()),
            "grade": "F",
            "readiness": "not_ready",
            "hardFailure": True,
            "warningCount": 0,
            "criticalIssueCount": 1,
            "dimensions": {},
            "issues": [{
                "severity": "critical",
                "code": "evaluation_exception",
                "message": f"{type(exc).__name__}: {exc}",
            }],
        }
        record["document"] = {"status": "evaluation_failed"}
    return record


def build_corpus_report(
    input_root: Path,
    *,
    excluded_names: set[str] | None = None,
) -> dict[str, Any]:
    excluded = {name.lower() for name in (excluded_names or {"readme.md"})}
    supported = {".pdf", ".docx", ".doc", ".xlsx", ".txt", ".md"}
    paths = sorted(
        (
            path for path in input_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in supported
            and path.name.lower() not in excluded
        ),
        key=lambda item: item.as_posix().lower(),
    )
    samples = [evaluate_file(path) for path in paths]
    quality_records = [sample["quality"] for sample in samples]
    readiness_counts = Counter(str(item["readiness"]) for item in quality_records)
    format_scores: dict[str, dict[str, Any]] = {}
    for sample in samples:
        fmt = str(sample["format"])
        bucket = format_scores.setdefault(fmt, {"sampleCount": 0, "scores": [], "readiness": Counter()})
        bucket["sampleCount"] += 1
        bucket["scores"].append(int(sample["quality"]["score"]))
        bucket["readiness"][sample["quality"]["readiness"]] += 1
    for bucket in format_scores.values():
        scores = bucket.pop("scores")
        bucket["averageScore"] = round(sum(scores) / len(scores), 2) if scores else 0
        bucket["readiness"] = dict(bucket["readiness"])

    all_scores = [int(item["score"]) for item in quality_records]
    ready_count = sum(
        item["readiness"] == "ready_for_next_stage"
        for item in quality_records
    )
    return {
        "schemaVersion": "document-ir-quality-v1",
        "generatedAt": date.today().isoformat(),
        "inputRoot": input_root.as_posix(),
        "criteria": {
            "readyForNextStage": "score >= 85 with no critical issues and no structural review flag",
            "usableWithReview": "score 70-84, or implicit-only sections/missing PDF bbox, with no critical issues",
            "notReady": "critical issue or score < 70",
            "dimensions": DIMENSION_MAX,
            "note": "This is structural quality, not semantic field precision/recall.",
            "structuralReview": "implicit-only sections or missing PDF bbox forces usable_with_review",
        },
        "summary": {
            "sampleCount": len(samples),
            "averageScore": round(sum(all_scores) / len(all_scores), 2) if all_scores else 0,
            "minimumScore": min(all_scores) if all_scores else 0,
            "maximumScore": max(all_scores) if all_scores else 0,
            "readyCount": ready_count,
            "readiness": dict(readiness_counts),
            "criticalIssueCount": sum(
                int(item["criticalIssueCount"]) for item in quality_records
            ),
            "warningCount": sum(int(item["warningCount"]) for item in quality_records),
        },
        "formats": format_scores,
        "samples": samples,
    }


def write_json_report(report: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
