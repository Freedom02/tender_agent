/**
 * 条款映射智能体（RequirementMapAgent）的只读工具集。
 *
 * 数据来源：环境变量 `BID_MAP_CONTEXT` 指向的一份 JSON 快照，由后端
 * `backend/app/pi_agent/agent_context/requirement_map_context.py` 在每次调用 Pi 前生成。
 * 工具**只读、无副作用**：不写盘、不联网、不接受任意文件路径。
 *
 * 启动方式（由 `backend/app/pi_agent/transport.py` 组装）：
 *   pi --no-builtin-tools --tools list_chapters,search_requirements,get_requirement_batch,validate_coverage_candidate \
 *      -e <本文件>
 *
 * 为什么不给 read/bash/write/edit：招标文件正文属于不可信输入，一旦被提示词注入，
 * 通用文件工具会让模型读到本机任意文件（含 agent-config.json 里的 API Key）；
 * 这里的工具只能读后端注入的那一份快照，且快照里没有任何密钥。
 */
import { readFileSync } from "node:fs";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

type Requirement = {
  id: string;
  requirementNumber: string;
  domain: string;
  mandatory: boolean;
  isRejection: boolean;
  responseType: string;
  importance: string;
  sourceLocator: string;
  text: string;
  rawText: string;
  inBatch: boolean;
};

type Chapter = {
  sectionId: string;
  title: string;
  parentSectionId: string;
  level: number;
  order: number;
  nodeType: string;
  responseType: string;
  kind: string;
  source: string;
  required: boolean;
  derived: boolean;
  requirementIds: string[];
  evaluationIds: string[];
};

type EvaluationItem = {
  id: string;
  factor: string;
  item: string;
  criteria: string;
  score: unknown;
};

type MapContext = {
  workspaceId: string;
  projectId: string;
  policy: { name: string; enabled: boolean };
  summary: {
    batchRequirementCount: number;
    chapterCount: number;
    evaluationItemCount: number;
    batchRequirementIds: string[];
    mandatoryIds: string[];
    rejectionIds: string[];
  };
  chapters: Chapter[];
  requirements: Requirement[];
  evaluationItems: EvaluationItem[];
  warnings: string[];
};

/** 与提示词里的映射规则一致：不合法的取值会被编排层改写，这里提前拦下。 */
const COVERAGE_TYPES = ["chapter_text", "table", "attachment", "commitment", "fixed_form", "other"];
const STATUSES = ["planned", "covered", "uncovered", "needs_review"];

let cached: MapContext | null = null;
let cachedError: string | null = null;

function loadContext(): { context: MapContext | null; error: string | null } {
  if (cached) return { context: cached, error: null };
  if (cachedError) return { context: null, error: cachedError };
  const path = process.env.BID_MAP_CONTEXT ?? "";
  if (!path) {
    cachedError = "未注入 BID_MAP_CONTEXT，工具数据不可用；请直接依据提示词中的待办清单作答。";
    return { context: null, error: cachedError };
  }
  try {
    cached = JSON.parse(readFileSync(path, "utf-8")) as MapContext;
    return { context: cached, error: null };
  } catch (error) {
    cachedError = `读取工具数据失败：${String(error)}`;
    return { context: null, error: cachedError };
  }
}

function textResult(payload: unknown): { content: Array<{ type: "text"; text: string }>; details: Record<string, unknown> } {
  return {
    content: [{ type: "text", text: typeof payload === "string" ? payload : JSON.stringify(payload, null, 1) }],
    details: {},
  };
}

function errorResult(message: string): {
  content: Array<{ type: "text"; text: string }>;
  details: Record<string, unknown>;
  isError: boolean;
} {
  return { ...textResult({ error: message }), isError: true };
}

/** 中文场景的朴素检索：按非汉字分隔切词 + 汉字二元组，命中越多分越高。 */
function scoreText(query: string, text: string): number {
  const normalized = query.trim().toLowerCase();
  if (!normalized) return 0;
  const tokens = new Set<string>();
  for (const chunk of normalized.split(/[\s,，、;；/|()（）:：]+/)) {
    if (!chunk) continue;
    if (/^[\u4e00-\u9fa5]+$/.test(chunk) && chunk.length > 2) {
      for (let i = 0; i + 2 <= chunk.length; i += 1) tokens.add(chunk.slice(i, i + 2));
    } else {
      tokens.add(chunk);
    }
  }
  const haystack = text.toLowerCase();
  let score = 0;
  for (const token of tokens) {
    if (haystack.includes(token)) score += token.length >= 2 ? token.length : 1;
  }
  return score;
}

function batchRequirements(context: MapContext): Requirement[] {
  return context.requirements.filter((item) => item.inBatch);
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "list_chapters",
    label: "列出可引用章节",
    description:
      "列出 OutlinePlanAgent 生成的目录节点：sectionId、标题、父节点、层级、responseType、kind、已有 requirementIds/evaluationIds。草拟 coverage 前先看它，sectionId 只能取自这里。",
    parameters: Type.Object({
      query: Type.Optional(Type.String({ description: "按标题关键词过滤，例如“技术方案”“售后”" })),
      parentSectionId: Type.Optional(Type.String({ description: "只看某个父节点下的子章节" })),
      level: Type.Optional(Type.Number({ description: "只看某一层级：1=卷，2/3=章节" })),
      limit: Type.Optional(Type.Number({ description: "返回条数上限，默认 60，最大 200" })),
    }),
    async execute(_toolCallId: any, params: any) {
      const { context, error } = loadContext();
      if (!context) return errorResult(error ?? "工具数据不可用");
      const query = String(params?.query ?? "").trim();
      const parentId = String(params?.parentSectionId ?? "").trim();
      const level = Number(params?.level ?? 0) || 0;
      const limit = Math.min(Math.max(Number(params?.limit ?? 60) || 60, 1), 200);
      const filtered = context.chapters
        .filter((chapter) => (parentId ? chapter.parentSectionId === parentId : true))
        .filter((chapter) => (level ? chapter.level === level : true))
        .filter((chapter) => (query ? scoreText(query, `${chapter.title} ${chapter.sectionId}`) > 0 : true));
      return textResult({
        totalChapters: context.chapters.length,
        matched: filtered.length,
        returned: Math.min(filtered.length, limit),
        chapters: filtered.slice(0, limit),
        hint: "coverage.sectionId 只能引用上面出现过的 sectionId。",
      });
    },
  });

  pi.registerTool({
    name: "search_requirements",
    label: "查条款原文",
    description:
      "按条款编号或关键词取本批条款的原文（rawText）。判断某条要求应由哪个章节响应时用它读原文；只需清单和摘要时用 get_requirement_batch 更省。",
    parameters: Type.Object({
      query: Type.String({ description: "条款编号或关键词，例如“technical-0007”“质保 售后 培训”" }),
      mandatoryOnly: Type.Optional(Type.Boolean({ description: "只看强制条款" })),
      limit: Type.Optional(Type.Number({ description: "返回条数上限，默认 8，最大 20" })),
    }),
    async execute(_toolCallId: any, params: any) {
      const { context, error } = loadContext();
      if (!context) return errorResult(error ?? "工具数据不可用");
      const query = String(params?.query ?? "");
      const mandatoryOnly = Boolean(params?.mandatoryOnly);
      const limit = Math.min(Math.max(Number(params?.limit ?? 8) || 8, 1), 20);
      const normalized = query.trim().toLowerCase();
      const pool = batchRequirements(context).filter((item) => (mandatoryOnly ? item.mandatory : true));
      const exact = pool.filter(
        (item) =>
          normalized.length > 0 &&
          (item.id.toLowerCase() === normalized ||
            item.requirementNumber.toLowerCase() === normalized),
      );
      const scored = pool
        .map((item) => ({
          item,
          score: scoreText(query, `${item.text} ${item.rawText} ${item.requirementNumber} ${item.id}`),
        }))
        .filter((row) => row.score > 0)
        .sort((left, right) => right.score - left.score)
        .map((row) => row.item);
      const merged: Requirement[] = [];
      const seen = new Set<string>();
      for (const item of [...exact, ...scored]) {
        if (seen.has(item.id)) continue;
        seen.add(item.id);
        merged.push(item);
      }
      return textResult({
        query,
        matched: merged.length,
        searched: pool.length,
        matches: merged.slice(0, limit).map((item) => ({
          id: item.id,
          requirementNumber: item.requirementNumber,
          domain: item.domain,
          mandatory: item.mandatory,
          isRejection: item.isRejection,
          responseType: item.responseType,
          sourceLocator: item.sourceLocator,
          text: item.text,
          rawText: item.rawText,
        })),
      });
    },
  });

  pi.registerTool({
    name: "get_requirement_batch",
    label: "读取本批条款",
    description:
      "取本次调用负责的本批条款清单（id、编号、领域、是否强制/废标、响应类型、摘要）。逐条映射前先看它，确认本批有哪些条款；需要原文再用 search_requirements。",
    parameters: Type.Object({
      offset: Type.Optional(Type.Number({ description: "起始下标，默认 0" })),
      limit: Type.Optional(Type.Number({ description: "返回条数上限，默认 40，最大 80" })),
      mandatoryOnly: Type.Optional(Type.Boolean({ description: "只看强制条款" })),
    }),
    async execute(_toolCallId: any, params: any) {
      const { context, error } = loadContext();
      if (!context) return errorResult(error ?? "工具数据不可用");
      const pool = batchRequirements(context).filter((item) =>
        params?.mandatoryOnly ? item.mandatory : true,
      );
      const offset = Math.max(Number(params?.offset ?? 0) || 0, 0);
      const limit = Math.min(Math.max(Number(params?.limit ?? 40) || 40, 1), 80);
      return textResult({
        total: pool.length,
        offset,
        limit,
        hasMore: offset + limit < pool.length,
        mandatoryCount: batchRequirements(context).filter((item) => item.mandatory).length,
        rejectionCount: batchRequirements(context).filter((item) => item.isRejection).length,
        requirements: pool.slice(offset, offset + limit).map((item) => ({
          id: item.id,
          requirementNumber: item.requirementNumber,
          domain: item.domain,
          mandatory: item.mandatory,
          isRejection: item.isRejection,
          responseType: item.responseType,
          text: item.text,
        })),
      });
    },
  });

  pi.registerTool({
    name: "validate_coverage_candidate",
    label: "自检覆盖候选",
    description:
      "提交你打算输出的 coverage 做自检：requirementId 是否属于本批、sectionId 是否真实存在、coverageType/status 取值是否合法、evaluationIds 是否存在、本批是否还有条款没被覆盖、同一条款是否被重复映射。定稿前务必调用并按结果修正。",
    parameters: Type.Object({
      coverage: Type.Array(
        Type.Object({
          requirementId: Type.String({ description: "本批条款 ID" }),
          sectionId: Type.String({ description: "目录中存在的 sectionId" }),
          coverageType: Type.Optional(Type.String({ description: "chapter_text/table/attachment/commitment/fixed_form/other" })),
          status: Type.Optional(Type.String({ description: "planned/covered/uncovered/needs_review" })),
          evaluationIds: Type.Optional(Type.Array(Type.String())),
          reason: Type.Optional(Type.String()),
        }),
      ),
      uncoveredRequirements: Type.Optional(
        Type.Array(Type.Object({ requirementId: Type.String(), reason: Type.Optional(Type.String()) })),
      ),
    }),
    async execute(_toolCallId: any, params: any) {
      const { context, error } = loadContext();
      if (!context) return errorResult(error ?? "工具数据不可用");
      const rows = Array.isArray(params?.coverage) ? params.coverage : [];
      const batch = batchRequirements(context);
      const batchIds = new Set(batch.map((item) => item.id));
      const chapterIds = new Set(context.chapters.map((chapter) => chapter.sectionId));
      const evaluationIds = new Set(context.evaluationItems.map((item) => item.id));
      const issues: Array<{ severity: string; code: string; message: string }> = [];
      const covered = new Set<string>();
      const duplicate = new Set<string>();

      rows.forEach((row: any, index: number) => {
        const label = `#${index + 1}`;
        const requirementId = String(row?.requirementId ?? "").trim();
        const sectionId = String(row?.sectionId ?? "").trim();
        if (!requirementId) {
          issues.push({ severity: "error", code: "missing_requirement", message: `${label}：缺少 requirementId` });
        } else if (!batchIds.has(requirementId)) {
          issues.push({
            severity: "error",
            code: "unknown_requirement",
            message: `${label}：条款 ${requirementId} 不属于本批，本批只有 ${context.summary.batchRequirementCount} 条`,
          });
        } else if (covered.has(requirementId)) {
          duplicate.add(requirementId);
        } else {
          covered.add(requirementId);
        }
        if (!sectionId) {
          issues.push({ severity: "error", code: "missing_section", message: `${label}：缺少 sectionId` });
        } else if (!chapterIds.has(sectionId)) {
          issues.push({
            severity: "error",
            code: "unknown_section",
            message: `${label}：章节 ${sectionId} 不存在，请用 list_chapters 里的既有 sectionId`,
          });
        }
        const coverageType = String(row?.coverageType ?? "").trim();
        if (coverageType && !COVERAGE_TYPES.includes(coverageType)) {
          issues.push({
            severity: "warning",
            code: "bad_coverage_type",
            message: `${label}：coverageType=${coverageType} 不是约定取值（${COVERAGE_TYPES.join("/")}）`,
          });
        }
        const status = String(row?.status ?? "").trim();
        if (status && !STATUSES.includes(status)) {
          issues.push({
            severity: "warning",
            code: "bad_status",
            message: `${label}：status=${status} 不是约定取值（${STATUSES.join("/")}）`,
          });
        }
        for (const id of Array.isArray(row?.evaluationIds) ? row.evaluationIds : []) {
          if (!evaluationIds.has(String(id))) {
            issues.push({
              severity: "error",
              code: "unknown_evaluation",
              message: `${label}：评分项 ${id} 不存在`,
            });
          }
        }
      });

      for (const id of duplicate) {
        issues.push({
          severity: "warning",
          code: "duplicate_requirement",
          message: `条款 ${id} 被多个章节响应：确认确有必要时放进 duplicatedRequirements，否则只保留一个主响应章节`,
        });
      }
      const uncovered = new Set(
        (Array.isArray(params?.uncoveredRequirements) ? params.uncoveredRequirements : [])
          .map((item: any) => String(item?.requirementId ?? "").trim())
          .filter(Boolean),
      );
      const missing = batch
        .map((item) => item.id)
        .filter((id) => !covered.has(id) && !uncovered.has(id));
      if (missing.length) {
        issues.push({
          severity: "error",
          code: "uncovered_batch",
          message: `还有 ${missing.length} 条本批条款既没有 coverage 也没有 uncoveredRequirements：${missing.slice(0, 10).join("、")}${missing.length > 10 ? " …" : ""}`,
        });
      }
      const hasError = issues.some((issue) => issue.severity === "error");
      return textResult({
        ok: !hasError,
        checked: rows.length,
        batchSize: batch.length,
        covered: covered.size,
        missingRequirementIds: missing,
        issues,
        hint: hasError ? "请修正 error 后重新自检" : "可以按此输出",
      });
    },
  });
}
