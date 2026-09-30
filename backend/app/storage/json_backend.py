"""JSON 文件后端：与现状完全一致的行为。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import StorageBackend


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def _write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) if compact
        else json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


class JsonBackend(StorageBackend):
    name = "json"

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.catalog_path = runtime_root / "catalog.json"
        self.libraries_path = runtime_root / "libraries.json"
        self.workspaces_path = runtime_root / "workspaces.json"
        self.deleted_workspaces_path = runtime_root / "workspaces-deleted.json"
        self.reminders_path = runtime_root / "reminders.json"
        self.runs_path = runtime_root / "automation-runs.json"

    def load_catalog(self) -> list[dict[str, Any]]:
        return _read_json(self.catalog_path, [])

    def save_catalog(self, value: list[dict[str, Any]]) -> None:
        _write_json(self.catalog_path, value, compact=True)

    def load_libraries(self) -> dict[str, Any]:
        return _read_json(self.libraries_path, {})

    def save_libraries(self, value: dict[str, Any]) -> None:
        _write_json(self.libraries_path, value)

    def load_workspaces(self) -> dict[str, Any]:
        value = _read_json(self.workspaces_path, {})
        return value if isinstance(value, dict) else {}

    def save_workspaces(self, value: dict[str, Any]) -> None:
        _write_json(self.workspaces_path, value, compact=True)

    def load_deleted_workspaces(self) -> list[str]:
        value = _read_json(self.deleted_workspaces_path, []) or []
        return [str(item) for item in value if str(item)]

    def save_deleted_workspaces(self, value: list[str]) -> None:
        _write_json(self.deleted_workspaces_path, sorted(value))

    def load_reminders(self) -> list[dict[str, Any]]:
        return _read_json(self.reminders_path, [])

    def save_reminders(self, value: list[dict[str, Any]]) -> None:
        _write_json(self.reminders_path, value)

    def load_automation_runs(self) -> list[dict[str, Any]]:
        return _read_json(self.runs_path, [])

    def save_automation_runs(self, value: list[dict[str, Any]]) -> None:
        _write_json(self.runs_path, value)
