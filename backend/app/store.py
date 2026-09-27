from __future__ import annotations

import os
import hashlib
import json
import re
import time
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from .config.service import ensure_config, public_configuration
from .runtime_lock import runtime_file_lock
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
        self._workspace_snapshot: dict[str, dict[str, Any]] = {}

    def initialize(self) -> None:
        # 企业数据单一来源：M3 图库（本地不再落 company-materials.json，只保留证明材料文件）。
        self.catalog = read_json(self.catalog_path, [])
        self.config = ensure_config(read_json(self.config_path, self.default_config()))
        self.libraries = read_json(self.libraries_path, self.default_libraries())
        self.reminders = read_json(self.runtime_root / "reminders.json", [])
        self.runs = read_json(self.runtime_root / "automation-runs.json", [])
        self.workspaces = read_json(self.workspaces_path, {})
        self._workspace_snapshot = deepcopy(self.workspaces)
        # 兼容旧版本工作区：补齐 fineReview 缺失字段，避免前端渲染崩溃。
        repaired_bid_documents = False
        for workspace in self.workspaces.values():
            reasoning = workspace.get("reasoning") if isinstance(workspace, dict) else None
            fine_review = None
            if not isinstance(reasoning, dict):
                fine_review = None
            else:
                fine_review = reasoning.get("fineReview")
                if isinstance(fine_review, dict):
                    fine_review.setdefault("hardBlockers", [])
                    fine_review.setdefault("rejectionRisks", [])
                    fine_review.setdefault("unresolvedMandatory", [])
            bid_document = workspace.get("bidDocument") if isinstance(workspace, dict) else None
            if isinstance(bid_document, dict):
                if self._migrate_bid_document_sources(bid_document, reasoning):
                    repaired_bid_documents = True
                if self._backfill_bid_document_coverage(bid_document, fine_review):
                    repaired_bid_documents = True
        self._refresh_company_from_m3()
        catalog_repaired = False
        for item in self.catalog:
            catalog_repaired = repair_notice_fields(item) or catalog_repaired
        if catalog_repaired:
            self.persist_catalog()
        if repaired_bid_documents:
            self.persist_runtime()
        write_json(self.config_path, self.config)

    @staticmethod
    def _migrate_bid_document_sources(
        bid_document: dict[str, Any],
        reasoning: dict[str, Any] | None = None,
    ) -> bool:
        """补齐解析来源与目录规划来源，修复旧版 Pi 标识混用问题。"""

        changed = False
        legacy_source = str(bid_document.get("planSource") or "").strip()
        planning_source = str(bid_document.get("planningSource") or "").strip()
        planning_status = str(bid_document.get("planningStatus") or "").strip()
        agent_results = bid_document.get("agentResults")
        has_planning_trace = isinstance(agent_results, list) and bool(agent_results)

        if not planning_source:
            if has_planning_trace and legacy_source in {
                "pi_agent",
                "cloud_agent",
                "tender_document",
                "builtin_template",
            }:
                planning_source = legacy_source
                planning_status = planning_status or "legacy_existing"
            elif legacy_source in {"pi_agent", "cloud_agent"}:
                # 旧版物化阶段把解析提供方误写进 planSource。没有目录编排
                # agentResults 时，只能确认它是解析结果，不能确认目录已规划。
                planning_source = "unplanned"
                planning_status = planning_status or "pending"
            elif legacy_source:
                planning_source = legacy_source
                planning_status = planning_status or "legacy_existing"
            else:
                planning_source = "unplanned"
                planning_status = planning_status or "pending"
            bid_document["planningSource"] = planning_source
            changed = True

        if not planning_status:
            planning_status = "pending" if planning_source == "unplanned" else "legacy_existing"
            bid_document["planningStatus"] = planning_status
            changed = True

        if planning_source == "unplanned" and not str(
            bid_document.get("planningSourceDetail") or ""
        ).strip():
            bid_document["planningSourceDetail"] = "解析已完成，尚未调用目录规划智能体"
            changed = True

        parse_provider = str(bid_document.get("parseProvider") or "").strip()
        if not parse_provider and isinstance(reasoning, dict):
            parse_provider = str(
                reasoning.get("parseBundleProvider")
                or reasoning.get("cloudParseProvider")
                or ""
            ).strip().lower()
        if not parse_provider and legacy_source in {"pi_agent", "cloud_agent"}:
            parse_provider = "pi" if legacy_source == "pi_agent" else "dify"
        if parse_provider and parse_provider != bid_document.get("parseProvider"):
            bid_document["parseProvider"] = parse_provider
            changed = True

        if (
            parse_provider
            and not str(bid_document.get("parseSourceDetail") or "").strip()
        ):
            detail = (
                "Pi 本地智能体解析结果"
                if parse_provider == "pi"
                else "Dify Cloud Workflow 解析结果"
                if parse_provider == "dify"
                else f"{parse_provider} 解析结果"
            )
            bid_document["parseSourceDetail"] = detail
            changed = True

        plan = bid_document.get("plan")
        if isinstance(plan, list):
            from .bid_writing.schemas import (
                material_keys_for_writing_profile,
                resolve_writing_profile,
            )

            for section in plan:
                if not isinstance(section, dict):
                    continue
                profile = resolve_writing_profile(section)
                scopes = list(material_keys_for_writing_profile(profile))
                if section.get("writingProfile") != profile:
                    section["writingProfile"] = profile
                    changed = True
                if section.get("materialScopes") != scopes:
                    section["materialScopes"] = scopes
                    changed = True

        return changed

    @staticmethod
    def _backfill_bid_document_coverage(
        bid_document: dict[str, Any],
        fine_review: dict[str, Any] | None = None,
    ) -> bool:
        """从已有 Pi 目录章节回填丢失的覆盖关系。

        仅处理覆盖数组为空、但章节仍保留 requirementIds 的旧持久化数据。
        这是确定性修复，不调用模型，也不改变章节正文或章节顺序。
        """
        if str(bid_document.get("planSource") or "") != "pi_agent":
            return False
        # 新版目录规划明确把全量条款映射延后到正文阶段。
        # 只有没有该状态字段的旧持久化数据才允许走兼容回填。
        if "requirementMappingStatus" in bid_document:
            return False
        existing = bid_document.get("requirementCoverage")
        if isinstance(existing, list) and existing:
            return False
        plan = bid_document.get("plan")
        if not isinstance(plan, list):
            return False

        coverage: list[dict[str, Any]] = []
        for section in plan:
            if not isinstance(section, dict):
                continue
            section_id = str(section.get("sectionId") or "").strip()
            if not section_id:
                continue
            source = str(section.get("source") or "").strip()
            kind = str(section.get("kind") or "").strip()
            coverage_type = (
                "fixed_form"
                if source in {"template", "copied_template"}
                else "table"
                if source in {"table", "deviation"} or kind == "deviation"
                else "attachment"
                if source == "attachment" or kind == "attachment"
                else "chapter_text"
            )
            for raw_requirement_id in section.get("requirementIds") or []:
                requirement_id = str(raw_requirement_id or "").strip()
                if not requirement_id:
                    continue
                coverage.append(
                    {
                        "requirementId": requirement_id,
                        "sectionId": section_id,
                        "coverageType": coverage_type,
                        "status": "planned",
                        "reason": (
                            "原目录已保留章节 requirementIds，"
                            "但持久化的 RequirementMapAgent 覆盖数组为空，"
                            "本次由后端确定性回填。"
                        ),
                    }
                )
        rows = fine_review.get("rows") if isinstance(fine_review, dict) else []
        covered_requirement_ids = {
            str(item.get("requirementId") or "").strip()
            for item in coverage
            if isinstance(item, dict) and str(item.get("requirementId") or "").strip()
        }
        section_lookup = {
            str(section.get("sectionId") or "").strip(): section
            for section in plan
            if isinstance(section, dict)
            and str(section.get("sectionId") or "").strip()
        }

        def pick_fallback_section(row: dict[str, Any]) -> tuple[str, str]:
            requirement = str(row.get("requirement") or row.get("rawText") or "")
            category = str(row.get("category") or "")
            candidates: list[str] = []
            if "中小企业" in requirement:
                candidates = ["o-01", "c-08"]
            elif "基本资格" in requirement or "承诺函" in requirement:
                candidates = ["c-02", "c-08"]
            elif "授权" in requirement or "委托书" in requirement:
                candidates = ["c-04", "c-03", "c-08"]
            elif "法定代表人" in requirement or "身份证明" in requirement:
                candidates = ["c-03", "c-04", "c-08"]
            elif "保证金" in requirement:
                candidates = ["c-05", "c-01", "p-01"]
            elif category == "qualification":
                candidates = ["c-08", "c-02", "c-01"]
            elif category in {"commercial", "pricing", "contract", "delivery"}:
                candidates = ["d-02", "c-01"]
            else:
                candidates = ["d-01", "d-02", "c-08"]
            for section_id in candidates:
                section = section_lookup.get(section_id)
                if not isinstance(section, dict):
                    continue
                source = str(section.get("source") or "").strip()
                kind = str(section.get("kind") or "").strip()
                coverage_type = (
                    "fixed_form"
                    if source in {"template", "copied_template"}
                    else "table"
                    if source in {"table", "deviation"} or kind == "deviation"
                    else "attachment"
                    if source == "attachment" or kind == "attachment"
                    else "chapter_text"
                )
                return section_id, coverage_type
            return "", ""

        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            requirement_id = str(row.get("requirementId") or row.get("id") or "").strip()
            if not requirement_id or requirement_id in covered_requirement_ids:
                continue
            section_id, coverage_type = pick_fallback_section(row)
            if not section_id:
                continue
            covered_requirement_ids.add(requirement_id)
            coverage.append(
                {
                    "requirementId": requirement_id,
                    "sectionId": section_id,
                    "coverageType": coverage_type,
                    "status": "planned",
                    "reason": (
                        "原 RequirementMapAgent 覆盖数组缺失该要求，"
                        "后端按要求类别和固定目录章节确定性补齐。"
                    ),
                }
            )

        if not coverage:
            return False
        bid_document["requirementCoverage"] = coverage
        warnings = bid_document.get("planningWarnings")
        if not isinstance(warnings, list):
            warnings = []
        warning = "已从 Pi 目录章节 requirementIds 回填缺失的 requirementCoverage。"
        if warning not in warnings:
            warnings.append(warning)
        bid_document["planningWarnings"] = warnings
        return True

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
        lock_path = self.runtime_root / ".store-runtime.lock"
        with runtime_file_lock(lock_path):
            disk_workspaces = read_json(self.workspaces_path, {})
            if not isinstance(disk_workspaces, dict):
                disk_workspaces = {}
            merged_workspaces = deepcopy(disk_workspaces)
            # Only overwrite a disk record when this process changed it since
            # initialization/last merge. This prevents an older backend
            # process from erasing a workspace materialized by a newer one.
            for workspace_id, workspace in self.workspaces.items():
                previous = self._workspace_snapshot.get(workspace_id)
                if (
                    workspace_id not in merged_workspaces
                    or workspace_id not in self._workspace_snapshot
                    or workspace != previous
                ):
                    merged_workspaces[workspace_id] = deepcopy(workspace)
            write_json(self.runtime_root / "reminders.json", self.reminders[-100:])
            write_json(self.runtime_root / "automation-runs.json", self.runs[-50:])
            write_json(self.workspaces_path, merged_workspaces, compact=True)
            self.workspaces = merged_workspaces
            self._workspace_snapshot = deepcopy(merged_workspaces)

    def _refresh_workspaces_from_disk(self) -> None:
        """Merge external process changes without losing local dirty records."""
        lock_path = self.runtime_root / ".store-runtime.lock"
        with runtime_file_lock(lock_path):
            disk_workspaces = read_json(self.workspaces_path, {})
            if not isinstance(disk_workspaces, dict):
                return
            merged_workspaces = deepcopy(disk_workspaces)
            for workspace_id, workspace in self.workspaces.items():
                previous = self._workspace_snapshot.get(workspace_id)
                if (
                    workspace_id not in merged_workspaces
                    or workspace_id not in self._workspace_snapshot
                    or workspace != previous
                ):
                    merged_workspaces[workspace_id] = deepcopy(workspace)
            self.workspaces = merged_workspaces
            self._workspace_snapshot = deepcopy(merged_workspaces)

    def save_parse_bundle(self, value: dict[str, Any]) -> dict[str, Any]:
        """校验并保存标准 ParseBundle，同时返回规范化后的 JSON 数据。"""
        from .agent_runtime.contracts import ParseBundle

        bundle = ParseBundle.model_validate(value)
        project_id = str(bundle.projectId or "unassigned")
        safe_project_id = re.sub(r"[^A-Za-z0-9_.-]", "_", project_id) or "unassigned"
        target = self.runtime_root / "projects" / safe_project_id / "parse" / "parse-bundle.json"
        artifact_path = str(target.relative_to(self.root)).replace("\\", "/")
        artifacts = list(bundle.artifacts)
        if not any(
            isinstance(item, dict) and item.get("type") == "parse_bundle"
            for item in artifacts
        ):
            artifacts.append(
                {
                    "artifactId": bundle.parseBundleId,
                    "type": "parse_bundle",
                    "name": "parse-bundle.json",
                    "storagePath": artifact_path,
                }
            )
        bundle = bundle.model_copy(
            update={
                "artifactPath": artifact_path,
                "updatedAt": now_iso(),
                "artifacts": artifacts,
            }
        )
        payload = bundle.model_dump(mode="json")
        write_json(target, payload)
        return payload

    def get_parse_bundle(self, workspace_id: str) -> dict[str, Any] | None:
        """按工作区读取标准解析包，并兼容迁移旧版工作区。"""
        from .agent_runtime.contracts import ParseBundle
        from .agent_runtime.submission_structure import (
            is_reliable_submission_structure,
            merge_submission_structures,
            normalize_submission_structure,
            parse_submission_format_tree,
        )

        workspace = self.workspaces.get(workspace_id)
        if not isinstance(workspace, dict):
            return None
        ref = workspace.get("parseBundleRef")
        artifact = str(ref.get("artifact") or "") if isinstance(ref, dict) else ""
        if not artifact:
            project_id = str(workspace.get("projectId") or "unassigned")
            safe_project_id = re.sub(r"[^A-Za-z0-9_.-]", "_", project_id) or "unassigned"
            target = self.runtime_root / "projects" / safe_project_id / "parse" / "parse-bundle.json"
        else:
            target = self.root / artifact if artifact.startswith("runtime/") else self.root / artifact
        value = read_json(target, None)
        if not isinstance(value, dict):
            value = self._build_legacy_parse_bundle(workspace_id, workspace)
            if not value:
                return None
            value = self.save_parse_bundle(value)
            workspace["parseBundleRef"] = {
                "parseBundleId": value["parseBundleId"],
                "schemaVersion": value["schemaVersion"],
                "provider": value["provider"],
                "artifact": value["artifactPath"],
            }
            reasoning = workspace.get("reasoning")
            if isinstance(reasoning, dict):
                reasoning["parseBundleId"] = value["parseBundleId"]
                reasoning["parseBundleProvider"] = value["provider"]
            self.persist_runtime()
        normalized = ParseBundle.model_validate(value).model_dump(mode="json")
        # 历史工作区可能已经把资格审查正文误存成 submissionStructure。
        # 读取时用当前文档正文重新校验一次，可靠的“电子投标文件组成”结构
        # 可以自动修复历史缓存，避免用户必须重新上传并解析文件。
        provided_structure = normalize_submission_structure(
            normalized.get("submissionStructure")
        )
        document_blocks = normalized.get("documentBlocks")
        document_text = (
            str(document_blocks.get("text") or "")
            if isinstance(document_blocks, dict)
            else ""
        )
        derived_structure = parse_submission_format_tree(document_text)
        if is_reliable_submission_structure(derived_structure):
            repaired_structure = merge_submission_structures(
                derived_structure,
                provided_structure,
            )
        else:
            repaired_structure = provided_structure
        if repaired_structure != normalized.get("submissionStructure"):
            normalized["submissionStructure"] = repaired_structure
            value = self.save_parse_bundle(normalized)
            normalized = ParseBundle.model_validate(value).model_dump(mode="json")
        current_fragments = (
            normalized.get("templateFragments")
            if isinstance(normalized.get("templateFragments"), list)
            else []
        )
        source_file = normalized.get("sourceFile")
        source_storage_path = (
            str(source_file.get("storagePath") or "").strip()
            if isinstance(source_file, dict)
            else ""
        )
        source_path = self.root / source_storage_path if source_storage_path else None
        if not (source_path and source_path.is_file()):
            project_id = str(normalized.get("projectId") or "").strip()
            source_dir = self.runtime_root / "projects" / project_id / "source"
            docx_candidates = (
                sorted(source_dir.glob("*.docx"))
                if project_id and source_dir.is_dir()
                else []
            )
            if len(docx_candidates) == 1:
                source_path = docx_candidates[0]
            else:
                source_path = None
        fragments_have_content = any(
            isinstance(fragment, dict)
            and (
                str(fragment.get("templateText") or "").strip()
                or fragment.get("bindingStatus") == "ready"
                or bool(fragment.get("blocks"))
                or bool(fragment.get("tables"))
            )
            for fragment in current_fragments
        )
        fragments_can_copy_word = any(
            isinstance(fragment, dict)
            and fragment.get("copyMode") == "ooxml_body_range"
            for fragment in current_fragments
        )
        fragments_need_field_refresh = any(
            isinstance(fragment, dict)
            and str(fragment.get("fragmentType") or fragment.get("templateType") or "")
            in {"fixed_template", "fixed_form"}
            and (
                (
                    "填写" in str(fragment.get("templateText") or "")
                    and not fragment.get("editableFields")
                )
                or (
                    "填写" in str(fragment.get("templateText") or "")
                    and "投标人" in str(fragment.get("templateText") or "")
                    and "companyName"
                    not in {
                        str(field.get("sourceKey") or "")
                        for field in (
                            fragment.get("editableFields")
                            if isinstance(fragment.get("editableFields"), list)
                            else []
                        )
                        if isinstance(field, dict)
                    }
                )
            )
            for fragment in current_fragments
        )
        if (
            not current_fragments
            or not fragments_have_content
            or (source_path and source_path.is_file() and not fragments_can_copy_word)
            or fragments_need_field_refresh
        ):
            from .bid_writing.template_fragments import attach_template_fragments_to_bundle
            rebuilt = attach_template_fragments_to_bundle(
                normalized,
                root=self.root,
                source_path=source_path if source_path and source_path.is_file() else None,
            )
            if rebuilt.get("templateFragments"):
                value = self.save_parse_bundle(rebuilt)
                normalized = ParseBundle.model_validate(value).model_dump(mode="json")
        template_fragments = (
            normalized.get("templateFragments")
            if isinstance(normalized.get("templateFragments"), list)
            else []
        )
        bid_document = workspace.get("bidDocument") if isinstance(workspace.get("bidDocument"), dict) else {}
        if isinstance(bid_document, dict) and isinstance(bid_document.get("plan"), list):
            from .bid_writing.template_fragments import (
                attach_template_fragments_to_plan,
                template_fragment_summaries,
            )

            updated_plan = attach_template_fragments_to_plan(
                bid_document.get("plan") or [],
                template_fragments,
            )
            if updated_plan != bid_document.get("plan"):
                bid_document["plan"] = updated_plan
                bid_document["templateFragments"] = template_fragment_summaries(template_fragments)
                bid_document["templateFragmentsArtifact"] = str(
                    normalized.get("templateFragmentsArtifact") or ""
                )
                workspace["bidDocument"] = bid_document
                self.persist_runtime()
        expected_ref = {
            "parseBundleId": normalized["parseBundleId"],
            "schemaVersion": normalized["schemaVersion"],
            "provider": normalized["provider"],
            "artifact": normalized.get("artifactPath") or str(target.relative_to(self.root)).replace("\\", "/"),
        }
        current_ref = workspace.get("parseBundleRef")
        if not isinstance(current_ref, dict) or any(
            current_ref.get(key) != expected_ref.get(key)
            for key in expected_ref
        ):
            workspace["parseBundleRef"] = expected_ref
            reasoning = workspace.get("reasoning")
            if isinstance(reasoning, dict):
                reasoning["parseBundleId"] = normalized["parseBundleId"]
                reasoning["parseBundleProvider"] = normalized["provider"]
            self.persist_runtime()
        return normalized

    def _build_legacy_parse_bundle(
        self,
        workspace_id: str,
        workspace: dict[str, Any],
    ) -> dict[str, Any] | None:
        """将旧版工作区中的解析字段一次性迁移为标准 ParseBundle。

        旧版工作区没有独立 parse-bundle.json，但通常仍保留原始文档结构、
        fineReview、评分项和全文。这里仅做确定性搬运，不声称这些结果来自
        Pi；provider 使用 legacy_local，便于前端和审计区分兼容数据。
        """
        project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}
        document = project.get("document") if isinstance(project.get("document"), dict) else {}
        reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
        fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
        parsed = project.get("parsed") if isinstance(project.get("parsed"), dict) else {}
        cloud_parse = reasoning.get("cloudParse") if isinstance(reasoning.get("cloudParse"), dict) else {}

        requirements = [
            dict(item)
            for item in (fine_review.get("rows") or parsed.get("requirements") or [])
            if isinstance(item, dict)
        ]
        evaluation = reasoning.get("evaluation") if isinstance(reasoning.get("evaluation"), dict) else {}
        evaluation_items = [
            dict(item)
            for item in (evaluation.get("items") or cloud_parse.get("evaluationItems") or [])
            if isinstance(item, dict)
        ]
        sections = document.get("sections") if isinstance(document.get("sections"), list) else []
        tables = document.get("tables") if isinstance(document.get("tables"), list) else []
        analysis_text = str(
            project.get("analysisText")
            or parsed.get("rawText")
            or document.get("text")
            or ""
        )
        filename = str(document.get("name") or "legacy-tender-document")
        project_id = str(workspace.get("projectId") or project.get("id") or "")
        download_url = str(project.get("downloadUrl") or "").strip()
        download_storage_path = (
            download_url.lstrip("/")
            if download_url.startswith("/runtime/")
            else ""
        )
        source_storage_path = str(
            document.get("storagePath")
            or project.get("sourceStoragePath")
            or download_storage_path
            or ""
        ).strip()
        if not evaluation_items:
            from .bid_writing.eval_parser import parse_eval_items

            evaluation_items = parse_eval_items(sections, tables).get("items", [])
        evaluation_items = [
            {
                **item,
                "id": str(item.get("id") or item.get("evaluationId") or f"eval-{index:03d}"),
                "evaluationId": str(item.get("evaluationId") or item.get("id") or f"eval-{index:03d}"),
            }
            for index, item in enumerate(evaluation_items, start=1)
            if isinstance(item, dict)
        ]
        submission_structure = (
            cloud_parse.get("submissionStructure")
            if isinstance(cloud_parse.get("submissionStructure"), dict)
            else {}
        )
        if not submission_structure and analysis_text:
            from .agent_runtime.submission_structure import parse_submission_format_tree

            submission_structure = parse_submission_format_tree(analysis_text)
        provider = (
            "matching_local"
            if str(document.get("kind") or "") == "tender_document"
            else "legacy_local"
        )
        return {
            "parseBundleId": f"parse-legacy-{workspace_id}",
            "schemaVersion": "1.0",
            "provider": provider,
            "status": "compatibility",
            "projectId": project_id,
            "workspaceId": workspace_id,
            "sourceFile": {
                "fileId": str(document.get("id") or ""),
                "filename": filename,
                "fileType": str(document.get("mimeType") or "").split("/")[-1],
                "mimeType": str(document.get("mimeType") or ""),
                "storagePath": source_storage_path,
            },
            "documentBlocks": {
                "text": analysis_text,
                "segments": document.get("segments") or [],
                "units": document.get("units") or [],
                "tables": tables,
                "sections": sections,
                "warnings": document.get("warnings") or [],
            },
            "documentStructure": {
                "sections": sections,
                "warnings": document.get("structureWarnings") or [],
            },
            "projectMeta": {
                "projectName": parsed.get("projectName") or project.get("title") or "",
                "projectCode": parsed.get("projectCode") or project.get("projectCode") or "",
                "tendererName": parsed.get("tendererName") or project.get("tendererName") or "",
                "agencyName": parsed.get("agencyName") or project.get("agencyName") or "",
                "budget": parsed.get("budgetAmount") or project.get("budget") or "",
                "deadline": parsed.get("bidDeadline") or project.get("bidDeadline") or "",
                "servicePeriod": parsed.get("servicePeriod") or project.get("servicePeriod") or "",
                "serviceLocation": parsed.get("serviceLocation") or project.get("region") or "",
            },
            "requirements": requirements,
            "evaluationItems": evaluation_items,
            "bidFormatSpec": cloud_parse.get("bidFormatSpec") if isinstance(cloud_parse.get("bidFormatSpec"), dict) else {},
            "submissionStructure": submission_structure,
            "templateFragments": cloud_parse.get("templateFragments") if isinstance(cloud_parse.get("templateFragments"), list) else [],
            "templateFragmentsArtifact": str(cloud_parse.get("templateFragmentsArtifact") or ""),
            "rawOutline": cloud_parse.get("outline") if isinstance(cloud_parse.get("outline"), list) else [],
            "riskItems": (
                cloud_parse.get("riskItems")
                if isinstance(cloud_parse.get("riskItems"), list)
                else fine_review.get("risks") or []
            ),
            "parseReviewReport": {
                "status": "compatibility",
                "warnings": [
                    "该工作区由旧版流程创建，ParseBundle 由现有本地解析产物兼容迁移生成。",
                    "兼容迁移没有重新调用 Pi 解析智能体。",
                ],
            },
            "warnings": [
                "旧版工作区缺少独立 ParseBundle，已按现有文档结构和 fineReview 生成兼容解析包。",
            ],
            "agentResults": [],
            "rawProviderResult": cloud_parse,
        }

    def prepare_matching_workspace_for_writing(
        self,
        workspace_id: str,
    ) -> dict[str, Any] | None:
        """Expose an existing detailed matching analysis as a writing workspace.

        This only normalizes persisted parser and review results. It never reads
        or reparses the uploaded source file and never invokes a parse agent.
        """

        self._refresh_workspaces_from_disk()
        workspace = self.workspaces.get(workspace_id)
        if not isinstance(workspace, dict):
            return None
        project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}
        document = project.get("document") if isinstance(project.get("document"), dict) else {}
        reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
        fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
        if document.get("kind") != "tender_document" or not fine_review.get("rows"):
            return None

        parse_bundle = self.get_parse_bundle(workspace_id)
        if not isinstance(parse_bundle, dict):
            return None
        self.persist_runtime()
        return {
            "workspace": workspace,
            "parseBundle": parse_bundle,
            "parseSource": str(parse_bundle.get("provider") or "matching_local"),
        }

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
        from .cloud_agent.config import DifyConfig
        from .pi_agent.config import PiConfig
        from .tender_monitor.service import monitoring_status

        self._refresh_workspaces_from_disk()
        runtime_config = DifyConfig.from_env()
        pi_config = PiConfig.from_env()
        # 每次首屏从 M3 刷新画像与计数（best-effort），保证导入后徽标/匹配数据即时更新。
        self._refresh_company_from_m3()

        return {
            "company": self.company,
            "ontology": {"name": "tenderhub", "version": "V0.4", "classes": 8, "relations": 8},
            "materialsCounts": self._materials_counts(),
            "config": self.public_config(),
            "agentRuntime": {
                "provider": runtime_config.provider,
                "piEnabled": pi_config.enabled,
                "piProvider": pi_config.provider,
                "piModel": pi_config.model,
                "backendPid": os.getpid(),
            },
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
                "storagePath": str(target.relative_to(self.root)).replace("\\", "/"),
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

    def materialize_cloud_parse(self, run: dict[str, Any]) -> dict[str, Any] | None:
        """把云端智能体解析结果接入现有项目、匹配和标书撰写数据模型。"""
        self._refresh_workspaces_from_disk()
        result = run.get("result") if isinstance(run.get("result"), dict) else None
        if not result:
            return None

        requested_project_id = str(run.get("projectId") or "")
        requested_workspace_id = str(run.get("workspaceId") or "")
        previous_workspace = self.workspaces.get(requested_workspace_id)

        project_id = requested_project_id or str(
            previous_workspace.get("projectId") if isinstance(previous_workspace, dict) else ""
        ) or uid("cloud")
        existing = next((entry for entry in self.catalog if entry.get("id") == project_id), None)
        if not existing and requested_workspace_id:
            existing = next(
                (entry for entry in self.catalog if entry.get("workspaceId") == requested_workspace_id),
                None,
            )
            if existing:
                project_id = str(existing.get("id") or project_id)

        workspace_id = requested_workspace_id or str(existing.get("workspaceId") if existing else "") or uid("workspace")

        from .cloud_agent.materializer import build_catalog_item, build_workspace
        from .agent_runtime.contracts import build_parse_bundle
        from .bid_writing.template_fragments import attach_template_fragments_to_bundle
        provider = str(run.get("provider") or "dify").strip().lower() or "unknown"

        project = build_catalog_item(
            run=run,
            result=result,
            project_id=project_id,
            workspace_id=workspace_id,
            root=self.root,
            existing=existing,
        )
        workspace = build_workspace(
            run=run,
            result=result,
            project=project,
            workspace_id=workspace_id,
            project_id=project_id,
            previous=previous_workspace,
        )
        parse_bundle = self.save_parse_bundle(
            attach_template_fragments_to_bundle(
                build_parse_bundle(
                    run=run,
                    result=result,
                    provider=provider,
                    project_id=project_id,
                    workspace_id=workspace_id,
                    root=self.root,
                    status="completed",
                ),
                root=self.root,
                source_path=(
                    self.root / str(run.get("inputArtifact") or "")
                    if str(run.get("inputArtifact") or "")
                    else None
                ),
            )
        )
        workspace["parseBundleRef"] = {
            "parseBundleId": parse_bundle["parseBundleId"],
            "schemaVersion": parse_bundle["schemaVersion"],
            "provider": parse_bundle["provider"],
            "artifact": parse_bundle["artifactPath"],
        }
        reasoning = workspace.get("reasoning")
        if isinstance(reasoning, dict):
            reasoning["parseBundleId"] = parse_bundle["parseBundleId"]
            reasoning["parseBundleProvider"] = parse_bundle["provider"]

        project_index = next((index for index, entry in enumerate(self.catalog) if entry.get("id") == project_id), -1)
        if project_index >= 0:
            self.catalog[project_index] = project
        else:
            self.catalog.insert(0, project)
        self.workspaces[workspace_id] = workspace
        self.persist_catalog()
        self.persist_runtime()
        return {"project": self.with_preview(project), "workspace": workspace}

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
        self._refresh_workspaces_from_disk()
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
        uncertainty_reason: str = "",
        response_value: str | None = None,
        material_bindings: list[dict[str, Any]] | None = None,
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
        updated = update_fine_review_status(
            fine_review,
            row_id,
            status,
            uncertainty_reason,
            response_value,
            material_bindings,
        )
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
        self._refresh_workspaces_from_disk()
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
        fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
        project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}
        document = project.get("document") if isinstance(project.get("document"), dict) else {}
        sections = document.get("sections") if isinstance(document.get("sections"), list) else []

        from .bid_writing.deviations import build_deviation_tables
        from .bid_writing.planning import (
            minimal_bid_skeleton,
            plan_from_outline,
        )
        from .bid_writing.volumes import plan_from_submission_structure
        from .bid_writing.schemas import build_bid_document
        from .bid_writing.outline import extract_response_outline
        from .bid_writing.template_reuse import attach_templates
        from .bid_writing.template_fragments import (
            attach_template_fragments_to_plan,
            template_fragment_summaries,
        )
        from .bid_writing.volumes import arrange_volumes
        from .agent_runtime.submission_structure import normalize_submission_structure

        bid_doc = build_bid_document(workspace_id, str(workspace.get("projectId", "")))
        parse_bundle = self.get_parse_bundle(workspace_id) or {}
        submission_structure = normalize_submission_structure(
            parse_bundle.get("submissionStructure")
            or (parse_bundle.get("bidFormatSpec") or {}).get("submissionStructure")
        )
        bid_doc["submissionStructure"] = submission_structure
        parse_provider = str(
            parse_bundle.get("provider")
            or reasoning.get("parseBundleProvider")
            or reasoning.get("cloudParseProvider")
            or ""
        ).strip().lower()
        if parse_provider:
            bid_doc["parseProvider"] = parse_provider
            bid_doc["parseSourceDetail"] = (
                "Pi 本地智能体解析结果"
                if parse_provider == "pi"
                else "Dify Cloud Workflow 解析结果"
                if parse_provider == "dify"
                else "复用匹配评估结果 · 本地解析"
                if parse_provider == "matching_local"
                else "历史项目兼容迁移的本地解析结果"
                if parse_provider == "legacy_local"
                else f"{parse_provider} 解析结果"
            )
        template_fragments = (
            parse_bundle.get("templateFragments")
            if isinstance(parse_bundle.get("templateFragments"), list)
            else []
        )
        ai = ensure_config(self.config).get("ai", {})

        pi_plan = self._plan_bid_document_with_pi(workspace_id, workspace, fine_review, project)
        pi_fallback_warnings = pi_plan.get("warnings", []) if pi_plan else []
        planning_provider = str((pi_plan or {}).get("provider") or "unknown").strip().lower()
        planning_status = str((pi_plan or {}).get("status") or "").strip()
        planning_fallback_reason = str((pi_plan or {}).get("fallbackReason") or "").strip()
        if pi_plan and pi_plan.get("plan"):
            planned_sections = attach_template_fragments_to_plan(
                pi_plan["plan"],
                template_fragments,
            )
            planned_sections, volume_warnings = arrange_volumes(
                planned_sections, submission_structure,
            )
            from .bid_writing.response_table import bind_response_table_requirements

            planned_sections = bind_response_table_requirements(planned_sections, fine_review)
            pi_fallback_warnings = [*pi_fallback_warnings, *volume_warnings]
            bid_doc["planSource"] = "pi_agent"
            bid_doc["planSourceDetail"] = "Pi 编排：VolumePlanAgent + OutlinePlanAgent"
            bid_doc["planningSource"] = "pi_agent"
            bid_doc["planningSourceDetail"] = "Pi 编排：VolumePlanAgent + OutlinePlanAgent"
            bid_doc["planningProvider"] = "pi"
            bid_doc["planningStatus"] = "success"
            bid_doc["planningModel"] = str((pi_plan or {}).get("model") or "")
            bid_doc["plan"] = planned_sections
            bid_doc["requirementCoverage"] = []
            bid_doc["uncoveredRequirements"] = []
            bid_doc["duplicatedRequirements"] = []
            bid_doc["requirementMappingStatus"] = "pending"
            bid_doc["requirementMapBatchCount"] = 0
            bid_doc["agentResults"] = pi_plan.get("agentResults", [])
            bid_doc["templateFragments"] = template_fragment_summaries(template_fragments)
            bid_doc["templateFragmentsArtifact"] = str(
                parse_bundle.get("templateFragmentsArtifact") or ""
            )
            bid_doc["deviationTables"] = build_deviation_tables(fine_review)
            bid_doc["status"] = "planned"
            if pi_fallback_warnings:
                bid_doc["planningWarnings"] = pi_fallback_warnings
            workspace["bidDocument"] = bid_doc
            self.persist_runtime()
            return {"bidDocument": bid_doc, "workspace": workspace}

        existing_bid_doc = workspace.get("bidDocument")
        existing_planning_source = (
            str(existing_bid_doc.get("planningSource") or "").strip()
            if isinstance(existing_bid_doc, dict)
            else ""
        )
        existing_plan = (
            existing_bid_doc.get("plan")
            if isinstance(existing_bid_doc, dict)
            and isinstance(existing_bid_doc.get("plan"), list)
            else []
        )
        has_non_deviation_draft = any(
            isinstance(section, dict)
            and str(section.get("kind") or "").strip() != "deviation"
            and str(section.get("source") or "").strip() != "deviation"
            for section in existing_plan
        )
        existing_plan_source = (
            str(existing_bid_doc.get("planSource") or "").strip()
            if isinstance(existing_bid_doc, dict)
            else ""
        )
        existing_is_parse_draft = existing_planning_source in {"", "unplanned"}
        can_reuse_existing_draft = (
            has_non_deviation_draft
            and not existing_is_parse_draft
        )
        if (
            isinstance(existing_bid_doc, dict)
            and existing_plan_source in {"cloud_agent", "pi_agent"}
            and existing_plan
            and can_reuse_existing_draft
        ):
            existing_bid_doc["status"] = "planned"
            planning_mismatch = (
                planning_status == "not_attempted"
                and planning_provider
                and (
                    (existing_plan_source == "pi_agent" and planning_provider != "pi")
                    or (existing_plan_source == "cloud_agent" and planning_provider != "dify")
                )
            )
            if planning_status in {"failed", "empty_response"} or planning_mismatch:
                existing_bid_doc["planningProvider"] = planning_provider or "unknown"
                existing_bid_doc["planningStatus"] = "fallback_existing_agent"
                existing_bid_doc["planningSource"] = (
                    "unplanned" if existing_is_parse_draft else existing_plan_source
                )
                existing_bid_doc["planningSourceDetail"] = (
                    "保留解析阶段的目录草案，尚未完成目录规划智能体调用"
                    if existing_is_parse_draft
                    else (
                        str(existing_bid_doc.get("planSourceDetail") or "").strip()
                        or "保留已有目录规划结果"
                    )
                )
                existing_bid_doc["planningFallbackReason"] = (
                    (
                        f"当前运行时 provider={planning_provider}，未执行 Pi 目录规划，"
                        "本次保留已有 Pi 目录。"
                        if planning_mismatch and existing_plan_source == "pi_agent"
                        else planning_fallback_reason
                    )
                    or "Pi 目录规划未完成，本次保留解析阶段已有的智能体目录。"
                )
                existing_bid_doc["planningWarnings"] = pi_fallback_warnings
            else:
                existing_bid_doc["planningProvider"] = (
                    planning_provider
                    or str(existing_bid_doc.get("planSource") or "cloud_agent")
                )
                existing_bid_doc["planningSource"] = (
                    existing_plan_source
                    or str(existing_bid_doc.get("planSource") or "cloud_agent")
                )
                existing_bid_doc["planningStatus"] = (
                    str(existing_bid_doc.get("planningStatus") or "").strip()
                    or "existing_plan"
                )
            existing_bid_doc["plan"] = attach_template_fragments_to_plan(
                existing_bid_doc.get("plan") or [],
                template_fragments,
            )
            existing_bid_doc["plan"], volume_warnings = arrange_volumes(
                existing_bid_doc["plan"], submission_structure,
            )
            from .bid_writing.response_table import bind_response_table_requirements

            existing_bid_doc["plan"] = bind_response_table_requirements(
                existing_bid_doc["plan"],
                fine_review,
            )
            existing_bid_doc["submissionStructure"] = submission_structure
            existing_bid_doc["planningWarnings"] = list(dict.fromkeys(
                [*(existing_bid_doc.get("planningWarnings") or []), *volume_warnings]
            ))
            existing_bid_doc["deviationTables"] = build_deviation_tables(fine_review)
            existing_bid_doc["templateFragments"] = template_fragment_summaries(template_fragments)
            existing_bid_doc["templateFragmentsArtifact"] = str(
                parse_bundle.get("templateFragmentsArtifact") or ""
            )
            workspace["bidDocument"] = existing_bid_doc
            self.persist_runtime()
            return {"bidDocument": existing_bid_doc, "workspace": workspace}

        has_explicit_format_items = any(
            isinstance(volume, dict)
            and isinstance(volume.get("items"), list)
            and volume.get("items")
            for volume in submission_structure.get("volumes", [])
        )
        outline = extract_response_outline(sections, ai)
        if has_explicit_format_items:
            plan = attach_template_fragments_to_plan(
                plan_from_submission_structure(submission_structure, fine_review),
                template_fragments,
            )
            bid_doc["planSource"] = "tender_document_structure"
            bid_doc["planSourceDetail"] = (
                "本地确定性解析：招标文件“投标文件格式”卷及卷内文件条目"
            )
            bid_doc["planningSource"] = "tender_document_structure"
            bid_doc["planningSourceDetail"] = bid_doc["planSourceDetail"]
            if pi_fallback_warnings:
                bid_doc["planningWarnings"] = pi_fallback_warnings
        elif outline and outline.get("items"):
            plan = attach_template_fragments_to_plan(
                attach_templates(
                    plan_from_outline(outline["items"], fine_review),
                    sections,
                ),
                template_fragments,
            )
            bid_doc["planSource"] = "tender_document"
            bid_doc["planSourceDetail"] = str(outline.get("sourceDetail") or "")
            bid_doc["planningSource"] = "tender_document"
            bid_doc["planningSourceDetail"] = str(outline.get("sourceDetail") or "")
            if pi_fallback_warnings:
                bid_doc["planningWarnings"] = pi_fallback_warnings
        else:
            raw_outline = parse_bundle.get("rawOutline")
            draft = (
                plan_from_outline(raw_outline, fine_review)
                if isinstance(raw_outline, list) and raw_outline
                else []
            )
            fallback_plan = draft or minimal_bid_skeleton()
            plan = attach_template_fragments_to_plan(
                fallback_plan,
                template_fragments,
            )
            bid_doc["planSource"] = "tender_document" if draft else "builtin_template"
            bid_doc["planSourceDetail"] = (
                "本地规则根据解析阶段的格式条目补齐多级章节"
                if draft else
                "本地最小文件骨架：未识别到可靠的招标文件目录格式，具体要求留到正文阶段"
            )
            bid_doc["planningSource"] = bid_doc["planSource"]
            bid_doc["planningSourceDetail"] = bid_doc["planSourceDetail"]
            if pi_fallback_warnings:
                bid_doc["planningWarnings"] = pi_fallback_warnings

        bid_doc["planningProvider"] = planning_provider or "unknown"
        bid_doc["planningStatus"] = planning_status or "local_rules"
        bid_doc["requirementMappingStatus"] = "pending"
        bid_doc["requirementMapBatchCount"] = 0
        bid_doc["requirementCoverage"] = []
        bid_doc["uncoveredRequirements"] = []
        bid_doc["duplicatedRequirements"] = []
        if planning_fallback_reason:
            bid_doc["planningFallbackReason"] = planning_fallback_reason
        if planning_status != "success":
            bid_doc["planningStatus"] = "fallback_local"

        bid_doc["plan"], volume_warnings = arrange_volumes(
            plan, submission_structure,
        )
        from .bid_writing.response_table import bind_response_table_requirements

        bid_doc["plan"] = bind_response_table_requirements(bid_doc["plan"], fine_review)
        if volume_warnings:
            bid_doc["planningWarnings"] = list(dict.fromkeys(
                [*(bid_doc.get("planningWarnings") or []), *volume_warnings]
            ))
        bid_doc["templateFragments"] = template_fragment_summaries(template_fragments)
        bid_doc["templateFragmentsArtifact"] = str(
            parse_bundle.get("templateFragmentsArtifact") or ""
        )
        bid_doc["deviationTables"] = build_deviation_tables(fine_review)
        bid_doc["status"] = "planned"
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {"bidDocument": bid_doc, "workspace": workspace}

    def _plan_bid_document_with_pi(
        self,
        workspace_id: str,
        workspace: dict[str, Any],
        fine_review: dict[str, Any],
        project: dict[str, Any],
    ) -> dict[str, Any] | None:
        try:
            from .cloud_agent.config import DifyConfig

            runtime_config = DifyConfig.from_env()
            provider = str(runtime_config.provider or "dify").strip().lower() or "dify"
            if provider != "pi":
                reason = (
                    f"当前运行时 provider={provider}，未启用 Pi 目录规划，"
                    "本次使用本地规则生成目录。"
                )
                return {
                    "plan": [],
                    "provider": provider,
                    "status": "not_attempted",
                    "fallbackReason": reason,
                    "warnings": [reason],
                }
            from .pi_agent.client import PiClient
            from .pi_agent.config import PiConfig

            reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
            document = project.get("document") if isinstance(project.get("document"), dict) else {}
            existing_bid_doc = workspace.get("bidDocument") if isinstance(workspace.get("bidDocument"), dict) else {}
            parse_bundle = self.get_parse_bundle(workspace_id)
            response = PiClient().plan_outline(
                workspace_id=workspace_id,
                project_id=str(workspace.get("projectId") or project.get("id") or ""),
                project=project,
                fine_review=fine_review,
                cloud_parse=reasoning.get("cloudParse") if isinstance(reasoning.get("cloudParse"), dict) else {},
                document_sections=document.get("sections") if isinstance(document.get("sections"), list) else [],
                existing_plan=existing_bid_doc.get("plan") if isinstance(existing_bid_doc.get("plan"), list) else [],
                parse_bundle=parse_bundle,
            )
            plan, warnings = self._normalize_pi_bid_plan(
                response, fine_review,
                (parse_bundle or {}).get("submissionStructure"),
            )
            if not plan:
                reason = "已启用 Pi 目录规划，但 Pi 未返回有效目录，本次已回退本地规则。"
                return {
                    "plan": [],
                    "provider": "pi",
                    "model": PiConfig.from_env().model,
                    "status": "empty_response",
                    "fallbackReason": reason,
                    "warnings": [reason],
                }
            return {
                "plan": plan,
                "provider": "pi",
                "model": PiConfig.from_env().model,
                "status": "success",
                "warnings": warnings,
                "coverage": [],
                "uncoveredRequirements": [],
                "duplicatedRequirements": [],
                "requirementMappingStatus": "pending",
                "requirementMapBatchCount": 0,
                "agentResults": (
                    response.get("agentResults")
                    if isinstance(response.get("agentResults"), list)
                    else []
                ),
            }
        except Exception as exc:
            reason = f"Pi 目录规划调用失败，本次已回退本地规则：{str(exc)[:300]}"
            return {
                "plan": [],
                "provider": "pi",
                "status": "failed",
                "fallbackReason": reason,
                "warnings": [reason],
            }

    @staticmethod
    def _normalize_pi_bid_plan(
        payload: dict[str, Any], fine_review: dict[str, Any],
        submission_structure: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        from .bid_writing.planning import assign_requirements
        from .bid_writing.schemas import (
            SECTION_KIND_ATTACHMENT,
            SECTION_KIND_COMMERCIAL,
            SECTION_KIND_DEVIATION,
            SECTION_KIND_TECHNICAL,
            SECTION_SOURCE_AI,
            SECTION_SOURCE_ATTACHMENT,
            SECTION_SOURCE_TABLE,
            SECTION_SOURCE_TEMPLATE,
            build_section,
        )

        if not isinstance(payload, dict):
            return [], []
        raw_plan = payload.get("plan") or payload.get("outline") or payload.get("items")
        if not isinstance(raw_plan, list):
            return [], []
        rows = fine_review.get("rows") if isinstance(fine_review, dict) else []
        valid_requirement_ids = {
            str(row.get("requirementId") or row.get("id") or "")
            for row in rows
            if isinstance(row, dict) and str(row.get("requirementId") or row.get("id") or "")
        }
        assignment = assign_requirements([row for row in rows if isinstance(row, dict)])
        valid_sources = {
            SECTION_SOURCE_AI,
            SECTION_SOURCE_ATTACHMENT,
            SECTION_SOURCE_TABLE,
            SECTION_SOURCE_TEMPLATE,
            "copied_template",
            "deviation",
        }
        valid_kinds = {
            SECTION_KIND_ATTACHMENT,
            SECTION_KIND_COMMERCIAL,
            SECTION_KIND_DEVIATION,
            SECTION_KIND_TECHNICAL,
        }
        sections: list[dict[str, Any]] = []
        seen: set[str] = set()
        source_id_map: dict[str, str] = {}
        assigned_rows: list[tuple[dict[str, Any], str, str]] = []
        custom_index = 1
        for item in raw_plan:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            source_section_id = str(
                item.get("sectionId") or item.get("id") or ""
            ).strip()
            section_id = source_section_id
            if not section_id or section_id in seen:
                section_id = f"custom-{custom_index:02d}"
                custom_index += 1
                while section_id in seen:
                    section_id = f"custom-{custom_index:02d}"
                    custom_index += 1
            seen.add(section_id)
            if source_section_id and source_section_id not in source_id_map:
                source_id_map[source_section_id] = section_id
            assigned_rows.append((item, section_id, source_section_id))

        levels_by_id: dict[str, int] = {}
        sibling_orders: dict[str, int] = {}
        for item, section_id, source_section_id in assigned_rows:
            source = str(item.get("source") or SECTION_SOURCE_AI).strip()
            kind = str(item.get("kind") or SECTION_KIND_TECHNICAL).strip()
            if source == "deviation":
                source = SECTION_SOURCE_TABLE
            if source not in valid_sources:
                source = SECTION_SOURCE_AI
            if kind not in valid_kinds:
                kind = SECTION_KIND_TECHNICAL if source == SECTION_SOURCE_AI else SECTION_KIND_COMMERCIAL
            requirement_ids = [
                str(value).strip()
                for value in (item.get("requirementIds") or [])
                if str(value).strip() in valid_requirement_ids
            ]
            if not requirement_ids:
                requirement_ids = assignment.get(section_id, [])
            parent_source_id = str(
                item.get("parentSectionId")
                or item.get("parentId")
                or ""
            ).strip()
            parent_section_id = source_id_map.get(parent_source_id, "")
            try:
                level = max(int(item.get("level") or 1), 1)
            except (TypeError, ValueError):
                level = 1
            if parent_section_id:
                level = max(level, levels_by_id.get(parent_section_id, level - 1) + 1)
            sibling_key = parent_section_id
            try:
                order = int(item.get("order") or 0)
            except (TypeError, ValueError):
                order = 0
            if order <= 0:
                order = sibling_orders.get(sibling_key, 0) + 1
            sibling_orders[sibling_key] = max(sibling_orders.get(sibling_key, 0), order)
            section = build_section(
                section_id,
                title,
                source,
                kind=kind,
                requirement_ids=requirement_ids,
                # 保留未解析的原始父级 ID，统一规范化器可以在父级
                # 出现在后面时重新建立关系；无效父级会提升为一级。
                parent_section_id=parent_section_id or parent_source_id,
                level=level,
                order=order,
                required=bool(item.get("required", True)),
                response_type=str(item.get("responseType") or "").strip(),
            )
            section["sourceSectionId"] = source_section_id or section_id
            section["evaluationIds"] = [
                str(value).strip()
                for value in (item.get("evaluationIds") or [])
                if str(value).strip()
            ]
            section["templateId"] = str(item.get("templateId") or "").strip()
            for key in (
                "formatItemId",
                "itemType",
                "conditional",
                "conditionText",
                "templateType",
                "sourceLocator",
                "structureSource",
            ):
                if key in item:
                    section[key] = item.get(key)
            if str(item.get("nodeType") or "") == "volume":
                section["nodeType"] = "volume"
                section["volumeId"] = str(item.get("volumeId") or section_id)
                section["responseType"] = "section_group"
            if str(item.get("volumeId") or "").strip() and not section.get("volumeId"):
                section["volumeId"] = str(item["volumeId"]).strip()
            for key in (
                "templateFragmentId",
                "templateOrigin",
                "templateType",
                "templateCopyMode",
            ):
                if str(item.get(key) or "").strip():
                    section[key] = str(item.get(key)).strip()
            sections.append(section)
            levels_by_id[section_id] = level
        from .bid_writing.plan_tree import normalize_plan_tree

        from .bid_writing.volumes import (
            arrange_volumes,
            reconcile_plan_with_submission_structure,
        )

        normalized_sections = normalize_plan_tree(sections)
        has_explicit_items = any(
            isinstance(volume, dict)
            and isinstance(volume.get("items"), list)
            and volume.get("items")
            for volume in (submission_structure or {}).get("volumes", [])
        )
        if has_explicit_items:
            normalized_sections = reconcile_plan_with_submission_structure(
                normalized_sections,
                submission_structure,
            )
        else:
            for section_id, title in (("d-01", "技术规格偏离表"), ("d-02", "商务偏离表")):
                if section_id not in seen:
                    normalized_sections.append(
                        build_section(
                            section_id,
                            title,
                            SECTION_SOURCE_TABLE,
                            kind=SECTION_KIND_DEVIATION,
                            level=1,
                            order=len(normalized_sections) + 1,
                            response_type="deviation",
                        )
                    )
            normalized_sections = normalize_plan_tree(normalized_sections)
        normalized_sections, volume_warnings = arrange_volumes(
            normalized_sections, submission_structure,
        )
        warnings = [str(item) for item in payload.get("warnings", []) if str(item).strip()] if isinstance(payload.get("warnings"), list) else []
        warnings.extend(volume_warnings)
        return normalized_sections, warnings

    def _template_fragment_for_section(
        self,
        section: dict[str, Any],
        parse_bundle: dict[str, Any] | None,
    ) -> dict[str, Any]:
        bundle = parse_bundle if isinstance(parse_bundle, dict) else {}
        fragments = bundle.get("templateFragments")
        if not isinstance(fragments, list):
            return {}
        from .bid_writing.template_fragments import match_template_fragment

        return match_template_fragment(section, fragments)

    def render_bid_template_preview(
        self,
        workspace_id: str,
        section_id: str,
    ) -> bytes | None:
        """返回当前模板章节的原始 DOCX 版式预览，不落临时文件。"""
        workspace = self.workspaces.get(workspace_id)
        if not isinstance(workspace, dict):
            return None
        bid_document = workspace.get("bidDocument")
        if not isinstance(bid_document, dict):
            return None
        section = next(
            (
                item
                for item in bid_document.get("plan", [])
                if isinstance(item, dict)
                and str(item.get("sectionId") or "") == section_id
            ),
            None,
        )
        if not isinstance(section, dict):
            return None
        parse_bundle = self.get_parse_bundle(workspace_id) or {}
        fragment = self._template_fragment_for_section(section, parse_bundle)
        if not fragment:
            return None
        from .bid_writing.assemble import render_template_preview_docx

        preview = render_template_preview_docx(
            fragment,
            section,
            root=self.root,
        )
        return preview[0] if preview else None

    @staticmethod
    def _invalidate_compliance_review(bid_doc: dict[str, Any]) -> None:
        review = bid_doc.get("complianceReview")
        if isinstance(review, dict) and review:
            review["status"] = "stale"
            review["staleAt"] = now_iso()

    def _fill_template_with_pi(
        self,
        *,
        workspace_id: str,
        project_id: str,
        parse_bundle: dict[str, Any],
        fragment: dict[str, Any],
        section: dict[str, Any],
        project: dict[str, Any],
        organization: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        """调用 Pi TableFormFillAgent，失败时返回空结果交给本地填充。"""

        if not fragment:
            return None, ""
        try:
            from .cloud_agent.config import DifyConfig

            if DifyConfig.from_env().provider != "pi":
                return None, ""
            from .pi_agent.client import PiClient
            from .pi_agent.writing_orchestrator import PiWritingOrchestrator

            agent_result = PiWritingOrchestrator(PiClient()).fill_template_fragment(
                parse_bundle=parse_bundle,
                fragment=fragment,
                section=section,
                project=project,
                company={**(self.company or {}), "organization": organization or {}},
                knowledge=self.company_knowledge or {},
                workspace_id=workspace_id,
                project_id=project_id,
            )
            return agent_result, ""
        except Exception as exc:
            return None, str(exc)[:1000]

    def _draft_response_table_rows_with_pi(
        self,
        *,
        section: dict[str, Any],
        prepared_rows: list[dict[str, Any]],
        workspace_id: str,
        project_id: str,
    ) -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
        """Draft matched response rows without running requirement/material matching again."""

        matched_rows = [
            row
            for row in prepared_rows
            if isinstance(row, dict) and row.get("materials")
        ]
        if not matched_rows:
            return {}, [], []

        try:
            from .cloud_agent.config import DifyConfig

            if DifyConfig.from_env().provider != "pi":
                return {}, [], ["当前不是 Pi 撰写提供方，已保留 M3 材料待人工核验。"]
            from .agent_runtime.contracts import build_agent_result, now_iso
            from .pi_agent.client import PiClient

            client = PiClient()
        except Exception as exc:
            return {}, [], [f"Pi 响应表智能体不可用：{str(exc)[:300]}"]

        batch_size = 12
        batches = [
            matched_rows[index : index + batch_size]
            for index in range(0, len(matched_rows), batch_size)
        ]
        responses: dict[str, str] = {}
        agent_results: list[dict[str, Any]] = []
        warnings: list[str] = []
        for batch_index, batch in enumerate(batches, start=1):
            started_at = now_iso()
            expected_ids = {
                str(item.get("requirementId") or "").strip()
                for item in batch
                if str(item.get("requirementId") or "").strip()
            }
            source_refs = [
                ref
                for item in batch
                for ref in (item.get("sourceRefs") or [])
                if isinstance(ref, dict)
            ]
            try:
                raw = client.draft_requirement_responses(
                    section=section,
                    requirements=batch,
                    workspace_id=workspace_id,
                    project_id=project_id,
                )
                raw_rows = raw.get("rows") or raw.get("requirementResponses") or []
                accepted: list[dict[str, str]] = []
                if isinstance(raw_rows, list):
                    for item in raw_rows:
                        if not isinstance(item, dict):
                            continue
                        requirement_id = str(
                            item.get("requirementId") or item.get("id") or ""
                        ).strip()
                        response = str(item.get("response") or item.get("content") or "").strip()
                        if requirement_id in expected_ids and response:
                            responses[requirement_id] = response[:3000]
                            accepted.append(
                                {"requirementId": requirement_id, "response": response[:3000]}
                            )
                missing_ids = expected_ids - set(responses)
                batch_warnings = [
                    str(value).strip()
                    for value in (raw.get("warnings") or [])
                    if str(value).strip()
                ] if isinstance(raw, dict) else []
                if missing_ids:
                    batch_warnings.append(
                        f"第 {batch_index} 批有 {len(missing_ids)} 条响应未返回，将保留待人工核验内容。"
                    )
                warnings.extend(batch_warnings)
                agent_results.append(
                    build_agent_result(
                        agent_id="RequirementResponseTableAgent",
                        provider="pi",
                        model=str(getattr(client.config, "model", "") or ""),
                        status="success" if accepted else "failed",
                        input_refs=[
                            "matching:fineReview.rows",
                            "matching:matchedObjectIds",
                            "m3:matchedCompanyObjects",
                        ],
                        output={"rows": accepted},
                        source_refs=source_refs,
                        warnings=batch_warnings,
                        run_id=f"{project_id or 'project'}-response-table-{batch_index:03d}",
                        created_at=started_at,
                        finished_at=now_iso(),
                    )
                )
            except Exception as exc:
                warning = f"第 {batch_index} 批响应表撰写失败：{str(exc)[:300]}"
                warnings.append(warning)
                agent_results.append(
                    build_agent_result(
                        agent_id="RequirementResponseTableAgent",
                        provider="pi",
                        model=str(getattr(client.config, "model", "") or ""),
                        status="failed",
                        input_refs=[
                            "matching:fineReview.rows",
                            "matching:matchedObjectIds",
                            "m3:matchedCompanyObjects",
                        ],
                        output={},
                        source_refs=source_refs,
                        warnings=[warning],
                        run_id=f"{project_id or 'project'}-response-table-{batch_index:03d}",
                        created_at=started_at,
                        finished_at=now_iso(),
                    )
                )
        return responses, agent_results, list(dict.fromkeys(warnings))

    def _fill_response_table_section(
        self,
        section: dict[str, Any],
        project: dict[str, Any],
        organization: dict[str, Any],
        fine_review: dict[str, Any],
        *,
        workspace_id: str,
        parse_bundle: dict[str, Any],
    ) -> dict[str, Any]:
        from .bid_writing.response_table import (
            bind_response_table_requirements,
            build_template_response_table_spec,
            build_response_table_result,
            prepare_response_table_inputs,
            replace_first_markdown_table,
        )

        bound = bind_response_table_requirements([section], fine_review)
        if bound:
            section.update(bound[0])
        prepared_rows = prepare_response_table_inputs(
            section,
            fine_review,
            self.company_knowledge,
        )
        responses, agent_results, warnings = self._draft_response_table_rows_with_pi(
            section=section,
            prepared_rows=prepared_rows,
            workspace_id=workspace_id,
            project_id=str(project.get("id") or project.get("projectId") or ""),
        )
        result = build_response_table_result(
            section,
            prepared_rows,
            responses,
            agent_results=agent_results,
            warnings=warnings,
        )

        fragment = self._template_fragment_for_section(section, parse_bundle)
        template_table_spec = build_template_response_table_spec(
            fragment,
            result["requirementResponseRows"],
        ) if fragment else None
        if fragment:
            from .bid_writing.template_reuse import fill_section_template

            base = fill_section_template(
                section,
                project,
                organization,
                self.company,
                fragment=fragment,
            )
        else:
            from .bid_writing.template_reuse import fill_format_item_without_template

            document_blocks = (
                parse_bundle.get("documentBlocks")
                if isinstance(parse_bundle.get("documentBlocks"), dict)
                else {}
            )
            tables = document_blocks.get("tables")
            base = fill_format_item_without_template(
                section,
                project,
                organization,
                self.company,
                tables=tables if isinstance(tables, list) else [],
                document_blocks=document_blocks,
            )

        base_content = str(base.get("content") or "")
        section["content"] = replace_first_markdown_table(
            base_content,
            str(result.get("tableMarkdown") or ""),
        )
        section["requirementResponseRows"] = result["requirementResponseRows"]
        section["templateResponseTable"] = template_table_spec or {"mapped": False}
        section["sourceRefs"] = result["sourceRefs"]
        section["blanksToFill"] = list(
            dict.fromkeys(
                [
                    str(value).strip()
                    for value in (base.get("blanksToFill") or [])
                    if str(value).strip()
                ]
                + result["blanksToFill"]
            )
        )
        section["status"] = result["status"]
        section["responseTableProvider"] = "pi" if agent_results and any(
            item.get("status") == "success" for item in agent_results
        ) else "local_fallback"
        section["responseTableWarnings"] = result["warnings"]
        section["requirementResponseAgentResults"] = agent_results
        if agent_results:
            section["agentResults"] = agent_results
        for key in (
            "filledTables",
            "templateFieldValues",
            "templateCellValues",
            "templateBlocks",
            "templateTables",
            "templateStyle",
            "contentMatchesTemplate",
            "templateFillWarnings",
        ):
            if key in base:
                section[key] = base.get(key)
        section["templateFillStatus"] = (
            "response_table_filled"
            if fragment
            else str(base.get("templateFillStatus") or "response_table_scaffold")
        )
        section["templateFillProvider"] = section["responseTableProvider"]
        return section

    def _fill_bid_section(
        self,
        section: dict[str, Any],
        project: dict[str, Any],
        organization: dict[str, Any],
        commercial: dict[str, Any],
        technical: dict[str, Any],
        *,
        workspace_id: str = "",
        parse_bundle: dict[str, Any] | None = None,
        fine_review: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """按 source/kind 填充单个章节（模板复用 / 报价占位 / 商务/技术生成）。"""
        from .bid_writing.template_reuse import fill_section_template

        section_id = section.get("sectionId", "")
        if str(section.get("responseType") or "") == "section_group":
            section["content"] = ""
            section["status"] = "planned"
            return section
        from .bid_writing.response_table import response_table_domains

        if response_table_domains(section):
            return self._fill_response_table_section(
                section,
                project,
                organization,
                fine_review if isinstance(fine_review, dict) else {},
                workspace_id=workspace_id,
                parse_bundle=parse_bundle if isinstance(parse_bundle, dict) else {},
            )
        fragment = self._template_fragment_for_section(section, parse_bundle)
        if section.get("source") == "copied_template" or fragment:
            agent_result, agent_error = self._fill_template_with_pi(
                workspace_id=workspace_id,
                project_id=str(project.get("id") or project.get("projectId") or ""),
                parse_bundle=parse_bundle or {},
                fragment=fragment,
                section=section,
                project=project,
                organization=organization,
            )
            try:
                filled = fill_section_template(
                    section,
                    project,
                    organization,
                    self.company,
                    fragment=fragment or None,
                    agent_result=agent_result,
                )
            except Exception:
                filled = {}
            if filled:
                section["content"] = str(filled.get("content", ""))
                section["blanksToFill"] = filled.get("blanksToFill") or []
                section["status"] = filled.get("status", "generated")
                for key in (
                    "templateFillStatus",
                    "templateFieldValues",
                    "templateCellValues",
                    "templateBlocks",
                    "templateTables",
                    "templateStyle",
                    "filledTables",
                    "templateFillAgentResults",
                    "templateFillWarnings",
                    "contentMatchesTemplate",
                ):
                    if key in filled:
                        section[key] = filled.get(key)
                if agent_result:
                    section["templateFillProvider"] = "pi"
                elif agent_error:
                    section["templateFillProvider"] = "local_fallback"
                    section["templateFillError"] = agent_error
                else:
                    section["templateFillProvider"] = "local"
            return section
        if (
            str(section.get("responseType") or "").strip() in {"fixed_form", "table"}
            or str(section.get("itemType") or "").strip() in {"fixed_form", "fixed_table"}
        ):
            from .bid_writing.template_reuse import fill_format_item_without_template

            document_blocks = parse_bundle.get("documentBlocks") if isinstance(parse_bundle, dict) else {}
            source_tables = (
                document_blocks.get("tables")
                if isinstance(document_blocks, dict)
                and isinstance(document_blocks.get("tables"), list)
                else []
            )
            filled = fill_format_item_without_template(
                section,
                project,
                organization,
                self.company,
                tables=source_tables,
                document_blocks=(
                    document_blocks
                    if isinstance(document_blocks, dict)
                    else {}
                ),
            )
            section["content"] = str(filled.get("content") or "")
            section["blanksToFill"] = filled.get("blanksToFill") or []
            section["filledTables"] = filled.get("filledTables") or []
            section["templateFillStatus"] = filled.get("templateFillStatus") or "scaffold"
            section["templateFillProvider"] = "local"
            section["status"] = str(filled.get("status") or "missing")
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
            for key in (
                "materialMatches",
                "unmatchedMaterialRequirementIds",
                "writingPlan",
                "agentResults",
                "provider",
                "phase",
            ):
                if key in merged:
                    section[key] = merged.get(key)
            section["status"] = merged.get("status", "generated")
        return section

    def _cloud_section_draft(
        self,
        workspace_id: str,
        section: dict[str, Any],
        fine_review: dict[str, Any],
        project: dict[str, Any],
        organization: dict[str, Any],
        mode: str = "open",
    ) -> dict[str, Any] | None:
        """优先调用云端章节撰写 Workflow；未配置或失败时交给本地生成链路回退。"""
        source = str(section.get("source") or "").strip()
        kind = str(section.get("kind") or "").strip()
        if str(section.get("responseType") or "") == "section_group":
            return None
        if source in {"template", "copied_template", "table", "attachment"}:
            return None
        if kind not in {"technical", "commercial"} and source != "ai":
            return None
        try:
            from .cloud_agent.config import DifyConfig
            provider = DifyConfig.from_env().provider
            if provider == "pi":
                from .pi_agent.client import PiClient

                workspace = self.workspaces.get(workspace_id)
                bid_document = (
                    workspace.get("bidDocument")
                    if isinstance(workspace, dict)
                    and isinstance(workspace.get("bidDocument"), dict)
                    else {}
                )
                parse_bundle = self.get_parse_bundle(workspace_id)
                self._ensure_pi_requirement_coverage(
                    workspace_id,
                    bid_document,
                    parse_bundle or {},
                )
                coverage = (
                    bid_document.get("requirementCoverage")
                    if isinstance(bid_document.get("requirementCoverage"), list)
                    else []
                )
                draft = PiClient().draft_section(
                    workspace_id=workspace_id,
                    project_id=str(project.get("id") or project.get("projectId") or ""),
                    section=section,
                    fine_review=fine_review,
                    project=project,
                    company={**(self.company or {}), "organization": organization or {}},
                    knowledge=self.company_knowledge or {},
                    mode=mode,
                    parse_bundle=parse_bundle,
                    coverage=coverage,
                )
                from .cloud_agent.normalizers import normalize_section_draft_result

                result = normalize_section_draft_result(draft, section=section).model_dump(mode="json")
                result["draftProvider"] = "pi"
                return result

            from .cloud_agent.dify_client import DifyClient
            from .cloud_agent.normalizers import normalize_section_draft_result

            config = DifyConfig.from_env()
            if not (config.enabled and config.api_key and config.section_workflow_key):
                return None
            project_id = str(project.get("id") or project.get("projectId") or "")
            response = DifyClient(config).draft_section(
                workspace_id=workspace_id,
                project_id=project_id,
                section=section,
                fine_review=fine_review,
                project=project,
                company={**(self.company or {}), "organization": organization or {}},
                knowledge=self.company_knowledge or {},
                mode=mode,
            )
            draft = normalize_section_draft_result(response, section=section).model_dump(mode="json")
            draft["draftProvider"] = "dify"
            return draft
        except Exception as exc:
            return {
                "content": "",
                "sourceRefs": [],
                "status": "missing",
                "cloudDraftError": str(exc)[:1000],
            }

    def _ensure_pi_requirement_coverage(
        self,
        workspace_id: str,
        bid_document: dict[str, Any],
        parse_bundle: dict[str, Any],
        *,
        force: bool = False,
    ) -> list[dict[str, Any]]:
        """正文阶段按需生成并缓存要求到目录章节的覆盖关系。"""
        current = bid_document.get("requirementCoverage")
        current = current if isinstance(current, list) else []
        status = str(bid_document.get("requirementMappingStatus") or "").strip()
        if status == "completed" and not force:
            return [item for item in current if isinstance(item, dict)]

        requirements = (
            parse_bundle.get("requirements")
            if isinstance(parse_bundle.get("requirements"), list)
            else []
        )
        if not requirements:
            bid_document["requirementMappingStatus"] = "completed"
            bid_document["requirementMapBatchCount"] = 0
            bid_document["requirementCoverage"] = []
            bid_document["uncoveredRequirements"] = []
            bid_document["duplicatedRequirements"] = []
            return []

        try:
            from .cloud_agent.config import DifyConfig

            if str(DifyConfig.from_env().provider or "").strip().lower() != "pi":
                return [item for item in current if isinstance(item, dict)]

            from .pi_agent.client import PiClient
            from .pi_agent.writing_orchestrator import PiWritingOrchestrator

            workspace = self.workspaces.get(workspace_id) or {}
            project = (
                workspace.get("project")
                if isinstance(workspace.get("project"), dict)
                else {}
            )
            result = PiWritingOrchestrator(PiClient()).map_requirement_coverage(
                parse_bundle=parse_bundle,
                outline_plan=bid_document.get("plan")
                if isinstance(bid_document.get("plan"), list)
                else [],
                workspace_id=workspace_id,
                project_id=str(project.get("id") or workspace.get("projectId") or ""),
            )
            bid_document["requirementCoverage"] = result.get("coverage") or []
            bid_document["uncoveredRequirements"] = result.get("uncoveredRequirements") or []
            bid_document["duplicatedRequirements"] = result.get("duplicatedRequirements") or []
            bid_document["requirementMappingStatus"] = str(
                result.get("requirementMappingStatus") or "completed"
            )
            bid_document["requirementMapBatchCount"] = int(
                result.get("requirementMapBatchCount") or 0
            )
            bid_document["requirementMappingWarnings"] = [
                str(item)[:500]
                for item in (result.get("warnings") or [])
                if str(item).strip()
            ]
            existing_agents = (
                bid_document.get("agentResults")
                if isinstance(bid_document.get("agentResults"), list)
                else []
            )
            bid_document["agentResults"] = [
                *existing_agents,
                *(
                    result.get("agentResults")
                    if isinstance(result.get("agentResults"), list)
                    else []
                ),
            ]
            return [
                item
                for item in (result.get("coverage") or [])
                if isinstance(item, dict)
            ]
        except Exception as exc:
            bid_document["requirementMappingStatus"] = "failed"
            bid_document["requirementMappingWarnings"] = [
                f"正文阶段条款映射失败：{str(exc)[:500]}"
            ]
            return [item for item in current if isinstance(item, dict)]

    def save_bid_plan(self, workspace_id: str, plan: list[dict[str, Any]]) -> dict[str, Any] | None:
        """保存用户编辑后的章节目录。"""
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        if not isinstance(workspace.get("bidDocument"), dict):
            self.plan_bid_document(workspace_id)
        bid_doc = workspace.get("bidDocument") or {}
        if isinstance(plan, list):
            from .bid_writing.plan_tree import normalize_plan_tree
            from .bid_writing.response_table import bind_response_table_requirements

            reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
            fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
            bid_doc["plan"] = bind_response_table_requirements(
                normalize_plan_tree(plan),
                fine_review,
            )
        # 用户编辑目录后，原有“要求 -> 章节”关系可能已经失效，
        # 下次进入正文生成时重新按最新目录映射。
        bid_doc["requirementMappingStatus"] = "pending"
        bid_doc["requirementMapBatchCount"] = 0
        bid_doc["requirementCoverage"] = []
        bid_doc["uncoveredRequirements"] = []
        bid_doc["duplicatedRequirements"] = []
        bid_doc["requirementMappingWarnings"] = []
        bid_doc["status"] = "planned"
        self._invalidate_compliance_review(bid_doc)
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
        from .bid_writing.response_table import response_table_domains

        is_response_table = bool(response_table_domains(section))
        if not is_response_table:
            self._refresh_company_from_m3(force=True)
        parse_bundle = self.get_parse_bundle(workspace_id) or {}

        commercial: dict[str, Any] = {}
        technical: dict[str, Any] = {}
        cloud_error = ""
        source = str(section.get("source") or "").strip()
        kind = str(section.get("kind") or "").strip()
        is_ai_section = (
            str(section.get("responseType") or "") != "section_group"
            and kind in {"technical", "commercial"}
            and source not in {"template", "copied_template", "table", "attachment"}
        )
        if not is_response_table and is_ai_section:
            self._ensure_pi_requirement_coverage(
                workspace_id,
                bid_doc,
                parse_bundle,
            )
            from .bid_writing.technical import generate_technical_section

            cloud = self._cloud_section_draft(workspace_id, section, fine_review, project, organization)
            if cloud and str(cloud.get("content") or "").strip():
                if kind == "commercial":
                    commercial = {section_id: cloud}
                else:
                    technical = {section_id: cloud}
            else:
                if cloud and cloud.get("cloudDraftError"):
                    cloud_error = str(cloud.get("cloudDraftError") or "")
                if kind == "commercial":
                    from .bid_writing.commercial import generate_commercial_sections

                    commercial = {
                        section_id: (
                            generate_commercial_sections(
                                project,
                                self.company,
                                self.company_knowledge,
                                organization,
                                ai,
                            )
                            or {}
                        ).get(section_id, {})
                    }
                else:
                    technical = {
                        section_id: generate_technical_section(
                            section, fine_review, project, self.company, self.company_knowledge, ai
                        ) or {}
                    }
        elif not is_response_table:
            from .bid_writing.commercial import generate_commercial_sections

            commercial = (
                generate_commercial_sections(
                    project, self.company, self.company_knowledge, organization, ai
                )
                or {}
            )

        self._fill_bid_section(
            section,
            project,
            organization,
            commercial,
            technical,
            workspace_id=workspace_id,
            parse_bundle=parse_bundle,
            fine_review=fine_review,
        )
        from .bid_writing.deviations import build_deviation_tables

        bid_doc["deviationTables"] = build_deviation_tables(fine_review)
        if cloud_error:
            section["cloudDraftError"] = cloud_error
            section["draftProvider"] = "local_fallback"
        elif technical.get(section_id, {}).get("draftProvider"):
            section["draftProvider"] = technical[section_id]["draftProvider"]
        elif commercial.get(section_id, {}).get("draftProvider"):
            section["draftProvider"] = commercial[section_id]["draftProvider"]
        bid_doc["status"] = "generated"
        self._invalidate_compliance_review(bid_doc)
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {"bidDocument": bid_doc}

    def save_bid_section(
        self,
        workspace_id: str,
        section_id: str,
        content: str,
        *,
        template_blocks: list[dict[str, Any]] | None = None,
        template_tables: list[dict[str, Any]] | None = None,
        content_matches_template: bool | None = None,
    ) -> dict[str, Any] | None:
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
        if template_blocks is not None:
            section["templateBlocks"] = template_blocks
        if template_tables is not None:
            section["templateTables"] = template_tables
        if (
            section.get("templateFragmentId")
            or section.get("source") == "copied_template"
            or section.get("itemType") in {"fixed_form", "fixed_table"}
        ):
            if content_matches_template is not None:
                section["contentMatchesTemplate"] = content_matches_template
            elif template_blocks is not None and template_tables is not None:
                section["contentMatchesTemplate"] = True
            else:
                section["contentMatchesTemplate"] = False
        self._invalidate_compliance_review(bid_doc)
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {"bidDocument": bid_doc}

    def fill_bid_section_template(self, workspace_id: str, section_id: str) -> dict[str, Any] | None:
        """只执行固定模板填充，不触发技术章节正文生成。"""
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        if not isinstance(workspace.get("bidDocument"), dict):
            self.plan_bid_document(workspace_id)
        bid_doc = workspace.get("bidDocument") or {}
        section = next(
            (
                item
                for item in bid_doc.get("plan", [])
                if isinstance(item, dict) and str(item.get("sectionId") or "") == section_id
            ),
            None,
        )
        if not isinstance(section, dict):
            return None

        parse_bundle = self.get_parse_bundle(workspace_id) or {}
        reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
        fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
        fragment = self._template_fragment_for_section(section, parse_bundle)
        has_template = bool(
            fragment
            or section.get("source") == "copied_template"
            or str(section.get("templateText") or "").strip()
        )
        if not has_template and str(section.get("itemType") or "").strip() in {
            "fixed_form",
            "fixed_table",
        }:
            # 结构解析已确认这是招标文件要求提交的固定格式项，即使
            # 旧项目暂时没有 templateFragments，也应允许走结构化表格/
            # 人工待补骨架，而不是直接报“没有模板”。
            has_template = True
        if not has_template:
            return {
                "filled": False,
                "message": "当前章节没有可填充的招标文件模板",
                "bidDocument": bid_doc,
                "section": section,
            }

        from .bid_writing.response_table import response_table_domains

        if not response_table_domains(section):
            self._refresh_company_from_m3(force=True)
        project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}
        organization = self.organization or {}
        self._fill_bid_section(
            section,
            project,
            organization,
            {},
            {},
            workspace_id=workspace_id,
            parse_bundle=parse_bundle,
            fine_review=fine_review,
        )
        bid_doc["status"] = "generated"
        self._invalidate_compliance_review(bid_doc)
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        filled_message = (
            "招标文件模板已复制并填充，可继续人工编辑"
            if fragment
            else "未定位到原始模板，已生成结构化待补底稿，请按招标文件核对"
        )
        return {
            "filled": True,
            "message": filled_message,
            "provider": str(section.get("templateFillProvider") or "local"),
            "bidDocument": bid_doc,
            "section": section,
        }

    def rewrite_bid_section(
        self,
        workspace_id: str,
        section_id: str,
        instruction: str,
        *,
        operation: str = "append",
        selected_text: str = "",
    ) -> dict[str, Any] | None:
        """调用 Pi 对非模板章节执行局部补写/重写，并保存编辑轨迹。"""
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        instruction = str(instruction or "").strip()
        if not instruction:
            return {
                "rewritten": False,
                "message": "请先输入本章修改要求",
            }
        if not isinstance(workspace.get("bidDocument"), dict):
            self.plan_bid_document(workspace_id)
        bid_doc = workspace.get("bidDocument") or {}
        section = next(
            (
                item
                for item in bid_doc.get("plan", [])
                if isinstance(item, dict) and str(item.get("sectionId") or "") == section_id
            ),
            None,
        )
        if not isinstance(section, dict):
            return None

        parse_bundle = self.get_parse_bundle(workspace_id) or {}
        fragment = self._template_fragment_for_section(section, parse_bundle)
        is_template = bool(
            fragment
            or section.get("source") in {"template", "copied_template"}
            or str(section.get("templateText") or "").strip()
        )
        if is_template:
            return {
                "rewritten": False,
                "protected": True,
                "message": "固定模板章节不能使用局部重写，请使用“仅填充模板”或人工编辑可填字段",
                "bidDocument": bid_doc,
                "section": section,
            }
        if section.get("kind") != "technical" and section.get("source") != "ai":
            return {
                "rewritten": False,
                "message": "当前章节不是可由 Pi 局部撰写的技术章节",
                "bidDocument": bid_doc,
                "section": section,
            }

        requested_operation = str(operation or "append").strip()
        if requested_operation in {"selection", "replace", "replaceSelection"}:
            requested_operation = "replace_selection"
        if requested_operation not in {"append", "rewrite", "replace_selection"}:
            requested_operation = "append"
        current_content = str(section.get("content") or "")
        selected_text = str(selected_text or "")
        if requested_operation == "replace_selection" and not selected_text:
            return {
                "rewritten": False,
                "message": "替换选区模式需要提供正文片段",
                "bidDocument": bid_doc,
                "section": section,
            }
        if selected_text and selected_text not in current_content:
            return {
                "rewritten": False,
                "message": "选中的正文片段已发生变化，请重新选择后再试",
                "bidDocument": bid_doc,
                "section": section,
            }

        provider = ""
        try:
            from .cloud_agent.config import DifyConfig

            provider = DifyConfig.from_env().provider
            if provider != "pi":
                return {
                    "rewritten": False,
                    "provider": provider,
                    "message": "当前智能体提供方不是 Pi，未执行局部修改",
                    "bidDocument": bid_doc,
                    "section": section,
                }
            from .pi_agent.client import PiClient
            from .pi_agent.writing_orchestrator import PiWritingOrchestrator

            reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
            fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
            self._ensure_pi_requirement_coverage(
                workspace_id,
                bid_doc,
                parse_bundle,
            )
            coverage = bid_doc.get("requirementCoverage")
            if not isinstance(coverage, list):
                coverage = []
            project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}
            self._refresh_company_from_m3(force=True)
            result = PiWritingOrchestrator(PiClient()).rewrite_section(
                parse_bundle=parse_bundle,
                coverage=[item for item in coverage if isinstance(item, dict)],
                section=section,
                current_content=current_content,
                instruction=instruction,
                operation=requested_operation,
                selected_text=selected_text,
                project=project,
                company={**(self.company or {}), "organization": self.organization or {}},
                knowledge=self.company_knowledge or {},
                mode=str(bid_doc.get("mode") or "open"),
                workspace_id=workspace_id,
                project_id=str(project.get("id") or workspace.get("projectId") or ""),
            )
        except Exception as exc:
            return {
                "rewritten": False,
                "provider": provider or "pi",
                "message": f"Pi 局部修改失败，原正文未改变：{str(exc)[:500]}",
                "bidDocument": bid_doc,
                "section": section,
            }

        actual_operation = str(result.get("operation") or requested_operation)
        replacement = str(result.get("replacement") or "").strip()
        generated_content = str(result.get("content") or "").strip()
        if actual_operation == "rewrite":
            updated_content = generated_content or replacement
        elif actual_operation == "replace_selection":
            if not selected_text or not replacement:
                return {
                    "rewritten": False,
                    "provider": "pi",
                    "message": "Pi 未返回可替换的正文片段，原正文未改变",
                    "bidDocument": bid_doc,
                    "section": section,
                }
            updated_content = current_content.replace(selected_text, replacement, 1)
        else:
            if not replacement:
                return {
                    "rewritten": False,
                    "provider": "pi",
                    "message": "Pi 未返回可追加的正文片段，原正文未改变",
                    "bidDocument": bid_doc,
                    "section": section,
                }
            updated_content = (
                f"{current_content.rstrip()}\n\n{replacement}".strip()
                if current_content.strip()
                else replacement
            )
        if not updated_content.strip() or updated_content == current_content:
            return {
                "rewritten": False,
                "provider": "pi",
                "message": "本次修改没有产生正文变化",
                "bidDocument": bid_doc,
                "section": section,
            }

        previous_content = current_content
        section["content"] = updated_content
        section["status"] = "generated"
        section["rewriteProvider"] = "pi"
        section["rewriteOperation"] = actual_operation
        section["rewriteWarnings"] = result.get("warnings") or []
        section["rewriteSourceRefs"] = result.get("sourceRefs") or []
        section["blanksToFill"] = list(
            dict.fromkeys(
                [
                    str(item)
                    for item in (section.get("blanksToFill") or [])
                    if str(item).strip()
                ]
                + [
                    str(item)
                    for item in (result.get("blanksToFill") or [])
                    if str(item).strip()
                ]
            )
        )
        previous_refs = section.get("sourceRefs") if isinstance(section.get("sourceRefs"), list) else []
        new_refs = result.get("sourceRefs") if isinstance(result.get("sourceRefs"), list) else []
        section["sourceRefs"] = (
            list(previous_refs) + list(new_refs)
            if actual_operation == "append"
            else list(new_refs)
        )
        existing_agents = section.get("agentResults") if isinstance(section.get("agentResults"), list) else []
        section["agentResults"] = list(existing_agents) + list(result.get("agentResults") or [])
        history = section.get("editHistory") if isinstance(section.get("editHistory"), list) else []
        history.append(
            {
                "id": uid("edit"),
                "operation": actual_operation,
                "instruction": instruction,
                "provider": "pi",
                "createdAt": now_iso(),
                "previousLength": len(previous_content),
                "newLength": len(updated_content),
                "sourceRefs": list(new_refs),
            }
        )
        section["editHistory"] = history[-50:]
        bid_doc["version"] = int(bid_doc.get("version") or 1) + 1
        bid_doc["status"] = "generated"
        self._invalidate_compliance_review(bid_doc)
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {
            "rewritten": True,
            "provider": "pi",
            "message": "Pi 已完成本章局部修改",
            "rewrite": result,
            "bidDocument": bid_doc,
            "section": section,
        }

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
        from .bid_writing.response_table import response_table_domains

        has_non_response_sections = any(
            isinstance(section, dict)
            and str(section.get("responseType") or "") != "section_group"
            and not response_table_domains(section)
            for section in (bid_doc.get("plan") or [])
        )
        if has_non_response_sections:
            self._refresh_company_from_m3(force=True)
        ai = ensure_config(self.config).get("ai", {})
        organization = self.organization or {}
        parse_bundle = self.get_parse_bundle(workspace_id) or {}

        from .bid_writing.commercial import generate_commercial_sections
        from .bid_writing.technical import generate_technical_section

        commercial: dict[str, Any] = {}
        technical: dict[str, Any] = {}
        cloud_errors: dict[str, str] = {}
        if has_non_response_sections:
            try:
                commercial = generate_commercial_sections(
                    project,
                    self.company,
                    self.company_knowledge,
                    organization,
                    ai,
                ) or {}
            except Exception:
                commercial = {}
        for section in bid_doc.get("plan", []):
            if not isinstance(section, dict):
                continue
            source = str(section.get("source") or "").strip()
            kind = str(section.get("kind") or "").strip()
            if (
                str(section.get("responseType") or "") == "section_group"
                or response_table_domains(section)
                or kind not in {"technical", "commercial"}
                or source in {"template", "copied_template", "table", "attachment"}
            ):
                continue
            section_id = str(section.get("sectionId") or "")
            if not section_id:
                continue
            cloud = self._cloud_section_draft(workspace_id, section, fine_review, project, organization, mode)
            if cloud and str(cloud.get("content") or "").strip():
                if kind == "commercial":
                    commercial[section_id] = cloud
                else:
                    technical[section_id] = cloud
                continue
            if cloud and cloud.get("cloudDraftError"):
                cloud_errors[section_id] = str(cloud.get("cloudDraftError") or "")
            try:
                if kind == "commercial":
                    commercial[section_id] = (
                        generate_commercial_sections(
                            project,
                            self.company,
                            self.company_knowledge,
                            organization,
                            ai,
                        )
                        or {}
                    ).get(section_id, {})
                else:
                    technical[section_id] = generate_technical_section(
                        section, fine_review, project, self.company, self.company_knowledge, ai
                    ) or {}
            except Exception:
                if kind == "commercial":
                    commercial[section_id] = {}
                else:
                    technical[section_id] = {}

        for section in bid_doc.get("plan", []):
            self._fill_bid_section(
                section,
                project,
                organization,
                commercial,
                technical,
                workspace_id=workspace_id,
                parse_bundle=parse_bundle,
                fine_review=fine_review,
            )
            section_id = str(section.get("sectionId") or "")
            if section_id in cloud_errors:
                section["cloudDraftError"] = cloud_errors[section_id]
                section["draftProvider"] = "local_fallback"
            elif technical.get(section_id, {}).get("draftProvider"):
                section["draftProvider"] = technical[section_id]["draftProvider"]
            elif commercial.get(section_id, {}).get("draftProvider"):
                section["draftProvider"] = commercial[section_id]["draftProvider"]

        from .bid_writing.deviations import build_deviation_tables

        bid_doc["deviationTables"] = build_deviation_tables(fine_review)
        bid_doc["mode"] = "secret" if mode == "secret" else "open"
        bid_doc["status"] = "generated"
        self._invalidate_compliance_review(bid_doc)
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
        from .bid_writing.volumes import separate_volume_plans
        parse_bundle = self.get_parse_bundle(workspace_id) or {}
        submission_structure = bid_doc.get("submissionStructure") or parse_bundle.get("submissionStructure")
        volume_plans, split_warning = separate_volume_plans(
            doc_to_export.get("plan") or [], submission_structure,
        )

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

        fragment_list = (
            parse_bundle.get("templateFragments")
            if isinstance(parse_bundle.get("templateFragments"), list)
            else []
        )
        results: list[dict[str, Any]] = []
        if volume_plans:
            for index, (spec, volume_plan) in enumerate(volume_plans, start=1):
                volume_doc = deepcopy(doc_to_export)
                volume_doc["plan"] = volume_plan
                project_title = str(
                    bid_doc.get("title")
                    or (item.get("title") if isinstance(item, dict) else "")
                    or "投标响应文件"
                )
                volume_doc["title"] = f"{index:02d}_{project_title}_{spec['title']}"
                results.append(assemble_docx(
                    volume_doc, output_dir, assets=assets or None,
                    root=self.root, template_fragments=fragment_list,
                    include_unlisted_deviations=False,
                ))
        else:
            results.append(assemble_docx(
                doc_to_export, output_dir, assets=assets or None,
                root=self.root, template_fragments=fragment_list,
            ))
        export_warnings = [
            *([split_warning] if split_warning else []),
            *[
                str(warning) for result in results
                for warning in (result.get("exportWarnings") or [])
                if str(warning).strip()
            ],
        ]
        copied_template_count = sum(
            int(result.get("copiedTemplateCount") or 0) for result in results
        )
        bid_doc["copiedTemplateCount"] = copied_template_count
        bid_doc["exportWarnings"] = export_warnings
        bid_doc["lastExportMode"] = mode
        bid_doc["exportedAt"] = now_iso()
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        paths = [Path(result["path"]) for result in results if result.get("path")]
        if len(paths) != len(results) or any(not path.exists() for path in paths):
            return {
                "files": [],
                "mode": mode,
                "error": "导出失败：未生成文件",
                "copiedTemplateCount": copied_template_count,
                "exportWarnings": export_warnings,
                "bidDocument": bid_doc,
            }
        files = [
            {"name": path.name, "url": f"/runtime/projects/{project_id}/outputs/{path.name}"}
            for path in paths
        ]
        archive = output_dir / f"{paths[0].stem}.json"
        write_json(archive, {"mode": mode, "bidDocument": doc_to_export})
        files.append({"name": archive.name, "url": f"/runtime/projects/{project_id}/outputs/{archive.name}"})
        return {
            "files": files,
            "mode": mode,
            "message": f"已按分册规则导出 {len(paths)} 份 Word" if volume_plans else "标书文档已导出",
            "copiedTemplateCount": copied_template_count,
            "exportWarnings": export_warnings,
            "bidDocument": bid_doc,
        }

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

    def compliance_review_workspace(self, workspace_id: str) -> dict[str, Any] | None:
        """执行确定性基础校对 + Pi ComplianceReviewAgent 合规审查。"""
        workspace = self.workspaces.get(workspace_id)
        if not workspace:
            return None
        if not isinstance(workspace.get("bidDocument"), dict):
            self.plan_bid_document(workspace_id)
        bid_doc = workspace.get("bidDocument") or {}
        reasoning = workspace.get("reasoning") if isinstance(workspace.get("reasoning"), dict) else {}
        fine_review = reasoning.get("fineReview") if isinstance(reasoning.get("fineReview"), dict) else {}
        project = workspace.get("project") if isinstance(workspace.get("project"), dict) else {}

        from .bid_writing.compliance import build_coverage_summary
        from .bid_writing.proofread import proofread

        baseline = proofread(bid_doc, fine_review, project, self.company) or {}
        baseline_coverage = build_coverage_summary(self.get_parse_bundle(workspace_id), bid_doc)
        baseline_items = [
            {
                "riskId": f"baseline-{index:03d}",
                "level": str(item.get("level") or "中"),
                "category": "确定性校对",
                "title": str(item.get("title") or "基础校对风险"),
                "detail": str(item.get("detail") or ""),
                "sectionId": "",
                "requirementId": "",
                "evaluationId": "",
                "sourceLocator": "",
                "suggestion": "请根据基础校对结果人工核对并处理。",
                "sourceRefs": [],
            }
            for index, item in enumerate(baseline.get("riskItems") or [], start=1)
            if isinstance(item, dict)
        ]
        provider = ""
        warnings: list[str] = []
        agent_review: dict[str, Any] = {}
        try:
            from .cloud_agent.config import DifyConfig

            provider = DifyConfig.from_env().provider
            if provider == "pi":
                self._refresh_company_from_m3(force=True)
                from .pi_agent.client import PiClient
                from .pi_agent.writing_orchestrator import PiWritingOrchestrator

                agent_review = PiWritingOrchestrator(PiClient()).review_compliance(
                    parse_bundle=self.get_parse_bundle(workspace_id) or {},
                    bid_document=bid_doc,
                    fine_review=fine_review,
                    baseline_review=baseline,
                    project=project,
                    company={**(self.company or {}), "organization": self.organization or {}},
                    knowledge=self.company_knowledge or {},
                    workspace_id=workspace_id,
                    project_id=str(project.get("id") or workspace.get("projectId") or ""),
                )
            else:
                warnings.append(f"当前智能体提供方为 {provider or 'unknown'}，未执行 Pi 合规审查。")
        except Exception as exc:
            provider = provider or "pi"
            warnings.append(f"Pi 合规审查失败，已保留确定性校对结果：{str(exc)[:500]}")

        if agent_review:
            coverage = dict(baseline_coverage)
            agent_coverage = agent_review.get("coverage")
            if isinstance(agent_coverage, dict):
                for key, value in agent_coverage.items():
                    if key == "contentSectionIds":
                        continue
                    if isinstance(value, list):
                        if value:
                            coverage[key] = value
                    elif isinstance(value, int):
                        if value > 0 or coverage.get(key, 0) == 0:
                            coverage[key] = value
            risk_items = list(agent_review.get("riskItems") or []) + baseline_items
            review_status = str(agent_review.get("status") or "needs_review")
            review = {
                **agent_review,
                "status": "needs_review" if baseline_items and review_status == "pass" else review_status,
                "coverage": coverage,
                "riskItems": risk_items,
                "baselineRiskItems": baseline_items,
                "baselineProofread": baseline,
                "warnings": list(dict.fromkeys(warnings + list(agent_review.get("warnings") or []))),
                "reviewedAt": now_iso(),
            }
        else:
            review = {
                "status": "needs_review" if baseline_items else "pass",
                "summary": "当前仅完成确定性基础校对，请在 Pi 合规审查可用后再次执行。" if warnings else "未发现基础校对风险。",
                "coverage": baseline_coverage,
                "riskItems": baseline_items,
                "baselineRiskItems": baseline_items,
                "baselineProofread": baseline,
                "warnings": warnings,
                "provider": "local_fallback" if warnings else "local",
                "phase": "compliance_review",
                "agentResults": [],
                "reviewedAt": now_iso(),
            }
        bid_doc["proofread"] = baseline
        bid_doc["complianceReview"] = review
        workspace["bidDocument"] = bid_doc
        self.persist_runtime()
        return {"bidDocument": bid_doc, **review}

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
