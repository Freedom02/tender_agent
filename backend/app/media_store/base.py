# -*- coding: utf-8 -*-
"""媒体资产契约与适配器接口（与底层存储解耦）。"""
from __future__ import annotations

from dataclasses import dataclass, field
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
    # 多页/多图：一个 evidencePath 文件夹可能含多张图、多页 PDF、或多个内嵌
    # 截图的 docx。``local_path`` 始终等于 ``page_images[0]``（保持既有消费方
    # 不变），组装层按 ``page_images`` 逐页插入。
    page_images: list[str] = field(default_factory=list)
    # 占位证据：文件夹里只有 ``.keep`` 或说明性的占位 ``.txt``（证据尚未补齐）。
    # 这类目录不得当证据插入，组装层渲染为「待补充证据」。
    placeholder: bool = False
    # 证据文字摘要（docx 解包时提取的标题/首段），用于插图时的图注。
    text_summary: str = ""


class MediaStore(Protocol):
    """图文库适配器协议。

    只暴露一个方法：把一个 sourceRef（含 evidencePath / factKey / factId / name）
    解析成 MediaAsset。解析失败一律返回 None 或降级为 attachment，不向上抛异常，
    保证导出流程不被单个坏文件拖垮。
    """
    def fetch(self, ref: dict[str, Any]) -> MediaAsset | None:
        ...
