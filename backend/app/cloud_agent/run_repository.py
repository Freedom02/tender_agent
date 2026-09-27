from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..runtime_lock import runtime_file_lock


class RunRepository:
    """Small JSON-backed repository for local development.

    Writes are atomic and serialized in-process. The interface is deliberately
    storage-agnostic so it can later be backed by SQLite/PostgreSQL.
    """

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = runtime_root
        self.path = runtime_root / "bid_runs.json"
        self._lock = threading.RLock()
        self._file_lock_path = runtime_root / ".bid-runs.lock"

    def _read(self) -> dict[str, dict[str, Any]]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, value: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def create(self, run: dict[str, Any]) -> dict[str, Any]:
        with self._lock, runtime_file_lock(self._file_lock_path):
            records = self._read()
            records[str(run["runId"])] = deepcopy(run)
            self._write(records)
            return deepcopy(run)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with self._lock, runtime_file_lock(self._file_lock_path):
            value = self._read().get(run_id)
            return deepcopy(value) if isinstance(value, dict) else None

    def update(self, run_id: str, **changes: Any) -> dict[str, Any] | None:
        with self._lock, runtime_file_lock(self._file_lock_path):
            records = self._read()
            current = records.get(run_id)
            if not isinstance(current, dict):
                return None
            current.update(changes)
            records[run_id] = current
            self._write(records)
            return deepcopy(current)
