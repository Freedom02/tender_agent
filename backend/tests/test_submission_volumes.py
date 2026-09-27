from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from docx import Document

from backend.app.agent_runtime.contracts import build_parse_bundle, validate_parse_bundle
from backend.app.agent_runtime.submission_structure import (
    normalize_submission_structure,
    parse_submission_format_tree,
)
from backend.app.bid_writing.volumes import (
    arrange_volumes,
    plan_from_submission_structure,
    separate_volume_plans,
)
from backend.app.pi_agent.orchestrator import _template_relevant_text
from backend.app.pi_agent.prompts import outline_plan_prompt, template_detect_prompt
from backend.app.pi_agent.writing_orchestrator import PiWritingOrchestrator, PiWritingOrchestrationError
from backend.app.store import Store


def structure(mode: str = "separate") -> dict:
    return {
        "mode": mode,
        "confidence": "explicit",
        "sourceLocator": "第二章 投标文件的组成 第3款",
        "volumes": [
            {"volumeId": "commercial", "title": "商务文件", "order": 1, "sourceLocator": "第3款"},
            {"volumeId": "technical", "title": "技术文件", "order": 2, "sourceLocator": "第3款"},
        ],
    }


def sections() -> list[dict]:
    return [
        {"sectionId": "c-01", "title": "投标函", "kind": "commercial",
         "source": "template", "content": "商务正文", "status": "generated"},
        {"sectionId": "d-02", "title": "商务偏离表", "kind": "deviation",
         "source": "table", "content": "", "status": "generated"},
        {"sectionId": "t-01", "title": "技术方案", "kind": "technical",
         "source": "ai", "content": "技术正文", "status": "generated"},
        {"sectionId": "d-01", "title": "技术偏离表", "kind": "deviation",
         "source": "table", "content": "", "status": "generated"},
    ]


class SubmissionVolumeTests(unittest.TestCase):
    def test_parse_explicit_format_tree_keeps_volume_items_and_stops_at_template_body(self) -> None:
        text = """
第七篇 投标文件格式
一、经济文件
（一）开标一览表
（二）分项报价明细表
二、技术（质量）文件
（一）技术（质量）条款差异表
（二）其他技术（质量）资料
三、商务文件
（一）投标函（格式）
（二）商务条款差异表
四、其他
（一）中小企业声明函（如果有）
五、资格文件
（一）营业执照等主体资格证明
（二）法定代表人授权委托书（格式）
一、经济文件
（一）模板正文，不应再次成为目录
第八篇 其他说明
"""
        result = parse_submission_format_tree(text)
        self.assertEqual(result["confidence"], "explicit")
        self.assertEqual(
            [volume["title"] for volume in result["volumes"]],
            ["经济文件", "技术（质量）文件", "商务文件", "其他", "资格文件"],
        )
        self.assertEqual(
            [item["title"] for item in result["volumes"][0]["items"]],
            ["开标一览表", "分项报价明细表"],
        )
        self.assertTrue(result["volumes"][3]["items"][0]["conditional"])
        self.assertEqual(
            result["volumes"][4]["items"][1]["itemType"],
            "fixed_form",
        )

    def test_explicit_format_items_are_authoritative_over_generic_pi_nodes(self) -> None:
        structure_value = {
            "mode": "unspecified",
            "confidence": "explicit",
            "sourceLocator": "第七篇 投标文件格式",
            "volumes": [
                {
                    "volumeId": "commercial",
                    "title": "商务文件",
                    "order": 1,
                    "items": [
                        {
                            "formatItemId": "commercial-01",
                            "title": "投标函（格式）",
                            "itemType": "fixed_form",
                            "responseType": "fixed_form",
                            "templateId": "tpl-commercial-01",
                        },
                        {
                            "formatItemId": "commercial-02",
                            "title": "其他商务资料",
                            "itemType": "narrative",
                            "responseType": "chapter_text",
                        },
                    ],
                }
            ],
        }
        plan = plan_from_submission_structure(
            structure_value,
            {"rows": []},
            generated_plan=[
                {
                    "sectionId": "volume-1",
                    "title": "商务响应文件",
                    "level": 1,
                    "nodeType": "volume",
                },
                {
                    "sectionId": "generic-1",
                    "title": "商务偏离表",
                    "parentSectionId": "volume-1",
                    "level": 2,
                },
                {
                    "sectionId": "item-1",
                    "title": "其他商务资料",
                    "parentSectionId": "commercial-02",
                    "level": 2,
                },
                {
                    "sectionId": "child-1",
                    "title": "商务资料响应说明",
                    "parentSectionId": "item-1",
                    "level": 3,
                    "source": "ai",
                },
                {
                    "sectionId": "child-2",
                    "title": "实施步骤",
                    "parentSectionId": "child-1",
                    "level": 4,
                    "source": "ai",
                },
            ],
        )
        self.assertEqual(
            [item["title"] for item in plan if item["level"] == 1],
            ["商务文件"],
        )
        self.assertEqual(
            [item["title"] for item in plan if item["level"] == 2],
            ["投标函（格式）", "其他商务资料"],
        )
        self.assertNotIn("商务偏离表", [item["title"] for item in plan])
        by_id = {item["sectionId"]: item for item in plan}
        self.assertEqual(by_id["child-1"]["parentSectionId"], "commercial-02")
        self.assertEqual(by_id["child-2"]["parentSectionId"], "child-1")
        self.assertEqual(by_id["child-2"]["level"], 4)

    def test_parse_bundle_records_explicit_rule_and_old_bundle_defaults(self) -> None:
        bundle = build_parse_bundle(result={
            "bidFormatSpec": {"submissionStructure": structure()},
        })
        self.assertEqual(bundle["submissionStructure"]["mode"], "separate")
        old = dict(bundle)
        old.pop("submissionStructure")
        self.assertEqual(validate_parse_bundle(old)["submissionStructure"]["mode"], "unspecified")

    def test_unverified_separate_rule_is_never_used_for_split(self) -> None:
        rule = structure()
        rule["sourceLocator"] = ""
        self.assertEqual(normalize_submission_structure(rule)["mode"], "unspecified")
        self.assertEqual(separate_volume_plans(sections(), rule)[0], [])

    def test_long_template_context_keeps_composition_and_format(self) -> None:
        text = (
            "第一章 投标文件的组成：商务文件、技术文件分别编制。" +
            "普通正文" * 4000 +
            "第七章 投标文件格式：投标函、技术方案。"
        )
        focused = _template_relevant_text(text)
        self.assertIn("分别编制", focused)
        self.assertIn("投标函", focused)
        prompt = template_detect_prompt(
            document_text=focused, document_structure={},
            filename="招标文件.docx", workspace_id="w", project_id="p",
        )
        self.assertIn("submissionStructure", prompt)
        self.assertIn("sourceLocator", prompt)
        outline = outline_plan_prompt(
            parse_bundle={"submissionStructure": structure(), "plannedVolumes": structure()["volumes"]},
            existing_plan=[], workspace_id="w", project_id="p",
        )
        self.assertIn("plannedVolumes", outline)
        self.assertIn("技术文件", outline)

    def test_arrange_and_partition_only_declared_volumes(self) -> None:
        plan, warnings = arrange_volumes(sections(), structure())
        self.assertEqual(warnings, [])
        self.assertEqual([row["title"] for row in plan if row["level"] == 1], ["商务文件", "技术文件"])
        self.assertTrue(all(row["parentSectionId"] for row in plan if row["level"] == 2))
        volumes, reason = separate_volume_plans(plan, structure())
        self.assertEqual(reason, "")
        self.assertEqual(
            [[row["title"] for row in volume] for _, volume in volumes],
            [["商务文件", "投标函", "商务偏离表"], ["技术文件", "技术方案", "技术偏离表"]],
        )
        volumes, reason = separate_volume_plans(plan[1:], structure())
        self.assertFalse(volumes)
        self.assertIn("不一致", reason)

    def test_unassigned_chapters_do_not_split(self) -> None:
        plan, warnings = arrange_volumes([
            *sections(),
            {"sectionId": "a-01", "title": "其他附件", "kind": "attachment",
             "source": "attachment", "content": "附件"},
        ], structure())
        self.assertTrue(warnings)
        self.assertIn("待确认归属", [row["title"] for row in plan])
        self.assertFalse(separate_volume_plans(plan, structure())[0])

    def test_multi_requirement_flat_pi_outline_is_rejected(self) -> None:
        class FlatClient:
            def plan_volumes_agent(self, **_: object) -> dict:
                return {"volumes": [{"volumeId": "volume-main", "title": "投标响应文件"}]}

            def plan_outline_agent(self, **_: object) -> dict:
                return {"plan": [{"sectionId": "t-01", "title": "技术方案", "kind": "technical"}]}

        with self.assertRaisesRegex(PiWritingOrchestrationError, "多级章节"):
            PiWritingOrchestrator(FlatClient()).plan_outline(
                parse_bundle={"requirements": [{"id": "r1"}, {"id": "r2"}]},
                existing_plan=[], workspace_id="w", project_id="p",
            )

    def test_store_exports_separate_word_files_without_cross_volume_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root)
            plan, _ = arrange_volumes(sections(), structure())
            store.workspaces["ws-1"] = {
                "id": "ws-1",
                "projectId": "project-1",
                "bidDocument": {
                    "plan": plan,
                    "submissionStructure": structure(),
                    "deviationTables": {"technical": [], "commercial": []},
                    "status": "generated",
                    "mode": "open",
                },
            }
            store.catalog = [{"id": "project-1", "title": "测试项目"}]
            with patch.object(store, "get_parse_bundle", return_value={"templateFragments": []}):
                result = store.export_bid_document("ws-1")
            assert result is not None
            words = [entry for entry in result["files"] if entry["name"].endswith(".docx")]
            self.assertEqual(len(words), 2)
            for index, expected in enumerate(
                [("商务文件", "投标函", "商务偏离表"), ("技术文件", "技术方案", "技术偏离表")]
            ):
                path = root / words[index]["url"].lstrip("/")
                text = "\n".join(paragraph.text for paragraph in Document(path).paragraphs)
                self.assertTrue(all(title in text for title in expected))
                other = ("技术方案", "技术偏离表") if index == 0 else ("投标函", "商务偏离表")
                self.assertTrue(all(title not in text for title in other))

    def test_unspecified_rule_exports_single_word_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            plan, _ = arrange_volumes(sections(), {})
            store.workspaces["ws-1"] = {
                "id": "ws-1", "projectId": "project-1",
                "bidDocument": {
                    "plan": plan, "submissionStructure": normalize_submission_structure({}),
                    "deviationTables": {}, "status": "generated",
                },
            }
            with patch.object(store, "get_parse_bundle", return_value={}):
                result = store.export_bid_document("ws-1")
            assert result is not None
            self.assertEqual(sum(entry["name"].endswith(".docx") for entry in result["files"]), 1)
