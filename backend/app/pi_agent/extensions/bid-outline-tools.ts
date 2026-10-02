/**
 * 目录规划智能体的只读工具集（M2′）。
 *
 * 数据来源：环境变量 `BID_OUTLINE_CONTEXT` 指向的一份 JSON 快照，由后端
 * `backend/app/pi_agent/outline_agent_context.py` 在每次调用 Pi 前生成。
 * 工具**只读、无副作用**：不写盘、不联网、不接受任意文件路径。
 *
 * 启动方式（由 backend/app/pi_agent/client.py 组装）：
 *   pi --no-builtin-tools --tools bid_outline_signals,search_requirements,... -e <本文件>
 *
 * 为什么不给 read/bash/write/edit：招标文件属于不可信输入，一旦被提示词注入，
 * 通用文件工具会让模型读到本机任意文件（含 agent-config.json 里的 API Key）。
 */
import { readFileSync } from "node:fs";

import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

type Requirement = {
  id: string;
  domain: string;
  responseType: string;
  mandatory: boolean;
  isRejection: boolean;
  boundTo: "narrative" | "table" | "none" | string;
  text: string;
};

type PlanNode = {
  sectionId: string;
  title: string;
  parentSectionId: string;
  level: number;
  order: number;
  nodeType?: string;
  responseType?: string;
  source?: string;
  kind?: string;
  required?: boolean;
};

type OutlineContext = {
  workspaceId: string;
  projectId: string;
  policy: { name: string; maxPerVolume: number; maxTotal: number; allowInitiative: boolean };
  summary: Record<string, unknown>;
  signals: {
    requiredGaps: unknown[];
    initiatives: unknown[];
    notes: string[];
    warnings: string[];
  };
  volumes: PlanNode[];
  formatItems: PlanNode[];
  existingSections: PlanNode[];
  requirements: Requirement[];
  evaluationItems: Array<Record<string, unknown>>;
};

let cached: OutlineContext | null = null;
let cachedError: string | null = null;

function loadContext(): { context: OutlineContext | null; error: string | null } {
  if (cached) return { context: cached, error: null };
  if (cachedError) return { context: null, error: cachedError };
  const path = process.env.BID_OUTLINE_CONTEXT ?? "";
  if (!path) {
    cachedError = "未注入 BID_OUTLINE_CONTEXT，工具数据不可用；请直接依据提示词中的解析视图作答。";
    return { context: null, error: cachedError };
  }
  try {
    cached = JSON.parse(readFileSync(path, "utf-8")) as OutlineContext;
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

function ancestorVolumeId(sectionId: string, nodes: Map<string, PlanNode>, volumes: PlanNode[]): string {
  const volumeIds = new Set(volumes.map((volume) => volume.sectionId));
  let current = sectionId;
  for (let hop = 0; hop < 8 && current; hop += 1) {
    if (volumeIds.has(current)) return current;
    const node = nodes.get(current);
    if (!node) return "";
    current = node.parentSectionId;
  }
  return "";
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "bid_outline_signals",
    label: "目录规划信号",
    description:
      "读取只读诊断信号：条款盘点（叙述承载/仅响应表/完全未绑定）、要求响应缺口（requiredGaps）、可主动发挥的章节原型（initiatives）、策略上限。定章节前先看它。",
    parameters: Type.Object({}),
    async execute() {
      const { context, error } = loadContext();
      if (!context) return errorResult(error ?? "工具数据不可用");
      return textResult({
        policy: context.policy,
        summary: context.summary,
        requiredGaps: context.signals.requiredGaps,
        initiatives: context.signals.initiatives,
        notes: context.signals.notes,
        warnings: context.signals.warnings,
      });
    },
  });

  pi.registerTool({
    name: "search_requirements",
    label: "检索招标条款",
    description:
      "按关键词检索解析出的招标要求条款，返回条款 ID、领域、响应类型、是否强制/废标、当前绑定情况（narrative=已有叙述章节承载 / table=仅响应表承载 / none=完全未绑定）与正文。用于给拟新增的章节找依据。",
    parameters: Type.Object({
      query: Type.String({ description: "检索词，例如“技术方案 实施方案”“售后 服务 响应”" }),
      domain: Type.Optional(Type.String({ description: "只看某个领域，如 technical / commercial / service" })),
      onlyUnbound: Type.Optional(Type.Boolean({ description: "只要当前没有任何叙述章节承载的条款" })),
      limit: Type.Optional(Type.Number({ description: "返回条数上限，默认 15，最大 40" })),
    }),
    async execute(_toolCallId: any, params: any) {
      const { context, error } = loadContext();
      if (!context) return errorResult(error ?? "工具数据不可用");
      const query = String(params?.query ?? "");
      const domain = String(params?.domain ?? "").trim();
      const onlyUnbound = Boolean(params?.onlyUnbound);
      const limit = Math.min(Math.max(Number(params?.limit ?? 15) || 15, 1), 40);
      const matches = context.requirements
        .filter((item) => (domain ? item.domain === domain : true))
        .filter((item) => (onlyUnbound ? item.boundTo !== "narrative" : true))
        .map((item) => ({ item, score: scoreText(query, item.text) }))
        .filter((row) => row.score > 0)
        .sort((left, right) => right.score - left.score)
        .slice(0, limit)
        .map((row) => ({
          id: row.item.id,
          domain: row.item.domain,
          responseType: row.item.responseType,
          mandatory: row.item.mandatory,
          isRejection: row.item.isRejection,
          boundTo: row.item.boundTo,
          text: row.item.text,
        }));
      return textResult({
        query,
        matched: matches.length,
        totalRequirements: context.requirements.length,
        matches,
      });
    },
  });

  pi.registerTool({
    name: "get_evaluation_points",
    label: "读取评分项",
    description:
      "读取评标办法里的评分项（评分因素/评分标准/分值）。主观分与技术评分往往是主动加分章节的依据。",
    parameters: Type.Object({}),
    async execute() {
      const { context, error } = loadContext();
      if (!context) return errorResult(error ?? "工具数据不可用");
      return textResult({
        total: context.evaluationItems.length,
        evaluationItems: context.evaluationItems,
      });
    },
  });

  pi.registerTool({
    name: "get_submission_structure",
    label: "读取原文骨架",
    description:
      "读取不可改动的原文骨架（卷 volumes、格式条目 formatItems）、已有自定义章节与策略上限。新增章节必须挂在其中某个既有节点的 parentSectionId 下。",
    parameters: Type.Object({}),
    async execute() {
      const { context, error } = loadContext();
      if (!context) return errorResult(error ?? "工具数据不可用");
      return textResult({
        policy: context.policy,
        volumes: context.volumes,
        formatItems: context.formatItems,
        existingSections: context.existingSections,
      });
    },
  });

  pi.registerTool({
    name: "validate_outline_candidate",
    label: "自检候选章节",
    description:
      "提交你打算新增的章节做自检：标题是否为空/过长、父节点是否存在、同级是否重名、依据 requirementIds/evaluationIds 是否真实存在、是否超出每卷上限。定稿前务必调用一次并按结果修正。",
    parameters: Type.Object({
      sections: Type.Array(
        Type.Object({
          title: Type.String({ description: "章节标题" }),
          parentSectionId: Type.String({ description: "要挂到的既有节点 ID（卷或格式条目）" }),
          archetype: Type.Optional(Type.String({ description: "章节原型，如 technical_plan / after_sales / understanding" })),
          requirementIds: Type.Optional(Type.Array(Type.String())),
          evaluationIds: Type.Optional(Type.Array(Type.String())),
          basis: Type.Optional(Type.String({ description: "一句话依据：为什么需要这一章" })),
        }),
      ),
      planIds: Type.Optional(
        Type.Array(
          Type.String({
            description: "你在本次输出里同时新建的其他节点 ID（新增的格式条目/分组），用于校验父子关系",
          }),
        ),
      ),
    }),
    async execute(_toolCallId: any, params: any) {
      const { context, error } = loadContext();
      if (!context) return errorResult(error ?? "工具数据不可用");
      const candidate = Array.isArray(params?.sections) ? params.sections : [];
      const nodes = new Map<string, PlanNode>();
      for (const node of [...context.volumes, ...context.formatItems, ...context.existingSections]) {
        nodes.set(node.sectionId, node);
      }
      const requirementIds = new Set(context.requirements.map((item) => item.id));
      const evaluationIds = new Set(
        context.evaluationItems.map((item) => String((item as { id?: string }).id ?? "")),
      );
      const parentAlias = new Map<string, string>();
      candidate.forEach((section: any, index: number) => {
        const alias = String(section?.sectionId ?? section?.title ?? `candidate-${index + 1}`);
        parentAlias.set(alias, String(section?.parentSectionId ?? ""));
      });
      const declaredPlanIds = new Set(
        (Array.isArray(params?.planIds) ? params.planIds : []).map((value: any) => String(value)),
      );
      const issues: Array<{ severity: string; code: string; message: string }> = [];
      const perVolume = new Map<string, number>();
      const siblingTitles = new Map<string, Set<string>>();

      candidate.forEach((section: any, index: number) => {
        const label = `#${index + 1} ${String(section?.title ?? "(无标题)")}`;
        const title = String(section?.title ?? "").trim();
        const parentId = String(section?.parentSectionId ?? "").trim();
        if (!title) issues.push({ severity: "error", code: "empty_title", message: `${label}：标题为空` });
        if (title.length > 60) {
          issues.push({ severity: "warning", code: "long_title", message: `${label}：标题超过 60 字，建议精简` });
        }
        if (!parentId) {
          issues.push({ severity: "error", code: "missing_parent", message: `${label}：缺少 parentSectionId` });
        } else if (
          !nodes.has(parentId) &&
          !declaredPlanIds.has(parentId) &&
          ![...parentAlias.values()].includes(parentId)
        ) {
          issues.push({
            severity: "error",
            code: "unknown_parent",
            message: `${label}：父节点 ${parentId} 不存在，请用 get_submission_structure 里的既有节点 ID`,
          });
        } else {
          const siblingKey = parentId;
          const titles = siblingTitles.get(siblingKey) ?? new Set<string>();
          if (titles.has(title)) {
            issues.push({ severity: "error", code: "duplicate_title", message: `${label}：同一父节点下已有同名章节` });
          }
          titles.add(title);
          siblingTitles.set(siblingKey, titles);
        }
        const basis = String(section?.basis ?? "").trim();
        if (!basis) {
          issues.push({ severity: "error", code: "missing_basis", message: `${label}：缺少依据（basis），无依据的章节会被丢弃` });
        }
        for (const id of Array.isArray(section?.requirementIds) ? section.requirementIds : []) {
          if (!requirementIds.has(String(id))) {
            issues.push({ severity: "error", code: "unknown_requirement", message: `${label}：条款 ${id} 不存在` });
          }
        }
        for (const id of Array.isArray(section?.evaluationIds) ? section.evaluationIds : []) {
          if (!evaluationIds.has(String(id))) {
            issues.push({ severity: "error", code: "unknown_evaluation", message: `${label}：评分项 ${id} 不存在` });
          }
        }
        const volumeId = ancestorVolumeId(parentId, nodes, context.volumes);
        if (volumeId) perVolume.set(volumeId, (perVolume.get(volumeId) ?? 0) + 1);
      });

      for (const [volumeId, count] of perVolume) {
        const max = Number(context.policy?.maxPerVolume ?? 0);
        if (max > 0 && count > max) {
          issues.push({
            severity: "error",
            code: "volume_limit",
            message: `卷 ${volumeId} 拟新增 ${count} 章，超过上限 ${max}`,
          });
        }
      }
      const maxTotal = Number(context.policy?.maxTotal ?? 0);
      if (maxTotal > 0 && candidate.length > maxTotal) {
        issues.push({
          severity: "error",
          code: "total_limit",
          message: `拟新增 ${candidate.length} 章，超过全篇上限 ${maxTotal}`,
        });
      }
      const hasError = issues.some((issue) => issue.severity === "error");
      return textResult({
        ok: !hasError,
        addedPerVolume: Object.fromEntries(perVolume),
        issues,
        hint: hasError ? "请修正 error 后重新自检" : "可以按此输出",
      });
    },
  });
}
