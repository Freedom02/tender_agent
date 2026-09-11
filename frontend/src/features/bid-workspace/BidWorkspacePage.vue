<script setup lang="ts">
import { computed, ref } from "vue";
import { BriefcaseBusiness, Database, FileText, Rocket, Save, Upload } from "lucide-vue-next";
import type { CatalogItem, LibrarySummary, MaterialProfile, Workspace } from "../../shared/types";

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
        <span class="form-hint">后续将由智能体读取招标要求，并结合企业资料自动生成内容和排版。</span>
        <el-button :icon="Rocket" :disabled="!project" :loading="busy" type="primary" @click="emit('generate')">
          生成标书草稿
        </el-button>
      </div>
    </el-card>

  </div>
</template>
