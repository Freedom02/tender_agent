from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.tender_matching.contracts import (
    build_comparison,
    normalize_evidence,
    normalize_requirement,
    summarize_comparisons,
)
from backend.app.tender_matching.document_fine_matching import (
    _status_from_evidence as fine_status_from_evidence,
    summarize_fine_review,
    update_fine_review_status,
)
from backend.app.tender_matching.eligibility import (
    _status_from_evidence as announcement_status_from_evidence,
    evaluate_announcement_eligibility,
    extract_announcement_requirements,
    extract_requirements_by_rule,
    split_announcement_sections,
)


class MatchingContractTests(unittest.TestCase):
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
        self.assertNotIn("平台部署、数据接入、可视化建模", user_content)
        self.assertEqual(extraction["extractionMethod"], "llm+rule")

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
        self.assertEqual(qualification_row["status"], "待补材料")
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
        self.assertEqual(
            fine_status_from_evidence("qualification", matched, evidences),
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
            status="待补材料",
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
            status="待澄清",
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
            status="待补材料",
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
