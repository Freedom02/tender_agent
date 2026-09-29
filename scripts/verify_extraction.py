r"""对单个招标文件跑纯规则的目标提取（不调 LLM），输出各 target 的 item 数。

用于改动 builtin_target_* 路由/提取后，对比同一文档的 itemCounts 是否按预期变化。

用法:
    python scripts/verify_extraction.py <docx|pdf 路径> [--out out.json]

输出 JSON 结构:
    { "file": ..., "moduleCounts": {technical, qualification, ...},
      "technicalSample": [前若干条技术 item 摘要] }
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.builtin_target_processing import extract_builtin_target_document  # noqa: E402
from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402


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


def _mime(path: Path) -> str:
    return {
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }.get(path.suffix.lower(), "")


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def evaluate(path: Path) -> dict[str, Any]:
    content = path.read_bytes()
    document = parse_document_ir(content, file_name=path.name, mime_type=_mime(path))
    segments = [item.model_dump(mode="json", by_alias=True) for item in document.segments]
    sections = [item.model_dump(mode="json", by_alias=True) for item in document.sections]
    tables = [item.model_dump(mode="json", by_alias=True) for item in document.tables]
    extraction = extract_builtin_target_document(
        text=document.text,
        segments=segments,
        sections=sections,
        tables=tables,
        ai={"enabled": False},
        source_file_id=path.name,
        project_id="verify-extraction",
        document_meta={
            "fileName": path.name,
            "contentHash": hashlib.sha256(content).hexdigest(),
        },
    )
    targets = {
        str(item.get("targetModule")): item
        for item in extraction.get("targetModules") or []
        if isinstance(item, dict)
    }
    module_counts: dict[str, int] = {}
    technical_sample: list[dict[str, Any]] = []
    for key in TARGET_KEYS:
        items = [i for i in (targets.get(key) or {}).get("items") or [] if isinstance(i, dict)]
        module_counts[key] = len(items)
        if key == "technical":
            technical_sample = [
                {
                    "productName": i.get("productName"),
                    "quantity": i.get("quantity"),
                    "unit": i.get("unit"),
                    "parameters": _text(i.get("parameters"), 120),
                    "text": _text(i.get("text"), 120),
                }
                for i in items[:15]
            ]
    return {
        "file": path.name,
        "parseMethod": document.parse_method,
        "parseStatus": document.diagnostics.status,
        "segmentCount": len(segments),
        "sectionCount": len(sections),
        "tableCount": len(tables),
        "moduleCounts": module_counts,
        "technicalSample": technical_sample,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()
    result = evaluate(args.file)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"written: {args.out}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
