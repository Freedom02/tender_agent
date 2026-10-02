from __future__ import annotations

import unittest

from backend.app.pi_agent.prompts import (
    _planning_parse_bundle_view,
    outline_plan_prompt,
)


class OutlineContextTests(unittest.TestCase):
    def test_outline_context_prioritizes_tender_file_preparation_instructions(self) -> None:
        bundle = {
            "documentStructure": {
                "sections": [
                    {
                        "sectionId": "format",
                        "title": "第七章 电子投标文件格式",
                        "sourceLocator": "第七章",
                        "text": "投标文件应包含投标函、技术响应方案和售后服务承诺，按此顺序编制。",
                    },
                    {
                        "sectionId": "qualification",
                        "title": "投标人资格要求",
                        "sourceLocator": "第二章",
                        "text": "投标人须具有有效营业执照。",
                    },
                ]
            },
            "requirements": [
                {
                    "id": "req-format",
                    "category": "document",
                    "requirement": "投标文件应包含技术响应方案。",
                    "sourceSection": "电子投标文件格式",
                    "sourceLocator": "第七章 第2项",
                },
                {
                    "id": "req-qualification",
                    "category": "qualification",
                    "requirement": "投标人须具有有效营业执照。",
                    "sourceSection": "投标人资格要求",
                    "sourceLocator": "第二章 第1项",
                },
            ],
            "evaluationItems": [],
            "bidFormatSpec": {},
        }

        view = _planning_parse_bundle_view(bundle, purpose="outline")
        context = view["outlineInstructionContext"]
        prompt = outline_plan_prompt(
            parse_bundle=bundle,
            existing_plan=[],
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertEqual(
            [item["sectionId"] for item in context["sourceSections"]],
            ["format"],
        )
        self.assertEqual(
            [item["id"] for item in context["requirements"]],
            ["req-format"],
        )
        self.assertIn("投标文件应包含投标函", prompt)
        # M2′ 起规则由“不得新增章节”改为“取证后必须带依据地新增章节”，
        # 但仍然禁止为单条要求机械造节点。
        self.assertIn("不得为单条要求各造一个节点", prompt)
        self.assertIn("derivedBasis", prompt)
        self.assertNotIn(
            "req-qualification",
            [item["id"] for item in context["requirements"]],
        )

    def test_outline_context_uses_project_sections_with_heading_and_locator_fields(self) -> None:
        bundle = {
            "outlineSourceSections": [
                {
                    "sectionId": "prepare",
                    "heading": "投标文件编制要求",
                    "startLocator": "第三章 17.2",
                    "text": "投标人应按照本章目录逐项提交技术响应文件。",
                },
                {
                    "sectionId": "noise",
                    "heading": "合同条款",
                    "startLocator": "第六章",
                    "text": "合同履行期限为一年。",
                },
            ],
            "documentStructure": {"sections": []},
            "requirements": [],
            "evaluationItems": [],
            "bidFormatSpec": {},
        }

        context = _planning_parse_bundle_view(bundle)["outlineInstructionContext"]

        self.assertEqual(context["sourceSections"][0]["sectionId"], "prepare")
        self.assertEqual(context["sourceSections"][0]["sourceLocator"], "第三章 17.2")
        self.assertIn("按照本章目录逐项提交", context["sourceSections"][0]["text"])
        self.assertEqual(len(context["sourceSections"]), 1)


if __name__ == "__main__":
    unittest.main()
