from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any


SECTION_TYPE_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # ★ 加词的硬约束（S1，第六轮实测）：本表**同时**被 `_looks_like_heading` 的
    #   `term_signal`（`:313` 附近）使用 —— 命中本表即认为"像标题"。所以往这里加词
    #   不只是改标签，还会让含该词的**正文行/长表格行**通过标题判定、新建章、截断父章、
    #   整体移位 `SEC` 编号，进而改变送进模型的内容。
    #   实测：完整候选集（14 词）会新建 8 个标题（01 +5、03 +3），其中一个是
    #   「序号 资格评审因素 1 1）营业执照…」整行表格。⇒ 加词前必须先用
    #   `待应用脚本/probe_s1_tree_neutral.py` 确认 Δ标题 = 0（树中性），
    #   再用 `probe_s1_model_input_delta.py` 确认四个 Δ 为 0 或为纯正向。
    #   下面这批词是**逐词实测通过**的（10 词）：Δ标题全 0、LLM 主路径模型输入
    #   新增 0/移除 0、规则兜底路径新增 28/移除 0、候选段 +2/−0。
    #   ⚠ 未纳入的 4 词（评标标准、评分细则、评审因素、资格证明文件）会让正文行变成
    #     标题，须等 `:313` 与 `SECTION_TYPE_TERMS` 解耦后再加（见 §10.2）。
    (
        "rejection",
        (
            "无效投标条款",
            "无效标条款",
            "废标条款",
            "否决条款",
            "无效投标",
            "投标无效",
            # S1 补充（树中性）。
            # 注意：「资格审查不合格」「符合性审查不合格」含后续类型的词
            # （qualification 的「资格审查」、compliance 的「符合性审查」），
            # 因 rejection 排第 1 而**改写**这些标题的判定。实测 5 处改写全是
            # 真否决条款（如「1.4有下列情形之一的，资格审查不合格：」），
            # 属修正而非误伤，且候选段与模型输入均 0 移除。
            "无效报价",
            "无效响应",
            "资格审查不合格",
            "符合性审查不合格",
        ),
    ),
    (
        "compliance",
        (
            "符合性审查",
            "符合性检查",
            "符合性评审",
            "初步审查",
            "初步审查表",
        ),
    ),
    (
        "qualification",
        (
            "资格性审查",
            "投标人资格要求",
            "投标人资格条件",
            "供应商资格要求",
            "供应商资格条件",
            "供应商的资格要求",
            "投标供应商资格条件",
            "资格审查要求",
            "资格审查",
            "合格投标人",
            "特定资格要求",
            # S1 补充（树中性）：03 上救回「二、申请人的资格要求：」5 段
            # （候选段 +2、规则兜底路径模型输入 +28、无移除）。
            "申请人的资格要求",
            "申请人资格要求",
        ),
    ),
    (
        "procurement",
        (
            "获取招标文件",
            "获取采购文件",
            "招标文件的获取",
            "采购文件的获取",
            "报名",
        ),
    ),
    (
        "evaluation",
        (
            "评标办法",
            "评审办法",
            "评标方法",
            "评分标准",
            "评审标准",
            "评审方法",
            "评标委员会",
            # S1 补充（树中性）。⚠ 注意：`evaluation` **不在** requirement_scope 的
            # PRIMARY_SECTION_TYPES 白名单里，判对这两个词**不会**新增要求候选；
            # 它们的价值在 `document_fine_matching.VALID_SCOPE_SECTION_TYPES`
            # （那里含 evaluation），即规则兜底路径的送模型过滤，以及类型语义正确性。
            "评标细则",
            "评分因素",
        ),
    ),
    (
        "technical",
        (
            "项目技术需求",
            "项目技术（质量）需求",
            "技术（质量）需求",
            "项目技术",
            "技术需求",
            "技术参数",
            "技术要求",
            "功能需求",
            "采购标的技术",
            "技术和服务要求",
        ),
    ),
    (
        "service_plan",
        (
            "服务方案",
            "项目实施方案",
            "实施方案",
            "服务实施方案",
            "项目理解",
            "组织实施方案",
            "质量保障方案",
            "应急服务方案",
            "培训方案",
            "运维方案",
        ),
    ),
    (
        "acceptance",
        (
            "验收标准",
            "验收方式",
            "项目验收",
            "验收要求",
            "交付及验收",
        ),
    ),
    (
        "commercial",
        (
            "项目商务需求",
            "商务需求",
            "商务要求",
            "商务条件",
            "服务内容及商务要求",
            "交货期",
            "服务期限",
            "售后服务",
        ),
    ),
    (
        "pricing",
        (
            "报价要求",
            "投标报价",
            "报价文件",
            "分项报价",
            "报价明细",
        ),
    ),
    (
        "document_format",
        (
            "投标文件格式",
            "响应文件格式",
            "投标文件编制",
            "响应文件编制",
            "投标文件组成",
            "响应文件组成",
            "文件编制要求",
            "签署与封装",
            "签字与盖章",
            "装订要求",
            # S1 补充（树中性）。⚠ 同 evaluation：`document_format` 不在
            # PRIMARY_SECTION_TYPES 里，判对不新增要求候选；价值在
            # VALID_SCOPE_SECTION_TYPES（含 document_format）与语义正确性。
            # 「合同格式」按有序首命中会遮蔽 `contract` 的「合同条款」，
            # 实测只 1 处（「（三）合同格式及合同条款」，1 段），且两类型都不在
            # PRIMARY 白名单 ⇒ 对候选集与模型输入均 0 影响。
            "合同格式",
            "合同文本",
        ),
    ),
    (
        "personnel",
        (
            "人员要求",
            "项目人员",
            "项目团队",
            "项目经理",
            "项目负责人",
        ),
    ),
    (
        "performance",
        (
            "业绩要求",
            "类似业绩",
            "项目业绩",
            "业绩证明",
        ),
    ),
    (
        "contract",
        (
            "合同条款",
            "合同主要条款",
            "采购合同",
            "服务合同",
            "合同范本",
        ),
    ),
    (
        "basic",
        (
            "项目基本情况",
            "项目概况",
            "采购项目概述",
            "招标项目内容",
            "采购范围",
            "项目要求",
            "采购需求",
        ),
    ),
    (
        "invitation",
        (
            "投标邀请书",
            "投标邀请",
            "采购邀请",
            "响应邀请",
            "邀请函",
        ),
    ),
    (
        "instructions",
        (
            "投标人须知",
            "供应商须知",
            "投标人须知前附表",
            "响应文件须知",
            "投标有关说明",
        ),
    ),
    (
        "appendix",
        (
            "附件",
            "附录",
        ),
    ),
)


CHINESE_NUMERAL = "一二三四五六七八九十百千万零〇两"
MARKER_PREFIX = r"[★▲※○●]?\s*"
BARE_TOP_LEVEL_PATTERN = re.compile(
    rf"^{MARKER_PREFIX}\u7b2c[{CHINESE_NUMERAL}0-9]+\s+\S.*"
)

HEADING_PREFIXES = (
    # Some response files use a bare numbered top-level heading such as
    # "第一 报价文件" instead of "第一章/第一篇".
    BARE_TOP_LEVEL_PATTERN,
    re.compile(rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+(?:篇|章|部|部分|册|卷).*"),
    re.compile(rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+节.*"),
    re.compile(rf"^{MARKER_PREFIX}[{CHINESE_NUMERAL}]+[、.．].*"),
    re.compile(rf"^{MARKER_PREFIX}[（(][{CHINESE_NUMERAL}0-9]+[）)].*"),
    re.compile(rf"^{MARKER_PREFIX}\d+(?:\.\d+){{1,4}}(?:\s|[、.．)]|$).*"),
    re.compile(rf"^{MARKER_PREFIX}附件(?:[一二三四五六七八九十0-9]+|[（(][一二三四五六七八九十0-9]+[）)]).*"),
)

TOP_LEVEL_PATTERN = re.compile(
    rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+(?:篇|章|部|部分|册|卷).*"
)
CHAPTER_PATTERN = re.compile(rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+章.*")
SECTION_PATTERN = re.compile(rf"^{MARKER_PREFIX}第[{CHINESE_NUMERAL}0-9]+节.*")
CHINESE_ITEM_PATTERN = re.compile(rf"^{MARKER_PREFIX}[{CHINESE_NUMERAL}]+[、.．].*")
PAREN_ITEM_PATTERN = re.compile(rf"^{MARKER_PREFIX}[（(][{CHINESE_NUMERAL}0-9]+[）)].*")
DECIMAL_PATTERN = re.compile(rf"^{MARKER_PREFIX}\d+(?:\.\d+){{1,4}}(?:\s|[、.．)]|$)")
APPENDIX_PATTERN = re.compile(rf"^{MARKER_PREFIX}附件(?:[一二三四五六七八九十0-9]+|[（(][一二三四五六七八九十0-9]+[）)]).*")

REQUIREMENT_HINTS = (
    "须",
    "必须",
    "应当",
    "提供",
    "具有",
    "具备",
    "不得",
    "不少于",
    "不低于",
    "至少",
    "否则",
    "无效",
)


def _clean_text(value: Any, limit: int = 1200) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip()[:limit]


def _clean_heading(value: Any) -> str:
    text = _clean_text(value, 240)
    text = re.sub(r"\bHYPERLINK\s+\\l\s+\"[^\"]*\"\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bPAGEREF\s+[^ ]+(?:\s+\\h)?", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\bTOC\b.*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+[-－—]\s*\d+\s*[-－—]\s*$", "", text)
    text = re.sub(r"\s+", " ", text).strip(" -－—")
    return text[:160]


def _style_has_heading_signal(segment: dict[str, Any]) -> bool:
    style = segment.get("style")
    if not isinstance(style, dict):
        return False
    style_id = str(style.get("styleId") or "").lower()
    return (
        style_id.startswith("heading")
        or "标题" in style_id
    )


# 表格行内容类型：不同解析器产出的 contentType 不一致——
#   docx  → "table_row"（document_parser.py）
#   pdf   → "pdf_table_row"（多列表格行，文本用 " | " 连接）
#   xlsx  → "spreadsheet_row"
# PDF 表格行还额外带 `style.tableRow=True`。
#
# 这些行**永远不应被当作章节标题**：否则「2 | 评标方法 | 采购包1:综合评分法
# (具体规则详见第五章)」这类须知前附表行会新建章、截断父章，并把整段须知内容
# 灌进 evaluation（评分标准）target（基准里 5.4 评分准则误召回即源于此——
# 原守卫只精确匹配 "table_row"，漏掉了 "pdf_table_row"）。
_TABLE_ROW_CONTENT_TYPES = frozenset({"table_row", "pdf_table_row", "spreadsheet_row"})


def _is_table_row(segment: dict[str, Any]) -> bool:
    if str(segment.get("contentType") or "") in _TABLE_ROW_CONTENT_TYPES:
        return True
    style = segment.get("style")
    return bool(isinstance(style, dict) and style.get("tableRow"))


def _heading_source_text(segment: dict[str, Any]) -> str:
    if _is_table_row(segment):
        cells = segment.get("cells")
        if isinstance(cells, list):
            values = [
                _clean_text(cell.get("text"), 160)
                for cell in cells
                if isinstance(cell, dict) and _clean_text(cell.get("text"), 160)
            ]
            if len(values) == 1:
                return values[0]
    return _clean_heading(segment.get("text"))


# 「像标题」的**长度闸门**。原值是硬编码的 `84 if 表格行 else 120`。
#
# 这两道闸门是 S6（长条款句被当章标题）的主要漏水口 —— 它和函数末尾那道
# 「结尾为 。！？； 才否决」的闸门配合，让"以编号开头 + 含条款动词 +
# 长度 ≤120 + 以「：」结尾"的**条款引导句**全部通过判定（「：」不在否决标点里）。
#
# 第七轮实测（4 份基准、共 590 章；脚本 `待应用脚本/probe_s313_decouple_design.py`）：
#
#     上限   残留S6   Δ标题消失   其中真标题误杀
#     120       56         0           0      ← 原值
#      40       24        35           0      ← 现值：3 条代价全是填空行/表格行
#      24        0        75           2      （含一条挂着 40 段的真标题）
#
# 取 40：清掉 32/56 章 S6，代价 3 条经逐条判读**全部是填空行/表格行**
# （`(5)政府采购方式：公开招标…`、`（五）投标地点：…`、表格跨行拼接）⇒ 零真标题误杀。
#
# ⚠ 改这两个值会改"什么算标题" ⇒ 改文档树 ⇒ 改送进模型的内容。
#   任何调整都必须同进程交错验证（`待应用脚本/probe_limit40_landed_verify.py`），
#   并逐条判读新消失的标题属"填空行/表格行"（收益）还是"真标题"（误杀）。
HEADING_MAX_TEXT_CHARS = 40
HEADING_MAX_TABLE_ROW_CHARS = 40


def _looks_like_heading(segment: dict[str, Any]) -> bool:
    raw_text = _clean_text(segment.get("text"), 240)
    if _looks_like_toc_entry(raw_text):
        return False
    text = _heading_source_text(segment)
    if not text or text in {"目录", "目 录", "索引"}:
        return False
    compact = re.sub(r"\s+", "", text).upper()
    if "HYPERLINK" in compact or "PAGEREF" in compact or compact.startswith("TOC"):
        return False

    style_signal = _style_has_heading_signal(segment)
    if _is_table_row(segment) and "|" in str(segment.get("text") or ""):
        return False
    if not style_signal:
        limit = (
            HEADING_MAX_TABLE_ROW_CHARS
            if _is_table_row(segment)
            else HEADING_MAX_TEXT_CHARS
        )
        if len(text) > limit:
            return False

    prefix_signal = any(pattern.match(text) for pattern in HEADING_PREFIXES)
    term_signal = _heading_starts_with_section_term(text)
    if not style_signal and not prefix_signal and not term_signal:
        return False
    if (
        not style_signal
        and DECIMAL_PATTERN.match(text)
        and len(re.sub(r"[\d\s,，.．]", "", text)) <= 2
    ):
        return False
    if (
        not style_signal
        and len(text) > 24
        and any(
            hint in text
            for hint in (
                "若出现",
                "按照",
                "根据",
                "应当",
                "不得",
                "必须",
            )
        )
    ):
        return False

    if prefix_signal and not style_signal:
        tail = re.sub(
            r"^(?:第[{0}0-9]+(?:篇|章|部|部分|册|卷)|第[{0}0-9]+节|[{0}]+|附件.*?)[、.．:\s：)]*".format(
                CHINESE_NUMERAL
            ),
            "",
            text,
        )
        if any(hint in tail[:24] for hint in REQUIREMENT_HINTS) and len(text) > 24:
            return False
    # ``text`` has already been NFKC-normalised by ``_clean_heading``, so the
    # full-width forms ``；！？`` arrive here as ``;!?``.  Matching only the
    # full-width class left every list item that ends with a semicolon
    # ("3）项目团队配备（格式在附件）；") looking like a heading, which then
    # swallowed the following sibling sections.  Cover both widths.
    if not style_signal and re.search(r"[。！？；!?;]$", text):
        return False
    return True


def _looks_like_toc_entry(text: str) -> bool:
    clean = _clean_text(text, 240)
    if not clean:
        return False
    if re.search(r"\b(?:TOC|HYPERLINK|PAGEREF)\b", clean, flags=re.IGNORECASE):
        return True
    if not any(pattern.match(clean) for pattern in HEADING_PREFIXES):
        return False
    if re.search(r"(?:\s|[.·…])[-－]?\s*\d+\s*[-－]?$", clean):
        return True
    return bool(re.search(r"\s+\d+$", clean)) and len(clean) <= 80


# 「混合篇」判定的标题长度上界（字符）。只在**短标题**上标记 `isMixed`：
# 长标题里偶然同时出现两类关键词很常见（如「交付时间：…达到验收…」），那不是"混合篇"，
# 而是 S6（长条款句被当章标题）。取 32，与清单标签的截断长度同量级。
MIXED_HEADING_MAX_CHARS = 32


def _section_types_for_heading(heading: str) -> list[str]:
    """返回该标题**命中的全部**类型（按 `SECTION_TYPE_TERMS` 的优先级顺序）。

    与 `_section_type_for_heading` **同源**：后者就是取本列表的首个元素。
    单独暴露这个函数是为了记录「混合篇」（同时命中 ≥2 个类型）—— 有序首命中会把
    这类标题判成优先级最高的那个，但标题里其实**同时**含另一类的语义：

        第四篇 资格审查及评标办法      qualification(先) + evaluation(后)
        （一）评审因素                评价类，但父章是 qualification

    ⚠ 本函数**只用于记录/诊断**，不参与任何判定 —— S2 的第 1 步就是"先只记录、
    不改判定"，等拿到 `isMixed` 的分布再决定要不要动 `sectionType`（动它会进模型输入）。
    """
    compact = re.sub(r"\s+", "", heading)
    return [
        section_type
        for section_type, terms in SECTION_TYPE_TERMS
        if any(term in compact for term in terms)
    ]


def _section_type_for_heading(heading: str) -> str:
    compact = re.sub(r"\s+", "", heading)
    for section_type, terms in SECTION_TYPE_TERMS:
        if any(term in compact for term in terms):
            return section_type
    return "other"


def _heading_starts_with_section_term(text: str) -> bool:
    """标题型关键词须出现在开头（前 8 字），且整句不含义务动词（须/应/提供/具备等）。

    正文句如"投标人须具备三年以上类似项目业绩经验"虽含"业绩"，但"业绩"在句尾、
    且含义务动词"须/具备"，不应判为标题。

    同理，以 section term 开头的**算式行**也不是标题——例如
    "投标报价得分=(评标基准价/投标报价)×100" 以"投标报价"（pricing 词）开头，
    若被误判为标题会把 5.4 评分细则整段截断（见 5.4 评分准则误召回）。等号/乘号/
    加减号等算符与标题互斥。
    """
    compact = re.sub(r"\s+", "", text)
    head = compact[:8]
    has_term = any(
        term in head
        for _section_type, terms in SECTION_TYPE_TERMS
        for term in terms
    )
    if not has_term:
        return False
    if re.search(r"[=＝×÷＋+]", compact):
        return False
    return not any(hint in compact for hint in REQUIREMENT_HINTS)


def _level_for_heading(heading: str, segment: dict[str, Any]) -> int:
    # A semantic numbering marker is more reliable than Word's outline level.
    # In the wild, chapter headings are often stored with outlineLevel=1,
    # although they are the business-level root of the document.
    if TOP_LEVEL_PATTERN.match(heading):
        return 1
    if BARE_TOP_LEVEL_PATTERN.match(heading):
        return 1
    if CHAPTER_PATTERN.match(heading):
        return 1
    if APPENDIX_PATTERN.match(heading):
        return 1
    if SECTION_PATTERN.match(heading):
        return 2
    if CHINESE_ITEM_PATTERN.match(heading):
        return 2
    if PAREN_ITEM_PATTERN.match(heading):
        return 3
    if DECIMAL_PATTERN.match(heading):
        digits = re.match(r"^\d+(?:\.\d+)*", heading)
        return min((digits.group(0).count(".") + 1) if digits else 2, 8)

    style = segment.get("style") if isinstance(segment.get("style"), dict) else {}
    outline_level = str(style.get("outlineLevel") or "").strip()
    if outline_level.isdigit():
        return min(int(outline_level) + 1, 8)
    return 2


def _segment_locator(segment: dict[str, Any], fallback_index: int) -> str:
    return _clean_text(segment.get("sourceLocator"), 160) or f"第{fallback_index + 1}个内容单元"


def _section_text(segments: list[dict[str, Any]], start: int, end: int) -> str:
    return "\n".join(
        _clean_text(segment.get("text"), 12000)
        for segment in segments[start : end + 1]
        if _clean_text(segment.get("text"), 12000)
    )


def _heading_span_length(
    index: int,
    headings: list[dict[str, Any]],
    total_segments: int,
) -> int:
    """Number of segments a heading covers, before its next peer."""

    level = int(headings[index]["level"])
    for next_candidate in headings[index + 1 :]:
        if int(next_candidate["level"]) <= level:
            return int(next_candidate["position"]) - int(headings[index]["position"])
    return total_segments - int(headings[index]["position"])


def _drop_duplicated_major_headings(
    headings: list[dict[str, Any]],
    total_segments: int,
) -> list[dict[str, Any]]:
    """Keep one section per document-level heading.

    A chapter title such as "第二部分 前附表及投标人须知" is regularly quoted a
    second time inside another chapter's 编制说明, where it is a cross-reference
    rather than a new chapter.  Accepting that duplicate builds a phantom root
    that re-parents every following sibling section (the 投标文件格式 forms end
    up under 投标人须知).  The genuine occurrence is the one that actually spans
    content, so the widest span wins and only exact repeats are considered.
    """

    grouped: dict[str, list[int]] = {}
    for index, candidate in enumerate(headings):
        if int(candidate.get("level") or 0) > 1:
            continue
        key = re.sub(r"\s+", "", str(candidate.get("heading") or "")).upper()
        if not key:
            continue
        grouped.setdefault(key, []).append(index)

    dropped: set[int] = set()
    for positions in grouped.values():
        if len(positions) < 2:
            continue
        spans = {
            index: _heading_span_length(index, headings, total_segments)
            for index in positions
        }
        keep = max(spans, key=lambda index: (spans[index], -index))
        dropped.update(index for index in positions if index != keep)
    if not dropped:
        return headings
    return [
        candidate
        for index, candidate in enumerate(headings)
        if index not in dropped
    ]


def build_section_tree(segments: list[dict[str, Any]]) -> dict[str, Any]:
    normalized_segments = [
        segment for segment in segments
        if isinstance(segment, dict) and _clean_text(segment.get("text"), 12000)
    ]
    headings: list[dict[str, Any]] = []
    for position, segment in enumerate(normalized_segments):
        heading = _heading_source_text(segment)
        if not _looks_like_heading(segment):
            continue
        headings.append(
            {
                "position": position,
                "heading": heading,
                "sourceSegmentId": str(segment.get("id") or ""),
                "sourceLocator": _segment_locator(segment, position),
                "level": _level_for_heading(heading, segment),
                "isMajor": _level_for_heading(heading, segment) <= 1,
                "sectionType": _section_type_for_heading(heading),
                # S2 第 1 步：只记录，不参与判定。`sectionType` 仍是有序首命中的结果，
                # 一字未动；这里额外把"命中的全部类型"和"是否混合篇"留下，
                # 用于后续决定要不要改判定（改判定会进模型输入，需另行验证）。
                "sectionTypeHits": _section_types_for_heading(heading),
                "isMixed": (
                    len(_section_types_for_heading(heading)) >= 2
                    and len(heading) <= MIXED_HEADING_MAX_CHARS
                ),
            }
        )

    headings = _drop_duplicated_major_headings(headings, len(normalized_segments))

    sections: list[dict[str, Any]] = []
    for index, candidate in enumerate(headings):
        end_position = len(normalized_segments) - 1
        for next_candidate in headings[index + 1 :]:
            if int(next_candidate["level"]) <= int(candidate["level"]):
                end_position = int(next_candidate["position"]) - 1
                break

        parent = next(
            (
                section for section in reversed(sections)
                if int(section["level"]) < int(candidate["level"])
                and int(section["startPosition"]) < int(candidate["position"])
            ),
            None,
        )
        section_type = candidate["sectionType"]
        if section_type == "other" and parent and parent.get("sectionType") not in {"other", "basic"}:
            section_type = str(parent["sectionType"])
        section_path = [
            *(parent.get("sectionPath", []) if parent else []),
            candidate["heading"],
        ]
        section_id = f"SEC-{index + 1:04d}"
        covered_segments = normalized_segments[
            int(candidate["position"]) : end_position + 1
        ]
        sections.append(
            {
                "id": section_id,
                "sectionId": section_id,
                "sectionType": section_type,
                "sectionTypeHits": candidate.get("sectionTypeHits") or [],
                "isMixed": bool(candidate.get("isMixed")),
                "heading": candidate["heading"],
                "level": candidate["level"],
                "isMajor": bool(candidate.get("isMajor")),
                "parentId": parent["id"] if parent else "",
                "sectionPath": section_path,
                "headingSegmentId": candidate["sourceSegmentId"],
                "startPosition": candidate["position"],
                "endPosition": end_position,
                "startLocator": candidate["sourceLocator"],
                "endLocator": _segment_locator(
                    normalized_segments[end_position],
                    end_position,
                ),
                "segmentIds": [
                    str(segment.get("id") or "")
                    for segment in covered_segments
                    if str(segment.get("id") or "")
                ],
                "text": _section_text(
                    normalized_segments,
                    int(candidate["position"]),
                    end_position,
                ),
            }
        )

    if headings:
        first_heading_position = int(headings[0]["position"])
        if first_heading_position > 0:
            prefix_segments = normalized_segments[:first_heading_position]
            sections.insert(
                0,
                {
                    "id": "SEC-0000",
                    "sectionId": "SEC-0000",
                    "sectionType": "other",
                    # 与其余 section 保持同一 shape（S2 第 1 步新增的两个记录键）
                    "sectionTypeHits": [],
                    "isMixed": False,
                    "heading": "前置内容",
                    "level": 0,
                    "isMajor": True,
                    "parentId": "",
                    "sectionPath": ["前置内容"],
                    "headingSegmentId": "",
                    "startPosition": 0,
                    "endPosition": first_heading_position - 1,
                    "startLocator": _segment_locator(prefix_segments[0], 0),
                    "endLocator": _segment_locator(
                        prefix_segments[-1],
                        first_heading_position - 1,
                    ),
                    "segmentIds": [
                        str(segment.get("id") or "")
                        for segment in prefix_segments
                        if str(segment.get("id") or "")
                    ],
                    "text": _section_text(
                        normalized_segments,
                        0,
                        first_heading_position - 1,
                    ),
                    "implicit": True,
                },
            )
    elif normalized_segments:
        sections = [
            {
                "id": "SEC-0000",
                "sectionId": "SEC-0000",
                "sectionType": "other",
                "heading": "未分类内容",
                "level": 0,
                "isMajor": True,
                "parentId": "",
                "sectionPath": ["未分类内容"],
                "headingSegmentId": "",
                "startPosition": 0,
                "endPosition": len(normalized_segments) - 1,
                "startLocator": _segment_locator(normalized_segments[0], 0),
                "endLocator": _segment_locator(
                    normalized_segments[-1],
                    len(normalized_segments) - 1,
                ),
                "segmentIds": [
                    str(segment.get("id") or "")
                    for segment in normalized_segments
                    if str(segment.get("id") or "")
                ],
                "text": _section_text(
                    normalized_segments,
                    0,
                    len(normalized_segments) - 1,
                ),
                "implicit": True,
            }
        ]

    for section in sections:
        section.pop("startPosition", None)
        section.pop("endPosition", None)

    return {
        "sections": sections,
        "sectionCount": len(sections),
        "headingCount": len(headings),
        "warnings": (
            []
            if headings
            else ["未识别到明确章节标题，已将内容归入未分类章节。"]
            if normalized_segments
            else ["文档没有可用于建立章节树的内容单元。"]
        ),
    }


def enrich_document_structure(parsed: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(parsed)
    structure = build_section_tree(result.get("segments", []))
    result["sections"] = structure["sections"]
    result["structureWarnings"] = structure["warnings"]
    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    metadata["sectionCount"] = structure["sectionCount"]
    metadata["headingCount"] = structure["headingCount"]
    result["metadata"] = metadata
    result["warnings"] = list(
        dict.fromkeys(
            [
                *(
                    item for item in result.get("warnings", [])
                    if isinstance(item, str)
                ),
                *structure["warnings"],
            ]
        )
    )
    return result
