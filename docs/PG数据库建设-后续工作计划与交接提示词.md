# PG 数据库建设 · 后续工作计划与交接提示词

> 本文件用途：① 固化各轮已完成/待办 ② 供**新对话**接手继续做数据库构建。
> 末尾「§七 交接提示词」是可直接整段粘贴给新对话的内容。
>
> 📌 **第二轮的完整交付报告见 `docs/PG数据库建设-本轮交付报告.md`**
> （db 模式硬阻塞的发现与修复、两张表的列级收缩实测、五项验证结果、踩过的坑）。

---

## 〇、第二轮已完成速览（2026-09-30，HEAD `06d5733`）

| # | 事项 | 结果 |
|:--|:--|:--|
| 1 | **发现 `db` 模式从未跑通** | 🔴 队友 16 张表在本地库**一张都没建** → 新增 `010_partner_scaffold.sql` 建到 `partner` schema + `PG_SCHEMA` 支持 → **db 模式首次跑通** |
| 2 | **P0 子表局部重建** | ✅ 三处无 `WHERE` 的 `DELETE` 改为 `WHERE workspace_id = ANY(%s)`；V1~V4 验证含**清空成 0 行的边界** |
| 3 | **`bid_section` 列级收缩** | ✅ **49 → 17 列（-65%）**，五项验证全 PASS。**关键发现：代码写 108 个 key，其中 66 个原本无处可去 = 静默丢失** |
| 4 | **`bid_document` 列级收缩** | ✅ **45 → 14 列（-69%）**，五项验证全 PASS |
| 5 | **其余 42 张表加 payload** | ✅ **44/44 张表 payload 全覆盖**（纯新增，零风险） |
| 6 | **治理自检** | ✅ `015_meta_column_projection.sql` + `scripts/check_projection_coverage.py`（含**反向测试**，证明能真的发现缺口） |

> ⚠️ 全程：**44 张表始终是 44 张**，一张没删、没改名、没合并。
> 测试基线**全程保持 89 failed 逐条相同** → 零行为回归。

### 三处认知纠正（实测数据推翻了旧文档）

| 旧说法 | 实测结论 |
|:--|:--|
| `workspace` 加 payload，`fineReview.rows` 61 key 是重头 | ❌ **不成立**：`workspace` 只有 **8 列**、`workspace_draft` **6 列**、`bid_plan` **5 列**，本来就极小，无列可收缩（那些 key **从未建过列**） |
| `analysis_run` 要改造 | ⚠️ **31 列里 29 列真实填充 100%**，是**真在用**的列 → **不做列收缩**，只加 payload |
| ✳ 真正该收缩的只有 | ✅ **`bid_section`（49 列）和 `bid_document`（45 列）**，依据是**实测填充率为 0** |

---

## 一、第一轮已完成（2026-09-30）

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

### 2.1 分工（不可动摇的边界，先看这里）

| 维度 | 定论 |
|:--|:--|
| **表本身**（表名、按域拆表、表数量） | ✅ **我方的 44 张，一张都不删、不改名、不合并**（A 组 10 张已按决定删除） |
| **保真技法**（借队友的） | `payload jsonb` 全保真 / `position` 排序列 / `coalesce_cols` 不覆盖 `created_at` / `touch_col` 只在真变化时改 `updated_at` / `deleted_at` 墓碑 / `json`-`dual`-`db` 三态开关 |
| **规格标准** | **严格的**（`org_id` 多租户 + 值域 `CHECK` + 审计时间戳 + 索引） |
| **"压缩"只发生在列上** | 按准入规则砍掉**从未被写入**的列，数据全部留在 `payload` 里，**零丢失** |
| **大方向** | 服务整个系统，不迁就任何一方的实现 |

> ⚠️ **队友的表（`workspaces` / `workspace_sections` / `workspace_fine_review_rows` / …）是技法参考，不是替代品。**
> 我方标书表（`workspace` / `workspace_draft` / `bid_document` / `bid_section` / `bid_plan`）
> **表名与拆分保持不变**，只吸收他的 `payload` 保真手法。
>
> 样板 `workspace_sections`（49→16 列）是拿队友的表做的**技术验证**——验证的是
> 「`payload` + 准入规则」这套**手法可行**，不是"要用他的表"。

### 2.1.1 关于"压缩"的澄清（避免误读）

| 说法 | 含义 | 是否涉及删表 |
|:--|:--|:--:|
| 49 列 → 16 列 | `workspace_sections` 这**一张表**的**列**从 49 减到 16 | ❌ 不涉及 |
| 44 张空表 | 实测 0 行、无应用写入点 → 是**还没落地**，不是冗余 | ❌ **保留** |
| A 组 10 张 M3 镜像表 | ✅ **已删除**（从 001/005/007 移除 + `009` 清存量库） | ❌ **已删除** |

**没有任何一条是"删你的表"。** 唯一的列级收缩有实测依据：`bid_section` 的 49 列里 12 个 jsonb 列在真实 6 行中**全空**、多列从未被写入。

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
4. `docs/PostgreSQL表字段总清单与补表计划.md` —— 我方全字段清单
   ⚠️ 该文件仍是删除前口径（54 张，含 A 组）；**以本文档的 44 张为准**

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

**3. 按样板手法改造我方标书表**（每张都要跑五项验证）

> ⚠️ **表名与拆分用我方的**——下面的表是**改造对象**，不是"换成队友的表"。
> 样板 `workspace_sections` 只是拿队友的表做了**手法验证**（`payload` + 准入规则可行），手法照搬到我方表上。

| 我方表（改造对象） | 现状（本轮实测） | 要做的 |
|:--|:--|:--|
| **`bid_section`** | **49 列**，其中 12 个 jsonb 列在真实 6 行里**实填充 0** | ✅ **已拍板**：加 `payload jsonb`，按准入规则砍到 ~16 列 |
| `bid_document` | **45 列**，14 个 jsonb 列实填充 0（仅 `plan`/`deviation_tables` 有值） | 加 `payload`；列裁决同 `bid_section` 手法 |
| `bid_plan` | **仅 5 列**（`id`/`workspace_id`/`plan`/时间戳） | ⚠️ **本来就极小，无需收缩**，只加 `payload` |
| `workspace` | **仅 8 列** | ⚠️ **不膨胀**（"`fineReview.rows` 61 key" 的说法不成立，那些 key 从未建过列）→ 只加 `payload` |
| `workspace_draft` | **仅 6 列** | ⚠️ **不膨胀** → 只加 `payload` |
| `analysis_run` | **31 列，29 列真实填充 100%**（17 行真数据） | ⚠️ **不是"没人用的列"** → 只加 `payload` 兜契约外字段，**不做列收缩** |

**每张表的动作**：① 列出当前列 ② 按准入规则裁决保留哪些列 ③ 其余全部进 `payload jsonb` ④ 加 `org_id`/`CHECK`/时间戳/索引 ⑤ 用真实数据跑五项验证

> ⚠️ **列裁决只对有真实数据的表做**。27 张空表（`match_item` 64 列 / `tender_requirement` 71 列 /
> `requirement_group` 40 列 …）**一行数据都没有**，无从判断哪些列没人用 →
> **只加 `payload`（纯新增、零风险），不砍列**。详见 §4.6。

### P1 · 🔴 前置阻塞：db 模式要跑起来，队友的 schema 得先建

**这是本轮发现的硬阻塞**（§4.4）：`STORE_BACKEND=db` 一开就报 `relation "workspaces" does not exist`，
因为队友的 16 张表**在本地库里一张都没有**。上一轮合并的是**代码**，不是库。

三个选项（**待用户拍板**）：

| 选项 | 做法 | 影响 |
|:--|:--|:--|
| **① 建队友 schema**（共存） | 把 `scripts/tender-agent-migration/schema.sql` 建到本地库（或另建 `partner` schema） | 我方 44 张表**不受影响**（表名不冲突：`workspace` vs `workspaces`）；db 模式立刻可跑；但**两套模型并存**，需明确谁是权威 |
| **② 只建我方表，db 模式改用我方表** | 给 `PostgresBackend` 换实现，指向我方 G 组表 | 工作量最大，且**存储层原本就用队友的**（既定决策），会推翻 §存储层接缝 |
| **③ 暂不跑 db 模式** | 继续用 `json`/`dual`（dual 也需要队友表 → 实际只剩 `json`） | 零风险，但 `db` 模式**始终未验证**，交付目标未达成 |

> 我的建议：**选 ① 并把队友 schema 建到独立的 `partner` schema**（而不是 `public`），
> 这样"我方的表"和"队友的表"在库层面也物理隔离，谁是权威一目了然，
> 且完全满足"不许用队友的表替代我方表"的红线。

### P2 · 治理自检（建议做，是"漏列"的根治手段）

**4. 把裁决结果写进 `meta_column`**（L 组，**已有数据**：`meta_table` 44 / `meta_column` 893 / `meta_relationship` 52）
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

### P3 · 已拍板（2026-09-30，**结论已定，勿再讨论**）

**6. A 组 10 张 M3 镜像表 → ✅ 已执行：删除（不是"预留"，是真的删了）**
`fact_sync_log` / `organization` / `qualification_certificate` / `credential` / `performance_case` /
`staff_member` / `financial_evidence` / `technical_product` / `technical_parameter` / `evidence_file`

- **依据**：① 实测 **0 行、无任何 `.py` 代码读写**（仅 `005`/`007` 两个 SQL 文件引用过）
  ② 我方 `001` 自己写着「**M3 仍是权威源，本组表是只读镜像**」，队友也写着
  「**企业本体事实仍以 M3 图库为单一来源，本库不复制**」→ 两边技术判断一致
  ③ **空的镜像表是陷阱**：留着会诱导有人往里写，导致企业事实存在两份并与 M3 分叉
- **已完成动作**：
  | 对象 | 改了什么 |
  |:--|:--|
  | `001_schema_v1.sql` | 删除 L27–L254（10 张表 + 1 索引 + 段头注释）；现 26 张表（+`002` 12 张 = 38） |
  | `005_comments.sql` | 删除 10 行 `COMMENT ON TABLE` + 10 行 `meta_table` 种子；说明改「38 张表」 |
  | `007_bid_views_v2.sql` | `v_deviation_row` 的 `matched_facts` 不再 JOIN 镜像表，改从 `match_item_fact.fact_snapshot` 取名称/证据路径（输出形状不变） |
  | **`009_drop_company_mirror_tables.sql`**（新增） | 给已跑过旧 `001` 的库存量清理：DROP 10 表 + 清 `meta_*` 行；执行后须重跑 `007` |
  | 本地库 | 已执行 009 + 重跑 007：**表 54 → 44**，`meta_table` 54→44、`meta_column` 1082→893、`meta_relationship` 61→52，5 个视图全部重建，真实数据 10 张表行数逐张核对无变化 |
- **视图为何改得更好**：`fact_snapshot` 的注释是「命中时刻的企业事实快照（防事后被改）」——
  从快照取名称/证据路径**自包含**，且不会因 M3 变动而与历史结论分叉；JOIN 镜像表反而是多余且有风险的设计
- **恢复成本极低**：DDL 在 git 历史里，需要时一行 migration 就能加回
- ⚠️ 注意 `evidence_file` 与队友的 `evidence_files` 同名不同物——本组已删，证明材料走 M3 + 文件系统

**7. `bid_section` 的 49 列 → ✅ 决定：执行列级收缩（49 列 → ~16 列 + `payload`）**
- **这是列级改造**：表名、主键、行数、外键关系**全都不变**
- 依据：49 列里 12 个 jsonb 列在真实 6 行中**全空**，多列从未被任何代码写入
- 收缩后数据零丢失（全部保留在 `payload jsonb` 里），样板已验证（A-E 五项全 PASS）
- 执行时须遵守 §二 2.3 的准入规则，并把裁决结果登记进 L 组 `meta_column`

**8. 合并范围 → ✅ 决定：保持全合**（`origin/main` = `51186ea` 的 5 个提交全部保留）

---

## 四、关键事实（避免重复调研）

### 4.1 我方 44 张表的真实状态（实测行数，A 组删除后）

**只有 10 张有数据**：`meta_column`(893) / `meta_relationship`(52) / `meta_table`(44) / `tender_project`(27) /
`workspace`(18) / `workspace_draft`(18) / `analysis_run`(17) / `bid_section`(6) / `bid_document`(1) / `bid_plan`(1)

**其余 34 张全空，且没有任何一张有应用侧写入点。**（44 − 10 张有数据） 数据全部来自 `scripts/backfill_bid_writing.py` 回填。
→ 说明这些表是**设计目标**，不是**在用现状**；且**表数多不是问题**（按域拆实体是对的），**列数多才是**（已修）。

### 4.2 真实的标书数据结构（实测，纠正了之前的误判）

- 真实 `bidDocument.plan[]` 元素**只有 5 个 key**：`kind / note / title / sectionId / requirementIds`
  → 之前「`bid_section` 缺 ~50 列」的说法**是从列设计反推的，与真实持久化形态不符**
- 代码契约 `bid_writing/schemas.py::build_section`（L236-252）产出**精确 15 个 key**
- 一批字段是**派生**的，不该各自建列：`writingProfile`←(source,kind,responseType)、`responseType`←(source,kind)、`materialScopes`←writingProfile、`level`/`order`←归一化
- 我方 `bid_section` 的 49 列里，**12 个 jsonb 列在真实 6 行中全空**

### 4.3 队友设计的三个已知硬伤

| # | 问题 | 位置 | 状态 |
|:--|:--|:--|:--|
| 1 | 子表全局 `DELETE` + 重建（无 `WHERE`） | `postgres_backend.py::save_workspaces` | ✅ **本轮已修**（`33d0135`，见 §4.5） |
| 2 | 整份 payload 读写 → 多实例并发互相覆盖（他自己文档也写明） | `StorageBackend` 接口是集合级（`load_workspaces()` 全量 / `save_workspaces(dict)` 全量） | ⚠️ 未变（靠单实例保护缓解） |
| 3 | 16 张表里 6 张应用侧零访问（`parse_bundles`/`parse_artifacts`/`evidence_files`/`asset_files`/`app_config`/`runtime_state`） | 只由 `migrate_to_db.py` 写过一次 | ⚠️ 未变 |

> 硬伤 2 已由我方 **`5f80bce` 单实例保护（同一 runtime 目录禁止起第二个后端）** 缓解，该提交必须保留。

### 4.4 🔴 本轮重大发现：`db` 模式在本地库**根本跑不起来**（此前无人记录）

实测（`psycopg` 3.3.6 直连 5433）：

```
PostgresBackend(dsn).load_workspaces()
  -> UndefinedTable: relation "workspaces" does not exist
```

逐个核对队友的表，**全部 MISSING**：

| 表 | 本地库 |
|:--|:--|
| `workspaces` / `workspace_sections` / `workspace_fine_review_rows` / `workspace_requirement_coverage` | ❌ 不存在 |
| `catalog_projects` / `libraries` / `library_documents` / `reminders` / `automation_runs` | ❌ 不存在 |
| `parse_bundles` / `parse_artifacts` / `bid_runs` | ❌ 不存在 |

**本地库里的 44 张表全是我方的**（`public.workspace` / `public.workspace_draft` 是**我方**的 G 组表，
不是队友的 `workspaces` / `workspace_sections`）。唯一的 `workspace_sections` 在上轮样板建的
`fusion_sample` schema 里，是**验证用的临时表**，不是队友的表。

**含义（重要）**：

1. 上一轮"合并了队友的三态存储层"是**代码层面**的合并；**库层面队友的表一张都没建**。
   → `STORE_BACKEND=db` / `dual` 今天一开就报错，**`db` 模式从未真正跑通过**。
2. 队友的表结构在 `scripts/tender-agent-migration/schema.sql`（16 张表）与
   `shared-db/init/01-schema.sql`。按既有红线，**不能把它当目标 schema 直接执行**
   （§禁止事项），但**要跑 db 模式就必须有这张 schema 存在**——这是个待你拍板的岔路口。
3. 我方 44 张表与队友 16 张表是**两套并存的模型**，共用同一个库、表名不冲突
   （`workspace` vs `workspaces`、`workspace_section` vs `workspace_sections`），**可以共存**。

> ⏳ **待拍板**：db 模式要跑起来，队友的 schema 得先建。三个选项见 §九。

### 4.5 P0 完成记录（2026-09-30 本轮）

`backend/app/storage/postgres_backend.py::save_workspaces()` 的三处无 `WHERE` 的 `DELETE`
已改为 `DELETE ... WHERE workspace_id = ANY(%s)`。

**关键实现细节**：以 **`workspaces` 字典的键**为 `ws_ids`，而**不是**各子表 rows 里出现过的 id。
若某工作区这次没产生任何章节行，用子表推导的 id 集合会漏掉它，旧行就会残留；
用字典键才能保证"传入即重建"，**包括清空成 0 行的边界**。

**验证**（`_fusion_sample/verify_p0.py`，对真实库跑，自建 fixture 后清理）：

| 项 | 结果 |
|:--|:--|
| V1 局部化（只重建 A 时 B 的 3 行不受影响） | ✅ PASS |
| V2 语义等价（A 的内容 == 整份重建应有内容） | ✅ PASS |
| V3 清空边界（A 产出 0 行时旧行被清掉，B 不受影响） | ✅ PASS |
| V4 旧写法对照（无 WHERE → B 的 3 行被误删） | ✅ 复现出被修的缺陷 |
| 测试 A/B（89 条失败逐条相同） | ✅ 零行为回归 |

### 4.6 🔴 列膨胀的真实分布（本轮实测，与之前判断有出入）

实测列数（`_fusion_sample/column_audit.py`，含每列的**真实填充率**）：

| 表 | 行数 | **列数** | 12+ 个 jsonb 空列？ |
|:--|--:|--:|:--|
| `bid_section` | 6 | **49** | ✅ 是（12 个 jsonb 实填充 **0**） |
| `bid_document` | 1 | **45** | ✅ 是（14 个 jsonb 实填充 **0**，仅 `plan`/`deviation_tables` 有值） |
| `match_item` | 0 | **64** | 表空，无实测依据 |
| `requirement_group` | 0 | **40** | 表空 |
| `analysis_run` | 17 | **31** | ❌ **不是**：31 列里 **29 列真实填充 100%**，是真在用 |
| `match_assessment` | 0 | 31 | 表空 |
| `parse_bundle` | 0 | 27 | 表空 |
| `match_item_evidence` | 0 | 22 | 表空 |
| `tender_document` | 0 | 22 | 表空 |
| `tender_requirement` | 0 | **71** | 表空 |
| `agent_result` | 0 | 17 | 表空 |
| `template_fragment` | 0 | 15 | 表空 |
| `evaluation_item` | 0 | 12 | 表空 |
| `workspace` | 18 | **8** | ❌ 不膨胀 |
| `workspace_draft` | 18 | **6** | ❌ 不膨胀 |
| `bid_plan` | 1 | **5** | ❌ 不膨胀 |

**三个必须纠正的认知**：

1. ❌ **`workspace` / `workspace_draft` 根本不膨胀**（8 列 / 6 列）——交接文档里
   "`workspace` 加 payload；`fineReview.rows` 61 key 是重头"的说法**不成立**：
   这两个表本来就小，`fineReview.rows` 从来没有被建过列。
   → 这两张表只需要**加 `payload` 列**，没有"列收缩"可做。
2. ⚠️ **`analysis_run` 的 31 列里 29 列真实填充 100%**（17 行真数据，F 组核心可观测表）。
   它**不是**"没人查的列"——按准入规则，这些列**应该保留**。
   → 这张表只需要**加 `payload`** 兜住契约外字段，**不做列收缩**。
   之前把它列进"要改造"的清单，容易被误读成要砍列，特此澄清。
3. ✅ **真正该做列收缩的只有 `bid_section`（49 列）和 `bid_document`（45 列）**，
   依据是**实测填充率为 0 的 jsonb 列**，而不是"列数多"。

> **空表（27 张）无实测依据**：`match_item`(64 列) / `tender_requirement`(71 列) /
> `requirement_group`(40 列) 等表**一行数据都没有**，无从判断哪些列没人用。
> 按既定红线，**不能因为"列多"就砍**——只能**加 `payload`**（新增，零风险），
> 列裁决留到有真实数据之后再按准入规则做。

---

## 五、环境与工具约束（踩过的坑，勿重复）

### 5.1 数据库与 Python 环境

| 项 | 值 |
|:--|:--|
| **Python 环境** | ✅ **统一用 `finance`**：`D:\APP\anaconda\envs\finance\python.exe`（用户指定，2026-09-30） |
| 本地库（我方） | `postgresql://tender_app:tender_app_dev@127.0.0.1:5433/tender_agent`，容器 `tenderagent-pg` |
| 队友库 | `tender_agent_dev`，容器 `tender-pg`，端口 5432 |
| 连接方式 | **用 Python 走 TCP 5433**，**不要用 docker CLI**——沙箱禁 Docker 命名管道 |
| 驱动 | 应用代码用 **`psycopg` v3**（`postgres_backend.py::_connect` → `psycopg.connect(dsn)`，见 `requirements.txt` 的 `psycopg[binary]`）<br>临时脚本用 `pg8000`（`_fusion_sample/lib/` 内有解包版，`sys.path.insert` 即可） |
| `psql` | 宿主机没有；容器内有 |

> **`finance` 环境现状（实测）**：Python 3.12.14；已装 `pytest 9.1.1`、`fastapi 0.115.14`、
> `uvicorn 0.52.4`、`pydantic 2.13.5`、`python-docx 1.2.0`、`requests 2.34.2`。
> ✅ **已验证可用**：`finance` 环境跑全量测试得 **89 failed / 407 passed / 16 skipped**，
> 与 base 环境结果**完全一致**，且耗时 **22.84s**（base 为 70.82s，快约 3 倍）。
> ⚠️ **`psycopg` / `psycopg2` / `pg8000` 均未安装** → 要连库先补装：
> ```powershell
> & "D:\APP\anaconda\envs\finance\python.exe" -m pip install "psycopg[binary]>=3.1,<4"
> ```
> 沙箱下写 `D:\APP\anaconda\envs\finance` 可能被拒（该路径在工作区外）→ 提权重试一次，
> 或退回「下载 wheel + `zipfile` 解到工作区 + `sys.path.insert`」的老办法
> （`_fusion_sample/lib/` 已有可用样例）。

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
# 第二轮的两张表已有专门的验证脚本（推荐直接用）
& "D:\APP\anaconda\envs\finance\python.exe" _fusion_sample\verify_bid_section.py
& "D:\APP\anaconda\envs\finance\python.exe" _fusion_sample\verify_bid_document.py

# 第一轮的样板（队友表 workspace_sections 的手法验证）
python E:\code\uinoproject\tenderagent\_fusion_sample\roundtrip.py
Get-Content E:\code\uinoproject\tenderagent\_fusion_sample\report.txt -Encoding UTF8
```

**五项验证（新表照做）**：

| 项 | 内容 | 样板结果 | bid_section | bid_document |
|:--|:--|:--|:--|:--|
| A 往返保真 | payload 读回 == 原始 JSON（逐字节） | 6/6 PASS | ✅ PASS | ✅ PASS |
| B 投影正确 | 提升列 == payload 派生值 | 6/6 PASS | ✅ PASS | ✅ PASS |
| C 完整性 | 代码契约 key vs 实际落位，无遗漏 | 15/15 全覆盖 | ✅ 108/108 | ✅ 40/40 |
| D 规格生效 | D1 脏值不阻塞写入 / D2 CHECK 拦截非法值 / D3 唯一键 | 全 PASS | ✅ PASS | ✅ PASS |
| E 加字段免迁移 | 新增契约外 key，列数不变且完整读回 | PASS | ✅ PASS | ✅ PASS |

> ⚠️ **A 项的判定口径**：payload 是**超集**——权威源的 key 必须逐字节保留（零丢失），
> 另外允许携带从投影列补回的**真实非空值**。判定时查的是"权威源有而 payload 没有的 key"。
> ⚠️ **回填 payload 必须从权威源重建**（`bid_plan.plan[]` 那种原始持久化形态），
> **不能从列反推**——否则会丢 key（`note`）并凭空造出默认值（`agentResults: []`）。`012` 就是修这个的。

---

## 七、交接提示词（整段复制给新对话）

```
你接手继续做「招投标智能体」项目的 PostgreSQL 数据库构建。
前两轮已完成合并、方案定稿、db 模式打通、两张表的列级收缩。请先读文档再动手。

## 第一步：读这些文档（按顺序，都在仓库 docs/ 下）
1. docs/PG数据库建设-本轮交付报告.md            ← 【最新】第二轮交付全貌（先读这个）
2. docs/融合方案样板验证-workspace_sections.md   ← 样板 + 实测数据 + 复现脚本
3. docs/PG表设计融合方案-规范化与payload保真.md   ← 融合原则
3. docs/PG迁移合并方案-以队友存储层为准.md        ← 存储层审计与冲突解法
4. docs/PG数据库建设-后续工作计划与交接提示词.md  ← 本文件的完整版（含全部待办与坑）

## 项目位置
- 工作区根：E:\code\uinoproject\tenderagent
- 代码根（git 仓库）：E:\code\uinoproject\tenderagent\tender-agent\tender-agent
- 当前分支：integrate/pg-fusion（第二轮 HEAD **06d5733**）
- Python 环境：**统一用 `finance`** → D:\APP\anaconda\envs\finance\python.exe
  （实测已装 pytest/fastapi/uvicorn/pydantic/docx/requests，
   **以及 psycopg 3.3.6 —— 不需要再补装**）
- 本地库：postgresql://tender_app:tender_app_dev@127.0.0.1:5433/tender_agent
  （容器 tenderagent-pg；用 psycopg v3 直连，不要用 docker CLI）
- 当前表数：**public 44 张**（我方目标模型）+ **partner 16 张**（队友脚手架，第二轮新建）
- ⚠️ 每次 `git commit` 后 git hook 在沙箱内会报错（Win32 error 5），
  但**提交其实成功了**，用 `git log` 确认，不要重复提交

## 前两轮做了什么（供你了解仓库现状）

### 第一轮
合并 origin/main 的 5 个提交，其中**只有 1 个是数据库相关**：
  a5b2e95  storage 三态后端 + 21 个迁移文件          ← 数据库（核心）
  62fae6d  伙伴接入指南 + .gitattributes              ← 迁移文档（周边）
  e66cb67  证明材料一键填充（bid_writing + 前端）      ← 标书功能，与数据库无关
  f53a6d7  现状盘点文档                               ← 文档
  51186ea  agent_bridge 残留引用清理                  ← 代码清理，与数据库无关
store.py 的存储接缝已统一走队友的 self.backend（json/dual/db 三态），
并保留了我方的 self._facts_repo（M3 企业事实）与数据懒加载/缓存优化。
另：删除 A 组 10 张 M3 镜像表（54 → 44 张）。

### 第二轮（7 个提交 33d0135 → 06d5733）
1. 【重大】发现 **db 模式从来没跑通过**：队友 16 张表在本地库**一张都没建**，
   `STORE_BACKEND=db` 一开就报 `relation "workspaces" does not exist`
   （连 dual 也跑不起来，实际一直只有 json 可用）。
   → 新增 `backend/migrations/010_partner_scaffold.sql` 把队友 16 张表建到**独立 schema partner**，
     并给 `PostgresBackend` 加 schema 参数 + `PG_SCHEMA` 环境变量。**db 模式首次跑通**。
2. P0：修 `save_workspaces()` 三处无 WHERE 的 DELETE → 按 `workspace_id` 局部重建。
3. `bid_section` 列级收缩 **49 → 17 列**（-65%），五项验证全 PASS。
4. `bid_document` 列级收缩 **45 → 14 列**（-69%），五项验证全 PASS。
5. 其余 42 张表统一加 payload（纯新增）→ **44/44 张表 payload 全覆盖**。
6. 治理自检：`015_meta_column_projection.sql` + `scripts/check_projection_coverage.py`。

## 不要提交这 3 个文件（另一个对话正在改）
docs/PostgreSQL表字段总清单与补表计划.md
docs/标书表替换可行性核对.md
scripts/backfill_bid_writing.py
（提交时不要用 `git add docs/` 这种整目录方式，会把这几个文件扫进去——已踩过这个坑）

## 最高优先级的约束（先读这条，违反即错）
1) **我方的 44 张表一张都不删、不改名、不合并、不用队友的表替代。** 它们是本项目的目标数据模型。
   原有 54 张；其中 A 组 10 张 M3 镜像表已按用户决定**删除**并完成（见第 5 条）——
   那是**唯一一处删表**，此后不得再删任何表。
2) 队友的表（workspaces / workspace_sections / workspace_fine_review_rows / bid_runs /
   parse_bundles …）**只是技法参考 + 存储层脚手架**。参考的是他的手法，不是他的表：
     - payload jsonb 全保真（结构多变的字段全塞 payload，不丢数据）
     - position 排序列 / coalesce_cols（不覆盖 created_at）/ touch_col（只在真变化时改 updated_at）
     - deleted_at 墓碑 / json-dual-db 三态开关
   这些手法要**加到我方的表上**，不是把我方的表换成他的。
   队友那 16 张表建在 **partner schema**（不是 public），是**脚手架**，
   最终统一时 `DROP SCHEMA partner CASCADE` 一句清掉即可。
3) **"压缩"只发生在列上，不发生在表上。** 已实测的收缩（第二轮执行）：
   - `bid_section`  **49 → 17 列（-65%）** — 12 个 jsonb 列在真实 6 行里实填充 0
   - `bid_document` **45 → 14 列（-69%）** — 35 列全空
   依据是**实测填充率为 0**，不是"列看起来多"。
   （样板表 `workspace_sections` 的 49 → 16 是队友表的**手法验证**，不是要换成他的表。）
   数据全部保留在 payload 里 → **零丢失**。
4) 空表（0 行、无应用写入点）是**还没落地**，不是冗余 → **保留**，按域逐步落地。
   ⚠️ **空表不做列裁决**：没有实测依据，不能因为"列看起来多"就砍，
   否则会把将来真会用的查询能力砍掉。空表统一**只加 payload**。
5) **三处认知已被实测推翻（勿再沿用旧说法）：**
   - `workspace`（8 列）/ `workspace_draft`（6 列）/ `bid_plan`（5 列）**根本不膨胀**，
     "fineReview.rows 61 key 是重头"的说法**不成立**（那些 key 从未建过列）
   - `analysis_run` **31 列里 29 列真实填充 100%**，是**真在用**的列 → **不做列收缩**
   - 真正该收缩的只有 `bid_section` 和 `bid_document`
5) **已拍板且已执行的结论（勿再讨论）：**
   - A 组 10 张 M3 镜像表 → **已删除**（不是"预留"）。
     已完成：`001` 删 L27-L254、`005` 删 10 行注释+10 行 meta 种子、`007` 的 v_deviation_row
     改从 `match_item_fact.fact_snapshot` 取名称/证据路径、新增 `009_drop_company_mirror_tables.sql`
     清存量库、本地库已执行（表 54→44，meta_* 同步收缩，视图全部重建）。
     **企业事实继续由 M3 图库提供**（它是唯一权威源）。
   - `bid_section` 49 列 → ✅ **已执行**列级收缩为 **17 列 + `payload`**（表名/主键/行数/外键全不变）。
   - `bid_document` 45 列 → ✅ **已执行**列级收缩为 **14 列 + `payload`**。
   - 其余 42 张表 → ✅ **已加 `payload`**（纯新增）→ **44/44 张表 payload 全覆盖**。
   - `db` 模式 → ✅ **已打通**（`010_partner_scaffold.sql` 建 partner schema + `PG_SCHEMA`）。
   - 合并范围 → **保持全合**（origin/main 的 5 个提交全部保留）。

## 设计原则（已定，勿再讨论）
1) 表用我方的（44 张，按域拆分；A 组 10 张镜像表已删），保真技法借队友的，规格标准用严格的
   （org_id 多租户 + 值域 CHECK + 审计时间戳 + 索引）。大方向是服务整个系统，不迁就任何一方实现。
2) 核心原则：payload 是事实（全保真，永不加约束）；提升列是投影（归一 + 约束，允许为空）。
   写入流程：先写 payload（永不失败）→ 再归一填提升列 → 归一失败留 NULL + 记治理告警。
   CHECK 一律写成 CHECK (col IS NULL OR col IN (...))。
   ⚠️ **回填 payload 必须从权威源重建，不能从列反推、不能注入默认值**：
   `011` 曾用 `jsonb_build_object` 从列拼 payload，结果 ① 丢了只存在于 `bid_plan.plan[]` 的
   `note` key ② 把 `agentResults: []` 等 18 个**真实数据里不存在**的 key 写了进去（凭空造事实）。
   已被 `012` 修正。**投影可以被重建，事实不能被编造。**
3) 提升列准入规则（满足至少一条才建列）：要 WHERE / 要 JOIN / 要 ORDER BY / 要参与唯一键 /
   要 CHECK / 要建索引 / 要进治理元数据。其余一律进 payload。
   这条规则用于**删列**（把没人用的列收进 payload），不用于删表。

## 任务

### ✅ 第二轮已完成
- P0 子表局部重建（`postgres_backend.py::save_workspaces`）
- db 模式打通（`010` partner schema + `PG_SCHEMA`）
- `bid_section` 49→17 列、`bid_document` 45→14 列（各五项验证全 PASS）
- 其余 42 张表加 payload（44/44 全覆盖）
- 治理自检（`015` meta_column 登记 + `scripts/check_projection_coverage.py`）

### P1 · `parse_bundle` 是否进库（**唯一还待用户拍板**）
db 模式下 ParseBundle 仍读磁盘（`store.get_parse_bundle/save_parse_bundle` 读写
`runtime/projects/<id>/parse/parse-bundle.json`），而队友的 StorageBackend 没有 parse_bundle 方法。
要么给 StorageBackend 加 `load/save_parse_bundle`，要么明确接受"解析产物留盘"。
（第二轮已把 `parse_bundle` 表加好 payload，投影列随时可补。）

### P2 · 27 张空表的列裁决
等有真实数据后按准入规则做。**当前只加 payload，不砍列。**

### P2 · 应用写入路径切到 payload 模式
代码目前仍写"列"；需按域逐步改成"先写 payload → 再归一填提升列"。
这才是"一点点迁移"的部分（表结构已一次到位）。

### 第二轮改造明细（供参考，均已执行）

| 表 | 第二轮实测 | 已做的 |
|:--|:--|:--|
| `bid_section` | 49 列，12 个 jsonb 列实填充 0 | ✅ **49 → 17 列** + payload + 3 CHECK + 3 索引 |
| `bid_document` | 45 列，35 列全空 | ✅ **45 → 14 列** + payload + 2 CHECK + 3 索引 |
| `bid_plan` | **仅 5 列** | ✅ 只加 payload（**本来就极小，无列可收缩**） |
| `workspace` | **仅 8 列** | ✅ 只加 payload（"`fineReview.rows` 61 key" 说法**不成立**） |
| `workspace_draft` | **仅 6 列** | ✅ 只加 payload |
| `analysis_run` | 31 列，**29 列真实填充 100%** | ✅ 只加 payload（**不做列收缩**） |
| 其余 36 张表 | 多为 0 行 | ✅ 只加 payload（**空表不做列裁决**） |

## 禁止事项（违反即错）
- ❌ 不许删任何表、改任何表名、把两张表合并成一张
  （**唯一的例外是 A 组 10 张 M3 镜像表，已按用户决定删除并完成**；此后再无例外）
- ❌ 不许用队友的表（`workspaces` / `workspace_sections` / `bid_runs` / `parse_bundles` …）替代我方的表。
  队友那 16 张表建在 **`partner` schema**，是**存储层脚手架**，不是目标模型；
  最终统一时 `DROP SCHEMA partner CASCADE` 一句清掉。
- ❌ 不许因为"表太多"就建议精简——空表是**未落地**，不是冗余
- ❌ 不许把"列压缩"（49→17 列）说成或做成"表压缩"
- ❌ 不要把队友的 schema.sql / migrate_to_db.py 当成目标 schema 直接执行

## 硬性约束（踩过的坑，务必遵守）
- ❌ 不要跑 scripts/tender-agent-migration/migrate_to_db.py（读本地 runtime/*.json，跨机读到空）
- ❌ 不要用 PowerShell `>` 导出 pg dump（实测把 7.1MB 写成 13.6MB 损坏文件）
- ❌ 不要用 `docker compose`（空格），用 `docker-compose`
- ⚠️ **Python 统一用 finance 环境**：D:\APP\anaconda\envs\finance\python.exe
  （✅ 第二轮实测**已装 `psycopg 3.3.6`**，不需要再补装）
- ⚠️ `db` / `dual` 模式必须设 **`PG_SCHEMA=partner`**，否则报 `relation "workspaces" does not exist`
- ⚠️ docker / git push / git fetch 在沙箱下被拒 → 提权重试一次即可
- ⚠️ 往 finance 环境 pip 装包可能被拒（路径在工作区外）→ 提权重试，或退回
  「下载 wheel + zipfile 解到工作区 + sys.path.insert」（_fusion_sample/lib 有样例）
- ⚠️ 控制台 GBK 会乱码 → 中文输出写 UTF-8 文件再读
- ⚠️ 容器首次启动可能 exit 127（CRLF 导致），再 `up -d` 一次即可
- ⚠️ **git commit 在沙箱内报错但提交其实成功了**（hook 报 Win32 error 5 / RPCRT4.dll），
  用 `git log` 确认，不要重复提交
- ⚠️ 写迁移 SQL：psycopg 一次 `execute` 可跑多语句；**标识符拼进 SQL 前要校验**
  （schema 名用白名单正则，标识符不能参数化）
- ⚠️ 做 D 组负向测试要用**真实外键值**（`bid_section`→`bid_document`、`bid_document`→`workspace`
  都有 FK），否则会被 ForeignKeyViolation 挡住而**误判为产品缺陷**
- ⚠️ 不要用 PowerShell here-doc（`<<'EOF'`）写 git commit message → 非法；
  写成文件后用 `git commit -F <file>`

## 测试基线
cd E:\code\uinoproject\tenderagent\tender-agent\tender-agent
& "D:\APP\anaconda\envs\finance\python.exe" -m pytest backend/tests -q --no-header -p no:cacheprovider
当前 89 failed / 407 passed / 16 skipped（89 个失败是既有问题，非本轮引入）
每改一处都要跑测试，并用失败清单 A/B 对照确认失败集合同源
（基准清单：_fusion_sample/baseline_failures.txt）

## 验证脚本（第二轮新增，可直接复跑）
_fusion_sample/verify_p0.py                    P0 局部重建 V1~V4
_fusion_sample/verify_dbmode.py                db 模式端到端
_fusion_sample/verify_bid_section.py           bid_section 五项验证
_fusion_sample/verify_bid_document.py          bid_document 五项验证
_fusion_sample/verify_checker_detects.py       证明校验脚本真能发现缺口（反向测试）
scripts/check_projection_coverage.py           治理完整性校验
_fusion_sample/column_audit.py                 列清单 + 真实填充率（裁决依据）
_fusion_sample/backup_bid_tables.json          改动前的原始数据备份（回滚用）

## 工作方式要求
- 每完成一项就同步文档（说清改了什么、在哪个目录），该删的删、该补的补
- 改动要可验证、可回滚；不确定的先问我，不要自己拍板
- 中文回复
```

---

## 八、本轮完成与遗留

### 已完成 · 第一轮（2026-09-30）

| # | 事项 | 结果 |
|:--|:--|:--|
| A | 拉取 `origin/main` = `51186ea` 并合并 | ✅ 分支 `integrate/pg-fusion`，唯一冲突 `store.py`（6 处）已解 |
| B | 原子写回归 | ✅ `storage/json_backend.py` 改用 `repository/io.py` |
| C | 零行为回归验证 | ✅ worktree A/B：合并前后**失败集合逐条相同**（89=89） |
| D | 文档口径统一（"压缩表"误解） | ✅ 4 份文档全部改口径 + 新增「禁止事项」「最高优先级约束」 |
| E | 三项拍板固化 | ✅ A 组 / bid_section / 合并范围 |
| F | **A 组 10 张 M3 镜像表删除** | ✅ `001`/`005`/`007` 已改 + 新增 `009` + 本地库已执行（**54 → 44 张**） |
| G | Python 环境统一 `finance` | ✅ 已写入文档（含缺 `psycopg` 的补装说明） |

### 已完成 · 第二轮（2026-09-30，HEAD `06d5733`，7 个提交）

| # | 提交 | 结果 |
|:--|:--|:--|
| A | `33d0135` | ✅ **P0**：`save_workspaces` 三处无 `WHERE` 的 `DELETE` → 按 `workspace_id` 局部重建；V1~V4 验证（含清空成 0 行的边界） |
| B | `0523f41` | ✅ 文档：记录 P0 + db 模式硬阻塞 + 列膨胀实测定正 |
| C | `1d3eefe` | ✅ **db 模式首次跑通**：`010_partner_scaffold.sql`（队友 16 表 → `partner` schema）+ `PG_SCHEMA` 支持 + 端到端验证 |
| D | `272c8b5` | ✅ **`bid_section` 49 → 17 列**（-65%），五项验证全 PASS（`011` + `012` 修正） |
| E | `ca50445` | ✅ **`bid_document` 45 → 14 列**（-69%），五项验证全 PASS（`013`） |
| F | `5361de8` | ✅ 其余 42 张表加 payload（纯新增）→ **44/44 全覆盖**（`014`） |
| G | `06d5733` | ✅ **治理自检**：`015` meta_column 登记 + `scripts/check_projection_coverage.py`（含反向测试） |

> 全程 **44 张表未变**；测试基线**全程 89 failed 逐条相同** → 零行为回归。

### 遗留（未做，明确记录）

| # | 事项 | 状态 |
|:--|:--|:--|
| 1 | 子表局部重建（P0） | ✅ **第二轮已完成** |
| 2 | `parse_bundle` 读写方法（是否让 ParseBundle 进库） | ⏳ **唯一还待拍板的**（见 §三 P1） |
| 3 | 标书表按样板手法改造 | ✅ **第二轮已完成**（`bid_section` / `bid_document` 列收缩 + 42 表加 payload） |
| 4 | `meta_column` 登记 + 完整性校验脚本 | ✅ **第二轮已完成** |
| 5 | 27 张空表的列裁决 | ⏳ 等有真实数据后按准入规则做（**当前只加 payload，不砍列**） |
| 6 | 应用写入路径切到 payload 模式 | ⏳ 需按域逐步改（"一点点迁移"的部分） |
| 7 | `meta_column` 的 `value_domain` / `semantic_type` 全量补齐 | ⏳ 第二轮只补了 bid_section / bid_document |
| 8 | 队友表最终被我方模型吸收（统一版） | ⏳ 届时 `DROP SCHEMA partner CASCADE` + 改 `PostgresBackend` 指向我方表 |
| 9 | `docs/PostgreSQL表字段总清单与补表计划.md` 仍是删除前口径（54 张、含 A 组） | ⚠️ 该文件被另一对话占用，暂不动；**以本文档的 44 张为准** |
| 10 | 本地 `_baseline_wt` 目录 | 已从 git 注册表移除；剩 6 个沙箱临时目录删不掉，在仓库外无害 |

### 第二轮新增的验证脚本（可直接复跑）

| 脚本 | 用途 |
|:--|:--|
| `_fusion_sample/verify_p0.py` | P0 局部重建 V1~V4（含旧写法对照） |
| `_fusion_sample/verify_dbmode.py` | db 模式端到端（6 load + save 往返 + 幂等） |
| `_fusion_sample/verify_bid_section.py` | `bid_section` 五项验证 |
| `_fusion_sample/verify_bid_document.py` | `bid_document` 五项验证 |
| `_fusion_sample/verify_checker_detects.py` | **反向测试**：证明校验脚本真能发现缺口 |
| `scripts/check_projection_coverage.py` | 治理完整性校验（代码 key ↔ 库列 ↔ meta_column） |
| `_fusion_sample/column_audit.py` | 列清单 + 真实填充率（准入裁决依据） |
| `_fusion_sample/backup_bid_tables.json` | **改动前的原始数据备份**（回滚用） |



