# PostgreSQL 表与字段总清单 + 补表实施计划

> 版本：V5（在 V4 基础上同步 **008 治理元数据同步 + 字段检阅清单重跑** 的结果）
> 状态：**✅ 54 表 / 5 视图 / 语义字典全部就位（live，001–008 均通过幂等验收）。标书模块的 schema 债已由 006/007 清完；008 把治理元数据补齐到 54 表 / 1082 列**
> 目标库：`postgresql://tender_app:***@127.0.0.1:5433/tender_agent`（本地 Docker，postgres:16，UTF8）

---

## 一、先定清楚：五个决策（已锁定）

| 编号 | 决策 | 结论 |
|:--|:--|:--|
| **D1** | 引擎与治理深度是**两个独立的轴** | 存 PG 还是 M3 不影响"要不要治理"。真正要选的是治理到多深 |
| **D2** | 治理路线 | 走**本体治理的实质**（源分析 → 本体建模 → 清洗 → DDL → 导入），**砍掉 M3 专属两步**（生成 JSONRule、同步问数系统）——那两步的产物在 PG 侧没有消费者，纯空转 |
| **D3** | 企业材料归 M3 | M3 是权威源；PG 的 A 组表是**只读镜像**（供匹配引擎事务性读取 + 结论可复现） |
| **D4** | 招标流水归 PG | 招标公告/项目/文件/解析/提取/匹配/运行/标书 → PG。招标文件**不进 M3**（流水数据、程序读、树状结构，无问数消费者） |
| **D5** | 文件归 OSS/磁盘 | PG 与 M3 都只存 `path + sha256`；不进库（不用 `bytea`） |

**一句话**：M3 管"企业有什么资产"，PG 管"这次投标过程发生了什么"，OSS 管"文件本体"。

---

## 二、现状：已建成 54 表 + 5 视图（已完成，已验收）

> **最终结构**：001 建 36 表 + 002 补 12 表 = **48 表**；003 去重补列、004 补标书读取列、
> 005 治理元数据、**008 治理元数据同步**，**都不建表**；**006 补 6 表 → 54 表**；007 重建 4 视图 + 新增 1 视图 → **5 视图**。
> `evaluation_criterion` / `project_info_item` / `document_template` 三张表**已直接从 001/002 移除**——
> 实测它们与 `tender_requirement` 存的是同一批 items（8 个模块共享同一套基础字段），
> 故按「方案 A」统一并入 `tender_requirement`，用 `target_module` 列区分模块。

验收结果（最新，008 后）：`54 tables / 5 views / encoding=UTF8`，八份迁移二次执行全部 `skipping`，**幂等验收通过**；
治理元数据已与真实结构对齐：`meta_table=54`、`meta_column=1082`（= 实际列数）、`meta_relationship=61`。

| 组 | 主题 | 表数 | 表名 |
|:--|:--|:--|:--|
| **A** | 企业事实（M3 镜像） | 10 | `fact_sync_log` `organization` `qualification_certificate` `credential` `performance_case` `staff_member` `financial_evidence` `technical_product` `technical_parameter` `evidence_file` |
| **B** | 招标与文件 | 6 | `source_site` `tender_announcement` `announcement_match_term` `tender_project` `tender_document` `document_acquisition` |
| **C** | 解析结果 | 3 | `document_segment` `document_section` `document_table` |
| **D** | 提取结果 | 3 | `requirement_group` `tender_requirement` `requirement_segment` |
| **E** | 匹配结果（核心） | 6 | `match_assessment` `match_item` `match_item_evidence` `match_item_fact` `match_risk` `review_record` |
| **F** | 运行记录 | 4 | `analysis_run` `analysis_run_stage` `llm_call_log` + **`agent_result`（006）** |
| **G** | 标书 | 5 | `workspace` `workspace_draft` `bid_document` `bid_section` `bid_plan`（006 补 37 + 39 列） |
| **P** | **解析包（006 新增）** | 5 | `parse_bundle` `evaluation_item` `template_fragment` `parse_risk` `parse_review` |
| **I** | 初筛与提醒（002） | 6 | `monitoring_rule` `screening_profile` `screening_constraint` `screening_assessment` `screening_check` `reminder` |
| **J** | 抽取补齐（002） | 1 | `procurement_need` |
| **K** | 决策与运行（002） | 2 | `decision_summary` `automation_run` |
| **L** | 治理元数据（002） | 3 | `meta_table` `meta_column` `meta_relationship` |
| **H** | 视图 | 5 | `v_match_item_full` `v_parameter_match` `v_qualification_match` `v_deviation_row` + **`v_parameter_deviation_row`（007 新增）** |

> **D 组变更说明**：原 4 张表（含 `evaluation_criterion`）→ 现 3 张。评分项不再是独立表，
> 而是 `tender_requirement` 中 `target_module='evaluation'` 的行（用 `criterion_name` / `scoring_rule` /
> `max_score` / `required_evidence` 四列承载）。
> **J 组变更说明**：原 3 张表（`document_template` / `project_info_item` / `procurement_need`）→ 现 1 张。
> 前两张删表，模板块与项目信息块同样并入 `tender_requirement`（`target_module='template'` / `'project_info'`）。
> **P 组说明（006 新增）**：合并「标书制作」代码引入的**全新解析包数据层**
> （`agent_runtime.contracts.ParseBundle`，20 字段，落 `projects/<id>/parse/parse-bundle.json`）
> 在 48 张表里一张都没有对应。006 按「读+写两向取并集」补 5 张：
> `parse_bundle`（汇总 + `submissionStructure`/`bidFormatSpec`/`documentBlocks`/… jsonb）、
> `evaluation_item`（B 档：本地 `eval_parser` 确定性产出）、`template_fragment`（B 档：本地 `template_fragments.py` 产出）、
> `parse_risk`（C 档：Pi 侧风险，本地无法重算）、`parse_review`（C 档：`parseReviewReport`）。
> **刻意不建的**：`compliance_review` → `bid_document.compliance_review` jsonb；`section_edit_history` → `bid_section.edit_history` jsonb。
> **`agent_result` 为什么不并入 `llm_call_log`**：后者语义是「一次 LLM 调用」，`AgentResultEnvelope` 是「一次智能体运行」
> （一次运行可能含多次调用），粒度不同，故独立建表。

**逐表逐字段完整清单** → 见 `表字段检阅清单.md`（+ 可批注的 `表字段检阅清单.csv`）。该文件由 `scripts/gen_field_checklist.py` **从全部迁移文件程序化提取**，含 001–008 的最终状态（含 ALTER 补列、`storage_kind` 等），改 DDL 重跑即同步，不会与库不一致。

---

## 二之二、标书读取核对（004 的依据 · 验收尺子）

**尺子**：标书制作同学要拿到的东西，能不能**直接从表里读到**？

核对方法：把真实的 `fineReview.rows[]`（0.94 GB `workspaces.json`）与 `analysisPack.targetModules[]`（10 个 `tender_pack.json`）逐字段对拍，标记「有列接收 / 无列接收」。

| # | 标书要读的东西 | 能读吗 | 从哪读 | 004 补了什么 |
|:--|:--|:--|:--|:--|
| 1 | **项目基本信息**（项目名称/采购人/代理机构/截止时间/开标时间/联系方式/保证金/有效期） | ✅ | `tender_requirement`(`target_module='project_info'`，`field_key`/`label`/`value` 11 项) + `tender_project` 结构化列 | `tender_project` 补 `opening_time`/`contact`/`bid_bond`/`bid_validity` |
| 2 | **技术资质匹配结果**（哪个产品 + 匹配到哪条技术参数 + 正文 + 证据） | ✅ | `match_item.product_name` / `parameters` + `match_item_fact`(命中 `prd-*`/`param-*`) + `match_item_evidence` | ⭐ `match_item.technical_alignment` + `gaps` |
| 3 | **评分标准**（评分项/分值/评分规则/所需证据） | ✅ | `tender_requirement`(`target_module='evaluation'`：`criterion_name`/`max_score`/`scoring_rule`/`required_evidence`) | 已覆盖，无需补 |
| 4 | **一整个模版**（模版项 + 40 个章节大纲 + 源章节视图） | ✅ | `tender_requirement`(`target_module='template'`：`template_id`/`template_type`/`writing_ready`/`source_section_ids` 41 个) + `document_section`(章节树) | ⭐ `requirement_group.source_view` + `template_count` + `source_items` |
| 5 | **废标项**（风险类型/触发词/是否否决） | ✅ | `tender_requirement`(`target_module='rejection'`：`risk_type`/`trigger_terms`/`is_rejection` 19 项) | 已覆盖，无需补 |

**为什么场景 2 和 4 是关键**：这两处前端/标书读取层是**直接把整个嵌套对象读出来渲染**的，JSON 时代「放一块」所以不用 join；落库时必须整体接住，否则展示会空：

- **场景 2** — `frontend/src/features/tender-matching/MatchingPage.vue:1751,1763,2474,2519` 直接读 `row.technicalAlignment.coverage` / `.gaps` / `.matchedUnits[].matchedParams` / `.matchedParameterDetails[].description`；`backend/app/bid_writing/param_match.py` 也从中提取命中参数。且 `bidDocument.parameterMatches`（`v_parameter_match` 视图的源）是读取时**从 `technical_alignment` 现算**的（`store.py:1819`），不落库则永算不出。
- **场景 4** — `builtin_target_processing.py:2074` 原话：*"The UI and writing workflow use sourceView and templateIndex"*。

**故意不落库的（可由读取层重建，避免冗余）**：

| 派生字段 | 重建来源 |
|:--|:--|
| `matchedObjectIds` | `match_item_fact.fact_biz_id` |
| `evidenceItems` | `match_item_evidence` |
| `matchedMaterialIds` | `match_item_fact.fact_snapshot` 里的 `materialIds` |
| `evidenceIds` | `match_item_evidence`（`source_type='evidence'`） |
| `templateSections`（41 章节） | `document_section`（用 `template.source_section_ids` 反查，字段全覆盖） |
| `bidDocument.parameterMatches` | 读取时从 `match_item.technical_alignment` 现算 |

**未拆表的理由**：`technical_alignment` 是两层嵌套（N 个产品单元 → 每个 N 条参数明细），现按 jsonb 原样落库即可满足「读出来渲染」；若将来要「按参数反查被哪些产品满足」，再拆 `match_item_matched_unit` / `match_item_matched_param` 两张表。

---

## 三、缺口分析：还有哪些数据没有表

我把 runtime 全量盘了一遍，用"**有没有表接收**"逐个核对，找出 **14 个缺口**。来源分四类：

### 3.1 缺口来源 A：初筛与提醒链（本体蓝图要求，目前完全缺表）

本体设计方案 `招标初筛与详细匹配本体设计方案.md` 定义了一条完整链路：

```
Organization ->configures-> MonitoringRule ->monitors-> SourceSite
             ->discovers-> TenderAnnouncement
Organization ->configures-> ScreeningProfile ->contains_constraint-> ScreeningConstraint
ScreeningProfile ->applies-> ScreeningAssessment ->evaluates-> TenderAnnouncement
                              ScreeningAssessment ->records_check-> ScreeningCheck
                              ScreeningAssessment ->triggers-> NotificationEvent ->notifies-> User
```

这条链里 **SourceSite / TenderAnnouncement 已有表**，但 **MonitoringRule / ScreeningProfile / ScreeningConstraint / ScreeningAssessment / ScreeningCheck / NotificationEvent / User 共 7 个类没有表**。

> 现状是：`tender_announcement` 上有 `relevance_score` / `score_breakdown` / `search_profile` / `reminder_status` 等**字段**，把"配置"和"评估结果"揉进了公告行里。走本体治理就必须拆出来——因为**配置要版本化、评估要可解释、每条约束的命中要能逐条查**。

### 3.2 缺口来源 B：抽取结果里有 3 类没有表（✅ 已解决：三表并入 tender_requirement）

`analysisPack` 的结构（来自 `projects/*/pack/tender_pack.json`，实测样例 local-0bed4975c7）：

| analysisPack 键 | 量级（实测样例） | 有表吗 |
|:--|:--|:--|
| `targetModules[].items[]` | 276/项目（8 模块） | ✅ `tender_requirement`（`target_module` 区分） |
| `templates[]` / `templateIndex[]` | 1/项目 | ✅ `tender_requirement`（`target_module='template'`，`document_template` 已移除后并入） |
| `projectInfo[]` | 11/项目 | ✅ `tender_requirement`（`target_module='project_info'`，`project_info_item` 已移除后并入） |
| `matchStageRequirements[]` / `enterpriseMatchRequirements[]` / `riskCheckRequirements[]` | 150/131/19 | ✅ **派生子集，无需单独表**：matchStage = technical+qualification+rejection；enterprise = technical+qualification；riskCheck = rejection（按 `target_module` 过滤即可） |
| `targetRanges[]` / `moduleCounts` / `stats` | — | ✅ `requirement_group.ranges`（jsonb）+ 计数按 `target_module` 聚合派生 |

> **003 关键修正**：原先以为「评分项 / 项目信息 / 模板」是三类独立抽取产物、需要单独建表。
> 实测后发现它们与技术要求、资格要求**同构**——都是 `targetModules[].items[]` 里的条目，只是
> `target_module` 不同、各带少量模块专有字段。故统一进 `tender_requirement` 一张表，专有字段补成列，
> 三张独立表删除。这也印证了「三份 requirements 清单是派生子集」——不按 `source_stage` 区分，按 `target_module`。

### 3.3 缺口来源 C：reasoning 里的 3 个结论对象没有表（runtime 有真实数据）

从 `workspaces.json`（1.0 GB）里实测到的结构：

| reasoning 键 | 实测字段 | 有表吗 |
|:--|:--|:--|
| `procurementNeeds[]` | `id/category/text/section/sourceLocator/mandatory/confidence/status/matchedObjectIds/matchedObjects/evidenceItems` | ✅ `procurement_need`（002） |
| `eligibility` | `eligibilityScore/eligibilityStatus/assessmentAvailable/hardBlockers/rejectionRisks[{requirementId,title,detail,sourceLocator}]/shouldGetTenderDocument/recommendation` | ✅ `screening_assessment`（002，`stage='document'`） |
| `decisionSummary` | `headline/projectFields{15键}/technicalMatch/technicalGaps/materialGaps/qualificationRisks/commercialRisks/scheduleRisks/needsHumanReviewCount/riskCount/warnings/recommendation` | ✅ `decision_summary`（002） |
| `relevance` | `matched/score/matchedBusinessDirections/matchedScenarios/matchedProducts/matchedCapabilities` | ⚠️ 部分（`relevance_score` + `announcement_match_term`） |

### 3.4 缺口来源 D：运行与提醒（runtime 有真实数据）

| runtime 文件 | 实测结构 | 有表吗 |
|:--|:--|:--|
| `reminders.json`（100 条） | `id/projectId/projectTitle/channels[]/createdAt/status/detail` | ✅ `reminder`（002） |
| `automation-runs.json`（当前空） | `status/startedAt/monitoring{...}` | ✅ `automation_run`（002） |
| `projects/*/runs/run-*.json` | `runId/projectId/.../stages[]/metrics/warnings/errors` | ✅ `analysis_run` + `analysis_run_stage` **完整覆盖** |
| `_proxy_dump/`、`_uino_proxy.log`、`diag_template.py` | 运维垃圾/脚本 | ⛔ **不进库**（见 §6） |

### 3.5 缺口汇总（原 14 张 —— ✅ 已由 002 全部建成，其中 2 张已移除）

| # | 表名 | 中文名 | 来源 | 优先级 |
|:--|:--|:--|:--|:--|
| 1 | `monitoring_rule` | 公告采集规则 | 蓝图 A | 高 |
| 2 | `screening_profile` | 企业初筛方案 | 蓝图 A | 高 |
| 3 | `screening_constraint` | 初筛约束 | 蓝图 A | 高 |
| 4 | `screening_assessment` | 初筛/资格评估 | 蓝图 A + C | 高 |
| 5 | `screening_check` | 约束逐条检查 | 蓝图 A | 高 |
| 6 | `reminder` | 提醒/通知事件 | D + 蓝图 A | 高 |
| 7 | ~~`document_template`~~ | 投标文件格式模板 | B | ❌ **已移除**（并入 `tender_requirement`） |
| 8 | ~~`project_info_item`~~ | 项目信息抽取项 | B | ❌ **已移除**（并入 `tender_requirement`） |
| 9 | `procurement_need` | 采购需求 | C | ✅ 已建 |
| 10 | `decision_summary` | 决策摘要 | C | ✅ 已建 |
| 11 | `automation_run` | 自动化运行记录 | D | ✅ 已建 |
| 12 | `meta_table` | 治理元数据·表 | 治理 | ✅ 已建（待 skill 灌数据） |
| 13 | `meta_column` | 治理元数据·字段 | 治理 | ✅ 已建（待 skill 灌数据） |
| 14 | `meta_relationship` | 治理元数据·关系 | 治理 | ✅ 已建（待 skill 灌数据） |

> **最终结果**：14 张里 **12 张留存**（表中 1–6、9–14），2 张（7、8）已移除。
> 另 `evaluation_criterion` 也从 001 移除——三张重复表都不再建，最终 48 表。

**合并说明（避免造近似重复表）**：
- `ScreeningAssessment`（公告初筛）与 `reasoning.eligibility`（招标文件资格预评估）**合并为一张 `screening_assessment`**，用 `stage` 列区分（`announcement` / `document`）——两者都在回答"这个机会/文件，我们够不够资格、要不要投"。
- `reminders.json` 与蓝图 `NotificationEvent` **合并为一张 `reminder`**，用 `channels[]` + `delivery_status` 承载多渠道。
- **可选暂不建**：`app_user`（工作人员）、`meta_metric`(指标口径)、`app_config`（配置版本化）。等真有需求再补。

---

## 四、002 补表的字段设计（14 张；其中 2 张已移除）

> 约定：所有表统一带 `id bigserial PK`、`org_id text NOT NULL DEFAULT 'org-001'`（多租户预留）、`created_at/updated_at timestamptz`。业务键统一为 `biz_id text NOT NULL` 并加唯一约束——**沿用旧 JSON 的 ID，前端零改动**。
>
> ⚠️ **4.7 `document_template` 与 4.8 `project_info_item` 已移除**，其字段并入 `tender_requirement`（`target_module='template'` / `'project_info'`）。保留下面两节仅供对照字段含义。

### 4.1 `monitoring_rule` 公告采集规则

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text UNIQUE | ruleId |
| `rule_name` | text NOT NULL | 规则名 |
| `enabled` | boolean DEFAULT true | 是否启用 |
| `schedule_cron` | text | 采集频率（cron） |
| `keyword_include` | text[] | 包含关键词 |
| `keyword_exclude` | text[] | 排除关键词 |
| `target_regions` | text[] | 目标地区 |
| `target_industries` | text[] | 目标行业 |
| `source_scope` | text[] | 监控的 `source_site.biz_id` 列表 |
| `description` | text | 说明 |

### 4.2 `screening_profile` 企业初筛方案

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text UNIQUE | profileId |
| `profile_name` | text NOT NULL | 方案名 |
| `profile_type` | text | 方案类型 |
| `enabled` | boolean DEFAULT true | 是否启用 |
| `threshold_score` | double precision | 提醒阈值分 |
| `hard_block_enabled` | boolean DEFAULT true | 是否启用硬性阻断 |
| `scoring_version` | text | 评分口径版本（**结论可复现的关键**） |
| `valid_from` / `valid_until` | date | 有效期 |
| `description` | text | 说明 |

### 4.3 `screening_constraint` 初筛约束

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text UNIQUE | constraintId |
| `profile_id` | text NOT NULL → `screening_profile(biz_id)` | 所属方案 |
| `constraint_name` | text NOT NULL | 约束名 |
| `constraint_type` | text NOT NULL | region/industry/service_type/amount/exclude_word/… |
| `operator` | text | in / not_in / gte / lte / contains / not_contains / regex |
| `target_value` | text | 目标值 |
| `weight` | double precision | 权重 |
| `mandatory` | boolean DEFAULT false | 是否强制 |
| `enabled` | boolean DEFAULT true | 是否启用 |
| `order_no` | integer | 顺序 |
| `explanation` | text | 业务解释（**治理要求：可读**） |

### 4.4 `screening_assessment` 初筛 / 资格评估

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text UNIQUE | assessmentId |
| `stage` | text NOT NULL CHECK IN ('announcement','document') | **区分公告初筛 / 文件资格预评估** |
| `profile_id` | text → `screening_profile(biz_id)` | 应用的方案 |
| `announcement_id` | text → `tender_announcement(biz_id)` | 公告阶段锚点 |
| `document_id` | text → `tender_document(biz_id)` | 文件阶段锚点 |
| `score` | double precision | 得分 |
| `threshold_score` | double precision | 当时阈值（快照） |
| `pass_threshold` | boolean | 是否过阈值 |
| `hard_block` | boolean | 是否硬性阻断 |
| `eligibility_status` | text | 满足 / 不确定 / 不满足 |
| `recommendation` | text | 建议（如"不建议参与"） |
| `risk_level` | text | 风险等级 |
| `matched_constraint_count` | integer | 命中约束数 |
| `total_constraint_count` | integer | 约束总数 |
| `reason_summary` | text | 结论理由 |
| `headline` | text | 一句话结论 |
| `confidence` | double precision | 置信度 |
| `assessment_available` | boolean | 是否可评估 |
| `should_get_tender_document` | boolean | 是否建议获取招标文件 |
| `hard_blockers` | jsonb DEFAULT '[]' | 硬性阻断项 |
| `risks` | jsonb DEFAULT '[]' | 废标风险（原 `rejectionRisks`） |
| `status` | text | 状态 |
| `assessed_at` | timestamptz | 评估时间 |
| `manual_confirmed` | boolean DEFAULT false | 是否人工确认 |

### 4.5 `screening_check` 约束逐条检查

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text UNIQUE | checkId |
| `assessment_id` | text NOT NULL → `screening_assessment(biz_id)` | 所属评估 |
| `constraint_id` | text → `screening_constraint(biz_id)` | 被检查的约束 |
| `requirement_id` | text | 文件阶段：命中的资格条款（软引用 `tender_requirement.biz_id`） |
| `result` | text NOT NULL | hit / miss / unknown |
| `score` | double precision | 该项得分 |
| `matched_value` | text | 命中的实际值 |
| `reason` | text | 理由 |
| `detail` | text | 明细 |
| `confidence` | double precision | 置信度 |
| `manual_confirmed` | boolean DEFAULT false | 是否人工确认 |
| `checked_at` | timestamptz | 检查时间 |

### 4.6 `reminder` 提醒 / 通知事件

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text UNIQUE | reminder-xxx / notification-xxx |
| `project_id` | text → `tender_project(biz_id)` | 关联项目 |
| `announcement_id` | text → `tender_announcement(biz_id)` | 关联公告 |
| `assessment_id` | text → `screening_assessment(biz_id)` | 触发它的评估 |
| `project_title` | text | 项目名快照 |
| `channels` | text[] DEFAULT '{}' | 页内弹窗 / 企业微信 / 邮件 |
| `title` | text | 标题 |
| `message` | text | 消息体 |
| `detail` | text | 明细 |
| `priority` | text | 优先级 |
| `status` | text NOT NULL DEFAULT '待处理' | 待处理 / 已完成 |
| `delivery_status` | text | 投递状态 |
| `dedup_key` | text | 去重键 |
| `triggered_at` / `sent_at` / `read_at` | timestamptz | 触发/发送/已读 |

### 4.7 ~~`document_template` 投标文件格式模板~~（已移除 → 并入 `tender_requirement`）

> 来源：`analysisPack.templates[]` + `templateIndex[]` + `writingProjection`，277 项/项目。**标书 agent 的核心输入之一**。

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text | template-0001 |
| `document_id` | text NOT NULL → `tender_document(biz_id)` | 所属文件 |
| `template_id` | text | TPL-0001 |
| `target_module` | text | 归属模块（template） |
| `template_type` | text | 模板类型 |
| `title` | text | 标题 |
| `text` / `raw_text` / `normalized_text` | text | 文本三态 |
| `download_text` | text | 下载态文本 |
| `handling_mode` | text | 处理方式（writing_task…） |
| `extract_mode` / `preview_mode` / `download_mode` | text | 抽取/预览/下载模式 |
| `writing_ready` | boolean DEFAULT false | 是否可写作 |
| `mandatory` | boolean DEFAULT false | 是否必填 |
| `granularity` | text | 粒度 |
| `confidence` | double precision | 置信度 |
| `source_section_id` / `source_section_ids` | text / text[] | 来源章节 |
| `child_section_count` | integer | 子章节数 |
| `source_segment_id` / `source_segment_ids` | text / text[] | 来源段落 |
| `section_id` / `section_path` | text / text[] | 章节定位 |
| `source_locator` / `start_locator` / `end_locator` | text | 溯源定位三态 |
| `table_ids` / `source_item_ids` | text[] | 关联表格/源条目 |
| `source` | jsonb DEFAULT '{}' | 原始 source 对象 |
| — | — | UNIQUE (document_id, biz_id) |

### 4.8 ~~`project_info_item` 项目信息抽取项~~（已移除 → 并入 `tender_requirement`）

> 来源：`analysisPack.projectInfo[]`（20/项目）+ `decisionSummary.projectFields`（15 键）

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text | project-info-0001 |
| `document_id` | text NOT NULL → `tender_document(biz_id)` | 所属文件 |
| `project_id` | text → `tender_project(biz_id)` | 关联项目 |
| `target_module` | text | project_info |
| `field_key` | text | **归一化字段名**（projectName / budgetAmount / bidDeadline / performanceBond …） |
| `title` / `text` / `raw_text` / `normalized_text` | text | 文本四态 |
| `value` | text | 抽取到的值（供 `projectFields` 直读） |
| `mandatory` | boolean DEFAULT false | 是否必填 |
| `confidence` | double precision | 置信度 |
| `handling_mode` | text | 处理方式（display…） |
| `source_segment_id(s)` / `source_locator` / `section_id` / `section_path` / `table_ids` | — | 溯源定位 |
| `source` | jsonb DEFAULT '{}' | 原始 source |
| — | — | UNIQUE (document_id, biz_id) |

### 4.9 `procurement_need` 采购需求

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text | need-001 |
| `announcement_id` | text → `tender_announcement(biz_id)` | 公告阶段锚点 |
| `project_id` | text → `tender_project(biz_id)` | 项目 |
| `document_id` | text → `tender_document(biz_id)` | 文件（如有） |
| `category` | text | 类别 |
| `text` | text NOT NULL | 需求原文 |
| `section` | text | 所在章节 |
| `source_locator` | text | 溯源定位 |
| `mandatory` | boolean DEFAULT false | 是否强制 |
| `confidence` | double precision | 置信度 |
| `status` | text | 待关联 / 已关联 / 不适用 |
| `matched_object_ids` | jsonb DEFAULT '[]' | 命中的企业对象 ID |
| `matched_objects` | jsonb DEFAULT '[]' | 命中对象快照 |
| `evidence_items` | jsonb DEFAULT '[]' | 证据项 |

> **取舍说明**：`matchedObjectIds/matchedObjects/evidenceItems` 保持 JSONB，不拆子表。理由：采购需求是**公告阶段的轻量预关联**，不是正式匹配结论（正式结论在 E 组）。拆三张子表会让表数从 48 涨到 50，收益不成比例。等它承担正式匹配职责时再拆。

### 4.10 `decision_summary` 决策摘要

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text UNIQUE | decisionId |
| `project_id` | text → `tender_project(biz_id)` | 项目 |
| `document_id` | text → `tender_document(biz_id)` | 文件 |
| `assessment_id` | text → `match_assessment(biz_id)` | 对应的详细匹配评估 |
| `headline` | text | 一句话结论 |
| `recommendation` | text | 建议 |
| `project_fields` | jsonb DEFAULT '{}' | 项目字段（15 键） |
| `technical_match` | jsonb DEFAULT '{}' | 技术匹配摘要 |
| `technical_gaps` / `material_gaps` | jsonb DEFAULT '[]' | 技术/材料缺口 |
| `qualification_risks` / `commercial_risks` / `schedule_risks` | jsonb DEFAULT '[]' | 资质/商务/工期风险 |
| `needs_human_review_count` | integer DEFAULT 0 | 待人工复核数 |
| `risk_count` | integer DEFAULT 0 | 风险数 |
| `warnings` | jsonb DEFAULT '[]' | 警告 |
| `summary` | jsonb DEFAULT '{}' | 原始整体快照（兜底） |

### 4.11 `automation_run` 自动化运行记录

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `biz_id` | text UNIQUE | runId |
| `status` | text NOT NULL | 完成 / 失败 / 运行中 |
| `trigger_source` | text | manual / scheduled |
| `started_at` / `finished_at` | timestamptz | 起止 |
| `elapsed_ms` | bigint | 耗时 |
| `sites_crawled` | integer | 采集站点数 |
| `announcements_found` | integer | 发现公告数 |
| `announcements_new` | integer | 新增公告数 |
| `reminders_created` | integer | 产生提醒数 |
| `llm_call_count` | integer | LLM 调用数 |
| `result` | jsonb DEFAULT '{}' | monitoring 原始结果 |
| `warnings` / `errors` | text[] DEFAULT '{}' | 警告/错误 |

### 4.12 `meta_table` 治理元数据·表

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `table_name` | text NOT NULL UNIQUE | 物理表名 |
| `table_cn_name` | text NOT NULL | 中文名 |
| `domain` | text | A企业/B招标/C解析/D提取/E匹配/F运行/G标书/H视图/M元数据 |
| `layer` | text | fact / tender / parse / extract / match / run / bid / meta |
| `description` | text | 业务说明 |
| `source_old_path` | text | 对应的旧 JSON 路径（迁移溯源） |
| `is_authoritative` | boolean | 是否权威源（企业事实表 = false，M3 才是权威） |
| `owner` | text | 负责人 |

### 4.13 `meta_column` 治理元数据·字段

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `table_name` | text NOT NULL → `meta_table(table_name)` | 所属表 |
| `column_name` | text NOT NULL | 物理字段名 |
| `column_cn_name` | text NOT NULL | 中文字段名 |
| `data_type` | text | 类型 |
| `description` | text | 业务含义 |
| `value_domain` | text | 值域说明（如"6 态契约：满足\|部分满足\|不满足\|待补材料\|待澄清\|不适用"） |
| `semantic_type` | text | id / amount / date / status / text / json / enum / bool |
| `is_business_key` | boolean DEFAULT false | 是否业务键（biz_id） |
| `is_pii` | boolean DEFAULT false | 是否含个人信息 |
| `deprecated` | boolean DEFAULT false | 是否废弃 |
| — | — | UNIQUE (table_name, column_name) |

### 4.14 `meta_relationship` 治理元数据·关系

| 字段 | 类型 | 说明 |
|:--|:--|:--|
| `constraint_name` | text | 约束名（外键有名则填） |
| `from_table` / `from_column` | text NOT NULL | 起点 |
| `to_table` / `to_column` | text NOT NULL | 终点 |
| `relation_cn_name` | text NOT NULL | 关系业务名（如"要求分组包含招标要求"） |
| `relation_type` | text | fk / soft（软引用）/ logical |
| `description` | text | 说明 |

---

## 五、实施计划（6 个阶段）

### 5.0 迁移范围决策（V3 新增）：主线优先，分批迁移 ★

**核心区分：建表已全量完成（48 张）；迁移读写分批做。**

表结构是"合同"，一次定下来避免反复改 schema；而**迁移读写**才是风险所在，分批释放。

| 批次 | 组 | 表数 | 内容 |
|:--|:--|:--|:--|
| **第一批（主线）** | A + B + C + D + E | **28** | 企业事实镜像 + 招标文件 → 解析 → 提取 → 匹配（完整匹配闭环） |
| **第二批（延后）** | F + G + I + J + K | **17** | 初筛与提醒、标书产出、运行记录、采购需求、决策摘要 |
| **已完成（治理）** | L | **3** | meta_table / meta_column / meta_relationship |

> ⚠️ **2026 标书代码合并后的修正（见 `标书表替换可行性核对.md`）——已由 006/007 解决 ✅**：G 组 5 张表是在标书代码合并**之前**设计的，实测曾**无法替换**——`bid_document` 缺 ~37 个 key、`bid_section` 缺 ~50 个 key，且新代码引入了整层 `ParseBundle`（含 evaluationItems / bidFormatSpec / submissionStructure / templateFragments / agentResults）在 48 张表里一张都没有对应。
> **已修复**：`006_bid_writing_tables.sql` 给 `bid_document` 补 37 列、`bid_section` 补 39 列，新增 `parse_bundle` / `evaluation_item` / `template_fragment` / `parse_risk` / `parse_review` / `agent_result` 6 表；`compliance_review`、`section_edit_history` 不建表，落 `bid_document.compliance_review`、`bid_section.edit_history` jsonb。
> **H 组视图缺口也已修复**（`007_bid_views_v2.sql`）：evidence / evidenceIds / matchedMaterialIds / matchedParameters / coverage / gaps / category(table_key) / matchedFacts 全部在 SQL 层重建，源数据在 `match_item.technical_alignment`、`gaps`、`match_item_evidence` 里，无需改表。
> **第二批范围仍按 V3 从 17 表扩到约 26 表**——006 只是把 schema 债补齐，读写迁移仍分批做（双写、读逐步切不变）。

> A 组（企业事实镜像）说明：迁移机制是 **M3 → PG 同步**（`read_facts_from_m3` 已独立封装），不是 JSON 回填；放进第一批是因为匹配需要它作"企业侧"输入。

**为什么这样分**

1. **对拍面 = 风险面**：全量 = 7 写点 + 5 读点同时切；主线 = 缩到 1/3，出问题好定位
2. **主线自洽闭环**：文件 → 解析 → 提取 → 匹配 → 结论，数据流单向，可独立完整验证
3. **收益最大**：数据量最大（23144 段落 / 3287 章节 / 3400 匹配行）也最乱（同一份数据存 5 处，先治这里）
4. **耦合低**：初筛是公告阶段独立链路；标书是匹配的下游；运行是运维数据

**必须遵守的约束（否则会出事）**

```text
双写一直开着（JSON 仍是权威）
   ↓
读按模块逐个从 JSON 切到 PG
   ↓
全部切完后，才统一停 JSON 写
```

**不能**在只迁一半时就停 JSON 写——还没切的模块（如标书）会读到不存在的旧数据。此约束保证任何中间阶段都安全、可回滚。

### 阶段 0 ✅ 建库建表（已完成）
- `docker-compose.yml` + `backend/migrations/001_schema_v1.sql` + `scripts/db_init.ps1`
- 验收：36 表 / 4 视图 / UTF8 / 幂等通过

### 阶段 1 补表 + 去重 + 治理元数据
- ✅ `backend/migrations/002_missing_tables.sql`：12 张新表（§四）——**已完成并通过幂等验收**
- ✅ `backend/migrations/003_merge_duplicate_tables.sql`：**提取结果去重 + 补列**（本次新增，非治理）
  - 三张重复表（`evaluation_criterion` / `project_info_item` / `document_template`）已**直接从 001/002 移除**，items 统一并入 `tender_requirement`（`target_module` 区分）
  - `tender_requirement` 补列：`source`(jsonb) + rejection/qualification/evaluation/project_info/template 各模块专有列（共 23 列）
  - `requirement_group` 补列：`ignored` 等模块级元信息 + `ranges`(jsonb)（共 21 列）
  - 另：删 `tender_requirement.source_text`（冗余死列）；`tender_document`/`evidence_file`/`document_acquisition` 加 `storage_kind`（local/oss，为 OSS 迁移做准备）
  - 验收结果：`48 tables / 4 views / 138 indexes / 157 constraints / UTF8`，二次执行 `skipping` 零报错
  - 依据：用 10 个真实 `tender_pack.json` 实测——8 个模块 items 同构，详见 §3.2
- ✅ `backend/migrations/004_bid_reading_columns.sql`：**补齐「标书读取」所需 13 列**（本次新增，非治理）
  - 动因：以「标书同学能否直接从表里读到」为尺子，对拍真实 `fineReview.rows[]` 与 `targetModules[]` 发现缺口
  - `match_item` 补 5 列：`technical_alignment`(jsonb，技术参数匹配明细) / `gaps` / `kind` / `normalized_text` / `source`
  - `requirement_group` 补 4 列：`source_view`(jsonb，撰写流程用) / `ignored_ranges` / `template_count` / `source_items`
  - `tender_project` 补 4 列：`opening_time` / `contact` / `bid_bond` / `bid_validity`（项目信息按列直读）
  - 验收结果：`48 tables / 918 字段 / 4 views / 138 indexes / 157 constraints`，二次执行 `skipping` 零报错
- ✅ `backend/migrations/005_comments.sql`：**治理元数据（精简版，未等 skill）**——已完成并通过幂等验收
  - `COMMENT ON`：48 张表全覆盖 + 256 列（5 通用列 ×48 + 30 关键业务列）
  - `meta_column` 补 `is_enabled`（对齐 JSONRule 的 `enable`）；描述列改可空（结构先灌、语义渐进补）
  - 自动灌 `meta_table`（48 行，全有中文名 + 分组 + 层级）、`meta_column`（919 行，全部字段）、`meta_relationship`（57 条外键）
  - 验收：`48 tables / 4 views / 139 indexes / 157 constraints / UTF8`，二次执行 `skipping` 零报错；`\d+ match_item` 可看中文说明
  - 踩坑：① `information_schema.columns` 含视图列 → 须 JOIN `tables` 过滤 `BASE TABLE`；② `to` 是 PG 保留字，不能做表别名
- ✅ `backend/migrations/006_bid_writing_tables.sql`：**标书模块补列 + 补表**——已完成并通过幂等验收
  - 依据：`docs/标书表替换可行性核对.md` §8.2/§8.3（写方向 37+50 key 对拍 + 读方向 ParseBundle 缺表）
  - `bid_document` 补 37 列（21 标量 + 16 jsonb，覆盖 workspace["bidDocument"] 全部 ~40 key）
  - `bid_section` 补 39 列（标量 + 数组 + jsonb + `extension` 兜底，覆盖 ~60 key）
  - 新增 6 表：`parse_bundle` / `evaluation_item` / `template_fragment` / `parse_risk` / `parse_review` / `agent_result`
  - **结构决策（§2.2 拍板）**：`bid_plan.plan` 存章节元数据（sectionId/title/kind/order/requirementIds…），`bid_section` 存内容与生成态；读取层按 `order_no` 拼回 `bidDocument.plan`
  - 验收：`54 tables`，二次执行 `skipping` 零报错（见 `docs/标书表替换可行性核对.md` §九「006/007 完成记录」）
- ✅ `backend/migrations/007_bid_views_v2.sql`：**重建 4 视图 + 新增 1 视图**——已完成并通过幂等验收
  - `v_match_item_full` 补 `evidence`（与 `_evidence_summary` 逐字一致）/ `evidenceIds` / `matchedMaterialIds`
  - `v_deviation_row` 补 `category` + `table_key`（还原 technical/commercial 二分的结构列）+ `evidence` / `evidencePath` / `requirementFull` / `requirementNumber` / `productName` / `matchedFacts`
  - `v_parameter_match` 投影 `technical_alignment` → `matchedParameters`（三层嵌套整列透出）/ `coverage` / `coveredPoints` / `gaps`；join 证据表 → `evidenceItems`
  - `v_qualification_match` 补 `evidenceItems`；域过滤补 `eligibility` + targetModule/category/matchDomain 别名
  - **新增** `v_parameter_deviation_row`（参数级技术规格偏离表，多 `matchedParameter` 列，`evidence` 语义 = 证据路径换行拼接）
  - 验收：`5 views`，二次执行 `skipping` 零报错；`evidence` 摘要串与 Python `_evidence_summary` 合成对拍 9/9 逐字一致
- ✅ `backend/migrations/008_governance_sync.sql`：**治理元数据同步（对齐 54 表 / 1082 列）**——已完成并通过幂等验收
  - 背景：005 是给 48 表灌的；006 之后 `meta_table` 仍 48 行（缺 6 表）、`meta_column` 仍 919 行（缺 163 列 = bid_document 37 + bid_section 39 + 6 新表 87）
  - `COMMENT ON TABLE` ×6 + `COMMENT ON COLUMN`（bid_document/bid_section/解析包 6 表的新列，全中文说明）
  - `meta_table` 补 6 行（P 解析包组 5 表 + F 运行记录组 `agent_result`）；`meta_column`/`meta_relationship` 沿用 005 的派生语句原样重跑自动吸收新列新外键
  - `meta_column.column_cn_name` 从 COLUMN COMMENT 直接回填（`col_description`，DRY 免维护两遍），带 `IS NULL` 守卫
  - 验收：`meta_table=54 / meta_column=1082（=实际列数）/ meta_relationship=61 / meta_column 有中文名 419`，二次执行 `skipping` 零报错
  - 顺带修掉 006 一处**多行 CHECK 约束**（`agent_result.confidence` 的 CHECK 跨行会让清单生成器把 `confidence IS NULL` 误解析成伪列 → 压成单行，SQL 语义不变）

> **与 skill 的衔接（已决策：不等 skill）**：组长给的「PG 导入本体 skill」（`promt.md` + `JSONRULE-Sample.json`）是"从 Excel 建 PG 库 + 生成 JSONRULE 接 M3 问数"的流程——**工作流对我们不适用**（表已在 PG、数据在 JSON，不经过 Excel）。
> 但其 `JSONRULE` 格式与我们的 `meta_*` 表**一一对应**，已完成对齐（补 `is_enabled`）。将来若要接 M3 问数，可由 `meta_*` 表**无损反生成 JSONRULE.json**。
> 结论：**005 自己做了，不阻塞后续**；skill 到了可作"增强版"覆盖升级（补 `column_cn_name`/`description`/`relation_cn_name`），不返工。

### 阶段 2 抽 Repository 层（第一批 28 表，zero behavior change）
- 位置：`backend/app/repository/`（`io.py` 共享读写 / `base.py` Protocol / `json_impl/` 当前实现 / `pg_impl/` + `dual.py` 占位）
- ✅ **第一刀（存储缝）已完成**：抽出 `FactsRepository`（M3 直连）/ `CatalogRepository` / `WorkspaceRepository` 三个聚合，`read_json`/`write_json` 迁到 `io.py`（名字保留向后兼容）；store.py 只换"数据从哪读写"，业务逻辑一行未动
  - 验收：py_compile 全过；运行时 3 repo 挂载 + catalog/workspace 往返一致；测试套件跑通（见 `docs/Repository层设计与读写点清单.md` §3.4）
- ⏳ **第二刀（能力方法下沉）**：把 store.py 的读能力搬进 repo（`get_workspace(id)` / `get_announcement(id)` / `list_workspaces()` / `filtered_catalog` 过滤下沉），为 pg_impl 的"按需查询"铺路；每搬一个方法跑一次测试
- 验收：所有 `/api/*` 响应与改造前 deep-equal 完全相同

### 阶段 3 双写影子（第一批）
- `dual.py` 开双写：JSON 正常写 + PG 同步影子写
- 读仍走 JSON（**零风险**），PG 只积累数据
- 验收：影子写失败不影响主流程（降级为告警）

### 阶段 4 回填存量（第一批）
- `scripts/backfill_runtime_to_pg.py`
- 目标量（已实测）：127 公告 / 23 workspace / 3400 fineReview 行 / 23144 段落 / 3287 章节 / 16 源文件 / 645 证据文件 / 8 tender_pack / 16 run
- 含**数据清洗**（日期/编号前导零/空值/对账：清洗前后行数一致）

### 阶段 5 对拍（第一批，★ go/no-go 闸门）
- `scripts/compare_json_vs_pg.py`：逐接口 deep-equal
- 不过 → 回阶段 4 修；过了才允许切读

### 阶段 6 切读（第一批）→ 重复 2–5 到第二批 → 停写 → 归档
- **第一批切读**：读按接口从 JSON 切到 PG（R1–R5），每切一个跑一次对拍；**此期间 JSON 写保持开启**（约束：写不停）
- **第二批**：对 F/G/I/J/K（17 表）重复阶段 2–5，同样遵守"写不停、读逐步切"
- 全部切完 → 停 JSON 写 → 改 `bid_writing_mcp/reader.py`（删 mmap 读 1 GB 文件的实现）
- 归档旧 JSON，释放约 **1.7 GB**（`workspaces.json` 1.0 GB + `.bak` 0.74 GB）

---

## 六、杂项数据的三分法（进库 / 留盘 / 别进库）

**判断标准：它是不是「业务事实」——不是"它现在躺在 runtime 里"。**

| 类别 | 内容 | 处理 |
|:--|:--|:--|
| ✅ **业务事实** | `catalog.json` `workspaces.json` `tender_pack.json` `run-*.json` `reminders.json` `automation-runs.json` `bid_writing/` | **进 PG**（阶段 1–4） |
| 📁 **文件本体** | `projects/*/source/`（招标文件）`libraries/company/证明材料/`（645 个）`projects/*/template-exports/` | **留 OSS/磁盘**，PG 只存 `path + sha256` |
| 🗑 **运维垃圾** | `_proxy_dump/`（20 个）`_uino_proxy.log` `diag_template.py` `document_map_agent/`（48 个中间产物）`real-tender-acceptance/` | **不进库**；日志走日志系统；`diag_template.py` 挪到 `scripts/` |

> 把日志和中间产物塞进库，就是新的一轮"乱"。PG 只装业务事实。

---

## 七、附录

- `标书读取接口说明.md` — **给标书同学看的接口约定**（5 个场景 × 表/列落点 + 待确认项），迁移后他们从 PG 读数据就看这份
- `表字段检阅清单.md` / `表字段检阅清单.csv` — **字段级权威清单**（已由 `scripts/gen_field_checklist.py` 重跑到 **54 表 / 1082 字段 / 5 视图，缺中文说明 0**，与真实库逐列对拍 0 差异）。逐表检阅就靠它，由脚本从全部迁移文件程序化提取（含 ALTER 补列），改 DDL 重跑即同步
- `匹配数据持久化设计（PostgreSQL）.md` — 可行性分析、本体映射与决策依据（D1–D5 的详细论证）
- `数据读写架构与迁移实施方案.md` — 读写点清单（W1–W7 / R1–R5）与 9 步迁移方案
- `招标初筛与详细匹配本体设计方案.md` — 本体蓝图（24 类），I 组初筛表与 D 组表的设计依据
