# -*- coding: utf-8 -*-
"""校对器 add() 参数顺序回归测试。

历史缺陷：六处调用点按 (分类, level, title, detail) 传参，而 add() 的签名是
(level, title, detail, count_key)。后果是 level 被强制降级为「中」、title 取到
「高/中」，且报告汇总里「漏响应/废标项/敏感词」恒为 0。

本测试锁定 riskItems 的 level/title 契约与报告汇总计数，防止再次回归。
"""
from __future__ import annotations

import unittest

from backend.app.bid_writing.proofread import RISK_LEVELS, proofread


def _section(section_id: str = "t-02", **overrides):
    section = {
        "sectionId": section_id,
        "title": "技术方案",
        "kind": "technical",
        "source": "ai",
        "content": "",
        "sourceRefs": [],
    }
    section.update(overrides)
    return section


class ProofreadRiskLevelTests(unittest.TestCase):
    def _run(self, bid_doc=None, fine_review=None, project=None, company=None):
        return proofread(
            bid_doc if bid_doc is not None else {"plan": [], "deviationTables": {}},
            fine_review if fine_review is not None else {"rows": []},
            project
            if project is not None
            else {"parsed": {"projectName": "某项目", "projectCode": "P-1"}},
            company if company is not None else {"company": "某公司"},
        )

    def _by_title(self, result):
        return {item["title"]: item for item in result["riskItems"]}

    def test_levels_are_within_contract(self):
        """任何风险项的 level 必须 ∈ {高,中,低}，且 title 不得是 level 字面量。"""
        result = self._run(
            bid_doc={
                "plan": [_section(content="我方具备最先进的技术。")],
                "deviationTables": {},
            },
            fine_review={
                "rows": [{"requirementId": "req-001", "requirement": "应提供三年质保"}],
                "hardBlockers": [
                    {"requirementId": "req-002", "detail": "未提供营业执照"}
                ],
            },
            project={"parsed": {}},
            company=None,
        )
        self.assertTrue(result["riskItems"])
        for item in result["riskItems"]:
            self.assertIn(item["level"], RISK_LEVELS, item)
            self.assertNotIn(item["title"], RISK_LEVELS, item)

    def test_missed_response_is_high_and_counted(self):
        result = self._run(
            fine_review={
                "rows": [{"requirementId": "req-001", "requirement": "应提供三年质保"}]
            }
        )
        item = self._by_title(result)["漏响应"]
        self.assertEqual(item["level"], "高")
        self.assertIn("req-001", item["detail"])
        self.assertIn("漏响应：**1**", result["report"])

    def test_rejection_risk_is_high_and_counted(self):
        result = self._run(
            fine_review={
                "rows": [],
                "hardBlockers": [
                    {"requirementId": "req-002", "detail": "未提供营业执照"}
                ],
            }
        )
        item = self._by_title(result)["废标风险（硬性否决）"]
        self.assertEqual(item["level"], "高")
        self.assertIn("未提供营业执照", item["detail"])
        self.assertIn("废标项：**1**", result["report"])

    def test_key_field_missing_is_high(self):
        result = self._run(project={"parsed": {}}, company={})
        items = [item for item in result["riskItems"] if item["title"] == "关键信息缺失"]
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertEqual(item["level"], "高")

    def test_project_name_mismatch_is_medium(self):
        result = self._run(
            bid_doc={"plan": [_section(content="本项目方案。")], "deviationTables": {}},
            project={
                "parsed": {"projectName": "数字孪生平台", "projectCode": "P-1"}
            },
        )
        item = self._by_title(result)["项目名不一致"]
        self.assertEqual(item["level"], "中")

    def test_sensitive_word_is_low_and_counted(self):
        result = self._run(
            bid_doc={
                "plan": [_section(content="本方案采用最先进技术。")],
                "deviationTables": {},
            }
        )
        item = self._by_title(result)["敏感词"]
        self.assertEqual(item["level"], "低")
        self.assertIn("敏感词：**1**", result["report"])

    def test_negative_deviation_is_high_and_counted(self):
        result = self._run(
            bid_doc={
                "plan": [],
                "deviationTables": {
                    "technical": [
                        {
                            "requirementId": "req-003",
                            "deviation": "负偏离",
                            "requirement": "支持三维可视化",
                        }
                    ]
                },
            }
        )
        item = self._by_title(result)["负偏离未处理"]
        self.assertEqual(item["level"], "高")
        self.assertIn("负偏离/待澄清：**1**", result["report"])


if __name__ == "__main__":
    unittest.main()
