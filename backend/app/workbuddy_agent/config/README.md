# WorkBuddy Agent 配置

这里集中保存新的“文档地图优先”自主 WorkBuddy Agent 配置。

## 文件

```text
defaults.py
document-map-agent.example.json
tender-review.mcp.example.json
```

`defaults.py` 是 Python 默认值的唯一来源。运行时默认配置由：

```text
backend/app/workbuddy_agent/workflow/runtime.py
backend/app/config/service.py
```

共同消费。

新 Agent 只能通过 Tender Review MCP 读取当前 `DocumentIR`，不能读取任意本地路径、
执行 Shell/PowerShell、直接访问企业数据库或跳过 `DocumentMap` Validator。

Agent 执行入口固定是：

```text
CodeBuddy Python Agent SDK -> CodeBuddy headless runtime -> Tender Review MCP
```

SDK 负责消息流、MCP 进程和结果协议；项目仍然负责文档边界、审计 artifact、
DocumentMap Validator 和 builtin 回退。SDK 主通道保留 MCP 工具、禁用通用
Shell/文件/Web 工具，并使用 `settingSources=[]`，避免用户目录中的其他 MCP
被带入本次审查。`codebuddy-agent-sdk==0.3.263` 需要安装在
实际运行 FastAPI 的 Python 环境中。SDK 默认使用包内置的 headless runtime；
`codebuddyCodePath` 或 `CODEBUDDY_CODE_PATH` 只用于显式覆盖。

权限模式默认使用 SDK 的 `default`，便于在不同机器和部署环境中复用。
不要把 `bypassPermissions` 写入共享示例；如确有需要，应只在本机忽略的配置中，
并在部署环境完成权限隔离评估后显式设置。

仓库不再保留独立 `codebuddy.cmd`、Node 启动器或 CLI executor。SDK 默认使用包内
`codebuddy-headless`；`codebuddyCodePath` 或 `CODEBUDDY_CODE_PATH` 只用于显式覆盖
SDK runtime。部署时只需在实际运行 FastAPI 的 Python 环境安装
`backend/requirements.txt`，并配置 CodeBuddy 的认证和模型访问权限。

## 通道边界

旧的文件桥和 Sidecar 已删除，不再作为第三种提取方法或兼容回退。
本目录就是唯一的 WorkBuddy 实现目录，运行产物使用：

```text
runtime/document_map_agent/
```

根目录的 `agent-config.json` 仍然是全项目配置文件，包含 AI、M3、监控等多个模块；
其中 `ai.workbuddyAgentMcp` 只保存本目录新 Agent 的运行参数和配置资产。

所有相对路径都相对于项目根目录。

不要把用户目录下的 WorkBuddy 登录状态、`models.json`、API Key 或个人配置提交到仓库。
