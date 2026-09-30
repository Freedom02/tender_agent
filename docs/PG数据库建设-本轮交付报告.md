# PG 数据库建设 · 本轮交付报告（2026-09-30）

> 分支 `integrate/pg-fusion`，本轮 7 个提交（`33d0135` → `06d5733`）。
> 测试基线全程保持 **89 failed / 407 passed / 16 skipped**，失败清单逐条相同 → **零行为回归**。

---

## 一、一句话总结

本轮把「融合方案」从**文档**变成了**库里真实的结构**，并且发现并修掉了一个**此前没人知道的硬阻塞**：

| # | 事项 | 结果 |
|:--|:--|:--|
| 1 | **`db` 模式根本跑不起来** | 🔴 发现 → ✅ 修复（建 partner schema，db 模式首次跑通） |
| 2 | P0 子表全局 DELETE | ✅ 修复 + V1~V4 验证 |
| 3 | `bid_section` 列级收缩 | ✅ 49 → 17 列（-65%），五项验证全 PASS |
| 4 | `bid_document` 列级收缩 | ✅ 45 → 14 列（-69%），五项验证全 PASS |
| 5 | 其余 42 张表加 payload | ✅ 44/44 张表 payload 全覆盖 |
| 6 | 治理自检（根治"漏列"） | ✅ meta_column 登记 + 校验脚本（含反向测试） |

---

## 二、🔴 头号发现：`db` 模式从未真正跑通过

### 现象（实测）

```python
PostgresBackend("postgresql://...@127.0.0.1:5433/tender_agent").load_workspaces()
# -> UndefinedTable: relation "workspaces" does not exist
```

逐个核对队友的 12 张表，**全部 MISSING**：

| 表 | 本地库 |
|:--|:--|
| `workspaces` / `workspace_sections` / `workspace_fine_review_rows` / `workspace_requirement_coverage` | ❌ 不存在 |
| `catalog_projects` / `libraries` / `library_documents` / `reminders` / `automation_runs` | ❌ 不存在 |
| `parse_bundles` / `parse_artifacts` / `bid_runs` | ❌ 不存在 |

### 根因

上一轮"合并了队友的三态存储层"是**代码层面**的合并（`store.py` 接缝、`storage/` 包），
**库层面队友的表一张都没建**。

而 `StorageBackend` 里的表名是**硬编码**的（`SELECT ... FROM workspaces`），
所以 `STORE_BACKEND=db` / `dual` 一开就报错。

> ⚠️ 连带含义：**`dual` 模式也跑不起来**（它内部同样持有 `PostgresBackend`），
> 实际可用的一直只有 `json` 模式。

### 修复

新增 `backend/migrations/010_partner_scaffold.sql`：把队友存储层所需的 **16 张表建到独立 schema `partner`**。

| 决策 | 理由 |
|:--|:--|
| **独立 schema 而非 public** | ① 我方 44 张目标表在 public，是**目标数据模型**，一张不删/不改名/不合并<br>② 实测两套表名**零冲突**（`workspace` vs `workspaces`、`parse_bundle` vs `parse_bundles`），可共存<br>③ 队友表最终会被我方模型吸收 → 届时 `DROP SCHEMA partner CASCADE` 一句清掉**脚手架**<br>④ 物理隔离，"谁是权威"一目了然，彻底杜绝"用队友表替代我方表" |
| **结构照抄队友 schema.sql** | 列名/类型/主键/约束**一字未改**（仅加 schema 限定），队友代码原样可用 |

配套代码改动：

| 文件 | 改动 |
|:--|:--|
| `postgres_backend.py` | `PostgresBackend(dsn, schema=None)`；`_connect` 里 `SET search_path TO <schema>, public`（schema 名白名单正则校验后才拼接，标识符不能参数化） |
| `storage/__init__.py` | `create_backend` 读 `PG_SCHEMA` 环境变量 |

### 验证（`_fusion_sample/verify_dbmode.py`）

```
6 个 load 方法全部 OK（建表前全部 UndefinedTable）
save 往返 payload 逐字节保真           PASS
子表拆行 sections=2/fine_review=1/coverage=1  PASS
契约外字段（含嵌套 + 中文 key）自动落 payload  PASS
幂等：重复 save 行数不翻倍              PASS
```

**用法**：`$env:STORE_BACKEND="db"; $env:PG_DSN="..."; $env:PG_SCHEMA="partner"`

---

## 三、P0 保底回归：子表局部重建

`postgres_backend.py::save_workspaces()` 三处无 `WHERE` 的 `DELETE`：

```python
# 改前（每次 persist_runtime() 都全库删全插 -> O(总行数)/次）
cur.execute("DELETE FROM workspace_requirement_coverage")

# 改后（只重建本次传入的工作区）
cur.execute("DELETE FROM workspace_requirement_coverage WHERE workspace_id = ANY(%s)", (ws_ids,))
```

**关键实现细节**：以 **`workspaces` 字典的键**为 `ws_ids`，而**不是**各子表 rows 里出现过的 id。
若某工作区这次没产生任何章节行，用子表推导的 id 集合会漏掉它，旧行就会残留；
用字典键才能保证"传入即重建"，**包括清空成 0 行的边界**。

**验证**（`_fusion_sample/verify_p0.py`）：

| 项 | 结果 |
|:--|:--|
| V1 局部化（只重建 A 时 B 的 3 行不受影响） | ✅ PASS |
| V2 语义等价 | ✅ PASS |
| V3 清空边界 | ✅ PASS |
| V4 旧写法对照（无 WHERE → B 的 3 行被误删） | ✅ 复现出被修的缺陷 |

---

## 四、核心工作量：列级收缩（**不是表级**）

> ⚠️ 表名 / 主键 / 行数 / 外键关系**全都不变**。**一张表都没有删**。
> 44 张表在本轮前后**始终是 44 张**。

### 4.1 先纠正三处认知（实测数据说话）

| 表 | 行数 | 列数 | 之前文档的说法 | **实测结论** |
|:--|--:|--:|:--|:--|
| `bid_section` | 6 | **49** | 要收缩 | ✅ 属实，12 个 jsonb 列实填充 0 |
| `bid_document` | 1 | **45** | 要加 payload | ✅ 属实，35 列全空 |
| `workspace` | 18 | **8** | "`fineReview.rows` 61 key 是重头" | ❌ **不成立**，这表本来就 8 列 |
| `workspace_draft` | 18 | **6** | 同上 | ❌ **不成立**，本来就 6 列 |
| `bid_plan` | 1 | **5** | 要改造 | ❌ 本来就极小，无可收缩 |
| `analysis_run` | 17 | **31** | 要改造 | ⚠️ **29 列真实填充 100%**，是**真在用**的列，**不该收缩** |

→ **真正该做列收缩的只有 `bid_section` 和 `bid_document`**。
依据是**实测填充率为 0**，不是"列看起来多"。

### 4.2 `bid_section`：49 → 17 列（-65%）

**最有价值的发现**：代码实际写入 section 的 key 有 **108 个**，
其中 **66 个在表里根本没有对应列** → **今天就是静默丢失**。

保留的 17 列（逐条准入理由）：

| 列 | 准入理由 |
|:--|:--|
| `id` / `biz_id` | 代理键 + 业务键（唯一） |
| `bid_document_id` | JOIN（FK） |
| `section_id` | 业务键 + 参与唯一键 |
| `title` | WHERE / ORDER BY |
| `content` | 长正文（text 自动 TOAST） |
| `version` | 参与唯一键 UNIQUE(bid_document_id, section_id, version) |
| `kind` / `source` / `status` | CHECK 值域 + 要查 |
| `required` | 要查（必答项过滤） |
| `level` / `order_no` | ORDER BY（树形层级 / 章节顺序） |
| `requirement_ids` | 覆盖关系查询（2/6 行有值） |
| `created_at` / `updated_at` | 审计时间戳 |
| `payload` | 全保真事实 |

收进 payload 的 33 列：全部**实测填充率为 0**。

新增约束（枚举取自 `bid_writing/schemas.py`，一律 `CHECK (col IS NULL OR col IN (...))`）：

```sql
bid_section_kind_chk   CHECK (kind   IN ('commercial','technical','deviation','attachment'))
bid_section_source_chk CHECK (source IN ('template','ai','table','attachment','copied_template'))
bid_section_status_chk CHECK (status IN ('pending','generating','generated','missing'))
```

### 4.3 `bid_document`：45 → 14 列（-69%）

**与 `bid_section` 的一个重要区别（已记入迁移注释）**：
`bid_document` 的列**已经覆盖代码全部 40 个 key**（缺口 0 个），
所以"静默丢失"在这张表上**今天不成立**。
加 payload 的价值是**面向未来**的：代码再加字段直接落 payload，不用再 `ALTER TABLE`。

保留 `plan` / `deviation_tables` 的理由：前者与 `bid_section` 有投影关系，
后者被视图 `v_deviation_row` / `v_parameter_deviation_row` 依赖。

### 4.4 其余 42 张表：只加 payload，**不砍列**

新增 `014_payload_all_tables.sql`，**纯新增**（只 `ADD COLUMN`）：

> **为什么空表不做列裁决**：按既定红线，「列裁决」需要**实测依据**——
> 真实数据里哪些列填充率为 0。这 42 张表里绝大多数是 **0 行（未落地）**，
> 没有实测依据，**不能因为"列看起来多"就砍**，否则会把将来真会用的查询能力砍掉。
> → 空表统一只加 payload，列裁决留到有真实数据之后按域逐步落地。

**结果：44/44 张表 payload 全覆盖。**

---

## 五、踩过并修掉的一个坑（重要，值得记录）

`011` 首次回填 payload 时用了 `jsonb_build_object` 把各列拼起来，有**两个实质缺陷**：

| # | 缺陷 | 后果 |
|:--|:--|:--|
| 1 | **数据丢失**：`c-01` 的 `note` key 丢了 | `note` 只存在于 `bid_plan.plan[]`，`bid_section` 从来没有 `note` 列，011 无从拷到它 |
| 2 | **凭空造事实**：给每条都写进 `agentResults: []`、`editHistory: []` 等 18-19 个 key | 它们**在真实数据里根本不存在**——把默认值当事实写 |

**这是被 A 项验证（0/6 FAIL）抓出来的**，如果没有五项验证就会带着错误数据上线。

**修正**（`012_fix_bid_section_payload.sql`）：
> **payload 从权威源重建，而不是从列反推。**
> `bid_plan.plan[]` 是**原始持久化形态**（它就是"事实"）；`bid_section` 的列是**投影**。
> **投影可以被重建，事实不能被编造。**

修正后：`note` 回来了，权威源 key **零丢失**，且只补投影列上**真实非空**的值。

---

## 六、治理自检：根治"漏列"

### 6.1 `015_meta_column_projection.sql`

把投影裁决结果登记进 L 组 `meta_column`：**列 ← payload 路径 + 归一函数 + 约束**。

| 项 | 结果 |
|:--|:--|
| `meta_column` 总行数 | 872（清掉 65 行已删列 + 新增 payload 登记） |
| `bid_section` 登记列数 | 17（与真实列一致） |
| `bid_document` 登记列数 | 14（与真实列一致） |
| payload 列登记数 | **44**（全表覆盖） |
| 带 `payload=$.xxx` 路径映射的行 | 17 |

### 6.2 `scripts/check_projection_coverage.py`

**完整性校验脚本**：代码实际写入的 key 集合 vs `meta_column` 的路径集合做差集。

检查三件事：
1. 真实列 ↔ `meta_column` 登记是否一致（**治理漂移**）
2. 代码写入的 key 是否有落位（列 或 payload）
3. 提升列是否都有 payload 路径映射（"纯投影"还是"独立事实"**必须有交代**）

**当前结果**：

```
bid_section ：代码写入 72 key（9 列 + 63 payload），无缺口 ✅
bid_document：代码写入 40 key（8 列 + 32 payload），无缺口 ✅
```

**反向测试**（`_fusion_sample/verify_checker_detects.py`）——证明它**不是"永远 PASS 的摆设"**：

| 步骤 | 结果 |
|:--|:--|
| 基线 | ✅ PASS |
| 人为加一个未登记的列 | ✅ 被检出（"提升列缺 payload 路径映射"） |
| 人为删一行 meta_column 制造漂移 | ✅ 被检出（"真实列未登记进 meta_column"） |
| 还原后 | ✅ PASS（库恢复原状） |

---

## 七、迁移文件清单（本轮新增 6 个）

| 文件 | 作用 | 幂等 |
|:--|:--|:--|
| `010_partner_scaffold.sql` | 队友 16 张表建到 `partner` schema（**脚手架**） | ✅ |
| `011_fusion_bid_section.sql` | `bid_section` 列级收缩 49→17 | ✅ |
| `012_fix_bid_section_payload.sql` | **修正** 011 的 payload 回填缺陷 | ✅ |
| `013_fusion_bid_document.sql` | `bid_document` 列级收缩 45→14 | ✅ |
| `014_payload_all_tables.sql` | 其余 42 张表加 payload（纯新增） | ✅ |
| `015_meta_column_projection.sql` | 投影裁决登记进 `meta_column` | ✅ |

**回滚**：
- 脚手架：`DROP SCHEMA IF EXISTS partner CASCADE;`
- 列收缩：DDL 在 git 历史里；数据在 payload 里可完整还原
- **改动前的原始数据备份**：`_fusion_sample/backup_bid_tables.json`（bid_section 6 行 + bid_document 1 行 + bid_plan 1 行）

---

## 八、验证与复现

```powershell
# 测试基线（每轮都跑，与 baseline_failures.txt 逐条 A/B 对照）
cd E:\code\uinoproject\tenderagent\tender-agent\tender-agent
& "D:\APP\anaconda\envs\finance\python.exe" -m pytest backend/tests -q --no-header -p no:cacheprovider
# 89 failed / 407 passed / 16 skipped（既有问题，非本轮引入）

# 五项验证
& "D:\APP\anaconda\envs\finance\python.exe" _fusion_sample\verify_bid_section.py
& "D:\APP\anaconda\envs\finance\python.exe" _fusion_sample\verify_bid_document.py
& "D:\APP\anaconda\envs\finance\python.exe" _fusion_sample\verify_dbmode.py
& "D:\APP\anaconda\envs\finance\python.exe" _fusion_sample\verify_p0.py
& "D:\APP\anaconda\envs\finance\python.exe" _fusion_sample\verify_checker_detects.py

# 治理校验
& "D:\APP\anaconda\envs\finance\python.exe" scripts\check_projection_coverage.py
```

---

## 九、遗留与建议下一步

| # | 事项 | 状态 |
|:--|:--|:--|
| 1 | **`parse_bundle` 是否进库** | ⏳ **仍待拍板**（您倾向 A：接受留盘 + 建好投影列；本轮已把 `parse_bundle` 加好 payload，投影列随时可补） |
| 2 | 27 张空表的列裁决 | ⏳ 等有真实数据后按准入规则做（本轮只加 payload，**不砍列**） |
| 3 | 应用写入路径切到 payload 模式 | ⏳ 代码目前仍写"列"；需要按域逐步改成"先写 payload → 再归一填提升列" |
| 4 | `meta_column` 的 `value_domain` / `semantic_type` 全量补齐 | ⏳ 本轮只补了 bid_section / bid_document，其余表可渐进补 |
| 5 | 队友表最终被我方模型吸收（统一版） | ⏳ 届时 `DROP SCHEMA partner CASCADE` + 改 `PostgresBackend` 指向我方表 |

### 关于"统一成一版"的说明（回答用户提问）

**最终必须统一，且只能是"我方那一版"**，判据：

| 判据 | 我方 44 张 | 队友 16 张 |
|:--|:--|:--|
| 业务域覆盖 | 11 组全覆盖（含 F 运行可观测 / L 治理元数据，**队友完全没有**） | 只有标书 + 目录 + 提醒 + 解析片段 |
| 真实数据 | 1077 行 | **0 行** |
| 多租户 `org_id` | 每表都有 | 无 |
| 值域 `CHECK` | 有（6 态契约） | 无 |
| 已建到库里 | ✅ | ❌（本轮才建，且是**脚手架**） |

**统一路径**：我方模型 + 队友的保真技法（payload / position / `coalesce_cols` / `touch_col` / `deleted_at` 墓碑 / 三态开关）。
本轮已完成的正是这条路——**partner schema 只是让 `db` 模式今天能跑的脚手架，随时可弃**。
