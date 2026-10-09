from __future__ import annotations

from copy import deepcopy
import json
import unittest

from backend.app.agent_runtime.clause_repair import apply_clause_repair, build_clause_repair_request
from backend.app.agent_runtime.parse_semantics import build_source_batches, hydrate_source_traces, validate_clause_batch
from backend.app.pi_agent.prompts.parse_v2 import tender_clause_repair_prompt
from backend.tests.test_native_parse_v2 import AnnotatedProtocolClient


class ClauseRepairTests(unittest.TestCase):
    def setUp(self):
        self.client = AnnotatedProtocolClient()
        self.blocks = self.client.sample["documentBlocks"]
        self.batch = build_source_batches(self.blocks, max_chars=90000)[0]
        self.original = self.client.extract_tender_clauses(inputs=self.batch)
        self.original["requirements"].sort(key=lambda item: item["responseRequired"] is not True)

    def request(self, payload):
        return build_clause_repair_request(payload, self.batch, document_blocks=self.blocks, source_file={})

    def apply(self, payload, patch, request):
        return apply_clause_repair(payload, patch, request, self.batch, document_blocks=self.blocks, source_file={})

    def test_cross_field_repair_preserves_all_untouched_objects(self):
        invalid = deepcopy(self.original)
        invalid["requirements"][0].pop("applicability")
        request = self.request(invalid)
        self.assertEqual([target["targetId"] for target in request["targets"]], ["requirements:0"])
        repaired = self.apply(invalid, {"requirementReplacements": [{"targetId": "requirements:0", "value": self.original["requirements"][0]}]}, request)
        self.assertEqual(repaired["requirements"][1:], invalid["requirements"][1:])
        self.assertEqual(repaired["evidenceRequirements"], invalid["evidenceRequirements"])
        self.assertEqual(repaired["assessments"], invalid["assessments"])
        self.assertEqual(invalid["requirements"][0].get("applicability"), None)
        self.assertLess(len(request["views"]), len(self.batch["views"]))

    def test_collects_multiple_schema_source_and_coverage_errors_in_one_repair(self):
        invalid = deepcopy(self.original)
        invalid["requirements"][0].pop("applicability")
        invalid["requirements"][1]["sourceTrace"][0]["quote"] = "不存在的采购要求"
        removed = invalid["assessments"].pop()
        request = self.request(invalid)
        codes = {issue["code"] for issue in request["issues"]}
        self.assertTrue({"schema_invalid", "source_invalid", "coverage_missing"}.issubset(codes))
        self.assertIn(removed["viewId"], request["missingViewIds"])
        patch = {"requirementReplacements": [{"targetId": target["targetId"], "value": self.original["requirements"][int(target["targetId"].split(":")[1])]}
                                            for target in request["targets"] if target["collection"] == "requirements"],
                 "assessmentReplacements": [{"targetId": target["targetId"], "value": self.original["assessments"][int(target["targetId"].split(":")[1])]}
                                           for target in request["targets"] if target["collection"] == "assessments"],
                 "addedAssessments": [removed]}
        repaired = self.apply(invalid, patch, request)
        self.assertEqual(len(repaired["requirements"]), len(self.original["requirements"]))
        self.assertEqual(len(repaired["assessments"]), len(self.batch["views"]))

    def test_omitted_view_can_add_a_real_obligation_without_rewriting_known_ones(self):
        invalid = deepcopy(self.original)
        omitted = invalid["requirements"].pop(0)
        invalid["assessments"] = [item for item in invalid["assessments"] if omitted["requirementId"] not in item["requirementIds"]]
        request = self.request(invalid)
        assessments = [item for item in self.original["assessments"] if item["viewId"] in request["missingViewIds"]]
        repaired = self.apply(invalid, {"addedRequirements": [omitted], "addedAssessments": assessments}, request)
        self.assertEqual(repaired["requirements"][:-1], invalid["requirements"])
        self.assertEqual(repaired["requirements"][-1]["requirementId"], omitted["requirementId"])

    def test_view_range_typo_is_repaired_without_regenerating_requirements(self):
        invalid = deepcopy(self.original)
        position = len(invalid["assessments"]) - 1
        correct = deepcopy(invalid["assessments"][position])
        invalid["assessments"][position]["viewId"] = correct["viewId"].split("@", 1)[0] + "@0:1"
        request = self.request(invalid)
        repaired = self.apply(invalid, {"assessmentReplacements": [{"targetId": f"assessments:{position}", "value": correct}]}, request)
        self.assertEqual(repaired["requirements"], invalid["requirements"])
        self.assertEqual(repaired["assessments"][position]["viewId"], correct["viewId"])

    def test_duplicate_assessments_can_be_removed_but_source_coverage_cannot(self):
        invalid = deepcopy(self.original)
        invalid["assessments"].append(deepcopy(invalid["assessments"][0]))
        request = self.request(invalid)
        repaired = self.apply(invalid, {"removeAssessmentTargets": [f'assessments:{len(invalid["assessments"]) - 1}']}, request)
        self.assertEqual(repaired["assessments"], self.original["assessments"])
        with self.assertRaisesRegex(ValueError, "coverage_incomplete"):
            self.apply(invalid, {"removeAssessmentTargets": ["assessments:0", f'assessments:{len(invalid["assessments"]) - 1}']}, request)

    def test_repair_cannot_modify_an_unaffected_object_or_rename_ids(self):
        invalid = deepcopy(self.original)
        invalid["requirements"][0].pop("applicability")
        request = self.request(invalid)
        with self.assertRaisesRegex(ValueError, "unique supplied object"):
            self.apply(invalid, {"requirementReplacements": [{"targetId": "requirements:1", "value": self.original["requirements"][1]}]}, request)
        changed = {**self.original["requirements"][0], "requirementId": "renamed"}
        with self.assertRaisesRegex(ValueError, "cannot change"):
            self.apply(invalid, {"requirementReplacements": [{"targetId": "requirements:0", "value": changed}]}, request)

    def test_repair_cannot_use_a_quote_outside_supplied_views(self):
        invalid = deepcopy(self.original)
        invalid["requirements"][0].pop("applicability")
        request = self.request(invalid)
        changed = deepcopy(self.original["requirements"][0])
        changed["sourceTrace"][0]["quote"] = "真实系统截图被替换为任意文字"
        with self.assertRaisesRegex(ValueError, "source_quote_outside_view"):
            self.apply(invalid, {"requirementReplacements": [{"targetId": "requirements:0", "value": changed}]}, request)

    def test_evidence_repair_requires_complete_reciprocal_links(self):
        invalid = deepcopy(self.original)
        evidence = invalid["evidenceRequirements"][0]
        parent_position = next(position for position, item in enumerate(invalid["requirements"]) if item["requirementId"] == evidence["requirementId"])
        invalid["requirements"][parent_position]["evidenceRequirementIds"].remove(evidence["evidenceRequirementId"])
        request = self.request(invalid)
        self.assertIn(f"requirements:{parent_position}", {target["targetId"] for target in request["targets"]})
        with self.assertRaisesRegex(ValueError, "reciprocal"):
            self.apply(invalid, {}, request)
        repaired = self.apply(invalid, {"requirementReplacements": [{"targetId": f"requirements:{parent_position}", "value": self.original["requirements"][parent_position]}]}, request)
        validated = validate_clause_batch(hydrate_source_traces(repaired, document_blocks=self.blocks, source_file={}, views=self.batch["views"]), self.batch)
        self.assertEqual(len(validated.evidenceRequirements), len(invalid["evidenceRequirements"]))

    def test_malformed_batches_use_full_retry_and_malformed_fields_still_have_targets(self):
        for invalid in ({"requirements": []}, {**self.original, "assessments": None}):
            self.assertIsNone(self.request(invalid))
        invalid = deepcopy(self.original)
        invalid["requirements"][0]["sourceTrace"] = None
        invalid["requirements"][0]["evidenceRequirementIds"] = None
        request = self.request(invalid)
        self.assertIn("requirements:0", {target["targetId"] for target in request["targets"]})
        self.assertEqual(request["views"], self.batch["views"])

    def test_repair_prompt_exposes_a_patch_contract_and_preserves_agent_identity(self):
        prompt = tender_clause_repair_prompt(inputs={})
        schema = json.loads(prompt.split("【严格输出契约】\n", 1)[1].split("\n【本次输入】", 1)[0])
        self.assertIn("requirementReplacements", schema["properties"])
        self.assertNotIn("requirements", schema["properties"])
        self.assertEqual(set(schema["$defs"]["SourceTrace"]["properties"]), {"segmentId", "quote"})
        self.assertIn("不得删除条款或证据", prompt)


if __name__ == "__main__":
    unittest.main()
