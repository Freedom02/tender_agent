from __future__ import annotations

import re
from typing import Any

"""企业事实台账视图（匹配层地基）。

把 M3 读出的 8 类 facts（snake_case 键 + CamelCase 行字段）投影成匹配侧统一消费结构。
与 document_recognition/storage.py 的两个投影不同，本模块保留全量 CamelCase 字段，
供确定性匹配器直接读取 level / contractAmount / completionDate / currentStatus /
professionalCertificates 等原字段，不改名、不折叠进 description。

字段改名统一放在 deterministic.py 各函数内部做；本模块只负责「透传 + 合并且打来源标记」，
保证台账可溯源、evidencePath 可直取。
"""


def build_company_ledger(facts: dict[str, Any]) -> dict[str, Any]:
    """M3 facts → 全量字段台账视图。

    参数 facts = read_facts_from_m3() 返回值：
        {"organization": {...} | None,
         "qualification_certificate": [...], "performance_case": [...],
         "staff_member": [...], "financial_evidence": [...],
         "technical_product": [...], "technical_parameter": [...], "credential": [...]}

    返回：
        {
            "certificates": [...],   # QualificationCertificate + Credential 合并，带 _source 标记
            "personnel": [...],      # StaffMember
            "projects": [...],       # PerformanceCase
            "finance": [...],        # FinancialEvidence
            "products": [...],       # TechnicalProduct
            "parameters": [...],     # TechnicalParameter
            "organization": {...},   # Organization（无则 {}）
        }
    """
    certificates: list[dict[str, Any]] = []
    for row in facts.get("qualification_certificate", []) or []:
        if isinstance(row, dict):
            certificates.append({"_source": "qualification_certificate", **row})
    for row in facts.get("credential", []) or []:
        if isinstance(row, dict):
            certificates.append({"_source": "credential", **row})

    organization = facts.get("organization") or {}
    if not isinstance(organization, dict):
        organization = {}

    capabilities = [
        {"id": f"capability-{index}", "name": name, "_source": "organization"}
        for index, name in enumerate(_split_caps(organization.get("capabilities")), start=1)
    ]

    return {
        "certificates": certificates,
        "personnel": _as_list(facts.get("staff_member")),
        "projects": _as_list(facts.get("performance_case")),
        "finance": _as_list(facts.get("financial_evidence")),
        "products": _as_list(facts.get("technical_product")),
        "parameters": _as_list(facts.get("technical_parameter")),
        "capabilities": capabilities,
        "organization": organization,
    }


def _split_caps(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [s.strip() for s in re.split(r"[、,，;；\n]+", str(value)) if s.strip()]


def _as_list(value: Any) -> list[dict[str, Any]]:
    """把事实列表规整为只含 dict 的列表，容忍 None / 非 dict 元素。"""
    return [item for item in (value or []) if isinstance(item, dict)]
