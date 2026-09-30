# PG 表设计融合方案（我方规范化 × 队友 payload 保真）

> ## ⚠️ 三条定论（先读，覆盖本文其他所有说法）
>
> 用户原话：「**他的只做参考，大方向还是为了最后服务整个系统。**」
>
> 1. **我方 54 张表一张都不删、不改名、不合并。** 它们是本项目的目标数据模型。
> 2. **队友的表只提供技法参考，不是替代品。** 借的是手法：`payload` 全保真、
>    `position` 排序列、`coalesce_cols`、`touch_col`、`deleted_at` 墓碑、三态开关。
>    这些手法**加到我方的表上**。
> 3. **本文里的「压缩 / 削减」一律指「列」，不是「表」。**
>    准入规则用来**删列**（把没人查的列收进 `payload`，数据零丢失），
>    **不用来删表**。唯一实测案例：样板表 `workspace_sections` 的**列** 49 → 16（-67%）。
>
> ⚠️ 另注：**空表 ≠ 该删的表**。我方实测 44 张 0 行表是"还没落地"，
> 不是冗余，按域逐步落地即可。
>
> ---
>
> 目标：**同时具备两边的优点**——
> 我方的多租户、严格值域约束、规范化查询、运行可观测、治理元数据
> × 队友的 payload 全保真、幂等 upsert、三态切换、顺序保持
>
> 决策背景：用户明确「标书表**参考**他的」（不是照搬），并要求"更严格、适合多租户"。

---

## 一、先纠正一个前提：我方的设计哲学里**本来就有** jsonb 机制

`001_schema_v1.sql` 的设计约定原文：

```
--   * 内部主键 id bigserial；业务键 biz_id text unique（沿用现有字符串 ID）
--   * 每表带 org_id（多租户预留）、created_at、updated_at
--   * 枚举用 text + CHECK，不用 PG enum 类型（改值域不需 ALTER TYPE）
--   * 长正文 text（自动 TOAST）；结构多变且不参与查询的用 jsonb
--   * 时间统一 timestamptz（旧 JSON 混用 +08:00 与 Z，迁移脚本需归一）
--   * 原始文件（招标文件/证据文件/导出件）不入库，只存 path + sha256
```

而且 `001` 里**已经用了 17 处 jsonb**：`summary`(22 字段汇总原样透出)、`fact_snapshot`、`source_refs`、`plan`、`metrics`、`search_profile`、`style`/`cells`/`rows`(表格)、`score_breakdown`、`status_counts`…

**所以问题不是"要不要 jsonb"，而是"G 组没按自己定的规则执行"**：
`bid_section` 提了约 50 个列 → 但按上面的规则，其中大部分（`templateBlocks`/`templateTables`/`filledTables`/`editHistory`/`writingProfile`…）**既不被查询、也不参与约束** → 本该留 jsonb。

**队友的贡献**：把「部分字段用 jsonb」升级为「**payload 全表保真**」——从"靠人判断"变成"结构性保证"。

---

## 二、融合的核心原则（一句话）

> **`payload` 是事实（全保真，永不加约束）；提升列是投影（归一 + 约束，允许为空）。**

推论：

| | 地位 | 写入行为 | 结构变更 |
|:--|:--|:--|:--|
| `payload jsonb` | **事实**（source of truth） | 永远写成功（原样存） | 代码加字段**不用迁移** |
| 提升列 | **投影**（projection） | 归一成功才填；失败留 NULL + 记治理告警 | 要查才加，可随时重建 |
| CHECK 约束 | **投影的质量门** | 只约束提升列 | 值域变化时只需改 CHECK |

**这样"严格"和"不丢数据"不再冲突**——严格性体现在投影上，保真性体现在 payload 上。

---

## 三、逐维度融合表

| 能力 | 队友做法 | 我方做法 | **融合方案** |
|:--|:--|:--|:--|
| 字段保真 | ✅ `payload` 全保真 | ⚠️ 只部分用 jsonb（17 处） | ✅ **采纳他的：payload 全表保真（强制）** |
| 字段可查 | ⚠️ 仅少数提升列 | ✅ 全字段列 | ✅ **严格准入规则**（见 §四）——只提升"要查/约束/排序/join"的 |
| 值域契约 | ❌ 无 | ✅ `text + CHECK`、6 态契约 | ✅ **保留我方的 CHECK**（这是"更严格"的实质） |
| 多租户 | ❌ 无 | ✅ 每表 `org_id` | ✅ **保留我方的 `org_id`** |
| 幂等 upsert | ✅ PK = 业务 id | ✅ `biz_id` unique | ✅ **融合**：`bigserial` 代理键 + `UNIQUE(org_id, biz_id)` |
| 有序集合顺序 | ✅ `position` 列 | ❌ 无 | ✅ **采纳他的 `position`** |
| 大集合拆行 | ✅ sections / fine_review / coverage | ✅ 规范化表 | ✅ **融合**：拆行 + payload 保真（拆行只提升列，正文不重复存） |
| `created_at` 不被刷 | ✅ `coalesce_cols` | ❌ 无 | ✅ **采纳他的**（否则每次保存都刷成全表同一时刻） |
| `updated_at` 只在实际变化时更新 | ✅ `touch_col` + `IS DISTINCT FROM` | ❌ 无 | ✅ **采纳他的** |
| 删除墓碑 | ✅ `deleted_at` **列** | ⚠️ 独立文件 + tombstone 数组 | ✅ **采纳他的列级墓碑**（更干净：一个 `WHERE deleted_at IS NULL`） |
| 原子写 | ❌ 朴素 `write_text` | ✅ temp + `os.replace` | ✅ **保留我方的**（1GB `workspaces.json` 崩溃即损坏） |
| 子表写入 | ⚠️ **全局 DELETE + 重建**（无 WHERE） | 按聚合 | ✅ **改成按 `workspace_id` 局部重建** |
| 并发安全 | ⚠️ 整份覆盖，多实例互覆 | ✅ 单实例保护（`5f80bce`） | ✅ **保留我方的单实例保护** |
| 三态切换 | ✅ `json/dual/db` 已落地 | ⚠️ `dual.py`/`pg_impl` 空占位 | ✅ **采纳他的** |
| 运行可观测 | ❌ 无 | ✅ F 组 3 表（`analysis_run`/`_stage`/`llm_call_log`） | ✅ **保留我方的** |
| 治理元数据 | ❌ 无 | ✅ L 组 3 表 | ✅ **保留我方的，并升级为"投影自描述"**（见 §七） |
| 审计记录 | ✅ payload 可回滚 | ✅ `review_record` | ✅ 两者都留 |
| 原始文件 | ✅ 只存元数据表 | ✅ 只存 `path + sha256` | 两边一致，沿用 |

---

## 四、提升列准入规则（融合的关键）

一个字段**只有当它满足以下至少一条**才提升为列：

1. 要 `WHERE` 过滤
2. 要 `JOIN`（作为外键）
3. 要 `ORDER BY`
4. 要参与唯一键
5. 要 `CHECK` 值域约束
6. 要建索引（含 GIN 之外的高频查询）
7. 要进治理元数据（语义必须暴露给 agent / DBA）

**其余一律进 payload。**

按此规则复核标书表（把 G 组从 ~50 列压回合理量）：

| 字段 | 提升？ | 理由 |
|:--|:--:|:--|
| `sectionId` / `workspace_biz_id` | ✅ | 唯一键 + JOIN |
| `title` | ✅ | 要查 |
| `kind` / `source` / `status` / `writing_profile` | ✅ | 要 CHECK 值域 + 要查 |
| `level` / `order` / `parentSectionId` | ✅ | 要排序 + JOIN |
| `content` | ✅ | 长正文（`text` 自动 TOAST） |
| `templateFragmentId` | ✅ | JOIN |
| `templateBlocks` / `templateTables` / `filledTables` / `blanksToFill` | ❌ | 结构多变、不查 → **payload** |
| `editHistory` / `agentResults` / `requirementResponseRows` | ❌ | 同上 → **payload** |
| `evidenceEntries` / `evidenceGroups` / `evidenceSummary` | ❌ | 同上 → **payload** |
| `evaluationIds` / `evaluationDepth` / `sectionBlueprint` | ❌ | 同上 → **payload** |

→ **约 50 列 → 约 12 列 + payload**，同时一个字段都不丢。

---

## 五、具体表结构模板（用标书表举例，枚举取自代码契约）

枚举来源：`backend/app/bid_writing/schemas.py`（实测）

```
BID_DOC_STATUS      = ("planned","generating","generated","exported")
BID_MODE            = ("open","secret")
SECTION_STATUS      = ("pending","generating","generated","missing")
SECTION_SOURCE      = ("template","ai","table","attachment","copied_template")
SECTION_KIND        = ("commercial","technical","deviation","attachment")
WRITING_PROFILE     = ("technical","commercial","template","attachment")
DEVIATION           = ("无偏离","正偏离","负偏离","不适用","待人工确认")
DEVIATION_TABLE     = ("technical","commercial")
```

```sql
-- 工作区：融合模板
CREATE TABLE workspace (
    id              bigserial PRIMARY KEY,                       -- 我方：代理键
    org_id          text NOT NULL DEFAULT 'org-001',             -- 我方：多租户
    biz_id          text NOT NULL,                               -- 他的：业务键（= JSON id）
    project_biz_id  text,                                        -- JOIN 用
    title           text,
    draft_version   integer NOT NULL DEFAULT 1,
    created_at      timestamptz NOT NULL DEFAULT now(),           -- 他的：coalesce 语义
    updated_at      timestamptz NOT NULL DEFAULT now(),           -- 他的：touch 语义
    deleted_at      timestamptz,                                  -- 他的：列级墓碑
    position        integer,                                      -- 他的：原始顺序
    payload         jsonb NOT NULL,                               -- ★ 全保真（含 bidDocument 37 key）
    UNIQUE (org_id, biz_id)
);

-- 章节：融合模板（拆行 + 保真 + 严格约束）
CREATE TABLE workspace_section (
    id                bigserial PRIMARY KEY,
    org_id            text NOT NULL DEFAULT 'org-001',
    workspace_biz_id  text NOT NULL,
    section_id        text NOT NULL,
    -- 提升列（严格准入）
    title             text,
    kind              text CHECK (kind IN ('commercial','technical','deviation','attachment')),
    source            text CHECK (source IN ('template','ai','table','attachment','copied_template')),
    writing_profile   text CHECK (writing_profile IN ('technical','commercial','template','attachment')),
    status            text CHECK (status IN ('pending','generating','generated','missing')),
    section_level     integer,
    sort_order        integer,
    parent_section_id text,
    template_fragment_biz_id text,
    content           text,                                       -- 长正文（自动 TOAST）
    -- 保真
    payload           jsonb NOT NULL,                             -- ★ 其余 ~38 个字段全在这
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, workspace_biz_id, section_id)
);

-- 细评审行（标书正文的主要输入）：同样的模板
CREATE TABLE workspace_fine_review_row (
    id              bigserial PRIMARY KEY,
    org_id          text NOT NULL DEFAULT 'org-001',
    workspace_biz_id text NOT NULL,
    row_biz_id      text NOT NULL,
    requirement_biz_id text,
    title           text,
    mandatory       boolean,
    handling_mode   text,
    status          text CHECK (status IN ('满足','部分满足','不满足','待补材料','待澄清','不适用')),  -- ★ 6 态契约
    review_status   text,
    payload         jsonb NOT NULL,                               -- ★ evidenceItems 18 字段等全在这
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (org_id, workspace_biz_id, row_biz_id)
);
```

---

## 六、必须解决的矛盾：强 CHECK vs 保真 payload

**矛盾**：如果 payload 里有个脏值（例如 `status` 是第 7 种、或历史数据里是 `""`），提升列的 CHECK 会**拒绝整行插入** → 等于因为一个派生字段丢掉了事实。

**解法（推荐）**：**归一在写入层，约束在列上**

```python
def normalize_status(raw: Any) -> str | None:
    """把原始值归一成 6 态之一；无法归一返回 None（列留 NULL，但不阻塞写入）。"""
    text = str(raw or "").strip()
    return text if text in MATCH_STATUSES else None
```

- 写入流程：**先写 payload（永不失败）→ 再尝试归一填提升列 → 归一失败留 NULL + 写一条治理告警**
- CHECK 用 `CHECK (col IS NULL OR col IN (...))` 形式（NULL 合法）
- 好处：**严格性保留（投影里的值一定合法）**，同时**绝不因脏数据丢整行**

备选：约束先用 `NOT VALID` 建、事后 `VALIDATE CONSTRAINT` 渐进加严。

> 我方 `001` 的约定里也写了「旧 JSON 混用 +08:00 与 Z，**迁移脚本需归一**」——这条思路延伸到值域上即可。

---

## 七、杀手锏：用治理元数据（L 组）解决"漏列"这个根本问题

我方 L 组三张表（`meta_table` / `meta_column` / `meta_relationship`）他完全没有。而在融合方案里，它的价值可以**升级**：

**`meta_column` 记录每个提升列来自 payload 的哪个路径 + 归一规则**：

| 列名 | payload 路径 | 归一函数 | 约束 |
|:--|:--|:--|:--|
| `kind` | `$.kind` | `normalize_section_kind` | `IN (commercial,technical,deviation,attachment)` |
| `status` | `$.status` | `normalize_match_status` | 6 态契约 |
| `sort_order` | `$.order` | `to_int` | — |

**这带来的能力**：
1. **投影是自描述的** —— payload → 列的映射有唯一权威清单，不靠人去记
2. **可自动校验完整性** —— 拿代码里实际写入的 key 集合 vs `meta_column` 的路径集合做差集，**自动发现"代码加了字段、投影没跟上"**
3. **根治我们踩过的坑** —— G 组「代码写 37 个 key、表只有 3 列」这种缺口，在这种机制下会被**自动检出**，不会再等到合并后才靠人工核对发现
4. 换 schema / 加投影时，迁移可以**从 `meta_column` 生成**

> 这是两边都没有的第三种能力：**他的 payload 保真解决了"存得下"，我方的治理元数据解决了"看得住"。**

---

## 八、多租户对他代码的影响（必须提前知道）

他的 `StorageBackend` 接口**没有租户上下文**：

```python
def load_workspaces(self) -> dict[str, Any]:        # 没有 org 参数
def save_workspaces(self, value: dict[str, Any]) -> None:
```

加多租户意味着：

1. 接口要带租户上下文 —— 二选一：
   - **构造时绑定**：`create_backend(runtime_root, org_id="org-001")` → 改动最小，backend 内部所有 SQL 自动带 `WHERE org_id = %s`
   - **调用时传参**：`load_workspaces(org_id)` → 更灵活，但要改 `base.py` + 3 个实现 + `store.py` 所有调用点
2. 所有唯一键/索引带 `org_id` 前缀：`UNIQUE (org_id, biz_id)`
3. 可选：PG 行级安全（RLS）作为第二道防线

**代价评估**：接口只有 14 个方法，改动量可控。**推荐构造时绑定**（改动面最小，且 `Store` 本来就是单租户实例）。

> ⚠️ 注意：`2026` 年现状是单租户（`org_id DEFAULT 'org-001'`），所以这属于**预留**而非立即使用——但**唯一键和索引必须先带上 `org_id`**，否则以后加租户要重建索引和数据。

---

## 九、落地步骤

| # | 步骤 | 说明 |
|:--|:--|:--|
| 1 | 合并分支，解 `store.py` 冲突 | 接缝留 `self.backend`(他的) + `self._facts_repo`(我方的 M3) |
| 2 | 补回**原子写** | `storage/json_backend.py` 改用 `repository/io.py` |
| 3 | 子表改**局部重建** | 三处无 `WHERE` 的 `DELETE` 加 `workspace_id` 条件 |
| 4 | 给实体表加**融合列** | `org_id` / `biz_id` + `UNIQUE(org_id,biz_id)` / `position` / `payload` / `deleted_at` |
| 5 | 按**准入规则**重排提升列 | G 组 ~50 列 → ~12 列 + payload（同迁移内完成） |
| 6 | 加 **CHECK 约束** + 归一函数 | 6 态契约、章节枚举、偏离枚举 |
| 7 | 用 **L 组**登记 payload 路径 | `meta_column` 记「列 ← 路径 + 归一函数 + 约束」 |
| 8 | 补 **F 组**运行可观测 | `analysis_run` / `analysis_run_stage` / `llm_call_log` |
| 9 | 写**完整性校验** | 代码写入的 key 集合 vs `meta_column` 路径集合，差集报警 |

---

## 十、融合后的能力对照（一张表看结论）

| 维度 | 队友 | 我方 | **融合后** |
|:--|:--:|:--:|:--:|
| 数据永不丢 | ✅ | ⚠️ | ✅ |
| 加字段免迁移 | ✅ | ❌ | ✅ |
| 按字段查询/聚合 | ⚠️ | ✅ | ✅ |
| 值域严格约束 | ❌ | ✅ | ✅ |
| 多租户 | ❌ | ✅ | ✅ |
| 幂等 upsert | ✅ | ✅ | ✅ |
| 顺序保持 | ✅ | ❌ | ✅ |
| 并发安全 | ❌ | ✅ | ✅ |
| 原子写（防损坏） | ❌ | ✅ | ✅ |
| 运行可观测 | ❌ | ✅ | ✅ |
| 治理/血缘 + 漏列自检 | ❌ | ⚠️ | ✅ |
| 渐进切换（json/dual/db） | ✅ | ⚠️ | ✅ |
| 已有真实数据验证 | ✅ | ⚠️ | ✅ |

**没有一行是"二选一"——每一项都能同时拿到。**
