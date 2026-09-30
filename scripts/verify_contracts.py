#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""静态扫描 import 契约：区分「模块缺失」与「符号缺失」，不执行任何业务代码。

合并 / 重构后最常见的断层是「远端在某个模块里新增了函数，本地版本没有」，
它要等运行时走到那行 ``from x import f`` 才爆 ``ImportError``。本脚本只做
AST 分析，一次性把这类断层列全：

1. 模块级——``import a.b.c`` / ``from a.b.c import ...`` 的目标模块是否存在；
2. 符号级——``from a.b import f`` 里的 ``f`` 是否真的在 ``a.b`` 中定义
   （含子模块、``__all__``、条件定义 ``if TYPE_CHECKING`` / ``try:`` 块）。

局限：无法覆盖运行期动态生成的属性（如装饰器/元类注入），可能产生少量
误报；对三方库只做「能否解析」检查，不深入符号。

用法
----
    python scripts/verify_contracts.py
    python scripts/verify_contracts.py --root backend/app
    python scripts/verify_contracts.py --root backend --include-tests
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND = REPO_ROOT / "backend"

# 平台专属标准库：Windows / POSIX 各自才有，另一侧必然缺失，属预期而非断层
# （如 runtime_lock.py 在非 Windows 分支 import fcntl，Windows 分支 import msvcrt）。
_PLATFORM_SPECIFIC = frozenset({
    "fcntl", "msvcrt", "termios", "resource", "pwd", "grp", "spwd", "crypt",
    "syslog", "winreg", "win32api", "win32con", "win32file", "win32event",
})


@dataclass
class Problem:
    kind: str  # "module" | "name"
    location: str
    target: str
    detail: str = ""


@dataclass
class Report:
    problems: list[Problem] = field(default_factory=list)
    files_scanned: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def collect_python_files(root: Path, include_tests: bool) -> list[Path]:
    files = [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]
    if not include_tests:
        files = [p for p in files if "tests" not in p.parts and "test_" not in p.name]
    return sorted(files)


def build_module_map(root: Path) -> dict[str, Path]:
    """把 root 下的 .py 文件映射成 {模块名: 路径}（含包 __init__）。"""

    mapping: dict[str, Path] = {}
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(root)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        if not parts:
            continue
        mapping[".".join(parts)] = path
    # 项目里个别模块用 `backend.app.x` 前缀 import（如 bid_writing_mcp），
    # 而扫描根是 backend/（模块键为 app.x）。补一份带前缀的别名，避免误报缺模块。
    if root.name == "backend":
        for name, path in list(mapping.items()):
            mapping[f"backend.{name}"] = path
    return mapping


def _walk_body(body: list[ast.stmt]) -> list[ast.stmt]:
    """展开模块级 if/try 块（覆盖 ``if TYPE_CHECKING`` 与 ``try: import`` 定义）。"""

    nodes: list[ast.stmt] = []
    for node in body:
        nodes.append(node)
        if isinstance(node, ast.If):
            nodes.extend(_walk_body(node.body))
            nodes.extend(_walk_body(node.orelse))
        elif isinstance(node, (ast.Try, ast.TryStar)):
            nodes.extend(_walk_body(node.body))
            for handler in node.handlers:
                nodes.extend(_walk_body(handler.body))
            nodes.extend(_walk_body(node.orelse))
            nodes.extend(_walk_body(node.finalbody))
    return nodes


def defined_names(tree: ast.Module) -> set[str]:
    """模块顶层（含条件块）定义/导入的名字。"""

    names: set[str] = set()
    for node in _walk_body(tree.body):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        names.add(sub.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
    return names


def module_resolvable(name: str, module_map: dict[str, Path]) -> tuple[bool, str]:
    """判断模块能否解析：平台专属模块直接放行，其次仓库内映射，其次三方包 find_spec。"""

    if name.split(".")[0] in _PLATFORM_SPECIFIC:
        return True, ""
    if name in module_map:
        return True, ""
    try:
        if importlib.util.find_spec(name) is not None:
            return True, ""
    except (ImportError, AttributeError, ValueError, ModuleNotFoundError):
        pass
    return False, f"模块 {name!r} 既不在仓库内也 importlib 解析不到"


def name_in_module(name: str, module_name: str, module_map: dict[str, Path]) -> tuple[bool, str]:
    """检查 ``from <module_name> import <name>`` 的符号是否存在。"""

    # 1) 子模块（from app.x import submodule，submodule 本身是 app/x/submodule.py）
    if f"{module_name}.{name}" in module_map:
        return True, ""
    path = module_map.get(module_name)
    if path is None:
        return False, f"目标模块 {module_name!r} 不存在（已在上面的模块级检查里报告）"
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return False, f"无法解析 {path}: {exc}"
    if name in defined_names(tree):
        return True, ""
    return False, f"符号 {name!r} 不在 {module_name} 的定义里"


def resolve_relative(current: str, is_package: bool, module: str | None, level: int) -> str:
    """把 from-import 的目标解析成绝对模块名。

    ``level == 0`` 是**绝对导入**（``from __future__ import ...``、
    ``from datetime import ...``），必须原样返回，绝不能拼上当前包名。
    """

    if level == 0:
        return module or ""
    parts = current.split(".")
    if not is_package and parts:
        parts = parts[:-1]
    base = parts[: len(parts) - (level - 1)] if level > 1 else parts
    if module:
        base = base + module.split(".")
    return ".".join(base)


def scan_file(path: Path, module_name: str, is_package: bool, module_map: dict[str, Path]) -> list[Problem]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return [Problem("parse", str(path), "", f"{exc}")]

    problems: list[Problem] = []

    def visit(node: ast.AST, in_try: bool) -> None:
        # 进入 try/try* 子树后一律视为「可选/受保护」导入（可选依赖、平台差异）
        guarded = in_try or isinstance(node, (ast.Try, ast.TryStar))

        if isinstance(node, ast.Import):
            for alias in node.names:
                ok, detail = module_resolvable(alias.name, module_map)
                if not ok and not guarded:
                    problems.append(Problem("module", str(path), alias.name, detail))
            return

        if isinstance(node, ast.ImportFrom):
            if node.module is None:
                return
            target = resolve_relative(module_name, is_package, node.module, node.level)
            ok, detail = module_resolvable(target, module_map)
            if not ok:
                if not guarded:
                    problems.append(Problem("module", str(path), target, detail))
                return
            # 外部模块（stdlib / 三方库）不做符号级检查（无法静态分析）
            if target not in module_map:
                return
            if any(alias.name == "*" for alias in node.names):
                return
            for alias in node.names:
                ok, detail = name_in_module(alias.name, target, module_map)
                if not ok:
                    problems.append(Problem("name", str(path), f"{target}.{alias.name}", detail))
            return

        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="静态扫描 import 契约")
    parser.add_argument("--root", default=str(BACKEND), help="扫描根目录（默认 backend）")
    parser.add_argument("--include-tests", action="store_true", help="也扫 tests/")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"x 目录不存在：{root}", file=sys.stderr)
        return 2

    files = collect_python_files(root, args.include_tests)
    module_map = build_module_map(root)
    report = Report()

    for path in files:
        report.files_scanned += 1
        rel = path.relative_to(root)
        parts = list(rel.with_suffix("").parts)
        is_package = parts[-1] == "__init__"
        if is_package:
            parts = parts[:-1]
        module_name = ".".join(parts)
        report.problems.extend(scan_file(path, module_name, is_package, module_map))

    module_problems = [p for p in report.problems if p.kind == "module"]
    name_problems = [p for p in report.problems if p.kind == "name"]
    other_problems = [p for p in report.problems if p.kind not in ("module", "name")]

    print(f"扫描 {report.files_scanned} 个文件")
    print(f"  模块缺失: {len(module_problems)}")
    print(f"  符号缺失: {len(name_problems)}")
    print(f"  其他    : {len(other_problems)}")

    def _dump(problems: list[Problem], title: str) -> None:
        if not problems:
            return
        print(f"\n{title}（{len(problems)}）:")
        for p in problems:
            print(f"  x {p.location}")
            print(f"      {p.target}{'  ← ' + p.detail if p.detail else ''}")

    _dump(module_problems, "模块缺失 —— 缺依赖或路径不对")
    _dump(name_problems, "符号缺失 —— 典型：合并时漏了远端新增的函数/类")
    _dump(other_problems, "其他")

    if report.ok:
        print("\n未发现契约断层。")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
