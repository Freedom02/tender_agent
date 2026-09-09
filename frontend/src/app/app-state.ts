import { computed, reactive, ref, watch } from "vue";
import { ElMessage, ElNotification } from "element-plus";
import { Clock3, Database, FileCheck2, Radar } from "lucide-vue-next";
import { api } from "../services/api";
import { fileToBase64 } from "../shared/file-utils";
import type { AppTab, StatItem } from "./navigation";
import { createNavigationItems } from "./navigation";
import type {
  BootstrapResponse,
  CatalogItem,
  CompanyKnowledge,
  KnowledgeObjectDetail,
  LibrarySummary,
  MonitoringStatus,
  Workspace
} from "../shared/types";

export function useAppState() {
  const loading = ref(false);
  const busy = ref(false);
  const activeTab = ref<AppTab>("catalog");
  const bootstrap = ref<BootstrapResponse | null>(null);
  const company = ref<Record<string, unknown>>({});
  const companyKnowledge = ref<CompanyKnowledge | null>(null);
  const selectedKnowledgeDetail = ref<KnowledgeObjectDetail | null>(null);
  const knowledgeDetailLoading = ref(false);
  const catalog = ref<CatalogItem[]>([]);
  const libraries = ref<LibrarySummary[]>([]);
  const workspaces = ref<Workspace[]>([]);
  const reminders = ref<Record<string, unknown>[]>([]);
  const automation = ref<Record<string, any>>({});
  const config = ref<Record<string, any>>({});
  const monitoring = ref<MonitoringStatus>({
    enabled: true,
    frequencyMinutes: 30,
    sites: [],
    stats: { latestCount: 0, totalCollected: 0, lastFetchedAt: "", nextFetchedAt: "" }
  });
  const configText = ref("{}");
  const selectedProjectId = ref("");
  const selectedWorkspaceId = ref("");
  const followUpStatus = ref("");
  const importFile = ref<File | null>(null);
  const importKind = ref<"notice" | "tender_document">("notice");
  const libraryFile = ref<File | null>(null);

  const searchForm = reactive({
    query: "",
    region: "",
    type: "",
    currentOnly: false
  });

  const importForm = reactive({
    name: "",
    text: ""
  });

  const libraryForm = reactive({
    key: "company",
    name: "",
    text: ""
  });

  const selectedProject = computed(
    () => catalog.value.find((item) => item.id === selectedProjectId.value) || null
  );
  const selectedWorkspace = computed(
    () => workspaces.value.find((item) => item.id === selectedWorkspaceId.value) || null
  );
  const selectedLibrary = computed(
    () => libraries.value.find((item) => item.key === libraryForm.key) || libraries.value[0] || null
  );
  const activeSectionLabel = computed(
    () =>
      createNavigationItems({
        catalog: catalog.value.length,
        "follow-up": catalog.value.filter((item) => item.followUp).length,
        matching: workspaces.value.length,
        workspace: workspaces.value.length,
        generator: workspaces.value.length,
        "library-documents": libraries.value.reduce((sum, item) => sum + (item.documentCount || 0), 0),
        libraries: libraries.value.reduce((sum, item) => sum + (item.documentCount || 0), 0),
        config: reminders.value.length
      }).find((item) => item.key === activeTab.value)?.label || "招标项目"
  );
  const navItems = computed(() =>
    createNavigationItems({
      catalog: catalog.value.length,
      "follow-up": catalog.value.filter((item) => item.followUp).length,
      matching: workspaces.value.length,
      workspace: workspaces.value.length,
      generator: workspaces.value.length,
      "library-documents": libraries.value.reduce((sum, item) => sum + (item.documentCount || 0), 0),
      libraries: libraries.value.reduce((sum, item) => sum + (item.documentCount || 0), 0),
      config: reminders.value.length
    })
  );
  const stats = computed<StatItem[]>(() => {
    const values = monitoring.value.stats;

    return [
      { label: "最新抓取", value: `${values.latestCount || 0} 条`, icon: Radar },
      { label: "累计抓取", value: `${values.totalCollected || 0} 条`, icon: Database },
      { label: "上次抓取", value: values.lastFetchedAt || "等待首次抓取", icon: FileCheck2 },
      { label: "下次抓取", value: values.nextFetchedAt || "等待首次抓取", icon: Clock3 }
    ];
  });

  function syncState(data: BootstrapResponse) {
    bootstrap.value = data;
    company.value = data.company || {};
    companyKnowledge.value = data.companyKnowledge || null;
    catalog.value = data.catalog || [];
    libraries.value = data.libraries || [];
    workspaces.value = data.workspaces || [];
    reminders.value = data.reminders || [];
    automation.value = data.automation || {};
    config.value = data.config || {};
    monitoring.value = data.monitoring || monitoring.value;
    configText.value = JSON.stringify(config.value, null, 2);

    if (!selectedProjectId.value && catalog.value.length) {
      selectedProjectId.value = catalog.value[0].id;
    }
    if (!selectedWorkspaceId.value && workspaces.value[0]) {
      selectedWorkspaceId.value = workspaces.value[0].id;
    }
    if (!libraries.value.some((item) => item.key === libraryForm.key) && libraries.value[0]) {
      libraryForm.key = libraries.value[0].key;
    }
    followUpStatus.value = selectedProject.value?.followUpStatus || "";
  }

  async function loadBootstrap() {
    loading.value = true;
    try {
      syncState(await api.bootstrap());
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      loading.value = false;
    }
  }

  async function runSearch() {
    busy.value = true;
    try {
      const result = await api.search({ ...searchForm });
      catalog.value = result.items;
      if (!catalog.value.some((item) => item.id === selectedProjectId.value)) {
        selectedProjectId.value = catalog.value[0]?.id || "";
      }
      ElMessage.success("检索完成");
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function analyzeProject(projectId = selectedProjectId.value) {
    if (!projectId) return;
    busy.value = true;
    try {
      const result = await api.analyze(projectId);
      const index = catalog.value.findIndex((item) => item.id === projectId);
      if (index >= 0) {
        catalog.value[index] = { ...catalog.value[index], ...result.item, workspaceId: result.workspace.id };
      }
      const existing = workspaces.value.findIndex((item) => item.id === result.workspace.id);
      if (existing >= 0) workspaces.value[existing] = result.workspace;
      else workspaces.value.unshift(result.workspace);
      selectedWorkspaceId.value = result.workspace.id;
      activeTab.value = "matching";
      ElMessage.success("匹配分析完成");
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function markSelectedRead() {
    if (!selectedProjectId.value) return;
    try {
      const result = await api.markRead(selectedProjectId.value);
      const index = catalog.value.findIndex((item) => item.id === selectedProjectId.value);
      if (index >= 0) catalog.value[index] = { ...catalog.value[index], ...result.item };
      ElMessage.success("已标记为已读");
    } catch (error) {
      ElMessage.error((error as Error).message);
    }
  }

  async function saveFollowUp(followUp = true) {
    if (!selectedProjectId.value) return;
    try {
      const result = await api.setFollowUp(selectedProjectId.value, followUp, followUpStatus.value);
      const index = catalog.value.findIndex((item) => item.id === selectedProjectId.value);
      if (index >= 0) catalog.value[index] = { ...catalog.value[index], ...result.item };
      ElMessage.success(followUp ? "已设置跟进" : "已取消跟进");
    } catch (error) {
      ElMessage.error((error as Error).message);
    }
  }

  async function toggleProjectReminder(projectId: string) {
    try {
      const result = await api.toggleReminder(projectId);
      const index = catalog.value.findIndex((item) => item.id === projectId);
      if (index >= 0) catalog.value[index] = { ...catalog.value[index], ...result.item };
      reminders.value = result.reminders;
      ElMessage.success(result.item.reminderStatus === "已提醒" ? "已标记为已提醒" : "已取消提醒");
    } catch (error) {
      ElMessage.error((error as Error).message);
    }
  }

  async function saveProjectFollowUp(project: CatalogItem) {
    try {
      const result = await api.setFollowUp(project.id, !project.followUp, project.followUpStatus || "");
      const index = catalog.value.findIndex((item) => item.id === project.id);
      if (index >= 0) catalog.value[index] = { ...catalog.value[index], ...result.item };
      ElMessage.success(project.followUp ? "已取消跟进" : "已设置跟进");
    } catch (error) {
      ElMessage.error((error as Error).message);
    }
  }

  async function downloadProject(projectId = selectedProjectId.value) {
    if (!projectId) return;
    try {
      await api.downloadProject(projectId);
      ElMessage.success("已生成下载文件");
      await loadBootstrap();
    } catch (error) {
      ElMessage.error((error as Error).message);
    }
  }

  async function generateResponse(projectId = selectedProjectId.value) {
    if (!projectId) return;
    try {
      const result = await api.generateResponse(projectId);
      ElMessage.success(result.message || "响应文件已生成");
      await loadBootstrap();
    } catch (error) {
      ElMessage.error((error as Error).message);
    }
  }

  function downloadDraft() {
    if (!selectedWorkspace.value) return;
    const blob = new Blob([selectedWorkspace.value.draft], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${selectedWorkspace.value.title || "响应文件"}-草稿.txt`;
    link.click();
    URL.revokeObjectURL(url);
    ElMessage.success("草稿文档已下载");
  }

  async function importDocument() {
    const text = importForm.text.trim();
    const file = importFile.value;
    if (!text && !file) {
      ElMessage.warning("请先选择文件或输入文本");
      return;
    }
    busy.value = true;
    try {
      const payload: Record<string, unknown> = {
        name: importForm.name || file?.name || "招标文件.txt",
        text,
        documentKind: importKind.value
      };
      if (file) {
        payload.fileBase64 = await fileToBase64(file);
        payload.mimeType = file.type || "application/octet-stream";
        payload.name = file.name;
      }
      const result = await api.importDocument(payload);
      catalog.value.unshift(result.item);
      selectedProjectId.value = result.item.id;
      importForm.name = "";
      importForm.text = "";
      importFile.value = null;
      ElMessage.success(`已导入 ${result.file.extractedTextLength || 0} 字符`);
      await analyzeProject(result.item.id);
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function saveDraft() {
    if (!selectedWorkspace.value) return;
    busy.value = true;
    try {
      const result = await api.saveDraft(selectedWorkspace.value.id, selectedWorkspace.value.draft);
      const index = workspaces.value.findIndex((item) => item.id === result.workspace.id);
      if (index >= 0) workspaces.value[index] = result.workspace;
      ElMessage.success(`草稿已保存为 v${result.workspace.draftVersion}`);
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function updateFineReviewStatus(rowId: string, status: string) {
    if (!selectedWorkspace.value || !rowId) return;
    busy.value = true;
    try {
      const result = await api.updateFineReviewStatus(selectedWorkspace.value.id, rowId, status);
      const index = workspaces.value.findIndex((item) => item.id === result.workspace.id);
      if (index >= 0) workspaces.value[index] = result.workspace;
      else workspaces.value.unshift(result.workspace);
      selectedWorkspaceId.value = result.workspace.id;
      ElMessage.success("人工匹配结论已保存");
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function saveConfig(nextConfig?: Record<string, unknown>) {
    busy.value = true;
    try {
      const parsed = nextConfig || JSON.parse(configText.value || "{}");
      const result = await api.saveConfig(parsed);
      config.value = result.config || parsed;
      if (bootstrap.value) bootstrap.value.config = config.value;
      configText.value = JSON.stringify(config.value, null, 2);
      monitoring.value = await api.monitoringStatus();
      ElMessage.success("配置已保存");
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function testAiConnection(ai: Record<string, unknown>) {
    busy.value = true;
    try {
      const result = await api.testAiConnection(ai);
      if (result.success) {
        ElMessage.success(`${result.detail}${result.model ? `：${result.model}` : ""}`);
      } else {
        ElMessage.warning(result.detail || "模型接口测试失败");
      }
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function saveLibrary() {
    if (!libraryForm.key) return;
    busy.value = true;
    try {
      const payload: Record<string, unknown> = {
        name: libraryForm.name || libraryFile.value?.name || "资料文件",
        text: libraryForm.text.trim()
      };
      if (libraryFile.value) {
        payload.fileBase64 = await fileToBase64(libraryFile.value);
        payload.mimeType = libraryFile.value.type || "application/octet-stream";
        payload.name = libraryFile.value.name;
      }
      const result = await api.saveLibrary(libraryForm.key, payload);
      if (result.libraries) libraries.value = result.libraries as LibrarySummary[];
      libraryForm.name = "";
      libraryForm.text = "";
      libraryFile.value = null;
      ElMessage.success("资料已保存");
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function openKnowledgeObject(objectType: string, objectId: string) {
    knowledgeDetailLoading.value = true;
    try {
      selectedKnowledgeDetail.value = await api.knowledgeObjectDetail(objectType, objectId);
    } catch (error) {
      selectedKnowledgeDetail.value = null;
      ElMessage.error((error as Error).message);
    } finally {
      knowledgeDetailLoading.value = false;
    }
  }

  async function runAutomation() {
    busy.value = true;
    try {
      const result = await api.runAutomation();
      automation.value = result.run || {};
      await loadBootstrap();
      ElMessage.success("自动化流程已执行");
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function runMonitoring(siteId = "") {
    busy.value = true;
    try {
      const result = await api.runMonitoring(siteId);
      monitoring.value = result.monitoring;
      if (result.items.length) {
        ElNotification({
          title: "发现匹配公告",
          message: `本次新增 ${result.items.length} 条符合搜索配置的招标公告。`,
          type: "success"
        });
      }
      await loadBootstrap();
      ElMessage.success(`抓取完成：匹配 ${result.matchedCount} 条，新增 ${result.addedCount} 条`);
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function updateMonitorSite(siteId: string, enabled: boolean) {
    try {
      const result = await api.updateMonitorSite(siteId, enabled);
      monitoring.value = result.monitoring;
      config.value.monitoring = { ...(config.value.monitoring || {}), sites: result.monitoring.sites };
      ElMessage.success(enabled ? "已启用监控网站" : "已暂停监控网站");
    } catch (error) {
      ElMessage.error((error as Error).message);
    }
  }

  async function createMonitorSite(payload: Record<string, unknown>) {
    busy.value = true;
    try {
      const result = await api.createMonitorSite(payload);
      monitoring.value = result.monitoring;
      config.value.monitoring = { ...(config.value.monitoring || {}), sites: result.monitoring.sites };
      ElMessage.success("自定义监控网站已添加");
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function updateMonitorSiteConfig(siteId: string, payload: Record<string, unknown>) {
    busy.value = true;
    try {
      const result = await api.updateMonitorSiteConfig(siteId, payload);
      monitoring.value = result.monitoring;
      config.value.monitoring = { ...(config.value.monitoring || {}), sites: result.monitoring.sites };
      ElMessage.success("监控网站配置已更新");
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function deleteMonitorSite(siteId: string) {
    try {
      const result = await api.deleteMonitorSite(siteId);
      monitoring.value = result.monitoring;
      config.value.monitoring = { ...(config.value.monitoring || {}), sites: result.monitoring.sites };
      ElMessage.success("自定义监控网站已删除");
    } catch (error) {
      ElMessage.error((error as Error).message);
    }
  }

  async function testMonitorSite(siteId: string) {
    busy.value = true;
    try {
      const result = await api.testMonitorSite(siteId);
      if (result.success) ElMessage.success(`连接成功，解析到 ${result.count || 0} 条公告`);
      else ElMessage.warning(result.detail || result.status);
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  async function testInAppNotification() {
    try {
      const result = await api.testInAppNotification();
      ElNotification({ title: result.title, message: result.message, type: "success" });
    } catch (error) {
      ElMessage.error((error as Error).message);
    }
  }

  async function testWecom() {
    busy.value = true;
    try {
      const result = await api.testWecom();
      if (result.sent) ElMessage.success(result.detail || result.status);
      else ElMessage.warning(result.detail || result.status);
    } catch (error) {
      ElMessage.error((error as Error).message);
    } finally {
      busy.value = false;
    }
  }

  function selectProject(item: CatalogItem) {
    selectedProjectId.value = item.id;
    if (item.workspaceId) selectedWorkspaceId.value = item.workspaceId;
    followUpStatus.value = item.followUpStatus || "";
  }

  watch(selectedProjectId, () => {
    followUpStatus.value = selectedProject.value?.followUpStatus || "";
  });

  return {
    loading,
    busy,
    activeTab,
    company,
    companyKnowledge,
    selectedKnowledgeDetail,
    knowledgeDetailLoading,
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
    libraryFile,
    searchForm,
    importForm,
    libraryForm,
    selectedProject,
    selectedWorkspace,
    selectedLibrary,
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
    saveLibrary,
    openKnowledgeObject,
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
  };
}
