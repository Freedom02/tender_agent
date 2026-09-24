from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.tender_matching.fact_models import AnalysisRun
from backend.app.tender_matching.observability import (
    AnalysisRunRecorder,
    observe_llm_call,
)


class ObservabilityTests(unittest.TestCase):
    def test_recorder_persists_stages_and_llm_counters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = AnalysisRunRecorder(
                Path(directory),
                project_id="PROJECT-1",
                document_version_id="DOC-1-V001",
                run_id="RUN-001",
                parser_name="pypdf_structured",
                input_artifact_paths=["source/tender.pdf"],
            )
            recorder.record_stage(
                "parse_document",
                metrics={"segmentCount": 3},
                output_paths=["parsed/DOC-1-V001.json"],
            )

            @observe_llm_call
            def fake_call() -> dict[str, object]:
                return {
                    "success": True,
                    "model": "test-model",
                    "usage": {"prompt_tokens": 12, "completion_tokens": 8},
                }

            fake_call()
            recorder.set_manual_change_count(2)
            run = recorder.finish()
            restored = AnalysisRun.model_validate_json(
                Path(directory, "projects", "PROJECT-1", "runs", "RUN-001.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(run.status, "completed")
        self.assertEqual(restored.document_version_id, "DOC-1-V001")
        self.assertEqual(restored.llm_call_count, 1)
        self.assertEqual(restored.llm_input_tokens, 12)
        self.assertEqual(restored.llm_output_tokens, 8)
        self.assertEqual(restored.manual_change_count, 2)
        self.assertEqual(restored.stages[0].stage, "parse_document")
        self.assertEqual(restored.output_artifact_paths, ["parsed/DOC-1-V001.json"])

    def test_failed_stage_is_recorded_as_failed_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            recorder = AnalysisRunRecorder(
                directory,
                project_id="PROJECT-1",
                document_version_id="DOC-1-V001",
                run_id="RUN-FAILED",
            )
            run = recorder.fail("模型服务不可用", stage="extract_requirements")

        self.assertEqual(run.status, "failed")
        self.assertIn("模型服务不可用", run.errors)
        self.assertEqual(run.stages[-1].stage, "extract_requirements")
        self.assertEqual(run.stages[-1].status, "failed")


if __name__ == "__main__":
    unittest.main()
