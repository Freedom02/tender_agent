<script setup lang="ts">
import { FolderOpen } from "lucide-vue-next";
import type { LibrarySummary } from "../../shared/types";

defineProps<{
  libraries: LibrarySummary[];
  selectedLibraryKey: string;
}>();

const emit = defineEmits<{
  selectLibrary: [key: string];
}>();
</script>

<template>
  <el-card class="panel library-directory-page" shadow="never">
    <template #header>
      <div class="panel-header">
        <div class="library-heading">
          <el-icon><FolderOpen /></el-icon>
          <span>资料库列表</span>
        </div>
        <el-tag type="info" effect="plain">{{ libraries.length }} 个资料库</el-tag>
      </div>
    </template>

    <div v-if="libraries.length" class="library-directory-grid">
      <button
        v-for="library in libraries"
        :key="library.key"
        type="button"
        class="library-directory-card"
        :class="{ active: library.key === selectedLibraryKey }"
        @click="emit('selectLibrary', library.key)"
      >
        <span class="library-card-icon"><el-icon :size="22"><FolderOpen /></el-icon></span>
        <span class="library-card-content">
          <strong>{{ library.name }}</strong>
          <small>{{ library.description || "企业投标相关资料" }}</small>
        </span>
        <el-tag size="small" effect="plain">{{ library.documentCount }} 份</el-tag>
      </button>
    </div>
    <el-empty v-else description="暂无资料库" />
  </el-card>
</template>
