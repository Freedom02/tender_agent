import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import ts from "typescript";

// 使用项目已有 TypeScript 编译器，无需增加前端测试依赖。
const source = readFileSync(new URL("./sectionJobs.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2020, module: ts.ModuleKind.ESNext }
});
const { sectionJobState, SECTION_JOB_BADGES, sectionAncestorIds, summarizeSectionJobs } =
  await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);

function section(sectionId, fields = {}) {
  return { sectionId, title: sectionId, status: "planned", source: "ai", kind: "technical", content: "", ...fields };
}

test("empty and group-only plans never report 100% completion or chapter todos", () => {
  const empty = summarizeSectionJobs([]);
  assert.equal(empty.summary.total, 0);
  assert.equal(empty.summary.percentage, 0);
  assert.deepEqual(empty.volumes, []);
  assert.deepEqual(empty.todos, []);
  const groups = summarizeSectionJobs([
    section("volume", { nodeType: "volume", status: "failed" }),
    section("group", { responseType: "section_group", parentSectionId: "volume" })
  ]);
  assert.equal(groups.summary.total, 0);
  assert.equal(groups.volumes[0].percentage, 0);
  assert.equal(groups.volumes[0].total, 0);
  assert.deepEqual(groups.todos, []);
});

test("chapter badges distinguish pending, generating, completed and retryable failure", () => {
  const samples = [
    ["planned", "待生成"], ["pending", "待生成"], ["generating", "生成中"],
    ["generated", "已完成"], ["completed", "已完成"], ["failed", "失败可重试"],
    ["error", "失败可重试"], ["needs_review", "待复核"], [" GENERATING ", "生成中"]
  ];
  for (const [status, label] of samples) {
    assert.equal(SECTION_JOB_BADGES[sectionJobState(section("chapter", { status }))].label, label);
  }
});

test("template text, placeholder content and derived origins do not imply completion", () => {
  const jobs = summarizeSectionJobs([
    section("template", { source: "copied_template", content: "招标原文", templateFragmentId: "t1" }),
    section("missing", { status: "missing", content: "【需人工填写：投标报价】" }),
    section("derived", { derived: true, derivedOrigin: "ai_initiative", content: "写作提示" }),
    section("failed", { status: "failed", content: "旧正文" }),
    section("review", { status: "needs_review", content: "待复核正文" })
  ]);
  assert.equal(jobs.summary.completed, 0);
  assert.equal(jobs.summary.pending, 3);
  assert.equal(jobs.summary.failed, 1);
  assert.equal(jobs.summary.review, 1);
  assert.deepEqual(jobs.todos.map((item) => item.section.sectionId), ["failed", "template", "missing", "derived", "review"]);
});

test("volume progress counts writable nested chapters once and handles unassigned chapters", () => {
  const jobs = summarizeSectionJobs([
    section("v1", { nodeType: "volume", volumeId: "business", title: "商务卷" }),
    section("v2", { nodeType: "volume", title: "技术卷" }),
    section("group", { responseType: "section_group", parentSectionId: "v1" }),
    section("done", { parentSectionId: "group", status: "generated" }),
    section("retry", { parentSectionId: "v1", status: "failed" }),
    section("running", { volumeId: "business", status: "generating" }),
    section("pending", { parentSectionId: "v2" }),
    section("orphan", { parentSectionId: "deleted-parent" })
  ]);
  assert.equal(jobs.summary.total, 5);
  assert.equal(jobs.summary.completed, 1);
  assert.equal(jobs.summary.percentage, 20);
  assert.deepEqual(jobs.volumes.map((volume) => [volume.title, volume.total, volume.completed, volume.percentage]), [
    ["商务卷", 3, 1, 33], ["技术卷", 1, 0, 0], ["未分卷章节", 1, 0, 0]
  ]);
  assert.deepEqual(jobs.todos.map((item) => [item.section.sectionId, item.volumeTitle]), [
    ["retry", "商务卷"], ["pending", "技术卷"], ["orphan", "未分卷章节"]
  ]);
});

test("legacy plans without volumes still get progress and preserve failure order", () => {
  const jobs = summarizeSectionJobs([
    section("first"), section("fail1", { status: "failed" }),
    section("done", { status: "generated" }), section("fail2", { status: "failed" })
  ]);
  assert.equal(jobs.volumes.length, 1);
  assert.equal(jobs.volumes[0].title, "全部章节");
  assert.equal(jobs.volumes[0].percentage, 25);
  assert.deepEqual(jobs.todos.map((item) => item.section.sectionId), ["fail1", "fail2", "first"]);
});

test("in-progress and completed chapters stay out of retry todos across status transitions", () => {
  const plan = [section("chapter", { status: "generating" })];
  let jobs = summarizeSectionJobs(plan);
  assert.equal(jobs.summary.generating, 1);
  assert.equal(jobs.summary.completed, 0);
  assert.equal(jobs.todos.length, 0);
  plan[0].status = "failed";
  jobs = summarizeSectionJobs(plan);
  assert.equal(jobs.summary.generating, 0);
  assert.equal(jobs.summary.failed, 1);
  assert.equal(jobs.todos[0].section.sectionId, "chapter");
  plan[0].status = "generated";
  jobs = summarizeSectionJobs(plan);
  assert.equal(jobs.summary.percentage, 100);
  assert.equal(jobs.todos.length, 0);
});

test("jump ancestor lookup opens all parents and tolerates broken or cyclic hierarchy", () => {
  const plan = [
    section("v", { nodeType: "volume" }), section("g", { parentSectionId: "v", responseType: "section_group" }),
    section("chapter", { parentSectionId: "g" }), section("orphan", { parentSectionId: "absent" }),
    section("a", { parentSectionId: "b" }), section("b", { parentSectionId: "a" })
  ];
  assert.deepEqual(sectionAncestorIds(plan, "chapter"), ["g", "v"]);
  assert.deepEqual(sectionAncestorIds(plan, "orphan"), []);
  assert.deepEqual(sectionAncestorIds(plan, "a"), ["b"]);
  assert.deepEqual(sectionAncestorIds(plan, "absent"), []);
  assert.equal(summarizeSectionJobs(plan).summary.total, 4);
});

test("summaries are read-only and unknown statuses cannot count as complete", () => {
  const plan = [section("chapter", { status: "unknown", derived: true, derivedBasis: "招标要求" })];
  const original = structuredClone(plan);
  const jobs = summarizeSectionJobs(plan);
  assert.equal(jobs.summary.completed, 0);
  assert.equal(jobs.summary.pending, 1);
  assert.deepEqual(plan, original);
});

test("near-complete volumes never display 100% while a failed chapter remains", () => {
  const plan = Array.from({ length: 199 }, (_, index) => section(`done-${index}`, { status: "generated" }));
  plan.push(section("failed", { status: "failed" }));
  const jobs = summarizeSectionJobs(plan);
  assert.equal(jobs.summary.percentage, 99);
  assert.equal(jobs.volumes[0].percentage, 99);
  assert.equal(jobs.todos.length, 1);
});
