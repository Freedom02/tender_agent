-- ============================================================================
-- tender_agent 共享开发库建表脚本（与 ../schema.sql 同源）
-- 首次 `docker-compose up`（数据卷为空时）由官方镜像自动执行。
-- 已初始化的数据卷不会重跑本文件：后续表结构变更请走编号迁移脚本。
-- ============================================================================

BEGIN;

CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS catalog_projects (
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

CREATE TABLE IF NOT EXISTS workspaces (
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

CREATE TABLE IF NOT EXISTS workspace_sections (
    workspace_id      TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS workspace_fine_review_rows (
    workspace_id   TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
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

CREATE TABLE IF NOT EXISTS workspace_requirement_coverage (
    workspace_id   TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    requirement_id TEXT NOT NULL,
    section_id     TEXT,
    coverage_type  TEXT,
    status         TEXT,
    reason         TEXT,
    payload        JSONB NOT NULL,
    PRIMARY KEY (workspace_id, requirement_id)
);

CREATE TABLE IF NOT EXISTS libraries (
    key         TEXT PRIMARY KEY,
    name        TEXT,
    description TEXT,
    position    INTEGER,
    payload     JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS library_documents (
    id            TEXT PRIMARY KEY,
    library_key   TEXT NOT NULL REFERENCES libraries(key) ON DELETE CASCADE,
    name          TEXT,
    type          TEXT,
    saved_at      TEXT,
    source        TEXT,
    storage_path  TEXT,
    content_hash  TEXT,
    mime_type     TEXT,
    size          BIGINT,
    preview       TEXT,
    payload       JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
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

CREATE TABLE IF NOT EXISTS automation_runs (
    id         TEXT PRIMARY KEY,
    status     TEXT,
    started_at TIMESTAMPTZ,
    position   INTEGER,
    payload    JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS bid_runs (
    run_id           TEXT PRIMARY KEY,
    workspace_id     TEXT,
    project_id       TEXT,
    provider         TEXT,
    status           TEXT,
    phase            TEXT,
    created_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ,
    parse_bundle_id  TEXT,
    payload          JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS parse_bundles (
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

CREATE TABLE IF NOT EXISTS parse_artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_id  TEXT,
    kind        TEXT,
    rel_path    TEXT,
    sha256      TEXT,
    size        BIGINT,
    payload     JSONB NOT NULL
);

CREATE TABLE IF NOT EXISTS evidence_files (
    rel_path   TEXT PRIMARY KEY,
    class_cn   TEXT,
    object_id  TEXT,
    filename   TEXT,
    ext        TEXT,
    size       BIGINT,
    sha256     TEXT,
    mtime      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS asset_files (
    rel_path   TEXT PRIMARY KEY,
    category   TEXT,
    project_id TEXT,
    format     TEXT,
    size       BIGINT,
    sha256     TEXT,
    mtime      TIMESTAMPTZ,
    payload    JSONB
);

CREATE TABLE IF NOT EXISTS runtime_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_catalog_region     ON catalog_projects (region);
CREATE INDEX IF NOT EXISTS idx_catalog_category   ON catalog_projects (category);
CREATE INDEX IF NOT EXISTS idx_catalog_origin     ON catalog_projects (data_origin);
CREATE INDEX IF NOT EXISTS idx_catalog_published  ON catalog_projects (published_date DESC);
CREATE INDEX IF NOT EXISTS idx_catalog_payload    ON catalog_projects USING GIN (payload);

CREATE INDEX IF NOT EXISTS idx_workspaces_project ON workspaces (project_id);
CREATE INDEX IF NOT EXISTS idx_workspaces_deleted ON workspaces (deleted_at);
CREATE INDEX IF NOT EXISTS idx_workspaces_payload ON workspaces USING GIN (payload);

CREATE INDEX IF NOT EXISTS idx_sections_title     ON workspace_sections (title);
CREATE INDEX IF NOT EXISTS idx_sections_kind      ON workspace_sections (kind);

CREATE INDEX IF NOT EXISTS idx_fr_rows_requirement ON workspace_fine_review_rows (requirement_id);
CREATE INDEX IF NOT EXISTS idx_fr_rows_status      ON workspace_fine_review_rows (review_status);

CREATE INDEX IF NOT EXISTS idx_lib_docs_key        ON library_documents (library_key);
CREATE INDEX IF NOT EXISTS idx_reminders_project   ON reminders (project_id);
CREATE INDEX IF NOT EXISTS idx_reminders_status    ON reminders (status);
CREATE INDEX IF NOT EXISTS idx_bid_runs_workspace  ON bid_runs (workspace_id);

CREATE INDEX IF NOT EXISTS idx_parse_bundles_ws    ON parse_bundles (workspace_id);
CREATE INDEX IF NOT EXISTS idx_parse_artifacts_proj ON parse_artifacts (project_id, kind);
CREATE INDEX IF NOT EXISTS idx_parse_artifacts_pay ON parse_artifacts USING GIN (payload);

CREATE INDEX IF NOT EXISTS idx_evidence_class      ON evidence_files (class_cn);
CREATE INDEX IF NOT EXISTS idx_evidence_object     ON evidence_files (object_id);

CREATE INDEX IF NOT EXISTS idx_asset_files_cat     ON asset_files (category);
CREATE INDEX IF NOT EXISTS idx_asset_files_proj    ON asset_files (project_id);

COMMIT;
