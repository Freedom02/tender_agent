from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.bid_writing.template_reuse import attach_templates, fill_section_template
from backend.app.tender_matching.analysis_contracts import build_analysis_pack
from backend.app.tender_matching.builtin_pack import write_tender_pack


class AnalysisContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segments = [
            {
                "id": "SEG-001",
                "sourceLocator": "第1页",
                "page": 1,
                "text": "附件：投标函格式\n投标人名称：____",
            },
            {
                "id": "SEG-002",
                "sourceLocator": "第2页",
                "page": 2,
                "text": "投标截止时间：2026年10月12日 09:30",
            },
        ]
        self.sections = [
            {
                "sectionId": "SEC-001",
                "id": "SEC-001",
                "heading": "附件：投标函格式",
                "startLocator": "第1页",
                "sectionPath": ["第六章 附件", "附件：投标函格式"],
                "segmentIds": ["SEG-001"],
                "text": "附件：投标函格式\n投标人名称：____",
            },
            {
                "sectionId": "SEC-002",
                "id": "SEC-002",
                "heading": "投标文件提交截止时间",
                "startLocator": "第2页",
                "sectionPath": ["第一章 投标人须知"],
                "segmentIds": ["SEG-002"],
                "text": "投标截止时间：2026年10月12日 09:30",
            },
        ]

    def test_analysis_pack_keeps_metadata_small_and_builds_template_index(self) -> None:
        pack = build_analysis_pack(
            requirements=[],
            segments=self.segments,
            sections=self.sections,
            tables=[],
            source_file_id="FILE-001",
            source_file_hash="HASH-001",
            document_meta={
                "fileName": "tender.pdf",
                "mimeType": "application/pdf",
                "pageCount": 2,
                "parsed": {"rawText": "FULL DOCUMENT BODY"},
                "projectFields": {"projectName": "城市更新数字平台项目"},
            },
        )

        self.assertEqual(pack["source"]["sourceFileId"], "FILE-001")
        self.assertEqual(pack["source"]["sourceFileHash"], "HASH-001")
        self.assertNotIn("parsed", pack["source"]["document"])
        self.assertNotIn("rawText", pack["source"]["document"])
        self.assertTrue(pack["templateIndex"])
        self.assertEqual(pack["templateIndex"][0]["extractMode"], "on_demand")
        self.assertNotIn("FULL DOCUMENT BODY", json.dumps(pack, ensure_ascii=False))

    def test_template_index_rejects_regular_rules_and_keeps_format_templates(self) -> None:
        sections = [
            {
                "sectionId": "SEC-RULE-PRICE",
                "id": "SEC-RULE-PRICE",
                "heading": "（2）报价明细要求：",
                "startLocator": "第3页",
                "sectionPath": ["第一章 投标邀请", "（2）报价明细要求："],
                "segmentIds": ["SEG-003"],
            },
            {
                "sectionId": "SEC-RULE-GUARANTEE",
                "id": "SEC-RULE-GUARANTEE",
                "heading": "（6）有下列情形之一的，投标保证金将不予退还或通过投标保函进行索赔：",
                "startLocator": "第4页",
                "sectionPath": ["投标人须知", "投标保证金"],
                "segmentIds": ["SEG-004"],
            },
            {
                "sectionId": "SEC-FORM-BID",
                "id": "SEC-FORM-BID",
                "heading": "一、投标函",
                "startLocator": "第20页",
                "sectionPath": ["第七章 电子投标文件格式", "一、投标函"],
                "segmentIds": ["SEG-005"],
            },
            {
                "sectionId": "SEC-FORM-PRICE",
                "id": "SEC-FORM-PRICE",
                "heading": "二、投标（响应）报价明细表",
                "startLocator": "第21页",
                "sectionPath": ["第七章 电子投标文件格式", "二、投标（响应）报价明细表"],
                "segmentIds": ["SEG-006"],
            },
        ]
        segments = [
            {"id": "SEG-003", "sourceLocator": "第3页", "text": "（2）报价明细要求："},
            {"id": "SEG-004", "sourceLocator": "第4页", "text": "投标保证金将不予退还"},
            {"id": "SEG-005", "sourceLocator": "第20页", "text": "一、投标函"},
            {"id": "SEG-006", "sourceLocator": "第21页", "text": "二、投标（响应）报价明细表"},
        ]

        pack = build_analysis_pack(
            requirements=[],
            segments=segments,
            sections=sections,
            tables=[],
        )

        titles = [item["title"] for item in pack["templateIndex"]]
        self.assertIn("一、投标函", titles)
        self.assertIn("二、投标（响应）报价明细表", titles)
        self.assertNotIn("（2）报价明细要求：", titles)
        self.assertNotIn(
            "（6）有下列情形之一的，投标保证金将不予退还或通过投标保函进行索赔：",
            titles,
        )

    def test_local_scoring_rows_are_indexed_when_agent_has_no_criteria(self) -> None:
        segments = [
            {
                "id": "SEG-SCORE-001",
                "sourceLocator": "第12页",
                "page": 12,
                "sectionPath": ["第四章 评标办法", "技术商务评分标准"],
                "text": "评分项 | 分值 | 评分标准\n类似业绩 | 5分 | 提供2024年以来类似项目合同复印件，每提供一项得1分。",
            }
        ]

        pack = build_analysis_pack(
            requirements=[],
            segments=segments,
            sections=[],
            tables=[],
            agent_analysis={"evaluationCriteria": []},
        )

        self.assertEqual(len(pack["evaluationCriteria"]), 1)
        criterion = pack["evaluationCriteria"][0]
        self.assertEqual(criterion["maxScore"], "5")
        self.assertIn("类似项目合同复印件", criterion["scoringRule"])
        self.assertEqual(criterion["handlingMode"], "database_match")

    def test_template_planning_stores_reference_and_generation_reads_original_section(self) -> None:
        plan = [
            {
                "sectionId": "c-01",
                "title": "投标函",
                "source": "template",
                "kind": "commercial",
                "content": "",
            }
        ]
        indexed_templates = build_analysis_pack(
            requirements=[],
            segments=self.segments,
            sections=self.sections,
            tables=[],
            source_file_id="FILE-001",
        )["templateIndex"]

        attached = attach_templates(plan, self.sections, indexed_templates)

        self.assertEqual(attached[0]["source"], "copied_template")
        self.assertIn("templateRef", attached[0])
        self.assertNotIn("templateText", attached[0])

        filled = fill_section_template(
            attached[0],
            {
                "title": "城市更新数字平台项目",
                "parsed": {"projectName": "城市更新数字平台项目"},
            },
            {"organizationName": "示例科技有限公司"},
            {},
            sections=self.sections,
            segments=self.segments,
        )

        self.assertEqual(filled["status"], "generated")
        self.assertIn("投标人名称：示例科技有限公司", filled["content"])
        self.assertIn("附件：投标函格式", filled["content"])

    def test_builtin_tender_pack_accepts_analysis_without_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            analysis_pack = {
                "schemaVersion": "tender-analysis/1.0",
                "templateIndex": [
                    {
                        "templateId": "tpl-bid-letter-001",
                        "templateType": "bid_letter",
                        "title": "附件：投标函格式",
                        "source": {
                            "sectionId": "SEC-001",
                            "segmentIds": ["SEG-001"],
                            "pageStart": 1,
                        },
                    }
                ],
            }
            output = write_tender_pack(
                project_id="PROJECT-001",
                source_file_id="FILE-001",
                job_id="JOB-001",
                requirements=[],
                document_meta={
                    "fileName": "tender.pdf",
                    "parsed": {"rawText": "FULL DOCUMENT BODY"},
                },
                analysis_pack=analysis_pack,
                pack_dir=directory,
            )

            self.assertIsNotNone(output)
            payload = json.loads(Path(output).read_text(encoding="utf-8"))
            self.assertEqual(
                payload["analysisPack"]["templateIndex"][0]["templateId"],
                "tpl-bid-letter-001",
            )
            self.assertEqual(payload["requirements"], [])
            self.assertNotIn("FULL DOCUMENT BODY", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
