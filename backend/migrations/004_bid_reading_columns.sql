-- =====================================================================
-- 招投标智能体 · 迁移 004：补齐「标书读取」所需字段
-- 目标库：PostgreSQL 14+（开发环境 postgres:16）
--
-- 背景：以「标书制作同学能否直接从表里读到」为尺子，对拍真实
--   fineReview.rows[]（0.94GB workspaces.json）与 analysisPack.targetModules[]
--   后发现 3 张表缺 13 列——缺的正是前端匹配页 / 标书 MCP 读取层直接消费的字段。
--   详见 docs2/PostgreSQL表字段总清单与补表计划.md §「标书读取核对」。
--
-- 本文件三件事：
--   (1) match_item 补：technicalAlignment（技术参数匹配明细）/ gaps / kind /
--       normalized_text / source —— 前端 MatchingPage.vue 与标书 param_match.py 直读
--   (2) requirement_group 补：sourceView（撰写流程用）/ ignored_ranges /
--       template_count / source_items —— 均为模块级派生结构
--   (3) tender_project 补：opening_time / contact / bid_bond / bid_validity ——
--       项目信息按列直读（否则只能去 tender_requirement 里翻 field/label/value）
--
-- 设计原则：复杂派生结构原样落 jsonb（前端读出来即用、无损回放），
--   只有要「按字段查询」的标量才单独建列；不拆新表（避免过度建模）。
--   可重建的派生字段（matchedObjectIds/evidenceItems/matchedMaterialIds/
--   evidenceIds/templateSections）不落列，由读取层从既有表拼回。
--
-- 幂等：全部 ADD COLUMN IF NOT EXISTS。
-- =====================================================================

BEGIN;

-- =====================================================================
-- (1) match_item 补列：技术参数匹配明细 + 契约兼容字段
--     对应 fineReview.rows[] 里此前未落库的字段。
-- =====================================================================
ALTER TABLE match_item
    -- 技术参数匹配明细：rows[].technicalAlignment
    --   {coverage, coveredPoints[], gaps[], matchedUnits[
    --     {productId, productName, brand, model, matchedParams[],
    --      matchedParameterDetails[{parameterId, description, productId,
    --      evidencePath, evidenceUpdatedAt}], matchedFragment}]}
    --   前端 MatchingPage.vue 渲染覆盖率/产品卡片/参数正文；标书 param_match.py 提取命中参数。
    ADD COLUMN IF NOT EXISTS technical_alignment jsonb,
    -- 未覆盖点：rows[].gaps（技术参数对齐里「哪几条要求没被满足」）
    ADD COLUMN IF NOT EXISTS gaps                jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 契约兼容字段 rows[].kind：要求类别标签（如「技术要求」「商务要求」），
    --   多模块用其生成显示标题（document_matching.py / eligibility.py）
    ADD COLUMN IF NOT EXISTS kind                text,
    -- 契约字段 rows[].normalizedText：归一化正文。
    --   注意与 normalized_requirement（整行原文）不同，这是逐条的干净正文。
    ADD COLUMN IF NOT EXISTS normalized_text     text,
    -- 溯源对象 rows[].source：{sectionId, sectionPath, segmentId, segmentIds,
    --   sourceLocator, tableIds}。与展平列（source_segment_id/section_id 等）并存，
    --   保证前端出参 contract 的无损回放。
    ADD COLUMN IF NOT EXISTS source              jsonb;

-- =====================================================================
-- (2) requirement_group 补列：模块级派生结构
--     对应 targetModules[] 里此前未落库的模块级字段。
-- =====================================================================
ALTER TABLE requirement_group
    -- 模块源章节视图：targetModules[].sourceView
    --   {schemaVersion, mode, displayMode, sectionCount, rootSectionCount,
    --    segmentCount, tableCount, blockCount, tableIds[], sections[
    --     {sectionId, title, level, parentId, sectionPath, position,
    --      sourceSegmentIds[], contentSegmentIds[], tableIds[],
    --      sourceLocator, startLocator, endLocator, blocks[]}]}
    --   UI 与撰写流程直接使用（builtin_target_processing.py 原话：
    --   "The UI and writing workflow use sourceView and templateIndex"）。
    ADD COLUMN IF NOT EXISTS source_view     jsonb,
    -- 被忽略的段落范围：targetModules[contract].ignoredRanges
    --   [{rangeId, startSegmentId, endSegmentId, segmentIds[]}]
    ADD COLUMN IF NOT EXISTS ignored_ranges  jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 模版数量：targetModules[template].templateCount
    ADD COLUMN IF NOT EXISTS template_count  integer,
    -- 模版段落级源项：targetModules[template].sourceItems（细粒度审计用）
    ADD COLUMN IF NOT EXISTS source_items    jsonb NOT NULL DEFAULT '[]'::jsonb;

-- =====================================================================
-- (3) tender_project 补列：项目基本信息按列直读
--     对应 analysisPack.projectInfo（field/label/value）里的常用项。
--     全部 11 项仍可在 tender_requirement(project_info) 读到；
--     这里补的是「标书同学想按列直读、不必解析 field/label/value」的高频列。
-- =====================================================================
ALTER TABLE tender_project
    -- 开标时间（project_info.field='openingTime'）
    ADD COLUMN IF NOT EXISTS opening_time text,
    -- 联系方式（project_info.field='contact'，实测可能多条；这里存主要一条）
    ADD COLUMN IF NOT EXISTS contact      text,
    -- 投标保证金（project_info.field='bidBond'）
    ADD COLUMN IF NOT EXISTS bid_bond     text,
    -- 投标有效期（project_info.field='bidValidity'）
    ADD COLUMN IF NOT EXISTS bid_validity text;

COMMIT;

-- =====================================================================
-- 备注
-- 1) 本文件只补列、不建表、不拆表。technical_alignment 的
--    matchedUnits[]（N 个产品）→ matchedParameterDetails[]（N 条参数）
--    是两层嵌套，现按 jsonb 原样落库；若将来要「按参数反查被哪些产品满足」，
--    再拆 match_item_matched_unit / match_item_matched_param 两张表。
-- 2) 故意不落的（可由读取层重建）：
--      matchedObjectIds    → match_item_fact（fact_biz_id）
--      evidenceItems       → match_item_evidence
--      matchedMaterialIds  → match_item_fact.fact_snapshot 里的 materialIds
--      evidenceIds         → match_item_evidence（source_type='evidence'）
--      templateSections    → document_section（template.source_section_ids 反查）
--    bidDocument.parameterMatches 也是读取时从 technical_alignment 现算
--    （store.py:1819），不落库。
-- 3) 原计划的「004_comments.sql（COMMENT ON 全库 + meta_* 字典）」顺延为 005。
-- =====================================================================
