from __future__ import annotations

import unittest

from backend.app.tender_matching.regression import (
    build_regression_snapshot,
    compare_regression_snapshots,
)


class RegressionTests(unittest.TestCase):
    def _parser_report(self, *, warnings: list[str] | None = None) -> dict:
        return {
            "records": [
                {
                    "path": "demo-data/sample.pdf",
                    "status": "parsed",
                    "sha256": "abc",
                    "metrics": {
                        "status": "parsed",
                        "textChars": 100,
                        "segmentCount": 10,
                        "sectionCount": 3,
                        "tableCount": 1,
                        "warnings": warnings or [],
                    },
                }
            ]
        }

    def _decision_report(self, *, error: str = "") -> dict:
        return {
            "records": [
                {
                    "path": "demo-data/sample.pdf",
                    "error": error,
                    "requirementCount": 4,
                    "technicalItemCount": 2,
                    "hardBlockerCount": 0,
                    "technicalGapCount": 1,
                    "materialGapCount": 1,
                    "needsHumanReviewCount": 1,
                    "decisionRecommendation": "谨慎继续",
                }
            ]
        }

    def test_snapshot_contains_comparable_stage_metrics(self) -> None:
        snapshot = build_regression_snapshot(
            {
                "samples": self._parser_report()["records"],
            },
            self._decision_report(),
            gold_report={"goldStatus": "pending_manual_annotation", "samples": []},
        )

        self.assertEqual(snapshot["parser"]["sampleCount"], 1)
        self.assertEqual(snapshot["parser"]["parseableRate"], 1.0)
        self.assertEqual(snapshot["decision"]["successRate"], 1.0)
        self.assertEqual(snapshot["samples"]["demo-data/sample.pdf"]["decision"]["requirementCount"], 4)

    def test_compare_flags_new_e2e_failure(self) -> None:
        baseline = build_regression_snapshot(self._parser_report(), self._decision_report())
        current = build_regression_snapshot(
            self._parser_report(warnings=[f"new warning {index}" for index in range(6)]),
            self._decision_report(error="extract failed"),
        )

        comparison = compare_regression_snapshots(baseline, current)

        self.assertFalse(comparison["passed"])
        failed_names = {
            item["name"] for item in comparison["checks"] if item["status"] == "failed"
        }
        self.assertIn("decision_failure_count", failed_names)
        self.assertIn("parser_warning_count", {
            item["name"] for item in comparison["checks"]
            if item["status"] in {"warning", "failed"}
        })

    def test_gold_pending_is_warning_not_accuracy_failure(self) -> None:
        snapshot = build_regression_snapshot(
            self._parser_report(),
            self._decision_report(),
            gold_report={"goldStatus": "pending_manual_annotation", "samples": []},
        )
        comparison = compare_regression_snapshots(snapshot, snapshot)

        self.assertTrue(comparison["passed"])
        self.assertEqual(comparison["currentGoldStatus"], "pending_manual_annotation")
        self.assertGreaterEqual(comparison["warningCount"], 1)


if __name__ == "__main__":
    unittest.main()
