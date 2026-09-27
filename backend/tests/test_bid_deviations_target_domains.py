from __future__ import annotations

import unittest

from backend.app.bid_writing.deviations import build_deviation_tables


class BidDeviationTargetDomainTests(unittest.TestCase):
    def test_target_domains_route_rows_and_unverified_satisfaction_stays_pending(self) -> None:
        tables = build_deviation_tables(
            {
                "rows": [
                    {
                        "requirementId": "req-technical-unverified",
                        "targetModule": "technical",
                        "matchDomain": "technical",
                        "requirement": "平台支持三维场景加载",
                        "status": "满足",
                        "matchedObjectIds": [],
                        "evidenceItems": [],
                    },
                    {
                        "requirementId": "req-technical-evidenced",
                        "matchDomain": "technical",
                        "requirement": "平台支持模型导入",
                        "status": "满足",
                        "matchedObjectIds": ["product-1"],
                    },
                    {
                        "requirementId": "req-qualification",
                        "targetModule": "qualification",
                        "requirement": "提供营业执照",
                        "status": "待补材料",
                    },
                    {
                        "requirementId": "req-rejection",
                        "matchDomain": "rejection",
                        "requirement": "不得串通投标",
                        "status": "待核查",
                    },
                ]
            }
        )

        self.assertEqual(
            [row["requirementId"] for row in tables["technical"]],
            ["req-technical-unverified", "req-technical-evidenced"],
        )
        self.assertEqual(tables["technical"][0]["deviation"], "待人工确认")
        self.assertEqual(tables["technical"][1]["deviation"], "无偏离")
        self.assertEqual(
            [row["requirementId"] for row in tables["commercial"]],
            ["req-qualification", "req-rejection"],
        )


if __name__ == "__main__":
    unittest.main()
