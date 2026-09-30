#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""导入后端全部 ``app.*`` 子模块，一次性收集所有导入错误。

合并 / 重构 / 迁移之后用来快速拿到「断点清单」。相较 pytest 的好处是
**不在第一个错误处停下**：pytest 收集阶段撞到一个 ``ImportError`` 只会报那一处，
本脚本会把每个坏掉的模块都列出来，并给出「缺模块」还是「缺符号」的区分。

用法
----
    python scripts/verify_imports.py
    python scripts/verify_imports.py --quiet
    python scripts/verify_imports.py --package app.tender_matching

退出码: 0 = 全部可导入；1 = 存在失败。
"""

from __future__ import annotations

import argparse
import importlib
import pkgutil
import sys
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"

if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))


def iter_module_names(package: str) -> list[str]:
    """先 import 顶层包，再用 pkgutil 递归列出全部子模块。"""

    try:
        root = importlib.import_module(package)
    except BaseException as exc:  # noqa: BLE001 —— 顶层就坏了，直接报
        raise RuntimeError(f"无法导入顶层包 {package}: {type(exc).__name__}: {exc}") from exc

    paths = list(getattr(root, "__path__", []) or [])
    if not paths:
        return [package]
    found = [package]
    for module_info in pkgutil.walk_packages(paths, prefix=f"{package}."):
        found.append(module_info.name)
    return sorted(set(found))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导入全部 app.* 子模块并汇总错误")
    parser.add_argument("--package", default="app", help="要扫描的顶层包（默认 app）")
    parser.add_argument("--quiet", action="store_true", help="只打印失败项，不打印成功计数")
    parser.add_argument("--traceback", action="store_true", help="打印完整 traceback")
    args = parser.parse_args(argv)

    try:
        names = iter_module_names(args.package)
    except RuntimeError as exc:
        print(f"x {exc}", file=sys.stderr)
        return 1

    failures: list[tuple[str, str]] = []
    for name in names:
        try:
            importlib.import_module(name)
        except BaseException as exc:  # noqa: BLE001 —— 任何异常都算断点，继续扫下一个
            detail = f"{type(exc).__name__}: {exc}"
            failures.append((name, detail))
            if args.traceback:
                print(f"--- {name} ---")
                traceback.print_exc()

    total = len(names)
    print(f"扫描 {total} 个模块：成功 {total - len(failures)} / 失败 {len(failures)}")
    if failures:
        print()
        print("失败明细（模块缺失 与 符号缺失 要分开看）：")
        for name, detail in failures:
            hint = ""
            lowered = detail.lower()
            if "no module named" in lowered:
                hint = "  ← 缺少依赖或模块路径不对"
            elif "cannot import name" in lowered:
                hint = "  ← 模块在、但符号没了（典型：合并时漏了远端新增的函数）"
            print(f"  x {name}")
            print(f"      {detail}{hint}")
        return 1

    if not args.quiet:
        print("全部模块可导入。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
