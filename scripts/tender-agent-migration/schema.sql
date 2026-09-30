-- ============================================================================
-- tender_agent 本地数据 → PostgreSQL 16 建表脚本
-- ----------------------------------------------------------------------------
-- 设计原则：
--   1) 每条业务实体一行，主键 = 原 JSON 中的业务 id，保证幂等 upsert。
--   2) 每个实体表保留一个 `payload JSONB` 完整保真副本（可回滚、可审计），
--      同时把真正"行级"的大集合（章节 / 细评审条款 / 覆盖关系）拆成明细表，
--      供应用后续按行增量读写，避免重蹈"每次全量重写 30MB JSON"的覆辙。
--   3) 2.6GB 证明文件不入库，文件仍留盘（或迁对象存储），这里只存元数据。
--   4) 企业本体事实仍以 M3 图库为单一来源，本库不复制。
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. 配置（取代 agent-config.json；含明文密钥，生产环境建议加密或改环境变量）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_config (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- 1. 招标公告 / 项目库（runtime/catalog.json）
-- ---------------------------------------------------------------------------
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
    position       INTEGER,             -- 原 JSON 数组中的顺序（应用接入后使用）
    payload        JSONB NOT NULL
);

-- ---------------------------------------------------------------------------
-- 2. 工作区（runtime/workspaces.json + workspaces-deleted.json 墓碑）
--    payload = 原始 workspace 完整对象（含 project/reasoning/bidDocument），保真。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workspaces (
    id            TEXT PRIMARY KEY,
    project_id    TEXT,
    title         TEXT,
    draft         TEXT,
    draft_version INTEGER,
    created_at    TIMESTAMPTZ,
    updated_at    TIMESTAMPTZ,
    deleted_at    TIMESTAMPTZ,          -- 原 workspaces-deleted.json 墓碑 → 逻辑删除
    position      INTEGER,              -- 原 JSON 对象中的顺序（应用接入后使用）
    payload       JSONB NOT NULL
);

-- 章节明细（bidDocument.plan[]）
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

-- 细评审条款明细（reasoning.fineReview.rows[]）
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

-- 条款覆盖关系（bidDocument.requirementCoverage[]）
CREATE TABLE IF NOT EXISTS workspace_requirement_coverage (
    workspace_id  TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    requirement_id TEXT NOT NULL,
    section_id    TEXT,
    coverage_type TEXT,
    status        TEXT,
    reason        TEXT,
    payload       JSONB NOT NULL,
    PRIMARY KEY (workspace_id, requirement_id)
);

-- ---------------------------------------------------------------------------
-- 3. 资料库（runtime/libraries.json）
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 4. 跟进提醒（runtime/reminders.json）
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 5. 自动化运行记录（runtime/automation-runs.json）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS automation_runs (
    id         TEXT PRIMARY KEY,
    status     TEXT,
    started_at TIMESTAMPTZ,
    position   INTEGER,
    payload    JSONB NOT NULL
);

-- ---------------------------------------------------------------------------
-- 6. 投标运行（runtime/bid_runs.json，cloud_agent/run_repository.py）
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- 7. 解析产物（runtime/projects/<id>/parse/parse-bundle.json 及 pack/tender_pack.json、
--    parse/template-fragments/*.json）
-- ---------------------------------------------------------------------------
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
    artifact_id TEXT PRIMARY KEY,        -- = rel_path（唯一）
    project_id  TEXT,
    kind        TEXT,                    -- 'tender_pack' | 'template_fragment'
    rel_path    TEXT,
    sha256      TEXT,
    size        BIGINT,
    payload     JSONB NOT NULL
);

-- ---------------------------------------------------------------------------
-- 8. 证明文件元数据（runtime/libraries/company/证明材料/**，文件仍留盘）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evidence_files (
    rel_path   TEXT PRIMARY KEY,         -- 相对 runtime/ 的路径（正斜杠）
    class_cn   TEXT,                     -- 资质认证 / 类似业绩 / 证照凭证 / 技术产品 / 技术参数 / 项目人员
    object_id  TEXT,                     -- 事实业务主键（目录序号或手动 id）
    filename   TEXT,
    ext        TEXT,
    size       BIGINT,
    sha256     TEXT,
    mtime      TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- 9. 留盘文件资产元数据（上传源文件 / 投标输出 / 模板 / 导入模板 / 运行副本）
--    文件本身仍留盘（或迁对象存储），这里只登记元数据；.json 顺带把内容存进 payload。
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS asset_files (
    rel_path   TEXT PRIMARY KEY,      -- 相对 runtime/ 的路径（正斜杠）
    category   TEXT,                  -- project_source / bid_output / run_copy / library_template / library_asset
    project_id TEXT,
    format     TEXT,                  -- json / docx / pdf / xlsx ...
    size       BIGINT,
    sha256     TEXT,
    mtime      TIMESTAMPTZ,
    payload    JSONB                  -- 仅 .json 文件有内容，二进制为 NULL
);

-- ---------------------------------------------------------------------------
-- 10. 运行时杂项状态（runtime/scheduler-state.json 等单键对象）
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runtime_state (
    key        TEXT PRIMARY KEY,
    value      JSONB NOT NULL,
    updated_at TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- 索引
-- ---------------------------------------------------------------------------
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
