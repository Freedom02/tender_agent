-- =====================================================================
-- 010_partner_scaffold.sql
--
-- 目的：在**独立 schema `partner`** 里建出队友存储层所需的 16 张表，
--       让 STORE_BACKEND=db / dual 这个开关**真的能跑起来**。
--
-- ⚠️⚠️ 这不是目标数据模型，是**脚手架** ⚠️⚠️
--
-- 背景（2026-09-30 实测）：
--   上一轮"合并了队友的三态存储层"是**代码层面**的合并；
--   **库层面队友的表一张都没建**，所以：
--       PostgresBackend(dsn).load_workspaces()
--         -> UndefinedTable: relation "workspaces" does not exist
--   `db` 模式**从未真正跑通过**。
--
-- 为什么放在 `partner` schema 而不是 `public`：
--   1. 我方 44 张表（public）是本项目的**目标数据模型**，一张不删/不改名/不合并。
--      实测两套表名**零冲突**（`workspace` vs `workspaces`、`parse_bundle` vs `parse_bundles` …），
--      物理隔离后"谁是权威"一目了然。
--   2. 队友的表最终会被我方模型吸收（统一版 = 我方表 + payload 保真手法）。
--      届时 `DROP SCHEMA partner CASCADE` 一句话即可干净移除脚手架，
--      不会与我方 44 张真模型纠缠。
--   3. 彻底杜绝"用队友的表替代我方表"的可能 —— 两者 schema 都不同。
--
-- 与队友原版 schema.sql 的差异（**仅限 schema 限定，结构一字未改**）：
--   - 全部表名加 `partner.` 前缀
--   - 外键显式限定为 `partner.<table>`
--   - 索引名加 `partner_` 前缀（PG 索引名是 schema 内唯一的，但加前缀更好认）
--   - 去掉 `app_config` 的"含明文密钥"注释歧义，改为明确不存密钥
--   - **不改变任何列名 / 列类型 / 主键 / 约束**，队友代码原样可用
--
-- 幂等性：全部 IF NOT EXISTS，可重复执行。
--
-- 回滚：DROP SCHEMA IF EXISTS partner CASCADE;
-- =====================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS partner;

-- ---------------------------------------------------------------------------
-- 0. 配置（取代 agent-config.json）
--    ⚠️ 生产环境不要把 API Key 写进来，走环境变量。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.app_config (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- 1. 招标公告 / 项目库（runtime/catalog.json）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.catalog_projects (
    id             TEXT PRIMARY KEY,
    title          TEXT,
    category       TEXT,
    region         TEXT,
    notice_type    TEXT,
    source         TEXT,
    data_origin    TEXT,
    published_date DATE,
    is_read        BOOLEAN DEFAULT FALSE,
    follow_up      BOOLEAN,
    reminded_at    TEXT,
    fetched_at     TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ,
    position       INTEGER,
    payload        JSONB NOT NULL
);

-- ---------------------------------------------------------------------------
-- 2. 工作区（runtime/workspaces.json + workspaces-deleted.json 墓碑）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.workspaces (
    id            TEXT PRIMARY KEY,
    project_id    TEXT,
    title         TEXT,
    draft         TEXT,
    draft_version INTEGER,
    created_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ,
    deleted_at    TIMESTAMPTZ,
    position      INTEGER,
    payload       JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS partner.workspace_sections (
    workspace_id      TEXT NOT NULL REFERENCES partner.workspaces(id) ON DELETE CASCADE,
    section_id        TEXT NOT NULL,
    title             TEXT,
    kind              TEXT,
    source            TEXT,
    section_level     INTEGER,
    sort_order        INTEGER,
    parent_section_id TEXT,
    content           TEXT,
    payload           JSONB NOT NULL,
    PRIMARY KEY (workspace_id, section_id)
);

CREATE TABLE IF NOT EXISTS partner.workspace_fine_review_rows (
    workspace_id   TEXT NOT NULL REFERENCES partner.workspaces(id) ON DELETE CASCADE,
    row_id         TEXT NOT NULL,
    requirement_id TEXT,
    title          TEXT,
    mandatory      BOOLEAN,
    handling_mode  TEXT,
    status         TEXT,
    review_status  TEXT,
    payload        JSONB NOT NULL,
    PRIMARY KEY (workspace_id, row_id)
);

CREATE TABLE IF NOT EXISTS partner.workspace_requirement_coverage (
    workspace_id   TEXT NOT NULL REFERENCES partner.workspaces(id) ON DELETE CASCADE,
    requirement_id TEXT NOT NULL,
    section_id     TEXT,
    coverage_type  TEXT,
    status         TEXT,
    reason         TEXT,
    payload        JSONB NOT NULL,
    PRIMARY KEY (workspace_id, requirement_id)
);

-- ---------------------------------------------------------------------------
-- 3. 资料库（runtime/libraries.json）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.libraries (
    key         TEXT PRIMARY KEY,
    name        TEXT,
    description TEXT,
    position    INTEGER,
    payload     JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS partner.library_documents (
    id           TEXT PRIMARY KEY,
    library_key  TEXT NOT NULL REFERENCES partner.libraries(key) ON DELETE CASCADE,
    name         TEXT,
    type         TEXT,
    saved_at     TEXT,
    source       TEXT,
    storage_path TEXT,
    content_hash TEXT,
    mime_type    TEXT,
    size         BIGINT,
    preview      TEXT,
    payload      JSONB NOT NULL
);

-- ---------------------------------------------------------------------------
-- 4. 跟进提醒（runtime/reminders.json）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.reminders (
    id            TEXT PRIMARY KEY,
    project_id    TEXT,
    project_title TEXT,
    status        TEXT,
    created_at    TIMESTAMPTZ,
    channels      JSONB,
    detail        TEXT,
    position      INTEGER,
    payload       JSONB NOT NULL
);

-- ---------------------------------------------------------------------------
-- 5. 自动化运行记录（runtime/automation-runs.json）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.automation_runs (
    id         TEXT PRIMARY KEY,
    status     TEXT,
    started_at TIMESTAMPTZ,
    position   INTEGER,
    payload    JSONB NOT NULL
);

-- ---------------------------------------------------------------------------
-- 6. 投标运行（runtime/bid_runs.json，cloud_agent/run_repository.py）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.bid_runs (
    run_id          TEXT PRIMARY KEY,
    workspace_id    TEXT,
    project_id      TEXT,
    provider        TEXT,
    status          TEXT,
    phase           TEXT,
    created_at      TIMESTAMPTZ,
    updated_at      TIMESTAMPTZ,
    parse_bundle_id TEXT,
    payload         JSONB NOT NULL
);

-- ---------------------------------------------------------------------------
-- 7. 解析产物
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.parse_bundles (
    parse_bundle_id TEXT PRIMARY KEY,
    workspace_id    TEXT,
    project_id      TEXT,
    provider        TEXT,
    schema_version  TEXT,
    status          TEXT,
    artifact_path   TEXT,
    updated_at      TIMESTAMPTZ,
    payload         JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS partner.parse_artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_id  TEXT,
    kind        TEXT,
    rel_path    TEXT,
    sha256      TEXT,
    size        BIGINT,
    payload     JSONB NOT NULL
);

-- ---------------------------------------------------------------------------
-- 8. 证明文件元数据（文件本身仍留盘，这里只存元数据）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.evidence_files (
    rel_path  TEXT PRIMARY KEY,
    class_cn  TEXT,
    object_id TEXT,
    filename  TEXT,
    ext       TEXT,
    size      BIGINT,
    sha256    TEXT,
    mtime     TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- 9. 留盘文件资产元数据
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.asset_files (
    rel_path   TEXT PRIMARY KEY,
    category   TEXT,
    project_id TEXT,
    format     TEXT,
    size       BIGINT,
    sha256     TEXT,
    mtime      TIMESTAMPTZ,
    payload    JSONB
);

-- ---------------------------------------------------------------------------
-- 10. 运行时杂项状态
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS partner.runtime_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- 索引（名字加 partner_ 前缀，便于识别归属）
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS partner_idx_catalog_region     ON partner.catalog_projects (region);
CREATE INDEX IF NOT EXISTS partner_idx_catalog_category   ON partner.catalog_projects (category);
CREATE INDEX IF NOT EXISTS partner_idx_catalog_origin     ON partner.catalog_projects (data_origin);
CREATE INDEX IF NOT EXISTS partner_idx_catalog_published  ON partner.catalog_projects (published_date DESC);
CREATE INDEX IF NOT EXISTS partner_idx_catalog_payload    ON partner.catalog_projects USING GIN (payload);

CREATE INDEX IF NOT EXISTS partner_idx_workspaces_project ON partner.workspaces (project_id);
CREATE INDEX IF NOT EXISTS partner_idx_workspaces_deleted ON partner.workspaces (deleted_at);
CREATE INDEX IF NOT EXISTS partner_idx_workspaces_payload ON partner.workspaces USING GIN (payload);

CREATE INDEX IF NOT EXISTS partner_idx_sections_title     ON partner.workspace_sections (title);
CREATE INDEX IF NOT EXISTS partner_idx_sections_kind      ON partner.workspace_sections (kind);

CREATE INDEX IF NOT EXISTS partner_idx_fr_rows_requirement ON partner.workspace_fine_review_rows (requirement_id);
CREATE INDEX IF NOT EXISTS partner_idx_fr_rows_status      ON partner.workspace_fine_review_rows (review_status);

CREATE INDEX IF NOT EXISTS partner_idx_lib_docs_key        ON partner.library_documents (library_key);
CREATE INDEX IF NOT EXISTS partner_idx_reminders_project   ON partner.reminders (project_id);
CREATE INDEX IF NOT EXISTS partner_idx_reminders_status    ON partner.reminders (status);
CREATE INDEX IF NOT EXISTS partner_idx_bid_runs_workspace  ON partner.bid_runs (workspace_id);

CREATE INDEX IF NOT EXISTS partner_idx_parse_bundles_ws    ON partner.parse_bundles (workspace_id);
CREATE INDEX IF NOT EXISTS partner_idx_parse_artifacts_proj ON partner.parse_artifacts (project_id, kind);
CREATE INDEX IF NOT EXISTS partner_idx_parse_artifacts_pay ON partner.parse_artifacts USING GIN (payload);

CREATE INDEX IF NOT EXISTS partner_idx_evidence_class      ON partner.evidence_files (class_cn);
CREATE INDEX IF NOT EXISTS partner_idx_evidence_object     ON partner.evidence_files (object_id);

CREATE INDEX IF NOT EXISTS partner_idx_asset_files_cat     ON partner.asset_files (category);
CREATE INDEX IF NOT EXISTS partner_idx_asset_files_proj    ON partner.asset_files (project_id);

COMMIT;
