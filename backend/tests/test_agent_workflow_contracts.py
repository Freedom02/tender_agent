from __future__ import annotations

import unittest

from pydantic import ValidationError

from backend.app.workbuddy_agent.workflow.handoff import (
    build_agent_review_input,
    build_writing_brief,
    project_agent_result_to_fine_review,
)
from backend.app.workbuddy_agent.workflow.integration import (
    build_review_output_from_analysis_pack,
)
from backend.app.workbuddy_agent.workflow.schemas import (
    AGENT_OUTPUT_CONTRACT_VERSION,
    AGENT_SKILL_VERSION,
    AgentRun,
    FeedbackEvent,
    SectionOverlay,
    ToolCall,
    WritingBrief,
)


class AgentWorkflowContractTests(unittest.TestCase):
    def test_agent_run_uses_aliases_and_versions(self) -> None:
        run = AgentRun(
            runId="AR-001",
            documentId="DOC-001",
            documentVersionId="DOC-001-V001",
            projectId="PROJECT-001",
            status="running",
            phase="section_review",
            model="test-model",
            turnCount=2,
            toolCallCount=3,
        )

        payload = run.model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["runId"], "AR-001")
        self.assertEqual(payload["documentVersionId"], "DOC-001-V001")
        self.assertEqual(payload["skillVersion"], AGENT_SKILL_VERSION)
        self.assertEqual(payload["turnCount"], 2)
        self.assertNotIn("run_id", payload)

    def test_contracts_reject_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            ToolCall(
                callId="CALL-001",
                runId="AR-001",
                toolName="inspect_document",
                unexpected=True,
            )

    def test_section_overlay_and_feedback_keep_auditable_fields(self) -> None:
        overlay = SectionOverlay(
            sectionId="SEC-001",
            primaryType="technical",
            typeHits=["technical", "document_format"],
            primaryRoute="company_match",
            secondaryRoutes=["writing_context"],
            confidence=0.91,
            needsHumanReview=False,
            reviewStatus="not_required",
            reason="技术参数章节，需要保留产品上下文。",
            sourceSegmentIds=["SEG-001"],
        )
        feedback = FeedbackEvent(
            feedbackId="FB-001",
            runId="AR-001",
            documentId="DOC-001",
            objectType="section",
            objectId="SEC-001",
            before={"primaryType": "technical"},
            after={"primaryType": "service_plan"},
            reason="该章节实际是服务方案要求。",
            reviewer="tester",
            approvedForLearning=False,
        )

        self.assertEqual(overlay.model_dump(by_alias=True)["sectionId"], "SEC-001")
        self.assertFalse(feedback.approved_for_learning)
        self.assertEqual(feedback.scope, "case")

    def test_analysis_pack_projects_to_writing_brief(self) -> None:
        pack = {
            "schemaVersion": "tender-analysis/1.0",
            "source": {
                "sourceFileId": "FILE-001",
                "sourceFileHash": "HASH-001",
                "document": {
                    "documentId": "DOC-001",
                    "documentVersionId": "DOC-001-V001",
                },
            },
            "projectInfo": [
                {
                    "field": "projectName",
                    "text": "城市更新数字平台项目",
                    "source": {"segmentIds": ["SEG-001"]},
                }
            ],
            "requirements": [
                {
                    "requirementId": "REQ-001",
                    "title": "技术响应",
                    "normalizedText": "平台应支持统一身份认证。",
                    "category": "technical",
                    "handlingMode": "database_match",
                    "mandatory": True,
                    "source": {"segmentIds": ["SEG-002"], "sourceLocator": "第2页"},
                }
            ],
            "evaluationCriteria": [],
            "submissionItems": [
                {
                    "submissionItemId": "SUB-001",
                    "itemName": "响应文件须加盖公章",
                    "handlingMode": "checklist",
                }
            ],
            "milestones": [
                {
                    "field": "bidDeadline",
                    "text": "投标截止时间：2026年10月12日 09:30",
                }
            ],
            "disqualificationRules": [],
            "templateIndex": [
                {
                    "templateId": "TPL-001",
                    "title": "投标函",
                    "source": {"sectionId": "SEC-TPL", "segmentIds": ["SEG-003"]},
                }
            ],
        }
        fine_review = {
            "rows": [
                {
                    "requirementId": "REQ-001",
                    "requirement": "平台应支持统一身份认证。",
                    "status": "待补材料",
                    "evidenceItems": [
                        {
                            "evidenceId": "EVD-001",
                            "name": "产品参数表",
                            "status": "已核验",
                            "sourceLocator": "企业材料/产品参数表.docx",
                        }
                    ],
                }
            ],
            "pendingMaterialList": [
                {"requirementId": "REQ-001", "detail": "补充产品参数证明"}
            ],
        }

        brief = build_writing_brief(
            pack,
            fine_review=fine_review,
            project={"title": "城市更新数字平台项目"},
            project_id="PROJECT-001",
        )

        payload = brief.model_dump(mode="json", by_alias=True)
        self.assertIsInstance(brief, WritingBrief)
        self.assertEqual(payload["sourceFileId"], "FILE-001")
        self.assertEqual(payload["templates"][0]["templateId"], "TPL-001")
        self.assertEqual(payload["enterpriseEvidence"][0]["evidenceId"], "EVD-001")
        self.assertEqual(payload["responsePlan"][0]["requirementId"], "REQ-001")
        self.assertEqual(payload["unresolvedItems"][0]["requirementId"], "REQ-001")
        self.assertNotIn("fineReview", payload)
        self.assertFalse(payload["metadata"]["containsHiddenReasoning"])

    def test_agent_result_projects_back_to_existing_fine_review_shape(self) -> None:
        result = project_agent_result_to_fine_review(
            {
                "modelUsed": "test-agent",
                "requirements": [
                    {
                        "id": "REQ-001",
                        "category": "document",
                        "rawText": "投标文件须加盖单位公章。",
                        "sourceSegmentIds": ["SEG-001"],
                        "sourceLocator": "第1页",
                        "handlingMode": "checklist",
                        "displayGroup": "checklist",
                        "mandatory": True,
                    }
                ],
                "analysisPack": {"schemaVersion": "tender-analysis/1.0"},
            },
            source_file_id="FILE-001",
        )

        self.assertEqual(
            {
                "rows",
                "risks",
                "score",
                "decision",
                "recommendation",
                "clauseCount",
                "statusCounts",
                "classCounts",
                "summary",
                "hardBlockers",
                "rejectionRisks",
                "unresolvedMandatory",
                "pendingMaterialList",
                "analysisPack",
                "extraction",
            },
            set(result),
        )
        self.assertEqual(result["clauseCount"], 1)
        self.assertEqual(result["rows"][0]["requirementId"], "REQ-001")
        self.assertEqual(result["rows"][0]["status"], "待确认")
        self.assertEqual(result["extraction"]["provider"], "workbuddy-agent")
        self.assertEqual(result["analysisPack"]["schemaVersion"], "tender-analysis/1.0")

    def test_historical_fine_review_without_analysis_requirements_builds_brief(self) -> None:
        brief = build_writing_brief(
            {"schemaVersion": "tender-analysis/1.0"},
            fine_review={
                "rows": [
                    {
                        "id": "REQ-OLD-001",
                        "requirement": "投标文件须加盖单位公章。",
                        "category": "document",
                        "requirementClass": "compliance",
                        "handlingMode": "checklist",
                        "status": "待确认",
                        "sourceSegmentIds": ["SEG-OLD-001"],
                        "sourceLocator": "第8页",
                    }
                ]
            },
            project_id="PROJECT-OLD",
        )

        payload = brief.model_dump(mode="json", by_alias=True)
        self.assertEqual(payload["requirements"][0]["requirementId"], "REQ-OLD-001")
        self.assertEqual(payload["responsePlan"][0]["requirementId"], "REQ-OLD-001")
        self.assertEqual(
            payload["responsePlan"][0]["source"]["segmentIds"],
            ["SEG-OLD-001"],
        )

    def test_review_input_is_compact_and_reuses_run_contract(self) -> None:
        run = AgentRun(
            runId="AR-001",
            documentId="DOC-001",
            documentVersionId="DOC-001-V001",
        )
        envelope = build_agent_review_input(
            run=run,
            fine_review={
                "rows": [
                    {
                        "id": "REQ-001",
                        "requirementId": "REQ-001",
                        "requirement": "投标文件须加盖单位公章。",
                        "category": "document",
                        "sourceSegmentIds": ["SEG-001"],
                        "status": "待确认",
                    }
                ],
                "analysisPack": {"schemaVersion": "tender-analysis/1.0"},
            },
            document_summary={"segmentCount": 1},
        )

        self.assertEqual(envelope["contractVersion"], AGENT_OUTPUT_CONTRACT_VERSION)
        self.assertEqual(envelope["run"]["runId"], "AR-001")
        self.assertEqual(envelope["requirements"][0]["source"]["sourceSegmentIds"], ["SEG-001"])
        self.assertEqual(envelope["analysisPack"]["schemaVersion"], "tender-analysis/1.0")
        self.assertNotIn("rows", envelope["requirements"][0])

    def test_builtin_target_modules_project_to_display_and_match_domains(self) -> None:
        fine_review = {
            "rows": [],
            "analysisPack": {
                "targetModules": [
                    {
                        "targetModule": "technical",
                        "items": [
                            {
                                "id": "technical-001",
                                "handlingMode": "match",
                                "sourceSegmentIds": ["SEG-TECH"],
                                "text": "平台支持三维场景",
                            }
                        ],
                    },
                    {
                        "targetModule": "qualification",
                        "items": [
                            {
                                "id": "qualification-001",
                                "handlingMode": "match",
                                "sourceSegmentIds": ["SEG-QUAL"],
                                "text": "具有独立承担民事责任的能力",
                            }
                        ],
                    },
                    {
                        "targetModule": "commercial",
                        "items": [
                            {
                                "id": "commercial-001",
                                "handlingMode": "display",
                                "sourceSegmentIds": ["SEG-COMM"],
                                "text": "合同签订后 60 日内交货",
                            }
                        ],
                    },
                    {
                        "targetModule": "evaluation",
                        "items": [
                            {
                                "id": "evaluation-001",
                                "handlingMode": "display",
                                "sourceSegmentIds": ["SEG-EVAL"],
                                "text": "技术评分 30 分",
                            }
                        ],
                    },
                    {
                        "targetModule": "rejection",
                        "items": [
                            {
                                "id": "rejection-001",
                                "handlingMode": "risk_check",
                                "sourceSegmentIds": ["SEG-REJ"],
                                "text": "未按要求签章的，投标无效。",
                                "isRejection": True,
                            }
                        ],
                    },
                    {
                        "targetModule": "contract",
                        "items": [],
                    },
                ]
            },
        }

        output = build_review_output_from_analysis_pack(fine_review)
        modules = output["modules"]

        self.assertEqual(modules["technical"]["count"], 1)
        self.assertEqual(modules["technical"]["primaryRoute"], "company_match")
        self.assertEqual(modules["technical"]["items"][0]["handlingMode"], "database_match")
        self.assertEqual(modules["qualification"]["count"], 1)
        self.assertEqual(modules["commercial"]["count"], 1)
        self.assertEqual(modules["commercial"]["primaryRoute"], "display")
        self.assertEqual(modules["evaluation"]["count"], 1)
        self.assertEqual(modules["evaluation"]["primaryRoute"], "display")
        self.assertEqual(modules["contract"]["count"], 0)
        self.assertTrue(modules["contract"]["ignored"])
        self.assertEqual(
            output["matchStage"]["targetModules"],
            ["technical", "qualification", "rejection"],
        )
        self.assertEqual(
            output["matchStage"]["enterpriseMatchTargets"],
            ["technical", "qualification"],
        )
        self.assertEqual(output["matchStage"]["riskCheckTargets"], ["rejection"])
        self.assertEqual(output["matchStage"]["enterpriseMatchCount"], 2)
        self.assertEqual(output["matchStage"]["riskCheckCount"], 1)
        self.assertEqual(
            modules["rejection"]["primaryRoute"],
            "rejection_check",
        )
        self.assertEqual(
            modules["rejection"]["items"][0]["handlingMode"],
            "risk_check",
        )


if __name__ == "__main__":
    unittest.main()
