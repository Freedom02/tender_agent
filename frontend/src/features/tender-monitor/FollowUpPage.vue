<script setup lang="ts">
import { computed, ref } from "vue";
import { ClipboardCheck, ExternalLink, FileCheck2, Flag } from "lucide-vue-next";
import type { CatalogItem, Workspace } from "../../shared/types";
import NoticeReviewDrawer from "./NoticeReviewDrawer.vue";

const props = defineProps<{
  items: CatalogItem[];
  workspaces: Workspace[];
  busy: boolean;
}>();

const emit = defineEmits<{
  removeFollowUp: [item: CatalogItem];
  preReview: [item: CatalogItem];
}>();

const drawerVisible = ref(false);
const drawerItem = ref<CatalogItem | null>(null);

const drawerWorkspace = computed(() => {
  const item = drawerItem.value;
  if (!item) return null;
  return (
    props.workspaces.find((w) => w.id === item.workspaceId) ||
    props.workspaces.find((w) => w.projectId === item.id) ||
    null
  );
});

function openPreReview(item: CatalogItem) {
  drawerItem.value = item;
  drawerVisible.value = true;
  if (!drawerWorkspace.value && !props.busy) {
    emit("preReview", item);
  }
}
</script>

<template>
  <el-card class="panel follow-up-panel" shadow="never">
    <template #header>
      <div class="panel-header">
        <div class="result-title">
          <el-icon><ClipboardCheck /></el-icon>
          <span>跟进项目列表</span>
        </div>
        <el-tag type="success" effect="plain">{{ items.length }} 个项目</el-tag>
      </div>
    </template>

    <el-table v-if="items.length" class="follow-up-table" :data="items" stripe>
      <el-table-column label="项目名称" width="300">
        <template #default="{ row }">
          <a
            v-if="row.sourceUrl"
            class="project-link"
            :href="row.sourceUrl"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ row.title }}
            <el-icon class="external-link-icon"><ExternalLink /></el-icon>
          </a>
          <span v-else class="project-link project-link-disabled">{{ row.title }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="category" label="类型" width="108" />
      <el-table-column prop="region" label="地区" width="100" />
      <el-table-column label="获取文件时间" width="188">
        <template #default="{ row }">
          {{ row.registrationPeriod || "待核验" }}
        </template>
      </el-table-column>
      <el-table-column label="响应文件截止时间" width="188" class-name="deadline-col">
        <template #default="{ row }">
          {{ row.bidDeadline || "待核验" }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="112" fixed="right">
        <template #default="{ row }">
          <div class="action-stack">
            <el-button size="small" :icon="Flag" type="success" plain @click="emit('removeFollowUp', row)">取消跟进</el-button>
            <el-button size="small" :icon="FileCheck2" type="primary" plain @click="openPreReview(row)">公告预审</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-else description="暂无跟进项目，请先在招标项目列表中点击“跟进”" />
  </el-card>

  <NoticeReviewDrawer
    v-model="drawerVisible"
    :workspace="drawerWorkspace"
    :busy="busy"
    :title="drawerItem?.title || '公告预审结果'"
  />
</template>

<style scoped>
:deep(.tender-monitor-table .cell),
:deep(.follow-up-table .cell) {
  padding-right: 6px;
  padding-left: 6px;
}

:deep(.follow-up-table .cell),
:deep(.tender-monitor-table .cell) {
  white-space: normal;
  word-break: break-word;
  line-height: 1.5;
}

:deep(.follow-up-table .deadline-col .cell) {
  padding-left: 6px !important;
}

.action-stack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: stretch;
}
.action-stack :deep(.el-button) {
  margin-left: 0 !important;
  padding-right: 6px;
  padding-left: 6px;
}
</style>
