<script setup lang="ts">
import { computed, ref } from "vue";
import { FileCheck2, FileText, Sparkles, Upload } from "lucide-vue-next";
import type { LibrarySummary, Workspace } from "../../shared/types";

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
}>();

const emit = defineEmits<{
  evaluate: [];
  fileSelected: [file: File | null];
  "update:input-kind": [kind: "notice" | "tender_document"];
  "update:selected-library-key": [key: string];
  openWorkspace: [];
}>();

const fileInput = ref<HTMLInputElement | null>(null);
const fileName = computed(() => props.importFile?.name || "");
const selectedLibrary = computed(
  () => props.libraries.find((library) => library.key === props.selectedLibraryKey) || props.libraries[0] || null
);

function chooseFile() {
  fileInput.value?.click();
}

function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null;
  emit("fileSelected", file);
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

    <el-card v-if="workspace" class="panel result-panel" shadow="never">
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
          <strong>{{ workspace.reasoning.eligibility?.eligibilityScore ?? workspace.reasoning.score }}</strong>
          <span>资格匹配分</span>
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
              {{ workspace.reasoning.extraction?.method === "llm" ? "大模型结构化抽取" : "规则抽取" }}
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

      <el-alert
        v-if="workspace.reasoning.eligibility?.hardBlockers?.length"
        title="发现未满足的硬性资格项，当前不建议参与。"
        type="error"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <el-table :data="workspace.reasoning.rows" class="mt-12" size="small">
        <el-table-column prop="kind" label="评估维度" width="110" />
        <el-table-column prop="status" label="匹配状态" width="100" />
        <el-table-column prop="requirement" label="招标要求" min-width="190" />
        <el-table-column prop="evidence" label="企业依据" min-width="220" />
        <el-table-column label="建议与理由" min-width="220">
          <template #default="{ row }">
            {{ row.suggestion || row.risk }}
          </template>
        </el-table-column>
      </el-table>

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

    <el-card v-if="workspace?.reasoning.fineReview" class="panel result-panel" shadow="never">
      <template #header>
        <div class="panel-header">
          <div class="result-title">
            <el-icon><FileCheck2 /></el-icon>
            <span>投标应答预审</span>
          </div>
          <el-tag type="warning" effect="light">{{ workspace.reasoning.fineReview.recommendation }}</el-tag>
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

      <el-alert
        v-if="workspace.reasoning.fineReview.unresolvedMandatory.length"
        :title="`存在 ${workspace.reasoning.fineReview.unresolvedMandatory.length} 条待补材料或待澄清的强制要求，请先人工复核。`"
        type="warning"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <el-table :data="workspace.reasoning.fineReview.rows" class="mt-12" size="small">
        <el-table-column prop="kind" label="条款类型" width="110" />
        <el-table-column prop="status" label="状态" width="100" />
        <el-table-column prop="sourceLocator" label="文件定位" width="110" />
        <el-table-column prop="requirement" label="招标条款" min-width="220" />
        <el-table-column prop="evidence" label="企业证据" min-width="240" />
        <el-table-column prop="suggestion" label="处理建议" min-width="220" />
      </el-table>
    </el-card>
    <el-empty v-else class="matching-empty" description="上传招标文件或粘贴招标内容后开始评估" />
  </div>
</template>
