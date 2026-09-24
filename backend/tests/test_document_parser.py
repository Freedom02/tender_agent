from __future__ import annotations

import unittest
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from backend.app.store import Store
from backend.app.tender_matching.document_parser import (
    _extract_pymupdf_tables,
    parse_doc,
    parse_docx,
    parse_document_ir,
    parse_pdf,
    parse_plain_text,
)
from backend.app.tender_matching.document_models import DocumentIR, document_ir_to_json


def _docx_with_body(body: str, *, header: str = "", footer: str = "") -> bytes:
    document_xml = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>{body}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/>
    </w:sectPr>
  </w:body>
</w:document>"""
    header_xml = f"""<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:p><w:r><w:t>{header}</w:t></w:r></w:p>
    </w:hdr>"""
    footer_xml = f"""<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:p><w:r><w:t>{footer}</w:t></w:r></w:p>
    </w:ftr>"""
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
        if header:
            archive.writestr("word/header1.xml", header_xml)
        if footer:
            archive.writestr("word/footer1.xml", footer_xml)
    return output.getvalue()


def _pdf_with_text_pages(pages: list[str]) -> bytes:
    """Build a tiny uncompressed PDF fixture without depending on a PDF writer."""
    page_count = len(pages)
    page_object_ids = [3 + index for index in range(page_count)]
    content_object_ids = [3 + page_count + index for index in range(page_count)]
    font_object_id = 3 + (page_count * 2)

    objects: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: (
            b"<< /Type /Pages /Kids ["
            + b" ".join(f"{object_id} 0 R".encode("ascii") for object_id in page_object_ids)
            + f"] /Count {page_count} >>".encode("ascii")
        ),
        font_object_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }
    for index, (page_text, page_object_id, content_object_id) in enumerate(
        zip(pages, page_object_ids, content_object_ids),
        start=1,
    ):
        escaped = (
            page_text.replace("\\", "\\\\")
            .replace("(", "\\(")
            .replace(")", "\\)")
            .encode("ascii")
        )
        stream = b"BT /F1 12 Tf 72 720 Td (" + escaped + b") Tj ET"
        objects[page_object_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Contents {content_object_id} 0 R /Resources << /Font << /F1 {font_object_id} 0 R >> >> >>".encode(
                "ascii"
            )
        )
        objects[content_object_id] = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )

    output = BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for object_id in range(1, font_object_id + 1):
        offsets.append(output.tell())
        output.write(f"{object_id} 0 obj\n".encode("ascii"))
        output.write(objects[object_id])
        output.write(b"\nendobj\n")
    xref_offset = output.tell()
    output.write(f"xref\n0 {font_object_id + 1}\n".encode("ascii"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.write(
        f"trailer\n<< /Size {font_object_id + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return output.getvalue()


class DocumentParserTests(unittest.TestCase):
    def test_plain_text_has_stable_structured_segments(self) -> None:
        parsed = parse_plain_text("项目名称：平台项目\n\n投标人须提供营业执照".encode("utf-8"))

        self.assertEqual(parsed["parseMethod"], "plain_text_structured")
        self.assertEqual(len(parsed["segments"]), 2)
        self.assertEqual(parsed["segments"][0]["id"], "SEG-000001")
        self.assertEqual(parsed["segments"][1]["sourceLocator"], "第3行")
        self.assertEqual(parsed["segments"], parsed["units"])

    def test_docx_keeps_paragraph_table_row_and_cell_structure_in_order(self) -> None:
        body = """
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>第三章 资格审查</w:t></w:r></w:p>
        <w:tbl>
          <w:tblGrid><w:gridCol w:w="1800"/><w:gridCol w:w="6200"/></w:tblGrid>
          <w:tr>
            <w:tc><w:p><w:r><w:t>序号</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>资格要求</w:t></w:r></w:p></w:tc>
          </w:tr>
          <w:tr>
            <w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>投标人须具有有效营业执照</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
        <w:p><w:r><w:t>第四章 技术要求</w:t></w:r></w:p>
        """
        parsed = parse_docx(_docx_with_body(body, header="采购文件", footer="第 PAGE 页"))

        self.assertEqual(parsed["parseMethod"], "docx_xml_structured")
        self.assertEqual([item["contentType"] for item in parsed["segments"]], [
            "paragraph",
            "table_row",
            "table_row",
            "paragraph",
        ])
        self.assertEqual(parsed["segments"][1]["sourceLocator"], "TABLE-001第1行")
        self.assertEqual(
            parsed["segments"][2]["cells"][1]["text"],
            "投标人须具有有效营业执照",
        )
        self.assertEqual(parsed["segments"][0]["style"]["styleId"], "Heading1")
        self.assertEqual(parsed["tables"][0]["columnCount"], 2)
        self.assertEqual(parsed["metadata"]["headers"], ["采购文件"])
        self.assertEqual(parsed["metadata"]["footers"], ["第 PAGE 页"])
        self.assertEqual(parsed["metadata"]["layout"]["pageWidthTwips"], "11906")
        self.assertEqual(parsed["segments"][0]["order"], 0)
        self.assertEqual(parsed["segments"][1]["order"], 1)
        self.assertEqual(parsed["segments"][2]["order"], 2)
        self.assertEqual(parsed["segments"][3]["order"], 3)

    def test_legacy_doc_is_not_decoded_as_plain_utf8(self) -> None:
        try:
            import legacy_doc  # noqa: F401
        except ImportError:
            self.skipTest("legacy-doc 未安装，跳过旧版 DOC 正文解析测试")
        sample = next(
            (
                path
                for path in (Path(__file__).parents[1] / ".." / "runtime").rglob("*.doc")
            ),
            None,
        )
        if sample is None:
            self.skipTest("没有可用于回归测试的旧版 DOC 样本")

        parsed = parse_doc(sample.read_bytes())

        self.assertEqual(parsed["parseMethod"], "legacy_doc_structured")
        self.assertGreater(len(parsed["text"]), 1000)
        self.assertGreater(sum("\u4e00" <= char <= "\u9fff" for char in parsed["text"]), 100)
        self.assertNotIn("\ufffd", parsed["text"])
        self.assertEqual(parsed["segments"], parsed["units"])

    def test_legacy_doc_parse_failure_returns_empty_result_instead_of_garbled_text(self) -> None:
        parsed = parse_doc(b"not a legacy word document")

        self.assertEqual(parsed["parseMethod"], "legacy_doc_failed")
        self.assertEqual(parsed["text"], "")
        self.assertEqual(parsed["segments"], [])
        self.assertTrue(parsed["warnings"])

        store = Store.__new__(Store)
        routed = store.extract_document_content(
            "broken.doc",
            b"not a legacy word document",
            "application/msword",
        )
        self.assertEqual(routed["parseMethod"], "legacy_doc_failed")
        self.assertEqual(routed["text"], "")

    def test_pdf_uses_page_segments_and_keeps_scan_page_warning(self) -> None:
        content = _pdf_with_text_pages(["Tender deadline: 2026-10-12 09:30", ""])

        parsed = parse_pdf(content)

        self.assertEqual(parsed["parseMethod"], "pypdf_structured")
        self.assertEqual(parsed["metadata"]["pageCount"], 2)
        self.assertEqual(len(parsed["segments"]), 1)
        self.assertEqual(parsed["segments"][0]["sourceLocator"], "第1页")
        self.assertEqual(parsed["segments"][0]["page"], 1)
        self.assertEqual(parsed["segments"][0]["contentType"], "page")
        self.assertIn("Tender deadline: 2026-10-12 09:30", parsed["text"])
        self.assertTrue(any(warning.startswith("第2页未提取到文字") for warning in parsed["warnings"]))

        routed = Store.__new__(Store).extract_document_content(
            "tender.pdf",
            content,
            "application/octet-stream",
        )
        self.assertEqual(routed["parseMethod"], "pypdf_structured")
        self.assertEqual(routed["segments"][0]["page"], 1)

    def test_document_ir_round_trips_and_keeps_docx_structure(self) -> None:
        body = """
        <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Technical requirements</w:t></w:r></w:p>
        <w:tbl>
          <w:tr><w:tc><w:p><w:r><w:t>Item</w:t></w:r></w:p></w:tc>
          <w:tc><w:p><w:r><w:t>Must support API integration</w:t></w:r></w:p></w:tc></w:tr>
        </w:tbl>
        """
        content = _docx_with_body(body)

        document = parse_document_ir(
            content,
            file_name="tender.docx",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

        self.assertIsInstance(document, DocumentIR)
        self.assertEqual(document.original_format, "docx")
        self.assertTrue(document.document_id.startswith("DOC-"))
        self.assertEqual(document.document_version_id, f"{document.document_id}-V001")
        self.assertEqual(len(document.segments), 2)
        self.assertEqual(len(document.tables), 1)
        self.assertEqual(document.tables[0].rows[0].cells[1].text, "Must support API integration")
        self.assertGreaterEqual(len(document.sections), 1)
        self.assertEqual(document.diagnostics.segment_count, 2)

        artifact = document_ir_to_json(document)
        restored = DocumentIR.model_validate(artifact)
        self.assertEqual(restored.document_version_id, document.document_version_id)
        self.assertEqual(restored.segments[0].source_locator, document.segments[0].source_locator)
        self.assertEqual(restored.tables[0].rows[0].cells[1].text, "Must support API integration")

    def test_document_ir_builds_pdf_pages_and_scan_diagnostics(self) -> None:
        content = _pdf_with_text_pages(["Page one", ""])

        document = parse_document_ir(content, file_name="tender.pdf", mime_type="application/pdf")

        self.assertEqual(document.original_format, "pdf")
        try:
            import pymupdf  # noqa: F401
            expected_method = "pymupdf_blocks_structured"
        except ImportError:
            try:
                import fitz  # noqa: F401
                expected_method = "pymupdf_blocks_structured"
            except ImportError:
                expected_method = "pypdf_lines_structured"
        self.assertEqual(document.parse_method, expected_method)
        self.assertEqual(len(document.pages), 2)
        self.assertEqual(document.pages[0].page_number, 1)
        self.assertTrue(document.pages[0].has_text)
        self.assertFalse(document.pages[1].has_text)
        self.assertEqual(document.pages[0].segment_ids, ["SEG-000001"])
        self.assertIn(
            document.segments[0].content_type,
            {"pdf_text_block", "pdf_text_line"},
        )
        self.assertEqual(document.segments[0].page, 1)
        if expected_method == "pymupdf_blocks_structured":
            self.assertEqual(len(document.segments[0].bbox or []), 4)
            self.assertGreater(document.pages[0].width or 0, 0)
            self.assertGreater(document.pages[0].height or 0, 0)
        else:
            self.assertIsNone(document.segments[0].bbox)
        self.assertEqual(document.diagnostics.page_count, 2)
        self.assertEqual(document.diagnostics.status, "parsed")
        self.assertTrue(document.warnings)

    def test_document_ir_pdf_fallback_keeps_line_units_and_scan_status(self) -> None:
        content = _pdf_with_text_pages(
            ["Chapter 1 Technical Requirements\nProject name: Test Project\nService period: one year", ""]
        )

        with patch(
            "backend.app.tender_matching.document_parser._parse_pdf_with_pymupdf",
            return_value=None,
        ):
            document = parse_document_ir(
                content,
                file_name="fallback.pdf",
                mime_type="application/pdf",
            )

        self.assertEqual(document.parse_method, "pypdf_lines_structured")
        self.assertEqual(
            [segment.content_type for segment in document.segments],
            ["pdf_text_line", "pdf_text_line", "pdf_text_line"],
        )
        self.assertEqual(document.segments[0].source_locator, "page 1, line 1")
        self.assertEqual(document.pages[0].segment_ids, [
            "SEG-000001",
            "SEG-000002",
            "SEG-000003",
        ])
        self.assertFalse(document.pages[1].has_text)
        self.assertTrue(document.warnings)

    def test_pymupdf_table_adapter_keeps_rows_cells_and_source_coordinates(self) -> None:
        class FakeTable:
            bbox = (10, 20, 300, 120)

            def extract(self):
                return [["字段", "值"], ["项目编号", "T-001"]]

        class FakeFinder:
            tables = [FakeTable()]

        class FakePage:
            def find_tables(self):
                return FakeFinder()

        tables, row_segments = _extract_pymupdf_tables(
            FakePage(),
            page_number=3,
            table_index_start=1,
        )

        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]["tableId"], "TABLE-P003-001")
        self.assertEqual(tables[0]["page"], 3)
        self.assertEqual(tables[0]["rowCount"], 2)
        self.assertEqual(tables[0]["rows"][1]["cells"][1]["text"], "T-001")
        self.assertEqual(len(row_segments), 2)
        self.assertEqual(row_segments[1]["contentType"], "pdf_table_row")
        self.assertEqual(row_segments[1]["page"], 3)
        self.assertEqual(row_segments[1]["bbox"], [10.0, 20.0, 300.0, 120.0])

    def test_store_typed_entry_point_does_not_change_legacy_dictionary_entry_point(self) -> None:
        content = b"Project name\nTechnical requirements"
        store = Store.__new__(Store)

        legacy = store.extract_document_content("tender.txt", content, "text/plain")
        typed = store.extract_document_ir("tender.txt", content, "text/plain")

        self.assertIsInstance(legacy, dict)
        self.assertIsInstance(typed, DocumentIR)
        self.assertEqual(legacy["text"], typed.text)
        self.assertEqual(legacy["parseMethod"], typed.parse_method)


if __name__ == "__main__":
    unittest.main()
