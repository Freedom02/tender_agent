from __future__ import annotations

import unittest
from types import SimpleNamespace

from backend.app.bid_writing.schemas import (
    build_section,
    material_keys_for_writing_profile,
    resolve_writing_profile,
)
from backend.app.pi_agent.prompts import (
    material_match_prompt,
    section_draft_prompt,
    section_plan_prompt,
)
from backend.app.pi_agent.writing_orchestrator import (
    PiWritingOrchestrator,
    _scope_knowledge,
)


class ProfileWritingClient:
    def __init__(self) -> None:
        self.config = SimpleNamespace(model="profile-test-model")
        self.profiles: list[str] = []
        self.knowledge_keys: list[set[str]] = []

    def _capture(self, writing_profile: str, knowledge: dict[str, object]) -> None:
        self.profiles.append(writing_profile)
        self.knowledge_keys.append(set(knowledge))

    def match_materials(self, **kwargs: object) -> dict[str, object]:
        self._capture(
            str(kwargs["writing_profile"]),
            kwargs["knowledge"],  # type: ignore[arg-type]
        )
        return {
            "matches": [
                {
                    "requirementId": "req-001",
                    "materialIds": ["qualification-001"],
                    "materialRefs": [
                        {
                            "materialId": "qualification-001",
                            "factKey": "qualifications",
                            "name": "有效资质",
                        }
                    ],
                    "matchStatus": "matched",
                }
            ],
            "unmatchedRequirementIds": [],
            "warnings": [],
        }

    def plan_section(self, **kwargs: object) -> dict[str, object]:
        self._capture(
            str(kwargs["writing_profile"]),
            kwargs["knowledge"],  # type: ignore[arg-type]
        )
        return {
            "sectionId": "c-01",
            "title": "商务响应",
            "requirementResponses": [
                {
                    "requirementId": "req-001",
                    "responseStrategy": "引用真实资质材料",
                    "materialIds": ["qualification-001"],
                }
            ],
            "evaluationResponses": [],
            "outline": [{"heading": "1.1 商务响应", "purpose": "响应要求"}],
            "templateInstructions": [],
            "warnings": [],
        }

    def draft_section_agent(self, **kwargs: object) -> dict[str, object]:
        self._capture(
            str(kwargs["writing_profile"]),
            kwargs["knowledge"],  # type: ignore[arg-type]
        )
        return {
            "sectionId": "c-01",
            "title": "商务响应",
            "content": "## 商务响应\n\n依据已核验材料响应。",
            "sourceRefs": [
                {
                    "materialId": "qualification-001",
                    "factKey": "qualifications",
                    "name": "有效资质",
                }
            ],
            "status": "generated",
            "warnings": [],
        }


class PiWritingProfileTests(unittest.TestCase):
    def test_section_profile_and_material_scope(self) -> None:
        technical = build_section("t-01", "技术方案", "ai", kind="technical")
        commercial = build_section("c-01", "商务响应", "ai", kind="commercial")
        template = build_section("c-02", "投标函", "template", kind="commercial")

        self.assertEqual(resolve_writing_profile(technical), "technical")
        self.assertEqual(resolve_writing_profile(commercial), "commercial")
        self.assertEqual(resolve_writing_profile(template), "template")
        self.assertIn("products", technical["materialScopes"])
        self.assertIn("financialRecords", commercial["materialScopes"])
        self.assertIn("products", template["materialScopes"])

    def test_scope_knowledge_removes_unrelated_groups(self) -> None:
        knowledge = {
            "products": [{"id": "product-001"}],
            "qualifications": [{"id": "qualification-001"}],
            "financialRecords": [{"id": "financial-001"}],
            "unrelated": [{"id": "unrelated-001"}],
        }
        technical = _scope_knowledge(knowledge, writing_profile="technical")
        commercial = _scope_knowledge(knowledge, writing_profile="commercial")

        self.assertEqual(
            set(technical),
            {"products", "qualifications"},
        )
        self.assertEqual(
            set(commercial),
            {"qualifications", "financialRecords"},
        )

    def test_commercial_section_uses_commercial_profile_end_to_end(self) -> None:
        client = ProfileWritingClient()
        result = PiWritingOrchestrator(client).draft_section(
            parse_bundle={
                "requirements": [
                    {
                        "id": "req-001",
                        "category": "qualification",
                        "requirement": "提供资质证明",
                    }
                ],
                "evaluationItems": [],
            },
            coverage=[
                {
                    "requirementId": "req-001",
                    "sectionId": "c-01",
                    "status": "planned",
                }
            ],
            section={
                "sectionId": "c-01",
                "title": "商务响应",
                "source": "ai",
                "kind": "commercial",
                "requirementIds": ["req-001"],
            },
            project={},
            company={},
            knowledge={
                "qualifications": [{"id": "qualification-001", "name": "有效资质"}],
                "products": [{"id": "product-001", "name": "技术产品"}],
                "financialRecords": [{"id": "financial-001", "name": "财务材料"}],
            },
            mode="open",
            workspace_id="w-01",
            project_id="p-01",
        )

        self.assertEqual(result["writingProfile"], "commercial")
        self.assertEqual(client.profiles, ["commercial", "commercial", "commercial"])
        self.assertTrue(
            all(keys <= {"qualifications", "financialRecords"} for keys in client.knowledge_keys)
        )

    def test_writing_prompt_keeps_only_current_context(self) -> None:
        parse_bundle = {
            "requirements": [
                {"id": "req-001", "category": "technical", "requirement": "响应参数"}
            ],
            "evaluationItems": [],
            "documentStructure": {"sections": []},
            "bidFormatSpec": {},
        }
        section = {
            "sectionId": "t-01",
            "title": "技术方案",
            "source": "ai",
            "kind": "technical",
            "requirementIds": ["req-001"],
        }
        knowledge = {
            "products": [{"id": "p-001", "name": "产品", "description": "产品说明"}],
            "financialRecords": [{"id": "f-001", "name": "财务"}],
        }
        kwargs = {
            "parse_bundle": parse_bundle,
            "coverage": [{"requirementId": "req-001", "sectionId": "t-01"}],
            "section": section,
            "project": {},
            "company": {},
            "knowledge": knowledge,
            "mode": "open",
            "workspace_id": "w-01",
            "project_id": "p-01",
            "writing_profile": "technical",
        }

        prompts = [
            material_match_prompt(**kwargs),
            section_plan_prompt(
                **kwargs,
                material_matches={"matches": []},
            ),
            section_draft_prompt(
                **kwargs,
                writing_plan={"sectionId": "t-01", "outline": []},
                material_matches={"matches": []},
            ),
        ]
        self.assertTrue(all(len(prompt) < 10000 for prompt in prompts))
        self.assertTrue(all("财务" not in prompt for prompt in prompts))


if __name__ == "__main__":
    unittest.main()
