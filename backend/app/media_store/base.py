# -*- coding: utf-8 -*-
"""媒体资产契约与适配器接口（与底层存储解耦）。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

KIND_IMAGE = "image"          # 可直接 add_picture 插入的图片
KIND_PDF = "pdf"              # PDF（未转图时的标记；转图后按 image 处理）
KIND_ATTACHMENT = "attachment"  # 其它文件 / 无法渲染，只出附件占位


@dataclass
class MediaAsset:
    """一个已解析、可插入 Word 的图文资产。

    ``asset_id`` 即 evidencePath（稳定键），标书组装层按它从装配结果里取用。
    """
    asset_id: str          # 稳定键（evidencePath）
    kind: str              # image / pdf / attachment
    local_path: str        # 可插入的图片路径（image 时必填；PDF 转图后填渲染 PNG）
    caption: str           # 图注 / 附件名
    page_count: int        # 证据文件夹内文件数（PDF 页数近似）
    width_cm: float        # 建议插图宽度
    source_path: str = ""  # 原始文件路径（未转图前）


class MediaStore(Protocol):
    """图文库适配器协议。

    只暴露一个方法：把一个 sourceRef（含 evidencePath / factKey / factId / name）
    解析成 MediaAsset。解析失败一律返回 None 或降级为 attachment，不向上抛异常，
    保证导出流程不被单个坏文件拖垮。
    """
    def fetch(self, ref: dict[str, Any]) -> MediaAsset | None:
        ...
