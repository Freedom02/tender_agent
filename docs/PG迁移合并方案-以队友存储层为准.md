# PG 迁移合并方案（存储层用队友的，表规格用严格的）

> ⚠️ **决策已定稿（2026-09-30，最新一次修正覆盖之前所有说法）**
>
> 用户原话：「**他的只做参考，大方向还是为了最后服务整个系统。**」
>
> **三条定论（本文与所有 PG 文档都按这个读）：**
>
> 1. **我方 54 张表一张都不删、不改名、不合并。** 它们是本项目的目标数据模型。
>    本文 **§步骤 3** 里所有标「❌ 退役」的结论**全部作废**，已在该节就地改写。
> 2. **队友的表只提供技法参考**（`payload` 全保真 / `position` / `coalesce_cols` /
>    `touch_col` / `deleted_at` 墓碑 / `json`-`dual`-`db` 三态开关），
>    **不是替代品**。技法加到**我方的表**上，不是把我方的表换成他的。
> 3. **「收缩」只发生在「列」上**（按准入规则把从未被写入的列收进 `payload`），
>    **绝不发生在「表」上**。唯一实测过的收缩是样板表 `workspace_sections`
>    的**列**从 49 减到 16（-67%），代码契约 15 个 key 零丢失
>    （依据：其中 12 个 jsonb 列在真实数据里全空）。详见
>    `docs/PG表设计融合方案-规范化与payload保真.md` 与
>    `docs/融合方案样板验证-workspace_sections.md`。
>
> **存储层仍然用队友的**（这是本文标题的含义，且未变）：`store.py` 的接缝统一走
> `self.backend`（json/dual/db 三态），我方另留 `self._facts_repo`（M3 企业事实）。
> 本文其余部分（审计结论、store.py 冲突解法、两处必须补的回归）**仍然有效**。
>
> ---
>
> 本文是审计结论 + 可执行的合并步骤。
>
> 审计对象：分支 `origin/fix/evidence-fill`（比 `origin/main` 多 4 个提交，2026-09-30）
> 我方基线：分支 `integrate/merge-origin-main`（= `origin/main` + 15 个提交）
> 合并结果：分支 `integrate/pg-fusion`（`origin/main` = `51186ea` 已合入）

---

## 一、审计结论：能用，但有三处必须知道

### 1.1 ✅ 标书模块在他的设计下**功能零改动即可运行**

他的 `PostgresBackend.load_workspaces()` 只做一件事：

```sql
SELECT id, payload FROM workspaces WHERE deleted_at IS NULL ORDER BY position NULLS LAST, id
```

`payload` 是**完整 workspace 对象**（`_workspace_rows()` 里 `"payload": ws`），包含：

| 标书要读/写的东西 | 在哪 | 状态 |
|:--|:--|:--|
| `bidDocument` 全部 37 个 key | `workspaces.payload.bidDocument` | ✅ 无损 |
| `bidDocument.plan[]`（章节数组，~50 key/节） | `payload.bidDocument.plan` + 投影到 `workspace_sections.payload` | ✅ 无损 |
| `reasoning.fineReview.rows[]` | `payload.reasoning.fineReview` + 投影到 `workspace_fine_review_rows.payload` | ✅ 无损 |
| `requirementCoverage[]` | `payload.bidDocument.requirementCoverage` + 投影到 `workspace_requirement_coverage.payload` | ✅ 无损 |
| `deviationTables` / `proofread` / `complianceReview` / `submissionStructure` / `templateFragments` … | 全在 `payload.bidDocument` | ✅ 无损 |
| `evidence`（派生串）/ `parameterMatches` / `qualificationMatches` | 从不落库，读取时现算（两套设计都一样） | — |

**结论：`db` 模式下 `Store.workspaces` 被全量载入内存，标书代码操作的仍是内存对象，行为与 JSON 模式完全一致。** 这是他的 payload 保真设计带来的最大好处——**我们那一整套「缺 37 列 / 缺 50 列」的缺口问题，在他的设计里不存在**。

### 1.2 ⚠️ ParseBundle 仍留磁盘（他有意为之，不是 bug）

他的 `store.py` 里 `save_parse_bundle` / `get_parse_bundle` **仍然读写文件**：

```python
target = self.runtime_root / "projects" / safe_project_id / "parse" / "parse-bundle.json"
```

他的 `storage/base.py` 明确划了范围：

> 只负责「结构化业务状态」的读写；**二进制文件（证明材料、资料文件、解析产物、导出文件）始终留在磁盘上**，不经过这里。config（含 API Key）也留在磁盘上。

**含义**：切到 `db` 模式后，标书要读的 `submissionStructure` / `evaluationItems` / `templateFragments` / `documentBlocks` **仍然来自磁盘文件**。

### 1.3 ⚠️ 他的 16 张表里，**6 张在应用侧没有任何读写路径**

我查了 `postgres_backend.py` 全 509 行，对以下表名**零命中**：

| 表 | 应用侧访问路径 |
|:--|:--|
| `catalog_projects` `libraries` `library_documents` `workspaces` `workspace_sections` `workspace_fine_review_rows` `workspace_requirement_coverage` `reminders` `automation_runs` | ✅ 经 `StorageBackend` |
| `bid_runs` | ✅ 经 `cloud_agent/run_repository.py`（**另一套独立抽象**，不走 StorageBackend） |
| **`parse_bundles` `parse_artifacts` `evidence_files` `asset_files` `app_config` `runtime_state`** | ❌ **无访问方法** —— 只由 `migrate_to_db.py` 写过一次 |

→ 这 6 张表当前是「**迁移/查询用的静态副本**」，应用不读不写。

---

## 二、三处硬伤（接受他的方案就要接受，或修掉）

### 2.1 🚨 子表每次保存**全局 DELETE + 全量重建**（无 WHERE）

`postgres_backend.py::save_workspaces()`：

```python
cur.execute("DELETE FROM workspace_requirement_coverage")   # 注意：没有 WHERE
cur.execute("DELETE FROM workspace_fine_review_rows")
cur.execute("DELETE FROM workspace_sections")
_upsert_rows(cur, "workspace_sections", sec_rows, ...)      # 再全量插回
```

而且 `load_workspaces()` **根本不读这三张子表**——它们是**只写不读的派生投影**。

**代价**：每次 `persist_runtime()`（保存标书章节、改匹配状态等都会触发）都要把**所有工作区**的章节 / 细评审 / 覆盖行**全删全插**。
- 现在规模（18 章节 / 286 细评审行）无所谓
- 规模上去就是 **O(总行数) / 次保存**
- **修法**：改成 `DELETE ... WHERE workspace_id = ANY(%s)`，只重建本次变化的工作区

### 2.2 🚨 整份 payload 读写 → 并发不安全

读 = 全量 `payload`，写 = 全量 `payload`，两个应用实例会互相覆盖。**他自己在 `PARTNER_ONBOARDING.md` 里写明了**：

> `STORE_BACKEND=dual` 是"读本地 JSON、写双份"，两个应用实例各自读写自己的 `runtime/`，再各自把整份状态覆盖进同一个库。共用时应**只由一个人跑应用**。

✅ **正好我方的 `5f80bce feat(runtime): 单实例保护 —— 同一 runtime 目录禁止起第二个后端` 能缓解这个硬伤，这个提交必须保留。**

### 2.3 🚨 他的 `json_backend.py` 丢掉了原子写（回归风险）

他的实现是朴素写：

```python
def _write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(...), encoding="utf-8")     # ← 直接写，非原子
```

我方的 `backend/app/repository/io.py` 是**原子写**（临时文件 + `os.replace`）。`workspaces.json` 有 **约 1GB**，写到一半崩溃就是整份损坏。

→ **合并时必须把原子写补回他的 `json_backend.py`**（让 `json_backend` 复用 `repository/io.py` 的 `read_json`/`write_json`，或把原子逻辑搬进去）。

> 这个坑我们踩过一次：上次 git 合并把 `store.py` 的原子写换成了朴素写，是事后恢复的。

---

## 三、合并步骤

### 步骤 1：合并分支（冲突只有一个文件）

已用 `git merge-tree` 实测：

```
CONFLICT (content): Merge conflict in backend/app/store.py     ← 唯一冲突
Auto-merging frontend/src/shared/types.ts                       ← 自动合并
```

```powershell
git switch -c integrate/pg-his-storage        # 从当前 integrate/merge-origin-main 开
git merge origin/fix/evidence-fill
```

**为什么只有一个冲突**：两边改的文件基本不重叠——
- 他的：`backend/app/storage/`（新包）、`backend/app/cloud_agent/run_repository.py`（新）、`scripts/tender-agent-migration/`（新）、`store.py`
- 我方的：`backend/migrations/001~008`（新）、`backend/app/repository/`（新）、`docs/`、`store.py`、`scripts/`

**唯一重叠 = `store.py`，因为两套抽象打在同一处接缝上。**

### 步骤 2：`store.py` 冲突解法（保留他的接缝 + 保留我方的非接缝改动）

两边的接缝对照：

| 存储访问点 | 他的写法 | 我方的写法 | 采用 |
|:--|:--|:--|:--:|
| catalog | `self.backend.load_catalog()` / `save_catalog()` | `self._catalog_repo.load()` / `save()` | **他的** |
| workspaces | `self.backend.load_workspaces()` / `save_workspaces()` | `self._workspace_repo.load()` / `save()` | **他的** |
| libraries / reminders / automation_runs / deleted_workspaces | `self.backend.load_*()` / `save_*()` | （原来就是裸 `read_json`/`write_json`） | **他的** |
| **M3 企业事实** | （无此方法） | `self._facts_repo`（`M3FactsRepository`，直连 M3 图库） | **我方的** |

**结论：合并后的接缝只有一个 `self.backend` + 一个 `self._facts_repo`。** 我方的 `_catalog_repo` / `_workspace_repo` 退役（被 `backend` 覆盖），`_facts_repo` 保留（他的 `StorageBackend` 接口里没有 facts，因为 M3 是外部图库、不是 runtime JSON）。

**同时必须保留我方 `store.py` 的非接缝改动**：

| 提交 | 内容 | 为什么保留 |
|:--|:--|:--|
| `5f80bce` | 单实例保护（同一 runtime 目录禁止起第二个后端） | 🚨 **补他的并发硬伤（§2.2）** |
| `d19d0f4` | 同一份文件重复导入时复用既有条目 | 独立 bug 修复 |
| `71c81eb` | LLM 调用失败不再静默，暴露到工作区 | 独立功能 |
| `169d408` | tech_units 对 company_knowledge 的兜底 | 独立修复 |

**并保留他的**：`_resolve_evidence_assets` / `_evidence_asset_info`（证明材料内联扫描件，来自 `e66cb67`）。

### 步骤 3：迁移文件与表的处置

> ⚠️ **本节旧版本曾把 A / C / D / E / `006` 等组标为「退役」，那个结论已被用户明确否决：**
> 「**他的只做参考，大方向还是为了最后服务整个系统。**」
>
> **现定论：我方 54 张表一张都不删、不改名、不合并。**
> 队友的表只提供**技法参考**（`payload` 全保真、`position` 排序列、`coalesce_cols`、
> `touch_col`、`deleted_at` 墓碑、`json`-`dual`-`db` 三态开关）。
> **「收缩」只发生在「列」上（准入规则），绝不发生在「表」上。**

| 我方文件 | 处置 | 说明 |
|:--|:--|:--|
| `001_schema_v1.sql`（48 表） | ✅ **保留** | 部分表按准入规则砍列 + 加 `payload jsonb` |
| `004_bid_reading_columns.sql` | ✅ **保留** | 给 `match_item` 补的列 |
| `005_comments.sql` | ✅ **保留** | L 组治理元数据；L 组本身已有真实数据 |
| `006_bid_writing_tables.sql`（6 表） | ✅ **保留** | 6 张表全部保留，见下表 |
| `007_bid_views_v2.sql` | ✅ **保留** | 视图是行级查询便利层，payload 模式下仍然有用 |
| `008_governance_sync.sql` | ✅ **保留** | 同 005 |

**`006` 的 6 张表：全部保留**（旧版标「退役」是错的）

| 我方表 | 队友的对应 | 处置 |
|:--|:--|:--|
| `parse_bundle` | 他的 `parse_bundles` | ✅ **保留我方表**；参考他的 `payload` 保真手法 |
| `template_fragment` | 他的 `parse_artifacts(kind='template_fragment')` | ✅ **保留** |
| `evaluation_item` | 他的 `parse_bundles.payload.evaluationItems` | ✅ **保留**（保留才有行级查询能力） |
| `parse_risk` / `parse_review` | 他的 payload 字段 | ✅ **保留** |
| `agent_result` | 他的 `parse_bundles.payload.agentResults` | ✅ **保留** |
| G 组补列（`bid_document` 37 列 / `bid_section` 50 列） | 他的 `workspaces.payload` + `workspace_sections` | ⚠️ **只做列级重排**：表名/主键/行数不变，按准入规则把没人用的列收进 `payload`，另加 `org_id`/CHECK/时间戳 |

**`001` 里 48 张表逐组处置：全部保留**

| 组 | 表数 | 处置 | 说明 |
|:--|:--:|:--|:--|
| **A 企业事实** | 10 | ✅ **DDL 保留 / 本轮不启用** | ✅ **已拍板：不建**——M3 是权威源，本组是只读镜像。DDL 保留在 `001_schema_v1.sql` 并标注「预留 / 不启用」，**不写数据、不依赖、不纳入迁移**；将来业务需要时启用即可。**这不是删表** |
| **B 招标信息与文件** | 6 | ✅ **保留** | `tender_project` 已有 **27 行真实数据** |
| **C 解析结果** | 3 | ✅ **保留** | 行级查询（"这张表在第几段"）靠它，payload 替代不了 |
| **D 提取结果** | 3 | ✅ **保留** | 同上（要求项/要求组的关系查询） |
| **E 匹配结果** | 6 | ✅ **保留** | 6 态匹配契约需要行级查询与聚合 |
| **F 运行与可观测** | 3 | ✅ **保留** | `analysis_run` 已有 **17 行真实数据**；队友完全没有 |
| **G 标书产出** | 5 | ✅ **保留我方表名** | 吸收他的 payload 保真手法；`bid_section` 做列级重排（49→~16 列 + payload） |
| **I 初筛与提醒链** | 6 | ✅ **保留** | 队友只有 `reminders` |
| **J 抽取补齐** | 1 | ✅ **保留** | 队友无对应 |
| **K 决策与运行** | 2 | ✅ **保留** | `decision_summary` 队友无对应 |
| **L 治理元数据** | 3 | ✅ **保留** | 已有 **1082 + 54 + 61 行真实数据**；队友完全没有 |

### 步骤 4：必须补的两处（否则是回归）

| # | 要补什么 | 怎么补 |
|:--|:--|:--|
| 1 | **原子写**（§2.3） | 让 `storage/json_backend.py` 改用 `repository/io.py` 的 `read_json`/`write_json`（temp + `os.replace`） |
| 2 | **子表局部重建**（§2.1） | `save_workspaces` 把三处无 `WHERE` 的 `DELETE` 改成 `DELETE ... WHERE workspace_id = ANY(%s)`，只重建传入的工作区 |

---

## 四、合并后的总装形态

```
┌─ 数据模型层（我方的 54 张表，一张都不删）──────────────────────┐
│  A 企业事实(10)   B 招标信息(6)    C 解析结果(3)   D 提取结果(3) │
│  E 匹配结果(6)    F 运行可观测(3)  G 标书产出(5)   I 初筛链(6)   │
│  J 抽取补齐(1)    K 决策运行(2)    L 治理元数据(3)              │
│  ＋ 每表加 payload JSONB 全保真（借队友的技法）                 │
│  ＋ 列按准入规则裁决：没人查的列收进 payload（只砍列，不砍表）  │
└────────────────────────────────────────────────────────────────┘
                              │
┌─ 存储切换层（队友的，已合并）▼──────────────────────────────────┐
│  backend/app/storage/   json（默认）/ dual（灰度）/ db（唯一源） │
│  ＋ 我方已补回：原子写（复用 repository/io.py）                  │
└────────────────────────────────────────────────────────────────┘
                              │
┌─ 接缝（合并后，只有一个）────▼──────────────────────────────────┐
│  self.backend      ← 队友的（catalog/libraries/workspaces/…）    │
│  self._facts_repo  ← 我方的（M3 图库直连，他的接口里没有 facts） │
└────────────────────────────────────────────────────────────────┘
                              │
┌─ 标书模块 ──────────────────▼──────────────────────────────────┐
│  零改动即可跑（读写的都是 Store 内存里的 workspace 对象）        │
└────────────────────────────────────────────────────────────────┘

仍留磁盘（队友有意的范围选择）：
  ParseBundle、证明材料、导出件、assets、config(含 API Key)

借队友的技法（加到**我方的表**上，不是换表）：
  payload 全保真 / position 排序列 / coalesce_cols（不覆盖 created_at）/
  touch_col（只在真变化时改 updated_at）/ deleted_at 墓碑 /
  json-dual-db 三态存储开关
```

---

## 五、待拍板事项（已收敛，只剩 2 项）

| # | 问题 | 状态 |
|:--|:--|:--|
| **1** | 接受 ParseBundle 仍留磁盘吗？ | ⏳ **待拍板**：① 接受（队友的范围选择，行为与今天一致）② 不接受 → 给 `StorageBackend` 加 `load/save_parse_bundle` |
| **2** | 子表全局 `DELETE` + 重建 | ✅ **已定**：改成按 `workspace_id` 局部重建（P0 待实施） |
| **3** | F 组 / L 组 | ✅ **已定：保留**（`analysis_run` 已有 17 行、`meta_*` 已有 1082+54+61 行真实数据；队友完全没有） |
| **4** | 原子写 | ✅ **已完成**：`storage/json_backend.py` 已改用 `repository/io.py` 的原子写 |

---

## 六、待决策：E 组（匹配）的查询能力

这是**唯一一处"他的设计确实会退化"**的地方，必须明确记录：

| 能力 | 我方 E 组 6 表 | 他的设计 |
|:--|:--|:--|
| 行级查询「哪些要求未满足」 | ✅ `WHERE status='不满足'` | ⚠️ 只能 jsonb 路径查询 `payload->'reasoning'->'fineReview'->'rows'`（已提升的列只有 `status`/`review_status`/`requirement_id`/`mandatory`/`handling_mode`） |
| 跨工作区要求追溯 | ✅ 表级 JOIN | ⚠️ 需要 jsonb 展开 |
| 6 态契约约束 | ✅ CHECK 约束 | ❌ 无约束（值域不校验） |
| 证据明细（`evidenceItems` 18 字段） | ✅ `match_item_evidence` 表 | ⚠️ 在 `fine_review_rows.payload` 里 |

**现状已经足够**（`workspace_fine_review_rows` 提升了 `status`/`requirement_id`/`review_status`/`mandatory`/`handling_mode`，覆盖了最常查的几个），**但如果要做报表/统计分析，需要补投影表**。建议先不补，等出现真实查询需求再说。
