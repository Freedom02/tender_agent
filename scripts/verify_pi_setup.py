#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""验证 Pi 智能体配置：解析结果 + 后端自检 + 网关端点诊断。

这是 ``GET /api/config/pi/preflight`` 的命令行版本，额外补了两块后端自检
覆盖不到的信息：

1. **解析结果**——``PI_MODEL > ai.piModel > ai.model`` 的最终取值，以及
   ``command`` 能否在 PATH 里找到。这一层最容易踩的坑是 ``PI_WORKING_DIR``
   没指向仓库根：``pi_agent/config.py`` 只从 ``PI_WORKING_DIR`` 或当前工作
   目录找 ``agent-config.json``，找不到就静默把 model 解析成空字符串。
2. **网关端点诊断**——当自检报「网关不提供 / 查不到模型」时，分别打
   ``{base}/models`` 与 ``{base}/v1/models``，指出哪一个才是真清单
   （缺 ``/v1`` 时很多网关会返回前端 HTML，表现为 JSONDecodeError）。

用法
----
    python scripts/verify_pi_setup.py
    python scripts/verify_pi_setup.py --json
    python scripts/verify_pi_setup.py --base-url https://gw.example/v1
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def _probe_endpoint(url: str, api_key: str, timeout: float = 10.0) -> str:
    """返回该端点的可读结论（不打印密钥）。"""

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {str(exc)[:70]}"
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return f"200 但非 JSON（前 60 字符: {body[:60]!r}）—— 大概率是前端页面，端点不对"
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return "200 JSON 但缺少 data 列表"
    ids = [str(i.get("id")) for i in data if isinstance(i, dict) and i.get("id")]
    preview = ", ".join(ids[:6]) + ("..." if len(ids) > 6 else "")
    return f"200 JSON，{len(ids)} 个模型：{preview}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="验证 Pi 智能体配置")
    parser.add_argument("--json", action="store_true", help="输出原始自检报告 JSON")
    parser.add_argument("--base-url", default="", help="额外诊断用的网关地址（覆盖自检结果）")
    parser.add_argument("--no-gateway", action="store_true", help="跳过网关端点诊断（不发网络请求）")
    args = parser.parse_args(argv)

    # 让 agent-config.json 能被找到（脚本可能从任意目录被调用）
    os.environ.setdefault("PI_WORKING_DIR", str(REPO_ROOT))

    try:
        from app.pi_agent.config import PiConfig
        from app.pi_agent.preflight import check_pi_model
    except Exception as exc:  # noqa: BLE001
        print(f"x 无法导入后端 pi_agent 模块：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    config = PiConfig.from_env()
    report = check_pi_model()

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1

    print("=" * 62)
    print("解析结果（PI_MODEL > ai.piModel > ai.model）")
    print("=" * 62)
    resolved = shutil.which(config.command) or shutil.which(f"{config.command}.cmd")
    rows = [
        ("enabled", config.enabled),
        ("command", f"{config.command!r}  →  {resolved or 'PATH 里找不到'}"),
        ("provider", config.provider or "(空)"),
        ("model", config.model or "(空 ← 会报「未配置 Pi 模型」)"),
        ("api_key", "<已设置>" if config.api_key else "<空>"),
        ("working_dir", config.working_dir or "(空 ← 回落到当前工作目录)"),
        ("当前工作目录", os.getcwd()),
        ("注册表路径", report.get("piRegistryPath")),
    ]
    for label, value in rows:
        print(f"  {label:14}: {value}")

    print()
    print("=" * 62)
    print("后端自检（零 token 消耗）")
    print("=" * 62)
    for key in ("modelConfigured", "declaredInPiRegistry", "gatewayServesModel"):
        print(f"  {key:22} = {report.get(key)}")
    for problem in report.get("problems", []):
        print(f"  x {problem}")
    for warning in report.get("warnings", []):
        print(f"  ! {warning}")
    if report.get("suggestions"):
        print(f"  · 同系列可选：{', '.join(report['suggestions'])}")
    print(f"  => {report.get('summary')}")

    gateway_base = args.base_url.strip() or str(report.get("gatewayBaseUrl") or "")
    if not args.no_gateway and gateway_base:
        print()
        print("=" * 62)
        print("网关端点诊断")
        print("=" * 62)
        api_key = config.api_key
        for candidate in (gateway_base.rstrip("/"), f"{gateway_base.rstrip('/')}/v1"):
            print(f"  {candidate + '/models':52} -> {_probe_endpoint(candidate + '/models', api_key)}")

    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
