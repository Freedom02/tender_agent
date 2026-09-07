from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

from .service import run_monitoring

if TYPE_CHECKING:
    from ..store import Store


def _is_due(store: "Store") -> bool:
    monitoring = store.config.get("monitoring", {})
    if monitoring.get("enabled") is False:
        return False
    stats = monitoring.get("stats", {})
    last_fetched = str(stats.get("lastFetchedAt", "")).strip()
    if not last_fetched:
        return True
    try:
        last_time = datetime.strptime(last_fetched, "%Y-%m-%d %H:%M").astimezone()
    except ValueError:
        return True
    frequency = max(int(monitoring.get("frequencyMinutes", 30) or 30), 1)
    return (datetime.now().astimezone() - last_time).total_seconds() >= frequency * 60


async def monitoring_loop(store: "Store") -> None:
    while True:
        if _is_due(store):
            run_monitoring(store)
        await asyncio.sleep(30)
