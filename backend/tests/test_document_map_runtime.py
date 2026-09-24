from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.workbuddy_agent.workflow.runtime import (
    DocumentMapAgentRuntime,
    DocumentMapRuntimeConfig,
    WorkBuddySdkExecutor,
)
from backend.app.tender_matching.document_models import (
    DocumentIR,
    DocumentSection,
    DocumentSegment,
)

ROOT = Path(__file__).resolve().parents[2]


class DocumentMapRuntimeTests(unittest.TestCase):
    def test_runtime_defaults_to_codebuddy_sdk(self) -> None:
        runtime = DocumentMapAgentRuntime(
            self._document(),
            config={"runtimeDir": str(ROOT / "runtime")},
            executor=None,
        )
        self.assertIsInstance(runtime.executor, WorkBuddySdkExecutor)

    def test_runtime_run_records_explicit_sdk_model(self) -> None:
        runtime = DocumentMapAgentRuntime(
            self._document(),
            config={
                "runtimeDir": str(ROOT / "runtime"),
                "model": "deepseek-v4.1-flash",
            },
            executor=lambda _task: {},
        )

        self.assertEqual(runtime.agent_run.model, "deepseek-v4.1-flash")

    def _document(self, *, include_rejection: bool = False) -> DocumentIR:
        texts = ["第一章 技术要求", "平台应支持统一身份认证。"]
        if include_rejection:
            texts.append("投标无效：未按要求提交响应文件。")
        segments = [
            DocumentSegment(
                id=f"SEG-{index:03d}",
                text=text,
                order=index,
                sourceLocator=f"第{index}页",
            )
            for index, text in enumerate(texts, start=1)
        ]
        return DocumentIR(
            documentId="DOC-RUNTIME-001",
            documentVersionId="DOC-RUNTIME-001-V001",
            fileName="runtime-test.docx",
            originalFormat="docx",
            text="\n".join(texts),
            segments=segments,
            sections=[
                DocumentSection(
                    id="SEC-TECH",
                    sectionId="SEC-TECH",
                    heading="第一章 技术要求",
                    sectionPath=["第一章 技术要求"],
                    segmentIds=["SEG-001", "SEG-002"],
                )
            ],
        )

    def _map(self, task, *, source_segment_id: str = "SEG-002") -> dict:
        sections = [
            {
                "mapId": "MAP-RUNTIME-001",
                "runId": task.run.run_id,
                "documentId": task.document.document_id,
                "documentVersionId": task.document.document_version_id,
                "domain": "technical",
                "role": "leaf",
                "processingMode": "fine_extract",
                "title": "技术要求",
                "sectionIds": ["SEC-TECH"],
                "sourceSegmentIds": [source_segment_id],
                "ranges": [
                    {
                        "rangeId": "RANGE-RUNTIME-001",
                        "segmentIds": [source_segment_id],
                        "contentSummary": "技术要求正文",
                    }
                ],
                "primaryRoute": "technical_processor",
                "priority": "high",
                "confidence": 0.96,
                "classificationSource": "agent",
            }
        ]
        return {
            "schemaVersion": "tender-document-map/v1",
            "mapId": "MAP-RUNTIME-001",
            "runId": task.run.run_id,
            "documentId": task.document.document_id,
            "documentVersionId": task.document.document_version_id,
            "documentType": "government_tender",
            "sections": sections,
            "ignoredSections": [],
            "rejectionCandidates": [],
            "unresolvedSections": [],
            "unresolvedIssues": [],
            "candidateFindings": [],
            "warnings": [],
        }

    def test_runtime_reads_through_mcp_and_completes_with_validated_map(self) -> None:
        document = self._document()

        def executor(task):
            identity = {
                "documentId": task.document.document_id,
                "documentVersionId": task.document.document_version_id,
            }
            self.assertNotIn(task.document.text, task.user_prompt)
            self.assertNotIn("sourceSegmentIds", task.user_prompt)
            inspected = task.server.call_tool("inspect_document", identity)
            self.assertEqual(
                inspected["document"]["documentId"],
                task.document.document_id,
            )
            full = task.server.call_tool(
                "read_document_full",
                {**identity, "maxChars": 4000},
            )
            self.assertEqual(full["records"][1]["recordId"], "SEG-002")
            return {"documentMap": self._map(task), "turnCount": 3}

        with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as directory:
            root = Path(directory)
            config = DocumentMapRuntimeConfig(
                skillDir="backend/app/workbuddy_agent/skills/tender-document-map",
                mcpConfigPath=(
                    "backend/app/workbuddy_agent/config/"
                    "tender-review.mcp.example.json"
                ),
                runtimeDir=str(root),
                model="test-model",
                maxRetries=0,
            )
            runtime = DocumentMapAgentRuntime(
                document,
                config=config,
                executor=executor,
            )
            result = runtime.run()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["documentMap"]["mapId"], "MAP-RUNTIME-001")
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["run"]["toolCallCount"], 3)
        self.assertEqual(result["metadata"]["runtimeVersion"], "workbuddy-document-map-runtime/v1")
        self.assertTrue(result["checkpoints"])

    def test_runtime_rejects_fabricated_source_ids(self) -> None:
        document = self._document()

        def executor(task):
            return {"documentMap": self._map(task, source_segment_id="SEG-NOT-FOUND")}

        with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as directory:
            runtime = DocumentMapAgentRuntime(
                document,
                config={
                    "runtimeDir": directory,
                    "maxRetries": 0,
                },
                executor=executor,
            )
            result = runtime.run()

        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["documentMap"])
        self.assertTrue(any("unknown_segment_id" in item for item in result["errors"]))

    def test_runtime_requires_high_risk_rejection_coverage(self) -> None:
        document = self._document(include_rejection=True)

        def executor(task):
            return {"documentMap": self._map(task)}

        with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as directory:
            runtime = DocumentMapAgentRuntime(
                document,
                config={
                    "runtimeDir": directory,
                    "maxRetries": 0,
                    "requireRejectionScan": True,
                },
                executor=executor,
            )
            result = runtime.run()

        self.assertEqual(result["status"], "failed")
        self.assertTrue(any("high-risk" in item for item in result["errors"]))

    def test_runtime_retries_with_a_fresh_attempt(self) -> None:
        document = self._document()
        calls = {"count": 0}

        def executor(task):
            calls["count"] += 1
            if calls["count"] == 1:
                return {"warnings": ["temporary failure"]}
            return {"documentMap": self._map(task), "turnCount": 1}

        with tempfile.TemporaryDirectory(dir=ROOT / "runtime") as directory:
            runtime = DocumentMapAgentRuntime(
                document,
                config={
                    "runtimeDir": directory,
                    "maxRetries": 1,
                },
                executor=executor,
            )
            result = runtime.run()

        self.assertEqual(result["status"], "completed")
        self.assertEqual(calls["count"], 2)
        self.assertEqual(result["metadata"]["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
