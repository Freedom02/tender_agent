# 项目长期笔记

## 标书生成链路（现有能力，勿重复实现）

- API：`backend/app/api/bid_writing.py`（plan / generate / 单章节生成 / export 明标暗标 / proofread / 模板下载）。
- 编排：`store.py` 的 `plan_bid_document`(1752) → `generate_bid_document`(1952) → `export_bid_document`(2003)。
- 模块：`backend/app/bid_writing/`（outline / planning / template_reuse / technical / commercial /
  deviations / param_match / qualification_match / evidence_assembler / anonymize / proofread / assemble）。
- 设计意图：`technical.py` 有刻意的三层防幻觉约束（输入白名单、提示硬约束、输出溯源）。
  任何 Agent 化改造都必须保留等价约束。

## bid-writing MCP（2026-09-27 新增）

把标书输入读取与导出暴露为外部 Agent 可调工具，免去自写 agent 编排。

- 代码：`backend/app/bid_writing_mcp/`（reader.py 定点读取 + server.py 协议与工具）
- 入口：`scripts/bid_writing_mcp.py`
- 配置：`~/.workbuddy/mcp.json` 的 `bid-writing` 服务
- **必须用 anaconda python 启动**（`D:\APP\anaconda\python.exe`）：
  managed python 无 python-docx，anaconda 才有；后端依赖也装在 anaconda。
- 工具：list_bid_projects / get_bid_context / get_requirement_detail /
  get_risk_digest / list_company_evidence / export_bid_docx
- 边界：前 5 个只读；导出只写 `runtime/bid_writing/{workspaceId}/`，不改原始数据。
- 偏离表（09-28）：`export_bid_docx` 从 `fineReview.rows` **实时派生**（读 `parameters`
  逐条成行），不再读手写 `deviations.json`；`reader.read_fine_review()` 提供未裁剪 rows。
- 技术表两列口径（09-28 定，勿改）：「**响应说明**」= 命中的企业技术参数正文
  （`evidenceItems` 里 `param-*`/技术参数条目的 `name`，不写匹配理由）；
  「**证据引用**」= **只放证明材料路径**（`locator`），不列材料名与正文。
- skill：`.workbuddy/skills/bid-writing/SKILL.md`

## 运行时数据的两个硬约束

1. `runtime/workspaces.json` 是**单文件全量 JSON，本机已 682MB**。
   全量 `json.load` 约 6 秒并驻留数 GB；必须定点切片读取（reader.py 已实现 mmap + 括号配平）。
   16 个 workspace，其中 `reasoning` 占大头，标书真正需要的 `reasoning.fineReview.rows` 只占约 4MB。
2. 企业事实来自 M3（`agent-config.json` 的 `m3` 段，namespace=tenderhub）；
   证据文件在 `runtime/libraries/company/证明材料/`（7 类：技术产品/技术参数/类似业绩/
   证照凭证/财务证明/资质认证/项目人员）。

## 数据质量结论（重要，2026-09-27；09-28 两处更正）

匹配结论目前**不足以直接生成可用标书**：

- **技术要求字段口径（09-28 更正）**：technical 行 159 条，逐条拆分字段是
  **`parameters`**（单条参数点，去重 154 条，平均 39 字）；`requirement`/`rawText`
  是**整张表格行原文**（283 条要求去重仅 3 条，单条约 5800 字）。
  偏离表/正文必须读 `parameters`，读 `requirement` 会出现整行原文重复上百次。
  （此前「159 条 = 3 条真实要求、重复展开」的判断系读错字段，已作废。）
- **匹配粒度是「整张表格行」而非参数点（09-28 新发现，需上游修）**：
  159 条技术行只有 3 种命中结果（138/14/7 行各共用一组 5 条），覆盖率约 10%/50%/78%。
  逐条可信需把匹配下沉到 `parameters`。
- 企业「技术参数」库来源已核实（09-27 更正）：来自我方对同一项目的既往投标响应
  （`demo-data/数字孪生…投标文件.docx` 的「技术和服务要求响应表」），匹配结论可信；
  真缺口是 72 个证据占位未补扫描件。
- 资格域混入招标程序描述（「开标结束后由某公司负责资格审查小组组建」等），属提取噪声。
- `technicalAlignment`（参数级对齐）在实测 workspace 中为 null。

**先修数据层（匹配下沉到参数点、补证据扫描件、过滤程序性文本），再谈全量生成。**

## 已知待修

- `bid_writing/planning.py::assign_requirements` 只读 `row["category"]`，
  而 8 域主线已在 `builtin_target_processing._public_row()` 中 pop 掉该字段，
  应改为 `category or targetModule` 并重映射 `CATEGORY_TO_SECTION`（`deviations.py` 已正确兼容）。
- `assemble_docx` 预留了 `template_path` 参数，但 `store.py:2038` 调用时未传，
  导出未套用招标方响应文件格式模板。
- `assemble.markdown_to_docx` 不渲染行内标记（粗体/引用/代码），章节正文需规避。
- **上游需改（tender_matching）**：①`requirement`/`rawText` 应存逐条要求原文，
  整行原文另设字段（现二者存整张表格行，逐条正文只在 `parameters`）；
  ②匹配粒度应从「表格行」下沉到 `parameters`（现在 159 条技术行只有 3 种命中结果）。

## 工程约定

- MCP 手写 newline-delimited JSON-RPC over stdio，零依赖（两边都没装官方 mcp SDK）。
- 不要用 bash 做文件操作：本机 Git Bash 的 PATH 异常（wc/ls/head 均不可用），
  改用 Python 脚本或专用工具。
- PowerShell 工具的 stdout 捕获不可靠：让脚本自行写 UTF-8 文件再读取。
