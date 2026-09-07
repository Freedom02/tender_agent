from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..notification.service import test_in_app_notification, toggle_project_reminder
from ..store import Store


def create_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/notifications", tags=["提醒"])

    @router.post("/projects/{project_id}/toggle")
    def toggle(project_id: str) -> dict[str, Any]:
        item = toggle_project_reminder(store, project_id)
        if not item:
            raise HTTPException(status_code=404, detail="未找到项目")
        return {"item": item, "reminders": store.reminder_snapshot()}

    @router.post("/test/in-app")
    def test_in_app() -> dict[str, Any]:
        return test_in_app_notification()

    return router
