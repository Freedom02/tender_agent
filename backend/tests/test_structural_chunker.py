from __future__ import annotations

import unittest

from backend.app.workbuddy_agent.document_map import (
    DocumentMap,
    DocumentMapSection,
    DocumentSourceAnchor,
    TemplateLocator,
    build_structural_blocks,
)
from backend.app.tender_matching.document_models import (
    DocumentIR,
    DocumentSection,
    DocumentSegment,
    DocumentTable,
    DocumentTableCell,
    DocumentTableRow,
)


class StructuralChunkerTests(unittest.TestCase):
    def _document_and_map(self):
        segments = [
            DocumentSegment(
                id="SEG-ROOT",
                text="第五章 招标内容及要求",
                order=1,
            ),
            DocumentSegment(
                id="SEG-TECH",
                text="技术要求正文。" * 30,
                order=2,
            ),
            DocumentSegment(
                id="SEG-ROW-1",
                text="平台 | 支持三维场景",
                order=3,
                tableId="TABLE-TECH",
            ),
            DocumentSegment(
                id="SEG-COMM",
                text="商务要求：交付期限为30日。",
                order=4,
            ),
            DocumentSegment(
                id="SEG-TPL",
                text="投标响应表，请填写。",
                order=5,
                tableId="TABLE-TPL",
            ),
            DocumentSegment(
                id="SEG-CONTRACT",
                text="合同范本正文。",
                order=6,
            ),
        ]
        sections = [
            DocumentSection(
                id="SEC-ROOT",
                sectionId="SEC-ROOT",
                heading="第五章 招标内容及要求",
                sectionPath=["第五章 招标内容及要求"],
                segmentIds=["SEG-ROOT", "SEG-TECH", "SEG-ROW-1", "SEG-COMM", "SEG-TPL"],
                isMajor=True,
            ),
            DocumentSection(
                id="SEC-TECH",
                sectionId="SEC-TECH",
                heading="一、技术要求",
                sectionPath=["第五章 招标内容及要求", "一、技术要求"],
                parentId="SEC-ROOT",
                segmentIds=["SEG-TECH", "SEG-ROW-1"],
            ),
            DocumentSection(
                id="SEC-COMM",
                sectionId="SEC-COMM",
                heading="二、商务要求",
                sectionPath=["第五章 招标内容及要求", "二、商务要求"],
                parentId="SEC-ROOT",
                segmentIds=["SEG-COMM"],
            ),
            DocumentSection(
                id="SEC-TPL",
                sectionId="SEC-TPL",
                heading="三、响应表",
                sectionPath=["第五章 招标内容及要求", "三、响应表"],
                parentId="SEC-ROOT",
                segmentIds=["SEG-TPL"],
            ),
            DocumentSection(
                id="SEC-CONTRACT",
                sectionId="SEC-CONTRACT",
                heading="第六章 合同范本",
                segmentIds=["SEG-CONTRACT"],
                isMajor=True,
            ),
        ]
        tables = [
            DocumentTable(
                id="TABLE-TECH",
                tableId="TABLE-TECH",
                rows=[
                    DocumentTableRow(
                        rowIndex=0,
                        cells=[
                            DocumentTableCell(text="平台"),
                            DocumentTableCell(text="支持三维场景"),
                        ],
                    )
                ],
            ),
            DocumentTable(
                id="TABLE-TPL",
                tableId="TABLE-TPL",
                rows=[
                    DocumentTableRow(
                        rowIndex=0,
                        cells=[
                            DocumentTableCell(text="序号"),
                            DocumentTableCell(text="响应"),
                        ],
                    )
                ],
            ),
        ]
        document = DocumentIR(
            documentId="DOC-BLOCK",
            documentVersionId="DOC-BLOCK-V1",
            fileName="block.docx",
            segments=segments,
            sections=sections,
            tables=tables,
        )
        source = lambda sid, segs, tables=None: DocumentSourceAnchor(
            documentId="DOC-BLOCK",
            documentVersionId="DOC-BLOCK-V1",
            sectionIds=[sid],
            segmentIds=segs,
            tableIds=tables or [],
        )
        mapped = DocumentMap(
            mapId="MAP-BLOCK",
            runId="RUN-BLOCK",
            documentId="DOC-BLOCK",
            documentVersionId="DOC-BLOCK-V1",
            sections=[
                DocumentMapSection(
                    mapId="MAP-TECH",
                    domain="technical",
                    role="leaf",
                    processingMode="fine_extract",
                    primaryRoute="technical_processor",
                    title="一、技术要求",
                    sectionIds=["SEC-TECH"],
                    sourceSegmentIds=["SEG-TECH", "SEG-ROW-1"],
                    source=source("SEC-TECH", ["SEG-TECH", "SEG-ROW-1"], ["TABLE-TECH"]),
                ),
                DocumentMapSection(
                    mapId="MAP-COMM",
                    domain="commercial",
                    processingMode="display",
                    primaryRoute="display_processor",
                    title="二、商务要求",
                    sectionIds=["SEC-COMM"],
                    sourceSegmentIds=["SEG-COMM"],
                    source=source("SEC-COMM", ["SEG-COMM"]),
                ),
                DocumentMapSection(
                    mapId="MAP-TPL",
                    domain="template",
                    processingMode="template_extract",
                    primaryRoute="template_processor",
                    title="三、响应表",
                    sectionIds=["SEC-TPL"],
                    sourceSegmentIds=["SEG-TPL"],
                    source=source("SEC-TPL", ["SEG-TPL"], ["TABLE-TPL"]),
                    templateLocator=TemplateLocator(
                        templateId="TPL-1",
                        title="三、响应表",
                        sectionIds=["SEC-TPL"],
                        segmentIds=["SEG-TPL"],
                        tableIds=["TABLE-TPL"],
                    ),
                ),
            ],
        )
        return document, mapped

    def test_only_fine_targets_create_fine_blocks_and_tables_stay_atomic(self):
        document, mapped = self._document_and_map()
        result = build_structural_blocks(document, mapped, max_tokens=20)
        self.assertTrue(result.stats["fineBlockCount"] > 1)
        self.assertEqual(result.stats["tableBlockCount"], 2)
        self.assertFalse(
            any(
                block.block_type == "fine" and block.domain in {"commercial", "template"}
                for block in result.blocks
            )
        )
        table_blocks = [block for block in result.blocks if block.block_type == "table"]
        self.assertEqual(
            {table_id for block in table_blocks for table_id in block.table_ids},
            {"TABLE-TECH", "TABLE-TPL"},
        )
        self.assertTrue(
            all(block.parent_block_id for block in table_blocks)
        )

    def test_parent_content_is_not_duplicated_into_child_range(self):
        document, mapped = self._document_and_map()
        result = build_structural_blocks(document, mapped)
        section_blocks = [block for block in result.blocks if block.block_type == "section"]
        self.assertNotIn("SEG-ROOT", {segment_id for block in section_blocks for segment_id in block.segment_ids})

    def test_explicit_disjoint_ranges_do_not_widen_to_parent_section(self):
        document, mapped = self._document_and_map()
        mapped.sections[0].source_segment_ids = []
        mapped.sections[0].ranges = [
            {
                "rangeId": "RANGE-TECH-1",
                "segmentIds": ["SEG-TECH"],
            },
            {
                "rangeId": "RANGE-TECH-2",
                "segmentIds": ["SEG-ROW-1"],
            },
        ]
        result = build_structural_blocks(document, mapped, max_tokens=1000)
        technical_ids = {
            segment_id
            for block in result.blocks
            if block.domain == "technical"
            for segment_id in block.segment_ids
        }
        self.assertEqual(technical_ids, {"SEG-TECH", "SEG-ROW-1"})
        self.assertNotIn("SEG-COMM", technical_ids)
        self.assertNotIn("SEG-TPL", technical_ids)


if __name__ == "__main__":
    unittest.main()
