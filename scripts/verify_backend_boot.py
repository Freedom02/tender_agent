#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""真实启动后端 ASGI 应用并探测接口（不只是 import 成功）。

import 成功不代表服务能起：启动阶段（lifespan）可能连数据库 / 读配置 / 注册
路由。本脚本用 FastAPI TestClient 真实走一遍启动生命周期，再实际请求无必填
参数的 GET 接口，报出每个端点的 HTTP 状态码。

用法
----
    python scripts/verify_backend_boot.py
    python scripts/verify_backend_boot.py --limit 20
    python scripts/verify_backend_boot.py --json

退出码: 0 = 启动成功且探测到的接口没有 5xx；1 = 启动失败或有 5xx。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
# app.main 里用相对路径读配置时以工作目录为准；保持与 `python backend/run.py` 从仓库根启动一致
os.chdir(REPO_ROOT)


def _no_required_query_params(route) -> bool:
    try:
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            return True
        return not any(p.required for p in dependant.query_params)
    except Exception:  # noqa: BLE001
        return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="真实启动后端并探测接口")
    parser.add_argument("--limit", type=int, default=12, help="最多探测多少个无参 GET（默认 12）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 汇总")
    args = parser.parse_args(argv)

    try:
        from app.main import app
    except BaseException as exc:  # noqa: BLE001
        print(f"x app.main 构建失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    route_count = len(app.routes)

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        print(f"x TestClient 不可用（需要 httpx）：{exc}", file=sys.stderr)
        return 2

    results: list[dict] = []
    server_errors = 0

    with TestClient(app) as client:
        # 1) OpenAPI 自省
        try:
            resp = client.get("/openapi.json")
            openapi_ok = resp.status_code == 200
            openapi_paths = len(resp.json().get("paths", {})) if openapi_ok else 0
            results.append({"path": "/openapi.json", "status": resp.status_code})
        except BaseException as exc:  # noqa: BLE001
            openapi_ok, openapi_paths = False, 0
            results.append({"path": "/openapi.json", "status": f"异常 {type(exc).__name__}"})
            server_errors += 1

        # 2) 探测无参 GET
        probed = 0
        for route in app.routes:
            path = getattr(route, "path", "")
            methods = getattr(route, "methods", set()) or set()
            if "GET" not in methods or "{" in path or path == "/openapi.json":
                continue
            if not _no_required_query_params(route):
                continue
            try:
                resp = client.get(path)
                code = resp.status_code
            except BaseException as exc:  # noqa: BLE001
                code = f"异常 {type(exc).__name__}"
            if isinstance(code, int) and code >= 500:
                server_errors += 1
            results.append({"path": path, "status": code})
            probed += 1
            if probed >= args.limit:
                break

    ok_count = sum(1 for r in results if isinstance(r["status"], int) and r["status"] < 500)

    if args.json:
        print(json.dumps({
            "routes": route_count,
            "openapiPaths": openapi_paths,
            "probed": results,
            "serverErrors": server_errors,
        }, ensure_ascii=False, indent=2))
    else:
        print(f"app 构建成功，路由数 = {route_count}，OpenAPI 接口数 = {openapi_paths}")
        print(f"探测 {len(results)} 个接口：通过 {ok_count} / 5xx {server_errors}")
        for r in results:
            status = r["status"]
            mark = "✓" if isinstance(status, int) and status < 500 else "x"
            print(f"  {mark} GET {r['path']} -> {status}")

    return 1 if server_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
