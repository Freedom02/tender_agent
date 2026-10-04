from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from backend.app.pi_agent.orchestrator import PiParseOrchestrator
from backend.app.pi_agent.prompts.parse import (
    document_structure_prompt,
    evaluation_extract_prompt,
    project_meta_prompt,
    requirement_evaluation_prompt,
    requirement_extract_prompt,
    structure_meta_prompt,
)


class MergedParseClient:
    """Deterministic client for the four-node orchestration contract."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(model="offline-merge-test")
        self.calls: list[str] = []
        self.prompts: list[str] = []

    def _parse_file(self, path: Path) -> dict[str, object]:
        return {
            "text": path.read_text(encoding="utf-8"),
            "segments": [{"id": "block-001", "text": "第三章 投标文件格式"}],
            "units": [{"id": "block-001", "text": "第三章 投标文件格式"}],
            "tables": [],
            "images": [],
            "sections": [],
            "warnings": [],
        }

    def run_json(self, prompt: str, **_: object) -> dict[str, object]:
        self.prompts.append(prompt)
        if "同时负责识别招标文件章节结构和项目基础信息" in prompt:
            self.calls.append("DocumentStructureAgent")
            return {
                "documentStructure": {
                    "sections": [
                        {
                            "sectionId": "doc-sec-001",
                            "title": "第三章 投标文件格式",
                            "level": 1,
                            "sourceLocator": "第三章 投标文件格式",
                            "sectionType": "document_format",
                        }
                    ],
                    "warnings": [],
                },
                "projectMeta": {
                    "projectName": "合并链路测试项目",
                    "projectCode": "MERGE-001",
                    "sourceRefs": [{"sourceLocator": "第一章 项目概况"}],
                },
                "warnings": [],
            }
        if "同时负责抽取投标方必须响应的原子条款和评标评分项" in prompt:
            self.calls.append("RequirementExtractAgent")
            return {
                "requirements": [
                    {
                        "id": "req-001",
                        "category": "technical",
                        "requirement": "提供完整技术方案",
                        "mandatory": True,
                        "sourceLocator": "第二章 技术要求第1条",
                    }
                ],
                "evaluationItems": [
                    {
                        "id": "eval-001",
                        "criterion": "技术方案完整性",
                        "score": 10,
                        "sourceLocator": "第四章 评标办法评分表",
                        "relatedRequirementIds": ["req-001"],
                    }
                ],
                "warnings": [],
            }
        raise AssertionError("unexpected prompt")

    def detect_templates(self, **_: object) -> dict[str, object]:
        self.calls.append("TemplateDetectAgent")
        return {
            "hasExplicitFormat": True,
            "formatSourceSection": "第三章 投标文件格式",
            "submissionStructure": {
                "mode": "single",
                "confidence": "explicit",
                "structureStatus": "valid",
                "sourceLocator": "第三章 投标文件格式",
                "volumes": [
                    {
                        "volumeId": "volume-01",
                        "title": "投标响应文件",
                        "order": 1,
                        "required": True,
                        "sourceLocator": "第三章 投标文件格式",
                        "items": [],
                    }
                ],
            },
            "requiredOutline": [],
            "templates": [],
            "requiredTables": [],
            "warnings": [],
        }

    def review_parse(self, **_: object) -> dict[str, object]:
        self.calls.append("ParseReviewAgent")
        return {
            "status": "pass",
            "confidence": 0.95,
            "missingFields": [],
            "riskItems": [],
            "warnings": [],
        }


class ParseChainMergeTests(unittest.TestCase):
    def test_four_nodes_preserve_parse_bundle_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tender.txt"
            path.write_text(
                "第一章 项目概况\n合并链路测试项目\n"
                "第二章 技术要求第1条：必须提供完整技术方案\n"
                "第三章 投标文件格式\n第四章 评标办法评分表\n",
                encoding="utf-8",
            )
            client = MergedParseClient()
            result = PiParseOrchestrator(client).parse_tender(
                path,
                workspace_id="workspace-1",
                project_id="project-1",
                project_context={"source": "test"},
            )

        self.assertEqual(
            client.calls,
            [
                "DocumentStructureAgent",
                "RequirementExtractAgent",
                "TemplateDetectAgent",
                "ParseReviewAgent",
            ],
        )
        self.assertEqual(len(result["agentResults"]), 4)
        self.assertEqual(result["projectMeta"]["projectName"], "合并链路测试项目")
        self.assertEqual(
            [(item["id"], item["sourceLocator"]) for item in result["requirements"]],
            [("req-001", "第二章 技术要求第1条")],
        )
        self.assertEqual(
            [(item["id"], item["sourceLocator"]) for item in result["evaluationItems"]],
            [("eval-001", "第四章 评标办法评分表")],
        )
        self.assertEqual(
            result["submissionStructure"]["volumes"][0]["title"],
            "投标响应文件",
        )
        self.assertEqual(result["parseReviewReport"]["status"], "pass")

        # This is the field-level snapshot from the six-node fixture. The
        # merged chain must keep the same ids, source locations, scores and
        # submission volume structure when the source document is unchanged.
        legacy_snapshot = {
            "requirements": [("req-001", "第二章 技术要求第1条")],
            "evaluationItems": [("eval-001", "第四章 评标办法评分表", 10)],
            "submissionStructure": {
                "mode": "single",
                "volumeTitles": ["投标响应文件"],
            },
        }
        merged_snapshot = {
            "requirements": [
                (item["id"], item["sourceLocator"])
                for item in result["requirements"]
            ],
            "evaluationItems": [
                (item["id"], item["sourceLocator"], item["score"])
                for item in result["evaluationItems"]
            ],
            "submissionStructure": {
                "mode": result["submissionStructure"]["mode"],
                "volumeTitles": [
                    volume["title"]
                    for volume in result["submissionStructure"]["volumes"]
                ],
            },
        }
        self.assertEqual(merged_snapshot, legacy_snapshot)

    def test_merged_prompts_reduce_repeated_source_text(self) -> None:
        document_text = "招标文件正文 " + ("技术要求与评标办法。 " * 800)
        common = {
            "filename": "tender.txt",
            "workspace_id": "workspace-1",
            "project_id": "project-1",
        }
        old_prompts = [
            document_structure_prompt(
                document_text=document_text,
                document_payload={},
                parser_warnings=[],
                project_context={},
                **common,
            ),
            project_meta_prompt(
                document_text=document_text,
                document_structure={},
                project_context={},
                **common,
            ),
            requirement_extract_prompt(
                document_text=document_text,
                document_structure={},
                project_meta={},
                **common,
            ),
            evaluation_extract_prompt(
                document_text=document_text,
                document_structure={},
                requirements=[],
                **common,
            ),
        ]
        new_prompts = [
            structure_meta_prompt(
                document_text=document_text,
                document_payload={},
                parser_warnings=[],
                project_context={},
                **common,
            ),
            requirement_evaluation_prompt(
                document_text=document_text,
                document_structure={},
                project_meta={},
                **common,
            ),
        ]
        self.assertEqual(sum(prompt.count(document_text) for prompt in old_prompts), 4)
        self.assertEqual(sum(prompt.count(document_text) for prompt in new_prompts), 2)
        self.assertLess(sum(map(len, new_prompts)), sum(map(len, old_prompts)))


if __name__ == "__main__":
    unittest.main()
