-- =====================================================================
-- 014_payload_all_tables.sql
--
-- 目的：给**其余表**统一加 `payload jsonb`（全保真载体）。
--
-- ⚠️ 这是一个**纯新增**迁移：
--    只 ADD COLUMN，**不删列、不改列、不删表、不改名**。
--    风险为零（新增可空列不影响任何现有读写）。
--
-- 为什么这部分只加 payload、不做列收缩：
--   按既定红线，「列裁决」需要**实测依据**（真实数据里哪些列填充率为 0）。
--   而下面这些表**大多是 0 行**（未落地），没有实测依据，
--   不能因为"列看起来多"就砍 —— 那会把将来真会用的查询能力砍掉。
--   → 空表统一**只加 payload**，列裁决留到有真实数据之后再做。
--
--   例外（有真实数据、本轮已单独处理）：
--     bid_section  49 -> 17 列（011/012，已五项验证）
--     bid_document 45 -> 14 列（013，已五项验证）
--
--   本迁移覆盖的表里，**有真实数据的只有 analysis_run（17 行）和
--   workspace / workspace_draft（各 18 行）**。实测：
--     - analysis_run：31 列里 29 列真实填充 100% → 这些列是**真在用**的，
--       按准入规则**应当保留**，不做收缩（只加 payload 兜契约外字段）
--     - workspace（8 列）/ workspace_draft（6 列）：本来就极小，无列可收缩
--
--   合计新增 42 张表的 payload（44 张表 − 已处理的 bid_section / bid_document）。
--
-- 幂等性：ADD COLUMN IF NOT EXISTS，可重复执行。
--
-- 回滚：ALTER TABLE <t> DROP COLUMN IF EXISTS payload;（逐表）
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- F 组 · 运行与可观测
-- ---------------------------------------------------------------------------
ALTER TABLE analysis_run        ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE analysis_run_stage  ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE llm_call_log        ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- C 组 · 解析结果（招标文件解析）
-- ---------------------------------------------------------------------------
ALTER TABLE parse_bundle        ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE template_fragment   ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE evaluation_item     ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE parse_risk          ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE parse_review        ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE document_section    ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE document_segment    ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE document_table      ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- D 组 · 提取结果（要求项 / 要求组）
-- ---------------------------------------------------------------------------
ALTER TABLE tender_requirement  ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE requirement_group   ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE requirement_segment ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE agent_result        ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- E 组 · 匹配结果（6 态匹配契约，行级查询能力靠这些表）
-- ---------------------------------------------------------------------------
ALTER TABLE match_assessment    ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE match_item          ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE match_item_evidence ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE match_item_fact     ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE match_risk          ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- G 组 · 标书产出（bid_section / bid_document 已在 011/013 处理）
-- ---------------------------------------------------------------------------
ALTER TABLE bid_plan            ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE workspace           ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE workspace_draft     ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE review_record       ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- B 组 · 招标信息与文件
-- ---------------------------------------------------------------------------
ALTER TABLE tender_project      ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE tender_document     ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE tender_announcement ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE source_site         ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE document_acquisition ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE announcement_match_term ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE automation_run      ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- I 组 · 初筛与提醒链
-- ---------------------------------------------------------------------------
ALTER TABLE monitoring_rule     ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE screening_profile   ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE screening_constraint ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE screening_assessment ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE screening_check     ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE reminder            ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- J 组 · 抽取补齐
-- ---------------------------------------------------------------------------
ALTER TABLE procurement_need    ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- K 组 · 决策与运行
-- ---------------------------------------------------------------------------
ALTER TABLE decision_summary    ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- L 组 · 治理元数据（已有真实数据：meta_table 44 / meta_column 893 /
--      meta_relationship 52）。这三张表是"投影自描述"的载体，
--      payload 用来兜住将来新增的治理字段。
-- ---------------------------------------------------------------------------
ALTER TABLE meta_table          ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE meta_column         ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE meta_relationship   ADD COLUMN IF NOT EXISTS payload jsonb NOT NULL DEFAULT '{}'::jsonb;

-- ---------------------------------------------------------------------------
-- 为已有真实数据的三张表回填 payload（用现有列原样搬，不注入默认值）
-- ---------------------------------------------------------------------------

-- analysis_run：把 31 列里**有值**的运行信息搬进 payload
UPDATE analysis_run
SET payload = jsonb_strip_nulls(jsonb_build_object(
    'stage',                    stage,
    'status',                   status,
    'projectId',                project_id,
    'documentVersionId',        document_version_id,
    'runSchemaVersion',         run_schema_version,
    'parserName',               parser_name,
    'parserVersion',            parser_version,
    'schemaVersion',            schema_version,
    'promptVersion',            prompt_version,
    'llmModel',                 llm_model,
    'llmParameters',            llm_parameters,
    'llmCallCount',             llm_call_count,
    'llmSuccessCount',          llm_success_count,
    'llmFailureCount',          llm_failure_count,
    'llmInputTokens',           llm_input_tokens,
    'llmOutputTokens',          llm_output_tokens,
    'manualChangeCount',        manual_change_count,
    'externalAgentCallCount',   external_agent_call_count,
    'inputArtifactPaths',       to_jsonb(input_artifact_paths),
    'outputArtifactPaths',      to_jsonb(output_artifact_paths),
    'metrics',                  metrics,
    'warnings',                 to_jsonb(warnings),
    'errors',                   to_jsonb(errors),
    'elapsedMs',                elapsed_ms
))
WHERE payload IS NULL OR payload = '{}'::jsonb;

-- meta_column：把已有的治理列信息也存一份进 payload（治理自描述）
--   实测 meta_column 的真实列：table_name / column_name / column_cn_name /
--   data_type / description / value_domain / semantic_type /
--   is_business_key / is_pii / deprecated / is_enabled
UPDATE meta_column
SET payload = jsonb_strip_nulls(jsonb_build_object(
    'tableName',     table_name,
    'columnName',    column_name,
    'columnCnName',  column_cn_name,
    'dataType',      data_type,
    'description',   description,
    'valueDomain',   value_domain,
    'semanticType',  semantic_type,
    'isBusinessKey', is_business_key,
    'isPii',         is_pii,
    'deprecated',    deprecated,
    'isEnabled',     is_enabled
))
WHERE payload IS NULL OR payload = '{}'::jsonb;

COMMIT;
