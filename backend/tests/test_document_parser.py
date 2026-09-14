from __future__ import annotations

import unittest
import zipfile
from io import BytesIO
from pathlib import Path

from backend.app.store import Store
from backend.app.tender_matching.document_parser import parse_doc, parse_docx, parse_plain_text


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


if __name__ == "__main__":
    unittest.main()
