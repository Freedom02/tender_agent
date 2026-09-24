from __future__ import annotations

import unittest

from backend.app.tender_matching.document_models import DocumentIR, DocumentSection, DocumentSegment
from backend.app.tender_matching.requirement_extractor import (
    LLM_REQUIREMENT_EXTRACTION_PROMPT,
    extract_requirements,
)
from backend.app.tender_matching.section_classifier import SectionClassification, classify_document


class RequirementExtractorTests(unittest.TestCase):
    def _document(self) -> DocumentIR:
        segments = [
            DocumentSegment(
                id="SEG-001",
                sourceLocator="page 1, block 1",
                text="项目名称：智慧平台建设项目",
                contentType="pdf_text_block",
                order=0,
                page=1,
            ),
            DocumentSegment(
                id="SEG-002",
                sourceLocator="page 2, block 1",
                text="第三章 技术要求",
                contentType="pdf_text_block",
                order=1,
                page=2,
            ),
            DocumentSegment(
                id="SEG-003",
                sourceLocator="page 2, block 2",
                text="平台必须支持统一身份认证；应支持标准接口集成。",
                contentType="pdf_text_block",
                order=2,
                page=2,
            ),
            DocumentSegment(
                id="SEG-004",
                sourceLocator="page 3, block 1",
                text="第四章 资格审查",
                contentType="pdf_text_block",
                order=3,
                page=3,
            ),
            DocumentSegment(
                id="SEG-005",
                sourceLocator="page 3, block 2",
                text="投标人须提供有效营业执照和相关资质证书。",
                contentType="pdf_text_block",
                order=4,
                page=3,
            ),
        ]
        sections = [
            DocumentSection(
                id="SEC-TECH",
                sectionId="SEC-TECH",
                sectionType="technical",
                sectionTypeHits=["technical"],
                heading="第三章 技术要求",
                sectionPath=["第三章 技术要求"],
                level=1,
                isMajor=True,
                segmentIds=["SEG-002", "SEG-003"],
            ),
            DocumentSection(
                id="SEC-QUAL",
                sectionId="SEC-QUAL",
                sectionType="qualification",
                sectionTypeHits=["qualification"],
                heading="第四章 资格审查",
                sectionPath=["第四章 资格审查"],
                level=1,
                isMajor=True,
                segmentIds=["SEG-004", "SEG-005"],
            ),
        ]
        return DocumentIR(
            documentId="DOC-EXTRACT",
            documentVersionId="DOC-EXTRACT-V001",
            fileName="tender.pdf",
            originalFormat="pdf",
            text="\n".join(item.text for item in segments),
            segments=segments,
            units=segments,
            sections=sections,
        )

    def test_extracts_technical_and_qualification_requirements_with_evidence(self) -> None:
        document = self._document()
        result = extract_requirements(document, classifications=classify_document(document))

        self.assertGreaterEqual(len(result.requirements), 3)
        self.assertEqual(result.stats["technicalCount"], 2)
        self.assertEqual(result.stats["qualificationCount"], 1)
        self.assertTrue(all(item.evidence_ids for item in result.requirements))
        self.assertTrue(all(item.source_segment_ids for item in result.requirements))
        self.assertTrue(any(item.mandatory for item in result.requirements))
        self.assertTrue(all(item.document_version_id == document.document_version_id for item in result.evidences))

    def test_project_field_keeps_source_evidence(self) -> None:
        result = extract_requirements(self._document())

        project_name = next(item for item in result.project_fields if item.field_name == "projectName")
        self.assertEqual(project_name.value, "智慧平台建设项目")
        self.assertTrue(project_name.evidence_id)

    def test_requirement_extraction_prompt_prioritizes_technical_content(self) -> None:
        self.assertIn("技术/产品/服务要求", LLM_REQUIREMENT_EXTRACTION_PROMPT)
        self.assertIn("sourceSegmentIds", LLM_REQUIREMENT_EXTRACTION_PROMPT)

    def test_max_requirements_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            extract_requirements(self._document(), max_requirements=0)

    def test_skips_heading_toc_and_background_sentences(self) -> None:
        document = self._document()
        document.segments.insert(
            1,
            DocumentSegment(
                id="SEG-TOC",
                sourceLocator="page 1, block 2",
                text="第三章 技术要求 - 2 -",
                contentType="pdf_text_block",
                order=1,
                page=1,
            ),
        )
        document.segments.insert(
            2,
            DocumentSegment(
                id="SEG-BACKGROUND",
                sourceLocator="page 1, block 3",
                text="本项目提供数字化建设服务，具体内容详见后文。",
                contentType="pdf_text_block",
                order=2,
                page=1,
            ),
        )
        result = extract_requirements(document, classifications=classify_document(document))

        self.assertFalse(any("技术要求 - 2 -" in item.raw_text for item in result.requirements))
        self.assertFalse(any(item.raw_text == "本项目提供数字化建设服务，具体内容详见后文。" for item in result.requirements))

    def test_skips_template_sections_from_fine_requirements(self) -> None:
        segments = [
            DocumentSegment(
                id="SEG-FORM-1",
                text="第七章 投标文件格式",
                order=1,
            ),
            DocumentSegment(
                id="SEG-FORM-2",
                text="技术和服务要求响应表：投标人须逐项填写响应情况并签字盖章。",
                order=2,
            ),
            DocumentSegment(
                id="SEG-TECH-1",
                text="第五章 招标内容及要求",
                order=3,
            ),
            DocumentSegment(
                id="SEG-TECH-2",
                text="平台必须支持三维场景加载。",
                order=4,
            ),
        ]
        document = DocumentIR(
            documentId="DOC-FORM",
            documentVersionId="DOC-FORM-V001",
            segments=segments,
            units=segments,
            text="\n".join(item.text for item in segments),
            sections=[
                DocumentSection(
                    id="SEC-FORM",
                    sectionId="SEC-FORM",
                    sectionType="document_format",
                    heading="第七章 投标文件格式",
                    sectionPath=["第七章 投标文件格式"],
                    segmentIds=["SEG-FORM-1", "SEG-FORM-2"],
                ),
                DocumentSection(
                    id="SEC-TECH",
                    sectionId="SEC-TECH",
                    sectionType="technical",
                    heading="第五章 招标内容及要求",
                    sectionPath=["第五章 招标内容及要求"],
                    segmentIds=["SEG-TECH-1", "SEG-TECH-2"],
                ),
            ],
        )

        result = extract_requirements(document, classifications=classify_document(document))

        self.assertEqual([item.category for item in result.requirements], ["technical"])
        self.assertIn("三维场景加载", result.requirements[0].raw_text)
        self.assertGreaterEqual(result.stats["skippedClauseCount"], 1)

    def test_clear_clause_does_not_inherit_section_review_flag(self) -> None:
        document = self._document()
        classifications = [
            SectionClassification(
                sectionId="SEC-TECH",
                headingType="technical",
                primaryType="technical",
                typeHits=["technical", "submission"],
                needsHumanReview=True,
            ),
            SectionClassification(
                sectionId="SEC-QUAL",
                headingType="qualification",
                primaryType="qualification",
                typeHits=["qualification"],
                needsHumanReview=False,
            ),
        ]

        result = extract_requirements(document, classifications=classifications)

        technical = [
            item for item in result.requirements
            if item.category == "technical"
        ]
        self.assertTrue(technical)
        self.assertFalse(any(item.needs_human_review for item in technical))

    def test_skips_standalone_business_heading(self) -> None:
        segment = DocumentSegment(
            id="SEG-COMMERCIAL-HEADING",
            text="三、商务要求（以“★”标示的内容为不允许负偏离的实质性要求）",
            order=0,
        )
        document = DocumentIR(
            documentId="DOC-COMMERCIAL-HEADING",
            documentVersionId="DOC-COMMERCIAL-HEADING-V001",
            segments=[segment],
            units=[segment],
            text=segment.text,
            sections=[
                DocumentSection(
                    id="SEC-COMMERCIAL",
                    sectionId="SEC-COMMERCIAL",
                    sectionType="commercial",
                    heading="三、商务要求（以“★”标示的内容为不允许负偏离的实质性要求）",
                    sectionPath=["三、商务要求（以“★”标示的内容为不允许负偏离的实质性要求）"],
                    segmentIds=[segment.id],
                )
            ],
        )

        result = extract_requirements(document, classifications=classify_document(document))

        self.assertEqual(result.requirements, [])

    def test_skips_service_plan_from_fine_requirements(self) -> None:
        segments = [
            DocumentSegment(
                id="SEG-SERVICE-1",
                text="十二、售后服务",
                order=0,
            ),
            DocumentSegment(
                id="SEG-SERVICE-2",
                text="中标人须提供不少于三年的免费质保和培训服务。",
                order=1,
            ),
            DocumentSegment(
                id="SEG-TECH-1",
                text="平台必须支持三维场景加载。",
                order=2,
            ),
        ]
        document = DocumentIR(
            documentId="DOC-SERVICE",
            documentVersionId="DOC-SERVICE-V001",
            segments=segments,
            units=segments,
            text="\n".join(item.text for item in segments),
            sections=[
                DocumentSection(
                    id="SEC-SERVICE",
                    sectionId="SEC-SERVICE",
                    sectionType="service_plan",
                    heading="十二、售后服务",
                    sectionPath=["十二、售后服务"],
                    segmentIds=["SEG-SERVICE-1", "SEG-SERVICE-2"],
                ),
                DocumentSection(
                    id="SEC-TECH",
                    sectionId="SEC-TECH",
                    sectionType="technical",
                    heading="第五章 技术要求",
                    sectionPath=["第五章 技术要求"],
                    segmentIds=["SEG-TECH-1"],
                ),
            ],
        )

        result = extract_requirements(document, classifications=classify_document(document))

        self.assertEqual([item.category for item in result.requirements], ["technical"])
        self.assertEqual(result.stats["technicalCount"], 1)
        self.assertGreaterEqual(result.stats["servicePlanSkippedCount"], 1)

    def test_keeps_explicit_technical_clause_inside_service_plan(self) -> None:
        segments = [
            DocumentSegment(
                id="SEG-SERVICE-TECH",
                text="售后服务平台必须支持远程故障诊断和日志导出。",
                order=0,
            ),
        ]
        document = DocumentIR(
            documentId="DOC-SERVICE-TECH",
            documentVersionId="DOC-SERVICE-TECH-V001",
            segments=segments,
            units=segments,
            text=segments[0].text,
            sections=[
                DocumentSection(
                    id="SEC-SERVICE-TECH",
                    sectionId="SEC-SERVICE-TECH",
                    sectionType="service_plan",
                    heading="十二、售后服务",
                    sectionPath=["十二、售后服务"],
                    segmentIds=["SEG-SERVICE-TECH"],
                )
            ],
        )

        result = extract_requirements(document, classifications=classify_document(document))

        self.assertEqual([item.category for item in result.requirements], ["technical"])
        self.assertEqual(result.requirements[0].primary_route, "company_match")


if __name__ == "__main__":
    unittest.main()
