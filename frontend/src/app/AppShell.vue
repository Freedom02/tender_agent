<script setup lang="ts">
import { RefreshCcw } from "lucide-vue-next";
import type { AppTab, NavigationItem, StatItem } from "./navigation";
import MetricOverview from "../components/MetricOverview.vue";
import ModuleNav from "../components/ModuleNav.vue";

defineProps<{
  activeTab: AppTab;
  activeSectionLabel: string;
  navItems: NavigationItem[];
  stats: StatItem[];
  pageTitle: string;
  pageSubtitle: string;
  projectDecision: string;
  loading: boolean;
}>();

const emit = defineEmits<{
  refresh: [];
  navigate: [tab: AppTab];
}>();
</script>

<template>
  <el-container class="app-shell">
    <el-header class="topbar">
      <div class="brand">
        标智通 v1
        <span>投标业务智能工作台 / Vue3 + TypeScript + Element Plus</span>
      </div>
      <div class="topbar-right">
        <el-tag effect="plain" type="info">{{ activeSectionLabel }}</el-tag>
        <el-button :icon="RefreshCcw" text :loading="loading" @click="emit('refresh')">刷新</el-button>
      </div>
    </el-header>

    <el-container class="shell-body">
      <el-aside class="sidebar" width="300px">
        <section class="sidebar-panel">
          <div class="panel-heading">
            <h2>模块导航</h2>
          </div>
          <ModuleNav :items="navItems" :active-tab="activeTab" @navigate="emit('navigate', $event)" />
        </section>

        <div class="help-note">
          <p>当前版本用于演示招标监控、匹配评估、标书工作台、企业资料和自动化流程。</p>
          <p>真实数据源、评分模型和文件生成能力后续接入对应服务层。</p>
        </div>
      </el-aside>

      <el-main class="workspace">
        <template v-if="activeTab === 'catalog'">
          <section class="page-head">
            <div>
              <p class="page-eyebrow">工作台视图</p>
              <h1>{{ pageTitle }}</h1>
              <p class="page-subtitle">{{ pageSubtitle }}</p>
            </div>
            <div class="project-state">{{ projectDecision }}</div>
          </section>

          <MetricOverview :items="stats" />
        </template>
        <slot />
      </el-main>
    </el-container>
  </el-container>
</template>
