# -*- coding: utf-8 -*-
"""提示词基线生成器：把「当前行为」固化成可逐字节比对的事实。

为什么需要它：
  标书撰写链路的提示词由 `backend/app/pi_agent/prompts.py`（2700+ 行）集中产出，
  任何一次重构（拆分模块、改上下文视图、换 schema 文本）都可能**悄悄改变提示词内容**，
  而提示词一变，模型输出、成本、缓存命中率都会变，现有单测却看不出来。

用法：
    python backend/tests/baselines/generate_prompt_baseline.py            # 写入基线
    python backend/tests/baselines/generate_prompt_baseline.py --check    # 只比对，不改写

`--check` 有差异时退出码为 1，可直接用于 CI；`test_prompt_baseline.py` 就是它的 unittest 包装。

注意：本基线**只覆盖提示词构造函数的返回值**（确定性、无网络）。
调用 Pi 的结果、技能文件内容、纯映射函数（resolve_writing_profile 等）不在基线内。
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BASELINE = Path(__file__).resolve().parent / "prompts_baseline.json"

#: 不纳入基线的公开函数：读磁盘（技能文件会演进）或纯映射（另有单测覆盖）
EXCLUDED = {
    "skill_body",
    "material_keys_for_writing_profile",
    "resolve_writing_profile",
}


def _fixtures() -> dict[str, Any]:
    """构造确定性夹具：结构参照真实 ParseBundle，但体量小、无随机性。"""

    requirements = [
        {
            "id": f"technical-{index:04d}",
            "category": "technical",
            "requirement": f"平台应支持第 {index} 项功能，并提供证明材料。",
            "sourceSection": "第五章 技术和服务要求",
            "sourceLocator": f"5.2.{index}",
            "isMandatory": True,
        }
        for index in range(1, 9)
    ] + [
        {
            "id": "qualification-0001",
            "category": "qualification",
            "requirement": "投标人须具有有效营业执照。",
            "sourceSection": "第二章 投标人资格要求",
            "sourceLocator": "2.1",
            "isMandatory": True,
        }
    ]
    evaluation_items = [
        {
            "id": f"eval-{index:03d}",
            "item": f"评分项 {index}",
            "score": 10 - index,
            "criteria": "方案完整、措施可行、证明材料齐全。",
            "sourceLocator": f"第四章 {index}",
        }
        for index in range(1, 5)
    ]
    document_sections = [
        {
            "sectionId": "chapter-3",
            "title": "第三章 投标人须知",
            "level": 1,
            "sectionType": "instruction",
        },
        {
            "sectionId": "chapter-5",
            "title": "第五章 技术和服务要求",
            "level": 1,
            "sectionType": "requirement",
        },
    ]
    submission_structure = {
        "confidence": "explicit",
        "structureStatus": "valid",
        "sourceLocator": "第三章 10.2",
        "volumes": [
            {
                "volumeId": "qualification",
                "title": "资格及资信证明部分",
                "order": 1,
                "items": [
                    {
                        "formatItemId": "qualification-01",
                        "title": "投标函",
                        "order": 1,
                        "itemType": "fixed_form",
                        "responseType": "fixed_form",
                    }
                ],
            },
            {
                "volumeId": "technical",
                "title": "技术商务部分",
                "order": 2,
                "items": [
                    {
                        "formatItemId": "technical-02",
                        "title": "技术和服务要求响应表",
                        "order": 1,
                        "itemType": "table",
                        "responseType": "table",
                    }
                ],
            },
        ],
    }
    parse_bundle = {
        "parseBundleId": "pb-test-0001",
        "schemaVersion": "1.0",
        "provider": "pi",
        "projectId": "project-test-0001",
        "workspaceId": "workspace-test-0001",
        "projectMeta": {
            "projectName": "数字孪生实训平台采购项目",
            "projectNumber": "TEST-2026-001",
            "tenderName": "某某职业技术学院",
            "budgetAmount": "320万元",
            "deadline": "2026-10-20 09:30",
        },
        "submissionStructure": submission_structure,
        "documentStructure": {"sections": document_sections},
        "requirements": requirements,
        "evaluationItems": evaluation_items,
        "evaluationMethod": {
            "methodName": "最低评标价法",
            "hasSubjectiveScore": False,
            "items": evaluation_items,
        },
        "bidFormatSpec": {
            "formatItems": [
                {"itemId": "qualification-01", "title": "投标函", "required": True},
                {"itemId": "technical-02", "title": "技术和服务要求响应表", "required": True},
            ],
            "requiredAttachments": ["营业执照", "检测报告"],
        },
    }
    section = {
        "sectionId": "technical-narr-01",
        "title": "技术方案",
        "source": "ai",
        "kind": "technical",
        "nodeType": "section",
        "parentSectionId": "technical",
        "level": 2,
        "order": 3,
        "required": False,
        "responseType": "chapter_text",
        "content": "",
        "status": "pending",
        "derived": True,
        "derivedOrigin": "required_by_tender",
        "derivedArchetype": "technical_plan",
        "derivedBasis": "145 条技术条款仅由响应表承载，缺叙述章节。",
        "requirementIds": ["technical-0001", "technical-0002"],
        "evaluationIds": ["eval-001"],
    }
    coverage = [
        {
            "sectionId": "technical-narr-01",
            "requirementId": requirement["id"],
            "title": section["title"],
            "requirement": requirement["requirement"],
            "category": requirement["category"],
            "isMandatory": True,
            "sourceLocator": requirement["sourceLocator"],
        }
        for requirement in requirements[:3]
    ]
    material_matches = {
        "matches": [
            {
                "requirementId": "technical-0001",
                "materialId": "mat-0001",
                "materialName": "数字孪生平台案例",
                "score": 0.87,
                "reason": "同为产教融合实训平台",
                "evidencePath": "evidence/case-0001.pdf",
            }
        ],
        "unmatched": ["technical-0003"],
    }
    project = {
        "projectName": "数字孪生实训平台采购项目",
        "projectNumber": "TEST-2026-001",
        "tenderName": "某某职业技术学院",
        "budgetAmount": "320万元",
    }
    company = {
        "companyName": "某某科技有限公司",
        "qualification": ["高新技术企业", "软件企业认定"],
        "performance": [{"name": "某某学院实训平台", "year": "2024", "amount": "180万元"}],
        "personnel": [{"name": "张三", "title": "项目经理", "certificate": "PMP"}],
    }
    knowledge = {
        "facts": [{"factKey": "org-001", "factId": "org-001", "name": "公司名称", "value": "某某科技有限公司"}],
        "materials": [{"materialId": "mat-0001", "name": "案例合同", "evidencePath": "evidence/case-0001.pdf"}],
    }
    fragment = {
        "fragmentId": "frag-0001",
        "title": "投标函",
        "blocks": [{"blockId": "b1", "text": "致：某某职业技术学院"}],
        "fields": [{"fieldId": "field-003", "label": "投标人名称", "value": ""}],
        "tables": [{"tableId": "t1", "rows": [["项目", "内容"], ["名称", ""]]}],
    }
    fine_review = {
        "rows": [
            {
                "requirementId": "technical-0001",
                "requirement": "平台应支持第 1 项功能",
                "materialId": "mat-0001",
                "materialName": "数字孪生平台案例",
                "judgement": "满足",
                "evidencePath": "evidence/case-0001.pdf",
            }
        ],
        "summary": {"total": 9, "matched": 1},
    }
    return {
        "document_text": "第一章 招标公告\n本项目采购数字孪生实训平台。\n第五章 技术和服务要求\n5.2.1 平台应支持第 1 项功能。",
        "document_payload": {"blocks": [{"type": "paragraph", "text": "第一章 招标公告"}], "pageCount": 120},
        "filename": "tender.pdf",
        "parser_warnings": ["第 12 页表格解析降级"],
        "workspace_id": "workspace-test-0001",
        "project_id": "project-test-0001",
        "project_context": {"projectName": "数字孪生实训平台采购项目"},
        "document_structure": {"sections": document_sections},
        "project_meta": parse_bundle["projectMeta"],
        "requirements": requirements,
        "evaluation_items": evaluation_items,
        "bid_format_spec": parse_bundle["bidFormatSpec"],
        "parse_bundle": parse_bundle,
        "existing_plan": [],
        "outline_plan": [
            {
                "sectionId": "technical-narr-01",
                "title": "技术方案",
                "parentSectionId": "technical",
                "level": 2,
                "nodeType": "section",
                "responseType": "chapter_text",
            }
        ],
        "section": section,
        "coverage": coverage,
        "material_matches": material_matches,
        "writing_plan": {"outline": ["需求理解", "技术路线"], "wordTarget": 1200},
        "project": project,
        "company": company,
        "knowledge": knowledge,
        "mode": "open",
        "writing_profile": "technical",
        "fine_review": fine_review,
        "cloud_parse": {"fineReview": fine_review, "summary": {"requirements": 9}},
        "document_sections": document_sections,
        "baseline_review": {"summary": {"score": 80}},
        "bid_document": {"plan": [section], "status": "generated"},
        "allowed_section_ids": ["technical-narr-01", "technical-02"],
        "allowed_requirement_ids": [item["id"] for item in requirements],
        "allowed_evaluation_ids": [item["id"] for item in evaluation_items],
        "fragment": fragment,
        "current_content": "现有正文内容。",
        "instruction": "把这段改得更具体，并补充指标。",
        "operation": "rewrite",
        "selected_text": "",
    }


def _build_calls() -> tuple[dict[str, str], list[str]]:
    """调用所有提示词构造函数，返回 {函数名: sha256(提示词)} 与跳过清单。"""

    sys.path.insert(0, str(ROOT / "backend"))
    from app.pi_agent import prompts as module

    pool = _fixtures()
    hashes: dict[str, str] = {}
    skipped: list[str] = []
    for name in sorted(dir(module)):
        if name.startswith("_") or name in EXCLUDED:
            continue
        func = getattr(module, name)
        if not inspect.isfunction(func) or func.__module__.split(".")[0] != "app":
            continue
        signature = inspect.signature(func)
        kwargs: dict[str, Any] = {}
        missing: list[str] = []
        for parameter in signature.parameters.values():
            if parameter.kind is not inspect.Parameter.KEYWORD_ONLY:
                continue
            if parameter.name in pool:
                kwargs[parameter.name] = pool[parameter.name]
            elif parameter.default is inspect.Parameter.empty:
                missing.append(parameter.name)
        if missing:
            skipped.append(f"{name}（缺少夹具：{', '.join(missing)}）")
            continue
        try:
            prompt = func(**kwargs)
        except Exception as error:  # pragma: no cover - 夹具不匹配时给出明确原因
            skipped.append(f"{name}（调用失败：{type(error).__name__}: {error}）")
            continue
        hashes[name] = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
    return hashes, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="提示词基线生成/比对")
    parser.add_argument("--check", action="store_true", help="只比对，不改写基线")
    args = parser.parse_args()

    hashes, skipped = _build_calls()
    if args.check:
        if not BASELINE.is_file():
            print(f"❌ 基线文件不存在：{BASELINE}")
            return 1
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        expected = baseline.get("prompts", {})
        changed = [name for name in sorted(expected) if hashes.get(name) != expected[name]]
        added = [name for name in sorted(hashes) if name not in expected]
        removed = [name for name in sorted(expected) if name not in hashes]
        print(f"比对 {len(expected)} 个提示词构造函数：")
        for name in changed:
            print(f"  ❌ 内容变化: {name}")
        for name in added:
            print(f"  ➕ 新增（未在基线）: {name}")
        for name in removed:
            print(f"  ➖ 消失（基线里有）: {name}")
        if changed or added or removed:
            print("基线不一致。若是有意修改提示词，请重新运行本脚本更新基线并在提交信息里说明原因。")
            return 1
        print("✅ 全部一致（逐字节）。")
        return 0

    payload = {
        "说明": "提示词构造函数输出的 sha256 基线；改动提示词后必须重新生成并在提交信息说明原因。",
        "fixtures": "generate_prompt_baseline.py 内置的确定性夹具",
        "prompts": hashes,
        "skipped": skipped,
    }
    BASELINE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"已写入基线：{BASELINE}")
    print(f"覆盖 {len(hashes)} 个函数。")
    for item in skipped:
        print(f"  跳过 {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
