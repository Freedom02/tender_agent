# 统一分类、路由与切块设计

> 记录版本：2026-09-20
> 当前状态：P05 受控可用，前后端统一字段已接入；正在进行前端验收与全量回归。
> 样本文档：本机评测样本（不随仓库提供）

## 1. 目标

章节分类、条款分类、企业匹配、写标和前端展示使用同一套后端契约。

分类回答“内容是什么”；路由回答“后续谁处理”；切块粒度回答“需要处理到什么程度”；复核字段回答“谁来确认”。

前端不再根据标题、关键词或 `category` 自行猜测页面分区，只消费后端输出的统一字段。

## 2. 统一对象

章节和条款都保留以下字段：

```json
{
  "contentType": "technical",
  "typeHits": ["technical", "document_format"],
  "primaryRoute": "company_match",
  "secondaryRoutes": ["writing_context"],
  "matchDomain": "technical",
  "decisionImpact": "important",
  "granularity": "clause",
  "reviewRequired": true,
  "reviewMode": "manual",
  "reviewReason": "同时命中技术参数和响应模板",
  "classificationSource": "hybrid",
  "confidence": 0.86
}
```

字段定义：

| 字段 | 含义 |
|---|---|
| `contentType` | 内容主类型 |
| `typeHits` | 内容同时涉及的类型 |
| `primaryRoute` | 后续主要处理工作流 |
| `secondaryRoutes` | 其他消费方 |
| `matchDomain` | 企业能力匹配领域 |
| `decisionImpact` | 对是否继续投标的影响 |
| `granularity` | 章节、条款、表格行或字段粒度 |
| `reviewRequired` | 是否需要二次复核 |
| `reviewMode` | `none`、`auto`、`assisted` 或 `manual` |
| `reviewReason` | 触发复核的原因 |
| `classificationSource` | `rule`、`content_rule`、`llm`、`hybrid` 或 `fallback` |
| `confidence` | 分类置信度 |

## 3. 内容类型

```text
project_info       项目基本信息
scope              采购范围、采购标的概览
technical          技术、产品、功能、性能参数
service_plan       实施、培训、运维、售后方案
qualification      资格、资质、证书
performance        类似业绩
personnel          人员要求
financial          财务要求
commercial         商务条件
pricing             报价、保证金、费用
schedule            时间、交付、工期
submission          投标须知、递交要求
document_format     投标文件格式、响应表、模板
contract            合同条款
evaluation          评标办法、评分标准
rejection           废标、无效、否决条件
attachment           附件、附录
other               其他
```

`contentType` 只表示内容是什么，不直接决定是否查企业库。

## 4. 主路由

前端主导航只使用以下路由：

```text
project_info        项目信息
company_match       企业能力匹配
rejection_check     否决风险核查
compliance_confirm  合规确认
fill                填写、报价、商务响应
writing_context     标书方案编制上下文
display_only        仅展示参考
```

### 4.1 企业匹配领域

`primaryRoute=company_match` 时填写 `matchDomain`：

```text
technical       技术产品服务
qualification   资质证书
performance     业绩
personnel       人员
financial       财务
credit          信用
region           地域
```

`evidence_match` 不再作为前端平行标签，它由 `primaryRoute=company_match` 表示。

技术专项匹配表示为：

```json
{
  "primaryRoute": "company_match",
  "matchDomain": "technical"
}
```

### 4.2 路由含义

| 路由 | 处理方式 |
|---|---|
| `project_info` | 项目概览、时间提醒、封面预填 |
| `company_match` | 查企业库和企业证据 |
| `rejection_check` | 核查废标、无效、否决风险 |
| `compliance_confirm` | 确认承诺、响应、签字盖章、不得负偏离 |
| `fill` | 填报价、工期、质保期、商务响应和表格字段 |
| `writing_context` | 为技术方案、实施方案、培训方案提供上下文 |
| `display_only` | 只展示，不进入逐条处理队列 |

## 5. 决策影响

```text
hard_gate    硬性门槛
important    重要影响项
normal       普通要求
context      仅作上下文
```

示例：

```text
营业执照要求       company_match / qualification / hard_gate
核心技术指标       company_match / technical / important
投标截止时间       project_info / normal
废标条件           rejection_check / hard_gate
服务方案章节       writing_context / context
```

## 6. 复核状态

`review` 和 `manual_review` 不作为两个并列工作流。

统一使用：

```json
{
  "reviewRequired": true,
  "reviewMode": "manual",
  "reviewReason": "模板章节中包含真实技术要求表格"
}
```

```text
none      不需要复核
auto      系统或 LLM 自动复核
assisted  系统初判后人工确认
manual    必须人工处理
```

前端展示“待复核”或“人工重点复核”，不直接展示内部标签。

## 7. 切块粒度

```text
section     保留章节上下文
clause      拆成可单独核验的条款
table_row   拆成表格行或表格单元格要求
field       抽成项目字段或响应字段
```

默认路由与粒度：

| 路由 | 默认粒度 |
|---|---|
| `project_info` | `field` |
| `company_match` | `clause` 或 `table_row` |
| `rejection_check` | `clause` |
| `compliance_confirm` | `clause` 或 `field` |
| `fill` | `field` 或 `table_row` |
| `writing_context` | `section` |
| `display_only` | `section` |

服务方案默认保留章节上下文。若其中出现明确技术参数，则抽出技术子条款进入 `company_match`。

## 8. 特殊分类规则

### 8.1 `technical + document_format`

不删除多类型命中。

```text
模板说明、签字盖章、响应格式 -> fill 或 compliance_confirm
品目名称 + 技术参数 + 响应情况 -> company_match / technical
```

章节可以是 `document_format` 主类，同时保留 `technical` 在 `typeHits`。

### 8.2 `service_plan`

普通服务方案：

```text
contentType = service_plan
primaryRoute = writing_context
granularity = section
```

服务方案中的明确技术要求：

```text
contentType = technical
primaryRoute = company_match
matchDomain = technical
granularity = clause
secondaryRoutes = ["writing_context"]
```

### 8.3 产品名

技术表格从以下列中提取目标名称：

```text
品目名称
产品名称
标的名称
模块名称
```

条款保存：

```json
{
  "productName": "数字孪生应用实训平台",
  "targetType": "product",
  "sourceColumn": "品目名称"
}
```

## 9. 正则和 LLM

### 9.1 正则负责

正则只直接处理高确定性信号：

```text
废标、无效投标、否决
投标截止时间、最高限价
营业执照、资质、认证
财务报告、审计报告
技术参数、性能指标
报价表、投标函、响应表
表格要求列识别
产品名、品目名提取
标题、表头和模板占位过滤
```

以下词只能作为弱信号：

```text
服务、方案、培训、运维、平台、系统、支持、提供、响应、要求
```

### 9.2 LLM 负责

以下情况触发 LLM：

```text
低置信度
同时命中多个类型
technical + document_format
service_plan + technical
rejection + qualification
长段落混合多个要求
模板表格可能包含真实技术要求
无法确定表格要求列
```

章节级 LLM 输入：

```text
章节标题
章节路径
父级类型
容器类型
子标题
兄弟标题
表头
正文摘要
```

条款级 LLM 输入：

```text
条款原文
所在章节
章节分类
附近标题
表格表头
产品名/品目名
sourceSegmentId
相邻片段
```

LLM 输出必须通过后端护栏：

```text
sourceSegmentId 必须真实存在
不能凭空生成来源
模板本体不能直接进入企业能力匹配
废标信号不能被模型取消
service_plan 不能直接生成企业匹配条款
技术表格必须尽量保留产品名
```

## 10. 前端设计

前端不再根据 `category`、标题或关键词自行判断模块。

主模块：

```text
项目概览
继续投标判断
企业能力匹配
技术产品服务匹配
否决风险
合规确认
填写/报价
方案编制上下文
人工复核
```

前端筛选规则：

```text
企业能力匹配：
  primaryRoute == company_match

技术产品服务：
  primaryRoute == company_match
  && matchDomain == technical

人工复核：
  reviewMode in {"assisted", "manual"}
```

服务方案不进入逐条匹配列表，但在“方案编制上下文”中显示章节入口。

## 11. 后端改造计划

1. 新增统一路由契约模块，集中维护内容类型、主路由、匹配领域、决策影响和粒度。
2. 扩展 `SectionClassification` 和 `ClauseClassification`，保留旧 `usageTags/route` 兼容字段。
3. 扩展 `RequirementFact` 和旧 `fineReview` 行，增加统一规范字段。
4. 将 `ROUTE_POLICIES` 和 `REQUIREMENT_CLASS` 改为由统一路由契约派生。
5. 按路由决定是否做条款切块。
6. 技术表格增加产品名、标的名和来源列字段。
7. LLM 只处理低置信、多类型和冲突项。
8. 前端模块改为消费 `primaryRoute`、`matchDomain`、`reviewMode`。
9. 为旧工作区保留兼容映射：

```text
requirementClass=evidence   -> primaryRoute=company_match
category=technical          -> matchDomain=technical
requirementClass=rejection  -> primaryRoute=rejection_check
requirementClass=compliance -> primaryRoute=compliance_confirm
requirementClass=fill       -> primaryRoute=fill
requirementClass=writing    -> primaryRoute=writing_context
```

## 12. 验收标准

```text
模板本体不进入企业能力匹配
模板中的真实技术要求行进入技术匹配
普通 service_plan 不生成逐条匹配 Requirement
service_plan 中明确技术要求可以单独生成技术子条款
所有技术表格尽量保留产品名/品目名
废标条款全部进入 rejection_check
前端不再通过关键词自行分类
旧 fineReview 结果仍可正常展示
```

## 13. 当前执行记录

### 已完成

- 统一路由契约已落地到 `backend/app/tender_matching/routing_contracts.py`。
- 章节分类和条款归一化均输出 `contentType/typeHits/primaryRoute/matchDomain/reviewMode` 等统一字段，并保留旧字段兼容。
- 技术表格已提取 `productName/targetType/sourceColumn`，合并单元格的产品名会向后续技术行继承。
- 普通 `service_plan` 保留为 `writing_context`，不再逐条进入企业能力匹配；其中明确的技术要求仍可进入 `company_match`。
- 模板本体、合同范本、普通投标须知和评标流程说明不会进入企业能力逐条匹配。
- 明确的废标、无效投标和否决条款统一进入 `rejection_check`。
- P05 样本文档复测结果为：章节 154、条款 119、条款主路由契约通过、模板/否决误入企业匹配 0、技术表格产品名覆盖率 100%。
- 前端匹配页已改为以统一路由字段驱动模块；条款详情展示主路由、内容类型、匹配领域、处理粒度、复核方式、分类来源和采购产品名。

### 正在执行

- 前端浏览器验收：确认历史项目、模块切换、条款点击和详情展开均可用。
- 后端完整测试回归：确认旧工作区、公告粗匹配和完整文件细匹配不回归。
- 更新 P04-P09 的验收记录，区分“结构/路由通过”和“语义准确率尚未有人工 gold”。

### 暂缓待办

- 条款级 LLM 兜底尚未作为默认生产链路启用；当前只保留章节级可选回调和确定性条款分类。
- 需要为高频样本文档补人工 gold，计算章节主类、条款主类和路由召回/精确率。
- 需要继续改进长混合条款的切句；只有在人工复核量和误路由仍明显影响后续工作时再投入。
- 需要补充前端“分类来源”和“复核原因”的批量筛选统计，暂不阻塞当前 P05 验收。

### 当前放行判断

P05 可以作为 P06-P09 的受控输入，但不能把当前结果表述成已证明的语义准确率。后续流程应优先消费 `primaryRoute` 和 `matchDomain`，把 `reviewMode=assisted/manual` 的条款保留给人工复核；只有确定性规则无法稳定判断的复杂条款，才进入后续 LLM 兜底。
