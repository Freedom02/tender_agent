from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.cloud_agent.config import DifyConfig
from backend.app.cloud_agent.dify_client import DifyClient, DifyError
from backend.app.cloud_agent.normalizers import normalize_parse_result, normalize_section_draft_result
from backend.app.cloud_agent.run_service import CloudRunService
from backend.app.agent_runtime.contracts import (
    AgentResultEnvelope,
    ParseBundle,
    build_agent_result,
    build_parse_bundle,
)
from backend.app.pi_agent.client import PiError, parse_json_text
from backend.app.pi_agent.orchestrator import PiParseOrchestrationError, PiParseOrchestrator
from backend.app.pi_agent.writing_orchestrator import (
    PiWritingOrchestrationError,
    PiWritingOrchestrator,
)
from backend.app.store import Store


def _config(**overrides: object) -> DifyConfig:
    values: dict[str, object] = {
        "provider": "dify",
        "base_url": "https://dify.example.test/v1",
        "api_key": "app-key",
        "parse_workflow_key": "parse-key",
        "section_workflow_key": "",
        "review_workflow_key": "",
        "timeout_seconds": 1.0,
        "enabled": True,
    }
    values.update(overrides)
    return DifyConfig(**values)


class FakeClient:
    def __init__(self, response: dict[str, object] | None = None, error: Exception | None = None):
        self.response = response or {
            "workflow_run_id": "workflow-1",
            "data": {
                "outputs": {
                    "projectMeta": {"projectName": "智慧园区项目"},
                    "outline": [
                        {
                            "sectionId": "s-01",
                            "title": "投标函",
                            "kind": "commercial",
                            "source": "generated",
                            "requirementIds": [],
                        }
                    ],
                    "requirements": [
                        {
                            "id": "req-001",
                            "requirement": "具有有效营业执照",
                            "mandatory": True,
                        }
                    ],
                }
            },
        }
        self.error = error

    def parse_tender(self, *args: object, **kwargs: object) -> dict[str, object]:
        if self.error:
            raise self.error
        return self.response


class FakePiClient(FakeClient):
    pass


class FakeParseOrchestrationClient:
    def __init__(self, failures: set[str] | None = None):
        self.config = SimpleNamespace(model="pi-test-model")
        self.failures = failures or set()
        self.calls: list[str] = []
        self.detect_document_structures: list[dict[str, object]] = []

    def _parse_file(self, path: Path) -> dict[str, object]:
        return {
            "text": path.read_text(encoding="utf-8"),
            "segments": [{"id": "block-001", "text": "招标文件正文"}],
            "units": [{"id": "block-001", "text": "招标文件正文"}],
            "tables": [],
            "images": [],
            "sections": [
                {
                    "id": "local-001",
                    "title": "第三章 投标文件格式",
                    "level": 1,
                    "sourceLocator": "第3页",
                }
            ],
            "warnings": [],
        }

    def _result(self, agent_id: str, value: dict[str, object]) -> dict[str, object]:
        self.calls.append(agent_id)
        if agent_id in self.failures:
            raise RuntimeError(f"{agent_id} failed")
        return value

    def parse_document_structure(self, **_: object) -> dict[str, object]:
        return self._result(
            "DocumentStructureAgent",
            {
                "sections": [
                    {
                        "sectionId": "doc-sec-001",
                        "title": "第三章 投标文件格式",
                        "level": 1,
                        "sourceLocator": "第3页",
                        "sectionType": "document_format",
                        "blockIds": ["block-001"],
                    }
                ],
                "warnings": [],
            },
        )

    def extract_project_meta(self, **_: object) -> dict[str, object]:
        return self._result(
            "ProjectMetaAgent",
            {"projectMeta": {"projectName": "Pi 编排测试项目"}, "warnings": []},
        )

    def extract_requirements(self, **_: object) -> dict[str, object]:
        return self._result(
            "RequirementExtractAgent",
            {
                "requirements": [
                    {
                        "id": "req-001",
                        "category": "technical",
                        "requirement": "提供完整技术方案",
                        "mandatory": True,
                        "sourceLocator": "第三章第1条",
                    }
                ],
                "warnings": [],
            },
        )

    def extract_evaluations(self, **_: object) -> dict[str, object]:
        return self._result(
            "EvaluationExtractAgent",
            {
                "evaluationItems": [
                    {
                        "id": "eval-001",
                        "criterion": "技术方案完整性",
                        "score": 10,
                        "relatedRequirementIds": ["req-001"],
                    }
                ],
                "warnings": [],
            },
        )

    def detect_templates(self, **kwargs: object) -> dict[str, object]:
        structure = kwargs.get("document_structure")
        if isinstance(structure, dict):
            self.detect_document_structures.append(structure)
        return self._result(
            "TemplateDetectAgent",
            {
                "hasExplicitFormat": True,
                "formatSourceSection": "第三章 投标文件格式",
                "requiredOutline": [
                    {
                        "formatItemId": "fmt-001",
                        "title": "技术方案",
                        "type": "free_text",
                        "templateId": "tpl-001",
                    }
                ],
                "templates": [
                    {
                        "templateId": "tpl-001",
                        "title": "技术方案模板",
                        "templateType": "chapter_skeleton",
                    }
                ],
                "requiredTables": [],
                "warnings": [],
            },
        )

    def review_parse(self, **_: object) -> dict[str, object]:
        return self._result(
            "ParseReviewAgent",
            {
                "status": "pass_with_warnings",
                "confidence": 0.91,
                "missingFields": [],
                "riskItems": [],
                "warnings": [],
            },
        )


class FakeWritingOrchestrationClient:
    def __init__(self, *, fail_outline: bool = False, fail_mapping: bool = False):
        self.config = SimpleNamespace(model="pi-writing-test-model")
        self.fail_outline = fail_outline
        self.fail_mapping = fail_mapping
        self.calls: list[str] = []

    def plan_volumes_agent(self, **_: object) -> dict[str, object]:
        self.calls.append("VolumePlanAgent")
        return {
            "volumes": [{"volumeId": "volume-main", "title": "投标响应文件", "order": 1}],
            "warnings": [],
        }

    def plan_outline_agent(self, **_: object) -> dict[str, object]:
        self.calls.append("OutlinePlanAgent")
        if self.fail_outline:
            raise RuntimeError("outline failed")
        return {
            "plan": [
                {
                    "sectionId": "t-01",
                    "title": "技术方案",
                    "source": "ai",
                    "kind": "technical",
                    "requirementIds": ["req-001"],
                    "evaluationIds": ["eval-001"],
                }
            ],
            "warnings": [],
        }

    def map_requirements(self, **_: object) -> dict[str, object]:
        self.calls.append("RequirementMapAgent")
        if self.fail_mapping:
            raise RuntimeError("mapping failed")
        return {
            "coverage": [
                {
                    "requirementId": "req-001",
                    "sectionId": "t-01",
                    "coverageType": "chapter_text",
                    "status": "planned",
                    "evaluationIds": ["eval-001"],
                    "reason": "技术方案章节逐条响应",
                }
            ],
            "uncoveredRequirements": [],
            "duplicatedRequirements": [],
            "warnings": [],
        }


class FakeSectionWritingClient:
    def __init__(self, *, fail_material: bool = False, fail_draft: bool = False):
        self.config = SimpleNamespace(model="pi-section-test-model")
        self.fail_material = fail_material
        self.fail_draft = fail_draft
        self.calls: list[str] = []

    def match_materials(self, **_: object) -> dict[str, object]:
        self.calls.append("MaterialMatchAgent")
        if self.fail_material:
            raise RuntimeError("material matching failed")
        return {
            "matches": [
                {
                    "requirementId": "req-001",
                    "materialIds": ["qualification-001"],
                    "materialRefs": [
                        {
                            "materialId": "qualification-001",
                            "factKey": "qualifications",
                            "name": "质量管理体系认证",
                            "evidencePath": "materials/qualification-001.pdf",
                        }
                    ],
                    "matchStatus": "matched",
                    "evidenceSummary": "存在有效认证材料",
                }
            ],
            "unmatchedRequirementIds": [],
            "warnings": [],
        }

    def plan_section(self, **_: object) -> dict[str, object]:
        self.calls.append("SectionPlanAgent")
        return {
            "sectionId": "t-02",
            "title": "技术方案",
            "objective": "逐条响应技术要求",
            "requirementResponses": [
                {
                    "requirementId": "req-001",
                    "responseStrategy": "说明技术方案并引用材料",
                    "materialIds": ["qualification-001"],
                }
            ],
            "evaluationResponses": [],
            "outline": [{"heading": "1.1 技术响应", "purpose": "响应要求"}],
            "templateInstructions": [],
            "warnings": [],
        }

    def draft_section_agent(self, **_: object) -> dict[str, object]:
        self.calls.append("SectionDraftAgent")
        if self.fail_draft:
            raise RuntimeError("draft failed")
        return {
            "sectionId": "t-02",
            "title": "技术方案",
            "content": "## 技术响应\n\n根据招标文件要求编制技术方案。",
            "sourceRefs": [
                {
                    "materialId": "qualification-001",
                    "factKey": "qualifications",
                    "name": "质量管理体系认证",
                    "evidencePath": "materials/qualification-001.pdf",
                }
            ],
            "status": "generated",
            "warnings": [],
        }


class CloudAgentTests(unittest.TestCase):
    def _run_with_result(self) -> dict[str, object]:
        return {
            "runId": "bid-run-test",
            "workspaceId": "",
            "projectId": "",
            "status": "outline_ready",
            "progress": 100,
            "originalFilename": "智慧园区招标文件.pdf",
            "mimeType": "application/pdf",
            "inputArtifact": "",
            "result": {
                "projectMeta": {
                    "projectName": "智慧园区项目",
                    "projectCode": "SP-2026",
                    "tendererName": "示例采购人",
                    "budget": "100万元",
                    "deadline": "2026-10-01 09:30",
                },
                "outline": [
                    {"sectionId": "x-1", "title": "投标函", "kind": "commercial", "source": "generated"},
                    {"sectionId": "x-2", "title": "技术方案", "kind": "technical", "source": "generated"},
                ],
                "requirements": [
                    {
                        "id": "req-001",
                        "category": "qualification",
                        "requirement": "投标人须具有有效营业执照",
                        "mandatory": True,
                        "sourceLocator": "第三章 资格要求",
                        "status": "待补材料",
                    }
                ],
                "warnings": [],
            },
        }

    def test_normalizer_accepts_dify_outputs_wrapper(self) -> None:
        result = normalize_parse_result(
            {
                "data": {
                    "outputs": {
                        "projectMeta": {"projectName": "测试项目"},
                        "outline": [
                            {
                                "sectionId": "s-01",
                                "title": "技术方案",
                            }
                        ],
                    }
                }
            }
        )

        self.assertEqual(result.projectMeta.projectName, "测试项目")
        self.assertEqual(result.outline[0].sectionId, "s-01")

    def test_section_draft_normalizer_accepts_common_output_keys(self) -> None:
        result = normalize_section_draft_result(
            {
                "data": {
                    "outputs": {
                        "result": json.dumps(
                            {
                                "markdown": "## 技术方案\n\n云端正文",
                                "source_refs": [{"name": "能力材料"}],
                                "materialMatches": [{"requirementId": "req-001"}],
                                "writingPlan": {"sectionId": "t-02"},
                                "agentResults": [{"agentId": "SectionDraftAgent"}],
                                "provider": "pi",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            },
            section={"sectionId": "t-02", "title": "技术方案"},
        )

        self.assertEqual(result.sectionId, "t-02")
        self.assertIn("云端正文", result.content)
        self.assertEqual(result.sourceRefs[0]["name"], "能力材料")
        self.assertEqual(result.materialMatches[0]["requirementId"], "req-001")
        self.assertEqual(result.writingPlan["sectionId"], "t-02")
        self.assertEqual(result.agentResults[0]["agentId"], "SectionDraftAgent")
        self.assertEqual(result.provider, "pi")

    def test_pi_json_parser_accepts_code_fence(self) -> None:
        result = parse_json_text('```json\n{"projectMeta":{"projectName":"Pi项目"},"outline":[],"requirements":[]}\n```')

        self.assertEqual(result["projectMeta"]["projectName"], "Pi项目")

    def test_agent_result_envelope_has_trace_fields(self) -> None:
        result = build_agent_result(
            agent_id="RequirementExtractAgent",
            provider="pi",
            output={"requirements": [{"id": "req-001"}]},
            input_refs=["parse:documentBlocks"],
            warnings=["需要人工复核"],
            confidence=0.8,
            run_id="agent-run-001",
        )

        validated = AgentResultEnvelope.model_validate(result)
        self.assertEqual(validated.agentId, "RequirementExtractAgent")
        self.assertEqual(validated.provider, "pi")
        self.assertEqual(validated.inputRefs, ["parse:documentBlocks"])
        self.assertEqual(validated.warnings, ["需要人工复核"])

    def test_parse_bundle_builder_uses_standard_sections(self) -> None:
        bundle = build_parse_bundle(
            run={
                "runId": "bid-run-001",
                "provider": "pi",
                "projectId": "project-001",
                "workspaceId": "workspace-001",
                "originalFilename": "招标文件.docx",
                "inputArtifact": "runtime/projects/project-001/source/招标文件.docx",
            },
            result={
                "projectMeta": {"projectName": "测试项目"},
                "outline": [{"sectionId": "t-01", "title": "技术方案"}],
                "requirements": [{"id": "req-001", "requirement": "提供技术方案"}],
                "evaluationItems": [{"id": "eval-001", "score": 8}],
                "documentBlocks": {"text": "正文"},
                "documentStructure": {"sections": [{"sectionId": "doc-001"}]},
                "bidFormatSpec": {"hasExplicitFormat": True},
                "parseReviewReport": {"status": "pass"},
                "agentResults": [{"agentId": "DocumentStructureAgent", "status": "success"}],
                "warnings": ["请复核页码"],
            },
        )

        validated = ParseBundle.model_validate(bundle)
        self.assertEqual(validated.provider, "pi")
        self.assertEqual(validated.projectMeta["projectName"], "测试项目")
        self.assertEqual(validated.rawOutline[0]["sectionId"], "t-01")
        self.assertEqual(validated.documentBlocks["text"], "正文")
        self.assertEqual(validated.documentStructure["sections"][0]["sectionId"], "doc-001")
        self.assertTrue(validated.bidFormatSpec["hasExplicitFormat"])
        self.assertEqual(validated.parseReviewReport["status"], "pass")
        self.assertEqual(validated.agentResults[0]["agentId"], "DocumentStructureAgent")

    def test_pi_parse_orchestrator_runs_six_agents_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tender.txt"
            path.write_text(
                "第七篇 投标文件格式\n"
                "一、商务文件\n"
                "（一）投标函（格式）\n"
                "二、技术文件\n"
                "（一）技术方案",
                encoding="utf-8",
            )
            client = FakeParseOrchestrationClient()
            progress_events: list[tuple[str, str, int, int]] = []

            result = PiParseOrchestrator(client).parse_tender(
                path,
                workspace_id="workspace-1",
                project_id="project-1",
                project_context={"source": "test"},
                progress_callback=lambda *args: progress_events.append(args),
            )

        self.assertEqual(
            client.calls,
            [
                "DocumentStructureAgent",
                "ProjectMetaAgent",
                "RequirementExtractAgent",
                "EvaluationExtractAgent",
                "TemplateDetectAgent",
                "ParseReviewAgent",
            ],
        )
        self.assertEqual(result["provider"], "pi")
        self.assertEqual(result["projectMeta"]["projectName"], "Pi 编排测试项目")
        self.assertEqual(result["requirements"][0]["id"], "req-001")
        self.assertEqual(result["evaluationItems"][0]["id"], "eval-001")
        self.assertTrue(result["bidFormatSpec"]["hasExplicitFormat"])
        self.assertEqual(
            [
                volume["title"]
                for volume in client.detect_document_structures[0]["submissionStructure"]["volumes"]
            ],
            ["商务文件", "技术文件"],
        )
        self.assertEqual(result["parseReviewReport"]["status"], "pass_with_warnings")
        self.assertEqual(
            [item["agentId"] for item in result["agentResults"]],
            client.calls,
        )
        self.assertEqual(len(progress_events), 13)
        self.assertEqual(
            progress_events[0],
            ("DocumentStructureAgent", "running", 0, 6),
        )
        self.assertIn(
            ("RequirementExtractAgent", "running:1/1", 2, 6),
            progress_events,
        )
        self.assertEqual(
            progress_events[-1],
            ("ParseReviewAgent", "success", 6, 6),
        )

    def test_pi_parse_orchestrator_continues_after_one_agent_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tender.txt"
            path.write_text("招标文件正文", encoding="utf-8")
            client = FakeParseOrchestrationClient({"RequirementExtractAgent"})

            result = PiParseOrchestrator(client).parse_tender(
                path,
                workspace_id="workspace-1",
                project_id="project-1",
                project_context={},
            )

        self.assertEqual(len(client.calls), 6)
        self.assertEqual(
            result["agentResults"][2]["agentId"],
            "RequirementExtractAgent",
        )
        self.assertEqual(result["agentResults"][2]["status"], "failed")
        self.assertEqual(result["requirements"], [])
        self.assertEqual(result["evaluationItems"][0]["id"], "eval-001")
        self.assertTrue(any("RequirementExtractAgent 执行失败" in item for item in result["warnings"]))

    def test_pi_parse_orchestrator_ignores_progress_callback_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tender.txt"
            path.write_text("招标文件正文", encoding="utf-8")
            client = FakeParseOrchestrationClient()

            result = PiParseOrchestrator(client).parse_tender(
                path,
                workspace_id="workspace-1",
                project_id="project-1",
                project_context={},
                progress_callback=lambda *_args: (_ for _ in ()).throw(
                    RuntimeError("progress store unavailable")
                ),
            )

        self.assertEqual(len(result["agentResults"]), 6)
        self.assertTrue(all(item["status"] == "success" for item in result["agentResults"]))

    def test_pi_parse_orchestrator_retries_transient_pi_error_once(self) -> None:
        class TransientReviewClient(FakeParseOrchestrationClient):
            def __init__(self) -> None:
                super().__init__()
                self.review_attempts = 0

            def review_parse(self, **kwargs: object) -> dict[str, object]:
                self.review_attempts += 1
                if self.review_attempts == 1:
                    raise PiError("temporary timeout")
                return super().review_parse(**kwargs)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tender.txt"
            path.write_text("招标文件正文", encoding="utf-8")
            client = TransientReviewClient()

            result = PiParseOrchestrator(client).parse_tender(
                path,
                workspace_id="workspace-1",
                project_id="project-1",
                project_context={},
            )

        self.assertEqual(client.review_attempts, 2)
        review_result = result["agentResults"][-1]
        self.assertEqual(review_result["status"], "success")
        self.assertTrue(any("自动重试" in item for item in review_result["warnings"]))

    def test_pi_parse_orchestrator_fails_when_all_agents_fail(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tender.txt"
            path.write_text("招标文件正文", encoding="utf-8")
            client = FakeParseOrchestrationClient(
                {
                    "DocumentStructureAgent",
                    "ProjectMetaAgent",
                    "RequirementExtractAgent",
                    "EvaluationExtractAgent",
                    "TemplateDetectAgent",
                    "ParseReviewAgent",
                }
            )

            with self.assertRaises(PiParseOrchestrationError):
                PiParseOrchestrator(client).parse_tender(
                    path,
                    workspace_id="workspace-1",
                    project_id="project-1",
                    project_context={},
                )

    def test_pi_writing_orchestrator_generates_outline_without_requirement_mapping(self) -> None:
        client = FakeWritingOrchestrationClient()
        result = PiWritingOrchestrator(client).plan_outline(
            parse_bundle={
                "parseBundleId": "parse-001",
                "requirements": [{"id": "req-001", "requirement": "提供技术方案"}],
                "evaluationItems": [{"id": "eval-001", "criterion": "完整性"}],
            },
            existing_plan=[],
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertEqual(client.calls, ["VolumePlanAgent", "OutlinePlanAgent"])
        self.assertEqual(result["plan"][0]["sectionId"], "volume-main")
        self.assertEqual(result["plan"][1]["sectionId"], "t-01")
        self.assertEqual(result["coverage"], [])
        self.assertEqual(result["requirementMappingStatus"], "pending")
        self.assertEqual(
            [item["agentId"] for item in result["agentResults"]],
            client.calls,
        )

    def test_pi_writing_orchestrator_retries_overexpanded_outline(self) -> None:
        class OversizedOutlineClient(FakeWritingOrchestrationClient):
            def __init__(self) -> None:
                super().__init__()
                self.outline_attempts = 0

            def plan_outline_agent(self, **_: object) -> dict[str, object]:
                self.outline_attempts += 1
                self.calls.append(
                    "OutlinePlanAgent"
                    if self.outline_attempts == 1
                    else "OutlinePlanAgent[质量修正]"
                )
                rows: list[dict[str, object]] = [
                    {
                        "sectionId": "volume-main",
                        "title": "投标响应文件",
                        "nodeType": "volume",
                        "responseType": "section_group",
                        "level": 1,
                        "source": "template",
                        "kind": "commercial",
                    },
                    {
                        "sectionId": "t-01",
                        "title": "技术响应",
                        "parentSectionId": "volume-main",
                        "responseType": "chapter_text",
                        "level": 2,
                        "source": "ai",
                        "kind": "technical",
                    },
                ]
                child_count = 161 if self.outline_attempts == 1 else 1
                rows.extend(
                    {
                        "sectionId": f"child-{index}",
                        "title": f"响应子项{index}",
                        "parentSectionId": "t-01",
                        "responseType": "chapter_text",
                        "level": 3,
                        "source": "ai",
                        "kind": "technical",
                    }
                    for index in range(child_count)
                )
                return {"plan": rows, "warnings": []}

        client = OversizedOutlineClient()
        result = PiWritingOrchestrator(client).plan_outline(
            parse_bundle={
                "requirements": [{"id": "req-001", "requirement": "提供技术方案"}],
                "evaluationItems": [],
            },
            existing_plan=[],
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertEqual(client.outline_attempts, 2)
        self.assertLess(len(result["plan"]), 10)
        self.assertTrue(any("自动压缩修正" in item for item in result["warnings"]))

    def test_many_requirement_batches_are_mapped_concurrently(self) -> None:
        class ConcurrentMappingClient(FakeWritingOrchestrationClient):
            def __init__(self) -> None:
                super().__init__()
                self.active = 0
                self.max_active = 0
                self.active_lock = threading.Lock()

            def plan_outline_agent(self, **_: object) -> dict[str, object]:
                self.calls.append("OutlinePlanAgent")
                return {
                    "plan": [
                        {
                            "sectionId": "volume-main",
                            "title": "投标响应文件",
                            "nodeType": "volume",
                            "responseType": "section_group",
                            "level": 1,
                            "source": "template",
                            "kind": "commercial",
                        },
                        {
                            "sectionId": "t-01",
                            "title": "技术响应",
                            "parentSectionId": "volume-main",
                            "responseType": "chapter_text",
                            "level": 2,
                            "source": "ai",
                            "kind": "technical",
                        },
                        {
                            "sectionId": "t-01-01",
                            "title": "技术要求响应",
                            "parentSectionId": "t-01",
                            "responseType": "chapter_text",
                            "level": 3,
                            "source": "ai",
                            "kind": "technical",
                        },
                    ],
                    "warnings": [],
                }

            def map_requirements(self, **kwargs: object) -> dict[str, object]:
                bundle = kwargs["parse_bundle"]
                batch = bundle["requirements"]  # type: ignore[index]
                with self.active_lock:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.05)
                with self.active_lock:
                    self.active -= 1
                self.calls.append("RequirementMapAgent")
                return {
                    "coverage": [
                        {
                            "requirementId": item["id"],
                            "sectionId": "t-01",
                            "coverageType": "chapter_text",
                            "status": "planned",
                        }
                        for item in batch
                    ],
                    "uncoveredRequirements": [],
                    "duplicatedRequirements": [],
                    "warnings": [],
                }

        client = ConcurrentMappingClient()
        requirements = [
            {"id": f"req-{index:03d}", "requirement": f"技术要求{index}"}
            for index in range(161)
        ]
        orchestrator = PiWritingOrchestrator(client)
        outline_result = orchestrator.plan_outline(
            parse_bundle={"requirements": requirements, "evaluationItems": []},
            existing_plan=[],
            workspace_id="workspace-1",
            project_id="project-1",
        )
        client.calls.clear()
        result = orchestrator.map_requirement_coverage(
            parse_bundle={"requirements": requirements, "evaluationItems": []},
            outline_plan=outline_result["plan"],
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertEqual(result["requirementMapBatchCount"], 3)
        self.assertEqual(len(result["coverage"]), len(requirements))
        self.assertGreaterEqual(client.max_active, 2)

    def test_failed_requirement_batch_does_not_report_completed(self) -> None:
        class PartialMappingClient(FakeWritingOrchestrationClient):
            def map_requirements(self, **kwargs: object) -> dict[str, object]:
                bundle = kwargs["parse_bundle"]
                assert isinstance(bundle, dict)
                batch = bundle["requirements"]
                assert isinstance(batch, list)
                self.calls.append("RequirementMapAgent")
                if batch[0]["id"] == "req-080":
                    raise PiError("Pi 请求被输入策略拦截（policy_blocked）")
                return {
                    "coverage": [
                        {"requirementId": item["id"], "sectionId": "t-01"}
                        for item in batch
                    ],
                    "uncoveredRequirements": [],
                    "duplicatedRequirements": [],
                }

        client = PartialMappingClient()
        result = PiWritingOrchestrator(client).map_requirement_coverage(
            parse_bundle={
                "requirements": [{"id": f"req-{index:03d}"} for index in range(81)],
                "evaluationItems": [],
            },
            outline_plan=[{"sectionId": "t-01", "title": "技术响应"}],
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertEqual(result["requirementMappingStatus"], "failed")
        self.assertEqual(result["requirementMapBatchCount"], 2)
        self.assertEqual(len(result["coverage"]), 80)
        self.assertEqual(
            [item["requirementId"] for item in result["uncoveredRequirements"]],
            ["req-080"],
        )
        self.assertEqual(
            sorted(item["status"] for item in result["agentResults"]),
            ["failed", "success"],
        )
        self.assertTrue(any("1/2 个批次失败" in item for item in result["warnings"]))

    def test_pi_writing_orchestrator_filters_unknown_outline_ids(self) -> None:
        client = FakeWritingOrchestrationClient()

        def plan_with_unknown_ids(**_: object) -> dict[str, object]:
            client.calls.append("OutlinePlanAgent")
            return {
                "plan": [
                    {
                        "sectionId": "t-01",
                        "title": "技术方案",
                        "source": "ai",
                        "kind": "technical",
                        "requirementIds": ["req-001", "req-unknown"],
                        "evaluationIds": ["eval-001", "eval-unknown"],
                    }
                ],
                "warnings": [],
            }

        client.plan_outline_agent = plan_with_unknown_ids  # type: ignore[method-assign]
        result = PiWritingOrchestrator(client).plan_outline(
            parse_bundle={
                "requirements": [{"id": "req-001", "requirement": "提供技术方案"}],
                "evaluationItems": [{"id": "eval-001", "criterion": "完整性"}],
            },
            existing_plan=[],
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertEqual(result["plan"][1]["requirementIds"], ["req-001"])
        self.assertEqual(result["plan"][1]["evaluationIds"], ["eval-001"])

    def test_pi_writing_orchestrator_keeps_outline_without_mapping(self) -> None:
        client = FakeWritingOrchestrationClient(fail_mapping=True)
        result = PiWritingOrchestrator(client).plan_outline(
            parse_bundle={"requirements": [{"id": "req-001"}]},
            existing_plan=[],
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertEqual(result["plan"][1]["title"], "技术方案")
        self.assertEqual(result["coverage"], [])
        self.assertEqual(result["uncoveredRequirements"], [])
        self.assertEqual(result["agentResults"][-1]["agentId"], "OutlinePlanAgent")

    def test_pi_writing_orchestrator_fails_without_outline(self) -> None:
        client = FakeWritingOrchestrationClient(fail_outline=True)

        with self.assertRaises(PiWritingOrchestrationError):
            PiWritingOrchestrator(client).plan_outline(
                parse_bundle={"requirements": []},
                existing_plan=[],
                workspace_id="workspace-1",
                project_id="project-1",
            )

    def test_pi_section_orchestrator_runs_material_plan_and_draft(self) -> None:
        client = FakeSectionWritingClient()
        result = PiWritingOrchestrator(client).draft_section(
            parse_bundle={
                "parseBundleId": "parse-001",
                "requirements": [{"id": "req-001", "requirement": "提供技术方案"}],
                "evaluationItems": [],
            },
            coverage=[
                {
                    "requirementId": "req-001",
                    "sectionId": "t-02",
                    "status": "planned",
                }
            ],
            section={
                "sectionId": "t-02",
                "title": "技术方案",
                "kind": "technical",
                "requirementIds": ["req-001"],
            },
            project={"id": "project-1"},
            company={"name": "测试企业"},
            knowledge={"qualifications": [{"id": "qualification-001"}]},
            mode="open",
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertEqual(
            client.calls,
            ["MaterialMatchAgent", "SectionPlanAgent", "SectionDraftAgent"],
        )
        self.assertIn("技术响应", result["content"])
        self.assertEqual(result["materialMatches"][0]["materialIds"], ["qualification-001"])
        self.assertEqual(result["writingPlan"]["sectionId"], "t-02")
        self.assertEqual(
            [item["agentId"] for item in result["agentResults"]],
            client.calls,
        )

    def test_pi_section_orchestrator_filters_unknown_references(self) -> None:
        client = FakeSectionWritingClient()

        def match_with_unknowns(**_: object) -> dict[str, object]:
            client.calls.append("MaterialMatchAgent")
            return {
                "matches": [
                    {
                        "requirementId": "req-001",
                        "materialIds": ["qualification-001", "material-unknown"],
                        "materialRefs": [
                            {"materialId": "qualification-001", "name": "有效材料"},
                            {"materialId": "material-unknown", "name": "未知材料"},
                        ],
                        "matchStatus": "matched",
                    },
                    {
                        "requirementId": "req-unknown",
                        "materialIds": ["qualification-001"],
                        "matchStatus": "matched",
                    },
                ]
            }

        def plan_with_unknowns(**_: object) -> dict[str, object]:
            client.calls.append("SectionPlanAgent")
            return {
                "sectionId": "wrong-section",
                "title": "错误标题",
                "requirementResponses": [
                    {"requirementId": "req-001", "materialIds": ["qualification-001"]},
                    {"requirementId": "req-unknown", "materialIds": ["qualification-001"]},
                ],
                "evaluationResponses": [{"evaluationId": "eval-unknown"}],
                "outline": [
                    {
                        "heading": "技术响应",
                        "requirementIds": ["req-001", "req-unknown"],
                        "evaluationIds": ["eval-unknown"],
                    }
                ],
            }

        def draft_with_unknown_refs(**_: object) -> dict[str, object]:
            client.calls.append("SectionDraftAgent")
            return {
                "sectionId": "wrong-section",
                "title": "错误标题",
                "content": "## 技术响应\n\n正文",
                "sourceRefs": [
                    {"materialId": "qualification-001", "name": "有效材料"},
                    {"materialId": "material-unknown", "name": "未知材料"},
                ],
                "status": "generated",
            }

        client.match_materials = match_with_unknowns  # type: ignore[method-assign]
        client.plan_section = plan_with_unknowns  # type: ignore[method-assign]
        client.draft_section_agent = draft_with_unknown_refs  # type: ignore[method-assign]
        result = PiWritingOrchestrator(client).draft_section(
            parse_bundle={
                "requirements": [{"id": "req-001", "requirement": "提供技术方案"}],
                "evaluationItems": [{"id": "eval-001", "criterion": "完整性"}],
            },
            coverage=[{"requirementId": "req-001", "sectionId": "t-02"}],
            section={
                "sectionId": "t-02",
                "title": "技术方案",
                "kind": "technical",
                "requirementIds": ["req-001", "req-unknown"],
                "evaluationIds": ["eval-001", "eval-unknown"],
            },
            project={"id": "project-1"},
            company={},
            knowledge={"qualifications": [{"id": "qualification-001"}]},
            mode="open",
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertEqual(result["materialMatches"][0]["materialIds"], ["qualification-001"])
        self.assertEqual(result["writingPlan"]["sectionId"], "t-02")
        self.assertEqual(result["writingPlan"]["title"], "技术方案")
        self.assertEqual(
            result["writingPlan"]["requirementResponses"][0]["requirementId"],
            "req-001",
        )
        self.assertEqual(result["sourceRefs"][0]["materialId"], "qualification-001")
        self.assertEqual(result["sectionId"], "t-02")
        self.assertEqual(result["title"], "技术方案")

    def test_pi_section_orchestrator_continues_when_material_match_fails(self) -> None:
        client = FakeSectionWritingClient(fail_material=True)
        result = PiWritingOrchestrator(client).draft_section(
            parse_bundle={"requirements": [{"id": "req-001"}]},
            coverage=[],
            section={"sectionId": "t-02", "title": "技术方案", "requirementIds": ["req-001"]},
            project={},
            company={},
            knowledge={},
            mode="open",
            workspace_id="workspace-1",
            project_id="project-1",
        )

        self.assertTrue(result["content"])
        self.assertEqual(result["agentResults"][0]["status"], "failed")
        self.assertIn("req-001", result["unmatchedMaterialRequirementIds"])
        self.assertTrue(any("MaterialMatchAgent 执行失败" in item for item in result["warnings"]))

    def test_pi_section_orchestrator_marks_missing_materials_and_plan_gaps(self) -> None:
        client = FakeSectionWritingClient()
        result = PiWritingOrchestrator(client).draft_section(
            parse_bundle={
                "requirements": [
                    {"id": "req-001", "requirement": "提供质量保证材料"},
                    {"id": "req-002", "requirement": "提供实施计划"},
                ],
                "evaluationItems": [{"id": "eval-001", "criterion": "方案完整性"}],
            },
            coverage=[
                {
                    "requirementId": "req-001",
                    "sectionId": "t-02",
                    "evaluationIds": ["eval-001"],
                },
                {"requirementId": "req-002", "sectionId": "t-02"},
            ],
            section={
                "sectionId": "t-02",
                "title": "技术方案",
                "requirementIds": ["req-001", "req-002"],
                "evaluationIds": ["eval-001"],
            },
            project={},
            company={},
            knowledge={"qualifications": [{"id": "qualification-001"}]},
            mode="open",
            workspace_id="workspace-1",
            project_id="project-1",
        )

        plan_ids = {
            item["requirementId"]
            for item in result["writingPlan"]["requirementResponses"]
        }
        self.assertEqual(plan_ids, {"req-001", "req-002"})
        missing_plan = next(
            item
            for item in result["writingPlan"]["requirementResponses"]
            if item["requirementId"] == "req-002"
        )
        self.assertEqual(missing_plan["responseStrategy"], "needs_review")
        self.assertIn("需人工补充", missing_plan["reason"])
        self.assertIn("【需人工补充：缺少具体资料】", result["content"])
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(
            any("未覆盖 1 条当前章节要求" in item for item in result["warnings"])
        )

    def test_pi_section_orchestrator_fails_when_draft_returns_no_content(self) -> None:
        client = FakeSectionWritingClient(fail_draft=True)

        with self.assertRaises(PiWritingOrchestrationError):
            PiWritingOrchestrator(client).draft_section(
                parse_bundle={"requirements": []},
                coverage=[],
                section={"sectionId": "t-02", "title": "技术方案"},
                project={},
                company={},
                knowledge={},
                mode="open",
                workspace_id="workspace-1",
                project_id="project-1",
            )

    def test_missing_dify_api_key_is_rejected(self) -> None:
        client = DifyClient(_config(api_key=""))

        with self.assertRaisesRegex(DifyError, "DIFY_API_KEY"):
            client.run_workflow("parse", inputs={}, user="workspace-1")

    def test_invalid_json_from_dify_is_reported(self) -> None:
        client = DifyClient(_config())

        class Response:
            def __enter__(self) -> "Response":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def read(self) -> bytes:
                return b"not-json"

        with patch("backend.app.cloud_agent.dify_client.request.urlopen", return_value=Response()):
            with self.assertRaisesRegex(DifyError, "非法 JSON"):
                client.run_workflow("parse", inputs={}, user="workspace-1")

    def test_process_run_persists_successful_parse_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CloudRunService(root, client=FakeClient())
            run = service.create_run(
                workspace_id="workspace-1",
                project_id="project-1",
                filename="招标文件.txt",
                content="投标人须提供营业执照".encode("utf-8"),
                mime_type="text/plain",
            )

            result = service.process_run(run["runId"])

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["status"], "outline_ready")
            self.assertEqual(result["progress"], 100)
            self.assertEqual(result["result"]["outline"][0]["title"], "投标函")
            output_path = root / result["outputArtifact"]
            self.assertTrue(output_path.is_file())
            self.assertEqual(json.loads(output_path.read_text(encoding="utf-8"))["projectMeta"]["projectName"], "智慧园区项目")
            self.assertEqual(result["provider"], "dify")
            self.assertTrue(result["parseBundleId"])
            parse_bundle_path = root / result["parseBundleArtifact"]
            self.assertTrue(parse_bundle_path.is_file())
            self.assertEqual(
                json.loads(parse_bundle_path.read_text(encoding="utf-8"))["provider"],
                "dify",
            )

    def test_process_run_uses_pi_provider_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dify = FakeClient(error=AssertionError("Dify should not be called"))
            service = CloudRunService(
                root,
                client=dify,  # type: ignore[arg-type]
                pi_client=FakePiClient(),  # type: ignore[arg-type]
                agent_provider="pi",
            )
            run = service.create_run(
                workspace_id="workspace-1",
                project_id="project-1",
                filename="招标文件.txt",
                content="投标人须提供营业执照".encode("utf-8"),
                mime_type="text/plain",
            )

            result = service.process_run(run["runId"])

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["status"], "outline_ready")
            self.assertEqual(result["result"]["projectMeta"]["projectName"], "智慧园区项目")

    def test_runtime_merge_does_not_erase_workspace_from_another_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = Store(root)
            writer.initialize()
            first = writer.materialize_cloud_parse(self._run_with_result())
            assert first is not None
            first_workspace_id = first["workspace"]["id"]

            stale_process = Store(root)
            stale_process.initialize()
            second_run = self._run_with_result()
            second_run["runId"] = "bid-run-second"
            second = writer.materialize_cloud_parse(second_run)
            assert second is not None
            second_workspace_id = second["workspace"]["id"]

            stale_process.workspaces[first_workspace_id]["draft"] = "用户在旧进程中的修改"
            stale_process.persist_runtime()

            reloaded = Store(root)
            reloaded.initialize()
            self.assertIn(first_workspace_id, reloaded.workspaces)
            self.assertIn(second_workspace_id, reloaded.workspaces)
            self.assertEqual(
                reloaded.workspaces[first_workspace_id]["draft"],
                "用户在旧进程中的修改",
            )

    def test_plan_fallback_records_actual_provider_and_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            run = self._run_with_result()
            run["provider"] = "pi"
            materialized = store.materialize_cloud_parse(run)
            assert materialized is not None
            workspace_id = materialized["workspace"]["id"]

            with patch(
                "backend.app.cloud_agent.config.DifyConfig.from_env",
                return_value=_config(provider="dify"),
            ):
                result = store.plan_bid_document(workspace_id)

            assert result is not None
            bid_document = result["bidDocument"]
            self.assertEqual(bid_document["planningProvider"], "dify")
            self.assertEqual(bid_document["planningStatus"], "fallback_local")
            self.assertTrue(any(row.get("level", 1) >= 3 for row in bid_document["plan"]))
            self.assertIn("本地规则", bid_document["planningFallbackReason"])

    def test_failed_pi_replanning_completes_parse_draft_locally(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            run = self._run_with_result()
            run["provider"] = "pi"
            materialized = store.materialize_cloud_parse(run)
            assert materialized is not None
            workspace_id = materialized["workspace"]["id"]
            original_plan = materialized["workspace"]["bidDocument"]["plan"]

            with patch(
                "backend.app.cloud_agent.config.DifyConfig.from_env",
                return_value=_config(provider="pi"),
            ):
                with patch(
                    "backend.app.pi_agent.client.PiClient.plan_outline",
                    side_effect=RuntimeError("Pi planning timeout"),
                ):
                    result = store.plan_bid_document(workspace_id)

            assert result is not None
            bid_document = result["bidDocument"]
            self.assertIn(bid_document["planSource"], {"tender_document", "builtin_template"})
            self.assertTrue(any(row.get("nodeType") == "volume" for row in bid_document["plan"]))
            self.assertTrue(any(row.get("level", 1) >= 3 for row in bid_document["plan"]))
            self.assertGreaterEqual(len(bid_document["plan"]), len(original_plan))
            self.assertEqual(bid_document["planningStatus"], "fallback_local")
            self.assertIn("Pi planning timeout", bid_document["planningFallbackReason"])

    def test_process_run_keeps_unassigned_parse_bundle_run_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CloudRunService(root, client=FakeClient())
            run = service.create_run(
                workspace_id="",
                project_id="",
                filename="招标文件.txt",
                content="投标人须提供营业执照".encode("utf-8"),
                mime_type="text/plain",
            )

            result = service.process_run(run["runId"])

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(
                result["parseBundleArtifact"],
                f"runtime/projects/unassigned/runs/{run['runId']}/parse-bundle.json",
            )
            self.assertTrue((root / result["parseBundleArtifact"]).is_file())

    def test_process_run_marks_cloud_timeout_as_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = CloudRunService(root, client=FakeClient(error=TimeoutError("timeout")))
            run = service.create_run(
                workspace_id="workspace-1",
                project_id="project-1",
                filename="招标文件.txt",
                content=b"content",
            )

            result = service.process_run(run["runId"])

            self.assertIsNotNone(result)
            assert result is not None
            self.assertEqual(result["status"], "failed")
            self.assertIn("timeout", result["error"])

    def test_missing_run_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = CloudRunService(Path(directory), client=FakeClient())

            self.assertIsNone(service.get_run("bid-run-not-found"))

    def test_materialize_cloud_parse_creates_workspace_and_bid_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = Store(root)
            store.initialize()

            result = store.materialize_cloud_parse(self._run_with_result())

            self.assertIsNotNone(result)
            assert result is not None
            project = result["project"]
            workspace = result["workspace"]
            self.assertEqual(project["title"], "智慧园区项目")
            self.assertEqual(workspace["projectId"], project["id"])
            self.assertEqual(workspace["reasoning"]["fineReview"]["rows"][0]["requirement"], "投标人须具有有效营业执照")
            self.assertEqual(workspace["bidDocument"]["planSource"], "cloud_agent")
            self.assertEqual(workspace["bidDocument"]["plan"][0]["sectionId"], "c-01")
            self.assertEqual(workspace["bidDocument"]["plan"][1]["sectionId"], "t-02")
            self.assertEqual(workspace["parseBundleRef"]["provider"], "dify")
            self.assertTrue((root / workspace["parseBundleRef"]["artifact"]).is_file())
            self.assertEqual(
                store.get_parse_bundle(workspace["id"])["parseBundleId"],
                workspace["parseBundleRef"]["parseBundleId"],
            )

    def test_materialize_cloud_parse_is_idempotent_for_requested_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            run = self._run_with_result()
            run["workspaceId"] = "workspace-fixed"
            run["projectId"] = "project-fixed"

            first = store.materialize_cloud_parse(run)
            second = store.materialize_cloud_parse(run)

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            assert first is not None and second is not None
            self.assertEqual(first["project"]["id"], "project-fixed")
            self.assertEqual(second["workspace"]["id"], "workspace-fixed")
            self.assertEqual(len([item for item in store.catalog if item["id"] == "project-fixed"]), 1)
            self.assertEqual(list(store.workspaces).count("workspace-fixed"), 1)

    def test_generate_bid_section_uses_cloud_section_workflow_when_configured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            materialized = store.materialize_cloud_parse(self._run_with_result())
            assert materialized is not None
            workspace_id = materialized["workspace"]["id"]

            with patch("backend.app.cloud_agent.config.DifyConfig.from_env", return_value=_config(section_workflow_key="section-key")):
                with patch(
                    "backend.app.cloud_agent.dify_client.DifyClient.draft_section",
                    return_value={
                        "data": {
                            "outputs": {
                                "content": "## 技术方案\n\n这是云端智能体撰写的章节。",
                                "sourceRefs": [{"name": "云端引用"}],
                            }
                        }
                    },
                ):
                    result = store.generate_bid_section(workspace_id, "t-02")

            self.assertIsNotNone(result)
            assert result is not None
            section = next(s for s in result["bidDocument"]["plan"] if s["sectionId"] == "t-02")
            self.assertEqual(section["draftProvider"], "dify")
            self.assertIn("云端智能体撰写", section["content"])
            self.assertEqual(section["sourceRefs"][0]["name"], "云端引用")

    def test_legacy_workspace_get_parse_bundle_is_explicitly_local(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["workspace-legacy"] = {
                "id": "workspace-legacy",
                "workspaceId": "workspace-legacy",
                "projectId": "project-legacy",
                "project": {
                    "id": "project-legacy",
                    "title": "旧版项目",
                    "analysisText": "旧版招标文件正文",
                    "parsed": {
                        "projectName": "旧版项目",
                        "projectCode": "LEGACY-001",
                    },
                    "document": {
                        "id": "doc-legacy",
                        "name": "legacy.docx",
                        "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "sections": [{"sectionId": "doc-1", "heading": "技术需求"}],
                        "tables": [],
                        "segments": [],
                        "units": [],
                    },
                },
                "reasoning": {
                    "fineReview": {
                        "rows": [
                            {
                                "id": "req-001",
                                "requirementId": "req-001",
                                "requirement": "提供技术方案",
                            }
                        ]
                    },
                    "evaluation": {
                        "items": [{"id": "eval-001", "criterion": "完整性"}]
                    },
                },
                "bidDocument": {"plan": []},
            }

            bundle = store.get_parse_bundle("workspace-legacy")

            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle["provider"], "legacy_local")
            self.assertEqual(bundle["status"], "compatibility")
            self.assertEqual(bundle["requirements"][0]["requirementId"], "req-001")
            self.assertEqual(bundle["evaluationItems"][0]["id"], "eval-001")
            self.assertEqual(
                store.workspaces["workspace-legacy"]["parseBundleRef"]["provider"],
                "legacy_local",
            )

    def test_existing_parse_bundle_repairs_missing_workspace_reference(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            materialized = store.materialize_cloud_parse(self._run_with_result())
            assert materialized is not None
            workspace_id = materialized["workspace"]["id"]
            store.workspaces[workspace_id].pop("parseBundleRef", None)
            store.persist_runtime()

            bundle = store.get_parse_bundle(workspace_id)

            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(
                store.workspaces[workspace_id]["parseBundleRef"]["provider"],
                "dify",
            )
            self.assertEqual(
                store.workspaces[workspace_id]["parseBundleRef"]["parseBundleId"],
                bundle["parseBundleId"],
            )

    def test_initialize_backfills_lost_pi_requirement_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["workspace-pi"] = {
                "id": "workspace-pi",
                "projectId": "project-pi",
                "bidDocument": {
                    "planSource": "pi_agent",
                    "requirementCoverage": [],
                    "plan": [
                        {
                            "sectionId": "t-01",
                            "source": "ai",
                            "kind": "technical",
                            "requirementIds": ["req-001", "req-002"],
                        },
                        {
                            "sectionId": "d-01",
                            "source": "table",
                            "kind": "deviation",
                            "requirementIds": ["req-003"],
                        },
                    ],
                },
            }
            store.persist_runtime()

            reloaded = Store(Path(directory))
            reloaded.initialize()
            bid_document = reloaded.workspaces["workspace-pi"]["bidDocument"]

            self.assertEqual(
                [
                    (item["requirementId"], item["sectionId"], item["coverageType"])
                    for item in bid_document["requirementCoverage"]
                ],
                [
                    ("req-001", "t-01", "chapter_text"),
                    ("req-002", "t-01", "chapter_text"),
                    ("req-003", "d-01", "table"),
                ],
            )
            self.assertTrue(
                any("回填缺失的 requirementCoverage" in item for item in bid_document["planningWarnings"])
            )

    def test_generate_bid_section_persists_pi_orchestration_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            materialized = store.materialize_cloud_parse(self._run_with_result())
            assert materialized is not None
            workspace_id = materialized["workspace"]["id"]

            with patch("backend.app.cloud_agent.config.DifyConfig.from_env", return_value=_config(provider="pi")):
                with patch(
                    "backend.app.pi_agent.client.PiClient.draft_section",
                    return_value={
                        "sectionId": "t-02",
                        "title": "技术方案",
                        "content": "## 技术响应\n\nPi 三节点编排正文。",
                        "sourceRefs": [{"name": "企业材料"}],
                        "materialMatches": [{"requirementId": "req-001", "materialIds": ["mat-001"]}],
                        "unmatchedMaterialRequirementIds": [],
                        "writingPlan": {"sectionId": "t-02", "outline": [{"heading": "技术响应"}]},
                        "agentResults": [
                            {"agentId": "MaterialMatchAgent", "status": "success"},
                            {"agentId": "SectionPlanAgent", "status": "success"},
                            {"agentId": "SectionDraftAgent", "status": "success"},
                        ],
                        "provider": "pi",
                        "phase": "section_draft",
                        "status": "generated",
                        "warnings": [],
                    },
                ):
                    result = store.generate_bid_section(workspace_id, "t-02")

            self.assertIsNotNone(result)
            assert result is not None
            section = next(s for s in result["bidDocument"]["plan"] if s["sectionId"] == "t-02")
            self.assertEqual(section["draftProvider"], "pi")
            self.assertEqual(section["materialMatches"][0]["materialIds"], ["mat-001"])
            self.assertEqual(section["writingPlan"]["sectionId"], "t-02")
            self.assertEqual(len(section["agentResults"]), 3)

    def test_generate_bid_section_falls_back_when_cloud_section_workflow_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            materialized = store.materialize_cloud_parse(self._run_with_result())
            assert materialized is not None
            workspace_id = materialized["workspace"]["id"]

            with patch("backend.app.cloud_agent.config.DifyConfig.from_env", return_value=_config(section_workflow_key="section-key")):
                with patch(
                    "backend.app.cloud_agent.dify_client.DifyClient.draft_section",
                    side_effect=DifyError("cloud failed"),
                ):
                    result = store.generate_bid_section(workspace_id, "t-02")

            self.assertIsNotNone(result)
            assert result is not None
            section = next(s for s in result["bidDocument"]["plan"] if s["sectionId"] == "t-02")
            self.assertEqual(section["draftProvider"], "local_fallback")
            self.assertIn("cloud failed", section["cloudDraftError"])
            self.assertTrue(section["content"])

    def test_plan_bid_document_uses_pi_provider_for_existing_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            materialized = store.materialize_cloud_parse(self._run_with_result())
            assert materialized is not None
            workspace_id = materialized["workspace"]["id"]

            with patch("backend.app.cloud_agent.config.DifyConfig.from_env", return_value=_config(provider="pi")):
                with patch(
                    "backend.app.pi_agent.client.PiClient.plan_outline",
                    return_value={
                        "plan": [
                            {
                                "sectionId": "c-01",
                                "title": "投标函",
                                "source": "template",
                                "kind": "commercial",
                                "requirementIds": [],
                            },
                            {
                                "sectionId": "custom-tech",
                                "title": "按招标要求编制的技术响应方案",
                                "source": "ai",
                                "kind": "technical",
                                "requirementIds": ["req-001", "not-exists"],
                                "evaluationIds": ["eval-001"],
                                "templateId": "tpl-tech",
                            },
                        ],
                        "coverage": [
                            {
                                "requirementId": "req-001",
                                "sectionId": "custom-tech",
                                "status": "planned",
                            }
                        ],
                        "warnings": ["Pi 目录规划测试"],
                    },
                ):
                    result = store.plan_bid_document(workspace_id)

            self.assertIsNotNone(result)
            assert result is not None
            bid_doc = result["bidDocument"]
            self.assertEqual(bid_doc["planSource"], "pi_agent")
            self.assertEqual(bid_doc["planningWarnings"], ["Pi 目录规划测试"])
            section = next(s for s in bid_doc["plan"] if s["sectionId"] == "custom-tech")
            self.assertEqual(section["requirementIds"], ["req-001"])
            self.assertEqual(section["evaluationIds"], ["eval-001"])
            self.assertEqual(section["templateId"], "tpl-tech")
            self.assertEqual(bid_doc["requirementCoverage"], [])
            self.assertEqual(bid_doc["requirementMappingStatus"], "pending")
            legacy_deviation = next(
                (s for s in bid_doc["plan"] if s["sectionId"] == "d-01"),
                None,
            )
            self.assertIsNotNone(legacy_deviation)
            self.assertFalse(legacy_deviation.get("volumeId"))

    def test_plan_bid_document_defers_requirement_mapping_until_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            materialized = store.materialize_cloud_parse(self._run_with_result())
            assert materialized is not None
            workspace_id = materialized["workspace"]["id"]
            bundle = store.get_parse_bundle(workspace_id)
            assert bundle is not None
            bundle["requirements"].append(
                {
                    "id": "req-002",
                    "category": "technical",
                    "requirement": "提供实施计划",
                }
            )
            store.save_parse_bundle(bundle)

            with patch(
                "backend.app.cloud_agent.config.DifyConfig.from_env",
                return_value=_config(provider="pi"),
            ):
                with patch(
                    "backend.app.pi_agent.client.PiClient.plan_outline",
                    return_value={
                        "plan": [
                            {
                                "sectionId": "t-02",
                                "title": "技术方案",
                                "source": "ai",
                                "kind": "technical",
                                "requirementIds": ["req-001"],
                            }
                        ],
                        "coverage": [
                            {
                                "requirementId": "req-001",
                                "sectionId": "t-02",
                                "status": "planned",
                            }
                        ],
                        "uncoveredRequirements": [],
                        "duplicatedRequirements": [],
                        "agentResults": [],
                    },
                ):
                    result = store.plan_bid_document(workspace_id)

            assert result is not None
            self.assertEqual(result["bidDocument"]["requirementCoverage"], [])
            self.assertEqual(result["bidDocument"]["uncoveredRequirements"], [])
            self.assertEqual(result["bidDocument"]["requirementMappingStatus"], "pending")


if __name__ == "__main__":
    unittest.main()
