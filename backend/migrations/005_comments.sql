-- =====================================================================
-- 招投标智能体 · 迁移 005：治理元数据（精简版）
-- 目标库：PostgreSQL 14+（开发环境 postgres:16）
--
-- 背景：把"本体治理"成果落成 PG 原生可查询形态，分两部分：
--   (1) COMMENT ON TABLE/COLUMN —— psql `\d+` 与 information_schema 直接可读
--   (2) meta_table / meta_column / meta_relationship 结构自动灌入 ——
--       agent 可直接 SQL 查"某表有哪些字段、什么含义"
--
-- 与公司 JSONRule 本体格式对齐（参照 docs2/JSONRULE-Sample.json）：
--   classDefs[].name/showName/desc   -> meta_table.table_name/table_cn_name/description
--   attrs[].name/showName/desc/type  -> meta_column.column_name/column_cn_name/description/data_type
--   attrs[].bizzKey                  -> meta_column.is_business_key
--   attrs[].enable                   -> meta_column.is_enabled（本文件新增）
--   relationshipDefs.*               -> meta_relationship.*
--   将来若要接 M3 问数，可由 meta_* 表无损反生成 JSONRULE.json。
--
-- 幂等：COMMENT ON 可重复设置；INSERT 用 ON CONFLICT DO NOTHING/UPDATE；
--       索引用 IF NOT EXISTS。可重复执行。
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- (0) 元数据表结构微调
--     * meta_column 补 is_enabled（对齐 JSONRule 的 enable：字段是否启用）
--     * 描述列改为可空：结构先自动灌入，中文描述后续渐进补全
-- ---------------------------------------------------------------------
ALTER TABLE meta_column
    ADD COLUMN IF NOT EXISTS is_enabled boolean NOT NULL DEFAULT true,
    ALTER COLUMN column_cn_name DROP NOT NULL;
ALTER TABLE meta_relationship
    ALTER COLUMN relation_cn_name DROP NOT NULL;

-- ---------------------------------------------------------------------
-- (1) COMMENT ON TABLE：38 张表（含业务含义）
-- ---------------------------------------------------------------------

COMMENT ON TABLE source_site               IS '招标来源网站';
COMMENT ON TABLE tender_announcement       IS '招标公告（从来源网站采集的一条公告，不等同于完整招标文件）';
COMMENT ON TABLE announcement_match_term   IS '公告匹配维度打分（公告 vs 企业业务方向的命中明细）';
COMMENT ON TABLE tender_project            IS '招标项目';
COMMENT ON TABLE tender_document           IS '招标文件（文件本体在磁盘，这里只存 path+hash）';
COMMENT ON TABLE document_acquisition      IS '文件获取记录（下载/上传）';

COMMENT ON TABLE document_segment          IS '文档段落（原 workspace.project.document.segments；segments 与 units 合并，is_unit 标记）';
COMMENT ON TABLE document_section          IS '文档章节（section_classifier 输出）';
COMMENT ON TABLE document_table            IS '文档表格';

COMMENT ON TABLE requirement_group         IS '要求分组（= targetModules[] 的模块级信息，8 模块：technical/qualification/commercial/evaluation/contract/template/project_info/rejection）';
COMMENT ON TABLE tender_requirement        IS '招标要求（8 模块 items 统一，target_module 区分；原 tender_pack.analysisPack.targetModules[].items[]）';
COMMENT ON TABLE requirement_segment       IS '要求-段落关联（一条要求出自哪些段落）';

COMMENT ON TABLE match_assessment          IS '匹配评估（一次 run 的一次 fineReview 汇总）';
COMMENT ON TABLE match_item                IS '匹配条目（逐条：一条招标要求 vs 企业能力的判定结论，前端 fineReview.rows 来源）';
COMMENT ON TABLE match_item_evidence       IS '匹配条目证据（该条要求命中的证明材料）';
COMMENT ON TABLE match_item_fact           IS '匹配条目-企业事实关联（该条要求命中了哪些企业本体对象）';
COMMENT ON TABLE match_risk                IS '匹配风险（废标风险等）';
COMMENT ON TABLE review_record             IS '复核记录（人工复核流水）';

COMMENT ON TABLE analysis_run              IS '分析运行（一次解析/提取/匹配的 run 信封）';
COMMENT ON TABLE analysis_run_stage        IS '分析运行阶段（run 内各 stage）';
COMMENT ON TABLE llm_call_log              IS 'LLM 调用日志（token/耗时/成败）';

COMMENT ON TABLE workspace                 IS '工作区（一个项目的一个匹配工作区）';
COMMENT ON TABLE workspace_draft           IS '工作区草稿（版本化）';
COMMENT ON TABLE bid_document              IS '标书文档（生成产物）';
COMMENT ON TABLE bid_section               IS '标书章节';
COMMENT ON TABLE bid_plan                  IS '标书计划（撰写计划，jsonb）';

COMMENT ON TABLE monitoring_rule           IS '公告采集规则（从哪些网站、以什么频率、抓什么公告）';
COMMENT ON TABLE screening_profile         IS '企业初筛方案（公告是否值得提醒企业的口径与阈值）';
COMMENT ON TABLE screening_constraint      IS '初筛约束（方案中的一条可执行条件）';
COMMENT ON TABLE screening_assessment      IS '初筛/资格评估（stage 区分：announcement 公告初筛 / document 招标文件资格预评估）';
COMMENT ON TABLE screening_check           IS '初筛约束逐条检查（hit/miss/unknown）';
COMMENT ON TABLE reminder                  IS '提醒/通知事件（合并 reminders.json 与 NotificationEvent）';

COMMENT ON TABLE procurement_need          IS '采购需求（reasoning.procurementNeeds：公告阶段的轻量预关联）';
COMMENT ON TABLE decision_summary          IS '决策摘要（reasoning.decisionSummary：前端决策摘要面板来源）';
COMMENT ON TABLE automation_run            IS '自动化运行记录（automation-runs.json）';

COMMENT ON TABLE meta_table                IS '治理元数据·表（数据字典：类）';
COMMENT ON TABLE meta_column               IS '治理元数据·字段（数据字典：属性）';
COMMENT ON TABLE meta_relationship         IS '治理元数据·关系（外键语义 + 软引用）';

-- ---------------------------------------------------------------------
-- (2) 通用列注释（id/org_id/created_at/updated_at 出现在绝大多数表）
--     用 DO 循环 + 存在性判断，保证健壮且不重复维护
-- ---------------------------------------------------------------------
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

-- ---------------------------------------------------------------------
-- (3) 关键业务列注释（高频查询 / 有值域契约的列，手写）
-- ---------------------------------------------------------------------
COMMENT ON COLUMN match_item.status              IS '匹配状态，值域契约（前端只认 6 个，不得新增第 7 态）：满足|部分满足|不满足|待补材料|待澄清|不适用';
COMMENT ON COLUMN match_item.target_module       IS '归属模块（8 域主线：technical/qualification/commercial/evaluation/contract/template/project_info/rejection）';
COMMENT ON COLUMN match_item.requirement_class   IS '要求类别：evidence|rejection|writing|compliance|fill';
COMMENT ON COLUMN match_item.mandatory           IS '是否强制项';
COMMENT ON COLUMN match_item.is_rejection        IS '是否废标项';
COMMENT ON COLUMN match_item.reason              IS '判定理由';
COMMENT ON COLUMN match_item.suggestion          IS '处理建议';
COMMENT ON COLUMN match_item.confidence          IS '判定置信度';
COMMENT ON COLUMN match_item.review_status       IS '复核状态';
COMMENT ON COLUMN match_item.technical_alignment IS '技术参数匹配明细（coverage/matchedUnits/matchedParameterDetails，前端卡片与标书 param_match 直读）';
COMMENT ON COLUMN match_item.kind                IS '要求类别标签（如"技术要求""商务要求"，多模块生成显示标题用）';
COMMENT ON COLUMN match_item.normalized_text     IS '归一化正文（逐条干净正文）';
COMMENT ON COLUMN match_item.source              IS '溯源对象 {sectionId,sectionPath,segmentId,segmentIds,sourceLocator,tableIds}';

COMMENT ON COLUMN match_assessment.decision              IS '整体结论';
COMMENT ON COLUMN match_assessment.fact_sync_batch_id    IS '本次匹配用的企业事实快照批次（D1 决策：结论可复现的关键）';
COMMENT ON COLUMN match_assessment.summary               IS '22 字段汇总原样透出（前端 reviewOutput 用）';

COMMENT ON COLUMN match_item_evidence.status            IS '证据状态';
COMMENT ON COLUMN match_item_evidence.evidence_level    IS '证据等级';
COMMENT ON COLUMN match_item_evidence.valid_from        IS '证据有效期起';
COMMENT ON COLUMN match_item_evidence.valid_until       IS '证据有效期止';
COMMENT ON COLUMN match_item_fact.fact_kind             IS '企业事实类别（8 类：organization/qualification_certificate/...）';
COMMENT ON COLUMN match_item_fact.fact_biz_id           IS '命中的企业事实 biz_id';
COMMENT ON COLUMN match_item_fact.fact_snapshot         IS '命中时刻的企业事实快照（防事后被改）';

COMMENT ON COLUMN tender_requirement.target_module      IS '归属模块（与 requirement_group.group_key 对应）';
COMMENT ON COLUMN tender_requirement.requirement_class  IS '要求类别：evidence|rejection|writing|compliance|fill';
COMMENT ON COLUMN tender_requirement.clause_index       IS '条款序号';
COMMENT ON COLUMN tender_requirement.clause_count       IS '条款总数';
COMMENT ON COLUMN tender_requirement.source             IS '溯源对象（真实 item 的 source 是对象非文本）';
COMMENT ON COLUMN requirement_group.group_key           IS '分组键（8 模块键，与 target_module 一致）';
COMMENT ON COLUMN requirement_group.ignored             IS '是否被忽略（display 聚合需过滤 ignored=false）';
COMMENT ON COLUMN requirement_group.ranges              IS '提取范围 targetRanges[].ranges[] 原样落 jsonb';

COMMENT ON COLUMN tender_announcement.relevance_score   IS '公告相关性打分（初筛）';
COMMENT ON COLUMN tender_announcement.reminder_status   IS '提醒状态';
COMMENT ON COLUMN tender_announcement.follow_up         IS '是否跟进';
COMMENT ON COLUMN tender_announcement.dedupe_key        IS '去重键';
COMMENT ON COLUMN tender_document.file_path             IS '磁盘相对路径（文件本体不入库）';
COMMENT ON COLUMN tender_document.file_hash             IS 'contentHash/sha256';
COMMENT ON COLUMN tender_document.parse_status          IS '解析状态';
COMMENT ON COLUMN tender_document.is_current            IS '是否当前版本';

COMMENT ON COLUMN screening_assessment.stage           IS '评估阶段：announcement=公告初筛 / document=招标文件资格预评估';
COMMENT ON COLUMN screening_assessment.eligibility_status IS '资格结论：满足|不确定|不满足';
COMMENT ON COLUMN screening_assessment.recommendation  IS '建议（如"不建议参与"）';
COMMENT ON COLUMN screening_assessment.risks           IS '废标风险（原 reasoning.eligibility.rejectionRisks）';
COMMENT ON COLUMN screening_check.result               IS '命中结论：hit|miss|unknown';
COMMENT ON COLUMN reminder.status                      IS '状态：待处理|已完成';
COMMENT ON COLUMN reminder.channels                    IS '通知渠道：页内弹窗|企业微信|邮件';
COMMENT ON COLUMN reminder.delivery_status             IS '投递状态';

COMMENT ON COLUMN bid_document.status                  IS '标书状态';
COMMENT ON COLUMN bid_document.export_path             IS '导出文件路径';
COMMENT ON COLUMN workspace.title                      IS '工作区标题';

-- ---------------------------------------------------------------------
-- (4) 自动灌 meta_table（表名 + 中文名 + 分组 + 层级）
-- ---------------------------------------------------------------------
INSERT INTO meta_table (table_name, table_cn_name, domain, layer) VALUES
    ('source_site','招标来源网站','B招标与文件','tender'),
    ('tender_announcement','招标公告','B招标与文件','tender'),
    ('announcement_match_term','公告匹配维度打分','B招标与文件','tender'),
    ('tender_project','招标项目','B招标与文件','tender'),
    ('tender_document','招标文件','B招标与文件','tender'),
    ('document_acquisition','文件获取记录','B招标与文件','tender'),
    ('document_segment','文档段落','C解析结果','parse'),
    ('document_section','文档章节','C解析结果','parse'),
    ('document_table','文档表格','C解析结果','parse'),
    ('requirement_group','要求分组','D提取结果','extract'),
    ('tender_requirement','招标要求','D提取结果','extract'),
    ('requirement_segment','要求-段落关联','D提取结果','extract'),
    ('match_assessment','匹配评估','E匹配结果','match'),
    ('match_item','匹配条目','E匹配结果','match'),
    ('match_item_evidence','匹配条目证据','E匹配结果','match'),
    ('match_item_fact','匹配条目-企业事实关联','E匹配结果','match'),
    ('match_risk','匹配风险','E匹配结果','match'),
    ('review_record','复核记录','E匹配结果','match'),
    ('analysis_run','分析运行','F运行记录','run'),
    ('analysis_run_stage','分析运行阶段','F运行记录','run'),
    ('llm_call_log','LLM 调用日志','F运行记录','run'),
    ('workspace','工作区','G标书','bid'),
    ('workspace_draft','工作区草稿','G标书','bid'),
    ('bid_document','标书文档','G标书','bid'),
    ('bid_section','标书章节','G标书','bid'),
    ('bid_plan','标书计划','G标书','bid'),
    ('monitoring_rule','公告采集规则','I初筛与提醒','screen'),
    ('screening_profile','企业初筛方案','I初筛与提醒','screen'),
    ('screening_constraint','初筛约束','I初筛与提醒','screen'),
    ('screening_assessment','初筛/资格评估','I初筛与提醒','screen'),
    ('screening_check','初筛约束逐条检查','I初筛与提醒','screen'),
    ('reminder','提醒/通知事件','I初筛与提醒','screen'),
    ('procurement_need','采购需求','J抽取补齐','extract'),
    ('decision_summary','决策摘要','K决策与运行','match'),
    ('automation_run','自动化运行记录','K决策与运行','run'),
    ('meta_table','治理元数据·表','L治理元数据','meta'),
    ('meta_column','治理元数据·字段','L治理元数据','meta'),
    ('meta_relationship','治理元数据·关系','L治理元数据','meta')
ON CONFLICT (table_name) DO UPDATE SET
    table_cn_name = EXCLUDED.table_cn_name,
    domain        = EXCLUDED.domain,
    layer         = EXCLUDED.layer;

-- 企业事实组（A 组）权威在 M3，PG 是只读镜像
UPDATE meta_table SET is_authoritative = false WHERE layer = 'fact';

-- ---------------------------------------------------------------------
-- (5) 自动灌 meta_column（表名 + 字段名 + 类型，从 information_schema）
--     描述列（column_cn_name/description/value_domain）后续由 skill 或人工补
-- ---------------------------------------------------------------------
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
    data_type      = EXCLUDED.data_type,
    is_business_key = EXCLUDED.is_business_key;

-- ---------------------------------------------------------------------
-- (6) 自动灌 meta_relationship（外键关系，从 pg_constraint）
--     复合外键只取第一列（精简版）；完整关系语义后续补 relation_cn_name
-- ---------------------------------------------------------------------
CREATE UNIQUE INDEX IF NOT EXISTS ux_meta_relationship_uniq
    ON meta_relationship (from_table, from_column, to_table, to_column);

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
-- 1) 本文件只做"精简治理"：COMMENT + meta 结构灌入。字段级中文描述
--    （meta_column.column_cn_name/description/value_domain）与关系语义
--    （meta_relationship.relation_cn_name）后续可由组长的 skill 或人工补。
-- 2) 已与 JSONRule 格式对齐（含补 is_enabled），将来可无损反生成 JSONRULE.json。
-- 3) 复合外键（requirement_group/tender_requirement 的 document_id+biz_id）在
--    meta_relationship 里只落第一列，属已知取舍；精确化时手工补行即可。
-- =====================================================================
