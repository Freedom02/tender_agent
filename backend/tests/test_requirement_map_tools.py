# -*- coding: utf-8 -*-
"""RequirementMapAgent 只读工具层测试（Wave 1 · 会话 B）。

这组测试钉住三件事（不依赖 Pi、不依赖 runtime/ 下的本机数据）：
  1. 工具快照的内容与边界：本批条款、目录章节、评分项，裁剪上限，无密钥；
  2. 工具面本身：扩展只注册 4 个只读工具，只读一个环境变量指定的快照，
     不注册 read/bash/write/edit 等内置工具，任何地方都不出现 API Key；
  3. `map_requirements` 的两条路径：
     - 工具层可用：短提示词（体积远小于旧的全量视图）+ 白名单工具 + 快照环境变量，
       调用结束后临时快照被清理；
     - 工具层不可用/关掉：提示词与改造前**逐字节相同**（`requirement_map_prompt`），
       保证任何环境都不会因为这次改造失去可用路径。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.pi_agent.agent_context import (
    MAP_TOOL_ALLOWLIST,
    MAP_TOOL_NAMES,
    build_context,
    extension_path,
    resolve_policy,
    write_context,
)
from backend.app.pi_agent.agents import PiAgentMethods
from backend.app.pi_agent.prompts import requirement_map_prompt

ROOT = Path(__file__).resolve().parents[2]
_MARKER = "性能参数与实施要求持续稳定运行"

_ENV_KEYS = ("BID_REQUIREMENT_MAP_POLICY", "BID_REQUIREMENT_MAP_TOOLS")


def _requirement(index: int, *, mandatory: bool = False, is_rejection: bool = False) -> dict:
    text = f"第 {index} 项技术要求：支持数字孪生场景的可视化配置与实时渲染。" + _MARKER * 8
    return {
        "id": f"technical-{index:04d}",
        "requirementNumber": f"2.1.{index}",
        "targetModule": "technical",
        "requirement": text,
        "rawText": text,
        "normalizedRequirement": text,
        "mandatory": mandatory,
        "isRejection": is_rejection,
        "responseType": "chapter_text",
        "sourceLocator": f"第三章 采购需求/2.1/{index}",
    }


def _bundle(count: int = 80) -> dict:
    requirements = [
        _requirement(index, mandatory=index % 7 == 0, is_rejection=index % 23 == 0)
        for index in range(1, count + 1)
    ]
    return {
        "schemaVersion": "1.0",
        "provider": "pi",
        "workspaceId": "ws-test",
        "projectId": "proj-test",
        "projectMeta": {"projectName": "测试项目"},
        "documentStructure": {"sections": [], "warnings": []},
        "submissionStructure": {"volumes": [{"volumeId": "volume-main", "title": "投标响应文件"}]},
        "requirements": requirements,
        "evaluationItems": [
            {"id": "eval-001", "factor": "技术评分", "criteria": "技术方案完整性与可实施性", "score": 30},
            {"id": "eval-002", "factor": "商务评分", "criteria": "类似业绩", "score": 10},
        ],
        "bidFormatSpec": {"requiredOutline": [], "requiredTables": []},
        "rawOutline": [],
        "warnings": [],
    }


def _plan(count: int = 40) -> list[dict]:
    nodes = [
        {
            "sectionId": "volume-1",
            "title": "商务及技术文件",
            "level": 1,
            "nodeType": "volume",
            "responseType": "section_group",
            "order": 1,
            "required": True,
        }
    ]
    for index in range(2, count + 1):
        nodes.append(
            {
                "sectionId": f"sec-{index:02d}",
                "title": f"第 {index} 章 技术响应与实施措施说明",
                "parentSectionId": "volume-1",
                "level": 2,
                "order": index - 1,
                "nodeType": "format_item",
                "responseType": "chapter_text",
                "requirementIds": [f"technical-{index:04d}"],
                "evaluationIds": ["eval-001"],
            }
        )
    return nodes


class _RecordingAgent(PiAgentMethods):
    """不启动 Pi：记录真实发送的提示词与启动参数。"""

    def __init__(self, result: dict | None = None) -> None:
        self.calls: list[dict] = []
        self.context_snapshot: dict | None = None
        self.context_path: str = ""
        self.result = result if isinstance(result, dict) else {
            "coverage": [],
            "uncoveredRequirements": [],
            "duplicatedRequirements": [],
            "warnings": [],
        }

    def run_json(self, prompt, **kwargs):  # type: ignore[override]
        self.calls.append({"prompt": prompt, **kwargs})
        context_path = str((kwargs.get("env_extra") or {}).get("BID_MAP_CONTEXT") or "")
        if context_path:
            self.context_path = context_path
            self.context_snapshot = json.loads(Path(context_path).read_text(encoding="utf-8"))
        return dict(self.result)


class ToolContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {key: os.environ.pop(key, None) for key in _ENV_KEYS}

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_context_carries_batch_chapters_and_evaluations(self) -> None:
        bundle, plan = _bundle(12), _plan(6)
        context = build_context(bundle, plan, workspace_id="ws-test", project_id="proj-test")
        self.assertEqual(context["workspaceId"], "ws-test")
        self.assertEqual(context["projectId"], "proj-test")
        self.assertEqual(
            context["summary"]["batchRequirementIds"],
            [item["id"] for item in bundle["requirements"]],
        )
        self.assertEqual(context["summary"]["batchRequirementCount"], 12)
        self.assertEqual(context["summary"]["chapterCount"], len(plan))
        self.assertEqual(
            [item["sectionId"] for item in context["chapters"]], [row["sectionId"] for row in plan]
        )
        self.assertEqual(context["chapters"][0]["nodeType"], "volume")
        self.assertEqual(context["chapters"][1]["requirementIds"], ["technical-0002"])
        self.assertEqual([item["id"] for item in context["evaluationItems"]], ["eval-001", "eval-002"])
        self.assertEqual(context["summary"]["evaluationItemCount"], 2)

    def test_context_flags_mandatory_and_rejection_and_clips_text(self) -> None:
        context = build_context(_bundle(24), _plan(3))
        by_id = {item["id"]: item for item in context["requirements"]}
        self.assertTrue(by_id["technical-0007"]["mandatory"])
        self.assertFalse(by_id["technical-0008"]["mandatory"])
        self.assertIn("technical-0023", context["summary"]["rejectionIds"])
        self.assertIn("technical-0007", context["summary"]["mandatoryIds"])
        for item in context["requirements"]:
            self.assertLessEqual(len(item["text"]), 200)
            self.assertLessEqual(len(item["rawText"]), 800)
            self.assertTrue(item["inBatch"])
            self.assertEqual(item["domain"], "technical")

    def test_context_ignores_rows_without_ids_and_dedupes(self) -> None:
        bundle = _bundle(4)
        bundle["requirements"] = [
            {"requirement": "没有 ID 的行"},
            {"id": "dup-1", "requirement": "第一条"},
            {"id": "dup-1", "requirement": "重复 ID"},
            "不是字典",
        ]
        context = build_context(bundle, _plan(2))
        self.assertEqual(context["summary"]["batchRequirementIds"], ["dup-1"])
        self.assertEqual(len(context["requirements"]), 1)

    def test_write_context_round_trip_and_no_secrets(self) -> None:
        path = write_context(build_context(_bundle(3), _plan(2), workspace_id="ws-test"))
        try:
            self.assertTrue(path.is_file())
            raw = path.read_text(encoding="utf-8")
            payload = json.loads(raw)
            self.assertEqual(
                sorted(payload.keys()),
                ["chapters", "evaluationItems", "policy", "projectId", "requirements", "summary", "warnings", "workspaceId"],
            )
            self.assertNotIn("sk-", raw)
            self.assertNotIn("apiKey", raw)
        finally:
            shutil.rmtree(path.parent, ignore_errors=True)
            self.assertFalse(path.exists())

    def test_policy_resolution_order(self) -> None:
        self.assertEqual(resolve_policy()["name"], "tools")
        self.assertTrue(resolve_policy()["enabled"])
        with mock.patch.dict(os.environ, {"BID_REQUIREMENT_MAP_POLICY": "legacy"}):
            self.assertFalse(resolve_policy()["enabled"])
        with mock.patch.dict(os.environ, {"BID_REQUIREMENT_MAP_TOOLS": "0"}):
            self.assertFalse(resolve_policy()["enabled"])
        with mock.patch.dict(os.environ, {"BID_REQUIREMENT_MAP_TOOLS": "1"}):
            self.assertTrue(resolve_policy()["enabled"])
        self.assertFalse(
            resolve_policy({"bidWriting": {"requirementMapPolicy": "legacy"}})["enabled"]
        )
        self.assertTrue(resolve_policy({"bidWriting": {"requirementMapPolicy": "tools"}})["enabled"])
        self.assertEqual(resolve_policy({}, override="不认识")["name"], "tools")
        # 环境变量优先于配置文件
        with mock.patch.dict(os.environ, {"BID_REQUIREMENT_MAP_POLICY": "tools"}):
            self.assertTrue(
                resolve_policy({"bidWriting": {"requirementMapPolicy": "legacy"}})["enabled"]
            )


class ToolSurfaceTests(unittest.TestCase):
    """扩展源码与 Python 侧白名单必须一一对应，且工具面最小、只读。"""

    def setUp(self) -> None:
        self.source = extension_path().read_text(encoding="utf-8")

    def test_extension_exists_and_declares_exactly_the_allowlisted_tools(self) -> None:
        self.assertTrue(extension_path().is_file())
        names = re.findall(r'name:\s*"([A-Za-z_][A-Za-z0-9_]*)"', self.source)
        self.assertEqual(names, list(MAP_TOOL_NAMES))
        self.assertEqual(MAP_TOOL_ALLOWLIST, ",".join(MAP_TOOL_NAMES))
        self.assertEqual(len(MAP_TOOL_NAMES), len(set(MAP_TOOL_NAMES)))

    def test_extension_registers_no_builtin_or_write_tools(self) -> None:
        for forbidden in ("bash", "powershell", "write", "edit", "read"):
            self.assertNotRegex(
                self.source,
                rf'name:\s*"{forbidden}"',
                f"扩展注册了不该开放的工具：{forbidden}",
            )
        self.assertEqual(self.source.count("pi.registerTool("), len(MAP_TOOL_NAMES))
        self.assertNotIn("writeFileSync", self.source)
        self.assertNotIn("apiKey", self.source)
        self.assertNotIn("sk-", self.source)

    def test_extension_only_reads_the_injected_snapshot(self) -> None:
        env_names = set(re.findall(r"process\.env\.([A-Za-z_][A-Za-z0-9_]*)", self.source))
        self.assertEqual(env_names, {"BID_MAP_CONTEXT"})
        self.assertEqual(self.source.count("readFileSync("), 1)

    def test_every_tool_declares_parameters(self) -> None:
        blocks = self.source.split("pi.registerTool(")[1:]
        self.assertEqual(len(blocks), len(MAP_TOOL_NAMES))
        for block in blocks:
            self.assertIn("parameters: Type.Object(", block[:600])


class MapRequirementsPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {key: os.environ.pop(key, None) for key in _ENV_KEYS}
        self.bundle = _bundle(80)
        self.plan = _plan(40)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _legacy_prompt(self, bundle: dict | None = None) -> str:
        return requirement_map_prompt(
            parse_bundle=bundle or self.bundle,
            outline_plan=self.plan,
            workspace_id="ws-test",
            project_id="proj-test",
        )

    def test_tool_path_sends_short_prompt_with_read_only_tools(self) -> None:
        agent = _RecordingAgent()
        agent.map_requirements(
            parse_bundle=self.bundle,
            outline_plan=self.plan,
            workspace_id="ws-test",
            project_id="proj-test",
        )
        self.assertEqual(len(agent.calls), 1)
        call = agent.calls[0]
        prompt = call["prompt"]

        # 1) 工具面：只有白名单里的 4 个只读工具，且挂载了扩展
        self.assertEqual(call["tools"], MAP_TOOL_ALLOWLIST)
        self.assertNotIn("read", call["tools"].split(","))
        self.assertNotIn("bash", call["tools"].split(","))
        self.assertEqual(call["extensions"], (str(extension_path()),))
        self.assertEqual(sorted(call["env_extra"].keys()), ["BID_MAP_CONTEXT"])

        # 2) 提示词只带「待办清单 + 章节清单」，不带条款原文
        legacy = self._legacy_prompt()
        self.assertGreater(len(legacy), 25000, "夹具应能代表真实的全量视图提示词")
        self.assertLess(len(prompt), len(legacy) * 0.25)
        self.assertLess(len(prompt.encode("utf-8")), 12000)
        for requirement in self.bundle["requirements"]:
            self.assertIn(requirement["id"], prompt)
        self.assertNotIn(_MARKER, prompt, "条款原文不得内联进提示词")
        self.assertIn("sec-40", prompt)
        self.assertIn("eval-001", prompt)
        for tool in MAP_TOOL_NAMES:
            self.assertIn(tool, prompt)

        # 3) 输出 schema 的字段名与旧提示词一致
        for field in (
            "coverage",
            "requirementId",
            "sectionId",
            "coverageType",
            "status",
            "evaluationIds",
            "uncoveredRequirements",
            "duplicatedRequirements",
            "reason",
        ):
            self.assertIn(field, prompt)

        # 4) 快照就是本批条款，调用后临时目录被清理
        self.assertIsNotNone(agent.context_snapshot)
        assert agent.context_snapshot is not None
        self.assertEqual(
            agent.context_snapshot["summary"]["batchRequirementIds"],
            [item["id"] for item in self.bundle["requirements"]],
        )
        self.assertEqual(agent.context_snapshot["summary"]["chapterCount"], len(self.plan))
        self.assertTrue(agent.context_path)
        self.assertFalse(Path(agent.context_path).exists(), "调用后应清理临时快照")

    def test_prompt_keeps_the_coverage_completeness_contract(self) -> None:
        """反向护栏：改造后「每条本批条款都必须有交代」这条契约不许被删掉。

        工具路径与旧路径的已知差异只允许出现在**归类**上（同一判断落在 coverage 还是
        uncoveredRequirements），不允许出现「既不覆盖也不说明」的条款；这条规则与工具侧的
        `uncovered_batch` 自检共同保证它。
        """

        agent = _RecordingAgent()
        agent.map_requirements(
            parse_bundle=self.bundle,
            outline_plan=self.plan,
            workspace_id="ws-test",
            project_id="proj-test",
        )
        prompt = agent.calls[0]["prompt"]
        self.assertIn("要么在 coverage 里，要么在 uncoveredRequirements 里", prompt)
        self.assertIn("【本批条款待办清单】（共 80 条", prompt)
        extension_source = extension_path().read_text(encoding="utf-8")
        for code in ("uncovered_batch", "unknown_section", "unknown_requirement"):
            self.assertIn(code, extension_source)

    def test_snapshot_is_scoped_to_the_batch_given(self) -> None:
        batch = _bundle(5)
        agent = _RecordingAgent()
        agent.map_requirements(
            parse_bundle=batch,
            outline_plan=self.plan,
            workspace_id="ws-test",
            project_id="proj-test",
        )
        snapshot = agent.context_snapshot
        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["summary"]["batchRequirementCount"], 5)
        self.assertEqual(
            [item["id"] for item in snapshot["requirements"]],
            ["technical-0001", "technical-0002", "technical-0003", "technical-0004", "technical-0005"],
        )

    def test_legacy_policy_reproduces_the_old_prompt_byte_for_byte(self) -> None:
        with mock.patch.dict(os.environ, {"BID_REQUIREMENT_MAP_POLICY": "legacy"}):
            agent = _RecordingAgent()
            agent.map_requirements(
                parse_bundle=self.bundle,
                outline_plan=self.plan,
                workspace_id="ws-test",
                project_id="proj-test",
            )
        call = agent.calls[0]
        self.assertEqual(call["prompt"], self._legacy_prompt())
        self.assertEqual(call["tools"], "")
        self.assertEqual(tuple(call["extensions"]), ())
        self.assertEqual(call["env_extra"], {})
        self.assertIsNone(agent.context_snapshot)

    def test_missing_extension_falls_back_and_reports_a_warning(self) -> None:
        missing = Path(tempfile.gettempdir()) / "no-such-bid-map-tools.ts"
        with mock.patch(
            "backend.app.pi_agent.agent_context.extension_path", return_value=missing
        ):
            agent = _RecordingAgent()
            raw = agent.map_requirements(
                parse_bundle=self.bundle,
                outline_plan=self.plan,
                workspace_id="ws-test",
                project_id="proj-test",
            )
        call = agent.calls[0]
        self.assertEqual(call["prompt"], self._legacy_prompt())
        self.assertEqual(call["tools"], "")
        self.assertTrue(
            any("未找到条款映射工具扩展" in str(item) for item in raw.get("warnings") or []),
            raw.get("warnings"),
        )

    def test_api_key_never_reaches_the_prompt_or_the_snapshot(self) -> None:
        secret = "sk-test-should-never-leak-0123456789"
        with mock.patch.dict(
            os.environ,
            {"PI_API_KEY": secret, "COMPANY_LLM_API_KEY": secret},
        ):
            agent = _RecordingAgent()
            agent.map_requirements(
                parse_bundle=self.bundle,
                outline_plan=self.plan,
                workspace_id="ws-test",
                project_id="proj-test",
            )
        call = agent.calls[0]
        self.assertNotIn(secret, call["prompt"])
        self.assertNotIn(secret, json.dumps(call["env_extra"], ensure_ascii=False))
        self.assertNotIn(secret, json.dumps(agent.context_snapshot, ensure_ascii=False))
        self.assertEqual(sorted(call["env_extra"].keys()), ["BID_MAP_CONTEXT"])


if __name__ == "__main__":
    unittest.main()
