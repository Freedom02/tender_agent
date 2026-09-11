<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import type { Component } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  Award,
  Briefcase,
  Building,
  Edit3,
  FilePlus2,
  FolderOpen,
  Landmark,
  Package,
  RefreshCw,
  ScrollText,
  SlidersHorizontal,
  Trash2,
  Upload,
  Users
} from "lucide-vue-next";
import { api } from "../../services/api";
import { fileToBase64 } from "../../shared/file-utils";
import type {
  EvidenceFile,
  MaterialFact,
  OntologyClassDef,
  OntologyMeta,
  OntologySummary
} from "../../shared/types";

const meta = ref<OntologyMeta | null>(null);
const summary = ref<OntologySummary | null>(null);
const activeClass = ref("");
const facts = ref<MaterialFact[]>([]);
const loading = ref(false);
const importing = ref(false);

// 证据
const evidenceVisible = ref(false);
const evidencePath = ref("");
const evidenceFiles = ref<EvidenceFile[]>([]);
const evidenceLoading = ref(false);

// 表单
const formVisible = ref(false);
const formMode = ref<"create" | "edit">("create");
const formFields = ref<Record<string, string>>({});
const editingFact = ref<MaterialFact | null>(null);

const classes = computed(() => meta.value?.classes ?? []);
const activeClassDef = computed<OntologyClassDef | null>(
  () => classes.value.find((c) => c.name === activeClass.value) ?? null
);
const counts = computed(() => summary.value?.counts ?? {});
const isOrganization = computed(() => activeClass.value === "organization");
const totalCount = computed(() => Object.values(counts.value).reduce((sum, n) => sum + n, 0));

const displayAttrs = computed(() => (activeClassDef.value?.attrs ?? []).filter((a) => !a.isPk));

// 类卡片右下角的数字/状态：企业显示「已录入/未录入」，其余显示条数
function cardCount(c: OntologyClassDef) {
  const n = counts.value[c.name] ?? 0;
  if (c.name === "organization") return n > 0 ? "已录入" : "未录入";
  return `${n} 条`;
}

// 类卡片图标（按类名映射）
const CLASS_ICONS: Record<string, Component> = {
  organization: Building,
  qualification_certificate: Award,
  performance_case: Briefcase,
  staff_member: Users,
  financial_evidence: Landmark,
  technical_product: Package,
  technical_parameter: SlidersHorizontal,
  credential: ScrollText
};

function classIcon(name: string) {
  return CLASS_ICONS[name] ?? Building;
}

function factId(fact: MaterialFact) {
  return String(fact.id || "").split(":").pop() || String(fact.id || "");
}

function cellValue(fact: MaterialFact, attr: string) {
  const v = fact[attr];
  if (v === null || v === undefined || v === "") return "—";
  return String(v);
}

async function loadMeta() {
  try {
    meta.value = await api.getOntologyMeta();
    summary.value = await api.getOntologySummary();
    if (!activeClass.value && classes.value.length) {
      activeClass.value = classes.value[0].name;
      await loadFacts();
    }
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

async function refreshSummary() {
  try {
    summary.value = await api.getOntologySummary();
  } catch {
    /* 静默 */
  }
}

async function loadFacts() {
  if (!activeClass.value) return;
  loading.value = true;
  try {
    facts.value = await api.getFacts(activeClass.value, 100);
  } catch (e) {
    ElMessage.error((e as Error).message);
    facts.value = [];
  } finally {
    loading.value = false;
  }
}

function selectClass(name: string) {
  activeClass.value = name;
  loadFacts();
}

// ---- 证据 ----
async function openEvidence(fact: MaterialFact) {
  const path = String(fact.evidence_path ?? fact.evidencePath ?? "");
  if (!path) {
    ElMessage.info("这条资料暂无证明材料");
    return;
  }
  evidencePath.value = path;
  evidenceVisible.value = true;
  evidenceLoading.value = true;
  try {
    evidenceFiles.value = (await api.getEvidenceFiles(path)).files;
  } catch {
    evidenceFiles.value = [];
  } finally {
    evidenceLoading.value = false;
  }
}

// ---- 导入 ----
const importVisible = ref(false);
const importFile = ref<File | null>(null);
const importFileName = ref("");

function onImportFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null;
  importFile.value = file;
  importFileName.value = file?.name || "";
}

async function doImport() {
  if (!importFile.value) {
    ElMessage.warning("请先选择压缩包文件");
    return;
  }
  importing.value = true;
  try {
    const fileBase64 = await fileToBase64(importFile.value);
    const result = await api.importMaterials({ fileBase64, fileName: importFile.value.name });
    const factTotal = Object.values(result.stats)
      .filter((v) => typeof v === "number")
      .reduce((a, b) => a + (b as number), 0);
    ElMessage.success(`已导入 ${factTotal} 条资料`);
    if (result.warnings?.length) {
      ElMessage.warning(`有 ${result.warnings.length} 条提示，部分内容可能未识别完整，请检查`);
    }
    importVisible.value = false;
    importFile.value = null;
    importFileName.value = "";
    await loadMeta();
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    importing.value = false;
  }
}

// ---- 表单 CRUD ----
function openCreate() {
  formMode.value = "create";
  formFields.value = {};
  editingFact.value = null;
  formVisible.value = true;
}

function openEdit(fact: MaterialFact) {
  formMode.value = "edit";
  editingFact.value = fact;
  formFields.value = {};
  for (const attr of activeClassDef.value?.attrs ?? []) {
    const v = fact[attr.name];
    if (v !== null && v !== undefined) formFields.value[attr.name] = String(v);
  }
  formVisible.value = true;
}

async function submitForm() {
  const cls = activeClass.value;
  if (!cls) return;
  try {
    if (formMode.value === "create") {
      await api.createFact(cls, { ...formFields.value });
      ElMessage.success("已新增");
    } else if (editingFact.value) {
      await api.updateFact(cls, factId(editingFact.value), { ...formFields.value });
      ElMessage.success("已更新");
    }
    formVisible.value = false;
    await loadFacts();
    await refreshSummary();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

async function removeFact(fact: MaterialFact) {
  try {
    await ElMessageBox.confirm("确认删除这条资料？删除后不可恢复。", "删除", { type: "warning" });
    await api.deleteFact(activeClass.value, factId(fact));
    ElMessage.success("已删除");
    await loadFacts();
    await refreshSummary();
  } catch (e) {
    if ((e as Error).message !== "cancel") ElMessage.error((e as Error).message);
  }
}

onMounted(loadMeta);
</script>

<template>
  <div class="materials-page">
    <!-- 头部：标题 + 摘要 + 操作 -->
    <div class="materials-header">
      <div class="materials-header-text">
        <h2 class="materials-title">企业资料库</h2>
        <p class="materials-subtitle">{{ classes.length }} 类资料 · 共 {{ totalCount }} 条</p>
      </div>
      <div class="inline-actions">
        <el-button :icon="RefreshCw" :loading="loading" @click="loadMeta">刷新</el-button>
        <el-button :icon="Upload" type="primary" @click="importVisible = true">导入企业资料</el-button>
      </div>
    </div>

    <!-- 类卡片：横向排列 -->
    <div class="class-cards">
      <button
        v-for="c in classes"
        :key="c.name"
        type="button"
        class="class-card"
        :class="{ active: c.name === activeClass }"
        @click="selectClass(c.name)"
      >
        <span class="class-card-icon">
          <component :is="classIcon(c.name)" :size="18" />
        </span>
        <span class="class-card-text">
          <span class="class-card-name">{{ c.cnName }}</span>
          <span class="class-card-count">{{ cardCount(c) }}</span>
        </span>
      </button>
    </div>

    <!-- 数据区（全宽） -->
    <section class="materials-main">
      <el-card class="panel" shadow="never">
        <template #header>
          <div class="panel-header">
            <div class="panel-header-title">
              <span>{{ activeClassDef?.cnName || "资料" }}</span>
              <el-tag v-if="!isOrganization" effect="plain" type="info">{{ counts[activeClass] ?? 0 }} 条</el-tag>
              <span v-if="activeClassDef?.description" class="form-hint">{{ activeClassDef.description }}</span>
            </div>
            <el-button v-if="!isOrganization" :icon="FilePlus2" type="primary" @click="openCreate">
              新增{{ activeClassDef?.cnName || "资料" }}
            </el-button>
          </div>
        </template>

        <!-- 企业（单对象） -->
        <div v-if="isOrganization">
          <el-form v-if="facts.length" label-width="140px">
            <el-form-item v-for="attr in displayAttrs" :key="attr.name" :label="attr.cn">
              <el-input :model-value="cellValue(facts[0], attr.name)" readonly />
            </el-form-item>
            <el-button type="primary" @click="openEdit(facts[0])">编辑企业信息</el-button>
          </el-form>
          <el-empty v-else description="暂未填写企业信息" />
        </div>

        <!-- 资料表（7 类） -->
        <el-table v-else v-loading="loading" :data="facts" stripe>
          <el-table-column label="编号" width="190">
            <template #default="{ row }">
              <code class="materials-id">{{ factId(row) }}</code>
            </template>
          </el-table-column>
          <el-table-column
            v-for="attr in displayAttrs"
            :key="attr.name"
            :label="attr.cn"
            :min-width="attr.name === 'description' ? 220 : 140"
            show-overflow-tooltip
          >
            <template #default="{ row }">{{ cellValue(row, attr.name) }}</template>
          </el-table-column>
          <el-table-column label="证据" width="90" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="row.evidence_path || row.evidencePath"
                :icon="FolderOpen"
                link
                type="primary"
                @click="openEvidence(row)"
              >
                查看
              </el-button>
              <span v-else class="form-hint">—</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="130" fixed="right">
            <template #default="{ row }">
              <el-button :icon="Edit3" link type="primary" @click="openEdit(row)">编辑</el-button>
              <el-button :icon="Trash2" link type="danger" @click="removeFact(row)">删除</el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </section>

    <!-- 证据抽屉 -->
    <el-drawer v-model="evidenceVisible" title="证明材料" size="520px">
      <p class="form-hint evidence-path">{{ evidencePath }}</p>
      <el-skeleton v-if="evidenceLoading" :rows="6" animated />
      <el-empty v-else-if="!evidenceFiles.length" description="该文件夹暂无文件" />
      <div v-else class="evidence-list">
        <a v-for="f in evidenceFiles" :key="f.name" :href="f.url" target="_blank" class="evidence-item">
          <el-icon :size="16"><FolderOpen /></el-icon>
          <span class="evidence-name">{{ f.name }}</span>
          <span class="form-hint">{{ Math.ceil(f.size / 1024) }} KB</span>
        </a>
      </div>
    </el-drawer>

    <!-- 导入对话框 -->
    <el-dialog v-model="importVisible" title="导入企业资料" width="540px" destroy-on-close>
      <p class="form-hint">
        上传「企业资料收集表」压缩包（Excel 表格 + 证明材料文件夹），导入后自动保存到企业资料库。
      </p>
      <label class="dialog-upload">
        <input class="hidden-file-input" type="file" accept=".zip" @change="onImportFileChange" />
        <el-icon :size="24"><Upload /></el-icon>
        <span>{{ importFileName || "选择压缩包文件" }}</span>
        <small>压缩包内：Excel 表格 + 证明材料/{表名}/{序号}/{文件名}</small>
      </label>
      <template #footer>
        <el-button @click="importVisible = false">取消</el-button>
        <el-button :icon="Upload" :loading="importing" type="primary" @click="doImport">开始导入</el-button>
      </template>
    </el-dialog>

    <!-- 表单对话框 -->
    <el-dialog
      v-model="formVisible"
      :title="formMode === 'create' ? `新增${activeClassDef?.cnName || ''}` : `编辑${activeClassDef?.cnName || ''}`"
      width="620px"
      destroy-on-close
    >
      <el-form label-width="140px">
        <el-form-item
          v-for="attr in activeClassDef?.attrs || []"
          :key="attr.name"
          :label="attr.cn"
          :required="attr.isPk && formMode === 'create'"
        >
          <el-input
            v-model="formFields[attr.name]"
            :disabled="attr.isPk && formMode === 'edit'"
            :placeholder="attr.isPk && formMode === 'create' ? '主键（必填，如 cert-0001）' : ''"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="formVisible = false">取消</el-button>
        <el-button type="primary" @click="submitForm">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.materials-page {
  min-width: 0;
}
.materials-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 16px;
}
.materials-title {
  margin: 0;
  color: var(--text-primary);
  font-size: 20px;
  font-weight: 700;
}
.materials-subtitle {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
}

/* 类卡片：横向排列，超出自动换行 */
.class-cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 18px;
}
.class-card {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-card);
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease, transform 0.2s ease;
}
.class-card:hover {
  border-color: rgba(91, 63, 255, 0.45);
  box-shadow: 0 8px 20px rgba(112, 144, 176, 0.08);
  transform: translateY(-1px);
}
.class-card.active {
  border-color: rgba(91, 63, 255, 0.55);
  background: rgba(91, 63, 255, 0.045);
  box-shadow: 0 8px 20px rgba(112, 144, 176, 0.1);
}
.class-card-icon {
  display: grid;
  place-items: center;
  width: 36px;
  height: 36px;
  flex: 0 0 auto;
  border-radius: 9px;
  background: rgba(91, 63, 255, 0.1);
  color: var(--primary);
  transition: background 0.2s ease, color 0.2s ease;
}
.class-card.active .class-card-icon {
  background: var(--primary);
  color: #fff;
}
.class-card-text {
  min-width: 0;
  flex: 1;
}
.class-card-name {
  display: block;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.class-card-count {
  display: block;
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 12px;
}
.class-card.active .class-card-count {
  color: var(--primary);
}

.materials-main {
  min-width: 0;
}
.panel-header-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.panel-header-title .form-hint {
  margin-left: 4px;
}
.materials-id {
  color: var(--text-secondary);
  font-size: 12px;
}
.evidence-path {
  margin: 0 0 12px;
  word-break: break-all;
}
.evidence-list {
  display: grid;
  gap: 8px;
}
.evidence-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-soft);
  color: var(--text-primary);
  text-decoration: none;
  font-size: 13px;
}
.evidence-item:hover {
  border-color: rgba(91, 63, 255, 0.45);
  color: var(--primary);
}
.evidence-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 768px) {
  .materials-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .class-cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
