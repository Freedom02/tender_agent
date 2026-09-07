from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..wecom.service import send_text

if TYPE_CHECKING:
    from ..store import Store


def _find_project(store: "Store", project_id: str) -> dict[str, Any] | None:
    return next((item for item in store.catalog if item.get("id") == project_id), None)


def _record(store: "Store", project: dict[str, Any], channels: list[str], status: str, detail: str = "") -> dict[str, Any]:
    reminder = {
        "id": store.new_id("reminder"),
        "projectId": project.get("id"),
        "projectTitle": project.get("title"),
        "channels": channels,
        "createdAt": store.current_time(),
        "status": status,
        "detail": detail,
    }
    store.reminders.append(reminder)
    return reminder


def notify_new_projects(store: "Store", projects: list[dict[str, Any]]) -> dict[str, Any]:
    config = store.config.get("notifications", {})
    reminders: list[dict[str, Any]] = []

    for project in projects:
        if project.get("reminderStatus") == "已提醒":
            continue
        channels: list[str] = []
        if config.get("inAppEnabled", True):
            channels.append("页面弹窗")
        wecom_result: dict[str, Any] | None = None
        if config.get("wecomEnabled"):
            wecom_result = send_text(
                store,
                "【标智通｜新招标公告】\n"
                f"项目：{project.get('title', '')}\n"
                f"类型：{project.get('category', '待分类')}\n"
                f"地区：{project.get('region', '未识别')}\n"
                f"来源：{project.get('source', '监控网站')}\n"
                f"链接：{project.get('sourceUrl', '')}",
            )
            if wecom_result.get("sent"):
                channels.append("企业微信")

        project["reminderStatus"] = "已提醒" if channels else "未提醒"
        project["remindedAt"] = store.current_time() if channels else ""
        project["notificationChannels"] = channels
        detail = "" if not wecom_result else str(wecom_result.get("detail", ""))
        reminders.append(_record(store, project, channels, project["reminderStatus"], detail))

    store.persist_catalog()
    store.persist_runtime()
    return {"reminders": reminders}


def toggle_project_reminder(store: "Store", project_id: str) -> dict[str, Any] | None:
    project = _find_project(store, project_id)
    if not project:
        return None
    reminded = project.get("reminderStatus") != "已提醒"
    project["reminderStatus"] = "已提醒" if reminded else "未提醒"
    project["remindedAt"] = store.current_time() if reminded else ""
    project["notificationChannels"] = ["手动标记"] if reminded else []
    _record(store, project, project["notificationChannels"], project["reminderStatus"])
    store.persist_catalog()
    store.persist_runtime()
    return store.with_preview(project)


def test_in_app_notification() -> dict[str, Any]:
    return {
        "title": "页面提醒测试",
        "message": "这是标智通的页面提醒测试消息。",
    }
