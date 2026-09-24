from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


class DocumentMapSkillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root = Path(__file__).resolve().parents[2]
        cls.skill = (
            cls.root
            / "backend"
            / "app"
            / "workbuddy_agent"
            / "skills"
            / "tender-document-map"
        )

    def test_skill_has_required_files_and_no_template_todos(self) -> None:
        skill_text = (self.skill / "SKILL.md").read_text(encoding="utf-8")

        self.assertRegex(skill_text, r"(?m)^name: tender-document-map$")
        self.assertIn("inspect_document", skill_text)
        self.assertIn("save_document_map", skill_text)
        self.assertIn("domain-guide.md", skill_text)
        self.assertIn("boundary-policy.md", skill_text)
        self.assertNotIn("[TODO:", skill_text)
        self.assertLess(len(skill_text.splitlines()), 500)

    def test_golden_set_contains_at_least_twenty_cases(self) -> None:
        payload = json.loads(
            (self.skill / "examples" / "golden-set.json").read_text(encoding="utf-8")
        )

        self.assertEqual(payload["schemaVersion"], "tender-document-map-golden/v1")
        self.assertGreaterEqual(len(payload["cases"]), 20)
        self.assertEqual(
            len({item["id"] for item in payload["cases"]}),
            len(payload["cases"]),
        )

    def test_examples_are_valid_json_and_cover_hard_boundaries(self) -> None:
        examples = list((self.skill / "examples").glob("*.json"))
        payloads = [json.loads(path.read_text(encoding="utf-8")) for path in examples]
        names = {path.name for path in examples}

        self.assertIn("mixed-section.json", names)
        self.assertIn("template-section.json", names)
        self.assertIn("contract-exclusion.json", names)
        self.assertIn("rejection-context.json", names)
        self.assertTrue(any(item.get("expected", {}).get("domain") == "contract" for item in payloads))
        self.assertTrue(any(item.get("expected", {}).get("domain") == "template" for item in payloads))
        self.assertTrue(any("rejectionCandidate" in item.get("expected", {}) for item in payloads))

    def test_boundary_policy_mentions_source_integrity_and_retention(self) -> None:
        policy = (self.skill / "boundary-policy.md").read_text(encoding="utf-8")

        self.assertIn("Source Integrity", policy)
        self.assertIn("run_only", policy)
        self.assertIn("high-risk", policy)
        self.assertRegex(policy, re.compile(r"contract.*ignored", re.IGNORECASE | re.DOTALL))


if __name__ == "__main__":
    unittest.main()
