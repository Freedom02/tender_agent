<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";
import { BriefcaseBusiness, Database, FileText, ListChecks, Rocket, ShieldCheck, Upload } from "lucide-vue-next";
import { api } from "../../services/api";
import type {
  BidDocument,
  BidMode,
  CatalogItem,
  DeviationRow,
  LibrarySummary,
  MaterialProfile,
  ProofreadResult,
  Workspace
} from "../../shared/types";

const props = defineProps<{
  workspace: Workspace | null;
  project: CatalogItem | null;
  libraries: LibrarySummary[];
  selectedLibraryKey: string;
  materialsProfile: MaterialProfile | null;
  busy: boolean;
}>();

const emit = defineEmits<{
  generate: [];
  "update:selected-library-key": [key: string];
}>();

const fileInput = ref<HTMLInputElement | null>(null);
const tenderFile = ref<File | null>(null);
const templateKey = ref("standard");
const industryKey = ref("human-resources");
const mode = ref<BidMode>("open");
const planning = ref(false);
const generating = ref(false);
const proofreading = ref(false);
const bidDocument = ref<BidDocument | null>(null);
const proofread = ref<ProofreadResult | null>(null);

const fileName = computed(() => tenderFile.value?.name || props.project?.title || "");
const selectedLibrary = computed(
  () => props.libraries.find((library) => library.key === props.selectedLibraryKey) || props.libraries[0] || null
);

const templates = [
  { value: "standard", label: "标准服务类响应文件", detail: "适用于咨询、服务采购和综合服务项目" },
  { value: "technical", label: "技术服务方案模板", detail: "突出技术方案、实施计划和交付保障" },
  { value: "digital-twin", label: "数字孪生技术服务模板", detail: "适用于数字孪生、数据智能和技术服务项目" }
];

const industries = [
  { value: "digital-twin", label: "数字孪生" },
  { value: "data-intelligence", label: "数据智能" },
  { value: "smart-park", label: "智慧园区" },
  { value: "data-center", label: "数据中心与运维" },
  { value: "other", label: "其他行业" }
];

const chapterTree = computed(() => {
  const plan = bidDocument.value?.plan || [];
  if (!plan.length) return [];
  return [
    {
      id: "root",
      label: props.project?.title || "响应文件",
      children: plan.map((section, index) => ({
        id: section.sectionId || `section-${index}`,
        label: section.title || `第 ${index + 1} 章`,
        section
      }))
    }
  ];
});

const deviationTables = computed<Array<{ key: string; title: string; rows: DeviationRow[] }>>(() => [
  {
    key: "technical",
    title: "技术偏离表",
    rows: bidDocument.value?.deviationTables?.technical || []
  },
  {
    key: "commercial",
    title: "商务偏离表",
    rows: bidDocument.value?.deviationTables?.commercial || []
  }
]);

function sectionStatusType(status?: string): "success" | "warning" | "danger" | "info" {
  const value = (status || "").toLowerCase();
  if (["完成", "已生成", "done", "complete", "success"].includes(value)) return "success";
  if (["生成中", "待生成", "pending", "generating"].includes(value)) return "warning";
  if (["失败", "failed", "error"].includes(value)) return "danger";
  return "info";
}

function riskAlertType(level?: string): "error" | "warning" | "success" | "info" {
  const value = (level || "").toLowerCase();
  if (["critical", "high", "error", "严重", "高", "重大"].includes(value)) return "error";
  if (["warning", "medium", "中", "中等", "一般"].includes(value)) return "warning";
  if (["success", "low", "低", "通过"].includes(value)) return "success";
  return "info";
}

function deviationType(deviation?: string): "success" | "warning" | "danger" | "info" {
  const value = (deviation || "").trim();
  if (value === "无偏离" || value === "正偏离") return "success";
  if (value === "负偏离") return "danger";
  if (value === "待人工确认") return "warning";
  if (value === "不适用") return "info";
  return "info";
}

async function planDocument() {
  const workspaceId = props.workspace?.id;
  if (!workspaceId) {
    ElMessage.warning("请先选择一个工作台项目");
    return;
  }
  planning.value = true;
  try {
    const result = await api.bidDocumentPlan(workspaceId);
    bidDocument.value = result.bidDocument;
    proofread.value = null;
    ElMessage.success("标书规划完成，请审阅章节结构与偏离表");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    planning.value = false;
  }
}

async function generateContent() {
  const workspaceId = props.workspace?.id;
  if (!workspaceId) {
    ElMessage.warning("请先选择一个工作台项目");
    return;
  }
  if (!bidDocument.value) {
    ElMessage.warning("请先点击「1. 规划标书」");
    return;
  }
  generating.value = true;
  try {
    const result = await api.bidDocumentGenerate(workspaceId, mode.value);
    bidDocument.value = result.bidDocument;
    if (result.bidDocument.mode) mode.value = result.bidDocument.mode;
    ElMessage.success("标书内容已生成");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    generating.value = false;
  }
}

async function runProofread() {
  const workspaceId = props.workspace?.id;
  if (!workspaceId) {
    ElMessage.warning("请先选择一个工作台项目");
    return;
  }
  if (!bidDocument.value) {
    ElMessage.warning("请先规划并生成标书");
    return;
  }
  proofreading.value = true;
  try {
    proofread.value = await api.proofread(workspaceId);
    ElMessage.success("校对完成");
  } catch (error) {
    proofread.value = null;
    ElMessage.error((error as Error).message);
  } finally {
    proofreading.value = false;
  }
}

function chooseFile() {
  fileInput.value?.click();
}

function onFileChange(event: Event) {
  tenderFile.value = (event.target as HTMLInputElement).files?.[0] || null;
}
</script>

<template>
  <div class="bid-workspace-page">
    <el-card class="panel generator-panel" shadow="never">
      <template #header>
        <div class="panel-header">
          <div>
            <div class="result-title">
              <el-icon><Rocket /></el-icon>
              <span>标书生成配置</span>
            </div>
            <p class="panel-subtitle">上传招标文件，选择模板和行业，结合企业资料库生成响应文件。</p>
          </div>
          <el-tag type="info" effect="plain">智能体生成入口</el-tag>
        </div>
      </template>

      <el-row :gutter="18">
        <el-col :xs="24" :lg="10">
          <div class="form-label">招标文件 <span>*</span></div>
          <input
            ref="fileInput"
            class="hidden-file-input"
            type="file"
            accept=".pdf,.doc,.docx,.txt"
            @change="onFileChange"
          />
          <button class="bid-upload-box" type="button" @click="chooseFile">
            <div class="bid-file-icons">
              <el-icon><FileText /></el-icon>
              <el-icon><FileText /></el-icon>
            </div>
            <strong>{{ tenderFile ? tenderFile.name : "上传招标文件" }}</strong>
            <span>深度解析项目要求、名称和类型</span>
            <small>支持 doc、docx、pdf、txt，单个文件最大 40MB</small>
            <el-button :icon="Upload" plain @click.stop="chooseFile">选择文件</el-button>
          </button>
          <p v-if="fileName && !tenderFile" class="file-name">当前项目：{{ fileName }}</p>
        </el-col>

        <el-col :xs="24" :md="12" :lg="7">
          <div class="form-label">模板选择 <span>*</span></div>
          <el-select v-model="templateKey" class="field" placeholder="选择响应文件模板">
            <el-option
              v-for="template in templates"
              :key="template.value"
              :label="template.label"
              :value="template.value"
            >
              <div class="select-option">
                <strong>{{ template.label }}</strong>
                <small>{{ template.detail }}</small>
              </div>
            </el-option>
          </el-select>
          <div class="selection-preview">
            <el-icon><FileText /></el-icon>
            <span>{{ templates.find((item) => item.value === templateKey)?.label }}</span>
          </div>
        </el-col>

        <el-col :xs="24" :md="12" :lg="7">
          <div class="form-label">行业选择 <span>*</span></div>
          <el-select v-model="industryKey" class="field" placeholder="选择所属行业">
            <el-option
              v-for="industry in industries"
              :key="industry.value"
              :label="industry.label"
              :value="industry.value"
            />
          </el-select>
          <div class="selection-preview">
            <el-icon><BriefcaseBusiness /></el-icon>
            <span>{{ industries.find((item) => item.value === industryKey)?.label }}</span>
          </div>
        </el-col>
      </el-row>

      <div class="form-label mode-label">标书类型 <span>*</span></div>
      <div class="bid-mode-row">
        <el-radio-group v-model="mode">
          <el-radio-button value="open">明标</el-radio-button>
          <el-radio-button value="secret">暗标</el-radio-button>
        </el-radio-group>
        <span class="form-hint">明标显示公司信息，暗标隐藏公司名称与标识，请按招标文件要求选择。</span>
      </div>

      <div class="form-label knowledge-label">引用企业资料库</div>
      <el-row :gutter="12" align="middle">
        <el-col :xs="24" :md="10">
          <el-select
            :model-value="selectedLibraryKey"
            class="field"
            placeholder="选择企业资料库"
            @update:model-value="emit('update:selected-library-key', $event)"
          >
            <el-option
              v-for="library in libraries"
              :key="library.key"
              :label="library.name"
              :value="library.key"
            />
          </el-select>
        </el-col>
        <el-col :xs="24" :md="14">
          <div v-if="selectedLibrary" class="knowledge-summary">
            <el-icon><Database /></el-icon>
            <span>{{ selectedLibrary.name }}，已收录 {{ selectedLibrary.documentCount }} 份企业材料</span>
          </div>
          <span v-else class="form-hint">暂无企业资料库，将无法自动引用公司资质和案例。</span>
          <div v-if="materialsProfile?.qualifications?.length" class="profile-summary">
            <span class="form-hint">已识别：</span>
            <el-tag
              v-for="q in materialsProfile.qualifications.slice(0, 5)"
              :key="q"
              size="small"
              type="success"
              effect="plain"
            >
              {{ q }}
            </el-tag>
            <span class="form-hint">
              证书 {{ materialsProfile.certificates?.length || 0 }} 份
              <template v-if="materialsProfile.service_regions?.length">
                · 区域 {{ materialsProfile.service_regions.join("、") }}
              </template>
            </span>
          </div>
        </el-col>
      </el-row>

      <div class="generator-actions">
        <span class="form-hint">
          按步骤操作：① 规划章节与偏离表 → ② 生成逐条响应内容 → ③ 校对自查，确认后再进入编辑与导出。
        </span>
        <div class="generator-buttons">
          <el-button
            :icon="ListChecks"
            :disabled="!workspace"
            :loading="planning"
            type="primary"
            @click="planDocument"
          >
            1. 规划标书
          </el-button>
          <el-button
            :icon="Rocket"
            :disabled="!workspace || !bidDocument"
            :loading="generating"
            type="primary"
            @click="generateContent"
          >
            2. 生成内容
          </el-button>
          <el-button
            :icon="ShieldCheck"
            :disabled="!workspace || !bidDocument"
            :loading="proofreading"
            @click="runProofread"
          >
            3. 校对自查
          </el-button>
          <el-button :disabled="!workspace || !bidDocument" plain @click="emit('generate')">
            进入编辑与导出 →
          </el-button>
        </div>
      </div>
    </el-card>

    <template v-if="bidDocument">
      <el-row :gutter="16">
        <el-col :xs="24" :lg="8">
          <el-card class="panel preview-card" shadow="never">
            <template #header>
              <div class="panel-header">
                <div class="result-title">
                  <el-icon><FileText /></el-icon>
                  <span>章节结构</span>
                </div>
                <el-tag size="small" type="info" effect="plain">{{ bidDocument.plan.length }} 章</el-tag>
              </div>
            </template>
            <el-tree
              :data="chapterTree"
              node-key="id"
              :props="{ label: 'label', children: 'children' }"
              default-expand-all
              class="chapter-tree"
            >
              <template #default="{ data }">
                <div class="chapter-node">
                  <span class="chapter-node-label">{{ data.label }}</span>
                  <template v-if="data.section">
                    <el-tag size="small" type="info" effect="plain">{{ data.section.kind || "章节" }}</el-tag>
                    <el-tag size="small" :type="sectionStatusType(data.section.status)" effect="plain">
                      {{ data.section.status || "待生成" }}
                    </el-tag>
                    <span class="chapter-node-source">来源：{{ data.section.source || "—" }}</span>
                  </template>
                </div>
              </template>
            </el-tree>
          </el-card>
        </el-col>

        <el-col :xs="24" :lg="16">
          <el-card class="panel preview-card" shadow="never">
            <template #header>
              <div class="panel-header">
                <div class="result-title">
                  <el-icon><BriefcaseBusiness /></el-icon>
                  <span>偏离表预览</span>
                </div>
              </div>
            </template>
            <div v-for="table in deviationTables" :key="table.key" class="deviation-group">
              <div class="deviation-group-title">{{ table.title }}</div>
              <el-table
                :data="table.rows"
                border
                size="small"
                class="deviation-table"
                :empty-text="`暂无${table.title}`"
              >
                <el-table-column type="index" label="序号" width="60" />
                <el-table-column prop="requirement" label="招标要求" min-width="220" show-overflow-tooltip />
                <el-table-column prop="deviation" label="偏离情况" width="120">
                  <template #default="{ row }">
                    <el-tag size="small" :type="deviationType(row.deviation)" effect="plain">
                      {{ row.deviation || "—" }}
                    </el-tag>
                  </template>
                </el-table-column>
                <el-table-column prop="response" label="响应说明" min-width="180" show-overflow-tooltip />
                <el-table-column prop="evidence" label="证据引用" min-width="140" show-overflow-tooltip />
              </el-table>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card v-if="proofread" class="panel preview-card" shadow="never">
        <template #header>
          <div class="panel-header">
            <div class="result-title">
              <el-icon><Rocket /></el-icon>
              <span>校对报告</span>
            </div>
            <el-tag size="small" :type="proofread.riskItems?.length ? 'danger' : 'success'" effect="plain">
              {{ proofread.riskItems?.length || 0 }} 项风险
            </el-tag>
          </div>
        </template>
        <el-alert
          v-for="(risk, index) in proofread.riskItems"
          :key="index"
          :title="risk.title"
          :description="risk.detail"
          :type="riskAlertType(risk.level)"
          :closable="false"
          show-icon
          class="risk-alert"
        />
        <el-empty v-if="!proofread.riskItems?.length" description="未发现风险项" :image-size="72" />
        <pre v-if="proofread.report" class="proofread-report">{{ proofread.report }}</pre>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.mode-label {
  margin-top: 18px;
}

.bid-mode-row {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}

.generator-buttons {
  display: flex;
  align-items: center;
  gap: 10px;
}

.preview-card {
  min-height: 220px;
}

.chapter-tree {
  background: transparent;
  --el-tree-node-content-height: 30px;
}

.chapter-node {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 6px;
  min-width: 0;
}

.chapter-node-label {
  color: var(--text-primary);
  font-weight: 600;
}

.chapter-node-source {
  color: var(--text-secondary);
  font-size: 12px;
}

.deviation-group + .deviation-group {
  margin-top: 18px;
}

.deviation-group-title {
  margin-bottom: 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 700;
}

.deviation-table {
  width: 100%;
}

.risk-alert {
  margin-bottom: 10px;
}

.proofread-report {
  margin: 12px 0 0;
  padding: 14px 16px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-soft);
  color: var(--text-regular);
  font-family: inherit;
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
