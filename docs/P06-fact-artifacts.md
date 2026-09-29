# P06 统一事实层与持久化

> 状态：已完成，待阶段验收
> 日期：2026-09-19

## 本阶段目标

冻结匹配和写标共同消费的事实契约，并用当前 runtime 文件系统保存版本化解析产物和事实产物。暂不建设新的数据库。

## 实现位置

- `backend/app/tender_matching/fact_models.py`
  - `RequirementFact`
  - `EvidenceFact`
  - `FactRecord`
  - `AnalysisRun`
  - `AnalysisArtifact`
  - `FactArtifactStore`
- `backend/tests/test_fact_models.py`

现有 `contracts.py` 和 `analysis_contracts.py` 继续作为历史字典输入、兼容归一化和前端投影，不被删除，也不作为新事实层的唯一契约。

## 持久化布局

```text
runtime/projects/{project_id}/source/
runtime/projects/{project_id}/parsed/{document_version_id}.json
runtime/projects/{project_id}/facts/{document_version_id}.json
runtime/projects/{project_id}/runs/{run_id}.json
```

当前实现已提供 `parsed`、`facts` 和 `runs` 的严格读写。`AnalysisRun` 由 P12 的
`AnalysisRunRecorder` 在真实分析链路中写入，运行记录路径为
`runtime/projects/{project_id}/runs/{run_id}.json`。

## 事实关系

```text
DocumentIR
  -> Section / Chunk
  -> RequirementFact
  -> EvidenceFact
  -> AnalysisArtifact
       -> AnalysisRun
```

`EvidenceFact` 必须引用当前 `DocumentIR` 中存在的 `segmentId`；`RequirementFact` 可以引用多个 Evidence ID。所有事实都带 `documentVersionId`，重跑时不会覆盖其他版本的事实。

## 版本字段

已固定或保留：

- `fileHash`
- `documentVersionId`
- `schemaVersion`
- `parserName` / `parserVersion`
- `promptVersion`
- `llmModel` / `llmParameters`
- `createdAt`
- `status`
- `warnings` / `errors`

## 验证结果

```powershell
python -m pytest -q backend\tests\test_fact_models.py backend\tests\test_section_classifier.py backend\tests\test_document_chunking.py
```

结果：`13 passed`。

覆盖内容：

- Requirement 和 Evidence 共享文档版本。
- Evidence 引用不存在的 segment 会被拒绝。
- DocumentIR/AnalysisArtifact JSON round-trip。
- 非法事实 artifact 读取时被 Pydantic 拒绝。
- 路径穿越组件被拒绝。

## 当前边界

- 尚未把所有历史分析结果一次性迁移成新 artifact，避免覆盖用户已有 runtime 数据。
- `Store.analyze()` 仍以兼容字典流程为主，后续 P07/P08 会逐步把新抽取结果写入 `AnalysisArtifact`。
- 尚未增加 Requirement/Evidence 的数据库索引；当前 JSON 文件适合单项目和低并发场景。

## 本轮严格验收

对 9 份真实样本执行 DocumentIR、Chunk、Requirement、Evidence 和 AnalysisRun 的保存/读取：

- 9/9 DocumentIR JSON 往返通过。
- 9/9 AnalysisArtifact JSON 往返通过。
- 9/9 版本隔离通过。
- 9/9 `AnalysisRun` 具备 `projectId`、`documentVersionId` 和 `runId`。

本轮补齐了 `build_analysis_artifact()` 中 `AnalysisRun.projectId` 的写入，避免运行记录脱离项目。

结论：当前低并发 JSON 持久化可以继续使用；数据库索引、文件锁和历史产物迁移暂不阻塞，列入待办。

## 下一阶段 P07

从已分类章节和 Chunk 中抽取项目基本信息、技术/产品/服务要求、资质要求、商务/提交要求和关键风险；技术产品服务要求优先，所有结果进入 RequirementFact 并绑定 Evidence。
