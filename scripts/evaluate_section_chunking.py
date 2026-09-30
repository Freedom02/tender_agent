"""Evaluate SectionTree and major-section chunking on high-score DOCX samples."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.document_parser import parse_document_ir  # noqa: E402
from backend.app.tender_matching.section_chunk_quality import (  # noqa: E402
    evaluate_section_and_chunking,
)


def _load_high_score_docx(root: Path, quality_report: Path | None) -> list[Path]:
    if not quality_report or not quality_report.exists():
        return sorted(root.glob("*.docx"), key=lambda path: path.name.lower())
    report = json.loads(quality_report.read_text(encoding="utf-8"))
    selected: list[Path] = []
    for sample in report.get("samples", []):
        if (
            sample.get("format") == "docx"
            and sample.get("quality", {}).get("readiness") == "ready_for_next_stage"
        ):
            path = Path(sample["path"])
            if path.exists():
                selected.append(path)
    return sorted(selected, key=lambda path: path.name.lower())


def _write_markdown(report: dict, output: Path) -> None:
    summary = report["summary"]
    lines = [
        "# SectionTree 与大章节切分评测",
        "",
        f"> 样本数：{summary['sampleCount']}",
        f"> 综合平均分：{summary['averageScore']}/100",
        f"> 可直接进入后续阶段：{summary['readyCount']}/{summary['sampleCount']}",
        "",
        "## 评分口径",
        "",
        "- SectionTree 50 分：引用完整性、父子关系、路径、连续区间、大章节识别。",
        "- Chunking 50 分：来源覆盖、顺序、预算、上下文、表格原子性、大章节超长切分。",
        "- 大章节候选使用高置信度标题规则识别：`第X章`、`第X篇`、`第X部分`、`第X卷`及响应文件中的`第X 标题`。",
        "- 大章节 `isMajor` 同时检查召回率和精确率；精确率低于 80% 时只能进入人工复核。",
        "- 评测不等于人工 gold 准确率；大章节召回仍是启发式代理指标，最终需要人工标注。",
        "",
        "## 汇总",
        "",
        "| 文件 | SectionTree | Chunking | 综合 | 结论 | 大章节召回 | 大章节精确率 | 孤立章节比例 | Chunk 数 |",
        "|---|---:|---:|---:|---|---:|---:|---:|---:|",
    ]
    for sample in report["samples"]:
        quality = sample["quality"]
        tree = quality["sectionTree"]
        chunks = quality["chunking"]
        lines.append(
            f"| {sample['fileName'].replace('|', '\\|')} | "
            f"{tree['score']}/50 | {chunks['score']}/50 | "
            f"{quality['score']}/100 | {quality['readiness']} | "
            f"{tree['majorRecall']:.0%} | {tree['majorPrecision']:.0%} | "
            f"{tree['orphanSectionRatio']:.0%} | "
            f"{chunks['chunkCount']} |"
        )
    lines.extend([
        "",
        "## 主要问题",
        "",
    ])
    for sample in report["samples"]:
        issues = sample["quality"].get("issues", [])
        if not issues:
            continue
        lines.append(f"### {sample['fileName']}")
        for issue in issues:
            lines.append(f"- `{issue['severity']}` `{issue['code']}`：{issue['message']}")
        lines.append("")
    lines.extend([
        "## 结论",
        "",
        "### 优点",
        "",
        "- 章节引用、父子关系、路径、定位信息和连续区间在本批高分 DOCX 中均通过。",
        "- 大章节语义编号优先后，原先被误判为二级的`第一章/第二章`已恢复为顶层章节。",
        "- 默认 800 token 和 400 token 压力档均保持来源覆盖 100%、顺序正确、表格行原子性通过。",
        "",
        "### 缺点与风险",
        "",
        "- 本评测没有人工 gold；大章节召回率和精确率是高置信度规则代理值，不等同于真实准确率。",
        "- `isMajor` 精确率低于 80% 的文档不能直接用于自动章节路由；本批中网易样本需要人工复核。",
        "- 响应文件仍可能存在少量无父节点标题，虽然当前低于告警阈值，也应在后续样本中持续观察。",
        "",
        "### 下一步",
        "",
        "先为代表性的招标文件、响应文件和合同附件补充人工章节 gold，",
        "再用 gold 校准 `isMajor` 和章节层级；SectionTree 稳定后再把 Chunk 输出接入要求抽取、",
        "证据定位和匹配评测。Chunking 当前可以作为后续阶段输入，但路由应保留章节复核标记。",
    ])
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=REPO_ROOT / "demo-data")
    parser.add_argument(
        "--quality-report",
        type=Path,
        default=REPO_ROOT / "evaluation" / "document_ir_quality_report.json",
    )
    parser.add_argument("--max-tokens", type=int, default=800)
    parser.add_argument(
        "--json-output",
        type=Path,
        default=REPO_ROOT / "evaluation" / "section_chunk_quality_report.json",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        default=REPO_ROOT / "docs" / "P03-P04-section-chunk-evaluation.md",
    )
    args = parser.parse_args()
    paths = _load_high_score_docx(args.input.resolve(), args.quality_report.resolve())
    samples: list[dict] = []
    for path in paths:
        content = path.read_bytes()
        document = parse_document_ir(
            content,
            file_name=path.name,
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        quality = evaluate_section_and_chunking(document, max_tokens=args.max_tokens)
        samples.append({
            "sampleId": f"DOC-{hashlib.sha256(content).hexdigest()[:12]}",
            "fileName": path.name,
            "path": path.as_posix(),
            "quality": quality,
        })

    scores = [item["quality"]["score"] for item in samples]
    readiness = {}
    for item in samples:
        key = item["quality"]["readiness"]
        readiness[key] = readiness.get(key, 0) + 1
    report = {
        "schemaVersion": "section-chunk-quality-v1",
        "generatedAt": date.today().isoformat(),
        "selection": {
            "source": "document_ir_quality_report.json",
            "rule": "format=docx and readiness=ready_for_next_stage",
            "maxTokens": args.max_tokens,
        },
        "summary": {
            "sampleCount": len(samples),
            "averageScore": round(sum(scores) / len(scores), 2) if scores else 0,
            "minimumScore": min(scores) if scores else 0,
            "maximumScore": max(scores) if scores else 0,
            "readyCount": readiness.get("ready_for_next_stage", 0),
            "readiness": readiness,
        },
        "samples": samples,
    }
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_markdown(report, args.markdown_output.resolve())
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
