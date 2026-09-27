from __future__ import annotations

import unittest

from backend.app.agent_runtime.contracts import build_parse_bundle
from backend.app.agent_runtime.submission_structure import (
    is_reliable_submission_structure,
    normalize_submission_structure,
    parse_submission_format_tree,
)
from backend.app.bid_writing.planning import minimal_bid_skeleton
from backend.app.pi_agent.writing_orchestrator import _outline_quality_issues


class SubmissionStructureParserTests(unittest.TestCase):
    def test_composition_clause_is_the_directory_skeleton(self) -> None:
        text = """
10、电子投标文件
10.1电子投标文件的编制
（1）投标人应先仔细阅读招标文件。
10.2电子投标文件由下述部分组成：
（1）资格及资信证明部分
①投标函
②投标人的资格及资信证明文件
③投标保证金
（2）报价部分
①开标（报价）一览表
②投标（响应）报价明细表
③招标文件规定的价格扣除证明材料（若有）
（3）技术商务部分
①标的说明一览表
②技术和服务要求响应表
③商务条件响应表
10.3电子投标文件的语言
（1）除招标文件另有规定外，电子投标文件应使用中文文本。
"""
        result = parse_submission_format_tree(text)

        self.assertEqual(result["confidence"], "explicit")
        self.assertEqual(result["structureStatus"], "valid")
        self.assertEqual(
            [item["title"] for item in result["volumes"]],
            ["资格及资信证明部分", "报价部分", "技术商务部分"],
        )
        self.assertEqual(
            [item["title"] for item in result["volumes"][2]["items"]],
            ["标的说明一览表", "技术和服务要求响应表", "商务条件响应表"],
        )
        self.assertTrue(is_reliable_submission_structure(result))

    def test_qualification_sentences_are_not_volumes(self) -> None:
        text = """
注：根据第四章资格审查要求，投标人根据投标文件格式二-2-1提供证明材料。
一、我单位(本人)具备采购文件要求以及《中华人民共和国政府采购法》第二十二条规定的条件:
1.具有独立承担民事责任的能力;
2.具有良好的商业信誉和健全的财务会计制度;
3.具有履行合同所必需的设备和专业技术能力;
"""
        result = parse_submission_format_tree(text)

        self.assertEqual(result["confidence"], "unknown")
        self.assertEqual(result["volumes"], [])
        self.assertFalse(is_reliable_submission_structure(result))

    def test_stale_invalid_structure_is_removed_during_normalization(self) -> None:
        result = normalize_submission_structure(
            {
                "confidence": "explicit",
                "sourceLocator": "资格审查",
                "volumes": [
                    {
                        "volumeId": "bad",
                        "title": "具有履行合同所必需的设备和专业技术能力;",
                    },
                    {"volumeId": "good", "title": "技术商务部分"},
                ],
            }
        )

        self.assertEqual([item["title"] for item in result["volumes"]], ["技术商务部分"])
        self.assertEqual(result["confidence"], "explicit")
        self.assertTrue(result["hasExplicitFormat"])
        self.assertTrue(any("疑似非目录卷标题" in item for item in result["warnings"]))

    def test_build_parse_bundle_prefers_reliable_derived_structure(self) -> None:
        text = """
10.2电子投标文件由下述部分组成：
（1）资格及资信证明部分
①投标函
（2）报价部分
①开标（报价）一览表
（3）技术商务部分
①技术和服务要求响应表
10.3电子投标文件的语言
"""
        bundle = build_parse_bundle(
            result={
                "text": text,
                "submissionStructure": {
                    "confidence": "explicit",
                    "sourceLocator": "资格审查",
                    "volumes": [
                        {
                            "volumeId": "bad",
                            "title": "具有独立承担民事责任的能力;",
                        }
                    ],
                },
            }
        )

        self.assertEqual(
            [item["title"] for item in bundle["submissionStructure"]["volumes"]],
            ["资格及资信证明部分", "报价部分", "技术商务部分"],
        )

    def test_minimal_fallback_does_not_expand_requirement_details(self) -> None:
        plan = minimal_bid_skeleton()

        self.assertEqual([item["title"] for item in plan if item["level"] == 1], ["投标响应文件"])
        self.assertEqual(
            [item["title"] for item in plan if item["level"] == 2],
            ["资格及资信证明文件", "报价文件", "技术商务响应文件"],
        )
        self.assertEqual(max(item["level"] for item in plan), 2)

    def test_outline_quality_rejects_sentence_like_volume(self) -> None:
        issues = _outline_quality_issues(
            [
                {
                    "sectionId": "bad-volume",
                    "title": "具有独立承担民事责任的能力;",
                    "nodeType": "volume",
                    "responseType": "section_group",
                    "level": 1,
                },
                {
                    "sectionId": "child",
                    "title": "技术商务响应",
                    "parentSectionId": "bad-volume",
                    "level": 2,
                },
            ]
        )

        self.assertTrue(any("顶层卷标题疑似" in item for item in issues))


if __name__ == "__main__":
    unittest.main()
