# WorkBuddy 智能体接入方式说明

> ## ⚠️ 本文档已过期，勿据此实施（2026-09-30 标注）
>
> **本文描述的 `agent_bridge` 机制已经从代码中删除。** `backend/app/tender_matching/agent_bridge.py`
> 在 git `f070899`（2026-09-24）中被移除，`git ls-files` 中已不存在任何 `agent_bridge` / `sidecar` 代码。
>
> 因此下文所述的 `runtime/agent_bridge/inbox|outbox` 任务包协议、`file` / `http` 两种传输方式、
> `agentBridge` 配置段、`workbuddy-agent` 提取层 Provider 接入流程，**均不再对应现行代码**。
> （`config/service.py` 里那段 `agentBridge` 默认值也已一并清除——它已无任何代码读取。）
>
> **现行实现是 `backend/app/workbuddy_agent/`：**
>
> | 用途 | 位置 |
> |---|---|
> | 配置与默认值 | `backend/app/workbuddy_agent/config/defaults.py` |
> | 配置说明 | `backend/app/workbuddy_agent/config/README.md` |
> | 能力契约 | `backend/app/workbuddy_agent/skills/tender-document-map/SKILL.md` |
> | 运行时目录 | `runtime/document_map_agent`（**不再是** `runtime/agent_bridge`） |
> | 测试 | `backend/tests/test_workbuddy_agent_matching.py` |
>
> 本文仅作为**历史沿革记录**保留，用于理解当初的接入尝试及其失败原因
> （Sidecar 能启动、能读任务包，但 headless 智能体始终未完成登录鉴权）。
> 同一事实另见 `docs/企业经营智能体-现状盘点-招投标智能体.md` 第 4.2 节与"已核实的文档/代码偏差"第 8 条。

本文整理当前项目中 WorkBuddy / CodeBuddy 智能体的接入现状、登录方式、实际能力边界，以及后续可选的接入路线。

## 1. 核心结论

当前项目中接入的并不是完整的 WorkBuddy 桌面端 API，而是一个本地智能体桥接通道：

```text
FastAPI 后端
  ↓
runtime/agent_bridge/inbox/*.job.json
  ↓
Sidecar 独立进程
  ↓
Python SDK / headless CLI
  ↓
runtime/agent_bridge/outbox/*.result.json
  ↓
document_fine_matching
```

项目里把这个通道标记为 `workbuddy-agent`，但这只是提取层的 Provider 名称，不能直接说明已经调用到了完整 WorkBuddy 桌面端能力。

从当前日志看，Sidecar 能启动、能读取任务包，但 headless 智能体没有完成登录，实际报错为：

```text
Authentication required. Please use /login command to sign in to your account
```

因此当前状态是：

```text
架构已预留
任务协议已定义
Sidecar 已能运行
但智能体鉴权未打通
当前没有成功返回有效 WorkBuddy / CodeBuddy 提取结果
```

## 2. 当前项目的接入形式

关键代码文件：

```text
backend/app/tender_matching/agent_bridge.py
```

当前支持两种传输方式：

| 传输方式 | 含义 | 当前状态 |
| --- | --- | --- |
| `file` | 文件收件箱模式，后端写 job，Sidecar 轮询处理 | 当前使用 |
| `http` | 本机智能体 HTTP 服务，后端同步调用 | 代码预留 |

当前配置位于：

```text
agent-config.json
```

相关配置：

```json
{
  "agentBridge": {
    "enabled": true,
    "transport": "file",
    "runtimeDir": "runtime/agent_bridge",
    "httpEndpoint": "http://127.0.0.1:8911",
    "timeoutSeconds": 20,
    "pollIntervalSeconds": 1,
    "writePack": true,
    "packDir": "runtime/projects"
  }
}
```

### 文件桥工作过程

```text
1. 后端解析招标文件
2. 生成 segments / sections / tables
3. agent_bridge.build_job() 生成任务包
4. 写入 runtime/agent_bridge/inbox
5. Sidecar 读取 job.json
6. Sidecar 调用 headless 智能体
7. 写入 runtime/agent_bridge/outbox
8. 后端读取 result.json
9. 归一化后进入匹配和撰写模块
```

## 3. 当前不是“原始文件直接交给 WorkBuddy”

当前项目并不是把原始 PDF / Word / Excel / 图片直接上传给 WorkBuddy 桌面端。

实际流程是：

```text
招标原文件
  ↓
document_parser
  ↓
解析成文本片段、章节、表格
  ↓
agent_bridge 打包任务
  ↓
交给智能体提取条款
```

也就是说，智能体主要拿到的是结构化后的文本片段：

```json
{
  "segments": [
    {
      "id": "SEG-001",
      "text": "投标人须具备……",
      "section": "投标人资格要求",
      "sourceLocator": "第12页"
    }
  ],
  "instructions": "请提取招标文件要求……",
  "outputSchema": {}
}
```

而不是完整原始文件。

当前项目自身负责文件解析，支持的解析能力主要来自：

```text
backend/app/tender_matching/document_parser.py
```

项目侧负责：

```text
PDF / DOC / DOCX / XLSX / PPTX / 纯文本解析
章节识别
表格提取
片段编号
原文定位
```

智能体侧负责：

```text
理解文本
拆分招标要求
分类
判断 mandatory / rejection
输出结构化 JSON
```

## 4. 当前登录方式

当前 Sidecar 使用的是 headless CLI / SDK 的登录状态，不是 `agent-config.json` 里的普通大模型 API Key。

项目中 `agent-config.json` 配置了：

```json
{
  "baseUrl": "https://token.uino.com/v1",
  "model": "qwen-turbo",
  "apiKey": "..."
}
```

这属于普通 OpenAI 兼容模型通道，主要用于项目内置 LLM 调用。

它不等于 WorkBuddy / CodeBuddy headless 登录凭据。

因此：

```text
项目 AI API Key
  ≠
WorkBuddy / CodeBuddy headless 登录状态
```

当前日志证明 headless 未登录：

```text
Authentication required. Please use /login command to sign in to your account
```

需要在运行 Sidecar 的同一用户环境下完成 CLI 登录，才可能让 headless 调用成功。

## 5. 当前实际拥有的能力

### 已明确拥有

当前项目代码已经具备：

```text
1. 文件收件箱桥接
2. Sidecar 心跳检测
3. 任务包生成
4. 招标条款提取提示词
5. 固定 JSON 输出 Schema
6. 分批处理
7. 结果写回 outbox
8. 原文片段 ID 校验
9. 章节 ID 校验
10. 智能体结果归一化
11. tender_pack.json 产物生成
12. 智能体失败时回退到内置模型 / 规则通道
```

智能体被要求输出的核心字段包括：

```text
category
rawText
normalizedRequirement
section
sourceSegmentId
sourceSegmentIds
mandatory
isRejection
evidenceNeed
responseType
quantity
timeLimit
confidence
```

### 当前不能确认拥有

当前项目没有证据证明 headless 环境已经拥有：

```text
1. WorkBuddy 桌面端全部 Skill
2. WorkBuddy 技能市场插件
3. WorkBuddy 办公连接器
4. 腾讯文档连接器
5. WorkBuddy 桌面端会话历史
6. WorkBuddy GUI 文件上传能力
7. WorkBuddy 桌面端“任意格式文件智能解析”能力
8. 自动访问用户电脑任意文件的权限
```

因此不能把当前 headless 通道等同于完整 WorkBuddy 桌面端。

## 6. 工具与 Skill 的边界

当前日志显示，平台仍声明了一批工具，例如：

```text
Skill
ToolSearch
ComputerUse
Workflow
Monitor
REPL
A2AGetTask
A2ASendMessage
```

日志同时提示：

```text
工具护栏未完全生效
```

这说明两个问题：

1. 当前 headless 环境能看到一些平台工具声明；
2. 项目原先设置的工具黑名单没有完全覆盖新增工具。

但这不等于这些工具都可安全、稳定、符合预期地使用。

尤其是 `Skill` 这个工具名出现，并不能直接证明 WorkBuddy 桌面端 Skill 市场中的技能已经自动加载。它只说明底层平台声明了一个叫 `Skill` 的工具入口，具体是否能调用、能调用哪些技能、是否需要授权，还需要单独验证。

## 7. 除当前方案外的其他接入方式

### 方案 A：WorkBuddy 官方 Agent / OpenAPI

理想调用方式：

```text
你的应用
  ↓
WorkBuddy API 上传文件 / 创建任务
  ↓
WorkBuddy 智能体处理
  ↓
你的应用获取结构化结果
```

这是最符合“你的后端直接调用 WorkBuddy 智能体”的方式。

优点：

```text
用户不必打开 WorkBuddy 桌面端
后端可以统一调度
更适合 SaaS / Web 应用
任务、结果、错误都可被系统管理
```

限制：

```text
需要 WorkBuddy 官方明确提供对应 API
需要确认是否支持文件上传、技能调用、结构化输出
需要确认授权和计费方式
```

这是最值得优先向 WorkBuddy 官方确认的路线。

### 方案 B：WorkBuddy MCP + Skill 连接器

调用方向是：

```text
WorkBuddy
  ↓
你的 MCP Server
  ↓
你的招标解析 / 匹配 / 撰写服务
```

这种方式不是“你的后端调用 WorkBuddy”，而是“WorkBuddy 调用你的系统”。

适合场景：

```text
用户主要在 WorkBuddy 里工作
希望在 WorkBuddy 中调用招标解析、匹配、撰写能力
```

你可以暴露工具：

```text
parse_tender_document
extract_tender_requirements
match_company_capability
generate_bid_response
```

再写 Skill，告诉 WorkBuddy：

```text
什么时候调用工具
传什么参数
如何解释结果
是否需要用户确认
```

优点：

```text
符合 WorkBuddy 连接器机制
可复用 WorkBuddy 对话和办公体验
适合企业内部使用
```

缺点：

```text
用户需要在 WorkBuddy 中启用连接器
不适合完全隐藏 WorkBuddy 的独立 Web SaaS
调用方向是 WorkBuddy → 你的系统
```

### 方案 C：WorkBuddy CLI + Skill

调用方向也是 WorkBuddy 调你的本地 CLI：

```text
WorkBuddy
  ↓
Skill
  ↓
你的 CLI
  ↓
你的后端或本地程序
```

适合已有本地命令行程序的场景。

优点：

```text
可处理本地文件
部署相对简单
适合企业内部桌面工具
```

缺点：

```text
安装、升级、登录态管理复杂
不适合作为正式 SaaS 主链路
仍然不是你的后端直接调用 WorkBuddy 技能
```

### 方案 D：桌面端自动化

形式是：

```text
Sidecar
  ↓
控制 WorkBuddy 桌面窗口
  ↓
上传文件、输入提示词
  ↓
读取返回结果
```

优点：

```text
最接近人工使用 WorkBuddy 桌面端
理论上能复用桌面端已登录账号、已安装 Skill、文件解析能力
```

缺点：

```text
不稳定
难并发
依赖窗口状态
容易被 UI 更新打断
难审计
不适合正式生产
```

只建议作为短期验证或演示，不建议作为核心架构。

### 方案 E：继续完善当前 headless / SDK 方案

当前项目已经走在这条路线上：

```text
FastAPI
  ↓
inbox / outbox
  ↓
Sidecar
  ↓
headless CLI / SDK
```

优点：

```text
和当前系统集成度最高
结果可以直接进入匹配和撰写模块
不需要操作桌面窗口
适合后台自动化
```

缺点：

```text
当前未登录成功
不等于完整 WorkBuddy 桌面端
不一定拥有桌面端 Skill、插件、连接器
需要项目侧自己处理文件解析和 JSON 校验
```

## 8. 针对招标文件解析的推荐路线

如果目标是：

```text
用户在你的应用上传招标文件
系统自动解析
结果进入匹配和撰写模块
```

推荐优先级：

```text
1. 向 WorkBuddy 官方确认是否有后端 Agent / 文件解析 API
2. 如果有，优先接官方 API
3. 如果没有，继续修通当前 headless 方案
4. 同时保留项目自己的 document_parser，保证格式解析可控
5. 另做 MCP + Skill，让 WorkBuddy 也能调用你的招标系统
6. 桌面端自动化只作为临时验证
```

如果目标是：

```text
用户主要在 WorkBuddy 中工作
并希望 WorkBuddy 调用招标解析、匹配和撰写能力
```

则推荐：

```text
MCP + Skill 连接器
```

## 9. 当前最应该做的验证

### 第一步：修通登录

在 Sidecar 运行的同一用户环境下，完成 headless CLI 登录。

验证目标：

```text
1. CLI / headless 不再报 Authentication required
2. 能对一段测试文本返回结果
3. 能输出有效 JSON
4. outbox 中 result.json 有 requirements
```

### 第二步：验证文件能力边界

分别测试：

```text
普通 PDF
扫描 PDF
DOCX
XLSX
图片
长文档
复杂表格
多附件
```

观察：

```text
是否能提取文本
是否能保留页码
是否能识别表格
是否能稳定返回 JSON
是否能提取资格、技术、商务、评分、废标条款
```

### 第三步：验证 Skill 是否可用

不要只看日志里是否出现 `Skill` 工具名，要实际验证：

```text
1. 指定某个 WorkBuddy Skill 是否能被 headless 调用
2. Skill 是否能访问文件
3. Skill 是否能稳定输出结构化结果
4. Skill 是否依赖 GUI / 用户会话
```

### 第四步：验证工具护栏

当前日志显示仍有工具未被黑名单覆盖。

需要确认：

```text
1. 哪些工具允许使用
2. 哪些工具必须禁用
3. disallowed_tools 是否真的生效
4. 是否需要更新黑名单
5. 是否需要审批机制
```

## 10. 安全提醒

当前 `agent-config.json` 中存在明文 API Key 和企业微信 Webhook。

建议立即处理：

```text
1. 撤销并重新生成当前 API Key
2. 重新生成企业微信机器人 Webhook
3. 将密钥移入环境变量或本地密钥存储
4. 确认 agent-config.json 不提交到 Git
5. 在配置展示时继续保留脱敏逻辑
```

## 11. 最终判断

当前项目已经具备“把外部智能体作为招标条款提取 Provider”的架构基础，但目前还没有真正打通可用的 WorkBuddy 智能解析链路。

当前最准确的描述是：

```text
项目已有智能体桥接框架
当前使用 file transport + Sidecar
任务输入是项目解析后的 segments，不是原始文件
当前 headless 鉴权失败
不能确认具备完整 WorkBuddy 桌面端 Skill 和文件解析能力
```

下一步的关键不是继续优化提示词，而是先确认：

```text
1. headless 登录是否可用
2. headless 是否真的能复用 WorkBuddy 的文件解析 / Skill 能力
3. WorkBuddy 是否提供官方后端 Agent / 文件解析 API
4. 是否需要转向 MCP + Skill 连接器模式
```

