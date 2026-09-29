# P04 章节内安全切分与上下文继承

> 状态：已完成，待阶段验收
> 日期：2026-09-19

## 本阶段目标

把已经确定的 SectionTree 转换为适合后续抽取和 LLM 调用的 Chunk，同时保证章节路径、原文 segment、页码和表格定位不丢失。

## 实现位置

- `backend/app/tender_matching/document_models.py`
  - 新增 `DocumentChunk` Pydantic 模型。
- `backend/app/tender_matching/document_chunking.py`
  - 新增 `chunk_document()`。
  - 新增稳定 token 估算和来源归属逻辑。
- `backend/tests/test_document_chunking.py`
  - 覆盖父子章节重叠、长段落切分、表格定位和异常预算。

## 核心算法

```text
DocumentIR.segments + SectionTree
  -> 找到包含每个 segment 的所有章节
  -> 选择 segmentIds 最短、层级最深的章节作为唯一归属
  -> 按原始 order 合并相邻 segment
  -> 未超预算：保留为章节块
  -> 超预算：先在 segment 边界切，再在句号/换行处切长段
  -> 表格行尽量作为原子单元
  -> 写入 sectionPath、contextPrefix、segmentIds、tableIds、locator、页码
```

父章节的 `segmentIds` 是包含后代的闭区间，不能直接把它们全部交给模型，否则同一段正文会被多次处理。P04 使用“最短覆盖章节”规则将其转换为不重复的 chunk 归属。

## Chunk 最低字段

- `chunkId`
- `documentVersionId`
- `sectionId`
- `sectionPath`
- `contextPrefix`
- `content`
- `segmentIds`
- `tableIds`
- `startLocator` / `endLocator`
- `pageStart` / `pageEnd`
- `tokenCount`
- `fingerprint`
- `splitReason`

## 验证结果

定向测试：

```powershell
python -m pytest -q backend\tests\test_document_chunking.py backend\tests\test_document_structure.py backend\tests\test_document_parser.py
```

结果：`15 passed`。

真实样本：

- 9 份 PDF/DOCX/DOC 全部生成 Chunk。
- 所有 Chunk 都有 `segmentIds` 和起止 locator。
- 所有 Chunk 都有非空章节路径，包括前置内容和未分类内容。
- DOCX 样本中的表格 Chunk 保留 `tableIds`。
- 长文档会以 `section_overflow` 拆分，过长单段会以 `segment_overflow` 拆分。

## 当前边界

- 当前默认预算是 `800` 个估算 token，实际调用方可以按任务 profile 调整。
- 未引入字符重叠窗口，避免同一要求因为重叠被重复抽取；需要上下文时使用 `contextPrefix` 和相邻 segment。
- 尚未实现跨页表格合并，后续在解析增强和要求抽取阶段处理。
- Chunk 是从 `DocumentIR` 派生的视图，不会修改原始 DocumentIR 或 SectionTree。

## 本轮严格验收

使用 `scripts/evaluate_p04_p09.py` 对本机样本目录中的 9 份 PDF/DOCX/DOC 重新评测：

- P04 平均 `99.44/100`，最低 `95/100`，8 份直接通过，网易样本因 SectionTree 大章节精确率仍需复核。
- 所有样本的 Chunk 来源覆盖、上下文路径、locator、顺序和预算检查通过。
- 同一套检查在 800 token 和 400 token 压力档均通过。

结论：P04 可以作为 P05-P09 的输入。跨页表格合并和更细的重叠窗口暂不阻塞，列入待办。

详细报告：[P04-P09-stage-evaluation.md](P04-P09-stage-evaluation.md)。

## 下一阶段 P05

章节分类与内容路由：先使用标题和结构规则，再用章节内容分析处理低置信章节，LLM 只作为有限兜底，不让分类逻辑改变章节边界。
