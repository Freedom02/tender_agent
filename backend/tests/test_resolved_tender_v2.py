from __future__ import annotations

from copy import deepcopy
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from backend.app.agent_runtime.resolved_audit import audit_resolved_tender
from backend.app.agent_runtime.resolved_sources import source_index
from backend.app.agent_runtime.resolved_tender import (
    RESOLVED_TENDER_V2,
    EvaluationRule,
    ParameterConstraint,
    ResolvedTenderV2,
    response_obligations,
)
from backend.app.bid_writing.resolved_input import resolve_tender
from backend.app.bid_writing.resolved_migration import migrate_resolved_tender
from backend.app.store import Store
from backend.tests.fixtures.resolved_tender_fixtures import digital_twin_v2


def legacy_input() -> dict:
    sample = digital_twin_v2()
    bundle = {
        "schemaVersion": "1.0", "parseBundleId": "parse-old", "projectId": "project-old", "workspaceId": "workspace-old",
        "sourceFile": deepcopy(sample["sourceFile"]), "documentBlocks": deepcopy(sample["documentBlocks"]),
        "submissionStructure": deepcopy(sample["submissionStructure"]), "templateFragments": deepcopy(sample["templateFragments"]),
        "templateFragmentsArtifact": "templates.json",
        "projectMeta": {"projectMeta": {"projectName": "数字孪生应用实训平台采购"}},
        "requirements": [
            {"requirementId": "qualification-0001", "text": sample["requirements"][0]["text"], "category": "qualification",
             "sourceSegmentIds": ["SEG-000368"], "status": "满足", "matchedMaterialIds": ["material-1"]},
            {"requirementId": "technical-0014", "text": sample["requirements"][4]["text"], "category": "technical",
             "source": {"segmentId": "SEG-000547"}, "parameters": "10万个及60fps"},
        ],
        "evaluationItems": [{"id": "eval004", "item": "异常低价审查", "criteria": "给予相关供应商的合理时间为30分钟。", "score": 30}],
    }
    bundle["templateFragments"][0]["rawXml"] = "<w:p><w:r><w:t>固定文字</w:t></w:r></w:p>"
    return {
        "requirements": deepcopy(bundle["requirements"]), "bundle": bundle,
        "eval": {"items": deepcopy(bundle["evaluationItems"]), "total": 30},
        "company": {"materials": [{"id": "material-1", "path": "proofs/license.pdf"}]},
        "organization": {"legalRepresentative": "测试负责人"},
        "knowledge": {"product": "测试产品"}, "risk": {"hardBlockers": ["old-blocker"]},
        "customLegacyField": {"keep": True},
    }


class ResolvedTenderV2ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sample = digital_twin_v2()

    def test_annotated_subset_is_source_verified(self) -> None:
        report = audit_resolved_tender(self.sample)
        self.assertTrue(report.readyForWriting, report.model_dump())
        self.assertEqual(report.counts, {"error": 0, "review": 0, "warning": 0})

    def test_non_bidder_items_and_clarification_do_not_enter_submission_writing(self) -> None:
        obligations = response_obligations(ResolvedTenderV2.model_validate(self.sample))
        ids = {item.requirementId for item in obligations}
        self.assertNotIn("qualification-0001", ids)
        self.assertNotIn("qualification-0002", ids)
        self.assertNotIn("price-review-process", ids)
        self.assertNotIn("price-clarification", ids)
        self.assertIn("technical-0012", ids)
        self.assertIn("price-deduction", ids)

    def test_multi_parameter_and_course_units_are_not_collapsed(self) -> None:
        requirements = {item["requirementId"]: item for item in self.sample["requirements"]}
        rendering = requirements["technical-0014"]
        self.assertEqual([(item["value"], item["unit"]) for item in rendering["parameters"]], [("100000", "个"), ("60", "fps")])
        course = requirements["technical-0056"]
        self.assertEqual(course["category"], "delivery")
        self.assertEqual(course["parameters"][1]["unit"], "教学时")
        self.assertNotIn("chapter_text", course["responseMode"])

    def test_multi_evidence_and_alternatives_are_preserved(self) -> None:
        evidence = {item["evidenceRequirementId"]: item for item in self.sample["evidenceRequirements"]}
        self.assertEqual(evidence["ev-screenshots"]["minimumQuantity"], 4)
        self.assertIn("加盖投标人公章", evidence["ev-screenshots"]["verificationRules"])
        self.assertEqual(evidence["ev-license"]["alternativeGroupId"], evidence["ev-institution"]["alternativeGroupId"])
        self.assertNotEqual(evidence["ev-license"]["condition"], evidence["ev-institution"]["condition"])

    def test_lowest_price_has_time_and_discount_but_no_score(self) -> None:
        self.assertEqual(self.sample["evaluationMethod"], "lowest_evaluated_price")
        self.assertEqual(self.sample["evaluationRules"][0]["parameters"][0]["unit"], "分钟")
        self.assertEqual(self.sample["evaluationRules"][1]["parameters"][0]["unit"], "%")
        self.assertTrue(all(item["maxScore"] is None for item in self.sample["evaluationRules"]))

    def test_original_volumes_and_response_tables_keep_distinct_provenance(self) -> None:
        self.assertEqual(len(self.sample["submissionStructure"]["volumes"]), 3)
        self.assertEqual(sum(len(volume["items"]) for volume in self.sample["submissionStructure"]["volumes"]), 12)
        self.assertEqual(len(self.sample["templateFragments"]), 6)
        table = self.sample["responseTableSchemas"][0]
        self.assertEqual(table["sourceTableId"], "TABLE-026")
        binding = table["rowBindings"][0]
        self.assertIsNone(binding["targetRowIndex"])
        self.assertEqual(binding["sourceTrace"][0]["tableId"], "TABLE-018")
        self.assertEqual(binding["sourceTrace"][0]["rowIndex"], 2)

    def test_strict_types_enums_and_unknown_fields_are_rejected(self) -> None:
        for key, value in (("actor", "supplier"), ("responseRequired", "true"), ("mandatory", 1), ("parseConfidence", 1.1), ("category", "technical"), ("unexpected", "value")):
            with self.subTest(key=key):
                changed = deepcopy(self.sample)
                changed["requirements"][3][key] = value
                with self.assertRaises(ValidationError):
                    ResolvedTenderV2.model_validate(changed)

    def test_interval_requires_upper_bound(self) -> None:
        with self.assertRaises(ValidationError):
            ParameterConstraint(name="range", operator="between", value="1")
        parameter = ParameterConstraint(name="range", operator="between", value="1", upperValue="2")
        self.assertEqual(parameter.upperValue, "2")

    def test_conditional_obligation_and_evidence_require_conditions(self) -> None:
        for key, position in (("requirements", 2), ("evidenceRequirements", 0)):
            with self.subTest(key=key):
                changed = deepcopy(self.sample)
                changed[key][position]["condition"] = ""
                with self.assertRaises(ValidationError):
                    ResolvedTenderV2.model_validate(changed)

    def test_invalid_non_bidder_response_is_rejected(self) -> None:
        changed = deepcopy(self.sample)
        changed["requirements"][3]["actor"] = "agency"
        with self.assertRaises(ValidationError):
            ResolvedTenderV2.model_validate(changed)

    def test_non_response_and_unknown_items_cannot_use_actionable_modes(self) -> None:
        for position, value in ((0, ["chapter_text"]), (3, ["no_response"])):
            changed = deepcopy(self.sample)
            changed["requirements"][position]["responseMode"] = value
            with self.assertRaises(ValidationError):
                ResolvedTenderV2.model_validate(changed)

    def test_duplicate_ids_are_rejected(self) -> None:
        for key in ("requirements", "evidenceRequirements", "evaluationRules", "responseTableSchemas"):
            with self.subTest(key=key):
                changed = deepcopy(self.sample)
                changed[key].append(deepcopy(changed[key][0]))
                with self.assertRaises(ValidationError):
                    ResolvedTenderV2.model_validate(changed)

    def test_evidence_cross_references_are_reciprocal(self) -> None:
        changed = deepcopy(self.sample)
        changed["requirements"][3]["evidenceRequirementIds"] = ["nonexistent"]
        with self.assertRaises(ValidationError):
            ResolvedTenderV2.model_validate(changed)
        changed = deepcopy(self.sample)
        changed["evidenceRequirements"][0]["requirementId"] = "technical-0012"
        with self.assertRaises(ValidationError):
            ResolvedTenderV2.model_validate(changed)

    def test_dangling_evaluation_and_template_links_are_rejected(self) -> None:
        changed = deepcopy(self.sample)
        changed["evaluationRules"][0]["requirementIds"] = ["missing"]
        with self.assertRaises(ValidationError):
            ResolvedTenderV2.model_validate(changed)
        changed = deepcopy(self.sample)
        changed["responseTableSchemas"][0]["templateFragmentId"] = "missing"
        with self.assertRaises(ValidationError):
            ResolvedTenderV2.model_validate(changed)

    def test_fixed_template_and_format_links_are_validated(self) -> None:
        changed = deepcopy(self.sample)
        requirement = changed["requirements"][3]
        requirement["templateFragmentIds"] = ["tpl-qualification-01"]
        requirement["formatItemIds"] = ["qualification-01"]
        ResolvedTenderV2.model_validate(changed)
        for key in ("templateFragmentIds", "formatItemIds"):
            invalid = deepcopy(changed)
            invalid["requirements"][3][key] = ["missing"]
            with self.assertRaises(ValidationError):
                ResolvedTenderV2.model_validate(invalid)

    def test_table_bindings_cannot_reference_non_response_items_or_repeat_ids(self) -> None:
        for bindings in ([{"requirementId": "qualification-0001"}], self.sample["responseTableSchemas"][0]["rowBindings"] * 2):
            changed = deepcopy(self.sample)
            changed["responseTableSchemas"][0]["rowBindings"] = bindings
            with self.assertRaises(ValidationError):
                ResolvedTenderV2.model_validate(changed)

    def test_scores_require_rule_type_and_explicit_source(self) -> None:
        changed = deepcopy(self.sample)
        changed["evaluationRules"][0]["maxScore"] = {"value": 30.0, "sourceTrace": []}
        with self.assertRaises(ValidationError):
            ResolvedTenderV2.model_validate(changed)
        changed["evaluationRules"][0]["maxScore"]["sourceTrace"] = self.sample["evaluationRules"][0]["sourceTrace"]
        with self.assertRaises(ValidationError):
            ResolvedTenderV2.model_validate(changed)
        changed["evaluationRules"][0]["ruleType"] = "subjective_scoring"
        with self.assertRaises(ValidationError):
            ResolvedTenderV2.model_validate(changed)

    def test_generated_json_schema_exposes_contract(self) -> None:
        schema = ResolvedTenderV2.model_json_schema()
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(schema["properties"]["schemaVersion"]["const"], RESOLVED_TENDER_V2)
        self.assertIn("SourceTrace", schema["$defs"])

    def test_all_scoring_types_require_explicit_scores_and_are_disallowed_for_lowest_price(self) -> None:
        for rule_type in ("subjective_scoring", "objective_scoring", "price_scoring"):
            with self.subTest(rule_type=rule_type):
                fields = {"evaluationRuleId": "score-1", "title": "评分项", "text": "该项最高30分", "ruleType": rule_type}
                with self.assertRaises(ValidationError):
                    EvaluationRule.model_validate(fields)
                fields["maxScore"] = {"value": 30.0, "sourceTrace": [{"quote": "该项最高30分"}]}
                self.assertEqual(EvaluationRule.model_validate(fields).maxScore.value, 30.0)
                changed = deepcopy(self.sample)
                changed["evaluationRules"].append(fields)
                with self.assertRaises(ValidationError):
                    ResolvedTenderV2.model_validate(changed)


class ResolvedTenderAuditTests(unittest.TestCase):
    def test_tampered_quotes_anchors_and_identities_are_detected(self) -> None:
        for key, value, code in (("quote", "不存在的原文", "source_quote_mismatch"), ("segmentId", "SEG-missing", "source_block_missing"),
                                 ("rowIndex", 99, "source_anchor_mismatch"), ("fileId", "other-file", "source_identity_mismatch"),
                                 ("documentVersionId", "other-version", "source_identity_mismatch"), ("page", 88, "source_anchor_mismatch")):
            with self.subTest(key=key):
                sample = digital_twin_v2()
                sample["requirements"][3]["sourceTrace"][0][key] = value
                report = audit_resolved_tender(sample)
                self.assertFalse(report.readyForWriting)
                self.assertIn(code, {item.code for item in report.issues})

    def test_source_normalization_only_ignores_whitespace(self) -> None:
        sample = digital_twin_v2()
        sample["requirements"][0]["sourceTrace"][0]["quote"] = sample["requirements"][0]["text"].replace(" ", "\n")
        self.assertTrue(audit_resolved_tender(sample).readyForWriting)
        sample["requirements"][0]["sourceTrace"][0]["quote"] = sample["requirements"][0]["text"].replace("，", ",")
        self.assertIn("source_quote_mismatch", {item.code for item in audit_resolved_tender(sample).issues})

    def test_cell_paragraph_source_index_inherits_table_location(self) -> None:
        blocks = {"tables": [{"id": "table-1", "bodyChildIndex": 40, "rows": [{"rowIndex": 2, "cells": [
            {"id": "cell-1", "cellIndex": 3, "text": "cell", "paragraphs": [{"id": "paragraph-1", "text": "paragraph"}]}
        ]}]}]}
        paragraph = source_index(blocks)["paragraph-1"]
        self.assertEqual((paragraph["tableId"], paragraph["rowIndex"], paragraph["cellIndex"], paragraph["bodyChildIndex"]), ("table-1", 2, 3, 40))

    def test_ambiguous_source_ids_are_not_silently_overwritten(self) -> None:
        sample = digital_twin_v2()
        duplicate = deepcopy(sample["documentBlocks"]["units"][0])
        duplicate["text"] = "different source"
        sample["documentBlocks"]["units"].append(duplicate)
        self.assertIn("ambiguous_document_blocks", {item.code for item in audit_resolved_tender(sample).issues})

    def test_flat_and_nested_copies_share_identity_and_enrich_missing_positions(self) -> None:
        cell = {"id": "cell-1", "cellIndex": 3, "text": "same source", "bodyChildIndex": None}
        blocks = {"units": [deepcopy(cell)], "tables": [{"id": "table-1", "bodyChildIndex": 40,
                  "rows": [{"rowIndex": 2, "cells": [deepcopy(cell)]}]}]}
        indexed = source_index(blocks)["cell-1"]
        self.assertEqual((indexed["bodyChildIndex"], indexed["tableId"], indexed["rowIndex"]), (40, "table-1", 2))

    def test_unbound_tables_and_missing_evidence_or_template_need_review(self) -> None:
        sample = digital_twin_v2()
        sample["responseTableSchemas"][0]["rowBindings"] = []
        report = audit_resolved_tender(sample)
        self.assertIn("response_table_binding_missing", {item.code for item in report.issues})
        sample = digital_twin_v2()
        sample["requirements"][4]["responseMode"] += ["evidence_attachment", "fixed_template"]
        codes = {item.code for item in audit_resolved_tender(sample).issues}
        self.assertIn("evidence_obligation_missing", codes)
        self.assertIn("fixed_template_binding_missing", codes)

    def test_unknown_stage_confidence_and_missing_source_need_review(self) -> None:
        sample = digital_twin_v2()
        sample["requirements"][4]["responseStage"] = "unknown"
        sample["requirements"][4]["parseConfidence"] = 0.5
        sample["requirements"][4]["parameters"][0]["sourceTrace"] = []
        report = audit_resolved_tender(sample)
        self.assertFalse(report.readyForWriting)
        codes = {item.code for item in report.issues}
        self.assertTrue({"response_stage_unknown", "requirement_confidence_unverified", "source_trace_missing"}.issubset(codes))

    def test_minutes_cannot_verify_a_score_even_in_scoring_method(self) -> None:
        sample = digital_twin_v2()
        sample["evaluationMethod"] = "comprehensive_scoring"
        sample["evaluationRules"][0]["ruleType"] = "subjective_scoring"
        sample["evaluationRules"][0]["maxScore"] = {"value": 30.0, "sourceTrace": sample["evaluationRules"][0]["sourceTrace"]}
        codes = {item.code for item in audit_resolved_tender(sample).issues}
        self.assertIn("score_value_unverified", codes)
        self.assertIn("evaluation_method_unverified", codes)

    def test_response_table_column_and_template_tampering_is_detected(self) -> None:
        sample = digital_twin_v2()
        sample["responseTableSchemas"][0]["columns"][2]["label"] = "wrong label"
        sample["responseTableSchemas"][0]["templateFragmentId"] = "tpl-technical-03"
        codes = {item.code for item in audit_resolved_tender(sample).issues}
        self.assertIn("response_table_columns_mismatch", codes)
        self.assertIn("response_template_table_mismatch", codes)


class ResolvedTenderMigrationTests(unittest.TestCase):
    def test_migration_preserves_legacy_templates_assets_and_source_without_mutation(self) -> None:
        data = legacy_input()
        before = deepcopy(data)
        migrated = migrate_resolved_tender(data)
        self.assertEqual(data, before)
        self.assertEqual(migrated["legacy"], before)
        for key in ("documentBlocks", "sourceFile", "submissionStructure", "templateFragments", "templateFragmentsArtifact"):
            self.assertEqual(migrated[key], data["bundle"][key])
        for key in ("company", "organization", "knowledge", "risk"):
            self.assertEqual(migrated[key], data[key])
        migrated["company"]["materials"][0]["path"] = "modified"
        self.assertEqual(data, before)
        self.assertEqual(migrated["legacy"], before)

    def test_legacy_statuses_do_not_become_confirmed_obligations_or_scores(self) -> None:
        migrated = migrate_resolved_tender(legacy_input())
        self.assertTrue(all(item["actor"] == "unknown" and item["responseRequired"] is None for item in migrated["requirements"]))
        self.assertEqual(response_obligations(ResolvedTenderV2.model_validate(migrated)), [])
        self.assertIsNone(migrated["evaluationRules"][0]["maxScore"])
        self.assertEqual(migrated["legacy"]["eval"]["total"], 30)
        self.assertEqual(migrated["evaluationMethod"], "unknown")
        self.assertFalse(audit_resolved_tender(migrated).readyForWriting)

    def test_migration_is_idempotent_and_native_v2_is_validated_not_repaired(self) -> None:
        migrated = migrate_resolved_tender(legacy_input())
        self.assertEqual(migrate_resolved_tender(migrated), migrated)
        sample = digital_twin_v2()
        self.assertEqual(migrate_resolved_tender(sample), sample)
        sample["requirements"][0]["actor"] = "invalid"
        with self.assertRaises(ValidationError):
            migrate_resolved_tender(sample)

    def test_fine_review_rows_can_reuse_same_id_bundle_source(self) -> None:
        data = legacy_input()
        data["requirements"] = [{"requirementId": "technical-0014", "requirement": data["bundle"]["requirements"][1]["text"], "status": "待复核"}]
        result = migrate_resolved_tender(data)
        trace = result["requirements"][0]["sourceTrace"][0]
        self.assertEqual(trace["segmentId"], "SEG-000547")
        self.assertEqual(trace["tableId"], "TABLE-018")
        self.assertIsNone(trace["page"])
        self.assertEqual(result["legacy"]["requirements"][0]["status"], "待复核")

    def test_missing_or_nonverbatim_sources_are_explicitly_reported(self) -> None:
        data = legacy_input()
        data["requirements"][0]["sourceSegmentIds"] = ["missing"]
        data["requirements"][1]["text"] = "normalized but not verbatim"
        result = migrate_resolved_tender(data)
        self.assertEqual(result["requirements"][0]["sourceTrace"], [])
        self.assertEqual(result["requirements"][1]["sourceTrace"][0]["quote"], data["bundle"]["documentBlocks"]["units"][6]["text"])
        self.assertTrue(any("missing" in warning for warning in result["migrationWarnings"]))
        self.assertTrue(any("not a verbatim" in warning for warning in result["migrationWarnings"]))

    def test_sparse_legacy_data_has_stable_ids_and_lossless_raw_snapshot(self) -> None:
        data = {"requirements": [{"text": "条款"}, {"text": ""}, "malformed-row"], "unknownField": 123}
        result = migrate_resolved_tender(data)
        self.assertEqual(result["parseBundleId"], migrate_resolved_tender(data)["parseBundleId"])
        self.assertEqual(result["requirements"][0]["requirementId"], "legacy-requirement-0001")
        self.assertEqual(result["legacy"], data)
        self.assertTrue(any("missing clause text" in warning for warning in result["migrationWarnings"]))

    def test_nested_metadata_is_flattened_only_in_projection(self) -> None:
        data = legacy_input()
        result = migrate_resolved_tender(data)
        self.assertEqual(result["projectMeta"]["projectName"], "数字孪生应用实训平台采购")
        self.assertIn("projectMeta", result["legacy"]["bundle"]["projectMeta"])

    def test_direct_parse_bundle_and_format_structure_fallback(self) -> None:
        bundle = legacy_input()["bundle"]
        structure = bundle.pop("submissionStructure")
        bundle["bidFormatSpec"] = {"submissionStructure": structure}
        result = migrate_resolved_tender(bundle)
        self.assertEqual(result["submissionStructure"], structure)
        self.assertEqual(len(result["requirements"]), 2)

    def test_unknown_schema_versions_are_rejected(self) -> None:
        for version in ("2.0", "resolved-tender/3.0"):
            with self.assertRaises(ValueError):
                migrate_resolved_tender({"schemaVersion": version})
            with self.assertRaises(ValueError):
                resolve_tender({}, {}, schema_version=version)

    def test_resolver_is_v1_by_default_and_v2_only_by_explicit_request(self) -> None:
        data = legacy_input()
        fine_review = {"rows": data["requirements"]}
        project = {"title": "sample", "document": {"sections": [], "tables": []}}
        legacy = resolve_tender(fine_review, project, parse_bundle=data["bundle"])
        v2 = resolve_tender(fine_review, project, parse_bundle=data["bundle"], schema_version=RESOLVED_TENDER_V2)
        self.assertNotIn("schemaVersion", legacy)
        self.assertEqual(legacy["eval"]["total"], 30.0)
        self.assertEqual(v2["schemaVersion"], RESOLVED_TENDER_V2)
        self.assertEqual(v2["legacy"], legacy)
        self.assertIsNone(v2["evaluationRules"][0]["maxScore"])

    def test_native_v2_resolver_does_not_reinterpret_it_as_legacy(self) -> None:
        sample = digital_twin_v2()
        result = resolve_tender({}, {}, parse_bundle=sample, schema_version=RESOLVED_TENDER_V2, company={"name": "测试公司"})
        self.assertEqual(result["requirements"], sample["requirements"])
        self.assertEqual(result["company"], {"name": "测试公司"})
        self.assertEqual(result["origin"], "native")
        self.assertEqual(result["legacy"], {})
        self.assertEqual(sample["company"], {})

    def test_store_forwards_opt_in_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["workspace-test"] = {"id": "workspace-test", "project": {"title": "test"},
                                                 "reasoning": {"fineReview": {"rows": legacy_input()["requirements"]}}}
            bundle = legacy_input()["bundle"]
            result = store.resolve_tender("workspace-test", bundle, schema_version=RESOLVED_TENDER_V2)
            self.assertEqual(result["schemaVersion"], RESOLVED_TENDER_V2)
            self.assertNotIn("schemaVersion", store.resolve_tender("workspace-test", bundle))


if __name__ == "__main__":
    unittest.main()
