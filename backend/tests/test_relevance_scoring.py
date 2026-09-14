from __future__ import annotations

import unittest

from backend.app.document_recognition.storage import build_company_knowledge
from backend.app.tender_matching.service import evaluate_company_relevance


def notice(
    title: str,
    *,
    body: str = "",
    region: str = "北京市",
    category: str = "数字孪生",
    service_type: str = "数字孪生",
    notice_type: str = "公开招标公告",
    procurement_method: str = "公开招标",
) -> dict[str, str]:
    return {
        "title": title,
        "projectName": title,
        "category": category,
        "serviceType": service_type,
        "region": region,
        "serviceLocation": region,
        "noticeType": notice_type,
        "procurementMethod": procurement_method,
        "analysisText": body,
    }


class RelevanceScoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = {
            "organization": {
                "organizationName": "示例科技",
                "serviceScope": "数字孪生、三维可视化",
                "productScope": "园区可视化平台",
                "industryScope": "智慧园区",
                "capabilities": "项目实施与数据接入",
            },
            "performance_case": [
                {"performanceId": "PERF-1", "industry": "智慧园区", "serviceScope": "平台实施"},
            ],
            "technical_product": [
                {
                    "productId": "PRD-1",
                    "productName": "ThingJS平台",
                    "aliases": "数字孪生平台",
                    "applicableScenario": "智慧园区",
                },
            ],
            "technical_parameter": [
                {"parameterId": "PARAM-1", "description": "物联数据接入与三维建模"},
            ],
        }
        self.knowledge = build_company_knowledge(self.facts)

    def test_m3_facts_build_discovery_vocabulary(self) -> None:
        self.assertEqual(
            self.knowledge["businessDirections"][0]["name"],
            "数字孪生",
        )
        self.assertEqual(
            self.knowledge["industryScenarios"][0]["name"],
            "智慧园区",
        )
        self.assertEqual(self.knowledge["products"][0]["id"], "PRD-1")
        self.assertTrue(any("数据接入" in item["name"] for item in self.knowledge["capabilities"]))

    def test_title_hit_scores_higher_than_body_only_hit(self) -> None:
        title_hit = evaluate_company_relevance(
            notice("智慧园区ThingJS平台数字孪生建设项目"),
            self.knowledge,
            {"region": "全国", "serviceType": ""},
        )
        body_hit = evaluate_company_relevance(
            notice(
                "某单位综合服务采购项目",
                body="采购内容包括数字孪生平台、智慧园区场景和ThingJS平台实施。",
                category="综合服务",
                service_type="",
            ),
            self.knowledge,
            {"region": "全国", "serviceType": ""},
        )

        self.assertGreater(title_hit["score"], body_hit["score"])
        self.assertTrue(title_hit["shouldAnalyze"])
        self.assertFalse(body_hit["shouldAnalyze"])
        self.assertEqual(title_hit["scoreVersion"], "relevance-v2")
        self.assertIn("标题/项目名称", " ".join(title_hit["matchEvidence"]))

    def test_exclude_region_and_procurement_are_hard_filters(self) -> None:
        excluded = evaluate_company_relevance(
            notice("ThingJS平台数字孪生中标公告", notice_type="中标公告"),
            self.knowledge,
            {"region": "全国", "excludeKeywords": ["中标公告"]},
        )
        wrong_region = evaluate_company_relevance(
            notice("ThingJS平台数字孪生项目", region="广东省"),
            self.knowledge,
            {"region": "北京市", "serviceType": ""},
        )
        wrong_procurement = evaluate_company_relevance(
            notice("ThingJS平台数字孪生项目", procurement_method="竞争性磋商", notice_type="竞争性磋商公告"),
            self.knowledge,
            {"region": "全国", "targetProcurementTypes": ["公开招标"]},
        )

        for result in (excluded, wrong_region, wrong_procurement):
            self.assertFalse(result["matched"])
            self.assertEqual(result["score"], 0)
            self.assertFalse(result["shouldAnalyze"])
            self.assertEqual(result["nextStage"], "不进入后续匹配")

    def test_procurement_filter_ignores_unlabeled_body_mentions(self) -> None:
        result = evaluate_company_relevance(
            notice(
                "ThingJS平台数字孪生项目",
                body="本项目不采用公开招标，采购方式为竞争性磋商。",
                notice_type="采购公告",
                procurement_method="",
            ),
            self.knowledge,
            {"region": "全国", "targetProcurementTypes": ["公开招标"]},
        )

        self.assertFalse(result["matched"])
        self.assertEqual(result["score"], 0)
        self.assertIn("采购方式未命中", result["excludeReason"])

    def test_low_score_does_not_enter_announcement_matching(self) -> None:
        result = evaluate_company_relevance(
            notice("某单位综合服务采购项目", body="项目要求具备数字孪生相关经验。"),
            self.knowledge,
            {"region": "全国", "serviceType": ""},
        )

        self.assertGreater(result["score"], 0)
        self.assertLess(result["score"], 70)
        self.assertFalse(result["shouldAnalyze"])
        self.assertNotEqual(result["nextStage"], "公告资格粗匹配")

    def test_empty_keywords_do_not_default_to_fifty(self) -> None:
        result = evaluate_company_relevance(
            notice("完全无关的项目", body="普通采购内容。"),
            {},
            {"region": "全国", "serviceType": "", "includeKeywords": [], "synonyms": []},
        )

        self.assertFalse(result["matched"])
        self.assertEqual(result["score"], 0)
        self.assertFalse(result["shouldAnalyze"])


if __name__ == "__main__":
    unittest.main()
