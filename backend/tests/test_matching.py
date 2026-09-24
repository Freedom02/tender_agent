from __future__ import annotations

import json
from pathlib import Path

from backend.app.tender_matching.company_facts import build_company_ledger
from backend.app.tender_matching.deterministic import (
    LEVEL_RANK,
    ROUTING,
    check_count_requirement,
    check_date_valid,
    deterministic_match,
    match_financial,
    match_qualification,
    match_region,
    parse_qualification_req,
    score_performance,
    solve_personnel,
)
from backend.app.tender_matching.semantic_judge import extract_json, recall_candidates

# ============================================================
# 手工构造台账（不依赖 M3 连通）
# ============================================================
_CERT_ERJI = {
    "qualificationId": "q-001",
    "qualificationName": "建筑工程施工总承包",
    "level": "贰级",
    "verificationStatus": "已核验有效",
    "validUntil": "2027-06-01",
}
_CERT_SANJI = {
    "qualificationId": "q-002",
    "qualificationName": "建筑工程施工总承包",
    "level": "叁级",
    "verificationStatus": "已核验有效",
    "validUntil": "2026-12-31",
}
_CERT_EXPIRED = {
    "qualificationId": "q-003",
    "qualificationName": "电子与智能化工程",
    "level": "贰级",
    "verificationStatus": "已核验过期",
    "validUntil": "2020-01-01",
}
_CERT_UNVERIFIED = {
    "qualificationId": "q-004",
    "qualificationName": "市政公用工程",
    "level": "一级",
    "verificationStatus": "未核验",
    "validUntil": "2027-12-31",
}
_CERT_NO_LEVEL = {
    "qualificationId": "q-005",
    "qualificationName": "信息技术服务管理体系认证",
    "level": "不分等级",
    "verificationStatus": "有效",
    "validUntil": "2028-01-01",
}
_CERT_CREDENTIAL = {
    "credentialId": "c-001",
    "credentialName": "ISO27001 信息安全管理体系认证",
    "credentialType": "体系认证",
    "verificationStatus": "有效",
    "validUntil": "2027-08-01",
}

_STAFF_ONE = {
    "staffId": "s-001",
    "name": "张三",
    "professionalCertificates": "一级建造师",
    "specialty": "建筑工程",
    "title": "高级工程师",
    "currentStatus": "在职",
}
_STAFF_TWO = {
    "staffId": "s-002",
    "name": "李四",
    "professionalCertificates": "二级建造师",
    "specialty": "建筑工程",
    "title": "工程师",
    "currentStatus": "在职",
}
_STAFF_RESIGNED = {
    "staffId": "s-003",
    "name": "王五",
    "professionalCertificates": "一级建造师",
    "specialty": "建筑工程",
    "title": "工程师",
    "currentStatus": "离职",
}

_PROJ_ONE = {
    "performanceId": "p-001",
    "caseName": "某园区数字孪生平台",
    "industry": "智慧园区",
    "contractAmount": 3_200_000,
    "completionDate": "2024-06-01",
}
_PROJ_TWO = {
    "performanceId": "p-002",
    "caseName": "某园区可视化平台",
    "industry": "智慧园区",
    "contractAmount": 1_800_000,
    "completionDate": "2025-01-15",
}

_FIN = {
    "financialEvidenceId": "f-001",
    "evidenceType": "审计报告",
    "assetAmount": 50_000_000,
}

_ORG = {
    "organizationId": "org-1",
    "organizationName": "北京优锘科技股份有限公司",
    "regionScope": "福建省,广东省",
    "totalAssetsWan": 5000,
    "annualRevenueWan": 8000,
}


def _ledger(**overrides):
    certs = [_CERT_ERJI, _CERT_SANJI, _CERT_EXPIRED, _CERT_UNVERIFIED, _CERT_NO_LEVEL, _CERT_CREDENTIAL]
    return build_company_ledger({
        "organization": overrides.get("organization", _ORG),
        "qualification_certificate": overrides.get("quals", [_CERT_ERJI, _CERT_SANJI, _CERT_EXPIRED, _CERT_UNVERIFIED, _CERT_NO_LEVEL]),
        "credential": overrides.get("creds", [_CERT_CREDENTIAL]),
        "staff_member": overrides.get("staff", [_STAFF_ONE, _STAFF_TWO, _STAFF_RESIGNED]),
        "performance_case": overrides.get("projs", [_PROJ_ONE, _PROJ_TWO]),
        "financial_evidence": overrides.get("fin", [_FIN]),
        "technical_product": overrides.get("prods", []),
        "technical_parameter": overrides.get("params", []),
    } | {k: v for k, v in overrides.items() if k not in ("organization","quals","creds","staff","projs","fin","prods","params")})


# ============================================================
# 资质
# ============================================================

def test_parse_qualification_req_erji():
    req = parse_qualification_req("建筑工程施工总承包贰级及以上")
    assert req["category"] == "建筑工程施工总承包及以上"
    assert req["level"] == "贰级"


def test_match_qualification_satisfy():
    result = match_qualification("建筑工程施工总承包贰级及以上", [_CERT_ERJI])
    assert result is not None
    assert result["status"] == "满足"


def test_match_qualification_level_insufficient():
    result = match_qualification("建筑工程施工总承包壹级及以上", [_CERT_SANJI])
    assert result is not None
    assert result["status"] == "不满足"
    assert "等级不足" in result["reason"]


def test_match_qualification_expired():
    result = match_qualification("电子与智能化工程贰级及以上", [_CERT_EXPIRED])
    assert result is not None
    assert result["status"] == "不满足"
    assert "过期" in result["reason"]


def test_match_qualification_unverified():
    result = match_qualification("市政公用工程一级及以上", [_CERT_UNVERIFIED])
    assert result is not None
    assert result["status"] == "待补材料"
    assert "未核验" in result["reason"]


def test_match_qualification_no_match():
    result = match_qualification("水利水电工程施工总承包特级", [_CERT_ERJI])
    assert result is None  # 找不到 → 路由兜底


def test_match_qualification_credential_in_ledger():
    ledger = _ledger(quals=[], creds=[_CERT_CREDENTIAL])
    result = deterministic_match("ISO27001 信息安全管理体系认证", "qualification", ledger)
    assert result is not None
    assert result["status"] == "满足"


# ============================================================
# 人员
# ============================================================

def test_solve_personnel_satisfy():
    result = solve_personnel("一级建造师（建筑工程专业）1名", [_STAFF_ONE, _STAFF_TWO, _STAFF_RESIGNED])
    assert result is not None
    assert result["status"] == "满足"


def test_solve_personnel_shortfall():
    result = solve_personnel("一级建造师（建筑工程专业）2名", [_STAFF_ONE])
    assert result is not None
    assert result["status"] == "不满足"
    assert "缺 1 名" in result["reason"]


def test_solve_personnel_resigned_not_counted():
    """离职人员不计入可用。"""
    result = solve_personnel("一级建造师（建筑工程专业）1名", [_STAFF_RESIGNED])
    assert result is not None
    assert result["status"] == "不满足"


def test_solve_personnel_no_constraint():
    """解析不出人员约束时返回 None（不硬判）。"""
    result = solve_personnel("项目团队应具有丰富经验", [_STAFF_ONE])
    assert result is None


# ============================================================
# 业绩
# ============================================================

def test_score_performance_count_shortfall():
    result = score_performance("近三年承担过不少于3个智慧园区数字孪生平台项目", [_PROJ_ONE, _PROJ_TWO])
    assert result is not None
    assert result["status"] == "不满足"
    assert "缺 1 条" in result["reason"]


def test_score_performance_amount_threshold():
    result = score_performance("近三年承担过单个合同金额不低于500万元的类似项目", [_PROJ_ONE, _PROJ_TWO])
    assert result is not None
    assert result["status"] == "不满足"  # 320万<500万→不满足
    # _PROJ_ONE: 3200000元 = 320万 < 500万，所以不通过
    # _PROJ_TWO: 1800000元 = 180万 < 500万，不通过
    # 0 passed, no count requirement → 不满足
    assert "未找到" in result["reason"] or "0 条" in result["reason"]


def test_score_performance_empty():
    result = score_performance("近三年承担过不少于1个类似项目", [])
    assert result is not None
    assert result["status"] == "不满足"
    assert "业绩库为空" in result["reason"]


def test_score_performance_no_constraint():
    """无金额/年限/行业/数量约束 → None。"""
    result = score_performance("类似项目业绩", [_PROJ_ONE])
    assert result is None


# ============================================================
# 财务
# ============================================================

def test_match_financial_satisfy():
    result = match_financial("净资产不低于2000万元", [_FIN], {})
    assert result is not None
    assert result["status"] == "满足"
    assert "50000000" in result["reason"]  # 5000万元 → 50000000元


def test_match_financial_not_satisfy():
    result = match_financial("净资产不低于8000万元", [_FIN], {})
    assert result is not None
    assert result["status"] == "不满足"


def test_match_financial_org_fallback():
    """无 FinancialEvidence 时走 organization 兜底（totalAssetsWan ×10000）。"""
    result = match_financial("净资产不低于2000万元", [], _ORG)
    assert result is not None
    assert result["status"] == "满足"


def test_match_financial_unclear_metric():
    """指标不明确 → None。"""
    result = match_financial("企业财务状况良好", [_FIN], {})
    assert result is None


def test_deterministic_match_financial_document_requirement():
    ledger = _ledger(fin=[{**_FIN, "evidenceType": "2025年度财务审计报告", "status": "已核验"}])
    result = deterministic_match("提供由会计师事务所出具的2024年度或2025年度财务审计报告，或银行出具的资信证明", "financial", ledger)
    assert result is not None
    assert result["status"] == "满足"
    assert result["matchedFactIds"] == ["f-001"]


def test_subject_type_branch_does_not_match_enterprise_license_to_institution():
    ledger = _ledger(
        quals=[],
        creds=[{
            "credentialId": "cred-business-license",
            "credentialName": "营业执照",
            "status": "已核验",
        }],
    )
    result = deterministic_match("营业执照等证明文件：投标人为事业单位的，提供有效的事业单位法人证书复印件", "qualification", ledger)
    assert result is None


def test_multi_branch_subject_type_clause_matches_enterprise_license():
    """多主体"择一"整条：企业命中"营业执照"分支即满足，不被事业单位分支误判。"""
    ledger = _ledger(
        quals=[],
        creds=[{
            "credentialId": "cred-business-license",
            "credentialName": "营业执照",
            "status": "已核验",
        }],
    )
    clause = (
        "营业执照等证明文件：①投标人为企业的，提供有效的营业执照复印件；"
        "投标人为事业单位的，提供有效的事业单位法人证书复印件；"
        "投标人为社会团体的，提供有效的社会团体法人登记证书复印件；"
        "投标人为合伙企业、个体工商户的，提供有效的营业执照复印件；"
        "投标人为非企业专业服务机构的，提供有效的执业许可证等证明材料复印件；"
        "投标人为自然人的，提供有效的自然人身份证件复印件；"
        "其他投标人应按照有关法律、法规和规章规定，提供有效的相应具体证照复印件。"
    )
    result = deterministic_match(clause, "qualification", ledger)
    assert result is not None
    assert result["status"] == "满足"
    assert result["matchedFactIds"] == ["cred-business-license"]


# ============================================================
# 地域
# ============================================================

def test_match_region_satisfy():
    result = match_region("投标人须在福建省内注册", _ORG)
    assert result is not None
    assert result["status"] == "满足"


def test_match_region_not_satisfy():
    result = match_region("投标人须在浙江省内注册", _ORG)
    assert result is not None
    assert result["status"] == "不满足"


def test_match_region_nationwide():
    org = {"organizationId": "org-x", "regionScope": "全国"}
    result = match_region("投标人须在福建省内注册", org)
    assert result is not None
    assert result["status"] == "满足"


def test_match_region_no_keyword():
    result = match_region("投标人应在本地有服务机构", _ORG)
    assert result is None  # 无省份关键词


# ============================================================
# 工具函数
# ============================================================

def test_check_date_valid():
    assert check_date_valid("2027-06-01") is True
    assert check_date_valid("2020-01-01") is False
    assert check_date_valid("") is True  # 空串不误杀


def test_check_count_requirement():
    assert check_count_requirement(3, 3) is True
    assert check_count_requirement(3, 2) is False


def test_level_rank():
    assert LEVEL_RANK["特级"] == 6
    assert LEVEL_RANK["一级"] == 5
    assert LEVEL_RANK["二级"] == 4
    assert LEVEL_RANK["丁级"] == 2
    assert LEVEL_RANK["不分等级"] == 1
    assert LEVEL_RANK[""] == 0


# ============================================================
# 台账 + 分发器
# ============================================================

def test_build_company_ledger():
    ledger = _ledger()
    assert "certificates" in ledger
    assert len(ledger["certificates"]) == 6  # 5 quals + 1 cred
    assert any(c["_source"] == "credential" for c in ledger["certificates"])
    assert len(ledger["personnel"]) == 3
    assert len(ledger["projects"]) == 2
    assert ledger["organization"]["organizationId"] == "org-1"


def test_deterministic_match_dispatcher():
    ledger = _ledger()
    assert deterministic_match("建筑工程施工总承包贰级及以上", "qualification", ledger) is not None
    assert deterministic_match("一级建造师（建筑工程专业）1名", "personnel", ledger) is not None
    assert deterministic_match("近三年承担过不少于3个智慧园区项目", "performance", ledger) is not None
    assert deterministic_match("净资产不低于2000万元", "financial", ledger) is not None
    assert deterministic_match("福建省内注册", "region", ledger) is not None
    # 非确定类别的 category → None
    assert deterministic_match("服务方案完善", "service_plan", ledger) is None
    assert deterministic_match("报价合理", "pricing", ledger) is None


# ============================================================
# 语义精排
# ============================================================

def test_extract_json_code_block():
    content = '```json\n{"status":"不满足","matchedFactIds":["p-001"],"reason":"缺\\u4e1a\\u7ee9"}\n```'
    result = extract_json(content)
    assert result is not None
    assert result["status"] == "不满足"
    assert "p-001" in result["matchedFactIds"]


def test_extract_json_bare_object():
    result = extract_json('{"status": "满足", "matchedFactIds": ["q-001"]}')
    assert result is not None
    assert result["status"] == "满足"


def test_extract_json_bare_array():
    result = extract_json('[{"status": "不满足", "matchedFactIds": ["p-001"]}, {}]')
    assert result is not None
    assert result["status"] == "不满足"


def test_extract_json_garbled():
    result = extract_json("这是一段解释。")
    assert result is None


def test_extract_json_empty():
    assert extract_json("") is None
    assert extract_json(None) is None


def test_recall_candidates():
    ledger = _ledger()
    candidates = recall_candidates("qualification", ledger, top_k=3)
    assert 1 <= len(candidates) <= 3
    # 性能：projects
    candidates = recall_candidates("performance", ledger, top_k=5)
    assert len(candidates) <= 2  # 只有 2 条业绩
    # other：全部来源
    candidates = recall_candidates("other", ledger, top_k=3)
    assert len(candidates) >= 1


# ============================================================
# gold.json 基线（评测）
# ============================================================

def test_gold_json_evaluation():
    gold_path = Path(__file__).parent / "gold.json"
    if not gold_path.exists():
        return  # gold.json 尚未编写时跳过，不阻塞 CI

    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    ledger = _ledger()
    passed = 0
    failed = 0
    failures: list[str] = []

    for case in gold.get("cases", []):
        req_text = str(case["requirement"])
        category = str(case.get("category", "other"))
        expected = str(case["expectedStatus"])
        result = deterministic_match(req_text, category, ledger)
        actual = result["status"] if result else "待补材料"
        if actual == expected:
            passed += 1
        else:
            failed += 1
            failures.append(f"  {req_text[:60]} → 期望 {expected}，实际 {actual}")

    total = passed + failed
    rate = round(passed * 100 / total) if total else 0
    summary = f"评测基线：{passed}/{total} 通过 ({rate}%)"
    assert failed == 0, f"{summary}\n失败用例：\n" + "\n".join(failures)
