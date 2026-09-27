# -*- coding: utf-8 -*-
"""标书撰写模块 API：规划 / 生成 / 导出（明标·暗标）/ 校对自查。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel


class BidModePayload(BaseModel):
    mode: str = "open"


class PlanPayload(BaseModel):
    plan: list[dict[str, Any]]


class SectionContentPayload(BaseModel):
    content: str = ""
    templateBlocks: list[dict[str, Any]] | None = None
    templateTables: list[dict[str, Any]] | None = None
    contentMatchesTemplate: bool | None = None


class SectionRewritePayload(BaseModel):
    instruction: str = ""
    mode: str = "append"
    selectedText: str = ""


def create_router(store: Any) -> APIRouter:
    router = APIRouter()

    @router.get("/api/workspaces/{workspace_id}/parse-bundle")
    def get_parse_bundle(workspace_id: str) -> dict[str, Any]:
        bundle = store.get_parse_bundle(workspace_id)
        if not bundle:
            raise HTTPException(status_code=404, detail="工作区暂无标准解析包")
        return {"parseBundle": bundle}

    @router.post("/api/workspaces/{workspace_id}/reuse-match-analysis")
    def reuse_match_analysis(workspace_id: str) -> dict[str, Any]:
        result = store.prepare_matching_workspace_for_writing(workspace_id)
        if not result:
            raise HTTPException(
                status_code=409,
                detail="该项目没有可复用的完整招标文件匹配评估结果",
            )
        return result

    @router.post("/api/workspaces/{workspace_id}/bid-document/plan")
    def plan_bid_document(workspace_id: str) -> dict[str, Any]:
        result = store.plan_bid_document(workspace_id)
        if not result:
            raise HTTPException(status_code=404, detail="工作区不存在")
        return result

    @router.post("/api/workspaces/{workspace_id}/bid-document/generate")
    def generate_bid_document(workspace_id: str, payload: BidModePayload) -> dict[str, Any]:
        result = store.generate_bid_document(workspace_id, payload.mode)
        if not result:
            raise HTTPException(status_code=404, detail="工作区不存在")
        return result

    @router.put("/api/workspaces/{workspace_id}/bid-document/plan")
    def save_bid_plan(workspace_id: str, payload: PlanPayload) -> dict[str, Any]:
        result = store.save_bid_plan(workspace_id, payload.plan)
        if not result:
            raise HTTPException(status_code=404, detail="工作区不存在")
        return result

    @router.post("/api/workspaces/{workspace_id}/bid-document/sections/{section_id}/generate")
    def generate_bid_section(workspace_id: str, section_id: str) -> dict[str, Any]:
        result = store.generate_bid_section(workspace_id, section_id)
        if not result:
            raise HTTPException(status_code=404, detail="工作区或章节不存在")
        return result

    @router.post("/api/workspaces/{workspace_id}/bid-document/sections/{section_id}/fill-template")
    def fill_bid_section_template(workspace_id: str, section_id: str) -> dict[str, Any]:
        result = store.fill_bid_section_template(workspace_id, section_id)
        if not result:
            raise HTTPException(status_code=404, detail="工作区或章节不存在")
        if not result.get("filled"):
            raise HTTPException(
                status_code=400,
                detail=str(result.get("message") or "当前章节没有可填充的招标文件模板"),
            )
        return result

    @router.get("/api/workspaces/{workspace_id}/bid-document/sections/{section_id}/template-preview")
    def preview_bid_section_template(workspace_id: str, section_id: str) -> Response:
        content = store.render_bid_template_preview(workspace_id, section_id)
        if not content:
            raise HTTPException(status_code=404, detail="当前章节没有可预览的原始 Word 模板")
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={
                "Content-Disposition": f'inline; filename="template-{section_id}.docx"',
                "Cache-Control": "no-store",
            },
        )

    @router.post("/api/workspaces/{workspace_id}/bid-document/sections/{section_id}/rewrite")
    def rewrite_bid_section(
        workspace_id: str,
        section_id: str,
        payload: SectionRewritePayload,
    ) -> dict[str, Any]:
        if not payload.instruction.strip():
            raise HTTPException(status_code=400, detail="请先输入本章修改要求")
        result = store.rewrite_bid_section(
            workspace_id,
            section_id,
            payload.instruction,
            operation=payload.mode,
            selected_text=payload.selectedText,
        )
        if not result:
            raise HTTPException(status_code=404, detail="工作区或章节不存在")
        if result.get("protected"):
            raise HTTPException(status_code=400, detail=str(result.get("message") or "固定模板章节不能重写"))
        if not result.get("rewritten"):
            raise HTTPException(status_code=503, detail=str(result.get("message") or "Pi 未返回可应用的修改结果"))
        return result

    @router.put("/api/workspaces/{workspace_id}/bid-document/sections/{section_id}")
    def save_bid_section(workspace_id: str, section_id: str, payload: SectionContentPayload) -> dict[str, Any]:
        result = store.save_bid_section(
            workspace_id,
            section_id,
            payload.content,
            template_blocks=payload.templateBlocks,
            template_tables=payload.templateTables,
            content_matches_template=payload.contentMatchesTemplate,
        )
        if not result:
            raise HTTPException(status_code=404, detail="工作区或章节不存在")
        return result

    @router.post("/api/workspaces/{workspace_id}/bid-document/export")
    def export_bid_document(workspace_id: str, payload: BidModePayload) -> dict[str, Any]:
        result = store.export_bid_document(workspace_id, payload.mode)
        if not result:
            raise HTTPException(status_code=404, detail="工作区不存在")
        return result

    @router.get("/api/workspaces/{workspace_id}/proofread")
    def proofread_workspace(workspace_id: str) -> dict[str, Any]:
        result = store.proofread_workspace(workspace_id)
        if not result:
            raise HTTPException(status_code=404, detail="工作区不存在")
        return result

    @router.post("/api/workspaces/{workspace_id}/compliance-review")
    def compliance_review_workspace(workspace_id: str) -> dict[str, Any]:
        result = store.compliance_review_workspace(workspace_id)
        if not result:
            raise HTTPException(status_code=404, detail="工作区不存在")
        return result

    return router
