from __future__ import annotations

import unittest

from backend.app.tender_matching.company_facts import build_company_ledger
from backend.app.tender_matching.fact_models import RequirementFact
from backend.app.tender_matching.technical_service_matching import (
    match_technical_service_requirements,
)


class TechnicalServiceMatchingTests(unittest.TestCase):
    def _ledger(self, *, evidence: bool = True) -> dict:
        return build_company_ledger(
            {
                "organization": {
                    "organizationId": "ORG-001",
                    "capabilities": "数字孪生平台建设、项目实施和培训",
                },
                "technical_product": [
                    {
                        "productId": "PRD-001",
                        "productName": "数字孪生平台",
                        "description": "支持三维可视化、数据接入和项目实施",
                        **({"evidencePath": "company/product/prd-001.pdf"} if evidence else {}),
                    }
                ],
                "technical_parameter": [
                    {
                        "parameterId": "PAR-001",
                        "productId": "PRD-001",
                        "description": "支持三维可视化和数据接入",
                        **({"evidencePath": "company/parameter/par-001.pdf"} if evidence else {}),
                    }
                ],
            }
        )

    def test_matches_requirement_with_company_evidence(self) -> None:
        result = match_technical_service_requirements(
            [
                RequirementFact(
                    requirementId="REQ-001",
                    documentVersionId="DOC-001",
                    category="technical",
                    rawText="平台应支持三维可视化和数据接入",
                )
            ],
            self._ledger(),
        )

        self.assertEqual(result.items[0].status, "满足")
        self.assertEqual(set(result.items[0].matched_fact_ids), {"PRD-001", "PAR-001"})
        self.assertTrue(result.items[0].evidence_items)
        self.assertEqual(result.recommendation, "建议继续")

    def test_missing_company_evidence_is_material_gap(self) -> None:
        result = match_technical_service_requirements(
            [
                {
                    "requirementId": "REQ-002",
                    "category": "service_plan",
                    "rawText": "投标人应提供项目实施和培训服务",
                }
            ],
            self._ledger(evidence=False),
        )

        self.assertEqual(result.items[0].status, "待补材料")
        self.assertIn("capability-2", result.items[0].matched_fact_ids)
        self.assertTrue(result.items[0].matched_fact_ids)
        self.assertTrue(result.items[0].needs_human_review)
        self.assertEqual(result.recommendation, "谨慎继续")

    def test_missing_hardware_fact_is_material_gap(self) -> None:
        result = match_technical_service_requirements(
            [
                {
                    "requirementId": "REQ-003",
                    "category": "technical",
                    "rawText": "服务器配置不低于32GB内存",
                }
            ],
            self._ledger(),
        )

        self.assertEqual(result.items[0].status, "待补材料")
        self.assertFalse(result.items[0].hard_blocker)
        self.assertEqual(result.recommendation, "谨慎继续")
        self.assertEqual(len(result.hard_blockers), 0)

    def test_explicit_hardware_metric_mismatch_is_blocker(self) -> None:
        ledger = self._ledger()
        ledger["products"].append(
            {
                "productId": "PRD-SERVER-001",
                "productName": "应用服务器",
                "description": "服务器配置16GB内存",
                "evidencePath": "company/product/server.pdf",
            }
        )
        result = match_technical_service_requirements(
            [
                {
                    "requirementId": "REQ-003-HARD",
                    "category": "technical",
                    "rawText": "服务器配置不低于32GB内存",
                }
            ],
            ledger,
        )

        self.assertEqual(result.items[0].status, "不满足")
        self.assertTrue(result.items[0].hard_blocker)
        self.assertEqual(result.recommendation, "不建议继续")
        self.assertEqual(len(result.hard_blockers), 1)

    def test_ignores_non_technical_requirements(self) -> None:
        result = match_technical_service_requirements(
            [{"requirementId": "REQ-004", "category": "qualification", "rawText": "提供营业执照"}],
            self._ledger(),
        )

        self.assertEqual(result.items, [])
        self.assertEqual(result.recommendation, "待澄清")


if __name__ == "__main__":
    unittest.main()
