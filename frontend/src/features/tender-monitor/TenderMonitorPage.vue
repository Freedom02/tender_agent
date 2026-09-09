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

        <el-table :data="catalog" height="560" highlight-current-row @row-click="emit('selectProject', $event)">
          <el-table-column label="项目名称" min-width="340">
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
          <el-table-column prop="category" label="类型" width="120" />
          <el-table-column label="相关性分" width="100">
            <template #default="{ row }">
              <el-tag :type="row.relevanceScore >= 70 ? 'success' : row.relevanceScore >= 45 ? 'warning' : 'info'" effect="plain">
                {{ row.relevanceScore ?? 0 }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="匹配业务" width="140">
            <template #default="{ row }">
              {{ row.matchedBusinessDirections?.[0] || row.searchProfile?.serviceType || row.matchedKeywords?.[0] || "未命中" }}
            </template>
          </el-table-column>
          <el-table-column label="本体命中" min-width="260">
            <template #default="{ row }">
              <div class="ontology-hit-list">
                <el-tag v-for="item in (row.matchedProducts || []).slice(0, 2)" :key="`product-${item}`" size="small" effect="plain">{{ item }}</el-tag>
                <el-tag v-for="item in (row.matchedScenarios || []).slice(0, 2)" :key="`scenario-${item}`" size="small" type="success" effect="plain">{{ item }}</el-tag>
                <el-tag v-for="item in (row.matchedCapabilities || []).slice(0, 1)" :key="`capability-${item}`" size="small" type="warning" effect="plain">{{ item }}</el-tag>
                <span v-if="!row.matchedProducts?.length && !row.matchedScenarios?.length && !row.matchedCapabilities?.length">待人工复核</span>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="region" label="地区" width="120" />
          <el-table-column prop="publishedDate" label="发布日期" width="120" />
          <el-table-column label="下一步" width="150">
            <template #default="{ row }">
              <el-tooltip v-if="row.matchReasons?.length" :content="row.matchReasons.join('；')" placement="top">
                <el-tag :type="row.shouldAnalyze ? 'success' : row.nextStage === '人工复核' ? 'warning' : 'info'" effect="plain">
                  {{ row.nextStage || "待评估" }}
                </el-tag>
              </el-tooltip>
              <el-tag v-else type="info" effect="plain">{{ row.nextStage || "待评估" }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="获取文件时间" min-width="180">
            <template #default="{ row }">
              {{ displayTime(row.registrationPeriod || row.registrationDeadline) }}
            </template>
          </el-table-column>
          <el-table-column label="提交截止" min-width="160">
            <template #default="{ row }">
              {{ displayTime(row.bidDeadline) }}
            </template>
          </el-table-column>
          <el-table-column label="状态" width="130">
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
          <el-table-column label="操作" width="110" fixed="right">
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
