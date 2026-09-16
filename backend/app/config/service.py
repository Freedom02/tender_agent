from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..store import Store


DEFAULT_MONITOR_SITES = [
    {
        "id": "government-procurement",
        "name": "中国政府采购网",
        "url": "https://www.ccgp.gov.cn",
        "enabled": True,
        "status": "待抓取",
        "latest": "",
        "next": "",
    },
    {
        "id": "public-resource",
        "name": "全国公共资源交易平台",
        "url": "https://www.ggzy.gov.cn",
        "enabled": True,
        "status": "待抓取",
        "latest": "",
        "next": "",
    },
    {
        "id": "service-platform",
        "name": "中国招标投标公共服务平台",
        "url": "https://www.cebpubservice.com",
        "enabled": False,
        "status": "已暂停",
        "latest": "",
        "next": "未安排",
    },
]


def default_monitoring_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "serviceType": "数字孪生",
        "region": "全国",
        "targetDirections": [],
        "targetScenarios": [],
        "targetProducts": [],
        "targetCapabilities": [],
        "synonyms": [],
        "targetProcurementTypes": [],
        "includeKeywords": [
            "数字孪生",
            "三维可视化",
            "智慧园区",
            "数据中心",
            "AI智能体",
            "本体数据治理"
        ],
        "excludeKeywords": [
            "中标公告",
            "成交公告",
            "结果公告",
            "废标公告",
            "终止公告"
        ],
        "frequencyMinutes": 30,
        "lookbackDays": 7,
        "maxKeywordsPerRun": 20,
        "maxPagesPerKeyword": 2,
        "maxGenericItems": 50,
        "fetchDetails": True,
        "demoFallback": False,
        "sites": deepcopy(DEFAULT_MONITOR_SITES),
        "stats": {
            "latestCount": 0,
            "latestMatchedCount": 0,
            "totalCollected": 0,
            "lastFetchedAt": "",
            "nextFetchedAt": "",
        },
    }


def default_notification_config() -> dict[str, Any]:
    return {
        "inAppEnabled": True,
        "wecomEnabled": False,
    }


def default_wecom_config() -> dict[str, Any]:
    return {
        "webhook": "",
        "configured": False,
    }


def default_ai_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "provider": "OpenAI 兼容接口",
        "baseUrl": "",
        "apiKey": "",
        "model": "",
        "organizationId": "",
        "configured": False,
        # 外部智能体（WorkBuddy）作为**提取层 Provider** 的通道配置。
        # 默认关闭：SDK / 授权未就绪时打开，会让每次分析都白等一轮超时再回退。
        # 逐键的含义见 `tender_matching/agent_bridge.py` 的模块注释与 DEFAULT_CONFIG。
        "agentBridge": {
            "enabled": False,
            "transport": "file",
            "runtimeDir": "runtime/agent_bridge",
            "httpEndpoint": "http://127.0.0.1:8911",
            # 只做"短等待命中"：Sidecar 是离线跑完一批的（单批 14~62 秒、整份文件
            # 可能几十批），分析接口是同步的，等不起完整一轮。正确用法是**先预热**
            # （投递任务包 → 跑 Sidecar → 结果写回 outbox），分析时直接命中快路径。
            # 未命中时这里短暂等待后回退到内置模型通道，保证接口不被拖死。
            "timeoutSeconds": 20,
            "pollIntervalSeconds": 1,
            "writePack": True,
            "packDir": "runtime/projects",
        },
    }


def ensure_config(config: dict[str, Any] | None) -> dict[str, Any]:
    result = deepcopy(config or {})
    monitoring = {**default_monitoring_config(), **result.get("monitoring", {})}
    monitoring["stats"] = {
        **default_monitoring_config()["stats"],
        **monitoring.get("stats", {}),
    }
    existing_sites = monitoring.get("sites") or []
    site_by_id = {str(site.get("id")): site for site in existing_sites if isinstance(site, dict)}
    builtin_sites = [
        {**site, **site_by_id.get(str(site["id"]), {})}
        for site in DEFAULT_MONITOR_SITES
    ]
    builtin_ids = {str(site["id"]) for site in DEFAULT_MONITOR_SITES}
    custom_sites = [
        site for site in existing_sites
        if isinstance(site, dict) and str(site.get("id")) not in builtin_ids and site.get("custom") is True
    ]
    monitoring["sites"] = [*builtin_sites, *custom_sites]

    result["monitoring"] = monitoring
    result["notifications"] = {**default_notification_config(), **result.get("notifications", {})}
    result["wecom"] = {**default_wecom_config(), **result.get("wecom", {})}
    result["wecom"]["configured"] = bool(result["wecom"].get("webhook"))
    result["ai"] = {**default_ai_config(), **result.get("ai", {})}
    result["ai"]["configured"] = bool(result["ai"].get("apiKey"))
    return result


def update_configuration(store: "Store", incoming: dict[str, Any]) -> dict[str, Any]:
    current = ensure_config(store.config)
    next_config = deepcopy(current)

    for key, value in incoming.items():
        if key in {"monitoring", "notifications", "wecom", "ai"} and isinstance(value, dict):
            next_config[key] = {**next_config.get(key, {}), **value}
        else:
            next_config[key] = value

    incoming_wecom = incoming.get("wecom")
    if isinstance(incoming_wecom, dict) and not incoming_wecom.get("webhook"):
        next_config["wecom"]["webhook"] = current.get("wecom", {}).get("webhook", "")

    incoming_ai = incoming.get("ai")
    if isinstance(incoming_ai, dict):
        incoming_key = str(incoming_ai.get("apiKey") or "").strip()
        if not incoming_key or incoming_key in {"***", "已配置"}:
            next_config["ai"]["apiKey"] = current.get("ai", {}).get("apiKey", "")

    next_config = ensure_config(next_config)
    store.save_config(next_config)
    return store.public_config()


def update_monitor_site(store: "Store", site_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    config = ensure_config(store.config)
    for site in config["monitoring"]["sites"]:
        if site["id"] == site_id:
            if "enabled" in payload:
                site["enabled"] = bool(payload["enabled"])
            for key in ("name", "url", "listUrl", "fetchMode", "selectors", "jsonFields"):
                if key in payload and payload[key] is not None:
                    site[key] = payload[key]
            if not site["enabled"]:
                site["status"] = "已暂停"
                site["next"] = "未安排"
            store.save_config(config)
            return site
    return None


def create_custom_monitor_site(store: "Store", payload: dict[str, Any]) -> dict[str, Any]:
    config = ensure_config(store.config)
    name = str(payload.get("name", "")).strip()
    url = str(payload.get("url", "")).strip()
    list_url = str(payload.get("listUrl", "") or url).strip()
    if not name or not url or not list_url:
        raise ValueError("网站名称、网站首页地址和公告列表地址不能为空")
    fetch_mode = str(payload.get("fetchMode", "html")).strip().lower()
    if fetch_mode not in {"html", "json"}:
        raise ValueError("抓取方式仅支持网页列表或 JSON/API")
    site = {
        "id": store.new_id("custom-site"),
        "name": name,
        "url": url,
        "listUrl": list_url,
        "fetchMode": fetch_mode,
        "enabled": bool(payload.get("enabled", True)),
        "custom": True,
        "status": "待测试",
        "latest": "",
        "next": "",
        "selectors": payload.get("selectors") or {},
        "jsonFields": payload.get("jsonFields") or {},
    }
    config["monitoring"]["sites"].append(site)
    store.save_config(config)
    return site


def delete_custom_monitor_site(store: "Store", site_id: str) -> bool:
    config = ensure_config(store.config)
    sites = config["monitoring"]["sites"]
    kept = [site for site in sites if not (site.get("id") == site_id and site.get("custom") is True)]
    if len(kept) == len(sites):
        return False
    config["monitoring"]["sites"] = kept
    store.save_config(config)
    return True


def public_configuration(config: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(ensure_config(config))
    result["wecom"]["webhook"] = "***" if result["wecom"].get("webhook") else ""
    result["wecom"]["configured"] = bool(config.get("wecom", {}).get("webhook"))
    result["ai"]["apiKey"] = "***" if result["ai"].get("apiKey") else ""
    result["ai"]["configured"] = bool(config.get("ai", {}).get("apiKey"))
    return result
