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
  document?: {
    kind?: string;
    name?: string;
    fileName?: string;
    parseMethod?: string;
    warnings?: string[];
    [key: string]: unknown;
  };
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
  requirementClass?: "evidence" | "rejection" | "writing" | "compliance" | "fill" | string;
  contentType?: string;
  directTypeHits?: string[];
  inheritedType?: string;
  typeHits?: string[];
  primaryRoute?: string;
  secondaryRoutes?: string[];
  matchDomain?: string;
  decisionImpact?: string;
  granularity?: string;
  reviewRequired?: boolean;
  reviewMode?: string;
  reviewReason?: string;
  classificationSource?: string;
  productName?: string;
  targetType?: string;
  sourceColumn?: string;
  module?: string;
  targetModule?: string;
  targetModuleLabel?: string;
  section?: string;
  rawText?: string;
  normalizedRequirement?: string;
  importance?: "critical" | "high" | "medium" | "normal" | string;
  requirementGroup?: string;
  evidenceNeed?: string;
  responseType?: string;
  sourceFileId?: string;
  sourceLocator?: string;
  source?: Record<string, any>;
  downloadText?: string;
  templateId?: string;
  templateType?: string;
  requirement: string;
  sourceText?: string;
  mandatory?: boolean;
  isRejection?: boolean;
  status: string;
  uncertaintyReason?: string;
  responseValue?: string;
  technicalAlignment?: Record<string, any>;
  gaps?: string[];
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
  supplementedMaterials?: Array<{
    rid: string;
    className?: string;
    objectId?: string;
    label?: string;
    evidencePath?: string;
  }>;
}

export interface RiskItem {
  level: string;
  title: string;
  detail: string;
}

export interface ReviewOutputModule {
  domain?: string;
  targetModule?: string;
  targetModuleLabel?: string;
  primaryRoute?: string;
  handlingMode?: string;
  ignored?: boolean;
  count?: number;
  items?: Array<Record<string, any>>;
  source?: Record<string, any>;
  warnings?: string[];
}

export interface ReviewOutput {
  schemaVersion?: string;
  producer?: string;
  source?: Record<string, any>;
  document?: Record<string, any>;
  modules?: Record<string, ReviewOutputModule>;
  summary?: Record<string, any>;
  matchStage?: Record<string, any>;
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
    provider?: string;
    modelUsed?: string;
    warning?: string;
    warnings?: string[];
    stats?: Record<string, unknown>;
    analysisPack?: Record<string, any>;
    documentMap?: Record<string, any>;
    reviewOutput?: ReviewOutput;
    agentStatus?: string;
    fallbackMode?: string;
    errors?: string[];
  };
  analysisPack?: Record<string, any>;
  documentMap?: Record<string, any>;
  reviewOutput?: ReviewOutput;
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
  classCounts?: Record<string, number>;
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
  pendingMaterialList?: Array<MatchRiskReference & { importance?: string; evidenceNeed?: string }>;
  coverageBreakdown?: {
    products?: Array<{ productName: string; paramCount: number; score: number }>;
    qualification?: { itemCount: number; score: number } | null;
  };
  extraction: {
    method: "llm" | "rule" | string;
    provider?: string;
    modelUsed?: string;
    warning?: string;
    warnings?: string[];
    stats?: Record<string, unknown>;
    analysisPack?: Record<string, any>;
    documentMap?: Record<string, any>;
    reviewOutput?: ReviewOutput;
    agentStatus?: string;
    fallbackMode?: string;
    errors?: string[];
  };
  analysisPack?: Record<string, any>;
  documentMap?: Record<string, any>;
  reviewOutput?: ReviewOutput;
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
  classCounts?: Record<string, number>;
  evidenceStatusCounts?: Record<string, number>;
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
  pendingMaterialCount?: number;
  pendingClarificationCount: number;
  rejectionPendingCount?: number;
  rejectionSafeCount?: number;
  writing?: { total: number; done: number; rate: number };
  compliance?: { total: number; done: number; rate: number };
  fill?: { total: number; done: number; rate: number };
  uncertainCount?: number;
  notSatisfiedCount: number;
}

export interface Workspace {
  id: string;
  workspaceId?: string;
  projectId: string;
  title: string;
  project: CatalogItem;
  reasoning: ReasoningResult;
  parseBundleRef?: {
    parseBundleId: string;
    schemaVersion: string;
    provider: string;
    artifact: string;
  };
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
  agentRuntime?: {
    provider: string;
    piEnabled: boolean;
    piProvider: string;
    piModel: string;
    backendPid?: number;
  };
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

export interface SubmissionStructure {
  mode: "single" | "separate" | "unspecified";
  confidence: "explicit" | "inferred" | "unknown";
  hasExplicitFormat?: boolean;
  sourceLocator: string;
  volumes: {
    volumeId: string;
    title: string;
    order: number;
    required: boolean;
    sourceLocator: string;
    formatItemIds: string[];
    items?: {
      formatItemId: string;
      title: string;
      order: number;
      required: boolean;
      conditional?: boolean;
      conditionText?: string;
      itemType?: string;
      responseType?: string;
      templateType?: string;
      templateId?: string;
      sourceLocator?: string;
      sourceSectionId?: string;
    }[];
  }[];
  warnings: string[];
}

export interface BidSection {
  sectionId: string;
  title: string;
  source: string;
  kind: string;
  nodeType?: "volume" | "format_item";
  volumeId?: string;
  formatItemId?: string;
  itemType?: string;
  conditional?: boolean;
  conditionText?: string;
  structureSource?: string;
  parentSectionId?: string;
  level?: number;
  order?: number;
  required?: boolean;
  responseType?: string;
  content: string;
  status: string;
  blanksToFill?: string[];
  templateOrigin?: string;
  templateText?: string;
  requirementIds?: string[];
  evaluationIds?: string[];
  templateId?: string;
  templateFragmentId?: string;
  templateType?: string;
  templateCopyMode?: string;
  templateStatus?: string;
  templateFieldCount?: number;
  templateLockedBlockCount?: number;
  templateTableCount?: number;
  templateBlocks?: Record<string, unknown>[];
  templateTables?: Record<string, unknown>[];
  templateStyle?: Record<string, unknown>;
  templateResponseTable?: Record<string, unknown>;
  templateFillProvider?: string;
  templateFillStatus?: string;
  templateFieldValues?: Record<string, unknown>[];
  templateCellValues?: Record<string, unknown>[];
  templateFillWarnings?: string[];
  templateFillError?: string;
  contentMatchesTemplate?: boolean;
  templateEditFallback?: boolean;
  sourceSectionId?: string;
  sourceRefs?: Record<string, unknown>[];
  sourceLocator?: string;
  materialMatches?: Record<string, unknown>[];
  unmatchedMaterialRequirementIds?: string[];
  writingPlan?: Record<string, unknown>;
  agentResults?: Record<string, unknown>[];
  provider?: string;
  phase?: string;
  draftProvider?: "dify" | "local_fallback" | string;
  cloudDraftError?: string;
  rewriteProvider?: string;
  rewriteOperation?: string;
  rewriteWarnings?: string[];
  rewriteSourceRefs?: Record<string, unknown>[];
  editHistory?: Record<string, unknown>[];
}

export interface EvidenceRef {
  factKey?: string;
  factId?: string;
  name?: string;
  evidencePath?: string;
  /** 磁盘上是否真有扫描件（false = 已匹配到证据位但原件尚未上传） */
  available?: boolean;
  [key: string]: unknown;
}

export interface EvidenceEntry {
  sequence: number;
  requirementId: string;
  requirement: string;
  category: string;
  matchStatus: string;
  matchReason: string;
  gaps: string[];
  materials: {
    materialId?: string;
    name?: string;
    description?: string;
    evidencePath?: string;
    sourceGroup?: string;
    verificationStatus?: string;
  }[];
  sourceRefs: EvidenceRef[];
  status: "ready" | "missing" | string;
}

export interface EvidenceChapterSummary {
  total: number;
  ready: number;
  missing: number;
  distinctEvidence?: number;
  /** 已匹配到证据的条目中，原件真正已上传的条数 */
  scannedReady?: number;
  scannedTotal?: number;
}

export interface EvidenceChapterPreview {
  sectionId: string;
  exists: boolean;
  entries: EvidenceEntry[];
  summary: EvidenceChapterSummary;
  blanksToFill: string[];
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
  submissionStructure?: SubmissionStructure;
  deviationTables: {
    technical: DeviationRow[];
    commercial: DeviationRow[];
  };
  mode: BidMode;
  status: string;
  planSource?: string;
  planSourceDetail?: string;
  planningSource?: string;
  planningSourceDetail?: string;
  parseProvider?: string;
  parseSourceDetail?: string;
  planningProvider?: string;
  planningStatus?: string;
  planningModel?: string;
  planningFallbackReason?: string;
  planningWarnings?: string[];
  requirementCoverage?: Record<string, unknown>[];
  uncoveredRequirements?: Record<string, unknown>[];
  duplicatedRequirements?: Record<string, unknown>[];
  requirementMappingStatus?: string;
  requirementMapBatchCount?: number;
  requirementMappingWarnings?: string[];
  agentResults?: Record<string, unknown>[];
  templateFragments?: Record<string, unknown>[];
  templateFragmentsArtifact?: string;
  copiedTemplateCount?: number;
  exportWarnings?: string[];
  lastExportMode?: BidMode;
  exportedAt?: string;
  complianceReview?: ComplianceReviewResult;
}

export interface ProofreadResult {
  report: string;
  riskItems: RiskItem[];
}

export interface ComplianceRiskItem extends RiskItem {
  riskId?: string;
  category?: string;
  sectionId?: string;
  requirementId?: string;
  evaluationId?: string;
  sourceLocator?: string;
  suggestion?: string;
  sourceRefs?: Record<string, unknown>[];
}

export interface ComplianceCoverage {
  requirementTotal: number;
  requirementCovered: number;
  mandatoryTotal: number;
  mandatoryCovered: number;
  evaluationTotal: number;
  evaluationCovered: number;
  uncoveredRequirementIds?: string[];
  uncoveredEvaluationIds?: string[];
  contentSectionIds?: string[];
}

export interface ComplianceReviewResult {
  status: "pass" | "needs_review" | "failed" | "stale" | string;
  summary: string;
  coverage: ComplianceCoverage;
  riskItems: ComplianceRiskItem[];
  baselineRiskItems?: ComplianceRiskItem[];
  baselineProofread?: ProofreadResult;
  warnings: string[];
  provider: string;
  phase?: string;
  agentResults?: Record<string, unknown>[];
  reviewedAt?: string;
  staleAt?: string;
}

// ---------------- 云端智能体解析 ----------------

export interface CloudProjectMeta {
  projectName: string;
  projectCode: string;
  tendererName: string;
  agencyName: string;
  budget: string;
  deadline: string;
  [key: string]: unknown;
}

export interface CloudOutlineItem {
  sectionId: string;
  title: string;
  kind: string;
  source: string;
  requirementIds: string[];
  [key: string]: unknown;
}

export interface CloudRequirementItem {
  id: string;
  category: string;
  requirement: string;
  mandatory: boolean;
  sourceLocator: string;
  status: string;
  [key: string]: unknown;
}

export interface CloudParseResult {
  projectMeta: CloudProjectMeta;
  outline: CloudOutlineItem[];
  requirements: CloudRequirementItem[];
  evaluationItems: Record<string, unknown>[];
  riskItems: Record<string, unknown>[];
  documentBlocks?: Record<string, unknown>;
  documentStructure?: Record<string, unknown>;
  bidFormatSpec?: Record<string, unknown>;
  submissionStructure?: SubmissionStructure;
  parseReviewReport?: Record<string, unknown>;
  agentResults?: Record<string, unknown>[];
  provider?: string;
  warnings: string[];
}

export type CloudRunStatus =
  | "created"
  | "uploading"
  | "uploaded"
  | "parsing"
  | "outline_ready"
  | "waiting_outline_confirmation"
  | "generating_sections"
  | "reviewing"
  | "waiting_export_confirmation"
  | "completed"
  | "failed"
  | "cancelled"
  | string;

export interface CloudBidRun {
  runId: string;
  workspaceId: string;
  projectId: string;
  provider?: string;
  providerSource?: string;
  backendPid?: number;
  status: CloudRunStatus;
  phase: string;
  progress: number;
  currentAgent?: string;
  currentAgentStatus?: string;
  createdAt: string;
  updatedAt: string;
  difyTaskId?: string;
  difyWorkflowRunId?: string;
  parseBundleId?: string;
  parseBundleArtifact?: string;
  workflowType: string;
  originalFilename: string;
  inputArtifact?: string;
  outputArtifact?: string;
  error?: string | null;
  materialized?: boolean;
  result?: CloudParseResult | null;
}

export interface ParseBundle {
  parseBundleId: string;
  schemaVersion: string;
  provider: string;
  status: string;
  projectId: string;
  workspaceId: string;
  createdAt: string;
  updatedAt: string;
  artifactPath?: string;
  sourceFile: Record<string, unknown>;
  documentBlocks: Record<string, unknown>;
  documentStructure: Record<string, unknown>;
  projectMeta: Record<string, unknown>;
  requirements: Record<string, unknown>[];
  evaluationItems: Record<string, unknown>[];
  bidFormatSpec: Record<string, unknown>;
  submissionStructure: SubmissionStructure;
  rawOutline: Record<string, unknown>[];
  riskItems: Record<string, unknown>[];
  parseReviewReport: Record<string, unknown>;
  artifacts: Record<string, unknown>[];
  warnings: string[];
  agentResults: Record<string, unknown>[];
  rawProviderResult: Record<string, unknown>;
}
