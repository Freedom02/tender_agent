from __future__ import annotations

import unittest

from backend.app.tender_matching.company_facts import build_company_ledger
from backend.app.tender_matching.document_matching import evaluate_tender_document
from backend.app.tender_matching.requirement_scope import is_form_title, is_placeholder_row


class RequirementScopeTests(unittest.TestCase):
    def test_form_titles_and_multi_field_placeholder_rows_are_recognized(self) -> None:
        placeholder = "国别：______ 品牌：______ 规格型号：______"
        form_title = "②技术和服务要求响应表"

        self.assertTrue(is_placeholder_row(placeholder))
        self.assertTrue(is_form_title(form_title))
        self.assertFalse(is_form_title("平台应支持三维场景加载。"))

    def test_service_plan_is_kept_out_of_fine_review_rows(self) -> None:
        company_knowledge = build_company_ledger(
            {
                "organization": {
                    "organizationId": "org-001",
                    "capabilities": "项目实施方案与培训方案交付",
                },
                "technical_product": [
                    {
                        "productId": "PRD-THINGJS",
                        "productName": "ThingJS 平台",
                        "evidencePath": "libraries/company/证明材料/技术产品/1/",
                        "status": "待核验",
                    }
                ],
            }
        )
        result = evaluate_tender_document(
            "",
            [
                {
                    "id": "SEG-COMPLIANCE",
                    "sourceLocator": "第12页",
                    "text": "投标文件须实质性响应技术参数，并提供 ThingJS 平台检验证明。",
                },
                {
                    "id": "SEG-SERVICE-PLAN",
                    "sourceLocator": "第13页",
                    "text": "投标人须提供项目实施方案与培训方案交付说明。",
                },
            ],
            company_knowledge,
            {"enabled": False},
        )

        self.assertTrue(result["rows"])
        self.assertTrue(
            all("category" not in row for row in result["rows"])
        )
        self.assertTrue(
            all(
                row["targetModule"] in {"technical", "qualification", "rejection"}
                for row in result["rows"]
            )
        )
        source_ids = {
            source_id
            for row in result["rows"]
            for source_id in row.get("sourceSegmentIds", [])
        }
        self.assertIn("SEG-COMPLIANCE", source_ids)
        self.assertNotIn("SEG-SERVICE-PLAN", source_ids)


if __name__ == "__main__":
    unittest.main()
