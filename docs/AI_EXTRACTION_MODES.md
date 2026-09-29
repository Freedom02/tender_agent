# 招标文件提取通道

当前项目只保留两条方法：

1. `builtin`：锁定主线，原有规则 + LLM + 企业材料匹配。
2. `workbuddy_agent_mcp`：由 CodeBuddy Python Agent SDK 驱动的 WorkBuddy + Agent MCP 文档地图链路。

旧的 WorkBuddy 文件桥、Sidecar、`agentBridge` 配置和 `runtime/agent_bridge`
处理代码已经删除，不再作为第三种方法或回退方法。

## 目录边界

```text
backend/app/tender_matching/
  document_parser.py
  document_fine_matching.py
  document_matching.py
  builtin_pack.py

backend/app/workbuddy_agent/
  document_reading.py
  tender_review_mcp.py
  document_map/
  workflow/
  skills/tender-document-map/
  config/
```

## builtin 主线

```text
DocumentIR
  -> 规则范围筛选
  -> 章节范围判断（配置 LLM 时）
  -> 条款 LLM + 规则抽取
  -> 标准化
  -> 企业材料/产品参数匹配
  -> fineReview / AnalysisPack / 前端
```

主线入口仍然是：

```text
backend/app/tender_matching/document_fine_matching.py
backend/app/tender_matching/document_matching.py
```

这条线的规则、LLM 兜底、技术产品服务匹配和企业证据匹配保持独立，
不依赖 WorkBuddy Agent。

## WorkBuddy + Agent MCP 新链路

```text
DocumentIR
  -> DocumentMapAgentRuntime
  -> WorkBuddy Skill + Tender Review MCP
  -> Agent 自主读取全文、识别章节范围和路由
  -> DocumentMap
  -> Validator
  -> 技术/资格细粒度处理
  -> 后续 AnalysisPack / fineReview 投影
```

新链路的代码只放在：

```text
backend/app/workbuddy_agent/
```

它负责文档地图、受控读取、章节范围和处理粒度，不直接访问企业数据库，也不绕过
来源校验。`workbuddy_agent_mcp` 固定使用 CodeBuddy Python Agent SDK，由 SDK
管理内置 headless runtime 和 MCP 会话；本地契约测试使用注入的测试 executor
验证 MCP、Validator、重试和来源护栏。

## 配置

合法值只有：

```text
builtin
workbuddy_agent_mcp
```

旧配置中的 `ai.agentBridge` 会被丢弃，不会重新启用旧方法。

两条通道共用 DocumentIR 来源 ID、AnalysisPack 和前端消费契约，但实现、配置和运行
目录完全分开。
