"""PostgreSQL 后端：结构化业务状态以 PostgreSQL 为唯一事实源。

与迁移脚本 migrate_to_db.py 的行拆分保持一致：每行既存整对象 payload（保真），
也拆出常用列（可查询）；有序集合额外存 position 保留原始顺序。

二进制文件（证明材料、资料文件、解析产物、导出文件）不经过这里，仍留盘。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any

from .base import StorageBackend

try:
    import psycopg
    from psycopg.types.json import Jsonb
except ImportError:  # pragma: no cover
    psycopg = None
    Jsonb = None

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 值适配
# ---------------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _adapt(value: Any) -> Any:
    """psycopg 不会自动把 dict/list 适配成 JSONB，需显式包 Jsonb。"""
    if Jsonb is not None and isinstance(value, (dict, list)):
        return Jsonb(value)
    return value


def _synthetic_id(prefix: str, obj: Any) -> str:
    digest = hashlib.sha256(
        json.dumps(obj, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return f"{prefix}-{digest[:16]}"


def _to_tstz(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _to_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _to_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "是"):
        return True
    if text in ("false", "0", "no", "否", ""):
        return False
    return None


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 行构造（与 migrate_to_db.py 保持一致）
# ---------------------------------------------------------------------------
def _catalog_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for position, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        rows.append({
            "id": str(it.get("id") or ""),
            "title": it.get("title"),
            "category": it.get("category"),
            "region": it.get("region"),
            "notice_type": it.get("noticeType"),
            "source": it.get("source"),
            "data_origin": it.get("dataOrigin"),
            "published_date": _to_date(it.get("publishedDate")),
            "is_read": bool(it.get("read")),
            "follow_up": _to_bool(it.get("followUp")),
            "reminded_at": it.get("remindedAt"),
            "fetched_at": _to_tstz(it.get("fetchedAt")),
            "updated_at": _now(),
            "position": position,
            "payload": it,
        })
    return rows


def _library_rows(libs: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lib_rows: list[dict[str, Any]] = []
    doc_rows: list[dict[str, Any]] = []
    for position, (key, value) in enumerate(libs.items()):
        if not isinstance(value, dict):
            continue
        lib_rows.append({
            "key": str(key),
            "name": value.get("name"),
            "description": value.get("description"),
            "position": position,
            "payload": value,
        })
        for doc in value.get("documents") or []:
            if not isinstance(doc, dict):
                continue
            doc_id = str(doc.get("id") or "")
            if not doc_id:
                continue
            doc_rows.append({
                "id": doc_id,
                "library_key": str(key),
                "name": doc.get("name"),
                "type": doc.get("type"),
                "saved_at": doc.get("savedAt"),
                "source": doc.get("source"),
                "storage_path": doc.get("storagePath"),
                "content_hash": doc.get("contentHash"),
                "mime_type": doc.get("mimeType") or doc.get("type"),
                "size": _to_int(doc.get("size")),
                "preview": doc.get("preview"),
                "payload": doc,
            })
    return lib_rows, doc_rows


def _workspace_rows(workspaces: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for position, (ws_id, ws) in enumerate(workspaces.items()):
        if not isinstance(ws, dict):
            continue
        rows.append({
            "id": str(ws_id),
            "project_id": ws.get("projectId"),
            "title": ws.get("title"),
            "draft": ws.get("draft"),
            "draft_version": _to_int(ws.get("draftVersion")),
            # 创建时间只认源数据里的 createdAt（应用在新建/重复物化工作区时盖章）。
            # 接入前的老数据没有这个字段 —— 宁可留 NULL 表示「未知」，也不用 now()
            # 伪造一个看似合理的值；save_workspaces 的 coalesce_cols 保证它不被刷掉。
            "created_at": _to_tstz(ws.get("createdAt")),
            "updated_at": _now(),
            "deleted_at": None,
            "position": position,
            "payload": ws,
        })
    return rows


def _section_rows(workspaces: dict[str, Any]) -> list[dict[str, Any]]:
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
                "workspace_id": str(ws_id),
                "section_id": sid,
                "title": sec.get("title"),
                "kind": sec.get("kind"),
                "source": sec.get("source"),
                "section_level": _to_int(sec.get("level")),
                "sort_order": _to_int(sec.get("order")),
                "parent_section_id": sec.get("parentSectionId"),
                "content": sec.get("content"),
                "payload": sec,
            })
    return rows


def _fine_review_rows(workspaces: dict[str, Any]) -> list[dict[str, Any]]:
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
                "workspace_id": str(ws_id),
                "row_id": rid,
                "requirement_id": row.get("requirementId"),
                "title": row.get("title"),
                "mandatory": _to_bool(row.get("mandatory")),
                "handling_mode": row.get("handlingMode"),
                "status": row.get("status"),
                "review_status": row.get("reviewStatus"),
                "payload": row,
            })
    return rows


def _coverage_rows(workspaces: dict[str, Any]) -> list[dict[str, Any]]:
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
                "workspace_id": str(ws_id),
                "requirement_id": req,
                "section_id": cov.get("sectionId"),
                "coverage_type": cov.get("coverageType"),
                "status": cov.get("status"),
                "reason": cov.get("reason"),
                "payload": cov,
            })
    return rows


def _reminder_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for position, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        rid = str(it.get("id") or "")
        if not rid:
            rid = _synthetic_id("reminder", it)
        rows.append({
            "id": rid,
            "project_id": it.get("projectId"),
            "project_title": it.get("projectTitle"),
            "status": it.get("status"),
            "created_at": _to_tstz(it.get("createdAt")),
            "channels": it.get("channels"),
            "detail": it.get("detail"),
            "position": position,
            "payload": it,
        })
    return rows


def _automation_run_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for position, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        run_id = str(it.get("id") or "")
        if not run_id:
            run_id = _synthetic_id("auto-run", it)
        rows.append({
            "id": run_id,
            "status": it.get("status"),
            "started_at": _to_tstz(it.get("startedAt")),
            "position": position,
            "payload": it,
        })
    return rows


# ---------------------------------------------------------------------------
# 写入辅助
# ---------------------------------------------------------------------------
def _valid_rows(rows: list[dict[str, Any]], key_cols: list[str]) -> list[dict[str, Any]]:
    """去重（同批保留最后一条）+ 丢弃主键为空的记录。"""
    deduped: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(c) for c in key_cols)
        if any(k is None or k == "" for k in key):
            continue
        deduped[key] = row
    return list(deduped.values())


def _conflict_sql(
    table: str,
    cols: list[str],
    key_cols: list[str],
    coalesce_cols: tuple[str, ...] = (),
    touch_col: str = "",
) -> str:
    """构造 upsert 语句的 ON CONFLICT 子句。

    - ``coalesce_cols``：冲突时取 ``COALESCE(新值, 旧值)``。用于 ``created_at`` 这类
      「一旦确定就不该再变」的列——否则每次整体保存都会把它刷成当前时刻，全表所有行
      的创建时间会恒等于最后一次保存时间。新值为 NULL 时保留库里原值（**包括表示
      「未知」的 NULL**）：既不会被后续保存刷掉，也允许后来补上的真值生效。
    - ``touch_col``：冲突时**仅当 payload 真的变了才更新**。用于 ``updated_at``
      这类「最后修改」列——同理，否则它也会退化成「最后一次保存时刻」。
    """
    updates = []
    for c in cols:
        if c in key_cols:
            continue
        if c in coalesce_cols:
            updates.append(f"{c} = COALESCE(EXCLUDED.{c}, {table}.{c})")
        elif c == touch_col:
            updates.append(
                f"{c} = CASE WHEN {table}.payload IS DISTINCT FROM EXCLUDED.payload "
                f"THEN EXCLUDED.{c} ELSE {table}.{c} END"
            )
        else:
            updates.append(f"{c} = EXCLUDED.{c}")
    ph = ", ".join(["%s"] * len(cols))
    return (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({ph}) "
        f"ON CONFLICT ({', '.join(key_cols)}) DO UPDATE SET {', '.join(updates)}"
    )


def _upsert_rows(
    cur: Any,
    table: str,
    rows: list[dict[str, Any]],
    key_cols: list[str],
    coalesce_cols: tuple[str, ...] = (),
    touch_col: str = "",
) -> int:
    valid = _valid_rows(rows, key_cols)
    if not valid:
        return 0
    cols = list(valid[0].keys())
    cur.executemany(
        _conflict_sql(table, cols, key_cols, coalesce_cols, touch_col),
        [[_adapt(r.get(c)) for c in cols] for r in valid],
    )
    return len(valid)


def _replace_table(
    cur: Any,
    table: str,
    rows: list[dict[str, Any]],
    key_cols: list[str],
    coalesce_cols: tuple[str, ...] = (),
    touch_col: str = "",
) -> int:
    """整表替换：upsert 传入行，删除主键不在传入行中的旧行。"""
    valid = _valid_rows(rows, key_cols)
    if valid:
        cols = list(valid[0].keys())
        cur.executemany(
            _conflict_sql(table, cols, key_cols, coalesce_cols, touch_col),
            [[_adapt(r.get(c)) for c in cols] for r in valid],
        )
    kc = key_cols[0]
    if valid:
        keys = [r.get(kc) for r in valid]
        ph = ", ".join(["%s"] * len(keys))
        cur.execute(f"DELETE FROM {table} WHERE {kc} NOT IN ({ph})", keys)
    else:
        cur.execute(f"DELETE FROM {table}")
    return len(valid)


class PostgresBackend(StorageBackend):
    name = "postgres"

    def __init__(self, dsn: str, schema: str | None = None) -> None:
        if psycopg is None:
            raise RuntimeError("psycopg 未安装：pip install 'psycopg[binary]>=3.1'")
        self.dsn = dsn
        # 表所在 schema。None = 走连接的默认 search_path（通常 public）。
        #
        # 为什么需要它：本模块所有 SQL 用的是**不带限定**的表名
        # （`SELECT ... FROM workspaces`），所以表在哪个 schema 由 search_path 决定。
        # 脚手架表建在独立的 `partner` schema（见 backend/migrations/010_partner_scaffold.sql），
        # 与我方 44 张目标表（public）物理隔离，避免两套模型混淆。
        self.schema = (schema or "").strip() or None

    def _connect(self) -> Any:
        conn = psycopg.connect(self.dsn)
        if self.schema:
            # set_schema 用参数占位符不安全（标识符不能参数化），
            # 这里对 schema 名做白名单式校验后再拼接。
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.schema):
                conn.close()
                raise ValueError(f"非法 schema 名：{self.schema!r}")
            with conn.cursor() as cur:
                cur.execute(f"SET search_path TO {self.schema}, public")
            conn.commit()
        return conn

    # ---- load ----
    def load_catalog(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM catalog_projects ORDER BY position NULLS LAST, id")
            return [row[0] for row in cur.fetchall()]

    def load_libraries(self) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT key, payload FROM libraries ORDER BY position NULLS LAST, key")
            return {row[0]: row[1] for row in cur.fetchall()}

    def load_workspaces(self) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, payload FROM workspaces "
                "WHERE deleted_at IS NULL ORDER BY position NULLS LAST, id"
            )
            return {row[0]: row[1] for row in cur.fetchall()}

    def load_deleted_workspaces(self) -> list[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT id FROM workspaces WHERE deleted_at IS NOT NULL ORDER BY id")
            return [row[0] for row in cur.fetchall()]

    def load_reminders(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM reminders ORDER BY position NULLS LAST, created_at, id")
            return [row[0] for row in cur.fetchall()]

    def load_automation_runs(self) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT payload FROM automation_runs ORDER BY position NULLS LAST, id")
            return [row[0] for row in cur.fetchall()]

    # ---- save ----
    def save_catalog(self, value: list[dict[str, Any]]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            # updated_at 只在公告内容真的变了才刷新（源 JSON 里没有该字段，
            # 不这样做的话它只会等于「最后一次保存时刻」）。
            _replace_table(cur, "catalog_projects", _catalog_rows(value), ["id"],
                           touch_col="updated_at")

    def save_libraries(self, value: dict[str, Any]) -> None:
        lib_rows, doc_rows = _library_rows(value)
        with self._connect() as conn, conn.cursor() as cur:
            _replace_table(cur, "libraries", lib_rows, ["key"])
            _replace_table(cur, "library_documents", doc_rows, ["id"])

    def save_workspaces(self, value: dict[str, Any]) -> None:
        ws_rows = _workspace_rows(value)
        sec_rows = _section_rows(value)
        fr_rows = _fine_review_rows(value)
        cov_rows = _coverage_rows(value)
        with self._connect() as conn, conn.cursor() as cur:
            # created_at 只认源数据的 createdAt，新值为 NULL 时保留库里原值
            # （包括「未知」的 NULL）；updated_at 仅在该工作区 payload 真变了才刷新。
            _upsert_rows(cur, "workspaces", ws_rows, ["id"],
                         coalesce_cols=("created_at",), touch_col="updated_at")
            # 派生表按 workspace_id 局部重建，与工作区 payload 保持一致。
            #
            # 这里以前是**无 WHERE 的整表 DELETE**（全库所有工作区的行全删全插），
            # 代价是 O(总行数)/次保存 —— 而 persist_runtime()（存章节、改匹配状态）
            # 每次都会走到这里，规模上去之后是纯粹的浪费。
            # 现在只清理**本次传入**的工作区：DELETE ... WHERE workspace_id = ANY(%s)，
            # 随后 upsert 出的行覆盖同一批 workspace_id，语义与整表重建等价。
            #
            # 注意：以 **workspaces 字典的键**（而不是各子表 rows 里出现过的 id）为准。
            # 若某个工作区这次没有产生任何章节行，用子表推导的 id 集合会漏掉它，
            # 旧行就会残留 —— 用字典键可以保证「传入即重建」，包括清空成 0 行的情形。
            ws_ids = [str(k) for k in value if isinstance(value.get(k), dict)]
            if ws_ids:
                for table in (
                    "workspace_requirement_coverage",
                    "workspace_fine_review_rows",
                    "workspace_sections",
                ):
                    cur.execute(f"DELETE FROM {table} WHERE workspace_id = ANY(%s)", (ws_ids,))
            _upsert_rows(cur, "workspace_sections", sec_rows, ["workspace_id", "section_id"])
            _upsert_rows(cur, "workspace_fine_review_rows", fr_rows, ["workspace_id", "row_id"])
            _upsert_rows(cur, "workspace_requirement_coverage", cov_rows, ["workspace_id", "requirement_id"])

    def save_deleted_workspaces(self, value: list[str]) -> None:
        """写删除墓碑。

        deleted_at 只在**首次落地**时确定：重复保存不刷新，否则它会退化成
        「最后一次保存时刻」。若该行原本 deleted_at 为空（工作区曾被恢复过），
        则用本次的值补上，保证「恢复 → 再删除」能得到新的删除时间。
        """
        with self._connect() as conn, conn.cursor() as cur:
            for ws_id in value:
                cur.execute(
                    "INSERT INTO workspaces (id, payload, deleted_at) VALUES (%s, %s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "deleted_at = COALESCE(workspaces.deleted_at, EXCLUDED.deleted_at)",
                    (str(ws_id), _adapt({}), _now()),
                )

    def save_reminders(self, value: list[dict[str, Any]]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            _replace_table(cur, "reminders", _reminder_rows(value), ["id"])

    def save_automation_runs(self, value: list[dict[str, Any]]) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            _replace_table(cur, "automation_runs", _automation_run_rows(value), ["id"])
