# -*- coding: utf-8 -*-
"""运行态契约（RunState / NodeRun）：让智能体执行「可记录、可重试、可恢复、可幂等」。

为什么需要它（架构改造方案 Wave 0 · 0.1）：
  现在一次生成的记账只到 `agentResults`（每个 agent 一条成功/失败记录），
  没有「这次运行的哪个节点重试过、卡在哪、下次能不能跳过」的概念。
  于是：一整卷生成失败只能整卷重来；相同输入重复跑会重复花钱；
  前端也没法显示「28 个章节里哪些已经写完」。

本模块只定义**数据结构与纯函数**，不接线任何执行路径；Wave 1 的编排改造会用它。
设计约束（来自方案 §3.2）：
  - 每次运行有 runId，每个节点有 nodeId + attempt，失败必须带 failureKind；
  - 幂等键 = (projectId, workspaceId, stage, nodeId, inputDigest)，
    输入摘要不变时可直接复用上次成功产物；
  - 状态是**事实记录**，不允许出现「状态成功但产物为空」这种自相矛盾的组合，
    由 `NodeRun.artifact_status` 与 `NodeRun.status` 分开表达。
"""
from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import now_iso, new_id


class NodeStatus(str, Enum):
    """节点执行状态。"""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"
    REUSED = "reused"


class ArtifactStatus(str, Enum):
    """产物状态：与执行状态分开，避免「跑通了但没产出」。"""

    MISSING = "missing"
    PARTIAL = "partial"
    COMPLETE = "complete"


class FieldStatus(str, Enum):
    """单字段填充状态（模板填充、响应表用）。"""

    FILLED = "filled"
    EMPTY = "empty"
    UNRESOLVED = "unresolved"
    SKIPPED = "skipped"


class ResponseRowStatus(str, Enum):
    """逐条响应行状态。"""

    MATCHED = "matched"
    UNMATCHED = "unmatched"
    NEEDS_REVIEW = "needs_review"


class EvidenceAssetStatus(str, Enum):
    """证据件状态（业绩、资质、检测报告等）。"""

    AVAILABLE = "available"
    MISSING = "missing"
    EXPIRED = "expired"
    UNVERIFIED = "unverified"


class FailureKind(str, Enum):
    """失败归类：决定重试策略与是否需要人工介入。"""

    POLICY_BLOCKED = "policy_blocked"  # provider/输入策略拦截（不可重试，应走回退）
    SCHEMA_INVALID = "schema_invalid"  # 模型返回结构不合法（可重试，通常加严提示词）
    TIMEOUT = "timeout"  # 超时（可重试）
    PROVIDER_ERROR = "provider_error"  # 通道/网关错误（可重试，退避）
    TOOL_ERROR = "tool_error"  # 工具调用失败（看工具类型决定）
    QUALITY_GATE = "quality_gate"  # 产出质量未过闸（可带反馈重试一次）
    PARSE_ERROR = "parse_error"  # 输入不可解析（不可重试，需人工）
    BUDGET_EXCEEDED = "budget_exceeded"  # 成本/时长超限（不可重试）
    UNKNOWN = "unknown"


#: 可自动重试的失败类型（Wave 1 的重试策略读它）
RETRYABLE_FAILURES: frozenset[str] = frozenset(
    {
        FailureKind.SCHEMA_INVALID.value,
        FailureKind.TIMEOUT.value,
        FailureKind.PROVIDER_ERROR.value,
        FailureKind.TOOL_ERROR.value,
        FailureKind.QUALITY_GATE.value,
    }
)


class NodeRun(BaseModel):
    """一次运行中某个节点的执行记录。"""

    model_config = ConfigDict(extra="allow")

    nodeId: str
    agentId: str
    stage: str = ""
    status: NodeStatus = NodeStatus.PENDING
    artifactStatus: ArtifactStatus = ArtifactStatus.MISSING
    attempt: int = 1
    inputRefs: list[str] = Field(default_factory=list)
    outputRefs: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    failureKind: str = ""
    failureMessage: str = ""
    startedAt: str = ""
    finishedAt: str = ""
    #: 复用来源：命中幂等复用时记录上一次的 runId
    reusedFrom: str = ""
    #: 输入摘要：幂等判定的依据（由 input_digest 生成，调用方负责传入稳定输入）
    inputDigest: str = ""

    @property
    def retryable(self) -> bool:
        return self.status is NodeStatus.FAILED and self.failureKind in RETRYABLE_FAILURES

    def mark_running(self, *, at: str | None = None) -> "NodeRun":
        self.status = NodeStatus.RUNNING
        self.startedAt = at or now_iso()
        return self

    def mark_succeeded(
        self, *, artifact_status: ArtifactStatus = ArtifactStatus.COMPLETE, at: str | None = None
    ) -> "NodeRun":
        self.status = NodeStatus.SUCCEEDED
        self.artifactStatus = artifact_status
        self.finishedAt = at or now_iso()
        self.failureKind = ""
        self.failureMessage = ""
        return self

    def mark_failed(
        self,
        kind: FailureKind | str,
        message: str,
        *,
        artifact_status: ArtifactStatus | None = None,
        at: str | None = None,
    ) -> "NodeRun":
        self.status = NodeStatus.FAILED
        self.failureKind = kind.value if isinstance(kind, FailureKind) else str(kind)
        self.failureMessage = str(message)[:500]
        if artifact_status is not None:
            self.artifactStatus = artifact_status
        self.finishedAt = at or now_iso()
        return self

    def mark_skipped(self, reason: str = "") -> "NodeRun":
        self.status = NodeStatus.SKIPPED
        if reason:
            self.warnings.append(str(reason)[:500])
        return self

    def mark_reused(self, *, from_run: str) -> "NodeRun":
        self.status = NodeStatus.REUSED
        self.artifactStatus = ArtifactStatus.COMPLETE
        self.reusedFrom = from_run
        return self


class RunState(BaseModel):
    """一次生成/解析运行的完整状态记录。"""

    model_config = ConfigDict(extra="allow")

    runId: str = Field(default_factory=lambda: new_id("run"))
    projectId: str = ""
    workspaceId: str = ""
    stage: str = ""
    status: NodeStatus = NodeStatus.PENDING
    nodes: list[NodeRun] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    startedAt: str = Field(default_factory=now_iso)
    finishedAt: str = ""
    schemaVersion: str = "1.0"

    # ── 读取 ────────────────────────────────────────────────────────
    def node(self, node_id: str) -> NodeRun | None:
        for item in self.nodes:
            if item.nodeId == node_id:
                return item
        return None

    def pending_nodes(self) -> list[NodeRun]:
        return [item for item in self.nodes if item.status is NodeStatus.PENDING]

    def failed_nodes(self) -> list[NodeRun]:
        return [item for item in self.nodes if item.status is NodeStatus.FAILED]

    def retryable_nodes(self) -> list[NodeRun]:
        return [item for item in self.nodes if item.retryable]

    def completed_count(self) -> int:
        done = {NodeStatus.SUCCEEDED, NodeStatus.REUSED, NodeStatus.SKIPPED}
        return sum(1 for item in self.nodes if item.status in done)

    def progress(self) -> float:
        if not self.nodes:
            return 0.0
        return round(self.completed_count() / len(self.nodes), 4)

    # ── 写入 ────────────────────────────────────────────────────────
    def upsert_node(self, node: NodeRun) -> NodeRun:
        """按 nodeId 记录节点；重复记录时保留已有记录并更新（不产生重复行）。"""

        for index, item in enumerate(self.nodes):
            if item.nodeId == node.nodeId:
                self.nodes[index] = node
                return node
        self.nodes.append(node)
        return node

    def begin_node(
        self,
        *,
        node_id: str,
        agent_id: str,
        stage: str = "",
        input_refs: list[str] | None = None,
        input_digest: str = "",
    ) -> NodeRun:
        existing = self.node(node_id)
        attempt = (existing.attempt + 1) if existing is not None else 1
        node = NodeRun(
            nodeId=node_id,
            agentId=agent_id,
            stage=stage or self.stage,
            attempt=attempt,
            inputRefs=list(input_refs or []),
            inputDigest=input_digest,
        )
        return self.upsert_node(node.mark_running())

    def finish(
        self,
        *,
        status: NodeStatus | None = None,
        at: str | None = None,
    ) -> "RunState":
        """收尾。显式传 status 时以调用方为准，否则按节点事实推断。

        推断规则：有失败节点，或还有节点停在 pending/running（说明中途断了），
        都算 FAILED；只有全部节点都落在「成功/复用/跳过」这类终结态才算 SUCCEEDED。
        """

        if status is not None:
            self.status = status
        elif any(
            item.status in {NodeStatus.FAILED, NodeStatus.PENDING, NodeStatus.RUNNING}
            for item in self.nodes
        ):
            self.status = NodeStatus.FAILED
        else:
            self.status = NodeStatus.SUCCEEDED
        self.finishedAt = at or now_iso()
        return self

    def to_summary(self) -> dict[str, Any]:
        return {
            "runId": self.runId,
            "projectId": self.projectId,
            "workspaceId": self.workspaceId,
            "stage": self.stage,
            "status": self.status.value,
            "total": len(self.nodes),
            "completed": self.completed_count(),
            "failed": len(self.failed_nodes()),
            "retryable": len(self.retryable_nodes()),
            "progress": self.progress(),
            "startedAt": self.startedAt,
            "finishedAt": self.finishedAt,
        }


def input_digest(*parts: Any) -> str:
    """稳定输入摘要：幂等键的组成部分。

    只对**结构性输入**取摘要（例如 ParseBundle 的相关字段、章节 ID、指令文本），
    不要传入带时间戳或随机 ID 的对象，否则摘要每次都变、幂等失效。
    """

    hasher = hashlib.sha256()
    for part in parts:
        if isinstance(part, (dict, list, tuple)):
            try:
                text = json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
            except (TypeError, ValueError):
                text = repr(part)
        else:
            text = str(part or "")
        hasher.update(text.encode("utf-8"))
        hasher.update(b"\x1f")
    return hasher.hexdigest()[:16]


def idempotency_key(
    *,
    project_id: str,
    workspace_id: str,
    stage: str,
    node_id: str,
    digest: str,
) -> str:
    """幂等键：同一项目/工作区/阶段/节点 + 同一输入摘要 → 同一键。"""

    return "|".join(
        [
            str(project_id or ""),
            str(workspace_id or ""),
            str(stage or ""),
            str(node_id or ""),
            str(digest or ""),
        ]
    )


def should_reuse(
    previous: NodeRun | None,
    *,
    digest: str,
) -> bool:
    """判断能否复用上次成功产物（幂等复用的唯一判据）。"""

    if previous is None:
        return False
    if previous.status not in {NodeStatus.SUCCEEDED, NodeStatus.REUSED}:
        return False
    if previous.artifactStatus is not ArtifactStatus.COMPLETE:
        return False
    if not digest or previous.inputDigest != digest:
        return False
    return True


__all__ = [
    "RETRYABLE_FAILURES",
    "ArtifactStatus",
    "EvidenceAssetStatus",
    "FailureKind",
    "FieldStatus",
    "NodeRun",
    "NodeStatus",
    "ResponseRowStatus",
    "RunState",
    "idempotency_key",
    "input_digest",
    "should_reuse",
]
