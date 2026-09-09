from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib import error, request


ALLOWED_CATEGORIES = {
    "qualification",
    "performance",
    "personnel",
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
    "region": "地域",
    "credit": "信用",
    "rejection": "否决条款",
    "technical": "产品与能力",
    "other": "其他要求",
}


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


def _is_gateway_timeout(result: dict[str, Any]) -> bool:
    return not result.get("success") and "HTTP 504" in str(result.get("error", ""))


def _ai_settings(ai: dict[str, Any]) -> tuple[str, str, str]:
    base_url = str(ai.get("baseUrl") or os.getenv("AI_BASE_URL", "")).strip()
    api_key = str(ai.get("apiKey") or os.getenv("AI_API_KEY", "")).strip()
    model = str(ai.get("model") or os.getenv("AI_MODEL", "")).strip()
    return base_url, api_key, model


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
        timeout_seconds = int(ai.get("timeoutSeconds", 90))
    except (TypeError, ValueError):
        timeout_seconds = 90
    timeout_seconds = min(max(timeout_seconds, 15), 180)
    req = request.Request(
        _chat_endpoint(base_url),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:
            raw_body = response.read()
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
    except error.HTTPError as exc:
        return {"success": False, "error": f"模型服务返回 HTTP {exc.code}"}
    except error.URLError as exc:
        reason = str(getattr(exc, "reason", "")).strip()
        detail = f"：{reason}" if reason else ""
        return {"success": False, "error": f"无法连接模型服务，请检查 Base URL 和网络{detail}"}
    except TimeoutError:
        return {"success": False, "error": f"模型服务响应超时（{timeout_seconds} 秒）"}
    except OSError:
        return {"success": False, "error": "模型服务连接中断或响应读取失败，请稍后重试"}


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
    if any(term in value for term in ("项目经理", "技术负责人", "人员", "团队", "工程师", "驻场")):
        return "personnel"
    if any(term in value for term in ("类似项目", "类似业绩", "项目业绩", "合同业绩", "案例")):
        return "performance"
    if any(term in value for term in ("服务地点", "注册地", "属地", "地域", "本地")):
        return "region"
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


def _extract_project_fields(text: str) -> dict[str, str]:
    def read(pattern: str) -> str:
        result = re.search(pattern, text)
        return result.group(1).strip()[:180] if result else ""

    return {
        "projectName": read(r"项目名称\s*[:：]\s*([^\r\n]+)"),
        "projectCode": read(r"(?:项目编号|采购编号|招标编号)\s*[:：]\s*([^\r\n]+)"),
        "serviceType": read(r"(?:服务类型|项目类型|采购内容)\s*[:：]\s*([^\r\n]+)"),
        "serviceLocation": read(r"(?:服务地点|实施地点|交付地点)\s*[:：]\s*([^\r\n]+)"),
        "bidDeadline": read(r"(?:投标截止时间|响应文件提交截止时间|递交响应文件截止时间)\s*[:：]\s*([^\r\n]+)"),
    }


def extract_requirements_by_rule(text: str) -> dict[str, Any]:
    lines = [_safe_text(line, 1000) for line in text.splitlines()]
    lines = [line for line in lines if len(line) >= 4]
    requirements: list[dict[str, Any]] = []
    section_category = ""
    trigger_terms = (
        "资格",
        "具有",
        "资质",
        "认证",
        "证书",
        "许可证",
        "业绩",
        "类似",
        "项目经理",
        "人员",
        "团队",
        "信用",
        "失信",
        "服务地点",
        "注册地",
        "否决",
        "无效投标",
        "不得",
        "技术要求",
        "服务要求",
    )

    for line in lines:
        inferred = _category_for(line)
        if inferred != "other" and len(line) <= 60 and any(word in line for word in ("要求", "资格", "条件", "审查")):
            section_category = inferred
            continue
        is_requirement = any(term in line for term in trigger_terms) or bool(
            re.match(r"^(?:\d+[.、．]|[（(]\d+[)）]|[一二三四五六七八九十]+[、.])", line)
        )
        if not is_requirement:
            continue
        category = inferred if inferred != "other" else (section_category or "other")
        requirements.append(
            {
                "id": f"rule-{len(requirements) + 1:03d}",
                "category": category,
                "text": line,
                "mandatory": True,
                "isRejection": category == "rejection",
                "quantity": _required_count(line),
                "timeLimit": "",
                "confidence": 0.62,
            }
        )
        if len(requirements) >= 24:
            break

    return {
        "project": _extract_project_fields(text),
        "requirements": requirements,
        "warnings": [] if requirements else ["规则未识别到明确资格条款，请人工核对公告原文。"],
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
        if not text:
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
                "mandatory": bool(raw.get("mandatory", True)),
                "isRejection": bool(raw.get("isRejection", category == "rejection")),
                "quantity": quantity,
                "timeLimit": _safe_text(raw.get("timeLimit"), 120),
                "confidence": confidence,
            }
        )

    if not requirements:
        return None
    project = value.get("project") if isinstance(value.get("project"), dict) else {}
    return {
        "project": {
            key: _safe_text(project.get(key), 180)
            for key in ("projectName", "projectCode", "serviceType", "serviceLocation", "bidDeadline")
        },
        "requirements": requirements,
        "warnings": [
            _safe_text(item, 240)
            for item in value.get("warnings", [])
            if _safe_text(item, 240)
        ][:8],
    }


def extract_announcement_requirements(text: str, ai: dict[str, Any]) -> dict[str, Any]:
    rule_result = extract_requirements_by_rule(text)
    if not all(_ai_settings(ai)):
        return {
            **rule_result,
            "extractionMethod": "rule",
            "modelUsed": "",
            "extractionWarning": "未配置大模型，已使用规则抽取。",
        }

    prompt = (
        "你是招投标公告结构化抽取助手。只提取原文明确写出的要求，不得推断企业是否满足。"
        "输出严格 JSON，不使用 Markdown。JSON 格式为："
        '{"project":{"projectName":"","projectCode":"","serviceType":"","serviceLocation":"","bidDeadline":""},'
        '"requirements":[{"id":"req-001","category":"qualification|performance|personnel|region|credit|rejection|technical|other",'
        '"text":"原始要求","mandatory":true,"isRejection":false,"quantity":null,"timeLimit":"","confidence":0.0}],"warnings":[]}'
        "。每条 requirement 只保留一个可核验要求。"
    )
    model_text = _trim_model_input(text, 5000)
    result = call_openai_compatible(
        ai,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"公告原文如下：\n{model_text}"},
        ],
        max_tokens=700,
    )
    if _is_gateway_timeout(result):
        result = call_openai_compatible(
            ai,
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"公告原文如下（已压缩，仅提取最关键的可核验要求）：\n{_trim_model_input(text, 2200)}",
                },
            ],
            max_tokens=350,
        )
    if result["success"]:
        normalized = _normalize_model_extraction(_json_from_model(result["content"]) or {})
        if normalized:
            return {
                **normalized,
                "extractionMethod": "llm",
                "modelUsed": result["model"],
                "extractionWarning": "",
            }
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
                    "sourceFiles": source_files,
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
        if category == "qualification":
            return "不满足"
        if category in {"personnel", "performance"}:
            return "待补材料"
        return "待确认"
    state_text = " ".join(
        [str(item.get("status", "")) for item in matched]
        + [str(item.get("status", "")) for item in evidences]
    )
    if any(word in state_text for word in ("待核验", "待补", "历史", "不能替代")):
        return "待确认"
    return "部分满足"


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
        "待确认": 48,
        "待补材料": 22,
        "不满足": 0,
    }.get(status, 35)


def evaluate_announcement_eligibility(
    text: str,
    company_knowledge: dict[str, Any],
    company_profile: dict[str, Any],
    ai: dict[str, Any],
) -> dict[str, Any]:
    extraction = extract_announcement_requirements(text, ai)
    rows: list[dict[str, Any]] = []
    hard_blockers: list[dict[str, Any]] = []
    weights = {
        "qualification": 24,
        "performance": 22,
        "personnel": 16,
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
        requirement_text = _safe_text(requirement.get("text"))
        matched = _candidate_records(category, requirement_text, company_knowledge)
        evidences = _evidence_for_records(matched, company_knowledge)
        status = _status_from_evidence(category, matched, evidences)

        if category == "region":
            regions = _list_of_dicts(company_profile.get("service_regions"))
            matching_regions = _matching_records(requirement_text, regions)
            if matching_regions:
                status = "待确认"
                evidences = [
                    {
                        "id": str(item.get("name", "")),
                        "name": f"服务区域：{item.get('name', '')}",
                        "status": str(item.get("status", "待核验")),
                        "level": "",
                        "sourceFiles": [],
                    }
                    for item in matching_regions
                ]
            else:
                status = "待确认"
                evidences = []
        elif category in {"credit", "rejection"}:
            status = "待确认"
            evidences = []

        risk = "低" if status == "满足" else "中" if status in {"部分满足", "待确认"} else "高"
        suggestion = {
            "满足": "仍需在递交前核验原件、有效期和招标文件附件。",
            "部分满足": "已找到关联本体对象和资料来源，需核验原件、有效期或项目适用性。",
            "待确认": "当前资料不能形成确定结论，请人工核验原始公告和对应证明材料。",
            "待补材料": "资料库未形成可直接核验的材料，请补充或核验后再判断。",
            "不满足": "当前本体资料未找到对应硬性资质，请确认是否确实缺失或尚未录入。",
        }[status]
        row = {
            "id": requirement.get("id", ""),
            "kind": label,
            "category": category,
            "requirement": requirement_text,
            "sourceText": requirement_text,
            "mandatory": bool(requirement.get("mandatory", True)),
            "isRejection": bool(requirement.get("isRejection", False)),
            "status": status,
            "evidence": _evidence_summary(evidences),
            "evidenceItems": evidences,
            "matchedObjectIds": [str(item.get("id", "")) for item in matched],
            "risk": risk,
            "suggestion": suggestion,
            "confidence": requirement.get("confidence", 0),
        }
        rows.append(row)

        weight = weights.get(category, weights["other"])
        total_weight += weight
        weighted_score += weight * _score_for_status(status)
        if (
            row["mandatory"]
            and status == "不满足"
            and category in {"qualification", "performance", "personnel"}
        ):
            hard_blockers.append(
                {
                    "requirementId": row["id"],
                    "title": f"{label}未满足",
                    "detail": requirement_text,
                }
            )

    score = round(weighted_score / total_weight) if total_weight else 0
    if hard_blockers:
        decision = "不建议参与"
        eligibility_status = "不满足"
        should_get_tender_document = False
    elif score >= 70:
        decision = "建议获取招标文件后复核"
        eligibility_status = "部分满足" if any(row["status"] != "满足" for row in rows) else "满足"
        should_get_tender_document = True
    elif score >= 45:
        decision = "人工重点评估"
        eligibility_status = "待确认"
        should_get_tender_document = True
    else:
        decision = "不建议参与"
        eligibility_status = "待确认"
        should_get_tender_document = False

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

    return {
        "rows": rows,
        "risks": risks,
        "score": score,
        "decision": decision,
        "project": extraction["project"],
        "extraction": {
            "method": extraction["extractionMethod"],
            "modelUsed": extraction["modelUsed"],
            "warning": extraction["extractionWarning"],
            "warnings": extraction.get("warnings", []),
        },
        "eligibility": {
            "eligibilityScore": score,
            "eligibilityStatus": eligibility_status,
            "hardBlockers": hard_blockers,
            "shouldGetTenderDocument": should_get_tender_document,
            "recommendation": decision,
        },
    }
