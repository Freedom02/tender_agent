"""技术能力单元（TechUnit）：把 M3 图关系拼成匹配侧的统一技术画像。

依据本体关系（ontology_defs.REL_MAPPING）：
  TechnicalProduct —has_parameter→ TechnicalParameter（productId）
  TechnicalProduct —certified_by→  Credential         （productId）

匹配层用 productId 把三类事实 join 成一个 TechUnit，供技术类「需求↔参数对齐 + 覆盖率」
判定使用，不再把产品 / 参数 / 产品级认证拍平成孤立、截断的扁平片段。本模块只做投影与
拼装，不参与打分。
"""
from __future__ import annotations

from typing import Any

# 供 recall 选择的孤儿参数兜底单元 ID（无 productId 的参数归到此单元）
ORPHAN_UNIT_ID = "__tech_orphan__"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _data_gap(unit: dict[str, Any]) -> str:
    """返回该技术单元的数据质量缺口说明；无缺口返回空串。

    - 有参数 → 无缺口；
    - 无参数但有功能描述 → "无技术参数"（资源包/案例类可能本就无参数）；
    - 无参数且无功能描述 → "无技术参数且缺少功能描述"（平台/引擎类只录了壳）。
    """
    if unit.get("parameters"):
        return ""
    if _text(unit.get("description")):
        return "无技术参数"
    return "无技术参数且缺少功能描述"


def build_tech_units(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    """ledger = company_facts.build_company_ledger(facts) 的返回值。

    返回 tech_units 列表：每个单元 = 一个产品 + 其参数（has_parameter）+ 其产品级认证
    （certified_by）。无 productId 的孤儿参数兜底成一个独立单元，避免技术参数被静默丢弃。
    """
    products = [p for p in (ledger.get("products") or []) if isinstance(p, dict)]
    parameters = [p for p in (ledger.get("parameters") or []) if isinstance(p, dict)]
    certificates = [c for c in (ledger.get("certificates") or []) if isinstance(c, dict)]

    params_by_product: dict[str, list[dict[str, Any]]] = {}
    for param in parameters:
        pid = _text(param.get("productId"))
        params_by_product.setdefault(pid, []).append(param)

    creds_by_product: dict[str, list[dict[str, Any]]] = {}
    for cert in certificates:
        # 只取产品级认证（certified_by 边）：_source == "credential" 且带 productId。
        if cert.get("_source") != "credential":
            continue
        pid = _text(cert.get("productId"))
        if not pid:
            continue
        creds_by_product.setdefault(pid, []).append(cert)

    units: list[dict[str, Any]] = []
    for product in products:
        pid = _text(product.get("productId"))
        unit = {
            "productId": pid,
            "productName": _text(product.get("productName")),
            "aliases": _text(product.get("aliases")),
            "category": _text(product.get("category")),
            "brand": _text(product.get("brand")),
            "model": _text(product.get("model")),
            "description": _text(product.get("description")),
            "applicableScenario": _text(product.get("applicableScenario")),
            "parameters": params_by_product.get(pid, []),
            "credentials": creds_by_product.get(pid, []),
        }
        unit["dataGap"] = _data_gap(unit)
        units.append(unit)

    # 孤儿参数（无 productId）：兜底成独立单元，方便 LLM 召回时仍可被选中。
    orphan_params = params_by_product.get("", [])
    if orphan_params:
        units.append(
            {
                "productId": ORPHAN_UNIT_ID,
                "productName": "（组织技术参数，未挂产品）",
                "aliases": "",
                "category": "",
                "brand": "",
                "model": "",
                "description": "",
                "applicableScenario": "",
                "parameters": orphan_params,
                "credentials": [],
            }
        )

    return units


def render_unit_compact(unit: dict[str, Any]) -> str:
    """用于 LLM 召回的单行摘要（只给 name + 一句话，控制 token）。

    召回阶段只按相关性选产品，不在此提示数据缺口——否则弱命名的旗舰产品会被
    提前排除，导致对齐/后处理阶段都拿不到它、数据缺口信号反而丢失。
    """
    name = _text(unit.get("productName")) or _text(unit.get("productId")) or "（未命名产品）"
    desc = _text(unit.get("description"))
    if not desc and unit.get("parameters"):
        desc = _text(unit["parameters"][0].get("description"))
    summary = desc[:80] + ("…" if len(desc) > 80 else "")
    return f"[ID:{_text(unit.get('productId'))}] {name}｜{summary}" if summary else f"[ID:{_text(unit.get('productId'))}] {name}"


def render_unit_full(unit: dict[str, Any]) -> str:
    """用于对齐判定的完整渲染：description / applicableScenario / 参数不按 120 字截断，
    只设较大上限以避免单次调用上下文爆炸。"""
    lines = [
        f"[ID:{_text(unit.get('productId'))}] {_text(unit.get('productName')) or '（未命名产品）'}",
    ]
    if unit.get("aliases"):
        lines.append(f"别名：{unit['aliases'][:400]}")
    if unit.get("category"):
        lines.append(f"类别：{unit['category'][:200]}")
    if unit.get("description"):
        lines.append(f"功能描述：{unit['description'][:2000]}")
    if unit.get("applicableScenario"):
        lines.append(f"适用场景：{unit['applicableScenario'][:1000]}")
    if unit.get("dataGap"):
        lines.append(f"⚠️ 数据不完整：{unit['dataGap']}（企业台账缺失，非能力缺失）")
    for i, p in enumerate((unit.get("parameters") or [])[:20], start=1):
        lines.append(f"参数{i}：[ID:{_text(p.get('parameterId'))}] {_text(p.get('description'))[:800]}")
    for i, c in enumerate((unit.get("credentials") or [])[:10], start=1):
        name = _text(c.get("credentialName"))
        extra = "；".join(
            x
            for x in (
                _text(c.get("credentialType")),
                _text(c.get("issuer")),
                _text(c.get("validUntil")),
            )
            if x
        )
        lines.append(f"认证{i}：{name}（{extra[:300]}）" if extra else f"认证{i}：{name}")
    return "\n".join(lines)


def project_matched_unit(
    unit: dict[str, Any],
    matched_param_ids: list[str] | None = None,
    matched_fragment: str = "",
) -> dict[str, Any]:
    """把技术匹配命中的产品和参数投影成前端可展示的最小详情。

    匹配结果只需要返回当前条款命中的参数，不把产品的全部参数重复复制到每条
    技术要求中。旧字段 ``matchedParams`` 保留用于兼容，新增
    ``matchedParameterDetails`` 用于展示参数正文和证据路径。
    """
    wanted = [str(value).strip() for value in (matched_param_ids or []) if str(value).strip()]
    wanted_set = set(wanted)
    details: list[dict[str, Any]] = []
    for parameter in unit.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        parameter_id = _text(parameter.get("parameterId"))
        if parameter_id not in wanted_set:
            continue
        details.append(
            {
                "parameterId": parameter_id,
                "productId": _text(parameter.get("productId")) or _text(unit.get("productId")),
                "description": _text(parameter.get("description")),
                "evidencePath": _text(parameter.get("evidencePath")),
                "evidenceUpdatedAt": _text(parameter.get("evidenceUpdatedAt")),
            }
        )

    return {
        "productId": _text(unit.get("productId")),
        "productName": _text(unit.get("productName")),
        "category": _text(unit.get("category")),
        "brand": _text(unit.get("brand")),
        "model": _text(unit.get("model")),
        "productDescription": _text(unit.get("description")),
        "dataGap": _text(unit.get("dataGap")),
        "matchedParams": wanted,
        "matchedParameterDetails": details,
        "matchedFragment": _text(matched_fragment),
    }
