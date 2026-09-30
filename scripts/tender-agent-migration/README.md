# tender_agent 本地数据 → PostgreSQL 迁移包

把 `tender_agent` 项目散落在本机磁盘的 JSON / 文件数据，一次性迁入 **PostgreSQL 16** 单库。
企业证明文件（2.6GB）**不搬进库**，仍留盘（可后续迁对象存储），库里只存元数据。

## 交付物

| 文件 | 作用 |
|---|---|
| `PARTNER_ONBOARDING.md` | **伙伴数据接入指南** —— 拿到什么/缺什么、数据怎么过去（共享库 or dump）、2.6GB 证明文件、`.env` 配置、自检清单。**给别人部署时先看这份** |
| `DEPLOYMENT_PLAN.md` | **从零部署 PostgreSQL 到数据搬迁的完整计划**（含环境实测、安装、调优、校验、备份、回滚） |
| `shared-db/` | Docker 共享开发库：Compose 编排 + 建表 init + 多人共享的网络方案 |
| `schema.sql` | PostgreSQL 16 建表脚本（16 张表 + 索引） |
| `migrations/` | 表结构变更文件（**已建过库的人**要按编号手动执行，新建库不需要） |
| `migrate_to_db.py` | 一次性迁移脚本（读 JSON → 写库 → 行数校验报告） |
| `README.md` | 本文档 |

> ⚠️ `migrate_to_db.py` 读的是**发起迁移那台机器**的 `runtime/*.json`。别人 clone 仓库后没有
> 这份源数据，跑它只会读到空 —— 他们要的是 `PARTNER_ONBOARDING.md` 里 dump 的路子。

## 前置条件

1. PostgreSQL 16（本机或 Docker）已启动。
2. Python 3.10+，并安装驱动：

   ```powershell
   pip install "psycopg[binary]>=3.1"
   ```

## 使用步骤

```powershell
# 1) 建库
createdb tender_agent

# 2) 建表（psql 换成你环境里的客户端；或用 Docker 的 psql）
psql -d tender_agent -f schema.sql

# 3) 迁移（推荐：先 dry-run 看源数据计数，不连库）
py migrate_to_db.py --dry-run

# 4) 真迁移
py migrate_to_db.py --dsn "postgresql://postgres:你的密码@localhost:5432/tender_agent"
#    或设置环境变量后省略 --dsn：
#    $env:PG_DSN = "postgresql://postgres:你的密码@localhost:5432/tender_agent"
```

`--root` 默认指向 `C:\Users\乔远\Desktop\tender_agent`，项目移动过就用 `--root <路径>` 覆盖。

脚本**幂等**：所有表按主键 `ON CONFLICT DO UPDATE`，可安全重跑。
证明文件 641 个、约 2.6GB，sha256 计算会花几分钟；只想快速看结构可加 `--no-hash`。

## 迁移映射

| 源（本地） | 目标表 |
|---|---|
| `runtime/catalog.json` | `catalog_projects` |
| `runtime/workspaces.json` | `workspaces`（整对象保真） |
| `workspaces[].bidDocument.plan[]` | `workspace_sections`（拆行） |
| `workspaces[].reasoning.fineReview.rows[]` | `workspace_fine_review_rows`（拆行） |
| `workspaces[].bidDocument.requirementCoverage[]` | `workspace_requirement_coverage` |
| `runtime/workspaces-deleted.json` | `workspaces.deleted_at`（墓碑→逻辑删除） |
| `runtime/libraries.json` | `libraries` + `library_documents` |
| `runtime/reminders.json` | `reminders` |
| `runtime/automation-runs.json` | `automation_runs` |
| `runtime/bid_runs.json` | `bid_runs` |
| `runtime/projects/*/parse/parse-bundle.json` | `parse_bundles` |
| `runtime/projects/*/pack/tender_pack.json`、`parse/template-fragments/*.json` | `parse_artifacts` |
| `runtime/libraries/company/证明材料/**`（文件仍留盘） | `evidence_files`（元数据 + sha256） |
| `runtime/projects/*/outputs/*`（投标响应 json+docx） | `asset_files`（category=`bid_output`） |
| `runtime/projects/*/source/*`（上传的招标原文） | `asset_files`（category=`project_source`） |
| `runtime/projects/*/runs/*/*`（运行输入副本） | `asset_files`（category=`run_copy`） |
| `runtime/libraries/templates/*`、`runtime/libraries/*.xlsx` | `asset_files`（category=`library_template` / `library_asset`） |
| `runtime/scheduler-state.json` | `runtime_state`（key=`scheduler`） |
| `agent-config.json` | `app_config`（迁移脚本暂未写入，见下文） |

> 凡走 `asset_files` / `evidence_files` 的，**文件本身都不入库**，只登记 `rel_path` + `sha256` + 大小；
> 只有 `.json` 会额外把内容存进 `payload` 列。

## 迁移范围说明

**没有迁的东西（有意为之）：**

- **企业本体 8 类事实**：仍在 M3 图库，是本项目的单一事实源，不复制。
- **`runtime/evidence_preview/`（61MB）**：PDF 渲染预览 / DOCX 拆图缓存，可重建。
- **`runtime/pi-prompt-inspection/`、`probe_section_overlap_stats.json`（3MB）、`pi-agent-runs.json`（空）、`runtime/*.log`、`agent_bridge/sidecar.log`**：调试/日志，不入库。
- **锁文件与空目录**（`.store-runtime.lock`、`.bid-runs.lock`、`downloads/`、`exports/`、`searches/`、`qa/` 等）：无数据。
- **`templates/`、`demo-data/`、`frontend/`、`backend/`**：代码与静态资源，不是运行数据。
- **`agent-config.json` 写入 `app_config`**：脚本已建表但未自动写入——因为里面含 AI API Key，建议改为环境变量注入或加密后再入库，避免明文进库。当前继续用 `agent-config.json` 也不影响迁移。

**迁移前必做：停掉后端进程。** 本项目的运行数据是持续变化的（实测一次操作期间 `catalog` 就从 81 涨到 89、`reminders` 从 80 涨到 88）。后端在跑的时候迁移，迁完瞬间就不同步了。`migrate_to_db.py` 是幂等的，停服后再跑一次即可补齐。

**已知数据事实（迁移报告里会出现）：**

- `requirementCoverage` 当前为空 → `workspace_requirement_coverage` 0 行，是正常现象（最新版目录规划把条款映射延后到正文阶段，见 `store.py::_backfill_bid_document_coverage`）。
- 5 个工作区里只有 1 个有 `bidDocument`（18 章），其余 4 个是旧的匹配工作区。

## 校验方法

脚本结束会打印每张表行数。可与 dry-run 的源计数逐项对照，例如：

```
catalog_projects        81
workspaces               5
workspace_sections      18
workspace_fine_review_rows 210
evidence_files         641   （2643.1 MB，且每条有 sha256）
```

抽查证据文件完整性：

```sql
SELECT rel_path, sha256, size FROM evidence_files WHERE class_cn = '资质认证' LIMIT 5;
-- 与磁盘文件 sha256sum 对比即可
```

## 后续（接入应用，见主方案阶段 3）

本包只负责**搬数据**。要让应用真正读写这个库，还需改 `backend/app/store.py` 等读写点走 repository 层——这是另一个工程量，本包不含。核心改动点清单已在前面对话的方案里给出（`store.py`、`cloud_agent/run_repository.py`、`media_store/local.py`、`api/materials.py`、`main.py` 的 `/runtime/{path}` 静态服务、前端 `/runtime/` 引用等）。
