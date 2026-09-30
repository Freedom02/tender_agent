# P07 结构化要求抽取

状态：已完成，待阶段验收
日期：2026-09-19

## 目标

从 `DocumentIR`、章节分类和 Chunk 中提取后续匹配与撰写都能复用的结构化要求。首屏关注技术、产品、服务要求；资质、商务、时间、废标和提交要求作为风险清单输入。

## 实现位置

- `backend/app/tender_matching/requirement_extractor.py`
- `backend/app/tender_matching/fact_models.py`
- `backend/tests/test_requirement_extractor.py`

## 输出

- 项目字段：项目名称、项目编号、采购人/代理机构、预算、截止时间、开标时间、服务期限。
- `RequirementFact`：分类、原文、标准化文本、强制性、废标标记、重要性、来源 Segment、Evidence ID。
- `EvidenceFact`：原文摘录、来源 Segment、章节、页码、表格 ID、置信度。
- `RequirementExtractionResult`：统一版本号、统计信息和警告。

## 当前规则

1. 仅从已切好的 Chunk 提取，不重新读取原始文件。
2. 技术/服务/资质/商务等分类由章节分类结果提供，分类不改变章节边界。
3. 依据“必须、应当、不得、至少、提供、具备、要求”等信号识别候选条款。
4. 标题、背景说明和重复表头不单独生成 Requirement。
5. 每条 Requirement 必须绑定来源 Segment 和 Evidence；无法绑定时留给 P08 标记复核。
6. 默认不调用 LLM，后续可在低置信度 Chunk 上接入严格 JSON 输出的 LLM。

## 已知限制

- 当前是第一轮规则抽取，可能产生重复条款或把同一段复合句拆得偏细。
- 跨 Chunk 合并、表格表头去重和同义要求归并由后续质量优化继续处理。
- 提取结果不直接作为最终展示，前端应使用技术匹配视图和风险投影。

## 本轮严格验收

对 9 份真实样本重新抽取：

- 共生成 `2,292` 条 Requirement 和 `2,396` 条 Evidence。
- Requirement/Evidence ID 唯一，来源 Segment 均存在，原文摘录均非空。
- 标准化要求重复率最高约 `0.96%`。
- 目录标题、页码行和项目背景句过滤规则已加强，抽取数量较上一轮明显下降。
- 结构和来源契约为 `9/9` 通过，但这不等价于语义 Precision/Recall。

结论：P07 可以向 P08 输出，但当前仍需把“复合句拆分、表格表头去重、要求归并”作为精度待办，不以抽取数量作为质量目标。

## 验收标准

- 所有抽出的 Requirement 都能回到 `documentVersionId`、Segment 和原文。
- 技术/产品/服务要求优先级可被统计。
- 没有原文证据的条款不会作为确定事实进入匹配。
- P08 校验失败时，Requirement 自动进入人工复核。

## 验证命令

```powershell
python -m pytest -q backend\tests\test_requirement_extractor.py
python -m compileall -q backend scripts
```
