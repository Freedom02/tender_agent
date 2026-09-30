-- =====================================================================
-- 012_fix_bid_section_payload.sql
--
-- 目的：**修正 011 的 payload 回填**。
--
-- 011 用了 `jsonb_build_object` 把各列拼成 payload，有**两个实质缺陷**：
--
--   缺陷 1（数据丢失）：`c-01` 的 `note` key 丢了。
--     `note` 只存在于 `bid_plan.plan[]`（原始持久化形态），
--     `bid_section` 从来没有 note 列，所以 011 无从拷到它 -> 丢失。
--
--   缺陷 2（凭空造事实）：011 给每条都写进了 `agentResults: []`、
--     `editHistory: []`、`materialScopes: []` 等 18-19 个 key，
--     而这些 key 在真实数据里**根本不存在**。
--     这是"把默认值当事实写"，违反融合方案的核心原则
--     ——payload 是**事实**，必须原样保真，不能注入推测值。
--
-- 正确做法：**payload 从权威源重建**，而不是从列反推。
--   bid_section.payload := bid_plan.plan[] 里对应的那个 section 对象（原样）
--   再把 bid_section 列上**真实有值**的信息补进去（仅当 payload 里没有该 key 时）。
--
-- 权威源的选择依据：
--   `bid_plan.plan[]` 是**原始持久化形态**（6 个元素，key 数 3~5），
--   它就是"事实"；`bid_section` 的列是**投影**。
--   —— 投影可以被重建，事实不能被编造。
--
-- 幂等性：可重复执行（每次都用权威源重算 payload）。
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1) 用权威源（bid_plan.plan[]）重建 payload。
--    匹配键 = sectionId。只有匹配上的才重建（匹配不上的保留原 payload）。
-- ---------------------------------------------------------------------
UPDATE bid_section bs
SET payload = src.section_obj
FROM (
    SELECT
        bp.workspace_id,
        elem->>'sectionId' AS section_id,
        elem               AS section_obj
    FROM bid_plan bp
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE jsonb_typeof(bp.plan)
            WHEN 'array' THEN bp.plan
            WHEN 'object' THEN COALESCE(bp.plan->'plan', '[]'::jsonb)
            ELSE '[]'::jsonb
        END
    ) AS elem
    WHERE elem->>'sectionId' IS NOT NULL
) AS src
WHERE bs.section_id = src.section_id
  AND bs.bid_document_id IN (
      -- 通过 bid_document 关联到同一个工作区，避免跨工作区误匹配
      SELECT bd.biz_id FROM bid_document bd WHERE bd.workspace_id = src.workspace_id
  );

-- ---------------------------------------------------------------------
-- 2) 补回投影列上**真实有值**、但权威源里没有的信息。
--    原则：只在 payload 缺该 key 时补，不覆盖权威源的值。
--    并且**只补非空值** —— 空值不补（否则又变成"凭空造事实"）。
-- ---------------------------------------------------------------------
UPDATE bid_section
SET payload = payload
    || CASE WHEN content IS NOT NULL AND content <> ''
            AND NOT (payload ? 'content')
            THEN jsonb_build_object('content', content) ELSE '{}'::jsonb END
    || CASE WHEN source IS NOT NULL AND source <> ''
            AND NOT (payload ? 'source')
            THEN jsonb_build_object('source', source) ELSE '{}'::jsonb END
    || CASE WHEN status IS NOT NULL AND status <> ''
            AND NOT (payload ? 'status')
            THEN jsonb_build_object('status', status) ELSE '{}'::jsonb END
    || CASE WHEN level IS NOT NULL AND NOT (payload ? 'level')
            THEN jsonb_build_object('level', level) ELSE '{}'::jsonb END
    || CASE WHEN order_no IS NOT NULL AND NOT (payload ? 'order')
            THEN jsonb_build_object('order', order_no) ELSE '{}'::jsonb END
    || CASE WHEN required IS NOT NULL AND NOT (payload ? 'required')
            THEN jsonb_build_object('required', required) ELSE '{}'::jsonb END
    || CASE WHEN requirement_ids IS NOT NULL AND array_length(requirement_ids, 1) IS NOT NULL
            AND NOT (payload ? 'requirementIds')
            THEN jsonb_build_object('requirementIds', to_jsonb(requirement_ids))
            ELSE '{}'::jsonb END;

-- ---------------------------------------------------------------------
-- 3) 兜底：仍为 NULL 或空的（没有任何权威源可依）→ 用 section_id/title 最小集
-- ---------------------------------------------------------------------
UPDATE bid_section
SET payload = jsonb_strip_nulls(jsonb_build_object(
        'sectionId', section_id,
        'title',     title
    )) || payload
WHERE payload IS NULL OR payload = '{}'::jsonb;

COMMIT;
