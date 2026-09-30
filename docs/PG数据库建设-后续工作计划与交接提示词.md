# PG 数据库建设 · 后续工作计划与交接提示词

> 本文件用途：① 固化本轮已完成/待办 ② 供**新对话**接手继续做数据库构建。
> 末尾「§七 交接提示词」是可直接整段粘贴给新对话的内容。

---

## 一、本轮已完成（2026-09-30）

| 项 | 结果 |
|:--|:--|
| 拉取最新 | `origin/main` = **`51186ea`**（含队友 5 个提交） |
| 合并 | 分支 **`integrate/pg-fusion`**，HEAD **`f529779`**（35 文件 +4905/-59） |
| 冲突 | 仅 `backend/app/store.py`（6 处），全部解决 |
| 回归防护 | `storage/json_backend.py` 改回原子写（复用 `repository/io.py`） |
| 测试对照 | **合并前 89 个失败 vs 合并后 89 个失败，失败集合逐条完全相同 → 零行为回归**；另 +10 通过（队友新增测试全绿） |

### 队友最新 5 个提交（`2a60b03..51186ea`）

```
51186ea chore: 清除 agent_bridge 的残留引用
62fae6d docs(migration): 新增伙伴数据接入指南，修复文档中的危险备份命令
f53a6d7 docs: 补充招投标智能体现状盘点文档
a5b2e95 feat(storage): Store 接入 PostgreSQL，支持 json/dual/db 三态存储后端
e66cb67 fix(bid-writing): 证明材料一键填充内联真实扫描件内容
```

### `store.py` 冲突解法（已定，勿推翻）

| 存储点 | 采用 | 理由 |
|:--|:--|:--|
| catalog / workspaces / libraries / reminders / automation_runs / deleted_workspaces | **队友的** `self.backend.load_*/save_*` | 三态开关（json/dual/db）已落地，是唯一能真连库的路径 |
| M3 企业事实 | **我方的** `self._facts_repo` | M3 是外部图库，不在 `StorageBackend` 接口范围内 |
| 我方 `_catalog_repo` / `_workspace_repo` | **已删** | 被 `self.backend` 取代 |
| `_workspaces_disk_mtime`、`_catalog_filter_cache.clear()` | **保留** | 我方懒加载/缓存优化的前置条件，与存储接缝无关 |

---

## 二、设计原则（一手定论，勿再讨论）

### 2.1 分工

| 维度 | 采用 |
|:--|:--|
| **表结构**（怎么拆表、主键、payload 摆放） | **队友的**（参考） |
| **规格标准**（多租户 + 值域约束 + 审计） | **严格的**（我方的） |
| **大方向** | 服务整个系统，不迁就任何一方的实现 |

### 2.2 核心原则

> **`payload` 是事实（全保真，永不加约束）；提升列是投影（归一 + 约束，允许为空）。**

```
先写 payload（永不失败）→ 再尝试归一填提升列 → 归一失败留 NULL + 记治理告警
CHECK 一律写成 CHECK (col IS NULL OR col IN (...))   -- NULL 合法
```

**这样"严格"和"不丢数据"不再冲突**：严格性体现在投影上，保真性体现在 payload 上。

### 2.3 提升列准入规则（7 条，满足至少一条才建列）

① 要 `WHERE` ② 要 `JOIN` ③ 要 `ORDER BY` ④ 要参与唯一键 ⑤ 要 `CHECK` ⑥ 要建索引 ⑦ 要进治理元数据

**其余一律进 payload。** 已实测有效：`workspace_sections` 从 **49 列压到 16 列（-67%）**，代码契约 15 个 key 零丢失。

### 2.4 参考文档（权威顺序）

1. `docs/融合方案样板验证-workspace_sections.md` —— **样板 + 实测数据 + 复现脚本**（最具体）
2. `docs/PG表设计融合方案-规范化与payload保真.md` —— 融合原则与逐维度对照
3. `docs/PG迁移合并方案-以队友存储层为准.md` —— 审计结论 + 存储层冲突解法
4. `docs/PostgreSQL表字段总清单与补表计划.md` —— 我方 54 表全字段清单

---

## 三、待办计划（按优先级）

### P0 · 保底回归（必须先做）

**1. 子表局部重建**
`backend/app/storage/postgres_backend.py::save_workspaces()` 里有三处**无 `WHERE` 的 `DELETE`**：

```python
cur.execute("DELETE FROM workspace_requirement_coverage")
cur.execute("DELETE FROM workspace_fine_review_rows")
cur.execute("DELETE FROM workspace_sections")
```

每次 `persist_runtime()`（存标书章节就会触发）都把**所有工作区**的行全删全插 → O(总行数)/次。
**改为** `DELETE ... WHERE workspace_id = ANY(%s)`，只重建本次传入的工作区。
（另注：`load_workspaces()` 根本不读这三张子表，它们是**只写不读的派生投影**。）

### P1 · 打通 db 模式

**2. `db` 模式下 ParseBundle 仍读磁盘**
`store.get_parse_bundle()` / `save_parse_bundle()` 读写 `runtime/projects/<id>/parse/parse-bundle.json`；
队友的 `StorageBackend` **没有** parse_bundle 方法，`parse_bundles`/`parse_artifacts` 表在应用侧零访问。
**要么**给 `StorageBackend` 加 `load/save_parse_bundle`，**要么**明确接受"解析产物留盘"（这是他 `base.py` 里写明的范围选择）。

### P1 · 表结构融合（核心工作量）

**3. 按 `workspace_sections` 样板铺开其余标书表**（每张都要跑五项验证）

| 表（队友的表名） | 重点 | 备注 |
|:--|:--|:--|
| `workspaces` | 主表，`payload` 含 `bidDocument` 37 key | `deleted_at`/`position` 他已建 |
| `workspace_fine_review_rows` | **`fineReview.rows` 有 61 个 key**（重头） | 标书正文/偏离表的主要输入 |
| `workspace_requirement_coverage` | 覆盖关系 | 已有 `coverage_type`/`status` 提升列 |
| `bid_runs` | 经 `cloud_agent/run_repository.py` 独立访问 | 不走 StorageBackend |
| `parse_bundles` / `parse_artifacts` | 解析产物 | 见 P1-2 |

**每张表的动作**：① 列队友结构 ② 按准入规则裁决投影列 ③ 加 `org_id`/CHECK/时间戳 ④ 真实数据往返 + 完整性 + 负向测试

### P2 · 治理自检（建议做，是"漏列"的根治手段）

**4. 把裁决结果写进 `meta_column`**（L 组，**已有数据**：`meta_table` 54 / `meta_column` 1082 / `meta_relationship` 61）
记录「列 ← payload 路径 + 归一函数 + 约束」，例如：

| 列名 | payload 路径 | 归一函数 | 约束 |
|:--|:--|:--|:--|
| `kind` | `$.kind` | `normalize_section_kind` | `IN (commercial,technical,deviation,attachment)` |
| `status` | `$.status` | `normalize_match_status` | 6 态契约 |

**5. 写完整性校验脚本**：代码实际写入的 key 集合 vs `meta_column` 路径集合做**差集** → 自动发现"代码加了字段、投影没跟上"。
> 这正好根治我们踩过的坑：G 组「代码写 37 key、表只有 3 列」在被自动检出之前，是靠人工核对才发现的。

### P2 · 补队友完全没有的表

| 组 | 表 | 状态 |
|:--|:--|:--|
| **F 组 运行可观测** | `analysis_run`（**已有 17 行真实数据**）/ `analysis_run_stage` / `llm_call_log` | ✅ 留 |
| **L 组 治理元数据** | `meta_table` / `meta_column` / `meta_relationship`（**已有数据**） | ✅ 留 |
| **I 组 初筛链** | `monitoring_rule` / `screening_profile` / `screening_constraint` / `screening_assessment` / `screening_check` / `reminder` | 按业务 |
| **J 采购** | `procurement_need` | 按业务 |
| **B 组残留** | `tender_project`（**已有 27 行**）/ `tender_document` | ✅ 留（队友无对应） |

### P3 · 需要拍板

**6. A 组 10 张 M3 镜像表要不要建？**
`fact_sync_log` / `organization` / `qualification_certificate` / `credential` / `performance_case` /
`staff_member` / `financial_evidence` / `technical_product` / `technical_parameter` / `evidence_file`

- 我方 `001` 自己写着「**M3 仍是权威源，本组表是只读镜像**」
- 队友写着「**企业本体事实仍以 M3 图库为单一来源，本库不复制**」
- **两边都认同 M3 权威 → 建议不建**（唯一能真正省掉的 10 张）

**7. `bid_section`（49 列）是否替换为 16 列样板？**（样板已证明可安全重排）

---

## 四、关键事实（避免重复调研）

### 4.1 我方 54 张表的真实状态（实测行数）

**只有 10 张有数据**：`meta_column`(1082) / `meta_relationship`(61) / `meta_table`(54) / `tender_project`(27) /
`workspace`(18) / `workspace_draft`(18) / `analysis_run`(17) / `bid_section`(6) / `bid_document`(1) / `bid_plan`(1)

**其余 44 张全空，且没有任何一张有应用侧写入点。** 数据全部来自 `scripts/backfill_bid_writing.py` 回填。
→ 说明这 54 张是**设计目标**，不是**在用现状**；且**表数多不是问题**（按域拆实体是对的），**列数多才是**（已修）。

### 4.2 真实的标书数据结构（实测，纠正了之前的误判）

- 真实 `bidDocument.plan[]` 元素**只有 5 个 key**：`kind / note / title / sectionId / requirementIds`
  → 之前「`bid_section` 缺 ~50 列」的说法**是从列设计反推的，与真实持久化形态不符**
- 代码契约 `bid_writing/schemas.py::build_section`（L236-252）产出**精确 15 个 key**
- 一批字段是**派生**的，不该各自建列：`writingProfile`←(source,kind,responseType)、`responseType`←(source,kind)、`materialScopes`←writingProfile、`level`/`order`←归一化
- 我方 `bid_section` 的 49 列里，**12 个 jsonb 列在真实 6 行中全空**

### 4.3 队友设计的三个已知硬伤

| # | 问题 | 位置 |
|:--|:--|:--|
| 1 | 子表全局 `DELETE` + 重建（无 `WHERE`） | `postgres_backend.py::save_workspaces` |
| 2 | 整份 payload 读写 → 多实例并发互相覆盖（他自己文档也写明） | `StorageBackend` 接口是集合级（`load_workspaces()` 全量 / `save_workspaces(dict)` 全量） |
| 3 | 16 张表里 6 张应用侧零访问（`parse_bundles`/`parse_artifacts`/`evidence_files`/`asset_files`/`app_config`/`runtime_state`） | 只由 `migrate_to_db.py` 写过一次 |

> 硬伤 2 已由我方 **`5f80bce` 单实例保护（同一 runtime 目录禁止起第二个后端）** 缓解，该提交必须保留。

---

## 五、环境与工具约束（踩过的坑，勿重复）

### 5.1 数据库

| 项 | 值 |
|:--|:--|
| 本地库（我方） | `postgresql://tender_app:tender_app_dev@127.0.0.1:5433/tender_agent`，容器 `tenderagent-pg` |
| 队友库 | `tender_agent_dev`，容器 `tender-pg`，端口 5432 |
| 连接方式 | **用 Python 走 TCP 5433**（`pg8000`），**不要用 docker CLI**——沙箱禁 Docker 命名管道 |
| `psql` | 宿主机没有；容器内有 |

### 5.2 必须遵守的禁忌

| 禁忌 | 原因 |
|:--|:--|
| ❌ 不要跑 `scripts/tender-agent-migration/migrate_to_db.py` | 它读**本地** `runtime/*.json`；跨机读不到数据 → 读到空 |
| ❌ 不要用 PowerShell `>` 导出 dump | 实测 7,125,701 B → 13,595,952 B **损坏**，`pg_restore` 报 `not a valid archive`。要用 `docker exec ... -f /tmp/x.dump` + `docker cp` |
| ❌ 不要用 `docker compose`（空格） | 开发机没装该插件，用 **`docker-compose`（连字符）** |
| ⚠️ 容器首次启动可能 `exit 127` | `.sh` 检出为 CRLF（`#!/bin/bash\r`）导致；**再 `up -d` 一次即可**（表在第一遍已建好） |
| ⚠️ 库名两套并存 | 统一用 `tender_agent_dev` |

### 5.3 沙箱环境

| 现象 | 对策 |
|:--|:--|
| `docker` / `ssh`（git push/fetch）被拒 | 提权重试一次（`sandbox_permissions: danger-full-access` + justification） |
| `pip install` 失败（anaconda 在 `D:\APP\anaconda`，工作区外） | **下载 wheel + `zipfile` 解到工作区**，加 `sys.path`（已验证可行） |
| `pg8000` 用 **`:name` 命名参数**，不是 `%s` | `con.run("... values (:a,:b)", a=1, b=2)`；jsonb 转型写 `:p::jsonb` |
| 控制台是 GBK，中文乱码 | **输出写 UTF-8 文件再读** |
| `pip` 的临时目录报 `Permission denied` | 设 `$env:TEMP` 到工作区内 |
| git hooks 在沙箱内静默不执行 | 仓库有 4 个 hook（`post-checkout`/`post-commit`/`post-merge`/`pre-push`）无法运行 |
| 系统临时目录 `PermissionError [WinError 5]` | 用工作区内临时目录 |

### 5.4 测试

```powershell
# 从代码根目录跑（E:\code\uinoproject\tenderagent\tender-agent\tender-agent）
python -m pytest backend/tests -q --no-header -p no:cacheprovider
# 当前基线：89 failed / 407 passed / 16 skipped
# （89 个失败是既有问题：沙箱临时目录 + 其他对话的 WIP，非本轮引入）
# 只取失败清单做 A/B 对照：
python -m pytest backend/tests -q --no-header -p no:cacheprovider --tb=no 2>&1 |
  Select-String "^FAILED " | ForEach-Object { $_.Line -replace '^FAILED ','' }
```

---

## 六、样板复现（照这个流程做新表）

```powershell
python E:\code\uinoproject\tenderagent\_fusion_sample\roundtrip.py
Get-Content E:\code\uinoproject\tenderagent\_fusion_sample\report.txt -Encoding UTF8
```

**五项验证（新表照做）**：

| 项 | 内容 | 样板结果 |
|:--|:--|:--|
| A 往返保真 | payload 读回 == 原始 JSON（逐字节） | 6/6 PASS |
| B 投影正确 | 提升列 == payload 派生值 | 6/6 PASS |
| C 完整性 | 代码契约 key vs 实际落位，无遗漏 | 15/15 全覆盖 |
| D 规格生效 | D1 脏值不阻塞写入 / D2 CHECK 拦截非法值 / D3 多租户唯一 | 全 PASS |
| E 加字段免迁移 | 新增契约外 key，列数不变且完整读回 | PASS |

---

## 七、交接提示词（整段复制给新对话）

```
你接手继续做「招投标智能体」项目的 PostgreSQL 数据库构建。上一轮已完成合并与方案定稿，
请先读文档再动手。

## 第一步：读这些文档（按顺序，都在仓库 docs/ 下）
1. docs/融合方案样板验证-workspace_sections.md   ← 最重要：样板 + 实测数据 + 复现脚本
2. docs/PG表设计融合方案-规范化与payload保真.md   ← 融合原则
3. docs/PG迁移合并方案-以队友存储层为准.md        ← 存储层审计与冲突解法
4. docs/PG数据库建设-后续工作计划与交接提示词.md  ← 本文件的完整版（含全部待办与坑）

## 项目位置
- 工作区根：E:\code\uinoproject\tenderagent
- 代码根（git 仓库）：E:\code\uinoproject\tenderagent\tender-agent\tender-agent
- 当前分支：integrate/pg-fusion，HEAD f529779（已合并 origin/main = 51186ea）
- 本地库：postgresql://tender_app:tender_app_dev@127.0.0.1:5433/tender_agent
  （容器 tenderagent-pg；用 Python + pg8000 走 TCP，不要用 docker CLI）

## 不要提交这 3 个文件（另一个对话正在改）
docs/PostgreSQL表字段总清单与补表计划.md
docs/标书表替换可行性核对.md
scripts/backfill_bid_writing.py

## 设计原则（已定，勿再讨论）
1) 表结构参考队友的（他的 16 张表 + payload 保真），规格标准用严格的（org_id 多租户 +
   值域 CHECK + 审计时间戳）。大方向是服务整个系统，不迁就任何一方实现。
2) 核心原则：payload 是事实（全保真，永不加约束）；提升列是投影（归一 + 约束，允许为空）。
   写入流程：先写 payload（永不失败）→ 再归一填提升列 → 归一失败留 NULL + 记治理告警。
   CHECK 一律写成 CHECK (col IS NULL OR col IN (...))。
3) 提升列准入规则（满足至少一条才建列）：要 WHERE / 要 JOIN / 要 ORDER BY / 要参与唯一键 /
   要 CHECK / 要建索引 / 要进治理元数据。其余一律进 payload。
   实测有效：workspace_sections 从 49 列压到 16 列（-67%），代码契约 15 个 key 零丢失。

## 任务（按优先级）

### P0 保底回归
修复 backend/app/storage/postgres_backend.py::save_workspaces() 里三处无 WHERE 的 DELETE
（workspace_requirement_coverage / workspace_fine_review_rows / workspace_sections），
每次保存全表删全插是 O(总行数)。改为 DELETE ... WHERE workspace_id = ANY(%s)，只重建传入的工作区。

### P1 打通 db 模式
db 模式下 ParseBundle 仍读磁盘（store.get_parse_bundle/save_parse_bundle 读写
runtime/projects/<id>/parse/parse-bundle.json），而队友的 StorageBackend 没有 parse_bundle 方法。
要么给 StorageBackend 加 load/save_parse_bundle，要么明确接受"解析产物留盘"（先问我）。

### P1 表结构融合（核心工作量）
按 docs/融合方案样板验证-workspace_sections.md 的样板逐张做：
  workspaces（主表，payload 含 bidDocument 37 key）
  workspace_fine_review_rows（fineReview.rows 有 61 个 key，是重头）
  workspace_requirement_coverage
  bid_runs
  parse_bundles / parse_artifacts
每张表的动作：① 列队友结构 ② 按准入规则裁决投影列 ③ 加 org_id/CHECK/时间戳
④ 用真实数据跑五项验证（A 往返保真 / B 投影正确 / C 完整性 / D 规格生效 / E 加字段免迁移）
复用脚本：_fusion_sample/roundtrip.py（改表名与投影映射即可）

### P2 治理自检
把每张表的裁决结果写进 L 组 meta_column（列 ← payload 路径 + 归一函数 + 约束），
再写完整性校验脚本：代码实际写入的 key 集合 vs meta_column 路径集合做差集，
自动发现"代码加了字段、投影没跟上"。这能根治项目里踩过的坑（代码写 37 key、表只有 3 列）。

### P2 补队友完全没有的表
F 组运行可观测（analysis_run 已有 17 行真实数据）/ L 组治理元数据（已有数据）/
I 组初筛链 / J procurement_need / B 组 tender_project（已有 27 行）

### P3 需拍板（先问我，别自己决定）
1) A 组 10 张 M3 镜像表要不要建？（两边都认同 M3 是权威源 → 建议不建）
2) 我方 49 列的 bid_section 是否替换为 16 列样板？

## 硬性约束（踩过的坑，务必遵守）
- ❌ 不要跑 scripts/tender-agent-migration/migrate_to_db.py（读本地 runtime/*.json，跨机读到空）
- ❌ 不要用 PowerShell `>` 导出 pg dump（实测把 7.1MB 写成 13.6MB 损坏文件）
- ❌ 不要用 `docker compose`（空格），用 `docker-compose`
- ⚠️ docker / git push / git fetch 在沙箱下被拒 → 提权重试一次即可
- ⚠️ pip 装不上（anaconda 在工作区外）→ 下载 wheel + zipfile 解到工作区加 sys.path
- ⚠️ pg8000 用 `:name` 命名参数，不是 %s；jsonb 写 `:p::jsonb`
- ⚠️ 控制台 GBK 会乱码 → 中文输出写 UTF-8 文件再读
- ⚠️ 容器首次启动可能 exit 127（CRLF 导致），再 `up -d` 一次即可

## 测试基线
python -m pytest backend/tests -q --no-header -p no:cacheprovider
当前 89 failed / 407 passed / 16 skipped（89 个失败是既有问题，非本轮引入）
每改一处都要跑测试，并用失败清单 A/B 对照确认失败集合同源。

## 工作方式要求
- 每完成一项就同步文档（说清改了什么、在哪个目录），该删的删、该补的补
- 改动要可验证、可回滚；不确定的先问我，不要自己拍板
- 中文回复
```

---

## 八、本轮遗留（未做，明确记录）

| # | 事项 | 状态 |
|:--|:--|:--|
| 1 | 子表局部重建（P0） | ❌ 未做 |
| 2 | `parse_bundles` 读写方法 | ❌ 未做（待拍板） |
| 3 | 其余标书表按样板铺开 | ❌ 未做 |
| 4 | `meta_column` 登记 + 完整性校验脚本 | ❌ 未做 |
| 5 | A 组 10 张 M3 镜像表的取舍 | ❌ 待拍板 |
| 6 | `bid_section` 49→16 列替换 | ❌ 待拍板 |
| 7 | 本地 `_baseline_wt` worktree | 已用 `git worktree remove` 清理 |
