from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.bid_writing.plan_tree import normalize_plan_tree
from backend.app.bid_writing.planning import plan_bid_document
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
