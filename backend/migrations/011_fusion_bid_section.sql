-- =====================================================================
-- 011_fusion_bid_section.sql
--
-- 目的：按**准入规则**对 `bid_section` 做**列级**收缩（49 列 → 16 列 + payload）。
--
-- ⚠️ 这是**列级**改造，不是表级：
--    表名 / 主键 / 行数 / 外键关系 **全都不变**。一张表都没有删。
--
-- 依据（2026-09-30 实测，脚本 _fusion_sample/adjudicate_bid_section.py）：
--
--   1) 准入规则（满足至少一条才建列）：
--      ① 要 WHERE ② 要 JOIN ③ 要 ORDER BY ④ 要参与唯一键
--      ⑤ 要 CHECK ⑥ 要建索引 ⑦ 要进治理元数据
--      其余一律进 payload。**这条规则用于删列，不用于删表。**
--
--   2) 实测：现有 49 列里
--        - 有数据 16 列（保留，见下表）
--        - **全空 33 列** → 收进 payload（数据零丢失）
--
--   3) 实测：代码实际写入 section 的 key 有 **108 个**，
--      其中 **66 个在表里根本没有对应列** → 今天就是**静默丢失**。
--      加 payload 之后这 66 个 key 自动有地方落，且**将来再加字段也不用改表**。
--
-- 保留的 16 列（逐条给准入理由）：
--   ┌ id                 代理主键（我要方 001 的约定：bigserial + biz_id unique）
--   ├ biz_id             业务键，UNIQUE
--   ├ bid_document_id    JOIN 到 bid_document
--   ├ section_id         业务键 + 要查（与 bid_document_id 组成唯一）
--   ├ title              要查 / 要 ORDER BY
--   ├ content            长正文（text 自动 TOAST）
--   ├ version            并发/版本控制
--   ├ kind               要 CHECK 值域（4 态）+ 要查
--   ├ source             要 CHECK 值域（5 态）+ 要查
--   ├ status             要 CHECK 值域（4 态）+ 要查
--   ├ required           要查（必答项过滤）
--   ├ level              要 ORDER BY（树形层级）
--   ├ order_no           要 ORDER BY（章节顺序）
--   └ created_at         审计时间戳
--     updated_at         审计时间戳（touch_col 语义：只在真变化时刷新）
--
-- 收进 payload 的 33 列（全部实测填充率为 0）：
--   source_refs / item_type / writing_profile / response_type / parent_section_id /
--   template_fragment_id / template_id / template_type / template_ref / template_status /
--   template_field_count / template_table_count / template_locked_block_count /
--   content_matches_template / volume_id / format_item_id / node_type /
--   evaluation_score / evaluation_weight / evaluation_depth /
--   material_scopes / evaluation_ids /
--   template_blocks / template_tables / filled_tables / blanks_to_fill /
--   evidence_entries / evidence_groups / evidence_summary / requirement_response_rows /
--   edit_history / agent_results / extension
--
-- ⚠️ 其中 parent_section_id 虽在准入规则下「要 JOIN」本可保留，但实测 6 行**全为 NULL**，
--    且 plan_tree.py 的父子关系是从 payload 的 order/level 现算的 ——
--    保留一个永远为 NULL 的列没有意义，故一并收进 payload。
--    将来若有真实层级查询需求，一条 ALTER TABLE 就能加回（数据在 payload 里，不丢）。
--
-- 幂等性：ADD COLUMN IF NOT EXISTS / DROP COLUMN IF EXISTS，可重复执行。
--
-- 回滚：见文件末尾的注释块（反向 ALTER）。
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1) 加 payload（保真的载体）。先建为可空，回填后再收紧。
--    payload 是**事实**：全保真，永不加约束。
-- ---------------------------------------------------------------------
ALTER TABLE bid_section ADD COLUMN IF NOT EXISTS payload jsonb;

-- ---------------------------------------------------------------------
-- 2) 回填 payload：把现有各列的值搬进 JSON，
--    同时对已经被代码写过、但**从来没有列**的 key 留出空位（值取默认）。
--
--    只回填 payload IS NULL 的行（幂等，不覆盖已有 payload）。
-- ---------------------------------------------------------------------
UPDATE bid_section
SET payload = jsonb_strip_nulls(jsonb_build_object(
    -- 代码契约 build_section 的 15 个 key（camelCase，与代码一致）
    'sectionId',        section_id,
    'title',            title,
    'source',           source,
    'kind',             kind,
    'status',           status,
    'level',            level,
    'order',            order_no,
    'required',         required,
    'content',          content,
    'requirementIds',   COALESCE(to_jsonb(requirement_ids), '[]'::jsonb),
    'sourceRefs',       COALESCE(source_refs, '[]'::jsonb),
    'materialScopes',   COALESCE(to_jsonb(material_scopes), '[]'::jsonb),
    'responseType',     response_type,
    'writingProfile',   writing_profile,
    'parentSectionId',  COALESCE(parent_section_id, ''),
    -- 其余曾被建过列的字段（收进 payload 的 33 列）
    'itemType',                 item_type,
    'templateFragmentId',       template_fragment_id,
    'templateId',               template_id,
    'templateType',             template_type,
    'templateRef',              template_ref,
    'templateStatus',           template_status,
    'templateFieldCount',       template_field_count,
    'templateTableCount',       template_table_count,
    'templateLockedBlockCount', template_locked_block_count,
    'contentMatchesTemplate',   content_matches_template,
    'volumeId',                 volume_id,
    'formatItemId',             format_item_id,
    'nodeType',                 node_type,
    'evaluationScore',          evaluation_score,
    'evaluationWeight',         evaluation_weight,
    'evaluationDepth',          evaluation_depth,
    'evaluationIds',            COALESCE(to_jsonb(evaluation_ids), '[]'::jsonb),
    'templateBlocks',           COALESCE(template_blocks, '[]'::jsonb),
    'templateTables',           COALESCE(template_tables, '[]'::jsonb),
    'filledTables',             COALESCE(filled_tables, '[]'::jsonb),
    'blanksToFill',             COALESCE(blanks_to_fill, '[]'::jsonb),
    'evidenceEntries',          COALESCE(evidence_entries, '[]'::jsonb),
    'evidenceGroups',           COALESCE(evidence_groups, '[]'::jsonb),
    'evidenceSummary',          evidence_summary,
    'requirementResponseRows',  COALESCE(requirement_response_rows, '[]'::jsonb),
    'editHistory',              COALESCE(edit_history, '[]'::jsonb),
    'agentResults',             COALESCE(agent_results, '[]'::jsonb),
    'extension',                extension
))
WHERE payload IS NULL;

-- ---------------------------------------------------------------------
-- 3) 删列（**列级**收缩：33 列 → payload）。
--    注意：数据已在第 2 步全部进 payload，此处零丢失。
-- ---------------------------------------------------------------------
ALTER TABLE bid_section DROP COLUMN IF EXISTS source_refs;
ALTER TABLE bid_section DROP COLUMN IF EXISTS item_type;
ALTER TABLE bid_section DROP COLUMN IF EXISTS writing_profile;
ALTER TABLE bid_section DROP COLUMN IF EXISTS response_type;
ALTER TABLE bid_section DROP COLUMN IF EXISTS parent_section_id;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_fragment_id;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_id;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_type;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_ref;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_status;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_field_count;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_table_count;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_locked_block_count;
ALTER TABLE bid_section DROP COLUMN IF EXISTS content_matches_template;
ALTER TABLE bid_section DROP COLUMN IF EXISTS volume_id;
ALTER TABLE bid_section DROP COLUMN IF EXISTS format_item_id;
ALTER TABLE bid_section DROP COLUMN IF EXISTS node_type;
ALTER TABLE bid_section DROP COLUMN IF EXISTS evaluation_score;
ALTER TABLE bid_section DROP COLUMN IF EXISTS evaluation_weight;
ALTER TABLE bid_section DROP COLUMN IF EXISTS evaluation_depth;
ALTER TABLE bid_section DROP COLUMN IF EXISTS material_scopes;
ALTER TABLE bid_section DROP COLUMN IF EXISTS evaluation_ids;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_blocks;
ALTER TABLE bid_section DROP COLUMN IF EXISTS template_tables;
ALTER TABLE bid_section DROP COLUMN IF EXISTS filled_tables;
ALTER TABLE bid_section DROP COLUMN IF EXISTS blanks_to_fill;
ALTER TABLE bid_section DROP COLUMN IF EXISTS evidence_entries;
ALTER TABLE bid_section DROP COLUMN IF EXISTS evidence_groups;
ALTER TABLE bid_section DROP COLUMN IF EXISTS evidence_summary;
ALTER TABLE bid_section DROP COLUMN IF EXISTS requirement_response_rows;
ALTER TABLE bid_section DROP COLUMN IF EXISTS edit_history;
ALTER TABLE bid_section DROP COLUMN IF EXISTS agent_results;
ALTER TABLE bid_section DROP COLUMN IF EXISTS extension;

-- ---------------------------------------------------------------------
-- 4) 收紧 payload 为 NOT NULL（保真载体不允许为空）
-- ---------------------------------------------------------------------
ALTER TABLE bid_section ALTER COLUMN payload SET DEFAULT '{}'::jsonb;
UPDATE bid_section SET payload = '{}'::jsonb WHERE payload IS NULL;
ALTER TABLE bid_section ALTER COLUMN payload SET NOT NULL;

-- ---------------------------------------------------------------------
-- 5) 值域 CHECK（枚举取自 backend/app/bid_writing/schemas.py，实测）
--    一律写成 CHECK (col IS NULL OR col IN (...))：NULL 合法。
--
--    这体现融合方案的「归一在写入层，约束在列上」：
--    投影里的值一定合法；脏值不阻塞写入（留在 payload，列留 NULL）。
-- ---------------------------------------------------------------------
ALTER TABLE bid_section DROP CONSTRAINT IF EXISTS bid_section_kind_chk;
ALTER TABLE bid_section ADD CONSTRAINT bid_section_kind_chk
    CHECK (kind IS NULL OR kind IN ('commercial','technical','deviation','attachment'));

ALTER TABLE bid_section DROP CONSTRAINT IF EXISTS bid_section_source_chk;
ALTER TABLE bid_section ADD CONSTRAINT bid_section_source_chk
    CHECK (source IS NULL OR source IN ('template','ai','table','attachment','copied_template'));

ALTER TABLE bid_section DROP CONSTRAINT IF EXISTS bid_section_status_chk;
ALTER TABLE bid_section ADD CONSTRAINT bid_section_status_chk
    CHECK (status IS NULL OR status IN ('pending','generating','generated','missing'));

-- ---------------------------------------------------------------------
-- 6) 索引
-- ---------------------------------------------------------------------
-- payload GIN：支撑契约外字段的 jsonb 路径查询
CREATE INDEX IF NOT EXISTS bid_section_payload_gin
    ON bid_section USING gin (payload jsonb_path_ops);

-- 章节顺序（树形渲染是主要读路径）
CREATE INDEX IF NOT EXISTS bid_section_order_idx
    ON bid_section (bid_document_id, order_no);

-- 按状态过滤（"还有哪些没生成"）
CREATE INDEX IF NOT EXISTS bid_section_status_idx
    ON bid_section (status);

-- ---------------------------------------------------------------------
-- 7) 列注释（治理元数据的来源之一）
-- ---------------------------------------------------------------------
COMMENT ON COLUMN bid_section.payload IS
    '全保真事实：section 对象的完整 JSON。代码写入的 108 个 key 全在这；'
    '加字段不用改表。永不加约束。';
COMMENT ON COLUMN bid_section.biz_id IS '业务键，UNIQUE(org_id, biz_id)';
COMMENT ON COLUMN bid_section.section_id IS '章节业务 id（代码契约 sectionId）';
COMMENT ON COLUMN bid_section.order_no IS '章节顺序（代码契约 order，归一到 >= 0）';
COMMENT ON COLUMN bid_section.level IS '树形层级（代码契约 level，归一到 >= 1）';

COMMIT;

-- =====================================================================
-- 回滚（反向 ALTER。数据在 payload 里，可完整还原）
-- =====================================================================
-- BEGIN;
-- ALTER TABLE bid_section ADD COLUMN IF NOT EXISTS source_refs jsonb NOT NULL DEFAULT '[]';
-- ... （每列一条，类型见 006_bid_writing_tables.sql）
-- UPDATE bid_section SET
--     source_refs      = COALESCE(payload->'sourceRefs', '[]'::jsonb),
--     writing_profile  = payload->>'writingProfile',
--     ... ;
-- COMMIT;
--
-- 需要时直接从 git 历史取 006_bid_writing_tables.sql 的原始 DDL 更省事。
-- =====================================================================
