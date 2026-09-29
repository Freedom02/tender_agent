from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.store import Store


def _workspace(workspace_id: str = "ws-del-1", project_id: str = "project-del-1") -> dict:
    return {
        "id": workspace_id,
        "workspaceId": workspace_id,
        "projectId": project_id,
        "title": "待删除项目",
        "project": {"id": project_id, "title": "待删除项目", "region": "北京市"},
        "reasoning": {"score": 80},
        "draft": "",
        "draftVersion": 0,
    }


class WorkspaceDeleteTests(unittest.TestCase):
    """删除历史项目：工作区 + 关联抓取项目 + 跟进提醒 + runtime 解析产物。

    重点回归 persist_runtime / _refresh_workspaces_from_disk 的「只增不删」语义：
    它们以磁盘副本为底做合并，若不用墓碑强制剔除，已删除的工作区会被重新合并回来。
    """

    def _store(self, directory: str) -> Store:
        store = Store(Path(directory))
        store.initialize()
        return store

    def test_delete_removes_workspace_from_memory_and_disk(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.workspaces["ws-del-1"] = _workspace()
            store.persist_runtime()

            result = store.delete_workspace("ws-del-1")

            self.assertIsNotNone(result)
            self.assertEqual(result["workspaceId"], "ws-del-1")
            self.assertEqual(result["projectId"], "project-del-1")
            self.assertEqual(result["title"], "待删除项目")
            self.assertNotIn("ws-del-1", store.workspaces)
            on_disk = json.loads(store.workspaces_path.read_text(encoding="utf-8"))
            self.assertNotIn("ws-del-1", on_disk)

    def test_delete_removes_linked_catalog_entries_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.workspaces["ws-del-1"] = _workspace()
            store.catalog = [
                {"id": "project-del-1", "title": "待删除项目", "workspaceId": "ws-del-1"},
                {"id": "notice-keep", "title": "保留的公告"},
                {"id": "project-other", "title": "别的项目", "workspaceId": "ws-other"},
            ]

            result = store.delete_workspace("ws-del-1")

            self.assertEqual(result["removedProjects"], ["project-del-1"])
            self.assertEqual(
                [item["id"] for item in store.catalog],
                ["notice-keep", "project-other"],
            )
            on_disk = json.loads(store.catalog_path.read_text(encoding="utf-8"))
            self.assertEqual([item["id"] for item in on_disk], ["notice-keep", "project-other"])

    def test_delete_removes_only_linked_reminders(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.workspaces["ws-del-1"] = _workspace()
            store.reminders = [
                {"id": "r1", "projectId": "project-del-1"},
                {"id": "r2", "projectId": "notice-keep"},
            ]

            result = store.delete_workspace("ws-del-1")

            self.assertEqual(result["removedReminders"], 1)
            self.assertEqual([item["id"] for item in store.reminders], ["r2"])
            on_disk = json.loads(
                (store.runtime_root / "reminders.json").read_text(encoding="utf-8")
            )
            self.assertEqual([item["id"] for item in on_disk], ["r2"])

    def test_delete_removes_project_runtime_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.workspaces["ws-del-1"] = _workspace()
            target = store.runtime_root / "projects" / "project-del-1"
            (target / "parse").mkdir(parents=True, exist_ok=True)
            (target / "parse" / "parse-bundle.json").write_text("{}", encoding="utf-8")
            keep = store.runtime_root / "projects" / "project-other"
            keep.mkdir(parents=True, exist_ok=True)

            result = store.delete_workspace("ws-del-1")

            self.assertFalse(target.exists())
            self.assertTrue(keep.exists())
            self.assertEqual(result["removedArtifacts"], ["runtime/projects/project-del-1"])

    def test_delete_keeps_shared_unassigned_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.workspaces["ws-del-1"] = _workspace(project_id="unassigned")
            target = store.runtime_root / "projects" / "unassigned"
            target.mkdir(parents=True, exist_ok=True)

            result = store.delete_workspace("ws-del-1")

            self.assertTrue(target.exists())
            self.assertEqual(result["removedArtifacts"], [])

    def test_delete_unknown_workspace_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            self.assertIsNone(store.delete_workspace("missing"))

    def test_deleted_workspace_is_not_resurrected_by_merge(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.workspaces["ws-del-1"] = _workspace()
            store.persist_runtime()
            store.delete_workspace("ws-del-1")

            # 模拟另一个后端进程手里仍持有删除前的旧副本，随后落盘
            store.workspaces["ws-del-1"] = _workspace()

            store.persist_runtime()
            self.assertNotIn("ws-del-1", store.workspaces)
            on_disk = json.loads(store.workspaces_path.read_text(encoding="utf-8"))
            self.assertNotIn("ws-del-1", on_disk)

            store.workspaces["ws-del-1"] = _workspace()
            store._refresh_workspaces_from_disk()
            self.assertNotIn("ws-del-1", store.workspaces)

    def test_deletion_survives_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(directory)
            store.workspaces["ws-del-1"] = _workspace()
            store.persist_runtime()
            store.delete_workspace("ws-del-1")

            # 重启前把工作区手工塞回磁盘，墓碑应当在新进程初始化时把它剔除
            on_disk = json.loads(store.workspaces_path.read_text(encoding="utf-8"))
            on_disk["ws-del-1"] = _workspace()
            store.workspaces_path.write_text(
                json.dumps(on_disk, ensure_ascii=False), encoding="utf-8"
            )

            reopened = self._store(directory)
            self.assertNotIn("ws-del-1", reopened.workspaces)


if __name__ == "__main__":
    unittest.main()
