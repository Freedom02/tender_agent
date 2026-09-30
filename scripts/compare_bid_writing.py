# -*- coding: utf-8 -*-
"""标书模块回填对拍脚本（阶段 5 的第一版输入）。

两个职责：
  1. `--emit-sql`：生成 `verify_bid_writing.sql`——把每条要核对的断言写成
     `SELECT '<name>|' || <值>`，输出一行一条，便于 psql 抓取后逐条比对。
  2. 默认：读取 psql 的输出文件，与 expected.json 逐条比对，打印 PASS/FAIL。

为什么不让 Python 直连 PG：环境没装 psycopg/psycopg2/asyncpg（requirements.txt 里
也没有），而且沙箱下 Python 子进程调 docker 需要命名管道权限。所以走
「Python 生成 SQL → docker exec psql 执行 → Python 比对输出」三段式，
与既有迁移通道一致，零新依赖。

用法：
  python scripts/compare_bid_writing.py --out _backfill --emit-sql
  #   docker cp _backfill/verify_bid_writing.sql tenderagent-pg:/tmp/
  #   docker exec -i tenderagent-pg psql -U tender_app -d tender_agent -f /tmp/verify_bid_writing.sql > verify_out.txt
  python scripts/compare_bid_writing.py --out _backfill --psql-out _backfill/verify_out.txt
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re


def sql_lit(v) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def emit_sql(out_dir: str) -> str:
    path = os.path.join(out_dir, "verify_bid_writing.sql")
    lines = [
        "-- 由 scripts/compare_bid_writing.py --emit-sql 生成",
        "-- 每条断言输出 'name|value'，供 compare 步骤逐条比对",
        "\\pset tuples_only on",
        "\\pset format unaligned",
        "",
        "-- 行数核对",
        "SELECT 'cnt_tender_project|' || count(*) FROM tender_project;",
        "SELECT 'cnt_analysis_run|' || count(*) FROM analysis_run;",
        "SELECT 'cnt_workspace|' || count(*) FROM workspace;",
        "SELECT 'cnt_workspace_draft|' || count(*) FROM workspace_draft;",
        "SELECT 'cnt_bid_plan|' || count(*) FROM bid_plan;",
        "SELECT 'cnt_bid_document|' || count(*) FROM bid_document;",
        "SELECT 'cnt_bid_section|' || count(*) FROM bid_section;",
        "",
        "-- P 组必须仍是 0 行（无源数据）",
        "SELECT 'cnt_parse_bundle|' || count(*) FROM parse_bundle;",
        "SELECT 'cnt_evaluation_item|' || count(*) FROM evaluation_item;",
        "SELECT 'cnt_template_fragment|' || count(*) FROM template_fragment;",
        "SELECT 'cnt_parse_risk|' || count(*) FROM parse_risk;",
        "SELECT 'cnt_parse_review|' || count(*) FROM parse_review;",
        "SELECT 'cnt_agent_result|' || count(*) FROM agent_result;",
        "",
        "-- 关键字段回读（与 JSON 逐字比对）",
        "SELECT 'ws_title|' || biz_id || '|' || coalesce(title,'<null>') FROM workspace ORDER BY biz_id;",
        "SELECT 'draft_len|' || workspace_id || '|' || coalesce(length(draft),-1) FROM workspace_draft ORDER BY workspace_id;",
        "SELECT 'plan_len|' || workspace_id || '|' || jsonb_array_length(plan) FROM bid_plan ORDER BY workspace_id;",
        "SELECT 'doc_plan_len|' || biz_id || '|' || jsonb_array_length(plan) FROM bid_document ORDER BY biz_id;",
        "SELECT 'doc_dev_tech|' || biz_id || '|' || jsonb_array_length(coalesce(deviation_tables->'technical','[]'::jsonb)) FROM bid_document ORDER BY biz_id;",
        "SELECT 'doc_dev_comm|' || biz_id || '|' || jsonb_array_length(coalesce(deviation_tables->'commercial','[]'::jsonb)) FROM bid_document ORDER BY biz_id;",
        "SELECT 'doc_export|' || biz_id || '|' || coalesce(export_path,'<null>') FROM bid_document ORDER BY biz_id;",
        "SELECT 'sec|' || section_id || '|' || coalesce(kind,'<null>') || '|' || coalesce(status,'<null>') "
        "|| '|' || order_no || '|' || coalesce(length(content),-1) "
        "|| '|' || coalesce(array_length(requirement_ids,1),0) FROM bid_section ORDER BY order_no;",
        "",
        "-- 读方向证明：从 bid_document.plan 逐元素回读（含 note），核对元数据无损",
    ]
    exp_path = os.path.join(out_dir, "expected.json")
    expected = None
    if os.path.isfile(exp_path):
        with io.open(exp_path, encoding="utf-8") as f:
            expected = json.load(f)
        for ent in expected.get("bid_entities", []):
            doc_id = ent["doc_biz_id"]
            lines.append(
                "SELECT 'plansec|' || ord || '|' || coalesce(e->>'sectionId','') || '|' "
                "|| coalesce(e->>'title','') || '|' || coalesce(e->>'kind','') || '|' "
                "|| coalesce(e->>'note','') "
                f"FROM bid_document d, jsonb_array_elements(d.plan) WITH ORDINALITY AS t(e, ord) "
                f"WHERE d.biz_id = {sql_lit(doc_id)} ORDER BY ord;")
            lines.append(
                "SELECT 'secseq|' || order_no || '|' || section_id "
                f"FROM bid_section WHERE bid_document_id = {sql_lit(doc_id)} ORDER BY order_no;")
    lines.append("")
    lines.append("-- 正文逐字一致性（md5 对比：PG 里存的 content vs 源 .md）")
    if expected is not None:
        for ent in expected.get("bid_entities", []):
            doc_id = ent["doc_biz_id"]
            for sid in ent.get("section_ids", []):
                lines.append(
                    f"SELECT 'md5|{doc_id}:{sid}|' || coalesce(md5(content),'<null>') "
                    f"FROM bid_section WHERE bid_document_id = {sql_lit(doc_id)} "
                    f"AND section_id = {sql_lit(sid)};")
    text = "\n".join(lines) + "\n"
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def parse_psql_out(path: str) -> dict[str, list[str]]:
    """把 'name|value...' 行按 name 归组。"""
    got: dict[str, list[str]] = {}
    with io.open(path, encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            if not line or "|" not in line:
                continue
            name = line.split("|", 1)[0]
            if name.startswith("--") or name in {"BEGIN", "COMMIT", "SET"}:
                continue
            got.setdefault(name, []).append(line)
    return got


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="_backfill")
    ap.add_argument("--emit-sql", action="store_true")
    ap.add_argument("--psql-out", help="psql 执行 verify SQL 的输出文件")
    args = ap.parse_args()

    if args.emit_sql:
        p = emit_sql(args.out)
        print(f"生成 {p}")
        return 0

    if not args.psql_out:
        ap.error("需要 --emit-sql 或 --psql-out")

    with io.open(os.path.join(args.out, "expected.json"), encoding="utf-8") as f:
        expected = json.load(f)
    got = parse_psql_out(args.psql_out)

    checks: list[tuple[str, str, str]] = []  # (名称, 期望, 实际)

    def one(name: str, exp: str) -> None:
        vals = got.get(name, [])
        actual = vals[0].split("|", 1)[1] if vals else "<missing>"
        checks.append((name, exp, actual))

    one("cnt_tender_project", str(expected["n_projects"]))
    one("cnt_analysis_run", str(expected["n_runs"]))
    one("cnt_workspace", str(expected["n_workspaces"]))
    one("cnt_workspace_draft", str(expected["n_drafts"]))
    one("cnt_bid_plan", str(expected["n_bid_plans"]))
    one("cnt_bid_document", str(expected["n_bid_docs"]))
    one("cnt_bid_section", str(expected["n_bid_sections"]))
    for t in ("parse_bundle", "evaluation_item", "template_fragment",
              "parse_risk", "parse_review", "agent_result"):
        one(f"cnt_{t}", "0")

    # bid_document / bid_section 细节
    for ent in expected.get("bid_entities", []):
        doc_id = ent["doc_biz_id"]
        one_plan = got.get("doc_plan_len", [])
        for line in one_plan:
            parts = line.split("|")
            if len(parts) >= 3 and parts[1] == doc_id:
                checks.append((f"doc_plan_len[{doc_id}]", str(ent["plan_len"]), parts[2]))
        dt = (ent.get("deviations") or {})
        for key, name in (("technical", "doc_dev_tech"), ("commercial", "doc_dev_comm")):
            for line in got.get(name, []):
                parts = line.split("|")
                if len(parts) >= 3 and parts[1] == doc_id:
                    checks.append((f"{name}[{doc_id}]", str(len(dt.get(key) or [])), parts[2]))

        # 读方向证明 1：bid_document.plan 逐元素元数据无损（含 note）
        plan_elems = ent.get("plan_elements") or []
        seen_ord: set[int] = set()
        for line in got.get("plansec", []):
            parts = line.split("|")  # plansec|ord|sectionId|title|kind|note
            if len(parts) < 6:
                continue
            ord_no = int(parts[1])
            seen_ord.add(ord_no)
            exp_e = plan_elems[ord_no - 1] if 0 <= ord_no - 1 < len(plan_elems) else None
            exp = (f"{exp_e['sectionId']}|{exp_e['title']}|{exp_e['kind']}|{exp_e['note']}"
                   if exp_e else "<no-source>")
            checks.append((f"plansec[{doc_id}][ord={ord_no}]", exp, "|".join(parts[2:6])))
        checks.append((f"plansec_count[{doc_id}]", str(len(plan_elems)), str(len(seen_ord))))

        # 读方向证明 2：bid_section 按 order_no 的章节序列 = plan.json 的章节序列
        got_seq = []
        for line in got.get("secseq", []):
            parts = line.split("|")  # secseq|order_no|section_id
            if len(parts) >= 3:
                got_seq.append((int(parts[1]), parts[2]))
        got_seq.sort()
        exp_seq = [(i, e["sectionId"]) for i, e in enumerate(plan_elems) if e["sectionId"]]
        checks.append((f"secseq[{doc_id}]",
                       ";".join(f"{i}:{s}" for i, s in exp_seq),
                       ";".join(f"{i}:{s}" for i, s in got_seq)))

    # 章节正文 md5 逐字对拍
    import hashlib
    for ent in expected.get("bid_entities", []):
        doc_id = ent["doc_biz_id"]
        for sid in ent.get("section_ids", []):
            content = (ent.get("sections_md") or {}).get(sid)
            exp_md5 = hashlib.md5(content.encode("utf-8")).hexdigest() if content else "<null>"
            key = f"md5"
            actual = "<missing>"
            for line in got.get(key, []):
                parts = line.split("|")
                if len(parts) >= 3 and parts[1] == f"{doc_id}:{sid}":
                    actual = parts[2]
            checks.append((f"md5[{doc_id}:{sid}]", exp_md5, actual))

    rep = io.open(os.path.join(args.out, "compare_report.txt"), "w", encoding="utf-8", newline="\n")
    npass = nfail = 0
    rep.write("标书模块回填对拍报告（JSON 源 vs PostgreSQL 回读）\n")
    rep.write("=" * 64 + "\n")
    for name, exp, act in checks:
        ok = exp == act
        npass += ok
        nfail += (not ok)
        rep.write(f"[{'PASS' if ok else 'FAIL'}] {name}\n")
        if not ok:
            rep.write(f"        期望={exp!r}\n        实际={act!r}\n")
    rep.write("\n" + "=" * 64 + "\n")
    rep.write(f"合计 {len(checks)} 项：PASS {npass} / FAIL {nfail}\n")
    rep.close()
    print(f"对拍完成：{len(checks)} 项，PASS {npass} / FAIL {nfail} -> {args.out}/compare_report.txt")
    return 1 if nfail else 0


if __name__ == "__main__":
    raise SystemExit(main())
