-- =====================================================================
-- 招投标智能体 · 迁移 007：重建 H 组 4 个视图 + 新增 1 个参数级偏离视图
-- 目标库：PostgreSQL 14+（开发环境 postgres:16）
--
-- 背景：主文档 §四 的核对结论——4 个视图都不能直接替换 JSON 时代的
--   读取产物，缺口全部可在 SQL 层重建（源数据已在 match_item.technical_alignment
--   jsonb、gaps jsonb、match_item_evidence（name/status/locator/excerpt）里，
--   这两列是 004 迁移专门补的），因此**不需要改表**。
--
-- 本文件重建 4 个视图并新增 1 个：
--   v_match_item_full           补 evidence / evidenceIds / matchedMaterialIds
--   v_deviation_row             补 category（table_key）+ evidence / evidencePath /
--                               requirementFull / requirementNumber / productName /
--                               matchedFacts —— 结构性的（没它无法还原
--                               deviationTables.{technical,commercial} 二分）
--   v_parameter_match           投影 technical_alignment → matchedParameters /
--                               coverage / coveredPoints / gaps；join 证据表 →
--                               evidenceItems；补 requirementFull
--   v_qualification_match       join 证据表 → evidenceItems；域过滤补 eligibility
--                               + targetModule/contentType/matchDomain 别名
--   v_parameter_deviation_row   参数级技术规格偏离表（比 v_deviation_row 多
--                               matchedParameter 列；evidence 语义 = 证明材料
--                               路径换行拼接，对齐 param_match.parameter_deviation_row）
--
-- ---------------------------------------------------------------------
-- 关键对齐点（与 Python 端逐字一致）
-- ---------------------------------------------------------------------
-- 1) evidence 摘要串 = document_matching._evidence_summary（document_matching.py:66）：
--      name（status；locator） 用「；」连接；status 空则「状态待核验」；locator 空则
--      不拼「；locator」；整表空则回退「企业资料库未找到可直接关联的证据」。
--      其中 status 的来源 = verificationStatus or status（contracts.py:640），
--      locator 的来源 = sourceLocator or locator（contracts.py:670）。
--      故 SQL 里：status 取 COALESCE(verification_status, status)，locator 取
--      COALESCE(source_locator, locator)。
-- 2) evidenceItems 精简投影 = param_match._evidence_items / qualification_match._evidence_items：
--      {id=evidence_id, name, status=verification_status|status,
--       sourceLocator=source_locator|locator, excerpt}；id 与 name 都空的行跳过。
-- 3) requirement 逐条参数点（param_match.build_parameter_match:235）：
--      parameters → normalizedText → text → title → requirement → rawText；
--      requirementFull = requirement → rawText（:244）。
-- 4) 偏离表分组（schemas.requirement_domain + deviation_table_for）：
--      domain = 首个非空的 lower(category, target_module, match_domain)，兜底 'other'；
--      technical 进技术表，**其余一律进商务表**。
--
-- 幂等：CREATE OR REPLACE VIEW。可连跑两遍。
--   ★ 注意：CREATE OR REPLACE VIEW **不允许改动已有列的名字与顺序**，
--     只允许在末尾追加新列。因此本文件里每个视图都**先按 001 版的原顺序
--     输出原有列，再在新列追加到末尾**（这样无需 DROP VIEW，也不打断
--     依赖方的列序契约）。
-- =====================================================================

BEGIN;

-- =====================================================================
-- H1 v_match_item_full —— 拼回 fineReview.rows 的原始 JSON 结构
--   在 001 版基础上末尾追加 3 个此前声明「由读取层拼回」但还没实现的字段：
--     evidence（证据摘要串，_evidence_summary 逐字）
--     evidenceIds（source_type='evidence' 的证据 ID）
--     matchedMaterialIds（match_item_fact.fact_snapshot.materialIds）
-- =====================================================================
CREATE OR REPLACE VIEW v_match_item_full AS
SELECT
    i.biz_id                                            AS "requirementId",
    i.biz_id                                            AS "id",
    i.assessment_id,
    i.document_id,
    i.target_module                                     AS "targetModule",
    i.target_module_label                               AS "targetModuleLabel",
    i.category,
    i.requirement,
    i.raw_text                                          AS "rawText",
    i.normalized_requirement                            AS "normalizedRequirement",
    i.title,
    i.text,
    i.mandatory,
    i.is_rejection                                      AS "isRejection",
    i.importance,
    i.confidence,
    i.handling_mode                                     AS "handlingMode",
    i.source_segment_id                                 AS "sourceSegmentId",
    i.source_segment_ids                                AS "sourceSegmentIds",
    i.source_locator                                    AS "sourceLocator",
    i.section_id                                        AS "sectionId",
    i.section_path                                      AS "sectionPath",
    i.table_ids                                         AS "tableIds",
    i.product_name                                      AS "productName",
    i.target_type                                       AS "targetType",
    i.source_column                                     AS "sourceColumn",
    i.quantity,
    i.unit,
    i.parameters,
    i.requirement_number                                AS "requirementNumber",
    i.clause_index                                      AS "clauseIndex",
    i.clause_count                                      AS "clauseCount",
    i.match_domain                                      AS "matchDomain",
    i.primary_route                                     AS "primaryRoute",
    i.requirement_class                                 AS "requirementClass",
    i.response_type                                     AS "responseType",
    i.evidence_need                                     AS "evidenceNeed",
    i.decision_impact                                   AS "decisionImpact",
    i.granularity,
    i.layer,
    i.source_stage                                      AS "sourceStage",
    i.source_file_id                                    AS "sourceFileId",
    i.comparison_id                                     AS "comparisonId",
    i.review_required                                   AS "reviewRequired",
    i.review_mode                                       AS "reviewMode",
    i.review_reason                                     AS "reviewReason",
    i.status,
    i.reason,
    i.suggestion,
    i.status_source                                     AS "statusSource",
    i.risk,
    i.risk_level                                        AS "riskLevel",
    i.review_status                                     AS "reviewStatus",
    i.evidence_coverage_status                          AS "evidenceCoverageStatus",
    COALESCE(f.ids, '{}'::text[])                       AS "matchedObjectIds",
    COALESCE(e.items, '[]'::jsonb)                      AS "evidenceItems",
    -- ↓↓↓ 007 新增（追加在末尾）↓↓↓
    -- 行级证据摘要串（_evidence_summary 逐字）：空证据回退固定话术
    COALESCE(e.evidence, '企业资料库未找到可直接关联的证据') AS "evidence",
    -- 直连证据 ID（source_type='evidence' 的证据条目）
    COALESCE(e.evidence_ids, '{}'::text[])              AS "evidenceIds",
    -- 命中事实快照里的材料 ID（004 注释声明的重建来源）
    COALESCE(m.material_ids, '{}'::text[])              AS "matchedMaterialIds"
FROM match_item i
LEFT JOIN LATERAL (
    SELECT array_agg(f2.fact_biz_id) AS ids
    FROM match_item_fact f2 WHERE f2.match_item_id = i.id
) f ON true
LEFT JOIN LATERAL (
    SELECT
        jsonb_agg(jsonb_build_object(
            'id',            e2.evidence_id,
            'name',          e2.name,
            'status',        e2.status,
            'level',         e2.level,
            'validFrom',     e2.valid_from,
            'validUntil',    e2.valid_until,
            'sourceLocator', e2.source_locator,
            'sourceType',    e2.source_type,
            'excerpt',       e2.excerpt
        ) ORDER BY e2.order_no NULLS LAST, e2.id) AS items,
        -- 证据摘要串：name（status；locator），「；」连接（对齐 _evidence_summary）
        string_agg(
            COALESCE(e2.name, '')
            || '（'
            || COALESCE(NULLIF(e2.verification_status, ''), NULLIF(e2.status, ''), '状态待核验')
            || CASE
                 WHEN COALESCE(NULLIF(e2.source_locator, ''), NULLIF(e2.locator, '')) IS NOT NULL
                 THEN '；' || COALESCE(NULLIF(e2.source_locator, ''), NULLIF(e2.locator, ''))
                 ELSE ''
               END
            || '）',
            '；' ORDER BY e2.order_no NULLS LAST, e2.id
        ) AS evidence,
        array_agg(DISTINCT e2.evidence_id)
            FILTER (WHERE e2.evidence_id IS NOT NULL AND e2.source_type = 'evidence')
            AS evidence_ids
    FROM match_item_evidence e2 WHERE e2.match_item_id = i.id
) e ON true
LEFT JOIN LATERAL (
    SELECT array_agg(DISTINCT mx.material_id) FILTER (WHERE mx.material_id IS NOT NULL) AS material_ids
    FROM match_item_fact f2
    CROSS JOIN LATERAL jsonb_array_elements_text(
        COALESCE(f2.fact_snapshot -> 'materialIds', '[]'::jsonb)
    ) AS mx(material_id)
    WHERE f2.match_item_id = i.id
) m ON true;

-- =====================================================================
-- H2 v_parameter_match —— 技术参数匹配投影（对齐 param_match.build_parameter_match）
--   末尾追加：requirementFull、matchedParameters（technical_alignment.matchedUnits
--   三层嵌套整列透出）、coverage、coveredPoints、gaps、evidenceItems；
--   原有 requirement 语义改为「逐条参数点」回退链（与 param_match 一致）。
-- =====================================================================
CREATE OR REPLACE VIEW v_parameter_match AS
SELECT
    i.biz_id             AS "requirementId",
    -- 逐条参数点正文（param_match.build_parameter_match:235 的回退链）
    COALESCE(
        NULLIF(i.parameters, ''), NULLIF(i.normalized_text, ''), NULLIF(i.text, ''),
        NULLIF(i.title, ''), NULLIF(i.requirement, ''), NULLIF(i.raw_text, '')
    )                    AS "requirement",
    -- 域键（param_match._row_category = category or target_module）
    COALESCE(i.category, i.target_module, 'other') AS "category",
    i.status,
    CASE i.status
        WHEN '满足'     THEN '无偏离'
        WHEN '部分满足' THEN '负偏离'
        WHEN '不满足'   THEN '负偏离'
        WHEN '待补材料' THEN '负偏离'
        WHEN '待澄清'   THEN '待人工确认'
        ELSE '不适用'
    END                  AS deviation,
    i.reason,
    COALESCE(i.confidence, 0.0) AS confidence,
    i.source_locator     AS "sourceLocator",
    i.source_segment_ids AS "sourceSegmentIds",
    -- ↓↓↓ 007 新增（追加在末尾）↓↓↓
    -- 整行原文（param_match 的 requirementFull = requirement or rawText）
    COALESCE(NULLIF(i.requirement, ''), NULLIF(i.raw_text, '')) AS "requirementFull",
    -- 三层嵌套（units→details）整列透出，不拆标量列（拆了会丢层级）
    COALESCE(i.technical_alignment -> 'matchedUnits', '[]'::jsonb) AS "matchedParameters",
    -- 覆盖度（0..1；非数值回退 0）
    CASE
        WHEN i.technical_alignment ->> 'coverage' ~ '^[0-9]+([.][0-9]+)?$'
        THEN (i.technical_alignment ->> 'coverage')::double precision
        ELSE 0.0
    END                  AS coverage,
    COALESCE(i.technical_alignment -> 'coveredPoints', '[]'::jsonb) AS "coveredPoints",
    COALESCE(i.technical_alignment -> 'gaps', i.gaps, '[]'::jsonb)  AS "gaps",
    COALESCE(e.items, '[]'::jsonb) AS "evidenceItems"
FROM match_item i
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
        'id',            e2.evidence_id,
        'name',          e2.name,
        'status',        COALESCE(e2.verification_status, e2.status),
        'sourceLocator', COALESCE(e2.source_locator, e2.locator),
        'excerpt',       e2.excerpt
    ) ORDER BY e2.order_no NULLS LAST, e2.id) AS items
    FROM match_item_evidence e2
    WHERE e2.match_item_id = i.id
      AND NOT (COALESCE(e2.evidence_id, '') = '' AND COALESCE(e2.name, '') = '')
) e ON true
WHERE i.category IN ('technical', 'service_plan')
   OR i.target_module = 'technical'
   OR i.match_domain = 'technical'
   OR i.technical_alignment IS NOT NULL;

-- =====================================================================
-- H3 v_qualification_match —— 资格域匹配投影（对齐 qualification_match）
--   末尾追加 evidenceItems；域过滤补 eligibility + targetModule/contentType/matchDomain
--   别名（原 001 版只按 category 六值过滤 → eligibility 整批丢失）。
--   _is_qualification_row（qualification_match.py:57）：
--     target = targetModule or domain；category_val = category or contentType or matchDomain
--     target=='qualification' 或 category_val=='qualification'
--     或 category_val ∈ {qualification,performance,personnel,financial,credit,region,eligibility}
--   match_item 没有 contentType/domain 列：category 列即 contentType 的落点，
--   match_domain 列即 matchDomain 的落点。
-- =====================================================================
CREATE OR REPLACE VIEW v_qualification_match AS
SELECT
    i.biz_id              AS "requirementId",
    i.requirement,
    COALESCE(i.category, i.target_module, 'other') AS "category",
    i.status,
    i.reason,
    COALESCE(f.ids, '{}'::text[]) AS "matchedObjectIds",
    i.source_locator      AS "sourceLocator",
    -- ↓↓↓ 007 新增（追加在末尾）↓↓↓
    COALESCE(e.items, '[]'::jsonb) AS "evidenceItems"
FROM match_item i
LEFT JOIN LATERAL (
    SELECT array_agg(f2.fact_biz_id) AS ids
    FROM match_item_fact f2 WHERE f2.match_item_id = i.id
) f ON true
LEFT JOIN LATERAL (
    SELECT jsonb_agg(jsonb_build_object(
        'id',            e2.evidence_id,
        'name',          e2.name,
        'status',        COALESCE(e2.verification_status, e2.status),
        'sourceLocator', COALESCE(e2.source_locator, e2.locator),
        'excerpt',       e2.excerpt
    ) ORDER BY e2.order_no NULLS LAST, e2.id) AS items
    FROM match_item_evidence e2
    WHERE e2.match_item_id = i.id
      AND NOT (COALESCE(e2.evidence_id, '') = '' AND COALESCE(e2.name, '') = '')
) e ON true
WHERE lower(COALESCE(i.target_module, '')) = 'qualification'
   OR lower(COALESCE(i.category, i.match_domain, '')) IN (
        'qualification','performance','personnel','financial','credit','region','eligibility'
      );

-- =====================================================================
-- H4 v_deviation_row —— 整条对标偏离表（偏离类型映射的唯一来源，禁止另写一套）
--   结构性问题修复：末尾追加 category + table_key，让读取层能按它还原
--   deviationTables.{technical,commercial} 二分；再补 enriched 展示字段
--   requirementFull / requirementNumber / productName / evidence / evidencePath /
--   matchedFacts。
--   no 改为按 (assessment_id, table_key) 分区，使每张偏离表内行号 1..N。
-- =====================================================================
CREATE OR REPLACE VIEW v_deviation_row AS
SELECT
    ROW_NUMBER() OVER (
        PARTITION BY i.assessment_id, d.table_key
        ORDER BY i.clause_index NULLS LAST, i.id
    )                    AS no,
    i.biz_id             AS "requirementId",
    i.target_module      AS "targetModule",
    -- 招标要求整行原文（build_deviation_row 的 requirement = requirement or rawText）
    COALESCE(NULLIF(i.requirement, ''), NULLIF(i.raw_text, '')) AS "requirement",
    i.status,
    CASE i.status
        WHEN '满足'     THEN '无偏离'
        WHEN '部分满足' THEN '负偏离'
        WHEN '不满足'   THEN '负偏离'
        WHEN '待补材料' THEN '负偏离'
        WHEN '待澄清'   THEN '待人工确认'
        ELSE '不适用'
    END                  AS deviation,
    i.reason             AS response,
    i.suggestion,
    i.mandatory,
    i.is_rejection       AS "isRejection",
    i.source_locator     AS "sourceLocator",
    -- ↓↓↓ 007 新增（追加在末尾）↓↓↓
    -- 域键（schemas.requirement_domain：category → target_module → match_domain，小写）
    d.domain             AS category,
    -- 分组键：technical → 技术表，其余一律进商务表（schemas.deviation_table_for）
    d.table_key          AS "table_key",
    -- enriched 展示字段：完整原文（与 requirement 同源；参数点级正文见 v_parameter_deviation_row）
    COALESCE(NULLIF(i.requirement, ''), NULLIF(i.raw_text, '')) AS "requirementFull",
    i.requirement_number AS "requirementNumber",
    i.product_name       AS "productName",
    -- 证据引用正文（_evidence_summary 逐字；空证据回退固定话术）
    COALESCE(e.evidence, '企业资料库未找到可直接关联的证据') AS "evidence",
    -- 证据路径（assemble._evidence_cell 优先读 evidencePath）；换行拼接对齐旧产物
    COALESCE(e.evidence_path, '') AS "evidencePath",
    -- 命中事实（factKey/factId/name/evidencePath；由 match_item_fact 反查 A 组事实表）
    COALESCE(f.matched_facts, '[]'::jsonb) AS "matchedFacts"
FROM match_item i
CROSS JOIN LATERAL (
    SELECT
        lower(COALESCE(NULLIF(i.category, ''), NULLIF(i.target_module, ''), NULLIF(i.match_domain, ''), 'other')) AS domain,
        CASE
            WHEN lower(COALESCE(NULLIF(i.category, ''), NULLIF(i.target_module, ''), NULLIF(i.match_domain, ''), 'other')) = 'technical'
            THEN 'technical'
            ELSE 'commercial'
        END AS table_key
) d
LEFT JOIN LATERAL (
    -- 两个聚合的过滤条件不同（摘要串要含全部证据行，路径串只收有路径的行），
    -- 故拆成两个独立子查询，避免 WHERE 把摘要串也一起过滤掉。
    SELECT
        (
            SELECT string_agg(
                       COALESCE(e2.name, '')
                       || '（'
                       || COALESCE(NULLIF(e2.verification_status, ''), NULLIF(e2.status, ''), '状态待核验')
                       || CASE
                            WHEN COALESCE(NULLIF(e2.source_locator, ''), NULLIF(e2.locator, '')) IS NOT NULL
                            THEN '；' || COALESCE(NULLIF(e2.source_locator, ''), NULLIF(e2.locator, ''))
                            ELSE ''
                          END
                       || '）',
                       '；' ORDER BY e2.order_no NULLS LAST, e2.id
                   )
            FROM match_item_evidence e2
            WHERE e2.match_item_id = i.id
        ) AS evidence,
        (
            SELECT string_agg(
                       DISTINCT COALESCE(NULLIF(e2.source_locator, ''), NULLIF(e2.locator, '')),
                       E'\n'
                   )
            FROM match_item_evidence e2
            WHERE e2.match_item_id = i.id
              AND COALESCE(NULLIF(e2.source_locator, ''), NULLIF(e2.locator, '')) IS NOT NULL
        ) AS evidence_path
) e ON true
LEFT JOIN LATERAL (
    SELECT jsonb_agg(
        jsonb_build_object(
            'factKey',      mf.fact_kind,
            'factId',       mf.fact_biz_id,
            'name',         fr.name,
            'evidencePath', fr.evidence_path
        )
        ORDER BY mf.id
    ) AS matched_facts
    FROM match_item_fact mf
    -- A 组 M3 镜像表已移除（见 009_drop_company_mirror_tables.sql）：
    -- 名称/证据路径改从「命中时的事实快照」取，不再 JOIN 镜像表。
    -- 快照是命中时刻的冻结值，既自包含，也不会因 M3 变动而与结论分叉。
    LEFT JOIN LATERAL (
        SELECT
            COALESCE(
                NULLIF(mf.fact_snapshot ->> 'name', ''),
                NULLIF(mf.fact_snapshot ->> 'organization_name', ''),
                NULLIF(mf.fact_snapshot ->> 'qualification_name', ''),
                NULLIF(mf.fact_snapshot ->> 'credential_name', ''),
                NULLIF(mf.fact_snapshot ->> 'case_name', ''),
                NULLIF(mf.fact_snapshot ->> 'product_name', ''),
                NULLIF(mf.fact_snapshot ->> 'evidence_type', ''),
                NULLIF(mf.fact_snapshot ->> 'description', '')
            ) AS name,
            COALESCE(
                NULLIF(mf.fact_snapshot ->> 'evidencePath', ''),
                NULLIF(mf.fact_snapshot ->> 'evidence_path', '')
            ) AS evidence_path
    ) fr ON true
    WHERE mf.match_item_id = i.id
) f ON true;

-- =====================================================================
-- H5 v_parameter_deviation_row —— 参数级技术规格偏离表（新增）
--   对齐 param_match.parameter_deviation_row（param_match.py:280）：
--     多 matchedParameter 列（命中的企业技术参数正文，描述或参数ID，用「；」连接）；
--     evidence 语义 = 证明材料路径**换行拼接**（不是 name（status；locator）摘要）。
--   注意：Python 端的 matchedParameters 扁平化 + 企业台账补全（_fill_from_knowledge）
--   依赖 company_knowledge，SQL 无法完整复刻；本视图只从 technical_alignment 的
--   matchedParameterDetails 取「描述或参数ID」做投影，去重/补全留给读取层。
-- =====================================================================
CREATE OR REPLACE VIEW v_parameter_deviation_row AS
SELECT
    ROW_NUMBER() OVER (
        PARTITION BY i.assessment_id
        ORDER BY i.clause_index NULLS LAST, i.id
    )                    AS no,
    i.biz_id             AS "requirementId",
    -- 逐条参数点正文（同 v_parameter_match 的回退链）
    COALESCE(
        NULLIF(i.parameters, ''), NULLIF(i.normalized_text, ''), NULLIF(i.text, ''),
        NULLIF(i.title, ''), NULLIF(i.requirement, ''), NULLIF(i.raw_text, '')
    )                    AS "requirement",
    i.status,
    CASE i.status
        WHEN '满足'     THEN '无偏离'
        WHEN '部分满足' THEN '负偏离'
        WHEN '不满足'   THEN '负偏离'
        WHEN '待补材料' THEN '负偏离'
        WHEN '待澄清'   THEN '待人工确认'
        ELSE '不适用'
    END                  AS deviation,
    -- 命中的企业技术参数正文（「；」连接，见 param_match.parameter_deviation_row:290）
    COALESCE(mp.matched_parameter, '') AS "matchedParameter",
    -- 证据引用：证明材料路径换行拼接（param_match.parameter_deviation_row:293-299）
    COALESCE(e.evidence_path, '') AS "evidence",
    i.reason             AS "suggestion",
    i.source_locator     AS "sourceLocator"
FROM match_item i
LEFT JOIN LATERAL (
    SELECT string_agg(
        COALESCE(NULLIF(d.detail ->> 'description', ''), NULLIF(d.detail ->> 'parameterId', '')),
        '；' ORDER BY u.u_ord, d.d_ord
    ) AS matched_parameter
    FROM jsonb_array_elements(COALESCE(i.technical_alignment -> 'matchedUnits', '[]'::jsonb)) WITH ORDINALITY AS u(unit, u_ord)
    CROSS JOIN LATERAL jsonb_array_elements(COALESCE(u.unit -> 'matchedParameterDetails', '[]'::jsonb)) WITH ORDINALITY AS d(detail, d_ord)
    WHERE NULLIF(d.detail ->> 'parameterId', '') IS NOT NULL
) mp ON true
LEFT JOIN LATERAL (
    SELECT string_agg(
        DISTINCT COALESCE(NULLIF(e2.source_locator, ''), NULLIF(e2.locator, '')),
        E'\n'
    ) AS evidence_path
    FROM match_item_evidence e2
    WHERE e2.match_item_id = i.id
      AND COALESCE(NULLIF(e2.source_locator, ''), NULLIF(e2.locator, '')) IS NOT NULL
) e ON true
WHERE i.category IN ('technical', 'service_plan')
   OR i.target_module = 'technical'
   OR i.match_domain = 'technical'
   OR i.technical_alignment IS NOT NULL;

COMMIT;

-- =====================================================================
-- 备注
-- 1) evidence 摘要串与 Python _evidence_summary 逐字一致的关键前提：
--    status 取 COALESCE(verification_status, status)（normalize_evidence:640），
--    locator 取 COALESCE(source_locator, locator)（normalize_evidence:670），
--    二者在 001 的 match_item_evidence 里都已落列。
-- 2) matchedParameters 是三层嵌套（units→details），v_parameter_match 整列透出
--    technical_alignment.matchedUnits jsonb，不拆标量列；真正的扁平化 + 企业台账
--    补全（param_match.alignment_matched_parameters / _fill_from_knowledge）依赖
--    company_knowledge，属读取层职责，SQL 不复刻（见 param_match.py:94-190）。
-- 3) v_deviation_row 的 matchedFacts 由 match_item_fact 反查 A 组 8 张事实表拼回
--    {factKey,factId,name,evidencePath}；旧 deviations.json 里的「name（evidencePath）」
--    字符串形状由读取层格式化（本视图提供结构化字段，信息无损）。
-- 4) 本文件可重复执行。schema 变更请新增 008_*.sql，不要原地改历史迁移。
-- =====================================================================
