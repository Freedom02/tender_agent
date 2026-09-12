<script setup lang="ts">
import { ClipboardCheck, ExternalLink, Flag, Search } from "lucide-vue-next";
import type { CatalogItem } from "../../shared/types";

defineProps<{
  items: CatalogItem[];
}>();

const emit = defineEmits<{
  removeFollowUp: [item: CatalogItem];
  enterMatching: [item: CatalogItem];
}>();
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

    <el-table v-if="items.length" :data="items" stripe>
      <el-table-column label="项目名称" min-width="320">
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
      <el-table-column prop="category" label="类型" width="150" />
      <el-table-column prop="region" label="地区" min-width="170" />
      <el-table-column label="报名时间" min-width="220">
        <template #default="{ row }">
          {{ row.registrationPeriod || row.registrationDeadline || "待核验" }}
        </template>
      </el-table-column>
      <el-table-column label="响应文件截止时间" min-width="220" class-name="deadline-col">
        <template #default="{ row }">
          {{ row.bidDeadline || "待核验" }}
        </template>
      </el-table-column>
      <el-table-column label="操作" width="120" fixed="right">
        <template #default="{ row }">
          <div class="action-stack">
            <el-button size="small" :icon="Flag" type="success" plain @click="emit('removeFollowUp', row)">取消跟进</el-button>
            <el-button size="small" :icon="Search" type="primary" plain @click="emit('enterMatching', row)">进入匹配</el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-else description="暂无跟进项目，请先在招标项目列表中点击“跟进”" />
  </el-card>
</template>

<style scoped>
:deep(.deadline-col .cell) {
  padding-left: 24px !important;
}

.action-stack {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: stretch;
}
.action-stack :deep(.el-button) {
  margin-left: 0 !important;
}
</style>
