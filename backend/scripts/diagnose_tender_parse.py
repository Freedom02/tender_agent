from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from backend.app.agent_runtime.parse_semantics import build_source_batches, hydrate_source_traces, validate_clause_batch
from backend.app.agent_runtime.clause_repair import apply_clause_repair, build_clause_repair_request
from backend.app.pi_agent.client import PiClient
from backend.app.pi_agent.prompts.parse_v2 import tender_clause_prompt, tender_clause_repair_prompt
from backend.app.pi_agent.resolved_parse import PiResolvedParseOrchestrator, _error_detail


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--mode", choices=("full", "clause", "repair"), default="full")
    parser.add_argument("--replay-nodes")
    parser.add_argument("--batch-id", default="batch-0013")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=1200)
    arguments = parser.parse_args()
    runs = json.loads((ROOT / "runtime" / "bid_runs.json").read_text(encoding="utf-8"))
    run = runs[arguments.run_id]
    source_path = ROOT / run["inputArtifact"]
    source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
    directory = ROOT / "runtime" / "parse-diagnostics" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    directory.mkdir(parents=True)
    client = PiClient()
    lock = threading.RLock()
    started = time.monotonic()

    def checkpoint(event: dict) -> None:
        with lock, (directory / "nodes.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")
        with lock:
            print(json.dumps({key: value for key, value in event.items()
                              if key not in {"agentResult", "attemptResult"}}, ensure_ascii=False), flush=True)

    print(json.dumps({"outputDirectory": str(directory), "mode": arguments.mode, "model": client.config.model,
                      "sourceSha256": source_hash, "workers": arguments.workers}, ensure_ascii=False), flush=True)
    try:
        if arguments.mode == "repair":
            if not arguments.replay_nodes:
                raise ValueError("repair mode requires --replay-nodes pointing to the recorded failed attempt")
            blocks = client._parse_file(source_path)
            batch = next(item for item in build_source_batches(blocks) if item["batchId"] == arguments.batch_id)
            events = [json.loads(line) for line in (ROOT / arguments.replay_nodes).read_text(encoding="utf-8").splitlines() if line.strip()]
            failed = next(event for event in events if event.get("agentId") == "TenderClauseAgent" and
                          event.get("label") == batch["batchId"] and event.get("status") == "attempt_failed" and
                          event.get("attempt") == 1 and isinstance(event.get("attemptResult", {}).get("output"), dict))
            raw = failed["attemptResult"]["output"]
            request = build_clause_repair_request(raw, batch, document_blocks=blocks, source_file={})
            if request is None:
                raise ValueError("recorded output needs full retry, not partial repair")
            correction = failed.get("error", "")
            (directory / "repair-request.json").write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
            checkpoint({"agentId": "TenderClauseAgent", "status": "repair_started", "targetCount": len(request["targets"]),
                        "sourceViewCount": len(request["views"]), "originalViewCount": len(batch["views"])})
            with client.request_deadline(started + arguments.timeout):
                patch = client.repair_tender_clauses(inputs=request, correction=correction)
            (directory / "repair-result.json").write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")
            merged = apply_clause_repair(raw, patch, request, batch, document_blocks=blocks, source_file={})
            (directory / "repaired-clause.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
            summary = {"status": "validated", "batchId": batch["batchId"], "requirements": len(merged["requirements"]),
                       "evidenceRequirements": len(merged["evidenceRequirements"]), "repairTargets": len(request["targets"]),
                       "repairViews": len(request["views"]), "originalViews": len(batch["views"]),
                       "originalOutputChars": len(json.dumps(raw, ensure_ascii=False)), "repairOutputChars": len(json.dumps(patch, ensure_ascii=False)),
                       "fullPromptChars": len(tender_clause_prompt(inputs=batch)),
                       "repairPromptChars": len(tender_clause_repair_prompt(inputs=request, correction=correction))}
        elif arguments.mode == "clause":
            blocks = client._parse_file(source_path)
            batch = next(item for item in build_source_batches(blocks) if item["batchId"] == arguments.batch_id)
            correction = ""
            repair_request = None
            for attempt in range(2):
                if repair_request is not None:
                    patch = client.repair_tender_clauses(inputs=repair_request, correction=correction)
                    (directory / "repair-result.json").write_text(json.dumps(patch, ensure_ascii=False, indent=2), encoding="utf-8")
                    raw = apply_clause_repair(raw, patch, repair_request, batch, document_blocks=blocks, source_file={}, validate=False)
                else:
                    raw = client.extract_tender_clauses(inputs=batch, correction=correction)
                (directory / f"clause-attempt-{attempt + 1}.json").write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
                try:
                    result = validate_clause_batch(hydrate_source_traces(raw, document_blocks=blocks, source_file={}, views=batch["views"]), batch)
                    summary = {"status": "validated", "batchId": batch["batchId"], "requirements": len(result.requirements),
                               "evidenceRequirements": len(result.evidenceRequirements), "attempts": attempt + 1}
                    break
                except Exception as exc:
                    correction = _error_detail(exc, contract=True)
                    repair_request = build_clause_repair_request(raw, batch, document_blocks=blocks, source_file={})
                    checkpoint({"agentId": "TenderClauseAgent", "status": "validation_failed", "error": correction})
                    if attempt == 1:
                        raise
        else:
            result = PiResolvedParseOrchestrator(client, max_workers=arguments.workers, timeout_seconds=arguments.timeout).parse_tender(
                source_path, workspace_id="", project_id="", project_context={"source": "diagnostic"}, node_callback=checkpoint,
            )
            (directory / "parse-result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            tender = result["resolvedTenderV2"]
            summary = {"status": result["parseReviewReport"]["status"], "coverage": result["parseReviewReport"]["coverage"],
                       "evaluationMethod": tender["evaluationMethod"], "requirements": len(tender["requirements"]),
                       "writingRequirements": len(result["requirements"]), "evidenceRequirements": len(tender["evidenceRequirements"]),
                       "templateFragments": len(tender["templateFragments"]), "responseTables": len(tender["responseTableSchemas"]),
                       "failedNodes": [item["runId"] for item in result["agentResults"] if item["status"] == "failed"],
                       "issueCounts": dict(Counter(item["code"] for item in result["parseReviewReport"]["issues"]))}
    except Exception as exc:
        summary = {"status": "failed", "error": _error_detail(exc)}
    summary["elapsedSeconds"] = round(time.monotonic() - started, 2)
    summary["sourceUnchanged"] = hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash
    (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 1 if summary["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
