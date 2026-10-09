from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from backend.app.agent_runtime.parse_semantics import ClauseExtraction, hydrate_source_traces, validate_clause_batch
from backend.app.agent_runtime.resolved_tender import WritingRequirement
from backend.app.cloud_agent.run_service import CloudRunService
from backend.app.pi_agent.client import PiClient, PiError
from backend.app.pi_agent.config import PiConfig
from backend.app.pi_agent.orchestrator import PiParseOrchestrationError
from backend.app.pi_agent.prompts.parse_v2 import tender_clause_prompt
from backend.app.pi_agent.resolved_parse import PiResolvedParseOrchestrator, _agent_dump, _error_detail
from backend.tests.test_native_parse_v2 import AnnotatedProtocolClient


class ParseExecutionTests(unittest.TestCase):
    def parse(self, client, **options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.txt"
            path.write_text("offline-test", encoding="utf-8")
            with patch("backend.app.pi_agent.resolved_parse.attach_template_fragments_to_bundle", side_effect=client.fragments):
                return PiResolvedParseOrchestrator(client, **options).parse_tender(
                    path, workspace_id="w1", project_id="p1", project_context={},
                    progress_callback=self.progress.append_event, node_callback=self.nodes.append,
                )

    def setUp(self):
        self.nodes = []

        class Progress(list):
            def append_event(self, *event):
                self.append(event)

        self.progress = Progress()

    def test_parallel_batches_keep_results_and_monotonic_progress(self):
        client = AnnotatedProtocolClient()
        original = client.extract_tender_clauses
        lock = threading.Lock()
        active = 0
        maximum = 0

        def extract(**kwargs):
            nonlocal active, maximum
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.02)
                return original(**kwargs)
            finally:
                with lock:
                    active -= 1

        client.extract_tender_clauses = extract
        result = self.parse(client, batch_chars=5000, max_workers=2)
        self.assertEqual(maximum, 2)
        self.assertEqual(len(result["resolvedTenderV2"]["requirements"]), 10)
        self.assertTrue(result["parseReviewReport"]["readyForWriting"])

        self.assertEqual([event[2] for event in self.progress], sorted(event[2] for event in self.progress))
        finished = [event for event in self.nodes if event["status"] in {"success", "failed", "skipped"}]
        self.assertEqual(len(finished), self.progress[-1][3])
        self.assertEqual(len({(event["agentId"], event["label"]) for event in finished}), len(finished))
        self.assertTrue(all(event["agentResult"]["finishedAt"] for event in finished if event["status"] == "success"))

    def test_rejected_repair_is_logged_without_a_third_full_retry(self):
        client = AnnotatedProtocolClient()
        original = client.extract_tender_clauses

        def extract(**kwargs):
            payload = original(**kwargs)
            next(item for item in payload["requirements"] if item["responseRequired"] is True).pop("applicability")
            return payload

        client.extract_tender_clauses = extract
        client.repair_tender_clauses = lambda **kwargs: {"removeAssessmentTargets": ["assessments:0"]}
        result = self.parse(client, batch_chars=90000)
        record = next(item for item in result["agentResults"] if item["agentId"] == "TenderClauseAgent")
        self.assertEqual(client.calls.count("clauses"), 1)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(len(record["output"]["attempts"]), 2)
        self.assertEqual(record["output"]["attempts"][1]["repairResult"], {"removeAssessmentTargets": ["assessments:0"]})
        self.assertIn("repair may remove", record["output"]["attempts"][1]["error"])
        self.assertFalse(result["parseReviewReport"]["readyForWriting"])

    def test_correction_reports_cross_field_reason_and_live_failed_attempt(self):
        client = AnnotatedProtocolClient()
        original = client.extract_tender_clauses
        corrections = []

        def extract(**kwargs):
            corrections.append(kwargs["correction"])
            payload = original(**kwargs)
            if not kwargs["correction"]:
                next(item for item in payload["requirements"] if item["responseRequired"] is True).pop("applicability")
            return payload

        client.extract_tender_clauses = extract
        result = self.parse(client, batch_chars=90000)
        self.assertIn("responseRequired requires a known bidder obligation", corrections[1])
        failed = next(event for event in self.nodes if event["status"] == "attempt_failed")
        self.assertIn("attemptResult", failed)
        self.assertEqual(failed["attempt"], 1)
        self.assertTrue(any(event[1].startswith("retrying:") for event in self.progress))
        self.assertTrue(result["parseReviewReport"]["readyForWriting"])
        record = next(item for item in result["agentResults"] if item["agentId"] == "TenderClauseAgent")
        self.assertEqual(record["output"]["attempts"][1]["mode"], "partial_repair")
        self.assertEqual(record["output"]["attempts"][1]["repairTargetCount"], 1)
        self.assertIn("repairResult", record["output"]["attempts"][1])

    def test_prompt_requires_semantic_fields_without_invented_anchors(self):
        prompt = tender_clause_prompt(inputs={})
        schema = json.loads(prompt.split("【严格输出契约】\n", 1)[1].split("\n【本次输入】", 1)[0])
        self.assertIn("applicability", schema["$defs"]["WritingRequirement"]["required"])
        for name in ("WritingRequirement", "ParameterConstraint", "EvidenceRequirement"):
            self.assertIn("sourceTrace", schema["$defs"][name]["required"])
            self.assertEqual(schema["$defs"][name]["properties"]["sourceTrace"]["minItems"], 1)
        self.assertEqual(set(schema["$defs"]["SourceTrace"]["properties"]), {"segmentId", "quote"})
        self.assertIn("responseRequired=false", prompt)
        self.assertIn("conditional", prompt)
        with self.assertRaises(ValidationError) as raised:
            ClauseExtraction.model_validate({"requirements": [{"requirementId": "r1", "text": "义务", "actor": "bidder", "responseRequired": True}],
                                            "evidenceRequirements": [], "assessments": []})
        self.assertIn("known bidder obligation", _error_detail(raised.exception))

    def test_compact_model_inputs_preserve_semantics_and_restorable_sources(self):
        client = AnnotatedProtocolClient()
        result = self.parse(client, batch_chars=90000)
        tender = result["resolvedTenderV2"]
        for payload in tender["requirements"]:
            requirement = WritingRequirement.model_validate(payload)
            compact = _agent_dump(requirement)
            restored = hydrate_source_traces({"requirements": [compact]}, document_blocks=tender["documentBlocks"],
                                            source_file=tender["sourceFile"])["requirements"][0]
            self.assertEqual(WritingRequirement.model_validate(restored), requirement)
            self.assertLess(len(json.dumps(compact)), len(json.dumps(payload)))

    def test_long_local_validation_feedback_remains_actionable_but_provider_errors_are_redacted(self):
        with self.assertRaises(ValidationError) as raised:
            ClauseExtraction.model_validate({"requirements": [{"requirementId": f"r{index}", "text": "义务",
                                            "actor": "bidder", "responseRequired": True} for index in range(8)],
                                            "evidenceRequirements": [], "assessments": []})
        detail = _error_detail(raised.exception)
        self.assertIn("known bidder obligation", detail)
        self.assertIn("共8处", detail)
        self.assertNotIn("diagnostic redacted", detail)
        issue = ValueError("coverage_incomplete: missing=" + ",".join(f"SEG-{index:06d}@0:20" for index in range(15)))
        self.assertIn("SEG-000000", _error_detail(issue, contract=True))
        self.assertIn("diagnostic redacted", _error_detail(PiError("document " + "sensitive" * 40)))
        secret = "sk-example0123456789"
        self.assertNotIn(secret, _error_detail(ValueError(f"api_key={secret}"), contract=True))

    def test_source_coverage_errors_identify_view_and_requirement_to_correct(self):
        client = AnnotatedProtocolClient()
        self.parse(client, batch_chars=90000)
        batch = client.inputs[0]
        payload = client.extract_tender_clauses(inputs=batch)
        assessment = next(item for item in payload["assessments"] if item["disposition"] == "extracted")
        other = next(item for item in payload["requirements"] if item["requirementId"] not in assessment["requirementIds"])
        assessment["requirementIds"] = [other["requirementId"]]
        with self.assertRaisesRegex(ValueError, "assessment requirement does not cite this source view") as raised:
            validate_clause_batch(payload, batch)
        self.assertIn(assessment["viewId"], str(raised.exception))
        self.assertIn(other["requirementId"], str(raised.exception))
        payload["assessments"] = payload["assessments"][1:]
        with self.assertRaisesRegex(ValueError, "coverage_incomplete") as raised:
            validate_clause_batch(payload, batch)
        self.assertIn(assessment["viewId"], str(raised.exception))

    def test_overall_deadline_stops_scheduling_new_batches(self):
        client = AnnotatedProtocolClient()
        original = client.run_json
        clock = [0.0]

        def slow_structure(*args, **kwargs):
            clock[0] = 2.0
            return original(*args, **kwargs)

        client.run_json = slow_structure
        with self.assertRaisesRegex(PiParseOrchestrationError, "总时限"):
            with patch("backend.app.pi_agent.resolved_parse.time.monotonic", side_effect=lambda: clock[0]):
                self.parse(client, batch_chars=90000, timeout_seconds=1)

    def test_service_checkpoints_and_reuses_only_active_identical_uploads(self):
        client = AnnotatedProtocolClient()
        with tempfile.TemporaryDirectory() as directory:
            service = CloudRunService(Path(directory), pi_client=client, agent_provider="pi")
            arguments = {"workspace_id": "", "project_id": "", "filename": "source.txt", "content": b"offline"}
            first = service.create_run(**arguments)
            self.assertEqual(service.create_run(**arguments)["runId"], first["runId"])
            self.assertNotEqual(service.create_run(**{**arguments, "content": b"changed"})["runId"], first["runId"])
            self.assertNotEqual(service.create_run(**{**arguments, "workspace_id": "other"})["runId"], first["runId"])
            with patch("backend.app.pi_agent.resolved_parse.attach_template_fragments_to_bundle", side_effect=client.fragments):
                finished = service.process_run(first["runId"])
            self.assertEqual(finished["status"], "outline_ready")
            events = [json.loads(line) for line in (Path(directory) / finished["nodeArtifact"]).read_text(encoding="utf-8").splitlines()]
            self.assertTrue(any(event["status"] == "success" and event.get("agentResult") for event in events))
            self.assertEqual(finished["activeNodes"], [])
            self.assertEqual(finished["completedNodes"], finished["totalNodes"])
            self.assertNotEqual(service.create_run(**arguments)["runId"], first["runId"])
            calls_before = deepcopy(client.calls)
            service.process_run(first["runId"])
            self.assertEqual(client.calls, calls_before)

    def test_service_does_not_execute_duplicate_background_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            service = CloudRunService(Path(directory), agent_provider="pi")
            run = service.create_run(workspace_id="", project_id="", filename="a.txt", content=b"offline")
            entered = threading.Event()
            release = threading.Event()

            def process(run_id):
                entered.set()
                release.wait(timeout=2)
                return service.repository.get(run_id)

            with patch.object(service, "_process_run", side_effect=process) as invoke:
                worker = threading.Thread(target=service.process_run, args=(run["runId"],))
                worker.start()
                try:
                    self.assertTrue(entered.wait(timeout=2))
                    service.process_run(run["runId"])
                    self.assertEqual(invoke.call_count, 1)
                finally:
                    release.set()
                    worker.join(timeout=2)


class TransportCleanupTests(unittest.TestCase):
    def client(self, script):
        client = PiClient(PiConfig(enabled=True, command=sys.executable, provider="", model="", api_key="",
                                  timeout_seconds=2, working_dir=""))
        client._command = lambda **kwargs: [sys.executable, "-u", "-c", script]
        return client

    def invoke(self, client, *, deadline=None):
        created = []
        original = subprocess.Popen

        def capture(*args, **kwargs):
            process = original(*args, **kwargs)
            created.append(process)
            return process

        with patch("backend.app.pi_agent.transport.subprocess.Popen", side_effect=capture):
            if deadline is None:
                result = client.run_json("offline")
            else:
                with client.request_deadline(deadline):
                    with self.assertRaises(PiError):
                        client.run_json("offline")
                result = None
        process = created[0]
        self.assertIsNotNone(process.poll())
        self.assertTrue(all(stream.closed for stream in (process.stdin, process.stdout, process.stderr)))
        return result

    def test_success_reaps_child_and_closes_streams(self):
        client = self.client('import sys,json;sys.stdin.readline();print(json.dumps({"type":"message_end","message":{"role":"assistant","content":[{"type":"text","text":"{\\"ok\\":true}"}]}}),flush=True);print(json.dumps({"type":"agent_settled"}),flush=True);sys.stdin.read()')
        self.assertEqual(self.invoke(client), {"ok": True})

    def test_deadline_reaps_child_and_closes_streams(self):
        client = self.client("import sys;sys.stdin.readline();sys.stdin.read()")
        self.invoke(client, deadline=time.monotonic() + 0.2)

    def test_expired_deadline_does_not_launch_pi(self):
        client = self.client("")
        with patch("backend.app.pi_agent.transport.subprocess.Popen") as launch:
            with client.request_deadline(time.monotonic() - 1):
                with self.assertRaisesRegex(PiError, "总时限"):
                    client.run_json("offline")
            self.assertIsNone(getattr(client._request_limits, "deadline", None))
            launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
