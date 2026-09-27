from __future__ import annotations

import unittest

from backend.app.pi_agent.orchestrator import _persisted_evidence_blocks
from backend.app.pi_agent.prompts import _section_parse_bundle_view


class WritingSourceEvidenceTests(unittest.TestCase):
    def test_parse_blocks_are_persisted_with_locator_and_table_cells(self) -> None:
        blocks = _persisted_evidence_blocks(
            {
                "units": [
                    {
                        "id": "SEG-001",
                        "sourceLocator": "第3页第2段",
                        "contentType": "paragraph",
                        "order": 2,
                        "text": "技术响应文件应逐项说明实施方案。",
                    },
                    {
                        "id": "SEG-002",
                        "sourceLocator": "TABLE-001第1行",
                        "contentType": "table_row",
                        "order": 3,
                        "text": "序号 | 要求",
                        "cells": [{"text": "序号"}, {"text": "要求"}],
                    },
                ]
            }
        )

        self.assertEqual([item["id"] for item in blocks], ["SEG-001", "SEG-002"])
        self.assertEqual(blocks[0]["sourceLocator"], "第3页第2段")
        self.assertEqual(blocks[1]["cells"], ["序号", "要求"])

    def test_chapter_context_includes_only_matching_source_blocks(self) -> None:
        bundle = {
            "requirements": [
                {
                    "id": "req-tech",
                    "requirement": "提供实施方案",
                    "sourceText": "投标人应提交详细实施方案，并说明进度安排。",
                    "sourceLocator": "第3页第2段",
                },
                {
                    "id": "req-price",
                    "requirement": "提交报价",
                    "sourceText": "报价应包含税费。",
                    "sourceLocator": "第8页第1段",
                },
            ],
            "evaluationItems": [],
            "documentBlocks": {
                "evidenceBlocks": [
                    {
                        "id": "SEG-TECH",
                        "sourceLocator": "第3页第2段",
                        "contentType": "paragraph",
                        "text": "投标人应提交详细实施方案，并说明进度安排。",
                    },
                    {
                        "id": "SEG-PRICE",
                        "sourceLocator": "第8页第1段",
                        "contentType": "paragraph",
                        "text": "报价应包含税费。",
                    },
                ]
            },
            "documentStructure": {"sections": []},
            "bidFormatSpec": {"templates": []},
        }

        view = _section_parse_bundle_view(
            bundle,
            section={"sectionId": "tech", "title": "实施方案", "requirementIds": ["req-tech"]},
            coverage=[{"sectionId": "tech", "requirementId": "req-tech"}],
        )

        self.assertEqual(
            [item["blockId"] for item in view["sourceEvidence"]["blocks"]],
            ["SEG-TECH"],
        )
        self.assertEqual(
            [item["requirementId"] for item in view["sourceEvidence"]["requirementExcerpts"]],
            ["req-tech"],
        )
        self.assertNotIn("SEG-PRICE", str(view["sourceEvidence"]))
        self.assertNotIn("req-price", str(view))

    def test_chapter_context_does_not_invent_source_evidence(self) -> None:
        view = _section_parse_bundle_view(
            {
                "requirements": [{"id": "req-1", "requirement": "提供方案"}],
                "evaluationItems": [],
                "documentBlocks": {"evidenceBlocks": []},
                "documentStructure": {"sections": []},
                "bidFormatSpec": {"templates": []},
            },
            section={"sectionId": "tech", "title": "技术方案", "requirementIds": ["req-1"]},
            coverage=[{"sectionId": "tech", "requirementId": "req-1"}],
        )

        self.assertNotIn("sourceEvidence", view)

    def test_summary_is_not_mislabeled_as_an_original_excerpt(self) -> None:
        view = _section_parse_bundle_view(
            {
                "requirements": [
                    {
                        "id": "req-1",
                        "requirement": "提交详细实施方案",
                        "sourceText": "应提供完善、可落地的实施方案。",
                        "sourceLocator": "第3页第2段",
                    }
                ],
                "evaluationItems": [],
                "documentBlocks": {
                    "evidenceBlocks": [
                        {
                            "id": "SEG-1",
                            "sourceLocator": "第3页第2段",
                            "contentType": "paragraph",
                            "text": "投标人应提交详细实施方案，并说明进度安排。",
                        }
                    ]
                },
                "documentStructure": {"sections": []},
                "bidFormatSpec": {"templates": []},
            },
            section={"sectionId": "tech", "title": "技术方案", "requirementIds": ["req-1"]},
            coverage=[{"sectionId": "tech", "requirementId": "req-1"}],
        )

        self.assertEqual(view["sourceEvidence"]["requirementExcerpts"], [])
        self.assertEqual(view["sourceEvidence"]["blocks"][0]["blockId"], "SEG-1")


if __name__ == "__main__":
    unittest.main()
