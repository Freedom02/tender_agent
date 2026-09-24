from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from backend.app.tender_matching.analysis_contracts import build_analysis_pack
from backend.app.workbuddy_agent.document_map import (
    CandidateFinding,
    DocumentMap,
    DocumentMapCoverage,
    DocumentMapSection,
    DocumentSourceAnchor,
    RejectionCandidate,
    TemplateLocator,
    UnresolvedIssue,
    validate_document_map,
)
from backend.app.workbuddy_agent.document_map.projections import project_document_map


class DocumentMapContractTests(unittest.TestCase):
    def _map(
        self,
        *,
        sections: list[DocumentMapSection] | None = None,
        rejection_candidates: list[RejectionCandidate] | None = None,
        unresolved_issues: list[UnresolvedIssue] | None = None,
        candidate_findings: list[CandidateFinding] | None = None,
    ) -> DocumentMap:
        return DocumentMap(
            mapId="MAP-001",
            runId="RUN-001",
            documentId="DOC-001",
            documentVersionId="DOC-001-V001",
            sections=sections or [],
            rejectionCandidates=rejection_candidates or [],
            unresolvedIssues=unresolved_issues or [],
            candidateFindings=candidate_findings or [],
        )

    def _technical_section(self, *, segment_id: str = "SEG-001") -> DocumentMapSection:
        return DocumentMapSection(
            mapId="MAP-TECH-001",
            documentId="DOC-001",
            documentVersionId="DOC-001-V001",
            domain="technical",
            processingMode="fine_extract",
            title="Technical requirements",
            sourceSegmentIds=[segment_id],
            primaryRoute="technical_processor",
        )

    def test_valid_map_can_be_constructed_with_aliases(self) -> None:
        document_map = self._map(sections=[self._technical_section()])

        payload = document_map.model_dump(mode="json", by_alias=True)

        self.assertEqual(payload["mapId"], "MAP-001")
        self.assertEqual(payload["sections"][0]["sourceSegmentIds"], ["SEG-001"])
        self.assertNotIn("map_id", payload)

    def test_contracts_reject_unknown_fields(self) -> None:
        with self.assertRaises(ValidationError):
            DocumentMap(
                mapId="MAP-001",
                runId="RUN-001",
                documentId="DOC-001",
                documentVersionId="DOC-001-V001",
                unexpected=True,
            )

    def test_contract_ranges_must_be_explicitly_ignored(self) -> None:
        with self.assertRaises(ValidationError):
            DocumentMapSection(
                mapId="MAP-CONTRACT-001",
                domain="contract",
                sourceSegmentIds=["SEG-001"],
                primaryRoute="display_processor",
            )

        contract = DocumentMapSection(
            mapId="MAP-CONTRACT-001",
            domain="contract",
            sourceSegmentIds=["SEG-001"],
            primaryRoute="ignored_contract",
            processingMode="ignore",
            ignored=True,
        )
        result = validate_document_map(
            self._map(sections=[contract]),
            segments=[{"id": "SEG-001"}],
        )

        self.assertTrue(result.valid)

    def test_template_ranges_require_a_locator(self) -> None:
        with self.assertRaises(ValidationError):
            DocumentMapSection(
                mapId="MAP-TEMPLATE-001",
                domain="template",
                sourceSegmentIds=["SEG-001"],
                primaryRoute="template_processor",
            )

        locator = TemplateLocator(
            templateId="TPL-001",
            title="Bid letter",
            segmentIds=["SEG-001"],
        )
        template = DocumentMapSection(
            mapId="MAP-TEMPLATE-001",
            domain="template",
            sourceSegmentIds=["SEG-001"],
            primaryRoute="template_processor",
            templateLocator=locator,
        )
        self.assertEqual(template.template_locator.template_id, "TPL-001")

    def test_validator_rejects_unknown_single_value_source_ids(self) -> None:
        section = DocumentMapSection(
            mapId="MAP-UNKNOWN-001",
            domain="technical",
            processingMode="fine_extract",
            source=DocumentSourceAnchor(
                sectionId="SEC-UNKNOWN",
                segmentId="SEG-UNKNOWN",
                tableId="TABLE-UNKNOWN",
            ),
            primaryRoute="technical_processor",
        )
        result = validate_document_map(
            self._map(sections=[section]),
            sections=[{"sectionId": "SEC-001"}],
            segments=[{"id": "SEG-001"}],
            tables=[{"tableId": "TABLE-001"}],
        )

        self.assertFalse(result.valid)
        self.assertEqual(
            {
                issue.code
                for issue in result.issues
                if issue.severity == "critical"
            },
            {"unknown_section_id", "unknown_segment_id", "unknown_table_id"},
        )

    def test_project_info_cannot_use_enterprise_matching_route(self) -> None:
        section = DocumentMapSection(
            mapId="MAP-PROJECT-001",
            domain="project_info",
            sourceSegmentIds=["SEG-001"],
            primaryRoute="technical_processor",
        )
        result = validate_document_map(
            self._map(sections=[section]),
            segments=[{"id": "SEG-001"}],
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "project_info_enterprise_route",
            {issue.code for issue in result.issues},
        )

    def test_rejection_route_is_explicit_and_hard_risk_cannot_be_downgraded(self) -> None:
        rejection = DocumentMapSection(
            mapId="MAP-REJECTION-001",
            domain="rejection",
            sourceSegmentIds=["SEG-001"],
            primaryRoute="rejection_processor",
        )
        candidate = RejectionCandidate(
            candidateId="REJ-001",
            matchedTerm="废标",
            source=DocumentSourceAnchor(segmentId="SEG-001"),
            isHardRisk=True,
            status="rejected",
        )
        result = validate_document_map(
            self._map(
                sections=[rejection],
                rejection_candidates=[candidate],
            ),
            segments=[{"id": "SEG-001"}],
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "hard_rejection_downgraded",
            {issue.code for issue in result.issues},
        )

    def test_high_risk_coverage_uses_actual_map_references(self) -> None:
        result = validate_document_map(
            self._map(sections=[self._technical_section()]),
            segments=[{"id": "SEG-001"}, {"id": "SEG-002"}],
            high_risk_segment_ids=["SEG-002"],
        )

        self.assertFalse(result.valid)
        self.assertIn(
            "high_risk_coverage_gap",
            {issue.code for issue in result.issues},
        )

    def test_section_reference_covers_all_segments_in_that_section(self) -> None:
        section = DocumentMapSection(
            mapId="MAP-SECTION-001",
            domain="technical",
            processingMode="fine_extract",
            sectionIds=["SEC-TECH"],
            primaryRoute="technical_processor",
        )
        result = validate_document_map(
            self._map(sections=[section]),
            sections=[
                {
                    "sectionId": "SEC-TECH",
                    "segmentIds": ["SEG-001", "SEG-002"],
                }
            ],
            segments=[{"id": "SEG-001"}, {"id": "SEG-002"}],
            high_risk_segment_ids=["SEG-002"],
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.coverage["highRiskCoveredCount"], 1)

    def test_validator_normalizes_coverage_from_explicit_section_references(self) -> None:
        section = DocumentMapSection(
            mapId="MAP-COVERAGE-001",
            domain="technical",
            processingMode="fine_extract",
            sectionIds=["SEC-TECH"],
            sourceSegmentIds=["SEG-002"],
            primaryRoute="technical_processor",
        )
        document_map = self._map(sections=[section])
        document_map.coverage = DocumentMapCoverage(
            coveredSegmentIds=["SEG-002"],
            unclassifiedSegmentIds=["SEG-001"],
        )

        result = validate_document_map(
            document_map,
            sections=[
                {
                    "sectionId": "SEC-TECH",
                    "segmentIds": ["SEG-001", "SEG-002"],
                }
            ],
            segments=[{"id": "SEG-001"}, {"id": "SEG-002"}],
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.warnings, [])
        self.assertEqual(
            result.normalized_map.coverage.covered_segment_ids,
            ["SEG-001", "SEG-002"],
        )
        self.assertEqual(
            result.normalized_map.coverage.unclassified_segment_ids,
            [],
        )

    def test_unresolved_issues_and_candidate_findings_are_run_only_by_default(self) -> None:
        document_map = self._map(
            unresolved_issues=[
                UnresolvedIssue(
                    issueId="ISSUE-001",
                    question="Is this a template or a requirement?",
                )
            ],
            candidate_findings=[
                CandidateFinding(
                    findingId="FINDING-001",
                    summary="A useful observation",
                )
            ],
        )
        projection = project_document_map(document_map)

        self.assertEqual(projection["unresolvedIssues"][0]["retention"], "run_only")
        self.assertEqual(
            projection["candidateFindings"][0]["retention"],
            "run_only",
        )
        self.assertEqual(projection["mapStats"]["candidateFindingCount"], 1)
        self.assertNotIn("fullText", projection)
        self.assertNotIn("toolCalls", projection)

    def test_document_map_projects_into_analysis_pack_without_full_text(self) -> None:
        document_map = self._map(sections=[self._technical_section()])
        pack = build_analysis_pack(
            requirements=[],
            segments=[{"id": "SEG-001", "text": "technical text"}],
            sections=[],
            tables=[],
            document_map=document_map,
        )

        self.assertEqual(pack["documentMap"]["mapId"], "MAP-001")
        self.assertEqual(pack["mapStats"]["sectionCount"], 1)
        self.assertIn("unresolvedIssues", pack)
        self.assertIn("candidateFindings", pack)
        self.assertNotIn("technical text", json.dumps(pack))


if __name__ == "__main__":
    unittest.main()
