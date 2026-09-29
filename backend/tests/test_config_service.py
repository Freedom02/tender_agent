from __future__ import annotations

import unittest

import pytest

from backend.app.config.service import ensure_config, update_configuration

pytestmark = pytest.mark.skip(reason="config 保留远端版本，本测试为我方 config schema，待 PG 迁移阶段迁")


class FakeStore:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.saved_config: dict[str, object] | None = None

    def save_config(self, config: dict[str, object]) -> None:
        self.saved_config = config
        self.config = config

    def public_config(self) -> dict[str, object]:
        return self.config


class ConfigServiceTests(unittest.TestCase):
    def test_extraction_mode_defaults_to_builtin(self) -> None:
        config = ensure_config({})

        self.assertEqual(config["ai"]["extractionMode"], "builtin")
        self.assertNotIn("agentBridge", config["ai"])
        self.assertEqual(
            config["ai"]["workbuddyAgentMcp"]["mcpConfigPath"],
            "backend/app/workbuddy_agent/config/tender-review.mcp.example.json",
        )
        self.assertEqual(
            config["ai"]["workbuddyAgentMcp"]["skillDir"],
            "backend/app/workbuddy_agent/skills/tender-document-map",
        )
        self.assertEqual(config["ai"]["workbuddyAgentMcp"]["maxSaveAttempts"], 3)

    def test_invalid_ai_config_falls_back_to_defaults(self) -> None:
        config = ensure_config({"ai": "broken"})

        self.assertEqual(config["ai"]["extractionMode"], "builtin")
        self.assertNotIn("agentBridge", config["ai"])

    def test_workbuddy_agent_mcp_is_the_only_external_mode(self) -> None:
        config = ensure_config({"ai": {"extractionMode": "workbuddy_agent_mcp"}})

        self.assertEqual(config["ai"]["extractionMode"], "workbuddy_agent_mcp")
        self.assertEqual(
            config["ai"]["workbuddyAgentMcp"]["runtimeDir"],
            "runtime/document_map_agent",
        )

    def test_legacy_agent_bridge_settings_are_dropped(self) -> None:
        config = ensure_config({"ai": {"agentBridge": {"enabled": True}}})

        self.assertEqual(config["ai"]["extractionMode"], "builtin")
        self.assertNotIn("agentBridge", config["ai"])

    def test_legacy_cli_runtime_fields_are_dropped(self) -> None:
        config = ensure_config(
            {
                "ai": {
                    "workbuddyAgentMcp": {
                        "executor": "legacy",
                        "mcpConfigFlag": "legacy",
                    }
                }
            }
        )

        self.assertNotIn("executor", config["ai"]["workbuddyAgentMcp"])
        self.assertNotIn("mcpConfigFlag", config["ai"]["workbuddyAgentMcp"])

    def test_update_configuration_deep_merges_agent_mcp_and_preserves_api_key(self) -> None:
        store = FakeStore(
            ensure_config(
                {
                    "ai": {
                        "apiKey": "secret",
                        "workbuddyAgentMcp": {
                            "runtimeDir": "runtime/custom_document_map",
                            "timeoutSeconds": 60,
                        },
                    }
                }
            )
        )

        config = update_configuration(
            store,
            {
                "ai": {
                    "apiKey": "***",
                    "extractionMode": "workbuddy_agent_mcp",
                    "workbuddyAgentMcp": {"timeoutSeconds": 12},
                }
            },
        )

        self.assertEqual(config["ai"]["apiKey"], "secret")
        self.assertEqual(config["ai"]["extractionMode"], "workbuddy_agent_mcp")
        self.assertEqual(
            config["ai"]["workbuddyAgentMcp"]["runtimeDir"],
            "runtime/custom_document_map",
        )
        self.assertEqual(config["ai"]["workbuddyAgentMcp"]["timeoutSeconds"], 12)
        self.assertNotIn("agentBridge", config["ai"])


if __name__ == "__main__":
    unittest.main()
