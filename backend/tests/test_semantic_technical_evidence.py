from __future__ import annotations

import unittest

from backend.app.tender_matching.semantic_judge import _tech_result_from_data


class SemanticTechnicalEvidenceTests(unittest.TestCase):
    def test_claimed_coverage_without_a_candidate_is_not_satisfaction(self) -> None:
        result = _tech_result_from_data(
            {
                "coveredPoints": ["支持三维场景加载"],
                "gaps": [],
                "matchedUnits": [
                    {
                        "productId": "无候选产品",
                        "matchedParams": ["invented-parameter"],
                    }
                ],
                "coverage": 1,
            },
            [],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "待补材料")
        self.assertEqual(result["matchedFactIds"], [])
        self.assertEqual(result["coverage"], 0)
        self.assertIn("未关联到可核验的企业产品或技术参数", result["gaps"])

    def test_only_candidate_product_and_parameter_ids_are_retained(self) -> None:
        result = _tech_result_from_data(
            {
                "coveredPoints": ["支持三维场景加载"],
                "matchedUnits": [
                    {
                        "productId": "product-1",
                        "matchedParams": ["parameter-1", "invented-parameter"],
                    },
                    {
                        "productId": "invented-product",
                        "matchedParams": ["parameter-2"],
                    },
                ],
                "coverage": 1,
            },
            [
                {
                    "productId": "product-1",
                    "parameters": [{"parameterId": "parameter-1"}],
                }
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["status"], "满足")
        self.assertEqual(result["matchedFactIds"], ["parameter-1", "product-1"])
        self.assertEqual(
            result["technicalAlignment"]["matchedUnits"],
            [
                {
                    "productId": "product-1",
                    "matchedParams": ["parameter-1"],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
