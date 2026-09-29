from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.pi_agent import preflight


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


def _gateway(ids: list[str]) -> _FakeResponse:
    return _FakeResponse(json.dumps({"data": [{"id": i} for i in ids]}).encode("utf-8"))


class PiPreflightTests(unittest.TestCase):
    """启动自检要能在"撰写之前"就把模型配错暴露出来。

    典型场景：模型在 pi 注册表里登记了、但网关根本不提供（历史 bug：
    models.json 写 gpt-5.5，网关无此模型 → 直到点自动撰写才 503）。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.agent_dir = Path(self._tmp.name, "agent")
        self.agent_dir.mkdir()
        self.work_dir = Path(self._tmp.name, "project")
        self.work_dir.mkdir()
        self._write_ai_config({"baseUrl": "https://gw.example/v1", "apiKey": "k", "model": "qwen-turbo"})
        self._env = patch.dict(
            os.environ,
            {
                "PI_CODING_AGENT_DIR": str(self.agent_dir),
                "PI_WORKING_DIR": str(self.work_dir),
                "PI_ENABLED": "true",
                "PI_COMMAND": sys.executable,
                "PI_PROVIDER": "company-ai",
                "PI_MODEL": "deepseek-v4-pro",
                "PI_API_KEY": "k",
            },
            clear=False,
        )
        self._env.start()
        preflight._CACHED = None

    def tearDown(self) -> None:
        self._env.stop()
        preflight._CACHED = None
        self._tmp.cleanup()

    def _write_ai_config(self, ai: dict) -> None:
        (self.work_dir / "agent-config.json").write_text(
            json.dumps({"ai": ai}, ensure_ascii=False), encoding="utf-8"
        )

    def _write_pi_registry(self, ids: list[str]) -> None:
        (self.agent_dir / "models.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "company-ai": {
                            "baseUrl": "https://gw.example/v1",
                            "api": "openai-completions",
                            "apiKey": "$COMPANY_LLM_API_KEY",
                            "models": [{"id": i} for i in ids],
                        }
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_healthy_model_reports_ok(self) -> None:
        self._write_pi_registry(["deepseek-v4-pro"])
        with patch("urllib.request.urlopen", return_value=_gateway(["deepseek-v4-pro", "qwen-turbo"])):
            report = preflight.check_pi_model()

        self.assertTrue(report["ok"], report)
        self.assertEqual(report["model"], "deepseek-v4-pro")
        self.assertTrue(report["declaredInPiRegistry"])
        self.assertTrue(report["gatewayServesModel"])
        self.assertEqual(report["problems"], [])

    def test_model_absent_from_gateway_is_hard_problem(self) -> None:
        """历史 bug 的回归：登记了但网关没有 → 必须判失败并给出同系列建议。"""

        patch_env = patch.dict(os.environ, {"PI_MODEL": "gpt-5.5"}, clear=False)
        patch_env.start()
        self.addCleanup(patch_env.stop)
        self._write_pi_registry(["gpt-5.5"])
        with patch(
            "urllib.request.urlopen",
            return_value=_gateway(["gpt-5.6-sol", "gpt-5.6-luna", "qwen-turbo"]),
        ):
            report = preflight.check_pi_model()

        self.assertFalse(report["ok"])
        self.assertFalse(report["gatewayServesModel"])
        self.assertTrue(report["declaredInPiRegistry"])
        self.assertTrue(any("网关不提供" in p for p in report["problems"]))
        self.assertIn("gpt-5.6-sol", report["suggestions"])

    def test_model_absent_from_pi_registry_is_hard_problem(self) -> None:
        self._write_pi_registry(["gpt-5.5"])
        with patch("urllib.request.urlopen", return_value=_gateway(["deepseek-v4-pro"])):
            report = preflight.check_pi_model()

        self.assertFalse(report["ok"])
        self.assertFalse(report["declaredInPiRegistry"])
        self.assertTrue(any("未在 pi 注册表登记" in p for p in report["problems"]))

    def test_gateway_unreachable_is_warning_not_failure(self) -> None:
        self._write_pi_registry(["deepseek-v4-pro"])
        with patch("urllib.request.urlopen", side_effect=OSError("network down")):
            report = preflight.check_pi_model()

        self.assertTrue(report["ok"], report)
        self.assertIsNone(report["gatewayServesModel"])
        self.assertTrue(report["warnings"])

    def test_missing_model_is_reported(self) -> None:
        self._write_ai_config({"baseUrl": "https://gw.example/v1", "apiKey": "k"})
        patch_env = patch.dict(os.environ, {"PI_MODEL": ""}, clear=False)
        patch_env.start()
        self.addCleanup(patch_env.stop)

        report = preflight.check_pi_model()

        self.assertFalse(report["ok"])
        self.assertFalse(report["modelConfigured"])
        self.assertTrue(any("未配置 Pi 模型" in p for p in report["problems"]))

    def test_missing_pi_command_is_reported(self) -> None:
        patch_env = patch.dict(os.environ, {"PI_COMMAND": "definitely-not-a-real-pi-command"}, clear=False)
        patch_env.start()
        self.addCleanup(patch_env.stop)
        self._write_pi_registry(["deepseek-v4-pro"])

        report = preflight.check_pi_model()

        self.assertFalse(report["ok"])
        self.assertFalse(report["commandFound"])
        self.assertTrue(any("未找到 pi 命令" in p for p in report["problems"]))

    def test_disabled_pi_skips_checks(self) -> None:
        patch_env = patch.dict(os.environ, {"PI_ENABLED": "false"}, clear=False)
        patch_env.start()
        self.addCleanup(patch_env.stop)

        report = preflight.check_pi_model()

        self.assertTrue(report["ok"])
        self.assertFalse(report["enabled"])

    def test_cache_avoids_repeat_gateway_queries(self) -> None:
        self._write_pi_registry(["deepseek-v4-pro"])
        with patch("urllib.request.urlopen", return_value=_gateway(["deepseek-v4-pro"])) as gateway:
            first = preflight.get_report()
            second = preflight.get_report()
            self.assertEqual(gateway.call_count, 1)

            preflight.get_report(refresh=True)
            self.assertEqual(gateway.call_count, 2)

        self.assertTrue(first["ok"])
        self.assertIs(second, first)


if __name__ == "__main__":
    unittest.main()
