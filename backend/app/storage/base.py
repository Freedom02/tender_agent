"""结构化状态的读写边界抽象。

只负责「结构化业务状态」的读写；二进制文件（证明材料、资料文件、解析产物、
导出文件）始终留在磁盘上，不经过这里。config（含 API Key）也留在磁盘上。

三个实现：
- JsonBackend       —— 现在的 JSON 文件行为（默认，回滚即此）
- PostgresBackend   —— PostgreSQL 16（唯一事实源）
- DualBackend       —— 读 JSON、双写 JSON + DB（过渡期）
"""
from __future__ import annotations

from typing import Any


class StorageBackend:
    name = "base"

    # ---- 公告目录 catalog.json ----
    def load_catalog(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def save_catalog(self, value: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    # ---- 资料库 libraries.json ----
    def load_libraries(self) -> dict[str, Any]:
        raise NotImplementedError

    def save_libraries(self, value: dict[str, Any]) -> None:
        raise NotImplementedError

    # ---- 工作区 workspaces.json ----
    def load_workspaces(self) -> dict[str, Any]:
        raise NotImplementedError

    def save_workspaces(self, value: dict[str, Any]) -> None:
        raise NotImplementedError

    # ---- 已删工作区墓碑 workspaces-deleted.json ----
    def load_deleted_workspaces(self) -> list[str]:
        raise NotImplementedError

    def save_deleted_workspaces(self, value: list[str]) -> None:
        raise NotImplementedError

    # ---- 跟进提醒 reminders.json ----
    def load_reminders(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def save_reminders(self, value: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    # ---- 自动化运行记录 automation-runs.json ----
    def load_automation_runs(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def save_automation_runs(self, value: list[dict[str, Any]]) -> None:
        raise NotImplementedError

    def close(self) -> None:
        """释放连接等资源（PostgresBackend 可空实现）。"""
        return None
