"""A 组企业事实的当前实现：M3 直连（无本地 JSON 持久化）。

通过注入的 loader 调 m3.sync.read_facts_from_m3，避免 store.py 与 m3 的
循环依赖；pg_impl 阶段换成「读 PG 只读镜像」即可。
"""
from __future__ import annotations

from typing import Any, Callable


class M3FactsRepository:
    def __init__(self, loader: Callable[[], dict[str, Any]]):
        self._loader = loader

    def load(self) -> dict[str, Any]:
        return self._loader()
