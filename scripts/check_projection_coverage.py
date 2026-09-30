# -*- coding: utf-8 -*-
"""投影完整性校验：代码实际写入的 key 集合 vs meta_column 登记的路径集合。

这是融合方案 §七 的"杀手锏"落地：
  拿代码里实际写入的 key 集合 与 meta_column 的 payload 路径集合做差集，
  **自动发现"代码加了字段、投影没跟上"**。

本项目踩过的坑正是这个：
  G 组「代码写 37 个 key、表只有 3 列」——在被自动检出之前，是靠人工核对才发现的。

用法：
  python scripts/check_projection_coverage.py            # 报告
  python scripts/check_projection_coverage.py --strict   # 有缺口则退出码 1

输出 UTF-8 文件（控制台 GBK 会乱码）。
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys
from dataclasses import dataclass, field

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DSN_DEFAULT = "postgresql://tender_app:tender_app_dev@127.0.0.1:5433/tender_agent"

# 仓库根（本脚本在 <root>/scripts/ 下）
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "projection_coverage_report.txt")


# ---------------------------------------------------------------------------
# 1) 从代码里扫出"实际写入某实体"的 key 集合
# ---------------------------------------------------------------------------
@dataclass
class KeyScan:
    """某个实体的写入 key 扫描结果。"""
    entity: str
    keys: dict[str, set[str]] = field(default_factory=dict)   # key -> 源文件集合

    def add(self, key: str, src: str) -> None:
        self.keys.setdefault(key, set()).add(src)

    @property
    def key_set(self) -> set[str]:
        return set(self.keys)


def scan_assignments(var_names: tuple[str, ...]) -> KeyScan:
    """扫 `var["key"] = ...` 形式的写入。

    var_names 是源码里指向该实体的变量名（如 section / bid_doc）。
    """
    pat = re.compile(
        r'\b(?:' + "|".join(re.escape(v) for v in var_names) + r')\[\s*"([A-Za-z_][A-Za-z0-9_]*)"\s*\]\s*='
    )
    scan = KeyScan(entity="|".join(var_names))
    app = os.path.join(ROOT, "backend", "app")
    for dirpath, _, files in os.walk(app):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    src = f.read()
            except OSError:
                continue
            for m in pat.finditer(src):
                scan.add(m.group(1), os.path.relpath(path, ROOT))
    return scan


def contract_keys(func_name: str, *args) -> set[str]:
    """取代码契约函数返回的 key 集合（build_section / build_bid_document）。"""
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    try:
        from app.bid_writing import schemas
        fn = getattr(schemas, func_name, None)
        if fn is None:
            return set()
        return set(fn(*args).keys())
    except Exception as e:  # pragma: no cover
        print(f"  ⚠️ 无法导入契约 {func_name}: {e}")
        return set()


# ---------------------------------------------------------------------------
# 2) 从库里读 meta_column 的登记情况
# ---------------------------------------------------------------------------
def load_meta(table: str, dsn: str) -> tuple[set[str], dict[str, str]]:
    """返回 (已登记的列集合, {列名: payload 路径})。"""
    try:
        import psycopg
    except ImportError:
        print("⚠️ psycopg 未安装，跳过库侧校验（pip install 'psycopg[binary]>=3.1'）")
        return set(), {}
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        cur.execute(
            "SELECT column_name, description FROM meta_column WHERE table_name=%s",
            (table,),
        )
        cols, paths = set(), {}
        for cn, desc in cur.fetchall():
            cols.add(cn)
            d = str(desc or "")
            m = re.match(r"payload=\$\.([A-Za-z_][A-Za-z0-9_]*)", d)
            if m:
                paths[cn] = m.group(1)          # 投影列：来自 payload 的哪个路径
            elif d.startswith("★全保真事实"):
                paths[cn] = "*"                  # payload 本身
            elif d.strip():
                # 有说明文字即视为"已登记"（例如「独立事实（非 payload 投影）：…」
                # 或「JOIN 到 xxx（FK）」「审计时间戳」）——它的来源已被明确交代。
                paths[cn] = "(已说明)"
        return cols, paths


def db_column_names(table: str, dsn: str) -> set[str]:
    try:
        import psycopg
    except ImportError:
        return set()
    with psycopg.connect(dsn) as con, con.cursor() as cur:
        cur.execute(
            """SELECT column_name FROM information_schema.columns
               WHERE table_schema='public' AND table_name=%s""",
            (table,),
        )
        return {r[0] for r in cur.fetchall()}


def snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


# ---------------------------------------------------------------------------
# 3) 校验
# ---------------------------------------------------------------------------
def check(table: str, scan: KeyScan, extra_contract: set[str], dsn: str, lines: list[str]) -> bool:
    """返回 True 表示无缺口。"""
    lines.append("=" * 92)
    lines.append(f"表 {table}")
    lines.append("=" * 92)

    db_cols = db_column_names(table, dsn)
    meta_cols, meta_paths = load_meta(table, dsn)

    written = scan.key_set | extra_contract
    lines.append(f"  代码写入 key 数        : {len(written)}")
    lines.append(f"  库里实际列数           : {len(db_cols)}")
    lines.append(f"  meta_column 登记列数   : {len(meta_cols)}")

    problems: list[str] = []

    # 3.1 meta_column 登记 vs 真实列（治理漂移）
    missing_in_meta = sorted(db_cols - meta_cols)
    stale_in_meta = sorted(meta_cols - db_cols)
    if missing_in_meta:
        problems.append(f"真实列未登记进 meta_column（{len(missing_in_meta)}）: {missing_in_meta}")
    if stale_in_meta:
        problems.append(f"meta_column 有已不存在的列（{len(stale_in_meta)}）: {stale_in_meta}")

    # 3.2 代码写入 key vs (列 + payload)
    #     payload 是万能兜底：只要表有 payload 列，就不会真丢。
    has_payload = "payload" in db_cols
    unplaced = []
    for k in sorted(written):
        if k in db_cols or snake(k) in db_cols:
            continue
        if not has_payload:
            unplaced.append(k)

    lines.append(f"  表是否有 payload 列    : {'是 ✅' if has_payload else '否 ❌'}")
    lines.append("")
    lines.append(f"  代码写入的 key 落位明细（共 {len(written)} 个）:")
    in_col = [k for k in sorted(written) if k in db_cols or snake(k) in db_cols]
    in_pay = [k for k in sorted(written) if k not in db_cols and snake(k) not in db_cols]
    lines.append(f"      落进提升列   : {len(in_col)}")
    lines.append(f"      落进 payload : {len(in_pay)}")
    if in_pay:
        lines.append(f"      payload 兜住的 key: {in_pay}")

    if unplaced:
        problems.append(f"❗无处可去的 key（表无 payload 列，会静默丢失）: {unplaced}")

    # 3.3 提升列必须有 payload 路径映射
    promoted = db_cols - {"payload", "id", "biz_id", "org_id",
                          "created_at", "updated_at", "deleted_at"}
    unmapped = sorted(c for c in promoted if c not in meta_paths)
    if unmapped:
        lines.append("")
        lines.append(f"  ⚠️ 提升列缺 payload 路径映射（{len(unmapped)} 个）:")
        lines.append(f"      {unmapped}")
        lines.append("      → 这些列是「纯投影」还是「独立事实」没有任何登记，"
                     "建议补 meta_column.description")

    lines.append("")
    if problems:
        lines.append("  ❌ 发现缺口:")
        for p in problems:
            lines.append(f"      {p}")
    else:
        lines.append("  ✅ 无缺口：真实列 ↔ meta_column ↔ 代码写入 key 三者一致")
    lines.append("")
    return not problems


def main() -> int:
    ap = argparse.ArgumentParser(description="投影完整性校验")
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN", DSN_DEFAULT))
    ap.add_argument("--strict", action="store_true", help="有缺口时退出码为 1")
    args = ap.parse_args()

    lines: list[str] = []
    lines.append("投影完整性校验：代码写入 key ↔ 库列 ↔ meta_column 登记")
    lines.append(f"仓库根: {ROOT}")
    lines.append("")

    ok = True

    # ── bid_section ──────────────────────────────────────────────────
    sec = scan_assignments(("section", "sec", "child"))
    ok &= check("bid_section", sec, contract_keys("build_section", "x", "t", "ai"), args.dsn, lines)

    # ── bid_document ─────────────────────────────────────────────────
    doc = scan_assignments(("bid_doc", "bid_document", "document"))
    ok &= check("bid_document", doc, contract_keys("build_bid_document", "w", "p"), args.dsn, lines)

    lines.append("=" * 92)
    lines.append(f"总判定: {'✅ PASS（无缺口）' if ok else '❌ FAIL（有缺口，见上）'}")
    lines.append("=" * 92)

    text = "\n".join(lines) + "\n"
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(text)
    print("报告 ->", OUT)
    return 0 if (ok or not args.strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
