from __future__ import annotations

import unittest

from backend.app.tender_matching.document_fine_matching import (
    evaluate_tender_document,
    extract_tender_requirements,
    extract_tender_requirements_by_rule,
)
from backend.app.tender_matching.requirement_scope import select_requirement_scope


class RequirementScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.segments = [
            {
                "id": "SEG-BASIC",
                "sourceLocator": "第1页",
                "text": "项目名称：城市运行平台项目；项目编号：CS-2026-001；采购范围：平台建设。",
            },
            {
                "id": "SEG-REF",
                "sourceLocator": "第2页",
                "text": "资格审查具体内容详见第五章技术要求，资格条件详见附件一。",
            },
            {
                "id": "SEG-QUAL",
                "sourceLocator": "第3页",
                "text": "一、资格性审查",
            },
            {
                "id": "SEG-QUAL-BODY",
                "sourceLocator": "第3页",
                "text": "投标人须具有有效的营业执照。",
            },
            {
                "id": "SEG-TECH",
                "sourceLocator": "第5页",
                "text": "第五章 技术要求",
            },
            {
                "id": "SEG-TECH-BODY",
                "sourceLocator": "第5页",
                "text": "平台应支持三维可视化和数据接入。",
            },
            {
                "id": "SEG-TABLE-REF",
                "sourceLocator": "TABLE-001第1行",
                "tableId": "TABLE-001",
                "text": "技术参数 | 投标响应 | 是否偏离",
            },
            {
                "id": "SEG-TABLE-BODY",
                "sourceLocator": "TABLE-001第2行",
                "tableId": "TABLE-001",
                "text": "服务器 | 支持国产处理器 | 是",
            },
            {
                "id": "SEG-UNRESOLVED",
                "sourceLocator": "第8页",
                "text": "具体要求详见附件九。",
            },
        ]
        self.sections = [
            {
                "id": "SEC-QUAL",
                "sectionId": "SEC-QUAL",
                "sectionType": "qualification",
                "heading": "一、资格性审查",
                "sectionPath": ["第二篇 资格审查", "一、资格性审查"],
                "startLocator": "第3页",
                "segmentIds": ["SEG-QUAL", "SEG-QUAL-BODY"],
            },
            {
                "id": "SEC-TECH",
                "sectionId": "SEC-TECH",
                "sectionType": "technical",
                "heading": "第五章 技术要求",
                "sectionPath": ["第五章 技术要求"],
                "startLocator": "第5页",
                "segmentIds": ["SEG-TECH", "SEG-TECH-BODY"],
            },
            {
                "id": "SEC-APPENDIX",
                "sectionId": "SEC-APPENDIX",
                "sectionType": "appendix",
                "heading": "附件一 资格审查表",
                "sectionPath": ["附件一 资格审查表"],
                "startLocator": "第10页",
                "segmentIds": [],
            },
        ]
        self.tables = [
            {
                "id": "TABLE-001",
                "tableId": "TABLE-001",
                "rows": [
                    {"sourceLocator": "TABLE-001第1行", "text": "技术参数 | 投标响应 | 是否偏离"},
                    {"sourceLocator": "TABLE-001第2行", "text": "服务器 | 支持国产处理器 | 是"},
                ],
            }
        ]

    def test_selects_requirement_sections_and_excludes_basic_project_information(self) -> None:
        result = select_requirement_scope(self.segments, self.sections, self.tables)

        selected_ids = {item["id"] for item in result["selectedSegments"]}
        self.assertIn("SEG-QUAL-BODY", selected_ids)
        self.assertIn("SEG-TECH-BODY", selected_ids)
        self.assertNotIn("SEG-BASIC", selected_ids)
        self.assertIn("TABLE-001", {item["tableId"] for item in result["selectedTables"]})

    def test_resolves_chapter_appendix_table_and_local_table_references(self) -> None:
        segments = [
            *[
                segment
                for segment in self.segments
                if segment["id"] != "SEG-UNRESOLVED"
            ],
            {
                "id": "SEG-APPENDIX-REF",
                "sourceLocator": "第9页",
                "text": "资格材料详见附件一。",
            },
            {
                "id": "SEG-TABLE-REF-TEXT",
                "sourceLocator": "第9页",
                "text": "技术参数详见技术参数表。",
            },
            {
                "id": "SEG-LOCAL-TABLE",
                "sourceLocator": "TABLE-001第3行",
                "tableId": "TABLE-001",
                "text": "本项目技术参数以本表为准。",
            },
        ]
        result = select_requirement_scope(segments, self.sections, self.tables)

        targets = [item["targetText"] for item in result["references"]]
        self.assertIn("第五章技术要求", targets)
        self.assertIn("附件一", targets)
        self.assertIn("技术参数表", targets)
        self.assertIn("本表", targets)
        self.assertEqual(result["unresolvedReferences"], [])
        referenced_items = [
            item for item in result["scopeItems"]
            if item["priority"] == "referenced"
        ]
        self.assertTrue(any(item["referenceFrom"] == "第9页" for item in referenced_items))

    def test_unresolved_reference_is_reported_and_duplicate_section_is_kept_once(self) -> None:
        segments = [
            *self.segments,
            {
                "id": "SEG-REF-2",
                "sourceLocator": "第4页",
                "text": "更多技术要求详见第五章。",
            },
        ]
        result = select_requirement_scope(segments, self.sections, self.tables)

        selected_section_ids = [
            item["sectionId"] for item in result["selectedSections"]
        ]
        self.assertEqual(selected_section_ids.count("SEC-TECH"), 1)
        self.assertEqual(len(result["unresolvedReferences"]), 1)
        self.assertEqual(result["unresolvedReferences"][0]["targetText"], "附件九")
        self.assertTrue(any("未定位" in warning for warning in result["warnings"]))

    def test_fallback_selects_condition_lines_when_no_section_tree_exists(self) -> None:
        result = select_requirement_scope(
            [
                {"id": "SEG-001", "sourceLocator": "第1行", "text": "项目名称：测试项目"},
                {"id": "SEG-002", "sourceLocator": "第2行", "text": "投标人须提供有效的资质证书。"},
            ],
            [],
            [],
        )

        self.assertEqual(
            [item["id"] for item in result["selectedSegments"]],
            ["SEG-002"],
        )
        self.assertIn("未识别到明确招标条件章节", result["warnings"][0])

    def test_fine_extraction_consumes_scope_instead_of_basic_project_text(self) -> None:
        result = extract_tender_requirements(
            "项目名称：不应进入条件。\n投标人须具有有效营业执照。",
            [
                {
                    "id": "SEG-BASIC",
                    "sourceLocator": "第1页",
                    "text": "项目名称：不应进入条件。",
                },
                {
                    "id": "SEG-QUAL",
                    "sourceLocator": "第2页",
                    "text": "一、资格性审查",
                },
                {
                    "id": "SEG-QUAL-BODY",
                    "sourceLocator": "第2页",
                    "text": "投标人须具有有效营业执照。",
                },
            ],
            {"enabled": False},
            [
                {
                    "id": "SEC-QUAL",
                    "sectionId": "SEC-QUAL",
                    "sectionType": "qualification",
                    "heading": "一、资格性审查",
                    "sectionPath": ["一、资格性审查"],
                    "startLocator": "第2页",
                    "segmentIds": ["SEG-QUAL", "SEG-QUAL-BODY"],
                }
            ],
            [],
        )

        self.assertEqual(result["scope"]["stats"]["selectedSegmentCount"], 2)
        self.assertTrue(
            all("项目名称" not in item["text"] for item in result["requirements"])
        )

    def test_fine_extraction_filters_invitation_text_even_when_parent_type_is_inherited(self) -> None:
        result = extract_tender_requirements(
            "投标邀请书\n项目名称：不应进入条件。\n投标人须具有有效营业执照。",
            [
                {
                    "id": "SEG-INVITATION",
                    "sourceLocator": "第1页",
                    "text": "投标邀请书：请符合条件的供应商参加本项目。",
                },
                {
                    "id": "SEG-BASIC",
                    "sourceLocator": "第1页",
                    "text": "项目名称：不应进入条件。项目编号：NO-001。",
                },
                {
                    "id": "SEG-QUAL",
                    "sourceLocator": "第2页",
                    "text": "一、资格性审查",
                },
                {
                    "id": "SEG-QUAL-BODY",
                    "sourceLocator": "第2页",
                    "text": "投标人须具有有效营业执照。",
                },
            ],
            {"enabled": False},
            [
                {
                    "id": "SEC-QUAL",
                    "sectionId": "SEC-QUAL",
                    "sectionType": "qualification",
                    "heading": "一、资格性审查",
                    "sectionPath": ["投标邀请书", "一、资格性审查"],
                    "startLocator": "第2页",
                    "segmentIds": ["SEG-INVITATION", "SEG-BASIC", "SEG-QUAL", "SEG-QUAL-BODY"],
                }
            ],
            [],
        )

        texts = [item["text"] for item in result["requirements"]]
        self.assertIn("投标人须具有有效营业执照。", texts)
        self.assertTrue(all("投标邀请书" not in text for text in texts))
        self.assertTrue(all("项目名称" not in text and "项目编号" not in text for text in texts))

    def test_fine_extraction_keeps_technical_parameter_table_rows(self) -> None:
        result = extract_tender_requirements(
            "",
            [
                {
                    "id": "SEG-HEADER",
                    "sourceLocator": "TABLE-TECH第1行",
                    "tableId": "TABLE-TECH",
                    "text": "技术参数 | 投标响应 | 是否偏离",
                },
                {
                    "id": "SEG-TECH-ROW",
                    "sourceLocator": "TABLE-TECH第2行",
                    "tableId": "TABLE-TECH",
                    "text": "服务器 | 支持国产处理器 | 是",
                },
            ],
            {"enabled": False},
            [],
            [
                {
                    "id": "TABLE-TECH",
                    "tableId": "TABLE-TECH",
                    "rows": [
                        {"sourceLocator": "TABLE-TECH第1行", "text": "技术参数 | 投标响应 | 是否偏离"},
                        {"sourceLocator": "TABLE-TECH第2行", "text": "服务器 | 支持国产处理器 | 是"},
                    ],
                }
            ],
        )

        texts = [item["text"] for item in result["requirements"]]
        self.assertIn("服务器 | 支持国产处理器 | 是", texts)
        self.assertNotIn("技术参数 | 投标响应 | 是否偏离", texts)

    def test_fine_extraction_does_not_fallback_to_a_mixed_announcement_line(self) -> None:
        result = extract_tender_requirements(
            "国道项目招标公告 招标项目编号：NO-001。项目名称：某项目。投标人须具备通信工程施工资质。",
            [
                {
                    "id": "SEG-LONG-ANNOUNCEMENT",
                    "sourceLocator": "第1页",
                    "text": "国道项目招标公告 招标项目编号：NO-001。项目名称：某项目。投标人须具备通信工程施工资质。",
                }
            ],
            {"enabled": False},
            [],
            [],
        )

        self.assertEqual(result["requirements"], [])

    def test_rule_extraction_splits_numbered_compound_requirements(self) -> None:
        result = extract_tender_requirements_by_rule(
            "",
            [
                {
                    "id": "SEG-COMPOUND",
                    "sourceLocator": "第7页",
                    "text": "（1）投标人须具有有效营业执照；（2）投标人须提供近三年类似业绩；（3）投标人不得有重大违法记录。",
                }
            ],
        )

        requirements = result["requirements"]
        self.assertEqual(len(requirements), 3)
        self.assertEqual(
            [item["id"] for item in requirements],
            ["rule-001-01", "rule-001-02", "rule-001-03"],
        )
        self.assertTrue(all(item["parentRequirementId"] == "rule-001" for item in requirements))
        self.assertEqual([item["clauseIndex"] for item in requirements], [1, 2, 3])
        self.assertTrue(all(item["clauseCount"] == 3 for item in requirements))
        self.assertTrue(all(item["sourceLocator"] == "第7页" for item in requirements))

    def test_rule_extraction_splits_unmarked_semicolon_requirements(self) -> None:
        result = extract_tender_requirements_by_rule(
            "",
            [
                {
                    "id": "SEG-SEMICOLON",
                    "sourceLocator": "第8页",
                    "text": "投标人须具有有效营业执照；投标人须提供法定代表人身份证明；投标人须加盖公章。",
                }
            ],
        )

        self.assertEqual(len(result["requirements"]), 3)
        self.assertEqual(
            [item["text"] for item in result["requirements"]],
            [
                "投标人须具有有效营业执照",
                "投标人须提供法定代表人身份证明",
                "投标人须加盖公章",
            ],
        )

    def test_rule_extraction_classifies_compliance_and_service_plan_requirements(self) -> None:
        result = extract_tender_requirements_by_rule(
            "",
            [
                {
                    "id": "SEG-COMPLIANCE",
                    "sourceLocator": "第12页",
                    "text": "投标文件须实质性响应技术参数，不得出现负偏离，并提供 ThingJS 平台检验证明。",
                },
                {
                    "id": "SEG-SERVICE-PLAN",
                    "sourceLocator": "第13页",
                    "text": "投标人须提供项目实施方案、培训方案和应急预案。",
                },
            ],
        )

        self.assertEqual(
            [item["category"] for item in result["requirements"]],
            ["compliance", "service_plan"],
        )

    def test_compliance_and_service_plan_match_company_technical_records(self) -> None:
        company_knowledge = {
            "products": [
                {
                    "id": "PRD-THINGJS",
                    "name": "ThingJS 平台",
                    "evidenceIds": ["EVD-THINGJS"],
                    "status": "资料已建模",
                }
            ],
            "capabilities": [
                {
                    "id": "CAP-DELIVERY",
                    "name": "项目实施方案与培训方案交付",
                    "evidenceIds": ["EVD-DELIVERY"],
                    "status": "资料已建模",
                }
            ],
            "evidences": [
                {
                    "id": "EVD-THINGJS",
                    "name": "ThingJS 平台检验证明",
                    "status": "待核验原件",
                },
                {
                    "id": "EVD-DELIVERY",
                    "name": "实施与培训交付材料",
                    "status": "待核验",
                },
            ],
        }
        result = evaluate_tender_document(
            "",
            [
                {
                    "id": "SEG-COMPLIANCE",
                    "sourceLocator": "第12页",
                    "text": "投标文件须实质性响应技术参数，并提供 ThingJS 平台检验证明。",
                },
                {
                    "id": "SEG-SERVICE-PLAN",
                    "sourceLocator": "第13页",
                    "text": "投标人须提供项目实施方案与培训方案交付说明。",
                },
            ],
            company_knowledge,
            {"enabled": False},
        )

        rows_by_category = {row["category"]: row for row in result["rows"]}
        self.assertEqual(rows_by_category["compliance"]["kind"], "符合性审查")
        self.assertEqual(rows_by_category["compliance"]["responseType"], "mandatory_confirmation")
        self.assertEqual(rows_by_category["compliance"]["matchedObjectIds"], ["PRD-THINGJS"])
        self.assertEqual(rows_by_category["service_plan"]["kind"], "服务方案要求")
        self.assertEqual(rows_by_category["service_plan"]["evidenceNeed"], "service_plan_reference")
        self.assertEqual(rows_by_category["service_plan"]["matchedObjectIds"], ["CAP-DELIVERY"])


if __name__ == "__main__":
    unittest.main()
