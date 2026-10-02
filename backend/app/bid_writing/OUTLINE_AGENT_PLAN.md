# 目录规划智能体 · Agent-native 改造方案（v3）

> **本文替代 v2 的「确定性补充器」路线。**
> 要求：功能的完善都由智能体完成 —— 章节由目录规划智能体**自己决定**，能力沉淀在**技能 / 工具 / 工作流**里；
> Python 只保留三件事：喂输入、校验与护栏、Pi 不可用时的显式降级。不做「为功能而纯开发」的旁路逻辑。

---

## 一、现状盘点（诚实版）

### 1.1 已完成的 M1（分支 `feat/outline-diagnosis`）

| commit | 内容 |
| --- | --- |
| `f7dbb97` | `bid_writing/outline_diagnosis.py`（只读诊断内核）、`Store.outline_diagnosis`、`GET /api/workspaces/{id}/bid-document/outline-diagnosis`、前端第 2 步「规划诊断」面板、8 例单测 |
| `85a1730` | 诊断改直读解析包文件，做到真正零副作用（原实现复用 `get_parse_bundle` 会顺带补写旧版解析包） |

### 1.2 M1 没有触碰任何智能体

没改提示词、没加 schema 字段、没加智能体、没有技能文件。它是**纯 Python + 一个只读接口 + 一个前端面板**。

### 1.3 因此 M1 的定位要改（不改代码，改语义）

诊断不再是「我来决定该有哪些章节」，而是：

1. **智能体的工具后端** —— `bid_outline_signals` 工具的现成实现就是这个只读接口；
2. **可观测面** —— 把「智能体拿到了什么信号、为什么这么定章节」摊开给人看。

---

## 二、Pi 0.84.4 实测能力面（本机核实，非推测）

| 能力 | 结论 | 用法 |
| --- | --- | --- |
| 内置 MCP | **没有**，官方设计上就不带（`docs/usage.md`），工作流交给 extensions/skills/packages | —— |
| 扩展（自定义工具） | ✅ `pi.registerTool({name,label,description,parameters,execute})` | `--extension/-e <path>`（可重复）；也可放 `.pi/extensions/*.ts`、`~/.pi/agent/extensions/*.ts`、settings `extensions: [...]` |
| 技能（Agent Skills 标准） | ✅ 目录 + `SKILL.md`（frontmatter: `name`/`description`） | `--skill <path>`（可重复）；系统提示词里只放 name/description，模型用 `read` 按需加载正文 |
| 提示词模板 | ✅ `--prompt-template <path>` | 把 agent 提示词外置成 markdown，改文案不必改 Python |
| 工具白名单 | ✅ `--tools <names>` / `--no-builtin-tools` / `--exclude-tools` | 只放我们自己的只读工具，禁掉 bash/write/edit |
| 内置工具清单 | `read` `bash` `powershell` `edit` `write` `grep` `find` `ls`（后三者默认关） | 见「六、安全」 |
| 依赖可用性 | Pi 自带 `typebox`（1.3.7）与 `@earendil-works/pi-coding-agent` 类型 | 扩展里可直接 import |

> **与「用 MCP」的关系**：Pi 不吃 MCP 配置，但扩展里完全可以自己接 MCP（写个 MCP 客户端把工具转发成 Pi 工具）。
> 建议：**先用原生扩展注册 4~5 个只读工具**（效果就是「智能体自己调工具取证再定章节」）；若之后要与
> `workbuddy_agent/tender_review_mcp.py` 那套 MCP 服务器互通，再加一层 MCP 桥接扩展，届时不用改业务。

---

## 三、目标架构

```
                 ┌───────────────────────── 招标文件（不可信文本）─────────────────────────┐
                 ▼
  解析结果 ParseBundle ──► Python：喂输入（signals / 目录骨架 / 技能正文）
                 │                                  │
                 │                       ┌──────────▼──────────────┐
                 │                       │ 目录规划智能体 OutlinePlanAgent │
                 │                       │  提示词 + SKILL.md 技能 + 工具  │
                 │                       └──────────┬──────────────┘
   只读工具（Pi 扩展，白名单） │                   │  产出：章节 + 每章依据
   ├ bid_outline_signals     │                   ▼
   ├ search_requirements     │        ┌───────────────────────┐
   ├ get_evaluation_points   │        │ 目录复核智能体 OutlineReviewAgent │
   ├ get_submission_structure│        └──────────┬────────────┘
   └ validate_outline_candidate                  │ 修正 / 确认
                                                 ▼
                                Python：schema 校验 → 放置（卷/父节点）→ 硬护栏 → 落盘
                                                 │
                                     前端第 2 步：目录树 + 规划诊断面板（可观测）
```

**职责边界（关键）**

| 谁 | 负责 |
| --- | --- |
| 智能体 | 判断该有哪些章节、标题怎么起、每章依据是什么、章节之间的从属关系 |
| 技能文件（`SKILL.md`） | 章节原型库、判断标准、放置规则、正反例、工具使用指引 —— **以后完善功能主要改这里** |
| 工具（扩展） | 取证：诊断信号、条款检索、评分点、原文骨架、候选目录自检 |
| Python | ① 喂输入（拼提示词、注入技能正文、传 env）② schema 校验与放置 ③ 硬护栏（不改原文卷/条目、每卷 ≤N、`strict` 档不做补充）④ Pi 失败时的**显式降级**（带 warning） |

---

## 四、组件与落点

| 文件 | 作用 | 状态 |
| --- | --- | --- |
| `backend/app/pi_agent/extensions/bid-outline-tools.ts` | Pi 扩展：注册只读工具 `bid_outline_signals` / `search_requirements` / `get_evaluation_points` / `get_submission_structure` / `validate_outline_candidate` | 待建 |
| `backend/app/pi_agent/skills/bid-outline/SKILL.md` | 技能正文：章节原型库、判断标准、放置规则、依据要求、正反例、工具用法 | 待建 |
| `backend/app/pi_agent/skills/bid-outline/references/*.md` | 拆出去的细则（评分类章节写法、常见漏项清单等） | 待建 |
| `backend/app/pi_agent/prompts.py` | `outline_plan_prompt` 改「用工具取证 + 按技能补充 + 每条带依据」；`OUTLINE_PLAN_SCHEMA` 增 `derived*` 字段 | 待改 |
| `backend/app/pi_agent/client.py` | 启动参数：`-e <扩展绝对路径>`、`--tools <白名单>`，从 `--no-tools` 改为**只读工具白名单**；注入 env（workspaceId / API base / runtime root） | 待改 |
| `backend/app/pi_agent/writing_orchestrator.py` | 新增 `OutlineReviewAgent` 环节（规划→复核，可回环一次） | 待改 |
| `backend/app/bid_writing/outline_diagnosis.py` | 已建（M1）：既是工具后端，也是前端可观测数据源 | 已建 |
| 只读检索接口 | `search_requirements` 等工具的后端（复用已有只读接口；缺的补 2~3 个 `GET`） | 待建 |
| 前端 `BidWritingPage.vue` | 第 2 步面板：改为展示「信号 + 智能体产出 + 每章依据 + 复核意见」 | 待改 |

---

## 五、里程碑

### M2′-0 可行性 spike（先证明再动手）— ✅ 已通过
写一个最小扩展（只注册一个 `ping` 工具）用一次真实 Pi 调用确认：扩展被加载、工具被模型调用、返回值进入后续推理。

**实测结论（本机 Pi 0.84.4 + 公司网关 deepseek-v4-pro）**

```
$env:COMPANY_LLM_API_KEY = <agent-config.json 的 ai.apiKey>
$env:PI_CODING_AGENT_DIR = <可写的 pi 配置目录>
$env:BID_WORKSPACE_ID   = workspace-spike-001
pi --provider company-ai --model deepseek-v4-pro --no-session \
   --no-builtin-tools --tools bid_ping \
   -e <扩展绝对路径>.ts --mode json \
   -p "调用 bid_ping 工具，name 传 spike-ok，然后原样转述工具返回的内容。"
```

调用日志（节选）：

```
tool_execution_start  toolName=bid_ping  args={"name":"spike-ok"}
tool_execution_end    result={"content":[{"type":"text","text":"pong:spike-ok|workspace:workspace-spike-001"}],"isError":false}
assistant             工具返回内容原样转述：pong:spike-ok|workspace:workspace-spike-001
```

一次调用约 1.8k tokens、两轮结束。证明：① `-e` 加载扩展生效；② `--no-builtin-tools` + `--tools` 白名单下自定义工具可被调用；
③ 工具返回值进入模型后续推理；④ **子进程环境变量注入可用** —— 将来用它把 workspaceId / 数据快照路径传给工具。

**操作要点（踩过的坑）**

| 事项 | 结论 |
| --- | --- |
| provider 从哪来 | `~/.pi/agent/models.json` 定义 `company-ai`（baseUrl `https://token.uino.com/v1`，apiKey 写的是 `$COMPANY_LLM_API_KEY`）→ 后端用环境变量注入密钥，不写进提示词 |
| Pi 需要写自己的配置目录 | 启动时会写 `settings.json.lock` / `auth.json.lock`；配置目录必须可写，否则报 EPERM 后直接退出 |
| 隔离测试 | 把 `PI_CODING_AGENT_DIR` 指向一个只放了 `models.json`/`settings.json` 副本的临时目录即可，不必碰真实用户配置 |
| 模型实际后端 | 网关返回 `responseModel: deepseek-flash`（对外型号名与后端型号不同，不影响） |

> 这一步不通过就不进入 M2′。**现已通过，可以动手。**


### M2′ 给目录智能体装上「技能 + 工具」
- 建技能正文（SKILL.md）与提示词接线；Python 读技能文件注入提示词（**不开放通用 `read` 工具**，理由见「六、安全」）。
- 建扩展与 5 个只读工具；`client.py` 改为工具白名单启动；注入 env。
- 改提示词规则：放开「不得新增章节」的禁令 → 改成「依据工具取证结果补充，每条必须写依据」；schema 增 `derived` / `derivedOrigin` / `derivedArchetype` / `derivedBasis`。
- Python 只做 schema 校验 + 放置 + 硬护栏。
- **前端验证**：重算目录后，技术商务卷下出现带「要求响应·补充」的章节，每章能展开看到依据（引用了哪些条款/评分点/工具调用）。

### M2′ 给目录智能体装上「技能 + 工具」— ✅ 已实现（提交 d06b2fa / d0d5807）

已落地（智能体侧）：

| 组件 | 落点 |
| --- | --- |
| 技能 | `backend/app/pi_agent/skills/bid-outline/SKILL.md`（Agent Skills 标准布局）——两条产出线、章节原型库、取证流程、放置与命名、依据写法、正反例；由 Python 读入注入提示词 |
| 只读工具 | `backend/app/pi_agent/extensions/bid-outline-tools.ts` —— `bid_outline_signals` / `search_requirements` / `get_evaluation_points` / `get_submission_structure` / `validate_outline_candidate` |
| 数据快照 | `backend/app/pi_agent/outline_agent_context.py` —— 每次调用前把诊断信号 + 条款索引 + 评分项 + 原文骨架写成 JSON，路径经 `BID_OUTLINE_CONTEXT` 注入子进程 |
| 启动参数 | `client.py`：默认仍 `--no-tools`；传 tools 时用 `--no-builtin-tools --tools <白名单> --extension <扩展>`（不开放 read/bash/write/edit） |
| 提示词 | 规则 4/5/8/14 由「不得新增章节」改为「取证后必须带依据地新增」；新增标题唯一性规则；schema 增 `derived*` 字段 |
| 放置与护栏 | `volumes.py` 增 `keep_derived`（默认 False，行为不变）；`store.py` 落盘保留 `derived*`；策略档 `strict` 时完全关闭并保持改动前输出 |

**真实数据端到端结果（数字孪生实训平台项目，210 条要求 / 8 个评分项）**

```
卷 3 / 格式条目 12 / 带依据新增 4          ← 原文骨架一个没动
★ 项目理解与需求分析 [ai_initiative/understanding]
★ 技术方案           [required_by_tender/technical_plan]
★ 技术参数证明材料   [required_by_tender/evidence_materials]
★ 培训方案           [required_by_tender/training]
```

智能体的说明（节选，均来自它自己调工具取证）：

- 「technical-0001~0026 等 145 条技术功能与性能条款目前仅有技术和服务要求响应表承载，目录中无叙述性章节承载技术方案」
- 「检索『售后 服务 响应时间 质保 维护 保修』『质量 验收 检验』『实施 进度 交付 安装 调试』均无成规模命中，故不生成对应章节，避免无依据凑数」
- 「本项目评标方法为最低评标价法（eval-007），未识别到主观评分项，故未新增『技术优势与创新点』等自由发挥章节」

**顺带修掉的两个既有 bug（在 main 上，影响整个目录功能）**

1. `writing_orchestrator._normalize_plan` 与 `store._normalize_pi_bid_plan` 在第二个循环里沿用了上一个循环残留的 `title` 变量，**所有目录节点标题被覆盖成最后一个节点的标题**（同事 2026-09-27 重构 `1796737` 引入）。
2. 质量门「未生成卷内多级章节」对直接挂在卷下的智能体新增章节误报。

**待你验证**：重启后端 → Ctrl+F5 → 第 1 步解析 / 复用评估结果 → 生成目录 → 第 2 步目录树应出现带「智能体·要求响应 / 智能体·主动发挥」标签的章节（悬浮看依据），规划诊断面板底部有「目录智能体已新增的章节」列表。

### M3′ 加「目录复核」智能体（工作流）
- 新增 `OutlineReviewAgent`：拿「诊断信号 + 生成目录」，逐条审「漏项 / 依据不足 / 放错卷」，输出修正后的目录或确认；不通过回环一次（上限 1 次，防死循环）。
- 照抄解析链路已有的 `parse_review_prompt` → `parseReviewReport` 做法。
- **前端验证**：面板里能看到复核意见（补了什么、为什么、哪条依据不足被驳）。

### M4′ 收口：降级与限额
- Pi 不可用 / 工具调用失败 → **显式降级**：直接沿用原文骨架并给出 warning（不再悄悄用确定性规则顶替智能体）。
- 限额分档（`balanced` ≤4/卷、≤12 全篇；`rich` ≤6/卷、≤24）在 Python 侧兜底 + 提示词侧写明。
- `strict` 档完全不启用本能力，输出与改动前逐节点一致。

---

## 六、安全与护栏（重要）

招标文件是**不可信输入**（可能存在提示词注入）。因此：

1. **不开放通用文件工具**：不给 `read`/`bash`/`write`/`edit`/`powershell`；只放我们自己的只读工具。
   代价：技能正文不能靠 `read` 按需加载 → 由 Python 注入提示词（技能仍是标准 `SKILL.md` 文件，改动体验不变）。
   > 若你坚持要标准按需加载（`--tools read,<自定义工具>`），要接受「智能体可读本机任意文件，
   > 包括 `agent-config.json` 里的 API Key」这一风险 —— 需要你明确点头。
2. **工具只读且窄**：工具参数只允许条款检索、评分点、诊断信号、原文骨架、候选目录自检；不接受任意路径、不写盘。
3. **硬护栏在 Python**：原文卷/条目数量·标题·顺序零改动；不新增/删除/改名招标文件明确列出的卷与条目；
   类 B 章节 `required=false`，不参与「必须提交项」核验。
4. **可追溯**：每个补充章节必须带 `derivedBasis`；工具调用与智能体产出都进 agent result 记录（现有的 `build_agent_result` 机制）。

---

## 七、风险

| 风险 | 应对 |
| --- | --- |
| 扩展加载失败 / Pi 版本差异 | M2′-0 spike 先证；失败即降级并 warning，不阻塞用户 |
| 模型不调工具、直接编章节 | 无 `derivedBasis` 的补充章节一律丢弃；复核智能体二审 |
| 每次跑结果不同 | 同一项目缓存规划结果到 `bidDocument`，重算按钮才重跑；面板标注本次工具调用次数 |
| 工具往返导致变慢 | 工具结果缓存到 process 内存 + 限制工具调用轮数（如 ≤8 轮） |
| MCP 互操作诉求 | 保留「MCP 桥接扩展」为后续独立小步，不动业务层 |

---

## 八、验收标准

1. 目录里的补充章节由智能体产出且每条带可核验依据（引用的条款/评分点 ID 真实存在）。
2. 原文骨架零改动（卷数/卷名/顺序、格式条目数/标题/顺序逐项一致）。
3. `strict` 档输出与改动前逐节点一致；三条入口（上传解析 / 复用评估结果 / 本地兜底）行为一致。
4. 章节标题自由命名后，评分点归位与篇幅档位仍生效。
5. Pi 不可用时显式降级且给出 warning（不静默替换）。
6. 单测 + 前端构建通过；新增扩展/技能有 spike 记录。

---

## 九、每一轮的固定动作

1. 改前留基线（目录节点数、叙述章数、评分点未归位数、导出份数、一次规划的耗时与工具调用次数）。
2. 只做一个里程碑；`strict` 档必须仍等于改动前行为。
3. 跑对应单测 + 前端构建；前端能验证的部分写成「验证清单」。
4. **所有改动都在分支 `feat/outline-diagnosis` 上，改完立刻本地提交留档**（只 `git add` 本轮自己的文件，绝不带上他人未提交改动）。
5. 整条改进做完再一次性 `git push` 并用 `--no-ff` 合进 `main`；未做完前 `main` 始终等于 `origin/main`。
