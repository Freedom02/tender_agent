from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from ..config.service import ensure_config
from ..notification.service import notify_new_projects
from ..tender_matching.service import filter_catalog_by_monitoring, matches_manual_search, matches_monitoring_profile
from .custom_site import CustomSiteError, fetch_custom_site, test_custom_site

if TYPE_CHECKING:
    from ..store import Store


def _format_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _source_matches(item: dict[str, Any], site_id: str) -> bool:
    source = str(item.get("source", "")).lower()
    if site_id == "government-procurement":
        return "政府采购" in source or "ccgp" in str(item.get("sourceUrl", "")).lower()
    if site_id == "public-resource":
        return "公共资源" in source or "ggzy" in str(item.get("sourceUrl", "")).lower()
    if site_id == "service-platform":
        return "招标投标公共服务" in source or "cebpubservice" in str(item.get("sourceUrl", "")).lower()
    return False


def _fixture_notices(site: dict[str, Any]) -> list[dict[str, Any]]:
    today = datetime.now().astimezone().date().isoformat()
    source_id = str(site["id"])
    source_name = str(site["name"])
    url = str(site["url"]).rstrip("/")
    fixtures = {
        "government-procurement": [
            {
                "id": "monitor-ccgp-labor-2026",
                "title": "某市公共服务中心劳务派遣服务采购项目",
                "category": "劳务派遣",
                "region": "全国",
                "noticeType": "公开招标公告",
                "publishedDate": today,
                "source": source_name,
                "sourceUrl": f"{url}/cggg/index.htm",
                "analysisText": "项目名称：某市公共服务中心劳务派遣服务采购项目。服务类型：劳务派遣、人力资源服务。服务地点：全国。公告类型：公开招标公告。",
            },
            {
                "id": "monitor-ccgp-security-2026",
                "title": "某医院秩序维护服务采购项目",
                "category": "保安服务",
                "region": "北京市",
                "noticeType": "公开招标公告",
                "publishedDate": today,
                "source": source_name,
                "sourceUrl": f"{url}/cggg/index.htm",
                "analysisText": "项目名称：某医院秩序维护服务采购项目。服务类型：保安服务。服务地点：北京市。",
            },
        ],
        "public-resource": [
            {
                "id": "monitor-ggzy-hr-2026",
                "title": "某产业园区人力资源外包服务项目",
                "category": "人力资源服务",
                "region": "全国",
                "noticeType": "招标公告",
                "publishedDate": today,
                "source": source_name,
                "sourceUrl": f"{url}/information/",
                "analysisText": "项目名称：某产业园区人力资源外包服务项目。服务类型：人力资源服务、劳务派遣。服务地点：全国。",
            }
        ],
        "service-platform": [
            {
                "id": "monitor-cebp-labor-2026",
                "title": "某集团劳务派遣服务项目",
                "category": "劳务派遣",
                "region": "广东省",
                "noticeType": "招标公告",
                "publishedDate": today,
                "source": source_name,
                "sourceUrl": url,
                "analysisText": "项目名称：某集团劳务派遣服务项目。服务类型：劳务派遣。服务地点：广东省。",
            }
        ],
    }
    return [dict(item, sourceKey=source_id, dataOrigin="monitor_fixture") for item in fixtures.get(source_id, [])]


def _candidate_notices(store: "Store", site: dict[str, Any]) -> list[dict[str, Any]]:
    if site.get("custom") is True:
        return fetch_custom_site(site)
    seed_catalog = store.seed_catalog()
    source_records = [item for item in seed_catalog if _source_matches(item, str(site["id"]))]
    return source_records or _fixture_notices(site)


def _dedupe_key(item: dict[str, Any]) -> str:
    return str(item.get("sourceUrl") or f"{item.get('title', '')}|{item.get('publishedDate', '')}").strip().lower()


def _existing_keys(store: "Store") -> set[str]:
    return {_dedupe_key(item) for item in store.catalog}


def _next_time(minutes: int) -> str:
    return _format_time(datetime.now().astimezone() + timedelta(minutes=minutes))


def monitoring_status(store: "Store") -> dict[str, Any]:
    config = ensure_config(store.config)
    monitoring = config["monitoring"]
    stats = monitoring["stats"]
    frequency = max(int(monitoring.get("frequencyMinutes", 30) or 30), 1)
    sites = deepcopy(monitoring["sites"])
    for site in sites:
        if site.get("enabled") and not site.get("next"):
            site["next"] = _next_time(frequency)
        if not site.get("enabled"):
            site["status"] = "已暂停"
            site["next"] = "未安排"
    return {
        "enabled": bool(monitoring.get("enabled", True)),
        "frequencyMinutes": frequency,
        "sites": sites,
        "stats": stats,
    }


def run_monitoring(store: "Store", site_id: str = "") -> dict[str, Any]:
    config = ensure_config(store.config)
    monitoring = config["monitoring"]
    if not monitoring.get("enabled", True):
        return {"status": "监控已暂停", "addedCount": 0, "matchedCount": 0, "dedupedCount": 0, "items": []}

    sites = monitoring["sites"]
    targets = [site for site in sites if site.get("enabled") and (not site_id or site["id"] == site_id)]
    if site_id and not targets:
        return {"status": "监控网站未启用", "addedCount": 0, "matchedCount": 0, "dedupedCount": 0, "items": []}

    existing_keys = _existing_keys(store)
    added: list[dict[str, Any]] = []
    matched_count = 0
    deduped_count = 0
    collected_count = 0
    site_results: list[dict[str, Any]] = []
    fetched_at = store.current_time()
    frequency = max(int(monitoring.get("frequencyMinutes", 30) or 30), 1)

    for site in targets:
        try:
            candidates = _candidate_notices(store, site)
        except CustomSiteError as exc:
            site["status"] = f"抓取失败：{str(exc)[:80]}"
            site["latest"] = fetched_at[:16].replace("T", " ")
            site_results.append({"siteId": site["id"], "name": site["name"], "status": site["status"], "count": 0})
            continue
        collected_count += len(candidates)
        for candidate in candidates:
            if not matches_monitoring_profile(candidate, monitoring):
                continue
            matched_count += 1
            key = _dedupe_key(candidate)
            if key in existing_keys:
                deduped_count += 1
                continue
            item = {
                **candidate,
                "id": candidate.get("id") or store.new_id("monitor"),
                "fetchedAt": fetched_at,
                "reminderStatus": "未提醒",
                "notificationChannels": [],
                "followUp": False,
                "read": False,
            }
            store.catalog.insert(0, item)
            existing_keys.add(key)
            added.append(item)

        site["latest"] = fetched_at[:16].replace("T", " ")
        site["next"] = _next_time(frequency)
        site["status"] = "运行正常"
        site_results.append({"siteId": site["id"], "name": site["name"], "status": site["status"], "count": len(candidates)})

    notification_result = notify_new_projects(store, added)
    stats = monitoring["stats"]
    stats["latestCount"] = matched_count
    stats["totalCollected"] = int(stats.get("totalCollected", 0) or 0) + collected_count
    stats["lastFetchedAt"] = fetched_at[:16].replace("T", " ")
    stats["nextFetchedAt"] = _next_time(frequency)
    store.save_config(config)
    store.persist_catalog()
    store.persist_runtime()

    return {
        "status": "抓取完成",
        "addedCount": len(added),
        "matchedCount": matched_count,
        "dedupedCount": deduped_count,
        "items": [store.with_preview(item) for item in added],
        "sites": site_results,
        "monitoring": monitoring_status(store),
    }


def test_monitor_site(site: dict[str, Any]) -> dict[str, Any]:
    if site.get("custom") is not True:
        return {"success": False, "status": "预置平台", "detail": "预置平台使用专用适配器，无需配置自定义解析规则。"}
    try:
        return test_custom_site(site)
    except CustomSiteError as exc:
        return {"success": False, "status": "连接失败", "detail": str(exc), "count": 0, "items": []}


def search_catalog(store: "Store", payload: dict[str, Any]) -> dict[str, Any]:
    config = ensure_config(store.config)
    monitored = filter_catalog_by_monitoring(store.catalog, config["monitoring"])
    items = [
        store.with_preview(item)
        for item in monitored
        if matches_manual_search(item, payload)
    ]
    return {
        "items": items,
        "search": {
            "searchId": store.new_id("search"),
            "fetchedAt": store.current_time(),
            "verifiedCount": len(items),
            "addedCount": 0,
            "dedupedCount": 0,
            "sources": [{"name": "已配置监控网站", "status": "已筛选", "count": len(items)}],
        },
    }
