from __future__ import annotations

import unittest
from datetime import date

from backend.app.tender_matching.decision_summary import (
    build_decision_summary,
    build_workspace_decision_summary,
)
from backend.app.tender_matching.technical_service_matching import (
    match_technical_service_requirements,
)


class DecisionSummaryTests(unittest.TestCase):
    def _technical(self, text: str) -> object:
        return match_technical_service_requirements(
            [{"requirementId": "REQ-T", "category": "technical", "rawText": text}],
            {
                "products": [
                    {
                        "productId": "PRD-001",
                        "productName": "数字孪生平台",
                        "description": text,
                        "evidencePath": "company/prd-001.pdf",
                    }
                ],
                "parameters": [],
                "capabilities": [],
            },
        )

    def test_first_screen_prioritizes_hard_blocker(self) -> None:
        technical = match_technical_service_requirements(
            [{"requirementId": "REQ-T", "category": "technical", "rawText": "服务器配置不低于32GB内存"}],
            {
                "products": [
                    {
                        "productId": "PRD-001",
                        "productName": "应用服务器",
                        "description": "服务器配置16GB内存",
                        "evidencePath": "company/server-001.pdf",
                    }
                ],
                "parameters": [],
                "capabilities": [],
            },
        )
        summary = build_decision_summary(
            project_fields=[{"fieldName": "projectName", "value": "测试项目"}],
            requirements=[
                {
                    "requirementId": "REQ-Q",
                    "category": "qualification",
                    "rawText": "投标人必须具备相关资质",
                    "mandatory": True,
                    "needsHumanReview": True,
                }
            ],
            technical_match=technical,
            today=date(2026, 9, 19),
        )

        self.assertEqual(summary.recommendation, "不建议继续")
        self.assertEqual(summary.project_fields["projectName"], "测试项目")
        self.assertEqual(len(summary.hard_blockers), 1)
        self.assertGreaterEqual(summary.needs_human_review_count, 1)

    def test_material_and_schedule_risks_are_compact(self) -> None:
        technical = match_technical_service_requirements(
            [{"requirementId": "REQ-T", "category": "service_plan", "rawText": "提供项目实施和培训服务"}],
            {"capabilities": [{"id": "CAP-1", "name": "项目实施和培训"}]},
        )
        summary = build_decision_summary(
            project_fields={
                "fieldName": "deadline",
                "value": "2026年9月24日",
            },
            requirements=[
                {
                    "requirementId": "REQ-C",
                    "category": "commercial",
                    "rawText": "交货期为合同签订后10日内",
                    "needsHumanReview": True,
                }
            ],
            technical_match=technical,
            today=date(2026, 9, 19),
        )

        self.assertEqual(summary.recommendation, "谨慎继续")
        self.assertTrue(summary.material_gaps)
        self.assertTrue(summary.schedule_risks)
        self.assertTrue(summary.commercial_risks)

    def test_no_technical_requirements_is_not_presented_as_continue(self) -> None:
        technical = match_technical_service_requirements(
            [{"requirementId": "REQ-Q", "category": "qualification", "rawText": "提供营业执照"}],
            {},
        )
        summary = build_decision_summary(technical_match=technical)

        self.assertEqual(summary.recommendation, "待澄清")
        self.assertIn("尚未识别", summary.headline)

    def test_human_review_count_excludes_writing_and_format_tasks(self) -> None:
        technical = match_technical_service_requirements(
            [{"requirementId": "REQ-T", "category": "technical", "rawText": "支持专用协议接入"}],
            {},
        )
        summary = build_decision_summary(
            requirements=[
                {
                    "requirementId": "REQ-Q",
                    "category": "qualification",
                    "rawText": "提供营业执照",
                    "status": "待补材料",
                },
                {
                    "requirementId": "REQ-C",
                    "category": "commercial",
                    "rawText": "付款方式需要确认",
                    "status": "待填写",
                    "mandatory": True,
                },
                {
                    "requirementId": "REQ-W",
                    "category": "writing",
                    "rawText": "编制实施方案",
                    "status": "待编制",
                    "needsHumanReview": True,
                },
                {
                    "requirementId": "REQ-F",
                    "category": "document_format",
                    "rawText": "逐页签章",
                    "status": "待确认",
                    "needsHumanReview": True,
                },
            ],
            technical_match=technical,
        )

        self.assertEqual(summary.needs_human_review_count, 2)
        self.assertEqual(len(summary.qualification_risks), 1)
        self.assertEqual(len(summary.commercial_risks), 2)

    def test_workspace_projection_uses_current_rows_and_project_fields(self) -> None:
        summary = build_workspace_decision_summary(
            project={
                "title": "城市数字化服务平台",
                "projectCode": "CG-2026-001",
                "bidDeadline": "2026年9月24日 09:30",
            },
            fine_review={
                "rows": [
                    {
                        "id": "REQ-T",
                        "requirementId": "REQ-T",
                        "category": "technical",
                        "requirement": "平台应支持三维可视化和数据接入",
                        "status": "满足",
                    },
                    {
                        "id": "REQ-Q-SAFE",
                        "requirementId": "REQ-Q-SAFE",
                        "category": "qualification",
                        "requirement": "提供营业执照",
                        "status": "满足",
                    },
                    {
                        "id": "REQ-Q",
                        "requirementId": "REQ-Q",
                        "category": "qualification",
                        "requirement": "提供近三年同类项目业绩",
                        "status": "待补材料",
                        "mandatory": True,
                    },
                ]
            },
            company_knowledge={
                "products": [
                    {
                        "productId": "PRD-001",
                        "productName": "数字孪生平台",
                        "description": "支持三维可视化和数据接入",
                        "evidencePath": "company/product/prd-001.pdf",
                    }
                ],
                "parameters": [],
                "capabilities": [],
                "projects": [],
            },
            today=date(2026, 9, 19),
        )
        payload = summary.model_dump(by_alias=True)

        self.assertEqual(payload["projectFields"]["projectName"], "城市数字化服务平台")
        self.assertEqual(payload["projectFields"]["projectCode"], "CG-2026-001")
        self.assertEqual(payload["technicalMatch"]["statusCounts"]["满足"], 1)
        self.assertEqual(len(payload["qualificationRisks"]), 1)
        self.assertEqual(payload["qualificationRisks"][0]["requirementId"], "REQ-Q")
        self.assertTrue(payload["scheduleRisks"])

    def test_manual_satisfaction_without_evidence_remains_material_gap(self) -> None:
        summary = build_workspace_decision_summary(
            project={"title": "测试项目"},
            fine_review={
                "rows": [
                    {
                        "id": "REQ-T",
                        "requirementId": "REQ-T",
                        "category": "technical",
                        "requirement": "平台应支持专用协议接入",
                        "status": "满足",
                        "manualStatus": "满足",
                        "statusSource": "人工审核",
                        "mandatory": True,
                    }
                ]
            },
            company_knowledge={},
        )

        self.assertEqual(summary.technical_match.items[0].status, "待补材料")
        self.assertEqual(summary.recommendation, "谨慎继续")
        self.assertTrue(summary.material_gaps)
        self.assertTrue(summary.warnings)


if __name__ == "__main__":
    unittest.main()
