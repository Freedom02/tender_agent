# 招标文件上传 → 直接标书撰写：实现方案

> 状态：持续实施中（解析包、Pi 编排、模板复用首批已落地）
> 目标：上传投标人对标的招标文件，系统自动完成「解析 → 沉淀解析产物 → 进入标书撰写 → 输出 .docx」，中间无需人工拼接字段。

---

## 一、结论先行

**能做，且现有系统已具备 90% 通道。** 唯一的工程增量是：

1. **补一个评分要素解析器**（`eval_parser.py`）—— 把上游刻意排除的「评标办法/评分标准」解析成结构化评分点，这是驱动正文章节/篇幅、生成评标索引的唯一缺失件；
2. **加一层解析产物聚合**（`resolved_input.py`）—— 把散落在 `fineReview / document.sections / project.parsed` 的产物归一成一个 `ResolvedTender`，作为标书撰写的唯一输入；
3. **接一条端到端串联入口**（`store.py` 加一个方法 + 一个 API）—— 让「上传后」一键走到 `.docx` 导出。

**不改**：现有 `fineReview.rows` 字段口径、`bid_writing` 各子模块的输入契约、`technical/commercial/deviations/assemble` 的既有实现。

---

## 二、现状盘点（实测代码，非推测）

### 2.1 现有通道已闭环

| 环节 | 现有实现 | 位置 |
|---|---|---|
| 招标文件解析 | `store.match_project` → `evaluate_tender_document` → `fineReview`（comparison rows） | `app/store.py:586` |
| 编制骨架抽取 | `outline.extract_response_outline`（已实现，从「投标文件格式」章节抽目录） | `app/bid_writing/outline.py:105` |
| 章节规划 | `plan_bid_document`：优先 `plan_from_outline`，回退固定模板 | `app/bid_writing/planning.py` |
| 偏离表 | `build_deviation_tables`（纯读 fineReview.rows，不调 LLM） | `app/bid_writing/deviations.py` |
| 商务文书 | `generate_commercial_sections`（固定文书 + 事实表格） | `app/bid_writing/commercial.py` |
| 技术正文 | `generate_technical_sections`（逐条响应，RAG+溯源） | `app/bid_writing/technical.py` |
| 组装导出 | `assemble_docx`（python-docx） | `app/bid_writing/assemble.py` |
| 编排层 | `store.plan_bid_document / generate_bid_document / export_bid_document` | `app/store.py:797~1027` |
| HTTP 入口 | `plan / generate / export / proofread` 五个端 | `app/api/bid_writing.py` |

**结论：从「招标文件已入库」到「导出 .docx」，现有代码已经能跑通**，无需重写。

### 2.2 真正缺的：评分要素（eval_items）

- `document_structure.py` 已经把「评标办法/评分标准/评分细则」识别为 `sectionType == "evaluation"`（见该文件 83～98 行）；
- 但 `contracts.py` 的 `SECTION_JUDGE_EXCLUDE` 与 `requirement_scope` 的白名单**刻意把 evaluation 排除在需求抽取之外**（合理，避免把评标流程文本灌进对标）；
- 因此 `fineReview.rows` 里**没有评分点**，导致：
  - 技术正文只能按 `planning.TECHNICAL_AI_SECTIONS` 固定的 t-01~t-07 **平铺七章**，无法按分值倾斜篇幅；
  - 无法生成「评标索引」页（评分因素→评分点→分值→页码）。

这是「评分要素」是本次唯一需要**新增解析**的产物。

---

## 三、完整实现方案

### 3.1 解析产物统一契约（ResolvedTender）

把上游三类解析产物 + 两个撰写必需输入，归一成一个对象：

```
ResolvedTender {
  composition   → 编制骨架（outline.extract_response_outline 的 items）
  requirements  → 需求条款库（= fineReview.rows）
  eval          → 评分要素（新解析：{ items, total, method }）
  projectMeta   → 项目元信息（project.parsed 归一）
  company       → 投标人企业画像
  organization  → 原始组织 fact
  knowledge     → M3 知识库（企业资产）
  risk          → 废标/否决信号（hardBlockers / rejectionRisks / unresolvedMandatory）
  summary       → fineReview.summary
}
```

**价值**：撰写层不再散读深层字段，改一处上游口径只改聚合层一处；这是「上传→撰写」链路稳定的关键。

### 3.2 改动点清单（按落地顺序）

| # | 改动 | 文件 | 类型 | 状态 |
|---|---|---|---|---|
| 1 | 评分要素解析器 | `bid_writing/eval_parser.py` | 新增 | ✅ 已写 |
| 2 | 解析产物聚合层 | `bid_writing/resolved_input.py` | 新增 | ✅ 已写 |
| 3 | 评分驱动章节规划 | `bid_writing/planning.py` 追加 `plan_from_eval` | 修改 | ✅ 已写 |
| 4 | 评标索引渲染 | `bid_writing/assemble.py` 追加 | 修改 | ⬜ 未写 |
| 5 | 技术正文评分定向 | `bid_writing/technical.py` 追加评分 criteria 注入 | 修改 | ⬜ 未写 |
| 6 | 端到端串联方法 | `store.py` 加 `resolve_and_generate_bid` | 修改 | ⬜ 未写 |
| 7 | 一键入口 | `api/bid_writing.py` 加 `resolve` 端点 | 修改 | ⬜ 未写 |
| 8 | 端到端演示脚本 | `bid_writing/demo_bid_pipeline.py` | 新增 | ⬜ 未写 |

> 已完成（✅）的三处是上一轮我按「最小闭环」先落地的；现在你要先评审方案，这三处保留还是回退由你定（见第五节）。

### 3.3 端到端调用链（目标态）

```
上传招标文件
  ↓  store.analyze(project_id)            # 已有：解析 + fineReview + sections/tables
  ↓  store.resolve_tender(workspace_id)   # 新增：聚合 ResolvedTender（含 eval_items）
  ↓
  plan     = plan_bid_document(ResolvedTender)        # 已有 + 评分驱动增强
  ↓
  sections = {
     commercial: generate_commercial_sections(...),   # 已有
     technical : generate_technical_sections(..., eval), # 已有 + 评分 criteria 注入
  }
  ↓
  deviationTables = build_deviation_tables(...)        # 已有
  ↓
  bidDocument = assemble(plan, sections, deviationTables, eval_index)  # 已有 + 评标索引
  ↓
  export_bid_document → .docx                          # 已有
```

### 3.4 各新增件职责边界（防越界）

- **eval_parser**：只解析评分点，产出 `{items,total,method}`；不碰需求对标、不碰正文。
- **resolved_input**：只聚合归一，不做任何解析与生成。
- **plan_from_eval**：只做「评分点 → 技术章节 sectionId + 分值」的映射，不生成正文。
- **评标索引**：只读 eval + 各章页码，生成一张索引表，不改正文。
- **technical 注入**：评分 criteria 仅作为生成 prompt 的**靶子字段**传入，不改 technical 的防幻觉/溯源约束。

### 3.5 已落地的模板复用阶段

当前模板复用已经接入实际撰写链路：

```text
bidFormatSpec.templates
  -> TemplateFragment 绑定 documentBlocks
  -> runtime/projects/<projectId>/parse/template-fragments/*.json
  -> 目录章节挂载 templateFragmentId
  -> Pi TableFormFillAgent
  -> 确定性字段/单元格填充
  -> DOCX 原始 body 片段复制
```

已覆盖的能力：

- DOCX 段落、表格和 body 位置索引；
- 模板片段标准结构、可编辑字段和锁定块；
- 固定模板章节与目录章节绑定；
- Pi 固定表单/表格字段填充及 `AgentResultEnvelope` 轨迹；
- 原始 DOCX 片段复制、图片关系重映射和失败时文本回退；
- 前端显示模板来源、原始 Word 复制状态、可填字段数和待补字段数。

当前边界：

1. 只有 DOCX 可以按原始 OOXML body 范围复制，PDF、旧版 DOC 和纯文本模板回退为文本渲染。
2. 缺少可靠来源的字段保留空位并进入 `blanksToFill`，不会由 Pi 猜测。
3. 页眉页脚、复杂跨文档关系、Word 内容控件和结构化富文本编辑器继续放在后续阶段。

---

## 四、实现要点与风险

### 4.1 评分要素解析的难点（需重点评审）

- 评分表列头不统一：有的「评分因素/评分点/分值/评分标准」四列，有的「评分办法(满分100分)」合并单元格，有的没有列头只有文本。
- `document.tables` 与 section 内嵌 `table_row`（`|` 分隔）两种来源都要兼容。
- 应对：`eval_parser` 已做「列头归位 → 无列头兜底 → 无表格按文本行」三级降级，解析不清时保留 `rawText` 供人工兜底。

### 4.2 评分点 → 章节映射的边界

评分点措辞千变万化（"技术响应方案说明" vs "技术方案" vs "关键技术"），映射表 `_EVAL_TO_SECTION` 是**关键词泛化**，不能保证 100% 命中。设计上：
- 命中 → 挂到对应 t-XX 章节并附分值；
- 未命中 → 归入 `t-02`（技术方案）默认，不丢弃。

### 4.3 风险清单

| 风险 | 等级 | 缓解 |
|---|---|---|
| 评分表解析不准（合并单元格/无列头） | 中 | 三级降级 + rawText 保留 + 人工可复核 |
| 评分点映射错章节 | 低 | 默认归 t-02，不丢数据 |
| 评分 criteria 注入导致正文偏离防幻觉约束 | 低 | 只作靶子字段，不改 system prompt 的「只依据资料」红线 |
| 端到端入口破坏既有 API 行为 | 低 | 新方法只读不写，不 override 现有 `plan/generate/export` |

---

## 五、已写三处代码的定位（请你定处置方式）

上一轮我已先落地三处，现方案确定前，它们可以：**保留 / 调整 / 回退**，由你确认：

| 文件 | 作用 | 建议 |
|---|---|---|
| `eval_parser.py` | 评分要素解析器（方案 #1） | ✅ 保留（这正是方案核心件）|
| `resolved_input.py` | 解析产物聚合层（方案 #2） | ✅ 保留 |
| `planning.py` 追加 `plan_from_eval` | 评分→章节映射（方案 #3） | ✅ 保留（与 #4/#5 配套）|

> 注：评分解析和聚合代码继续保留；模板复用属于独立的当前实施阶段，不改变 fineReview 的既有字段口径。

---

## 七、本轮已落地：局部修改与合规审查

### 7.1 局部修改链路

```text
BidSection（非固定模板）
  + instruction / operation
  -> SectionRewriteAgent（Pi）
  -> 确定性应用器
  -> append / rewrite / replace_selection
  -> editHistory + sourceRefs + complianceReview=stale
```

接口：

```text
POST /api/workspaces/{workspaceId}/bid-document/sections/{sectionId}/rewrite
```

固定模板章节不会进入该链路，只能使用：

```text
POST /api/workspaces/{workspaceId}/bid-document/sections/{sectionId}/fill-template
```

### 7.2 合规审查链路

```text
ParseBundle + BidDocument + fineReview
  -> 确定性基础校对与覆盖率计算
  -> ComplianceReviewAgent（Pi）
  -> ComplianceReviewResult
  -> 保存到 bidDocument.complianceReview
```

接口：

```text
POST /api/workspaces/{workspaceId}/compliance-review
```

审查只输出风险，不自动修改正文；正文、目录或模板重新生成后，已有审查会标记为 `stale`。

### 7.3 当前验证

- 后端 `unittest`：115 个测试全部通过。
- 后端 `compileall`：通过。
- 前端 `vue-tsc + vite build`：通过。
- Vite 仍有既有的单 chunk 大于 500KB 警告，不影响构建。

## 八、待你拍板的三个问题

1. **端到端入口形态**：是要一个 `POST /api/.../resolve-bid` 一键接口，还是只要 `store` 里加方法 + 命令行脚本即可？
2. **评分要素的作用范围**：`eval_items` 是只用于「评标索引页 + 正文篇幅倾斜」，还是也要进入 matching（把评分点当需求对标）？（本方案默认只用前者，后者会牵扯 requirement_scope 白名单，改动大）
3. **已写三处**：确认保留，还是先回退、等方案整体评审完再一次性落地？

当前继续按“小阶段、可回退、每步补测试”的方式推进：下一步是结构化正文块、图片/附件插入和二次合规审查，不回退已通过测试的模板复用链路。
