"""智能体提取桥（Agent Extraction Bridge）。

把"外部智能体（WorkBuddy 等）"作为**提取层的一个可插拔 Provider**接入：

    招标文件
      -> 解析层  document_parser / document_structure   （不经过本模块）
      -> 提取层  document_fine_matching                  （本模块在这里插入）
      -> 匹配层  document_matching + M3 本体库            （不经过本模块）

设计原则（与 `docs/解析与匹配模块边界.md` 一致）：

1. 本模块只产出 `RequirementExtraction.requirements[]`，不做企业资料匹配、不读 M3。
2. 智能体结果与规则结果由调用方（提取层）合并，本模块不改变合并语义。
3. 桥不可用 / 超时 / 结果不合法时统一返回 `None`，由调用方回退到内置模型或规则通道，
   保证"智能体离线时系统仍可用"。
4. 支持两种传输方式：
   - `file`：文件收件箱（runtime/agent_bridge/inbox -> outbox），异步、抗超时、可人工复核；
   - `http`：本机智能体 HTTP 服务，同步、低延迟。

配置写在 `agent-config.json` 的 `ai.agentBridge` 段，例如：

    "ai": {
      "...": "...",
      "agentBridge": {
        "enabled": true,
        "transport": "file",
        "runtimeDir": "runtime/agent_bridge",
        "httpEndpoint": "http://127.0.0.1:8911",
        "timeoutSeconds": 90,
        "pollIntervalSeconds": 2
      }
    }
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

from .contracts import CATEGORY_MODULES, EVIDENCE_NEEDS, RESPONSE_TYPES

SCHEMA_VERSION = "1.0"

# 与 document_fine_matching.CATEGORY_LABELS 保持同一枚举（此处显式声明以避免循环导入）。
AGENT_CATEGORIES = (
    "qualification",
    "compliance",
    "rejection",
    "performance",
    "personnel",
    "financial",
    "credit",
    "region",
    "commercial",
    "pricing",
    "contract",
    "service_plan",
    "document",
    "technical",
    "other",
)

AGENT_CATEGORY_LABELS = {
    "qualification": "资格审查",
    "compliance": "符合性审查",
    "rejection": "否决条款",
    "performance": "业绩要求",
    "personnel": "人员要求",
    "financial": "财务要求",
    "credit": "信用要求",
    "region": "地域要求",
    "commercial": "商务要求",
    "pricing": "报价要求",
    "contract": "合同条款",
    "service_plan": "服务方案要求",
    "document": "文件格式",
    "technical": "技术要求",
    "other": "其他要求",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "transport": "file",
    "runtimeDir": "runtime/agent_bridge",
    "httpEndpoint": "http://127.0.0.1:8911",
    "timeoutSeconds": 90,
    "pollIntervalSeconds": 2,
    "maxSegmentsPerJob": 4000,
    "maxTextLengthPerSegment": 4000,
    "requireSegmentLocator": True,
}

PROJECT_ROOT = Path(__file__).resolve().parents[3]


# --------------------------------------------------------------------------- #
# 配置与路径
# --------------------------------------------------------------------------- #
def bridge_config(ai: dict[str, Any] | None) -> dict[str, Any]:
    """合并默认配置与 `ai.agentBridge`，任何缺失字段都用默认值补齐。"""
    raw = (ai or {}).get("agentBridge")
    config = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        config.update({key: value for key, value in raw.items() if value is not None})
    # 兼容最简写法：ai.provider = "workbuddy-agent" 时自动打开文件桥。
    if str((ai or {}).get("provider") or "").strip().lower() in {"workbuddy", "workbuddy-agent", "agent"}:
        config["enabled"] = True
    config["transport"] = str(config.get("transport") or "file").strip().lower()
    try:
        config["timeoutSeconds"] = max(5, min(int(config.get("timeoutSeconds") or 90), 900))
    except (TypeError, ValueError):
        config["timeoutSeconds"] = 90
    try:
        config["pollIntervalSeconds"] = max(1, min(int(config.get("pollIntervalSeconds") or 2), 30))
    except (TypeError, ValueError):
        config["pollIntervalSeconds"] = 2
    return config


def bridge_root(config: dict[str, Any]) -> Path:
    raw = Path(str(config.get("runtimeDir") or DEFAULT_CONFIG["runtimeDir"]))
    return raw if raw.is_absolute() else (PROJECT_ROOT / raw)


def inbox_dir(config: dict[str, Any]) -> Path:
    return bridge_root(config) / "inbox"


def outbox_dir(config: dict[str, Any]) -> Path:
    return bridge_root(config) / "outbox"


def job_path(config: dict[str, Any], job_id: str) -> Path:
    return inbox_dir(config) / f"{job_id}.job.json"


def result_path(config: dict[str, Any], job_id: str) -> Path:
    return outbox_dir(config) / f"{job_id}.result.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _safe_text(value: Any, limit: int = 4000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


# --------------------------------------------------------------------------- #
# 任务包构建
# --------------------------------------------------------------------------- #
def build_instructions() -> str:
    """给智能体的自然语言任务说明；与 outputSchema 一起放进任务包。"""
    return (
        "你是招标文件条款拆解助手。请仅依据任务包 segments 中的原文，把招标文件拆成可以逐条核验的要求。\n"
        "硬性规则：\n"
        "1. 只输出原文中真实存在的要求，不得补写、不得推断、不得合并多条为一条。\n"
        "2. 不要判断投标企业是否满足，不要给投标建议。\n"
        "3. 每条要求只对应一个可核验点，保持原文顺序，尽量拆到最小可核验粒度。\n"
        "4. sourceSegmentId 必须取自 segments[].id，不得自行编造；表格行请带上 tableId/rowIndex。\n"
        "5. 质疑投诉、监督、评标流程、中标人确定方式等流程性内容不要提取。\n"
        "6. 带“★”“废标”“投标无效”“否决”等字样的条款，mandatory=true 且 isRejection=true。\n"
        "7. rawText 保留原文措辞；normalizedRequirement 写标准化后的可核验表述。\n"
        "8. 输出严格 JSON（不要 Markdown 代码块），结构见任务包 outputSchema。"
    )


def build_output_schema() -> dict[str, Any]:
    properties = {
        "id": {"type": "string"},
        "category": {"type": "string", "enum": list(AGENT_CATEGORIES)},
        "rawText": {"type": "string"},
        "normalizedRequirement": {"type": "string"},
        "section": {"type": "string"},
        "sourceSegmentId": {"type": "string"},
        "sourceSegmentIds": {"type": "array", "items": {"type": "string"}},
        "sourceLocator": {"type": "string"},
        "mandatory": {"type": "boolean"},
        "isRejection": {"type": "boolean"},
        "evidenceNeed": {"type": "string", "enum": sorted(set(EVIDENCE_NEEDS.values()))},
        "responseType": {"type": "string", "enum": sorted(set(RESPONSE_TYPES.values()))},
        "quantity": {"type": ["number", "null"]},
        "timeLimit": {"type": "string"},
        "confidence": {"type": "number"},
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schemaVersion": {"type": "string"},
            "jobId": {"type": "string"},
            "modelUsed": {"type": "string"},
            "requirements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": properties,
                    "required": [
                        "id",
                        "category",
                        "rawText",
                        "section",
                        "sourceSegmentId",
                        "sourceSegmentIds",
                        "mandatory",
                        "isRejection",
                        "confidence",
                    ],
                },
            },
            "warnings": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["requirements"],
    }


def build_job(
    *,
    source_file_id: str,
    segments: list[dict[str, Any]],
    document_meta: dict[str, Any] | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """把解析层的 `segments` 打包成一份智能体任务包。"""
    resolved = config or dict(DEFAULT_CONFIG)
    text_limit = int(resolved.get("maxTextLengthPerSegment") or 4000)
    max_segments = int(resolved.get("maxSegmentsPerJob") or 4000)

    packed: list[dict[str, Any]] = []
    for index, segment in enumerate(segments[:max_segments], start=1):
        segment_id = _safe_text(segment.get("id"), 80)
        text = _safe_text(segment.get("text"), text_limit)
        if not segment_id or not text:
            continue
        item: dict[str, Any] = {
            "id": segment_id,
            "order": segment.get("order", index),
            "contentType": _safe_text(segment.get("contentType") or "paragraph", 40),
            "text": text,
        }
        for key in ("section", "heading", "sectionId", "tableId", "rowIndex", "cells"):
            value = segment.get(key)
            if value not in (None, "", [], {}):
                item[key] = value
        packed.append(item)

    job_id = _job_id(source_file_id, packed)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "jobId": job_id,
        "createdAt": _now_iso(),
        "document": {
            "sourceFileId": source_file_id,
            **_normalize_meta(document_meta or {}),
            "segmentCount": len(packed),
            "charCount": sum(len(item["text"]) for item in packed),
        },
        "instructions": build_instructions(),
        "hints": {
            "categoryLabels": AGENT_CATEGORY_LABELS,
            "categoryEnum": list(AGENT_CATEGORIES),
            "evidenceNeedEnum": sorted(set(EVIDENCE_NEEDS.values())),
            "responseTypeEnum": sorted(set(RESPONSE_TYPES.values())),
        },
        "outputSchema": build_output_schema(),
        "segments": packed,
    }


def _normalize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    allowed = ("name", "fileName", "title", "projectId", "projectName", "documentKind")
    return {key: _safe_text(meta.get(key), 200) for key in allowed if _safe_text(meta.get(key), 200)}


def _job_id(source_file_id: str, segments: list[dict[str, Any]]) -> str:
    seed = f"{source_file_id}|{len(segments)}|{sum(len(item['text']) for item in segments)}"
    sanitized = re.sub(r"[^0-9A-Za-z]+", "-", seed).strip("-")[:40] or "tender"
    return f"JOB-{sanitized}-{abs(hash(seed)) % 100000:05d}"


# --------------------------------------------------------------------------- #
# 传输：文件收件箱
# --------------------------------------------------------------------------- #
def submit_job_file(config: dict[str, Any], job: dict[str, Any]) -> None:
    directory = inbox_dir(config)
    directory.mkdir(parents=True, exist_ok=True)
    target = job_path(config, job["jobId"])
    if target.exists():
        return
    temp = target.with_suffix(".json.tmp")
    temp.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)


def read_result_file(config: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    path = result_path(config, job_id)
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def wait_for_result_file(config: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    deadline = time.monotonic() + float(config.get("timeoutSeconds") or 90)
    interval = float(config.get("pollIntervalSeconds") or 2)
    while time.monotonic() < deadline:
        result = read_result_file(config, job_id)
        if result is not None:
            return result
        time.sleep(interval)
    return read_result_file(config, job_id)


# --------------------------------------------------------------------------- #
# 传输：本机智能体 HTTP 服务
# --------------------------------------------------------------------------- #
def request_extraction_http(config: dict[str, Any], job: dict[str, Any]) -> dict[str, Any] | None:
    endpoint = str(config.get("httpEndpoint") or "").rstrip("/")
    if not endpoint:
        return None
    payload = json.dumps({"action": "extract_requirements", "job": job}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        f"{endpoint}/extract",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=float(config.get("timeoutSeconds") or 90)) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (error.HTTPError, error.URLError, TimeoutError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if isinstance(body, dict) and isinstance(body.get("result"), dict):
        return body["result"]
    return body if isinstance(body, dict) else None


# --------------------------------------------------------------------------- #
# 结果归一化
# --------------------------------------------------------------------------- #
def normalize_agent_result(
    raw: dict[str, Any] | None,
    available_segments: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    """把智能体返回的 JSON 归一成提取层内部 requirement 结构。

    与 `document_fine_matching._normalize_model_extraction` 的输出字段保持一致，
    这样匹配层（`document_matching`）无需任何改动即可消费。
    """
    if not isinstance(raw, dict):
        return None
    items = raw.get("requirements")
    if not isinstance(items, list) or not items:
        return None

    warnings: list[str] = []
    raw_warnings = raw.get("warnings")
    if isinstance(raw_warnings, list):
        warnings.extend(_safe_text(value, 200) for value in raw_warnings if _safe_text(value, 200))

    requirements: list[dict[str, Any]] = []
    dropped = 0
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            dropped += 1
            continue
        text = _safe_text(item.get("rawText") or item.get("text") or item.get("requirement"), 1200)
        if not text:
            dropped += 1
            continue

        category = _safe_text(item.get("category") or "other", 40).lower()
        if category not in AGENT_CATEGORIES:
            warnings.append(f"第 {index} 条要求分类“{category}”不在允许枚举内，已归入 other。")
            category = "other"

        source_ids = [
            _safe_text(value, 120)
            for value in (item.get("sourceSegmentIds") or [item.get("sourceSegmentId")])
            if _safe_text(value, 120) and _safe_text(value, 120) in available_segments
        ]
        source_ids = list(dict.fromkeys(source_ids))
        if not source_ids:
            # 摘不到真实片段编号的要求会破坏"可回溯到原文"的契约，直接丢弃。
            dropped += 1
            continue

        primary = available_segments[source_ids[0]]
        locator = _safe_text(item.get("sourceLocator") or primary.get("sourceLocator"), 120)
        section = _safe_text(item.get("section") or primary.get("section") or primary.get("heading"), 160)

        evidence_need = _safe_text(item.get("evidenceNeed"), 80)
        if evidence_need and evidence_need not in set(EVIDENCE_NEEDS.values()):
            evidence_need = ""
        response_type = _safe_text(item.get("responseType"), 80)
        if response_type and response_type not in set(RESPONSE_TYPES.values()):
            response_type = ""

        try:
            confidence = float(item.get("confidence"))
        except (TypeError, ValueError):
            confidence = 0.9
        confidence = min(max(confidence, 0.0), 1.0)

        requirements.append(
            {
                "id": _safe_text(item.get("id") or f"agent-req-{index:03d}", 60),
                "category": category,
                "text": text,
                "rawText": text,
                "normalizedRequirement": _safe_text(item.get("normalizedRequirement") or text, 1200),
                "section": section,
                "sourceLocator": locator,
                "sourceSegmentId": source_ids[0],
                "sourceSegmentIds": source_ids,
                "mandatory": bool(item.get("mandatory", True)),
                "isRejection": bool(item.get("isRejection", category == "rejection")),
                "quantity": item.get("quantity") if isinstance(item.get("quantity"), (int, float)) else None,
                "timeLimit": _safe_text(item.get("timeLimit"), 160),
                "evidenceNeed": evidence_need,
                "responseType": response_type,
                "confidence": confidence,
                "extractionSource": "agent",
            }
        )

    if dropped:
        warnings.append(f"有 {dropped} 条智能体结果因缺少有效原文定位或正文为空被丢弃。")
    if not requirements:
        return None
    return {"requirements": requirements, "warnings": warnings}


def _available_segments(segments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(segment.get("id")): segment
        for segment in segments
        if str(segment.get("id") or "")
    }


# --------------------------------------------------------------------------- #
# 对外入口
# --------------------------------------------------------------------------- #
def run_agent_bridge_extraction(
    segments: list[dict[str, Any]],
    ai: dict[str, Any] | None,
    *,
    source_file_id: str = "",
    document_meta: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """尝试用智能体桥完成提取。

    返回值为提取层内部结构；任何环节不可用时返回 `None`，由调用方回退。
    """
    config = bridge_config(ai)
    if not config.get("enabled"):
        return None
    if not segments:
        return None

    available = _available_segments(segments)
    job = build_job(
        source_file_id=source_file_id,
        segments=segments,
        document_meta=document_meta,
        config=config,
    )
    if not job["segments"]:
        return None

    if config["transport"] == "http":
        raw_result = request_extraction_http(config, job)
    else:
        job_id = job["jobId"]
        # 快路径：结果已经由智能体在上一轮写好，直接复用，不阻塞。
        raw_result = read_result_file(config, job_id)
        if raw_result is None:
            submit_job_file(config, job)
            raw_result = wait_for_result_file(config, job_id)

    normalized = normalize_agent_result(raw_result, available)
    if normalized is None:
        return None

    requirements = normalized["requirements"]
    sent_segment_ids = {
        _safe_text(segment.get("id"), 120)
        for segment in job["segments"]
        if _safe_text(segment.get("id"), 120)
    }
    reported_segment_ids = {
        str(value)
        for requirement in requirements
        for value in requirement.get("sourceSegmentIds", [])
        if str(value)
    }
    return {
        "requirements": requirements,
        "warnings": normalized["warnings"][:8],
        "modelUsed": _safe_text((raw_result or {}).get("modelUsed") or "workbuddy-agent", 80),
        "batchCount": 1,
        "failedBatchCount": 0,
        "sentSegmentIds": sent_segment_ids,
        "reportedSegmentIds": reported_segment_ids,
        "jobId": job["jobId"],
        "transport": config["transport"],
    }
