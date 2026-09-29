"""Pi 智能体模型可用性自检（启动时 / 配置页手动触发）。

检查三项，全部是**零 token 消耗**的判定：
  1. pi 命令是否可执行；
  2. 配置的模型是否已在 pi 注册表（~/.pi/agent/models.json）登记；
  3. 网关（baseUrl）是否真的提供该模型（GET /v1/models）。

结果会被缓存；``public_config`` 只读缓存（绝不发起网络请求），
``GET /api/config/pi/preflight`` 才真正重查。任何异常都被吞掉，
自检永远不能拖垮启动或配置页。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import PiConfig, _project_ai_config

_CACHE_LOCK = threading.Lock()
_CACHED: dict[str, Any] | None = None

_GATEWAY_TIMEOUT_SECONDS = 8.0


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def pi_agent_dir() -> Path:
    """pi 配置目录：PI_CODING_AGENT_DIR 或 ~/.pi/agent（与 pi CLI 一致）。"""

    override = os.getenv("PI_CODING_AGENT_DIR", "").strip()
    if override:
        return Path(override)
    return Path.home() / ".pi" / "agent"


def _provider_entry(provider: str) -> tuple[dict[str, Any] | None, str]:
    """读取 models.json 里该 provider 的配置（含 baseUrl / apiKey / models）。"""

    path = pi_agent_dir() / "models.json"
    if not path.is_file():
        return None, f"未找到 {path}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"{path} 解析失败：{exc}"
    providers = payload.get("providers") if isinstance(payload, dict) else None
    if not isinstance(providers, dict):
        return None, f"{path} 缺少 providers 段"
    entry = providers.get(provider)
    if not isinstance(entry, dict):
        return None, f"{path} 未声明 provider {provider!r}"
    return entry, ""


def _declared_model_ids(provider: str) -> tuple[list[str] | None, str]:
    entry, reason = _provider_entry(provider)
    if entry is None:
        return None, reason
    models = entry.get("models")
    if not isinstance(models, list):
        return [], ""
    return [str(m.get("id")) for m in models if isinstance(m, dict) and m.get("id")], ""


def _gateway_model_ids(base_url: str, api_key: str, timeout: float) -> tuple[list[str] | None, str]:
    if not base_url:
        return None, "未配置网关 baseUrl"
    url = base_url.rstrip("/") + "/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 —— 网络/网关问题不视为硬失败
        return None, f"查询网关 {url} 失败：{type(exc).__name__}: {exc}"
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None, "网关返回格式无法识别（缺少 data 列表）"
    return [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")], ""


def _alpha_prefix(model_id: str) -> str:
    match = re.match(r"[a-zA-Z]+", str(model_id).strip())
    return match.group(0).lower() if match else ""


def _suggest_gateway_models(configured: str, gateway_ids: list[str], limit: int = 8) -> list[str]:
    prefix = _alpha_prefix(configured)
    if not prefix:
        return []
    candidates = [mid for mid in gateway_ids if _alpha_prefix(mid) == prefix and mid != configured]
    candidates.sort(key=lambda mid: (abs(len(mid) - len(configured)), mid.lower()))
    return candidates[:limit]


def check_pi_model(*, gateway_timeout: float = _GATEWAY_TIMEOUT_SECONDS) -> dict[str, Any]:
    report: dict[str, Any] = {
        "checkedAt": _now(),
        "enabled": True,
        "command": "",
        "commandFound": True,
        "provider": "",
        "model": "",
        "modelConfigured": False,
        "declaredInPiRegistry": None,
        "piRegistryPath": str(pi_agent_dir() / "models.json"),
        "gatewayServesModel": None,
        "gatewayBaseUrl": "",
        "gatewayModelCount": 0,
        "ok": True,
        "problems": [],
        "warnings": [],
        "suggestions": [],
        "summary": "",
    }

    try:
        config = PiConfig.from_env()
    except Exception as exc:  # noqa: BLE001
        report["ok"] = False
        report["enabled"] = False
        report["problems"].append(f"读取 Pi 配置失败：{type(exc).__name__}: {exc}")
        report["summary"] = "Pi 配置读取失败"
        return report

    report["enabled"] = config.enabled
    report["command"] = config.command
    report["provider"] = config.provider
    report["model"] = config.model

    if not config.enabled:
        report["summary"] = "Pi 智能体未启用"
        return report

    # 1) pi 命令可执行？
    try:
        resolved = shutil.which(config.command) or shutil.which(f"{config.command}.cmd")
    except Exception:  # noqa: BLE001
        resolved = None
    report["commandFound"] = bool(resolved)
    if not resolved:
        report["ok"] = False
        report["problems"].append(f"未找到 pi 命令：{config.command}")

    # 2) 是否配置了模型
    report["modelConfigured"] = bool(config.model)
    if not config.model:
        report["ok"] = False
        report["problems"].append(
            "未配置 Pi 模型：.env 的 PI_MODEL 与 agent-config.json 的 ai.piModel 均为空"
        )
        report["summary"] = "未配置 Pi 模型"
        return report

    # 3) pi 注册表是否登记了该模型
    declared: list[str] | None = None
    if config.provider:
        declared, reason = _declared_model_ids(config.provider)
        if declared is not None:
            report["declaredInPiRegistry"] = config.model in declared
            if not report["declaredInPiRegistry"]:
                report["ok"] = False
                detail = f"{config.model!r} 未在 pi 注册表登记（{config.provider!r} 仅声明了 {declared or '无模型'}）"
                report["problems"].append(detail)
        else:
            report["warnings"].append(f"无法读取 pi 注册表：{reason}")

    # 4) 网关是否提供该模型
    entry, _ = _provider_entry(config.provider) if config.provider else (None, "")
    base_url = ""
    if isinstance(entry, dict):
        base_url = str(entry.get("baseUrl") or "").strip()
    if not base_url:
        base_url = str(_project_ai_config().get("baseUrl") or "").strip()
    report["gatewayBaseUrl"] = base_url

    gateway_ids, gw_reason = _gateway_model_ids(base_url, config.api_key, gateway_timeout)
    if gateway_ids is None:
        report["warnings"].append(gw_reason)
    else:
        report["gatewayModelCount"] = len(gateway_ids)
        report["gatewayServesModel"] = config.model in gateway_ids
        if not report["gatewayServesModel"]:
            report["ok"] = False
            report["problems"].append(f"网关不提供模型 {config.model!r}")
            suggestions = _suggest_gateway_models(config.model, gateway_ids)
            if suggestions:
                report["suggestions"] = suggestions
                report["problems"].append(f"网关同系列可选：{', '.join(suggestions)}")

    if report["ok"]:
        report["summary"] = f"配置正常：{config.provider or '(默认)'} / {config.model}"
    else:
        report["summary"] = "、".join(report["problems"]) or "自检发现问题"

    return report


def get_report(*, refresh: bool = False, gateway_timeout: float = _GATEWAY_TIMEOUT_SECONDS) -> dict[str, Any]:
    """返回自检报告；refresh=False 时优先返回缓存，避免重复网络请求。"""

    global _CACHED
    if not refresh:
        with _CACHE_LOCK:
            if _CACHED is not None:
                return _CACHED
    report = check_pi_model(gateway_timeout=gateway_timeout)
    with _CACHE_LOCK:
        _CACHED = report
    return report


def cached_report() -> dict[str, Any] | None:
    """仅返回缓存（绝不发起网络请求）；未自检过返回 None。"""

    with _CACHE_LOCK:
        return _CACHED
