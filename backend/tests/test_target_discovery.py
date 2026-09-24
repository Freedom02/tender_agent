from __future__ import annotations

import unittest

from backend.app.workbuddy_agent.document_map import discover_document_map
from backend.app.tender_matching.document_models import (
    DocumentIR,
    DocumentSection,
    DocumentSegment,
    DocumentTable,
    DocumentTableCell,
    DocumentTableRow,
)


class TargetDiscoveryTests(unittest.TestCase):
    def _document(self) -> DocumentIR:
        segments = [
            DocumentSegment(
                id="SEG-ROOT",
                text="第五章 招标内容及要求",
                order=1,
            ),
            DocumentSegment(
                id="SEG-TECH",
                text="技术和服务要求 平台必须支持三维场景和 GLTF 模型加载。",
                order=2,
            ),
            DocumentSegment(
                id="SEG-COMM",
                text="商务要求：交付期限为合同签订后30日，质保期三年。",
                order=3,
            ),
            DocumentSegment(
                id="SEG-EVAL",
                text="评分标准：技术评分20分，商务评分10分。",
                order=4,
            ),
            DocumentSegment(
                id="SEG-TPL",
                text="技术和服务要求响应表，投标人填写并签字盖章。",
                order=5,
                table_id="TABLE-TPL",
            ),
            DocumentSegment(
                id="SEG-CONTRACT",
                text="合同范本：中标后合同履行及违约责任。",
                order=6,
            ),
            DocumentSegment(
                id="SEG-REJ",
                text="投标报价超过最高限价的，投标无效。",
                order=7,
            ),
        ]
        sections = [
            DocumentSection(
                id="SEC-ROOT",
                sectionId="SEC-ROOT",
                heading="第五章 招标内容及要求",
                level=1,
                isMajor=True,
                segmentIds=["SEG-ROOT"],
            ),
            DocumentSection(
                id="SEC-TECH",
                sectionId="SEC-TECH",
                heading="一、技术和服务要求",
                level=2,
                parentId="SEC-ROOT",
                sectionPath=["第五章 招标内容及要求", "一、技术和服务要求"],
                segmentIds=["SEG-TECH"],
            ),
            DocumentSection(
                id="SEC-COMM",
                sectionId="SEC-COMM",
                heading="二、商务要求",
                level=2,
                parentId="SEC-ROOT",
                sectionPath=["第五章 招标内容及要求", "二、商务要求"],
                segmentIds=["SEG-COMM"],
            ),
            DocumentSection(
                id="SEC-EVAL",
                sectionId="SEC-EVAL",
                heading="三、评分标准",
                level=2,
                parentId="SEC-ROOT",
                sectionPath=["第五章 招标内容及要求", "三、评分标准"],
                segmentIds=["SEG-EVAL"],
            ),
            DocumentSection(
                id="SEC-TPL",
                sectionId="SEC-TPL",
                heading="四、技术和服务要求响应表",
                level=2,
                parentId="SEC-ROOT",
                sectionPath=["第五章 招标内容及要求", "四、技术和服务要求响应表"],
                segmentIds=["SEG-TPL"],
            ),
            DocumentSection(
                id="SEC-CONTRACT",
                sectionId="SEC-CONTRACT",
                heading="第六章 合同范本",
                level=1,
                isMajor=True,
                segmentIds=["SEG-CONTRACT"],
            ),
            DocumentSection(
                id="SEC-REJ",
                sectionId="SEC-REJ",
                heading="投标无效情形",
                level=2,
                segmentIds=["SEG-REJ"],
            ),
        ]
        table = DocumentTable(
            id="TABLE-TPL",
            tableId="TABLE-TPL",
            rows=[
                DocumentTableRow(
                    rowIndex=0,
                    cells=[
                        DocumentTableCell(text="序号"),
                        DocumentTableCell(text="技术要求"),
                        DocumentTableCell(text="投标响应"),
                    ],
                )
            ],
        )
        return DocumentIR(
            documentId="DOC-TARGET",
            documentVersionId="DOC-TARGET-V1",
            fileName="target.docx",
            fileHash="FILE-TARGET",
            originalFormat="docx",
            segments=segments,
            sections=sections,
            tables=[table],
        )

    def test_discovers_leaf_ranges_and_keeps_container_as_context(self) -> None:
        result = discover_document_map(self._document(), run_id="RUN-TARGET")
        active = {(item.domain, item.title): item for item in result.sections}

        technical = active[("technical", "一、技术和服务要求")]
        self.assertEqual(technical.processing_mode, "fine_extract")
        self.assertEqual(technical.role, "leaf")
        self.assertEqual(technical.owner_section_id, "SEC-TECH")
        self.assertEqual(technical.context_section_ids, ["SEC-ROOT"])

        self.assertNotIn(("technical", "第五章 招标内容及要求"), active)
        self.assertEqual(active[("commercial", "二、商务要求")].processing_mode, "display")
        self.assertEqual(active[("evaluation", "三、评分标准")].primary_route, "evaluation_processor")
        self.assertNotIn(("qualification", "第一章 投标邀请"), active)

    def test_template_and_contract_have_separate_processing_modes(self) -> None:
        result = discover_document_map(self._document())
        template = next(item for item in result.sections if item.domain == "template")
        contract = next(item for item in result.ignored_sections if item.domain == "contract")

        self.assertEqual(template.processing_mode, "template_extract")
        self.assertIsNotNone(template.template_locator)
        self.assertEqual(contract.processing_mode, "ignore")
        self.assertEqual(contract.primary_route, "ignored_contract")

    def test_rejection_is_a_full_text_overlay_with_source_candidate(self) -> None:
        result = discover_document_map(self._document())
        self.assertTrue(result.rejection_candidates)
        self.assertTrue(any(item.domain == "rejection" for item in result.sections))
        self.assertEqual(
            sum(item.domain == "rejection" for item in result.sections),
            1,
        )
        candidate = result.rejection_candidates[0]
        self.assertEqual(candidate.matched_term, "投标无效")
        self.assertEqual(candidate.source.segment_id, "SEG-REJ")


if __name__ == "__main__":
    unittest.main()
