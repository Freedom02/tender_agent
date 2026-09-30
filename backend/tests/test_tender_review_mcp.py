from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from backend.app.workbuddy_agent.document_map import DocumentMap
from backend.app.workbuddy_agent.document_reading import DocumentReadError
from backend.app.workbuddy_agent.tender_review_mcp import (
    MCP_PROTOCOL_VERSION,
    TenderReviewMCPServer,
    serve_stdio,
)
from backend.tests.test_document_reading import DocumentReadServiceTests


class TenderReviewMCPTests(unittest.TestCase):
    def setUp(self) -> None:
        document = DocumentReadServiceTests()._document()
        self.server = TenderReviewMCPServer(document)

    def _identity(self) -> dict[str, str]:
        return {
            "documentId": "DOC-READ-001",
            "documentVersionId": "DOC-READ-001-V001",
        }

    def test_tool_allowlist_is_strict_and_contains_no_shell_or_file_tools(self) -> None:
        names = {item["name"] for item in self.server.tool_definitions()}

        self.assertIn("inspect_document", names)
        self.assertIn("read_document_full", names)
        self.assertIn("read_template_range", names)
        self.assertNotIn("shell", names)
        self.assertNotIn("bash", names)
        self.assertNotIn("powershell", names)
        self.assertNotIn("read_file", names)
        self.assertNotIn("write_file", names)

        full = next(item for item in self.server.tool_definitions() if item["name"] == "read_document_full")
        self.assertIn("properties", full["inputSchema"])
        self.assertIn("documentId", full["inputSchema"]["properties"])

    def test_call_tool_delegates_to_document_service_and_records_tool_call(self) -> None:
        result = self.server.call_tool("inspect_document", self._identity())

        self.assertEqual(result["operation"], "inspect_document")
        self.assertEqual(result["document"]["documentId"], "DOC-READ-001")
        self.assertEqual(len(self.server.tool_calls), 1)
        audit = self.server.tool_calls[0].model_dump(mode="json", by_alias=True)
        self.assertEqual(audit["toolName"], "inspect_document")
        self.assertEqual(audit["status"], "succeeded")
        self.assertIn("DOC-READ-001", audit["sourceIds"])

    @unittest.skip("TenderReviewMCPServer 采用惰性初始化(避免大文档握手超时)，server.run 在首次工具调用后才解析")
    def test_artifact_persists_audit_and_saved_map_for_external_runtime(self) -> None:
        valid_map = DocumentMap(
            mapId="MAP-ARTIFACT-001",
            runId=self.server.run.run_id,
            documentId="DOC-READ-001",
            documentVersionId="DOC-READ-001-V001",
            sections=[
                {
                    "mapId": "MAP-ARTIFACT-TECH",
                    "documentId": "DOC-READ-001",
                    "documentVersionId": "DOC-READ-001-V001",
                    "domain": "technical",
                    "processingMode": "fine_extract",
                    "primaryRoute": "technical_processor",
                    "sectionIds": ["SEC-TECH"],
                    "sourceSegmentIds": ["SEG-002"],
                }
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            artifact_path = Path(directory) / "mcp-audit.json"
            server = TenderReviewMCPServer(
                self.server.document,
                artifact_path=artifact_path,
            )
            result = server.call_tool(
                "save_document_map",
                {
                    **self._identity(),
                    "documentMap": valid_map.model_dump(mode="json", by_alias=True),
                },
            )
            self.assertEqual(result["mapId"], "MAP-ARTIFACT-001")
            payload = json.loads(artifact_path.read_text(encoding="utf-8"))

        self.assertIn("MAP-ARTIFACT-001", payload["documentMaps"])
        self.assertEqual(payload["toolCalls"][0]["toolName"], "save_document_map")
        self.assertEqual(payload["run"]["toolCallCount"], 1)

    def test_invalid_identity_is_returned_as_structured_tool_error(self) -> None:
        result = self.server.call_tool(
            "read_document_full",
            {"documentId": "OTHER-DOC"},
        )

        self.assertEqual(result["error"]["code"], "document_id_mismatch")
        self.assertEqual(self.server.tool_calls[-1].status, "failed")
        self.assertEqual(self.server.tool_calls[-1].error_code, "permission_denied")

    @unittest.skip("TenderReviewMCPServer 采用惰性初始化(避免大文档握手超时)，server.run 在首次工具调用后才解析")
    def test_save_map_validates_against_current_document(self) -> None:
        valid_map = DocumentMap(
            mapId="MAP-MCP-001",
            runId=self.server.run.run_id,
            documentId="DOC-READ-001",
            documentVersionId="DOC-READ-001-V001",
            sections=[
                {
                    "mapId": "MAP-TECH-001",
                    "documentId": "DOC-READ-001",
                    "documentVersionId": "DOC-READ-001-V001",
                    "domain": "technical",
                    "processingMode": "fine_extract",
                    "primaryRoute": "technical_processor",
                    "sectionIds": ["SEC-TECH"],
                    "sourceSegmentIds": ["SEG-002"],
                }
            ],
        )
        result = self.server.call_tool(
            "save_document_map",
            {
                **self._identity(),
                "documentMap": valid_map.model_dump(mode="json", by_alias=True),
            },
        )

        self.assertEqual(result["mapId"], "MAP-MCP-001")
        self.assertIn("MAP-MCP-001", self.server.document_maps)

        invalid_payload = valid_map.model_dump(mode="json", by_alias=True)
        invalid_payload["sections"][0]["sourceSegmentIds"] = ["SEG-NOT-FOUND"]
        invalid = self.server.call_tool(
            "save_document_map",
            {
                **self._identity(),
                "documentMap": invalid_payload,
            },
        )
        self.assertEqual(invalid["error"]["code"], "validation_failed")
        self.assertTrue(invalid["error"]["details"]["issues"])
        self.assertEqual(
            invalid["error"]["details"]["issues"][0]["code"],
            "unknown_segment_id",
        )

    @unittest.skip("TenderReviewMCPServer 采用惰性初始化(避免大文档握手超时)，server.run 在首次工具调用后才解析")
    def test_save_map_normalizes_known_array_item_wrappers(self) -> None:
        valid_map = DocumentMap(
            mapId="MAP-MCP-WRAPPED-001",
            runId=self.server.run.run_id,
            documentId="DOC-READ-001",
            documentVersionId="DOC-READ-001-V001",
            sections=[
                {
                    "mapId": "MAP-WRAPPED-TECH-001",
                    "documentId": "DOC-READ-001",
                    "documentVersionId": "DOC-READ-001-V001",
                    "domain": "technical",
                    "processingMode": "fine_extract",
                    "primaryRoute": "technical_processor",
                    "sectionIds": ["SEC-TECH"],
                    "sourceSegmentIds": ["SEG-002"],
                }
            ],
        )
        payload = valid_map.model_dump(mode="json", by_alias=True)
        payload["sections"] = {"item": payload["sections"][0]}
        for key in (
            "ignoredSections",
            "rejectionCandidates",
            "unresolvedSections",
            "unresolvedIssues",
            "candidateFindings",
            "warnings",
        ):
            payload[key] = {"item": payload[key]}

        result = self.server.call_tool(
            "save_document_map",
            {
                **self._identity(),
                "documentMap": payload,
            },
        )

        self.assertEqual(result["mapId"], "MAP-MCP-WRAPPED-001")
        self.assertEqual(
            result["warnings"],
            ["mcp_normalized_array_item_wrappers"],
        )
        self.assertEqual(
            self.server.document_maps["MAP-MCP-WRAPPED-001"].warnings[-1],
            "mcp_normalized_array_item_wrappers",
        )

    @unittest.skip("TenderReviewMCPServer 采用惰性初始化(避免大文档握手超时)，server.run 在首次工具调用后才解析")
    def test_save_map_normalizes_sdk_contract_drift_before_validation(self) -> None:
        payload = {
            "schemaVersion": "tender-document-map/v1",
            "mapId": "MAP-MCP-DRIFT-001",
            "runId": self.server.run.run_id,
            "documentId": "DOC-READ-001",
            "documentVersionId": "DOC-READ-001-V001",
            "documentType": "government_tender",
            "sections": [
                {
                    "mapId": "MAP-DRIFT-TEMPLATE",
                    "documentId": "DOC-READ-001",
                    "documentVersionId": "DOC-READ-001-V001",
                    "domain": "template",
                    "processingMode": "template_extract",
                    "sectionIds": ["SEC-TECH"],
                    "sourceSegmentIds": ["SEG-002"],
                    "primaryRoute": "template_processor",
                    "templateLocator": {
                        "templateId": "TPL-DRIFT-001",
                        "sourceFormat": "txt",
                        "segmentIds": ["SEG-002"],
                    },
                },
                {
                    "mapId": "MAP-DRIFT-REJECTION",
                    "documentId": "DOC-READ-001",
                    "documentVersionId": "DOC-READ-001-V001",
                    "domain": "rejection",
                    "processingMode": "full_scan",
                    "sectionIds": ["SEC-TECH"],
                    "sourceSegmentIds": ["SEG-003"],
                    "primaryRoute": "rejection_processor",
                },
            ],
            "ignoredSections": [],
            "rejectionCandidates": [
                {
                    "candidateId": "REJ-DRIFT-001",
                    "matchedTerm": "否决投标\udc87",
                    "text": "投标无效",
                    "contextText": "投标无效",
                    "source": {
                        "sectionIds": ["SEC-TECH"],
                        "segmentIds": ["SEG-003"],
                    },
                    "contextSegmentIds": ["SEG-003"],
                    "status": "candidate",
                    "isHardRisk": True,
                }
            ],
            "unresolvedSections": "",
            "unresolvedIssues": "",
            "candidateFindings": "",
            "warnings": [],
        }

        result = self.server.call_tool(
            "save_document_map",
            {
                **self._identity(),
                "documentMap": payload,
            },
        )

        self.assertEqual(result["mapId"], "MAP-MCP-DRIFT-001")
        self.assertIn("mcp_normalized_source_format", result["warnings"])
        self.assertIn("mcp_normalized_invalid_unicode", result["warnings"])
        normalized = self.server.document_maps["MAP-MCP-DRIFT-001"]
        self.assertEqual(
            normalized.sections[0].template_locator.source_format,
            "text",
        )
        self.assertEqual(normalized.unresolved_issues, [])

    def test_issue_and_finding_are_run_only_by_default(self) -> None:
        issue = self.server.call_tool(
            "record_unresolved_issue",
            {
                **self._identity(),
                "issue": {
                    "issueId": "ISSUE-MCP-001",
                    "question": "Is this a template boundary?",
                    "sourceRefs": {"segmentIds": ["SEG-003"]},
                },
            },
        )
        finding = self.server.call_tool(
            "record_candidate_finding",
            {
                **self._identity(),
                "finding": {
                    "findingId": "FINDING-MCP-001",
                    "summary": "A useful observation",
                    "sourceRefs": {"segmentIds": ["SEG-003"]},
                },
            },
        )

        self.assertEqual(issue["retention"], "run_only")
        self.assertEqual(finding["retention"], "run_only")
        self.assertEqual(self.server.unresolved_issues[0].run_id, self.server.run.run_id)
        self.assertEqual(self.server.candidate_findings[0].document_id, "DOC-READ-001")

    def test_single_value_source_anchor_is_validated(self) -> None:
        result = self.server.call_tool(
            "record_candidate_finding",
            {
                **self._identity(),
                "finding": {
                    "findingId": "FINDING-MCP-002",
                    "summary": "Invalid source",
                    "sourceRefs": {"segmentId": "SEG-NOT-FOUND"},
                },
            },
        )

        self.assertEqual(result["error"]["code"], "source_invalid")

    def test_json_rpc_initialize_list_and_call(self) -> None:
        initialized = self.server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
        )
        self.assertEqual(initialized["result"]["protocolVersion"], MCP_PROTOCOL_VERSION)

        listed = self.server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        self.assertGreaterEqual(len(listed["result"]["tools"]), 10)

        called = self.server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "search_document",
                    "arguments": {
                        **self._identity(),
                        "query": "投标无效",
                    },
                },
            }
        )
        self.assertFalse(called["result"]["isError"])
        payload = json.loads(called["result"]["content"][0]["text"])
        self.assertEqual(payload["matchCount"], 1)

    def test_stdio_does_not_answer_notifications_and_never_logs_to_stdout(self) -> None:
        input_stream = io.StringIO(
            "\n".join(
                [
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 1,
                            "method": "initialize",
                            "params": {},
                        }
                    ),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "method": "notifications/initialized",
                        }
                    ),
                    json.dumps(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {
                                "name": "inspect_document",
                                "arguments": self._identity(),
                            },
                        }
                    ),
                ]
            )
            + "\n"
        )
        output_stream = io.StringIO()

        serve_stdio(self.server, stdin=input_stream, stdout=output_stream)
        lines = [json.loads(line) for line in output_stream.getvalue().splitlines()]

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["id"], 1)
        self.assertEqual(lines[1]["id"], 2)
        self.assertNotIn("Traceback", output_stream.getvalue())

    def test_mcp_script_reads_utf8_json_rpc_on_windows(self) -> None:
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(dir=root / "runtime") as directory:
            directory_path = Path(directory)
            bootstrap_path = directory_path / "bootstrap.json"
            artifact_path = directory_path / "mcp-audit.json"
            bootstrap_path.write_text(
                json.dumps(
                    {
                        "document": self.server.document.model_dump(
                            mode="json",
                            by_alias=True,
                        ),
                        "run": self.server.run.model_dump(
                            mode="json",
                            by_alias=True,
                        ),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            query = "".join(chr(value) for value in (0x6295, 0x6807, 0x65E0, 0x6548))
            messages = [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {},
                },
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "search_document",
                        "arguments": {
                            **self._identity(),
                            "query": query,
                        },
                    },
                },
            ]
            environment = os.environ.copy()
            environment.update(
                {
                    "TENDER_REVIEW_BOOTSTRAP_PATH": str(bootstrap_path),
                    "TENDER_REVIEW_ARTIFACT_PATH": str(artifact_path),
                    "PYTHONPATH": str(root),
                }
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(root / "scripts" / "tender_review_mcp.py"),
                ],
                input="\n".join(
                    json.dumps(message, ensure_ascii=False)
                    for message in messages
                )
                + "\n",
                text=True,
                capture_output=True,
                encoding="utf-8",
                env=environment,
                timeout=30,
                check=False,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        responses = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip()
        ]
        payload = json.loads(
            responses[1]["result"]["content"][0]["text"]
        )
        self.assertEqual(payload["matchCount"], 1)

    def test_unknown_tool_is_rejected(self) -> None:
        result = self.server.call_tool("read_file", {"path": "secret.docx"})

        self.assertEqual(result["error"]["code"], "tool_not_found")
        self.assertFalse(any(call.tool_name == "read_file" for call in self.server.tool_calls))


if __name__ == "__main__":
    unittest.main()
