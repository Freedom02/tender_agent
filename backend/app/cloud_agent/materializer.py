from __future__ import annotations

from copy import deepcopy
import hashlib
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any

from ..bid_writing.schemas import (
    SECTION_KIND_ATTACHMENT,
    SECTION_KIND_COMMERCIAL,
    SECTION_KIND_DEVIATION,
    SECTION_KIND_TECHNICAL,
    SECTION_SOURCE_AI,
    SECTION_SOURCE_TABLE,
    SECTION_SOURCE_TEMPLATE,
    build_bid_document,
    build_section,
)
from ..tender_matching.contracts import CATEGORY_MODULES, EVIDENCE_NEEDS, RESPONSE_TYPES
from ..tender_matching.document_matching import summarize_fine_review


_SECTION_RULES: tuple[tuple[tuple[str, ...], str, str, str], ...] = (
    (("技术偏离", "技术规格偏离", "技术条款偏离"), "d-01", SECTION_SOURCE_TABLE, SECTION_KIND_DEVIATION),
    (("商务偏离", "商务条款偏离", "偏离表"), "d-02", SECTION_SOURCE_TABLE, SECTION_KIND_DEVIATION),
    (("开标一览", "报价明细", "分项报价", "报价表", "报价单", "报价"), "p-01", SECTION_SOURCE_TEMPLATE, SECTION_KIND_COMMERCIAL),
    (("项目理解", "总体理解", "需求理解", "方案概述"), "t-01", SECTION_SOURCE_AI, SECTION_KIND_TECHNICAL),
    (("技术方案", "技术响应", "技术规格响应", "技术参数响应", "技术需求响应"), "t-02", SECTION_SOURCE_AI, SECTION_KIND_TECHNICAL),
    (("实施方案", "服务方案", "组织实施方案", "项目实施方案", "实施计划", "进度计划"), "t-03", SECTION_SOURCE_AI, SECTION_KIND_TECHNICAL),
    (("质量保证", "质量保障", "质保", "质量管理", "质量控制"), "t-04", SECTION_SOURCE_AI, SECTION_KIND_TECHNICAL),
    (("售后", "服务承诺", "售后服务", "维护服务", "运维"), "t-05", SECTION_SOURCE_AI, SECTION_KIND_TECHNICAL),
    (("培训",), "t-06", SECTION_SOURCE_AI, SECTION_KIND_TECHNICAL),
    (("应急", "预案", "保障方案"), "t-07", SECTION_SOURCE_AI, SECTION_KIND_TECHNICAL),
    (("投标函", "响应函", "投标书", "响应书"), "c-01", SECTION_SOURCE_TEMPLATE, SECTION_KIND_COMMERCIAL),
    (("承诺书", "承诺函", "承诺"), "c-02", SECTION_SOURCE_TEMPLATE, SECTION_KIND_COMMERCIAL),
    (("法定代表人身份证明", "法人身份证明", "法定代表人资格证明"), "c-03", SECTION_SOURCE_TEMPLATE, SECTION_KIND_COMMERCIAL),
    (("授权委托书", "授权书", "委托书"), "c-04", SECTION_SOURCE_TEMPLATE, SECTION_KIND_COMMERCIAL),
    (("保证金",), "c-05", SECTION_SOURCE_TEMPLATE, SECTION_KIND_COMMERCIAL),
    (("声明",), "c-06", SECTION_SOURCE_TEMPLATE, SECTION_KIND_COMMERCIAL),
    (("企业简介", "公司简介", "基本情况", "投标人情况"), "c-07", SECTION_SOURCE_TEMPLATE, SECTION_KIND_COMMERCIAL),
    (("资格", "资质", "财务", "审计", "纳税", "社保", "信用"), "c-08", SECTION_SOURCE_TABLE, SECTION_KIND_COMMERCIAL),
    (("业绩", "类似项目", "案例", "中标"), "c-09", SECTION_SOURCE_TABLE, SECTION_KIND_COMMERCIAL),
    (("人员", "团队", "负责人", "项目组", "拟投入"), "c-10", SECTION_SOURCE_TABLE, SECTION_KIND_COMMERCIAL),
)

_CATEGORY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("资格", "资质", "营业执照", "许可证", "认证"), "qualification"),
    (("业绩", "案例", "合同", "类似项目"), "performance"),
    (("人员", "项目经理", "负责人", "团队"), "personnel"),
    (("财务", "审计", "纳税"), "financial"),
    (("区域", "服务地点", "本地化"), "region"),
    (("信用", "失信"), "credit"),
    (("技术", "功能", "参数", "性能"), "technical"),
    (("实施", "进度", "交付", "服务方案"), "service_plan"),
    (("报价", "价格", "预算", "最高限价"), "pricing"),
    (("合同", "付款", "履约"), "contract"),
    (("废标", "无效", "否决", "实质性"), "rejection"),
    (("文件", "格式", "签字", "盖章", "密封", "装订"), "document"),
    (("商务", "承诺", "售后"), "commercial"),
)

_CATEGORY_TO_SECTION = {
    "technical": "t-02",
    "service_plan": "t-03",
    "commercial": "t-05",
}

_STATUS_MAP = {
    "满足": "满足",
    "通过": "满足",
    "部分满足": "部分满足",
    "不满足": "不满足",
    "待补材料": "待补材料",
    "待补充": "待补材料",
    "待澄清": "待澄清",
    "待核验": "待澄清",
    "待确认": "待澄清",
    "不适用": "不适用",
}


def _text(value: Any, limit: int = 800) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or ""))


def _meta(result: dict[str, Any]) -> dict[str, Any]:
    value = result.get("projectMeta")
    return value if isinstance(value, dict) else {}


def _normalize_category(value: Any, requirement: str = "") -> str:
    raw = _text(value, 60).lower()
    known = set(EVIDENCE_NEEDS)
    if raw in known:
        return raw
    source = f"{value}{requirement}"
    for keywords, category in _CATEGORY_RULES:
        if any(keyword in source for keyword in keywords):
            return category
    return "other"


def _normalize_status(value: Any, mandatory: bool) -> str:
    raw = _text(value, 40)
    if raw in _STATUS_MAP:
        return _STATUS_MAP[raw]
    if any(term in raw for term in ("不满足", "不符合", "缺失")):
        return "不满足"
    if any(term in raw for term in ("待补", "补充")):
        return "待补材料"
    if any(term in raw for term in ("满足", "符合", "通过")):
        return "满足"
    return "待澄清" if mandatory else "待澄清"


def _classify_section(title: str, raw: dict[str, Any]) -> tuple[str, str, str]:
    value = _norm(title)
    for keywords, section_id, source, kind in _SECTION_RULES:
        if any(_norm(keyword) in value for keyword in keywords):
            return section_id, source, kind
    raw_kind = _text(raw.get("kind"), 40)
    if raw_kind in {SECTION_KIND_COMMERCIAL, SECTION_KIND_TECHNICAL, SECTION_KIND_DEVIATION, SECTION_KIND_ATTACHMENT}:
        kind = raw_kind
    else:
        kind = SECTION_KIND_TECHNICAL
    raw_source = _text(raw.get("source"), 40)
    if raw_source in {SECTION_SOURCE_AI, SECTION_SOURCE_TABLE, SECTION_SOURCE_TEMPLATE, "copied_template", "attachment"}:
        source = raw_source
    else:
        source = SECTION_SOURCE_AI if kind == SECTION_KIND_TECHNICAL else SECTION_SOURCE_TEMPLATE
    return "", source, kind


def _stable_custom_id(index: int) -> str:
    return f"custom-{index:02d}"


def _analysis_text(result: dict[str, Any], filename: str) -> str:
    meta = _meta(result)
    lines = [
        f"项目名称：{_text(meta.get('projectName') or Path(filename).stem or '未识别')}",
        f"项目编号：{_text(meta.get('projectCode'))}",
        f"招标人/采购人：{_text(meta.get('tendererName'))}",
        f"代理机构：{_text(meta.get('agencyName'))}",
        f"预算/最高限价：{_text(meta.get('budget') or meta.get('budgetAmount'))}",
        f"投标截止时间：{_text(meta.get('deadline') or meta.get('bidDeadline'))}",
        "",
        "一、云端智能体生成目录",
    ]
    for index, item in enumerate(result.get("outline") or [], start=1):
        if isinstance(item, dict):
            lines.append(f"{index}. {_text(item.get('title'))}")
    lines.append("")
    lines.append("二、关键要求")
    for index, item in enumerate(result.get("requirements") or [], start=1):
        if isinstance(item, dict):
            mandatory = "强制" if item.get("mandatory") else "一般"
            locator = _text(item.get("sourceLocator"), 120)
            lines.append(f"{index}. [{mandatory}] {_text(item.get('requirement'))} {locator}".strip())
    warnings = [_text(item, 200) for item in (result.get("warnings") or []) if _text(item, 200)]
    if warnings:
        lines.append("")
        lines.append("三、解析提示")
        lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines).strip()


def build_fine_review(
    result: dict[str, Any],
    *,
    source_file_id: str = "",
    provider: str = "dify",
) -> dict[str, Any]:
    provider_name = str(provider or "unknown").strip().lower() or "unknown"
    provider_label = {
        "pi": "Pi 智能体",
        "dify": "Dify 云端智能体",
        "workbuddy": "WorkBuddy 智能体",
        "local": "本地解析",
    }.get(provider_name, provider_name)
    rows: list[dict[str, Any]] = []
    for index, item in enumerate(result.get("requirements") or [], start=1):
        if not isinstance(item, dict):
            continue
        requirement = _text(item.get("requirement"), 1200)
        if not requirement:
            continue
        requirement_id = _text(item.get("id"), 80) or f"cloud-req-{index:03d}"
        category = _normalize_category(item.get("category"), requirement)
        mandatory = bool(item.get("mandatory"))
        is_rejection = bool(item.get("isRejection")) or category == "rejection" or any(
            term in requirement for term in ("废标", "无效投标", "否决", "实质性")
        )
        status = _normalize_status(item.get("status"), mandatory)
        row = {
            "id": requirement_id,
            "comparisonId": f"cmp-{requirement_id}",
            "requirementId": requirement_id,
            "layer": 2,
            "sourceStage": "tender_document",
            "kind": _text(item.get("kind"), 80) or CATEGORY_MODULES.get(category, "投标要求"),
            "category": category,
            "module": CATEGORY_MODULES.get(category, "商务和服务要求"),
            "section": _text(item.get("section"), 160),
            "rawText": _text(item.get("rawText") or requirement, 1600),
            "normalizedRequirement": requirement,
            "importance": "critical" if mandatory or is_rejection else "normal",
            "evidenceNeed": EVIDENCE_NEEDS.get(category, EVIDENCE_NEEDS["other"]),
            "responseType": RESPONSE_TYPES.get(category, RESPONSE_TYPES["other"]),
            "sourceFileId": source_file_id,
            "sourceLocator": _text(item.get("sourceLocator"), 240),
            "requirement": requirement,
            "sourceText": _text(item.get("sourceText") or requirement, 1600),
            "mandatory": mandatory,
            "isRejection": is_rejection,
            "status": status,
            "evidence": _text(item.get("evidence"), 1200),
            "evidenceItems": [],
            "matchedObjectIds": [],
            "matchedMaterialIds": [],
            "evidenceIds": [],
            "evidenceCoverageStatus": "待核验证据" if mandatory else "未要求证据",
            "risk": _text(item.get("risk"), 20) or ("高" if is_rejection or mandatory else "中"),
            "riskLevel": _text(item.get("riskLevel"), 20) or ("高" if is_rejection or mandatory else "中"),
            "reason": _text(item.get("reason"), 1200) or f"由{provider_label}从招标文件解析生成，需人工复核原文。",
            "suggestion": _text(item.get("suggestion"), 1200) or "核对原文位置并补齐响应材料或正文说明。",
            "confidence": item.get("confidence") if isinstance(item.get("confidence"), (int, float)) else None,
            "statusSource": provider_label,
        }
        rows.append(row)

    extraction = {
        "method": "cloud_agent",
        "provider": provider_name,
        "modelUsed": "",
        "warning": "；".join(str(item) for item in (result.get("warnings") or [])[:3]),
        "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
    }
    return summarize_fine_review(rows, extraction)


def build_cloud_bid_document(
    result: dict[str, Any],
    workspace_id: str,
    project_id: str,
    *,
    provider: str = "dify",
) -> dict[str, Any]:
    fine_review = build_fine_review(result, provider=provider)
    rows = fine_review.get("rows") if isinstance(fine_review.get("rows"), list) else []
    by_category: dict[str, list[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        section_id = _CATEGORY_TO_SECTION.get(str(row.get("category") or ""))
        requirement_id = str(row.get("requirementId") or row.get("id") or "")
        if section_id and requirement_id:
            by_category.setdefault(section_id, []).append(requirement_id)

    seen: set[str] = set()
    sections: list[dict[str, Any]] = []
    custom_index = 1
    for item in result.get("outline") or []:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"), 200)
        if not title:
            continue
        classified_id, source, kind = _classify_section(title, item)
        raw_id = _text(item.get("sectionId"), 80)
        if classified_id and classified_id not in seen:
            section_id = classified_id
        elif raw_id and re.match(r"^(?:c|t|d|p|a|custom)-[\w-]+$", raw_id) and raw_id not in seen:
            section_id = raw_id
        else:
            section_id = _stable_custom_id(custom_index)
            custom_index += 1
            while section_id in seen:
                section_id = _stable_custom_id(custom_index)
                custom_index += 1
        seen.add(section_id)

        requirement_ids = [
            _text(value, 80)
            for value in (item.get("requirementIds") or [])
            if _text(value, 80)
        ]
        if not requirement_ids:
            requirement_ids = by_category.get(section_id, [])
        raw_parent_id = _text(item.get("parentSectionId") or item.get("parentId"), 80)
        raw_level = item.get("level")
        try:
            level = max(int(raw_level), 1)
        except (TypeError, ValueError):
            level = 1
        raw_order = item.get("order")
        try:
            order = max(int(raw_order), 1)
        except (TypeError, ValueError):
            order = len(sections) + 1
        sections.append(
            build_section(
                section_id,
                title,
                source,
                kind=kind,
                requirement_ids=requirement_ids,
                parent_section_id=raw_parent_id,
                level=level,
                order=order,
                required=bool(item.get("required", True)),
                response_type=_text(item.get("responseType"), 60),
            )
        )

    for section_id, title in (("d-01", "技术规格偏离表"), ("d-02", "商务偏离表")):
        if section_id not in seen:
            sections.append(build_section(section_id, title, SECTION_SOURCE_TABLE, kind=SECTION_KIND_DEVIATION))

    bid_doc = build_bid_document(workspace_id, project_id)
    provider_name = str(provider or "unknown").strip().lower() or "unknown"
    parse_source_detail = (
        "Pi 本地智能体解析结果"
        if provider_name == "pi"
        else "Dify Cloud Workflow 解析结果"
    )
    bid_doc["planSource"] = "pi_agent" if provider_name == "pi" else "cloud_agent"
    bid_doc["planSourceDetail"] = parse_source_detail
    bid_doc["planningSource"] = "unplanned"
    bid_doc["planningSourceDetail"] = "解析已完成，尚未调用目录规划智能体"
    bid_doc["planningStatus"] = "pending"
    bid_doc["parseProvider"] = provider_name
    bid_doc["parseSourceDetail"] = parse_source_detail
    bid_doc["plan"] = sections
    return bid_doc


def build_catalog_item(
    *,
    run: dict[str, Any],
    result: dict[str, Any],
    project_id: str,
    workspace_id: str,
    root: Path,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = _meta(result)
    provider_name = str(run.get("provider") or "dify").strip().lower() or "unknown"
    provider_label = {
        "pi": "Pi 本地智能体",
        "dify": "Dify Cloud",
    }.get(provider_name, provider_name)
    filename = _text(run.get("originalFilename"), 240) or "招标文件"
    analysis = _analysis_text(result, filename)
    project_name = _text(meta.get("projectName"), 240) or Path(filename).stem or "云端解析招标文件"
    input_artifact = _text(run.get("inputArtifact"), 400)
    input_path = root / input_artifact if input_artifact else None
    content_hash = ""
    if input_path and input_path.is_file():
        content_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()

    item = dict(existing or {})
    item.update(
        {
            "id": project_id,
            "workspaceId": workspace_id,
            "title": project_name,
            "projectName": project_name,
            "projectCode": _text(meta.get("projectCode"), 120),
            "tendererName": _text(meta.get("tendererName"), 240),
            "agencyName": _text(meta.get("agencyName"), 240),
            "budget": _text(meta.get("budget") or meta.get("budgetAmount"), 120),
            "category": _text(meta.get("serviceType") or meta.get("category"), 120) or "未识别",
            "region": _text(meta.get("region") or meta.get("serviceLocation"), 120),
            "noticeType": f"{provider_label}解析招标文件",
            "source": provider_label,
            "publishedDate": str(existing.get("publishedDate") if existing else "") or date.today().isoformat(),
            "bidDeadline": _text(meta.get("deadline") or meta.get("bidDeadline"), 120),
            "deadline": _text(meta.get("deadline") or meta.get("bidDeadline"), 120),
            "dataOrigin": "cloud_agent",
            "analysisText": analysis,
            "parsed": {
                "projectName": project_name,
                "projectCode": _text(meta.get("projectCode"), 120),
                "tendererName": _text(meta.get("tendererName"), 240),
                "budgetAmount": _text(meta.get("budget") or meta.get("budgetAmount"), 120),
                "bidDeadline": _text(meta.get("deadline") or meta.get("bidDeadline"), 120),
                "requirements": result.get("requirements") or [],
                "source": provider_label,
            },
            "document": {
                "id": f"cloud-file-{project_id}",
                "kind": "tender_document",
                "name": filename,
                "mimeType": _text(run.get("mimeType"), 120) or "application/octet-stream",
                "contentHash": content_hash,
                "parseMethod": f"{provider_name}_agent_workflow",
                "warnings": result.get("warnings") if isinstance(result.get("warnings"), list) else [],
                "segments": [],
                "units": [],
                "tables": [],
                "metadata": {"runId": run.get("runId"), "outputArtifact": run.get("outputArtifact")},
                "sections": [],
                "structureWarnings": [],
            },
            "downloadStatus": "已下载",
            "downloadUrl": f"/{input_artifact}" if input_artifact.startswith("runtime/") else "",
            "status": "已分析",
        }
    )
    return item


def _now_iso() -> str:
    """本地时区 ISO 时间戳（与 store.now_iso 同格式，带时区偏移）。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_workspace(
    *,
    run: dict[str, Any],
    result: dict[str, Any],
    project: dict[str, Any],
    workspace_id: str,
    project_id: str,
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    provider_name = str(run.get("provider") or "dify").strip().lower() or "unknown"
    fine_review = build_fine_review(
        result,
        source_file_id=str(project.get("document", {}).get("id") or ""),
        provider=provider_name,
    )
    risk_items = [
        item for item in (result.get("riskItems") or [])
        if isinstance(item, dict)
    ]
    reasoning = {
        "rows": [],
        "risks": fine_review.get("risks", []),
        "score": fine_review.get("score", 0),
        "decision": fine_review.get("decision", "人工重点评估"),
        "summary": fine_review.get("summary", {}),
        "project": {
            "projectName": str(project.get("projectName") or project.get("title") or ""),
            "projectCode": str(project.get("projectCode") or ""),
            "tendererName": str(project.get("tendererName") or ""),
            "budget": str(project.get("budget") or ""),
            "bidDeadline": str(project.get("bidDeadline") or ""),
        },
        "procurementNeeds": [],
        "fineReview": fine_review,
        "cloudParse": result,
        "cloudParseProvider": provider_name,
        "cloudRunId": run.get("runId"),
        "cloudRisks": risk_items,
    }
    bid_doc = build_cloud_bid_document(
        result,
        workspace_id,
        project_id,
        provider=provider_name,
    )
    previous_bid_doc = previous.get("bidDocument") if isinstance(previous, dict) else None
    if (
        isinstance(previous_bid_doc, dict)
        and previous_bid_doc.get("planSource") in {"cloud_agent", "pi_agent"}
        and isinstance(previous_bid_doc.get("plan"), list)
        and previous_bid_doc.get("plan")
    ):
        # 重复物化时保留人工编辑过的目录和正文，只刷新由解析结果派生的匹配数据。
        bid_doc = deepcopy(previous_bid_doc)
    workspace = {
        "id": workspace_id,
        "workspaceId": workspace_id,
        "projectId": project_id,
        "title": project.get("title", ""),
        "project": project,
        "reasoning": reasoning,
        "draft": previous.get("draft") if previous and previous.get("draft") else _default_draft(project, fine_review),
        "draftVersion": int(previous.get("draftVersion", 1) or 1) if previous else 1,
        "bidDocument": bid_doc,
    }
    if previous and previous.get("auditRecords"):
        workspace["auditRecords"] = previous["auditRecords"]
    # 创建时间盖章规则（存储层据此填 workspaces.created_at）：
    #   首次创建 -> 记当前时间；
    #   重复物化 -> 沿用原记录的值；
    #   接入前的老数据（previous 里没有）-> 一律不写，交给存储层保留库里已有的值，
    #   而不是伪造一个「现在」。
    if isinstance(previous, dict):
        if previous.get("createdAt"):
            workspace["createdAt"] = previous["createdAt"]
    else:
        workspace["createdAt"] = _now_iso()
    return workspace


def _default_draft(project: dict[str, Any], fine_review: dict[str, Any]) -> str:
    rows = fine_review.get("rows") if isinstance(fine_review.get("rows"), list) else []
    pending = [
        f"- {row.get('requirement')}（{row.get('status')}；来源：{row.get('sourceLocator') or '待定位'}）"
        for row in rows
        if isinstance(row, dict) and row.get("status") not in {"满足", "不适用"}
    ][:30]
    return (
        f"# {project.get('title', '投标项目')}\n\n"
        "## 一、项目理解\n"
        "本底稿由云端智能体解析招标文件后生成，后续章节将按确认后的目录逐章撰写。\n\n"
        "## 二、关键待核验要求\n"
        f"{chr(10).join(pending) if pending else '- 暂无待核验要求，仍需人工复核原始招标文件。'}\n\n"
        "> 当前内容是可审核底稿，不代表可以直接递交。\n"
    )
