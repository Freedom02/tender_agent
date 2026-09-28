from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.bid_writing.plan_tree import normalize_plan_tree
from backend.app.bid_writing.planning import plan_bid_document, plan_from_eval
from backend.app.bid_writing.response_table import bind_response_table_requirements
from backend.app.store import Store


class BidPlanTreeTests(unittest.TestCase):
    def test_response_table_binds_only_technical_and_service_requirements(self) -> None:
        plan = [
            {
                "sectionId": "technical-response",
                "title": "技术和服务要求响应表",
                "source": "template",
                "kind": "technical",
                "responseType": "table",
                "requirementIds": [],
            },
            {
                "sectionId": "technical-deviation",
                "title": "技术规格偏离表",
                "source": "table",
                "kind": "deviation",
                "responseType": "deviation",
                "requirementIds": [],
            },
        ]
        fine_review = {
            "rows": [
                {"id": "req-tech", "category": "technical", "requirement": "处理能力不低于100项"},
                {"id": "req-service", "category": "service_plan", "requirement": "提供现场培训服务"},
                {"id": "req-qualification", "category": "qualification", "requirement": "提供资质证书"},
                {"id": "req-rejection", "category": "rejection", "requirement": "不得存在重大违法记录"},
            ]
        }

        result = bind_response_table_requirements(plan, fine_review)
        by_id = {item["sectionId"]: item for item in result}

        self.assertEqual(
            by_id["technical-response"]["requirementIds"],
            ["req-tech", "req-service"],
        )
        self.assertEqual(by_id["technical-deviation"]["requirementIds"], [])
        self.assertEqual(
            by_id["technical-response"]["requirementMappingSource"],
            "fineReview:technical_service_response_table",
        )

    def test_response_table_keeps_valid_existing_ids_and_fills_missing_ones(self) -> None:
        result = bind_response_table_requirements(
            [
                {
                    "sectionId": "service-response",
                    "title": "服务要求响应表",
                    "responseType": "table",
                    "requirementIds": ["req-existing"],
                }
            ],
            {
                "rows": [
                    {"id": "req-existing", "category": "service_plan", "requirement": "已关联"},
                    {"id": "req-new", "category": "service_plan", "requirement": "补充条款"},
                    {"id": "req-tech", "category": "technical", "requirement": "技术条款"},
                ]
            },
        )

        self.assertEqual(result[0]["requirementIds"], ["req-existing", "req-new"])

    def test_normalize_plan_tree_reorders_parent_before_child(self) -> None:
        result = normalize_plan_tree(
            [
                {
                    "sectionId": "child",
                    "parentSectionId": "parent",
                    "level": 2,
                    "title": "子章节",
                },
                {
                    "sectionId": "parent",
                    "parentSectionId": "",
                    "level": 1,
                    "title": "父章节",
                },
            ]
        )

        self.assertEqual([item["sectionId"] for item in result], ["parent", "child"])
        self.assertEqual(result[0]["level"], 1)
        self.assertEqual(result[1]["parentSectionId"], "parent")
        self.assertEqual(result[1]["level"], 2)

    def test_normalize_plan_tree_infers_parent_from_level(self) -> None:
        result = normalize_plan_tree(
            [
                {"sectionId": "root", "level": 1, "title": "一级"},
                {"sectionId": "child", "level": 2, "title": "二级"},
                {"sectionId": "leaf", "level": 3, "title": "三级"},
            ]
        )

        self.assertEqual(result[1]["parentSectionId"], "root")
        self.assertEqual(result[2]["parentSectionId"], "child")
        self.assertEqual([item["level"] for item in result], [1, 2, 3])

    def test_normalize_plan_tree_preserves_deep_nesting(self) -> None:
        result = normalize_plan_tree(
            [
                {
                    "sectionId": f"level-{level}",
                    "parentSectionId": f"level-{level - 1}" if level > 1 else "",
                    "level": level,
                    "title": f"第{level}级",
                }
                for level in range(1, 10)
            ]
        )

        self.assertEqual([item["level"] for item in result], list(range(1, 10)))
        self.assertEqual(result[-1]["parentSectionId"], "level-8")

    def test_local_fallback_plan_contains_multiple_levels(self) -> None:
        result = plan_bid_document(
            {
                "rows": [
                    {
                        "requirementId": "req-technical",
                        "category": "technical",
                        "requirement": "提供技术方案",
                    },
                    {
                        "requirementId": "req-service",
                        "category": "service_plan",
                        "requirement": "提供实施计划",
                    },
                ]
            }
        )

        self.assertTrue({item["level"] for item in result} >= {1, 2, 3})
        by_id = {item["sectionId"]: item for item in result}
        self.assertEqual(by_id["t-02"]["parentSectionId"], "technical-root")
        self.assertEqual(by_id["t-02-01"]["parentSectionId"], "t-02")
        self.assertEqual(by_id["d-01"]["parentSectionId"], "deviation-root")

    def test_save_bid_plan_normalizes_invalid_hierarchy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["workspace-1"] = {
                "id": "workspace-1",
                "projectId": "project-1",
                "bidDocument": {"plan": [], "status": "planned"},
            }

            result = store.save_bid_plan(
                "workspace-1",
                [
                    {
                        "sectionId": "child",
                        "title": "子章节",
                        "parentSectionId": "parent",
                        "level": 9,
                        "order": 7,
                        "source": "ai",
                        "kind": "technical",
                        "content": "",
                        "status": "pending",
                    },
                    {
                        "sectionId": "parent",
                        "title": "父章节",
                        "parentSectionId": "",
                        "level": 8,
                        "order": 4,
                        "source": "ai",
                        "kind": "technical",
                        "content": "",
                        "status": "pending",
                    },
                ],
            )

            self.assertIsNotNone(result)
            assert result is not None
            saved = result["bidDocument"]["plan"]
            self.assertEqual([item["sectionId"] for item in saved], ["parent", "child"])
            self.assertEqual([item["level"] for item in saved], [1, 2])
            self.assertEqual([item["order"] for item in saved], [1, 1])

    def test_pi_plan_keeps_parent_when_parent_is_returned_later(self) -> None:
        result, warnings = Store._normalize_pi_bid_plan(
            {
                "plan": [
                    {
                        "sectionId": "child",
                        "title": "子章节",
                        "parentSectionId": "root",
                        "level": 2,
                        "source": "ai",
                        "kind": "technical",
                    },
                    {
                        "sectionId": "root",
                        "title": "技术响应",
                        "level": 1,
                        "source": "ai",
                        "kind": "technical",
                        "responseType": "section_group",
                    },
                ]
            },
            {"rows": []},
        )

        self.assertEqual(warnings, [])
        self.assertEqual([item["sectionId"] for item in result[:2]], ["root", "child"])
        self.assertEqual(result[1]["parentSectionId"], "root")
        self.assertEqual(result[1]["level"], 2)

    def test_plan_from_eval_maps_scoring_points_to_fixed_sections(self) -> None:
        result = plan_from_eval(
            [
                {
                    "factor": "技术方案",
                    "item": "技术响应程度",
                    "score": 30,
                    "criteria": "技术方案完整可行",
                },
                {
                    "factor": "售后服务",
                    "item": "售后响应",
                    "score": 20,
                    "criteria": "响应及时",
                },
                {
                    "factor": "售后服务",
                    "item": "备品备件",
                    "score": 5,
                    "criteria": "备件充足",
                },
            ]
        )

        by_section = {item["sectionId"]: item for item in result}
        # 同一章节聚合多个评分点时只保留一条，score 累加
        self.assertEqual(set(by_section), {"t-02", "t-05"})
        self.assertEqual(by_section["t-02"]["score"], 30.0)
        self.assertEqual(by_section["t-05"]["score"], 25.0)
        self.assertIn("响应及时", by_section["t-05"]["criteria"])

    def test_resolve_tender_aggregates_writing_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["workspace-1"] = {
                "id": "workspace-1",
                "projectId": "project-1",
                "reasoning": {
                    "fineReview": {
                        "rows": [{"requirementId": "req-1", "category": "technical"}],
                        "summary": {"total": 1},
                        "hardBlockers": ["blk-1"],
                    }
                },
                "project": {
                    "title": "智慧园区项目",
                    "parsed": {"projectName": "智慧园区项目", "projectCode": "SP-2026"},
                    "document": {"sections": [], "tables": []},
                },
            }

            resolved = store.resolve_tender("workspace-1")

            assert resolved is not None
            self.assertEqual(
                [row["requirementId"] for row in resolved["requirements"]],
                ["req-1"],
            )
            self.assertEqual(resolved["projectMeta"]["projectName"], "智慧园区项目")
            self.assertEqual(resolved["projectMeta"]["projectCode"], "SP-2026")
            self.assertEqual(resolved["risk"]["hardBlockers"], ["blk-1"])
            self.assertEqual(resolved["summary"], {"total": 1})
            self.assertEqual(resolved["composition"]["source"], "builtin_template")

    def test_plan_bid_document_records_evaluation_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["workspace-1"] = {
                "id": "workspace-1",
                "projectId": "project-1",
                "reasoning": {"fineReview": {"rows": []}},
                "project": {
                    "title": "智慧园区项目",
                    "parsed": {"projectName": "智慧园区项目"},
                    "document": {
                        "sections": [
                            {
                                "sectionType": "evaluation",
                                "heading": "第三章 评标办法",
                                "text": "本项目采用综合评分法。",
                            }
                        ],
                        "tables": [
                            {
                                "rows": [
                                    {
                                        "cells": [
                                            {"text": "评分因素"},
                                            {"text": "评分点"},
                                            {"text": "分值"},
                                            {"text": "评分标准"},
                                        ]
                                    },
                                    {
                                        "cells": [
                                            {"text": "技术方案"},
                                            {"text": "技术响应程度"},
                                            {"text": "30分"},
                                            {"text": "技术方案完整可行"},
                                        ]
                                    },
                                    {
                                        "cells": [
                                            {"text": "售后服务"},
                                            {"text": "售后响应"},
                                            {"text": "20分"},
                                            {"text": "响应及时"},
                                        ]
                                    },
                                ]
                            }
                        ],
                    },
                },
            }

            result = store.plan_bid_document("workspace-1")

            assert result is not None
            bid_document = result["bidDocument"]
            by_section = {
                row["sectionId"]: row for row in bid_document["evaluationPlan"]
            }
            self.assertEqual(set(by_section), {"t-02", "t-05"})
            self.assertEqual(by_section["t-02"]["score"], 30.0)
            self.assertEqual(bid_document["evaluationPlanTotal"], 50.0)
            self.assertEqual(bid_document["evaluationMethod"], "综合评分法")
