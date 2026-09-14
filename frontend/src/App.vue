<script setup lang="ts">
import { onMounted } from "vue";
import AppShell from "./app/AppShell.vue";
import { useAppState } from "./app/app-state";
import type { AppTab } from "./app/navigation";
import type { CatalogItem, Workspace } from "./shared/types";
import TenderMonitorPage from "./features/tender-monitor/TenderMonitorPage.vue";
import FollowUpPage from "./features/tender-monitor/FollowUpPage.vue";
import MatchingPage from "./features/tender-matching/MatchingPage.vue";
import BidWorkspacePage from "./features/bid-workspace/BidWorkspacePage.vue";
import BidGeneratorPage from "./features/bid-workspace/BidGeneratorPage.vue";
import MaterialsPage from "./features/document-library/MaterialsPage.vue";
import AutomationPage from "./features/automation/AutomationPage.vue";

const {
  loading,
  busy,
  activeTab,
  catalog,
  libraries,
  workspaces,
  reminders,
  automation,
  config,
  monitoring,
  configText,
  selectedProjectId,
  selectedWorkspaceId,
  followUpStatus,
  importFile,
  importKind,
  searchForm,
  importForm,
  libraryForm,
  selectedProject,
  selectedWorkspace,
  materialsProfile,
  activeSectionLabel,
  navItems,
  stats,
  loadBootstrap,
  runSearch,
  analyzeProject,
  updateFineReviewStatus,
  markSelectedRead,
  saveFollowUp,
  toggleProjectReminder,
  saveProjectFollowUp,
  downloadProject,
  generateResponse,
  downloadDraft,
  importDocument,
  saveDraft,
  saveConfig,
  testAiConnection,
  runAutomation,
  runMonitoring,
  updateMonitorSite,
  createMonitorSite,
  updateMonitorSiteConfig,
  deleteMonitorSite,
  testMonitorSite,
  testInAppNotification,
  testWecom,
  selectProject
} = useAppState();

function pageTitle() {
  if (activeTab.value === "catalog") return "实时抓取监控";
  if (activeTab.value === "follow-up") return "跟进项目";
  if (activeTab.value === "generator") return "标书生成";
  if (activeTab.value === "materials") return "企业材料";
  return selectedProject.value?.title || "请选择一个项目";
}

function pageSubtitle() {
  if (activeTab.value === "catalog") return "24小时实时监控招标公告，每30分钟自动抓取一次。";
  if (activeTab.value === "follow-up") return "集中查看已标记跟进的招标项目，及时核对报名和响应文件截止时间。";
  if (activeTab.value === "generator") return "预览并编辑标书草稿，生成文档后下载响应文件。";
  if (activeTab.value === "materials") return "集中管理企业资质、业绩、人员等投标资料，支持导入模板包或逐条维护。";
  if (selectedProject.value?.analysisPreview) return selectedProject.value.analysisPreview;
  if (activeTab.value === "matching") return "输入招标文件开始评估，或从右侧历史项目查看已保存的匹配结果。";
  if (activeTab.value === "workspace") return "维护响应文件草稿并导出响应包。";
  return "从左侧选择一个项目，或进入匹配评估开始分析。";
}

function navigate(tab: AppTab) {
  activeTab.value = tab;
}

function openWorkspace() {
  activeTab.value = "workspace";
}

function openGenerator() {
  activeTab.value = "generator";
}

function backToWorkspace() {
  activeTab.value = "workspace";
}

function updateDraft(value: string) {
  if (selectedWorkspace.value) selectedWorkspace.value.draft = value;
}

async function enterMatching(item: CatalogItem) {
  selectProject(item);
  importForm.name = item.title || "";
  importForm.text = item.analysisText || "";
  await analyzeProject(item.id);
  const refreshed = catalog.value.find((entry) => entry.id === item.id);
  if (refreshed?.analysisText) importForm.text = refreshed.analysisText;
}

function selectMatchingWorkspace(workspace: Workspace) {
  selectedWorkspaceId.value = workspace.id;
  selectedProjectId.value = workspace.projectId;
  importKind.value = workspace.reasoning.fineReview ? "tender_document" : "notice";
}

onMounted(loadBootstrap);
</script>

<template>
  <AppShell
    :active-tab="activeTab"
    :active-section-label="activeTab === 'generator' ? '标书生成' : activeSectionLabel"
    :nav-items="navItems"
    :stats="stats"
    :page-title="pageTitle()"
    :page-subtitle="pageSubtitle()"
    :project-decision="activeTab === 'catalog' ? '监控中' : activeTab === 'generator' ? '编辑中' : selectedProject?.decision || '待分析'"
    :loading="loading"
    @refresh="loadBootstrap"
    @navigate="navigate"
  >
    <TenderMonitorPage
      v-if="activeTab === 'catalog'"
      :catalog="catalog"
      :busy="busy"
      :search-form="searchForm"
      @search="runSearch"
      @run-monitoring="runMonitoring()"
      @remind="toggleProjectReminder($event.id)"
      @follow-up="saveProjectFollowUp"
      @select-project="selectProject"
    />

    <FollowUpPage
      v-else-if="activeTab === 'follow-up'"
      :items="catalog.filter((item) => item.followUp)"
      @remove-follow-up="saveProjectFollowUp"
      @enter-matching="enterMatching($event)"
    />

    <MatchingPage
      v-else-if="activeTab === 'matching'"
      :workspace="selectedWorkspace"
      :workspaces="workspaces"
      :import-form="importForm"
      :import-file="importFile"
      :input-kind="importKind"
      :busy="busy"
      @evaluate="importDocument"
      @file-selected="importFile = $event"
      @update:input-kind="importKind = $event"
      @update-fine-status="updateFineReviewStatus($event.rowId, $event.status)"
      @open-workspace="openWorkspace"
      @select-workspace="selectMatchingWorkspace"
    />

    <BidWorkspacePage
      v-else-if="activeTab === 'workspace'"
      :workspace="selectedWorkspace"
      :project="selectedProject"
      :libraries="libraries"
      :selected-library-key="libraryForm.key"
      :materials-profile="materialsProfile"
      :busy="busy"
      @generate="openGenerator"
      @update:selected-library-key="libraryForm.key = $event"
    />

    <BidGeneratorPage
      v-else-if="activeTab === 'generator'"
      :workspace="selectedWorkspace"
      :project="selectedProject"
      :busy="busy"
      @back="backToWorkspace"
      @save-draft="saveDraft"
      @generate-document="generateResponse()"
      @download-document="downloadDraft"
      @update:draft="updateDraft"
    />

    <MaterialsPage v-else-if="activeTab === 'materials'" />

    <AutomationPage
      v-else
      :search-form="searchForm"
      :config="config"
      :monitoring="monitoring"
      :busy="busy"
      @save-config="saveConfig($event)"
      @run-site="runMonitoring($event)"
      @update-site="updateMonitorSite($event.id, $event.enabled)"
      @create-site="createMonitorSite"
      @update-site-config="updateMonitorSiteConfig($event.id, $event.payload)"
      @delete-site="deleteMonitorSite"
      @test-site="testMonitorSite"
      @test-in-app="testInAppNotification"
      @test-wecom="testWecom"
      @test-ai="testAiConnection"
    />
  </AppShell>
</template>
