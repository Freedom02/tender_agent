-- =====================================================================
-- 015_meta_column_projection.sql
--
-- 目的：把 **bid_section / bid_document 的投影裁决结果**登记进 L 组治理元数据
--       (`meta_column`)，让「列 ← payload 路径 + 归一函数 + 约束」有唯一权威清单。
--
-- 背景（融合方案 §七「杀手锏」）：
--   payload 保真解决了"存得下"，治理元数据解决"看得住"。
--   meta_column 记录每个提升列的来源路径后：
--     1) 投影是**自描述**的 —— payload → 列的映射有权威清单，不靠人记
--     2) 可**自动校验完整性** —— 拿代码实际写入的 key 集合 vs meta_column 路径集合
--        做差集，自动发现"代码加了字段、投影没跟上"
--     3) 根治本项目踩过的坑：G 组「代码写 37 个 key、表只有 3 列」这种缺口，
--        在这种机制下会被自动检出，不再等合并后靠人工核对
--
-- 本迁移做什么：
--   1. 重跑 008 的自动灌列逻辑（吸收 011/013/014 造成的新列/删列）
--   2. 删掉已被收进 payload 的列的 meta_column 行（它们不再是提升列），
--      改为登记为 **payload 路径**
--   3. 给保留的提升列写「payloadPath + normalizeFn + constraint」三要素
--
-- 幂等性：全部 INSERT ... ON CONFLICT DO UPDATE / DELETE 带 WHERE，可重复执行。
--
-- ⚠️ 本迁移**不新增表**，只是往已有的 meta_column 写行。
-- =====================================================================

BEGIN;

-- =====================================================================
-- (1) 自动同步 meta_column（吸收 011/013/014 之后的真实列结构）
--     重跑 008 的派生逻辑：从 information_schema 全量对照
-- =====================================================================
INSERT INTO meta_column (table_name, column_name, data_type, is_business_key)
SELECT
    c.table_name,
    c.column_name,
    c.data_type,
    (c.column_name = 'biz_id')
FROM information_schema.columns c
JOIN information_schema.tables t
    ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
ON CONFLICT (table_name, column_name) DO UPDATE SET
    data_type       = EXCLUDED.data_type,
    is_business_key = EXCLUDED.is_business_key,
    updated_at      = now();

-- 清掉"表里已不存在的列"的治理行（011/013 删掉的 65 列）
DELETE FROM meta_column mc
WHERE mc.table_name IN ('bid_section', 'bid_document')
  AND NOT EXISTS (
      SELECT 1 FROM information_schema.columns c
      WHERE c.table_schema = 'public'
        AND c.table_name = mc.table_name
        AND c.column_name = mc.column_name
  );

-- =====================================================================
-- (2) 给 bid_section 的提升列登记「payload 路径 + 归一函数 + 约束」
--     用 description 字段承载三要素（该列本来就是"语义描述"）。
--     格式统一：payload=$.xxx | norm=fn | check=...
-- =====================================================================
UPDATE meta_column SET description =
    'payload=$.sectionId | norm=str | key=PK(section_id)', updated_at = now()
WHERE table_name='bid_section' AND column_name='section_id';

UPDATE meta_column SET description =
    'payload=$.title | norm=str | query=WHERE/ORDER BY', updated_at = now()
WHERE table_name='bid_section' AND column_name='title';

UPDATE meta_column SET description =
    'payload=$.kind | norm=normalize_section_kind | check=IN(commercial,technical,deviation,attachment)',
    value_domain = 'commercial|technical|deviation|attachment',
    semantic_type = 'enum', updated_at = now()
WHERE table_name='bid_section' AND column_name='kind';

UPDATE meta_column SET description =
    'payload=$.source | norm=normalize_section_source | check=IN(template,ai,table,attachment,copied_template)',
    value_domain = 'template|ai|table|attachment|copied_template',
    semantic_type = 'enum', updated_at = now()
WHERE table_name='bid_section' AND column_name='source';

UPDATE meta_column SET description =
    'payload=$.status | norm=normalize_section_status | check=IN(pending,generating,generated,missing)',
    value_domain = 'pending|generating|generated|missing',
    semantic_type = 'enum', updated_at = now()
WHERE table_name='bid_section' AND column_name='status';

UPDATE meta_column SET description =
    'payload=$.content | norm=str | 长正文（text 自动 TOAST）', updated_at = now()
WHERE table_name='bid_section' AND column_name='content';

UPDATE meta_column SET description =
    'payload=$.level | norm=to_int, max(v,1) | 排序（树形层级）', updated_at = now()
WHERE table_name='bid_section' AND column_name='level';

UPDATE meta_column SET description =
    'payload=$.order | norm=to_int, max(v,0) | 排序（章节顺序）', updated_at = now()
WHERE table_name='bid_section' AND column_name='order_no';

UPDATE meta_column SET description =
    'payload=$.required | norm=to_bool | query=WHERE（必答项过滤）', updated_at = now()
WHERE table_name='bid_section' AND column_name='required';

UPDATE meta_column SET description =
    'payload=$.requirementIds | norm=list[str] | query=覆盖关系（另有 workspace_requirement_coverage）',
    semantic_type = 'array', updated_at = now()
WHERE table_name='bid_section' AND column_name='requirement_ids';

UPDATE meta_column SET description =
    '★全保真事实：section 对象完整 JSON。代码写入的 108 个 key 全在这；'
    '加字段不用改表。永不加约束。已收进 payload 的字段：'
    'sourceRefs/materialScopes/responseType/writingProfile/parentSectionId/'
    'templateBlocks/templateTables/filledTables/blanksToFill/evidenceEntries/'
    'evidenceGroups/evidenceSummary/requirementResponseRows/editHistory/'
    'agentResults/extension/evaluationScore/evaluationWeight/evaluationDepth/'
    'evaluationIds/volumeId/formatItemId/nodeType/itemType/template*(8 个)/'
    'contentMatchesTemplate',
    semantic_type = 'payload', updated_at = now()
WHERE table_name='bid_section' AND column_name='payload';

UPDATE meta_column SET description =
    'JOIN 到 bid_document（FK）', updated_at = now()
WHERE table_name='bid_section' AND column_name='bid_document_id';

UPDATE meta_column SET description =
    '独立事实（非 payload 投影）：章节版本号，参与唯一键 '
    'UNIQUE(bid_document_id, section_id, version)', updated_at = now()
WHERE table_name='bid_section' AND column_name='version';

UPDATE meta_column SET description =
    '独立事实（非 payload 投影）：文档版本号，并发控制用', updated_at = now()
WHERE table_name='bid_document' AND column_name='version';

UPDATE meta_column SET description = '审计时间戳（coalesce 语义：不被刷新）', updated_at = now()
WHERE table_name='bid_section' AND column_name='created_at';
UPDATE meta_column SET description = '审计时间戳（touch 语义：只在真变化时刷新）', updated_at = now()
WHERE table_name='bid_section' AND column_name='updated_at';

-- =====================================================================
-- (3) bid_document 的提升列登记
-- =====================================================================
UPDATE meta_column SET description =
    'JOIN 到 workspace（FK）', updated_at = now()
WHERE table_name='bid_document' AND column_name='workspace_id';
UPDATE meta_column SET description =
    'payload=$.status | norm=normalize_bid_doc_status | '
    'check=IN(planned,generating,generated,exported,missing)',
    value_domain = 'planned|generating|generated|exported|missing',
    semantic_type = 'enum', updated_at = now()
WHERE table_name='bid_document' AND column_name='status';

UPDATE meta_column SET description =
    'payload=$.mode | norm=normalize_bid_mode | check=IN(open,secret)',
    value_domain = 'open|secret', semantic_type='enum', updated_at = now()
WHERE table_name='bid_document' AND column_name='mode';

UPDATE meta_column SET description =
    'payload=$.plan | 章节数组（jsonb）。与 bid_section 有投影关系：'
    'bid_section.payload 即本数组元素', semantic_type='jsonb', updated_at = now()
WHERE table_name='bid_document' AND column_name='plan';

UPDATE meta_column SET description =
    'payload=$.deviationTables | 偏离表（jsonb）。'
    '视图 v_deviation_row / v_parameter_deviation_row 依赖', semantic_type='jsonb', updated_at = now()
WHERE table_name='bid_document' AND column_name='deviation_tables';

UPDATE meta_column SET description =
    'payload=$.exportPath | 导出产物路径', updated_at = now()
WHERE table_name='bid_document' AND column_name='export_path';

UPDATE meta_column SET description =
    'payload=$.projectId | JOIN 到投标项目', updated_at = now()
WHERE table_name='bid_document' AND column_name='project_id';

UPDATE meta_column SET description =
    'payload=$.requirementMappingStatus | 要求映射状态', updated_at = now()
WHERE table_name='bid_document' AND column_name='requirement_mapping_status';

UPDATE meta_column SET description =
    '★全保真事实：bidDocument 对象完整 JSON。'
    '已收进 payload 的字段（35 个，全是规划/解析运行元信息 + warnings）：'
    'planSource*/planning*（9 个）/parse*/generatedAt/exportedAt/lastExportMode/'
    'evaluationMethod/evaluationPlan/evaluationStrategy/evaluationPlanTotal/'
    'complianceReview/sectionBlueprints/submissionStructure/templateFragments/'
    'templateFragmentsArtifact/requirementCoverage/uncoveredRequirements/'
    'duplicatedRequirements/requirementMapBatchCount/requirementMappingWarnings/'
    'proofread/agentResults/copiedTemplateCount/*_warnings（3 个）',
    semantic_type = 'payload', updated_at = now()
WHERE table_name='bid_document' AND column_name='payload';

-- =====================================================================
-- (4) 其余表新增的 payload 列，统一登记语义
-- =====================================================================
UPDATE meta_column SET description =
    '★全保真事实：该实体完整 JSON。写 payload 永不失败；'
    '加字段不用改表。提升列是其投影（归一 + 约束，允许为空）。',
    semantic_type = 'payload', updated_at = now()
WHERE column_name = 'payload'
  AND table_name NOT IN ('bid_section', 'bid_document');

-- =====================================================================
-- (5) analysis_run 的 payload 登记（有 17 行真实数据，F 组核心可观测表）
-- =====================================================================
UPDATE meta_column SET description =
    '★全保真事实（已回填 17/17 行，23 个 key）。'
    '本表 31 列里 29 列真实填充 100% —— 那些列是**真在用**的，'
    '按准入规则保留，不做列收缩；payload 只用于兜住契约外的新字段。',
    semantic_type = 'payload', updated_at = now()
WHERE table_name='analysis_run' AND column_name='payload';

COMMIT;
