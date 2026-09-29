# -*- coding: utf-8 -*-
"""标书生成 MCP server（stdio, newline-delimited JSON-RPC）。

设计目标：把「读取标书输入 → 写章节 → 组装导出」这条链路变成外部 Agent
可以直接调用的工具，从而不必为标书撰写单独编写一套 agent 编排代码。

与 `workbuddy_agent/tender_review_mcp.py` 的关系：
  两者是同构范式（同样的手写 JSON-RPC over stdio、同样的受控输入思路），
  但绑定对象不同——Tender Review MCP 绑定**单份招标文件**，本 server 绑定
  **运行时里已有的匹配产物**（`runtime/workspaces.json` + 证据目录）。

只读边界：
  前 5 个工具全部只读。唯一产生文件的是 `export_bid_docx`，且只写入
  `runtime/bid_writing/{workspaceId}/` 这个本模块自有的目录，
  绝不改写 `workspaces.json` 或任何既有运行时数据。

防幻觉约束（承接 `bid_writing/technical.py` 的三层设计）：
  1. 输入受控——工具按需返回「该条要求 + 该条命中的对象 ID」，不倾倒全库；
  2. 溯源可查——每条要求都能用 `get_requirement_detail` 取到证据链；
  3. 引用可验——`list_company_evidence` 给出可引用的证据白名单，
     `export_bid_docx` 会回报未生成章节，不把缺失伪装成完成。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from . import reader
from .reader import BidDataError

MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_VERSION = "bid-writing-mcp/v1"

_DRIFT_NOTE = (
    "标书正文必须只依据 get_bid_context / get_requirement_detail 返回的材料撰写；"
    "严禁编造资质、业绩、人员、证书、参数或数字。资料未覆盖处必须显式写占位，"
    "交由人工补充。"
)


def _tool_definitions() -> list[dict[str, Any]]:
    """工具清单。手写 JSON Schema，避免为少量参数引入额外依赖。"""
    return [
        {
            "name": "list_bid_projects",
            "description": (
                "列出本机已分析、且已绑定工作区的招标项目，返回 projectId / workspaceId / 标题 / 状态。"
                "先调它选目标，再用 get_bid_context 取标书生成输入。"
            ),
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "name": "get_bid_context",
            "description": (
                "取某个工作区的标书生成受控输入：项目信息摘要 + 按域统计 + 分页的对标行。"
                "对标行是「招标要求 ↔ 企业事实」的逐条结论，是标书章节规划与逐条响应的唯一依据。"
                "columns: requirementId/domain/requirement/status/reason/suggestion/evidence/matchedObjectIds。"
                "detail=index 返回精炼字段（规划用），detail=full 附加技术参数对齐与证据明细（写作时用）。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspaceId": {"type": "string", "description": "工作区 ID，如 workspace-6af8cb36c9"},
                    "domains": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "按域过滤，如 [\"technical\"]、[\"qualification\"]、[\"rejection\"]；省略则返回全部域",
                    },
                    "detail": {"type": "string", "enum": ["index", "full"], "description": "字段详细程度，默认 index"},
                    "limit": {"type": "integer", "description": "单页条数，默认 40，上限 200"},
                    "offset": {"type": "integer", "description": "偏移量，默认 0；用 hasMore 判断是否继续翻页"},
                },
                "required": ["workspaceId"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_requirement_detail",
            "description": (
                "取单条要求的完整证据链：要求原文、判定理由、命中企业对象 ID、证明材料明细、"
                "技术参数对齐结果。写某条要求的响应正文前用它确认可用材料。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspaceId": {"type": "string"},
                    "requirementId": {"type": "string", "description": "对标行的 requirementId"},
                },
                "required": ["workspaceId", "requirementId"],
                "additionalProperties": False,
            },
        },
        {
            "name": "get_risk_digest",
            "description": (
                "取该项目的风险与缺口清单：硬性否决项、废标风险、未解决的强制项、待补材料、"
                "总分与投标建议。用于标书里的风险提示章节与人工复核清单。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"workspaceId": {"type": "string"}},
                "required": ["workspaceId"],
                "additionalProperties": False,
            },
        },
        {
            "name": "list_company_evidence",
            "description": (
                "列出企业证明材料白名单（资质认证/类似业绩/项目人员/财务证明/技术产品/技术参数/证照凭证）。"
                "正文只能引用这里出现过的条目；未列入的视为不存在。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "可选，按类别过滤，如 资质认证、类似业绩；省略则返回全部类别",
                    }
                },
                "additionalProperties": False,
            },
        },
        {
            "name": "export_bid_docx",
            "description": (
                "把已写入的标书章节组装为一个 .docx 并落盘到 runtime/bid_writing/{workspaceId}/output/。"
                "读取同目录下的 plan.json（章节树）与 sections/*.md（章节正文）；"
                "偏离表由匹配结果 fineReview.rows 实时派生（逐条参数点，一条要求一行），无需手工提供。"
                "会回报未生成章节，不会把缺失当作完成。"
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "workspaceId": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["open", "secret"],
                        "description": "open=明标（含企业信息），secret=暗标（脱敏）；默认 open",
                    },
                    "title": {"type": "string", "description": "可选，封面标题；省略则从工作区读取"},
                },
                "required": ["workspaceId"],
                "additionalProperties": False,
            },
        },
    ]


class BidWritingMCPServer:
    """标书生成工具服务（只读 + 自有目录导出）。"""

    def __init__(self) -> None:
        self._dispatch: dict[str, Callable[..., dict[str, Any]]] = {
            "list_bid_projects": self._list_bid_projects,
            "get_bid_context": self._get_bid_context,
            "get_requirement_detail": self._get_requirement_detail,
            "get_risk_digest": self._get_risk_digest,
            "list_company_evidence": self._list_company_evidence,
            "export_bid_docx": self._export_bid_docx,
        }

    # -- 工具实现 ---------------------------------------------------------

    def _list_bid_projects(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return reader.list_projects()

    def _get_bid_context(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = self._require_str(arguments, "workspaceId")
        domains = arguments.get("domains")
        domain_list = [str(item) for item in domains] if isinstance(domains, list) else None
        detail = "full" if str(arguments.get("detail") or "index") == "full" else "index"
        result = reader.get_bid_context(
            workspace_id,
            domains=domain_list,
            detail=detail,
            limit=self._as_int(arguments.get("limit"), 40),
            offset=self._as_int(arguments.get("offset"), 0),
        )
        result["driftNote"] = _DRIFT_NOTE
        return result

    def _get_requirement_detail(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = self._require_str(arguments, "workspaceId")
        requirement_id = self._require_str(arguments, "requirementId")
        return reader.get_requirement_detail(workspace_id, requirement_id)

    def _get_risk_digest(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        workspace_id = self._require_str(arguments, "workspaceId")
        return reader.get_risk_digest(workspace_id)

    def _list_company_evidence(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        category = arguments.get("category")
        return reader.list_company_evidence(str(category) if category else None)

    def _export_bid_docx(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """读取本模块自有目录下的标书草稿，组装 DOCX。

        复用 backend/app/bid_writing/assemble.py，导出逻辑与后端完全一致，
        因此本工具产出的文档和从界面导出的格式相同。
        """
        workspace_id = self._require_str(arguments, "workspaceId")
        mode = str(arguments.get("mode") or "open")
        if mode not in {"open", "secret"}:
            raise BidDataError("invalid_arguments", "mode 只能是 open 或 secret")

        out_dir = reader.bid_output_dir(workspace_id)
        plan_path = out_dir / "plan.json"
        if not plan_path.is_file():
            raise BidDataError(
                "not_planned",
                f"缺少章节规划：{plan_path}。请先按 plan.json 结构写入章节树与 sections/*.md 正文。",
            )

        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if isinstance(plan, dict):
            plan = plan.get("sections") or plan.get("plan") or []
        if not isinstance(plan, list) or not plan:
            raise BidDataError("not_planned", "plan.json 为空或格式不正确")

        sections_dir = out_dir / "sections"
        generated: list[str] = []
        missing: list[str] = []
        for section in plan:
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("sectionId") or "").strip()
            if not section_id:
                continue
            md_path = sections_dir / f"{section_id}.md"
            if md_path.is_file():
                section["content"] = md_path.read_text(encoding="utf-8")
                generated.append(section_id)
            elif str(section.get("kind") or "") == "deviation":
                # 偏离表正文来自 deviations.json，由 assemble_docx 原位渲染成表格，
                # 不依赖 sections/*.md，因此不计入缺失章节。
                generated.append(section_id)
            elif not str(section.get("content") or "").strip():
                missing.append(section_id)

        # 偏离表：直接由匹配结果 fineReview.rows 派生（逐条参数点，一条要求一行），
        # 不从手写文件读取——手写极易与匹配结果脱节，且会把整行原文重复上百遍。
        # 派生结果同时落盘到本模块自有目录，便于人工核查。
        from backend.app.bid_writing.deviations import build_deviation_tables

        fine_review = reader.read_fine_review(workspace_id)
        deviation_tables = build_deviation_tables(fine_review)
        try:
            (out_dir / "deviations.json").write_text(
                json.dumps(deviation_tables, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

        title = str(arguments.get("title") or "").strip()
        if not title:
            try:
                workspace = reader.read_workspace(workspace_id)
                project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}
                title = str(project.get("title") or workspace.get("title") or "")
            except BidDataError:
                title = ""

        bid_document: dict[str, Any] = {
            "plan": plan,
            "deviationTables": deviation_tables,
            "mode": mode,
            "title": title or "投标响应文件",
        }
        if mode == "secret":
            from backend.app.bid_writing.anonymize import anonymize_document

            bid_document = anonymize_document(bid_document, {}, {})

        from backend.app.bid_writing.assemble import assemble_docx

        output_dir = out_dir / "output"
        result = assemble_docx(bid_document, output_dir)

        return {
            "operation": "export_bid_docx",
            "workspaceId": workspace_id,
            "mode": mode,
            "fileName": result.get("fileName"),
            "path": result.get("path"),
            "generatedSections": generated,
            "missingSections": missing,
            "warning": (
                f"以下章节尚无正文，导出的文档中为占位：{'、'.join(missing)}" if missing else ""
            ),
        }

    # -- 参数与协议 -------------------------------------------------------

    @staticmethod
    def _require_str(arguments: Mapping[str, Any], key: str) -> str:
        value = str(arguments.get(key) or "").strip()
        if not value:
            raise BidDataError("invalid_arguments", f"缺少必填参数 {key}")
        return value

    @staticmethod
    def _as_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        handler = self._dispatch.get(name)
        if handler is None:
            return {"error": {"code": "unknown_tool", "message": f"未知工具：{name}"}}
        try:
            return handler(arguments)
        except BidDataError as error:
            return {"error": {"code": error.code, "message": error.message}}
        except FileNotFoundError as error:
            return {"error": {"code": "runtime_missing", "message": str(error)}}
        except Exception as error:  # 兜底：不把堆栈泄漏给调用方
            return {"error": {"code": "internal_error", "message": f"{type(error).__name__}: {error}"}}

    def handle_message(self, message: Mapping[str, Any]) -> dict[str, Any] | None:
        method = str(message.get("method") or "")
        request_id = message.get("id")

        # notification（无 id）不需要响应
        if request_id is None and method.startswith("notifications/"):
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "bid-writing", "version": MCP_SERVER_VERSION},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": _tool_definitions()},
            }
        if method == "tools/call":
            params = message.get("params")
            params = params if isinstance(params, Mapping) else {}
            name = str(params.get("name") or "")
            arguments = params.get("arguments")
            result = self.call_tool(name, arguments if isinstance(arguments, Mapping) else {})
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                    "structuredContent": result,
                    "isError": "error" in result,
                },
            }
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": "Method not found.", "data": {"method": method}},
        }


def serve_stdio(
    server: BidWritingMCPServer,
    *,
    stdin: Any = None,
    stdout: Any = None,
) -> None:
    """按行读取 JSON-RPC 并写回响应；日志一律走 stderr，不污染 stdout。"""
    input_stream = stdin or sys.stdin
    output_stream = stdout or sys.stdout
    for line in input_stream:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, Mapping):
                raise ValueError("JSON-RPC message must be an object")
            response = server.handle_message(message)
            if response is not None:
                output_stream.write(json.dumps(response, ensure_ascii=True) + "\n")
                output_stream.flush()
        except (json.JSONDecodeError, ValueError) as error:
            output_stream.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {
                            "code": -32700,
                            "message": "Invalid JSON-RPC message.",
                            "data": {"error": str(error)},
                        },
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
            output_stream.flush()


__all__ = ("BidWritingMCPServer", "serve_stdio", "MCP_SERVER_VERSION", "MCP_PROTOCOL_VERSION")
