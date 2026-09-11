from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import time
from typing import Any
from urllib import error, request

from .contracts import build_comparison, normalize_evidence, normalize_requirement, summarize_comparisons


ALLOWED_CATEGORIES = {
    "qualification",
    "performance",
    "personnel",
    "financial",
    "region",
    "credit",
    "rejection",
    "technical",
    "other",
}

CATEGORY_LABELS = {
    "qualification": "资质",
    "performance": "业绩",
    "personnel": "人员",
    "financial": "财务",
    "region": "地域",
    "credit": "信用",
    "rejection": "否决条款",
    "technical": "产品与能力",
    "other": "其他要求",
}

EXTRACTION_CACHE_VERSION = "announcement-extraction-v1"
EXTRACTION_CACHE_MAX_SIZE = 64
_ANNOUNCEMENT_EXTRACTION_CACHE: dict[str, dict[str, Any]] = {}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]


def _safe_text(value: Any, limit: int = 900) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _trim_model_input(value: Any, limit: int) -> str:
    text = str(value or "")
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.72))
    tail = max(1, limit - head)
    return f"{text[:head]}\n...[中间原文已压缩]...\n{text[-tail:]}"


def _is_retryable_error(result: dict[str, Any]) -> bool:
    """True when the model call failed transiently and a retry may succeed."""
    if result.get("success"):
        return False
    error_text = str(result.get("error", ""))
    retryable_markers = (
        "HTTP 502", "HTTP 503", "HTTP 504", "HTTP 429",
        "响应超时", "连接中断", "连接失败", "无法连接",
    )
    return any(marker in error_text for marker in retryable_markers)


def _is_gateway_timeout(result: dict[str, Any]) -> bool:
    """Backward-compatible alias for transient model failures."""
    return _is_retryable_error(result)


def _is_full_tender_document(text: str) -> bool:
    """True when text is a complete tender document rather than a short notice.

    Result-notice keywords (终止公告/中标公告/废标公告) appear inside complete
    tender documents as normal boilerplate clauses, so we must not classify the
    whole document as a result notice based on body matches alone.
    """
    head = re.sub(r"\s+", "", str(text)[:600])
    return any(term in head for term in (
        "招标文件", "公开招标文件", "竞争性磋商文件", "竞争性谈判文件", "询价通知书",
    ))


def _ai_settings(ai: dict[str, Any]) -> tuple[str, str, str]:
    base_url = str(ai.get("baseUrl") or os.getenv("AI_BASE_URL", "")).strip()
    api_key = str(ai.get("apiKey") or os.getenv("AI_API_KEY", "")).strip()
    model = str(ai.get("model") or os.getenv("AI_MODEL", "")).strip()
    return base_url, api_key, model


def _announcement_extraction_cache_key(text: str, ai: dict[str, Any], model_input: str) -> str:
    base_url, _, model = _ai_settings(ai)
    organization_id = str(ai.get("organizationId") or "").strip()
    payload = {
        "version": EXTRACTION_CACHE_VERSION,
        "baseUrl": base_url.rstrip("/"),
        "model": model,
        "organizationId": organization_id,
        "text": text,
        "modelInput": model_input,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cached_announcement_extraction(cache_key: str) -> dict[str, Any] | None:
    cached = _ANNOUNCEMENT_EXTRACTION_CACHE.get(cache_key)
    if cached is None:
        return None
    result = copy.deepcopy(cached)
    result["extractionCacheHit"] = True
    return result


def _remember_announcement_extraction(cache_key: str, result: dict[str, Any]) -> dict[str, Any]:
    stored = copy.deepcopy(result)
    stored["extractionCacheHit"] = False
    if cache_key not in _ANNOUNCEMENT_EXTRACTION_CACHE and len(_ANNOUNCEMENT_EXTRACTION_CACHE) >= EXTRACTION_CACHE_MAX_SIZE:
        _ANNOUNCEMENT_EXTRACTION_CACHE.pop(next(iter(_ANNOUNCEMENT_EXTRACTION_CACHE)))
    _ANNOUNCEMENT_EXTRACTION_CACHE[cache_key] = stored
    response = copy.deepcopy(stored)
    response["extractionCacheHit"] = False
    return response


def _chat_endpoint(base_url: str) -> str:
    clean = base_url.rstrip("/")
    return clean if clean.endswith("/chat/completions") else f"{clean}/chat/completions"


def call_openai_compatible(
    ai: dict[str, Any],
    messages: list[dict[str, str]],
    *,
    max_tokens: int,
    temperature: float = 0,
) -> dict[str, Any]:
    """Call an OpenAI-compatible chat endpoint with safe, actionable failure messages."""
    if ai.get("enabled") is False:
        return {"success": False, "error": "大模型功能已关闭"}

    base_url, api_key, model = _ai_settings(ai)
    if not base_url or not api_key or not model:
        return {"success": False, "error": "请先填写 API Base URL、API Key 和模型名称"}

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    organization_id = str(ai.get("organizationId") or "").strip()
    if organization_id:
        headers["OpenAI-Organization"] = organization_id

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    try:
        timeout_seconds = int(ai.get("timeoutSeconds", 120))
    except (TypeError, ValueError):
        timeout_seconds = 120
    timeout_seconds = min(max(timeout_seconds, 15), 300)

    max_attempts = 3
    last_error = ""
    raw_body = b""
    for attempt in range(1, max_attempts + 1):
        req = request.Request(
            _chat_endpoint(base_url),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as response:
                raw_body = response.read()
        except error.HTTPError as exc:
            last_error = f"模型服务返回 HTTP {exc.code}"
        except error.URLError as exc:
            reason = str(getattr(exc, "reason", "")).strip()
            detail = f"：{reason}" if reason else ""
            last_error = f"无法连接模型服务，请检查 Base URL 和网络{detail}"
        except TimeoutError:
            last_error = f"模型服务响应超时（{timeout_seconds} 秒）"
        except OSError:
            last_error = "模型服务连接中断或响应读取失败，请稍后重试"
        else:
            break
        if attempt < max_attempts and _is_retryable_error({"success": False, "error": last_error}):
            time.sleep(min(2 * attempt, 6))
            continue
        return {"success": False, "error": last_error}

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except UnicodeDecodeError:
        return {"success": False, "error": "模型服务返回的内容不是 UTF-8 文本"}
    except json.JSONDecodeError:
        return {"success": False, "error": "模型服务返回了非 JSON 内容，请稍后重试"}
    if not isinstance(body, dict):
        return {"success": False, "error": "模型服务返回 JSON 结构异常"}
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {"success": False, "error": "模型服务响应缺少 choices 字段"}
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return {"success": False, "error": "模型服务响应缺少 message 字段"}
    content = message.get("content", "")
    if isinstance(content, list):
        content = "".join(
            str(item.get("text", "")) if isinstance(item, dict) else str(item)
            for item in content
        )
    content = str(content).strip()
    if not content:
        return {"success": False, "error": "模型服务未返回可用内容"}
    return {"success": True, "content": content, "model": model}


def test_ai_connection(ai: dict[str, Any]) -> dict[str, Any]:
    result = call_openai_compatible(
        ai,
        [
            {"role": "system", "content": "你是连通性测试助手，只回复 OK。"},
            {"role": "user", "content": "请回复 OK。"},
        ],
        max_tokens=12,
    )
    if not result["success"]:
        return {"success": False, "detail": result["error"]}
    return {"success": True, "model": result["model"], "detail": "模型接口连接成功"}


def _category_for(text: str) -> str:
    value = _compact(text)
    if any(term in value for term in ("否决", "无效投标", "废标", "不予受理", "不得参与")):
        return "rejection"
    if any(term in value for term in ("信用中国", "失信", "违法", "信用记录", "重大税收")):
        return "credit"
    if any(term in value for term in ("财务报告", "审计报告", "财务状况", "资产负债", "银行资信", "资信证明")):
        return "financial"
    if any(
        term in value
        for term in ("项目经理", "项目负责人", "技术负责人", "人员", "团队", "工程师", "驻场")
    ):
        return "personnel"
    if any(term in value for term in ("类似项目", "类似业绩", "项目业绩", "合同业绩", "案例")):
        return "performance"
    if any(term in value for term in ("服务地点", "注册地", "属地", "地域", "本地")):
        return "region"
    if "联合体" in value:
        return "qualification"
    if any(term in value for term in ("资质", "认证", "证书", "许可证", "iso", "itss", "cmmi", "营业执照")):
        return "qualification"
    if any(
        term in value
        for term in (
            "数字孪生",
            "三维",
            "可视化",
            "平台",
            "系统",
            "数据治理",
            "物联",
            "ai",
            "智能体",
            "实施",
            "技术",
        )
    ):
        return "technical"
    return "other"


def _required_count(text: str) -> int | None:
    result = re.search(r"(?:不少于|至少|不低于|具有)\s*(\d+)\s*(?:个|项|名|人)", text)
    return int(result.group(1)) if result else None


PROJECT_FIELD_LABELS: dict[str, tuple[str, ...]] = {
    "projectName": ("采购项目名称", "招标项目名称", "项目名称"),
    "projectCode": ("采购项目编号", "招标项目编号", "项目编号", "采购编号", "招标编号"),
    "tendererName": ("采购人名称", "招标人名称", "采购单位", "采购人", "招标人"),
    "agencyName": ("采购代理机构名称", "招标代理机构名称", "代理机构名称", "采购代理机构", "招标代理机构"),
    "procurementMethod": ("采购方式", "招标方式"),
    "tenderNoticeDate": ("招标公告发布日期", "采购公告发布日期"),
    "noticeDate": ("公告发布日期", "公告时间"),
    "budgetAmount": ("采购预算", "预算金额", "项目预算"),
    "maxPrice": ("最高限价", "控制价", "招标控制价"),
    "serviceType": ("服务类型", "项目类型", "采购内容", "采购品目"),
    "serviceLocation": ("服务地点", "实施地点", "交付地点", "建设地点", "项目地点", "交货地点"),
    "bidDeadline": ("投标截止时间", "响应文件提交截止时间", "递交响应文件截止时间", "提交投标文件截止时间", "截止时间"),
    "openingTime": ("开标时间", "开启时间", "响应文件开启时间"),
    "bidLocation": ("投标文件递交地点", "响应文件提交地点", "提交投标文件地点", "投标地点", "递交地点"),
    "openingLocation": ("开标地点", "开启地点"),
    "projectDuration": ("项目工期", "合同履行期限", "服务期限", "服务期", "供货期", "交货期", "工期"),
    "warrantyRequirement": ("质量保证期", "质保要求", "质保期", "保修期"),
    "bidCopyRequirement": ("投标文件份数", "响应文件份数", "投标份数", "正副本份数"),
    "packagingRequirement": ("投标文件封装要求", "响应文件封装要求", "封装要求", "密封要求", "封套要求", "装订要求"),
    "signatureSealRequirement": ("签字盖章要求", "签字和盖章要求", "签章要求", "签字要求", "盖章要求"),
    "jointVentureRequirement": ("本项目是否接受联合体投标", "是否接受联合体投标", "联合体投标"),
}

PROJECT_FIELD_KEYS = tuple(PROJECT_FIELD_LABELS)
RESULT_NOTICE_TYPES = {
    "中标公告",
    "成交公告",
    "结果公告",
    "中标候选人公示",
    "废标公告",
    "流标公告",
    "终止公告",
}
RESULT_NOTICE_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("中标候选人公示", "中标候选人公示"),
    ("中标情况", "中标公告"),
    ("成交情况", "成交公告"),
    ("中标结果", "中标公告"),
    ("成交结果", "成交公告"),
    ("中标公告", "中标公告"),
    ("成交公告", "成交公告"),
    ("采购结果公告", "结果公告"),
    ("结果公告", "结果公告"),
    ("废标公告", "废标公告"),
    ("流标公告", "流标公告"),
    ("终止公告", "终止公告"),
    ("终止采购公告", "终止公告"),
)
CONTACT_SECTION_HEADERS = {
    "tenderer": ("采购人信息", "招标人信息"),
    "agency": ("采购代理机构信息", "招标代理机构信息", "代理机构信息"),
}
CONTACT_FIELD_LABELS = {
    "tenderer": {
        "tendererName": ("名称",),
        "tendererAddress": ("地址",),
        "tendererContact": ("联系人", "项目联系人"),
        "tendererPhone": ("联系方式", "联系电话", "项目联系电话"),
    },
    "agency": {
        "agencyName": ("名称",),
        "agencyAddress": ("地址",),
        "agencyContact": ("联系人", "项目联系人"),
        "agencyPhone": ("联系方式", "联系电话", "项目联系电话"),
    },
}
DEFAULT_PROJECT_FIELD_SECTION_PRIORITY = ("basic", "other", "procurement", "bid_opening", "contact", "result")
PROJECT_FIELD_SECTION_PRIORITY: dict[str, tuple[str, ...]] = {
    "bidDeadline": ("bid_opening", "basic", "other", "procurement", "result"),
    "openingTime": ("bid_opening", "basic", "other", "procurement", "result"),
    "bidLocation": ("bid_opening", "basic", "other", "procurement", "result"),
    "openingLocation": ("bid_opening", "basic", "other", "procurement", "result"),
    "tendererName": ("basic", "contact", "other", "result"),
    "agencyName": ("basic", "contact", "other", "result"),
    "jointVentureRequirement": ("basic", "qualification", "other", "result"),
}
TOP_LEVEL_HEADING = re.compile(r"^(?:第[一二三四五六七八九十百]+[章节部分]|[一二三四五六七八九十]+[、.．])")
NUMBERED_PREFIX = r"(?:\s*(?:\d+(?:\.\d+)+\s+|\d+(?:\.\d+)*[、.．、)]|[一二三四五六七八九十]+[、.．]|[（(]\d+[)）])\s*)?"
SECTION_HEADING_TERMS: dict[str, tuple[str, ...]] = {
    "basic": (
        "项目基本情况",
        "项目概况",
        "采购项目基本情况",
        "招标项目基本情况",
        "采购项目用途",
        "采购需求",
    ),
    "qualification": (
        "申请人的资格要求",
        "投标人的资格要求",
        "供应商资格要求",
        "供应商的资格要求",
        "供应商的资格条件",
        "投标人资格条件",
        "资格审查要求",
        "合格投标人",
        "特定资格要求",
    ),
    "procurement": (
        "获取招标文件",
        "获取采购文件",
        "获取磋商文件",
        "获取谈判文件",
        "招标文件的获取",
        "采购文件的获取",
        "报名及招标文件",
    ),
    "bid_opening": (
        "提交投标文件截止时间",
        "投标文件截止时间",
        "响应文件提交",
        "响应文件开启",
        "开标时间和地点",
        "开标时间",
        "投标截止时间",
    ),
    "contact": (
        "对本次招标提出询问",
        "联系方式",
        "采购人信息",
        "招标人信息",
        "采购代理机构信息",
        "招标代理机构信息",
        "代理机构信息",
        "项目联系方式",
    ),
    "result": (
        "中标情况",
        "成交情况",
        "中标候选人公示",
        "中标结果",
        "成交结果",
        "结果公告",
        "中标公告",
        "成交公告",
        "废标公告",
        "流标公告",
        "终止公告",
    ),
}


def _clean_lines(text: str) -> list[tuple[int, str]]:
    return [
        (index, _safe_text(line, 1200))
        for index, line in enumerate(text.splitlines(), start=1)
        if _safe_text(line, 1200)
    ]


def _section_type_for_heading(line: str) -> str:
    compact = _compact(line)
    for section_type, terms in SECTION_HEADING_TERMS.items():
        if any(term in compact for term in terms):
            return section_type
    return "other"


def _is_announcement_section_heading(line: str) -> bool:
    compact = _compact(line)
    if "：" in line or ":" in line:
        field_labels = tuple(label for labels in PROJECT_FIELD_LABELS.values() for label in labels)
        label_pattern = "|".join(re.escape(label) for label in sorted(field_labels, key=len, reverse=True))
        if re.match(rf"^{NUMBERED_PREFIX}(?:{label_pattern})\s*[:：]", line):
            return False
    if TOP_LEVEL_HEADING.match(line):
        return True
    if "：" in line or ":" in line:
        return False
    return any(
        term in compact and len(compact) <= 40
        for terms in SECTION_HEADING_TERMS.values()
        for term in terms
    )


def split_announcement_sections(text: str) -> list[dict[str, Any]]:
    lines = _clean_lines(text)
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line_number, line in lines:
        if _is_announcement_section_heading(line):
            if current is not None:
                sections.append(current)
            current = {
                "type": _section_type_for_heading(line),
                "heading": line,
                "startLine": line_number,
                "endLine": line_number,
                "lines": [],
                "text": "",
            }
            continue

        if current is None:
            current = {
                "type": "other",
                "heading": "",
                "startLine": line_number,
                "endLine": line_number,
                "lines": [],
                "text": "",
            }
        current["lines"].append({"lineNumber": line_number, "text": line})
        current["endLine"] = line_number
        current["text"] = "\n".join(item["text"] for item in current["lines"])

    if current is not None:
        sections.append(current)
    return sections


def _label_value(line: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    result = re.match(rf"^{NUMBERED_PREFIX}(?:{label_pattern})\s*[:：]\s*(.+)$", line)
    return _safe_text(result.group(1), 360) if result else ""


def _first_matching_line(
    lines: list[tuple[int, str]],
    terms: tuple[str, ...],
    validator: Any | None = None,
) -> str:
    for _, line in lines:
        if any(term in line for term in terms) and (validator is None or validator(line)):
            return line
    return ""


def _ordered_section_lines(
    sections: list[dict[str, Any]],
    section_types: tuple[str, ...],
) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    for section_type in section_types:
        for section in sections:
            if section.get("type") != section_type:
                continue
            result.extend(
                (int(item["lineNumber"]), str(item["text"]))
                for item in section.get("lines", [])
                if isinstance(item, dict)
            )
    return result


def _contact_section_for_line(line: str) -> str:
    if any(header in line for header in CONTACT_SECTION_HEADERS["tenderer"]):
        return "tenderer"
    if any(header in line for header in CONTACT_SECTION_HEADERS["agency"]):
        return "agency"
    return ""


def _detect_notice_type(text: str) -> str:
    # 完整招标文件（含"招标文件"等标题）天然包含"终止公告""中标公告"
    # 等词汇作为正常条款，不应被误判为结果类公告。
    if _is_full_tender_document(text):
        return ""
    sections = split_announcement_sections(text)
    leading_text = "\n".join(line for _, line in _clean_lines(text)[:16])
    compact = _compact(f"{leading_text}\n{text[:1600]}")
    for keyword, notice_type in RESULT_NOTICE_KEYWORDS:
        if keyword in compact:
            return notice_type
    for section in sections:
        if section.get("type") == "result":
            section_text = _compact(f"{section.get('heading', '')}\n{section.get('text', '')}")
            for keyword, notice_type in RESULT_NOTICE_KEYWORDS:
                if keyword in section_text:
                    return notice_type
            return "结果公告"
    for notice_type in (
        "更正公告",
        "资格预审公告",
        "竞争性磋商公告",
        "竞争性谈判公告",
        "询价公告",
        "公开招标公告",
        "招标公告",
        "采购公告",
    ):
        if notice_type in compact:
            return notice_type
    return ""


def extract_project_fields(text: str) -> dict[str, str]:
    sections = split_announcement_sections(text)
    result = {key: "" for key in PROJECT_FIELD_KEYS}
    result["noticeType"] = _detect_notice_type(text)

    for key, labels in PROJECT_FIELD_LABELS.items():
        section_types = PROJECT_FIELD_SECTION_PRIORITY.get(key, DEFAULT_PROJECT_FIELD_SECTION_PRIORITY)
        for _, line in _ordered_section_lines(sections, section_types):
            value = _label_value(line, labels)
            if value:
                result[key] = value
                break

    for section in sections:
        if section.get("type") != "contact":
            continue
        contact_section = _contact_section_for_line(str(section.get("heading", "")))
        for item in section.get("lines", []):
            if not isinstance(item, dict):
                continue
            line = str(item.get("text", ""))
            detected_contact_section = _contact_section_for_line(line)
            if detected_contact_section:
                contact_section = detected_contact_section
                continue
            if not contact_section:
                continue
            for key, labels in CONTACT_FIELD_LABELS[contact_section].items():
                if result.get(key):
                    continue
                value = _label_value(line, labels)
                if value:
                    result[key] = value

    sentence_fields = {
        "jointVentureRequirement": (
            ("basic", "qualification", "other", "result"),
            ("联合体",),
            lambda line: "联合体" in line and any(term in line for term in ("接受", "不接受", "不得", "否")),
        ),
        "bidCopyRequirement": (
            ("bid_opening", "basic", "other", "procurement", "result"),
            ("投标文件份数", "响应文件份数", "正本", "副本"),
            lambda line: bool(re.search(r"(?:正本|副本|投标文件|响应文件).{0,24}\d+\s*份", line)),
        ),
        "packagingRequirement": (
            ("bid_opening", "basic", "other", "procurement", "result"),
            ("封装", "密封", "封套", "装订"),
            lambda line: any(term in line for term in ("投标文件", "响应文件", "正本", "副本")),
        ),
        "signatureSealRequirement": (
            ("bid_opening", "basic", "other", "procurement", "result"),
            ("签字盖章", "签章", "加盖公章", "法定代表人签字"),
            lambda line: any(
                term in line for term in ("投标文件", "响应文件", "法定代表人", "授权代表", "投标人")
            ),
        ),
        "warrantyRequirement": (
            ("basic", "other", "bid_opening", "procurement", "result"),
            ("质保期", "质量保证期", "保修期"),
            lambda line: bool(
                re.search(
                    r"(?:质保期|质量保证期|保修期).{0,30}(?:不少于|不低于|至少|为|：|:)?\s*\d+\s*(?:年|个月|月|日|天)",
                    line,
                )
            ),
        ),
    }
    for key, (section_types, terms, validator) in sentence_fields.items():
        if not result[key]:
            lines = _ordered_section_lines(sections, section_types)
            value = _first_matching_line(lines, terms, validator)
            result[key] = re.sub(rf"^{NUMBERED_PREFIX}", "", value).strip() if value else ""

    return {key: value for key, value in result.items() if value}


def _looks_like_basic_information(line: str) -> bool:
    compact = _compact(line)
    if any(
        label in compact
        for key, labels in PROJECT_FIELD_LABELS.items()
        if key != "jointVentureRequirement"
        for label in labels
    ):
        return True
    return any(
        heading in compact
        for heading in (
            "项目基本情况",
            "采购项目用途",
            "采购需求",
            "联系方式",
            "采购人信息",
            "代理机构信息",
            "中标情况",
            "评审专家名单",
            "收费标准及金额",
            "公告发布的媒介",
        )
    )


def _qualification_section_heading(line: str) -> bool:
    compact = _compact(line)
    return any(
        heading in compact
        for heading in (
            "申请人的资格要求",
            "投标人的资格要求",
            "供应商资格要求",
            "供应商的资格要求",
            "供应商的资格条件",
            "投标人资格条件",
            "资格审查要求",
            "合格投标人",
            "特定资格要求",
        )
    )


def _requirement_group(category: str, text: str) -> str:
    compact = _compact(text)
    if category == "qualification":
        return "enterprise_qualification"
    if category == "personnel":
        return "personnel_qualification"
    if category == "performance":
        return "project_performance"
    if category == "financial":
        return "financial_condition"
    if category == "credit":
        return "credit_condition"
    if "联合体" in compact:
        return "consortium_condition"
    return category


def _is_eligibility_requirement(line: str, *, in_qualification_section: bool = False) -> bool:
    compact = _compact(line)
    if not compact or _looks_like_basic_information(line) or _qualification_section_heading(line):
        return False
    if any(
        term in compact
        for term in (
            "供应商名称",
            "供应商地址",
            "中标供应商",
            "成交供应商",
            "中标人名称",
            "评审专家",
            "中标金额",
            "成交金额",
        )
    ):
        return False
    qualification_terms = (
        "投标人",
        "供应商",
        "申请人",
        "资格",
        "资质",
        "证书",
        "许可证",
        "业绩",
        "项目经理",
        "项目负责人",
        "技术负责人",
        "财务报告",
        "审计报告",
        "资信证明",
        "信用中国",
        "失信",
        "重大违法",
        "联合体",
        "政府采购法第二十二条",
        "面向中小企业",
    )
    if in_qualification_section:
        return len(compact) >= 5
    if "联合体" in compact and any(term in compact for term in ("不接受", "不得", "否")):
        return True
    return any(term in compact for term in qualification_terms) and any(
        term in compact
        for term in (
            "须",
            "应",
            "具有",
            "提供",
            "不得",
            "不接受",
            "满足",
            "符合",
            "无重大违法记录",
            "无不良信用记录",
            "不存在",
            "未被",
            "要求",
        )
    )


def _is_standalone_eligibility_requirement(line: str) -> bool:
    compact = _compact(line)
    if not compact or _looks_like_basic_information(line) or _qualification_section_heading(line):
        return False
    if "联合体" in compact and any(term in compact for term in ("不接受", "不得", "否")):
        return True
    if "政府采购法第二十二条" in compact:
        return True
    if "面向中小企业" in compact and any(term in compact for term in ("专门", "采购", "要求", "预留")):
        return True
    if "信用中国" in compact and any(term in compact for term in ("未被", "不得", "失信", "违法")):
        return True
    subject_terms = ("投标人", "供应商", "申请人")
    condition_terms = (
        "资格",
        "资质",
        "证书",
        "许可证",
        "业绩",
        "项目经理",
        "项目负责人",
        "技术负责人",
        "财务报告",
        "审计报告",
        "资信证明",
        "信用记录",
        "重大违法",
    )
    action_terms = ("须", "应", "具有", "提供", "不得", "满足", "符合", "未被", "要求")
    return (
        any(term in compact for term in subject_terms)
        and any(term in compact for term in condition_terms)
        and any(term in compact for term in action_terms)
    )


def _section_allows_requirement_scan(section_type: str, line: str) -> bool:
    if section_type == "qualification":
        return True
    if section_type in {"procurement", "bid_opening", "contact", "result"}:
        return False
    return _is_standalone_eligibility_requirement(line)


def _looks_like_project_field_candidate(line: str) -> bool:
    if any(_label_value(line, labels) for labels in PROJECT_FIELD_LABELS.values()):
        return True
    return any(
        validator(line)
        for _, _, validator in (
            (
                "bidCopyRequirement",
                ("投标文件份数", "响应文件份数", "正本", "副本"),
                lambda value: bool(re.search(r"(?:正本|副本|投标文件|响应文件).{0,24}\d+\s*份", value)),
            ),
            (
                "packagingRequirement",
                ("封装", "密封", "封套", "装订"),
                lambda value: any(term in value for term in ("投标文件", "响应文件", "正本", "副本")),
            ),
            (
                "signatureSealRequirement",
                ("签字盖章", "签章", "加盖公章", "法定代表人签字"),
                lambda value: any(
                    term in value for term in ("投标文件", "响应文件", "法定代表人", "授权代表", "投标人")
                ),
            ),
            (
                "warrantyRequirement",
                ("质保期", "质量保证期", "保修期"),
                lambda value: bool(
                    re.search(
                        r"(?:质保期|质量保证期|保修期).{0,30}(?:不少于|不低于|至少|为|：|:)?\s*\d+\s*(?:年|个月|月|日|天)",
                        value,
                    )
                ),
            ),
        )
    )


def _model_lines_for_section(section: dict[str, Any]) -> list[tuple[int, str]]:
    section_type = str(section.get("type") or "other")
    result: list[tuple[int, str]] = []
    for item in section.get("lines", []):
        if not isinstance(item, dict):
            continue
        line_number = int(item.get("lineNumber") or 0)
        line = str(item.get("text") or "")
        if section_type == "qualification":
            result.append((line_number, line))
        elif section_type in {"bid_opening", "contact"}:
            result.append((line_number, line))
        elif section_type in {"basic", "other"} and (
            _looks_like_project_field_candidate(line) or _is_standalone_eligibility_requirement(line)
        ):
            result.append((line_number, line))
    return result


def build_announcement_model_input(text: str, *, limit: int = 3600) -> str:
    chunks: list[str] = []
    for section in split_announcement_sections(text):
        lines = _model_lines_for_section(section)
        if not lines:
            continue
        section_type = str(section.get("type") or "other")
        heading = str(section.get("heading") or "未分节前置内容")
        start_line = min(line_number for line_number, _ in lines)
        end_line = max(line_number for line_number, _ in lines)
        body = "\n".join(f"L{line_number}: {line}" for line_number, line in lines)
        chunks.append(f"[{section_type} | {heading} | L{start_line}-L{end_line}]\n{body}")
    if not chunks:
        return _trim_model_input(text, limit)
    return _trim_model_input("\n\n".join(chunks), limit)


def _result_notice_warning(notice_type: str) -> str:
    label = notice_type or "结果类公告"
    return f"当前内容疑似{label}，通常不包含完整资格条件，请上传原始招标公告或招标文件后再评估。"


def _split_requirement_line(line: str) -> list[str]:
    cleaned = re.sub(rf"^{NUMBERED_PREFIX}", "", line).strip().rstrip("；;").strip()
    parts = [
        _safe_text(part, 1000).rstrip("；;").strip()
        for part in re.split(r"[；;](?=\s*(?:\d+[、.．]|[（(]\d+[)）]|[①②③④⑤⑥⑦⑧⑨]))", cleaned)
        if _safe_text(part, 1000)
    ]
    return parts or [cleaned]


def extract_requirements_by_rule(text: str) -> dict[str, Any]:
    sections = split_announcement_sections(text)
    project = extract_project_fields(text)
    if project.get("noticeType") in RESULT_NOTICE_TYPES and not _is_full_tender_document(text):
        return {
            "project": project,
            "requirements": [],
            "warnings": [_result_notice_warning(str(project.get("noticeType", "")))],
        }

    requirements: list[dict[str, Any]] = []

    for section in sections:
        section_type = str(section["type"])
        in_qualification_section = section_type == "qualification"
        for item in section["lines"]:
            line_number = item["lineNumber"]
            line = item["text"]
            if not _section_allows_requirement_scan(section_type, line):
                continue
            if not _is_eligibility_requirement(line, in_qualification_section=in_qualification_section):
                continue
            for clause in _split_requirement_line(line):
                category = _category_for(clause)
                is_rejection = any(
                    term in _compact(clause)
                    for term in ("投标无效", "无效投标", "资格审查不通过", "不得参加", "拒绝其投标", "否决")
                )
                requirements.append(
                    {
                        "id": f"rule-{len(requirements) + 1:03d}",
                        "category": category,
                        "text": clause,
                        "section": section["heading"] or "资格条件",
                        "sourceLocator": f"第{line_number}行",
                        "mandatory": not any(term in clause for term in ("可选择", "如有", "若适用")),
                        "isRejection": is_rejection,
                        "requirementGroup": _requirement_group(category, clause),
                        "quantity": _required_count(clause),
                        "timeLimit": "",
                        "confidence": 0.82 if in_qualification_section else 0.68,
                    }
                )
                if len(requirements) >= 32:
                    break
            if len(requirements) >= 32:
                break
        if len(requirements) >= 32:
            break

    warnings: list[str] = []
    if not requirements:
        warnings.append("未识别到明确资格准入条款，请确认粘贴的是招标公告正文而不是中标、结果或新闻页面。")
    return {
        "project": project,
        "requirements": requirements,
        "warnings": warnings,
    }


def _json_from_model(content: str) -> dict[str, Any] | None:
    clean = content.strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        value = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            value = json.loads(clean[start : end + 1])
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) else None


def _normalize_model_extraction(value: dict[str, Any]) -> dict[str, Any] | None:
    raw_requirements = value.get("requirements")
    if not isinstance(raw_requirements, list):
        return None
    requirements: list[dict[str, Any]] = []
    for raw in raw_requirements[:32]:
        if not isinstance(raw, dict):
            continue
        text = _safe_text(raw.get("text"), 1000)
        if not text or not _is_eligibility_requirement(text, in_qualification_section=True):
            continue
        category = str(raw.get("category") or _category_for(text)).lower()
        if category not in ALLOWED_CATEGORIES:
            category = _category_for(text)
        quantity = raw.get("quantity")
        quantity = int(quantity) if isinstance(quantity, (int, float)) else _required_count(text)
        confidence = raw.get("confidence", 0.7)
        try:
            confidence = min(max(float(confidence), 0), 1)
        except (TypeError, ValueError):
            confidence = 0.7
        requirements.append(
            {
                "id": str(raw.get("id") or f"llm-{len(requirements) + 1:03d}"),
                "category": category,
                "text": text,
                "section": _safe_text(raw.get("section") or "资格条件", 120),
                "sourceLocator": _safe_text(raw.get("sourceLocator"), 120),
                "mandatory": bool(raw.get("mandatory", True)),
                "isRejection": bool(raw.get("isRejection", category == "rejection")),
                "requirementGroup": _safe_text(raw.get("requirementGroup"), 80)
                or _requirement_group(category, text),
                "quantity": quantity,
                "timeLimit": _safe_text(raw.get("timeLimit"), 120),
                "confidence": confidence,
            }
        )

    project = value.get("project") if isinstance(value.get("project"), dict) else {}
    return {
        "project": {
            key: _safe_text(project.get(key), 180)
            for key in (*PROJECT_FIELD_KEYS, "noticeType", "tendererAddress", "tendererContact", "tendererPhone", "agencyAddress", "agencyContact", "agencyPhone")
            if _safe_text(project.get(key), 180)
        },
        "requirements": requirements,
        "warnings": [
            warning
            for item in value.get("warnings", [])
            if (warning := _safe_text(item, 240))
            and not any(field_key in warning for field_key in (*PROJECT_FIELD_KEYS, "noticeType"))
        ][:8],
    }


def _merge_project_fields(rule_project: dict[str, str], model_project: dict[str, str], source_text: str) -> dict[str, str]:
    merged = dict(rule_project)
    compact_source = _compact(source_text)
    for key, value in model_project.items():
        if merged.get(key) or not value:
            continue
        if _compact(value) in compact_source:
            merged[key] = value
    return merged


def _merge_requirements(
    rule_requirements: list[dict[str, Any]],
    model_requirements: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for requirement in [*rule_requirements, *model_requirements]:
        key = _compact(requirement.get("text"))
        if not key or key in seen:
            continue
        if any(key in existing or existing in key for existing in seen if min(len(existing), len(key)) >= 12):
            continue
        seen.add(key)
        merged.append({**requirement, "id": f"req-{len(merged) + 1:03d}"})
    return merged[:32]


def extract_announcement_requirements(text: str, ai: dict[str, Any]) -> dict[str, Any]:
    rule_result = extract_requirements_by_rule(text)
    if rule_result["project"].get("noticeType") in RESULT_NOTICE_TYPES:
        return {
            **rule_result,
            "extractionMethod": "rule",
            "modelUsed": "",
            "extractionWarning": _result_notice_warning(str(rule_result["project"].get("noticeType", ""))),
        }
    if not all(_ai_settings(ai)):
        return {
            **rule_result,
            "extractionMethod": "rule",
            "modelUsed": "",
            "extractionWarning": "未配置大模型，已使用规则抽取。",
        }

    prompt = (
        "你是招投标公告结构化抽取助手。必须把项目基础信息与资格准入条款严格分开。"
        "project 只放基础信息；requirements 只放投标人/供应商资格准入条件，不得放项目名称、编号、预算、采购范围、时间地点或联系方式。"
        "只提取原文明确写出的内容，不得推断企业是否满足。"
        "输出严格 JSON，不使用 Markdown。JSON 格式为："
        '{"project":{"projectName":"","projectCode":"","tendererName":"","agencyName":"","procurementMethod":"","noticeType":"",'
        '"tenderNoticeDate":"","noticeDate":"","budgetAmount":"","maxPrice":"","serviceType":"","serviceLocation":"","bidDeadline":"","openingTime":"",'
        '"bidLocation":"","openingLocation":"","projectDuration":"","warrantyRequirement":"","bidCopyRequirement":"",'
        '"packagingRequirement":"","signatureSealRequirement":"","jointVentureRequirement":""},'
        '"requirements":[{"id":"req-001","category":"qualification|performance|personnel|financial|region|credit|rejection|technical|other",'
        '"section":"资格条件","sourceLocator":"原文位置","requirementGroup":"enterprise_qualification|personnel_qualification|'
        'project_performance|financial_condition|credit_condition|consortium_condition",'
        '"text":"原始资格要求","mandatory":true,"isRejection":false,"quantity":null,"timeLimit":"","confidence":0.0}],"warnings":[]}'
        "。企业资质、人员资质、业绩、财务和信用要求应分别分类；每条 requirement 只保留一个可核验资格条件。"
        "warnings 仅用于公告类型不适合资格判断、正文疑似截断或原文存在冲突，不要为普通基础字段缺失生成警告。"
    )
    model_text = build_announcement_model_input(text, limit=3600)
    cache_key = _announcement_extraction_cache_key(text, ai, model_text)
    cached = _cached_announcement_extraction(cache_key)
    if cached is not None:
        return cached

    result = call_openai_compatible(
        ai,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"公告相关章节切片如下，请仅基于这些原文片段抽取：\n{model_text}"},
        ],
        max_tokens=2500,
    )
    if _is_gateway_timeout(result):
        fallback_text = build_announcement_model_input(text, limit=1800)
        result = call_openai_compatible(
            ai,
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"公告相关章节切片如下（已进一步压缩，仅提取最关键的可核验要求）：\n{fallback_text}",
                },
            ],
            max_tokens=1200,
        )
    if result["success"]:
        normalized = _normalize_model_extraction(_json_from_model(result["content"]) or {})
        if normalized:
            merged_project = _merge_project_fields(rule_result["project"], normalized["project"], text)
            merged_requirements = _merge_requirements(
                rule_result["requirements"],
                normalized["requirements"],
            )
            return _remember_announcement_extraction(cache_key, {
                **normalized,
                "project": merged_project,
                "requirements": merged_requirements,
                "warnings": list(dict.fromkeys([*rule_result["warnings"], *normalized["warnings"]])),
                "extractionMethod": "llm+rule",
                "modelUsed": result["model"],
                "extractionWarning": "",
            })
        warning = "模型未返回可用 JSON，已回退到规则抽取。"
    else:
        warning = f"{result['error']}，已回退到规则抽取。"
    return {
        **rule_result,
        "extractionMethod": "rule",
        "modelUsed": "",
        "extractionWarning": warning,
    }


def _record_terms(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("name", "aliases", "industry", "description", "category", "keywordIds"):
        value = record.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    result: list[str] = []
    for value in values:
        cleaned = _compact(value)
        if len(cleaned) >= 3:
            result.append(cleaned)
        for part in re.split(r"[/、，,;；|与和及()（）\-]+", value):
            part = _compact(part)
            if len(part) >= 3:
                result.append(part)
        for domain_term in (
            "数字孪生",
            "三维可视化",
            "智慧园区",
            "数据中心",
            "低空",
            "实训",
            "智能体",
            "数据治理",
            "企业架构",
            "智慧矿山",
            "电力",
        ):
            if domain_term in value:
                result.append(_compact(domain_term))
    return list(dict.fromkeys(result))


def _matching_records(requirement: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = _compact(requirement)
    matched: list[dict[str, Any]] = []
    for record in records:
        if any(term in text for term in _record_terms(record)):
            matched.append(record)
    return matched


def _evidence_for_records(records: list[dict[str, Any]], knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("evidences"))}
    material_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("materials"))}
    source_map = {str(item.get("id")): item for item in _list_of_dicts(knowledge.get("sourceFiles"))}
    evidence_material_map: dict[str, str] = {}
    for material_id, material in material_map.items():
        for evidence_id in material.get("evidenceIds") or []:
            evidence_material_map.setdefault(str(evidence_id), material_id)
    result: list[dict[str, Any]] = []

    for record in records:
        evidence_ids = list(record.get("evidenceIds") or [])
        for material_id in record.get("materialIds") or []:
            material = material_map.get(str(material_id), {})
            evidence_ids.extend(material.get("evidenceIds") or [])
        resolved = [evidence_map.get(str(evidence_id)) for evidence_id in evidence_ids]
        resolved = [item for item in resolved if item]
        if not resolved:
            result.append(
                {
                    "id": str(record.get("id", "")),
                    "name": str(record.get("name", "")),
                    "status": str(record.get("status", "资料已建模")),
                    "level": str(record.get("evidenceLevel", "")),
                    "sourceType": "knowledge_object",
                    "materialId": str((record.get("materialIds") or [""])[0]),
                    "sourceFiles": [],
                }
            )
            continue
        for evidence in resolved:
            source_files = [
                str(source_map.get(str(source_id), {}).get("name", source_id))
                for source_id in evidence.get("sourceIds", [])
            ]
            result.append(
                {
                    "id": str(evidence.get("id", "")),
                    "name": str(evidence.get("name", "")),
                    "status": str(evidence.get("status", "")),
                    "level": str(evidence.get("level", "")),
                    "sourceType": "evidence",
                    "materialId": evidence_material_map.get(str(evidence.get("id", "")), ""),
                    "sourceFileIds": [str(source_id) for source_id in evidence.get("sourceIds", [])],
                    "sourceFiles": source_files,
                    "sourceLocator": str(evidence.get("locator", "")),
                    "excerpt": str(evidence.get("excerpt", "")),
                    "validFrom": str(evidence.get("validFrom", "")),
                    "validUntil": str(evidence.get("validUntil", "")),
                }
            )
    unique: dict[str, dict[str, Any]] = {}
    for evidence in result:
        unique[evidence["id"] or evidence["name"]] = evidence
    return list(unique.values())[:6]


def _evidence_summary(evidences: list[dict[str, Any]]) -> str:
    if not evidences:
        return "企业资料库未找到可关联证据"
    names = [f"{item['name']}（{item['status'] or '状态待核验'}）" for item in evidences]
    return "；".join(names)


def _status_from_evidence(
    category: str,
    matched: list[dict[str, Any]],
    evidences: list[dict[str, Any]],
) -> str:
    if not matched:
        if category in {"qualification", "personnel", "performance", "financial", "technical"}:
            return "待补材料"
        return "待澄清"
    direct_evidences = [
        normalize_evidence(item)
        for item in evidences
        if item.get("sourceType") == "evidence"
    ]
    if any(item.get("isVerified") for item in direct_evidences):
        return "满足"
    if direct_evidences and all(item.get("isExpired") for item in direct_evidences):
        return "不满足"
    return "待补材料" if direct_evidences or matched else "待澄清"


def _candidate_records(category: str, text: str, knowledge: dict[str, Any]) -> list[dict[str, Any]]:
    qualifications = _list_of_dicts(knowledge.get("qualifications"))
    performances = _list_of_dicts(knowledge.get("projectReferences"))
    products = _list_of_dicts(knowledge.get("products"))
    capabilities = _list_of_dicts(knowledge.get("capabilities"))
    scenarios = _list_of_dicts(knowledge.get("industryScenarios"))
    directions = _list_of_dicts(knowledge.get("businessDirections"))
    if category == "qualification":
        return _matching_records(text, qualifications)
    if category == "performance":
        return _matching_records(text, [*performances, *scenarios, *products])
    if category == "technical":
        return _matching_records(text, [*products, *capabilities, *scenarios, *directions])
    return []


def _score_for_status(status: str) -> int:
    return {
        "满足": 100,
        "部分满足": 70,
        "待澄清": 48,
        "待补材料": 22,
        "不满足": 0,
        "不适用": 100,
    }.get(status, 35)


def evaluate_announcement_eligibility(
    text: str,
    company_knowledge: dict[str, Any],
    company_profile: dict[str, Any],
    ai: dict[str, Any],
    source_file_id: str = "",
) -> dict[str, Any]:
    extraction = extract_announcement_requirements(text, ai)
    rows: list[dict[str, Any]] = []
    hard_blockers: list[dict[str, Any]] = []
    weights = {
        "qualification": 24,
        "performance": 22,
        "personnel": 16,
        "financial": 12,
        "region": 8,
        "credit": 8,
        "rejection": 10,
        "technical": 20,
        "other": 10,
    }
    total_weight = 0
    weighted_score = 0.0

    for requirement in extraction["requirements"]:
        category = str(requirement.get("category") or "other")
        label = CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"])
        requirement_contract = normalize_requirement(
            requirement,
            source_stage="announcement",
            layer=2,
            kind=label,
            source_file_id=source_file_id,
        )
        requirement_text = str(requirement_contract["rawText"])
        matched = _candidate_records(category, requirement_text, company_knowledge)
        evidences = _evidence_for_records(matched, company_knowledge)
        status = _status_from_evidence(category, matched, evidences)

        if "联合体" in requirement_text:
            status = "待澄清"
            evidences = []
        elif category == "region":
            regions = _list_of_dicts(company_profile.get("service_regions"))
            matching_regions = _matching_records(requirement_text, regions)
            if matching_regions:
                status = "待补材料"
                evidences = [
                    {
                        "id": str(item.get("name", "")),
                        "name": f"服务区域：{item.get('name', '')}",
                        "status": str(item.get("status", "待核验")),
                        "level": "",
                        "sourceType": "knowledge_object",
                        "sourceFiles": [],
                    }
                    for item in matching_regions
                ]
            else:
                status = "待澄清"
                evidences = []
        elif category in {"credit", "rejection"}:
            status = "待澄清"
            evidences = []

        risk = "低" if status in {"满足", "不适用"} else "中" if status in {"部分满足", "待澄清"} else "高"
        suggestion = {
            "满足": "仍需在递交前核验原件、有效期和招标文件附件。",
            "部分满足": "已找到关联本体对象和资料来源，需核验原件、有效期或项目适用性。",
            "待补材料": "资料库未形成可直接核验的材料，请补充或核验后再判断。",
            "待澄清": "当前要求或企业资料无法形成确定结论，请人工核验原始公告和对应材料。",
            "不满足": "当前本体资料未找到对应硬性资质，请确认是否确实缺失或尚未录入。",
            "不适用": "该要求已确认不适用于当前投标主体或本次响应。",
        }[status]
        reason = {
            "满足": "已找到经过核验且当前有效的直接企业证据。",
            "部分满足": "已找到相关企业对象或证据，但适用范围仍需确认。",
            "待补材料": "已找到相关企业对象，但直接证明材料尚未核验或未完整入库。",
            "待澄清": "当前公告要求与企业资料之间无法建立确定对应关系。",
            "不满足": "现有直接证据显示关键条件已失效或明确不符合。",
            "不适用": "该要求经人工确认不适用于本次响应。",
        }[status]
        row = build_comparison(
            requirement_contract,
            status=status,
            reason=reason,
            matched_objects=matched,
            evidences=evidences,
            risk=risk,
            suggestion=suggestion,
        )
        row["evidence"] = _evidence_summary(row["evidenceItems"])
        rows.append(row)

        weight = weights.get(category, weights["other"])
        total_weight += weight
        weighted_score += weight * _score_for_status(status)
        if (
            row["mandatory"]
            and status == "不满足"
            and row["isRejection"]
        ):
            hard_blockers.append(
                {
                    "requirementId": row["id"],
                    "title": f"{label}未满足",
                    "detail": requirement_text,
                }
            )

    score = round(weighted_score / total_weight) if total_weight else 0
    notice_type = str(extraction["project"].get("noticeType", ""))
    if notice_type in RESULT_NOTICE_TYPES:
        decision = "无法完成资格判断，请补充原始招标公告或招标文件"
        eligibility_status = "待澄清"
        should_get_tender_document = False
    elif hard_blockers:
        decision = "不建议参与"
        eligibility_status = "不满足"
        should_get_tender_document = False
    elif score >= 70:
        decision = "建议获取招标文件后复核"
        eligibility_status = "部分满足" if any(row["status"] != "满足" for row in rows) else "满足"
        should_get_tender_document = True
    elif score >= 45:
        decision = "人工重点评估"
        eligibility_status = "待澄清"
        should_get_tender_document = True
    else:
        decision = "不建议参与"
        eligibility_status = "待澄清"
        should_get_tender_document = False

    if notice_type in RESULT_NOTICE_TYPES:
        risks = [
            {
                "level": "高",
                "title": "公告类型不适合资格评估",
                "detail": _result_notice_warning(notice_type),
            }
        ]
    else:
        risks = [
            {
                "level": row["risk"],
                "title": f"{row['kind']}需要关注",
                "detail": f"{row['requirement']}。{row['suggestion']}",
            }
            for row in rows
            if row["risk"] != "低"
        ]
    if not risks:
        risks = [{"level": "低", "title": "暂未发现明显风险", "detail": "仍需核验原始公告和证明材料。"}]

    summary = summarize_comparisons(rows)
    return {
        "rows": rows,
        "risks": risks,
        "score": score,
        "decision": decision,
        "summary": summary,
        "project": extraction["project"],
        "extraction": {
            "method": extraction["extractionMethod"],
            "modelUsed": extraction["modelUsed"],
            "cacheHit": bool(extraction.get("extractionCacheHit")),
            "warning": extraction["extractionWarning"],
            "warnings": extraction.get("warnings", []),
        },
        "eligibility": {
            "eligibilityScore": score,
            "eligibilityStatus": eligibility_status,
            "assessmentAvailable": notice_type not in RESULT_NOTICE_TYPES,
            "hardBlockers": hard_blockers,
            "rejectionRisks": [
                {
                    "requirementId": str(row.get("requirementId", "")),
                    "title": f"{row.get('kind', '否决条款')}：{row.get('status', '待澄清')}",
                    "detail": str(row.get("rawText", "")),
                    "sourceLocator": str(row.get("sourceLocator", "")),
                }
                for row in rows
                if row.get("isRejection") and row.get("status") not in {"满足", "不适用"}
            ],
            "shouldGetTenderDocument": should_get_tender_document,
            "recommendation": decision,
        },
    }
