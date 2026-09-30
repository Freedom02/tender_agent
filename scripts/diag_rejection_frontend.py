"""Diagnose why the frontend rejection (废标项) module is empty.

Runs the actual builtin target-oriented extraction (the path the frontend
consumes) on the 350181 DOCX and reports the `rejection` target module.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.builtin_target_processing import (  # noqa: E402
    extract_builtin_target_document,
)
from backend.app.tender_matching.builtin_target_map import (  # noqa: E402
    discover_target_ranges,
    rejection_terms,
)
from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402

DOCX = REPO_ROOT / "demo-data" / "【招标文件】数字孪生应用实训平台采购招标文件（[350181]FJUS[GK]202600120260113001）.docx"


def main() -> int:
    ir = parse_document_ir(
        DOCX.read_bytes(),
        file_name=DOCX.name,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    d = ir.model_dump(mode="json", by_alias=True) if hasattr(ir, "model_dump") else {}
    segments = d.get("segments") or []
    sections = d.get("sections") or []
    tables = d.get("tables") or []
    text = d.get("text") or ""

    print("=== rejection_terms ===")
    print(rejection_terms())

    print("\n=== discover_target_ranges (rule only, no AI) ===")
    target_map = discover_target_ranges(
        segments=segments,
        sections=sections,
        tables=tables,
        ai=None,
    )
    for target in target_map.get("targets") or []:
        key = target.get("targetModule")
        seg_ids = target.get("segmentIds") or []
        print(f"  {key}: segmentIds={len(seg_ids)}  expanded={len(target.get('expandedSegmentIds') or [])}")

    print("\n=== extract_builtin_target_document ===")
    result = extract_builtin_target_document(
        text=text,
        segments=segments,
        sections=sections,
        tables=tables,
        ai=None,
        source_file_id="diag-350181",
        project_id="diag-350181",
    )
    for t in result.get("targetModules") or []:
        key = t.get("targetModule")
        items = t.get("items") or []
        print(f"  {key}: items={len(items)}  segmentIds={len(t.get('segmentIds') or [])}")
        if key == "rejection" and items:
            for it in items[:5]:
                print(f"      - {(it.get('rawText') or it.get('text') or '')[:60]}")

    print("\n=== analysisPack targetModuleMap rejection.sourceView ===")
    ap = result.get("analysisPack") or {}
    tmm = ap.get("targetModuleMap") or {}
    rej = tmm.get("rejection") or {}
    sv = rej.get("sourceView") or {}
    print(f"  displayMode: {rej.get('displayMode')}")
    print(f"  sourceView present: {bool(sv)}")
    print(f"  sourceView.sectionCount: {sv.get('sectionCount')}")
    print(f"  sourceView.rootSectionCount: {sv.get('rootSectionCount')}")
    print(f"  sourceView.blockCount: {sv.get('blockCount')}")
    sections = sv.get("sections") or []
    print(f"  sourceView.sections: {len(sections)}")
    for s in sections[:5]:
        print(f"      - {s.get('title')} (blocks={len(s.get('blocks') or [])})")

    print("\n=== warnings ===")
    for w in result.get("warnings") or []:
        print("  -", w)

    print("\n=== stats ===")
    for k, v in (result.get("stats") or {}).items():
        if "Count" in k or "count" in k:
            print(f"  {k}: {v}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
