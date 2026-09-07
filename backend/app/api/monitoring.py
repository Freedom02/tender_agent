from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from ..config.service import create_custom_monitor_site, delete_custom_monitor_site, ensure_config, update_monitor_site
from ..store import Store
from ..tender_monitor.service import monitoring_status, run_monitoring, test_monitor_site


def create_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/monitoring", tags=["实时监控"])

    @router.get("/status")
    def status() -> dict[str, Any]:
        return monitoring_status(store)

    @router.post("/run")
    def run(payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return run_monitoring(store, str((payload or {}).get("siteId", "")))

    @router.patch("/sites/{site_id}")
    def update_site(site_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        site = update_monitor_site(store, site_id, payload)
        if not site:
            raise HTTPException(status_code=404, detail="未找到监控网站")
        return {"site": site, "monitoring": monitoring_status(store)}

    @router.post("/sites")
    def create_site(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            site = create_custom_monitor_site(store, payload)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"site": site, "monitoring": monitoring_status(store)}

    @router.delete("/sites/{site_id}")
    def delete_site(site_id: str) -> dict[str, Any]:
        if not delete_custom_monitor_site(store, site_id):
            raise HTTPException(status_code=404, detail="只能删除已添加的自定义监控网站")
        return {"monitoring": monitoring_status(store)}

    @router.post("/sites/{site_id}/test")
    def test_site(site_id: str) -> dict[str, Any]:
        config = ensure_config(store.config)
        site = next((item for item in config["monitoring"]["sites"] if item.get("id") == site_id), None)
        if not site:
            raise HTTPException(status_code=404, detail="未找到监控网站")
        return test_monitor_site(site)

    return router
