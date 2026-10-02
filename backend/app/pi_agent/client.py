# -*- coding: utf-8 -*-
"""Pi 客户端组合入口：`PiClient` = 传输能力 + 智能体方法。

Wave 0 · 0.3 把原来的单文件实现拆成：
  - `transport.py`：进程启动、RPC、超时、JSON 解析（`PiTransport`）
  - `agents.py`  ：各智能体方法（`PiAgentMethods`）
本模块只做组合与对外再导出，`from .client import PiClient` 等旧写法全部不变。
"""
from __future__ import annotations

from .agents import PiAgentMethods
from .transport import (
    PiError,
    _clip,
    _extract_text_from_message,
    parse_json_text,
)
from .transport import PiTransport


class PiClient(PiAgentMethods, PiTransport):
    """Pi 客户端：传输 + 智能体方法。行为与原单文件实现一致。"""

    pass


__all__ = [
    "PiError",
    "_clip",
    "_extract_text_from_message",
    "parse_json_text",
    "PiTransport",
    "PiClient",
]
