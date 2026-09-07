from __future__ import annotations

import json
from typing import Any


def _keywords(value: Any) -> list[str]:
    if isinstance(value, str):
        return [item.strip().lower() for item in value.replace("，", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip().lower() for item in value if str(item).strip()]
    return []


def _notice_text(item: dict[str, Any]) -> str:
    fields = [
        item.get("title", ""),
        item.get("category", ""),
        item.get("serviceType", ""),
        item.get("region", ""),
        item.get("serviceLocation", ""),
        item.get("noticeType", ""),
        item.get("analysisText", ""),
    ]
    return " ".join(str(value) for value in fields).lower()


def matches_monitoring_profile(item: dict[str, Any], monitoring: dict[str, Any]) -> bool:
    text = _notice_text(item)
    service_type = str(monitoring.get("serviceType", "")).strip().lower()
    region = str(monitoring.get("region", "")).strip().lower()
    include_keywords = _keywords(monitoring.get("includeKeywords"))
    exclude_keywords = _keywords(monitoring.get("excludeKeywords"))

    if service_type and service_type not in {"全国", "不限"} and service_type not in text:
        return False
    if region and region not in {"全国", "不限"} and region not in text:
        return False
    if include_keywords and not any(keyword in text for keyword in include_keywords):
        return False
    if any(keyword in text for keyword in exclude_keywords):
        return False
    return True


def filter_catalog_by_monitoring(catalog: list[dict[str, Any]], monitoring: dict[str, Any]) -> list[dict[str, Any]]:
    if monitoring.get("enabled") is False:
        return []
    return [item for item in catalog if matches_monitoring_profile(item, monitoring)]


def matches_manual_search(item: dict[str, Any], payload: dict[str, Any]) -> bool:
    text = json.dumps(item, ensure_ascii=False).lower()
    query = str(payload.get("query", "")).strip().lower()
    region = str(payload.get("region", "")).strip().lower()
    service_type = str(payload.get("type", "")).strip().lower()
    if query and query not in text:
        return False
    if region and region not in f"{item.get('region', '')}{item.get('serviceLocation', '')}".lower():
        return False
    if service_type and service_type not in f"{item.get('category', '')}{item.get('serviceType', '')}".lower():
        return False
    return True
