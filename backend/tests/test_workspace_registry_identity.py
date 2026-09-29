from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.store import Store


class WorkspaceRegistryIdentityTests(unittest.TestCase):
    """回归：_refresh_workspaces_from_disk / persist_runtime 不得整体替换 self.workspaces。

    历史实现是 `self.workspaces = merged`（deepcopy 换新字典），会让调用方已经持有的
    `workspace = self.workspaces.get(id)` 引用变成孤儿：后续写入既不被本进程其它读取看到，
    也不会被 persist_runtime 遍历到。直接后果是 plan_bid_document 返回了完整目录，
    但紧接着 generate_bid_section 按同一引用查章节却查不到，接口回 404。
    """

    def test_held_workspace_reference_survives_refresh_and_persist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["ws-1"] = {"id": "ws-1"}
            ref = store.workspaces["ws-1"]

            store._refresh_workspaces_from_disk()
            self.assertIs(store.workspaces["ws-1"], ref)

            ref["bidDocument"] = {"plan": [{"sectionId": "qualification-01"}]}
            store.persist_runtime()
            self.assertIs(store.workspaces["ws-1"], ref)

            on_disk = json.loads(store.workspaces_path.read_text(encoding="utf-8"))
            self.assertEqual(
                on_disk["ws-1"]["bidDocument"]["plan"][0]["sectionId"],
                "qualification-01",
            )

    def _matching_workspace(self) -> dict:
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
                "parsed": {"projectName": "示例招标项目", "projectCode": "MATCH-001"},
                "document": {
                    "id": "document-match-001",
                    "kind": "tender_document",
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
                    "rows": [
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
        }

    def test_plan_bid_document_persists_bid_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory))
            store.initialize()
            store.workspaces["workspace-match-001"] = self._matching_workspace()

            with patch.object(Store, "_plan_bid_document_with_pi", return_value=None):
                result = store.plan_bid_document("workspace-match-001")

            self.assertIsNotNone(result)
            bd = store.workspaces["workspace-match-001"].get("bidDocument")
            self.assertIsInstance(bd, dict)
            self.assertTrue(bd.get("plan"))

            on_disk = json.loads(store.workspaces_path.read_text(encoding="utf-8"))
            self.assertTrue(on_disk["workspace-match-001"].get("bidDocument", {}).get("plan"))


if __name__ == "__main__":
    unittest.main()
