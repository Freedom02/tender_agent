"""Contracts and handoff helpers for the autonomous tender-review agent.

This package keeps the WorkBuddy runtime and its matching adapter in one
explicit boundary.  The builtin parser and fine-review path remain separate;
the Store chooses the route from ``ai.extractionMode``.
"""

from .schemas import (
    AGENT_OUTPUT_CONTRACT_VERSION,
    AGENT_RUN_SCHEMA_VERSION,
    AGENT_SKILL_VERSION,
    AGENT_TOOL_POLICY_VERSION,
    WRITING_BRIEF_SCHEMA_VERSION,
    AgentCheckpoint,
    AgentRun,
    FeedbackEvent,
    SectionOverlay,
    ToolCall,
    WritingBrief,
)
from .handoff import (
    build_agent_review_input,
    build_writing_brief,
    project_agent_result_to_fine_review,
)
from .runtime import (
    AGENT_RUNTIME_VERSION,
    AgentExecutionEnvelope,
    DocumentMapAgentRuntime,
    DocumentMapAgentTask,
    DocumentMapRunResult,
    DocumentMapRuntimeConfig,
    WorkBuddySdkExecutor,
)
from .integration import (
    WorkBuddyAgentMatchingError,
    build_review_output_from_analysis_pack,
    evaluate_tender_document_with_workbuddy_agent,
)

__all__ = [
    "AGENT_OUTPUT_CONTRACT_VERSION",
    "AGENT_RUN_SCHEMA_VERSION",
    "AGENT_SKILL_VERSION",
    "AGENT_TOOL_POLICY_VERSION",
    "WRITING_BRIEF_SCHEMA_VERSION",
    "AgentCheckpoint",
    "AgentRun",
    "FeedbackEvent",
    "SectionOverlay",
    "ToolCall",
    "WritingBrief",
    "build_agent_review_input",
    "build_writing_brief",
    "project_agent_result_to_fine_review",
    "AGENT_RUNTIME_VERSION",
    "AgentExecutionEnvelope",
    "DocumentMapAgentRuntime",
    "DocumentMapAgentTask",
    "DocumentMapRunResult",
    "DocumentMapRuntimeConfig",
    "WorkBuddySdkExecutor",
    "WorkBuddyAgentMatchingError",
    "build_review_output_from_analysis_pack",
    "evaluate_tender_document_with_workbuddy_agent",
]
