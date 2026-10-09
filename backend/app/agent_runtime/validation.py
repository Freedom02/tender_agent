# -*- coding: utf-8 -*-
"""智能体产物的容错校验（validation）：先归一，再判定，最后才记账。

为什么需要它（架构改造方案 Wave 0 · 0.1 / §3.1）：
  现状是每个调用点自己写 `raw.get("rows") or raw.get("requirementResponses") or []`
  这类兜底，写法各异，且「结构不合法」和「确实没内容」分不清——模型返回
  `{"plan": "..."}`（字符串）时，下游往往静默产出空目录，再在质量闸那里
  以「未生成卷内多级章节」这种与真实原因无关的话报错（已经真实踩过）。

本模块提供三件事：
  1. `ValidationIssue` / `ValidationReport`：把「哪里不合法、严重到什么程度」记下来；
  2. `resolve_list` / `resolve_dict`：按候选键与别名取值，取不到给明确默认值；
  3. `validate_payload`：对「外壳 + 列表项」这种最常见形态做结构校验，
     未知键**一律容忍**（模型多给字段不是错误），只有该有的东西缺失/类型错误
     才产生 `schema_invalid`。

注意：`pi_agent.prompts` 里的 `*_SCHEMA` 常量是给模型看的**示例实例**，
不是 JSON Schema，因此这里不依赖它们做校验；各智能体期望的形态由
`ArtifactShape` 显式声明。

Wave 1 · A 已把形态表接到真实调用点（`pi_agent.transport.run_json`）：
`shape_for_agent()` 按 `specs.AGENT_SPECS[agentId].artifact` 找到形态，
`validate_agent_output()` 出报告，`validation_warnings()` 出人读告警，
`validation_section()` 出返回结构里的 `validation` 段。
**接线只记录、不阻断**：产物不合规不会抛异常，也不会改变任何流程分支。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: 结构不合法的统一问题码（与 run_state.FailureKind.SCHEMA_INVALID 对齐）
SCHEMA_INVALID = "schema_invalid"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    """一条校验问题。"""

    code: str
    path: str
    message: str
    severity: str = SEVERITY_ERROR

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "severity": self.severity,
        }


@dataclass
class ValidationReport:
    """校验结果集合。"""

    issues: list[ValidationIssue] = field(default_factory=list)

    def add(
        self,
        code: str,
        path: str,
        message: str,
        severity: str = SEVERITY_ERROR,
    ) -> ValidationReport:
        self.issues.append(
            ValidationIssue(code=code, path=path, message=message, severity=severity)
        )
        return self

    def schema_invalid(self, path: str, message: str) -> ValidationReport:
        return self.add(SCHEMA_INVALID, path, message)

    def warn(self, code: str, path: str, message: str) -> ValidationReport:
        return self.add(code, path, message, severity=SEVERITY_WARNING)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [item for item in self.issues if item.severity == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def messages(self) -> list[str]:
        return [f"{item.path}: {item.message}" for item in self.issues]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "issues": [item.to_dict() for item in self.issues],
        }


@dataclass(frozen=True)
class ArtifactShape:
    """一个智能体产物的期望形态。

    - ``list_keys``：列表可能挂在哪些键上（按优先级），例如 ``("plan", "outline", "sections")``；
    - ``item_fields``：列表项**必须**带的键；
    - ``min_items``：少于这个数量视为产物不完整（不算结构非法，记 warning）；
    - ``list_required``：列表本身是否必须存在（True 时缺失＝结构非法）。
    - ``allow_empty``：列表为空是否算不完整（仍只是 warning）。

    Wave 1 · A 追加的三个字段（默认值保证 Wave 0 行为逐条不变），用于覆盖
    「对象形态」「文本形态」产物与「标题可能叫 title/heading/name」这类真实差异：

    - ``dict_keys`` / ``dict_required``：对象形态产物（如 ``projectMeta``、``writingPlan``）
      挂在哪几个键上，缺失是否算非法；
    - ``text_keys`` / ``text_required``：文本形态产物（如 ``sectionContent``）挂在哪几个键上；
    - ``item_any_fields``：列表项「至少命中一组」的键，例如 ``(("title", "heading", "name"),)``。
    """

    list_keys: tuple[str, ...] = ()
    item_fields: tuple[str, ...] = ()
    min_items: int = 0
    list_required: bool = True
    allow_empty: bool = True
    dict_keys: tuple[str, ...] = ()
    dict_required: bool = False
    text_keys: tuple[str, ...] = ()
    text_required: bool = False
    item_any_fields: tuple[tuple[str, ...], ...] = ()


def as_dict(value: Any) -> dict[str, Any]:
    """容错取字典：不是字典就返回空字典（绝不抛异常）。"""

    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    """容错取列表：不是列表就返回空列表。"""

    return value if isinstance(value, list) else []


def resolve_list(
    payload: Any,
    keys: tuple[str, ...] | list[str],
    report: ValidationReport | None = None,
    *,
    path: str = "",
) -> list[Any]:
    """按候选键取列表；键存在但类型不对时记 `schema_invalid`。"""

    data = as_dict(payload)
    for key in keys:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, list):
            return value
        if report is not None:
            report.schema_invalid(
                f"{path}.{key}" if path else key,
                f"应为数组，实际是 {type(value).__name__}",
            )
        return []
    return []


def resolve_dict(
    payload: Any,
    keys: tuple[str, ...] | list[str],
    report: ValidationReport | None = None,
    *,
    path: str = "",
) -> dict[str, Any]:
    """按候选键取字典；键存在但类型不对时记 `schema_invalid`。"""

    data = as_dict(payload)
    for key in keys:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, dict):
            return value
        if report is not None:
            report.schema_invalid(
                f"{path}.{key}" if path else key,
                f"应为对象，实际是 {type(value).__name__}",
            )
        return {}
    return {}


def validate_payload(payload: Any, shape: ArtifactShape) -> ValidationReport:
    """按 `ArtifactShape` 校验智能体返回。

    容忍规则（方案 §3.1 明确要求）：
      - 未知键不是错误；
      - 列表项多给的键不是错误；
      - 只有「整体不是对象」「列表缺失/类型错误」「列表项不是对象」
        「必需字段缺失」才记 error（`schema_invalid`）；
      - 「列表为空」默认不算错误（空结果可能是合法的），只按 min_items 记 warning。

    执行顺序固定为：外壳类型 → 列表形态 → 对象/文本形态（记录用）。追加的
    对象/文本形态检查排在原有列表检查之后，因此对只声明了 ``list_keys`` 的
    既有形态，问题集合与顺序与 Wave 0 完全一致。
    """

    report = ValidationReport()
    if not isinstance(payload, dict):
        report.schema_invalid("$", f"智能体返回应为对象，实际是 {type(payload).__name__}")
        return report

    if shape.list_keys:
        _validate_list_shape(payload, shape, report)
    _validate_dict_shape(payload, shape, report)
    _validate_text_shape(payload, shape, report)
    return report


def _validate_list_shape(
    payload: dict[str, Any],
    shape: ArtifactShape,
    report: ValidationReport,
) -> None:
    """列表形态产物（目录、条款、覆盖关系等）的校验。"""

    found_key = ""
    found_value: Any = None
    for key in shape.list_keys:
        if key in payload:
            found_key = key
            found_value = payload[key]
            break

    if not found_key:
        if shape.list_required:
            report.schema_invalid(
                "$",
                f"缺少列表字段（{'/'.join(shape.list_keys)} 至少要有一个）",
            )
        return

    if not isinstance(found_value, list):
        report.schema_invalid(
            found_key,
            f"应为数组，实际是 {type(found_value).__name__}",
        )
        return

    for index, item in enumerate(found_value):
        item_path = f"{found_key}[{index}]"
        if not isinstance(item, dict):
            report.schema_invalid(item_path, f"列表项应为对象，实际是 {type(item).__name__}")
            continue
        for key in shape.item_fields:
            if key in item:
                continue
            report.schema_invalid(item_path, f"缺少必需字段 {key}")
        for group in shape.item_any_fields:
            if any(key in item for key in group):
                continue
            report.schema_invalid(
                item_path,
                f"缺少必需字段（{'/'.join(group)} 至少要有一个）",
            )

    if len(found_value) < shape.min_items:
        report.warn(
            "artifact_incomplete",
            found_key,
            f"条目数 {len(found_value)} 少于期望的 {shape.min_items}",
        )
    elif not found_value and not shape.allow_empty:
        report.warn("artifact_incomplete", found_key, "结果为空")


def _pick_present(payload: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, Any] | None:
    for key in keys:
        if key in payload:
            return key, payload[key]
    return None


def _validate_dict_shape(
    payload: dict[str, Any],
    shape: ArtifactShape,
    report: ValidationReport,
) -> None:
    """对象形态产物（projectMeta、writingPlan 等）。"""

    if not shape.dict_keys:
        return
    found = _pick_present(payload, shape.dict_keys)
    if found is None:
        if shape.dict_required:
            report.schema_invalid(
                "$",
                f"缺少对象字段（{'/'.join(shape.dict_keys)} 至少要有一个）",
            )
        return
    key, value = found
    if not isinstance(value, dict):
        report.schema_invalid(key, f"应为对象，实际是 {type(value).__name__}")


def _validate_text_shape(
    payload: dict[str, Any],
    shape: ArtifactShape,
    report: ValidationReport,
) -> None:
    """文本形态产物（正文、状态字等）。"""

    if not shape.text_keys:
        return
    found = _pick_present(payload, shape.text_keys)
    if found is None:
        if shape.text_required:
            report.schema_invalid(
                "$",
                f"缺少文本字段（{'/'.join(shape.text_keys)} 至少要有一个）",
            )
        return
    key, value = found
    if not isinstance(value, str):
        report.schema_invalid(key, f"应为字符串，实际是 {type(value).__name__}")


#: 各智能体产物的期望形态（Wave 1 的调用点直接用）
ARTIFACT_SHAPES: dict[str, ArtifactShape] = {
    "clauseExtractionV2": ArtifactShape(list_keys=("requirements",), item_fields=("requirementId", "text")),
    "evaluationExtractionV2": ArtifactShape(list_keys=("evaluationRules",), item_fields=("evaluationRuleId", "ruleType")),
    "tableBindingV2": ArtifactShape(list_keys=("responseTableSchemas",), item_fields=("responseTableId", "sourceTableId")),
    "semanticReviewV2": ArtifactShape(list_keys=("findings",), item_fields=("code", "severity", "message"), text_keys=("status",), text_required=True),
    "outlinePlan": ArtifactShape(
        list_keys=("plan", "outline", "sections"),
        item_fields=("title",),
        min_items=1,
    ),
    "volumePlan": ArtifactShape(
        list_keys=("plan", "volumes", "sections"),
        item_fields=("title",),
        min_items=1,
    ),
    "requirementCoverage": ArtifactShape(
        list_keys=("coverage", "mappings", "rows", "items"),
        item_fields=("sectionId", "requirementId"),
    ),
    "requirements": ArtifactShape(
        list_keys=("requirements", "items", "rows"),
        item_fields=("requirement",),
    ),
    "evaluationItems": ArtifactShape(
        list_keys=("evaluationItems", "items", "rows"),
        item_fields=("item",),
        list_required=False,
    ),
    "materialMatches": ArtifactShape(
        list_keys=("matches", "materialMatches", "items"),
        item_fields=("requirementId",),
        list_required=False,
    ),
    "requirementResponseRows": ArtifactShape(
        list_keys=("rows", "requirementResponses", "items"),
        # Wave 1 · A：真实数据（6 个工作区里 41 行响应）里 requirementId 是逐行
        # 回填的唯一锚点，缺它的行下游无法归位，因此提升为关键字段。
        item_fields=("requirementId",),
        list_required=False,
    ),
    # ── Wave 1 · A 追加：补齐 `specs.AGENT_SPECS` 用到的全部 artifact ──────
    #: 对应 prompts.parse.DOCUMENT_STRUCTURE_SCHEMA
    "documentStructure": ArtifactShape(
        list_keys=("sections",),
        item_fields=("sectionId",),
        item_any_fields=(("title", "heading", "name"),),
        min_items=1,
    ),
    #: 对应 prompts.parse.PROJECT_META_SCHEMA（对象形态产物）
    "projectMeta": ArtifactShape(dict_keys=("projectMeta",), dict_required=True),
    #: 对应 prompts.parse.TEMPLATE_SCHEMA（对象形态；submissionStructure 只在出现时查类型）
    "bidFormatSpec": ArtifactShape(dict_keys=("submissionStructure",)),
    #: 对应 prompts.parse.PARSE_REVIEW_SCHEMA（missingFields 是字符串数组，故不入列表项检查）
    "parseReviewReport": ArtifactShape(
        list_keys=("riskItems",),
        item_fields=("title",),
        list_required=False,
        text_keys=("status",),
    ),
    #: 对应 prompts.draft.SECTION_PLAN_SCHEMA（可能是扁平对象，也可能挂在 writingPlan 下）
    "writingPlan": ArtifactShape(
        dict_keys=("writingPlan",),
        list_keys=("requirementResponses", "outline"),
        list_required=False,
        text_keys=("sectionId",),
    ),
    #: 对应 prompts.draft.SECTION_DRAFT_SCHEMA / SECTION_REWRITE_SCHEMA
    "sectionContent": ArtifactShape(
        text_keys=("content", "replacement", "markdown"),
        list_keys=("sourceRefs",),
        list_required=False,
    ),
    #: 对应 prompts.fill.TABLE_FORM_FILL_SCHEMA（只填一种也可以，故列表非必需）
    "templateFragmentFields": ArtifactShape(
        list_keys=("fieldValues", "cellValues"),
        list_required=False,
        text_keys=("templateId", "sectionId"),
    ),
    #: 对应 prompts.review.COMPLIANCE_REVIEW_SCHEMA
    "complianceFindings": ArtifactShape(
        list_keys=("riskItems",),
        item_fields=("title",),
        list_required=False,
        text_keys=("status",),
    ),
}


def shape_for(artifact: str) -> ArtifactShape | None:
    return ARTIFACT_SHAPES.get(str(artifact or ""))


#: 返回结构里承载校验结果的那一段（不要用 result/data/output 这些名字：
#: 编排层的 `_nested_payload` 会把它们当成外壳再拆一层）。
VALIDATION_SECTION = "validation"

#: 人读告警里最多列几处问题（编排层会把每条告警截到 500 字，故必须短）
MAX_WARNING_ISSUES = 6


def shape_for_agent(agent_id: str) -> ArtifactShape | None:
    """按 ``agent_id`` 取产物形态：走 `specs.AGENT_SPECS[agentId].artifact` 对应。

    未登记、未声明 artifact、或该 artifact 还没有形态表时返回 None——
    调用方据此「不校验」，而不是误报。
    """

    from .specs import resolve_spec

    spec = resolve_spec(agent_id)
    if spec is None:
        return None
    return ARTIFACT_SHAPES.get(str(spec.artifact or ""))


def validate_agent_output(agent_id: str, payload: Any) -> ValidationReport:
    """校验某个智能体的返回；形态未知时返回空报告（不制造假问题）。"""

    shape = shape_for_agent(agent_id)
    if shape is None:
        return ValidationReport()
    return validate_payload(payload, shape)


def issue_summary(issue: ValidationIssue) -> str:
    return f"{issue.path} {issue.message}".strip()


def validation_warnings(
    agent_id: str,
    report: ValidationReport,
    *,
    limit: int = MAX_WARNING_ISSUES,
) -> list[str]:
    """把校验问题转成 1~2 条人读告警（供 agentResult.warnings）。

    只描述事实与影响，不写「已阻断」之类的措辞——Wave 1 的接线不阻断任何流程。
    """

    if not report.issues:
        return []
    messages: list[str] = []
    errors = report.errors
    if errors:
        detail = "；".join(issue_summary(item) for item in errors[:limit])
        more = len(errors) - limit
        if more > 0:
            detail += f"；…等共 {len(errors)} 处"
        messages.append(
            f"{agent_id} 返回结构不符合示例 schema（schema_invalid {len(errors)} 处，仅记录不阻断）：{detail}"
        )
    warnings = report.warnings
    if warnings:
        detail = "；".join(issue_summary(item) for item in warnings[:limit])
        more = len(warnings) - limit
        if more > 0:
            detail += f"；…等共 {len(warnings)} 处"
        messages.append(f"{agent_id} 产物不完整（{len(warnings)} 处，仅记录不阻断）：{detail}")
    return messages


def validation_section(agent_id: str, report: ValidationReport) -> dict[str, Any]:
    """返回结构里的 ``validation`` 段（JSON 可序列化，便于落进 agentResults）。"""

    from .specs import resolve_spec

    spec = resolve_spec(agent_id)
    shape = shape_for_agent(agent_id)
    return {
        "agentId": agent_id,
        "artifact": spec.artifact if spec is not None else "",
        "checked": shape is not None,
        #: 明确写死模式，避免下游误以为「ok=false 就会被拦下」
        "mode": "record_only",
        "ok": report.ok,
        "errorCount": len(report.errors),
        "warningCount": len(report.warnings),
        "issues": [item.to_dict() for item in report.issues],
    }


__all__ = [
    "ARTIFACT_SHAPES",
    "MAX_WARNING_ISSUES",
    "SCHEMA_INVALID",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "VALIDATION_SECTION",
    "ArtifactShape",
    "ValidationIssue",
    "ValidationReport",
    "as_dict",
    "as_list",
    "issue_summary",
    "resolve_dict",
    "resolve_list",
    "shape_for",
    "shape_for_agent",
    "validate_agent_output",
    "validate_payload",
    "validation_section",
    "validation_warnings",
]
