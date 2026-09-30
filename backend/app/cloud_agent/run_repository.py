from __future__ import annotations

import json
import logging
import os
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..runtime_lock import runtime_file_lock

logger = logging.getLogger(__name__)

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover
    psycopg = None
    Jsonb = None


def _adapt(value: Any) -> Any:
    if Jsonb is not None and isinstance(value, (dict, list)):
        return Jsonb(value)
    return value


def _to_tstz(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


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


class PostgresRunRepository:
    """bid_runs 的 PostgreSQL 实现，接口与 RunRepository 完全一致。"""

    def __init__(self, dsn: str) -> None:
        if psycopg is None:
            raise RuntimeError("psycopg 未安装：pip install 'psycopg[binary]>=3.1'")
        self.dsn = dsn

    @staticmethod
    def _row(run: dict[str, Any]) -> dict[str, Any]:
        return {
            "run_id": str(run.get("runId") or ""),
            "workspace_id": run.get("workspaceId"),
            "project_id": run.get("projectId"),
            "provider": run.get("provider"),
            "status": run.get("status"),
            "phase": run.get("phase"),
            "created_at": _to_tstz(run.get("createdAt")),
            "updated_at": _to_tstz(run.get("updatedAt")),
            "parse_bundle_id": run.get("parseBundleId"),
            "payload": run,
        }

    def _upsert(self, run: dict[str, Any]) -> None:
        row = self._row(run)
        if not row["run_id"]:
            return
        cols = list(row.keys())
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c != "run_id")
        ph = ", ".join(["%s"] * len(cols))
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"INSERT INTO bid_runs ({', '.join(cols)}) VALUES ({ph}) "
                f"ON CONFLICT (run_id) DO UPDATE SET {updates}",
                [_adapt(row[c]) for c in cols],
            )

    def create(self, run: dict[str, Any]) -> dict[str, Any]:
        self._upsert(run)
        return deepcopy(run)

    def get(self, run_id: str) -> dict[str, Any] | None:
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM bid_runs WHERE run_id = %s", (run_id,))
            row = cur.fetchone()
        return deepcopy(row[0]) if row and isinstance(row[0], dict) else None

    def update(self, run_id: str, **changes: Any) -> dict[str, Any] | None:
        if not changes:
            return self.get(run_id)
        # 单条 UPDATE + jsonb 合并，天然原子，不需要额外的进程内锁。
        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE bid_runs
                   SET payload    = payload || %s::jsonb,
                       status     = COALESCE(%s, status),
                       phase      = COALESCE(%s, phase),
                       updated_at = COALESCE(%s, updated_at)
                 WHERE run_id = %s
             RETURNING payload
                """,
                (
                    json.dumps(changes, ensure_ascii=False),
                    changes.get("status"),
                    changes.get("phase"),
                    _to_tstz(changes.get("updatedAt")),
                    run_id,
                ),
            )
            row = cur.fetchone()
        return deepcopy(row[0]) if row and isinstance(row[0], dict) else None


class DualRunRepository:
    """读走 primary（JSON），写同时落 primary + mirror（DB）；mirror 失败不影响主流程。"""

    def __init__(self, primary: RunRepository, mirror: PostgresRunRepository) -> None:
        self.primary = primary
        self.mirror = mirror

    def _mirror_call(self, method: str, *args: Any, **kwargs: Any) -> None:
        try:
            getattr(self.mirror, method)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            logger.warning("dual 模式：bid_runs %s 写 DB 失败（不影响 JSON 主流程）：%s", method, exc)

    def create(self, run: dict[str, Any]) -> dict[str, Any]:
        result = self.primary.create(run)
        self._mirror_call("create", run)
        return result

    def get(self, run_id: str) -> dict[str, Any] | None:
        return self.primary.get(run_id)

    def update(self, run_id: str, **changes: Any) -> dict[str, Any] | None:
        result = self.primary.update(run_id, **changes)
        self._mirror_call("update", run_id, **changes)
        return result


def create_run_repository(runtime_root: Path) -> RunRepository | PostgresRunRepository | DualRunRepository:
    """按 STORE_BACKEND 选择 bid_runs 的存储实现，与 Store 的后端保持同一开关。"""
    mode = os.environ.get("STORE_BACKEND", "json").strip().lower()
    if mode not in ("db", "dual"):
        return RunRepository(runtime_root)

    dsn = os.environ.get("PG_DSN", "").strip()
    if not dsn:
        if mode == "db":
            raise RuntimeError("STORE_BACKEND=db 需要设置 PG_DSN 环境变量")
        logger.warning("STORE_BACKEND=dual 但未设置 PG_DSN，bid_runs 退化为 json 模式")
        return RunRepository(runtime_root)

    if mode == "dual":
        return DualRunRepository(RunRepository(runtime_root), PostgresRunRepository(dsn))
    return PostgresRunRepository(dsn)
