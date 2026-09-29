# -*- coding: utf-8 -*-
"""字段检阅清单生成器。

输入：backend/migrations/001_schema_v1.sql （唯一权威 DDL）
输出：
  docs2/表字段检阅清单.md    —— 便于从头到尾阅读
  docs2/表字段检阅清单.csv   —— 便于在 Excel 里逐行批注、筛选

设计意图：683 个字段靠手写清单必然漏。这里用「从 DDL 精确解析 + 字段名→中文含义字典」
的方式生成，保证：字段不遗漏、同名译名一致、与 DDL 永远同步（改 DDL 重跑即可）。
"""
from __future__ import annotations

import csv
import glob
import io
import os
import re
from collections import OrderedDict

DDL = r"E:\code\uinoproject\tenderagent\tender-agent\tender-agent\backend\migrations\001_schema_v1.sql"
MIGRATIONS_DIR = r"E:\code\uinoproject\tenderagent\tender-agent\tender-agent\backend\migrations"
OUT_MD = r"E:\code\uinoproject\tenderagent\docs2\表字段检阅清单.md"
OUT_CSV = r"E:\code\uinoproject\tenderagent\docs2\表字段检阅清单.csv"

# ---------------------------------------------------------------- 1. 解析 DDL

# 表级约束的续行/关键字，不是字段定义
CONSTRAINT_STARTS = re.compile(
    r"^(UNIQUE|PRIMARY KEY|FOREIGN KEY|CHECK|CONSTRAINT|REFERENCES|OR|AND)\b", re.I)

# 列类型：支持「double precision」这类双词类型，以及 text[] / numeric(18,2)
TYPE_RE = r"[A-Za-z_][\w]*(?:\s+precision)?(?:\s*\([^)]*\))?(?:\[\])?"


def _make_col(col: str, typ: str, rest: str) -> dict:
    """把「列名 类型 剩余修饰」解析成统一结构（CREATE 与 ALTER 共用）。"""
    note = ""
    if "--" in rest:
        note = rest.split("--", 1)[1].strip()
        rest = rest.split("--", 1)[0]
    return {
        "col": col,
        "type": typ,
        "nullable": "NOT NULL" not in rest.upper(),
        "notnull": "NOT NULL" in rest.upper(),
        "has_default": bool(re.search(r"DEFAULT", rest, re.I)),
        "has_check": "CHECK" in rest.upper(),
        "ref": (lambda r: f"{r.group(1)}({r.group(2)})" if r else "")(
            re.search(r"REFERENCES\s+(\w+)\s*\(([^)]*)\)", rest, re.I)),
        "ddl_note": note,
    }


def parse_ddl(path: str):
    sql = open(path, encoding="utf-8").read()
    tables: "OrderedDict[str, list[dict]]" = OrderedDict()
    constraints: "OrderedDict[str, list[str]]" = OrderedDict()

    for m in re.finditer(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)\s*\((.*?)\n\);", sql, re.S):
        tname, body = m.group(1), m.group(2)
        cols: list[dict] = []
        cons: list[str] = []
        for raw in body.split("\n"):
            line = raw.strip().rstrip(",")
            if not line or line.startswith("--"):
                continue
            if CONSTRAINT_STARTS.match(line):
                cons.append(" ".join(line.split()))
                continue
            cm = re.match(rf"^(\w+)\s+({TYPE_RE})\s*(.*)$", line)
            if not cm:
                continue
            cols.append(_make_col(cm.group(1), cm.group(2), cm.group(3)))
        tables[tname] = cols
        constraints[tname] = cons

    # 003 起用 ALTER TABLE ... ADD COLUMN 补列：合并进同一张表
    for m in re.finditer(r"ALTER TABLE(?: IF EXISTS)?\s+(\w+)\s*(.*?);", sql, re.S):
        tname, body = m.group(1), m.group(2)
        cols = tables.setdefault(tname, [])
        existing = {c["col"] for c in cols}
        for cm in re.finditer(
                rf"ADD\s+COLUMN(?: IF NOT EXISTS)?\s+(\w+)\s+({TYPE_RE})\s*([^\n,]*)",
                body, re.I):
            col = cm.group(1)
            if col in existing:
                continue
            cols.append(_make_col(col, cm.group(2), cm.group(3).strip()))
            existing.add(col)

    views = [m.group(1) for m in re.finditer(r"CREATE OR REPLACE VIEW\s+(\w+)", sql)]
    # 被后续迁移 DROP 掉的表，不出现在最终清单里
    dropped = {m.group(1).lower()
               for m in re.finditer(r"DROP TABLE(?: IF EXISTS)?\s+(\w+)", sql, re.I)}
    return tables, constraints, views, dropped


# ------------------------------------------------- 2. 字段名 → 中文含义字典
# 跨表通用的 5 列（在阅读指南里统一说明，逐表不再重复展开）

COMMON = {
    "id": "数据库内部自增主键（仅供表间关联，业务与前端不使用）",
    "biz_id": "业务编号（沿用现有字符串 ID，如 SEG-000001 / technical-0001；前端与标书侧引用的是它）",
    "org_id": "企业编号（多企业预留，当前固定 org-001）",
    "created_at": "记录写入数据库的时间",
    "updated_at": "记录最后更新时间",
}

NAME_CN: dict[str, str] = {
    # ---- 同步与通用 ----
    "sync_batch_id": "企业事实同步批次号（这条事实来自哪一次 M3 同步）",
    "synced_at": "该事实从 M3 同步过来的时间",
    "fact_sync_batch_id": "本次匹配所依据的企业事实批次（结论可复现的关键）",
    "source": "数据来源",
    "namespace": "M3 命名空间（tenderhub）",
    "started_at": "开始时间",
    "finished_at": "结束时间",
    "m3_reachable": "同步当时 M3 图库是否可连通",
    "object_counts": "各类对象同步条数（按类统计）",
    "warnings": "警告信息（不阻断流程）",
    "errors": "错误信息（阻断流程的）",
    "note": "备注",
    "retry_count": "重试次数",
    "elapsed_ms": "耗时（毫秒）",
    "status": "状态",
    "title": "标题",
    "version": "版本号",
    "level": "等级",
    "name": "名称 / 姓名",
    "description": "描述 / 说明",
    "category": "类别",
    "region": "地区",
    "industry": "所属行业",
    "role": "承担角色",
    "detail": "详细信息",

    # ---- A 组 企业事实 ----
    "organization_id": "所属企业编号（→ organization.biz_id）",
    "project_id": "所属项目编号（→ tender_project.biz_id）",
    "bid_document_id": "所属标书文档编号（→ bid_document.biz_id）",
    "organization_name": "企业名称",
    "organization_type": "企业类型（有限责任公司 / 股份有限公司 …）",
    "unified_social_credit_code": "统一社会信用代码",
    "legal_representative": "法定代表人",
    "registered_address": "注册地址",
    "contact_info": "联系方式",
    "official_site": "官网地址",
    "aliases": "别名 / 简称（用于名称模糊匹配）",
    "employee_count": "从业人员数",
    "annual_revenue_wan": "年营业收入（万元）",
    "total_assets_wan": "资产总额（万元）",
    "enterprise_size": "企业规模（大型 / 中型 / 小型 / 微型）",
    "management_policies": "管理制度（企业已建立的管理体系，文本）",
    "capabilities": "能力清单（顿号 / 逗号分隔的能力词）",
    "service_scope": "服务范围",
    "product_scope": "产品范围",
    "region_scope": "区域范围（可服务的地区）",
    "industry_scope": "行业范围",
    "scale_value": "规模数值",
    "scale_unit": "规模单位",
    "qualification_type": "资质类型（行业资质 / 行政许可 …）",
    "qualification_name": "资质名称",
    "certificate_no": "证书编号",
    "issuer": "发证机构",
    "scope": "适用范围 / 许可范围",
    "issue_date": "发证日期",
    "valid_from": "有效期起始日",
    "valid_until": "有效期截止日（判断证书是否过期的关键字段）",
    "verification_status": "核验状态（未核验 / 已核验有效 / 已核验过期 / 待人工确认）",
    "credential_type": "凭证类型（专利 / 软著 / 商标 / 检测报告 / 产品认证 …）",
    "credential_name": "凭证名称",
    "registration_no": "登记号",
    "owner": "权利人",
    "standard_no": "标准号",
    "product_id": "所属产品编号",
    "case_name": "业绩 / 案例名称",
    "customer_name": "客户名称（业主单位）",
    "contract_amount": "合同金额（单位：元）",
    "currency": "币种",
    "signed_date": "合同签订日期",
    "completion_date": "项目完成 / 验收日期（业绩时间要求靠它判断）",
    "acceptance_status": "验收状态（accepted 已验收 / ongoing 进行中 / pending 待验收）",
    "contact_name": "联系人",
    "staff_type": "人员类型（项目负责人 / 技术负责人 / 一般人员）",
    "professional_certificates": "专业证书（文本，如「一级建造师」）",
    "years_of_experience": "从业年限",
    "employment_start_date": "入职日期",
    "specialty": "专业方向",
    "current_status": "当前状态（在职 / 离职）",
    "resume_summary": "履历摘要",
    "evidence_type": "证明类型（审计报告 / 纳税证明 / 银行资信 …）",
    "period_start": "财务期间起始",
    "period_end": "财务期间截止",
    "issued_by": "出具机构（会计师事务所等）",
    "revenue": "营业收入（单位：元）",
    "asset_amount": "资产总额（单位：元）",
    "credit_level": "信用等级",
    "evidence_path": "证据文件夹相对路径（该事实的证明材料存在哪）",
    "evidence_updated_at": "证据最后更新时间",
    "product_name": "产品名称",
    "product_type": "产品类型",
    "brand": "品牌",
    "model": "型号",
    "applicable_scenario": "适用场景",
    "relative_path": "文件相对路径（相对 runtime/，前端可用 /runtime/{path} 直接访问）",
    "file_name": "文件名",
    "mime_type": "文件类型（MIME）",
    "size_bytes": "文件大小（字节）",
    "sha256": "文件内容哈希（判断文件是否被改动）",
    "fact_kind": "归属的事实类（qualification_certificate / performance_case / …）",
    "fact_biz_id": "归属的事实编号（→ 对应事实表的 biz_id）",

    # ---- B 组 招标信息与文件 ----
    "site_id": "来源网站编号（→ source_site.biz_id）",
    "site_name": "网站名称",
    "site_type": "网站类型（公共资源交易 / 政府采购 / 第三方付费）",
    "base_url": "网站首页地址",
    "access_mode": "访问方式（公开 / 需登录 / 付费）",
    "crawl_frequency": "抓取频率",
    "last_success_at": "最近一次抓取成功时间",
    "project_name": "项目名称",
    "notice_type": "公告类型（招标公告 / 中标公告 / 采购公告）",
    "published_date": "公告发布日期",
    "source_url": "列表页地址",
    "detail_url": "公告详情页地址",
    "source_key": "来源站点标识（如 government-procurement）",
    "data_origin": "数据来源方式（public_notice_crawl 爬取 / local_upload 本地上传）",
    "analysis_text": "公告全文（用于相关度打分与项目信息提取）",
    "fetched_at": "抓取时间",
    "relevance_score": "相关度得分（初筛分数，满分 100）",
    "score_version": "打分算法版本（如 relevance-v2）",
    "score_breakdown": "打分明细（各维度得分与依据）",
    "exclude_reason": "被排除的原因（命中排除词等）",
    "next_stage": "下一步动作（人工复核 / 直接分析 / 暂不提醒）",
    "should_analyze": "是否建议进一步分析",
    "search_profile": "当时的检索画像快照（关键词 / 地区 / 排除词 / 业务方向）",
    "dedupe_key": "去重键（同一公告被多个来源抓到只留一条）",
    "dedupe_hash": "去重哈希",
    "reminder_status": "提醒状态（已提醒 / 未提醒）",
    "reminded_at": "提醒发送时间",
    "notification_channels": "通知渠道（页面弹窗 / 企业微信）",
    "follow_up": "是否已跟进",
    "read": "是否已读",
    "announcement_id": "所属公告编号（→ tender_announcement.biz_id）",
    "dimension": "命中维度（businessDirection 业务方向 / industryScenario 行业场景 / product 产品 / capability 能力 / keyword 关键词 / reason 理由 / evidence 依据）",
    "term": "命中的词 / 术语",
    "points": "该维度实得分",
    "max_points": "该维度满分",
    "evidence": "命中依据（原文片段）",
    "project_code": "项目编号",
    "project_type": "项目类型",
    "procurement_method": "采购方式（公开招标 / 竞争性磋商 / 询价 …）",
    "purchaser_name": "采购人",
    "agency_name": "代理机构",
    "industry_tags": "行业标签",
    "budget_amount": "预算金额",
    "project_status": "项目状态",
    "service_type": "服务类型",
    "service_location": "服务地点",
    "service_period": "服务期限",
    "registration_period": "报名时间",
    "bid_deadline": "投标截止时间",
    "tenderer_name": "招标人名称",
    "signature_seal_requirement": "签字盖章要求",
    "joint_venture_requirement": "联合体要求",
    "document_type": "文件类型",
    "document_name": "文件名称",
    "version_no": "版本号（对应 documentVersionId 的 V 段）",
    "document_kind": "文件种类（tender_document 招标文件 / response_document 响应文件 / notice 公告）",
    "issued_at": "文件发出时间",
    "file_path": "文件存储路径（原件落磁盘，库里只存路径）",
    "file_hash": "文件内容哈希（判断文件是否变更）",
    "page_count": "页数",
    "parse_status": "解析状态（pending 待解析 / parsed 已解析 / failed 失败）",
    "parse_method": "解析方式（docx_xml_structured / pdf_text / …）",
    "is_current": "是否当前版本（一个项目多版本时只有一版有效）",
    "acquisition_status": "获取状态",
    "user_id": "操作人",
    "acquisition_mode": "获取方式（manual 人工下载 / upload 上传）",
    "requested_at": "请求获取时间",
    "acquired_at": "实际获取时间",
    "uploaded_at": "上传时间",
    "file_ref": "文件引用标识",
    "operator_note": "操作备注",

    # ---- C 组 解析结果 ----
    "document_id": "所属招标文件编号（→ tender_document.biz_id）",
    "order_no": "在原文中的顺序号",
    "content_type": "内容类型（paragraph 段落 / table_row 表格行）",
    "text": "文本内容",
    "source_locator": "原文位置（如「第130页」「TABLE-018第2行」，用于溯源）",
    "paragraph_index": "段落序号",
    "table_id": "所属表格编号",
    "row_index": "表格内行号",
    "cell_index": "单元格列号",
    "style": "样式信息（styleId / outlineLevel 大纲级别 / 对齐方式）",
    "cells": "该行各单元格内容",
    "is_unit": "是否属于「内容单元」视图（原 units，与 segments 同源，合并存一张表）",
    "parent_biz_id": "父章节编号（构成章节树）",
    "heading": "章节标题",
    "section_type": "章节类型（资格要求 / 技术要求 / 评分办法 / 合同条款 …）",
    "section_type_hits": "章节类型的判定依据",
    "is_mixed": "是否混合类型章节",
    "is_major": "是否主要章节",
    "section_path": "章节路径（从顶层到本级的标题数组）",
    "heading_segment_id": "标题所在段落的编号",
    "start_locator": "章节起始位置",
    "end_locator": "章节结束位置",
    "segment_ids": "该章节包含的段落编号列表",
    "implicit": "是否隐式章节（原文无显式标题、由系统推断）",
    "row_count": "表格行数",
    "column_count": "表格列数",
    "grid_widths": "各列宽度",
    "rows": "表格全部行与单元格内容",
    "section_id": "所属章节编号",

    # ---- D 组 提取结果 ----
    "group_key": "分组标识（technical / qualification / commercial / evaluation / contract / template / project_info / rejection）",
    "group_name": "分组中文名",
    "primary_route": "主路由（company_match 企业匹配 / rejection_check 否决检查 / display 仅展示）",
    "handling_mode": "处理方式（match 需匹配 / database_match 查库匹配 / manual 人工 / semantic 语义）",
    "group_no": "分组编号",
    "item_count": "组内要求条数",
    "source_page": "来源页码",
    "target_module": "模块名（实测 8 个值：technical 技术 / qualification 资格 / commercial 商务 / evaluation 评分 / contract 合同 / template 模板 / project_info 项目信息 / rejection 废标）",
    "target_module_label": "模块中文名",
    "requirement_class": "条款性质（evidence 需证据 / rejection 废标 / writing 需撰写 / compliance 合规声明 / fill 填空）",
    "match_domain": "匹配域",
    "raw_text": "要求原文（未加工）",
    "normalized_text": "归一化后的要求文本",
    "source_text": "来源文本",
    "mandatory": "是否强制项（★号实质性条款）",
    "is_rejection": "是否废标 / 否决类条款",
    "importance": "重要程度",
    "decision_impact": "对投标决策的影响（high / medium / low）",
    "source_segment_id": "来源段落编号（首段）",
    "source_segment_ids": "来源段落编号列表（一条要求可能跨多段）",
    "table_ids": "来源表格编号列表",
    "target_type": "目标类型（产品 / 服务 / 工程）",
    "source_column": "来源表格的列",
    "quantity": "数量",
    "unit": "单位",
    "parameters": "技术参数点（技术类要求逐条拆分后的正文，偏离表与正文必须读它）",
    "requirement_number": "条款编号（原文自带编号，如 1.2.3）",
    "clause_index": "条款序号（本组内第几条）",
    "clause_count": "本组条款总数",
    "response_type": "响应类型（technical_response 技术响应 / commercial 商务响应 / commitment 承诺函）",
    "evidence_need": "需要什么证据（product_or_capability 产品能力 / certificate 证书 / performance 业绩）",
    "granularity": "拆分粒度（clause 按条款 / parameter 按参数点）",
    "review_required": "是否需要人工复核",
    "review_mode": "复核模式（assisted 辅助复核 / manual 全人工）",
    "review_reason": "为什么需要人工复核",
    "confidence": "置信度（0~1）",
    "extraction_confidence": "提取置信度（原字段名保留）",
    "layer": "提取层级",
    "source_stage": "来源阶段（tender_document）",
    "source_file_id": "来源文件编号",
    "group_id": "所属要求分组编号",
    "criterion_no": "评分项编号",
    "criterion_name": "评分项名称",
    "criterion_type": "评分项类型（商务分 / 技术分 / 价格分）",
    "max_score": "该项满分",
    "scoring_rule": "评分规则原文",
    "required_evidence": "该项需要提供的证明材料",
    "requirement_id": "对应的招标要求编号（→ tender_requirement.biz_id）",
    "segment_id": "段落编号",

    # ---- E 组 匹配结果 ----
    "assessment_id": "所属匹配评估编号（→ match_assessment.biz_id）",
    "run_id": "所属分析运行编号（→ analysis_run.biz_id）",
    "score": "总分",
    "decision": "投标决策（推荐 / 谨慎 / 放弃）",
    "recommendation": "投标建议",
    "clause_count": "条款总数",
    "status_counts": "按匹配状态统计的条数（满足 / 部分满足 / 不满足 / 待补材料 / 待澄清 / 不适用）",
    "class_counts": "按条款性质统计的条数（evidence / rejection / writing / compliance / fill）",
    "evidence_status_counts": "按证据覆盖状态统计的条数",
    "hard_blocker_count": "硬性阻断项数量",
    "rejection_count": "废标 / 否决项数量",
    "rejection_pending_count": "待处理废标项数量",
    "pending_material_count": "待补材料项数量",
    "mandatory_count": "强制项数量",
    "mandatory_closed_count": "已闭环的强制项数量",
    "evidence_required_count": "需要证据的条数",
    "evidence_covered_count": "已覆盖证据的条数",
    "evidence_coverage_rate": "证据覆盖率（%）",
    "uncertain_count": "待澄清条数",
    "not_satisfied_count": "不满足条数",
    "summary": "汇总信息（原 22 字段整体透传给前端）",
    "assessed_at": "评估时间",
    "manual_confirmed": "是否已人工确认",
    "comparison_id": "对标记录编号",
    "requirement": "招标要求正文（前端与标书展示的就是它）",
    "normalized_requirement": "归一化后的要求文本",
    "reason": "判定理由（为什么是满足 / 不满足）",
    "suggestion": "投标建议",
    "status_source": "状态来源（lexical_rule 词法规则 / deterministic 确定性比对 / llm 模型 / manual 人工）",
    "risk": "风险描述",
    "risk_level": "风险等级（低 / 中 / 高）",
    "review_status": "复核状态（待人工复核 / 已人工确认）",
    "evidence_coverage_status": "证据覆盖状态（已核验证据 / 待核验证据 / 仅对象关联 / 未覆盖）",
    "match_item_id": "所属匹配条目（内部主键 → match_item.id）",
    "evidence_id": "命中的证据编号（企业事实 ID，如 prd-49431889-9）",
    "material_id": "材料编号",
    "excerpt": "证据摘录（用于标书正文引证与人工核验）",
    "source_type": "证据来源类型（company_fact 企业事实 / evidence 证明材料）",
    "locator": "证据位置（相对路径）",
    "evidence_level": "证据等级",
    "source_file_ids": "证据文件编号列表",
    "source_files": "证据文件列表",
    "is_historical": "是否历史证据",
    "is_expired": "是否已过期",
    "is_verified": "是否已核验",
    "fact_snapshot": "命中事实的快照（结论可复现）",
    "risk_type": "风险类型（risk 一般风险 / rejection 废标风险 / hard_blocker 硬性阻断）",
    "review_type": "复核类型",
    "reviewer_name": "复核人",
    "from_status": "修改前状态",
    "to_status": "修改后状态",
    "comment": "复核意见",
    "completed_at": "完成时间",

    # ---- F 组 运行与可观测 ----
    "document_version_id": "文件版本编号",
    "stage": "阶段名（parse_document 解析 / eligibility 资格评估 / company_relevance 相关度 / tender_fine_review 细匹配 / extract_requirements 提取 / match_company 企业匹配 / build_decision_summary 汇总）",
    "run_schema_version": "运行记录结构版本",
    "parser_name": "解析器名称",
    "parser_version": "解析器版本",
    "schema_version": "数据结构版本",
    "prompt_version": "提示词版本",
    "llm_model": "使用的模型名",
    "llm_parameters": "模型参数（温度等）",
    "llm_call_count": "模型调用次数",
    "llm_success_count": "模型调用成功次数",
    "llm_failure_count": "模型调用失败次数",
    "llm_input_tokens": "输入 token 数",
    "llm_output_tokens": "输出 token 数",
    "manual_change_count": "人工修改次数",
    "external_agent_call_count": "外部 Agent 调用次数",
    "input_artifact_paths": "输入产物路径",
    "output_artifact_paths": "输出产物路径",
    "metrics": "指标明细",
    "provider": "模型提供方",
    "prompt_chars": "提示词字符数",
    "output_chars": "输出字符数",
    "input_tokens": "输入 token 数",
    "output_tokens": "输出 token 数",
    "success": "是否成功",
    "error": "错误信息",
    "model": "模型名",

    # ---- G 组 标书产出 ----
    "analysis_run_id": "关联的分析运行编号（→ analysis_run.biz_id）",
    "workspace_id": "所属工作区编号（→ workspace.biz_id）",
    "draft_version": "草稿版本号（每次保存 +1）",
    "draft": "标书草稿正文",
    "mode": "模式（open 明标 / blind 暗标）",
    "export_path": "导出文件路径",
    "content": "章节正文",
    "source_refs": "溯源引用（生成该段引用了哪些企业事实 / 证据，防幻觉）",
    "plan": "标书章节规划",

    # ---- 002 新增字段 ----
    "rule_name": "规则名称",
    "enabled": "是否启用",
    "schedule_cron": "采集调度（cron 表达式）",
    "keyword_include": "包含关键词（命中才采）",
    "keyword_exclude": "排除关键词（命中则跳过）",
    "target_regions": "目标地区",
    "target_industries": "目标行业",
    "source_scope": "监控的网站范围（source_site.biz_id 列表）",
    "profile_name": "方案名称",
    "profile_type": "方案类型",
    "threshold_score": "提醒阈值分（低于则不提醒）",
    "hard_block_enabled": "是否启用硬性排除",
    "scoring_version": "评分口径版本（结论可复现）",
    "constraint_name": "约束条件名",
    "constraint_type": "约束类型（region/industry/service_type/amount/exclude_word/…）",
    "operator": "比较运算符（in/not_in/gte/lte/contains/not_contains/regex）",
    "target_value": "目标值",
    "weight": "权重",
    "mandatory": "是否强制条件",
    "explanation": "业务解释（可读）",
    "profile_id": "所属初筛方案编号",
    "stage": "阶段（announcement 公告初筛 / document 文件资格评估）",
    "pass_threshold": "是否过阈值",
    "hard_block": "是否命中硬性排除",
    "eligibility_status": "资格结论（满足/不确定/不满足）",
    "matched_constraint_count": "命中约束条数",
    "total_constraint_count": "约束总条数",
    "reason_summary": "结论理由摘要",
    "headline": "一句话结论",
    "assessment_available": "是否具备评估条件",
    "should_get_tender_document": "是否建议获取招标文件",
    "hard_blockers": "硬性否决项清单",
    "risks": "风险清单（原 rejectionRisks）",
    "result": "检查结果（hit 命中 / miss 未命中 / unknown 未知）",
    "matched_value": "命中值（公告里的实际值）",
    "checked_at": "检查时间",
    "constraint_id": "对应的初筛约束编号",
    "project_title": "项目名称快照",
    "channels": "通知渠道（页内弹窗/企业微信/邮件）",
    "message": "通知内容",
    "priority": "优先级",
    "delivery_status": "投递状态",
    "dedup_key": "去重键",
    "triggered_at": "触发时间",
    "sent_at": "发送时间",
    "read_at": "阅读时间",
    "template_id": "模板编号（TPL-0001）",
    "template_type": "模板类型",
    "section": "所属章节（采购需求所在位置）",
    "download_text": "下载态文本",
    "writing_ready": "是否可写（writing_task 判定）",
    "source_section_id": "来源章节编号",
    "source_section_ids": "来源章节编号列表",
    "child_section_count": "子章节数量",
    "source_item_ids": "来源条目编号列表",
    "extract_mode": "抽取方式",
    "preview_mode": "预览方式",
    "download_mode": "下载方式",
    "field_key": "归一化字段名（projectName/budgetAmount/bidDeadline/…）",
    "value": "抽取到的值（供 projectFields 直读）",
    "matched_object_ids": "命中的企业对象 ID 列表",
    "matched_objects": "命中的企业对象快照",
    "evidence_items": "关联证明材料清单",
    "project_fields": "项目字段（15 键）",
    "technical_match": "技术匹配摘要",
    "technical_gaps": "技术缺口清单",
    "material_gaps": "材料缺口清单",
    "qualification_risks": "资格风险清单",
    "commercial_risks": "商务风险清单",
    "schedule_risks": "进度风险清单",
    "needs_human_review_count": "需人工复核条数",
    "risk_count": "风险总数",
    "trigger_source": "触发来源（manual 手动 / scheduled 定时）",
    "sites_crawled": "已抓取站点数",
    "announcements_found": "发现公告数",
    "announcements_new": "新增公告数",
    "reminders_created": "产生提醒数",
    "table_name": "表名",
    "table_cn_name": "表中文名",
    "domain": "所属域（A企业/B招标/C解析/D提取/E匹配/F运行/G标书/H视图/I初筛/J抽取/K决策/L元数据）",
    "layer": "数据层（fact/tender/parse/extract/match/run/bid/screen/meta）",
    "source_old_path": "对应的旧 JSON 路径（迁移溯源）",
    "is_authoritative": "是否权威源（企业事实表=false，M3 才是权威）",
    "owner": "负责人",
    "column_name": "字段名",
    "column_cn_name": "字段中文名",
    "data_type": "数据类型",
    "value_domain": "值域说明（如 6 态契约）",
    "semantic_type": "语义类型（id/amount/date/status/text/json/enum/bool）",
    "is_business_key": "是否业务键（biz_id）",
    "is_pii": "是否含个人信息（需脱敏）",
    "deprecated": "是否已废弃",
    "from_table": "来源表",
    "from_column": "来源字段",
    "to_table": "目标表",
    "to_column": "目标字段",
    "relation_cn_name": "关系中文名（如「要求分组包含招标要求」）",
    "relation_type": "关系类型（fk 外键 / soft 软引用 / logical 逻辑）",

    # ---- 003 新增字段 ----
    "trigger_terms": "触发的否决词（rejection 条款）",
    "is_commitment": "是否承诺函类（qualification）",
    "label": "中文标签（如「项目名称」）",
    "ignored": "是否忽略（该模块不参与展示 / 匹配，如 contract 模块实测 ignored=true）",
    "matchable": "是否可匹配（是否需要企业匹配）",
    "display_mode": "展示方式",
    "processing_mode": "处理方式",
    "selection_source": "范围选择来源（llm / rule / …）",
    "reviewed_by_llm": "是否已经过 LLM 复核",
    "source_item_count": "来源条款总数",
    "selected_section_count": "选中章节数",
    "selected_segment_count": "选中段落数",
    "selected_table_count": "选中表格数",
    "section_ids": "所属章节编号列表",
    "segment_ids": "所属段落编号列表",
    "table_ids": "所属表格编号列表",
    "expanded_section_ids": "展开的章节编号列表",
    "expanded_segment_ids": "展开的段落编号列表",
    "llm_excluded_segment_ids": "LLM 排除的段落编号列表",
    "review_section_ids": "需复核的章节编号列表",
    "review_segment_ids": "需复核的段落编号列表",
    "ranges": "提取范围明细（rangeId/sectionId/segmentIds/startSegmentId/endSegmentId/sourceLocator）",
}

# 某些字段名在不同表里含义不同，用 (表, 字段) 精确覆盖（优先于 NAME_CN）
TABLE_COLUMN_CN: dict[tuple[str, str], str] = {
    ("tender_requirement", "source"): "来源对象（真实 item 的 source 是对象：segmentId/segmentIds/sourceLocator 等）",
    ("requirement_group", "ranges"): "提取范围明细（targetRanges[].ranges[]：rangeId/segmentIds/sourceLocator/startSegmentId/endSegmentId）",
    # ---- 004 补齐的「标书读取」列 ----
    ("match_item", "technical_alignment"): "技术参数匹配明细（technicalAlignment：coverage/coveredPoints/gaps/matchedUnits[{productId,productName,brand,model,matchedParams,matchedParameterDetails[{parameterId,description,evidencePath}],matchedFragment}]）",
    ("match_item", "gaps"): "未覆盖点列表（rows[].gaps：技术参数对齐里「哪几条要求没被满足」）",
    ("match_item", "kind"): "要求类别标签（rows[].kind：如「技术要求」「商务要求」，多模块用它生成显示标题）",
    ("match_item", "normalized_text"): "归一化正文（rows[].normalizedText：逐条干净正文，与 normalized_requirement 整行原文不同）",
    ("match_item", "source"): "来源对象（rows[].source：sectionId/sectionPath/segmentId/segmentIds/sourceLocator/tableIds，与展平列并存）",
    ("requirement_group", "source_view"): "模块源章节视图（sourceView：sections[{sectionId,title,level,parentId,sectionPath,position,sourceSegmentIds,contentSegmentIds,tableIds,blocks}]，UI+撰写流程用）",
    ("requirement_group", "ignored_ranges"): "被忽略的段落范围（ignoredRanges：[{rangeId,startSegmentId,endSegmentId,segmentIds}]，contract 模块用）",
    ("requirement_group", "template_count"): "模版数量（templateCount）",
    ("requirement_group", "source_items"): "模版段落级源项（sourceItems，细粒度审计用）",
    ("tender_project", "opening_time"): "开标时间（project_info.field=openingTime）",
    ("tender_project", "contact"): "联系方式（project_info.field=contact，主要一条；多条在 tender_requirement）",
    ("tender_project", "bid_bond"): "投标保证金（project_info.field=bidBond）",
    ("tender_project", "bid_validity"): "投标有效期（project_info.field=bidValidity）",
}

# ------------------------------------------- 3. 表级元信息（组 / 中文名 / 来源）

TABLE_META: dict[str, tuple[str, str, str]] = {
    # table: (组, 中文名, 现在对应 JSON 里的位置)
    "fact_sync_log":              ("A 企业事实", "事实同步日志", "（新增，用于记录每次 M3 同步）"),
    "organization":               ("A 企业事实", "企业", "M3 图库 organization"),
    "qualification_certificate":  ("A 企业事实", "资质认证", "M3 qualification_certificate"),
    "credential":                 ("A 企业事实", "证照凭证", "M3 credential"),
    "performance_case":           ("A 企业事实", "类似业绩", "M3 performance_case"),
    "staff_member":               ("A 企业事实", "项目人员", "M3 staff_member"),
    "financial_evidence":         ("A 企业事实", "财务证明", "M3 financial_evidence"),
    "technical_product":          ("A 企业事实", "技术产品", "M3 technical_product"),
    "technical_parameter":        ("A 企业事实", "技术参数", "M3 technical_parameter"),
    "evidence_file":              ("A 企业事实", "证据文件登记", "runtime/libraries/company/证明材料/**（目录约定 → 表）"),
    "source_site":                ("B 招标与文件", "招标来源网站", "（新增，原散在 crawlMeta.siteId/siteName）"),
    "tender_announcement":        ("B 招标与文件", "招标公告", "runtime/catalog.json 一条公告"),
    "announcement_match_term":    ("B 招标与文件", "公告命中术语", "catalog 里的 matchedXXX 数组（拆表）"),
    "tender_project":             ("B 招标与文件", "采购项目", "workspace.project + project.parsed"),
    "tender_document":            ("B 招标与文件", "招标文件", "workspace.project.document 的头部信息 + projects/*/source/ 文件"),
    "document_acquisition":       ("B 招标与文件", "文件获取记录", "（新增，记录人工下载 / 上传）"),
    "document_segment":           ("C 解析结果", "文档内容单元", "workspace.project.document.segments + .units"),
    "document_section":           ("C 解析结果", "文档章节", "workspace.project.document.sections"),
    "document_table":             ("C 解析结果", "文档表格", "workspace.project.document.tables"),
    "requirement_group":          ("D 提取结果", "提取模块（对应 targetModules 的一个模块；含 ignored/ranges 等模块级元信息）", "analysisPack.targetModules"),
    "tender_requirement":         ("D 提取结果", "招标要求（原子条款）", "analysisPack.matchStageRequirements / extraction.requirements"),
    "requirement_segment":        ("D 提取结果", "要求↔段落关联", "requirement.sourceSegmentIds（拆成关联表）"),
    "evaluation_criterion":       ("D 提取结果", "评审评分项", "analysisPack 里的评分项"),
    "match_assessment":           ("E 匹配结果", "匹配评估（一次汇总）", "reasoning.fineReview 根级字段 + summary"),
    "match_item":                 ("E 匹配结果", "逐条匹配结果", "reasoning.fineReview.rows[]（前端唯一认的行契约）"),
    "match_item_evidence":        ("E 匹配结果", "命中证明材料明细", "rows[].evidenceItems[]"),
    "match_item_fact":            ("E 匹配结果", "命中的企业事实", "rows[].matchedObjectIds[]（拆成关联表）"),
    "match_risk":                 ("E 匹配结果", "风险清单", "fineReview.risks / rejectionRisks / hardBlockers"),
    "review_record":              ("E 匹配结果", "人工复核记录", "workspace.auditRecords[]"),
    "analysis_run":               ("F 运行与可观测", "分析运行记录", "projects/*/runs/run-*.json 头部"),
    "analysis_run_stage":         ("F 运行与可观测", "运行阶段明细", "run-*.json 的 stages[]"),
    "llm_call_log":               ("F 运行与可观测", "模型调用日志", "（新增，用于成本核算）"),
    "workspace":                  ("G 标书产出", "工作区", "workspaces.json 的 key + 头部"),
    "workspace_draft":            ("G 标书产出", "标书草稿版本", "workspace.draft + draftVersion"),
    "bid_document":               ("G 标书产出", "标书文档", "workspace.bidDocument"),
    "bid_section":                ("G 标书产出", "标书章节内容", "bidDocument.sections / runtime/bid_writing/*"),
    "bid_plan":                   ("G 标书产出", "标书章节规划", "workspace.bidPlan"),
    # ---- 002 新增 ----
    "monitoring_rule":            ("I 初筛与提醒", "公告采集规则", "（新增：原 config.monitoring 抓取配置落库）"),
    "screening_profile":          ("I 初筛与提醒", "企业初筛方案", "（新增：检索画像/阈值口径）"),
    "screening_constraint":       ("I 初筛与提醒", "初筛约束条件", "（新增：include/exclude/target* 数组拆成可解释条件）"),
    "screening_assessment":       ("I 初筛与提醒", "初筛/资格评估", "reasoning.eligibility + catalog 的 relevanceScore/scoreBreakdown"),
    "screening_check":            ("I 初筛与提醒", "初筛逐条检查", "（新增：让打分可逐条解释）"),
    "reminder":                   ("I 初筛与提醒", "提醒/通知事件", "runtime/reminders.json"),
    "document_template":          ("J 抽取补齐", "投标格式模板", "analysisPack.templates / templateIndex（实测 277 项/项目）"),
    "project_info_item":          ("J 抽取补齐", "项目信息抽取项", "analysisPack.projectInfo + decisionSummary.projectFields"),
    "procurement_need":           ("J 抽取补齐", "采购需求预关联", "reasoning.procurementNeeds"),
    "decision_summary":           ("K 决策与运行", "决策摘要", "reasoning.decisionSummary（前端决策摘要面板唯一来源）"),
    "automation_run":             ("K 决策与运行", "自动化运行记录", "runtime/automation-runs.json"),
    "meta_table":                 ("L 治理元数据", "表级元数据（数据字典）", "（新增：数据字典落成 PG 可查）"),
    "meta_column":                ("L 治理元数据", "字段级元数据", "（新增：agent 可直接查字段语义）"),
    "meta_relationship":          ("L 治理元数据", "关系级元数据", "（新增：外键/软引用语义）"),
}

VIEW_META = {
    "v_match_item_full": ("H 派生视图", "逐条匹配结果完整视图",
                          "拼回 fineReview.rows 的原始 JSON 结构（含 matchedObjectIds / evidenceItems）；对拍验收基准"),
    "v_parameter_match": ("H 派生视图", "技术参数匹配投影",
                          "原 bidDocument.parameterMatches（技术域）"),
    "v_qualification_match": ("H 派生视图", "资质审核匹配投影",
                              "原 bidDocument.qualificationMatches"),
    "v_deviation_row": ("H 派生视图", "对标偏离表",
                        "原 bidDocument.deviationTables（偏离类型映射的唯一来源）"),
}

# 状态标注：该字段在现有 JSON 里是否已存在
#   沿用 = 现有数据里就有，迁移即搬
#   新增 = 原方案没有，本次为可查询 / 可审计而加
NEW_FIELDS = {
    ("fact_sync_log", "*"), ("evidence_file", "*"), ("source_site", "*"),
    ("document_acquisition", "*"), ("llm_call_log", "*"),
    ("requirement_segment", "*"), ("match_item_fact", "*"), ("review_record", "*"),
    ("announcement_match_term", "*"),
}
NEW_FIELD_NAMES = {
    "sync_batch_id", "synced_at", "fact_sync_batch_id", "dedupe_hash", "version_no",
    "is_unit", "order_no", "org_id", "sha256", "fact_kind", "fact_biz_id",
    "manual_confirmed", "fact_snapshot", "review_type", "reviewer_name",
    "from_status", "to_status", "comment", "acquisition_status", "object_counts",
    "m3_reachable", "namespace", "retry_count", "prompt_chars", "output_chars",
    "source_refs", "workspace_id", "analysis_run_id", "score_version",
}


def status_of(table: str, col: str) -> str:
    if (table, "*") in NEW_FIELDS:
        return "新增"
    if col in NEW_FIELD_NAMES:
        return "新增"
    return "沿用"


def _cn(table: str, col: str, ddl_note: str) -> str:
    """字段中文含义：表级精确覆盖 > 通用列 > 全局字段名 > DDL 内联注释。"""
    return (TABLE_COLUMN_CN.get((table, col))
            or COMMON.get(col)
            or NAME_CN.get(col)
            or ddl_note
            or "⚠️ 待补中文说明")


def _write_text(path: str, text: str, mode: str = "w") -> str:
    """写文件；被占用（Excel 打开）时自动降级为 <name>.<ext>.更新版 并返回实际写入路径。"""
    try:
        with open(path, mode, encoding="utf-8" if "b" not in mode else None, newline="") as f:
            f.write(text)
        return path
    except PermissionError:
        root, ext = os.path.splitext(path)
        alt = f"{root}.更新版{ext}"
        with open(alt, mode, encoding="utf-8" if "b" not in mode else None, newline="") as f:
            f.write(text)
        print(f"  [!] {os.path.basename(path)} 被 Excel 占用，已另存为 {os.path.basename(alt)}")
        return alt


def main() -> None:
    # 扫描全部迁移文件（001/002/...），保证清单与真实库结构一致
    sql_files = sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))
    tables: "OrderedDict[str, list[dict]]" = OrderedDict()
    constraints: "OrderedDict[str, list[str]]" = OrderedDict()
    views: list[str] = []
    per_file: list[tuple[str, int]] = []
    dropped: set[str] = set()
    dropped_in: list[str] = []
    for fp in sql_files:
        t, c, v, d = parse_ddl(fp)
        n0 = len(tables)
        for k, val in t.items():
            if k in tables:
                # 同一张表在后续迁移里用 ALTER 补了列：按列名去重后追加（追加在末尾，符合 ALTER 语义）
                seen = {col["col"] for col in tables[k]}
                for col in val:
                    if col["col"] not in seen:
                        tables[k].append(col)
                        seen.add(col["col"])
            else:
                tables[k] = val
            constraints.setdefault(k, c.get(k, []))
        for vv in v:
            if vv not in views:
                views.append(vv)
        for dd in d:
            if dd in tables:
                dropped.add(dd)
                dropped_in.append(f"{os.path.basename(fp)}→{dd}")
        per_file.append((os.path.basename(fp), len(tables) - n0))
    print("迁移文件: " + " / ".join(f"{n}(+{k}表)" for n, k in per_file))
    for dd in dropped:
        tables.pop(dd, None)
        constraints.pop(dd, None)
    if dropped:
        print("已按 DROP 移除: " + ", ".join(sorted(dropped)))

    # ---------- CSV ----------
    csv_rows = []
    idx = 0
    for tname, cols in tables.items():
        grp, tcn, src = TABLE_META.get(tname, ("?", tname, "?"))
        for c in cols:
            idx += 1
            cn = _cn(tname, c["col"], c["ddl_note"])
            extra = []
            if c["ref"]:
                extra.append(f"外键→{c['ref']}")
            if c["has_check"]:
                extra.append("带 CHECK 约束")
            if c["nullable"]:
                extra.append("可空")
            if c["ddl_note"] and c["ddl_note"] != cn:
                extra.append(c["ddl_note"])
            csv_rows.append({
                "序号": idx, "组": grp, "表": tname, "表中文名": tcn,
                "字段": c["col"], "类型": c["type"], "中文含义": cn,
                "现在来自": src, "状态": status_of(tname, c["col"]),
                "备注": "；".join(extra), "你的意见": "",
            })
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=list(csv_rows[0].keys()))
    w.writeheader()
    w.writerows(csv_rows)
    _write_text(OUT_CSV, buf.getvalue(), "w")

    # ---------- Markdown ----------
    L: list[str] = []
    total_fields = sum(len(c) for c in tables.values())
    L.append("# 表字段检阅清单（PostgreSQL schema v1）")
    L.append("")
    L.append("> 由 `backend/migrations/*.sql`（001 + 002 + 003）自动生成，与 DDL 永远同步（改完 DDL 重跑生成器即可）。")
    L.append("> 生成器：`scripts/gen_field_checklist.py`；已支持 `ALTER TABLE ADD COLUMN` 合并与 `DROP TABLE` 剔除，清单反映**最终态**。")
    L.append(f"> 规模：**{len(tables)} 张表 / {total_fields} 个字段 / {len(views)} 个派生视图**。")
    L.append("> 配套可批注版本：`docs2/表字段检阅清单.csv`（Excel 打开，最后一列「你的意见」留空给你写）。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 0. 怎么读这份清单")
    L.append("")
    L.append("**每张表都有这 5 个通用字段，下文逐表不再重复展开：**")
    L.append("")
    L.append("| 字段 | 中文含义 |")
    L.append("|---|---|")
    for k, v in COMMON.items():
        L.append(f"| `{k}` | {v} |")
    L.append("")
    L.append("**「状态」列的含义：**")
    L.append("")
    L.append("| 标记 | 含义 |")
    L.append("|---|---|")
    L.append("| 沿用 | 现有 JSON / 目录里已经有这个数据，迁移就是搬过来 |")
    L.append("| 新增 | 原方案没有，为了让数据可查询、可审计而加的（多为版本、去重、审计、日志类） |")
    L.append("")
    L.append("**字段类型速查：** `text` 长文本 · `varchar(n)` 定长短文本 · `integer` 整数 · "
             "`bigint` 大整数 · `double precision` 小数 · `boolean` 是/否 · `date` 日期 · "
             "`timestamptz` 时间点 · `text[]` 文本数组 · `jsonb` 结构化对象")
    L.append("")
    L.append("---")
    L.append("")
    L.append("## 1. 总览")
    L.append("")
    L.append("| 组 | 表 | 中文名 | 字段数 | 现在来自哪里 |")
    L.append("|---|---|---|---:|---|")
    for tname, cols in tables.items():
        grp, tcn, src = TABLE_META.get(tname, ("?", tname, "?"))
        L.append(f"| {grp} | `{tname}` | {tcn} | {len(cols)} | {src} |")
    L.append("")
    L.append("| 组 | 视图 | 中文名 | 说明 |")
    L.append("|---|---|---|---|")
    for v in views:
        grp, vcn, src = VIEW_META.get(v, ("H 派生视图", v, "?"))
        L.append(f"| {grp} | `{v}` | {vcn} | {src} |")
    L.append("")
    L.append("---")
    L.append("")

    # 逐表
    L.append("## 2. 逐表字段清单")
    L.append("")
    cur_grp = None
    for tname, cols in tables.items():
        grp, tcn, src = TABLE_META.get(tname, ("?", tname, "?"))
        if grp != cur_grp:
            cur_grp = grp
            L.append(f"### {grp}")
            L.append("")
        L.append(f"#### `{tname}` — {tcn}")
        L.append("")
        L.append(f"- **字段数**：{len(cols)}")
        L.append(f"- **现在来自**：{src}")
        if constraints.get(tname):
            L.append(f"- **表级约束**：{' / '.join(f'`{c}`' for c in constraints[tname])}")
        L.append("")
        L.append("| # | 字段 | 类型 | 中文含义 | 状态 | 备注 |")
        L.append("|---:|---|---|---|---|---|")
        for i, c in enumerate(cols, 1):
            cn = _cn(tname, c["col"], c["ddl_note"])
            note = []
            if c["ref"]:
                note.append(f"→{c['ref']}")
            if c["has_check"]:
                note.append("CHECK")
            if c["nullable"]:
                note.append("可空")
            if c["col"] in COMMON:
                note.append("（通用列）")
            L.append(f"| {i} | `{c['col']}` | {c['type']} | {cn} | {status_of(tname, c['col'])} | "
                     f"{' '.join(note)} |")
        L.append("")

    _write_text(OUT_MD, "\n".join(L), "w")

    print(f"表 {len(tables)} / 字段 {total_fields} / 视图 {len(views)}")
    print(f"MD  -> {OUT_MD}")
    print(f"CSV -> {OUT_CSV}")
    missing = [(t, c["col"]) for t, cols in tables.items() for c in cols
               if _cn(t, c["col"], c["ddl_note"]) == "⚠️ 待补中文说明"]
    print(f"缺中文说明的字段: {len(missing)}")
    for t, f in missing:
        print(f"   {t}.{f}")


if __name__ == "__main__":
    main()
