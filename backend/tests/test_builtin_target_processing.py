from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.tender_matching.builtin_target_processing import (
    _split_technical_clauses,
    build_target_fine_review,
)
from backend.app.tender_matching.document_fine_matching import extract_tender_requirements
from backend.app.tender_matching.document_matching import evaluate_tender_document


class BuiltinTargetProcessingTests(unittest.TestCase):
    def _document(self) -> list[dict[str, str]]:
        return [
            {"id": "SEG-PROJECT", "sourceLocator": "第1页", "text": "项目名称：智慧平台采购"},
            {"id": "SEG-QUAL", "sourceLocator": "第2页", "text": "投标人须具有有效营业执照。"},
            {
                "id": "SEG-TECH",
                "sourceLocator": "第3页",
                "text": "平台应支持三维场景加载，数量：2套。",
            },
            {"id": "SEG-COMM", "sourceLocator": "第4页", "text": "投标截止时间：2026年10月1日"},
            {"id": "SEG-EVAL", "sourceLocator": "第5页", "text": "技术响应，满分20分。"},
            {"id": "SEG-TEMPLATE", "sourceLocator": "第6页", "text": "投标函应按本格式填写并盖章。"},
            {"id": "SEG-CONTRACT", "sourceLocator": "第7页", "text": "合同签订后，乙方负责履约验收。"},
            {"id": "SEG-REJECT", "sourceLocator": "第8页", "text": "未按要求递交的，投标无效。"},
        ]

    def test_builtin_output_is_target_oriented_without_category(self) -> None:
        result = extract_tender_requirements(
            "",
            self._document(),
            {"enabled": False},
            sections=[],
            tables=[],
        )
        pack = result["analysisPack"]
        modules = {
            item["targetModule"]: item
            for item in pack["targetModules"]
        }
        self.assertEqual(
            set(modules),
            {
                "technical",
                "qualification",
                "commercial",
                "evaluation",
                "contract",
                "template",
                "project_info",
                "rejection",
            },
        )
        self.assertTrue(modules["technical"]["items"])
        self.assertTrue(modules["qualification"]["items"])
        self.assertTrue(modules["commercial"]["items"])
        self.assertTrue(modules["evaluation"]["items"])
        self.assertTrue(modules["template"]["items"])
        self.assertTrue(modules["project_info"]["items"])
        self.assertTrue(modules["rejection"]["items"])
        self.assertEqual(modules["contract"]["items"], [])
        self.assertTrue(modules["contract"]["ignored"])
        self.assertTrue(result["requirements"])
        self.assertTrue(all("category" not in item for item in result["requirements"]))
        self.assertNotIn("qualificationRequirements", pack)
        self.assertNotIn("technicalRequirements", pack)

    def test_matching_stage_receives_three_targets_but_rejection_is_not_matched(self) -> None:
        company = {
            "organization": {
                "organizationId": "ORG-001",
                "unifiedSocialCreditCode": "91350000TEST",
            },
            "certificates": [],
            "projects": [],
            "personnel": [],
            "finance": [],
            "products": [],
            "parameters": [],
            "capabilities": [],
        }
        result = evaluate_tender_document(
            "",
            self._document(),
            company,
            {"enabled": False},
        )
        rows = result["rows"]
        self.assertTrue(rows)
        self.assertEqual(
            {row["targetModule"] for row in rows},
            {"technical", "qualification", "rejection"},
        )
        self.assertTrue(all("category" not in row for row in rows))
        self.assertTrue(
            all(
                row["targetModule"] != "rejection"
                or row["requirementClass"] == "rejection"
                for row in rows
            )
        )
        self.assertEqual(
            result["matchStage"],
            {
                "targetModules": ["technical", "qualification", "rejection"],
                "enterpriseMatchTargets": ["technical", "qualification"],
                "riskCheckTargets": ["rejection"],
                "enterpriseMatchCount": 2,
                "riskCheckCount": 1,
            },
        )
        rejection_rows = [
            row for row in rows if row["targetModule"] == "rejection"
        ]
        self.assertTrue(rejection_rows)
        self.assertTrue(
            all(
                row["primaryRoute"] == "rejection_check"
                and row["handlingMode"] == "risk_check"
                and not row.get("matchedObjectIds")
                and not row.get("matchedMaterialIds")
                and not row.get("evidenceIds")
                for row in rejection_rows
            )
        )
        pack = result["analysisPack"]
        self.assertEqual(
            pack["matchStageTargets"],
            ["technical", "qualification", "rejection"],
        )
        self.assertEqual(
            pack["enterpriseMatchTargets"],
            ["technical", "qualification"],
        )
        self.assertEqual(pack["riskCheckTargets"], ["rejection"])
        self.assertEqual(
            {
                item["targetModule"]
                for item in pack["matchStageRequirements"]
            },
            {"technical", "qualification", "rejection"},
        )
        self.assertTrue(
            all(
                item["targetModule"] != "rejection"
                for item in pack["enterpriseMatchRequirements"]
            )
        )
        self.assertEqual(result["extraction"]["provider"], "builtin-target")

    # ── helpers for technical-alignment contract tests ──

    def _tech_company(self) -> dict[str, object]:
        return {
            "organization": {
                "organizationId": "ORG-001",
                "unifiedSocialCreditCode": "91350000TEST",
            },
            "products": [
                {
                    "productId": "prd-1",
                    "productName": "智慧平台",
                    "aliases": "",
                    "category": "平台",
                    "brand": "",
                    "model": "",
                    "description": "支持三维场景加载与渲染的平台产品。",
                    "applicableScenario": "数字孪生",
                }
            ],
            "parameters": [
                {
                    "parameterId": "param-1",
                    "productId": "prd-1",
                    "description": "支持三维场景加载",
                    "evidencePath": "lib/param1.png",
                },
                {
                    "parameterId": "param-2",
                    "productId": "prd-1",
                    "description": "支持实时渲染",
                    "evidencePath": "",
                },
            ],
            "certificates": [],
            "projects": [],
            "personnel": [],
            "finance": [],
            "capabilities": [],
        }

    def _tech_extraction(self) -> dict[str, object]:
        return {
            "requirements": [],
            "targetModules": [
                {
                    "targetModule": "technical",
                    "items": [
                        {
                            "id": "technical-0001",
                            "targetModule": "technical",
                            "text": "1.支持三维场景加载，数量：2套。",
                            "rawText": "1 | 产品A | 1.支持三维场景加载，数量：2套。 | 套 | 2",
                            "productName": "产品A",
                            "targetType": "产品/标的",
                            "quantity": "2",
                            "unit": "套",
                            "parameters": "1.支持三维场景加载，数量：2套。",
                            "requirementNumber": "1",
                            "clauseIndex": 1,
                            "clauseCount": 1,
                            "matchDomain": "technical",
                            "confidence": 0.78,
                            "handlingMode": "database_match",
                            "source": {
                                "segmentId": "SEG-TECH",
                                "segmentIds": ["SEG-TECH"],
                                "sourceLocator": "第3页",
                                "sectionId": "SEC-1",
                                "sectionPath": ["技术要求"],
                                "tableIds": [],
                            },
                        }
                    ],
                }
            ],
            "targetRanges": [],
            "extractionProvider": "builtin-target",
            "extractionMethod": "target-rule",
            "analysisPack": {"schemaVersion": "tender-targets/1.0"},
            "stats": {},
            "warnings": [],
        }

    # ── tests ──

    def test_technical_alignment_lands_on_row_when_judge_returns_coverage(
        self,
    ) -> None:
        """技术类行应当携带 technicalAlignment（coverage/matchedUnits/gaps），
        招标侧 productName 不受影响。"""
        extraction = self._tech_extraction()
        company = self._tech_company()

        fake_match = {
            "status": "满足",
            "matchedFactIds": ["prd-1", "param-1"],
            "reason": "已覆盖：支持三维场景加载",
            "coverage": 1.0,
            "gaps": [],
            "technicalAlignment": {
                "coverage": 1.0,
                "coveredPoints": ["支持三维场景加载"],
                "gaps": [],
                "matchedUnits": [
                    {
                        "productId": "prd-1",
                        "matchedParams": ["param-1"],
                        "matchedFragment": "支持三维场景加载",
                    }
                ],
            },
            "statusSource": "技术对齐判定",
        }

        with patch(
            "backend.app.tender_matching.builtin_target_processing"
            ".judge_technical_requirements",
            return_value={0: fake_match},
        ) as mock_judge:
            review = build_target_fine_review(
                extraction=extraction,
                company_knowledge=company,
                ai={
                    "enabled": True,
                    "model": "test",
                    "baseUrl": "http://test",
                    "apiKey": "test",
                },
                source_file_id="",
            )

        tech_rows = [
            row
            for row in review["rows"]
            if row.get("targetModule") == "technical"
        ]
        self.assertEqual(len(tech_rows), 1)
        row = tech_rows[0]

        # 1) 招标侧所属产品保留
        self.assertEqual(row["productName"], "产品A")

        # 2) status / reason 透传
        self.assertEqual(row["status"], "满足")
        self.assertEqual(row["statusSource"], "技术对齐判定")
        self.assertIn("已覆盖", row["reason"])

        # 3) technicalAlignment 落到 row
        self.assertIn("technicalAlignment", row)
        ta = row["technicalAlignment"]
        self.assertEqual(ta["coverage"], 1.0)
        self.assertEqual(ta["coveredPoints"], ["支持三维场景加载"])
        self.assertEqual(ta["gaps"], [])
        self.assertEqual(len(ta["matchedUnits"]), 1)

        mu = ta["matchedUnits"][0]
        self.assertEqual(mu["productId"], "prd-1")
        self.assertEqual(mu["matchedParams"], ["param-1"])
        self.assertEqual(mu["matchedFragment"], "支持三维场景加载")

        # 4) gaps 也落到 row（正交 field）
        self.assertIn("gaps", row)
        self.assertEqual(row["gaps"], [])

        # 5) matchedObjectIds 包含产品 ID（由 _status_result → _facts_by_ids 产出）
        self.assertIn("prd-1", row["matchedObjectIds"])

        # 6) judge_technical_requirements 被调用
        mock_judge.assert_called_once()
        call_args = mock_judge.call_args[0]
        # tech_req_items
        self.assertEqual(len(call_args[0]), 1)
        self.assertEqual(call_args[0][0]["index"], 0)
        self.assertIn("支持三维场景加载", call_args[0][0]["req_text"])
        # tech_units
        self.assertTrue(call_args[1])
        unit_ids = {u["productId"] for u in call_args[1]}
        self.assertIn("prd-1", unit_ids)

    def test_technical_matching_without_company_products_falls_back(
        self,
    ) -> None:
        """企业台账无产品/参数时，技术行应 待补材料 且不调用 judge。"""
        extraction = self._tech_extraction()
        empty_company = self._tech_company()
        empty_company["products"] = []
        empty_company["parameters"] = []

        with patch(
            "backend.app.tender_matching.builtin_target_processing"
            ".judge_technical_requirements",
        ) as mock_judge:
            review = build_target_fine_review(
                extraction=extraction,
                company_knowledge=empty_company,
                ai={"enabled": False},
                source_file_id="",
            )

        tech_rows = [
            row
            for row in review["rows"]
            if row.get("targetModule") == "technical"
        ]
        self.assertEqual(len(tech_rows), 1)
        row = tech_rows[0]

        self.assertEqual(row["status"], "待补材料")
        self.assertEqual(row["statusSource"], "规则兜底")
        self.assertIn("企业未维护技术产品", row["reason"])
        self.assertNotIn("technicalAlignment", row)

        # judge 不应被调用（没有 tech_units）
        mock_judge.assert_not_called()

    def test_requirement_uses_split_clause_not_full_cell(self) -> None:
        """Row 上 requirement 字段必须是切分后的单条（text），rawText 保留整段原文。"""
        extraction = self._tech_extraction()
        company = self._tech_company()

        with patch(
            "backend.app.tender_matching.builtin_target_processing"
            ".judge_technical_requirements",
            return_value={
                0: {
                    "status": "待补材料",
                    "matchedFactIds": [],
                    "reason": "",
                    "statusSource": "规则兜底",
                }
            },
        ):
            review = build_target_fine_review(
                extraction=extraction,
                company_knowledge=company,
                ai={"enabled": False},
                source_file_id="",
            )

        row = [
            r
            for r in review["rows"]
            if r.get("targetModule") == "technical"
        ][0]

        # requirement = 切分后的单条（text），不应包含 " | " 表格分隔符
        self.assertEqual(row["requirement"], "1.支持三维场景加载，数量：2套。")
        self.assertNotIn(" | ", row["requirement"])
        self.assertEqual(row["sourceText"], row["requirement"])
        self.assertEqual(row["normalizedRequirement"], row["requirement"])

        # rawText = 整段单元格原文（保留来源回查能力）
        self.assertIn(" | ", row["rawText"])
        self.assertIn("产品A", row["rawText"])
        self.assertGreater(len(row["rawText"]), len(row["requirement"]))

        # productName（招标侧所属产品）不受影响
        self.assertEqual(row["productName"], "产品A")

    def test_split_technical_clauses_handles_chinese_and_circled_numbering(self) -> None:
        """技术单元格拆解应覆盖中文序号/（1）/①等旧正则漏掉的编号格式。"""
        segment = {"tableId": "T1", "cells": [], "text": "（一）三维引擎 （1）支持场景加载 （2）支持实时渲染"}

        clauses = _split_technical_clauses(segment, [])
        self.assertEqual(len(clauses), 3)
        self.assertEqual(clauses[0][0], "一")
        self.assertEqual(clauses[1][0], "1")
        self.assertEqual(clauses[2][0], "2")

        circled = _split_technical_clauses(
            {"tableId": "T2", "cells": [], "text": "①支持国产化 ②支持信创适配 ③支持集群部署"},
            [],
        )
        self.assertEqual(len(circled), 3)
        self.assertTrue(all("支持" in text for _num, text in circled))

    def test_split_technical_clauses_uses_paragraph_level_for_section_hierarchy(
        self,
    ) -> None:
        """参数单元格存在 paragraphs 时应走段落级拆解：过滤「一、/（一）」标题，
        保留「1支持/2支持」无分隔符编号。"""
        segment = {
            "tableId": "T1",
            "tableRequirementType": "technical",
            "text": "1 | 产品A | 一、底座平台\n（一）三维引擎\n1支持场景加载\n2支持实时渲染\n3支持数据融合 | 套 | 2",
            "cells": [
                {"text": "1", "paragraphs": [{"text": "1"}]},
                {"text": "产品A", "paragraphs": [{"text": "产品A"}]},
                {
                    "text": "一、底座平台\n（一）三维引擎\n1支持场景加载\n2支持实时渲染\n3支持数据融合",
                    "paragraphs": [
                        {"text": "一、底座平台"},
                        {"text": "（一）三维引擎"},
                        {"text": "1支持场景加载"},
                        {"text": "2支持实时渲染"},
                        {"text": "3支持数据融合"},
                    ],
                },
                {"text": "套", "paragraphs": [{"text": "套"}]},
                {"text": "2", "paragraphs": [{"text": "2"}]},
            ],
        }
        cells = ["1", "产品A", "一、底座平台\n（一）三维引擎\n1支持场景加载\n2支持实时渲染\n3支持数据融合", "套", "2"]

        clauses = _split_technical_clauses(segment, cells)
        self.assertEqual([num for num, _text in clauses], ["1", "2", "3"])
        self.assertNotIn("一、底座平台", " ".join(text for _num, text in clauses))
        self.assertNotIn("（一）三维引擎", " ".join(text for _num, text in clauses))

    def test_project_info_requires_explicit_field_value_and_reads_table_cells(self) -> None:
        segments = [
            {
                "id": "SEG-NAME",
                "sourceLocator": "第1页",
                "text": "项目名称：智慧平台采购",
            },
            {
                "id": "SEG-PROSE",
                "sourceLocator": "第2页",
                "text": "本项目服务内容包括平台建设与培训，相关费用按合同约定支付。",
            },
            {
                "id": "SEG-TABLE",
                "sourceLocator": "TABLE-001第2行",
                "text": "1 | 是否接受联合体 | 不接受联合体",
                "tableId": "TABLE-001",
            },
        ]
        result = extract_tender_requirements(
            "",
            segments,
            {"enabled": False},
            sections=[],
            tables=[],
        )
        project_items = result["analysisPack"]["targetModuleMap"]["project_info"]["items"]
        by_field = {
            item["field"]: item["value"]
            for item in project_items
        }

        self.assertEqual(by_field["projectName"], "智慧平台采购")
        self.assertEqual(by_field["jointVenture"], "不接受联合体")
        self.assertNotIn("procurementScope", by_field)
        self.assertNotIn("fee", by_field)

    def test_pure_technical_section_keeps_low_keyword_body_for_matching(self) -> None:
        segments = [
            {
                "id": "SEG-TECH-HEADING",
                "sourceLocator": "第1页",
                "text": "技术要求",
            },
            {
                "id": "SEG-TECH-LOW",
                "sourceLocator": "第1页",
                "text": "系统应稳定运行，提供完整的实施成果和操作说明。",
            },
        ]
        result = extract_tender_requirements(
            "",
            segments,
            {"enabled": False},
            sections=[
                {
                    "sectionId": "SEC-TECH",
                    "heading": "技术要求",
                    "sectionType": "technical",
                    "sectionTypeHits": ["technical"],
                    "sectionPath": ["技术要求"],
                    "segmentIds": [item["id"] for item in segments],
                }
            ],
            tables=[],
        )
        technical = result["analysisPack"]["targetModuleMap"]["technical"]
        technical_texts = {
            item["text"]
            for item in technical["items"]
        }
        self.assertIn(
            "系统应稳定运行，提供完整的实施成果和操作说明。",
            technical_texts,
        )
        self.assertTrue(
            any(
                item.get("sourceSegmentId") == "SEG-TECH-LOW"
                for item in technical["items"]
            )
        )

    def test_technical_table_keeps_full_parameter_cell_and_splits_numbered_rows(self) -> None:
        parameter_text = " ".join(
            f"{index}.支持技术指标{index}。"
            for index in range(1, 121)
        )
        segments = [
            {
                "id": "SEG-TECH-TABLE-HEADING",
                "sourceLocator": "第1段",
                "text": "二、技术和服务要求",
            },
            {
                "id": "SEG-TECH-TABLE-ROW",
                "sourceLocator": "TABLE-001第2行",
                "text": f"1 | 产品A | {parameter_text} | 套 | 1",
                "tableId": "TABLE-001",
                "cells": [
                    {"text": "1"},
                    {"text": "产品A"},
                    {"text": parameter_text},
                    {"text": "套"},
                    {"text": "1"},
                ],
            },
        ]
        result = extract_tender_requirements(
            "",
            segments,
            {"enabled": False},
            sections=[
                {
                    "sectionId": "SEC-TECH-TABLE",
                    "heading": "二、技术和服务要求",
                    "sectionType": "technical",
                    "sectionTypeHits": ["technical"],
                    "sectionPath": ["二、技术和服务要求"],
                    "headingSegmentId": "SEG-TECH-TABLE-HEADING",
                    "segmentIds": [item["id"] for item in segments],
                }
            ],
            tables=[],
        )
        technical_items = result["analysisPack"]["targetModuleMap"]["technical"]["items"]
        row_items = [
            item
            for item in technical_items
            if item.get("sourceSegmentId") == "SEG-TECH-TABLE-ROW"
        ]

        self.assertEqual(len(row_items), 120)
        self.assertEqual(row_items[0]["requirementNumber"], "1")
        self.assertTrue(row_items[0]["text"].startswith("1."))
        self.assertNotIn("产品A |", row_items[0]["text"])
        self.assertTrue(all(item["tableIds"] == ["TABLE-001"] for item in row_items))

    def test_pure_evaluation_section_filters_evaluation_process_rows(self) -> None:
        segments = [
            {
                "id": "SEG-SCORE",
                "sourceLocator": "第1页",
                "text": "技术评分标准：满足要求得20分。",
            },
            {
                "id": "SEG-PROCESS",
                "sourceLocator": "第1页",
                "text": "评标委员会启动异常低价投标审查程序。",
            },
        ]
        result = extract_tender_requirements(
            "",
            segments,
            {"enabled": False},
            sections=[
                {
                    "sectionId": "SEC-EVAL",
                    "heading": "评分标准",
                    "sectionType": "evaluation",
                    "sectionTypeHits": ["evaluation"],
                    "sectionPath": ["评分标准"],
                    "segmentIds": [item["id"] for item in segments],
                }
            ],
            tables=[],
        )
        evaluation = result["analysisPack"]["targetModuleMap"]["evaluation"]
        source_ids = {
            item["sourceSegmentId"]
            for item in evaluation["items"]
        }
        self.assertIn("SEG-SCORE", source_ids)
        self.assertNotIn("SEG-PROCESS", source_ids)

    def test_template_root_preserves_the_complete_source_range(self) -> None:
        segments = [
            {
                "id": "SEG-NOTICE",
                "sourceLocator": "第1页",
                "text": "发布更正公告，通知所有潜在投标人。",
            },
            {
                "id": "SEG-FORM",
                "sourceLocator": "第1页",
                "text": "投标函应按本格式填写并签字盖章。",
            },
        ]
        result = extract_tender_requirements(
            "",
            segments,
            {"enabled": False},
            sections=[
                {
                    "sectionId": "SEC-TEMPLATE",
                    "heading": "投标文件格式",
                    "sectionType": "document_format",
                    "sectionTypeHits": ["document_format"],
                    "sectionPath": ["投标文件格式"],
                    "segmentIds": [item["id"] for item in segments],
                }
            ],
            tables=[],
        )
        template = result["analysisPack"]["targetModuleMap"]["template"]
        source_ids = set(template["items"][0]["sourceSegmentIds"])
        self.assertEqual(source_ids, {"SEG-NOTICE", "SEG-FORM"})
        fine_source_ids = {
            item["sourceSegmentId"]
            for item in template["sourceItems"]
        }
        self.assertIn("SEG-FORM", fine_source_ids)
        self.assertNotIn("SEG-NOTICE", fine_source_ids)

    def test_product_capability_template_is_not_a_standalone_template(self) -> None:
        segments = [
            {
                "id": "SEG-CAPABILITY",
                "sourceLocator": "第1页",
                "text": "平台内置场景模板库和效果模板，支持快速配置。",
            },
            {
                "id": "SEG-BID-LETTER",
                "sourceLocator": "第2页",
                "text": "投标函应按本格式填写并签字盖章。",
            },
        ]
        result = extract_tender_requirements(
            "",
            segments,
            {"enabled": False},
            sections=[],
            tables=[],
        )
        template = result["analysisPack"]["targetModuleMap"]["template"]
        template_texts = {
            item["text"]
            for item in template["sourceItems"]
        }

        self.assertIn("投标函应按本格式填写并签字盖章。", template_texts)
        self.assertNotIn(
            "平台内置场景模板库和效果模板，支持快速配置。",
            template_texts,
        )

    def test_source_view_groups_sections_and_restores_table_rows(self) -> None:
        segments = [
            {
                "id": "SEG-COMM-H",
                "sourceLocator": "第1页",
                "text": "商务要求",
            },
            {
                "id": "SEG-COMM-P",
                "sourceLocator": "第1页",
                "text": "交付期：合同签订后30日内完成交付。",
            },
            {
                "id": "SEG-COMM-R1",
                "sourceLocator": "TABLE-COMM第1行",
                "text": "项目 | 要求",
                "contentType": "table_row",
                "tableId": "TABLE-COMM",
                "rowIndex": 1,
                "cells": ["项目", "要求"],
            },
            {
                "id": "SEG-COMM-R2",
                "sourceLocator": "TABLE-COMM第2行",
                "text": "付款方式 | 验收合格后付款",
                "contentType": "table_row",
                "tableId": "TABLE-COMM",
                "rowIndex": 2,
                "cells": ["付款方式", "验收合格后付款"],
            },
            {
                "id": "SEG-TPL-H",
                "sourceLocator": "第2页",
                "text": "投标文件格式",
            },
            {
                "id": "SEG-TPL-S",
                "sourceLocator": "第2页",
                "text": "一、投标函",
            },
            {
                "id": "SEG-TPL-P",
                "sourceLocator": "第2页",
                "text": "投标函应按本格式填写并签字盖章。",
            },
            {
                "id": "SEG-TPL-P2",
                "sourceLocator": "第2页",
                "text": "投标人名称、地址和授权代表应填写完整。",
            },
        ]
        sections = [
            {
                "sectionId": "SEC-COMM",
                "id": "SEC-COMM",
                "heading": "商务要求",
                "level": 1,
                "parentId": "",
                "sectionType": "commercial",
                "sectionTypeHits": ["commercial"],
                "sectionPath": ["商务要求"],
                "segmentIds": [
                    "SEG-COMM-H",
                    "SEG-COMM-P",
                    "SEG-COMM-R1",
                    "SEG-COMM-R2",
                ],
                "headingSegmentId": "SEG-COMM-H",
            },
            {
                "sectionId": "SEC-TPL",
                "id": "SEC-TPL",
                "heading": "投标文件格式",
                "level": 1,
                "parentId": "",
                "sectionType": "document_format",
                "sectionTypeHits": ["document_format"],
                "sectionPath": ["投标文件格式"],
                "segmentIds": [
                    "SEG-TPL-H",
                    "SEG-TPL-S",
                    "SEG-TPL-P",
                    "SEG-TPL-P2",
                ],
                "headingSegmentId": "SEG-TPL-H",
            },
            {
                "sectionId": "SEC-TPL-BID",
                "id": "SEC-TPL-BID",
                "heading": "一、投标函",
                "level": 2,
                "parentId": "SEC-TPL",
                "sectionType": "document_format",
                "sectionTypeHits": ["document_format"],
                "sectionPath": ["投标文件格式", "一、投标函"],
                "segmentIds": ["SEG-TPL-S", "SEG-TPL-P", "SEG-TPL-P2"],
                "headingSegmentId": "SEG-TPL-S",
            },
        ]
        tables = [
            {
                "tableId": "TABLE-COMM",
                "id": "TABLE-COMM",
                "rows": [
                    {
                        "rowIndex": 1,
                        "sourceLocator": "TABLE-COMM第1行",
                        "cells": [
                            {"cellIndex": 1, "text": "项目"},
                            {"cellIndex": 2, "text": "要求"},
                        ],
                    },
                    {
                        "rowIndex": 2,
                        "sourceLocator": "TABLE-COMM第2行",
                        "cells": [
                            {"cellIndex": 1, "text": "付款方式"},
                            {"cellIndex": 2, "text": "验收合格后付款"},
                        ],
                    },
                ],
                "rowCount": 2,
                "columnCount": 2,
            }
        ]

        result = extract_tender_requirements(
            "",
            segments,
            {"enabled": False},
            sections=sections,
            tables=tables,
        )
        pack = result["analysisPack"]
        commercial = pack["targetModuleMap"]["commercial"]
        template = pack["targetModuleMap"]["template"]

        self.assertEqual(commercial["displayMode"], "source_sections")
        self.assertEqual(commercial["sourceView"]["sectionCount"], 1)
        commercial_blocks = commercial["sourceView"]["sections"][0]["blocks"]
        table_block = next(
            block for block in commercial_blocks if block["type"] == "table"
        )
        self.assertEqual(len(table_block["rows"]), 2)
        self.assertEqual(
            table_block["rows"][1]["cells"][1]["text"],
            "验收合格后付款",
        )

        self.assertEqual(len(pack["templateIndex"]), len(template["items"]))
        self.assertGreater(template["sourceItemCount"], template["templateCount"])
        self.assertEqual(len(pack["templateIndex"]), 1)
        template_view = template["sourceView"]["sections"][0]
        self.assertEqual(template_view["children"][0]["title"], "一、投标函")
        self.assertEqual(
            pack["templateIndex"][0]["sourceSectionId"],
            "SEC-TPL",
        )
        self.assertIn(
            "SEG-TPL-P",
            pack["templateIndex"][0]["sourceSegmentIds"],
        )
        self.assertIn(
            "SEG-TPL-H",
            pack["templateIndex"][0]["sourceSegmentIds"],
        )
        self.assertEqual(template_view["templateId"], "TPL-0001")
        self.assertEqual(template["templateCount"], 1)
        self.assertEqual(template["templateSections"][0]["sectionId"], "SEC-TPL")
        self.assertEqual(
            template["templateSections"][1]["sectionId"],
            "SEC-TPL-BID",
        )

    def test_template_root_keeps_mixed_form_text_and_exports_as_one_range(self) -> None:
        segments = [
            {
                "id": "SEG-TPL-ROOT",
                "sourceLocator": "第10页",
                "text": "第七章 电子投标文件格式",
            },
            {
                "id": "SEG-TPL-INTRO",
                "sourceLocator": "第10页",
                "text": "兹收到贵单位关于项目的投标邀请，本投标人提交电子投标文件。",
            },
            {
                "id": "SEG-TPL-TECH",
                "sourceLocator": "第11页",
                "text": "二、技术和服务要求响应表",
            },
            {
                "id": "SEG-TPL-TECH-BODY",
                "sourceLocator": "TABLE-TPL第2行",
                "contentType": "table_row",
                "tableId": "TABLE-TPL",
                "rowIndex": 2,
                "text": "技术参数 | 投标响应 | 备注",
                "cells": [
                    {"cellIndex": 1, "text": "技术参数"},
                    {"cellIndex": 2, "text": "投标响应"},
                    {"cellIndex": 3, "text": "备注"},
                ],
            },
            {
                "id": "SEG-TPL-COMM",
                "sourceLocator": "第12页",
                "text": "三、商务条件响应表",
            },
            {
                "id": "SEG-TPL-END",
                "sourceLocator": "第12页",
                "text": "投标人签字盖章。",
            },
        ]
        sections = [
            {
                "sectionId": "SEC-TPL",
                "heading": "第七章 电子投标文件格式",
                "level": 1,
                "sectionPath": ["第七章 电子投标文件格式"],
                "segmentIds": [item["id"] for item in segments],
                "headingSegmentId": "SEG-TPL-ROOT",
            },
            {
                "sectionId": "SEC-TPL-TECH",
                "heading": "二、技术和服务要求响应表",
                "level": 2,
                "parentId": "SEC-TPL",
                "sectionPath": [
                    "第七章 电子投标文件格式",
                    "二、技术和服务要求响应表",
                ],
                "segmentIds": ["SEG-TPL-TECH", "SEG-TPL-TECH-BODY"],
                "headingSegmentId": "SEG-TPL-TECH",
            },
            {
                "sectionId": "SEC-TPL-COMM",
                "heading": "三、商务条件响应表",
                "level": 2,
                "parentId": "SEC-TPL",
                "sectionPath": [
                    "第七章 电子投标文件格式",
                    "三、商务条件响应表",
                ],
                "segmentIds": ["SEG-TPL-COMM", "SEG-TPL-END"],
                "headingSegmentId": "SEG-TPL-COMM",
            },
        ]
        tables = [
            {
                "tableId": "TABLE-TPL",
                "rows": [
                    {
                        "rowIndex": 2,
                        "sourceLocator": "TABLE-TPL第2行",
                        "cells": [
                            {"cellIndex": 1, "text": "技术参数"},
                            {"cellIndex": 2, "text": "投标响应"},
                            {"cellIndex": 3, "text": "备注"},
                        ],
                    }
                ],
            }
        ]

        result = extract_tender_requirements(
            "",
            segments,
            {"enabled": False},
            sections=sections,
            tables=tables,
        )
        pack = result["analysisPack"]
        template = pack["targetModuleMap"]["template"]
        template_index = pack["templateIndex"]

        self.assertEqual(len(template_index), 1)
        self.assertEqual(template_index[0]["sourceSectionId"], "SEC-TPL")
        self.assertEqual(template_index[0]["granularity"], "chapter")
        self.assertEqual(
            set(template_index[0]["sourceSegmentIds"]),
            {item["id"] for item in segments},
        )
        self.assertEqual(template["sourceView"]["segmentCount"], len(segments))
        self.assertEqual(
            template["sourceView"]["sections"][0]["sourceSegmentIds"],
            [item["id"] for item in segments],
        )
        self.assertEqual(
            template["sourceView"]["sections"][0]["templateId"],
            template_index[0]["templateId"],
        )
        self.assertIn(
            "兹收到贵单位关于项目的投标邀请",
            "\n".join(
                block["text"]
                for block in template["sourceView"]["sections"][0]["blocks"]
                if block["type"] == "paragraph"
            ),
        )
        self.assertEqual(
            template["sourceView"]["sections"][0]["children"][0]["title"],
            "二、技术和服务要求响应表",
        )

    def test_contract_range_is_retained_but_contract_items_are_ignored(self) -> None:
        segments = [
            {
                "id": "SEG-CONTRACT-TITLE",
                "sourceLocator": "第7页",
                "text": "第七章 拟签订合同文本",
            },
            {
                "id": "SEG-CONTRACT-BODY",
                "sourceLocator": "第7页",
                "text": "详见附件：政府采购合同文本。",
            },
        ]
        result = extract_tender_requirements(
            "",
            segments,
            {"enabled": False},
            sections=[
                {
                    "sectionId": "SEC-CONTRACT",
                    "heading": "第七章 拟签订合同文本",
                    "sectionType": "document_format",
                    "sectionTypeHits": ["document_format"],
                    "sectionPath": ["第七章 拟签订合同文本"],
                    "segmentIds": [
                        "SEG-CONTRACT-TITLE",
                        "SEG-CONTRACT-BODY",
                    ],
                }
            ],
            tables=[],
        )
        contract = next(
            item
            for item in result["analysisPack"]["targetModules"]
            if item["targetModule"] == "contract"
        )

        self.assertTrue(contract["ignored"])
        self.assertEqual(contract["items"], [])
        self.assertEqual(contract["selectedSegmentCount"], 2)
        self.assertTrue(contract["ignoredRanges"])


if __name__ == "__main__":
    unittest.main()
