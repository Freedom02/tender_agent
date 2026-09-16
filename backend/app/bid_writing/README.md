# 标书撰写模块（app/bid_writing）契约说明

本包是「多智能体并行开发」的对齐基准。所有子模块只依赖 `schemas.py` 定义的字段。

## 输入（全部复用现有层，禁止重写）

| 符号 | 来源 | 关键字段 |
|---|---|---|
| `fine_review` | `store.match_project()` 的 `fineReview` | `rows`（comparison rows）、`hardBlockers`、`rejectionRisks`、`unresolvedMandatory`、`summary` |
| `row`（comparison） | `fine_review.rows[i]` | `requirementId/id`、`category`、`requirement`(raw)、`status`、`reason`、`evidence`、`suggestion`、`mandatory`、`isRejection`、`responseType`、`sourceLocator` |
| `project` | `store.catalog` 里的 CatalogItem | `title`、`parsed`（projectName/projectCode/tendererName/…） |
| `company` | `storage.build_profile(facts)` | `company`、`qualifications`、`certificates`、`performances`、`personnel`、`financial_evidences`、`products`、`parameters`、`credentials` |
| `knowledge` | `storage.build_eligibility_knowledge(facts)` | `qualifications`、`projectReferences`、`personnel`、`financialRecords`、`credentials`、`products`、`capabilities` |

## 输出契约（schemas.py）

- `build_section()` → 章节 dict（`sectionId/title/source/kind/requirementIds/content/sourceRefs/status`）
- `build_deviation_row(row)` → 偏离表行（`deviation` 由 `STATUS_TO_DEVIATION` 唯一映射）
- `build_bid_document()` → 标书文档（`plan` + `deviationTables{technical,commercial}` + `proofread`）
- `build_source_ref()` → 溯源引用（`factKey/factId/name/evidencePath`）

## 子模块分工（一个智能体一个文件，互不写对方文件）

| 模块 | 文件 | 职责 | 复用 |
|---|---|---|---|
| 规划 | `planning.py` | 章节树 + 要求归属（已完成） | fineReview.rows |
| 偏离表 | `deviations.py` | rows → 技术/商务偏离表 | `build_deviation_row` |
| 商务标书 | `commercial.py` | 固定文书 + 事实表格化 | project + company |
| 技术标书 | `technical.py` | 逐条响应撰写（RAG+溯源） | knowledge + `call_openai_compatible` |
| 组装 | `assemble.py` | 章节→Word（docxtpl） | plan + deviationTables |
| 暗标 | `anonymize.py` | 身份信息屏蔽 | assemble 产物 |
| 校对 | `proofread.py` | 漏响应/一致性/暗标残留/敏感词 | `summarize_comparisons` |

## 硬性约束（所有子模块必须遵守）

1. **防幻觉**：生成正文只能引用「招标要求原文 + 命中 knowledge 对象」，资料未覆盖→输出缺失占位，绝不编造参数/业绩/人员。
2. **溯源**：每个 AI 段落带 `sourceRefs`（`build_source_ref`）。
3. **偏离表不调 LLM**：只读 comparison 的 status/reason/evidence。
4. **一对一归属**：一条 requirement 只进一个技术章节（见 `planning.CATEGORY_TO_SECTION`）。

## 开发顺序

Phase 0 规划（本包已完成）→ 并行：deviations / commercial / technical / proofread / 前端
→ 串行：assemble → anonymize。
