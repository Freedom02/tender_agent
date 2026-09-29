from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.pi_agent.config import PiConfig


class PiConfigResolutionTests(unittest.TestCase):
    """回归：Pi 智能体的模型不得在代码里硬编码，必须由配置驱动。

    历史实现把 `gpt-5.5` 写死在 from_env() 里（provider == company-ai 时兜底），
    网关没有该模型时就只能 503 model_not_found，且从配置上无从改起。
    现在的优先级：PI_MODEL > ai.piModel > ai.model（与匹配模块同一模型）> 空。
    """

    def _write_config(self, directory: str, ai: dict) -> None:
        Path(directory, "agent-config.json").write_text(
            json.dumps({"ai": ai}, ensure_ascii=False),
            encoding="utf-8",
        )

    def _resolve(self, directory: str, **env: str) -> PiConfig:
        merged = {"PI_WORKING_DIR": directory, "PI_PROVIDER": "", "PI_MODEL": ""}
        merged.update(env)
        with patch.dict(os.environ, merged, clear=False):
            return PiConfig.from_env()

    def test_falls_back_to_shared_model_instead_of_hardcode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_config(
                directory,
                {
                    "baseUrl": "https://token.uino.com/v1",
                    "apiKey": "k",
                    "model": "qwen-turbo",
                },
            )
            config = self._resolve(directory)

            self.assertEqual(config.model, "qwen-turbo")
            self.assertEqual(config.provider, "company-ai")

    def test_unconfigured_model_stays_empty(self) -> None:
        """没有任何模型配置时必须是空，而不是兜到一个写死的模型名。"""

        with tempfile.TemporaryDirectory() as directory:
            self._write_config(
                directory,
                {"baseUrl": "https://token.uino.com/v1", "apiKey": "k"},
            )
            config = self._resolve(directory)

            self.assertEqual(config.model, "")

    def test_pi_model_field_wins_over_shared_model(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_config(
                directory,
                {
                    "baseUrl": "https://token.uino.com/v1",
                    "apiKey": "k",
                    "model": "qwen-turbo",
                    "piModel": "gpt-4.1",
                },
            )
            config = self._resolve(directory)

            self.assertEqual(config.model, "gpt-4.1")

    def test_env_model_overrides_config_field(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_config(
                directory,
                {
                    "baseUrl": "https://token.uino.com/v1",
                    "apiKey": "k",
                    "piModel": "gpt-4.1",
                },
            )
            config = self._resolve(directory, PI_MODEL="claude-sonnet")

            self.assertEqual(config.model, "claude-sonnet")

    def test_pi_provider_field_is_honoured(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self._write_config(
                directory,
                {
                    "baseUrl": "https://token.uino.com/v1",
                    "apiKey": "k",
                    "model": "qwen-turbo",
                    "piProvider": "custom-gateway",
                },
            )
            config = self._resolve(directory)

            self.assertEqual(config.provider, "custom-gateway")


if __name__ == "__main__":
    unittest.main()
