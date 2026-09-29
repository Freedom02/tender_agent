"""Evaluate every real sample against the DocumentIR structural contract.

Run from the current repository root:

    python scripts/evaluate_document_ir.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.document_quality import (  # noqa: E402
    build_corpus_report,
    write_json_report,
)


def _write_markdown(report: dict, output: Path) -> None:
    summary = report["summary"]
    lines = [
        "# DocumentIR 质量评估",
        "",
        f"> 生成日期：{report['generatedAt']}",
        f"> 样本数：{summary['sampleCount']}",
        f"> 平均分：{summary['averageScore']}/100",
        f"> 下一阶段可直接使用：{summary['readyCount']}/{summary['sampleCount']}",
        "",
        "## 评分规则",
        "",
        "- 90-100：A，结构质量稳定。",
        "- 85-89：可进入下一阶段。",
        "- 70-84：可使用，但必须带人工复核或降级策略。",
        "- 即使分数达到85，只有隐式章节或缺少 PDF bbox 时仍标记为 usable_with_review。",
        "- 0-69，或存在 critical：暂不适合作为后续事实层输入。",
        "- 评分只衡量结构、定位、诊断和可序列化性，不等同于业务字段准确率。",
        "",
        "## 格式汇总",
        "",
        "| 格式 | 样本数 | 平均分 | 状态 |",
        "|---|---:|---:|---|",
    ]
    for fmt, metrics in sorted(report["formats"].items()):
        readiness = ", ".join(
            f"{key}={value}"
            for key, value in sorted(metrics["readiness"].items())
        )
        lines.append(
            f"| {fmt} | {metrics['sampleCount']} | "
            f"{metrics['averageScore']} | {readiness} |"
        )
    lines.extend([
        "",
        "## 样本明细",
        "",
        "| 文件 | 分数 | 等级 | 结论 | 解析方法 | 关键问题 |",
        "|---|---:|---|---|---|---|",
    ])
    for sample in report["samples"]:
        quality = sample["quality"]
        document = sample.get("document", {})
        problems = [
            f"{item['severity']}:{item['code']}"
            for item in quality.get("issues", [])
            if item["severity"] in {"critical", "warning"}
        ]
        lines.append(
            f"| {sample['fileName'].replace('|', '\\|')} | "
            f"{quality['score']} | {quality['grade']} | {quality['readiness']} | "
            f"{document.get('parseMethod', '')} | "
            f"{', '.join(problems) or '无'} |"
        )
    lines.extend([
        "",
        "## 结论解释",
        "",
        "DOCX 样本主要验证段落、表格、单元格、章节和 JSON round-trip。",
        "PDF 样本还需要验证物理页、页级定位和 bbox；若环境没有 PyMuPDF，评估会明确扣除坐标能力分。",
        "旧版 DOC 若没有 legacy-doc 依赖，必须保留失败诊断，不能降级为乱码文本。",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
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
        "--json-output",
        type=Path,
        default=REPO_ROOT / "evaluation" / "document_ir_quality_report.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPO_ROOT / "docs" / "P02-document-ir-evaluation.md",
    )
    args = parser.parse_args()
    input_root = args.input.resolve()
    if not input_root.exists():
        raise SystemExit(f"sample root does not exist: {input_root}")
    report = build_corpus_report(input_root)
    write_json_report(report, args.json_output.resolve())
    _write_markdown(report, args.markdown_output.resolve())
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
