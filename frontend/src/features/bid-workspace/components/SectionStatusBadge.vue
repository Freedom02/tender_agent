<script setup lang="ts">
import { computed } from "vue";
import type { BidSection } from "../../../shared/types";
import { SECTION_JOB_BADGES, sectionJobState } from "../sectionJobs";

const props = defineProps<{ section: BidSection }>();
const badge = computed(() => SECTION_JOB_BADGES[sectionJobState(props.section)]);
</script>

<template>
  <el-tag class="section-status-badge" size="small" :type="badge.type" effect="plain">
    {{ badge.label }}
  </el-tag>
</template>

<style scoped>
.section-status-badge {
  flex-shrink: 0;
}
</style>
