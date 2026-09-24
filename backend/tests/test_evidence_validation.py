from __future__ import annotations

import unittest

from backend.app.tender_matching.document_models import DocumentIR, DocumentSection, DocumentSegment
from backend.app.tender_matching.evidence_validation import validate_extraction_result
from backend.app.tender_matching.fact_models import (
    EvidenceFact,
    RequirementExtractionResult,
    RequirementFact,
)


class EvidenceValidationTests(unittest.TestCase):
    def _document(self) -> DocumentIR:
        segment = DocumentSegment(
            id="SEG-001",
            sourceLocator="page 3, block 4",
            text="平台必须支持统一身份认证。",
            contentType="pdf_text_block",
            order=0,
            page=3,
        )
        section = DocumentSection(
            id="SEC-TECH",
            sectionId="SEC-TECH",
            heading="技术要求",
            sectionPath=["第三章 技术要求"],
            segmentIds=["SEG-001"],
        )
        return DocumentIR(
            documentId="DOC-1",
            documentVersionId="DOC-1-V001",
            fileName="tender.pdf",
            originalFormat="pdf",
            text=segment.text,
            segments=[segment],
            units=[segment],
            sections=[section],
        )

    def _result(self, *, quote: str = "平台必须支持统一身份认证。") -> RequirementExtractionResult:
        return RequirementExtractionResult(
            documentVersionId="DOC-1-V001",
            requirements=[
                RequirementFact(
                    requirementId="REQ-001",
                    documentVersionId="DOC-1-V001",
                    sectionId="SEC-TECH",
                    sectionPath=["第三章 技术要求"],
                    category="technical",
                    rawText=quote,
                    normalizedRequirement=quote,
                    mandatory=True,
                    sourceSegmentIds=["SEG-001"],
                    evidenceIds=["EVD-001"],
                    sourceQuote=quote,
                )
            ],
            evidences=[
                EvidenceFact(
                    evidenceId="EVD-001",
                    documentVersionId="DOC-1-V001",
                    sourceType="tender_document",
                    sourceSegmentIds=["SEG-001"],
                    sectionId="SEC-TECH",
                    sectionPath=["第三章 技术要求"],
                    sourceLocator="page 3, block 4",
                    pageStart=3,
                    pageEnd=3,
                    sourceQuote=quote,
                )
            ],
        )

    def test_validates_quote_segment_section_and_page(self) -> None:
        result = validate_extraction_result(self._document(), self._result())

        self.assertTrue(result.valid)
        self.assertEqual(result.stats["verifiedEvidenceCount"], 1)
        self.assertEqual(result.evidences[0].verification_status, "verified")
        self.assertFalse(result.issues)

    def test_rejects_quote_that_is_not_supported_by_source(self) -> None:
        result = validate_extraction_result(
            self._document(),
            self._result(quote="平台支持完全不同的能力。"),
        )

        self.assertFalse(result.valid)
        self.assertIn("quote_not_supported", {issue.code for issue in result.issues})
        self.assertIn("requirement_invalid_evidence", {issue.code for issue in result.issues})
        self.assertEqual(result.evidences[0].verification_status, "invalid")
        self.assertTrue(result.requirements[0].needs_human_review)

    def test_rejects_unknown_evidence_and_mismatched_page(self) -> None:
        result_data = self._result().model_dump(mode="json", by_alias=True)
        result_data["requirements"][0]["evidenceIds"] = ["EVD-404"]
        result_data["evidences"][0]["pageStart"] = 9
        result_data["evidences"][0]["pageEnd"] = 9
        result = validate_extraction_result(
            self._document(),
            RequirementExtractionResult.model_validate(result_data),
        )

        self.assertFalse(result.valid)
        codes = {issue.code for issue in result.issues}
        self.assertIn("missing_evidence", codes)
        self.assertIn("page_mismatch", codes)
        self.assertEqual(result.evidences[0].verification_status, "invalid")

    def test_rejects_source_segment_that_does_not_exist(self) -> None:
        result_data = self._result().model_dump(mode="json", by_alias=True)
        result_data["evidences"][0]["sourceSegmentIds"] = ["SEG-404"]
        result = validate_extraction_result(
            self._document(),
            RequirementExtractionResult.model_validate(result_data),
        )

        self.assertFalse(result.valid)
        self.assertIn("missing_segment", {issue.code for issue in result.issues})


if __name__ == "__main__":
    unittest.main()
