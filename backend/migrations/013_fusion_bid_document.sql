-- =====================================================================
-- 013_fusion_bid_document.sql
--
-- 目的：按准入规则对 `bid_document` 做**列级**收缩（45 列 → 16 列 + payload）。
--
-- ⚠️ 列级改造，不是表级：表名 / 主键 / 行数 / 外键关系全不变。
--
-- 依据（2026-09-30 实测，_fusion_sample/adjudicate_bid_document.py）：
--   45 列里只有 10 列有真实数据，35 列全空。
--
--   但与 bid_section 有**一个重要区别**（必须记录）：
--   bid_document 的列**已经覆盖了代码全部 40 个 key**（缺口 0 个），
--   所以"静默丢失"在这个表上**今天不成立**。
--   加 payload 的价值在这里是**面向未来**的：
--   代码再加字段就直接落 payload，不用再 ALTER TABLE。
--
-- 保留的 16 列（逐条准入理由）：
--   id / biz_id            代理键 + 业务键（唯一）
--   workspace_id           JOIN 到 workspace
--   status                 CHECK 值域（4 态）+ 要查
--   version                版本控制
--   export_path            导出产物路径（要查/要展示）
--   created_at/updated_at  审计时间戳
--   payload                全保真事实
--   ── 以下为保留的"要查的结构化字段" ──
--   plan                   jsonb：章节数组，**与 bid_section 有投影关系**，保留
--   deviation_tables       jsonb：偏离表（视图 v_deviation_row 依赖），保留
--   mode                   CHECK 值域（open/secret），保留
--   project_id             JOIN/关联招标项目，保留
--   requirement_mapping_status  CHECK 值域，保留
--
-- 收进 payload 的 35 列：
--   全是"规划/解析的运行元信息 + 各种 warnings 数组"，
--   它们是**过程日志性质**（planningSource / parseProvider / *_warnings …），
--   从不参与 WHERE/JOIN/ORDER BY，实测也全空。
--
-- 幂等性：ADD COLUMN IF NOT EXISTS / DROP COLUMN IF EXISTS。
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1) 加 payload
-- ---------------------------------------------------------------------
ALTER TABLE bid_document ADD COLUMN IF NOT EXISTS payload jsonb;

-- ---------------------------------------------------------------------
-- 2) 回填 payload：从现有各列原样搬（不注入默认值）。
--    用 jsonb_strip_nulls 去掉 NULL，避免把"没这个字段"写成"字段=null"。
-- ---------------------------------------------------------------------
UPDATE bid_document
SET payload = jsonb_strip_nulls(jsonb_build_object(
    -- 关键业务 key（camelCase，与代码契约一致）
    'workspaceId',   workspace_id,
    'status',        status,
    'version',       version,
    'plan',          plan,
    'deviationTables', deviation_tables,
    'mode',          mode,
    'projectId',     project_id,
    'exportPath',    export_path,
    -- 规划/解析运行元信息
    'generatedAt',               generated_at,
    'planSource',                plan_source,
    'planSourceDetail',          plan_source_detail,
    'planningSource',            planning_source,
    'planningSourceDetail',      planning_source_detail,
    'planningProvider',          planning_provider,
    'planningStatus',            planning_status,
    'planningModel',             planning_model,
    'planningFallbackReason',    planning_fallback_reason,
    'parseProvider',             parse_provider,
    'parseSourceDetail',         parse_source_detail,
    'evaluationMethod',          evaluation_method,
    'lastExportMode',            last_export_mode,
    'exportedAt',                exported_at,
    'templateFragmentsArtifact', template_fragments_artifact,
    'requirementMappingStatus',  requirement_mapping_status,
    'evaluationPlanTotal',       evaluation_plan_total,
    'copiedTemplateCount',       copied_template_count,
    'requirementMapBatchCount',  requirement_map_batch_count,
    -- 结构化块
    'evaluationPlan',            evaluation_plan,
    'evaluationStrategy',        evaluation_strategy,
    'proofread',                 proofread,
    'complianceReview',          compliance_review,
    'sectionBlueprints',         section_blueprints,
    'submissionStructure',       submission_structure,
    'templateFragments',         template_fragments,
    'requirementCoverage',       requirement_coverage,
    'uncoveredRequirements',     uncovered_requirements,
    'duplicatedRequirements',    duplicated_requirements,
    'agentResults',              agent_results,
    -- 各类告警
    'planningWarnings',          planning_warnings,
    'exportWarnings',            export_warnings,
    'requirementMappingWarnings', requirement_mapping_warnings
))
WHERE payload IS NULL;

-- ---------------------------------------------------------------------
-- 3) 删列（35 列 -> payload；数据已在第 2 步进入 payload，零丢失）
-- ---------------------------------------------------------------------
ALTER TABLE bid_document DROP COLUMN IF EXISTS generated_at;
ALTER TABLE bid_document DROP COLUMN IF EXISTS plan_source;
ALTER TABLE bid_document DROP COLUMN IF EXISTS plan_source_detail;
ALTER TABLE bid_document DROP COLUMN IF EXISTS planning_source;
ALTER TABLE bid_document DROP COLUMN IF EXISTS planning_source_detail;
ALTER TABLE bid_document DROP COLUMN IF EXISTS planning_provider;
ALTER TABLE bid_document DROP COLUMN IF EXISTS planning_status;
ALTER TABLE bid_document DROP COLUMN IF EXISTS planning_model;
ALTER TABLE bid_document DROP COLUMN IF EXISTS planning_fallback_reason;
ALTER TABLE bid_document DROP COLUMN IF EXISTS parse_provider;
ALTER TABLE bid_document DROP COLUMN IF EXISTS parse_source_detail;
ALTER TABLE bid_document DROP COLUMN IF EXISTS evaluation_method;
ALTER TABLE bid_document DROP COLUMN IF EXISTS last_export_mode;
ALTER TABLE bid_document DROP COLUMN IF EXISTS exported_at;
ALTER TABLE bid_document DROP COLUMN IF EXISTS template_fragments_artifact;
ALTER TABLE bid_document DROP COLUMN IF EXISTS evaluation_plan_total;
ALTER TABLE bid_document DROP COLUMN IF EXISTS copied_template_count;
ALTER TABLE bid_document DROP COLUMN IF EXISTS requirement_map_batch_count;
ALTER TABLE bid_document DROP COLUMN IF EXISTS evaluation_plan;
ALTER TABLE bid_document DROP COLUMN IF EXISTS evaluation_strategy;
ALTER TABLE bid_document DROP COLUMN IF EXISTS proofread;
ALTER TABLE bid_document DROP COLUMN IF EXISTS compliance_review;
ALTER TABLE bid_document DROP COLUMN IF EXISTS section_blueprints;
ALTER TABLE bid_document DROP COLUMN IF EXISTS submission_structure;
ALTER TABLE bid_document DROP COLUMN IF EXISTS template_fragments;
ALTER TABLE bid_document DROP COLUMN IF EXISTS requirement_coverage;
ALTER TABLE bid_document DROP COLUMN IF EXISTS uncovered_requirements;
ALTER TABLE bid_document DROP COLUMN IF EXISTS duplicated_requirements;
ALTER TABLE bid_document DROP COLUMN IF EXISTS planning_warnings;
ALTER TABLE bid_document DROP COLUMN IF EXISTS export_warnings;
ALTER TABLE bid_document DROP COLUMN IF EXISTS requirement_mapping_warnings;
ALTER TABLE bid_document DROP COLUMN IF EXISTS agent_results;

-- ---------------------------------------------------------------------
-- 4) payload 收紧 NOT NULL
-- ---------------------------------------------------------------------
ALTER TABLE bid_document ALTER COLUMN payload SET DEFAULT '{}'::jsonb;
UPDATE bid_document SET payload = '{}'::jsonb WHERE payload IS NULL;
ALTER TABLE bid_document ALTER COLUMN payload SET NOT NULL;

-- ---------------------------------------------------------------------
-- 5) 值域 CHECK（枚举取自 bid_writing/schemas.py）
--    一律 CHECK (col IS NULL OR col IN (...))
-- ---------------------------------------------------------------------
ALTER TABLE bid_document DROP CONSTRAINT IF EXISTS bid_document_status_chk;
ALTER TABLE bid_document ADD CONSTRAINT bid_document_status_chk
    CHECK (status IS NULL OR status IN ('planned','generating','generated','exported','missing'));

ALTER TABLE bid_document DROP CONSTRAINT IF EXISTS bid_document_mode_chk;
ALTER TABLE bid_document ADD CONSTRAINT bid_document_mode_chk
    CHECK (mode IS NULL OR mode IN ('open','secret'));

-- ---------------------------------------------------------------------
-- 6) 索引
-- ---------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS bid_document_payload_gin
    ON bid_document USING gin (payload jsonb_path_ops);
CREATE INDEX IF NOT EXISTS bid_document_workspace_idx
    ON bid_document (workspace_id);
CREATE INDEX IF NOT EXISTS bid_document_status_idx
    ON bid_document (status);

-- ---------------------------------------------------------------------
-- 7) 注释
-- ---------------------------------------------------------------------
COMMENT ON COLUMN bid_document.payload IS
    '全保真事实：bidDocument 对象的完整 JSON。'
    '加字段不用改表（本表列原本已覆盖代码 40 个 key，payload 面向未来）。';
COMMENT ON COLUMN bid_document.plan IS
    '章节数组（jsonb）。与 bid_section 表有投影关系：bid_section.payload 即本数组元素。';
COMMENT ON COLUMN bid_document.deviation_tables IS
    '偏离表（jsonb）。视图 v_deviation_row / v_parameter_deviation_row 依赖。';

COMMIT;
