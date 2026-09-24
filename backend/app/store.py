from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from .config.service import ensure_config, public_configuration
from .tender_matching.eligibility import (
    call_openai_compatible,
    evaluate_announcement_eligibility,
    test_ai_connection,
)
from .tender_matching.document_matching import (
    evaluate_tender_document,
    summarize_fine_review,
    update_fine_review_status,
)
from .tender_matching.document_parser import (
    parse_doc,
    parse_docx,
    parse_document_ir,
    parse_pdf,
    parse_plain_text,
    parse_xlsx,
)
from .tender_matching.document_structure import enrich_document_structure
from .tender_matching.decision_summary import build_workspace_decision_summary
from .tender_matching.service import evaluate_company_relevance, matches_manual_search
from .workbuddy_agent.workflow.integration import (
    WorkBuddyAgentMatchingError,
    build_review_output_from_analysis_pack,
    evaluate_tender_document_with_workbuddy_agent,
)
from .tender_monitor.adapters import (
    is_procurement_notice,
    normalize_notice_text,
    repair_notice_fields,
    valid_time_value,
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, value: Any, *, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) if compact
        else json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# M3 画像刷新缓存时长（秒）：避免每次 bootstrap 都打 8 条 MQL 拖慢首屏。
M3_REFRESH_TTL_SECONDS = 60.0


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.runtime_root = root / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.runtime_root / "catalog.json"
        self.libraries_path = self.runtime_root / "libraries.json"
        self.config_path = root / "agent-config.json"
        self.workspaces_path = self.runtime_root / "workspaces.json"
        self.catalog: list[dict[str, Any]] = []
        self.libraries: dict[str, Any] = {}
        self.config: dict[str, Any] = {}
        self.company: dict[str, Any] = {}
        # 企业组织原始事实（含法定代表人/统一社会信用代码/注册地址，供商务文书填充）。
        self.organization: dict[str, Any] = {}
        # 历史遗留：招标匹配/监控的企业数据源（旧 company-knowledge.json 已删，现为空壳）。
        self.company_knowledge: dict[str, Any] = {}
        # M3 画像刷新缓存：上次刷新时间戳。
        self._m3_last_refresh = 0.0
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.reminders: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []

    def initialize(self) -> None:
        # 企业数据单一来源：M3 图库（本地不再落 company-materials.json，只保留证明材料文件）。
        self.catalog = read_json(self.catalog_path, [])
        self.config = ensure_config(read_json(self.config_path, self.default_config()))
        self.libraries = read_json(self.libraries_path, self.default_libraries())
        self.reminders = read_json(self.runtime_root / "reminders.json", [])
        self.runs = read_json(self.runtime_root / "automation-runs.json", [])
        self.workspaces = read_json(self.workspaces_path, {})
        # 兼容旧版本工作区：补齐 fineReview 缺失字段，避免前端渲染崩溃。
        for workspace in self.workspaces.values():
            reasoning = workspace.get("reasoning") if isinstance(workspace, dict) else None
            if not isinstance(reasoning, dict):
                continue
            fine_review = reasoning.get("fineReview")
            if isinstance(fine_review, dict):
                fine_review.setdefault("hardBlockers", [])
                fine_review.setdefault("rejectionRisks", [])
                fine_review.setdefault("unresolvedMandatory", [])
        self._refresh_company_from_m3()
        catalog_repaired = False
        for item in self.catalog:
            catalog_repaired = repair_notice_fields(item) or catalog_repaired
        if catalog_repaired:
            self.persist_catalog()
        write_json(self.config_path, self.config)

    @staticmethod
    def default_config() -> dict[str, Any]:
        return {
            "profileName": "默认检索画像",
            "enabled": True,
            "targetServiceTypes": [],
            "targetRegions": [],
            "includeKeywords": [],
            "excludeKeywords": [],
            "scoreWeights": {
                "qualification": 14,
                "performance": 22,
                "personnel": 14,
                "region": 12,
                "serviceType": 18,
                "time": 10,
                "documentReadiness": 10,
            },
            "decisionThresholds": {"recommend": 78, "review": 58},
            "ai": {"enabled": True, "provider": "auto", "model": "qwen2.5:7b"},
            "automation": {"enabled": True, "maxProjects": 10},
        }

    @staticmethod
    def default_libraries() -> dict[str, Any]:
        return {
            key: {
                "key": key,
                "name": name,
                "description": description,
                "documents": [],
            }
            for key, name, description in [
                ("company", "公司资料库", "企业资质、业绩、人员与服务能力"),
                ("tenders", "招标项目库", "公开公告、招标文件与已下载项目"),
                ("templates", "响应模板库", "项目理解、服务方案与承诺模板"),
                ("searches", "搜索记录库", "保存实时搜索条件和结果索引"),
                ("outputs", "自动化产出库", "保存匹配工作台和响应文件导出包"),
            ]
        }

    def _company_qualification_names(self) -> list[str]:
        values = self.company.get("qualifications", [])
        names: list[str] = []
        for value in values:
            if isinstance(value, str):
                names.append(value)
            elif isinstance(value, dict):
                name = str(value.get("name") or value.get("qualificationName") or "").strip()
                if name:
                    names.append(name)
        return names

    def _company_service_regions(self) -> list[str]:
        values = self.company.get("service_regions", [])
        regions: list[str] = []
        for value in values:
            if isinstance(value, str):
                regions.append(value)
            elif isinstance(value, dict):
                # A pending nationwide label must not become an automatic pass.
                if value.get("status") in {"待核验", "待补材料", "未核验"}:
                    continue
                name = str(value.get("name") or "").strip()
                if name:
                    regions.append(name)
        return regions

    def _company_service_types(self) -> list[str]:
        values = self.company.get("serviceTypes", [])
        return [str(value).strip() for value in values if str(value).strip()]

    def _seed_demo_records(self) -> None:
        if self.catalog:
            return
        self.catalog = [
            {
                "id": "sample-smartpark-digitaltwin",
                "title": "智慧园区数字孪生平台建设项目（完整招标样本）",
                "category": "数字孪生",
                "region": "福建省福州市",
                "noticeType": "Demo 完整招标文件",
                "source": "本地脱敏示例",
                "publishedDate": "2026-09-01",
                "servicePeriod": "12个月",
                "dataOrigin": "demo",
                "analysisText": (
                    "项目名称：福州市某智慧园区数字孪生平台建设项目\n"
                    "项目编号：FZ-DT-2026-018\n招标人：福州市某产业园区管委会\n"
                    "服务类型：数字孪生\n服务地点：福建省福州市\n预算金额：人民币300万元\n"
                    "服务期限：12个月\n投标截止时间：2026-09-08 09:30\n\n"
                    "一、投标人资格要求\n1. 具有有效营业执照。\n"
                    "2. 具有有效的信息安全管理体系认证（ISO27001）。\n"
                    "3. 近三年承担过不少于3个类似园区数字孪生或可视化平台项目。\n"
                    "4. 拟派项目经理1名，具有3年以上数字孪生平台实施经验。\n\n"
                    "二、服务要求\n建设园区三维可视化底座、物联数据接入和运维管理功能。\n"
                    "提供低代码场景搭建能力，支持园区态势感知和告警联动。\n"
                    "每月提交平台运行、数据接入和质量保障报告。\n\n"
                    "三、投标文件要求\n提交项目理解、技术方案、实施计划、质量保障和应急预案。\n"
                    "未按要求提供资格证明材料的，视为无效投标。"
                ),
            },
            {
                "id": "sample-nonmatch-logistics",
                "title": "医院后勤综合服务项目（不匹配样本）",
                "category": "后勤服务",
                "region": "河南省郑州市",
                "noticeType": "Demo 风险识别样本",
                "source": "本地脱敏示例",
                "publishedDate": "2026-09-01",
                "servicePeriod": "12个月",
                "dataOrigin": "demo",
                "analysisText": "项目名称：某医院后勤综合服务项目\n服务类型：后勤综合服务\n服务地点：河南省郑州市\n投标截止时间：2026-09-20 09:00\n资格要求：近三年承担过不少于2个医院后勤服务项目。",
            },
        ]
        self.persist_catalog()

    def persist_catalog(self) -> None:
        write_json(self.catalog_path, self.catalog, compact=True)

    def persist_libraries(self) -> None:
        write_json(self.libraries_path, self.libraries)

    def persist_runtime(self) -> None:
        write_json(self.runtime_root / "reminders.json", self.reminders[-100:])
        write_json(self.runtime_root / "automation-runs.json", self.runs[-50:])
        write_json(self.workspaces_path, self.workspaces, compact=True)

    @staticmethod
    def current_time() -> str:
        return now_iso()

    @staticmethod
    def new_id(prefix: str) -> str:
        return uid(prefix)

    def with_preview(self, item: dict[str, Any]) -> dict[str, Any]:
        result = self._with_preview(item)
        match = evaluate_company_relevance(
            result,
            self.company_knowledge,
            ensure_config(self.config).get("monitoring", {}),
        )
        result.update(
            {
                "relevanceScore": match["score"],
                "matchedBusinessDirections": match["matchedBusinessDirections"],
                "matchedScenarios": match["matchedScenarios"],
                "matchedProducts": match["matchedProducts"],
                "matchedCapabilities": match["matchedCapabilities"],
                "matchedKeywords": match["matchedKeywords"],
                "matchReasons": match["reasons"],
                "matchEvidence": match["matchEvidence"],
                "scoreBreakdown": match["scoreBreakdown"],
                "scoreVersion": match["scoreVersion"],
                "excludeReason": match["excludeReason"],
                "nextStage": match["nextStage"],
                "shouldAnalyze": match["shouldAnalyze"],
            }
        )
        return result

    def _materials_counts(self) -> dict[str, int]:
        """8 类事实计数（M3 图库，前端首屏徽标用）。"""
        keys = (
            "organization", "qualification_certificate", "performance_case",
            "staff_member", "financial_evidence", "technical_product",
            "technical_parameter", "credential",
        )
        cached = getattr(self, "_m3_counts", None)
        if cached:
            return {k: cached.get(k, 0) for k in keys}
        return {k: 0 for k in keys}

    def _refresh_company_from_m3(self, force: bool = False) -> None:
        """从 M3 拉取企业 facts，刷新 self.company（画像）与 self._m3_counts（计数）。

        best-effort：M3 不可用时保持现有值（首次为空画像/零计数），不抛异常。
        默认按 M3_REFRESH_TTL_SECONDS 做缓存，避免每次 bootstrap 都打 8 条 MQL 拖慢首屏。
        """
        now = time.time()
        if not force and (now - self._m3_last_refresh) < M3_REFRESH_TTL_SECONDS:
            return

        from .document_recognition.storage import FACT_LIST_KEYS, build_company_knowledge, build_eligibility_knowledge, build_profile
        from .m3.sync import build_m3_client, read_facts_from_m3

        try:
            facts = read_facts_from_m3(build_m3_client(self))
            self.company = build_profile(facts)
            self.organization = facts.get("organization") or {}
            self.company_knowledge = {
                **build_company_knowledge(facts),
                **build_eligibility_knowledge(facts),
            }
            counts: dict[str, int] = {"organization": 1 if facts.get("organization") else 0}
            for key in FACT_LIST_KEYS:
                counts[key] = len(facts.get(key, []))
            self._m3_counts = counts
        except Exception:
            if not getattr(self, "_m3_counts", None):
                empty: dict[str, int] = {"organization": 0}
                for key in FACT_LIST_KEYS:
                    empty[key] = 0
                self._m3_counts = empty
        finally:
            self._m3_last_refresh = time.time()

    def bootstrap(self) -> dict[str, Any]:
        from .tender_monitor.service import monitoring_status

        # 每次首屏从 M3 刷新画像与计数（best-effort），保证导入后徽标/匹配数据即时更新。
        self._refresh_company_from_m3()

        return {
            "company": self.company,
            "ontology": {"name": "tenderhub", "version": "V0.4", "classes": 8, "relations": 8},
            "materialsCounts": self._materials_counts(),
            "config": self.public_config(),
            "monitoring": monitoring_status(self),
            "libraries": self.library_snapshot(),
            "catalog": self.filtered_catalog(),
            "reminders": self.reminder_snapshot(),
            "automation": self.automation_status(),
            "workspaces": list(self.workspaces.values()),
        }

    def public_config(self) -> dict[str, Any]:
        config = public_configuration(self.config)
        ai = config.get("ai", {})
        for key in ("apiKey", "key", "token", "password"):
            if key in ai:
                ai[key] = "***"
        return config

    def filtered_catalog(self, query: str = "", region: str = "", type: str = "", current_only: bool = False) -> list[dict[str, Any]]:
        valid_items = [
            item for item in self.catalog
            if item.get("dataOrigin") != "local_upload"
            and is_procurement_notice(
                str(item.get("title", "")),
                str(item.get("analysisText", "")),
                str(item.get("sourceUrl") or item.get("detailUrl") or ""),
            )
        ]
        items = sorted(
            valid_items,
            key=lambda item: str(item.get("fetchedAt") or item.get("publishedDate") or ""),
            reverse=True,
        )
        manual_payload = {"query": query, "region": region, "type": type}
        if current_only:
            items = [item for item in items if item.get("publishedDate", "") >= date.today().isoformat()]
        return [self.with_preview(item) for item in items if matches_manual_search(item, manual_payload)]

    @staticmethod
    def _with_preview(item: dict[str, Any]) -> dict[str, Any]:
        result = dict(item)
        text = str(result.get("analysisText", ""))
        for field in ("registrationPeriod", "registrationDeadline", "bidDeadline"):
            if result.get(field) and not valid_time_value(result[field]):
                result[field] = ""
        result.setdefault("status", "未处理")
        result.setdefault("followUp", False)
        result.setdefault("read", False)
        result.setdefault("reminderStatus", "未提醒")
        result.setdefault("notificationChannels", [])
        result["analysisPreview"] = text[:240]
        return result

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        item = next((item for item in self.catalog if item.get("id") == project_id), None)
        return self.with_preview(item) if item else None

    def search(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .tender_monitor.service import search_catalog

        result = search_catalog(self, payload)
        items = result["items"]
        run = result["search"]
        run["items"] = items
        self.runs.append({"type": "search", **run})
        self.persist_runtime()
        return {"items": items, "search": run, "libraries": self.library_snapshot()}

    def mark_read(self, project_id: str) -> dict[str, Any] | None:
        item = next((item for item in self.catalog if item.get("id") == project_id), None)
        if not item:
            return None
        item["read"] = True
        self.persist_catalog()
        return self.with_preview(item)

    def set_follow_up(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        item = next((item for item in self.catalog if item.get("id") == project_id), None)
        if not item:
            return None
        item["followUp"] = payload.get("followUp", True)
        item["followUpStatus"] = payload.get("followUpStatus", "")
        self.persist_catalog()
        return self.with_preview(item)

    def download_project(self, project_id: str) -> dict[str, Any] | None:
        item = next((item for item in self.catalog if item.get("id") == project_id), None)
        if not item:
            return None
        folder = self.runtime_root / "projects" / project_id / "source"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / f"{project_id}.txt"
        target.write_text(str(item.get("analysisText", "")), encoding="utf-8")
        item["downloadStatus"] = "已下载"
        item["downloadUrl"] = f"/runtime/projects/{project_id}/source/{target.name}"
        item["downloadFileName"] = target.name
        self.persist_catalog()
        return {"item": self.with_preview(item), "download": {"status": "已下载", "fileName": target.name, "url": item["downloadUrl"]}}

    def parse_tender(self, text: str, source: str) -> dict[str, Any]:
        def match(pattern: str, fallback: str = "未识别") -> str:
            result = re.search(pattern, text)
            return result.group(1).strip() if result else fallback

        service_type = match(r"(?:服务类型|项目类型)[:：]\s*([^\r\n]+)", self.detect_type(text))
        qualifications = [
            name for name in ["营业执照", "ISO9001", "ISO14001", "ISO20000", "ISO27001", "ITSS", "CMMI", "CNAS"]
            if name in text
        ]
        performance_lines = [line.strip() for line in text.splitlines() if "类似" in line or "业绩" in line]
        personnel_lines = [line.strip() for line in text.splitlines() if "项目经理" in line or "人员" in line or "团队" in line]
        risks = [line.strip() for line in text.splitlines() if any(word in line for word in ["废标", "无效投标", "否决", "逾期"])]
        return {
            "tenderProjectId": uid("project"),
            "projectName": match(r"(?:采购项目名称|招标项目名称|项目名称)[:：]\s*([^\r\n]+)"),
            "projectCode": match(r"(?:采购项目编号|招标项目编号|项目编号|采购编号|招标编号)[:：]\s*([^\r\n]+)"),
            "tendererName": match(r"(?:采购人名称|招标人名称|采购单位|采购人|招标人)[:：]\s*([^\r\n]+)"),
            "serviceType": service_type,
            "serviceLocation": match(r"服务地点[:：]\s*([^\r\n]+)"),
            "budgetAmount": match(r"(?:预算金额|最高限价)[:：]\s*([^\r\n]+)"),
            "servicePeriod": match(r"(?:服务期限|服务期)[:：]\s*([^\r\n]+)"),
            "registrationPeriod": match(
                r"(?:报名时间|获取(?:招标|采购)?文件时间|获取文件时间)[:：]\s*([^\r\n]+)"
            ),
            "bidDeadline": match(
                r"(?:投标截止时间|响应文件提交截止时间|递交响应文件截止时间)[:：]\s*([^\r\n]+)"
            ),
            "requirements": {
                "qualifications": qualifications,
                "performances": [{"text": "；".join(performance_lines), "required_count": self.required_count(text)}] if performance_lines else [],
                "personnel": [{"text": "；".join(personnel_lines), "role": "项目经理" if "项目经理" in text else "项目人员"}] if personnel_lines else [],
                "technical": [line.strip() for line in text.splitlines() if any(word in line for word in ["服务方案", "应急", "质量", "培训", "考勤", "社保", "报告"])],
                "rejection": risks,
            },
            "rawText": text,
            "source": source,
        }

    @staticmethod
    def detect_type(text: str) -> str:
        for key, value in [
            ("数字孪生", "数字孪生"),
            ("三维可视化", "三维可视化"),
            ("数据中心", "数据中心与运维"),
            ("智慧园区", "智慧园区"),
            ("低空", "低空经济"),
            ("智能体", "AI 智能体"),
            ("数据智能", "数据智能"),
            ("企业架构", "企业架构"),
            ("智慧矿山", "智慧矿山"),
        ]:
            if key in text:
                return value
        return "未识别"

    @staticmethod
    def required_count(text: str) -> int:
        result = re.search(r"(?:不少于|至少|不低于)\s*(\d+)\s*个", text)
        return int(result.group(1)) if result else 0

    def import_document(self, name: str, content: bytes, mime_type: str, document_kind: str = "notice") -> dict[str, Any]:
        if len(content) > 100 * 1024 * 1024:
            raise ValueError("招标文件需小于100MB")
        parsed_file = self.extract_document_content(name, content, mime_type)
        text = parsed_file["text"].strip()
        if len(text) < 20:
            if name.lower().endswith(".doc") or "msword" in mime_type.lower():
                detail = parsed_file.get("warnings", ["未知解析错误"])[0]
                raise ValueError(
                    f"{detail} 请确认文件是未加密的旧版 DOC，或先用 Word 另存为 DOCX。"
                )
            raise ValueError("未能从该文件提取到足够文字，请上传可复制文字的 TXT / Markdown / PDF / DOC / DOCX / XLSX 文件")
        project_id = uid("local")
        source_dir = self.runtime_root / "projects" / project_id / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", name)[:120]
        target = source_dir / f"{project_id}-{safe_name}"
        target.write_bytes(content)
        parsed = self.parse_tender(text, "本地上传")
        kind = "tender_document" if document_kind == "tender_document" else "notice"
        item = {
            "id": project_id,
            "title": parsed["projectName"] if parsed["projectName"] != "未识别" else Path(name).stem,
            "category": parsed["serviceType"],
            "region": parsed["serviceLocation"],
            "noticeType": "本地上传完整招标文件" if kind == "tender_document" else "本地上传招标公告",
            "source": "本地上传",
            "publishedDate": date.today().isoformat(),
            "dataOrigin": "local_upload",
            "analysisText": text,
            "parsed": parsed,
            "document": {
                "id": uid("tender-file"),
                "kind": kind,
                "name": name,
                "mimeType": mime_type or "application/octet-stream",
                "contentHash": hashlib.sha256(content).hexdigest(),
                "parseMethod": parsed_file["parseMethod"],
                "warnings": parsed_file["warnings"],
                "segments": parsed_file["segments"],
                "units": parsed_file.get("units", parsed_file["segments"]),
                "tables": parsed_file.get("tables", []),
                "metadata": parsed_file.get("metadata", {}),
                "sections": parsed_file.get("sections", []),
                "structureWarnings": parsed_file.get("structureWarnings", []),
            },
            "downloadStatus": "已下载",
            "downloadUrl": f"/runtime/projects/{project_id}/source/{target.name}",
        }
        self.catalog.insert(0, item)
        self.persist_catalog()
        return {
            "item": self.with_preview(item),
            "file": {
                "name": name,
                "parseStatus": "解析完成",
                "parseMethod": parsed_file["parseMethod"],
                "warnings": parsed_file["warnings"],
                "extractedTextLength": len(text),
            },
        }

    def extract_text_from_file(self, name: str, content: bytes, mime_type: str = "") -> str:
        return self.extract_document_content(name, content, mime_type)["text"]

    def extract_document_content(self, name: str, content: bytes, mime_type: str = "") -> dict[str, Any]:
        lower = name.lower()
        if lower.endswith(".doc") or "msword" in mime_type.lower():
            return enrich_document_structure(parse_doc(content))
        if lower.endswith(".docx") or "wordprocessingml" in mime_type:
            return enrich_document_structure(parse_docx(content))
        if lower.endswith(".pdf") or "pdf" in mime_type.lower():
            return enrich_document_structure(parse_pdf(content))
        if lower.endswith(".xlsx") or "spreadsheetml" in mime_type:
            return enrich_document_structure(parse_xlsx(content))
        return enrich_document_structure(parse_plain_text(content))

    def extract_document_ir(self, name: str, content: bytes, mime_type: str = ""):
        """Return the typed parser artifact without changing the legacy API."""
        return parse_document_ir(content, file_name=name, mime_type=mime_type)

    @staticmethod
    def extract_text_from_docx(content: bytes) -> str:
        return Store.extract_document_content_from_docx(content)["text"]

    @staticmethod
    def extract_document_content_from_docx(content: bytes) -> dict[str, Any]:
        return parse_docx(content)

    @staticmethod
    def extract_document_content_from_doc(content: bytes) -> dict[str, Any]:
        return parse_doc(content)

    @staticmethod
    def extract_text_from_pdf(content: bytes) -> str:
        return Store.extract_document_content_from_pdf(content)["text"]

    @staticmethod
    def extract_document_content_from_pdf(content: bytes) -> dict[str, Any]:
        return parse_pdf(content)

    @staticmethod
    def extract_document_content_from_xlsx(content: bytes) -> dict[str, Any]:
        return parse_xlsx(content)

    def match_project(self, item: dict[str, Any]) -> dict[str, Any]:
        original_text = str(item.get("analysisText", ""))
        text = original_text
        if item.get("dataOrigin") in {"public_notice_crawl", "custom_site", "monitor_fixture"}:
            text = normalize_notice_text(original_text)
            if text != original_text:
                item["analysisText"] = text[:12000]
        document = item.get("document") if isinstance(item.get("document"), dict) else {}
        source_file_id = str(document.get("id") or document.get("contentHash") or item.get("id") or "")
        result = evaluate_announcement_eligibility(
            text,
            self.company_knowledge,
            self.company,
            ensure_config(self.config).get("ai", {}),
            source_file_id,
        )
        result["relevance"] = evaluate_company_relevance(
            item,
            self.company_knowledge,
            ensure_config(self.config).get("monitoring", {}),
        )
        if document.get("kind") == "tender_document":
            ai_config = ensure_config(self.config).get("ai", {})
            segments = document.get("segments") if isinstance(document.get("segments"), list) else []
            sections = document.get("sections") if isinstance(document.get("sections"), list) else []
            tables = document.get("tables") if isinstance(document.get("tables"), list) else []
            project_id = str(item.get("id") or source_file_id or "project")
            document_meta = {
                "name": str(document.get("name") or document.get("fileName") or item.get("title") or ""),
                "fileName": str(document.get("fileName") or document.get("name") or ""),
                "title": str(item.get("title") or ""),
                "projectId": project_id,
                "projectName": str(item.get("title") or ""),
                "documentKind": "tender_document",
                "contentHash": str(document.get("contentHash") or ""),
            }
            extraction_mode = str(ai_config.get("extractionMode") or "builtin").strip().lower()
            workbuddy_route: dict[str, Any] | None = None

            if extraction_mode == "workbuddy_agent_mcp":
                try:
                    workbuddy_route = evaluate_tender_document_with_workbuddy_agent(
                        text=text,
                        document=document,
                        company_knowledge=self.company_knowledge,
                        ai=ai_config,
                        source_file_id=source_file_id,
                        project_id=project_id,
                        document_meta=document_meta,
                    )
                    fine_review = workbuddy_route["fineReview"]
                except WorkBuddyAgentMatchingError as error:
                    runtime_result = error.runtime_result
                    fallback_mode = str(
                        (ai_config.get("workbuddyAgentMcp") or {}).get("fallbackMode")
                        or "builtin"
                    ).strip().lower()
                    if fallback_mode != "builtin":
                        failed_extraction = {
                            "method": "workbuddy_agent_mcp",
                            "provider": "workbuddy-agent-mcp",
                            "modelUsed": "",
                            "warnings": [],
                            "errors": [str(value) for value in runtime_result.get("errors") or [str(error)]],
                            "agentStatus": "failed",
                            "stats": {"agentStatus": "failed", "fallbackMode": fallback_mode},
                            "analysisPack": None,
                        }
                        fine_review = summarize_fine_review([], failed_extraction)
                        workbuddy_route = {"runtime": runtime_result, "fallback": ""}
                    else:
                        fine_review = evaluate_tender_document(
                            text,
                            segments,
                            self.company_knowledge,
                            ai_config,
                            source_file_id,
                            sections,
                            tables,
                            project_id=project_id,
                            document_meta=document_meta,
                        )
                        extraction = fine_review.get("extraction")
                        if not isinstance(extraction, dict):
                            extraction = {}
                            fine_review["extraction"] = extraction
                        agent_errors = [
                            str(value)
                            for value in runtime_result.get("errors") or [str(error)]
                            if str(value).strip()
                        ]
                        extraction["agentStatus"] = "failed"
                        extraction["agentErrors"] = agent_errors
                        extraction["fallbackMode"] = "builtin"
                        extraction["warnings"] = list(
                            dict.fromkeys(
                                [
                                    *[str(value) for value in extraction.get("warnings") or [] if str(value).strip()],
                                    "workbuddy_agent_fallback_builtin",
                                ]
                            )
                        )
                        extraction.setdefault("stats", {}).update(
                            {
                                "agentProvider": "workbuddy-agent-mcp",
                                "agentStatus": "failed",
                                "fallbackMode": "builtin",
                                "agentErrors": agent_errors,
                            }
                        )
                        workbuddy_route = {"runtime": runtime_result, "fallback": "builtin"}
            else:
                fine_review = evaluate_tender_document(
                    text,
                    segments,
                    self.company_knowledge,
                    ai_config,
                    source_file_id,
                    sections,
                    tables,
                    project_id=project_id,
                    document_meta=document_meta,
                )

            review_output = build_review_output_from_analysis_pack(fine_review)
            fine_review["reviewOutput"] = review_output
            result["fineReview"] = fine_review
            result["reviewOutput"] = review_output
            extraction = fine_review.get("extraction")
            if isinstance(extraction, dict):
                extraction["reviewOutput"] = review_output
                analysis_pack = fine_review.get("analysisPack")
                if not isinstance(analysis_pack, dict):
                    analysis_pack = extraction.get("analysisPack")
                if isinstance(analysis_pack, dict):
                    analysis_pack["reviewOutput"] = review_output
            if workbuddy_route is not None:
                result["workbuddyAgent"] = workbuddy_route
        return result

    def analyze(self, project_id: str) -> dict[str, Any] | None:
        item = next((entry for entry in self.catalog if entry.get("id") == project_id), None)
        if not item:
            return None
        previous_workspace = self.workspaces.get(item.get("workspaceId"))
        reasoning = self.match_project(item)
        workspace_id = item.get("workspaceId") or uid("workspace")
        item["workspaceId"] = workspace_id
        item["status"] = "已分析"
        item["score"] = reasoning["score"]
        item["decision"] = reasoning["decision"]
        workspace = {
            "id": workspace_id,
            "workspaceId": workspace_id,
            "projectId": project_id,
            "title": item.get("title", ""),
            "project": item,
            "reasoning": reasoning,
            "draft": self.build_draft(item, reasoning),
            "draftVersion": 1,
        }
        if isinstance(previous_workspace, dict):
            # Re-analysis refreshes extraction while preserving user-authored
            # response text and the audit trail from the prior workspace.
            if previous_workspace.get("draft"):
                workspace["draft"] = previous_workspace["draft"]
                workspace["draftVersion"] = int(previous_workspace.get("draftVersion", 1) or 1)
            if previous_workspace.get("auditRecords"):
                workspace["auditRecords"] = previous_workspace["auditRecords"]
        self.workspaces[workspace_id] = workspace
        self.persist_catalog()
        self.persist_runtime()
        return {"item": self.with_preview(item), "workspace": workspace, "reasoning": reasoning}

    def batch_match(self, project_ids: list[str]) -> dict[str, Any]:
        results = []
        for project_id in project_ids or [item.get("id") for item in self.catalog[:10]]:
            item = next((entry for entry in self.catalog if entry.get("id") == project_id), None)
            if item:
                results.append({"projectId": project_id, "title": item.get("title"), **self.match_project(item)})
        return {"results": results, "count": len(results)}

    def build_draft(self, item: dict[str, Any], reasoning: dict[str, Any]) -> str:
        missing = "\n".join(f"- {row['requirement']}（{row['status']}；依据：{row['evidence']}）" for row in reasoning["rows"] if row["status"] != "满足")
        return (
            f"# {item.get('title', '投标项目')}\n\n"
            "## 一、项目理解\n"
            f"本项目为{item.get('region', '待识别')}的{item.get('category', '服务')}项目。我方将围绕招标文件要求建立项目服务机制。\n\n"
            "## 二、项目组织与人员配置\n"
            "拟由项目经理统一负责项目协调，配置招聘、现场管理及后台支持人员，具体材料将在定稿前逐项补齐。\n\n"
            "## 三、服务实施方案\n"
            "围绕人员招聘、入职、考勤、薪酬、服务报告、投诉处理和应急响应建立闭环流程。\n\n"
            "## 四、材料与人工确认清单\n"
            f"{missing or '- 暂未发现结构化缺失项，仍需人工核验原始文件和附件。'}\n\n"
            "> 当前内容是可审核底稿，不代表可以直接递交。\n"
        )

    def workspace(self, workspace_id: str) -> dict[str, Any] | None:
        return self.workspaces.get(workspace_id)

    def save_draft(self, workspace_id: str, draft: str) -> dict[str, Any] | None:
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        workspace["draft"] = draft
        workspace["draftVersion"] = int(workspace.get("draftVersion", 0)) + 1
        self.persist_runtime()
        return {"workspace": workspace}

    def update_fine_review_row(
        self,
        workspace_id: str,
        row_id: str,
        status: str,
        reviewer: str = "当前用户",
    ) -> dict[str, Any] | None:
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        reasoning = workspace.get("reasoning")
        if not isinstance(reasoning, dict):
            return None
        fine_review = reasoning.get("fineReview")
        if not isinstance(fine_review, dict):
            return None
        rows = fine_review.get("rows")
        previous_row = next(
            (
                row for row in rows
                if isinstance(row, dict) and str(row.get("id", "")) == row_id
            ),
            None,
        ) if isinstance(rows, list) else None
        previous_status = str(previous_row.get("status", "待澄清")) if previous_row else ""
        updated = update_fine_review_status(fine_review, row_id, status)
        if not updated:
            return None
        reasoning["fineReview"] = updated
        if previous_status != status:
            audit_records = workspace.setdefault("auditRecords", [])
            if not isinstance(audit_records, list):
                audit_records = []
                workspace["auditRecords"] = audit_records
            audit_records.append(
                {
                    "id": uid("audit"),
                    "workspaceId": workspace_id,
                    "projectId": str(workspace.get("projectId", "")),
                    "projectTitle": str(workspace.get("title", "")),
                    "rowId": row_id,
                    "requirement": str(previous_row.get("requirement", "")) if previous_row else "",
                    "previousStatus": previous_status,
                    "newStatus": status,
                    "reviewer": reviewer.strip() or "当前用户",
                    "reviewedAt": now_iso(),
                    "source": "人工审核",
                }
            )
            workspace["auditRecords"] = audit_records[-200:]
        self.persist_runtime()
        return {"workspace": workspace}

    def generate_response(self, project_id: str) -> dict[str, Any] | None:
        item = next((entry for entry in self.catalog if entry.get("id") == project_id), None)
        if not item:
            return None
        workspace = self.workspaces.get(item.get("workspaceId")) or self.analyze(project_id)
        workspace = workspace.get("workspace") if workspace and "workspace" in workspace else workspace
        if not workspace:
            return None
        output_dir = self.runtime_root / "projects" / project_id / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "-", str(item.get("title", "response")))[:70].strip("-") or "response"
        markdown = output_dir / f"{stem}.md"
        archive = output_dir / f"{stem}.json"
        markdown.write_text(workspace["draft"], encoding="utf-8")
        archive.write_text(json.dumps({"project": item, "workspace": workspace}, ensure_ascii=False, indent=2), encoding="utf-8")
        files = [
            {"name": markdown.name, "url": f"/runtime/projects/{project_id}/outputs/{markdown.name}"},
            {"name": archive.name, "url": f"/runtime/projects/{project_id}/outputs/{archive.name}"},
        ]
        try:
            from docx import Document

            docx_file = output_dir / f"{stem}.docx"
            document = Document()
            for line in workspace["draft"].splitlines():
                if line.startswith("# "):
                    document.add_heading(line[2:], level=1)
                elif line.startswith("## "):
                    document.add_heading(line[3:], level=2)
                elif line.startswith("- "):
                    document.add_paragraph(line[2:], style="List Bullet")
                elif line.startswith("> "):
                    document.add_paragraph(line[2:], style="Intense Quote")
                else:
                    document.add_paragraph(line)
            document.save(docx_file)
            files.insert(0, {"name": docx_file.name, "url": f"/runtime/projects/{project_id}/outputs/{docx_file.name}"})
        except Exception:
            pass
        return {"message": "响应文件已生成", "files": files}
    # ------------------------------------------------------------------
    # 标书撰写模块（M3 产出层）：规划 / 生成 / 导出 / 校对
    # ------------------------------------------------------------------
    def plan_bid_document(self, workspace_id: str) -> dict[str, Any] | None:
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
        fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
        project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}
        document = project.get("document") if isinstance(project.get("document"), dict) else {}
        sections = document.get("sections") if isinstance(document.get("sections"), list) else []

        from .bid_writing.deviations import build_deviation_tables
        from .bid_writing.planning import plan_bid_document as _plan
        from .bid_writing.planning import plan_from_outline
        from .bid_writing.schemas import build_bid_document
        from .bid_writing.outline import extract_response_outline
        from .bid_writing.template_reuse import attach_templates

        bid_doc = build_bid_document(workspace_id, str(workspace.get("projectId", "")))
        ai = ensure_config(self.config).get("ai", {})

        outline = extract_response_outline(sections, ai)
        if outline and outline.get("items"):
            plan = attach_templates(plan_from_outline(outline["items"], fine_review), sections)
            bid_doc["planSource"] = "tender_document"
            bid_doc["planSourceDetail"] = str(outline.get("sourceDetail") or "")
        else:
            plan = _plan(fine_review)
            bid_doc["planSource"] = "builtin_template"
            bid_doc["planSourceDetail"] = ""

        bid_doc["plan"] = plan
        bid_doc["deviationTables"] = build_deviation_tables(fine_review)
        bid_doc["status"] = "planned"
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {"bidDocument": bid_doc, "workspace": workspace}

    def _fill_bid_section(
        self,
        section: dict[str, Any],
        project: dict[str, Any],
        organization: dict[str, Any],
        commercial: dict[str, Any],
        technical: dict[str, Any],
    ) -> dict[str, Any]:
        """按 source/kind 填充单个章节（模板复用 / 报价占位 / 商务/技术生成）。"""
        from .bid_writing.template_reuse import fill_section_template

        section_id = section.get("sectionId", "")
        if section.get("source") == "copied_template":
            try:
                filled = fill_section_template(section, project, organization, self.company)
            except Exception:
                filled = {}
            if filled:
                section["content"] = str(filled.get("content", ""))
                section["blanksToFill"] = filled.get("blanksToFill") or []
                section["status"] = filled.get("status", "generated")
            return section
        if section_id == "p-01":
            if not section.get("content"):
                section["content"] = "# 开标一览表\n\n【需人工填写：投标报价】"
                section["status"] = "missing"
            return section
        merged = commercial.get(section_id) or technical.get(section_id)
        if merged:
            section["content"] = str(merged.get("content", ""))
            section["sourceRefs"] = merged.get("sourceRefs") or []
            section["status"] = merged.get("status", "generated")
        return section

    def save_bid_plan(self, workspace_id: str, plan: list[dict[str, Any]]) -> dict[str, Any] | None:
        """保存用户编辑后的章节目录。"""
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        if not isinstance(workspace.get("bidDocument"), dict):
            self.plan_bid_document(workspace_id)
        bid_doc = workspace.get("bidDocument") or {}
        if isinstance(plan, list):
            bid_doc["plan"] = plan
        bid_doc["status"] = "planned"
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {"bidDocument": bid_doc, "workspace": workspace}

    def generate_bid_section(self, workspace_id: str, section_id: str) -> dict[str, Any] | None:
        """按章节生成内容（一键自动撰写单章）。"""
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        if not isinstance(workspace.get("bidDocument"), dict):
            self.plan_bid_document(workspace_id)
        bid_doc = workspace.get("bidDocument") or {}
        section = next((s for s in bid_doc.get("plan", []) if s.get("sectionId") == section_id), None)
        if not section:
            return None
        reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
        fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
        project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}
        ai = ensure_config(self.config).get("ai", {})
        organization = self.organization or {}
        self._refresh_company_from_m3(force=True)

        commercial: dict[str, Any] = {}
        technical: dict[str, Any] = {}
        if section.get("kind") == "technical":
            from .bid_writing.technical import generate_technical_section

            technical = {
                section_id: generate_technical_section(
                    section, fine_review, project, self.company, self.company_knowledge, ai
                ) or {}
            }
        else:
            from .bid_writing.commercial import generate_commercial_sections

            commercial = (
                generate_commercial_sections(
                    project, self.company, self.company_knowledge, organization, ai
                )
                or {}
            )

        self._fill_bid_section(section, project, organization, commercial, technical)
        bid_doc["status"] = "generated"
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {"bidDocument": bid_doc}

    def save_bid_section(self, workspace_id: str, section_id: str, content: str) -> dict[str, Any] | None:
        """保存用户手动编辑后的章节正文。"""
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        if not isinstance(workspace.get("bidDocument"), dict):
            self.plan_bid_document(workspace_id)
        bid_doc = workspace.get("bidDocument") or {}
        section = next((s for s in bid_doc.get("plan", []) if s.get("sectionId") == section_id), None)
        if not section:
            return None
        section["content"] = str(content or "")
        section["status"] = "generated"
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {"bidDocument": bid_doc}

    def generate_bid_document(self, workspace_id: str, mode: str = "open") -> dict[str, Any] | None:

        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        if not isinstance(workspace.get("bidDocument"), dict):
            self.plan_bid_document(workspace_id)
        bid_doc = workspace.get("bidDocument") or {}
        bid_doc["status"] = "generating"

        reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
        fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
        project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}
        self._refresh_company_from_m3(force=True)
        ai = ensure_config(self.config).get("ai", {})
        organization = self.organization or {}

        from .bid_writing.commercial import generate_commercial_sections
        from .bid_writing.technical import generate_technical_sections

        commercial: dict[str, Any] = {}
        technical: dict[str, Any] = {}
        try:
            commercial = generate_commercial_sections(project, self.company, self.company_knowledge, organization, ai) or {}
        except Exception:
            commercial = {}
        try:
            technical = generate_technical_sections(bid_doc.get("plan", []), fine_review, project, self.company, self.company_knowledge, ai) or {}
        except Exception:
            technical = {}

        for section in bid_doc.get("plan", []):
            self._fill_bid_section(section, project, organization, commercial, technical)

        bid_doc["mode"] = "secret" if mode == "secret" else "open"
        bid_doc["status"] = "generated"
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {"bidDocument": bid_doc}

    def export_bid_document(self, workspace_id: str, mode: str = "open") -> dict[str, Any] | None:
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        bid_doc = workspace.get("bidDocument")
        if not isinstance(bid_doc, dict):
            return None
        if bid_doc.get("status") != "generated" or not any(str(s.get("content", "")) for s in bid_doc.get("plan", [])):
            self.generate_bid_document(workspace_id, mode)
            bid_doc = workspace.get("bidDocument") or {}

        doc_to_export = bid_doc
        if mode == "secret":
            from .bid_writing.anonymize import anonymize_document
            doc_to_export = anonymize_document(bid_doc, self.company, self.organization or {})

        project_id = str(workspace.get("projectId", ""))
        item = next((entry for entry in self.catalog if entry.get("id") == project_id), None)
        output_dir = self.runtime_root / "projects" / project_id / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)

        from .bid_writing.assemble import assemble_docx

        # 证据图文装配：把各章节 sourceRefs 的 evidencePath 解析为可插图资产。
        # 暗标（secret）不插图 —— 图片内的 LOGO/印章无法可靠脱敏，只保留文字占位。
        assets: dict[str, Any] = {}
        if mode != "secret":
            from .media_store.local import LocalFolderStore
            from .bid_writing.evidence_assembler import assemble_evidence

            _refs: list[dict[str, Any]] = []
            for _section in doc_to_export.get("plan", []):
                _refs.extend(r for r in (_section.get("sourceRefs") or []) if isinstance(r, dict))
            assets = assemble_evidence(_refs, LocalFolderStore(self.runtime_root))

        result = assemble_docx(doc_to_export, output_dir, assets=assets or None)
        path = Path(result["path"]) if result.get("path") else None
        if path is None or not path.exists():
            return {"files": [], "mode": mode, "error": "导出失败：未生成文件"}
        files = [{"name": path.name, "url": f"/runtime/projects/{project_id}/outputs/{path.name}"}]
        archive = output_dir / f"{path.stem}.json"
        write_json(archive, {"mode": mode, "bidDocument": doc_to_export})
        files.append({"name": archive.name, "url": f"/runtime/projects/{project_id}/outputs/{archive.name}"})
        return {"files": files, "mode": mode, "bidDocument": doc_to_export}

    def proofread_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        if not isinstance(workspace.get("bidDocument"), dict):
            self.plan_bid_document(workspace_id)
        bid_doc = workspace.get("bidDocument") or {}

        reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
        fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
        project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}

        from .bid_writing.proofread import proofread

        result = proofread(bid_doc, fine_review, project, self.company) or {}
        bid_doc["proofread"] = result
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return result

    def ask(self, question: str, project_id: str = "", agent: bool = False) -> dict[str, Any]:
        project = self.get_project(project_id) if project_id else None
        llm_answer = self.try_llm_answer(question, project)
        if llm_answer:
            return {"answer": llm_answer, "action": "agent" if agent else "answer", "sourceRefs": ["llm", "catalog", "libraries"]}
        if "资料" in question or "材料" in question:
            answer = f"当前共有 {sum(len(value.get('documents', [])) for value in self.libraries.values())} 份已登记资料。"
        elif project:
            answer = f"当前项目“{project.get('title')}”的状态是 {project.get('status', '待分析')}，可以执行信息匹配并生成响应底稿。"
        else:
            answer = "可以查询招标项目、公司资料、匹配结果和响应文件生成状态。"
        return {"answer": answer, "action": "agent" if agent else "answer", "sourceRefs": ["catalog", "libraries"]}

    def try_llm_answer(self, question: str, project: dict[str, Any] | None = None) -> str:
        system_prompt = (
            "你是企业内部招投标智能体。"
            "请用简洁中文回答，优先引用当前项目、资料库、匹配结果和风险结论。"
            "如果信息不足，请直接说明需要补充哪些材料。"
        )
        if project:
            system_prompt += f" 当前项目名称：{project.get('title', '')}。"
        result = call_openai_compatible(
            ensure_config(self.config).get("ai", {}),
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            max_tokens=800,
            temperature=0.2,
        )
        return str(result.get("content", "")) if result.get("success") else ""

    @staticmethod
    def test_ai_connection(ai: dict[str, Any]) -> dict[str, Any]:
        return test_ai_connection(ai)

    def library_snapshot(self) -> list[dict[str, Any]]:
        return [
            {
                "key": key,
                "name": value.get("name", key),
                "description": value.get("description", ""),
                "documentCount": len(value.get("documents", [])),
                "documents": value.get("documents", [])[-8:],
            }
            for key, value in self.libraries.items()
        ]

    def library_detail(self, key: str) -> dict[str, Any] | None:
        value = self.libraries.get(key)
        if not value:
            return None
        return {"key": key, **value}

    def save_library_document(self, key: str, name: str, content: bytes, mime_type: str) -> dict[str, Any]:
        if key not in self.libraries:
            raise ValueError("资料库不存在")
        library_dir = self.runtime_root / "libraries" / key
        library_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", name)[:120] or "document.txt"
        target = library_dir / f"{uid('doc')}-{safe_name}"
        target.write_bytes(content)
        document = {
            "id": uid("document"),
            "name": name,
            "type": mime_type or "资料文档",
            "savedAt": now_iso(),
            "storagePath": f"runtime/libraries/{key}/{target.name}",
            "contentHash": hashlib.sha256(content).hexdigest(),
            "size": len(content),
            "preview": content.decode("utf-8", errors="ignore")[:12000],
        }
        self.libraries[key].setdefault("documents", []).append(document)
        self.persist_libraries()
        return {"document": document, "libraries": self.library_snapshot()}

    def delete_library_document(self, key: str, document_id: str) -> dict[str, Any]:
        value = self.libraries.get(key)
        if not value:
            raise HTTPException(status_code=404, detail="资料库不存在")
        documents = value.setdefault("documents", [])
        document = next((item for item in documents if item.get("id") == document_id), None)
        if not document:
            raise HTTPException(status_code=404, detail="未找到资料")
        value["documents"] = [item for item in documents if item.get("id") != document_id]
        self.persist_libraries()
        return {"document": document, "libraries": self.library_snapshot()}

    def save_config(self, incoming: dict[str, Any]) -> dict[str, Any]:
        self.config = ensure_config({**self.config, **incoming})
        write_json(self.config_path, self.config)
        return {"config": self.public_config()}

    def automation_status(self) -> dict[str, Any]:
        return {"running": False, "totalRuns": len(self.runs), "latest": self.runs[-1] if self.runs else None}

    def run_automation(self) -> dict[str, Any]:
        from .tender_monitor.service import run_monitoring

        result = run_monitoring(self)
        run = {"status": result.get("status", "完成"), "startedAt": now_iso(), "monitoring": result}
        self.runs.append(run)
        self.persist_runtime()
        return {"run": run}

    def reminder_snapshot(self) -> list[dict[str, Any]]:
        return self.reminders[-100:]

    def create_reminder(self, payload: dict[str, Any]) -> dict[str, Any]:
        reminder = {"id": uid("reminder"), **payload, "createdAt": now_iso(), "status": "待处理"}
        self.reminders.append(reminder)
        self.persist_runtime()
        return {"reminder": reminder, "reminders": self.reminder_snapshot()}
