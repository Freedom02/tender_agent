import type { BidSection } from "../../shared/types";

export type SectionJobState = "pending" | "generating" | "completed" | "failed" | "review" | "group";

export function sectionJobState(section: BidSection): SectionJobState {
  // 卷和纯目录分组没有独立正文，不能成为撰写待办或进入完成度分母。
  if (section.nodeType === "volume" || section.responseType === "section_group") return "group";
  const status = (section.status || "").trim().toLowerCase();
  if (["generating", "running", "processing", "生成中"].includes(status)) return "generating";
  if (["failed", "error", "failure", "失败"].includes(status)) return "failed";
  if (["generated", "done", "complete", "completed", "success", "filled", "完成", "已完成", "已生成"].includes(status)) return "completed";
  if (["needs_review", "needs_confirmation", "待复核"].includes(status)) return "review";
  // missing 可能已有占位正文，仍未完成；模板原文和正文存在也不能替代 status。
  return "pending";
}

export const SECTION_JOB_BADGES: Record<SectionJobState, {
  label: string;
  type: "success" | "warning" | "danger" | "info";
}> = {
  pending: { label: "待生成", type: "info" },
  generating: { label: "生成中", type: "warning" },
  completed: { label: "已完成", type: "success" },
  failed: { label: "失败可重试", type: "danger" },
  review: { label: "待复核", type: "warning" },
  group: { label: "目录分组", type: "info" }
};

export function sectionJobOrigin(section: BidSection): string {
  if (section.derived) {
    return section.derivedOrigin === "ai_initiative" ? "智能体·主动发挥" : "智能体·要求响应";
  }
  return ({
    copied_template: "招标原文模板",
    template: "固定文书",
    ai: "智能体撰写",
    table: "表格响应",
    attachment: "附件材料"
  } as Record<string, string>)[section.source] || section.source || "正文章节";
}

export function sectionAncestorIds(plan: BidSection[], sectionId: string): string[] {
  const byId = new Map(plan.map((section) => [section.sectionId, section]));
  return ancestorIds(byId, sectionId);
}

function ancestorIds(byId: Map<string, BidSection>, sectionId: string): string[] {
  const visited = new Set([sectionId]);
  const ancestors: string[] = [];
  let parentId = byId.get(sectionId)?.parentSectionId;
  while (parentId && byId.has(parentId) && !visited.has(parentId)) {
    visited.add(parentId);
    ancestors.push(parentId);
    parentId = byId.get(parentId)?.parentSectionId;
  }
  return ancestors;
}

export interface SectionJobCounts {
  total: number;
  completed: number;
  pending: number;
  generating: number;
  failed: number;
  review: number;
  percentage: number;
}

function emptyCounts(): SectionJobCounts {
  return { total: 0, completed: 0, pending: 0, generating: 0, failed: 0, review: 0, percentage: 0 };
}

export interface SectionJobVolume extends SectionJobCounts {
  id: string;
  title: string;
}

export interface SectionJobTodo {
  section: BidSection;
  state: "pending" | "failed" | "review";
  volumeTitle: string;
}

export function summarizeSectionJobs(plan: BidSection[]) {
  const summary = emptyCounts();
  const byId = new Map(plan.map((section) => [section.sectionId, section]));
  const volumeNodes = plan.filter((section) => section.nodeType === "volume");
  const volumes: SectionJobVolume[] = volumeNodes.map((volume) => ({
    ...emptyCounts(), id: volume.sectionId, title: volume.title
  }));
  const volumeById = new Map<string, SectionJobVolume>();
  volumeNodes.forEach((volume, index) => {
    volumeById.set(volume.sectionId, volumes[index]);
    if (volume.volumeId) volumeById.set(volume.volumeId, volumes[index]);
  });
  const unassigned: SectionJobVolume = {
    ...emptyCounts(), id: "__unassigned__", title: volumeNodes.length ? "未分卷章节" : "全部章节"
  };
  const todos: SectionJobTodo[] = [];

  for (const section of plan) {
    const state = sectionJobState(section);
    if (state === "group") continue;
    // 优先按父目录找卷；兼容仅有 volumeId 的条目和旧版未分卷目录。
    const ancestorVolumeId = ancestorIds(byId, section.sectionId)
      .find((id) => byId.get(id)?.nodeType === "volume");
    const volume = (ancestorVolumeId ? volumeById.get(ancestorVolumeId) : undefined)
      || volumeById.get(section.volumeId || "") || unassigned;
    for (const counts of [summary, volume]) {
      counts.total += 1;
      counts[state] += 1;
    }
    if (state === "failed" || state === "pending" || state === "review") {
      todos.push({ section, state, volumeTitle: volume.title });
    }
  }

  if (unassigned.total) volumes.push(unassigned);
  for (const counts of [summary, ...volumes]) {
    counts.percentage = counts.total ? Math.floor(counts.completed * 100 / counts.total) : 0;
  }
  // 失败先处理，同类章节保留目录顺序。
  todos.sort((a, b) => Number(b.state === "failed") - Number(a.state === "failed"));
  return { summary, volumes, todos };
}
