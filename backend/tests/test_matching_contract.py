from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.tender_matching.contracts import (
    build_comparison,
    coerce_requirement_category,
    normalize_evidence,
    normalize_requirement,
    requirement_class_for_route,
    summarize_comparisons,
)
from backend.app.tender_matching.document_matching import (
    summarize_fine_review,
    update_fine_review_status,
)
from backend.app.tender_matching.deterministic import (
    deterministic_match,
    subject_type_matches_requirement,
)
from backend.app.tender_matching.eligibility import (
    _status_from_evidence as announcement_status_from_evidence,
    _category_for as announcement_category_for,
    _normalize_model_extraction,
    build_announcement_model_input,
    evaluate_announcement_eligibility,
    extract_announcement_requirements,
    extract_requirements_by_rule,
    _procurement_need_category,
    split_announcement_sections,
)


class MatchingContractTests(unittest.TestCase):
    def test_announcement_category_does_not_use_bare_technical_nouns(self) -> None:
        for text in ("平台", "系统", "数据", "技术"):
            with self.subTest(text=text):
                self.assertEqual(announcement_category_for(text), "other")

        self.assertEqual(
            announcement_category_for("数字孪生平台建设及数据接入"),
            "technical",
        )
        self.assertEqual(
            announcement_category_for("供应商应完成系统部署和数据接入服务"),
            "technical",
        )
        self.assertEqual(
            _procurement_need_category("供应商应提供实施培训和运维服务"),
            "service",
        )
        for text in ("服务", "实施", "验收", "履约"):
            with self.subTest(text=text):
                self.assertEqual(_procurement_need_category(text), "other")
        self.assertEqual(
            _procurement_need_category("项目实施方案和售后服务内容"),
            "service",
        )
        self.assertEqual(
            _procurement_need_category("项目验收标准及交付成果"),
            "deliverable",
        )
        self.assertEqual(
            announcement_category_for("供应商应具备专业技术能力"),
            "qualification",
        )

    def test_commitment_and_policy_text_do_not_route_to_company_evidence(self) -> None:
        self.assertEqual(
            coerce_requirement_category(
                "financial",
                "提供政府采购供应商资格承诺函的，在投标文件中可不提供财务状况报告、纳税证明等资格条件证明材料。",
            ),
            "document",
        )
        self.assertEqual(
            coerce_requirement_category("qualification", "（1）资格及资信证明部分"),
            "document",
        )
        self.assertEqual(
            coerce_requirement_category("qualification", "质疑人为法人或其他组织的，提供营业执照复印件。"),
            "document",
        )

    def test_multi_branch_subject_type_clause_keeps_qualification_route(self) -> None:
        clause = (
            "营业执照等证明文件：①投标人为企业的，提供有效的营业执照复印件；"
            "投标人为事业单位的，提供有效的事业单位法人证书复印件；"
            "投标人为社会团体的，提供有效的社会团体法人登记证书复印件；"
            "投标人为合伙企业、个体工商户的，提供有效的营业执照复印件；"
            "投标人为非企业专业服务机构的，提供有效的执业许可证等证明材料复印件；"
            "投标人为自然人的，提供有效的自然人身份证件复印件；"
            "其他投标人应按照有关法律、法规和规章规定，提供有效的相应具体证照复印件。"
        )
        self.assertEqual(
            coerce_requirement_category("qualification", clause),
            "qualification",
        )
        # 单一主体分支（事业单位）仍按"非企业主体"归为 document，不查企业证照
        self.assertEqual(
            coerce_requirement_category("qualification", "投标人为事业单位的，提供有效的事业单位法人证书复印件。"),
            "document",
        )

    def test_conditional_or_procedural_materials_do_not_route_to_company_evidence(self) -> None:
        cases = [
            (
                "qualification",
                "\u8054\u5408\u4f53\u5404\u65b9\u5e94\u63d0\u4ea4\u8054\u5408\u4f53\u534f\u8bae\uff0c\u8054\u5408\u4f53\u534f\u8bae\u5e94\u7b26\u5408\u62db\u6807\u6587\u4ef6\u89c4\u5b9a\u3002",
                "document",
            ),
            (
                "qualification",
                "\u9879\u76ee\u5982\u6d89\u53ca\u8d44\u8d28\u8981\u6c42\uff0c\u8be5\u90e8\u5206\u5de5\u4f5c\u5185\u5bb9\u5e94\u7531\u8054\u5408\u4f53\u4e2d\u7b26\u5408\u8be5\u8d44\u8d28\u8981\u6c42\u7684\u4f9b\u5e94\u5546\u627f\u62c5\u3002",
                "document",
            ),
            (
                "personnel",
                "\u539f\u4ef6\u5c5e\u4e8e\u975e\u4e2d\u6587\u63cf\u8ff0\u7684\uff0c\u5e94\u63d0\u4f9b\u5177\u6709\u7ffb\u8bd1\u8d44\u8d28\u7684\u673a\u6784\u7ffb\u8bd1\u7684\u4e2d\u6587\u8bd1\u672c\u3002",
                "document",
            ),
            (
                "qualification",
                "b3\u4f9d\u6cd5\u5e94\u7ec8\u6b62\u91c7\u8d2d\u7a0b\u5e8f\u7684\u8bc1\u660e\u6750\u6599\uff1b",
                "document",
            ),
            (
                "personnel",
                "\u4e0d\u540c\u6295\u6807\u4eba\u7684\u7535\u5b50\u6295\u6807\u6587\u4ef6\u8f7d\u660e\u7684\u9879\u76ee\u7ba1\u7406\u6210\u5458\u6216\u8054\u7cfb\u4eba\u5458\u4e3a\u540c\u4e00\u4eba\uff1b",
                "rejection",
            ),
            (
                "financial",
                "\u8d44\u683c\u53ca\u8d44\u4fe1\u8bc1\u660e\u90e8\u5206\u4e2d\u4e0d\u5f97\u51fa\u73b0\u62a5\u4ef7\u90e8\u5206\u7684\u5168\u90e8\u6216\u8005\u90e8\u5206\u7684\u6295\u6807\u62a5\u4ef7\u4fe1\u606f\uff0c\u5426\u5219\u8d44\u683c\u5ba1\u67e5\u4e0d\u5408\u683c\u3002",
                "rejection",
            ),
            (
                "qualification",
                "\u5355\u4f4d\u6388\u6743\u4e66\uff1a\u6295\u6807\u4eba\u4e3a\u81ea\u7136\u4eba\u7684\uff0c\u53ef\u4e0d\u586b\u5199\u672c\u6388\u6743\u4e66\u3002",
                "document",
            ),
            (
                "qualification",
                "\u8425\u4e1a\u6267\u7167\u7b49\u8bc1\u660e\u6587\u4ef6\uff1a\u6295\u6807\u4eba\u4e3a\u4e8b\u4e1a\u5355\u4f4d\u7684\uff0c\u63d0\u4f9b\u6709\u6548\u7684\u4e8b\u4e1a\u5355\u4f4d\u6cd5\u4eba\u8bc1\u4e66\u590d\u5370\u4ef6\u3002",
                "document",
            ),
        ]
        for raw_category, text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(coerce_requirement_category(raw_category, text), expected)

    def test_route_contract_uses_only_existing_ontology_classes(self) -> None:
        self.assertEqual(
            requirement_class_for_route(
                "financial",
                "提供由会计师事务所出具的2025年度财务审计报告。",
                {"ontologyClasses": ["financial_evidence"]},
            ),
            "evidence",
        )
        self.assertEqual(
            requirement_class_for_route(
                "credit",
                "未被信用中国列入失信被执行人名单。",
                {"ontologyClasses": ["credit_record"]},
            ),
            "compliance",
        )

    def test_subject_type_branches_do_not_match_enterprise_license(self) -> None:
        company = {
            "organization": {
                "organizationName": "北京示例科技有限公司",
                "organizationType": "企业",
            },
            "certificates": [
                {
                    "credentialId": "cert-license",
                    "credentialName": "公司 营业执照",
                    "verificationStatus": "有效",
                }
            ],
        }

        self.assertTrue(
            subject_type_matches_requirement("投标人为企业的，提供有效的营业执照复印件", company["organization"])
        )
        self.assertFalse(
            subject_type_matches_requirement("投标人为事业单位的，提供有效的事业单位法人证书复印件", company["organization"])
        )
        self.assertIsNone(
            deterministic_match(
                "投标人为事业单位的，提供有效的事业单位法人证书复印件",
                "qualification",
                company,
            )
        )

    def test_announcement_section_splitter_classifies_common_sections(self) -> None:
        text = """一、项目基本情况
1、采购项目编号：ABC-2026-001
2、采购项目名称：城市运行数字孪生平台项目
二、申请人的资格要求
1. 投标人须具有有效的营业执照。
2. 拟派项目负责人须具有信息系统项目管理师证书。
三、获取招标文件
获取时间：2026年9月11日至9月18日
四、提交投标文件截止时间、开标时间和地点
投标截止时间：2026年10月12日 09:30
七、对本次招标提出询问，请按以下方式联系
1.采购人信息
名称：某采购人
三、中标情况
供应商名称：某公司
"""
        sections = split_announcement_sections(text)
        section_types = [section["type"] for section in sections]

        for expected_type in (
            "basic",
            "qualification",
            "procurement",
            "bid_opening",
            "contact",
            "result",
        ):
            self.assertIn(expected_type, section_types)

        qualification_section = next(
            section for section in sections if section["type"] == "qualification"
        )
        self.assertEqual(qualification_section["heading"], "二、申请人的资格要求")
        self.assertEqual(qualification_section["startLine"], 4)
        self.assertEqual(qualification_section["endLine"], 6)
        self.assertEqual(
            [item["text"] for item in qualification_section["lines"]],
            [
                "1. 投标人须具有有效的营业执照。",
                "2. 拟派项目负责人须具有信息系统项目管理师证书。",
            ],
        )

    def test_basic_project_information_does_not_enter_qualification_rows(self) -> None:
        text = """一、项目基本情况
1、采购项目编号：平采招标-2026-103
2、采购项目名称：河南质量工程职业学院智慧工厂数字孪生虚拟仿真实训基地建设项目
3、采购方式：公开招标
4、招标公告发布日期：2026年07月16日
二、采购项目用途、数量、简要技术要求、合同履行日期：
1、采购数量、规格、目标：详见招标文件采购需求；
2、招标范围：设备采购、安装、调试、验收及培训；
3、交货期：合同签订后30个工作日；
4、交货地点：采购人指定地点；
5、本项目是否接受联合体投标：否；
三、中标情况
供应商名称：某公司
"""
        extraction = extract_requirements_by_rule(text)

        self.assertEqual(extraction["project"]["projectCode"], "平采招标-2026-103")
        self.assertEqual(
            extraction["project"]["projectName"],
            "河南质量工程职业学院智慧工厂数字孪生虚拟仿真实训基地建设项目",
        )
        self.assertEqual(extraction["project"]["projectDuration"], "合同签订后30个工作日；")
        self.assertEqual(extraction["project"]["tenderNoticeDate"], "2026年07月16日")
        self.assertNotIn("noticeDate", extraction["project"])
        self.assertNotIn("warrantyRequirement", extraction["project"])
        self.assertEqual(extraction["project"]["jointVentureRequirement"], "否；")
        self.assertFalse(
            any(
                "项目基本情况" in row["text"] or "采购项目编号" in row["text"]
                for row in extraction["requirements"]
            )
        )
        self.assertEqual(extraction["requirements"], [])
        self.assertIn("中标公告", extraction["warnings"][0])

        result = evaluate_announcement_eligibility(text, {}, {}, {"enabled": False}, "FILE-RESULT")
        self.assertEqual(
            result["decision"],
            "无法完成资格判断，请补充原始招标公告或招标文件",
        )
        self.assertFalse(result["eligibility"]["assessmentAvailable"])
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["risks"][0]["level"], "高")

    def test_result_notice_blocks_requirement_extraction_and_model_call(self) -> None:
        text = """某单位智慧平台采购结果公告
一、项目编号：JG-2026-008
二、项目名称：某单位智慧平台采购项目
三、中标情况
供应商名称：某科技有限公司
供应商地址：北京市海淀区
四、主要标的信息
供应商应具有平台部署和运维能力。
五、评审专家名单
张三、李四
"""
        extraction = extract_announcement_requirements(
            text,
            {
                "baseUrl": "http://127.0.0.1:1/v1",
                "apiKey": "unused",
                "model": "unused",
            },
        )

        self.assertEqual(extraction["project"]["noticeType"], "中标公告")
        self.assertEqual(extraction["requirements"], [])
        self.assertEqual(extraction["extractionMethod"], "rule")
        self.assertEqual(extraction["modelUsed"], "")
        self.assertIn("中标公告", extraction["extractionWarning"])

    def test_model_extraction_receives_section_slices_instead_of_full_notice(self) -> None:
        text = """一、项目基本情况
项目名称：城市运行平台项目
项目编号：CS-2026-088
二、采购需求
供应商应完成平台部署、数据接入、可视化建模、驾驶舱配置和培训服务。
三、申请人的资格要求
1. 投标人须具有有效的营业执照。
四、提交投标文件截止时间、开标时间和地点
开标时间：2026年10月12日 09:30
开标地点：北京市海淀区第一开标室
"""
        with patch("backend.app.tender_matching.eligibility.call_openai_compatible") as mocked_call:
            mocked_call.return_value = {
                "success": True,
                "content": '{"project":{},"requirements":[],"warnings":[]}',
                "model": "mock-model",
            }

            extraction = extract_announcement_requirements(
                text,
                {
                    "baseUrl": "http://127.0.0.1:1/v1",
                    "apiKey": "unused",
                    "model": "mock-model",
                },
            )

        sent_messages = mocked_call.call_args.args[1]
        user_content = sent_messages[1]["content"]
        self.assertIn("公告相关章节切片", user_content)
        self.assertIn("项目编号：CS-2026-088", user_content)
        self.assertIn("投标人须具有有效的营业执照", user_content)
        self.assertIn("开标时间：2026年10月12日 09:30", user_content)
        self.assertIn("平台部署、数据接入、可视化建模", user_content)
        self.assertEqual(extraction["extractionMethod"], "llm+rule")

    def test_model_input_preserves_qualification_and_procurement_sections(self) -> None:
        text = """一、项目基本情况
项目名称：园区数字化平台建设项目
项目说明：""" + ("本项目包含大量背景说明和建设目标。 " * 80) + """
二、采购需求
采购范围：建设园区三维可视化底座；支持数据接入和指标治理。
三、资格审查
资格审查表
序号 | 审查因素 | 审查标准
1 | 主体资格 | 投标人须具有有效营业执照
"""
        model_input = build_announcement_model_input(text, limit=520)

        self.assertIn("采购范围：建设园区三维可视化底座", model_input)
        self.assertIn("投标人须具有有效营业执照", model_input)

    def test_mixed_announcement_separates_procurement_rows_from_qualification_rows(self) -> None:
        text = """一、项目基本情况
本项目建设园区三维可视化底座，提供数据接入和指标治理服务。
二、采购标的一览表
序号 | 货物名称 | 数量 | 技术参数
1 | 三维可视化平台 | 1套 | 支持数据接入
三、资格审查
资格审查表
序号 | 审查因素 | 审查标准
1 | 主体资格 | 投标人须具有有效营业执照
"""
        extraction = extract_requirements_by_rule(text)
        need_texts = [item["text"] for item in extraction["procurementNeeds"]]
        requirement_texts = [item["text"] for item in extraction["requirements"]]

        self.assertTrue(any("园区三维可视化底座" in item for item in need_texts))
        self.assertTrue(any("三维可视化平台" in item and "支持数据接入" in item for item in need_texts))
        self.assertTrue(any("有效营业执照" in item for item in requirement_texts))
        self.assertFalse(any("审查因素" in item or "货物名称" in item for item in need_texts))
        self.assertFalse(any("三维可视化平台" in item for item in requirement_texts))

    def test_model_normalization_drops_cross_classified_items(self) -> None:
        normalized = _normalize_model_extraction(
            {
                "project": {},
                "procurementNeeds": [
                    {"text": "投标人须具有有效营业执照", "category": "qualification"},
                    {"text": "建设园区三维可视化平台并支持数据接入", "category": "technical"},
                ],
                "requirements": [
                    {"text": "建设园区三维可视化平台并支持数据接入", "category": "technical"},
                    {"text": "投标人须具有有效营业执照", "category": "qualification"},
                ],
                "warnings": [],
            }
        )

        self.assertIsNotNone(normalized)
        self.assertEqual(
            [item["text"] for item in normalized["procurementNeeds"]],
            ["建设园区三维可视化平台并支持数据接入"],
        )
        self.assertEqual(
            [item["text"] for item in normalized["requirements"]],
            ["投标人须具有有效营业执照"],
        )

    def test_rule_extraction_returns_procurement_needs_separately(self) -> None:
        text = """某市平台建设公开招标公告
一、项目基本情况
项目名称：
城市运行管理平台建设项目
项目编号：NEED-2026-001
二、采购内容及技术要求
采购范围：完成数据接入、指标治理和三维可视化驾驶舱建设；
供应商应提供系统部署、培训和一年运维服务。
三、申请人的资格要求
1. 投标人须具有有效的营业执照。
四、获取招标文件
时间：
2026年9月11日至2026年9月18日
五、响应文件提交
截止时间：2026年10月12日09点30分
"""
        extraction = extract_requirements_by_rule(text)

        self.assertEqual(extraction["project"]["projectName"], "城市运行管理平台建设项目")
        self.assertEqual(
            extraction["project"]["registrationPeriod"],
            "2026年9月11日至2026年9月18日",
        )
        need_texts = [item["text"] for item in extraction["procurementNeeds"]]
        self.assertTrue(any("数据接入" in item for item in need_texts))
        self.assertTrue(any("系统部署" in item for item in need_texts))
        self.assertEqual(len(extraction["requirements"]), 1)
        self.assertNotIn("数据接入", extraction["requirements"][0]["text"])

    def test_flattened_heading_values_are_extracted_into_matching_fields(self) -> None:
        text = (
            "\u4e00\u3001\u9879\u76ee\u57fa\u672c\u60c5\u51b5\n"
            "1\u3001\u9879\u76ee\u7f16\u53f7\uff1aCS-2026-001\n"
            "2\u3001\u9879\u76ee\u540d\u79f0\uff1a\u57ce\u5e02\u8fd0\u884c\u5e73\u53f0\u9879\u76ee\n"
            "\u4e8c\u3001\u91c7\u8d2d\u9700\u6c42\uff1a\u5e73\u53f0\u5efa\u8bbe\u3001\u6570\u636e\u63a5\u5165\u53ca\u57f9\u8bad\n"
            "\u4e09\u3001\u7533\u8bf7\u4eba\u7684\u8d44\u683c\u8981\u6c42\n"
            "1\u3001\u6295\u6807\u4eba\u987b\u5177\u6709\u6709\u6548\u7684\u8425\u4e1a\u6267\u7167\u3002\n"
            "\u56db\u3001\u83b7\u53d6\u62db\u6807\u6587\u4ef6\u65f6\u95f4\uff1a2026\u5e749\u67081\u65e5\u81f32026\u5e749\u67088\u65e5\n"
            "\u4e94\u3001\u6295\u6807\u622a\u6b62\u65f6\u95f4\uff1a2026\u5e749\u670820\u65e59\u65f630\u5206"
        )

        sections = split_announcement_sections(text)
        self.assertEqual(
            [section["type"] for section in sections],
            ["basic", "needs", "qualification", "procurement", "bid_opening"],
        )
        self.assertEqual(
            sections[1]["lines"][0]["text"],
            "\u5e73\u53f0\u5efa\u8bbe\u3001\u6570\u636e\u63a5\u5165\u53ca\u57f9\u8bad",
        )

        extraction = extract_requirements_by_rule(text)
        project = extraction["project"]
        self.assertEqual(project["projectCode"], "CS-2026-001")
        self.assertEqual(project["projectName"], "\u57ce\u5e02\u8fd0\u884c\u5e73\u53f0\u9879\u76ee")
        self.assertEqual(
            project["registrationPeriod"],
            "2026\u5e749\u67081\u65e5\u81f32026\u5e749\u67088\u65e5",
        )
        self.assertEqual(project["bidDeadline"], "2026\u5e749\u670820\u65e59\u65f630\u5206")
        self.assertTrue(
            any("\u5e73\u53f0\u5efa\u8bbe" in item["text"] for item in extraction["procurementNeeds"])
        )
        self.assertTrue(
            any("\u6709\u6548\u7684\u8425\u4e1a\u6267\u7167" in item["text"] for item in extraction["requirements"])
        )

    def test_announcement_evaluation_returns_procurement_associations_without_scoring_them(self) -> None:
        text = """一、项目基本情况
项目名称：数字孪生平台项目
采购需求：建设数字孪生平台并提供数据治理服务。
二、申请人的资格要求
1. 投标人须具有有效的营业执照。
"""
        knowledge = {
            "products": [{"id": "PROD-001", "name": "数字孪生平台"}],
            "capabilities": [{"id": "CAP-001", "name": "数据治理"}],
            "qualifications": [],
        }
        result = evaluate_announcement_eligibility(
            text,
            knowledge,
            {},
            {"enabled": False},
            "FILE-NEED",
        )

        self.assertEqual(len(result["rows"]), 1)
        self.assertTrue(result["procurementNeeds"])
        self.assertEqual(result["procurementNeeds"][0]["status"], "已关联")
        self.assertIn("PROD-001", result["procurementNeeds"][0]["matchedObjectIds"])

    def test_successful_model_extraction_is_cached_for_same_notice_and_model(self) -> None:
        text = """一、项目基本情况
项目名称：城市体征平台项目
项目编号：CACHE-2026-001
二、申请人的资格要求
1. 投标人须具有有效的营业执照。
"""
        ai = {
            "baseUrl": "http://127.0.0.1:1/v1",
            "apiKey": "unused",
            "model": "cache-model",
        }
        with patch("backend.app.tender_matching.eligibility.call_openai_compatible") as mocked_call:
            mocked_call.return_value = {
                "success": True,
                "content": '{"project":{"projectName":"城市体征平台项目"},"requirements":[],"warnings":[]}',
                "model": "cache-model",
            }

            first = extract_announcement_requirements(text, ai)
            first["project"]["projectName"] = "调用方误改缓存检查"
            second = extract_announcement_requirements(text, ai)

        self.assertEqual(mocked_call.call_count, 1)
        self.assertFalse(first["extractionCacheHit"])
        self.assertTrue(second["extractionCacheHit"])
        self.assertEqual(second["project"]["projectName"], "城市体征平台项目")

    def test_rule_extraction_separates_all_basic_fields_and_qualification_types(self) -> None:
        text = """项目名称：智慧园区三维可视化平台建设项目
项目编号：YZ-2026-009
招标人：某产业园管理委员会
投标截止时间：2026年10月12日 09:30
开标时间：2026年10月12日 09:30
投标地点：北京市海淀区开标室
项目工期：合同签订后90日
质保期：验收合格后3年
投标文件份数：正本1份，副本4份
封装要求：正副本分开密封
签字盖章要求：法定代表人签字并加盖公章
三、申请人的资格要求
1. 投标人须具有电子与智能化工程专业承包资质。
2. 拟派项目负责人须具有信息系统项目管理师证书。
3. 近三年具有至少2项类似数字孪生项目业绩。
4. 提供2025年度经审计的财务报告。
5. 未被信用中国列入失信被执行人名单。
四、获取招标文件
获取时间：2026年9月11日至9月18日
"""
        extraction = extract_requirements_by_rule(text)
        project = extraction["project"]

        self.assertEqual(project["openingTime"], "2026年10月12日 09:30")
        self.assertEqual(project["bidLocation"], "北京市海淀区开标室")
        self.assertEqual(project["warrantyRequirement"], "验收合格后3年")
        self.assertEqual(project["bidCopyRequirement"], "正本1份，副本4份")
        self.assertEqual(project["packagingRequirement"], "正副本分开密封")
        self.assertEqual(project["signatureSealRequirement"], "法定代表人签字并加盖公章")
        self.assertEqual(len(extraction["requirements"]), 5)
        self.assertEqual(
            {row["category"] for row in extraction["requirements"]},
            {"qualification", "personnel", "performance", "financial", "credit"},
        )

    def test_project_fields_prefer_basic_and_opening_sections_over_qualification_text(self) -> None:
        text = """一、项目基本情况
项目名称：城市生命线安全监测平台项目
项目编号：CL-2026-052
二、申请人的资格要求
投标截止时间：供应商应在投标截止时间前完成 CA 证书办理。
项目名称：供应商提供的类似项目名称应体现在合同中。
1. 投标人须具有有效的营业执照。
三、提交投标文件截止时间、开标时间和地点
投标截止时间：2026年10月12日 09:30
开标时间：2026年10月12日 09:30
开标地点：北京市海淀区第一开标室
"""
        extraction = extract_requirements_by_rule(text)
        project = extraction["project"]

        self.assertEqual(project["projectName"], "城市生命线安全监测平台项目")
        self.assertEqual(project["projectCode"], "CL-2026-052")
        self.assertEqual(project["bidDeadline"], "2026年10月12日 09:30")
        self.assertEqual(project["openingTime"], "2026年10月12日 09:30")
        self.assertEqual(project["openingLocation"], "北京市海淀区第一开标室")

    def test_requirement_extraction_ignores_procurement_opening_and_contact_sections(self) -> None:
        text = """一、项目基本情况
项目名称：智慧水务平台项目
本项目是否接受联合体投标：否
二、采购需求
供应商应完成平台部署、数据接入和系统培训。
供应商应提供不少于三年的免费质保服务。
三、申请人的资格要求
1. 投标人须具有有效的营业执照。
四、获取招标文件
供应商应在规定时间内获取招标文件。
五、提交投标文件截止时间、开标时间和地点
供应商应在投标截止时间前递交投标文件。
六、对本次招标提出询问，请按以下方式联系
供应商联系人应保持电话畅通。
"""
        extraction = extract_requirements_by_rule(text)

        self.assertEqual(
            [row["text"] for row in extraction["requirements"]],
            [
                "本项目是否接受联合体投标：否",
                "投标人须具有有效的营业执照。",
            ],
        )

    def test_realistic_announcement_regression_fixtures(self) -> None:
        fixtures = [
            {
                "name": "政府采购公开招标公告",
                "text": """某市城市运行管理服务平台建设项目公开招标公告
项目概况
某市城市运行管理服务平台建设项目的潜在投标人应在公共资源交易平台获取招标文件。
一、项目基本情况
1.项目编号：ZFCG-2026-071
2.项目名称：某市城市运行管理服务平台建设项目
3.采购方式：公开招标
4.预算金额：680万元
5.最高限价：680万元
6.采购需求：建设城市运行指标、事件协同、三维可视化等功能。
7.合同履行期限：合同签订后120日历天内完成建设。
8.本项目不接受联合体投标。
二、申请人的资格要求：
1.满足《中华人民共和国政府采购法》第二十二条规定；
2.落实政府采购政策需满足的资格要求：本项目专门面向中小企业采购。
3.本项目的特定资格要求：
3.1 投标人须具有电子与智能化工程专业承包二级及以上资质。
3.2 拟派项目经理须具有机电工程专业二级及以上注册建造师证书。
3.3 提供2025年度经审计的财务报告。
3.4 未被信用中国列入失信被执行人名单。
三、获取招标文件
时间：2026年9月11日至2026年9月18日，每天上午9:00至12:00，下午14:00至17:00。
四、提交投标文件截止时间、开标时间和地点
提交投标文件截止时间：2026年10月12日09点30分
开标地点：某市公共资源交易中心第一开标室
""",
                "project": {
                    "projectCode": "ZFCG-2026-071",
                    "projectName": "某市城市运行管理服务平台建设项目",
                    "bidDeadline": "2026年10月12日09点30分",
                    "openingLocation": "某市公共资源交易中心第一开标室",
                    "jointVentureRequirement": "本项目不接受联合体投标。",
                },
                "noticeType": "公开招标公告",
                "required_text": [
                    "本项目不接受联合体投标。",
                    "满足《中华人民共和国政府采购法》第二十二条规定",
                    "本项目专门面向中小企业采购。",
                    "投标人须具有电子与智能化工程专业承包二级及以上资质。",
                    "拟派项目经理须具有机电工程专业二级及以上注册建造师证书。",
                    "提供2025年度经审计的财务报告。",
                    "未被信用中国列入失信被执行人名单。",
                ],
                "forbidden_text": ["建设城市运行指标", "获取招标文件", "提交投标文件截止时间"],
            },
            {
                "name": "竞争性磋商公告",
                "text": """智慧校园数据治理平台竞争性磋商公告
一、项目基本情况
项目编号：CS-2026-019
项目名称：智慧校园数据治理平台
采购方式：竞争性磋商
采购需求：供应商应完成数据治理平台部署、数据标准配置和系统培训。
服务期限：合同签订后90日内完成。
二、供应商的资格要求
1.供应商须具有独立承担民事责任的能力。
2.供应商近三年具有至少1项数据治理或智慧校园类似项目业绩。
3.供应商拟派项目负责人须具有信息系统项目管理师证书。
4.供应商须提供银行资信证明或2025年度财务报表。
三、获取采购文件
时间：2026年9月11日至2026年9月17日。
四、响应文件提交
截止时间：2026年9月22日14点00分
地点：某大学采购中心会议室
五、开启
时间：2026年9月22日14点00分
地点：某大学采购中心会议室
""",
                "project": {
                    "projectCode": "CS-2026-019",
                    "projectName": "智慧校园数据治理平台",
                    "procurementMethod": "竞争性磋商",
                    "projectDuration": "合同签订后90日内完成。",
                    "bidDeadline": "2026年9月22日14点00分",
                },
                "noticeType": "竞争性磋商公告",
                "required_text": [
                    "供应商须具有独立承担民事责任的能力。",
                    "供应商近三年具有至少1项数据治理或智慧校园类似项目业绩。",
                    "供应商拟派项目负责人须具有信息系统项目管理师证书。",
                    "供应商须提供银行资信证明或2025年度财务报表。",
                ],
                "forbidden_text": ["数据标准配置和系统培训", "获取采购文件"],
            },
            {
                "name": "废标结果公告",
                "text": """某单位数据中心运维服务废标公告
一、项目编号：FB-2026-004
二、项目名称：某单位数据中心运维服务
三、废标原因
通过资格审查的供应商不足三家，本项目予以废标。
四、评审专家名单
张三、李四、王五
五、凡对本次公告内容提出询问，请按以下方式联系
1.采购人信息
名称：某单位
""",
                "project": {
                    "projectCode": "FB-2026-004",
                    "projectName": "某单位数据中心运维服务",
                },
                "noticeType": "废标公告",
                "required_text": [],
                "forbidden_text": ["通过资格审查的供应商不足三家"],
                "requirements_count": 0,
            },
        ]

        for fixture in fixtures:
            with self.subTest(fixture["name"]):
                extraction = extract_requirements_by_rule(fixture["text"])
                project = extraction["project"]
                requirement_texts = [row["text"] for row in extraction["requirements"]]

                self.assertEqual(project["noticeType"], fixture["noticeType"])
                for key, value in fixture["project"].items():
                    self.assertEqual(project.get(key), value)
                if "requirements_count" in fixture:
                    self.assertEqual(len(requirement_texts), fixture["requirements_count"])
                for expected in fixture["required_text"]:
                    self.assertTrue(
                        any(expected in actual for actual in requirement_texts),
                        f"{expected!r} not found in {requirement_texts!r}",
                    )
                joined_requirements = "\n".join(requirement_texts)
                for forbidden in fixture["forbidden_text"]:
                    self.assertNotIn(forbidden, joined_requirements)

    def test_missing_qualification_is_pending_material_not_not_satisfied(self) -> None:
        result = evaluate_announcement_eligibility(
            "投标人资格要求\n1. 投标人须具有有效的ISO27001认证证书。",
            {},
            {},
            {"enabled": False},
            "FILE-001",
        )

        qualification_row = next(
            row for row in result["rows"] if row["category"] == "qualification"
        )
        self.assertEqual(qualification_row["status"], "不确定")
        self.assertEqual(result["summary"]["notSatisfiedCount"], 0)

    def test_pending_evidence_is_not_verified(self) -> None:
        evidence = normalize_evidence(
            {
                "id": "EVD-001",
                "name": "ISO9001证书",
                "status": "待核验有效期",
                "level": "强",
                "sourceType": "evidence",
            }
        )

        self.assertFalse(evidence["isVerified"])
        self.assertFalse(evidence["isExpired"])

    def test_valid_status_is_verified(self) -> None:
        """Excel「状态」列填「有效」应判为已核验→满足。"""
        evidence = normalize_evidence(
            {
                "id": "EVD-003",
                "name": "ISO27001认证",
                "status": "有效",
                "sourceType": "evidence",
            }
        )
        self.assertTrue(evidence["isVerified"])
        self.assertFalse(evidence["isExpired"])

    def test_current_verified_evidence_wins_over_expired_history(self) -> None:
        matched = [{"id": "QUAL-001"}]
        evidences = [
            {
                "id": "EVD-OLD",
                "status": "已核验但已过期",
                "sourceType": "evidence",
            },
            {
                "id": "EVD-CURRENT",
                "status": "已核验 当前有效",
                "sourceType": "evidence",
            },
        ]

        self.assertEqual(
            announcement_status_from_evidence("qualification", matched, evidences),
            "满足",
        )

    def test_summary_counts_direct_evidence_and_mandatory_closure(self) -> None:
        requirement = normalize_requirement(
            {
                "id": "REQ-001",
                "category": "qualification",
                "text": "提供有效的ISO9001认证证书",
                "mandatory": True,
            },
            source_stage="tender_document",
            layer=3,
            kind="资格审查",
        )
        row = build_comparison(
            requirement,
            status="不确定",
            reason="证据待核验",
            matched_objects=[{"id": "QUAL-001", "materialIds": ["MAT-001"]}],
            evidences=[
                {
                    "id": "EVD-001",
                    "name": "ISO9001证书",
                    "status": "待核验有效期",
                    "sourceType": "evidence",
                }
            ],
            risk="高",
            suggestion="核验证书原件和有效期",
        )

        summary = summarize_comparisons([row])

        self.assertEqual(summary["evidenceCoverageRate"], 100)
        self.assertEqual(summary["verifiedEvidenceCount"], 0)
        self.assertEqual(summary["mandatoryClosureRate"], 0)
        self.assertEqual(row["matchedMaterialIds"], ["MAT-001"])

    def test_unresolved_rejection_is_risk_but_not_confirmed_blocker(self) -> None:
        requirement = normalize_requirement(
            {
                "id": "REQ-REJECT",
                "category": "rejection",
                "text": "未按要求盖章的，投标无效。",
                "mandatory": True,
                "isRejection": True,
            },
            source_stage="tender_document",
            layer=3,
            kind="否决条款",
        )
        row = build_comparison(
            requirement,
            status="不确定",
            reason="需要人工核验签章要求",
            matched_objects=[],
            evidences=[],
            risk="中",
            suggestion="人工核验",
        )

        review = summarize_fine_review([row], {})

        self.assertEqual(review["hardBlockers"], [])
        self.assertEqual(len(review["rejectionRisks"]), 1)
        self.assertEqual(review["recommendation"], "人工重点评估")

    def test_confirmed_rejection_failure_blocks_participation(self) -> None:
        requirement = normalize_requirement(
            {
                "id": "REQ-REJECT",
                "category": "rejection",
                "text": "关键资质缺失的，投标无效。",
                "mandatory": True,
                "isRejection": True,
            },
            source_stage="tender_document",
            layer=3,
            kind="否决条款",
        )
        row = build_comparison(
            requirement,
            status="不满足",
            reason="人工确认关键资质缺失",
            matched_objects=[],
            evidences=[],
            risk="高",
            suggestion="不建议参与",
            status_source="人工审核",
        )

        review = summarize_fine_review([row], {})

        self.assertEqual(len(review["hardBlockers"]), 1)
        self.assertEqual(review["recommendation"], "不建议参与")

    def test_manual_review_updates_contract_status_fields(self) -> None:
        requirement = normalize_requirement(
            {
                "id": "REQ-MANUAL",
                "category": "technical",
                "text": "提供技术响应证明",
            },
            source_stage="tender_document",
            layer=3,
            kind="技术要求",
        )
        row = build_comparison(
            requirement,
            status="不确定",
            reason="证据尚未核验",
            matched_objects=[],
            evidences=[],
            risk="高",
            suggestion="补充材料",
        )
        review = summarize_fine_review([row], {})

        updated = update_fine_review_status(review, "REQ-MANUAL", "满足")

        self.assertIsNotNone(updated)
        updated_row = updated["rows"][0]
        self.assertEqual(updated_row["statusSource"], "人工审核")
        self.assertEqual(updated_row["reviewStatus"], "已人工复核")
        self.assertEqual(updated_row["riskLevel"], "低")
        self.assertIn("人工复核", updated_row["reason"])


if __name__ == "__main__":
    unittest.main()
