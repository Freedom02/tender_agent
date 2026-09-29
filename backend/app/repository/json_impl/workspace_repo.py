"""B+C+D+E 的当前实现：直接读写 runtime/workspaces.json（零行为变更）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..io import read_json, write_json


class JsonWorkspaceRepository:
    def __init__(self, path: Path):
        self._path = path

    def load(self) -> dict[str, dict[str, Any]]:
        return read_json(self._path, {})

    def save(self, workspaces: dict[str, dict[str, Any]]) -> None:
        write_json(self._path, workspaces, compact=True)
