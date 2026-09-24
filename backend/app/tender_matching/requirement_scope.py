from __future__ import annotations

import re
from typing import Any


FORM_TITLE_PATTERNS = (
    "技术和服务要求响应表",
    "商务和技术偏离表",
    "技术规格偏离表",
    "技术响应表",
    "商务响应表",
    "响应表",
    "偏离表",
    "投标报价表",
    "分项报价表",
)

PLACEHOLDER_FIELD_PATTERN = (
    r"(?:统一社会信用代码|单位名称|投标人名称|供应商名称|地址|联系人|联系电话|"
    r"电子邮箱|邮箱|邮编|开户行|账号|国别|品牌|规格型号|制造商|型号|产地)"
)
PLACEHOLDER_VALUE_PATTERN = r"(?:[_＿\-—]{2,}|\.{2,}|。{2,}|…{2,}|空白|待填写|请填写)"


def _safe_text(value: Any, limit: int = 1200) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _strip_title_marker(value: str) -> str:
    return re.sub(
        r"^(?:[①②③④⑤⑥⑦⑧⑨⑩]|[一二三四五六七八九十百]+[、.．]|"
        r"\d+(?:[、.．])?|[（(][一二三四五六七八九十百\d]+[)）])\s*",
        "",
        value,
    ).strip()


def is_form_title(text: str) -> bool:
    value = _safe_text(text, 120)
    if not value or len(value) > 80:
        return False
    title = _strip_title_marker(value)
    return any(title == pattern or title.endswith(pattern) for pattern in FORM_TITLE_PATTERNS)


def is_placeholder_row(text: str) -> bool:
    value = _safe_text(text, 1200)
    if not value:
        return False
    matches = re.findall(
        rf"{PLACEHOLDER_FIELD_PATTERN}\s*[:：]\s*{PLACEHOLDER_VALUE_PATTERN}",
        value,
        flags=re.IGNORECASE,
    )
    return len(matches) >= 2
