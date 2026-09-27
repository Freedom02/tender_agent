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
        # 清空历史后首次启动不应立即把监控项目重新写回列表。
        # 用户可以通过“立即抓取”主动开始第一轮抓取；已有抓取记录
        # 的项目仍按 frequencyMinutes 进入后续自动调度。
        return False
    try:
        last_time = datetime.strptime(last_fetched, "%Y-%m-%d %H:%M").astimezone()
    except ValueError:
        return True
    frequency = max(int(monitoring.get("frequencyMinutes", 30) or 30), 1)
    return (datetime.now().astimezone() - last_time).total_seconds() >= frequency * 60


async def monitoring_loop(store: "Store") -> None:
    while True:
        if _is_due(store):
            await asyncio.to_thread(run_monitoring, store)
        await asyncio.sleep(30)
