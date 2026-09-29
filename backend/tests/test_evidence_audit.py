# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

from backend.app.bid_writing.evidence_audit import audit_evidence


def _section(section_id: str = "t-02", **overrides):
    section = {
        "sectionId": section_id,
        "title": "技术方案",
        "kind": "technical",
        "source": "ai",
        "content": "",
        "sourceRefs": [],
    }
    section.update(overrides)
    return section


class EvidenceAuditTests(unittest.TestCase):
    def _doc(self, sections):
        return {"plan": sections}

    def test_flags_fact_assertions_without_sourcerefs(self) -> None:
        result = audit_evidence(
            self._doc([_section(content="我方具备相关资质和多项业绩，累计合同额 800 万元。")])
        )
        titles = {item["title"] for item in result["riskItems"]}
        self.assertIn("无溯源断言", titles)
        self.assertIn("t-02", result["summary"]["noTraceSectionIds"])

    def test_no_flag_when_sourcerefs_present(self) -> None:
        result = audit_evidence(
            self._doc(
                [
                    _section(
                        content="我方具备相关资质和多项业绩。",
                        sourceRefs=[
                            {
                                "factKey": "qualifications",
                                "name": "ISO9001",
                                "evidencePath": "/a/b.pdf",
                            }
                        ],
                    )
                ]
            )
        )
        self.assertNotIn("无溯源断言", {item["title"] for item in result["riskItems"]})

    def test_skips_fixed_documents_and_attachments(self) -> None:
        result = audit_evidence(
            self._doc(
                [
                    _section(
                        "c-01",
                        title="投标函",
                        kind="commercial",
                        source="template",
                        content="我方具备相关资质与业绩。",
                    ),
                    _section(
                        "a-01",
                        title="营业执照",
                        kind="attachment",
                        source="attachment",
                        content="营业执照复印件（注册资本 1000 万元）。",
                    ),
                ]
            )
        )
        self.assertEqual(result["riskItems"], [])

    def test_flags_source_refs_without_evidence_path(self) -> None:
        result = audit_evidence(
            self._doc(
                [
                    _section(
                        content="我方具备 ISO9001 认证。",
                        sourceRefs=[
                            {
                                "factKey": "qualifications",
                                "name": "ISO9001",
                                "evidencePath": "",
                            }
                        ],
                    )
                ]
            )
        )
        titles = {item["title"] for item in result["riskItems"]}
        self.assertIn("证据路径缺失", titles)
        self.assertIn("t-02", result["summary"]["missingPathSectionIds"])

    def test_flags_capability_gap_markers(self) -> None:
        result = audit_evidence(
            self._doc(
                [_section(content="本项目建设方案【需人工补充：缺少具体资料】。")]
            )
        )
        titles = {item["title"] for item in result["riskItems"]}
        self.assertIn("材料缺口未补齐", titles)
        self.assertIn("t-02", result["summary"]["gapSectionIds"])
        self.assertEqual(result["summary"]["gapMarkerCount"], 1)

    def test_proofread_includes_evidence_audit(self) -> None:
        from backend.app.bid_writing.proofread import proofread

        bid_doc = {
            "plan": [_section(content="我方具备相关资质和多项业绩。")],
            "deviationTables": {},
        }
        result = proofread(
            bid_doc,
            {"rows": []},
            {"parsed": {"projectName": "X", "projectCode": "Y"}},
        )
        titles = {item["title"] for item in result["riskItems"]}
        self.assertIn("无溯源断言", titles)
        self.assertIn("证据/一致性", result["report"])

    def test_flags_commitment_conflicts_across_sections(self) -> None:
        result = audit_evidence(
            self._doc(
                [
                    _section("t-04", content="我方提供质保期 3 年。"),
                    _section("t-05", content="售后服务质保期 1 年。"),
                ]
            )
        )
        titles = {item["title"] for item in result["riskItems"]}
        self.assertIn("承诺值跨章不一致", titles)
        self.assertEqual(len(result["summary"]["commitmentConflicts"]), 1)

    def test_no_conflict_when_same_commitment(self) -> None:
        result = audit_evidence(
            self._doc(
                [
                    _section("t-04", content="我方提供质保期 3 年。"),
                    _section("t-05", content="质保期 3 年。"),
                ]
            )
        )
        self.assertNotIn(
            "承诺值跨章不一致", {item["title"] for item in result["riskItems"]}
        )

    def test_ignores_requirement_quotes_in_consistency(self) -> None:
        # 招标要求引用不应被当作投标人承诺，避免要求 vs 承诺误判。
        result = audit_evidence(
            self._doc(
                [
                    _section("t-04", content="质保期不少于 3 年。"),
                    _section("t-05", content="我方承诺质保期 1 年。"),
                ]
            )
        )
        self.assertNotIn(
            "承诺值跨章不一致", {item["title"] for item in result["riskItems"]}
        )


if __name__ == "__main__":
    unittest.main()
