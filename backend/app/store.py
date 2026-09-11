from __future__ import annotations

import hashlib
import json
import re
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
from .tender_matching.document_fine_matching import evaluate_tender_document, update_fine_review_status
from .tender_matching.document_parser import parse_docx, parse_pdf, parse_plain_text, parse_xlsx
from .tender_matching.document_structure import enrich_document_structure
from .tender_matching.service import evaluate_company_relevance, matches_manual_search
from .tender_monitor.adapters import is_procurement_notice, repair_notice_fields, valid_time_value


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return fallback


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class Store:
    KNOWLEDGE_COLLECTIONS: tuple[tuple[str, str], ...] = (
        ("businessDirections", "业务方向"),
        ("industryScenarios", "行业场景"),
        ("products", "产品或服务"),
        ("capabilities", "技术能力"),
        ("qualifications", "资质证书"),
        ("projectReferences", "项目业绩"),
        ("materials", "企业材料"),
        ("evidences", "证据"),
        ("sourceFiles", "来源文件"),
    )

    def __init__(self, root: Path):
        self.root = root
        self.runtime_root = root / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.runtime_root / "catalog.json"
        self.libraries_path = self.runtime_root / "libraries.json"
        self.config_path = root / "agent-config.json"
        self.catalog_seed_path = root / "demo-data" / "tender-catalog.json"
        self.company_path = root / "company-profile.json"
        self.company_material_index_path = root / "company-material-index.json"
        self.company_knowledge_path = root / "company-knowledge.json"
        self.ontology_path = root / "ontology-governance.json"
        self.workspaces_path = self.runtime_root / "workspaces.json"
        self.catalog: list[dict[str, Any]] = []
        self.libraries: dict[str, Any] = {}
        self.config: dict[str, Any] = {}
        self.company: dict[str, Any] = {}
        self.company_material_index: dict[str, Any] = {}
        self.company_knowledge: dict[str, Any] = {}
        self.ontology: dict[str, Any] = {}
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.reminders: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []

    def initialize(self) -> None:
        self.company = read_json(self.company_path, {})
        self.company_material_index = read_json(self.company_material_index_path, {})
        self.company_knowledge = read_json(self.company_knowledge_path, {})
        self.ontology = read_json(self.ontology_path, {})
        # The runtime catalog is authoritative. New tender projects must enter
        # through a real monitor result or the upload endpoint.
        self.catalog = read_json(self.catalog_path, [])
        self.config = ensure_config(read_json(self.config_path, self.default_config()))
        self.libraries = read_json(self.libraries_path, self.default_libraries())
        self.reminders = read_json(self.runtime_root / "reminders.json", [])
        self.runs = read_json(self.runtime_root / "automation-runs.json", [])
        self.workspaces = read_json(self.workspaces_path, {})
        self._seed_company_library_index()
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

    def _seed_company_library_index(self) -> None:
        """Expose indexed UINO source files before all originals are uploaded."""
        if not self.company_material_index:
            return
        company_library = self.libraries.setdefault(
            "company",
            {
                "key": "company",
                "name": "公司资料库",
                "description": "企业资质、业绩、人员与服务能力",
                "documents": [],
            },
        )
        documents = company_library.setdefault("documents", [])
        legacy_markers = (
            "demo-company-",
            "濮阳人才服务",
            "安衡人力资源",
            "劳务派遣经营许可证_模拟样本",
            "人力资源服务许可证_模拟样本",
            "公司综合材料_模拟版",
            "公司综合介绍（演示）",
        )
        company_library["documents"] = [
            item
            for item in documents
            if not any(
                marker in f"{item.get('id', '')} {item.get('name', '')} {item.get('preview', '')}"
                for marker in legacy_markers
            )
        ]
        documents = company_library["documents"]
        existing_ids = {str(item.get("sourceId") or item.get("id")) for item in documents}
        for source in self.company_material_index.get("documents", []):
            source_id = str(source.get("id", "")).strip()
            if not source_id or source_id in existing_ids:
                continue
            documents.append(
                {
                    "id": f"indexed-{source_id.lower()}",
                    "sourceId": source_id,
                    "name": source.get("name", source_id),
                    "type": source.get("type", "资料"),
                    "savedAt": "",
                    "storagePath": source.get("sourcePath", ""),
                    "contentHash": "",
                    "size": 0,
                    "preview": source.get("purpose", ""),
                    "indexedOnly": True,
                    "status": source.get("status", "来源登记"),
                    "evidenceLevel": source.get("evidenceLevel", "待核验"),
                }
            )
            existing_ids.add(source_id)
        self.persist_libraries()

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

    def persist_catalog(self) -> None:
        write_json(self.catalog_path, self.catalog)

    def persist_libraries(self) -> None:
        write_json(self.libraries_path, self.libraries)

    def persist_runtime(self) -> None:
        write_json(self.runtime_root / "reminders.json", self.reminders[-100:])
        write_json(self.runtime_root / "automation-runs.json", self.runs[-50:])
        write_json(self.workspaces_path, self.workspaces)

    @staticmethod
    def current_time() -> str:
        return now_iso()

    @staticmethod
    def new_id(prefix: str) -> str:
        return uid(prefix)

    def seed_catalog(self) -> list[dict[str, Any]]:
        return read_json(self.catalog_seed_path, [])

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
                "excludeReason": match["excludeReason"],
                "nextStage": match["nextStage"],
                "shouldAnalyze": match["shouldAnalyze"],
            }
        )
        return result

    def bootstrap(self) -> dict[str, Any]:
        from .tender_monitor.service import monitoring_status

        return {
            "ontology": {
                "name": self.ontology.get("meta", {}).get("name", "招投标智能体本体"),
                "version": self.ontology.get("meta", {}).get("version", "1.0.0"),
                "classes": len(self.ontology.get("classes", [])),
                "relations": len(self.ontology.get("relations", [])),
            },
            "company": self.company,
            "companyMaterialIndex": self.company_material_index,
            "companyKnowledge": self.company_knowledge,
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
            if is_procurement_notice(
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
            name for name in ["营业执照", "人力资源服务许可证", "劳务派遣经营许可证", "保安服务许可证"]
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
            ("劳务派遣", "劳务派遣"),
            ("保安", "保安服务"),
            ("保洁", "保洁服务"),
            ("家政", "家政服务"),
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
            raise ValueError("未能从该文件提取到足够文字，请上传可复制文字的 TXT / Markdown / PDF / DOCX / XLSX 文件")
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
        if lower.endswith(".docx") or "wordprocessingml" in mime_type:
            return enrich_document_structure(parse_docx(content))
        if lower.endswith(".pdf") or "pdf" in mime_type.lower():
            return enrich_document_structure(parse_pdf(content))
        if lower.endswith(".xlsx") or "spreadsheetml" in mime_type:
            return enrich_document_structure(parse_xlsx(content))
        return enrich_document_structure(parse_plain_text(content))

    @staticmethod
    def extract_text_from_docx(content: bytes) -> str:
        return Store.extract_document_content_from_docx(content)["text"]

    @staticmethod
    def extract_document_content_from_docx(content: bytes) -> dict[str, Any]:
        return parse_docx(content)

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
        text = str(item.get("analysisText", ""))
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
            result["fineReview"] = evaluate_tender_document(
                text,
                document.get("segments") if isinstance(document.get("segments"), list) else [],
                self.company_knowledge,
                ensure_config(self.config).get("ai", {}),
                source_file_id,
                document.get("sections") if isinstance(document.get("sections"), list) else [],
                document.get("tables") if isinstance(document.get("tables"), list) else [],
            )
        return result

    def analyze(self, project_id: str) -> dict[str, Any] | None:
        item = next((entry for entry in self.catalog if entry.get("id") == project_id), None)
        if not item:
            return None
        reasoning = self.match_project(item)
        workspace_id = item.get("workspaceId") or uid("workspace")
        item["workspaceId"] = workspace_id
        item["status"] = "已分析"
        item["score"] = reasoning["score"]
        item["decision"] = reasoning["decision"]
        self.workspaces[workspace_id] = {
            "id": workspace_id,
            "workspaceId": workspace_id,
            "projectId": project_id,
            "title": item.get("title", ""),
            "project": item,
            "reasoning": reasoning,
            "draft": self.build_draft(item, reasoning),
            "draftVersion": 1,
        }
        self.persist_catalog()
        self.persist_runtime()
        return {"item": self.with_preview(item), "workspace": self.workspaces[workspace_id], "reasoning": reasoning}

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

    def _knowledge_collection(self, object_type: str) -> tuple[str, str, list[dict[str, Any]]] | None:
        for key, label in self.KNOWLEDGE_COLLECTIONS:
            if key == object_type:
                values = self.company_knowledge.get(key, [])
                return key, label, values if isinstance(values, list) else []
        return None

    def _knowledge_index(self) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        company_id = str(self.company.get("companyId") or self.company_knowledge.get("meta", {}).get("companyId") or "").strip()
        company_name = str(self.company.get("companyName") or self.company_knowledge.get("meta", {}).get("companyName") or "").strip()
        if company_id:
            index[company_id] = {
                "objectType": "company",
                "objectTypeLabel": "公司主体",
                "item": {"id": company_id, "name": company_name, "companyName": company_name},
            }
        for key, label in self.KNOWLEDGE_COLLECTIONS:
            collection = self._knowledge_collection(key)
            if not collection:
                continue
            for item in collection[2]:
                item_id = str(item.get("id", "")).strip()
                if item_id:
                    index[item_id] = {"objectType": key, "objectTypeLabel": label, "item": item}
        source_index = {
            str(item.get("id", "")).strip(): item
            for item in self.company_material_index.get("documents", [])
            if str(item.get("id", "")).strip()
        }
        for item_id, entry in index.items():
            if entry["objectType"] != "sourceFiles":
                continue
            source = source_index.get(item_id)
            if source:
                entry["item"] = {**entry["item"], "sourcePath": source.get("sourcePath", ""), "purpose": source.get("purpose", "")}
        return index

    @staticmethod
    def _knowledge_matches(item: dict[str, Any], query: str) -> bool:
        if not query:
            return True
        haystack = " ".join(
            str(value)
            for value in item.values()
            if isinstance(value, (str, int, float, list, dict))
        ).lower()
        return query.lower() in haystack

    def knowledge_objects(self, object_type: str = "", query: str = "") -> dict[str, Any]:
        collections: list[dict[str, Any]] = []
        selected = [object_type] if object_type else [key for key, _ in self.KNOWLEDGE_COLLECTIONS]
        for key in selected:
            collection = self._knowledge_collection(key)
            if not collection:
                continue
            _, label, values = collection
            items = [
                {"objectType": key, "objectTypeLabel": label, **item}
                for item in values
                if self._knowledge_matches(item, query.strip())
            ]
            collections.append({"objectType": key, "objectTypeLabel": label, "count": len(items), "items": items})
        return {
            "companyId": self.company.get("companyId", self.company_knowledge.get("meta", {}).get("companyId", "")),
            "companyName": self.company.get("companyName", self.company_knowledge.get("meta", {}).get("companyName", "")),
            "query": query,
            "collections": collections,
        }

    def knowledge_object_detail(self, object_type: str, object_id: str) -> dict[str, Any] | None:
        collection = self._knowledge_collection(object_type)
        if not collection:
            return None
        _, label, values = collection
        item = next((entry for entry in values if str(entry.get("id", "")) == object_id), None)
        if not item:
            return None

        index = self._knowledge_index()
        related: list[dict[str, Any]] = []
        for relation in self.company_knowledge.get("relations", []):
            if relation.get("fromId") != object_id and relation.get("toId") != object_id:
                continue
            related_id = relation.get("toId") if relation.get("fromId") == object_id else relation.get("fromId")
            related_entry = index.get(str(related_id))
            related.append(
                {
                    **relation,
                    "direction": "outgoing" if relation.get("fromId") == object_id else "incoming",
                    "relatedObject": (
                        {
                            "objectType": related_entry["objectType"],
                            "objectTypeLabel": related_entry["objectTypeLabel"],
                            **related_entry["item"],
                        }
                        if related_entry
                        else None
                    ),
                }
            )

        def resolve_ids(ids: Any) -> list[dict[str, Any]]:
            if not isinstance(ids, list):
                return []
            result: list[dict[str, Any]] = []
            for item_id in ids:
                entry = index.get(str(item_id))
                if entry:
                    result.append(
                        {
                            "objectType": entry["objectType"],
                            "objectTypeLabel": entry["objectTypeLabel"],
                            **entry["item"],
                        }
                    )
            return result

        evidence_ids = item.get("evidenceIds", [])
        source_ids = item.get("sourceIds", [])
        material_ids = item.get("materialIds", [])
        if object_type == "evidences":
            evidence_ids = [object_id]
            source_ids = item.get("sourceIds", [])
        if object_type == "sourceFiles":
            source_ids = [object_id]
        resolved_evidences = resolve_ids(evidence_ids)
        resolved_materials = resolve_ids(material_ids)
        resolved_source_ids = list(source_ids) if isinstance(source_ids, list) else []
        for evidence in resolved_evidences:
            for source_id in evidence.get("sourceIds", []):
                if source_id not in resolved_source_ids:
                    resolved_source_ids.append(source_id)
        for material in resolved_materials:
            for source_id in material.get("sourceIds", []):
                if source_id not in resolved_source_ids:
                    resolved_source_ids.append(source_id)

        return {
            "objectType": object_type,
            "objectTypeLabel": label,
            "object": item,
            "related": related,
            "evidences": resolved_evidences,
            "materials": resolved_materials,
            "sourceFiles": resolve_ids(resolved_source_ids),
        }

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
