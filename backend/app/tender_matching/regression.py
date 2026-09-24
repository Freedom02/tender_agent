"""Comparable, offline regression snapshots for tender analysis."""
from __future__ import annotations

from collections import Counter
from typing import Any


REGRESSION_SCHEMA_VERSION = "tender-regression/v1"


def _records(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    source = report or {}
    values = source.get("records")
    if not isinstance(values, list):
        values = source.get("samples")
    return [item for item in values if isinstance(item, dict)] if isinstance(values, list) else []


def _sample_key(record: dict[str, Any]) -> str:
    return str(record.get("path") or record.get("fileName") or record.get("sampleId") or "")


def _sum(records: list[dict[str, Any]], key: str) -> int:
    return sum(int(record.get(key) or 0) for record in records)


def _parser_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    records = _records(report)
    parseable = sum(
        1
        for record in records
        if str(record.get("status") or "") in {"parsed", "needs_review"}
        or str((record.get("metrics") or {}).get("status") or "")
        in {"parsed", "needs_review"}
    )
    metrics = [
        record.get("metrics") or {}
        for record in records
        if isinstance(record.get("metrics"), dict)
    ]
    return {
        "sampleCount": len(records),
        "parseableSampleCount": parseable,
        "parseableRate": round(parseable / len(records), 4) if records else 0.0,
        "totalTextChars": sum(int(item.get("textChars") or 0) for item in metrics),
        "totalSegments": sum(int(item.get("segmentCount") or 0) for item in metrics),
        "totalSections": sum(int(item.get("sectionCount") or 0) for item in metrics),
        "totalTables": sum(int(item.get("tableCount") or 0) for item in metrics),
        "totalWarnings": sum(len(item.get("warnings") or []) for item in metrics),
    }


def _decision_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    records = _records(report)
    recommendations = Counter(
        str(record.get("decisionRecommendation") or "未返回")
        for record in records
        if not record.get("error")
    )
    return {
        "sampleCount": len(records),
        "successCount": sum(1 for record in records if not record.get("error")),
        "failureCount": sum(1 for record in records if record.get("error")),
        "successRate": round(
            sum(1 for record in records if not record.get("error")) / len(records),
            4,
        ) if records else 0.0,
        "totalRequirementCount": _sum(records, "requirementCount"),
        "totalTechnicalItemCount": _sum(records, "technicalItemCount"),
        "totalHardBlockerCount": _sum(records, "hardBlockerCount"),
        "totalTechnicalGapCount": _sum(records, "technicalGapCount"),
        "totalMaterialGapCount": _sum(records, "materialGapCount"),
        "totalHumanReviewCount": _sum(records, "needsHumanReviewCount"),
        "recommendationCounts": dict(sorted(recommendations.items())),
    }


def build_regression_snapshot(
    parser_report: dict[str, Any],
    decision_report: dict[str, Any],
    *,
    gold_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parser_records = {
        _sample_key(record): {
            "status": record.get("status", ""),
            "sha256": record.get("sha256", ""),
            "textChars": int((record.get("metrics") or {}).get("textChars") or 0),
            "segmentCount": int((record.get("metrics") or {}).get("segmentCount") or 0),
            "sectionCount": int((record.get("metrics") or {}).get("sectionCount") or 0),
            "tableCount": int((record.get("metrics") or {}).get("tableCount") or 0),
            "warningCount": len((record.get("metrics") or {}).get("warnings") or []),
        }
        for record in _records(parser_report)
        if _sample_key(record)
    }
    decision_records = {
        _sample_key(record): {
            "error": record.get("error", ""),
            "requirementCount": int(record.get("requirementCount") or 0),
            "technicalItemCount": int(record.get("technicalItemCount") or 0),
            "hardBlockerCount": int(record.get("hardBlockerCount") or 0),
            "technicalGapCount": int(record.get("technicalGapCount") or 0),
            "materialGapCount": int(record.get("materialGapCount") or 0),
            "needsHumanReviewCount": int(record.get("needsHumanReviewCount") or 0),
            "decisionRecommendation": record.get("decisionRecommendation", ""),
        }
        for record in _records(decision_report)
        if _sample_key(record)
    }
    gold_samples = [
        item
        for item in ((gold_report or {}).get("samples") or [])
        if isinstance(item, dict)
    ]
    reviewed_gold = [
        item for item in gold_samples
        if str(item.get("annotationStatus") or "") == "reviewed"
    ]
    pending_gold = [
        item for item in gold_samples
        if str(item.get("annotationStatus") or "") == "pending"
    ]
    gold_status = str((gold_report or {}).get("goldStatus") or "").strip()
    if not gold_status:
        if reviewed_gold and len(reviewed_gold) == len(gold_samples):
            gold_status = "reviewed"
        elif pending_gold:
            gold_status = "pending_manual_annotation"
        else:
            gold_status = "unknown"
    return {
        "schemaVersion": REGRESSION_SCHEMA_VERSION,
        "parser": _parser_snapshot(parser_report),
        "decision": _decision_snapshot(decision_report),
        "gold": {
            "status": gold_status,
            "sampleCount": len(gold_samples),
            "reviewedSampleCount": len(reviewed_gold),
        },
        "samples": {
            key: {
                "parser": parser_records.get(key),
                "decision": decision_records.get(key),
            }
            for key in sorted(set(parser_records) | set(decision_records))
        },
    }


def _check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    passed: bool,
    actual: Any,
    expected: Any,
    detail: str,
    severity: str = "error",
) -> None:
    checks.append(
        {
            "name": name,
            "status": "passed" if passed else ("warning" if severity == "warning" else "failed"),
            "actual": actual,
            "expected": expected,
            "detail": detail,
        }
    )


def compare_regression_snapshots(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Compare structural/operational metrics without claiming gold accuracy."""

    checks: list[dict[str, Any]] = []
    baseline_samples = set((baseline.get("samples") or {}).keys())
    current_samples = set((current.get("samples") or {}).keys())
    _check(
        checks,
        name="sample_set",
        passed=baseline_samples == current_samples,
        actual=sorted(current_samples),
        expected=sorted(baseline_samples),
        detail="输入样本集合必须稳定；新增/删除文件需要显式更新基线。",
    )

    baseline_parser = baseline.get("parser") or {}
    current_parser = current.get("parser") or {}
    _check(
        checks,
        name="parser_parseable_rate",
        passed=float(current_parser.get("parseableRate") or 0) >= float(
            baseline_parser.get("parseableRate") or 0
        ),
        actual=current_parser.get("parseableRate", 0),
        expected=f">={baseline_parser.get('parseableRate', 0)}",
        detail="可解析样本比例不能低于基线。",
    )
    _check(
        checks,
        name="parser_warning_count",
        passed=int(current_parser.get("totalWarnings") or 0)
        <= int(baseline_parser.get("totalWarnings") or 0) + 5,
        actual=current_parser.get("totalWarnings", 0),
        expected=f"<= {int(baseline_parser.get('totalWarnings') or 0) + 5}",
        detail="允许少量新增提示，但解析警告明显增加时必须定位原因。",
        severity="warning",
    )

    baseline_decision = baseline.get("decision") or {}
    current_decision = current.get("decision") or {}
    _check(
        checks,
        name="decision_success_rate",
        passed=float(current_decision.get("successRate") or 0) >= float(
            baseline_decision.get("successRate") or 0
        ),
        actual=current_decision.get("successRate", 0),
        expected=f">={baseline_decision.get('successRate', 0)}",
        detail="真实文件端到端执行成功率不能低于基线。",
    )
    _check(
        checks,
        name="decision_failure_count",
        passed=int(current_decision.get("failureCount") or 0) == 0,
        actual=current_decision.get("failureCount", 0),
        expected=0,
        detail="每个失败样本都必须能定位到解析、抽取、校验或匹配阶段。",
    )

    baseline_gold = baseline.get("gold") or {}
    current_gold = current.get("gold") or {}
    checks.append(
        {
            "name": "gold_annotation_gate",
            "status": "warning",
            "actual": current_gold.get("reviewedSampleCount", 0),
            "expected": f"当前黄金标注状态：{current_gold.get('status', 'unknown')}",
            "detail": (
                "黄金样本仍未完成人工标注，本轮只执行结构和运行质量门禁；"
                "完成 reviewed 标注后再启用章节/要求/证据准确率门禁。"
            ),
        }
    )

    failures = [item for item in checks if item["status"] == "failed"]
    warnings = [item for item in checks if item["status"] == "warning"]
    return {
        "passed": not failures,
        "checks": checks,
        "failureCount": len(failures),
        "warningCount": len(warnings),
        "baselineGoldStatus": baseline_gold.get("status", "unknown"),
        "currentGoldStatus": current_gold.get("status", "unknown"),
    }
