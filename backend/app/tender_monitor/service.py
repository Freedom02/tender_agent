from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..config.service import ensure_config
from ..notification.service import notify_new_projects
from ..tender_matching.service import evaluate_company_relevance, matches_manual_search
from .adapters import MonitorAdapterError, adapter_for, is_procurement_notice
from .custom_site import CustomSiteError, fetch_custom_site, test_custom_site

if TYPE_CHECKING:
    from ..store import Store


def _format_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y-%m-%d %H:%M")


def _fixture_notices(site: dict[str, Any]) -> list[dict[str, Any]]:
    today = datetime.now().astimezone().date().isoformat()
    source_id = str(site["id"])
    source_name = str(site["name"])
    url = str(site["url"]).rstrip("/")
    fixtures = {
        "government-procurement": [
            {
                "id": "monitor-ccgp-digitaltwin-2026",
                "title": "某市智慧园区数字孪生平台建设项目",
                "category": "数字孪生",
                "region": "全国",
                "noticeType": "公开招标公告",
                "publishedDate": today,
                "source": source_name,
                "sourceUrl": f"{url}/cggg/index.htm",
                "analysisText": "项目名称：某市智慧园区数字孪生平台建设项目。服务类型：数字孪生、智慧园区。服务地点：全国。公告类型：公开招标公告。",
            },
            {
                "id": "monitor-ccgp-datacenter-2026",
                "title": "某数据中心可视化运维管理项目",
                "category": "数据中心",
                "region": "北京市",
                "noticeType": "公开招标公告",
                "publishedDate": today,
                "source": source_name,
                "sourceUrl": f"{url}/cggg/index.htm",
                "analysisText": "项目名称：某数据中心可视化运维管理项目。服务类型：数据中心与运维。服务地点：北京市。",
            },
        ],
        "public-resource": [
            {
                "id": "monitor-ggzy-dataintel-2026",
                "title": "某企业数据智能分析平台采购项目",
                "category": "数据智能",
                "region": "全国",
                "noticeType": "招标公告",
                "publishedDate": today,
                "source": source_name,
                "sourceUrl": f"{url}/information/",
                "analysisText": "项目名称：某企业数据智能分析平台采购项目。服务类型：数据智能、AI 智能体。服务地点：全国。",
            }
        ],
        "service-platform": [
            {
                "id": "monitor-cebp-lowalt-2026",
                "title": "某市低空经济态势感知平台项目",
                "category": "低空经济",
                "region": "广东省",
                "noticeType": "招标公告",
                "publishedDate": today,
                "source": source_name,
                "sourceUrl": url,
                "analysisText": "项目名称：某市低空经济态势感知平台项目。服务类型：低空经济、数字孪生。服务地点：广东省。",
            }
        ],
    }
    return [dict(item, sourceKey=source_id, dataOrigin="monitor_fixture") for item in fixtures.get(source_id, [])]


def _candidate_notices(store: "Store", site: dict[str, Any]) -> list[dict[str, Any]]:
    if site.get("custom") is True:
        return fetch_custom_site(site)
    try:
        return adapter_for(site).fetch(site, ensure_config(store.config)["monitoring"])
    except MonitorAdapterError:
        if ensure_config(store.config)["monitoring"].get("demoFallback") is True:
            return _fixture_notices(site)
        raise


def _canonical_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value.strip())
    removable = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "_"}
    query = urlencode([(key, val) for key, val in parse_qsl(parsed.query, keep_blank_values=True) if key not in removable])
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.rstrip("/"), query, ""))


def _dedupe_key(item: dict[str, Any]) -> str:
    url = _canonical_url(str(item.get("sourceUrl") or item.get("detailUrl") or ""))
    if url:
        return url
    source = str(item.get("source") or item.get("sourceKey") or "").strip().lower()
    return f"{source}|{item.get('title', '')}|{item.get('publishedDate', '')}".strip().lower()


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
        site_matched_count = 0
        site_added_count = 0
        site_deduped_count = 0
        try:
            candidates = _candidate_notices(store, site)
        except (CustomSiteError, MonitorAdapterError) as exc:
            site["status"] = f"抓取失败：{str(exc)[:80]}"
            site["latest"] = fetched_at[:16].replace("T", " ")
            site_results.append({"siteId": site["id"], "name": site["name"], "status": site["status"], "count": 0})
            continue
        collected_count += len(candidates)
        for candidate in candidates:
            if not is_procurement_notice(
                str(candidate.get("title", "")),
                str(candidate.get("analysisText", "")),
                str(candidate.get("sourceUrl") or candidate.get("detailUrl") or ""),
            ):
                continue
            match = evaluate_company_relevance(candidate, store.company_knowledge, monitoring)
            if not match["matched"]:
                continue
            matched_count += 1
            site_matched_count += 1
            key = _dedupe_key(candidate)
            if key in existing_keys:
                deduped_count += 1
                site_deduped_count += 1
                continue
            item = {
                **candidate,
                "id": candidate.get("id") or store.new_id("monitor"),
                "fetchedAt": fetched_at,
                "relevanceScore": match["score"],
                "matchedBusinessDirections": match["matchedBusinessDirections"],
                "matchedScenarios": match["matchedScenarios"],
                "matchedProducts": match["matchedProducts"],
                "matchedCapabilities": match["matchedCapabilities"],
                "matchedKeywords": match["matchedKeywords"],
                "matchReasons": match["reasons"],
                "excludeReason": match["excludeReason"],
                "nextStage": match["nextStage"],
                "shouldAnalyze": match["shouldAnalyze"],
                "searchProfile": {
                    "serviceType": monitoring.get("serviceType", ""),
                    "region": monitoring.get("region", ""),
                    "includeKeywords": deepcopy(monitoring.get("includeKeywords", [])),
                    "excludeKeywords": deepcopy(monitoring.get("excludeKeywords", [])),
                    "targetDirections": deepcopy(monitoring.get("targetDirections", [])),
                    "targetScenarios": deepcopy(monitoring.get("targetScenarios", [])),
                    "targetProducts": deepcopy(monitoring.get("targetProducts", [])),
                    "targetCapabilities": deepcopy(monitoring.get("targetCapabilities", [])),
                    "synonyms": deepcopy(monitoring.get("synonyms", [])),
                    "targetProcurementTypes": deepcopy(monitoring.get("targetProcurementTypes", [])),
                    "lookbackDays": monitoring.get("lookbackDays", 7),
                },
                "crawlMeta": {
                    "siteId": site.get("id"),
                    "siteName": site.get("name"),
                    "dedupeKey": key,
                    "fetchedAt": fetched_at,
                },
                "reminderStatus": "未提醒",
                "notificationChannels": [],
                "followUp": False,
                "read": False,
            }
            store.catalog.insert(0, item)
            existing_keys.add(key)
            added.append(item)
            site_added_count += 1

        site["latest"] = fetched_at[:16].replace("T", " ")
        site["next"] = _next_time(frequency)
        site["status"] = "运行正常"
        site_results.append({
            "siteId": site["id"],
            "name": site["name"],
            "status": site["status"],
            "count": len(candidates),
            "matchedCount": site_matched_count,
            "addedCount": site_added_count,
            "dedupedCount": site_deduped_count,
        })

    notification_result = notify_new_projects(store, added)
    stats = monitoring["stats"]
    stats["latestCount"] = len(added)
    stats["latestMatchedCount"] = matched_count
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
        "collectedCount": collected_count,
        "items": [store.with_preview(item) for item in added],
        "sites": site_results,
        "monitoring": monitoring_status(store),
    }


def test_monitor_site(site: dict[str, Any]) -> dict[str, Any]:
    if site.get("custom") is not True:
        try:
            monitoring = ensure_config({})["monitoring"]
            monitoring["includeKeywords"] = monitoring.get("includeKeywords", [])[:1]
            monitoring["maxKeywordsPerRun"] = 1
            monitoring["maxPagesPerKeyword"] = 1
            monitoring["fetchDetails"] = False
            items = adapter_for(site).fetch(site, monitoring)
            return {"success": True, "status": "试抓成功", "count": len(items), "items": items[:10]}
        except MonitorAdapterError as exc:
            return {"success": False, "status": "连接失败", "detail": str(exc), "count": 0, "items": []}
    try:
        return test_custom_site(site)
    except CustomSiteError as exc:
        return {"success": False, "status": "连接失败", "detail": str(exc), "count": 0, "items": []}


def search_catalog(store: "Store", payload: dict[str, Any]) -> dict[str, Any]:
    valid_items = [
        item for item in store.catalog
        if is_procurement_notice(
            str(item.get("title", "")),
            str(item.get("analysisText", "")),
            str(item.get("sourceUrl") or item.get("detailUrl") or ""),
        )
    ]
    monitored = sorted(
        valid_items,
        key=lambda item: str(item.get("fetchedAt") or item.get("publishedDate") or ""),
        reverse=True,
    )
    if payload.get("currentOnly"):
        today = datetime.now().astimezone().date().isoformat()
        monitored = [item for item in monitored if str(item.get("publishedDate", "")) >= today]
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
            "sources": [{"name": "已入库项目", "status": "已筛选", "count": len(items)}],
        },
    }
