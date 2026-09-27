from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.store import Store


class MatchingWritingReuseTests(unittest.TestCase):
    def _workspace(self, *, document_kind: str = "tender_document", rows: list[dict] | None = None) -> dict:
        return {
            "id": "workspace-match-001",
            "workspaceId": "workspace-match-001",
            "projectId": "project-match-001",
            "title": "示例招标项目",
            "project": {
                "id": "project-match-001",
                "title": "示例招标项目",
                "analysisText": (
                    "投标文件的组成\n"
                    "一、商务文件\n（一）投标函\n"
                    "二、技术文件\n（一）技术方案"
                ),
                "parsed": {
                    "projectName": "示例招标项目",
                    "projectCode": "MATCH-001",
                },
                "document": {
                    "id": "document-match-001",
                    "kind": document_kind,
                    "name": "招标文件.docx",
                    "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "text": "招标文件原文",
                    "sections": [
                        {
                            "sectionId": "section-1",
                            "heading": "评标办法",
                            "text": "技术方案完整、合理得分。",
                            "sourceLocator": "第六章 评标办法",
                        }
                    ],
                    "tables": [],
                    "segments": [
                        {
                            "id": "block-1",
                            "contentType": "paragraph",
                            "sourceLocator": "第六章 评标办法",
                            "text": "技术方案完整、合理得分。",
                        }
                    ],
                    "units": [],
                },
            },
            "reasoning": {
                "fineReview": {
                    "rows": rows if rows is not None else [
                        {
                            "id": "req-001",
                            "requirementId": "req-001",
                            "requirement": "提供完整的技术方案",
                            "sourceLocator": "第六章 评标办法",
                        }
                    ],
                    "risks": [],
                },
                "evaluation": {
                    "items": [
                        {
                            "id": "eval-001",
                            "criterion": "技术方案完整性",
                            "sourceLocator": "第六章 评标办法",
                        }
                    ]
                },
            },
            "bidDocument": {"plan": []},
        }

    def test_reuses_saved_match_data_without_reparsing_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["workspace-match-001"] = self._workspace()

            result = store.prepare_matching_workspace_for_writing("workspace-match-001")

            self.assertIsNotNone(result)
            assert result is not None
            bundle = result["parseBundle"]
            self.assertEqual(bundle["provider"], "matching_local")
            self.assertEqual(bundle["requirements"][0]["requirementId"], "req-001")
            self.assertEqual(bundle["evaluationItems"][0]["id"], "eval-001")
            self.assertEqual(
                bundle["documentBlocks"]["segments"][0]["id"],
                "block-1",
            )
            self.assertEqual(
                [volume["title"] for volume in bundle["submissionStructure"]["volumes"]],
                ["商务文件", "技术文件"],
            )
            self.assertEqual(result["parseSource"], "matching_local")

            saved_workspace = store.workspaces["workspace-match-001"]
            self.assertEqual(saved_workspace["parseBundleRef"]["provider"], "matching_local")
            self.assertTrue(saved_workspace["parseBundleRef"]["artifact"].endswith("parse-bundle.json"))

    def test_rejects_projects_without_a_complete_tender_document_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["announcement-only"] = self._workspace(
                document_kind="announcement",
            )
            store.workspaces["no-review-rows"] = self._workspace(rows=[])

            self.assertIsNone(
                store.prepare_matching_workspace_for_writing("announcement-only")
            )
            self.assertIsNone(
                store.prepare_matching_workspace_for_writing("no-review-rows")
            )


if __name__ == "__main__":
    unittest.main()
