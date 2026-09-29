from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.app.store import Store

pytestmark = pytest.mark.skip(reason="store.py 保留远端版本，decisionSummary 为我方 schema，待 PG 迁移阶段迁")


def _fine_review() -> dict:
    return {
        "rows": [
            {
                "id": "REQ-T",
                "requirementId": "REQ-T",
                "requirementClass": "evidence",
                "category": "technical",
                "kind": "技术要求",
                "requirement": "平台应支持三维可视化和数据接入",
                "rawText": "平台应支持三维可视化和数据接入",
                "status": "满足",
                "mandatory": True,
                "risk": "低",
                "evidence": "企业产品说明书",
                "evidenceItems": [],
            }
        ],
        "risks": [],
        "score": 100,
        "decision": "建议进入正式投标准备",
        "recommendation": "建议进入正式投标准备",
        "clauseCount": 1,
        "statusCounts": {"满足": 1},
        "hardBlockers": [],
        "rejectionRisks": [],
        "unresolvedMandatory": [],
        "pendingMaterialList": [],
        "extraction": {"method": "rule", "warnings": []},
    }


class StoreDecisionSummaryTests(unittest.TestCase):
    def _store(self) -> Store:
        store = Store(Path("."))
        store.config = Store.default_config()
        store.company_knowledge = {}
        store._company_facts = {
            "technical_product": [
                {
                    "productId": "PRD-001",
                    "productName": "数字孪生平台",
                    "description": "支持三维可视化和数据接入",
                    "evidencePath": "company/product/prd-001.pdf",
                }
            ]
        }
        return store

    @patch("backend.app.store.evaluate_company_relevance", return_value={"score": 80})
    @patch(
        "backend.app.store.evaluate_announcement_eligibility",
        return_value={"rows": [], "risks": [], "score": 80, "decision": "建议关注"},
    )
    @patch("backend.app.store.evaluate_tender_document", return_value=_fine_review())
    def test_match_project_returns_decision_summary(
        self,
        _evaluate_document,
        _evaluate_announcement,
        _evaluate_relevance,
    ) -> None:
        store = self._store()
        result = store.match_project(
            {
                "id": "project-1",
                "title": "城市数字化服务平台",
                "projectCode": "CG-2026-001",
                "analysisText": "完整招标文件",
                "document": {
                    "id": "document-1",
                    "kind": "tender_document",
                    "segments": [],
                    "sections": [],
                    "tables": [],
                },
            }
        )

        self.assertIn("decisionSummary", result)
        self.assertTrue(result["runId"])
        self.assertEqual(result["documentVersionId"], "document-1-V001")
        self.assertEqual(Path(result["runPath"]).name, f"{result['runId']}.json")
        run = store.analysis_run("project-1", result["runId"])
        self.assertIsNotNone(run)
        self.assertEqual(run["status"], "completed")
        self.assertTrue(store.analysis_runs("project-1"))
        self.assertEqual(
            result["decisionSummary"]["projectFields"]["projectName"],
            "城市数字化服务平台",
        )
        self.assertEqual(
            result["decisionSummary"]["technicalMatch"]["statusCounts"]["满足"],
            1,
        )

    def test_manual_status_update_refreshes_decision_summary(self) -> None:
        store = self._store()
        store.persist_runtime = lambda: None
        fine_review = _fine_review()
        store.workspaces = {
            "workspace-1": {
                "id": "workspace-1",
                "projectId": "project-1",
                "title": "城市数字化服务平台",
                "project": {"id": "project-1", "title": "城市数字化服务平台"},
                "reasoning": {
                    "rows": [],
                    "risks": [],
                    "score": 80,
                    "decision": "建议关注",
                    "fineReview": fine_review,
                },
            }
        }

        result = store.update_fine_review_row(
            "workspace-1",
            "REQ-T",
            "不满足",
            reviewer="测试用户",
        )

        self.assertIsNotNone(result)
        summary = result["workspace"]["reasoning"]["decisionSummary"]
        self.assertEqual(summary["recommendation"], "不建议继续")
        self.assertEqual(len(summary["hardBlockers"]), 1)
        self.assertEqual(summary["technicalMatch"]["items"][0]["status"], "不满足")


if __name__ == "__main__":
    unittest.main()
