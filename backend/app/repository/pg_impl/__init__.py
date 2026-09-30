"""目标实现（阶段 3+ 才写，当前占位）。

阶段 3 起在这里实现 PG 版：
- PGFactsRepository      → A 组：M3 拉取后写 PG 只读镜像 + fact_sync_log.sync_batch_id
- PGCatalogRepository    → B 组公告：tender_announcement 等 3 表
- PGWorkspaceRepository  → B+C+D+E：一个 workspace 拆 25 表（事务性写入）
"""
