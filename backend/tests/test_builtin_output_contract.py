from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.tender_matching.document_matching import evaluate_tender_document


class BuiltinOutputContractTests(unittest.TestCase):
    def test_builtin_extraction_keeps_fine_review_contract(self) -> None:
        extraction = {
            "requirements": [
                {
                    "id": "builtin-req-0001",
                    "category": "document",
                    "text": "投标文件须加盖单位公章。",
                    "rawText": "投标文件须加盖单位公章。",
                    "normalizedRequirement": "投标文件须加盖单位公章。",
                    "sourceSegmentId": "SEG-001",
                    "sourceSegmentIds": ["SEG-001"],
                    "mandatory": True,
                    "isRejection": False,
                    "confidence": 0.92,
                    "handlingMode": "checklist",
                    "displayGroup": "checklist",
                    "routeHints": {"ontologyClasses": [], "factKeys": [], "valid": False},
                }
            ],
            "extractionMethod": "llm+rule",
            "extractionProvider": "builtin-llm",
            "modelUsed": "custom-local:deepseek-v4-flash",
            "extractionWarning": "",
            "warnings": [],
            "scope": {},
            "packPath": "",
            "analysisPack": {"schemaVersion": "tender-analysis/1.0"},
            "stats": {},
        }

        with patch(
            "backend.app.tender_matching.document_matching.extract_tender_requirements",
            return_value=extraction,
        ):
            result = evaluate_tender_document(
                "投标文件须加盖单位公章。",
                [
                    {
                        "id": "SEG-001",
                        "sourceLocator": "第1页",
                        "text": "投标文件须加盖单位公章。",
                    }
                ],
                {},
                {"enabled": False, "extractionMode": "builtin"},
            )

        self.assertEqual(
            {
                "rows",
                "risks",
                "score",
                "decision",
                "recommendation",
                "clauseCount",
                "statusCounts",
                "classCounts",
                "summary",
                "hardBlockers",
                "rejectionRisks",
                "unresolvedMandatory",
                "pendingMaterialList",
                "analysisPack",
                "extraction",
            },
            set(result),
        )
        self.assertEqual(result["clauseCount"], 1)
        self.assertEqual(result["rows"][0]["status"], "待确认")
        self.assertTrue(
            {
                "id",
                "requirementId",
                "category",
                "requirementClass",
                "status",
                "reason",
                "matchedObjectIds",
                "matchedMaterialIds",
                "evidenceIds",
                "evidenceItems",
                "evidenceCoverageStatus",
                "risk",
                "riskLevel",
                "suggestion",
                "reviewStatus",
                "statusSource",
            }.issubset(result["rows"][0])
        )
        self.assertNotIn("agentRawResponse", result)
        self.assertNotIn("requirements", result)


if __name__ == "__main__":
    unittest.main()
