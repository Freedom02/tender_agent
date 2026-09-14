<script setup lang="ts">
import { Bell, Check, Flag, Radar, Search } from "lucide-vue-next";
import type { CatalogItem } from "../../shared/types";

defineProps<{
  catalog: CatalogItem[];
  busy: boolean;
  searchForm: {
    query: string;
    region: string;
    type: string;
    currentOnly: boolean;
  };
}>();

const emit = defineEmits<{
  search: [];
  runMonitoring: [];
  remind: [item: CatalogItem];
  followUp: [item: CatalogItem];
  selectProject: [item: CatalogItem];
}>();

function displayTime(value?: string) {
  const text = String(value || "").trim();
  if (!text || text.length > 64 || !/20\d{2}[-年./]\d{1,2}[-月./]\d{1,2}/.test(text)) {
    return "待核验";
  }
  return text;
}
</script>

<template>
  <el-row :gutter="16">
    <el-col :span="24">
      <el-card class="panel" shadow="never">
        <template #header>
          <div class="panel-header">
            <span>项目列表</span>
            <div class="catalog-actions">
              <el-button :icon="Search" :loading="busy" plain @click="emit('search')">检索</el-button>
              <el-button :icon="Radar" :loading="busy" type="primary" @click="emit('runMonitoring')">立即抓取</el-button>
            </div>
          </div>
        </template>

        <el-row :gutter="12" class="search-row">
          <el-col :xs="24" :md="8"><el-input v-model="searchForm.query" placeholder="搜索关键字" /></el-col>
          <el-col :xs="24" :md="6"><el-input v-model="searchForm.region" placeholder="地区" /></el-col>
          <el-col :xs="24" :md="6"><el-input v-model="searchForm.type" placeholder="类型" /></el-col>
          <el-col :xs="24" :md="4"><el-switch v-model="searchForm.currentOnly" active-text="仅当前" /></el-col>
        </el-row>

        <el-table
          class="tender-monitor-table"
          :data="catalog"
          height="560"
          highlight-current-row
          @row-click="emit('selectProject', $event)"
        >
          <el-table-column label="项目名称" width="300">
            <template #default="{ row }">
              <a
                v-if="row.sourceUrl"
                class="project-link"
                :href="row.sourceUrl"
                target="_blank"
                rel="noopener noreferrer"
                @click.stop="emit('selectProject', row)"
              >
                {{ row.title }}
              </a>
              <button v-else type="button" class="project-link project-link-button" @click.stop="emit('selectProject', row)">
                {{ row.title }}
              </button>
            </template>
          </el-table-column>
          <el-table-column prop="category" label="类型" width="100" />
          <el-table-column label="相关性分" width="82">
            <template #default="{ row }">
              <el-popover placement="top" width="360" trigger="hover">
                <template #reference>
                  <el-tag :type="row.relevanceScore >= 70 ? 'success' : row.relevanceScore >= 45 ? 'warning' : 'info'" effect="plain">
                    {{ row.relevanceScore ?? 0 }}
                  </el-tag>
                </template>
                <div class="score-popover">
                  <div class="score-popover-title">相关性评分 {{ row.relevanceScore ?? 0 }} 分</div>
                  <div v-if="row.scoreVersion" class="score-popover-version">规则：{{ row.scoreVersion }}</div>
                  <div v-if="row.scoreBreakdown?.dimensions" class="score-popover-breakdown">
                    <span>业务方向 {{ row.scoreBreakdown.dimensions.businessDirection?.points ?? 0 }}</span>
                    <span>行业场景 {{ row.scoreBreakdown.dimensions.industryScenario?.points ?? 0 }}</span>
                    <span>产品服务 {{ row.scoreBreakdown.dimensions.product?.points ?? 0 }}</span>
                    <span>技术能力 {{ row.scoreBreakdown.dimensions.capability?.points ?? 0 }}</span>
                    <span>搜索词 {{ row.scoreBreakdown.dimensions.configuredKeywords?.points ?? 0 }}</span>
                  </div>
                  <div v-if="row.matchReasons?.length" class="score-popover-section">
                    <div v-for="reason in row.matchReasons" :key="reason">{{ reason }}</div>
                  </div>
                  <div v-if="row.matchEvidence?.length" class="score-popover-section">
                    <div v-for="evidence in row.matchEvidence" :key="evidence">{{ evidence }}</div>
                  </div>
                  <div v-if="row.excludeReason" class="score-popover-warning">{{ row.excludeReason }}</div>
                  <div v-if="row.nextStage" class="score-popover-stage">下一阶段：{{ row.nextStage }}</div>
                </div>
              </el-popover>
            </template>
          </el-table-column>
          <el-table-column prop="region" label="地区" width="96" />
          <el-table-column prop="publishedDate" label="发布日期" width="108" />
          <el-table-column label="获取文件时间" width="172">
            <template #default="{ row }">
              {{ displayTime(row.registrationPeriod) }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="96">
            <template #default="{ row }">
              <el-button
                size="small"
                :icon="row.reminderStatus === '已提醒' ? Check : Bell"
                :type="row.reminderStatus === '已提醒' ? 'success' : 'warning'"
                plain
                @click.stop="emit('remind', row)"
              >
                {{ row.reminderStatus === "已提醒" ? "已提醒" : "未提醒" }}
              </el-button>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="96" fixed="right">
            <template #default="{ row }">
              <el-button
                size="small"
                :icon="Flag"
                :type="row.followUp ? 'success' : 'default'"
                plain
                @click.stop="emit('followUp', row)"
              >
                {{ row.followUp ? "跟进中" : "跟进" }}
              </el-button>
            </template>
          </el-table-column>
        </el-table>
      </el-card>
    </el-col>
  </el-row>
</template>

<style scoped>
:deep(.tender-monitor-table .cell) {
  padding-right: 6px;
  padding-left: 6px;
  white-space: normal;
  word-break: break-word;
  line-height: 1.5;
}

:deep(.tender-monitor-table .el-button--small) {
  padding-right: 7px;
  padding-left: 7px;
}
</style>
