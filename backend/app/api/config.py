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

    return router
