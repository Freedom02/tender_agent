# -*- coding: utf-8 -*-
"""Wave 1 · A 契约测试：形态校验接线、运行记账接线、示例 schema 偏差扫描。

这个文件守住三件事（对应架构改造方案 §2-A）：
  1. **形态表与登记表不许漂移**：`specs.AGENT_SPECS` 里每个 artifact 都必须在
     `validation.ARTIFACT_SHAPES` 有形态；Wave 0 已有形态的语义不许被顺手改掉。
  2. **接线只记录、不阻断**：产物不合示例 schema 时不抛异常、不改变返回内容
     （只多出 `validation` 段与人读告警），`_command()` 的 argv 逐字节不变。
  3. **偏差清单来自真实数据**：扫 6 个真实工作区的 `bidDocument.agentResults`，
     统计「示例 schema 里有的字段，实际输出里没有/类型不符」。

偏差扫描不依赖 `runtime/`（那是本机数据）：没有数据时相关用例自动 skip，
需要出报告时用 `python -m backend.tests.test_agent_contracts --scan`。
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from pathlib import Path
from typing import Any

from backend.app.agent_runtime.run_state import FailureKind, input_digest
from backend.app.agent_runtime.specs import (
    AGENT_SPECS,
    ARTIFACT_SCHEMA_SOURCES,
    agents_for_artifact,
    artifact_for,
    resolve_spec,
)
from backend.app.agent_runtime.validation import (
    ARTIFACT_SHAPES,
    ArtifactShape,
    shape_for,
    shape_for_agent,
    validate_agent_output,
    validate_payload,
    validation_section,
    validation_warnings,
)
from backend.app.pi_agent.config import PiConfig
from backend.app.pi_agent import prompts as prompt_module
from backend.app.pi_agent.prompts import requirement_map_prompt, volume_plan_prompt
from backend.app.pi_agent.transport import (
    PiError,
    PolicyBlockedError,
    PiTransport,
    agent_id_from_prompt,
    classify_failure,
    parse_json_text,
    validation_enabled,
)

ROOT = Path(__file__).resolve().parents[2]
WORKSPACES_JSON = ROOT / "runtime" / "workspaces.json"

#: 提示词首句的智能体声明（校验接线的归因依据）
DECLARATION = re.compile(r"你是\s*([A-Za-z][A-Za-z0-9]*Agent)")

#: 真实提示词首句（与 prompts/plan.py 一致），用于接线测试
OUTLINE_PROMPT = "你是 OutlinePlanAgent，负责为一份招标文件规划可执行的投标文件目录。\n输出结构：{...}"
RESPONSE_TABLE_PROMPT = "你是 RequirementResponseTableAgent，负责撰写技术和服务要求响应表中的“投标应答”单元格。"


def pi_config(**overrides: Any) -> PiConfig:
    """确定性配置：命令用当前解释器绝对路径，避免依赖 PATH 上有没有 pi。"""

    fields: dict[str, Any] = {
        "enabled": True,
        "command": sys.executable,
        "provider": "",
        "model": "",
        "api_key": "",
        "timeout_seconds": 360.0,
        "working_dir": "",
    }
    fields.update(overrides)
    return PiConfig(**fields)  # type: ignore[arg-type]


class StubTransport(PiTransport):
    """把 `_run_json_raw` 换成固定返回：不启动 Pi 也能观察接线行为。"""

    def __init__(self, payload: Any = None, error: BaseException | None = None, **overrides: Any) -> None:
        super().__init__(pi_config(**overrides))
        self.payload = {} if payload is None else payload
        self.error = error
        self.raw_calls: list[dict[str, Any]] = []
        self.commands: list[list[str]] = []

    def _run_json_raw(
        self,
        prompt: str,
        *,
        timeout_seconds: float | None = None,
        tools: str = "",
        extensions: tuple[str, ...] = (),
        env_extra: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        self.raw_calls.append(
            {
                "prompt": prompt,
                "timeout_seconds": timeout_seconds,
                "tools": tools,
                "extensions": extensions,
                "env_extra": env_extra,
            }
        )
        self.commands.append(self._command(tools=tools, extensions=extensions))
        if self.error is not None:
            raise self.error
        return dict(self.payload) if isinstance(self.payload, dict) else self.payload


class ShapeTableTests(unittest.TestCase):
    """形态表必须覆盖登记表里的每个 artifact，且 Wave 0 语义不许漂移。"""

    def test_every_registered_artifact_has_a_shape(self) -> None:
        missing = sorted(
            {spec.artifact for spec in AGENT_SPECS if spec.artifact not in ARTIFACT_SHAPES}
        )
        self.assertEqual(missing, [], f"这些 artifact 还没有形态表，校验会静默跳过：{missing}")

    def test_every_registered_agent_resolves_to_a_shape(self) -> None:
        unresolved = [spec.agent_id for spec in AGENT_SPECS if shape_for_agent(spec.agent_id) is None]
        self.assertEqual(unresolved, [], f"这些智能体取不到形态：{unresolved}")

    def test_runtime_label_suffix_still_resolves(self) -> None:
        self.assertIsNotNone(shape_for_agent("RequirementMapAgent[2/3]"))
        self.assertIsNone(shape_for_agent("NotAnAgent"))
        self.assertEqual(artifact_for("NotAnAgent"), "")

    def test_wave0_shapes_keep_their_semantics(self) -> None:
        """Wave 0 的 7 个形态：候选键、必需字段、min_items 逐个冻结。"""

        expected: dict[str, tuple[tuple[str, ...], tuple[str, ...], int]] = {
            "outlinePlan": (("plan", "outline", "sections"), ("title",), 1),
            "volumePlan": (("plan", "volumes", "sections"), ("title",), 1),
            "requirementCoverage": (("coverage", "mappings", "rows", "items"), ("sectionId", "requirementId"), 0),
            "requirements": (("requirements", "items", "rows"), ("requirement",), 0),
            "evaluationItems": (("evaluationItems", "items", "rows"), ("item",), 0),
            "materialMatches": (("matches", "materialMatches", "items"), ("requirementId",), 0),
        }
        for artifact, (list_keys, item_fields, min_items) in expected.items():
            shape = ARTIFACT_SHAPES[artifact]
            self.assertEqual(shape.list_keys, list_keys, artifact)
            self.assertEqual(shape.item_fields, item_fields, artifact)
            self.assertEqual(shape.min_items, min_items, artifact)

    def test_response_rows_require_the_row_anchor(self) -> None:
        """真实数据里逐行回填的锚点是 requirementId，缺它的行下游无法归位。"""

        self.assertEqual(ARTIFACT_SHAPES["requirementResponseRows"].item_fields, ("requirementId",))
        self.assertFalse(ARTIFACT_SHAPES["requirementResponseRows"].list_required)

    def test_new_shape_fields_default_to_wave0_behaviour(self) -> None:
        shape = ArtifactShape(list_keys=("plan",), item_fields=("title",))
        self.assertEqual(shape.dict_keys, ())
        self.assertEqual(shape.text_keys, ())
        self.assertEqual(shape.item_any_fields, ())
        self.assertTrue(validate_payload({"plan": [{"title": "技术方案"}]}, shape).ok)

    def test_artifact_reverse_lookup(self) -> None:
        self.assertIn("OutlinePlanAgent", agents_for_artifact("outlinePlan"))
        self.assertEqual(agents_for_artifact("不存在的产物"), ())
        self.assertEqual(set(ARTIFACT_SCHEMA_SOURCES), set(ARTIFACT_SHAPES))


class AttributionTests(unittest.TestCase):
    """提示词首句的「你是 XxxAgent」是归因依据，必须覆盖每个智能体。"""

    def test_every_declared_agent_in_prompt_sources_is_registered(self) -> None:
        found: dict[str, str] = {}
        for path in sorted((ROOT / "backend" / "app" / "pi_agent" / "prompts").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for match in DECLARATION.finditer(text):
                found.setdefault(match.group(1), path.name)
        self.assertTrue(found, "没扫到任何「你是 XxxAgent」声明，正则或路径已失效")
        unregistered = sorted(name for name in found if resolve_spec(name) is None)
        self.assertEqual(unregistered, [], f"提示词里出现但未登记的智能体：{unregistered}")

        declared = set(found)
        specs_with_declaration = {
            spec.agent_id for spec in AGENT_SPECS if spec.agent_id in declared
        }
        # 16 个登记智能体里，除「解析前的合并提示词」外都应有独立声明
        self.assertGreaterEqual(len(specs_with_declaration), 15)

    def test_agent_id_is_read_from_real_prompt_builders(self) -> None:
        self.assertEqual(
            agent_id_from_prompt(
                volume_plan_prompt(
                    parse_bundle={}, existing_plan=[], workspace_id="w1", project_id="p1"
                )
            ),
            "VolumePlanAgent",
        )
        self.assertEqual(
            agent_id_from_prompt(
                requirement_map_prompt(
                    parse_bundle={},
                    outline_plan=[],
                    workspace_id="w1",
                    project_id="p1",
                )
            ),
            "RequirementMapAgent",
        )

    def test_unknown_or_missing_declaration_yields_no_agent(self) -> None:
        self.assertEqual(agent_id_from_prompt("你是 NotAnAgent，负责不存在的事。"), "")
        self.assertEqual(agent_id_from_prompt("随便写点什么，没有声明。"), "")
        self.assertEqual(agent_id_from_prompt(""), "")


class ValidationWiringTests(unittest.TestCase):
    """接线只记录：不抛异常、不删键、不改分支。"""

    VALID_OUTLINE = {
        "plan": [
            {"sectionId": "qualification", "title": "资格及资信证明部分", "level": 1},
            {"sectionId": "ai-technical-plan", "title": "技术方案", "derived": True},
        ],
        "warnings": ["原骨架严格保留。"],
    }

    def test_valid_payload_is_recorded_and_untouched(self) -> None:
        transport = StubTransport(payload=self.VALID_OUTLINE)
        payload = transport.run_json(OUTLINE_PROMPT)

        self.assertEqual(payload["plan"], self.VALID_OUTLINE["plan"])
        self.assertEqual(payload["warnings"], self.VALID_OUTLINE["warnings"], "没有问题时不许加告警")
        section = payload["validation"]
        self.assertEqual(section["agentId"], "OutlinePlanAgent")
        self.assertEqual(section["artifact"], "outlinePlan")
        self.assertTrue(section["ok"])
        self.assertEqual(section["errorCount"], 0)
        self.assertEqual(section["mode"], "record_only")

    def test_schema_invalid_is_recorded_but_never_raised(self) -> None:
        transport = StubTransport(payload={"plan": "不是数组"})
        payload = transport.run_json(OUTLINE_PROMPT)

        section = payload["validation"]
        self.assertFalse(section["ok"])
        self.assertEqual(section["errorCount"], 1)
        self.assertEqual(section["issues"][0]["code"], "schema_invalid")
        self.assertEqual(section["issues"][0]["path"], "plan")
        self.assertEqual(len(payload["warnings"]), 1)
        self.assertIn("schema_invalid", payload["warnings"][0])
        self.assertIn("仅记录不阻断", payload["warnings"][0])

    def test_missing_critical_field_is_recorded(self) -> None:
        transport = StubTransport(payload={"plan": [{"sectionId": "t-01"}]})
        payload = transport.run_json(OUTLINE_PROMPT)
        self.assertFalse(payload["validation"]["ok"])
        self.assertEqual(payload["validation"]["issues"][0]["message"], "缺少必需字段 title")

    def test_response_table_empty_batch_is_still_tolerated(self) -> None:
        """已记录的缺口：真实数据里失败批次返回 `{}`，而该形态 list_required=False 会放行。

        这里把「放行」钉成事实，等 Wave 2 收紧形态或改成按 status=failed 记 failureKind 时，
        这条用例会失败并提醒更新偏差清单。
        """

        self.assertFalse(ARTIFACT_SHAPES["requirementResponseRows"].list_required)
        payload = StubTransport(payload={}).run_json(RESPONSE_TABLE_PROMPT)
        self.assertTrue(payload["validation"]["ok"])
        self.assertEqual(payload["validation"]["errorCount"], 0)

        rows = StubTransport(payload={"rows": [{"response": "缺锚点的行"}]})
        recorded = rows.run_json(RESPONSE_TABLE_PROMPT)
        self.assertFalse(recorded["validation"]["ok"])
        self.assertEqual(
            recorded["validation"]["issues"][0]["message"], "缺少必需字段 requirementId"
        )

    def test_unknown_keys_are_tolerated_through_the_wiring(self) -> None:
        transport = StubTransport(
            payload={
                "plan": [{"title": "技术方案", "未来新增字段": {"nested": [1, 2]}}],
                "surprise": "ok",
            }
        )
        payload = transport.run_json(OUTLINE_PROMPT)
        self.assertTrue(payload["validation"]["ok"])
        self.assertNotIn("warnings", payload, "没问题时不该凭空加一个 warnings 键")

    def test_existing_warnings_key_is_appended_not_replaced(self) -> None:
        transport = StubTransport(payload={"plan": [{"title": "技术方案"}], "warnings": ["模型自己的告警"]})
        payload = transport.run_json(OUTLINE_PROMPT)
        self.assertEqual(payload["warnings"], ["模型自己的告警"])
        self.assertTrue(payload["validation"]["ok"])

    def test_validation_can_be_switched_off(self) -> None:
        transport = StubTransport(payload={"plan": "不是数组"})
        payload = transport.run_json(OUTLINE_PROMPT, validate=False)
        self.assertNotIn("validation", payload)
        self.assertNotIn("warnings", payload)

        previous = os.environ.get("PI_AGENT_VALIDATION")
        os.environ["PI_AGENT_VALIDATION"] = "off"
        try:
            self.assertFalse(validation_enabled())
            env_off = StubTransport(payload={"plan": "不是数组"})
            self.assertNotIn("validation", env_off.run_json(OUTLINE_PROMPT))
        finally:
            if previous is None:
                os.environ.pop("PI_AGENT_VALIDATION", None)
            else:
                os.environ["PI_AGENT_VALIDATION"] = previous
        self.assertTrue(validation_enabled())

    def test_explicit_agent_id_overrides_prompt_inference(self) -> None:
        transport = StubTransport(payload={"volumes": [{"title": "技术文件"}]})
        payload = transport.run_json("没有声明的提示词", agent_id="VolumePlanAgent")
        self.assertEqual(payload["validation"]["artifact"], "volumePlan")
        self.assertTrue(payload["validation"]["ok"])

    def test_unattributed_call_is_not_validated(self) -> None:
        transport = StubTransport(payload={"plan": "不是数组"})
        payload = transport.run_json("没有声明的提示词")
        self.assertNotIn("validation", payload)

    def test_non_dict_payload_passes_through(self) -> None:
        transport = StubTransport(payload=["不是对象"])
        self.assertEqual(transport.run_json(OUTLINE_PROMPT), ["不是对象"])

    def test_errors_from_the_raw_call_still_propagate(self) -> None:
        transport = StubTransport(error=PiError("Pi 智能体执行超时"))
        with self.assertRaises(PiError):
            transport.run_json(OUTLINE_PROMPT)

    def test_warning_messages_stay_short_enough_for_agent_results(self) -> None:
        """编排层会把每条告警截到 500 字，接线自产的告警必须远短于此。"""

        broken = {"plan": [{"sectionId": f"t-{index:02d}"} for index in range(40)]}
        report = validate_agent_output("OutlinePlanAgent", broken)
        messages = validation_warnings("OutlinePlanAgent", report)
        self.assertTrue(messages)
        for message in messages:
            self.assertLess(len(message), 500)
        self.assertEqual(len(report.errors), 40)
        self.assertIn("等共 40 处", messages[0])

    def test_validation_section_is_json_serializable(self) -> None:
        report = validate_agent_output("OutlinePlanAgent", {"plan": "不是数组"})
        json.dumps(validation_section("OutlinePlanAgent", report), ensure_ascii=False)


class RunStateWiringTests(unittest.TestCase):
    """运行记账只接在 plan_outline 一条链路上，且只进返回结构、不落盘。"""

    VALID_OUTLINE = {"plan": [{"sectionId": "ai-technical-plan", "title": "技术方案"}], "warnings": []}

    def test_outline_chain_records_a_succeeded_node(self) -> None:
        transport = StubTransport(payload=self.VALID_OUTLINE)
        payload = transport.run_json(OUTLINE_PROMPT)

        section = payload["runState"]
        self.assertFalse(section["persisted"], "Wave 1 明确不落盘")
        self.assertEqual(section["node"]["nodeId"], "OutlinePlanAgent")
        self.assertEqual(section["node"]["agentId"], "OutlinePlanAgent")
        self.assertEqual(section["node"]["stage"], "plan")
        self.assertEqual(section["node"]["status"], "succeeded")
        self.assertEqual(section["node"]["artifactStatus"], "complete")
        self.assertEqual(section["node"]["attempt"], 1)
        self.assertTrue(section["node"]["inputDigest"])
        self.assertEqual(section["run"]["total"], 1)
        self.assertEqual(section["run"]["completed"], 1)

    def test_volume_plan_and_outline_share_one_run(self) -> None:
        transport = StubTransport(payload={"volumes": []})
        transport.run_json("你是 VolumePlanAgent，只规划投标文件的卷。")
        transport.run_json(OUTLINE_PROMPT)

        state = transport.current_run_state
        self.assertIsNotNone(state)
        self.assertEqual([node.nodeId for node in state.nodes], ["VolumePlanAgent", "OutlinePlanAgent"])
        self.assertEqual(
            [node.artifactStatus.value for node in state.nodes],
            ["partial", "partial"],
            "空卷集合与空目录都算产物不完整",
        )

    def test_same_prompt_yields_the_same_input_digest(self) -> None:
        """幂等键的前提：同输入同摘要（真跑两次 VolumePlanAgent 提示词）。"""

        prompt = "你是 VolumePlanAgent，只规划投标文件的卷。\n工作区：w1；项目：p1"
        transport = StubTransport(payload={"volumes": []})
        transport.run_json(prompt)
        transport.run_json(prompt)
        node = transport.current_run_state.node("VolumePlanAgent")
        self.assertEqual(node.attempt, 2)
        self.assertEqual(node.inputDigest, input_digest(prompt))

    def test_quality_retry_is_recorded_as_a_second_attempt(self) -> None:
        transport = StubTransport(payload=self.VALID_OUTLINE)
        transport.run_json(OUTLINE_PROMPT)
        retried = transport.run_json("你是 OutlinePlanAgent，负责重新规划目录。\n【目录修正要求】上次重复。")
        self.assertEqual(retried["runState"]["node"]["attempt"], 2)
        self.assertEqual(len(transport.current_run_state.nodes), 1, "同一 nodeId 不产生重复行")

    def test_schema_invalid_downgrades_artifact_status(self) -> None:
        transport = StubTransport(payload={"plan": "不是数组"})
        payload = transport.run_json(OUTLINE_PROMPT)

        node = payload["runState"]["node"]
        self.assertEqual(node["status"], "succeeded", "记账不改变执行事实")
        self.assertEqual(node["artifactStatus"], "partial", "产物不合规就不能记 complete")
        self.assertEqual(node["failureKind"], "")

    def test_incomplete_artifact_is_partial_not_complete(self) -> None:
        transport = StubTransport(payload={"plan": []})
        payload = transport.run_json(OUTLINE_PROMPT)
        self.assertTrue(payload["validation"]["ok"], "空目录是合法返回，只记 warning 不记错误")
        self.assertEqual(payload["validation"]["warningCount"], 1)
        self.assertEqual(payload["validation"]["issues"][0]["code"], "artifact_incomplete")
        self.assertEqual(payload["runState"]["node"]["artifactStatus"], "partial")

    def test_failure_kind_is_recorded_and_the_exception_still_propagates(self) -> None:
        cases = [
            ("Pi 智能体执行超时（provider=x）", FailureKind.TIMEOUT, True),
            ("Pi 返回内容不是合法 JSON：{", FailureKind.SCHEMA_INVALID, True),
            ("Pi 执行失败（provider=x）：通道错误", FailureKind.PROVIDER_ERROR, True),
            ("说不清的失败", FailureKind.UNKNOWN, False),
        ]
        for message, expected_kind, retryable in cases:
            transport = StubTransport(error=PiError(message))
            with self.assertRaises(PiError) as caught:
                transport.run_json(OUTLINE_PROMPT)
            self.assertEqual(str(caught.exception), message, "异常必须原样抛出")
            node = transport.current_run_state.node("OutlinePlanAgent")
            self.assertEqual(node.status.value, "failed")
            self.assertEqual(node.failureKind, expected_kind.value)
            self.assertEqual(node.retryable, retryable)
            self.assertEqual(node.artifactStatus.value, "missing")

    def test_classify_failure_covers_transport_errors(self) -> None:
        self.assertIs(classify_failure(PiError("Pi 返回内容不是合法 JSON：{")), FailureKind.SCHEMA_INVALID)
        self.assertIs(classify_failure(PiError("Pi 没有返回文本")), FailureKind.PROVIDER_ERROR)
        self.assertIs(classify_failure(PiError("无法向 Pi 发送请求：子进程已退出")), FailureKind.PROVIDER_ERROR)
        self.assertIs(classify_failure(PiError("未知情况")), FailureKind.UNKNOWN)

    def test_policy_blocked_is_distinct_non_retryable_and_redacted(self) -> None:
        raw = "content_policy: api_key=sk-super-secret 招标原文不得外泄"
        error = PolicyBlockedError(raw)
        self.assertIs(classify_failure(error), FailureKind.POLICY_BLOCKED)
        self.assertFalse(error.retryable)
        self.assertNotEqual(error.__class__.__name__, "PiError", "旧编排器只重试精确的 PiError")
        self.assertNotIn("sk-super-secret", str(error))
        self.assertNotIn("招标原文不得外泄", str(error))
        transport = StubTransport(error=error)
        with self.assertRaises(PolicyBlockedError):
            transport.run_json(OUTLINE_PROMPT)
        node = transport.current_run_state.node("OutlinePlanAgent")
        self.assertEqual(node.failureKind, FailureKind.POLICY_BLOCKED.value)
        self.assertFalse(node.retryable)
        self.assertNotIn("sk-super-secret", node.failureMessage)

    def test_policy_timeout_and_invalid_json_remain_distinct(self) -> None:
        self.assertIs(
            classify_failure(PiError("Pi 智能体执行超时（provider=x）")),
            FailureKind.TIMEOUT,
        )
        self.assertIs(
            classify_failure(PiError("Pi 返回内容不是合法 JSON：已省略")),
            FailureKind.SCHEMA_INVALID,
        )
        self.assertIs(
            classify_failure(PiError("Pi 执行失败（provider=x）：通道错误")),
            FailureKind.PROVIDER_ERROR,
        )
        with self.assertRaises(PolicyBlockedError):
            parse_json_text("content_policy: input rejected")

    def test_invalid_json_diagnostic_does_not_echo_model_input(self) -> None:
        secret_text = "投标文件原文-不可记录-" + ("敏感" * 80)
        with self.assertRaises(PiError) as caught:
            parse_json_text(secret_text)
        self.assertIs(classify_failure(caught.exception), FailureKind.SCHEMA_INVALID)
        self.assertNotIn(secret_text, str(caught.exception))
        self.assertIn("不是合法 JSON", str(caught.exception))

    def test_provider_policy_event_is_non_retryable_and_redacted(self) -> None:
        fixture = (
            "import json,sys; sys.stdin.readline(); "
            "print(json.dumps({'type':'response','command':'prompt','success':False, "
            "'error':{'code':'content_policy','message':'api_key=sk-secret 投标原文'}}), flush=True)"
        )

        class FixtureTransport(PiTransport):
            def _command(self, *, tools: str = "", extensions: tuple[str, ...] = ()) -> list[str]:
                return [sys.executable, "-c", fixture]

        transport = FixtureTransport(pi_config(provider="fixture"))
        with self.assertRaises(PolicyBlockedError) as caught:
            transport.run_json(OUTLINE_PROMPT)
        self.assertIs(classify_failure(caught.exception), FailureKind.POLICY_BLOCKED)
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn("sk-secret", str(caught.exception))
        node = transport.current_run_state.node("OutlinePlanAgent")
        self.assertEqual(node.failureKind, FailureKind.POLICY_BLOCKED.value)
        self.assertFalse(node.retryable)

    def test_provider_policy_stderr_is_classified_and_redacted(self) -> None:
        fixture = (
            "import sys; sys.stdin.readline(); "
            "print('content_policy api_key=sk-stderr-secret 投标原文', file=sys.stderr, flush=True)"
        )

        class FixtureTransport(PiTransport):
            def _command(self, *, tools: str = "", extensions: tuple[str, ...] = ()) -> list[str]:
                return [sys.executable, "-c", fixture]

        transport = FixtureTransport(pi_config(provider="fixture"))
        with self.assertRaises(PolicyBlockedError) as caught:
            transport.run_json(OUTLINE_PROMPT)
        self.assertIs(classify_failure(caught.exception), FailureKind.POLICY_BLOCKED)
        self.assertNotIn("sk-stderr-secret", str(caught.exception))
        self.assertNotIn("投标原文", str(caught.exception))
        node = transport.current_run_state.node("OutlinePlanAgent")
        self.assertEqual(node.failureKind, FailureKind.POLICY_BLOCKED.value)

    def test_provider_policy_before_request_is_not_retried(self) -> None:
        fixture = (
            "import sys; print('safety_violation api_key=sk-early-secret', file=sys.stderr, flush=True)"
        )

        class FixtureTransport(PiTransport):
            def _command(self, *, tools: str = "", extensions: tuple[str, ...] = ()) -> list[str]:
                return [sys.executable, "-c", fixture]

        transport = FixtureTransport(pi_config(provider="fixture"))
        with self.assertRaises(PolicyBlockedError) as caught:
            transport.run_json(OUTLINE_PROMPT)
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn("sk-early-secret", str(caught.exception))
        node = transport.current_run_state.node("OutlinePlanAgent")
        self.assertEqual(node.failureKind, FailureKind.POLICY_BLOCKED.value)

    def test_untracked_agent_is_not_recorded(self) -> None:
        transport = StubTransport(payload={"rows": [{"requirementId": "technical-0001", "response": "满足"}]})
        payload = transport.run_json(RESPONSE_TABLE_PROMPT)
        self.assertNotIn("runState", payload)
        self.assertIsNone(transport.current_run_state, "不在试点链路上的智能体不建运行态")

    def test_begin_run_injects_identity(self) -> None:
        transport = StubTransport(payload=self.VALID_OUTLINE)
        transport.begin_run(project_id="local-690263cac1", workspace_id="workspace-f22e272de6", stage="outline")
        payload = transport.run_json(OUTLINE_PROMPT)

        run = payload["runState"]["run"]
        self.assertEqual(run["projectId"], "local-690263cac1")
        self.assertEqual(run["workspaceId"], "workspace-f22e272de6")
        self.assertEqual(run["status"], "running", "链路未收尾时只能是当下快照")

    def test_validation_switch_does_not_disable_bookkeeping(self) -> None:
        transport = StubTransport(payload=self.VALID_OUTLINE)
        payload = transport.run_json(OUTLINE_PROMPT, validate=False)
        self.assertNotIn("validation", payload)
        self.assertIn("runState", payload)


class CommandArgvTests(unittest.TestCase):
    """`_command()` 的 argv 是冻结契约：接线不许碰它。"""

    def test_default_argv_is_frozen(self) -> None:
        argv = StubTransport()._command()
        self.assertEqual(argv, [sys.executable, "--mode", "rpc", "--no-session", "--no-tools"])

    def test_tool_argv_is_frozen(self) -> None:
        argv = StubTransport()._command(
            tools="bid_outline_signals,search_requirements",
            extensions=(Path("/tmp/ext-a.ts"), Path("/tmp/ext-b.ts")),
        )
        self.assertEqual(
            argv,
            [
                sys.executable,
                "--mode",
                "rpc",
                "--no-session",
                "--no-builtin-tools",
                "--tools",
                "bid_outline_signals,search_requirements",
                "--extension",
                str(Path("/tmp/ext-a.ts")),
                "--extension",
                str(Path("/tmp/ext-b.ts")),
            ],
        )

    def test_wiring_does_not_change_the_argv_actually_used(self) -> None:
        transport = StubTransport(payload={"plan": [{"title": "技术方案"}]})
        before = transport._command()
        transport.run_json(
            OUTLINE_PROMPT,
            tools="bid_outline_signals",
            extensions=(Path("/tmp/ext-a.ts"),),
            timeout_seconds=120,
        )
        after = transport._command()
        self.assertEqual(before, after, "接线后默认 argv 必须逐字节相同")
        self.assertEqual(
            transport.commands[0],
            [
                sys.executable,
                "--mode",
                "rpc",
                "--no-session",
                "--no-builtin-tools",
                "--tools",
                "bid_outline_signals",
                "--extension",
                str(Path("/tmp/ext-a.ts")),
            ],
        )

    def test_run_json_passes_transport_arguments_through(self) -> None:
        transport = StubTransport(payload={"plan": [{"title": "技术方案"}]})
        transport.run_json(
            OUTLINE_PROMPT,
            timeout_seconds=42,
            tools="bid_outline_signals",
            extensions=(Path("/tmp/ext-a.ts"),),
            env_extra={"BID_OUTLINE_CONTEXT": "/tmp/ctx.json"},
        )
        call = transport.raw_calls[0]
        self.assertEqual(call["timeout_seconds"], 42)
        self.assertEqual(call["tools"], "bid_outline_signals")
        self.assertEqual(call["extensions"], (Path("/tmp/ext-a.ts"),))
        self.assertEqual(call["env_extra"], {"BID_OUTLINE_CONTEXT": "/tmp/ctx.json"})


# ══════════════════════════════════════════════════════════════════════
# 偏差清单：示例 schema ↔ 真实输出的字段级对比
# ══════════════════════════════════════════════════════════════════════

#: VolumePlanAgent 的示例 schema **没有独立常量**（样例内联在 plan.py 的提示词正文第 135 行），
#: 这里按原文抄录。若将来补了 `VOLUME_PLAN_SCHEMA` 常量，请改成引用它（用例会提醒）。
VOLUME_PLAN_INLINE_SCHEMA: dict[str, Any] = {
    "volumes": [
        {
            "volumeId": "volume-01",
            "title": "文件名称",
            "order": 1,
            "kind": "commercial",
            "sourceLocator": "原文定位",
        }
    ],
    "warnings": [],
}

#: artifact → 给模型看的示例 schema（出处见 specs.ARTIFACT_SCHEMA_SOURCES）
EXAMPLE_SCHEMAS: dict[str, dict[str, Any]] = {
    "clauseExtractionV2": prompt_module.CLAUSE_EXTRACTION_V2_SCHEMA,
    "evaluationExtractionV2": prompt_module.EVALUATION_EXTRACTION_V2_SCHEMA,
    "tableBindingV2": prompt_module.TABLE_BINDING_V2_SCHEMA,
    "semanticReviewV2": prompt_module.SEMANTIC_REVIEW_V2_SCHEMA,
    "documentStructure": prompt_module.DOCUMENT_STRUCTURE_SCHEMA,
    "projectMeta": prompt_module.PROJECT_META_SCHEMA,
    "requirements": prompt_module.REQUIREMENT_SCHEMA,
    "evaluationItems": prompt_module.EVALUATION_SCHEMA,
    "bidFormatSpec": prompt_module.TEMPLATE_SCHEMA,
    "parseReviewReport": prompt_module.PARSE_REVIEW_SCHEMA,
    "volumePlan": VOLUME_PLAN_INLINE_SCHEMA,
    "outlinePlan": prompt_module.OUTLINE_PLAN_SCHEMA,
    "requirementCoverage": prompt_module.REQUIREMENT_MAP_SCHEMA,
    "materialMatches": prompt_module.MATERIAL_MATCH_SCHEMA,
    "writingPlan": prompt_module.SECTION_PLAN_SCHEMA,
    "sectionContent": prompt_module.SECTION_DRAFT_SCHEMA,
    "templateFragmentFields": prompt_module.TABLE_FORM_FILL_SCHEMA,
    "requirementResponseRows": prompt_module.REQUIREMENT_RESPONSE_TABLE_SCHEMA,
    "complianceFindings": prompt_module.COMPLIANCE_REVIEW_SCHEMA,
}

NUMBER = "number"
ANY_TYPE = "any"

#: 提示词里**明确枚举**过的字段（plan.py 规则 9 / 16）：出现枚举外的值一律算偏差。
#: 这是方案 §3.1「枚举归一化必须集中实现」的真实依据——真实数据里
#: 同一个 agent 两次运行分别给出 `chapter_text` 与 `narrative`。
SCHEMA_ENUMS: dict[str, dict[str, tuple[str, ...]]] = {
    "outlinePlan": {
        "source": ("template", "table", "ai", "deviation", "attachment", "copied_template"),
        "responseType": (
            "section_group",
            "chapter_text",
            "fixed_form",
            "table",
            "attachment",
            "deviation",
        ),
        "kind": ("commercial", "technical", "deviation", "attachment"),
    },
}

#: 这些键是「外壳」或运行期附加信息，比对时不算偏差
IGNORED_KEYS = frozenset(
    {"agentResults", "validation", "runState", "provider", "model", "runId"}
)


def type_name(value: Any) -> str:
    """给示例值与真实值统一的类型名（bool 必须先判，它是 int 的子类）。"""

    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return NUMBER
    if isinstance(value, str):
        return "str"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if value is None:
        return "null"
    return type(value).__name__


def expected_type(example: Any) -> str:
    """示例里的 None 表示「可能是任意值/未知」，不做类型比对。"""

    return ANY_TYPE if example is None else type_name(example)


def _item_key_stats(schema: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    """条目级比对：示例里「每个条目都有」的键，在真实条目里缺了多少条。"""

    stats: dict[str, Any] = {
        "listKey": "",
        "itemCount": 0,
        "common": [],
        "missing": {},
        "extra": {},
        "items": [],
    }
    for key, example in schema.items():
        if not isinstance(example, list) or not example:
            continue
        actual = payload.get(key)
        if not isinstance(actual, list) or not actual:
            continue
        example_dicts = [item for item in example if isinstance(item, dict)]
        if not example_dicts:
            continue
        actual_dicts = [item for item in actual if isinstance(item, dict)]
        common = [k for k in example_dicts[0] if all(k in item for item in example_dicts)]
        union = {k for item in example_dicts for k in item}
        stats["listKey"] = key
        stats["itemCount"] = len(actual)
        stats["nonObjectItems"] = len(actual) - len(actual_dicts)
        stats["common"] = common
        stats["missing"] = {
            k: sum(1 for item in actual_dicts if k not in item) for k in common
        }
        extra: dict[str, int] = {}
        for item in actual_dicts:
            for k in item:
                if k not in union:
                    extra[k] = extra.get(k, 0) + 1
        stats["extra"] = extra
        stats["items"] = actual_dicts
        break
    return stats


def enum_violations(artifact: str, items: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """统计越出提示词枚举的取值（如 responseType=narrative、source=ai_derived）。"""

    enums = SCHEMA_ENUMS.get(artifact) or {}
    result: dict[str, dict[str, int]] = {}
    for field, allowed in enums.items():
        counts: dict[str, int] = {}
        for item in items:
            if field not in item:
                continue
            value = str(item.get(field) or "")
            if value not in allowed:
                counts[value] = counts.get(value, 0) + 1
        if counts:
            result[field] = counts
    return result


def compare_with_example(agent_id: str, payload: Any) -> dict[str, Any]:
    """把一个真实返回与它的示例 schema 逐字段比对。"""

    artifact = artifact_for(agent_id)
    schema = EXAMPLE_SCHEMAS.get(artifact)
    result: dict[str, Any] = {
        "agentId": agent_id,
        "artifact": artifact,
        "checked": schema is not None,
        "empty": False,
        "topMissing": [],
        "topTypeMismatch": [],
        "unknownTop": [],
        "emptyLists": [],
        "item": {},
    }
    if schema is None:
        return result
    if not isinstance(payload, dict) or not payload:
        result["empty"] = True
        return result

    for key, example in schema.items():
        if key not in payload:
            result["topMissing"].append(key)
            continue
        expected = expected_type(example)
        actual = type_name(payload[key])
        if expected != ANY_TYPE and expected != actual:
            result["topTypeMismatch"].append({"key": key, "expected": expected, "actual": actual})
        elif isinstance(example, list) and example and not payload[key]:
            # 示例给了条目、实际是空数组：类型对但产物缺失（不算 schema_invalid）
            result["emptyLists"].append(key)
    result["unknownTop"] = [
        key for key in payload if key not in schema and key not in IGNORED_KEYS
    ]
    result["item"] = _item_key_stats(schema, payload)
    result["enumViolations"] = enum_violations(artifact, result["item"].get("items") or [])
    return result


def collect_agent_records(path: Path = WORKSPACES_JSON) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """扫 6 个真实工作区的 `bidDocument.agentResults`（含章节级挂载的记录）。"""

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    facts: dict[str, Any] = {"workspaces": 0, "withDocument": 0, "titles": [], "containers": []}
    records: list[dict[str, Any]] = []
    for workspace_id, workspace in data.items():
        facts["workspaces"] += 1
        if not isinstance(workspace, dict):
            continue
        document = workspace.get("bidDocument") if isinstance(workspace.get("bidDocument"), dict) else None
        if not document:
            continue
        facts["withDocument"] += 1
        facts["titles"].append(str(workspace.get("title") or workspace_id))

        def take(value: Any, source: str) -> None:
            if not isinstance(value, list):
                return
            facts["containers"].append({"source": source, "count": len(value)})
            for record in value:
                if isinstance(record, dict):
                    records.append({**record, "source": source, "workspaceId": workspace_id})

        take(document.get("agentResults"), f"{workspace_id}.bidDocument.agentResults")
        for item in document.get("plan") or []:
            if not isinstance(item, dict):
                continue
            take(
                item.get("agentResults"),
                f"{workspace_id}.bidDocument.plan[{item.get('sectionId')}].agentResults",
            )
    return facts, records


def scan_deviations(records: list[dict[str, Any]]) -> dict[str, Any]:
    """按智能体聚合偏差：顶层缺键、类型不符、条目缺键、未知键、空产物。"""

    per_agent: dict[str, Any] = {}
    for record in records:
        agent_id = str(record.get("agentId") or "")
        comparison = compare_with_example(agent_id, record.get("output"))
        comparison["status"] = str(record.get("status") or "")
        comparison["source"] = str(record.get("source") or "")
        bucket = per_agent.setdefault(
            agent_id,
            {
                "artifact": comparison["artifact"],
                "records": 0,
                "empty": 0,
                "failed": 0,
                "items": 0,
                "topMissing": {},
                "topTypeMismatch": [],
                "emptyLists": {},
                "enumViolations": {},
                "itemMissing": {},
                "itemExtra": {},
                "unknownTop": {},
                "comparisons": [],
            },
        )
        bucket["records"] += 1
        bucket["empty"] += 1 if comparison["empty"] else 0
        bucket["failed"] += 1 if comparison["status"] == "failed" else 0
        bucket["items"] += int(comparison["item"].get("itemCount") or 0)
        for key in comparison["topMissing"]:
            bucket["topMissing"][key] = bucket["topMissing"].get(key, 0) + 1
        for key in comparison["emptyLists"]:
            bucket["emptyLists"][key] = bucket["emptyLists"].get(key, 0) + 1
        for field, counts in (comparison.get("enumViolations") or {}).items():
            field_counts = bucket["enumViolations"].setdefault(field, {})
            for value, count in counts.items():
                field_counts[value] = field_counts.get(value, 0) + count
        bucket["topTypeMismatch"].extend(comparison["topTypeMismatch"])
        for key, count in (comparison["item"].get("missing") or {}).items():
            if count:
                bucket["itemMissing"][key] = bucket["itemMissing"].get(key, 0) + count
        for key, count in (comparison["item"].get("extra") or {}).items():
            bucket["itemExtra"][key] = bucket["itemExtra"].get(key, 0) + count
        for key in comparison["unknownTop"]:
            bucket["unknownTop"][key] = bucket["unknownTop"].get(key, 0) + 1
        bucket["comparisons"].append(comparison)
    return {"perAgent": per_agent, "recordCount": len(records)}


def scan_persisted_artifacts(path: Path = WORKSPACES_JSON) -> list[dict[str, Any]]:
    """参考对照：落盘产物（经 Python 归一化）里与示例 schema 的偏差。

    与 `agentResults` 的区别必须记住：`bidDocument.plan` 已经被 `arrange_volumes` /
    `reconcile_plan_with_submission_structure` 处理过，缺键可能是归一化造成的，
    因此只能作为参考证据，不能当成模型输出偏差。
    """

    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    findings: list[dict[str, Any]] = []
    for workspace_id, workspace in data.items():
        if not isinstance(workspace, dict):
            continue
        document = workspace.get("bidDocument") if isinstance(workspace.get("bidDocument"), dict) else None
        if not document:
            continue
        source = f"{workspace_id}.bidDocument"
        plan = [item for item in document.get("plan") or [] if isinstance(item, dict)]
        if plan:
            findings.append(
                {
                    "label": f"{source}.plan（{len(plan)} 项，来源 {document.get('planSource')}）",
                    "artifact": "outlinePlan",
                    "comparison": compare_with_example(
                        "OutlinePlanAgent",
                        {"plan": plan, "warnings": document.get("planningWarnings") or []},
                    ),
                }
            )
        fill_sections = [
            item
            for item in plan
            if item.get("templateFillProvider") or item.get("templateFieldValues") is not None
        ]
        for item in fill_sections:
            findings.append(
                {
                    "label": (
                        f"{source}.plan[{item.get('sectionId')}]"
                        f"（provider={item.get('templateFillProvider')}，"
                        f"status={item.get('templateFillStatus')}）"
                    ),
                    "artifact": "templateFragmentFields",
                    # 用该章节真实落盘的字段拼出「模板填充产物」，不用构造值充数：
                    # templateFieldValues/templateCellValues/blanksToFill 都是落盘字段。
                    "comparison": compare_with_example(
                        "TableFormFillAgent",
                        {
                            "templateId": item.get("templateId") or "",
                            "sectionId": item.get("sectionId") or "",
                            "status": item.get("templateFillStatus") or "",
                            "fieldValues": item.get("templateFieldValues") or [],
                            "cellValues": item.get("templateCellValues") or [],
                            "blanksToFill": item.get("blanksToFill") or [],
                            "warnings": item.get("templateFillWarnings") or [],
                        },
                    ),
                }
            )
    return findings


def _counter_lines(counter: dict[str, int], total: int, unit: str) -> list[str]:
    if not counter:
        return ["    （无）"]
    return [
        f"    - {key}: {count}/{total} {unit}"
        for key, count in sorted(counter.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def render_report(facts: dict[str, Any], scanned: dict[str, Any], persisted: list[dict[str, Any]]) -> str:
    """把扫描结果渲染成可贴进交付报告的中文清单。"""

    lines: list[str] = []
    lines.append("# 示例 schema ↔ 真实输出偏差清单（Wave 1 · A）")
    lines.append("")
    lines.append("## 1. 数据面")
    lines.append("")
    lines.append(f"- 工作区总数：{facts['workspaces']}，其中带 `bidDocument` 的：{facts['withDocument']}")
    lines.append(f"- agentResults 容器：{len(facts['containers'])} 个，原始返回记录：{scanned['recordCount']} 条")
    for container in facts["containers"]:
        lines.append(f"  - {container['source']}：{container['count']} 条")
    lines.append("")
    lines.append("## 2. 按智能体聚合的偏差")
    lines.append("")
    for agent_id, bucket in sorted(scanned["perAgent"].items()):
        lines.append(f"### {agent_id}（artifact={bucket['artifact'] or '未登记'}）")
        lines.append("")
        lines.append(
            f"- 记录 {bucket['records']} 条（失败 {bucket['failed']}，空产物 {bucket['empty']}），"
            f"列表项合计 {bucket['items']} 个"
        )
        lines.append("- 顶层缺键（示例有、实际无）：")
        lines.extend(_counter_lines(bucket["topMissing"], bucket["records"], "条记录"))
        if bucket["topTypeMismatch"]:
            lines.append("- 顶层类型不符：")
            for issue in bucket["topTypeMismatch"][:8]:
                lines.append(
                    f"    - {issue['key']}: 期望 {issue['expected']}，实际 {issue['actual']}"
                )
        lines.append("- 示例有条目但实际为空数组的键：")
        lines.extend(_counter_lines(bucket["emptyLists"], bucket["records"], "条记录"))
        lines.append("- 条目缺键（示例里每个条目都给的键）：")
        lines.extend(_counter_lines(bucket["itemMissing"], bucket["items"], "个条目"))
        lines.append("- 条目多给的键（示例里没有，属容忍范围）：")
        lines.extend(_counter_lines(bucket["itemExtra"], bucket["items"], "个条目"))
        lines.append("- 枚举越界（提示词里明确枚举过的字段取了别的值）：")
        if not bucket["enumViolations"]:
            lines.append("    （无）")
        for field, counts in sorted(bucket["enumViolations"].items()):
            detail = "、".join(f"{value}（{count} 项）" for value, count in sorted(counts.items(), key=lambda p: -p[1]))
            lines.append(f"    - {field}: {detail}")
        lines.append("")
    lines.append("## 3. 落盘产物参考对照（已经过 Python 归一化，仅作参考）")
    lines.append("")
    for finding in persisted:
        comparison = finding["comparison"]
        missing = comparison["item"].get("missing") or {}
        lines.append(f"- {finding['label']}")
        if comparison["empty"]:
            lines.append("    - 产物为空")
        if missing:
            lines.append(
                "    - 条目缺键：" +
                "、".join(
                    f"{key} 缺 {count}/{comparison['item'].get('itemCount')}"
                    for key, count in sorted(missing.items(), key=lambda pair: -pair[1])
                    if count
                )
            )
        if comparison["topMissing"]:
            lines.append(f"    - 顶层缺键：{'、'.join(comparison['topMissing'])}")
        if comparison["emptyLists"]:
            lines.append(f"    - 示例有条目但实际为空：{'、'.join(comparison['emptyLists'])}")
    lines.append("")
    lines.append("## 4. 这些偏差会被形态表记下来吗")
    lines.append("")
    lines.append(
        "（真实偏差 ≠ schema_invalid：形态表有意只收「关键字段」，其余偏差进的是这份清单）"
    )
    lines.append("")
    for agent_id, bucket in sorted(scanned["perAgent"].items()):
        artifact = bucket["artifact"]
        shape = ARTIFACT_SHAPES.get(artifact)
        if shape is None:
            lines.append(f"- {agent_id} / {artifact}：**没有形态表**")
            continue
        required = list(shape.item_fields) + [
            "/".join(group) for group in shape.item_any_fields
        ]
        blind = [
            key for key, count in (bucket["itemMissing"] or {}).items() if count and key not in shape.item_fields
        ]
        notes = [f"形态表必需字段={required or '（无）'}"]
        if blind:
            notes.append(f"真实缺的 {'、'.join(sorted(blind))} 不在必需字段里（不会被记成 schema_invalid）")
        if bucket["enumViolations"]:
            notes.append(
                "枚举越界（" + "、".join(sorted(bucket["enumViolations"])) + "）目前没有任何检查"
            )
        if not shape.list_required:
            notes.append("list_required=False：整体为空/失败返回的 `{}` 也不会被记")
        lines.append(f"- {agent_id} / {artifact}：" + "；".join(notes))
    lines.append("")
    lines.append("## 5. 覆盖缺口")
    lines.append("")
    uncovered = [
        spec.agent_id
        for spec in AGENT_SPECS
        if spec.agent_id not in scanned["perAgent"]
    ]
    lines.append(f"- 本批真实数据里没有返回记录的智能体（{len(uncovered)}/{len(AGENT_SPECS)}）：")
    for agent_id in uncovered:
        spec = resolve_spec(agent_id)
        lines.append(f"  - {agent_id}（{spec.title}，artifact={spec.artifact}）")
    return "\n".join(lines)


def run_scan() -> int:
    """`python -m backend.tests.test_agent_contracts --scan`：出偏差清单。"""

    if not WORKSPACES_JSON.is_file():
        print(f"没有找到真实数据：{WORKSPACES_JSON}（worktree 的 runtime/ 需要灌样本）")
        return 1
    facts, records = collect_agent_records()
    scanned = scan_deviations(records)
    persisted = scan_persisted_artifacts()
    print(render_report(facts, scanned, persisted))
    print("")
    print("## 6. 典型例子")
    print("")
    for agent_id, bucket in sorted(scanned["perAgent"].items()):
        typical = next(
            (
                item
                for item in bucket["comparisons"]
                if item["topMissing"] or item["item"].get("missing")
            ),
            bucket["comparisons"][0],
        )
        missing = {k: v for k, v in (typical["item"].get("missing") or {}).items() if v}
        print(
            f"- {agent_id} @ {typical['source']}：顶层缺键={typical['topMissing'] or '无'}，"
            f"条目 {typical['item'].get('itemCount')} 个，缺键计数={missing or '无'}，"
            f"枚举越界={typical.get('enumViolations') or '无'}"
        )
    return 0


class DeviationScanTests(unittest.TestCase):
    """偏差扫描器的正确性（用合成数据）＋真实数据可用性（缺数据时 skip）。"""

    def test_every_artifact_has_an_example_schema(self) -> None:
        self.assertEqual(sorted(EXAMPLE_SCHEMAS), sorted(ARTIFACT_SHAPES))

    def test_schema_table_matches_the_prompt_constants(self) -> None:
        """示例 schema 必须直接引用提示词常量，不许在测试里另抄一份。"""

        for artifact, schema in EXAMPLE_SCHEMAS.items():
            if artifact == "volumePlan":
                continue
            # 出处可能写成「A / B」（两个形态共用一段产物），取第一个常量名
            source = ARTIFACT_SCHEMA_SOURCES[artifact].split(" / ")[0].split(".")[-1]
            self.assertIs(schema, getattr(prompt_module, source), artifact)

    def test_volume_plan_schema_is_still_inlined_in_the_prompt(self) -> None:
        """已记录的契约缺口：VolumePlanAgent 的示例 schema 只有内联样例，没有常量。"""

        self.assertFalse(
            hasattr(prompt_module, "VOLUME_PLAN_SCHEMA"),
            "补了常量就该改成本文件引用它，并更新偏差清单里的这一条",
        )
        self.assertIn("volumes", VOLUME_PLAN_INLINE_SCHEMA)

    def test_scanner_flags_missing_keys_and_type_mismatch(self) -> None:
        comparison = compare_with_example(
            "OutlinePlanAgent",
            {
                "plan": [
                    {"sectionId": "t-01", "title": "技术方案", "level": 2, "order": "1"},
                ]
            },
        )
        # 顶层：示例里的 warnings 没给（outlinePlan 的示例每个条目都有它）
        self.assertEqual(comparison["topMissing"], ["warnings"])
        self.assertEqual(comparison["topTypeMismatch"], [], "顶层类型都对，问题在条目级")
        missing = {key: count for key, count in comparison["item"]["missing"].items() if count}
        self.assertEqual(missing.get("parentSectionId"), 1)
        self.assertEqual(missing.get("responseType"), 1)
        self.assertNotIn("title", missing, "条目给了的键不该算缺")

    def test_scanner_flags_top_level_type_mismatch(self) -> None:
        comparison = compare_with_example("OutlinePlanAgent", {"plan": {"不是": "数组"}})
        self.assertEqual(
            comparison["topTypeMismatch"],
            [{"key": "plan", "expected": "array", "actual": "object"}],
        )

    def test_scanner_ignores_unknown_keys_and_runtime_sections(self) -> None:
        comparison = compare_with_example(
            "OutlinePlanAgent",
            {
                "plan": [{"title": "技术方案", "未来新增字段": 1}],
                "warnings": [],
                "validation": {"ok": True},
                "runState": {"node": {}},
            },
        )
        self.assertEqual(comparison["topMissing"], [])
        self.assertEqual(comparison["unknownTop"], [])
        self.assertEqual(comparison["topTypeMismatch"], [])

    def test_scanner_reports_empty_output(self) -> None:
        comparison = compare_with_example("RequirementResponseTableAgent", {})
        self.assertTrue(comparison["empty"])

    def test_scanner_flags_empty_list_where_the_example_has_items(self) -> None:
        """示例给了条目、实际是空数组：类型正确但产物没出来。"""

        comparison = compare_with_example(
            "RequirementResponseTableAgent", {"rows": [], "warnings": []}
        )
        self.assertEqual(comparison["emptyLists"], ["rows"])
        self.assertEqual(comparison["topTypeMismatch"], [])
        self.assertFalse(comparison["empty"])

    def test_scanner_flags_enum_violations(self) -> None:
        """枚举越界是真实存在的（§3.1 要求集中归一化），必须被扫出来。"""

        comparison = compare_with_example(
            "OutlinePlanAgent",
            {
                "plan": [
                    {"title": "技术方案", "source": "ai_derived", "responseType": "narrative", "kind": "technical"},
                    {"title": "合规章节", "source": "ai", "responseType": "chapter_text", "kind": "technical"},
                ],
                "warnings": [],
            },
        )
        self.assertEqual(comparison["enumViolations"]["source"], {"ai_derived": 1})
        self.assertEqual(comparison["enumViolations"]["responseType"], {"narrative": 1})
        self.assertNotIn("kind", comparison["enumViolations"])

    def test_schema_objects_are_not_shared_mutable_state(self) -> None:
        """扫描器只读示例 schema，不许就地改写它。"""

        before = json.dumps(prompt_module.OUTLINE_PLAN_SCHEMA, ensure_ascii=False, sort_keys=True)
        compare_with_example("OutlinePlanAgent", {"plan": [{"title": "技术方案"}]})
        after = json.dumps(prompt_module.OUTLINE_PLAN_SCHEMA, ensure_ascii=False, sort_keys=True)
        self.assertEqual(before, after)

    @unittest.skipUnless(WORKSPACES_JSON.is_file(), "本机没有 runtime/workspaces.json，跳过真实数据扫描")
    def test_real_workspaces_scan_runs_and_covers_the_recorded_agents(self) -> None:
        facts, records = collect_agent_records()
        self.assertEqual(facts["workspaces"], 6, "本机样本应有 6 个真实工作区")
        self.assertTrue(records, "worktree 里应该已经灌过真实工作区样本")
        scanned = scan_deviations(records)
        self.assertEqual(sum(item["records"] for item in scanned["perAgent"].values()), len(records))
        self.assertEqual(
            [agent for agent, bucket in scanned["perAgent"].items() if not bucket["artifact"]],
            [],
            "真实数据里出现了未登记（或未声明 artifact）的智能体",
        )
        for bucket in scanned["perAgent"].values():
            for comparison in bucket["comparisons"]:
                self.assertTrue(comparison["checked"], comparison["agentId"])


if __name__ == "__main__":
    if "--scan" in sys.argv:
        sys.exit(run_scan())
    unittest.main()
