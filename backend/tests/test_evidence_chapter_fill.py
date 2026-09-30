# -*- coding: utf-8 -*-
"""证明材料一键填充：正文应内联真实扫描件内容，而不是只给证据地址。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.bid_writing.assemble import markdown_to_docx  # noqa: E402
from backend.app.bid_writing.evidence_chapter import (  # noqa: E402
    build_evidence_chapter_result,
)


def _entry(sequence: int, refs: list[dict]) -> dict:
    return {
        "sequence": sequence,
        "requirementId": f"technical-{sequence:04d}",
        "requirement": f"第 {sequence} 条技术要求",
        "category": "technical",
        "matchStatus": "matched",
        "matchReason": "",
        "gaps": [],
        "materials": [],
        "sourceRefs": refs,
        "status": "ready" if refs else "missing",
    }


class EvidenceChapterFillTests(unittest.TestCase):
    def test_inlines_material_images_when_assets_provided(self) -> None:
        refs = [
            {"factKey": "products", "factId": "p-1", "name": "产品说明书",
             "evidencePath": "libraries/company/证明材料/技术产品/1/"},
        ]
        assets = {
            "libraries/company/证明材料/技术产品/1/": {
                "available": True,
                "files": [
                    {"url": "/runtime/libraries/company/证明材料/技术产品/1/1.png", "name": "1.png"},
                    {"url": "/runtime/libraries/company/证明材料/技术产品/1/2.png", "name": "2.png"},
                ],
                "placeholder": False,
                "kind": "image",
                "caption": "产品说明书",
                "pageCount": 2,
            },
        }
        result = build_evidence_chapter_result(None, [_entry(1, refs)], assets=assets)
        content = result["content"]

        # 多页证据的图注带页码，便于在正文里对应原件第几页
        self.assertIn(
            "![产品说明书（第 1 页）](/runtime/libraries/company/证明材料/技术产品/1/1.png)",
            content,
        )
        self.assertIn("附图：产品说明书（共 2 页）", content)
        # 命中真实扫描件后，不再退化为只给证据地址
        self.assertNotIn("证据路径：libraries/company/证明材料/技术产品/1/", content)

        group = result["evidenceGroups"][0]
        self.assertEqual(
            group["insertRefs"][0]["files"][0]["url"],
            "/runtime/libraries/company/证明材料/技术产品/1/1.png",
        )
        self.assertEqual(result["summary"]["scannedTotal"], 1)
        self.assertEqual(result["summary"]["scannedReady"], 1)

    def test_falls_back_to_path_when_no_assets(self) -> None:
        refs = [
            {"factKey": "products", "factId": "p-1", "name": "产品说明书",
             "evidencePath": "libraries/company/证明材料/技术产品/1/"},
        ]
        result = build_evidence_chapter_result(None, [_entry(1, refs)])
        self.assertIn("证据路径：libraries/company/证明材料/技术产品/1/", result["content"])

    def test_placeholder_keeps_hint_without_images(self) -> None:
        refs = [
            {"factKey": "qualifications", "factId": "q-1", "name": "资质证书",
             "evidencePath": "libraries/company/证明材料/资质认证/9/"},
        ]
        assets = {
            "libraries/company/证明材料/资质认证/9/": {
                "available": False,
                "files": [],
                "placeholder": True,
                "kind": "attachment",
                "caption": "资质证书",
                "pageCount": 0,
            },
        }
        result = build_evidence_chapter_result(None, [_entry(1, refs)], assets=assets)
        self.assertIn("待补扫描件（原件尚未上传）", result["content"])
        self.assertEqual(result["summary"]["scannedTotal"], 1)
        self.assertEqual(result["summary"]["scannedReady"], 0)


class MarkdownImageSkipTests(unittest.TestCase):
    def test_markdown_to_docx_skips_inline_image_lines(self) -> None:
        from docx import Document

        doc = Document()
        markdown_to_docx(
            doc,
            "# 技术参数证明材料\n\n## 1. 要求\n\n![图注](/runtime/evidence_preview/x/1.png)\n\n- 附图：图注",
        )
        texts = [p.text for p in doc.paragraphs]
        self.assertFalse(any("![" in t or "/runtime/" in t for t in texts))
        self.assertTrue(any("附图：图注" in t for t in texts))


if __name__ == "__main__":
    unittest.main()
