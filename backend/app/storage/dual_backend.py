"""双写后端：读走 primary（JSON），写同时落到 primary + mirror（DB）。

mirror 写失败只记日志、绝不抛异常——过渡期 DB 是影子，绝不能拖垮主流程。
"""
from __future__ import annotations

import logging
from typing import Any

from .base import StorageBackend

logger = logging.getLogger(__name__)


class DualBackend(StorageBackend):
    name = "dual"

    def __init__(self, primary: StorageBackend, mirror: StorageBackend) -> None:
        self.primary = primary
        self.mirror = mirror

    def _mirror(self, method: str, value: Any) -> None:
        try:
            getattr(self.mirror, method)(value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dual 模式：%s 写 DB 失败（不影响 JSON 主流程）：%s", method, exc)

    def load_catalog(self) -> list[dict[str, Any]]:
        return self.primary.load_catalog()

    def save_catalog(self, value: list[dict[str, Any]]) -> None:
        self.primary.save_catalog(value)
        self._mirror("save_catalog", value)

    def load_libraries(self) -> dict[str, Any]:
        return self.primary.load_libraries()

    def save_libraries(self, value: dict[str, Any]) -> None:
        self.primary.save_libraries(value)
        self._mirror("save_libraries", value)

    def load_workspaces(self) -> dict[str, Any]:
        return self.primary.load_workspaces()

    def save_workspaces(self, value: dict[str, Any]) -> None:
        self.primary.save_workspaces(value)
        self._mirror("save_workspaces", value)

    def load_deleted_workspaces(self) -> list[str]:
        return self.primary.load_deleted_workspaces()

    def save_deleted_workspaces(self, value: list[str]) -> None:
        self.primary.save_deleted_workspaces(value)
        self._mirror("save_deleted_workspaces", value)

    def load_reminders(self) -> list[dict[str, Any]]:
        return self.primary.load_reminders()

    def save_reminders(self, value: list[dict[str, Any]]) -> None:
        self.primary.save_reminders(value)
        self._mirror("save_reminders", value)

    def load_automation_runs(self) -> list[dict[str, Any]]:
        return self.primary.load_automation_runs()

    def save_automation_runs(self, value: list[dict[str, Any]]) -> None:
        self.primary.save_automation_runs(value)
        self._mirror("save_automation_runs", value)

    def close(self) -> None:
        self.mirror.close()
