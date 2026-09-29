r"""Run the P12 regression gate and write a comparable quality report.

First establish or intentionally refresh a baseline:

    .\backend\.venv\Scripts\python.exe scripts/run_tender_regression.py --accept-baseline

Normal regression run:

    .\backend\.venv\Scripts\python.exe scripts/run_tender_regression.py

The gate is deliberately conservative while the real-document gold file is
still pending manual annotation: it checks parser health, E2E execution and
operational failures, but does not claim extraction accuracy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.app.tender_matching.regression import (  # noqa: E402
    build_regression_snapshot,
    compare_regression_snapshots,
)


DEFAULT_EVALUATION_DIR = REPO_ROOT / "evaluation"
DEFAULT_BASELINE = DEFAULT_EVALUATION_DIR / "tender_regression_baseline.json"
DEFAULT_REPORT = DEFAULT_EVALUATION_DIR / "tender_regression_report.json"


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fingerprint() -> str:
    paths = [
        REPO_ROOT / "backend" / "app" / "tender_matching" / "document_parser.py",
        REPO_ROOT / "backend" / "app" / "tender_matching" / "document_structure.py",
        REPO_ROOT / "backend" / "app" / "tender_matching" / "document_fine_matching.py",
        REPO_ROOT / "backend" / "app" / "tender_matching" / "deterministic.py",
        REPO_ROOT / "backend" / "app" / "tender_matching" / "decision_summary.py",
        REPO_ROOT / "backend" / "app" / "tender_matching" / "observability.py",
        REPO_ROOT / "frontend" / "src" / "features" / "tender-matching" / "MatchingPage.vue",
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(REPO_ROOT).as_posix().encode("utf-8"))
        try:
            digest.update(path.read_bytes())
        except FileNotFoundError:
            digest.update(b"<missing>")
    return digest.hexdigest()[:16]


def _run_command(
    stage: str,
    command: list[str],
    *,
    cwd: Path = REPO_ROOT,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        status = "completed" if completed.returncode == 0 else "failed"
        return {
            "stage": stage,
            "status": status,
            "returnCode": completed.returncode,
            "elapsedMs": round((time.perf_counter() - started) * 1000),
            "command": command,
            "stdoutTail": completed.stdout[-2000:],
            "stderrTail": completed.stderr[-2000:],
        }
    except Exception as exc:
        return {
            "stage": stage,
            "status": "failed",
            "returnCode": -1,
            "elapsedMs": round((time.perf_counter() - started) * 1000),
            "command": command,
            "stdoutTail": "",
            "stderrTail": f"{type(exc).__name__}: {exc}",
        }


def _skipped(stage: str, reason: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "status": "skipped",
        "returnCode": 0,
        "elapsedMs": 0,
        "command": [],
        "stdoutTail": "",
        "stderrTail": reason,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=REPO_ROOT / "demo-data")
    parser.add_argument("--evaluation-dir", type=Path, default=DEFAULT_EVALUATION_DIR)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--accept-baseline", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-frontend", action="store_true")
    args = parser.parse_args()

    input_root = args.input_root.resolve()
    evaluation_dir = args.evaluation_dir.resolve()
    baseline_path = args.baseline.resolve()
    output_path = args.output.resolve()
    evaluation_dir.mkdir(parents=True, exist_ok=True)

    commands: list[dict[str, Any]] = [
        _run_command(
            "parser_baseline",
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "evaluate_tender_baseline.py"),
                "--input",
                str(input_root),
                "--output-dir",
                str(evaluation_dir),
            ],
        ),
        _run_command(
            "decision_e2e",
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "evaluate_tender_decision_e2e.py"),
                "--input-root",
                str(input_root),
                "--output",
                str(evaluation_dir / "tender_decision_e2e_report.json"),
            ],
        ),
    ]
    if args.skip_tests:
        commands.append(_skipped("backend_tests", "--skip-tests"))
        commands.append(_skipped("python_compile", "--skip-tests"))
    else:
        commands.extend(
            [
                _run_command(
                    "backend_tests",
                    [sys.executable, "-m", "pytest", "-q", "backend/tests"],
                ),
                _run_command(
                    "python_compile",
                    [sys.executable, "-m", "compileall", "-q", "backend", "scripts"],
                ),
            ]
        )
    if args.skip_frontend:
        commands.append(_skipped("frontend_build", "--skip-frontend"))
    else:
        npm = "npm.cmd" if sys.platform.startswith("win") else "npm"
        commands.append(
            _run_command(
                "frontend_build",
                [npm, "run", "build"],
                cwd=REPO_ROOT / "frontend",
            )
        )

    parser_report = _read_json(evaluation_dir / "tender_baseline_report.json")
    decision_report = _read_json(evaluation_dir / "tender_decision_e2e_report.json")
    gold_report = _read_json(evaluation_dir / "tender_document_gold.json")
    current = build_regression_snapshot(
        parser_report,
        decision_report,
        gold_report=gold_report,
    )

    baseline = _read_json(baseline_path)
    baseline_updated = False
    if args.accept_baseline:
        command_failures = [item for item in commands if item["status"] == "failed"]
        if not command_failures:
            baseline = current
            _write_json(baseline_path, baseline)
            baseline_updated = True
            comparison = {
                "passed": True,
                "mode": "baseline_created",
                "checks": [],
                "failureCount": 0,
                "warningCount": 0,
            }
        else:
            comparison = {
                "passed": False,
                "mode": "baseline_not_updated",
                "checks": [],
                "failureCount": len(command_failures),
                "warningCount": 0,
            }
    elif baseline:
        comparison = compare_regression_snapshots(baseline, current)
    else:
        comparison = {
            "passed": False,
            "mode": "baseline_missing",
            "checks": [
                {
                    "name": "baseline_exists",
                    "status": "failed",
                    "actual": str(baseline_path),
                    "expected": "existing baseline; use --accept-baseline intentionally",
                    "detail": "首次建立或接受样本变化时必须显式更新基线。",
                }
            ],
            "failureCount": 1,
            "warningCount": 0,
        }

    command_failures = [item for item in commands if item["status"] == "failed"]
    report = {
        "schemaVersion": "tender-regression-report/v1",
        "generatedAt": date.today().isoformat(),
        "environment": {
            "requiredEnvironment": "project-venv",
            "pythonExecutable": sys.executable,
            "pythonVersion": sys.version.split()[0],
            "codeFingerprint": _fingerprint(),
        },
        "inputRoot": str(input_root),
        "baselinePath": str(baseline_path),
        "baselineUpdated": baseline_updated,
        "commands": commands,
        "commandFailureCount": len(command_failures),
        "current": current,
        "comparison": comparison,
    }
    _write_json(output_path, report)
    print(
        "tender regression: "
        f"passed={bool(comparison.get('passed')) and not command_failures} "
        f"commandFailures={len(command_failures)} "
        f"comparisonFailures={int(comparison.get('failureCount') or 0)} "
        f"report={output_path}"
    )
    return 0 if bool(comparison.get("passed")) and not command_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
