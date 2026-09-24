from __future__ import annotations

import unittest

from backend.app.tender_matching.document_models import (
    DocumentIR,
    DocumentSection,
    DocumentSegment,
)
from backend.app.tender_matching.section_chunk_quality import (
    evaluate_chunking,
    evaluate_section_tree,
)


class SectionChunkQualityTests(unittest.TestCase):
    def _document(self, *, chapter_level: int = 1) -> DocumentIR:
        segments = [
            DocumentSegment(id="S1", text="第一章 技术要求", order=0, sourceLocator="line 1"),
            DocumentSegment(id="S2", text="平台应支持统一认证。", order=1, sourceLocator="line 2"),
            DocumentSegment(id="S3", text="一、接口要求", order=2, sourceLocator="line 3"),
            DocumentSegment(id="S4", text="平台应提供标准接口。", order=3, sourceLocator="line 4"),
        ]
        sections = [
            DocumentSection(
                id="ROOT",
                sectionId="ROOT",
                heading="第一章 技术要求",
                level=chapter_level,
                isMajor=chapter_level <= 1,
                sectionPath=["第一章 技术要求"],
                headingSegmentId="S1",
                segmentIds=["S1", "S2", "S3", "S4"],
                startLocator="line 1",
                endLocator="line 4",
            ),
            DocumentSection(
                id="CHILD",
                sectionId="CHILD",
                heading="一、接口要求",
                level=chapter_level + 1,
                parentId="ROOT",
                sectionPath=["第一章 技术要求", "一、接口要求"],
                headingSegmentId="S3",
                segmentIds=["S3", "S4"],
                startLocator="line 3",
                endLocator="line 4",
            ),
        ]
        return DocumentIR(
            documentId="DOC-Q",
            documentVersionId="DOC-Q-V001",
            fileName="quality.docx",
            originalFormat="docx",
            text="\n".join(segment.text for segment in segments),
            segments=segments,
            units=segments,
            sections=sections,
        )

    def test_detects_major_heading_level_regression(self) -> None:
        quality = evaluate_section_tree(self._document(chapter_level=2))

        self.assertLess(quality["majorRecall"], 1.0)
        self.assertEqual(quality["readiness"], "usable_with_review")
        self.assertTrue(any(issue["code"] == "major_heading_recall" for issue in quality["issues"]))

    def test_detects_major_heading_precision_regression(self) -> None:
        document = self._document()
        document.sections.append(
            DocumentSection(
                id="EXTRA",
                sectionId="EXTRA",
                heading="项目概况",
                level=1,
                isMajor=True,
                sectionPath=["项目概况"],
                headingSegmentId="S2",
                segmentIds=["S2"],
                startLocator="line 2",
                endLocator="line 2",
            )
        )

        quality = evaluate_section_tree(document)

        self.assertLess(quality["majorPrecision"], 0.8)
        self.assertEqual(quality["readiness"], "usable_with_review")
        self.assertTrue(any(issue["code"] == "major_heading_precision" for issue in quality["issues"]))

    def test_valid_tree_and_chunks_are_ready(self) -> None:
        document = self._document()

        tree = evaluate_section_tree(document)
        chunks = evaluate_chunking(document, max_tokens=20)

        self.assertEqual(tree["readiness"], "ready_for_next_stage")
        self.assertEqual(chunks["sourceCoverage"], 1.0)
        self.assertFalse(chunks["hardFailure"])


if __name__ == "__main__":
    unittest.main()
