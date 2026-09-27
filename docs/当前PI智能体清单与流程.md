# 当前 PI 智能体清单与流程

> 盘点日期：2026-09-23  
> 依据：当前工作区代码、当前 `.env` 配置和 `runtime` 中已生成的运行产物。  
> 重点：区分“已经接入代码并可调用的智能体”“编排器节点”“本地规则模块”和“仅保留的旧提示词”。

## 1. 结论先看

当前已经接入代码、具有独立提示词并可以通过 `PiClient` 调用的逻辑子智能体共有 **14 个**：

| 阶段 | 数量 | 智能体 |
|---|---:|---|
| 招标文件解析 | 6 | `DocumentStructureAgent`、`ProjectMetaAgent`、`RequirementExtractAgent`、`EvaluationExtractAgent`、`TemplateDetectAgent`、`ParseReviewAgent` |
| 目录规划 | 2 | `OutlinePlanAgent`、`RequirementMapAgent` |
| 单章节撰写 | 3 | `MaterialMatchAgent`、`SectionPlanAgent`、`SectionDraftAgent` |
| 模板/表格填充 | 1 | `TableFormFillAgent` |
| 章节局部修改 | 1 | `SectionRewriteAgent` |
| 最终合规审查 | 1 | `ComplianceReviewAgent` |
| **合计** | **14** |  |

另外还有一个 `ParseBundleAssembler` 节点，但它**不是大模型智能体**，没有独立提示词，负责把 6 个解析节点的结果归一化并写入 `ParseBundle`。

### 当前用户操作会触发哪些智能体

| 用户操作 | 实际调用 |
|---|---|
| 上传招标文件并解析 | 6 个解析智能体，最后执行 `ParseBundleAssembler` |
| 点击“生成目录” | `OutlinePlanAgent`，然后 `RequirementMapAgent` |
| 点击某一技术/商务章节“自动撰写” | `MaterialMatchAgent` -> `SectionPlanAgent` -> `SectionDraftAgent` |
| 点击固定模板“仅填充模板” | `TableFormFillAgent` |
| 输入要求并点击“Pi 补写/重写” | `SectionRewriteAgent` |
| 点击“Pi 合规审查” | `ComplianceReviewAgent`，同时先执行本地确定性校对 |
| 手工保存章节、导出 Word | 不调用 PI |

当前 `.env` 的运行配置为：

```text
AGENT_PROVIDER=pi
PI_ENABLED=true
PI_PROVIDER=company-ai
PI_MODEL=gpt-5.5
PI_API_TIMEOUT=360
```

这里的 `AGENT_PROVIDER=pi` 是后端的总开关。它决定上传解析、目录规划、章节撰写和合规审查是否进入 Pi 分支；`PI_PROVIDER=company-ai` 是 Pi 进程内部使用的模型提供方。

## 2. 当前不是 Pi 原生多智能体，而是后端 Python 编排

当前架构中，“子智能体”是 14 套不同角色的提示词和结果规范。真正的调用方式是：

```text
FastAPI
  -> Python 编排器
    -> PiClient
      -> 启动一次 pi --mode rpc --no-session --no-tools
        -> 发送一个子智能体提示词
        -> 读取 JSON
        -> 结束本次 Pi 子进程
```

因此：

- 14 个智能体是逻辑节点，不是 14 个常驻服务。
- 解析和撰写的顺序由 `PiParseOrchestrator`、`PiWritingOrchestrator` 控制。
- 当前没有使用 LangGraph，也没有由 Pi 自己发现和调度其他子智能体。
- 当前是“FastAPI + Python 顺序编排 + Pi RPC 调用”。
- 每次子智能体调用都会由 `PiClient.run_json()` 创建一次 Pi RPC 子进程。

关键文件：

- [backend/app/pi_agent/client.py](../backend/app/pi_agent/client.py)：Pi RPC 适配、JSON 解析、超时和错误处理。
- [backend/app/pi_agent/orchestrator.py](../backend/app/pi_agent/orchestrator.py)：解析阶段 6 个节点的编排。
- [backend/app/pi_agent/writing_orchestrator.py](../backend/app/pi_agent/writing_orchestrator.py)：目录、覆盖映射、章节撰写、模板填充、重写、审查编排。
- [backend/app/pi_agent/prompts.py](../backend/app/pi_agent/prompts.py)：所有当前和历史提示词。

## 3. 通用输入输出约定

### 3.1 每个智能体的统一结果外壳

每次调用后的结果会被包装成 `AgentResultEnvelope`：

```json
{
  "runId": "project-RequirementExtractAgent",
  "agentId": "RequirementExtractAgent",
  "provider": "pi",
  "model": "gpt-5.5",
  "status": "success",
  "inputRefs": [
    "parse:documentBlocks",
    "parse:documentStructure"
  ],
  "output": {},
  "sourceRefs": [],
  "warnings": [],
  "createdAt": "",
  "finishedAt": ""
}
```

统一外壳定义在：

- [backend/app/agent_runtime/contracts.py](../backend/app/agent_runtime/contracts.py)

不同智能体的业务字段放在 `output` 中，并不要求所有智能体输出完全相同的字段。

### 3.2 解析阶段统一产物 `ParseBundle`

6 个解析智能体的结果最终进入同一个标准解析包：

```text
ParseBundle
├── sourceFile
├── documentBlocks
├── documentStructure
├── projectMeta
├── requirements
├── evaluationItems
├── bidFormatSpec
├── templateFragments
├── rawOutline
├── riskItems
├── parseReviewReport
├── artifacts
├── warnings
└── agentResults
```

`ParseBundle` 定义在：

- [backend/app/agent_runtime/contracts.py](../backend/app/agent_runtime/contracts.py)

后续目录、章节撰写和合规审查都优先读取 `ParseBundle`，而不是直接读取 Pi 的原始返回。

## 4. 上传招标文件后的完整流程

```mermaid
flowchart TD
    A[前端选择招标文件] --> B[POST /api/bid-runs]
    B --> C[创建运行记录并保存原文件]
    C --> D[BackgroundTasks 执行 process_run]
    D --> E[本地解析 PDF/DOCX/XLSX/TXT]
    E --> F[DocumentStructureAgent]
    F --> G[ProjectMetaAgent]
    G --> H[RequirementExtractAgent]
    H --> I[EvaluationExtractAgent]
    F --> J[TemplateDetectAgent]
    I --> K[ParseReviewAgent]
    J --> K
    K --> L[ParseBundleAssembler]
    L --> M[保存 parse-bundle.json]
    M --> N[前端轮询 /api/bid-runs/{runId}]
    N --> O[POST /materialize]
    O --> P[创建项目、工作区和 bidDocument 初始目录]
```

### 4.1 前端和后端接口

前端入口：

- `frontend/src/features/bid-workspace/BidWritingPage.vue`
- `frontend/src/services/api.ts`

实际接口：

| 顺序 | 接口 | 作用 |
|---:|---|---|
| 1 | `POST /api/bid-runs` | 接收上传文件，创建异步解析任务 |
| 2 | `GET /api/bid-runs/{runId}` | 前端每 2 秒轮询运行状态 |
| 3 | `POST /api/bid-runs/{runId}/materialize` | 将解析结果写入项目和工作区 |
| 4 | `GET /api/workspaces/{workspaceId}/parse-bundle` | 查看标准解析包 |
| 5 | `POST /api/workspaces/{workspaceId}/bid-document/plan` | 进入目录规划阶段 |

后端入口：

- [backend/app/api/cloud_agent.py](../backend/app/api/cloud_agent.py)
- [backend/app/cloud_agent/run_service.py](../backend/app/cloud_agent/run_service.py)

### 4.2 本地解析器先做什么

Pi 不是直接读取二进制 Word/PDF。后端会先使用本地解析器：

```text
PDF  -> parse_pdf
DOCX -> parse_docx
DOC  -> parse_doc
XLSX/XLS -> parse_xlsx
其他 -> parse_plain_text
```

本地解析器产出文本、段落、页码、表格、章节线索等 `documentBlocks`。Pi 负责在这些结构化输入上做语义识别。

## 5. 解析阶段 6 个 PI 智能体

### 5.1 `DocumentStructureAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1285-1323`
- 函数：`document_structure_prompt()`

**提示词核心**

```text
你是 DocumentStructureAgent，负责识别招标文件的章节结构。
只依据招标文件内容和本地解析块，不得使用外部知识，不得编造章节。
必须只输出一个合法 JSON 对象。
保留原文顺序；标题不确定时放入 warnings，不要猜测页码。
```

提示词还要求识别公告、须知、资格、评标办法、技术需求、商务要求、合同、投标文件格式和附件等章节，并限制最多返回 80 个关键章节。

**输入**

- 上传文件名。
- 本地解析器警告 `parserWarnings`。
- 本地解析结果 `documentPayload`。
- 招标文件文本；如果本地已经识别到章节，当前实现只额外提供较短的文本证据。
- `workspaceId`、`projectId`、项目上下文。

**输出**

```json
{
  "sections": [
    {
      "sectionId": "doc-sec-001",
      "title": "第三章 评标办法",
      "level": 1,
      "startPage": 1,
      "endPage": 1,
      "sourceLocator": "第三章 评标办法",
      "blockIds": [],
      "sectionType": "evaluation"
    }
  ],
  "warnings": []
}
```

**下游**

- 与本地结构合并为 `documentStructure`。
- 给项目基础信息、要求、评分、模板和复核智能体使用。

### 5.2 `ProjectMetaAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1326-1358`
- 函数：`project_meta_prompt()`

**提示词核心**

```text
你是 ProjectMetaAgent，负责从招标文件中抽取项目基础信息。
只依据输入内容，不得补全未知信息，不得使用外部知识。
字段缺失使用空字符串；每个已填写字段尽量提供 sourceRefs。
```

**输入**

- 招标文件文本。
- `DocumentStructureAgent` 规范化后的 `documentStructure`。
- 文件名、工作区、项目上下文。

**输出**

```json
{
  "projectMeta": {
    "projectName": "",
    "projectCode": "",
    "tendererName": "",
    "agencyName": "",
    "budget": "",
    "deadline": "",
    "deliveryPeriod": "",
    "serviceLocation": ""
  },
  "sourceRefs": [],
  "warnings": []
}
```

**下游**

- 进入 `ParseBundle.projectMeta`。
- 给要求提取、目录规划、模板填充、正文撰写和投标函使用。

### 5.3 `RequirementExtractAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1361-1401`
- 函数：`requirement_extract_prompt()`

**提示词核心**

```text
你是 RequirementExtractAgent，负责抽取所有需要投标方响应的原子要求。
每条要求必须是可执行的原子条款，不能把整章原文复制进 requirement。
每条要求必须有唯一 id，格式为 req-001、req-002……
```

提示词要求区分资格、技术、商务、服务、交付、人员、业绩、财务、信用、价格、合同、文件格式、废标/否决等类别，并识别 `mandatory`、`isRejection` 和 `responseType`。

**输入**

- 当前代码先通过本地规则抽取候选要求。
- 长文档时，将高优先级候选压缩为最多约 6000 字符的样本；短文档则直接使用全文。
- `documentStructure`。
- `projectMeta`。
- 文件名、工作区、项目。

**输出**

```json
{
  "requirements": [
    {
      "id": "req-001",
      "category": "technical",
      "requirement": "",
      "mandatory": true,
      "isRejection": false,
      "importance": "high",
      "responseType": "chapter_text",
      "sourceLocator": "",
      "sourceSection": "",
      "sourceText": "",
      "suggestedEvidence": []
    }
  ],
  "warnings": []
}
```

**特别说明**

当前实现不是“纯 Pi 抽取”：Pi 结果会和本地规则候选合并、去重，最后由编排器重新编号为 `req-001` 等。因此最终要求中可能同时存在：

- `extractionSource=pi` 或模型识别结果；
- `extractionSource=rule` 的本地规则候选。

### 5.4 `EvaluationExtractAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1404-1441`
- 函数：`evaluation_extract_prompt()`

**提示词核心**

```text
你是 EvaluationExtractAgent，负责抽取评标办法和评分细则。
只依据招标文件，不得使用外部知识，不得猜测分值。
如果没有明确评分信息，返回空数组并在 warnings 中说明。
```

**输入**

- 招标文件文本。
- `documentStructure`。
- 已归一化的 `requirements`，用于关联要求 ID。
- 文件名、工作区、项目。

**输出**

```json
{
  "evaluationItems": [
    {
      "id": "eval-001",
      "category": "technical",
      "score": 20,
      "criterion": "",
      "criteria": "",
      "writingFocus": "",
      "sourceLocator": "",
      "relatedRequirementIds": ["req-001"]
    }
  ],
  "warnings": []
}
```

**下游**

- 进入 `ParseBundle.evaluationItems`。
- 供目录规划绑定 `evaluationIds`。
- 供章节写作计划和最终合规审查使用。

### 5.5 `TemplateDetectAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1444-1479`
- 函数：`template_detect_prompt()`

**提示词核心**

```text
你是 TemplateDetectAgent，负责识别招标文件中给定的投标文件模板和固定表格。
只依据招标文件，不得把普通说明文字当作投标模板。
```

模板规则重点识别：

- 投标文件格式。
- 响应文件组成。
- 投标函、偏离表、报价表、承诺函。
- `fixed_template`、`chapter_skeleton`、`reference_example`。
- 可编辑字段 `editableFields`。
- 不得改动的 `lockedBlocks`。

**输入**

- 招标文件中与“投标文件格式”等关键词相关的聚焦文本。
- `documentStructure`。
- 文件名、工作区、项目。

**输出**

```json
{
  "hasExplicitFormat": true,
  "formatSourceSection": "",
  "requiredOutline": [],
  "templates": [
    {
      "templateId": "tpl-001",
      "title": "",
      "templateType": "fixed_template",
      "sourceLocator": "",
      "editableFields": [],
      "lockedBlocks": [],
      "blocks": []
    }
  ],
  "requiredTables": [],
  "warnings": []
}
```

之后，后端还会从原始 DOCX 中抽取并保存 `templateFragments`，这部分不是 Pi 生成，而是本地 Word 结构处理模块完成。

### 5.6 `ParseReviewAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1482-1527`
- 函数：`parse_review_prompt()`

**提示词核心**

```text
你是 ParseReviewAgent，负责检查前面解析智能体的结果质量。
只做解析质检，不要重新编写完整解析结果。
发现不确定内容必须报告，不要用猜测掩盖缺失。
```

**输入**

- `projectMeta`。
- `documentStructure`。
- `requirements`。
- `evaluationItems`。
- `bidFormatSpec`。
- 文件名、工作区、项目。

**输出**

```json
{
  "status": "pass_with_warnings",
  "confidence": 0.0,
  "missingFields": [],
  "warnings": [],
  "riskItems": [
    {
      "level": "medium",
      "title": "",
      "detail": "",
      "sourceLocator": ""
    }
  ]
}
```

**下游**

- 进入 `ParseBundle.parseReviewReport`。
- 风险进入 `ParseBundle.riskItems`。
- 前端显示解析警告、缺失字段和风险。

## 6. `ParseBundleAssembler` 解析产物整理节点

这个节点没有 Pi 提示词，不是模型调用。

执行位置：

- [backend/app/cloud_agent/run_service.py](../backend/app/cloud_agent/run_service.py)
- `process_run()` 中解析完成后执行。

主要工作：

1. 对 6 个智能体结果执行归一化。
2. 生成 `ParseBundle`。
3. 把原始文件、解析结果和模板片段写入 `runtime/projects/...`。
4. 写入 `agentResults`，记录每个智能体的输入引用、输出、状态和警告。
5. 将任务状态改为 `outline_ready`。

解析阶段最终落盘：

```text
runtime/projects/{projectId}/parse/parse-bundle.json
runtime/projects/{projectId}/runs/{runId}/parse-result.json
```

## 7. 目录规划阶段 2 个 PI 智能体

目录规划入口：

- 前端：`api.bidDocumentPlan(workspaceId)`
- 后端：`POST /api/workspaces/{workspaceId}/bid-document/plan`
- 业务方法：`Store.plan_bid_document()`
- PI 入口：`PiClient.plan_outline()` -> `PiWritingOrchestrator.plan_outline()`

### 7.1 `OutlinePlanAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1530-1577`
- 函数：`outline_plan_prompt()`

**提示词核心**

```text
你是 OutlinePlanAgent，负责为一份招标文件规划可执行的投标文件目录。
你只负责生成章节目录和章节元数据，不撰写任何正文，不生成企业承诺。
只能依据输入的 ParseBundle 和当前已有目录。
```

关键规则：

- 优先保留招标文件明确的目录、顺序、模板 ID 和固定表格。
- 强制/废标条款必须有响应章节。
- 评分项必须绑定到章节。
- 输出必须是扁平数组。
- 通过 `parentSectionId`、`level`、`order` 表达父子关系。
- 存在多个要求时，必须生成一级、二级章节。
- 不能只返回技术偏离表和商务偏离表。

**输入**

- `ParseBundle` 的目录规划精简视图。
- `existingPlan`。
- 工作区和项目 ID。

精简视图包括：

- 项目基本信息摘要。
- 文档章节摘要。
- 要求摘要和全部要求 ID。
- 评分项摘要。
- 投标格式要求。
- 原始目录线索。

**输出**

```json
{
  "plan": [
    {
      "sectionId": "part-technical",
      "parentSectionId": "",
      "level": 1,
      "order": 1,
      "title": "技术响应部分",
      "source": "ai",
      "kind": "technical",
      "required": true,
      "responseType": "section_group",
      "requirementIds": ["req-001"],
      "evaluationIds": [],
      "templateId": ""
    }
  ],
  "warnings": []
}
```

编排器会继续做 ID 校验、父子关系扁平化、章节标准化和技术标/商务标画像判定。

### 7.2 `RequirementMapAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1580-1617`
- 函数：`requirement_map_prompt()`

**提示词核心**

```text
你是 RequirementMapAgent，负责建立招标要求、评分项与投标目录之间的覆盖关系。
你只负责映射和覆盖检查，不撰写正文，不修改招标原文，不编造企业能力。
所有 ID 必须来自输入的 ParseBundle 或已给出的目录。
```

**输入**

- `ParseBundle` 的条款映射精简视图。
- `OutlinePlanAgent` 返回的目录。
- 工作区和项目 ID。

**输出**

```json
{
  "coverage": [
    {
      "requirementId": "req-001",
      "sectionId": "t-01",
      "coverageType": "chapter_text",
      "status": "planned",
      "evaluationIds": ["eval-001"],
      "reason": ""
    }
  ],
  "uncoveredRequirements": [],
  "duplicatedRequirements": [],
  "warnings": []
}
```

**当前特殊实现**

- 每批最多处理 80 条要求。
- 要求超过 80 条时，同一个逻辑智能体会被多次调用，例如：
  `RequirementMapAgent[1/2]`、`RequirementMapAgent[2/2]`。
- 编排器会合并各批结果、去重，并把没有返回覆盖关系的要求确定性标记为未覆盖。

目录成功后，后端会写入：

```text
bidDocument.planSource = "pi_agent"
bidDocument.planningSource = "pi_agent"
bidDocument.planningStatus = "success"
bidDocument.planSourceDetail = "Pi 编排：OutlinePlanAgent + RequirementMapAgent"
```

注意：`planSource` 还可能表示解析阶段生成的初始目录来源；判断本次是否真正调用目录智能体，应优先看 `planningSource`、`planningStatus` 和 `agentResults`。

## 8. 单章节撰写阶段 3 个 PI 智能体

入口：

- 前端点击“自动撰写”。
- `POST /api/workspaces/{workspaceId}/bid-document/sections/{sectionId}/generate`
- 后端：`Store.generate_bid_section()`
- 编排器：`PiWritingOrchestrator.draft_section()`

对于技术标和商务标，调用链相同，但 `writingProfile` 和企业材料范围不同：

```text
技术/服务标 -> technical -> 技术方案、实施、质量、服务、培训、应急
商务/资格标 -> commercial -> 资格、商务、合同、财务、业绩、人员、信用
```

### 8.1 `MaterialMatchAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1620-1674`
- 函数：`material_match_prompt()`

**提示词核心**

```text
你是 MaterialMatchAgent，负责为一个投标章节匹配企业材料和可引用证据。
只做材料匹配，不写正文，不把招标要求当成企业能力。
找不到材料就返回 unmatched。
```

**输入**

- `ParseBundle` 的当前章节视图。
- 当前章节 `section`。
- 当前章节的要求覆盖关系。
- 项目字段。
- 企业画像。
- 按 `writingProfile` 筛选后的企业材料。
- `mode`，例如明标或暗标。

**输出**

```json
{
  "matches": [
    {
      "requirementId": "req-001",
      "materialIds": ["qualification-001"],
      "materialRefs": [
        {
          "materialId": "qualification-001",
          "factKey": "qualifications",
          "name": "",
          "evidencePath": ""
        }
      ],
      "matchStatus": "matched",
      "evidenceSummary": "",
      "missingReason": ""
    }
  ],
  "unmatchedRequirementIds": [],
  "warnings": []
}
```

### 8.2 `SectionPlanAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1677-1752`
- 函数：`section_plan_prompt()`

**提示词核心**

```text
你是 SectionPlanAgent，负责为一个投标章节制定可审核的写作计划。
只做写作计划，不写正文，不编造企业承诺。
计划必须让下游正文智能体逐条响应要求并引用真实材料。
```

**输入**

- 当前章节解析上下文。
- 当前章节字段。
- 当前章节要求覆盖关系。
- `MaterialMatchAgent` 的匹配结果。
- 项目字段、企业画像和已筛选企业材料。
- `writingProfile` 和 `mode`。

**输出**

```json
{
  "sectionId": "t-02",
  "title": "技术方案",
  "objective": "",
  "requirementResponses": [
    {
      "requirementId": "req-001",
      "responseStrategy": "",
      "mustInclude": [],
      "materialIds": [],
      "risk": ""
    }
  ],
  "evaluationResponses": [],
  "outline": [
    {
      "heading": "1.1 响应说明",
      "purpose": "",
      "requirementIds": []
    }
  ],
  "templateInstructions": [],
  "warnings": []
}
```

编排器会补齐模型漏掉的 requirement/evaluation 响应占位，避免下游静默漏写。

### 8.3 `SectionDraftAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1755-1835`
- 函数：`section_draft_prompt()`

**提示词核心**

```text
你是 SectionDraftAgent，负责根据已确认的写作计划生成一个投标章节正文。
严禁编造资质、证书、业绩、人员、金额、日期、参数、案例和承诺。
资料不足时写“【需人工补充：缺少具体资料】”。
```

**输入**

- 当前章节。
- `ParseBundle` 当前章节上下文。
- 当前章节覆盖关系。
- `SectionPlanAgent` 的写作计划。
- `MaterialMatchAgent` 的材料匹配结果。
- 项目字段、企业画像、已筛选材料。
- `writingProfile`、`mode`。

**输出**

```json
{
  "sectionId": "t-02",
  "title": "技术方案",
  "content": "Markdown 正文",
  "sourceRefs": [
    {
      "factKey": "",
      "factId": "",
      "name": "",
      "evidencePath": ""
    }
  ],
  "status": "generated",
  "warnings": []
}
```

后端还会检查未匹配材料。如果当前章节存在未匹配或部分匹配的要求，且正文中没有人工补充标记，编排器会自动追加：

```text
【需人工补充：缺少具体资料】
```

## 9. 固定模板、局部修改和审查

### 9.1 `TableFormFillAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1838-1895`
- 函数：`table_form_fill_prompt()`

**触发**

```text
POST /api/workspaces/{workspaceId}/bid-document/sections/{sectionId}/fill-template
```

**提示词核心**

```text
你是 TableFormFillAgent，负责给招标文件已经提供的固定模板和固定表格规划字段填充值。
只允许填写 editableFields 和明确可编辑的单元格。
lockedBlocks、固定文字、表头、签字盖章位置、格式和原始顺序不能修改。
```

**输入**

- `ParseBundle`。
- 当前模板片段 `fragment`。
- 当前章节。
- 项目资料。
- 企业画像和企业材料。

**输出**

```json
{
  "templateId": "tpl-001",
  "sectionId": "c-01",
  "status": "completed",
  "fieldValues": [
    {
      "fieldId": "field-001",
      "label": "项目名称",
      "sourceKey": "projectName",
      "value": "",
      "status": "filled",
      "sourceRefs": []
    }
  ],
  "cellValues": [],
  "blanksToFill": [],
  "warnings": []
}
```

模型只返回字段/单元格填充值，真正把值写回模板和 DOCX 结构的是本地 `template_reuse`、`template_fragments` 和导出模块。

### 9.2 `SectionRewriteAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1898-1964`
- 函数：`section_rewrite_prompt()`

**触发**

```text
POST /api/workspaces/{workspaceId}/bid-document/sections/{sectionId}/rewrite
```

**输入**

- ParseBundle 当前章节上下文。
- 当前章节目录元数据。
- 当前正文 `currentContent`。
- 用户指令 `instruction`。
- 操作类型：`append`、`rewrite`、`replace_selection`。
- 选中的正文片段 `selectedText`。
- 项目、企业画像、企业材料、明暗标模式。

**输出**

```json
{
  "sectionId": "t-02",
  "operation": "append",
  "content": "",
  "replacement": "新增或替换的 Markdown 片段",
  "sourceRefs": [],
  "changed": true,
  "blanksToFill": [],
  "warnings": []
}
```

后端应用规则：

- `append`：追加 `replacement`。
- `replace_selection`：用 `replacement` 替换选中的原文。
- `rewrite`：使用完整 `content` 或 `replacement` 替换正文。
- 固定模板章节不进入该链路。

### 9.3 `ComplianceReviewAgent`

**提示词位置**

- `backend/app/pi_agent/prompts.py:1967-2034`
- 函数：`compliance_review_prompt()`

**触发**

```text
POST /api/workspaces/{workspaceId}/compliance-review
```

**提示词核心**

```text
你是 ComplianceReviewAgent，负责审查一份投标响应文件是否覆盖招标要求、评分点和模板约束。
只做审查，不修改正文、不生成替代正文、不填充模板。
```

**输入**

- `ParseBundle` 压缩摘要。
- 当前 `bidDocument` 压缩摘要。
- `fineReview` 要求核验摘要。
- 本地确定性 `baselineReview`。
- 允许的章节 ID、要求 ID、评分项 ID。
- 项目字段、企业画像和企业材料。

**输出**

```json
{
  "status": "needs_review",
  "summary": "",
  "coverage": {
    "requirementTotal": 0,
    "requirementCovered": 0,
    "mandatoryTotal": 0,
    "mandatoryCovered": 0,
    "evaluationTotal": 0,
    "evaluationCovered": 0,
    "uncoveredRequirementIds": [],
    "uncoveredEvaluationIds": []
  },
  "riskItems": [
    {
      "riskId": "risk-001",
      "level": "高",
      "category": "漏响应",
      "title": "",
      "detail": "",
      "sectionId": "",
      "requirementId": "",
      "evaluationId": "",
      "sourceLocator": "",
      "suggestion": "",
      "sourceRefs": []
    }
  ],
  "warnings": []
}
```

最终结果是：

```text
本地 proofread
  + Pi ComplianceReviewAgent
  -> bidDocument.complianceReview
```

因此页面显示的风险不全部来自 Pi，也包括本地确定性校对。

## 10. 目录到正文的全链路

```text
ParseBundle
  -> OutlinePlanAgent
       输出 plan
  -> RequirementMapAgent
       输出 requirementCoverage
  -> 人工编辑/保存目录
  -> MaterialMatchAgent
       输出 materialMatches
  -> SectionPlanAgent
       输出 writingPlan
  -> SectionDraftAgent
       输出正文、sourceRefs、warnings
  -> 人工编辑
  -> SectionRewriteAgent（可选）
  -> ComplianceReviewAgent
       输出覆盖率、风险和待复核项
  -> 本地 Word 模板复用和导出
```

不同产物进入下一步的关系：

| 上游产物 | 下游使用者 |
|---|---|
| `documentStructure` | 项目提取、要求提取、评分提取、模板识别、复核 |
| `projectMeta` | 要求提取、目录、模板填充、正文 |
| `requirements` | 评分关联、目录、覆盖映射、正文、合规审查 |
| `evaluationItems` | 目录绑定、章节写作计划、合规审查 |
| `bidFormatSpec` | 目录、模板片段、模板填充、导出 |
| `ParseBundle` | 所有目录/撰写/审查智能体 |
| `requirementCoverage` | 材料匹配、章节计划、正文、合规审查 |
| `materialMatches` | 章节计划、章节正文 |
| `writingPlan` | 章节正文 |
| `templateFragments` | 模板填充和 Word 导出 |
| `bidDocument` | 局部修改、合规审查、导出 |

## 11. 已实现但当前没有执行的内容

### 11.1 `VisualInsertAgent` 尚未实现

总体规划文档中曾规划 `VisualInsertAgent`，用于架构图、证书、案例图片和表格插入位置规划。

当前代码中没有：

- `visual_insert_prompt()`。
- `VisualInsertAgent` 的客户端方法。
- 编排器调用。
- 对应 API。

因此它不能计入当前已经完成的 14 个 PI 智能体。

### 11.2 三个旧提示词仍保留，但当前没有调用

`backend/app/pi_agent/prompts.py` 末尾仍保留：

- `parse_prompt()`。
- `section_prompt()`。
- `plan_prompt()`。

这些是旧的一体化解析、章节撰写和目录规划提示词。当前 `PiClient` 使用的是新的细分提示词函数，代码搜索中没有它们的实际调用点。因此它们不应被计入当前实际运行链路。

### 11.3 本地规则回退不是 PI 智能体

以下情况会走本地规则或本地生成：

- `AGENT_PROVIDER` 不是 `pi`。
- Pi 目录规划失败或返回空目录。
- Pi 章节撰写失败。
- Pi 模板填充失败。
- Pi 合规审查不可用。

本地回退模块不属于 PI 智能体，例如：

- `bid_writing.planning`。
- `bid_writing.technical`。
- `bid_writing.commercial`。
- `bid_writing.proofread`。
- `bid_writing.template_reuse`。

## 12. 当前运行状态如何判断

不要只看 `planSource`。建议按以下字段判断：

### 解析是否使用 Pi

```json
{
  "parseProvider": "pi",
  "parseSourceDetail": "Pi 本地智能体解析结果",
  "parseBundle.agentResults": [
    {"agentId": "DocumentStructureAgent"},
    {"agentId": "ProjectMetaAgent"},
    {"agentId": "RequirementExtractAgent"},
    {"agentId": "EvaluationExtractAgent"},
    {"agentId": "TemplateDetectAgent"},
    {"agentId": "ParseReviewAgent"}
  ]
}
```

### 目录本次是否真正使用 Pi

```json
{
  "planningSource": "pi_agent",
  "planningProvider": "pi",
  "planningStatus": "success",
  "agentResults": [
    {"agentId": "OutlinePlanAgent"},
    {"agentId": "RequirementMapAgent"}
  ]
}
```

### 目录没有真正调用 Pi

```json
{
  "planningSource": "unplanned",
  "planningStatus": "pending"
}
```

或者：

```json
{
  "planningStatus": "fallback_local",
  "planningFallbackReason": "..."
}
```

当前 `runtime` 中已经生成的 Pi 解析包可以看到 6 个解析智能体结果；部分历史工作区仍是 `planningStatus=pending` 且 `agentResults=[]`，这代表它只完成了解析或保留了解析阶段目录草案，尚未执行当前的目录规划智能体。

## 13. 当前代码入口速查

| 功能 | 代码入口 |
|---|---|
| Pi 配置 | `backend/app/pi_agent/config.py` |
| Pi RPC 调用 | `backend/app/pi_agent/client.py:101` |
| 解析编排 | `backend/app/pi_agent/orchestrator.py:695` |
| 撰写编排 | `backend/app/pi_agent/writing_orchestrator.py:769` |
| 所有提示词 | `backend/app/pi_agent/prompts.py` |
| 上传任务 | `backend/app/cloud_agent/run_service.py:61` |
| 目录生成 | `backend/app/store.py:1371` |
| 目录 Pi 调用 | `backend/app/store.py:1636` |
| 单章生成 | `backend/app/store.py:2193` |
| 模板填充 | `backend/app/store.py:2304` |
| 局部重写 | `backend/app/store.py:2362` |
| 合规审查 | `backend/app/store.py:2783` |
| 前端页面操作 | `frontend/src/features/bid-workspace/BidWritingPage.vue` |

