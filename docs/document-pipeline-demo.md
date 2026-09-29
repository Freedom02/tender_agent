# 单文档端到端展示与节点状态记录

## 目的

单文档演示不应只输出一段终端日志。后续验收、人工复核和前端节点状态页都需要同时看到：

- 本次运行使用的原文件、解析器和版本；
- 每个阶段是否完成、耗时多少、是否有 warning/error；
- 阶段的输入和输出 artifact；
- Requirement/Evidence 能否沿着 `sourceSegmentIds + sourceQuote` 回到 DocumentIR；
- P09 的匹配结果是否来自真实企业台账还是合成演示台账。

因此 `scripts/run_document_pipeline_demo.py` 采用“运行包”作为展示单位。

## 执行

在真实项目根目录 `tender-agent/tender-agent` 下执行：

```powershell
python scripts/run_document_pipeline_demo.py
```

仓库不保证存在内置样本。建议显式指定任意本地 PDF/DOC/DOCX 文件：

```powershell
python scripts/run_document_pipeline_demo.py `
  --input "D:\path\to\your-document.docx" `
  --run-id RUN-DEMO-MANUAL-001
```

## 运行包

输出目录：

```text
runtime/demo-pipeline/projects/DEMO-PIPELINE/runs/<runId>/
  README.md
  manifest.json
  run.json
  stages/
    p02_parse_document/document_ir.json
    p03_section_tree/section_tree.json
    p04_chunking/chunks.json
    p05_classification/classifications.json
    p07_requirement_extraction/extraction.json
    p08_evidence_validation/validation.json
    p06_fact_persistence/analysis_artifact.json
    p09_technical_service_matching/matching.json
```

### 面向人的入口

`README.md` 是按时间线排列的摘要，适合人工查看。它展示节点状态、耗时、关键计数、输出文件和 P09 最终快照。

### 面向程序的入口

`manifest.json` 是后续 UI/API 的稳定入口，包含：

- `runId/projectId/documentVersionId`；
- 节点 `nodeId/executionStatus/qualityStatus/reviewRequired`；
- 节点依赖 `dependsOn`、输入 artifact `inputs` 和输出 artifact `outputs`；
- `run.json` 和摘要文件的位置；
- 本次是否离线、是否启用 LLM、企业台账是否为合成数据。

### 面向审计的入口

`run.json` 是 `AnalysisRunRecorder` 持久化的运行账本，包含：

- 每个节点的状态、耗时、输入/输出 artifact、warning/error；
- parser/schema/prompt/LLM 元数据；
- run 级指标和人工变更数；
- 失败时的失败节点和错误。

## 节点语义

| 节点 | 产物 | 主要查看点 |
|---|---|---|
| P02 | `document_ir.json` | parser、segments、tables、locators、warnings |
| P03 | `section_tree.json` | 章节树、父子关系、`isMajor`、质量结果 |
| P04 | `chunks.json` | contextPrefix、segmentIds、预算、表格原子性 |
| P05 | `classifications.json` | primaryType、route、人工复核比例 |
| P07 | `extraction.json` | Requirement/Evidence 数量、类别、引用 |
| P08 | `validation.json` | verified/invalid、来源错误、有限重试预算 |
| P06 | `analysis_artifact.json` | 统一事实层、版本隔离、持久化对象 |
| P09 | `matching.json` | 技术/产品/服务状态、企业证据、覆盖率、建议 |

P06 在执行上落在 P08 之后，是因为最终持久化 artifact 要写入已经通过来源校验的 Requirement/Evidence；它仍然是统一事实层的承载节点，而不是额外的一次重新抽取。

## 这是否适合以后查看每个节点状态

适合，原因是展示格式和状态记录共用同一个模型：

1. `run.json` 可直接被现有 `GET /api/projects/{project_id}/analysis-runs/{run_id}` 返回；
2. `manifest.json` 解决了 UI 查找 artifact 和构建节点连线的问题；
3. 每个节点的指标是结构化字段，不需要从日志文本中解析；
4. 单阶段失败时仍会在 `run.json` 写入失败节点，便于断点重跑和人工介入；
5. artifact 路径是相对运行包的稳定引用，后续迁移到对象存储时只需替换路径解析层。

节点必须同时展示两个状态：

- `executionStatus`：`pending/running/completed/partial/failed/skipped`，回答“有没有跑完”；
- `qualityStatus`：例如 `ready_for_next_stage/usable_with_review/ready_for_validation`，回答“结果能不能直接给下游”。

`reviewRequired` 单独存在，避免把“运行成功但仍需人工复核”误显示成完全通过。

## 当前边界

- P09 使用脚本内置的合成企业台账，只能证明匹配契约、证据约束和产物链路，不能证明真实企业匹配准确率。
- 当前演示为同步离线执行；若以后改成队列，需要把 `running` 状态在节点开始时立即持久化，而不是只在节点结束时写入。
- `manifest.json` 当前保存了依赖 artifact 路径；后续可以增加 `artifactHash`、schema version 和重试编号，用于缓存命中和断点恢复。
- OCR、跨页表格和人工标注 gold 仍属于已有待办，不在本次展示层改动。
