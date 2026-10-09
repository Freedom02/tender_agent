# ResolvedTender v2：契约与原生 PI 解析

实施日期：2026-10-08。

第一步已建立撰写输入契约、历史兼容与人工回归基线；第二步已将实际 `PiClient.parse_tender` 入口切换到原生 v2 解析编排。新 PI 解析按完整原文分批抽取、独立复核，保存可追溯的条款、参数、证据义务、评审规则和原响应表绑定。

**前端复核页面、目录/正文消费者全面切换和在线模型验收尚未完成。** 当前旧接口只接收已确认的投标提交义务与有原文分值的综合评分项；`readyForWriting` 是解析审计标记，尚未成为前端生成按钮的实际阻断条件。复用历史解析不会自动获得新语义，需要重新上传/解析。

## 1. 已完成与边界

- [x] 新增版本 `resolved-tender/2.0` 的严格 Pydantic 模型，支持导出 JSON Schema。
- [x] 新增历史 `ResolvedTender` / `ParseBundle 1.0` 的只读迁移与来源诊断。
- [x] 聚合函数和 `Store.resolve_tender` 增加显式版本参数，默认行为不变。
- [x] 建立数字孪生采购文件的人工标注回归子集与自动化测试。
- [x] 对真实历史包验证条款、卷结构、模板保留，以及样本引用和源文件 SHA256。
- [x] 第二步：新 PI 解析节点产出原生字段，完成分批拆分、分类、参数与证据抽取、原表绑定和独立语义复核。
- [x] 第二步：保存内嵌原生快照与审计报告，工作台材料化时生成两个独立工件。
- [ ] 后续步骤：在线运行实际 PI 模型，与人工标注对照验收和调优。
- [ ] 后续步骤：展示待确认项，将确认状态接入目录/正文生成，并逐步切换消费者。
- [ ] 后续步骤：完善非表格固定模板与提交组成项的条款映射，以及条件材料适用性判断。
- [ ] 后续步骤：重新验证模板复制、响应表填充、原证明材料插入和导出全链路。

本次开发验证未修改现有项目的 `runtime`，未执行数据库迁移、重新生成实际标书或调用在线 PI。上线后的新解析会按下文写入解析工件。

## 2. 新契约内容

模型：`backend/app/agent_runtime/resolved_tender.py`。

| 数据 | 用途与约束 |
| --- | --- |
| `schemaVersion` / `parseBundleId` | 精确标记契约版本和解析批次；不接受未知版本 |
| `sourceFile` / `documentBlocks` | 原文件身份、不可变版本和段落/表格/单元格原文；Word 无页码时保留空值 |
| `projectMeta` | 项目名称、编号等元信息；历史嵌套包装只在新投影中展开 |
| `submissionStructure` | 原招标文件要求的卷和文件组成；不等同于模型自拟章节 |
| `templateFragments` / `templateFragmentsArtifact` | 继续保留既有模板块、样式、锁定区、可编辑字段、工件路径等原始数据 |
| `requirements` | 责任主体、是否响应、响应阶段、适用性和条件、分类、响应方式、参数、证据与模板引用 |
| `evidenceRequirements` | 一条要求可对应多份材料；材料类型、数量、条件、核验规则、获取方式、互选组单独建模 |
| `evaluationMethod` / `evaluationMethodSourceTrace` | 明确采用最低评标价法还是综合评分法，并携带方法原文 |
| `evaluationRules` | 将评分、资格/符合性门槛、价格规则与内部审查分开；数值参数不自动等于分值 |
| `responseTableSchemas` | 保留原表、原列及顺序、绑定模板和要求；来源行号与生成后的目标行号分别记录 |
| `company` / `organization` / `knowledge` | 继续透传投标人事实与原证明材料资产信息；不生成虚构证明文件 |
| `legacy` / `migrationWarnings` | 完整保存旧输入及兼容风险，不丢失比较状态、证据候选或旧分值 |

顶层和语义子模型禁止未知字段，禁止把字符串 `"true"` 当作布尔值。原文块、模板、企业资产等既有载荷仍采用开放字典，避免因增加新契约丢失旧导出需要的字段。

### 责任、阶段与适用性

- `actor`：`bidder`、`purchaser`、`agency`、`evaluator`、`background`、`unknown`。
- `responseRequired`：`true` 是已确认投标人义务，`false` 是已确认无需响应，`null` 是未确认，不能按无需响应处理。
- `responseStage`：`bid_submission`、`evaluation_clarification`、`contract_performance`、`not_applicable`、`unknown`。评审现场说明不能直接进入投标正文。
- `applicability`：`required`、`conditional`、`optional`、`not_applicable`、`unknown`；条件项必须提供 `condition`。
- `responseMode` 可同时包含 `response_table` 和 `evidence_attachment` 等多种方式；无需响应只能是 `["no_response"]`。
- `response_obligations()` 只取已确认的投标提交阶段义务。返回的条件项仍需后续根据企业和项目事实判断是否适用，不能直接全部生成。

`mandatory`、`isRejection` 和 `deviationPolicy` 分别表示强制要求、否决影响和偏离政策，不能互相替代。

### 参数与证据

`parameters[]` 的每项有名称、运算符、值、单位及来源。数字采用字符串保存精度，区间还需 `upperValue`。例如同一条款同时保存 `>=100000 个` 和 `>=60 fps`，不能压缩成一个数量字段。

证据要求是“需要什么材料”，不是“已经有什么材料”。同一要求的多份证据默认分别处理；共享 `alternativeGroupId` 的条件证据表示同组替代选择，不能无条件全部索要。样本中的营业执照和事业单位法人证书需按投标人实体类型选择。

源证据资产继续透传原企业载荷；历史迁移仍保留 `legacy`。新解析建立的是证据义务，后续证据智能体负责材料匹配、可读性、插入顺序和缺口工件。解析完成不代表证据已经插入 Word。

### 来源与表格绑定

`sourceTrace[]` 保存 `segmentId`、`fileId`、`documentVersionId`、`quote`、`sourceLocator`，以及可得的页、段落、OOXML 子块、表、行、列位置。

来源 ID 可以指向段落、表格单元格或单元格段落。扁平块与表格树中的相同来源可补齐缺失位置，但同名来源出现内容/锚点冲突时不可静默覆盖。

技术要求来自 `TABLE-018`，生成用原模板为 `TABLE-026`，两者不能混淆。`rowBindings[].targetRowIndex` 在填表前可以为空，不伪造最终行号。固定表单还可用要求的 `templateFragmentIds` 和 `formatItemIds` 关联原模板与提交组成项。

### 评审规则

最低评标价法不能携带分值规则。综合评分支持主观评分、客观评分及价格评分三种显式规则，只有这些评分规则可有 `maxScore`，且必须附分值来源；未知旧分值只能留在 `legacy`，不能进入 v2 分值字段。

`30 分钟` 是异常低价说明时间，`15%` 是符合条件的价格扣除比例，都不是技术评分。源文为中文数字或复杂评分公式时，当前轻量分值审计可能要求人工复核，不能用它替代后续评审语义智能体。

## 3. 兼容方式与调用

迁移函数不推断旧条款的责任、阶段、分类、参数和证据义务。即使旧行已标为“满足”或“资格”，也不会因此变成已确认的写作结论。缺少来源或文本时发出迁移告警；不完整原始行仍完整保存在 `legacy`。

新快照可重复迁移，结果保持相同。显式原生 v2 必须通过严格验证，不能走历史迁移自动修补。无批次 ID 的旧数据使用确定性快照 ID；不伪造源文件版本或页码。

```python
from backend.app.agent_runtime.resolved_tender import RESOLVED_TENDER_V2, ResolvedTenderV2
from backend.app.agent_runtime.resolved_audit import audit_resolved_tender
from backend.app.bid_writing.resolved_migration import migrate_resolved_tender

# 在业务代码中使用已有 store 和 workspace_id。
legacy = store.resolve_tender(workspace_id)
v2 = store.resolve_tender(workspace_id, schema_version=RESOLVED_TENDER_V2)
report = audit_resolved_tender(v2)

# 也可以仅迁移已有内存快照，不访问或修改项目存储。
snapshot = migrate_resolved_tender(legacy)
json_schema = ResolvedTenderV2.model_json_schema()
```

聚合函数 `bid_writing.resolved_input.resolve_tender` 支持同样的版本参数。默认无版本参数或指定 `1.0` 返回原有结构。显式请求 v2 时优先读取 `ParseBundle` 中的 `resolvedTenderV2`，原生内容不合法即拒绝，不能静默回退历史迁移。直接传入原生 v2 时不重做旧聚合，仅允许用调用者给出的企业资产覆盖相应字段。

旧解析方法和 `PiParseOrchestrator` 保留用于兼容；实际 PI 上传入口使用新 `PiResolvedParseOrchestrator`。此次没有改变 Dify 提供方的解析流程。

## 4. 审计输出

`audit_resolved_tender` 是只读工具，返回批次 ID、`readyForWriting`、`counts` 和含 `code` / `severity` / `path` / `message` 的问题列表。

- 结构/类型/枚举/重复 ID/悬空交叉引用不符合契约时抛出 Pydantic `ValidationError`。
- `error`：引用不存在、引文不在对应原文块、文件身份或锚点冲突、模板与原表/原列不一致等。
- `review`：责任/响应/阶段未知、来源或版本缺失、置信度未确认或低于当前审计阈值 0.8、缺少证据义务或表行绑定等。
- 只有没有 `error` 和 `review` 时才标记 `readyForWriting=true`。这只表示输入通过当前契约检查，不表示材料齐全、文件已生成或投标合规。

审计函数本身核对输入所携带的原文块，不重新打开文件或调用 PI，也不证明模型语义完全正确。第二步编排器在本地读取前后和全部模型工序结束时核对真实 SHA256，并逐批安排独立语义复核。节点失败、未判定原文块和复核发现保存为 `risk.parseIssues`，纳入审计；只读审计函数的输出由调用层落盘，尚不影响工作台按钮。

## 5. 第一步验收基线

样本源：真实“数字孪生应用实训平台采购”文件。SHA256：`fad7b3956cbd6a548f4ec4a282ea2dcc53b71385fec6421e337c9ecf20f2f3a4`。

- 原文摘录：`backend/tests/fixtures/digital_twin_v2_sources.json`。
- 人工预期标注：`backend/tests/fixtures/resolved_tender_fixtures.py`。
- 契约、审计和兼容测试：`backend/tests/test_resolved_tender_v2.py`。

基线仅含 10 个选定条款/子条款、6 个证据义务、2 个评审规则和2个响应表结构。来源块只保留选定摘录，模板只保留相应摘要/表头，并非全文件或完整 OOXML 模板；营业执照条款只标注企业及事业单位两个场景。样本置信度 1.0 表示人工预期标注，不能解释为 PI 的置信度。

验收重点：内部资格审查不写成响应内容；截图保留数量与真实性/盖章要求；同条款多参数分别保存；课程资源不变成人员培训48小时；低价解释按评审阶段和条件处理；小微企业声明按政策条件处理；商务交付期限绑定原商务响应表。

真实历史包只读验证结果：213条要求全部保留、3卷和6模板完整保留、输入无改写；样本33处来源引用及锚点对照历史原文块通过，源文件实际哈希匹配。迁移审计为0个来源错误、1290个待复核字段级诊断；这不是1290条错误条款，而是旧结构缺少新语义和版本字段的逐字段报告。

定向测试命令（标准库 unittest，不依赖 pytest）：

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_resolved_tender_v2 -v
```

第一步验证：40项新测试与相邻目录规划、运行时契约、卷结构、模板片段、保真往返、证据章节及来源测试，共178项通过。相邻测试运行中出现已有子进程流未关闭的 `ResourceWarning`，没有失败；第一步未修改相关子进程逻辑。

## 6. 第二步：原生 PI 编排

### 工作流与工具边界

本地读取文件并建立完整来源索引 → 结构与元信息 → 按原文批次抽取条款和评审规则 → 识别原模板并提取原件 → 逐批绑定原响应表并独立复核 → 聚合审计 → 兼容投影和保存。

| 智能体 | 输入 | 输出与工作流 |
| --- | --- | --- |
| `DocumentStructureAgent` | 结构摘要、文档开头、已有本地结构 | 章节和元信息；和确定性本地结构合并，元信息仍需在线验收 |
| `TenderClauseAgent` | 当前完整 `views`、相邻理解上下文、结构与元信息 | `ClauseExtraction`；拆分原子条款，判定责任/阶段/条件，抽取多参数、多证据，每个 view 都给处理结论 |
| `TenderEvaluationAgent` | 当前完整原文与当前批条款 | `EvaluationExtraction`；识别实际评审方法，区分门槛/价格政策/评分，核实显式分值来源 |
| `TemplateDetectAgent` | 格式相关原文、结构、完整本地提交组成树 | 原模板规格与提交组成；继续使用已有确定性模板提取和 OOXML 保真工具 |
| `TenderTableBindingAgent` | 当前需填表条款、原响应表及模板目录 | `TableBinding`；选择真实响应模板表，保留原列名和顺序，建立条款行绑定，目标行号留空 |
| `TenderSemanticReviewAgent` | 当前完整原文、条款、证据、评审和表绑定 | `SemanticReview`；独立检查遗漏和语义误判，精确覆盖全部 view/条款，输出具体问题 |

四个新中间契约位于 `backend/app/agent_runtime/parse_semantics.py`，禁止未知字段和隐式类型转换。每个新节点有独立提示词、JSON Schema 和智能体登记。新节点通过真实 `PiClient.run_json` 调用，但停用旧的附加形态校验字段，由编排器执行严格校验；模型任意多写的字段仍拒绝。

PI 工序不给 shell、联网或写文件权限。本地编排器调用只读解析/来源索引/引文校验、SHA256、提交组成识别、模板提取、审计与保存工具；写工件由业务代码负责，模型不能修改原文件或生成假证明材料。

### 全文覆盖与失败处理

- `build_source_batches` 保留全部可定位非空原文；同一表格行去掉重复的单元格视图，超长块切分但不丢字。`contextViews` 仅帮助理解，不能冒充本批引用来源。
- 每条参数、证据和评审规则带逐字引文。代码核实来源 ID、真实原文、文件版本和位置，拒绝跨批引用与伪造锚点。
- 条款抽取必须对本批每个 view 给出唯一处理结论，抽出的条款必须关联真实来源；条款、证据和互选组 ID 按批次命名，保持交叉引用。
- 节点校验失败最多修正/重试一次，策略拒绝不重复同一节点；保留每次输出和脱敏错误。抽取失败仅保留 `unknown` 原文，不能制造已确认义务。
- 部分失败产出 `review_required`，全部节点失败则解析任务失败。方法冲突、最低价法混入评分、原表属性冲突、复核遗漏均记录，不能伪报就绪。
- 原响应表只能选择实际模板包含的原表，列名和顺序逐字保留，不重画近似表；非表格固定模板义务的完整自动映射留待下一步。

### 工件与兼容入口

`ParseBundle 1.0` 继续作为旧页面的外层包，新增 `resolvedTenderV2` 和 `parseReviewReport`。原文块、原模板片段、工件路径和企业真实资产保持兼容；在最终模板提取后重新同步内嵌快照并审计，核对批次、项目、工作区和来源身份。

后台解析任务首先保存内嵌快照。工作台完成材料化并调用 `Store.save_parse_bundle` 后，项目解析目录包含：

- `parse-bundle.json`：外层兼容包与内嵌 v2；
- `resolved-tender-v2.json`：严格原生撰写输入；
- `resolved-tender-v2-review.json`：就绪状态、问题列表和原文处理覆盖统计；
- 既有原模板工件与索引继续保留，路径同步到原生输入。

旧 `requirements` 仅投影 `responseRequired=true` 且 `responseStage=bid_submission` 的条款；原阶段、条件、参数、证据与语义分类仍保留，未知条款和履约/评审现场流程保存在 v2 中供后续复核。旧 `mandatory` 必须为布尔值，未确认时投影为 false，原三态值保存为 `semanticMandatory`，不能据此判定无需响应。旧 `evaluationItems` 仅投影综合评分法中有明确来源分值的评分项。

### 第二步验证与实际体验

`backend/tests/test_native_parse_v2.py` 使用人工标注回放验证协议，覆盖全文后半段、超长表行、引文/ID/锚点造假、逐块遗漏、修正与失败、方法与分值误判、原表保留、多批合并、来源变化、真实客户端传输接线、模板工件及存储读取。回放没有调用在线模型，**不能将协议测试解释为 PI 语义准确率**。

真实数字孪生原 DOCX 只读验证：2455 个原文单元，其中 2388 个非空单元、27 张表；新流程组织为 23 批、1372 个去重视图，直接引用或完整表格行覆盖所有非空单元，遗漏为 0。源文件 SHA256 与人工基线一致。所有原文被送入流程不等于所有义务均被正确识别，后者需在线对照基线。

该文件计划进度节点为 `2 + 4 × 23 = 94`，无表格绑定批次跳过该调用，失败节点最多增加一次调用。视图 JSON 预算为每批 14000 字符；相邻上下文、语义结果、结构摘要和 JSON Schema 另占模型输入。调用数与耗时会高于旧流程，实际延迟、输出长度和模型行为需在线测量后优化。

```powershell
backend\.venv\Scripts\python.exe -m unittest backend.tests.test_native_parse_v2 backend.tests.test_resolved_tender_v2 backend.tests.test_bid_plan_tree backend.tests.test_agent_contracts backend.tests.test_agent_runtime_contracts backend.tests.test_submission_volumes backend.tests.test_template_fragments backend.tests.test_fidelity_roundtrip backend.tests.test_evidence_chapter_fill backend.tests.test_writing_source_evidence backend.tests.test_parse_chain_merge backend.tests.test_cloud_agent backend.tests.test_document_parser -q
```

第二步最终验证：上述回归运行 260 项，259 项通过、1 项跳过；其中原生编排新增 28 项全部通过。另有 5 项既有 PI 客户端组合与启动参数测试通过，共计 265 项、264 项通过、1 项跳过。启动参数测试在获准的沙箱外读取已有 `pi.cmd`，没有启动在线模型；沙箱内读该命令受限，不能据此判断电脑未安装 PI。相邻测试仍有既有子进程流 `ResourceWarning`，本轮未改相关逻辑。`git diff --check` 通过。

重启后端后，在标书工作台重新上传/解析源文件；完成解析和材料化后可通过解析包接口或项目解析目录检查新工件。当前旧页面展示的是兼容投影，完整责任、阶段、证据及待确认项仍需第三步前端接入。历史项目选择“复用解析结果”仍使用旧快照；不应以此验收新解析效果。

## 7. 下一步接入要求

2026-10-08 性能修复：原生条款节点的第二次调用已接入 `ClauseRepair` 局部修补契约。模型只返回有问题对象的替换或遗漏对象的补充，本地工具控制可修改范围并在合并后重新执行整批契约、来源、覆盖和双向引用校验。结构损坏等不能可靠定位的情况才执行一次全量重试，失败结果仍标待复核。逐节点记录提供诊断信息，尚未实现断点续跑。真实失败批次验证及剩余性能待办见 [解析耗时诊断与优化方案](解析耗时诊断与优化方案.md)。

下一步先使用真实 PI 输出对照人工基线，然后接入解析复核页面、可追溯人工确认与条件适用性，再让目录和正文消费者读取确认后的 v2。**不能通过把 unknown 全部默认改成 bidder/required 来过关**，也不能仅凭任务进度 100% 就放行自动撰写。

保留现有原模板复制、确定性填充和证据插入工具。解析智能体负责正确选择“哪份模板、哪些可写字段、哪些响应行、哪些证据义务”，不负责重新画一个近似模板替代原件。等新解析结果通过语义和保真验证后，才切换目录、正文、表格和证据消费者。
