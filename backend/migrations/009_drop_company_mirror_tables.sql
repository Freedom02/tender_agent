-- =====================================================================
-- 009_drop_company_mirror_tables.sql
--
-- 目的：移除 A 组「企业事实 M3 镜像表」共 10 张。
--
-- 决策依据（2026-09-30）：
--   1. 实测这 10 张表 0 行、没有任何 .py 代码读写；
--      仅 005（COMMENT + meta_table 种子）与 007（v_deviation_row 的一个 LATERAL）引用过它们。
--   2. M3 图库是唯一权威源 —— 001 自己写着「M3 仍是权威源，本组表是只读镜像」，
--      队友的设计也写着「企业本体事实仍以 M3 图库为单一来源，本库不复制」。
--   3. 空的镜像表是陷阱：留着会诱导有人往里写，导致企业事实存在两份并与 M3 分叉。
--   4. 恢复成本极低：DDL 在 git 历史里（本提交之前），需要时一行 migration 就能加回。
--
-- 配套改动（同一次提交）：
--   - 001_schema_v1.sql  : 删除这 10 张表的 DDL（26 张表 + 002 的 12 张 = 38 张）
--   - 005_comments.sql   : 删除 10 行 COMMENT ON TABLE + 10 行 meta_table 种子
--   - 007_bid_views_v2.sql: v_deviation_row 的 matched_facts 不再 JOIN 镜像表，
--                          改从 match_item_fact.fact_snapshot 取名称/证据路径
--                          （快照是命中时刻的冻结值，自包含且不会与结论分叉）
--
-- 幂等性：全部 IF EXISTS / 带 WHERE 的 DELETE，可重复执行。
--
-- 执行后必须重跑 007_bid_views_v2.sql 重建 v_deviation_row。
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 1) 删表。CASCADE 会连带删除依赖它们的视图（当前仅 v_deviation_row）。
-- ---------------------------------------------------------------------
DROP TABLE IF EXISTS evidence_file             CASCADE;
DROP TABLE IF EXISTS technical_parameter       CASCADE;
DROP TABLE IF EXISTS technical_product         CASCADE;
DROP TABLE IF EXISTS financial_evidence        CASCADE;
DROP TABLE IF EXISTS staff_member              CASCADE;
DROP TABLE IF EXISTS performance_case          CASCADE;
DROP TABLE IF EXISTS credential                CASCADE;
DROP TABLE IF EXISTS qualification_certificate CASCADE;
DROP TABLE IF EXISTS organization              CASCADE;
DROP TABLE IF EXISTS fact_sync_log             CASCADE;

-- ---------------------------------------------------------------------
-- 2) 清治理元数据（否则 meta_table/meta_column 会描述不存在的表）
--    注：005 的机制是「扫 schema 自动灌 meta_*」，重跑 005 也会同步这一结果。
-- ---------------------------------------------------------------------
DELETE FROM meta_column
 WHERE table_name IN (
    'fact_sync_log','organization','qualification_certificate','credential',
    'performance_case','staff_member','financial_evidence','technical_product',
    'technical_parameter','evidence_file'
 );

DELETE FROM meta_relationship
 WHERE from_table IN (
    'fact_sync_log','organization','qualification_certificate','credential',
    'performance_case','staff_member','financial_evidence','technical_product',
    'technical_parameter','evidence_file'
 )
    OR to_table IN (
    'fact_sync_log','organization','qualification_certificate','credential',
    'performance_case','staff_member','financial_evidence','technical_product',
    'technical_parameter','evidence_file'
 );

DELETE FROM meta_table
 WHERE table_name IN (
    'fact_sync_log','organization','qualification_certificate','credential',
    'performance_case','staff_member','financial_evidence','technical_product',
    'technical_parameter','evidence_file'
 );

COMMIT;

-- =====================================================================
-- 3) 重建视图（必做）
--    请紧接着执行：007_bid_views_v2.sql
--    它会重建 v_deviation_row / v_match_item_full / v_parameter_match /
--    v_qualification_match / v_parameter_deviation_row。
-- =====================================================================

-- ---------------------------------------------------------------------
-- 4) 自检（可选）：应返回 0 行
-- ---------------------------------------------------------------------
-- SELECT table_name FROM information_schema.tables
--  WHERE table_schema='public'
--    AND table_name IN ('fact_sync_log','organization','qualification_certificate',
--        'credential','performance_case','staff_member','financial_evidence',
--        'technical_product','technical_parameter','evidence_file');
