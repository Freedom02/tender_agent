# -*- coding: utf-8 -*-
"""目录规划诊断（M1）单元测试：只读诊断不碰目录、不调 LLM。"""
from __future__ import annotations

import unittest

from backend.app.bid_writing.outline_diagnosis import (
    ORIGIN_INITIATIVE,
    ORIGIN_REQUIRED,
    RESPONSE_ARCHETYPES,
    diagnose,
)


def volume(volume_id: str, title: str, order: int, kind: str = "") -> dict:
    return {
        "sectionId": volume_id,
        "title": title,
        "nodeType": "volume",
        "volumeId": volume_id,
        "level": 1,
        "order": order,
        "responseType": "section_group",
        "kind": kind,
        "source": "template",
        "content": "",
        "status": "planned",
    }


def format_item(
    section_id: str,
    title: str,
    parent_id: str,
    order: int,
    *,
    response_type: str = "table",
    item_type: str = "fixed_table",
    requirement_ids: list[str] | None = None,
) -> dict:
    return {
        "sectionId": section_id,
        "title": title,
        "nodeType": "format_item",
        "parentSectionId": parent_id,
        "level": 2,
        "order": order,
        "responseType": response_type,
        "itemType": item_type,
        "kind": "technical",
        "source": "table",
        "content": "",
        "status": "planned",
        "requirementIds": list(requirement_ids or []),
    }


def technical_plan_fixture() -> tuple[dict, list[dict]]:
    """复刻真实项目形状：3 个卷、全表格式条目、技术条款只挂在响应表上。"""

    technical_ids = [f"technical-{index:04d}" for index in range(1, 9)]
    requirements = [
        {
            "id": requirement_id,
            "targetModule": "technical",
            "responseType": "technical_response",
            "requirement": f"支持三维场景功能第 {index} 项，性能指标不低于招标要求。",
            "mandatory": True,
            "sourceLocator": f"TABLE-002第{index}行",
        }
        for index, requirement_id in enumerate(technical_ids, start=1)
    ]
    requirements.append(
        {
            "id": "evidence-0001",
            "targetModule": "evidence",
            "responseType": "evidence",
            "requirement": "须提供信息系统安全等级保护备案证明复印件。",
            "mandatory": True,
            "sourceLocator": "TABLE-008第9行",
        }
    )
    requirements.append(
        {
            "id": "qualification-0006",
            "targetModule": "qualification",
            "responseType": "evidence",
            "requirement": "提供有效的营业执照复印件。",
            "mandatory": True,
            "sourceLocator": "TABLE-008第3行",
        }
    )
    bundle = {
        "requirements": requirements,
        "evaluationItems": [
            {"id": "eval-001", "factor": "技术评分", "item": "项目理解与需求分析", "score": 10},
            {"id": "eval-002", "factor": "技术评分", "item": "技术方案完整性", "score": 30},
        ],
    }
    plan = [
        volume("qualification", "资格及资信证明部分", 1, "commercial"),
        format_item("qualification-01", "投标函", "qualification", 1, response_type="fixed_form", item_type="fixed_form"),
        volume("pricing", "报价部分", 2, "commercial"),
        format_item("pricing-01", "开标（报价）一览表", "pricing", 1),
        volume("technical", "技术商务部分", 3, "technical"),
        format_item("technical-01", "标的说明一览表", "technical", 1),
        format_item(
            "technical-02",
            "技术和服务要求响应表",
            "technical",
            2,
            requirement_ids=technical_ids,
        ),
        format_item("technical-03", "商务条件响应表", "technical", 3),
    ]
    return bundle, plan


class OutlineDiagnosisTests(unittest.TestCase):
    def test_archetypes_declare_both_origins(self) -> None:
        origins = {archetype["origin"] for archetype in RESPONSE_ARCHETYPES}
        self.assertEqual(origins, {ORIGIN_REQUIRED, ORIGIN_INITIATIVE})
        keys = [archetype["archetype"] for archetype in RESPONSE_ARCHETYPES]
        self.assertEqual(len(keys), len(set(keys)), "原型 ID 必须唯一")

    def test_table_bound_requirements_still_count_as_narrative_gap(self) -> None:
        bundle, plan = technical_plan_fixture()
        result = diagnose(bundle, plan, workspace_id="ws-1")

        summary = result["summary"]
        self.assertEqual(summary["volumes"], 3)
        self.assertEqual(summary["formatItems"], 5)
        self.assertEqual(summary["responseSections"], 0)
        self.assertEqual(summary["narrativeBound"], 0)
        self.assertEqual(summary["tableBound"], 8)
        self.assertFalse(summary["hasNarrativeCapacity"])

        gaps = {item["archetype"]: item for item in result["requiredGaps"]}
        self.assertIn("technical_plan", gaps)
        self.assertEqual(gaps["technical_plan"]["origin"], ORIGIN_REQUIRED)
        self.assertEqual(gaps["technical_plan"]["requirementCount"], 8)
        self.assertEqual(gaps["technical_plan"]["targetVolumeId"], "technical")
        self.assertIn("技术商务部分", gaps["technical_plan"]["targetVolumeTitle"])

    def test_evidence_chapter_gap_is_derived_from_technical_clauses(self) -> None:
        """技术参数证明材料依据应来自技术条款，而不是资格类材料条款。"""

        bundle, plan = technical_plan_fixture()
        result = diagnose(bundle, plan)
        gaps = {item["archetype"]: item for item in result["requiredGaps"]}
        self.assertIn("evidence_materials", gaps)
        evidence = gaps["evidence_materials"]
        self.assertEqual(evidence["targetVolumeId"], "technical")
        self.assertEqual(evidence["requirementCount"], 8)
        self.assertTrue(
            all(requirement_id.startswith("technical-") for requirement_id in evidence["requirementIds"]),
            "证明材料缺口不应由 qualification/evidence 类条款驱动",
        )

    def test_standalone_qualification_material_clauses_do_not_drive_chapters(self) -> None:
        """独立材料条款（营业执照、授权书）应由资格材料承载，不进叙述章节原型。"""

        bundle, plan = technical_plan_fixture()
        result = diagnose(bundle, plan)
        all_ids = {
            requirement_id
            for entry in [*result["requiredGaps"], *result["initiatives"]]
            for requirement_id in entry["requirementIds"]
        }
        self.assertNotIn("qualification-0006", all_ids)
        self.assertEqual(result["summary"]["unbound"], 2, "营业执照与备案证明两条应计为未绑定")

    def test_evaluation_items_produce_initiative_suggestions(self) -> None:
        bundle, plan = technical_plan_fixture()
        result = diagnose(bundle, plan)
        initiatives = {item["archetype"]: item for item in result["initiatives"]}
        self.assertIn("understanding", initiatives)
        self.assertEqual(initiatives["understanding"]["origin"], ORIGIN_INITIATIVE)
        self.assertIn("eval-001", initiatives["understanding"]["evaluationIds"])
        self.assertEqual(initiatives["understanding"]["evaluationScore"], 10.0)

    def test_existing_narrative_section_closes_the_gap(self) -> None:
        bundle, plan = technical_plan_fixture()
        technical_ids = [f"technical-{index:04d}" for index in range(1, 9)]
        plan.append(
            {
                "sectionId": "derived-technical_plan",
                "title": "总体技术方案",
                "nodeType": "derived_item",
                "parentSectionId": "technical",
                "level": 2,
                "order": 6,
                "responseType": "chapter_text",
                "kind": "technical",
                "source": "ai",
                "content": "",
                "status": "planned",
                "requirementIds": technical_ids,
            }
        )
        result = diagnose(bundle, plan)
        gaps = {item["archetype"]: item for item in result["requiredGaps"]}
        self.assertNotIn("technical_plan", gaps, "已有叙述章节时不应再报同一原型缺口")
        self.assertEqual(result["summary"]["narrativeBound"], 8)
        self.assertTrue(result["summary"]["hasNarrativeCapacity"])

    def test_missing_plan_returns_warning_instead_of_failure(self) -> None:
        bundle, _plan = technical_plan_fixture()
        result = diagnose(bundle, [], workspace_id="ws-empty")
        self.assertEqual(result["summary"]["volumes"], 0)
        self.assertTrue(result["warnings"])
        self.assertIn("还没有目录", result["warnings"][0])

    def test_unbound_requirements_are_reported_when_no_binding_exists(self) -> None:
        bundle, plan = technical_plan_fixture()
        for row in plan:
            row.pop("requirementIds", None)
        result = diagnose(bundle, plan)
        self.assertEqual(result["summary"]["tableBound"], 0)
        self.assertEqual(result["summary"]["unbound"], 10)
        self.assertTrue(result["unboundRequirementSamples"])
        self.assertIn("requirementId", result["unboundRequirementSamples"][0])


if __name__ == "__main__":
    unittest.main()
