# 招投标智能体 v2

面向企业业务员和投标团队的招标项目发现、企业材料匹配和响应文件辅助工作台。系统只提供**辅助判断**，不替代业务员、法务、财务或项目负责人对原始公告和招标文件的最终核验。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 前端 | Vue 3（Composition API）+ TypeScript + Vite + Element Plus + lucide-vue-next |
| 后端 | Python + FastAPI |
| 数据 | **M3 图数据库直连**（单一事实源） |
| 本体 | `tenderhub` 命名空间，V0.4（证据文件夹化） |

## 核心架构

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

- M3 地址与登录凭据通过环境变量 `M3_HOST` / `M3_COMPANY` / `M3_USER` / `M3_PASSWORD`（或 `agent-config.json` 的 `m3` 段）配置，不再内置默认地址；命名空间 `tenderhub`。
- 对象 RID 统一为 `tenderhub_{class_snake}:{业务主键}`（如 `tenderhub_organization:org-001`）。
- 导入/编辑直接 upsert 进 M3；M3 不可用时导入会记录警告，读写返回空数据，不阻断其它功能。

### 企业材料本体 V0.4：8 类 + 8 关系

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

## 目录结构

```text
.
├─ frontend/            # Vue 3 工作台（src/app 壳层、src/features 业务页、src/services 接口）
├─ backend/             # FastAPI 后端（app/api 路由、app/m3 M3 客户端与写透、app/store 状态）
├─ runtime/             # 运行时数据：catalog.json、libraries/ 证据文件等（不提交 Git）
├─ demo-data/           # 脱敏演示招标项目数据
├─ docs/                # 开发文档、本体文档、API 清单、架构方案
├─ templates/           # 企业材料收集表模板 + 填写指南
├─ M3_workspace/        # M3 对接脚本与说明（检查完整性、写透测试等，属一次性脚本）
├─ scripts/             # 辅助脚本
├─ UI设计规范.md          # Data Agent UI 设计规范
├─ 模块协作说明.md        # 前端模块职责与协作边界
└─ agent-config.json    # 本机配置：M3 连接、AI、监控（不提交 Git，见 .env.example）
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

### 2. 启动前端

另开一个 PowerShell 窗口：

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开 `http://127.0.0.1:5173/`，Vite 会将 `/api` 与 `/runtime` 代理到后端 `8793`。

### 3. 生产构建（可选）

```powershell
cd frontend
npm run build
```

构建产物输出到 `frontend/dist/`，后端 `app/main.py` 已挂载 `dist/` 静态托管，可直接由后端同一端口提供前端页面（构建前会执行 `vue-tsc --noEmit` 做类型检查）。

> 依赖只需首次安装；日常启动只执行 `python run.py` 与 `npm run dev`，不要重复 `pip install` / `npm install`。

## M3 图库连接配置

M3 连接信息优先级（从高到低）：

1. 显式构造参数（`backend/app/m3/client.py`）
2. 环境变量 `M3_HOST` / `M3_COMPANY` / `M3_USER` / `M3_PASSWORD`
3. 无内置默认值（必须通过环境变量或 `agent-config.json` 的 `m3` 段提供）

也可在 `agent-config.json` 中通过 `m3` 段覆盖（由 `app/m3/sync.py` 的 `build_m3_client` 读取）。M3 不可用时，企业材料页会显示计数为 0、读/写操作返回空结果或警告，不阻断其它功能。

AI / 监控 / 通知等本机密钥统一放 `agent-config.json`（参考 `.env.example` 与 `docs/DEVELOPMENT_GUIDE.md`），已加入 `.gitignore`，不提交。

## 核心功能

- **招标监控**：公开网站/自定义 HTML/JSON 源抓取、定时监控、去重、已读、跟进与站内/企业微信提醒。
- **匹配评估**：公告资格粗匹配 + 招标文件细匹配，输出评分、证据、缺失项、风险与投标建议。
- **标书工作台**：响应文件草稿编辑，Markdown / JSON / DOCX 底稿导出。
- **企业材料（M3）**：8 类事实 tab 浏览、模板 ZIP 批量导入（写透 M3）、逐条增删改、证据文件夹查看。
- **自动化与配置**：监控源管理、AI 连接测试、企业微信 Webhook 测试、智能体问答入口。

## 企业材料数据流

```text
设计《企业材料收集表》模板（templates/）
  → 工作人员填写 xlsx，并按序号整理证明材料/ 文件夹
  → 打包 ZIP（xlsx + 证明材料/）
  → 前端「企业材料 → 导入模板 ZIP」或 POST /api/materials/import
  → 直接 upsert 进 M3（对象 + 关系边），本地只落证明材料文件
  → 前端从 M3 读取展示，逐条维护（POST/PUT/DELETE /api/ontology/classes/*）
```

导入支持「预览 → 确认」两步（`/api/materials/import/preview` + `/confirm`），重复导入按主键 upsert 不产生重复对象；证据文件按 `libraries/company/证明材料/{表名}/{序号}/` 落盘（相对路径 `evidencePath`，后续可平滑切到 OSS）。

## 文档索引

- [开发文档](docs/DEVELOPMENT_GUIDE.md)：启动、接口、数据流、三层匹配架构与分阶段计划
- [项目文件清单](docs/PROJECT_FILE_CATALOG.md)：目录、逐文件职责与修改边界
- [本体设计](docs/ontology/00_总览与命名空间.md)：命名空间、8 类 8 关系、跨簇衔接
- [API 清单](docs/API_INTERFACES.md)：全部 HTTP 接口
- [UI 设计规范](UI设计规范.md)：Data Agent 视觉设计语言与组件规范
- [模块协作说明](模块协作说明.md)：前端模块职责与协作边界
- [数据源与架构调整](docs/数据源采购与架构调整.md)：标讯数据采购方案与 M1 改写

## 验证命令

```powershell
git diff --check
python -m compileall backend
cd frontend
npm run build
```

## 重要限制

- 尚未实现扫描 PDF/图片 OCR（招标文件侧）、Excel 表格解析、条款级证据页码、真正的三层匹配引擎、多公司数据隔离与账号权限。
- 企业材料侧已改为「人工填表导入」，不再依赖 OCR 与字段抽取；招标公告/招标文件的解析仍保留文本提取与结构化解析。
- 预置网站与项目数据仅用于演示，须回到原始网站核验。
- 系统不做自动登录、验证码处理、付费文件获取、自动报名、自动报价或自动提交正式标书。
- “下载项目”当前保存的是公告文本快照，不应理解为自动下载完整招标文件；匹配分数与建议只用于辅助业务判断。
