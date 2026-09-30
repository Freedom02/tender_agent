"""Single source of truth for autonomous WorkBuddy agent defaults."""
from __future__ import annotations

from copy import deepcopy
from typing import Any


WORKBUDDY_AGENT_SKILL_DIR = (
    "backend/app/workbuddy_agent/skills/tender-document-map"
)
WORKBUDDY_AGENT_MCP_CONFIG_PATH = (
    "backend/app/workbuddy_agent/config/tender-review.mcp.example.json"
)
WORKBUDDY_AGENT_RUNTIME_DIR = "runtime/document_map_agent"

DEFAULT_DOCUMENT_MAP_AGENT_CONFIG: dict[str, Any] = {
    "skillDir": WORKBUDDY_AGENT_SKILL_DIR,
    "mcpConfigPath": WORKBUDDY_AGENT_MCP_CONFIG_PATH,
    "runtimeDir": WORKBUDDY_AGENT_RUNTIME_DIR,
    # Keep the model provider-neutral. Deployments can override it through
    # agent-config.json or WORKBUDDY_MODEL.
    "model": "auto",
    "codebuddyCodePath": "",
    # Do not inherit arbitrary user MCP/settings by default. A deployment
    # that intentionally needs user-scoped model settings can opt in with
    # settingSources=["user"] in its local, ignored configuration.
    "settingSources": [],
    # The agent only receives the restricted Tender Review MCP tools.
    # Keep the SDK's normal permission checks for portable deployments.
    "permissionMode": "default",
    "maxTurns": 40,
    "maxToolCalls": 40,
    "maxSaveAttempts": 3,
    "timeoutSeconds": 600,
    "maxRetries": 1,
    "requireMapValidation": True,
    "requireRejectionScan": True,
    "fallbackMode": "builtin",
}


def default_document_map_agent_config() -> dict[str, Any]:
    """Return a detached copy suitable for application config merging."""

    return deepcopy(DEFAULT_DOCUMENT_MAP_AGENT_CONFIG)


__all__ = [
    "DEFAULT_DOCUMENT_MAP_AGENT_CONFIG",
    "WORKBUDDY_AGENT_MCP_CONFIG_PATH",
    "WORKBUDDY_AGENT_RUNTIME_DIR",
    "WORKBUDDY_AGENT_SKILL_DIR",
    "default_document_map_agent_config",
]
