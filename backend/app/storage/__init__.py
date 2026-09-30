"""存储后端工厂。

通过环境变量选择后端：
- STORE_BACKEND=json  （默认）—— 现状，JSON 文件
- STORE_BACKEND=dual           —— 读 JSON、双写 JSON + DB（过渡期）
- STORE_BACKEND=db             —— 读库写库（PostgreSQL 为唯一事实源）

PG_DSN 形如 postgresql://user:password@127.0.0.1:5432/tender_agent_dev

PG_SCHEMA 可选：队友脚手架表所在的 schema（默认 public）。
  本项目把队友的 16 张表建在独立的 `partner` schema 里，与我方 44 张目标表
  （public）物理隔离 —— 见 backend/migrations/010_partner_scaffold.sql。
  用 db/dual 模式时设 PG_SCHEMA=partner 即可。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import StorageBackend
from .dual_backend import DualBackend
from .json_backend import JsonBackend
from .postgres_backend import PostgresBackend

logger = logging.getLogger(__name__)


def create_backend(runtime_root: Path) -> StorageBackend:
    mode = os.environ.get("STORE_BACKEND", "json").strip().lower()
    dsn = os.environ.get("PG_DSN", "").strip()
    schema = os.environ.get("PG_SCHEMA", "").strip() or None

    if mode == "db":
        if not dsn:
            raise RuntimeError("STORE_BACKEND=db 需要设置 PG_DSN 环境变量")
        return PostgresBackend(dsn, schema=schema)

    if mode == "dual":
        json_backend = JsonBackend(runtime_root)
        if not dsn:
            logger.warning("STORE_BACKEND=dual 但未设置 PG_DSN，退化为 json 模式")
            return json_backend
        return DualBackend(json_backend, PostgresBackend(dsn, schema=schema))

    if mode not in ("", "json"):
        raise RuntimeError(f"未知 STORE_BACKEND：{mode}（可选 json / dual / db）")

    return JsonBackend(runtime_root)
