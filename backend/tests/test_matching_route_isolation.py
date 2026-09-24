from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.config.service import ensure_config
from backend.app.workbuddy_agent.workflow.integration import WorkBuddyAgentMatchingError

try:
    from backend.app.store import Store
except ModuleNotFoundError as error:  # pragma: no cover - environment dependent
    Store = None
    _STORE_IMPORT_ERROR = str(error)
else:
    _STORE_IMPORT_ERROR = ""


class _Summary:
    def model_dump(self, *, by_alias: bool = False):
        return {"recommendation": "test", "hardBlockers": [], "technicalGaps": [], "materialGaps": []}


def _fine_review(provider: str) -> dict:
    return {
        "rows": [],
        "risks": [],
        "score": 80,
        "decision": "test",
        "recommendation": "test",
        "clauseCount": 0,
        "statusCounts": {},
        "classCounts": {},
        "summary": {},
        "hardBlockers": [],
        "rejectionRisks": [],
        "unresolvedMandatory": [],
        "pendingMaterialList": [],
        "analysisPack": None,
        "extraction": {
            "method": "workbuddy_agent_mcp" if provider == "workbuddy-agent-mcp" else "rule",
            "provider": provider,
            "modelUsed": "test-model",
            "warnings": [],
            "stats": {},
            "timings": {"extraction": 0, "matching": 0},
        },
    }


@unittest.skipIf(
    Store is None,
    f"Store integration dependencies are unavailable: {_STORE_IMPORT_ERROR}",
)
class MatchingRouteIsolationTests(unittest.TestCase):
    def _store(self, root: Path, mode: str) -> Store:
        store = Store(root)
        store.config = ensure_config(
            {
                "ai": {
                    "extractionMode": mode,
                    "workbuddyAgentMcp": {
                        "runtimeDir": "runtime/test-agent",
                        "maxRetries": 0,
                    },
                }
            }
        )
        store.company = {}
        store.company_knowledge = {}
        store._company_facts = {}
        return store

    def _item(self) -> dict:
        return {
            "id": "PROJECT-ROUTE-001",
            "title": "Route test",
            "analysisText": "招标文件内容",
            "document": {
                "id": "FILE-ROUTE-001",
                "kind": "tender_document",
                "name": "route-test.txt",
                "contentHash": "route-test-hash",
                "segments": [],
                "sections": [],
                "tables": [],
                "metadata": {},
            },
        }

    def test_builtin_route_does_not_call_workbuddy_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory), "builtin")
            with (
                patch(
                    "backend.app.store.evaluate_announcement_eligibility",
                    return_value={"score": 50, "decision": "test"},
                ),
                patch(
                    "backend.app.store.evaluate_company_relevance",
                    return_value={"score": 50},
                ),
                patch(
                    "backend.app.store.evaluate_tender_document",
                    return_value=_fine_review("builtin-rule"),
                ) as builtin,
                patch(
                    "backend.app.store.evaluate_tender_document_with_workbuddy_agent",
                    side_effect=AssertionError("builtin imported WorkBuddy route"),
                ),
                patch(
                    "backend.app.store.build_workspace_decision_summary",
                    return_value=_Summary(),
                ),
            ):
                result = store.match_project(self._item())

        builtin.assert_called_once()
        self.assertEqual(result["fineReview"]["extraction"]["provider"], "builtin-rule")
        self.assertIn("technical", result["reviewOutput"]["modules"])
        self.assertIs(result["fineReview"]["reviewOutput"], result["reviewOutput"])
        self.assertNotIn("workbuddyAgent", result)

    def test_workbuddy_route_does_not_call_builtin_directly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory), "workbuddy_agent_mcp")
            with (
                patch(
                    "backend.app.store.evaluate_announcement_eligibility",
                    return_value={"score": 50, "decision": "test"},
                ),
                patch(
                    "backend.app.store.evaluate_company_relevance",
                    return_value={"score": 50},
                ),
                patch(
                    "backend.app.store.evaluate_tender_document",
                    side_effect=AssertionError("WorkBuddy route called builtin directly"),
                ),
                patch(
                    "backend.app.store.evaluate_tender_document_with_workbuddy_agent",
                    return_value={
                        "fineReview": _fine_review("workbuddy-agent-mcp"),
                        "runtime": {"status": "completed"},
                    },
                ) as agent,
                patch(
                    "backend.app.store.build_workspace_decision_summary",
                    return_value=_Summary(),
                ),
            ):
                result = store.match_project(self._item())

        agent.assert_called_once()
        self.assertEqual(
            result["fineReview"]["extraction"]["provider"],
            "workbuddy-agent-mcp",
        )
        self.assertEqual(result["workbuddyAgent"]["runtime"]["status"], "completed")

    def test_workbuddy_failure_falls_back_with_explicit_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = self._store(Path(directory), "workbuddy_agent_mcp")
            error = WorkBuddyAgentMatchingError(
                "CodeBuddy Agent SDK is unavailable.",
                runtime_result={
                    "status": "failed",
                    "errors": ["CodeBuddy Agent SDK is unavailable."],
                },
            )
            with (
                patch(
                    "backend.app.store.evaluate_announcement_eligibility",
                    return_value={"score": 50, "decision": "test"},
                ),
                patch(
                    "backend.app.store.evaluate_company_relevance",
                    return_value={"score": 50},
                ),
                patch(
                    "backend.app.store.evaluate_tender_document",
                    return_value=_fine_review("builtin-rule"),
                ) as builtin,
                patch(
                    "backend.app.store.evaluate_tender_document_with_workbuddy_agent",
                    side_effect=error,
                ),
                patch(
                    "backend.app.store.build_workspace_decision_summary",
                    return_value=_Summary(),
                ),
            ):
                result = store.match_project(self._item())

        builtin.assert_called_once()
        extraction = result["fineReview"]["extraction"]
        self.assertEqual(extraction["agentStatus"], "failed")
        self.assertEqual(extraction["fallbackMode"], "builtin")
        self.assertIn("workbuddy_agent_fallback_builtin", extraction["warnings"])
        self.assertEqual(result["reviewOutput"]["source"]["agentStatus"], "failed")
        self.assertEqual(result["reviewOutput"]["source"]["fallbackMode"], "builtin")
        self.assertIn("technical", result["reviewOutput"]["modules"])
        self.assertEqual(result["workbuddyAgent"]["fallback"], "builtin")


if __name__ == "__main__":
    unittest.main()
