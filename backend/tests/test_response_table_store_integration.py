from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.store import Store


class FakeRequirementResponseClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="pi-response-table-test")
        self.calls: list[dict[str, object]] = []

    def draft_requirement_responses(self, **payload: object) -> dict[str, object]:
        self.calls.append(payload)
        requirements = payload.get("requirements") or []
        return {
            "rows": [
                {
                    "requirementId": item["requirementId"],
                    "response": "我方平台依据已匹配的产品资料满足该项要求。",
                }
                for item in requirements
            ]
        }


class ResponseTableStoreIntegrationTests(unittest.TestCase):
    def test_single_section_reuses_saved_matches_without_refresh_or_general_drafting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.config = {}
            store.company_knowledge = {
                "products": [
                    {
                        "id": "product-001",
                        "name": "数字孪生应用平台",
                        "description": "支持三维场景可视化与数据接入。",
                        "evidencePath": "m3/products/digital-twin.pdf",
                        "factKey": "technical_product",
                    }
                ]
            }
            section = {
                "sectionId": "tech-service-response",
                "title": "技术和服务要求响应表",
                "kind": "technical",
                "source": "ai",
                "responseType": "table",
                "content": "",
            }
            store.workspaces["workspace-001"] = {
                "id": "workspace-001",
                "projectId": "project-001",
                "project": {"id": "project-001", "title": "数字孪生项目"},
                "reasoning": {
                    "fineReview": {
                        "rows": [
                            {
                                "requirementId": "req-technical-001",
                                "category": "technical",
                                "requirement": "平台应支持三维场景可视化。",
                                "status": "满足",
                                "matchedObjectIds": ["product-001"],
                            }
                        ]
                    }
                },
                "bidDocument": {
                    "status": "planned",
                    "plan": [section],
                    "deviationTables": {
                        "technical": [],
                        "commercial": [{"requirementId": "stale-row"}],
                    },
                },
            }
            client = FakeRequirementResponseClient()

            with (
                patch(
                    "backend.app.cloud_agent.config.DifyConfig.from_env",
                    return_value=SimpleNamespace(provider="pi"),
                ),
                patch("backend.app.pi_agent.client.PiClient", return_value=client),
                patch.object(store, "_refresh_company_from_m3") as refresh_m3,
                patch.object(store, "_ensure_pi_requirement_coverage") as coverage,
                patch.object(store, "_cloud_section_draft") as cloud_draft,
            ):
                result = store.generate_bid_section(
                    "workspace-001",
                    "tech-service-response",
                )

            self.assertIsNotNone(result)
            assert result is not None
            generated = result["bidDocument"]["plan"][0]
            self.assertIn(
                "我方平台依据已匹配的产品资料满足该项要求。",
                generated["content"],
            )
            self.assertEqual(generated["requirementResponseRows"][0]["requirementId"], "req-technical-001")
            self.assertEqual(
                generated["requirementResponseRows"][0]["materials"][0]["materialId"],
                "product-001",
            )
            self.assertEqual(
                generated["sourceRefs"][0]["evidencePath"],
                "m3/products/digital-twin.pdf",
            )
            self.assertEqual(
                [
                    row["requirementId"]
                    for row in result["bidDocument"]["deviationTables"]["technical"]
                ],
                ["req-technical-001"],
            )
            self.assertEqual(
                result["bidDocument"]["deviationTables"]["technical"][0]["deviation"],
                "无偏离",
            )
            self.assertEqual(result["bidDocument"]["deviationTables"]["commercial"], [])
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(
                client.calls[0]["requirements"][0]["requirementId"],
                "req-technical-001",
            )
            refresh_m3.assert_not_called()
            coverage.assert_not_called()
            cloud_draft.assert_not_called()

    def test_full_generation_skips_general_drafting_for_response_table_only_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.config = {}
            store.company_knowledge = {
                "products": [
                    {
                        "id": "product-001",
                        "name": "数字孪生应用平台",
                        "description": "支持三维场景可视化与数据接入。",
                        "evidencePath": "m3/products/digital-twin.pdf",
                        "factKey": "technical_product",
                    }
                ]
            }
            section = {
                "sectionId": "tech-service-response",
                "title": "技术和服务要求响应表",
                "kind": "technical",
                "source": "ai",
                "responseType": "table",
                "content": "",
            }
            store.workspaces["workspace-002"] = {
                "id": "workspace-002",
                "projectId": "project-002",
                "project": {"id": "project-002", "title": "数字孪生项目"},
                "reasoning": {
                    "fineReview": {
                        "rows": [
                            {
                                "requirementId": "req-technical-002",
                                "targetModule": "technical",
                                "requirement": "平台应支持三维场景可视化。",
                                "status": "满足",
                                "matchedObjectIds": ["product-001"],
                            }
                        ]
                    }
                },
                "bidDocument": {
                    "status": "planned",
                    "plan": [section],
                    "deviationTables": {
                        "technical": [],
                        "commercial": [{"requirementId": "stale-row"}],
                    },
                },
            }
            client = FakeRequirementResponseClient()

            with (
                patch(
                    "backend.app.cloud_agent.config.DifyConfig.from_env",
                    return_value=SimpleNamespace(provider="pi"),
                ),
                patch("backend.app.pi_agent.client.PiClient", return_value=client),
                patch(
                    "backend.app.bid_writing.commercial.generate_commercial_sections",
                    return_value={},
                ) as commercial_draft,
                patch.object(store, "_refresh_company_from_m3") as refresh_m3,
                patch.object(store, "_ensure_pi_requirement_coverage") as coverage,
                patch.object(store, "_cloud_section_draft") as cloud_draft,
            ):
                result = store.generate_bid_document("workspace-002")

            self.assertIsNotNone(result)
            assert result is not None
            generated = result["bidDocument"]["plan"][0]
            self.assertIn("requirementResponseRows", generated)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(
                [
                    row["requirementId"]
                    for row in result["bidDocument"]["deviationTables"]["technical"]
                ],
                ["req-technical-002"],
            )
            self.assertEqual(result["bidDocument"]["deviationTables"]["commercial"], [])
            commercial_draft.assert_not_called()
            refresh_m3.assert_not_called()
            coverage.assert_not_called()
            cloud_draft.assert_not_called()


if __name__ == "__main__":
    unittest.main()
