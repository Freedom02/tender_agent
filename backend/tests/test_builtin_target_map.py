from __future__ import annotations

import json
import re
import unittest
from unittest.mock import patch

from backend.app.tender_matching.builtin_target_map import (
    TARGET_KEYS,
    _build_indexes,
    discover_target_ranges,
    _llm_review_ranges,
)


def _segments() -> list[dict[str, str]]:
    return [
        {"id": "SEG-001", "sourceLocator": "第1页", "text": "一、项目需求"},
        {"id": "SEG-002", "sourceLocator": "第1页", "text": "技术和服务要求"},
        {
            "id": "SEG-003",
            "sourceLocator": "第1页",
            "text": "平台应支持三维场景加载，数量：2套。",
        },
        {"id": "SEG-004", "sourceLocator": "第2页", "text": "商务条件"},
        {
            "id": "SEG-005",
            "sourceLocator": "第2页",
            "text": "交付期为合同签订后30日，投标有效期为90日。",
        },
        {"id": "SEG-006", "sourceLocator": "第3页", "text": "投标文件格式"},
        {"id": "SEG-007", "sourceLocator": "第3页", "text": "投标函应按本格式填写并盖章。"},
        {"id": "SEG-008", "sourceLocator": "第4页", "text": "未按要求递交的，投标无效。"},
    ]


class BuiltinTargetMapTests(unittest.TestCase):
    def test_rule_recall_splits_a_mixed_parent_chapter(self) -> None:
        result = discover_target_ranges(
            segments=_segments(),
            sections=[
                {
                    "sectionId": "SEC-001",
                    "heading": "一、项目需求",
                    "sectionPath": ["一、项目需求"],
                    "segmentIds": [item["id"] for item in _segments()],
                }
            ],
            tables=[],
            ai={"enabled": False},
        )
        targets = {
            item["targetModule"]: item
            for item in result["targets"]
        }
        self.assertEqual(set(targets), set(TARGET_KEYS))
        self.assertEqual(targets["technical"]["selectionSource"], "rule")
        self.assertEqual(targets["commercial"]["selectionSource"], "rule")
        self.assertIn("SEG-003", targets["technical"]["segmentIds"])
        self.assertIn("SEG-005", targets["commercial"]["segmentIds"])
        self.assertIn("SEG-007", targets["template"]["segmentIds"])
        self.assertNotIn("SEG-005", targets["technical"]["segmentIds"])
        self.assertNotIn("SEG-007", targets["technical"]["segmentIds"])
        self.assertNotIn("SEG-003", targets["commercial"]["segmentIds"])
        self.assertNotIn("SEG-003", targets["template"]["segmentIds"])
        self.assertNotIn("SEG-008", targets["commercial"]["segmentIds"])
        self.assertEqual(targets["rejection"]["segmentIds"], ["SEG-008"])

    def test_pure_section_is_protected_from_llm_and_keeps_low_keyword_body(self) -> None:
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
        with patch(
            "backend.app.tender_matching.builtin_target_map.call_openai_compatible",
            side_effect=AssertionError("pure section must not call LLM"),
        ) as mocked_call:
            result = discover_target_ranges(
                segments=segments,
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
                ai={
                    "enabled": True,
                    "baseUrl": "http://llm.local/v1",
                    "apiKey": "test",
                    "model": "test-model",
                },
            )

        technical = next(
            item
            for item in result["targets"]
            if item["targetModule"] == "technical"
        )
        self.assertEqual(mocked_call.call_count, 0)
        self.assertEqual(
            technical["segmentIds"],
            ["SEG-TECH-HEADING", "SEG-TECH-LOW"],
        )
        self.assertEqual(technical["expandedSectionIds"], ["SEC-TECH"])
        self.assertEqual(
            technical["expandedSegmentIds"],
            ["SEG-TECH-HEADING", "SEG-TECH-LOW"],
        )
        self.assertEqual(technical["reviewSegmentIds"], [])
        self.assertNotIn("reviewedByLlm", technical)

    def test_failed_llm_keeps_rule_source_and_ranges(self) -> None:
        with patch(
            "backend.app.tender_matching.builtin_target_map.call_openai_compatible",
            return_value={
                "success": False,
                "error": "timeout",
            },
        ):
            result = discover_target_ranges(
                segments=_segments(),
                sections=[
                    {
                        "sectionId": "SEC-001",
                        "heading": "一、项目需求",
                        "sectionPath": ["一、项目需求"],
                        "segmentIds": [item["id"] for item in _segments()],
                    }
                ],
                tables=[],
                ai={
                    "enabled": True,
                    "baseUrl": "http://llm.local/v1",
                    "apiKey": "test",
                    "model": "test-model",
                },
            )
        targets = {
            item["targetModule"]: item
            for item in result["targets"]
        }
        self.assertEqual(targets["technical"]["selectionSource"], "rule")
        self.assertIn("SEG-003", targets["technical"]["segmentIds"])
        self.assertFalse(result["diagnostics"]["llmSuccess"])

    def test_llm_review_is_limited_to_rule_candidates_and_real_ids(self) -> None:
        def fake_call(_config, messages, **_kwargs):
            user_text = str(messages[-1].get("content") or "")
            marker = "目标 technical 待复核候选"
            if marker not in user_text:
                return {
                    "success": True,
                    "content": '{"targets":[]}',
                    "finishReason": "stop",
                }
            candidate_ids = re.findall(r"\[(SEG-\d+)\]", user_text)
            keep_ids = [
                value
                for value in candidate_ids
                if value == "SEG-003"
            ]
            exclude_ids = [
                value
                for value in candidate_ids
                if value != "SEG-003"
            ]
            return {
                "success": True,
                "content": json.dumps(
                    {
                        "targets": [
                            {
                                "targetModule": "technical",
                                "keepSegmentIds": keep_ids,
                                "excludeSegmentIds": exclude_ids,
                                "confidence": 0.91,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "finishReason": "stop",
            }

        with patch(
            "backend.app.tender_matching.builtin_target_map.call_openai_compatible",
            side_effect=fake_call,
        ):
            result = discover_target_ranges(
                segments=_segments(),
                sections=[
                    {
                        "sectionId": "SEC-001",
                        "heading": "一、项目需求",
                        "sectionPath": ["一、项目需求"],
                        "segmentIds": [item["id"] for item in _segments()],
                    }
                ],
                tables=[],
                ai={
                    "enabled": True,
                    "baseUrl": "http://llm.local/v1",
                    "apiKey": "test",
                    "model": "test-model",
                },
            )
        targets = {
            item["targetModule"]: item
            for item in result["targets"]
        }
        self.assertEqual(
            targets["technical"]["selectionSource"],
            "rule+review",
        )
        self.assertEqual(targets["technical"]["segmentIds"], ["SEG-003"])
        self.assertNotIn("FORGED", targets["technical"]["segmentIds"])
        self.assertTrue(result["diagnostics"]["llmSuccess"])
        self.assertTrue(result["diagnostics"]["llmReviewCalled"])
        self.assertTrue(result["diagnostics"]["llmReviewSuccess"])

    def test_llm_review_removes_mixed_segment_but_keeps_pure_range(self) -> None:
        segments = [
            {
                "id": "SEG-TECH",
                "sourceLocator": "第1页",
                "text": "平台应支持三维场景加载，满足技术参数要求。",
            },
            {
                "id": "SEG-COMM",
                "sourceLocator": "第1页",
                "text": "交付期为合同签订后30日，投标有效期为90日。",
            },
            {
                "id": "SEG-LOW",
                "sourceLocator": "第1页",
                "text": "系统提供完整部署成果和操作说明。",
            },
        ]
        discovery_response = {
            "success": True,
            "content": (
                '{"targets":['
                '{"targetModule":"technical","sectionIds":["SEC-MIXED"],'
                '"segmentIds":["SEG-TECH","SEG-COMM"],"confidence":0.82}'
                "]}"
            ),
            "finishReason": "stop",
        }
        review_response = {
            "success": True,
            "content": (
                '{"targets":['
                '{"targetModule":"technical",'
                '"keepSegmentIds":["SEG-TECH","SEG-LOW"],'
                '"excludeSegmentIds":["SEG-COMM"],'
                '"confidence":0.96,'
                '"reason":"技术要求保留，交付期属于商务条件"}'
                "]}"
            ),
            "finishReason": "stop",
        }

        def fake_call(_config, messages, **_kwargs):
            user_text = str(messages[-1].get("content") or "")
            if "待复核候选" in user_text:
                if "目标 technical 待复核候选" in user_text:
                    return review_response
                return {
                    "success": True,
                    "content": '{"targets":[]}',
                    "finishReason": "stop",
                }
            return discovery_response

        with patch(
            "backend.app.tender_matching.builtin_target_map.call_openai_compatible",
            side_effect=fake_call,
        ) as mocked_call:
            result = discover_target_ranges(
                segments=segments,
                sections=[
                    {
                        "sectionId": "SEC-MIXED",
                        "heading": "第五章 招标内容及要求",
                        "isMixed": True,
                        "sectionPath": ["第五章 技术和服务要求"],
                        "segmentIds": ["SEG-TECH", "SEG-COMM", "SEG-LOW"],
                    }
                ],
                tables=[],
                ai={
                    "enabled": True,
                    "baseUrl": "http://llm.local/v1",
                    "apiKey": "test",
                    "model": "test-model",
                },
            )

        targets = {
            item["targetModule"]: item
            for item in result["targets"]
        }
        self.assertGreaterEqual(mocked_call.call_count, 2)
        self.assertEqual(
            targets["technical"]["segmentIds"],
            ["SEG-TECH", "SEG-LOW"],
        )
        self.assertEqual(
            targets["technical"]["llmExcludedSegmentIds"],
            ["SEG-COMM"],
        )
        self.assertEqual(
            targets["technical"]["selectionSource"],
            "rule+review",
        )

    def test_llm_review_splits_a_timed_out_batch_and_records_budget(self) -> None:
        segments = [
            {
                "id": f"SEG-TECH-{index}",
                "sourceLocator": "第1页",
                "text": f"平台应支持技术参数 {index}，满足功能要求。",
            }
            for index in range(6)
        ]
        indexes = _build_indexes(
            segments,
            [
                {
                    "sectionId": "SEC-MIXED",
                    "heading": "第五章 招标内容及要求",
                    "isMixed": True,
                    "sectionPath": ["第五章 招标内容及要求"],
                    "segmentIds": [item["id"] for item in segments],
                }
            ],
            [],
        )
        selection = {
            "technical": {
                "segmentIds": [item["id"] for item in segments],
                "reviewSegmentIds": [item["id"] for item in segments],
            }
        }

        def fake_call(_config, messages, **_kwargs):
            user_text = str(messages[-1].get("content") or "")
            candidate_ids = re.findall(r"\[(SEG-TECH-\d+)\]", user_text)
            if len(candidate_ids) > 4:
                return {
                    "success": False,
                    "error": "模型服务响应超时（30 秒）",
                }
            return {
                "success": True,
                "content": json.dumps(
                    {
                        "targets": [
                            {
                                "targetModule": "technical",
                                "keepSegmentIds": candidate_ids,
                                "excludeSegmentIds": [],
                                "confidence": 0.9,
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                "finishReason": "stop",
            }

        diagnostics: dict[str, object] = {}
        with patch(
            "backend.app.tender_matching.builtin_target_map.call_openai_compatible",
            side_effect=fake_call,
        ):
            reviewed = _llm_review_ranges(
                selection,
                indexes,
                {
                    "enabled": True,
                    "baseUrl": "http://llm.local/v1",
                    "apiKey": "test",
                    "model": "test-model",
                    "targetReviewBatchSize": 64,
                    "targetReviewMaxInputChars": 60000,
                    "targetReviewMaxAttempts": 1,
                },
                diagnostics,
            )

        self.assertEqual(
            reviewed["technical"]["reviewedSegmentIds"],
            [item["id"] for item in segments],
        )
        self.assertGreaterEqual(int(diagnostics["llmReviewSplitCount"]), 1)
        self.assertEqual(diagnostics["llmReviewFallbackToRule"], False)
        self.assertTrue(diagnostics["llmReviewBatches"])
        self.assertTrue(
            any(
                item.get("action") == "split"
                for item in diagnostics["llmReviewBatches"]
            )
        )
        self.assertLessEqual(
            int(diagnostics["llmReviewLimits"]["maxInputChars"]),
            60000,
        )

    def test_llm_review_falls_back_to_rule_after_split_limit(self) -> None:
        segments = [
            {
                "id": f"SEG-TECH-{index}",
                "sourceLocator": "第1页",
                "text": f"平台应支持技术参数 {index}，满足功能要求。",
            }
            for index in range(6)
        ]
        indexes = _build_indexes(
            segments,
            [
                {
                    "sectionId": "SEC-MIXED",
                    "heading": "第五章 招标内容及要求",
                    "isMixed": True,
                    "sectionPath": ["第五章 招标内容及要求"],
                    "segmentIds": [item["id"] for item in segments],
                }
            ],
            [],
        )
        selection = {
            "technical": {
                "segmentIds": [item["id"] for item in segments],
                "reviewSegmentIds": [item["id"] for item in segments],
            }
        }
        diagnostics: dict[str, object] = {}
        with patch(
            "backend.app.tender_matching.builtin_target_map.call_openai_compatible",
            return_value={
                "success": False,
                "error": "timeout",
            },
        ):
            reviewed = _llm_review_ranges(
                selection,
                indexes,
                {
                    "enabled": True,
                    "baseUrl": "http://llm.local/v1",
                    "apiKey": "test",
                    "model": "test-model",
                    "targetReviewBatchSize": 64,
                    "targetReviewMaxInputChars": 60000,
                    "targetReviewMaxAttempts": 1,
                    "targetReviewMaxSplitDepth": 1,
                },
                diagnostics,
            )

        self.assertEqual(reviewed, {})
        self.assertTrue(diagnostics["llmReviewFallbackToRule"])
        self.assertEqual(
            diagnostics["llmReviewFallbackSegmentCount"],
            len(segments),
        )
        self.assertFalse(diagnostics["llmReviewSuccess"])

    def test_pure_and_mixed_ranges_for_same_target_are_merged_safely(self) -> None:
        segments = [
            {
                "id": "SEG-PURE-LOW",
                "sourceLocator": "第1页",
                "text": "系统应稳定运行并完成项目实施。",
            },
            {
                "id": "SEG-MIX-TECH",
                "sourceLocator": "第2页",
                "text": "平台应支持三维场景加载。",
            },
            {
                "id": "SEG-MIX-COMM",
                "sourceLocator": "第2页",
                "text": "交付期为合同签订后30日。",
            },
        ]
        discovery_response = {
            "success": True,
            "content": (
                '{"targets":['
                '{"targetModule":"technical","sectionIds":["SEC-MIX"],'
                '"segmentIds":["SEG-MIX-TECH"],"confidence":0.9}'
                "]}"
            ),
            "finishReason": "stop",
        }
        review_response = {
            "success": True,
            "content": (
                '{"targets":['
                '{"targetModule":"technical",'
                '"keepSegmentIds":["SEG-MIX-TECH"],'
                '"excludeSegmentIds":["SEG-MIX-COMM"],'
                '"confidence":0.95}'
                "]}"
            ),
            "finishReason": "stop",
        }

        def fake_call(_config, messages, **_kwargs):
            user_text = str(messages[-1].get("content") or "")
            if "待复核候选" in user_text:
                if "目标 technical 待复核候选" in user_text:
                    return review_response
                return {
                    "success": True,
                    "content": '{"targets":[]}',
                    "finishReason": "stop",
                }
            return discovery_response

        with patch(
            "backend.app.tender_matching.builtin_target_map.call_openai_compatible",
            side_effect=fake_call,
        ):
            result = discover_target_ranges(
                segments=segments,
                sections=[
                    {
                        "sectionId": "SEC-PURE",
                        "heading": "技术要求",
                        "sectionType": "technical",
                        "sectionTypeHits": ["technical"],
                        "sectionPath": ["技术要求"],
                        "segmentIds": ["SEG-PURE-LOW"],
                    },
                    {
                        "sectionId": "SEC-MIX",
                        "heading": "第五章 招标内容及要求",
                        "isMixed": True,
                        "sectionPath": ["第五章 招标内容及要求"],
                        "segmentIds": [
                            "SEG-MIX-TECH",
                            "SEG-MIX-COMM",
                        ],
                    },
                ],
                tables=[],
                ai={
                    "enabled": True,
                    "baseUrl": "http://llm.local/v1",
                    "apiKey": "test",
                    "model": "test-model",
                },
            )

        technical = next(
            item
            for item in result["targets"]
            if item["targetModule"] == "technical"
        )
        self.assertEqual(
            technical["segmentIds"],
            ["SEG-PURE-LOW", "SEG-MIX-TECH"],
        )
        self.assertIn("SEC-PURE", technical["expandedSectionIds"])
        self.assertNotIn("SEG-MIX-COMM", technical["segmentIds"])

    def test_rejection_regex_is_not_capped_at_500_segments(self) -> None:
        segments = [
            {
                "id": f"SEG-REJECT-{index:04d}",
                "sourceLocator": f"第{index + 1}页",
                "text": f"第{index + 1}项未按要求递交的，投标无效。",
            }
            for index in range(501)
        ]
        result = discover_target_ranges(
            segments=segments,
            sections=[],
            tables=[],
            ai={"enabled": False},
        )
        rejection = next(
            item
            for item in result["targets"]
            if item["targetModule"] == "rejection"
        )
        self.assertEqual(len(rejection["segmentIds"]), 501)

    def test_mixed_sections_keep_scoring_and_templates_at_segment_level(self) -> None:
        segments = [
            {"id": "SEG-TECH", "sourceLocator": "第1页", "text": "平台应支持三维场景加载。"},
            {"id": "SEG-COMM", "sourceLocator": "第2页", "text": "交付期为合同签订后30日。"},
            {"id": "SEG-SCORE", "sourceLocator": "第3页", "text": "技术评分满分20分，按响应情况计分。"},
            {"id": "SEG-PROCESS", "sourceLocator": "第3页", "text": "评标委员会负责组织评审。"},
            {"id": "SEG-FORM", "sourceLocator": "第4页", "text": "投标函应按本格式填写并签字盖章。"},
            {"id": "SEG-PRODUCT-TEMPLATE", "sourceLocator": "第5页", "text": "系统内置场景模板库和效果模板。"},
            {"id": "SEG-CONTRACT", "sourceLocator": "第6页", "text": "甲方、乙方应按照合同约定履行义务。"},
        ]
        all_ids = [item["id"] for item in segments]
        sections = [
            {
                "sectionId": "SEC-MIXED",
                "heading": "第五章 招标内容及要求",
                "sectionPath": ["第五章 招标内容及要求"],
                "segmentIds": all_ids,
            },
            {
                "sectionId": "SEC-TECH",
                "heading": "技术要求",
                "sectionType": "technical",
                "sectionTypeHits": ["technical"],
                "parentId": "SEC-MIXED",
                "sectionPath": ["第五章 招标内容及要求", "技术要求"],
                "segmentIds": ["SEG-TECH", "SEG-PRODUCT-TEMPLATE"],
            },
            {
                "sectionId": "SEC-COMM",
                "heading": "商务要求",
                "sectionType": "commercial",
                "sectionTypeHits": ["commercial"],
                "parentId": "SEC-MIXED",
                "sectionPath": ["第五章 招标内容及要求", "商务要求"],
                "segmentIds": ["SEG-COMM"],
            },
            {
                "sectionId": "SEC-EVAL",
                "heading": "评标办法",
                "sectionType": "evaluation",
                "sectionTypeHits": ["evaluation"],
                "parentId": "SEC-MIXED",
                "sectionPath": ["第五章 招标内容及要求", "评标办法"],
                "segmentIds": ["SEG-SCORE", "SEG-PROCESS"],
            },
            {
                "sectionId": "SEC-FORM",
                "heading": "投标文件格式",
                "sectionType": "document_format",
                "sectionTypeHits": ["document_format"],
                "parentId": "SEC-MIXED",
                "sectionPath": ["第五章 招标内容及要求", "投标文件格式"],
                "segmentIds": ["SEG-FORM"],
            },
            {
                "sectionId": "SEC-CONTRACT",
                "heading": "政府采购合同",
                "sectionType": "contract",
                "sectionTypeHits": ["contract"],
                "parentId": "SEC-MIXED",
                "sectionPath": ["第五章 招标内容及要求", "政府采购合同"],
                "segmentIds": ["SEG-CONTRACT"],
            },
        ]
        result = discover_target_ranges(
            segments=segments,
            sections=sections,
            tables=[],
            ai={"enabled": False},
        )
        targets = {
            item["targetModule"]: item
            for item in result["targets"]
        }

        self.assertIn("SEG-TECH", targets["technical"]["segmentIds"])
        self.assertEqual(targets["commercial"]["segmentIds"], ["SEG-COMM"])
        self.assertIn("SEG-SCORE", targets["evaluation"]["segmentIds"])
        self.assertNotIn("SEG-PROCESS", targets["evaluation"]["segmentIds"])
        self.assertIn("SEG-FORM", targets["template"]["segmentIds"])
        self.assertNotIn(
            "SEG-PRODUCT-TEMPLATE",
            targets["template"]["segmentIds"],
        )
        self.assertIn("SEG-CONTRACT", targets["contract"]["segmentIds"])

    def test_unknown_parent_chapter_splits_typed_child_modules_without_fixed_numbering(
        self,
    ) -> None:
        segments = [
            {
                "id": "SEG-DYNAMIC-TECH",
                "sourceLocator": "page-1",
                "text": "\u7cfb\u7edf\u5e94\u652f\u6301\u4e09\u7ef4\u573a\u666f\u52a0\u8f7d\uff0c\u6280\u672f\u53c2\u6570\u6ee1\u8db3\u8981\u6c42\u3002",
            },
            {
                "id": "SEG-DYNAMIC-QUAL",
                "sourceLocator": "page-2",
                "text": "\u6295\u6807\u4eba\u5e94\u63d0\u4f9b\u8425\u4e1a\u6267\u7167\u548c\u9879\u76ee\u4e1a\u7ee9\u3002",
            },
            {
                "id": "SEG-DYNAMIC-COMM",
                "sourceLocator": "page-3",
                "text": "\u4ea4\u4ed8\u671f\u9650\u4e3a\u5408\u540c\u7b7e\u8ba2\u540e30\u65e5\u3002",
            },
            {
                "id": "SEG-DYNAMIC-EVAL",
                "sourceLocator": "page-4",
                "text": "\u8bc4\u5206\u6807\u51c6\uff1a\u6309\u54cd\u5e94\u60c5\u51b5\u8ba1\u5206\u3002",
            },
            {
                "id": "SEG-DYNAMIC-TEMPLATE",
                "sourceLocator": "page-5",
                "text": "\u8bf7\u6309\u54cd\u5e94\u8868\u683c\u5f0f\u586b\u5199\u5e76\u7b7e\u5b57\u76d6\u7ae0\u3002",
            },
            {
                "id": "SEG-DYNAMIC-CONTRACT",
                "sourceLocator": "page-6",
                "text": "\u5408\u540c\u4ef7\u6b3e\u53ca\u8fdd\u7ea6\u8d23\u4efb\u6309\u7167\u5408\u540c\u7ea6\u5b9a\u6267\u884c\u3002",
            },
        ]
        all_ids = [item["id"] for item in segments]
        sections = [
            {
                "sectionId": "SEC-DYNAMIC-ROOT",
                "heading": "\u9879\u76ee\u5b9e\u65bd\u4e0e\u54cd\u5e94\u5b89\u6392",
                "level": 1,
                "sectionType": "content",
                "sectionTypeHits": [],
                "sectionPath": ["\u9879\u76ee\u5b9e\u65bd\u4e0e\u54cd\u5e94\u5b89\u6392"],
                "segmentIds": all_ids,
            },
            {
                "sectionId": "SEC-DYNAMIC-TECH",
                "heading": "\u6a21\u5757A",
                "level": 2,
                "sectionType": "technical",
                "sectionTypeHits": ["technical"],
                "parentId": "SEC-DYNAMIC-ROOT",
                "sectionPath": ["\u9879\u76ee\u5b9e\u65bd\u4e0e\u54cd\u5e94\u5b89\u6392", "\u6a21\u5757A"],
                "segmentIds": ["SEG-DYNAMIC-TECH"],
            },
            {
                "sectionId": "SEC-DYNAMIC-QUAL",
                "heading": "\u6a21\u5757B",
                "level": 2,
                "sectionType": "qualification",
                "sectionTypeHits": ["qualification"],
                "parentId": "SEC-DYNAMIC-ROOT",
                "sectionPath": ["\u9879\u76ee\u5b9e\u65bd\u4e0e\u54cd\u5e94\u5b89\u6392", "\u6a21\u5757B"],
                "segmentIds": ["SEG-DYNAMIC-QUAL"],
            },
            {
                "sectionId": "SEC-DYNAMIC-COMM",
                "heading": "\u6a21\u5757C",
                "level": 2,
                "sectionType": "commercial",
                "sectionTypeHits": ["commercial"],
                "parentId": "SEC-DYNAMIC-ROOT",
                "sectionPath": ["\u9879\u76ee\u5b9e\u65bd\u4e0e\u54cd\u5e94\u5b89\u6392", "\u6a21\u5757C"],
                "segmentIds": ["SEG-DYNAMIC-COMM"],
            },
            {
                "sectionId": "SEC-DYNAMIC-EVAL",
                "heading": "\u6a21\u5757D",
                "level": 2,
                "sectionType": "evaluation",
                "sectionTypeHits": ["evaluation"],
                "parentId": "SEC-DYNAMIC-ROOT",
                "sectionPath": ["\u9879\u76ee\u5b9e\u65bd\u4e0e\u54cd\u5e94\u5b89\u6392", "\u6a21\u5757D"],
                "segmentIds": ["SEG-DYNAMIC-EVAL"],
            },
            {
                "sectionId": "SEC-DYNAMIC-TEMPLATE",
                "heading": "\u6a21\u5757E",
                "level": 2,
                "sectionType": "document_format",
                "sectionTypeHits": ["document_format"],
                "parentId": "SEC-DYNAMIC-ROOT",
                "sectionPath": ["\u9879\u76ee\u5b9e\u65bd\u4e0e\u54cd\u5e94\u5b89\u6392", "\u6a21\u5757E"],
                "segmentIds": ["SEG-DYNAMIC-TEMPLATE"],
            },
            {
                "sectionId": "SEC-DYNAMIC-CONTRACT",
                "heading": "\u6a21\u5757F",
                "level": 2,
                "sectionType": "contract",
                "sectionTypeHits": ["contract"],
                "parentId": "SEC-DYNAMIC-ROOT",
                "sectionPath": ["\u9879\u76ee\u5b9e\u65bd\u4e0e\u54cd\u5e94\u5b89\u6392", "\u6a21\u5757F"],
                "segmentIds": ["SEG-DYNAMIC-CONTRACT"],
            },
        ]
        result = discover_target_ranges(
            segments=segments,
            sections=sections,
            tables=[],
            ai={"enabled": False},
        )
        targets = {
            item["targetModule"]: item
            for item in result["targets"]
        }

        self.assertEqual(
            targets["technical"]["segmentIds"],
            ["SEG-DYNAMIC-TECH"],
        )
        self.assertEqual(
            targets["qualification"]["segmentIds"],
            ["SEG-DYNAMIC-QUAL"],
        )
        self.assertEqual(
            targets["commercial"]["segmentIds"],
            ["SEG-DYNAMIC-COMM"],
        )
        self.assertEqual(
            targets["evaluation"]["segmentIds"],
            ["SEG-DYNAMIC-EVAL"],
        )
        self.assertEqual(
            targets["template"]["segmentIds"],
            ["SEG-DYNAMIC-TEMPLATE"],
        )
        self.assertEqual(
            targets["contract"]["segmentIds"],
            ["SEG-DYNAMIC-CONTRACT"],
        )
        self.assertNotIn(
            "SEG-DYNAMIC-COMM",
            targets["technical"]["segmentIds"],
        )
        self.assertNotIn(
            "SEG-DYNAMIC-TEMPLATE",
            targets["qualification"]["segmentIds"],
        )

    def test_standalone_contract_heading_overrides_document_format_type(self) -> None:
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
            {
                "id": "SEG-CONTRACT-REFERENCE",
                "sourceLocator": "第2页",
                "text": "中标后无正当理由拒不与采购人签订政府采购合同。",
            },
        ]
        result = discover_target_ranges(
            segments=segments,
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
                },
                {
                    "sectionId": "SEC-REFERENCE",
                    "heading": "中标后无正当理由拒不与采购人签订政府采购合同",
                    "sectionType": "contract",
                    "sectionTypeHits": ["contract"],
                    "sectionPath": [
                        "第二章 投标人须知",
                        "中标后无正当理由拒不与采购人签订政府采购合同",
                    ],
                    "segmentIds": ["SEG-CONTRACT-REFERENCE"],
                },
            ],
            tables=[],
            ai={"enabled": False},
        )
        targets = {
            item["targetModule"]: item
            for item in result["targets"]
        }

        self.assertIn(
            "SEG-CONTRACT-TITLE",
            targets["contract"]["segmentIds"],
        )
        self.assertIn(
            "SEG-CONTRACT-BODY",
            targets["contract"]["segmentIds"],
        )
        self.assertNotIn(
            "SEG-CONTRACT-REFERENCE",
            targets["contract"]["segmentIds"],
        )
        self.assertNotIn(
            "SEG-CONTRACT-TITLE",
            targets["template"]["segmentIds"],
        )


if __name__ == "__main__":
    unittest.main()
