# P12 回归测试与可观测性

状态：已完成（2026-09-19）
环境：项目 Python 虚拟环境

## 目标

让一次分析可以回答三件事：

1. 这次使用了哪个解析器、Schema、Prompt 和模型。
2. 结果经过了哪些阶段，哪一步失败或发生了降级。
3. 新版本是否比已接受的样本基线变差。

## 运行记录

`Store.match_project()` 为每次分析创建：

```text
runtime/projects/{project_id}/runs/{run_id}.json
```

记录包括：

- `runId`、`projectId`、`documentVersionId`
- `parserName`、`parserVersion`、`schemaVersion`、`promptVersion`
- `llmModel`、调用次数、成功/失败次数、token usage
- `stages[]`：解析、资格评估、企业相关性、要求抽取、企业匹配、决策摘要
- 每个阶段的状态、耗时、warning、error、输入/输出 artifact 路径
- `manualChangeCount`：细匹配人工审核变更次数
- 汇总指标：要求数量、匹配状态、决策建议等

LLM 调用只记录元数据，不保存提示词、API Key 和完整正文。

## 查询接口

```text
GET /api/projects/{project_id}/analysis-runs
GET /api/projects/{project_id}/analysis-runs/{run_id}
```

## 回归入口

```powershell
# 首次建立或明确接受新的样本基线
python scripts\run_tender_regression.py --accept-baseline

# 日常回归
python scripts\run_tender_regression.py
```

统一入口执行：

- 解析基线
- 真实招标文件决策 E2E
- 后端测试
- Python 编译检查
- 前端类型检查和构建

## 门禁

当前黄金标注还没有 `reviewed` 样本，因此门禁只比较：

- 样本集合是否变化
- 可解析样本比例
- 解析警告是否明显增加
- E2E 成功率
- E2E 失败数是否为零

黄金标注完成后，再增加：

- SectionTree 边界 Precision / Recall / F1
- 章节分类 Macro-F1
- 要求抽取 Precision / Recall / F1
- Evidence 定位准确率
- 技术匹配结论准确率

## 当前基线摘要

| 指标 | 当前值 |
|---|---:|
| 解析样本 | 9 |
| 可解析率 | 100% |
| 文本字符数 | 517,275 |
| Segment 数 | 7,041 |
| 章节数 | 1,080 |
| 表格数 | 122 |
| 决策 E2E 样本 | 8 |
| E2E 成功率 | 100% |
| E2E 失败数 | 0 |

机器可读产物：

- `evaluation/tender_regression_baseline.json`
- `evaluation/tender_regression_report.json`
- `evaluation/tender_baseline_report.json`
- `evaluation/tender_decision_e2e_report.json`

## 剩余风险

- `tender_document_gold.json` 仍需人工完成章节、要求和证据标注。
- 解析样本包含响应文件，而决策 E2E 只评估 8 个招标文件，两个阶段的样本数不能直接相加比较。
- 当前运行记录是本地文件存储，尚未接入集中式日志、指标时序库或 CI 平台。
