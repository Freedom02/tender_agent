<script setup lang="ts">
import { computed } from "vue";
import { AlertTriangle, FileCheck2, Loader2 } from "lucide-vue-next";
import type { Workspace } from "../../shared/types";

const props = defineProps<{
  modelValue: boolean;
  workspace: Workspace | null;
  busy: boolean;
  title?: string;
}>();

const emit = defineEmits<{
  "update:modelValue": [value: boolean];
}>();

const eligibility = computed(() => props.workspace?.reasoning?.eligibility);
const rows = computed(() => props.workspace?.reasoning?.rows || []);
const needs = computed(() => props.workspace?.reasoning?.procurementNeeds || []);
const risks = computed(() => props.workspace?.reasoning?.risks || []);
const hardBlockers = computed(() => eligibility.value?.hardBlockers || []);
const projectInfo = computed(() => props.workspace?.reasoning?.project || {});

const score = computed(() =>
  eligibility.value?.assessmentAvailable === false
    ? "--"
    : eligibility.value?.eligibilityScore ?? props.workspace?.reasoning?.score ?? "-"
);
const scoreLabel = computed(() =>
  eligibility.value?.assessmentAvailable === false ? "资料不足" : "资格匹配分"
);
const recommendation = computed(
  () => eligibility.value?.recommendation || props.workspace?.reasoning?.decision || ""
);
const shouldGet = computed(() =>
  eligibility.value?.shouldGetTenderDocument ?? (props.workspace?.reasoning?.score ?? 0) >= 70
);
const relevanceScore = computed(
  () => props.workspace?.reasoning?.relevance?.score ?? props.workspace?.project?.relevanceScore ?? "-"
);
const extraction = computed(() => props.workspace?.reasoning?.extraction);

function statusType(status?: string) {
  const s = status || "";
  if (["满足", "已满足", "已关联", "通过"].includes(s)) return "success";
  if (["部分满足", "部分关联"].includes(s)) return "warning";
  if (["不满足", "未满足"].includes(s)) return "danger";
  return "info";
}

function needLabel(category?: string) {
  const map: Record<string, string> = {
    goods: "货物类",
    service: "服务类",
    project: "工程类",
    qualification: "资质要求",
    personnel: "人员要求",
    other: "其他",
  };
  return category ? map[category] || category : "采购需求";
}
</script>

<template>
  <el-drawer
    :model-value="modelValue"
    @update:model-value="emit('update:modelValue', $event)"
    size="640px"
    :title="title || '公告预审结果'"
    destroy-on-close
  >
    <!-- 分析中 -->
    <div v-if="busy && !workspace" class="review-state">
      <el-icon class="is-loading review-loading-icon"><Loader2 /></el-icon>
      <p>正在解析公告并匹配企业资格…</p>
    </div>

    <!-- 无结果 -->
    <div v-else-if="!workspace" class="review-state">
      <el-icon class="review-empty-icon"><FileCheck2 /></el-icon>
      <p>暂无预审结果，请稍后重试。</p>
    </div>

    <!-- 结果 -->
    <div v-else class="review-body">
      <div class="score-overview">
        <div class="score-ring">
          <strong>{{ score }}</strong>
          <span>{{ scoreLabel }}</span>
        </div>
        <div class="score-summary">
          <p class="result-project">{{ workspace.title }}</p>
          <el-tag :type="shouldGet ? 'success' : 'warning'" effect="light">
            {{ recommendation }}
          </el-tag>
          <p class="form-hint">公告资格粗匹配；项目相关性 {{ relevanceScore }} 分。</p>
          <div class="catalog-meta">
            <el-tag size="small" effect="plain">
              {{ extraction?.method?.includes("llm") ? "大模型 + 规则校验" : "规则抽取" }}
            </el-tag>
            <el-tag v-if="extraction?.modelUsed" size="small" effect="plain">
              {{ extraction.modelUsed }}
            </el-tag>
          </div>
        </div>
      </div>

      <el-alert
        v-if="extraction?.warning"
        :title="extraction.warning"
        type="warning"
        show-icon
        :closable="false"
        class="mt-12"
      />

      <!-- 硬性否决项 -->
      <div v-if="hardBlockers.length" class="section-block mt-12">
        <div class="section-heading">
          <el-icon class="danger-text"><AlertTriangle /></el-icon>
          <strong>硬性否决项</strong>
          <span>{{ hardBlockers.length }} 项</span>
        </div>
        <el-card
          v-for="blocker in hardBlockers"
          :key="blocker.requirementId"
          shadow="never"
          class="blocker-card"
        >
          <strong>{{ blocker.title }}</strong>
          <p>{{ blocker.detail }}</p>
        </el-card>
      </div>

      <!-- 项目基础信息 -->
      <div v-if="Object.keys(projectInfo).length" class="section-block mt-12">
        <div class="section-heading">
          <strong>项目基础信息</strong>
          <span>基础信息不参与企业资格匹配</span>
        </div>
        <div class="project-info-grid">
          <div v-for="(value, key) in projectInfo" :key="key" class="project-info-item">
            <span>{{ key }}</span>
            <strong>{{ value }}</strong>
          </div>
        </div>
      </div>

      <!-- 采购需求提取 -->
      <div v-if="needs.length" class="section-block mt-12">
        <div class="section-heading">
          <strong>采购需求提取</strong>
          <span>采购内容单独提取，不参与资格准入评分</span>
        </div>
        <div class="procurement-needs-list">
          <div v-for="need in needs" :key="need.id" class="procurement-need-item">
            <div class="requirement-item-head">
              <el-tag size="small" effect="plain">{{ needLabel(need.category) }}</el-tag>
              <el-tag size="small" :type="need.status === '已关联' ? 'success' : 'warning'" effect="plain">
                {{ need.status || "待关联" }}
              </el-tag>
            </div>
            <p class="requirement-text">{{ need.text }}</p>
          </div>
        </div>
      </div>

      <!-- 资格条件智能筛查 -->
      <div class="section-block mt-12">
        <div class="section-heading">
          <strong>资格条件智能筛查</strong>
          <span>自动比对招标要求与企业资质</span>
        </div>
        <el-table v-if="rows.length" :data="rows" size="small" class="review-table">
          <el-table-column label="状态" width="92">
            <template #default="{ row }">
              <el-tag size="small" :type="statusType(row.status)" effect="light">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="准入条款" min-width="200">
            <template #default="{ row }">
              <div class="requirement-cell">
                <el-tag v-if="row.kind" size="small" effect="plain" class="kind-tag">{{ row.kind }}</el-tag>
                <span>{{ row.requirement }}</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column label="企业能力/证据" min-width="160">
            <template #default="{ row }">
              <span class="form-hint">{{ row.evidence || "—" }}</span>
            </template>
          </el-table-column>
          <el-table-column label="建议" min-width="150">
            <template #default="{ row }">
              {{ row.suggestion || row.risk || "—" }}
            </template>
          </el-table-column>
        </el-table>
        <el-empty
          v-else
          class="qualification-empty"
          description="当前内容未识别到可用于资格匹配的准入条款"
        />
      </div>

      <!-- 风险提示 -->
      <div v-if="risks.length" class="risk-list mt-12">
        <el-card v-for="(risk, index) in risks" :key="index" shadow="never" class="risk-card">
          <strong>{{ risk.title }}</strong>
          <p>{{ risk.detail }}</p>
        </el-card>
      </div>
    </div>
  </el-drawer>
</template>

<style scoped>
.review-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 80px 20px;
  color: var(--el-text-color-secondary);
}
.review-loading-icon,
.review-empty-icon {
  font-size: 40px;
}
.review-loading-icon {
  color: var(--el-color-primary);
}
.review-empty-icon {
  color: var(--el-text-color-placeholder);
}

.review-body {
  padding-bottom: 24px;
}

.score-overview {
  display: flex;
  gap: 20px;
  align-items: center;
  padding: 16px;
  background: var(--el-fill-color-light);
  border-radius: 8px;
}
.score-ring {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 96px;
  height: 96px;
  border-radius: 50%;
  border: 4px solid var(--el-color-primary);
  color: var(--el-color-primary);
}
.score-ring strong {
  font-size: 28px;
  line-height: 1;
}
.score-ring span {
  font-size: 12px;
  margin-top: 4px;
  color: var(--el-text-color-secondary);
}
.score-summary {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.result-project {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
}
.form-hint {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.catalog-meta {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.mt-12 {
  margin-top: 12px;
}

.section-block {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.section-heading {
  display: flex;
  align-items: center;
  gap: 8px;
}
.section-heading strong {
  font-size: 15px;
}
.section-heading span {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  margin-left: auto;
}
.danger-text {
  color: var(--el-color-danger);
}

.blocker-card {
  border-left: 3px solid var(--el-color-danger);
}
.blocker-card strong {
  color: var(--el-color-danger);
}
.blocker-card p {
  margin: 6px 0 0;
  color: var(--el-text-color-regular);
  font-size: 13px;
}

.project-info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
}
.project-info-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  background: var(--el-fill-color-light);
  border-radius: 6px;
}
.project-info-item span {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.project-info-item strong {
  font-size: 13px;
  font-weight: 500;
  word-break: break-word;
}

.procurement-needs-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.procurement-need-item {
  padding: 10px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}
.requirement-item-head {
  display: flex;
  gap: 6px;
  align-items: center;
}
.requirement-text {
  margin: 8px 0 0;
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.6;
}

.review-table {
  width: 100%;
}
.requirement-cell {
  display: flex;
  flex-direction: column;
  gap: 4px;
  align-items: flex-start;
}
.kind-tag {
  flex: 0 0 auto;
}
.qualification-empty {
  padding: 20px 0;
}

.risk-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.risk-card {
  border-left: 3px solid var(--el-color-warning);
}
.risk-card strong {
  color: var(--el-color-warning);
}
.risk-card p {
  margin: 6px 0 0;
  color: var(--el-text-color-regular);
  font-size: 13px;
}
</style>
