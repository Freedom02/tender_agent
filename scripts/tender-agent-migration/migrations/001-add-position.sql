-- ============================================================================
-- 001 — 应用接入 PostgreSQL：为有序集合补 position 列
--
-- 背景：Store 在内存里用 Python list/dict 持有有序集合，落盘顺序即文件顺序。
-- 迁到关系表后行序不再有保证，故显式加一列 position 保留原始顺序。
-- 应用侧读取时统一 ORDER BY position NULLS LAST, <主键>；迁移脚本会按 JSON
-- 文件顺序回填 position。
--
-- 幂等，可重复执行。
-- ============================================================================

BEGIN;

ALTER TABLE catalog_projects ADD COLUMN IF NOT EXISTS position INTEGER;
ALTER TABLE workspaces       ADD COLUMN IF NOT EXISTS position INTEGER;
ALTER TABLE libraries        ADD COLUMN IF NOT EXISTS position INTEGER;
ALTER TABLE reminders        ADD COLUMN IF NOT EXISTS position INTEGER;
ALTER TABLE automation_runs  ADD COLUMN IF NOT EXISTS position INTEGER;

COMMIT;
