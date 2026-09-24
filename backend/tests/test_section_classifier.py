from __future__ import annotations

import unittest

from backend.app.tender_matching.document_models import DocumentSection
from backend.app.tender_matching.section_classifier import (
    SectionClassification,
    classify_clause,
    LLM_SECTION_CLASSIFICATION_PROMPT,
    classify_section,
    classify_sections,
    needs_llm_classification_review,
)


class SectionClassifierTests(unittest.TestCase):
    def test_rule_classification_separates_type_from_usage(self) -> None:
        section = DocumentSection(
            id="SEC-TECH",
            sectionId="SEC-TECH",
            sectionType="technical",
            sectionTypeHits=["technical"],
            heading="第三章 技术要求",
            sectionPath=["第三章 技术要求"],
            text="平台应支持统一身份认证、接口集成和性能指标要求。",
            segmentIds=["SEG-1", "SEG-2"],
        )

        result = classify_section(section)

        self.assertEqual(result.primary_type, "technical")
        self.assertEqual(result.classification_source, "rule")
        self.assertIn("matching", result.usage_tags)
        self.assertIn("writing", result.usage_tags)
        self.assertTrue(result.route.matching)
        self.assertTrue(result.route.writing)
        self.assertEqual(section.section_type, "technical")

    def test_low_confidence_can_use_validated_llm_fallback(self) -> None:
        section = DocumentSection(
            id="SEC-OTHER",
            sectionId="SEC-OTHER",
            sectionType="other",
            heading="项目说明",
            sectionPath=["项目说明"],
            text="包含若干无法仅凭标题判断的内容。",
            segmentIds=["SEG-1"],
        )
        calls: list[str] = []

        def fake_llm(request):
            calls.append(request.section_id)
            return {
                "primaryType": "scope",
                "typeHits": ["scope", "basic"],
                "confidence": 0.81,
                "rationale": "正文描述采购范围",
                "needsHumanReview": False,
            }

        result = classify_section(section, llm_classifier=fake_llm)

        self.assertEqual(calls, ["SEC-OTHER"])
        self.assertEqual(result.primary_type, "scope")
        self.assertEqual(result.classification_source, "llm")
        self.assertFalse(result.needs_human_review)
        self.assertIn("matching", result.usage_tags)

    def test_invalid_llm_output_falls_back_to_other_and_review(self) -> None:
        section = DocumentSection(
            id="SEC-UNKNOWN",
            sectionId="SEC-UNKNOWN",
            heading="其他事项",
            sectionPath=["其他事项"],
            text="内容不明确。",
            segmentIds=["SEG-1"],
        )

        result = classify_section(section, llm_classifier=lambda _: {"primaryType": "not-a-type"})

        self.assertEqual(result.primary_type, "other")
        self.assertEqual(result.classification_source, "fallback")
        self.assertTrue(result.needs_human_review)
        self.assertIn("other", result.type_hits)

    def test_parent_type_is_only_a_low_confidence_fallback(self) -> None:
        sections = [
            DocumentSection(
                id="SEC-PARENT",
                sectionId="SEC-PARENT",
                sectionType="technical",
                sectionTypeHits=["technical"],
                heading="第三章 技术要求",
                sectionPath=["第三章 技术要求"],
                segmentIds=["SEG-1", "SEG-2"],
            ),
            DocumentSection(
                id="SEC-CHILD",
                sectionId="SEC-CHILD",
                sectionType="other",
                heading="项目说明",
                parentId="SEC-PARENT",
                sectionPath=["第三章 技术要求", "项目说明"],
                text="无明确分类词。",
                segmentIds=["SEG-2"],
            ),
        ]

        results = classify_sections(sections)

        self.assertEqual(results[0].primary_type, "technical")
        self.assertEqual(results[1].primary_type, "technical")
        self.assertEqual(results[1].classification_source, "content_rule")
        self.assertTrue(results[1].needs_human_review)

    def test_existing_section_type_is_a_low_confidence_fallback(self) -> None:
        section = DocumentSection(
            id="SEC-TECH",
            sectionId="SEC-TECH",
            sectionType="technical",
            heading="第五章 项目说明",
            sectionPath=["第五章 项目说明"],
            text="正文没有稳定的标题或分类关键词。",
            segmentIds=["SEG-1"],
        )

        result = classify_section(section)

        self.assertEqual(result.primary_type, "technical")
        self.assertEqual(result.type_hits, ["technical"])
        self.assertEqual(result.classification_source, "content_rule")
        self.assertTrue(result.needs_human_review)

    def test_concrete_must_support_signal_overrides_service_context(self) -> None:
        section = DocumentSection(
            id="SEC-SERVICE",
            sectionId="SEC-SERVICE",
            sectionType="service_plan",
            heading="十二、售后服务",
            sectionPath=["十二、售后服务"],
            segmentIds=["SEG-1"],
        )

        result = classify_clause(
            "售后服务平台必须支持远程故障诊断和日志导出。",
            section=section,
            section_classification=classify_section(section),
        )

        self.assertEqual(result.primary_type, "technical")
        self.assertIn("service_plan", result.type_hits)
        self.assertIn("technical", result.direct_type_hits)
        self.assertEqual(result.inherited_type, "service_plan")
        self.assertEqual(result.primary_route, "company_match")

    def test_scope_container_keeps_technical_descendant_as_context_only(self) -> None:
        section = DocumentSection(
            id="SEC-SCOPE",
            sectionId="SEC-SCOPE",
            sectionType="scope",
            heading="第五章 招标内容及要求",
            sectionPath=["第五章 招标内容及要求"],
            text="本章包含技术和商务要求。",
            segmentIds=["SEG-1"],
        )

        result = classify_section(section)

        self.assertEqual(result.primary_type, "scope")
        self.assertIn("technical", result.type_hits)
        self.assertEqual(result.primary_route, "display_only")
        self.assertEqual(result.match_domain, "")

    def test_response_table_template_overrides_technical_matching_route(self) -> None:
        section = DocumentSection(
            id="SEC-FORM",
            sectionId="SEC-FORM",
            sectionType="technical",
            sectionTypeHits=["technical", "attachment"],
            heading="\u4e8c\u3001\u6280\u672f\u548c\u670d\u52a1\u8981\u6c42\u54cd\u5e94\u8868",
            sectionPath=[
                "\u7b2c\u4e03\u7ae0 \u7535\u5b50\u6295\u6807\u6587\u4ef6\u683c\u5f0f",
                "\u4e8c\u3001\u6280\u672f\u548c\u670d\u52a1\u8981\u6c42\u54cd\u5e94\u8868",
            ],
            text="\u6295\u6807\u54cd\u5e94 | \u662f\u5426\u504f\u79bb\u53ca\u8bf4\u660e | \u6295\u6807\u4eba\u586b\u5199",
            segmentIds=["SEG-1"],
        )

        result = classify_section(section)

        self.assertEqual(result.primary_type, "document_format")
        self.assertIn("technical", result.type_hits)
        self.assertFalse(result.route.matching)
        self.assertTrue(result.route.writing)

    def test_template_guardrail_still_requests_llm_review_when_available(self) -> None:
        section = DocumentSection(
            id="SEC-FORM",
            sectionId="SEC-FORM",
            sectionType="technical",
            sectionTypeHits=["technical"],
            heading="\u4e8c\u3001\u6280\u672f\u548c\u670d\u52a1\u8981\u6c42\u54cd\u5e94\u8868",
            sectionPath=[
                "\u7b2c\u4e03\u7ae0 \u7535\u5b50\u6295\u6807\u6587\u4ef6\u683c\u5f0f",
                "\u4e8c\u3001\u6280\u672f\u548c\u670d\u52a1\u8981\u6c42\u54cd\u5e94\u8868",
            ],
            text="\u6295\u6807\u4eba\u586b\u5199\u662f\u5426\u504f\u79bb\u53ca\u8bf4\u660e\u3002",
            segmentIds=["SEG-1"],
        )
        calls: list[str] = []

        def fake_llm(request):
            calls.append(request.section_id)
            return {
                "primaryType": "technical",
                "typeHits": ["technical", "document_format"],
                "confidence": 0.77,
                "rationale": "\u8868\u683c\u63d0\u5230\u6280\u672f\u8981\u6c42",
                "needsHumanReview": False,
            }

        result = classify_section(section, llm_classifier=fake_llm)

        self.assertEqual(calls, ["SEC-FORM"])
        self.assertEqual(result.primary_type, "document_format")
        self.assertIn("technical", result.type_hits)
        self.assertFalse(result.route.matching)

    def test_standalone_response_table_is_document_format(self) -> None:
        section = DocumentSection(
            id="SEC-FORM-STANDALONE",
            sectionId="SEC-FORM-STANDALONE",
            sectionType="technical",
            sectionTypeHits=["technical", "attachment"],
            heading="2\u6280\u672f\u548c\u670d\u52a1\u8981\u6c42\u54cd\u5e94\u8868",
            sectionPath=[
                "\u7b2c\u4e09\u7ae0 \u6295\u6807\u4eba\u987b\u77e5",
                "2\u6280\u672f\u548c\u670d\u52a1\u8981\u6c42\u54cd\u5e94\u8868",
            ],
            text="2\u6280\u672f\u548c\u670d\u52a1\u8981\u6c42\u54cd\u5e94\u8868",
            segmentIds=["SEG-1"],
        )

        result = classify_section(section)

        self.assertEqual(result.primary_type, "document_format")
        self.assertIn("technical", result.type_hits)
        self.assertFalse(result.route.matching)

    def test_form_index_and_project_content_sections_are_templates(self) -> None:
        credential_index = DocumentSection(
            id="SEC-CREDENTIAL-INDEX",
            sectionId="SEC-CREDENTIAL-INDEX",
            sectionType="financial",
            heading="（1）资格及资信证明部分",
            sectionPath=["第二章 投标人须知", "（1）资格及资信证明部分"],
            text="1投标函 2投标人的资格及资信证明文件 3投标保证金",
        )
        project_content = DocumentSection(
            id="SEC-PROJECT-CONTENT",
            sectionId="SEC-PROJECT-CONTENT",
            sectionType="technical",
            heading="（3）项目内容：",
            sectionPath=["第六章 政府采购合同", "（3）项目内容："],
            text="采购标的及数量：________ 品牌：________ 规格型号：________",
        )

        credential_result = classify_section(credential_index)
        project_result = classify_section(
            project_content,
            container_type="contract",
        )

        self.assertEqual(credential_result.primary_type, "document_format")
        self.assertFalse(credential_result.route.matching)
        self.assertEqual(project_result.primary_type, "document_format")
        self.assertFalse(project_result.route.matching)

    def test_llm_failure_keeps_guarded_rule_result(self) -> None:
        section = DocumentSection(
            id="SEC-REJECT",
            sectionId="SEC-REJECT",
            sectionType="pricing",
            sectionTypeHits=["pricing"],
            heading="\u6295\u6807\u62a5\u4ef7\u8d85\u8fc7\u62db\u6807\u6587\u4ef6\u4e2d\u89c4\u5b9a\u7684\u6700\u9ad8\u9650\u4ef7",
            sectionPath=["10.6\u6295\u6807\u62a5\u4ef7"],
            text="\u6709\u4e0b\u5217\u60c5\u5f62\u4e4b\u4e00\u7684\uff0c\u6295\u6807\u65e0\u6548\u3002",
            segmentIds=["SEG-1"],
        )

        result = classify_section(section, llm_classifier=lambda _: {"primaryType": "bad"})

        self.assertEqual(result.primary_type, "rejection")
        self.assertIn("pricing", result.type_hits)
        self.assertFalse(result.needs_human_review)
        self.assertIn("LLM", result.rationale)

    def test_invalid_bid_clause_overrides_pricing_parent(self) -> None:
        section = DocumentSection(
            id="SEC-REJECT",
            sectionId="SEC-REJECT",
            sectionType="pricing",
            sectionTypeHits=["pricing"],
            heading="\u6295\u6807\u62a5\u4ef7\u8d85\u8fc7\u62db\u6807\u6587\u4ef6\u4e2d\u89c4\u5b9a\u7684\u6700\u9ad8\u9650\u4ef7",
            sectionPath=["10.6\u6295\u6807\u62a5\u4ef7"],
            text="\u6709\u4e0b\u5217\u60c5\u5f62\u4e4b\u4e00\u7684\uff0c\u6295\u6807\u65e0\u6548\u3002",
            segmentIds=["SEG-1"],
        )

        result = classify_section(section)

        self.assertEqual(result.primary_type, "rejection")
        self.assertFalse(result.route.matching)
        self.assertTrue(result.route.review)

    def test_llm_review_helper_flags_multi_type_and_high_risk_sections(self) -> None:
        template = DocumentSection(
            id="SEC-FORM",
            sectionId="SEC-FORM",
            heading="\u6295\u6807\u51fd",
            sectionPath=["\u7b2c\u4e03\u7ae0 \u7535\u5b50\u6295\u6807\u6587\u4ef6\u683c\u5f0f"],
            text="\u6295\u6807\u4eba\u586b\u5199\u5e76\u52a0\u76d6\u516c\u7ae0\u3002",
            segmentIds=["SEG-1"],
        )
        mixed = DocumentSection(
            id="SEC-MIXED",
            sectionId="SEC-MIXED",
            sectionType="basic",
            sectionTypeHits=["basic"],
            heading="\u7b2c\u4e00\u7ae0 \u6295\u6807\u9080\u8bf7",
            text="\u8d44\u683c\u8981\u6c42\uff1a\u6295\u6807\u4eba\u987b\u5177\u5907\u8425\u4e1a\u6267\u7167\u3002",
            segmentIds=["SEG-2"],
        )

        self.assertTrue(needs_llm_classification_review(template))
        self.assertTrue(needs_llm_classification_review(mixed, classify_section(mixed)))

    def test_mixed_container_keeps_descendant_hits_without_matching_whole_section(self) -> None:
        section = DocumentSection(
            id="SEC-MIXED",
            sectionId="SEC-MIXED",
            sectionType="basic",
            sectionTypeHits=["basic"],
            heading="\u7b2c\u4e00\u7ae0 \u6295\u6807\u9080\u8bf7",
            sectionPath=["\u7b2c\u4e00\u7ae0 \u6295\u6807\u9080\u8bf7"],
            text="\u8d44\u683c\u8981\u6c42\uff1a\u6295\u6807\u4eba\u987b\u5177\u5907\u8425\u4e1a\u6267\u7167\u3002",
            segmentIds=["SEG-1"],
        )

        result = classify_section(section)

        self.assertEqual(result.primary_type, "basic")
        self.assertIn("qualification", result.type_hits)
        self.assertFalse(result.route.matching)
        self.assertEqual(result.primary_route, "project_info")
        self.assertTrue(result.needs_human_review)

    def test_broad_container_context_is_not_copied_to_clause_hits(self) -> None:
        section = DocumentSection(
            id="SEC-BASIC",
            sectionId="SEC-BASIC",
            sectionType="basic",
            heading="第一章 投标邀请",
            sectionPath=["第一章 投标邀请"],
        )

        result = classify_clause(
            "投标人应提供有效的营业执照。",
            section=section,
            section_classification=classify_section(section),
        )

        self.assertEqual(result.primary_type, "qualification")
        self.assertIn("qualification", result.type_hits)
        self.assertNotIn("basic", result.type_hits)

    def test_llm_string_type_hits_are_normalized(self) -> None:
        section = DocumentSection(
            id="SEC-OTHER",
            sectionId="SEC-OTHER",
            sectionType="other",
            heading="\u9879\u76ee\u8bf4\u660e",
            sectionPath=["\u9879\u76ee\u8bf4\u660e"],
            text="\u96be\u4ee5\u4ec5\u51ed\u6807\u9898\u5224\u65ad\u7684\u91c7\u8d2d\u8303\u56f4\u8bf4\u660e\u3002",
            segmentIds=["SEG-1"],
        )

        result = classify_section(
            section,
            llm_classifier=lambda _: {
                "primaryType": "scope",
                "typeHits": "scope, basic",
                "confidence": 0.8,
                "rationale": "\u91c7\u8d2d\u8303\u56f4",
                "needsHumanReview": False,
            },
            confidence_threshold=0.75,
        )

        self.assertEqual(result.classification_source, "llm")
        self.assertEqual(result.type_hits[:2], ["scope", "basic"])

    def test_prompt_is_explicit_about_no_boundary_changes(self) -> None:
        self.assertIn("\u4e0d\u521b\u5efa\u6216\u5220\u9664\u7ae0\u8282", LLM_SECTION_CLASSIFICATION_PROMPT)
        self.assertIn("primaryType", LLM_SECTION_CLASSIFICATION_PROMPT)

    def test_clause_classification_overrides_submission_parent_for_technical_text(self) -> None:
        section = DocumentSection(
            id="SEC-SUBMISSION",
            sectionId="SEC-SUBMISSION",
            sectionType="submission",
            sectionTypeHits=["submission"],
            heading="\u7b2c\u4e09\u7ae0 \u6295\u6807\u4eba\u987b\u77e5",
            sectionPath=["\u7b2c\u4e09\u7ae0 \u6295\u6807\u4eba\u987b\u77e5", "\u4e94\u3001\u62db\u6807\u5185\u5bb9\u53ca\u8981\u6c42"],
            text="",
            segmentIds=["SEG-1"],
        )
        section_classification = classify_section(section)

        result = classify_clause(
            "\u5e73\u53f0\u5e94\u652f\u6301\u4e09\u7ef4\u573a\u666f\u52a0\u8f7d\u3001\u6a21\u578b\u6e32\u67d3\u548c\u6807\u51c6\u63a5\u53e3\u3002",
            section=section,
            section_classification=section_classification,
        )

        self.assertEqual(result.primary_type, "technical")
        self.assertIn("technical", result.type_hits)
        self.assertTrue(result.route.matching)

    def test_clause_rejection_overrides_pricing_and_submission_context(self) -> None:
        section = DocumentSection(
            id="SEC-SUBMISSION",
            sectionId="SEC-SUBMISSION",
            sectionType="submission",
            sectionTypeHits=["submission", "pricing"],
            heading="\u7b2c\u4e09\u7ae0 \u6295\u6807\u4eba\u987b\u77e5",
            sectionPath=["\u7b2c\u4e09\u7ae0 \u6295\u6807\u4eba\u987b\u77e5"],
            text="",
            segmentIds=["SEG-1"],
        )

        result = classify_clause(
            "\u6295\u6807\u62a5\u4ef7\u8d85\u8fc7\u6700\u9ad8\u9650\u4ef7\u7684\uff0c\u6295\u6807\u65e0\u6548\u3002",
            section=section,
            section_classification=classify_section(section),
        )

        self.assertEqual(result.primary_type, "rejection")
        self.assertIn("pricing", result.type_hits)
        self.assertFalse(result.route.matching)

    def test_explicit_guarantee_signal_overrides_joint_venture_context(self) -> None:
        section = DocumentSection(
            id="SEC-SUBMISSION-PRICING",
            sectionId="SEC-SUBMISSION-PRICING",
            sectionType="submission",
            heading="投标保证金",
            sectionPath=["第三章 投标人须知", "投标保证金"],
        )

        result = classify_clause(
            "若投标人为联合体，则联合体中的牵头方应提交投标保证金。",
            section=section,
            section_classification=classify_section(section),
        )

        self.assertEqual(result.primary_type, "pricing")
        self.assertNotEqual(result.primary_route, "company_match")
    def test_classify_sections_records_container_and_descendant_types(self) -> None:
        sections = [
            DocumentSection(
                id="SEC-PARENT",
                sectionId="SEC-PARENT",
                sectionType="submission",
                sectionTypeHits=["submission"],
                heading="\u7b2c\u4e09\u7ae0 \u6295\u6807\u4eba\u987b\u77e5",
                sectionPath=["\u7b2c\u4e09\u7ae0 \u6295\u6807\u4eba\u987b\u77e5"],
                level=1,
                isMajor=True,
                segmentIds=["SEG-1"],
            ),
            DocumentSection(
                id="SEC-CHILD",
                sectionId="SEC-CHILD",
                sectionType="technical",
                sectionTypeHits=["technical"],
                heading="\u4e94\u3001\u6280\u672f\u8981\u6c42",
                parentId="SEC-PARENT",
                sectionPath=["\u7b2c\u4e09\u7ae0 \u6295\u6807\u4eba\u987b\u77e5", "\u4e94\u3001\u6280\u672f\u8981\u6c42"],
                level=2,
                segmentIds=["SEG-2"],
            ),
        ]

        results = classify_sections(sections)

        self.assertEqual(results[0].container_type, "submission")
        self.assertIn("technical", results[0].descendant_type_hits)
        self.assertEqual(results[1].container_type, "submission")

    def test_inherited_section_type_is_not_counted_as_heading_signal(self) -> None:
        sections = [
            DocumentSection(
                id="SEC-PARENT",
                sectionId="SEC-PARENT",
                sectionType="instructions",
                sectionTypeHits=["instructions"],
                heading="\u7b2c\u4e09\u7ae0 \u6295\u6807\u4eba\u987b\u77e5",
                level=1,
                isMajor=True,
            ),
            DocumentSection(
                id="SEC-CHILD",
                sectionId="SEC-CHILD",
                sectionType="instructions",
                sectionTypeHits=[],
                heading="\uff08\uff15\uff09\u62db\u6807\u5185\u5bb9\u53ca\u8981\u6c42",
                parentId="SEC-PARENT",
                level=3,
            ),
        ]

        results = classify_sections(sections)

        self.assertEqual(results[1].heading_type, "scope")
        self.assertEqual(results[1].primary_type, "scope")
        self.assertEqual(results[1].type_hits, ["scope"])

    def test_clause_context_does_not_copy_unrelated_section_hits(self) -> None:
        section = DocumentSection(
            id="SEC-QUALIFICATION",
            sectionId="SEC-QUALIFICATION",
            sectionType="qualification",
            sectionTypeHits=["qualification"],
            heading="\u4e09\u3001\u8d44\u683c\u5ba1\u67e5",
            sectionPath=["\u4e09\u3001\u8d44\u683c\u5ba1\u67e5"],
        )
        classification = SectionClassification(
            sectionId="SEC-QUALIFICATION",
            headingType="qualification",
            primaryType="qualification",
            typeHits=[
                "qualification",
                "financial",
                "submission",
                "schedule",
                "pricing",
            ],
        )

        result = classify_clause(
            "\u6295\u6807\u4eba\u5e94\u63d0\u4f9b\u4e0a\u4e00\u5e74\u5ea6\u8d22\u52a1\u62a5\u544a\u3002",
            section=section,
            section_classification=classification,
        )

        self.assertEqual(result.primary_type, "financial")
        self.assertEqual(result.type_hits, ["financial", "qualification"])
        self.assertNotIn("schedule", result.type_hits)
        self.assertNotIn("pricing", result.type_hits)

    def test_policy_support_text_is_not_technical(self) -> None:
        section = DocumentSection(
            id="SEC-POLICY",
            sectionId="SEC-POLICY",
            sectionType="submission",
            heading="\u516b\u3001\u653f\u5e9c\u91c7\u8d2d\u653f\u7b56",
            sectionPath=["\u516b\u3001\u653f\u5e9c\u91c7\u8d2d\u653f\u7b56"],
        )

        result = classify_clause(
            "\u53c2\u52a0\u653f\u5e9c\u91c7\u8d2d\u6d3b\u52a8\u7684\u5c0f\u578b\u3001\u5fae\u578b\u4f01\u4e1a\u5e94\u6309\u8981\u6c42\u63d0\u4f9b\u4e2d\u5c0f\u4f01\u4e1a\u58f0\u660e\u51fd\u3002",
            section=section,
            section_classification=classify_section(section),
        )

        self.assertNotIn("technical", result.type_hits)
        self.assertNotEqual(result.primary_type, "technical")

    def test_bare_context_words_do_not_create_technical_or_template_hits(self) -> None:
        section = DocumentSection(
            id="SEC-OTHER",
            sectionId="SEC-OTHER",
            sectionType="other",
            heading="项目说明",
            sectionPath=["项目说明"],
        )

        context_result = classify_clause(
            "本项目支持中小企业参与，系统名称和平台名称以采购文件为准。",
            section=section,
            section_classification=classify_section(section),
        )
        format_result = classify_clause(
            "本文件格式说明见附件。",
            section=section,
            section_classification=classify_section(section),
        )

        self.assertNotIn("technical", context_result.type_hits)
        self.assertNotEqual(context_result.primary_type, "technical")
        self.assertNotIn("document_format", format_result.type_hits)
        self.assertNotEqual(format_result.primary_type, "document_format")

    def test_second_batch_bare_words_do_not_create_workflow_types(self) -> None:
        section = DocumentSection(
            id="SEC-OTHER-SECOND-BATCH",
            sectionId="SEC-OTHER-SECOND-BATCH",
            sectionType="other",
            heading="项目说明",
            sectionPath=["项目说明"],
        )

        for text in ("提交", "费用", "人员", "验收", "履约", "服务", "实施", "工程师", "密封", "封装"):
            with self.subTest(text=text):
                result = classify_clause(text, section=section)
                self.assertEqual(result.primary_type, "other")

        self.assertEqual(
            classify_clause("提交投标文件", section=section).primary_type,
            "submission",
        )
        self.assertEqual(
            classify_clause("投标文件密封要求", section=section).primary_type,
            "document_format",
        )
        self.assertEqual(
            classify_clause("项目验收标准", section=section).primary_type,
            "commercial",
        )


if __name__ == "__main__":
    unittest.main()
