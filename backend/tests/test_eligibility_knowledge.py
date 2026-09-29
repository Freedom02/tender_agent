from __future__ import annotations

import unittest

from backend.app.document_recognition.storage import build_company_knowledge, build_eligibility_knowledge
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


if __name__ == "__main__":
    unittest.main()
