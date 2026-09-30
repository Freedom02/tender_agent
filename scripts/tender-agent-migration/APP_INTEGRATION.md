# 应用接入 PostgreSQL（存储层改造）

目标：让后端运行时数据以 PostgreSQL 为**唯一事实源**，同时保留随时回滚到 JSON 的能力。

## 一、改了什么

| 文件 | 改动 |
|---|---|
| `backend/app/storage/`（**新增**） | 存储适配层：`base.py` 抽象、`json_backend.py`、`postgres_backend.py`、`dual_backend.py`、`__init__.py` 工厂 |
| `backend/app/store.py` | **只改读写边界**约 10 处，业务逻辑一行未动 |
| `backend/app/cloud_agent/run_repository.py` | 新增 `PostgresRunRepository` / `DualRunRepository` / `create_run_repository` |
| `backend/app/cloud_agent/run_service.py` | 仓库改为按开关创建（2 行） |
| `backend/requirements.txt` | 加 `psycopg[binary]` |
| 项目根 `.env` | 加 `STORE_BACKEND`、`PG_DSN` |

核心思路：`Store` 是「全量载入内存 → 业务逻辑改内存 → 整体落盘」，所以只要替换
**载入**（`initialize`）和**落盘**（`persist_*`）这两个边界，4000 行业务逻辑完全不用碰。

## 二、三种模式

由环境变量 `STORE_BACKEND` 切换（改完重启后端生效）：

| 值 | 读 | 写 | 用途 |
|---|---|---|---|
| `json` | JSON 文件 | JSON 文件 | 现状，回滚就切回这里 |
| `dual` | JSON 文件 | **JSON + DB 双写** | 过渡期；DB 写失败只记 warning，不影响主流程 |
| `db` | DB | DB | 数据库成为唯一事实源 |

`PG_DSN` 形如 `postgresql://tender_app:密码@127.0.0.1:5432/tender_agent_dev`。

## 三、范围界定

**进库（结构化业务状态，7 类）**

| 内存属性 | 原文件 | 表 |
|---|---|---|
| `catalog` | catalog.json | `catalog_projects` |
| `libraries` | libraries.json | `libraries` + `library_documents` |
| `workspaces` | workspaces.json | `workspaces` + 3 张派生明细表 |
| `deleted_workspaces` | workspaces-deleted.json | `workspaces.deleted_at` |
| `reminders` | reminders.json | `reminders` |
| `runs` | automation-runs.json | `automation_runs` |
| （`RunRepository`） | bid_runs.json | `bid_runs` |

**仍留盘（不改）**

- `agent-config.json`：含 AI API Key 明文，按决定留在磁盘，不进库。
- 解析包 `parse-bundle.json`、模板片段、`tender_pack.json`：文件产物，被 `storagePath`
  引用并由 `/runtime/{path}` 静态路由提供，仍以磁盘为准。
- 证明材料、资料库文件、上传原件、投标输出、导出包：二进制文件，始终留盘。

> 因此 `parse_bundles` / `parse_artifacts` / `evidence_files` / `asset_files` 四张表里的数据
> 是**迁移时刻的副本**，应用运行期间不会自动更新。它们是给查询/BI 用的，不是事实源。

**顺序保真**：有序集合（catalog / workspaces / libraries / reminders / automation_runs）
新增 `position` 列保存原始顺序，读取时统一 `ORDER BY position NULLS LAST, <主键>`。

## 四、启用步骤

```powershell
# 0) 确保共享库在跑
cd C:\Users\乔远\Desktop\tender_agent\scripts\tender-agent-migration\shared-db
docker-compose ps

# 1) 补 position 列（幂等）
docker-compose exec postgres psql -U tender_app -d tender_agent_dev -c "ALTER TABLE catalog_projects ADD COLUMN IF NOT EXISTS position INTEGER; ALTER TABLE workspaces ADD COLUMN IF NOT EXISTS position INTEGER; ALTER TABLE libraries ADD COLUMN IF NOT EXISTS position INTEGER; ALTER TABLE reminders ADD COLUMN IF NOT EXISTS position INTEGER; ALTER TABLE automation_runs ADD COLUMN IF NOT EXISTS position INTEGER;"

# 2) 重跑迁移，按 JSON 顺序回填 position（幂等）
cd ..
py migrate_to_db.py --dsn "postgresql://tender_app:密码@127.0.0.1:5432/tender_agent_dev"
```

然后确认 `.env` 里是 `STORE_BACKEND=dual`，**重启后端**。

## 五、验证（dual 模式）

后端跑起来后做几个操作，再查库看是否同步：

```powershell
cd C:\Users\乔远\Desktop\tender_agent\scripts\tender-agent-migration\shared-db

# 后端日志里应无 "dual 模式：... 写 DB 失败"
# 每做一次操作（标记已读 / 建提醒 / 存工作区），查一次下面两行：
docker-compose exec postgres psql -U tender_app -d tender_agent_dev -c "SELECT count(*), max(updated_at) FROM catalog_projects;"
docker-compose exec postgres psql -U tender_app -d tender_agent_dev -c "SELECT count(*) FROM reminders;"

# 抽查 position 是否已回填（应全为 NOT NULL）
docker-compose exec postgres psql -U tender_app -d tender_agent_dev -c "SELECT count(*) FILTER (WHERE position IS NULL) AS no_pos, count(*) FROM catalog_projects;"
```

`json` 与 `dual` 两种模式下，**应用行为应完全一致**（读的都是 JSON）。所以此时如果
功能有异常，一定不是 DB 引起的，可以直接切回 `json` 排除。

## 六、切到 db 模式

`dual` 跑几天、确认库里的数据与 JSON 一致后：

1. 停后端；
2. `.env` 改 `STORE_BACKEND=db`；
3. 重启后端；
4. 检查首屏（公告列表、工作台、资料库、提醒）是否与切之前一致。

**回滚**：把 `STORE_BACKEND` 改回 `json` 重启即可——JSON 文件从头到尾没被删过，
`dual`/`db` 模式只是"多写了一份"，不是"搬家"。

## 七、已知限制

1. **提醒只保留最近 100 条、自动化运行保留最近 50 条**——沿用原有 JSON 行为
   （`persist_runtime` 里的切片），不是本次引入的。若想让库成为完整历史，需要改这两处切片。
2. **解析包/解析产物仍以磁盘为准**，库中为迁移副本（见第三节）。
3. **多进程写**：现在按"单写者"假设设计，未加 `pg_advisory_lock`。多个后端进程同时
   写同一批行时，后写覆盖先写（与 JSON 时代的文件锁语义不同）。单进程部署无需关心。
4. **`config` 不进库**，仍在 `agent-config.json`。
5. **`scheduler-state.json` 无消费方**（后台代码从不读它），迁移存了但接入未处理，可忽略。

## 八、时间戳列的语义（2026-09-30 修复）

### 8.1 问题

`Store` 是"整体保存"模型，每次落盘都会把**全表**重写一遍。早期实现给
`workspaces.created_at / updated_at`、`catalog_projects.updated_at` 直接填 `now()`，
于是这些列退化成「**最后一次整体保存的时刻**」——全表所有行的值几乎完全相同
（实测 9 行工作区全是 `2026-09-30 09:51:49`，而其中 `workspace-601ded72ff` 对应的
项目目录实际创建于 `09-29 15:00:49`，**记错近 19 小时**），无法回答"这条什么时候建的/改的"。

根因是**源 JSON 里根本没有这些字段**（工作区的键只有
`draft / draftVersion / id / project / projectId / reasoning / title / workspaceId`），
数据库再怎么填都无从得知真实时间。

### 8.2 修复：两层一起做

**第一层 —— 存储层不再篡改**（`storage/postgres_backend.py`）

新增 `_conflict_sql(...)`，给 upsert 增加两种冲突语义：

| 参数 | 生成的子句 | 用在哪 |
|---|---|---|
| `coalesce_cols` | `c = COALESCE(EXCLUDED.c, <表>.c)` | `workspaces.created_at` |
| `touch_col` | `c = CASE WHEN <表>.payload IS DISTINCT FROM EXCLUDED.payload THEN EXCLUDED.c ELSE <表>.c END` | `workspaces.updated_at`、`catalog_projects.updated_at` |

- `save_workspaces` → `coalesce_cols=("created_at",), touch_col="updated_at"`
- `save_catalog` → `touch_col="updated_at"`
- `save_deleted_workspaces` → `deleted_at = COALESCE(<表>.deleted_at, EXCLUDED.deleted_at)`，
  墓碑时间不再被反复刷新；「恢复→再删除」仍能得到新的删除时间（恢复时 `deleted_at` 被置空）。

`coalesce_cols` 的语义是"**有真值就用、没有就保留**"，不是"永远保留"——
这样既能挡住每次保存的 `NULL` 冲刷，又允许将来补上的真值生效。

**第二层 —— 源头把时间记下来**（真正解决问题的一层）

光靠存储层猜不出来，所以让**应用在生成源数据时就盖章**。工作区只有两个创建入口，
两边都拿得到 `previous`，可以沿用原值：

| 入口 | 文件 |
|---|---|
| `match_project`（从公告匹配创建） | `backend/app/store.py` |
| `build_workspace`（云端解析物化创建） | `backend/app/cloud_agent/materializer.py` |

盖章规则：

- **首次创建** → `createdAt = now()`
- **重复物化/重新分析**（`previous` 存在且有 `createdAt`）→ 沿用原值
- **接入前的老数据**（`previous` 里没有）→ **一律不写**，交给存储层保留库里的原值，
  绝不伪造一个「现在」

同时 `_workspace_rows` 的 `created_at` 改为**只认源数据的 `createdAt`**，不再有
`or now()` 回退——没有就是 `NULL`。

### 8.3 修复后的列语义

| 列 | 来源 | 含义 |
|---|---|---|
| `workspaces.created_at` | 源 JSON `createdAt` | 工作区的**真实创建时间**（2026-10 之后新建的） |
| `workspaces.updated_at` | 存储层推导 | 该工作区 payload **最后一次真正变化**的时间 |
| `catalog_projects.updated_at` | 存储层推导 | 该公告 payload 最后一次真正变化的时间 |
| `catalog_projects.fetched_at` | 源 JSON `fetchedAt` | 抓取时间，**一直是真值** |
| `reminders.created_at` | 源 JSON `createdAt` | 提醒创建时间，**一直是真值** |
| `bid_runs.created_at / updated_at` | 源 JSON | **一直是真值** |
| `workspaces.deleted_at` | 首次墓碑时刻 | 删除时间（修复前为最后一次保存时刻） |

### 8.4 历史数据处理：不猜，留 NULL

修复前入库的 **6 个存活工作区**，源数据生成时还没有盖章机制，拿不到真值。
排查后手上只有这些证据：

| workspace | 硬证据（项目目录创建时间） | 弱证据（提醒时间，仅上界） |
|---|---|---|
| `workspace-601ded72ff` | 09-29 15:00:49 | — |
| `workspace-811d2e5052` | 09-30 09:50:59 | — |
| `workspace-be3abf689c` | 无 | 09-28 01:03:16（下界 01:02:16） |
| `workspace-978552f9b7` | 无 | 09-28 01:03:14（下界 01:02:16） |
| `workspace-1614248391` | 无 | 09-28 01:03:11（下界 01:02:16） |
| `workspace-cb10005561` | 无 | 09-28 13:07:04（下界 13:06:29） |

**决策：全部置为 `NULL`，表示「未知」。** 理由是不把推断值写进一个之后无法与真值
区分的字段（`remindedAt` 虽然误差 <1 分钟，但它终究是提醒时间，不是创建时间）。
宁可承认不知道，也不要一个看起来合理的错值。

`created_at IS NULL` 的含义就是：**该工作区创建于 2026-09-30 引入 `createdAt` 之前，时间未知。**

### 8.5 验证记录

1. **冲突语义单元测试**（临时表）：无变更保存后 `created_at`/`updated_at` 均不变；
   payload 变化后只有 `updated_at` 更新、`created_at` 保持；墓碑重复写不刷新、
   恢复后再删除能拿到新时间。
2. **真实数据空跑**：从库读出原样写回，workspaces 9 行 / catalog 93 行 / 墓碑 3 个
   **全部时间戳零变化**。
3. **NULL 耐久性**：置 NULL 后连续两次原样保存，9/9 行仍为 NULL——不会被刷回来。
4. **COALESCE 双向验证**：源数据无 `createdAt` → NULL；后来补上真值 → 写入生效；
   再原样保存 → 真值保留未被 NULL 覆盖。
5. **盖章规则测试**：`build_workspace` 首次创建盖当前时间、重复物化沿用原值、
   老数据返回 `None`（不伪造）。
6. **回归**：`_replace_table` 的"删除不在集合中的行"语义未受影响；7 类实体载入往返
   计数一致（libraries 5 / reminders 91 / catalog 93 / workspaces 6 / automation_runs 0，
   派生表 sections 18 / fine_review_rows 286）。

### 8.6 ⚠️ 生效条件

本次改动涉及 `store.py`、`cloud_agent/materializer.py`、`storage/postgres_backend.py`，
**必须重启后端**才会加载。判断依据：`Get-NetTCPConnection -LocalPort 8799` 查到的
PID 的启动时间早于改动时间时，跑的还是旧代码。

重启后可以这样验收——新建或重新分析一个工作区，然后：

```sql
SELECT id, created_at, updated_at FROM workspaces ORDER BY updated_at DESC LIMIT 5;
```

新工作区的 `created_at` 应该是**真实创建时刻**，而不是保存时刻。


