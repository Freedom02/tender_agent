<script setup lang="ts">
import { computed } from "vue";
import type { BidSection } from "../../../shared/types";
import { sectionJobOrigin, summarizeSectionJobs } from "../sectionJobs";
import SectionStatusBadge from "./SectionStatusBadge.vue";

const props = defineProps<{ sections: BidSection[] }>();
const emit = defineEmits<{ "select-section": [sectionId: string] }>();
const jobs = computed(() => summarizeSectionJobs(props.sections));
</script>

<template>
  <el-card class="section-job-panel" shadow="never">
    <template #header>
      <div class="job-header">
        <strong>章节作业</strong>
        <div v-if="jobs.summary.total" class="job-counts" aria-live="polite">
          <span>已完成 {{ jobs.summary.completed }}/{{ jobs.summary.total }}</span>
          <span>待生成 {{ jobs.summary.pending }}</span>
          <span>生成中 {{ jobs.summary.generating }}</span>
          <span class="job-failed">失败 {{ jobs.summary.failed }}</span>
          <span v-if="jobs.summary.review">待复核 {{ jobs.summary.review }}</span>
        </div>
      </div>
    </template>

    <el-empty v-if="!sections.length" description="暂无章节，请先回到第二步生成目录" :image-size="48" />
    <div v-else class="job-columns">
      <div class="job-volumes">
        <h3>按卷完成度</h3>
        <p class="job-hint">按章节状态统计，卷与目录分组不计入。</p>
        <div v-for="volume in jobs.volumes" :key="volume.id" class="job-volume">
          <div class="job-volume-heading">
            <span class="job-volume-title" :title="volume.title">{{ volume.title }}</span>
            <span>{{ volume.completed }}/{{ volume.total }}</span>
          </div>
          <el-progress v-if="volume.total" :percentage="volume.percentage" :stroke-width="6" />
          <p v-else class="job-hint">暂无可撰写章节</p>
          <div v-if="volume.total" class="job-volume-counts">
            <span>待生成 {{ volume.pending }}</span>
            <span>生成中 {{ volume.generating }}</span>
            <span v-if="volume.failed" class="job-failed">失败 {{ volume.failed }}</span>
            <span v-if="volume.review">待复核 {{ volume.review }}</span>
          </div>
        </div>
        <p v-if="!jobs.volumes.length" class="job-hint">暂无可撰写章节</p>
      </div>

      <div class="job-todos">
        <h3>章节待办 <span class="job-hint">{{ jobs.todos.length }} 项</span></h3>
        <p v-if="jobs.todos.length" class="job-hint">点击章节进入正文编辑，失败章节可使用原有撰写或填充按钮重试。</p>
        <ul v-if="jobs.todos.length" class="job-todo-list">
          <li v-for="todo in jobs.todos" :key="todo.section.sectionId">
            <button
              type="button"
              class="job-todo-button"
              :class="{ 'is-failed': todo.state === 'failed' }"
              :aria-label="`前往${todo.section.title}，${todo.state === 'failed' ? '失败可重试' : todo.state === 'review' ? '待复核' : '待生成'}`"
              @click="emit('select-section', todo.section.sectionId)"
            >
              <span class="job-todo-copy">
                <span class="job-todo-title">{{ todo.section.title }}</span>
                <span class="job-todo-meta" :title="todo.section.derivedBasis">
                  {{ todo.volumeTitle }} · {{ sectionJobOrigin(todo.section) }}
                </span>
              </span>
              <SectionStatusBadge :section="todo.section" />
              <span class="job-todo-link">前往章节 →</span>
            </button>
          </li>
        </ul>
        <p v-else-if="!jobs.summary.total" class="job-hint">目录中只有分组，请先添加正文章节。</p>
        <p v-else-if="jobs.summary.generating" class="job-hint" role="status">
          {{ jobs.summary.generating }} 个章节正在生成，请等待完成。
        </p>
        <p v-else class="job-hint" role="status">所有章节已完成，暂无章节待办。</p>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.section-job-panel {
  margin-bottom: 16px;
}
.job-header,
.job-counts,
.job-volume-heading,
.job-volume-counts {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px 16px;
}
.job-header {
  justify-content: space-between;
  font-size: 13px;
}
.job-counts,
.job-volume-counts {
  font-size: 12px;
  color: var(--el-text-color-regular);
}
.job-columns {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 2fr);
  gap: 24px;
}
.job-volumes,
.job-todos {
  min-width: 0;
}
h3 {
  margin: 0 0 8px;
  color: var(--el-text-color-primary);
  font-size: 13px;
}
.job-hint {
  margin: 6px 0;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.6;
}
.job-volume {
  margin-top: 12px;
}
.job-volume-heading {
  justify-content: space-between;
  flex-wrap: nowrap;
  margin-bottom: 6px;
  font-size: 12px;
}
.job-volume-title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.job-volume-counts {
  gap: 4px 12px;
  margin-top: 4px;
}
.job-failed {
  color: var(--el-color-danger);
}
.job-todo-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 240px;
  overflow-y: auto;
  margin: 8px 0 0;
  padding: 2px;
  list-style: none;
}
.job-todo-button {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  background: var(--el-fill-color-blank);
  color: var(--el-text-color-primary);
  text-align: left;
  font: inherit;
  cursor: pointer;
}
.job-todo-button:hover,
.job-todo-button:focus-visible {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}
.job-todo-button:focus-visible {
  outline: 2px solid var(--el-color-primary);
  outline-offset: 1px;
}
.job-todo-button.is-failed {
  border-left: 3px solid var(--el-color-danger);
}
.job-todo-copy {
  display: flex;
  flex: 1;
  min-width: 0;
  flex-direction: column;
  gap: 4px;
}
.job-todo-title,
.job-todo-meta {
  overflow-wrap: anywhere;
  font-size: 12px;
}
.job-todo-meta {
  color: var(--el-text-color-secondary);
}
.job-todo-link {
  flex-shrink: 0;
  color: var(--el-color-primary);
  font-size: 12px;
}
@media (max-width: 720px) {
  .job-columns {
    grid-template-columns: minmax(0, 1fr);
    gap: 16px;
  }
  .job-todo-button {
    flex-wrap: wrap;
  }
  .job-todo-copy {
    flex-basis: calc(100% - 110px);
  }
}
</style>
