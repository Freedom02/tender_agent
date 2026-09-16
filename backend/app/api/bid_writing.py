# -*- coding: utf-8 -*-
"""标书撰写模块 API：规划 / 生成 / 导出（明标·暗标）/ 校对自查。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel


class BidModePayload(BaseModel):
    mode: str = "open"


def create_router(store: Any) -> APIRouter:
    router = APIRouter()

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

    return router
