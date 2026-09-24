from __future__ import annotations

import unittest

from backend.app.tender_matching.document_models import DocumentIR
from backend.app.tender_matching.document_parser import parse_document_ir
from backend.app.tender_matching.document_quality import evaluate_document_ir


class DocumentQualityTests(unittest.TestCase):
    def test_plain_text_document_is_ready_and_round_trips(self) -> None:
        content = "项目名称：质量评估测试项目\n技术要求：支持结构化解析".encode("utf-8")
        document = parse_document_ir(
            content,
            file_name="sample.txt",
            mime_type="text/plain",
        )

        quality = evaluate_document_ir(
            document,
            source_content=content,
            expected_file_name="sample.txt",
        )

        self.assertFalse(quality["hardFailure"])
        self.assertEqual(quality["readiness"], "ready_for_next_stage")
        self.assertTrue(quality["dimensions"]["diagnostics"]["jsonRoundTrip"])
        self.assertEqual(quality["dimensions"]["provenance"]["fileHashValid"], True)

    def test_failed_document_is_not_ready_even_when_schema_is_valid(self) -> None:
        document = DocumentIR(
            fileName="broken.doc",
            originalFormat="doc",
            parseMethod="legacy_doc_failed",
            diagnostics={
                "status": "failed",
                "parseMethod": "legacy_doc_failed",
            },
        )

        quality = evaluate_document_ir(document, source_content=b"broken")

        self.assertTrue(quality["hardFailure"])
        self.assertEqual(quality["readiness"], "not_ready")
        self.assertTrue(any(item["code"] == "parse_failed" for item in quality["issues"]))

    def test_pdf_without_coordinates_is_usable_only_with_review(self) -> None:
        document = parse_document_ir(
            b"not a real pdf",
            file_name="sample.pdf",
            mime_type="application/pdf",
        )

        quality = evaluate_document_ir(document, source_content=b"not a real pdf")

        self.assertTrue(quality["hardFailure"])
        self.assertEqual(quality["readiness"], "not_ready")


if __name__ == "__main__":
    unittest.main()
