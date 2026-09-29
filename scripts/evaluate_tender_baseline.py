r"""Build the P01 tender-document sample manifest and parser baseline.

Run from the repository root with the project virtual environment:

    .\backend\.venv\Scripts\python.exe scripts\evaluate_tender_baseline.py

The script only measures the current parser. It does not call an LLM, query
M3, or mutate runtime project data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.document_parser import (  # noqa: E402
    parse_doc,
    parse_docx,
    parse_pdf,
    parse_plain_text,
    parse_xlsx,
)
from backend.app.tender_matching.document_structure import (  # noqa: E402
    enrich_document_structure,
)


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".doc", ".xlsx", ".txt", ".md"}
TECHNICAL_TERMS = (
    "技术要求",
    "技术参数",
    "产品参数",
    "功能要求",
    "服务内容",
    "服务要求",
    "实施方案",
    "运维",
    "数字孪生",
    "平台建设",
)
SERVICE_TERMS = (
    "服务",
    "运维",
    "实施",
    "交付",
    "培训",
    "售后",
    "质保",
    "响应时间",
)
SERIOUS_WARNING_TERMS = (
    "解析失败",
    "无法读取",
    "正文 XML 解析失败",
    "未提取到文字",
    "扫描件需后续接入 OCR",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _parse(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        parsed = parse_pdf(content)
    elif suffix == ".docx":
        parsed = parse_docx(content)
    elif suffix == ".doc":
        parsed = parse_doc(content)
    elif suffix == ".xlsx":
        parsed = parse_xlsx(content)
    else:
        parsed = parse_plain_text(content)
    return enrich_document_structure(parsed)


def _relative(path: Path) -> str:
    return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()


def _sample_role(path: Path) -> str:
    return "response_document" if "响应文件" in path.name else "tender_document"


def _section_type_counts(sections: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str(section.get("sectionType") or "other")
        for section in sections
        if isinstance(section, dict)
    )
    return dict(sorted(counts.items()))


def _text_quality(parsed: dict[str, Any]) -> dict[str, Any]:
    text = str(parsed.get("text") or "")
    segments = [
        item for item in parsed.get("segments", [])
        if isinstance(item, dict)
    ]
    warnings = [
        str(item)
        for item in parsed.get("warnings", [])
        if str(item).strip()
    ]
    structure_warnings = [
        str(item)
        for item in parsed.get("structureWarnings", [])
        if str(item).strip()
    ]
    all_warnings = list(dict.fromkeys([*warnings, *structure_warnings]))
    normalized_text = re.sub(r"\s+", "", text)
    technical_hits = [
        term for term in TECHNICAL_TERMS
        if term in normalized_text
    ]
    service_hits = [
        term for term in SERVICE_TERMS
        if term in normalized_text
    ]
    serious_warnings = [
        warning
        for warning in all_warnings
        if any(term in warning for term in SERIOUS_WARNING_TERMS)
    ]
    non_empty_segments = [
        segment for segment in segments if str(segment.get("text") or "").strip()
    ]
    located_segments = [
        segment
        for segment in non_empty_segments
        if str(segment.get("sourceLocator") or "").strip()
    ]
    page_located_segments = [
        segment
        for segment in non_empty_segments
        if segment.get("page") is not None
    ]
    status = "parsed"
    if not text.strip() or not non_empty_segments:
        status = "failed"
    elif serious_warnings:
        status = "needs_review"

    metadata = parsed.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    sections = parsed.get("sections")
    sections = sections if isinstance(sections, list) else []
    return {
        "status": status,
        "textChars": len(text),
        "textNonWhitespaceChars": len(normalized_text),
        "segmentCount": len(segments),
        "nonEmptySegmentCount": len(non_empty_segments),
        "unitCount": len(parsed.get("units") or []),
        "tableCount": len(parsed.get("tables") or []),
        "sectionCount": len(sections),
        "headingCount": int(metadata.get("headingCount") or 0),
        "pageCount": int(metadata.get("pageCount") or 0),
        "sourceLocatorCoverage": round(
            len(located_segments) / len(non_empty_segments), 4
        ) if non_empty_segments else 0.0,
        "pageLocatorCoverage": round(
            len(page_located_segments) / len(non_empty_segments), 4
        ) if non_empty_segments else 0.0,
        "technicalSignal": bool(technical_hits),
        "technicalTermHits": technical_hits,
        "serviceSignal": bool(service_hits),
        "serviceTermHits": service_hits,
        "sectionTypeCounts": _section_type_counts(sections),
        "warnings": all_warnings,
        "seriousWarnings": serious_warnings,
        "parseMethod": str(parsed.get("parseMethod") or ""),
        "ocrPages": int(metadata.get("ocr_pages") or metadata.get("ocrPages") or 0),
    }


def inspect_sample(path: Path) -> dict[str, Any]:
    sample_id = f"DOC-{_sha256(path)[:12]}"
    record: dict[str, Any] = {
        "sampleId": sample_id,
        "path": _relative(path),
        "fileName": path.name,
        "extension": path.suffix.lower(),
        "fileSize": path.stat().st_size,
        "sha256": _sha256(path),
        "sampleRole": _sample_role(path),
    }
    try:
        parsed = _parse(path)
    except Exception as exc:  # Keep one broken sample from hiding the rest.
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["metrics"] = {
            "status": "failed",
            "textChars": 0,
            "segmentCount": 0,
            "sectionCount": 0,
            "warnings": [record["error"]],
        }
        return record

    record["metrics"] = _text_quality(parsed)
    record["status"] = record["metrics"]["status"]
    return record


def build_manifest(input_root: Path) -> list[dict[str, Any]]:
    paths = sorted(
        (
            path for path in input_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and path.name.lower() != "readme.md"
        ),
        key=lambda path: path.as_posix().lower(),
    )
    return [inspect_sample(path) for path in paths]


def build_gold(samples: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schemaVersion": "tender-document-gold-v1",
        "createdAt": date.today().isoformat(),
        "description": (
            "人工复核用黄金标注骨架。自动基线只填 sampleId/path/format，"
            "不得把模型或规则输出直接当成 gold。"
        ),
        "annotationInstructions": {
            "sections": "填写一级和二级章节的 heading、level、startLocator、endLocator、sectionType。",
            "requirements": (
                "只标注可独立检查的技术/产品/服务、资质、商务、否决和时间要求；"
                "每条保留 sourceSegmentIds 与 sourceQuote。"
            ),
            "evidence": "关键要求必须填写 page 或 sourceLocator；表格要求填写 tableId/rowIndex/cellIndex。",
            "status": "pending 表示尚未人工核对；reviewed 后才能用于发布门槛评测。",
        },
        "samples": [
            {
                "sampleId": sample["sampleId"],
                "path": sample["path"],
                "fileName": sample["fileName"],
                "format": sample["extension"].lstrip("."),
                "sampleRole": sample["sampleRole"],
                "annotationStatus": "pending",
                "document": {
                    "pageCount": None,
                    "isScanned": None,
                    "notes": "",
                },
                "sections": [],
                "requirements": [],
                "risks": [],
                "milestones": [],
                "reviewer": "",
                "reviewedAt": "",
            }
            for sample in samples
        ],
    }


def build_report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    statuses = Counter(str(sample.get("status") or "failed") for sample in samples)
    formats = Counter(str(sample.get("extension") or "") for sample in samples)
    tender_samples = [
        sample for sample in samples
        if sample.get("sampleRole") == "tender_document"
    ]
    technical_samples = [
        sample for sample in tender_samples
        if sample.get("metrics", {}).get("technicalSignal")
    ]
    service_samples = [
        sample for sample in tender_samples
        if sample.get("metrics", {}).get("serviceSignal")
    ]
    metrics = [
        sample.get("metrics", {})
        for sample in samples
        if isinstance(sample.get("metrics"), dict)
    ]
    return {
        "schemaVersion": "tender-document-baseline-v1",
        "generatedAt": date.today().isoformat(),
        "environment": {
            "requiredEnvironment": "project-venv",
            "pythonExecutable": sys.executable,
            "pythonVersion": sys.version.split()[0],
        },
        "sampleCount": len(samples),
        "formatCounts": dict(sorted(formats.items())),
        "statusCounts": dict(sorted(statuses.items())),
        "tenderSampleCount": len(tender_samples),
        "technicalSignalSampleCount": len(technical_samples),
        "serviceSignalSampleCount": len(service_samples),
        "parseableSampleCount": sum(
            1 for sample in samples
            if sample.get("status") in {"parsed", "needs_review"}
        ),
        "aggregate": {
            "totalTextChars": sum(int(item.get("textChars") or 0) for item in metrics),
            "totalSegments": sum(int(item.get("segmentCount") or 0) for item in metrics),
            "totalSections": sum(int(item.get("sectionCount") or 0) for item in metrics),
            "totalTables": sum(int(item.get("tableCount") or 0) for item in metrics),
            "totalWarnings": sum(len(item.get("warnings") or []) for item in metrics),
        },
        "goldStatus": "pending_manual_annotation",
        "samples": samples,
    }


def write_markdown(report: dict[str, Any], output: Path) -> None:
    lines = [
        "# P01 招标文件解析基线报告",
        "",
        f"> 生成日期：{report['generatedAt']}",
        f"> Python：`{report['environment']['pythonExecutable']}`",
        f"> 版本：`{report['environment']['pythonVersion']}`",
        "",
        "## 汇总",
        "",
        f"- 样本数：{report['sampleCount']}",
        f"- 招标文件样本：{report['tenderSampleCount']}",
        f"- 技术信号样本：{report['technicalSignalSampleCount']}",
        f"- 服务/运维信号样本：{report['serviceSignalSampleCount']}",
        f"- 可解析样本：{report['parseableSampleCount']}",
        f"- 状态：`{report['goldStatus']}`",
        "",
        "## 样本",
        "",
        "| Sample ID | 文件 | 格式 | 状态 | 文本字符 | Segment | 章节 | 表格 | 技术信号 | 来源定位覆盖率 |",
        "|---|---|---:|---|---:|---:|---:|---:|---|---:|",
    ]
    for sample in report["samples"]:
        metrics = sample.get("metrics", {})
        lines.append(
            "| {sample_id} | {file_name} | {extension} | {status} | {text_chars} | "
            "{segments} | {sections} | {tables} | {technical} | {coverage:.2%} |".format(
                sample_id=sample.get("sampleId", ""),
                file_name=sample.get("fileName", "").replace("|", "\\|"),
                extension=sample.get("extension", ""),
                status=sample.get("status", ""),
                text_chars=int(metrics.get("textChars") or 0),
                segments=int(metrics.get("segmentCount") or 0),
                sections=int(metrics.get("sectionCount") or 0),
                tables=int(metrics.get("tableCount") or 0),
                technical="是" if metrics.get("technicalSignal") else "否",
                coverage=float(metrics.get("sourceLocatorCoverage") or 0),
            )
        )
    lines.extend(
        [
            "",
            "## 当前限制",
            "",
            "- `tender_document_gold.json` 仍需人工标注，当前不能据此宣称章节或要求准确率。",
            "- 当前主线 PDF 基线使用 `pypdf`，尚未纳入表格和 bbox；扫描 PDF 仍会进入待复核。",
            "- DOCX 有结构化段落和表格行，但暂未映射物理页码。",
            "- 本报告不调用 LLM、不查询 M3，也不代表企业匹配结果。",
        ]
    )
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=REPO_ROOT / "demo-data",
        help="sample root; defaults to demo-data",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "evaluation",
        help="output directory",
    )
    args = parser.parse_args()
    input_root = args.input.resolve()
    output_dir = args.output_dir.resolve()
    if not input_root.exists():
        raise SystemExit(f"sample root does not exist: {input_root}")

    samples = build_manifest(input_root)
    report = build_report(samples)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "tender_sample_manifest.json").write_text(
        json.dumps(
            {
                "schemaVersion": "tender-document-sample-manifest-v1",
                "generatedAt": date.today().isoformat(),
                "inputRoot": _relative(input_root),
                "samples": samples,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (output_dir / "tender_document_gold.json").write_text(
        json.dumps(build_gold(samples), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "tender_baseline_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    write_markdown(report, REPO_ROOT / "docs" / "P01-baseline.md")
    print(json.dumps({
        "sampleCount": report["sampleCount"],
        "tenderSampleCount": report["tenderSampleCount"],
        "technicalSignalSampleCount": report["technicalSignalSampleCount"],
        "serviceSignalSampleCount": report["serviceSignalSampleCount"],
        "statusCounts": report["statusCounts"],
        "outputDir": str(output_dir),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
