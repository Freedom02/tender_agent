from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from .contracts import build_comparison, normalize_evidence, normalize_requirement, summarize_comparisons
from .eligibility import _is_gateway_timeout, call_openai_compatible
from .requirement_scope import is_form_title, is_placeholder_row, select_requirement_scope


# 单元格段落级拆解：招标文件的技术/资格要求常在单元格内以
# 「一、… / （一）… / 1. 2. 3. / （1）…」多段落罗列，段落边界即条目边界。
_CELL_SECTION_RE = re.compile(rf"^([一二三四五六七八九十百]+)[、.．]\s*(.*)$")
_CELL_SUBSECTION_RE = re.compile(rf"^（([一二三四五六七八九十百]+)）\s*(.*)$")
_CELL_ITEM_RE = re.compile(r"^(\d+)\s*[、.．)]\s*(.*)$")
_CELL_ITEM_NOSEP_RE = re.compile(r"^(\d+)(?=[\u4e00-\u9fff])(.*)$")
_CELL_SUBITEM_RE = re.compile(r"^（(\d+)）\s*(.*)$")


ALLOWED_CATEGORIES = {
    "qualification",
    "performance",
    "personnel",
    "financial",
    "credit",
    "region",
    "compliance",
    "technical",
    "service_plan",
    "commercial",
    "document",
    "pricing",
    "contract",
    "rejection",
    "other",
}

CATEGORY_LABELS = {
    "qualification": "资格审查",
    "performance": "业绩要求",
    "personnel": "人员要求",
    "financial": "财务要求",
    "credit": "信用要求",
    "region": "地域要求",
    "compliance": "符合性审查",
    "technical": "技术要求",
    "service_plan": "服务方案要求",
    "commercial": "商务要求",
    "document": "文件格式",
    "pricing": "报价要求",
    "contract": "合同条款",
    "rejection": "否决条款",
    "other": "其他要求",
}


VALID_SCOPE_SECTION_TYPES = {
    "qualification",
    "compliance",
    "technical",
    "service_plan",
    "commercial",
    "acceptance",
    "evaluation",
    "pricing",
    "document_format",
    "rejection",
    "personnel",
    "performance",
}

NON_REQUIREMENT_SCOPE_TERMS = (
    "投标邀请书",
    "投标邀请",
    "采购邀请",
    "响应邀请",
    "邀请函",
    "项目名称：",
    "项目编号：",
    "采购项目编号：",
    "招标编号：",
    "采购人：",
    "招标人：",
    "代理机构：",
    "联系方式：",
    "联系电话：",
    "项目概况",
    "项目基本情况",
    "采购项目概述",
    "采购范围",
)


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _safe_text(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value or [] if isinstance(item, dict)]


def _source_locator(item: dict[str, Any], index: int) -> str:
    return _safe_text(item.get("sourceLocator"), 160) or f"第{index + 1}段"


def _category_for(text: str) -> str:
    value = _compact(text)
    if any(term in value for term in ("无效投标", "投标无效", "否决", "废标", "拒绝", "不予受理", "不得参加", "视为无效", "行为无效", "按无效")):
        return "rejection"
    if any(term in value for term in ("虚假材料", "拒收")):
        return "rejection"
    if any(term in value for term in ("应表述为", "请填写", "填写“", "在相应的")):
        return "document"
    if any(
        term in value
        for term in (
            "符合性审查",
            "符合性检查",
            "符合性评审",
            "实质性响应",
            "实质性要求",
            "实质性条款",
            "负偏离",
            "重大偏离",
            "响应招标文件要求",
        )
    ):
        return "compliance"
    if any(term in value for term in ("大写金额", "小写金额", "总价金额", "单价汇总金额")):
        return "pricing"
    if any(term in value for term in ("报价", "最高限价", "控制价", "下浮", "折扣", "费用")):
        return "pricing"
    if any(term in value for term in ("合同", "付款", "结算", "履约", "违约", "验收", "安全事故")):
        return "contract"
    if "中标人" in value and any(term in value for term in ("应", "须", "不得", "承担", "有权")):
        return "contract"
    if any(term in value for term in ("投标保证金", "履约保证金", "保证金")):
        return "pricing"
    if any(term in value for term in ("投标文件", "响应文件", "签字", "盖章", "格式", "密封", "装订")):
        return "document"
    if any(
        term in value
        for term in (
            "授权书",
            "授权委托书",
            "身份证明",
            "身份证件",
            "证照复印件",
            "依法缴纳税收",
            "税收缴纳",
            "缴纳社会保障资金",
            "社会保障资金",
            "纳税证明",
            "社保缴纳",
            "法定代表人",
        )
    ):
        return "qualification"
    if "联合体" in value and _has_obligation_signal(text):
        return "qualification"
    if any(term in value for term in ("项目经理", "项目负责人", "驻场", "人员", "团队", "工程师")):
        if any(term in value for term in ("须具有", "须具备", "资格", "证书", "资质", "经验")):
            return "qualification"
        return "service_plan"
    if any(term in value for term in ("类似业绩", "类似项目", "项目业绩", "合同业绩", "案例")):
        return "performance"
    if any(term in value for term in ("资质", "证书", "认证", "许可证", "营业执照", "iso", "itss", "cmmi")):
        return "qualification"
    if any(term in value for term in ("财务报告", "财务状况", "资信证明", "注册资本", "营业收入", "资产总额")):
        return "financial"
    if any(term in value for term in ("信用中国", "失信被执行人", "重大税收违法", "严重违法失信", "重大违法记录")):
        return "credit"
    if any(term in value for term in ("注册地", "本地企业", "福建省内", "属地", "地域")):
        return "region"
    if any(term in value for term in ("证明材料", "证明文件", "资格证明文件", "资格承诺函", "资格及资信")):
        return "qualification"
    if any(term in value for term in ("真实性负责", "真实性、合法性、有效性")):
        return "compliance"
    if any(term in value for term in ("商务", "交付期", "工期", "售后", "质保", "服务期", "投标有效期", "交货地点", "交货条件")):
        return "commercial"
    if any(term in value for term in ("应表述为", "请填写", "填写“", "填报上一年度数据", "本表中进行相应说明", "审查全部招标文件")):
        return "document"
    if "保持一致" in value and any(term in value for term in ("采购包", "品目号", "投标标的", "数量")):
        return "document"
    if any(
        term in value
        for term in (
            "服务方案",
            "项目实施方案",
            "实施方案",
            "服务实施",
            "项目理解",
            "组织方案",
            "质量保障",
            "应急预案",
            "培训方案",
            "运维方案",
            "实施计划",
            "保障措施",
        )
    ):
        return "service_plan"
    if any(
        term in value
        for term in (
            "技术",
            "功能",
            "性能",
            "系统",
            "平台",
            "接口",
            "数据",
            "参数",
            "配置",
            "服务器",
            "规格",
            "性能指标",
            "三维",
            "数字孪生",
            "可视化",
            "智能体",
            "实施方案",
        )
    ):
        return "technical"
    return "other"


def _is_heading(text: str) -> bool:
    value = _safe_text(text, 200)
    return len(value) <= 42 and bool(
        re.match(r"^(?:第?[一二三四五六七八九十]+[章节、.]|\d+(?:\.\d+)*[、.]|[（(]\d+[)）])", value)
    )


def _has_obligation_signal(text: str) -> bool:
    """判断文本是否真正出现义务表达，避免把“供应商”中的“应”误判为要求。"""
    value = _compact(text)
    if not value:
        return False
    if any(
        term in value
        for term in (
            "必须",
            "须",
            "不得",
            "应当",
            "应在",
            "应按",
            "应按照",
            "应提供",
            "应提交",
            "应具有",
            "应具备",
            "应完成",
            "应满足",
            "应符合",
            "应缴",
            "应于",
            "应以",
            "应由",
            "应将",
        )
    ):
        return True
    return bool(re.search(r"(?<!供)应(?!商)", value))


def _is_requirement(text: str) -> bool:
    value = _compact(text)
    if len(value) < 8:
        return False
    if _is_heading(text) and not any(marker in text for marker in ("★", "▲", "※")):
        return False
    terms = (
        "不得",
        "提供",
        "具备",
        "具有",
        "不少于",
        "不低于",
        "不超过",
        "不大于",
        "不小于",
        "至少",
        "响应文件",
        "报价",
        "合同",
        "服务期",
        "交付",
        "验收",
        "方案",
        "计划",
        "预案",
        "保障",
        "偏离",
        "无效投标",
        "投标无效",
        "否决",
        "支持",
        "兼容",
        "满足",
        "符合",
        "参数",
        "性能",
        "指标",
        "规格",
        "精度",
        "范围",
        "响应时间",
        "并发",
        "吞吐",
        "投标有效期",
        "交货期",
        "交付期",
        "履约期限",
        "注册资本",
        "成立年限",
        "联合体",
        "质保",
        "售后",
        "保证金",
        "数量",
        "金额",
        "期限",
        "日历日",
        "工作日",
        "个月",
        "元",
        "万元",
        "国产",
        "进口",
        "≥",
        "≤",
    )
    return _has_obligation_signal(text) or any(term in value for term in terms) or bool(
        re.search(r"(?:\d+(?:\.\d+)?\s*(?:日历日|工作日|天|年|个月|元|万元|%))", text)
    ) or bool(
        re.match(r"^(?:\d+(?:\.\d+)*[、.]|[（(]\d+[)）])", _safe_text(text, 100))
    )


PROCESS_ONLY_TERMS = (
    "质疑投诉",
    "质疑函",
    "质疑时效",
    "监督管理",
    "监督部门",
    "评审专家",
    "评标委员会",
    "评审委员会",
    "评标办法",
    "评分标准",
    "主持人",
    "工作人员",
    "中标候选人名单",
    "中标人的确定",
    "中标候选人",
    "随机抽取",
    "指定媒体",
    "公告期限",
    "采购活动结束",
    "特别提示",
    "请勿遗漏",
    "不得收受投标人",
    "与投标人存在利害关系",
    "主动声明并回避",
    "串联鼓动其他人袒护",
    "评标纪律",
    "开标结束后",
    "资格审查小组",
    "发布更正公告",
    "终止招标并发布终止公告",
    "招标文件进行必要的澄清或修改",
    "进行资格审查",
    "评标应",
    "评标情况",
    "要求保密的资料",
    "全体评委",
    "评委对",
    "终止招标的",
    "主持召开开标会",
    "邀请投标人参加",
    "开标环节",
    "远程参加开标会",
    "开标操作手册",
    "开标过程",
    "项目名称：",
    "项目编号：",
    "采购人：",
    "代理机构：",
)


def _is_process_only_text(text: str) -> bool:
    value = _compact(text)
    if not value:
        return True
    if re.fullmatch(r"[\d.（）()\-—、\s]+", text):
        return True
    if re.fullmatch(r"[\d.（）()\-—、\s]*\d+(?:\.\d+)?条规定的?", text):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)*[.、\-]?", value):
        return True
    if re.fullmatch(r"[（(]\d+[)）]、?", value):
        return True
    if re.fullmatch(r"[①②③④⑤⑥⑦⑧⑨⑩]、?", value):
        return True
    if re.fullmatch(r"\d+(?:\.\d+)*[“\"「]", value):
        return True
    if re.search(r"\d+(?:\.\d+)+[“\"「]$", value):
        return True
    if re.fullmatch(r"(?:第[一二三四五六七八九十百千万零〇两0-9…]+|\d+(?:\.\d+)*)条第?", value):
        return True
    if value.endswith("第") or value.endswith("款第") or value.endswith("点规定"):
        return True
    if value.endswith("的“") or value.endswith('的"'):
        return True
    if value.endswith("的") and any(term in value for term in ("资格审查", "招标文件第四章", "本章第")):
        return True
    if re.fullmatch(r"[①②③④⑤⑥⑦⑧⑨⑩]、?规定处理", value):
        return True
    if re.fullmatch(r"(?:\d+(?:\.\d+)*-?|第[一二三四五六七八九十百千万零〇两0-9]+条)?(?:规定|要求)[:：]?", value):
        return True
    return any(_compact(term) in value for term in PROCESS_ONLY_TERMS)


COMPOUND_MARKER_PATTERN = re.compile(
    r"(?:[（(]\s*[一二三四五六七八九十百千万零〇两0-9]+\s*[）)]|[①②③④⑤⑥⑦⑧⑨⑩]|\d+[、．)]|\d+\.(?!\d))"
)


def _split_requirement_text(text: str, max_length: int = 1200) -> list[str]:
    clean = _safe_text(text, max_length)
    if not clean:
        return []

    markers = list(COMPOUND_MARKER_PATTERN.finditer(clean))
    if len(markers) >= 2:
        prefix = clean[:markers[0].start()].strip(" ：:；;")
        clauses = [
            clean[marker.start() : markers[index + 1].start() if index + 1 < len(markers) else len(clean)]
            .strip(" ；;。")
            for index, marker in enumerate(markers)
        ]
        clauses = [clause for clause in clauses if clause]
        if len(clauses) >= 2:
            if prefix and _is_requirement(prefix):
                clauses.insert(0, prefix)
            return clauses

    semicolon_clauses = [item.strip(" ；;。") for item in re.split(r"[；;]", clean)]
    semicolon_clauses = [item for item in semicolon_clauses if item]
    if len(semicolon_clauses) >= 2 and sum(_is_requirement(item) for item in semicolon_clauses) >= 2:
        return semicolon_clauses
    return [clean]


def _is_template_placeholder(text: str) -> bool:
    value = _compact(text)
    if not value:
        return True
    if is_form_title(text) or is_placeholder_row(text):
        return True
    if any(
        phrase in value
        for phrase in (
            "由投标人填写",
            "由供应商填写",
            "全称并加盖单位公章",
            "全称并加盖公章",
            "请填写项目名称",
            "请填写采购项目名称",
            "（标的名称）",
            "(标的名称)",
            "（企业名称）",
            "(企业名称)",
            "采购文件中明确的所属行业",
            "填写“项目名称”",
            "填写“所投采购包、品目号”",
            "从业人员人",
            "资产总额为万元",
            "（项目名称）",
            "(项目名称)",
            "（单位名称）",
            "(单位名称)",
            "（所投采购包、品目号）",
            "(所投采购包、品目号)",
            "从业人员、营业收入、资产总额填报",
            "填报上一年度数据",
            "联合体协议（若有）",
            "联合体协议(若有)",
        )
    ):
        return True
    if value in {
        "甲方",
        "乙方",
        "住址",
        "电话",
        "传真",
        "开户名称",
        "开户银行",
        "银行账号",
        "账号",
    }:
        return True
    return False


def _is_incomplete_requirement_label(text: str) -> bool:
    clean = str(text or "").strip()
    if not clean or not re.search(r"[：:|]\s*$", clean):
        return False
    label = clean.rstrip(" ：:|")
    if label.endswith(("规定", "要求", "第", "的")) and any(
        marker in label for marker in ("第", "条", "资格审查", "招标文件", "下列")
    ):
        return True
    if _has_obligation_signal(clean):
        return False
    return not bool(
        re.search(r"\d+(?:\.\d+)?\s*(?:日历日|工作日|天|年|个月|元|万元|%)", clean)
    )


def _is_table_requirement_row(source: dict[str, Any], text: str) -> bool:
    if (
        not source.get("tableId")
        or "|" not in text
        or _is_table_header_row(source, text)
        or _is_template_placeholder(text)
    ):
        return False
    cells = [item.strip() for item in text.split("|") if item.strip()]
    if len(cells) < 2 or len(_compact(text)) < 8:
        return False
    table_type = str(source.get("tableRequirementType") or "")
    value = _compact(text)
    if table_type == "account":
        return _has_obligation_signal(text) or any(
            term in value for term in ("缴交", "缴纳", "提交", "办理")
        )
    if any(term in value for term in ("开户名称", "开户银行", "银行账号")):
        return any(term in value for term in ("应按", "应提交", "必须", "不得", "提供", "缴交", "提交"))
    if table_type in {"technical", "qualification", "compliance", "acceptance", "rejection"}:
        return True
    if table_type == "pricing":
        return True
    if table_type in {"commercial", "contract", "document"}:
        return (
            _has_obligation_signal(text)
            or any(
                term in value
                for term in (
                    "质保",
                    "交付",
                    "验收",
                    "期限",
                    "日内",
                    "日历日",
                    "工作日",
                    "万元",
                    "元",
                    "%",
                )
            )
        )
    if any(
        term in value
        for term in (
            "须", "必须", "不得", "提供", "具有", "具备", "支持",
            "满足", "符合", "不接受", "投标无效", "质保", "交付", "验收",
            "期限", "日历日", "工作日", "万元", "元",
        )
    ):
        return True
    return False


def _table_cell_texts(source: dict[str, Any]) -> list[str]:
    cells = source.get("cells")
    if not isinstance(cells, list):
        return []
    # 技术指标单元格可能包含数千字和几十条编号要求，不能复用普通段落的短截断。
    return [_safe_text(cell.get("text"), 20000) for cell in cells if isinstance(cell, dict)]


_REQUIREMENT_CELL_HEADERS = (
    "技术参数",
    "简要技术指标",
    "技术指标",
    "技术要求",
    "技术需求",
    "技术规格",
    "性能参数",
    "参数或功能",
    "主要技术指标",
    "招标要求",
    "检查内容",
    "资格要求",
)


def _requirement_cell_index(table_type: str, header_cells: list[str] | None = None) -> int | None:
    """返回要求内容所在列的索引（cells 列表下标），无明确列布局时返回 None。

    优先按表头关键词动态定位，兼容「序号|品目名称|简要技术指标|单位|数量」与
    「序号|产品名称|参数类别|技术参数」等不同列布局；找不到时回退到按表格类型的固定布局。
    """
    if header_cells:
        for index, cell in enumerate(header_cells):
            compact = _compact(cell)
            if compact and any(term in compact for term in _REQUIREMENT_CELL_HEADERS):
                return index
    if table_type == "technical":
        return 2
    if table_type == "qualification":
        return 2
    if table_type in {"commercial", "acceptance", "rejection"}:
        return 3
    return None


def _classify_cell_paragraph(text: str) -> tuple[str, str, str, str]:
    """对单元格段落做层级分类，返回 (kind, num, title, tail)。

    kind ∈ {大项, 子项, 要求, 子条目, 说明}；大项标题后「：」的内容存入 tail。
    """
    s = text.strip()
    if not s:
        return ("说明", "", "", "")
    match = _CELL_SECTION_RE.match(s)
    if match:
        num, rest = match.group(1), match.group(2).strip()
        title, tail = rest, ""
        for sep in ("：", ":"):
            if sep in rest:
                title, tail = rest.split(sep, 1)
                break
        return ("大项", num, title.strip().strip("：:"), tail.strip().strip("：:"))
    match = _CELL_SUBSECTION_RE.match(s)
    if match:
        return ("子项", match.group(1), match.group(2).strip(), "")
    match = _CELL_ITEM_RE.match(s)
    if match:
        return ("要求", match.group(1), match.group(2).strip(), "")
    match = _CELL_ITEM_NOSEP_RE.match(s)
    if match:
        return ("要求", match.group(1), match.group(2).strip(), "")
    match = _CELL_SUBITEM_RE.match(s)
    if match:
        return ("子条目", match.group(1), match.group(2).strip(), "")
    return ("说明", "", s, "")


def _table_cell_paragraph_clauses(source: dict[str, Any]) -> list[str]:
    """按单元格段落逐条拆解要求，保留原文编号与顺序。

    招标文件的技术/资格要求常在单元格内以「一、… / 1. 2. 3.」多段落罗列，
    段落边界即条目边界；逐段拆解可避免把整段长文本压成一条导致大量遗漏。
    仅在要求单元格存在多个带编号段落时才启用，否则返回空列表由旧逻辑兜底。
    """
    cells = source.get("cells")
    if not isinstance(cells, list) or not cells:
        return []
    table_type = str(source.get("tableRequirementType") or "")
    req_index = source.get("tableRequirementCellIndex")
    if req_index is None:
        req_index = _requirement_cell_index(table_type)
    if req_index is None or req_index >= len(cells):
        return []
    req_cell = cells[req_index]
    if not isinstance(req_cell, dict):
        return []
    paragraphs = req_cell.get("paragraphs")
    if not isinstance(paragraphs, list) or len(paragraphs) < 3:
        return []
    numbered = 0
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        kind, _, _, _ = _classify_cell_paragraph(_safe_text(paragraph.get("text"), 4000))
        if kind in {"要求", "子条目"}:
            numbered += 1
    if numbered < 3:
        return []

    clauses: list[str] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, dict):
            continue
        ptext = _safe_text(paragraph.get("text"), 4000)
        if not ptext:
            continue
        kind, _num, _title, tail = _classify_cell_paragraph(ptext)
        if kind == "大项":
            # 标题尾随的“投标人须提供…截图”等属于实质要求，需保留。
            if tail and _is_requirement(tail):
                clauses.append(tail)
            continue
        if kind == "子项":
            continue
        if kind in {"要求", "子条目"}:
            clauses.append(ptext)
            continue
        # 说明段：仅当含义务/要求信号或长度足够承载具体内容时保留，避免混入纯引导语。
        if _is_requirement(ptext) or len(_compact(ptext)) >= 40:
            clauses.append(ptext)
    return clauses


def _table_requirement_clauses(source: dict[str, Any], text: str) -> list[str]:
    """将表格行按字段语义展开，避免把序号、响应列和要求混成一条。"""
    paragraph_clauses = _table_cell_paragraph_clauses(source)
    if paragraph_clauses:
        return paragraph_clauses

    cells = _table_cell_texts(source)
    table_type = str(source.get("tableRequirementType") or "")
    if not cells:
        return _split_requirement_text(text)

    # 优先使用按表头定位到的要求列，其次回退到按表格类型的固定列布局。
    req_index = source.get("tableRequirementCellIndex")
    if req_index is None:
        req_index = _requirement_cell_index(table_type)

    # Word 表格常见列布局：序号、名称/类型、要求、单位、数量、响应。
    if table_type == "qualification":
        title = cells[1] if len(cells) > 1 else ""
        body = "；".join(item for item in cells[2:] if item and not _is_response_cell(item))
    elif table_type == "technical":
        title = cells[1] if len(cells) > 1 else ""
        body = cells[req_index] if req_index is not None and req_index < len(cells) else (cells[-1] if cells else "")
    elif table_type in {"commercial", "acceptance", "rejection"}:
        title = cells[2] if len(cells) > 2 else (cells[1] if len(cells) > 1 else "")
        body = cells[req_index] if req_index is not None and req_index < len(cells) else (cells[-1] if cells else "")
    else:
        title = cells[1] if len(cells) > 1 else ""
        body = next((item for item in reversed(cells[2:]) if item and not _is_response_cell(item)), "")

    body = body.strip()
    if not body:
        body = next((item for item in reversed(cells) if item), "")
    # 单元格内若只是「一、…/（一）…」章节标题、未附带具体要求，则不算作要求。
    kind, _num, _title, tail = _classify_cell_paragraph(body)
    if kind in {"大项", "子项"} and not tail:
        return []
    clauses = _split_requirement_text(body, max_length=20000)
    prefix = title.strip(" ：:;")
    if prefix and prefix not in body and len(clauses) > 0:
        clauses = [f"{prefix}：{clause}" for clause in clauses]
    return clauses


def _is_response_cell(value: str) -> bool:
    compact = _compact(value)
    return compact in {"投标响应", "响应", "是否偏离", "偏离情况", "符合", "不符合"}


def _table_requirement_type(table: dict[str, Any]) -> str:
    rows = [
        _safe_text(row.get("text"), 1200)
        for row in table.get("rows") or []
        if isinstance(row, dict) and _safe_text(row.get("text"), 1200)
    ]
    value = _compact("\n".join(rows[:3]))
    if "采购标的一览表" in value or (
        "标的名称" in value
        and "数量" in value
        and ("计量单位" in value or "单位" in value)
        and any(term in value for term in ("金额", "最高限价", "报价", "单价"))
    ):
        return "pricing"
    if "合同信息表" in value or "合同联系人" in value or (
        "甲方" in value and "乙方" in value
    ) or (
        "单位名称" in value and "联系人" in value and "联系方式" in value
    ):
        return "contract"
    if "特别提示" in value and any(term in value for term in ("招标文件", "章节条款", "条款号", "编列内容")):
        return "document"
    if "合同条款索引" in value or "条款索引" in value or (
        "条款号" in value and "招标文件合同条款" in value and "投标响应" in value
    ) or (
        sum(
            1
            for term in (
                "联合体具体要求",
                "其他术语解释",
                "甲方提出异议",
                "甲方承担的其他义务",
            )
            if term in value
        ) >= 2
    ):
        return "document"
    if any(term in value for term in ("投标保证金账户", "开户名称", "开户银行", "银行账号")):
        return "account"
    if any(term in value for term in ("资格审查", "资格及资信", "营业执照", "资格承诺函")):
        return "qualification"
    if any(term in value for term in ("投标无效", "否决", "符合性审查", "符合性")):
        return "rejection"
    if any(term in value for term in ("技术和服务要求", "技术参数", "简要技术指标")):
        return "technical"
    if any(term in value for term in ("商务条件", "交货时间", "履约验收", "质保")):
        return "commercial"
    if any(term in value for term in ("报价", "最高限价", "报价明细")):
        return "pricing"
    if any(term in value for term in ("合同签订", "合同条款", "甲方", "乙方")):
        return "contract"
    return ""


def _table_header_cells(table: dict[str, Any]) -> list[str]:
    rows = table.get("rows") or []
    if not rows or not isinstance(rows[0], dict):
        return []
    cells = rows[0].get("cells") or []
    return [_safe_text(cell.get("text"), 80) for cell in cells if isinstance(cell, dict)]


def _annotate_table_context(
    segments: list[dict[str, Any]],
    tables: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    table_list = [
        table for table in tables or []
        if isinstance(table, dict) and str(table.get("tableId") or table.get("id") or "")
    ]
    table_types = {
        str(table.get("tableId") or table.get("id")): _table_requirement_type(table)
        for table in table_list
    }
    table_indexes = {
        str(table.get("tableId") or table.get("id")): _requirement_cell_index(
            _table_requirement_type(table), _table_header_cells(table)
        )
        for table in table_list
    }
    annotated: list[dict[str, Any]] = []
    for segment in segments:
        if not segment.get("tableId"):
            annotated.append(segment)
            continue
        table_id = str(segment.get("tableId"))
        table_type = table_types.get(table_id, "")
        annotated.append(
            {
                **segment,
                "tableRequirementType": table_type,
                "tableRequirementCellIndex": table_indexes.get(table_id),
            }
        )
    return annotated


def _is_table_header_row(source: dict[str, Any], text: str) -> bool:
    if not source.get("tableId") or "|" not in text:
        return False
    cells = [item.strip() for item in text.split("|") if item.strip()]
    if len(cells) < 2:
        return False
    header_cells = {
        "序号",
        "编号",
        "条款号",
        "品目号",
        "采购包",
        "标的名称",
        "货物名称",
        "商务条件",
        "技术参数",
        "技术要求",
        "技术和服务要求",
        "采购需求",
        "投标响应",
        "是否偏离",
        "是否偏离及说明",
        "偏离情况",
        "报价要求",
        "最高限价",
        "数量",
        "计量单位",
        "报价单位",
        "备注",
        "合同条款索引",
        "合同条款响应",
        "条款名称",
        "合同信息表",
        "甲方",
        "乙方",
        "住址",
        "联系人",
        "联系方式",
        "开户名称",
        "开户银行",
        "银行账号",
        "账号",
    }
    normalized_headers = {_compact(item) for item in header_cells}
    matches = sum(1 for cell in cells if _compact(cell) in normalized_headers)
    if any(
        phrase in _compact(text)
        for phrase in (
            "资格审查要求概况",
            "评审点具体描述",
            "简要技术指标",
            "编列内容",
            "报价明细内容",
            "是否客观项",
        )
    ) and int(source.get("rowIndex") or 1) <= 2:
        return True
    cell_values = {_compact(cell) for cell in cells}
    requirement_cells = {
        value for value in cell_values
        if any(
            hint in value
            for hint in ("须", "必须", "不得", "提供", "支持", "不少于", "不低于")
        ) or ("应" in value and "响应" not in value and "是否" not in value)
    }
    return matches >= max(2, (len(cells) + 1) // 2) and not requirement_cells


def _is_non_requirement_scope_text(text: str) -> bool:
    value = _safe_text(text, 1200)
    compact = _compact(value)
    if not value:
        return True
    if is_form_title(value) or is_placeholder_row(value):
        return True
    if any(_compact(term) in compact for term in NON_REQUIREMENT_SCOPE_TERMS):
        return True
    return False


def _is_eligible_scoped_segment(segment: dict[str, Any]) -> bool:
    text = _safe_text(segment.get("text"), 1200)
    if not text or _is_non_requirement_scope_text(text):
        return False

    scope_types = {
        str(item).strip()
        for item in segment.get("scopeSectionTypes") or []
        if str(item).strip()
    }
    if scope_types and scope_types.isdisjoint(VALID_SCOPE_SECTION_TYPES):
        return False

    # Table rows selected by the scope selector may not carry a section type;
    # the table itself has already been classified as a target requirement table.
    return bool(scope_types & VALID_SCOPE_SECTION_TYPES) or bool(segment.get("tableId"))


def _eligible_scope_segments(
    scoped_segments: list[dict[str, Any]],
    fallback_segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    selected = [segment for segment in scoped_segments if _is_eligible_scoped_segment(segment)]
    if selected:
        return selected
    return [
        segment
        for segment in fallback_segments
        if _is_requirement(_safe_text(segment.get("text"), 1200))
        and not _is_non_requirement_scope_text(_safe_text(segment.get("text"), 1200))
    ]


def _section_for(text: str, current: str) -> str:
    label = _category_for(text)
    if _is_heading(text) and label != "other":
        return _safe_text(text, 120)
    return current


def _requirement_from_segment(
    source: dict[str, Any],
    index: int,
    section: str,
) -> list[dict[str, Any]]:
    text = _safe_text(source.get("text"), 1200)
    table_requirement_row = bool(
        source.get("tableId") and _is_table_requirement_row(source, text)
    )
    if (
        _is_table_header_row(source, text)
        or _is_process_only_text(text)
        or _is_template_placeholder(text)
        or _is_incomplete_requirement_label(text)
    ):
        return []
    if source.get("tableId") and not table_requirement_row:
        return []
    clauses = (
        _table_requirement_clauses(source, text)
        if source.get("tableId") and _table_cell_texts(source)
        else _split_requirement_text(text)
    )
    clauses = [
        clause
        for clause in clauses
        if not _is_process_only_text(clause)
        and not _is_template_placeholder(clause)
        and not _is_incomplete_requirement_label(clause)
    ]
    if not _is_requirement(text) and not table_requirement_row:
        clauses = [clause for clause in clauses if _is_requirement(clause)]
    if not clauses:
        return []
    compound = len(clauses) > 1
    parent_id = f"rule-{index + 1:03d}"
    source_id = str(source.get("id") or "")
    table_type = str(source.get("tableRequirementType") or "")
    category_override = {
        "qualification": "qualification",
        "compliance": "compliance",
        "technical": "technical",
        "commercial": "commercial",
        "acceptance": "commercial",
        "rejection": "rejection",
        "contract": "contract",
        "pricing": "pricing",
    }.get(table_type)
    return [
        {
            "id": f"{parent_id}-{clause_index:02d}" if compound else parent_id,
            "category": category_override or _category_for(clause),
            "text": clause,
            "section": section,
            "sourceLocator": _source_locator(source, index),
            "mandatory": True,
            "isRejection": (category_override or _category_for(clause)) == "rejection",
            "confidence": 0.64,
            "parentRequirementId": parent_id if compound else "",
            "clauseIndex": clause_index if compound else 1,
            "clauseCount": len(clauses),
            "sourceSegmentId": source_id,
            "extractionSource": "rule",
        }
        for clause_index, clause in enumerate(clauses, start=1)
    ]
    category = category_override or _category_for(text)
    return {
        "id": f"rule-{index + 1:03d}",
        "category": category,
        "text": text,
        "section": section,
        "sourceLocator": _source_locator(source, index),
        "mandatory": True,
        "isRejection": category == "rejection",
        "confidence": 0.64,
        "sourceSegmentId": source_id,
        "extractionSource": "rule",
    }


def extract_tender_requirements_by_rule(text: str, segments: list[dict[str, Any]]) -> dict[str, Any]:
    prepared = segments or [
        {"text": line, "sourceLocator": f"第{index + 1}行"}
        for index, line in enumerate(text.splitlines())
        if line.strip()
    ]
    requirements: list[dict[str, Any]] = []
    current_section = ""
    for index, segment in enumerate(prepared):
        segment_text = _safe_text(segment.get("text"), 1200)
        if not segment_text:
            continue
        current_section = _section_for(segment_text, current_section)
        requirements.extend(_requirement_from_segment(segment, len(requirements), current_section))
        if len(requirements) >= 2000:
            break

    return {
        "requirements": requirements,
        "warnings": [] if requirements else ["规则未识别到可逐条核验的招标要求，请人工检查原始文件。"],
    }


def _json_from_model(content: str) -> dict[str, Any] | None:
    clean = re.sub(r"^```(?:json)?\s*", "", str(content or "").strip(), flags=re.IGNORECASE)
    clean = re.sub(r"\s*```$", "", clean)
    try:
        result = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            result = json.loads(clean[start : end + 1])
        except json.JSONDecodeError:
            return None
    return result if isinstance(result, dict) else None


def _normalize_model_extraction(
    value: dict[str, Any],
    available_segments: dict[str, dict[str, Any]] | set[str],
) -> dict[str, Any] | None:
    raw_requirements = value.get("requirements")
    if not isinstance(raw_requirements, list):
        return None
    if isinstance(available_segments, set):
        segment_map: dict[str, dict[str, Any]] = {}
        locator_map: dict[str, list[str]] = {}
    else:
        segment_map = available_segments
        locator_map = {}
        for segment_id, segment in segment_map.items():
            locator_map.setdefault(_source_locator(segment, 0), []).append(segment_id)

    def as_bool(raw_value: Any, default: bool = True) -> bool:
        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, str):
            lowered = raw_value.strip().lower()
            if lowered in {"false", "0", "no", "否"}:
                return False
            if lowered in {"true", "1", "yes", "是"}:
                return True
        return default if raw_value is None else bool(raw_value)

    requirements: list[dict[str, Any]] = []
    unresolved_count = 0
    filtered_count = 0
    for index, raw in enumerate(raw_requirements[:500]):
        if not isinstance(raw, dict):
            continue
        text = _safe_text(raw.get("text"), 1200)
        if not text:
            continue
        if _is_process_only_text(text):
            filtered_count += 1
            continue

        raw_source_ids = raw.get("sourceSegmentIds")
        if not isinstance(raw_source_ids, list):
            raw_source_ids = [raw.get("sourceSegmentId")]
        source_ids = [
            str(source_id).strip()
            for source_id in raw_source_ids
            if str(source_id or "").strip() in segment_map
        ]
        locator = _safe_text(raw.get("sourceLocator"), 160)
        if not source_ids and locator:
            source_ids = locator_map.get(locator, [])[:1]
        if not source_ids and segment_map:
            text_key = _compact(text)
            candidates = [
                segment_id
                for segment_id, segment in segment_map.items()
                if text_key and text_key in _compact(segment.get("text"))
            ]
            if len(candidates) == 1:
                source_ids = candidates
        if not source_ids:
            unresolved_count += 1
            continue

        source_segment = segment_map.get(source_ids[0], {})
        if _is_template_placeholder(text) or (
            source_segment.get("tableId")
            and not _is_table_requirement_row(source_segment, text)
        ):
            filtered_count += 1
            continue
        source_locator = _source_locator(source_segment, index)
        category = str(raw.get("category") or _category_for(text)).lower()
        if category not in ALLOWED_CATEGORIES:
            category = _category_for(text)
        confidence = raw.get("confidence", 0.72)
        try:
            confidence = min(max(float(confidence), 0), 1)
        except (ValueError, TypeError):
            confidence = 0.72
        requirements.append(
            {
                "id": str(raw.get("id") or f"llm-{index + 1:03d}"),
                "category": category,
                "text": text,
                "section": _safe_text(raw.get("section"), 120) or CATEGORY_LABELS[category],
                "sourceLocator": source_locator,
                "sourceSegmentId": source_ids[0],
                "sourceSegmentIds": source_ids,
                "mandatory": as_bool(raw.get("mandatory"), True),
                "isRejection": as_bool(raw.get("isRejection"), category == "rejection"),
                "confidence": confidence,
                "extractionSource": "llm",
            }
        )
    if not requirements:
        return None
    warnings = [
        _safe_text(item, 240)
        for item in value.get("warnings", [])
        if _safe_text(item, 240)
    ]
    if unresolved_count:
        warnings.append(f"{unresolved_count} 条模型结果未能回溯到输入片段，已跳过。")
    if filtered_count:
        warnings.append(f"已过滤 {filtered_count} 条流程性或非目标内容。")
    return {
        "requirements": requirements,
        "warnings": warnings[:8],
    }


def _requirement_similarity(left: dict[str, Any], right: dict[str, Any]) -> float:
    left_text = _compact(left.get("text"))
    right_text = _compact(right.get("text"))
    if not left_text or not right_text:
        return 0.0
    if left_text in right_text or right_text in left_text:
        return min(len(left_text), len(right_text)) / max(len(left_text), len(right_text))
    return SequenceMatcher(None, left_text, right_text).ratio()


def _merge_requirement_candidates(
    model_requirements: list[dict[str, Any]],
    rule_requirements: list[dict[str, Any]],
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge LLM and rule clauses while keeping one row per source clause."""
    merged: list[dict[str, Any]] = []
    source_order = {
        str(segment.get("id")): index
        for index, segment in enumerate(segments)
        if str(segment.get("id") or "")
    }

    for candidate in [*model_requirements, *rule_requirements]:
        text = _compact(candidate.get("text"))
        if not text:
            continue
        source_ids = {
            str(value)
            for value in (candidate.get("sourceSegmentIds") or [candidate.get("sourceSegmentId")])
            if str(value or "")
        }
        matched: dict[str, Any] | None = None
        for existing in merged:
            existing_ids = {
                str(value)
                for value in (existing.get("sourceSegmentIds") or [existing.get("sourceSegmentId")])
                if str(value or "")
            }
            same_source = bool(source_ids & existing_ids)
            similar = _requirement_similarity(candidate, existing)
            if same_source and similar >= 0.58 or similar >= 0.86:
                matched = existing
                break
        if matched is None:
            merged.append(dict(candidate))
            continue

        existing_source = str(matched.get("extractionSource") or "")
        candidate_source = str(candidate.get("extractionSource") or "")
        if existing_source != candidate_source and {existing_source, candidate_source} == {"llm", "rule"}:
            matched["extractionSource"] = "llm+rule"
            # 类目以规则路径判定为准（含表格类型上下文，比模型分类更稳定），
            # 避免模型把“报价/开标/格式”等内容误归入“技术”等其它类目。
            if candidate_source == "rule":
                rule_category = candidate.get("category", "")
                if rule_category and rule_category != matched.get("category"):
                    matched["category"] = rule_category
        if candidate_source == "llm" and existing_source != "llm":
            # 类目以规则路径判定为准（含表格类型上下文，比模型分类更稳定），
            # 避免模型把“报价/开标/格式”等内容误归入“技术”等其它类目。
            rule_category = matched.get("category", "")
            preserved = dict(candidate)
            preserved["extractionSource"] = "llm+rule"
            if rule_category and rule_category != preserved.get("category"):
                preserved["category"] = rule_category
            matched.clear()
            matched.update(preserved)
        all_source_ids = list(dict.fromkeys([
            *[str(value) for value in (matched.get("sourceSegmentIds") or []) if str(value)],
            *[str(value) for value in source_ids if str(value)],
        ]))
        if all_source_ids:
            matched["sourceSegmentIds"] = all_source_ids
            matched["sourceSegmentId"] = all_source_ids[0]

    for index, item in enumerate(merged, start=1):
        item["id"] = f"req-{index:03d}"
    merged.sort(
        key=lambda item: min(
            [source_order.get(str(value), len(source_order)) for value in (
                item.get("sourceSegmentIds") or [item.get("sourceSegmentId")]
            ) if str(value)] or [len(source_order)]
        )
    )
    return merged


def _model_input_batches(
    segments: list[dict[str, Any]],
    *,
    max_chars: int = 6500,
) -> list[dict[str, Any]]:
    """按游标切分模型输入，保证每个非空片段只进入一个批次。"""
    def display_line(segment: dict[str, Any], segment_id: str, locator: str, text: str) -> str:
        cells = _table_cell_texts(segment)
        if not segment.get("tableId") or not cells:
            return f"[{segment_id} | {locator}] {text}"
        table_type = str(segment.get("tableRequirementType") or "")
        labels_by_type = {
            "qualification": ["序号", "资格要求概况", "评审点具体描述"],
            "technical": ["序号", "品目名称", "简要技术指标", "单位", "数量"],
            "commercial": ["序号", "参数性质", "类型", "要求"],
            "acceptance": ["序号", "参数性质", "类型", "要求"],
            "rejection": ["序号", "参数性质", "类型", "要求"],
        }
        labels = labels_by_type.get(table_type, [])
        # 要求单元格含多段落时逐段渲染，保留条目边界，避免整段长文本压成一条导致模型漏提/合并。
        raw_cells = segment.get("cells")
        if isinstance(raw_cells, list):
            req_index = segment.get("tableRequirementCellIndex")
            if req_index is None:
                req_index = _requirement_cell_index(table_type)
            if req_index is not None and req_index < len(raw_cells):
                req_cell = raw_cells[req_index]
                paragraphs = [
                    _safe_text(p.get("text"), 4000)
                    for p in (req_cell.get("paragraphs") or [])
                    if isinstance(p, dict)
                ]
                paragraphs = [item for item in paragraphs if item]
                if len(paragraphs) >= 3:
                    lines: list[str] = []
                    for index, cell in enumerate(raw_cells):
                        cell_text = _safe_text(cell.get("text"), 20000) if isinstance(cell, dict) else ""
                        label = labels[index] if index < len(labels) else f"第{index + 1}列"
                        if index == req_index:
                            for p_index, p_text in enumerate(paragraphs, start=1):
                                lines.append(f"{label}[{p_index}]={p_text}")
                        elif cell_text:
                            lines.append(f"{label}={cell_text}")
                    return f"[{segment_id} | {locator}] 表格字段：\n" + "\n".join(lines)
        fields = "；".join(
            f"{labels[index] if index < len(labels) else f'第{index + 1}列'}={value}"
            for index, value in enumerate(cells) if value
        )
        return f"[{segment_id} | {locator}] 表格字段：{fields}\n原始行：{text}"

    candidates: list[dict[str, str]] = []
    for index, segment in enumerate(segments):
        text_limit = 20000 if segment.get("tableId") else 1800
        text = _safe_text(segment.get("text"), text_limit)
        if not text:
            continue
        segment_id = str(segment.get("id") or f"SEG-FALLBACK-{index + 1:06d}")
        locator = _source_locator(segment, index)
        candidates.append(
            {
                "id": segment_id,
                "line": display_line(segment, segment_id, locator, text),
            }
        )
    if not candidates:
        return []

    batches: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(candidates):
        selected: list[dict[str, str]] = []
        length = 0
        while cursor < len(candidates):
            candidate = candidates[cursor]
            candidate_length = len(candidate["line"]) + (1 if selected else 0)
            if selected and length + candidate_length > max_chars:
                break
            selected.append(candidate)
            length += candidate_length
            cursor += 1
        if not selected:
            selected = [candidates[cursor]]
            cursor += 1
        batches.append(
            {
                "text": "\n".join(item["line"] for item in selected),
                "segmentIds": [item["id"] for item in selected],
            }
        )
    return batches


def _segments_for_sections(
    segments: list[dict[str, Any]],
    sections: list[dict[str, Any]] | None,
    section_ids: list[str],
) -> list[dict[str, Any]]:
    """返回属于指定章节的所有 segment（按 section.segmentIds 关联）。"""
    target = set(section_ids)
    segment_ids: set[str] = set()
    for section in sections or []:
        if str(section.get("sectionId")) in target:
            segment_ids.update(str(item) for item in section.get("segmentIds") or [])
    if not segment_ids:
        return []
    return [segment for segment in segments if str(segment.get("id")) in segment_ids]


def identify_requirement_sections(
    sections: list[dict[str, Any]],
    ai: dict[str, Any],
) -> list[str]:
    """第一段：让 LLM 从章节标题里挑出"含具体投标要求"的章节，返回 sectionId 列表。"""
    section_list = [section for section in sections if isinstance(section, dict)]
    menu_lines: list[str] = []
    for section in section_list:
        section_id = str(section.get("sectionId") or "")
        if not section_id:
            continue
        heading = _safe_text(section.get("heading"), 200)
        count = len(section.get("segmentIds") or [])
        menu_lines.append(f"[{section_id}] {heading} | 段落数 {count}")
    if not menu_lines:
        return []
    menu = "\n".join(menu_lines)

    prompt = (
        "你是招标文件结构识别助手。下面是一份招标文件的章节清单，格式为：[章节ID] 标题 | 段落数。"
        "请挑出【包含需要投标人满足或响应的具体条件的章节】：技术/功能/性能/参数、商务、资格、验收、服务、交付、售后等。"
        "排除流程性章节：投标邀请、投标人须知、评标办法、评分标准、合同格式、质疑投诉、中标人确定、开标程序、监督。"
        "只依据标题判断，不要编造不存在的章节ID。"
        '输出严格 JSON，不使用 Markdown：{"sections":["章节ID1","章节ID2"]}'
    )
    result = call_openai_compatible(
        ai,
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": menu},
        ],
        max_tokens=800,
        temperature=0,
    )
    data = _json_from_model(result.get("content", "")) if result.get("success") else None
    if not isinstance(data, dict):
        return []
    valid = {str(section.get("sectionId")) for section in section_list if section.get("sectionId")}
    return [item for item in (str(value) for value in data.get("sections") or []) if item in valid]


def extract_tender_requirements(
    text: str,
    segments: list[dict[str, Any]],
    ai: dict[str, Any],
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    scope = select_requirement_scope(segments, sections, tables)
    base_url = str(ai.get("baseUrl") or "").strip()
    api_key = str(ai.get("apiKey") or "").strip()
    model = str(ai.get("model") or "").strip()
    ai_ready = ai.get("enabled") is not False and bool(base_url and api_key and model)

    # 第一段：LLM 语义识别"含具体投标要求"的章节（不靠关键词词表）。
    llm_section_ids: list[str] = []
    if ai_ready and sections:
        llm_section_ids = identify_requirement_sections(sections, ai)
        scope["llmSections"] = [
            {"sectionId": str(section.get("sectionId")), "heading": str(section.get("heading") or "")}
            for section in sections
            if str(section.get("sectionId")) in set(llm_section_ids)
        ]

    # 章节语义筛选优先：LLM 识别到目标章节时以其为主，仅补充规则识别出的“要求类”表格行，
    # 不再与规则范围取并集，避免把评标办法、合同条款、格式模板等流程性内容灌回模型。
    section_segments = _segments_for_sections(segments, sections, llm_section_ids) if llm_section_ids else []
    if llm_section_ids:
        requirement_table_types = {
            "technical", "qualification", "commercial", "acceptance", "rejection",
        }
        requirement_table_ids = {
            str(table.get("tableId") or table.get("id"))
            for table in tables or []
            if isinstance(table, dict)
            and _table_requirement_type(table) in requirement_table_types
        }
        table_segments = [
            segment
            for segment in scope["selectedSegments"]
            if segment.get("tableId") and str(segment.get("tableId")) in requirement_table_ids
        ]
        scoped_ids = {
            str(segment.get("id"))
            for segment in [*section_segments, *table_segments]
            if str(segment.get("id") or "") and _safe_text(segment.get("text"), 800)
        }
    else:
        eligible_scope_segments = _eligible_scope_segments(scope["selectedSegments"], segments)
        scoped_ids = {
            str(segment.get("id"))
            for segment in eligible_scope_segments
            if str(segment.get("id") or "") and _safe_text(segment.get("text"), 800)
        }
    scoped_segments = [
        segment for segment in segments
        if str(segment.get("id") or "") in scoped_ids
    ]
    if not scoped_segments:
        scoped_segments = [segment for segment in segments if _safe_text(segment.get("text"), 800)]
    scoped_segments = _annotate_table_context(scoped_segments, tables)

    scoped_text = "\n".join(
        _safe_text(segment.get("text"), 12000)
        for segment in scoped_segments
        if _safe_text(segment.get("text"), 12000)
    )
    if not scoped_text and text:
        scoped_text = text
    rule_result = extract_tender_requirements_by_rule(scoped_text, scoped_segments)

    if not ai_ready:
        return {
            **rule_result,
            "scope": scope,
            "extractionMethod": "rule",
            "modelUsed": "",
            "extractionWarning": "未配置大模型，已使用规则拆解条款。",
        }

    prompt = (
        "你是招标文件条款拆解助手。仅依据给定原文，拆成可以逐条核验的要求。"
        "不得判断企业是否满足，不得补写原文没有的内容。"
        "只提取三大类要求：资格审查、商务与服务、技术参数。"
        "资格审查包括 qualification/compliance/rejection/performance/financial/credit/region；"
        "商务与服务包括 commercial/pricing/contract/service_plan/personnel/document；"
        "技术参数包括 technical。"
        "人员资格、项目负责人、驻场团队等属于商务与服务中的 personnel，必须提取；"
        "质疑投诉、监督、评标流程、中标人确定方式等流程性内容不要提取。"
        "每条要求必须独立成条、只写一个可核验要求，不要把多条合并成一条，保持原文顺序。"
        "输出严格 JSON，不使用 Markdown："
        '{"requirements":[{"id":"req-001","category":"qualification|compliance|rejection|performance|financial|credit|region|commercial|pricing|contract|service_plan|personnel|document|technical",'
        '"text":"原文要求","section":"所在章节","sourceSegmentId":"输入方括号中的片段ID",'
        '"sourceSegmentIds":["可选的关联片段ID"],'
        '"mandatory":true,"isRejection":false,"confidence":0.0}],"warnings":[]}'
        "。sourceSegmentId 必须来自输入片段方括号中的真实 ID；不要自行生成定位，不要填写 sourceLocator。"
        "请逐条提取该批原文中全部可核验要求，不要只挑关键条款，不要合并、不要遗漏，保持原文顺序。每批最多输出 30 条。"
    )
    available_segments = {
        str(item.get("id")): item
        for item in scoped_segments
        if str(item.get("id") or "")
    }
    extracted: list[dict[str, Any]] = []
    warnings: list[str] = []
    model_used = ""
    sent_segment_ids: set[str] = set()
    reported_segment_ids: set[str] = set()
    batch_count = 0
    failed_batch_count = 0

    requirement_schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "tender_requirement_extraction",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "requirements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {"type": "string"},
                                "category": {
                                    "type": "string",
                                    "enum": [
                                        "qualification",
                                        "compliance",
                                        "rejection",
                                        "performance",
                                        "personnel",
                                        "financial",
                                        "credit",
                                        "region",
                                        "commercial",
                                        "pricing",
                                        "contract",
                                        "service_plan",
                                        "document",
                                        "technical",
                                        "other",
                                    ],
                                },
                                "text": {"type": "string"},
                                "section": {"type": "string"},
                                "sourceSegmentId": {"type": "string"},
                                "sourceSegmentIds": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "mandatory": {"type": "boolean"},
                                "isRejection": {"type": "boolean"},
                                "confidence": {"type": "number"},
                            },
                            "required": [
                                "id",
                                "category",
                                "text",
                                "section",
                                "sourceSegmentId",
                                "sourceSegmentIds",
                                "mandatory",
                                "isRejection",
                                "confidence",
                            ],
                        },
                    },
                    "warnings": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["requirements", "warnings"],
            },
        },
    }
    batches = _model_input_batches(scoped_segments)
    for index, batch in enumerate(batches, start=1):
        batch_count = index
        batch_segment_ids = {
            str(value) for value in batch.get("segmentIds") or [] if str(value)
        }
        sent_segment_ids.update(batch_segment_ids)
        result = call_openai_compatible(
            ai,
            [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"招标文件片段（第 {index} 批）如下：\n{batch['text']}",
                },
            ],
            max_tokens=4000,
            temperature=0,
            response_format=requirement_schema,
        )
        if _is_gateway_timeout(result):
            result = call_openai_compatible(
                ai,
                [
                    {"role": "system", "content": prompt},
                    {
                        "role": "user",
                        "content": (
                            f"招标文件片段（第 {index} 批，已压缩）如下：\n"
                            f"{batch['text'][:2200]}"
                        ),
                    },
                ],
                max_tokens=1200,
                temperature=0,
                response_format=requirement_schema,
            )
        if not result.get("success"):
            failed_batch_count += 1
            warnings.append(f"第 {index} 批模型调用失败：{result.get('error', '未知错误')}")
            continue

        normalized = _normalize_model_extraction(
            _json_from_model(result.get("content", "")) or {},
            {
                segment_id: available_segments[segment_id]
                for segment_id in batch_segment_ids
                if segment_id in available_segments
            },
        )
        if not normalized:
            warnings.append(f"第 {index} 批模型未返回可用 JSON。")
            continue
        extracted.extend(normalized["requirements"])
        for requirement in normalized["requirements"]:
            reported_segment_ids.update(
                str(value)
                for value in (
                    requirement.get("sourceSegmentIds")
                    or [requirement.get("sourceSegmentId")]
                )
                if str(value)
            )
        warnings.extend(normalized["warnings"])
        model_used = str(result.get("model", ""))

    deduplicated = _merge_requirement_candidates(
        extracted,
        rule_result.get("requirements", []),
        scoped_segments,
    )
    missing_segment_ids = sorted(sent_segment_ids - reported_segment_ids)
    coverage_rate = (
        round(len(reported_segment_ids & sent_segment_ids) * 100 / len(sent_segment_ids))
        if sent_segment_ids
        else 0
    )
    if missing_segment_ids:
        warnings.append(
            f"有 {len(missing_segment_ids)} 个输入片段未被模型引用，已由规则结果兜底或保留人工核验。"
        )
    if failed_batch_count:
        warnings.append(f"{failed_batch_count}/{batch_count} 个模型批次失败，已使用规则结果补齐。")
    extraction_stats = {
        "candidateSegmentCount": len(available_segments),
        "sentSegmentCount": len(sent_segment_ids),
        "reportedSegmentCount": len(reported_segment_ids & sent_segment_ids),
        "missingSegmentIds": missing_segment_ids[:200],
        "coverageRate": coverage_rate,
        "batchCount": batch_count,
        "failedBatchCount": failed_batch_count,
        "ruleRequirementCount": len(rule_result.get("requirements", [])),
        "llmRequirementCount": len(extracted),
    }

    if deduplicated:
        return {
            "requirements": deduplicated,
            "warnings": warnings[:8],
            "scope": scope,
            "extractionMethod": "llm+rule" if rule_result.get("requirements") else "llm",
            "modelUsed": model_used,
            "stats": extraction_stats,
            "extractionWarning": "模型结果已与规则结果合并，并对未引用片段做了覆盖核验。" if warnings else "",
        }

    warning = "；".join(warnings[:3]) or "模型未返回可用 JSON"
    return {
        **rule_result,
        "scope": scope,
        "extractionMethod": "rule",
        "modelUsed": "",
        "stats": extraction_stats,
        "extractionWarning": f"{warning}，已回退到规则拆解。",
    }
