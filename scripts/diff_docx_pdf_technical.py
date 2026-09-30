"""Offline rule-based DOCX vs PDF technical-clause diff for 350181 tender.

Runs the current P02->P07 offline pipeline (no LLM) on both the .docx and .pdf
versions of the same tender document, collects technical clauses, and writes a
side-by-side diff (common / DOCX-only / PDF-only) to JSON + Markdown.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.document_chunking import chunk_document  # noqa: E402
from backend.app.tender_matching.document_models import DocumentIR  # noqa: E402
from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402
from backend.app.tender_matching.requirement_extractor import extract_requirements  # noqa: E402
from backend.app.tender_matching.section_classifier import classify_document  # noqa: E402

DOCX = REPO_ROOT / "demo-data" / "【招标文件】数字孪生应用实训平台采购招标文件（[350181]FJUS[GK]202600120260113001）.docx"
PDF = REPO_ROOT / "demo-data" / "数字孪生应用实训平台采购招标文件（[350181]FJUS[GK]202600120260113001）.pdf"


def _mime(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(path.suffix.lower(), "")


def _norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _tech_clauses(path: Path) -> tuple[DocumentIR, list[dict[str, Any]]]:
    document = parse_document_ir(
        path.read_bytes(),
        file_name=path.name,
        mime_type=_mime(path),
    )
    classifications = classify_document(document)
    chunks = chunk_document(document, max_tokens=800)
    extraction = extract_requirements(
        document,
        chunks=chunks,
        classifications=classifications,
    )
    clauses: list[dict[str, Any]] = []
    for req in extraction.requirements:
        if req.category != "technical":
            continue
        clauses.append(
            {
                "text": req.raw_text or "",
                "norm": _norm(req.raw_text),
                "productName": req.product_name or "",
                "sectionPath": " / ".join(str(item) for item in (req.section_path or [])),
                "sourceColumn": req.source_column or "",
            }
        )
    return document, clauses


def _dedup(clauses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in clauses:
        if not item["norm"] or item["norm"] in seen:
            continue
        seen.add(item["norm"])
        out.append(item)
    return out


def main() -> int:
    print("=== parsing DOCX ===")
    doc_docx, docx_clauses = _tech_clauses(DOCX)
    print("=== parsing PDF ===")
    doc_pdf, pdf_clauses = _tech_clauses(PDF)

    docx_clauses = _dedup(docx_clauses)
    pdf_clauses = _dedup(pdf_clauses)

    docx_norms = {item["norm"]: item for item in docx_clauses}
    pdf_norms = {item["norm"]: item for item in pdf_clauses}

    common_norms = sorted(set(docx_norms) & set(pdf_norms))
    docx_only = sorted(set(docx_norms) - set(pdf_norms))
    pdf_only = sorted(set(pdf_norms) - set(docx_norms))

    result = {
        "docx": {
            "file": DOCX.name,
            "parseMethod": doc_docx.parse_method,
            "sectionCount": len(doc_docx.sections),
            "tableCount": len(doc_docx.tables),
            "technicalClauseCount": len(docx_clauses),
        },
        "pdf": {
            "file": PDF.name,
            "parseMethod": doc_pdf.parse_method,
            "sectionCount": len(doc_pdf.sections),
            "tableCount": len(doc_pdf.tables),
            "technicalClauseCount": len(pdf_clauses),
        },
        "diff": {
            "commonCount": len(common_norms),
            "docxOnlyCount": len(docx_only),
            "pdfOnlyCount": len(pdf_only),
            "common": [docx_norms[n]["text"] for n in common_norms],
            "docxOnly": [docx_norms[n] for n in docx_only],
            "pdfOnly": [pdf_norms[n] for n in pdf_only],
        },
    }

    out_dir = REPO_ROOT / "evaluation"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "diff_docx_pdf_technical.json"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    md_path = out_dir / "diff_docx_pdf_technical.md"
    lines = [
        "# DOCX vs PDF 技术条款逐条 diff（离线规则，无 LLM）",
        "",
        f"- DOCX：`{DOCX.name}` — 解析 `{doc_docx.parse_method}`，"
        f"章节 {len(doc_docx.sections)}，表格 {len(doc_docx.tables)}，技术条款 {len(docx_clauses)}",
        f"- PDF：`{PDF.name}` — 解析 `{doc_pdf.parse_method}`，"
        f"章节 {len(doc_pdf.sections)}，表格 {len(doc_pdf.tables)}，技术条款 {len(pdf_clauses)}",
        "",
        "| 统计 | 数量 |",
        "|---|---:|---|",
        f"| 两版共有技术条款 | {len(common_norms)} |",
        f"| 仅 DOCX 提取到 | {len(docx_only)} |",
        f"| 仅 PDF 提取到 | {len(pdf_only)} |",
        "",
        "> 说明：同一份招标文件的 DOCX/PDF 两版，用当前离线规则重跑后做条款级对齐。",
        "> 差异主要反映 PDF 解析（跨页表格拆分、去重）与 DOCX 解析的差别。",
        "",
        "## 仅 DOCX 提取到（PDF 缺失）",
        "",
    ]
    if docx_only:
        for item in docx_clauses:
            if item["norm"] not in docx_only:
                continue
            lines.append(f"- `{item['productName']}` | {item['text'][:80]}")
    else:
        lines.append("- （无）")
    lines += ["", "## 仅 PDF 提取到（DOCX 缺失）", ""]
    if pdf_only:
        for item in pdf_clauses:
            if item["norm"] not in pdf_only:
                continue
            lines.append(f"- `{item['productName']}` | {item['text'][:80]}")
    else:
        lines.append("- （无）")
    lines += ["", "## 两版共有（共 " + str(len(common_norms)) + " 条）", ""]
    for norm in common_norms:
        lines.append(f"- {docx_norms[norm]['text'][:80]}")
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"DOCX technical clauses: {len(docx_clauses)}")
    print(f"PDF  technical clauses: {len(pdf_clauses)}")
    print(f"common: {len(common_norms)}, docx-only: {len(docx_only)}, pdf-only: {len(pdf_only)}")
    print(f"wrote: {json_path}")
    print(f"wrote: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
