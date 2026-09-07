<script setup lang="ts">
import type { AppTab, NavigationItem } from "../app/navigation";

defineProps<{
  items: NavigationItem[];
  activeTab: AppTab;
}>();

const emit = defineEmits<{
  navigate: [tab: AppTab];
}>();
</script>

<template>
  <div class="nav-list">
    <button
      v-for="item in items"
      :key="item.key"
      type="button"
      class="nav-link"
      :class="{ active: activeTab === item.key }"
      @click="emit('navigate', item.key)"
    >
      <span class="nav-label">
        <el-icon><component :is="item.icon" /></el-icon>
        {{ item.label }}
      </span>
      <el-tag size="small" effect="plain">{{ item.count }}</el-tag>
    </button>
  </div>
</template>
