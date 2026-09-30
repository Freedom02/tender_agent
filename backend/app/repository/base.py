"""Repository 接口契约（Protocol）。

设计原则（阶段 2 的关键约束）：
- 接口贴**现有 JSON 聚合边界**（facts / catalog / workspace），不贴 48 张表粒度。
  因为 json_impl 必须零行为变更，接口要贴当前数据形状；切 pg_impl 时，pg_impl
  内部才把这 3 个聚合拆成第一批 28 张表。
- 阶段 2 只实现 json_impl（+ M3 facts）；pg_impl 与 dual 留空占位。

对应关系（第一批 28 表 = A + B + C + D + E）：
- FactsRepository     → A 组 10 表（企业事实，M3 权威 → PG 只读镜像）
- CatalogRepository   → B 组公告（tender_announcement / announcement_match_term / source_site）
- WorkspaceRepository → B+C+D+E（tender_project / tender_document / document_* /
                        requirement_* / match_* / review_record，一个 workspace 跨这些表）
"""
from __future__ import annotations

from typing import Any, Protocol


class FactsRepository(Protocol):
    """A 组：企业事实。当前实现是 M3 直连（无本地 JSON 持久化）。"""

    def load(self) -> dict[str, Any]:
        """返回 8 类 CamelCase 事实（同 m3.sync.read_facts_from_m3 的返回结构）。"""
        ...


class CatalogRepository(Protocol):
    """B 组公告：catalog.json（list[dict]，一条公告一个 dict）。"""

    def load(self) -> list[dict[str, Any]]:
        ...

    def save(self, items: list[dict[str, Any]]) -> None:
        ...


class WorkspaceRepository(Protocol):
    """B+C+D+E：workspaces.json（dict[workspace_id, workspace]）。

    注意：一个 workspace 对象同时承载 project/document(解析)/analysisPack(提取)/
    fineReview(匹配)，阶段 2 当整体读写；pg_impl 阶段再拆 28 张表。
    """

    def load(self) -> dict[str, dict[str, Any]]:
        ...

    def save(self, workspaces: dict[str, dict[str, Any]]) -> None:
        ...
