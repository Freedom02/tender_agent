from __future__ import annotations

import json
import re
from typing import Any

from .eligibility import call_openai_compatible
from .tech_units import project_matched_unit, render_unit_compact, render_unit_full

"""语义精排：LLM 单条判定，只兜底确定性匹配覆盖不了的模糊要求。

状态契约：输出只能取 4 态（满足/部分满足/不满足/待补材料），
LLM 若返回「需人工核验/待澄清/不适用/不确定」等别名，统一归一化到「待补材料」。
"""

_VALID_STATUSES = {"满足", "部分满足", "不满足", "待补材料"}
_STATUS_ALIASES = {
    "需人工核验": "待补材料", "需人工确认": "待补材料", "人工核验": "待补材料",
    "待澄清": "待补材料", "不确定": "待补材料", "不适用": "待补材料",
}

# category → 企业台账来源键
CATEGORY_SOURCES: dict[str, list[str]] = {
    "qualification": ["certificates"],
    "performance": ["projects"],
    "personnel": ["personnel"],
    "financial": ["finance"],
    "technical": ["products", "parameters"],
    "compliance": ["products", "parameters", "certificates", "capabilities"],
    "service_plan": ["capabilities", "products"],
    "other": ["certificates", "projects", "personnel", "finance", "products", "parameters", "capabilities"],
}

_SYSTEM_PROMPT = (
    "你是资深招投标合规评审专家。你的任务是：对一条招标要求，判断企业给定的候选事实能否满足。\n"
    "\n"
    "硬性规则：\n"
    "1. 只依据【招标要求原文】和【候选事实】判断，禁止补写、禁止臆测企业没有的事实。\n"
    "2. 金额、数量、日期、资质等级必须按数值比较：\n"
    "   - 要求\"不少于3个\"而事实只有2个 → 不满足（数量不足）\n"
    "   - 要求\"一级\"而事实是\"二级\" → 不满足（等级不足）\n"
    "   - 证书 validUntil 早于今天 → 不满足（已过期）\n"
    "3. 事实里没有对应信息、或信息不足以判断时，输出\"待补材料\"，不要硬判满足。\n"
    "4. 否决性要求（废标/无效投标/否决）不确定时，宁可\"待补材料\"也不能判\"满足\"。\n"
    "\n"
    "输出格式（只输出一个 JSON 对象，不要解释文字，不要 Markdown，字段固定）：\n"
    '{"status": "满足|部分满足|不满足|待补材料", '
    '"matchedFactIds": ["支持本判定的候选事实ID（填 [ID:xxx] 中的 xxx，不带 ID: 前缀，可空数组）"], '
    '"reason": "一句话判定依据", "confidence": 0.0到1.0之间的小数}'
)


def extract_json(content: Any) -> dict[str, Any] | None:
    """从 LLM 返回文本稳健提取 JSON（支持 ```json 代码块包裹，[]/{}/{} 兜底）。"""
    if not content:
        return None
    text = str(content)
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1)
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
        return parsed[0]

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return parsed[0]
        except ValueError:
            pass
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except ValueError:
            pass
    return None


def extract_json_list(content: Any) -> list[dict[str, Any]]:
    """从 LLM 返回文本稳健提取 JSON 数组（支持 ```json 代码块包裹，单对象兜底）。"""
    if not content:
        return []
    text = str(content)
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        text = m.group(1)
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, list):
        return [x for x in parsed if isinstance(x, dict)]
    if isinstance(parsed, dict):
        return [parsed]

    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
            if isinstance(parsed, list):
                return [x for x in parsed if isinstance(x, dict)]
        except ValueError:
            pass
    return []


def _fact_id(fact: dict[str, Any]) -> str:
    for key in (
        "qualificationId", "credentialId", "performanceId", "staffId",
        "financialEvidenceId", "parameterId", "productId", "organizationId",
    ):
        if fact.get(key):
            return str(fact[key])
    return ""


def _fact_label(fact: dict[str, Any]) -> str:
    name = (
        fact.get("qualificationName") or fact.get("credentialName") or fact.get("caseName")
        or fact.get("name") or fact.get("evidenceType") or fact.get("productName")
        or fact.get("description") or fact.get("organizationName") or ""
    )
    extra: list[str] = []
    if fact.get("level"):
        extra.append(f"等级:{fact['level']}")
    if fact.get("verificationStatus"):
        extra.append(f"核验:{fact['verificationStatus']}")
    elif fact.get("currentStatus"):
        extra.append(f"状态:{fact['currentStatus']}")
    if fact.get("validUntil"):
        extra.append(f"有效至:{fact['validUntil']}")
    if fact.get("contractAmount") not in (None, ""):
        extra.append(f"金额:{fact['contractAmount']}元")
    if fact.get("completionDate"):
        extra.append(f"完成:{fact['completionDate']}")
    if fact.get("industry"):
        extra.append(f"行业:{fact['industry']}")
    if fact.get("professionalCertificates"):
        extra.append(f"证书:{fact['professionalCertificates']}")
    if fact.get("specialty"):
        extra.append(f"专业:{fact['specialty']}")
    if fact.get("title"):
        extra.append(f"职称:{fact['title']}")
    if fact.get("region"):
        extra.append(f"区域:{fact['region']}")
    description = str(fact.get("description") or "").strip()
    if description:
        extra.append(f"说明:{description[:120]}")
    if fact.get("applicableScenario"):
        extra.append(f"场景:{fact['applicableScenario']}")
    if fact.get("aliases"):
        extra.append(f"别名:{str(fact['aliases'])[:80]}")
    return f"{name} | {' | '.join(extra)}" if extra else str(name)


def recall_candidates(
    category: str,
    company: dict[str, Any],
    top_k: int = 5,
    req_text: str = "",
) -> list[dict[str, Any]]:
    """按类别取对应企业类 + 关键词粗筛 + 跨类均衡采样；量大了再换 embedding。"""
    keys = CATEGORY_SOURCES.get(category, CATEGORY_SOURCES["other"])
    buckets: list[list[dict[str, Any]]] = []
    for key in keys:
        facts = [f for f in (company.get(key, []) or []) if isinstance(f, dict)]
        if req_text:
            # 过滤为空 = 该桶无关键词命中（不含「无关键词则保留全量」的兜底，避免无关事实混入）
            facts = _rough_filter(facts, req_text)
        buckets.append(facts)

    # 跨类均衡：按「来源类」轮询取，避免单一来源（如 certificates 79 条）占满 top_k 挤出产品/参数
    candidates: list[dict[str, Any]] = []
    row = 0
    while len(candidates) < top_k:
        added = False
        for facts in buckets:
            if row < len(facts):
                candidates.append(facts[row])
                added = True
                if len(candidates) >= top_k:
                    break
        if not added:
            break
        row += 1
    return candidates


_STOPWORDS = {
    "投标人", "供应商", "申请人", "应当", "不得", "具有", "具备", "提供", "满足", "符合",
    "要求", "近三年", "不少于", "不低于", "不低于", "至少", "以及", "或者", "并", "且",
}


def _extract_terms(req_text: str) -> list[str]:
    """抽取召回关键词：拉丁 token（ThingJS/ISO27001）+ 中文 2-4 字滑动窗口（捕获「数字孪生」等内容词）。

    贪心 {2,6} 会把「数字孪生」错切成「备数字孪生」，导致召回漏匹配；滑动窗口 + 去重更稳。
    """
    terms: list[str] = []
    terms.extend(re.findall(r"[A-Za-z][A-Za-z0-9]{1,}", req_text))
    for run in re.findall(r"[\u4e00-\u9fa5]+", req_text):
        for width in (2, 3, 4):
            for i in range(len(run) - width + 1):
                terms.append(run[i : i + width])
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        if term in seen or term in _STOPWORDS:
            continue
        seen.add(term)
        out.append(term)
    return out


def _rough_filter(candidates: list[dict[str, Any]], req_text: str) -> list[dict[str, Any]]:
    terms = _extract_terms(req_text)
    if not terms:
        return candidates
    result = []
    for fact in candidates:
        blob = " ".join(str(v) for v in fact.values() if v not in (None, ""))
        if any(term in blob for term in terms):
            result.append(fact)
    return result


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip()
    if status in _VALID_STATUSES:
        return status
    return _STATUS_ALIASES.get(status, "待补材料")


def _strip_id_prefix(value: Any) -> str:
    v = str(value or "").strip()
    return v[3:] if v.upper().startswith("ID:") else v


def semantic_judge(
    req_text: str,
    category: str,
    candidates: list[dict[str, Any]],
    ai: dict[str, Any],
) -> dict[str, Any] | None:
    """LLM 判定单条要求；失败返回 None（路由兜底「不确定」）。"""
    if not candidates:
        return None

    candidate_lines = "\n".join(
        f"{index}. [ID:{_fact_id(fact)}] {_fact_label(fact)}"
        for index, fact in enumerate(candidates, start=1)
    )
    user_prompt = (
        f"【招标要求】{req_text}\n"
        f"【要求分类】{category}\n"
        f"【候选事实】（企业库中可能相关的事实，金额单位为元）\n{candidate_lines}\n"
        "\n请判断该要求是否满足，输出一个 JSON 对象。"
    )
    result = call_openai_compatible(
        ai,
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1500,
        temperature=0,
    )
    if not result.get("success"):
        return None

    data = extract_json(result.get("content", ""))
    if not isinstance(data, dict):
        return None

    matched = [i for i in (_strip_id_prefix(value) for value in (data.get("matchedFactIds") or [])) if i]
    reason = str(data.get("reason") or "").strip()
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5

    return {
        "status": _normalize_status(data.get("status")),
        "matchedFactIds": matched,
        "reason": reason or "语义判定完成，需结合上下文复核",
        "missing": str(data.get("missing") or ""),
        "confidence": confidence,
        "statusSource": "语义判定",
    }


_BATCH_SYSTEM_PROMPT = (
    "你是资深招投标合规评审专家。你的任务是：对多条招标要求，逐条判断企业给定的候选事实能否满足。\n"
    "\n"
    "硬性规则：\n"
    "1. 只依据每条要求自己的【原文】和【候选事实】判断，禁止补写、禁止臆测企业没有的事实，禁止跨条串用候选。\n"
    "2. 金额、数量、日期、资质等级必须按数值比较（不少于/不低于/至少→下限，不高于/不超过→上限，一级>二级等）。\n"
    "3. 事实里没有对应信息、或信息不足以判断时，输出\"待补材料\"，不要硬判满足。\n"
    "4. 否决性要求（废标/无效投标/否决）不确定时，宁可\"待补材料\"也不能判\"满足\"。\n"
    "\n"
    "输出格式（只输出一个 JSON 数组，不要解释文字，不要 Markdown）：\n"
    '数组长度必须等于要求条数，元素按顺序对应【要求1】到【要求N】，每个元素字段固定为：\n'
    '{"status": "满足|部分满足|不满足|待补材料", '
    '"matchedFactIds": ["本条候选里支持判定的ID（填 [ID:xxx] 中的 xxx，不带 ID: 前缀，可空数组）"], '
    '"reason": "一句话判定依据", "confidence": 0.0到1.0之间的小数}'
)


def semantic_judge_batch(
    items: list[dict[str, Any]],
    ai: dict[str, Any],
) -> list[dict[str, Any] | None]:
    """一次 LLM 调用判定多条要求，返回与 items 对齐的结果列表（失败项为 None）。

    items 元素字段：req_text / category / candidates（list[fact]）。
    """
    if not items:
        return []

    blocks: list[str] = []
    for index, item in enumerate(items, start=1):
        candidate_lines = "\n".join(
            f"{j}. [ID:{_fact_id(fact)}] {_fact_label(fact)}"
            for j, fact in enumerate(item.get("candidates") or [], start=1)
        ) or "（无候选事实）"
        blocks.append(
            f"【要求{index}｜分类:{item.get('category', 'other')}】{item.get('req_text', '')}\n"
            f"候选事实：\n{candidate_lines}"
        )

    user_prompt = (
        "请逐条判断以下各条招标要求是否满足，每条独立判断、互不影响。\n\n"
        + "\n\n".join(blocks)
        + "\n\n请输出 JSON 数组。"
    )

    result = call_openai_compatible(
        ai,
        [
            {"role": "system", "content": _BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=min(8000, 800 + 350 * len(items)),
        temperature=0,
    )
    if not result.get("success"):
        return [None] * len(items)

    arr = extract_json_list(result.get("content", ""))
    if not arr:
        return [None] * len(items)

    out: list[dict[str, Any] | None] = []
    for index, item in enumerate(items):
        data = arr[index] if index < len(arr) else None
        if not isinstance(data, dict):
            out.append(None)
            continue
        matched = [i for i in (_strip_id_prefix(value) for value in (data.get("matchedFactIds") or [])) if i]
        reason = str(data.get("reason") or "").strip()
        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        out.append({
            "status": _normalize_status(data.get("status")),
            "matchedFactIds": matched,
            "reason": reason or "语义判定完成，需结合上下文复核",
            "missing": str(data.get("missing") or ""),
            "confidence": confidence,
            "statusSource": "语义判定",
        })
    while len(out) < len(items):
        out.append(None)
    return out


# ------------------------------------------------------------------ 技术类：LLM 召回 + 需求↔参数对齐/覆盖率
# 技术参数多为功能性长句，通用 4 态点分类不够用；这里改成「拆点 → 对齐 → 覆盖率」，
# 再把 coverage 映射回 4 态（满足/部分满足/不满足/待补材料），gaps 作为正交字段挂到 row 上。

_TECH_RECALL_SYSTEM = (
    "你是技术需求匹配助手。给定一组招标技术要求和一组企业技术产品（含一句话描述），"
    "为每条要求选出最可能相关、能支撑其功能点的产品，最多 3 个，按相关度降序。"
    "只依据给定材料判断，禁止补写。输出一个 JSON 数组，长度必须等于要求条数，"
    '每个元素为 {"productIds": ["...", "..."]}（无相关则空数组）。'
)

_TECH_ALIGN_SYSTEM = (
    "你是技术参数对齐评审专家。把一条招标技术要求拆成若干『可核验技术点』，"
    "逐点判断企业产品/参数是否覆盖。只依据给定原文判断，禁止补写、禁止臆测。\n"
    "输出严格 JSON（不要 Markdown）：\n"
    '{"coveredPoints": ["已覆盖的技术点"], "gaps": ["未覆盖或证据不足的技术点"], '
    '"matchedUnits": [{"productId": "产品ID", "matchedParams": ["参数ID"], "matchedFragment": "支撑该点的原文片段"}], '
    '"coverage": 0.0}。coverage = 已覆盖技术点数 / 技术点总数，取值 0~1。'
)


def recall_tech_units(
    req_items: list[dict[str, Any]],
    tech_units: list[dict[str, Any]],
    ai: dict[str, Any],
) -> list[list[dict[str, Any]]]:
    """LLM 召回：分批为每个技术需求返回最相关的 TechUnit（top-k）。

    需求多时单次调用会超出模型稳定返回能力，这里按批（默认 25 条）切分，避免
    上百条需求挤在一次调用里导致 JSON 截断/解析失败。返回与 req_items 对齐的列表。
    """
    if not req_items or not tech_units:
        return [[] for _ in req_items]

    index_lines = "\n".join(render_unit_compact(unit) for unit in tech_units)
    unit_by_id = {str(unit.get("productId") or "").strip(): unit for unit in tech_units}
    selected: list[list[dict[str, Any]]] = [[] for _ in req_items]

    _RECALL_BATCH = 25
    for offset in range(0, len(req_items), _RECALL_BATCH):
        chunk = req_items[offset : offset + _RECALL_BATCH]
        req_blocks = "\n\n".join(
            f"【要求{offset + i + 1}】{item['req_text']}" for i, item in enumerate(chunk)
        )
        user_prompt = (
            f"企业技术产品列表：\n{index_lines}\n\n"
            f"招标技术要求：\n{req_blocks}\n\n"
            "请为每条要求选出相关产品（最多 3 个），输出 JSON 数组（长度等于要求条数）。"
        )
        result = call_openai_compatible(
            ai,
            [
                {"role": "system", "content": _TECH_RECALL_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=min(4000, 200 + 40 * len(chunk)),
            temperature=0,
        )
        arr = extract_json_list(result.get("content", "")) if result.get("success") else []
        for i, item in enumerate(chunk):
            data = arr[i] if i < len(arr) and isinstance(arr[i], dict) else {}
            ids = [str(x).strip() for x in (data.get("productIds") or []) if str(x or "").strip()]
            picked: list[dict[str, Any]] = []
            for pid in ids[:3]:
                unit = unit_by_id.get(pid)
                if unit is not None and unit not in picked:
                    picked.append(unit)
            selected[offset + i] = picked
    return selected


def _tech_result_from_data(
    data: Any,
    available_units: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """把 LLM 对齐输出 JSON 转成 match_result（4 态 + coverage/gaps/matchedUnits）。"""
    if not isinstance(data, dict):
        return None
    covered = [str(x).strip() for x in (data.get("coveredPoints") or []) if str(x or "").strip()]
    gaps = [str(x).strip() for x in (data.get("gaps") or []) if str(x or "").strip()]
    raw_units = [u for u in (data.get("matchedUnits") or []) if isinstance(u, dict)]
    unit_index = {
        str(unit.get("productId") or "").strip(): unit
        for unit in available_units or []
        if isinstance(unit, dict) and str(unit.get("productId") or "").strip()
    }
    matched_units: list[dict[str, Any]] = []
    if available_units is None:
        matched_units = raw_units
    else:
        for raw_unit in raw_units:
            product_id = str(raw_unit.get("productId") or "").strip()
            candidate = unit_index.get(product_id)
            if candidate is None:
                continue
            candidate_params = {
                str(param.get("parameterId") or "").strip()
                for param in candidate.get("parameters") or []
                if isinstance(param, dict) and str(param.get("parameterId") or "").strip()
            }
            raw_params = raw_unit.get("matchedParams") or []
            if not isinstance(raw_params, list):
                raw_params = [raw_params]
            valid_params = [
                str(value).strip()
                for value in raw_params
                if str(value or "").strip() in candidate_params
            ]
            matched_units.append(
                {
                    **raw_unit,
                    "productId": product_id,
                    "matchedParams": valid_params,
                }
            )
    try:
        coverage = min(max(float(data.get("coverage", 0.0)), 0.0), 1.0)
    except (TypeError, ValueError):
        coverage = 0.0

    if available_units is not None and not matched_units:
        gaps = list(dict.fromkeys([*gaps, *covered, "未关联到可核验的企业产品或技术参数"]))
        covered = []
        coverage = 0.0

    matched_fact_ids: list[str] = []
    for mu in matched_units:
        for pid in (mu.get("matchedParams") or []):
            if str(pid or "").strip():
                matched_fact_ids.append(str(pid).strip())
        product_id = str(mu.get("productId") or "").strip()
        if product_id:
            matched_fact_ids.append(product_id)

    # coverage → 4 态。覆盖率 0 通常意味着「未召回/无候选/数据不全」，判「待补材料」
    # 而不是「不满足」——「不满足」是强结论，只在有明确证据时才该下。
    if coverage >= 0.9:
        status = "满足"
    elif coverage > 0:
        status = "部分满足"
    else:
        status = "待补材料"

    reason_parts: list[str] = []
    if covered:
        reason_parts.append("已覆盖：" + "、".join(covered[:6]))
    if gaps:
        reason_parts.append("未覆盖：" + "、".join(gaps[:6]))
    reason = "；".join(reason_parts) or f"技术对齐完成，覆盖率约 {coverage:.0%}"

    return {
        "status": status,
        "matchedFactIds": list(dict.fromkeys(matched_fact_ids)),
        "reason": reason,
        "coverage": coverage,
        "gaps": gaps,
        "technicalAlignment": {
            "coverage": coverage,
            "coveredPoints": covered,
            "gaps": gaps,
            "matchedUnits": matched_units,
        },
        "statusSource": "技术对齐判定",
    }


def _enrich_matched_units(
    result: dict[str, Any],
    tech_units: list[dict[str, Any]],
) -> None:
    """把 LLM 返回的产品/参数 ID 对齐回企业台账，补充可展示的参数正文。"""
    alignment = result.get("technicalAlignment")
    if not isinstance(alignment, dict):
        return

    unit_by_id = {
        str(unit.get("productId") or "").strip(): unit
        for unit in tech_units
        if isinstance(unit, dict) and str(unit.get("productId") or "").strip()
    }
    enriched: list[dict[str, Any]] = []
    for raw_unit in alignment.get("matchedUnits") or []:
        if not isinstance(raw_unit, dict):
            continue
        raw_ids = raw_unit.get("matchedParams") or []
        param_ids = raw_ids if isinstance(raw_ids, list) else [raw_ids]
        param_ids = [str(value).strip() for value in param_ids if str(value).strip()]
        product_id = str(raw_unit.get("productId") or "").strip()
        unit = unit_by_id.get(product_id)
        if unit is None:
            enriched.append(raw_unit)
            continue
        projected = project_matched_unit(
            unit,
            matched_param_ids=param_ids,
            matched_fragment=str(raw_unit.get("matchedFragment") or ""),
        )
        enriched.append({**raw_unit, **projected})
    alignment["matchedUnits"] = enriched


def _tech_fallback() -> dict[str, Any]:
    return {
        "status": "待补材料",
        "matchedFactIds": [],
        "reason": "技术对齐判定不可用，待人工复核",
        "statusSource": "规则兜底",
    }


def _annotate_data_gaps(res: dict[str, Any], units: list[dict[str, Any]]) -> None:
    """命中产品若被标记数据缺口（零参数/缺描述），在 reason 里明示是数据缺口而非能力缺失。

    数据缺口来自 tech_units.build_tech_units 的 unit["dataGap"]；这里只做投影 + 追加提示，
    不改动 LLM 的覆盖判定本身，避免误把「没录数据」当成「能力不足」。
    """
    matched_product_ids = {
        str(mu.get("productId") or "").strip()
        for mu in (res.get("technicalAlignment", {}).get("matchedUnits") or [])
        if isinstance(mu, dict) and str(mu.get("productId") or "").strip()
    }
    unit_by_id = {str(u.get("productId") or "").strip(): u for u in units}
    gaps: list[str] = []
    for pid in sorted(matched_product_ids):
        gap = str((unit_by_id.get(pid) or {}).get("dataGap") or "").strip()
        if gap and gap not in gaps:
            gaps.append(gap)
    if not gaps:
        return
    res["dataGaps"] = gaps
    note = "企业台账数据不完整（" + "；".join(gaps) + "），上述覆盖判定可能低估"
    res["reason"] = (res.get("reason") + "。" + note) if res.get("reason") else note


def judge_technical_requirements(
    req_items: list[dict[str, Any]],
    tech_units: list[dict[str, Any]],
    ai: dict[str, Any],
) -> dict[int, dict[str, Any]]:
    """技术类入口：LLM 召回 → 批量对齐，返回 {index: match_result}。"""
    results: dict[int, dict[str, Any]] = {}
    if not req_items:
        return results

    selected = recall_tech_units(req_items, tech_units, ai)
    name_by_id = {str(u.get("productId") or ""): str(u.get("productName") or "") for u in tech_units}
    _BATCH = 8  # 对齐上下文较长，批次比通用语义判定小
    for offset in range(0, len(req_items), _BATCH):
        chunk = req_items[offset : offset + _BATCH]
        units_chunk = selected[offset : offset + _BATCH]
        blocks: list[str] = []
        for item, units in zip(chunk, units_chunk):
            unit_text = "\n\n".join(render_unit_full(unit) for unit in units) if units else "（无候选产品）"
            blocks.append(f"【要求】{item['req_text']}\n企业产品与参数：\n{unit_text}")

        user_prompt = (
            "请逐条拆解技术点并对齐覆盖，每条输出一个 JSON 对象，整体输出一个 JSON 数组。\n\n"
            + "\n\n".join(blocks)
        )
        result = call_openai_compatible(
            ai,
            [
                {"role": "system", "content": _TECH_ALIGN_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=min(8000, 800 + 900 * len(chunk)),
            temperature=0,
        )
        arr = extract_json_list(result.get("content", "")) if result.get("success") else []
        for local_idx, (item, data) in enumerate(zip(chunk, arr)):
            res = _tech_result_from_data(data, units_chunk[local_idx])
            if res:
                # 给前端补友好产品名和对应产品参数正文（LLM 只回 ID）。
                _enrich_matched_units(res, tech_units)
                for mu in (res.get("technicalAlignment", {}).get("matchedUnits") or []):
                    if isinstance(mu, dict) and not mu.get("productName"):
                        pid = str(mu.get("productId") or "").strip()
                        mu["productName"] = name_by_id.get(pid, pid)
            results[item["index"]] = res or _tech_fallback()
        for local_idx, item in enumerate(chunk):
            if item["index"] not in results:
                results[item["index"]] = _tech_fallback()
            _annotate_data_gaps(results[item["index"]], selected[offset + local_idx])
    return results
