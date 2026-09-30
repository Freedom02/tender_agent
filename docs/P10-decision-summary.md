# P10 决策摘要

状态：已完成
日期：2026-09-19

## 目标

让工作人员第一眼判断“是否值得继续制作标书”，而不是先浏览大量原始抽取字段。

## 实现位置

- `backend/app/tender_matching/decision_summary.py`
- `backend/app/store.py`
- `backend/tests/test_decision_summary.py`
- `backend/tests/test_store_decision_summary.py`
- `frontend/src/features/tender-matching/MatchingPage.vue`
- `frontend/src/shared/types.ts`

## 首屏契约

`DecisionSummary` 只包含首屏所需信息：

- `recommendation`：建议继续、谨慎继续、不建议继续、待澄清。
- `headline`：一句话结论。
- `projectFields`：项目名称、编号、截止时间等关键字段。
- `technicalMatch`：技术/产品/服务匹配概览和条目。
- `hardBlockers`：硬性技术或服务不匹配。
- `technicalGaps`：部分满足、待澄清的技术缺口。
- `materialGaps`：企业材料缺口。
- `qualificationRisks`：资质、业绩、人员、财务、废标风险。
- `commercialRisks`：商务、报价、合同、提交和格式风险。
- `scheduleRisks`：截止时间、开标时间和时间紧迫性。
- `needsHumanReviewCount`：需要人工判断结论的数量；不重复统计已经单列的“待补材料”，也不把普通编制、签章格式和填写清单混入其中。

## 决策规则

1. 有技术或服务硬性障碍，结论为“不建议继续”。
2. 没有技术要求或匹配信息不足，结论为“待澄清”或“谨慎继续”，不会默认建议继续。
3. 技术没有硬障碍但存在企业材料缺口、技术缺口或高优先级资质/时间风险，结论为“谨慎继续”。
4. 技术匹配通过且没有高优先级风险，结论为“建议继续”。
5. 摘要只投影已有 Requirement、Evidence 和匹配结果，不重新解析文档或重新计算企业匹配。
6. “待人工确认”只统计技术部分满足/不满足/待澄清、硬阻断、资质与时间风险，以及带有强制性或明确风险的商务/报价/合同条款；普通文书编制和格式任务进入下方清单处理。

## 运行链路

1. 完整招标文件生成 `fineReview.rows`。
2. 决策投影使用当前条款、企业事实台账和项目字段生成 `technicalMatch`。
3. `build_decision_summary()` 生成首屏摘要并写入 `reasoning.decisionSummary`。
4. 人工修改细匹配状态后，同步刷新 `fineReview` 和 `decisionSummary`。
5. 人工标记“满足”但没有可核验企业证据时，摘要仍按“待补材料”处理。

## 当前边界

- 摘要是辅助决策，不是法律意义上的最终投标结论。
- 详细原文仍通过 Requirement -> Evidence 展开查看。
- 旧 workspace 没有 `decisionSummary` 时，前端使用兼容投影；重新分析后会写入正式摘要。

## 验收标准

- 硬性障碍优先于平均覆盖率。
- 技术匹配占据主要摘要位置。
- 资质、商务、时间风险可快速扫描。
- 每个摘要项目可以回到 Requirement 或 Evidence。
- 前端无需重新解析或计算。

## 验证命令

```powershell
python -m pytest -q backend\tests
python -m compileall -q backend scripts
cd frontend
npm.cmd run build
```
