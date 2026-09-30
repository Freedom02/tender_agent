from __future__ import annotations

import unittest

from backend.app.document_recognition.storage import build_company_knowledge, build_eligibility_knowledge
from backend.app.tender_matching.builtin_target_processing import _facts_by_ids
from backend.app.tender_matching.company_facts import build_company_ledger
from backend.app.tender_matching.deterministic import deterministic_match
from backend.app.tender_matching.document_matching import _fact_to_evidence
from backend.app.tender_matching.eligibility import _candidate_records as ann_candidate_records
from backend.app.tender_matching.eligibility import _evidence_for_records as ann_evidence


class EligibilityKnowledgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = {
            "organization": {
                "organizationName": "示例科技",
                "capabilities": "项目实施与数据接入",
                "serviceScope": "数字孪生",
            },
            "qualification_certificate": [
                {
                    "qualificationId": "qc-1",
                    "qualificationName": "ISO27001信息安全管理体系认证",
                    "certificateNo": "C-001",
                    "status": "已核验",
                    "evidencePath": "libraries/company/证明材料/资质认证/1/",
                    "validUntil": "2027-01-01",
                },
            ],
            "performance_case": [
                {
                    "performanceId": "perf-1",
                    "caseName": "某园区数字孪生平台建设项目",
                    "industry": "智慧园区",
                    "region": "北京市",
                    "evidencePath": "libraries/company/证明材料/类似业绩/1/",
                },
            ],
            "staff_member": [
                {
                    "staffId": "staff-1",
                    "name": "张三",
                    "role": "项目经理",
                    "yearsOfExperience": 5,
                },
            ],
            "financial_evidence": [
                {
                    "financialEvidenceId": "fin-1",
                    "evidenceType": "审计报告",
                    "issuedBy": "某会计师事务所",
                    "evidencePath": "",
                },
            ],
            "technical_product": [
                {
                    "productId": "prd-1",
                    "productName": "ThingJS平台",
                    "aliases": "数字孪生平台",
                    "category": "数字孪生",
                    "evidencePath": "",
                },
            ],
            "technical_parameter": [
                {"parameterId": "param-1", "description": "物联数据接入与三维建模", "evidencePath": ""},
            ],
            "credential": [
                {"credentialId": "cred-1", "credentialName": "软件著作权-园区可视化", "credentialType": "软著"},
            ],
        }

    def test_build_eligibility_knowledge_maps_all_fact_classes(self) -> None:
        knowledge = build_eligibility_knowledge(self.facts)

        self.assertEqual(knowledge["qualifications"][0]["name"], "ISO27001信息安全管理体系认证")
        self.assertEqual(knowledge["qualifications"][0]["id"], "qc-1")
        self.assertEqual(
            knowledge["qualifications"][0]["evidencePath"],
            "libraries/company/证明材料/资质认证/1/",
        )
        self.assertEqual(knowledge["projectReferences"][0]["name"], "某园区数字孪生平台建设项目")
        self.assertEqual(knowledge["personnel"][0]["name"], "张三")
        self.assertEqual(knowledge["financialRecords"][0]["name"], "审计报告")
        self.assertEqual(knowledge["credentials"][0]["name"], "软件著作权-园区可视化")
        self.assertEqual(knowledge["products"][0]["id"], "prd-1")
        # capabilities 含技术参数（带证据）+ 组织能力清单（纯词表）
        self.assertTrue(any(c["name"] == "物联数据接入与三维建模" for c in knowledge["capabilities"]))
        self.assertTrue(any(c["name"] == "项目实施与数据接入" for c in knowledge["capabilities"]))

    def test_merged_knowledge_has_vocabulary_and_records(self) -> None:
        knowledge = {**build_company_knowledge(self.facts), **build_eligibility_knowledge(self.facts)}
        # 语义词表仍在（服务公告语义筛选）
        self.assertTrue(any(d["name"] == "数字孪生" for d in knowledge["businessDirections"]))
        self.assertEqual(knowledge["industryScenarios"][0]["name"], "智慧园区")
        # 资格匹配记录也在
        self.assertTrue(knowledge["qualifications"])

    def test_announcement_qualification_matching_finds_fact_and_evidence(self) -> None:
        knowledge = {**build_company_knowledge(self.facts), **build_eligibility_knowledge(self.facts)}
        matched = ann_candidate_records("qualification", "投标人须具有ISO27001信息安全管理体系认证", knowledge)
        self.assertEqual([m["name"] for m in matched], ["ISO27001信息安全管理体系认证"])

        evidences = ann_evidence(matched, knowledge)
        self.assertEqual(len(evidences), 1)
        self.assertEqual(evidences[0]["sourceType"], "evidence")
        self.assertEqual(evidences[0]["sourceLocator"], "libraries/company/证明材料/资质认证/1/")
        self.assertEqual(evidences[0]["status"], "已核验")

    def test_announcement_personnel_financial_matching(self) -> None:
        knowledge = {**build_company_knowledge(self.facts), **build_eligibility_knowledge(self.facts)}
        personnel = ann_candidate_records("personnel", "拟派项目经理1名具有3年以上经验", knowledge)
        self.assertEqual([m["name"] for m in personnel], ["张三"])
        financial = ann_candidate_records("financial", "提供最近一年审计报告", knowledge)
        self.assertEqual([m["name"] for m in financial], ["审计报告"])

    def test_fine_qualification_evidence_derived_from_evidence_path(self) -> None:
        ledger = build_company_ledger(self.facts)
        result = deterministic_match("具有ISO27001信息安全管理体系认证", "qualification", ledger)
        self.assertEqual(result["matchedFactIds"], ["qc-1"])
        evidence = _fact_to_evidence(ledger["certificates"][0])
        self.assertEqual(evidence["sourceType"], "evidence")
        self.assertEqual(evidence["sourceLocator"], "libraries/company/证明材料/资质认证/1/")
        self.assertEqual(evidence["id"], "qc-1")

    def test_technical_parameter_records_resolve_to_own_id_not_parent_product(self) -> None:
        """回归：技术参数记录必须解析成自身 parameterId，而非父产品 productId。

        此前 _fact_records 只写通用 id 字段、不写 parameterId，导致 _facts_by_ids
        按「parameterId → productId → id」取主键时，技术参数记录（id=param-x +
        productId=prd-1）全部塌陷成 prd-1；一旦匹配集含产品 ID，就会把该产品的
        **全部参数**当成命中证据灌进一条技术要求（前端每条技术参数挂几十条企业证据）。
        """
        facts = {
            "technical_product": [
                {"productId": "prd-1", "productName": "ThingJS平台", "evidencePath": ""},
            ],
            "technical_parameter": [
                {"parameterId": "param-1", "productId": "prd-1", "description": "参数一", "evidencePath": "libraries/company/证明材料/技术参数/1/"},
                {"parameterId": "param-2", "productId": "prd-1", "description": "参数二", "evidencePath": "libraries/company/证明材料/技术参数/2/"},
                {"parameterId": "param-3", "productId": "prd-1", "description": "参数三", "evidencePath": ""},
            ],
        }
        knowledge = {**build_company_knowledge(facts), **build_eligibility_knowledge(facts)}

        # 技术对齐产生的匹配集：命中参数 + 产品兜底
        matched = _facts_by_ids(knowledge, ["param-2", "prd-1"])
        ids = sorted(_fact_to_evidence(f)["id"] for f in matched)
        # 只该命中「产品本体 + 对口参数」两条，而不是产品全部参数
        self.assertEqual(ids, ["param-2", "prd-1"])

        # 参数记录自身应带 parameterId 字段，供各 _fact_id 变体按族内主键解析
        param_record = next(c for c in knowledge["capabilities"] if c.get("id") == "param-2")
        self.assertEqual(param_record["parameterId"], "param-2")


if __name__ == "__main__":
    unittest.main()
