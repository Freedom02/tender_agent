"""Development entry point for the bid-writing MCP server.

Speaks newline-delimited JSON-RPC over stdio.  Unlike the document-bound
Tender Review MCP, this server needs no bootstrap envelope: it reads the
project's own runtime artifacts (``runtime/workspaces.json`` and the evidence
folder) through narrowed, read-only tools.

Run with the interpreter that has the backend dependencies installed
(``python-docx`` is required by ``export_bid_docx``).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.bid_writing_mcp.server import BidWritingMCPServer, serve_stdio


def _configure_utf8_stdio() -> None:
    """Keep JSON-RPC text UTF-8 across Windows subprocess code pages."""

    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def main() -> int:
    _configure_utf8_stdio()
    serve_stdio(BidWritingMCPServer())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
