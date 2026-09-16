# 招投标智能体（tender-agent）前后端接口文档

> **维护约定**：本文档是前后端接口的**单一事实源（API 单一入口 + 契约）**，与代码同步维护。后端新增/修改路由（`app/main.py` + `app/api/*.py`）或前端改动封装（`src/services/api.ts`）、类型（`src/shared/types.ts`）时，务必同步更新本文档对应条目与文末附录。
>
> - 基础地址：开发环境后端 `http://127.0.0.1:8793`（前端 Vite 开发服务器为 `http://127.0.0.1:5173`，代理 `/api` 与 `/runtime` 到后端）；生产环境前端 SPA 由后端 `StaticFiles` 托管在 `/`。
> - 版本对齐：**tender-agent v2.0.0**，企业材料本体 **tenderhub V0.4（8 类 + 8 关系）**。

---

## 目录

1. [全局约定](#0-全局约定)
2. [健康检查 / 启动引导](#1-健康检查--启动引导)
3. [招标项目目录（Catalog）](#2-招标项目目录-catalog)
4. [实时搜索](#3-实时搜索)
5. [导入招标文件](#4-导入招标文件)
6. [分析与匹配](#5-分析与匹配)
7. [AI 问答 / Agent](#6-ai-问答--agent)
8. [工作台（Workspace）](#7-工作台-workspace)
9. [资料库（Libraries）](#8-资料库-libraries)
10. [企业材料本体（M3 读取 / CRUD）](#9-企业材料本体m3-读取--crud)
11. [企业材料导入（模板收集）](#10-企业材料导入模板收集)
12. [招标监控（Monitoring）](#11-招标监控-monitoring)
13. [消息提醒 / 企业微信](#12-消息提醒--企业微信)
14. [系统配置（Config）](#13-系统配置-config)
15. [自动化任务 / 提醒 / 运行时文件](#14-自动化任务--提醒--运行时文件)
- [附录 A：前端 API 封装对照](#附录-a前端-api-封装对照)
- [附录 B：关键数据类型速查](#附录-b关键数据类型速查)
- [附录 C：企业材料本体 8 类 + 8 关系](#附录-c企业材料本体-8-类--8-关系)
- [附录 D：已移除 / 变更的接口（历史）](#附录-d已移除--变更的接口历史)

---

## 0. 全局约定

- **响应统一为 JSON**；错误时 FastAPI 返回 `{ "detail": "错误信息" }` 与对应状态码。
- 常见错误码：`400` 参数/校验错误、`404` 资源不存在、`502` 上游（M3/大模型）错误、`501` 未实现。
- **敏感字段脱敏**：`/api/config`、`/api/bootstrap` 中 `ai.apiKey`、`wecom.webhook` 返回 `"***"`。
- **企业数据单一来源**：企业画像与材料事实来自 **M3 图库**（`tenderhub` namespace），本地不再落 `company-materials.json`，仅保留证明材料文件（`runtime/libraries/company/**`）。
- **RID 约定**：M3 对象 RID = `tenderhub_{class_snake}:{business_id}`，例如 `tenderhub_organization:org-001`。本体 CRUD 接口用 `business_id`（冒号后部分）定位对象，返回的 `id` 为完整 RID，前端用 `id.split(":").pop()` 取业务主键。

---

## 1. 健康检查 / 启动引导

### `GET /api/health`

**描述**：服务存活探针。

**响应** `200`：
```json
{ "status": "ok", "service": "tender-agent-api" }
```

---

### `GET /api/bootstrap`

**描述**：一次性返回首屏全量数据。前端 `AppShell`/`app-state.loadBootstrap()` 在启动时调用。**每次调用都会从 M3 刷新企业画像与 8 类计数（best-effort）**，保证导入后徽标/匹配数据即时更新。

**响应** `200`：
```json
{
  "company": {
    "company": "北京优锘科技股份有限公司",
    "enterpriseSize": "中型企业",
    "employeeCount": 200,
    "annualRevenueWan": 5000,
    "totalAssetsWan": 8000,
    "description": "...",
    "capabilities": ["数字孪生", "AI 智能体"],
    "service_regions": ["全国", "北京市"],
    "service_types": ["数字孪生", "数据智能"],
    "service_industries": ["智慧园区"],
    "qualifications": ["ISO27001", "CMMI 3级"],
    "certificates": [ { "name": "ISO27001", "number": "...", "validUntil": "...", "issuer": "..." } ],
    "performances": [ { "name": "...", "industry": "...", "region": "...", "amount": "...", "date": "..." } ],
    "personnel": [ { "role": "项目经理", "count": 2, "experienceYears": 5 } ],
    "financial_evidences": [ { "evidenceType": "...", "issuedBy": "...", "...": "..." } ],
    "products": [ { "name": "...", "type": "...", "category": "...", "description": "..." } ],
    "parameters": [ { "parameterId": "...", "description": "...", "productId": "...", "evidencePath": "..." } ],
    "credentials": [ { "type": "...", "name": "...", "registrationNo": "...", "...": "..." } ],
    "evidence_paths": [ { "factKey": "qualification_certificate", "evidencePath": "..." } ]
  },
  "ontology": { "name": "tenderhub", "version": "V0.4", "classes": 8, "relations": 8 },
  "materialsCounts": {
    "organization": 1,
    "qualification_certificate": 3,
    "performance_case": 5,
    "staff_member": 8,
    "financial_evidence": 2,
    "technical_product": 4,
    "technical_parameter": 6,
    "credential": 7
  },
  "config": {
    "monitoring": { "enabled": true, "frequencyMinutes": 30, "sites": [], "stats": {} },
    "notifications": { "inAppEnabled": true, "wecomEnabled": false },
    "wecom": { "webhook": "***", "configured": false },
    "ai": { "enabled": true, "provider": "...", "baseUrl": "...", "apiKey": "***", "model": "...", "configured": true }
  },
  "monitoring": { "enabled": true, "frequencyMinutes": 30, "sites": [], "stats": {} },
  "libraries": [ /* LibrarySummary[] */ ],
  "catalog": [ /* CatalogItem[] */ ],
  "reminders": [],
  "automation": { "running": false, "totalRuns": 0, "latest": null },
  "workspaces": [ /* Workspace[] */ ]
}
```

> `company` 字段即 `/api/materials/profile` 的画像结构（由 `build_profile` 派生）。

---

## 2. 招标项目目录（Catalog）

### `GET /api/catalog`

**描述**：获取招标项目列表。自动过滤废标/结果公告等非采购公告，按 `fetchedAt`/`publishedDate` 倒序，并为每项计算公司匹配度。

**查询参数**：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `query` | string | 否 | 搜索关键词 |
| `region` | string | 否 | 地区筛选 |
| `type` | string | 否 | 公告类型筛选 |
| `currentOnly` | boolean | 否 | 仅 `publishedDate >= 今天`（默认 false） |

**响应** `200`：`CatalogItem[]`（字段见附录 B）。

---

### `GET /api/catalog/{project_id}`

**描述**：获取单个项目详情（含匹配计算字段）。

**响应** `200`：`CatalogItem`。**错误**：`404` 未找到项目。

---

### `POST /api/catalog/{project_id}/read`

**描述**：标记项目为「已读」。

**响应** `200`：`{ "item": CatalogItem /* read=true */ }`

---

### `POST /api/catalog/{project_id}/follow-up`

**描述**：设置项目跟进状态。

**请求体**：
```json
{ "followUp": true, "followUpStatus": "已跟进" }
```

**响应** `200`：`{ "item": CatalogItem }`

---

### `POST /api/catalog/{project_id}/download`

**描述**：把项目原文写入本地 `runtime/projects/{id}/source/`，返回下载地址，并将 `downloadStatus` 置为「已下载」。

**响应** `200`：
```json
{
  "item": { /* CatalogItem, downloadStatus="已下载" */ },
  "download": { "status": "已下载", "fileName": "xxx.txt", "url": "/runtime/projects/{id}/source/xxx.txt" }
}
```

---

## 3. 实时搜索

### `POST /api/search/realtime`

**描述**：在已入库项目中实时筛选（与 `/api/catalog` 逻辑一致，但额外返回搜索记录摘要）。前端「检索」视图使用。

**请求体**：
```json
{ "query": "数字孪生", "region": "福州市", "type": "公开招标", "currentOnly": false }
```

**响应** `200`：
```json
{
  "items": [ /* CatalogItem[] */ ],
  "search": {
    "searchId": "search-xxxxx",
    "fetchedAt": "2026-09-15T10:30:00+08:00",
    "verifiedCount": 3,
    "addedCount": 0,
    "dedupedCount": 0,
    "sources": [ { "name": "已入库项目", "status": "已筛选", "count": 3 } ]
  }
}
```

---

## 4. 导入招标文件

### `POST /api/import`

**描述**：上传并解析招标公告/文件（TXT/DOCX/PDF/XLSX），提取结构化字段并入库。文件 < 100MB。前端「招标导入」页使用。

**请求体**：
```json
{
  "name": "招标公告-某项目.docx",
  "text": "",
  "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
  "fileBase64": "...",
  "documentKind": "notice"   // "notice"(公告,默认) | "tender_document"(完整招标文件)
}
```

**响应** `200`：
```json
{
  "item": { /* CatalogItem, dataOrigin="local_upload" */ },
  "file": {
    "name": "招标公告-某项目.docx",
    "parseStatus": "解析完成",
    "parseMethod": "docx_xml_structured",
    "warnings": [],
    "extractedTextLength": 1520
  }
}
```

---

## 5. 分析与匹配

### `POST /api/analyze`

**描述**：对项目执行资格评估 + 匹配打分，生成推理结论与工作台（`Workspace`），并写回项目 `score`/`decision`/`workspaceId`。

**请求体**：`{ "projectId": "monitor-ccgp-001" }`

**响应** `200`：
```json
{
  "item": { /* CatalogItem, status="已分析" */ },
  "workspace": {
    "id": "workspace-xxxxx",
    "projectId": "monitor-ccgp-001",
    "title": "...",
    "project": { /* CatalogItem */ },
    "reasoning": {
      "rows": [ { "kind": "qualification", "requirement": "...", "mandatory": true, "status": "满足", "evidence": "...", "risk": "低", "confidence": 0.95 } ],
      "risks": [ { "level": "中", "title": "...", "detail": "..." } ],
      "score": 85,
      "decision": "recommend",
      "relevance": { "score": 92, "reasons": [], "nextStage": "recommend" },
      "eligibility": { "eligibilityScore": 78, "eligibilityStatus": "pass", "hardBlockers": [], "shouldGetTenderDocument": true, "recommendation": "..." }
    },
    "draft": "# ...",
    "draftVersion": 1
  },
  "reasoning": { /* 同上 */ }
}
```

---

### `POST /api/tenders/match`

**描述**：批量匹配多个项目的资格条件（不生成工作台）。

**请求体**：`{ "projectIds": ["id1", "id2"] }`

**响应** `200`：
```json
{
  "results": [ { "projectId": "id1", "title": "...", "score": 85, "decision": "recommend", "rows": [], "risks": [] } ],
  "count": 2
}
```

---

### `POST /api/tenders/{project_id}/generate-response`

**描述**：根据工作台草稿生成投标响应文件（`.md` + `.json` + 可选 `.docx`）。

**响应** `200`：
```json
{
  "message": "响应文件已生成",
  "files": [
    { "name": "项目名称.docx", "url": "/runtime/projects/{id}/outputs/项目名称.docx" },
    { "name": "项目名称.md", "url": "/runtime/projects/{id}/outputs/项目名称.md" },
    { "name": "项目名称.json", "url": "/runtime/projects/{id}/outputs/项目名称.json" }
  ]
}
```

---

## 6. AI 问答 / Agent

### `POST /api/ask`

**描述**：向内置 LLM 提问（带项目上下文）；LLM 未配置时降级为规则回答。

**请求体**：`{ "question": "该项目有哪些硬性资质要求？", "projectId": "monitor-ccgp-001" }`

**响应** `200`：
```json
{ "answer": "...", "action": "answer", "sourceRefs": ["llm", "catalog", "libraries"] }
```

### `POST /api/agent/chat`

**描述**：与 `/api/ask` 逻辑一致，`action` 返回 `"agent"`（Agent 模式标识）。

**请求体/响应**：同 `/api/ask`。

---

## 7. 工作台（Workspace）

### `GET /api/workspaces/{workspace_id}`

**描述**：获取工作台详情（项目、推理结果、草稿）。**错误**：`404`。

**响应** `200`：`Workspace`（见附录 B）。

### `POST /api/workspaces/{workspace_id}/draft`

**描述**：保存草稿（Markdown 文本），`draftVersion` 自增。

**请求体**：`{ "draft": "# ..." }`

**响应** `200`：`{ "workspace": Workspace }`

---

## 8. 资料库（Libraries）

### `GET /api/libraries`

**描述**：资料库摘要列表（每库返回最近 8 份文档）。5 个内置库：`company`、`tenders`、`templates`、`searches`、`outputs`。

**响应** `200`：`LibrarySummary[]`（见附录 B）。

### `GET /api/libraries/{library_key}`

**描述**：指定库完整信息（含全部文档）。

**响应** `200`：`{ "key": "company", "name": "公司资料库", "description": "...", "documents": [...] }`

### `POST /api/libraries/{library_key}`

**描述**：上传一份文档到指定库。

**请求体**：
```json
{ "name": "资质证书.pdf", "text": "", "mimeType": "application/pdf", "fileBase64": "..." }
```

**响应** `200`：`{ "document": LibraryDocument, "libraries": LibrarySummary[] }`

### `DELETE /api/libraries/{library_key}/{document_id}`

**描述**：删除指定库内文档。

**响应** `200`：`{ "document": LibraryDocument, "libraries": LibrarySummary[] }`

---

## 9. 企业材料本体（M3 读取 / CRUD）

> 8 类（snake_case 类名）+ 8 关系，类/字段定义唯一来源为 `app/document_recognition/ontology_defs.py`。M3 连接信息来自 `agent-config.json` 的 `m3` 段（或环境变量 `M3_HOST/M3_COMPANY/M3_USER/M3_PASSWORD`），无内置默认地址；namespace `tenderhub`。
>
> 有效类名：`organization`、`qualification_certificate`、`performance_case`、`staff_member`、`financial_evidence`、`technical_product`、`technical_parameter`、`credential`
> 有效关系名：`holds`、`completes`、`employs`、`provides_financial_evidence`、`owns_product`、`has_parameter`、`owns_credential`、`certified_by`

### `GET /api/ontology/meta`

**描述**：返回 8 类 + 8 关系的 schema 元信息（含中文字段名、类型、是否主键）。

**响应** `200`：
```json
{
  "classes": [
    {
      "name": "qualification_certificate",
      "cnName": "资质认证",
      "description": "企业持有的资质认证证书",
      "attrs": [
        { "name": "qualification_id", "cn": "资质ID", "type": "varchar", "isPk": true },
        { "name": "qualification_name", "cn": "资质名称", "type": "varchar", "isPk": false }
      ]
    }
  ],
  "relations": [
    { "name": "holds", "fromClass": "organization", "toClass": "qualification_certificate", "description": "企业持有资质认证" }
  ]
}
```

### `GET /api/ontology/summary`

**描述**：8 类计数汇总（前端企业材料页 tab 徽标用）。M3 不可用时对应类计数为 0 并记入 `unavailable`。

**响应** `200`：
```json
{
  "namespace": "tenderhub",
  "classes": 8,
  "relations": 8,
  "counts": { "organization": 1, "qualification_certificate": 3, "...": 0 },
  "unavailable": []
}
```

### `GET /api/ontology/classes/{class_name}`

**描述**：列出某类的全部对象（M3 原始行，字段为 **snake_case**，`id` 为 RID）。

**查询参数**：`limit`（默认 100）。

**响应** `200`：
```json
[
  {
    "id": "tenderhub_qualification_certificate:qc-abc12345-1",
    "qualification_id": "qc-abc12345-1",
    "qualification_name": "ISO27001 认证",
    "certificate_no": "01234",
    "valid_until": "2028-06-01",
    "evidence_path": "libraries/company/证明材料/资质认证/1/",
    "organization_id": "org-001"
  }
]
```

**错误**：`404` 未知类名，`502` M3 错误。

### `GET /api/ontology/classes/{class_name}/{object_id}`

**描述**：获取单个对象。`object_id` 为业务主键（RID 冒号后部分）。

**响应** `200`：单个对象（snake_case）。**错误**：`404` 不存在，`502` M3 错误。

### `POST /api/ontology/classes/{class_name}`

**描述**：创建对象。`fields` 为 snake_case 属性→值，**必须含主键字段**（如 `organization_id='org-001'`）。

**请求体**：`{ "fields": { "organization_id": "org-001", "organization_name": "某公司" } }`

**响应** `200`：upsert 后的对象。**错误**：`400` 缺主键，`404` 未知类名，`502` M3 错误。

### `PUT /api/ontology/classes/{class_name}/{object_id}`

**描述**：部分更新对象（先 fetch 再合并，避免重置 M3 内置字段）。

**请求体**：`{ "fields": { "verification_status": "已核验有效" } }`

**响应** `200`：更新后的对象。**错误**：`404` 对象/类不存在，`502` M3 错误。

### `DELETE /api/ontology/classes/{class_name}/{object_id}`

**描述**：删除对象（级联删除其所有关系边）。

**响应** `200`：`{ "deleted": true, "class": "organization", "id": "org-001" }`

### `GET /api/ontology/objects/{class_name}/{object_id}/relations`

**描述**：查询对象的一跳关系。

**查询参数**：`edge`（关系名，可选）、`direction`（`out`|`in`|`both`，默认 `both`）。

**响应** `200`：
```json
{
  "class": "organization", "id": "org-001",
  "rid": "tenderhub_organization:org-001", "direction": "both",
  "edges": [ /* M3 边对象 */ ]
}
```

### `POST /api/ontology/relations/{rel_name}`

**描述**：创建一条关系边（幂等）。

**请求体**：
```json
{
  "source_class": "organization", "source_id": "org-001",
  "target_class": "qualification_certificate", "target_id": "qc-abc-1"
}
```

**响应** `200`：`{ "created": true, "edge": "holds", "source": "organization/org-001", "target": "qualification_certificate/qc-abc-1" }`

### `DELETE /api/ontology/relations/{rel_name}`

**描述**：**未实现**——M3 不支持单条边删除。返回 `501`，提示改用删除源对象（级联删边）或幂等重建边。

---

## 10. 企业材料导入（模板收集）

> 导入流程：ZIP 包（`企业材料收集表.xlsx` 8 个 sheet + 可选 `证明材料/` 目录）→ ExcelImporter 解析 → facts → `m3.sync` 写透 M3。支持**一步导入**与**预览-确认**两种模式。

### `GET /api/materials`

**描述**：从 M3 读取 8 类事实，组装本体对齐结构（facts + relationships + edges + profile）。

**响应** `200`：
```json
{
  "namespace": "/tenderhub",
  "facts": {
    "organization": { "organizationId": "org-001", "organizationName": "...", "...": "..." },
    "qualification_certificate": [ { "qualificationId": "qc-...", "qualificationName": "...", "evidencePath": "...", "...": "..." } ],
    "performance_case": [], "staff_member": [], "financial_evidence": [],
    "technical_product": [], "technical_parameter": [], "credential": []
  },
  "relationships": [ { "relationName": "holds", "fromClass": "Organization", "toClass": "QualificationCertificate", "desc": "企业持有资质认证" } ],
  "edges": [ { "relationName": "holds", "fromClass": "Organization", "fromId": "org-001", "toClass": "QualificationCertificate", "toId": "qc-..." } ],
  "profile": { /* 同 bootstrap.company */ }
}
```

### `GET /api/materials/profile`

**描述**：企业画像（聚合派生）。M3 不可用时返回空画像。

**响应** `200`：见 `bootstrap.company`（附录 B 详述）。

### `GET /api/materials/evidence-files`

**描述**：枚举某事实证据文件夹内的文件。`path` = 事实的 `evidencePath`（如 `libraries/company/证明材料/资质认证/1/`）。

**查询参数**：`path`（必填，相对 `runtime_root`）。

**响应** `200`：
```json
{
  "path": "libraries/company/证明材料/资质认证/1/",
  "files": [ { "name": "1.png", "url": "/runtime/libraries/company/证明材料/资质认证/1/1.png", "size": 102400 } ]
}
```

**错误**：`400` 非法路径（越权）。

### `GET /api/materials/export?fmt={md|json|xlsx|m3}`

**描述**：导出材料数据，四种格式：

| fmt | 格式 | 说明 |
|-----|------|------|
| `md`（默认） | Markdown | 人类可读报告 |
| `json` | JSON | 本体对齐完整 JSON |
| `xlsx` | Excel | 8 类分表 + relationship_mapping + controlled_vocabularies（需 `openpyxl`） |
| `m3` | ZIP | M3 治理包：standardized/*.csv + ontology_model.json + relationship_mapping.json |

**响应**：文件下载流（`Content-Disposition: attachment`）。前端 `MatchingPage` 通过 `window.open("/api/materials/export", "_blank")` 打开默认 md 导出。

### `POST /api/materials/import`

**描述**：**一步导入**——ZIP 解析后直接 upsert 进 M3（跳过预览）。

**请求体**：`{ "fileBase64": "...", "fileName": "企业材料收集包.zip" }`

**响应** `200`：
```json
{
  "stats": { "qualification_certificate": 3, "performance_case": 5, "evidence_paths": 2, "...": 0 },
  "warnings": [ "缺少「企业基本信息」sheet，企业主体用默认名" ],
  "m3Sync": { "objects": 10, "edges": 9, "warnings": [] },
  "profile": { /* build_profile 结果 */ }
}
```

### `POST /api/materials/import/preview`

**描述**：**预览导入（不落库）**——解析 ZIP 暂存到 `runtime/_imports/`，返回 `previewId` + 摘要。M3 不变。

**请求体**：同 `/api/materials/import`。

**响应** `200`：
```json
{
  "previewId": "ab12cd34ef56",
  "stats": { "qualification_certificate": 3, "...": 0 },
  "warnings": [],
  "summary": {
    "company": "某公司",
    "organizationType": "bidder",
    "enterpriseSize": "中型企业",
    "stats": { "...": 0 },
    "qualification_certificate": { "count": 3, "sample": ["ISO27001", "..."] },
    "performance_case": { "count": 5, "sample": ["某园区项目"] },
    "staff_member": { "count": 8, "sample": ["张三"] },
    "financial_evidence": { "count": 2, "sample": ["审计报告"] },
    "technical_product": { "count": 4, "sample": ["UINO 平台"] },
    "technical_parameter": { "count": 6, "sample": ["..."] },
    "credential": { "count": 7, "sample": ["专利：xxx"] }
  }
}
```

### `GET /api/materials/import/preview/{preview_id}`

**描述**：查看某次预览的完整解析结果。

**响应** `200`：`{ "previewId": "...", "createdAt": "...", "facts": {...}, "stats": {...}, "warnings": [] }`。**错误**：`404` 预览不存在/已过期。

### `POST /api/materials/import/confirm`

**描述**：确认导入——把预览暂存的 facts 写透 M3 并删除暂存。

**请求体**：`{ "previewId": "ab12cd34ef56" }`

**响应** `200`：`{ "stats": {...}, "warnings": [], "m3Sync": {...}, "profile": {...} }`

### `DELETE /api/materials/import/preview/{preview_id}`

**描述**：放弃预览（删除暂存，不写 M3）。

**响应** `200`：`{ "discarded": "ab12cd34ef56" }`

---

## 11. 招标监控（Monitoring）

### `GET /api/monitoring/status`

**描述**：监控状态（开关、频率、站点、统计）。

**响应** `200`：`MonitoringStatus`（见附录 B）。

### `POST /api/monitoring/run`

**描述**：手动触发一次抓取。`siteId` 为空抓全部启用站点；命中项目自动入库并推送通知。

**请求体**：`{ "siteId": "" }`

**响应** `200`：
```json
{
  "status": "抓取完成",
  "addedCount": 2, "matchedCount": 3, "dedupedCount": 1, "collectedCount": 15,
  "items": [ /* CatalogItem[]（本次新增匹配项） */ ],
  "sites": [ { "siteId": "government-procurement", "name": "中国政府采购网", "status": "运行正常", "count": 10, "matchedCount": 3, "addedCount": 2, "dedupedCount": 1 } ],
  "monitoring": { /* MonitoringStatus */ }
}
```

### `POST /api/monitoring/sites`

**描述**：创建自定义监控站点（`html` 网页列表 / `json` API 两种模式）。

**请求体**：
```json
{
  "name": "某省公共资源交易平台",
  "url": "https://xxx.cn",
  "listUrl": "https://xxx.cn/list",
  "fetchMode": "html",
  "enabled": true,
  "selectors": { "container": ".list-item", "title": ".title", "url": "a", "date": ".date", "region": ".region", "noticeType": ".type" },
  "jsonFields": { "itemsPath": "data.records", "title": "projectName", "url": "detailUrl", "date": "publishDate", "region": "regionName", "noticeType": "noticeType" }
}
```

**响应** `200`：`{ "site": MonitorSite, "monitoring": MonitoringStatus }`。**错误**：`400` 名称/URL 为空或 fetchMode 不合法。

### `PATCH /api/monitoring/sites/{site_id}`

**描述**：更新站点（启停 / 名称 / URL / fetchMode / selectors / jsonFields）。

**请求体**：`{ "enabled": false }` 或含其他可更新字段。

**响应** `200`：`{ "site": MonitorSite, "monitoring": MonitoringStatus }`

### `DELETE /api/monitoring/sites/{site_id}`

**描述**：删除自定义站点（仅 `custom: true`；3 个内置站点不可删）。

**响应** `200`：`{ "monitoring": MonitoringStatus }`。**错误**：`404`。

### `POST /api/monitoring/sites/{site_id}/test`

**描述**：测试站点连接与抓取（不入库，返回前 10 条）。

**响应** `200`：
```json
{ "success": true, "status": "试抓成功", "count": 5, "items": [ ... ] }
```
失败：`{ "success": false, "status": "连接失败", "detail": "...", "count": 0, "items": [] }`

---

## 12. 消息提醒 / 企业微信

### `POST /api/notifications/projects/{project_id}/toggle`

**描述**：切换项目提醒标记（已提醒 ↔ 未提醒）。

**响应** `200`：`{ "item": CatalogItem, "reminders": [...] }`

### `POST /api/notifications/test/in-app`

**描述**：测试页面内弹窗提醒。

**响应** `200`：`{ "title": "页面提醒测试", "message": "这是标智通的页面提醒测试消息。" }`

### `POST /api/wecom/test`

**描述**：向企业微信机器人 Webhook 发送测试消息。

**响应** `200`：
- 成功：`{ "sent": true, "status": "发送成功", "detail": "..." }`
- 失败：`{ "sent": false, "status": "未配置 Webhook|Webhook 格式错误|发送失败", "detail": "..." }`

---

## 13. 系统配置（Config）

### `GET /api/config`

**描述**：当前配置（敏感字段脱敏为 `"***"`）。

**响应** `200`：`{ "monitoring": {...}, "notifications": {...}, "wecom": {...}, "ai": {...} }`

### `POST /api/config`

**描述**：保存配置（支持部分更新，仅传需变更字段）。`monitoring`/`notifications`/`wecom`/`ai` 按段合并。

**请求体**：
```json
{ "config": { "monitoring": { "includeKeywords": ["数字孪生"] }, "ai": { "model": "gpt-4o" } } }
```

**响应** `200`：`{ "config": { /* 更新后完整配置（脱敏） */ } }`

### `POST /api/config/ai/test`

**描述**：测试大模型连接。`apiKey` 传 `"***"` 时复用已存密钥。

**请求体**：`{ "config": { "ai": { "baseUrl": "...", "apiKey": "...", "model": "..." } } }`

**响应** `200`：`{ "success": true, "model": "gpt-4o", "detail": "连接成功：模型正常响应。" }`

---

## 14. 自动化任务 / 提醒 / 运行时文件

### `GET /api/automation/status`

**响应** `200`：`{ "running": false, "totalRuns": 5, "latest": { "status": "完成", "startedAt": "...", "monitoring": {...} } }`

### `POST /api/automation/run`

**描述**：触发一次完整自动化（抓取 + 匹配入库 + 通知）。

**响应** `200`：`{ "run": { "status": "抓取完成", "startedAt": "...", "monitoring": {...} } }`

### `GET /api/reminders`

**响应** `200`：最近 100 条提醒：`[ { "id": "...", "projectId": "...", "label": "", "remindAt": "", "createdAt": "...", "status": "待处理" } ]`

### `POST /api/reminders`

**请求体**：`{ "projectId": "...", "label": "投标截止前3天", "remindAt": "2026-09-12T09:00:00+08:00" }`

**响应** `200`：`{ "reminder": {...}, "reminders": [...] }`

### `GET /runtime/{file_path}`

**描述**：访问运行时文件（下载的招标原文、生成的响应文件、上传资料、证明材料等）。

**示例**：`/runtime/projects/{id}/outputs/项目名称.docx`、`/runtime/libraries/company/证明材料/资质认证/1/1.png`

**错误**：`404` 文件不存在或路径越权。

---

## 附录 A：前端 API 封装对照

前端 `src/services/api.ts` 方法名 → HTTP 方法/路径：

| 前端方法 | 方法 | 路径 |
|---------|------|------|
| `bootstrap()` | GET | `/api/bootstrap` |
| `search(payload)` | POST | `/api/search/realtime` |
| `importDocument(payload)` | POST | `/api/import` |
| `analyze(projectId)` | POST | `/api/analyze` |
| `markRead(projectId)` | POST | `/api/catalog/{id}/read` |
| `setFollowUp(projectId, followUp, status)` | POST | `/api/catalog/{id}/follow-up` |
| `downloadProject(projectId)` | POST | `/api/catalog/{id}/download` |
| `generateResponse(projectId)` | POST | `/api/tenders/{id}/generate-response` |
| `saveDraft(workspaceId, draft)` | POST | `/api/workspaces/{id}/draft` |
| `getOntologyMeta()` | GET | `/api/ontology/meta` |
| `getOntologySummary()` | GET | `/api/ontology/summary` |
| `getFacts(className, limit)` | GET | `/api/ontology/classes/{class}` |
| `createFact(className, fields)` | POST | `/api/ontology/classes/{class}` |
| `updateFact(className, id, fields)` | PUT | `/api/ontology/classes/{class}/{id}` |
| `deleteFact(className, id)` | DELETE | `/api/ontology/classes/{class}/{id}` |
| `getEvidenceFiles(path)` | GET | `/api/materials/evidence-files` |
| `importMaterials({fileBase64, fileName})` | POST | `/api/materials/import` |
| `getMaterialProfile()` | GET | `/api/materials/profile` |
| `saveConfig(config)` | POST | `/api/config` |
| `testAiConnection(ai)` | POST | `/api/config/ai/test` |
| `monitoringStatus()` | GET | `/api/monitoring/status` |
| `runMonitoring(siteId)` | POST | `/api/monitoring/run` |
| `updateMonitorSite(siteId, enabled)` | PATCH | `/api/monitoring/sites/{id}` |
| `createMonitorSite(payload)` | POST | `/api/monitoring/sites` |
| `updateMonitorSiteConfig(siteId, payload)` | PATCH | `/api/monitoring/sites/{id}` |
| `deleteMonitorSite(siteId)` | DELETE | `/api/monitoring/sites/{id}` |
| `testMonitorSite(siteId)` | POST | `/api/monitoring/sites/{id}/test` |
| `toggleReminder(projectId)` | POST | `/api/notifications/projects/{id}/toggle` |
| `testInAppNotification()` | POST | `/api/notifications/test/in-app` |
| `testWecom()` | POST | `/api/wecom/test` |
| `runAutomation()` | POST | `/api/automation/run` |

> 未封装进 `api.ts` 的直接调用：`MatchingPage.vue` 用 `window.open("/api/materials/export")` 触发导出。

---

## 附录 B：关键数据类型速查

定义文件：`frontend/src/shared/types.ts`（标注 `*` 表示前端类型尚未与后端最新返回结构完全对齐，以本文档后端章节为准）。

| 类型 | 说明 | 关键字段 |
|------|------|---------|
| `CatalogItem` | 招标项目 | `id/title/category/region/noticeType/publishedDate/source/sourceUrl/bidDeadline/status/score/relevanceScore/matched*/matchReasons/excludeReason/nextStage/shouldAnalyze/analysisText/workspaceId/followUp/read/reminderStatus/notificationChannels/downloadStatus/downloadUrl` |
| `MonitorSite` | 监控站点 | `id/name/url/enabled/status/latest/next/listUrl/fetchMode/custom/selectors/jsonFields` |
| `MonitoringStatus` | 监控状态 | `enabled/frequencyMinutes/sites[]/stats{latestCount,latestMatchedCount,totalCollected,lastFetchedAt,nextFetchedAt}` |
| `ReasoningResult` | 评估推理结果 | `rows[]/risks[]/score/decision/relevance/eligibility/fineReview` |
| `Workspace` | 工作台 | `id/projectId/title/project/reasoning/draft/draftVersion` |
| `LibrarySummary` | 资料库摘要 | `key/name/description/documentCount/documents[]` |
| `OntologyMeta` | 本体元信息 | `classes[]/relations[]`（`OntologyClassDef`：`name/cnName/description/attrs[]`；`OntologyAttrDef`：`name/cn/type/isPk`） |
| `OntologySummary` | 本体计数 | `namespace/classes/relations/counts{}/unavailable[]` |
| `MaterialFact` | 本体事实 | `id`（RID）+ snake_case 属性 |
| `EvidenceFile` / `EvidenceFilesResponse` | 证据文件 | `{name,url,size}` / `{path,files[]}` |
| `ImportResult` | 导入结果 | `stats{}` / `warnings[]` / `m3Sync{objects,edges,warnings}` / `profile{}` |
| `BootstrapResponse` | 启动引导 | `company/ontology/materialsCounts/config/monitoring/catalog/libraries/reminders/automation/workspaces` |

**画像结构（`/api/materials/profile`、`bootstrap.company`）**：`company`、`enterpriseSize`、`employeeCount`、`annualRevenueWan`、`totalAssetsWan`、`description`、`capabilities[]`、`service_regions[]`、`service_types[]`、`service_industries[]`、`qualifications[]`、`certificates[]`、`performances[]`、`personnel[]`、`financial_evidences[]`、`products[]`、`parameters[]`、`credentials[]`、`evidence_paths[]`

> `*MaterialProfile` 前端接口目前仍是旧字段集（`company/qualifications/certificates/performances/personnel/service_regions/capabilities`），后端 `build_profile` 已扩展，建议前端同步更新该类型。

---

## 附录 C：企业材料本体 8 类 + 8 关系

| snake_case 类名 | 中文 | 主键 | 说明 |
|----------------|------|------|------|
| `organization` | 企业 | `organizationId` | 投标企业主体（含规模 + 能力范围属性） |
| `qualification_certificate` | 资质认证 | `qualificationId` | 资质认证证书 |
| `performance_case` | 类似业绩 | `performanceId` | 类似项目业绩 |
| `staff_member` | 项目人员 | `staffId` | 项目人员 |
| `financial_evidence` | 财务证明 | `financialEvidenceId` | 财务证明 |
| `technical_product` | 技术产品 | `productId` | 技术产品 |
| `technical_parameter` | 技术参数 | `parameterId` | 产品参数指标 |
| `credential` | 证照凭证 | `credentialId` | 认证/检测报告/知识产权 |

| 关系名 | 方向 | 说明 |
|--------|------|------|
| `holds` | Organization → QualificationCertificate | 持有资质认证 |
| `completes` | Organization → PerformanceCase | 完成类似业绩 |
| `employs` | Organization → StaffMember | 聘用人员 |
| `provides_financial_evidence` | Organization → FinancialEvidence | 提供财务证明 |
| `owns_product` | Organization → TechnicalProduct | 拥有技术产品 |
| `has_parameter` | TechnicalProduct → TechnicalParameter | 产品含参数 |
| `owns_credential` | Organization → Credential | 持有证照凭证 |
| `certified_by` | TechnicalProduct → Credential | 产品获产品级认证 |

> 完整字段定义见 `app/document_recognition/ontology_defs.py`（唯一来源）。

---

## 附录 D：已移除 / 变更的接口（历史）

> 供排查旧代码/旧文档引用，**以下接口在当前版本已不存在**：

| 旧接口 | 去向 |
|--------|------|
| `GET /api/company/profile` | 已移除，改由 `bootstrap.company` / `/api/materials/profile` 提供（M3 画像） |
| `GET /api/company/knowledge` | 已移除，企业知识数据迁移至 M3 本体（`/api/ontology/*`） |
| `GET /api/company/knowledge/objects` | 已移除 |
| `GET /api/company/knowledge/objects/{type}/{id}` | 已移除 |
| `POST /api/materials/upload` | 由 `/api/materials/import`（ZIP 模板包）替代 |
| `DELETE /api/materials/{material_id}` | 由 `/api/ontology/classes/{class}/{id}` 的 DELETE 替代 |
| `POST /api/materials/summarize-capabilities` | **待开发（代办）**：LLM 根据企业资料生成企业画像/能力画像 + 前端触发按钮，见文末「待办事项」 |
| `bootstrap` 的 `companyMaterialIndex` / `companyKnowledge` 字段 | 由 `materialsCounts` + `company`（M3 画像）替代 |

> 本体由 **V0.3（12 类 19 关系）→ V0.4（8 类 8 关系）**：删除 `EnterpriseProfile/EnterpriseCapability/MaterialCategory/EnterpriseMaterial/ProductCertification/IntellectualProperty`，新增 `TechnicalParameter/Credential`，能力范围属性内联到 `Organization`。

---

---

## 待办事项（TODO）

> 记录尚未实现、待前后端共同落地的接口与功能。实现后请从本表移除，并把接口补进正文对应章节。

| 编号 | 待办内容 | 涉及前端 | 涉及后端 | 说明 |
|------|---------|---------|---------|------|
| TODO-1 | **企业画像 / 能力画像（LLM 生成）** | 企业材料页增加「生成能力画像」按钮 | 恢复 `POST /api/materials/summarize-capabilities` | 根据 M3 中的企业资料（资质/业绩/产品/材料摘要）调用 LLM 归纳 `summary`、`capabilities`、`serviceTypes`、`serviceRegions`，写回 `Organization`/画像；未配置大模型时返回 `400` 提示 |

---

> **最后更新**：2026-09（对齐 tender-agent v2.0.0 / 本体 tenderhub V0.4）
