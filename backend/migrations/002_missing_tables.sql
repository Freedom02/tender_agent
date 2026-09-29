-- =====================================================================
-- 招投标智能体 · 补表迁移（缺口表）
-- 迁移编号：002
-- 目标库：PostgreSQL 14+（开发环境 postgres:16）
--
-- 配套文档：
--   docs2/PostgreSQL表字段总清单与补表计划.md   （缺口分析与字段设计）
--   docs2/附录A-已建37表字段清单.md             （001 已建表逐字段清单）
--   docs2/招标初筛与详细匹配本体设计方案.md      （本体蓝图 24 类，本文件的对齐依据）
--
-- 本文件补齐 001 未覆盖的 14 张表，来源四类：
--   A. 初筛与提醒链：本体蓝图要求，001 完全缺表（现被揉进 tender_announcement 字段）
--   B. 抽取结果：  analysisPack.templates / projectInfo 无表接收
--   C. reasoning： procurementNeeds / eligibility / decisionSummary 无表接收
--   D. 运行与治理：automation-runs.json 无表；治理元数据（本体治理产物）
--
-- 合并说明（刻意避免造近似重复表）：
--   * ScreeningAssessment（公告初筛）与 reasoning.eligibility（招标文件资格预评估）
--     合并为 screening_assessment，用 stage 列区分 announcement / document
--   * reminders.json 与蓝图 NotificationEvent 合并为 reminder，
--     用 channels[] + delivery_status 承载多渠道
--
-- 设计约定（与 001 完全一致）：
--   * 内部主键 id bigserial；业务键 biz_id text unique（沿用旧 JSON 的字符串 ID）
--   * 每表带 org_id（多租户预留）、created_at、updated_at
--   * 枚举用 text + CHECK，不用 PG enum 类型
--   * 时间统一 timestamptz；结构多变且不参与查询的用 jsonb
--
-- 幂等性：本文件可重复执行（全用 IF NOT EXISTS）。
-- 注意：  003（治理元数据 COMMENT ON + meta_* 表内容）待组长的
--         「PostgreSQL 导入本体 skill」产出后执行，避免口径不一致。
-- =====================================================================

BEGIN;

-- =====================================================================
-- I 组：初筛与提醒链
--    对齐本体蓝图：Organization ->configures-> ScreeningProfile
--                  ->contains_constraint-> ScreeningConstraint
--                  ->applies-> ScreeningAssessment ->records_check-> ScreeningCheck
--                  ->triggers-> NotificationEvent
-- =====================================================================

-- I1 公告采集规则（"从哪些网站、以什么频率、抓什么公告"）
CREATE TABLE IF NOT EXISTS monitoring_rule (
    id                bigserial PRIMARY KEY,
    biz_id            text        NOT NULL UNIQUE,               -- ruleId
    org_id            text        NOT NULL DEFAULT 'org-001',
    rule_name         text        NOT NULL,
    enabled           boolean     NOT NULL DEFAULT true,
    schedule_cron     text,                                      -- 采集频率
    keyword_include   text[]      NOT NULL DEFAULT '{}',
    keyword_exclude   text[]      NOT NULL DEFAULT '{}',
    target_regions    text[]      NOT NULL DEFAULT '{}',
    target_industries text[]      NOT NULL DEFAULT '{}',
    source_scope      text[]      NOT NULL DEFAULT '{}',         -- 监控的 source_site.biz_id 列表
    description       text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_monitoring_rule_enabled ON monitoring_rule (enabled) WHERE enabled;

-- I2 企业初筛方案（"公告是否值得提醒企业"的口径与阈值）
CREATE TABLE IF NOT EXISTS screening_profile (
    id                 bigserial PRIMARY KEY,
    biz_id             text        NOT NULL UNIQUE,              -- profileId
    org_id             text        NOT NULL DEFAULT 'org-001',
    profile_name       text        NOT NULL,
    profile_type       text,
    enabled            boolean     NOT NULL DEFAULT true,
    threshold_score    double precision,                         -- 提醒阈值分
    hard_block_enabled boolean     NOT NULL DEFAULT true,
    scoring_version    text,                                     -- 评分口径版本（结论可复现的关键）
    valid_from         date,
    valid_until        date,
    description        text,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);

-- I3 初筛约束（方案中的一条可执行条件）
CREATE TABLE IF NOT EXISTS screening_constraint (
    id               bigserial PRIMARY KEY,
    biz_id           text        NOT NULL UNIQUE,                -- constraintId
    org_id           text        NOT NULL DEFAULT 'org-001',
    profile_id       text        NOT NULL REFERENCES screening_profile(biz_id) ON DELETE CASCADE,
    constraint_name  text        NOT NULL,
    constraint_type  text        NOT NULL,                       -- region/industry/service_type/amount/exclude_word/...
    operator         text,                                       -- in/not_in/gte/lte/contains/not_contains/regex
    target_value     text,
    weight           double precision,
    mandatory        boolean     NOT NULL DEFAULT false,
    enabled          boolean     NOT NULL DEFAULT true,
    order_no         integer,
    explanation      text,                                       -- 业务解释（治理要求：可读）
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_screening_constraint_profile ON screening_constraint (profile_id, order_no);

-- I4 初筛 / 资格评估（stage 区分：公告初筛 vs 招标文件资格预评估）
CREATE TABLE IF NOT EXISTS screening_assessment (
    id                       bigserial PRIMARY KEY,
    biz_id                   text        NOT NULL UNIQUE,        -- assessmentId
    org_id                   text        NOT NULL DEFAULT 'org-001',
    stage                    text        NOT NULL
        CHECK (stage IN ('announcement','document')),
    profile_id               text REFERENCES screening_profile(biz_id) ON DELETE SET NULL,
    announcement_id          text REFERENCES tender_announcement(biz_id) ON DELETE CASCADE,
    document_id              text REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    score                    double precision,
    threshold_score          double precision,                   -- 当时阈值快照
    pass_threshold           boolean,
    hard_block               boolean     NOT NULL DEFAULT false,
    eligibility_status       text,                               -- 满足 / 不确定 / 不满足
    recommendation           text,                               -- 如"不建议参与"
    risk_level               text,
    matched_constraint_count integer,
    total_constraint_count   integer,
    reason_summary           text,
    headline                 text,
    confidence               double precision,
    assessment_available     boolean,
    should_get_tender_document boolean,
    hard_blockers            jsonb       NOT NULL DEFAULT '[]'::jsonb,
    risks                    jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- 原 rejectionRisks
    status                   text,
    assessed_at              timestamptz NOT NULL DEFAULT now(),
    manual_confirmed         boolean     NOT NULL DEFAULT false,
    created_at               timestamptz NOT NULL DEFAULT now(),
    updated_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_screening_assessment_ann ON screening_assessment (announcement_id) WHERE announcement_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_screening_assessment_doc ON screening_assessment (document_id) WHERE document_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_screening_assessment_stage ON screening_assessment (stage, assessed_at DESC);

-- I5 初筛约束逐条检查（解释性：每条约束命中/未命中/未知）
CREATE TABLE IF NOT EXISTS screening_check (
    id               bigserial PRIMARY KEY,
    biz_id           text        NOT NULL UNIQUE,                -- checkId
    org_id           text        NOT NULL DEFAULT 'org-001',
    assessment_id    text        NOT NULL REFERENCES screening_assessment(biz_id) ON DELETE CASCADE,
    constraint_id    text REFERENCES screening_constraint(biz_id) ON DELETE SET NULL,
    requirement_id   text,                                       -- 文件阶段：命中的资格条款（软引用 tender_requirement.biz_id）
    result           text        NOT NULL
        CHECK (result IN ('hit','miss','unknown')),
    score            double precision,
    matched_value    text,
    reason           text,
    detail           text,
    confidence       double precision,
    manual_confirmed boolean     NOT NULL DEFAULT false,
    checked_at       timestamptz NOT NULL DEFAULT now(),
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_screening_check_assessment ON screening_check (assessment_id);

-- I6 提醒 / 通知事件（合并 reminders.json 与蓝图 NotificationEvent）
CREATE TABLE IF NOT EXISTS reminder (
    id              bigserial PRIMARY KEY,
    biz_id          text        NOT NULL UNIQUE,                 -- reminder-xxx / notification-xxx
    org_id          text        NOT NULL DEFAULT 'org-001',
    project_id      text REFERENCES tender_project(biz_id) ON DELETE CASCADE,
    announcement_id text REFERENCES tender_announcement(biz_id) ON DELETE CASCADE,
    assessment_id   text REFERENCES screening_assessment(biz_id) ON DELETE SET NULL,
    project_title   text,                                        -- 项目名快照
    channels        text[]      NOT NULL DEFAULT '{}',           -- 页内弹窗 / 企业微信 / 邮件
    title           text,
    message         text,
    detail          text,
    priority        text,
    status          text        NOT NULL DEFAULT '待处理',        -- 待处理 / 已完成
    delivery_status text,
    dedup_key       text,
    triggered_at    timestamptz NOT NULL DEFAULT now(),
    sent_at         timestamptz,
    read_at         timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_reminder_project ON reminder (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_reminder_status ON reminder (status, triggered_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_reminder_dedup ON reminder (dedup_key) WHERE dedup_key IS NOT NULL;

-- =====================================================================
-- J 组：抽取结果补齐
--   注：原 J1 document_template、J2 project_info_item 两张表已移除——
--       实测它们与 tender_requirement 存的是同一批 items（8 个模块同构），
--       已统一并入 tender_requirement（target_module='template' / 'project_info'）。
--       见 003 的模块专有列补丁。
-- =====================================================================

-- J3 采购需求（reasoning.procurementNeeds：公告阶段的轻量预关联）
--    注意：matched_* 保持 jsonb 不拆子表——它是预关联不是正式匹配结论
--          （正式结论在 001 的 E 组 match_item_* 表）。承担正式职责时再拆。
CREATE TABLE IF NOT EXISTS procurement_need (
    id                 bigserial PRIMARY KEY,
    biz_id             text        NOT NULL,                     -- need-001
    org_id             text        NOT NULL DEFAULT 'org-001',
    announcement_id    text REFERENCES tender_announcement(biz_id) ON DELETE CASCADE,
    project_id         text REFERENCES tender_project(biz_id) ON DELETE CASCADE,
    document_id        text REFERENCES tender_document(biz_id) ON DELETE SET NULL,
    category           text,
    text               text        NOT NULL,
    section            text,
    source_locator     text,
    mandatory          boolean     NOT NULL DEFAULT false,
    confidence         double precision,
    status             text,                                     -- 待关联 / 已关联 / 不适用
    matched_object_ids jsonb       NOT NULL DEFAULT '[]'::jsonb,
    matched_objects    jsonb       NOT NULL DEFAULT '[]'::jsonb,
    evidence_items     jsonb       NOT NULL DEFAULT '[]'::jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_procurement_need_ann ON procurement_need (announcement_id) WHERE announcement_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_procurement_need_status ON procurement_need (status);

-- =====================================================================
-- K 组：决策与运行
-- =====================================================================

-- K1 决策摘要（reasoning.decisionSummary：前端"决策摘要"面板的唯一来源）
CREATE TABLE IF NOT EXISTS decision_summary (
    id                        bigserial PRIMARY KEY,
    biz_id                    text        NOT NULL UNIQUE,       -- decisionId
    org_id                    text        NOT NULL DEFAULT 'org-001',
    project_id                text REFERENCES tender_project(biz_id) ON DELETE CASCADE,
    document_id               text REFERENCES tender_document(biz_id) ON DELETE CASCADE,
    assessment_id             text REFERENCES match_assessment(biz_id) ON DELETE SET NULL,
    headline                  text,                              -- 一句话结论
    recommendation            text,
    project_fields            jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- 15 键项目字段
    technical_match           jsonb       NOT NULL DEFAULT '{}'::jsonb,
    technical_gaps            jsonb       NOT NULL DEFAULT '[]'::jsonb,
    material_gaps             jsonb       NOT NULL DEFAULT '[]'::jsonb,
    qualification_risks       jsonb       NOT NULL DEFAULT '[]'::jsonb,
    commercial_risks          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    schedule_risks            jsonb       NOT NULL DEFAULT '[]'::jsonb,
    needs_human_review_count  integer     NOT NULL DEFAULT 0,
    risk_count                integer     NOT NULL DEFAULT 0,
    warnings                  jsonb       NOT NULL DEFAULT '[]'::jsonb,
    summary                   jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- 原始整体快照（兜底）
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_decision_summary_project ON decision_summary (project_id) WHERE project_id IS NOT NULL;

-- K2 自动化运行记录（automation-runs.json）
CREATE TABLE IF NOT EXISTS automation_run (
    id                   bigserial PRIMARY KEY,
    biz_id               text        NOT NULL UNIQUE,            -- runId
    org_id               text        NOT NULL DEFAULT 'org-001',
    status               text        NOT NULL,                   -- 完成 / 失败 / 运行中
    trigger_source       text,                                   -- manual / scheduled
    started_at           timestamptz NOT NULL DEFAULT now(),
    finished_at          timestamptz,
    elapsed_ms           bigint,
    sites_crawled        integer,
    announcements_found  integer,
    announcements_new    integer,
    reminders_created    integer,
    llm_call_count       integer,
    result               jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- monitoring 原始结果
    warnings             text[]      NOT NULL DEFAULT '{}',
    errors               text[]      NOT NULL DEFAULT '{}',
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_automation_run_started ON automation_run (started_at DESC);

-- =====================================================================
-- L 组：治理元数据（本体治理的产物，让 agent / 团队 / DBA 都能读到语义）
--    数据字典本体化：类(表) - 属性(字段) - 关系，与 M3 的 JSONRule 同构，
--    但落成 PG 可查询的表，而不是推给问数系统的配置文件。
-- =====================================================================

-- L1 表级元数据
CREATE TABLE IF NOT EXISTS meta_table (
    id               bigserial PRIMARY KEY,
    table_name       text        NOT NULL UNIQUE,
    table_cn_name    text        NOT NULL,
    domain           text,                                       -- A企业/B招标/C解析/D提取/E匹配/F运行/G标书/H视图/I初筛/J抽取/K决策/L元数据
    layer            text,                                       -- fact/tender/parse/extract/match/run/bid/screen/meta
    description      text,
    source_old_path  text,                                       -- 对应的旧 JSON 路径（迁移溯源）
    is_authoritative boolean     NOT NULL DEFAULT true,           -- 企业事实表=false（M3 才是权威）
    owner            text,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- L2 字段级元数据（agent 可直接 SQL 查"某表有哪些字段、什么含义"）
CREATE TABLE IF NOT EXISTS meta_column (
    id               bigserial PRIMARY KEY,
    table_name       text        NOT NULL REFERENCES meta_table(table_name) ON DELETE CASCADE,
    column_name      text        NOT NULL,
    column_cn_name   text        NOT NULL,
    data_type        text,
    description      text,
    value_domain     text,                                       -- 值域说明（如 6 态契约）
    semantic_type    text,                                       -- id/amount/date/status/text/json/enum/bool
    is_business_key  boolean     NOT NULL DEFAULT false,          -- 是否 biz_id
    is_pii           boolean     NOT NULL DEFAULT false,
    deprecated       boolean     NOT NULL DEFAULT false,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (table_name, column_name)
);
CREATE INDEX IF NOT EXISTS ix_meta_column_table ON meta_column (table_name, column_name);

-- L3 关系级元数据（外键语义 + 软引用）
CREATE TABLE IF NOT EXISTS meta_relationship (
    id               bigserial PRIMARY KEY,
    constraint_name  text,
    from_table       text        NOT NULL,
    from_column      text        NOT NULL,
    to_table         text        NOT NULL,
    to_column        text        NOT NULL,
    relation_cn_name text        NOT NULL,                        -- 如"要求分组包含招标要求"
    relation_type    text,                                       -- fk / soft（软引用）/ logical
    description      text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_meta_relationship_from ON meta_relationship (from_table, from_column);
CREATE INDEX IF NOT EXISTS ix_meta_relationship_to ON meta_relationship (to_table, to_column);

COMMIT;

-- =====================================================================
-- 备注
-- 1) 本文件补 14 张表：001 的 37 张 + 本文件 14 张 = 51 张。
-- 2) screening_assessment 刻意合并了两个阶段的评估（stage 区分），
--    避免 ScreeningAssessment 与 eligibility 造出两张近似重复表。
-- 3) reminder 刻意合并了 reminders.json 与蓝图 NotificationEvent。
-- 4) meta_* 三张表是"治理成果落成 PG"的形态；具体内容（数据字典行）
--    与 COMMENT ON 语句由 003 迁移或组长的「PostgreSQL 导入本体 skill」产出。
-- 5) app_user / meta_metric / app_config 暂不建——等真有消费者再补，
--    不预先造没有消费者的表。
-- =====================================================================
