# 招投标智能体项目文件清单

## 1. 文档目的

本文档用于帮助新成员快速理解当前 Demo 的目录结构、文件职责、依赖关系和修改边界。

当前仓库是一个可运行的前后端 Demo：

- 前端使用 Vue 3、TypeScript、Vite、Element Plus。
- 后端使用 Python、FastAPI。
- 当前数据主要使用 JSON 文件和 `runtime/` 本地运行目录保存。
- 当前匹配结果用于辅助业务员判断，不构成正式投标资格结论。

## 2. 当前目录结构

```text
tender_agent/
├─ .env.example
├─ .gitignore
├─ README.md
├─ agent-config.json               # 本机配置（M3、AI、监控、通知，不提交 Git）
├─ docs/                           # 开发、本体、接口文档
│  ├─ PROJECT_STATUS.md
│  ├─ DEVELOPMENT_GUIDE.md
│  ├─ PROJECT_FILE_CATALOG.md
│  ├─ API_INTERFACES.md
│  ├─ 上传资料接口.md
│  ├─ 解析与匹配模块边界.md
│  ├─ 招标筛选与抓取流程.md
│  ├─ 开发计划与待办.md
│  ├─ UI设计规范.md
│  ├─ archive/                     # 历史决策文档归档
│  └─ ontology/                    # 本体文档
├─ backend/
│  ├─ requirements.txt
│  ├─ run.py                       # 启动入口（127.0.0.1:8793）
│  ├─ build_ontology.py
│  └─ app/
│     ├─ __init__.py
│     ├─ main.py
│     ├─ store.py
│     ├─ api/
│     │  ├─ __init__.py
│     │  ├─ config.py
│     │  ├─ materials.py
│     │  ├─ monitoring.py
│     │  ├─ notifications.py
│     │  ├─ ontology.py
│     │  └─ wecom.py
│     ├─ config/
│     │  ├─ __init__.py
│     │  └─ service.py
│     ├─ document_recognition/
│     │  ├─ ontology_defs.py
│     │  └─ storage.py
│     ├─ importers/
│     │  └─ excel_importer.py
│     ├─ m3/
│     │  ├─ client.py
│     │  └─ sync.py
│     ├─ notification/
│     │  ├─ __init__.py
│     │  └─ service.py
│     ├─ tender_matching/
│     │  ├─ __init__.py
│     │  ├─ agent_bridge.py        # 智能体提取桥（可插拔）
│     │  ├─ contracts.py
│     │  ├─ document_fine_matching.py  # 提取层
│     │  ├─ document_matching.py       # 匹配层（M3 事实路由）
│     │  ├─ document_parser.py         # 解析层
│     │  ├─ document_structure.py
│     │  ├─ eligibility.py             # 公告资格粗匹配
│     │  ├─ requirement_scope.py
│     │  └─ service.py
│     ├─ tender_monitor/
│     │  ├─ __init__.py
│     │  ├─ adapters.py
│     │  ├─ custom_site.py
│     │  ├─ scheduler.py
│     │  └─ service.py
│     └─ wecom/
│        ├─ __init__.py
│        └─ service.py
├─ frontend/
│  ├─ index.html
│  ├─ package.json
│  ├─ package-lock.json
│  ├─ tsconfig.json
│  ├─ tsconfig.node.json
│  ├─ vite.config.ts
│  └─ src/
│     ├─ App.vue
│     ├─ main.ts
│     ├─ env.d.ts
│     ├─ styles.css
│     ├─ app/
│     │  ├─ AppShell.vue
│     │  ├─ app-state.ts
│     │  └─ navigation.ts
│     ├─ components/
│     │  ├─ MetricOverview.vue
│     │  └─ ModuleNav.vue
│     ├─ features/
│     │  ├─ automation/
│     │  │  └─ AutomationPage.vue
│     │  ├─ bid-workspace/
│     │  │  ├─ BidGeneratorPage.vue
│     │  │  └─ BidWorkspacePage.vue
│     │  ├─ document-library/
│     │  │  └─ MaterialsPage.vue
│     │  ├─ tender-matching/
│     │  │  └─ MatchingPage.vue
│     │  └─ tender-monitor/
│     │     ├─ FollowUpPage.vue
│     │     ├─ NoticeReviewDrawer.vue
│     │     └─ TenderMonitorPage.vue
│     ├─ services/
│     │  └─ api.ts
│     └─ shared/
│        ├─ file-utils.ts
│        └─ types.ts
├─ templates/                      # 企业材料收集表模板 + 证明材料
├─ scripts/                        # 辅助脚本（模板生成、上传验证）
├─ preview/                        # UI 主题预览生成
├─ demo-data/                      # 脱敏演示数据（不提交 Git）
└─ runtime/                        # 本地运行时生成（不提交 Git）
   ├─ catalog.json
   ├─ libraries.json
   ├─ workspaces.json
   ├─ reminders.json
   ├─ automation-runs.json
   ├─ libraries/company/证明材料/   # 企业材料证据文件（事实在 M3，本地只留证据文件）
   └─ projects/
```

`runtime/` 目录由后端首次启动时创建，实际文件可能随运行过程增加。它不应提交到 Git。

## 3. 根目录文件

| 文件 | 功能 | 修改说明 |
| --- | --- | --- |
| `.env.example` | AI 接口环境变量示例 | 可以补充非敏感变量；禁止提交真实密钥 |
| `.gitignore` | 忽略依赖、运行数据、密钥和构建产物 | 新增本地生成目录时同步维护 |
| `README.md` | 项目简介、启动方式和 Demo 状态 | 面向所有开发成员，保持简明 |
| `UI设计规范.md` | 前端界面、交互和视觉约束 | 前端改版前先阅读 |
| `模块协作说明.md` | 当前前端模块职责和协作边界 | 团队分工调整时更新 |

> 说明：企业材料单一来源为 **M3 图库 `/tenderhub`**（由《企业材料收集表.xlsx》模板导入 upsert 产出）。本地不再落 `company-materials.json`，只保留证明材料文件（`runtime/libraries/company/证明材料/`）。旧的 `company-profile.json`、`company-knowledge.json`、`company-material-index.json`、`ontology-governance.json` 已删除，不再作为现状列出。

### 3.1 演示数据

| 文件 | 功能 | 说明 |
| --- | --- | --- |
| `demo-data/README.md` | 说明演示数据来源、用途和限制 | 明确数据不能直接作为真实投标依据 |
| `demo-data/tender-catalog.json` | 预置招标项目目录 | 用于初始项目列表和监控适配器演示 |

### 3.2 不应再作为运行入口的旧版原型

以下文件属于早期 Node 单页原型，目前已从工作区删除，历史提交中仍可追溯：

- `index.html`
- `server.js`
- `demo-core.js`
- `docx-export.js`
- `pi-tender-agent.mjs`
- `validate-ontology.js`
- `sample-tender.txt`
- `package.json`
- `package-lock.json`
- `ONTOLOGY_GOVERNANCE_ALIGNMENT.md`

新的运行入口统一为 `backend/` 和 `frontend/`。不要基于旧版文件继续开发新功能。

## 4. 后端文件

### 4.1 启动和应用入口

| 文件 | 功能 | 关键内容 |
| --- | --- | --- |
| `backend/run.py` | 本地启动辅助入口 | 用于以 Python 方式启动 FastAPI |
| `backend/app/main.py` | FastAPI 应用入口和路由注册 | 创建 `Store`、注册生命周期任务、挂载 config/materials/monitoring/notifications/ontology/wecom 6 个路由 + catalog/search/import/analyze/ask/libraries 等内部接口 |
| `backend/app/__init__.py` | Python 包标识 | 不承载业务逻辑 |
| `backend/requirements.txt` | Python 依赖 | FastAPI、Uvicorn、DOCX、PDF 等依赖 |
| `backend/build_data.py` | 构建脚本 | JSON → cleaned_data CSV |
| `backend/build_ontology.py` | 构建脚本 | ontology_defs → ontology_model.json |

`main.py` 挂载的路由与通用接口包括：

- 健康检查和初始化数据：`/api/health`、`/api/bootstrap`
- 项目检索和项目动作：`/api/catalog`、`/api/search/realtime`、项目已读、跟进、快照下载
- 匹配和工作台：`/api/tenders/match`、`/api/analyze`、工作台草稿和响应文件生成
- 文件导入：`/api/import`
- 企业材料：`/api/materials/import`、`/api/materials/import/preview`、`/api/materials/import/confirm`、`/api/materials/profile`、`/api/materials/export`、`/api/materials/evidence-files`
- M3 本体 CRUD：`/api/ontology/*`
- 自动化和提醒：`/api/automation/*`、`/api/reminders`
- AI 问答：`/api/ask`、`/api/agent/chat`

### 4.2 数据存储和总业务门面

| 文件 | 功能 | 当前状态 |
| --- | --- | --- |
| `backend/app/store.py` | 本地 JSON 存储（catalog/workspace/libraries/reminders/automation）、文件落盘、文档提取、项目解析、匹配、工作台和问答的总业务门面，企业材料则写透 M3 图库 | Demo 逻辑集中，后续应拆分为领域服务和持久化层 |

`Store` 当前管理：

- `catalog.json`：项目目录和项目状态
- `libraries.json`：资料库索引和文档元数据
- `workspaces.json`：匹配工作台和响应草稿
- `reminders.json`：提醒记录
- `automation-runs.json`：自动化执行记录
- 企业材料事实存于 **M3 图库 `/tenderhub`**（本地不再落 `company-materials.json`）
- `runtime/projects/`：项目源文件和输出文件
- `runtime/libraries/company/证明材料/`：企业材料证据文件

注意：当前没有真正的数据库、多租户、用户身份和权限控制。

### 4.3 API 模块

| 文件 | 路由前缀 | 功能 |
| --- | --- | --- |
| `backend/app/api/config.py` | `/api/config` | 读取和保存搜索、评分、AI、监控、通知等配置 |
| `backend/app/api/materials.py` | `/api/materials` | 企业材料 API（导入 / 预览-确认 / 读取 / 证据文件 / 导出） |
| `backend/app/api/monitoring.py` | `/api/monitoring` | 查询监控状态、运行抓取、管理自定义网站、测试自定义网站 |
| `backend/app/api/notifications.py` | `/api/notifications` | 项目提醒开关和站内通知测试 |
| `backend/app/api/ontology.py` | `/api/ontology` | M3 本体 CRUD |
| `backend/app/api/wecom.py` | `/api/wecom` | 企业微信 Webhook 测试 |
| 各目录下的 `__init__.py` | 无 | Python 包标识，不增加业务行为 |

API 路由层只负责参数接收、调用服务和转换 HTTP 错误。复杂业务不应继续堆到路由函数中。

### 4.4 企业材料与本体领域

| 文件 | 功能 |
| --- | --- |
| `backend/app/document_recognition/ontology_defs.py` | 本体定义单一来源（8 类 + 8 条关系） |
| `backend/app/document_recognition/storage.py` | 本体契约（relationships/build_edges/build_profile） |
| `backend/app/importers/excel_importer.py` | 模板导入器（ZIP → facts） |
| `backend/app/m3/client.py` | M3 客户端（Cookie 登录 + MQL） |

> 说明：企业材料已从「上传 PDF/DOCX + OCR + 字段抽取」改为「工作人员填《企业材料收集表.xlsx》模板 → 打包 ZIP → 上传导入」。旧的 `document_recognition/` 下 parser/extraction_engine/normalizer/patterns/pipeline/schema/modules/module_router/llm 等识别代码已归档到 `_archive/document_recognition_v1/`。

### 4.5 配置领域

| 文件 | 功能 |
| --- | --- |
| `backend/app/config/service.py` | 默认配置合并、监控网站默认值、自定义网站增删改、敏感字段脱敏 |

当前配置主要包括：

- 招标业务搜索画像
- 目标服务类型和地区
- 包含及排除关键词
- 评分权重和建议阈值
- AI 接口
- 自动化开关
- 监控网站及抓取规则
- 站内提醒和企业微信

### 4.6 招标项目监控领域

| 文件 | 功能 |
| --- | --- |
| `backend/app/tender_monitor/service.py` | 监控执行、候选公告获取、关键词筛选、去重、统计、保存和新项目通知 |
| `backend/app/tender_monitor/custom_site.py` | 自定义 HTML/JSON 网站抓取和字段标准化 |
| `backend/app/tender_monitor/scheduler.py` | 每 30 秒检查一次是否到达监控频率，到期后调用监控服务 |

当前预置平台使用 Demo 数据或本地种子数据；自定义网站只支持简单的公开 HTTP HTML/JSON 抓取。

当前不支持：

- 自动登录
- 验证码处理或绕过
- 付费文件自动获取
- 复杂 JavaScript 页面渲染
- 真实平台的长期稳定适配保证

### 4.7 匹配领域

| 文件 | 功能 | 当前状态 |
| --- | --- | --- |
| `backend/app/tender_matching/service.py` | 监控画像和手动搜索的项目过滤 | 已实现基础关键词、地区、服务类型过滤 |
| `Store.match_project()` | 公告/招标文件与企业材料的演示匹配和评分 | 当前集中在 `store.py`，后续需要拆分为三层匹配服务 |

当前能够演示：

- 资质是否在企业材料中出现
- 类似业绩数量是否达到公告识别出的数量
- 项目经理或项目人员是否存在
- 服务地点是否属于公司服务区域
- 基于结果生成演示分数和建议

当前还没有完整实现：

- 第一层项目相关性评分
- 第二层公告资格硬性门槛判定
- 第三层招标文件条款级详细匹配
- 证据页码和原文引用
- 资料有效期核验
- 条款冲突和缺失附件的系统化判断

### 4.8 通知领域

| 文件 | 功能 |
| --- | --- |
| `backend/app/notification/service.py` | 新项目通知、项目提醒状态和站内通知测试 |
| `backend/app/wecom/service.py` | 企业微信 Webhook 消息发送和测试 |

通知不能替代业务员审核。正式版本需要记录通知对象、发送结果、失败原因和重试状态。

### 4.9 M3_workspace 脚本

| 文件 | 功能 |
| --- | --- |
| `M3_workspace/generate_sql.py` | CSV → DDL+DML SQL |
| `M3_workspace/import_tenderhub.py` | 导入在线 M3 |
| `M3_workspace/drop_tenderhub.py` | 清空 M3 命名空间 |
| `M3_workspace/query_m3.py` | 查询 M3 |
| `M3_workspace/verify_tenderhub.py` | 验证 M3 |
| `M3_workspace/regenerate_ontology_outputs.py` | defs → ontology_model.json + JSONRule.json |

## 5. 前端文件

### 5.1 应用入口和状态

| 文件 | 功能 |
| --- | --- |
| `frontend/src/main.ts` | Vue 应用启动入口，引入 Element Plus 和全局样式 |
| `frontend/src/App.vue` | 页面路由式切换、页面组件组合和事件连接 |
| `frontend/src/app/AppShell.vue` | 页面外壳、侧边导航、页头、统计区域和内容容器 |
| `frontend/src/app/app-state.ts` | 全局响应式状态、接口调用和页面动作编排 |
| `frontend/src/app/navigation.ts` | 导航项、页面 Tab 类型和统计项类型 |
| `frontend/src/styles.css` | 全局布局、颜色、表格、表单和页面样式 |
| `frontend/src/env.d.ts` | Vite/TypeScript 环境类型声明 |

当前状态管理集中在 `app-state.ts`，适合 Demo；当用户、公司、资料库、项目和任务数量增加后，建议按领域拆分 store。

### 5.2 公共组件

| 文件 | 功能 |
| --- | --- |
| `frontend/src/components/MetricOverview.vue` | 抓取统计指标展示 |
| `frontend/src/components/ModuleNav.vue` | 模块导航展示 |
| `frontend/src/components/ProjectList.vue` | 招标项目列表和项目操作展示 |

### 5.3 招标项目和监控

| 文件 | 功能 |
| --- | --- |
| `frontend/src/features/tender-monitor/TenderMonitorPage.vue` | 项目搜索、项目列表、实时抓取、提醒、跟进 |
| `frontend/src/features/tender-monitor/FollowUpPage.vue` | 已跟进项目列表 |
| `frontend/src/features/tender-monitor/TenderImportPage.vue` | 本地招标文件导入页面，当前未接入 `App.vue` 主流程，属于待清理/待复用组件 |

### 5.4 匹配和响应文件

| 文件 | 功能 |
| --- | --- |
| `frontend/src/features/tender-matching/MatchingPage.vue` | 上传招标文件、选择资料库、查看匹配结果 |
| `frontend/src/features/bid-workspace/BidWorkspacePage.vue` | 查看项目分析结果、选择资料库、进入响应工作台 |
| `frontend/src/features/bid-workspace/BidGeneratorPage.vue` | 编辑、保存、生成和下载响应文件草稿 |

当前页面上的评分和结论来自后端 Demo 匹配逻辑，不应在前端自行重新计算。

### 5.5 企业材料

| 文件 | 功能 | 当前状态 |
| --- | --- | --- |
| `frontend/src/features/document-library/LibraryDirectoryPage.vue` | 资料库目录和资料库切换 |
| `frontend/src/features/document-library/LibraryDocumentsPage.vue` | 资料列表、上传入口、搜索和展示 |
| `frontend/src/features/document-library/LibraryPage.vue` | 另一份资料库页面实现，目前与 `LibraryDocumentsPage.vue` 有重复，尚未接入主流程 |

> 说明：企业材料前端当前围绕「模板收集 / 导入」展开（填写《企业材料收集表.xlsx》→ 打包 ZIP → 上传导入 → 直接 upsert 进 M3 → 从 M3 展示/编辑），不再需要 OCR、自动分类和字段抽取工作流。

### 5.6 自动化配置

| 文件 | 功能 |
| --- | --- |
| `frontend/src/features/automation/AutomationPage.vue` | 搜索画像、网站监控、定时任务、通知和 AI 配置 |

### 5.7 前端接口和类型

| 文件 | 功能 |
| --- | --- |
| `frontend/src/services/api.ts` | 封装前端访问后端 API 的请求函数 |
| `frontend/src/shared/types.ts` | 项目、监控、匹配、工作台和资料库 TypeScript 类型 |
| `frontend/src/shared/file-utils.ts` | 浏览器文件转 Base64，供上传 API 使用 |

新增后端字段时，应同步更新 `types.ts` 和相关 API 返回类型。

## 6. 依赖关系和主数据流

### 6.1 页面启动

```text
frontend/src/main.ts
  -> App.vue
  -> useAppState()
  -> GET /api/bootstrap
  -> Store.bootstrap()
  -> 从 M3 图库刷新企业画像与计数，并读取 demo-data 和 runtime
  -> 返回项目、资料库、监控、配置、工作台和提醒
```

### 6.2 公开招标项目监控

```text
定时器或前端手动运行
  -> POST /api/monitoring/run
  -> tender_monitor.service.run_monitoring()
  -> 获取预置种子/Fixture 或自定义网站 HTML/JSON
  -> matches_monitoring_profile()
  -> 按 sourceUrl 或标题+日期去重
  -> 保存 runtime/catalog.json
  -> notify_new_projects()
  -> 返回新增项目和监控统计
```

### 6.3 招标文件导入和分析

```text
前端上传 TXT/PDF/DOCX
  -> POST /api/import
  -> Store.extract_text_from_file()
  -> Store.parse_tender()
  -> 保存源文件和解析结果
  -> 项目加入 catalog
  -> POST /api/analyze
  -> Store.match_project()
  -> 创建 workspace
  -> 返回匹配行、风险、分数和建议
```

## 7. 修改边界

### 可以直接修改

- `frontend/src/features/` 下对应业务模块
- `frontend/src/components/` 下公共展示组件
- `frontend/src/services/api.ts` 和 `frontend/src/shared/types.ts`
- `backend/app/api/` 下接口适配
- `backend/app/tender_monitor/`、`backend/app/tender_matching/` 下对应领域服务
- `docs/` 下项目文档
- `demo-data/` 下脱敏演示数据

### 修改前需要同步评审

- `backend/app/store.py`：当前是所有业务的共享核心，改动容易影响多个流程
- `backend/app/main.py`：影响 API 路由和应用生命周期
- `backend/app/document_recognition/ontology_defs.py`：本体定义单一来源（8 类 + 8 关系），会改变领域建模约束
- M3 图库 `/tenderhub`：企业材料单一事实源，会改变匹配演示结果
- `.gitignore`：可能影响数据泄露或提交内容

### 禁止提交

- `agent-config.json`
- `.env`
- 任何 API Key、Webhook、账号密码和 Cookie
- `runtime/`
- `frontend/node_modules/`
- `backend/.venv/`
- 真实企业敏感资料、真实招标文件和未脱敏响应文件

## 8. 当前已知技术债

1. `Store` 承担了存储、解析、匹配、生成和问答多个职责，需要逐步拆分。
2. 资料库当前只有文件元数据和预览文本，没有文档版本、来源、有效期、分类和证据页码。
3. PDF 只支持普通文本提取，扫描 PDF 和图片需要 OCR（招标文件侧）。
4. DOCX 提取当前只读取段落文本，表格、页眉页脚、批注和图片文字尚未处理（招标文件侧）。
5. Excel 和图片格式（招标文件侧）还没有完整的后端解析链路。
6. `LibraryPage.vue` 与 `LibraryDocumentsPage.vue` 重复，需要后续确定唯一实现。
7. 当前监控平台适配器主要用于 Demo，真实平台需要单独适配和回归测试。
8. 当前没有数据库、多公司隔离、用户认证、权限和审计日志。
9. “下载项目”当前实际保存公告文本快照，不是真实招标文件自动下载。
10. 当前评分是演示评分，不能作为法律、商务或投标承诺。

> 注：企业材料部分已改为模板收集/导入（不再需要 OCR/字段抽取），上述「OCR/解析」技术债仅针对招标公告/招标文件侧。
