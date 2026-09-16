<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import {
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  BriefcaseBusiness,
  Check,
  Download,
  FileText,
  FolderPlus,
  ListTree,
  Plus,
  Trash2,
  Upload,
  Wand2,
} from "lucide-vue-next";
import { api } from "../../services/api";
import type {
  BidDocument,
  BidMode,
  BidSection,
  CatalogItem,
  LibrarySummary,
  MaterialProfile,
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
  evaluate: [];
  "update:input-kind": [kind: "notice" | "tender_document"];
}>();

// ---- 状态 ----
const activeStep = ref(1);
const bidDocument = ref<BidDocument | null>(null);
const mode = ref<BidMode>("open");
const planning = ref(false);
const savingPlan = ref(false);
const generatingSectionId = ref("");
const savingSectionId = ref("");
const exporting = ref(false);
const exportFiles = ref<{ name: string; url: string }[]>([]);

const currentSectionId = ref("");
const newSectionTitle = ref("");
const newSectionSource = ref("template");
const fileInput = ref<HTMLInputElement | null>(null);

// ---- 从共享状态同步 bidDocument ----
watch(
  () => props.workspace?.bidDocument as BidDocument | undefined,
  (doc) => {
    if (doc && doc.plan?.length) {
      bidDocument.value = doc;
      if (doc.mode) mode.value = doc.mode;
      if (!currentSectionId.value) currentSectionId.value = doc.plan[0]?.sectionId || "";
    }
  },
  { immediate: true }
);

const workspaceId = computed(() => props.workspace?.id || "");
const plan = computed(() => bidDocument.value?.plan || []);

// ---- 工具函数 ----
const SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: "template", label: "模板文书" },
  { value: "ai", label: "AI 撰写" },
  { value: "table", label: "事实表格" },
  { value: "copied_template", label: "复制招标格式" },
  { value: "deviation", label: "偏离表" },
  { value: "attachment", label: "附件" }
];

function sourceLabel(source?: string): string {
  return SOURCE_OPTIONS.find((o) => o.value === source)?.label || source || "—";
}

function kindForSource(source?: string): string {
  if (source === "deviation") return "deviation";
  if (source === "attachment") return "attachment";
  if (source === "ai") return "technical";
  return "commercial";
}

const STATUS_NAMES: Record<string, string> = {
  generated: "已生成",
  pending: "待生成",
  generating: "生成中",
  missing: "缺失",
  planned: "已规划"
};
function statusName(status?: string): string {
  return STATUS_NAMES[status || ""] || status || "待生成";
}
function statusTagType(status?: string): "success" | "warning" | "danger" | "info" {
  const s = (status || "").toLowerCase();
  if (["generated", "done", "complete", "success", "planned", "完成", "已生成"].includes(s)) return "success";
  if (["pending", "generating", "待生成", "生成中"].includes(s)) return "warning";
  if (["missing", "缺失", "failed", "error", "失败"].includes(s)) return "danger";
  return "info";
}

const analyzedWorkspaces = computed(() =>
  [...props.workspaces].sort((a, b) => {
    const aFine = a.reasoning?.fineReview?.rows?.length || 0;
    const bFine = b.reasoning?.fineReview?.rows?.length || 0;
    return bFine - aFine;
  })
);

const blanksToFill = computed(() => {
  const set = new Set<string>();
  plan.value.forEach((s) => (s.blanksToFill || []).forEach((b) => set.add(b)));
  return [...set];
});
const pendingSections = computed(() => plan.value.filter((s) => !s.content?.trim()));

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

function onSelectWorkspace(id: string) {
  const ws = props.workspaces.find((w) => w.id === id);
  if (ws) emit("select-workspace", ws);
}

function chooseFile() {
  fileInput.value?.click();
}
function onFileChange(event: Event) {
  emit("file-selected", (event.target as HTMLInputElement).files?.[0] || null);
}
function startImport() {
  emit("evaluate");
}

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
    if (!currentSectionId.value) currentSectionId.value = result.bidDocument.plan?.[0]?.sectionId || "";
    activeStep.value = 2;
    ElMessage.success(
      result.bidDocument.planSource === "tender_document"
        ? "已按招标文件格式生成目录，可编辑后进入正文"
        : "已生成标准目录（招标文件未提供格式），可编辑后进入正文"
    );
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    planning.value = false;
  }
}

// ---- 第二步：目录编辑 ----
function moveSection(index: number, dir: -1 | 1) {
  const target = index + dir;
  if (target < 0 || target >= plan.value.length) return;
  const arr = [...plan.value];
  const [item] = arr.splice(index, 1);
  arr.splice(target, 0, item);
  plan.value.splice(0, plan.value.length, ...arr);
  markPlanDirty();
}
function removeSection(index: number) {
  plan.value.splice(index, 1);
  markPlanDirty();
}
function addSection() {
  const title = newSectionTitle.value.trim();
  if (!title) {
    ElMessage.warning("请输入章节标题");
    return;
  }
  const id = `s-${Date.now()}`;
  plan.value.push({
    sectionId: id,
    title,
    source: newSectionSource.value,
    kind: kindForSource(newSectionSource.value),
    content: "",
    status: "pending",
    requirementIds: [],
    sourceRefs: []
  });
  newSectionTitle.value = "";
  markPlanDirty();
}
function markPlanDirty() {
  if (bidDocument.value) bidDocument.value.plan = [...plan.value];
}
async function savePlan() {
  if (!workspaceId.value) return;
  savingPlan.value = true;
  try {
    const result = await api.bidDocumentSavePlan(workspaceId.value, plan.value);
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
function scrollToSection(id: string) {
  currentSectionId.value = id;
  requestAnimationFrame(() => {
    document.getElementById(`sec-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
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
    ElMessage.success("本章已自动撰写，可继续编辑");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    generatingSectionId.value = "";
  }
}

async function saveSection(sectionId: string) {
  const sec = plan.value.find((s) => s.sectionId === sectionId);
  if (!workspaceId.value || !sec) return;
  savingSectionId.value = sectionId;
  try {
    const result = await api.bidDocumentSaveSection(workspaceId.value, sectionId, sec.content || "");
    bidDocument.value = result.bidDocument;
    emit("update:bid-document", result.bidDocument);
    ElMessage.success("本章已保存");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    savingSectionId.value = "";
  }
}

async function exportDocument() {
  if (!workspaceId.value) return;
  exporting.value = true;
  try {
    const result = await api.bidDocumentExport(workspaceId.value, mode.value);
    exportFiles.value = result.files || [];
    ElMessage.success(result.message || "导出成功");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    exporting.value = false;
  }
}
</script>

<template>
  <div class="bid-writing-page">
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
      <el-card class="panel" shadow="never">
        <template #header>
          <div class="panel-header">
            <div class="result-title"><el-icon><BriefcaseBusiness /></el-icon><span>选择已匹配的项目</span></div>
            <el-tag type="info" effect="plain">优先选择已完成匹配评估的项目</el-tag>
          </div>
        </template>
        <el-row :gutter="12" align="middle">
          <el-col :xs="24" :md="16">
            <el-select
              :model-value="workspace?.id || ''"
              class="field"
              filterable
              placeholder="选择已匹配评估的项目"
              @update:model-value="onSelectWorkspace"
            >
              <el-option v-for="ws in analyzedWorkspaces" :key="ws.id" :label="ws.title || ws.project.title" :value="ws.id">
                <div class="select-option">
                  <strong>{{ ws.title || ws.project.title }}</strong>
                  <small>{{ ws.reasoning.fineReview ? `已拆解 ${ws.reasoning.fineReview.clauseCount} 条条款` : "公告资格粗匹配" }}</small>
                </div>
              </el-option>
            </el-select>
          </el-col>
          <el-col :xs="24" :md="8">
            <div v-if="workspace" class="project-current">
              <span class="form-hint">当前项目：</span>
              <strong>{{ project?.title || workspace.title }}</strong>
            </div>
            <span v-else class="form-hint">尚未选择项目</span>
          </el-col>
        </el-row>
      </el-card>

      <el-card class="panel" shadow="never">
        <template #header>
          <div class="panel-header">
            <div class="result-title"><el-icon><Upload /></el-icon><span>或上传新招标文件</span></div>
            <el-tag type="warning" effect="plain">上传后需完成解析与匹配评估</el-tag>
          </div>
        </template>
        <div class="upload-zone" @click="chooseFile">
          <el-icon :size="28"><Upload /></el-icon>
          <p>{{ importFile?.name || "点击选择招标文件（PDF / Word）" }}</p>
          <span class="form-hint">选择后点击「上传并解析」，解析完成后自动出现在上方项目列表</span>
        </div>
        <input ref="fileInput" type="file" hidden accept=".pdf,.doc,.docx" @change="onFileChange" />
        <div class="step-actions">
          <el-button type="primary" :loading="busy" @click="startImport">上传并解析</el-button>
        </div>
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
              <el-tag v-if="bidDocument?.planSource" size="small" :type="bidDocument.planSource === 'tender_document' ? 'success' : 'info'" effect="plain">
                {{ bidDocument.planSource === 'tender_document' ? '来自招标文件格式' : '内置标准模板' }}
              </el-tag>
              <el-tag size="small" type="info" effect="plain">{{ plan.length }} 章</el-tag>
            </div>
          </div>
        </template>

        <el-table :data="plan" size="small" border class="toc-table">
          <el-table-column type="index" label="#" width="52" />
          <el-table-column label="章节标题" min-width="220">
            <template #default="{ row }">
              <el-input v-model="row.title" size="small" />
            </template>
          </el-table-column>
          <el-table-column label="类型" width="130">
            <template #default="{ row }">
              <el-select v-model="row.source" size="small">
                <el-option v-for="o in SOURCE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
              </el-select>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="92">
            <template #default="{ row }">
              <el-tag size="small" :type="statusTagType(row.status)" effect="plain">{{ statusName(row.status) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="120" align="center">
            <template #default="{ $index }">
              <el-button size="small" text :icon="ArrowUp" :disabled="$index === 0" @click="moveSection($index, -1)" />
              <el-button size="small" text :icon="ArrowDown" :disabled="$index === plan.length - 1" @click="moveSection($index, 1)" />
              <el-button size="small" text type="danger" :icon="Trash2" @click="removeSection($index)" />
            </template>
          </el-table-column>
        </el-table>

        <div class="add-section-row">
          <el-input v-model="newSectionTitle" size="small" placeholder="新章节标题" style="max-width: 260px" />
          <el-select v-model="newSectionSource" size="small" style="width: 130px">
            <el-option v-for="o in SOURCE_OPTIONS" :key="o.value" :label="o.label" :value="o.value" />
          </el-select>
          <el-button size="small" :icon="Plus" @click="addSection">添加章节</el-button>
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
      <el-row :gutter="16">
        <el-col :xs="24" :md="5">
          <el-card class="panel nav-panel" shadow="never">
            <template #header><span class="panel-title">章节导航</span></template>
            <div
              v-for="sec in plan"
              :key="sec.sectionId"
              class="nav-item"
              :class="{ active: currentSectionId === sec.sectionId }"
              @click="scrollToSection(sec.sectionId)"
            >
              <span class="nav-title">{{ sec.title }}</span>
              <el-tag size="small" :type="statusTagType(sec.status)" effect="plain">{{ statusName(sec.status) }}</el-tag>
            </div>
            <el-empty v-if="!plan.length" description="暂无目录，请先回到第二步" :image-size="60" />
          </el-card>
        </el-col>

        <el-col :xs="24" :md="13">
          <el-card class="panel doc-panel" shadow="never">
            <template #header>
              <div class="panel-header">
                <div class="result-title"><el-icon><FileText /></el-icon><span>全文编辑</span></div>
                <el-tag size="small" type="info" effect="plain">{{ plan.length }} 章</el-tag>
              </div>
            </template>
            <div class="doc-scroll">
              <div
                v-for="(sec, i) in plan"
                :key="sec.sectionId"
                :id="`sec-${sec.sectionId}`"
                class="doc-chapter"
              >
                <div class="chapter-head">
                  <span class="chapter-index">{{ i + 1 }}</span>
                  <h3 class="chapter-title">{{ sec.title }}</h3>
                  <el-tag size="small" :type="statusTagType(sec.status)" effect="plain">{{ statusName(sec.status) }}</el-tag>
                  <el-tag size="small" type="info" effect="plain">{{ sourceLabel(sec.source) }}</el-tag>
                  <div class="chapter-actions">
                    <el-button size="small" type="primary" :icon="Wand2" :loading="generatingSectionId === sec.sectionId" @click="generateSection(sec.sectionId)">自动撰写</el-button>
                    <el-button size="small" :icon="Check" :loading="savingSectionId === sec.sectionId" @click="saveSection(sec.sectionId)">保存</el-button>
                  </div>
                </div>
                <el-input
                  v-model="sec.content"
                  type="textarea"
                  :autosize="{ minRows: 3 }"
                  placeholder="本章正文…可点「自动撰写」填充；图片/附件粘贴地址或 ![](路径)"
                />
              </div>
              <el-empty v-if="!plan.length" description="暂无目录，请先回到第二步编辑目录" :image-size="80" />
            </div>
          </el-card>
        </el-col>

        <el-col :xs="24" :md="6">
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
            <template #header><span class="panel-title">待办提醒</span></template>
            <div v-if="blanksToFill.length" class="todo-list">
              <div v-for="b in blanksToFill" :key="b" class="todo-item">⚠ {{ b }}</div>
            </div>
            <div v-if="pendingSections.length" class="todo-item">⚠ {{ pendingSections.length }} 个章节尚无内容</div>
            <span v-if="!blanksToFill.length && !pendingSections.length" class="form-hint">暂无待办</span>
          </el-card>

          <el-card class="panel" shadow="never">
            <template #header><span class="panel-title">导出</span></template>
            <div class="mode-row">
              <el-radio-group v-model="mode">
                <el-radio-button label="open">明标</el-radio-button>
                <el-radio-button label="secret">暗标</el-radio-button>
              </el-radio-group>
            </div>
            <el-button type="success" :icon="Download" :loading="exporting" style="width: 100%" @click="exportDocument">
              导出 Word
            </el-button>
            <div v-if="exportFiles.length" class="export-files">
              <div class="export-files-title">导出文件</div>
              <a v-for="f in exportFiles" :key="f.url" :href="f.url" target="_blank" class="export-file-link">
                <el-icon><Download /></el-icon>{{ f.name }}
              </a>
            </div>
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
.step-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  align-items: center;
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.header-tags {
  display: flex;
  gap: 8px;
}
.result-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--text-primary);
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
.toc-table {
  margin-bottom: 12px;
}
.add-section-row {
  display: flex;
  gap: 8px;
  align-items: center;
}
.nav-panel {
  position: sticky;
  top: 0;
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
  font-size: 13px;
  color: var(--text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
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
.export-file-link {
  display: flex;
  align-items: center;
  gap: 6px;
  color: var(--primary);
  font-size: 13px;
  text-decoration: none;
}
.doc-panel {
  display: flex;
  flex-direction: column;
}
.doc-scroll {
  max-height: 68vh;
  overflow-y: auto;
  padding-right: 4px;
}
.doc-chapter {
  border: 1px solid var(--border-color);
  border-radius: 8px;
  padding: 12px 14px;
  margin-bottom: 14px;
  scroll-margin-top: 12px;
}
.chapter-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.chapter-index {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--primary-soft, rgba(64, 158, 255, 0.14));
  color: var(--primary);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}
.chapter-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-primary);
  flex: 1;
  min-width: 120px;
}
.chapter-actions {
  display: flex;
  gap: 6px;
  margin-left: auto;
}
</style>
