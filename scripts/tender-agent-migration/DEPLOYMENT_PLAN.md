# tender_agent：PostgreSQL 部署 + 数据搬迁全流程计划

> 目标：在本机（Windows）从零部署 PostgreSQL 16，把 `tender_agent` 的本地 JSON / 文件数据迁入单库，
> 全程可回滚、可校验，最后进入日常备份运维状态。
>
> 本文档配套文件：`schema.sql`（建表）、`migrate_to_db.py`（迁移脚本）、`README.md`（迁移映射与用法）。

---

## 0. 现状与环境结论（实测）

| 项目 | 实测结果 | 对方案的影响 |
|---|---|---|
| 操作系统 | Windows 家庭版（`EditionID=CoreCountrySpecific`，Build 26200） | **无 Hyper-V** |
| WSL2 | **未安装** | Docker Desktop 走 WSL2 后端，需额外装 WSL2 |
| CPU | 20 逻辑核 | 调优可用默认值，无需改并发参数 |
| 端口 5432 | **空闲** | 直接用默认端口，无冲突 |
| 磁盘 | `C:` 剩 16.8GB、`D:` 剩 7.1GB、`E:` 剩 **570.3GB** | 数据目录与备份放 **`E:`** |
| 已有 PostgreSQL | 无 | 全新安装 |
| Python | 3.13（`py` 启动器） | 迁移脚本直接可用 |
| Docker Desktop | 已装但守护进程未启动 | 见下：不推荐 |

**选型结论：用 Windows 原生安装版（EDB 安装器），不用 Docker。**

理由：
1. 家庭版没有 Hyper-V，Docker Desktop 只能靠 WSL2 后端，而 WSL2 未装，等于还要多部署一层 Linux 环境；
2. Docker Desktop 对大企业有商业授权约束（你们是优锘科技，规模可能触发付费条款）；
3. 原生版注册为 Windows 服务，**开机自启、随系统重启**，更适合“长期常驻的本地业务库”，不依赖任何后台守护进程；
4. 备份（`pg_dump`）、调优、看日志都更直接。

---

## 阶段 A：部署前准备（0.5 小时）

### A1. 停掉应用后端
确保 `tender_agent` 后端没有在跑（否则迁移期间它可能重写 JSON）：
```powershell
Get-Process python,uvicorn -ErrorAction SilentlyContinue   # 有就 taskkill /PID <pid> /F
```

### A2. 全量备份项目（迁移的安全底线）
把运行数据整目录压缩归档，放 `E:`：
```powershell
$src = "C:\Users\乔远\Desktop\tender_agent"
$dst = "E:\backup\tender_agent_pre_pg"
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item "$src\runtime"         "$dst\runtime"         -Recurse -Force
Copy-Item "$src\agent-config.json" "$dst\" -Force
Copy-Item "$src\.env"            "$dst\" -Force
```
> 备份后**在整个验证期内（至少 1~2 周）不要动** `$src\runtime` 里的 JSON。

### A3. 确认内存（决定调优档位）
```powershell
systeminfo | findstr /C:"物理内存"   # 或直接看任务管理器
```
对照阶段 C 的调优表选一档。

### A4. 确认磁盘空间
数据目录放 `E:`（剩 570GB，充足）。若坚持放 `C:`，先清出至少 5GB。

---

## 阶段 B：安装 PostgreSQL 16（原生版，约 0.5~1 小时）

### B1. 下载
官网 Windows 下载页：https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
选 **PostgreSQL 16.x Windows x86-64**（EDB 安装器）。国内网络慢可找镜像。

### B2. 安装向导关键选项
| 步骤 | 选择 |
|---|---|
| 组件 | 勾选 **PostgreSQL Server**、**pgAdmin 4**、**Command Line Tools**；**Stack Builder 不勾选** |
| **Data Directory** | **必须改**：`E:\postgres\data`（默认在 `C:\Program Files\...`，会吃掉 C 盘且不利于备份） |
| Port | `5432` |
| Locale | **Encoding 选 `UTF8`**；Locale 建议选 `C`（排序最确定，避免中文排序不一致）；时区选 Asia/Shanghai |
| 超级用户密码 | 设一个强密码（如 `postgres` 用户），**记到密码管理器**，后续 DSN 要用 |

### B3. 验证安装
```powershell
# 服务应已存在并运行
Get-Service *postgresql*
# 命令行工具可用（安装器会问是否加入 PATH；没加就用全路径，通常在
#   C:\Program Files\PostgreSQL\16\bin\）
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -c "SELECT version();"
```

---

## 阶段 C：初始化配置（约 0.5 小时）

### C1. 编辑 `E:\postgres\data\postgresql.conf`
先停服务再改：
```powershell
net stop postgresql-x64-16   # 服务名以 Get-Service *postgresql* 实际为准
```

**按内存调优**（只改这几项，其余用默认）：

| 内存 | shared_buffers | effective_cache_size | maintenance_work_mem | work_mem | max_connections |
|---|---|---|---|---|---|
| 8 GB | 2 GB | 4 GB | 256 MB | 16 MB | 20 |
| 16 GB | 4 GB | 8 GB | 512 MB | 16 MB | 20 |
| 32 GB | 8 GB | 16 GB | 1 GB | 16 MB | 20 |
| 64 GB | 16 GB | 32 GB | 2 GB | 16 MB | 20 |

固定加这几项（假设数据盘是 SSD）：
```ini
shared_buffers = <按上表>
effective_cache_size = <按上表>
maintenance_work_mem = <按上表>
work_mem = 16MB
max_connections = 20

random_page_cost = 1.1        # SSD
max_wal_size = 2GB
min_wal_size = 512MB
checkpoint_completion_target = 0.9

log_timezone = 'Asia/Shanghai'
timezone = 'Asia/Shanghai'
log_destination = 'stderr'
logging_collector = on
log_directory = 'log'
log_filename = 'postgresql-%a.log'
log_rotation_age = 1d
log_rotation_size = 50MB
```
改完启动服务：
```powershell
net start postgresql-x64-16
```

### C2. 确认只监听本机（默认即可）
`postgresql.conf` 里 `listen_addresses = 'localhost'`；`pg_hba.conf` 保持默认（`scram-sha-256`）。这台库只给本机应用用，不需要开外网。

### C3. 防病毒白名单（重要，Windows 常见坑）
Windows Defender 实时扫描会明显拖慢数据库和备份。把以下目录加进排除项：
```
E:\postgres\data
E:\postgres\backup
```
路径：`设置 → 隐私和安全性 → Windows 安全中心 → 病毒和威胁防护 → 管理设置 → 排除项`。
（公司若有第三方杀软/EDR，同样加白名单。）

---

## 阶段 D：建库、建角色、建表（约 15 分钟）

用 **pgAdmin 4** 或 psql 执行。下面用 psql：

```powershell
$PSQL = "C:\Program Files\PostgreSQL\16\bin\psql.exe"
& $PSQL -U postgres
```

进入后执行：

```sql
-- 1) 应用专用角色（不要用 superuser 跑应用）
CREATE ROLE tender_app LOGIN PASSWORD '改成强密码';

-- 2) 正式库 + 试跑库（先拿试跑库验证，再动正式库）
CREATE DATABASE tender_agent       OWNER tender_app ENCODING 'UTF8';
CREATE DATABASE tender_agent_test  OWNER tender_app ENCODING 'UTF8';
\q
```

应用建表脚本（先在**试跑库**上）：
```powershell
& $PSQL -U postgres -d tender_agent_test -f "C:\...\tender-agent-migration\schema.sql"
& $PSQL -U postgres -d tender_agent_test -c "\dt"   # 应看到 16 张表
```

> 迁移脚本用 `ON CONFLICT DO UPDATE` 幂等写入，但**表必须先建好**；脚本本身不建表。

---

## 阶段 E：安装迁移依赖 + 试跑（约 0.5 小时）

```powershell
py -m pip install "psycopg[binary]>=3.1"
```

**第一步：dry-run（不连库，先看源数据计数）**
```powershell
cd "C:\...\tender-agent-migration"
py migrate_to_db.py --dry-run
```
预期输出见 `README.md`（catalog 81、workspaces 5、sections 18、fine_review 210、evidence 641…）。

**第二步：迁入试跑库**
```powershell
$env:PG_DSN = "postgresql://tender_app:密码@localhost:5432/tender_agent_test"
py migrate_to_db.py
```
> 证明文件 2.6GB 的 sha256 计算会花几分钟，属正常。

**第三步：在试跑库上做验收**（见阶段 F 校验清单），全部通过后才迁正式库。

---

## 阶段 F：正式迁移 + 校验（约 1 小时）

```powershell
$env:PG_DSN = "postgresql://tender_app:密码@localhost:5432/tender_agent"
py migrate_to_db.py
```

### 校验清单（每项都过才算成功）

**1) 行数核对**（脚本末尾已打印，与 dry-run 逐项对比）：
```sql
SELECT 'catalog' t, count(*) FROM catalog_projects
UNION ALL SELECT 'workspaces', count(*) FROM workspaces
UNION ALL SELECT 'sections', count(*) FROM workspace_sections
UNION ALL SELECT 'fine_review', count(*) FROM workspace_fine_review_rows
UNION ALL SELECT 'evidence', count(*) FROM evidence_files;
```

**2) 完整性：JSONB 往返一致**（关键验证——证明搬过去的是“同一个数据”）：
```sql
-- 抽查任一工作区 payload 能否原样读回（无报错 = JSONB 结构完好）
SELECT id, jsonb_typeof(payload), pg_column_size(payload) AS bytes
FROM workspaces LIMIT 5;
```

**3) 证据文件 sha256 抽查**（拿库里的哈希和磁盘文件比对）：
```powershell
Get-FileHash "C:\Users\乔远\Desktop\tender_agent\runtime\libraries\company\证明材料\资质认证\1\1.png" -Algorithm SHA256
```
```sql
SELECT sha256 FROM evidence_files WHERE rel_path = 'libraries/company/证明材料/资质认证/1/1.png';
```
两边哈希应一致。

**4) 抽查业务字段可读**：
```sql
SELECT id, title, category, region, published_date FROM catalog_projects LIMIT 5;
SELECT workspace_id, title, kind, left(content, 40) FROM workspace_sections LIMIT 5;
```

**5) 墓碑已转逻辑删除**：
```sql
SELECT id, deleted_at IS NOT NULL AS is_deleted FROM workspaces ORDER BY is_deleted DESC;
```

---

## 阶段 G：回滚预案

本次迁移**不删除、不修改源 JSON 文件**，所以回滚很简单：

| 场景 | 回滚动作 |
|---|---|
| 应用还没接库 | 数据库不启用即可，应用照旧读本地 JSON（`$src\runtime` 一直在原地） |
| 已接库但出问题 | 代码里把存储开关切回 `STORE_BACKEND=json`（阶段 I 的开关） |
| 数据库坏了 | 用 `E:\backup\tender_agent_pre_pg` 里的原始 runtime 目录整包还原 |

> 铁律：**验证期内源 JSON 只读不动**；确认库完全稳定后，源数据再归档（不删，移入 `E:\backup\` 长期保留）。

---

## 阶段 H：备份与日常运维

### H1. 每日自动备份（pg_dump + 保留策略）
创建计划任务，每天凌晨 2 点跑：

```powershell
# dump 脚本 backup_tender.bat 内容（放 E:\postgres\backup\）
@echo off
set PGBIN=C:\Program Files\PostgreSQL\16\bin
set PGDUMP=%PGBIN%\pg_dump.exe
set STAMP=%date:~0,4%%date:~5,2%%date:~8,2%-%time:~0,2%%time:~3,2%%time:~6,2%
set STAMP=%STAMP: =0%
"%PGDUMP%" -h localhost -p 5432 -U tender_app -Fc -f "E:\postgres\backup\tender_agent_%STAMP%.dump" tender_agent
```
- 计划任务：`schtasks /create /tn "TenderAgent PGDump" /tr "E:\postgres\backup\backup_tender.bat" /sc daily /st 02:00`
- 保留策略：另加一个每周清理脚本，删除 14 天前的 `*.dump`。

> 用 `-Fc`（自定义格式）方便 `pg_restore` 按库/按表恢复。

### H2. 定期维护
- 手动 `VACUUM ANALYZE`：初始迁移后跑一次；之后依赖 autovacuum（默认已开）。
- 升级：小版本升级（16.x）用安装器覆盖即可；大版本留到以后评估。
- 磁盘水位：监控 `E:` 剩余空间；dump 每份约几十 MB（结构化数据），不会暴涨；证明文件仍留盘不占库。

### H3. 最小监控（可选）
```sql
SELECT pg_size_pretty(pg_database_size('tender_agent')) AS db_size;
SELECT now() - last_checkpoint_time FROM pg_control_checkpoint();
```

---

## 阶段 I：应用接入（搬迁之后的第二阶段，另行排期）

数据库跑起来 ≠ 应用会读它。接入要改代码（`store.py` 读写点走 repository 层），属于独立工作量，建议分小步：

1. 先做最小闭环：`cloud_agent/run_repository.py` 已经是接口化（注释明确“can later be backed by SQLite/PostgreSQL”），给它加一个 PostgreSQL 实现，跑通“写一条 bid_run → 读回来”。
2. 加全局开关 `STORE_BACKEND=json|db`，默认 `json`（保持现状，零风险）。
3. 逐个把 `store.py` 的 `persist_catalog / persist_runtime / persist_libraries / save_config / save_parse_bundle / save_library_document / import_document / delete_workspace` 切到 repository，双写 + 读校验。
4. 大文件走对象存储/磁盘 + 元数据表，前端 `/runtime/{path}` 静态服务改为走 API。

---

## 时间与工作量估算

| 阶段 | 内容 | 预估 |
|---|---|---|
| A | 准备 + 备份 | 0.5h |
| B | 安装 PostgreSQL | 0.5~1h |
| C | 初始化配置 | 0.5h |
| D | 建库建表 | 15min |
| E | 依赖 + 试跑 | 0.5h |
| F | 正式迁移 + 校验 | 1h |
| G | 回滚预案确认 | 15min |
| H | 备份运维搭建 | 0.5h |
| **部署+搬迁合计** | | **约 1 个工作日（4~5h）** |
| I | 应用接入（后续） | 另行排期，分多轮 |

---

## 检查清单（Checklist）

- [ ] 后端进程已停，`runtime/` + `agent-config.json` + `.env` 已备份到 `E:\backup\tender_agent_pre_pg`
- [ ] PostgreSQL 16 服务已安装并开机自启，`psql -c "select version()"` 正常
- [ ] 数据目录在 `E:\postgres\data`，防病毒已加白名单
- [ ] 应用角色 `tender_app`（非 superuser）+ 两个库（`tender_agent` / `tender_agent_test`）已建
- [ ] `schema.sql` 已在**试跑库**建表成功
- [ ] `migrate_to_db.py --dry-run` 计数与源一致
- [ ] 试跑库迁移 + 全部校验项通过
- [ ] 正式库迁移 + 行数 / JSONB / sha256 / 业务字段 / 墓碑 五类校验通过
- [ ] 每日 pg_dump 计划任务已建，保留策略生效
- [ ] 验证期内源 JSON 只读，未改动
- [ ] （后续）应用接入 repository 层 + 开关
