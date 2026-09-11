<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { Download, FileCheck2, FileText, Sparkles, Upload } from "lucide-vue-next";
import type { LibrarySummary, MatchSummary, MaterialProfile, ReasoningRow, Workspace } from "../../shared/types";

const props = defineProps<{
  workspace: Workspace | null;
  libraries: LibrarySummary[];
  busy: boolean;
  importFile: File | null;
  importForm: {
    name: string;
    text: string;
  };
  inputKind: "notice" | "tender_document";
  selectedLibraryKey: string;
  materialsProfile: MaterialProfile | null;
}>();

const emit = defineEmits<{
  evaluate: [];
  fileSelected: [file: File | null];
  "update:input-kind": [kind: "notice" | "tender_document"];
  "update:selected-library-key": [key: string];
  "update-fine-status": [payload: { rowId: string; status: string }];
  openWorkspace: [];
}>();

const fileInput = ref<HTMLInputElement | null>(null);
const fineStatusFilter = ref("全部状态");
const fineStatusValues = ["满足", "部分满足", "不满足", "待补材料", "待澄清", "不适用"];
const fileName = computed(() => props.importFile?.name || "");
const auditRecords = computed(() => props.workspace?.auditRecords || []);
const latestAuditRecord = computed(() => auditRecords.value[auditRecords.value.length - 1] || null);
const selectedLibrary = computed(
  () => props.libraries.find((library) => library.key === props.selectedLibraryKey) || props.libraries[0] || null
);
const fineStatusOptions = computed(() => {
  const statusCounts = props.workspace?.reasoning.fineReview?.statusCounts || {};
  return ["全部状态", ...Object.keys(statusCounts)];
});
const filteredFineRows = computed(() => {
  const rows = props.workspace?.reasoning.fineReview?.rows || [];
  if (fineStatusFilter.value === "全部状态") return rows;
  return rows.filter((row) => row.status === fineStatusFilter.value);
});

const REQUIREMENT_MODULE_ORDER = ["资格审查要求", "商务和服务要求", "技术要求"];
const MODULE_NUMBERS: Record<string, string> = {
  资格审查要求: "②",
  商务和服务要求: "③",
  技术要求: "④",
};
const moduleGroups = computed(() => {
  const rows = filteredFineRows.value.filter(
    (row) => row.module && REQUIREMENT_MODULE_ORDER.includes(row.module)
  );
  return REQUIREMENT_MODULE_ORDER.map((module) => ({
    module,
    rows: rows.filter((row) => row.module === module),
  })).filter((group) => group.rows.length > 0);
});

const activeModule = ref("资格审查要求");
const activeModuleGroup = computed(() => {
  return (
    moduleGroups.value.find((group) => group.module === activeModule.value) ||
    moduleGroups.value[0] ||
    null
  );
});
const announcementSummary = computed(() => props.workspace?.reasoning.summary || null);
const fineSummary = computed(() => props.workspace?.reasoning.fineReview?.summary || null);
const projectInfoLabels: Record<string, string> = {
  projectName: "项目名称",
  projectCode: "项目编号",
  tendererName: "招标人 / 采购人",
  agencyName: "代理机构",
  procurementMethod: "采购方式",
  noticeType: "公告类型",
  tenderNoticeDate: "招标 / 采购公告发布日期",
  noticeDate: "公告日期",
  budgetAmount: "预算金额",
  maxPrice: "最高限价",
  serviceType: "项目类型",
  serviceLocation: "项目地点",
  bidDeadline: "投标截止时间",
  openingTime: "开标时间",
  bidLocation: "投标地点",
  openingLocation: "开标地点",
  projectDuration: "项目工期 / 履约期限",
  warrantyRequirement: "质保要求",
  bidCopyRequirement: "投标文件份数",
  packagingRequirement: "封装要求",
  signatureSealRequirement: "签字盖章要求",
  jointVentureRequirement: "联合体要求",
  tendererAddress: "采购人地址",
  tendererContact: "采购人联系人",
  tendererPhone: "采购人联系电话",
  agencyAddress: "代理机构地址",
  agencyContact: "代理机构联系人",
  agencyPhone: "代理机构联系电话"
};
const projectInfoItems = computed(() => {
  const project = props.workspace?.reasoning.project || {};
  return Object.entries(projectInfoLabels)
    .map(([key, label]) => ({ key, label, value: String(project[key] || "").trim() }))
    .filter((item) => item.value);
});

function summaryItems(summary: MatchSummary | null) {
  if (!summary) return [];
  return [
    { label: "要求总数", value: summary.totalRequirementCount, tone: "neutral" },
    { label: "证据覆盖率", value: `${summary.evidenceCoverageRate}%`, tone: summary.evidenceCoverageRate >= 80 ? "success" : "warning" },
    { label: "强制条款闭环", value: `${summary.mandatoryClosureRate}%`, tone: summary.mandatoryClosureRate >= 90 ? "success" : "warning" },
    { label: "待补材料", value: summary.pendingMaterialCount, tone: summary.pendingMaterialCount ? "warning" : "success" },
    { label: "待澄清", value: summary.pendingClarificationCount, tone: summary.pendingClarificationCount ? "warning" : "success" },
    { label: "否决项风险", value: summary.rejectionRiskCount, tone: summary.rejectionRiskCount ? "danger" : "success" }
  ];
}

function evidenceStatusType(
  item: NonNullable<ReasoningRow["evidenceItems"]>[number]
): "success" | "warning" | "info" | "danger" {
  if (item.isExpired) return "danger";
  if (item.isVerified) return "success";
  if (item.isHistorical) return "info";
  return "warning";
}

watch(
  () => props.workspace?.id,
  () => {
    fineStatusFilter.value = "全部状态";
  }
);

function chooseFile() {
  fileInput.value?.click();
}

function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null;
  emit("fileSelected", file);
}

function exportReport() {
  window.open("/api/materials/export", "_blank");
}
</script>

<template>
  <div class="matching-page">
    <el-row :gutter="16">
      <el-col :xs="24" :lg="14">
        <el-card class="panel" shadow="never">
          <template #header>
            <div class="panel-header">
              <span>{{ inputKind === "tender_document" ? "输入完整招标文件" : "输入招标公告" }}</span>
              <el-tag type="info" effect="plain">支持上传或粘贴</el-tag>
            </div>
          </template>

          <el-radio-group
            :model-value="inputKind"
            class="field"
            @update:model-value="emit('update:input-kind', $event)"
          >
            <el-radio-button label="notice">公告资格粗匹配</el-radio-button>
            <el-radio-button label="tender_document">完整招标文件细匹配</el-radio-button>
          </el-radio-group>

          <el-input v-model="importForm.name" placeholder="文件名称（可选）" class="field" />

          <input
            ref="fileInput"
            class="hidden-file-input"
            type="file"
            accept=".pdf,.docx,.xlsx,.txt,.md"
            @change="onFileChange"
          />
          <button class="upload-dropzone" type="button" @click="chooseFile">
            <el-icon :size="24"><Upload /></el-icon>
            <span>{{ fileName || (inputKind === "tender_document" ? "点击选择完整招标文件" : "点击选择招标公告") }}</span>
            <small>PDF、DOCX、XLSX、TXT、Markdown</small>
          </button>

          <el-input
            v-model="importForm.text"
            :rows="9"
            type="textarea"
            :placeholder="inputKind === 'tender_document' ? '也可以直接粘贴完整招标文件内容，系统将拆解为可核验条款' : '也可以直接粘贴公告正文或资格要求'"
            class="field"
          />

          <div class="action-row">
            <span class="form-hint">
              {{ inputKind === "tender_document" ? "将生成公告资格粗匹配和投标应答预审。" : "将生成是否值得获取完整招标文件的资格评估。" }}
            </span>
            <el-button :icon="Sparkles" :loading="busy" type="primary" @click="emit('evaluate')">
              开始匹配评估
            </el-button>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="10">
        <el-card class="panel" shadow="never">
          <template #header>
            <div class="panel-header">
              <span>企业资料库</span>
              <el-tag v-if="selectedLibrary" type="success" effect="plain">
                {{ selectedLibrary.documentCount }} 份资料
              </el-tag>
            </div>
          </template>

          <el-select
            :model-value="selectedLibraryKey"
            class="field"
            placeholder="选择用于匹配的资料库"
            @update:model-value="emit('update:selected-library-key', $event)"
          >
            <el-option
              v-for="library in libraries"
              :key="library.key"
              :label="library.name"
              :value="library.key"
            />
          </el-select>

          <div v-if="selectedLibrary" class="library-summary">
            <div class="library-summary-title">
              <el-icon><FileText /></el-icon>
              <strong>{{ selectedLibrary.name }}</strong>
            </div>
            <p>{{ selectedLibrary.description || "将使用企业资质、案例和服务能力资料参与匹配。" }}</p>
            <div class="catalog-meta">
              <el-tag
                v-for="document in selectedLibrary.documents.slice(0, 4)"
                :key="document.id"
                size="small"
                effect="plain"
              >
                {{ document.name }}
              </el-tag>
            </div>
          </div>
          <div v-if="materialsProfile?.qualifications?.length" class="profile-summary">
            <div class="library-summary-title">
              <el-icon><FileCheck2 /></el-icon>
              <strong>已识别企业画像</strong>
              <el-button :icon="Download" size="small" text type="primary" @click="exportReport">
                导出报告
              </el-button>
            </div>
            <p v-if="materialsProfile.company" class="profile-company">{{ materialsProfile.company }}</p>
            <div class="catalog-meta">
              <el-tag
                v-for="q in materialsProfile.qualifications.slice(0, 6)"
                :key="q"
                size="small"
                type="success"
                effect="plain"
              >
                {{ q }}
              </el-tag>
            </div>
            <p class="form-hint">
              证书 {{ materialsProfile.certificates?.length || 0 }} 份
              <template v-if="materialsProfile.certificates?.length">
                · {{ materialsProfile.certificates.map((c) => c.name).join("、") }}
              </template>
            </p>
            <p v-if="materialsProfile.service_regions?.length" class="form-hint">
              服务区域：{{ materialsProfile.service_regions.join("、") }}
            </p>
          </div>
          <el-empty v-else description="暂无可用的企业资料库" />
        </el-card>

        <el-alert
          title="评估结果会从总体评分、分项匹配、证据依据和风险建议几个维度呈现。"
          type="info"
          show-icon
          :closable="false"
        />
      </el-col>
    </el-row>

    <el-card v-if="workspace && inputKind === 'notice'" class="panel result-panel" shadow="never">
      <template #header>
        <div class="panel-header">
          <div class="result-title">
            <el-icon><FileCheck2 /></el-icon>
            <span>匹配评估结果</span>
          </div>
          <el-button type="primary" plain @click="emit('openWorkspace')">进入标书工作台</el-button>
        </div>
      </template>

      <div class="score-overview">
        <div class="score-ring">
          <strong>
            {{
              workspace.reasoning.eligibility?.assessmentAvailable === false
                ? "--"
                : workspace.reasoning.eligibility?.eligibilityScore ?? workspace.reasoning.score
            }}
          </strong>
          <span>{{ workspace.reasoning.eligibility?.assessmentAvailable === false ? "资料不足" : "资格匹配分" }}</span>
        </div>
        <div class="score-summary">
          <p class="result-project">{{ workspace.title }}</p>
          <el-tag
            :type="(workspace.reasoning.eligibility?.shouldGetTenderDocument ?? workspace.reasoning.score >= 70) ? 'success' : 'warning'"
            effect="light"
          >
            {{ workspace.reasoning.eligibility?.recommendation || workspace.reasoning.decision }}
          </el-tag>
          <p class="form-hint">
            公告资格粗匹配；项目相关性 {{ workspace.reasoning.relevance?.score ?? workspace.project.relevanceScore ?? "-" }} 分。
          </p>
          <div class="catalog-meta">
            <el-tag size="small" effect="plain">
              {{ workspace.reasoning.extraction?.method?.includes("llm") ? "大模型 + 规则校验" : "规则抽取" }}
            </el-tag>
            <el-tag v-if="workspace.reasoning.extraction?.modelUsed" size="small" effect="plain">
              {{ workspace.reasoning.extraction.modelUsed }}
            </el-tag>
          </div>
        </div>
      </div>

      <el-alert
        v-if="workspace.reasoning.extraction?.warning"
        :title="workspace.reasoning.extraction.warning"
        type="warning"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <div v-if="projectInfoItems.length" class="project-info-section mt-12">
        <div class="section-heading">
          <strong>项目基础信息</strong>
          <span>基础信息不参与企业资格匹配</span>
        </div>
        <div class="project-info-grid">
          <div v-for="item in projectInfoItems" :key="item.key" class="project-info-item">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>
      </div>

      <el-alert
        v-for="warning in workspace.reasoning.extraction?.warnings || []"
        :key="warning"
        :title="warning"
        type="warning"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <div v-if="announcementSummary" class="match-summary-grid mt-12">
        <div
          v-for="item in summaryItems(announcementSummary)"
          :key="item.label"
          class="match-summary-item"
          :data-tone="item.tone"
        >
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </div>

      <el-alert
        v-if="workspace.reasoning.eligibility?.hardBlockers?.length"
        title="发现未满足的硬性资格项，当前不建议参与。"
        type="error"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <div class="section-heading mt-12">
        <strong>资格条件智能筛查</strong>
        <span>仅匹配企业资质、人员、业绩、财务、信用及其他资格准入条款</span>
      </div>
      <template v-if="workspace.reasoning.rows.length">
        <el-table :data="workspace.reasoning.rows" class="mt-12" size="small">
          <el-table-column prop="kind" label="资格类型" width="110" />
          <el-table-column prop="status" label="匹配状态" width="100" />
          <el-table-column prop="requirement" label="资格准入条款" min-width="190" />
          <el-table-column label="企业依据" min-width="260">
            <template #default="{ row }">
              <div v-if="row.evidenceItems?.length" class="evidence-list">
                <div v-for="evidence in row.evidenceItems" :key="evidence.evidenceId || evidence.id" class="evidence-item">
                  <div class="evidence-item-heading">
                    <strong>{{ evidence.name }}</strong>
                    <el-tag size="small" :type="evidenceStatusType(evidence)" effect="plain">
                      {{ evidence.verificationStatus || evidence.status || "待核验" }}
                    </el-tag>
                  </div>
                  <span v-if="evidence.sourceFiles?.length">{{ evidence.sourceFiles.join("、") }}</span>
                  <span v-if="evidence.sourceLocator || evidence.locator">
                    {{ evidence.sourceLocator || evidence.locator }}
                  </span>
                  <p v-if="evidence.excerpt">{{ evidence.excerpt }}</p>
                </div>
              </div>
              <span v-else class="form-hint">{{ row.evidence }}</span>
            </template>
          </el-table-column>
          <el-table-column label="建议与理由" min-width="220">
            <template #default="{ row }">
              {{ row.suggestion || row.risk }}
            </template>
          </el-table-column>
        </el-table>
      </template>
      <el-empty
        v-else
        class="qualification-empty"
        description="当前内容未识别到可用于资格匹配的准入条款"
      />

      <div v-if="workspace.reasoning.risks.length" class="risk-list">
        <el-card
          v-for="(risk, index) in workspace.reasoning.risks"
          :key="index"
          shadow="never"
          class="risk-card"
        >
          <strong>{{ risk.title }}</strong>
          <p>{{ risk.detail }}</p>
        </el-card>
      </div>
    </el-card>

    <el-card v-if="workspace?.reasoning.fineReview && inputKind === 'tender_document'" class="panel result-panel" shadow="never">
      <template #header>
        <div class="panel-header">
          <div class="result-title">
            <el-icon><FileCheck2 /></el-icon>
            <span>投标应答预审</span>
          </div>
          <div class="catalog-actions">
            <el-tag
              :type="workspace.reasoning.fineReview.hardBlockers.length ? 'danger' : 'warning'"
              effect="light"
            >
              {{ workspace.reasoning.fineReview.recommendation }}
            </el-tag>
            <el-button type="primary" plain @click="emit('openWorkspace')">进入标书工作台</el-button>
          </div>
        </div>
      </template>

      <div class="score-overview">
        <div class="score-ring">
          <strong>{{ workspace.reasoning.fineReview.score }}</strong>
          <span>应答预审分</span>
        </div>
        <div class="score-summary">
          <p class="result-project">已拆解 {{ workspace.reasoning.fineReview.clauseCount }} 条可核验条款</p>
          <div class="catalog-meta">
            <el-tag size="small" effect="plain">
              {{ workspace.reasoning.fineReview.extraction.method === "llm" ? "大模型条款拆解" : "规则条款拆解" }}
            </el-tag>
            <el-tag v-if="workspace.reasoning.fineReview.extraction.modelUsed" size="small" effect="plain">
              {{ workspace.reasoning.fineReview.extraction.modelUsed }}
            </el-tag>
            <el-tag v-for="(count, status) in workspace.reasoning.fineReview.statusCounts" :key="status" size="small" effect="plain">
              {{ status }} {{ count }}
            </el-tag>
          </div>
        </div>
      </div>

      <el-alert
        v-if="workspace.reasoning.fineReview.extraction.warning"
        :title="workspace.reasoning.fineReview.extraction.warning"
        type="warning"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <div v-if="fineSummary" class="match-summary-grid mt-12">
        <div
          v-for="item in summaryItems(fineSummary)"
          :key="item.label"
          class="match-summary-item"
          :data-tone="item.tone"
        >
          <span>{{ item.label }}</span>
          <strong>{{ item.value }}</strong>
        </div>
      </div>

      <el-alert
        v-if="workspace.reasoning.fineReview.hardBlockers.length"
        :title="`发现 ${workspace.reasoning.fineReview.hardBlockers.length} 条已确认不满足的否决性条款，已拦截参与建议。`"
        type="error"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <el-alert
        v-if="!workspace.reasoning.fineReview.hardBlockers.length && workspace.reasoning.fineReview.rejectionRisks?.length"
        :title="`存在 ${workspace.reasoning.fineReview.rejectionRisks.length} 条否决性要求尚未闭环，必须人工核验后才能进入正式编制。`"
        type="warning"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <el-alert
        v-if="workspace.reasoning.fineReview.unresolvedMandatory.length"
        :title="`存在 ${workspace.reasoning.fineReview.unresolvedMandatory.length} 条待补材料或待澄清的强制要求，请先人工复核。`"
        type="warning"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <div v-if="latestAuditRecord" class="audit-summary mt-12">
        <div class="audit-summary-heading">
          <strong>人工审核记录</strong>
          <el-tag size="small" type="success" effect="plain">
            共 {{ auditRecords.length }} 条
          </el-tag>
        </div>
        <span>
          最近一次：{{ latestAuditRecord.reviewer }}于 {{ latestAuditRecord.reviewedAt }} 将
          “{{ latestAuditRecord.requirement }}”从“{{ latestAuditRecord.previousStatus }}”调整为“{{ latestAuditRecord.newStatus }}”
        </span>
      </div>

      <div class="fine-review-toolbar mt-12">
        <span class="form-hint">按匹配状态查看条款</span>
        <el-select v-model="fineStatusFilter" aria-label="按匹配状态筛选" class="fine-status-filter">
          <el-option
            v-for="status in fineStatusOptions"
            :key="status"
            :label="status === '全部状态' ? status : `${status} ${workspace.reasoning.fineReview.statusCounts[status] ?? 0}`"
            :value="status"
          />
        </el-select>
      </div>

      <div v-if="projectInfoItems.length" class="module-block">
        <div class="module-heading">
          <span class="module-num">①</span>
          <strong>招标项目基本信息</strong>
        </div>
        <div class="project-info-grid">
          <div v-for="item in projectInfoItems" :key="item.key" class="project-info-item">
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>
      </div>

      <div v-if="moduleGroups.length" class="module-tabs">
        <button
          v-for="group in moduleGroups"
          :key="group.module"
          type="button"
          class="module-tab"
          :class="{ 'module-tab--active': group.module === (activeModuleGroup?.module || '') }"
          @click="activeModule = group.module"
        >
          <span class="module-num">{{ MODULE_NUMBERS[group.module] }}</span>
          <strong>{{ group.module }}</strong>
          <el-tag size="small" effect="plain">{{ group.rows.length }} 条</el-tag>
        </button>
      </div>

      <div v-if="activeModuleGroup" class="module-block">
        <ol class="requirement-list">
          <li v-for="(row, idx) in activeModuleGroup.rows" :key="`${activeModuleGroup.module}-${idx}`" class="requirement-item">
            <div class="requirement-item-head">
              <span class="requirement-index">{{ idx + 1 }}</span>
              <el-tag size="small" effect="plain">{{ row.kind }}</el-tag>
              <el-select
                :model-value="row.status"
                size="small"
                class="requirement-status-select"
                :disabled="busy || !row.id"
                aria-label="修改匹配结论"
                @update:model-value="emit('update-fine-status', { rowId: row.id || '', status: $event })"
              >
                <el-option v-for="status in fineStatusValues" :key="status" :label="status" :value="status" />
              </el-select>
              <el-tag v-if="row.statusSource === '人工审核'" size="small" type="success" effect="plain">
                人工
              </el-tag>
              <span v-if="row.sourceLocator" class="requirement-locator">{{ row.sourceLocator }}</span>
            </div>
            <div class="requirement-text">{{ row.requirement }}</div>
            <div class="requirement-meta">
              <div v-if="row.evidenceItems?.length" class="evidence-list">
                <div v-for="evidence in row.evidenceItems" :key="evidence.evidenceId || evidence.id" class="evidence-item">
                  <div class="evidence-item-heading">
                    <strong>{{ evidence.name }}</strong>
                    <el-tag size="small" :type="evidenceStatusType(evidence)" effect="plain">
                      {{ evidence.verificationStatus || evidence.status || "待核验" }}
                    </el-tag>
                  </div>
                  <span v-if="evidence.sourceFiles?.length">{{ evidence.sourceFiles.join("、") }}</span>
                  <span v-if="evidence.sourceLocator || evidence.locator">
                    {{ evidence.sourceLocator || evidence.locator }}
                  </span>
                  <p v-if="evidence.excerpt">{{ evidence.excerpt }}</p>
                </div>
              </div>
              <span v-else class="form-hint">{{ row.evidence }}</span>
              <span v-if="row.suggestion" class="requirement-suggestion">建议：{{ row.suggestion }}</span>
            </div>
          </li>
        </ol>
      </div>

      <el-empty
        v-if="!projectInfoItems.length && !moduleGroups.length"
        class="qualification-empty"
        description="当前内容未识别到可展示的要求"
      />
    </el-card>
  </div>
</template>
