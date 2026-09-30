# tender_agent 数据接入指南（伙伴版）

> 给"拿到这个仓库、要在自己机器上跑起来并接上数据库"的人。
>
> 配套阅读：`README.md`（迁移映射与用法）、`DEPLOYMENT_PLAN.md`（Windows 原生安装全流程）、
> `shared-db/README.md`（Docker 编排与多人共享）、`APP_INTEGRATION.md`（应用如何接库）。

---

## 0. 先读这一节：你会拿到什么、不会拿到什么

**这一步不看清，后面一定白忙。**

git 仓库里只有**代码和建表脚本**，**一条业务数据都没有**。

| 内容 | 在仓库里？ | 大小 | 你怎么拿到 |
|---|:---:|---|---|
| 后端 / 前端代码 | ✅ | — | `git clone` |
| 建表 DDL（16 张表 + 索引） | ✅ | 12 KB | `git clone` |
| Docker 编排 + `.env.example` | ✅ | — | `git clone` |
| 迁移脚本 `migrate_to_db.py` | ✅ | 28 KB | `git clone` |
| **数据库里的业务数据** | ❌ | dump 后 **6.8 MB** | 第 3 节，两条路选一 |
| **证明材料文件本体（641 个）** | ❌ | **2,643 MB** | 第 4 节，必须单独传 |
| **磁盘状态 JSON** | ❌ | 74.6 MB | `runtime/` 被 gitignore |
| **AI API Key / 企业微信 webhook** | ❌ | — | 第 5 节，**必须自己配** |

> `runtime/` 整个目录（2,867 MB）都在 `.gitignore` 里。这是**有意为之**：里面是业务数据和
> 大文件，不适合进版本库。

### ⚠️ 最常见的误解

跑完 `docker-compose up -d`，看到 16 张表建好了，会以为环境搭完了 ——
**其实里面一行数据都没有。** 建库只解决"表结构"，"数据"是另一件事。

### ⚠️ 关于 `migrate_to_db.py`：这个脚本对你没用

`README.md` / `DEPLOYMENT_PLAN.md` 里让你跑：

```powershell
py migrate_to_db.py --dsn "postgresql://..."
```

**别跑，会失败。** 这个脚本做的是「读**本地磁盘**的 `runtime/*.json` → 写进数据库」。
而 `runtime/` 不在仓库里，你没有源数据，脚本读到的是空。

你的数据走第 3 节。

---

## 1. 先定接入模式（二选一）

| | **模式 A：连到现有库** | **模式 B：自己建一份** |
|---|---|---|
| 数据在哪 | 维护者的机器上 | 你自己的机器上 |
| 怎么拿 | 网络直连 | dump 文件导入 |
| 数据同步 | 实时同一份，谁改都看得见 | 一次性快照，之后各改各的 |
| 依赖 | 维护者机器要开机 | 无 |
| 适合 | 一起调同一套数据 | 自己开发 / 演示 / 离线 |

先定下来，再往后看。

---

## 2. 建库（两种方式选一）

### 2.1 Docker 方式（推荐，与维护者环境一致）

完整步骤见 `shared-db/README.md` §1-3，要点：

- **Windows 家庭版必须先装 WSL2**，否则 Docker 守护进程起不来（`docker version` 只有 Client 段）
- 库名 `tender_agent_dev`，角色 `tender_app`
- 首次启动时 `init/01-schema.sql` 自动建 **16 张表 + 索引**
- **`init/` 只在数据卷为空时执行一次**，之后改表结构不会重跑（见 `shared-db/README.md` §6.2）
- 本机实测用 `docker-compose`（连字符），`docker compose` 插件不可用

### ⚠️ 已知问题：首次启动可能以退出码 127 失败

**现象**（全新 clone 到 Windows 后）：

```
/usr/local/bin/docker-entrypoint.sh: running /docker-entrypoint-initdb.d/02-extra-databases.sh
cannot execute: required file not found
容器退出码: 127
```

**原因**：该 `.sh` 被 git 检出成了 CRLF 换行（Windows 默认 `core.autocrlf=true`），
shebang 变成 `#!/bin/bash\r`，Linux 找不到叫 `/bin/bash\r` 的解释器。

**处理：什么都不用改，再执行一次即可。**

```powershell
docker-compose up -d
```

第二次会正常启动 —— 因为数据目录已非空，官方入口会跳过整个 init 流程。
**16 张表在第一次就建好了**（`01-schema.sql` 跑在出问题的 `.sh` 之前），
唯一没做成的是"给每个伙伴额外建库"（`EXTRA_DATABASES`）那一步。

> 根本修法是给仓库加 `.gitattributes` 写 `*.sh text eol=lf`。

### 2.2 Windows 原生安装

完整步骤见 `DEPLOYMENT_PLAN.md` 阶段 B / C / D（含按内存档位的调优参数表、防病毒白名单）。

### 2.3 库名对照（消除歧义）

仓库里有两套并行命名，**不是错，但会让人困惑**：

| 文档 | 库名 |
|---|---|
| `shared-db/`、`.env.example`、`docker-compose.yml`、`APP_INTEGRATION.md` | **`tender_agent_dev`** ← 建议统一用这个 |
| `DEPLOYMENT_PLAN.md` | `tender_agent` / `tender_agent_test` |

本指南后续命令一律按 **`tender_agent_dev`** 写。

---

## 3. 拿数据

### 模式 A：连到维护者的库

完整网络方案见 `shared-db/README.md` §4（局域网 / Tailscale / 云主机三种，外加"别走公网"的警告）。

**要点**：

1. 维护者侧：改 `shared-db/.env` 里 `PG_BIND=0.0.0.0`，`docker-compose up -d` 重建容器
   （`restart` 不会重读 `.env`），再放行防火墙 5432 端口
2. 你拿到连接串：`postgresql://tender_app:<密码>@<维护者IP>:5432/tender_agent_dev`
3. 你**不需要**建库、不需要导数据、不需要拷那 2.6 GB 文件
4. 你的 `.env` 里 `PG_DSN` 主机改成维护者的 IP

> ⚠️ `DEPLOYMENT_PLAN.md` 阶段 C2 写的是「只监听本机…不需要开外网」——那是**单人本机部署**的
> 建议。要多人共享，以 `shared-db/README.md` §4 为准。这两处文档口径不同，注意区分。

> ⚠️ **多人同时跑应用会互相覆盖**：`STORE_BACKEND=dual` 是"读本地 JSON、写双份"，
> 两个应用实例各自读写自己的 `runtime/`，再各自把整份状态覆盖进同一个库。
> 共用时应**只由一个人跑应用**，或改用 `db` 模式并约定分工。详见 `APP_INTEGRATION.md`。

### 模式 B：导入 dump

#### 维护者侧 —— 导出

> ### 🚨 不要用 `>` 重定向！
>
> **`docker exec tender-pg pg_dump ... > file.dump` 这种写法是坏的。**
>
> 实测（PowerShell 5.1）：重定向会把原生命令的二进制输出当文本转码，
> dump 从 **7,125,701 字节** 膨胀到 **13,595,952 字节**，
> `pg_restore` 直接报 `input file does not appear to be a valid archive`。
>
> **你会以为自己做了一份备份，其实那文件根本恢复不了。**
> （`shared-db/README.md` §6.4 目前写的正是这种危险写法，别照抄。）

正确做法是「容器内导出 + `docker cp`」，全程字节安全：

```powershell
# 1) 容器内导出到临时文件（注意 -f，不是 >）
docker exec tender-pg pg_dump -U tender_app -Fc -f /tmp/tender_agent_dev.dump tender_agent_dev

# 2) 拷到 Windows
docker cp tender-pg:/tmp/tender_agent_dev.dump .\tender_agent_dev.dump

# 3) 清理容器内临时文件
docker exec tender-pg rm -f /tmp/tender_agent_dev.dump
```

**导出后自检（务必做）**：

```powershell
# 应打印目录项数量（实测 90），且没有任何 error
docker cp .\tender_agent_dev.dump tender-pg:/tmp/check.dump
docker exec tender-pg pg_restore -l /tmp/check.dump | Measure-Object -Line
docker exec tender-pg pg_restore -l /tmp/check.dump | Select-String "error"
docker exec tender-pg rm -f /tmp/check.dump
```

**正常结果**：文件 **7,125,701 字节**（约 6.8 MB），`pg_restore -l` 列出 **90** 个目录项、**0** 个错误。

#### 你侧 —— 恢复

```powershell
# 1) 建一个全新的空库（也可以直接用 init 建好的 tender_agent_dev）
docker exec tender-pg psql -U tender_app -d postgres `
  -c "CREATE DATABASE restore_probe OWNER tender_app ENCODING 'UTF8'"

# 2) 把 dump 传进容器
docker cp .\tender_agent_dev.dump tender-pg:/tmp/restore.dump

# 3) 恢复（--no-owner 避免角色不匹配的报错）
docker exec tender-pg pg_restore --no-owner -U tender_app -d restore_probe /tmp/restore.dump

# 4) 清理
docker exec tender-pg rm -f /tmp/restore.dump
```

#### 恢复后自检（重要）

跟维护者核对行数：

```sql
SELECT 'catalog_projects' t, count(*) FROM catalog_projects
UNION ALL SELECT 'workspaces', count(*) FROM workspaces
UNION ALL SELECT 'workspace_sections', count(*) FROM workspace_sections
UNION ALL SELECT 'workspace_fine_review_rows', count(*) FROM workspace_fine_review_rows
UNION ALL SELECT 'reminders', count(*) FROM reminders
UNION ALL SELECT 'evidence_files', count(*) FROM evidence_files
ORDER BY 1;
```

实测基准（2026-09-30 导出，完整往返 **16 张表逐项一致、0 错误**）：

| 表 | 行数 |
|---|---|
| `catalog_projects` | 93 |
| `workspaces` | 9（其中 3 个是删除墓碑） |
| `workspace_sections` | 18 |
| `workspace_fine_review_rows` | 286 |
| `reminders` | 91 |
| `libraries` / `library_documents` | 5 / 10 |
| `evidence_files` | 641 |
| `parse_bundles` / `parse_artifacts` / `asset_files` | 1 / 9 / 6 |
| `bid_runs` / `runtime_state` | 1 / 1 |
| `automation_runs` / `app_config` / `workspace_requirement_coverage` | 0 / 0 / 0 |

---

## 4. 那 2.6 GB 证明材料文件

### 为什么必须单独传

数据库里的 `evidence_files` 表，641 条记录**只是索引**（相对路径 + 文件大小 + sha256），
**文件本体不在数据库里**。

**不传文件的后果**：库里记录都在，但你在界面上点开任何一条证明材料**都是空的**。

### 目录结构

```
runtime/libraries/company/证明材料/
  技术产品/     14 个
  技术参数/    304 个
  类似业绩/    134 个
  证照凭证/     22 个
  财务证明/     72 个
  资质认证/     58 个
  项目人员/     37 个
  ──────────────────────
  合计         645 个文件、2,643.1 MB
```

> 645 个里有 4 个是 `.keep` 占位符文件，迁移时有意跳过 → 所以库里是 641 条。这个差值是正常的。

### 怎么传

git 传不了（在 `.gitignore` 里）。用移动硬盘、网盘或内网共享目录，
放到**完全相同的相对路径**下：

```
<你的项目根>/runtime/libraries/company/证明材料/...
```

### 校验（挑一个文件比对 sha256）

```powershell
Get-FileHash ".\runtime\libraries\company\证明材料\资质认证\1\1.png" -Algorithm SHA256
```

```sql
SELECT sha256 FROM evidence_files WHERE rel_path = 'libraries/company/证明材料/资质认证/1/1.png';
```

两边哈希应完全一致。

### `runtime/` 下还有别的目录，按需拷

| 目录 | 大小 | 是什么 | 不拷的后果 |
|---|---|---|---|
| `libraries/` | 2,647 MB | 证明材料 + 模板 | **证明材料点开是空的** |
| `projects/` | 83.6 MB | 解析产物、tender_pack、上传原件 | 工作区看不到解析详情 |
| `evidence_preview/` | 61 MB | PDF 渲染 / DOCX 拆图缓存 | 可自动重建，**不拷也行** |
| 顶层状态文件 | 74.6 MB | `catalog.json`、`workspaces.json` 等 | 模式 B 下用不到（数据来自 dump） |

---

## 5. 必须自己配的东西（含密钥）

### 5.1 `agent-config.json`（项目根）

**不在仓库里**（`.gitignore` 第 17 行挡住）。没有它，应用会退到默认配置，**AI 功能不可用**。

里面包含：

- `ai.apiKey` —— AI 模型 API Key（维护者的不会给你，**你得用自己的**）
- `ai.baseUrl` / `ai.model` / `ai.piModel`
- `wecom.webhook` —— 企业微信机器人地址（**你自己的群**）
- `monitoring.sites` —— 监控的招标网站列表
- `notifications` / `automation` / `decisionThresholds` 等业务配置

**建议**：先启动后端，在界面「设置」里配好，让应用自己生成这个文件，比手写可靠。

### 5.2 `.env`（项目根）

**不在仓库里**。需要的键（值按你的环境填）：

```ini
# 存储后端：json（只读写本地 JSON）/ dual（读 JSON、双写 JSON+DB）/ db（以库为唯一事实源）
STORE_BACKEND=dual

# 数据库连接串
PG_DSN=postgresql://tender_app:<密码>@127.0.0.1:5432/tender_agent_dev

# M3 图库（企业本体事实源；异地一般连不上，不影响招投标主干流程）
M3_COMPANY=
M3_USER=
M3_PASSWORD=

# Pi 撰写模型
PI_COMMAND=
PI_PROVIDER=
PI_API_KEY=
PI_API_TIMEOUT=
PI_WORKING_DIR=
```

**`STORE_BACKEND` 的三个取值**：

| 值 | 读 | 写 | 用途 |
|---|---|---|---|
| `json` | 本地 JSON | 本地 JSON | **默认，完全不碰数据库** |
| `dual` | 本地 JSON | JSON + 数据库 | 灰度期，出问题可随时切回 |
| `db` | 数据库 | 数据库 | 数据库成为唯一事实源 |

详见 `APP_INTEGRATION.md`。

> ⚠️ 模式 A（连维护者的库）时，`PG_DSN` 的主机换成维护者的 IP。

### 5.3 后端依赖

```powershell
cd backend
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> `requirements.txt` 已含 `psycopg[binary]>=3.1,<4`（本指南所依赖的数据库驱动）。

---

## 6. 自检清单

**建库**
- [ ] `docker-compose ps` 显示 `healthy`
- [ ] `psql -c "\dt"` 看到 **16 张表**

**数据（模式 B）**
- [ ] dump 文件约 **6.8 MB**（若是 13.6 MB 左右，说明被 `>` 重定向损坏了，重新导）
- [ ] `pg_restore -l` 能列出 **90** 个目录项且无 error
- [ ] 恢复后 `catalog_projects` = 93、`workspaces` = 9、`evidence_files` = 641

**文件**
- [ ] `runtime/libraries/company/证明材料/` 下 **645 个文件 / 2,643 MB**
- [ ] 抽查任一文件的 sha256 与库里 `evidence_files.sha256` 一致

**应用**
- [ ] `agent-config.json` 存在，且 AI Key 是**自己的**
- [ ] `.env` 里有 `STORE_BACKEND` 和 `PG_DSN`
- [ ] 后端启动无报错，`GET /api/health` 返回 `200 {"status":"ok",...}`
- [ ] 界面上能看到项目和进度

---

## 7. 常见问题

| 现象 | 原因 / 处理 |
|---|---|
| 容器首次启动退出码 127，报 `cannot execute: required file not found` | `.sh` 被检出成 CRLF。**再执行一次 `docker-compose up -d`** 即可，见 2.1 |
| 库建好了但界面空空如也 | **正常**。建库只给表结构，数据要按第 3 节导入 |
| 跑 `migrate_to_db.py` 读不到数据 / 报错 | 该脚本读的是**本地** `runtime/*.json`，你没有被迁移的源数据。走第 3 节的 dump |
| 证明材料列表有记录，点开是空的 | 第 4 节的 2.6 GB 文件没拷 |
| `pg_restore` 报 `input file does not appear to be a valid archive` | dump 被 PowerShell `>` 重定向损坏。按第 3 节用 `-f` + `docker cp` 重导 |
| `docker: unknown command: docker compose` | 本机 compose 插件不可用，改用 `docker-compose`（连字符） |
| 界面能开但 AI 功能报错 | `agent-config.json` 没配，或 API Key 无效 |
| 多人同时跑应用，数据互相覆盖 | `dual` 模式整份 JSON 读写。共用时只由一人跑应用，或改 `db` 模式并约定分工 |
| 连不上维护者的库 | 检查 `PG_BIND=0.0.0.0`、防火墙规则、是否同网段 / 同 tailnet |

---

## 附：本文档命令的实测记录

| 验证项 | 结果 |
|---|---|
| PowerShell `>` 重定向导出 | 13,595,952 字节，`pg_restore` 报 `not a valid archive` ✗ |
| `-f` + `docker cp` 导出 | 7,125,701 字节，90 个目录项，0 错误 ✓ |
| 恢复进全新空库 | 16 张表逐项行数一致，0 错误 ✓ |
| CRLF 版的 `02-extra-databases.sh` | 首次启动退出 127；**第二次启动正常，16 张表齐全** ✓ |

环境：Windows + Docker Desktop，容器 `tender-pg`（postgres:16），PowerShell 5.1。
