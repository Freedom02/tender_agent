from __future__ import annotations

import unittest

from backend.app.workbuddy_agent.document_map import (
    DocumentMap,
    DocumentMapSection,
    DocumentRange,
    DocumentSourceAnchor,
    TemplateLocator,
    RejectionCandidate,
    project_document_map,
    validate_document_map,
)


class DocumentMapContractTests(unittest.TestCase):
    def _source(self) -> DocumentSourceAnchor:
        return DocumentSourceAnchor(
            documentId="DOC-1",
            documentVersionId="DOC-1-V1",
            sourceFileId="FILE-1",
            sourceFormat="docx",
            sectionIds=["SEC-1"],
            segmentIds=["SEG-1", "SEG-2"],
            sourceLocator="第1页",
        )

    def _range(self) -> DocumentRange:
        return DocumentRange(
            rangeId="RANGE-1",
            startSegmentId="SEG-1",
            endSegmentId="SEG-2",
            segmentIds=["SEG-1", "SEG-2"],
            sectionIds=["SEC-1"],
            source=self._source(),
        )

    def test_eight_domains_are_expressible_with_processing_routes(self) -> None:
        domains = (
            "technical",
            "qualification",
            "commercial",
            "evaluation",
            "contract",
            "template",
            "project_info",
            "rejection",
        )
        sections = []
        for index, domain in enumerate(domains, start=1):
            section_id = f"SEC-{index}"
            common = {
                "mapId": f"MAP-{index}",
                "documentId": "DOC-1",
                "documentVersionId": "DOC-1-V1",
                "domain": domain,
                "title": domain,
                "sectionIds": [section_id],
                "sourceSegmentIds": ["SEG-1"],
                "source": DocumentSourceAnchor(
                    documentId="DOC-1",
                    documentVersionId="DOC-1-V1",
                    sectionIds=[section_id],
                    segmentIds=["SEG-1"],
                ),
            }
            if domain == "technical":
                common["primaryRoute"] = "technical_processor"
                common["processingMode"] = "fine_extract"
            elif domain == "qualification":
                common["primaryRoute"] = "qualification_processor"
                common["processingMode"] = "fine_extract"
            elif domain == "rejection":
                common["primaryRoute"] = "rejection_processor"
            elif domain == "contract":
                common["primaryRoute"] = "ignored_contract"
                common["processingMode"] = "ignore"
                common["ignored"] = True
            elif domain == "template":
                common["primaryRoute"] = "template_processor"
                common["templateLocator"] = TemplateLocator(
                    templateId=f"TPL-{index}",
                    title="投标函",
                    sectionIds=[section_id],
                )
            elif domain == "evaluation":
                common["primaryRoute"] = "evaluation_processor"
            else:
                common["primaryRoute"] = "display_processor"
            sections.append(DocumentMapSection(**common))

        document_map = DocumentMap(
            mapId="MAP-ROOT",
            runId="RUN-1",
            documentId="DOC-1",
            documentVersionId="DOC-1-V1",
            sections=[item for item in sections if item.domain != "contract"],
            ignoredSections=[item for item in sections if item.domain == "contract"],
        )
        self.assertEqual(len(document_map.sections), 7)
        self.assertEqual(document_map.ignored_sections[0].primary_route, "ignored_contract")

    def test_contract_must_be_ignored_and_template_needs_locator(self) -> None:
        with self.assertRaises(ValueError):
            DocumentMapSection(
                mapId="MAP-C",
                domain="contract",
                primaryRoute="display_processor",
                source=self._source(),
            )
        with self.assertRaises(ValueError):
            DocumentMapSection(
                mapId="MAP-T",
                domain="template",
                primaryRoute="template_processor",
                source=self._source(),
            )

    def test_validator_rejects_fabricated_source_ids(self) -> None:
        document_map = DocumentMap(
            mapId="MAP-1",
            runId="RUN-1",
            documentId="DOC-1",
            documentVersionId="DOC-1-V1",
            sections=[
                DocumentMapSection(
                    mapId="MAP-T",
                    domain="technical",
                    primaryRoute="technical_processor",
                    processingMode="fine_extract",
                    sectionIds=["SEC-MISSING"],
                    sourceSegmentIds=["SEG-MISSING"],
                    source=DocumentSourceAnchor(
                        documentId="DOC-1",
                        documentVersionId="DOC-1-V1",
                        sectionIds=["SEC-MISSING"],
                        segmentIds=["SEG-MISSING"],
                    ),
                )
            ],
        )
        result = validate_document_map(
            document_map,
            sections=[{"id": "SEC-1"}],
            segments=[{"id": "SEG-1"}],
        )
        self.assertFalse(result.valid)
        self.assertIn(
            "unknown_section_id",
            {issue.code for issue in result.issues},
        )
        self.assertIn(
            "unknown_segment_id",
            {issue.code for issue in result.issues},
        )

    def test_project_document_map_exposes_map_stats_and_sources(self) -> None:
        document_map = DocumentMap(
            mapId="MAP-1",
            runId="RUN-1",
            documentId="DOC-1",
            documentVersionId="DOC-1-V1",
            sections=[
                DocumentMapSection(
                    mapId="MAP-T",
                    domain="technical",
                    primaryRoute="technical_processor",
                    processingMode="fine_extract",
                    title="技术要求",
                    sectionIds=["SEC-1"],
                    sourceSegmentIds=["SEG-1", "SEG-2"],
                    ranges=[self._range()],
                    source=self._source(),
                )
            ],
            rejectionCandidates=[
                RejectionCandidate(
                    candidateId="REJ-1",
                    matchedTerm="投标无效",
                    text="投标无效",
                    source=self._source(),
                    contextSegmentIds=["SEG-2"],
                )
            ],
        )
        projection = project_document_map(document_map)
        self.assertEqual(projection["mapStats"]["sectionCount"], 1)
        self.assertEqual(projection["mapStats"]["rejectionCandidateCount"], 1)
        self.assertEqual(projection["sections"][0]["ranges"][0]["segmentIds"], ["SEG-1", "SEG-2"])
