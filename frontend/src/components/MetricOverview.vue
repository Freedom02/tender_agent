<script setup lang="ts">
import type { StatItem } from "../app/navigation";

defineProps<{
  items: StatItem[];
}>();
</script>

<template>
  <section class="overview">
    <el-card v-for="stat in items" :key="stat.label" class="metric-card" shadow="never">
      <div class="metric-head">
        <el-icon><component :is="stat.icon" /></el-icon>
        <span>{{ stat.label }}</span>
      </div>
      <strong>{{ stat.value }}</strong>
    </el-card>
  </section>
</template>
