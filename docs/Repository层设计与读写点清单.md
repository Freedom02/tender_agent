# Repository 层设计与读写点清单（阶段 2 工作文档）

> 版本：V1 · 阶段 2 专用
> 范围：**第一批 28 表 = A(10) + B(6) + C(3) + D(3) + E(6)**
> 依据：`backend/app/store.py`（2192 行）逐方法盘点，行号标注以当前版本为准
> 原则：**阶段 2 只做 json_impl（零行为变更），接口贴现有 JSON 聚合边界，不贴表粒度**

---

## 一、关键发现（后面读取的人必看）

### 1.1 存储机制有 9 处，不是"只有一个 workspaces.json"

| # | 存储位置 | 内存属性 | 类型 | 对应组 | 迁移类型 |
|:--|:--|:--|:--|:--|:--|
| 1 | `runtime/catalog.json` | `self.catalog` | list | B 公告 | JSON→PG |
| 2 | `runtime/workspaces.json` | `self.workspaces` | dict | B+C+D+E+G | JSON→PG |
| 3 | `runtime/libraries.json` | `self.libraries` | dict | 资料库（证据文件登记） | JSON→PG |
| 4 | `agent-config.json` | `self.config` | dict | I 初筛配置 | JSON→PG |
| 5 | `runtime/reminders.json` | `self.reminders` | list | I 提醒 | JSON→PG |
| 6 | `runtime/automation-runs.json` | `self.runs` | list | K 自动化 | JSON→PG |
| 7 | `projects/*/runs/run-*.json` | `FactArtifactStore` | 每 run 一文件 | F 运行 | 文件→PG |
| 8 | `projects/*/pack/tender_pack.json` | `write_tender_pack()` | 每项目一文件 | D 导出快照 | **非权威，派生** |
| 9 | `projects/*/source/*` | 文件系统 | 文件本体 | 文件 | 留盘（只存 path+hash） |
| 10 | **M3（无本地持久化）** | `self._company_facts` | 内存 | A 企业事实 | **M3→PG 镜像** |

### 1.2 三个必须记住的结论

1. **A 组企业事实没有 JSON 持久化**——它走 `_refresh_company_from_m3()`（L327）：M3 拉取 → 内存缓存（60s TTL）。所以 A 组的迁移是"**M3 → PG 镜像**"，不是"JSON → PG"。写入点与 B/C/D/E 完全不同。
2. **F 组运行记录是独立文件存储**（`projects/*/runs/run-*.json`，经 `FactArtifactStore`），不在 workspaces.json 里。它在第二批，这里只登记不迁移。
3. **tender_pack.json 是非权威导出快照**——由 `builtin_pack.write_tender_pack()` 从 analysisPack 派生写出。D 组的权威数据在 workspace 的 `analysisPack`/`fineReview` 里，tender_pack.json 只是归档产物。**回填时以权威源为准，不要读 tender_pack.json。**

---

## 二、读写点清单（第一批 28 表）

### 2.1 写点（W）

| 编号 | 方法（行号） | 写入内容 | 落存储 | 对应表 |
|:--|:--|:--|:--|:--|
| W-A | `_refresh_company_from_m3()` L327 | 8 类企业事实 + 画像 + 计数 | M3→内存 | A 组 10 表（organization/…/evidence_file） |
| W-B1 | `persist_catalog()` L270 | catalog 全量（公告 + 初筛字段 + 下载状态） | catalog.json | tender_announcement / announcement_match_term / source_site |
| W-B2 | `import_document()` L561 | workspace.project + project.document 头部 | workspaces.json | tender_project / tender_document / document_acquisition |
| W-C | `import_document()` L561 | document.segments / sections / tables | workspaces.json | document_segment / document_section / document_table |
| W-D | `match_project()` L1114（经 document_fine_matching） | analysisPack.targetModules / requirements | workspaces.json（+tender_pack 导出） | requirement_group / tender_requirement / requirement_segment |
| W-E1 | `match_project()` L1114 | reasoning.fineReview（rows/summary/risks） | workspaces.json | match_assessment / match_item / match_item_evidence / match_item_fact / match_risk |
| W-E2 | `update_fine_review_row()` L1651 | fineReview 单行 + decisionSummary + auditRecords | workspaces.json | match_item（更新）/ review_record（追加） |

> 注意：`persist_runtime()`（L276）同时写 reminders.json + automation-runs.json + workspaces.json。切 PG 时要拆成三个独立写点，不能照搬。

### 2.2 读点（R）

| 编号 | 方法（行号） | 读内容 | 来源 | 对应表 |
|:--|:--|:--|:--|:--|
| R-A | `bootstrap()` L362 / `match_project()` L1114 | company / _company_facts / company_knowledge | 内存（M3） | A 组 |
| R-B | `filtered_catalog()` L389 / `get_project()` L424 / `search()` L428 | catalog 列表 + with_preview | catalog.json | B 组公告 |
| R-C | `workspace()` L1613 | 单个 workspace 全量 | workspaces.json | B+C+D+E |
| R-D | `match_project()` L1114 | requirements（读）+ company_facts（读） | workspaces.json + 内存 | D + A |
| R-E | `workspace()` → 前端匹配页 | fineReview（rows） | workspaces.json | E 组 |

---

## 三、Repository 层实现（✅ 第一刀已完成：存储缝）

**核心设计决策**：json_impl 必须零行为变更，所以接口贴**现有 JSON 聚合边界**（facts / catalog / workspace），不贴 28 张表粒度。切 pg_impl 时，pg_impl 内部才拆表。

### 3.1 已落地的结构（实际代码）

```text
backend/app/repository/
├── __init__.py            # 导出 3 个 Protocol + 3 个 impl
├── io.py                  # read_json / write_json（从 store.py 迁出，全项目唯一落盘出口）
├── base.py                # 3 个 Protocol 接口契约
├── json_impl/
│   ├── __init__.py
│   ├── facts_repo.py      # M3FactsRepository（A 组：M3 直连）
│   ├── catalog_repo.py    # JsonCatalogRepository（B 组公告：catalog.json）
│   └── workspace_repo.py  # JsonWorkspaceRepository（B+C+D+E：workspaces.json）
├── pg_impl/__init__.py    # 阶段 3+ 占位
└── dual.py                # 阶段 3 双写占位
```

### 3.2 接口契约（实际实现，不是草案）

```python
class FactsRepository(Protocol):
    def load(self) -> dict[str, Any]: ...          # 8 类 CamelCase 事实

class CatalogRepository(Protocol):
    def load(self) -> list[dict[str, Any]]: ...
    def save(self, items: list[dict[str, Any]]) -> None: ...

class WorkspaceRepository(Protocol):
    def load(self) -> dict[str, dict[str, Any]]: ...
    def save(self, workspaces: dict[str, dict[str, Any]]) -> None: ...
```

### 3.3 store.py 的改动（最小侵入，业务逻辑一行未动）

> ⚠️ **合并后重打版（13 处）**：合并「标书制作」代码时 `store.py` 被整体换成远端版本，本节第 1 刀的接线（9 处）**全部丢失**（`repository/` 包本身已被提交保留下来）。现已在新版 store.py 上重打，并覆盖合并新增的并发路径。**每次合并后务必复查本节接线是否还在**——见 §3.6。

| # | 位置 | 改动 |
|:--|:--|:--|
| 1 | 顶部 | 删本地 `read_json`/`write_json` 定义 → `from .repository.io import read_json, write_json`（**名字保留**，其他调用点零改动） |
| 2 | 顶部 | `from .repository import JsonCatalogRepository, JsonWorkspaceRepository, M3FactsRepository` |
| 3 | `__init__` | 新建 3 个 repo 实例：`_catalog_repo` / `_workspace_repo` / `_facts_repo` |
| 4 | 新增 `_load_facts_from_m3()` | 注入 loader，解 store↔m3 循环依赖 |
| 5 | `initialize()` | `self.catalog = self._catalog_repo.load()` |
| 6 | `initialize()` | `self.workspaces = self._workspace_repo.load()` |
| 7 | `persist_catalog()` | → `self._catalog_repo.save(self.catalog)` |
| 8 | `persist_runtime()` 读 | `disk_workspaces = self._workspace_repo.load()` |
| 9 | `persist_runtime()` 写 | → `self._workspace_repo.save(merged_workspaces)`（**注意：写的是合并结果，不是 `self.workspaces`**） |
| 10 | `_refresh_workspaces_from_disk()` 读（**合并新增**） | → `self._workspace_repo.load()` |
| 11 | `delete_workspace()` 读（**合并新增**） | → `self._workspace_repo.load()` |
| 12 | `delete_workspace()` 写（**合并新增**） | → `self._workspace_repo.save(disk_workspaces)` |
| 13 | `_refresh_company_from_m3()` | `facts = self._facts_repo.load()` |

**没动的**：`filtered_catalog` / `with_preview` / `import_document` / `match_project` / `save_draft` / 全部标书方法等原样。本次只换了"数据从哪读、往哪写"。

**为什么是 13 处而不是 9 处**：合并进来的提交（`963c371 fix(store): preserve workspace dict identity across refresh and persist` 等）给 store.py 加了并发安全逻辑——`persist_runtime` 变成**带锁的 read-merge-write**（`_workspace_snapshot` 脏检测 + `deleted_workspaces` 墓碑 + `_install_workspaces` 保对象身份），并新增 `_refresh_workspaces_from_disk()` 与 `delete_workspace()` 两条直接读写 `workspaces.json` 的路径。**这 4 处新路径必须一起接进 Repository，否则抽象是漏的**（切 PG 时它们仍会去写 JSON 文件）。

### 3.4 验收结果（两轮都做过，含零回归严格对拍）

**第一轮（合并前，9 处接线）**：25 failed / 312 passed，基线由 `_gen_baseline*.py` 反向还原 9 处生成 → FAILED 集合一致。

**第二轮（合并后重打，13 处接线）**：

| 运行 | 结果 |
|:--|:--|
| A. 有 Repository 改动（13 处） | **90 failed / 397 passed / 15 skipped** |
| B. 基线（`git show HEAD:backend/app/store.py`，即合并版、无接线） | **90 failed / 397 passed / 15 skipped** |
| C. FAILED 集合逐条 diff | **完全一致**（`Compare-Object` 无差异 → TRUE zero-regression） |

- ✅ `py_compile` 通过；`from app.store import Store` 与 `from app.repository import ...` 均可导入
- ✅ 接线完整：`_catalog_repo`/`_workspace_repo`/`_facts_repo` 共 **12 处引用**；`read_json(self.catalog_path)` / `write_json(self.workspaces_path)` 这类**直接文件访问已归零**
- ✅ 第二轮基线用的是 **git HEAD 本身**（不再需要反向生成脚本），比第一轮更硬

> **失败数从 25 涨到 90，与本次改动无关**：合并带进来大量新测试（bid_writing 等，通过数也从 312 涨到 397）+ 另一个对话的 WIP。
> 证明方式是 **FAILED 集合逐条完全一致**——有接线和没接线跑出来的失败清单一个不差。`pytest -q --ignore=backend/tests/test_pi_client.py`（见 §3.4.2）。

### 3.4.1 一个必须先确认的前提（重要）

**全库没有任何测试 `patch` 被搬走的函数**：搜 `patch("...store.read_json")` / `write_json` / `persist_catalog` / `persist_runtime` —— **零命中**。

这是"把 `read_json`/`write_json` 从 store.py 搬到 `repository/io.py`"能安全落地的前提。**如果有测试 patch 了 `store.write_json`，搬走就会破测试**（因为 repo 内部调的是 `repository.io.write_json`，patch 不到）。以后再搬函数时，先跑这个搜索。

### 3.4.2 遗留的 90 个既有失败 + 1 个收集阻断（阶段 5 对拍前必须清掉）

**① 收集阻断（合并引入，必修）**：`backend/tests/test_pi_client.py` 用的是 `from app.pi_agent.client import ...`（**裸 `app.`**），而全库其余 165 处是 `from backend.app...`。从仓库根跑 pytest 时 `app` 不可导入 → **pytest 直接中断收集**（`Interrupted: 1 error during collection`），整套测试一个都不跑。
- 修法（二选一）：① 把该文件 3 行 import 改成 `from backend.app...`（与全库一致，推荐）；② 跑测试时加 `--ignore=backend/tests/test_pi_client.py`（本次零回归对拍即用此法，避免改他人代码）。
- 该文件由合并提交 `1796737` 引入，**与 Repository 改动无关**。

**② 90 个既有失败**，主要来自：
- 沙箱临时目录权限（`PermissionError` / `FileNotFoundError`）——**无沙箱环境下大概率会通过**
- 另一个对话的 WIP 改动（`workbuddy_agent` / `document_map` / `observability` / `fact_models`）

**动作项**：阶段 5 之前先在正常环境（无沙箱）拿到干净基线；不要把 90 当成本次迁移的欠账。


### 3.5 ⚠️ 第一刀的边界（下一刀必须知道）

**这一刀只建了"存储缝"，业务逻辑仍在 store.py。** 具体局限：

1. `CatalogRepository` 只有 `load/save`，**没有能力方法**（如 `get(project_id)` / `filtered(...)`）——所以 pg_impl 现在还只能用 `load()` 全量取，没法"只查一条"。
2. `WorkspaceRepository` 同理：`load()` 会把整个 ~1 GB 读进内存。**这正是要治的病，但阶段 2 必须保持此行为**（零行为变更）。

**下一刀（阶段 2 续）**：把 store.py 的**读能力**逐步下沉进 repo——`get_workspace(id)` / `get_announcement(id)` / `list_workspaces()`，以及把 `filtered_catalog` 的过滤逻辑下沉。每搬一个方法跑一次测试。搬完之后 pg_impl 才有位置做"按需查询"，阶段 5 的切读才谈得上。

### 3.6 ⚠️ 事故复盘：合并把第一刀覆盖了（后面的人必看）

**发生了什么**：合并「标书制作」代码时，`store.py` 在冲突解决中被整文件换成远端版本，第 1 刀在 store.py 上的 9 处接线**全部丢失**；但 `repository/` 包（untracked→被提交）保留了下来。事后 `git status` 显示 store.py **干净且等于 HEAD**——即改动被静默丢弃，不是冲突标记，也不是 `git status` 里能直接看出的"已改"。

**教训与护栏**：
1. **合并后必查接线**：跑 `Select-String store.py -Pattern "_catalog_repo|_workspace_repo|_facts_repo"`，期望 13 处；`git status` 干净不等于接线还在（因为被覆盖后也干净）。
2. **本刀的文件要 commit**：repository 包已被提交才幸免，但 store.py 的接线当时未提交才被覆盖。接线重打后应立即提交（或至少 `git stash` 保护）。
3. **`write_json` 的原子写也一起丢了**：合并版 store.py 的本地 `write_json` 是朴素直写，而 `repository/io.py` 是原子写（临时文件 + `os.replace`）。重打后原子写随 `from .repository.io import` 恢复——这也是"唯一落盘出口在 io.py"的价值所在。

---

## 四、给后续读取的注意点（迁移 3–6 阶段必读）

1. **A 组是 M3→PG，不是 JSON→PG**：写 pg_impl 时，`FactsRepository` 的 pg 实现要接 `m3.sync.read_facts_from_m3()`，并写 `fact_sync_log.sync_batch_id`（结论可复现的关键，D1 决策）。
2. **workspace 一个对象跨 B+C+D+E**：json 阶段当整体读写；pg_impl 阶段才拆 28 张表，拆的时候要保证"一个 workspace 的事务性写入"（要么全成要么全滚）。
3. **tender_pack.json 别回填**：它是导出快照，权威在 analysisPack。回填脚本读 workspaces.json 的 analysisPack，不读 pack 文件。
4. **`persist_runtime` 现在是「带锁的 read-merge-write」**（合并后语义变了）：它先 `_workspace_repo.load()` 读磁盘 → 用 `_workspace_snapshot` 脏检测决定哪些 key 覆盖 → 剔除 `deleted_workspaces` 墓碑 → `_workspace_repo.save(merged)`。所以 repo 的 `save()` 收到的是**合并结果**，不是 `self.workspaces`。切 PG 时要拆成 reminders / automation-runs / workspaces 三个独立持久化，且 pg_impl 的 `save()` 必须保留"只覆盖脏 key"的语义（否则多进程会互相抹掉）。
5. **`with_preview` 是读时增强**（L289）：初筛字段（relevanceScore/matchedXXX/scoreBreakdown）是每次读 catalog 时实时算的，不是存储态。切 PG 时这些要么存 `tender_announcement` 列，要么保持读时计算——**这是对拍最容易翻车的点**，要单独对拍。
6. **`analyze()` 同时改 catalog 和 workspace**（L1562–1563 两次 persist）：跨聚合事务，pg_impl 要用同一个事务包住 catalog 更新 + workspace 写入。
7. **`workspaces-deleted.json` 墓碑是第 4 个 workspace 持久化点**（合并新增，`persist_deleted_workspaces()` L577）：它**不在** `WorkspaceRepository` 的 load/save 里，是独立文件（已删 workspace id 列表）。切 PG 时要么给 `workspace` 表加软删标记、要么建 `workspace_tombstone` 表——**必须显式决策**，不能默认丢：否则另一个后端进程手里的旧副本会把删掉的项目合并回来。

---

## 五、范围外的内容（第二批，这里只登记不实现）

- F 运行（`projects/*/runs/`，FactArtifactStore）→ 阶段 6 第二批
- G 标书（workspace.draft / bidDocument / bidPlan）→ 阶段 6 第二批。**⚠️ 2026 核对发现这批范围远不止 5 张表**：见 `标书表替换可行性核对.md`——现有 G 组表缺 ~37 / ~50 个字段**无法替换**，且缺整层 `ParseBundle` 等 6 类实体，第二批从 17 表扩到 ~26 表。动 G 组之前必须先出 `006_bid_writing_tables.sql`。
- I 初筛（agent-config.json 的 monitoring/searchProfile + reminders.json）→ 阶段 6 第二批
- J/K（procurement_need / decision_summary / automation_run 的独立读写）→ 阶段 6 第二批
