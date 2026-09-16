# -*- coding: utf-8 -*-
"""商务标书子模块：固定文书 + 事实表格化（纯函数、确定性、无 LLM）。

职责（与 planning.py 的章节规划对齐）：
  - c-01..c-07：固定文书（投标函 / 投标承诺书 / 法定代表人身份证明 / 授权委托书 /
    投标保证金说明 / 资格审查声明 / 企业简介），用项目字段 + 企业信息做占位符替换。
  - c-08..c-10：事实表格（企业资信一览表 / 业绩一览表 / 人员配置表），
    由 knowledge 记录（qualifications / projectReferences / personnel）表格化。

输入（全部复用现有层，禁止重写）：
  - project      ← store.catalog 的 CatalogItem：`parsed`（extract_project_fields 字段）
                  与顶层 `title` 等。
  - company      ← document_recognition/storage.build_profile 的企业画像
                  （`company` 键为企业名）。
  - knowledge    ← storage.build_eligibility_knowledge 的记录结构
                  （每行含 id/name/description/evidencePath/status/validUntil）。
  - organization ← 原始组织 fact（organizationName/legalRepresentative/
                  unifiedSocialCreditCode/registeredAddress/contactInfo）。
  - ai           ← LLM 配置（预留；本模块为确定性纯函数，不调用大模型）。

硬性约束：
  - 正文只引用 project/company/knowledge/organization；取不到的字段统一输出
    `【待填写：字段名】` 占位，绝不编造业绩数字、证书、人员。
  - 每个表格行带 `sourceRefs`（schemas.build_source_ref），防幻觉溯源。
"""
from __future__ import annotations

from typing import Any

from .planning import COMMERCIAL_TABLE_SECTIONS, COMMERCIAL_TEMPLATE_SECTIONS
from .schemas import build_source_ref

# 事实表格规格：(knowledge 键, factKey, 表头)
_TABLE_SPECS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "c-08": (
        "qualifications",
        "qualification_certificate",
        ("序号", "资质/证书名称", "说明", "状态", "有效期至", "证明材料"),
    ),
    "c-09": (
        "projectReferences",
        "performance_case",
        ("序号", "项目/案例名称", "项目说明", "状态", "有效期至", "证明材料"),
    ),
    "c-10": (
        "personnel",
        "staff_member",
        ("序号", "姓名", "角色/资质说明", "状态", "有效期至", "证明材料"),
    ),
}


# ---------------------------------------------------------------------------
# 通用取值工具
# ---------------------------------------------------------------------------
def _as_text(value: Any) -> str:
    """把 list/tuple/str 统一成文本；list/tuple 用中文顿号拼接，None 返回空串。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _num_text(value: Any) -> str:
    """数值字段转文本：0 / 空 / None 视为缺失返回空串，避免误填「0」。"""
    if value is None or value == "" or value is False:
        return ""
    if isinstance(value, (int, float)) and value == 0:
        return ""
    return str(value).strip()


def _pick(*values: Any) -> str:
    """返回第一个非空文本值，否则返回空串。"""
    for value in values:
        text = _as_text(value)
        if text:
            return text
    return ""


def _field(label: str, *values: Any) -> str:
    """取第一个非空值，取不到则返回「【待填写：label】」占位（绝不编造）。"""
    text = _pick(*values)
    return text if text else f"【待填写：{label}】"


def _project_value(project: dict, key: str, *aliases: str) -> str:
    """从 project["parsed"] 或 project 顶层按 key/别名取值，返回空串表示缺失。"""
    if not isinstance(project, dict):
        return ""
    parsed = project.get("parsed")
    parsed = parsed if isinstance(parsed, dict) else {}
    for name in (key, *aliases):
        for source in (parsed, project):
            text = _as_text(source.get(name))
            if text:
                return text
    return ""


def _company_info(organization: dict | None, company: dict | None) -> dict[str, str]:
    """聚合企业信息：organization（原始组织 fact）优先，company 画像兜底。"""
    org = organization if isinstance(organization, dict) else {}
    profile = company if isinstance(company, dict) else {}
    return {
        "name": _pick(
            org.get("organizationName"),
            profile.get("company"),
            profile.get("organizationName"),
        ),
        "legalRepresentative": _pick(
            org.get("legalRepresentative"),
            profile.get("legalRepresentative"),
        ),
        "creditCode": _pick(
            org.get("unifiedSocialCreditCode"),
            profile.get("unifiedSocialCreditCode"),
        ),
        "registeredAddress": _pick(
            org.get("registeredAddress"),
            profile.get("registeredAddress"),
        ),
        "contactInfo": _pick(
            org.get("contactInfo"),
            profile.get("contactInfo"),
        ),
        "enterpriseSize": _pick(
            org.get("enterpriseSize"),
            profile.get("enterpriseSize"),
        ),
        "employeeCount": _num_text(
            _pick(org.get("employeeCount"), profile.get("employeeCount"))
        ),
        "description": _pick(
            org.get("description"),
            profile.get("description"),
        ),
        "capabilities": _pick(
            org.get("capabilities"),
            profile.get("capabilities"),
        ),
        "serviceRegions": _pick(
            org.get("regionScope"),
            profile.get("service_regions"),
        ),
        "serviceTypes": _pick(
            org.get("serviceScope"),
            profile.get("service_types"),
        ),
        "serviceIndustries": _pick(
            org.get("industryScope"),
            profile.get("service_industries"),
        ),
    }


def _markdown_table(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    """把表头与行列表渲染成 Markdown 表格。"""

    def cell(value: Any) -> str:
        return _as_text(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")

    lines = [
        "| " + " | ".join(cell(h) for h in headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell(c) for c in row) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 固定文书（c-01..c-07）
# ---------------------------------------------------------------------------
def _bid_letter(project: dict, info: dict[str, str]) -> dict:
    """c-01 投标函。"""
    tenderer = _field("招标人（采购人）名称", _project_value(project, "tendererName"))
    project_name = _field("项目名称", _project_value(project, "projectName", "title"))
    project_code = _field("项目编号", _project_value(project, "projectCode"))
    bidder = _field("投标人名称", info["name"])
    content = f"""# 投标函

致：{tenderer}

我方（投标人全称：{bidder}）经认真研究贵方发布的《{project_name}》（项目编号：{project_code}）招标文件的全部内容，决定参加该项目的投标，并郑重承诺如下：

一、我方完全响应并接受招标文件的各项规定和要求，愿意按照招标文件规定的服务范围、技术标准、商务条件和合同条款完成本项目。

二、我方投标报价详见投标文件"开标一览表"及"报价明细表"，该报价为完成本项目全部工作内容的最终含税报价，在投标有效期内固定不变。

三、若我方中标，我方保证在招标文件规定的时间与地点与贵方签订合同，并严格履行合同义务，保证服务质量和交付进度，按期保质完成本项目。

四、我方同意本投标函在招标文件规定的投标有效期内保持有效，对我方具有法律约束力。若我方在投标有效期内撤回投标、拒绝签订合同或提供虚假资料，贵方有权取消我方中标资格并没收投标保证金。

五、我方保证本投标文件所载全部资料真实、准确、合法、有效，如有任何虚假、伪造或隐瞒，我方愿承担由此产生的一切法律责任。

投标人（单位公章）：{bidder}
法定代表人或其授权代表（签字或盖章）：【待填写：签字人】
日期：____年____月____日
"""
    return {"content": content, "sourceRefs": []}


def _commitment_letter(project: dict, info: dict[str, str]) -> dict:
    """c-02 投标承诺书。"""
    tenderer = _field("招标人（采购人）名称", _project_value(project, "tendererName"))
    bidder = _field("投标人名称", info["name"])
    project_name = _field("项目名称", _project_value(project, "projectName", "title"))
    project_code = _field("项目编号", _project_value(project, "projectCode"))
    content = f"""# 投标承诺书

致：{tenderer}

我方（{bidder}）郑重承诺：

一、我方已仔细阅读并充分理解《{project_name}》（项目编号：{project_code}）招标文件及其澄清、修改、补充文件的全部内容，对招标文件的所有条款均无异议。

二、我方所提交的投标文件均依据招标文件要求编制，投标文件内容真实、完整、有效，所附资质证书、业绩证明、财务报表、人员资料等证明材料均为真实原件或经核实的复印件，无伪造、变造、涂改或隐瞒。

三、我方没有处于被责令停业、投标资格被取消、财产被接管、冻结或破产状态；在最近三年内没有骗取中标、严重违约及重大工程质量或安全事故；未被列入"信用中国"等平台失信被执行人、重大税收违法案件当事人、政府采购严重违法失信行为记录名单。

四、我方完全接受招标文件的评标办法和评审标准，服从评审结果；若我方中标，将按招标文件和合同约定组建项目团队、配置资源，保证按期、保质、保量履行合同。

五、我方保证投标报价不低于成本、不串通投标、不弄虚作假，不以任何方式向评标委员会成员或其他相关人员行贿。

六、如我方违反上述承诺，愿接受招标人及有关部门依据法律法规和招标文件作出的处理，并承担相应的法律责任和经济损失。

投标人（盖章）：{bidder}
法定代表人或其授权代表（签字）：【待填写：签字人】
日期：____年____月____日
"""
    return {"content": content, "sourceRefs": []}


def _legal_rep_id(project: dict, info: dict[str, str]) -> dict:
    """c-03 法定代表人身份证明。"""
    bidder = _field("投标人名称", info["name"])
    credit_code = _field("统一社会信用代码", info["creditCode"])
    address = _field("注册地址", info["registeredAddress"])
    legal_rep = _field("法定代表人", info["legalRepresentative"])
    contact = _field("法定代表人联系电话", info["contactInfo"])
    content = f"""# 法定代表人身份证明

投标人名称：{bidder}
统一社会信用代码：{credit_code}
注册地址：{address}

成立时间：【待填写：成立时间】
经营期限：【待填写：经营期限】
经营范围：【待填写：经营范围】

姓名：{legal_rep}　　性别：【待填写：性别】　　年龄：【待填写：年龄】
职务：【待填写：法定代表人职务】　　联系电话：{contact}

兹证明，{legal_rep} 同志系 {bidder} 的法定代表人。

特此证明。

投标人（盖章）：{bidder}
日期：____年____月____日

附：法定代表人身份证复印件（正反面）
"""
    return {"content": content, "sourceRefs": []}


def _power_of_attorney(project: dict, info: dict[str, str]) -> dict:
    """c-04 授权委托书。"""
    legal_rep = _field("法定代表人", info["legalRepresentative"])
    bidder = _field("投标人名称", info["name"])
    project_name = _field("项目名称", _project_value(project, "projectName", "title"))
    project_code = _field("项目编号", _project_value(project, "projectCode"))
    content = f"""# 授权委托书

本授权委托书声明：我 {legal_rep} 系 {bidder} 的法定代表人，现授权委托【待填写：被授权人姓名】（职务：【待填写：被授权人职务】）为我方参加 {project_name}（项目编号：{project_code}）投标活动的合法授权代表。

被授权人代表我方全权处理本项目投标活动中的一切事宜，包括但不限于：签署投标文件、进行澄清与答辩、递交和撤回投标文件、参加开标会议、谈判与签约等。被授权人在本项目投标活动中的签字及所签署的一切文件，我方均予以承认并承担全部法律责任。

授权期限：自本授权委托书签署之日起至本项目招标活动结束之日止（或【待填写：授权期限】）。

被授权人无转委托权。

特此委托。

授权人（法定代表人）签字：{legal_rep}
被授权人签字：【待填写：被授权人签字】
投标人（盖章）：{bidder}
日期：____年____月____日

附：被授权人身份证复印件（正反面）
"""
    return {"content": content, "sourceRefs": []}


def _bid_bond(project: dict, info: dict[str, str]) -> dict:
    """c-05 投标保证金说明。"""
    tenderer = _field("招标人（采购人）名称", _project_value(project, "tendererName"))
    bidder = _field("投标人名称", info["name"])
    project_name = _field("项目名称", _project_value(project, "projectName", "title"))
    project_code = _field("项目编号", _project_value(project, "projectCode"))
    content = f"""# 投标保证金说明

致：{tenderer}

我方（{bidder}）参加 {project_name}（项目编号：{project_code}）的投标，就投标保证金事宜说明如下：

一、我方已按招标文件要求，于投标截止时间前以【待填写：保证金缴纳方式（电汇/银行保函/转账等）】方式缴纳投标保证金人民币【待填写：投标保证金金额】元（大写：【待填写：投标保证金大写金额】）。

二、我方同意并接受招标文件关于投标保证金退还及没收的规定：在投标有效期内不撤回投标，中标后按要求签订合同并提交履约担保；如我方出现招标文件规定的应没收投标保证金的情形，招标人有权不予退还。

三、保证金退还相关信息：
收款单位名称：{bidder}
开户银行：【待填写：开户银行】
银行账号：【待填写：银行账号】

投标人（盖章）：{bidder}
日期：____年____月____日
"""
    return {"content": content, "sourceRefs": []}


def _qualification_declaration(project: dict, info: dict[str, str]) -> dict:
    """c-06 资格审查声明。"""
    tenderer = _field("招标人（采购人）名称", _project_value(project, "tendererName"))
    bidder = _field("投标人名称", info["name"])
    project_name = _field("项目名称", _project_value(project, "projectName", "title"))
    project_code = _field("项目编号", _project_value(project, "projectCode"))
    credit_code = _field("统一社会信用代码", info["creditCode"])
    address = _field("注册地址", info["registeredAddress"])
    content = f"""# 资格审查声明

致：{tenderer}

我方（{bidder}）就参加 {project_name}（项目编号：{project_code}）投标的资格审查事项，郑重声明如下：

一、我方具有独立承担民事责任的能力，持有有效的营业执照，统一社会信用代码为 {credit_code}，注册地址为 {address}。

二、我方具有良好的商业信誉和健全的财务会计制度，有依法缴纳税收和社会保障资金的良好记录。

三、我方在参加本次采购活动前三年内，在经营活动中没有重大违法记录；未被"信用中国"网站列入失信被执行人、重大税收违法案件当事人名单，未被"中国政府采购网"列入政府采购严重违法失信行为记录名单。

四、我方具备履行合同所必需的设备和专业技术能力，所投产品/服务符合国家及行业相关标准，人员配置满足招标文件要求。

五、我方未被列入禁止参加政府采购活动的供应商名单，与本项目其他投标人不存在直接控股、管理关系，亦不存在为本项目提供整体设计、规范编制或者项目管理、监理、检测等服务的关联情形。

六、我方保证上述声明及随投标文件提交的全部资格审查资料真实、完整、有效，如有虚假，愿承担由此引起的一切法律责任，并接受取消投标资格或中标资格的处理。

投标人（盖章）：{bidder}
法定代表人或其授权代表（签字）：【待填写：签字人】
日期：____年____月____日
"""
    return {"content": content, "sourceRefs": []}


def _company_intro(project: dict, info: dict[str, str]) -> dict:
    """c-07 企业简介。"""
    bidder = _field("投标人名称", info["name"])
    credit_code = _field("统一社会信用代码", info["creditCode"])
    address = _field("注册地址", info["registeredAddress"])
    legal_rep = _field("法定代表人", info["legalRepresentative"])
    contact = _field("联系方式", info["contactInfo"])
    enterprise_size = _field("企业规模", info["enterpriseSize"])
    employee_count = _field("员工人数", info["employeeCount"])
    description = _field("企业简介", info["description"])
    capabilities = _field("主营业务与服务能力", info["capabilities"])
    service_regions = _field("服务区域", info["serviceRegions"])
    service_types = _field("服务类型", info["serviceTypes"])
    service_industries = _field("服务行业", info["serviceIndustries"])
    content = f"""# 企业简介

## 一、企业概况

| 项目 | 内容 |
| --- | --- |
| 企业名称 | {bidder} |
| 统一社会信用代码 | {credit_code} |
| 注册地址 | {address} |
| 法定代表人 | {legal_rep} |
| 联系方式 | {contact} |
| 企业规模 | {enterprise_size} |
| 员工人数 | {employee_count} |

## 二、企业简介

{description}

## 三、主营业务与服务能力

{capabilities}

## 四、服务范围

- 服务区域：{service_regions}
- 服务类型：{service_types}
- 服务行业：{service_industries}

## 五、资质与业绩

我方持有的资质证书、类似项目业绩及人员配置情况，详见投标文件"企业资信一览表""业绩一览表"及"人员配置表"。

投标人（盖章）：{bidder}
日期：____年____月____日
"""
    return {"content": content, "sourceRefs": []}


# ---------------------------------------------------------------------------
# 事实表格（c-08..c-10）
# ---------------------------------------------------------------------------
def _fact_table(
    title: str,
    knowledge: dict,
    key: str,
    fact_key: str,
    headers: tuple[str, ...],
) -> dict:
    """由 knowledge[key] 记录生成 Markdown 表格，每行一条 sourceRef。"""
    records: list[dict] = []
    raw = knowledge.get(key) if isinstance(knowledge, dict) else None
    if isinstance(raw, list):
        records = [item for item in raw if isinstance(item, dict)]

    if not records:
        return {"content": "暂无入库资料，需人工补充。", "sourceRefs": []}

    rows: list[tuple[Any, ...]] = []
    source_refs: list[dict] = []
    for index, record in enumerate(records, start=1):
        name = _pick(record.get("name"))
        description = _pick(record.get("description"))
        status = _pick(record.get("status"))
        valid_until = _pick(record.get("validUntil"))
        evidence_path = _pick(record.get("evidencePath"))
        rows.append((index, name, description, status, valid_until, evidence_path))
        source_refs.append(
            build_source_ref(
                fact_key,
                fact_id=_pick(record.get("id")),
                name=name,
                evidence_path=evidence_path,
            )
        )

    content = f"# {title}\n\n" + _markdown_table(headers, rows)
    return {"content": content, "sourceRefs": source_refs}


# ---------------------------------------------------------------------------
# 对外入口
# ---------------------------------------------------------------------------
def generate_commercial_sections(
    project: dict,
    company: dict,
    knowledge: dict,
    organization: dict | None = None,
    ai: dict | None = None,
) -> dict[str, dict]:
    """生成商务标书 10 个章节（c-01..c-10）。

    返回 {sectionId: section_dict}，section_dict 含 "content"（Markdown 正文）
    与 "sourceRefs"（溯源引用列表）两个键：
      - c-01..c-07 为固定文书，用项目字段 + 企业信息做占位符替换，sourceRefs 为空。
      - c-08..c-10 为事实表格，每条记录一条 sourceRef，factKey 分别对应
        qualification_certificate / performance_case / staff_member。

    参数：
      project      招标项目 CatalogItem（含 `parsed` 与顶层 `title` 等）。
      company      企业画像（storage.build_profile 输出，`company` 键为企业名）。
      knowledge    资格匹配知识（storage.build_eligibility_knowledge 输出）。
      organization 原始组织 fact（可选，企业信息优先来源）。
      ai           LLM 配置（预留；本模块为确定性纯函数，不调用大模型）。
    """
    project = project if isinstance(project, dict) else {}
    company = company if isinstance(company, dict) else {}
    knowledge = knowledge if isinstance(knowledge, dict) else {}
    info = _company_info(organization, company)

    builders: dict[str, Any] = {
        "c-01": lambda: _bid_letter(project, info),
        "c-02": lambda: _commitment_letter(project, info),
        "c-03": lambda: _legal_rep_id(project, info),
        "c-04": lambda: _power_of_attorney(project, info),
        "c-05": lambda: _bid_bond(project, info),
        "c-06": lambda: _qualification_declaration(project, info),
        "c-07": lambda: _company_intro(project, info),
    }

    sections: dict[str, dict] = {}
    for section_id, _title in COMMERCIAL_TEMPLATE_SECTIONS:
        sections[section_id] = builders[section_id]()

    for section_id, title in COMMERCIAL_TABLE_SECTIONS:
        key, fact_key, headers = _TABLE_SPECS[section_id]
        sections[section_id] = _fact_table(title, knowledge, key, fact_key, headers)

    return sections