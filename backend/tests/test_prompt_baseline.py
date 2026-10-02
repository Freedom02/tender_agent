# -*- coding: utf-8 -*-
"""提示词基线护栏：保证提示词内容不会在重构中被悄悄改动。

对应 `backend/tests/baselines/generate_prompt_baseline.py` 的 `--check`。
有任何差异就失败，并提示如何（以及为什么要）更新基线。
"""
from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "backend" / "tests" / "baselines" / "generate_prompt_baseline.py"


class PromptBaselineTests(unittest.TestCase):
    def test_prompt_outputs_match_frozen_baseline(self) -> None:
        result = subprocess.run(
            [sys.executable, str(GENERATOR), "--check"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
        )
        output = (result.stdout or "") + (result.stderr or "")
        self.assertEqual(result.returncode, 0, f"提示词基线不一致：\n{output}")

    def test_baseline_covers_all_prompt_builders(self) -> None:
        """防止新提示词函数漏进基线（漏了就失去护栏意义）。"""

        sys.path.insert(0, str(ROOT / "backend"))
        from app.pi_agent import prompts as module
        from backend.tests.baselines.generate_prompt_baseline import EXCLUDED, _fixtures

        import inspect

        pool = _fixtures()
        builders = [
            name
            for name in dir(module)
            if not name.startswith("_")
            and name not in EXCLUDED
            and inspect.isfunction(getattr(module, name))
            and getattr(module, name).__module__.startswith("app.")
        ]
        uncovered: list[str] = []
        for name in builders:
            parameters = [
                item
                for item in inspect.signature(getattr(module, name)).parameters.values()
                if item.kind is inspect.Parameter.KEYWORD_ONLY
            ]
            if any(
                item.name not in pool and item.default is inspect.Parameter.empty
                for item in parameters
            ):
                uncovered.append(name)
        self.assertEqual(uncovered, [], f"这些提示词函数没有夹具、未纳入基线：{uncovered}")


if __name__ == "__main__":
    unittest.main()
