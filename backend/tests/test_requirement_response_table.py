from __future__ import annotations

import unittest

from backend.app.bid_writing.response_table import (
    bind_response_table_requirements,
    build_response_table_result,
    prepare_response_table_inputs,
    replace_first_markdown_table,
    technical_service_requirements,
)
from backend.app.pi_agent.prompts import requirement_response_table_prompt


class RequirementResponseTableTests(unittest.TestCase):
    def test_target_oriented_domains_bind_technical_and_service_rows(self) -> None:
        fine_review = {
            "rows": [
                {
                    "requirementId": "req-technical",
                    "targetModule": "technical",
                    "matchDomain": "technical",
                    "requirement": "平台支持三维场景加载",
                },
                {
                    "requirementId": "req-service",
                    "matchDomain": "service_plan",
                    "requirement": "提供现场培训",
                },
                {
                    "requirementId": "req-qualification",
                    "targetModule": "qualification",
                    "requirement": "提供资质证书",
                },
                {
                    "requirementId": "req-rejection",
                    "matchDomain": "rejection",
                    "requirement": "不得出现影响公平竞争的行为",
                },
            ]
        }
        section = {
            "title": "技术和服务要求响应表",
            "responseType": "table",
            "requirementIds": [],
        }

        selected = technical_service_requirements(fine_review)
        bound = bind_response_table_requirements([section], fine_review)

        self.assertEqual(
            [row["requirementId"] for row in selected],
            ["req-technical", "req-service"],
        )
        self.assertEqual(
            bound[0]["requirementIds"],
            ["req-technical", "req-service"],
        )

    def test_inputs_reuse_only_row_matched_m3_materials(self) -> None:
        section = {
            "title": "技术和服务要求响应表",
            "responseType": "table",
            "requirementIds": ["req-tech", "req-service", "req-qualification"],
        }
        fine_review = {
            "rows": [
                {
                    "requirementId": "req-tech",
                    "category": "technical",
                    "requirement": "平台应支持三维可视化",
                    "status": "满足",
                    "matchedObjectIds": ["product-1"],
                },
                {
                    "requirementId": "req-service",
                    "category": "service_plan",
                    "requirement": "提供现场培训服务",
                    "status": "待补材料",
                    "matchedObjectIds": [],
                },
                {
                    "requirementId": "req-qualification",
                    "category": "qualification",
                    "requirement": "提供资质证书",
                    "status": "满足",
                    "matchedObjectIds": ["product-1"],
                },
            ]
        }
        knowledge = {
            "products": [
                {
                    "id": "product-1",
                    "name": "数字孪生平台",
                    "description": "支持三维可视化和数据接入",
                    "evidencePath": "m3/products/platform.pdf",
                    "factKey": "technical_product",
                },
                {
                    "id": "product-2",
                    "name": "不相关产品",
                    "description": "不得被注入当前条款",
                },
            ]
        }

        rows = prepare_response_table_inputs(section, fine_review, knowledge)

        self.assertEqual([row["requirementId"] for row in rows], ["req-tech", "req-service"])
        self.assertEqual([item["materialId"] for item in rows[0]["materials"]], ["product-1"])
        self.assertEqual(rows[0]["sourceRefs"][0]["evidencePath"], "m3/products/platform.pdf")
        self.assertEqual(rows[1]["materials"], [])

    def test_inputs_resolve_valid_ids_from_target_alignment(self) -> None:
        rows = prepare_response_table_inputs(
            {"title": "技术要求响应表", "responseType": "table"},
            {
                "rows": [
                    {
                        "requirementId": "req-technical",
                        "targetModule": "technical",
                        "requirement": "平台支持三维场景加载",
                        "status": "满足",
                        "technicalAlignment": {
                            "matchedUnits": [
                                {
                                    "productId": "product-1",
                                    "matchedParams": ["parameter-1"],
                                }
                            ]
                        },
                    }
                ]
            },
            {
                "products": [
                    {
                        "id": "product-1",
                        "name": "数字孪生平台",
                        "description": "支持三维场景加载",
                    }
                ],
                "capabilities": [
                    {
                        "id": "parameter-1",
                        "name": "三维场景加载参数",
                        "description": "支持三维场景加载",
                    }
                ],
            },
        )

        self.assertEqual(
            [material["materialId"] for material in rows[0]["materials"]],
            ["product-1", "parameter-1"],
        )

    def test_result_marks_missing_material_and_traces_matched_material(self) -> None:
        result = build_response_table_result(
            {"title": "技术和服务要求响应表"},
            [
                {
                    "requirementId": "req-tech",
                    "requirement": "平台应支持三维可视化",
                    "matchStatus": "满足",
                    "materials": [{"name": "数字孪生平台"}],
                    "sourceRefs": [
                        {
                            "factKey": "technical_product",
                            "factId": "product-1",
                            "name": "数字孪生平台",
                            "evidencePath": "m3/products/platform.pdf",
                        }
                    ],
                },
                {
                    "requirementId": "req-service",
                    "requirement": "提供现场培训服务",
                    "matchStatus": "待补材料",
                    "materials": [],
                    "sourceRefs": [],
                },
            ],
            {"req-tech": "我方平台支持三维可视化。"},
        )

        self.assertEqual(result["status"], "generated")
        self.assertEqual(len(result["requirementResponseRows"]), 2)
        self.assertEqual(result["requirementResponseRows"][0]["deviation"], "无偏离")
        self.assertIn("未在 M3 中", result["requirementResponseRows"][1]["response"])
        self.assertEqual(len(result["sourceRefs"]), 1)
        self.assertIn("平台支持三维可视化", result["content"])
        self.assertTrue(result["blanksToFill"])

    def test_unverified_match_is_not_marked_no_deviation(self) -> None:
        result = build_response_table_result(
            {"title": "技术要求响应表"},
            [
                {
                    "requirementId": "req-unverified",
                    "requirement": "平台支持三维场景加载",
                    "matchStatus": "满足",
                    "materials": [],
                }
            ],
        )

        row = result["requirementResponseRows"][0]
        self.assertEqual(row["deviation"], "待人工确认")
        self.assertTrue(row["needsReview"])
        self.assertIn("未在 M3 中", row["response"])

    def test_table_replacement_keeps_surrounding_template_text(self) -> None:
        content = (
            "# 技术响应表\n\n填表说明\n\n"
            "| 招标要求 | 投标应答 |\n| --- | --- |\n| 原要求 | |\n\n"
            "投标人盖章："
        )

        updated = replace_first_markdown_table(
            content,
            "| 招标要求 | 投标应答 |\n| --- | --- |\n| 新要求 | 已响应 |",
        )

        self.assertIn("填表说明", updated)
        self.assertIn("新要求", updated)
        self.assertIn("投标人盖章：", updated)
        self.assertNotIn("原要求", updated)

    def test_pi_prompt_is_limited_to_existing_matches_and_m3_rows(self) -> None:
        prompt = requirement_response_table_prompt(
            section={"sectionId": "tech-response", "title": "技术和服务要求响应表"},
            requirements=[
                {
                    "requirementId": "req-1",
                    "requirement": "性能指标不少于10项",
                    "materials": [
                        {
                            "materialId": "m3-1",
                            "name": "产品参数表",
                            "description": "支持12项指标",
                        }
                    ],
                }
            ],
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertIn("不得重新解析招标文件", prompt)
        self.assertIn("m3-1", prompt)
        self.assertIn("requirementId", prompt)


if __name__ == "__main__":
    unittest.main()
