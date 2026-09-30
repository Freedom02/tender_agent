-- =====================================================================
-- 招投标智能体 · 迁移 006：补齐「标书制作」模块所需的表与列
-- 目标库：PostgreSQL 14+（开发环境 postgres:16）
--
-- 背景：合并「标书制作」代码后核对（见 docs/标书表替换可行性核对.md）发现
--   G 组 5 张表是在标书代码合并**之前**设计的，只覆盖实际字段约 10%：
--     bid_document  只有 3 个业务列，代码实际写入 ~40 个 key（缺 ~37 列）
--     bid_section   只有 5 个列，代码实际写入 ~60 个 key（缺 ~50 列）
--   同时合并引入了**全新的解析包数据层**（agent_runtime.contracts.ParseBundle）
--   与 AgentResultEnvelope，48 张表里一张都没有对应实体。
--
-- 本文件三件事：
--   (1) bid_document 补列：标量（可查询/治理）+ jsonb（不规则大对象）
--   (2) bid_section  补列：标量 + 数组/jsonb
--   (3) 新增 6 张表：parse_bundle / evaluation_item / template_fragment /
--       parse_risk / parse_review / agent_result
--
-- ---------------------------------------------------------------------
-- ★ 结构决策（主文档 §2.2 / §2.3 要求显式拍板）
-- ---------------------------------------------------------------------
-- 问题：代码里**章节对象是嵌在 bidDocument.plan 数组里的**——每个 plan item
--   就是章节本身（带 content / status / templateBlocks / evidenceEntries …），
--   `save_bid_section` 直接改 `bid_doc["plan"][i]["content"]`（store.py:3291）。
--   而表设计把 `bid_plan`(jsonb) 与 `bid_section`(行) **拆成了两张**。
--
-- 选定映射规则：**plan 存顺序与元数据，bid_section 存内容与状态，读取层拼回。**
--
--   bid_plan.plan (jsonb)        ← 章节**元数据**数组，只保留「定结构」必需的键：
--        sectionId / title / kind / order / level / parentSectionId /
--        requirementIds / source / itemType / writingProfile / responseType /
--        required / volumeId …（即不含正文与生成态）
--   bid_section (行)             ← 章节**内容与生成态**：
--        content / status / version / source_refs / template_* / evaluation_* /
--        evidence_* / edit_history / requirement_response_rows …
--   读取层拼回 plan：`bid_section ORDER BY order_no`，逐行取元数据列 + content。
--
-- 为什么不选另一条路（plan 冗余存一份、与 bid_section 双写）：
--   双写违背 D5「同一份数据只落一处」的去重原则，且正文（最大字段）会重复
--   存两遍——本章节对象的体积 99% 是 content。也不选「单张章节表 + plan 只存
--   顺序」：那要改 bid_plan 的语义与其唯一消费者，回填与切读都会乱。
--
-- 影响面（本次只加 schema，不改应用代码，切读是后续阶段）：
--   阶段 4 回填时按上面规则拆分；阶段 6 切读时读取层负责拼回原 `plan` 数组，
--   保证前端契约（bidDocument.plan[i].content）不变。
--
-- ---------------------------------------------------------------------
-- 设计原则（沿用 004 的既定方针，别推翻）
-- ---------------------------------------------------------------------
--   1) 只有需要「按字段查询/治理」的才建标量列；复杂派生结构原样落 jsonb
--      （读出来即用、无损回放）。
--   2) 不拆新表以免过度建模：能用 jsonb 表达的嵌套结构（如 bid_section 的
--      templateBlocks / evidenceGroups）整列透出，不拆标量列（会丢层级）。
--   3) 可重建的派生字段不落列，由读取层从既有表拼回（见 007 视图）。
--   4) 本批只加 schema，store.py / bid_writing/* 一行不动。
--
-- 幂等：CREATE TABLE IF NOT EXISTS / ALTER TABLE ... ADD COLUMN IF NOT EXISTS。
--   本文件可连跑两遍不报错、不重复建对象。
-- =====================================================================

BEGIN;

-- =====================================================================
-- (1) bid_document 补列
--     覆盖 build_bid_document（schemas.py:282）+ workspace["bidDocument"]
--     实际写入的全部 key（主文档 §2.1 的 ~37 个 key + workspaceId/projectId/
--     generatedAt）。原有 3 个业务列 status / mode / export_path 保持不变。
-- =====================================================================
ALTER TABLE bid_document
    -- ---- 标量列（可查询/治理）----
    ADD COLUMN IF NOT EXISTS version                      integer,
    -- 所属项目（build_bid_document 的 projectId；workspace 已挂 project_id，
    -- 这里按契约原样落一份，避免每次都 join workspace 才能拿到）
    ADD COLUMN IF NOT EXISTS project_id                   text,
    -- 生成时间（契约里初值是 ""，可能是空串；用 text 无损回放，
    -- 需要时间范围查询时再在读取层转型）
    ADD COLUMN IF NOT EXISTS generated_at                 text,
    -- 章节规划来源（unplanned / ai / fallback …）
    ADD COLUMN IF NOT EXISTS plan_source                  text,
    ADD COLUMN IF NOT EXISTS plan_source_detail           text,
    ADD COLUMN IF NOT EXISTS planning_source              text,
    ADD COLUMN IF NOT EXISTS planning_source_detail       text,
    ADD COLUMN IF NOT EXISTS planning_provider            text,
    ADD COLUMN IF NOT EXISTS planning_status              text,
    ADD COLUMN IF NOT EXISTS planning_model               text,
    ADD COLUMN IF NOT EXISTS planning_fallback_reason     text,
    -- 解析来源（provider ∈ local/pi/workbuddy/dify）
    ADD COLUMN IF NOT EXISTS parse_provider               text,
    ADD COLUMN IF NOT EXISTS parse_source_detail          text,
    -- 评分方法（eval_parser 的 method，如「综合评分法」）
    ADD COLUMN IF NOT EXISTS evaluation_method            text,
    -- 上次导出模式与时间
    ADD COLUMN IF NOT EXISTS last_export_mode             text,
    ADD COLUMN IF NOT EXISTS exported_at                  text,
    -- 模板片段产物路径（parse/template-fragments/*.json）
    ADD COLUMN IF NOT EXISTS template_fragments_artifact  text,
    -- 需求映射状态
    ADD COLUMN IF NOT EXISTS requirement_mapping_status   text,
    -- ---- 标量列（计数，可聚合/排序）----
    ADD COLUMN IF NOT EXISTS evaluation_plan_total        integer,
    ADD COLUMN IF NOT EXISTS copied_template_count        integer,
    ADD COLUMN IF NOT EXISTS requirement_map_batch_count  integer,
    -- ---- jsonb 列（不规则大对象，原样透出）----
    -- 章节数组：按上面的「结构决策」，这里只存元数据（正文在 bid_section）
    ADD COLUMN IF NOT EXISTS plan                         jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 评分方案 / 评分策略（attach_evaluation_strategy 产出）
    ADD COLUMN IF NOT EXISTS evaluation_plan              jsonb,
    ADD COLUMN IF NOT EXISTS evaluation_strategy          jsonb,
    -- 技术/商务两张偏离表（{technical:[], commercial:[]}）
    ADD COLUMN IF NOT EXISTS deviation_tables             jsonb NOT NULL DEFAULT '{"technical": [], "commercial": []}'::jsonb,
    -- 校对结果（{report, riskItems}）
    ADD COLUMN IF NOT EXISTS proofread                    jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- 合规审查结果（compliance_review_workspace 产出；正文改后置 stale）
    -- 主文档 §三：**不单独建表**，落这里
    ADD COLUMN IF NOT EXISTS compliance_review            jsonb,
    -- 分册前的章节蓝图（section_blueprints）
    ADD COLUMN IF NOT EXISTS section_blueprints           jsonb,
    -- 电子投标文件组成（submissionStructure，本地确定性解析产出）
    ADD COLUMN IF NOT EXISTS submission_structure         jsonb,
    -- 模板片段（templateFragments）
    ADD COLUMN IF NOT EXISTS template_fragments           jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 需求覆盖台账
    ADD COLUMN IF NOT EXISTS requirement_coverage         jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS uncovered_requirements       jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS duplicated_requirements      jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 各类告警（数组）
    ADD COLUMN IF NOT EXISTS planning_warnings            jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS export_warnings              jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS requirement_mapping_warnings jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 规划/撰写智能体运行记录（AgentResultEnvelope[]；结构化明细另落 agent_result 表）
    ADD COLUMN IF NOT EXISTS agent_results                jsonb NOT NULL DEFAULT '[]'::jsonb;

-- =====================================================================
-- (2) bid_section 补列
--     覆盖 build_section（schemas.py:200）+ attach_template_fragments_to_bundle
--     + resolve_tender / rewrite_bid_section / evidence_chapter 实际写入的 key
--     （主文档 §2.2 的 ~60 个 key）。
--     原有 section_id / title / content / version / source_refs 保持语义不变。
-- =====================================================================
ALTER TABLE bid_section
    -- ---- 标量列：分类与编排 ----
    ADD COLUMN IF NOT EXISTS kind                     text,   -- commercial/technical/deviation/attachment
    ADD COLUMN IF NOT EXISTS source                   text,   -- template/ai/table/attachment/copied_template
    ADD COLUMN IF NOT EXISTS item_type                text,
    ADD COLUMN IF NOT EXISTS writing_profile          text,   -- technical/commercial/template/attachment
    ADD COLUMN IF NOT EXISTS response_type            text,   -- chapter_text/fixed_form/table/deviation/attachment
    ADD COLUMN IF NOT EXISTS required                 boolean NOT NULL DEFAULT true,
    -- 排序与层级（读取层按 order_no 拼回 bidDocument.plan 数组顺序）
    ADD COLUMN IF NOT EXISTS order_no                 integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS level                    integer NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS parent_section_id        text,
    -- 生成状态；注意与表已有的 version（乐观锁）不同：status 是生成态词表
    ADD COLUMN IF NOT EXISTS status                   text,
    -- ---- 标量列：模板复用 ----
    ADD COLUMN IF NOT EXISTS template_fragment_id     text,
    ADD COLUMN IF NOT EXISTS template_id              text,
    ADD COLUMN IF NOT EXISTS template_type            text,
    ADD COLUMN IF NOT EXISTS template_ref             text,
    ADD COLUMN IF NOT EXISTS template_status          text,
    ADD COLUMN IF NOT EXISTS template_field_count     integer,
    ADD COLUMN IF NOT EXISTS template_table_count     integer,
    ADD COLUMN IF NOT EXISTS template_locked_block_count integer,
    ADD COLUMN IF NOT EXISTS content_matches_template boolean,
    -- ---- 标量列：分册与格式项 ----
    ADD COLUMN IF NOT EXISTS volume_id                text,
    ADD COLUMN IF NOT EXISTS format_item_id           text,
    ADD COLUMN IF NOT EXISTS node_type                text,
    -- ---- 标量列：评分挂载（numeric：需要「按分值排序/筛选」）----
    --    写入层遇到空串请先转 NULL，再入库
    ADD COLUMN IF NOT EXISTS evaluation_score         numeric,
    ADD COLUMN IF NOT EXISTS evaluation_weight        numeric,
    ADD COLUMN IF NOT EXISTS evaluation_depth         integer,
    -- ---- 数组列（需要按元素查询：ANY / && / @>）----
    ADD COLUMN IF NOT EXISTS requirement_ids          text[] NOT NULL DEFAULT '{}',
    -- knowledge 材料分组键（material_keys_for_writing_profile 的输出）
    ADD COLUMN IF NOT EXISTS material_scopes          text[] NOT NULL DEFAULT '{}',
    -- 挂载的评分要素 ID（evaluation_item.biz_id）
    ADD COLUMN IF NOT EXISTS evaluation_ids           text[] NOT NULL DEFAULT '{}',
    -- ---- jsonb 列：模板结构（嵌套，整列透出、不拆标量）----
    ADD COLUMN IF NOT EXISTS template_blocks          jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS template_tables          jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS filled_tables            jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS blanks_to_fill           jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- ---- jsonb 列：证据与响应 ----
    ADD COLUMN IF NOT EXISTS evidence_entries         jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS evidence_groups          jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS evidence_summary         jsonb,
    ADD COLUMN IF NOT EXISTS requirement_response_rows jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- ---- jsonb 列：局部修改历史（rewrite_bid_section 的 append/rewrite/replace）----
    --    主文档 §三：**不单独建表**，落这里
    ADD COLUMN IF NOT EXISTS edit_history             jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- 章节级智能体运行记录
    ADD COLUMN IF NOT EXISTS agent_results            jsonb NOT NULL DEFAULT '[]'::jsonb,
    -- ---- jsonb 兜底列（本次拍板：**加**）----
    --    理由：章节对象字段还在长（模板/改写/评分/证据四组元数据都在演进），
    --    加一列兜底可以避免「每加一个字段就出一版迁移」。约定：**只放没有
    --    专门列、且当前不需要按字段查询**的键；一旦某键需要治理，再提升为
    --    正式标量列。不加就得接受后续连续的补列迁移。
    ADD COLUMN IF NOT EXISTS extension                jsonb;

-- 读取层拼回 plan 数组时的排序索引 + 状态筛选索引
CREATE INDEX IF NOT EXISTS ix_bid_section_doc_order ON bid_section (bid_document_id, order_no);
CREATE INDEX IF NOT EXISTS ix_bid_section_doc_status ON bid_section (bid_document_id, status);

-- =====================================================================
-- (3) 新增 6 张表（读+写两向取并集，见主文档 §三 + §6.4）
-- =====================================================================

-- ---------------------------------------------------------------------
-- (3.1) parse_bundle —— 解析包汇总（对应 agent_runtime.contracts.ParseBundle）
--   档位：C（本地无法重算的汇总）+ B（submissionStructure 等本地派生结构落这里）
--   来源：projects/<id>/parse/parse-bundle.json（workspace 里只存 parseBundleRef）
--   说明：project_id / workspace_id 用**软引用**（ParseBundle 允许空串，
--         硬 FK 会在空串上失败）；raw_provider_result 保留供应方原始返回，
--         保证「本地解析 / Pi 补属性」两条来源都可回溯。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parse_bundle (
    id                          bigserial PRIMARY KEY,
    parse_bundle_id             text        NOT NULL UNIQUE,
    schema_version              text        NOT NULL DEFAULT '1.0',
    provider                    text        NOT NULL DEFAULT 'unknown',  -- local/pi/workbuddy/dify
    status                      text        NOT NULL DEFAULT 'completed',
    project_id                  text,
    workspace_id                text,
    artifact_path               text,
    template_fragments_artifact text,
    -- 契约里的 createdAt/updatedAt 恒为 now_iso()（不会为空），故用 timestamptz
    created_at                  timestamptz,
    updated_at                  timestamptz,
    -- ---- A 档底料（本地解析产出，与 C 组表同源，此处按包原样快照一份）----
    source_file                 jsonb       NOT NULL DEFAULT '{}'::jsonb,
    document_blocks             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    document_structure          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    project_meta                jsonb       NOT NULL DEFAULT '{}'::jsonb,
    requirements                jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- ---- B 档：本地派生结构（落这里做结果缓存，避免每次重跑 Python/docx）----
    --   评分要素：结构化明细另落 evaluation_item 表，这里保留原样快照
    evaluation_items            jsonb       NOT NULL DEFAULT '[]'::jsonb,
    bid_format_spec             jsonb       NOT NULL DEFAULT '{}'::jsonb,
    --   电子投标文件组成（parse_submission_format_tree 确定性解析）
    submission_structure        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    --   模板片段：结构化明细另落 template_fragment 表，这里保留原样快照
    template_fragments          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    -- ---- C 档：本地无底料，必须存 ----
    raw_outline                 jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- Pi OutlinePlanAgent
    risk_items                  jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- 解析级风险（明细另落 parse_risk）
    parse_review_report         jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- 解析审查（明细另落 parse_review）
    artifacts                   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    warnings                    jsonb       NOT NULL DEFAULT '[]'::jsonb,
    agent_results               jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- 明细另落 agent_result 表
    raw_provider_result         jsonb       NOT NULL DEFAULT '{}'::jsonb    -- 供应方原始返回（审计/回溯用）
);
CREATE INDEX IF NOT EXISTS ix_parse_bundle_project   ON parse_bundle (project_id);
CREATE INDEX IF NOT EXISTS ix_parse_bundle_workspace ON parse_bundle (workspace_id);
CREATE INDEX IF NOT EXISTS ix_parse_bundle_provider  ON parse_bundle (provider, status);

-- ---------------------------------------------------------------------
-- (3.2) evaluation_item —— 评分要素（eval_parser.parse_eval_items 产出）
--   档位：B（本地纯解析、确定性，落表避免每次重算）
--   契约：{"factor","item","score","criteria"}（eval_parser.py:13）
--   score 允许 None（「评分说明」类条目），故 numeric 可空；
--   item_payload 兜底保存原始条目（解析器将来加键不必再出迁移）。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS evaluation_item (
    id                bigserial PRIMARY KEY,
    parse_bundle_id   text        NOT NULL REFERENCES parse_bundle(parse_bundle_id) ON DELETE CASCADE,
    factor            text,
    item              text,
    score             numeric,
    criteria          text,
    raw_text          text,       -- 该评分点的原文行（无则空）
    evaluation_method text,       -- 评分方法（与 parse_bundle 里的 method 一致，便于单表查询）
    order_no          integer     NOT NULL DEFAULT 0,
    item_payload      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    -- 解析器不产出评分点 ID，用「包 + 序号」做幂等键（可重复导入）
    UNIQUE (parse_bundle_id, order_no)
);
CREATE INDEX IF NOT EXISTS ix_evaluation_item_bundle ON evaluation_item (parse_bundle_id, order_no);

-- ---------------------------------------------------------------------
-- (3.3) template_fragment —— 模板片段（template_fragments.py 产出）
--   档位：B（从 document_* + 原始 docx 现算，落表做结果缓存）
--   blocks / locked_blocks 是嵌套结构（可填字段 + 锁定块），整列透出。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS template_fragment (
    id              bigserial PRIMARY KEY,
    parse_bundle_id text        NOT NULL REFERENCES parse_bundle(parse_bundle_id) ON DELETE CASCADE,
    template_id     text        NOT NULL,
    title           text,
    kind            text,
    source_file_id  text,
    source_locator  text,
    index_order     integer     NOT NULL DEFAULT 0,
    block_count     integer,
    locked_block_count integer,
    blocks          jsonb       NOT NULL DEFAULT '[]'::jsonb,
    locked_blocks   jsonb       NOT NULL DEFAULT '[]'::jsonb,
    fragment_payload jsonb      NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    -- templateId 在一个包内唯一（attach_template_fragments_to_bundle 按它建索引）
    UNIQUE (parse_bundle_id, template_id)
);
CREATE INDEX IF NOT EXISTS ix_template_fragment_bundle ON template_fragment (parse_bundle_id, index_order);

-- ---------------------------------------------------------------------
-- (3.4) parse_risk —— 解析级风险（ParseBundle.riskItems）
--   档位：C（Pi 侧产出，本地无法重算）
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parse_risk (
    id              bigserial PRIMARY KEY,
    parse_bundle_id text        NOT NULL REFERENCES parse_bundle(parse_bundle_id) ON DELETE CASCADE,
    level           text,
    title           text,
    detail          text,
    source_locator  text,
    order_no        integer     NOT NULL DEFAULT 0,
    risk_payload    jsonb       NOT NULL DEFAULT '{}'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_parse_risk_bundle ON parse_risk (parse_bundle_id, level);

-- ---------------------------------------------------------------------
-- (3.5) parse_review —— 解析审查报告（ParseBundle.parseReviewReport）
--   档位：C。一个解析包一份报告（UNIQUE parse_bundle_id）。
--   summary 存整份报告（含 status / warnings / 各分项检查结论）。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS parse_review (
    id              bigserial PRIMARY KEY,
    parse_bundle_id text        NOT NULL REFERENCES parse_bundle(parse_bundle_id) ON DELETE CASCADE,
    status          text,       -- completed / not_run / failed …
    summary         jsonb       NOT NULL DEFAULT '{}'::jsonb,
    warnings        jsonb       NOT NULL DEFAULT '[]'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (parse_bundle_id)
);

-- ---------------------------------------------------------------------
-- (3.6) agent_result —— 智能体运行记录（AgentResultEnvelope, contracts.py:25）
--   档位：C。粒度是「一次智能体运行」，与 llm_call_log 的「一次 LLM 调用」
--   不同（一次运行可能含多次调用），故**独立建表、不并入 llm_call_log**。
--   finishedAt 允许为空串（status=success/failed/skipped 之外），写入时请转 NULL。
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_result (
    id            bigserial PRIMARY KEY,
    run_id        text        NOT NULL UNIQUE,
    agent_id      text        NOT NULL,
    provider      text        NOT NULL DEFAULT 'unknown',
    model         text,
    status        text        NOT NULL DEFAULT 'success',
    input_refs    text[]      NOT NULL DEFAULT '{}',
    output        jsonb       NOT NULL DEFAULT '{}'::jsonb,   -- 各智能体的业务结果（结构各异）
    source_refs   jsonb       NOT NULL DEFAULT '[]'::jsonb,   -- 防幻觉溯源引用
    warnings      text[]      NOT NULL DEFAULT '{}',
    confidence    double precision,
    created_at    timestamptz,
    finished_at   timestamptz,
    -- 归属（可为空：解析级运行属于解析包，撰写级运行属于 workspace/章节）
    parse_bundle_id text,
    workspace_id  text,
    project_id    text,
    -- 入库时间（与契约时间区分）
    inserted_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_agent_result_confidence CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1))
);
CREATE INDEX IF NOT EXISTS ix_agent_result_agent     ON agent_result (agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_agent_result_bundle    ON agent_result (parse_bundle_id);
CREATE INDEX IF NOT EXISTS ix_agent_result_workspace ON agent_result (workspace_id);

COMMIT;

-- =====================================================================
-- 备注
-- 1) **不单独建表**的两个实体（主文档 §8.3 的明确结论）：
--      compliance_review      → bid_document.compliance_review jsonb
--      section_edit_history   → bid_section.edit_history jsonb
-- 2) **故意不落的派生字段**（可由读取层从既有表拼回，见 007 视图）：
--      fineReview 侧的 matchedObjectIds / evidenceItems / matchedMaterialIds /
--      evidenceIds                                        → match_item_fact / match_item_evidence
--      bidDocument.parameterMatches / qualificationMatches → match_item.technical_alignment 现算
--      bidDocument.plan[].content                          → bid_section.content（本文件的结构决策）
-- 3) **章节对象字段还在长**：本次已加 bid_section.extension jsonb 兜底；
--    约定只放「无专门列且不需按字段查询」的键，需要治理时再提升为标量列。
-- 4) 本文件可重复执行。schema 变更请新增 008_*.sql，不要原地改历史迁移。
-- =====================================================================
