<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { renderAsync } from "docx-preview";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Bold,
  Check,
  ChevronDown,
  Download,
  Eye,
  FileText,
  Heading1,
  Heading2,
  Italic,
  ListTree,
  List,
  ListOrdered,
  PenLine,
  Plus,
  Redo2,
  RemoveFormatting,
  Search,
  ShieldCheck,
  Trash2,
  Underline,
  Upload,
  Undo2,
  Wand2,
} from "lucide-vue-next";
import { api } from "../../services/api";
import SectionJobPanel from "./components/SectionJobPanel.vue";
import SectionStatusBadge from "./components/SectionStatusBadge.vue";
import { sectionAncestorIds } from "./sectionJobs";
import type {
  BidDocument,
  BidMode,
  BidSection,
  CatalogItem,
  CloudBidRun,
  EvidenceChapterPreview,
  EvidenceEntry,
  EvidenceAssetFile,
  LibrarySummary,
  MaterialProfile,
  OutlineDiagnosis,
  OutlineDiagnosisSuggestion,
  SubmissionStructure,
  Workspace
} from "../../shared/types";

const props = defineProps<{
  workspace: Workspace | null;
  project: CatalogItem | null;
  libraries: LibrarySummary[];
  workspaces: Workspace[];
  selectedLibraryKey: string;
  materialsProfile: MaterialProfile | null;
  busy: boolean;
  importFile: File | null;
  importForm: { name: string; text: string };
  inputKind: "notice" | "tender_document";
}>();

const emit = defineEmits<{
  "update:selected-library-key": [key: string];
  "update:bid-document": [doc: BidDocument];
  "select-workspace": [workspace: Workspace];
  "file-selected": [file: File | null];
  "cloud-workspace-ready": [payload: { workspace: Workspace; project: CatalogItem }];
  evaluate: [];
  "update:input-kind": [kind: "notice" | "tender_document"];
  "delete-workspace": [workspace: Workspace];
}>();

// ---- 状态 ----
const activeStep = ref(1);
const bidDocument = ref<BidDocument | null>(null);
const mode = ref<BidMode>("open");
const planning = ref(false);
const savingPlan = ref(false);
const generatingSectionId = ref("");
const fillingTemplateSectionId = ref("");
const rewritingSectionId = ref("");
const reviewingCompliance = ref(false);
const savingSectionId = ref("");
const exporting = ref(false);
const buildingEvidence = ref(false);
const evidencePreview = ref<EvidenceChapterPreview | null>(null);
const evidencePanelOpen = ref(false);
const previewingExport = ref(false);
const exportFiles = ref<{ name: string; url: string }[]>([]);
const exportPreviewVisible = ref(false);
const exportPreviewFiles = ref<{ name: string; url: string }[]>([]);
const activeExportPreviewIndex = ref(0);
const exportPreviewRef = ref<HTMLElement | null>(null);
const exportPreviewLoading = ref(false);
let exportPreviewRequestId = 0;
const rewriteInstruction = ref("");

const currentSectionId = ref("");
const editorHtml = ref("");
const editorLayoutRef = ref<{ $el?: HTMLElement } | null>(null);
const editorColumnHeight = ref("");
let editorColumnHeightFrame = 0;
const collapsedNavSectionIds = ref<Set<string>>(new Set());
const fileInput = ref<HTMLInputElement | null>(null);
const cloudRun = ref<CloudBidRun | null>(null);
const cloudMaterializing = ref(false);
let cloudPollingTimer: number | null = null;
const MAX_PLAN_LEVEL = 9;
const historyQuery = ref("");
const historyFilter = ref<"all" | "with_plan" | "parsed">("all");
const selectedHistoryWorkspaceId = ref(props.workspace?.id || "");
const historyActionWorkspaceId = ref("");
const inlineAddTargetId = ref("");
const inlineAddMode = ref<"child" | "sibling">("child");
const inlineAddTitle = ref("");
const inlineAddTitleInput = ref<{ focus: () => void } | null>(null);

// ---- 从共享状态同步 bidDocument ----
watch(
  () => props.workspace?.bidDocument as BidDocument | undefined,
  (doc) => {
    bidDocument.value = doc || null;
    mode.value = doc?.mode || "open";
  },
  { immediate: true }
);

watch(
  () => props.workspace?.id,
  (workspaceId) => {
    if (workspaceId) selectedHistoryWorkspaceId.value = workspaceId;
    activeStep.value = props.workspace?.bidDocument?.plan?.length ? 2 : 1;
    currentSectionId.value = "";
    editorHtml.value = "";
    collapsedNavSectionIds.value = new Set();
    exportFiles.value = [];
  },
  { immediate: true }
);

function syncEditorColumnHeight() {
  if (editorColumnHeightFrame) cancelAnimationFrame(editorColumnHeightFrame);
  editorColumnHeightFrame = requestAnimationFrame(() => {
    editorColumnHeightFrame = 0;
    if (
      activeStep.value !== 3 ||
      window.innerWidth <= 991 ||
      !editorLayoutRef.value?.$el
    ) {
      editorColumnHeight.value = "";
      return;
    }

    const layoutTop = editorLayoutRef.value.$el.getBoundingClientRect().top;
    const visibleTop = Math.max(16, layoutTop);
    editorColumnHeight.value = `${Math.max(520, Math.floor(window.innerHeight - visibleTop))}px`;
  });
}

const editorColumnStyle = computed(() =>
  editorColumnHeight.value ? { height: editorColumnHeight.value } : {}
);

watch(activeStep, () => {
  void nextTick(syncEditorColumnHeight);
});

const workspaceId = computed(() => props.workspace?.id || "");
const currentProject = computed(() => props.workspace?.project || props.project);
const currentProjectTitle = computed(() => currentProject.value?.title || props.workspace?.title || "");
const plan = computed(() => bidDocument.value?.plan || []);

// ---- 目录规划诊断（只读，不改动目录；先让用户校准“要求章节 / 自由发挥章节”口径）----
const outlineDiagnosis = ref<OutlineDiagnosis | null>(null);
const outlineDiagnosisLoading = ref(false);
const outlineDiagnosisOpen = ref(true);

async function loadOutlineDiagnosis() {
  const id = workspaceId.value;
  if (!id) {
    outlineDiagnosis.value = null;
    return;
  }
  outlineDiagnosisLoading.value = true;
  try {
    outlineDiagnosis.value = await api.bidDocumentOutlineDiagnosis(id);
  } catch {
    // 诊断只是辅助信息：失败不打断目录流程
    outlineDiagnosis.value = null;
  } finally {
    outlineDiagnosisLoading.value = false;
  }
}
function diagnosisOriginLabel(origin: string): string {
  return origin === "ai_initiative" ? "自由发挥" : "要求响应";
}
function diagnosisOriginTagType(origin: string): "warning" | "success" {
  return origin === "ai_initiative" ? "success" : "warning";
}
function diagnosisRequirementSummary(item: OutlineDiagnosisSuggestion): string {
  const parts: string[] = [];
  if (item.requirementCount) parts.push(`相关条款 ${item.requirementCount} 条`);
  if (item.tableBoundCount) parts.push(`其中仅响应表承载 ${item.tableBoundCount} 条`);
  if (item.unboundCount) parts.push(`完全未绑定 ${item.unboundCount} 条`);
  if (item.evaluationIds.length) parts.push(`评分点 ${item.evaluationIds.length} 个`);
  return parts.join(" · ") || "依据评分项判断";
}
/** 目录智能体新增章节的标记与依据（M2′）。 */
function derivedOriginLabel(section: BidSection): string {
  return section.derivedOrigin === "ai_initiative" ? "智能体·主动发挥" : "智能体·要求响应";
}
function derivedOriginTagType(section: BidSection): "success" | "warning" {
  return section.derivedOrigin === "ai_initiative" ? "success" : "warning";
}
function derivedBasisText(section: BidSection): string {
  const parts: string[] = [];
  if (section.derivedArchetype) parts.push(`原型：${section.derivedArchetype}`);
  parts.push(section.derivedBasis || "依据不足");
  const requirementCount = section.requirementIds?.length || 0;
  const evaluationCount = section.evaluationIds?.length || 0;
  if (requirementCount) parts.push(`引用条款 ${requirementCount} 条`);
  if (evaluationCount) parts.push(`引用评分项 ${evaluationCount} 个`);
  return parts.join("\n");
}

watch(() => props.workspace?.id, () => void loadOutlineDiagnosis(), { immediate: true });
watch(
  () => plan.value.length,
  () => {
    if (activeStep.value === 2) void loadOutlineDiagnosis();
  }
);
watch(activeStep, (step) => {
  if (step === 2) void loadOutlineDiagnosis();
});

// ---- 工具函数 ----
function draftProviderLabel(provider?: string): string {
  if (provider === "pi") return "Pi 智能体";
  if (provider === "dify") return "云端智能体";
  if (provider === "local_fallback") return "本地回退";
  return provider || "";
}

function templateCopyLabel(section?: BidSection): string {
  if (!section?.templateFragmentId) return "";
  if (section.templateCopyMode === "ooxml_body_range") return "原始 Word 格式";
  return "文本回退";
}

function templateCopyTagType(section?: BidSection): "success" | "warning" | "danger" | "info" {
  if (section?.templateCopyMode === "ooxml_body_range") return "success";
  if (section?.templateFragmentId) return "warning";
  return "info";
}

function templateFillLabel(section?: BidSection): string {
  if (!section?.templateFragmentId) return "";
  if (section.templateFillProvider === "pi") return "Pi 表单填充";
  if (section.templateFillProvider === "local_fallback") return "本地填空回退";
  if (section.templateFillProvider === "local") return "本地确定性填充";
  return "待填充";
}

function complianceStatusLabel(status?: string): string {
  if (status === "pass") return "审查通过";
  if (status === "stale") return "审查已过期";
  if (status === "failed") return "审查失败";
  return "需要人工复核";
}

function complianceTagType(status?: string): "success" | "warning" | "danger" | "info" {
  if (status === "pass") return "success";
  if (status === "failed") return "danger";
  if (status === "stale") return "warning";
  return "warning";
}

function complianceProviderLabel(provider?: string): string {
  if (provider === "pi") return "Pi 合规审查";
  if (provider === "local_fallback") return "本地基础校对回退";
  if (provider === "local") return "本地基础校对";
  return provider || "合规审查";
}

function providerLabel(provider?: string): string {
  if (provider === "pi") return "Pi 智能体";
  if (provider === "dify") return "Dify 云端智能体";
  if (provider === "local") return "本地解析";
  return provider || "当前智能体";
}

function submissionItemTitles(volume: SubmissionStructure["volumes"][number]): string {
  return volume.items?.map((item) => item.title).join("、") || "";
}

const AGENT_NAMES: Record<string, string> = {
  DocumentStructureAgent: "文档结构识别",
  ProjectMetaAgent: "项目基本信息提取",
  RequirementExtractAgent: "招标要求提取",
  EvaluationExtractAgent: "评分办法提取",
  TemplateDetectAgent: "投标模板识别",
  ParseReviewAgent: "解析结果复核",
  TenderClauseAgent: "条款与证据抽取",
  TenderEvaluationAgent: "评审办法识别",
  TenderTableBindingAgent: "原响应表绑定",
  TenderSemanticReviewAgent: "独立语义复核",
  ParseBundleAssembler: "解析产物整理"
};

function currentAgentName(agentId?: string): string {
  return AGENT_NAMES[agentId || ""] || agentId || "准备解析";
}

function currentAgentStatus(status?: string): string {
  if (status?.includes(":")) {
    const [state, label] = status.split(":", 2);
    const names: Record<string, string> = { running: "执行中", retrying: "校验修正中", success: "已完成", failed: "执行失败", skipped: "无需执行" };
    return `${names[state] || state} · 分片 ${label}`;
  }
  return (
    {
      running: "执行中",
      success: "已完成",
      failed: "执行失败",
      completed: "已完成"
    }[status || ""] || status || "等待中"
  );
}

function agentName(agent: Record<string, unknown>): string {
  const agentId = String(agent.agentId || "");
  return currentAgentName(agentId) || "未命名智能体";
}

function agentStatus(agent: Record<string, unknown>): string {
  return String(agent.status || "unknown");
}

function agentStatusType(agent: Record<string, unknown>): "success" | "warning" | "danger" | "info" {
  const status = agentStatus(agent);
  if (status === "success") return "success";
  if (status === "failed") return "danger";
  return "warning";
}

function issueIds(items?: Record<string, unknown>[]): string {
  return (
    items
      ?.map((item) => String(item.requirementId || item.id || "").trim())
      .filter(Boolean)
      .join("、") || "未识别条款"
  );
}

function requirementMappingStatusLabel(status?: string): string {
  if (status === "completed") return "已完成";
  if (status === "failed") return "映射失败";
  if (status === "pending") return "正文阶段执行";
  return status || "待处理";
}

function requirementMappingTagType(status?: string): "success" | "warning" | "danger" | "info" {
  if (status === "completed") return "success";
  if (status === "failed") return "danger";
  if (status === "pending") return "warning";
  return "info";
}

function planSourceLabel(source?: string): string {
  if (source === "unplanned") return "解析完成，待生成目录";
  if (source === "pi_agent") return "Pi 智能体生成";
  if (source === "cloud_agent") return "云端智能体生成";
  if (source === "tender_document_structure") return "招标文件原文结构";
  if (source === "tender_document") return "本地规则：招标文件格式";
  if (source === "builtin_template") return "本地规则：标准模板";
  return source || "目录规划";
}

function planningSourceKey(doc?: BidDocument | null): string {
  if (doc?.planningSource) return doc.planningSource;
  if (doc?.planningStatus === "pending") return "unplanned";
  return doc?.planSource || "";
}

function parseSourceLabel(doc?: BidDocument | null): string {
  if (doc?.parseSourceDetail) return doc.parseSourceDetail;
  if (doc?.parseProvider === "pi") return "Pi 本地智能体解析结果";
  if (doc?.parseProvider === "dify") return "Dify 云端智能体解析结果";
  if (doc?.parseProvider === "matching_local") return "匹配评估结果复用 · 本地解析";
  if (doc?.parseProvider === "legacy_local") return "兼容迁移的本地解析结果";
  if (doc?.planSource === "pi_agent") return "Pi 本地智能体解析结果";
  if (doc?.planSource === "cloud_agent") return "云端智能体解析结果";
  return "解析来源待确认";
}

function planSourceTag(
  source?: string,
  doc?: BidDocument | null
): "success" | "warning" | "danger" | "info" {
  if (doc?.planningStatus?.startsWith("fallback")) return "warning";
  if (source === "unplanned") return "warning";
  if (source === "pi_agent" || source === "cloud_agent") return "success";
  if (source === "tender_document_structure") return "success";
  if (source === "tender_document" || source === "builtin_template") return "warning";
  return "info";
}

function planSourceMessage(doc?: BidDocument | null): string {
  const source = planningSourceKey(doc);
  const fallbackReason = doc?.planningFallbackReason
    ? `回退/保留原因：${doc.planningFallbackReason}`
    : "";
  const parseSource = `解析来源：${parseSourceLabel(doc)}。`;
  if (source === "unplanned") {
    return `${parseSource}目录规划来源：尚未生成，请点击“下一步：生成目录”调用目录规划智能体。`;
  }
  if (source === "pi_agent") {
    return [
      `${parseSource}本次目录由 Pi 智能体根据招标文件要求、条款库和历史解析结果生成。`,
      fallbackReason
    ]
      .filter(Boolean)
      .join(" ");
  }
  if (source === "cloud_agent") {
    return [
      `${parseSource}本次目录由云端智能体生成。`,
      fallbackReason
    ]
      .filter(Boolean)
      .join(" ");
  }
  if (source === "tender_document") {
    const reason = doc?.planningFallbackReason ? `回退原因：${doc.planningFallbackReason}` : "";
    return [
      doc?.planningSourceDetail
        ? `${parseSource}本次目录由本地规则按招标文件中的投标文件格式生成；提取依据：${doc.planningSourceDetail}`
        : "本次目录由本地规则按招标文件中的投标文件格式生成。",
      reason
    ]
      .filter(Boolean)
      .join(" ");
  }
  if (source === "tender_document_structure") {
    const structure = doc?.submissionStructure;
    const volumeCount = structure?.volumes?.length || 0;
    const itemCount = structure?.volumes?.reduce(
      (total, volume) => total + (volume.items?.length || 0),
      0
    ) || 0;
    const splitHint = structure?.mode === "separate"
      ? "招标文件明确要求独立分册。"
      : structure?.mode === "single"
        ? "招标文件明确要求合卷。"
        : "已识别文件组成，但未确认必须独立分册，默认合卷导出。";
    return [
      `${parseSource}目录按招标文件原文生成：${volumeCount} 个卷、${itemCount} 个卷内文件条目。`,
      splitHint,
      structure?.sourceLocator ? `提取依据：${structure.sourceLocator}` : "",
      fallbackReason
    ]
      .filter(Boolean)
      .join(" ");
  }
  if (source === "builtin_template") {
    const reason = doc?.planningFallbackReason ? `回退原因：${doc.planningFallbackReason}` : "";
    return [parseSource, doc?.planningSourceDetail || "本次目录由本地内置规则生成。", reason]
      .filter(Boolean)
      .join(" ");
  }
  return `${parseSource}当前目录来源待确认。`;
}

function isMatchingTenderWorkspace(workspace: Workspace): boolean {
  return (
    workspace.project?.document?.kind === "tender_document" &&
    Boolean(workspace.reasoning?.fineReview?.rows?.length)
  );
}

function isWorkspaceParsed(workspace: Workspace): boolean {
  return Boolean(
    workspace.parseBundleRef ||
    workspace.reasoning?.fineReview ||
    workspace.project?.dataOrigin === "local_upload"
  );
}

// 历史项目区只保留两类项目：
//   1) 匹配模块解析过的完整招标文件（document.kind === "tender_document"）
//   2) 标书工作台上传的项目（dataOrigin === "cloud_agent"，经 /api/bid-runs 上传解析）
// 仅做过公告匹配（监控爬取的公告、匹配模块只导入公告而未做招标文件细匹配）的项目不再进入历史项目区。
function isBidHistoryWorkspace(workspace: Workspace): boolean {
  // 已经生成过标书目录的项目始终保留，避免用户丢失正在编辑的标书
  if (workspace.bidDocument?.plan?.length) return true;
  // 匹配模块解析过的完整招标文件
  if (workspace.project?.document?.kind === "tender_document") return true;
  // 标书工作台上传的项目（防御性判断，materialize 产物一定带 tender_document）
  return String(workspace.project?.dataOrigin || "") === "cloud_agent";
}

const historyWorkspaces = computed(() => {
  const query = historyQuery.value.trim().toLowerCase();
  return [...props.workspaces]
    .filter((workspace) => {
      const title = String(workspace.title || workspace.project?.title || "").toLowerCase();
      const projectId = String(workspace.projectId || "").toLowerCase();
      const matchesQuery = !query || title.includes(query) || projectId.includes(query);
      const hasPlan = Boolean(workspace.bidDocument?.plan?.length);
      const isParsed = isWorkspaceParsed(workspace);
      const eligible = isBidHistoryWorkspace(workspace);
      const matchesFilter =
        historyFilter.value === "all" ||
        (historyFilter.value === "with_plan" && hasPlan) ||
        (historyFilter.value === "parsed" && isParsed);
      return eligible && matchesQuery && matchesFilter;
    })
    .sort((a, b) => {
      const aSelected = a.id === props.workspace?.id ? 1 : 0;
      const bSelected = b.id === props.workspace?.id ? 1 : 0;
      if (aSelected !== bSelected) return bSelected - aSelected;
      return Number(Boolean(b.bidDocument?.plan?.length)) - Number(Boolean(a.bidDocument?.plan?.length));
  });
});

const selectedHistoryWorkspace = computed(
  () => historyWorkspaces.value.find((workspace) => workspace.id === selectedHistoryWorkspaceId.value) || null
);

async function confirmDeleteWorkspace(item: Workspace) {
  const label = item.title || item.project?.title || item.projectId || "该项目";
  try {
    await ElMessageBox.confirm(
      `确认删除「${label}」？将同时删除它的目录、章节内容、解析产物和关联抓取项目，删除后不可恢复。`,
      "删除项目",
      {
        type: "warning",
        confirmButtonText: "删除",
        cancelButtonText: "取消",
        confirmButtonClass: "el-button--danger",
      }
    );
  } catch {
    return;
  }
  emit("delete-workspace", item);
}

function historyStatus(workspace: Workspace): string {
  if (workspace.bidDocument?.plan?.length) return "目录已保存";
  if (isWorkspaceParsed(workspace)) return "已解析，待生成目录";
  return "已保存";
}

function historyStatusType(workspace: Workspace): "success" | "warning" | "info" {
  if (workspace.bidDocument?.plan?.length) return "success";
  if (workspace.parseBundleRef || workspace.reasoning?.fineReview) return "warning";
  return "info";
}

function historyClauseCount(workspace: Workspace): number {
  return workspace.reasoning?.fineReview?.clauseCount ||
    workspace.reasoning?.fineReview?.rows?.length ||
    workspace.reasoning?.rows?.length ||
    0;
}

const blanksToFill = computed(() => {
  const set = new Set<string>();
  plan.value.forEach((s) => (s.blanksToFill || []).forEach((b) => set.add(b)));
  return [...set];
});
const templateFragmentCount = computed(() => bidDocument.value?.templateFragments?.length || 0);

const sectionNumberMap = computed(() => {
  const counters = new Map<string, number>();
  const numbers = new Map<string, string>();

  for (const section of plan.value) {
    const parentId = String(section.parentSectionId || "");
    const next = (counters.get(parentId) || 0) + 1;
    counters.set(parentId, next);
    const parentNumber = parentId ? numbers.get(parentId) || "" : "";
    numbers.set(
      section.sectionId,
      parentNumber ? `${parentNumber}.${next}` : String(next)
    );
  }

  return numbers;
});

function navigationSectionHasChildren(sectionId: string): boolean {
  return plan.value.some(
    (section) => String(section.parentSectionId || "") === sectionId
  );
}

function navigationSectionCollapsed(sectionId: string): boolean {
  return collapsedNavSectionIds.value.has(sectionId);
}

function toggleNavigationSection(sectionId: string) {
  const next = new Set(collapsedNavSectionIds.value);
  if (next.has(sectionId)) next.delete(sectionId);
  else next.add(sectionId);
  collapsedNavSectionIds.value = next;
}

function navigationSectionVisible(section: BidSection): boolean {
  const byId = new Map(plan.value.map((item) => [item.sectionId, item]));
  const visited = new Set<string>();
  let parentId = String(section.parentSectionId || "");
  while (parentId && !visited.has(parentId)) {
    if (collapsedNavSectionIds.value.has(parentId)) return false;
    visited.add(parentId);
    parentId = String(byId.get(parentId)?.parentSectionId || "");
  }
  return true;
}

const visibleNavigationSections = computed(() =>
  plan.value.filter(navigationSectionVisible)
);

const planLevelStats = computed(() => {
  const counts = new Map<number, number>();
  let maxLevel = 1;
  for (const section of plan.value) {
    const level = Math.max(Number(section.level || 1), 1);
    counts.set(level, (counts.get(level) || 0) + 1);
    maxLevel = Math.max(maxLevel, level);
  }
  const visibleMax = Math.min(maxLevel, 5);
  return {
    total: plan.value.length,
    levels: Array.from({ length: visibleMax }, (_, index) => ({
      level: index + 1,
      count: counts.get(index + 1) || 0
    })),
    overflow: maxLevel > 5
      ? [...counts.entries()]
          .filter(([level]) => level > 5)
          .reduce((total, [, count]) => total + count, 0)
      : 0
  };
});

type PlanTreeNode = { id: string; section: BidSection; children: PlanTreeNode[] };
const treePlan = computed<PlanTreeNode[]>(() => {
  const nodes = new Map(plan.value.map((section) => [
    section.sectionId, { id: section.sectionId, section, children: [] as PlanTreeNode[] }
  ]));
  const roots: PlanTreeNode[] = [];
  for (const section of plan.value) {
    const node = nodes.get(section.sectionId)!;
    const parent = nodes.get(section.parentSectionId || "");
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return roots;
});

/** 目录智能体据证据新增的章节（M2′）：直接取自已保存的目录。 */
const derivedPlanSections = computed(() =>
  plan.value.filter((section) => section.derived && section.nodeType !== "volume" && section.nodeType !== "format_item")
);

function sectionRole(section: BidSection): string {
  if (section.nodeType === "volume") return "卷";
  if (section.nodeType === "format_item") return "文件";
  return roleForLevel(Number(section.level || 1));
}

function roleForLevel(level: number): string {
  return ["", "一级", "章", "节", "目", "条", "款", "项"][level] || `第${level}级`;
}

function sectionChildren(sectionId: string): BidSection[] {
  return plan.value.filter((section) => String(section.parentSectionId || "") === sectionId);
}

function canAddChild(section: BidSection): boolean {
  return Number(section.level || 1) < MAX_PLAN_LEVEL;
}

function canAddSibling(section: BidSection): boolean {
  return section.nodeType === "volume" || Boolean(section.parentSectionId) || Number(section.level || 1) === 1;
}

function resetInlineAdd() {
  inlineAddTargetId.value = "";
  inlineAddTitle.value = "";
}

function beginInlineAdd(section: BidSection, mode: "child" | "sibling") {
  if (mode === "child" && !canAddChild(section)) {
    ElMessage.info(`目录最多支持 ${MAX_PLAN_LEVEL} 级`);
    return;
  }
  if (mode === "sibling" && !canAddSibling(section)) {
    ElMessage.info("当前节点不能添加同级章节");
    return;
  }
  if (inlineAddTargetId.value === section.sectionId && inlineAddMode.value === mode) {
    resetInlineAdd();
    return;
  }
  inlineAddTargetId.value = section.sectionId;
  inlineAddMode.value = mode;
  inlineAddTitle.value = "";
  void nextTick(() => inlineAddTitleInput.value?.focus());
}

function handleAddCommand(section: BidSection, command: string | number) {
  if (command === "child" || command === "sibling") {
    beginInlineAdd(section, command);
  }
}

function newSectionId(): string {
  return `s-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

function commitInlineAdd(section: BidSection) {
  const title = inlineAddTitle.value.trim();
  if (!title) {
    ElMessage.warning("请输入章节标题");
    void nextTick(() => inlineAddTitleInput.value?.focus());
    return;
  }

  const mode = inlineAddMode.value;
  const parentId = mode === "child"
    ? section.sectionId
    : String(section.parentSectionId || "");
  const parent = plan.value.find((item) => item.sectionId === parentId);
  const isVolume = mode === "sibling" && section.nodeType === "volume";
  const id = newSectionId();
  const siblings = sectionChildren(parentId);
  const selectedIndex = siblings.findIndex((item) => item.sectionId === section.sectionId);
  const insertOrder = mode === "sibling" && selectedIndex >= 0
    ? selectedIndex + 2
    : siblings.length + 1;
  const inheritedKind = isVolume
    ? section.kind || "technical"
    : parent?.nodeType === "volume" && ["technical", "commercial", "attachment"].includes(parent.kind)
      ? parent.kind
      : section.kind || "technical";
  const inheritedSource = isVolume
    ? "template"
    : section.source || (inheritedKind === "technical" ? "ai" : "template");

  const next: BidSection = {
    sectionId: id,
    title,
    source: inheritedSource,
    kind: inheritedKind,
    ...(isVolume ? { nodeType: "volume" as const, volumeId: id } : {}),
    ...(!isVolume && volumeIdForParent(parent) ? { volumeId: volumeIdForParent(parent) } : {}),
    responseType: isVolume ? "section_group" : "chapter_text",
    parentSectionId: parentId,
    level: parent ? Number(parent.level || 1) + 1 : 1,
    order: insertOrder,
    content: "",
    status: "pending",
    requirementIds: [],
    sourceRefs: []
  };

  const shifted = plan.value.map((item) => {
    if (String(item.parentSectionId || "") === parentId && Number(item.order || 0) >= insertOrder) {
      return { ...item, order: Number(item.order || 0) + 1 };
    }
    return item;
  });
  replacePlan([...shifted, next]);
  markPlanDirty();
  resetInlineAdd();
  ElMessage.success(`已添加${isVolume ? "卷" : mode === "child" ? "子级章节" : "同级章节"}`);
}

function volumeIdForParent(parent?: BidSection): string {
  let current = parent;
  const visited = new Set<string>();
  while (current && !visited.has(current.sectionId)) {
    visited.add(current.sectionId);
    if (current.nodeType === "volume") {
      return String(current.volumeId || current.sectionId);
    }
    current = plan.value.find(
      (item) => item.sectionId === String(current?.parentSectionId || "")
    );
  }
  return "";
}

function isDescendant(candidateId: string, ancestorId: string): boolean {
  let current = plan.value.find((section) => section.sectionId === candidateId);
  const visited = new Set<string>();
  while (current?.parentSectionId) {
    const parentId = String(current.parentSectionId);
    if (parentId === ancestorId) return true;
    if (visited.has(parentId)) return false;
    visited.add(parentId);
    current = plan.value.find((section) => section.sectionId === parentId);
  }
  return false;
}

function canMoveSection(item: BidSection, dir: -1 | 1): boolean {
  const parentId = String(item.parentSectionId || "");
  const siblings = plan.value.filter(
    (section) => String(section.parentSectionId || "") === parentId
  );
  const position = siblings.findIndex((section) => section.sectionId === item.sectionId);
  return position >= 0 && position + dir >= 0 && position + dir < siblings.length;
}

function normalizePlanForEditor(items: BidSection[]): BidSection[] {
  const rows = items.map((item, index) => ({ item: { ...item }, index }));
  const ids = new Set(rows.map(({ item }) => item.sectionId));
  const parentById = new Map<string, string>();

  for (const { item } of rows) {
    let parentId = String(item.parentSectionId || "");
    if (!parentId || parentId === item.sectionId || !ids.has(parentId)) parentId = "";
    const seen = new Set<string>();
    let cursor = parentId;
    while (cursor) {
      if (cursor === item.sectionId || seen.has(cursor)) {
        parentId = "";
        break;
      }
      seen.add(cursor);
      cursor = String(rows.find(({ item: row }) => row.sectionId === cursor)?.item.parentSectionId || "");
    }
    parentById.set(item.sectionId, parentId);
  }

  const children = new Map<string, typeof rows>();
  for (const entry of rows) {
    const parentId = parentById.get(entry.item.sectionId) || "";
    const group = children.get(parentId) || [];
    group.push(entry);
    children.set(parentId, group);
  }
  for (const group of children.values()) {
    group.sort((a, b) => {
      const aOrder = Number(a.item.order || 0);
      const bOrder = Number(b.item.order || 0);
      return (aOrder || 999999) - (bOrder || 999999) || a.index - b.index;
    });
  }

  const result: BidSection[] = [];
  const visit = (parentId: string, level: number) => {
    (children.get(parentId) || []).forEach((entry, index) => {
      const row = entry.item;
      row.parentSectionId = parentId;
      row.level = level;
      row.order = index + 1;
      result.push(row);
      visit(row.sectionId, level + 1);
    });
  };
  visit("", 1);
  return result;
}

function replacePlan(items: BidSection[]) {
  if (bidDocument.value) bidDocument.value.plan = normalizePlanForEditor(items);
}

function sectionNumber(section?: BidSection, fallbackIndex = 0): string {
  return sectionNumberMap.value.get(section?.sectionId || "") || String(fallbackIndex + 1);
}

function sectionIndentLevel(section?: BidSection): number {
  const level = Number(section?.level || 1);
  return Number.isFinite(level) && level > 1 ? level - 1 : 0;
}

function sectionIndentStyle(section?: BidSection): Record<string, string> {
  return { paddingLeft: `${sectionIndentLevel(section) * 18}px` };
}

const projectInfo = computed<Record<string, string>>(() => {
  const p = props.project;
  if (!p) return {};
  const out: Record<string, string> = {};
  const set = (k: string, v: string | undefined) => {
    if (v) out[k] = v;
  };
  set("项目名称", p.projectName || p.title);
  set("地区", p.region);
  set("公告类型", p.noticeType);
  set("发布时间", p.publishedDate);
  set("报名截止", p.registrationDeadline);
  set("投标截止", p.bidDeadline);
  set("来源", p.source);
  return out;
});

// ---- 步骤导航 ----
function goStep(step: number) {
  activeStep.value = step;
}

function openHistoryWorkspace(workspace: Workspace) {
  selectedHistoryWorkspaceId.value = workspace.id;
  activeStep.value = workspace.bidDocument?.plan?.length ? 2 : 1;
  emit("select-workspace", workspace);
}

async function reuseMatchingWorkspace(workspace: Workspace) {
  if (!isMatchingTenderWorkspace(workspace)) {
    ElMessage.warning("该项目没有完整招标文件匹配评估结果");
    return;
  }
  selectedHistoryWorkspaceId.value = workspace.id;
  historyActionWorkspaceId.value = workspace.id;
  try {
    const result = await api.reuseMatchAnalysis(workspace.id);
    activeStep.value = 1;
    emit("select-workspace", result.workspace);
    planning.value = true;
    const planned = await api.bidDocumentPlan(workspace.id);
    bidDocument.value = planned.bidDocument;
    emit("select-workspace", planned.workspace);
    emit("update:bid-document", planned.bidDocument);
    activeStep.value = 2;
    ElMessage.success("已复用匹配评估结果并生成目录，可继续撰写正文");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    planning.value = false;
    historyActionWorkspaceId.value = "";
  }
}

async function generateHistoryToc(workspace: Workspace) {
  selectedHistoryWorkspaceId.value = workspace.id;
  emit("select-workspace", workspace);
  activeStep.value = 1;
  historyActionWorkspaceId.value = workspace.id;
  planning.value = true;
  try {
    const result = await api.bidDocumentPlan(workspace.id);
    bidDocument.value = result.bidDocument;
    emit("select-workspace", result.workspace);
    emit("update:bid-document", result.bidDocument);
    activeStep.value = 2;
    ElMessage.success(`已生成目录：${planSourceLabel(planningSourceKey(result.bidDocument))}`);
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    planning.value = false;
    historyActionWorkspaceId.value = "";
  }
}

function historyActionLabel(workspace: Workspace): string {
  if (workspace.bidDocument?.plan?.length) return "继续编辑";
  if (isMatchingTenderWorkspace(workspace)) return "复用评估结果并生成目录";
  if (isWorkspaceParsed(workspace)) return "继续生成目录";
  return "打开项目";
}

function runHistoryAction(workspace: Workspace) {
  if (workspace.bidDocument?.plan?.length) {
    openHistoryWorkspace(workspace);
  } else if (isMatchingTenderWorkspace(workspace)) {
    void reuseMatchingWorkspace(workspace);
  } else if (isWorkspaceParsed(workspace)) {
    void generateHistoryToc(workspace);
  } else {
    openHistoryWorkspace(workspace);
  }
}

function chooseFile() {
  fileInput.value?.click();
}
function onFileChange(event: Event) {
  emit("file-selected", (event.target as HTMLInputElement).files?.[0] || null);
}
function startImport() {
  void startCloudParse();
}

function stopCloudPolling() {
  if (cloudPollingTimer !== null) {
    window.clearInterval(cloudPollingTimer);
    cloudPollingTimer = null;
  }
}

function isCloudRunTerminal(status?: string): boolean {
  return ["outline_ready", "completed", "failed", "cancelled"].includes(status || "");
}

function cloudStatusName(status?: string): string {
  return (
    {
      created: "任务已创建",
      uploading: "上传文件",
      uploaded: "文件已上传",
      parsing: "智能体解析中",
      outline_ready: "解析完成，待生成目录",
      completed: "任务完成",
      failed: "解析失败",
      cancelled: "任务已取消"
    }[status || ""] || status || "等待处理"
  );
}

async function refreshCloudRun(runId: string) {
  const result = await api.getBidRun(runId);
  cloudRun.value = result.run;
  if (["outline_ready", "completed"].includes(result.run.status) && !result.run.materialized) {
    await materializeCloudRun(runId);
  }
  if (isCloudRunTerminal(result.run.status)) stopCloudPolling();
}

async function materializeCloudRun(runId: string) {
  if (cloudMaterializing.value) return;
  cloudMaterializing.value = true;
  try {
    const result = await api.materializeBidRun(runId);
    cloudRun.value = result.run;
    bidDocument.value = result.workspace.bidDocument || null;
    if (result.workspace.bidDocument) {
      emit("update:bid-document", result.workspace.bidDocument);
      activeStep.value = 1;
    }
    emit("cloud-workspace-ready", {
      workspace: result.workspace,
      project: result.project
    });
    ElMessage.success("解析结果已接入标书工作区");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    cloudMaterializing.value = false;
  }
}

async function startCloudParse() {
  const file = props.importFile;
  if (!file) {
    ElMessage.warning("请先选择招标文件");
    return;
  }
  activeStep.value = 1;
  stopCloudPolling();
  cloudRun.value = null;
  try {
    // A newly uploaded file is a new tender document. Do not reuse the
    // currently selected historical workspace, otherwise materialization can
    // overwrite that workspace and a later refresh can show its old outline.
    const result = await api.createBidRun(file);
    cloudRun.value = result.run;
    await refreshCloudRun(result.run.runId);
    if (!isCloudRunTerminal(cloudRun.value.status)) {
      cloudPollingTimer = window.setInterval(() => {
        void refreshCloudRun(result.run.runId).catch((error) => {
          stopCloudPolling();
          ElMessage.error((error as Error).message);
        });
      }, 2000);
    }
    ElMessage.success("已提交云端智能体解析任务");
  } catch (error) {
    ElMessage.error((error as Error).message);
  }
}

onMounted(() => {
  window.addEventListener("scroll", syncEditorColumnHeight, { passive: true });
  window.addEventListener("resize", syncEditorColumnHeight);
  void nextTick(syncEditorColumnHeight);
});

onBeforeUnmount(() => {
  stopCloudPolling();
  window.removeEventListener("scroll", syncEditorColumnHeight);
  window.removeEventListener("resize", syncEditorColumnHeight);
  if (editorColumnHeightFrame) cancelAnimationFrame(editorColumnHeightFrame);
});

// ---- 第一步 → 生成目录 ----
async function generateToc() {
  if (!workspaceId.value) {
    ElMessage.warning("请先选择或上传招标文件");
    return;
  }
  planning.value = true;
  try {
    const result = await api.bidDocumentPlan(workspaceId.value);
    bidDocument.value = result.bidDocument;
    emit("update:bid-document", result.bidDocument);
    activeStep.value = 2;
    ElMessage.success(`已生成目录：${planSourceLabel(planningSourceKey(result.bidDocument))}`);
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    planning.value = false;
  }
}

// ---- 第二步：目录编辑 ----
function moveSection(item: BidSection, dir: -1 | 1) {
  const index = plan.value.findIndex((section) => section.sectionId === item.sectionId);
  if (index < 0) return;
  const parentId = String(item.parentSectionId || "");
  const siblingIndexes = plan.value
    .map((section, rowIndex) => ({ section, rowIndex }))
    .filter(({ section }) => String(section.parentSectionId || "") === parentId)
    .map(({ rowIndex }) => rowIndex);
  const siblingPosition = siblingIndexes.indexOf(index);
  const targetPosition = siblingPosition + dir;
  if (siblingPosition < 0 || targetPosition < 0 || targetPosition >= siblingIndexes.length) return;
  const targetIndex = siblingIndexes[targetPosition];
  const arr = [...plan.value];
  arr[index] = { ...arr[index], order: targetPosition + 1 };
  arr[targetIndex] = { ...arr[targetIndex], order: siblingPosition + 1 };
  replacePlan(arr);
  markPlanDirty();
}
function removeSection(item: BidSection) {
  const descendants = plan.value.filter((section) => isDescendant(section.sectionId, item.sectionId));
  if (descendants.length && !window.confirm(`删除“${item.title}”将同时删除其 ${descendants.length} 个子章节，是否继续？`)) {
    return;
  }
  const removedIds = new Set([item.sectionId, ...descendants.map((section) => section.sectionId)]);
  replacePlan(plan.value.filter((section) => !removedIds.has(section.sectionId)));
  markPlanDirty();
}
function markPlanDirty() {
  if (bidDocument.value) bidDocument.value.plan = [...plan.value];
}
async function savePlan() {
  if (!workspaceId.value) return;
  savingPlan.value = true;
  try {
    const normalized = normalizePlanForEditor([...plan.value]);
    const result = await api.bidDocumentSavePlan(workspaceId.value, normalized);
    bidDocument.value = result.bidDocument;
    emit("update:bid-document", result.bidDocument);
    ElMessage.success("目录已保存");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    savingPlan.value = false;
  }
}

// ---- 第三步：正文编辑 ----
function openJobSection(id: string) {
  goStep(3);
  void nextTick(() => scrollToSection(id));
}

function scrollToSection(id: string) {
  if (!plan.value.some((section) => section.sectionId === id)) return;
  const expanded = new Set(collapsedNavSectionIds.value);
  sectionAncestorIds(plan.value, id).forEach((parentId) => expanded.delete(parentId));
  collapsedNavSectionIds.value = expanded;
  currentSectionId.value = id;
  syncEditorFromSection(id);
  requestAnimationFrame(() => {
    document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}

function selectDocSection(id: string) {
  currentSectionId.value = id;
  syncEditorFromSection(id);
}

function deselect() {
  currentSectionId.value = "";
  editorHtml.value = "";
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function renderInline(s: string): string {
  let out = s;
  // 内联图片必须先于链接处理，否则 ![图注](url) 会被当成链接并遗留一个 "!"。
  out = out.replace(
    /!\[([^\]]*)\]\(([^)\s]+)\)/g,
    '<img class="evidence-inline-image" src="$2" alt="$1" loading="lazy" />'
  );
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/【(需人工填写|待补充|需人工补充|注意)[^】]*】/g, "<mark>$&</mark>");
  return out;
}

function renderMarkdown(md: string): string {
  if (!md) return "";
  const lines = md.split("\n");
  const out: string[] = [];
  let list: "ul" | "ol" | null = null;
  let para: string[] = [];
  let tableRows: string[][] = [];

  const splitTableRow = (value: string): string[] => {
    const trimmed = value.trim().replace(/^\|/, "").replace(/\|$/, "");
    return trimmed.split("|").map((cell) => cell.trim());
  };
  const isTableSeparator = (value: string): boolean =>
    splitTableRow(value).length > 0 &&
    splitTableRow(value).every((cell) => /^:?-{3,}:?$/.test(cell));
  const flushTable = () => {
    if (!tableRows.length) return;
    const header = tableRows[0];
    const body = tableRows.slice(1);
    out.push(
      `<table><thead><tr>${header
        .map((cell) => `<th>${renderInline(escapeHtml(cell))}</th>`)
        .join("")}</tr></thead><tbody>${body
        .map(
          (row) =>
            `<tr>${row
              .map((cell) => `<td>${renderInline(escapeHtml(cell))}</td>`)
              .join("")}</tr>`
        )
        .join("")}</tbody></table>`
    );
    tableRows = [];
  };
  const flushPara = () => {
    if (para.length) {
      out.push(`<p>${renderInline(escapeHtml(para.join(" ")))}</p>`);
      para = [];
    }
  };
  const closeList = () => {
    if (list) {
      out.push(list === "ul" ? "</ul>" : "</ol>");
      list = null;
    }
  };
  for (const raw of lines) {
    if (/^\s*\|.+\|\s*$/.test(raw)) {
      flushPara();
      closeList();
      if (isTableSeparator(raw)) {
        continue;
      }
      tableRows.push(splitTableRow(raw));
      continue;
    }
    flushTable();
    const heading = raw.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      flushPara();
      closeList();
      out.push(`<h${heading[1].length}>${renderInline(escapeHtml(heading[2]))}</h${heading[1].length}>`);
      continue;
    }
    const quote = raw.match(/^>\s?(.*)$/);
    if (quote) {
      flushPara();
      closeList();
      out.push(`<blockquote>${renderInline(escapeHtml(quote[1]))}</blockquote>`);
      continue;
    }
    const imageLine = raw.match(/^\s*!\[([^\]]*)\]\(([^)\s]+)\)\s*$/);
    if (imageLine) {
      flushPara();
      closeList();
      out.push(
        `<p class="evidence-image-block"><img class="evidence-inline-image" src="${escapeHtml(
          imageLine[2]
        )}" alt="${escapeHtml(imageLine[1])}" loading="lazy" /></p>`
      );
      continue;
    }
    const ul = raw.match(/^\s*[-*]\s+(.*)$/);
    if (ul) {
      flushPara();
      if (list !== "ul") {
        closeList();
        out.push("<ul>");
        list = "ul";
      }
      out.push(`<li>${renderInline(escapeHtml(ul[1]))}</li>`);
      continue;
    }
    const ol = raw.match(/^\s*\d+[.)]\s+(.*)$/);
    if (ol) {
      flushPara();
      if (list !== "ol") {
        closeList();
        out.push("<ol>");
        list = "ol";
      }
      out.push(`<li>${renderInline(escapeHtml(ol[1]))}</li>`);
      continue;
    }
    if (/^\s*$/.test(raw)) {
      flushPara();
      closeList();
      continue;
    }
    para.push(raw);
  }
  flushPara();
  closeList();
  flushTable();
  return out.join("\n");
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asRecordList(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    : [];
}

function numericValue(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function templateParagraphStyle(block: Record<string, unknown>): string {
  const style = asRecord(block.style);
  const rules: string[] = [];
  const alignment = String(style.alignment || "").toLowerCase();
  if (["left", "center", "right", "both", "justify"].includes(alignment)) {
    rules.push(`text-align:${alignment === "both" ? "justify" : alignment}`);
  }
  const fontSize = Array.isArray(style.fontSizes)
    ? numericValue(style.fontSizes[0])
    : numericValue(style.fontSize);
  if (fontSize && fontSize > 0 && fontSize < 96) {
    rules.push(`font-size:${fontSize}pt`);
  }
  const fontName = Array.isArray(style.fontNames)
    ? String(style.fontNames[0] || "")
    : String(style.fontName || "");
  if (fontName && !/[;"{}]/.test(fontName)) {
    rules.push(`font-family:"${fontName}"`);
  }
  if (style.bold === true) rules.push("font-weight:700");
  if (style.italic === true) rules.push("font-style:italic");
  const firstLineIndent = numericValue(style.firstLineIndent);
  if (firstLineIndent && firstLineIndent > 0) {
    rules.push(`text-indent:${Math.round(firstLineIndent / 15)}px`);
  }
  const leftIndent = numericValue(style.leftIndent);
  if (leftIndent && leftIndent > 0) {
    rules.push(`padding-left:${Math.round(leftIndent / 15)}px`);
  }
  const lineSpacing = numericValue(style.lineSpacing);
  if (lineSpacing && lineSpacing > 0) {
    rules.push(`line-height:${Math.max(1.2, lineSpacing / 240)}`);
  }
  return rules.join(";");
}

function editableText(element: HTMLElement): string {
  return element.innerText.replace(/\r/g, "").replace(/\n$/, "");
}

function renderTemplateTable(
  table: Record<string, unknown>,
  responseTable?: Record<string, unknown>
): string {
  const rows = asRecordList(table.rows);
  if (!rows.length) return "";
  const tableId = String(table.tableId || table.id || "");
  const clonedRows: Record<string, unknown>[] = rows.map((row) => ({
    ...row,
    cells: asRecordList(row.cells).map((cell) => ({ ...cell }))
  }));
  const spec = responseTable || {};
  const columns = asRecord(spec.columns);
  const requirementColumn = numericValue(columns.requirement) || 0;
  const responseColumn = numericValue(columns.response) || 0;
  const headerRows = numericValue(spec.headerRowCount) || 1;
  const usedRows = new Set<number>();

  const applyResponse = (row: Record<string, unknown>, entry: Record<string, unknown>) => {
    const cells = asRecordList(row.cells);
    for (const value of asRecordList(entry.cells)) {
      const cellIndex = numericValue(value.cellIndex);
      const cell = cellIndex
        ? cells.find((item) => numericValue(item.cellIndex) === cellIndex)
        : undefined;
      if (cell) cell.filledText = String(value.value || "");
    }
  };

  for (const entry of asRecordList(spec.rows)) {
    const rowIndex = numericValue(entry.rowIndex);
    const row = rowIndex
      ? clonedRows.find((item) => numericValue(item.rowIndex) === rowIndex)
      : undefined;
    if (row) {
      applyResponse(row, entry);
      usedRows.add(rowIndex!);
    }
  }

  const isBlankSlot = (row: Record<string, unknown>) => {
    const cells = asRecordList(row.cells);
    const textAt = (index: number) => {
      const cell = cells.find((item) => numericValue(item.cellIndex) === index);
      return String(cell?.filledText ?? cell?.text ?? "").replace(/\s/g, "");
    };
    const blank = (text: string) =>
      !text || /待填写|投标人填写|供应商填写|待补充/.test(text) || /^[_＿—\-□☐（）()：:]*$/.test(text);
    return requirementColumn > 0 && responseColumn > 0 &&
      blank(textAt(requirementColumn)) && blank(textAt(responseColumn));
  };

  const pending = asRecordList(spec.appendRows).slice();
  const dataStart = Math.max(numericValue(spec.dataStartRowIndex) || 1, 1);
  for (const row of clonedRows) {
    const rowIndex = numericValue(row.rowIndex) || 0;
    if (!pending.length) break;
    if (rowIndex < dataStart || usedRows.has(rowIndex) || !isBlankSlot(row)) continue;
    applyResponse(row, pending.shift()!);
    usedRows.add(rowIndex);
  }

  const insertAfter = Math.max(
    numericValue(spec.insertAfterRowIndex) || headerRows,
    headerRows
  );
  const prototypeIndex = numericValue(spec.prototypeRowIndex) || dataStart;
  if (pending.length) {
    clonedRows.forEach((row) => {
      const rowIndex = numericValue(row.rowIndex) || 0;
      if (rowIndex > insertAfter) row.rowIndex = rowIndex + pending.length;
    });
  }
  let nextRowIndex = insertAfter;
  for (const entry of pending) {
    const prototype = clonedRows.find(
      (row) => numericValue(row.rowIndex) === prototypeIndex
    ) || clonedRows[clonedRows.length - 1];
    if (!prototype) break;
    nextRowIndex += 1;
    const row = {
      ...prototype,
      rowIndex: nextRowIndex,
      cells: asRecordList(prototype.cells).map((cell) => ({ ...cell }))
    };
    for (const cell of asRecordList(row.cells)) {
      cell.filledText = "";
    }
    applyResponse(row, entry);
    clonedRows.push(row);
  }

  clonedRows.sort(
    (left, right) => (numericValue(left.rowIndex) || 0) - (numericValue(right.rowIndex) || 0)
  );
  const gridWidths = Array.isArray(table.gridWidths)
    ? table.gridWidths.map((value) => numericValue(value) || 0)
    : [];
  const gridWidthTotal = gridWidths.reduce((sum, width) => sum + width, 0);
  const colgroup = gridWidthTotal > 0
    ? `<colgroup>${gridWidths.map((width) => `<col style="width:${Math.max(1, width / gridWidthTotal * 100)}%">`).join("")}</colgroup>`
    : "";
  const renderedRows = clonedRows
    .map((row, rowIndex) => {
      const rowNumber = numericValue(row.rowIndex) || rowIndex + 1;
      const cells = asRecordList(row.cells);
      const renderedCells = cells
        .map((cell, cellPosition) => {
          const cellIndex = numericValue(cell.cellIndex) || cellPosition + 1;
          const tag = rowNumber <= headerRows ? "th" : "td";
          const span = numericValue(cell.gridSpan);
          const colspan = span && span > 1 ? ` colspan="${Math.round(span)}"` : "";
          const rawText = String(cell.filledText ?? cell.text ?? "");
          const text = escapeHtml(rawText).replace(/\r?\n/g, "<br>");
          return `<${tag} data-template-cell-index="${cellIndex}"${colspan}>${renderInline(text)}</${tag}>`;
        })
        .join("");
      return `<tr data-template-row-index="${rowNumber}">${renderedCells}</tr>`;
    })
    .join("");
  return `<table class="template-table" data-template-table-id="${escapeHtml(tableId)}">${colgroup}<tbody>${renderedRows}</tbody></table>`;
}

function renderTemplateFragment(section: BidSection): string {
  const blocks = asRecordList(section.templateBlocks);
  const tables = asRecordList(section.templateTables);
  if (!blocks.length && !tables.length) return renderMarkdown(section.content || "");

  const responseTable = asRecord(section.templateResponseTable);
  const items: { index: number; html: string }[] = [];
  for (const block of blocks) {
    const contentType = String(block.contentType || "").toLowerCase();
    if (contentType.startsWith("table")) continue;
    const text = String(block.text || "");
    if (!text) continue;
    const index = numericValue(block.bodyChildIndex) ?? numericValue(block.order) ?? items.length;
    const style = templateParagraphStyle(block);
    items.push({
      index,
      html: `<p class="template-paragraph" data-template-block-index="${index}"${style ? ` style="${escapeHtml(style)}"` : ""}>${renderInline(
        escapeHtml(text).replace(/\r?\n/g, "<br>")
      )}</p>`
    });
  }
  for (const table of tables) {
    const tableId = String(table.tableId || table.id || "");
    const html = renderTemplateTable(
      table,
      String(responseTable.tableId || "") === tableId ? responseTable : undefined
    );
    if (!html) continue;
    items.push({
      index: numericValue(table.bodyChildIndex) ?? numericValue(table.order) ?? items.length,
      html
    });
  }
  items.sort((a, b) => a.index - b.index);
  return `<div class="template-page">${items.map((item) => item.html).join("")}</div>`;
}

function hasVisualTemplate(section?: BidSection): boolean {
  return Boolean(
    section?.templateFragmentId &&
      ((section.templateBlocks?.length || 0) > 0 || (section.templateTables?.length || 0) > 0)
  );
}

function sectionHasContent(section?: BidSection): boolean {
  if (!section || section.responseType === "section_group") return false;
  if (section.content?.trim()) return true;
  return hasVisualTemplate(section);
}

function syncEditorFromSection(sectionId: string) {
  const section = plan.value.find((item) => item.sectionId === sectionId);
  if (!section) {
    editorHtml.value = "";
    return;
  }
  editorHtml.value = hasVisualTemplate(section)
    ? renderTemplateFragment(section)
    : renderMarkdown(section.content || "");
}

function updateSectionFromEditor(event: Event, section: BidSection) {
  const target = event.currentTarget as HTMLElement | null;
  if (!target) return;
  if (hasVisualTemplate(section)) {
    const blocks = asRecordList(section.templateBlocks);
    target.querySelectorAll<HTMLElement>("[data-template-block-index]").forEach((element) => {
      const bodyChildIndex = Number(element.dataset.templateBlockIndex);
      const block = blocks.find((item) => numericValue(item.bodyChildIndex) === bodyChildIndex);
      if (block) block.text = editableText(element);
    });

    const tables = asRecordList(section.templateTables);
    target.querySelectorAll<HTMLElement>("table[data-template-table-id]").forEach((element) => {
      const tableId = element.dataset.templateTableId || "";
      const table = tables.find((item) => String(item.tableId || item.id || "") === tableId);
      if (!table) return;
      const rows = asRecordList(table.rows);
      element.querySelectorAll<HTMLElement>("tr[data-template-row-index]").forEach((rowElement) => {
        const rowIndex = Number(rowElement.dataset.templateRowIndex);
        let row = rows.find((item) => numericValue(item.rowIndex) === rowIndex);
        if (!row) {
          const prototype = rows[rows.length - 1];
          if (!prototype) return;
          row = {
            ...prototype,
            rowIndex,
            cells: asRecordList(prototype.cells).map((cell) => ({ ...cell }))
          };
          rows.push(row);
        }
        const cells = asRecordList(row.cells);
        rowElement.querySelectorAll<HTMLElement>("[data-template-cell-index]").forEach((cellElement) => {
          const cellIndex = Number(cellElement.dataset.templateCellIndex);
          let cell = cells.find((item) => numericValue(item.cellIndex) === cellIndex);
          if (!cell) {
            cell = { cellIndex };
            cells.push(cell);
          }
          cell.filledText = editableText(cellElement);
        });
      });
    });
    section.content = htmlToMarkdown(target);
    section.contentMatchesTemplate = true;
  } else {
    section.content = htmlToMarkdown(target);
    if (section.templateFragmentId) {
    section.contentMatchesTemplate = false;
    }
  }
  if (section.content.trim()) {
    section.status = "generated";
  }
}

function htmlToMarkdown(root: HTMLElement): string {
  const renderNode = (node: Node): string => {
    if (node.nodeType === Node.TEXT_NODE) {
      return node.textContent || "";
    }
    if (!(node instanceof HTMLElement)) return "";
    const children = Array.from(node.childNodes).map(renderNode).join("");
    const tag = node.tagName.toLowerCase();
    if (tag === "style") return "";
    if (node.classList.contains("codex-docx-wrapper")) return children;
    if (tag === "br") return "\n";
    if (/^h[1-6]$/.test(tag)) return `${"#".repeat(Number(tag[1]))} ${children.trim()}\n\n`;
    if (tag === "p" || tag === "div") return `${children.trim()}\n\n`;
    if (tag === "strong" || tag === "b") return `**${children}**`;
    if (tag === "em" || tag === "i") return `*${children}*`;
    if (tag === "u") return `<u>${children}</u>`;
    if (tag === "blockquote") return `${children.trim().split("\n").map((line) => `> ${line}`).join("\n")}\n\n`;
    if (tag === "li") return children.trim();
    if (tag === "ul" || tag === "ol") {
      const items = Array.from(node.children)
        .filter((child): child is HTMLElement => child.tagName.toLowerCase() === "li")
        .map((child, index) => {
          const value = renderNode(child).trim();
          return tag === "ol" ? `${index + 1}. ${value}` : `- ${value}`;
        });
      return `${items.join("\n")}\n\n`;
    }
    if (tag === "img") {
      const alt = node.getAttribute("alt") || "";
      const src = node.getAttribute("src") || "";
      return src ? `![${alt}](${src})` : "";
    }
    if (tag === "table") {
      const rows = Array.from(node.querySelectorAll("tr")).map((row) =>
        Array.from(row.querySelectorAll("th,td")).map((cell) =>
          (cell.textContent || "").replace(/\|/g, "\\|").trim()
        )
      );
      if (!rows.length) return "";
      const width = Math.max(...rows.map((row) => row.length));
      const normalized = rows.map((row) => [...row, ...Array(Math.max(0, width - row.length)).fill("")]);
      return [
        `| ${normalized[0].join(" | ")} |`,
        `| ${normalized[0].map(() => "---").join(" | ")} |`,
        ...normalized.slice(1).map((row) => `| ${row.join(" | ")} |`)
      ].join("\n") + "\n\n";
    }
    return children;
  };
  return Array.from(root.childNodes)
    .map(renderNode)
    .join("")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function applyEditorCommand(command: string, value?: string) {
  document.execCommand(command, false, value);
  const active = document.activeElement;
  if (active instanceof HTMLElement && active.classList.contains("doc-rich-editor")) {
    active.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "formatBlock" }));
    active.focus();
  }
}

async function generateSection(sectionId: string) {
  if (!workspaceId.value || !sectionId) {
    ElMessage.warning("请先选择要撰写的章节");
    return;
  }
  generatingSectionId.value = sectionId;
  try {
    const result = await api.bidDocumentGenerateSection(workspaceId.value, sectionId);
    bidDocument.value = result.bidDocument;
    emit("update:bid-document", result.bidDocument);
    syncEditorFromSection(sectionId);
    ElMessage.success("本章已自动撰写，可继续编辑");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    generatingSectionId.value = "";
  }
}

function regenerateToc() {
  if (
    bidDocument.value?.plan?.length &&
    !window.confirm("重新生成会覆盖当前目录编辑结果，但不会重新解析招标文件。是否继续？")
  ) {
    return;
  }
  void generateToc();
}

async function loadEvidencePreview() {
  if (!workspaceId.value) return;
  try {
    evidencePreview.value = await api.bidDocumentEvidencePreview(workspaceId.value);
  } catch {
    evidencePreview.value = null;
  }
}

async function buildEvidenceChapter() {
  if (!workspaceId.value) {
    ElMessage.warning("请先选择工作区");
    return;
  }
  if (!bidDocument.value) {
    ElMessage.warning("请先回到上一步生成目录");
    return;
  }
  buildingEvidence.value = true;
  try {
    const result = await api.bidDocumentBuildEvidenceChapter(workspaceId.value);
    bidDocument.value = result.bidDocument;
    emit("update:bid-document", result.bidDocument);
    await loadEvidencePreview();
    evidencePanelOpen.value = true;
    ElMessage.success(result.message || "技术参数证明材料章节已生成");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    buildingEvidence.value = false;
  }
}

function openEvidenceSection() {
  goStep(3);
  void nextTick(() => scrollToSection(evidencePreview.value?.sectionId || "evidence-01"));
}

function evidenceEntryState(entry: EvidenceEntry): { type: "success" | "warning" | "danger" | "info"; label: string } {
  if (entry.status !== "ready") {
    return { type: "danger", label: "待人工补充" };
  }
  if (entry.sourceRefs.some((ref) => ref.available === false)) {
    return { type: "warning", label: "待补扫描件" };
  }
  return { type: "success", label: "已命中" };
}

function entryMaterialFiles(entry: EvidenceEntry): EvidenceAssetFile[] {
  const files: EvidenceAssetFile[] = [];
  for (const ref of entry.sourceRefs) {
    if (Array.isArray(ref.files)) files.push(...ref.files);
  }
  return files;
}

function entryMaterialFileUrls(entry: EvidenceEntry): string[] {
  return entryMaterialFiles(entry).map((file) => file.url);
}

async function fillTemplate(sectionId: string) {
  if (!workspaceId.value || !sectionId) {
    ElMessage.warning("请先选择要填充的章节");
    return;
  }
  fillingTemplateSectionId.value = sectionId;
  try {
    const result = await api.bidDocumentFillTemplate(workspaceId.value, sectionId);
    bidDocument.value = result.bidDocument;
    emit("update:bid-document", result.bidDocument);
    syncEditorFromSection(sectionId);
    ElMessage.success(result.message || "模板字段已填充");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    fillingTemplateSectionId.value = "";
  }
}

async function rewriteSection(sectionId: string, mode: "append" | "rewrite") {
  if (!workspaceId.value || !sectionId) return;
  const instruction = rewriteInstruction.value.trim();
  if (!instruction) {
    ElMessage.warning("请输入本章修改要求");
    return;
  }
  rewritingSectionId.value = sectionId;
  try {
    const result = await api.bidDocumentRewriteSection(
      workspaceId.value,
      sectionId,
      instruction,
      mode
    );
    bidDocument.value = result.bidDocument;
    emit("update:bid-document", result.bidDocument);
    syncEditorFromSection(sectionId);
    rewriteInstruction.value = "";
    ElMessage.success(result.message || "Pi 已完成本章修改");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    rewritingSectionId.value = "";
  }
}

async function saveSection(sectionId: string) {
  const sec = plan.value.find((s) => s.sectionId === sectionId);
  if (!workspaceId.value || !sec) return;
  savingSectionId.value = sectionId;
  try {
    const keepTemplateStructure = hasVisualTemplate(sec);
    const result = await api.bidDocumentSaveSection(workspaceId.value, sectionId, {
      content: sec.content || "",
      ...(keepTemplateStructure
        ? {
            templateBlocks: sec.templateBlocks,
            templateTables: sec.templateTables
          }
        : {}),
      contentMatchesTemplate: keepTemplateStructure ? true : sec.contentMatchesTemplate
    });
    bidDocument.value = result.bidDocument;
    emit("update:bid-document", result.bidDocument);
    syncEditorFromSection(sectionId);
    ElMessage.success("本章已保存");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    savingSectionId.value = "";
  }
}

async function runComplianceReview() {
  if (!workspaceId.value) {
    ElMessage.warning("请先选择一个工作区");
    return;
  }
  if (!bidDocument.value) {
    ElMessage.warning("请先生成目录或正文");
    return;
  }
  reviewingCompliance.value = true;
  try {
    const result = await api.complianceReview(workspaceId.value);
    bidDocument.value = result.bidDocument;
    emit("update:bid-document", result.bidDocument);
    ElMessage.success(
      result.provider === "pi" ? "Pi 合规审查完成" : "基础校对完成，请人工复核"
    );
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    reviewingCompliance.value = false;
  }
}

async function exportDocument() {
  if (!workspaceId.value) return;
  exporting.value = true;
  try {
    const result = await api.bidDocumentExport(workspaceId.value, mode.value);
    exportFiles.value = result.files || [];
    if (result.bidDocument) {
      bidDocument.value = result.bidDocument;
      emit("update:bid-document", result.bidDocument);
    }
    if (result.exportWarnings?.length) {
      ElMessage.warning(`导出完成，但有 ${result.exportWarnings.length} 项格式回退提醒`);
    }
    ElMessage.success(result.message || "导出成功");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    exporting.value = false;
  }
}

async function renderExportPreview(index = activeExportPreviewIndex.value) {
  const file = exportPreviewFiles.value[index];
  if (!file) return;
  activeExportPreviewIndex.value = index;
  const requestId = ++exportPreviewRequestId;
  exportPreviewLoading.value = true;
  await nextTick();
  const target = exportPreviewRef.value;
  if (!target) {
    exportPreviewLoading.value = false;
    return;
  }
  target.replaceChildren();
  try {
    const response = await fetch(file.url);
    if (!response.ok) throw new Error(`预览文件读取失败：${response.status}`);
    const blob = await response.blob();
    if (requestId !== exportPreviewRequestId) return;
    await renderAsync(blob, target, undefined, {
      className: "codex-export-docx",
      inWrapper: true,
      breakPages: true,
      ignoreWidth: false,
      ignoreHeight: false,
      renderHeaders: true,
      renderFooters: true
    });
  } catch (error) {
    if (requestId === exportPreviewRequestId) {
      ElMessage.error((error as Error).message || "导出预览加载失败");
    }
  } finally {
    if (requestId === exportPreviewRequestId) {
      exportPreviewLoading.value = false;
    }
  }
}

async function previewExport() {
  if (!workspaceId.value) {
    ElMessage.warning("请先选择一个工作区");
    return;
  }
  previewingExport.value = true;
  try {
    const result = await api.bidDocumentExport(workspaceId.value, mode.value);
    const files = result.files || [];
    const wordFiles = files.filter((file) => file.name.toLowerCase().endsWith(".docx"));
    if (!wordFiles.length) {
      ElMessage.warning("当前没有可预览的导出文件");
      return;
    }
    exportFiles.value = files;
    exportPreviewFiles.value = wordFiles;
    activeExportPreviewIndex.value = 0;
    if (result.bidDocument) {
      bidDocument.value = result.bidDocument;
      emit("update:bid-document", result.bidDocument);
    }
    exportPreviewVisible.value = true;
    await renderExportPreview(0);
    if (result.exportWarnings?.length) {
      ElMessage.warning(`预览已生成，另有 ${result.exportWarnings.length} 项格式回退提醒`);
    }
  } catch (error) {
    ElMessage.error((error as Error).message || "生成导出预览失败");
  } finally {
    previewingExport.value = false;
  }
}
</script>

<template>
  <div class="bid-writing-page">
    <el-dialog
      v-model="exportPreviewVisible"
      title="导出预览"
      width="min(1100px, 94vw)"
      top="4vh"
      class="export-preview-dialog"
      destroy-on-close
    >
      <div v-if="exportPreviewFiles.length > 1" class="export-preview-files" role="tablist" aria-label="导出文件">
        <el-button
          v-for="(file, index) in exportPreviewFiles"
          :key="file.url"
          :type="activeExportPreviewIndex === index ? 'primary' : 'default'"
          plain
          @click="renderExportPreview(index)"
        >
          {{ file.name }}
        </el-button>
      </div>
      <div v-loading="exportPreviewLoading" class="export-preview-viewport">
        <div ref="exportPreviewRef" class="export-preview-document" />
      </div>
    </el-dialog>
    <div class="stepper-bar">
      <div
        v-for="(step, i) in [
          { n: 1, title: '选择招标文件', icon: FileText },
          { n: 2, title: '编辑目录', icon: ListTree },
          { n: 3, title: '正文编辑', icon: Wand2 }
        ]"
        :key="step.n"
        class="stepper-item"
        :class="{ active: activeStep === step.n, done: activeStep > step.n }"
        @click="goStep(step.n)"
      >
        <div class="stepper-dot">
          <el-icon v-if="activeStep > step.n"><Check /></el-icon>
          <span v-else>{{ step.n }}</span>
        </div>
        <div class="stepper-label">
          <component :is="step.icon" :size="14" />
          <span>{{ step.title }}</span>
        </div>
        <div v-if="i < 2" class="stepper-line" />
      </div>
    </div>

    <!-- ============ 第一步：选择招标文件 ============ -->
    <div v-if="activeStep === 1" class="step-panel">
      <el-card class="panel history-panel" shadow="never">
        <template #header>
          <div class="panel-header">
            <div class="result-title">
              <el-icon><ListTree /></el-icon>
              <span>选择已有项目</span>
            </div>
            <el-tag type="success" effect="plain">{{ historyWorkspaces.length }} 个已保存项目</el-tag>
          </div>
        </template>
        <div class="history-toolbar">
          <el-input
            v-model="historyQuery"
            :prefix-icon="Search"
            clearable
            placeholder="搜索项目名称或项目 ID"
          />
          <el-radio-group v-model="historyFilter" size="small">
            <el-radio-button value="all">全部</el-radio-button>
            <el-radio-button value="with_plan">已有目录</el-radio-button>
            <el-radio-button value="parsed">已解析</el-radio-button>
          </el-radio-group>
        </div>
        <div v-if="historyWorkspaces.length" class="history-list">
          <div
            v-for="item in historyWorkspaces"
            :key="item.id"
            class="history-item"
            :class="{ active: item.id === selectedHistoryWorkspaceId }"
            role="button"
            tabindex="0"
            :aria-pressed="item.id === selectedHistoryWorkspaceId"
            @click="selectedHistoryWorkspaceId = item.id"
            @keydown.enter.prevent="selectedHistoryWorkspaceId = item.id"
            @keydown.space.prevent="selectedHistoryWorkspaceId = item.id"
          >
            <span class="history-main">
              <span class="history-title">{{ item.title || item.project.title }}</span>
              <span class="history-meta">
                {{ item.projectId }}
                <template v-if="historyClauseCount(item)"> · 已识别 {{ historyClauseCount(item) }} 条要求</template>
              </span>
            </span>
            <span class="history-status">
              <el-tag size="small" :type="historyStatusType(item)" effect="plain">
                {{ historyStatus(item) }}
              </el-tag>
              <el-icon v-if="item.id === selectedHistoryWorkspaceId" class="history-selected-icon"><Check /></el-icon>
              <el-button
                :icon="Trash2"
                size="small"
                text
                type="danger"
                :disabled="busy"
                title="删除该项目"
                aria-label="删除该项目"
                @click.stop="confirmDeleteWorkspace(item)"
              />
            </span>
          </div>
        </div>
        <el-empty v-else description="没有符合条件的历史项目" :image-size="64" />
        <div v-if="selectedHistoryWorkspace" class="history-selection">
          <div class="history-selection-copy">
            <strong>{{ selectedHistoryWorkspace.title || selectedHistoryWorkspace.project.title }}</strong>
            <span class="history-meta">{{ selectedHistoryWorkspace.projectId }} · {{ historyStatus(selectedHistoryWorkspace) }}</span>
          </div>
          <el-button
            type="primary"
            :loading="historyActionWorkspaceId === selectedHistoryWorkspace.id"
            :disabled="Boolean(historyActionWorkspaceId)"
            @click="runHistoryAction(selectedHistoryWorkspace)"
          >
            {{ historyActionLabel(selectedHistoryWorkspace) }}
          </el-button>
        </div>
      </el-card>

      <el-card class="panel" shadow="never">
        <template #header>
          <div class="panel-header">
            <div class="result-title"><el-icon><Upload /></el-icon><span>或上传新招标文件</span></div>
            <el-tag type="warning" effect="plain">上传后由当前配置的智能体解析并生成目录</el-tag>
          </div>
        </template>
        <div class="upload-zone" @click="chooseFile">
          <el-icon :size="28"><Upload /></el-icon>
          <p>{{ importFile?.name || "点击选择招标文件（PDF / Word）" }}</p>
          <span class="form-hint">选择后点击「上传并解析」，完成后自动进入标书工作区</span>
        </div>
        <input ref="fileInput" type="file" hidden accept=".pdf,.doc,.docx" @change="onFileChange" />
        <div class="step-actions">
          <el-button
            type="primary"
            :loading="cloudMaterializing || cloudRun?.status === 'parsing' || cloudRun?.status === 'uploading'"
            @click="startImport"
          >
            上传并解析
          </el-button>
        </div>
      </el-card>

      <el-card v-if="cloudRun" class="panel cloud-result-panel" shadow="never">
        <template #header>
          <div class="panel-header">
            <div class="result-title"><el-icon><Wand2 /></el-icon><span>{{ providerLabel(cloudRun.provider) }}解析</span></div>
            <el-tag :type="cloudRun.status === 'failed' ? 'danger' : cloudRun.status === 'outline_ready' ? 'success' : 'warning'" effect="plain">
              {{ cloudStatusName(cloudRun.status) }}
            </el-tag>
          </div>
        </template>
        <el-progress :percentage="cloudRun.progress" :status="cloudRun.status === 'failed' ? 'exception' : undefined" />
        <p v-if="cloudRun.totalNodes" class="form-hint">
          已完成 {{ cloudRun.completedNodes || 0 }}/{{ cloudRun.totalNodes }} 个节点
          · 已用时 {{ Math.floor((cloudRun.elapsedSeconds || 0) / 60) }} 分 {{ Math.floor((cloudRun.elapsedSeconds || 0) % 60) }} 秒
          · {{ cloudRun.activeNodes?.length || 0 }} 个节点执行中
          <span v-if="cloudRun.failedNodes"> · {{ cloudRun.failedNodes }} 个节点失败，结果需复核</span>
        </p>
        <div v-if="cloudRun.currentAgent" class="cloud-agent-progress">
          <span>当前节点</span>
          <strong>{{ currentAgentName(cloudRun.currentAgent) }}</strong>
          <el-tag
            size="small"
            :type="cloudRun.currentAgentStatus === 'failed' ? 'danger' : cloudRun.currentAgentStatus === 'success' ? 'success' : 'warning'"
            effect="plain"
          >
            {{ currentAgentStatus(cloudRun.currentAgentStatus) }}
          </el-tag>
        </div>
        <p v-if="cloudRun.error" class="cloud-error">{{ cloudRun.error }}</p>
        <el-alert
          v-if="cloudRun.result?.parseReviewReport?.status === 'review_required'"
          title="解析已结束，结果仍有待复核项，请检查原文覆盖、条款和来源后再撰写。"
          type="warning"
          :closable="false"
          show-icon
        />

        <template v-if="cloudRun.result">
          <div class="cloud-meta-grid">
            <div v-for="(value, key) in cloudRun.result.projectMeta" :key="key" class="cloud-meta-item">
              <span>{{ key }}</span>
              <strong>{{ value || "—" }}</strong>
            </div>
          </div>

          <el-divider content-position="left">招标文件要求的文件组成</el-divider>
          <div class="parse-submission-summary">
            <span>{{ cloudRun.result.submissionStructure?.mode === 'separate' ? '明确要求独立分册' : cloudRun.result.submissionStructure?.mode === 'single' ? '明确要求合卷' : '未明确分册方式' }}</span>
            <span v-if="cloudRun.result.submissionStructure?.sourceLocator" class="form-hint">依据：{{ cloudRun.result.submissionStructure.sourceLocator }}</span>
          </div>
          <el-table v-if="cloudRun.result.submissionStructure?.volumes?.length" :data="cloudRun.result.submissionStructure.volumes" size="small" border class="cloud-table">
            <el-table-column prop="order" label="顺序" width="70" />
            <el-table-column prop="title" label="文件 / 卷" min-width="220" />
            <el-table-column prop="sourceLocator" label="原文位置" min-width="200" />
            <el-table-column label="卷内文件" min-width="320">
              <template #default="{ row }">
                <span v-if="row.items?.length">
                  {{ submissionItemTitles(row) }}
                </span>
                <span v-else class="form-hint">未识别</span>
              </template>
            </el-table-column>
          </el-table>

          <el-divider content-position="left">解析提取的格式条目（{{ cloudRun.result.outline.length }} 项）</el-divider>
          <div v-if="cloudRun.result.agentResults?.length" class="header-tags">
            <span class="form-hint">本次解析节点：</span>
            <el-tag
              v-for="(agent, index) in cloudRun.result.agentResults"
              :key="`${agentName(agent)}-${index}`"
              size="small"
              :type="agentStatusType(agent)"
              effect="plain"
            >
              {{ agentName(agent) }} · {{ agentStatus(agent) }}
            </el-tag>
          </div>
          <el-table :data="cloudRun.result.outline" size="small" border class="cloud-table">
            <el-table-column type="index" label="#" width="52" />
            <el-table-column prop="title" label="章节标题" min-width="220" />
            <el-table-column prop="kind" label="类型" width="120" />
            <el-table-column prop="source" label="来源" width="140" />
          </el-table>

          <el-divider content-position="left">关键要求（{{ cloudRun.result.requirements.length }} 条）</el-divider>
          <el-table :data="cloudRun.result.requirements" size="small" border class="cloud-table">
            <el-table-column prop="category" label="类别" width="120" />
            <el-table-column prop="requirement" label="要求" min-width="280" show-overflow-tooltip />
            <el-table-column label="强制" width="80">
              <template #default="{ row }">
                <el-tag size="small" :type="row.mandatory ? 'danger' : 'info'" effect="plain">{{ row.mandatory ? "是" : "否" }}</el-tag>
              </template>
            </el-table-column>
            <el-table-column prop="sourceLocator" label="来源位置" width="140" />
          </el-table>

          <el-alert
            v-for="(warning, index) in cloudRun.result.warnings"
            :key="index"
            :title="warning"
            type="warning"
            :closable="false"
            show-icon
            class="cloud-warning"
          />
        </template>
      </el-card>

      <div class="step-actions">
        <el-button type="primary" :icon="ArrowRight" :disabled="!workspace" :loading="planning" @click="generateToc">
          下一步：生成目录
        </el-button>
      </div>
    </div>

    <!-- ============ 第二步：编辑目录 ============ -->
    <div v-else-if="activeStep === 2" class="step-panel">
      <el-card class="panel" shadow="never">
        <template #header>
          <div class="panel-header">
            <div class="result-title"><el-icon><ListTree /></el-icon><span>章节目录（可编辑）</span></div>
            <div class="header-tags">
              <el-button
                size="small"
                text
                type="primary"
                :icon="Wand2"
                :loading="planning"
                @click="regenerateToc"
              >
                按当前规则重算目录
              </el-button>
              <el-button
                size="small"
                text
                type="primary"
                :icon="FileText"
                :loading="buildingEvidence"
                @click="buildEvidenceChapter"
              >
                一键插入技术参数证明材料
              </el-button>
              <el-tag v-if="planningSourceKey(bidDocument)" size="small" :type="planSourceTag(planningSourceKey(bidDocument), bidDocument)" effect="plain">
                {{ planSourceLabel(planningSourceKey(bidDocument)) }}
              </el-tag>
              <el-tag
                v-for="(agent, index) in bidDocument?.agentResults || []"
                :key="`${agentName(agent)}-${index}`"
                size="small"
                :type="agentStatusType(agent)"
                effect="plain"
              >
                {{ agentName(agent) }}
              </el-tag>
              <el-tag size="small" type="info" effect="plain" class="plan-level-stats">
                共 {{ planLevelStats.total }} 项
                <span v-for="stat in planLevelStats.levels" :key="stat.level">
                  {{ stat.level === 1 ? "卷" : roleForLevel(stat.level) }} {{ stat.count }}
                </span>
                <span v-if="planLevelStats.overflow">6级以上 {{ planLevelStats.overflow }}</span>
              </el-tag>
            </div>
          </div>
        </template>

        <el-alert
          v-if="bidDocument"
          class="plan-source-alert"
          :type="planSourceTag(planningSourceKey(bidDocument), bidDocument)"
          :closable="false"
          show-icon
        >
          <template #title>
            <span class="plan-source-title">解析来源：{{ parseSourceLabel(bidDocument) }}</span>
            <span class="plan-source-detail">目录规划来源：{{ planSourceLabel(planningSourceKey(bidDocument)) }}。{{ planSourceMessage(bidDocument) }}</span>
          </template>
        </el-alert>
        <el-alert
          v-if="bidDocument?.submissionStructure"
          class="plan-source-alert"
          :type="bidDocument.submissionStructure.mode === 'separate' ? 'warning' : 'info'"
          :closable="false"
          show-icon
          :title="bidDocument.submissionStructure.mode === 'separate'
            ? `招标文件要求独立分册；依据：${bidDocument.submissionStructure.sourceLocator}`
            : bidDocument.submissionStructure.mode === 'single'
              ? `招标文件要求合卷导出；依据：${bidDocument.submissionStructure.sourceLocator}`
              : '招标文件未明确独立分册规则：目录可按卷组织，默认导出一份 Word。'"
        />

        <el-alert
          v-if="templateFragmentCount"
          class="plan-source-alert"
          type="success"
          :closable="false"
          show-icon
          :title="`已绑定 ${templateFragmentCount} 个招标模板片段；固定文字和表格将优先按原始 Word 格式复制。`"
        />

        <el-alert
          v-for="(warning, index) in bidDocument?.planningWarnings || []"
          :key="index"
          class="plan-source-alert"
          type="warning"
          :closable="false"
          show-icon
          :title="warning"
        />

        <!-- 目录规划诊断（只读）：先看清“已有章节 / 缺哪些要求章节 / 建议哪些自由发挥章节” -->
        <div class="diagnosis-panel">
          <div class="diagnosis-head" @click="outlineDiagnosisOpen = !outlineDiagnosisOpen">
            <el-icon><ListTree /></el-icon>
            <span class="diagnosis-title">规划诊断</span>
            <template v-if="outlineDiagnosis">
              <el-tag size="small" type="info" effect="plain">
                原文条目 {{ outlineDiagnosis.summary.formatItems }} · 响应章节 {{ outlineDiagnosis.summary.responseSections }}
              </el-tag>
              <el-tag v-if="outlineDiagnosis.requiredGaps.length" size="small" type="warning" effect="plain">
                建议补充要求章节 {{ outlineDiagnosis.requiredGaps.length }}
              </el-tag>
              <el-tag v-if="outlineDiagnosis.initiatives.length" size="small" type="success" effect="plain">
                自由发挥建议 {{ outlineDiagnosis.initiatives.length }}
              </el-tag>
            </template>
            <span class="diagnosis-spacer" />
            <el-button
              size="small"
              text
              type="primary"
              :loading="outlineDiagnosisLoading"
              @click.stop="loadOutlineDiagnosis"
            >
              重新诊断
            </el-button>
            <el-icon class="diagnosis-caret" :class="{ 'is-open': outlineDiagnosisOpen }">
              <ArrowDown />
            </el-icon>
          </div>
          <div v-show="outlineDiagnosisOpen" v-loading="outlineDiagnosisLoading" class="diagnosis-body">
            <el-alert
              v-if="!outlineDiagnosis"
              type="info"
              :closable="false"
              show-icon
              title="暂无诊断数据：请先在第一、二步生成目录后再查看。"
            />
            <template v-else>
              <div class="diagnosis-stats">
                <span>
                  条款 {{ outlineDiagnosis.summary.requirements }} 条：叙述章节承载
                  {{ outlineDiagnosis.summary.narrativeBound }} · 仅响应表承载
                  {{ outlineDiagnosis.summary.tableBound }} · 完全未绑定 {{ outlineDiagnosis.summary.unbound }}
                </span>
                <span>
                  评分项 {{ outlineDiagnosis.summary.evaluationItems }} 个：已归位
                  {{ outlineDiagnosis.summary.evaluationMapped }} · 未归位
                  {{ outlineDiagnosis.summary.evaluationUnmapped }}
                </span>
                <span>
                  {{ outlineDiagnosis.summary.hasNarrativeCapacity
                    ? "目录中已有可撰写正文的章节"
                    : "目录中没有任何可撰写正文的章节" }}
                </span>
              </div>
              <el-alert
                v-for="(warning, index) in outlineDiagnosis.warnings"
                :key="`diagnosis-warning-${index}`"
                class="diagnosis-alert"
                type="warning"
                :closable="false"
                show-icon
                :title="warning"
              />
              <el-alert
                v-for="(note, index) in outlineDiagnosis.notes"
                :key="`diagnosis-note-${index}`"
                class="diagnosis-alert"
                type="info"
                :closable="false"
                show-icon
                :title="note"
              />
              <div class="diagnosis-columns">
                <section class="diagnosis-column">
                  <header>规则聚类参考：要求响应缺口</header>
                  <p v-if="!outlineDiagnosis.requiredGaps.length" class="diagnosis-empty">
                    暂未发现要求响应缺口。
                  </p>
                  <article
                    v-for="item in outlineDiagnosis.requiredGaps"
                    :key="`gap-${item.archetype}`"
                    class="diagnosis-card"
                  >
                    <div class="diagnosis-card-head">
                      <strong>{{ item.title }}</strong>
                      <el-tag size="small" effect="plain" :type="diagnosisOriginTagType(item.origin)">
                        {{ diagnosisOriginLabel(item.origin) }}
                      </el-tag>
                      <el-tag v-if="item.targetVolumeTitle" size="small" type="info" effect="plain">
                        拟归属：{{ item.targetVolumeTitle }}
                      </el-tag>
                    </div>
                    <p class="diagnosis-card-meta">{{ diagnosisRequirementSummary(item) }}</p>
                    <p class="diagnosis-card-reason">{{ item.reason }}</p>
                    <p class="diagnosis-card-basis">建议依据：{{ item.basis }}</p>
                    <p v-for="label in item.evaluationLabels" :key="label" class="diagnosis-card-eval">
                      评分项：{{ label }}
                    </p>
                  </article>
                </section>
                <section class="diagnosis-column">
                  <header>规则聚类参考：可自由发挥</header>
                  <p v-if="!outlineDiagnosis.initiatives.length" class="diagnosis-empty">
                    暂未识别到需要自主补充的章节。
                  </p>
                  <article
                    v-for="item in outlineDiagnosis.initiatives"
                    :key="`initiative-${item.archetype}`"
                    class="diagnosis-card"
                  >
                    <div class="diagnosis-card-head">
                      <strong>{{ item.title }}</strong>
                      <el-tag size="small" effect="plain" :type="diagnosisOriginTagType(item.origin)">
                        {{ diagnosisOriginLabel(item.origin) }}
                      </el-tag>
                      <el-tag v-if="item.targetVolumeTitle" size="small" type="info" effect="plain">
                        拟归属：{{ item.targetVolumeTitle }}
                      </el-tag>
                    </div>
                    <p class="diagnosis-card-meta">{{ diagnosisRequirementSummary(item) }}</p>
                    <p class="diagnosis-card-reason">{{ item.reason }}</p>
                    <p class="diagnosis-card-basis">建议依据：{{ item.basis }}</p>
                  </article>
                </section>
              </div>
              <div class="diagnosis-decided">
                <header>
                  目录智能体已新增的章节（{{ derivedPlanSections.length }}）
                  <span class="diagnosis-decided-hint">章节由目录智能体取证后自行决定并写入目录，这里展示它的依据</span>
                </header>
                <p v-if="!derivedPlanSections.length" class="diagnosis-empty">
                  本次目录没有新增章节。若规则聚类仍提示缺口，说明智能体取证后认为依据不足（见上方智能体 warnings）。
                </p>
                <article
                  v-for="section in derivedPlanSections"
                  :key="`derived-${section.sectionId}`"
                  class="diagnosis-card"
                >
                  <div class="diagnosis-card-head">
                    <strong>{{ section.title }}</strong>
                    <el-tag size="small" effect="plain" :type="derivedOriginTagType(section)">
                      {{ derivedOriginLabel(section) }}
                    </el-tag>
                    <el-tag v-if="section.derivedArchetype" size="small" type="info" effect="plain">
                      {{ section.derivedArchetype }}
                    </el-tag>
                    <el-tag v-if="section.required === false" size="small" type="info" effect="plain">
                      非必交项
                    </el-tag>
                  </div>
                  <p class="diagnosis-card-basis">依据：{{ section.derivedBasis || "（未填写依据）" }}</p>
                  <p class="diagnosis-card-meta">
                    引用条款 {{ section.requirementIds?.length || 0 }} 条 · 引用评分项
                    {{ section.evaluationIds?.length || 0 }} 个
                  </p>
                </article>
              </div>
              <div v-if="outlineDiagnosis.unboundRequirementSamples.length" class="diagnosis-samples">
                <header>完全未绑定到任何章节的条款（样例）</header>
                <p v-for="row in outlineDiagnosis.unboundRequirementSamples" :key="row.requirementId">
                  <span class="diagnosis-sample-id">{{ row.requirementId }}</span>
                  <span class="diagnosis-sample-domain">{{ row.domain }}</span>
                  {{ row.text }}
                </p>
              </div>
            </template>
          </div>
        </div>

        <div class="toc-tree-wrap">
          <el-tree
            :data="treePlan"
            node-key="id"
            :props="{ children: 'children', label: (item: PlanTreeNode) => item.section.title }"
            :expand-on-click-node="false"
            default-expand-all
            class="toc-tree"
          >
            <template #default="{ data }">
              <div
                class="toc-tree-row"
                :class="{
                  'toc-volume-row': data.section.nodeType === 'volume',
                  'toc-format-item-row': data.section.nodeType === 'format_item'
                }"
                @click.stop
              >
                <span class="toc-role">{{ sectionRole(data.section) }}</span>
                <span class="toc-number">{{ sectionNumber(data.section) }}</span>
                <el-input v-model="data.section.title" size="small" class="toc-title-input" />
                <el-tooltip
                  v-if="data.section.derived"
                  placement="top"
                  :show-after="200"
                  :content="derivedBasisText(data.section)"
                >
                  <el-tag
                    size="small"
                    :type="derivedOriginTagType(data.section)"
                    effect="plain"
                    class="toc-origin-tag"
                  >
                    {{ derivedOriginLabel(data.section) }}
                  </el-tag>
                </el-tooltip>
                <el-tag
                  v-if="data.section.structureSource"
                  size="small"
                  type="info"
                  effect="plain"
                  class="toc-origin-tag"
                >
                  {{ data.section.structureSource }}
                </el-tag>
                <el-tag
                  v-if="data.section.conditional"
                  size="small"
                  type="warning"
                  effect="plain"
                  class="toc-origin-tag"
                >
                  如有
                </el-tag>
                <el-tag v-if="data.section.templateFragmentId" size="small" :type="templateCopyTagType(data.section)" effect="plain">
                  {{ templateCopyLabel(data.section) }}
                </el-tag>
                <el-tag
                  v-else-if="data.section.templateId && ['fixed_form', 'fixed_table'].includes(data.section.itemType || '')"
                  size="small"
                  type="warning"
                  effect="plain"
                  title="目录条目标记为模板类，但尚未定位到可复用的原文片段"
                >
                  模板待定位
                </el-tag>
                <SectionStatusBadge :section="data.section" />
                <div class="toc-row-actions">
                  <el-dropdown
                    trigger="click"
                    @command="handleAddCommand(data.section, $event)"
                  >
                    <el-button
                      size="small"
                      text
                      class="toc-add-trigger"
                      :aria-label="`为${data.section.title}添加章节`"
                    >
                      <el-icon><Plus /></el-icon>
                      <el-icon><ChevronDown /></el-icon>
                    </el-button>
                    <template #dropdown>
                      <el-dropdown-menu>
                        <el-dropdown-item command="sibling" :disabled="!canAddSibling(data.section)">
                          添加同级
                        </el-dropdown-item>
                        <el-dropdown-item command="child" :disabled="!canAddChild(data.section)">
                          添加子级
                        </el-dropdown-item>
                      </el-dropdown-menu>
                    </template>
                  </el-dropdown>
                  <el-button size="small" text :icon="ArrowUp" :disabled="!canMoveSection(data.section, -1)" @click="moveSection(data.section, -1)" />
                  <el-button size="small" text :icon="ArrowDown" :disabled="!canMoveSection(data.section, 1)" @click="moveSection(data.section, 1)" />
                  <el-button size="small" text type="danger" :icon="Trash2" @click="removeSection(data.section)" />
                </div>
              </div>
              <div
                v-if="inlineAddTargetId === data.section.sectionId"
                class="toc-inline-add"
                @click.stop
              >
                <span class="toc-inline-add-label">
                  {{ inlineAddMode === "child" ? "新增子级" : "新增同级" }}
                  <small>{{ inlineAddMode === "child" ? roleForLevel(Number(data.section.level || 1) + 1) : sectionRole(data.section) }}</small>
                </span>
                <el-input
                  ref="inlineAddTitleInput"
                  v-model="inlineAddTitle"
                  size="small"
                  autofocus
                  placeholder="输入章节名称"
                  class="toc-inline-add-title"
                  @keyup.enter="commitInlineAdd(data.section)"
                  @keyup.esc="resetInlineAdd"
                />
                <el-button size="small" type="primary" :icon="Check" @click="commitInlineAdd(data.section)">
                  确定
                </el-button>
                <el-button size="small" text @click="resetInlineAdd">取消</el-button>
              </div>
            </template>
          </el-tree>
        </div>

      </el-card>

      <el-card v-if="evidencePreview" class="panel" shadow="never">
        <template #header>
          <div class="panel-header">
            <div class="result-title"><el-icon><FileText /></el-icon><span>技术参数证明材料</span></div>
            <div class="header-tags">
              <el-tag size="small" type="info" effect="plain">共 {{ evidencePreview.summary.total }} 条</el-tag>
              <el-tag size="small" type="success" effect="plain">命中 {{ evidencePreview.summary.ready }}</el-tag>
              <el-tag size="small" type="warning" effect="plain">待补扫描 {{ (evidencePreview.summary.ready || 0) - (evidencePreview.summary.scannedReady || 0) }}</el-tag>
              <el-tag size="small" type="danger" effect="plain">待人工补 {{ evidencePreview.summary.missing }}</el-tag>
              <el-button size="small" text type="primary" @click="openEvidenceSection">查看正文</el-button>
            </div>
          </div>
        </template>
        <div class="evidence-entry-list">
          <div
            v-for="entry in evidencePreview.entries"
            :key="entry.sequence"
            class="evidence-entry"
          >
            <div class="evidence-entry-head">
              <span class="evidence-entry-seq">#{{ entry.sequence }}</span>
              <span class="evidence-entry-title" :title="entry.requirement">{{ entry.requirement }}</span>
              <el-tag size="small" :type="evidenceEntryState(entry).type" effect="plain">
                {{ evidenceEntryState(entry).label }}
              </el-tag>
            </div>
            <div v-if="entry.sourceRefs.length" class="evidence-entry-refs">
              <span
                v-for="ref in entry.sourceRefs"
                :key="ref.evidencePath"
                class="evidence-ref"
                :class="{ 'evidence-ref-missing': ref.available === false }"
                :title="ref.evidencePath"
              >
                {{ ref.available === false ? "◌" : "●" }} {{ ref.evidencePath }}
              </span>
            </div>
            <div v-if="entryMaterialFiles(entry).length" class="evidence-entry-thumbs">
              <el-image
                v-for="file in entryMaterialFiles(entry)"
                :key="file.url"
                class="evidence-thumb"
                :src="file.url"
                :preview-src-list="entryMaterialFileUrls(entry)"
                :initial-index="entryMaterialFileUrls(entry).indexOf(file.url)"
                fit="cover"
                preview-teleported
                lazy
              >
                <template #error>
                  <div class="evidence-thumb-fallback">无法预览</div>
                </template>
              </el-image>
            </div>
          </div>
        </div>
      </el-card>

      <div class="step-actions">
        <el-button :icon="ArrowLeft" @click="goStep(1)">上一步</el-button>
        <el-button :icon="Check" :loading="savingPlan" @click="savePlan">保存目录</el-button>
        <el-button type="primary" :icon="ArrowRight" @click="goStep(3)">下一步：正文编辑</el-button>
      </div>
    </div>

    <!-- ============ 第三步：正文编辑 ============ -->
    <div v-else class="step-panel">
      <SectionJobPanel :sections="plan" @select-section="openJobSection" />
      <el-row ref="editorLayoutRef" class="editor-layout" :gutter="16">
        <el-col
          class="editor-layout-col editor-navigation-col"
          :style="editorColumnStyle"
          :xs="24"
          :md="5"
        >
          <div class="nav-panel-viewport">
            <el-card class="panel nav-panel" shadow="never">
              <template #header><span class="panel-title">章节导航</span></template>
              <div
                v-for="sec in visibleNavigationSections"
                :key="sec.sectionId"
                class="nav-item"
                :class="{ active: currentSectionId === sec.sectionId }"
                @click="scrollToSection(sec.sectionId)"
              >
                <span class="nav-title" :style="sectionIndentStyle(sec)">
                  <button
                    v-if="navigationSectionHasChildren(sec.sectionId)"
                    class="nav-collapse-button"
                    type="button"
                    :aria-label="navigationSectionCollapsed(sec.sectionId) ? `展开${sec.title}` : `收起${sec.title}`"
                    @click.stop="toggleNavigationSection(sec.sectionId)"
                  >
                    <ChevronDown
                      :size="14"
                      :class="{ collapsed: navigationSectionCollapsed(sec.sectionId) }"
                    />
                  </button>
                  <span v-else class="nav-collapse-placeholder" />
                  <span class="nav-number">{{ sectionNumber(sec) }}</span>
                  {{ sec.title }}
                </span>
                <SectionStatusBadge :section="sec" />
              </div>
              <el-empty v-if="!plan.length" description="暂无目录，请先回到第二步" :image-size="60" />
            </el-card>
          </div>
        </el-col>

        <el-col
          class="editor-layout-col editor-document-col"
          :style="editorColumnStyle"
          :xs="24"
          :md="13"
        >
          <div class="word-page-wrap">
            <div class="word-page">
              <section
                v-for="(sec, i) in plan"
                :key="sec.sectionId"
                :id="`sec-${sec.sectionId}`"
                class="doc-section"
                :class="{ selected: currentSectionId === sec.sectionId }"
                @click="selectDocSection(sec.sectionId)"
              >
                <div class="doc-heading" :style="{ marginLeft: `${sectionIndentLevel(sec) * 20}px` }">
                  <span class="chapter-num">{{ sectionNumber(sec, i) }}</span>
                  <h2 class="doc-title">{{ sec.title }}</h2>
                  <el-tag
                    v-if="sec.draftProvider"
                    size="small"
                    :type="['pi', 'dify'].includes(sec.draftProvider) ? 'success' : 'warning'"
                    effect="plain"
                  >
                    {{ draftProviderLabel(sec.draftProvider) }}
                  </el-tag>
                  <el-tag
                    v-for="(agent, index) in sec.agentResults || []"
                    :key="`${agentName(agent)}-${index}`"
                    size="small"
                    :type="agentStatusType(agent)"
                    effect="plain"
                  >
                    {{ agentName(agent) }}
                  </el-tag>
                <el-tag
                  v-if="sec.templateFragmentId"
                  size="small"
                  :type="templateCopyTagType(sec)"
                  effect="plain"
                >
                  {{ templateCopyLabel(sec) }}
                </el-tag>
                  <el-tag
                    v-else-if="sec.templateStatus === 'unresolved' || (['fixed_form', 'fixed_table'].includes(sec.itemType || '') && !sec.templateFragmentId)"
                    size="small"
                    type="warning"
                    effect="plain"
                  >
                    模板待定位
                  </el-tag>
                  <el-tag
                    v-if="sec.templateFillProvider"
                    size="small"
                    :type="sec.templateFillProvider === 'pi' ? 'success' : 'warning'"
                    effect="plain"
                  >
                    {{ templateFillLabel(sec) }}
                  </el-tag>
                  <el-tag
                    v-if="sec.rewriteProvider"
                    size="small"
                    type="success"
                    effect="plain"
                  >
                    Pi 局部修改
                  </el-tag>
                  <SectionStatusBadge :section="sec" />
                </div>

                <el-alert
                  v-if="sec.cloudDraftError"
                  class="section-warning"
                  type="warning"
                  :closable="false"
                  show-icon
                  :title="`云端章节撰写失败，已使用本地回退：${sec.cloudDraftError}`"
                />

                <div v-if="sec.materialMatches?.length || sec.unmatchedMaterialRequirementIds?.length" class="section-trace">
                  <span class="form-hint">Pi 章节编排：</span>
                  <el-tag size="small" type="info" effect="plain">
                    已匹配材料 {{ sec.materialMatches?.length || 0 }} 条
                  </el-tag>
                  <el-tag v-if="sec.writingPlan" size="small" type="success" effect="plain">
                    写作计划已生成
                  </el-tag>
                  <el-tag v-if="sec.unmatchedMaterialRequirementIds?.length" size="small" type="warning" effect="plain">
                    待补材料 {{ sec.unmatchedMaterialRequirementIds.length }} 条
                  </el-tag>
                </div>

                <div v-if="sec.templateFragmentId" class="section-trace">
                  <span class="form-hint">模板片段：</span>
                  <el-tag size="small" :type="templateCopyTagType(sec)" effect="plain">
                    {{ templateCopyLabel(sec) }}
                  </el-tag>
                  <el-tag size="small" type="info" effect="plain">
                    可填字段 {{ sec.templateFieldCount || 0 }}
                  </el-tag>
                  <el-tag size="small" type="info" effect="plain">
                    固定表格 {{ sec.templateTableCount || 0 }}
                  </el-tag>
                  <el-tag v-if="sec.blanksToFill?.length" size="small" type="warning" effect="plain">
                    待补 {{ sec.blanksToFill.length }} 项
                  </el-tag>
                  <el-tag v-if="sec.templateFillStatus === 'needs_confirmation'" size="small" type="warning" effect="plain">
                    待确认字段
                  </el-tag>
                </div>

                <el-alert
                  v-if="sec.templateFillWarnings?.length"
                  class="section-warning"
                  type="warning"
                  :closable="false"
                  show-icon
                  :title="sec.templateFillWarnings.join('；')"
                />

                <div
                  v-if="currentSectionId === sec.sectionId && sec.responseType !== 'section_group'"
                  class="doc-editor"
                  @click.stop
                >
                  <div class="doc-toolbar" role="toolbar" aria-label="正文排版工具">
                    <el-button text size="small" :icon="Undo2" title="撤销" @mousedown.prevent="applyEditorCommand('undo')" />
                    <el-button text size="small" :icon="Redo2" title="重做" @mousedown.prevent="applyEditorCommand('redo')" />
                    <span class="toolbar-divider" />
                    <el-button text size="small" :icon="Bold" title="加粗" @mousedown.prevent="applyEditorCommand('bold')" />
                    <el-button text size="small" :icon="Italic" title="斜体" @mousedown.prevent="applyEditorCommand('italic')" />
                    <el-button text size="small" :icon="Underline" title="下划线" @mousedown.prevent="applyEditorCommand('underline')" />
                    <span class="toolbar-divider" />
                    <el-button text size="small" :icon="Heading1" title="一级标题" @mousedown.prevent="applyEditorCommand('formatBlock', 'h1')" />
                    <el-button text size="small" :icon="Heading2" title="二级标题" @mousedown.prevent="applyEditorCommand('formatBlock', 'h2')" />
                    <el-button text size="small" :icon="List" title="项目符号" @mousedown.prevent="applyEditorCommand('insertUnorderedList')" />
                    <el-button text size="small" :icon="ListOrdered" title="编号列表" @mousedown.prevent="applyEditorCommand('insertOrderedList')" />
                    <el-button text size="small" :icon="RemoveFormatting" title="清除格式" @mousedown.prevent="applyEditorCommand('removeFormat')" />
                  </div>
                  <div
                    class="doc-rich-editor"
                    :class="{ 'structured-template-editor': hasVisualTemplate(sec) }"
                    contenteditable="true"
                    role="textbox"
                    aria-multiline="true"
                    :data-placeholder="sec.content ? '' : '点击此处输入正文，或使用上方按钮进行排版'"
                    v-html="editorHtml"
                    @input="updateSectionFromEditor($event, sec)"
                    @keydown.ctrl.s.prevent="saveSection(sec.sectionId)"
                  />
                  <el-input
                    v-model="rewriteInstruction"
                    size="small"
                    :prefix-icon="PenLine"
                    placeholder="输入本章修改要求，例如：补充实施步骤和质量控制"
                    @click.stop
                  />
                  <div class="doc-actions">
                    <el-button size="small" type="primary" :icon="Wand2" :loading="generatingSectionId === sec.sectionId" @click="generateSection(sec.sectionId)">自动撰写 / 一键填充</el-button>
                    <el-button
                      v-if="sec.templateFragmentId || sec.source === 'copied_template' || sec.templateText || ['fixed_form', 'fixed_table'].includes(sec.itemType || '')"
                      size="small"
                      :icon="FileText"
                      :loading="fillingTemplateSectionId === sec.sectionId"
                      @click="fillTemplate(sec.sectionId)"
                    >
                      仅填充模板
                    </el-button>
                    <el-button
                      v-if="!sec.templateFragmentId && sec.source !== 'copied_template' && !sec.templateText"
                      size="small"
                      :icon="PenLine"
                      :loading="rewritingSectionId === sec.sectionId"
                      @click="rewriteSection(sec.sectionId, 'append')"
                    >
                      Pi 补写
                    </el-button>
                    <el-button
                      v-if="!sec.templateFragmentId && sec.source !== 'copied_template' && !sec.templateText"
                      size="small"
                      :icon="Wand2"
                      :loading="rewritingSectionId === sec.sectionId"
                      @click="rewriteSection(sec.sectionId, 'rewrite')"
                    >
                      Pi 重写
                    </el-button>
                    <el-button size="small" :icon="Check" :loading="savingSectionId === sec.sectionId" @click="saveSection(sec.sectionId)">保存本章</el-button>
                    <el-button size="small" text @click="deselect">完成</el-button>
                  </div>
                </div>
                <div v-else-if="sec.responseType === 'section_group'" class="doc-body doc-group-hint">
                  <span>目录分组标题，下级章节在此分组内编排。</span>
                </div>
                <div v-else class="doc-body">
                  <span v-if="!sectionHasContent(sec)" class="doc-empty">（本章暂无内容，点击此处或左侧目录，然后「自动撰写 / 一键填充」）</span>
                  <div
                    v-else
                    v-html="hasVisualTemplate(sec) ? renderTemplateFragment(sec) : renderMarkdown(sec.content || '')"
                  ></div>
                </div>
              </section>
              <el-empty v-if="!plan.length" description="暂无目录，请先回到第二步编辑目录" :image-size="80" />
            </div>
          </div>
        </el-col>

        <el-col class="editor-layout-col editor-side-col" :xs="24" :md="6">
          <el-card class="panel" shadow="never">
            <template #header><span class="panel-title">项目基本信息</span></template>
            <div v-if="project" class="info-list">
              <div v-for="(v, k) in projectInfo" :key="k" class="info-row">
                <span class="info-key">{{ k }}</span>
                <span class="info-value">{{ v || "—" }}</span>
              </div>
            </div>
            <span v-else class="form-hint">未选择项目</span>
          </el-card>

          <el-card class="panel" shadow="never">
            <template #header><span class="panel-title">待补字段</span></template>
            <div v-if="blanksToFill.length" class="todo-list">
              <div v-for="b in blanksToFill" :key="b" class="todo-item">⚠ {{ b }}</div>
            </div>
            <span v-if="!blanksToFill.length" class="form-hint">暂无待补字段</span>
          </el-card>

          <el-card
            v-if="bidDocument?.requirementMappingStatus && bidDocument.requirementMappingStatus !== 'pending'"
            class="panel"
            shadow="never"
          >
            <template #header>
              <div class="panel-header">
                <span class="panel-title">条款映射</span>
                <el-tag
                  size="small"
                  :type="requirementMappingTagType(bidDocument.requirementMappingStatus)"
                  effect="plain"
                >
                  {{ requirementMappingStatusLabel(bidDocument.requirementMappingStatus) }}
                </el-tag>
              </div>
            </template>
            <el-alert
              v-if="bidDocument?.uncoveredRequirements?.length"
              class="section-warning"
              type="warning"
              :closable="false"
              show-icon
              :title="`未覆盖条款：${issueIds(bidDocument.uncoveredRequirements)}`"
            />
            <el-alert
              v-if="bidDocument?.duplicatedRequirements?.length"
              class="section-warning"
              type="info"
              :closable="false"
              show-icon
              :title="`重复覆盖条款：${issueIds(bidDocument.duplicatedRequirements)}`"
            />
            <el-table
              v-if="bidDocument?.requirementCoverage?.length"
              :data="bidDocument.requirementCoverage"
              size="small"
              border
              class="coverage-table"
            >
              <el-table-column prop="requirementId" label="条款" width="110" />
              <el-table-column prop="sectionId" label="响应章节" width="110" />
              <el-table-column prop="status" label="状态" width="90" />
              <el-table-column prop="reason" label="映射依据" min-width="180" show-overflow-tooltip />
            </el-table>
            <p
              v-if="bidDocument?.requirementMappingWarnings?.length"
              class="form-hint"
            >
              {{ bidDocument.requirementMappingWarnings[0] }}
            </p>
          </el-card>

          <el-card class="panel" shadow="never">
            <template #header>
              <div class="panel-header">
                <span class="panel-title">合规审查</span>
                <el-tag
                  v-if="bidDocument?.complianceReview"
                  size="small"
                  :type="complianceTagType(bidDocument.complianceReview.status)"
                  effect="plain"
                >
                  {{ complianceStatusLabel(bidDocument.complianceReview.status) }}
                </el-tag>
              </div>
            </template>
            <el-button
              type="warning"
              plain
              :icon="ShieldCheck"
              :loading="reviewingCompliance"
              style="width: 100%"
              @click="runComplianceReview"
            >
              执行 Pi 合规审查
            </el-button>
            <template v-if="bidDocument?.complianceReview">
              <div class="compliance-meta">
                <span>{{ complianceProviderLabel(bidDocument.complianceReview.provider) }}</span>
                <span>
                  要求 {{ bidDocument.complianceReview.coverage.requirementCovered }}/{{ bidDocument.complianceReview.coverage.requirementTotal }}
                </span>
                <span>
                  强制项 {{ bidDocument.complianceReview.coverage.mandatoryCovered }}/{{ bidDocument.complianceReview.coverage.mandatoryTotal }}
                </span>
              </div>
              <p v-if="bidDocument.complianceReview.summary" class="compliance-summary">
                {{ bidDocument.complianceReview.summary }}
              </p>
              <el-alert
                v-for="risk in bidDocument.complianceReview.riskItems?.slice(0, 5)"
                :key="risk.riskId || `${risk.title}-${risk.detail}`"
                :type="risk.level === '高' ? 'error' : risk.level === '中' ? 'warning' : 'info'"
                :closable="false"
                show-icon
                class="compliance-risk"
                :title="`${risk.title}${risk.sectionId ? ` · ${risk.sectionId}` : ''}`"
                :description="risk.suggestion ? `${risk.detail} 建议：${risk.suggestion}` : risk.detail"
              />
              <span v-if="!bidDocument.complianceReview.riskItems?.length" class="form-hint">未发现已记录风险</span>
            </template>
          </el-card>

          <el-card class="panel" shadow="never">
            <template #header><span class="panel-title">导出</span></template>
            <div class="export-layout-hint">
              {{ bidDocument?.submissionStructure?.mode === 'separate'
                ? `按招标文件要求分卷导出（${bidDocument.submissionStructure.volumes.length} 份）`
                : '导出一份 Word（目录中的卷保留为文档分组）' }}
            </div>
            <div class="mode-row">
              <el-radio-group v-model="mode">
                <el-radio-button label="open">明标</el-radio-button>
                <el-radio-button label="secret">暗标</el-radio-button>
              </el-radio-group>
            </div>
            <el-button type="success" :icon="Download" :loading="exporting" style="width: 100%" @click="exportDocument">
              导出 Word
            </el-button>
            <el-button
              :icon="Eye"
              :loading="previewingExport"
              style="width: 100%; margin: 8px 0 0"
              @click="previewExport"
            >
              导出预览
            </el-button>
            <div v-if="exportFiles.length" class="export-files">
              <div class="export-files-title">导出文件</div>
              <a v-for="f in exportFiles" :key="f.url" :href="f.url" target="_blank" class="export-file-link">
                <el-icon><Download /></el-icon>{{ f.name }}
              </a>
            </div>
            <el-alert
              v-if="bidDocument?.exportWarnings?.length"
              class="export-warning"
              type="warning"
              :closable="false"
              show-icon
              :title="`导出包含 ${bidDocument.exportWarnings.length} 项模板格式回退提醒`"
            />
          </el-card>
        </el-col>
      </el-row>

      <div class="step-actions">
        <el-button :icon="ArrowLeft" @click="goStep(2)">上一步（目录）</el-button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.bid-writing-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.stepper-bar {
  display: flex;
  align-items: flex-start;
  gap: 0;
  padding: 14px 18px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-card, #fff);
}
.stepper-item {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  position: relative;
}
.stepper-dot {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--bg-soft);
  color: var(--text-regular);
  font-size: 13px;
  border: 1px solid var(--border-color);
  flex-shrink: 0;
}
.stepper-item.active .stepper-dot {
  background: var(--primary);
  color: #fff;
  border-color: var(--primary);
}
.stepper-item.done .stepper-dot {
  background: var(--primary-soft, rgba(var(--primary-rgb), 0.15));
  color: var(--primary);
  border-color: var(--primary);
}
.stepper-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-regular);
  white-space: nowrap;
}
.stepper-item.active .stepper-label {
  color: var(--text-primary);
  font-weight: 600;
}
.stepper-line {
  flex: 1;
  height: 2px;
  min-width: 40px;
  margin: 0 12px;
  background: var(--border-color);
  border-radius: 1px;
}
.step-panel {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.editor-layout {
  --editor-sticky-top: 16px;
  align-items: flex-start;
}
.editor-layout-col {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
}
.editor-navigation-col,
.editor-document-col {
  min-height: 520px;
  position: sticky;
  top: var(--editor-sticky-top);
  align-self: flex-start;
}
.editor-layout-col > .nav-panel-viewport,
.editor-layout-col > .word-page-wrap {
  width: 100%;
  box-sizing: border-box;
}
.nav-panel-viewport {
  display: flex;
  width: 100%;
  height: 100%;
  min-height: 0;
}
.nav-panel-viewport > :deep(.el-card) {
  width: 100%;
  box-sizing: border-box;
}
.editor-side-col {
  height: auto;
  min-height: 0;
  align-self: flex-start;
  gap: 16px;
}
.editor-side-col > :deep(.el-card) {
  flex: 0 0 auto;
  overflow: visible;
}
.step-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  align-items: center;
}
.history-panel {
  margin-bottom: 16px;
}
.history-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.history-toolbar :deep(.el-input) {
  max-width: 360px;
}
.history-list {
  display: flex;
  flex-direction: column;
  max-height: 360px;
  overflow-y: auto;
  border: 1px solid var(--border-color);
  border-radius: 6px;
}
.history-item {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
  min-height: 58px;
  padding: 8px 12px;
  border: 0;
  border-bottom: 1px solid var(--border-color);
  border-radius: 0;
  background: var(--bg-card, #fff);
  color: inherit;
  font: inherit;
  appearance: none;
  text-align: left;
  cursor: pointer;
}
.history-item:last-child {
  border-bottom: 0;
}
.history-item.active {
  background: var(--primary-soft, rgba(var(--primary-rgb), 0.08));
}
.history-item:focus-visible {
  position: relative;
  z-index: 1;
  outline: 2px solid var(--primary-color);
  outline-offset: -2px;
}
.history-main {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 0;
  border: 0;
  background: transparent;
  color: inherit;
  text-align: left;
  cursor: pointer;
}
.history-title {
  max-width: 100%;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.history-meta {
  max-width: 100%;
  overflow: hidden;
  color: var(--text-regular);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.history-status {
  display: flex;
  align-items: center;
  flex-shrink: 0;
  gap: 8px;
}
.history-selected-icon {
  color: var(--primary-color);
}
.history-selection {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-top: 14px;
  padding: 14px 2px 0;
  border-top: 1px solid var(--border-color);
}
.history-selection-copy {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}
.history-selection-copy strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
@media (max-width: 720px) {
  .history-toolbar {
    align-items: stretch;
    flex-direction: column;
  }
  .history-toolbar :deep(.el-input) {
    max-width: none;
  }
  .history-toolbar :deep(.el-radio-group) {
    display: flex;
    width: 100%;
  }
  .history-toolbar :deep(.el-radio-button) {
    flex: 1;
  }
  .history-toolbar :deep(.el-radio-button__inner) {
    width: 100%;
  }
  .history-item {
    align-items: flex-start;
  }
  .history-status {
    padding-top: 2px;
  }
  .history-selection {
    align-items: stretch;
    flex-direction: column;
  }
  .history-selection :deep(.el-button) {
    align-self: flex-start;
  }
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.header-tags {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-end;
  gap: 8px;
  min-width: 0;
}
.plan-level-stats {
  height: auto;
  min-height: 24px;
  white-space: normal;
  line-height: 1.6;
}
.result-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--text-primary);
}
.evidence-entry-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 480px;
  overflow: auto;
}
.evidence-entry {
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  padding: 8px 10px;
}
.evidence-entry-head {
  display: flex;
  align-items: center;
  gap: 8px;
}
.evidence-entry-seq {
  flex: none;
  color: var(--el-text-color-secondary);
  font-variant-numeric: tabular-nums;
}
.evidence-entry-title {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.evidence-entry-refs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 10px;
  margin-top: 6px;
}
.evidence-ref {
  font-size: 12px;
  color: var(--el-text-color-regular);
  font-family: var(--el-font-family-mono, monospace);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}
.evidence-ref-missing {
  color: var(--el-color-warning);
}
.panel-title {
  font-weight: 600;
  color: var(--text-primary);
  font-size: 13px;
}
.field {
  width: 100%;
}
.select-option {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.select-option small {
  color: var(--text-regular);
  font-size: 12px;
}
.project-current strong {
  color: var(--text-primary);
}
.form-hint {
  color: var(--text-regular);
  font-size: 12px;
}
.upload-zone {
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  padding: 22px;
  text-align: center;
  color: var(--text-regular);
  cursor: pointer;
  transition: border-color 0.15s;
  margin-bottom: 12px;
}
.upload-zone:hover {
  border-color: var(--primary);
}
.upload-zone p {
  margin: 8px 0 4px;
  color: var(--text-primary);
}
.cloud-result-panel {
  overflow: hidden;
}
.cloud-result-panel :deep(.el-progress) {
  margin-bottom: 10px;
}
.cloud-agent-progress {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 28px;
  margin-bottom: 12px;
  color: var(--text-regular);
  font-size: 13px;
}
.cloud-agent-progress strong {
  color: var(--text-primary);
  font-weight: 600;
}
.cloud-error {
  margin: 0 0 12px;
  color: var(--danger, #f56c6c);
  white-space: pre-wrap;
  word-break: break-word;
}
.cloud-meta-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 14px;
}
.cloud-meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  font-size: 12px;
}
.cloud-meta-item span {
  color: var(--text-regular);
}
.cloud-meta-item strong {
  overflow: hidden;
  color: var(--text-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}
.cloud-table {
  margin-bottom: 12px;
}
.cloud-warning {
  margin-top: 8px;
}
.plan-source-alert {
  margin-bottom: 12px;
}
.plan-source-title {
  margin-right: 8px;
  font-weight: 600;
}
.plan-source-detail {
  color: var(--text-regular);
  font-weight: 400;
}
.toc-tree-wrap {
  overflow-x: auto;
  margin-bottom: 12px;
  border: 1px solid var(--border-color);
}
.parse-submission-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 14px;
  margin-bottom: 10px;
  font-size: 13px;
}
.export-layout-hint {
  margin-bottom: 10px;
  color: var(--text-regular);
  font-size: 12px;
  line-height: 1.5;
}
.toc-tree {
  min-width: 960px;
}
.toc-tree :deep(.el-tree-node__content) {
  height: auto;
  min-height: 42px;
  border-bottom: 1px solid var(--border-color);
}
.toc-tree-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
  min-width: 0;
  padding: 5px 8px 5px 0;
}
.toc-volume-row {
  font-weight: 600;
}
.toc-role {
  flex: 0 0 22px;
  color: var(--text-regular);
}
.toc-title-input {
  min-width: 150px;
  flex: 1 1 220px;
}
.toc-row-actions {
  display: flex;
  flex: 0 0 154px;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
}
.toc-add-trigger {
  display: inline-flex;
  align-items: center;
  gap: 2px;
}
.toc-add-trigger :deep(.el-icon + .el-icon) {
  margin-left: -2px;
  font-size: 12px;
}
.toc-number {
  color: var(--primary, #409eff);
  font-weight: 700;
  min-width: 22px;
}
.toc-inline-add {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin: 0 8px 8px 34px;
  padding: 8px 10px;
  border: 1px solid var(--el-color-primary-light-5, #b3d8ff);
  border-radius: 6px;
  background: var(--el-color-primary-light-9, #ecf5ff);
}
.toc-inline-add-label {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  color: var(--el-color-primary, #409eff);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}
.toc-inline-add-label small {
  color: var(--text-regular);
  font-weight: 400;
}
.toc-inline-add-title {
  flex: 1 1 240px;
  min-width: 180px;
}
.nav-panel {
  flex: 1 0 auto;
  height: 100%;
  min-height: 0;
  margin-bottom: 0;
  box-sizing: border-box;
  border-bottom: 0;
  border-radius: var(--radius) var(--radius) 0 0;
  box-shadow: none;
  overflow-y: auto;
}
.nav-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 6px;
  padding: 8px 10px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
  transition: background 0.12s;
}
.nav-item:hover {
  background: var(--bg-soft);
}
.nav-item.active {
  background: var(--primary-soft, rgba(var(--primary-rgb), 0.12));
}
.nav-title {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 6px;
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav-collapse-button,
.nav-collapse-placeholder {
  width: 18px;
  height: 18px;
  flex: 0 0 18px;
}
.nav-collapse-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  background: transparent;
  color: #7b8491;
  cursor: pointer;
  border-radius: 3px;
}
.nav-collapse-button:hover {
  color: var(--primary, #409eff);
  background: #edf5ff;
}
.nav-collapse-button svg {
  transition: transform 0.18s ease;
}
.nav-collapse-button svg.collapsed {
  transform: rotate(-90deg);
}
@media (max-width: 900px) {
  .toc-inline-add {
    margin-left: 12px;
  }
}
.nav-number {
  color: var(--primary, #409eff);
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
}
.editor-actions {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
.info-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.info-row {
  display: flex;
  justify-content: space-between;
  gap: 8px;
  font-size: 12px;
}
.info-key {
  color: var(--text-regular);
  flex-shrink: 0;
}
.info-value {
  color: var(--text-primary);
  text-align: right;
  word-break: break-all;
}
.todo-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.todo-item {
  font-size: 12px;
  color: var(--warning, #e6a23c);
  padding: 4px 0;
}
.mode-row {
  margin-bottom: 10px;
}
.export-files {
  margin-top: 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.export-files-title {
  font-size: 12px;
  color: var(--text-regular);
}
.export-preview-files {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}
.export-preview-viewport {
  height: min(76vh, 900px);
  overflow: auto;
  background: #eef0f3;
}
.export-preview-document {
  min-height: 100%;
  padding: 18px 20px 26px;
}
.export-preview-document :deep(.codex-export-docx-wrapper) {
  align-items: center;
  min-width: max-content;
  background: #eef0f3;
}
.export-preview-document :deep(section.codex-export-docx) {
  margin-bottom: 18px;
  box-shadow: 0 1px 5px rgba(31, 35, 41, 0.16);
}
.export-preview-document :deep(section.codex-export-docx:last-child) {
  margin-bottom: 0;
}
.export-file-link {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--primary);
  font-size: 13px;
  text-decoration: none;
}
.word-page-wrap {
  width: 100%;
  height: 100%;
  min-height: 0;
  display: flex;
  align-items: flex-start;
  background: #ffffff;
  border-radius: 8px 8px 0 0;
  padding: 0;
  box-sizing: border-box;
  overflow-y: auto;
  overflow-x: hidden;
  overscroll-behavior: contain;
  scrollbar-gutter: stable;
}
.word-page {
  flex: 0 0 auto;
  background: #ffffff;
  width: 100%;
  max-width: none;
  margin: 0 auto;
  min-height: 100%;
  box-sizing: border-box;
  padding: 46px 54px 80px;
  box-shadow: none;
  color: #1f2329;
  font-size: 14px;
  line-height: 1.85;
}
.doc-section {
  scroll-margin-top: 16px;
}
.doc-heading {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
  margin: 30px 0 12px;
  padding: 4px 8px;
  border-bottom: none;
  cursor: pointer;
  border-radius: 4px;
}
.doc-section.selected .doc-heading {
  background: #f0f7ff;
  border-left: 3px solid var(--primary, #409eff);
  padding-left: 12px;
}
.section-warning {
  margin: 6px 0 10px;
}
.section-trace {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  margin: 6px 0 10px;
}
.chapter-num {
  font-size: 12px;
  font-weight: 700;
  color: var(--primary, #409eff);
  letter-spacing: 0.5px;
}
.doc-title {
  margin: 0;
  font-size: 17px;
  font-weight: 600;
  color: #1f2329;
  flex: 1;
}
.doc-body {
  cursor: text;
  min-height: 1.2em;
  color: #1f2329;
}
.doc-empty {
  color: #b3b8c2;
  font-style: italic;
}
.doc-group-hint {
  color: var(--text-regular);
  font-size: 13px;
}
.doc-editor {
  margin-top: 4px;
}
.doc-toolbar {
  position: sticky;
  top: 8px;
  z-index: 2;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 2px;
  margin-bottom: 10px;
  padding: 5px 8px;
  border: 1px solid #e2e6eb;
  background: rgba(255, 255, 255, 0.96);
  box-shadow: 0 2px 8px rgba(31, 35, 41, 0.06);
  border-radius: 4px;
}
.toolbar-divider {
  width: 1px;
  height: 18px;
  margin: 0 4px;
  background: #dfe3e8;
}
.doc-rich-editor {
  min-height: 260px;
  padding: 18px 0 28px;
  border: none;
  border-radius: 0;
  background: transparent;
  color: #1f2329;
  outline: none;
  font-size: 14px;
  line-height: 1.85;
  box-shadow: none;
}
.doc-rich-editor:focus {
  border: none;
  box-shadow: none;
}
.doc-rich-editor:empty::before {
  content: attr(data-placeholder);
  color: #a8b0bb;
  pointer-events: none;
}
.doc-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
  padding: 10px 12px;
  background: #f0f7ff;
  border-radius: 6px;
}
.doc-body :deep(h1),
.doc-body :deep(h2),
.doc-body :deep(h3),
.doc-body :deep(h4),
.doc-body :deep(h5),
.doc-body :deep(h6) {
  margin: 16px 0 8px;
  font-weight: 600;
  color: #1f2329;
  line-height: 1.4;
}
.doc-body :deep(h1) { font-size: 18px; }
.doc-body :deep(h2) { font-size: 16px; }
.doc-body :deep(h3),
.doc-body :deep(h4) { font-size: 15px; }
.doc-body :deep(p) { margin: 0 0 8px; }
.doc-body :deep(blockquote) {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid var(--primary, #409eff);
  background: #f6f9fc;
  color: #555c66;
  border-radius: 0 4px 4px 0;
}
.doc-body :deep(ul),
.doc-body :deep(ol) { margin: 8px 0; padding-left: 22px; }
.doc-body :deep(li) { margin: 3px 0; }
.doc-body :deep(table),
.doc-rich-editor :deep(table) {
  width: 100%;
  margin: 12px 0 16px;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 13px;
}
.doc-body :deep(th),
.doc-body :deep(td),
.doc-rich-editor :deep(th),
.doc-rich-editor :deep(td) {
  min-width: 60px;
  padding: 7px 9px;
  border: 1px solid #cfd6df;
  vertical-align: top;
  text-align: left;
  word-break: break-word;
}
.doc-body :deep(th),
.doc-rich-editor :deep(th) {
  background: #f5f7fa;
  font-weight: 600;
}
.doc-rich-editor :deep(h1),
.doc-rich-editor :deep(h2),
.doc-rich-editor :deep(h3),
.doc-rich-editor :deep(h4),
.doc-rich-editor :deep(h5),
.doc-rich-editor :deep(h6) {
  margin: 16px 0 8px;
  color: #1f2329;
  line-height: 1.4;
}
.doc-rich-editor :deep(h1) { font-size: 21px; }
.doc-rich-editor :deep(h2) { font-size: 18px; }
.doc-rich-editor :deep(h3),
.doc-rich-editor :deep(h4) { font-size: 16px; }
.doc-rich-editor :deep(p),
.doc-rich-editor :deep(div) { margin: 0 0 8px; }
.doc-rich-editor :deep(blockquote) {
  margin: 8px 0;
  padding: 6px 12px;
  border-left: 3px solid var(--primary, #409eff);
  background: #f6f9fc;
  color: #555c66;
}
.doc-rich-editor :deep(ul),
.doc-rich-editor :deep(ol) {
  margin: 8px 0;
  padding-left: 24px;
}
.doc-rich-editor :deep(.template-page) {
  color: #111;
  font-family: "宋体", SimSun, serif;
  font-size: 12pt;
  line-height: 1.5;
}
.doc-rich-editor :deep(.template-paragraph) {
  margin: 0 0 0.5em;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.doc-rich-editor :deep(.template-table) {
  width: 100%;
  margin: 10px 0 14px;
  border-collapse: collapse;
  table-layout: auto;
  color: inherit;
  font: inherit;
}
.doc-rich-editor :deep(.template-table th),
.doc-rich-editor :deep(.template-table td) {
  min-width: 36px;
  padding: 4px 6px;
  border: 1px solid #222;
  background: transparent;
  color: inherit;
  font: inherit;
  vertical-align: middle;
}
.doc-rich-editor :deep(.template-table th) {
  font-weight: 700;
}
.structured-template-editor {
  min-height: 320px;
  padding-top: 8px;
  background: #fff;
}
@media (max-width: 991px) {
  .editor-layout-col {
    height: auto;
    min-height: 0;
  }
  .editor-navigation-col,
  .editor-document-col {
    height: auto;
    min-height: 0;
    position: static;
  }
  .nav-panel-viewport {
    height: auto;
    max-height: 360px;
  }
  .nav-panel {
    height: auto;
    max-height: 360px;
  }
  .word-page-wrap {
    height: auto;
    min-height: 0;
    max-height: none;
    overflow: visible;
  }
  .word-page {
    height: auto;
    min-height: 520px;
  }
}
.doc-body :deep(mark) {
  background: #fff3cd;
  color: #856404;
  padding: 0 4px;
  border-radius: 3px;
}
.doc-body :deep(a) { color: var(--primary, #409eff); }
/* 证明材料章节：正文内联的真实扫描件（v-html 渲染，需 :deep 命中） */
.doc-rich-editor :deep(.evidence-inline-image),
.doc-body :deep(.evidence-inline-image) {
  max-width: 100%;
  height: auto;
  display: block;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 4px;
}
.doc-rich-editor :deep(.evidence-image-block),
.doc-body :deep(.evidence-image-block) {
  margin: 10px 0;
  text-align: center;
}
.evidence-entry-thumbs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}
.evidence-thumb {
  width: 96px;
  height: 96px;
  border-radius: 4px;
  border: 1px solid var(--el-border-color-lighter);
  overflow: hidden;
}
.evidence-thumb :deep(img) {
  object-fit: cover;
}
.evidence-thumb-fallback {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  background: var(--el-fill-color-light);
}

/* 目录规划诊断面板（只读） */
.diagnosis-panel {
  margin: 12px 0 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-blank);
}
.diagnosis-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
  user-select: none;
}
.diagnosis-title {
  font-weight: 600;
  font-size: 13px;
}
.diagnosis-spacer {
  flex: 1;
}
.diagnosis-caret {
  transition: transform 0.2s ease;
}
.diagnosis-caret.is-open {
  transform: rotate(180deg);
}
.diagnosis-body {
  padding: 4px 12px 12px;
  border-top: 1px solid var(--el-border-color-lighter);
}
.diagnosis-stats {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 18px;
  padding: 8px 0;
  color: var(--el-text-color-regular);
  font-size: 12px;
}
.diagnosis-alert {
  margin-bottom: 6px;
}
.diagnosis-columns {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
  gap: 12px;
  margin-top: 6px;
}
.diagnosis-decided {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--el-border-color-lighter);
}
.diagnosis-decided > header {
  margin-bottom: 6px;
  font-weight: 600;
  font-size: 12px;
  color: var(--el-text-color-primary);
}
.diagnosis-decided-hint {
  margin-left: 8px;
  font-weight: 400;
  color: var(--el-text-color-secondary);
}
.diagnosis-column > header {
  margin-bottom: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}
.diagnosis-empty {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.diagnosis-card {
  padding: 8px 10px;
  margin-bottom: 8px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-light);
}
.diagnosis-card-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  font-size: 13px;
}
.diagnosis-card-meta,
.diagnosis-card-reason,
.diagnosis-card-basis,
.diagnosis-card-eval {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--el-text-color-regular);
}
.diagnosis-card-basis {
  color: var(--el-text-color-secondary);
}
.diagnosis-card-eval {
  color: var(--el-color-primary);
}
.diagnosis-samples {
  margin-top: 10px;
  font-size: 12px;
  color: var(--el-text-color-regular);
}
.diagnosis-samples > header {
  margin-bottom: 4px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}
.diagnosis-samples > p {
  margin: 2px 0;
  line-height: 1.5;
}
.diagnosis-sample-id {
  display: inline-block;
  min-width: 132px;
  color: var(--el-text-color-secondary);
}
.diagnosis-sample-domain {
  display: inline-block;
  min-width: 96px;
  color: var(--el-text-color-placeholder);
}
</style>
