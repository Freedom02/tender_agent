# P05 章节分类与内容路由

> 状态：已完成，待阶段验收
> 日期：2026-09-19

## 本阶段结论

章节分类是 SectionTree 的派生投影，不负责重新切章节。`primaryType` 表示内容主类，`usageTags` 和 `route` 表示后续哪些流程消费它，两者不混用。

## 实现位置

- `backend/app/tender_matching/section_classifier.py`
  - 分类枚举和别名。
  - 规则分类器。
  - 低置信度判定。
  - 可选 LLM 分类回调和 Pydantic 校验。
  - 路由策略表。
- `backend/tests/test_section_classifier.py`
  - 覆盖技术章节、父章节继承、LLM 合法返回、LLM 非法返回和提示词边界。

## 分类顺序

```text
当前标题直接命中
  -> 当前章节直接正文摘要信号
  -> 父章节/大章节容器仅作为低置信度上下文
  -> 高风险/低置信度/多类型章节调用 LLM 复核
  -> LLM 结果解析与枚举校验
  -> 模板/废标/路由护栏再次覆盖
  -> LLM 失败或结果缺失：保留规则结果并记录人工复核/调用失败
```

默认离线调用不会依赖 LLM。真实在线评测和生产调用方可以提供 `llm_classifier`；触发条件包括低置信度、多个类型命中、模板/响应表、废标/无效投标信号和采购需求总览章节。返回必须通过 `LLMSectionClassification` 校验；兼容 `typeHits` 字符串或数组、带代码围栏的 JSON，以及批量结果顶层数组/对象。

当前实现已经拆成两层：

- 章节层：输出 `headingType`、`containerType`、`descendantTypeHits`、`primaryType`、`typeHits`。`headingType` 只来自当前标题直接命中，不再把 SectionTree 继承来的 `sectionType` 当作当前标题类别。
- 条款层：P07 抽取出的候选正文会调用 `classify_clause()` 重新分类，正文明确命中优先于父章节；输出写入 `category`、`categoryHits`、`routeTags`。因此正文技术条款不会简单继承“投标人须知”，响应表/投标函模板不会进入企业能力匹配。
- 精细条款范围：只保留技术、资质、业绩、人员、财务、商务、报价、时间和否决风险。`service_plan`（售后、培训、运维、质保）保留章节级分类和写标上下文，但暂不逐条生成 Requirement。招标基本信息走 `projectFields`；模板、合同范本、普通投标须知、评标流程说明和目录性条目不再逐条生成 Requirement。

LLM 每次只接收一个批次的章节投影，不接收整份文件：

- `sectionId`、章节标题、完整父章节路径。
- 规则结果：`rulePrimaryType`、`ruleTypeHits`、`ruleConfidence`、触发原因。
- 正文摘要：章节正文压缩空白后最多 900 个字符。

提示词要求 LLM 判断“下游路由主类”，而不是只看主题关键词。它可以返回多个 `typeHits`，但只能选择一个 `primaryType`。LLM 返回后仍由确定性护栏处理：响应表强制 `document_format` 且不进企业匹配；废标/无效投标条款强制 `rejection` 且不进企业匹配；采购内容总览保留 `scope` 主类并保留技术/商务等多标签。

## 路由用途

当前主类包括：

`basic`、`scope`、`technical`、`service_plan`、`qualification`、`performance`、`personnel`、`financial`、`commercial`、`evaluation`、`submission`、`document_format`、`rejection`、`schedule`、`pricing`、`contract`、`attachment`、`other`。

路由用途包括：

`screening`、`matching`、`display`、`writing`、`review`、`manual_review`。

技术、产品、服务和资质章节会进入企业匹配；报价、废标、时间、文件格式等章节主要进入风险/展示/写标路由，不会被错误地当成技术能力匹配输入。

## 样本检查

9 份真实 PDF/DOCX/DOC 均可以完成规则分类。大多数章节由规则直接分类，低置信度主要集中在标题为 `other` 或正文语义不足的章节；这些章节才适合后续 LLM 兜底。当前没有接入 WorkBuddy，也没有把 LLM 作为必需依赖。

数字孪生应用实训平台招标文件在线复测（154 个章节）：

- LLM 候选 94 个，分 6 批调用，6/6 批成功，94/94 条结果通过解析和枚举校验。
- LLM 结果来源 94 条，规则/内容规则保留 60 条；路由契约 100% 一致。
- 人工复核率从规则护栏后的 29.22% 降到 24.68%。
- `技术和服务要求响应表`、`商务条件响应表` 等模板均为 `document_format`，保留 `technical/commercial` 多标签但 `matching=false`。
- `投标报价超过最高限价`、`投标无效`、`符合性审查不合格` 等条款均为 `rejection`，不进入企业能力匹配。

## 当前边界

- 真实分类 gold 尚未填入本地 `evaluation/tender_document_gold.json`，因此暂不宣称 Macro-F1 达标；上述结果是结构、路由和抽样检查，不是人工标注准确率。
- LLM 复核目前通过 OpenAI-compatible 批量接口接入，仍保留回调接口，不绑定 WorkBuddy 或具体 Agent 框架。
- 条款级分类目前仍是确定性规则，尚未接条款级 LLM；后续只建议对低置信度、多类型和长混合条款做 LLM 兜底，避免全量调用。
- `needsHumanReview` 对多类别混合章节会保留人工复核标记，即使规则置信度较高。
- P05 不抽取 Requirement，不查询企业材料，不生成最终投标结论。

## 2026-09-20 重新分类复测

本轮按“层级上下文 + 正文条款级分类”重做，评测脚本为 `scripts/evaluate_p05_reclassification.py`，报告输出：

- `evaluation/p05_reclassification_350181_20260920.json`
- `docs/P05-reclassification-350181-evaluation.md`

指定样本：本机评测目录中的一份脱敏 DOCX（样本不随仓库提供）。

结果：

- 章节数 154，分类结果与 SectionTree 一一对应，路由契约通过。
- 章节主类分布：`document_format=35`、`contract=30`、`submission=21`、`rejection=19`、`qualification=11`、`pricing=10`、`technical=7`、`scope=6`、`commercial=4`、`service_plan=3`、`evaluation=3`、`financial=2`、`basic=2`、`other=1`。
- 重点修正：`SEC-0016 (5)招标内容及要求` 从继承类 `submission` 修正为 `scope`；`SEC-0029/0030/0132/0147` 等响应表保持 `document_format` 且 `matching=false`；`SEC-0038 投标报价超过预算金额或最高限价` 为 `rejection` 且 `matching=false`。
- 第一轮全量条款曾抽取 356 条，其中大量来自模板、合同范本、投标须知和评标流程说明；这些内容不应逐条进入人工队列。
- 收紧精细条款范围后，条款级抽取降为 118 条；跳过非精细条款 186 条，其中服务方案条款 10 条；技术候选 30 条；`document_format/rejection` 误入企业匹配为 0。
- 条款级人工复核从 264 条降到 38 条。剩余复核主要来自资质/财务长表格、少量商务混合条款，以及需要人工确认的多类型条款。

结论：P05 当前适合作为后续流程的受控输入，状态为 `suitable_with_review`。但这不是人工 gold 准确率，后续验收需要补人工标注后再算章节主类、条款主类和路由召回。

## 本轮严格验收

对 9 份真实样本逐章节检查：

- 分类结果与 SectionTree 数量和 ID 一一对应。
- `primaryType` 全部属于注册枚举。
- `usageTags` 与 `route` 策略表 100% 一致。
- 分类没有改变章节边界或来源。
- 规则分类平均 `100/100`；8 份直接通过，贵州响应文件人工复核率约 `72%`，因此保留 `usable_with_review`。

结论：路由契约可以进入后续流程，但人工复核标记必须继续透传；当前不能宣称章节语义 Macro-F1 已达标。

## 下一阶段 P06

冻结统一事实层和 runtime artifact 持久化，让 DocumentIR、Section、Chunk、Requirement、Evidence 通过版本化 JSON 共享。
