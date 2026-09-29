# -*- coding: utf-8 -*-
"""标书生成 MCP 的运行时数据读取层（只读）。

职责边界：
  - 只读 `runtime/` 下的既有产物，**不写入、不改动**任何原始数据；
  - 把「标书生成所需的受控输入」整理成稳定的 JSON 结构，供外部 Agent 消费；
  - 不调用 LLM、不做匹配、不改判匹配结论。

为什么需要单独的读取层：
  `runtime/workspaces.json` 是单文件全量 JSON（本机已达数百 MB，其中
  `reasoning.fineReview.rows` 只占约 4MB，`fineReview.extraction` 等字段占
  绝大部分）。全量 `json.load` 需要数秒并驻留数 GB 内存，因此本模块采用
  **定点切片**：内存映射文件后按键名定位目标 workspace 的字节区间，
  只解析这一段。

受控输入原则（与 `bid_writing/technical.py` 的防幻觉约束一致）：
  返回给 Agent 的每一行只带「该条要求 + 该条命中的企业对象 ID」，
  不返回全量企业资料库，避免模型跨条联想或照抄无关内容。
"""
from __future__ import annotations

import json
import mmap
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

# backend/app/bid_writing_mcp/reader.py -> 项目根
PROJECT_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_DIR = PROJECT_ROOT / "runtime"
WORKSPACES_PATH = RUNTIME_DIR / "workspaces.json"
CATALOG_PATH = RUNTIME_DIR / "catalog.json"
EVIDENCE_DIR = RUNTIME_DIR / "libraries" / "company" / "证明材料"
BID_OUTPUT_DIR = RUNTIME_DIR / "bid_writing"

# 标书正文生成真正消费的 8 域（其余域只做展示/忽略，不进叙述章节）
WRITING_DOMAINS = ("technical", "qualification")
RISK_DOMAINS = ("rejection",)

# 单条要求在「索引模式」下返回的字段：够做章节规划与逐条响应，
# 但不含 rawText / normalizedText / sourceText —— 这三者与 requirement 同文，
# 是 rows 体积膨胀的主因（约 3 倍冗余）。
_ROW_INDEX_FIELDS = (
    "requirementId",
    "id",
    "targetModule",
    "targetModuleLabel",
    "requirementClass",
    "requirement",
    # 逐条拆分出的「参数点/单条要求」正文：8 目标通道下 rows 的粒度即为一条参数点，
    # requirement 是整张表格行原文，偏离表与正文都必须读这个字段。
    "parameters",
    "requirementNumber",
    "status",
    "reason",
    "suggestion",
    "evidence",
    "matchedObjectIds",
    "mandatory",
    "isRejection",
    "sourceLocator",
    "productName",
    "reviewStatus",
)

# 「完整模式」额外返回的证据链字段
_ROW_FULL_EXTRA_FIELDS = (
    "evidenceItems",
    "evidenceIds",
    "matchedMaterialIds",
    "technicalAlignment",
    "gaps",
    "parameters",
    "confidence",
    "sourceSegmentIds",
    "tableIds",
    "sectionPath",
)

_TEXT_CLIP_INDEX = 400
_TEXT_CLIP_FULL = 4000


class BidDataError(Exception):
    """读取层错误；code 用于映射为 MCP 的工具错误码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# 定点切片读取
# ---------------------------------------------------------------------------

def _locate_value_span(buf: mmap.mmap, key: bytes) -> tuple[int, int] | None:
    """在 JSON 字节流中定位 ``"key": <object>`` 的字节区间 ``[start, end)``。

    只对 ``{`` / ``[`` 同一对括号做配平计数，字符串内部的括号与转义引号会被跳过，
    因此不会误判嵌套结构。
    """
    pattern = b'"' + key + b'":'
    idx = buf.find(pattern)
    if idx < 0:
        return None

    size = len(buf)
    cursor = idx + len(pattern)
    while cursor < size and buf[cursor] in b" \t\r\n":
        cursor += 1
    if cursor >= size:
        return None

    opener = buf[cursor]
    if opener == 0x7B:  # {
        closer = 0x7D
    elif opener == 0x5B:  # [
        closer = 0x5D
    else:
        return None

    depth = 0
    in_string = False
    escaped = False
    while cursor < size:
        char = buf[cursor]
        if in_string:
            if escaped:
                escaped = False
            elif char == 0x5C:  # backslash
                escaped = True
            elif char == 0x22:  # quote
                in_string = False
        elif char == 0x22:
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return (idx, cursor + 1)
        cursor += 1
    return None


def _read_json_slice(path: Path, key: str) -> Any:
    """内存映射读取单文件 JSON 中某个顶层键的值，不加载整个文件。"""
    if not path.is_file():
        raise BidDataError("runtime_missing", f"运行时数据不存在：{path.name}")

    with path.open("rb") as handle:
        try:
            with mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as buf:
                span = _locate_value_span(buf, key.encode("utf-8"))
                if span is None:
                    raise BidDataError("workspace_not_found", f"未找到键 {key}")
                start, end = span
                chunk = bytes(buf[start:end])
        except ValueError as error:
            # 空文件等无法映射的情况，退回整文件读取
            handle.seek(0)
            payload = json.loads(handle.read().decode("utf-8"))
            value = payload.get(key) if isinstance(payload, dict) else None
            if value is None:
                raise BidDataError("workspace_not_found", f"未找到键 {key}") from error
            return value

    text = chunk.decode("utf-8")
    # 切片以 "key": 开头，补一层外层对象才能得到合法 JSON
    return json.loads("{" + text + "}")[key]


# 进程内缓存：MCP server 常驻，同一 workspace 在一个会话里会被反复读取
# （取上下文、取单条详情、取风险清单），缓存可把每次数秒的切片解析降到一次。
# 以文件 mtime 作为失效判据；workspaces.json 被后端改写后自动重新读取。
_WORKSPACES_MTIME = "workspaces.json"
_CATALOG_MTIME = "catalog.json"
_workspace_cache: dict[str, dict[str, Any]] = {}
_workspace_cache_mtime: dict[str, float] = {}
_catalog_cache: list[dict[str, Any]] = []
_catalog_cache_mtime: float = -1.0
_WORKSPACE_CACHE_MAX = 3


def _mtime_of(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return -1.0


def read_workspace(workspace_id: str) -> dict[str, Any]:
    """读取单个 workspace（定点切片，不加载整个 workspaces.json），带 mtime 缓存。"""
    mtime = _mtime_of(WORKSPACES_PATH)
    cached = _workspace_cache.get(workspace_id)
    if cached is not None and _workspace_cache_mtime.get(workspace_id) == mtime:
        return cached

    value = _read_json_slice(WORKSPACES_PATH, workspace_id)
    if not isinstance(value, dict):
        raise BidDataError("workspace_not_found", f"{workspace_id} 不是有效的工作区")

    # 简单的容量控制：超出上限时丢弃最早写入的条目，避免常驻内存持续增长
    if len(_workspace_cache) >= _WORKSPACE_CACHE_MAX:
        oldest = next(iter(_workspace_cache))
        _workspace_cache.pop(oldest, None)
        _workspace_cache_mtime.pop(oldest, None)
    _workspace_cache[workspace_id] = value
    _workspace_cache_mtime[workspace_id] = mtime
    return value


def read_catalog() -> list[dict[str, Any]]:
    """读取项目目录（catalog.json），带 mtime 缓存。"""
    global _catalog_cache, _catalog_cache_mtime

    if not CATALOG_PATH.is_file():
        return []
    mtime = _mtime_of(CATALOG_PATH)
    if _catalog_cache and _catalog_cache_mtime == mtime:
        return _catalog_cache

    payload = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    entries: list[dict[str, Any]] = []
    if isinstance(payload, list):
        entries = [item for item in payload if isinstance(item, dict)]
    elif isinstance(payload, dict):
        for key in ("projects", "entries", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                entries = [item for item in value if isinstance(item, dict)]
                break

    _catalog_cache = entries
    _catalog_cache_mtime = mtime
    return entries


# ---------------------------------------------------------------------------
# 展示用投影
# ---------------------------------------------------------------------------

def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "…[已截断]"


def _rows_of(fine_review: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = fine_review.get("rows")
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _row_domain(row: Mapping[str, Any]) -> str:
    """取行的归属域。8 域主线写 targetModule，15 类兼容层写 category。"""
    return str(row.get("targetModule") or row.get("category") or "other")


def _project_row(row: Mapping[str, Any], *, detail: str) -> dict[str, Any]:
    """把一条对标行裁剪成 Agent 可消费的结构。"""
    fields = list(_ROW_INDEX_FIELDS)
    clip = _TEXT_CLIP_INDEX
    if detail == "full":
        fields += list(_ROW_FULL_EXTRA_FIELDS)
        clip = _TEXT_CLIP_FULL

    out: dict[str, Any] = {}
    for key in fields:
        if key not in row:
            continue
        value = row.get(key)
        if isinstance(value, str):
            out[key] = _clip(value, clip)
        else:
            out[key] = value

    out["domain"] = _row_domain(row)
    has_alignment = isinstance(row.get("technicalAlignment"), (dict, list)) and bool(row.get("technicalAlignment"))
    out["hasTechnicalAlignment"] = has_alignment
    return out


def _status_counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "未判定")
        counts[status] = counts.get(status, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# 对外接口
# ---------------------------------------------------------------------------

def list_projects() -> dict[str, Any]:
    """列出可生成标书的项目（已分析、且已绑定 workspace）。"""
    entries = read_catalog()
    projects: list[dict[str, Any]] = []
    for entry in entries:
        workspace_id = str(entry.get("workspaceId") or "").strip()
        if not workspace_id:
            continue
        projects.append(
            {
                "projectId": str(entry.get("id") or ""),
                "workspaceId": workspace_id,
                "title": str(entry.get("title") or ""),
                "category": str(entry.get("category") or ""),
                "region": str(entry.get("region") or ""),
                "publishedDate": str(entry.get("publishedDate") or ""),
                "status": str(entry.get("status") or ""),
                "decision": str(entry.get("decision") or ""),
                "score": entry.get("score"),
            }
        )
    return {
        "operation": "list_projects",
        "count": len(projects),
        "projects": projects,
        "hint": "选择 workspaceId 后调用 get_bid_context 取标书生成输入。",
    }


def _context_summary(project: Mapping[str, Any], reasoning: Mapping[str, Any], fine_review: Mapping[str, Any]) -> dict[str, Any]:
    rows = _rows_of(fine_review)
    by_domain: dict[str, int] = {}
    for row in rows:
        domain = _row_domain(row)
        by_domain[domain] = by_domain.get(domain, 0) + 1
    parsed = project.get("parsed") if isinstance(project.get("parsed"), dict) else {}
    return {
        "title": str(project.get("title") or ""),
        "projectId": str(project.get("id") or ""),
        "totalRows": len(rows),
        "byDomain": by_domain,
        "statusCounts": _status_counts(rows),
        "score": fine_review.get("score") or reasoning.get("score"),
        "decision": fine_review.get("decision") or reasoning.get("decision"),
        "recommendation": fine_review.get("recommendation") or "",
        "matchStage": fine_review.get("matchStage") if isinstance(fine_review.get("matchStage"), dict) else {},
        "parsed": {
            key: _clip(parsed.get(key), 300)
            for key in ("projectName", "serviceType", "serviceLocation", "purchaser", "budget", "deadline")
            if parsed.get(key)
        },
    }


def get_bid_context(
    workspace_id: str,
    *,
    domains: Iterable[str] | None = None,
    detail: str = "index",
    limit: int = 40,
    offset: int = 0,
) -> dict[str, Any]:
    """取标书生成的受控输入：项目信息 + 按域分组的对标行（分页）。"""
    workspace = read_workspace(workspace_id)
    reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
    fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
    project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}

    rows = _rows_of(fine_review)
    wanted = {str(d) for d in domains} if domains else set()
    if wanted:
        rows = [row for row in rows if _row_domain(row) in wanted]

    total = len(rows)
    safe_limit = max(1, min(int(limit or 40), 200))
    safe_offset = max(0, int(offset or 0))
    page = rows[safe_offset : safe_offset + safe_limit]

    return {
        "operation": "get_bid_context",
        "workspaceId": workspace_id,
        "summary": _context_summary(project, reasoning, fine_review),
        "detail": detail,
        "total": total,
        "offset": safe_offset,
        "limit": safe_limit,
        "hasMore": safe_offset + safe_limit < total,
        "rows": [_project_row(row, detail=detail) for row in page],
        "writingDomains": list(WRITING_DOMAINS),
        "riskDomains": list(RISK_DOMAINS),
    }


def read_fine_review(workspace_id: str) -> dict[str, Any]:
    """读取单个 workspace 的 fineReview 原始结构（只读，供偏离表等纯函数消费）。

    与 get_bid_context 的「受控投影」不同，本函数返回未经裁剪的 rows，
    因为 `bid_writing.deviations` 等纯函数需要读取 evidenceItems 等完整字段。
    """
    workspace = read_workspace(workspace_id)
    reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
    fine_review = reasoning.get("fineReview")
    return fine_review if isinstance(fine_review, dict) else {}


def get_requirement_detail(workspace_id: str, requirement_id: str) -> dict[str, Any]:
    """取单条要求的完整证据链。"""
    workspace = read_workspace(workspace_id)
    reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
    fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}

    target = str(requirement_id or "").strip()
    for row in _rows_of(fine_review):
        row_id = str(row.get("requirementId") or row.get("id") or "").strip()
        if row_id != target:
            continue
        detail = _project_row(row, detail="full")
        detail["rawText"] = _clip(row.get("rawText") or row.get("requirement"), _TEXT_CLIP_FULL)
        return {
            "operation": "get_requirement_detail",
            "workspaceId": workspace_id,
            "requirementId": target,
            "row": detail,
        }

    raise BidDataError("not_found", f"未找到要求 {target}")


def get_risk_digest(workspace_id: str) -> dict[str, Any]:
    """取风险与缺口清单：硬性否决、废标风险、未解决强制项、待补材料。"""
    workspace = read_workspace(workspace_id)
    reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
    fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}

    def _clip_list(value: Any, limit: int = 40) -> list[Any]:
        if not isinstance(value, list):
            return []
        out: list[Any] = []
        for item in value[:limit]:
            if isinstance(item, dict):
                out.append(
                    {
                        key: _clip(item.get(key), _TEXT_CLIP_INDEX)
                        for key in ("requirementId", "id", "requirement", "title", "text", "reason", "suggestion", "riskLevel", "status")
                        if item.get(key)
                    }
                )
            else:
                out.append(_clip(item, _TEXT_CLIP_INDEX))
        return out

    return {
        "operation": "get_risk_digest",
        "workspaceId": workspace_id,
        "hardBlockers": _clip_list(fine_review.get("hardBlockers")),
        "rejectionRisks": _clip_list(fine_review.get("rejectionRisks")),
        "unresolvedMandatory": _clip_list(fine_review.get("unresolvedMandatory")),
        "pendingMaterialList": _clip_list(fine_review.get("pendingMaterialList")),
        "risks": _clip_list(fine_review.get("risks")),
        "score": fine_review.get("score"),
        "decision": fine_review.get("decision"),
        "recommendation": fine_review.get("recommendation"),
    }


def list_company_evidence(category: str | None = None) -> dict[str, Any]:
    """列出企业证明材料清单（只读目录，供正文引用与核验）。

    返回的是「能引用什么」，不是证据内容本身；正文必须只引用这里出现过的条目。
    """
    if not EVIDENCE_DIR.is_dir():
        return {
            "operation": "list_company_evidence",
            "count": 0,
            "categories": [],
            "hint": f"证据目录不存在：{EVIDENCE_DIR}",
        }

    categories: list[dict[str, Any]] = []
    wanted = str(category or "").strip()
    for folder in sorted(EVIDENCE_DIR.iterdir()):
        if not folder.is_dir():
            continue
        if wanted and folder.name != wanted:
            continue
        items = []
        for entry in sorted(folder.iterdir()):
            if entry.is_dir():
                files = [f.name for f in sorted(entry.iterdir()) if f.is_file()]
                items.append({"name": entry.name, "type": "folder", "files": files[:12]})
            else:
                items.append({"name": entry.name, "type": "file", "files": []})
        categories.append({"category": folder.name, "count": len(items), "items": items[:200]})

    return {
        "operation": "list_company_evidence",
        "root": str(EVIDENCE_DIR.relative_to(RUNTIME_DIR)),
        "count": sum(item["count"] for item in categories),
        "categories": categories,
    }


def bid_output_dir(workspace_id: str) -> Path:
    """标书产物的独立输出目录（本模块创建的目录，绝不写回原始运行时数据）。"""
    safe = "".join(ch for ch in str(workspace_id) if ch.isalnum() or ch in "-_")
    return BID_OUTPUT_DIR / (safe or "unknown")
