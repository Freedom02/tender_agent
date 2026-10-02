# -*- coding: utf-8 -*-
"""提示词包：对外导出与拆分前 `prompts.py` 完全一致的符号面。

由 Wave 0 · 0.2 机械拆分而来（示例 schema 已随阶段归位）；旧代码里的
`from .prompts import xxx`、`from app.pi_agent.prompts import xxx` 全部保持可用。
各阶段的 *_SCHEMA 与提示词函数同属一个阶段文件，便于并行修改互不冲突；
改动提示词前请先看 `backend/tests/baselines/generate_prompt_baseline.py`。
"""
from __future__ import annotations

from ._shared import *  # noqa: F401,F403
from ._shared import (  # noqa: F401
    _DEPTH_GUIDANCE,
    _OUTLINE_SOURCE_TERMS,
    _WRITING_PROFILE_SPECS,
    _agent_context,
    _compact_bid_document_for_review,
    _compact_company,
    _compact_fine_review,
    _compact_material_record,
    _compact_parse_bundle_for_review,
    _compact_project,
    _dump,
    _outline_instruction_context,
    _planning_parse_bundle_view,
    _profile_block,
    _section_guidance_block,
    _section_knowledge_view,
    _section_parse_bundle_view,
    _section_prompt_view,
    _writing_parse_bundle_view,
    _writing_profile_spec,
)
from .parse import (  # noqa: F401
    DOCUMENT_STRUCTURE_SCHEMA,
    EVALUATION_SCHEMA,
    PARSE_REVIEW_SCHEMA,
    PARSE_SCHEMA,
    PROJECT_META_SCHEMA,
    REQUIREMENT_SCHEMA,
    TEMPLATE_SCHEMA,
    document_structure_prompt,
    evaluation_extract_prompt,
    parse_prompt,
    parse_review_prompt,
    project_meta_prompt,
    requirement_extract_prompt,
    template_detect_prompt,
)
from .plan import (  # noqa: F401
    OUTLINE_PLAN_SCHEMA,
    REQUIREMENT_MAP_SCHEMA,
    outline_plan_prompt,
    plan_prompt,
    requirement_map_prompt,
    volume_plan_prompt,
)
from .draft import (  # noqa: F401
    MATERIAL_MATCH_SCHEMA,
    SECTION_DRAFT_SCHEMA,
    SECTION_PLAN_SCHEMA,
    SECTION_REWRITE_SCHEMA,
    material_match_prompt,
    section_draft_prompt,
    section_plan_prompt,
    section_prompt,
    section_rewrite_prompt,
)
from .fill import (  # noqa: F401
    REQUIREMENT_RESPONSE_TABLE_SCHEMA,
    TABLE_FORM_FILL_SCHEMA,
    requirement_response_table_prompt,
    table_form_fill_prompt,
)
from .review import (  # noqa: F401
    COMPLIANCE_REVIEW_SCHEMA,
    compliance_review_prompt,
)
