from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.bid_writing.compliance import build_coverage_summary
from backend.app.pi_agent.writing_orchestrator import PiWritingOrchestrator
from backend.app.store import Store


class FakeWritingExtensionsClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="pi-extension-test-model")
        self.calls: list[str] = []

    def rewrite_section(self, **_: object) -> dict[str, object]:
        self.calls.append("SectionRewriteAgent")
        return {
            "sectionId": "t-01",
            "operation": "append",
            "replacement": "## 追加的实施步骤\n根据已核验资料补充实施安排。",
            "sourceRefs": [
                {
                    "factKey": "technical_product",
                    "factId": "product-001",
                    "name": "已登记产品",
                    "evidencePath": "materials/product-001.pdf",
                }
            ],
            "changed": True,
            "blanksToFill": [],
            "warnings": [],
        }

    def review_compliance(self, **_: object) -> dict[str, object]:
        self.calls.append("ComplianceReviewAgent")
        return {
            "status": "needs_review",
            "summary": "存在一项强制条款需要人工确认。",
            "coverage": {
                "requirementTotal": 1,
                "requirementCovered": 0,
                "mandatoryTotal": 1,
                "mandatoryCovered": 0,
                "evaluationTotal": 1,
                "evaluationCovered": 1,
                "uncoveredRequirementIds": ["req-001", "req-unknown"],
                "uncoveredEvaluationIds": [],
            },
            "riskItems": [
                {
                    "riskId": "risk-001",
                    "level": "高",
                    "category": "强制条款",
                    "title": "强制条款未覆盖",
                    "detail": "正文没有可核验响应。",
                    "sectionId": "unknown-section",
                    "requirementId": "req-unknown",
                    "evaluationId": "eval-001",
                    "suggestion": "补充响应并引用证据。",
                    "sourceRefs": [],
                }
            ],
            "warnings": [],
        }


class WritingExtensionsTests(unittest.TestCase):
    def test_section_rewrite_orchestrator_returns_trace(self) -> None:
        client = FakeWritingExtensionsClient()
        result = PiWritingOrchestrator(client).rewrite_section(
            parse_bundle={"requirements": [{"id": "req-001"}]},
            coverage=[{"requirementId": "req-001", "sectionId": "t-01"}],
            section={"sectionId": "t-01", "title": "项目理解"},
            current_content="已有正文",
            instruction="补充实施步骤",
            operation="append",
            selected_text="",
            project={},
            company={},
            knowledge={},
            mode="open",
            workspace_id="w-01",
            project_id="p-01",
        )
        self.assertEqual(client.calls, ["SectionRewriteAgent"])
        self.assertEqual(result["provider"], "pi")
        self.assertEqual(result["phase"], "section_rewrite")
        self.assertEqual(result["agentResults"][0]["agentId"], "SectionRewriteAgent")
        self.assertIn("实施步骤", result["replacement"])

    def test_compliance_orchestrator_filters_unknown_ids(self) -> None:
        client = FakeWritingExtensionsClient()
        result = PiWritingOrchestrator(client).review_compliance(
            parse_bundle={
                "requirements": [{"id": "req-001"}],
                "evaluationItems": [{"id": "eval-001"}],
            },
            bid_document={
                "plan": [{"sectionId": "t-01", "content": "正文"}],
            },
            fine_review={},
            baseline_review={},
            project={},
            company={},
            knowledge={},
            workspace_id="w-01",
            project_id="p-01",
        )
        self.assertEqual(client.calls, ["ComplianceReviewAgent"])
        self.assertEqual(result["provider"], "pi")
        self.assertEqual(result["phase"], "compliance_review")
        risk = result["riskItems"][0]
        self.assertEqual(risk["sectionId"], "")
        self.assertEqual(risk["requirementId"], "")
        self.assertEqual(risk["evaluationId"], "eval-001")

    def test_coverage_summary_counts_only_sections_with_content(self) -> None:
        summary = build_coverage_summary(
            {
                "requirements": [
                    {"id": "req-001", "mandatory": True},
                    {"id": "req-002", "mandatory": False},
                ],
                "evaluationItems": [{"id": "eval-001"}],
            },
            {
                "requirementCoverage": [
                    {"requirementId": "req-001", "sectionId": "t-01", "status": "covered"},
                    {"requirementId": "req-002", "sectionId": "t-02", "status": "covered"},
                ],
                "plan": [
                    {
                        "sectionId": "t-01",
                        "content": "已有正文",
                        "requirementIds": ["req-001"],
                        "evaluationIds": ["eval-001"],
                    },
                    {
                        "sectionId": "t-02",
                        "content": "",
                        "requirementIds": ["req-002"],
                    },
                ],
            },
        )
        self.assertEqual(summary["requirementTotal"], 2)
        self.assertEqual(summary["requirementCovered"], 1)
        self.assertEqual(summary["mandatoryCovered"], 1)
        self.assertEqual(summary["evaluationCovered"], 1)
        self.assertEqual(summary["uncoveredRequirementIds"], ["req-002"])

    def _store(self, root: Path) -> Store:
        store = Store(root)
        bundle = store.save_parse_bundle(
            {
                "parseBundleId": "parse-001",
                "schemaVersion": "1.0",
                "provider": "pi",
                "status": "completed",
                "projectId": "p-01",
                "workspaceId": "w-01",
                "sourceFile": {"filename": "tender.docx"},
                "documentBlocks": {},
                "documentStructure": {},
                "projectMeta": {"projectName": "测试项目"},
                "requirements": [
                    {"id": "req-001", "mandatory": True, "requirement": "补充技术方案"}
                ],
                "evaluationItems": [{"id": "eval-001", "criterion": "方案完整性"}],
                "bidFormatSpec": {},
                "rawOutline": [],
                "riskItems": [],
                "parseReviewReport": {},
                "artifacts": [],
                "warnings": [],
                "agentResults": [],
                "rawProviderResult": {},
            }
        )
        store.workspaces["w-01"] = {
            "id": "w-01",
            "projectId": "p-01",
            "title": "测试项目",
            "project": {"id": "p-01", "title": "测试项目"},
            "reasoning": {
                "fineReview": {
                    "rows": [
                        {
                            "requirementId": "req-001",
                            "requirement": "补充技术方案",
                            "mandatory": True,
                        }
                    ]
                }
            },
            "parseBundleRef": {
                "parseBundleId": bundle["parseBundleId"],
                "schemaVersion": bundle["schemaVersion"],
                "provider": bundle["provider"],
                "artifact": bundle["artifactPath"],
            },
            "bidDocument": {
                "status": "generated",
                "version": 1,
                "mode": "open",
                "requirementCoverage": [
                    {
                        "requirementId": "req-001",
                        "sectionId": "t-01",
                        "status": "covered",
                    }
                ],
                "plan": [
                    {
                        "sectionId": "t-01",
                        "title": "项目理解",
                        "source": "ai",
                        "kind": "technical",
                        "content": "已有正文",
                        "requirementIds": ["req-001"],
                        "evaluationIds": ["eval-001"],
                        "sourceRefs": [],
                    }
                ],
                "complianceReview": {"status": "pass"},
                "deviationTables": {"technical": [], "commercial": []},
            },
        }
        return store

    def test_store_rewrite_persists_history_and_invalidates_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            fake = FakeWritingExtensionsClient()
            with (
                patch(
                    "backend.app.cloud_agent.config.DifyConfig.from_env",
                    return_value=SimpleNamespace(provider="pi"),
                ),
                patch("backend.app.pi_agent.client.PiClient", return_value=fake),
            ):
                result = store.rewrite_bid_section(
                    "w-01",
                    "t-01",
                    "补充实施步骤",
                    operation="append",
                )
            assert result is not None
            self.assertTrue(result["rewritten"])
            section = result["section"]
            self.assertIn("实施步骤", section["content"])
            self.assertEqual(section["rewriteProvider"], "pi")
            self.assertEqual(len(section["editHistory"]), 1)
            self.assertEqual(result["bidDocument"]["complianceReview"]["status"], "stale")

    def test_store_protects_template_from_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            store.workspaces["w-01"]["bidDocument"]["plan"][0].update(
                {"source": "copied_template", "templateFragmentId": "tpl-001"}
            )
            result = store.rewrite_bid_section("w-01", "t-01", "改写固定模板")
            assert result is not None
            self.assertFalse(result["rewritten"])
            self.assertTrue(result["protected"])

    def test_store_compliance_review_persists_pi_result_and_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory))
            fake = FakeWritingExtensionsClient()
            with (
                patch(
                    "backend.app.cloud_agent.config.DifyConfig.from_env",
                    return_value=SimpleNamespace(provider="pi"),
                ),
                patch("backend.app.pi_agent.client.PiClient", return_value=fake),
            ):
                result = store.compliance_review_workspace("w-01")
            assert result is not None
            self.assertEqual(fake.calls, ["ComplianceReviewAgent"])
            self.assertEqual(result["provider"], "pi")
            self.assertEqual(result["bidDocument"]["complianceReview"]["provider"], "pi")
            self.assertTrue(result["bidDocument"]["complianceReview"]["baselineProofread"])
            self.assertEqual(
                result["bidDocument"]["complianceReview"]["riskItems"][0]["requirementId"],
                "",
            )

    def test_save_parse_bundle_writes_compact_json(self) -> None:
        import json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._store(root)
            bundle_path = (
                root / "runtime" / "projects" / "p-01" / "parse" / "parse-bundle.json"
            )
            raw = bundle_path.read_text(encoding="utf-8")
            # 紧凑写盘：无缩进换行，避免大解析产物体积翻倍。
            self.assertNotIn("\n  ", raw)
            self.assertEqual(json.loads(raw)["parseBundleId"], "parse-001")


if __name__ == "__main__":
    unittest.main()
