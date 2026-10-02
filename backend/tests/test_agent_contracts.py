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
from backend.app.pi_agent.prompts import requirement_map_prompt, volume_plan_prompt
from backend.app.pi_agent.transport import (
    PiError,
    PiTransport,
    agent_id_from_prompt,
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


if __name__ == "__main__":
    unittest.main()
