# PG 迁移合并方案（标书表以队友存储层为准）

> 决策前提（用户 2026-09-30 定）：**标书制作的表以队友的为准。**
> 本文是审计结论 + 可执行的合并步骤。
>
> 审计对象：分支 `origin/fix/evidence-fill`（比 `origin/main` 多 4 个提交，2026-09-30）
> 我方基线：分支 `integrate/merge-origin-main`（= `origin/main` + 15 个提交）

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

| 我方文件 | 处置 | 理由 |
|:--|:--|:--|
| `001_schema_v1.sql` | ⚠️ **拆开用**（见下表） | 标书以他为准 + payload 保真 |
| `004_bid_reading_columns.sql` | ❌ 退役 | 给 `match_item` 补的列，该表不再使用 |
| `005_comments.sql` | ❌ 退役 | 治理元数据（对应 L 组）；若保留 L 组则只留 L 部分 |
| `006_bid_writing_tables.sql` | ❌ **大部分退役** | 见下面映射 |
| `007_bid_views_v2.sql` | ❌ 退役 | 视图是规范化表的投影，payload 模式下用不上 |
| `008_governance_sync.sql` | ❌ 退役 | 同 005 |

**`006` 的 6 张新表映射到他的设计：**

| 我方的表 | 他的对应 | 处置 |
|:--|:--|:--|
| `parse_bundle` | `parse_bundles`（表结构对应，但应用侧他不读） | ❌ 退役（用他的） |
| `template_fragment` | `parse_artifacts`（`kind='template_fragment'`） | ❌ 退役 |
| `evaluation_item` | `parse_bundles.payload.evaluationItems` | ❌ 退役（无损） |
| `parse_risk` / `parse_review` | `parse_bundles.payload.riskItems` / `.parseReviewReport` | ❌ 退役（无损） |
| `agent_result` | `parse_bundles.payload.agentResults` | ❌ 退役（无损） |
| G 组补列（`bid_document` 37 列 / `bid_section` 50 列） | 他的 `workspaces.payload` + `workspace_sections` | ❌ 退役（**标书以他为准**） |

**`001` 里 48 张表逐组处置：**

| 组 | 表数 | 他的对应 | 处置 |
|:--|:--:|:--|:--|
| **A 企业事实** | 10 | ❌ 无（他明确「M3 是单一来源，本库不复制」） | ❌ **退役**（两边都认同 M3 权威；我方 `001` 自己也写着「M3 仍是权威源，本组表是只读镜像」） |
| **B 招标信息与文件** | 6 | ✅ `catalog_projects`（公告）；其余在 `payload.project` | ⚠️ **部分退役**：`tender_announcement`→`catalog_projects`；`tender_project`/`tender_document`→payload |
| **C 解析结果** | 3 | 在 `payload.project.document` | ❌ 退役（无损） |
| **D 提取结果** | 3 | 在 `payload` | ❌ 退役（无损） |
| **E 匹配结果** | 6 | ✅ `workspace_fine_review_rows`（`fineReview.rows`）+ 其余在 payload | ❌ 退役（无损，但**丢掉行级查询能力**，见 §五 待决策） |
| **F 运行与可观测** | 3 | ❌ **无** | ✅ **建议保留/补进他的库** |
| **G 标书产出** | 5 | ✅ `workspaces` + `workspace_sections` | ❌ 退役（**以他为准**） |
| **I 初筛与提醒链** | 6 | ⚠️ 仅 `reminders` | ⚠️ 待定（看业务） |
| **J 抽取补齐** | 1 | ❌ 无 | ⚠️ 待定 |
| **K 决策与运行** | 2 | ✅ `automation_runs` | ⚠️ 部分退役 |
| **L 治理元数据** | 3 | ❌ **无** | ✅ **建议保留/补进他的库** |

### 步骤 4：必须补的两处（否则是回归）

| # | 要补什么 | 怎么补 |
|:--|:--|:--|
| 1 | **原子写**（§2.3） | 让 `storage/json_backend.py` 改用 `repository/io.py` 的 `read_json`/`write_json`（temp + `os.replace`） |
| 2 | **子表局部重建**（§2.1） | `save_workspaces` 把三处无 `WHERE` 的 `DELETE` 改成 `DELETE ... WHERE workspace_id = ANY(%s)`，只重建传入的工作区 |

---

## 四、合并后的总装形态

```
┌─ 保真层（他的 16 表）────────────────────────────────────┐
│  workspaces / catalog_projects / libraries / library_documents │
│  reminders / automation_runs / bid_runs                        │
│  + 每表 payload JSONB 全保真（含 bidDocument 37 key）           │
│  + 3 张派生投影（sections / fine_review / coverage，当前只写）  │
│  + 6 张迁移/查询用（parse_bundles / parse_artifacts /           │
│    evidence_files / asset_files / app_config / runtime_state）  │
└────────────────────────────────────────────────────────────────┘
                              │
┌─ 切换层（他的）─────────────▼──────────────────────────────────┐
│  backend/app/storage/   json（默认）/ dual（灰度）/ db（唯一源） │
└────────────────────────────────────────────────────────────────┘
                              │
┌─ 接缝（合并后，只有一个）────▼──────────────────────────────────┐
│  self.backend      ← 他的（catalog/libraries/workspaces/…）      │
│  self._facts_repo  ← 我方的（M3 图库直连，他的接口里没有 facts） │
└────────────────────────────────────────────────────────────────┘
                              │
┌─ 标书模块 ──────────────────▼──────────────────────────────────┐
│  零改动即可跑（读写的都是 Store 内存里的 workspace 对象）        │
└────────────────────────────────────────────────────────────────┘

仍留磁盘（他有意的范围选择）：
  ParseBundle、证明材料、导出件、assets、config(含 API Key)

可选补充（我方独有、他完全没有）：
  F 组 运行可观测（analysis_run / analysis_run_stage / llm_call_log）
  L 组 治理元数据（meta_table / meta_column / meta_relationship）
```

---

## 五、合并前必须拍板的三件事

| # | 问题 | 选项 |
|:--|:--|:--|
| **1** | **接受 ParseBundle 仍留磁盘吗？** | ① 接受（他的范围选择，行为与今天一致）<br>② 不接受 → 给 `parse_bundles` 加 `load/save_parse_bundle`，把 `store.get_parse_bundle` 切过去 |
| **2** | **接受子表全局 DELETE + 重建吗？** | ① 接受（现规模无所谓）<br>② 改成按 `workspace_id` 局部重建（推荐，成本很低） |
| **3** | **F 组 / L 组要不要补进他的库？** | ① 补（运行可观测 + 治理元数据是他完全没有的）<br>② 不补（先只跑通主干） |

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
