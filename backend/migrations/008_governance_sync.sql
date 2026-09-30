-- =====================================================================
-- 招投标智能体 · 迁移 008：治理元数据同步（对齐 006 之后的 54 表 / 5 视图）
-- 目标库：PostgreSQL 14+（开发环境 postgres:16）
--
-- 背景：005 是给 48 表灌的治理元数据。006 之后实测：
--     meta_table   48 行  vs 实际 54 张表   ← 缺 6 张
--     meta_column 919 行  vs 实际 1082 列   ← 缺 163 列（bid_document 37 + bid_section 39
--                                             + 6 张新表 87）
--   本文件把这 6 表 / 163 列补进治理元数据，并给新增列补中文说明
--   （COLUMN COMMENT + meta_column.column_cn_name）。
--
-- 与 005 的分工：005 是"首次全量灌 + 建索引"；008 是"结构增量同步"。
--   005 的 (5)(6) 两段本来就是从 information_schema / pg_constraint 自动派生，
--   所以这里**原样重跑**即可自动吸收新表新列新外键，不需要逐列维护。
--   只有 meta_table（硬编码中文名/分组/层级）需要显式补 6 行。
--
-- 幂等：COMMENT ON 可重复设置；INSERT 用 ON CONFLICT DO UPDATE/NOTHING；
--       column_cn_name 回填带 IS NULL 守卫。可重复执行。
-- =====================================================================

BEGIN;

-- =====================================================================
-- (1) 通用列注释：id / biz_id / org_id / created_at / updated_at
--     原样重跑 005 的 DO 循环（它按 information_schema 遍历所有表，
--     因此 006 新增的 6 张表会被自动覆盖；无对应列时跳过）
-- =====================================================================
DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
    LOOP
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=r.table_name AND column_name='id') THEN
            EXECUTE format('COMMENT ON COLUMN public.%I.id IS %L', r.table_name, '内部主键（自增，跨表关联用）');
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=r.table_name AND column_name='biz_id') THEN
            EXECUTE format('COMMENT ON COLUMN public.%I.biz_id IS %L', r.table_name, '业务键，沿用旧 JSON 字符串 ID（前端与标书侧零改动）');
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=r.table_name AND column_name='org_id') THEN
            EXECUTE format('COMMENT ON COLUMN public.%I.org_id IS %L', r.table_name, '组织 ID（多租户预留，默认 org-001）');
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=r.table_name AND column_name='created_at') THEN
            EXECUTE format('COMMENT ON COLUMN public.%I.created_at IS %L', r.table_name, '创建时间');
        END IF;
        IF EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_schema='public' AND table_name=r.table_name AND column_name='updated_at') THEN
            EXECUTE format('COMMENT ON COLUMN public.%I.updated_at IS %L', r.table_name, '更新时间');
        END IF;
    END LOOP;
END $$;

-- =====================================================================
-- (2) 表级注释：006 新增的 6 张表
-- =====================================================================
COMMENT ON TABLE parse_bundle       IS '解析包（ParseBundle 汇总；解析供应方本地无法重算，必须存）';
COMMENT ON TABLE evaluation_item    IS '评分要素（eval_parser 确定性产出，本地可重算但落表免重跑）';
COMMENT ON TABLE template_fragment  IS '模板片段（template_fragments.py 产出，含可填块与锁定块）';
COMMENT ON TABLE parse_risk         IS '解析级风险（Pi 侧产出，本地无底料）';
COMMENT ON TABLE parse_review       IS '解析审查报告（ParseBundle.parseReviewReport，一个包一份）';
COMMENT ON TABLE agent_result       IS '智能体运行记录（AgentResultEnvelope；粒度是"一次运行"，区别于 llm_call_log 的"一次调用"）';

-- =====================================================================
-- (3) 列级注释：bid_document 的 37 个新列
--     （覆盖 workspace["bidDocument"] 实际写入的全部 key）
-- =====================================================================
COMMENT ON COLUMN bid_document.version                      IS '标书文档版本号（build_bid_document 初始为 1）';
COMMENT ON COLUMN bid_document.project_id                   IS '所属项目（契约 projectId；与 workspace.project_id 同源）';
COMMENT ON COLUMN bid_document.generated_at                 IS '文档生成时间（契约里初值为空串，故用 text 无损回放）';
COMMENT ON COLUMN bid_document.plan_source                  IS '章节规划来源（unplanned/ai/fallback…）';
COMMENT ON COLUMN bid_document.plan_source_detail           IS '章节规划来源明细';
COMMENT ON COLUMN bid_document.planning_source              IS '规划来源标识';
COMMENT ON COLUMN bid_document.planning_source_detail       IS '规划来源明细';
COMMENT ON COLUMN bid_document.planning_provider            IS '规划所用模型供应方';
COMMENT ON COLUMN bid_document.planning_status              IS '规划状态';
COMMENT ON COLUMN bid_document.planning_model               IS '规划所用模型名';
COMMENT ON COLUMN bid_document.planning_fallback_reason     IS '规划回退原因（为何走了兜底规划）';
COMMENT ON COLUMN bid_document.parse_provider               IS '解析供应方（local/pi/workbuddy/dify）';
COMMENT ON COLUMN bid_document.parse_source_detail          IS '解析来源明细';
COMMENT ON COLUMN bid_document.evaluation_method            IS '评分方法（eval_parser 的 method，如"综合评分法"）';
COMMENT ON COLUMN bid_document.last_export_mode             IS '上次导出模式（open 明标 / secret 暗标）';
COMMENT ON COLUMN bid_document.exported_at                  IS '上次导出时间';
COMMENT ON COLUMN bid_document.template_fragments_artifact  IS '模板片段产物路径（parse/template-fragments/*.json）';
COMMENT ON COLUMN bid_document.requirement_mapping_status   IS '需求映射状态';
COMMENT ON COLUMN bid_document.evaluation_plan_total        IS '评分方案总分（各评分点分值合计）';
COMMENT ON COLUMN bid_document.copied_template_count        IS '从招标文件整段复制的格式章节数';
COMMENT ON COLUMN bid_document.requirement_map_batch_count  IS '需求映射批次数';
COMMENT ON COLUMN bid_document.plan                         IS '章节数组，**只存元数据**（sectionId/title/kind/order/requirementIds…）；正文与生成态在 bid_section，读取层按 order_no 拼回（见 006 文件头结构决策）';
COMMENT ON COLUMN bid_document.evaluation_plan              IS '评分方案（评分点清单）';
COMMENT ON COLUMN bid_document.evaluation_strategy          IS '评分策略（撰写侧挂载策略）';
COMMENT ON COLUMN bid_document.deviation_tables             IS '技术/商务两张偏离表 {technical:[],commercial:[]}（偏离类型映射唯一来源）';
COMMENT ON COLUMN bid_document.proofread                    IS '校对结果 {report, riskItems}';
COMMENT ON COLUMN bid_document.compliance_review            IS '合规审查结果（正文改动后置 stale）；**刻意不单独建表**，落本列';
COMMENT ON COLUMN bid_document.section_blueprints           IS '分册前的章节蓝图';
COMMENT ON COLUMN bid_document.submission_structure         IS '电子投标文件组成（本地确定性解析产出；plan_bid_document 的入参之一）';
COMMENT ON COLUMN bid_document.template_fragments           IS '模板片段清单（原样快照；结构化行在 template_fragment 表）';
COMMENT ON COLUMN bid_document.requirement_coverage         IS '需求覆盖台账';
COMMENT ON COLUMN bid_document.uncovered_requirements       IS '未被覆盖的招标要求';
COMMENT ON COLUMN bid_document.duplicated_requirements      IS '重复的招标要求';
COMMENT ON COLUMN bid_document.planning_warnings            IS '规划告警';
COMMENT ON COLUMN bid_document.export_warnings              IS '导出告警';
COMMENT ON COLUMN bid_document.requirement_mapping_warnings IS '需求映射告警';
COMMENT ON COLUMN bid_document.agent_results                IS '规划/撰写智能体运行记录（原样快照；结构化行在 agent_result 表）';

-- =====================================================================
-- (4) 列级注释：bid_section 的 39 个新列
--     （覆盖章节对象实际写入的 key；正文 content 已在 001）
-- =====================================================================
COMMENT ON COLUMN bid_section.kind                       IS '章节大类：commercial/technical/deviation/attachment';
COMMENT ON COLUMN bid_section.source                     IS '章节内容来源：template/ai/table/attachment/copied_template';
COMMENT ON COLUMN bid_section.item_type                  IS '条目类型';
COMMENT ON COLUMN bid_section.writing_profile            IS '撰写画像：technical/commercial/template/attachment（决定提示词与可注入材料范围）';
COMMENT ON COLUMN bid_section.response_type              IS '响应类型：chapter_text/fixed_form/table/deviation/attachment/section_group';
COMMENT ON COLUMN bid_section.required                   IS '是否必需章节';
COMMENT ON COLUMN bid_section.order_no                   IS '章节排序号（读取层按它拼回 bidDocument.plan 数组顺序）';
COMMENT ON COLUMN bid_section.level                      IS '章节层级（Word 标题级别）';
COMMENT ON COLUMN bid_section.parent_section_id          IS '父章节 ID（章节树）';
COMMENT ON COLUMN bid_section.status                     IS '章节生成状态：pending/generating/generated/missing（与 version 的乐观锁语义不同）';
COMMENT ON COLUMN bid_section.template_fragment_id       IS '复用的模板片段 ID（对应 template_fragment.template_id）';
COMMENT ON COLUMN bid_section.template_id                IS '模板 ID';
COMMENT ON COLUMN bid_section.template_type              IS '模板类型';
COMMENT ON COLUMN bid_section.template_ref               IS '模板引用（源章节引用）';
COMMENT ON COLUMN bid_section.template_status            IS '模板复用状态';
COMMENT ON COLUMN bid_section.template_field_count       IS '模板可填字段数';
COMMENT ON COLUMN bid_section.template_table_count       IS '模板表格数';
COMMENT ON COLUMN bid_section.template_locked_block_count IS '模板锁定块数（锁定块不允许改写）';
COMMENT ON COLUMN bid_section.content_matches_template   IS '正文是否仍与模板结构一致（false → 导出走正文渲染兜底）';
COMMENT ON COLUMN bid_section.volume_id                  IS '所属分册 ID';
COMMENT ON COLUMN bid_section.format_item_id             IS '投标文件格式项 ID（对应 tender_requirement 里 target_module=template 的行）';
COMMENT ON COLUMN bid_section.node_type                  IS '章节节点类型';
COMMENT ON COLUMN bid_section.evaluation_score           IS '挂载评分项的分值（numeric：需要按分值排序/筛选）';
COMMENT ON COLUMN bid_section.evaluation_weight          IS '挂载评分项的权重';
COMMENT ON COLUMN bid_section.evaluation_depth           IS '评分项层级深度';
COMMENT ON COLUMN bid_section.requirement_ids            IS '本章节响应的招标要求 ID 列表（text[]，可按元素查询）';
COMMENT ON COLUMN bid_section.material_scopes            IS '允许注入的 knowledge 材料分组键（material_keys_for_writing_profile 的输出）';
COMMENT ON COLUMN bid_section.evaluation_ids             IS '挂载的评分要素 ID 列表';
COMMENT ON COLUMN bid_section.template_blocks            IS '模板块结构（嵌套，整列 jsonb 透出、不拆标量列）';
COMMENT ON COLUMN bid_section.template_tables            IS '模板表格结构';
COMMENT ON COLUMN bid_section.filled_tables              IS '已填写的表格';
COMMENT ON COLUMN bid_section.blanks_to_fill             IS '待填空位';
COMMENT ON COLUMN bid_section.evidence_entries           IS '证据条目';
COMMENT ON COLUMN bid_section.evidence_groups            IS '证据分组';
COMMENT ON COLUMN bid_section.evidence_summary           IS '证据摘要';
COMMENT ON COLUMN bid_section.requirement_response_rows  IS '逐条响应表行（响应表章节用）';
COMMENT ON COLUMN bid_section.edit_history               IS '局部修改历史（rewrite_bid_section 的 append/rewrite/replace）；**刻意不单独建表**，落本列';
COMMENT ON COLUMN bid_section.agent_results              IS '章节级智能体运行记录';
COMMENT ON COLUMN bid_section.extension                  IS 'jsonb 兜底列：只放"无专门列且当前不需按字段查询"的键；某键需要治理时再提升为正式标量列';

-- =====================================================================
-- (5) 列级注释：6 张新表
-- =====================================================================
COMMENT ON COLUMN parse_bundle.parse_bundle_id             IS '解析包业务键（ParseBundle.parseBundleId）';
COMMENT ON COLUMN parse_bundle.schema_version              IS '解析包结构版本（默认 1.0）';
COMMENT ON COLUMN parse_bundle.provider                    IS '解析供应方：local/pi/workbuddy/dify';
COMMENT ON COLUMN parse_bundle.status                      IS '解析状态（默认 completed）';
COMMENT ON COLUMN parse_bundle.project_id                  IS '所属项目（软引用，允许空）';
COMMENT ON COLUMN parse_bundle.workspace_id                IS '所属工作区（软引用，允许空）';
COMMENT ON COLUMN parse_bundle.artifact_path               IS '解析包产物路径（projects/<id>/parse/parse-bundle.json）';
COMMENT ON COLUMN parse_bundle.template_fragments_artifact IS '模板片段产物路径';
COMMENT ON COLUMN parse_bundle.source_file                 IS '源文件信息（path/hash 等）';
COMMENT ON COLUMN parse_bundle.document_blocks             IS '文档块（段落级原始结构快照）';
COMMENT ON COLUMN parse_bundle.document_structure          IS '文档结构树';
COMMENT ON COLUMN parse_bundle.project_meta                IS '项目元信息';
COMMENT ON COLUMN parse_bundle.requirements                IS '要求清单快照（正表在 tender_requirement）';
COMMENT ON COLUMN parse_bundle.evaluation_items            IS '评分要素快照（结构化行在 evaluation_item）';
COMMENT ON COLUMN parse_bundle.bid_format_spec             IS '投标文件格式规范';
COMMENT ON COLUMN parse_bundle.submission_structure        IS '电子投标文件组成（parse_submission_format_tree 确定性解析）';
COMMENT ON COLUMN parse_bundle.template_fragments          IS '模板片段快照（结构化行在 template_fragment）';
COMMENT ON COLUMN parse_bundle.raw_outline                 IS '大纲（Pi OutlinePlanAgent 产出，本地无法重算）';
COMMENT ON COLUMN parse_bundle.risk_items                  IS '解析级风险快照（明细在 parse_risk）';
COMMENT ON COLUMN parse_bundle.parse_review_report         IS '解析审查报告快照（明细在 parse_review）';
COMMENT ON COLUMN parse_bundle.artifacts                   IS '产物清单';
COMMENT ON COLUMN parse_bundle.warnings                    IS '解析告警';
COMMENT ON COLUMN parse_bundle.agent_results               IS '智能体运行记录快照（明细在 agent_result）';
COMMENT ON COLUMN parse_bundle.raw_provider_result         IS '供应方原始返回（审计/回溯用）';

COMMENT ON COLUMN evaluation_item.parse_bundle_id   IS '所属解析包（FK → parse_bundle）';
COMMENT ON COLUMN evaluation_item.factor            IS '评分因素（如"商务评分"）';
COMMENT ON COLUMN evaluation_item.item              IS '评分项名称';
COMMENT ON COLUMN evaluation_item.score             IS '分值（可为空，如"评分说明"类条目）';
COMMENT ON COLUMN evaluation_item.criteria          IS '评分标准（原文）';
COMMENT ON COLUMN evaluation_item.raw_text          IS '该评分点原文行';
COMMENT ON COLUMN evaluation_item.evaluation_method IS '评分方法（与 parse_bundle 一致，便于单表查询）';
COMMENT ON COLUMN evaluation_item.order_no          IS '序号（与 parse_bundle_id 组成幂等键）';
COMMENT ON COLUMN evaluation_item.item_payload      IS '原始条目 jsonb（解析器新增键不必再出迁移）';

COMMENT ON COLUMN template_fragment.parse_bundle_id     IS '所属解析包（FK → parse_bundle）';
COMMENT ON COLUMN template_fragment.template_id         IS '模板 ID（一个包内唯一；与 bid_section.template_id 对应）';
COMMENT ON COLUMN template_fragment.title               IS '模板标题';
COMMENT ON COLUMN template_fragment.kind                IS '模板类型';
COMMENT ON COLUMN template_fragment.source_file_id      IS '来源招标文件 ID';
COMMENT ON COLUMN template_fragment.source_locator      IS '招标文件溯源定位';
COMMENT ON COLUMN template_fragment.index_order         IS '序号（保持原文件顺序）';
COMMENT ON COLUMN template_fragment.block_count         IS '块数';
COMMENT ON COLUMN template_fragment.locked_block_count  IS '锁定块数';
COMMENT ON COLUMN template_fragment.blocks              IS '可填块结构（嵌套 jsonb）';
COMMENT ON COLUMN template_fragment.locked_blocks       IS '锁定块结构（不允许改写）';
COMMENT ON COLUMN template_fragment.fragment_payload    IS '原始片段 jsonb';

COMMENT ON COLUMN parse_risk.parse_bundle_id IS '所属解析包（FK → parse_bundle）';
COMMENT ON COLUMN parse_risk.level           IS '风险等级';
COMMENT ON COLUMN parse_risk.title           IS '风险标题';
COMMENT ON COLUMN parse_risk.detail          IS '风险说明';
COMMENT ON COLUMN parse_risk.source_locator  IS '招标文件溯源定位';
COMMENT ON COLUMN parse_risk.order_no        IS '序号';
COMMENT ON COLUMN parse_risk.risk_payload    IS '原始风险条目 jsonb';

COMMENT ON COLUMN parse_review.parse_bundle_id IS '所属解析包（一个包一份报告）';
COMMENT ON COLUMN parse_review.status          IS '审查状态：completed/not_run/failed';
COMMENT ON COLUMN parse_review.summary         IS '审查报告全文（含各分项检查结论）';
COMMENT ON COLUMN parse_review.warnings        IS '审查告警';

COMMENT ON COLUMN agent_result.run_id          IS '运行 ID（唯一，AgentResultEnvelope.runId）';
COMMENT ON COLUMN agent_result.agent_id        IS '智能体 ID';
COMMENT ON COLUMN agent_result.provider        IS '模型供应方';
COMMENT ON COLUMN agent_result.model           IS '模型名';
COMMENT ON COLUMN agent_result.status          IS '运行状态：success/failed/skipped';
COMMENT ON COLUMN agent_result.input_refs      IS '输入引用列表（text[]）';
COMMENT ON COLUMN agent_result.output          IS '业务结果（各智能体结构各异，整列 jsonb 透出）';
COMMENT ON COLUMN agent_result.source_refs     IS '防幻觉溯源引用';
COMMENT ON COLUMN agent_result.warnings        IS '运行告警（text[]）';
COMMENT ON COLUMN agent_result.confidence      IS '置信度（0..1，带 CHECK 约束）';
COMMENT ON COLUMN agent_result.created_at      IS '运行开始时间（AgentResultEnvelope.createdAt）';
COMMENT ON COLUMN agent_result.finished_at     IS '运行结束时间（AgentResultEnvelope.finishedAt；空串写入时应转 NULL）';
COMMENT ON COLUMN agent_result.parse_bundle_id IS '所属解析包（解析级运行）';
COMMENT ON COLUMN agent_result.workspace_id    IS '所属工作区（撰写级运行）';
COMMENT ON COLUMN agent_result.project_id      IS '所属项目';
COMMENT ON COLUMN agent_result.inserted_at     IS '入库时间（与契约时间区分）';

-- =====================================================================
-- (6) meta_table：补 006 新增的 6 张表
-- =====================================================================
INSERT INTO meta_table (table_name, table_cn_name, domain, layer, description, is_authoritative) VALUES
    ('parse_bundle','解析包','P解析包','parse','ParseBundle 汇总（含 submissionStructure/bidFormatSpec/documentBlocks/rawOutline/rawProviderResult）', true),
    ('evaluation_item','评分要素','P解析包','parse','eval_parser 确定性产出（factor/item/score/criteria）', true),
    ('template_fragment','模板片段','P解析包','parse','template_fragments.py 产出（blocks + lockedBlocks）', true),
    ('parse_risk','解析级风险','P解析包','parse','Pi 侧产出，本地无底料', true),
    ('parse_review','解析审查报告','P解析包','parse','ParseBundle.parseReviewReport，一个包一份', true),
    ('agent_result','智能体运行记录','F运行记录','run','AgentResultEnvelope；粒度是"一次运行"，区别于 llm_call_log 的"一次调用"', true)
ON CONFLICT (table_name) DO UPDATE SET
    table_cn_name = EXCLUDED.table_cn_name,
    domain        = EXCLUDED.domain,
    layer         = EXCLUDED.layer,
    description   = EXCLUDED.description,
    updated_at    = now();

-- =====================================================================
-- (7) 自动灌 meta_column（重跑 005 的派生语句，自动吸收 163 个新列）
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

-- 非 biz_id 的业务键：解析包用 parse_bundle_id、运行记录用 run_id
UPDATE meta_column SET is_business_key = true, updated_at = now()
WHERE (table_name, column_name) IN
      (('parse_bundle','parse_bundle_id'), ('agent_result','run_id'));

-- meta_column 补 6 张新表时，column_cn_name 为 NULL（005 起就是"结构先灌、语义渐进补"）；
-- 这里从 COLUMN COMMENT 直接回填，避免中文说明维护两遍（DRY）。带 IS NULL 守卫，可重复执行。
UPDATE meta_column mc
SET column_cn_name = d.col_comment,
    updated_at     = now()
FROM (
    SELECT c.table_name,
           c.column_name,
           col_description(cl.oid, c.ordinal_position) AS col_comment
    FROM information_schema.columns c
    JOIN pg_class cl     ON cl.relname = c.table_name
    JOIN pg_namespace n  ON n.oid = cl.relnamespace AND n.nspname = 'public'
    WHERE c.table_schema = 'public'
) d
WHERE mc.table_name = d.table_name
  AND mc.column_name = d.column_name
  AND mc.column_cn_name IS NULL
  AND d.col_comment IS NOT NULL;

-- =====================================================================
-- (8) 自动灌 meta_relationship（重跑 005 的派生语句，自动吸收 6 张新表的外键）
-- =====================================================================
INSERT INTO meta_relationship (constraint_name, from_table, from_column, to_table, to_column, relation_type)
SELECT
    con.conname,
    fr.relname,
    fa.attname,
    tgt.relname,
    ta.attname,
    'fk'
FROM pg_constraint con
JOIN pg_class fr      ON fr.oid = con.conrelid
JOIN pg_class tgt     ON tgt.oid = con.confrelid
JOIN pg_namespace n   ON n.oid  = fr.relnamespace
JOIN pg_attribute fa  ON fa.attrelid = con.conrelid  AND fa.attnum = con.conkey[1]
JOIN pg_attribute ta  ON ta.attrelid = con.confrelid AND ta.attnum = con.confkey[1]
WHERE con.contype = 'f' AND n.nspname = 'public'
ON CONFLICT (from_table, from_column, to_table, to_column) DO NOTHING;

COMMIT;

-- =====================================================================
-- 备注
-- 1) 本文件**不改任何业务表结构**，只补 COMMENT 与 meta_* 治理元数据。
-- 2) 与 005 的分工：005 = 首次全量；008 = 结构增量同步。
--    将来每次新增迁移（009…）之后，若 (2)(6) 两段需要新中文名，
--    再出 010_governance_sync.sql 即可；(7)(8) 两段可直接复制复用。
-- 3) 视图（5 个）不进 meta_table（005 起 meta_table 只装 BASE TABLE）；
--    视图的说明由 scripts/gen_field_checklist.py 的 VIEW_META 维护。
-- 4) 本文件可重复执行。
-- =====================================================================
