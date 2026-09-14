from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib import error, parse, request


class MonitorAdapterError(RuntimeError):
    pass


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def normalize_date(value: Any) -> str:
    text = clean_text(value)
    match = re.search(r"(20\d{2})[年./:-](\d{1,2})[月./:-](\d{1,2})", text)
    if not match:
        return ""
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def stable_id(source_key: str, url: str, title: str, published_date: str) -> str:
    raw = f"{source_key}|{url}|{title}|{published_date}".encode("utf-8")
    return f"notice-{hashlib.sha1(raw).hexdigest()[:16]}"


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0
        self.block_tags = {
            "address", "article", "aside", "blockquote", "br", "dd", "div",
            "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form",
            "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li",
            "main", "nav", "ol", "p", "pre", "section", "table", "tbody",
            "td", "tfoot", "th", "thead", "tr", "ul",
        }

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self.skip_depth += 1
            return
        if not self.skip_depth and tag.lower() in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if not self.skip_depth and tag.lower() in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth and data.strip():
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = _TextParser()
    parser.feed(html)
    raw_text = "".join(parser.parts)
    lines = [clean_text(line) for line in raw_text.splitlines()]
    return "\n".join(line for line in lines if line)


NOTICE_STRUCTURE_MARKERS = (
    "项目基本情况", "项目概况", "采购项目用途", "采购需求", "采购内容",
    "采购范围", "主要标的", "技术要求", "服务内容", "申请人的资格要求",
    "投标人的资格要求", "供应商资格要求", "资格审查", "资格条件",
    "获取招标文件", "获取采购文件", "招标文件的获取", "报名时间",
    "提交投标文件截止时间", "投标文件截止时间", "投标文件递交",
    "响应文件提交", "响应文件递交", "开标时间", "联系方式",
    "采购人信息", "代理机构信息", "中标情况", "成交情况",
)

NOTICE_FIELD_MARKERS = (
    "采购项目名称", "招标项目名称", "项目名称", "采购项目编号", "招标项目编号",
    "项目编号", "采购编号", "招标编号", "采购方式", "招标方式",
    "采购人名称", "招标人名称", "采购单位", "采购人", "招标人",
    "采购代理机构名称", "招标代理机构名称", "代理机构名称",
    "预算金额", "采购预算", "项目预算", "最高限价", "控制价",
    "服务类型", "项目类型", "采购品目", "服务地点", "实施地点",
    "交付地点", "建设地点", "项目地点", "交货地点", "合同履行期限",
    "服务期限", "服务期", "供货期", "交货期", "投标截止时间",
    "响应文件提交截止时间", "递交响应文件截止时间", "提交投标文件截止时间",
    "开标时间", "开启时间", "获取招标文件时间", "获取采购文件时间",
    "获取文件时间", "报名时间", "联合体投标",
)


def normalize_notice_text(value: Any) -> str:
    """Keep notice paragraphs/tables readable and restore common lost boundaries.

    Crawlers and custom selectors often return a visually structured page as one
    text node. Matching relies on line/section boundaries, so recover boundaries
    before the text reaches the extractor. This is intentionally conservative:
    it only inserts breaks before known notice headings/field labels.
    """
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    if re.search(r"</?[A-Za-z][^>]*>", text):
        text = html_to_text(text)

    markers = sorted(
        set(NOTICE_STRUCTURE_MARKERS + NOTICE_FIELD_MARKERS),
        key=len,
        reverse=True,
    )
    marker_pattern = "|".join(re.escape(marker) for marker in markers)
    # Recover numbered fields that were concatenated with the preceding
    # section/value. Do not match digits that are part of an identifier or
    # date-like value.
    text = re.sub(
        r"(?<!^)(?<!\n)(?<![-A-Za-z0-9])"
        r"(?=\d+(?:\.\d+)*[、.．)])",
        "\n",
        text,
    )
    # A label immediately following another value is the usual signature of a
    # flattened list/table cell. Keep a real line start untouched.
    text = re.sub(
        rf"(?<!^)(?<!\n)(?<!\d[、.．)])(?<![一二三四五六七八九十百][、.．])"
        rf"(?=(?:{marker_pattern})\s*[:：])",
        "\n",
        text,
    )
    text = re.sub(
        r"(?<!^)(?<!\n)(?=(?:第[一二三四五六七八九十百]+[章节部分]|"
        r"[一二三四五六七八九十]+[、.．]))",
        "\n",
        text,
    )
    # Some list pages concatenate a section heading and its first numbered
    # field, for example ``一、项目基本情况1、项目编号``.
    text = re.sub(
        r"((?:第[一二三四五六七八九十百]+[章节部分]|"
        r"[一二三四五六七八九十]+[、.．])[^。\n]{1,48}?)(?=\d+(?:\.\d+)*[、.．)])",
        r"\1\n",
        text,
    )
    text = re.sub(
        r"([。；;])\s*(?=(?:\d+(?:\.\d+)*[、.．)]|[（(]\d+[)）]))",
        r"\1\n",
        text,
    )

    lines: list[str] = []
    for raw_line in text.splitlines():
        line = re.sub(r"[ \t\f\v]+", " ", raw_line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def fetch_url(url: str, timeout: int = 15) -> tuple[str, str]:
    req = request.Request(
        url,
        headers={
            "User-Agent": "TenderAgent/2.0 (+public-notice-monitor)",
            "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read(3 * 1024 * 1024)
            charset = response.headers.get_content_charset()
            # Some public-resource servers occasionally send an incorrect
            # charset header. Prefer an explicit HTML meta charset, then use
            # UTF-8 when it decodes cleanly; otherwise fall back to GB18030.
            head = body[:8192].decode("ascii", errors="ignore").lower()
            meta_match = re.search(
                r"<meta[^>]+charset\s*=\s*[\"']?\s*([a-z0-9_-]+)",
                head,
                flags=re.IGNORECASE,
            )
            detected_charset = (meta_match.group(1) if meta_match else "").lower()
            if detected_charset in {"utf-8", "utf8"}:
                charset = "utf-8"
            elif detected_charset in {"gbk", "gb2312", "gb18030"}:
                charset = "gb18030"
            if charset and charset.lower().replace("-", "") not in {"utf8"}:
                text = body.decode(charset, errors="replace")
            else:
                text = body.decode("utf-8", errors="replace")
                if "\ufffd" in text:
                    gb_text = body.decode("gb18030", errors="replace")
                    if gb_text.count("\ufffd") < text.count("\ufffd"):
                        text = gb_text
            return text, response.headers.get_content_type()
    except (OSError, UnicodeError, error.URLError) as exc:
        raise MonitorAdapterError(f"访问网站失败：{exc}") from exc


def fetch_form_json(
    url: str,
    form: dict[str, Any],
    timeout: int = 15,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {
        "User-Agent": "TenderAgent/2.0 (+public-notice-monitor)",
        "Accept": "application/json,text/plain,*/*",
        "Content-Type": "application/x-www-form-urlencoded",
        "X-Pass-Token": "",
    }
    request_headers.update(headers or {})
    body = parse.urlencode(
        [(key, str(value)) for key, value in form.items() if value is not None]
    ).encode("utf-8")
    req = request.Request(url, data=body, headers=request_headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read(3 * 1024 * 1024)
            charset = response.headers.get_content_charset() or "utf-8"
            payload = json.loads(raw.decode(charset, errors="replace"))
    except error.HTTPError as exc:
        raise MonitorAdapterError(f"接口请求失败：HTTP {exc.code}") from exc
    except (OSError, UnicodeError, ValueError, error.URLError) as exc:
        raise MonitorAdapterError(f"访问接口失败：{exc}") from exc
    if not isinstance(payload, dict):
        raise MonitorAdapterError("接口返回格式异常")
    return payload


def search_keywords(monitoring: dict[str, Any]) -> list[str]:
    result: list[str] = []

    def add_terms(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                add_terms(item)
            return
        for term in re.split(r"[,，;；、/|\n\r]+", clean_text(value)):
            text = clean_text(term)
            if text and text not in {"全国", "不限"}:
                result.append(text)

    add_terms(monitoring.get("includeKeywords") or [])
    add_terms(monitoring.get("serviceType", ""))
    expansions = {
        "数字孪生": ["数字孪生平台", "三维可视化", "3D建模", "可视化大屏", "智慧园区", "智慧城市", "态势感知", "孪生体"],
        "数据智能": ["数据治理", "数据分析", "智能分析", "指标", "知识图谱", "自然语言问数"],
        "企业架构": ["业务架构", "应用架构", "数据架构", "技术架构", "架构治理"],
        "AI 智能体": ["智能体", "大模型", "RAG", "多智能体", "知识库"],
        "智慧园区": ["园区可视化", "园区管理", "智慧园区/城市"],
        "智慧城市": ["数字孪生", "城市大脑", "智慧园区", "可视化平台"],
        "数据中心": ["数据中心与运维", "机房", "动环", "DCIM", "容量管理"],
        "低空经济": ["低空", "态势感知", "无人机", "空域管理"],
        "电力与能源": ["电网一张图", "保电", "变电站", "电力"],
        "智慧矿山": ["矿山", "矿山安全", "综合管控"],
        "可视化大屏": ["数据可视化", "三维可视化", "态势感知"],
    }
    for keyword in list(result):
        result.extend(expansions.get(keyword, []))
    unique: list[str] = []
    seen: set[str] = set()
    for keyword in result:
        key = keyword.lower()
        if key not in seen:
            seen.add(key)
            unique.append(keyword)
    return unique[: int(monitoring.get("maxKeywordsPerRun", 20) or 20)] or [""]


def date_window(monitoring: dict[str, Any]) -> tuple[date, date]:
    lookback = max(int(monitoring.get("lookbackDays", 7) or 7), 1)
    end = date.today()
    return end - timedelta(days=lookback), end


def notice_type_from_text(text: str) -> str:
    for kind in ("公开招标公告", "更正公告", "竞争性磋商公告", "竞争性谈判公告", "询价公告", "比选公告", "资格预审公告", "采购公告", "招标公告"):
        if kind in text:
            return kind
    if "中标" in text or "成交" in text:
        return "结果公告"
    return "招标公告"


def is_procurement_notice(title: str, summary: str = "", url: str = "") -> bool:
    text = clean_text(f"{title} {summary}")
    lowered_url = url.lower()
    negative_patterns = (
        "行业动态", "新闻", "资讯", "政策", "法规", "解读", "通知公告",
        "工作通知", "放假", "培训", "研讨会", "交流会", "会议", "论坛",
        "关于举办", "征文", "名单公示", "入库通知", "会员", "招聘",
    )
    if any(word in text for word in negative_patterns):
        strong_notice = any(word in text for word in ("采购公告", "招标公告", "磋商公告", "谈判公告", "询价公告", "比选公告", "资格预审公告", "更正公告"))
        if not strong_notice:
            return False
    positive_patterns = (
        "公开招标公告", "采购公告", "招标公告", "磋商公告", "谈判公告", "询价公告",
        "比选公告", "资格预审公告", "更正公告", "采购项目", "招标项目",
        "竞争性磋商", "竞争性谈判", "询价采购", "招标文件", "采购文件",
        "投标文件", "响应文件",
    )
    positive_url_markers = ("/cggg/", "/zfcg/", "/jyxx/", "/deal/", "bid", "tender", "purchase")
    return any(word in text for word in positive_patterns) or any(marker in lowered_url for marker in positive_url_markers)


def category_from_text(text: str) -> str:
    for keyword in ("数字孪生", "数据智能", "企业架构", "AI 智能体", "智慧园区", "智慧城市", "数据中心", "低空经济", "电力与能源", "智慧矿山", "三维可视化", "技术服务"):
        if keyword in text:
            return keyword
    return "待分类"


def extract_field(text: str, labels: tuple[str, ...], stop_labels: tuple[str, ...] = ()) -> str:
    escaped = "|".join(re.escape(label) for label in labels)
    pattern = rf"(?:{escaped})\s*[:：]\s*(.+?)(?:\s+(?:{'|'.join(re.escape(label) for label in stop_labels)})\s*[:：]|$)"
    match = re.search(pattern, text)
    return clean_text(match.group(1))[:160] if match else ""


def extract_published_date(text: str, url: str = "") -> str:
    label_pattern = r"(?:发布时间|发布日期|公告日期|公告时间|发出日期)\s*[:：]?\s*"
    date_pattern = r"(20\d{2})[年./:-](\d{1,2})[月./:-](\d{1,2})"
    match = re.search(label_pattern + date_pattern, text)
    if not match:
        match = re.search(date_pattern, url)
    if not match:
        compact = re.search(r"(20\d{2})(\d{2})(\d{2})", url)
        if compact:
            return f"{compact.group(1)}-{int(compact.group(2)):02d}-{int(compact.group(3)):02d}"
    if not match:
        match = re.search(date_pattern, text)
    if match:
        return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return ""


DATE_TIME_PATTERN = re.compile(
    r"(20\d{2})\s*[年./-]\s*(\d{1,2})\s*[月./-]\s*(\d{1,2})\s*(?:日)?"
    r"(?:\s*(上午|下午)?\s*(\d{1,2})\s*[:：时点]\s*(\d{1,2})?\s*(?:分)?)?"
)


def normalize_time_text(text: str) -> str:
    """Join layout/OCR-inserted spaces inside numeric date and time values."""
    return re.sub(r"(?<=\d)\s+(?=\d)", "", clean_text(text))


def _format_date_time(match: re.Match[str]) -> str:
    hour = int(match.group(5)) if match.group(5) else None
    minute = int(match.group(6) or 0) if hour is not None else None
    if hour is not None and match.group(4) == "下午" and hour < 12:
        hour += 12
    if hour is not None and match.group(4) == "上午" and hour == 12:
        hour = 0
    result = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    if hour is not None:
        result += f" {hour:02d}:{minute:02d}"
    return result


def extract_labeled_time(text: str, labels: tuple[str, ...], max_chars: int = 260) -> str:
    compact = normalize_time_text(text)
    label_pattern = "|".join(re.escape(label) for label in labels)
    for label_match in re.finditer(rf"(?:{label_pattern})\s*[:：]?", compact):
        snippet = compact[label_match.end(): label_match.end() + max_chars]
        date_matches = list(DATE_TIME_PATTERN.finditer(snippet))
        if not date_matches:
            continue
        first = date_matches[0]
        if first.start() > 80:
            continue
        result = _format_date_time(first)
        if len(date_matches) > 1:
            between = snippet[first.end(): date_matches[1].start()]
            if len(between) <= 12 and re.fullmatch(r"\s*(?:至|到|—|-|~)\s*", between):
                result += f" 至 {_format_date_time(date_matches[1])}"
        return result
    return ""


DOCUMENT_TIME_LABELS = (
    "获取招标文件时间", "获取采购文件时间", "获取文件时间",
    "招标文件获取时间", "采购文件获取时间", "招标文件发售时间", "采购文件发售时间",
    "报名时间", "发售时间",
)


def extract_registration_period(text: str) -> str:
    """Extract the document-download/registration window from common notice layouts."""
    value = extract_labeled_time(text, DOCUMENT_TIME_LABELS)
    if value:
        return value

    compact = normalize_time_text(text)
    # Many government notices use a section heading followed by ``1.时间：``
    # instead of writing ``获取招标文件时间：``.  Do not start at the overview
    # sentence ``获取招标文件，并于 ... 前递交投标文件`` because that date is the
    # bid deadline, not the document-download window.
    section_pattern = re.compile(
        r"(?:获取招标文件|获取采购文件|获取文件|"
        r"招标文件的获取|采购文件的获取|招标文件获取|采购文件获取|"
        r"发售招标文件|发售采购文件|报名)"
        r"[^。]{0,80}?(?:1[.、]\s*)?(?:时间|日期)\s*[:：]?",
    )
    for match in section_pattern.finditer(compact):
        snippet = compact[match.end(): match.end() + 320]
        date_matches = list(DATE_TIME_PATTERN.finditer(snippet))
        if not date_matches:
            continue
        first = date_matches[0]
        result = _format_date_time(first)
        if len(date_matches) > 1:
            between = snippet[first.end(): date_matches[1].start()]
            if len(between) <= 20 and re.fullmatch(r"\s*(?:至|到|—|-|~)\s*", between):
                result += f" 至 {_format_date_time(date_matches[1])}"
        return result
    return ""


def extract_deadline(text: str) -> str:
    value = extract_labeled_time(
        text,
        (
            "提交投标文件截止时间", "递交投标文件截止时间", "响应文件提交截止时间",
            "提交响应文件截止时间", "投标文件提交截止时间", "投标文件递交截止时间",
            "响应文件递交截止时间", "响应文件提交的截止时间", "投标文件提交的截止时间",
            "投标文件的递交截止时间", "响应文件的递交截止时间",
            "投标截止时间", "响应截止时间", "响应文件提交", "投标文件提交", "响应文件递交",
            "投标文件的递交", "响应文件的递交", "响应文件的提交",
            "开标时间", "开启时间",
        ),
    )
    if value:
        return value
    compact = normalize_time_text(text)
    section_pattern = re.compile(
        r"(?:投标文件的递交|投标文件递交|投标文件提交|响应文件的递交|响应文件递交|响应文件的提交|响应文件提交)"
        r"[^。]{0,100}?(?:截止时间|截止日期|时间|日期)\s*[:：]?",
    )
    for match in section_pattern.finditer(compact):
        snippet = compact[match.end(): match.end() + 320]
        date_matches = list(DATE_TIME_PATTERN.finditer(snippet))
        if date_matches:
            return _format_date_time(date_matches[0])
    overview = re.search(
        rf"并于\s*({DATE_TIME_PATTERN.pattern})\s*(?:（[^）]*）)?\s*前(?:提交|递交)",
        compact,
    )
    if overview:
        nested = DATE_TIME_PATTERN.search(overview.group(0))
        return _format_date_time(nested) if nested else ""
    return ""


def extract_project_name(text: str, fallback_title: str = "") -> str:
    compact = clean_text(text)
    for label in ("采购项目名称", "项目名称", "招标项目名称"):
        match = re.search(
            rf"{label}\s*[:：]?\s*(.+?)(?=\s(?:品目|采购单位|招标人|行政区域|公告时间|项目编号|采购方式|预算金额|最高限价|一、)\s*[:：]?)",
            compact,
        )
        if match:
            value = clean_text(match.group(1))
            if 2 <= len(value) <= 180:
                return value
    title = clean_text(fallback_title)
    title = re.sub(r"^.+?(?:有限责任公司|股份有限公司|有限公司|公司)\s*关于\s*", "", title)
    return title[:180]


def valid_time_value(value: Any) -> bool:
    text = clean_text(value)
    return bool(text and len(text) <= 64 and DATE_TIME_PATTERN.search(text))


def extract_region(text: str) -> str:
    match = re.search(r"(北京|天津|上海|重庆|河北|山西|辽宁|吉林|黑龙江|江苏|浙江|安徽|福建|江西|山东|河南|湖北|湖南|广东|海南|四川|贵州|云南|陕西|甘肃|青海|台湾|内蒙古|广西|西藏|宁夏|新疆|香港|澳门)[省市自治区特别行政区]*", text)
    return match.group(0) if match else "未识别"


def extract_links(html: str, base_url: str) -> list[tuple[str, str, str]]:
    pattern = re.compile(r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
    result: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for match in pattern.finditer(html):
        href = parse.urljoin(base_url, match.group(1))
        title = html_to_text(match.group(2))
        if not title or len(title) < 4 or title in {"更多", "首页", "上一页", "下一页"}:
            continue
        if href in seen:
            continue
        seen.add(href)
        start = max(match.start() - 300, 0)
        end = min(match.end() + 500, len(html))
        result.append((href, title, html[start:end]))
    return result


def normalize_notice(site: dict[str, Any], title: str, url: str, published: str, summary: str = "") -> dict[str, Any]:
    source_key = str(site.get("id", "source"))
    source_name = str(site.get("name", source_key))
    text = normalize_notice_text(f"{title}\n{summary}")
    published_date = normalize_date(published) or extract_published_date(text, url)
    return {
        "id": stable_id(source_key, url, title, published_date),
        "title": clean_text(title),
        "projectName": extract_project_name(text, title),
        "category": category_from_text(text),
        "region": extract_region(text),
        "noticeType": notice_type_from_text(text),
        "publishedDate": published_date,
        "source": source_name,
        "sourceUrl": url,
        "detailUrl": url,
        "analysisText": text[:12000],
        "sourceKey": source_key,
        "dataOrigin": "public_notice_crawl",
    }


def enrich_detail(item: dict[str, Any]) -> dict[str, Any]:
    url = str(item.get("detailUrl") or item.get("sourceUrl") or "")
    if not url.startswith(("http://", "https://")):
        return item
    try:
        html, _ = fetch_url(url, timeout=12)
    except MonitorAdapterError:
        return item
    text = normalize_notice_text(html_to_text(html))

    # The national platform's ``/a/`` page is a summary shell.  The actual
    # announcement body is linked from it through a ``/b/`` page, where the
    # document-download and bid-deadline sections are present.
    body_match = re.search(
        r"(?:https?://[^\"'\s]+)?(/information/deal/html/b/[^\"'\s<>]+\.html)",
        html,
        flags=re.IGNORECASE,
    )
    if body_match:
        body_url = parse.urljoin(url, body_match.group(1).replace("&amp;", "&"))
        if body_url != url:
            try:
                body_html, _ = fetch_url(body_url, timeout=12)
                body_text = normalize_notice_text(html_to_text(body_html))
                if len(body_text) > len(text):
                    html = body_html
                    text = body_text
                    item["contentUrl"] = body_url
            except MonitorAdapterError:
                pass
    if len(text) > len(str(item.get("analysisText", ""))):
        item["analysisText"] = text[:12000]
    published_date = extract_published_date(text, url)
    if published_date:
        item["publishedDate"] = published_date
    item["projectName"] = extract_project_name(text, str(item.get("title", "")))
    item["budget"] = item.get("budget") or extract_field(text, ("预算金额", "项目预算", "最高限价", "采购预算"), ("采购需求", "合同履行期限", "提交投标文件截止时间"))
    item["bidDeadline"] = extract_deadline(text)
    item["registrationPeriod"] = extract_registration_period(text)
    item["registrationDeadline"] = extract_labeled_time(
        text, ("获取招标文件截止时间", "获取采购文件截止时间", "获取文件截止时间", "报名截止时间")
    )
    buyer = extract_field(text, ("采购人", "招标人"), ("地址", "联系方式", "联系人"))
    if buyer:
        item["buyerName"] = buyer
    if item.get("region") == "未识别":
        item["region"] = extract_region(text)
    return item


def repair_notice_fields(item: dict[str, Any]) -> bool:
    if item.get("dataOrigin") not in {"public_notice_crawl", "custom_site", "monitor_fixture"}:
        return False
    changed = False

    original_text = str(item.get("analysisText", ""))
    text = normalize_notice_text(original_text)
    if text != original_text:
        item["analysisText"] = text[:12000]
        changed = True
    url = str(item.get("detailUrl") or item.get("sourceUrl") or "")

    project_name = extract_project_name(text, str(item.get("title", "")))
    if project_name and item.get("projectName") != project_name:
        item["projectName"] = project_name
        changed = True

    published_date = extract_published_date(text, url)
    if published_date and item.get("publishedDate") != published_date:
        item["publishedDate"] = published_date
        changed = True

    extracted = {
        "bidDeadline": extract_deadline(text),
        "registrationPeriod": extract_registration_period(text),
        "registrationDeadline": extract_labeled_time(
            text, ("获取招标文件截止时间", "获取采购文件截止时间", "获取文件截止时间", "报名截止时间")
        ),
    }
    for field, value in extracted.items():
        current = item.get(field, "")
        next_value = value if valid_time_value(value) else ""
        if current != next_value and (next_value or not valid_time_value(current)):
            item[field] = next_value
            changed = True
    return changed


class TenderSourceAdapter:
    def fetch(self, site: dict[str, Any], monitoring: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError


class CcgpAdapter(TenderSourceAdapter):
    def fetch(self, site: dict[str, Any], monitoring: dict[str, Any]) -> list[dict[str, Any]]:
        start, end = date_window(monitoring)
        max_pages = max(int(monitoring.get("maxPagesPerKeyword", 2) or 2), 1)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for keyword in search_keywords(monitoring):
            for page in range(1, max_pages + 1):
                params = {
                    "searchtype": "1",
                    "page_index": str(page),
                    "bidSort": "0",
                    "buyerName": "",
                    "projectId": "",
                    "pinMu": "0",
                    "bidType": "0",
                    "dbselect": "bidx",
                    "kw": keyword,
                    "start_time": start.strftime("%Y:%m:%d"),
                    "end_time": end.strftime("%Y:%m:%d"),
                    "timeType": "6",
                    "displayZone": "",
                    "zoneId": "",
                    "pppStatus": "0",
                    "agentName": "",
                }
                url = f"https://search.ccgp.gov.cn/bxsearch?{parse.urlencode(params)}"
                html, _ = fetch_url(url)
                for href, title, block in extract_links(html, url):
                    if "ccgp.gov.cn" not in href:
                        continue
                    block_text = html_to_text(block)
                    if not is_procurement_notice(title, block_text, href):
                        continue
                    published = extract_published_date(block_text, href)
                    item = normalize_notice(site, title, href, published, block_text)
                    if href not in seen:
                        seen.add(href)
                        items.append(enrich_detail(item) if monitoring.get("fetchDetails", True) else item)
        return items


class GgzyAdapter(TenderSourceAdapter):
    API_URL = "https://www.ggzy.gov.cn/information/pubTradingInfo/getTradList"
    BASE_URL = "https://www.ggzy.gov.cn"

    def fetch(self, site: dict[str, Any], monitoring: dict[str, Any]) -> list[dict[str, Any]]:
        start, end = date_window(monitoring)
        max_pages = max(int(monitoring.get("maxPagesPerKeyword", 2) or 2), 1)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        base_url = str(site.get("url") or self.BASE_URL).rstrip("/")
        for keyword in search_keywords(monitoring):
            for page in range(1, max_pages + 1):
                form = {
                    "SOURCE_TYPE": "1",
                    "DEAL_TIME": "06",
                    "TIMEBEGIN": start.isoformat(),
                    "TIMEEND": end.isoformat(),
                    "PAGENUMBER": page,
                    "FINDTXT": keyword,
                }
                payload = fetch_form_json(self.API_URL, form)
                code = payload.get("code")
                if code != 200:
                    message = clean_text(payload.get("message")) or f"业务状态码 {code}"
                    raise MonitorAdapterError(f"官方平台返回失败：{message}")
                data = payload.get("data")
                records = data.get("records", []) if isinstance(data, dict) else []
                if not isinstance(records, list):
                    raise MonitorAdapterError("官方平台返回的公告列表格式异常")
                for record in records:
                    if not isinstance(record, dict):
                        continue
                    title = clean_text(record.get("title"))
                    relative_url = clean_text(record.get("url"))
                    if not title or not relative_url:
                        continue
                    href = parse.urljoin(base_url + "/", relative_url)
                    parsed_href = parse.urlsplit(href)
                    if (
                        "ggzy.gov.cn" not in parsed_href.netloc.lower()
                        or not parsed_href.path.startswith("/information/deal/html/")
                    ):
                        continue
                    summary_parts = [
                        record.get("provinceText"),
                        record.get("cityText"),
                        record.get("transactionSourcesPlatformText"),
                        record.get("businessTypeText"),
                        record.get("industryTypeText"),
                        record.get("informationTypeText"),
                    ]
                    summary = clean_text(" ".join(
                        clean_text(part) for part in summary_parts if clean_text(part)
                    ))
                    if not is_procurement_notice(title, summary, href):
                        continue
                    item = normalize_notice(
                        site,
                        title,
                        href,
                        clean_text(record.get("publishTime")),
                        summary,
                    )
                    if href not in seen:
                        seen.add(href)
                        items.append(
                            enrich_detail(item)
                            if monitoring.get("fetchDetails", True)
                            else item
                        )
        return items


class GenericHtmlAdapter(TenderSourceAdapter):
    def fetch(self, site: dict[str, Any], monitoring: dict[str, Any]) -> list[dict[str, Any]]:
        url = str(site.get("listUrl") or site.get("url") or "").strip()
        html, _ = fetch_url(url)
        items: list[dict[str, Any]] = []
        for href, title, block in extract_links(html, url):
            text = html_to_text(block)
            if not is_procurement_notice(title, text, href):
                continue
            item = normalize_notice(site, title, href, extract_published_date(text, href), text)
            items.append(enrich_detail(item) if monitoring.get("fetchDetails", True) else item)
            if len(items) >= max(int(monitoring.get("maxGenericItems", 50) or 50), 1):
                break
        return items


def adapter_for(site: dict[str, Any]) -> TenderSourceAdapter:
    site_id = str(site.get("id", ""))
    if site_id == "government-procurement":
        return CcgpAdapter()
    if site_id == "public-resource":
        return GgzyAdapter()
    return GenericHtmlAdapter()
