# 招投标智能体

面向企业投标团队的招标发现、资格与材料匹配、招标文件解析、响应文件撰写和导出工作台。系统输出用于辅助判断和起草，不能替代业务、法务、财务或项目负责人对原始公告、招标文件和最终响应文件的核验。

## 当前状态

项目采用 Vue 3 前端和 FastAPI 后端。企业材料以 M3 图数据库为单一事实源，`runtime/` 仅保存本机运行时项目、解析工件和导出文件。

招标文件上传后的 PI 原生解析链路已接入：本地解析器先提取可定位的段落、表格和文档结构；PI 编排器再按来源批次抽取条款、评审办法和原表绑定，并把严格的 `ResolvedTenderV2` 快照与兼容 `ParseBundle` 一并保存。工作台仍可使用兼容投影进入目录、章节和模板填充流程。

最新定向回归：305 项通过、1 项跳过；前端 `npm run build` 通过。该结果验证的是代码契约和编排行为，不代表 PI 对真实招标文件的语义准确率已经验收。

## 核心能力

- 招标监控：公开网站、自定义 HTML/JSON 来源抓取，定时监控、去重、跟进和通知。
- 匹配评估：公告资格粗匹配和招标文件细匹配，输出证据、缺失项、风险和投标建议。
- 标书工作台：上传招标文件、异步解析、生成目录、撰写/重写章节、原模板填充、校对与导出 Markdown、JSON、DOCX。
- 原模板复用：保留本地解析出的原表、列名、顺序和 OOXML 片段；填充以真实模板为准，不重新绘制近似表格。
- 企业材料：M3 事实浏览、模板 ZIP 导入、材料维护及证据文件引用。

## PI 解析与撰写架构

### 解析链路

```text
上传 DOCX / PDF / XLSX / TXT
  -> 本地解析、来源索引、结构与原模板提取
  -> PI 原生解析编排（全文来源批次）
     -> DocumentStructureAgent
     -> TenderClauseAgent
     -> TenderEvaluationAgent
     -> TemplateDetectAgent
     -> TenderTableBindingAgent
     -> TenderSemanticReviewAgent
  -> ClauseRepair 局部修补与严格来源/契约审计
  -> ParseBundle + ResolvedTenderV2 + review report
  -> 材料化为项目和标书工作区
```

- 每个条款、参数、证据要求、评分规则和响应表绑定都要求关联可定位的原文来源。
- `ResolvedTenderV2` 保存责任主体、响应阶段、条件、多参数、证据义务、评审规则和表格绑定；`ParseBundle` 保持与既有工作台兼容。
- 模型只能接收结构化输入并返回 JSON；本地代码负责文件读取、来源校验、SHA256、模板保真和工件落盘。
- 条款批次出现可定位的校验问题时，`ClauseRepair` 仅修改被授权的对象；无法安全定位的问题至多执行一次整批重试，失败项会标记为待复核。
- 解析任务记录节点状态、尝试次数、耗时、失败数和活动节点，前端会展示进度和待复核提示。

### 撰写链路

```text
已材料化的解析包 + 匹配结果 + M3 企业事实
  -> 目录规划与要求归属
  -> 材料匹配 / 章节计划 / 章节起草
  -> 原模板或响应表填充
  -> 章节局部重写
  -> 确定性校对 + PI 合规审查
  -> 导出 DOCX / Markdown / JSON
```

当前保留并已接线的逻辑节点包括目录规划、要求映射、材料匹配、章节计划、章节起草、表单填充、章节重写和合规审查。AI 生成的正文必须以招标要求和已匹配企业材料为依据；资料不足时应保留缺口或待复核项，而不是编造事实。

## 目录结构

```text
tender_agent/
├─ frontend/                       # Vue 3 + TypeScript + Vite 工作台
│  └─ src/features/bid-workspace/  # 标书解析、撰写、进度与导出页面
├─ backend/
│  ├─ app/api/                     # FastAPI 路由
│  ├─ app/cloud_agent/             # 异步解析任务、工件与运行状态
│  ├─ app/pi_agent/                # Pi RPC、提示词、解析与撰写编排
│  ├─ app/agent_runtime/           # 运行契约、来源审计、ResolvedTenderV2、局部修补
│  ├─ app/bid_writing/             # 目录、章节、模板、证据、校对和导出
│  ├─ app/tender_matching/         # 文档解析、要求提取和匹配
│  ├─ app/m3/                      # M3 图数据库读写
│  └─ run.py                       # 后端启动入口
├─ docs/                           # 架构、流程、诊断和接口说明
├─ templates/                      # 企业材料收集模板
├─ runtime/                        # 本机运行时数据与解析工件，不提交 Git
├─ .env.example                    # 环境变量样例
└─ agent-config.example.json       # 本机配置样例
```

## 快速启动

### 后端

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python run.py
```

后端默认地址为 `http://127.0.0.1:8797`。可通过 `TENDER_AGENT_PORT` 覆盖端口，例如已设置为 `8799` 时，以实际启动日志为准。

```powershell
Invoke-RestMethod http://127.0.0.1:8797/api/health
```

### 前端开发服务

```powershell
cd frontend
npm install
npm run dev
```

打开 `http://127.0.0.1:5173/`。开发服务器将 `/api` 与 `/runtime` 代理到后端。

### 构建前端

```powershell
cd frontend
npm run build
```

构建结果位于 `frontend/dist/`，后端会静态托管该目录。

## 配置

复制 `.env.example` 或按其中字段设置环境变量；本机密钥不要提交 Git。

```text
AGENT_PROVIDER=pi
PI_ENABLED=true
PI_COMMAND=pi
PI_PROVIDER=
PI_MODEL=
PI_API_KEY=
PI_API_TIMEOUT=360
PI_PARSE_WORKERS=3
PI_PARSE_TIMEOUT=1200
```

- 使用 PI 前，`PI_COMMAND` 指向可执行的 `pi` 命令，且对应模型网关、模型名和 API Key 必须可用。
- `PI_PARSE_WORKERS` 控制解析批次的并发数，取值被限制为 1 到 6；`PI_PARSE_TIMEOUT` 是单次解析任务的总时限，默认 1200 秒。
- `agent-config.json`、`.env`、`.pi-agent/`、`runtime/` 和构建产物已被 `.gitignore` 排除。不要在 Issue、日志或提交中粘贴 API Key。
- M3 使用 `M3_HOST`、`M3_COMPANY`、`M3_USER`、`M3_PASSWORD` 配置；不可用时不应把空结果误认为企业材料已经通过核验。

## 解析工件与验收

解析完成并材料化后，项目解析目录至少包含：

- `parse-bundle.json`：工作台兼容外层包。
- `resolved-tender-v2.json`：严格的原生撰写输入。
- `resolved-tender-v2-review.json`：来源覆盖、契约和语义复核问题。
- 原始模板片段及索引工件：用于后续原表填充和导出。

`review_required` 表示解析已返回，但来源覆盖、条款、条件、模板或评审语义仍存在需要人工确认的项；不能仅因进度到 100% 就直接视为可投标或自动放行撰写。

## 验证命令

```powershell
git diff --check
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_native_parse_v2 backend.tests.test_resolved_tender_v2 backend.tests.test_clause_repair backend.tests.test_parse_execution -q
cd frontend
npm run build
```

## 已知边界与下一步

- 原生 v2 已保存且可通过兼容投影进入既有工作台；完整的 v2 解析复核、人工确认和条件适用性前端尚未完成。
- 当前有并发、总超时、子进程回收和相同上传去重，但还没有断点续跑、缓存复用和按章节路由；长文档解析耗时仍需基于真实模型持续优化。
- 真实 PI 输出必须对照人工基线和原文验收。离线回放和契约测试不能证明模型对所有招标文件都准确。
- 不支持扫描 PDF/图片 OCR、需要登录或验证码的网站抓取、自动报名/报价/投标、完整多租户权限和生产级高可用部署。

## 文档索引

- [ResolvedTender v2 契约与原生 PI 解析](docs/resolved-tender-v2.md)
- [解析耗时诊断与优化方案](docs/解析耗时诊断与优化方案.md)
- [标书撰写智能体架构改造方案](docs/标书撰写智能体架构改造方案.md)
- [标书撰写端到端链路与智能体清单](docs/标书撰写端到端链路与智能体清单.md)
- [当前 PI 智能体清单与流程](docs/当前PI智能体清单与流程.md)
- [开发计划与待办](docs/开发计划与待办.md)
- [开发指南](docs/DEVELOPMENT_GUIDE.md)
- [API 清单](docs/API_INTERFACES.md)
- [解析与匹配模块边界](docs/解析与匹配模块边界.md)
