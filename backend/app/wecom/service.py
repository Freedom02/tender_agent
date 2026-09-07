from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from urllib import error, request
from urllib.parse import urlparse

if TYPE_CHECKING:
    from ..store import Store


def _webhook(store: "Store") -> str:
    return str(store.config.get("wecom", {}).get("webhook", "")).strip()


def _valid_webhook(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc == "qyapi.weixin.qq.com"
        and parsed.path == "/cgi-bin/webhook/send"
        and "key=" in parsed.query
    )


def send_text(store: "Store", content: str) -> dict[str, Any]:
    webhook = _webhook(store)
    if not webhook:
        return {"sent": False, "status": "未配置 Webhook", "detail": "请先填写企业微信机器人 Webhook 地址。"}
    if not _valid_webhook(webhook):
        return {"sent": False, "status": "Webhook 格式错误", "detail": "仅支持企业微信机器人 HTTPS Webhook 地址。"}

    payload = json.dumps({"msgtype": "text", "text": {"content": content}}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            body = json.loads(response.read().decode("utf-8"))
        if body.get("errcode") == 0:
            return {"sent": True, "status": "发送成功", "detail": "企业微信机器人已接收测试消息。"}
        return {"sent": False, "status": "发送失败", "detail": str(body.get("errmsg", "企业微信返回异常"))}
    except (error.URLError, OSError, ValueError) as exc:
        return {"sent": False, "status": "发送失败", "detail": str(exc)}


def test_webhook(store: "Store") -> dict[str, Any]:
    return send_text(store, "【标智通】企业微信机器人测试成功。后续符合筛选条件的新招标公告将发送到本群。")
