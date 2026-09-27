from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _usable_secret(value: Any) -> str:
    secret = str(value or "").strip()
    if not secret or secret in {"***", "replace-me", "your-api-key"}:
        return ""
    return secret


def _project_ai_config() -> dict[str, Any]:
    """Read the existing project AI config without requiring a second secret."""

    candidates: list[Path] = []
    working_dir = os.getenv("PI_WORKING_DIR", "").strip()
    if working_dir:
        candidates.append(Path(working_dir) / "agent-config.json")
    candidates.append(Path.cwd() / "agent-config.json")

    seen: set[Path] = set()
    for path in candidates:
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved in seen or not resolved.is_file():
            continue
        seen.add(resolved)
        try:
            payload = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        ai = payload.get("ai") if isinstance(payload, dict) else {}
        if isinstance(ai, dict):
            return ai
    return {}


@dataclass(frozen=True)
class PiConfig:
    """Configuration for the locally installed Pi coding agent CLI."""

    enabled: bool
    command: str
    provider: str
    model: str
    api_key: str
    timeout_seconds: float
    working_dir: str

    @classmethod
    def from_env(cls) -> "PiConfig":
        timeout_raw = os.getenv("PI_API_TIMEOUT", "360").strip()
        try:
            timeout = max(float(timeout_raw), 1.0)
        except ValueError:
            timeout = 360.0
        project_ai = _project_ai_config()
        project_api_key = _usable_secret(project_ai.get("apiKey"))
        explicit_provider = os.getenv("PI_PROVIDER", "").strip()
        explicit_model = os.getenv("PI_MODEL", "").strip()
        explicit_api_key = _usable_secret(os.getenv("PI_API_KEY", ""))

        # The project already has a configured OpenAI-compatible enterprise
        # endpoint. Pi knows it as company-ai through ~/.pi/agent/models.json.
        provider = explicit_provider or (
            "company-ai"
            if project_api_key and str(project_ai.get("baseUrl") or "").strip()
            else ""
        )
        model = explicit_model or (
            str(project_ai.get("piModel") or "").strip()
            or ("gpt-5.5" if provider == "company-ai" else "")
        )
        return cls(
            enabled=_env_bool("PI_ENABLED", True),
            command=os.getenv("PI_COMMAND", "pi").strip() or "pi",
            provider=provider,
            model=model,
            api_key=explicit_api_key or project_api_key,
            timeout_seconds=timeout,
            working_dir=os.getenv("PI_WORKING_DIR", "").strip(),
        )
