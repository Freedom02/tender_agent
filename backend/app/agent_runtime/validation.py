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
`ArtifactShape` 显式声明（Wave 1 会把它接到 `run_json` 上）。
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
    """

    list_keys: tuple[str, ...] = ()
    item_fields: tuple[str, ...] = ()
    min_items: int = 0
    list_required: bool = True
    allow_empty: bool = True


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
    """

    report = ValidationReport()
    if not isinstance(payload, dict):
        report.schema_invalid("$", f"智能体返回应为对象，实际是 {type(payload).__name__}")
        return report

    if not shape.list_keys:
        return report

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
        return report

    if not isinstance(found_value, list):
        report.schema_invalid(
            found_key,
            f"应为数组，实际是 {type(found_value).__name__}",
        )
        return report

    for index, item in enumerate(found_value):
        item_path = f"{found_key}[{index}]"
        if not isinstance(item, dict):
            report.schema_invalid(item_path, f"列表项应为对象，实际是 {type(item).__name__}")
            continue
        for key in shape.item_fields:
            if key in item:
                continue
            report.schema_invalid(item_path, f"缺少必需字段 {key}")

    if len(found_value) < shape.min_items:
        report.warn(
            "artifact_incomplete",
            found_key,
            f"条目数 {len(found_value)} 少于期望的 {shape.min_items}",
        )
    elif not found_value and not shape.allow_empty:
        report.warn("artifact_incomplete", found_key, "结果为空")

    return report


#: 各智能体产物的期望形态（Wave 1 的调用点直接用）
ARTIFACT_SHAPES: dict[str, ArtifactShape] = {
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
        item_fields=(),
        list_required=False,
    ),
}


def shape_for(artifact: str) -> ArtifactShape | None:
    return ARTIFACT_SHAPES.get(str(artifact or ""))


__all__ = [
    "ARTIFACT_SHAPES",
    "SCHEMA_INVALID",
    "SEVERITY_ERROR",
    "SEVERITY_WARNING",
    "ArtifactShape",
    "ValidationIssue",
    "ValidationReport",
    "as_dict",
    "as_list",
    "resolve_dict",
    "resolve_list",
    "shape_for",
    "validate_payload",
]
