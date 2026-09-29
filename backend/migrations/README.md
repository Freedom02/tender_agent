# 数据库迁移目录

本目录存放 PostgreSQL 的建表/变更脚本，按文件名顺序执行（`001_*` → `002_*` → …）。

## 约定

1. **只增不改**：已合入的迁移文件不要原地修改；新变更新建 `NNN_描述.sql`。
2. **幂等**：脚本必须能重复执行（`CREATE TABLE IF NOT EXISTS` / `CREATE OR REPLACE VIEW` / `CREATE INDEX IF NOT EXISTS`）。
3. **可回滚**：破坏性变更（DROP/ALTER COLUMN）单独成文件，并在文件头写清回滚方式。
4. **枚举用 `text + CHECK`**，不用 PG 的 `enum` 类型。

## 如何执行

本地开发（一键，含幂等性验收）：

```powershell
# 项目根目录（含 docker-compose.yml 的那层）
pwsh -File scripts/db_init.ps1
```

等价的手工步骤：

```powershell
docker compose up -d
docker cp backend/migrations/001_schema_v1.sql tenderagent-pg:/tmp/001_schema_v1.sql
docker exec tenderagent-pg psql -U tender_app -d tender_agent -v ON_ERROR_STOP=1 -f /tmp/001_schema_v1.sql
```

连接串（本机开发）：

```
postgresql://tender_app:tender_app_dev@127.0.0.1:5433/tender_agent
```

## 当前迁移

| 文件 | 内容 | 对应设计 |
|---|---|---|
| `001_schema_v1.sql` | 全量 30 表 + 4 视图 + 索引（A 企业事实 / B 招标文件 / C 解析 / D 提取 / E 匹配 / F 运行 / G 标书 / H 派生视图） | `docs2/匹配数据持久化设计（PostgreSQL）.md` §4 |

> 生产环境的连接信息按 `docs2/匹配数据持久化设计（PostgreSQL）.md` §8 向公司申请；`docker-compose.yml` 里的口令仅限本机开发。
