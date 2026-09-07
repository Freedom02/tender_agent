<script setup lang="ts">
import { ArrowLeft, Download, FileText, Rocket, Save } from "lucide-vue-next";
import type { CatalogItem, Workspace } from "../../shared/types";

defineProps<{
  workspace: Workspace | null;
  project: CatalogItem | null;
  busy: boolean;
}>();

const emit = defineEmits<{
  back: [];
  saveDraft: [];
  generateDocument: [];
  downloadDocument: [];
  "update:draft": [value: string];
}>();
</script>

<template>
  <div class="bid-generator-page">
    <div class="generator-toolbar">
      <el-button :icon="ArrowLeft" text @click="emit('back')">返回标书工作台</el-button>
      <div class="generator-toolbar-title">
        <el-icon><FileText /></el-icon>
        <span>标书生成</span>
      </div>
      <el-tag v-if="workspace" type="info" effect="plain">草稿 v{{ workspace.draftVersion }}</el-tag>
    </div>

    <el-row :gutter="16">
      <el-col :xs="24" :lg="17">
        <el-card class="panel document-preview-panel" shadow="never">
          <template #header>
            <div class="panel-header">
              <div>
                <strong>草稿预览与编辑</strong>
                <p class="panel-subtitle">{{ project?.title || "尚未选择招标项目" }}</p>
              </div>
              <el-tag type="warning" effect="plain">未生成正式文档</el-tag>
            </div>
          </template>

          <template v-if="workspace">
            <div class="document-page">
              <div class="document-page-head">
                <span>响应文件（草稿）</span>
                <small>可直接编辑以下内容</small>
              </div>
              <el-input
                :model-value="workspace.draft"
                :rows="28"
                type="textarea"
                class="draft-editor document-editor"
                @update:model-value="emit('update:draft', $event)"
              />
            </div>
          </template>
          <el-empty v-else description="请先从招标项目或匹配评估中选择一个项目" />
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="7">
        <el-card class="panel generator-side-panel" shadow="never">
          <template #header><span>文档操作</span></template>

          <div v-if="workspace" class="document-info">
            <div class="document-info-icon"><el-icon :size="24"><FileText /></el-icon></div>
            <strong>{{ project?.title || workspace.title }}</strong>
            <span>当前草稿可继续编辑和保存</span>
          </div>

          <el-button
            class="side-action"
            :icon="Save"
            :disabled="!workspace"
            :loading="busy"
            plain
            @click="emit('saveDraft')"
          >
            保存草稿
          </el-button>
          <el-button
            class="side-action"
            :icon="Rocket"
            :disabled="!workspace"
            :loading="busy"
            type="primary"
            @click="emit('generateDocument')"
          >
            生成文档
          </el-button>
          <el-button
            class="side-action"
            :icon="Download"
            :disabled="!workspace"
            type="success"
            @click="emit('downloadDocument')"
          >
            下载文档
          </el-button>

          <el-alert
            class="generator-note"
            title="后续接入智能体后，将根据招标文件、模板、行业和企业资料库自动补全内容并完成排版。"
            type="info"
            show-icon
            :closable="false"
          />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>
