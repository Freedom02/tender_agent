from __future__ import annotations

import unittest

from backend.app.tender_matching.document_structure import (
    build_section_tree,
    enrich_document_structure,
)


class DocumentStructureTests(unittest.TestCase):
    def test_builds_hierarchical_tender_sections_and_classifies_types(self) -> None:
        segments = [
            {"id": "SEG-001", "contentType": "paragraph", "sourceLocator": "第1段", "text": "目录", "style": {}},
            {
                "id": "SEG-002",
                "contentType": "paragraph",
                "sourceLocator": "第2段",
                "text": "第一篇 投标邀请书",
                "style": {},
            },
            {"id": "SEG-003", "contentType": "paragraph", "sourceLocator": "第3段", "text": "一、招标项目内容", "style": {}},
            {"id": "SEG-004", "contentType": "paragraph", "sourceLocator": "第4段", "text": "项目说明和采购范围。", "style": {}},
            {"id": "SEG-005", "contentType": "paragraph", "sourceLocator": "第5段", "text": "第二篇 投标人须知", "style": {}},
            {"id": "SEG-006", "contentType": "paragraph", "sourceLocator": "第6段", "text": "一、投标人", "style": {}},
            {"id": "SEG-007", "contentType": "paragraph", "sourceLocator": "第7段", "text": "第三篇 项目技术需求", "style": {}},
            {"id": "SEG-008", "contentType": "table_row", "sourceLocator": "TABLE-001第1行", "text": "一、资格性审查", "cells": [{"text": "一、资格性审查"}], "style": {}},
            {"id": "SEG-009", "contentType": "table_row", "sourceLocator": "TABLE-001第2行", "text": "序号 | 检查因素 | 检查内容", "cells": [{"text": "序号"}, {"text": "检查因素"}, {"text": "检查内容"}], "style": {}},
            {"id": "SEG-010", "contentType": "paragraph", "sourceLocator": "第10段", "text": "第五篇 投标文件格式", "style": {}},
        ]

        result = build_section_tree(segments)
        sections = result["sections"]

        self.assertEqual(result["headingCount"], 7)
        self.assertEqual(sections[0]["heading"], "前置内容")
        self.assertEqual(
            [(section["heading"], section["sectionType"], section["level"]) for section in sections[1:]],
            [
                ("第一篇 投标邀请书", "invitation", 1),
                ("一、招标项目内容", "basic", 2),
                ("第二篇 投标人须知", "instructions", 1),
                ("一、投标人", "instructions", 2),
                ("第三篇 项目技术需求", "technical", 1),
                ("一、资格性审查", "qualification", 2),
                ("第五篇 投标文件格式", "document_format", 1),
            ],
        )
        qualification = next(section for section in sections if section["heading"] == "一、资格性审查")
        self.assertEqual(qualification["parentId"], "SEC-0005")
        self.assertEqual(qualification["startLocator"], "TABLE-001第1行")
        self.assertIn("SEG-009", qualification["segmentIds"])
        self.assertEqual(
            qualification["sectionPath"],
            ["第三篇 项目技术需求", "一、资格性审查"],
        )
        self.assertFalse(any(section["heading"].startswith("序号") for section in sections))

    def test_enrich_document_structure_preserves_parser_payload_and_metadata(self) -> None:
        parsed = {
            "text": "项目名称：平台项目",
            "segments": [
                {
                    "id": "SEG-001",
                    "contentType": "paragraph",
                    "sourceLocator": "第1段",
                    "text": "项目名称：平台项目",
                    "style": {},
                }
            ],
            "metadata": {"paragraphCount": 1},
            "warnings": [],
        }

        enriched = enrich_document_structure(parsed)

        self.assertEqual(enriched["text"], parsed["text"])
        self.assertEqual(enriched["metadata"]["paragraphCount"], 1)
        self.assertEqual(enriched["metadata"]["sectionCount"], 1)
        self.assertEqual(enriched["sections"][0]["heading"], "未分类内容")
        self.assertIn("未识别到明确章节标题", enriched["structureWarnings"][0])

    def test_directory_page_entries_are_not_treated_as_body_sections(self) -> None:
        result = build_section_tree(
            [
                {
                    "id": "SEG-001",
                    "contentType": "paragraph",
                    "sourceLocator": "第1段",
                    "text": "第一篇 投标邀请书 - 3 -",
                    "style": {},
                },
                {
                    "id": "SEG-002",
                    "contentType": "paragraph",
                    "sourceLocator": "第2段",
                    "text": "第一篇 投标邀请书",
                    "style": {},
                },
            ]
        )

        self.assertEqual(result["headingCount"], 1)
        self.assertEqual(result["sections"][0]["heading"], "前置内容")
        self.assertEqual(result["sections"][1]["heading"], "第一篇 投标邀请书")


if __name__ == "__main__":
    unittest.main()
