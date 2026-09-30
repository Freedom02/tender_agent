# P09 技术、产品与服务匹配

状态：已完成，待阶段验收
日期：2026-09-19

## 目标

优先回答工作人员最关心的问题：本公司的产品、技术和服务能力是否覆盖招标文件的关键要求，是否值得继续制作标书。

## 实现位置

- `backend/app/tender_matching/technical_service_matching.py`
- `backend/app/tender_matching/company_facts.py`
- `backend/app/tender_matching/tech_units.py`
- `backend/app/tender_matching/deterministic.py`
- `backend/app/tender_matching/semantic_judge.py`
- `backend/tests/test_technical_service_matching.py`

## 匹配顺序

1. 只接收 `RequirementFact` 或兼容字典，忽略资质、商务、价格等非技术/服务条款。
2. 先做硬性确定性检查，例如硬件型号、数值规格和明显不匹配。
3. 再做产品、参数、服务能力的词法召回。
4. 结果不足且显式配置了模型时，才调用语义判断；LLM 只能从已召回的企业事实中选择。
5. 所有 LLM 返回的事实 ID 都会和企业台账做交叉校验，未知 ID 被丢弃。
6. 企业事实没有证据路径时，即使文本相似，也不能判为“满足”。

## 状态

```text
满足
部分满足
不满足
待补材料
待澄清
不适用
```

“没有找到企业材料”默认是“待补材料”，不是“不满足”。只有确定性规则识别到硬性技术/服务冲突时，才产生“不满足”和硬性障碍。

## 输出

每条匹配结果包括：

- Requirement ID 和原文。
- 状态、置信度和判断来源。
- 企业事实 ID。
- 企业证据路径和摘录。
- 缺口。
- 风险级别。
- 是否需要人工复核。
- 是否为硬性障碍。

汇总结果包括状态计数、硬性障碍、技术缺口、材料缺口、覆盖率和辅助建议：

```text
建议继续
谨慎继续
不建议继续
待澄清
```

建议不是法律意义上的最终投标结论，也不会替代人工确认。

## 验收标准

- 明确匹配、部分匹配、硬性不匹配、无企业材料四类样例均有测试。
- 没有企业证据不能输出“满足”。
- 硬性不匹配会进入 `hardBlockers`，不被平均覆盖率掩盖。
- 结果可回到招标 Requirement 和企业事实证据路径。
- 未启用 LLM 时仍能使用规则匹配和安全兜底。

## 本轮严格验收

匹配契约测试覆盖：

- 有企业证据的明确满足。
- 缺少企业证据时返回“待补材料”。
- 硬件规格缺失不误判为“不满足”。
- 非技术/服务要求不会进入技术匹配。
- 状态、证据、硬性障碍和汇总计数保持一致。

固定 4 条契约样例 `4/4` 通过。9 份真实样本共进入 305 条技术/服务匹配条目；当前评测企业台账与招标要求不是同一项目台账，因此真实覆盖率为 `0%`，不能把它解释成匹配引擎准确率。

结论：P09 的状态和证据安全契约可以继续使用；真实语义准确率、企业事实召回和“部分满足”边界需要补充人工 gold 后再设发布门槛。

## 验证命令

```powershell
python -m pytest -q backend\tests\test_technical_service_matching.py
python -m compileall -q backend scripts
```
