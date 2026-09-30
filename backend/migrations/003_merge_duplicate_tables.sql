-- =====================================================================
-- 招投标智能体 · 迁移 003：提取结果去重 + 补齐字段
-- 目标库：PostgreSQL 14+（开发环境 postgres:16）
--
-- 背景（与用户确认的「方案 A」）：
--   原 001/002 里的 evaluation_criterion / project_info_item / document_template
--   三张表，实测其实是 targetModules 某个模块 items 的「第二份拷贝」——
--   8 个模块的 items 共享同一套基础字段，只有少量模块专有字段不同。
--   故统一并入 tender_requirement 一张表，用 target_module 列区分模块。
--   这三张表的 CREATE 已直接从 001/002 删除（纯历史清理，pre-data 零风险），
--   所以本文件不再需要 DROP。
--
-- 本文件两件事：
--   (1) tender_requirement 补各模块专有列 + source(jsonb，真实是对象不是文本)
--   (2) requirement_group 补模块级元信息（ignored 等）+ ranges(jsonb，提取范围)
--
-- 幂等：全部 ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS。
-- =====================================================================

BEGIN;

-- =====================================================================
-- (1) tender_requirement 补列：各模块专有字段 + source
--     基础字段（title/text/raw_text/normalized_text/mandatory/confidence/
--     handling_mode/source_locator/section_id/section_path/table_ids/
--     clause_index/clause_count/parameters/...）已在 001 覆盖。
-- =====================================================================
ALTER TABLE tender_requirement
    -- 通用：真实 item 的 source 是对象 {segmentId,segmentIds,sourceLocator,...}
    ADD COLUMN IF NOT EXISTS source              jsonb,
    -- rejection 模块专有
    ADD COLUMN IF NOT EXISTS risk_type           text,
    ADD COLUMN IF NOT EXISTS trigger_terms       text[] NOT NULL DEFAULT '{}',
    -- qualification 模块专有
    ADD COLUMN IF NOT EXISTS is_commitment       boolean NOT NULL DEFAULT false,
    -- evaluation 模块专有
    ADD COLUMN IF NOT EXISTS criterion_name      text,
    ADD COLUMN IF NOT EXISTS scoring_rule        text,
    ADD COLUMN IF NOT EXISTS max_score           double precision,
    ADD COLUMN IF NOT EXISTS required_evidence   text,
    -- project_info 模块专有
    ADD COLUMN IF NOT EXISTS field_key           text,
    ADD COLUMN IF NOT EXISTS label               text,
    ADD COLUMN IF NOT EXISTS value               text,
    -- template 模块专有
    ADD COLUMN IF NOT EXISTS template_id         text,
    ADD COLUMN IF NOT EXISTS template_type       text,
    ADD COLUMN IF NOT EXISTS child_section_count integer,
    ADD COLUMN IF NOT EXISTS download_mode       text,
    ADD COLUMN IF NOT EXISTS extract_mode        text,
    ADD COLUMN IF NOT EXISTS preview_mode        text,
    ADD COLUMN IF NOT EXISTS source_item_ids     text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS source_section_id   text,
    ADD COLUMN IF NOT EXISTS source_section_ids  text[] NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS start_locator       text,
    ADD COLUMN IF NOT EXISTS end_locator         text,
    ADD COLUMN IF NOT EXISTS writing_ready       boolean NOT NULL DEFAULT false;

-- 常用检索：按项目信息字段名 / 模板编号定位
CREATE INDEX IF NOT EXISTS ix_req_field_key  ON tender_requirement (document_id, field_key)  WHERE field_key  IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_req_template   ON tender_requirement (document_id, template_id) WHERE template_id IS NOT NULL;

-- =====================================================================
-- (2) requirement_group 补模块级元信息 + 提取范围 ranges
--     对应 targetModules[]（27 键）中除 items/sourceView 外的模块级字段，
--     以及 targetRanges[].ranges[]（rangeId/segmentIds/sourceLocator 等）。
-- =====================================================================
ALTER TABLE requirement_group
    ADD COLUMN IF NOT EXISTS ignored                    boolean     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS matchable                  boolean     NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS display_mode               text,
    ADD COLUMN IF NOT EXISTS processing_mode            text,
    ADD COLUMN IF NOT EXISTS selection_source           text,
    ADD COLUMN IF NOT EXISTS reviewed_by_llm            boolean     NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS confidence                 double precision,
    ADD COLUMN IF NOT EXISTS reason                     text,
    ADD COLUMN IF NOT EXISTS source_item_count          integer,
    ADD COLUMN IF NOT EXISTS selected_section_count     integer,
    ADD COLUMN IF NOT EXISTS selected_segment_count     integer,
    ADD COLUMN IF NOT EXISTS selected_table_count       integer,
    ADD COLUMN IF NOT EXISTS section_ids                text[]      NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS segment_ids                text[]      NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS table_ids                  text[]      NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS expanded_section_ids       text[]      NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS expanded_segment_ids       text[]      NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS llm_excluded_segment_ids   text[]      NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS review_section_ids         text[]      NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS review_segment_ids         text[]      NOT NULL DEFAULT '{}',
    -- 提取范围：targetRanges[].ranges[] 原样落 jsonb
    --   [{rangeId, sectionId, segmentIds, startSegmentId, endSegmentId, sourceLocator}]
    ADD COLUMN IF NOT EXISTS ranges                     jsonb       NOT NULL DEFAULT '[]'::jsonb;

COMMIT;

-- =====================================================================
-- 备注
-- 1) 三张重复表已移除，8 个模块 items 统一走 tender_requirement：
--      target_module IN ('technical','qualification','commercial','evaluation',
--                        'contract','template','project_info','rejection')
-- 2) 三份 requirements 清单是派生子集，无需建表：
--      matchStageRequirements  = technical + qualification + rejection
--      enterpriseMatchRequirements = technical + qualification
--      riskCheckRequirements   = rejection
-- 3) moduleCounts / moduleDisplayCounts 由 tender_requirement 按 target_module
--    聚合派生（display 需同时满足 requirement_group.ignored=false）。
-- =====================================================================
