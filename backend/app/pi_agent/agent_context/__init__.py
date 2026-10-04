# -*- coding: utf-8 -*-
"""智能体工具上下文包（M2′/M3）。

把「智能体需要用只读工具去查的东西」在 Python 侧一次性快照成小 JSON，
路径经环境变量注入 Pi 子进程，由 `pi_agent/extensions/*.ts` 里注册的只读工具按需查询。
"""
from __future__ import annotations

from .requirement_map_context import (
    MAP_TOOL_ALLOWLIST,
    MAP_TOOL_NAMES,
    REQUIREMENT_MAP_POLICIES,
    build_context,
    extension_path,
    resolve_policy,
    write_context,
)

__all__ = [
    "MAP_TOOL_ALLOWLIST",
    "MAP_TOOL_NAMES",
    "REQUIREMENT_MAP_POLICIES",
    "build_context",
    "extension_path",
    "resolve_policy",
    "write_context",
]
