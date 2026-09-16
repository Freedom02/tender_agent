# -*- coding: utf-8 -*-
"""证据装配器：把各章节 sourceRefs 的 evidencePath 解析为可插图资产。

输出 ``{evidencePath: MediaAsset-as-dict}``（用 asdict 展平，供 assemble 层按
evidencePath 取用，避免组装层直接依赖 dataclass）。去重、跳过空路径；单个
资产解析失败不中断整体，store.fetch 内部已降级或返回 None。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from ..media_store.base import MediaStore


def assemble_evidence(source_refs: list[dict[str, Any]], store: MediaStore) -> dict[str, dict[str, Any]]:
    """把一组 sourceRef 解析为 {evidencePath: asset-dict}。"""
    assets: dict[str, dict[str, Any]] = {}
    for ref in source_refs:
        if not isinstance(ref, dict):
            continue
        key = str(ref.get("evidencePath") or "").strip()
        if not key or key in assets:
            continue
        try:
            asset = store.fetch(ref)
        except Exception:
            asset = None
        if asset is not None:
            assets[key] = asdict(asset)
    return assets
