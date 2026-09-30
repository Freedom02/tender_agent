# -*- coding: utf-8 -*-
"""标书生成 MCP：把「标书输入读取 + 章节导出」暴露为外部 Agent 可调用的工具。

本包不写入任何既有运行时数据；唯一产出是 `runtime/bid_writing/{workspaceId}/`
这一自有目录下的标书草稿与导出文档。
"""
from .reader import BidDataError
from .server import MCP_PROTOCOL_VERSION, MCP_SERVER_VERSION, BidWritingMCPServer, serve_stdio

__all__ = (
    "BidDataError",
    "BidWritingMCPServer",
    "serve_stdio",
    "MCP_SERVER_VERSION",
    "MCP_PROTOCOL_VERSION",
)
