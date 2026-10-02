# -*- coding: utf-8 -*-
"""Wave 0 · 0.3 回归：client 拆分后的组合关系与 Pi 启动参数。

拆分把 `PiClient` 变成 `PiAgentMethods + PiTransport` 的组合，
这里守住两件最容易在搬迁中悄悄改坏的事：
  1. 组合关系（run_json 来自传输层、agent 方法来自方法层，旧符号仍可导入）；
  2. Pi 启动参数（默认最小能力面；挂工具时必须关内置工具并只放行白名单）。
"""
from __future__ import annotations

import unittest
from pathlib import Path

from backend.app.pi_agent import PiClient, PiError
from backend.app.pi_agent.agents import PiAgentMethods
from backend.app.pi_agent.client import _extract_text_from_message, parse_json_text
from backend.app.pi_agent.transport import PiTransport


class ClientCompositionTests(unittest.TestCase):
    def test_client_composes_transport_and_agent_methods(self) -> None:
        self.assertTrue(issubclass(PiClient, PiTransport))
        self.assertTrue(issubclass(PiClient, PiAgentMethods))
        self.assertIs(PiClient.run_json, PiTransport.run_json)
        self.assertIs(PiClient._command, PiTransport._command)
        self.assertIs(PiClient.plan_outline, PiAgentMethods.plan_outline)
        self.assertIs(PiClient.draft_section, PiAgentMethods.draft_section)

    def test_legacy_imports_still_resolve(self) -> None:
        """store.py / tests 里的旧写法必须继续可用。"""

        from backend.app.pi_agent.client import PiClient as LegacyClient
        from backend.app.pi_agent.client import PiError as LegacyError

        self.assertIs(LegacyClient, PiClient)
        self.assertIs(LegacyError, PiError)
        self.assertTrue(callable(parse_json_text))
        self.assertTrue(callable(_extract_text_from_message))

    def test_agent_surface_is_complete(self) -> None:
        expected = {
            "parse_tender",
            "parse_document_structure",
            "extract_project_meta",
            "extract_requirements",
            "extract_evaluations",
            "detect_templates",
            "review_parse",
            "draft_section",
            "match_materials",
            "plan_section",
            "draft_section_agent",
            "rewrite_section",
            "review_compliance",
            "fill_template_fields",
            "draft_requirement_responses",
            "plan_outline",
            "plan_volumes_agent",
            "plan_outline_agent",
            "map_requirements",
        }
        missing = sorted(name for name in expected if not hasattr(PiClient, name))
        self.assertEqual(missing, [])


class PiCommandTests(unittest.TestCase):
    def _client(self, **overrides: object) -> PiClient:
        from backend.app.pi_agent.config import PiConfig

        fields: dict[str, object] = {
            "enabled": True,
            "command": "pi",
            "provider": "",
            "model": "",
            "api_key": "",
            "timeout_seconds": 360.0,
            "working_dir": "",
        }
        fields.update(overrides)
        return PiClient(PiConfig(**fields))  # type: ignore[arg-type]

    def test_default_launch_disables_every_tool(self) -> None:
        argv = self._client()._command()
        self.assertIn("--no-session", argv)
        self.assertIn("--no-tools", argv)
        self.assertNotIn("--no-builtin-tools", argv)
        self.assertNotIn("--tools", argv)
        self.assertNotIn("--extension", argv)

    def test_tool_launch_restricts_to_whitelist_and_mounts_extensions(self) -> None:
        argv = self._client()._command(
            tools="bid_outline_signals,search_requirements",
            extensions=(Path("/tmp/ext-a.ts"), Path("/tmp/ext-b.ts")),
        )
        self.assertIn("--no-builtin-tools", argv)
        self.assertNotIn("--no-tools", argv)
        self.assertEqual(argv[argv.index("--tools") + 1], "bid_outline_signals,search_requirements")
        mounted = [argv[index + 1] for index, item in enumerate(argv) if item == "--extension"]
        self.assertEqual(len(mounted), 2)
        self.assertTrue(all(item.endswith(".ts") for item in mounted))


if __name__ == "__main__":
    unittest.main()
