"""企业材料本体契约（M3 单一事实源）。

V0.4 重构（证据文件夹化）：
- 数据来源为「工作人员填 Excel 模板 → 导入」，本地只保留证明材料文件。
- facts：8 个本体类的结构化事实（含 organizationId / evidencePath 外键与证据路径），
  实际存于 M3 图库；本模块只提供契约与派生逻辑，不再读写 company-materials.json。
- relationships：8 条关系定义（由 ontology_defs.REL_MAPPING 派生语义，此处为运行时契约）。
- build_edges：从 facts 外键确定性生成关系实例（边表）。
- build_profile：企业画像（前端兼容派生）。
"""
from __future__ import annotations

import re
from typing import Any

DEFAULT_ORGANIZATION_NAME = "北京优锘科技股份有限公司"

# 8 条关系定义（7 管理关系 + 1 产品认证）
RELATIONSHIPS: list[dict[str, str]] = [
    {"relationName": "holds", "fromClass": "Organization", "toClass": "QualificationCertificate", "desc": "企业持有资质认证"},
    {"relationName": "completes", "fromClass": "Organization", "toClass": "PerformanceCase", "desc": "企业完成类似业绩"},
    {"relationName": "employs", "fromClass": "Organization", "toClass": "StaffMember", "desc": "企业聘用人员"},
    {"relationName": "provides_financial_evidence", "fromClass": "Organization", "toClass": "FinancialEvidence", "desc": "企业提供财务证明"},
    {"relationName": "owns_product", "fromClass": "Organization", "toClass": "TechnicalProduct", "desc": "企业拥有技术产品"},
    {"relationName": "has_parameter", "fromClass": "TechnicalProduct", "toClass": "TechnicalParameter", "desc": "产品包含技术参数"},
    {"relationName": "owns_credential", "fromClass": "Organization", "toClass": "Credential", "desc": "企业持有证照凭证"},
    {"relationName": "certified_by", "fromClass": "TechnicalProduct", "toClass": "Credential", "desc": "产品获得产品级认证"},
]

# 事实集合里的 7 个列表键（organization 是单对象，不在此列）
FACT_LIST_KEYS: tuple[str, ...] = (
    "qualification_certificate",
    "performance_case",
    "staff_member",
    "financial_evidence",
    "technical_product",
    "technical_parameter",
    "credential",
)

# 每个事实键的主键字段（用于生成关系实例 + 编辑接口定位）
ID_FIELD_BY_KEY: dict[str, str] = {
    "organization": "organizationId",
    "qualification_certificate": "qualificationId",
    "performance_case": "performanceId",
    "staff_member": "staffId",
    "financial_evidence": "financialEvidenceId",
    "technical_product": "productId",
    "technical_parameter": "parameterId",
    "credential": "credentialId",
}


def build_edges(facts: dict[str, Any]) -> list[dict[str, str]]:
    """从 facts 的外键确定性生成「关系实例」（边表），单独存储，不依赖 M3。

    每条边显式列出 from/to 的具体 ID，对应 ontology_defs.REL_MAPPING 的 8 条关系。
    外键为空的（如无 productId）不生成对应边。
    """
    org = facts.get("organization") or {}
    org_id = org.get("organizationId", "")
    edges: list[dict[str, str]] = []

    def add(rel: str, from_cls: str, from_id: Any, to_cls: str, to_id: Any) -> None:
        if from_id and to_id:
            edges.append({
                "relationName": rel,
                "fromClass": from_cls, "fromId": str(from_id),
                "toClass": to_cls, "toId": str(to_id),
            })

    quals = facts.get("qualification_certificate", [])
    perfs = facts.get("performance_case", [])
    staff = facts.get("staff_member", [])
    fins = facts.get("financial_evidence", [])
    prods = facts.get("technical_product", [])
    params = facts.get("technical_parameter", [])
    creds = facts.get("credential", [])

    for q in quals:
        add("holds", "Organization", org_id, "QualificationCertificate", q.get("qualificationId"))
    for p in perfs:
        add("completes", "Organization", org_id, "PerformanceCase", p.get("performanceId"))
    for s in staff:
        add("employs", "Organization", org_id, "StaffMember", s.get("staffId"))
    for f in fins:
        add("provides_financial_evidence", "Organization", org_id, "FinancialEvidence", f.get("financialEvidenceId"))
    for p in prods:
        add("owns_product", "Organization", org_id, "TechnicalProduct", p.get("productId"))
    for tp in params:
        add("has_parameter", "TechnicalProduct", tp.get("productId"), "TechnicalParameter", tp.get("parameterId"))
    for c in creds:
        add("owns_credential", "Organization", org_id, "Credential", c.get("credentialId"))
        add("certified_by", "TechnicalProduct", c.get("productId"), "Credential", c.get("credentialId"))

    return edges


def build_profile(facts: dict[str, Any]) -> dict[str, Any]:
    """聚合企业画像（前端兼容）。facts 为 8 类 CamelCase 事实。"""
    org = facts.get("organization") or {}

    certificates = []
    qualifications = []
    for q in facts.get("qualification_certificate", []):
        name = q.get("qualificationName") or ""
        if not name:
            continue
        if name not in qualifications:
            qualifications.append(name)
        if q.get("certificateNo") or q.get("validUntil") or q.get("issuer"):
            cert = {"name": name, "number": q.get("certificateNo", ""),
                    "validUntil": q.get("validUntil", ""), "issuer": q.get("issuer", "")}
            if cert not in certificates:
                certificates.append(cert)

    performances = [
        {"name": p.get("caseName", ""), "industry": p.get("industry", ""),
         "type": "", "region": p.get("region", ""),
         "amount": _num_str(p.get("contractAmount")), "date": p.get("signedDate", "")}
        for p in facts.get("performance_case", [])
    ]

    personnel = [
        {"role": s.get("role") or s.get("name", ""),
         "count": s.get("headcount"), "experienceYears": s.get("yearsOfExperience")}
        for s in facts.get("staff_member", [])
    ]

    financial_evidences = [
        {"evidenceType": f.get("evidenceType", ""), "issuedBy": f.get("issuedBy", ""),
         "issueDate": f.get("issueDate", ""), "validUntil": f.get("validUntil", ""),
         "revenue": _num_str(f.get("revenue")), "assetAmount": _num_str(f.get("assetAmount")),
         "creditLevel": f.get("creditLevel", "")}
        for f in facts.get("financial_evidence", [])
    ]

    products = [
        {"name": p.get("productName", ""), "type": p.get("productType", ""),
         "category": p.get("category", ""), "description": p.get("description", "")}
        for p in facts.get("technical_product", [])
    ]

    credentials = [
        {"type": c.get("credentialType", ""), "name": c.get("credentialName", ""),
         "registrationNo": c.get("registrationNo", ""), "issuer": c.get("issuer", ""),
         "owner": c.get("owner", ""), "productId": c.get("productId", "")}
        for c in facts.get("credential", [])
    ]

    parameters = [
        {"parameterId": p.get("parameterId", ""), "description": p.get("description", ""),
         "productId": p.get("productId", ""), "evidencePath": p.get("evidencePath", "")}
        for p in facts.get("technical_parameter", [])
    ]

    # 证据路径汇总
    evidence_paths: list[dict[str, str]] = []
    for key in FACT_LIST_KEYS:
        for row in facts.get(key, []):
            ep = row.get("evidencePath", "")
            if ep:
                evidence_paths.append({"factKey": key, "evidencePath": ep})

    return {
        "company": org.get("organizationName", ""),
        "enterpriseSize": org.get("enterpriseSize", ""),
        "employeeCount": org.get("employeeCount", 0),
        "annualRevenueWan": org.get("annualRevenueWan", 0),
        "totalAssetsWan": org.get("totalAssetsWan", 0),
        "description": org.get("description", ""),
        "capabilities": _split_text(org.get("capabilities", "")),
        "service_regions": _split_text(org.get("regionScope", "")),
        "service_types": _split_text(org.get("serviceScope", "")),
        "service_industries": _split_text(org.get("industryScope", "")),
        "qualifications": _dedupe(qualifications),
        "certificates": certificates,
        "performances": performances,
        "personnel": personnel,
        "financial_evidences": financial_evidences,
        "products": products,
        "parameters": parameters,
        "credentials": credentials,
        "evidence_paths": evidence_paths,
    }


def build_company_knowledge(facts: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build the discovery vocabulary from the M3 enterprise facts.

    The qualification and evidence facts remain available to later matching
    stages. This projection is specifically for opportunity discovery and
    keeps business scope, scenarios, products and capabilities separate.
    """
    org = facts.get("organization") or {}

    def split_values(*values: Any) -> list[str]:
        result: list[str] = []
        for value in values:
            result.extend(_split_text(value))
        return _dedupe(result)

    def named_rows(prefix: str, values: list[str], description: str = "") -> list[dict[str, Any]]:
        return [
            {"id": f"{prefix}-{index}", "name": value, "description": description}
            for index, value in enumerate(values, start=1)
        ]

    direction_values = split_values(
        org.get("serviceScope"),
        org.get("productScope"),
    )
    scenario_values = split_values(
        org.get("industryScope"),
        *(row.get("industry", "") for row in facts.get("performance_case", []) if isinstance(row, dict)),
        *(row.get("applicableScenario", "") for row in facts.get("technical_product", []) if isinstance(row, dict)),
    )
    capability_values = split_values(
        org.get("capabilities"),
        *(row.get("serviceScope", "") for row in facts.get("performance_case", []) if isinstance(row, dict)),
        *(row.get("description", "") for row in facts.get("technical_parameter", []) if isinstance(row, dict)),
    )

    products: list[dict[str, Any]] = []
    for row in facts.get("technical_product", []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("productName") or "").strip()
        if not name:
            continue
        products.append({
            "id": str(row.get("productId") or f"product-{len(products) + 1}"),
            "name": name,
            "aliases": row.get("aliases", ""),
            "description": row.get("description", ""),
            "category": row.get("category", ""),
            "applicableScenario": row.get("applicableScenario", ""),
        })

    return {
        "businessDirections": named_rows("direction", direction_values),
        "industryScenarios": named_rows("scenario", scenario_values),
        "products": products,
        "capabilities": named_rows("capability", capability_values),
    }


# ============================================================
# 工具函数
# ============================================================

def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("，", "")
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _num_str(value: Any) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = str(item).strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _split_text(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return _dedupe([str(v).strip() for v in value])
    return _dedupe(re.split(r"[、,，;；\n]+", str(value)))
