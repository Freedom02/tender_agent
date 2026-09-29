"""Repository 层：数据读写的地基（阶段 2 起）。

当前（阶段 2）只有 json_impl（+ M3 facts）实现；pg_impl 阶段 3+ 补齐。
详见 docs2/Repository层设计与读写点清单.md。
"""
from __future__ import annotations

from .base import CatalogRepository, FactsRepository, WorkspaceRepository
from .json_impl import JsonCatalogRepository, JsonWorkspaceRepository, M3FactsRepository

__all__ = [
    "CatalogRepository",
    "FactsRepository",
    "WorkspaceRepository",
    "JsonCatalogRepository",
    "JsonWorkspaceRepository",
    "M3FactsRepository",
]
