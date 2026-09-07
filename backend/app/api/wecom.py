from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..store import Store
from ..wecom.service import test_webhook


def create_router(store: Store) -> APIRouter:
    router = APIRouter(prefix="/api/wecom", tags=["企业微信"])

    @router.post("/test")
    def test() -> dict[str, Any]:
        return test_webhook(store)

    return router
