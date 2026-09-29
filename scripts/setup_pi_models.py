#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 pi CLI 的 provider 注册表（``~/.pi/agent/models.json``）。

背景
----
后端通过 ``pi --provider <p> --model <m>`` 调用 Pi CLI（Node 包
``@earendil-works/pi-coding-agent``，见 ``backend/app/pi_agent/client.py``）。
pi 需要知道请求该发往哪个网关，这份信息放在 ``<agent-dir>/models.json``
（默认 ``~/.pi/agent/models.json``，可用 ``PI_CODING_AGENT_DIR`` 覆盖）。

格式（pi 官方 docs/models.md「Configure a compatible endpoint」）::

    {"providers": {"<id>": {"baseUrl": ..., "api": "openai-completions",
                            "apiKey": "$ENV_NAME", "models": [{"id": ...}]}}}

密钥**不落盘**：写入 ``models.json`` 的是 ``$COMPANY_LLM_API_KEY`` 这类环境变量
插值（pi 支持 ``$NAME`` / ``${NAME}``），真实密钥由后端在拉起子进程时注入
（``client.py`` 里的 ``COMPANY_LLM_API_KEY``）。

用法
----
    python scripts/setup_pi_models.py
    python scripts/setup_pi_models.py --dry-run
    python scripts/setup_pi_models.py --provider company-ai --base-url https://gw.example/v1

配置来源（按优先级）
--------------------
    网关地址: --base-url > 环境变量 PI_GATEWAY_BASE_URL > .env 的 AI_BASE_URL
              > agent-config.json 的 ai.baseUrl（缺版本段时自动补 /v1）
    查询密钥: 环境变量 AI_API_KEY > .env 的 AI_API_KEY（仅用于拉模型清单）

写入 ``~/.pi/agent/`` 属于工作区之外的操作，受限环境下需要提权执行。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DEFAULT_PROVIDER = "company-ai"
DEFAULT_API_KEY_REF = "$COMPANY_LLM_API_KEY"
_FETCH_TIMEOUT = 15.0

# 明显不是对话模型的，不进注册表（避免污染 /model 选择器）
_NON_CHAT_MARKERS = (
    "embedding",
    "tts",
    "whisper",
    "rerank",
    "asr",
    "moderation",
    "ocr",
    "image",
    "video",
)


def parse_env_file(path: Path) -> dict[str, str]:
    """读取 .env（只在缺少环境变量时作为兜底）。"""

    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def normalize_base_url(base_url: str) -> str:
    """OpenAI 兼容网关的 ``/models`` 位于版本段下（如 ``/v1/models``）。

    实测 ``https://token.uino.com/models`` 返回前端 HTML，而
    ``https://token.uino.com/v1/models`` 才是模型清单，所以缺版本段时补 ``/v1``。
    """

    cleaned = (base_url or "").strip().rstrip("/")
    if not cleaned:
        return ""
    tail = cleaned.rsplit("/", 1)[-1].lower()
    if tail[:1] == "v" and tail[1:2].isdigit():
        return cleaned
    return f"{cleaned}/v1"


def project_ai_config() -> dict:
    """读 agent-config.json 的 ai 段（用于兜底网关地址 + 收集在用的模型名）。"""

    path = REPO_ROOT / "agent-config.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    ai = payload.get("ai") if isinstance(payload, dict) else None
    return ai if isinstance(ai, dict) else {}


def configured_model_ids(ai: dict) -> list[str]:
    """项目配置里实际引用的模型名，必须保证已登记。"""

    ids: list[str] = []
    for key in ("model", "piModel", "extractionModel", "matchingModel"):
        value = str(ai.get(key) or "").strip()
        if value and value.lower() != "auto" and value not in ids:
            ids.append(value)
    mcp = ai.get("workbuddyAgentMcp")
    if isinstance(mcp, dict):
        value = str(mcp.get("model") or "").strip()
        if value and value.lower() != "auto" and value not in ids:
            ids.append(value)
    return ids


def fetch_gateway_model_ids(base_url: str, api_key: str) -> list[str] | None:
    """拉取网关模型清单；失败返回 None（调用方降级为只登记在用的模型）。"""

    if not base_url:
        return None
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"  ! 查询网关 {url} 失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        print(f"  ! 网关返回格式无法识别（缺少 data 列表）：{url}", file=sys.stderr)
        return None
    return [str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")]


def filter_chat_models(model_ids: list[str]) -> list[str]:
    return sorted(
        {m for m in model_ids if m and not any(marker in m.lower() for marker in _NON_CHAT_MARKERS)}
    )


def registry_path() -> Path:
    """与 pi 及后端 preflight 一致：PI_CODING_AGENT_DIR 或 ~/.pi/agent。"""

    import os

    override = os.getenv("PI_CODING_AGENT_DIR", "").strip()
    return (Path(override) if override else Path.home() / ".pi" / "agent") / "models.json"


def main(argv: list[str] | None = None) -> int:
    import os

    parser = argparse.ArgumentParser(description="生成 pi 的 provider 注册表 models.json")
    parser.add_argument("--provider", default=os.getenv("PI_PROVIDER", "").strip() or DEFAULT_PROVIDER)
    parser.add_argument("--base-url", default="", help="网关地址；留空则按 .env / agent-config.json 推断")
    parser.add_argument("--api-key-ref", default=DEFAULT_API_KEY_REF, help="写入 models.json 的 apiKey（环境变量插值）")
    parser.add_argument("--no-fetch", action="store_true", help="不拉网关清单，只登记项目在用的模型")
    parser.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    args = parser.parse_args(argv)

    env_file = parse_env_file(REPO_ROOT / ".env")
    ai = project_ai_config()

    base_url = (
        args.base_url.strip()
        or os.getenv("PI_GATEWAY_BASE_URL", "").strip()
        or env_file.get("AI_BASE_URL", "")
        or str(ai.get("baseUrl") or "")
    )
    base_url = normalize_base_url(base_url)
    if not base_url:
        print("x 无法确定网关地址：请用 --base-url 指定，或在 .env 里配置 AI_BASE_URL", file=sys.stderr)
        return 2

    api_key = os.getenv("AI_API_KEY", "").strip() or env_file.get("AI_API_KEY", "")

    required = configured_model_ids(ai)
    declared: list[str] = []
    if not args.no_fetch:
        fetched = fetch_gateway_model_ids(base_url, api_key)
        if fetched is not None:
            print(f"网关 {base_url} 返回 {len(fetched)} 个模型")
            declared = filter_chat_models(fetched)
    declared = sorted(set(declared) | set(required))

    missing = [m for m in required if m not in declared]
    if missing:
        print(f"  ! 项目在用但清单里仍缺失：{', '.join(missing)}", file=sys.stderr)
        declared = sorted(set(declared) | set(missing))

    document = {
        "providers": {
            args.provider: {
                "baseUrl": base_url,
                "api": "openai-completions",
                "apiKey": args.api_key_ref,
                "models": [{"id": model_id} for model_id in declared],
            }
        }
    }

    target = registry_path()
    print(f"provider   : {args.provider}")
    print(f"baseUrl    : {base_url}")
    print(f"apiKey     : {args.api_key_ref}（环境变量插值，密钥不落盘）")
    print(f"模型数     : {len(declared)}")
    print(f"项目在用   : {', '.join(required) or '(无)'}")
    print(f"目标文件   : {target}")

    if args.dry_run:
        print("--dry-run：未写入。内容如下：")
        print(json.dumps(document, ensure_ascii=False, indent=2))
        return 0

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # 显式 UTF-8 无 BOM：带 BOM 会让 json.loads 直接解析失败
        target.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"x 写入失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("  （~/.pi/agent/ 在工作区之外，受限环境需提权运行）", file=sys.stderr)
        return 1

    print(f"已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
