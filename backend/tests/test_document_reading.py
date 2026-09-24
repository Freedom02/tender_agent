from __future__ import annotations

import unittest

from backend.app.workbuddy_agent.document_map import TemplateLocator
from backend.app.tender_matching.document_models import (
    DocumentIR,
    DocumentPage,
    DocumentSection,
    DocumentSegment,
    DocumentTable,
    DocumentTableCell,
    DocumentTableRow,
)
from backend.app.workbuddy_agent.document_reading import (
    DOCUMENT_READ_SCHEMA_VERSION,
    DocumentReadError,
    DocumentReadService,
)


class DocumentReadServiceTests(unittest.TestCase):
    def _document(self) -> DocumentIR:
        segments = [
            DocumentSegment(
                id="SEG-001",
                text="项目名称：智慧平台采购项目",
                contentType="paragraph",
                order=1,
                page=1,
                paragraphIndex=1,
                sourceLocator="第1页第1段",
            ),
            DocumentSegment(
                id="SEG-002",
                text="技术要求：平台应支持统一身份认证和日志审计。",
                contentType="paragraph",
                order=2,
                page=1,
                paragraphIndex=2,
                sourceLocator="第1页第2段",
            ),
            DocumentSegment(
                id="SEG-003",
                text="模板响应表：投标人填写响应情况并加盖公章。",
                contentType="table_row",
                order=3,
                page=2,
                tableId="TABLE-001",
                rowIndex=0,
                sourceLocator="TABLE-001第0行",
            ),
            DocumentSegment(
                id="SEG-004",
                text="投标无效：未按要求签字盖章。",
                contentType="paragraph",
                order=4,
                page=2,
                paragraphIndex=3,
                sourceLocator="第2页第3段",
            ),
        ]
        sections = [
            DocumentSection(
                id="SEC-ROOT",
                sectionId="SEC-ROOT",
                heading="第一章 项目概况",
                level=1,
                sectionPath=["第一章 项目概况"],
                segmentIds=["SEG-001"],
                startLocator="第1页第1段",
            ),
            DocumentSection(
                id="SEC-TECH",
                sectionId="SEC-TECH",
                heading="第二章 技术要求",
                level=1,
                sectionPath=["第二章 技术要求"],
                segmentIds=["SEG-002"],
                startLocator="第1页第2段",
            ),
            DocumentSection(
                id="SEC-TPL",
                sectionId="SEC-TPL",
                heading="第三章 投标文件格式",
                level=1,
                sectionPath=["第三章 投标文件格式"],
                segmentIds=["SEG-003"],
                startLocator="TABLE-001第0行",
            ),
            DocumentSection(
                id="SEC-REJ",
                sectionId="SEC-REJ",
                heading="第四章 无效投标",
                level=1,
                sectionPath=["第四章 无效投标"],
                segmentIds=["SEG-004"],
                startLocator="第2页第3段",
            ),
        ]
        table = DocumentTable(
            id="TABLE-001",
            tableId="TABLE-001",
            rowCount=2,
            columnCount=2,
            rows=[
                DocumentTableRow(
                    rowIndex=0,
                    sourceLocator="TABLE-001第0行",
                    text="序号 | 技术要求",
                    cells=[
                        DocumentTableCell(
                            id="CELL-001-0",
                            text="序号",
                            tableId="TABLE-001",
                            rowIndex=0,
                            cellIndex=0,
                        ),
                        DocumentTableCell(
                            id="CELL-001-1",
                            text="技术要求",
                            tableId="TABLE-001",
                            rowIndex=0,
                            cellIndex=1,
                        ),
                    ],
                ),
                DocumentTableRow(
                    rowIndex=1,
                    sourceLocator="TABLE-001第1行",
                    text="1 | 统一认证",
                    cells=[
                        DocumentTableCell(
                            id="CELL-002-0",
                            text="1",
                            tableId="TABLE-001",
                            rowIndex=1,
                            cellIndex=0,
                        ),
                        DocumentTableCell(
                            id="CELL-002-1",
                            text="统一认证",
                            tableId="TABLE-001",
                            rowIndex=1,
                            cellIndex=1,
                        ),
                    ],
                ),
            ],
        )
        return DocumentIR(
            documentId="DOC-READ-001",
            documentVersionId="DOC-READ-001-V001",
            fileName="tender.docx",
            fileHash="HASH-READ-001",
            originalFormat="docx",
            mimeType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            text="\n".join(item.text for item in segments),
            pages=[
                DocumentPage(
                    page=1,
                    text="\n".join(item.text for item in segments[:2]),
                    segmentIds=["SEG-001", "SEG-002"],
                    sourceLocator="第1页",
                    hasText=True,
                ),
                DocumentPage(
                    page=2,
                    text="\n".join(item.text for item in segments[2:]),
                    segmentIds=["SEG-003", "SEG-004"],
                    sourceLocator="第2页",
                    hasText=True,
                ),
            ],
            segments=segments,
            sections=sections,
            tables=[table],
            metadata={"parts": [{"partId": "word/document.xml", "kind": "document"}]},
        )

    def setUp(self) -> None:
        self.service = DocumentReadService(self._document())

    def test_inspect_exposes_identity_counts_and_parts_without_full_text(self) -> None:
        result = self.service.inspect_document(
            document_id="DOC-READ-001",
            document_version_id="DOC-READ-001-V001",
        )

        self.assertEqual(result["schemaVersion"], DOCUMENT_READ_SCHEMA_VERSION)
        self.assertEqual(result["document"]["fileHash"], "HASH-READ-001")
        self.assertEqual(result["segmentCount"], 4)
        self.assertEqual(result["sectionCount"], 4)
        self.assertEqual(result["tableCount"], 1)
        self.assertEqual(result["parts"][0]["partId"], "word/document.xml")
        self.assertNotIn("text", result["document"])

    def test_full_read_is_paginated_and_cursor_is_bound_to_operation(self) -> None:
        first = self.service.read_document_full(max_chars=45, max_items=2)

        self.assertTrue(first["hasMore"])
        self.assertTrue(first["nextCursor"])
        self.assertEqual(first["records"][0]["source"]["segmentId"], "SEG-001")
        self.assertEqual(first["records"][0]["source"]["documentVersionId"], "DOC-READ-001-V001")

        second = self.service.read_document_full(
            max_chars=45,
            max_items=2,
            cursor=first["nextCursor"],
        )
        self.assertNotEqual(
            [item["recordId"] for item in first["records"]],
            [item["recordId"] for item in second["records"]],
        )

        with self.assertRaises(DocumentReadError) as error:
            self.service.read_document_range(
                segment_ids=["SEG-001"],
                cursor=first["nextCursor"],
            )
        self.assertEqual(error.exception.code, "invalid_cursor")

    def test_read_range_supports_sections_boundaries_and_source_ids(self) -> None:
        result = self.service.read_document_range(
            section_id="SEC-TECH",
            document_id="DOC-READ-001",
            document_version_id="DOC-READ-001-V001",
        )

        self.assertEqual([item["recordId"] for item in result["records"]], ["SEG-002"])
        self.assertEqual(result["records"][0]["source"]["sectionId"], "SEC-TECH")
        self.assertEqual(result["records"][0]["source"]["paragraphStart"], 2)

        bounded = self.service.read_document_range(
            start_segment_id="SEG-002",
            end_segment_id="SEG-003",
        )
        self.assertEqual(
            [item["recordId"] for item in bounded["records"]],
            ["SEG-002", "SEG-003"],
        )

    def test_read_section_keeps_heading_and_segment_provenance(self) -> None:
        result = self.service.read_section("SEC-TPL")

        self.assertEqual(result["records"][0]["recordType"], "section")
        self.assertEqual(result["records"][0]["text"], "第三章 投标文件格式")
        self.assertEqual(result["records"][1]["recordId"], "SEG-003")
        self.assertEqual(result["records"][1]["source"]["tableId"], "TABLE-001")

    def test_search_returns_context_and_real_source_anchor(self) -> None:
        result = self.service.search_document("投标无效", context_before=1, context_after=0)

        self.assertEqual(result["matchCount"], 1)
        match = result["matches"][0]
        self.assertEqual(match["matchedText"], "投标无效")
        self.assertEqual(match["contextSegmentIds"], ["SEG-003", "SEG-004"])
        self.assertEqual(match["source"]["segmentId"], "SEG-004")

    def test_table_read_keeps_rows_cells_and_table_coordinates(self) -> None:
        result = self.service.read_table_range("TABLE-001", row_start=1, row_end=1)

        self.assertEqual(result["records"][0]["recordType"], "table")
        self.assertEqual(result["records"][1]["recordType"], "table_row")
        row = result["records"][1]
        self.assertEqual(row["source"]["tableId"], "TABLE-001")
        self.assertEqual(row["source"]["rowStart"], 1)
        self.assertEqual(row["metadata"]["cells"][1]["text"], "统一认证")

    def test_template_read_resolves_locator_and_reports_fidelity_boundary(self) -> None:
        result = self.service.read_template_range(
            TemplateLocator(
                templateId="TPL-001",
                templateType="response_table",
                sectionIds=["SEC-TPL"],
                segmentIds=["SEG-003"],
                fidelityLevel="high_fidelity",
            )
        )

        self.assertEqual(result["metadata"]["templateId"], "TPL-001")
        self.assertEqual(result["metadata"]["providedFidelity"], "text_and_table_structure")
        self.assertTrue(result["warnings"])
        self.assertEqual(result["records"][0]["source"]["segmentId"], "SEG-003")

    def test_identity_and_unknown_source_errors_are_explicit(self) -> None:
        with self.assertRaises(DocumentReadError) as identity_error:
            self.service.inspect_document(document_id="OTHER-DOC")
        self.assertEqual(identity_error.exception.code, "document_id_mismatch")

        with self.assertRaises(DocumentReadError) as source_error:
            self.service.read_document_range(segment_ids=["SEG-NOT-FOUND"])
        self.assertEqual(source_error.exception.code, "segment_not_found")

        with self.assertRaises(DocumentReadError) as table_error:
            self.service.read_table_range("TABLE-NOT-FOUND")
        self.assertEqual(table_error.exception.code, "table_not_found")


if __name__ == "__main__":
    unittest.main()
