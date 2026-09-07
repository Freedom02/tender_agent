from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib import error, request
from urllib.parse import urljoin, urlparse


class CustomSiteError(RuntimeError):
    pass


def _valid_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _published_date(value: Any) -> str:
    text = _clean_text(value)
    match = re.search(r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})", text)
    if not match:
        return text[:20]
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


class _Node:
    def __init__(self, tag: str = "root", attrs: dict[str, str] | None = None) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[_Node | str] = []

    def text(self) -> str:
        return _clean_text(" ".join(child.text() if isinstance(child, _Node) else child for child in self.children))


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node()
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs})
        self.stack[-1].children.append(node)
        if tag.lower() not in {"meta", "link", "img", "input", "br", "hr", "source"}:
            self.stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                break

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.stack[-1].children.append(data)


def _walk(node: _Node) -> list[_Node]:
    result: list[_Node] = []
    for child in node.children:
        if isinstance(child, _Node):
            result.append(child)
            result.extend(_walk(child))
    return result


def _matches(node: _Node, selector: str) -> bool:
    selector = _clean_text(selector)
    if not selector or any(char in selector for char in " >+~[]:"):
        return False
    selector = selector.split(",", 1)[0].strip()
    tag = ""
    class_name = ""
    node_id = ""
    if selector.startswith("#"):
        node_id = selector[1:]
    elif selector.startswith("."):
        class_name = selector[1:]
    elif "." in selector:
        tag, class_name = selector.split(".", 1)
    elif "#" in selector:
        tag, node_id = selector.split("#", 1)
    else:
        tag = selector
    if tag and node.tag != tag.lower():
        return False
    if node_id and node.attrs.get("id", "") != node_id:
        return False
    if class_name and class_name not in node.attrs.get("class", "").split():
        return False
    return True


def _find(node: _Node, selector: str) -> list[_Node]:
    return [child for child in _walk(node) if _matches(child, selector)]


def _first(node: _Node, selector: str) -> _Node | None:
    return next(iter(_find(node, selector)), None)


def _href(node: _Node, base_url: str) -> str:
    href = node.attrs.get("href", "")
    return urljoin(base_url, href) if href else ""


def _first_link(node: _Node, base_url: str) -> str:
    if node.tag == "a":
        return _href(node, base_url)
    link = next((item for item in _walk(node) if item.tag == "a" and item.attrs.get("href")), None)
    return _href(link, base_url) if link else ""


def _fetch(url: str) -> tuple[str, str]:
    if not _valid_url(url):
        raise CustomSiteError("网站地址必须是有效的 HTTP 或 HTTPS 地址")
    req = request.Request(
        url,
        headers={
            "User-Agent": "TenderAgent/2.0 (+public-notice-monitor)",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with request.urlopen(req, timeout=15) as response:
            content_type = response.headers.get_content_type()
            body = response.read(3 * 1024 * 1024)
            charset = response.headers.get_content_charset() or "utf-8"
            return body.decode(charset, errors="replace"), content_type
    except (error.URLError, OSError, UnicodeError) as exc:
        raise CustomSiteError(f"访问网站失败：{exc}") from exc


def _json_path(value: Any, path: str) -> Any:
    current = value
    for part in str(path or "").strip(".").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _json_items(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "results", "records", "data", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
        return [payload]
    return []


def _stable_id(site_id: str, url: str, title: str, published_date: str) -> str:
    raw = f"{site_id}|{url}|{title}|{published_date}".encode("utf-8")
    return f"custom-{hashlib.sha1(raw).hexdigest()[:16]}"


def _normalize(
    site: dict[str, Any],
    title: Any,
    detail_url: Any,
    published: Any = "",
    region: Any = "",
    category: Any = "",
    content: Any = "",
) -> dict[str, Any] | None:
    title_text = _clean_text(title)
    if not title_text or len(title_text) < 2:
        return None
    base_url = str(site.get("url", ""))
    link = urljoin(base_url, _clean_text(detail_url)) if detail_url else base_url
    date_text = _published_date(published)
    return {
        "id": _stable_id(str(site["id"]), link, title_text, date_text),
        "title": title_text,
        "category": _clean_text(category) or "待分类",
        "region": _clean_text(region) or "未识别",
        "noticeType": "招标公告",
        "publishedDate": date_text,
        "source": str(site.get("name", "自定义监控网站")),
        "sourceUrl": link,
        "detailUrl": link,
        "analysisText": _clean_text(content) or title_text,
        "sourceKey": str(site["id"]),
        "dataOrigin": "custom_site",
    }


def _parse_html(site: dict[str, Any], html: str) -> list[dict[str, Any]]:
    parser = _TreeParser()
    parser.feed(html)
    selectors = site.get("selectors") or {}
    item_selector = str(selectors.get("item", "")).strip()
    containers = _find(parser.root, item_selector) if item_selector else []
    if not containers:
        containers = [
            node for node in _walk(parser.root)
            if node.tag in {"article", "li", "tr"} and _first_link(node, str(site.get("url", "")))
        ]
    if not containers:
        containers = [node for node in _walk(parser.root) if node.tag == "a" and node.attrs.get("href")]

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for container in containers:
        title_node = _first(container, str(selectors.get("title", ""))) if selectors.get("title") else None
        date_node = _first(container, str(selectors.get("date", ""))) if selectors.get("date") else None
        link_node = _first(container, str(selectors.get("link", ""))) if selectors.get("link") else None
        region_node = _first(container, str(selectors.get("region", ""))) if selectors.get("region") else None
        category_node = _first(container, str(selectors.get("category", ""))) if selectors.get("category") else None
        content_node = _first(container, str(selectors.get("content", ""))) if selectors.get("content") else None
        title = title_node.text() if title_node else (container.text() if container.tag != "a" else container.text())
        detail_url = _href(link_node, str(site.get("url", ""))) if link_node else _first_link(container, str(site.get("url", "")))
        item = _normalize(
            site,
            title,
            detail_url,
            date_node.text() if date_node else container.text(),
            region_node.text() if region_node else "",
            category_node.text() if category_node else "",
            content_node.text() if content_node else container.text(),
        )
        if item and item["sourceUrl"] not in seen:
            seen.add(item["sourceUrl"])
            result.append(item)
    return result


def _parse_json(site: dict[str, Any], raw: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CustomSiteError("JSON/API 返回内容无法解析") from exc
    fields = site.get("jsonFields") or {}
    items_path = str(fields.get("items", "")).strip()
    rows = _json_path(payload, items_path) if items_path else payload
    result: list[dict[str, Any]] = []
    for row in _json_items(rows):
        if not isinstance(row, dict):
            continue
        title = _json_path(row, fields.get("title", "title")) or row.get("name")
        link = _json_path(row, fields.get("link", "url")) or row.get("href")
        item = _normalize(
            site,
            title,
            link,
            _json_path(row, fields.get("date", "publishedDate")) or row.get("date"),
            _json_path(row, fields.get("region", "region")),
            _json_path(row, fields.get("category", "category")),
            _json_path(row, fields.get("content", "summary")) or row.get("description"),
        )
        if item:
            result.append(item)
    return result


def fetch_custom_site(site: dict[str, Any]) -> list[dict[str, Any]]:
    list_url = str(site.get("listUrl") or site.get("url") or "").strip()
    raw, content_type = _fetch(list_url)
    mode = str(site.get("fetchMode", "html")).lower()
    if mode == "json" or "json" in content_type:
        return _parse_json(site, raw)
    return _parse_html(site, raw)


def test_custom_site(site: dict[str, Any]) -> dict[str, Any]:
    items = fetch_custom_site(site)
    return {
        "success": True,
        "count": len(items),
        "items": items[:10],
        "status": "连接成功",
    }
