#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tender_agent 本地 JSON 数据 → PostgreSQL 16 一次性迁移脚本。

用法：
    1) 安装依赖：  pip install "psycopg[binary]>=3.1"
    2) 建库：      createdb tender_agent   （或你想要的库名）
    3) 建表：      psql -d tender_agent -f schema.sql
    4) 跑迁移：    python migrate_to_db.py --dsn "postgresql://user:pass@localhost:5432/tender_agent"
                   （缺省读环境变量 PG_DSN；也可用 --root 指定项目根目录）

特性：
    - 幂等：所有表按主键 upsert，可重复执行。
    - 保真：实体表保留 payload JSONB 完整副本；大集合拆成明细表。
    - 文件不搬家：证明文件仍留盘，脚本只写 evidence_files 元数据并算 sha256。
    - 校验：结束打印每张表的行数报告，可与源 JSON 计数逐项核对。
    - --dry-run：不连库，只输出"将要迁移什么 + 计数 + 校验"报告。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover
    psycopg = None
    Jsonb = None

DEFAULT_ROOT = Path(r"C:\Users\乔远\Desktop\tender_agent")
EVIDENCE_BASE = Path("libraries", "company", "证明材料")


def adapt(value):
    """psycopg 不会自动把 dict/list 适配成 JSONB，必须显式包一层 Jsonb。

    注意只包 dict/list：None 要原样传（NULL），bool/int/str/date/datetime
    psycopg 都能原生适配，包了反而出错。
    """
    if Jsonb is not None and isinstance(value, (dict, list)):
        return Jsonb(value)
    return value


def now_dt() -> datetime:
    """当前时间（timezone-aware datetime）。

    必须返回 datetime 而不是字符串：psycopg 会把 str 绑成 text 类型，
    插到 TIMESTAMPTZ 列会报 "column is of type timestamp with time zone
    but expression is of type text"。
    """
    return datetime.now(timezone.utc)


def read_json(path: Path, fallback):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def to_tstz(value):
    """把 ISO 字符串尽量转成 timezone-aware datetime；失败返回 None。"""
    if not isinstance(value, str) or not value:
        return None
    text = value.strip()
    try:
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def to_date(value):
    """把字符串转成 date；非标准日期（如"2026-09"或空）返回 None，避免 DATE 列报错。"""
    if not isinstance(value, str) or not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 读取源数据（纯内存，全部读进 dict/list，便于 dry-run 复用）
# ---------------------------------------------------------------------------
class Source:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.runtime = self.root / "runtime"

    def catalog(self):
        return read_json(self.runtime / "catalog.json", [])

    def libraries(self):
        return read_json(self.runtime / "libraries.json", {})

    def workspaces(self):
        return read_json(self.runtime / "workspaces.json", {})

    def deleted_workspaces(self):
        raw = read_json(self.runtime / "workspaces-deleted.json", [])
        return {str(x) for x in (raw or []) if str(x)}

    def reminders(self):
        return read_json(self.runtime / "reminders.json", [])

    def automation_runs(self):
        return read_json(self.runtime / "automation-runs.json", [])

    def bid_runs(self):
        return read_json(self.runtime / "bid_runs.json", {})

    def scheduler_state(self):
        return read_json(self.runtime / "scheduler-state.json", None)

    def parse_bundles(self):
        out = []
        projects = self.runtime / "projects"
        if projects.is_dir():
            for bundle in projects.glob("*/parse/parse-bundle.json"):
                value = read_json(bundle, None)
                if isinstance(value, dict):
                    value["__rel_path"] = bundle.relative_to(self.runtime).as_posix()
                    out.append(value)
        return out

    def parse_artifacts(self):
        out = []
        projects = self.runtime / "projects"
        if not projects.is_dir():
            return out
        for pack in projects.glob("*/pack/tender_pack.json"):
            value = read_json(pack, None)
            if isinstance(value, dict):
                rel = pack.relative_to(self.runtime).as_posix()
                out.append({"kind": "tender_pack", "rel_path": rel,
                            "project_id": pack.parent.parent.name, "payload": value})
        for frag in projects.glob("*/parse/template-fragments/*.json"):
            value = read_json(frag, None)
            if isinstance(value, (dict, list)):
                rel = frag.relative_to(self.runtime).as_posix()
                out.append({"kind": "template_fragment", "rel_path": rel,
                            "project_id": frag.parent.parent.parent.name, "payload": value})
        return out

    def evidence_files(self):
        out = []
        base = self.runtime / EVIDENCE_BASE
        if base.is_dir():
            for f in base.rglob("*"):
                if not f.is_file() or f.name == ".keep":
                    continue
                rel = f.relative_to(self.runtime).as_posix()
                parts = f.relative_to(base).parts
                # 结构：<class_cn>/<object_id>/<filename>  （也可能多级）
                class_cn = parts[0] if len(parts) >= 1 else ""
                object_id = parts[1] if len(parts) >= 2 else ""
                out.append({
                    "rel_path": rel,
                    "class_cn": class_cn,
                    "object_id": object_id,
                    "filename": f.name,
                    "ext": f.suffix.lower().lstrip("."),
                    "size": f.stat().st_size,
                    "mtime": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc),
                    "sha256": None,  # 惰性计算（2.6GB，见 migrate() 的 --no-hash 开关）
                })
        return out

    def asset_files(self):
        """扫描「留盘文件资产」：上传源文件、投标输出、运行副本、模板、导入模板。

        二进制文件仍留盘（或迁对象存储），这里只登记元数据（含 sha256）；
        .json 是结构化内容，顺带把解析后的内容存进 payload。
        """
        out = []
        r = self.runtime
        projects = r / "projects"

        def add(path: Path, category: str, project_id):
            if not path.is_file():
                return
            suffix = path.suffix.lower()
            out.append({
                "rel_path": path.relative_to(r).as_posix(),
                "category": category,
                "project_id": project_id,
                "format": suffix.lstrip(".") or "bin",
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
                "mtime": datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc),
                "payload": read_json(path, None) if suffix == ".json" else None,
            })

        if projects.is_dir():
            # 投标输出产物（json + docx）——业务成果，必须登记
            for f in projects.glob("*/outputs/*"):
                add(f, "bid_output", f.relative_to(projects).parts[0])
            # 上传的招标原文
            for f in projects.glob("*/source/*"):
                add(f, "project_source", f.relative_to(projects).parts[0])
            # 单次运行的输入副本
            for f in projects.glob("*/runs/*/*"):
                add(f, "run_copy", f.relative_to(projects).parts[0])

        libs = r / "libraries"
        if libs.is_dir():
            for f in libs.glob("templates/*"):
                add(f, "library_template", None)
            for f in libs.iterdir():
                if f.is_file():
                    add(f, "library_asset", None)
        return out


# ---------------------------------------------------------------------------
# 行构造
# ---------------------------------------------------------------------------
def catalog_rows(items):
    rows = []
    for position, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        rows.append({
            "id": str(it.get("id") or ""),
            "position": position,
            "title": it.get("title"),
            "category": it.get("category"),
            "region": it.get("region"),
            "notice_type": it.get("noticeType"),
            "source": it.get("source"),
            "data_origin": it.get("dataOrigin"),
            "published_date": to_date(it.get("publishedDate")),
            "is_read": bool(it.get("read")),
            "follow_up": it.get("followUp"),
            "reminded_at": it.get("remindedAt"),
            "fetched_at": to_tstz(it.get("fetchedAt")),
            "updated_at": now_dt(),
            "payload": it,
        })
    return rows


def workspace_rows(workspaces, deleted):
    rows = []
    for position, (ws_id, ws) in enumerate(workspaces.items()):
        if not isinstance(ws, dict):
            continue
        rows.append({
            "id": ws_id,
            "position": position,
            "project_id": ws.get("projectId"),
            "title": ws.get("title"),
            "draft": ws.get("draft"),
            "draft_version": ws.get("draftVersion"),
            "created_at": now_dt(),
            "updated_at": now_dt(),
            "deleted_at": now_dt() if ws_id in deleted else None,
            "payload": ws,
        })
    return rows


def section_rows(workspaces):
    rows = []
    for ws_id, ws in workspaces.items():
        if not isinstance(ws, dict):
            continue
        bid = ws.get("bidDocument")
        if not isinstance(bid, dict):
            continue
        for sec in bid.get("plan") or []:
            if not isinstance(sec, dict):
                continue
            sid = str(sec.get("sectionId") or "")
            if not sid:
                continue
            rows.append({
                "workspace_id": ws_id,
                "section_id": sid,
                "title": sec.get("title"),
                "kind": sec.get("kind"),
                "source": sec.get("source"),
                "section_level": sec.get("level"),
                "sort_order": sec.get("order"),
                "parent_section_id": sec.get("parentSectionId"),
                "content": sec.get("content"),
                "payload": sec,
            })
    return rows


def fine_review_rows(workspaces):
    rows = []
    for ws_id, ws in workspaces.items():
        if not isinstance(ws, dict):
            continue
        reasoning = ws.get("reasoning")
        if not isinstance(reasoning, dict):
            continue
        fine = reasoning.get("fineReview")
        if not isinstance(fine, dict):
            continue
        for row in fine.get("rows") or []:
            if not isinstance(row, dict):
                continue
            rid = str(row.get("id") or row.get("requirementId") or "")
            if not rid:
                continue
            rows.append({
                "workspace_id": ws_id,
                "row_id": rid,
                "requirement_id": row.get("requirementId"),
                "title": row.get("title"),
                "mandatory": row.get("mandatory"),
                "handling_mode": row.get("handlingMode"),
                "status": row.get("status"),
                "review_status": row.get("reviewStatus"),
                "payload": row,
            })
    return rows


def coverage_rows(workspaces):
    rows = []
    for ws_id, ws in workspaces.items():
        if not isinstance(ws, dict):
            continue
        bid = ws.get("bidDocument")
        if not isinstance(bid, dict):
            continue
        for cov in bid.get("requirementCoverage") or []:
            if not isinstance(cov, dict):
                continue
            req = str(cov.get("requirementId") or "")
            if not req:
                continue
            rows.append({
                "workspace_id": ws_id,
                "requirement_id": req,
                "section_id": cov.get("sectionId"),
                "coverage_type": cov.get("coverageType"),
                "status": cov.get("status"),
                "reason": cov.get("reason"),
                "payload": cov,
            })
    return rows


def library_rows(libraries):
    rows = []
    for position, (key, lib) in enumerate(libraries.items()):
        if not isinstance(lib, dict):
            continue
        rows.append({
            "key": key,
            "position": position,
            "name": lib.get("name", key),
            "description": lib.get("description"),
            "payload": lib,
        })
    return rows


def library_doc_rows(libraries):
    rows = []
    for key, lib in libraries.items():
        if not isinstance(lib, dict):
            continue
        for doc in lib.get("documents") or []:
            if not isinstance(doc, dict):
                continue
            rows.append({
                "id": str(doc.get("id") or ""),
                "library_key": key,
                "name": doc.get("name"),
                "type": doc.get("type"),
                "saved_at": doc.get("savedAt"),
                "source": doc.get("source"),
                "storage_path": doc.get("storagePath"),
                "content_hash": doc.get("contentHash"),
                "mime_type": doc.get("mimeType"),
                "size": doc.get("size"),
                "preview": doc.get("preview"),
                "payload": doc,
            })
    return rows


def reminder_rows(items):
    rows = []
    for position, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        rows.append({
            "id": str(it.get("id") or ""),
            "position": position,
            "project_id": it.get("projectId"),
            "project_title": it.get("projectTitle"),
            "status": it.get("status"),
            "created_at": to_tstz(it.get("createdAt")),
            "channels": it.get("channels"),
            "detail": it.get("detail"),
            "payload": it,
        })
    return rows


def automation_run_rows(items):
    rows = []
    for position, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        rows.append({
            "id": str(it.get("id") or ""),
            "position": position,
            "status": it.get("status"),
            "started_at": to_tstz(it.get("startedAt")),
            "payload": it,
        })
    return rows


def bid_run_rows(bid_runs):
    rows = []
    for run_id, run in bid_runs.items():
        if not isinstance(run, dict):
            continue
        rows.append({
            "run_id": str(run_id),
            "workspace_id": run.get("workspaceId"),
            "project_id": run.get("projectId"),
            "provider": run.get("provider"),
            "status": run.get("status"),
            "phase": run.get("phase"),
            "created_at": to_tstz(run.get("createdAt")),
            "updated_at": to_tstz(run.get("updatedAt")),
            "parse_bundle_id": run.get("parseBundleId"),
            "payload": run,
        })
    return rows


def parse_bundle_rows(bundles):
    rows = []
    for b in bundles:
        rows.append({
            "parse_bundle_id": str(b.get("parseBundleId") or ""),
            "workspace_id": b.get("workspaceId"),
            "project_id": b.get("projectId"),
            "provider": b.get("provider"),
            "schema_version": b.get("schemaVersion"),
            "status": b.get("status"),
            "artifact_path": b.get("artifactPath") or b.get("__rel_path"),
            "updated_at": to_tstz(b.get("updatedAt")) or now_dt(),
            "payload": {k: v for k, v in b.items() if not k.startswith("__")},
        })
    return rows


def parse_artifact_rows(artifacts):
    rows = []
    for a in artifacts:
        payload = a["payload"]
        size = None
        try:
            size = len(json.dumps(payload, ensure_ascii=False))
        except (TypeError, ValueError):
            size = None
        rows.append({
            "artifact_id": a["rel_path"],
            "project_id": a["project_id"],
            "kind": a["kind"],
            "rel_path": a["rel_path"],
            "sha256": hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            ).hexdigest(),
            "size": size,
            "payload": payload,
        })
    return rows


def evidence_rows_to_db(ev_rows):
    """构造 evidence_files 行。

    sha256 只在源行里存在时才输出：`--no-hash` 模式会故意把它从列集合里摘掉，
    免得用 NULL 覆盖掉上一次已经算好的哈希。
    """
    out = []
    for r in ev_rows:
        row = {
            "rel_path": r["rel_path"],
            "class_cn": r["class_cn"],
            "object_id": r["object_id"],
            "filename": r["filename"],
            "ext": r["ext"],
            "size": r["size"],
            "mtime": r["mtime"],
        }
        if "sha256" in r:
            row["sha256"] = r["sha256"]
        out.append(row)
    return out


def asset_file_rows(assets):
    return [
        {
            "rel_path": a["rel_path"],
            "category": a["category"],
            "project_id": a["project_id"],
            "format": a["format"],
            "size": a["size"],
            "sha256": a["sha256"],
            "mtime": a["mtime"],
            "payload": a["payload"],
        }
        for a in assets
    ]


# ---------------------------------------------------------------------------
# 数据库写入
# ---------------------------------------------------------------------------
def upsert_rows(cur, table: str, rows: list[dict], key_cols: list[str]) -> int:
    """按主键 upsert。

    两条防御（源 JSON 是历史数据，不能假设主键一定唯一/非空）：
      1) 主键为空的记录直接丢弃——否则会挤在同一条空主键上互相覆盖；
      2) 同批次内主键重复时保留最后一条——否则 PostgreSQL 会报
         "ON CONFLICT DO UPDATE command cannot affect row a second time"。
    """
    deduped: dict[tuple, dict] = {}
    dropped = 0
    for row in rows:
        key = tuple(row.get(c) for c in key_cols)
        if any(k is None or k == "" for k in key):
            dropped += 1
            continue
        deduped[key] = row
    values = list(deduped.values())
    if dropped:
        print(f"    （{table} 跳过主键为空的记录 {dropped} 条）")
    if not values:
        return 0
    cols = list(values[0].keys())
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in key_cols)
    ph = ", ".join(["%s"] * len(cols))
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT ({', '.join(key_cols)}) DO UPDATE SET {updates}"
    )
    cur.executemany(sql, [[adapt(r.get(c)) for c in cols] for r in values])
    return len(values)


def migrate(conn, src: Source, hashes: bool):
    with conn.cursor() as cur:
        ws = src.workspaces()
        deleted = src.deleted_workspaces()
        print(f"[catalog]        {upsert_rows(cur, 'catalog_projects', catalog_rows(src.catalog()), ['id'])}")
        print(f"[workspaces]     {upsert_rows(cur, 'workspaces', workspace_rows(ws, deleted), ['id'])}")
        print(f"[sections]       {upsert_rows(cur, 'workspace_sections', section_rows(ws), ['workspace_id', 'section_id'])}")
        print(f"[fine_review]    {upsert_rows(cur, 'workspace_fine_review_rows', fine_review_rows(ws), ['workspace_id', 'row_id'])}")
        print(f"[coverage]       {upsert_rows(cur, 'workspace_requirement_coverage', coverage_rows(ws), ['workspace_id', 'requirement_id'])}")

        libs = src.libraries()
        print(f"[libraries]      {upsert_rows(cur, 'libraries', library_rows(libs), ['key'])}")
        print(f"[library_docs]   {upsert_rows(cur, 'library_documents', library_doc_rows(libs), ['id'])}")
        print(f"[reminders]      {upsert_rows(cur, 'reminders', reminder_rows(src.reminders()), ['id'])}")
        print(f"[automation]     {upsert_rows(cur, 'automation_runs', automation_run_rows(src.automation_runs()), ['id'])}")
        print(f"[bid_runs]       {upsert_rows(cur, 'bid_runs', bid_run_rows(src.bid_runs()), ['run_id'])}")
        print(f"[parse_bundles]  {upsert_rows(cur, 'parse_bundles', parse_bundle_rows(src.parse_bundles()), ['parse_bundle_id'])}")
        print(f"[parse_artifacts]{upsert_rows(cur, 'parse_artifacts', parse_artifact_rows(src.parse_artifacts()), ['artifact_id'])}")

        ev = src.evidence_files()
        if hashes:
            print(f"[evidence] 正在计算 {len(ev)} 个文件的 sha256（约 2.6GB，耗时几分钟）...")
            base = src.runtime
            for r in ev:
                r["sha256"] = sha256_file(base / r["rel_path"])
        else:
            # 不加哈希时把 sha256 键整个摘掉（而不是留 None），否则会把库里
            # 上一次算好的哈希覆盖成 NULL。
            for r in ev:
                r.pop("sha256", None)
        print(f"[evidence_files] {upsert_rows(cur, 'evidence_files', evidence_rows_to_db(ev), ['rel_path'])}")

        assets = src.asset_files()
        print(f"[asset_files]   {upsert_rows(cur, 'asset_files', asset_file_rows(assets), ['rel_path'])}")

        sched = src.scheduler_state()
        if isinstance(sched, dict):
            cur.execute(
                "INSERT INTO runtime_state (key, value, updated_at) VALUES (%s, %s, %s) "
                "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = EXCLUDED.updated_at",
                ("scheduler", adapt(sched), now_dt()),
            )
            print("[runtime_state]  scheduler")
    conn.commit()
    print("\n迁移完成，已提交事务。")


def backfill_positions(conn, src: Source) -> None:
    """只回填 position 列，不动其它列。

    应用接入后读取统一用 ORDER BY position NULLS LAST，所以必须按 JSON 文件的
    原始顺序把 position 填上，否则有序集合（提醒、工作区、资料库）顺序会乱。
    """
    jobs: list[tuple[str, str, list[tuple[str, int]]]] = [
        ("catalog_projects", "id", [
            (str(it.get("id") or ""), pos)
            for pos, it in enumerate(src.catalog()) if isinstance(it, dict)
        ]),
        ("workspaces", "id", [(str(k), pos) for pos, k in enumerate(src.workspaces())]),
        ("libraries", "key", [(str(k), pos) for pos, k in enumerate(src.libraries())]),
        ("reminders", "id", [
            (str(it.get("id") or ""), pos)
            for pos, it in enumerate(src.reminders()) if isinstance(it, dict)
        ]),
        ("automation_runs", "id", [
            (str(it.get("id") or ""), pos)
            for pos, it in enumerate(src.automation_runs()) if isinstance(it, dict)
        ]),
    ]
    print("===== 回填 position =====")
    with conn.cursor() as cur:
        for table, key_col, items in jobs:
            updated = 0
            for key, pos in items:
                if not key:
                    continue
                cur.execute(f"UPDATE {table} SET position = %s WHERE {key_col} = %s", (pos, key))
                updated += cur.rowcount
            print(f"  {table:18s} 更新 {updated} 行（源 {len(items)} 条）")
    conn.commit()
    print("position 回填完成。")


def report(cur) -> None:
    tables = [
        "catalog_projects", "workspaces", "workspace_sections",
        "workspace_fine_review_rows", "workspace_requirement_coverage",
        "libraries", "library_documents", "reminders", "automation_runs",
        "bid_runs", "parse_bundles", "parse_artifacts", "evidence_files",
        "asset_files", "runtime_state",
    ]
    print("\n===== 迁移后行数校验 =====")
    for t in tables:
        try:
            cur.execute(f"SELECT count(*) FROM {t}")
            print(f"  {t:34s} {cur.fetchone()[0]}")
        except Exception as exc:  # noqa: BLE001
            print(f"  {t:34s} 读取失败：{exc}")


def main() -> int:
    # Windows 控制台默认 GBK，中文报告会乱码；强制 UTF-8 输出。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("PG_DSN"),
                    help="PostgreSQL DSN；缺省读 PG_DSN 环境变量")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="tender_agent 项目根目录")
    ap.add_argument("--no-hash", action="store_true", help="跳过证明文件 sha256 计算（快，但无完整性校验）")
    ap.add_argument("--dry-run", action="store_true", help="不连库，只打印源数据计数")
    ap.add_argument("--position-only", action="store_true", help="只按 JSON 顺序回填 position 列，不动其它数据")
    args = ap.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"项目根目录不存在：{root}", file=sys.stderr)
        return 1
    src = Source(root)

    if args.dry_run:
        ws = src.workspaces()
        print("===== 源数据计数（dry-run）=====")
        print(f"  catalog           {len(src.catalog())}")
        print(f"  workspaces        {len(ws)}")
        print(f"  sections(plan)    {len(section_rows(ws))}")
        print(f"  fine_review_rows  {len(fine_review_rows(ws))}")
        print(f"  coverage          {len(coverage_rows(ws))}")
        print(f"  libraries         {len(src.libraries())}")
        print(f"  library_documents {len(library_doc_rows(src.libraries()))}")
        print(f"  reminders         {len(src.reminders())}")
        print(f"  automation_runs   {len(src.automation_runs())}")
        print(f"  bid_runs          {len(src.bid_runs())}")
        print(f"  parse_bundles     {len(src.parse_bundles())}")
        print(f"  parse_artifacts   {len(src.parse_artifacts())}")
        ev = src.evidence_files()
        total = sum(r["size"] for r in ev)
        print(f"  evidence_files    {len(ev)}  （{total / 1024 / 1024:.1f} MB）")
        assets = src.asset_files()
        by_cat: dict[str, int] = {}
        for a in assets:
            by_cat[a["category"]] = by_cat.get(a["category"], 0) + 1
        detail = "、".join(f"{k}={v}" for k, v in sorted(by_cat.items())) or "无"
        print(f"  asset_files       {len(assets)}  （{detail}）")
        return 0

    if psycopg is None:
        print("缺少依赖：请先 `pip install \"psycopg[binary]>=3.1\"`", file=sys.stderr)
        return 1
    if not args.dsn:
        print("未提供数据库连接串：请用 --dsn 或设置 PG_DSN 环境变量", file=sys.stderr)
        return 1

    with psycopg.connect(args.dsn) as conn:
        if args.position_only:
            backfill_positions(conn, src)
            return 0
        migrate(conn, src, hashes=not args.no_hash)
        with conn.cursor() as cur:
            report(cur)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
