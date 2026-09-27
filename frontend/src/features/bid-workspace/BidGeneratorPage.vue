<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { ElMessage } from "element-plus";
import { ArrowLeft, Download, FileText, Rocket } from "lucide-vue-next";
import { api } from "../../services/api";
import type { BidDocument, BidMode, BidSection, CatalogItem, Workspace } from "../../shared/types";

const props = defineProps<{
  workspace: Workspace | null;
  project: CatalogItem | null;
  busy: boolean;
}>();

const emit = defineEmits<{
  back: [];
}>();

const bidDocument = ref<BidDocument | null>(null);
const mode = ref<BidMode>("open");
const generating = ref(false);
const exporting = ref(false);
const exportFiles = ref<{ name: string; url: string }[]>([]);
const activeSections = ref<string[]>([]);

function statusTagType(status?: string): 'success' | 'warning' | 'danger' | 'info' {
  const s = (status || "").toLowerCase();
  if (["完成", "已生成", "generated", "done", "complete", "success"].includes(s)) return "success";
  if (["生成中", "待生成", "pending", "generating"].includes(s)) return "warning";
  if (["缺失", "missing", "失败", "failed", "error"].includes(s)) return "danger";
  return "info";
}
const STATUS_NAMES: Record<string, string> = {
  generated: "已生成",
  pending: "待生成",
  generating: "生成中",
  missing: "缺失",
  planned: "正文待撰写",
};
function statusName(status?: string): string {
  return STATUS_NAMES[status || ""] || status || "待生成";
}

const workspaceId = computed(() => props.workspace?.id || "");
const plan = computed(() => bidDocument.value?.plan || []);

async function generateDocument() {
  if (!workspaceId.value) {
    ElMessage.warning("请先选择一个工作台项目");
    return;
  }
  generating.value = true;
  try {
    await api.bidDocumentPlan(workspaceId.value);
    const result = await api.bidDocumentGenerate(workspaceId.value, mode.value);
    bidDocument.value = result.bidDocument;
    if (result.bidDocument.mode) mode.value = result.bidDocument.mode;
    activeSections.value = result.bidDocument.plan?.map((section: BidSection) => section.sectionId) || [];
    ElMessage.success("标书文档已生成");
  } catch (error) {
    ElMessage.error((error as Error).message);
  } finally {
    generating.value = false;
  }
}

async function exportDocument() {
  if (!workspaceId.value) {
    ElMessage.warning("请先选择一个工作台项目");
    return;
  }
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

watch(
  () => bidDocument.value,
  (doc) => {
    if (doc?.plan?.length && !activeSections.value.length) {
      activeSections.value = doc.plan.map((section) => section.sectionId);
    }
  }
);

watch(
  () => props.workspace?.bidDocument as BidDocument | undefined,
  (doc) => {
    if (doc && doc.plan?.length) {
      bidDocument.value = doc;
      if (doc.mode) mode.value = doc.mode;
    }
  },
  { immediate: true }
);
</script>

<template>
  <div class="bid-generator-page">
    <div class="generator-toolbar">
      <el-button :icon="ArrowLeft" text @click="emit('back')">返回标书工作台</el-button>
      <div class="generator-toolbar-title">
        <el-icon><FileText /></el-icon>
        <span>标书生成</span>
      </div>
      <div class="toolbar-right">
        <template v-if="bidDocument">
          <el-tag type="info" effect="plain">{{ bidDocument.mode === "secret" ? "暗标" : "明标" }}</el-tag>
          <el-tag v-if="bidDocument.status" type="success" effect="plain" class="tag-margin">
            {{ bidDocument.status }}
          </el-tag>
        </template>
        <el-tag v-else type="warning" effect="plain">未生成文档</el-tag>
      </div>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :lg="17">
        <el-card class="panel document-preview-panel" shadow="never">
          <template #header>
            <div class="panel-header">
              <div>
                <strong>章节编辑</strong>
                <p class="panel-subtitle">{{ project?.title || "尚未选择招标项目" }}</p>
              </div>
            </div>
          </template>

          <template v-if="bidDocument && plan.length">
            <div class="editor-mode-row">
              <span class="editor-mode-label">标书类型：</span>
              <el-radio-group v-model="mode" size="small">
                <el-radio-button value="open">明标</el-radio-button>
                <el-radio-button value="secret">暗标</el-radio-button>
              </el-radio-group>
            </div>

            <el-collapse v-model="activeSections" class="section-editor">
              <el-collapse-item
                v-for="(section, index) in plan"
                :key="section.sectionId"
                :name="section.sectionId"
              >
                <template #title>
                  <div class="section-title">
                    <span class="section-index">{{ index + 1 }}</span>
                    <strong>{{ section.title }}</strong>
                    <el-tag size="small" type="info" effect="plain">{{ section.kind || "章节" }}</el-tag>
                    <el-tag size="small" effect="plain">{{ section.source || "—" }}</el-tag>
                    <el-tag
                      v-if="section.status"
                      size="small"
                      :type="statusTagType(section.status)"
                      effect="plain"
                    >
                      {{ statusName(section.status) }}
                    </el-tag>
                  </div>
                </template>
                <el-input
                  v-model="section.content"
                  type="textarea"
                  :rows="12"
                  class="section-textarea"
                  placeholder="本章节内容为空，可在此编辑"
                />
              </el-collapse-item>
            </el-collapse>
          </template>
          <el-empty v-else description="尚未生成标书文档，请点击右侧「生成文档」开始。" />
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="7">
        <el-card class="panel generator-side-panel" shadow="never">
          <template #header><span>文档操作</span></template>

          <div v-if="workspace" class="document-info">
            <div class="document-info-icon"><el-icon :size="24"><FileText /></el-icon></div>
            <strong>{{ project?.title || workspace.title }}</strong>
            <span>生成文档后可编辑各章节内容并导出</span>
          </div>

          <el-button
            class="side-action"
            :icon="Rocket"
            :disabled="!workspace"
            :loading="generating || busy"
            type="primary"
            @click="generateDocument"
          >
            生成文档
          </el-button>
          <el-button
            class="side-action"
            :icon="Download"
            :disabled="!workspace || !bidDocument"
            :loading="exporting"
            type="success"
            @click="exportDocument"
          >
            导出文档
          </el-button>

          <template v-if="exportFiles.length">
            <div class="export-files">
              <div class="export-files-title">导出文件</div>
              <a
                v-for="(file, i) in exportFiles"
                :key="i"
                class="export-file-link"
                :href="file.url"
                target="_blank"
                rel="noopener"
              >
                <el-icon><FileText /></el-icon>
                <span>{{ file.name }}</span>
              </a>
            </div>
          </template>

          <el-alert
            class="generator-note"
            title="选择标书类型后点击「生成文档」，将根据招标文件和企业资料库自动生成响应内容。导出后可下载文档文件。"
            type="info"
            show-icon
            :closable="false"
          />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 6px;
}

.tag-margin {
  margin-left: 6px;
}

.editor-mode-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 14px;
  padding: 0 2px;
}

.editor-mode-label {
  color: var(--text-regular);
  font-size: 13px;
}

.section-editor {
  --el-collapse-border-color: var(--border-color);
}

.section-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  min-width: 0;
  padding-right: 4px;
}

.section-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 22px;
  height: 22px;
  border-radius: 6px;
  background: rgba(var(--primary-rgb), 0.1);
  color: var(--primary);
  font-size: 12px;
  font-weight: 700;
}

.section-textarea :deep(.el-textarea__inner) {
  min-height: 200px;
  font-size: 14px;
  line-height: 1.85;
}

.export-files {
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-soft);
}

.export-files-title {
  margin-bottom: 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.export-file-link {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 0;
  color: var(--primary);
  font-size: 13px;
  text-decoration: none;
  transition: opacity 0.15s;
}

.export-file-link:hover {
  opacity: 0.8;
}
</style>
