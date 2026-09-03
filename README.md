# 招标智策 v1

基于 `ontology-governance.json` 的招标智能体演示工作台，覆盖：

- 招标项目搜索、筛选与演示下载
- 本地招标文件导入、结构化解析
- 招标要求与企业资质、业绩、人员和地域能力的比对匹配
- 风险识别、参与建议和本体问答
- 标书底稿编辑、保存，以及 Markdown / JSON 导出
- 检索画像、评分权重和决策阈值配置
- 一键运行“筛选 → 下载 → 匹配 → 标书导出”全流程
- 主页智能体对话，可直接触发全流程、查询资料库和定时状态
- 每日定时运行记录，以及待人工复核项目统计
- 公司资料库、招标项目库、标书模板库的本地持久化
- 可保存的大模型服务商、接口地址、模型、密钥和温度配置

## 启动

在当前目录执行：

```powershell
node server.js
```

浏览器打开 `http://127.0.0.1:8793`。

如果端口已被占用：

```powershell
$env:PORT=8794
node server.js
```

## 关键文件

- `index.html`：暖色出版风格的三栏工作台前端
- `server.js`：无第三方依赖的 Node HTTP 服务和演示 API
- `ontology-governance.json`：本体治理模型
- `agent-config.json`：智能体检索、匹配和 AI 配置
- `company-profile.json`：企业能力材料样例
- `sample-tender.txt`：本地招标文件样例
- `runtime/downloads`：演示下载文件
- `runtime/exports`：标书导出文件
- `runtime/libraries`：资料库原始文本
- `runtime/libraries.json`、`runtime/catalog.json`：资料与项目库索引
- `runtime/scheduler-state.json`：每日调度运行状态

## API

`GET /api/bootstrap`、`GET /api/catalog`、`GET /api/catalog/:id`

`POST /api/catalog/:id/download`、`POST /api/import`、`POST /api/analyze`

`GET /api/workspaces/:id`、`POST /api/workspaces/:id/draft`

`POST /api/ask`、`POST /api/agent/chat`、`POST /api/export`、`POST /api/config`

`GET /api/libraries`、`POST /api/libraries/:key`

`GET /api/automation/status`、`POST /api/automation/run`

当前版本使用规则抽取和本体治理匹配作为可复现演示引擎；AI 接口配置已可由网页保存到本地，但不会把密钥发送到第三方服务。每日调度在服务运行期间每 15 秒检查一次，到达设定的时区和时间后执行一次全流程。

自动化运行会将最近记录保存到 `runtime/automation-runs.json`。存在高风险或材料缺失时，系统仍会生成标记为“强制导出-待人工复核”的底稿包，不代表可以直接递交。
