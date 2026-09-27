from __future__ import annotations

import re
import time
from typing import Any

from .contracts import (
    CLASS_DEFAULT_STATUS,
    CLASS_STATUSES,
    REQUIREMENT_CLASS,
    _normalized_row_status,
    build_comparison,
    coerce_requirement_category,
    normalize_requirement,
    normalize_route_hints,
    requirement_class_for_route,
    requirement_class_of,
    summarize_comparisons,
)
from .document_fine_matching import (
    CATEGORY_LABELS,
    extract_tender_requirements,
)
from .builtin_target_map import TARGET_META
from .deterministic import MANUAL_REASONS, deterministic_match, subject_type_matches_requirement
from .eligibility import call_openai_compatible
from .semantic_judge import (
    extract_json_list,
    judge_technical_requirements,
)
from .tech_units import build_tech_units, project_matched_unit


def _module_label(module_key: Any) -> str:
    """8 域 targetModule 的中文标签；未知键回退「其他要求」。"""
    key = str(module_key or "")
    meta = TARGET_META.get(key)
    return str(meta["label"]) if meta else CATEGORY_LABELS["other"]

SCORE_BY_STATUS = {
    "满足": 100,
    "部分满足": 78,
    "待补材料": 50,
    "不满足": 0,
}

WEIGHTS = {
    "qualification": 18,
    "performance": 14,
    "personnel": 12,
    "financial": 10,
    "credit": 10,
    "region": 8,
    "compliance": 12,
    "technical": 22,
    "service_plan": 14,
    "commercial": 10,
    "document": 8,
    "pricing": 6,
    "contract": 4,
    "rejection": 6,
    "other": 6,
}


def _evidence_summary(evidences: list[dict[str, Any]]) -> str:
    if not evidences:
        return "企业资料库未找到可直接关联的证据"
    parts = []
    for item in evidences:
        locator = f"；{item['locator']}" if item.get("locator") else ""
        parts.append(f"{item['name']}（{item.get('status') or '状态待核验'}{locator}）")
    return "；".join(parts)


def _suggestion_for_status(status: str, category: str, requirement_class: str | None = None) -> str:
    if category == "project_info":
        return "项目基础信息已提取，直接回看原文确认关键字段。"
    if category == "commercial":
        return "请在商务响应和报价编制时确认该条款。"
    if category == "evaluation":
        return "请按评分规则准备对应响应内容和证明材料。"
    if category == "template":
        return "请下载或复用该模板，并在写标工作台中完成填写。"
    if category == "contract":
        return "该合同范围当前按设计忽略，不进入匹配。"
    requirement_class = requirement_class or REQUIREMENT_CLASS.get(category, "writing")
    if requirement_class == "rejection":
        return "请人工核对否决条件，确认是否触发资格审查不合格/符合性审查不合格/投标无效情形。"
    if requirement_class == "writing":
        return "请编制项目理解、实施方案、服务保障、培训或应急预案，并关联可复核的同类交付材料。"
    if requirement_class == "compliance":
        return "按招标文件逐项核验格式、签章、密封、附件、份数和递交要求。"
    if requirement_class == "fill":
        return "请商务/法务人员确认或填写交付、质保、付款、报价及履约责任。"
    return {
        "满足": "已找到已核验的关联证据，递交前仍应复核原件及适用性。",
        "部分满足": "已找到关联本体对象和资料来源，请复核项目适用性与证据原件。",
        "待补材料": "企业台账暂缺对应数据或证据未核验，请补充/核验材料后再复核。",
        "不满足": "当前未发现可以支持该要求的有效证据，请复核资料库和招标要求。",
    }.get(status, "请结合原文与企业资料进行人工确认。")


def _risk_for_status(status: str) -> str:
    if status in {"满足", "已确认安全", "已编制", "已完成"}:
        return "低"
    if status in {"有风险", "不满足"}:
        return "高"
    return "中"


def summarize_fine_review(rows: list[dict[str, Any]], extraction: dict[str, Any]) -> dict[str, Any]:
    # ② 证据匹配区加权分（仅 evidence 类参与）
    weighted_score = 0.0
    total_weight = 0
    unresolved_mandatory = []
    for row in rows:
        if requirement_class_of(row) != "evidence":
            continue
        category = str(row.get("category") or row.get("targetModule") or "other")
        status = _normalized_row_status(row)
        weight = WEIGHTS.get(category, WEIGHTS["other"])
        total_weight += weight
        weighted_score += weight * SCORE_BY_STATUS.get(status, 50)
        if row.get("mandatory") and status in {"部分满足", "待补材料", "不满足"}:
            unresolved_mandatory.append(row)

    score = round(weighted_score / total_weight) if total_weight else 0

    # ① 否决红线区：门槛判定（人工可确认安全后解除）
    rejection_rows = [row for row in rows if requirement_class_of(row) == "rejection"]
    rejection_risks = [
        row for row in rejection_rows
        if _normalized_row_status(row) == "有风险"
    ]
    rejection_pending = [
        row for row in rejection_rows
        if _normalized_row_status(row) not in {"已确认安全", "有风险"}
    ]
    hard_blockers = [
        {
            "requirementId": str(row.get("requirementId", row.get("id", ""))),
        "title": f"{row.get('kind', _module_label(row.get('targetModule')))}：{row.get('status', '待核查')}",
            "detail": str(row.get("requirement", row.get("rawText", ""))),
            "sourceLocator": str(row.get("sourceLocator", "")),
        }
        for row in rejection_risks
    ]
    if hard_blockers:
        recommendation = "不建议参与"
        score = min(score, 40)
    elif rejection_pending:
        recommendation = "人工重点评估"
    elif score >= 85 and not unresolved_mandatory:
        recommendation = "建议进入正式投标准备"
    elif score >= 70:
        recommendation = "有条件参与，先补材料"
    elif score >= 55:
        recommendation = "人工重点评估"
    else:
        recommendation = "暂不建议参与"

    risks = [
        {
            "level": str(row.get("risk", "中")),
            "title": f"{row.get('kind', _module_label(row.get('targetModule')))}：{row.get('status', '待补材料')}",
            "detail": f"{row.get('requirement', '')}。{row.get('suggestion', '')}",
            "sourceLocator": str(row.get("sourceLocator", "")),
        }
        for row in rows
        if row.get("risk") != "低"
    ][:30]
    summary = summarize_comparisons(rows)

    # 待补材料清单（②区 evidence 中状态为"待补材料"的条款）
    pending_material_rows = [
        row for row in rows
        if requirement_class_of(row) == "evidence" and _normalized_row_status(row) == "待补材料"
    ]

    return {
        "rows": rows,
        "risks": risks,
        "score": score,
        "decision": recommendation,
        "recommendation": recommendation,
        "clauseCount": len(rows),
        "statusCounts": summary["statusCounts"],
        "classCounts": summary["classCounts"],
        "summary": summary,
        "hardBlockers": hard_blockers,
        "rejectionRisks": [
            {
                "requirementId": str(row.get("requirementId", row.get("id", ""))),
                "title": f"{row.get('kind', CATEGORY_LABELS['other'])}：{row.get('status', '')}",
                "detail": str(row.get("rawText", row.get("requirement", ""))),
                "sourceLocator": str(row.get("sourceLocator", "")),
            }
            for row in rejection_risks + rejection_pending
        ],
        "unresolvedMandatory": [
            {
                "requirementId": str(row.get("id", "")),
                "title": str(row.get("kind", CATEGORY_LABELS["other"])),
                "detail": str(row.get("requirement", "")),
                "sourceLocator": str(row.get("sourceLocator", "")),
            }
            for row in unresolved_mandatory
        ],
        # 待补材料清单（供前端独立展示）
        "pendingMaterialList": [
            {
                "requirementId": str(row.get("requirementId", row.get("id", ""))),
                "title": f"{row.get('kind', _module_label(row.get('targetModule')))}",
                "detail": str(row.get("requirement", row.get("rawText", ""))),
                "sourceLocator": str(row.get("sourceLocator", "")),
                "importance": str(row.get("importance", "high")),
                "evidenceNeed": str(row.get("evidenceNeed", "")),
            }
            for row in pending_material_rows
        ],
        "analysisPack": extraction.get("analysisPack")
        if isinstance(extraction.get("analysisPack"), dict)
        else None,
        "extraction": extraction,
    }


def update_fine_review_status(
    fine_review: dict[str, Any],
    row_id: str,
    status: str,
    uncertainty_reason: str = "",
    response_value: str | None = None,
    supplemented_materials: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    # 按条款所属分区校验状态合法性
    rows = fine_review.get("rows")
    if not isinstance(rows, list):
        return None
    target = next(
        (row for row in rows if isinstance(row, dict) and str(row.get("id", "")) == row_id),
        None,
    )
    if target is None:
        return None
    requirement_class = requirement_class_of(target)
    valid_statuses = CLASS_STATUSES.get(requirement_class, CLASS_STATUSES["writing"])
    if status not in valid_statuses:
        raise ValueError(f"该分区不支持状态“{status}”，允许：{valid_statuses}")
    previous_status = str(target.get("status") or "")
    target["status"] = status
    if response_value is not None:
        target["responseValue"] = response_value
    target["risk"] = _risk_for_status(status)
    target["riskLevel"] = target["risk"]
    target["suggestion"] = _suggestion_for_status(
        status,
        str(target.get("category") or target.get("targetModule") or "other"),
        requirement_class,
    )
    target["uncertaintyReason"] = uncertainty_reason
    if previous_status != status:
        target["manualStatus"] = status
        target["statusSource"] = "人工审核"
        target["reviewStatus"] = "已人工复核"
        target["reason"] = f"人工复核已将匹配状态更新为“{status}”。"
    if supplemented_materials:
        existing = target.get("supplementedMaterials")
        if not isinstance(existing, list):
            existing = []
        by_rid: dict[str, dict[str, Any]] = {}
        for item in existing:
            if isinstance(item, dict) and item.get("rid"):
                by_rid[str(item["rid"])] = dict(item)
        for item in supplemented_materials:
            if not isinstance(item, dict) or not item.get("rid"):
                continue
            rid = str(item["rid"])
            merged = by_rid.get(rid, {})
            merged.update({k: v for k, v in item.items() if v not in (None, "")})
            by_rid[rid] = merged
        target["supplementedMaterials"] = list(by_rid.values())
    extraction = fine_review.get("extraction")
    if not isinstance(extraction, dict):
        extraction = {}
    return summarize_fine_review(rows, extraction)


def _fact_primary_id(fact: dict[str, Any]) -> str:
    for key in (
        "qualificationId", "credentialId", "performanceId", "staffId",
        "financialEvidenceId", "parameterId", "productId", "organizationId", "id",
    ):
        if fact.get(key):
            return str(fact[key])
    return ""


def _fact_name(fact: dict[str, Any]) -> str:
    name = (
        fact.get("qualificationName") or fact.get("credentialName") or fact.get("caseName")
        or fact.get("name") or fact.get("evidenceType") or fact.get("productName")
        or fact.get("description") or fact.get("organizationName") or ""
    )
    return str(name or _fact_primary_id(fact) or "")


def _facts_by_ids(company: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    wanted = {str(value) for value in ids if value}
    if not wanted:
        return []
    found: list[dict[str, Any]] = []
    for key in ("certificates", "personnel", "projects", "finance", "products", "parameters", "capabilities"):
        for fact in company.get(key, []) or []:
            if isinstance(fact, dict) and _fact_primary_id(fact) in wanted:
                found.append(fact)
    organization = company.get("organization")
    if isinstance(organization, dict) and _fact_primary_id(organization) in wanted:
        found.append(organization)
    return found


def _fact_to_evidence(fact: dict[str, Any]) -> dict[str, Any]:
    """把台账事实投影成 normalize_evidence 可消费的证据条目，保留 evidencePath 溯源。"""
    status = str(fact.get("verificationStatus") or fact.get("currentStatus") or fact.get("status") or "")
    evidence_path = str(fact.get("evidencePath") or "")
    return {
        "id": _fact_primary_id(fact),
        "name": _fact_name(fact),
        "status": status,
        "verificationStatus": status,
        "level": str(fact.get("level", "")),
        "validFrom": str(fact.get("validFrom") or fact.get("issueDate") or ""),
        "validUntil": str(fact.get("validUntil", "")),
        "sourceLocator": evidence_path,
        "sourceType": "evidence" if evidence_path else "knowledge_object",
        "excerpt": str(fact.get("description") or fact.get("caseName") or fact.get("scope") or ""),
        "sourceFiles": [],
        "sourceFileIds": [],
        "materialId": "",
    }


def _recall_fallback(candidates: list[dict[str, Any]], base_reason: str) -> dict[str, Any]:
    """语义判定不可用/失败时，回退到关键词召回结果，保留证据溯源，交人工确认。"""
    ids = [i for i in (_fact_primary_id(f) for f in candidates) if i]
    if ids:
        return {
            "status": "待补材料",
            "matchedFactIds": ids,
            "reason": "语义判定不可用，已按关键词召回相关企业对象，待人工确认是否满足",
            "statusSource": "规则兜底",
        }
    return {
        "status": "待补材料",
        "matchedFactIds": [],
        "reason": base_reason,
        "statusSource": "规则兜底",
    }


def _normalize_tech_text(text: str) -> str:
    """去掉技术条款/参数前导序号、星级标记与空白，便于精确文本比对。"""
    s = str(text or "")
    s = re.sub(r"^[\s★▲※○●]*[（(]?[0-9一二三四五六七八九十百]+[）)、.．\s]*", "", s)
    return re.sub(r"\s+", "", s)


def _tech_exact_match(requirement_text: str, tech_units: list[dict[str, Any]]) -> dict[str, Any] | None:
    """技术类确定性兜底：需求文本与产品参数描述精确/包含匹配时直接判定，不依赖 LLM。

    常见情形：供应商响应表逐条照抄招标技术条款（如「支持在地图摆放器中摆放点、线、面…」），
    需求与台账参数文本几乎一致，但 LLM 召回/对齐——尤其 LLM 不可用或语义召回漏配时——会判
    「不满足」。这里在进入 LLM 前先做确定性文本匹配，避免把「已录参数」误判成「能力缺失」。
    """
    req = _normalize_tech_text(requirement_text)
    if len(req) < 8:
        return None

    exact: list[tuple[dict[str, Any], dict[str, Any]]] = []   # 需求完全被参数覆盖
    partial: list[tuple[dict[str, Any], dict[str, Any]]] = []  # 参数是需求的一部分
    for unit in tech_units:
        for p in unit.get("parameters") or []:
            desc = _normalize_tech_text(p.get("description"))
            if len(desc) < 8:
                continue
            if req in desc or desc == req:
                exact.append((unit, p))
            elif desc in req:
                partial.append((unit, p))

    if not exact and not partial:
        return None

    unit, param = (exact or partial)[0]
    matched_fact_ids = [x for x in dict.fromkeys(
        [str(param.get("parameterId") or "").strip(), str(unit.get("productId") or "").strip()]
    ) if x]
    status = "满足" if exact else "部分满足"
    coverage = 1.0 if exact else 0.5
    covered_points = [str(param.get("description"))[:120]]
    return {
        "status": status,
        "matchedFactIds": matched_fact_ids,
        "reason": f"企业技术参数已明确覆盖（{param.get('parameterId')}）：{str(param.get('description'))[:60]}",
        "statusSource": "确定性规则",
        "technicalAlignment": {
            "coverage": coverage,
            "coveredPoints": covered_points,
            "gaps": [],
            "matchedUnits": [
                project_matched_unit(
                    unit,
                    matched_param_ids=[str(param.get("parameterId") or "")],
                    matched_fragment=str(param.get("description"))[:120],
                )
            ],
        },
    }


def _company_ledger_summary(company_knowledge: dict[str, Any]) -> str:
    """企业台账概要（名称清单），供 full 模式复核「待补材料/部分满足」时做召回参考。"""
    lines: list[str] = []
    for label, key in (
        ("证书", "certificates"),
        ("业绩", "projects"),
        ("人员", "personnel"),
        ("财务", "finance"),
        ("产品", "products"),
    ):
        for fact in company_knowledge.get(key, []) or []:
            name = _fact_name(fact) or _fact_primary_id(fact)
            if name:
                lines.append(f"{label}:{name}")
    return "；".join(lines)[:2000]


def _apply_audit_verdict(res: dict[str, Any] | None, original_status: str, data: dict[str, Any]) -> None:
    """单条复核结论应用。

    - 满足/不满足（确定性结论）被 disagree/uncertain → 降级为待补材料（转人工）
    - 待补材料/部分满足（保守结论）被 disagree → 不改状态，只追加「疑似漏匹配」提示
    """
    if not res:
        return
    verdict = str(data.get("verdict") or "").strip().lower()
    if verdict == "agree":
        return
    reason = str(data.get("reason") or "").strip()
    if original_status in {"满足", "不满足"}:
        res["status"] = "待补材料"
        res["reason"] = (
            f"LLM 复核认为原「{original_status}」结论不可靠"
            f"（{reason or '证据与要求不匹配'}），已降级为待补材料，请人工复核"
        )
        res["statusSource"] = "LLM 复核"
    else:
        hint = f"LLM 复核提示：{reason or '企业台账疑似有可支持证据'}，请人工确认是否升级"
        prev = str(res.get("reason") or "")
        res["reason"] = f"{prev}；{hint}" if prev else hint
        res["statusSource"] = "LLM 复核提示"


def _llm_audit_non_tech_matches(
    requirements: list[dict[str, Any]],
    results: dict[int, dict[str, Any]],
    company_knowledge: dict[str, Any],
    ai: dict[str, Any],
) -> None:
    """对非技术类匹配结论做 LLM 复核（错判降级 / 漏匹配提示）。

    - 只复核非技术类（技术类已有 LLM 对齐，不重复复核）
    - auditScope：
      * strict（默认）：只复核「满足/不满足」（验证任务，证据已给）
      * full：复核「满足/不满足/部分满足/待补材料」（含召回任务，附台账概要）
    - 复核模型：auditModel > matchingModel > model；最终为空则整体跳过
    - 结果应用：满足/不满足 → 待补材料；待补材料/部分满足 → 仅追加提示，不自动升级
    """
    audit_model = (
        str(ai.get("auditModel") or ai.get("matchingModel") or ai.get("model") or "")
        .strip()
    )
    if ai.get("auditEnabled") is False or not audit_model:
        return
    ai_audit = {**ai, "model": audit_model}

    scope = str(ai.get("auditScope") or "strict").strip().lower()
    audit_statuses = (
        {"满足", "不满足"}
        if scope != "full"
        else {"满足", "不满足", "部分满足", "待补材料"}
    )

    # 收集待复核项（非技术类 + 指定状态）
    audit_items: list[dict[str, Any]] = []
    for index, requirement in enumerate(requirements, start=1):
        if str(requirement.get("category") or "other") == "technical":
            continue
        res = results.get(index)
        if not res:
            continue
        status = str(res.get("status") or "")
        if status not in audit_statuses:
            continue
        req_text = str(
            requirement.get("rawText")
            or requirement.get("text")
            or requirement.get("requirement")
            or ""
        ).strip()
        facts = _facts_by_ids(company_knowledge, res.get("matchedFactIds", []))
        evidence = "；".join(_fact_name(f) for f in facts) or "（无企业证据）"
        audit_items.append(
            {"index": index, "req_text": req_text[:600], "status": status, "evidence": evidence}
        )

    if not audit_items:
        return

    # full 模式复核待补材料/部分满足时，附台账概要供召回参考
    ledger_summary = ""
    if scope == "full" and any(i["status"] in {"待补材料", "部分满足"} for i in audit_items):
        ledger_summary = _company_ledger_summary(company_knowledge)

    _BATCH = 8
    for offset in range(0, len(audit_items), _BATCH):
        chunk = audit_items[offset : offset + _BATCH]
        blocks: list[str] = []
        for item in chunk:
            blocks.append(
                f"【序号{item['index']}】要求：{item['req_text']}\n"
                f"系统结论：{item['status']}\n"
                f"匹配证据：{item['evidence']}"
            )
        user_prompt = (
            "逐条复核以下匹配结论，每条输出一个 JSON 对象，整体输出一个 JSON 数组。\n"
            '{"index": 序号, "verdict": "agree"|"disagree"|"uncertain", "reason": "简短理由"}\n\n'
            "判断标准：\n"
            "- 条款是豁免/程序说明（如「提供承诺函即可不提供X」「质疑人需提供X」）却去匹配企业资质 → disagree\n"
            "- 条款要求提供具体文件（如「营业执照复印件」），却只匹配了企业统一信用代码/公司简介 → disagree\n"
            "- 匹配证据与条款要求明显不相关、类型不符或等级不足 → disagree\n"
            "- 拿不准 → uncertain；否则 → agree\n\n"
        )
        if ledger_summary:
            user_prompt += f"企业台账概要（供判断「待补材料/部分满足」是否漏匹配）：{ledger_summary}\n\n"
        user_prompt += "\n\n".join(blocks)
        result = call_openai_compatible(
            ai_audit,
            [
                {
                    "role": "system",
                    "content": "你是招投标资格匹配的复核员，只输出 JSON 数组，不输出其他文字。",
                },
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=min(4000, 300 + 250 * len(chunk)),
            temperature=0,
        )
        arr = extract_json_list(result.get("content", "")) if result.get("success") else []
        for item, data in zip(chunk, arr):
            _apply_audit_verdict(results.get(item["index"]), item["status"], data)
        # LLM 解析失败或缺失条目：保持原结论不动（保守——不因复核失败而降级）


def evaluate_tender_document(
    text: str,
    segments: list[dict[str, Any]],
    company_knowledge: dict[str, Any],
    ai: dict[str, Any],
    source_file_id: str = "",
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    project_id: str = "",
    document_meta: dict[str, Any] | None = None,
    extraction_override: dict[str, Any] | None = None,
    allow_direct_llm_matching: bool = True,
) -> dict[str, Any]:
    # 分模型：提取用强模型（extractionModel），匹配用轻模型（matchingModel），缺省回退 model
    ai_extraction = {**ai, "model": str(ai.get("extractionModel") or ai.get("model") or "")}
    ai_matching = {**ai, "model": str(ai.get("matchingModel") or ai.get("model") or "")}
    # `project_id` / `document_meta` 只用于定位提取产物的落盘目录与产物头部信息，
    # 不参与任何匹配判定，匹配层不因它们改变行为。
    t_start = time.perf_counter()
    extraction = (
        extraction_override
        if isinstance(extraction_override, dict)
        else extract_tender_requirements(
            text,
            segments,
            ai_extraction,
            sections,
            tables,
            source_file_id=source_file_id,
            project_id=project_id,
            document_meta=document_meta,
        )
    )
    t_extracted = time.perf_counter()
    rows: list[dict[str, Any]] = []
    requirements = extraction["requirements"]

    if extraction.get("targetModules") or extraction.get("extractionProvider") == "builtin-target":
        from .builtin_target_processing import build_target_fine_review

        target_review = build_target_fine_review(
            extraction=extraction,
            company_knowledge=company_knowledge,
            ai=ai_matching,
            source_file_id=source_file_id,
        )
        extraction_payload = target_review.get("extraction")
        if not isinstance(extraction_payload, dict):
            extraction_payload = dict(extraction)
        extraction_payload.setdefault("timings", {})
        extraction_payload["timings"].update(
            {
                "extraction": round(t_extracted - t_start, 2),
                "matching": round(time.perf_counter() - t_extracted, 2),
            }
        )
        extraction_payload.setdefault("stats", {})
        extraction_payload["stats"]["targetFineReviewRowCount"] = len(
            target_review.get("rows") or []
        )
        target_review["extraction"] = extraction_payload
        target_review["analysisPack"] = extraction_payload.get("analysisPack")
        return target_review

    # ── 第一遍：按条款性质分区路由 ──
    # ① 否决 / ③ 编制 / ④ 确认填写 → 不查库，给分区默认工作流状态
    # ② 证据匹配 → 查库（确定性/技术对齐）
    results: dict[int, dict[str, Any]] = {}
    tech_req_items: list[dict[str, Any]] = []
    tech_units = build_tech_units(company_knowledge)
    for index, requirement in enumerate(requirements, start=1):
        requirement_text = str(
            requirement.get("rawText")
            or requirement.get("text")
            or requirement.get("requirement")
            or requirement.get("sourceText")
            or ""
        )
        category = coerce_requirement_category(str(requirement.get("category") or "other"), requirement_text)
        if (
            category in {"qualification", "financial", "personnel", "performance", "credit", "region"}
            and subject_type_matches_requirement(requirement_text, company_knowledge.get("organization")) is False
        ):
            category = "document"
        requirement["category"] = category
        route_hints = normalize_route_hints(category, requirement_text, requirement.get("routeHints"))
        requirement["routeHints"] = route_hints
        requirement_class = requirement_class_for_route(category, requirement_text, route_hints)

        # ── 非证据类（①③④）：不查库，给分区默认工作流状态 ──
        if requirement_class != "evidence":
            default_status = CLASS_DEFAULT_STATUS.get(requirement_class, "待编制")
            results[index] = {
                "status": default_status,
                "matchedFactIds": [],
                "reason": MANUAL_REASONS.get(category, "该类别需人工处理"),
                "statusSource": "分区默认",
            }
            continue

        # ── ② 证据匹配 ──
        # 技术类：硬件拦截 → 精确文本 → LLM 对齐
        if category == "technical":
            hw_result = deterministic_match(requirement_text, "technical", company_knowledge)
            if hw_result:
                results[index] = hw_result
                continue
            exact = _tech_exact_match(requirement_text, tech_units)
            if exact:
                results[index] = exact
                continue
            tech_req_items.append({"index": index, "req_text": requirement_text})
            continue

        # 信用：企业侧无结构化数据，标待补材料
        if category == "credit":
            results[index] = {
                "status": "待补材料",
                "matchedFactIds": [],
                "reason": MANUAL_REASONS.get("credit", "需人工核验信用记录"),
                "statusSource": "人工确认",
            }
            continue

        # 其余证据类（资格/业绩/人员/财务/地域）：确定性匹配
        det = deterministic_match(requirement_text, category, company_knowledge)
        if det:
            results[index] = det
            continue
        results[index] = {
            "status": "待补材料",
            "matchedFactIds": [],
            "reason": "企业事实库中未找到对应记录，需补充或核验材料",
            "statusSource": "确定性规则",
        }

    # ── 技术类：LLM 召回 + 需求↔参数对齐/覆盖率 ──
    if tech_req_items:
        if tech_units and allow_direct_llm_matching:
            results.update(judge_technical_requirements(tech_req_items, tech_units, ai_matching))
        else:
            for item in tech_req_items:
                results[item["index"]] = {
                    "status": "待补材料",
                    "matchedFactIds": [],
                    "reason": "企业未维护技术产品/参数，待人工补充后复核",
                    "statusSource": "规则兜底",
                }

    # ── 非技术类「满足/不满足」LLM 复核：错判则降级（未配 audit 模型时自动跳过）──
    if allow_direct_llm_matching:
        _llm_audit_non_tech_matches(requirements, results, company_knowledge, ai)

    # ── 第二遍：按原顺序组装 rows ──
    for index, requirement in enumerate(requirements, start=1):
        requirement_text = str(
            requirement.get("rawText")
            or requirement.get("text")
            or requirement.get("requirement")
            or requirement.get("sourceText")
            or ""
        )
        category = coerce_requirement_category(str(requirement.get("category") or "other"), requirement_text)
        if (
            category in {"qualification", "financial", "personnel", "performance", "credit", "region"}
            and subject_type_matches_requirement(requirement_text, company_knowledge.get("organization")) is False
        ):
            category = "document"
        requirement["category"] = category
        route_hints = normalize_route_hints(category, requirement_text, requirement.get("routeHints"))
        requirement["routeHints"] = route_hints
        requirement_class = requirement_class_for_route(category, requirement_text, route_hints)
        kind = CATEGORY_LABELS.get(category, CATEGORY_LABELS["other"])
        requirement_contract = normalize_requirement(
            {**requirement, "requirementId": f"req-{index:03d}"},
            source_stage="tender_document",
            layer=3,
            kind=kind,
            source_file_id=source_file_id,
        )
        match_result = results.get(index) or {
            "status": "待补材料",
            "matchedFactIds": [],
            "reason": "",
            "statusSource": "规则兜底",
        }
        status = str(match_result.get("status") or "待补材料")
        matched_facts = _facts_by_ids(company_knowledge, match_result.get("matchedFactIds", []))
        evidences = [_fact_to_evidence(fact) for fact in matched_facts]
        matched_objects = [
            {"id": _fact_primary_id(fact), "name": _fact_name(fact)}
            for fact in matched_facts
        ]
        reason = str(match_result.get("reason") or "") or {
            "满足": "已找到经过核验且当前有效的直接企业证据。",
            "部分满足": "已找到相关企业对象或证据，但尚不能证明全部条件。",
            "待补材料": "企业台账暂缺对应数据或证据未核验，需补充/核验材料后复核。",
            "不满足": "现有直接证据显示关键条件已失效或明确不符合。",
        }.get(status, "该要求需要结合上下文人工确认。")
        row = build_comparison(
            requirement_contract,
            status=status,
            reason=reason,
            matched_objects=matched_objects,
            evidences=evidences,
            risk=_risk_for_status(status),
            suggestion=_suggestion_for_status(status, category, requirement_class),
            status_source=str(match_result.get("statusSource") or "模型/规则"),
        )
        row["evidence"] = _evidence_summary(row["evidenceItems"])
        if match_result.get("technicalAlignment"):
            row["technicalAlignment"] = match_result["technicalAlignment"]
        if match_result.get("gaps"):
            row["gaps"] = match_result["gaps"]
            if category == "technical":
                row["suggestion"] = "建议补充或核验：" + "、".join(match_result["gaps"][:5])
        rows.append(row)

    return summarize_fine_review(
        rows,
        {
            "method": extraction["extractionMethod"],
            "provider": extraction.get("extractionProvider") or (extraction.get("stats") or {}).get("extractionProvider", ""),
            "modelUsed": extraction["modelUsed"],
            "warning": extraction["extractionWarning"],
            "warnings": extraction.get("warnings", []),
            "scope": extraction.get("scope", {}),
            "packPath": extraction.get("packPath") or (extraction.get("stats") or {}).get("packPath", ""),
            "analysisPack": extraction.get("analysisPack")
            if isinstance(extraction.get("analysisPack"), dict)
            else None,
            "requirementCount": len(requirements),
            "timings": {
                "extraction": round(t_extracted - t_start, 2),
                "matching": round(time.perf_counter() - t_extracted, 2),
            },
            # 提取层在 document_fine_matching 已算好覆盖率/失败批次等统计，
            # 此前未透出，导致"提取质量"不可观测、无法建立基线。此处原样带出。
            "stats": extraction.get("stats", {}),
        },
    )
