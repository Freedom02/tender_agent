from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.app.tender_matching.document_models import DocumentIR, DocumentSection, DocumentSegment
from backend.app.tender_matching.fact_models import (
    AnalysisArtifact,
    FactArtifactStore,
    build_analysis_artifact,
    evidence_from_segment,
    requirement_from_legacy,
)


class FactModelTests(unittest.TestCase):
    def _document(self) -> DocumentIR:
        segment = DocumentSegment(
            id="SEG-001",
            sourceLocator="page 3, block 4",
            text="平台应支持统一身份认证。",
            contentType="pdf_text_block",
            order=0,
            page=3,
        )
        section = DocumentSection(
            id="SEC-TECH",
            sectionId="SEC-TECH",
            heading="技术要求",
            sectionPath=["第三章 技术要求"],
            sectionType="technical",
            segmentIds=["SEG-001"],
            startLocator="page 3, block 4",
            endLocator="page 3, block 4",
        )
        return DocumentIR(
            documentId="DOC-1",
            documentVersionId="DOC-1-V001",
            fileName="tender.pdf",
            fileHash="abc",
            originalFormat="pdf",
            text=segment.text,
            segments=[segment],
            units=[segment],
            sections=[section],
        )

    def test_evidence_and_requirement_share_document_version_and_source(self) -> None:
        document = self._document()
        evidence = evidence_from_segment(
            document,
            evidence_id="EVD-001",
            segment_ids=["SEG-001"],
            section_id="SEC-TECH",
            section_path=["第三章 技术要求"],
            confidence=0.92,
        )
        requirement = requirement_from_legacy(
            {
                "requirementId": "REQ-001",
                "category": "technical",
                "rawText": "平台应支持统一身份认证。",
                "sourceSegmentIds": ["SEG-001"],
                "sectionId": "SEC-TECH",
                "sectionPath": ["第三章 技术要求"],
                "importance": "high",
                "mandatory": True,
            },
            document=document,
            evidence=evidence,
        )

        self.assertEqual(evidence.document_version_id, "DOC-1-V001")
        self.assertEqual(evidence.page_start, 3)
        self.assertEqual(requirement.document_version_id, evidence.document_version_id)
        self.assertEqual(requirement.evidence_ids, ["EVD-001"])

    def test_evidence_rejects_unknown_segment(self) -> None:
        with self.assertRaises(ValueError):
            evidence_from_segment(
                self._document(),
                evidence_id="EVD-404",
                segment_ids=["SEG-404"],
            )

    def test_artifact_round_trip_and_filesystem_versioned_paths(self) -> None:
        document = self._document()
        evidence = evidence_from_segment(
            document,
            evidence_id="EVD-001",
            segment_ids=["SEG-001"],
        )
        requirement = requirement_from_legacy(
            {
                "requirementId": "REQ-001",
                "category": "technical",
                "rawText": "平台应支持统一身份认证。",
                "sourceSegmentIds": ["SEG-001"],
            },
            document=document,
            evidence=evidence,
        )
        artifact = build_analysis_artifact(
            project_id="PROJECT-1",
            document=document,
            run_id="RUN-001",
            requirements=[requirement],
            evidences=[evidence],
        )

        with tempfile.TemporaryDirectory() as directory:
            store = FactArtifactStore(Path(directory))
            parsed_path = store.save_document("PROJECT-1", document)
            facts_path = store.save_artifact(artifact)
            restored_document = store.load_document("PROJECT-1", document.document_version_id)
            restored_artifact = store.load_artifact("PROJECT-1", document.document_version_id)

        self.assertTrue(parsed_path.name.endswith(".json"))
        self.assertTrue(facts_path.name.endswith(".json"))
        self.assertEqual(restored_document.document_version_id, "DOC-1-V001")
        self.assertEqual(restored_artifact.requirements[0].requirement_id, "REQ-001")
        self.assertEqual(restored_artifact.evidences[0].source_segment_ids, ["SEG-001"])
        self.assertEqual(restored_artifact.run.project_id, "PROJECT-1")
        self.assertIsInstance(restored_artifact, AnalysisArtifact)

    def test_analysis_run_round_trip(self) -> None:
        document = self._document()
        artifact = build_analysis_artifact(
            project_id="PROJECT-1",
            document=document,
            run_id="RUN-001",
        )
        artifact.run.project_id = "PROJECT-1"
        artifact.run.stages = []
        artifact.run.metrics = {"requirementCount": 0}

        with tempfile.TemporaryDirectory() as directory:
            store = FactArtifactStore(directory)
            path = store.save_run("PROJECT-1", artifact.run)
            restored = store.load_run("PROJECT-1", "RUN-001")

        self.assertTrue(path.as_posix().endswith("projects/PROJECT-1/runs/RUN-001.json"))
        self.assertEqual(restored.project_id, "PROJECT-1")
        self.assertEqual(restored.metrics["requirementCount"], 0)

    def test_invalid_artifact_is_rejected_on_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FactArtifactStore(directory)
            path = store.facts_path("PROJECT-1", "DOC-1-V001")
            path.parent.mkdir(parents=True)
            path.write_text('{"artifactId":"bad"}', encoding="utf-8")
            with self.assertRaises(ValidationError):
                store.load_artifact("PROJECT-1", "DOC-1-V001")

    def test_unsafe_path_components_are_rejected(self) -> None:
        store = FactArtifactStore("runtime")
        with self.assertRaises(ValueError):
            store.parsed_path("..", "DOC-1-V001")
        with self.assertRaises(ValueError):
            store.facts_path("PROJECT-1", "../DOC-1")


if __name__ == "__main__":
    unittest.main()
