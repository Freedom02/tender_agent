# -*- coding: utf-8 -*-
"""证据图文媒体资产层：把结构化事实的 evidencePath 解析为可插图资产。

结构化事实（M3）只存 evidencePath（相对 runtime/ 的文件夹路径），真实图文
（扫描件 PDF/图片）在另一个存储。本层提供 MediaStore 适配器接口与本地实现，
标书组装层只认 evidencePath、不感知底层存储 —— 将来换对象存储/数据库时，
替换本包实现即可，上游（匹配层/标书层/装配器）零改动。
"""
from .base import MediaAsset, MediaStore

__all__ = ("MediaAsset", "MediaStore")
