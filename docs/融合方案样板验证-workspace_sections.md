# 融合方案样板验证 · `workspace_sections`

> 结论：**A 往返保真 PASS | B 投影正确 PASS | C 完整性 PASS | D 规格生效 PASS | E 加字段免迁移 PASS**
>
> 实测：我方 `bid_section` **49 列 → 16 列（削减 67%）**，代码契约 15 个 key **一个不丢**，
> 且**新增字段不需要改表**（验证中新增 2 个契约外 key，列数 16 → 16 未变，完整读回）。

---

## 一、验证方法

按融合方案（`docs/PG表设计融合方案-规范化与payload保真.md`）给 `workspace_sections` 做样板：

| 维度 | 采用 |
|:--|:--|
| **表结构** | ✅ **队友的**（字段名/类型/顺序/`PRIMARY KEY`/`payload` 保真照抄他的 `schema.sql`） |
| **规格标准** | ✅ **严格的**（多租户 `org_id`、值域 `CHECK`、审计时间戳、索引） |
| 数据 | ✅ **真实数据**：本地 PG 的 `bid_document.plan`（6 元素）+ `bid_section`（6 行），来自 `workspace-6af8cb36c9` |
| 隔离 | ✅ 建在独立 `fusion_sample` schema，**不动 `public`** |
| 驱动 | `pg8000`（沙箱下 `pip` 装不上；改为直接下载 wheel + `zipfile` 解到工作区） |

---

## 二、三条实测修正（重要）

验证过程中发现我方原设计与真实数据有**实质性偏差**，必须记录：

### 修正 1：真实 `bidDocument.plan[]` 元素**只有 5 个 key**

```
plan 元素 key 统计: {'kind': 6, 'note': 1, 'title': 6, 'sectionId': 6, 'requirementIds': 4}
```

样本：
```json
{"kind":"commercial","note":"固定文书，模板驱动…","title":"投标函","sectionId":"c-01","requirementIds":[]}
{"kind":"technical","title":"资格审查响应","sectionId":"q-01","requirementIds":["qualification-0007","qualification-0013"]}
{"kind":"deviation","title":"技术规格偏离表","sectionId":"d-01"}
```

**不是 ~50 个 key。** 之前「`bid_section` 缺 ~50 列」的判断基于列设计反推，与真实持久化形态不符。

### 修正 2：代码契约是**精确 15 个 key**

`backend/app/bid_writing/schemas.py::build_section`（L236-252）返回：

```
sectionId · title · source · kind · writingProfile · materialScopes
parentSectionId · level · order · required · responseType
requirementIds · content · sourceRefs · status
```

**以这 15 个 key 作为完整性基准**，而不是以列数为基准。

### 修正 3：一批字段是**派生**的，不该各自建列

| 字段 | 派生自 | 出处 |
|:--|:--|:--|
| `writingProfile` | `(source, kind, responseType)` | `resolve_writing_profile()` |
| `responseType` | `(source, kind)` 缺省推导 | `build_section` L219-228 |
| `materialScopes` | `writingProfile` | `material_keys_for_writing_profile()` |
| `level` / `order` | 归一化（`max(v, 1)` / `max(v, 0)`） | `build_section` L216-217 |

> 这与我们自己的设计约定一致——「**可派生的字段不存，由读层重建**」。

### 修正 4：我方 `bid_section` 的 49 列里，12 个 jsonb 列**在真实数据中全空**

```
template_blocks / template_tables / filled_tables / blanks_to_fill
evidence_entries / evidence_groups / requirement_response_rows
edit_history / agent_results / source_refs / material_scopes / extension
```

真实 6 行中这些列**全部是 `[]`**。它们符合"嵌套、不查询"的定义 → 按准入规则应**留在 payload**。

---

## 三、样板 DDL（可直接使用）

```sql
CREATE SCHEMA IF NOT EXISTS fusion_sample;

CREATE TABLE fusion_sample.workspace_sections (
    -- ══ 队友原样（表结构：字段名/类型/顺序照抄他的 schema.sql）══
    workspace_id      text   NOT NULL,
    section_id        text   NOT NULL,
    title             text,
    kind              text,
    source            text,
    section_level     integer,
    sort_order        integer,
    parent_section_id text,
    content           text,
    payload           jsonb  NOT NULL,
    -- ══ 严格规格新增（多租户 + 值域契约 + 审计）══
    org_id            text   NOT NULL DEFAULT 'org-001',
    status            text,
    writing_profile   text,
    required          boolean,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT workspace_sections_pkey  PRIMARY KEY (org_id, workspace_id, section_id),
    CONSTRAINT ws_sec_kind_chk   CHECK (kind            IS NULL OR kind            IN ('commercial','technical','deviation','attachment')),
    CONSTRAINT ws_sec_source_chk CHECK (source          IS NULL OR source          IN ('template','ai','table','attachment','copied_template')),
    CONSTRAINT ws_sec_status_chk CHECK (status          IS NULL OR status          IN ('pending','generating','generated','missing')),
    CONSTRAINT ws_sec_profile_chk CHECK (writing_profile IS NULL OR writing_profile IN ('technical','commercial','template','attachment'))
);

CREATE INDEX ws_sec_ws_idx  ON fusion_sample.workspace_sections (org_id, workspace_id, sort_order);
CREATE INDEX ws_sec_gin_idx ON fusion_sample.workspace_sections USING gin (payload jsonb_path_ops);
```

**逐项对应**：

| 部分 | 来源 | 说明 |
|:--|:--|:--|
| 前 10 列 | **队友原样** | 一行未改，连列顺序都保持 |
| `org_id` | 严格 | 多租户；主键改为 `(org_id, workspace_id, section_id)` |
| `status` | 严格 | 取自代码契约 `SECTION_STATUS`，他的表里**没有** |
| `writing_profile` | 严格 | 取自代码契约 `WRITING_PROFILE` |
| `required` | 严格 | 代码契约字段，要查 |
| `created_at` / `updated_at` | 严格 | 审计；他的子表没有时间戳 |
| 4 个 `CHECK` | 严格 | 值域契约，枚举取自 `bid_writing/schemas.py` |
| `payload` | 队友 | ★ 全保真，契约外 key 与未来新 key 都自动落这里 |

---

## 四、五项验证结果

### A · 往返保真（payload 读回 vs 原始 JSON）

```
逐字节等价: 6/6  PASS
```
以 `sort_keys=True` 归一后逐字节比较，6 条真实章节全部等价 → **payload 保真成立**。

### B · 投影正确（提升列 vs payload 派生值）

```
投影一致: 6/6  PASS
```
逐行核对 `title/kind/source/section_level/sort_order/parent_section_id/status/writing_profile/required` → 与 payload 派生值完全一致。

### C · 完整性（代码契约 15 key vs 实际落位）

| 契约 key | 落位 | 依据 |
|:--|:--|:--|
| `sectionId` | 列 `section_id`（主键） | 主键 + JOIN |
| `title` | 列 `title` | 要查/排序 |
| `source` | 列 `source` | CHECK + 要查 |
| `kind` | 列 `kind` | CHECK + 要查 |
| `writingProfile` | 列 `writing_profile` | CHECK + 要查 |
| `parentSectionId` | 列 `parent_section_id` | JOIN |
| `level` | 列 `section_level` | 排序 |
| `order` | 列 `sort_order` | 排序 |
| `required` | 列 `required` | 要查 |
| `content` | 列 `content` | 长正文 |
| `status` | 列 `status` | CHECK + 要查 |
| `materialScopes` | payload | 派生自 `writingProfile`，不独立查 |
| `responseType` | payload | 派生自 `source + kind` |
| `requirementIds` | payload | 覆盖关系另有 `workspace_requirement_coverage` |
| `sourceRefs` | payload | 嵌套数组，GIN 可查 |

**契约 15 key 全部有落位，0 遗漏。**
真实数据独有的契约外 key（`note`）**自动进 payload，无需改表**。

### D · 严格规格生效（负向测试）

| 测试 | 结果 | 说明 |
|:--|:--|:--|
| D1 脏值不阻塞写入 | **PASS** | `kind="第七种类型"`、`status=""`、`level="abc"` → payload 保真写入成功，投影列归一为 `NULL`（不丢事实） |
| D2 CHECK 拦截非法值 | **PASS** | 绕过归一直接写非法 `kind` → `23514 check_violation` 被拒 |
| D3 多租户唯一键 | **PASS** | 主键含 `org_id`，同 `(org, ws, section)` 唯一 |

> **D1 + D2 同时成立**，正是融合方案「归一在写入层、约束在列上」的验证：
> **严格性保留**（投影里的值一定合法）**且脏数据绝不丢整行**。

### E · 加字段免迁移（payload 保真的核心价值）

```
新增 2 个契约外 key，列数 16 → 16（未变）: PASS
新 key 完整读回: {"brandNewFieldV2": {"nested": {"deep": [1,2,3]}, "中文": "未来才加的字段"}, "anotherNewList": ["a","b"]}
```

**这就是"一劳永逸"的实证**：代码将来新增字段，表结构一行不改、无需迁移，数据也不丢。

---

## 五、列数对比

| | 列数 | 说明 |
|:--|:--|:--|
| 我方现行 `public.bid_section` | **49**（其中 jsonb 12） | 过投影：14 个 jsonb 列在真实数据中全空 |
| 融合样板 `fusion_sample.workspace_sections` | **16**（队友原样 10 + 严格规格 6） | 契约 15 key 零丢失 + 契约外 key 自动进 payload |

**削减 33 列（67%）**，同时**能力只增不减**（多了多租户、值域约束、payload 保真）。

---

## 六、复现方式

```powershell
# 依赖（沙箱下 pip 装不上 anaconda，改为下载 wheel 到工作区）
python E:\code\uinoproject\tenderagent\_fusion_sample\roundtrip.py
# 报告（UTF-8，控制台是 GBK 会乱码）
Get-Content E:\code\uinoproject\tenderagent\_fusion_sample\report.txt -Encoding UTF8
```

脚本：`_fusion_sample/roundtrip.py`（gitignored，可重建）
报告：`_fusion_sample/report.txt`

---

## 七、下一步建议

> ⚠️ **表名用我方的**——本样板是拿队友的 `workspace_sections` 做的**手法验证**，
> 验证的是「`payload` + 准入规则」这套手法可行，**不是要用他的表替代我方的表**。
> 我方 **44 张**表**一张都不删、不改名、不合并**（A 组 10 张已删）；只做**列级**收缩。

1. **按同一模式改造我方标书表**（表名与拆分保持我方的）：
   `bid_section`（最该先改，现状 49 列）、`bid_document`、`bid_plan`、`workspace`、`workspace_draft`
   - 预期同样大幅削减**列**；可对 `workspace` 里 `fineReview.rows` 的 **61 个 key** 做准入裁决
2. **把准入裁决结果写进治理元数据**（L 组 `meta_column`）：记录「列 ← payload 路径 + 归一函数 + 约束」，
   然后用**代码写入的 key 集合 vs `meta_column` 路径集合的差集**做自动完整性校验，根治"漏列"
3. ✅ **`bid_section`（49 列）→ 已拍板：执行列级收缩为 ~16 列 + `payload`**
   （表名 / 主键 / 行数 / 外键关系**全都不变**）；裁决结果须登记进 L 组 `meta_column`
4. **`match_item` / `match_assessment` 为空表**（实测 0 行）——E 组的投影设计需按同一套准入规则重新裁决
   ⚠️ **空表 ≠ 该删表**：它们是"还没落地"，E 组的 6 态匹配契约需要行级查询与聚合能力
