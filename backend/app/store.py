from __future__ import annotations

import hashlib
import json
import os
import re
import zipfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from urllib import error, request
import xml.etree.ElementTree as ET

from fastapi import HTTPException
from .config.service import ensure_config, public_configuration
from .tender_matching.service import filter_catalog_by_monitoring, matches_manual_search


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
    def __init__(self, root: Path):
        self.root = root
        self.runtime_root = root / "runtime"
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        self.catalog_path = self.runtime_root / "catalog.json"
        self.libraries_path = self.runtime_root / "libraries.json"
        self.config_path = root / "agent-config.json"
        self.catalog_seed_path = root / "demo-data" / "tender-catalog.json"
        self.company_path = root / "company-profile.json"
        self.ontology_path = root / "ontology-governance.json"
        self.workspaces_path = self.runtime_root / "workspaces.json"
        self.catalog: list[dict[str, Any]] = []
        self.libraries: dict[str, Any] = {}
        self.config: dict[str, Any] = {}
        self.company: dict[str, Any] = {}
        self.ontology: dict[str, Any] = {}
        self.workspaces: dict[str, dict[str, Any]] = {}
        self.reminders: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []

    def initialize(self) -> None:
        self.company = read_json(self.company_path, {})
        self.ontology = read_json(self.ontology_path, {})
        seed = read_json(self.catalog_seed_path, [])
        self.catalog = read_json(self.catalog_path, seed)
        self.config = ensure_config(read_json(self.config_path, self.default_config()))
        self.libraries = read_json(self.libraries_path, self.default_libraries())
        self.reminders = read_json(self.runtime_root / "reminders.json", [])
        self.runs = read_json(self.runtime_root / "automation-runs.json", [])
        self.workspaces = read_json(self.workspaces_path, {})
        self._seed_demo_records()
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

    def _seed_demo_records(self) -> None:
        if self.catalog:
            return
        self.catalog = [
            {
                "id": "sample-hospital-labor",
                "title": "医院劳务派遣服务项目（完整招标样本）",
                "category": "劳务派遣",
                "region": "河南省濮阳市",
                "noticeType": "Demo 完整招标文件",
                "source": "本地脱敏示例",
                "publishedDate": "2026-09-01",
                "servicePeriod": "12个月",
                "dataOrigin": "demo",
                "analysisText": (
                    "项目名称：濮阳市某医院2026年度劳务派遣服务项目\n"
                    "项目编号：PY-HR-2026-018\n招标人：濮阳市某医院\n"
                    "服务类型：劳务派遣\n服务地点：河南省濮阳市\n预算金额：人民币300万元/年\n"
                    "服务期限：12个月\n投标截止时间：2026-09-08 09:30\n\n"
                    "一、投标人资格要求\n1. 具有有效营业执照。\n"
                    "2. 具有有效的人力资源服务许可证和劳务派遣经营许可证。\n"
                    "3. 近三年承担过不少于3个类似医院或事业单位劳务派遣项目。\n"
                    "4. 拟派项目经理1名，具有3年以上人力资源服务项目管理经验。\n\n"
                    "二、服务要求\n负责人员招聘、入职、考勤、培训、薪酬核算和日常管理。\n"
                    "提供驻场服务团队，建立投诉处理和应急响应机制。\n"
                    "每月提交人员考勤、社保和服务质量报告。\n\n"
                    "三、投标文件要求\n提交项目理解、人员配置、服务方案、质量保障和应急预案。\n"
                    "未按要求提供资格证明材料的，视为无效投标。"
                ),
            },
            {
                "id": "sample-security",
                "title": "医院保安服务项目（不匹配样本）",
                "category": "保安服务",
                "region": "河南省濮阳市",
                "noticeType": "Demo 风险识别样本",
                "source": "本地脱敏示例",
                "publishedDate": "2026-09-01",
                "servicePeriod": "12个月",
                "dataOrigin": "demo",
                "analysisText": "项目名称：某医院保安服务项目\n服务类型：保安服务\n服务地点：河南省濮阳市\n投标截止时间：2026-09-20 09:00\n资格要求：提供保安服务许可证；近三年承担过不少于2个医院保安服务项目。",
            },
        ]
        self.persist_catalog()

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
        return self._with_preview(item)

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
        monitoring = ensure_config(self.config)["monitoring"]
        items = filter_catalog_by_monitoring(self.catalog, monitoring)
        manual_payload = {"query": query, "region": region, "type": type}
        if current_only:
            items = [item for item in items if item.get("publishedDate", "") >= date.today().isoformat()]
        return [self._with_preview(item) for item in items if matches_manual_search(item, manual_payload)]

    @staticmethod
    def _with_preview(item: dict[str, Any]) -> dict[str, Any]:
        result = dict(item)
        text = str(result.get("analysisText", ""))
        result.setdefault("status", "未处理")
        result.setdefault("followUp", False)
        result.setdefault("read", False)
        result.setdefault("reminderStatus", "未提醒")
        result.setdefault("notificationChannels", [])
        result["analysisPreview"] = text[:240]
        return result

    def get_project(self, project_id: str) -> dict[str, Any] | None:
        item = next((item for item in self.catalog if item.get("id") == project_id), None)
        return self._with_preview(item) if item else None

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
        return self._with_preview(item)

    def set_follow_up(self, project_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        item = next((item for item in self.catalog if item.get("id") == project_id), None)
        if not item:
            return None
        item["followUp"] = payload.get("followUp", True)
        item["followUpStatus"] = payload.get("followUpStatus", "")
        self.persist_catalog()
        return self._with_preview(item)

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
        return {"item": self._with_preview(item), "download": {"status": "已下载", "fileName": target.name, "url": item["downloadUrl"]}}

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
            "projectName": match(r"项目名称[:：]\s*([^\r\n]+)"),
            "projectCode": match(r"项目编号[:：]\s*([^\r\n]+)"),
            "tendererName": match(r"招标人[:：]\s*([^\r\n]+)"),
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
        for key, value in [("劳务派遣", "劳务派遣"), ("保安", "保安服务"), ("保洁", "保洁服务"), ("家政", "家政服务")]:
            if key in text:
                return value
        return "人力资源服务"

    @staticmethod
    def required_count(text: str) -> int:
        result = re.search(r"(?:不少于|至少|不低于)\s*(\d+)\s*个", text)
        return int(result.group(1)) if result else 0

    def import_document(self, name: str, content: bytes, mime_type: str) -> dict[str, Any]:
        if len(content) > 20 * 1024 * 1024:
            raise ValueError("招标文件需小于20MB")
        text = self.extract_text_from_file(name, content, mime_type).strip()
        if len(text) < 20:
            raise ValueError("未能从该文件提取到足够文字，请上传可复制文字的 TXT / Markdown / PDF / DOCX 文件")
        project_id = uid("local")
        source_dir = self.runtime_root / "projects" / project_id / "source"
        source_dir.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", name)[:120]
        target = source_dir / f"{project_id}-{safe_name}"
        target.write_bytes(content)
        parsed = self.parse_tender(text, "本地上传")
        item = {
            "id": project_id,
            "title": parsed["projectName"] if parsed["projectName"] != "未识别" else Path(name).stem,
            "category": parsed["serviceType"],
            "region": parsed["serviceLocation"],
            "noticeType": "本地上传招标文件",
            "source": "本地上传",
            "publishedDate": date.today().isoformat(),
            "dataOrigin": "local_upload",
            "analysisText": text,
            "parsed": parsed,
            "downloadStatus": "已下载",
            "downloadUrl": f"/runtime/projects/{project_id}/source/{target.name}",
        }
        self.catalog.insert(0, item)
        self.persist_catalog()
        return {"item": self._with_preview(item), "file": {"name": name, "parseStatus": "解析完成", "extractedTextLength": len(text)}}

    def extract_text_from_file(self, name: str, content: bytes, mime_type: str = "") -> str:
        lower = name.lower()
        if lower.endswith(".docx") or "wordprocessingml" in mime_type:
            return self.extract_text_from_docx(content)
        if lower.endswith(".pdf") or "pdf" in mime_type.lower():
            return self.extract_text_from_pdf(content)
        return content.decode("utf-8", errors="ignore")

    @staticmethod
    def extract_text_from_docx(content: bytes) -> str:
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(content)) as archive:
            try:
                xml = archive.read("word/document.xml")
            except KeyError:
                return ""
        root = ET.fromstring(xml)
        namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        lines: list[str] = []
        for paragraph in root.findall(".//w:p", namespace):
            parts = [node.text for node in paragraph.findall(".//w:t", namespace) if node.text]
            if parts:
                lines.append("".join(parts))
        return "\n".join(lines)

    @staticmethod
    def extract_text_from_pdf(content: bytes) -> str:
        try:
            from pypdf import PdfReader
        except Exception:
            return ""
        from io import BytesIO

        try:
            reader = PdfReader(BytesIO(content))
        except Exception:
            return ""
        pages = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")
        return "\n".join(pages)

    def match_project(self, item: dict[str, Any]) -> dict[str, Any]:
        text = str(item.get("analysisText", ""))
        parsed = item.get("parsed") or self.parse_tender(text, "项目文件")
        requirements = parsed.get("requirements", {})
        qualifications = self.company.get("qualifications", [])
        rows = []
        for requirement in requirements.get("qualifications", []):
            found = requirement in qualifications
            rows.append({"kind": "资质", "requirement": requirement, "status": "满足" if found else "待补材料", "evidence": "公司资料库" if found else "企业材料库未找到对应依据", "risk": "低" if found else "高"})
        performance = (requirements.get("performances") or [None])[0]
        if performance:
            matching = [entry for entry in self.company.get("performances", []) if entry.get("type") in {parsed.get("serviceType"), "人力资源服务"}]
            required = performance.get("required_count", 0)
            ok = not required or len(matching) >= required
            rows.append({"kind": "业绩", "requirement": performance.get("text", ""), "status": "满足" if ok else "部分满足", "evidence": f"匹配到 {len(matching)} 个相关业绩，要求 {required or '未识别'} 个", "risk": "低" if ok else "高"})
        personnel = (requirements.get("personnel") or [None])[0]
        if personnel:
            role = personnel.get("role", "项目人员")
            found = next((entry for entry in self.company.get("personnel", []) if entry.get("role") == role), None)
            rows.append({"kind": "人员", "requirement": personnel.get("text", ""), "status": "满足" if found else "待补材料", "evidence": f"{role} {found.get('count')}名" if found else "企业材料库未找到对应人员", "risk": "低" if found else "中"})
        location = parsed.get("serviceLocation", "")
        region_ok = any(region in location for region in self.company.get("service_regions", []))
        rows.append({"kind": "地域", "requirement": location, "status": "满足" if region_ok else "待澄清", "evidence": "企业服务区域：" + "、".join(self.company.get("service_regions", [])), "risk": "低" if region_ok else "中"})
        score = round(sum(20 if row["status"] == "满足" else 4 for row in rows))
        score = min(score, 100)
        thresholds = self.config.get("decisionThresholds", {"recommend": 78, "review": 58})
        decision = "建议参与" if score >= thresholds.get("recommend", 78) else "人工评估" if score >= thresholds.get("review", 58) else "不建议参与"
        risks = [{"level": row["risk"], "title": f"{row['kind']}需要关注", "detail": row["evidence"]} for row in rows if row["risk"] != "低"]
        if not risks:
            risks = [{"level": "低", "title": "暂未发现明显高风险", "detail": "仍需人工核验原始文件和附件。"}]
        return {"rows": rows, "risks": risks, "score": score, "decision": decision, "project": parsed}

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
        return {"item": self._with_preview(item), "workspace": self.workspaces[workspace_id], "reasoning": reasoning}

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
        ai = self.config.get("ai", {})
        if ai.get("enabled") is False:
            return ""
        base_url = str(ai.get("baseUrl") or os.getenv("AI_BASE_URL", "")).strip()
        api_key = str(ai.get("apiKey") or os.getenv("AI_API_KEY", "")).strip()
        model = str(ai.get("model") or os.getenv("AI_MODEL", "")).strip()
        if not base_url or not api_key or not model:
            return ""
        system_prompt = (
            "你是企业内部招投标智能体。"
            "请用简洁中文回答，优先引用当前项目、资料库、匹配结果和风险结论。"
            "如果信息不足，请直接说明需要补充哪些材料。"
        )
        if project:
            system_prompt += f" 当前项目名称：{project.get('title', '')}。"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            "temperature": float(ai.get("temperature", 0.2) or 0.2),
        }
        req = request.Request(
            f"{base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
            return str(body["choices"][0]["message"]["content"]).strip()
        except (OSError, ValueError, KeyError, error.URLError):
            return ""

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
