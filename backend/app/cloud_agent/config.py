from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class DifyConfig:
    """Runtime configuration for the Dify Cloud API.

    Secrets are intentionally read only from the server environment. They are
    never accepted from browser payloads or returned by the API.
    """

    provider: str
    base_url: str
    api_key: str
    parse_workflow_key: str
    section_workflow_key: str
    review_workflow_key: str
    timeout_seconds: float
    enabled: bool

    @classmethod
    def from_env(cls) -> "DifyConfig":
        timeout_raw = os.getenv("DIFY_API_TIMEOUT", "180").strip()
        try:
            timeout = max(float(timeout_raw), 1.0)
        except ValueError:
            timeout = 180.0
        return cls(
            provider=os.getenv("AGENT_PROVIDER", "dify").strip().lower(),
            base_url=os.getenv("DIFY_BASE_URL", "https://api.dify.ai/v1").strip().rstrip("/"),
            api_key=os.getenv("DIFY_API_KEY", "").strip(),
            parse_workflow_key=os.getenv("DIFY_PARSE_WORKFLOW_KEY", "").strip(),
            section_workflow_key=os.getenv("DIFY_SECTION_WORKFLOW_KEY", "").strip(),
            review_workflow_key=os.getenv("DIFY_REVIEW_WORKFLOW_KEY", "").strip(),
            timeout_seconds=timeout,
            enabled=_env_bool("DIFY_ENABLED", True),
        )

    def workflow_key(self, workflow_type: str) -> str:
        return {
            "parse": self.parse_workflow_key,
            "section": self.section_workflow_key,
            "review": self.review_workflow_key,
        }.get(workflow_type, "")
