# -*- coding: utf-8 -*-
"""技术参数匹配契约（标书撰写侧唯一对齐基准）。

本模块把「匹配层已经算好的技术参数匹配结果」投影成标书撰写可直接消费的结构，
并统一「技术规格偏离表」和「技术方案逐条响应正文」两条下游对同一份数据的读法。

数据来源（只读，不重新匹配、不调用 LLM、不另造字段）：
  - fineReview.rows                        ← tender_matching/document_matching.py
    每条技术类 comparison row 上的 technicalAlignment.matchedUnits[].matchedParameterDetails[]
    已含「参数ID / 参数正文 / 所属产品 / 证据路径」，本模块只做规整与补齐。
  - company_knowledge                      ← document_recognition/storage.py 的企业台账投影
    作为 technicalAlignment 缺失时的兜底：用 matchedObjectIds 反查 products/capabilities。

产出一个标准化「技术参数匹配记录」ParameterMatch，字段如下（详见 docs/匹配接口与标书制作.md）：

    requirementId / requirement / category      招标要求侧（ID、原文、分类）
    status / deviation / reason / confidence   匹配结论（状态、偏离、理由、置信度）
    coverage / coveredPoints / gaps            技术点覆盖（覆盖点 / 缺口）
    matchedParameters[]                        命中的企业技术参数（参数ID/正文/产品/证据）
    evidenceItems[]                            证明材料（ID/名称/核验状态/定位/摘要）
    sourceLocator / sourceSegmentIds           招标文件溯源
"""
from __future__ import annotations

from typing import Any

from .schemas import DEVIATION_PENDING, STATUS_TO_DEVIATION

# 视为「技术参数匹配」的 requirement 分类（技术 + 服务方案）
_TECH_CATEGORIES = {"technical", "service_plan"}


# ---------------------------------------------------------------------------
# 通用安全读取
# ---------------------------------------------------------------------------

def _text(value: Any, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _list(value: Any) -> list[Any]:
    return [item for item in value] if isinstance(value, (list, tuple)) else []


def _rows(fine_review: Any) -> list[dict[str, Any]]:
    """安全取出 fineReview.rows 中的 dict 行。"""
    if not isinstance(fine_review, dict):
        return []
    rows = fine_review.get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _row_category(row: dict[str, Any]) -> str:
    return str(row.get("category") or row.get("targetModule") or "other")


def _is_technical_row(row: dict[str, Any]) -> bool:
    """是否为需要产出「技术参数匹配」的行。"""
    if _row_category(row) in _TECH_CATEGORIES:
        return True
    return isinstance(row.get("technicalAlignment"), dict)


# ---------------------------------------------------------------------------
# 企业台账索引（technicalAlignment 缺失时的兜底）
# ---------------------------------------------------------------------------

def _knowledge_index(knowledge: Any) -> dict[str, tuple[dict[str, Any], str]]:
    """把 company_knowledge 中 products/capabilities 记录按 id 建索引。

    capabilities 即 technical_parameter 的投影（id == parameterId，name == 参数正文）。
    """
    index: dict[str, tuple[dict[str, Any], str]] = {}
    if not isinstance(knowledge, dict):
        return index
    for list_key in ("products", "capabilities", "parameters"):
        for item in knowledge.get(list_key) or []:
            if not isinstance(item, dict):
                continue
            record_id = str(item.get("id") or "").strip()
            if record_id and record_id not in index:
                index[record_id] = (item, str(list_key))
    return index


def _fact_key_of(list_key: str) -> str:
    return "technical_parameter" if list_key in {"capabilities", "parameters"} else "technical_product"


# ---------------------------------------------------------------------------
# 从 technicalAlignment 提取命中的技术参数
# ---------------------------------------------------------------------------

def alignment_matched_parameters(row: dict[str, Any]) -> list[dict[str, Any]]:
    """把 row.technicalAlignment.matchedUnits 规整成 matchedParameters 列表。

    每个元素：
      parameterId / description / productId / productName / brand / model /
      evidencePath / evidenceUpdatedAt
    """
    alignment = row.get("technicalAlignment")
    if not isinstance(alignment, dict):
        return []
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unit in alignment.get("matchedUnits") or []:
        if not isinstance(unit, dict):
            continue
        product_id = _text(unit.get("productId"), 100)
        product_name = _text(unit.get("productName"), 240)
        brand = _text(unit.get("brand"), 120)
        model = _text(unit.get("model"), 120)
        details = unit.get("matchedParameterDetails")
        if isinstance(details, list) and details:
            for detail in details:
                if not isinstance(detail, dict):
                    continue
                parameter_id = _text(detail.get("parameterId"), 100)
                if not parameter_id or parameter_id in seen:
                    continue
                seen.add(parameter_id)
                result.append(
                    {
                        "parameterId": parameter_id,
                        "description": _text(detail.get("description"), 2000),
                        "productId": _text(detail.get("productId"), 100) or product_id,
                        "productName": product_name,
                        "brand": brand,
                        "model": model,
                        "evidencePath": _text(detail.get("evidencePath"), 500),
                        "evidenceUpdatedAt": _text(detail.get("evidenceUpdatedAt"), 40),
                    }
                )
        # 旧字段 matchedParams 只有参数ID、无正文，兜底用 ID 反查台账补正文。
        for param_id in _list(unit.get("matchedParams")):
            key = str(param_id).strip()
            if key and key not in seen:
                seen.add(key)
                result.append(
                    {
                        "parameterId": key,
                        "description": "",
                        "productId": product_id,
                        "productName": product_name,
                        "brand": brand,
                        "model": model,
                        "evidencePath": "",
                        "evidenceUpdatedAt": "",
                    }
                )
    return result


def _fill_from_knowledge(
    matched_parameters: list[dict[str, Any]],
    row: dict[str, Any],
    knowledge_index: dict[str, tuple[dict[str, Any], str]],
) -> None:
    """补齐参数正文/产品/证据：优先 matchedObjectIds 反查台账，其次按参数ID补正文。"""
    by_id = {mp.get("parameterId"): mp for mp in matched_parameters if mp.get("parameterId")}

    # matchedObjectIds 里可能同时含参数ID与产品ID；按 ID 反查台账补正文/证据。
    for object_id in _list(row.get("matchedObjectIds")):
        key = str(object_id).strip()
        hit = knowledge_index.get(key)
        if not hit:
            continue
        record, list_key = hit
        if key in by_id:
            target = by_id[key]
            if not target.get("description"):
                target["description"] = _text(record.get("name"), 2000)
            if not target.get("evidencePath"):
                target["evidencePath"] = _text(record.get("evidencePath"), 500)
            continue
        # matchedObjectIds 里出现参数ID，但 technicalAlignment 未带参数详情时补一条。
        if _fact_key_of(list_key) == "technical_parameter":
            matched_parameters.append(
                {
                    "parameterId": key,
                    "description": _text(record.get("name"), 2000),
                    "productId": "",
                    "productName": "",
                    "brand": "",
                    "model": "",
                    "evidencePath": _text(record.get("evidencePath"), 500),
                    "evidenceUpdatedAt": _text(record.get("evidenceUpdatedAt"), 40),
                }
            )
            by_id[key] = matched_parameters[-1]


def _evidence_items(row: dict[str, Any]) -> list[dict[str, Any]]:
    """把 row.evidenceItems 投影成精简的证明材料清单。"""
    result: list[dict[str, Any]] = []
    for item in _list(row.get("evidenceItems")):
        if not isinstance(item, dict):
            continue
        evidence_id = str(item.get("id") or item.get("evidenceId") or "").strip()
        name = str(item.get("name") or "").strip()
        if not evidence_id and not name:
            continue
        result.append(
            {
                "id": evidence_id,
                "name": name,
                "status": _text(item.get("verificationStatus") or item.get("status"), 100),
                "sourceLocator": _text(item.get("sourceLocator") or item.get("locator"), 500),
                "excerpt": _text(item.get("excerpt"), 600),
            }
        )
    return result


def build_parameter_match(
    row: dict[str, Any],
    knowledge_index: dict[str, tuple[dict[str, Any], str]],
) -> dict[str, Any]:
    """把一条技术类 comparison row 规整成一条 ParameterMatch 记录。"""
    alignment = row.get("technicalAlignment") if isinstance(row.get("technicalAlignment"), dict) else {}
    matched_parameters = alignment_matched_parameters(row)
    _fill_from_knowledge(matched_parameters, row, knowledge_index)

    status = _text(row.get("status") or "待补材料", 40)
    coverage = alignment.get("coverage")
    try:
        coverage = min(max(float(coverage), 0.0), 1.0)
    except (TypeError, ValueError):
        coverage = 0.0

    return {
        "requirementId": _text(row.get("requirementId") or row.get("id"), 100),
        # requirement 取「逐条参数点」（8 目标通道落在 parameters）；
        # 整行原文另存 requirementFull，供需要完整上下文时回查。
        "requirement": _text(
            row.get("parameters")
            or row.get("normalizedText")
            or row.get("text")
            or row.get("title")
            or row.get("requirement")
            or row.get("rawText"),
            4000,
        ),
        "requirementFull": _text(row.get("requirement") or row.get("rawText"), 4000),
        "category": _row_category(row),
        "status": status,
        "deviation": STATUS_TO_DEVIATION.get(status, DEVIATION_PENDING),
        "reason": _text(row.get("reason"), 2000),
        "confidence": row.get("confidence") if isinstance(row.get("confidence"), (int, float)) else 0.0,
        "coverage": coverage,
        "coveredPoints": [_text(p, 400) for p in _list(alignment.get("coveredPoints")) if _text(p, 400)],
        "gaps": [_text(p, 400) for p in _list(alignment.get("gaps")) if _text(p, 400)],
        "matchedParameters": matched_parameters,
        "evidenceItems": _evidence_items(row),
        "sourceLocator": _text(row.get("sourceLocator"), 500),
        "sourceSegmentIds": [_text(p, 120) for p in _list(row.get("sourceSegmentIds")) if _text(p, 120)],
    }


def build_parameter_matches(
    fine_review: Any,
    company_knowledge: Any,
) -> list[dict[str, Any]]:
    """把 fineReview.rows 中的技术类行规整成 ParameterMatch[] 清单。

    纯函数、确定性、只读输入；无技术类行时返回空列表。
    """
    knowledge_index = _knowledge_index(company_knowledge)
    return [
        build_parameter_match(row, knowledge_index)
        for row in _rows(fine_review)
        if _is_technical_row(row)
    ]


# ---------------------------------------------------------------------------
# 标书撰写两条下游消费
# ---------------------------------------------------------------------------

def parameter_deviation_row(match: dict[str, Any], index: int = 0) -> dict[str, Any]:
    """把一条 ParameterMatch 派生为「技术规格偏离表（参数级）」行。

    相比 schemas.build_deviation_row 的整条对标，本行额外带 matchedParameters 参数列，
    便于把「招标技术指标 ↔ 企业技术参数」一一对照。

    - matchedParameter（响应说明）：只放命中的企业技术参数正文；
    - evidence（证据引用）：只放证明材料路径，不列材料名与正文。
    """
    params = match.get("matchedParameters") or []
    param_text = "；".join(
        f"{p.get('description') or p.get('parameterId')}" for p in params if isinstance(p, dict)
    )
    paths: list[str] = []
    for item in match.get("evidenceItems") or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("sourceLocator") or item.get("locator") or "").strip()
        if path and path not in paths:
            paths.append(path)
    return {
        "no": index + 1,
        "requirementId": match.get("requirementId", ""),
        "requirement": match.get("requirement", ""),
        "status": match.get("status", "待澄清"),
        "deviation": match.get("deviation", DEVIATION_PENDING),
        "matchedParameter": param_text,
        "evidence": "\n".join(paths),
        "suggestion": match.get("reason", ""),
        "sourceLocator": match.get("sourceLocator", ""),
    }


def parameter_deviation_rows(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """ParameterMatch[] → 技术规格偏离表（参数级）行列表。"""
    return [parameter_deviation_row(match, index) for index, match in enumerate(matches)]


def alignment_records(row: dict[str, Any]) -> list[tuple[dict[str, Any], str]]:
    """把 row.technicalAlignment 的命中参数投影成 knowledge 同构的 (record, list_key)。

    供 technical.py 在逐条响应正文里引用「产品 + 参数 + 证据」，与 matchedObjectIds
    反查的结果合并后统一走 sourceRefs 溯源，保证证据图文装配（evidence_assembler）可用。
    """
    records: list[tuple[dict[str, Any], str]] = []
    seen: set[str] = set()
    for mp in alignment_matched_parameters(row):
        if not mp.get("parameterId") or mp.get("parameterId") in seen:
            continue
        seen.add(mp["parameterId"])
        name = mp.get("description") or mp.get("parameterId")
        product = mp.get("productName") or ""
        description = f"所属产品：{product}" if product else ""
        if mp.get("brand") or mp.get("model"):
            description += f"；品牌/型号：{'/'.join(x for x in (mp.get('brand'), mp.get('model')) if x)}"
        records.append(
            (
                {
                    "id": mp["parameterId"],
                    "name": name,
                    "description": description,
                    "factKey": "technical_parameter",
                    "evidencePath": mp.get("evidencePath") or "",
                },
                "technical_parameter",
            )
        )
    return records


def parameter_context(match: dict[str, Any]) -> str:
    """把一条 ParameterMatch 压缩成 prompt/正文里的「企业技术参数」上下文块。"""
    lines: list[str] = []
    for mp in match.get("matchedParameters") or []:
        if not isinstance(mp, dict):
            continue
        desc = mp.get("description") or "（未命名参数）"
        product = mp.get("productName") or ""
        evidence = mp.get("evidencePath") or ""
        part = f"参数：{desc}"
        if product:
            part += f"｜产品：{product}"
        if evidence:
            part += f"｜证据：{evidence}"
        lines.append(f"- {part}")
    if not lines:
        return "（无命中企业技术参数）"
    return "\n".join(lines)
