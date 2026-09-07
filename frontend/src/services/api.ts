import type { BootstrapResponse, CatalogItem, MonitoringStatus, Workspace } from "../shared/types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
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
  saveLibrary: (libraryKey: string, payload: Record<string, unknown>) =>
    request<Record<string, unknown>>(`/api/libraries/${encodeURIComponent(libraryKey)}`, {
      method: "POST",
      body: JSON.stringify(payload)
    }),
  saveConfig: (config: Record<string, unknown>) =>
    request<{ config: Record<string, unknown> }>("/api/config", {
      method: "POST",
      body: JSON.stringify({ config })
    }),
  monitoringStatus: () => request<MonitoringStatus>("/api/monitoring/status"),
  runMonitoring: (siteId = "") =>
    request<{
      status: string;
      addedCount: number;
      matchedCount: number;
      dedupedCount: number;
      items: CatalogItem[];
      monitoring: MonitoringStatus;
    }>("/api/monitoring/run", {
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
  runAutomation: () => request<Record<string, unknown>>("/api/automation/run", { method: "POST" })
};
