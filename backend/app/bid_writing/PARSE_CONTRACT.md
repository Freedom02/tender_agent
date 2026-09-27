# 标书工作台解析产物契约（PARSE_CONTRACT）

## 文件组成与分册规则（当前已接入）

解析后的标准 `ParseBundle` 新增独立字段 `submissionStructure`：

```json
{
  "mode": "separate",
  "confidence": "explicit",
  "sourceLocator": "第二章 投标文件的组成 第3款",
  "volumes": [
    {
      "volumeId": "commercial",
      "title": "商务文件",
      "order": 1,
      "required": true,
      "sourceLocator": "第二章第3款",
      "formatItemIds": ["commercial-01"],
      "items": [
        {
          "formatItemId": "commercial-01",
          "title": "投标函（格式）",
          "itemType": "fixed_form",
          "responseType": "fixed_form",
          "conditional": false,
          "templateId": "tpl-commercial-01",
          "sourceLocator": "第二章第3款 / （一）投标函（格式）"
        }
      ]
    }
  ],
  "warnings": []
}
```

`TemplateDetectAgent` 输出同名对象；`build_parse_bundle` 规范化后持久化。
`mode` 为 `single`、`separate`、`unspecified`；只有明确原文定位、`confidence=explicit`
且至少两卷时允许 `separate`。旧解析包默认 `unspecified`，逻辑分卷不等于必须分别提交。
`TemplateDetectAgent` 的输入上下文包含本地确定性提取的 `submissionStructure`；
明确卷名、卷内条目和顺序以该结构为准，智能体只补充属性。固定模板只有在解析器能够
以唯一标题锚点确定原文范围时才绑定 DOCX 片段；锚点缺失或重复时不扩大复制范围，
而是记录警告并回退到文本模式。
`VolumePlanAgent` 先确认卷，`OutlinePlanAgent` 再生成卷内章、节；写作计划的卷节点是
`nodeType=volume`、`responseType=section_group`，其余节点通过 `parentSectionId` 归属。
导出规则及回退边界见 [VOLUME_FLOW.md](./VOLUME_FLOW.md)。

> 用途：定义「招标文件解析提取」应产出的完整数据结构，作为标书工作台与解析器的唯一对齐基准。
> 消费者：`frontend/src/shared/types.ts`（FineReviewResult / BidDocument / Workspace）
> 生产者：解析器（本会话中用 Python 产出，未来可迁移进后端 `tender_matching`）

---

## 0. 定位

工作台依赖两份产物驱动「目录生成 → 正文填充编辑」：

| 产物 | 驱动 | 对应 types.ts |
|---|---|---|
| **fineReview**（细匹配/条款库） | 偏离表、技术逐条响应、废标风险 | `FineReviewResult` |
| **outline**（编制骨架） | 目录生成 | `BidSection[]`（经 planning 派生） |

外加一份**评分要素**（当前 types.ts 未定义，需补）：

| 产物 | 驱动 | 需新增类型 |
|---|---|---|
| **eval_items**（评分要素） | 正文章节/篇幅 + 评标索引页 | `EvalItem[]` |
| **templateFragments**（模板片段） | 原始 Word 复制、固定字段和表格填充 | `ParseBundle.templateFragments[]` |

---

## 1. fineReview（核心，工作台已定义消费契约）

即 `FineReviewResult`。解析器必须产出其中 `rows`（条款库），其余字段（summary/risk 等）按从 rows 派生。

### 1.1 rows 元素（ReasoningRow）—— 必填字段

| 字段 | 类型 | 必填 | 说明 | 来源 |
|---|---|---|---|---|
| `requirementId` | string | ✅ | 条款全局唯一 id（如 `REQ-0001`） | 解析器生成 |
| `id` | string | ✅ | 同 requirementId（兼容旧字段） | 同上 |
| `requirement` | string | ✅ | 条款**原文**（忠实，保留 ★/数字/单位） | 招标文件原文 |
| `rawText` | string | ✅ | 同 requirement（兼容） | 同上 |
| `category` | string | ✅ | 枚举之一 | 见下方 category 枚举 |
| `mandatory` | bool | ✅ | 是否强制项 | ★/必须/否决 → true |
| `isRejection` | bool | ✅ | 是否否决项 | 废标/无效投标/★ → true |
| `status` | string | ✅ | 匹配状态 | 初值 `"待澄清"` |
| `reason` | string | ⬜ | 匹配理由（工作台对标后填） | 后置 |
| `evidence` | string | ⬜ | 证据摘要 | 后置 |
| `suggestion` | string | ⬜ | 补正建议 | 后置 |
| `responseType` | string | ⬜ | 响应类型 | 见 RESPONSE_TYPES |
| `sourceLocator` | string | ⬜ | 原文定位（段/表坐标） | 解析器 |
| `sourceSegmentId` | string | ⬜ | 来源片段 id | 解析器 |
| `module` | string | ⬜ | 前端三大模块 | 派生 |

### 1.2 category 枚举（对齐 contracts.py / types.ts）

```
qualification / performance / personnel / financial / credit / region /
compliance / technical / service_plan / commercial / document / pricing /
contract / rejection / other
```

### 1.3 派生字段（解析器按 contracts.py 同口径产出）

- `fineReview.summary`：`MatchSummary`（totalRequirementCount / statusCounts / mandatoryCount / ...）
- `fineReview.hardBlockers` / `rejectionRisks` / `unresolvedMandatory`：从 rows 筛出否决/未满足/未解决强制项
- `fineReview.extraction`：`{method, provider, modelUsed, warning}`

---

## 2. outline（编制骨架，驱动目录）

即 `extract_response_outline` 的 `items`：

```json
[
  {"sectionId": "c-01", "title": "投标函", "source": "template", "kind": "commercial"},
  {"sectionId": "t-02", "title": "技术方案", "source": "ai", "kind": "technical"},
  {"sectionId": "d-01", "title": "技术规格偏离表", "source": "table", "kind": "deviation"}
]
```

`sectionId` 复用固定池（c-XX 商务 / t-XX 技术 / d-XX 偏离 / p-01 报价 / a-01 附件）；
同一个通用偏离表节点只能归属到一个卷，存在明确卷内条目时不再自动追加全局通用偏离表。
source ∈ {template, ai, table, copied_template, attachment}，kind ∈ {commercial, technical, deviation, attachment}。

---

## 3. eval_items（评分要素，需前端补 EvalItem 类型）

```json
{
  "items": [
    {"factor": "技术部分", "item": "技术响应方案说明", "score": 28, "criteria": "从总体设计…评审"},
    {"factor": "商务部分", "item": "业绩", "score": 4, "criteria": "每份同类合同得2分…"}
  ],
  "total": 100,
  "method": "综合评分法"
}
```

前端需在 types.ts 新增：

```ts
export interface EvalItem {
  factor: string;      // 评分因素（技术部分/商务部分/价格分…）
  item: string;        // 评分点（技术响应方案说明/业绩/项目团队配备…）
  score: number | null;// 分值
  criteria: string;    // 评分标准
}
export interface EvalResult {
  items: EvalItem[];
  total: number | null;
  method: string;
}
```

并挂到 `Workspace.reasoning.evaluation`（或 `FineReviewResult.evaluation`）。

### 3.1 `templateFragments`（固定模板和表格）

固定模板不能只保存纯文本，还必须绑定原始 DOCX 的结构位置：

```json
{
  "schemaVersion": "1.0",
  "templateId": "tpl-001",
  "sectionId": "c-01",
  "title": "投标函格式",
  "fragmentType": "fixed_template",
  "templateType": "fixed_template",
  "sourceLocator": "投标文件格式章节",
  "sourceFileArtifact": "runtime/projects/project-001/source/tender.docx",
  "fragmentArtifact": "runtime/projects/project-001/parse/template-fragments/tpl-001.json",
  "copyMode": "ooxml_body_range",
  "bodyChildStart": 42,
  "bodyChildEnd": 58,
  "blocks": [],
  "tables": [],
  "editableFields": [
    {
      "fieldId": "field-001",
      "label": "投标人名称",
      "sourceKey": "companyName",
      "valueType": "text",
      "required": true,
      "editable": true
    }
  ],
  "lockedBlocks": [],
  "warnings": []
}
```

约束：

1. `editableFields` 只表示允许填充的区域；没有来源的字段必须保留空位。
2. `lockedBlocks`、固定表头、签字盖章位置和原始顺序不能由智能体改写。
3. `copyMode=ooxml_body_range` 时，导出层从原始 DOCX 复制段落和表格，并重映射图片关系。
4. 没有可复制原始结构时使用 `text_fallback`，前端和导出结果必须显示回退告警。

---

## 4. 解析器输出落点

解析器产出一份 JSON，结构对齐后端 `workspace.reasoning`：

```json
{
  "fineReview": { ...FineReviewResult... },
  "evaluation": { ...EvalResult... },
  "outline": { "items": [...], "sourceDetail": "..." }
}
```

写盘到：`<项目根>/runtime/projects/<project_id>/parse_result.json`，
工作台新增一个读取入口（或后端 `GET /api/workspaces/{id}/parse-result`）读取它。

---

## 5. 解析器实现清单（本会话交付）

1. **条款抽取**：从招标文件正文 + 表格，按 `document_fine_matching.py` 的原子拆分规则（`一、/（一）/1. /（1）`），抽取原子条款，标 `category` / `mandatory` / `isRejection`，产出 `ReasoningRow[]`。
2. **目录抽取**：`outline.extract_response_outline` 已实现，直接复用（从「投标文件格式」章节抽 response outline）。
3. **评分要素**：`eval_parser.parse_eval_items` 已实现，从「评标办法/评分标准」章节抽 `EvalResult`。
4. **聚合**：`resolved_input.py` 的 `resolve_tender` 已实现，归一成统一对象。
5. **模板片段**：`template_fragments.py` 已实现固定模板绑定、落盘、目录挂载和导出引用。

> 已写好的评分解析、聚合和模板片段模块继续复用；
> 尚缺的是「条款抽取」这一步的落地产物（等你上传文件后执行）。

---

## 6. 待你确认的 3 个契约细节

1. **eval_items 挂在哪**：`workspace.reasoning.evaluation`（新）还是并入 `fineReview`？（默认新字段 `evaluation`）
2. **评分要素是否要进偏离表/对标**：本契约默认**只用于评标索引 + 正文篇幅倾斜**，不进 requirement 对标（避免与 fineReview.rows 双重计数）。
3. **产物落点**：是写盘 `parse_result.json` + 工作台加读取接口，还是你后端直接跑我的解析脚本、把结果塞进 `workspace.reasoning`？（取决于后端解析链路后续怎么接）
