# -*- coding: utf-8 -*-
"""M2′：目录智能体新增章节的放置规则与标题回归测试。

覆盖三件事：
1. 带依据的智能体新增章节可以挂在卷下；未带依据或不带 derived 标记的一律丢弃；
2. `_normalize_plan` 不再把标题覆盖成最后一个节点的标题（既有 bug 回归）；
3. 落盘用的 `Store._normalize_pi_bid_plan` 同样保留标题与依据字段。
"""
from __future__ import annotations

import unittest

from backend.app.bid_writing.volumes import plan_from_submission_structure
from backend.app.pi_agent.writing_orchestrator import _normalize_plan
from backend.app.store import Store

SUBMISSION_STRUCTURE = {
    "confidence": "explicit",
    "structureStatus": "valid",
    "volumes": [
        {
            "volumeId": "technical",
            "title": "技术商务部分",
            "order": 1,
            "items": [
                {
                    "formatItemId": "technical-02",
                    "title": "技术和服务要求响应表",
                    "order": 1,
                    "itemType": "table",
                    "responseType": "table",
                }
            ],
        }
    ],
}


def _derived_row(title: str, *, basis: str = "technical-0001 等 12 条技术条款无叙述承载") -> dict:
    return {
        "sectionId": f"sec-{title}",
        "title": title,
        "parentSectionId": "technical",
        "level": 2,
        "order": 9,
        "nodeType": "section",
        "source": "ai",
        "kind": "technical",
        "required": False,
        "responseType": "chapter_text",
        "derived": True,
        "derivedOrigin": "required_by_tender",
        "derivedArchetype": "technical_plan",
        "derivedBasis": basis,
    }


def _generated_plan(*chapters: dict) -> list[dict]:
    """真实调用传入的是完整计划（卷 + 格式条目 + 新增章节），父级引用才有效。"""

    return [
        {
            "sectionId": "technical",
            "title": "技术商务部分",
            "nodeType": "volume",
            "level": 1,
            "order": 1,
            "responseType": "section_group",
        },
        {
            "sectionId": "technical-02",
            "title": "技术和服务要求响应表",
            "parentSectionId": "technical",
            "nodeType": "format_item",
            "level": 2,
            "order": 1,
            "itemType": "table",
            "responseType": "table",
        },
        *chapters,
    ]


class DerivedSectionPlacementTests(unittest.TestCase):
    def test_keeps_derived_chapter_under_volume(self) -> None:
        plan = plan_from_submission_structure(
            SUBMISSION_STRUCTURE,
            generated_plan=_generated_plan(_derived_row("技术方案")),
            keep_derived=True,
        )
        titles = [row["title"] for row in plan]
        self.assertIn("技术方案", titles)
        volume_item = next(row for row in plan if row["title"] == "技术商务部分")
        chapter = next(row for row in plan if row["title"] == "技术方案")
        self.assertEqual(chapter["parentSectionId"], volume_item["sectionId"])
        self.assertEqual(chapter["level"] == volume_item["level"] + 1, True)
        self.assertTrue(chapter.get("derivedBasis"))

    def test_drops_derived_chapter_when_disabled(self) -> None:
        plan = plan_from_submission_structure(
            SUBMISSION_STRUCTURE,
            generated_plan=_generated_plan(_derived_row("技术方案")),
        )
        self.assertNotIn("技术方案", [row["title"] for row in plan])

    def test_drops_derived_chapter_without_basis(self) -> None:
        plan = plan_from_submission_structure(
            SUBMISSION_STRUCTURE,
            generated_plan=_generated_plan(_derived_row("技术方案", basis="")),
            keep_derived=True,
        )
        self.assertNotIn("技术方案", [row["title"] for row in plan])

    def test_drops_unmarked_ai_chapter_under_volume(self) -> None:
        row = _derived_row("技术方案")
        row.pop("derived")
        plan = plan_from_submission_structure(
            SUBMISSION_STRUCTURE, generated_plan=_generated_plan(row), keep_derived=True
        )
        self.assertNotIn("技术方案", [row["title"] for row in plan])


class TitleRegressionTests(unittest.TestCase):
    def test_normalize_plan_keeps_each_title(self) -> None:
        plan = _normalize_plan({
            "plan": [
                {
                    "sectionId": "technical",
                    "title": "技术商务部分",
                    "nodeType": "volume",
                    "level": 1,
                    "order": 1,
                    "responseType": "section_group",
                },
                _derived_row("技术方案"),
                _derived_row("培训方案"),
            ]
        })
        self.assertEqual(
            [row["title"] for row in plan],
            ["技术商务部分", "技术方案", "培训方案"],
        )

    def test_store_normalization_keeps_titles_and_basis(self) -> None:
        payload = {
            "plan": [
                {
                    "sectionId": "technical",
                    "title": "技术商务部分",
                    "nodeType": "volume",
                    "level": 1,
                    "order": 1,
                    "responseType": "section_group",
                },
                _derived_row("技术方案"),
                _derived_row("培训方案"),
            ]
        }
        plan, _warnings = Store._normalize_pi_bid_plan(payload, {"rows": []}, None)
        titles = [row["title"] for row in plan]
        self.assertEqual(titles[:3], ["技术商务部分", "技术方案", "培训方案"])
        derived = [row for row in plan if row.get("derived")]
        self.assertEqual(len(derived), 2)
        self.assertTrue(all(row.get("derivedBasis") for row in derived))
        self.assertTrue(all(row.get("derivedArchetype") == "technical_plan" for row in derived))


if __name__ == "__main__":
    unittest.main()
