from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.app.config.service import ensure_config
from backend.app.tender_monitor.adapters import MonitorAdapterError
from backend.app.tender_monitor.service import run_monitoring


class FakeStore:
    def __init__(self, sites: list[dict[str, object]]) -> None:
        requested_ids = {str(item["id"]) for item in sites}
        self.config = ensure_config(
            {
                "monitoring": {
                    "sites": sites,
                    "fetchDetails": False,
                    "demoFallback": False,
                }
            }
        )
        for configured_site in self.config["monitoring"]["sites"]:
            if configured_site["id"] not in requested_ids:
                configured_site["enabled"] = False
        self.catalog: list[dict[str, object]] = []
        self.company_knowledge: dict[str, object] = {}
        self.saved_config = False
        self.persisted_catalog = False
        self.persisted_runtime = False

    def current_time(self) -> str:
        return "2026-09-12T12:00:00+08:00"

    def new_id(self, prefix: str) -> str:
        return f"{prefix}-test"

    def with_preview(self, item: dict[str, object]) -> dict[str, object]:
        return item

    def save_config(self, config: dict[str, object]) -> None:
        self.config = config
        self.saved_config = True

    def persist_catalog(self) -> None:
        self.persisted_catalog = True

    def persist_runtime(self) -> None:
        self.persisted_runtime = True


def site(site_id: str) -> dict[str, object]:
    return {
        "id": site_id,
        "name": site_id,
        "url": f"https://{site_id}.example.test",
        "enabled": True,
        "custom": True,
        "status": "待抓取",
        "latest": "",
        "next": "",
    }


class MonitoringRunTests(unittest.TestCase):
    def test_all_site_failures_are_reported_as_failure(self) -> None:
        store = FakeStore([site("government-procurement"), site("public-resource")])

        with patch(
            "backend.app.tender_monitor.service._candidate_notices",
            side_effect=MonitorAdapterError("网络不可用"),
        ):
            result = run_monitoring(store)

        self.assertEqual(result["status"], "抓取失败")
        self.assertEqual(result["collectedCount"], 0)
        self.assertEqual(len(result["sites"]), 2)
        self.assertTrue(all(item["success"] is False for item in result["sites"]))
        self.assertTrue(all(item["error"] == "网络不可用" for item in result["sites"]))
        self.assertTrue(store.saved_config)

    def test_one_failed_site_does_not_block_successful_sites(self) -> None:
        store = FakeStore([site("government-procurement"), site("public-resource")])

        def candidates(current_store: FakeStore, current_site: dict[str, object]) -> list[dict[str, object]]:
            if current_site["id"] == "government-procurement":
                raise MonitorAdapterError("站点超时")
            return []

        with patch(
            "backend.app.tender_monitor.service._candidate_notices",
            side_effect=candidates,
        ):
            result = run_monitoring(store)

        self.assertEqual(result["status"], "部分站点抓取完成")
        self.assertEqual([item["success"] for item in result["sites"]], [False, True])
        self.assertEqual(result["sites"][1]["count"], 0)

    def test_empty_successful_fetch_is_not_reported_as_match(self) -> None:
        store = FakeStore([site("government-procurement")])

        with patch(
            "backend.app.tender_monitor.service._candidate_notices",
            return_value=[],
        ):
            result = run_monitoring(store)

        self.assertEqual(result["status"], "抓取完成但未获取到公告")
        self.assertEqual(result["matchedCount"], 0)
        self.assertEqual(result["addedCount"], 0)
        self.assertTrue(result["sites"][0]["success"])

    def test_fetched_but_unmatched_notices_are_explained(self) -> None:
        store = FakeStore([site("government-procurement")])
        candidate = {
            "id": "notice-1",
            "title": "医院食堂服务项目采购公告",
            "analysisText": "项目名称：医院食堂服务项目采购公告。",
            "sourceUrl": "https://www.ccgp.gov.cn/notice-1",
        }

        with patch(
            "backend.app.tender_monitor.service._candidate_notices",
            return_value=[candidate],
        ):
            result = run_monitoring(store)

        self.assertEqual(result["status"], "抓取完成但没有匹配公告")
        self.assertEqual(result["collectedCount"], 1)
        self.assertEqual(result["matchedCount"], 0)
        self.assertEqual(result["items"], [])


if __name__ == "__main__":
    unittest.main()
