<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import type { Component } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  Archive,
  Award,
  Briefcase,
  Building,
  CheckCircle2,
  Database,
  Edit3,
  FilePlus2,
  FileText,
  FolderOpen,
  Landmark,
  Package,
  RefreshCw,
  ScrollText,
  Search,
  ShieldAlert,
  SlidersHorizontal,
  Trash2,
  Upload,
  Users,
  XCircle
} from "lucide-vue-next";
import { api } from "../../services/api";
import type {
  EvidenceFile,
  MaterialFact,
  OntologyClassDef,
  OntologyMeta,
  OntologySummary
} from "../../shared/types";

interface KnowledgeSection {
  name: string;
  label: string;
  description: string;
  classNames: string[];
  icon: Component;
}

interface TemplateRow {
  id: string;
  name: string;
  type: string;
  mode: "明标" | "暗标";
  scenario: string;
  version: string;
  risk: string;
  status: string;
  updatedAt: string;
}

const meta = ref<OntologyMeta | null>(null);
const summary = ref<OntologySummary | null>(null);
const activeSection = ref("enterprise_credit");
const activeClass = ref("");
const facts = ref<MaterialFact[]>([]);
const selectedFact = ref<MaterialFact | null>(null);
const loading = ref(false);
const importing = ref(false);
const keyword = ref("");
const evidenceFilter = ref("all");

// 证据
const evidenceVisible = ref(false);
const detailVisible = ref(false);
const evidencePath = ref("");
const evidenceFiles = ref<EvidenceFile[]>([]);
const evidenceLoading = ref(false);

// 表单
const formVisible = ref(false);
const formMode = ref<"create" | "edit">("create");
const formFields = ref<Record<string, any>>({});
const editingFact = ref<MaterialFact | null>(null);
const evidenceUploadFiles = ref<File[]>([]);
const evidenceUploadNames = ref("");
const productOptions = ref<MaterialFact[]>([]);
const productOptionsLoading = ref(false);

const KNOWLEDGE_SECTIONS: KnowledgeSection[] = [
  {
    name: "enterprise_credit",
    label: "企业资信库",
    description: "营业执照、资质证书、体系认证、荣誉、财务和信用材料",
    classNames: ["organization", "qualification_certificate", "financial_evidence", "credential"],
    icon: Landmark
  },
  {
    name: "performance",
    label: "项目业绩库",
    description: "中标项目、合同案例、验收报告和类似业绩",
    classNames: ["performance_case"],
    icon: Briefcase
  },
  {
    name: "staff",
    label: "人员资源库",
    description: "项目管理人员、技术人员、证书与简历",
    classNames: ["staff_member"],
    icon: Users
  },
  {
    name: "product_tech",
    label: "产品与技术库",
    description: "产品、服务、技术参数、技术说明和产品证照",
    classNames: ["technical_product", "technical_parameter", "credential"],
    icon: Package
  },
  {
    name: "templates",
    label: "模板资源库",
    description: "明标模板、暗标模板、通用方案和版本管理",
    classNames: [],
    icon: FileText
  },
  {
    name: "archived",
    label: "归档停用",
    description: "已停用、已归档或暂不参与 AI 调用的资料",
    classNames: [],
    icon: Archive
  }
];

const templateRows = ref<TemplateRow[]>([
  {
    id: "tpl-tech-dark",
    name: "技术标通用方案模板",
    type: "技术标书",
    mode: "暗标",
    scenario: "服务方案 / 技术响应",
    version: "v1",
    risk: "待检查",
    status: "接口预留",
    updatedAt: "待接入"
  },
  {
    id: "tpl-business-open",
    name: "商务标基础文件模板",
    type: "商务标书",
    mode: "明标",
    scenario: "投标函 / 授权书 / 声明",
    version: "v1",
    risk: "不适用",
    status: "接口预留",
    updatedAt: "待接入"
  },
  {
    id: "tpl-service-plan",
    name: "售后服务与培训方案模板",
    type: "服务方案",
    mode: "暗标",
    scenario: "售后 / 培训 / 质量保障",
    version: "v1",
    risk: "待检查",
    status: "接口预留",
    updatedAt: "待接入"
  }
]);

const classes = computed(() => meta.value?.classes ?? []);
const activeClassDef = computed<OntologyClassDef | null>(
  () => classes.value.find((c) => c.name === activeClass.value) ?? null
);
const currentSection = computed(() => KNOWLEDGE_SECTIONS.find((s) => s.name === activeSection.value) ?? KNOWLEDGE_SECTIONS[0]);
const counts = computed(() => summary.value?.counts ?? {});
const isOrganization = computed(() => activeClass.value === "organization");
const totalCount = computed(() => Object.values(counts.value).reduce((sum, n) => sum + n, 0));
const isTemplateSection = computed(() => activeSection.value === "templates");
const isArchiveSection = computed(() => activeSection.value === "archived");
const showClassSwitcher = computed(() =>
  ["enterprise_credit", "product_tech"].includes(activeSection.value) && displayedClasses.value.length > 1
);

const displayAttrs = computed(() => (activeClassDef.value?.attrs ?? []).filter((a) => !a.isPk));
const formAttrs = computed(() =>
  (activeClassDef.value?.attrs ?? []).filter((attr) => {
    if (attr.isPk) return false;
    return !["organization_id", "evidence_path", "evidence_updated_at"].includes(attr.name);
  })
);
const TITLE_FIELDS_BY_CLASS: Record<string, string[]> = {
  organization: ["organization_name", "organizationName"],
  qualification_certificate: ["qualification_name", "qualificationName"],
  performance_case: ["case_name", "caseName", "customer_name", "customerName"],
  staff_member: ["name"],
  financial_evidence: ["evidence_type", "evidenceType", "issued_by", "issuedBy"],
  technical_product: ["product_name", "productName"],
  technical_parameter: ["description"],
  credential: ["credential_name", "credentialName"]
};
const activeTitleFields = computed(() => TITLE_FIELDS_BY_CLASS[activeClass.value] ?? []);
const tableAttrs = computed(() =>
  displayAttrs.value
    .filter((attr) => {
      const hidden = ["organization_id", "evidence_path", "evidence_updated_at", "level"];
      return !hidden.includes(attr.name) && !activeTitleFields.value.includes(attr.name);
    })
    .slice(0, 5)
);
const needsProductSelect = computed(() => ["technical_parameter", "credential"].includes(activeClass.value));

const displayedClasses = computed(() => {
  const section = currentSection.value;
  return classes.value.filter((c) => section.classNames.includes(c.name));
});

const filteredFacts = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  return facts.value.filter((row) => {
    const evidencePathValue = getEvidencePath(row);
    if (evidenceFilter.value === "with_evidence" && !evidencePathValue) return false;
    if (evidenceFilter.value === "missing_evidence" && evidencePathValue) return false;
    if (!q) return true;
    return Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(q));
  });
});

const templateFilteredRows = computed(() => {
  const q = keyword.value.trim().toLowerCase();
  if (!q) return templateRows.value;
  return templateRows.value.filter((row) =>
    Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(q))
  );
});

const currentClassEvidenceStats = computed(() => {
  const withEvidence = facts.value.filter((row) => Boolean(getEvidencePath(row))).length;
  return {
    total: facts.value.length,
    withEvidence,
    missingEvidence: Math.max(facts.value.length - withEvidence, 0),
    expiringSoon: facts.value.filter(isExpiringSoon).length
  };
});

const governanceCards = computed(() => {
  if (isTemplateSection.value) {
    return [
      { label: "模板条目", value: templateRows.value.length, hint: "本地模板仓库预留", tone: "info", icon: FileText },
      {
        label: "暗标待检",
        value: templateRows.value.filter((row) => row.risk === "待检查").length,
        hint: "身份信息检查接口预留",
        tone: "warning",
        icon: ShieldAlert
      },
      { label: "版本管理", value: "待接入", hint: "上传、更新、停用", tone: "info", icon: RefreshCw }
    ];
  }
  if (isArchiveSection.value) {
    return [
      { label: "归档条目", value: "待接入", hint: "停用/归档状态预留", tone: "info", icon: Archive },
      { label: "不参与调用", value: "待接入", hint: "默认排除 AI 生成", tone: "warning", icon: ShieldAlert },
      { label: "恢复调用", value: "待接入", hint: "从归档恢复为可用", tone: "info", icon: CheckCircle2 }
    ];
  }
  return [
    { label: "全部资料", value: totalCount.value, hint: `${classes.value.length} 类本体事实`, tone: "info", icon: Database },
    { label: "当前分类可溯源", value: currentClassEvidenceStats.value.withEvidence, hint: "已挂证明材料", tone: "success", icon: CheckCircle2 },
    {
      label: "到期提醒",
      value: currentClassEvidenceStats.value.expiringSoon,
      hint: "90 天内到期",
      tone: currentClassEvidenceStats.value.expiringSoon ? "danger" : "info",
      icon: XCircle
    }
  ];
});

// 类卡片右下角的数字/状态：企业显示「已录入/未录入」，其余显示条数
function cardCount(c: OntologyClassDef) {
  const n = counts.value[c.name] ?? 0;
  if (c.name === "organization") return n > 0 ? "已录入" : "未录入";
  return `${n} 条`;
}

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

function getEvidencePath(fact: MaterialFact) {
  return String(fact.evidence_path ?? fact.evidencePath ?? "");
}

function cellValue(fact: MaterialFact, attr: string) {
  const v = fact[attr];
  if (v === null || v === undefined || v === "") return "—";
  return String(v);
}

function productLabel(row: MaterialFact) {
  const name = String(row.product_name || row.productName || "");
  const id = factId(row);
  return name ? `${name}（${id}）` : id;
}

function firstFactValue(fact: MaterialFact, fields: string[]) {
  for (const field of fields) {
    const value = fact[field];
    if (value !== null && value !== undefined && value !== "") return String(value);
  }
  return "";
}

function factTitle(fact: MaterialFact) {
  const classTitle = firstFactValue(fact, activeTitleFields.value);
  if (classTitle) return classTitle;
  const candidates = [
    "organization_name",
    "organizationName",
    "qualification_name",
    "qualificationName",
    "case_name",
    "caseName",
    "customer_name",
    "customerName",
    "name",
    "product_name",
    "productName",
    "credential_name",
    "credentialName",
    "evidence_type",
    "evidenceType",
    "description"
  ];
  return firstFactValue(fact, candidates) || factId(fact);
}

function factStatus(fact: MaterialFact) {
  const validUntil = String(fact.valid_until || fact.validUntil || "");
  const validUntilTime = validUntil ? Date.parse(validUntil) : NaN;
  if (Number.isFinite(validUntilTime) && validUntilTime < Date.now()) return "已过期";
  if (isExpiringSoon(fact)) return "即将过期";
  const status = firstFactValue(fact, [
    "status",
    "current_status",
    "currentStatus",
    "verification_status",
    "verificationStatus",
    "acceptance_status",
    "acceptanceStatus"
  ]);
  if (status && !/过期|expired/i.test(status)) return status;
  return "可用";
}

function statusTone(status: string) {
  if (/过期|停用|失败|无效/.test(status)) return "danger";
  if (/待|即将|不确定|pending|ongoing/i.test(status)) return "warning";
  if (/已核验|可用|有效|accepted|active/i.test(status)) return "success";
  return "info";
}

function isExpiringSoon(fact: MaterialFact) {
  const raw = String(fact.valid_until || fact.validUntil || "");
  if (!raw) return false;
  const time = Date.parse(raw);
  if (!Number.isFinite(time)) return false;
  const now = Date.now();
  const ninetyDays = 90 * 24 * 60 * 60 * 1000;
  return time >= now && time - now <= ninetyDays;
}

async function loadProductOptions() {
  if (!needsProductSelect.value) return;
  productOptionsLoading.value = true;
  try {
    productOptions.value = await api.getFacts("technical_product", 1000);
  } catch {
    productOptions.value = [];
  } finally {
    productOptionsLoading.value = false;
  }
}

function firstClassInSection() {
  const candidates = displayedClasses.value;
  if (!candidates.length) return "";
  if (activeClass.value && candidates.some((c) => c.name === activeClass.value)) return activeClass.value;
  return candidates[0].name;
}

async function loadMeta() {
  try {
    meta.value = await api.getOntologyMeta();
    summary.value = await api.getOntologySummary();
    if (!activeClass.value && classes.value.length) {
      activeClass.value = firstClassInSection() || classes.value[0].name;
    }
    if (activeClass.value) await loadFacts();
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

async function selectSection(name: string) {
  activeSection.value = name;
  selectedFact.value = null;
  detailVisible.value = false;
  if (name === "templates" || name === "archived") {
    return;
  }
  const nextClass = firstClassInSection();
  if (nextClass) {
    activeClass.value = nextClass;
    await loadFacts();
  }
}

function selectClass(name: string) {
  activeClass.value = name;
  selectedFact.value = null;
  loadFacts();
}

function openFactDetail(fact: MaterialFact) {
  selectedFact.value = fact;
  detailVisible.value = true;
}

function openTemplatePlaceholder() {
  ElMessage.info("模板资源库接口已预留，后续会接入本地模板仓库与暗标检查。");
}

// ---- 证据 ----
async function openEvidence(fact: MaterialFact) {
  const path = getEvidencePath(fact);
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
const FILE_READ_ERROR = "文件读取失败，可能被占用或已移动，请关闭占用程序后重新选择";

async function onImportFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0] || null;
  importFile.value = file;
  importFileName.value = file?.name || "";
  if (file) {
    try {
      await file.slice(0, 1).arrayBuffer();
    } catch {
      ElMessage.error(FILE_READ_ERROR);
      importFile.value = null;
      importFileName.value = "";
      input.value = "";
    }
  }
}

async function doImport() {
  if (!importFile.value) {
    ElMessage.warning("请先选择压缩包文件");
    return;
  }
  importing.value = true;
  try {
    const result = await api.importMaterialsFile(importFile.value);
    const factTotal = Object.values(result.stats)
      .filter((v) => typeof v === "number")
      .reduce((a, b) => a + (b as number), 0);
    if (result.warnings?.length) {
      const detail = result.warnings.join("；");
      ElMessage.warning({ message: `已导入 ${factTotal} 条，${result.warnings.length} 条提示：${detail}`, duration: 10000 });
    } else {
      ElMessage.success(`已导入 ${factTotal} 条资料`);
    }
    importVisible.value = false;
    importFile.value = null;
    importFileName.value = "";
    await loadMeta();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    if (/could not be read|permission|file.*changed|NotReadable/i.test(msg)) {
      ElMessage.error(FILE_READ_ERROR);
    } else {
      ElMessage.error(msg);
    }
  } finally {
    importing.value = false;
  }
}

// ---- 表单 CRUD ----
function openCreate() {
  if (isTemplateSection.value) {
    openTemplatePlaceholder();
    return;
  }
  formMode.value = "create";
  formFields.value = {};
  editingFact.value = null;
  evidenceUploadFiles.value = [];
  evidenceUploadNames.value = "";
  loadProductOptions();
  formVisible.value = true;
}

function openEdit(fact: MaterialFact) {
  formMode.value = "edit";
  editingFact.value = fact;
  formFields.value = {};
  evidenceUploadFiles.value = [];
  evidenceUploadNames.value = "";
  for (const attr of activeClassDef.value?.attrs ?? []) {
    const v = fact[attr.name];
    if (v !== null && v !== undefined) formFields.value[attr.name] = String(v);
  }
  loadProductOptions();
  formVisible.value = true;
}

function onEvidenceUploadChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  evidenceUploadFiles.value = files;
  evidenceUploadNames.value = files.map((file) => file.name).join("、");
}

async function submitForm() {
  const cls = activeClass.value;
  if (!cls) return;
  try {
    if (formMode.value === "create") {
      if (cls === "organization") {
        await api.createFact(cls, { ...formFields.value });
        ElMessage.success("已新增");
      } else {
        const result = await api.createFactWithEvidence(cls, { ...formFields.value }, evidenceUploadFiles.value);
        if (result.warnings?.length) {
          ElMessage.warning({ message: `已新增，但关系校验有提示：${result.warnings.join("；")}`, duration: 9000 });
        } else {
          ElMessage.success(`已新增${result.relations?.length ? `，已建立 ${result.relations.length} 条关系` : ""}`);
        }
      }
    } else if (editingFact.value) {
      await api.updateFact(cls, factId(editingFact.value), { ...formFields.value });
      ElMessage.success("已更新");
    }
    formVisible.value = false;
    evidenceUploadFiles.value = [];
    evidenceUploadNames.value = "";
    await loadFacts();
    await refreshSummary();
  } catch (e) {
    ElMessage.error((e as Error).message);
  }
}

async function removeFact(fact: MaterialFact) {
  try {
    await ElMessageBox.confirm("确认删除这条资料？其证明材料文件将一并删除，且不可恢复。", "删除", { type: "warning" });
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
    <div class="knowledge-hero">
      <div class="knowledge-hero-main">
        <p class="section-eyebrow">Enterprise Private Knowledge Base</p>
        <h2 class="materials-title">企业私有知识库</h2>
      </div>
      <div class="knowledge-hero-actions">
        <el-input
          v-model="keyword"
          class="knowledge-search"
          clearable
          :prefix-icon="Search"
          placeholder="搜索资料名称、证书编号、项目名、人员、产品参数"
        />
        <el-button :icon="RefreshCw" :loading="loading" @click="loadMeta">刷新</el-button>
        <el-button :icon="Upload" type="primary" @click="importVisible = true">批量导入资料包</el-button>
      </div>
    </div>

    <section class="governance-grid">
      <article
        v-for="card in governanceCards"
        :key="card.label"
        class="governance-card"
        :data-tone="card.tone"
      >
        <span class="governance-card-icon">
          <component :is="card.icon" :size="18" />
        </span>
        <span class="governance-card-text">
          <span>{{ card.label }}</span>
          <strong>{{ card.value }}</strong>
          <small>{{ card.hint }}</small>
        </span>
      </article>
    </section>

    <nav class="knowledge-tabs" aria-label="企业资料分类">
      <button
        v-for="section in KNOWLEDGE_SECTIONS"
        :key="section.name"
        type="button"
        class="knowledge-tab"
        :class="{ active: section.name === activeSection }"
        @click="selectSection(section.name)"
      >
        <component :is="section.icon" :size="16" />
        <span>{{ section.label }}</span>
      </button>
    </nav>

    <template v-if="isTemplateSection">
      <section class="template-panel">
        <div class="template-toolbar">
          <div>
            <h3>模板资源库</h3>
            <p>明标、暗标和通用方案模板先以本地仓库方式预留，后续接入上传、版本和暗标检查接口。</p>
          </div>
          <el-button :icon="FilePlus2" type="primary" @click="openTemplatePlaceholder">新增模板</el-button>
        </div>
        <el-table :data="templateFilteredRows" stripe class="template-table">
          <el-table-column label="模板名称" min-width="220">
            <template #default="{ row }">
              <div class="template-name-cell">
                <FileText :size="17" />
                <div>
                  <strong>{{ row.name }}</strong>
                  <small>{{ row.scenario }}</small>
                </div>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="type" label="类型" width="120" />
          <el-table-column label="模式" width="90">
            <template #default="{ row }">
              <el-tag :type="row.mode === '暗标' ? 'warning' : 'info'" effect="plain">{{ row.mode }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="version" label="版本" width="80" />
          <el-table-column label="暗标风险" width="130">
            <template #default="{ row }">
              <el-tag :type="row.risk === '不适用' ? 'info' : 'warning'" effect="plain">{{ row.risk }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="status" label="状态" width="110" />
          <el-table-column prop="updatedAt" label="最近更新" width="110" />
          <el-table-column label="操作" width="140" fixed="right">
            <template #default>
              <el-button link type="primary" @click="openTemplatePlaceholder">管理</el-button>
              <el-button link type="primary" @click="openTemplatePlaceholder">检查</el-button>
            </template>
          </el-table-column>
        </el-table>
      </section>
    </template>

    <template v-else-if="isArchiveSection">
      <section class="archive-empty">
        <Archive :size="34" />
        <strong>归档停用能力待接入</strong>
        <span>后续会把删除操作升级为停用/归档，已归档资料默认不参与 AI 生成调用。</span>
      </section>
    </template>

    <template v-else>
      <div class="knowledge-layout">
        <aside v-if="showClassSwitcher" class="knowledge-directory">
          <button
            v-for="c in displayedClasses"
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
        </aside>

        <main class="materials-main">
          <el-card class="panel materials-panel" shadow="never">
            <template #header>
              <div class="panel-header">
                <div class="panel-header-title">
                  <span>{{ activeClassDef?.cnName || "资料" }}</span>
                  <el-tag v-if="!isOrganization" effect="plain" type="info">{{ counts[activeClass] ?? 0 }} 条</el-tag>
                  <span v-if="activeClassDef?.description" class="form-hint">{{ activeClassDef.description }}</span>
                </div>
                <div class="materials-panel-actions">
                  <el-select v-model="evidenceFilter" class="evidence-filter" size="small">
                    <el-option label="全部证据状态" value="all" />
                    <el-option label="已有证明材料" value="with_evidence" />
                    <el-option label="缺少证明材料" value="missing_evidence" />
                  </el-select>
                  <el-button v-if="!isOrganization" :icon="FilePlus2" type="primary" @click="openCreate">
                    新增{{ activeClassDef?.cnName || "资料" }}
                  </el-button>
                </div>
              </div>
            </template>

            <div v-if="isOrganization">
              <el-form v-if="facts.length" label-width="140px">
                <el-form-item v-for="attr in displayAttrs" :key="attr.name" :label="attr.cn">
                  <el-input :model-value="cellValue(facts[0], attr.name)" readonly />
                </el-form-item>
                <el-button type="primary" @click="openEdit(facts[0])">编辑企业信息</el-button>
              </el-form>
              <el-empty v-else description="暂未填写企业信息" />
            </div>

            <el-table v-else v-loading="loading" :data="filteredFacts" stripe @row-click="openFactDetail">
              <el-table-column label="资料名称" min-width="220" fixed>
                <template #default="{ row }">
                  <button class="fact-name-button" type="button" @click.stop="openFactDetail(row)">
                    <strong>{{ factTitle(row) }}</strong>
                    <small>{{ factId(row) }}</small>
                  </button>
                </template>
              </el-table-column>
              <el-table-column
                v-for="attr in tableAttrs"
                :key="attr.name"
                :label="attr.cn"
                :min-width="attr.name === 'description' ? 220 : 140"
                show-overflow-tooltip
              >
                <template #default="{ row }">{{ cellValue(row, attr.name) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="110">
                <template #default="{ row }">
                  <span class="status-pill" :data-tone="statusTone(factStatus(row))">
                    {{ factStatus(row) }}
                  </span>
                </template>
              </el-table-column>
              <el-table-column label="证据" width="120">
                <template #default="{ row }">
                  <el-button
                    v-if="getEvidencePath(row)"
                    :icon="FolderOpen"
                    link
                    type="primary"
                    @click.stop="openEvidence(row)"
                  >
                    查看
                  </el-button>
                  <span v-else class="evidence-missing">缺证据</span>
                </template>
              </el-table-column>
              <el-table-column label="操作" width="150" fixed="right">
                <template #default="{ row }">
                  <el-button :icon="Edit3" link type="primary" @click.stop="openEdit(row)">编辑</el-button>
                  <el-button :icon="Trash2" link type="danger" @click.stop="removeFact(row)">删除</el-button>
                </template>
              </el-table-column>
            </el-table>
          </el-card>
        </main>
      </div>
    </template>

    <el-drawer v-model="detailVisible" title="资料详情" size="560px">
      <template v-if="selectedFact">
        <div class="detail-head">
          <strong>{{ factTitle(selectedFact) }}</strong>
          <span>{{ factId(selectedFact) }}</span>
        </div>
        <div class="detail-actions">
          <el-button :icon="Edit3" type="primary" @click="openEdit(selectedFact)">编辑资料</el-button>
          <el-button :icon="FolderOpen" @click="openEvidence(selectedFact)">查看证据</el-button>
        </div>
        <el-descriptions :column="1" border class="detail-descriptions">
          <el-descriptions-item
            v-for="attr in displayAttrs"
            :key="attr.name"
            :label="attr.cn"
          >
            {{ cellValue(selectedFact, attr.name) }}
          </el-descriptions-item>
        </el-descriptions>
        <div class="ai-rule-box">
          <ShieldAlert :size="17" />
          <span>AI 调用约束：事实型内容必须引用本条资料及其证明材料；缺证据时只可标记为待补充。</span>
        </div>
      </template>
    </el-drawer>

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

    <el-dialog
      v-model="formVisible"
      :title="formMode === 'create' ? `新增${activeClassDef?.cnName || ''}` : `编辑${activeClassDef?.cnName || ''}`"
      width="620px"
      destroy-on-close
    >
      <el-form label-width="140px">
        <el-form-item
          v-for="attr in formAttrs"
          :key="attr.name"
          :label="attr.cn"
        >
          <el-select
            v-if="attr.name === 'product_id' && needsProductSelect"
            v-model="formFields[attr.name]"
            :loading="productOptionsLoading"
            filterable
            clearable
            placeholder="选择已录入的技术产品"
            class="full-width"
          >
            <el-option
              v-for="product in productOptions"
              :key="factId(product)"
              :label="productLabel(product)"
              :value="factId(product)"
            />
          </el-select>
          <el-date-picker
            v-else-if="attr.type === 'date'"
            v-model="formFields[attr.name]"
            type="date"
            value-format="YYYY-MM-DD"
            placeholder="选择日期"
            class="full-width"
          />
          <el-input-number
            v-else-if="attr.type === 'int' || attr.type === 'double'"
            v-model="formFields[attr.name]"
            :precision="attr.type === 'int' ? 0 : 2"
            class="full-width"
          />
          <el-input
            v-else-if="attr.type === 'text'"
            v-model="formFields[attr.name]"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
          />
          <el-input v-else v-model="formFields[attr.name]" />
        </el-form-item>
        <el-form-item v-if="formMode === 'create' && !isOrganization" label="证明文件">
          <label class="dialog-upload evidence-upload-box">
            <input class="hidden-file-input" type="file" multiple @change="onEvidenceUploadChange" />
            <el-icon :size="22"><Upload /></el-icon>
            <span class="evidence-upload-copy">
              <strong>{{ evidenceUploadNames || "选择这条资料的证明文件" }}</strong>
              <small>保存后写入 runtime/libraries/company/证明材料/{{ activeClassDef?.cnName }}/新序号/</small>
            </span>
          </label>
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

.knowledge-hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 18px;
  margin-bottom: 16px;
}

.knowledge-hero-main {
  min-width: 0;
}

.materials-title {
  margin: 0;
  color: var(--text-primary);
  font-size: 22px;
  font-weight: 700;
}

.materials-subtitle {
  max-width: 780px;
  margin: 6px 0 0;
  color: var(--text-secondary);
  font-size: 13px;
}

.knowledge-hero-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 10px;
}

.knowledge-search {
  width: min(420px, 42vw);
}

.governance-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.governance-card {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
  min-height: 66px;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-card);
  box-shadow: var(--shadow-xs);
}

.governance-card[data-tone="success"] {
  border-color: rgba(var(--success-rgb), 0.28);
  background: rgba(var(--success-rgb), 0.06);
}

.governance-card[data-tone="warning"] {
  border-color: rgba(var(--warning-rgb), 0.3);
  background: rgba(var(--warning-rgb), 0.06);
}

.governance-card[data-tone="danger"] {
  border-color: rgba(var(--danger-rgb), 0.3);
  background: rgba(var(--danger-rgb), 0.06);
}

.governance-card-icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  flex: 0 0 auto;
  border-radius: 10px;
  background: rgba(var(--primary-rgb), 0.09);
  color: var(--primary);
}

.governance-card[data-tone="success"] .governance-card-icon {
  background: rgba(var(--success-rgb), 0.12);
  color: var(--success);
}

.governance-card[data-tone="warning"] .governance-card-icon {
  background: rgba(var(--warning-rgb), 0.14);
  color: var(--warning);
}

.governance-card[data-tone="danger"] .governance-card-icon {
  background: rgba(var(--danger-rgb), 0.12);
  color: var(--danger);
}

.governance-card-text {
  min-width: 0;
}

.governance-card-text span,
.governance-card-text small,
.governance-card-text strong {
  display: block;
}

.governance-card-text span {
  color: var(--text-secondary);
  font-size: 11px;
}

.governance-card-text strong {
  margin-top: 1px;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 18px;
  line-height: 1.1;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.governance-card-text small {
  margin-top: 2px;
  color: var(--text-secondary);
  font-size: 11px;
}

.knowledge-tabs {
  display: grid;
  grid-template-columns: repeat(6, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 10px;
  padding: 6px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-card);
}

.knowledge-tab {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  min-height: 34px;
  padding: 7px 12px;
  border: 1px solid transparent;
  border-radius: 999px;
  background: transparent;
  color: var(--text-regular);
  cursor: pointer;
  font: inherit;
  font-size: 13px;
  text-align: center;
}

.knowledge-tab:hover,
.knowledge-tab.active {
  background: rgba(var(--primary-rgb), 0.08);
  color: var(--primary);
}

.knowledge-tab.active {
  background: var(--primary);
  color: #fff;
}

.knowledge-layout {
  display: grid;
  gap: 14px;
}

.knowledge-directory {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-card);
  box-shadow: var(--shadow-xs);
}

.class-card {
  display: flex;
  align-items: center;
  min-width: 0;
  gap: 12px;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 10px;
  background: var(--bg-card);
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: border-color 0.2s ease, background 0.2s ease, box-shadow 0.2s ease;
}

.class-card:hover,
.class-card.active {
  border-color: rgba(var(--primary-rgb), 0.45);
  background: rgba(var(--primary-rgb), 0.045);
}

.class-card.active {
  box-shadow: inset 3px 0 0 var(--primary);
}

.class-card-icon {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  border-radius: 9px;
  background: rgba(var(--primary-rgb), 0.1);
  color: var(--primary);
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

.materials-main {
  min-width: 0;
}

.materials-panel {
  min-height: 520px;
}

.panel-header-title {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

.panel-header-title .form-hint {
  margin-left: 4px;
}

.materials-panel-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.evidence-filter {
  width: 150px;
}

.fact-name-button {
  display: grid;
  gap: 3px;
  max-width: 100%;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  font: inherit;
  text-align: left;
}

.fact-name-button strong {
  overflow: hidden;
  color: var(--primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.fact-name-button small {
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  min-height: 24px;
  padding: 3px 8px;
  border-radius: 8px;
  background: var(--el-color-info-light-9);
  color: var(--el-color-info);
  font-size: 12px;
}

.status-pill[data-tone="success"] {
  background: var(--el-color-success-light-9);
  color: var(--el-color-success);
}

.status-pill[data-tone="warning"] {
  background: var(--el-color-warning-light-9);
  color: var(--el-color-warning);
}

.status-pill[data-tone="danger"] {
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
}

.evidence-missing {
  color: var(--warning);
  font-size: 12px;
}

.template-panel,
.archive-empty {
  border: 1px solid var(--border-color);
  border-radius: 12px;
  background: var(--bg-card);
  box-shadow: var(--shadow-xs);
}

.template-panel {
  padding: 16px;
}

.template-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  margin-bottom: 14px;
}

.template-toolbar h3 {
  margin: 0;
  color: var(--text-primary);
  font-size: 17px;
}

.template-toolbar p {
  margin: 4px 0 0;
  color: var(--text-secondary);
  font-size: 12px;
}

.template-name-cell {
  display: flex;
  align-items: center;
  gap: 10px;
}

.template-name-cell svg {
  flex: 0 0 auto;
  color: var(--primary);
}

.template-name-cell strong,
.template-name-cell small {
  display: block;
}

.template-name-cell strong {
  color: var(--text-primary);
}

.template-name-cell small {
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 11px;
}

.archive-empty {
  display: grid;
  justify-items: center;
  gap: 8px;
  padding: 64px 18px;
  color: var(--text-secondary);
  text-align: center;
}

.archive-empty svg {
  color: var(--primary);
}

.archive-empty strong {
  color: var(--text-primary);
}

.detail-head {
  display: grid;
  gap: 4px;
  margin-bottom: 12px;
}

.detail-head strong {
  color: var(--text-primary);
  font-size: 18px;
}

.detail-head span {
  color: var(--text-secondary);
  font-size: 12px;
}

.detail-actions {
  display: flex;
  gap: 10px;
  margin-bottom: 16px;
}

.detail-descriptions {
  margin-bottom: 16px;
}

.ai-rule-box {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 12px;
  border: 1px solid rgba(var(--warning-rgb), 0.32);
  border-radius: 10px;
  background: rgba(var(--warning-rgb), 0.07);
  color: var(--text-regular);
  font-size: 12px;
  line-height: 1.6;
}

.ai-rule-box svg {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--warning);
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
  border-color: rgba(var(--primary-rgb), 0.45);
  color: var(--primary);
}

.evidence-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evidence-upload-box {
  width: 100%;
  min-height: 72px;
  align-items: center;
  justify-content: flex-start;
  flex-direction: row;
  gap: 12px;
  margin-bottom: 0;
  padding: 12px 14px;
  text-align: left;
}

.evidence-upload-box .el-icon {
  flex: 0 0 auto;
}

.evidence-upload-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.evidence-upload-copy strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.evidence-upload-copy small {
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (max-width: 1280px) {
  .governance-grid {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
}

@media (max-width: 980px) {
  .knowledge-hero {
    flex-direction: column;
  }

  .knowledge-hero-actions {
    width: 100%;
    justify-content: flex-start;
  }

  .knowledge-search {
    width: 100%;
  }

  .class-card {
    max-width: none;
  }

  .knowledge-tabs {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 768px) {
  .governance-grid {
    grid-template-columns: 1fr;
  }

  .knowledge-directory {
    display: grid;
    grid-template-columns: 1fr;
  }

  .template-toolbar,
  .detail-actions,
  .materials-panel-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .evidence-filter {
    width: 100%;
  }

  .knowledge-tabs {
    grid-template-columns: 1fr;
  }
}
</style>
