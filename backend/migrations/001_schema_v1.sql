-- =====================================================================
-- 招投标智能体 · 匹配数据持久化 schema v1
-- 迁移编号：001
-- 目标库：PostgreSQL 14+（开发环境 postgres:16）
--
-- 配套文档：
--   docs2/匹配数据持久化设计（PostgreSQL）.md      （表结构与字段清单）
--   docs2/数据读写架构与迁移实施方案.md            （读写切换与 9 步迁移）
--
-- 设计约定
--   * 内部主键 id bigserial；业务键 biz_id text unique（沿用现有字符串 ID，
--     如 SEG-000001 / technical-0001 / prd-49431889-9，前端与标书侧无需改动）
--   * 每表带 org_id（多租户预留）、created_at、updated_at
--   * 枚举用 text + CHECK，不用 PG enum 类型（改值域不需 ALTER TYPE）
--   * 长正文 text（自动 TOAST）；结构多变且不参与查询的用 jsonb
--   * 时间统一 timestamptz（旧 JSON 混用 +08:00 与 Z，迁移脚本需归一）
--   * 原始文件（招标文件/证据文件/导出件）不入库，只存 path + sha256
--
-- 匹配状态 6 态契约（frontend 只认这 6 个，不得新增第 7 态）：
--   满足 | 部分满足 | 不满足 | 待补材料 | 待澄清 | 不适用
--
-- 幂等性：本文件可重复执行（全用 IF NOT EXISTS / OR REPLACE）。
-- =====================================================================

BEGIN;

-- =====================================================================
-- B 组：招标信息与文件
-- =====================================================================

CREATE TABLE IF NOT EXISTS source_site (
    id              bigserial PRIMARY KEY,
    biz_id          text        NOT NULL UNIQUE,                 -- siteId
    org_id          text        NOT NULL DEFAULT 'org-001',
    site_name       text,
    site_type       text,
    base_url        text,
    region          text,
    access_mode     text,
    status          text,
    crawl_frequency integer,
    last_success_at timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- 公告（替代 runtime/catalog.json；现 127 条 = 83.5 MB，主因是行内大数组）
CREATE TABLE IF NOT EXISTS tender_announcement (
    id                    bigserial PRIMARY KEY,
    biz_id                text        NOT NULL UNIQUE,           -- announcementId
    org_id                text        NOT NULL DEFAULT 'org-001',
    site_id               text REFERENCES source_site(biz_id) ON DELETE SET NULL,
    title                 text,
    project_name          text,
    category              text,
    region                text,
    notice_type           text,
    published_date        date,
    source                text,
    source_url            text,
    detail_url            text,
    source_key            text,
    data_origin           text,                                  -- public_notice_crawl / local_upload
    analysis_text         text,                                  -- 公告全文
    fetched_at            timestamptz,
    -- 初筛打分
    relevance_score       integer,
    score_version         text,
    score_breakdown       jsonb       NOT NULL DEFAULT '{}'::jsonb,
    exclude_reason        text,
    next_stage            text,
    should_analyze        boolean     NOT NULL DEFAULT false,
    search_profile        jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- 当时检索画像快照
    -- 去重与提醒
    dedupe_key            text,
    dedupe_hash           text,
    reminder_status       text,
    reminded_at           timestamptz,
    notification_channels text[]      NOT NULL DEFAULT '{}',
    follow_up             boolean     NOT NULL DEFAULT false,
    read                  boolean     NOT NULL DEFAULT false,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_announcement_dedupe_key
    ON tender_announcement (dedupe_key) WHERE dedupe_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_announcement_published ON tender_announcement (published_date DESC);
CREATE INDEX IF NOT EXISTS ix_announcement_score ON tender_announcement (relevance_score DESC);

-- 公告命中术语子表
-- 把 matchedBusinessDirections/matchedScenarios/matchedProducts/matchedCapabilities
-- /matchedKeywords/matchReasons/matchEvidence 这些大数组从行内拆出来。
-- dimension ∈ businessDirection / industryScenario / product / capability /
--             configuredKeyword / keyword / reason / evidence
CREATE TABLE IF NOT EXISTS announcement_match_term (
    id              bigserial PRIMARY KEY,
    announcement_id text NOT NULL REFERENCES tender_announcement(biz_id) ON DELETE CASCADE,
    dimension       text NOT NULL,
    term            text NOT NULL,
    points          double precision,
    max_points      double precision,
    evidence        text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (announcement_id, dimension, term)
);
CREATE INDEX IF NOT EXISTS ix_ann_term_ann ON announcement_match_term (announcement_id, dimension);
CREATE INDEX IF NOT EXISTS ix_ann_term_lookup ON announcement_match_term (dimension, term);

CREATE TABLE IF NOT EXISTS tender_project (
    id                         bigserial PRIMARY KEY,
    biz_id                     text        NOT NULL UNIQUE,      -- projectId
    org_id                     text        NOT NULL DEFAULT 'org-001',
    announcement_id            text REFERENCES tender_announcement(biz_id) ON DELETE SET NULL,
    project_name               text,
    project_code               text,
    project_type               text,
    procurement_method         text,
    purchaser_name             text,
    agency_name                text,
    region                     text,
    industry_tags              text[]      NOT NULL DEFAULT '{}',
    budget_amount              double precision,
    currency                   text,
    project_status             text,
    -- 解析出的项目关键信息（原 project.parsed）
    service_type               text,
    service_location           text,
    service_period             text,
    registration_period        text,
    bid_deadline               text,
    tenderer_name              text,
    signature_seal_requirement text,
    joint_venture_requirement  text,
    created_at                 timestamptz NOT NULL DEFAULT now(),
    updated_at                 timestamptz NOT NULL DEFAULT now()
);

-- 招标文件（一个项目可有多个版本/类型）
CREATE TABLE IF NOT EXISTS tender_document (
    id                 bigserial PRIMARY KEY,
    biz_id             text        NOT NULL UNIQUE,              -- documentId
    org_id             text        NOT NULL DEFAULT 'org-001',
    project_id         text REFERENCES tender_project(biz_id) ON DELETE CASCADE,
    announcement_id    text REFERENCES tender_announcement(biz_id) ON DELETE SET NULL,
    document_type      text,
    document_name      text,
    version_no         varchar(32) NOT NULL DEFAULT 'V001',      -- 对应 documentVersionId 的 V 段
    document_kind      text,                                     -- tender_document / response_document / notice
    issued_at          timestamptz,
    file_path          text,                                     -- 文件地址（现在=本地 runtime 相对路径，将来=OSS key/URL）
    file_hash          text,                                     -- contentHash / sha256
    storage_kind       text        NOT NULL DEFAULT 'local' CHECK (storage_kind IN ('local','oss')),  -- local 本地磁盘 / oss 对象存储
    mime_type          text,
    size_bytes         bigint,
    page_count         integer,
    parse_status       text,
    parse_method       text,
    is_current         boolean     NOT NULL DEFAULT true,
    acquisition_status text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
-- document_name 可空，用 COALESCE 避免 NULL 破坏唯一性判定
CREATE UNIQUE INDEX IF NOT EXISTS ux_document_project_version
    ON tender_document (project_id, COALESCE(document_name, ''), version_no);
CREATE INDEX IF NOT EXISTS ix_document_project ON tender_document (project_id) WHERE is_current;

CREATE TABLE IF NOT EXISTS document_acquisition (
    id               bigserial PRIMARY KEY,
    biz_id           text        NOT NULL UNIQUE,
    org_id           text        NOT NULL DEFAULT 'org-001',
    document_id      text REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    user_id          text,
    acquisition_mode text        NOT NULL DEFAULT 'manual',      -- manual / upload
    requested_at     timestamptz,
    acquired_at      timestamptz,
    uploaded_at      timestamptz,
    file_ref         text,
    file_hash        text,
    storage_kind     text        NOT NULL DEFAULT 'local' CHECK (storage_kind IN ('local','oss')),  -- local 本地磁盘 / oss 对象存储
    status           text,
    operator_note    text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- =====================================================================
-- C 组：解析结果（替代 workspace.project.document）
--    注意：实测 segments 与 units 内容完全相同，合并为一张表（is_unit 标记）
-- =====================================================================

CREATE TABLE IF NOT EXISTS document_segment (
    id              bigserial PRIMARY KEY,
    biz_id          text        NOT NULL,                        -- SEG-000001
    org_id          text        NOT NULL DEFAULT 'org-001',
    document_id     text        NOT NULL REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    order_no        integer     NOT NULL,                        -- 原文顺序
    content_type    text        NOT NULL,                        -- paragraph / table_row / ...
    text            text,
    source_locator  text,                                        -- 第 N 页 / TABLE-001第2行
    paragraph_index integer,
    table_id        text,
    row_index       integer,
    cell_index      integer,
    style           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    cells           jsonb,                                       -- 表格行时有
    is_unit         boolean     NOT NULL DEFAULT false,           -- 原 units 视角标记
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, biz_id)
);
CREATE INDEX IF NOT EXISTS ix_segment_document_order ON document_segment (document_id, order_no);
CREATE INDEX IF NOT EXISTS ix_segment_table ON document_segment (document_id, table_id) WHERE table_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS document_section (
    id                 bigserial PRIMARY KEY,
    biz_id             text        NOT NULL,                     -- SEC-0021
    org_id             text        NOT NULL DEFAULT 'org-001',
    document_id        text        NOT NULL REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    parent_biz_id      text,
    level              integer,
    heading            text,
    section_type       text,                                     -- section_classifier 输出
    section_type_hits  text[]      NOT NULL DEFAULT '{}',
    is_mixed           boolean     NOT NULL DEFAULT false,
    is_major           boolean     NOT NULL DEFAULT false,
    section_path       text[]      NOT NULL DEFAULT '{}',
    heading_segment_id text,
    start_locator      text,
    end_locator        text,
    segment_ids        text[]      NOT NULL DEFAULT '{}',
    text               text,
    implicit           boolean     NOT NULL DEFAULT false,
    order_no           integer,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, biz_id)
);
CREATE INDEX IF NOT EXISTS ix_section_document_type ON document_section (document_id, section_type);

CREATE TABLE IF NOT EXISTS document_table (
    id             bigserial PRIMARY KEY,
    biz_id         text        NOT NULL,                         -- TABLE-018
    org_id         text        NOT NULL DEFAULT 'org-001',
    document_id    text        NOT NULL REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    order_no       integer,
    row_count      integer,
    column_count   integer,
    grid_widths    text[]      NOT NULL DEFAULT '{}',
    rows           jsonb       NOT NULL DEFAULT '[]'::jsonb,
    section_id     text,
    source_locator text,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, biz_id)
);

-- =====================================================================
-- D 组：提取结果（替代 extraction.requirements / analysisPack.targetModules）
-- =====================================================================

CREATE TABLE IF NOT EXISTS requirement_group (
    id            bigserial PRIMARY KEY,
    biz_id        text        NOT NULL,
    org_id        text        NOT NULL DEFAULT 'org-001',
    document_id   text        NOT NULL REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    group_key     text        NOT NULL,   -- technical/qualification/commercial/evaluation/
                                          -- contract/template/project_info/rejection
    group_name    text,
    primary_route text,
    handling_mode text,
    group_no      text,
    description   text,
    order_no      integer,
    source_page   text,
    item_count    integer     NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    -- biz_id / group_key 只在单个文档内唯一，故用复合唯一约束（供复合外键引用）
    UNIQUE (document_id, group_key),
    UNIQUE (document_id, biz_id)
);

-- 一条可独立检查、匹配和追溯的招标要求（实测 26 字段 + 扩展）
CREATE TABLE IF NOT EXISTS tender_requirement (
    id                    bigserial PRIMARY KEY,
    biz_id                text        NOT NULL,                  -- technical-0001（前端 requirementId）
    org_id                text        NOT NULL DEFAULT 'org-001',
    document_id           text        NOT NULL REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    group_id              text,        -- 要求分组（复合外键，见本表末尾）
    -- 分类与路由
    target_module         text,        -- 8 域主线（前端优先读）
    category              text,        -- 15 类兼容层
    target_module_label   text,
    requirement_class     text CHECK (requirement_class IS NULL
                               OR requirement_class IN ('evidence','rejection','writing','compliance','fill')),
    primary_route         text,
    match_domain          text,
    handling_mode         text,
    -- 文本
    title                 text,
    text                  text,
    raw_text              text,
    normalized_text       text,
    -- 强制性与风险
    mandatory             boolean     NOT NULL DEFAULT false,
    is_rejection          boolean     NOT NULL DEFAULT false,
    importance            text,
    decision_impact       text,
    -- 溯源定位
    source_segment_id     text,
    source_segment_ids    text[]      NOT NULL DEFAULT '{}',
    source_locator        text,
    section_id            text,
    section_path          text[]      NOT NULL DEFAULT '{}',
    table_ids             text[]      NOT NULL DEFAULT '{}',
    -- 技术类专属
    product_name          text,
    target_type           text,
    source_column         text,
    quantity              text,
    unit                  text,
    parameters            text,
    -- 条款编号与拆条
    requirement_number    text,
    clause_index          integer,
    clause_count          integer,
    -- 响应与复核
    response_type         text,
    evidence_need         text,
    granularity           text,
    review_required       boolean     NOT NULL DEFAULT false,
    review_mode           text,
    review_reason         text,
    -- 置信与来源
    confidence            double precision,
    extraction_confidence double precision,
    layer                 integer,
    source_stage          text,
    source_file_id        text,
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, biz_id),
    -- 复合外键：biz_id 只保证文档内唯一，故带 document_id 一起引用
    FOREIGN KEY (document_id, group_id)
        REFERENCES requirement_group(document_id, biz_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS ix_req_document_module ON tender_requirement (document_id, target_module);
CREATE INDEX IF NOT EXISTS ix_req_mandatory ON tender_requirement (document_id) WHERE mandatory;
CREATE INDEX IF NOT EXISTS ix_req_rejection ON tender_requirement (document_id) WHERE is_rejection;

CREATE TABLE IF NOT EXISTS requirement_segment (
    id             bigserial PRIMARY KEY,
    document_id    text NOT NULL,
    requirement_id text NOT NULL,
    segment_id     text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (document_id, requirement_id, segment_id),
    FOREIGN KEY (document_id, requirement_id)
        REFERENCES tender_requirement(document_id, biz_id) ON DELETE CASCADE
);

-- =====================================================================
-- E 组：匹配结果（替代 fineReview）—— 核心
-- =====================================================================

-- 一次匹配评估（= 一个 run 的一次 fineReview 汇总）
CREATE TABLE IF NOT EXISTS match_assessment (
    id                      bigserial PRIMARY KEY,
    biz_id                  text        NOT NULL UNIQUE,         -- assessmentId
    org_id                  text        NOT NULL DEFAULT 'org-001',
    project_id              text REFERENCES tender_project(biz_id) ON DELETE CASCADE,
    document_id             text REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    run_id                  text,
    -- 用了哪一版企业事实（D1 决策：结论可复现的关键）
    fact_sync_batch_id      text REFERENCES fact_sync_log(sync_batch_id) ON DELETE SET NULL,
    score                   integer,
    decision                text,
    recommendation          text,
    clause_count            integer     NOT NULL DEFAULT 0,
    status_counts           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    class_counts            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    evidence_status_counts  jsonb       NOT NULL DEFAULT '{}'::jsonb,
    hard_blocker_count      integer     NOT NULL DEFAULT 0,
    rejection_count         integer     NOT NULL DEFAULT 0,
    rejection_pending_count integer     NOT NULL DEFAULT 0,
    pending_material_count  integer     NOT NULL DEFAULT 0,
    mandatory_count         integer     NOT NULL DEFAULT 0,
    mandatory_closed_count  integer     NOT NULL DEFAULT 0,
    evidence_required_count integer     NOT NULL DEFAULT 0,
    evidence_covered_count  integer     NOT NULL DEFAULT 0,
    evidence_coverage_rate  integer,
    uncertain_count         integer     NOT NULL DEFAULT 0,
    not_satisfied_count     integer     NOT NULL DEFAULT 0,
    summary                 jsonb       NOT NULL DEFAULT '{}'::jsonb,  -- 22 字段汇总原样透出
    assessed_at             timestamptz NOT NULL DEFAULT now(),
    manual_confirmed        boolean     NOT NULL DEFAULT false,
    status                  text,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_assessment_project ON match_assessment (project_id, assessed_at DESC);

-- 逐条匹配结果（= fineReview.rows[i]，前端唯一认的行契约）
CREATE TABLE IF NOT EXISTS match_item (
    id                       bigserial PRIMARY KEY,
    biz_id                   text        NOT NULL,               -- requirementId，如 technical-0001
    org_id                   text        NOT NULL DEFAULT 'org-001',
    assessment_id            text        NOT NULL REFERENCES match_assessment(biz_id) ON DELETE CASCADE,
    document_id              text REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    requirement_id           text,                               -- 软回指 tender_requirement.biz_id
    comparison_id            text,
    source_file_id           text,
    -- 要求文本（冗余保留，保证前端契约不依赖 join）
    target_module            text,
    target_module_label      text,
    category                 text,
    requirement              text,
    raw_text                 text,
    normalized_requirement   text,
    title                    text,
    text                     text,
    clause_index             integer,
    clause_count             integer,
    requirement_number       text,
    quantity                 text,
    unit                     text,
    parameters               text,
    product_name             text,
    source_column            text,
    target_type              text,
    -- 分类与路由
    primary_route            text,
    match_domain             text,
    requirement_class        text,
    handling_mode            text,
    response_type            text,
    evidence_need            text,
    decision_impact          text,
    granularity              text,
    layer                    integer,
    source_stage             text,
    -- 溯源
    source_segment_id        text,
    source_segment_ids       text[]      NOT NULL DEFAULT '{}',
    source_locator           text,
    section_id               text,
    section_path             text[]      NOT NULL DEFAULT '{}',
    table_ids                text[]      NOT NULL DEFAULT '{}',
    source_text              text,
    -- 强制与复核
    mandatory                boolean     NOT NULL DEFAULT false,
    is_rejection             boolean     NOT NULL DEFAULT false,
    importance               text,
    review_required          boolean     NOT NULL DEFAULT false,
    review_mode              text,
    review_reason            text,
    -- 匹配结论。注意：status 是「分模块词表」，不可用单一 CHECK 约束——
    --   证据匹配(evidence)      : 满足/部分满足/不满足/待补材料/待澄清/不适用（6 态契约，不得新增第 7 态）
    --   否决检查(rejection)     : 待核查/已确认安全/有风险
    --   撰写类(writing)         : 待编制/已编制
    --   仅展示(display)         : 已提取/待确认
    status                   text        NOT NULL,
    reason                   text,
    suggestion               text,
    confidence               double precision,
    status_source            text,
    risk                     text,
    risk_level               text,       -- 低 / 中 / 高
    review_status            text,       -- not_required / pending / confirmed / rejected（无需复核/待复核/已复核/复核退回）
    evidence_coverage_status text,       -- 已核验证据 / 待核验证据 / 仅对象关联 / 未覆盖
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now(),
    UNIQUE (assessment_id, biz_id)
);
CREATE INDEX IF NOT EXISTS ix_item_assessment_order ON match_item (assessment_id, clause_index);
CREATE INDEX IF NOT EXISTS ix_item_status ON match_item (assessment_id, status);
CREATE INDEX IF NOT EXISTS ix_item_review ON match_item (assessment_id, review_status);
CREATE INDEX IF NOT EXISTS ix_item_module ON match_item (assessment_id, target_module);

-- 命中证明材料明细（evidenceItems，实测 18 字段）
CREATE TABLE IF NOT EXISTS match_item_evidence (
    id                  bigserial PRIMARY KEY,
    match_item_id       bigint      NOT NULL REFERENCES match_item(id) ON DELETE CASCADE,
    evidence_id         text,        -- 证据 ID（如 prd-49431889-9）
    material_id         text,
    name                text,
    excerpt             text,
    source_type         text,        -- company_fact / evidence / ...
    source_locator      text,        -- 证明材料位置（相对路径）
    locator             text,
    status              text,
    verification_status text,
    evidence_level      text,
    level               text,
    valid_from          date,
    valid_until         date,
    source_file_ids     text[]      NOT NULL DEFAULT '{}',
    source_files        text[]      NOT NULL DEFAULT '{}',
    is_historical       boolean     NOT NULL DEFAULT false,
    is_expired          boolean     NOT NULL DEFAULT false,
    is_verified         boolean     NOT NULL DEFAULT false,
    order_no            integer,
    created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_evidence_item ON match_item_evidence (match_item_id);
CREATE INDEX IF NOT EXISTS ix_evidence_evidence_id ON match_item_evidence (evidence_id);

-- 命中的企业事实（matchedObjectIds 落表；可反查"这条事实被哪些要求引用"）
CREATE TABLE IF NOT EXISTS match_item_fact (
    id            bigserial PRIMARY KEY,
    match_item_id bigint      NOT NULL REFERENCES match_item(id) ON DELETE CASCADE,
    fact_kind     text        NOT NULL,  -- qualification_certificate / performance_case / staff_member / ...
    fact_biz_id   text        NOT NULL,
    fact_snapshot jsonb,                 -- 命中时的事实快照，保证结论可复现
    created_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (match_item_id, fact_kind, fact_biz_id)
);
CREATE INDEX IF NOT EXISTS ix_item_fact_lookup ON match_item_fact (fact_kind, fact_biz_id);

-- 风险清单（risks / rejectionRisks / hardBlockers 合并，用 risk_type 区分）
CREATE TABLE IF NOT EXISTS match_risk (
    id             bigserial PRIMARY KEY,
    biz_id         text        NOT NULL UNIQUE,
    assessment_id  text        NOT NULL REFERENCES match_assessment(biz_id) ON DELETE CASCADE,
    match_item_id  bigint REFERENCES match_item(id) ON DELETE SET NULL,
    risk_type      text        NOT NULL CHECK (risk_type IN ('risk','rejection','hard_blocker')),
    level          text,
    title          text,
    detail         text,
    source_locator text,
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_risk_assessment ON match_risk (assessment_id, risk_type);

-- 人工复核记录（审计）
-- 替代现状：改一行结论要整写 846MB 文件；PG 化后是一条 UPDATE + 一条审计 INSERT
CREATE TABLE IF NOT EXISTS review_record (
    id            bigserial PRIMARY KEY,
    biz_id        text        NOT NULL UNIQUE,
    org_id        text        NOT NULL DEFAULT 'org-001',
    match_item_id bigint REFERENCES match_item(id) ON DELETE SET NULL,
    assessment_id text REFERENCES match_assessment(biz_id) ON DELETE CASCADE,
    review_type   text,
    reviewer_name text,
    from_status   text,
    to_status     text,       -- 复核可改到任意模块词表内的状态，故不加 CHECK
    comment       text,
    started_at    timestamptz,
    completed_at  timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_review_item ON review_record (match_item_id, created_at DESC);

-- =====================================================================
-- F 组：运行与可观测（替代 runs/run-*.json）
-- =====================================================================

CREATE TABLE IF NOT EXISTS analysis_run (
    id                        bigserial PRIMARY KEY,
    biz_id                    text        NOT NULL UNIQUE,       -- runId
    org_id                    text        NOT NULL DEFAULT 'org-001',
    project_id                text REFERENCES tender_project(biz_id) ON DELETE CASCADE,
    document_version_id       text,
    stage                     text,
    status                    text,
    run_schema_version        text,
    created_at                timestamptz NOT NULL DEFAULT now(),  -- 业务创建时间（原 JSON createdAt）
    started_at                timestamptz,
    finished_at               timestamptz,
    elapsed_ms                bigint,
    parser_name               text,
    parser_version            text,
    schema_version            text,
    prompt_version            text,
    llm_model                 text,
    llm_parameters            jsonb       NOT NULL DEFAULT '{}'::jsonb,
    llm_call_count            integer     NOT NULL DEFAULT 0,
    llm_success_count         integer     NOT NULL DEFAULT 0,
    llm_failure_count         integer     NOT NULL DEFAULT 0,
    llm_input_tokens          bigint      NOT NULL DEFAULT 0,
    llm_output_tokens         bigint      NOT NULL DEFAULT 0,
    manual_change_count       integer     NOT NULL DEFAULT 0,
    external_agent_call_count integer     NOT NULL DEFAULT 0,
    input_artifact_paths      text[]      NOT NULL DEFAULT '{}',
    output_artifact_paths     text[]      NOT NULL DEFAULT '{}',
    metrics                   jsonb       NOT NULL DEFAULT '{}'::jsonb,
    warnings                  text[]      NOT NULL DEFAULT '{}',
    errors                    text[]      NOT NULL DEFAULT '{}',
    updated_at                timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_run_project ON analysis_run (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS analysis_run_stage (
    id                    bigserial PRIMARY KEY,
    run_id                text        NOT NULL REFERENCES analysis_run(biz_id) ON DELETE CASCADE,
    stage                 text        NOT NULL,   -- parse_document / eligibility / company_relevance /
                                                  -- tender_fine_review / extract_requirements /
                                                  -- match_company / build_decision_summary
    status                text,
    started_at            timestamptz,
    finished_at           timestamptz,
    elapsed_ms            bigint,
    retry_count           integer     NOT NULL DEFAULT 0,
    input_artifact_paths  text[]      NOT NULL DEFAULT '{}',
    output_artifact_paths text[]      NOT NULL DEFAULT '{}',
    metrics               jsonb       NOT NULL DEFAULT '{}'::jsonb,
    warnings              text[]      NOT NULL DEFAULT '{}',
    errors                text[]      NOT NULL DEFAULT '{}',
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, stage, started_at)
);
CREATE INDEX IF NOT EXISTS ix_run_stage_run ON analysis_run_stage (run_id, started_at);

CREATE TABLE IF NOT EXISTS llm_call_log (
    id            bigserial PRIMARY KEY,
    run_id        text REFERENCES analysis_run(biz_id) ON DELETE CASCADE,
    stage         text,
    target_module text,
    provider      text,
    model         text,
    prompt_chars  integer,
    output_chars  integer,
    input_tokens  bigint,
    output_tokens bigint,
    elapsed_ms    bigint,
    success       boolean     NOT NULL DEFAULT false,
    error         text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_llm_run ON llm_call_log (run_id, created_at);

-- =====================================================================
-- G 组：标书产出（替代 workspace.draft + runtime/bid_writing/*）
--    必须一起迁移，否则 workspaces.json 还得留着大 JSON
-- =====================================================================

CREATE TABLE IF NOT EXISTS workspace (
    id              bigserial PRIMARY KEY,
    biz_id          text        NOT NULL UNIQUE,                 -- workspaceId
    org_id          text        NOT NULL DEFAULT 'org-001',
    project_id      text REFERENCES tender_project(biz_id) ON DELETE CASCADE,
    title           text,
    analysis_run_id text REFERENCES analysis_run(biz_id) ON DELETE SET NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS workspace_draft (
    id            bigserial PRIMARY KEY,
    workspace_id  text        NOT NULL REFERENCES workspace(biz_id) ON DELETE CASCADE,
    draft_version integer     NOT NULL DEFAULT 1,
    draft         text,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (workspace_id, draft_version)
);

CREATE TABLE IF NOT EXISTS bid_document (
    id           bigserial PRIMARY KEY,
    biz_id       text        NOT NULL UNIQUE,
    workspace_id text        NOT NULL REFERENCES workspace(biz_id) ON DELETE CASCADE,
    status       text,
    mode         text,        -- open 明标 / secret 暗标（代码用 secret，不是 blind）
    export_path  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bid_section (
    id              bigserial PRIMARY KEY,
    biz_id          text        NOT NULL,
    bid_document_id text        NOT NULL REFERENCES bid_document(biz_id) ON DELETE CASCADE,
    section_id      text        NOT NULL,
    title           text,
    content         text,
    version         integer     NOT NULL DEFAULT 1,
    source_refs     jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- 防幻觉溯源：factKey/factId/name/evidencePath
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (bid_document_id, section_id, version)
);

CREATE TABLE IF NOT EXISTS bid_plan (
    id           bigserial PRIMARY KEY,
    workspace_id text        NOT NULL REFERENCES workspace(biz_id) ON DELETE CASCADE,
    plan         jsonb       NOT NULL DEFAULT '[]'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- =====================================================================
-- H 组：视图（派生，不落表 —— 避免出现新的一轮重复存储）
--    H1 是核心：拼回 fineReview.rows 的原始 JSON 结构，保证前端契约不变
-- =====================================================================

-- H1 逐条匹配结果完整视图（对拍基准：必须与旧 JSON 的 rows 字段级一致）
CREATE OR REPLACE VIEW v_match_item_full AS
SELECT
    i.biz_id                                            AS "requirementId",
    i.biz_id                                            AS "id",
    i.assessment_id,
    i.document_id,
    i.target_module                                     AS "targetModule",
    i.target_module_label                               AS "targetModuleLabel",
    i.category,
    i.requirement,
    i.raw_text                                          AS "rawText",
    i.normalized_requirement                            AS "normalizedRequirement",
    i.title,
    i.text,
    i.mandatory,
    i.is_rejection                                      AS "isRejection",
    i.importance,
    i.confidence,
    i.handling_mode                                     AS "handlingMode",
    i.source_segment_id                                 AS "sourceSegmentId",
    i.source_segment_ids                                AS "sourceSegmentIds",
    i.source_locator                                    AS "sourceLocator",
    i.section_id                                        AS "sectionId",
    i.section_path                                      AS "sectionPath",
    i.table_ids                                         AS "tableIds",
    i.product_name                                      AS "productName",
    i.target_type                                       AS "targetType",
    i.source_column                                     AS "sourceColumn",
    i.quantity,
    i.unit,
    i.parameters,
    i.requirement_number                                AS "requirementNumber",
    i.clause_index                                      AS "clauseIndex",
    i.clause_count                                      AS "clauseCount",
    i.match_domain                                      AS "matchDomain",
    i.primary_route                                     AS "primaryRoute",
    i.requirement_class                                 AS "requirementClass",
    i.response_type                                     AS "responseType",
    i.evidence_need                                     AS "evidenceNeed",
    i.decision_impact                                   AS "decisionImpact",
    i.granularity,
    i.layer,
    i.source_stage                                      AS "sourceStage",
    i.source_file_id                                    AS "sourceFileId",
    i.comparison_id                                     AS "comparisonId",
    i.review_required                                   AS "reviewRequired",
    i.review_mode                                       AS "reviewMode",
    i.review_reason                                     AS "reviewReason",
    i.status,
    i.reason,
    i.suggestion,
    i.status_source                                     AS "statusSource",
    i.risk,
    i.risk_level                                        AS "riskLevel",
    i.review_status                                     AS "reviewStatus",
    i.evidence_coverage_status                          AS "evidenceCoverageStatus",
    COALESCE(f.ids, '{}'::text[])                       AS "matchedObjectIds",
    COALESCE(e.items, '[]'::jsonb)                      AS "evidenceItems"
FROM match_item i
LEFT JOIN LATERAL (
    SELECT array_agg(f2.fact_biz_id) AS ids
    FROM match_item_fact f2 WHERE f2.match_item_id = i.id
) f ON true
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
        'id',            e2.evidence_id,
        'name',          e2.name,
        'status',        e2.status,
        'level',         e2.level,
        'validFrom',     e2.valid_from,
        'validUntil',    e2.valid_until,
        'sourceLocator', e2.source_locator,
        'sourceType',    e2.source_type,
        'excerpt',       e2.excerpt
    ) ORDER BY e2.order_no) AS items
    FROM match_item_evidence e2 WHERE e2.match_item_id = i.id
) e ON true;

-- H2 技术参数匹配投影（从 match_item 技术域派生，不落表）
CREATE OR REPLACE VIEW v_parameter_match AS
SELECT
    i.biz_id             AS "requirementId",
    i.requirement,
    i.category,
    i.status,
    CASE i.status
        WHEN '满足'     THEN '无偏离'
        WHEN '部分满足' THEN '负偏离'
        WHEN '不满足'   THEN '负偏离'
        WHEN '待补材料' THEN '负偏离'
        WHEN '待澄清'   THEN '待人工确认'
        ELSE '不适用'
    END                  AS deviation,
    i.reason,
    i.confidence,
    i.source_locator     AS "sourceLocator",
    i.source_segment_ids AS "sourceSegmentIds"
FROM match_item i
WHERE i.target_module = 'technical' OR i.match_domain = 'technical';

-- H3 资质审核匹配投影（资格域）
CREATE OR REPLACE VIEW v_qualification_match AS
SELECT
    i.biz_id              AS "requirementId",
    i.requirement,
    i.category,
    i.status,
    i.reason,
    COALESCE(f.ids, '{}'::text[]) AS "matchedObjectIds",
    i.source_locator      AS "sourceLocator"
FROM match_item i
LEFT JOIN LATERAL (
    SELECT array_agg(f2.fact_biz_id) AS ids
    FROM match_item_fact f2 WHERE f2.match_item_id = i.id
) f ON true
WHERE i.category IN ('qualification','performance','personnel','financial','credit','region');

-- H4 整条对标偏离表（偏离类型映射的唯一来源，禁止另写一套）
CREATE OR REPLACE VIEW v_deviation_row AS
SELECT
    ROW_NUMBER() OVER (PARTITION BY i.assessment_id ORDER BY i.clause_index, i.id) AS no,
    i.biz_id         AS "requirementId",
    i.target_module  AS "targetModule",
    i.requirement,
    i.status,
    CASE i.status
        WHEN '满足'     THEN '无偏离'
        WHEN '部分满足' THEN '负偏离'
        WHEN '不满足'   THEN '负偏离'
        WHEN '待补材料' THEN '负偏离'
        WHEN '待澄清'   THEN '待人工确认'
        ELSE '不适用'
    END              AS deviation,
    i.reason         AS response,
    i.suggestion,
    i.mandatory,
    i.is_rejection   AS "isRejection",
    i.source_locator AS "sourceLocator"
FROM match_item i;

COMMIT;

-- =====================================================================
-- 备注
-- 1) H 组视图只覆盖最常用的 4 个投影；parameterMatches / reviewOutput 的 8 域
--    分组在应用层按 target_module 聚合即可，无需再建表。
-- 2) 存量迁移时，原 workspaces.json 里 5 份重复的技术条款只落 1 份
--    （tender_requirement + match_item），投影全部由视图重算。
-- 3) 时间字段统一 timestamptz；迁移脚本需把混用的 '+08:00' 与 'Z' 归一。
-- 4) 本文件可重复执行。schema 变更请新增 002_*.sql，不要原地改历史迁移。
-- =====================================================================
