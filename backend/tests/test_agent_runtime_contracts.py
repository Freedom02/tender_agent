# -*- coding: utf-8 -*-
"""Wave 0 · 0.1 契约测试：AgentSpec 登记表、容错校验、运行态与 BidSection 保真。

这些测试的作用不是「覆盖代码」，而是把并行改造的公共前提钉死：
  - 登记表与真实代码不许漂移（client 方法、提示词函数、工具白名单、编排层 agent_id）；
  - 校验必须容错（未知键不是错），但该报的错必须报成 schema_invalid；
  - 幂等复用的判据必须严格（摘要不一致就不许复用）；
  - 读模型往返不得改变任何字段（历史 bug 的教训：归一化顺手把标题/字段改了）。
"""
from __future__ import annotations

import inspect
import re
import unittest
from pathlib import Path

from backend.app.agent_runtime import (
    AGENT_SPECS,
    ARTIFACT_KINDS,
    WORKFLOW_TEMPLATES,
    ArtifactStatus,
    ArtifactShape,
    BidSectionModel,
    FailureKind,
    NodeRun,
    NodeStatus,
    RunState,
    ValidationReport,
    get_spec,
    idempotency_key,
    input_digest,
    missing_required_fields,
    normalize_agent_id,
    resolve_list,
    resolve_spec,
    round_trip,
    shape_for,
    should_reuse,
    spec_ids,
    spec_summary,
    validate_payload,
)
from backend.app.pi_agent import prompts as prompt_module
from backend.app.pi_agent.client import PiClient
from backend.app.pi_agent.outline_agent_context import OUTLINE_TOOL_ALLOWLIST

ROOT = Path(__file__).resolve().parents[2]


class AgentSpecRegistryTests(unittest.TestCase):
    def test_agent_ids_unique(self) -> None:
        self.assertEqual(len(spec_ids()), len(set(spec_ids())))

    def test_every_spec_points_at_a_real_client_method(self) -> None:
        missing = [
            (spec.agent_id, spec.client_method)
            for spec in AGENT_SPECS
            if not callable(getattr(PiClient, spec.client_method, None))
        ]
        self.assertEqual(missing, [], f"登记的 client 方法不存在：{missing}")

    def test_every_spec_points_at_a_real_prompt_builder(self) -> None:
        missing = [
            (spec.agent_id, spec.prompt_builder)
            for spec in AGENT_SPECS
            if not callable(getattr(prompt_module, spec.prompt_builder, None))
        ]
        self.assertEqual(missing, [], f"登记的提示词函数不存在：{missing}")

    def test_workflow_and_artifact_kind_are_known(self) -> None:
        for spec in AGENT_SPECS:
            self.assertIn(spec.workflow, WORKFLOW_TEMPLATES, spec.agent_id)
            self.assertIn(spec.artifact_kind, ARTIFACT_KINDS, spec.agent_id)

    def test_tools_are_inside_the_allowlist(self) -> None:
        for spec in AGENT_SPECS:
            unknown = [tool for tool in spec.tools if tool not in OUTLINE_TOOL_ALLOWLIST]
            self.assertEqual(unknown, [], f"{spec.agent_id} 挂了未放行的工具：{unknown}")

    def test_runtime_labels_resolve_to_base_spec(self) -> None:
        self.assertEqual(normalize_agent_id("RequirementMapAgent[1/3]"), "RequirementMapAgent")
        self.assertEqual(normalize_agent_id("OutlinePlanAgent"), "OutlinePlanAgent")
        self.assertEqual(
            resolve_spec("RequirementMapAgent[2/2]").agent_id, "RequirementMapAgent"
        )
        self.assertEqual(resolve_spec("OutlinePlanAgent[质量修正]").agent_id, "OutlinePlanAgent")
        self.assertIsNone(resolve_spec("NotAnAgent"))
        with self.assertRaises(KeyError):
            get_spec("NotAnAgent")

    def test_every_agent_id_used_in_code_is_registered(self) -> None:
        """漂移护栏：编排层新起一个 agent_id 而忘记登记，这里就会失败。"""

        sources = [
            ROOT / "backend" / "app" / "pi_agent" / "orchestrator.py",
            ROOT / "backend" / "app" / "pi_agent" / "writing_orchestrator.py",
            ROOT / "backend" / "app" / "store.py",
        ]
        pattern = re.compile(
            r"""agent_id\s*=\s*["']([^"']+)["']|run_agent\(\s*["']([^"']+)["']"""
        )
        found: set[str] = set()
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                found.add(match.group(1) or match.group(2))
        self.assertTrue(found, "没扫到任何 agent_id，正则或文件路径已失效")
        unregistered = sorted(
            name for name in found if resolve_spec(name) is None
        )
        self.assertEqual(unregistered, [], f"编排层用到但未登记的智能体：{unregistered}")

    def test_spec_summary_is_serializable(self) -> None:
        summary = spec_summary()
        self.assertEqual(len(summary), len(AGENT_SPECS))
        self.assertTrue(all(item["agentId"] for item in summary))


class ValidationTests(unittest.TestCase):
    def test_unknown_keys_are_tolerated(self) -> None:
        shape = ArtifactShape(list_keys=("plan",), item_fields=("title",))
        report = validate_payload(
            {"plan": [{"title": "技术方案", "extra": 1, "derived": True}], "surprise": "ok"},
            shape,
        )
        self.assertTrue(report.ok, report.messages())
        self.assertEqual(report.issues, [])

    def test_wrong_list_type_is_schema_invalid(self) -> None:
        shape = ArtifactShape(list_keys=("plan", "outline"), item_fields=("title",))
        report = validate_payload({"plan": "不是数组"}, shape)
        self.assertFalse(report.ok)
        self.assertEqual(report.errors[0].code, "schema_invalid")
        self.assertEqual(report.errors[0].path, "plan")

    def test_missing_list_is_reported_when_required(self) -> None:
        shape = ArtifactShape(list_keys=("plan", "outline"), item_fields=("title",))
        self.assertFalse(validate_payload({"plan": []} if False else {"other": 1}, shape).ok)
        optional = ArtifactShape(
            list_keys=("plan", "outline"), item_fields=("title",), list_required=False
        )
        self.assertTrue(validate_payload({"other": 1}, optional).ok)

    def test_non_object_payload_is_schema_invalid(self) -> None:
        report = validate_payload(["技术方案"], ArtifactShape(list_keys=("plan",)))
        self.assertFalse(report.ok)
        self.assertIn("应为对象", report.errors[0].message)

    def test_item_field_and_item_type_are_checked(self) -> None:
        shape = ArtifactShape(
            list_keys=("plan",), item_fields=("title", "sectionId"), min_items=3
        )
        report = validate_payload({"plan": [{"title": "技术方案"}, "裸字符串"]}, shape)
        paths = {(item.path, item.code) for item in report.issues}
        # 第 0 项缺 sectionId；第 1 项类型错误；整表条目数也少于期望
        self.assertIn(("plan[0]", "schema_invalid"), paths)
        self.assertIn(("plan[1]", "schema_invalid"), paths)
        self.assertIn(("plan", "artifact_incomplete"), paths)
        self.assertTrue(report.warnings)
        self.assertEqual(len(report.errors), 2)

    def test_empty_result_is_warning_not_error(self) -> None:
        shape = ArtifactShape(list_keys=("rows",), min_items=1)
        report = validate_payload({"rows": []}, shape)
        self.assertTrue(report.ok)
        self.assertEqual(report.warnings[0].code, "artifact_incomplete")

    def test_resolve_list_prefers_first_key_and_reports_type_error(self) -> None:
        report = ValidationReport()
        self.assertEqual(resolve_list({"outline": [1]}, ("plan", "outline"), report), [1])
        self.assertTrue(report.ok)
        self.assertEqual(resolve_list({"plan": {}}, ("plan",), report), [])
        self.assertFalse(report.ok)

    def test_known_artifact_shapes_exist(self) -> None:
        for artifact in ("outlinePlan", "requirementCoverage", "requirementResponses"):
            if artifact == "requirementResponses":
                continue
            self.assertIsNotNone(shape_for(artifact), artifact)
        self.assertIsNone(shape_for("不存在的产物"))
        self.assertIn("plan", shape_for("outlinePlan").list_keys)


class RunStateTests(unittest.TestCase):
    def _state(self) -> RunState:
        return RunState(projectId="p1", workspaceId="w1", stage="outline")

    def test_begin_node_tracks_attempts(self) -> None:
        state = self._state()
        first = state.begin_node(node_id="technical", agent_id="OutlinePlanAgent")
        self.assertEqual(first.attempt, 1)
        self.assertIs(first.status, NodeStatus.RUNNING)
        second = state.begin_node(node_id="technical", agent_id="OutlinePlanAgent")
        self.assertEqual(second.attempt, 2)
        self.assertEqual(len(state.nodes), 1, "同一 nodeId 不得产生重复记录")

    def test_failure_kind_decides_retryability(self) -> None:
        state = self._state()
        timeout = state.begin_node(node_id="a", agent_id="SectionDraftAgent")
        timeout.mark_failed(FailureKind.TIMEOUT, "超时")
        parse_error = state.begin_node(node_id="b", agent_id="SectionDraftAgent")
        parse_error.mark_failed(FailureKind.PARSE_ERROR, "输入不可解析")
        self.assertEqual([node.nodeId for node in state.retryable_nodes()], ["a"])
        self.assertEqual([node.nodeId for node in state.failed_nodes()], ["a", "b"])

    def test_finish_summarizes(self) -> None:
        state = self._state()
        ok_node = state.begin_node(node_id="a", agent_id="OutlinePlanAgent")
        ok_node.mark_succeeded()
        state.begin_node(node_id="b", agent_id="SectionDraftAgent")
        state.finish()
        self.assertIs(state.status, NodeStatus.FAILED, "有未完成节点即视为未成功收尾")
        summary = state.to_summary()
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["completed"], 1)
        self.assertEqual(summary["progress"], 0.5)

        clean = self._state()
        clean.begin_node(node_id="a", agent_id="OutlinePlanAgent").mark_succeeded()
        clean.finish()
        self.assertIs(clean.status, NodeStatus.SUCCEEDED)

    def test_reuse_requires_matching_digest_and_complete_artifact(self) -> None:
        digest = input_digest({"a": 1})
        node = NodeRun(nodeId="a", agentId="OutlinePlanAgent", inputDigest=digest)
        self.assertFalse(should_reuse(node, digest=digest), "没成功过不许复用")
        node.mark_succeeded()
        self.assertTrue(should_reuse(node, digest=digest))
        self.assertFalse(should_reuse(node, digest=input_digest({"a": 2})))
        self.assertFalse(should_reuse(node, digest=""))
        node.artifactStatus = ArtifactStatus.PARTIAL
        self.assertFalse(should_reuse(node, digest=digest), "半成品不许复用")

    def test_input_digest_is_stable_and_order_insensitive(self) -> None:
        first = input_digest({"b": 2, "a": [1, 2]}, "技术方案")
        second = input_digest({"a": [1, 2], "b": 2}, "技术方案")
        third = input_digest({"a": [1, 3], "b": 2}, "技术方案")
        self.assertEqual(first, second)
        self.assertNotEqual(first, third)
        self.assertEqual(len(first), 16)

    def test_idempotency_key_composition(self) -> None:
        key = idempotency_key(
            project_id="p1", workspace_id="w1", stage="draft", node_id="t-01", digest="abc"
        )
        self.assertEqual(key, "p1|w1|draft|t-01|abc")


class BidSectionModelTests(unittest.TestCase):
    SECTION = {
        "sectionId": "d-technical-technical-plan",
        "title": "技术方案",
        "source": "ai",
        "kind": "technical",
        "writingProfile": "technical",
        "materialScopes": ["company", "performance"],
        "parentSectionId": "technical",
        "level": 2,
        "order": 3,
        "required": False,
        "responseType": "chapter_text",
        "requirementIds": ["technical-0001"],
        "evaluationIds": [],
        "content": "正文",
        "sourceRefs": [{"requirementId": "technical-0001"}],
        "status": "generated",
        "derived": True,
        "derivedOrigin": "required_by_tender",
        "derivedArchetype": "technical_plan",
        "derivedBasis": "18 条技术条款无叙述承载",
        "requirementResponseRows": [{"requirementId": "technical-0001", "response": "满足"}],
        "未来新增字段": {"nested": [1, 2, 3]},
    }

    def test_round_trip_preserves_every_field(self) -> None:
        self.assertEqual(round_trip(self.SECTION), self.SECTION)

    def test_model_keeps_unknown_fields(self) -> None:
        model = BidSectionModel(**self.SECTION)
        self.assertEqual(model.model_extra.get("未来新增字段"), {"nested": [1, 2, 3]})
        self.assertTrue(model.derived)
        self.assertEqual(model.derivedArchetype, "technical_plan")

    def test_missing_required_fields_reports_without_mutating(self) -> None:
        broken = {"sectionId": "t-01", "title": "技术方案"}
        missing = missing_required_fields(broken)
        self.assertIn("responseType", missing)
        self.assertIn("parentSectionId", missing)
        self.assertEqual(list(broken.keys()), ["sectionId", "title"], "只读不写")

    def test_empty_section_does_not_raise(self) -> None:
        model = BidSectionModel(**{})
        self.assertEqual(model.sectionId, "")
        self.assertEqual(model.level, 1)


if __name__ == "__main__":
    unittest.main()
