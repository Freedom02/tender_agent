from __future__ import annotations

import re
from typing import Any

from .builtin_target_processing import extract_builtin_target_document
from .requirement_scope import is_form_title, is_placeholder_row


# ⚠ LEGACY（兼容层）：15 类中文标签。细匹配主线是 8 域
# （builtin_target_map.TARGET_MODULES），本词表仅用于匹配层的条款标签
# （document_matching / handoff），新增代码勿依赖其分类含义。
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


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").lower())


def _safe_text(value: Any, limit: int = 1000) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _source_locator(item: dict[str, Any], index: int) -> str:
    return _safe_text(item.get("sourceLocator"), 160) or f"第{index + 1}段"


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


REJECTION_HEADING_TERMS = (
    "无效投标",
    "无效标",
    "投标无效",
    "废标",
    "否决",
    "视为串通",
    "资格审查不合格",
    "符合性审查不合格",
    "不得出现报价",
    "无效响应",
    "响应无效",
    "作无效处理",
)

# 正文层词表与标题层**刻意不同源**：正文判据要抓"片段自身把话说全"的条款。
REJECTION_BODY_TERMS = (
    "无效投标",
    "投标无效",
    "无效标",
    "废标",
    "否决",
    "串通",
    "不合格",
    "不予退还",
    "取消投标资格",
    "取消中标资格",
    "拒绝其投标",
    "予以拒绝",
    "不得参加",
    "作无效处理",
    "视为无效",
)

# 用于在"标题命中否决词"的那一章内，划出条款的真实右边界。
_REJECTION_ITEM_PATTERN = re.compile(r"^[（(]?\s*[一二三四五六七八九十\d]{1,3}\s*[）)、.．]")
_REJECTION_LONG_SEGMENT_CHARS = 300


def _hits_any(value: str, terms: tuple[str, ...]) -> bool:
    """`value` 已由 `_compact` 归一化（去空白 + 小写）后判断是否含任一关键词。"""
    return any(_compact(term) in value for term in terms)


def _deterministic_rejection_segments(
    segments: list[dict[str, Any]],
    sections: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """确定性挑出「应被视为否决条款」的片段（零模型调用）。

    ``keep = 标题范围层 ∪ 正文命中层``：

    1. **标题范围层** —— 章的标题命中 ``REJECTION_HEADING_TERMS`` 时，从该章的
       ``headingSegmentId`` 往后收**连续**的枚举子项 / 表格行；遇到下一个章的
        标题片段、或超长片段（>300 字）立即停止。
    2. **正文命中层** —— 片段正文命中 ``REJECTION_BODY_TERMS``。

    第 1 层的两个停止条件不是保守起见，而是必需的：``document_structure.py``
    用**扁平闭区间**切章节（``covered_segments = normalized_segments[position :
    end_position + 1]``），于是"条款编号"会被当成章标题，把其后的正文一路吞进来。
    实测 ``10.12…有下列情形之一的，投标无效：`` 吞进 25 段开标程序；
    ``三、无效标检查`` 甚至把整张评分表挂进了自己的 ``segmentIds``（13 段）。
    加上这两个停止条件后，被并入的噪声段从 01 的 11 段 / 04 的 7 段压到
    2 段 / 0 段，而目标覆盖**一段未掉**。
    """
    ordered_ids = [str(item.get("id")) for item in segments if str(item.get("id") or "")]
    by_id = {
        str(item.get("id")): item for item in segments if str(item.get("id") or "")
    }
    position = {value: index for index, value in enumerate(ordered_ids)}

    body_hits = {
        segment_id
        for segment_id, segment in by_id.items()
        if _hits_any(_compact(segment.get("text")), REJECTION_BODY_TERMS)
    }

    section_list = [item for item in sections or [] if isinstance(item, dict)]
    heading_segment_ids = {
        str(item.get("headingSegmentId"))
        for item in section_list
        if str(item.get("headingSegmentId") or "")
    }

    span_hits: set[str] = set()
    for section in section_list:
        if not _hits_any(_compact(section.get("heading")), REJECTION_HEADING_TERMS):
            continue
        start = position.get(str(section.get("headingSegmentId") or ""))
        if start is None:
            continue
        index = start + 1
        while index < len(ordered_ids):
            segment_id = ordered_ids[index]
            if segment_id in heading_segment_ids:
                # 下一个章的标题 → 这一条条款的范围结束
                break
            text = " ".join(str((by_id.get(segment_id) or {}).get("text") or "").split())
            if not text:
                index += 1
                continue
            if len(text) > _REJECTION_LONG_SEGMENT_CHARS:
                # 整块大表（典型是被错挂到本章下的评分表）→ 停
                break
            if _REJECTION_ITEM_PATTERN.match(text) or "|" in text:
                span_hits.add(segment_id)
                index += 1
                continue
            break

    keep = body_hits | span_hits
    return [segment for segment in segments if str(segment.get("id") or "") in keep]


# 兜底路径的**最小**噪声判据。
#
# 刻意不复用 `_is_requirement` / `_is_process_only_text` / `_is_incomplete_requirement_label`
# 那一套：那几道闸是为"在一整篇文档里挑出像要求的句子"设计的，**必须严**，
# 否则目录、页眉、模板占位、栏目名会全变成要求。
# 但兜底路径的**选择性已经由否决词表完成**（确定性、窄），再过一遍"像不像要求"
# 既是判据重复，又会系统性误杀否决条款的典型句式。实测（四份真实文件）：
#
#   · `9.2 投标人应对同一个采购包内的所有内容进行完整投标，否则投标无效。`
#     —— 无 `_is_requirement` 词表命中 ⇒ 被判"非要求句"丢弃
#   · `9.7 有下列情形之一的，视为投标人串通投标，其投标无效：`
#     —— 以冒号结尾且不含时间/金额量词 ⇒ 被 `_is_incomplete_requirement_label` 丢弃
#   · `（6）如出现下列情形之一的，…按照无效投标处理。` —— 同上
#
# 结果：兜底片段里"命中否决目标词"的部分，被规则通道挡掉了
# 01 28/49、02 8/16、03 4/6、04 13/20 —— 即离线预期的 100% 召回在真实链路上
# 会被削掉一半以上。这正是"只在真实执行中才暴露的接缝"。
_REJECTION_TITLE_STRIP_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百]+[章节篇部分]"
    r"|[一二三四五六七八九十]+[、.．]"
    r"|（[一二三四五六七八九十\d]+）"
    r"|\d+(?:\.\d+)*[、.．]?)[\s　]*"
)
_REJECTION_TOC_TAIL_RE = re.compile(r"[-—–－\s]*\d{1,3}\s*[-—–－\s]*$")
_REJECTION_TITLE_MAX_CHARS = 8
_REJECTION_MIN_CHARS = 6
_REJECTION_TOC_MAX_CHARS = 40
_REJECTION_PUNCTUATION_RE = re.compile(r"[，。；：、,.;:！？]")


def _is_rejection_noise(text: str) -> bool:
    """兜底片段的噪声判据：只挡"确定不是条款"的东西，宁松勿严。

    三类：
      · **模板占位** —— 表单字段名、占位符（`（单位名称）` 等）；
      · **目录条目** —— 短、以章节编号开头、以页码结尾（`五、废标条款 - 34 -`）；
      · **纯标题** —— 去掉编号后不足 8 字且不含任何标点（`三、无效标检查`）。
        阈值刻意压到 8 字并叠加"无标点"条件：像 `（六）投标人串通投标的；`
        这种真枚举子项有分号、`9.7有下列情形之一的，…其投标无效：` 有逗号，
        两者都不会被误挡。
    """
    clean = str(text or "").strip()
    if len(_compact(clean)) < _REJECTION_MIN_CHARS:
        return True
    if _is_template_placeholder(clean):
        return True
    if _REJECTION_TOC_TAIL_RE.search(clean):
        # 以页码状结尾：只有"短 + 以章节编号开头"才算目录条目
        return bool(
            len(clean) <= _REJECTION_TOC_MAX_CHARS and _REJECTION_TITLE_STRIP_RE.match(clean)
        )
    body = _REJECTION_TITLE_STRIP_RE.sub("", clean, count=1).strip()
    return len(_compact(body)) < _REJECTION_TITLE_MAX_CHARS and not _REJECTION_PUNCTUATION_RE.search(body)


def _rejection_requirements_from_segments(
    segments: list[dict[str, Any]],
    sections: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """把兜底片段直接转成"否决条款"要求条目（确定性，零模型调用）。

    与规则通道（`extract_tender_requirements_by_rule`）的关键差别：
    **不做"这句话像不像一条要求"的判断**，因为这一层已经由否决词表完成了选择，
    再判一次会误杀（见 `_is_rejection_noise` 上方注释）。

    一条片段产出一条要求：否决条款需要逐条核验，合并会丢掉"逐条对照"的能力；
    而枚举子项（`（六）投标人串通投标的；`）脱离引导句会语义不全，故保留其原文，
    由 `section` 带上所属章标题作为上下文。
    """
    section_list = [item for item in sections or [] if isinstance(item, dict)]
    depth = {
        str(item.get("sectionId")): len(item.get("sectionPath") or [])
        for item in section_list
    }
    owner: dict[str, str] = {}
    for section in section_list:
        section_id = str(section.get("sectionId"))
        for segment_id in section.get("segmentIds") or []:
            current = owner.get(str(segment_id))
            if current is None or depth.get(section_id, 0) > depth.get(current, 0):
                owner[str(segment_id)] = section_id
    heading_of = {
        str(item.get("sectionId")): str(item.get("heading") or "") for item in section_list
    }

    requirements: list[dict[str, Any]] = []
    for segment in segments:
        text = _safe_text(segment.get("text"), 1200)
        if not text or _is_rejection_noise(text):
            continue
        segment_id = str(segment.get("id") or "")
        index = len(requirements) + 1
        requirements.append(
            {
                "id": f"rej-{index:03d}",
                "category": "rejection",
                "text": text,
                "section": heading_of.get(owner.get(segment_id, ""), ""),
                "sourceLocator": _source_locator(segment, index),
                "mandatory": True,
                # 与规则通道一致：判据本身就是否决条款，故直接置真。
                "isRejection": True,
                # 比规则通道的 0.64 高：这一层是确定性判据命中的，不是启发式猜测。
                "confidence": 0.9,
                "sourceSegmentId": segment_id,
                # ⚠ **同时**给列表字段，不能只给标量。
                # `_merge_requirement_candidates` 合并两个条目时，重建 source 列表用的是
                # `matched.get("sourceSegmentIds") or []` —— 它**不会**回退到标量。
                # 于是"只有标量的条目"一旦与别的条目合并，它的定位 id 就丢了，
                # 下游按 `sourceSegmentIds` 找覆盖时会误判成"这段没被提取"。
                # 实测：文件 01 的 `rej-015`/`rej-052`/`rej-077` 三个真否决条款
                # 因此从最终结果里"消失"（目标召回 93% 而非 100%）。
                "sourceSegmentIds": [segment_id] if segment_id else [],
                "extractionSource": "rule",
            }
        )
    return requirements


def extract_tender_requirements(
    text: str,
    segments: list[dict[str, Any]],
    ai: dict[str, Any],
    sections: list[dict[str, Any]] | None = None,
    tables: list[dict[str, Any]] | None = None,
    source_file_id: str = "",
    project_id: str = "",
    document_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Builtin extraction is target-oriented（8 域）。旧的 15 类分类提取链已随
    # 8 域主线收敛移除，见 docs/归档-15类提取层删除记录.md。
    extraction = extract_builtin_target_document(
        text=text,
        segments=segments,
        sections=sections,
        tables=tables,
        ai=ai,
        source_file_id=source_file_id,
        project_id=project_id,
        document_meta=document_meta,
    )
    try:
        from .builtin_pack import write_tender_pack

        written = write_tender_pack(
            project_id=project_id,
            source_file_id=source_file_id,
            document_meta=document_meta,
            requirements=[],
            scope=extraction.get("scope"),
            stats=extraction.get("stats"),
            warnings=extraction.get("warnings"),
            extraction_method=str(extraction.get("extractionMethod") or "target-rule"),
            model_used=str(extraction.get("modelUsed") or ""),
            analysis_pack=extraction.get("analysisPack"),
            pack_dir="runtime/projects",
        )
        if written:
            extraction["packPath"] = str(written)
            extraction.setdefault("stats", {})["packPath"] = str(written)
    except Exception as error:
        extraction.setdefault("warnings", []).append(
            f"目标导向提取产物落盘失败（{type(error).__name__}）：{error}"
        )
        extraction["extractionWarning"] = "；".join(
            str(value) for value in extraction.get("warnings")[:3]
        )
    return extraction
