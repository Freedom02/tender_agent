# P08 证据定位与质量校验

状态：已完成，待阶段验收
日期：2026-09-19

## 目标

在 Requirement 进入企业匹配或标书撰写前，验证它引用的 Evidence 是否真的存在于当前文档版本，并保留失败原因。

## 实现位置

- `backend/app/tender_matching/evidence_validation.py`
- `backend/tests/test_evidence_validation.py`

## 校验内容

- `documentVersionId` 是否一致。
- Evidence 引用的 `segmentId` 是否存在。
- `sourceQuote` 是否能在来源 Segment 中找到。
- `sectionId` 是否存在且与来源 Segment 相交。
- 页码范围是否和来源 Segment 一致。
- `tableIds` 是否和来源表格一致。
- Requirement 引用的 Evidence 是否存在。
- Requirement 是否有 Evidence。
- Requirement 的 `sourceSegmentIds` 是否存在。
- 相同标准化要求但来源摘录不同，生成冲突警告，不自动合并。

## 结果语义

- Evidence 通过：`verificationStatus=verified`。
- Evidence 失败：`verificationStatus=invalid`。
- 引用无效 Evidence 的 Requirement：自动设置 `needsHumanReview=true`。
- 缺少非强制条款证据：警告。
- 缺少强制条款证据：错误。

校验器不会根据字符串搜索重新猜测来源位置。Segment ID 是主定位，原文搜索只用于验证摘录。

## 有限重试预算

```json
{
  "network": 3,
  "json_repair": 2,
  "evidence_relocate": 1
}
```

重试预算只是运行记录，不允许通过重复重试绕过 Evidence 校验。

## 验收标准

- 错误摘录不能被标记为已验证。
- 无效 Evidence 的 Requirement 必须进入人工复核。
- 页码、章节、表格定位错误可单独识别。
- 校验结果可以 JSON 序列化并在 Pydantic 读取时再次验证。

## 本轮严格验收

对本机样本目录中的 9 份 PDF/DOCX/DOC 运行 P04-P08 链路：

- 9/9 真实抽取结果通过 Evidence 来源校验。
- 9/9 Evidence 验证率为 `100%`。
- 对错误摘录、错误 Segment、错误版本和缺失 Evidence 的破坏性反例，9/9 样本全部拦截。
- 有物理页码的 PDF 额外通过错误页码反例；没有页码的 DOCX/DOC 将该反例标记为不适用，而不是伪造页码能力。

本轮修复了一个高风险问题：页码不匹配现在会把 Evidence 标记为 `invalid`，不会继续保留为 `verified`。

结论：P08 的来源安全契约可以进入 P09；跨页表格、OCR 坐标和复杂版面仍属于待办。

## 验证命令

```powershell
python -m pytest -q backend\tests\test_evidence_validation.py
python -m compileall -q backend scripts
```
