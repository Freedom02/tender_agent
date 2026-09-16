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

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import error, request

from .contracts import (
    CATEGORY_MODULES,
    EVIDENCE_NEEDS,
    RESPONSE_TYPES,
    SECTION_JUDGE_EXCLUDE,
    SECTION_JUDGE_INCLUDE,
    build_section_menu,
)

SCHEMA_VERSION = "1.0"

# 任务包「契约版本」：instructions / outputSchema / sectionMenu 的语义版本。
# 改动其中任何一处都必须 +1 —— 它进 `_job_id` 的种子，是"旧结果自动失效"的开关。
# 不 +1 的后果：改了提示词却一直命中按旧契约跑出的结果，外观上"改了像没改"（见 _job_id）。
#   1.0 → 初版（requirements + hints）
#   1.1 → P0 并轨：任务包带 sectionMenu + sectionInstructions，输出带 sections/sectionJudgement
INSTRUCTION_VERSION = "1.1"

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
    # Sidecar 心跳闸门：没有活着的心跳就不等待结果（但仍投递任务包）。
    "requireSidecarHeartbeat": True,
    "heartbeatMaxAgeSeconds": 60,
    # 提取产物落盘根目录（相对 PROJECT_ROOT）；每个项目落在 <packDir>/<projectId>/pack/。
    "packDir": "runtime/projects",
    "writePack": True,
}

PACK_SCHEMA_VERSION = "tender-pack/1.0"

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


def heartbeat_path(config: dict[str, Any]) -> Path:
    return bridge_root(config) / "sidecar.heartbeat.json"


def sidecar_status(config: dict[str, Any], *, max_age_seconds: int = 60) -> dict[str, Any] | None:
    """读 Sidecar 心跳；返回 None 表示"没有活着的 Sidecar"。

    为什么必须有这一步
    ------------------
    文件桥的工作方式是「投递 → 轮询等待结果」。如果 Sidecar 没在跑，这个等待会
    走满 `timeoutSeconds` 才回退 —— 也就是**每次分析都白等一轮**，接口看起来就是
    "卡住然后给了个旧口径的结果"。运维上极难归因：日志里没有任何错误。

    所以投递前先看心跳：没有心跳就**不等待**（但仍投递，留给下次），立刻回退。
    判据用文件 mtime 而不是文件内容，避免时钟格式/时区差异带来的误判。
    """
    path = heartbeat_path(config)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age > max(5, int(max_age_seconds)):
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


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


def build_section_instructions() -> str:
    """章节判断说明（P0 并轨）——**只在第 1 批**下发，不放进 `build_instructions()`。

    ★ 为什么不并进主 instructions
    ----------------------------
    主 instructions 每批都送。章节判断是**全文档级**的一次性判断（菜单本身就含
    全部章节路径），每批重复送菜单等于把 4800 tok 的菜单乘以批次数（1371 段的文件
    是 37 批 ⇒ 177k tok），比它替换掉的那次独立调用贵 37 倍 —— 那就不是并轨，是
    把一次小开销换成一个大开销。

    所以：菜单与这段说明只随**第 1 批**下发，其余批次连"该输出 sections"都不提。
    判断规则与本地 LLM 那次调用**同源**（`contracts.SECTION_JUDGE_*`），保证两个
    判断者的判据逐字一致。
    """
    return (
        "=== 本章节判断（只在第 1 批要求，与其他批次的要求提取是同一件事）===\n"
        "任务包 `sectionMenu` 是这份文件的**完整章节清单**，格式为"
        "`[章节ID] 完整章节路径 | 段落数`。\n"
        "请从中挑出【包含需要投标人满足或响应的具体条件的章节】："
        f"{SECTION_JUDGE_INCLUDE}。\n"
        f"排除流程性章节：{SECTION_JUDGE_EXCLUDE}。\n"
        "把结果放进本次返回 JSON 的顶层 `sections`（章节ID 数组），"
        "并在 `sectionJudgement` 写一句判断依据。\n"
        "只依据 `sectionMenu` 里列出的章节路径判断，**不得编造不存在的章节ID**"
        "（编造的 ID 会被剔除，并且会让整个章节判断失效）。\n"
        "段落数少且路径像流程名的章，通常不是要求章。\n"
        "注意：`sections` 只是结构判断，**不影响**要求提取 —— "
        "本批片段里的要求仍要逐条提取，不要因为某章没被选中就跳过它的内容。"
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
            # P0 并轨：章节判断与条目提取由**同一次调用**产出。放在 requirements 之外，
            # 因为它是"结构判断"，不是"要求条目"；消费方也不同（提取层的第一跳）。
            "sections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "判定为“含具体投标要求”的章节ID数组，取值必须来自 sectionMenu",
            },
            "sectionJudgement": {"type": "string", "description": "章节判断依据（一句话）"},
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
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把解析层的 `segments` 打包成一份智能体任务包。

    `sections` / `tables` 是**可选**的，但强烈建议传（提取层本来就持有它们）：

    · `sections` → 给每个片段回填它**最具体的**所属章节路径。解析层只把章节树放在
      `result["sections"]`（每章带 `segmentIds`），**不往 segment 上回填**，于是智能体
      拿到片段时看不到自己属于哪一章，产物也无法重建 `sectionPath`（溯源断链）。
    · `tables`   → 给片段补 `tableRequirementType`。该字段由**提取层**
      `_annotate_table_context()` 才算出并挂在片段上，解析层产物里没有 ⇒ 不补则
      智能体无从判断表格行是不是"要求类"，下游富块也只能全部落到 generic。

    两处补齐都**只在字段缺失时**写入，不覆盖调用方已经标好的值。
    """
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
        # 白名单：既含"智能体判断上下文需要"的字段，也含"产物重建溯源需要"的字段。
        # 早期版本只带 section/heading/sectionId/tableId/rowIndex/cells —— 而
        # `tableRequirementType` / `tableRequirementCellIndex` / `sourceLocator` /
        # `sectionPath` / `sectionType` 都是解析层或提取层**确实挂在片段上**的字段，
        # 被白名单挡在包外 ⇒ 产物里富块全落 generic、溯源只能靠编号反查。
        # 详见关键帧检查脚本 `probe_job_roundtrip.py` 的字段断言。
        for key in (
            "section",
            "heading",
            "sectionId",
            "sectionPath",
            "sectionType",
            "tableId",
            "rowIndex",
            "cells",
            "tableRequirementType",
            "tableRequirementCellIndex",
            "sourceLocator",
        ):
            value = segment.get(key)
            if value not in (None, "", [], {}):
                item[key] = value
        packed.append(item)

    packed = _attach_section_paths(packed, sections)
    packed = _attach_table_types(packed, tables)

    # P0 并轨：章节菜单随任务包一起送去，让智能体**顺手**完成章节判断。
    #
    # 为什么不另开一次调用：智能体手上已经有全量片段（为 jobId 稳定刻意送全量），
    # 早就"看到"了整份文件；而原第一跳只拿到标题菜单、看不到正文。同一份判断，
    # 一个能看正文、一个只能看标题 —— 前者质量必然更高，且是零边际信息成本。
    #
    # 菜单与第一跳喂给本地 LLM 的**同源**（`contracts.build_section_menu`），
    # 保证两个判断者的输入逐字一致、可比（否则"agent 判的章"与"LLM 判的章"
    # 的差异里会混进"菜单格式不同"这个无关变量）。
    section_menu = build_section_menu(sections)

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
        "sectionMenu": section_menu,
        # 章节判断的说明**单独一栏**，由消费方（Sidecar）只在第 1 批下发。
        # 放进 `instructions` 会让每批都带菜单与说明，成本随批次数线性增长（见
        # `build_section_instructions` 的注释）。
        "sectionInstructions": build_section_instructions(),
        "segments": packed,
    }


def _attach_section_paths(
    packed: list[dict[str, Any]],
    sections: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """把章节归属回填到已打包的片段上（只补缺失值）。

    ⚠ `sections[].segmentIds` 是**扁平闭区间**（父章 ⊇ 全部后代），所以一个片段会被
    多个章节包含。取 **`segmentIds` 最短**的那个才是它真正的归属 —— 否则每个片段都会
    被记到根章上，"所属章节"就退化成文档标题。
    """
    if not sections:
        return packed
    best: dict[str, dict[str, Any]] = {}
    for section in sections:
        if not isinstance(section, dict):
            continue
        ids = section.get("segmentIds") or []
        path = [str(value) for value in (section.get("sectionPath") or [])]
        info = {
            "span": len(ids),
            "sectionPath": path,
            "section": "›".join(path),
            "heading": _safe_text(section.get("heading"), 200),
            "sectionId": _safe_text(section.get("sectionId"), 80),
            "sectionType": _safe_text(section.get("sectionType"), 60),
        }
        for segment_id in ids:
            key = str(segment_id)
            current = best.get(key)
            if current is None or info["span"] < current["span"]:
                best[key] = info

    for item in packed:
        info = best.get(str(item.get("id")))
        if not info:
            continue
        for key in ("section", "heading", "sectionId", "sectionPath", "sectionType"):
            if item.get(key) in (None, "", [], {}):
                item[key] = info[key]
    return packed


def _attach_table_types(
    packed: list[dict[str, Any]],
    tables: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """给表格行片段补 `tableRequirementType`（只补缺失值）。

    复用**提取层**的 `_annotate_table_context()`，不另写一套判据 —— 两处判据必然漂移，
    到时"智能体看到的表类型"与"匹配层用的表类型"会对不上，且极难排查。
    这里用函数内延迟导入打破循环导入（`document_fine_matching` 顶层会 import 本模块）。
    """
    if not tables:
        return packed
    try:
        from .document_fine_matching import _annotate_table_context
    except Exception:  # pragma: no cover - 导入失败不应影响主链路
        return packed
    annotated = {
        str(segment.get("id")): segment
        for segment in _annotate_table_context(
            [item for item in packed if isinstance(item, dict)],
            [table for table in tables if isinstance(table, dict)],
        )
    }
    for item in packed:
        source = annotated.get(str(item.get("id")))
        if not source:
            continue
        for key in ("tableRequirementType", "tableRequirementCellIndex"):
            if item.get(key) in (None, "", [], {}):
                item[key] = source.get(key)
    return packed


def _normalize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    allowed = ("name", "fileName", "title", "projectId", "projectName", "documentKind")
    return {key: _safe_text(meta.get(key), 200) for key in allowed if _safe_text(meta.get(key), 200)}


def _job_id(source_file_id: str, segments: list[dict[str, Any]]) -> str:
    """由「契约版本 + 文件标识 + 片段规模 + 正文长度」派生稳定 jobId。

    ⚠ 这里**不能**用内置 `hash()`：CPython 对 str 的 `hash()` 带进程级随机盐
    （PYTHONHASHSEED），同一份文件在两次进程里会得到不同 jobId。实测同一文件
    三次投递分别得到 `…84017` / `…79959` / `…81381` ⇒ `submit_job_file()` 的
    「同名不覆盖」去重完全失效，每次分析都会新建一个任务包，Sidecar 反复跑同一份
    原文，而桥的"快路径复用已有结果"永远命中不了。改用 sha256 后 jobId 跨进程稳定。

    ★ `INSTRUCTION_VERSION` 必须进种子（P0 并轨补的）
    ----------------------------------------------
    原来种子只有"文件内容"三项。后果：改了 `instructions` / `outputSchema` 之后
    jobId **不变** ⇒ 结果文件还在 ⇒ 快路径一直命中**按旧契约跑出来的旧结果**，
    新要求（比如 P0 的 `sections`）永远拿不到。表现是"改完像没改"，而且日志、
    产物、耗时全都正常，极难排查。

    版本进种子后：契约不变 ⇒ 同一份文件仍是同一个 jobId（预热的收益保留）；
    契约一变 ⇒ jobId 自动换 ⇒ 旧结果自然失效、不需要人工 `--force` 重跑。
    """
    seed = (
        f"{INSTRUCTION_VERSION}|{source_file_id}|"
        f"{len(segments)}|{sum(len(item['text']) for item in segments)}"
    )
    sanitized = re.sub(r"[^0-9A-Za-z]+", "-", seed).strip("-")[:40] or "tender"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return f"JOB-{sanitized}-{int(digest[:8], 16) % 100000:05d}"


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


def meta_path(config: dict[str, Any], job_id: str) -> Path:
    return outbox_dir(config) / f"{job_id}.meta.json"


def read_meta_file(config: dict[str, Any], job_id: str) -> dict[str, Any]:
    """读 Sidecar 写的批次诊断（`{jobId}.meta.json`）。

    ⚠ 别把批次数写死成 1。第一版就是这么写的，结果一份跑了 **37 批 / 995 秒** 的任务
    在 stats 里显示 `batchCount=1`，与真实值差 37 倍 —— 而且这种"看起来很正常"的错误
    数字比缺字段更危险：它会让"这次跑得特别慢/特别差"的排查从正确的方向被带开。
    """
    path = meta_path(config, job_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


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
    valid_section_ids: set[str] | None = None,
) -> dict[str, Any] | None:
    """把智能体返回的 JSON 归一成提取层内部 requirement 结构。

    与 `document_fine_matching._normalize_model_extraction` 的输出字段保持一致，
    这样匹配层（`document_matching`）无需任何改动即可消费。

    `valid_section_ids`（P0 并轨新增）：章节菜单里的合法 ID 集合。智能体返回的
    `sections` 会**逐个校验**——编造的 ID 一律剔除并计数上报，与要求条目的
    `sourceSegmentId` 校验同一个口径：**归属不明的判断不进入下游**。
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

    # ---- 章节判断（P0 并轨）------------------------------------------------
    # 放在 requirements 之前处理：即使条目全部被丢弃，章节判断仍可能是有效的，
    # 这层信息对上游（scoped 范围）比条目更上游，不属于"可连带丢弃"的附属品。
    allowed_sections = {str(value) for value in (valid_section_ids or set()) if str(value)}
    raw_sections = raw.get("sections")
    sections: list[str] = []
    fabricated_sections: list[str] = []
    if isinstance(raw_sections, list):
        for value in raw_sections:
            token = _safe_text(value, 40)
            if not token:
                continue
            # `allowed_sections` 为空表示调用方没给菜单（例如旧任务包的结果文件），
            # 此时**不静默放行也不静默拒绝**：保留原值，由调用方决定用不用。
            if allowed_sections and token not in allowed_sections:
                fabricated_sections.append(token)
                continue
            sections.append(token)
        sections = list(dict.fromkeys(sections))
    if fabricated_sections:
        warnings.append(
            f"智能体返回的章节判断里有 {len(fabricated_sections)} 个 ID 不在章节菜单内"
            f"（如 {', '.join(fabricated_sections[:3])}），已剔除。"
        )

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
                # ⚠ 这里必须写 "llm"，不能写 "agent"。
                # `document_fine_matching._merge_requirement_candidates()` 的规则类别校正
                # 只认 {"llm","rule"}：`extractionSource="agent"` 会让校正**整段不生效**
                # ⇒ 智能体通道的类别质量反而低于现有 LLM 通道。
                # 「到底是谁产出的」另用 extractionProvider 表达，两个关注点分开。
                "extractionSource": "llm",
                "extractionProvider": "workbuddy-agent",
            }
        )

    if dropped:
        warnings.append(f"有 {dropped} 条智能体结果因缺少有效原文定位或正文为空被丢弃。")
    if not requirements:
        return None
    return {
        "requirements": requirements,
        "warnings": warnings,
        "sections": sections,
        "sectionJudgement": _safe_text(raw.get("sectionJudgement"), 300),
        "fabricatedSectionCount": len(fabricated_sections),
    }


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
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
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
        sections=sections,
        tables=tables,
    )
    if not job["segments"]:
        return None

    # 结构快照：产物重建溯源（sectionPath / 表类型 / 原始定位）需要它，而任务包只带
    # agent 判断所必需的字段。两者分开放，避免"任务包膨胀"与"产物缺结构"两头不讨好。
    #
    # ⚠ 这里传的是 `job["segments"]`（**打包后的副本**），不是调用方给的原始 segments。
    #   章节归属与表类型是在 build_job 内部回填到打包副本上的，原始片段对象上并没有 ——
    #   传原始片段会得到一份"章节路径全空"的快照，产物里 400 多条要求全部溯源不到章节，
    #   而且**看不出来**（快照生成成功、字段齐全、只是值为空）。
    structure_path = write_structure_snapshot(
        config,
        job,
        sections=sections,
        tables=tables,
        segments=job["segments"],
    )

    job_id = job["jobId"]
    if config["transport"] == "http":
        raw_result = request_extraction_http(config, job)
        sidecar_meta: dict[str, Any] = {}
    else:
        # 快路径：结果已经由智能体在上一轮写好，直接复用，不阻塞。
        raw_result = read_result_file(config, job_id)
        if raw_result is None:
            # 投递**总是**做（任务包留着，Sidecar 下次启动会捡起来）；
            # 但只有在确认 Sidecar 活着时才等待结果，否则立刻回退 —— 见 sidecar_status 注释。
            submit_job_file(config, job)
            if config.get("requireSidecarHeartbeat", True):
                if sidecar_status(
                    config, max_age_seconds=int(config.get("heartbeatMaxAgeSeconds") or 60)
                ) is None:
                    return None
            raw_result = wait_for_result_file(config, job_id)
        # 批次数、耗时、token、护栏漂移都从 Sidecar 的 meta 里取真实值，
        # 不要用"1 批"这种占位值（见 read_meta_file 的注释）。
        meta = read_meta_file(config, job_id)
        raw_sidecar = meta.get("sidecar")
        sidecar_meta = raw_sidecar if isinstance(raw_sidecar, dict) else {}

    normalized = normalize_agent_result(
        raw_result,
        available,
        valid_section_ids={
            str(item.get("sectionId"))
            for item in (sections or [])
            if isinstance(item, dict) and str(item.get("sectionId") or "")
        },
    )
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
    delivered_segment_ids = reported_segment_ids & sent_segment_ids
    batch_detail = [
        item for item in (sidecar_meta.get("batchDetail") or []) if isinstance(item, dict)
    ]
    batch_count = int(sidecar_meta.get("batches") or 0) or len(batch_detail) or 1
    retried_batch_count = sum(
        1 for item in batch_detail if len(item.get("attempts") or []) > 1
    )
    failed_batch_count = sum(
        1
        for item in batch_detail
        if item.get("attempts")
        and not ((item.get("attempts") or [{}])[-1] or {}).get("gotStructured")
    )
    stats = {
        "candidateSegmentCount": len(available),
        "sentSegmentCount": len(sent_segment_ids),
        "reportedSegmentCount": len(delivered_segment_ids),
        "coverageRate": (
            round(len(delivered_segment_ids) * 100 / len(sent_segment_ids))
            if sent_segment_ids
            else 0
        ),
        "modelCoverageRate": (
            round(len(delivered_segment_ids) * 100 / len(sent_segment_ids))
            if sent_segment_ids
            else 0
        ),
        # 智能体通道下"模型引用率"就是"交付覆盖率"（规则通道在此不参与），
        # 保留同名的两个键是为了让下游对比脚本不必分叉。
        "zeroOutputSegmentCount": len(sent_segment_ids - delivered_segment_ids),
        "zeroOutputSegmentIds": sorted(sent_segment_ids - delivered_segment_ids)[:200],
        "batchCount": batch_count,
        "failedBatchCount": failed_batch_count,
        "retryBatchCount": retried_batch_count,
        "ruleRequirementCount": 0,
        "llmRequirementCount": len(requirements),
        "jobId": job_id,
        "transport": config["transport"],
        "structurePath": str(structure_path) if structure_path else "",
        "extractionProvider": "workbuddy-agent",
        # ---- Sidecar 侧的真实成本与护栏状态（全部来自 meta.json）----------
        "agentBridgeBatchCount": batch_count,
        "agentBridgeRetryBatchCount": retried_batch_count,
        "agentBridgeElapsedSeconds": meta.get("elapsedSeconds") if config["transport"] != "http" else None,
        "agentBridgeInputTokensPerCall": sidecar_meta.get("inputTokensPerCall"),
        "agentBridgeOutputTokensTotal": sidecar_meta.get("outputTokensTotal"),
        "agentBridgeDeclaredToolCount": sidecar_meta.get("disallowedToolCount"),
        "agentBridgeLeakedTools": sidecar_meta.get("leakedTools") or [],
    }
    return {
        "requirements": requirements,
        "warnings": normalized["warnings"][:8],
        "modelUsed": _safe_text((raw_result or {}).get("modelUsed") or "workbuddy-agent", 80),
        "batchCount": batch_count,
        "failedBatchCount": failed_batch_count,
        "sentSegmentIds": sent_segment_ids,
        "reportedSegmentIds": reported_segment_ids,
        "jobId": job_id,
        "transport": config["transport"],
        "structurePath": str(structure_path) if structure_path else "",
        "stats": stats,
        # ---- P0 并轨：章节判断（第一跳的替代品）------------------------------
        # `sections` 为空不等于"没有要求章节"，也可能是这次任务包/结果里没有该字段
        # （例如 Sidecar 是用旧 instructions 跑出来的）。调用方必须区分这两种情况：
        # 前者可用、后者要回退本地 LLM。`sectionJudgement` 与
        # `fabricatedSectionCount` 是给人看的归因信息。
        "sections": normalized.get("sections") or [],
        "sectionJudgement": normalized.get("sectionJudgement") or "",
        "sectionMenuCount": len(job.get("sectionMenu") or []),
        "fabricatedSectionCount": int(normalized.get("fabricatedSectionCount") or 0),
    }


# --------------------------------------------------------------------------- #
# 结构快照（任务包的伴随产物）
# --------------------------------------------------------------------------- #
def structure_path(config: dict[str, Any], job_id: str) -> Path:
    return bridge_root(config) / "structure" / f"{job_id}.structure.json"


def write_structure_snapshot(
    config: dict[str, Any],
    job: dict[str, Any],
    *,
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    segments: list[dict[str, Any]] | None = None,
) -> Path | None:
    """落一份结构快照：章节树 + 表格 + 产物需要但任务包不带的片段级字段。

    任务包是「给智能体看的」，只保留它判断所必需的内容；快照是「给产物/人工复核看的」，
    保留完整结构。两者分开，任务包才能保持在 MB 级以下。
    """
    if not sections and not tables:
        return None
    try:
        directory = structure_path(config, str(job.get("jobId") or "")).parent
        directory.mkdir(parents=True, exist_ok=True)
        segment_extras = {
            str(segment.get("id")): {
                "sectionPath": [str(value) for value in (segment.get("sectionPath") or [])],
                "section": str(segment.get("section") or ""),
                "heading": str(segment.get("heading") or ""),
                "sectionType": str(segment.get("sectionType") or ""),
                "tableRequirementType": str(segment.get("tableRequirementType") or ""),
                "tableRequirementCellIndex": segment.get("tableRequirementCellIndex"),
                "sourceLocator": str(segment.get("sourceLocator") or ""),
                "contentType": str(segment.get("contentType") or ""),
                # 富块重建需要这两个键：产物按 tableId 聚合表格行，按 rowIndex 保序。
                "tableId": str(segment.get("tableId") or ""),
                "rowIndex": segment.get("rowIndex"),
            }
            for segment in (segments or [])
            if segment.get("id")
        }
        # 表格对象上也标一份语义类型，产物做富块时不必再从片段反推
        #（复用提取层判据，避免两处漂移）。
        table_records = []
        for table in tables or []:
            if not isinstance(table, dict):
                continue
            record = dict(table)
            if not record.get("tableRequirementType"):
                record["tableRequirementType"] = _table_type_of(table)
            table_records.append(record)

        target = structure_path(config, str(job.get("jobId") or ""))
        target.write_text(
            json.dumps(
                {
                    "schemaVersion": SCHEMA_VERSION,
                    "jobId": job.get("jobId"),
                    "sourceFileId": (job.get("document") or {}).get("sourceFileId", ""),
                    "document": job.get("document") or {},
                    "sections": sections or [],
                    "tables": table_records,
                    "segmentExtras": segment_extras,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return target
    except OSError:
        # 快照写失败不能影响提取主链路。
        return None


def _table_type_of(table: dict[str, Any]) -> str:
    """取表格语义类型；复用提取层判据（延迟导入以打破循环导入）。"""
    try:
        from .document_fine_matching import _table_requirement_type

        return str(_table_requirement_type(table) or "")
    except Exception:  # pragma: no cover
        return ""


# --------------------------------------------------------------------------- #
# 提取产物（D）：runtime/projects/<projectId>/pack/
# --------------------------------------------------------------------------- #
def _safe_dir_name(value: str, fallback: str = "unknown") -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._\u4e00-\u9fff-]+", "-", str(value or "")).strip("-. ")
    return cleaned[:80] or fallback


def pack_dir(config: dict[str, Any], project_id: str) -> Path:
    """产物的落盘目录：<PROJECT_ROOT>/<packDir>/<projectId>/pack/。

    刻意与 `runtime/projects/<id>/source/`（上传原件）同级同构 —— 项目相关的东西
    都挂在项目目录下，删项目时一起删得干净，也不会散落到别处。
    """
    raw = Path(str(config.get("packDir") or DEFAULT_CONFIG["packDir"]))
    root = raw if raw.is_absolute() else (PROJECT_ROOT / raw)
    name = _safe_dir_name(project_id, fallback="unassigned")
    return root / name / "pack"


def _load_structure_snapshot(path: str) -> dict[str, Any]:
    if not path:
        return {}
    target = Path(path)
    if not target.is_file():
        return {}
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _compact_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    """把 `scope` 瘦身成"计数 + 片段 id 清单"，丢掉所有明细对象。

    ⚠ 这是实测踩到的坑：`scope` 里带着 `selectedSegments`、`selectedTables`、
    `selectedSections`、`scopeItems` 四个**明细列表**，元素都是带正文/单元格的完整对象。
    一份 1371 段的文件里，仅 `selectedTables` 一项就 431 KB、`selectedSegments` 让产物
    从几百 KB 涨到 **7 MB** —— 而产物里**没有任何一处**需要那些正文：
    溯源靠 `segmentId` 回原始文档查，比在产物里塞副本更可靠（副本会过期、会与原文不一致，
    而且会让"产物里的文本是不是原文"变成一件说不清的事）。

    所以这里改成**白名单**：只放计数、id 清单、以及小型的 `stats` / `warnings`。
    新增字段时默认不会进产物 —— 要进就得显式加进白名单，这是故意的。
    """
    if not isinstance(scope, dict):
        return {}
    compact: dict[str, Any] = {}
    for key in ("stats", "warnings"):
        value = scope.get(key)
        if value not in (None, "", [], {}):
            compact[key] = value
    for key in (
        "selectedSections",
        "selectedTables",
        "scopeItems",
        "references",
        "unresolvedReferences",
        "candidateSections",
    ):
        value = scope.get(key)
        if isinstance(value, list):
            compact[f"{key}Count"] = len(value)
    selected = scope.get("selectedSegments") or []
    compact["selectedSegmentCount"] = len(selected)
    compact["selectedSegmentIds"] = [
        str(segment.get("id"))
        for segment in selected
        if isinstance(segment, dict) and segment.get("id")
    ][:4000]
    llm_sections = scope.get("llmSections") or []
    compact["llmSectionCount"] = len(llm_sections)
    # P0 并轨：章节判断的来源（agent / llm / skipped）与"判断选中的章数"。
    # 不加这两个键，产物里"哪些章被判定为要求章"就无法归因 —— 并轨前后
    # `llmSections` 的外观完全一样，但一个是智能体判的、一个是本地 LLM 判的。
    compact["llmSectionSource"] = str(scope.get("llmSectionSource") or "")
    compact["llmSectionPickedCount"] = int(scope.get("llmSectionPickedCount") or 0)
    compact["llmSections"] = [
        {
            "sectionId": str(section.get("sectionId") or ""),
            "heading": _safe_text(section.get("heading"), 200),
        }
        for section in llm_sections
        if isinstance(section, dict)
    ][:200]
    return compact


def write_tender_pack(
    ai: dict[str, Any] | None,
    *,
    project_id: str,
    source_file_id: str,
    job_id: str,
    requirements: list[dict[str, Any]],
    structure_path: str = "",
    scope: dict[str, Any] | None = None,
    stats: dict[str, Any] | None = None,
    warnings: list[str] | None = None,
    extraction_method: str = "",
    model_used: str = "",
    document_meta: dict[str, Any] | None = None,
) -> Path | None:
    """把一次提取的**完整产物**落盘，供匹配（已有）与方案撰写（bid_writing）消费。

    设计取舍（对应 D4「产物不绑现有约定」）
    --------------------------------------
    产物**不**复刻 `fineReview.rows` 的形状。理由：rows 是"匹配结果"，每个字段都
    携带了企业侧证据与判定状态；而提取阶段根本没有企业侧信息，硬凑成 rows 只会得到
    一堆空字段，且把"提取契约"和"匹配契约"焊死 —— 以后撰写侧想多要一个字段就得
    同时动匹配层。所以这里给**面向需求本身**的结构（要求 + 溯源 + 结构上下文），
    再额外提供两个 `projections` 兼容层给现有消费方直接取用。

    文件布局：
        <packDir>/<projectId>/pack/tender_pack.json          最新一份（覆盖写）
        <packDir>/<projectId>/pack/archive/tender_pack.<jobId>.json   归档（同 jobId 不重复写）
    """
    config = bridge_config(ai)
    if not config.get("writePack", True):
        return None
    if not requirements:
        return None

    snapshot = _load_structure_snapshot(structure_path)
    extras = snapshot.get("segmentExtras") or {}
    sections_meta = [item for item in (snapshot.get("sections") or []) if isinstance(item, dict)]
    tables_meta = [item for item in (snapshot.get("tables") or []) if isinstance(item, dict)]

    packed_requirements: list[dict[str, Any]] = []
    section_counts: dict[str, int] = {}
    table_counts: dict[str, int] = {}
    for index, requirement in enumerate(requirements, start=1):
        segment_ids = [
            str(value)
            for value in (requirement.get("sourceSegmentIds") or [requirement.get("sourceSegmentId")])
            if str(value)
        ]
        primary = segment_ids[0] if segment_ids else ""
        extra = extras.get(primary) or {}
        section_path = [str(value) for value in (extra.get("sectionPath") or [])]
        section_key = "›".join(section_path)
        if section_key:
            section_counts[section_key] = section_counts.get(section_key, 0) + 1
        table_id = str(extra.get("tableId") or "")
        if table_id:
            table_counts[table_id] = table_counts.get(table_id, 0) + 1

        packed_requirements.append(
            {
                "requirementId": _safe_text(requirement.get("id") or f"req-{index:03d}", 60),
                "category": _safe_text(requirement.get("category") or "other", 40),
                "categoryLabel": AGENT_CATEGORY_LABELS.get(
                    str(requirement.get("category") or "other"), "其他要求"
                ),
                "requirement": _safe_text(
                    requirement.get("normalizedRequirement")
                    or requirement.get("rawText")
                    or requirement.get("text"),
                    1200,
                ),
                "rawText": _safe_text(requirement.get("rawText") or requirement.get("text"), 1200),
                "mandatory": bool(requirement.get("mandatory", True)),
                "isRejection": bool(requirement.get("isRejection")),
                "responseType": _safe_text(requirement.get("responseType"), 80),
                "evidenceNeed": _safe_text(requirement.get("evidenceNeed"), 80),
                "quantity": requirement.get("quantity"),
                "timeLimit": _safe_text(requirement.get("timeLimit"), 160),
                "confidence": requirement.get("confidence"),
                # 溯源：这一段是产物的**核心价值** —— 没有它，下游看到的只是一句
                # "文本"，无法回到原文核对；有它才能逐条核验、才能让撰写侧引用出处。
                "source": {
                    "segmentId": primary,
                    "segmentIds": segment_ids,
                    "sourceLocator": _safe_text(
                        requirement.get("sourceLocator") or extra.get("sourceLocator"), 200
                    ),
                    "sectionPath": section_path,
                    "section": section_key or _safe_text(requirement.get("section"), 300),
                    "heading": _safe_text(extra.get("heading"), 200),
                    "sectionType": _safe_text(extra.get("sectionType"), 60),
                    "contentType": _safe_text(extra.get("contentType"), 60),
                    "tableId": table_id,
                    "tableRequirementType": _safe_text(extra.get("tableRequirementType"), 60),
                    "rowIndex": extra.get("rowIndex"),
                },
                # 产出通道：D3「直接替换」下，一份产物里可能混有"智能体条款"与
                # "规则兜底条款"（否决项兜底走规则通道），必须逐条标出来，
                # 否则下游无法解释"为什么这条的写法与前一条不一样"。
                "extractionSource": _safe_text(requirement.get("extractionSource"), 40),
                "extractionProvider": _safe_text(
                    requirement.get("extractionProvider") or "workbuddy-agent", 60
                ),
            }
        )

    sections_out = [
        {
            "sectionId": _safe_text(section.get("sectionId"), 80),
            "heading": _safe_text(section.get("heading"), 200),
            "sectionPath": [str(value) for value in (section.get("sectionPath") or [])],
            "sectionType": _safe_text(section.get("sectionType"), 60),
            "segmentCount": len(section.get("segmentIds") or []),
            "requirementCount": section_counts.get(
                "›".join(str(value) for value in (section.get("sectionPath") or [])), 0
            ),
        }
        for section in sections_meta
    ]

    rich_blocks = [
        {
            "tableId": table_id,
            "tableRequirementType": _safe_text(
                next(
                    (
                        table.get("tableRequirementType")
                        for table in tables_meta
                        if str(table.get("tableId") or table.get("id") or "") == table_id
                    ),
                    "",
                ),
                60,
            ),
            "requirementCount": count,
            "requirementIds": [
                item["requirementId"]
                for item in packed_requirements
                if item["source"]["tableId"] == table_id
            ],
        }
        for table_id, count in sorted(table_counts.items())
    ]

    stats = stats or {}
    pack = {
        "schemaVersion": PACK_SCHEMA_VERSION,
        "generatedAt": _now_iso(),
        "source": {
            "projectId": project_id,
            "sourceFileId": source_file_id,
            "jobId": job_id,
            "document": _normalize_meta(document_meta or {}),
            "structurePath": structure_path,
        },
        "extraction": {
            "method": extraction_method,
            "provider": stats.get("extractionProvider") or "",
            "modelUsed": model_used,
            "requirementCount": len(packed_requirements),
            "scopedSegmentCount": stats.get("scopedSegmentCount"),
            "sentSegmentCount": stats.get("sentSegmentCount"),
            "reportedSegmentCount": stats.get("reportedSegmentCount"),
            "deliveredCoverageRate": stats.get("deliveredCoverageRate"),
            "ruleRequirementCount": stats.get("ruleRequirementCount"),
            "rejectionFallbackRequirementCount": stats.get("rejectionFallbackRequirementCount"),
            "warnings": warnings or [],
            # 完整 stats 原样带出：新增统计键时不必同步改产物 schema。
            "stats": stats,
        },
        "scope": _compact_scope(scope),
        "requirements": packed_requirements,
        "sections": sections_out,
        "richBlocks": rich_blocks,
        "projections": {
            # 兼容层 1：匹配层 `document_matching.evaluate_tender_document` 的输入口径。
            # 有了它，匹配层可以直接消费产物，不必再去跑一遍提取。
            "matchExtraction": {
                "extractionMethod": extraction_method,
                "extractionProvider": stats.get("extractionProvider") or "",
                "modelUsed": model_used,
                "extractionWarning": "；".join((warnings or [])[:3]),
                "warnings": warnings or [],
                "scope": _compact_scope(scope),
                "stats": stats,
            },
            # 兼容层 2：方案撰写（bid_writing）要的 row 字段子集。
            # 只填**提取阶段可知**的字段；status/reason/evidence/suggestion 属匹配结果，
            # 这里显式留空而不是编造 —— 撰写侧拿到空值会去回查匹配结果，比拿到假值安全。
            #
            # ⚠ 边界说明：`bid_writing/README.md` 明确写了它的输入是 **`fineReview.rows`**
            #   （并行开发各子模块的对齐基准，且"禁止重写"上游）。所以这份投影**不是**
            #   要取代 rows，而是给"还没跑匹配、或要拿未匹配的原始要求"的场景留一条旁路。
            #   两份数据并存时以 `fineReview.rows` 为准。
            "bidWritingRowsNote": (
                "撰写层既定输入是 fineReview.rows（见 backend/app/bid_writing/README.md）。"
                "本投影仅供未跑匹配的场景使用；status/reason/evidence/suggestion 留空属正常。"
            ),
            "bidWritingRows": [
                {
                    "requirementId": item["requirementId"],
                    "category": item["category"],
                    "requirement": item["requirement"],
                    "status": "",
                    "reason": "",
                    "evidence": "",
                    "suggestion": "",
                    "mandatory": item["mandatory"],
                    "isRejection": item["isRejection"],
                    "responseType": item["responseType"],
                    "sourceLocator": item["source"]["sourceLocator"],
                }
                for item in packed_requirements
            ],
        },
    }

    directory = pack_dir(config, project_id)
    directory.mkdir(parents=True, exist_ok=True)
    latest = directory / "tender_pack.json"
    temp = latest.with_suffix(".json.tmp")
    temp.write_text(json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(latest)

    if job_id:
        archive_dir = directory / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived = archive_dir / f"tender_pack.{_safe_dir_name(job_id)}.json"
        if not archived.is_file():
            archived.write_text(
                json.dumps(pack, ensure_ascii=False, indent=2), encoding="utf-8"
            )
    return latest
