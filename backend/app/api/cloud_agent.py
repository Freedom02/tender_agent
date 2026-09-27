from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

from ..cloud_agent.run_service import CloudRunService
from ..store import Store


def create_router(service: CloudRunService, store: Store) -> APIRouter:
    router = APIRouter()

    @router.post("/api/bid-runs", status_code=202)
    async def create_bid_run(
        background_tasks: BackgroundTasks,
        file: UploadFile = File(...),
        workspace_id: str = Form(""),
        project_id: str = Form(""),
    ) -> dict[str, Any]:
        content = await file.read()
        if not content:
            raise HTTPException(status_code=400, detail="招标文件不能为空")
        run = service.create_run(
            workspace_id=workspace_id.strip(),
            project_id=project_id.strip(),
            filename=file.filename or "tender-document",
            content=content,
            mime_type=file.content_type or "",
        )
        background_tasks.add_task(service.process_run, run["runId"])
        return {"run": run}

    @router.get("/api/bid-runs/{run_id}")
    def get_bid_run(run_id: str) -> dict[str, Any]:
        run = service.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="未找到解析任务")
        return {"run": run}

    @router.post("/api/bid-runs/{run_id}/materialize")
    def materialize_bid_run(run_id: str) -> dict[str, Any]:
        run = service.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="未找到解析任务")
        if run.get("status") not in {"outline_ready", "completed"}:
            if run.get("status") == "failed":
                raise HTTPException(status_code=409, detail=run.get("error") or "云端智能体解析失败")
            raise HTTPException(status_code=409, detail="云端智能体解析尚未完成")

        if run.get("materialized"):
            project = store.get_project(str(run.get("projectId") or ""))
            workspace = store.workspace(str(run.get("workspaceId") or ""))
            if project and workspace:
                return {
                    "run": run,
                    "project": store.with_preview(project),
                    "workspace": workspace,
                    "materialized": True,
                }

        materialized = store.materialize_cloud_parse(run)
        if not materialized:
            raise HTTPException(status_code=422, detail="解析结果为空，无法创建标书工作区")

        updated_run = service.repository.update(
            run_id,
            materialized=True,
            projectId=materialized["project"]["id"],
            workspaceId=materialized["workspace"]["id"],
        ) or run
        return {
            "run": updated_run,
            "project": materialized["project"],
            "workspace": materialized["workspace"],
            "materialized": True,
        }

    return router
