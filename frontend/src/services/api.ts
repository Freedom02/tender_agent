import type {
  BidDocument,
  BidMode,
  BidSection,
  BootstrapResponse,
  CatalogItem,
  CloudBidRun,
  ComplianceReviewResult,
  EvidenceChapterSummary,
  EvidenceEntry,
  EvidenceFilesResponse,
  ImportResult,
  MaterialFact,
  MaterialProfile,
  MonitoringStatus,
  MonitoringRunResult,
  OntologyMeta,
  OntologySummary,
  ParseBundle,
  ProofreadResult,
  Workspace
} from "../shared/types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const isFormData = typeof FormData !== "undefined" && init?.body instanceof FormData;
  const headers = new Headers(init?.headers || {});
  if (!isFormData && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(url, {
    headers,
    ...init
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(text || `请求失败：${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  bootstrap: () => request<BootstrapResponse>("/api/bootstrap"),
  search: (payload: Record<string, unknown>) =>
    request<{ items: CatalogItem[]; search: Record<string, unknown> }>("/api/search/realtime", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  importDocument: (payload: Record<string, unknown>) =>
    request<{ item: CatalogItem; file: Record<string, unknown> }>("/api/import", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  createBidRun: (file: File, workspaceId = "", projectId = "") => {
    const form = new FormData();
    form.append("file", file, file.name);
    form.append("workspace_id", workspaceId);
    form.append("project_id", projectId);
    return request<{ run: CloudBidRun }>("/api/bid-runs", {
      method: "POST",
      body: form
    });
  },
  getBidRun: (runId: string) =>
    request<{ run: CloudBidRun }>(`/api/bid-runs/${encodeURIComponent(runId)}`),
  materializeBidRun: (runId: string) =>
    request<{
      run: CloudBidRun;
      project: CatalogItem;
      workspace: Workspace;
      materialized: boolean;
    }>(`/api/bid-runs/${encodeURIComponent(runId)}/materialize`, {
      method: "POST"
    }),
  getParseBundle: (workspaceId: string) =>
    request<{ parseBundle: ParseBundle }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/parse-bundle`
    ),
  reuseMatchAnalysis: (workspaceId: string) =>
    request<{ workspace: Workspace; parseBundle: ParseBundle; parseSource: string }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/reuse-match-analysis`,
      { method: "POST" }
    ),
  analyze: (projectId: string) =>
    request<{ item: CatalogItem; workspace: Workspace }>("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ projectId })
    }),
  markRead: (projectId: string) =>
    request<{ item: CatalogItem }>(`/api/catalog/${encodeURIComponent(projectId)}/read`, {
      method: "POST"
    }),
  setFollowUp: (projectId: string, followUp = true, followUpStatus = "") =>
    request<{ item: CatalogItem }>(`/api/catalog/${encodeURIComponent(projectId)}/follow-up`, {
      method: "POST",
      body: JSON.stringify({ followUp, followUpStatus })
    }),
  downloadProject: (projectId: string) =>
    request<Record<string, unknown>>(`/api/catalog/${encodeURIComponent(projectId)}/download`, { method: "POST" }),
  generateResponse: (projectId: string) =>
    request<{ message: string; files: { name: string; url: string }[] }>(
      `/api/tenders/${encodeURIComponent(projectId)}/generate-response`,
      { method: "POST" }
    ),
  saveDraft: (workspaceId: string, draft: string) =>
    request<{ workspace: Workspace }>(`/api/workspaces/${encodeURIComponent(workspaceId)}/draft`, {
      method: "POST",
      body: JSON.stringify({ draft })
    }),
  updateFineReviewStatus: (
    workspaceId: string,
    rowId: string,
    status: string,
    uncertaintyReason = "",
    reviewer = "当前用户",
    responseValue?: string,
    materialBindings?: Array<Record<string, unknown>>
  ) =>
    request<{ workspace: Workspace }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/fine-review/status`,
      {
        method: "POST",
        body: JSON.stringify({
          rowId,
          status,
          reviewer,
          uncertaintyReason,
          responseValue,
          materialBindings
        })
      }
    ),
  // ---- 企业材料本体（M3 读取）----
  getOntologyMeta: () => request<OntologyMeta>("/api/ontology/meta"),
  getOntologySummary: () => request<OntologySummary>("/api/ontology/summary"),
  getFacts: (className: string, limit = 20) =>
    request<MaterialFact[]>(`/api/ontology/classes/${encodeURIComponent(className)}?limit=${limit}`),
  createFactWithEvidence: async (className: string, fields: Record<string, unknown>, files: File[]) => {
    const form = new FormData();
    form.append("fields", JSON.stringify(fields));
    files.forEach((file) => form.append("files", file, file.name));
    const response = await fetch(`/api/materials/facts/${encodeURIComponent(className)}/with-evidence`, {
      method: "POST",
      body: form
    });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `请求失败：${response.status}`);
    }
    return response.json() as Promise<{
      item: MaterialFact;
      objectId: string;
      rid: string;
      className: string;
      evidencePath: string;
      relations: unknown[];
      warnings: string[];
    }>;
  },
  attachEvidenceToFact: async (className: string, objectId: string, files: File[]) => {
    const form = new FormData();
    files.forEach((file) => form.append("files", file, file.name));
    const response = await fetch(
      `/api/materials/facts/${encodeURIComponent(className)}/${encodeURIComponent(objectId)}/attach-evidence`,
      { method: "POST", body: form }
    );
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `请求失败：${response.status}`);
    }
    return response.json() as Promise<{
      item: MaterialFact;
      objectId: string;
      rid: string;
      className: string;
      evidencePath: string;
    }>;
  },
  createFact: (className: string, fields: Record<string, unknown>) =>
    request<MaterialFact>(`/api/ontology/classes/${encodeURIComponent(className)}`, {
      method: "POST",
      body: JSON.stringify({ fields })
    }),
  updateFact: (className: string, id: string, fields: Record<string, unknown>) =>
    request<MaterialFact>(`/api/ontology/classes/${encodeURIComponent(className)}/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify({ fields })
    }),
  deleteFact: (className: string, id: string) =>
    request<{ deleted: boolean }>(`/api/ontology/classes/${encodeURIComponent(className)}/${encodeURIComponent(id)}`, {
      method: "DELETE"
    }),
  getEvidenceFiles: (path: string) =>
    request<EvidenceFilesResponse>(`/api/materials/evidence-files?path=${encodeURIComponent(path)}`),
  importMaterials: (payload: { fileBase64: string; fileName: string }) =>
    request<ImportResult>("/api/materials/import", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  importMaterialsFile: async (file: File) => {
    // multipart 直传，不转 base64，支持大文件（避免 2GB 内存爆炸）
    const fd = new FormData();
    fd.append("file", file, file.name);
    const response = await fetch("/api/materials/import/upload", { method: "POST", body: fd });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `请求失败：${response.status}`);
    }
    return response.json() as Promise<ImportResult>;
  },
  getMaterialProfile: () => request<MaterialProfile>("/api/materials/profile"),
  saveConfig: (config: Record<string, unknown>) =>
    request<{ config: Record<string, unknown> }>("/api/config", {
      method: "POST",
      body: JSON.stringify({ config })
    }),
  testAiConnection: (ai: Record<string, unknown>) =>
    request<{ success: boolean; model?: string; detail: string }>("/api/config/ai/test", {
      method: "POST",
      body: JSON.stringify({ config: { ai } })
    }),
  piPreflight: () => request<Record<string, any>>("/api/config/pi/preflight"),
  workspaceDetail: (workspaceId: string) =>
    request<Workspace>(`/api/workspaces/${encodeURIComponent(workspaceId)}`),
  deleteWorkspace: (workspaceId: string) =>
    request<{
      success: boolean;
      workspaceId: string;
      projectId: string;
      title: string;
      removedProjects: string[];
      removedReminders: number;
      removedArtifacts: string[];
    }>(`/api/workspaces/${encodeURIComponent(workspaceId)}`, { method: "DELETE" }),
  monitoringStatus: () => request<MonitoringStatus>("/api/monitoring/status"),
  runMonitoring: (siteId = "") =>
    request<MonitoringRunResult>("/api/monitoring/run", {
      method: "POST",
      body: JSON.stringify({ siteId })
    }),
  updateMonitorSite: (siteId: string, enabled: boolean) =>
    request<{ monitoring: MonitoringStatus }>(`/api/monitoring/sites/${encodeURIComponent(siteId)}`, {
      method: "PATCH",
      body: JSON.stringify({ enabled })
    }),
  createMonitorSite: (payload: Record<string, unknown>) =>
    request<{ site: Record<string, unknown>; monitoring: MonitoringStatus }>("/api/monitoring/sites", {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  updateMonitorSiteConfig: (siteId: string, payload: Record<string, unknown>) =>
    request<{ site: Record<string, unknown>; monitoring: MonitoringStatus }>(`/api/monitoring/sites/${encodeURIComponent(siteId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload)
    }),
  deleteMonitorSite: (siteId: string) =>
    request<{ monitoring: MonitoringStatus }>(`/api/monitoring/sites/${encodeURIComponent(siteId)}`, {
      method: "DELETE"
    }),
  testMonitorSite: (siteId: string) =>
    request<{ success: boolean; status: string; detail?: string; count?: number; items?: CatalogItem[] }>(
      `/api/monitoring/sites/${encodeURIComponent(siteId)}/test`,
      { method: "POST" }
    ),
  toggleReminder: (projectId: string) =>
    request<{ item: CatalogItem; reminders: Record<string, unknown>[] }>(
      `/api/notifications/projects/${encodeURIComponent(projectId)}/toggle`,
      { method: "POST" }
    ),
  testInAppNotification: () =>
    request<{ title: string; message: string }>("/api/notifications/test/in-app", { method: "POST" }),
  testWecom: () =>
    request<{ sent: boolean; status: string; detail: string }>("/api/wecom/test", { method: "POST" }),
  runAutomation: () => request<Record<string, unknown>>("/api/automation/run", { method: "POST" }),
  // ---- 标书生成 ----
  bidDocumentPlan: (workspaceId: string) =>
    request<{ bidDocument: BidDocument; workspace: Workspace }>(`/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/plan`, {
      method: "POST",
      body: JSON.stringify({})
    }),
  bidDocumentGenerate: (workspaceId: string, mode: BidMode) =>
    request<{ bidDocument: BidDocument }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/generate`,
      { method: "POST", body: JSON.stringify({ mode }) }
    ),
  bidDocumentSavePlan: (workspaceId: string, plan: BidSection[]) =>
    request<{ bidDocument: BidDocument; workspace: Workspace }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/plan`,
      { method: "PUT", body: JSON.stringify({ plan }) }
    ),
  bidDocumentGenerateSection: (workspaceId: string, sectionId: string) =>
    request<{ bidDocument: BidDocument }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/sections/${encodeURIComponent(sectionId)}/generate`,
      { method: "POST", body: JSON.stringify({}) }
    ),
  bidDocumentFillTemplate: (workspaceId: string, sectionId: string) =>
    request<{ bidDocument: BidDocument; section: BidSection; provider?: string; message?: string }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/sections/${encodeURIComponent(sectionId)}/fill-template`,
      { method: "POST", body: JSON.stringify({}) }
    ),
  bidDocumentRewriteSection: (
    workspaceId: string,
    sectionId: string,
    instruction: string,
    mode: "append" | "rewrite" | "replace_selection" = "append",
    selectedText = ""
  ) =>
    request<{
      bidDocument: BidDocument;
      section: BidSection;
      rewritten: boolean;
      provider?: string;
      message?: string;
    }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/sections/${encodeURIComponent(sectionId)}/rewrite`,
      {
        method: "POST",
        body: JSON.stringify({ instruction, mode, selectedText })
      }
    ),
  bidDocumentSaveSection: (
    workspaceId: string,
    sectionId: string,
    payload: {
      content: string;
      templateBlocks?: Record<string, unknown>[];
      templateTables?: Record<string, unknown>[];
      contentMatchesTemplate?: boolean;
    }
  ) =>
    request<{ bidDocument: BidDocument }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/sections/${encodeURIComponent(sectionId)}`,
      { method: "PUT", body: JSON.stringify(payload) }
    ),
  bidDocumentTemplatePreview: async (workspaceId: string, sectionId: string) => {
    const response = await fetch(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/sections/${encodeURIComponent(sectionId)}/template-preview`
    );
    if (!response.ok) {
      const text = await response.text();
      throw new Error(text || `请求失败：${response.status}`);
    }
    return response.blob();
  },
  bidDocumentExport: (workspaceId: string, mode: BidMode) =>
    request<{
      files: { name: string; url: string }[];
      message: string;
      bidDocument?: BidDocument;
      copiedTemplateCount?: number;
      exportWarnings?: string[];
    }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/export`,
      { method: "POST", body: JSON.stringify({ mode }) }
    ),
  proofread: (workspaceId: string) =>
    request<ProofreadResult>(`/api/workspaces/${encodeURIComponent(workspaceId)}/proofread`),
  // ---- 技术参数证明材料 ----
  bidDocumentBuildEvidenceChapter: (workspaceId: string) =>
    request<{
      bidDocument: BidDocument;
      section: BidSection;
      summary: EvidenceChapterSummary;
      message: string;
    }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/evidence-chapter`,
      { method: "POST", body: JSON.stringify({}) }
    ),
  bidDocumentEvidencePreview: (workspaceId: string) =>
    request<{
      sectionId: string;
      exists: boolean;
      entries: EvidenceEntry[];
      summary: EvidenceChapterSummary;
      blanksToFill: string[];
    }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/bid-document/evidence-chapter`
    ),
  complianceReview: (workspaceId: string) =>
    request<ComplianceReviewResult & { bidDocument: BidDocument }>(
      `/api/workspaces/${encodeURIComponent(workspaceId)}/compliance-review`,
      { method: "POST", body: JSON.stringify({}) }
    )
};
