# 卷、章、节与分册导出

## 数据流

1. 本地确定性解析先从招标文件“投标文件格式/组成”内容中提取 `submissionStructure`，并作为 `TemplateDetectAgent` 输入上下文。智能体识别分册模式、补充属性和有来源依据的遗漏；明确卷名、卷内条目和顺序以本地结构为准。`ParseBundle.submissionStructure` 是独立持久化的标准字段。历史包无此字段时默认为 `unspecified`。
2. `VolumePlanAgent` 消费解析包，先确认卷及顺序。解析中明确给出的卷是权威；没有明确卷时可以生成逻辑卷，但不能将逻辑卷解释成必须独立提交。
3. `OutlinePlanAgent` 消费 `plannedVolumes`、格式模板、投标编制要求和评分项，在每卷下生成章、节。目录生成阶段不执行全量条款映射；进入正文生成或正文编辑时，再由 `RequirementMapAgent` 将要求和评分项映射到响应章节并缓存。保存后的计划仍是扁平 `BidSection[]`，通过 `parentSectionId` 表达树；卷为 `nodeType=volume`、`responseType=section_group`。
4. 多条要求却只得到平铺目录时，Pi 规划视为失败。本地规划器保留解析到的格式条目并补齐章、节，再由 `arrange_volumes` 统一卷节点。归属不明的章节进入“待确认归属”卷，不会自动拆分导出。
5. 前端目录树可编辑卷、章、节，保存接口不变：`PUT /api/workspaces/{id}/bid-document/plan`。旧计划再次规划时会补逻辑卷；保存后保持节点 ID 和内容。
6. `POST /api/workspaces/{id}/bid-document/export` 读取 `bidDocument.submissionStructure`。仅当 `mode=separate` 且有明确原文定位、至少两卷、卷顺序和章归属均与解析规则吻合时逐卷输出 Word；否则输出一份 Word，并在归属不符时给出警告。每份 Word 的目录、正文和偏离表仅包含本卷章节。

## 模板原文绑定

- 固定模板需要 `templateId` 或 `formatItemId`，并在对应文档结构范围内找到唯一、完全匹配的标题锚点。
- DOCX 片段只包含标题锚点至下一个编号格式条目之前的段落和表格，不复制整个格式章节。
- 锚点缺失、重复或无法验证范围时记录警告并使用文本回退，不猜测范围，也不把同一大片段挂给多个格式条目。
- `d-01` / `d-02` 是兼容旧计划的通用偏离表 ID；由明确卷内条目生成时必须归属到对应卷，不得作为跨卷共享节点重复导出。

## 审核边界

- `separate` 需要人工复核原文定位和实际提交媒介；来源字段是智能体提取结果，不代表自动法律合规判断。
- 旧数据没有可信的分册依据时默认单份，不把商务/技术逻辑分组误称为强制分册。
- 删除或调整显式卷后，如果与解析规则不一致，导出安全地回退为单份，并提示核对。
- 正文富文本、复杂 Word 模板及真实项目的 Pi 解析质量仍需逐例人工验收。
