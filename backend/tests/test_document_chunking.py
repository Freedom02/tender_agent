from __future__ import annotations

import unittest

from backend.app.tender_matching.document_chunking import chunk_document, estimate_tokens
from backend.app.tender_matching.document_models import (
    DocumentIR,
    DocumentSection,
    DocumentSegment,
)


class DocumentChunkingTests(unittest.TestCase):
    def _document(self) -> DocumentIR:
        segments = [
            DocumentSegment(
                id="SEG-001",
                sourceLocator="page 1, block 1",
                text="第一章 技术要求",
                contentType="pdf_text_block",
                order=0,
                page=1,
            ),
            DocumentSegment(
                id="SEG-002",
                sourceLocator="page 1, block 2",
                text="平台应支持统一身份认证和接口集成。",
                contentType="pdf_text_block",
                order=1,
                page=1,
            ),
            DocumentSegment(
                id="SEG-003",
                sourceLocator="page 1, block 3",
                text="一、产品参数",
                contentType="pdf_text_block",
                order=2,
                page=1,
            ),
            DocumentSegment(
                id="SEG-004",
                sourceLocator="page 1, block 4",
                text="设备数量 | 10 | 套",
                contentType="table_row",
                order=3,
                page=1,
                tableId="TABLE-001",
            ),
            DocumentSegment(
                id="SEG-005",
                sourceLocator="page 2, block 1",
                text="第二章 商务要求",
                contentType="pdf_text_block",
                order=4,
                page=2,
            ),
            DocumentSegment(
                id="SEG-006",
                sourceLocator="page 2, block 2",
                text="交付期限为合同签订后 30 日内。",
                contentType="pdf_text_block",
                order=5,
                page=2,
            ),
        ]
        sections = [
            DocumentSection(
                id="SEC-001",
                sectionId="SEC-001",
                heading="第一章 技术要求",
                sectionPath=["第一章 技术要求"],
                level=1,
                isMajor=True,
                segmentIds=["SEG-001", "SEG-002", "SEG-003", "SEG-004"],
                startLocator="page 1, block 1",
                endLocator="page 1, block 4",
            ),
            DocumentSection(
                id="SEC-002",
                sectionId="SEC-002",
                heading="一、产品参数",
                sectionPath=["第一章 技术要求", "一、产品参数"],
                parentId="SEC-001",
                level=2,
                segmentIds=["SEG-003", "SEG-004"],
                startLocator="page 1, block 3",
                endLocator="page 1, block 4",
            ),
            DocumentSection(
                id="SEC-003",
                sectionId="SEC-003",
                heading="第二章 商务要求",
                sectionPath=["第二章 商务要求"],
                level=1,
                isMajor=True,
                segmentIds=["SEG-005", "SEG-006"],
                startLocator="page 2, block 1",
                endLocator="page 2, block 2",
            ),
        ]
        return DocumentIR(
            documentId="DOC-TEST",
            documentVersionId="DOC-TEST-V001",
            fileName="test.pdf",
            originalFormat="pdf",
            text="\n".join(segment.text for segment in segments),
            segments=segments,
            units=segments,
            sections=sections,
        )

    def test_assigns_overlapping_parent_spans_to_most_specific_section(self) -> None:
        chunks = chunk_document(self._document(), max_tokens=20)

        self.assertEqual([chunk.section_id for chunk in chunks], ["SEC-001", "SEC-002", "SEC-003"])
        self.assertEqual(chunks[0].segment_ids, ["SEG-001", "SEG-002"])
        self.assertEqual(chunks[1].section_path, ["第一章 技术要求", "一、产品参数"])
        self.assertEqual(chunks[1].table_ids, ["TABLE-001"])
        self.assertEqual(chunks[1].page_start, 1)
        self.assertEqual(chunks[1].page_end, 1)
        self.assertIn("第一章 技术要求 > 一、产品参数", chunks[1].context_prefix)

    def test_splits_long_segments_without_losing_source_anchor(self) -> None:
        document = self._document()
        document.segments[1].text = "。".join(["平台应支持统一身份认证"] * 30) + "。"

        chunks = chunk_document(document, max_tokens=10)

        self.assertGreater(len(chunks), 3)
        long_chunks = [chunk for chunk in chunks if chunk.section_id == "SEC-001"]
        self.assertTrue(all(chunk.segment_ids == ["SEG-001"] or "SEG-002" in chunk.segment_ids for chunk in long_chunks))
        self.assertTrue(all(chunk.content for chunk in chunks))
        self.assertTrue(all(chunk.token_count <= 10 for chunk in chunks))
        self.assertEqual(len({chunk.fingerprint for chunk in chunks}), len(chunks))

    def test_estimate_tokens_is_stable_and_rejects_invalid_budget(self) -> None:
        self.assertEqual(estimate_tokens(""), 1)
        self.assertEqual(estimate_tokens("abcd"), 1)
        with self.assertRaises(ValueError):
            chunk_document(self._document(), max_tokens=0)


if __name__ == "__main__":
    unittest.main()
