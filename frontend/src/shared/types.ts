export interface CatalogItem {
  id: string;
  title: string;
  category?: string;
  region?: string;
  noticeType?: string;
  publishedDate?: string;
  source?: string;
  sourceUrl?: string;
  registrationPeriod?: string;
  registrationDeadline?: string;
  bidDeadline?: string;
  dataOrigin?: string;
  status?: string;
  score?: number;
  decision?: string;
  analysisText?: string;
  analysisPreview?: string;
  workspaceId?: string;
  followUp?: boolean;
  followUpStatus?: string;
  read?: boolean;
  reminderStatus?: "已提醒" | "未提醒" | string;
  remindedAt?: string;
  notificationChannels?: string[];
  downloadStatus?: string;
  downloadUrl?: string;
}

export interface MonitorSite {
  id: string;
  name: string;
  url: string;
  enabled: boolean;
  status: string;
  latest: string;
  next: string;
  listUrl?: string;
  fetchMode?: "html" | "json" | string;
  custom?: boolean;
  selectors?: Record<string, string>;
  jsonFields?: Record<string, string>;
}

export interface MonitoringStatus {
  enabled: boolean;
  frequencyMinutes: number;
  sites: MonitorSite[];
  stats: {
    latestCount: number;
    totalCollected: number;
    lastFetchedAt: string;
    nextFetchedAt: string;
  };
}

export interface ReasoningRow {
  kind: string;
  requirement: string;
  status: string;
  evidence: string;
  risk: string;
}

export interface RiskItem {
  level: string;
  title: string;
  detail: string;
}

export interface ReasoningResult {
  rows: ReasoningRow[];
  risks: RiskItem[];
  score: number;
  decision: string;
}

export interface Workspace {
  id: string;
  workspaceId?: string;
  projectId: string;
  title: string;
  project: CatalogItem;
  reasoning: ReasoningResult;
  draft: string;
  draftVersion: number;
}

export interface LibraryDocument {
  id: string;
  name: string;
  type?: string;
  savedAt?: string;
  storagePath?: string;
  preview?: string;
  size?: number;
}

export interface LibrarySummary {
  key: string;
  name: string;
  description?: string;
  documentCount: number;
  documents: LibraryDocument[];
}

export interface BootstrapResponse {
  ontology: {
    name: string;
    version: string;
    classes: number;
    relations: number;
  };
  company: Record<string, unknown>;
  config: Record<string, any>;
  monitoring?: MonitoringStatus;
  catalog: CatalogItem[];
  libraries: LibrarySummary[];
  reminders: Record<string, unknown>[];
  automation: Record<string, any>;
  workspaces: Workspace[];
}
