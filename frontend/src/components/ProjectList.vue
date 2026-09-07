<script setup lang="ts">
import type { CatalogItem } from "../shared/types";

defineProps<{
  items: CatalogItem[];
  selectedId: string;
}>();

const emit = defineEmits<{
  select: [item: CatalogItem];
}>();
</script>

<template>
  <div class="project-list">
    <button
      v-for="item in items"
      :key="item.id"
      class="project-item"
      :class="{ active: item.id === selectedId }"
      type="button"
      @click="emit('select', item)"
    >
      <div class="project-item-title">
        <span>{{ item.title }}</span>
        <span v-if="item.read" class="project-new-badge">已读</span>
      </div>
      <div class="project-item-meta">
        <span>{{ item.category || "待分类" }}</span>
        <span>·</span>
        <span>{{ item.region || "全国" }}</span>
      </div>
      <div class="catalog-meta">
        <el-tag size="small" effect="plain" :type="item.status === '已分析' ? 'success' : 'warning'">
          {{ item.status || "待分析" }}
        </el-tag>
        <el-tag size="small" effect="plain">{{ item.decision || "未分析" }}</el-tag>
      </div>
    </button>
  </div>
</template>
