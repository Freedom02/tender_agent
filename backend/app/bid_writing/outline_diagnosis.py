# -*- coding: utf-8 -*-
"""目录规划诊断（只读）：算清「已有哪些章节 / 缺哪些要求响应章节 / 建议哪些自由发挥章节」。

定位与边界：
- **只读**：不写盘、不调用 LLM、不修改目录；只消费现有解析结果
  （``ParseBundle.requirements`` / ``evaluationItems`` / ``submissionStructure``）
  与当前 ``bidDocument.plan``。
- **同一套口径**：与后续「响应章节补充」共用 ``RESPONSE_ARCHETYPES`` 原型表，
  保证前端诊断看到的候选 = 后续实际会补出来的章节，不会出现两套口径。
- **两类章节**：
  * ``required_by_tender`` 要求章节——招标文件条款要求提交/响应的内容；
  * ``ai_initiative``       自由发挥章节——招标文件未强制，但属于得分或完整度加分项。
- **响应类型要分清**：挂在 ``table`` / ``fixed_form`` / ``attachment`` 上的条款算
  「表格式响应」，不等于有叙述性章节承载。因此每条缺口同时给出三个口径
  （仅响应表承载 / 完全未绑定 / 已有叙述承载），避免把「响应表已覆盖」误报成
  「已响应」，也避免把「响应表覆盖」误报成「完全没管」。
- **不越界**：资格、业绩、人员、财务、信用、合规、否决、报价、合同等领域的条款
  由商务模板 / 偏离表 / 资格材料承载，不驱动叙述性章节，不参与原型聚类。
"""
from __future__ import annotations

from typing import Any

from .schemas import requirement_domain

ORIGIN_REQUIRED = "required_by_tender"
ORIGIN_INITIATIVE = "ai_initiative"

#: 章节原型表。archetype 是稳定标识（后续「响应章节补充」用它生成 sectionId 与归位），
#: title 只是参考标题——允许 Pi 按项目语义改名，归属靠 archetype 而非标题。
#: evaluationSections 复用 planning.match_eval_section 的 t-01..t-07 关键词池。
#: prerequisite 表示「前置原型达到规模才建议成章」，避免小规模项目凭空长章。
RESPONSE_ARCHETYPES: tuple[dict[str, Any], ...] = (
    {
        "archetype": "technical_plan",
        "title": "技术方案",
        "origin": ORIGIN_REQUIRED,
        "volumeKeywords": ("技术", "质量", "服务"),
        "domains": ("technical",),
        "keywords": ("技术", "参数", "功能", "性能", "指标", "配置", "接口", "架构"),
        "evaluationSections": ("t-02",),
        "minRequirements": 5,
        "reason": "技术类要求数量较多，但目录中没有叙述性章节承载技术方案",
    },
    {
        "archetype": "evidence_materials",
        "title": "技术参数证明材料",
        "origin": ORIGIN_REQUIRED,
        "volumeKeywords": ("技术", "质量"),
        "domains": (),
        "keywords": (),
        "evaluationSections": (),
        "minRequirements": 1,
        # 证明材料是为技术响应服务的：不从资格/资信材料里取，而是从技术条款里
        # 挑出「需要逐条附证明材料」的那些条款作为依据。
        "derivedFrom": {
            "archetype": "technical_plan",
            "keywords": ("参数", "证明材料", "检测报告", "指标", "截图", "证书"),
            "minRequirements": 3,
        },
        "prerequisite": {"archetype": "technical_plan", "minRequirements": 5},
        "reason": "技术参数类条款需要逐条附证明材料，需有章节承载原件插页",
    },
    {
        "archetype": "implementation",
        "title": "实施方案与进度计划",
        "origin": ORIGIN_REQUIRED,
        "volumeKeywords": ("技术", "服务"),
        "domains": ("service_plan", "service"),
        "keywords": ("实施", "进度", "工期", "交付", "施工", "安装", "调试", "部署"),
        "evaluationSections": ("t-03",),
        "minRequirements": 1,
        "reason": "存在实施/交付/进度类要求，需有章节给出可落地安排",
    },
    {
        "archetype": "quality",
        "title": "质量保证措施",
        "origin": ORIGIN_REQUIRED,
        "volumeKeywords": ("技术", "质量"),
        "domains": ("quality",),
        "keywords": ("质量", "质保", "验收", "合格", "检验"),
        "evaluationSections": ("t-04",),
        "minRequirements": 1,
        "reason": "存在质量、质保或验收类要求，需有章节说明质量保证措施",
    },
    {
        "archetype": "service",
        "title": "售后服务方案",
        "origin": ORIGIN_REQUIRED,
        "volumeKeywords": ("技术", "服务", "商务"),
        "domains": ("service",),
        "keywords": ("售后", "运维", "维保", "服务承诺", "响应时间", "技术支持", "保修", "免费维护"),
        "evaluationSections": ("t-05",),
        "minRequirements": 1,
        "reason": "存在售后服务或运维类要求，需有章节给出服务承诺与流程",
    },
    {
        "archetype": "training",
        "title": "培训方案",
        "origin": ORIGIN_REQUIRED,
        "volumeKeywords": ("技术", "服务"),
        "domains": ("training",),
        "keywords": ("培训", "实训", "教学", "课程", "上机"),
        "evaluationSections": ("t-06",),
        "minRequirements": 1,
        "reason": "存在培训类要求，需有章节说明培训内容与安排",
    },
    {
        "archetype": "emergency",
        "title": "应急保障方案",
        "origin": ORIGIN_INITIATIVE,
        "volumeKeywords": ("技术", "服务"),
        "domains": (),
        "keywords": ("应急", "预案", "突发", "故障", "灾备", "演练"),
        "evaluationSections": ("t-07",),
        "minRequirements": 1,
        "reason": "应急与风险兜底属于评审加分内容，招标文件通常不单独设为提交项",
    },
    {
        "archetype": "understanding",
        "title": "项目理解与需求分析",
        "origin": ORIGIN_INITIATIVE,
        "volumeKeywords": ("技术", "服务", "商务"),
        "domains": (),
        "keywords": (),
        "evaluationSections": ("t-01",),
        "minRequirements": 0,
        # 有主观分或技术条款成规模时，项目理解都是值得主动写的加分章
        "prerequisite": {"archetype": "technical_plan", "minRequirements": 5},
        "triggerOnPrerequisite": True,
        "reason": "评审通常关注对采购需求的理解程度；招标文件很少列为独立提交项",
    },
    {
        "archetype": "advantage",
        "title": "技术优势与创新点",
        "origin": ORIGIN_INITIATIVE,
        "volumeKeywords": ("技术", "服务", "商务"),
        "domains": (),
        "keywords": ("优势", "创新", "亮点", "特色", "增值"),
        # 不参与评分点归位：t-02 池已被 technical_plan 占用，否则同一评分点会被
        # 两个原型重复建议。本原型只由条款关键词触发。
        "evaluationSections": (),
        "minRequirements": 1,
        "reason": "展示差异化优势与创新点，属于自主加分内容",
    },
)

#: 叙述性（可撰写正文）的响应类型 / 条目类型。
NARRATIVE_RESPONSE_TYPES = {"chapter_text", "narrative"}
#: 表格式 / 材料式响应：能承载条款，但不算叙述性章节。
TABLE_RESPONSE_TYPES = {"table", "fixed_form", "fixed_table", "deviation", "attachment"}
#: 这些领域的条款由商务模板、偏离表或资格材料承载，不驱动叙述性章节。
NON_NARRATIVE_DOMAINS = frozenset(
    {
        "qualification",
        "performance",
        "personnel",
        "financial",
        "credit",
        "region",
        "compliance",
        "pricing",
        "contract",
        "rejection",
        "document",
        "template",
        "project_info",
        "evaluation",
        # 单独的证明材料条款（营业执照、授权书等）由资格材料承载；
        # 技术参数证明材料改由 technical_plan 派生（见 evidence_materials.derivedFrom）。
        "evidence",
    }
)

_MAX_REQUIREMENT_SAMPLES = 20
_MAX_UNBOUND_SAMPLES = 10


# ---------------------------------------------------------------------------
# 供其它模块复用的公共入口（工具上下文 / 智能体侧只读工具都基于同一套口径）
# ---------------------------------------------------------------------------
def requirement_id_of(item: dict[str, Any]) -> str:
    """条款 ID（解析结果里 id/requirementId 两种写法都兼容）。"""

    return _requirement_id(item)


def requirement_text_of(item: dict[str, Any]) -> str:
    """条款正文（用于聚类与检索，已归一化空白）。"""

    return _requirement_text(item)


def requirement_domain_of(item: dict[str, Any]) -> str:
    """条款领域键（technical / compliance / qualification …）。"""

    return _requirement_domain_of(item)


def index_plan_bindings(plan: list[dict[str, Any]] | None) -> dict[str, Any]:
    """当前目录对条款的绑定情况：叙述性承载 / 表格式承载 / 响应章节。"""

    index = _index_plan([row for row in (plan or []) if isinstance(row, dict)])
    return {
        "narrative": set(index["boundNarrative"]),
        "table": set(index["boundTable"]),
        "evaluations": set(index["boundEvaluations"]),
        "responseSections": index["responseSections"],
        "formatItems": index["formatItems"],
        "volumes": index["volumes"],
    }


def _text(value: Any, limit: int = 200) -> str:
    return " ".join(str(value or "").split())[:limit]


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value not in (None, "") else []


def _requirement_id(item: dict[str, Any]) -> str:
    return _text(item.get("id") or item.get("requirementId"), 80)


def _requirement_text(item: dict[str, Any]) -> str:
    return _text(
        item.get("requirement")
        or item.get("text")
        or item.get("normalizedRequirement")
        or item.get("title")
        or item.get("sourceText"),
        400,
    )


def _requirement_domain_of(item: dict[str, Any]) -> str:
    """归并出用于聚类的领域键：domain + responseType + requirementClass。"""

    domain = requirement_domain(item)
    if domain and domain != "other":
        return domain
    response_type = _text(item.get("responseType"), 40).lower()
    requirement_class = _text(item.get("requirementClass"), 40).lower()
    for candidate in (response_type, requirement_class):
        if candidate == "technical_response":
            return "technical"
        if candidate in {"evidence", "service", "service_plan", "quality", "training"}:
            return candidate
        if candidate == "mandatory_confirmation":
            return "compliance"
    return "other"


def _is_narrative_node(node: dict[str, Any]) -> bool:
    response_type = _text(node.get("responseType"), 40)
    if response_type in NARRATIVE_RESPONSE_TYPES:
        return True
    return _text(node.get("itemType"), 40) == "narrative"


def _index_plan(plan: list[dict[str, Any]]) -> dict[str, Any]:
    """把当前目录切成：卷 / 原文件项 / 响应章节，并统计条款绑定方式。"""

    nodes = [row for row in plan if isinstance(row, dict)]
    volumes: list[dict[str, Any]] = []
    format_items: list[dict[str, Any]] = []
    response_sections: list[dict[str, Any]] = []
    for row in nodes:
        node_type = _text(row.get("nodeType"), 40)
        if node_type == "volume" or (
            not row.get("parentSectionId")
            and _text(row.get("responseType"), 40) == "section_group"
        ):
            volumes.append(row)
        elif node_type == "format_item":
            format_items.append(row)
        elif row.get("parentSectionId"):
            response_sections.append(row)

    bound_narrative: set[str] = set()
    bound_table: set[str] = set()
    bound_evaluations: set[str] = set()
    for row in nodes:
        if row in volumes:
            continue
        requirement_ids = [
            _text(entry, 80) for entry in _as_list(row.get("requirementIds")) if _text(entry, 80)
        ]
        if requirement_ids:
            target = bound_narrative if _is_narrative_node(row) else bound_table
            target.update(requirement_ids)
        bound_evaluations.update(
            _text(entry, 80) for entry in _as_list(row.get("evaluationIds")) if _text(entry, 80)
        )

    volume_rows: list[dict[str, Any]] = []
    for volume in volumes:
        volume_id = _text(volume.get("sectionId"), 80)
        children = [row for row in nodes if _text(row.get("parentSectionId"), 80) == volume_id]
        item_rows = [row for row in children if _text(row.get("nodeType"), 40) == "format_item"]
        volume_rows.append(
            {
                "volumeId": volume_id,
                "title": _text(volume.get("title"), 120),
                "kind": _text(volume.get("kind"), 40),
                "formatItemCount": len(item_rows),
                "responseSectionCount": len(children) - len(item_rows),
                "narrativeItemCount": sum(1 for row in item_rows if _is_narrative_node(row)),
                "canHostNarrative": any(_is_narrative_node(row) for row in children),
            }
        )

    return {
        "nodes": nodes,
        "volumes": volumes,
        "volumeRows": volume_rows,
        "formatItems": format_items,
        "responseSections": response_sections,
        "boundNarrative": bound_narrative,
        "boundTable": bound_table,
        "boundEvaluations": bound_evaluations,
    }


def _match_archetype_by_title(title: str) -> str:
    """标题 → 原型（复用 planning.match_eval_section 的 t-01..t-07 关键词池）。"""

    if not title:
        return ""
    from .planning import match_eval_section

    section_key = match_eval_section(title)
    if not section_key:
        return ""
    for archetype in RESPONSE_ARCHETYPES:
        if section_key in archetype["evaluationSections"]:
            return archetype["archetype"]
    return ""


def _target_volume(
    volume_rows: list[dict[str, Any]], archetype: dict[str, Any]
) -> dict[str, Any] | None:
    """给原型挑一个归属卷：按标题关键词命中，只有一个卷时直接用它。"""

    if not volume_rows:
        return None
    keywords = archetype.get("volumeKeywords") or ()
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in volume_rows:
        title = str(row.get("title") or "")
        score = sum(1 for keyword in keywords if keyword in title)
        scored.append((score, row))
    best_score = max(score for score, _row in scored)
    if best_score <= 0:
        return volume_rows[0] if len(volume_rows) == 1 else None
    return next(row for score, row in scored if score == best_score)


def _archetype_score(archetype: dict[str, Any], domain: str, text: str) -> int:
    """条款 → 原型的匹配强度：领域命中权重高于关键词命中，避免同一条款被重复计数。"""

    if domain in NON_NARRATIVE_DOMAINS:
        return 0
    score = 0
    domains = archetype.get("domains") or ()
    if domains and domain in domains:
        score += 3
    keywords = archetype.get("keywords") or ()
    if keywords and text:
        score += min(sum(1 for keyword in keywords if keyword in text), 3)
    return score


def _cluster_requirements(
    requirements: list[dict[str, Any]],
    bound_narrative: set[str],
    bound_table: set[str],
) -> dict[str, dict[str, Any]]:
    """按原型聚类条款，并分别统计「叙述承载 / 仅响应表承载 / 完全未绑定」。

    一条款只归一个原型（取匹配强度最高者，同分按原型表顺序），
    这样各原型的条数相加不会超过条款总数，前端看到的缺口规模才可信。
    """

    clusters: dict[str, dict[str, Any]] = {
        archetype["archetype"]: {
            "archetype": archetype["archetype"],
            "requirementIds": [],
            "domainCounts": {},
            "narrativeBoundCount": 0,
            "tableBoundCount": 0,
            "unboundCount": 0,
        }
        for archetype in RESPONSE_ARCHETYPES
    }
    for item in requirements:
        requirement_id = _requirement_id(item)
        if not requirement_id:
            continue
        domain = _requirement_domain_of(item)
        text = _requirement_text(item)
        best_key = ""
        best_score = 0
        for archetype in RESPONSE_ARCHETYPES:
            score = _archetype_score(archetype, domain, text)
            if score > best_score:
                best_key = archetype["archetype"]
                best_score = score
        if not best_key:
            continue
        bucket = clusters[best_key]
        if requirement_id in bound_narrative:
            bucket["narrativeBoundCount"] += 1
            continue
        bucket["requirementIds"].append(requirement_id)
        bucket["domainCounts"][domain] = bucket["domainCounts"].get(domain, 0) + 1
        if requirement_id in bound_table:
            bucket["tableBoundCount"] += 1
        else:
            bucket["unboundCount"] += 1
    return clusters


#: 评分项里混入的评标流程文本（“评标委员会由…组成”“评标纪律”等）不是评分点，
#: 它们会让原型凭空长章，因此在归位前先过滤。
_EVALUATION_NOISE_MARKERS = (
    "评标委员会",
    "评标纪律",
    "评标办法",
    "评标方法",
    "评标标准",
    "评标情况",
    "评标结果",
    "评标过程",
    "评委",
    "保密",
    "开标",
    "无效投标",
    "中标候选人",
    "采购人代表",
    "资格审查小组",
    "录音录像",
)
#: 评分点名称应当是短标签；超过该长度基本是正文段落而非评分项。
_MAX_EVALUATION_LABEL = 40


def _is_scoring_point_label(value: str) -> bool:
    """判断一段文本是否像评分点名称（短标签且不含评标流程用语）。"""

    text = value.strip()
    if not text or len(text) > _MAX_EVALUATION_LABEL:
        return False
    return not any(marker in text for marker in _EVALUATION_NOISE_MARKERS)


def _evaluation_signals(evaluation_items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """评分项 → 原型：按关键词把评分点归到原型上（与 evaluation_strategy 同池）。

    只使用 ``item`` / ``factor`` 这两个「名称」字段，且必须通过
    :func:`_is_scoring_point_label`：解析出的 ``criteria`` 常是整段正文，
    拼进来会把「技术评分 · 项目理解」误判成技术方案，也会把评标流程文本
    误判成质量评分点。
    """

    from .planning import match_eval_section

    signals: dict[str, dict[str, Any]] = {}
    for item in evaluation_items:
        if not isinstance(item, dict):
            continue
        evaluation_id = _text(item.get("id") or item.get("evaluationId"), 80)
        name = _text(item.get("item"), 120)
        factor = _text(item.get("factor"), 120)
        label = ""
        section_key = ""
        for candidate in (name, factor):
            if not _is_scoring_point_label(candidate):
                continue
            key = match_eval_section(candidate)
            if key:
                label, section_key = candidate, key
                break
        if not section_key:
            continue
        for archetype in RESPONSE_ARCHETYPES:
            if section_key not in archetype["evaluationSections"]:
                continue
            bucket = signals.setdefault(
                archetype["archetype"],
                {"evaluationIds": [], "score": 0.0, "labels": []},
            )
            if evaluation_id and evaluation_id not in bucket["evaluationIds"]:
                bucket["evaluationIds"].append(evaluation_id)
            try:
                bucket["score"] = round(bucket["score"] + float(item.get("score") or 0), 2)
            except (TypeError, ValueError):
                pass
            if label.strip() and len(bucket["labels"]) < 3:
                bucket["labels"].append(label.strip()[:120])
            break
    return signals


def _prerequisite_ok(
    archetype: dict[str, Any],
    clusters: dict[str, dict[str, Any]],
) -> bool:
    """前置原型是否已达规模（未配置前置条件时恒为真）。"""

    prerequisite = archetype.get("prerequisite")
    if not isinstance(prerequisite, dict):
        return True
    key = str(prerequisite.get("archetype") or "")
    minimum = int(prerequisite.get("minRequirements") or 1)
    cluster = clusters.get(key)
    if not cluster:
        return False
    return len(cluster["requirementIds"]) >= minimum


def diagnose(
    parse_bundle: dict[str, Any] | None,
    plan: list[dict[str, Any]] | None,
    *,
    workspace_id: str = "",
) -> dict[str, Any]:
    """产出目录规划诊断结果（纯函数，可直接单测）。"""

    bundle = parse_bundle if isinstance(parse_bundle, dict) else {}
    requirements = [row for row in _as_list(bundle.get("requirements")) if isinstance(row, dict)]
    evaluation_items = [
        row for row in _as_list(bundle.get("evaluationItems")) if isinstance(row, dict)
    ]
    plan_rows = [row for row in _as_list(plan) if isinstance(row, dict)]

    index = _index_plan(plan_rows)
    bound_narrative: set[str] = index["boundNarrative"]
    bound_table: set[str] = index["boundTable"]
    volume_rows: list[dict[str, Any]] = index["volumeRows"]

    all_requirement_ids = {
        _requirement_id(item) for item in requirements if _requirement_id(item)
    }
    # 保持解析结果里的原始顺序（集合迭代顺序不稳定，会让前端样例每次刷新都在变）
    unbound_ids = list(
        dict.fromkeys(
            requirement_id
            for item in requirements
            for requirement_id in [_requirement_id(item)]
            if requirement_id
            and requirement_id not in bound_narrative
            and requirement_id not in bound_table
        )
    )

    clusters = _cluster_requirements(requirements, bound_narrative, bound_table)
    signals = _evaluation_signals(evaluation_items)
    existing_archetypes = {
        _match_archetype_by_title(str(row.get("title") or ""))
        for row in index["responseSections"]
    }

    warnings: list[str] = []
    notes: list[str] = []
    if not plan_rows:
        warnings.append("当前工作区还没有目录：请先在第一步生成目录，再查看诊断。")
    if not requirements:
        warnings.append("解析结果中没有可用的要求条款，诊断只能依据评分项与目录结构。")
    if not volume_rows and plan_rows:
        warnings.append("当前目录没有卷节点，建议补充章节前先完成卷规划（导出分册依赖卷归属）。")
    if evaluation_items and not signals:
        notes.append(
            "未从评分项中识别到主观评分点（若本项目为最低评标价法，属正常现象）："
            "自由发挥章节建议主要由技术条款缺口驱动。"
        )

    text_by_id = {
        _requirement_id(item): _requirement_text(item)
        for item in requirements
        if _requirement_id(item)
    }

    gaps: list[dict[str, Any]] = []
    initiatives: list[dict[str, Any]] = []
    for archetype in RESPONSE_ARCHETYPES:
        key = archetype["archetype"]
        cluster = clusters.get(key) or {
            "requirementIds": [],
            "domainCounts": {},
            "tableBoundCount": 0,
            "unboundCount": 0,
            "narrativeBoundCount": 0,
        }
        derived = archetype.get("derivedFrom")
        derived = derived if isinstance(derived, dict) else None
        if derived:
            # 派生型候选：依据来自另一个原型的条款子集（例如证明材料取技术条款里
            # 明确要求逐条附证明的那些），不做独立聚类，避免把资格材料算成技术证明。
            source = clusters.get(str(derived.get("archetype") or "")) or {}
            derived_keywords = tuple(derived.get("keywords") or ())
            requirement_ids = [
                requirement_id
                for requirement_id in (source.get("requirementIds") or [])
                if not derived_keywords
                or any(
                    keyword in text_by_id.get(requirement_id, "")
                    for keyword in derived_keywords
                )
            ]
            domain_counts = {str(source.get("archetype") or "derived"): len(requirement_ids)}
            minimum = max(int(derived.get("minRequirements") or 1), 1)
        else:
            requirement_ids = list(cluster["requirementIds"])
            domain_counts = dict(cluster["domainCounts"])
            minimum = max(int(archetype.get("minRequirements") or 0), 1)

        pending = len(requirement_ids)
        table_bound = len([item for item in requirement_ids if item in bound_table])
        unbound = len([item for item in requirement_ids if item not in bound_table])
        signal = signals.get(key) or {"evaluationIds": [], "score": 0.0, "labels": []}
        prerequisite_ok = _prerequisite_ok(archetype, clusters)
        has_signal = pending >= minimum or bool(signal["evaluationIds"])
        if archetype.get("triggerOnPrerequisite") and prerequisite_ok:
            # 「自由发挥」型章节可以只由前置原型的规模触发：
            # 技术条款成规模时，项目理解本身就是值得主动写的加分章。
            has_signal = True
        if not has_signal or not prerequisite_ok:
            continue
        if key in existing_archetypes:
            continue

        if pending:
            reason = f"{pending} 条相关条款没有叙述性章节承载"
            if table_bound:
                reason += f"（其中 {table_bound} 条目前仅由响应表承载）"
        else:
            reason = "依据评分项判断的补充建议"
        target = _target_volume(volume_rows, archetype)
        if not target:
            reason += "；未找到可归属的卷，暂不建议自动补充"

        entry = {
            "archetype": key,
            "title": archetype["title"],
            "origin": archetype["origin"],
            "targetVolumeId": str(target.get("volumeId") or "") if target else "",
            "targetVolumeTitle": str(target.get("title") or "") if target else "",
            "requirementIds": requirement_ids[:_MAX_REQUIREMENT_SAMPLES],
            "requirementCount": pending,
            "tableBoundCount": table_bound,
            "unboundCount": unbound,
            "evaluationIds": list(signal["evaluationIds"])[:_MAX_REQUIREMENT_SAMPLES],
            "evaluationScore": signal["score"] or None,
            "evaluationLabels": list(signal["labels"]),
            "domainCounts": domain_counts,
            "reason": reason,
            "basis": archetype["reason"],
        }
        (gaps if archetype["origin"] == ORIGIN_REQUIRED else initiatives).append(entry)

    unsignalled_evaluations = [
        _text(item.get("id") or item.get("evaluationId"), 80)
        for item in evaluation_items
        if not any(
            _text(item.get("id") or item.get("evaluationId"), 80)
            in (signals.get(archetype["archetype"]) or {}).get("evaluationIds", [])
            for archetype in RESPONSE_ARCHETYPES
        )
    ]

    unbound_samples = []
    for item in requirements:
        if _requirement_id(item) not in unbound_ids:
            continue
        unbound_samples.append(
            {
                "requirementId": _requirement_id(item),
                "domain": _requirement_domain_of(item),
                "responseType": _text(item.get("responseType"), 40),
                "mandatory": bool(item.get("mandatory")),
                "text": _requirement_text(item)[:160],
                "sourceLocator": _text(item.get("sourceLocator"), 80),
            }
        )
        if len(unbound_samples) >= _MAX_UNBOUND_SAMPLES:
            break

    return {
        "workspaceId": workspace_id,
        "summary": {
            "volumes": len(index["volumes"]),
            "formatItems": len(index["formatItems"]),
            "responseSections": len(index["responseSections"]),
            "requirements": len(all_requirement_ids),
            "narrativeBound": len(bound_narrative),
            "tableBound": len(bound_table),
            "unbound": len(unbound_ids),
            "evaluationItems": len(evaluation_items),
            "evaluationMapped": len(evaluation_items) - len(unsignalled_evaluations),
            "evaluationUnmapped": len(unsignalled_evaluations),
            "hasNarrativeCapacity": any(row["canHostNarrative"] for row in volume_rows),
        },
        "volumes": volume_rows,
        "requiredGaps": gaps,
        "initiatives": initiatives,
        "unboundRequirementSamples": unbound_samples,
        "warnings": warnings,
        "notes": notes,
    }
