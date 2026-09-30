r"""快照当前已跑项目的解析/匹配行为，作为回归基线。

用法:
    python scripts/regression_snapshot.py --save <输出路径>    # 保存基线（第 0 步）
    python scripts/regression_snapshot.py --diff <基线路径>    # 对比当前快照与基线

快照内容（每项目）:
  - catalog: score / decision / status
  - workspace: reasoning.score / decision、fineReview.score / decision / statusCounts / clauseCount
  - pack: analysisPack.moduleCounts（technical/qualification/commercial/evaluation/...）

这些字段正好对应后续两处改动的影响面:
  - 技术参数漏抽(改路由)  -> moduleCounts.technical 应从 1 涨到 ~100
  - 匹配口径(改匹配)      -> fineReview.statusCounts / score / decision
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME = REPO_ROOT / "runtime"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _load_catalog() -> list[dict[str, Any]]:
    value = _read_json(RUNTIME / "catalog.json")
    return value if isinstance(value, list) else []


def _load_workspaces() -> dict[str, dict[str, Any]]:
    value = _read_json(RUNTIME / "workspaces.json")
    return value if isinstance(value, dict) else {}


def _snapshot_project(project: dict[str, Any], workspaces: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pid = project.get("id", "")
    ws = workspaces.get(project.get("workspaceId", "")) or {}
    reasoning = ws.get("reasoning") or {}
    fine_review = reasoning.get("fineReview") or {}

    pack = _read_json(RUNTIME / "projects" / pid / "pack" / "tender_pack.json") or {}
    analysis_pack = pack.get("analysisPack") or {}

    def _first(*keys: str) -> Any:
        for key in keys:
            value = fine_review.get(key)
            if value not in (None, "", [], {}):
                return value
        return None

    entry: dict[str, Any] = {
        "title": project.get("title", ""),
        "workspaceId": project.get("workspaceId", ""),
        "status": project.get("status", ""),
        "catalogScore": project.get("score"),
        "catalogDecision": project.get("decision", ""),
        "reasoningScore": reasoning.get("score"),
        "reasoningDecision": reasoning.get("decision", ""),
        "fineReviewScore": fine_review.get("score"),
        "fineReviewDecision": fine_review.get("decision") or fine_review.get("recommendation", ""),
        "fineReviewStatusCounts": fine_review.get("statusCounts") or {},
        "fineReviewClauseCount": fine_review.get("clauseCount"),
        "moduleCounts": analysis_pack.get("moduleCounts") or {},
    }
    # 只保留非空字段，减小体积
    return {k: v for k, v in entry.items() if v not in (None, "", {}, [])}


def build_snapshot() -> dict[str, Any]:
    catalog = _load_catalog()
    workspaces = _load_workspaces()
    projects: dict[str, Any] = {}
    for project in catalog:
        pid = project.get("id", "")
        if not pid:
            continue
        projects[pid] = _snapshot_project(project, workspaces)
    return {
        "schemaVersion": "tender-behavior-baseline/v1",
        "generatedAt": datetime.now().isoformat(timespec="seconds"),
        "date": date.today().isoformat(),
        "projectCount": len(projects),
        "projects": projects,
    }


def _diff(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    base_projects = baseline.get("projects", {})
    cur_projects = current.get("projects", {})
    changed: list[dict[str, Any]] = []
    added = sorted(set(cur_projects) - set(base_projects))
    removed = sorted(set(base_projects) - set(cur_projects))
    for pid in sorted(set(base_projects) & set(cur_projects)):
        base = base_projects[pid]
        cur = cur_projects[pid]
        diffs: dict[str, Any] = {}
        for key in (
            "catalogScore", "catalogDecision", "reasoningScore", "reasoningDecision",
            "fineReviewScore", "fineReviewDecision", "fineReviewStatusCounts",
            "fineReviewClauseCount", "moduleCounts",
        ):
            if base.get(key) != cur.get(key):
                diffs[key] = {"before": base.get(key), "after": cur.get(key)}
        if diffs:
            changed.append({"projectId": pid, "title": base.get("title", ""), "diffs": diffs})
    return {
        "changedCount": len(changed),
        "addedCount": len(added),
        "removedCount": len(removed),
        "added": added,
        "removed": removed,
        "changed": changed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save", type=Path, help="保存当前快照为基线")
    parser.add_argument("--diff", type=Path, help="与指定基线对比")
    args = parser.parse_args()

    current = build_snapshot()
    print(f"projectCount={current['projectCount']}")

    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"baseline saved: {args.save}")
        return 0

    if args.diff:
        baseline = _read_json(args.diff)
        if not isinstance(baseline, dict) or not baseline.get("projects"):
            print(f"基线无效或为空: {args.diff}")
            return 2
        result = _diff(baseline, current)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(json.dumps(current, ensure_ascii=False, indent=2)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
