from __future__ import annotations

import unittest

from backend.app.tender_matching.routing_contracts import (
    has_explicit_technical_signal,
    has_fill_signal,
    has_packaging_signal,
    has_personnel_signal,
    has_submission_signal,
    has_template_signal,
    is_confirmation_only_text,
    resolve_routing,
    unified_route_fields,
)


class RoutingContractTests(unittest.TestCase):
    def test_bare_words_do_not_trigger_template_fill_or_technical_signals(self) -> None:
        self.assertFalse(has_template_signal("本文件格式说明见采购文件。"))
        self.assertTrue(has_template_signal("电子投标文件格式及签字盖章要求。"))
        self.assertFalse(has_fill_signal("填报上一年度营业收入和资产总额。"))
        self.assertTrue(has_fill_signal("请填写报价明细表中的响应情况。"))
        self.assertFalse(has_explicit_technical_signal("平台名称、系统名称和数据名称。"))
        self.assertTrue(has_explicit_technical_signal("平台应支持三维场景加载和数据接入。"))

    def test_second_batch_keeps_only_specific_workflow_signals(self) -> None:
        for text in ("提交", "密封", "封装", "费用", "人员", "工程师", "验收", "履约", "服务", "实施"):
            with self.subTest(text=text):
                self.assertFalse(has_submission_signal(text))
                self.assertFalse(has_packaging_signal(text))
                self.assertFalse(has_personnel_signal(text))

        self.assertTrue(has_submission_signal("提交投标文件截止时间"))
        self.assertTrue(has_packaging_signal("投标文件密封要求"))
        self.assertTrue(has_personnel_signal("项目人员配置要求"))
        self.assertTrue(has_personnel_signal("高级工程师职称"))
        self.assertFalse(has_personnel_signal("工作人员"))
        self.assertFalse(has_personnel_signal("技术人员"))
        self.assertTrue(has_personnel_signal("技术人员配备数量"))

    def test_technical_requirement_is_company_match_with_technical_domain(self) -> None:
        result = resolve_routing(
            "technical",
            type_hits=["technical"],
            text="平台必须支持 GLTF 模型导入。",
            mandatory=True,
        )

        self.assertEqual(result["primaryRoute"], "company_match")
        self.assertEqual(result["matchDomain"], "technical")
        self.assertEqual(result["granularity"], "clause")
        self.assertEqual(result["decisionImpact"], "important")

    def test_template_and_technical_hit_keep_both_types_but_template_owns_section_route(self) -> None:
        result = unified_route_fields(
            "document_format",
            type_hits=["document_format", "technical"],
            text="技术和服务要求响应表：投标人填写响应情况并签字盖章。",
            section_level=True,
        )

        self.assertEqual(result["primaryRoute"], "compliance_confirm")
        self.assertIn("technical", result["typeHits"])
        self.assertEqual(result["granularity"], "field")
        self.assertEqual(result["reviewMode"], "manual")

    def test_service_plan_stays_writing_context_without_explicit_technical_signal(self) -> None:
        result = resolve_routing(
            "service_plan",
            type_hits=["service_plan"],
            text="投标人应提供项目实施、培训和运维方案。",
            section_level=True,
        )

        self.assertEqual(result["primaryRoute"], "writing_context")
        self.assertEqual(result["granularity"], "section")
        self.assertEqual(result["decisionImpact"], "context")

    def test_scope_container_does_not_route_whole_chapter_to_company_match(self) -> None:
        result = resolve_routing(
            "scope",
            type_hits=["scope", "technical"],
            text="第五章招标内容及要求，后文包含技术参数。",
            section_level=True,
        )

        self.assertEqual(result["primaryRoute"], "display_only")
        self.assertEqual(result["matchDomain"], "")
        self.assertEqual(result["granularity"], "section")
        self.assertIn("technical", result["typeHits"])

    def test_project_and_instruction_containers_do_not_route_by_descendant_hits(self) -> None:
        project = resolve_routing(
            "basic",
            type_hits=["basic", "qualification", "financial", "pricing"],
            text="投标邀请，后文包含资格、财务和报价要求。",
            section_level=True,
        )
        instructions = resolve_routing(
            "submission",
            type_hits=["submission", "qualification", "rejection"],
            text="投标人须知，后文包含资格和投标无效情形。",
            section_level=True,
        )

        self.assertEqual(project["primaryRoute"], "project_info")
        self.assertEqual(project["matchDomain"], "")
        self.assertEqual(instructions["primaryRoute"], "display_only")
        self.assertEqual(instructions["matchDomain"], "")

    def test_evaluation_and_opening_containers_do_not_route_to_enterprise_match(self) -> None:
        result = resolve_routing(
            "qualification",
            type_hits=["qualification", "personnel"],
            text="一、资格审查。资格审查小组负责资格审查工作的组织。",
            section_level=True,
        )

        self.assertEqual(result["primaryRoute"], "display_only")
        self.assertEqual(result["matchDomain"], "")

    def test_contract_container_does_not_route_technical_subsection_to_matching(self) -> None:
        result = resolve_routing(
            "technical",
            type_hits=["technical", "commercial"],
            text="合同组成文件及技术资料由中标人提供。",
            section_level=True,
            container_type="contract",
        )

        self.assertEqual(result["primaryRoute"], "fill")
        self.assertEqual(result["matchDomain"], "")

    def test_procedural_qualification_text_routes_to_confirmation(self) -> None:
        confirmation_text = "投标人提供资格承诺函，可不提供营业执照等资格条件证明材料。"
        confirmation = resolve_routing(
            "qualification",
            text=confirmation_text,
        )
        evidence = resolve_routing(
            "qualification",
            text="投标人应提供有效的营业执照和资质证书。",
        )

        self.assertTrue(is_confirmation_only_text(confirmation_text, "qualification"))
        self.assertEqual(confirmation["primaryRoute"], "compliance_confirm")
        self.assertEqual(confirmation["matchDomain"], "")
        self.assertEqual(evidence["primaryRoute"], "company_match")
        self.assertEqual(evidence["matchDomain"], "qualification")

    def test_procedural_documents_and_policy_do_not_route_to_evidence(self) -> None:
        for text in (
            "质疑人为法人或其他组织的，提供营业执照等证明文件。",
            "投标人提供的相应证明材料均应加盖其单位公章。",
            "政府采购节能产品依据认证机构出具的认证证书实施优先采购。",
            "以联合体形式落实中小企业预留份额时，还需提供分包意向协议。",
        ):
            with self.subTest(text=text):
                result = resolve_routing("qualification", text=text)
                self.assertEqual(result["primaryRoute"], "compliance_confirm")
                self.assertEqual(result["matchDomain"], "")

    def test_form_rows_self_declarations_and_generic_stamps_are_confirmation_only(self) -> None:
        for category, text in (
            ("qualification", "（ ）投标人提供财务报告的"),
            ("financial", "（）投标人提供资信证明的"),
            ("qualification", "我方具备履行合同所必需的设备和专业技术能力。"),
            ("qualification", "本公司（联合体）郑重声明所提交材料真实有效。"),
            ("qualification", "其他证明材料或资料加盖投标人的单位公章。"),
            ("qualification", "联合体各方不得再单独参加同一合同项下的投标。"),
            ("qualification", "联合体中有同类资质的供应商按照联合体分工承担相同工作。"),
        ):
            with self.subTest(category=category, text=text):
                result = resolve_routing(category, text=text)
                self.assertEqual(result["primaryRoute"], "compliance_confirm")
                self.assertEqual(result["matchDomain"], "")

    def test_technical_attachment_cross_reference_is_context_only(self) -> None:
        for text in (
            "采购标的的技术要求、商务要求具体见附件。",
            "附件：具体标的及其技术要求和商务要求。",
        ):
            with self.subTest(text=text):
                result = resolve_routing(
                    "technical",
                    type_hits=["technical", "commercial"],
                    text=text,
                )
                self.assertEqual(result["primaryRoute"], "display_only")
                self.assertEqual(result["matchDomain"], "")

    def test_template_route_cannot_inherit_technical_match_domain(self) -> None:
        result = resolve_routing(
            "technical",
            type_hits=["technical", "document_format"],
            text="技术和服务要求响应表：投标人填写响应情况并签字盖章。",
            section_level=True,
        )

        self.assertEqual(result["primaryRoute"], "compliance_confirm")
        self.assertEqual(result["matchDomain"], "")

    def test_pure_template_does_not_force_manual_review(self) -> None:
        result = resolve_routing(
            "document_format",
            type_hits=["document_format"],
            text="投标函模板，请投标人填写并盖章。",
            section_level=True,
        )

        self.assertEqual(result["primaryRoute"], "fill")
        self.assertEqual(result["reviewMode"], "none")

    def test_service_plan_container_keeps_technical_hits_as_clause_context(self) -> None:
        result = resolve_routing(
            "service_plan",
            type_hits=["service_plan", "technical"],
            text="售后服务章节包含平台必须支持远程故障诊断的条款。",
            section_level=True,
        )

        self.assertEqual(result["primaryRoute"], "writing_context")
        self.assertEqual(result["matchDomain"], "")
        self.assertEqual(result["granularity"], "section")

    def test_compliance_compatibility_category_routes_to_confirmation(self) -> None:
        result = unified_route_fields(
            "compliance",
            text="投标文件须实质性响应技术参数，并提供承诺函。",
            mandatory=True,
        )

        self.assertEqual(result["primaryRoute"], "compliance_confirm")
        self.assertEqual(result["legacyRequirementClass"], "compliance")

    def test_rejection_is_manual_hard_gate(self) -> None:
        result = resolve_routing(
            "rejection",
            text="超过最高限价的，投标无效。",
            is_rejection=True,
        )

        self.assertEqual(result["primaryRoute"], "rejection_check")
        self.assertEqual(result["decisionImpact"], "hard_gate")
        self.assertEqual(result["reviewMode"], "manual")

    def test_container_section_rejection_hit_does_not_hijack_primary_route(self) -> None:
        result = resolve_routing(
            "submission",
            type_hits=["submission", "rejection"],
            text="投标人须知，后文包含投标无效情形。",
            section_level=True,
        )

        self.assertEqual(result["primaryRoute"], "display_only")
        self.assertIn("rejection", result["typeHits"])


if __name__ == "__main__":
    unittest.main()
