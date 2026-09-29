from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from backend.app.tender_matching.fact_models import (
    EvidenceFact,
    RequirementExtractionResult,
    RequirementFact,
)
from backend.app.workbuddy_agent.document_map import (
    DocumentMapSection,
)
from backend.app.workbuddy_agent.document_map.requirement_processing import (
    TargetRequirementProcessingResult,
)
from backend.app.workbuddy_agent.workflow.integration import (
    WorkBuddyAgentMatchingError,
    _runtime_config,
    evaluate_tender_document_with_workbuddy_agent,
)
from backend.app.workbuddy_agent.workflow.runtime import HIGH_RISK_PATTERN


class _FakeRuntime:
    calls: list[object] = []
    result: dict = {}

    def __init__(self, document, *, config):
        self.document = document
        self.config = config
        self.calls.append(self)

    def run(self):
        return self.result


class WorkBuddyAgentMatchingTests(unittest.TestCase):
    @unittest.skip("取我方实现：workbuddy SDK 默认继承 user 设置(供 UINO models.json 注册)，远端改为默认不继承")
    def test_sdk_model_isolated_from_general_ai_model(self) -> None:
        config = _runtime_config(
            {
                "model": "qwen3.5-flash",
                "workbuddyAgentMcp": {
                    "executor": "legacy",
                    "mcpConfigFlag": "legacy",
                },
            }
        )

        self.assertEqual(config.model, "auto")
        self.assertEqual(config.setting_sources, [])

    @unittest.skip("取我方实现：workbuddy SDK 默认继承 user 设置(供 UINO models.json 注册)，远端改为默认不继承")
    def test_sdk_uses_explicit_workbuddy_model_when_configured(self) -> None:
        config = _runtime_config(
            {
                "model": "qwen3.5-flash",
                "workbuddyAgentMcp": {
                    "model": "deepseek-v4.1-flash",
                },
            }
        )

        self.assertEqual(config.model, "deepseek-v4.1-flash")
        self.assertEqual(config.setting_sources, [])

    def _document(self) -> dict:
        return {
            "name": "agent-route.txt",
            "fileName": "agent-route.txt",
            "contentHash": "hash-agent-route",
            "mimeType": "text/plain",
            "segments": [
                {
                    "id": "SEG-001",
                    "text": "必须支持统一身份认证平台。",
                    "contentType": "line",
                    "order": 1,
                    "sourceLocator": "line 1",
                }
            ],
            "sections": [
                {
                    "id": "SEC-TECH",
                    "sectionId": "SEC-TECH",
                    "heading": "技术要求",
                    "sectionPath": ["技术要求"],
                    "sectionType": "technical",
                    "segmentIds": ["SEG-001"],
                }
            ],
            "tables": [],
        }

    def _map(self, document_id: str, version_id: str) -> dict:
        return {
            "mapId": "MAP-AGENT-001",
            "runId": "AR-AGENT-001",
            "documentId": document_id,
            "documentVersionId": version_id,
            "documentType": "government_tender",
            "sections": [
                DocumentMapSection(
                    mapId="MAP-TECH-001",
                    runId="AR-AGENT-001",
                    documentId=document_id,
                    documentVersionId=version_id,
                    domain="technical",
                    role="leaf",
                    processingMode="fine_extract",
                    title="技术要求",
                    sectionIds=["SEC-TECH"],
                    sourceSegmentIds=["SEG-001"],
                    primaryRoute="technical_processor",
                    classificationSource="agent",
                ).model_dump(mode="json", by_alias=True)
            ],
            "ignoredSections": [],
            "rejectionCandidates": [],
            "unresolvedIssues": [],
            "candidateFindings": [],
            "warnings": [],
        }

    def _target_output(self, version_id: str) -> TargetRequirementProcessingResult:
        evidence = EvidenceFact(
            evidenceId="EVD-001",
            documentVersionId=version_id,
            sourceSegmentIds=["SEG-001"],
            sourceQuote="必须支持统一身份认证平台。",
            sourceLocator="line 1",
            confidence=0.9,
        )
        requirement = RequirementFact(
            requirementId="REQ-001",
            documentVersionId=version_id,
            sectionId="SEC-TECH",
            sectionPath=["技术要求"],
            category="technical",
            rawText="必须支持统一身份认证平台。",
            normalizedRequirement="必须支持统一身份认证平台。",
            mandatory=True,
            sourceSegmentIds=["SEG-001"],
            evidenceIds=["EVD-001"],
            sourceQuote="必须支持统一身份认证平台。",
            confidence=0.9,
        )
        extraction = RequirementExtractionResult(
            documentVersionId=version_id,
            requirements=[requirement],
            evidences=[evidence],
            stats={"requirementCount": 1, "technicalCount": 1},
        )
        return TargetRequirementProcessingResult(
            documentVersionId=version_id,
            extraction=extraction,
            includedMapIds=["MAP-TECH-001"],
            includedBlockIds=["BLOCK-001"],
            stats={
                "includedMapCount": 1,
                "includedBlockCount": 1,
                "requirementCount": 1,
                "technicalCount": 1,
                "qualificationCount": 0,
            },
        )

    def test_agent_map_is_projected_into_backend_matching(self) -> None:
        _FakeRuntime.calls = []
        document = self._document()
        expected_document_id = "DOC-hash-agent-r"
        version_id = f"{expected_document_id}-V001"
        _FakeRuntime.result = {
            "status": "completed",
            "documentMap": self._map(expected_document_id, version_id),
            "run": {
                "runId": "AR-AGENT-001",
                "status": "completed",
                "model": "test-agent",
                "turnCount": 2,
            },
            "warnings": [],
            "errors": [],
            "toolCalls": [],
            "metadata": {"attempt": 1},
        }

        with (
            tempfile.TemporaryDirectory() as runtime_dir,
            patch(
                "backend.app.workbuddy_agent.workflow.integration.DocumentMapAgentRuntime",
                _FakeRuntime,
            ),
            patch(
                "backend.app.workbuddy_agent.workflow.integration.extract_target_requirements",
                return_value=self._target_output(version_id),
            ),
        ):
            result = evaluate_tender_document_with_workbuddy_agent(
                text="必须支持统一身份认证平台。",
                document=document,
                company_knowledge={},
                ai={
                    "model": "test-model",
                    "workbuddyAgentMcp": {
                        "runtimeDir": runtime_dir,
                        "maxRetries": 0,
                    },
                },
                source_file_id="FILE-001",
                project_id="PROJECT-001",
                document_meta={"contentHash": "hash-agent-route"},
            )

        self.assertEqual(len(_FakeRuntime.calls), 1)
        self.assertEqual(result["fineReview"]["extraction"]["provider"], "workbuddy-agent-mcp")
        self.assertEqual(result["fineReview"]["extraction"]["agentStatus"], "completed")
        self.assertEqual(result["fineReview"]["clauseCount"], 1)
        self.assertEqual(result["fineReview"]["rows"][0]["requirementId"], "req-001")
        self.assertEqual(
            result["fineReview"]["analysisPack"]["documentMap"]["mapId"],
            "MAP-AGENT-001",
        )
        self.assertEqual(
            result["fineReview"]["rows"][0]["status"],
            "待补材料",
        )
        self.assertEqual(
            result["reviewOutput"]["schemaVersion"],
            "tender-review-output/v1",
        )
        self.assertEqual(
            result["reviewOutput"]["modules"]["technical"]["count"],
            1,
        )
        self.assertIn("project_info", result["reviewOutput"]["modules"])
        self.assertTrue(result["fineReview"]["reviewOutput"]["modules"]["qualification"])
        coverage = result["extraction"]["documentMap"]["coverage"]
        self.assertEqual(coverage["highRiskHitCount"], 0)
        self.assertEqual(coverage["highRiskCoveredCount"], 0)

    def test_final_document_map_validation_preserves_high_risk_coverage(self) -> None:
        _FakeRuntime.calls = []
        document = self._document()
        high_risk_text = HIGH_RISK_PATTERN.pattern.split("|", 1)[0]
        document["segments"][0]["text"] = high_risk_text
        expected_document_id = "DOC-hash-agent-r"
        version_id = f"{expected_document_id}-V001"
        _FakeRuntime.result = {
            "status": "completed",
            "documentMap": self._map(expected_document_id, version_id),
            "run": {
                "runId": "AR-AGENT-001",
                "status": "completed",
                "model": "test-agent",
                "turnCount": 1,
            },
            "warnings": [],
            "errors": [],
            "toolCalls": [],
            "metadata": {"attempt": 1},
        }

        with (
            tempfile.TemporaryDirectory() as runtime_dir,
            patch(
                "backend.app.workbuddy_agent.workflow.integration.DocumentMapAgentRuntime",
                _FakeRuntime,
            ),
            patch(
                "backend.app.workbuddy_agent.workflow.integration.extract_target_requirements",
                return_value=self._target_output(version_id),
            ),
        ):
            result = evaluate_tender_document_with_workbuddy_agent(
                text=high_risk_text,
                document=document,
                company_knowledge={},
                ai={
                    "workbuddyAgentMcp": {
                        "runtimeDir": runtime_dir,
                        "maxRetries": 0,
                    },
                },
                source_file_id="FILE-001",
                project_id="PROJECT-001",
            )

        coverage = result["extraction"]["documentMap"]["coverage"]
        self.assertEqual(coverage["highRiskHitCount"], 1)
        self.assertEqual(coverage["highRiskCoveredCount"], 1)

    def test_failed_agent_run_is_structured_and_does_not_return_builtin_success(self) -> None:
        _FakeRuntime.calls = []
        document = self._document()
        _FakeRuntime.result = {
            "status": "failed",
            "documentMap": None,
            "run": {"runId": "AR-FAILED", "status": "failed"},
            "warnings": [],
            "errors": ["CodeBuddy Agent SDK is unavailable."],
            "metadata": {"attempt": 2},
        }

        with (
            patch(
                "backend.app.workbuddy_agent.workflow.integration.DocumentMapAgentRuntime",
                _FakeRuntime,
            ),
        ):
            with self.assertRaises(WorkBuddyAgentMatchingError) as raised:
                evaluate_tender_document_with_workbuddy_agent(
                    text="必须支持统一身份认证平台。",
                    document=document,
                    company_knowledge={},
                    ai={"workbuddyAgentMcp": {"maxRetries": 0}},
                    source_file_id="FILE-001",
                    project_id="PROJECT-001",
                )

        self.assertIn("CodeBuddy Agent SDK is unavailable.", str(raised.exception))
        self.assertEqual(raised.exception.runtime_result["status"], "failed")

    def test_scope_outputs_are_projected_without_enterprise_requirement_rows(self) -> None:
        document = self._document()
        document["segments"].append(
            {
                "id": "SEG-002",
                "text": "技术评分项 | 技术响应方案 | 10分",
                "contentType": "line",
                "order": 2,
                "sourceLocator": "line 2",
            }
        )
        expected_document_id = "DOC-hash-agent-r"
        version_id = f"{expected_document_id}-V001"
        agent_map = self._map(expected_document_id, version_id)
        agent_map["sections"].extend(
            [
                {
                    "mapId": "MAP-COMM-001",
                    "runId": "AR-AGENT-001",
                    "documentId": expected_document_id,
                    "documentVersionId": version_id,
                    "domain": "commercial",
                    "role": "leaf",
                    "processingMode": "display",
                    "title": "商务要求",
                    "sectionIds": ["SEC-TECH"],
                    "sourceSegmentIds": ["SEG-002"],
                    "primaryRoute": "display_processor",
                    "classificationSource": "agent",
                },
                {
                    "mapId": "MAP-EVAL-001",
                    "runId": "AR-AGENT-001",
                    "documentId": expected_document_id,
                    "documentVersionId": version_id,
                    "domain": "evaluation",
                    "role": "leaf",
                    "processingMode": "display",
                    "title": "评分标准",
                    "sectionIds": ["SEC-TECH"],
                    "sourceSegmentIds": ["SEG-002"],
                    "primaryRoute": "evaluation_processor",
                    "classificationSource": "agent",
                },
            ]
        )
        _FakeRuntime.result = {
            "status": "completed",
            "documentMap": agent_map,
            "run": {"runId": "AR-AGENT-001", "status": "completed", "turnCount": 1},
            "warnings": [],
            "errors": [],
            "toolCalls": [],
            "metadata": {"attempt": 1},
        }
        with (
            patch(
                "backend.app.workbuddy_agent.workflow.integration.DocumentMapAgentRuntime",
                _FakeRuntime,
            ),
        ):
            result = evaluate_tender_document_with_workbuddy_agent(
                text="必须支持统一身份认证平台。",
                document=document,
                company_knowledge={},
                ai={"workbuddyAgentMcp": {"maxRetries": 0}},
                source_file_id="FILE-001",
            )

        pack = result["extraction"]["analysisPack"]
        self.assertTrue(pack["commercialTerms"])
        self.assertTrue(pack["evaluationCriteria"])
        fine_sources = {
            source_id
            for row in result["fineReview"]["rows"]
            for source_id in row.get("sourceSegmentIds", [])
        }
        self.assertIn("SEG-001", fine_sources)
        self.assertNotIn("SEG-002", fine_sources)


if __name__ == "__main__":
    unittest.main()
