# 招投标智能体

面向企业业务员和投标团队的招标项目发现、企业材料匹配与响应文件辅助工作台。系统只提供**辅助判断**，不替代业务员、法务、财务或项目负责人对原始公告和招标文件的最终核验。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 前端 | Vue 3（Composition API）+ TypeScript + Vite + Element Plus + lucide-vue-next |
| 后端 | Python + FastAPI |
| 数据 | **M3 图数据库直连**（企业材料单一事实源）+ `runtime/` 本地运行数据 |
| 本体 | `tenderhub` 命名空间，V0.4（8 类对象 + 8 关系，证据文件夹化） |

## 核心功能

- **招标监控**：公开网站 / 自定义 HTML / JSON 源抓取，定时监控、去重、已读、跟进，站内与企业微信提醒。
- **跟进项目**：集中查看已跟进项目；点「公告预审」在右侧抽屉直接展示预审结果（资格匹配分、硬性否决项、项目信息、采购需求、资格筛查表、风险），不跳转页面；跟进时自动后台预审。
- **匹配评估**：公告资格粗匹配 + 招标文件细匹配，输出评分、证据、缺失项、风险与投标建议。
- **标书工作台**：查看分析结果、维护响应文件底稿，导出 Markdown / JSON / DOCX。
- **企业材料（M3）**：8 类事实浏览、模板 ZIP 批量导入（写透 M3）、逐条增删改、证据文件夹查看。
- **自动化与配置**：监控源管理、搜索画像、AI 连接测试、企业微信 Webhook 测试、智能体问答入口。

## 目录结构

```text
tender_agent/
├─ frontend/            # Vue 3 工作台
│  ├─ src/
│  │  ├─ app/           # 应用壳层、导航、全局状态
│  │  ├─ components/    # 跨页面复用组件
│  │  ├─ features/      # 业务页：tender-monitor / tender-matching / bid-workspace / document-library / automation
│  │  ├─ services/      # 接口访问（api.ts）
│  │  └─ shared/        # 共享类型与工具
│  └─ dist/             # 构建产物（不提交 Git）
├─ backend/             # FastAPI 后端
│  ├─ app/
│  │  ├─ main.py        # 应用入口 + 静态托管 frontend/dist
│  │  ├─ store.py       # 业务门面（状态读取、落盘、解析、匹配、工作台编排）
│  │  ├─ api/           # HTTP 路由：config / materials / monitoring / notifications / ontology / wecom
│  │  ├─ config/        # 配置服务
│  │  ├─ document_recognition/  # 本体定义 + 词典
│  │  ├─ importers/     # Excel 模板导入
│  │  ├─ m3/            # M3 图库客户端与写透
│  │  ├─ notification/  # 站内 / 企业微信通知
│  │  ├─ tender_matching/  # 解析层 + 提取层 + 匹配层（agent_bridge / document_parser / document_fine_matching / document_matching / eligibility …）
│  │  ├─ tender_monitor/   # 招标监控（adapters / custom_site / scheduler / service）
│  │  └─ wecom/         # 企业微信
│  ├─ run.py            # 启动入口（127.0.0.1:8793）
│  └─ requirements.txt
├─ runtime/             # 运行时数据：catalog.json、workspaces.json、libraries/、projects/ 等（不提交 Git）
├─ docs/                # 开发文档、本体文档、API 清单
├─ templates/           # 企业材料收集表模板 + 填写指南
├─ demo-data/           # 脱敏演示招标项目数据（不提交 Git）
├─ preview/             # UI 主题预览生成脚本与图片
├─ scripts/             # 辅助脚本（模板生成、上传验证）
├─ agent-config.json    # 本机配置：M3 连接、AI、监控、通知（不提交 Git）
└─ README.md
```

## 快速启动

### 1. 启动后端

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

后端默认监听 `http://127.0.0.1:8793`，健康检查：

```powershell
Invoke-RestMethod http://127.0.0.1:8793/api/health
```

### 2. 启动前端（开发）

另开一个 PowerShell 窗口：

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开 `http://127.0.0.1:5173/`，Vite 将 `/api` 与 `/runtime` 代理到后端 `8793`。

### 3. 生产构建

```powershell
cd frontend
npm run build
```

构建产物输出到 `frontend/dist/`，后端 `app/main.py` 已挂载 `dist/` 静态托管，可由后端同一端口直接提供前端页面（构建前会执行 `vue-tsc --noEmit` 类型检查）。

> 依赖只需首次安装；日常启动只执行 `python run.py` 与 `npm run dev`，不要重复 `pip install` / `npm install`。

## 架构概览

```text
┌──────────────────── 前端 Vue 工作台 ────────────────────┐
│ 招标监控 │ 跟进项目 │ 公告匹配 │ 招标文件细匹配 │ 企业材料 │ 配置自动化 │
└────────────────────────┬─────────────────────────────┘
                         │ HTTP /api
┌────────────────────────▼─────────────────────────────┐
│ FastAPI + Store 业务门面                               │
│ 项目目录、监控、解析、匹配、工作台、提醒、导出          │
└───────────────┬───────────────────────┬───────────────┘
                │                       │
      ┌─────────▼─────────┐   ┌─────────▼─────────┐
      │ 招标监控与匹配领域 │   │ 企业材料与本体领域 │
      │ adapters / service │   │ Excel / M3 / facts │
      └─────────┬─────────┘   └─────────┬─────────┘
                │                       │
      外部公开网站 / 自定义站点      M3 tenderhub 图库
                │                       │
                └──────────┬────────────┘
                           ▼
                 runtime 本地运行数据和文件
```

### 匹配链路

```text
招标文件
  -> 解析层  document_parser / document_structure（文本、章节、表格）
  -> 提取层  document_fine_matching（项目信息 / 采购需求 / 资格审查，支持智能体桥 agent_bridge 可插拔）
  -> 匹配层  document_matching + M3 事实库（公告资格粗匹配 eligibility + 招标文件细匹配）
  -> 评分 / 证据 / 缺失项 / 风险 / 投标建议
```

### 数据源：直连 M3 图库，写透到 M3

企业材料的结构化事实以 **M3 图数据库为单一事实源**（Single Source of Truth）：

```text
模板 ZIP 批量导入 / 表单逐条维护
        │
        ▼
  M3 图库 /tenderhub（upsert 对象 + 生成关系边）
        │  从 M3 读取（read_facts_from_m3 → build_profile）
        ▼
  前端展示 / 匹配评估 / 导出
```

- M3 地址与登录凭据通过环境变量 `M3_HOST` / `M3_COMPANY` / `M3_USER` / `M3_PASSWORD`（或 `agent-config.json` 的 `m3` 段）配置，不内置默认地址。
- 对象 RID 统一为 `tenderhub_{class_snake}:{业务主键}`。
- M3 不可用时，企业材料页显示计数为 0、读/写返回空结果或警告，不阻断其它功能。

## 企业材料本体 V0.4：8 类 + 8 关系

证据溯源采用**文件夹化**——事实直接挂 `evidencePath`（相对 `runtime/` 的路径），由 `/runtime/{path}` 静态路由提供文件访问。

| 类（snake_case） | 中文 | 主键字段 |
| --- | --- | --- |
| `organization` | 企业组织 | `organizationId` |
| `qualification_certificate` | 资质认证 | `qualificationId` |
| `performance_case` | 类似业绩 | `performanceId` |
| `staff_member` | 项目人员 | `staffId` |
| `financial_evidence` | 财务证明 | `financialId` |
| `technical_product` | 技术产品 | `productId` |
| `technical_parameter` | 技术参数 | `parameterId` |
| `credential` | 证照凭证 | `credentialId` |

关系方向为「企业/产品 → 事实」，共 8 条：`holds`、`completes`、`employs`、`provides_financial_evidence`、`owns_product`、`owns_credential`、`has_parameter`、`certified_by`。

> 完整本体定义见 `docs/ontology/`，字段 schema 见 `backend/app/document_recognition/ontology_defs.py`。

## 配置

AI / 监控 / 通知 / M3 等本机密钥统一放 `agent-config.json`（参考 `.env.example`），已加入 `.gitignore`，不提交 Git。详见 `docs/DEVELOPMENT_GUIDE.md`。

## 文档索引

- [项目现状与功能链路](docs/PROJECT_STATUS.md)：当前版本各功能的输入、处理、输出、数据落点与边界
- [开发计划与待办](docs/开发计划与待办.md)：待办事项与长期路线图
- [开发指南](docs/DEVELOPMENT_GUIDE.md)：启动、接口、数据流与配置
- [项目文件清单](docs/PROJECT_FILE_CATALOG.md)：目录、逐文件职责与修改边界
- [API 清单](docs/API_INTERFACES.md)：全部 HTTP 接口
- [上传资料接口](docs/上传资料接口.md)：企业材料模板导入接口
- [解析与匹配模块边界](docs/解析与匹配模块边界.md)：解析层 / 提取层 / 匹配层职责划分
- [招标筛选与抓取流程](docs/招标筛选与抓取流程.md)
- [本体设计](docs/ontology/00_总览与命名空间.md)：命名空间、8 类 8 关系
- [UI 设计规范](docs/UI设计规范.md)：Data Agent 视觉设计语言与组件规范

## 验证命令

```powershell
git diff --check
python -m compileall backend
.\backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -v
cd frontend
npm run build
```

## 重要限制

**尚未实现，不应误解为已实现：**

- 扫描 PDF、图片与图片文字 OCR；
- 需要登录、验证码或付费权限的网站抓取；
- 自动报名、自动报价、自动提交投标文件；
- 多公司、多用户、登录认证、权限隔离与正式审计日志；
- 生产级数据库、对象存储、任务队列与高可用部署。

**仍属首版能力的边界：**

- `Store` 业务职责集中，后续需拆分为项目、监控、解析、匹配、工作台与持久化服务；
- 真实网站适配器需持续回归，源站改版可能导致抓取失败；
- 评分权重与阈值主要由代码/配置约定，尚无完整后台规则管理；
- 企业材料对象已进入 M3，但资料有效期、原件核验、失效标记仍需完善；
- 响应文件生成为底稿模板，尚未完成章节映射与证据自动引用。

系统输出只做辅助判断；匹配分数与建议不构成正式投标资格结论，须回到原始公告与招标文件核验。
