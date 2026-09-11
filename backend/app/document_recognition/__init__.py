"""企业材料本体与存储模块（模板收集模式）。

只保留本体 schema（ontology_defs）+ 存储契约（storage：关系定义、边表生成、画像聚合）。
企业材料事实以 M3 图库为单一事实源，本地不再落 company-materials.json（只保留证明材料文件）。
"""
from __future__ import annotations

from .storage import RELATIONSHIPS, FACT_LIST_KEYS, ID_FIELD_BY_KEY, build_edges, build_profile

__all__ = ["RELATIONSHIPS", "FACT_LIST_KEYS", "ID_FIELD_BY_KEY", "build_edges", "build_profile"]
