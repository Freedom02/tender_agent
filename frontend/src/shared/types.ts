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
  relevanceScore?: number;
  matchedBusinessDirections?: string[];
  matchedScenarios?: string[];
  matchedProducts?: string[];
  matchedCapabilities?: string[];
  matchedKeywords?: string[];
  matchReasons?: string[];
  matchEvidence?: string[];
  scoreBreakdown?: RelevanceScoreBreakdown;
  scoreVersion?: string;
  excludeReason?: string;
  nextStage?: string;
  shouldAnalyze?: boolean;
  projectName?: string;
  searchProfile?: {
    serviceType?: string;
    region?: string;
    includeKeywords?: string[];
    excludeKeywords?: string[];
    targetDirections?: string[];
    targetScenarios?: string[];
    targetProducts?: string[];
    targetCapabilities?: string[];
    synonyms?: string[];
    targetProcurementTypes?: string[];
    lookbackDays?: number;
  };
  crawlMeta?: Record<string, unknown>;
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

export interface RelevanceScoreComponent {
  points: number;
  maxPoints: number;
  matched?: string[];
  terms?: string[];
  evidence?: string[];
}

export interface RelevanceScoreBreakdown {
  version: string;
  total?: number;
  dimensions?: {
    businessDirection?: RelevanceScoreComponent;
    industryScenario?: RelevanceScoreComponent;
    product?: RelevanceScoreComponent;
    capability?: RelevanceScoreComponent;
    configuredKeywords?: RelevanceScoreComponent;
  };
  configuredKeywords?: RelevanceScoreComponent;
  hardFilters?: {
    exclude?: { passed: boolean; hits?: string[] };
    region?: { passed: boolean; target?: string; matchedText?: string };
    procurementType?: { passed: boolean; target?: string[] };
  };
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
    latestMatchedCount?: number;
    totalCollected: number;
    lastFetchedAt: string;
    nextFetchedAt: string;
  };
}

export interface MonitorRunSite {
  siteId: string;
  name: string;
  success: boolean;
  status: string;
  error?: string;
  count: number;
  matchedCount: number;
  addedCount: number;
  dedupedCount: number;
}

export interface MonitoringRunResult {
  status: string;
  addedCount: number;
  matchedCount: number;
  dedupedCount: number;
  collectedCount: number;
  items: CatalogItem[];
  sites: MonitorRunSite[];
  monitoring: MonitoringStatus;
}

export interface ReasoningRow {
  id?: string;
  comparisonId?: string;
  requirementId?: string;
  layer?: number;
  sourceStage?: "announcement" | "tender_document" | string;
  kind: string;
  category?: string;
  module?: string;
  section?: string;
  rawText?: string;
  normalizedRequirement?: string;
  importance?: "critical" | "high" | "medium" | "normal" | string;
  requirementGroup?: string;
  evidenceNeed?: string;
  responseType?: string;
  sourceFileId?: string;
  sourceLocator?: string;
  requirement: string;
  sourceText?: string;
  mandatory?: boolean;
  isRejection?: boolean;
  status: string;
  evidence: string;
  evidenceItems?: Array<{
    id: string;
    evidenceId?: string;
    materialId?: string;
    name: string;
    sourceType?: "evidence" | "knowledge_object" | string;
    status?: string;
    verificationStatus?: string;
    level?: string;
    evidenceLevel?: string;
    validFrom?: string;
    validUntil?: string;
    sourceFileIds?: string[];
    sourceFiles?: string[];
    sourceLocator?: string;
    locator?: string;
    excerpt?: string;
    isHistorical?: boolean;
    isExpired?: boolean;
    isVerified?: boolean;
  }>;
  matchedObjectIds?: string[];
  matchedMaterialIds?: string[];
  evidenceIds?: string[];
  evidenceCoverageStatus?: string;
  risk: string;
  riskLevel?: string;
  reason?: string;
  suggestion?: string;
  confidence?: number;
  manualStatus?: string;
  statusSource?: "模型/规则" | "人工审核" | string;
  reviewStatus?: string;
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
  summary?: MatchSummary;
  project?: Record<string, string>;
  procurementNeeds?: Array<{
    id: string;
    category: string;
    text: string;
    section?: string;
    sourceLocator?: string;
    status?: "已关联" | "待关联" | string;
    matchedObjectIds?: string[];
    matchedObjects?: Array<{ id?: string; name?: string; category?: string }>;
    evidenceItems?: ReasoningRow["evidenceItems"];
    confidence?: number;
  }>;
  relevance?: {
    score: number;
    reasons?: string[];
    matchEvidence?: string[];
    scoreBreakdown?: RelevanceScoreBreakdown;
    scoreVersion?: string;
    nextStage?: string;
  };
  extraction?: {
    method: "llm" | "rule" | string;
    modelUsed?: string;
    warning?: string;
    warnings?: string[];
  };
  eligibility?: {
    eligibilityScore: number;
    eligibilityStatus: string;
    assessmentAvailable?: boolean;
    hardBlockers: Array<{
      requirementId: string;
      title: string;
      detail: string;
      sourceLocator?: string;
    }>;
    rejectionRisks?: MatchRiskReference[];
    shouldGetTenderDocument: boolean;
    recommendation: string;
  };
  fineReview?: FineReviewResult;
}

export interface FineReviewResult {
  rows: ReasoningRow[];
  risks: RiskItem[];
  score: number;
  decision: string;
  recommendation: string;
  clauseCount: number;
  statusCounts: Record<string, number>;
  summary?: MatchSummary;
  hardBlockers: Array<{
    requirementId: string;
    title: string;
    detail: string;
    sourceLocator?: string;
  }>;
  rejectionRisks?: MatchRiskReference[];
  unresolvedMandatory: Array<{
    requirementId: string;
    title: string;
    detail: string;
    sourceLocator?: string;
  }>;
  extraction: {
    method: "llm" | "rule" | string;
    modelUsed?: string;
    warning?: string;
    warnings?: string[];
  };
}

export interface MatchRiskReference {
  requirementId: string;
  title: string;
  detail: string;
  sourceLocator?: string;
}

export interface MatchSummary {
  totalRequirementCount: number;
  statusCounts: Record<string, number>;
  mandatoryCount: number;
  mandatoryClosedCount: number;
  mandatoryClosureRate: number;
  evidenceRequiredCount: number;
  evidenceCoveredCount: number;
  verifiedEvidenceCount: number;
  evidenceCoverageRate: number;
  rejectionCount: number;
  rejectionRiskCount: number;
  hardBlockerCount: number;
  pendingMaterialCount: number;
  pendingClarificationCount: number;
  notSatisfiedCount: number;
}

export interface Workspace {
  id: string;
  workspaceId?: string;
  projectId: string;
  title: string;
  project: CatalogItem;
  reasoning: ReasoningResult;
  auditRecords?: AuditRecord[];
  draft: string;
  draftVersion: number;
  bidDocument?: BidDocument;
}

export interface AuditRecord {
  id: string;
  workspaceId: string;
  projectId: string;
  projectTitle: string;
  rowId: string;
  requirement: string;
  previousStatus: string;
  newStatus: string;
  reviewer: string;
  reviewedAt: string;
  source: string;
}

export interface LibraryDocument {
  id: string;
  sourceId?: string;
  name: string;
  type?: string;
  savedAt?: string;
  storagePath?: string;
  preview?: string;
  size?: number;
  indexedOnly?: boolean;
  status?: string;
  evidenceLevel?: string;
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
  materialsCounts: Record<string, number>;
  company: Record<string, unknown>;
  config: Record<string, any>;
  monitoring?: MonitoringStatus;
  catalog: CatalogItem[];
  libraries: LibrarySummary[];
  reminders: Record<string, unknown>[];
  automation: Record<string, any>;
  workspaces: Workspace[];
}

export interface MaterialCertificate {
  name: string;
  number?: string;
  validUntil?: string;
  issuer?: string;
}

export interface MaterialPerformance {
  name: string;
  industry?: string;
  type?: string;
  region?: string;
  amount?: string;
  date?: string;
}

export interface MaterialPersonnel {
  role: string;
  count?: number | null;
  experienceYears?: number | null;
}

export interface MaterialProfile {
  company: string;
  qualifications: string[];
  certificates: MaterialCertificate[];
  performances: MaterialPerformance[];
  personnel: MaterialPersonnel[];
  service_regions: string[];
  capabilities: string[];
}

// ---------------- 企业材料本体（M3 读取）----------------

export interface OntologyAttrDef {
  name: string; // snake_case
  cn: string;
  type: string; // varchar/text/date/timestamp/int/double
  isPk: boolean;
}

export interface OntologyClassDef {
  name: string; // snake_case
  cnName: string;
  description: string;
  attrs: OntologyAttrDef[];
}

export interface OntologyRelationDef {
  name: string;
  fromClass: string;
  toClass: string;
  description: string;
}

export interface OntologyMeta {
  classes: OntologyClassDef[];
  relations: OntologyRelationDef[];
}

export interface OntologySummary {
  namespace: string;
  classes: number;
  relations: number;
  counts: Record<string, number>;
  unavailable: string[];
}

export interface MaterialFact {
  id: string; // RID：tenderhub_{class}:{businessId}
  [key: string]: unknown;
}

export interface EvidenceFile {
  name: string;
  url: string;
  size: number;
}

export interface EvidenceFilesResponse {
  path: string;
  files: EvidenceFile[];
}

export interface ImportResult {
  stats: Record<string, number>;
  warnings: string[];
  m3Sync: { objects: number; edges: number; warnings: string[] };
  profile: Record<string, unknown>;
}

// ---------------- 标书生成 ----------------

export type BidMode = "open" | "secret";

export interface BidSection {
  sectionId: string;
  title: string;
  source: string;
  kind: string;
  content: string;
  status: string;
}

export interface DeviationRow {
  clause?: string;
  requirement?: string;
  response?: string;
  deviation?: string;
  type?: string;
  level?: string;
  remark?: string;
  [key: string]: unknown;
}

export interface BidDocument {
  plan: BidSection[];
  deviationTables: {
    technical: DeviationRow[];
    commercial: DeviationRow[];
  };
  mode: BidMode;
  status: string;
}

export interface ProofreadResult {
  report: string;
  riskItems: RiskItem[];
}
