"""Manual expected semantics over real source excerpts, never a mock PI output."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from backend.app.agent_runtime.resolved_tender import RESOLVED_TENDER_V2, ResolvedTenderV2


def digital_twin_v2() -> dict[str, Any]:
    fixture = json.loads(Path(__file__).with_name("digital_twin_v2_sources.json").read_text(encoding="utf-8"))
    units = fixture["units"]
    blocks = {unit["id"]: unit for unit in units}
    source_file = fixture["sourceFile"]

    def trace(block_id: str, quote: str) -> list[dict[str, Any]]:
        block = blocks[block_id]
        return [{
            "segmentId": block_id, "quote": quote,
            "fileId": source_file["fileId"], "documentVersionId": source_file["sha256"],
            **{key: block[key] for key in ("sourceLocator", "paragraphIndex", "bodyChildIndex", "tableId", "rowIndex", "cellIndex") if key in block},
        }]

    def requirement(item_id: str, block_id: str, quote: str, **fields: Any) -> dict[str, Any]:
        return {
            "requirementId": item_id, "text": quote, "actor": "bidder", "responseRequired": True,
            "responseStage": "bid_submission", "applicability": "required", "parseConfidence": 1.0,
            "procurementPackageId": "1", "sourceTrace": trace(block_id, quote), **fields,
        }

    agency_text = blocks["SEG-000368"]["text"]
    evaluator_text = blocks["SEG-000370"]["text"]
    license_text = blocks["SEG-000379"]["text"]
    screenshot_text, rendering_text, course_text = blocks["SEG-000547"]["text"].split("\n")
    internal_text, explanation_text, cost_text = blocks["SEG-000515"]["text"].split("\n")
    discount_text, manufacturer_text, declaration_text = blocks["SEG-000524"]["text"].split("\n")
    delivery_text = blocks["SEG-000553"]["text"]
    requirements = [
        requirement("qualification-0001", "SEG-000368", agency_text, actor="agency", responseRequired=False,
                    responseStage="not_applicable", category="procurement_process", responseMode=["no_response"]),
        requirement("qualification-0002", "SEG-000370", evaluator_text, actor="evaluator", responseRequired=False,
                    responseStage="not_applicable", category="review_process", responseMode=["no_response"]),
        requirement("qualification-0006", "SEG-000379", license_text, category="qualification",
                    applicability="conditional", condition="投标人为企业或事业单位（本样本只标注这两个实体类型）",
                    responseMode=["evidence_attachment"], evidenceRequirementIds=["ev-license", "ev-institution"]),
        requirement("technical-0012", "SEG-000547", screenshot_text, category="technical_parameter",
                    responseMode=["response_table", "evidence_attachment"], evidenceRequirementIds=["ev-screenshots"]),
        requirement("technical-0014", "SEG-000547", rendering_text, category="technical_parameter", responseMode=["response_table"],
                    parameters=[
                        {"name": "同屏设备级数字孪生体数量", "operator": ">=", "value": "100000", "unit": "个", "sourceTrace": trace("SEG-000547", rendering_text)},
                        {"name": "同屏渲染帧率", "operator": ">=", "value": "60", "unit": "fps", "sourceTrace": trace("SEG-000547", rendering_text)},
                    ]),
        requirement("technical-0056", "SEG-000547", course_text, category="delivery", responseMode=["response_table"],
                    parameters=[
                        {"name": "课程视频数量", "operator": ">=", "value": "16", "unit": "个", "sourceTrace": trace("SEG-000547", course_text)},
                        {"name": "教学支撑容量", "operator": ">=", "value": "48", "unit": "教学时", "sourceTrace": trace("SEG-000547", course_text)},
                        {"name": "配套资源", "operator": "includes", "value": "PPT及实训任务指导书", "sourceTrace": trace("SEG-000547", course_text)},
                    ]),
        requirement("price-review-process", "SEG-000515", internal_text, actor="evaluator", responseRequired=False,
                    responseStage="not_applicable", category="review_process", responseMode=["no_response"]),
        requirement("price-clarification", "SEG-000515", explanation_text, category="pricing",
                    responseStage="evaluation_clarification", applicability="conditional", condition="评审委员会启动异常低价审查并要求解释",
                    responseMode=["manual_confirmation", "evidence_attachment"], evidenceRequirementIds=["ev-price-explanation", "ev-cost-material"]),
        requirement("price-deduction", "SEG-000524", declaration_text, category="price_policy",
                    applicability="conditional", condition="申请小微企业价格扣除且货物制造商等条件符合适用政策",
                    responseMode=["evidence_attachment"], evidenceRequirementIds=["ev-sme-declaration"]),
        requirement("commercial-delivery", "SEG-000553", delivery_text, category="delivery", mandatory=True,
                    deviationPolicy="no_negative", responseMode=["response_table"],
                    parameters=[{"name": "合同签订后交付期限", "operator": "<=", "value": "20", "unit": "日", "sourceTrace": trace("SEG-000553", delivery_text)}]),
    ]

    def evidence(item_id: str, requirement_id: str, block_id: str, quote: str, **fields: Any) -> dict[str, Any]:
        return {
            "evidenceRequirementId": item_id, "requirementId": requirement_id, "description": quote,
            "required": True, "applicability": "required", "acquisition": "either",
            "sourceTrace": trace(block_id, quote), **fields,
        }

    evidence_requirements = [
        evidence("ev-license", "qualification-0006", "SEG-000379", license_text, type="license", applicability="conditional",
                 condition="投标人为企业", alternativeGroupId="entity-registration", verificationRules=["有效营业执照复印件"]),
        evidence("ev-institution", "qualification-0006", "SEG-000379", license_text, type="certificate", applicability="conditional",
                 condition="投标人为事业单位", alternativeGroupId="entity-registration", verificationRules=["有效事业单位法人证书复印件"]),
        evidence("ev-screenshots", "technical-0012", "SEG-000547", screenshot_text, type="screenshot", minimumQuantity=4,
                 acquisition="human_upload", verificationRules=["真实系统截图", "加盖投标人公章", "截取视点、编辑解说文本、上传声音、播放模式切换均有展示"]),
        evidence("ev-price-explanation", "price-clarification", "SEG-000515", explanation_text, type="other",
                 applicability="conditional", condition="被要求解释异常低价", verificationRules=["书面说明报价合理性"]),
        evidence("ev-cost-material", "price-clarification", "SEG-000515", cost_text, type="other",
                 applicability="conditional", condition="被要求解释异常低价", verificationRules=["项目具体成本测算及必要证明材料"]),
        evidence("ev-sme-declaration", "price-deduction", "SEG-000524", declaration_text, type="other",
                 applicability="conditional", condition="申请小微企业价格扣除并满足政策", verificationRules=["中小企业声明函", "货物制造商条件应与申报一致"]),
    ]
    evaluation_rules = [
        {"evaluationRuleId": "eval-low-price", "title": "异常低价审查", "text": explanation_text + cost_text,
         "ruleType": "internal_review", "requirementIds": ["price-review-process", "price-clarification"],
         "sourceTrace": trace("SEG-000515", cost_text),
         "parameters": [{"name": "评审现场解释时间", "operator": "=", "value": "30", "unit": "分钟", "sourceTrace": trace("SEG-000515", cost_text)}]},
        {"evaluationRuleId": "eval-sme-discount", "title": "小微企业价格扣除", "text": discount_text + manufacturer_text,
         "ruleType": "price_rule", "requirementIds": ["price-deduction"], "sourceTrace": trace("SEG-000524", discount_text),
         "parameters": [{"name": "符合条件的小微企业价格扣除比例", "operator": "=", "value": "15", "unit": "%", "sourceTrace": trace("SEG-000524", discount_text)}]},
    ]
    source_tables: list[dict[str, Any]] = []
    response_tables: list[dict[str, Any]] = []
    for table in fixture["responseTables"]:
        cells = [{"id": f"{table['id']}-R001-C{position:03d}", "tableId": table["id"], "rowIndex": 1,
                  "cellIndex": position, "sourceLocator": f"{table['id']}第1行第{position}列", "text": label}
                 for position, label in enumerate(table["labels"], 1)]
        row = {"rowIndex": 1, "sourceLocator": f"{table['id']}第1行", "cells": cells, "text": " | ".join(table["labels"])}
        source_table = {"id": table["id"], "tableId": table["id"], "bodyChildIndex": table["bodyChildIndex"], "rows": [row]}
        if "gridWidths" in table:
            source_table["gridWidths"] = table["gridWidths"]
        source_tables.append(source_table)
        blocks[cells[0]["id"]] = {**cells[0], "bodyChildIndex": table["bodyChildIndex"]}
        bindings = requirements[3:6] if table["domain"] == "technical" else requirements[9:10]
        response_tables.append({
            "responseTableId": f"response-{table['domain']}", "title": table["title"], "templateFragmentId": table["templateId"],
            "sourceTableId": table["id"], "domain": table["domain"], "preserveOriginalFormat": True,
            "columns": [{"key": key, "label": label, "writable": position >= 3}
                        for position, (key, label) in enumerate(zip(("package", "item", "requirement", "response", "deviation"), table["labels"]))],
            "rowBindings": [{"requirementId": item["requirementId"], "targetRowIndex": None, "sourceTrace": item["sourceTrace"]} for item in bindings],
            "sourceTrace": trace(cells[0]["id"], cells[0]["text"]),
        })
    volume_items = [
        ("qualification", "资格及资信证明部分", ["投标函", "投标人的资格及资信证明文件", "投标保证金"]),
        ("pricing", "报价部分", ["开标（报价）一览表", "投标（响应）报价明细表", "招标文件规定的价格扣除证明材料（若有）", "招标文件规定的加分证明材料（若有）"]),
        ("technical", "技术商务部分", ["标的说明一览表", "技术和服务要求响应表", "商务条件响应表", "投标人提交的其他资料（若有）", "招标文件规定作为电子投标文件组成部分的其他内容（若有）"]),
    ]
    templates = [("tpl-qualification-01", "投标函"), ("tpl-pricing-01", "开标（报价）一览表"),
                 ("tpl-pricing-02", "投标（响应）报价明细表"), ("tpl-technical-01", "标的说明一览表"),
                 ("tpl-technical-02", "技术和服务要求响应表"), ("tpl-technical-03", "商务条件响应表")]
    tender = {
        "schemaVersion": RESOLVED_TENDER_V2, "parseBundleId": "acceptance-digital-twin-subset",
        "projectId": fixture["fixtureMeta"]["sourceProjectId"], "workspaceId": fixture["fixtureMeta"]["sourceWorkspaceId"],
        "sourceFile": source_file, "projectMeta": {"projectName": "数字孪生应用实训平台采购"},
        "documentBlocks": {"units": units, "tables": source_tables},
        "submissionStructure": {"sourceLocator": "10.2电子投标文件由下述部分组成：", "confidence": "explicit", "structureStatus": "valid",
                                "volumes": [{"volumeId": volume_id, "title": title,
                                             "items": [{"formatItemId": f"{volume_id}-{position:02d}", "title": item_title} for position, item_title in enumerate(titles, 1)]}
                                            for volume_id, title, titles in volume_items]},
        "templateFragments": [{"templateId": template_id, "title": title, "fragmentType": "fixed_template",
                               "tables": [deepcopy(source_tables[0])] if template_id == "tpl-technical-02" else [deepcopy(source_tables[1])] if template_id == "tpl-technical-03" else []}
                              for template_id, title in templates],
        "requirements": requirements, "evidenceRequirements": evidence_requirements,
        "evaluationMethod": "lowest_evaluated_price", "evaluationMethodSourceTrace": trace("SEG-000510", blocks["SEG-000510"]["text"]),
        "evaluationRules": evaluation_rules, "responseTableSchemas": response_tables,
    }
    return ResolvedTenderV2.model_validate(tender).model_dump(mode="json")
