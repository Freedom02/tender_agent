# -*- coding: utf-8 -*-
"""M2′：目录智能体新增章节的放置规则与标题回归测试。

覆盖三件事：
1. 带依据的智能体新增章节可以挂在卷下；未带依据或不带 derived 标记的一律丢弃；
2. `_normalize_plan` 不再把标题覆盖成最后一个节点的标题（既有 bug 回归）；
3. 落盘用的 `Store._normalize_pi_bid_plan` 同样保留标题与依据字段。
"""
from __future__ import annotations

import unittest

from backend.app.bid_writing.schemas import normalize_response_type
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


class ResponseTypeVocabularyTests(unittest.TestCase):
    """真实踩坑回归：模型把 itemType 词表（narrative）写进 responseType，
    放置护栏若用白名单会让整章静默消失。"""

    def test_normalize_response_type_maps_item_type_vocabulary(self) -> None:
        self.assertEqual(normalize_response_type("narrative"), "chapter_text")
        self.assertEqual(normalize_response_type("Narrative"), "chapter_text")
        self.assertEqual(normalize_response_type("text"), "chapter_text")
        self.assertEqual(normalize_response_type("chapter_text"), "chapter_text")
        self.assertEqual(normalize_response_type("fixed_table"), "fixed_form")
        self.assertEqual(normalize_response_type(""), "")

    def test_derived_chapter_with_narrative_response_type_is_kept(self) -> None:
        row = _derived_row("技术方案")
        row["responseType"] = "narrative"
        plan = plan_from_submission_structure(
            SUBMISSION_STRUCTURE,
            generated_plan=_generated_plan(row),
            keep_derived=True,
        )
        self.assertIn("技术方案", [item["title"] for item in plan])

    def test_derived_chapter_may_not_become_fixed_form_or_section_group(self) -> None:
        for response_type in ("fixed_form", "section_group", "deviation"):
            row = _derived_row("技术方案")
            row["responseType"] = response_type
            plan = plan_from_submission_structure(
                SUBMISSION_STRUCTURE,
                generated_plan=_generated_plan(row),
                keep_derived=True,
            )
            self.assertNotIn(
                "技术方案", [item["title"] for item in plan], response_type
            )

    def test_normalize_plan_converts_narrative_to_chapter_text(self) -> None:
        row = _derived_row("技术方案")
        row["responseType"] = "narrative"
        plan = _normalize_plan({"plan": _generated_plan(row)})
        chapter = next(item for item in plan if item["title"] == "技术方案")
        self.assertEqual(chapter["responseType"], "chapter_text")

    def test_store_normalization_converts_narrative_to_chapter_text(self) -> None:
        row = _derived_row("技术方案")
        row["responseType"] = "narrative"
        plan, _warnings = Store._normalize_pi_bid_plan(
            {"plan": _generated_plan(row)}, {"rows": []}, None
        )
        chapter = next(item for item in plan if item["title"] == "技术方案")
        self.assertEqual(chapter["responseType"], "chapter_text")


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


class AgentAddedSectionIdTests(unittest.TestCase):
    """Wave 0 · 0.5：智能体新增章节的 ID 由 Python 稳定生成。

    真实观察：两次运行同一个项目，模型给同一个新增章节的 sectionId 会在
    `technical-plan` 与 `technical-narr-01` 之间漂移，导致「同一输入两次运行
    目录不同」。这里把 ID 交给 Python 按「父级 + 章节原型」推导，
    原文骨架（卷 / 格式条目）的 ID 一律不动。
    """

    def _two_runs(self) -> tuple[list[dict], list[dict]]:
        first = _generated_plan(_derived_row("技术方案"), _derived_row("培训方案"))
        second = _generated_plan(_derived_row("技术方案"), _derived_row("培训方案"))
        # 只有模型自己起的 ID 不同，其余信息完全一致
        second[2]["sectionId"] = "technical-narr-01"
        second[3]["sectionId"] = "technical-plan"
        return _normalize_plan({"plan": first}), _normalize_plan({"plan": second})

    def test_ids_stable_even_when_model_ids_drift(self) -> None:
        first, second = self._two_runs()
        strip = lambda plan: [  # noqa: E731 - 只比较 ID 序列与标题
            (row["title"], row["sectionId"]) for row in plan
        ]
        self.assertEqual(strip(first), strip(second))

    def test_ids_are_derived_from_parent_and_archetype(self) -> None:
        plan = _normalize_plan({"plan": _generated_plan(_derived_row("技术方案"))})
        chapter = next(row for row in plan if row["title"] == "技术方案")
        self.assertEqual(chapter["sectionId"], "d-technical-technical-plan")

    def test_skeleton_ids_are_never_rewritten(self) -> None:
        first, _second = self._two_runs()
        volume = next(row for row in first if row["nodeType"] == "volume")
        format_item = next(row for row in first if row["nodeType"] == "format_item")
        self.assertEqual(volume["sectionId"], "technical")
        self.assertEqual(format_item["sectionId"], "technical-02")

    def test_same_archetype_chapters_are_separated_by_title_token(self) -> None:
        """两章共用同一原型时，ID 必须可区分且都带得出处（父级+原型）。"""

        first, _second = self._two_runs()
        derived = [row for row in first if row.get("derived")]
        ids = [row["sectionId"] for row in derived]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(
            all(item.startswith("d-technical-technical-plan") for item in ids), ids
        )

    def test_volume_marked_derived_keeps_its_own_id(self) -> None:
        """护栏：智能体误把卷标成 derived 时，也不能改写卷的 ID。"""

        volume = {
            "sectionId": "technical",
            "title": "技术商务部分",
            "nodeType": "volume",
            "level": 1,
            "order": 1,
            "responseType": "section_group",
            "derived": True,
            "derivedArchetype": "technical_plan",
        }
        plan = _normalize_plan({"plan": [volume, _derived_row("技术方案")]})
        self.assertEqual(plan[0]["sectionId"], "technical")

    def test_child_follows_parent_renamed_to_stable_id(self) -> None:
        child = _derived_row("技术方案实施步骤")
        child["sectionId"] = "technical-plan-step"
        child["parentSectionId"] = "technical-plan"  # 引用模型自己给父级的 ID
        child["level"] = 3
        child["derivedArchetype"] = "technical_steps"
        parent = _derived_row("技术方案")
        parent["sectionId"] = "technical-plan"
        plan = _normalize_plan({"plan": _generated_plan(parent, child)})
        normalized_parent = next(row for row in plan if row["title"] == "技术方案")
        normalized_child = next(row for row in plan if row["title"] == "技术方案实施步骤")
        self.assertEqual(normalized_parent["sectionId"], "d-technical-technical-plan")
        self.assertEqual(
            normalized_child["parentSectionId"], normalized_parent["sectionId"]
        )

    def test_same_archetype_chapters_get_order_independent_unique_ids(self) -> None:
        first = _normalize_plan(
            {"plan": _generated_plan(_derived_row("技术方案"), _derived_row("培训方案"))}
        )
        second = _normalize_plan(
            {"plan": _generated_plan(_derived_row("培训方案"), _derived_row("技术方案"))}
        )
        ids = lambda plan: {  # noqa: E731
            row["title"]: row["sectionId"] for row in plan if row.get("derived")
        }
        self.assertEqual(ids(first), ids(second))
        self.assertEqual(len(set(ids(first).values())), 2)

    def test_store_normalization_keeps_stable_ids(self) -> None:
        """真实链路：目录智能体先归一化，落盘时 Store 不得把稳定 ID 改回去。"""

        payload = {"plan": _generated_plan(_derived_row("技术方案"))}
        normalized = _normalize_plan(payload)
        plan, _warnings = Store._normalize_pi_bid_plan(
            {"plan": normalized}, {"rows": []}, None
        )
        from_plan = next(row for row in normalized if row["title"] == "技术方案")
        from_store = next(row for row in plan if row["title"] == "技术方案")
        self.assertEqual(from_plan["sectionId"], "d-technical-technical-plan")
        self.assertEqual(from_store["sectionId"], from_plan["sectionId"])


if __name__ == "__main__":
    unittest.main()
