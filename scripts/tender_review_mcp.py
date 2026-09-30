"""Development entry point for the document-bound Tender Review MCP server.

The first stdin line is a caller-owned bootstrap envelope:

    {"document": { ...DocumentIR... }, "run": { ...AgentRun... }}

Remaining lines are standard newline-delimited JSON-RPC messages. Production
code should construct ``TenderReviewMCPServer`` directly after parsing and
authorizing a document; this wrapper never accepts a filesystem path.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.workbuddy_agent.tender_review_mcp import (
    TenderReviewMCPServer,
    serve_stdio,
)


def _configure_utf8_stdio() -> None:
    """Keep JSON-RPC text UTF-8 across Windows subprocess code pages."""

    for stream in (sys.stdin, sys.stdout):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="strict")


def main() -> int:
    _configure_utf8_stdio()
    bootstrap_path = os.environ.get("TENDER_REVIEW_BOOTSTRAP_PATH", "").strip()
    try:
        if bootstrap_path:
            bootstrap = json.loads(
                Path(bootstrap_path).read_text(encoding="utf-8")
            )
        else:
            bootstrap_line = sys.stdin.readline()
            if not bootstrap_line.strip():
                sys.stderr.write(
                    "tender_review_mcp expects a bootstrap JSON object on the first stdin line.\n"
                )
                return 2
            bootstrap = json.loads(bootstrap_line)
        if not isinstance(bootstrap, dict):
            raise ValueError("bootstrap must be a JSON object")
        document_payload = bootstrap.get("document") or bootstrap
    except (json.JSONDecodeError, ValueError) as error:
        sys.stderr.write(f"invalid DocumentIR bootstrap payload: {error}\n")
        return 2
    # Pass the raw payload (not a validated DocumentIR) so the server can defer
    # validation until the first tool call and answer the MCP handshake fast.
    server = TenderReviewMCPServer(
        document_payload,
        run=bootstrap.get("run"),
        artifact_path=os.environ.get("TENDER_REVIEW_ARTIFACT_PATH", "").strip(),
    )
    try:
        serve_stdio(server, stdin=sys.stdin)
    finally:
        server.write_audit_artifact()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
