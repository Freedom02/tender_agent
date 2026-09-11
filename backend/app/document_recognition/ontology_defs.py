# -*- coding: utf-8 -*-
"""企业材料本体（Enterprise Material Ontology）唯一来源。

8 类字段 schema + 8 关系连接契约 + 中文元数据 + 运行时辅助字段声明。
这是纯静态 schema，不依赖任何数据解析结果。本体变更只改这一个文件。

V0.4 重构（证据文件夹化）：
- 删除「企业材料 EnterpriseMaterial」——证据不再逐文件注册，改为每个事实类直接挂
  evidencePath（证据文件夹相对路径）+ evidenceUpdatedAt（证据最后更新时间）。
  一个事实一个文件夹，文件夹内 N 个文件按需在应用层枚举。
- 相应删除 8 条溯源/管理关系：owns（组织→材料）+ 7 条 documented_by_*（事实→材料）。

V0.3 重构：
- 删除「企业能力档案 EnterpriseProfile」「企业能力 EnterpriseCapability」——
  能力/范围属性内联为 Organization 属性（LLM 检索时现算总结，不建独立档案/能力类）。
- 删除「材料分类 MaterialCategory」「证据单元 EvidenceUnit」——溯源只留 materialId 回指材料。
- 合并「产品认证 ProductCertification」+「知识产权 IntellectualProperty」→「证照凭证 Credential」。
- 拆出「技术参数 TechnicalParameter」：原 TechnicalProduct.technicalSpecification 独立成类。
- Organization 新增规模属性（employeeCount/annualRevenueWan/totalAssetsWan/enterpriseSize）
  与能力范围属性（capabilities/serviceScope/productScope/regionScope/industryScope）。

用法：
  from app.document_recognition.ontology_defs import CLASS_SCHEMA, REL_MAPPING, FACT_KEY_BY_CLASS, RUNTIME_FIELDS
"""
import re

from .storage import RELATIONSHIPS

NAMESPACE = "tenderhub"


def camel_to_snake(name: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()
    return s.replace("-", "_").replace(" ", "_")


# -- 8 类中文元数据：类名 → (中文名, 描述)
CLASS_CN = {
    "Organization": ("企业", "投标企业主体"),
    "QualificationCertificate": ("资质认证", "企业持有的资质认证证书"),
    "PerformanceCase": ("类似业绩", "企业完成的类似项目业绩"),
    "StaffMember": ("项目人员", "企业聘用的项目人员"),
    "FinancialEvidence": ("财务证明", "企业的财务证明"),
    "TechnicalProduct": ("技术产品", "企业的技术产品"),
    "TechnicalParameter": ("技术参数", "技术产品的参数指标，可挂多份证明材料"),
    "Credential": ("证照凭证", "企业/产品持有的认证、检测报告与知识产权（专利/软著/商标等）"),
}

# -- 属性中文名映射（覆盖主要属性；查不到回退为 snake_case 英文）
ATTR_CN: dict[str, str] = {
    # id 类
    "organizationId": "企业ID", "qualificationId": "资质ID", "performanceId": "业绩ID",
    "staffId": "人员ID", "financialEvidenceId": "财务证明ID", "productId": "产品ID",
    "credentialId": "凭证ID", "parameterId": "参数ID",
    # 名称
    "organizationName": "企业名称", "qualificationName": "资质名称", "productName": "产品名称",
    "credentialName": "凭证名称", "caseName": "案例名称", "customerName": "客户名称",
    "aliases": "别名",
    # 编号/代码
    "certificateNo": "证书编号", "registrationNo": "登记号", "standardNo": "标准号",
    "unifiedSocialCreditCode": "统一社会信用代码",
    # 日期
    "issueDate": "发证日期", "validFrom": "有效期起", "validUntil": "有效期至",
    "signedDate": "签署日期", "completionDate": "完成日期",
    "employmentStartDate": "入职日期", "periodStart": "期间起", "periodEnd": "期间止",
    "uploadedAt": "上传时间",
    # 状态
    "status": "状态", "verificationStatus": "核验状态", "currentStatus": "当前状态",
    "acceptanceStatus": "验收状态",
    # 类型
    "qualificationType": "资质类型", "productType": "产品类型", "staffType": "人员类型",
    "organizationType": "企业类型", "evidenceType": "证明类型", "credentialType": "凭证类型",
    "enterpriseSize": "企业规模",
    # 企业规模/能力（重构）
    "employeeCount": "从业人员数", "annualRevenueWan": "营业收入(万元)",
    "totalAssetsWan": "资产总额(万元)", "capabilities": "能力清单",
    "managementPolicies": "管理制度", "serviceScope": "服务范围",
    "productScope": "产品范围", "regionScope": "区域范围", "industryScope": "行业范围",
    "scaleValue": "规模数值", "scaleUnit": "规模单位",
    # 证据（文件夹相对路径）
    "evidencePath": "证据路径", "evidenceUpdatedAt": "证据更新时间",
    # 其他
    "issuer": "发证机构", "scope": "范围", "level": "等级", "description": "描述",
    "legalRepresentative": "法定代表人", "registeredAddress": "注册地址",
    "contactInfo": "联系方式", "title": "职称", "specialty": "专业方向",
    "resumeSummary": "履历摘要", "yearsOfExperience": "从业年限",
    "professionalCertificates": "专业证书", "contractAmount": "合同金额", "currency": "币种",
    "role": "角色", "contactName": "联系人", "industry": "行业",
    "region": "区域", "brand": "品牌", "model": "型号",
    "applicableScenario": "适用场景", "owner": "权利人", "category": "类别", "officialSite": "官网",
}


def attr_cn(name: str) -> str:
    return ATTR_CN.get(name, camel_to_snake(name))


# -- 8 类字段 schema：(字段驼峰, M3类型, 是否主键)
CLASS_SCHEMA: dict[str, list[tuple[str, str, int]]] = {
    "Organization": [
        ("organizationId", "varchar", 1), ("organizationName", "varchar", 0),
        ("organizationType", "varchar", 0), ("unifiedSocialCreditCode", "varchar", 0),
        ("legalRepresentative", "varchar", 0), ("registeredAddress", "text", 0),
        ("contactInfo", "varchar", 0), ("officialSite", "varchar", 0), ("aliases", "text", 0),
        # 企业规模
        ("employeeCount", "int", 0), ("annualRevenueWan", "double", 0),
        ("totalAssetsWan", "double", 0), ("enterpriseSize", "varchar", 0),
        # 能力/范围（原 EnterpriseProfile / EnterpriseCapability 归并，LLM 检索时现算总结）
        ("description", "text", 0), ("managementPolicies", "text", 0),
        ("capabilities", "text", 0), ("serviceScope", "text", 0), ("productScope", "text", 0),
        ("regionScope", "text", 0), ("industryScope", "text", 0),
        ("scaleValue", "double", 0), ("scaleUnit", "varchar", 0),
        ("status", "varchar", 0),
    ],
    "QualificationCertificate": [
        ("qualificationId", "varchar", 1), ("qualificationType", "varchar", 0),
        ("qualificationName", "varchar", 0), ("certificateNo", "varchar", 0),
        ("level", "varchar", 0), ("issuer", "varchar", 0), ("scope", "text", 0),
        ("issueDate", "date", 0), ("validFrom", "date", 0), ("validUntil", "date", 0),
        ("status", "varchar", 0), ("verificationStatus", "varchar", 0),
        ("evidencePath", "varchar", 0), ("evidenceUpdatedAt", "timestamp", 0), ("organizationId", "varchar", 0),
    ],
    "PerformanceCase": [
        ("performanceId", "varchar", 1), ("caseName", "varchar", 0), ("customerName", "varchar", 0),
        ("industry", "varchar", 0), ("region", "varchar", 0), ("serviceScope", "text", 0),
        ("contractAmount", "double", 0), ("currency", "varchar", 0), ("signedDate", "date", 0),
        ("completionDate", "date", 0), ("acceptanceStatus", "varchar", 0), ("role", "varchar", 0),
        ("contactName", "varchar", 0), ("evidencePath", "varchar", 0), ("evidenceUpdatedAt", "timestamp", 0), ("organizationId", "varchar", 0),
    ],
    "StaffMember": [
        ("staffId", "varchar", 1), ("name", "varchar", 0), ("role", "varchar", 0),
        ("staffType", "varchar", 0), ("title", "varchar", 0), ("professionalCertificates", "text", 0),
        ("yearsOfExperience", "double", 0), ("employmentStartDate", "date", 0),
        ("specialty", "varchar", 0), ("currentStatus", "varchar", 0), ("resumeSummary", "text", 0),
        ("evidencePath", "varchar", 0), ("evidenceUpdatedAt", "timestamp", 0), ("organizationId", "varchar", 0),
    ],
    "FinancialEvidence": [
        ("financialEvidenceId", "varchar", 1), ("evidenceType", "varchar", 0),
        ("periodStart", "date", 0), ("periodEnd", "date", 0), ("issuedBy", "varchar", 0),
        ("issueDate", "date", 0), ("validUntil", "date", 0), ("revenue", "double", 0),
        ("assetAmount", "double", 0), ("creditLevel", "varchar", 0), ("status", "varchar", 0),
        ("evidencePath", "varchar", 0), ("evidenceUpdatedAt", "timestamp", 0), ("organizationId", "varchar", 0),
    ],
    "TechnicalProduct": [
        ("productId", "varchar", 1), ("productName", "varchar", 0), ("aliases", "text", 0),
        ("productType", "varchar", 0), ("category", "varchar", 0), ("brand", "varchar", 0),
        ("model", "varchar", 0), ("description", "text", 0),
        ("applicableScenario", "text", 0),
        ("status", "varchar", 0), ("evidencePath", "varchar", 0), ("evidenceUpdatedAt", "timestamp", 0), ("organizationId", "varchar", 0),
    ],
    "TechnicalParameter": [
        ("parameterId", "varchar", 1), ("description", "text", 0),
        ("productId", "varchar", 0), ("evidencePath", "varchar", 0), ("evidenceUpdatedAt", "timestamp", 0), ("organizationId", "varchar", 0),
    ],
    "Credential": [
        ("credentialId", "varchar", 1), ("credentialType", "varchar", 0),
        ("credentialName", "text", 0), ("registrationNo", "varchar", 0),
        ("issuer", "varchar", 0), ("owner", "varchar", 0), ("standardNo", "varchar", 0),
        ("issueDate", "date", 0), ("validUntil", "date", 0), ("status", "varchar", 0),
        ("productId", "varchar", 0), ("evidencePath", "varchar", 0), ("evidenceUpdatedAt", "timestamp", 0), ("organizationId", "varchar", 0),
    ],
}

# -- 运行时辅助字段：非本体属性，只用于内部计算/报告，不进 M3 本体 schema
RUNTIME_FIELDS: dict[str, set[str]] = {
    "staff_member": {"headcount"},
}

# -- 8 条关系连接契约：(relationName, fromClass, toClass, sourcePK, sourceJoinField, targetPK, targetJoinField)
#    管理关系「组织→事实」：FK 在 toClass（targetJoinField=事实外键回指）。
REL_MAPPING: list[tuple[str, str, str, str, str, str, str]] = [
    # 组织 → 事实（管理关系）
    ("holds", "Organization", "QualificationCertificate", "organizationId", "organizationId", "qualificationId", "organizationId"),
    ("completes", "Organization", "PerformanceCase", "organizationId", "organizationId", "performanceId", "organizationId"),
    ("employs", "Organization", "StaffMember", "organizationId", "organizationId", "staffId", "organizationId"),
    ("provides_financial_evidence", "Organization", "FinancialEvidence", "organizationId", "organizationId", "financialEvidenceId", "organizationId"),
    ("owns_product", "Organization", "TechnicalProduct", "organizationId", "organizationId", "productId", "organizationId"),
    ("has_parameter", "TechnicalProduct", "TechnicalParameter", "productId", "productId", "parameterId", "productId"),
    ("owns_credential", "Organization", "Credential", "organizationId", "organizationId", "credentialId", "organizationId"),
    # 产品 → 证照（产品级认证）
    ("certified_by", "TechnicalProduct", "Credential", "productId", "productId", "credentialId", "productId"),
]

# -- 类名 → pipeline facts 键
FACT_KEY_BY_CLASS: dict[str, str] = {
    "Organization": "organization",
    "QualificationCertificate": "qualification_certificate",
    "PerformanceCase": "performance_case",
    "StaffMember": "staff_member",
    "FinancialEvidence": "financial_evidence",
    "TechnicalProduct": "technical_product",
    "TechnicalParameter": "technical_parameter",
    "Credential": "credential",
}
