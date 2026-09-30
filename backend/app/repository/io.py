"""共享的 JSON 文件读写工具（从 store.py 迁出，供 json_impl 与 store.py 共用）。

迁移注意：这两个函数是全项目 JSON 落盘的唯一出口，改这里影响所有数据读写。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) if compact
        else json.dumps(value, ensure_ascii=False, indent=2)
    )
    # 原子写入：先写同目录临时文件再原子替换，避免大文件写入中途被
    # 进程重启/并发读打断，留下半截文件或触发 Windows OSError EINVAL。
    tmp = path.with_name(path.name + f".{uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
