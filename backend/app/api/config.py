from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config.service import update_configuration
from ..store import Store


class ConfigPayload(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


def create_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/config", tags=["配置"])

    @router.get("")
    def get_config() -> dict[str, Any]:
        return store.public_config()

    @router.post("")
    def save_config(payload: ConfigPayload) -> dict[str, Any]:
        return {"config": update_configuration(store, payload.config)}

    @router.post("/ai/test")
    def test_ai(payload: ConfigPayload) -> dict[str, Any]:
        configured = dict(store.config.get("ai", {}))
        incoming = payload.config.get("ai", payload.config)
        if isinstance(incoming, dict):
            configured.update(incoming)
        api_key = str(configured.get("apiKey") or "").strip()
        if api_key in {"***", "已配置"}:
            configured["apiKey"] = store.config.get("ai", {}).get("apiKey", "")
        return store.test_ai_connection(configured)

    @router.get("/pi/preflight")
    def pi_preflight() -> dict[str, Any]:
        """重新执行 Pi 模型自检并返回结果（会查询网关，供配置页手动触发）。"""

        from ..pi_agent.preflight import get_report

        return get_report(refresh=True)

    return router
