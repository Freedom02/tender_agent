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
  selectedLibraryKey: string;
}>();

const emit = defineEmits<{
  evaluate: [];
  fileSelected: [file: File | null];
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
              <span>输入招标文件</span>
              <el-tag type="info" effect="plain">支持上传或粘贴</el-tag>
            </div>
          </template>

          <el-input v-model="importForm.name" placeholder="文件名称（可选）" class="field" />

          <input
            ref="fileInput"
            class="hidden-file-input"
            type="file"
            accept=".pdf,.doc,.docx,.txt"
            @change="onFileChange"
          />
          <button class="upload-dropzone" type="button" @click="chooseFile">
            <el-icon :size="24"><Upload /></el-icon>
            <span>{{ fileName || "点击选择招标文件" }}</span>
            <small>PDF、DOC、DOCX、TXT</small>
          </button>

          <el-input
            v-model="importForm.text"
            :rows="9"
            type="textarea"
            placeholder="也可以直接粘贴公告正文、资格要求或招标文件内容"
            class="field"
          />

          <div class="action-row">
            <span class="form-hint">导入后将创建项目，并基于企业资料库生成匹配结果。</span>
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
          <strong>{{ workspace.reasoning.score }}</strong>
          <span>综合评分</span>
        </div>
        <div class="score-summary">
          <p class="result-project">{{ workspace.title }}</p>
          <el-tag :type="workspace.reasoning.score >= 70 ? 'success' : 'warning'" effect="light">
            {{ workspace.reasoning.decision }}
          </el-tag>
          <p class="form-hint">评分基于招标要求与当前企业资料库的演示匹配结果。</p>
        </div>
      </div>

      <el-table :data="workspace.reasoning.rows" class="mt-12" size="small">
        <el-table-column prop="kind" label="评估维度" width="110" />
        <el-table-column prop="status" label="匹配状态" width="100" />
        <el-table-column prop="requirement" label="招标要求" min-width="190" />
        <el-table-column prop="evidence" label="企业依据" min-width="220" />
        <el-table-column prop="risk" label="建议与理由" min-width="220" />
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
    <el-empty v-else class="matching-empty" description="上传招标文件或粘贴招标内容后开始评估" />
  </div>
</template>
