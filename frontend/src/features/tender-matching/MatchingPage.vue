<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  ClipboardCheck,
  Download,
  FileCheck2,
  FileText,
  History,
  Info,
  ListChecks,
  PenLine,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  Table2,
  Upload,
  Wrench,
  X,
  Archive
} from "lucide-vue-next";
import { ElMessage } from "element-plus";
import { api } from "../../services/api";
import type { MaterialFact, MatchSummary, ReasoningRow, ReviewOutput, Workspace } from "../../shared/types";

const props = defineProps<{
  workspace: Workspace | null;
  workspaces: Workspace[];
  busy: boolean;
  importFile: File | null;
  importForm: {
    name: string;
    text: string;
  };
  inputKind: "notice" | "tender_document";
  config: Record<string, any>;
}>();

const emit = defineEmits<{
  evaluate: [];
  fileSelected: [file: File | null];
  "update:input-kind": [kind: "notice" | "tender_document"];
  "save-config": [config: Record<string, unknown>];
  "update-fine-status": [payload: { rowId: string; status: string; uncertaintyReason?: string; responseValue?: string; materialBindings?: Array<Record<string, unknown>> }];
  openWorkspace: [];
  reanalyze: [];
  selectWorkspace: [workspace: Workspace];
}>();

type ModuleKey =
  | "technical"
  | "qualification"
  | "commercial"
  | "evaluation"
  | "contract"
  | "rejection"
  | "template"
  | "project_info";
type StatusTone = "success" | "warning" | "danger" | "info";
type AnalysisPack = Record<string, any>;
type PackItem = Record<string, any>;
type ProjectInfoItem = { key: string; label: string; value: string };
type DisplayRow = ReasoningRow & Record<string, any> & {
  id: string;
  moduleKey: ModuleKey;
  isPackItem?: boolean;
  statusEditable?: boolean;
  rawItem?: PackItem;
  source?: PackItem;
  sourceSegmentIds?: string[];
  handlingMode?: string;
};
type SourceCell = Record<string, any> & {
  text?: string;
  gridSpan?: string | number;
  vMerge?: string;
};
type SourceBlock = Record<string, any> & {
  type?: "paragraph" | "table" | string;
  text?: string;
  tableId?: string;
  rows?: Array<Record<string, any> & { cells?: SourceCell[] }>;
  sourceLocator?: string;
};
type SourceSectionNode = Record<string, any> & {
  sectionId: string;
  title?: string;
  level?: number;
  parentId?: string;
  sectionPath?: string[];
  sourceLocator?: string;
  startLocator?: string;
  endLocator?: string;
  templateId?: string;
  blocks?: SourceBlock[];
  children?: SourceSectionNode[];
  depth?: number;
  hasChildren?: boolean;
};
type SourceView = Record<string, any> & {
  mode?: string;
  displayMode?: string;
  sectionCount?: number;
  rootSectionCount?: number;
  segmentCount?: number;
  tableCount?: number;
  blockCount?: number;
  sections?: SourceSectionNode[];
};

const fileInput = ref<HTMLInputElement | null>(null);
const resultArea = ref<HTMLElement | null>(null);
const activeModule = ref<ModuleKey>("technical");
const activeRowId = ref<string | null>(null);
const detailDrawerOpen = ref(false);

// ---- 补充证明材料 ----
type SupplementField = { key: string; label: string; type: "text" | "textarea" | "date" | "number" };
const SUPPLEMENT_FIELDS_BY_CLASS: Record<string, SupplementField[]> = {
  qualification_certificate: [
    { key: "qualification_name", label: "资质名称", type: "text" },
    { key: "qualification_type", label: "资质类型", type: "text" },
    { key: "certificate_no", label: "证书编号", type: "text" },
    { key: "level", label: "等级", type: "text" },
    { key: "issuer", label: "发证机构", type: "text" },
    { key: "valid_from", label: "有效期起", type: "date" },
    { key: "valid_until", label: "有效期至", type: "date" },
  ],
  credential: [
    { key: "credential_name", label: "凭证名称", type: "textarea" },
    { key: "credential_type", label: "凭证类型", type: "text" },
    { key: "registration_no", label: "登记号/编号", type: "text" },
    { key: "issuer", label: "发证机构", type: "text" },
    { key: "valid_until", label: "有效期至", type: "date" },
  ],
  performance_case: [
    { key: "case_name", label: "案例名称", type: "text" },
    { key: "customer_name", label: "客户名称", type: "text" },
    { key: "industry", label: "行业", type: "text" },
    { key: "contract_amount", label: "合同金额(万)", type: "number" },
    { key: "completion_date", label: "完成日期", type: "date" },
  ],
  staff_member: [
    { key: "name", label: "姓名", type: "text" },
    { key: "role", label: "角色", type: "text" },
    { key: "title", label: "职称", type: "text" },
    { key: "specialty", label: "专业方向", type: "text" },
    { key: "years_of_experience", label: "从业年限", type: "number" },
  ],
  financial_evidence: [
    { key: "evidence_type", label: "证明类型", type: "text" },
    { key: "issued_by", label: "出具机构", type: "text" },
    { key: "period_start", label: "期间起", type: "date" },
    { key: "period_end", label: "期间止", type: "date" },
    { key: "revenue", label: "营收(万)", type: "number" },
    { key: "asset_amount", label: "资产总额(万)", type: "number" },
  ],
  technical_product: [
    { key: "product_name", label: "产品名称", type: "text" },
    { key: "product_type", label: "产品类型", type: "text" },
    { key: "category", label: "类别", type: "text" },
    { key: "brand", label: "品牌", type: "text" },
    { key: "model", label: "型号", type: "text" },
    { key: "description", label: "功能描述", type: "textarea" },
  ],
  technical_parameter: [
    { key: "description", label: "参数描述", type: "textarea" },
  ],
};
const SUPPLEMENT_TITLE_FIELD: Record<string, string> = {
  qualification_certificate: "qualification_name",
  credential: "credential_name",
  performance_case: "case_name",
  staff_member: "name",
  financial_evidence: "evidence_type",
  technical_product: "product_name",
  technical_parameter: "description",
};
const SUPPLEMENT_CLASSES_BY_MODULE: Record<string, Array<{ name: string; label: string }>> = {
  technical: [
    { name: "technical_product", label: "技术产品" },
    { name: "technical_parameter", label: "技术参数" },
    { name: "credential", label: "证照凭证" },
  ],
  qualification: [
    { name: "qualification_certificate", label: "资质证书" },
    { name: "credential", label: "证照凭证" },
    { name: "performance_case", label: "类似业绩" },
    { name: "staff_member", label: "项目人员" },
    { name: "financial_evidence", label: "财务证明" },
  ],
};

const supplementVisible = ref(false);
const supplementClass = ref("");
const supplementFields = ref<Record<string, any>>({});
const supplementFiles = ref<File[]>([]);
const supplementProductId = ref("");
const supplementCreateProduct = ref(false);
const newProductName = ref("");
const newProductType = ref("");
const newProductDescription = ref("");
const productOptions = ref<MaterialFact[]>([]);
const supplementSubmitting = ref(false);
const attachInput = ref<HTMLInputElement | null>(null);
const attachTarget = ref<{ className: string; objectId: string; label: string } | null>(null);
const attachSubmitting = ref(false);

const supplementClasses = computed(() =>
  SUPPLEMENT_CLASSES_BY_MODULE[activeModule.value] || SUPPLEMENT_CLASSES_BY_MODULE.technical
);
const supplementClassFields = computed(() =>
  SUPPLEMENT_FIELDS_BY_CLASS[supplementClass.value] || []
);
const needsProduct = computed(() =>
  ["technical_parameter", "credential"].includes(supplementClass.value)
);

function factId(fact: MaterialFact) {
  return String(fact.id || "").split(":").pop() || String(fact.id || "");
}

function productOptionLabel(fact: MaterialFact) {
  const name = String(fact.productName || fact.product_name || "");
  const id = factId(fact);
  return name ? `${name}（${id}）` : id;
}

async function openSupplement() {
  supplementClass.value = "";
  supplementFields.value = {};
  supplementFiles.value = [];
  supplementProductId.value = "";
  supplementCreateProduct.value = false;
  newProductName.value = "";
  newProductType.value = "";
  newProductDescription.value = "";
  if (activeModule.value === "technical" || activeModule.value === "qualification") {
    try {
      productOptions.value = await api.getFacts("technical_product", 500);
    } catch {
      productOptions.value = [];
    }
  }
  supplementVisible.value = true;
}

function onSupplementFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  supplementFiles.value = Array.from(input.files || []);
}

async function submitSupplement() {
  if (!props.workspace?.id || !activeRow.value) return;
  const cls = supplementClass.value;
  if (!cls) { ElMessage.warning("请选择材料类别"); return; }
  if (!supplementFiles.value.length) { ElMessage.warning("请选择证明文件"); return; }
  supplementSubmitting.value = true;
  try {
    const fields: Record<string, unknown> = {};
    for (const f of supplementClassFields.value) {
      const v = supplementFields.value[f.key];
      if (v !== undefined && v !== null && v !== "") fields[f.key] = v;
    }
    if (needsProduct.value && supplementCreateProduct.value) {
      const prodName = newProductName.value.trim();
      if (!prodName) { ElMessage.warning("请填写新产品名称"); return; }
      const prodFields: Record<string, unknown> = { product_name: prodName };
      if (newProductType.value.trim()) prodFields["product_type"] = newProductType.value.trim();
      if (newProductDescription.value.trim()) prodFields["description"] = newProductDescription.value.trim();
      const prod = await api.createFactWithEvidence("technical_product", prodFields, []);
      fields["product_id"] = prod.objectId;
    } else if (needsProduct.value && supplementProductId.value) {
      fields["product_id"] = supplementProductId.value;
    } else if (needsProduct.value) {
      ElMessage.warning("请选择所属产品或新建产品"); return;
    }
    const result = await api.createFactWithEvidence(cls, fields, supplementFiles.value);
    const titleField = SUPPLEMENT_TITLE_FIELD[cls] || "";
    const label = String(fields[titleField] || supplementClasses.value.find((c) => c.name === cls)?.label || "");
    emit("update-fine-status", {
      rowId: activeRow.value.id,
      status: "满足",
      materialBindings: [{
        rid: result.rid,
        className: cls,
        objectId: result.objectId,
        label,
        evidencePath: result.evidencePath || "",
      }],
    });
    ElMessage.success("已补充材料并关联到本条要求");
    supplementVisible.value = false;
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    supplementSubmitting.value = false;
  }
}

function requestAttachParameter(param: { parameterId?: string; description?: string }) {
  if (!param.parameterId) return;
  attachTarget.value = {
    className: "technical_parameter",
    objectId: param.parameterId,
    label: param.description || param.parameterId,
  };
  attachInput.value?.click();
}

async function onAttachFileChange(event: Event) {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files || []);
  input.value = "";
  if (!files.length || !attachTarget.value || !props.workspace?.id || !activeRow.value) return;
  const target = attachTarget.value;
  attachSubmitting.value = true;
  try {
    const result = await api.attachEvidenceToFact(target.className, target.objectId, files);
    emit("update-fine-status", {
      rowId: activeRow.value.id,
      status: "满足",
      materialBindings: [{
        rid: result.rid,
        className: target.className,
        objectId: target.objectId,
        label: target.label,
        evidencePath: result.evidencePath || "",
      }],
    });
    ElMessage.success("已上传证据并关联到本条要求");
  } catch (e) {
    ElMessage.error((e as Error).message);
  } finally {
    attachSubmitting.value = false;
    attachTarget.value = null;
  }
}

// ---- 补充证明材料 end ----

const collapsedSourceSections = ref<Set<string>>(new Set());const searchText = ref("");
const statusFilter = ref("全部");
const extractionMode = ref<"builtin" | "workbuddy_agent_mcp">("builtin");
const fileName = computed(() => props.importFile?.name || "");
const historyWorkspaces = computed(() => [...props.workspaces].reverse());
const auditRecords = computed(() => props.workspace?.auditRecords || []);
const latestAuditRecord = computed(() => auditRecords.value[auditRecords.value.length - 1] || null);

const fineReview = computed(() => props.workspace?.reasoning.fineReview || null);
const fineRows = computed(() => fineReview.value?.rows || []);
const baseRows = computed(() => props.workspace?.reasoning.rows || []);
const allRows = computed(() => (fineRows.value.length ? fineRows.value : baseRows.value));
const fineSummary = computed(() => fineReview.value?.summary || props.workspace?.reasoning.summary || null);
const analysisPack = computed<AnalysisPack | null>(() => {
  const pack =
    fineReview.value?.analysisPack ||
    fineReview.value?.extraction?.analysisPack ||
    props.workspace?.reasoning.analysisPack ||
    props.workspace?.reasoning.extraction?.analysisPack ||
    null;
  return pack && typeof pack === "object" && Object.keys(pack).length ? pack : null;
});
const documentMap = computed<Record<string, any> | null>(() => {
  const candidates = [
    props.workspace?.reasoning.documentMap,
    props.workspace?.reasoning.extraction?.documentMap,
    fineReview.value?.documentMap,
    fineReview.value?.extraction?.documentMap,
    analysisPack.value?.documentMap
  ];
  const value = candidates.find((item) => item && typeof item === "object");
  return (value as Record<string, any> | undefined) || null;
});
const mapStats = computed(() => {
  const map = documentMap.value || {};
  const rawStats = (
    map.mapStats ||
    analysisPack.value?.mapStats ||
    {}
  ) as Record<string, any>;
  const coverage = (
    map.coverage ||
    rawStats.coverage ||
    {}
  ) as Record<string, any>;
  const coveredSegmentIds = Array.isArray(coverage.coveredSegmentIds)
    ? [...new Set(coverage.coveredSegmentIds.map((value: unknown) => String(value || "").trim()).filter(Boolean))]
    : [];
  const unclassifiedSegmentIds = Array.isArray(coverage.unclassifiedSegmentIds)
    ? [...new Set(coverage.unclassifiedSegmentIds.map((value: unknown) => String(value || "").trim()).filter(Boolean))]
    : [];
  const sectionSegmentIds = Array.isArray(map.sections)
    ? [
        ...new Set(
          map.sections.flatMap((section: Record<string, any>) => [
            ...(Array.isArray(section.sourceSegmentIds) ? section.sourceSegmentIds : []),
            ...(Array.isArray(section.ranges)
              ? section.ranges.flatMap((range: Record<string, any>) =>
                  Array.isArray(range.segmentIds) ? range.segmentIds : []
                )
              : [])
          ])
        )
      ].filter(Boolean)
    : [];
  const sectionCount = Number(
    rawStats.sectionCount ||
      (Array.isArray(map.sections) ? map.sections.length : 0)
  );
  const ignoredSectionCount = Number(
    rawStats.ignoredSectionCount ||
      (Array.isArray(map.ignoredSections) ? map.ignoredSections.length : 0)
  );
  const segmentCount = Number(
    rawStats.segmentCount ||
      coveredSegmentIds.length + unclassifiedSegmentIds.length ||
      sectionSegmentIds.length ||
      0
  );
  const highRiskHitCount = Number(
    coverage.highRiskHitCount || rawStats.highRiskHitCount || 0
  );
  const highRiskCoveredCount = Number(
    coverage.highRiskCoveredCount || rawStats.highRiskCoveredCount || 0
  );
  return {
    ...rawStats,
    sectionCount,
    ignoredSectionCount,
    segmentCount,
    coveredSegmentCount: coveredSegmentIds.length,
    unclassifiedSegmentCount: unclassifiedSegmentIds.length,
    coverageRate: segmentCount
      ? Math.round((coveredSegmentIds.length / segmentCount) * 100)
      : null,
    highRiskHitCount,
    highRiskCoveredCount
  };
});
const reviewOutput = computed<ReviewOutput | null>(() => {
  const candidates = [
    props.workspace?.reasoning.reviewOutput,
    fineReview.value?.reviewOutput,
    fineReview.value?.extraction?.reviewOutput,
    props.workspace?.reasoning.extraction?.reviewOutput,
    props.workspace?.reasoning.analysisPack?.reviewOutput,
    analysisPack.value?.reviewOutput
  ];
  const value = candidates.find((item) => item && typeof item === "object") as ReviewOutput | undefined;
  return value?.modules && typeof value.modules === "object" ? value : null;
});
const usesCanonicalOutput = computed(() => Boolean(reviewOutput.value?.modules));
const hasFineReview = computed(() => Boolean(fineReview.value || reviewOutput.value));

const STATUS_OPTIONS: Record<string, string[]> = {
  evidence: ["满足", "部分满足", "待补材料", "不满足"],
  rejection: ["待核查", "已确认安全", "有风险"],
  writing: ["待编制", "已编制"],
  display: ["已提取", "待确认"]
};

const projectInfoLabels: Record<string, string> = {
  projectName: "项目名称",
  projectCode: "项目编号",
  category: "项目分类",
  region: "项目地区",
  tendererName: "采购人",
  agencyName: "代理机构",
  procurementMethod: "采购方式",
  noticeType: "公告类型",
  publishedDate: "发布日期",
  tenderNoticeDate: "公告日期",
  noticeDate: "公告日期",
  registrationPeriod: "获取文件 / 报名时间",
  registrationDeadline: "报名截止时间",
  budgetAmount: "预算金额",
  maxPrice: "最高限价",
  serviceType: "项目类型",
  serviceLocation: "项目地点",
  bidDeadline: "投标截止时间",
  openingTime: "开标时间",
  bidLocation: "投标地点",
  openingLocation: "开标地点",
  projectDuration: "工期 / 履约期限",
  warrantyRequirement: "质保要求",
  bidCopyRequirement: "投标文件份数",
  packagingRequirement: "封装要求",
  signatureSealRequirement: "签字盖章要求",
  jointVentureRequirement: "联合体要求"
};

const moduleMeta: Array<{ key: ModuleKey; label: string; description: string; icon: typeof ClipboardCheck }> = [
  { key: "technical", label: "技术要求", description: "技术、产品和服务能力核验", icon: Wrench },
  { key: "qualification", label: "资格要求", description: "资质、业绩、人员和信用证明", icon: FileCheck2 },
  { key: "rejection", label: "废标项", description: "无效、否决和不予评审条件", icon: AlertTriangle },
  { key: "commercial", label: "商务要求", description: "报价、付款、交付和一般商务条件", icon: ListChecks },
  { key: "evaluation", label: "评分标准", description: "评分项、分值和证明材料", icon: Table2 },
  { key: "contract", label: "合同范围", description: "识别后暂不进入投标匹配", icon: Archive },
  { key: "project_info", label: "项目基础信息", description: "项目身份、时间、费用和递交要求", icon: Info },
  { key: "template", label: "响应模板", description: "投标函、偏离表和附件格式", icon: FileText }
];

const matchingModuleMeta = moduleMeta.filter(
  (item) => item.key !== "contract" && item.key !== "project_info"
);

const ROUTE_LABELS: Record<string, string> = {
  project_info: "项目信息",
  company_match: "企业材料核验",
  rejection_check: "否决风险核查",
  compliance_confirm: "合规确认",
  fill: "商务响应",
  display: "展示信息",
  database_match: "企业材料核验",
  risk_check: "风险核查",
  writing_task: "写作输入",
  manual_confirm: "人工确认",
  manual_fill: "人工填写",
  checklist: "清单确认",
  writing_context: "方案编制上下文",
  display_only: "仅展示参考"
};

const TARGET_MODULE_LABELS: Record<string, string> = {
  technical: "技术要求",
  qualification: "资格审查",
  commercial: "商务要求",
  evaluation: "评分标准",
  contract: "合同范围",
  template: "模板部分",
  project_info: "项目基础信息",
  rejection: "废标项"
};

const MATCH_DOMAIN_LABELS: Record<string, string> = {
  technical: "技术产品服务",
  qualification: "资质证书",
  performance: "类似业绩",
  personnel: "项目人员",
  financial: "财务能力",
  credit: "信用记录",
  region: "地域要求"
};

Object.assign(ROUTE_LABELS, {
  technical_processor: "技术处理",
  qualification_processor: "资格处理",
  display_processor: "文档展示",
  evaluation_processor: "评分处理",
  template_processor: "模板定位",
  rejection_processor: "废标核查",
  ignored_contract: "合同范围已忽略"
});

const HANDLING_MODE_LABELS: Record<string, string> = {
  display: "分析包展示",
  database_match: "企业资料匹配",
  risk_check: "风险核查",
  writing_task: "写作输入",
  manual_confirm: "人工确认",
  manual_fill: "人工填写",
  checklist: "清单确认"
};

const REVIEW_STATUS_LABELS: Record<string, string> = {
  not_required: "无需人工复核",
  pending: "待人工复核",
  confirmed: "已复核",
  rejected: "复核退回"
};

const GRANULARITY_LABELS: Record<string, string> = {
  section: "章节",
  clause: "条款",
  table_row: "表格行",
  field: "字段"
};

const BASIC_PROJECT_INFO_KEYS = [
  "projectName",
  "projectCode",
  "tendererName",
  "agencyName",
  "budgetAmount",
  "bidDeadline"
];

const projectInfoExpanded = ref(false);

const projectInfoItems = computed<ProjectInfoItem[]>(() => {
  const merged = new Map<string, ProjectInfoItem>();
  const hiddenProjectKeys = new Set([
    "id",
    "workspaceId",
    "projectId",
    "status",
    "score",
    "relevanceScore",
    "scoreBreakdown",
    "scoreVersion",
    "matchReasons",
    "matchedBusinessDirections",
    "matchedScenarios",
    "matchedProducts",
    "matchedCapabilities",
    "matchedKeywords",
    "searchProfile",
    "crawlMeta",
    "analysisText",
    "analysisPreview",
    "decision",
    "nextStage",
    "shouldAnalyze",
    "read",
    "reminderStatus",
    "remindedAt",
    "notificationChannels",
    "downloadStatus",
    "domain",
    "processingMode",
    "ignored",
    "targetModule",
    "targetModuleKey",
    "primaryRoute",
    "contract",
    "contractTerms",
    "contractClause"
  ]);
  const add = (key: unknown, label: unknown, value: unknown) => {
    const normalizedKey = String(key || "").trim();
    const normalizedValue = String(value ?? "").trim();
    if (!normalizedKey || !normalizedValue || merged.has(normalizedKey)) return;
    merged.set(normalizedKey, {
      key: normalizedKey,
      label: String(label || projectInfoLabels[normalizedKey] || "项目信息").trim(),
      value: normalizedValue
    });
  };

  const packItems = [
    ...canonicalItemsForModule("project_info")
  ];
  packItems
    .filter((item): item is PackItem => item && typeof item === "object" && !isContractScoped(item))
    .forEach((item, index) => {
      add(
        item.field || item.id || item.fieldId || `project-${index + 1}`,
        item.label || item.title || item.field || "项目信息",
        item.value ?? item.text ?? item.rawText
      );
    });

  const project = {
    ...(props.workspace?.project || {}),
    ...(props.workspace?.reasoning.project || {})
  } as Record<string, unknown>;
  const aliases: Record<string, string[]> = {
    tendererName: ["tendererName", "purchaser", "tenderer"],
    agencyName: ["agencyName", "agency"],
    projectDuration: ["projectDuration", "servicePeriod", "deliveryPeriod"]
  };
  Object.entries(projectInfoLabels).forEach(([key, label]) => {
    const value = (aliases[key] || [key])
      .map((alias) => project[alias])
      .find((candidate) => candidate !== undefined && candidate !== null && candidate !== "");
    add(key, label, value);
  });
  Object.entries(project).forEach(([key, value]) => {
    if (
      hiddenProjectKeys.has(key) ||
      Object.prototype.hasOwnProperty.call(projectInfoLabels, key) ||
      key === "title" ||
      value === undefined ||
      value === null ||
      typeof value === "object"
    ) {
      return;
    }
    add(key, key, value);
  });
  add("projectName", projectInfoLabels.projectName, props.workspace?.title);

  return [...merged.values()];
});

const basicProjectInfoItems = computed<ProjectInfoItem[]>(() => {
  const byKey = new Map(projectInfoItems.value.map((item) => [item.key, item]));
  return BASIC_PROJECT_INFO_KEYS
    .map((key) => byKey.get(key))
    .filter((item): item is ProjectInfoItem => Boolean(item));
});

const extraProjectInfoItems = computed<ProjectInfoItem[]>(() => {
  const basicKeys = new Set(basicProjectInfoItems.value.map((item) => item.key));
  return projectInfoItems.value.filter((item) => !basicKeys.has(item.key));
});

const visibleProjectInfoItems = computed<ProjectInfoItem[]>(() =>
  projectInfoExpanded.value ? projectInfoItems.value : basicProjectInfoItems.value
);

function normalizedText(row: ReasoningRow | DisplayRow): string {
  const displayRow = row as DisplayRow;
  return [
    displayRow.title,
    displayRow.label,
    displayRow.value,
    displayRow.field,
    row.targetModule,
    row.targetModuleLabel,
    row.primaryRoute,
    displayRow.handlingMode,
    row.matchDomain,
    row.kind,
    row.section,
    row.requirement,
    row.normalizedRequirement,
    row.rawText,
    row.sourceText
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function primaryRouteOf(row: ReasoningRow | DisplayRow): string {
  const displayRow = row as DisplayRow;
  const explicit = String(row.primaryRoute || displayRow.source?.primaryRoute || "").trim();
  if (explicit) return explicit;
  return {
    database_match: "company_match",
    risk_check: "rejection_check",
    writing_task: "writing_context",
    manual_fill: "fill",
    manual_confirm: "display",
    checklist: "display",
    display: "display"
  }[String(displayRow.handlingMode || "").trim()] || "";
}

function targetModuleKeyOf(row: ReasoningRow | DisplayRow): string {
  const displayRow = row as DisplayRow;
  return String(displayRow.moduleKey || row.targetModule || displayRow.domain || "").trim().toLowerCase();
}

function targetModuleLabel(row: ReasoningRow | DisplayRow): string {
  const displayRow = row as DisplayRow;
  const key = targetModuleKeyOf(row);
  return String(
    displayRow.targetModuleLabel ||
      TARGET_MODULE_LABELS[key] ||
      row.targetModule ||
      "未指定模块"
  );
}

function routeLabel(row: ReasoningRow | DisplayRow): string {
  return ROUTE_LABELS[primaryRouteOf(row)] || "未指定路由";
}

function matchDomainLabel(row: ReasoningRow | DisplayRow): string {
  const domain = String(row.matchDomain || "").trim().toLowerCase();
  return MATCH_DOMAIN_LABELS[domain] || domain;
}

function handlingModeOf(row: ReasoningRow | DisplayRow): string {
  const displayRow = row as DisplayRow;
  return String(displayRow.handlingMode || displayRow.source?.handlingMode || "").trim();
}

function handlingModeLabel(row: ReasoningRow | DisplayRow): string {
  const mode = handlingModeOf(row);
  return HANDLING_MODE_LABELS[mode] || mode || "未指定处理方式";
}

function reviewStatusLabel(row: ReasoningRow | DisplayRow): string {
  const displayRow = row as DisplayRow;
  const status = String(displayRow.reviewStatus || "").trim();
  if (status) return REVIEW_STATUS_LABELS[status] || status;
  if (displayRow.needsHumanReview === true || row.reviewRequired === true) return "待人工复核";
  if (row.reviewMode === "assisted" || row.reviewMode === "manual") return "待人工复核";
  return "无需人工复核";
}

function granularityLabel(row: ReasoningRow | DisplayRow): string {
  const granularity = String(row.granularity || "").trim();
  return GRANULARITY_LABELS[granularity] || granularity || "未提供";
}

function routeTone(row: ReasoningRow | DisplayRow): StatusTone {
  const route = primaryRouteOf(row);
  if (["rejection_check", "risk_check", "rejection_processor"].includes(route)) return "danger";
  if (["company_match", "technical_processor", "qualification_processor"].includes(route)) return "success";
  if (["manual_confirm", "manual_fill", "checklist"].includes(handlingModeOf(row))) return "warning";
  return "info";
}

function reviewTone(row: ReasoningRow | DisplayRow): StatusTone {
  const displayRow = row as DisplayRow;
  if (String(displayRow.reviewStatus || "") === "rejected") return "danger";
  if (
    displayRow.needsHumanReview === true ||
    row.reviewRequired === true ||
    ["pending", "assisted", "manual"].includes(String(displayRow.reviewStatus || row.reviewMode || ""))
  ) {
    return "warning";
  }
  return "success";
}

function sourceFromItem(item: PackItem): PackItem {
  const sourceCandidate = item.source || item.sourceRefs;
  const source = sourceCandidate && typeof sourceCandidate === "object" ? sourceCandidate : {};
  return {
    ...source,
    sourceLocator: source.sourceLocator || item.sourceLocator || item.locator || "",
    sectionId: source.sectionId || item.sectionId || "",
    sectionPath: source.sectionPath || item.sectionPath || [],
    segmentIds: source.segmentIds || item.sourceSegmentIds || item.segmentIds || [],
  };
}

function textFromItem(item: PackItem): string {
  return String(
    item.requirement ||
      item.normalizedRequirement ||
      item.normalizedText ||
      item.text ||
      item.rawText ||
      item.value ||
      item.title ||
      item.label ||
      item.field ||
      ""
  ).trim();
}

function kindForPackItem(moduleKey: ModuleKey, item: PackItem): string {
  if (moduleKey === "technical") return "技术要求";
  if (moduleKey === "qualification") return "资格要求";
  if (moduleKey === "commercial") return "商务要求";
  if (moduleKey === "evaluation") return "评分标准";
  if (moduleKey === "contract") return "合同范围";
  if (moduleKey === "template") return "响应模板";
  if (moduleKey === "project_info") return "项目基础信息";
  if (moduleKey === "rejection") return "废标风险";
  return String(item.kind || "条款");
}

function defaultHandlingMode(moduleKey: ModuleKey): string {
  return {
    technical: "database_match",
    qualification: "database_match",
    commercial: "manual_confirm",
    evaluation: "display",
    contract: "display",
    rejection: "risk_check",
    template: "writing_task",
    project_info: "display"
  }[moduleKey];
}

function handlingModeForItem(item: PackItem, moduleKey: ModuleKey): string {
  const mode = String(item.handlingMode || "");
  return mode === "match" ? defaultHandlingMode(moduleKey) : mode || defaultHandlingMode(moduleKey);
}

function defaultStatus(moduleKey: ModuleKey, item: PackItem, matched?: ReasoningRow): string {
  if (item.status) return String(item.status);
  if (matched?.status) return matched.status;
  if (moduleKey === "technical" || moduleKey === "qualification") return "待澄清";
  if (moduleKey === "rejection") return "待核查";
  if (moduleKey === "template") return "待编制";
  if (moduleKey === "contract") return "已忽略";
  return "已提取";
}

function isContractScoped(value: unknown): boolean {
  const item = value && typeof value === "object" ? value as Record<string, any> : {};
  const source = item.source && typeof item.source === "object" ? item.source as Record<string, any> : {};
  const domain = String(item.domain || item.category || item.contentType || source.domain || "").trim().toLowerCase();
  const targetModule = String(item.targetModule || item.targetModuleKey || "").trim().toLowerCase();
  const primaryRoute = String(item.primaryRoute || source.primaryRoute || "").trim().toLowerCase();
  const processingMode = String(item.processingMode || source.processingMode || "").trim().toLowerCase();
  return (
    ["contract", "contract_terms", "contract_clause", "合同", "合同条款"].includes(domain) ||
    ["contract", "合同", "合同部分"].includes(targetModule) ||
    ["ignored_contract", "contract_ignore"].includes(primaryRoute) ||
    item.ignored === true ||
    processingMode === "ignore"
  );
}

function buildFineRowIndex(): Map<string, ReasoningRow> {
  const index = new Map<string, ReasoningRow>();
  allRows.value.filter((row) => !isContractScoped(row)).forEach((row) => {
    const keys = [
      row.requirementId,
      row.id,
      row.sourceLocator,
      row.normalizedRequirement,
      row.requirement,
      row.rawText,
    ]
      .map((value) => String(value || "").trim().toLowerCase())
      .filter(Boolean);
    keys.forEach((key) => {
      if (!index.has(key)) index.set(key, row);
    });
  });
  return index;
}

function matchingFineRow(item: PackItem, index: Map<string, ReasoningRow>): ReasoningRow | undefined {
  const keys = [
    item.requirementId,
    item.id,
    item.sourceLocator,
    item.normalizedRequirement,
    item.requirement,
    item.text,
    item.rawText,
  ]
    .map((value) => String(value || "").trim().toLowerCase())
    .filter(Boolean);
  const byKey = keys.map((key) => index.get(key)).find(Boolean);
  if (byKey) return byKey;
  const sourceIds = new Set<string>([
    ...(item.sourceSegmentIds || []),
    ...(item.segmentIds || []),
    ...(item.source?.segmentIds || []),
  ].map((value) => String(value)));
  if (!sourceIds.size) return undefined;
  return allRows.value.find((row) => {
    if (isContractScoped(row)) return false;
    const rowIds = [
      ...((row as any).sourceSegmentIds || []),
      ...((row as any).segmentIds || []),
    ].map((value) => String(value));
    return rowIds.some((value) => sourceIds.has(value));
  });
}

function compatibilityFields(matched?: ReasoningRow): PackItem {
  if (!matched) return {};
  const result: PackItem = {};
  const keys = [
    "status",
    "uncertaintyReason",
    "responseValue",
    "source",
    "sourceSegmentIds",
    "sourceLocator",
    "section",
    "rawText",
    "sourceText",
    "productName",
    "technicalAlignment",
    "gaps",
    "evidence",
    "evidenceItems",
    "matchedObjectIds",
    "matchedMaterialIds",
    "evidenceIds",
    "evidenceCoverageStatus",
    "risk",
    "riskLevel",
    "reason",
    "suggestion",
    "confidence",
    "manualStatus",
    "statusSource",
    "reviewStatus",
    "needsHumanReview",
    "reviewRequired",
    "reviewMode",
    "granularity",
    "handlingMode",
    "primaryRoute",
    "matchDomain"
  ];
  keys.forEach((key) => {
    const value = (matched as Record<string, any>)[key];
    if (value !== undefined && value !== null && value !== "") {
      result[key] = value;
    }
  });
  return result;
}

function packItemToRow(item: PackItem, moduleKey: ModuleKey, index: number, matched?: ReasoningRow): DisplayRow {
  const itemSource = sourceFromItem(item);
  const matchedSource = matched ? sourceFromItem(matched as PackItem) : {};
  const source = {
    ...matchedSource,
    ...itemSource,
    sourceLocator: itemSource.sourceLocator || matchedSource.sourceLocator || "",
    sectionId: itemSource.sectionId || matchedSource.sectionId || "",
    sectionPath: itemSource.sectionPath?.length
      ? itemSource.sectionPath
      : matchedSource.sectionPath || [],
    segmentIds: itemSource.segmentIds?.length
      ? itemSource.segmentIds
      : matchedSource.segmentIds || []
  };
  const requirement = textFromItem(item) || `${moduleMeta.find((meta) => meta.key === moduleKey)?.label || "条目"} ${index + 1}`;
  const id = String(
    matched?.id ||
      matched?.requirementId ||
      item.requirementId ||
      item.id ||
      item.fieldId ||
      item.templateId ||
      `${moduleKey}-${index + 1}`
  );
  return {
    ...compatibilityFields(matched),
    ...item,
    id,
    requirementId: String(matched?.requirementId || item.requirementId || id),
    moduleKey,
    targetModule: moduleKey,
    targetModuleLabel: String(
      TARGET_MODULE_LABELS[moduleKey] ||
        item.targetModuleLabel ||
        ""
    ),
    isPackItem: true,
    statusEditable: moduleKey === "technical" || moduleKey === "qualification" || moduleKey === "rejection",
    rawItem: item,
    source,
    sourceSegmentIds: item.sourceSegmentIds?.length
      ? item.sourceSegmentIds
      : item.segmentIds?.length
        ? item.segmentIds
        : source.segmentIds || [],
    kind: kindForPackItem(moduleKey, item),
    matchDomain: ["technical", "qualification"].includes(moduleKey)
      ? moduleKey
      : String(item.matchDomain || matched?.matchDomain || ""),
    requirement,
    rawText: String(item.rawText || item.text || item.sourceQuote || matched?.rawText || ""),
    sourceText: String(
      item.sourceText ||
        item.rawText ||
        item.text ||
        item.sourceQuote ||
        matched?.sourceText ||
        matched?.rawText ||
        ""
    ),
    sourceLocator: source.sourceLocator || "",
    section: item.section || (Array.isArray(source.sectionPath) ? source.sectionPath.join(" / ") : "") || matched?.section || "",
    handlingMode: handlingModeForItem(
      {
        ...item,
        handlingMode: item.handlingMode || (matched as Record<string, any> | undefined)?.handlingMode
      },
      moduleKey
    ),
    primaryRoute: String(
      item.primaryRoute ||
        matched?.primaryRoute ||
        (
          moduleKey === "technical" || moduleKey === "qualification"
            ? "company_match"
            : moduleKey === "rejection"
              ? "rejection_check"
              : moduleKey === "template"
                ? "writing_task"
                : "display"
        )
    ),
    status: defaultStatus(moduleKey, item, matched),
    evidence: String(
      item.evidence ||
        item.evidenceNeed ||
        item.requiredEvidence ||
        item.requiredMaterial ||
        matched?.evidence ||
        ""
    ),
    risk: String(item.risk || item.detail || matched?.risk || ""),
    reason: String(
      item.reason ||
        item.scoringRule ||
        item.description ||
        matched?.reason ||
        ""
    ),
    suggestion: String(item.suggestion || item.action || matched?.suggestion || ""),
  };
}

function canonicalItemsForModule(key: ModuleKey): PackItem[] {
  const modules = reviewOutput.value?.modules || {};
  const module = modules[key] || Object.values(modules).find(
    (item) => item && (
      item.targetModule === key ||
      item.domain === key
    )
  );
  return Array.isArray(module?.items)
    ? module.items.filter((item): item is PackItem => Boolean(item && typeof item === "object"))
    : [];
}

function rowsFromCanonical(key: ModuleKey): DisplayRow[] {
  const index = buildFineRowIndex();
  return canonicalItemsForModule(key)
    .filter((item) => key !== "contract" || item.ignored === true || reviewOutput.value?.modules?.[key]?.ignored === true)
    .map((item, itemIndex) => packItemToRow(
      item,
      key,
      itemIndex,
      matchingFineRow(item, index)
    ));
}

function rowsForModule(key: ModuleKey): DisplayRow[] {
  if (usesCanonicalOutput.value) {
    return rowsFromCanonical(key).filter(
      (row) => !isContractScoped(row) || key === "contract"
    );
  }
  return allRows.value
    .filter((row) => targetModuleKeyOf(row) === key)
    .map((row, index) => packItemToRow(row as PackItem, key, index, row))
    .filter((row) => !isContractScoped(row) || key === "contract");
}

const SOURCE_VIEW_MODULES: ModuleKey[] = ["commercial", "evaluation", "template", "rejection"];
const SOURCE_VIEW_LABELS: Record<string, { title: string; note: string; empty: string }> = {
  commercial: {
    title: "商务要求原文",
    note: "按原章节连续展示报价、付款、交付、验收、质保和其他商务条款；这里不拆成匹配卡片。",
    empty: "当前没有可展示的商务要求原文。"
  },
  evaluation: {
    title: "评分标准原文",
    note: "按评分办法原文展示分值、评分规则和证明材料要求；表格会尽量保留行列结构。",
    empty: "当前没有可展示的评分标准原文。"
  },
  template: {
    title: "响应模板章节",
    note: "按投标文件格式的大章节展示模板和表格，可下载源文件样式，也可进入标书工作台。",
    empty: "当前没有识别到响应模板章节。"
  },
  rejection: {
    title: "废标项原文",
    note: "仅展示全文正则召回的废标、无效、否决和不接受条款，不进入企业能力匹配。",
    empty: "当前没有召回废标项。"
  }
};

function modulePayload(key: ModuleKey): Record<string, any> {
  const map = analysisPack.value?.targetModuleMap;
  if (map && typeof map === "object" && !Array.isArray(map)) {
    const value = (map as Record<string, any>)[key];
    if (value && typeof value === "object") return value;
  }
  const targetModules = Array.isArray(analysisPack.value?.targetModules)
    ? analysisPack.value.targetModules
    : [];
  const fromList = targetModules.find(
    (item: Record<string, any>) => String(item?.targetModule || "") === key
  );
  if (fromList && typeof fromList === "object") return fromList;
  const canonicalModules = (reviewOutput.value?.modules || {}) as Record<string, any>;
  const canonical = canonicalModules[key];
  return canonical && typeof canonical === "object" ? canonical : {};
}

function sourceViewForModule(key: ModuleKey): SourceView | null {
  const view = modulePayload(key).sourceView;
  return view && typeof view === "object" ? view as SourceView : null;
}

function flattenSourceSections(
  sections: SourceSectionNode[] | undefined,
  depth = 0
): SourceSectionNode[] {
  return (sections || []).flatMap((section) => {
    const node = {
      ...section,
      sectionId: String(section.sectionId || ""),
      depth,
      hasChildren: Boolean(section.children?.length)
    };
    return [
      node,
      ...flattenSourceSections(section.children, depth + 1)
    ];
  });
}

function rowsAsSourceSections(key: ModuleKey): SourceSectionNode[] {
  const rows = rowsForModule(key);
  const groups = new Map<string, SourceSectionNode>();
  rows.forEach((row, index) => {
    const source = row.source || {};
    const path = Array.isArray(source.sectionPath)
      ? source.sectionPath.filter(Boolean).map((item) => String(item))
      : [];
    const title = path[path.length - 1] || row.section || targetModuleLabel(row);
    const groupKey = String(source.sectionId || row.section || path.join("/") || row.sourceLocator || `${key}-${index + 1}`);
    if (!groups.has(groupKey)) {
      groups.set(groupKey, {
        sectionId: groupKey,
        title: String(title || targetModuleLabel(row)),
        sectionPath: path.length ? path : [String(title || targetModuleLabel(row))],
        sourceLocator: String(row.sourceLocator || source.sourceLocator || ""),
        templateId: key === "template" ? String(row.templateId || row.id || "") : "",
        blocks: []
      });
    }
    const node = groups.get(groupKey);
    if (!node) return;
    const rawText = String(row.rawText || row.sourceText || row.requirement || "").trim();
    const text = key === "evaluation"
      ? [
          row.criterionNo || row.itemNo ? `序号：${row.criterionNo || row.itemNo}` : "",
          row.score !== undefined || row.maxScore !== undefined ? `分值：${row.score ?? row.maxScore}` : "",
          row.criterionName ? `评分项：${row.criterionName}` : "",
          row.scoringRule ? `评分规则：${row.scoringRule}` : "",
          row.requiredEvidence ? `证明材料：${row.requiredEvidence}` : "",
          rawText
        ].filter(Boolean).join("\n")
      : rawText;
    if (text) {
      node.blocks = [
        ...(node.blocks || []),
        {
          type: "paragraph",
          text,
          sourceLocator: String(row.sourceLocator || source.sourceLocator || "")
        }
      ];
    }
  });
  return [...groups.values()];
}

function sourceSectionsForModule(key: ModuleKey): SourceSectionNode[] {
  const viewSections = sourceViewForModule(key)?.sections;
  if (Array.isArray(viewSections) && viewSections.length) {
    if (key === "template") {
      // A template root is one reusable source artifact. Its child headings
      // are navigation metadata, not separate cards or download units.
      const root = viewSections[0];
      const blocks: SourceBlock[] = [];
      const collect = (node: SourceSectionNode) => {
        blocks.push(...(node.blocks || []));
        for (const child of node.children || []) collect(child);
      };
      collect(root);
      return [{
        ...root,
        children: [],
        hasChildren: false,
        blocks,
        depth: 0
      }];
    }
    return viewSections;
  }
  // Source-backed modules must never fall back to legacy requirement rows.
  // Those rows are clause-level compatibility data and make a complete
  // commercial/evaluation/template chapter look like scattered cards.
  if (SOURCE_VIEW_MODULES.includes(key)) {
    return [];
  }
  return rowsAsSourceSections(key);
}

function sourceDisplayCount(key: ModuleKey): number {
  const payload = modulePayload(key);
  const view = sourceViewForModule(key);
  if (key === "template" && Number(payload.templateCount || 0)) {
    return Number(payload.templateCount);
  }
  return Number(view?.rootSectionCount || sourceSectionsForModule(key).length || 0);
}

function sourceNodeSearchText(node: SourceSectionNode): string {
  const blockText = (node.blocks || []).flatMap((block) => [
    block.text || "",
    ...(block.rows || []).flatMap((row) =>
      (row.cells || []).map((cell) => cell.text || "")
    )
  ]);
  return [
    node.title || "",
    ...(node.sectionPath || []),
    ...blockText
  ].join(" ").toLowerCase();
}

function sourceNodeIsVisible(
  node: SourceSectionNode,
  nodes: SourceSectionNode[],
  collapsed: Set<string>
): boolean {
  const byId = new Map(nodes.map((item) => [item.sectionId, item]));
  let parentId = String(node.parentId || "");
  const visited = new Set<string>();
  while (parentId && !visited.has(parentId)) {
    visited.add(parentId);
    if (collapsed.has(parentId)) return false;
    const parent = byId.get(parentId);
    if (!parent) break;
    parentId = String(parent.parentId || "");
  }
  return true;
}

const sourceDisplayActive = computed(() => SOURCE_VIEW_MODULES.includes(activeModule.value));
const projectInfoDisplayActive = computed(() => activeModule.value === "project_info");
const sourceViewForActive = computed(() => sourceViewForModule(activeModule.value));
const sourceViewMetaForActive = computed(() => SOURCE_VIEW_LABELS[activeModule.value] || SOURCE_VIEW_LABELS.commercial);
const sourceViewStatsForActive = computed(() => {
  const view = sourceViewForActive.value;
  const sections = sourceSectionsForModule(activeModule.value);
  const tableCount = Number(
    view?.tableCount ||
      sections.reduce((total, section) =>
        total + (section.blocks || []).filter((block) => block.type === "table").length,
      0)
  );
  return {
    sectionCount: Number(
      activeModule.value === "template"
        ? view?.rootSectionCount || sections.length || 0
        : view?.sectionCount || sections.length || 0
    ),
    tableCount
  };
});
const sourceSectionsForActive = computed(() =>
  flattenSourceSections(sourceSectionsForModule(activeModule.value))
);
const filteredSourceSections = computed(() => {
  const nodes = sourceSectionsForActive.value;
  const query = searchText.value.trim().toLowerCase();
  const collapsed = collapsedSourceSections.value;
  if (!query) {
    return nodes.filter((node) => sourceNodeIsVisible(node, nodes, collapsed));
  }
  const byId = new Map(nodes.map((item) => [item.sectionId, item]));
  const keep = new Set<string>();
  nodes.forEach((node) => {
    if (!sourceNodeSearchText(node).includes(query)) return;
    let current: SourceSectionNode | undefined = node;
    const visited = new Set<string>();
    while (current && !visited.has(current.sectionId)) {
      visited.add(current.sectionId);
      keep.add(current.sectionId);
      current = byId.get(String(current.parentId || ""));
    }
  });
  return nodes.filter(
    (node) =>
      keep.has(node.sectionId) &&
      sourceNodeIsVisible(node, nodes, collapsed)
  );
});

function toggleSourceSection(node: SourceSectionNode) {
  if (!node.hasChildren) return;
  const next = new Set(collapsedSourceSections.value);
  if (next.has(node.sectionId)) {
    next.delete(node.sectionId);
  } else {
    next.add(node.sectionId);
  }
  collapsedSourceSections.value = next;
}

function sourceSectionCollapsed(node: SourceSectionNode): boolean {
  return collapsedSourceSections.value.has(node.sectionId);
}

function sourceCellColspan(cell: SourceCell): number | undefined {
  const span = Number(cell.gridSpan || 1);
  return Number.isFinite(span) && span > 1 ? span : undefined;
}

function sourceNodeTemplateRow(node: SourceSectionNode): DisplayRow {
  const sourceText = (node.blocks || [])
    .flatMap((block) => [
      block.text || "",
      ...(block.rows || []).flatMap((row) =>
        (row.cells || []).map((cell) => cell.text || "").join(" | ")
      )
    ])
    .filter(Boolean)
    .join("\n");
  return {
    id: node.templateId || node.sectionId,
    templateId: node.templateId || "",
    requirement: String(node.title || "响应模板"),
    rawText: sourceText,
    sourceText,
    sourceSegmentIds: node.sourceSegmentIds || [],
    sourceLocator: String(node.sourceLocator || ""),
    source: {
      sectionId: node.sectionId,
      sectionPath: node.sectionPath || [],
      segmentIds: node.sourceSegmentIds || [],
      sourceLocator: node.sourceLocator || ""
    }
  } as unknown as DisplayRow;
}

function downloadSourceSection(node: SourceSectionNode) {
  if (!node.templateId) return;
  downloadTemplateStyle(sourceNodeTemplateRow(node));
}

function openSourceSectionForWriting() {
  emit("openWorkspace");
}

const visibleAnalysisRows = computed(() =>
  matchingModuleMeta.flatMap((item) => rowsForModule(item.key))
);
const moduleRows = computed<DisplayRow[]>(() => rowsForModule(activeModule.value));
const activeRow = computed(() => {
  const row = moduleRows.value.find((item) => item.id === activeRowId.value);
  return row || moduleRows.value[0] || null;
});

const filteredModuleRows = computed(() => {
  const query = searchText.value.trim().toLowerCase();
  return moduleRows.value.filter((row) => {
    const matchesSearch = !query || normalizedText(row).includes(query);
    const matchesStatus = statusFilter.value === "全部" || displayStatus(row) === statusFilter.value;
    return matchesSearch && matchesStatus;
  });
});

const tabs = computed(() => matchingModuleMeta.map((item) => ({
  ...item,
  count: item.key === "project_info"
    ? projectInfoItems.value.length
    : SOURCE_VIEW_MODULES.includes(item.key)
    ? sourceDisplayCount(item.key)
    : rowsForModule(item.key).length
})));

const packStatusCounts = computed(() => {
  const counts: Record<string, number> = {};
  matchingModuleMeta
    .flatMap((item) => rowsForModule(item.key))
    .forEach((row) => {
      const status = displayStatus(row);
      counts[status] = (counts[status] || 0) + 1;
    });
  return counts;
});

const statusCounts = computed(() => {
  const summary = fineSummary.value;
  const counts = summary?.statusCounts || {};
  const sourceCounts = Object.keys(counts).length ? counts : packStatusCounts.value;
  return {
    satisfied: sourceCounts["满足"] || 0,
    partial: sourceCounts["部分满足"] || 0,
    pending: summary?.pendingMaterialCount || sourceCounts["待补材料"] || 0,
    notSatisfied: summary?.notSatisfiedCount || sourceCounts["不满足"] || 0,
    clarify: sourceCounts["待澄清"] || 0,
    untouched: sourceCounts["未触发"] || 0
  };
});

const recommendation = computed(() => {
  if (!props.workspace) return "待分析";
  if (fineReview.value?.hardBlockers?.length) return "不建议继续";
  if (fineReview.value?.recommendation) return fineReview.value.recommendation;
  if (analysisPack.value?.recommendation) return String(analysisPack.value.recommendation);
  if (analysisPack.value?.decision) return String(analysisPack.value.decision);
  return props.workspace.reasoning.eligibility?.recommendation || props.workspace.reasoning.decision || "待澄清";
});

const recommendationTone = computed<StatusTone>(() => {
  if (recommendation.value === "不建议继续") return "danger";
  if (["谨慎继续", "待澄清"].includes(recommendation.value)) return "warning";
  if (recommendation.value === "建议继续") return "success";
  return "info";
});

const missingMaterialCount = computed(() =>
  fineReview.value?.pendingMaterialList?.length ||
  fineSummary.value?.pendingMaterialCount ||
  [...rowsForModule("technical"), ...rowsForModule("qualification")]
    .filter((row) => ["待补材料", "不满足"].includes(displayStatus(row))).length ||
  0
);

const riskCount = computed(() =>
  fineReview.value?.hardBlockers?.length ||
  fineSummary.value?.rejectionRiskCount ||
  rowsForModule("rejection").filter((row) => ["有风险", "待核查"].includes(displayStatus(row))).length ||
  0
);

const rejectionPendingCount = computed(() =>
  fineSummary.value?.rejectionPendingCount ||
  rowsForModule("rejection").filter((row) => ["待核查", "待澄清"].includes(displayStatus(row))).length ||
  0
);

const manualReviewCount = computed(() =>
  visibleAnalysisRows.value
    .filter((row) =>
      row.needsHumanReview === true ||
      row.reviewStatus === "pending" ||
      row.reviewMode === "assisted" ||
      row.reviewMode === "manual" ||
      row.reviewRequired === true
    )
    .length
);

const analysisMethodLabel = computed(() => {
  const output = reviewOutput.value;
  if (!output) return "";
  const source = output.source || {};
  const fallbackMode = String(source.fallbackMode || "").toLowerCase();
  const agentStatus = String(source.agentStatus || "").toLowerCase();
  if (fallbackMode === "builtin" || agentStatus === "failed") {
    return "Agent 失败，已回退 builtin";
  }
  const producer = String(source.provider || output.producer || "").toLowerCase();
  if (producer.includes("workbuddy") || producer.includes("codebuddy")) {
    return "CodeBuddy Agent SDK + Tender Review MCP";
  }
  if (producer.includes("builtin")) {
    return "Builtin 模块化输出";
  }
  return "模块化审核输出";
});

const analyzedClauseCount = computed(() => {
  const moduleCount = Object.values(reviewOutput.value?.modules || {})
    .reduce((total, module) => total + Number(module?.count || module?.items?.length || 0), 0);
  return moduleCount || visibleAnalysisRows.value.length || 0;
});

const headerStatus = computed<{ label: string; tone: StatusTone }>(() => {
  if (riskCount.value) return { label: `有 ${riskCount.value} 条否决风险`, tone: "danger" };
  if (rejectionPendingCount.value) {
    return { label: `${rejectionPendingCount.value} 条否决项待核查`, tone: "warning" };
  }
  if (manualReviewCount.value) return { label: `需人工复核 ${manualReviewCount.value} 项`, tone: "warning" };
  if (missingMaterialCount.value) {
    return { label: `待补材料 ${missingMaterialCount.value} 项`, tone: "warning" };
  }
  return { label: "自动核验完成", tone: "success" };
});

const mapTagLabel = computed(() => {
  if (!documentMap.value) return "";
  const sectionCount = mapStats.value.sectionCount || 0;
  const segmentCount = mapStats.value.segmentCount || 0;
  const coveredSegmentCount = mapStats.value.coveredSegmentCount || 0;
  if (segmentCount) {
    return `文档地图 ${sectionCount} 章 / ${coveredSegmentCount}/${segmentCount} 段已覆盖`;
  }
  if (mapStats.value.highRiskHitCount) {
    return `文档地图 ${sectionCount} 章 / 高风险 ${mapStats.value.highRiskCoveredCount}/${mapStats.value.highRiskHitCount} 已覆盖`;
  }
  return `文档地图 ${sectionCount} 章`;
});

const headerTags = computed(() => [
  {
    label: hasFineReview.value ? "完整文件 · 细匹配" : "公告 · 资格预审",
    tone: "info" as StatusTone
  },
  {
    label: reviewOutput.value ? "统一审核输出" : "等待统一审核输出",
    tone: "info" as StatusTone
  },
  ...(mapTagLabel.value
    ? [{ label: mapTagLabel.value, tone: "info" as StatusTone }]
    : []),
  ...(reviewOutput.value?.modules?.contract?.ignored
    ? [{
        label: `合同范围已忽略 ${reviewOutput.value.modules.contract.count || 0} 条`,
        tone: "info" as StatusTone
      }]
    : []),
  {
    label: `已识别 ${analyzedClauseCount.value} 条`,
    tone: "info" as StatusTone
  },
  ...(analysisMethodLabel.value
    ? [{ label: analysisMethodLabel.value, tone: "info" as StatusTone }]
    : []),
  {
    label: headerStatus.value.label,
    tone: headerStatus.value.tone
  }
]);

const moduleEmptyText: Record<ModuleKey, string> = {
  technical: "当前结果没有识别到技术、产品或服务匹配条款",
  qualification: "当前结果没有识别到资格审查条款",
  commercial: "当前结果没有识别到商务要求",
  evaluation: "当前结果没有识别到评分标准",
  contract: "当前结果没有识别到需要暂时忽略的合同范围",
  template: "当前结果没有识别到响应模板定位",
  project_info: "当前结果没有提取到项目基础信息",
  rejection: "当前没有识别到否决性风险条款"
};

function statusTone(status: string): StatusTone {
  if (["满足", "已确认安全", "可接受", "已完成", "已编制", "已提取", "已忽略"].includes(status)) return "success";
  if (["不满足", "有风险"].includes(status)) return "danger";
  if (["部分满足", "待补材料", "待澄清", "待核查", "需确认", "待填写", "待编制", "待确认"].includes(status)) return "warning";
  return "info";
}

function statusTagType(status: string): "success" | "warning" | "danger" | "info" {
  return statusTone(status);
}

function displayStatus(row: ReasoningRow | DisplayRow): string {
  return row.status || "待澄清";
}

function statusOptionsForRow(row: ReasoningRow | DisplayRow): string[] {
  const moduleKey = targetModuleKeyOf(row);
  if (moduleKey === "rejection") {
    return STATUS_OPTIONS.rejection;
  }
  const mode = handlingModeOf(row);
  if (mode === "writing_task") return STATUS_OPTIONS.writing;
  if (["display", "manual_confirm", "manual_fill", "checklist"].includes(mode)) {
    return STATUS_OPTIONS.display;
  }
  if (primaryRouteOf(row) === "writing_context") return STATUS_OPTIONS.writing;
  if (["display", "display_only", "manual_confirm", "manual_fill", "checklist"].includes(primaryRouteOf(row))) {
    return STATUS_OPTIONS.display;
  }
  return STATUS_OPTIONS.evidence;
}

function sourceLabel(row: ReasoningRow | DisplayRow): string {
  const displayRow = row as DisplayRow;
  const source = displayRow.source || {};
  const path = Array.isArray(source.sectionPath) ? source.sectionPath.join(" / ") : "";
  const ids = Array.isArray(displayRow.sourceSegmentIds) ? displayRow.sourceSegmentIds.join("、") : "";
  return row.sourceLocator || source.sourceLocator || row.section || path || ids || row.module || "原文位置待识别";
}

function templateDownloadUrl(row: DisplayRow, format = "docx"): string {
  const item = row as Record<string, any>;
  const project = props.workspace?.project as Record<string, any> | undefined;
  const workspaceId = String(props.workspace?.id || "").trim();
  const templateId = String(item.templateId || item.id || "").trim();
  if (workspaceId && templateId) {
    return `/api/workspaces/${encodeURIComponent(workspaceId)}/templates/${encodeURIComponent(templateId)}/download?format=${encodeURIComponent(format)}`;
  }
  return [
    item.styleDownloadUrl,
    item.downloadUrl,
    item.templateUrl,
    item.fileUrl,
    item.sourceFileUrl,
    project?.downloadUrl,
    project?.sourceUrl
  ]
    .map((value) => String(value || "").trim())
    .find(Boolean) || "";
}

function downloadTemplateStyle(row: DisplayRow) {
  const url = templateDownloadUrl(row, "docx");
  const filename = `${firstLine(row.requirement) || "响应模板"}-样式`;
  if (url) {
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    return;
  }
  const sourceText = String((row as Record<string, any>).downloadText || row.rawText || "").trim();
  if (!sourceText) return;
  const blob = new Blob([sourceText], { type: "text/plain;charset=utf-8" });
  const blobUrl = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = blobUrl;
  link.download = `${filename}.txt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(blobUrl);
}

function rowEvidence(row: ReasoningRow | DisplayRow): NonNullable<ReasoningRow["evidenceItems"]> {
  return row.evidenceItems || [];
}

function evidenceStatusType(item: NonNullable<ReasoningRow["evidenceItems"]>[number]): StatusTone {
  if (item.isExpired) return "danger";
  if (item.isVerified) return "success";
  if (item.isHistorical) return "info";
  return "warning";
}

function coveragePercent(row: ReasoningRow | DisplayRow): number | null {
  if (!row.technicalAlignment) return null;
  return Math.round((row.technicalAlignment.coverage || 0) * 100);
}

function coverageTone(row: ReasoningRow | DisplayRow): StatusTone {
  const coverage = row.technicalAlignment?.coverage || 0;
  if (coverage >= 0.9) return "success";
  if (coverage >= 0.5) return "warning";
  return "danger";
}

function gapsFor(row: ReasoningRow | DisplayRow): string[] {
  return row.technicalAlignment?.gaps || row.gaps || [];
}

function firstLine(value?: string): string {
  return String(value || "").split("\n").map((line) => line.trim()).find(Boolean) || "";
}

function summaryItems(summary: MatchSummary | null) {
  if (!summary) return [];
  return [
    { label: "要求总数", value: summary.totalRequirementCount, tone: "info" as StatusTone },
    { label: "证据覆盖率", value: `${summary.evidenceCoverageRate}%`, tone: summary.evidenceCoverageRate >= 80 ? "success" as StatusTone : "warning" as StatusTone },
    { label: "强制条款闭环", value: `${summary.mandatoryClosureRate}%`, tone: summary.mandatoryClosureRate >= 90 ? "success" as StatusTone : "warning" as StatusTone },
    { label: "待补材料", value: missingMaterialCount.value, tone: missingMaterialCount.value ? "warning" as StatusTone : "success" as StatusTone },
    { label: "否决风险", value: riskCount.value, tone: riskCount.value ? "danger" as StatusTone : "success" as StatusTone }
  ];
}

function chooseFile() {
  fileInput.value?.click();
}

function onFileChange(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0] || null;
  if (
    file &&
    file.name.toLowerCase().endsWith(".doc") &&
    !file.name.toLowerCase().endsWith(".docx")
  ) {
    ElMessage.warning({
      message:
        "建议先用 Word 另存为 .docx 再上传，可完整解析表格；直接上传 .doc 将以纯文本解析（表格结构可能丢失）。",
      duration: 8000,
    });
  }
  emit("fileSelected", file);
}

function selectModule(key: ModuleKey) {
  activeModule.value = key;
  searchText.value = "";
  statusFilter.value = "全部";
  collapsedSourceSections.value = new Set();
  activeRowId.value = rowsForModule(key)[0]?.id || null;
  detailDrawerOpen.value = false;
}

function selectRow(row: DisplayRow) {
  activeRowId.value = row.id || null;
  detailDrawerOpen.value = true;
}

function closeDetail() {
  detailDrawerOpen.value = false;
}

function updateStatus(row: DisplayRow, status: string) {
  emit("update-fine-status", {
    rowId: row.id || "",
    status,
    uncertaintyReason: row.uncertaintyReason || "",
    responseValue: row.responseValue
  });
}

function saveExtractionMode() {
  emit("save-config", {
    ai: {
      extractionMode: extractionMode.value
    }
  });
}

watch(
  () => props.config?.ai?.extractionMode,
  (mode) => {
    extractionMode.value = mode === "workbuddy_agent_mcp" ? "workbuddy_agent_mcp" : "builtin";
  },
  { immediate: true }
);

watch(
  () => props.workspace?.id,
  (workspaceId, previousWorkspaceId) => {
    activeModule.value = "technical";
    activeRowId.value = null;
    detailDrawerOpen.value = false;
    searchText.value = "";
    statusFilter.value = "全部";
    projectInfoExpanded.value = false;
    if (workspaceId && previousWorkspaceId && workspaceId !== previousWorkspaceId) {
      void nextTick(() => resultArea.value?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }
  }
);

watch(
  () => [activeModule.value, moduleRows.value.length],
  () => {
    if (!activeRowId.value || !moduleRows.value.some((row) => row.id === activeRowId.value)) {
      activeRowId.value = moduleRows.value[0]?.id || null;
    }
  }
);
</script>

<template>
  <div class="matching-page">
    <el-row class="matching-import-row" :gutter="16">
      <el-col :xs="24" :lg="14">
        <el-card class="panel matching-import-panel" shadow="never">
          <template #header>
            <div class="panel-header">
              <div class="result-title">
                <el-icon><Upload /></el-icon>
                <span>{{ inputKind === "tender_document" ? "导入完整招标文件" : "导入招标公告" }}</span>
              </div>
              <el-tag type="info" effect="plain">上传或粘贴</el-tag>
            </div>
          </template>

          <el-radio-group
            :model-value="inputKind"
            class="field"
            @update:model-value="emit('update:input-kind', $event)"
          >
            <el-radio-button label="notice">公告资格粗匹配</el-radio-button>
            <el-radio-button label="tender_document">完整招标文件细匹配</el-radio-button>
          </el-radio-group>

          <div class="extraction-mode-row field">
            <div class="extraction-mode-control">
              <span class="extraction-mode-label">招标文件条款提取方式</span>
              <el-select v-model="extractionMode" class="full-width">
                <el-option label="内置 LLM + 规则" value="builtin" />
                <el-option label="CodeBuddy Agent SDK + Tender Review MCP" value="workbuddy_agent_mcp" />
              </el-select>
              <span class="form-hint">
                只影响条款提取和重点章节识别，后续匹配、M3 和报告流程不变。
              </span>
            </div>
            <el-button :icon="Save" :loading="busy" plain @click="saveExtractionMode">
              保存方式
            </el-button>
          </div>

          <el-input v-model="importForm.name" placeholder="文件名称（可选）" class="field" />

          <input
            ref="fileInput"
            class="hidden-file-input"
            type="file"
            accept=".pdf,.doc,.docx,.xlsx,.txt,.md"
            @change="onFileChange"
          />
          <button class="upload-dropzone" type="button" @click="chooseFile">
            <el-icon :size="24"><Upload /></el-icon>
            <span>{{ fileName || (inputKind === "tender_document" ? "点击选择完整招标文件" : "点击选择招标公告") }}</span>
            <small>PDF、DOC、DOCX、XLSX、TXT、Markdown</small>
          </button>

          <el-input
            v-model="importForm.text"
            :rows="4"
            type="textarea"
            :placeholder="inputKind === 'tender_document' ? '也可以直接粘贴完整招标文件内容，系统将拆解为可核验条款' : '也可以直接粘贴公告正文或资格要求'"
            class="field"
          />

          <div class="action-row">
            <span class="form-hint">
              {{ inputKind === "tender_document" ? "完整文件会生成统一路由结果，并按用途进入下方审核模块。" : "公告先判断资格和是否值得继续获取文件。" }}
            </span>
            <el-button :icon="Sparkles" :loading="busy" type="primary" @click="emit('evaluate')">
              开始匹配评估
            </el-button>
          </div>
        </el-card>
      </el-col>

      <el-col :xs="24" :lg="10">
        <el-card class="panel matching-history-panel" shadow="never">
          <template #header>
            <div class="panel-header">
              <div class="result-title">
                <el-icon><History /></el-icon>
                <span>历史匹配项目</span>
              </div>
              <el-tag type="info" effect="plain">{{ historyWorkspaces.length }} 个项目</el-tag>
            </div>
          </template>

          <div v-if="historyWorkspaces.length" class="match-history-list">
            <button
              v-for="item in historyWorkspaces"
              :key="item.id"
              type="button"
              class="match-history-item"
              :class="{ 'match-history-item--active': item.id === workspace?.id }"
              @click="emit('selectWorkspace', item)"
            >
              <span class="match-history-item-main">
                <strong>{{ item.title || item.project.title || "未命名项目" }}</strong>
                <span>
                  <template v-if="item.project.region && item.project.region !== '未识别'">
                    {{ item.project.region }} ·
                  </template>
                  {{ item.reasoning.fineReview ? "完整文件细匹配" : "公告资格粗匹配" }}
                </span>
              </span>
              <span class="match-history-item-side">
                <el-tag
                  size="small"
                  :type="item.reasoning.fineReview
                    ? ((item.reasoning.fineReview.hardBlockers || []).length ? 'danger' : 'success')
                    : ((item.reasoning.eligibility?.shouldGetTenderDocument ?? item.reasoning.score >= 70) ? 'success' : 'warning')"
                  effect="plain"
                >
                  {{ item.reasoning.fineReview?.score ?? item.reasoning.eligibility?.eligibilityScore ?? item.reasoning.score }}
                </el-tag>
                <el-icon><ChevronRight /></el-icon>
              </span>
            </button>
          </div>
          <el-empty v-else description="暂无历史匹配项目" />
        </el-card>

      </el-col>
    </el-row>

    <div ref="resultArea">
      <el-card v-if="workspace" class="panel result-panel matching-result-panel" shadow="never">
        <template #header>
          <div class="result-header">
            <div class="result-header-main">
              <span class="result-eyebrow">匹配结果</span>
              <h2>{{ workspace.title || workspace.project.title || "未命名项目" }}</h2>
              <div class="result-header-meta">
                <el-tag
                  v-for="tag in headerTags"
                  :key="tag.label"
                  :type="tag.tone"
                  effect="plain"
                >
                  {{ tag.label }}
                </el-tag>
              </div>
            </div>
            <div class="catalog-actions">
              <el-button :icon="RefreshCw" :loading="busy" plain @click="emit('reanalyze')">重新分析</el-button>
              <el-button :icon="Wrench" type="primary" plain @click="emit('openWorkspace')">进入标书工作台</el-button>
            </div>
          </div>
        </template>

        <div class="decision-strip">
          <div class="decision-strip-main">
            <span class="decision-label">当前建议</span>
            <el-tag :type="recommendationTone" effect="light" size="large">{{ recommendation }}</el-tag>
            <span v-if="fineReview?.summary" class="decision-note">
              已生成 {{ analyzedClauseCount }} 条模块输出，先处理风险和缺口，再进入编制。
            </span>
            <span v-else class="decision-note">当前为公告资格粗匹配，获取完整文件后可展开技术与商务判断。</span>
          </div>
          <div class="decision-strip-stats">
            <span><strong>{{ riskCount }}</strong> 否决风险</span>
            <span><strong>{{ missingMaterialCount }}</strong> 待补材料</span>
            <span><strong>{{ statusCounts.notSatisfied }}</strong> 不满足</span>
          </div>
        </div>

        <div v-if="fineSummary" class="match-summary-grid matching-summary-grid">
          <div
            v-for="item in summaryItems(fineSummary)"
            :key="item.label"
            class="match-summary-item"
            :data-tone="item.tone"
          >
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
          </div>
        </div>

        <el-alert
          v-if="workspace.reasoning.extraction?.warning || fineReview?.extraction.warning"
          :title="workspace.reasoning.extraction?.warning || fineReview?.extraction.warning"
          type="warning"
          show-icon
          :closable="false"
          class="mt-12"
        />

        <section class="overview-layout">
          <div class="overview-main">
            <div class="section-heading">
              <strong>项目基本信息</strong>
              <span>项目身份、预算、时间、递交与履约信息集中展示</span>
            </div>
            <div v-if="visibleProjectInfoItems.length" class="project-info-grid matching-project-info-grid">
              <div v-for="item in visibleProjectInfoItems" :key="item.key" class="project-info-item">
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
              </div>
            </div>
            <el-empty v-else description="当前没有提取到项目基本信息" />
            <div v-if="extraProjectInfoItems.length" class="project-info-toggle">
              <el-button
                text
                type="primary"
                :icon="projectInfoExpanded ? ChevronUp : ChevronDown"
                @click="projectInfoExpanded = !projectInfoExpanded"
              >
                {{ projectInfoExpanded ? "收起" : `展开全部（${extraProjectInfoItems.length} 项）` }}
              </el-button>
            </div>

            <div class="section-heading section-heading-spaced">
              <strong>下一步优先级</strong>
              <span>只保留会影响是否继续投标的动作</span>
            </div>
            <div class="priority-list">
              <div v-if="riskCount" class="priority-item priority-item--danger">
                <el-icon><AlertTriangle /></el-icon>
                <div>
                  <strong>先核查 {{ riskCount }} 条否决风险</strong>
                  <span>确认是否真的触发，未确认前不要直接进入正式编制。</span>
                </div>
                <el-button text type="danger" @click="selectModule('rejection')">查看</el-button>
              </div>
              <div v-if="missingMaterialCount" class="priority-item priority-item--warning">
                <el-icon><FileCheck2 /></el-icon>
                <div>
                  <strong>补齐 {{ missingMaterialCount }} 项材料</strong>
                  <span>当前没有足够的可核验证据，不等同于企业一定不具备能力。</span>
                </div>
                <el-button text type="warning" @click="selectModule('qualification')">查看</el-button>
              </div>
              <div v-if="!riskCount && !missingMaterialCount" class="priority-item priority-item--success">
                <el-icon><ClipboardCheck /></el-icon>
                <div>
                  <strong>当前未发现需要优先处理的硬风险</strong>
                  <span>继续检查技术响应、商务接受度和合规要求。</span>
                </div>
              </div>
            </div>
          </div>

          <aside class="overview-side">
            <div class="decision-card" :data-tone="recommendationTone">
              <span>投标决策</span>
              <strong>{{ recommendation }}</strong>
              <p>{{ workspace.reasoning.fineReview?.decision || workspace.reasoning.decision || "等待分析结果" }}</p>
            </div>
            <div class="overview-count-list">
              <div><span>满足</span><strong>{{ statusCounts.satisfied }}</strong></div>
              <div><span>部分满足</span><strong>{{ statusCounts.partial }}</strong></div>
              <div><span>待补材料</span><strong>{{ statusCounts.pending }}</strong></div>
              <div><span>待澄清</span><strong>{{ statusCounts.clarify }}</strong></div>
              <div><span>未触发</span><strong>{{ statusCounts.untouched }}</strong></div>
            </div>
            <p class="overview-note">“未触发”表示条件性条款存在，但当前项目事实或投标方式没有触发，不计入风险和缺口。</p>
          </aside>
        </section>

        <div class="module-navigation" role="tablist" aria-label="处理模块">
          <div class="module-navigation-items">
            <button
              v-for="tab in tabs"
              :key="tab.key"
              type="button"
              class="matching-tab"
              :class="{ 'matching-tab--active': activeModule === tab.key }"
              @click="selectModule(tab.key)"
            >
              <el-icon><component :is="tab.icon" /></el-icon>
              <span class="matching-tab-copy">
                <strong>{{ tab.label }}</strong>
                <small>{{ tab.description }}</small>
              </span>
              <el-tag size="small" effect="plain">{{ tab.count }}</el-tag>
            </button>
          </div>
        </div>

        <section class="module-view">
          <div class="module-toolbar">
            <div>
              <div class="section-heading">
                <strong>{{ moduleMeta.find((item) => item.key === activeModule)?.label }}</strong>
                <span>{{ moduleMeta.find((item) => item.key === activeModule)?.description }}</span>
              </div>
              <div v-if="activeModule === 'technical'" class="module-helper">
                这里核验产品、平台和服务能力是否满足技术要求；企业材料只是匹配证据来源。
              </div>
              <div v-else-if="activeModule === 'qualification'" class="module-helper">
                这里核验企业是否具备投标资格，包含资质、业绩、人员、财务、信用和地域证明。
              </div>
              <div v-else-if="activeModule === 'commercial'" class="module-helper">
                这里展示报价、付款、交付、质保和商务条款，只做确认与写作输入，不查企业库。
              </div>
              <div v-else-if="activeModule === 'evaluation'" class="module-helper">
                这里查看评分项、分值、评分规则和需要准备的证明材料。
              </div>
              <div v-else-if="activeModule === 'contract'" class="module-helper">
                这里仅保留合同范围定位，当前不进入投标资格和技术匹配。
              </div>
              <div v-else-if="activeModule === 'template'" class="module-helper">
                这里按模板章节块展示原文和表格，不把段落拆成 277 条；每个模板块可下载原文件样式并进入写标书。
              </div>
              <div v-else-if="activeModule === 'project_info'" class="module-helper">
                这里查看项目身份、时间、费用和递交要求，不进入企业材料匹配。
              </div>
              <div v-else-if="activeModule === 'rejection'" class="module-helper">
                这里仅展示全文正则召回的废标、无效、否决和不接受条款，不进入企业能力匹配。
              </div>
            </div>
            <div class="module-toolbar-actions">
              <el-input
                v-model="searchText"
                :prefix-icon="Search"
                clearable
                :placeholder="sourceDisplayActive ? '搜索原文' : '搜索条款'"
              />
              <el-select v-if="!sourceDisplayActive" v-model="statusFilter" aria-label="按状态筛选">
                <el-option label="全部状态" value="全部" />
                <el-option
                  v-for="status in [...new Set(moduleRows.map((row) => displayStatus(row)))]"
                  :key="status"
                  :label="status"
                  :value="status"
                />
              </el-select>
              <span v-if="sourceDisplayActive" class="source-view-toolbar-note">
                {{ sourceViewStatsForActive.sectionCount }} 个章节块 · {{ sourceViewStatsForActive.tableCount }} 张表格
              </span>
            </div>
          </div>

          <div v-if="sourceDisplayActive && filteredSourceSections.length" class="source-document-view" :class="`source-document-view--${activeModule}`">
            <div class="source-view-summary">
              <span>{{ sourceViewMetaForActive.title }}</span>
              <small>
                {{ sourceViewMetaForActive.note }}
              </small>
            </div>
            <article
              v-for="node in filteredSourceSections"
              :key="node.sectionId"
              class="source-section-node"
              :style="{ '--source-depth': node.depth || 0 }"
            >
              <div class="source-section-header">
                <button
                  v-if="node.hasChildren"
                  type="button"
                  class="source-section-toggle"
                  :aria-label="sourceSectionCollapsed(node) ? '展开章节' : '收起章节'"
                  @click="toggleSourceSection(node)"
                >
                  <el-icon>
                    <ChevronRight v-if="sourceSectionCollapsed(node)" />
                    <ChevronDown v-else />
                  </el-icon>
                </button>
                <span v-else class="source-section-toggle-placeholder" aria-hidden="true" />
                <div class="source-section-title">
                  <strong>{{ node.title || "未命名章节" }}</strong>
                  <span>{{ (node.sectionPath || []).join(" / ") || node.sourceLocator || "原文位置待识别" }}</span>
                </div>
                <div class="source-section-actions">
                  <el-tag v-if="node.blocks?.length" size="small" effect="plain">
                    {{ node.blocks.length }} 个原文块
                  </el-tag>
                  <el-button
                    v-if="activeModule === 'template' && node.templateId"
                    :icon="Download"
                    size="small"
                    plain
                    @click="downloadSourceSection(node)"
                  >
                    下载模板
                  </el-button>
                  <el-button
                    v-if="activeModule === 'template' && node.templateId"
                    :icon="PenLine"
                    size="small"
                    type="primary"
                    plain
                    @click="openSourceSectionForWriting"
                  >
                    写入标书
                  </el-button>
                </div>
              </div>

              <div v-if="!sourceSectionCollapsed(node)" class="source-section-body">
                <template v-for="(block, blockIndex) in node.blocks || []" :key="`${node.sectionId}-block-${blockIndex}`">
                  <p v-if="block.type === 'paragraph'" class="source-paragraph">
                    {{ block.text }}
                  </p>
                  <div v-else-if="block.type === 'table'" class="source-table-block">
                    <div class="source-table-heading">
                      <span>表格{{ block.tableId ? ` · ${block.tableId}` : "" }}</span>
                      <small>{{ block.sourceLocator || node.sourceLocator || "原文表格" }}</small>
                    </div>
                    <div class="source-table-scroll">
                      <table class="source-table">
                        <tbody>
                          <tr
                            v-for="(row, rowIndex) in block.rows || []"
                            :key="`${block.tableId || blockIndex}-row-${row.rowIndex || rowIndex}`"
                            :class="{ 'source-table-header-row': rowIndex === 0 }"
                          >
                            <td
                              v-for="(cell, cellIndex) in row.cells || []"
                              :key="`${block.tableId || blockIndex}-row-${rowIndex}-cell-${cell.cellIndex || cellIndex}`"
                              :colspan="sourceCellColspan(cell)"
                            >
                              {{ cell.text }}
                            </td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </template>
                <div v-if="!(node.blocks || []).length" class="source-empty-block">
                  当前章节只有层级标题，正文位于下级章节。
                </div>
              </div>
            </article>
          </div>
          <section v-else-if="projectInfoDisplayActive" class="project-info-document-view">
            <div class="source-view-summary">
              <span>项目关键基础信息</span>
              <small>集中查看项目身份、预算费用、时间节点、递交方式、交付履约和联合体要求。</small>
            </div>
            <div v-if="visibleProjectInfoItems.length" class="project-info-document-grid">
              <article v-for="item in visibleProjectInfoItems" :key="item.key" class="project-info-document-item">
                <span>{{ item.label }}</span>
                <strong>{{ item.value }}</strong>
              </article>
            </div>
            <el-empty v-else description="当前没有提取到项目基础信息" />
            <div v-if="extraProjectInfoItems.length" class="project-info-document-footer">
              <el-button
                text
                type="primary"
                :icon="projectInfoExpanded ? ChevronUp : ChevronDown"
                @click="projectInfoExpanded = !projectInfoExpanded"
              >
                {{ projectInfoExpanded ? "收起全部信息" : `展开全部（${extraProjectInfoItems.length} 项）` }}
              </el-button>
            </div>
          </section>
          <el-empty
            v-else-if="sourceDisplayActive"
            :description="sourceViewMetaForActive.empty"
          />
          <div v-else-if="filteredModuleRows.length" class="module-content">
            <div class="module-list">
              <div
                v-for="(row, index) in filteredModuleRows"
                :key="row.id || `${activeModule}-${index}`"
                type="button"
                class="clause-list-item"
                :class="{ 'clause-list-item--active': detailDrawerOpen && activeRow?.id === row.id }"
                @click="selectRow(row)"
                role="button"
                tabindex="0"
                @keydown.enter.prevent="selectRow(row)"
                @keydown.space.prevent="selectRow(row)"
              >
                <div class="clause-list-head">
                  <span class="clause-index">{{ index + 1 }}</span>
                  <el-tag size="small" effect="plain">{{ row.kind || "条款" }}</el-tag>
                  <el-tag size="small" :type="routeTone(row)" effect="plain">
                    {{ routeLabel(row) }}
                  </el-tag>
                  <el-tag size="small" :type="statusTagType(displayStatus(row))" effect="light">
                    {{ displayStatus(row) }}
                  </el-tag>
                </div>
                <strong>{{ firstLine(row.requirement) }}</strong>
                <span v-if="row.productName" class="clause-list-product">
                  采购标的：{{ row.productName }}
                </span>
                <span class="clause-list-location">{{ sourceLabel(row) }}</span>
                <span v-if="coveragePercent(row) !== null" class="clause-list-coverage">
                  覆盖 {{ coveragePercent(row) }}%
                </span>
                <div v-if="activeModule === 'template'" class="template-row-actions">
                  <el-button
                    :icon="Download"
                    size="small"
                    plain
                    :disabled="!templateDownloadUrl(row)"
                    @click.stop="downloadTemplateStyle(row)"
                  >
                    下载 Word
                  </el-button>
                </div>
              </div>
            </div>
          </div>
          <el-empty v-else :description="moduleEmptyText[activeModule]" />
        </section>

        <el-drawer
          v-model="detailDrawerOpen"
          direction="rtl"
          size="50%"
          :with-header="false"
          class="clause-detail-drawer"
        >
          <article v-if="activeRow" class="clause-detail">
            <div class="clause-detail-head">
              <div>
                <span class="detail-eyebrow">{{ activeRow.kind || "条款详情" }}</span>
                <h3>{{ activeRow.requirement }}</h3>
              </div>
              <div class="clause-detail-head-actions">
                <el-tag :type="statusTagType(displayStatus(activeRow))" effect="light" size="large">
                  {{ displayStatus(activeRow) }}
                </el-tag>
                <el-button :icon="X" circle plain aria-label="关闭详情" @click="closeDetail" />
              </div>
            </div>

              <div class="detail-route-grid">
                <div class="detail-route-item">
                  <span class="detail-label">输出模块</span>
                  <strong>{{ targetModuleLabel(activeRow) }}</strong>
                </div>
                <div class="detail-route-item">
                  <span class="detail-label">主路由</span>
                  <el-tag :type="routeTone(activeRow)" effect="plain">{{ routeLabel(activeRow) }}</el-tag>
                </div>
                <div class="detail-route-item">
                  <span class="detail-label">处理方式</span>
                  <el-tag type="info" effect="plain">{{ handlingModeLabel(activeRow) }}</el-tag>
                </div>
                <div v-if="matchDomainLabel(activeRow)" class="detail-route-item">
                  <span class="detail-label">匹配领域</span>
                  <strong>{{ matchDomainLabel(activeRow) }}</strong>
                </div>
                <div class="detail-route-item">
                  <span class="detail-label">处理粒度</span>
                  <strong>{{ granularityLabel(activeRow) }}</strong>
                </div>
                <div class="detail-route-item">
                  <span class="detail-label">复核状态</span>
                  <el-tag :type="reviewTone(activeRow)" effect="plain">
                    {{ reviewStatusLabel(activeRow) }}
                  </el-tag>
                </div>
              </div>

              <div class="detail-section">
                <span class="detail-label">判断理由</span>
                <p>{{ activeRow.reason || activeRow.risk || activeRow.suggestion || "暂未生成说明，请结合原文和证据人工确认。" }}</p>
              </div>

              <div v-if="activeModule === 'commercial'" class="detail-section">
                <span class="detail-label">商务确认</span>
                <p>{{ activeRow.suggestion || activeRow.reason || "请在商务响应和报价编制时确认该条款。" }}</p>
              </div>

              <div v-if="activeModule === 'evaluation'" class="detail-section">
                <span class="detail-label">评分信息</span>
                <div class="detail-kv-grid">
                  <div v-if="activeRow.criterionNo || activeRow.itemNo"><span>序号</span><strong>{{ activeRow.criterionNo || activeRow.itemNo }}</strong></div>
                  <div v-if="activeRow.score !== undefined || activeRow.maxScore !== undefined"><span>分值</span><strong>{{ activeRow.score ?? activeRow.maxScore }}{{ activeRow.score !== undefined && activeRow.maxScore !== undefined ? ` / ${activeRow.maxScore}` : '' }}</strong></div>
                  <div v-if="activeRow.criterionName"><span>评分项</span><strong>{{ activeRow.criterionName }}</strong></div>
                  <div v-if="activeRow.scoringRule"><span>评分规则</span><strong>{{ activeRow.scoringRule }}</strong></div>
                  <div v-if="activeRow.evaluationMethod"><span>评审方式</span><strong>{{ activeRow.evaluationMethod }}</strong></div>
                </div>
                <p v-if="activeRow.requiredEvidence" class="detail-muted">需要准备：{{ activeRow.requiredEvidence }}</p>
              </div>

              <div v-if="activeModule === 'template'" class="detail-section">
                <span class="detail-label">模板定位</span>
                <div class="detail-kv-grid">
                  <div v-if="activeRow.templateId"><span>模板 ID</span><strong>{{ activeRow.templateId }}</strong></div>
                  <div v-if="activeRow.templateType"><span>模板类型</span><strong>{{ activeRow.templateType }}</strong></div>
                  <div v-if="activeRow.extractMode"><span>读取方式</span><strong>{{ activeRow.extractMode }}</strong></div>
                  <div v-if="activeRow.previewMode"><span>预览方式</span><strong>{{ activeRow.previewMode }}</strong></div>
                </div>
                <div class="template-detail-actions">
                  <span class="detail-label">模板操作</span>
                  <el-button
                    :icon="Download"
                    plain
                    :disabled="!templateDownloadUrl(activeRow)"
                    @click="downloadTemplateStyle(activeRow)"
                  >
                    下载 Word
                  </el-button>
                </div>
              </div>

              <div v-if="activeModule === 'technical'" class="detail-section">
                <div class="technical-match-heading">
                  <span class="detail-label">对应企业产品与参数</span>
                  <el-tag
                    v-if="coveragePercent(activeRow) !== null"
                    :type="coverageTone(activeRow)"
                    size="small"
                    effect="plain"
                  >
                    覆盖 {{ coveragePercent(activeRow) }}%
                  </el-tag>
                </div>
                <div v-if="activeRow.technicalAlignment?.matchedUnits?.length" class="product-match-list">
                  <div
                    v-for="(unit, index) in activeRow.technicalAlignment.matchedUnits"
                    :key="`${activeRow.id}-unit-${index}`"
                    class="product-match-card"
                  >
                    <div class="product-match-heading">
                      <div class="product-match-title">
                        <strong>{{ unit.productName || unit.productId || "企业能力" }}</strong>
                        <span v-if="[unit.brand, unit.model, unit.category].filter(Boolean).length">
                          {{ [unit.brand, unit.model, unit.category].filter(Boolean).join(" · ") }}
                        </span>
                      </div>
                      <el-tag size="small" type="success" effect="plain">关联产品</el-tag>
                    </div>
                    <p v-if="unit.productDescription" class="product-match-description">
                      {{ unit.productDescription }}
                    </p>
                    <div v-if="unit.matchedParameterDetails?.length" class="product-parameter-block">
                      <div class="product-parameter-title">对应产品参数</div>
                      <div
                        v-for="(parameter, parameterIndex) in unit.matchedParameterDetails"
                        :key="`${activeRow.id}-unit-${index}-parameter-${parameterIndex}`"
                        class="product-parameter-row"
                      >
                        <span class="product-parameter-id">
                          {{ parameter.parameterId || `参数 ${parameterIndex + 1}` }}
                        </span>
                        <div class="product-parameter-copy">
                          <strong>{{ parameter.description || "未填写参数描述" }}</strong>
                          <small v-if="parameter.evidencePath">参数证据：{{ parameter.evidencePath }}</small>
                          <el-button
                            v-if="!parameter.evidencePath && parameter.parameterId"
                            :icon="Upload"
                            size="small"
                            plain
                            :loading="attachSubmitting"
                            @click.stop="requestAttachParameter({ parameterId: parameter.parameterId, description: parameter.description })"
                          >
                            上传证据
                          </el-button>
                        </div>
                      </div>
                    </div>
                    <span v-else-if="unit.matchedParams?.length" class="detail-muted">
                      已匹配产品参数 ID：{{ unit.matchedParams.join("、") }}，重新分析后可显示参数正文。
                    </span>
                    <small v-if="unit.matchedFragment" class="product-match-fragment">
                      匹配依据：{{ unit.matchedFragment }}
                    </small>
                    <small v-if="unit.dataGap" class="product-match-gap">
                      产品资料缺口：{{ unit.dataGap }}
                    </small>
                  </div>
                </div>
                <span v-else class="detail-muted">暂无直接匹配的企业产品或能力参数证据。</span>
              </div>

              <div v-if="activeModule === 'technical' && gapsFor(activeRow).length" class="detail-section detail-section--warning">
                <span class="detail-label">技术缺口</span>
                <ul class="detail-bullet-list">
                  <li v-for="(gap, index) in gapsFor(activeRow)" :key="`${activeRow.id}-gap-${index}`">{{ gap }}</li>
                </ul>
              </div>

              <div v-if="activeModule === 'qualification' || activeModule === 'technical'" class="detail-section">
                <span class="detail-label">企业证据</span>
                <div v-if="rowEvidence(activeRow).length" class="evidence-list">
                  <div v-for="evidence in rowEvidence(activeRow)" :key="evidence.evidenceId || evidence.id" class="evidence-item">
                    <div class="evidence-item-heading">
                      <strong>{{ evidence.name }}</strong>
                      <el-tag size="small" :type="evidenceStatusType(evidence)" effect="plain">
                        {{ evidence.verificationStatus || evidence.status || "待核验" }}
                      </el-tag>
                    </div>
                    <span v-if="evidence.sourceFiles?.length">{{ evidence.sourceFiles.join("、") }}</span>
                    <span v-if="evidence.sourceLocator || evidence.locator">{{ evidence.sourceLocator || evidence.locator }}</span>
                    <p v-if="evidence.excerpt">{{ evidence.excerpt }}</p>
                  </div>
                </div>
                <span v-else class="detail-muted">{{ activeRow.evidence || "暂无可回源的企业证据。" }}</span>
              </div>

              <div v-if="activeModule === 'qualification' || activeModule === 'technical'" class="detail-section">
                <div class="technical-match-heading">
                  <span class="detail-label">补充证明材料</span>
                </div>
                <div v-if="activeRow.supplementedMaterials?.length" class="supplement-list">
                  <div v-for="(item, index) in activeRow.supplementedMaterials" :key="`${item.rid}-${index}`" class="supplement-item">
                    <el-icon :size="16"><FileCheck2 /></el-icon>
                    <span class="supplement-item-copy">
                      <strong>{{ item.label || item.objectId || item.rid }}</strong>
                      <small v-if="item.evidencePath">{{ item.evidencePath }}</small>
                    </span>
                  </div>
                </div>
                <el-button :icon="Upload" type="primary" plain :disabled="busy" @click="openSupplement">
                  新建材料并上传证据
                </el-button>
              </div>

              <div class="detail-section detail-section--source">
                <span class="detail-label">招标原文定位</span>
                <p>{{ sourceLabel(activeRow) }}</p>
                <blockquote v-if="activeRow.sourceText || activeRow.rawText">{{ activeRow.sourceText || activeRow.rawText }}</blockquote>
              </div>

              <div v-if="activeRow.id" class="detail-actions">
                <span class="detail-label">人工确认</span>
                <el-select
                  :model-value="displayStatus(activeRow)"
                  :disabled="busy || !activeRow.statusEditable || displayStatus(activeRow) === '未触发'"
                  placeholder="选择状态"
                  @update:model-value="updateStatus(activeRow, $event)"
                >
                  <el-option
                    v-for="status in statusOptionsForRow(activeRow)"
                    :key="status"
                    :label="status"
                    :value="status"
                  />
                </el-select>
              </div>
          </article>
        </el-drawer>

        <el-dialog v-model="supplementVisible" title="补充证明材料" width="620px" destroy-on-close>
          <el-form label-width="120px">
            <el-form-item label="材料类别">
              <el-radio-group v-model="supplementClass">
                <el-radio-button v-for="c in supplementClasses" :key="c.name" :value="c.name">
                  {{ c.label }}
                </el-radio-button>
              </el-radio-group>
            </el-form-item>
            <el-form-item v-if="needsProduct" label="所属产品">
              <div class="supplement-product-row">
                <el-select
                  v-if="!supplementCreateProduct"
                  v-model="supplementProductId"
                  filterable
                  clearable
                  placeholder="选择已录入产品"
                  class="full-width"
                >
                  <el-option
                    v-for="p in productOptions"
                    :key="factId(p)"
                    :label="productOptionLabel(p)"
                    :value="factId(p)"
                  />
                </el-select>
                <el-checkbox v-model="supplementCreateProduct">无对应产品，新建产品</el-checkbox>
              </div>
            </el-form-item>
            <template v-if="supplementCreateProduct">
              <el-form-item label="新产品名称"><el-input v-model="newProductName" /></el-form-item>
              <el-form-item label="产品类型"><el-input v-model="newProductType" /></el-form-item>
              <el-form-item label="功能描述">
                <el-input v-model="newProductDescription" type="textarea" :autosize="{ minRows: 2, maxRows: 4 }" />
              </el-form-item>
            </template>
            <el-form-item v-for="f in supplementClassFields" :key="f.key" :label="f.label">
              <el-date-picker
                v-if="f.type === 'date'"
                v-model="supplementFields[f.key]"
                type="date"
                value-format="YYYY-MM-DD"
                placeholder="选择日期"
                class="full-width"
              />
              <el-input-number
                v-else-if="f.type === 'number'"
                v-model="supplementFields[f.key]"
                :precision="2"
                class="full-width"
              />
              <el-input
                v-else-if="f.type === 'textarea'"
                v-model="supplementFields[f.key]"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 4 }"
              />
              <el-input v-else v-model="supplementFields[f.key]" />
            </el-form-item>
            <el-form-item label="证明文件">
              <label class="supplement-upload">
                <input class="hidden-file-input" type="file" multiple @change="onSupplementFileChange" />
                <el-icon :size="20"><Upload /></el-icon>
                <span>{{ supplementFiles.length ? supplementFiles.map((f) => f.name).join("、") : "选择证明文件（可多选）" }}</span>
              </label>
            </el-form-item>
          </el-form>
          <template #footer>
            <el-button @click="supplementVisible = false">取消</el-button>
            <el-button type="primary" :loading="supplementSubmitting" @click="submitSupplement">保存并关联</el-button>
          </template>
        </el-dialog>

        <input ref="attachInput" class="hidden-file-input" type="file" multiple @change="onAttachFileChange" />

        <div v-if="latestAuditRecord" class="audit-summary mt-12">
          <div class="audit-summary-heading">
            <strong>最近人工审核</strong>
            <el-tag size="small" type="success" effect="plain">{{ auditRecords.length }} 条记录</el-tag>
          </div>
          <span>
            {{ latestAuditRecord.reviewer }}于 {{ latestAuditRecord.reviewedAt }} 将
            “{{ latestAuditRecord.requirement }}”从“{{ latestAuditRecord.previousStatus }}”调整为“{{ latestAuditRecord.newStatus }}”
          </span>
        </div>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.matching-result-panel {
  overflow: hidden;
}

.matching-import-row {
  align-items: flex-start;
}

.matching-import-row > .el-col {
  display: block;
}

.matching-import-panel,
.matching-history-panel {
  width: 100%;
  height: auto;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

.matching-import-panel .upload-dropzone {
  min-height: 88px;
  padding: 12px;
}

.matching-import-panel :deep(.el-card__body),
.matching-history-panel :deep(.el-card__body) {
  min-height: 0;
  display: flex;
  flex: 0 1 auto;
  flex-direction: column;
}

.matching-history-panel .match-history-list {
  min-height: 0;
  max-height: 430px;
  flex: 0 1 auto;
  overflow-y: auto;
  align-content: start;
}

@media (max-width: 991px) {
  .matching-import-row > .el-col {
    display: block;
  }

  .matching-import-panel,
  .matching-history-panel {
    height: auto;
  }

  .matching-import-panel :deep(.el-card__body),
  .matching-history-panel :deep(.el-card__body) {
    display: block;
  }

  .matching-history-panel .match-history-list {
    max-height: 430px;
  }
}

.extraction-mode-row {
  display: flex;
  align-items: flex-end;
  gap: 12px;
}

.extraction-mode-control {
  min-width: 0;
  flex: 1;
}

.extraction-mode-label {
  display: block;
  margin-bottom: 8px;
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.extraction-mode-control .form-hint {
  display: block;
  margin-top: 6px;
}

.result-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.result-header-main {
  min-width: 0;
}

.result-eyebrow,
.detail-eyebrow {
  display: block;
  color: var(--text-secondary);
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.result-header h2 {
  max-width: 760px;
  margin: 5px 0 10px;
  overflow-wrap: anywhere;
  color: var(--text-primary);
  font-size: 24px;
  line-height: 1.35;
}

.result-header-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.decision-strip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 16px 18px;
  border: 1px solid rgba(var(--primary-rgb), 0.16);
  border-radius: 8px;
  background: rgba(var(--primary-rgb), 0.035);
}

.decision-strip-main,
.decision-strip-stats {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.decision-strip-main {
  min-width: 0;
}

.decision-label,
.detail-label {
  color: var(--text-secondary);
  font-size: 12px;
}

.decision-note {
  max-width: 520px;
  color: var(--text-secondary);
  font-size: 12px;
}

.decision-strip-stats {
  flex: 0 0 auto;
  justify-content: flex-end;
}

.decision-strip-stats span {
  padding-left: 12px;
  border-left: 1px solid var(--border-color);
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.decision-strip-stats strong {
  margin-right: 3px;
  color: var(--text-primary);
  font-size: 18px;
}

.matching-summary-grid {
  margin-top: 14px;
}

.module-navigation {
  display: grid;
  gap: 14px;
  margin-top: 18px;
}

.module-navigation-group {
  display: grid;
  grid-template-columns: 150px minmax(0, 1fr);
  gap: 14px;
  align-items: start;
  padding: 12px 0;
  border-top: 1px solid var(--border-color);
}

.module-navigation-group--display {
  border-bottom: 1px solid var(--border-color);
}

.module-navigation-label {
  display: grid;
  gap: 4px;
  padding: 4px 0;
}

.module-navigation-label strong {
  color: var(--text-primary);
  font-size: 13px;
}

.module-navigation-label span {
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.5;
}

.module-navigation-items {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
}

.matching-tab {
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 70px;
  padding: 10px 11px;
  border: 1px solid var(--border-color);
  border-bottom: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-card);
  color: var(--text-regular);
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: border-color 0.18s ease, background 0.18s ease, color 0.18s ease;
}

.matching-tab:hover,
.matching-tab--active {
  border-color: rgba(var(--primary-rgb), 0.34);
  background: rgba(var(--primary-rgb), 0.055);
  color: var(--primary);
}

.matching-tab--active {
  box-shadow: inset 3px 0 0 var(--primary);
}

.matching-tab > .el-icon {
  flex: 0 0 auto;
  font-size: 18px;
}

.matching-tab-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
  flex: 1;
}

.matching-tab-copy strong,
.matching-tab-copy small {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.matching-tab-copy strong {
  color: var(--text-primary);
  font-size: 13px;
}

.matching-tab-copy small {
  color: var(--text-secondary);
  font-size: 11px;
}

.overview-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.6fr) minmax(230px, 0.7fr);
  gap: 18px;
  margin-top: 18px;
}

.overview-main,
.overview-side {
  min-width: 0;
}

.overview-side {
  display: grid;
  align-content: start;
  gap: 12px;
}

.matching-project-info-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin-top: 10px;
}

.project-info-toggle {
  display: flex;
  justify-content: flex-start;
  margin-top: 10px;
}

.section-heading-spaced {
  margin-top: 24px;
}

.priority-list {
  display: grid;
  gap: 8px;
  margin-top: 10px;
}

.priority-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-left-width: 3px;
  border-radius: 6px;
  background: var(--bg-card);
}

.priority-item > .el-icon {
  margin-top: 2px;
  flex: 0 0 auto;
}

.priority-item > div {
  min-width: 0;
  flex: 1;
}

.priority-item strong,
.priority-item span {
  display: block;
}

.priority-item strong {
  color: var(--text-primary);
  font-size: 13px;
}

.priority-item span {
  margin-top: 3px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.55;
}

.priority-item--danger {
  border-left-color: var(--danger);
  background: rgba(var(--danger-rgb), 0.04);
}

.priority-item--danger > .el-icon {
  color: var(--danger);
}

.priority-item--warning {
  border-left-color: var(--warning);
  background: rgba(var(--warning-rgb), 0.045);
}

.priority-item--warning > .el-icon {
  color: var(--warning);
}

.priority-item--success {
  border-left-color: var(--success);
  background: rgba(var(--success-rgb), 0.045);
}

.priority-item--success > .el-icon {
  color: var(--success);
}

.decision-card {
  display: grid;
  gap: 6px;
  padding: 16px;
  border: 1px solid var(--border-color);
  border-top: 3px solid var(--primary);
  border-radius: 6px;
  background: var(--bg-soft);
}

.decision-card[data-tone="danger"] {
  border-top-color: var(--danger);
  background: rgba(var(--danger-rgb), 0.055);
}

.decision-card[data-tone="warning"] {
  border-top-color: var(--warning);
  background: rgba(var(--warning-rgb), 0.055);
}

.decision-card[data-tone="success"] {
  border-top-color: var(--success);
  background: rgba(var(--success-rgb), 0.055);
}

.decision-card span {
  color: var(--text-secondary);
  font-size: 12px;
}

.decision-card strong {
  color: var(--text-primary);
  font-size: 22px;
}

.decision-card p {
  margin: 0;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.overview-count-list {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-top: 1px solid var(--border-color);
  border-left: 1px solid var(--border-color);
}

.overview-count-list div {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 10px;
  border-right: 1px solid var(--border-color);
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-card);
}

.overview-count-list span {
  color: var(--text-secondary);
  font-size: 12px;
}

.overview-count-list strong {
  color: var(--text-primary);
  font-size: 18px;
}

.overview-note {
  margin: 0;
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.6;
}

.module-view {
  min-width: 0;
  margin-top: 18px;
}

.module-toolbar {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.module-toolbar-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 0 0 auto;
}

.module-toolbar-actions .el-input {
  width: 220px;
}

.module-toolbar-actions .el-select {
  width: 140px;
}

.source-view-toolbar-note {
  color: var(--text-secondary);
  font-size: 12px;
  white-space: nowrap;
}

.module-helper {
  margin-top: 7px;
  color: var(--text-secondary);
  font-size: 12px;
}

.module-content {
  min-height: 160px;
  border: 1px solid var(--border-color);
  border-radius: 7px;
  overflow: hidden;
  background: var(--bg-soft);
}

.source-document-view {
  display: grid;
  gap: 8px;
  min-height: 470px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 7px;
  background: var(--bg-soft);
}

.source-document-view--commercial,
.source-document-view--evaluation,
.source-document-view--template {
  gap: 0;
  padding: 0 16px;
  background: var(--bg-card);
}

.source-view-summary {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  padding: 4px 2px 9px;
  border-bottom: 1px solid var(--border-color);
}

.source-view-summary span {
  color: var(--text-primary);
  font-size: 13px;
  font-weight: 600;
}

.source-view-summary small {
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.5;
  text-align: right;
}

.source-section-node {
  min-width: 0;
  margin-left: calc(var(--source-depth, 0) * 18px);
  border-bottom: 1px solid var(--border-color);
  background: transparent;
}

.source-document-view--commercial .source-section-node,
.source-document-view--evaluation .source-section-node,
.source-document-view--template .source-section-node {
  margin-left: calc(var(--source-depth, 0) * 22px);
}

.source-document-view--commercial .source-section-node[style*="--source-depth: 0"],
.source-document-view--evaluation .source-section-node[style*="--source-depth: 0"],
.source-document-view--template .source-section-node[style*="--source-depth: 0"] {
  margin-left: 0;
}

.source-section-header {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  padding: 12px 4px;
}

.source-document-view--commercial .source-section-header,
.source-document-view--evaluation .source-section-header,
.source-document-view--template .source-section-header {
  padding-top: 14px;
  padding-bottom: 10px;
}

.source-section-toggle,
.source-section-toggle-placeholder {
  width: 24px;
  height: 24px;
  flex: 0 0 24px;
}

.source-section-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--text-secondary);
  cursor: pointer;
}

.source-section-toggle:hover {
  background: var(--bg-soft);
  color: var(--primary);
}

.source-section-toggle-placeholder {
  display: block;
}

.source-section-title {
  min-width: 0;
  display: grid;
  gap: 3px;
  flex: 1;
}

.source-section-title strong {
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.source-document-view--commercial .source-section-title strong,
.source-document-view--evaluation .source-section-title strong,
.source-document-view--template .source-section-title strong {
  font-size: 14px;
}

.source-section-title span {
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.source-section-actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  flex-wrap: wrap;
  gap: 6px;
}

.source-section-body {
  display: grid;
  gap: 8px;
  padding: 0 12px 16px 43px;
}

.source-paragraph {
  margin: 9px 0 0;
  color: var(--text-regular);
  font-size: 13px;
  line-height: 1.75;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.source-table-block {
  min-width: 0;
  margin-top: 9px;
  border: 1px solid var(--border-color);
  border-radius: 5px;
  overflow: hidden;
  background: var(--bg-card);
}

.source-document-view--commercial .source-table-block,
.source-document-view--evaluation .source-table-block,
.source-document-view--template .source-table-block {
  border-radius: 0;
  border-left: 0;
  border-right: 0;
}

.project-info-document-view {
  min-height: 260px;
  padding: 0 16px 16px;
  border: 1px solid var(--border-color);
  background: var(--bg-card);
}

.project-info-document-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-top: 1px solid var(--border-color);
  border-left: 1px solid var(--border-color);
}

.project-info-document-item {
  min-width: 0;
  display: grid;
  gap: 6px;
  min-height: 78px;
  padding: 13px 14px;
  border-right: 1px solid var(--border-color);
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-card);
}

.project-info-document-item span {
  color: var(--text-secondary);
  font-size: 11px;
}

.project-info-document-item strong {
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.project-info-document-footer {
  padding-top: 10px;
}

.source-table-heading {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 10px;
  padding: 8px 10px;
  border-bottom: 1px solid var(--border-color);
  background: var(--bg-soft);
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
}

.source-table-heading small {
  color: var(--text-secondary);
  font-size: 10px;
  font-weight: 400;
}

.source-table-scroll {
  max-width: 100%;
  overflow-x: auto;
}

.source-table {
  width: 100%;
  min-width: 620px;
  border-collapse: collapse;
  table-layout: auto;
}

.source-table td {
  min-width: 74px;
  padding: 8px 9px;
  border-right: 1px solid var(--border-color);
  border-bottom: 1px solid var(--border-color);
  color: var(--text-regular);
  font-size: 12px;
  line-height: 1.55;
  vertical-align: top;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.source-table td:last-child {
  border-right: 0;
}

.source-table tr:last-child td {
  border-bottom: 0;
}

.source-table-header-row td {
  background: var(--bg-soft);
  color: var(--text-primary);
  font-weight: 600;
}

.source-empty-block {
  margin-top: 9px;
  color: var(--text-secondary);
  font-size: 12px;
}

.module-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
  padding: 12px;
}

.clause-list-item {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 14px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-card);
  color: inherit;
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
}

.clause-list-item:hover {
  border-color: rgba(var(--primary-rgb), 0.5);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.06);
  transform: translateY(-1px);
}

.clause-list-item--active {
  border-color: var(--primary);
  box-shadow: 0 0 0 2px rgba(var(--primary-rgb), 0.2);
}

.clause-list-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
}

.clause-index {
  min-width: 20px;
  color: var(--text-secondary);
  font-size: 11px;
}

.clause-list-item > strong {
  display: -webkit-box;
  margin-top: 0;
  overflow: hidden;
  color: var(--text-primary);
  font-size: 13px;
  line-height: 1.55;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 3;
}

.clause-list-location,
.clause-list-coverage,
.clause-list-product {
  display: block;
  margin-top: 0;
  overflow: hidden;
  color: var(--text-secondary);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.clause-list-product {
  color: var(--text-regular);
}

.clause-list-coverage {
  color: var(--primary);
}

.clause-list-item:focus-visible {
  outline: 2px solid var(--primary);
  outline-offset: -2px;
}

.template-row-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: auto;
  padding-top: 8px;
  border-top: 1px solid var(--border-color);
}

.template-row-actions .el-button {
  max-width: 100%;
}

.clause-detail {
  min-width: 0;
  padding: 20px 22px 24px;
  background: var(--bg-card);
}

.clause-detail-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 15px;
  border-bottom: 1px solid var(--border-color);
}

.clause-detail-head > div {
  min-width: 0;
}

.clause-detail-head-actions {
  display: flex;
  align-items: center;
  flex: 0 0 auto;
  gap: 8px;
}

.clause-detail-drawer :deep(.el-drawer__body) {
  padding: 0;
  overflow: auto;
}

.clause-detail h3 {
  margin: 5px 0 0;
  color: var(--text-primary);
  font-size: 18px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.detail-route-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px;
  margin-top: 16px;
  padding: 10px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-soft);
}

.detail-route-item {
  min-width: 0;
  display: grid;
  align-content: start;
  gap: 4px;
}

.detail-route-item strong {
  overflow: hidden;
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.detail-kv-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px;
}

.detail-kv-grid > div {
  min-width: 0;
  display: grid;
  gap: 4px;
  padding: 9px 10px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-soft);
}

.detail-kv-grid span {
  color: var(--text-secondary);
  font-size: 11px;
}

.detail-kv-grid strong {
  color: var(--text-primary);
  font-size: 12px;
  line-height: 1.55;
  overflow-wrap: anywhere;
}

.mt-8 {
  margin-top: 8px;
}

.detail-chip-list--inline {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.classification-provenance {
  display: flex;
  flex-wrap: wrap;
  gap: 6px 14px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.detail-section--classification {
  padding-bottom: 14px;
  border-bottom: 1px solid var(--border-color);
}

.detail-section {
  display: grid;
  gap: 7px;
  margin-top: 18px;
}

.detail-section p {
  margin: 0;
  color: var(--text-regular);
  font-size: 13px;
  line-height: 1.7;
  white-space: pre-wrap;
}

.detail-section--warning {
  padding: 12px;
  border: 1px solid rgba(var(--warning-rgb), 0.35);
  border-radius: 6px;
  background: rgba(var(--warning-rgb), 0.055);
}

.detail-chip-list {
  display: grid;
  gap: 8px;
}

.detail-chip {
  display: grid;
  gap: 3px;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-soft);
}

.detail-chip strong {
  color: var(--text-primary);
  font-size: 13px;
}

.technical-match-heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}

.product-match-list {
  display: grid;
  gap: 10px;
}

.product-match-card {
  display: grid;
  gap: 9px;
  padding: 12px;
  border: 1px solid var(--border-color);
  border-radius: 6px;
  background: var(--bg-soft);
}

.product-match-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.product-match-title {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.product-match-title strong {
  color: var(--text-primary);
  font-size: 13px;
}

.product-match-title span,
.product-match-description,
.product-match-fragment,
.product-match-gap {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.product-match-description {
  margin: 0;
}

.product-parameter-block {
  display: grid;
  gap: 6px;
  padding-top: 9px;
  border-top: 1px solid var(--border-color);
}

.product-parameter-title {
  color: var(--text-regular);
  font-size: 12px;
  font-weight: 600;
}

.product-parameter-row {
  display: grid;
  grid-template-columns: minmax(76px, auto) minmax(0, 1fr);
  gap: 10px;
  align-items: start;
  padding: 8px 9px;
  border: 1px solid var(--border-color);
  border-radius: 5px;
  background: var(--bg-card);
}

.product-parameter-id {
  color: var(--primary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 11px;
  overflow-wrap: anywhere;
}

.product-parameter-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.product-parameter-copy strong {
  color: var(--text-primary);
  font-size: 12px;
  font-weight: 500;
  line-height: 1.6;
  white-space: pre-wrap;
}

.product-parameter-copy small {
  color: var(--text-secondary);
  font-size: 11px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}

.product-match-fragment,
.product-match-gap {
  display: block;
}

.product-match-gap {
  color: var(--warning);
}

.detail-chip span,
.detail-chip small,
.detail-muted {
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.detail-bullet-list {
  margin: 0;
  padding-left: 18px;
  color: var(--text-regular);
  font-size: 13px;
  line-height: 1.7;
}

.detail-section--source {
  padding-top: 15px;
  border-top: 1px solid var(--border-color);
}

.detail-section blockquote {
  max-height: 150px;
  margin: 0;
  padding: 10px 12px;
  overflow: auto;
  border-left: 3px solid var(--border-color-dark);
  background: var(--bg-soft);
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.65;
  white-space: pre-wrap;
}

.detail-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 20px;
  padding-top: 15px;
  border-top: 1px solid var(--border-color);
}

.detail-actions .el-select {
  width: 180px;
}

.template-detail-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 14px;
  padding-top: 14px;
  border-top: 1px solid var(--border-color);
}

.supplement-list {
  display: grid;
  gap: 8px;
}

.supplement-item {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid var(--border-color);
  border-radius: 8px;
  background: var(--bg-soft);
}

.supplement-item .el-icon {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--primary);
}

.supplement-item-copy {
  min-width: 0;
  display: grid;
  gap: 3px;
}

.supplement-item-copy strong {
  color: var(--text-primary);
  font-size: 13px;
  overflow-wrap: anywhere;
}

.supplement-item-copy small {
  color: var(--text-secondary);
  font-size: 11px;
  word-break: break-all;
}

.supplement-product-row {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}

.supplement-product-row .el-select {
  flex: 1;
}

.supplement-upload {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-height: 46px;
  padding: 10px 12px;
  border: 1px dashed var(--border-color);
  border-radius: 8px;
  background: var(--bg-soft);
  color: var(--text-secondary);
  cursor: pointer;
  font-size: 13px;
}

.supplement-upload:hover {
  border-color: rgba(var(--primary-rgb), 0.5);
  color: var(--primary);
}

.supplement-upload .el-icon {
  flex: 0 0 auto;
}

.supplement-upload span {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.hidden-file-input {
  display: none;
}

@media (max-width: 1100px) {
  .module-navigation-group {
    grid-template-columns: 120px minmax(0, 1fr);
  }

  .module-navigation-items {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }

  .matching-project-info-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .detail-route-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 800px) {
  .result-header,
  .decision-strip,
  .module-toolbar {
    align-items: stretch;
    flex-direction: column;
  }

  .decision-strip-stats {
    justify-content: flex-start;
  }

  .decision-strip-stats span:first-child {
    padding-left: 0;
    border-left: 0;
  }

  .overview-layout {
    grid-template-columns: 1fr;
  }

  .module-list {
    grid-template-columns: repeat(auto-fill, minmax(240px, 1fr));
  }

  .module-toolbar-actions {
    width: 100%;
  }

  .module-toolbar-actions .el-input,
  .module-toolbar-actions .el-select {
    width: 100%;
  }

  .module-toolbar-actions .el-input {
    flex: 1;
  }
}

@media (max-width: 560px) {
  .module-navigation-group {
    grid-template-columns: 1fr;
  }

  .module-navigation-items {
    grid-template-columns: 1fr;
  }

  .matching-project-info-grid {
    grid-template-columns: 1fr;
  }

  .matching-tab {
    min-height: 62px;
  }

  .matching-tab-copy small {
    display: none;
  }

  .decision-strip-stats {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    width: 100%;
  }

  .decision-strip-stats span {
    padding-left: 8px;
    font-size: 11px;
    white-space: normal;
  }

  .decision-strip-stats strong {
    display: block;
    margin: 0;
  }

  .clause-detail {
    padding: 16px;
  }

  .detail-route-grid {
    grid-template-columns: 1fr;
  }

  .clause-detail-head {
    flex-direction: column;
  }

  .detail-actions {
    align-items: stretch;
    flex-direction: column;
  }

  .detail-actions .el-select {
    width: 100%;
  }

  .extraction-mode-row {
    align-items: stretch;
    flex-direction: column;
  }
}
</style>
