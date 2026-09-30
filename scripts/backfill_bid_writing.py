# -*- coding: utf-8 -*-
"""标书模块（G 组）独立回填脚本 —— 从 runtime 真实 JSON 生成幂等 SQL。

目标：把「有地存」从 schema 图纸变成实测——用真实数据灌 006 的表，暴露
      类型/约束/拓扑问题，产出阶段 5 对拍的第一版输入。

设计约束（重要）：
  * 只读 JSON、只产出 SQL，**不 import store.py、不连 PG**（环境无 psycopg，
    且另一对话正在改 store.py）。SQL 通过既有通道 `docker exec -i tenderagent-pg
    psql -f ...` 应用，见 README 说明与 compare_bid_writing.py。
  * 幂等：能 UPSERT 的表用 `ON CONFLICT (自然键) DO UPDATE`；`bid_plan` 没有
    唯一键（001 的已知缺陷），用「按 workspace_id DELETE 再 INSERT」兜底。
  * 拓扑顺序满足 FK：tender_project → analysis_run → workspace → workspace_draft
    → bid_plan → bid_document → bid_section。

数据事实（2026-09 交接时点，已在 _verify006/data_inventory.txt 坐实）：
  * workspaces.json.bak 有 18 个 workspace，**全部 bidDocument/bidPlan = null**。
    标书正文不落 workspaces.json，落 runtime/bid_writing/{workspaceId}/ 自有目录：
        plan.json         章节树元数据（sectionId/title/kind/requirementIds/note）
        sections/*.md     章节正文（按 sectionId 命名）
        deviations.json   偏离表 {technical:[], commercial:[]}
        output/*.docx     导出产物
  * projects/*/parse/parse-bundle.json **不存在**（全盘 0 个）→ P 组 6 表无源，
    本脚本回填 0 行并记录原因（阶段 3 双写接线后才有数据）。

用法：
  python scripts/backfill_bid_writing.py --runtime <runtime-backup目录> --out <输出目录>
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os
import sys
from pathlib import Path


# ---------------------------------------------------------------- 工具


def sql_lit(v) -> str:
    """把 Python 值变成 SQL 字面量（NULL / 字符串 / 数字 / 布尔）。"""
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    raise TypeError(f"sql_lit 不支持 {type(v).__name__}")


def sql_json(v) -> str:
    """把 Python 值变成 jsonb 字面量（单引号字符串内是 JSON）。"""
    return sql_lit(json.dumps(v, ensure_ascii=False, separators=(",", ":")))


def sql_text_array(v) -> str:
    """text[] 字面量。

    注意：本脚本写入的 text[] 列（analysis_run.input/output_artifact_paths、
    warnings/errors；bid_section.requirement_ids/material_scopes/evaluation_ids）
    在 001/006 里都是 `NOT NULL DEFAULT '{}'`——语义是「没有条目」= 空数组，
    不是 NULL。真实 plan.json 里 d-01/d-02 这类偏离章节**没有 requirementIds 键**，
    实测会触发 `null value in column "requirement_ids" violates not-null constraint`，
    故 None 一律映射成空数组（这是本次回填实测暴露的真实问题）。
    """
    if v is None:
        return "'{}'::text[]"
    if not isinstance(v, list):
        v = [v]
    return "ARRAY[" + ",".join(sql_lit(str(x)) for x in v) + "]::text[]"


def upsert(table: str, cols: list[str], row: dict, conflict_cols: list[str],
           update_cols: list[str]) -> str:
    """生成一条 INSERT ... ON CONFLICT DO UPDATE。"""
    vals = ", ".join(row[c] for c in cols)
    conflict = ", ".join(conflict_cols)
    upd = ", ".join(f"{c} = EXCLUDED.{c}" for c in update_cols)
    return (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({vals}) "
            f"ON CONFLICT ({conflict}) DO UPDATE SET {upd};")


# ---------------------------------------------------------------- 装载源数据


def load_workspaces(runtime: str) -> dict:
    p = os.path.join(runtime, "workspaces.json.bak")
    if not os.path.isfile(p):
        p = os.path.join(runtime, "workspaces.json")
    if not os.path.isfile(p):
        sys.exit(f"找不到 workspaces.json(.bak)：{runtime}")
    with io.open(p, encoding="utf-8") as f:
        return json.load(f)


def load_bid_writing(runtime: str) -> dict[str, dict]:
    """{workspaceId: {plan, deviations, sections: {sectionId: content}, output: path}}"""
    root = os.path.join(runtime, "bid_writing")
    out: dict[str, dict] = {}
    if not os.path.isdir(root):
        return out
    for wid in os.listdir(root):
        d = os.path.join(root, wid)
        if not os.path.isdir(d):
            continue
        rec: dict = {"plan": None, "deviations": None, "sections": {}, "output": None}
        pp = os.path.join(d, "plan.json")
        if os.path.isfile(pp):
            with io.open(pp, encoding="utf-8") as f:
                plan = json.load(f)
            if isinstance(plan, dict):
                plan = plan.get("sections") or plan.get("plan") or []
            rec["plan"] = plan if isinstance(plan, list) else None
        dp = os.path.join(d, "deviations.json")
        if os.path.isfile(dp):
            with io.open(dp, encoding="utf-8") as f:
                rec["deviations"] = json.load(f)
        sd = os.path.join(d, "sections")
        if os.path.isdir(sd):
            for fn in os.listdir(sd):
                if fn.endswith(".md"):
                    with io.open(os.path.join(sd, fn), encoding="utf-8") as f:
                        rec["sections"][fn[:-3]] = f.read()
        od = os.path.join(d, "output")
        if os.path.isdir(od):
            docs = glob.glob(os.path.join(od, "*.docx"))
            if docs:
                rec["output"] = os.path.relpath(docs[0], runtime).replace("\\", "/")
        out[wid] = rec
    return out


def load_runs(runtime: str) -> dict[str, dict]:
    """{runId: run对象}，从 projects/*/runs/run-*.json。"""
    out: dict[str, dict] = {}
    for fp in glob.glob(os.path.join(runtime, "projects", "*", "runs", "run-*.json")):
        try:
            with io.open(fp, encoding="utf-8") as f:
                run = json.load(f)
        except Exception:
            continue
        if isinstance(run, dict) and run.get("runId"):
            out[run["runId"]] = run
    return out


# ---------------------------------------------------------------- 行构造


def build_tender_projects(workspaces: dict, runs: dict) -> list[dict]:
    """project 对象（18 个）→ tender_project 行；run 引用了但无 project 对象的补 stub。"""
    rows: dict[str, dict] = {}
    for wid, w in workspaces.items():
        prj = w.get("project") if isinstance(w.get("project"), dict) else {}
        pid = prj.get("id") or w.get("projectId")
        if not pid:
            continue
        rows[pid] = {
            "biz_id": sql_lit(pid),
            "project_name": sql_lit(prj.get("title")),
            "project_type": sql_lit(prj.get("category")),
            "region": sql_lit(prj.get("region")),
            "project_status": sql_lit(prj.get("status")),
        }
    for run in runs.values():
        pid = run.get("projectId")
        if pid and pid not in rows:
            rows[pid] = {  # stub：run 有、但没有 workspace 的 project（历史数据缺口）
                "biz_id": sql_lit(pid),
                "project_name": sql_lit(pid),
                "project_type": sql_lit(None),
                "region": sql_lit(None),
                "project_status": sql_lit(None),
            }
    return list(rows.values())


def build_analysis_runs(runs: dict) -> list[dict]:
    rows = []
    for rid, r in runs.items():
        rows.append({
            "biz_id": sql_lit(rid),
            "project_id": sql_lit(r.get("projectId")),
            "document_version_id": sql_lit(r.get("documentVersionId")),
            "stage": sql_lit(r.get("stage")),
            "status": sql_lit(r.get("status")),
            "created_at": sql_lit(r.get("createdAt")),
            "started_at": sql_lit(r.get("startedAt")),
            "finished_at": sql_lit(r.get("finishedAt")),
            "elapsed_ms": sql_lit(r.get("elapsedMs")),
            "parser_name": sql_lit(r.get("parserName")),
            "parser_version": sql_lit(r.get("parserVersion")),
            "schema_version": sql_lit(r.get("schemaVersion")),
            "prompt_version": sql_lit(r.get("promptVersion")),
            "llm_model": sql_lit(r.get("llmModel")),
            "llm_parameters": sql_json(r.get("llmParameters") or {}),
            "llm_call_count": sql_lit(r.get("llmCallCount") or 0),
            "llm_success_count": sql_lit(r.get("llmSuccessCount") or 0),
            "llm_failure_count": sql_lit(r.get("llmFailureCount") or 0),
            "llm_input_tokens": sql_lit(r.get("llmInputTokens") or 0),
            "llm_output_tokens": sql_lit(r.get("llmOutputTokens") or 0),
            "input_artifact_paths": sql_text_array(r.get("inputArtifactPaths")),
            "output_artifact_paths": sql_text_array(r.get("outputArtifactPaths")),
            "metrics": sql_json(r.get("metrics") or {}),
            "warnings": sql_text_array(r.get("warnings")),
            "errors": sql_text_array(r.get("errors")),
        })
    return rows


def build_workspaces(workspaces: dict, runs: dict) -> tuple[list[dict], list[str]]:
    rows = []
    missing_run = []
    for wid, w in workspaces.items():
        prj = w.get("project") if isinstance(w.get("project"), dict) else {}
        run_id = w.get("analysisRunId")
        if run_id and run_id not in runs:
            missing_run.append(f"{wid} -> {run_id}")
            run_id = None  # 缺 run 文件，不臆造，置 NULL（FK 允许空）
        rows.append({
            "biz_id": sql_lit(w.get("workspaceId") or wid),
            "project_id": sql_lit(prj.get("id") or w.get("projectId")),
            "title": sql_lit(w.get("title")),
            "analysis_run_id": sql_lit(run_id),
        })
    return rows, missing_run


def build_drafts(workspaces: dict) -> list[dict]:
    rows = []
    for wid, w in workspaces.items():
        rows.append({
            "workspace_id": sql_lit(w.get("workspaceId") or wid),
            "draft_version": sql_lit(w.get("draftVersion") or 1),
            "draft": sql_lit(w.get("draft")),
        })
    return rows


def _derive_status(section: dict, content: str | None) -> str:
    if str(section.get("kind") or "") == "deviation":
        return "generated"  # 偏离表正文由 deviations.json 渲染，不计缺失
    return "generated" if content else "missing"


def _derive_source(section: dict) -> str | None:
    if str(section.get("kind") or "") == "deviation":
        return "table"
    note = str(section.get("note") or "")
    if "模板" in note or "固定文书" in note:
        return "template"
    return "ai"


def build_bid_entities(bw: dict[str, dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """返回 (bid_plan 行, bid_document 行, bid_section 行)。"""
    plans, docs, sections = [], [], []
    for wid, rec in sorted(bw.items()):
        plan = rec.get("plan")
        if not isinstance(plan, list):
            continue
        plans.append({"workspace_id": sql_lit(wid), "plan": sql_json(plan)})

        doc_id = f"bid-{wid}"
        has_missing = any(
            _derive_status(s, rec["sections"].get(str(s.get("sectionId") or "")))
            == "missing" for s in plan)
        docs.append({
            "biz_id": sql_lit(doc_id),
            "workspace_id": sql_lit(wid),
            "status": sql_lit("missing" if has_missing else "generated"),
            "mode": sql_lit(None),          # 导出模式不落盘，无源
            "export_path": sql_lit(rec.get("output")),
            "version": sql_lit(1),
            "plan": sql_json(plan),         # 元数据（见 006 文件头结构决策）
            "deviation_tables": sql_json(rec.get("deviations")
                                         or {"technical": [], "commercial": []}),
        })

        for i, s in enumerate(plan):
            sid = str(s.get("sectionId") or "").strip()
            if not sid:
                continue
            content = rec["sections"].get(sid)
            sections.append({
                "biz_id": sql_lit(f"{doc_id}:{sid}"),
                "bid_document_id": sql_lit(doc_id),
                "section_id": sql_lit(sid),
                "title": sql_lit(s.get("title")),
                "content": sql_lit(content),
                "version": sql_lit(1),
                "kind": sql_lit(s.get("kind")),
                "status": sql_lit(_derive_status(s, content)),
                "order_no": sql_lit(i),
                "requirement_ids": sql_text_array(s.get("requirementIds")),
                "source": sql_lit(_derive_source(s)),
            })
    return plans, docs, sections


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtime", required=True, help="runtime 目录（含 workspaces.json(.bak)/bid_writing/projects）")
    ap.add_argument("--out", default="_backfill", help="输出目录")
    args = ap.parse_args()

    workspaces = load_workspaces(args.runtime)
    bw = load_bid_writing(args.runtime)
    runs = load_runs(args.runtime)

    projects = build_tender_projects(workspaces, runs)
    runs_rows = build_analysis_runs(runs)
    ws_rows, missing_run = build_workspaces(workspaces, runs)
    draft_rows = build_drafts(workspaces)
    plan_rows, doc_rows, sec_rows = build_bid_entities(bw)

    os.makedirs(args.out, exist_ok=True)
    sql = io.open(os.path.join(args.out, "backfill_bid_writing.sql"), "w", encoding="utf-8", newline="\n")
    sql.write("-- 由 scripts/backfill_bid_writing.py 生成；幂等，可重复执行。\n")
    sql.write("-- 拓扑顺序满足 FK：tender_project → analysis_run → workspace →\n")
    sql.write("--   workspace_draft → bid_plan → bid_document → bid_section\n\n")
    sql.write("BEGIN;\n\n")

    # 1. tender_project
    sql.write("-- (1) tender_project\n")
    for r in projects:
        sql.write(upsert("tender_project", ["biz_id", "project_name", "project_type", "region", "project_status"],
                         r, ["biz_id"], ["project_name", "project_type", "region", "project_status"]) + "\n")

    # 2. analysis_run
    sql.write("\n-- (2) analysis_run\n")
    for r in runs_rows:
        cols = ["biz_id", "project_id", "document_version_id", "stage", "status",
                "created_at", "started_at", "finished_at", "elapsed_ms",
                "parser_name", "parser_version", "schema_version", "prompt_version",
                "llm_model", "llm_parameters", "llm_call_count", "llm_success_count",
                "llm_failure_count", "llm_input_tokens", "llm_output_tokens",
                "input_artifact_paths", "output_artifact_paths", "metrics", "warnings", "errors"]
        sql.write(upsert("analysis_run", cols, r, ["biz_id"], cols[1:]) + "\n")

    # 3. workspace
    sql.write("\n-- (3) workspace\n")
    for r in ws_rows:
        sql.write(upsert("workspace", ["biz_id", "project_id", "title", "analysis_run_id"],
                         r, ["biz_id"], ["project_id", "title", "analysis_run_id"]) + "\n")

    # 4. workspace_draft
    sql.write("\n-- (4) workspace_draft\n")
    for r in draft_rows:
        sql.write(upsert("workspace_draft", ["workspace_id", "draft_version", "draft"],
                         r, ["workspace_id", "draft_version"], ["draft"]) + "\n")

    # 5. bid_plan（无唯一键 → 按 workspace_id 先删后插）
    sql.write("\n-- (5) bid_plan（无唯一键，DELETE+INSERT 兜底）\n")
    for r in plan_rows:
        sql.write(f"DELETE FROM bid_plan WHERE workspace_id = {r['workspace_id']};\n")
        sql.write(f"INSERT INTO bid_plan (workspace_id, plan) VALUES ({r['workspace_id']}, {r['plan']});\n")

    # 6. bid_document
    sql.write("\n-- (6) bid_document\n")
    for r in doc_rows:
        cols = ["biz_id", "workspace_id", "status", "mode", "export_path", "version",
                "plan", "deviation_tables"]
        sql.write(upsert("bid_document", cols, r, ["biz_id"], cols[1:]) + "\n")

    # 7. bid_section
    sql.write("\n-- (7) bid_section\n")
    for r in sec_rows:
        cols = ["biz_id", "bid_document_id", "section_id", "title", "content", "version",
                "kind", "status", "order_no", "requirement_ids", "source"]
        sql.write(upsert("bid_section", cols, r, ["bid_document_id", "section_id", "version"],
                         cols[3:]) + "\n")

    sql.write("\nCOMMIT;\n")
    sql.close()

    # expected.json（供 compare_bid_writing.py 对拍）
    def doc_expected():
        out = []
        for wid, rec in sorted(bw.items()):
            plan = rec.get("plan")
            if not isinstance(plan, list):
                continue
            out.append({
                "workspace_id": wid,
                "doc_biz_id": f"bid-{wid}",
                "plan_len": len(plan),
                "section_ids": [str(s.get("sectionId") or "") for s in plan if str(s.get("sectionId") or "")],
                # 读方向证明用：plan.json 每个元素的元数据（sectionId/title/kind/note）
                "plan_elements": [
                    {
                        "sectionId": str(s.get("sectionId") or ""),
                        "title": str(s.get("title") or ""),
                        "kind": str(s.get("kind") or ""),
                        "note": str(s.get("note") or ""),
                    }
                    for s in plan if isinstance(s, dict)
                ],
                "deviations": rec.get("deviations"),
                "sections_md": {sid: rec["sections"].get(sid) for sid in
                                [str(s.get("sectionId") or "") for s in plan]},
            })
        return out

    expected = {
        "n_workspaces": len(workspaces),
        "n_projects": len(projects),
        "n_runs": len(runs_rows),
        "n_drafts": len(draft_rows),
        "n_bid_plans": len(plan_rows),
        "n_bid_docs": len(doc_rows),
        "n_bid_sections": len(sec_rows),
        "bid_entities": doc_expected(),
    }
    with io.open(os.path.join(args.out, "expected.json"), "w", encoding="utf-8") as f:
        json.dump(expected, f, ensure_ascii=False, indent=2)

    # 报告
    rep = io.open(os.path.join(args.out, "backfill_report.txt"), "w", encoding="utf-8", newline="\n")
    rep.write("标书模块（G 组）回填报告\n")
    rep.write("=" * 50 + "\n")
    rep.write(f"tender_project    {len(projects):>5} 行\n")
    rep.write(f"analysis_run      {len(runs_rows):>5} 行（projects/*/runs/run-*.json）\n")
    rep.write(f"workspace         {len(ws_rows):>5} 行\n")
    rep.write(f"workspace_draft   {len(draft_rows):>5} 行\n")
    rep.write(f"bid_plan          {len(plan_rows):>5} 行（bid_writing/*/plan.json）\n")
    rep.write(f"bid_document      {len(doc_rows):>5} 行（合成 biz_id=bid-<workspaceId>）\n")
    rep.write(f"bid_section       {len(sec_rows):>5} 行（plan.json 章节 × sections/*.md 正文）\n")
    rep.write("\n")
    rep.write("数据缺口（诚实记录，不臆造）：\n")
    rep.write(f"  workspace.analysis_run_id 缺 run 文件、已置 NULL 的 workspace：{len(missing_run)} 个\n")
    for m in missing_run:
        rep.write(f"    {m}\n")
    rep.write("  （run-*.json 只存下 17 个，且都不在 18 个 workspace 引用的 analysisRunId 里；\n")
    rep.write("   备份不完整或 run 落盘位置另有约定，阶段 4 正式回填前需确认。）\n")
    rep.write("\n")
    rep.write("P 组（parse_bundle/evaluation_item/template_fragment/parse_risk/parse_review/agent_result）：\n")
    rep.write("  回填 0 行 —— 全盘无 projects/*/parse/parse-bundle.json（ParseBundle 运行时构建、不落盘）。\n")
    rep.write("  这些表要等阶段 3「双写」接线后才有数据；本脚本不臆造。\n")
    rep.write("\n")
    rep.write("已知类型风险（本数据未覆盖，仍需留意）：\n")
    rep.write("  * bid_section.evaluation_score 为 numeric，真实 JSON 若出现 '5分' 类字符串会插不进去；\n")
    rep.write("    当前 plan.json 无评分字段，此风险未被本次回填触发。\n")
    rep.write("  * bid_document.generated_at 为 text（契约初值空串）；无源，本次置 NULL。\n")
    rep.write("\n")
    rep.write("无源字段（本次置 NULL / 省略，非缺陷）：\n")
    rep.write("  bid_document.mode（导出模式不落盘）、submission_structure/template_fragments/\n")
    rep.write("  evaluation_plan 等（依赖 ParseBundle，而 ParseBundle 不落盘）。\n")
    rep.close()

    print(f"生成 {args.out}/backfill_bid_writing.sql")
    print(f"生成 {args.out}/expected.json")
    print(f"生成 {args.out}/backfill_report.txt")
    print(f"  计数: projects={len(projects)} runs={len(runs_rows)} workspaces={len(ws_rows)} "
          f"drafts={len(draft_rows)} plans={len(plan_rows)} docs={len(doc_rows)} sections={len(sec_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
