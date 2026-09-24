from __future__ import annotations

import re
from datetime import date
from typing import Any

from .contracts import INVALID_TERMS, POSITIVE_VERIFICATION_TERMS

"""确定性匹配器：规则比对（等级/数量/日期/金额/地域），不依赖 LLM。

移植自 BidDataHub（app/matcher/qualification.py、personnel_solver.py、similarity.py、
config.py），字段名已按 M3 本体调整（见 docs/匹配引擎开发计划.md §8.2）。

返回约定：
  - 返回 dict = 能确定性判定，status ∈ 6 态契约；
  - 返回 None = 无法确定性判定 / 找不到对应记录，由路由决定兜底。
金额口径：contractAmount / revenue / assetAmount 单位为「元」，需求里的
「X万元/X万」经 _parse_amount_yuan 归一化到元后比较；annualRevenueWan /
totalAssetsWan 为「万元」，兜底时 ×10000。
"""

# ------------------------------------------------------------------ 常量

# 等级序：特级 > 壹级/一级/甲级 > 贰级/二级/乙级 > 叁级/三级/丙级 > 丁级。
# 注意「甲级」与「壹级」同档（不同资质体系），「乙级」与「贰级」同档，不可跨档抬高。
LEVEL_RANK = {
    "特级": 6,
    "壹级": 5, "一级": 5, "甲级": 5,
    "贰级": 4, "二级": 4, "乙级": 4,
    "叁级": 3, "三级": 3, "丙级": 3,
    "丁级": 2,
    "不分等级": 1, "无": 0, "": 0,
}

QUALIFICATION_LEVELS = ["特级", "壹级", "一级", "贰级", "二级", "叁级", "三级", "甲级", "乙级", "丙级", "不分等级"]

QUAL_INDUSTRY_KEYS = [
    "建筑工程", "市政公用", "水利水电", "公路", "机电", "电力", "通信", "矿山",
    "港口与航道", "铁路", "石油化工", "冶炼", "装饰装修", "钢结构", "地基基础",
    "起重设备", "防水防腐", "电子与智能化", "消防设施", "建筑幕墙", "古建筑",
    "城市及道路照明", "环保", "房建", "市政", "石油", "化工",
]

PERF_INDUSTRY_KEYS = [
    "建筑", "市政", "水利", "公路", "桥梁", "隧道", "房建", "住宅", "公共建筑",
    "商业", "学校", "医院", "电力", "机电", "装修", "装饰", "钢结构", "钢构",
    "地铁", "轨道交通", "场馆", "体育", "厂房", "园区", "市政道路", "给排水", "园林",
    "数字孪生", "可视化", "智慧园区", "平台", "系统", "数据", "物联", "智能体",
]

PERSONNEL_TYPE_KEYWORDS = [
    "注册监理工程师", "注册造价师", "注册结构师", "注册建筑师", "建造师",
    "技术负责人", "工程师", "安全员", "施工员", "质量员",
]

PERSONNEL_LEVEL_RANK = {"高级": 4, "一级": 3, "壹级": 3, "中级": 2, "二级": 2, "贰级": 2, "初级": 1}

REGION_KEYS = [
    "福建", "北京", "上海", "天津", "重庆", "广东", "江苏", "浙江", "山东", "河南",
    "河北", "四川", "湖北", "湖南", "安徽", "江西", "辽宁", "陕西", "云南", "广西",
    "山西", "贵州", "内蒙古", "吉林", "黑龙江", "新疆", "甘肃", "海南", "宁夏",
    "青海", "西藏",
]

# 硬件采购项名词：用于识别需要单独核验的服务器、传感器等规格要求。
_HARDWARE_NOUNS = (
    "机柜", "机架", "传感器", "控制器", "控制箱", "网关", "交换机", "路由器",
    "服务器", "内存", "硬盘", "网口", "网卡", "CPU", "处理器", "主频",
    "DDR", "LoRaWAN", "LoRa", "RFID", "工控机", "显示器", "摄像头",
    "电表", "水表", "采集器",
)

# 硬件规格化语境：名词附近必须出现「比较符+数字」或「数字+单位」，才算采购规格；
# 否则「接入传感器数据」「兼容 LoRaWAN 协议」这类软件能力描述不应命中硬件。
_HARDWARE_COMPARATORS = (
    "不低于", "不少于", "不高于", "不超过", "不小于", "不大于", "至少", "最多",
    "最大", "最小", "最高", "最低", "大于", "小于", "等于", "≥", "≤", ">", "<",
)
_HARDWARE_UNITS = (
    "U", "GB", "TB", "GHz", "MHz", "Mbps", "Gbps", "Mb", "Gb", "bit",
    "个", "台", "套", "路", "核", "位", "W", "V", "A", "℃", "°C",
    "英寸", "米", "mm", "cm",
)
_HARDWARE_SPEC_RE = re.compile(
    r"(?:"
    + "|".join(re.escape(c) for c in _HARDWARE_COMPARATORS)
    + r")\s*\d+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?\s*(?:"
    + "|".join(re.escape(u) for u in _HARDWARE_UNITS)
    + r")"
)


def _has_hardware_spec(text: str) -> bool:
    """文本里是否存在「硬件名词 + 数值规格」的规格化语境。"""
    for noun in _HARDWARE_NOUNS:
        for match in re.finditer(re.escape(noun), text):
            start = max(0, match.start() - 10)
            window = text[start: match.end() + 45]
            if _HARDWARE_SPEC_RE.search(window):
                return True
    return False


_MEMORY_CAPACITY_PATTERNS = (
    re.compile(
        r"(?:内存(?:配置容量|容量)?|DDR\d*\s*内存)"
        r"[^。；;\n]{0,30}?(\d+(?:\.\d+)?)\s*(TB|GB|G)(?![A-Za-z])",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"(\d+(?:\.\d+)?)\s*(TB|GB|G)(?![A-Za-z])"
        r"[^。；;\n]{0,20}?(?:内存|DDR\d*)",
        flags=re.IGNORECASE,
    ),
)


def _memory_capacity_gb(text: str) -> float | None:
    values: list[float] = []
    for pattern in _MEMORY_CAPACITY_PATTERNS:
        for match in pattern.finditer(str(text or "")):
            value = float(match.group(1))
            unit = match.group(2).upper()
            values.append(value * 1024 if unit == "TB" else value)
    return max(values) if values else None

# 金额：必须带单位（元/万/万元/亿元），避免把「不少于3个」里的 3 误判为金额。
_AMOUNT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(亿元|万元|万|元)")
_CN_DIGITS = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "零": 0, "〇": 0}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}

# ------------------------------------------------------------------ 工具

def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("，", "")
    if not text or text.lower() in ("none", "null", "nan"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _split_text(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [p for p in re.split(r"[、,，;；\n]+", str(value)) if p.strip()]


def _to_int(raw: Any) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    # 支持多字中文数词：十一=11、二十=20、二十三=23、一百零五=105、一万=10000。
    if not all(ch in _CN_DIGITS or ch in _CN_UNITS for ch in text):
        return None
    total = 0
    current = 0
    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        else:
            unit = _CN_UNITS[ch]
            if current == 0:
                current = 1  # "十" 单独出现 = 10
            total += current * unit
            current = 0
    return total + current


def _year_from(value: Any) -> int | None:
    m = re.search(r"(\d{4})", str(value or ""))
    return int(m.group(1)) if m else None


def _parse_amount_yuan(text: str) -> float | None:
    """解析要求里的金额并归一化到「元」；无单位金额返回 None。"""
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    if unit == "亿元":
        return value * 100_000_000
    if unit in ("万", "万元"):
        return value * 10_000
    return value  # 元


def _parse_count(text: str) -> int | None:
    m = re.search(r"(?:不少于|至少|不低于|须|需)\s*([0-9]+|[一二两三四五六七八九十]+)\s*[个项名位人]", text)
    if m:
        return _to_int(m.group(1))
    m = re.search(r"([0-9]+|[一二两三四五六七八九十]+)\s*[名个位人]", text)
    return _to_int(m.group(1)) if m else None


def _parse_years(text: str) -> int | None:
    m = re.search(r"近\s*([0-9]+|[一二两三四五六七八九十]+)\s*年", text)
    return _to_int(m.group(1)) if m else None


def _fact_id(fact: dict[str, Any]) -> str:
    for key in (
        "qualificationId", "credentialId", "performanceId", "staffId",
        "financialEvidenceId", "parameterId", "productId", "organizationId",
    ):
        if fact.get(key):
            return str(fact[key])
    return ""


def check_date_valid(valid_until: Any, today: date | None = None) -> bool:
    """validUntil 是否仍有效（>= today）。空串/无法解析 → True（不误杀）。"""
    text = str(valid_until or "").strip()
    if not text:
        return True
    today = today or date.today()
    try:
        return date.fromisoformat(text[:10]) >= today
    except ValueError:
        return True


def check_count_requirement(required: int, actual: int) -> bool:
    """「不少于/至少 N 个」 vs 实际条数。"""
    return actual >= required


def _result(status: str, fact_ids: list[str], reason: str, enterprise_value: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "matchedFactIds": fact_ids,
        "reason": reason,
        "enterprise_value": enterprise_value,
        "statusSource": "确定性规则",
    }


def _organization_subject_type(organization: dict[str, Any] | None) -> str:
    org = organization or {}
    raw = " ".join(
        str(org.get(key) or "")
        for key in ("organizationType", "organizationName")
    )
    if "事业" in raw:
        return "institution"
    if "社会团体" in raw or "社团" in raw:
        return "social_group"
    if "自然人" in raw or "个人" in raw:
        return "natural_person"
    if any(term in raw for term in ("企业", "公司", "有限", "股份", "法人")):
        return "enterprise"
    return ""


def subject_type_required_by_text(req_text: str) -> str:
    """从条款原文解析其所要求的主体类型。

    返回单一主体类型；若未提及任何主体类型，或同时提及多种主体类型
    （"按主体类型择一"的多分支整条，如"投标人为企业的…；投标人为事业单位的…"），
    返回空串表示"不锁死单一主体"，交由匹配层按关键词命中（避免先命中"事业单位"
    把本应匹配"营业执照"的企业分支误判掉）。
    """
    value = re.sub(r"\s+", "", str(req_text or ""))
    types: set[str] = set()
    if "投标人为事业单位" in value or "事业单位的" in value:
        types.add("institution")
    if "投标人为社会团体" in value or "社会团体的" in value:
        types.add("social_group")
    if "投标人为自然人" in value or "自然人的" in value:
        types.add("natural_person")
    if "投标人为合伙企业" in value or "个体工商户" in value:
        types.add("partnership_or_individual_business")
    if "投标人为非企业专业服务机构" in value or "非企业专业服务机构" in value:
        types.add("non_enterprise_professional")
    if "其他投标人应按照" in value:
        types.add("other_subject")
    if "投标人为企业" in value or "企业法人" in value:
        types.add("enterprise")
    if len(types) == 1:
        return next(iter(types))
    return ""


def subject_type_matches_requirement(req_text: str, organization: dict[str, Any] | None) -> bool | None:
    required = subject_type_required_by_text(req_text)
    if not required:
        return None
    actual = _organization_subject_type(organization)
    if not actual:
        return None
    return required == actual


# ------------------------------------------------------------------ 5 个匹配函数

def parse_qualification_req(text: str) -> dict[str, str]:
    """拆解资质要求 → {"category": str, "level": str}。"""
    # 剔除「（…等行业除外 / 不包括…）」等排除性说明，避免把排除列表里的行业词
    # （如「电力」）误当成资质行业关键词，导致匹配到无关证书。
    cleaned = re.sub(r"[（(][^）)]*除外[^）)]*[）)]", " ", text)
    # 剔除「A、B、C等行业[:：]/以…身份/不包括…」这类定义性/排除性枚举。
    # 例如「银行、保险、石油石化、电力、电信等行业：以法人身份参加投标的…」是解释
    # 「单位负责人」的定义，不是资质要求，枚举里的「电力」不能当行业关键词。
    cleaned = re.sub(
        r"[\u4e00-\u9fa5]{2,}(?:[、，,][\u4e00-\u9fa5]{2,})+等行业(?=[:：以除不（(])",
        " ",
        cleaned,
    )
    cleaned = re.sub(r"[、，,;；\s]*除外", " ", cleaned)
    level = ""
    for lv in QUALIFICATION_LEVELS:
        if lv in text:
            level = lv
            break
    category = cleaned.replace(level, "").strip() if level else cleaned.strip()
    return {"category": category, "level": level}


def _qual_industry_key(category: str) -> str:
    for kw in QUAL_INDUSTRY_KEYS:
        if kw in category:
            return kw
    return ""


def _level_from_text(text: str) -> str:
    """从自由文本抽取资质等级（M3 level 列常为空，等级埋进名称，如「乙级测绘资质证书」）。"""
    for lv in QUALIFICATION_LEVELS:
        if lv in text:
            return lv
    return ""


def _cert_name(cert: dict[str, Any]) -> str:
    return str(cert.get("qualificationName") or cert.get("credentialName") or "")


def _cert_level(cert: dict[str, Any]) -> str:
    return str(cert.get("level") or _level_from_text(_cert_name(cert)))


def match_qualification(req_text: str, certificates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """单个资质要求 vs 证书库（含 QualificationCertificate + Credential）。"""
    req = parse_qualification_req(req_text)
    key = _qual_industry_key(req["category"])
    query = req["category"].strip()
    best: dict[str, Any] | None = None
    for cert in certificates:
        name = _cert_name(cert)
        if not name:
            continue
        if key:
            matched = key in name
        elif query:
            # 无行业词（体系认证/标准类，如 ISO27001）：要求原文与证书名互为子串
            matched = query in name or name in query
        else:
            matched = False
        if not matched:
            continue
        if best is None or LEVEL_RANK.get(_cert_level(cert), 0) > LEVEL_RANK.get(_cert_level(best), 0):
            best = cert

    if best is None:
        return None  # 找不到匹配证书，交给路由兜底

    cert_name = _cert_name(best)
    cert_level = _cert_level(best)
    cert_rank = LEVEL_RANK.get(cert_level, 1)
    req_rank = LEVEL_RANK.get(req["level"], 1)
    cert_desc = f"{cert_name} {cert_level}".strip()
    cert_id = _fact_id(best)

    if req["level"] and req_rank > cert_rank:
        return _result("不满足", [cert_id], f"招标要求『{req['level']}』，企业仅有『{cert_level or '无等级'}』，等级不足", cert_desc)

    status = str(best.get("verificationStatus") or best.get("status") or "")
    if any(term in status for term in INVALID_TERMS):
        return _result("不满足", [cert_id], f"证书『{cert_name}』状态为『{status}』，已失效", cert_desc)

    # 状态为正向（有效/已核验等）即视为当前有效，优先信任 status，而非 validUntil
    # （M3 里 validUntil 常被误填成 issueDate，如 ISO27001 证书 validUntil=2026-08-04=发证日）。
    if any(term in status for term in POSITIVE_VERIFICATION_TERMS):
        return _result("满足", [cert_id], f"证书核验状态『{status}』，等级满足要求", cert_desc)

    valid_until = best.get("validUntil", "")
    if valid_until and not check_date_valid(valid_until):
        return _result("不满足", [cert_id], f"证书『{cert_name}』已于 {str(valid_until)[:10]} 过期", cert_desc)

    return _result("待补材料", [cert_id], f"证书核验状态为『{status or '未核验'}』，需补充/核验材料后确认是否有效", cert_desc)


def _match_basic_document(req_text: str, company: dict[str, Any]) -> dict[str, Any] | None:
    """基础证照/财务/声明类要求（营业执照、审计报告、中小企业身份）匹配企业基础信息。

    这些要求属于资格审查，但证明材料常挂在 organization / financial_evidence 而非
    qualification_certificate，且要求原文是整句，不能靠全文子串匹配，需按关键词命中。
    """
    organization = company.get("organization") or {}
    subject_match = subject_type_matches_requirement(req_text, organization)
    if subject_match is False:
        return None

    # 豁免/否定语境：材料被"可不提供/无需提供/承诺函"豁免时，不视为需匹配的企业证据，
    # 直接交还路由兜底（待补材料），避免"可不提供营业执照"误判成"已核验营业执照"。
    if any(
        term in req_text
        for term in ("可不提供", "无需提供", "免于提供", "不需提供", "不再要求", "可不提交", "无需提交", "承诺函")
    ):
        return None

    # 营业执照 / 统一社会信用代码
    if any(k in req_text for k in ("营业执照", "统一社会信用代码", "营业执照等证明文件")):
        # 优先匹配具体的营业执照证照记录（credential），避免用 organization 简介冒充证据。
        for item in company.get("certificates", []) or []:
            name = _cert_name(item)
            if "营业执照" in name:
                return _result(
                    "满足",
                    [_fact_id(item)],
                    f"已核验『{name}』，证照齐全",
                    name,
                )
        # 兜底：organization 有统一社会信用代码 → 基础信息已核验，但缺证照文件，需补充复印件。
        code = str(organization.get("unifiedSocialCreditCode") or "")
        if code:
            return _result(
                "部分满足",
                [str(organization.get("organizationId") or "")],
                f"企业统一社会信用代码『{code}』已核验，请补充营业执照复印件/证照文件",
                "营业执照",
            )

    # 审计报告 / 财务状况报告
    if any(k in req_text for k in ("审计报告", "财务状况报告", "财务报告", "财务报表", "财务状况")):
        for fact in company.get("finance", []) or []:
            evidence_type = str(fact.get("evidenceType") or "")
            if "审计" in evidence_type or "财务" in evidence_type:
                fid = _fact_id(fact)
                period = f"{str(fact.get('periodStart') or '')[:10]}~{str(fact.get('periodEnd') or '')[:10]}"
                return _result("满足", [fid], f"已录入『{evidence_type}』（{period}）", evidence_type)

    # 中小企业声明（中小微企业：中型/小型/微型/小微）
    if any(k in req_text for k in ("中小企业", "小微企业", "中小微")):
        size = str(organization.get("enterpriseSize") or "")
        if size and any(c in size for c in ("中", "小", "微")) and "大" not in size:
            return _result(
                "满足",
                [str(organization.get("organizationId") or "")],
                f"企业规模『{size}』，属于中小企业",
                size,
            )

    return None


def match_financial_document(req_text: str, finance: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not any(k in req_text for k in ("审计报告", "财务状况报告", "财务报告", "财务报表", "资信证明", "银行资信")):
        return None
    preferred_years = {int(value) for value in re.findall(r"(20\d{2})年度", req_text)}
    candidates: list[dict[str, Any]] = []
    for fact in finance or []:
        evidence_type = str(fact.get("evidenceType") or fact.get("name") or "")
        blob = " ".join(str(value or "") for value in fact.values())
        if not any(term in blob for term in ("审计", "财务", "资信")) and not any(
            term in evidence_type for term in ("审计", "财务", "资信")
        ):
            continue
        if preferred_years:
            years = {int(value) for value in re.findall(r"(20\d{2})", blob)}
            if years and not (years & preferred_years):
                continue
        candidates.append(fact)
    if not candidates:
        return None
    fact = candidates[0]
    fid = _fact_id(fact)
    evidence_type = str(fact.get("evidenceType") or fact.get("name") or "财务证明")
    status = str(fact.get("verificationStatus") or fact.get("currentStatus") or fact.get("status") or "")
    if any(term in status for term in INVALID_TERMS):
        return _result("不满足", [fid], f"财务证明『{evidence_type}』状态为『{status}』，不可直接使用", evidence_type)
    if status and not any(term in status for term in POSITIVE_VERIFICATION_TERMS):
        return _result("待补材料", [fid], f"已找到『{evidence_type}』，但状态为『{status}』，需核验原件/有效期", evidence_type)
    return _result("满足", [fid], f"已找到可用于响应的『{evidence_type}』", evidence_type)


def parse_personnel_req(text: str) -> dict[str, Any]:
    """解析「一级建造师（建筑工程专业）2名」→ {level, type, major, count}。"""
    level = ""
    for lv in ["高级", "一级", "二级", "壹级", "贰级", "中级", "初级"]:
        if lv in text:
            level = lv
            break
    type_ = ""
    for t in PERSONNEL_TYPE_KEYWORDS:
        if t in text:
            type_ = t
            break
    major = ""
    m = re.search(r"[（(]([\u4e00-\u9fa5]{2,8}?)(?:专业)?[)）]", text)
    if m:
        major = m.group(1).replace("专业", "")
    count = _parse_count(text) or 1
    return {"level": level, "type": type_, "major": major, "count": count}


def _person_matches(parsed: dict[str, Any], person: dict[str, Any]) -> bool:
    # ⚠️ 我方 M3 无 qualification/qual_major 独立字段，拼 professionalCertificates + specialty + title。
    blob = f"{person.get('professionalCertificates', '')} {person.get('specialty', '')} {person.get('title', '')}"
    if parsed["type"] and parsed["type"] not in blob:
        return False
    if parsed["level"]:
        req_rank = PERSONNEL_LEVEL_RANK.get(parsed["level"], 1)
        if not any(lv in blob and rk >= req_rank for lv, rk in PERSONNEL_LEVEL_RANK.items()):
            return False
    if parsed["major"] and parsed["major"] not in blob:
        return False
    return True


def solve_personnel(req_text: str, personnel: list[dict[str, Any]]) -> dict[str, Any] | None:
    """人员约束求解，返回是否足额与缺口。"""
    parsed = parse_personnel_req(req_text)
    if not (parsed["level"] or parsed["type"] or parsed["major"]):
        return None  # 解析不出人员约束，交给路由兜底

    matched = [p for p in personnel if _person_matches(parsed, p)]
    available = [p for p in matched if str(p.get("currentStatus", "在职")) in ("在职", "可用", "")]
    required = parsed["count"]
    satisfied = len(available) >= required
    shortfall = max(0, required - len(available))
    names = "、".join(str(p.get("name", "")) for p in available[:5])
    reason = f"符合条件在职人员 {len(available)} 名，要求 {required} 名"
    if shortfall:
        reason += f"，缺 {shortfall} 名"
    return _result(
        "满足" if satisfied else "不满足",
        [str(p.get("staffId") or "") for p in available],
        reason,
        names,
    )


def parse_performance_req(text: str) -> dict[str, Any]:
    """→ {"amount_yuan": float|None, "years": int|None, "industry_words": list[str]}。"""
    amount_yuan = _parse_amount_yuan(text)
    years = _parse_years(text)
    industry_words = [kw for kw in PERF_INDUSTRY_KEYS if kw in text]
    return {"amount_yuan": amount_yuan, "years": years, "industry_words": industry_words}


def score_performance(req_text: str, projects: list[dict[str, Any]]) -> dict[str, Any] | None:
    """业绩约束判定：金额（元）/年限/行业/数量。"""
    req = parse_performance_req(req_text)
    count = _parse_count(req_text)
    if not (req["amount_yuan"] or req["years"] or req["industry_words"] or count):
        return None  # 无任何可解析约束，交给路由兜底

    if not projects:
        return _result("不满足", [], "企业业绩库为空")

    now_year = date.today().year

    def passes(project: dict[str, Any]) -> bool:
        amount = _to_float(project.get("contractAmount")) or 0.0
        if req["amount_yuan"] and amount < req["amount_yuan"]:
            return False
        year = _year_from(project.get("completionDate")) or _year_from(project.get("signedDate"))
        if req["years"] is not None:
            if year is None or (now_year - year) > req["years"]:
                return False
        if req["industry_words"]:
            blob = f"{project.get('industry', '')} {project.get('caseName', '')} {project.get('serviceScope', '')}"
            if not any(kw in blob for kw in req["industry_words"]):
                return False
        return True

    passed = [p for p in projects if passes(p)]
    matched_ids = [str(p.get("performanceId") or "") for p in passed]

    if count is not None:
        status = "满足" if len(passed) >= count else "不满足"
        reason = f"符合条件业绩 {len(passed)} 条，要求不少于 {count} 条"
        if status == "不满足":
            reason += f"，缺 {count - len(passed)} 条"
    else:
        status = "满足" if passed else "不满足"
        reason = f"找到 {len(passed)} 条符合金额/年限/行业要求的业绩" if passed else "未找到符合金额/年限/行业要求的业绩"

    return _result(status, matched_ids, reason, f"{len(passed)} 条")


def match_financial(
    req_text: str,
    finance: list[dict[str, Any]],
    organization: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """财务指标比对：净资产/资产总额/营业收入 ≥ 阈值（元）。"""
    threshold = _parse_amount_yuan(req_text)
    if threshold is None:
        return None

    if any(kw in req_text for kw in ("净资产", "资产总额", "总资产")):
        field, org_field = "assetAmount", "totalAssetsWan"
    elif any(kw in req_text for kw in ("营业收入", "营收", "年收入")):
        field, org_field = "revenue", "annualRevenueWan"
    else:
        return None  # 指标不明确，交给路由兜底

    values = [v for v in (_to_float(f.get(field)) for f in finance) if v is not None]
    best = max(values) if values else None
    if best is None and organization:
        org_val = _to_float(organization.get(org_field))
        if org_val is not None:
            best = org_val * 10_000  # 万元 → 元
    if best is None:
        return None  # 无财务数据

    satisfied = best >= threshold
    return _result(
        "满足" if satisfied else "不满足",
        [],
        f"企业{('净资产' if field == 'assetAmount' else '营业收入')} {best:.0f} 元，"
        f"{'≥' if satisfied else '<'} 要求 {threshold:.0f} 元",
        f"{best:.0f} 元",
    )


def match_region(req_text: str, organization: dict[str, Any]) -> dict[str, Any] | None:
    """地域要求：检查 organization.regionScope 是否覆盖要求地域。"""
    if not organization:
        return None
    scope = _split_text(organization.get("regionScope"))
    if not scope:
        return None

    if any("全国" in s for s in scope):
        return _result("满足", [str(organization.get("organizationId") or "")], "服务区域覆盖全国", "全国")

    req_regions = [kw for kw in REGION_KEYS if kw in req_text]
    if not req_regions:
        return None  # 无地域词，交给路由兜底

    for rr in req_regions:
        if any(rr in s or s in rr for s in scope):
            return _result("满足", [str(organization.get("organizationId") or "")], f"服务区域覆盖『{rr}』", "、".join(scope))

    return _result("不满足", [], f"服务区域不覆盖要求地域『{'、'.join(req_regions)}』", "、".join(scope))


def match_technical_parameter(
    req_text: str,
    products: list[dict[str, Any]],
    parameters: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Conservatively compare hardware specifications.

    Missing hardware facts are a material gap, never an explicit mismatch.
    ``不满足`` is returned only when the requirement and an enterprise fact
    expose a comparable metric and the enterprise value is lower.
    """
    if not _has_hardware_spec(req_text):
        return None
    hardware_facts: list[dict[str, Any]] = []
    for fact in (*products, *parameters):
        blob = " ".join(str(v) for v in fact.values() if v not in (None, ""))
        if _has_hardware_spec(blob):
            hardware_facts.append(fact)
    if not hardware_facts:
        return _result(
            "待补材料",
            [],
            "该条款属于硬件采购规格，企业台账没有可比较的硬件产品或参数，需补充材料后确认",
        )

    required_memory = _memory_capacity_gb(req_text)
    memory_facts = [
        (fact, _memory_capacity_gb(" ".join(str(value) for value in fact.values())))
        for fact in hardware_facts
    ]
    comparable_memory = [
        (fact, value)
        for fact, value in memory_facts
        if value is not None
    ]
    if required_memory is not None and comparable_memory:
        best_fact, best_memory = max(comparable_memory, key=lambda item: item[1])
        fact_id = _fact_id(best_fact)
        satisfied = best_memory >= required_memory
        return _result(
            "满足" if satisfied else "不满足",
            [fact_id] if fact_id else [],
            (
                f"企业硬件内存 {best_memory:g}GB，"
                f"{'≥' if satisfied else '<'} 要求 {required_memory:g}GB"
            ),
            f"{best_memory:g}GB",
        )
    return _result(
        "待补材料",
        [fact_id for fact_id in (_fact_id(fact) for fact in hardware_facts) if fact_id],
        "企业台账存在硬件资料，但当前规则无法比较该规格，需人工核验对应参数",
    )


# ------------------------------------------------------------------ 分发器

def deterministic_match(req_text: str, category: str, company: dict[str, Any]) -> dict[str, Any] | None:
    """按 category 选确定性函数；无法判定返回 None。"""
    if category == "qualification":
        if subject_type_matches_requirement(req_text, company.get("organization")) is False:
            return None
        result = match_qualification(req_text, company.get("certificates", []))
        if result:
            return result
        return _match_basic_document(req_text, company)
    if category == "performance":
        return score_performance(req_text, company.get("projects", []))
    if category == "personnel":
        return solve_personnel(req_text, company.get("personnel", []))
    if category == "financial":
        result = match_financial(req_text, company.get("finance", []), company.get("organization"))
        if result:
            return result
        return match_financial_document(req_text, company.get("finance", []))
    if category == "region":
        return match_region(req_text, company.get("organization") or {})
    if category == "technical":
        return match_technical_parameter(req_text, company.get("products", []), company.get("parameters", []))
    return None


# ------------------------------------------------------------------ 路由表

# (method, 要查的企业类)。method ∈ deterministic | hybrid | semantic | manual
ROUTING: dict[str, tuple[str, list[str]]] = {
    "qualification": ("deterministic", ["certificates"]),
    "performance":   ("deterministic", ["projects"]),
    "personnel":     ("deterministic", ["personnel"]),
    "financial":     ("deterministic", ["finance"]),
    "region":        ("deterministic", ["organization"]),
    "technical":     ("hybrid",       ["products", "parameters"]),
    "compliance":    ("hybrid",       ["certificates"]),
    "credit":        ("manual",       []),
    "service_plan":  ("semantic",     ["capabilities", "products"]),
    "commercial":    ("manual",       []),
    "document":      ("manual",       []),
    "pricing":       ("manual",       []),
    "contract":      ("manual",       []),
    "rejection":     ("manual",       []),
    "other":         ("semantic",     []),
}

MANUAL_REASONS: dict[str, str] = {
    "credit": "企业侧无结构化信用黑名单数据，需人工核验信用中国/失信记录",
    "commercial": "商务条款（交付期/质保/售后）需商务人员确认",
    "document": "投标文件格式/签章/装订要求需人工逐项核对",
    "pricing": "报价需商务人员根据最新成本/策略填写",
    "contract": "合同条款需法务确认",
    "rejection": "否决条款为高风险项，不可自动判定，必须人工复核",
}
