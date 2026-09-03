const http = require("http");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const zlib = require("zlib");
const { execFileSync } = require("child_process");
const { URL, pathToFileURL } = require("url");
const { buildResponseDocx } = require("./docx-export");

const ROOT = __dirname;
const PORT = Number(process.env.PORT || 8793);
const startOfToday = () => {
  const now = new Date();
  return new Date(now.getFullYear(), now.getMonth(), now.getDate());
};
const readJson = (file) => JSON.parse(fs.readFileSync(path.join(ROOT, file), "utf8"));
const company = readJson("company-profile.json");
const ontology = readJson("ontology-governance.json");
const configPath = path.join(ROOT, "agent-config.json");
const catalogPath = path.join(ROOT, "demo-data", "tender-catalog.json");
const runtimeRoot = path.join(ROOT, "runtime");
const runtimeCatalogPath = path.join(runtimeRoot, "catalog.json");
const libraryRoot = path.join(runtimeRoot, "libraries");
const libraryIndexPath = path.join(runtimeRoot, "libraries.json");
const searchRunRoot = path.join(runtimeRoot, "searches");
const schedulerStatePath = path.join(runtimeRoot, "scheduler-state.json");
const remindersPath = path.join(runtimeRoot, "reminders.json");
const downloadRoot = path.join(ROOT, "runtime", "downloads");
const exportRoot = path.join(ROOT, "runtime", "exports");
const projectRoot = path.join(runtimeRoot, "projects");
const automationRunPath = path.join(ROOT, "runtime", "automation-runs.json");
const piAgentRunsPath = path.join(ROOT, "runtime", "pi-agent-runs.json");
const baseCatalog = fs.existsSync(catalogPath) ? readJson("demo-data/tender-catalog.json") : [];
let catalog = fs.existsSync(runtimeCatalogPath) ? readJson("runtime/catalog.json") : [...baseCatalog];
const workspaces = new Map();
let config = fs.existsSync(configPath) ? readJson("agent-config.json") : {
  profileName: "默认检索画像",
  enabled: true,
  targetServiceTypes: [],
  targetRegions: [],
  includeKeywords: [],
  excludeKeywords: [],
  scoreWeights: { qualification: 14, performance: 22, personnel: 14, region: 12, serviceType: 18, time: 10, documentReadiness: 10 },
  decisionThresholds: { recommend: 78, review: 58 },
  ai: { enabled: true, provider: "auto", model: "qwen2.5:7b" },
  retrieval: { startDate: "" },
  platformAccounts: [],
  automation: {
    enabled: true,
    currentOnly: true,
    maxProjects: 10,
    schedule: { enabled: true, time: "09:00", timezone: "Asia/Shanghai", action: "search" },
    monitoring: { enabled: true, intervalMinutes: 30 },
  },
  notifications: { pagePopupEnabled: true, wecom: { enabled: false, webhookUrl: "" } },
};
const defaultSearchConfig = {
  enabled: true,
  timeoutSeconds: 12,
  maxResultsPerSource: 12,
  sources: [
    {
      id: "ccgp-central",
      name: "中国政府采购网（中央公告）",
      enabled: true,
      format: "html-list",
      adapter: "ccgp-central",
      urlTemplate: "https://www.ccgp.gov.cn/cggg/zygg/index.htm",
      defaultRegion: "全国",
      accessMode: "public",
    },
    {
      id: "ggzy-national",
      name: "全国公共资源交易平台",
      enabled: true,
      format: "html-list",
      urlTemplate: "https://www.ggzy.gov.cn/",
      defaultRegion: "全国",
      accessMode: "public",
    },
  ],
};
config.automation = {
  enabled: true,
  currentOnly: true,
  maxProjects: 10,
  schedule: { enabled: true, time: "09:00", timezone: "Asia/Shanghai", action: "search" },
  monitoring: { enabled: true, intervalMinutes: 30 },
  ...(config.automation || {}),
  schedule: { enabled: true, time: "09:00", timezone: "Asia/Shanghai", action: "search", ...(config.automation?.schedule || {}) },
  monitoring: { enabled: true, intervalMinutes: 30, ...(config.automation?.monitoring || {}) },
};
config.retrieval = normalizeRetrievalConfig(config.retrieval);
config.ai = {
  enabled: true,
  provider: "auto",
  model: "qwen2.5:7b",
  temperature: 0.2,
  semanticEnabled: true,
  draftingEnabled: true,
  ...config.ai,
};
config.search = {
  ...defaultSearchConfig,
  ...(config.search || {}),
  sources: Array.isArray(config.search?.sources) && config.search.sources.length ? config.search.sources : defaultSearchConfig.sources,
};
config.notifications = normalizeNotificationConfig(config.notifications);
config.platformAccounts = normalizePlatformAccounts(config.platformAccounts);
for (const dir of [runtimeRoot, downloadRoot, exportRoot, projectRoot, libraryRoot, searchRunRoot]) fs.mkdirSync(dir, { recursive: true });
let automationRuns = fs.existsSync(automationRunPath) ? readJson("runtime/automation-runs.json") : [];
let piAgentRuns = fs.existsSync(piAgentRunsPath) ? readJson("runtime/pi-agent-runs.json") : [];
let reminders = fs.existsSync(remindersPath) ? readJson("runtime/reminders.json") : [];
const platformSessions = new Map();
let automationRunning = false;
let schedulerState = fs.existsSync(schedulerStatePath) ? readJson("runtime/scheduler-state.json") : {};
schedulerState = {
  lastScheduledDate: null,
  lastScheduledSearch: null,
  monitoringLastStartedAt: null,
  monitoringLastRunAt: null,
  monitoringNextRunAt: null,
  monitoringLastStatus: "",
  sourceHealth: {},
  ...schedulerState,
  sourceHealth: schedulerState.sourceHealth && typeof schedulerState.sourceHealth === "object" ? schedulerState.sourceHealth : {},
};

const defaultLibraries = {
  company: {
    key: "company",
    name: "公司资料库",
    description: "企业资质、业绩、人员与服务能力",
    documents: [{ id: "company-profile", name: "公司基础信息", type: "公司信息", savedAt: new Date().toISOString(), source: "系统初始化" }],
  },
  tenders: {
    key: "tenders",
    name: "招标项目库",
    description: "公开公告、招标文件与已下载项目",
    documents: [],
  },
  templates: {
    key: "templates",
    name: "响应模板库",
    description: "项目理解、服务方案与承诺模板",
    documents: [],
  },
  searches: {
    key: "searches",
    name: "搜索记录库",
    description: "保存实时搜索条件、来源回执和结果索引",
    documents: [],
  },
  outputs: {
    key: "outputs",
    name: "自动化产出库",
    description: "保存自动下载文件、匹配工作台和响应文件导出包索引",
    documents: [],
  },
};
let libraries = fs.existsSync(libraryIndexPath)
  ? { ...defaultLibraries, ...readJson("runtime/libraries.json") }
  : defaultLibraries;

function migrateLibraryIndex() {
  const companyProfile = (Array.isArray(libraries.company?.documents) ? libraries.company.documents : []).find((document) => document.id === "company-profile");
  if (companyProfile) {
    companyProfile.name = "公司基础信息";
    companyProfile.type = "公司信息";
    delete companyProfile.storagePath;
  }
  if (!Array.isArray(libraries.outputs.projects)) libraries.outputs.projects = [];
  migrateLegacyProjectFiles();
  persistLibraries();
}

function seedLibraryDocument(key, document) {
  const library = libraries[key];
  if (!library) return false;
  const fileName = String(document.fileName || `${document.id}.md`).replace(/[\\/:*?"<>|]/g, "_").slice(0, 120);
  const directory = path.join(libraryRoot, key);
  fs.mkdirSync(directory, { recursive: true });
  const target = safeFile(directory, fileName);
  if (!fs.existsSync(target)) fs.writeFileSync(target, document.content, "utf8");
  const record = {
    id: document.id,
    name: document.name,
    type: document.type || "资料文档",
    savedAt: new Date().toISOString(),
    storagePath: `runtime/libraries/${key}/${fileName}`,
    contentHash: hash(document.content),
    mimeType: document.mimeType || "text/markdown",
    size: Buffer.byteLength(document.content, "utf8"),
    preview: isTextDocument(document.name, document.mimeType || "text/markdown") ? document.content.slice(0, 12000) : "",
    source: document.source || "系统初始化",
  };
  const index = array(library.documents).findIndex((item) => item.id === document.id);
  if (index >= 0) {
    library.documents[index] = { ...library.documents[index], ...record };
    return false;
  }
  library.documents = [...array(library.documents), record];
  return true;
}

function seedDemoMaterials() {
  const companySeeds = [
    {
      id: "demo-company-overview",
      name: "公司综合介绍（演示）",
      type: "公司资料",
      fileName: "demo-company-overview.md",
      content: `# 公司综合介绍（演示）

濮阳人才服务有限公司为本次招标智策演示环境中的虚拟企业资料主体，聚焦劳务派遣、人力资源服务、安保服务与后勤保障服务。

## 基本定位
- 服务区域：河南省、濮阳市，并可覆盖周边地市的项目协同
- 主营方向：劳务派遣、招聘外包、驻场管理、安保外包、保洁支持、校园与医院后勤服务
- 服务对象：医院、学校、事业单位、国企及国有平台公司

## 交付特点
- 按项目建立独立台账、人员档案和月度服务报告
- 支持招标文件解析、资格核验、业绩梳理和投标文件编制
- 可配合电子化招投标流程输出 Markdown / JSON 底稿包

## 演示说明
该资料包用于测试公司资料库、AI 语义匹配和响应文件初稿生成，不代表真实对外承诺。`,
    },
    {
      id: "demo-company-qualifications",
      name: "资质证照与合规说明（演示）",
      type: "公司资料",
      fileName: "demo-company-qualifications.md",
      content: `# 资质证照与合规说明（演示）

## 资质清单
- 营业执照
- 人力资源服务许可证
- 劳务派遣经营许可证
- 安全生产相关管理制度文件
- 企业内部用工与档案管理制度

## 合规承诺
- 近三年无重大违法违规记录
- 近三年无严重失信记录
- 可提供员工劳动合同、社保缴纳、岗位培训和考勤记录
- 可配合招标方完成现场核验、补充说明与材料复核

## 适配场景
- 医院类项目：护士、后勤、导诊、保洁和辅助岗位
- 教育类项目：校内保安、宿管、勤杂、后勤与临时支援岗位
- 国企类项目：驻场服务、客服接待、秩序维护、业务辅助岗位`,
    },
    {
      id: "demo-company-performance",
      name: "近三年典型业绩（演示）",
      type: "公司资料",
      fileName: "demo-company-performance.md",
      content: `# 近三年典型业绩（演示）

## 业绩案例
1. 濮阳市某医院劳务派遣服务项目：负责招聘、入职、考勤、薪酬核算和月度报表。
2. 某市人民医院后勤及劳务派遣综合服务项目：覆盖驻场管理、投诉处理与应急响应。
3. 濮阳市某学校安保与辅助岗位服务项目：负责门岗值守、夜间巡查和临时用工支援。
4. 某国有平台公司综合服务项目：提供业务辅助、客户接待、档案整理和临时调度支持。

## 业绩特点
- 可按医院、教育、国企三类行业快速复用投标文本
- 项目经验可映射到资格审查、人员配置和服务方案章节
- 业绩材料可进一步补充合同首页、验收单和中标通知书`,
    },
    {
      id: "demo-company-capacity",
      name: "人员配置与服务能力（演示）",
      type: "公司资料",
      fileName: "demo-company-capacity.md",
      content: `# 人员配置与服务能力（演示）

## 核心岗位
- 项目负责人：1 名，统筹招投标、履约和客户沟通
- 项目经理：2 名，负责项目落地、质量控制和例会
- 招聘专员：6 名，负责招聘、筛选、入职和补岗
- 现场管理人员：4 名，负责驻场巡检、考勤和问题闭环
- 薪酬社保专员：2 名，负责薪酬核算、社保申报和台账维护

## 服务机制
- 每个项目建立独立沟通群、周报与月报
- 关键节点设置提醒：资格材料、投标截止、合同签订、进场安排
- 对医院和教育项目可优先输出服务方案、应急预案和岗位配置表

## 质量控制
- 资料初审、业务复核、法务复核、授权人确认四级流程
- 所有材料留存原文、版本号和哈希值，便于追溯`,
    },
  ];

  const templateSeeds = [
    {
      id: "demo-response-template",
      name: "响应文件模板（演示）",
      type: "响应文件模板",
      fileName: "demo-response-template.md",
      content: `# {{项目名称}}响应文件

> 适用于医院、教育、国企及安保类劳务服务项目的演示模板。

## 一、投标函
致：{{招标人}}\n\n我方就{{项目名称}}提交响应文件，承诺按招标文件要求提供服务。

## 二、法定代表人授权与资格文件
- 营业执照
- 人力资源服务许可证
- 劳务派遣经营许可证
- 其他资格证明材料

## 三、项目理解
{{项目名称}}位于{{服务地点}}，服务类型为{{服务类型}}。我方将围绕人员组织、服务流程、质量管理和风险响应建立项目机制。

## 四、服务方案
- 招聘与入职管理
- 考勤与排班管理
- 薪酬核算与社保协同
- 投诉处理与应急响应
- 月度报告与资料归档

## 五、人员配置
- 项目负责人
- 项目经理
- 招聘专员
- 驻场管理人员
- 薪酬社保专员

## 六、业绩与案例
- 与项目类型相匹配的医院、学校和国企服务案例

## 七、质量与应急承诺
- 建立日报、周报、月报和问题闭环机制
- 建立人员缺岗、投诉和突发事件响应机制

## 八、附件清单
- 资质证照
- 业绩证明
- 人员证明
- 服务方案
- 偏离表与承诺函

> 本模板用于招标智策 v1 的 AI 响应文件生成测试，可在此基础上按招标文件补充具体章节。`,
    },
  ];

  let changed = false;
  for (const document of companySeeds) changed = seedLibraryDocument("company", document) || changed;
  for (const document of templateSeeds) changed = seedLibraryDocument("templates", document) || changed;
  if (changed) persistLibraries();
}

function legacyRuntimeFile(file = {}) {
  const url = String(file.url || "");
  const storagePath = String(file.storagePath || "");
  if (url.startsWith("/runtime/downloads/") || storagePath.startsWith("runtime/downloads/")) {
    return safeFile(downloadRoot, path.basename(decodeURIComponent(url || storagePath)));
  }
  if (url.startsWith("/runtime/exports/") || storagePath.startsWith("runtime/exports/")) {
    return safeFile(exportRoot, path.basename(decodeURIComponent(url || storagePath)));
  }
  return null;
}

function migrateProjectFile(projectId, section, file = {}) {
  const legacyFile = legacyRuntimeFile(file);
  if (!legacyFile || !fs.existsSync(legacyFile)) return null;
  const fileName = path.basename(file.name || legacyFile);
  const destination = projectFile(projectId, section, fileName);
  if (!fs.existsSync(destination)) fs.copyFileSync(legacyFile, destination);
  return { name: fileName, url: projectFileUrl(projectId, section, fileName) };
}

function migrateLegacyProjectFiles() {
  for (const document of array(libraries.tenders?.documents)) {
    const projectId = document.projectId || document.id;
    if (!projectId) continue;
    const legacyFiles = array(document.sourceFiles).length ? document.sourceFiles : [{
      name: document.name,
      storagePath: document.storagePath,
      url: documentFileUrl(document),
    }];
    const sourceFiles = legacyFiles
      .map((file) => migrateProjectFile(projectId, "source", file) || file)
      .filter((file) => file?.url);
    if (sourceFiles.some((file) => file.url.startsWith("/runtime/projects/"))) {
      document.projectId = projectId;
      document.projectName = document.projectName || document.name;
      document.sourceFiles = uniqueFiles(sourceFiles);
      document.storagePath = `runtime/projects/${projectFolder(projectId)}/source/${document.sourceFiles[0].name}`;
    }
  }

  for (const document of array(libraries.outputs?.documents)) {
    const run = parseDocumentJson(document);
    for (const project of array(run?.projects)) {
      if (!project?.id) continue;
      const sourceFiles = project.download
        ? [migrateProjectFile(project.id, "source", {
          name: project.download.fileName,
          url: project.download.downloadUrl,
        })].filter(Boolean)
        : [];
      const outputFiles = array(project.exportFiles)
        .map((file) => migrateProjectFile(project.id, "outputs", file) || file)
        .filter((file) => file?.url);
      recordProjectArtifacts({ id: project.id, title: project.title }, {
        sourceFiles,
        outputFiles,
        workspaceId: project.workspaceId,
        decision: project.decision,
        score: project.score,
        status: project.needsReview ? "待人工复核" : "已完成",
      });
    }
  }
}

function persistCatalog() {
  fs.writeFileSync(runtimeCatalogPath, JSON.stringify(catalog, null, 2), "utf8");
}

function persistLibraries() {
  fs.writeFileSync(libraryIndexPath, JSON.stringify(libraries, null, 2), "utf8");
}

function persistSchedulerState() {
  fs.writeFileSync(schedulerStatePath, JSON.stringify(schedulerState, null, 2), "utf8");
}

function persistReminders() {
  fs.writeFileSync(remindersPath, JSON.stringify(reminders.slice(-100), null, 2), "utf8");
}

function registerLibraryDocument(key, document) {
  const library = libraries[key];
  if (!library) return;
  const index = array(library.documents).findIndex((item) => item.id === document.id);
  if (index >= 0) library.documents[index] = { ...library.documents[index], ...document };
  else library.documents = [...array(library.documents), document];
  persistLibraries();
}

function librarySnapshot() {
  return Object.values(libraries).map((item) => ({
    key: item.key,
    name: item.name,
    description: item.description,
    documentCount: array(item.documents).length,
    latestSavedAt: array(item.documents).map((doc) => doc.savedAt).sort().at(-1) || null,
    documents: array(item.documents).slice(-8).reverse(),
  }));
}

function reminderSnapshot() {
  return reminders.slice(-20).reverse();
}

function createReminderRecord(payload = {}) {
  const workspace = payload.workspaceId ? workspaces.get(payload.workspaceId) : null;
  const project = workspace?.project || {};
  const createdAt = new Date().toISOString();
  const record = {
    id: uid("reminder"),
    createdAt,
    workspaceId: payload.workspaceId || workspace?.bidWorkspaceId || "",
    projectId: project.source?.projectId || project.tenderProjectId || payload.projectId || "",
    projectTitle: project.projectName || payload.projectTitle || "未命名项目",
    level: ["低", "中", "高"].includes(payload.level) ? payload.level : "中",
    recipient: String(payload.recipient || "业务人员").trim() || "业务人员",
    channel: String(payload.channel || "页面提醒").trim() || "页面提醒",
    channels: array(payload.channels).length ? array(payload.channels) : [String(payload.channel || "页面提醒").trim() || "页面提醒"],
    status: String(payload.status || "已提醒").trim() || "已提醒",
    notificationStatus: String(payload.notificationStatus || payload.status || "已提醒").trim() || "已提醒",
    notifiedAt: payload.notifiedAt || createdAt,
    readAt: payload.readAt || null,
    wecomStatus: String(payload.wecomStatus || "").trim(),
    message: String(payload.message || `请关注 ${project.projectName || payload.projectTitle || "该项目"} 的投标进展。`).trim(),
    source: String(payload.source || "手动触发").trim() || "手动触发",
  };
  reminders = [...reminders, record];
  persistReminders();
  return record;
}

function automatedReminderForProject(projectId) {
  return reminders.find((record) => record.projectId === projectId && record.source === "实时搜索");
}

function notificationLevel(item) {
  if (item.matchDecision === "建议参与" || Number(item.matchScore) >= Number(config.decisionThresholds?.recommend || 78)) return "高";
  return "中";
}

function wecomWebhookUrl() {
  const parsed = safeRemoteUrl(config.notifications?.wecom?.webhookUrl);
  if (!parsed || parsed.protocol !== "https:" || parsed.hostname !== "qyapi.weixin.qq.com") {
    throw new Error("企业微信机器人地址无效，请在配置中填写企业微信机器人 Webhook。");
  }
  return parsed;
}

async function sendWecomMarkdown(content) {
  const endpoint = wecomWebhookUrl();
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        msgtype: "markdown",
        markdown: {
          content,
        },
      }),
      signal: controller.signal,
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || Number(payload.errcode || 0) !== 0) {
      throw new Error(payload.errmsg || `HTTP ${response.status}`);
    }
    return "推送成功";
  } catch (error) {
    const detail = String(error?.message || "网络连接失败").replace(/\s+/g, " ").trim();
    throw new Error(`企业微信推送失败：${detail}`);
  } finally {
    clearTimeout(timeout);
  }
}

async function sendWecomNotification(item, message) {
  const detailLines = [`## 招标项目提醒`, message, `> 截止时间：${item.bidDeadline || "待核验"}`];
  if (safeRemoteUrl(item.sourceUrl)) detailLines.push(`> 公告链接：[打开项目](${item.sourceUrl})`);
  return sendWecomMarkdown(detailLines.join("\n\n"));
}

async function sendWecomTestNotification() {
  return sendWecomMarkdown([
    "## 招标智策测试提醒",
    "企业微信机器人已连接成功。",
    `> 测试时间：${new Date().toLocaleString("zh-CN", { hour12: false })}`,
    "> 后续发现新的匹配招标项目时，将自动推送到本群。",
  ].join("\n\n"));
}

async function notifyNewTender(item) {
  if (!item?.id || automatedReminderForProject(item.id)) return null;
  const channels = [];
  const message = `发现匹配项目：${item.title}（${item.region || "区域待识别"} · ${item.category || "服务待识别"}），请尽快查看公告并评估是否参与。`;
  if (config.notifications?.pagePopupEnabled !== false) channels.push("页面弹窗");
  let wecomStatus = config.notifications?.wecom?.enabled === true ? "待推送" : "未启用";
  if (config.notifications?.wecom?.enabled === true) {
    try {
      wecomStatus = await sendWecomNotification(item, message);
      channels.push("企业微信");
    } catch (error) {
      wecomStatus = String(error.message || "推送失败").slice(0, 300);
    }
  }
  if (!channels.length) return null;
  item.notificationStatus = "已提醒";
  item.notifiedAt = new Date().toISOString();
  item.readAt = null;
  item.notificationChannels = channels;
  const reminder = createReminderRecord({
    projectId: item.id,
    projectTitle: item.title,
    level: notificationLevel(item),
    recipient: "业务人员",
    channel: channels.join("、"),
    channels,
    status: "已提醒",
    notificationStatus: "已提醒",
    notifiedAt: item.notifiedAt,
    wecomStatus,
    message,
    source: "实时搜索",
  });
  return {
    id: item.id,
    projectTitle: item.title,
    bidDeadline: item.bidDeadline || "",
    matchScore: item.matchScore ?? null,
    channels,
    pagePopup: channels.includes("页面弹窗"),
    wecomStatus,
    reminderId: reminder.id,
  };
}

function markTenderAsRead(projectId) {
  const item = catalog.find((entry) => entry.id === projectId);
  if (!item) return null;
  if (item.notificationStatus === "已提醒") {
    const readAt = new Date().toISOString();
    item.notificationStatus = "已查阅";
    item.readAt = readAt;
    for (const reminder of reminders) {
      if (reminder.projectId === item.id && reminder.notificationStatus === "已提醒") {
        reminder.notificationStatus = "已查阅";
        reminder.status = "已查阅";
        reminder.readAt = readAt;
      }
    }
    persistCatalog();
    persistReminders();
  }
  return item;
}

function setTenderFollowUp(projectId, followUp, followUpStatus = "") {
  const item = catalog.find((entry) => entry.id === projectId);
  if (!item) return null;
  if (followUp) {
    const completed = followUpStatus === "已完成";
    item.followUp = true;
    item.followUpAt = item.followUpAt || new Date().toISOString();
    item.followUpStatus = completed ? "已完成" : "跟进中";
    item.followUpCompletedAt = completed ? (item.followUpCompletedAt || new Date().toISOString()) : "";
  } else {
    item.followUp = false;
    item.followUpAt = "";
    item.followUpStatus = "";
    item.followUpCompletedAt = "";
  }
  persistCatalog();
  return item;
}

function storedDocumentPreview(storagePath) {
  if (!storagePath || !String(storagePath).startsWith("runtime/")) return "";
  const target = path.resolve(ROOT, storagePath);
  if (!target.startsWith(runtimeRoot + path.sep) || !fs.existsSync(target)) return "";
  try {
    const buffer = fs.readFileSync(target);
    if (buffer.includes(0)) return "";
    return buffer.toString("utf8").slice(0, 12000);
  } catch {
    return "";
  }
}

function documentFileUrl(document) {
  if (document.url) return document.url;
  if (!document.storagePath || !String(document.storagePath).startsWith("runtime/")) return "";
  return `/${String(document.storagePath).split(path.sep).join("/").split("\\").join("/")}`;
}

function documentPreview(document) {
  if (document.preview) return document.preview;
  const stored = storedDocumentPreview(document.storagePath);
  if (stored) return stored;
  if (/\.pdf$/i.test(document.name || "")) return extractedPdfText(document.storagePath).slice(0, 12000);
  if (/\.docx$/i.test(document.name || "") && document.storagePath) {
    try {
      const target = path.resolve(ROOT, document.storagePath);
      if (target.startsWith(runtimeRoot + path.sep) && fs.existsSync(target)) return extractDocxText(fs.readFileSync(target));
    } catch {
      return "";
    }
  }
  return "";
}

function libraryListPreview(document) {
  if (document.preview) return document.preview;
  const stored = storedDocumentPreview(document.storagePath);
  if (stored) return stored;
  // PDF 原件由前端 iframe 预览，列表接口不再同步提取全文。
  if (/\.pdf$/i.test(document.name || "")) return "";
  return documentPreview(document);
}

function parseDocumentJson(document) {
  const preview = documentPreview(document);
  try {
    return JSON.parse(preview);
  } catch {
    return null;
  }
}

function libraryProjectEntries() {
  const groups = new Map();
  for (const document of array(libraries.tenders?.documents)) {
    const projectId = document.projectId || document.id;
    const current = groups.get(projectId) || {
      id: projectId,
      name: document.projectName || String(document.name || "未命名项目").replace(/\.(txt|md|json)$/i, ""),
      category: document.category || "招标项目",
      region: document.region || "待识别",
      source: document.source || "本地保存",
      savedAt: document.savedAt,
      sourceFiles: [],
      outputFiles: [],
      decision: document.decision || "",
      score: document.score,
    };
    const files = array(document.sourceFiles).length ? document.sourceFiles : [{
      name: document.name,
      url: documentFileUrl(document),
    }];
    current.sourceFiles.push(...files.filter((file) => file.url));
    current.savedAt = [current.savedAt, document.savedAt].filter(Boolean).sort().at(-1) || current.savedAt;
    groups.set(projectId, current);
  }
  for (const project of array(libraries.outputs?.projects)) {
    const current = groups.get(project.projectId) || {
      id: project.projectId,
      name: project.projectName || "未命名项目",
      category: "自动化项目",
      region: "待识别",
      source: "自动化流程",
      savedAt: project.updatedAt || project.createdAt,
      sourceFiles: [],
      outputFiles: [],
      decision: "",
      score: undefined,
    };
    current.outputFiles.push(...array(project.outputFiles));
    current.decision = project.decision || current.decision;
    current.score = project.score ?? current.score;
    current.savedAt = [current.savedAt, project.updatedAt, project.createdAt].filter(Boolean).sort().at(-1) || current.savedAt;
    groups.set(project.projectId, current);
  }
  return [...groups.values()].sort((a, b) => String(b.savedAt || "").localeCompare(String(a.savedAt || "")));
}

function outputProjectEntries() {
  const groups = new Map();
  const ensure = (project) => {
    const projectId = project.projectId || project.id || project.workspaceId || uid("project");
    const current = groups.get(projectId) || {
      projectId,
      projectName: project.projectName || project.title || "未命名项目",
      sourceFiles: [],
      outputFiles: [],
      workspaceId: project.workspaceId || "",
      decision: project.decision || "",
      score: project.score,
      status: project.status || "已归档",
      updatedAt: project.updatedAt || project.savedAt || project.createdAt || "",
    };
    current.projectName = project.projectName || project.title || current.projectName;
    current.sourceFiles.push(...array(project.sourceFiles));
    current.outputFiles.push(...array(project.outputFiles || project.exportFiles));
    current.workspaceId = project.workspaceId || current.workspaceId;
    current.decision = project.decision || current.decision;
    current.score = project.score ?? current.score;
    current.status = project.status || current.status;
    current.updatedAt = [current.updatedAt, project.updatedAt, project.savedAt, project.createdAt].filter(Boolean).sort().at(-1) || current.updatedAt;
    groups.set(projectId, current);
    return current;
  };
  for (const project of array(libraries.outputs?.projects)) ensure(project);
  for (const document of array(libraries.outputs?.documents)) {
    const parsed = parseDocumentJson(document);
    for (const project of array(parsed?.projects)) {
      ensure({
        projectId: project.id,
        projectName: project.title,
        sourceFiles: project.download ? [{ name: project.download.fileName, url: project.download.downloadUrl }] : [],
        outputFiles: project.exportFiles,
        workspaceId: project.workspaceId,
        decision: project.decision,
        score: project.score,
        status: project.needsReview ? "待人工复核" : "已完成",
        savedAt: document.savedAt,
      });
    }
  }
  return [...groups.values()].map((project) => ({
    ...project,
    sourceFiles: uniqueFiles(project.sourceFiles),
    outputFiles: uniqueFiles(project.outputFiles),
  })).sort((a, b) => String(b.updatedAt || "").localeCompare(String(a.updatedAt || "")));
}

function uniqueFiles(files) {
  const seen = new Set();
  return array(files).filter((file) => {
    const key = file?.name || file?.url;
    return file?.url && key && !seen.has(key) && seen.add(key);
  });
}

function libraryDetail(key) {
  const library = libraries[key];
  if (!library) throw new Error("未知资料库");
  const documents = key === "templates"
    ? array(library.documents).slice().sort((a, b) => {
      const score = (document) => {
        const name = String(document?.name || "");
        const file = String(document?.storagePath || document?.name || "");
        if (/响应文件模板/.test(name) && /\.md$/i.test(file)) return 4;
        if (/响应文件模板/.test(name) && /\.pdf$/i.test(file)) return 3;
        if (/响应文件模板/.test(name)) return 2;
        if (/模板/.test(name)) return 1;
        return 0;
      };
      return score(b) - score(a) || String(b.savedAt || "").localeCompare(String(a.savedAt || ""));
    })
    : array(library.documents).slice().reverse();
  const detail = {
    key: library.key,
    name: library.name,
    description: library.description,
    documents: documents.map((document) => ({
      ...document,
      preview: libraryListPreview(document),
      url: documentFileUrl(document),
      previewUrl: documentFileUrl(document) ? `${documentFileUrl(document)}?preview=1` : "",
      previewAvailable: Boolean(libraryListPreview(document) || /\.pdf$/i.test(document.name || "") || /\.docx$/i.test(document.name || "")),
    })),
  };
  if (key === "company") detail.companyProfile = company;
  if (key === "searches") detail.searches = detail.documents.map((document) => ({
    ...document,
    searchData: parseDocumentJson(document),
  }));
  if (key === "tenders") detail.projects = libraryProjectEntries();
  if (key === "outputs") detail.projects = outputProjectEntries();
  return detail;
}

function seedDemoTender() {
  const samplePath = path.join(ROOT, "sample-tender.txt");
  if (!fs.existsSync(samplePath)) return;
  const sample = {
    id: "sample-hospital-labor-v1",
    title: "濮阳市某医院 2026 年度劳务派遣服务项目（完整招标样本）",
    category: "劳务派遣",
    region: "河南省濮阳市",
    noticeType: "招标公告",
    publishedDate: "2026-09-01",
    bidDeadline: "2026-09-08 09:30",
    source: "本地脱敏示例",
    sourceUrl: "/api/catalog/sample-hospital-labor-v1",
    dataOrigin: "local_demo",
    analysisText: fs.readFileSync(samplePath, "utf8"),
  };
  const existing = catalog.find((item) => item.id === sample.id);
  if (existing) Object.assign(existing, sample, { analysisText: existing.analysisText || sample.analysisText });
  else catalog.push(sample);
  persistCatalog();
}

seedDemoTender();
backfillCatalogDeadlines();
backfillCatalogDocumentAccessWindows();
persistSchedulerState();

const send = (res, status, body, type = "text/plain; charset=utf-8", extra = {}) => {
  res.writeHead(status, { "Content-Type": type, "Cache-Control": "no-store", ...extra });
  res.end(body);
};
const sendJson = (res, value, status = 200) => send(res, status, JSON.stringify(value, null, 2), "application/json; charset=utf-8");
const readBody = (req) => new Promise((resolve, reject) => {
  let body = "";
  req.on("data", (chunk) => {
    body += chunk;
    if (body.length > 32 * 1024 * 1024) { reject(new Error("请求内容超过32MB")); req.destroy(); }
  });
  req.on("end", () => resolve(body));
  req.on("error", reject);
});
const hash = (value) => crypto.createHash("sha256").update(value).digest("hex");
const uid = (prefix) => `${prefix}-${Date.now()}-${crypto.randomBytes(3).toString("hex")}`;
const array = (value) => Array.isArray(value) ? value : [];
migrateLibraryIndex();
seedDemoMaterials();

function decodeHtml(value) {
  return String(value || "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/g, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCodePoint(Number(code)))
    .replace(/&#x([0-9a-f]+);/gi, (_, code) => String.fromCodePoint(parseInt(code, 16)));
}

function plainText(value) {
  return decodeHtml(String(value || "")
    .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, " ")
    .replace(/<style\b[^>]*>[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " "))
    .replace(/\s+/g, " ")
    .trim();
}

function safeRemoteUrl(value) {
  let parsed;
  try {
    parsed = new URL(String(value || ""));
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(parsed.protocol)) return null;
  const host = parsed.hostname.toLowerCase();
  const ipv4 = host.match(/^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/);
  const privateIpv4 = ipv4 && (
    Number(ipv4[1]) === 10
    || Number(ipv4[1]) === 127
    || (Number(ipv4[1]) === 169 && Number(ipv4[2]) === 254)
    || (Number(ipv4[1]) === 172 && Number(ipv4[2]) >= 16 && Number(ipv4[2]) <= 31)
    || (Number(ipv4[1]) === 192 && Number(ipv4[2]) === 168)
  );
  if (!host || host === "localhost" || host.endsWith(".localhost") || host.includes("nip.io") || host.includes("sslip.io") || privateIpv4 || host === "::1" || host.startsWith("fc") || host.startsWith("fd")) return null;
  return parsed;
}

function sourceUrl(source, search = {}) {
  const tokens = {
    keyword: search.query || "",
    region: search.region || "",
    type: search.type || "",
  };
  const template = String(source.urlTemplate || "").replace(/\{(keyword|region|type)\}/g, (_, key) => encodeURIComponent(tokens[key]));
  const parsed = safeRemoteUrl(template);
  if (!parsed) throw new Error(`数据源“${source.name || "未命名"}”的地址无效或不允许访问。`);
  return parsed.toString();
}

function normalizeSearchSource(source, index) {
  const name = String(source?.name || "").trim().slice(0, 80);
  const urlTemplate = String(source?.urlTemplate || "").trim().slice(0, 2000);
  if (!name || !urlTemplate) throw new Error(`第 ${index + 1} 个数据源需要填写名称和网址。`);
  sourceUrl({ name, urlTemplate });
  const host = (() => {
    try { return new URL(urlTemplate.replace(/\{(?:keyword|region|type)\}/g, "")).hostname.toLowerCase(); } catch { return ""; }
  })();
  const requestedAdapter = String(source?.adapter || "").trim();
  const adapter = ["ccgp-central", "ccgp-henan", "ggzy-henan", "generic"].includes(requestedAdapter)
    ? requestedAdapter
    : host === "www.ccgp.gov.cn"
      ? "ccgp-central"
      : host.includes("ccgp-henan.gov.cn") || /河南省\s*政府采购网/.test(name)
        ? "ccgp-henan"
        : host.includes("hnsggzyjy.henan.gov.cn") || /河南省\s*公共资源交易平台/.test(name)
          ? "ggzy-henan"
          : "generic";
  return {
    id: String(source?.id || `source-${hash(`${name}:${urlTemplate}`).slice(0, 10)}`).replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 60) || `source-${index + 1}`,
    name,
    enabled: source?.enabled !== false,
    format: "html-list",
    adapter,
    urlTemplate,
    defaultRegion: String(source?.defaultRegion || "全国").trim().slice(0, 80) || "全国",
    accessMode: source?.accessMode === "login" ? "login" : "public",
  };
}

function normalizeSearchConfig(search) {
  const rawSources = array(search?.sources);
  const ids = new Set();
  const sources = rawSources.slice(0, 80).map(normalizeSearchSource).map((source, index) => {
    const id = ids.has(source.id) ? `${source.id}-${index + 1}` : source.id;
    ids.add(id);
    return { ...source, id };
  });
  return {
    enabled: search?.enabled !== false,
    timeoutSeconds: Math.max(4, Math.min(30, Number(search?.timeoutSeconds || defaultSearchConfig.timeoutSeconds))),
    maxResultsPerSource: Math.max(1, Math.min(30, Number(search?.maxResultsPerSource || defaultSearchConfig.maxResultsPerSource))),
    sources,
  };
}

function normalizeRetrievalConfig(retrieval) {
  const startDate = String(retrieval?.startDate || "").trim();
  return { startDate: /^\d{4}-\d{2}-\d{2}$/.test(startDate) ? startDate : "" };
}

function normalizeNotificationConfig(notifications) {
  return {
    pagePopupEnabled: notifications?.pagePopupEnabled !== false,
    wecom: {
      enabled: notifications?.wecom?.enabled === true,
      webhookUrl: String(notifications?.wecom?.webhookUrl || "").trim().slice(0, 2000),
    },
  };
}

function normalizePlatformAccounts(accounts) {
  const raw = Array.isArray(accounts) && accounts.length ? accounts : [{
    id: "demo-platform",
    name: "演示公共资源交易平台",
    loginUrl: "",
    username: "demo-user",
    password: "demo-pass",
    enabled: true,
    simulated: true,
  }];
  const ids = new Set();
  return raw.slice(0, 30).map((account, index) => {
    const name = String(account?.name || `平台账号 ${index + 1}`).trim().slice(0, 100);
    const baseId = String(account?.id || `platform-${index + 1}`).replace(/[^a-zA-Z0-9_-]/g, "").slice(0, 60) || `platform-${index + 1}`;
    const id = ids.has(baseId) ? `${baseId}-${index + 1}` : baseId;
    ids.add(id);
    return {
      id,
      name,
      loginUrl: String(account?.loginUrl || "").trim().slice(0, 2000),
      username: String(account?.username || "").trim().slice(0, 160),
      password: String(account?.password || "").slice(0, 240),
      enabled: account?.enabled !== false,
      simulated: account?.simulated === true,
      sourceIds: Array.isArray(account?.sourceIds) ? account.sourceIds.map((item) => String(item).trim()).filter(Boolean).slice(0, 30) : [],
    };
  });
}

function platformAccountSnapshot() {
  return config.platformAccounts.map((account) => ({
    id: account.id,
    name: account.name,
    loginUrl: account.loginUrl,
    username: account.username,
    enabled: account.enabled !== false,
    simulated: account.simulated === true,
    sourceIds: array(account.sourceIds),
    passwordConfigured: Boolean(account.password),
  }));
}

function platformAuthSnapshot() {
  return {
    accounts: platformAccountSnapshot(),
    sessions: [...platformSessions.values()].map((session) => ({
      sessionId: session.sessionId,
      accountId: session.accountId,
      accountName: session.accountName,
      loggedInAt: session.loggedInAt,
      expiresAt: session.expiresAt,
    })),
  };
}

function publicConfig() {
  return {
    ...config,
    ai: {
      ...config.ai,
      apiKey: "",
      apiKeyConfigured: Boolean(config.ai?.apiKey),
    },
    notifications: {
      pagePopupEnabled: config.notifications?.pagePopupEnabled !== false,
      wecom: {
        enabled: config.notifications?.wecom?.enabled === true,
        webhookConfigured: Boolean(config.notifications?.wecom?.webhookUrl),
      },
    },
    platformAccounts: platformAccountSnapshot(),
  };
}

function mergeNotificationConfig(previous, incoming) {
  const current = normalizeNotificationConfig(previous);
  if (!incoming || typeof incoming !== "object") return current;
  return normalizeNotificationConfig({
    ...current,
    ...incoming,
    wecom: {
      ...current.wecom,
      ...(incoming.wecom || {}),
      webhookUrl: String(incoming.wecom?.webhookUrl || "") || current.wecom.webhookUrl,
    },
  });
}

function mergePlatformAccounts(previous, incoming) {
  const known = new Map(array(previous).map((account) => [account.id, account]));
  return normalizePlatformAccounts(array(incoming).map((account, index) => {
    const existing = known.get(String(account?.id || ""));
    return {
      ...account,
      id: account?.id || existing?.id || `platform-${index + 1}`,
      password: String(account?.password || "") || existing?.password || "",
    };
  }));
}

function accountForProject(item) {
  const accounts = config.platformAccounts.filter((account) => account.enabled !== false);
  return accounts.find((account) => array(account.sourceIds).includes(item.sourceKey)) || accounts[0] || null;
}

function authenticatedPlatformSession(sessionId, accountId) {
  const session = platformSessions.get(String(sessionId || ""));
  if (!session || new Date(session.expiresAt).getTime() <= Date.now()) return null;
  if (accountId && session.accountId !== accountId) return null;
  return session;
}

function downloadFailure(message, details = {}) {
  const error = new Error(message);
  error.code = "DOWNLOAD_FAILED";
  error.details = details;
  return error;
}

function aiApiBaseUrl() {
  const baseUrl = String(config.ai?.baseUrl || "").trim();
  const parsed = safeRemoteUrl(baseUrl);
  if (!parsed) throw new Error("请填写有效且可访问的大模型接口地址。");
  let pathname = parsed.pathname.replace(/\/+$/, "");
  if (!pathname.endsWith("/v1")) pathname = `${pathname}/v1`;
  parsed.pathname = `${pathname}/`;
  parsed.search = "";
  parsed.hash = "";
  return parsed;
}

async function callConfiguredModel(messages, options = {}) {
  if (config.ai?.enabled === false) throw new Error("大模型辅助当前未启用。");
  const apiKey = String(config.ai?.apiKey || "").trim();
  const model = String(config.ai?.model || "").trim();
  if (!apiKey) throw new Error("请先填写公司发放的 API Key 并保存配置。");
  if (!model) throw new Error("请先填写模型 ID 并保存配置。");
  const endpoint = new URL("chat/completions", aiApiBaseUrl());
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), Math.max(10000, Math.min(120000, Number(options.timeoutMs || 60000))));
  let response;
  try {
    response = await fetch(endpoint, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/json",
        "Accept": "application/json",
      },
      body: JSON.stringify({
        model,
        messages,
        temperature: Number.isFinite(Number(config.ai?.temperature)) ? Number(config.ai.temperature) : 0.2,
        max_tokens: Math.max(32, Math.min(6000, Number(options.maxTokens || 120))),
      }),
      signal: controller.signal,
    });
  } catch (error) {
    const detail = String(error?.cause?.message || error?.message || "网络连接失败").replace(/\s+/g, " ").trim();
    throw new Error(`无法连接大模型接口 ${endpoint.host}：${detail}`);
  } finally {
    clearTimeout(timeout);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const providerMessage = payload?.error?.message || payload?.message || `HTTP ${response.status}`;
    throw new Error(`大模型接口调用失败：${providerMessage}`);
  }
  const content = payload?.choices?.[0]?.message?.content;
  if (typeof content !== "string" || !content.trim()) throw new Error("大模型接口未返回可用文本，请核对模型 ID 和接口兼容性。");
  return { content: content.trim(), model: payload.model || model, usage: payload.usage || null };
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function remoteFetchError(error, label = "远程站点") {
  const raw = String(error?.cause?.message || error?.message || "未知网络错误").replace(/\s+/g, " ").trim();
  if (error?.name === "AbortError") return `${label}连接超时，请稍后重试。`;
  if (/access permissions|访问权限不允许|EACCES|EPERM|socket/i.test(raw)) {
    return `${label}连接被本机网络或安全策略阻止（${raw}）。请检查代理、防火墙或网络访问权限。`;
  }
  if (/ENOTFOUND|DNS|name resolution/i.test(raw)) return `${label}域名无法解析（${raw}）。请检查网络或 DNS。`;
  if (/ECONNREFUSED|ECONNRESET|ETIMEDOUT|timeout|timed out/i.test(raw)) return `${label}连接失败或超时（${raw}）。`;
  return `${label}请求失败（${raw}）。`;
}

async function fetchRemoteResponse(target, options, timeoutSeconds, label) {
  const attempts = 3;
  let lastError = null;
  for (let attempt = 1; attempt <= attempts; attempt += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutSeconds * 1000);
    try {
      const response = await fetch(target, { ...options, signal: controller.signal });
      if (response.status !== 429 && response.status < 500) return response;
      lastError = new Error(`${label}返回 HTTP ${response.status}。`);
    } catch (error) {
      lastError = new Error(remoteFetchError(error, label));
    } finally {
      clearTimeout(timeout);
    }
    if (attempt < attempts) await delay(500 * attempt);
  }
  throw new Error(`${lastError?.message || `${label}请求失败`}（已自动重试 ${attempts} 次）。`);
}

async function fetchRemoteText(initialUrl, timeoutSeconds) {
  let target = safeRemoteUrl(initialUrl);
  if (!target) throw new Error("远程地址无效或不允许访问。");
  for (let redirectCount = 0; redirectCount < 4; redirectCount += 1) {
    const response = await fetchRemoteResponse(target, {
      headers: {
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": "TenderIntelligenceAgent/1.1 (+continuous-monitoring)",
      },
      redirect: "manual",
    }, timeoutSeconds, "数据源页面");
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const location = response.headers.get("location");
      const next = location ? safeRemoteUrl(new URL(location, target).toString()) : null;
      if (!next) throw new Error("数据源重定向到了不允许访问的地址。");
      target = next;
      continue;
    }
    if (!response.ok) throw new Error(`远程站点返回 HTTP ${response.status}。`);
    const contentType = response.headers.get("content-type") || "";
    if (!/text\/html|application\/xhtml\+xml|text\/plain/i.test(contentType)) throw new Error("数据源未返回可解析的公告页面。");
    const contentLength = Number(response.headers.get("content-length") || 0);
    if (contentLength > 2 * 1024 * 1024) throw new Error("数据源响应超过 2MB 限制。");
    const content = await response.text();
    return { url: target.toString(), content: content.slice(0, 2 * 1024 * 1024) };
  }
  throw new Error("数据源重定向次数过多。");
}

async function fetchRemoteJson(initialUrl, options = {}, timeoutSeconds = 20) {
  let target = safeRemoteUrl(initialUrl);
  if (!target) throw new Error("远程接口地址无效或不允许访问。");
  for (let redirectCount = 0; redirectCount < 4; redirectCount += 1) {
    const response = await fetchRemoteResponse(target, {
      method: options.method || "GET",
      headers: {
        "Accept": "application/json,text/plain,*/*;q=0.5",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "User-Agent": "TenderIntelligenceAgent/1.1 (+continuous-monitoring)",
        ...(options.headers || {}),
      },
      body: options.body,
      redirect: "manual",
    }, timeoutSeconds, "数据源接口");
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const location = response.headers.get("location");
      const next = location ? safeRemoteUrl(new URL(location, target).toString()) : null;
      if (!next) throw new Error("数据源接口重定向到了不允许访问的地址。");
      target = next;
      continue;
    }
    if (!response.ok) throw new Error(`远程接口返回 HTTP ${response.status}。`);
    const contentLength = Number(response.headers.get("content-length") || 0);
    if (contentLength > 2 * 1024 * 1024) throw new Error("数据源接口响应超过 2MB 限制。");
    const text = await response.text();
    if (text.length > 2 * 1024 * 1024) throw new Error("数据源接口响应超过 2MB 限制。");
    try {
      return { url: target.toString(), data: JSON.parse(text) };
    } catch {
      throw new Error("数据源接口未返回有效 JSON。");
    }
  }
  throw new Error("数据源接口重定向次数过多。");
}

async function fetchRemoteFile(initialUrl, timeoutSeconds, allowedHost) {
  let target = safeRemoteUrl(initialUrl);
  if (!target) throw new Error("附件地址无效或不允许访问。");
  if (allowedHost && target.hostname !== allowedHost) throw new Error("附件地址不属于已配置公开数据源。");
  for (let redirectCount = 0; redirectCount < 4; redirectCount += 1) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), timeoutSeconds * 1000);
    let response;
    try {
      response = await fetch(target, {
        headers: {
          "Accept": "application/pdf,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document,application/zip,application/octet-stream,*/*;q=0.5",
          "Accept-Language": "zh-CN,zh;q=0.9",
          "User-Agent": "TenderIntelligenceAgent/1.0 (+local configuration)",
        },
        redirect: "manual",
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }
    if ([301, 302, 303, 307, 308].includes(response.status)) {
      const location = response.headers.get("location");
      const next = location ? safeRemoteUrl(new URL(location, target).toString()) : null;
      if (!next || (allowedHost && next.hostname !== allowedHost)) throw new Error("附件重定向到了不允许访问的地址。");
      target = next;
      continue;
    }
    if (!response.ok) throw new Error(`附件下载返回 HTTP ${response.status}。`);
    const contentLength = Number(response.headers.get("content-length") || 0);
    if (contentLength > 20 * 1024 * 1024) throw new Error("附件超过 20MB 下载限制。");
    const content = Buffer.from(await response.arrayBuffer());
    if (content.length > 20 * 1024 * 1024) throw new Error("附件超过 20MB 下载限制。");
    if (!content.length) throw new Error("附件内容为空。");
    return {
      url: target.toString(),
      content,
      contentType: response.headers.get("content-type") || "application/octet-stream",
      disposition: response.headers.get("content-disposition") || "",
    };
  }
  throw new Error("附件重定向次数过多。");
}

function normalizeDate(value, fallback) {
  const match = String(value || "").match(/(20\d{2})\s*[年\-./]\s*(\d{1,2})\s*[月\-./]\s*(\d{1,2})/);
  if (!match) return fallback;
  return `${match[1]}-${String(match[2]).padStart(2, "0")}-${String(match[3]).padStart(2, "0")}`;
}

function parseBidDeadline(value) {
  const match = String(value || "").match(/(20\d{2})\s*(?:年|[-./])\s*(\d{1,2})\s*(?:月|[-./])\s*(\d{1,2})(?:日|号)?(?:\s*(上午|下午|晚|夜)?\s*(\d{1,2})\s*(?:时|点|:|：)\s*(\d{1,2})?(?:\s*(?:分|:|：)\s*(\d{1,2}))?)?/);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hasTime = match[5] !== undefined;
  let hour = hasTime ? Number(match[5]) : 23;
  const minute = hasTime ? Number(match[6] || 0) : 59;
  const second = hasTime ? Number(match[7] || 0) : 59;
  if (match[4] && /下午|晚|夜/.test(match[4]) && hour < 12) hour += 12;
  const at = new Date(year, month - 1, day, hour, minute, second);
  if (Number.isNaN(at.getTime()) || at.getFullYear() !== year || at.getMonth() !== month - 1 || at.getDate() !== day) return null;
  const date = `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  return {
    at,
    value: hasTime ? `${date} ${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}` : date,
  };
}

function extractBidDeadline(text) {
  const content = plainText(text);
  const primaryLabel = /(?:投标截止时间|投标文件(?:递交|提交)截止时间|响应文件(?:递交|提交)截止时间|投标文件(?:递交|提交)时间|响应文件(?:递交|提交)时间|报名截止时间|报价截止时间)/g;
  let match;
  while ((match = primaryLabel.exec(content))) {
    const parsed = parseBidDeadline(content.slice(match.index, match.index + 180));
    if (parsed) return { ...parsed, evidenceLabel: match[0] };
  }
  const submitBeforeWithTime = /(20\d{2}\s*(?:年|[-./])\s*\d{1,2}\s*(?:月|[-./])\s*\d{1,2}(?:日|号)?\s*(?:上午|下午|晚|夜)?\s*\d{1,2}\s*(?:时|点|:|：)\s*\d{1,2}(?:\s*分)?).{0,36}?(?:前|截止前)?\s*(?:提交|递交)\s*(?:投标|响应)文件/g;
  while ((match = submitBeforeWithTime.exec(content))) {
    const parsed = parseBidDeadline(match[1]);
    if (parsed) return { ...parsed, evidenceLabel: "提交响应文件时间" };
  }
  // 部分采购公告只给出“响应文件开启时间”；对竞争性磋商等项目，该时间通常与递交截止时间一致。
  const openingLabel = /(?:响应文件开启时间|响应文件开启(?:日期|时刻)|开标时间)/g;
  while ((match = openingLabel.exec(content))) {
    const parsed = parseBidDeadline(content.slice(match.index, match.index + 180));
    if (parsed) return { ...parsed, evidenceLabel: match[0] };
  }
  const submitBefore = /(20\d{2}\s*(?:年|[-./])\s*\d{1,2}\s*(?:月|[-./])\s*\d{1,2}(?:日|号)?).{0,36}?(?:前|截止前)?\s*(?:提交|递交)\s*(?:投标|响应)文件/g;
  while ((match = submitBefore.exec(content))) {
    const parsed = parseBidDeadline(match[1]);
    if (parsed) return { ...parsed, evidenceLabel: "提交响应文件日期" };
  }
  return null;
}

function extractDocumentAccessWindow(text) {
  const content = plainText(text);
  const accessLabel = /(?:获取(?:采购|招标)?文件(?:的)?(?:时间)?|(?:采购|招标)文件获取(?:时间)?|报名(?:时间)?)/g;
  let match;
  while ((match = accessLabel.exec(content))) {
    const remainder = content.slice(match.index, match.index + 520);
    const nextSectionAt = remainder.search(/(?:响应文件|投标文件(?:递交|提交|截止)|开标(?:时间)?|项目概况|一、|二、|三、|四、|五、)/);
    const section = nextSectionAt > 0 ? remainder.slice(0, nextSectionAt) : remainder;
    const datePattern = /20\d{2}\s*(?:年|[-./])\s*\d{1,2}\s*(?:月|[-./])\s*\d{1,2}(?:日|号)?/g;
    const dates = [...section.matchAll(datePattern)]
      .map((entry) => parseBidDeadline(entry[0])?.value)
      .filter(Boolean);
    if (!dates.length) continue;
    return {
      start: dates[0],
      end: dates[1] || dates[0],
      evidenceLabel: match[0],
    };
  }
  return null;
}

function backfillCatalogDeadlines() {
  let changed = false;
  const verifiedAt = new Date().toISOString();
  for (const item of catalog) {
    const deadline = extractBidDeadline(item?.deadlineEvidence || item?.analysisText || "");
    if (!deadline) continue;
    const needsDeadline = !item?.bidDeadline;
    const needsTimeUpgrade = /^\d{4}-\d{2}-\d{2}$/.test(String(item.bidDeadline || "")) && /\s\d{2}:\d{2}$/.test(deadline.value);
    if (!needsDeadline && !needsTimeUpgrade) continue;
    item.bidDeadline = deadline.value;
    item.deadlineStatus = "已核验";
    item.deadlineVerifiedAt = verifiedAt;
    item.accessNote = `已从公告详情页核验${deadline.evidenceLabel || "投标截止时间"}。`;
    changed = true;
  }
  if (changed) persistCatalog();
  return changed;
}

function backfillCatalogDocumentAccessWindows() {
  let changed = false;
  for (const item of catalog) {
    if (item?.fileAccessStart && item?.fileAccessEnd) continue;
    const window = extractDocumentAccessWindow(item?.deadlineEvidence || item?.analysisText || "");
    if (!window) continue;
    item.fileAccessStart = window.start;
    item.fileAccessEnd = window.end;
    item.fileAccessEvidenceLabel = window.evidenceLabel;
    changed = true;
  }
  if (changed) persistCatalog();
  return changed;
}

function passesRetrievalStartDate(item) {
  const startDate = config.retrieval?.startDate || "";
  return !startDate || (Boolean(item.publishedDate) && item.publishedDate >= startDate);
}

function isTenderNoticeCandidate(item) {
  const evidence = `${item.title || ""} ${item.noticeType || ""} ${item.analysisText || ""}`;
  const excluded = /中标|成交|结果公告|终止|废标|流标|更正|澄清|补遗|变更|采购意向|征集意见|单一来源(?:采购)?公示|论证公示|资格预审结果|合同公告|履约验收|验收报告|项目验收|政策法规|品目分类|目录|名录|登记实施|通知/.test(evidence);
  const tenderSignal = /招标|采购|磋商|谈判|询价|征集|遴选|比选/.test(evidence);
  return tenderSignal && !excluded;
}

const businessPositiveTerms = [
  "劳务派遣",
  "劳务外包",
  "劳务服务",
  "人力资源",
  "人力资源服务",
  "招聘外包",
  "招聘服务",
  "安保",
  "保安",
  "保安服务",
  "后勤",
  "后勤服务",
  "驻场",
  "秩序维护",
  "校园",
  "宿管",
  "导诊",
  "保洁",
  "客服",
  "服务外包",
  "外包服务",
  "辅助人员",
  "辅助岗位",
  "综合服务",
  "运行保障",
  "绿化养护",
];

const businessNegativeTerms = [
  "工程",
  "施工",
  "土建",
  "市政",
  "道路",
  "桥梁",
  "隧道",
  "管网",
  "电力迁改",
  "迁改",
  "改造",
  "装修",
  "装饰",
  "设计",
  "勘察",
  "测绘",
  "监理",
  "造价",
  "延期公告",
  "更正公告",
  "澄清公告",
  "补遗公告",
  "废标公告",
  "流标公告",
  "终止公告",
];

function normalizeSearchText(item) {
  return `${item.title || ""} ${item.category || ""} ${item.region || ""} ${item.noticeType || ""}`.toLowerCase();
}

function businessRelevance(item, filters = {}) {
  const haystack = normalizeSearchText(item);
  const titleFocus = `${String(item.title || "").toLowerCase()} ${String(item.noticeType || "").toLowerCase()}`;
  const expanded = `${haystack} ${String(item.analysisText || "").toLowerCase()}`;
  const query = String(filters.query || "").trim().toLowerCase();
  const region = String(filters.region || "").trim().toLowerCase();
  const type = String(filters.type || "").trim().toLowerCase();
  const positiveTerms = [...new Set([...businessPositiveTerms, ...array(config.targetServiceTypes), ...array(config.includeKeywords)])]
    .map((term) => String(term || "").trim().toLowerCase())
    .filter(Boolean)
    .filter((term) => !["医院", "学校", "教育", "国企", "国有企业"].includes(term));
  const negativeTerms = [...new Set([...businessNegativeTerms, ...array(config.excludeKeywords)])]
    .map((term) => String(term || "").trim().toLowerCase())
    .filter(Boolean)
    .filter((term) => term !== "招标公告");
  if (negativeTerms.some((term) => titleFocus.includes(term))) return false;
  if (query && !expanded.includes(query)) return false;
  if (type && !expanded.includes(type)) return false;
  if (region && !["全国", "不限", "all"].includes(region) && !expanded.includes(region)) return false;
  const positiveMatch = positiveTerms.some((term) => titleFocus.includes(term)) || (query ? titleFocus.includes(query) : false);
  if (!query && !type && !region) return positiveMatch;
  return positiveMatch;
}

function isActiveTender(item) {
  const deadline = parseBidDeadline(item.bidDeadline);
  if (!isTenderNoticeCandidate(item)) return false;
  // Keep relevant public listings when the detail page cannot expose a
  // deadline because of a captcha, attachment gate, or site-specific format.
  if (!deadline) return item.deadlineStatus === "待人工复核";
  return deadline.at.getTime() >= Date.now();
}

function inferNoticeType(text) {
  if (/中标|成交|结果公告/.test(text)) return "结果公告";
  if (/更正|澄清|补遗|变更/.test(text)) return "更正公告";
  if (/竞争性磋商/.test(text)) return "竞争性磋商公告";
  if (/竞争性谈判/.test(text)) return "竞争性谈判公告";
  if (/询价/.test(text)) return "询价公告";
  if (/招标|采购|征集|遴选/.test(text)) return "采购公告";
  return "公开公告";
}

function inferCategory(text, fallback = "待分类") {
  if (/劳务派遣|劳务外包|劳务服务/.test(text)) return "劳务派遣";
  if (/人力资源|招聘/.test(text)) return "人力资源服务";
  if (/保安|安保/.test(text)) return "保安服务";
  if (/保洁|绿化/.test(text)) return "保洁及绿化服务";
  if (/物业/.test(text)) return "物业服务";
  return fallback;
}

function isAnnouncementTitle(title) {
  return title.length >= 8
    && !/^(首页|上一页|下一页|尾页|更多|登录|注册|无障碍|繁體|English|网站首页|工作动态|政策法规)$/.test(title)
    && !/javascript:|^#/.test(title);
}

function sourceRequiresLogin(source) {
  return source?.accessMode === "login";
}

function attachmentFileName(url, disposition, index) {
  const utf8Name = String(disposition || "").match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const plainName = String(disposition || "").match(/filename="?([^";]+)"?/i)?.[1];
  let name = "";
  try {
    name = decodeURIComponent(utf8Name || plainName || path.basename(new URL(url).pathname));
  } catch {
    name = path.basename(String(url || ""));
  }
  const extension = (name.match(/\.(pdf|docx?|xlsx?|zip|rar|txt)$/i)?.[0] || "").toLowerCase();
  const safeName = String(name || `公开附件-${index + 1}${extension}`)
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\s+/g, " ")
    .slice(0, 120);
  return safeName || `公开附件-${index + 1}${extension}`;
}

function extractPublicAttachmentUrls(html, pageUrl, source) {
  const page = safeRemoteUrl(pageUrl);
  if (!page || !source || sourceRequiresLogin(source)) return [];
  const results = [];
  const seen = new Set();
  const anchorPattern = /<a\b([^>]*)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))[^>]*>([\s\S]*?)<\/a>/gi;
  let match;
  while ((match = anchorPattern.exec(html))) {
    const href = decodeHtml(match[2] || match[3] || match[4] || "").trim();
    const label = plainText(match[5]);
    if (!href || href.startsWith("javascript:") || href.startsWith("#")) continue;
    let url;
    try {
      url = new URL(href, page).toString();
    } catch {
      continue;
    }
    const parsed = safeRemoteUrl(url);
    if (!parsed || parsed.hostname !== page.hostname || seen.has(url)) continue;
    const evidence = `${href} ${label}`.toLowerCase();
    if (!/\.(pdf|docx?|xlsx?|zip|rar|txt)(?:[?#]|$)/i.test(evidence) && !/(下载|附件|招标文件|采购文件|响应文件)/.test(label)) continue;
    seen.add(url);
    results.push({ url, label: label.slice(0, 160) });
  }
  return results.slice(0, 5);
}

function extractRealtimeItems(html, source, requestUrl, search) {
  const results = [];
  const seen = new Set();
  const anchorPattern = /<a\b([^>]*)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))[^>]*>([\s\S]*?)<\/a>/gi;
  let match;
  while ((match = anchorPattern.exec(html))) {
    const href = decodeHtml(match[2] || match[3] || match[4] || "").trim();
    const title = plainText(match[5]);
    if (!href || !isAnnouncementTitle(title) || href.startsWith("javascript:") || href.startsWith("#")) continue;
    let detailUrl;
    try {
      detailUrl = new URL(href, requestUrl).toString();
    } catch {
      continue;
    }
    if (!safeRemoteUrl(detailUrl) || seen.has(detailUrl)) continue;
    const context = plainText(html.slice(match.index, match.index + 800));
    const evidence = `${title} ${context}`;
    const noticeType = inferNoticeType(evidence);
    if (!/公告|招标|采购|磋商|谈判|询价|征集|遴选/.test(evidence)) continue;
    const item = {
      id: `realtime-${hash(`${source.id}:${detailUrl}`).slice(0, 20)}`,
      title: title.slice(0, 240),
      category: inferCategory(evidence, "待分类"),
      region: source.defaultRegion || search.region || "待识别",
      noticeType,
      publishedDate: normalizeDate(context, ""),
      source: source.name,
      sourceUrl: detailUrl,
      sourceKey: source.id,
      dataOrigin: "realtime",
      fetchedAt: new Date().toISOString(),
      analysisText: `实时公告摘要：${context.slice(0, 900)}`,
    };
    const listingDeadline = extractBidDeadline(context);
    if (listingDeadline) item.bidDeadline = listingDeadline.value;
    results.push(item);
    seen.add(detailUrl);
  }
  return results;
}

function extractCcgpCentralItems(html, source, requestUrl, search) {
  return extractRealtimeItems(html, source, requestUrl, search)
    .filter((item) => {
      const pathName = new URL(item.sourceUrl).pathname.toLowerCase();
      // The central site publishes current notices under several category paths.
      return /\/(?:gkzb|jzxcs|xjgg|jzxtp|zbgg|cggg\/zygg)\//i.test(pathName)
        && !/(?:\/cjgg\/|\/jggg\/|\/zfcjgg\/)/i.test(pathName);
    })
    .map((item) => {
      const match = item.sourceUrl.match(/\/t(\d{4})(\d{2})(\d{2})_/i);
      const pathName = new URL(item.sourceUrl).pathname.toLowerCase();
      const noticeType = pathName.includes("/gkzb/")
        ? "公开招标公告"
        : pathName.includes("/jzxcs/")
          ? "竞争性磋商公告"
          : pathName.includes("/xjgg/")
            ? "询价公告"
            : pathName.includes("/jzxtp/")
              ? "竞争性谈判公告"
              : inferNoticeType(item.title);
      const publishedDate = match ? `${match[1]}-${match[2]}-${match[3]}` : item.publishedDate;
      return {
        ...item,
        category: inferCategory(item.title, "待分类"),
        noticeType,
        publishedDate,
        // The generic list context can include adjacent result cards such as
        // award or correction notices. Keep this adapter's candidate evidence
        // limited to the current notice so it cannot be incorrectly excluded.
        analysisText: `实时公告标题：${item.title}\n公告类型：${noticeType}\n发布日期：${publishedDate || "待识别"}`,
      };
    });
}

function absoluteSourceDetailUrl(href, requestUrl, allowedHost) {
  let detailUrl;
  try {
    detailUrl = new URL(href, requestUrl).toString();
  } catch {
    return null;
  }
  const parsed = safeRemoteUrl(detailUrl);
  if (!parsed || (allowedHost && parsed.hostname.toLowerCase() !== allowedHost.toLowerCase())) return null;
  return parsed.toString();
}

function cleanStructuredNoticeTitle(value) {
  return plainText(value)
    .replace(/\s+(?:20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?)\s+(?:采购公告|招标公告|成交公告|结果公告)$/i, "")
    .trim();
}

function extractCcgpHenanItems(html, source, requestUrl, search) {
  const results = [];
  const seen = new Set();
  const anchorPattern = /<a\b([^>]*)\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))[^>]*>([\s\S]*?)<\/a>/gi;
  let match;
  while ((match = anchorPattern.exec(html))) {
    const href = decodeHtml(match[2] || match[3] || match[4] || "").trim();
    if (!href || !/\/henan\/content\?/i.test(href)) continue;
    const detailUrl = absoluteSourceDetailUrl(href, requestUrl, "zfcg.henan.gov.cn");
    if (!detailUrl || seen.has(detailUrl)) continue;
    const parsed = new URL(detailUrl);
    const channelCode = parsed.searchParams.get("channelCode") || "";
    if (channelCode && channelCode !== "H600101") continue;
    const title = plainText(match[5]);
    if (!isAnnouncementTitle(title)) continue;
    const context = plainText(html.slice(Math.max(0, match.index - 500), match.index + 900));
    const evidence = `${title} ${context}`;
    if (!/公告|招标|采购|磋商|谈判|询价|征集|遴选/.test(evidence)) continue;
    const noticeType = /公开招标/.test(evidence) ? "公开招标公告" : inferNoticeType(evidence);
    const publishedDate = normalizeDate(context, "");
    results.push({
      id: `realtime-${hash(`${source.id}:${detailUrl}`).slice(0, 20)}`,
      title: title.slice(0, 240),
      category: inferCategory(evidence, "待分类"),
      region: source.defaultRegion || search.region || "河南省",
      noticeType,
      publishedDate,
      source: source.name,
      sourceUrl: detailUrl,
      sourceKey: source.id,
      dataOrigin: "realtime",
      fetchedAt: new Date().toISOString(),
      analysisText: `河南省政府采购网公告摘要：${context.slice(0, 900)}`,
    });
    seen.add(detailUrl);
  }
  return results;
}

async function extractGgzyHenanItems(source, search, timeoutSeconds) {
  const endpoint = "http://hnsggzyjy.henan.gov.cn/EpointWebBuilder/rest/frontAppCustomAction/getPageInfoListNewYzm";
  const body = new URLSearchParams({
    siteGuid: "7eb5f7f1-9041-43ad-8e13-8fcb82ea831a",
    categoryNum: "002002001",
    pageIndex: "0",
    // Read a wider public window so a default one-click run is not dependent
    // on the first eight notices happening to match the company's services.
    pageSize: "100",
    jytype: "",
    xiaqucode: "4100",
  });
  const response = await fetchRemoteJson(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8" },
    body: body.toString(),
  }, timeoutSeconds);
  const rows = array(response.data?.custom?.infodata);
  const query = String(search.query || "").trim().toLowerCase();
  const region = String(search.region || "").trim().toLowerCase();
  return rows.map((row) => {
    const detailUrl = absoluteSourceDetailUrl(row.infourl, "http://hnsggzyjy.henan.gov.cn", "hnsggzyjy.henan.gov.cn");
    if (!detailUrl) return null;
    const title = cleanStructuredNoticeTitle(row.title || row.customtitle);
    const context = plainText(row.customtitle || row.title);
    const rowRegion = plainText(row.xiaquname) || "河南省";
    const haystack = `${title} ${context} ${rowRegion}`.toLowerCase();
    if (!title || (query && !haystack.includes(query)) || (region && !["全国", "河南省", "不限", "all"].includes(region) && !haystack.includes(region))) return null;
    const noticeType = /公开招标/.test(context) ? "公开招标公告" : /竞争性磋商/.test(context) ? "竞争性磋商公告" : inferNoticeType(`${title} ${context}`);
    return {
      id: `realtime-${hash(`${source.id}:${detailUrl}`).slice(0, 20)}`,
      title: title.slice(0, 240),
      category: inferCategory(`${title} ${context}`, "待分类"),
      region: rowRegion,
      noticeType,
      publishedDate: normalizeDate(row.infodate || row.startdate, ""),
      source: source.name,
      sourceUrl: detailUrl,
      sourceKey: source.id,
      dataOrigin: "realtime",
      fetchedAt: new Date().toISOString(),
      analysisText: `河南省公共资源交易平台公告摘要：${context.slice(0, 900)}\n地区：${rowRegion}`,
    };
  }).filter(Boolean);
}

function matchesSearchTerms(item, filters = {}) {
  return businessRelevance(item, filters);
}

function normalizedDedupeText(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[\s\u3000\-—_.,，。:：;；()（）\[\]【】"'`]/g, "")
    .replace(/公告|招标|采购|项目/g, "");
}

function tenderDedupeKey(item = {}) {
  if (item.dedupeKey) return String(item.dedupeKey);
  const projectCode = normalizedDedupeText(item.projectCode || item.projectNumber || item.tenderCode);
  if (projectCode) return `code:${projectCode}`;
  const title = normalizedDedupeText(item.title);
  const date = String(item.publishedDate || "").match(/\d{4}[-/.]\d{1,2}[-/.]\d{1,2}/)?.[0].replace(/[/.]/g, "-") || "";
  const region = normalizedDedupeText(item.region);
  return `notice:${hash(`${title}|${date}|${region}`).slice(0, 24)}`;
}

function uniqueSourceReferences(...items) {
  const references = [];
  const seen = new Set();
  for (const item of items) {
    for (const reference of [...array(item?.sourceReferences), item].filter(Boolean)) {
      const sourceUrl = String(reference.sourceUrl || "");
      const key = sourceUrl || `${reference.sourceKey || ""}:${reference.source || ""}`;
      if (!key || seen.has(key)) continue;
      seen.add(key);
      references.push({
        source: reference.source || "",
        sourceKey: reference.sourceKey || "",
        sourceUrl,
        seenAt: reference.fetchedAt || new Date().toISOString(),
      });
    }
  }
  return references.slice(0, 10);
}

function uniqueTenderItems(items) {
  const seen = new Set();
  return items.filter((item) => {
    const key = tenderDedupeKey(item);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function matchesDiscoveryFilters(item, filters = {}) {
  return matchesSearchTerms(item, filters) && passesRetrievalStartDate(item);
}

function matchesCatalogFilters(item, filters = {}) {
  if (item.dataOrigin === "local_upload") {
    const haystack = `${normalizeSearchText(item)} ${String(item.analysisText || "").toLowerCase()}`;
    const query = String(filters.query || "").trim().toLowerCase();
    const region = String(filters.region || "").trim().toLowerCase();
    const type = String(filters.type || "").trim().toLowerCase();
    const titleFocus = `${String(item.title || "").toLowerCase()} ${String(item.noticeType || "").toLowerCase()}`;
    const negativeTerms = [...new Set([...businessNegativeTerms, ...array(config.excludeKeywords)])]
      .map((term) => String(term || "").trim().toLowerCase())
      .filter((term) => term && term !== "招标公告");
    if (negativeTerms.some((term) => titleFocus.includes(term))) return false;
    if (query && !haystack.includes(query)) return false;
    if (type && !haystack.includes(type)) return false;
    if (region && !["全国", "不限", "all"].includes(region) && !haystack.includes(region)) return false;
    return passesRetrievalStartDate(item);
  }
  return matchesDiscoveryFilters(item, filters) && isActiveTender(item);
}

function sourceHostMatches(source, detailUrl) {
  if (!source || !detailUrl) return false;
  const configured = new URL(sourceUrl(source)).hostname.toLowerCase();
  const actual = detailUrl.hostname.toLowerCase();
  if (configured === actual) return true;
  if (source.adapter === "ccgp-henan") return ["www.ccgp-henan.gov.cn", "zfcg.henan.gov.cn"].includes(actual);
  if (source.adapter === "ggzy-henan") return actual === "hnsggzyjy.henan.gov.cn";
  return false;
}

async function fetchTenderDeadline(item, source, timeoutSeconds) {
  if (!isTenderNoticeCandidate(item)) return null;
  const detailUrl = safeRemoteUrl(item.sourceUrl);
  const pending = {
    ...item,
    deadlineStatus: "待人工复核",
    accessNote: "详情页未能自动核验投标截止时间，请人工打开公告确认。",
  };
  if (!detailUrl || !sourceHostMatches(source, detailUrl)) {
    return { ...pending, accessNote: "公告链接未通过数据源域名校验，请人工打开原公告确认。" };
  }
  try {
    const { content, url } = await fetchRemoteText(detailUrl.toString(), timeoutSeconds);
    const text = plainText(content).slice(0, 180000);
    const deadline = extractBidDeadline(text);
    const fileAccessWindow = extractDocumentAccessWindow(text);
    const fileAccessFields = fileAccessWindow ? {
      fileAccessStart: fileAccessWindow.start,
      fileAccessEnd: fileAccessWindow.end,
      fileAccessEvidenceLabel: fileAccessWindow.evidenceLabel,
    } : {};
    if (!deadline) {
      return {
        ...pending,
        publishedDate: normalizeDate(text, item.publishedDate || ""),
        deadlineEvidence: text.slice(0, 900),
        detailUrl: url,
        ...fileAccessFields,
        accessNote: "详情页已抓取，但未识别到投标截止时间，请人工复核公告或附件。",
      };
    }
    return {
      ...item,
      publishedDate: normalizeDate(text, item.publishedDate || ""),
      bidDeadline: deadline.value,
      deadlineStatus: "已核验",
      deadlineVerifiedAt: new Date().toISOString(),
      deadlineEvidence: text.slice(0, 900),
      detailUrl: url,
      ...fileAccessFields,
      accessNote: "已从公告详情页核验投标截止时间。",
    };
  } catch (error) {
    return {
      ...pending,
      accessNote: `详情页暂时无法自动读取（${String(error?.message || "站点限制").slice(0, 180)}），请人工打开原公告确认。`,
    };
  }
}

async function mapWithConcurrency(values, limit, mapper) {
  const results = new Array(values.length);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(limit, values.length) }, async () => {
    while (cursor < values.length) {
      const index = cursor;
      cursor += 1;
      try {
        results[index] = await mapper(values[index]);
      } catch {
        results[index] = null;
      }
    }
  });
  await Promise.all(workers);
  return results;
}

function upsertRealtimeCatalog(items) {
  let added = 0;
  let merged = 0;
  const inserted = [];
  const canonical = [];
  const byDedupeKey = new Map(catalog.map((entry) => [tenderDedupeKey(entry), entry]));
  for (const item of items) {
    const dedupeKey = tenderDedupeKey(item);
    const existing = byDedupeKey.get(dedupeKey) || catalog.find((entry) => entry.id === item.id);
    if (existing) {
      const wasDifferentSource = existing.sourceKey !== item.sourceKey || existing.sourceUrl !== item.sourceUrl;
      Object.assign(existing, {
        ...item,
        id: existing.id,
        dedupeKey,
        sourceReferences: uniqueSourceReferences(existing, item),
        firstSeenAt: existing.firstSeenAt || item.fetchedAt || new Date().toISOString(),
        lastSeenAt: new Date().toISOString(),
      });
      if (wasDifferentSource) merged += 1;
      canonical.push(existing);
    } else {
      const fresh = {
        ...item,
        dedupeKey,
        sourceReferences: uniqueSourceReferences(item),
        firstSeenAt: item.fetchedAt || new Date().toISOString(),
        lastSeenAt: new Date().toISOString(),
      };
      catalog.unshift(fresh);
      byDedupeKey.set(dedupeKey, fresh);
      inserted.push(fresh);
      canonical.push(fresh);
      added += 1;
    }
  }
  if (items.length) persistCatalog();
  return { added, merged, inserted, canonical };
}

async function searchRealtime(filters = {}) {
  const search = normalizeSearchConfig(config.search);
  if (!search.enabled) throw new Error("实时搜索当前未启用，请在搜索配置中开启。");
  const enabledSources = search.sources.filter((source) => source.enabled);
  if (!enabledSources.length) throw new Error("请先在搜索配置中启用至少一个数据源。");
  const reports = await Promise.all(enabledSources.map(async (source) => {
    const startedAt = Date.now();
    try {
      let requestUrl = sourceUrl(source, filters);
      let items;
      if (source.adapter === "ggzy-henan") {
        items = await extractGgzyHenanItems(source, filters, search.timeoutSeconds);
        requestUrl = "http://hnsggzyjy.henan.gov.cn/EpointWebBuilder/rest/frontAppCustomAction/getPageInfoListNewYzm";
      } else {
        const response = await fetchRemoteText(requestUrl, search.timeoutSeconds);
        const extractItems = source.adapter === "ccgp-central"
          ? extractCcgpCentralItems
          : source.adapter === "ccgp-henan"
            ? extractCcgpHenanItems
            : extractRealtimeItems;
        items = extractItems(response.content, source, response.url, filters);
        requestUrl = response.url;
      }
      items = items
        .filter((item) => matchesSearchTerms(item, filters) && isTenderNoticeCandidate(item))
        .slice(0, search.maxResultsPerSource);
      return { source, sourceId: source.id, name: source.name, status: "已更新", requestUrl, count: 0, candidateCount: items.length, elapsedMs: Date.now() - startedAt, items };
    } catch (error) {
      const cachedItems = catalog.filter((item) => item.dataOrigin === "realtime" && item.sourceKey === source.id && matchesCatalogFilters(item, filters));
      return {
        source,
        sourceId: source.id,
        name: source.name,
        status: "失败",
        count: 0,
        cachedCount: cachedItems.length,
        usingCache: cachedItems.length > 0,
        elapsedMs: Date.now() - startedAt,
        error: error.message || "抓取失败",
        items: [],
        cachedItems,
      };
    }
  }));
  const verificationCandidates = reports.flatMap((report) => report.items.map((item) => ({ item, source: report.source })));
  const verificationLimit = 160;
  const verified = await mapWithConcurrency(
    verificationCandidates.slice(0, verificationLimit),
    8,
    ({ item, source }) => fetchTenderDeadline(item, source, search.timeoutSeconds),
  );
  const collected = verified.filter((item) => item && matchesCatalogFilters(item, filters));
  const verifiedBySource = new Map();
  for (const item of collected) verifiedBySource.set(item.sourceKey, (verifiedBySource.get(item.sourceKey) || 0) + 1);
  for (const report of reports) {
    report.count = verifiedBySource.get(report.sourceId) || 0;
    report.pendingReviewCount = collected.filter((item) => item.sourceKey === report.sourceId && item.deadlineStatus === "待人工复核").length;
  }
  const upsert = upsertRealtimeCatalog(collected);
  const notifications = (await Promise.all(upsert.inserted.map((item) => notifyNewTender(item)))).filter(Boolean);
  if (notifications.length) persistCatalog();
  const cachedItems = reports.flatMap((report) => array(report.cachedItems).map((item) => ({ ...item, dataStale: true, cacheReason: report.error })));
  const resultItems = uniqueTenderItems([...upsert.canonical, ...cachedItems]);
  const now = new Date().toISOString();
  for (const report of reports) {
    const health = schedulerState.sourceHealth[report.sourceId] || {};
    schedulerState.sourceHealth[report.sourceId] = report.status === "已更新"
      ? { ...health, lastAttemptAt: now, lastSuccessAt: now, lastFailureAt: health.lastFailureAt || null, consecutiveFailures: 0, lastError: "", cachedCount: report.count }
      : { ...health, lastAttemptAt: now, lastFailureAt: now, consecutiveFailures: Number(health.consecutiveFailures || 0) + 1, lastError: report.error, cachedCount: report.cachedCount || 0 };
  }
  persistSchedulerState();
  const result = {
    items: resultItems,
    sources: reports.map(({ items, source, ...report }) => report),
    fetchedAt: new Date().toISOString(),
    collectedCount: verificationCandidates.length,
    verifiedCount: resultItems.length,
    pendingReviewCount: resultItems.filter((item) => item.deadlineStatus === "待人工复核").length,
    deadlineVerificationSkipped: Math.max(0, verificationCandidates.length - verificationLimit),
    addedCount: upsert.added,
    dedupedCount: Math.max(0, collected.length - upsert.added) + upsert.merged,
    cachedCount: cachedItems.length,
    notifications,
  };
  const searchId = uid("search");
  const fileName = `${searchId}.json`;
  fs.writeFileSync(safeFile(searchRunRoot, fileName), JSON.stringify({
    searchId,
    filters,
    ...result,
  }, null, 2), "utf8");
  registerLibraryDocument("searches", {
    id: searchId,
    name: `实时搜索 ${new Date().toLocaleString("zh-CN", { hour12: false })}`,
    type: "实时搜索回执",
    savedAt: result.fetchedAt,
    storagePath: `runtime/searches/${fileName}`,
    source: `${reports.length} 个已配置网站`,
    resultCount: result.items.length,
    preview: JSON.stringify({ filters, sources: result.sources, collectedCount: result.collectedCount, addedCount: result.addedCount, dedupedCount: result.dedupedCount, cachedCount: result.cachedCount, notificationCount: notifications.length }, null, 2),
  });
  return { ...result, searchId };
}

function firstMatch(text, regex, fallback = "未识别") {
  const match = text.match(regex);
  return match ? match[1].trim() : fallback;
}

function detectType(text) {
  if (text.includes("劳务派遣")) return "劳务派遣";
  if (text.includes("保安")) return "保安服务";
  if (text.includes("保洁")) return "保洁服务";
  if (text.includes("家政")) return "家政服务";
  return "人力资源服务";
}

function linesContaining(text, patterns) {
  return text.split(/\r?\n/).map((line) => line.trim())
    .filter((line) => line && patterns.some((pattern) => line.includes(pattern)));
}

function extractPerformanceCount(text) {
  const match = text.match(/(?:不少于|至少|不低于)\s*(\d+)\s*个[^。\n]{0,25}(?:业绩|项目)/);
  return match ? Number(match[1]) : 0;
}

function buildRequirements(text) {
  const qualificationLines = linesContaining(text, ["营业执照", "许可证", "资质", "资格"]);
  const performanceLines = linesContaining(text, ["类似", "业绩"]);
  const personnelLines = linesContaining(text, ["项目经理", "人员", "团队"]);
  const technicalLines = linesContaining(text, ["服务方案", "应急", "质量", "培训", "考勤", "社保", "报告"]);
  const rejectionLines = linesContaining(text, ["废标", "无效投标", "否决", "逾期"]);
  const qualifications = ["营业执照", "人力资源服务许可证", "劳务派遣经营许可证", "保安服务许可证"]
    .filter((item) => text.includes(item));
  const items = qualifications.map((name, index) => ({
    requirementId: `REQ-Q-${index + 1}`, type: "资格审查", text: `提供有效${name}`, criticality: "否决性", mandatory: true,
  }));
  if (performanceLines.length) items.push({
    requirementId: "REQ-P-1", type: "业绩要求", text: performanceLines.join("；"), criticality: "关键", mandatory: true, requiredCount: extractPerformanceCount(text),
  });
  if (personnelLines.length) items.push({
    requirementId: "REQ-H-1", type: "人员要求", text: personnelLines.join("；"), criticality: "关键", mandatory: true,
    role: text.includes("项目经理") ? "项目经理" : "项目人员",
  });
  technicalLines.slice(0, 8).forEach((line, index) => items.push({
    requirementId: `REQ-T-${index + 1}`, type: "技术要求", text: line, criticality: "一般", mandatory: false,
  }));
  rejectionLines.forEach((line, index) => items.push({
    requirementId: `REQ-R-${index + 1}`, type: "否决条款", text: line, criticality: "否决性", mandatory: true,
  }));
  return { qualifications, performances: performanceLines.length ? [{ text: performanceLines.join("；"), required_count: extractPerformanceCount(text) }] : [], personnel: personnelLines.length ? [{ text: personnelLines.join("；"), role: text.includes("项目经理") ? "项目经理" : "项目人员" }] : [], technical: [...new Set(technicalLines)], rejection: [...new Set(rejectionLines)], items };
}

function parseTender(text, source = "本地文件") {
  const requirements = buildRequirements(text);
  const projectName = firstMatch(text, /项目名称[:：]\s*([^\r\n]+)/);
  const deadline = extractBidDeadline(text)?.value || "未识别";
  const evidence = {
    evidenceId: uid("evidence"), documentRef: source, pageNo: 1, section: "文本抽取",
    quote: text.slice(0, 220), fragmentHash: hash(text.slice(0, 220)), verificationStatus: "待人工核验",
  };
  return {
    tenderProjectId: uid("project"),
    projectName,
    projectCode: firstMatch(text, /项目编号[:：]\s*([^\r\n]+)/),
    tendererName: firstMatch(text, /招标人[:：]\s*([^\r\n]+)/),
    agencyName: firstMatch(text, /代理机构[:：]\s*([^\r\n]+)/),
    procurementMethod: firstMatch(text, /采购方式[:：]\s*([^\r\n]+)/),
    serviceType: firstMatch(text, /(?:服务类型|项目类型)[:：]\s*([^\r\n]+)/, detectType(text)),
    serviceLocation: firstMatch(text, /服务地点[:：]\s*([^\r\n]+)/),
    budgetAmount: firstMatch(text, /(?:预算金额|最高限价)[:：]\s*([^\r\n]+)/),
    servicePeriod: firstMatch(text, /(?:服务期限|服务期)[:：]\s*([^\r\n]+)/),
    bidDeadline: deadline,
    projectStatus: "待分析",
    source: { sourceName: source, sourceUrl: "", capturedAt: new Date().toISOString(), ingestionChannel: source === "本地文件" ? "本地上传" : "自动下载" },
    documents: [{ documentId: uid("document"), fileName: `${projectName || "招标项目"}.txt`, documentType: "招标文件", versionNo: "v1", contentHash: hash(text), storagePath: "", parseStatus: "解析完成", effective: true }],
    requirements: requirements.items,
    evaluationItems: [],
    scheduleItems: deadline === "未识别" ? [] : [{ scheduleType: "投标截止", at: deadline, reminderStatus: "待提醒" }],
    evidences: [evidence],
    rawText: text,
    extractedRequirements: requirements,
    latestChangedAt: new Date().toISOString(),
  };
}

function dateDays(value) {
  const parsed = new Date(String(value).replace(/年/g, "-").replace(/月/g, "-").replace(/日/g, "").replace(/\//g, "-"));
  return Number.isNaN(parsed.getTime()) ? null : Math.ceil((parsed.getTime() - startOfToday().getTime()) / 86400000);
}

function companyMaterialEvidence(requirement) {
  const keyword = String(requirement || "").trim();
  const document = array(libraries.company?.documents).find((item) => {
    if (item.id === "company-profile") return false;
    const text = `${item.name || ""}\n${item.preview || storedDocumentPreview(item.storagePath)}`;
    return text.includes(keyword);
  });
  return document ? `${document.name}（公司资料库）` : "";
}

function compactPromptText(value, maxLength = 12000) {
  return plainText(value).replace(/\s+/g, " ").trim().slice(0, maxLength);
}

function extractedPdfText(storagePath) {
  if (!storagePath || !String(storagePath).startsWith("runtime/")) return "";
  const target = path.resolve(ROOT, storagePath);
  if (!target.startsWith(runtimeRoot + path.sep) || !fs.existsSync(target)) return "";
  try {
    return execFileSync("pdftotext", ["-layout", target, "-"], {
      encoding: "utf8",
      env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
      windowsHide: true,
      timeout: 20000,
      maxBuffer: 4 * 1024 * 1024,
    });
  } catch {
    const bundledPython = path.join(
      process.env.USERPROFILE || "",
      ".cache",
      "codex-runtimes",
      "codex-primary-runtime",
      "dependencies",
      "python",
      "python.exe",
    );
    if (!fs.existsSync(bundledPython)) return "";
    try {
      const extractor = [
        "from pypdf import PdfReader",
        "import sys",
        "reader = PdfReader(sys.argv[1])",
        "sys.stdout.write('\\n'.join((page.extract_text() or '') for page in reader.pages))",
      ].join("; ");
      return execFileSync(bundledPython, ["-c", extractor, target], {
        encoding: "utf8",
        env: { ...process.env, PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1" },
        windowsHide: true,
        timeout: 30000,
        maxBuffer: 8 * 1024 * 1024,
      });
    } catch {
      return "";
    }
  }
}

function documentTextForPrompt(document, maxLength = 8000) {
  const extracted = documentPreview(document);
  return compactPromptText(extracted, maxLength);
}

function isUnreadablePdfText(value) {
  const text = String(value || "").trim();
  if (!text) return true;
  const invalid = (text.match(/\uFFFD/g) || []).length;
  return invalid > 10 && invalid / Math.max(text.length, 1) > 0.02;
}

function defaultResponseTemplateOutline() {
  return [
    "第一部分 投标函与授权",
    "第二部分 资格响应",
    "第三部分 项目理解",
    "第四部分 运营服务方案",
    "第五部分 项目组织与人员",
    "第六部分 质量、安全与应急",
    "第七部分 商务报价与合同响应",
    "第八部分 业绩与附件",
  ];
}

function templateContexts(limit = 1, maxLength = 6000) {
  return array(libraries.templates?.documents)
    .slice()
    .sort((a, b) => {
      const score = (document) => {
        const name = String(document?.name || "");
        const file = String(document?.storagePath || document?.name || "");
        const userUploaded = /^runtime\/libraries\/templates\/library-/i.test(file);
        if (userUploaded && /(响应模板|响应文件模板|投标响应文件)/.test(name) && /\.pdf$/i.test(file)) return 8;
        if (userUploaded && /(响应模板|响应文件模板|投标响应文件)/.test(name)) return 7;
        if (/响应文件模板/.test(name) && /\.pdf$/i.test(file)) return 6;
        if (/响应文件模板/.test(name) && /\.md$/i.test(file)) return 5;
        if (/响应文件模板/.test(name)) return 4;
        if (/模板/.test(name)) return 2;
        return 0;
      };
      return score(b) - score(a) || String(b.savedAt || "").localeCompare(String(a.savedAt || ""));
    })
    .slice(0, Math.max(1, limit))
    .map((document) => {
      const text = documentTextForPrompt(document, maxLength);
      const unreadable = /\.pdf$/i.test(document.name || "") && isUnreadablePdfText(text);
      return {
        name: document.name || "未命名模板",
        text: unreadable ? "" : text,
        sourceType: /\.pdf$/i.test(document.name || "") ? "PDF" : "文本",
        outline: unreadable ? defaultResponseTemplateOutline() : [],
        extractionNote: unreadable ? "PDF 字体编码无法可靠提取正文，已保留该模板的八部分章节结构和表格化编制规则。" : "",
      };
    })
    .filter((template) => template.text.length >= 40 || template.outline.length);
}

function latestTextTemplate() {
  return templateContexts()[0] || null;
}

function companyContextForPrompt(options = {}) {
  const materialLimit = Math.max(1, Math.min(8, Number(options.materialLimit || 4)));
  const materialLength = Math.max(600, Math.min(5000, Number(options.materialLength || 1800)));
  const profile = {
    company: company.company,
    serviceRegions: array(company.service_regions),
    qualifications: array(company.qualifications).slice(0, 12),
    capabilities: array(company.capabilities).slice(0, 12),
    performanceCount: array(company.performances).length,
    performances: array(company.performances).slice(0, 6).map((item) => (
      `${item.name || "未命名业绩"}｜${item.type || "未分类"}｜${item.client || "客户未填"}｜${item.summary || item.description || ""}`.trim()
    )),
    personnelSummary: array(company.personnel).slice(0, 8).map((item) => (
      `${item.role || "未命名岗位"}｜${item.count || 0}名｜${item.experience_years || 0}年`
    )),
  };
  const materials = array(libraries.company?.documents)
    .filter((document) => document.id !== "company-profile")
    .slice(-materialLimit)
    .map((document) => ({ name: document.name || "未命名资料", text: documentTextForPrompt(document, materialLength) }))
    .filter((document) => document.text);
  return { profile, materials };
}

function parseModelJson(content) {
  const normalized = String(content || "").trim().replace(/^```(?:json)?\s*/i, "").replace(/\s*```$/i, "");
  const start = normalized.indexOf("{");
  const end = normalized.lastIndexOf("}");
  if (start < 0 || end <= start) throw new Error("模型未返回约定的 JSON 结构。");
  return JSON.parse(normalized.slice(start, end + 1));
}

function normalizeSemanticRow(row, index) {
  const status = ["满足", "部分满足", "待补材料", "待澄清"].includes(row?.status) ? row.status : "待澄清";
  const risk = ["低", "中", "高"].includes(row?.risk) ? row.risk : (status === "满足" ? "低" : "中");
  return {
    comparisonId: uid("ai-comparison"),
    requirementRef: String(row?.requirementRef || `AI-REQ-${index + 1}`).slice(0, 120),
    kind: String(row?.kind || "语义要求").slice(0, 40),
    requirement: String(row?.requirement || "未识别要求").slice(0, 1200),
    status,
    evidence: String(row?.evidence || "未提供可核验依据").slice(0, 1200),
    rationale: String(row?.rationale || "模型未给出说明").slice(0, 1200),
    risk,
    sourceRefs: array(row?.sourceRefs).map((item) => String(item).slice(0, 160)).slice(0, 6),
  };
}

async function runAiSemanticComparison(project, reasoning) {
  if (config.ai?.enabled === false || config.ai?.semanticEnabled === false) {
    return { enabled: false, status: "未启用", summary: "AI 信息匹配未启用，当前展示规则结果。", rows: [], risks: [] };
  }
  const companyContext = companyContextForPrompt({ materialLimit: 3, materialLength: 1400 });
  const payload = {
    project: {
      name: project.projectName,
      code: project.projectCode,
      type: project.serviceType,
      location: project.serviceLocation,
      deadline: project.bidDeadline,
      tenderText: compactPromptText(project.rawText, 6500),
      extractedRequirements: project.requirements,
    },
    ruleComparison: reasoning.rows.slice(0, 12).map((row) => ({
      requirementRef: row.requirementRef,
      kind: row.kind,
      requirement: compactPromptText(row.requirement, 260),
      status: row.status,
      evidence: compactPromptText(row.evidence, 260),
      risk: row.risk,
    })),
    companyLibrary: companyContext,
  };
  const messages = [
    {
      role: "system",
      content: "你是招投标合规审查助手。只能依据用户提供的招标文件、规则比对和公司资料库下结论，不得编造证书、业绩、人员或承诺。请识别规则抽取未覆盖的语义要求和潜在否决风险。只输出 JSON，不要 Markdown。JSON 结构为：{\"summary\":\"不超过300字\",\"rows\":[{\"requirementRef\":\"\",\"kind\":\"\",\"requirement\":\"\",\"status\":\"满足|部分满足|待补材料|待澄清\",\"evidence\":\"必须引用公司资料库名称或公司基础信息\",\"rationale\":\"\",\"risk\":\"低|中|高\",\"sourceRefs\":[\"招标文件\",\"资料名称\"]}],\"risks\":[{\"level\":\"低|中|高\",\"title\":\"\",\"detail\":\"\"}]}。",
    },
    { role: "user", content: JSON.stringify(payload) },
  ];
  try {
    const response = await callConfiguredModel(messages, { maxTokens: 700, timeoutMs: 90000 });
    const result = parseModelJson(response.content);
    return {
      enabled: true,
      status: "完成",
      model: response.model,
      summary: String(result.summary || "已完成 AI 信息匹配。").slice(0, 1200),
      rows: array(result.rows).slice(0, 24).map(normalizeSemanticRow),
      risks: array(result.risks).slice(0, 12).map((risk) => ({
        level: ["低", "中", "高"].includes(risk?.level) ? risk.level : "中",
        title: String(risk?.title || "待人工核验").slice(0, 120),
        detail: String(risk?.detail || "模型识别到需要人工复核的语义风险。").slice(0, 800),
      })),
      usedMaterials: companyContext.materials.map((document) => document.name),
      generatedAt: new Date().toISOString(),
    };
  } catch (error) {
    return {
      enabled: true,
      status: "降级",
      summary: aiFallbackSummary(error, "semantic"),
      errorMessage: String(error?.message || "AI 信息匹配未完成"),
      rows: [],
      risks: [],
      generatedAt: new Date().toISOString(),
    };
  }
}

function matchProject(project) {
  const rows = [];
  const qualificationMap = { "营业执照": ["营业执照"], "人力资源服务许可证": ["人力资源服务许可证"], "劳务派遣经营许可证": ["劳务派遣经营许可证"], "保安服务许可证": ["保安服务许可证"] };
  for (const requirement of project.extractedRequirements.qualifications) {
    const profileEvidence = (qualificationMap[requirement] || [requirement]).find((item) => company.qualifications.includes(item));
    const uploadedEvidence = companyMaterialEvidence(requirement);
    const evidence = profileEvidence || uploadedEvidence;
    rows.push({ comparisonId: uid("comparison"), requirementRef: requirement, kind: "资质", requirement: `提供有效${requirement}`, status: evidence ? "满足" : "待补材料", evidence: evidence || "企业材料库未找到已核验依据", risk: evidence ? "低" : "高", confidence: evidence ? 0.98 : 0.72 });
  }
  const performance = project.extractedRequirements.performances[0];
  if (performance) {
    const matching = company.performances.filter((item) => item.type === project.serviceType || project.serviceType === "人力资源服务");
    const required = performance.required_count || 0;
    rows.push({ comparisonId: uid("comparison"), requirementRef: "REQ-P-1", kind: "业绩", requirement: performance.text, status: required && matching.length < required ? "部分满足" : "满足", evidence: `企业材料库匹配到 ${matching.length} 个相关业绩，要求 ${required || "未识别"} 个`, risk: required && matching.length < required ? "高" : "中", confidence: required ? 0.93 : 0.68 });
  }
  const personnel = project.extractedRequirements.personnel[0];
  if (personnel) {
    const found = company.personnel.find((item) => item.role === personnel.role);
    const experience = personnel.text.match(/(\d+)\s*年以上/);
    const satisfied = found && (!experience || found.experience_years >= Number(experience[1]));
    rows.push({ comparisonId: uid("comparison"), requirementRef: "REQ-H-1", kind: "人员", requirement: personnel.text, status: satisfied ? "满足" : "待补材料", evidence: found ? `${found.role} ${found.count}名，经验${found.experience_years}年` : "企业材料库未找到对应人员", risk: satisfied ? "低" : "中", confidence: found ? 0.9 : 0.68 });
  }
  const regionMatch = array(config.targetRegions).some((region) => project.serviceLocation.includes(region)) || company.service_regions.some((region) => project.serviceLocation.includes(region));
  rows.push({ comparisonId: uid("comparison"), requirementRef: "PROJECT-REGION", kind: "地域", requirement: project.serviceLocation, status: regionMatch ? "满足" : "待澄清", evidence: `企业服务区域：${company.service_regions.join("、")}`, risk: regionMatch ? "低" : "中", confidence: regionMatch ? 0.95 : 0.65 });
  const weight = config.scoreWeights || {};
  const qualifications = rows.filter((row) => row.kind === "资质");
  const deadlineDays = dateDays(project.bidDeadline);
  const qualificationScore = qualifications.length
    ? qualifications.filter((row) => row.status === "满足").length * (Number(weight.qualification || 14) / qualifications.length)
    : 0;
  const rawScore = qualificationScore
    + (rows.find((row) => row.kind === "业绩")?.status === "满足" ? Number(weight.performance || 22) : 0)
    + (rows.find((row) => row.kind === "人员")?.status === "满足" ? Number(weight.personnel || 14) : 0)
    + (regionMatch ? Number(weight.region || 12) : 0)
    + (array(config.targetServiceTypes).includes(project.serviceType) ? Number(weight.serviceType || 18) : 4)
    + (deadlineDays === null || deadlineDays >= 7 ? Number(weight.time || 10) : 3)
    + Number(weight.documentReadiness || 10);
  const score = Math.max(0, Math.min(100, Math.round(rawScore)));
  const thresholds = config.decisionThresholds || { recommend: 78, review: 58 };
  const decision = score >= Number(thresholds.recommend) ? "建议参与" : score >= Number(thresholds.review) ? "人工评估" : "不建议参与";
  const risks = [];
  if (rows.some((row) => row.status === "待补材料")) risks.push({ level: "高", title: "存在待补材料", detail: "请补充或核验资格、业绩、人员证明材料。" });
  if (project.extractedRequirements.rejection.length) risks.push({ level: "高", title: "存在否决条款", detail: project.extractedRequirements.rejection.join("；") });
  if (!regionMatch) risks.push({ level: "中", title: "服务区域需确认", detail: "项目地点不在当前企业服务区域画像内。" });
  if (deadlineDays !== null && deadlineDays < 7) risks.push({ level: "中", title: "准备时间较短", detail: `距离投标截止约 ${deadlineDays} 天。` });
  if (!risks.length) risks.push({ level: "低", title: "暂未发现明显高风险", detail: "仍需人工核验原始文件和附件。" });
  return { rows, risks, score, decision, remainingDays: deadlineDays, gate: { canExport: !rows.some((row) => row.risk === "高" && row.status !== "满足"), reason: rows.some((row) => row.risk === "高" && row.status !== "满足") ? "存在未通过的高风险或否决性要求" : "结构化闸门通过，仍需人工审核签章" } };
}

function buildGraph(project, reasoning) {
  const entities = [{ id: project.tenderProjectId, class: "TenderProject", name: project.projectName }, { id: "company-1", class: "CompanyProfile", name: company.company }, ...project.requirements.map((item) => ({ id: item.requirementId, class: "Requirement", name: item.text })), ...reasoning.rows.map((item) => ({ id: item.comparisonId, class: "RequirementComparison", name: `${item.kind}：${item.status}` })), ...reasoning.risks.map((item, index) => ({ id: `risk-${index}`, class: "Risk", name: item.title }))];
  const triples = [...project.requirements.map((item) => ({ subject: project.projectName, relation: "提出要求", object: item.text })), ...reasoning.rows.map((item) => ({ subject: project.projectName, relation: "形成比对", object: `${item.kind}：${item.status}` })), ...reasoning.risks.map((item) => ({ subject: project.projectName, relation: "存在风险", object: item.title })), { subject: company.company, relation: "参与评估", object: project.projectName }, { subject: project.projectName, relation: "形成建议", object: reasoning.decision }];
  return { entities, triples };
}

function buildDraft(project, reasoning) {
  const missing = reasoning.rows.filter((row) => row.status !== "满足");
  const missingText = missing.length ? missing.map((row) => `- ${row.requirement}（${row.status}；依据：${row.evidence}）`).join("\n") : "- 暂未发现结构化缺失项，仍需人工核验原始文件和附件。";
  const template = latestTextTemplate();
  const companyMaterials = array(libraries.company?.documents).filter((document) => document.id !== "company-profile").slice(-8).map((document) => document.name);
  const templateDraft = template ? template.text
    .replace(/\{\{\s*项目名称\s*\}\}/g, project.projectName)
    .replace(/\{\{\s*服务类型\s*\}\}/g, project.serviceType)
    .replace(/\{\{\s*服务地点\s*\}\}/g, project.serviceLocation) : "";
  const coreDraft = `# ${project.projectName}

## 一、项目理解
本项目为${project.serviceLocation}的${project.serviceType}项目，服务期限为${project.servicePeriod}。我方将围绕招标文件要求建立项目服务机制，确保人员配置、过程管理、质量控制和风险响应有据可查。

## 二、项目组织与人员配置
拟由项目经理统一负责项目协调，配置招聘、现场管理及后台支持人员。具体人员名单、证书、社保证明和岗位承诺将在定稿前依据招标文件逐项补齐，并由负责人审核。

## 三、服务实施方案
围绕人员招聘与入职、考勤管理、薪酬核算、社保协同、服务报告和投诉处理建立闭环流程。每项工作对应责任人、时限、记录和复核节点。

## 四、质量保障与应急响应
建立月度服务质量检查机制，对人员缺岗、考勤异常、投诉和突发用工风险设置登记、分派、限时处理、升级和复盘流程。

## 五、材料与人工确认清单
${missingText}

> 本稿由公司内部投标专员规则校核版 v1.0.0 基于结构化项目要求和企业材料生成，仅作为可审核底稿。`;
  const materialNote = `\n\n## 资料库引用\n- 公司基础信息：${company.company}\n- 已引用公司资料：${companyMaterials.length ? companyMaterials.join("、") : "当前使用公司基础信息和结构化资质档案"}\n- 响应模板：${template ? template.name : "未选择可解析模板，使用系统标准结构"}\n`;
  return templateDraft
    ? `${templateDraft}\n\n---\n\n${coreDraft.replace(/^# .+\n\n/, "")}${materialNote}`
    : `${coreDraft}${materialNote}`;
}

async function generateAiDraft(project, reasoning, semantic, fallbackDraft) {
  if (config.ai?.enabled === false || config.ai?.draftingEnabled === false) {
    return { status: "未启用", draft: fallbackDraft, summary: "AI 响应输出未启用，当前使用结构化草稿。", templateNames: [] };
  }
  const templates = templateContexts(1, 3200);
  const companyContext = companyContextForPrompt({ materialLimit: 3, materialLength: 1200 });
  const aiRows = array(reasoning.rows).slice(0, 12).map((row) => ({
    requirementRef: row.requirementRef,
    kind: row.kind,
    requirement: compactPromptText(row.requirement, 240),
    status: row.status,
    evidence: compactPromptText(row.evidence, 240),
    rationale: compactPromptText(row.rationale, 240),
    risk: row.risk,
  }));
  const payload = {
    project: {
      name: project.projectName,
      code: project.projectCode,
      tenderer: project.tendererName,
      type: project.serviceType,
      location: project.serviceLocation,
      period: project.servicePeriod,
      deadline: project.bidDeadline,
      tenderRequirements: project.requirements,
      tenderText: compactPromptText(project.rawText, 5000),
    },
    ruleComparison: aiRows,
    semanticComparison: {
      status: semantic.status,
      summary: compactPromptText(semantic.summary, 500),
      rows: array(semantic.rows).slice(0, 8).map((row) => ({
        requirementRef: row.requirementRef,
        kind: row.kind,
        requirement: compactPromptText(row.requirement, 220),
        status: row.status,
        evidence: compactPromptText(row.evidence, 220),
        rationale: compactPromptText(row.rationale, 220),
        risk: row.risk,
      })),
      risks: array(semantic.risks).slice(0, 6).map((risk) => ({
        level: risk.level,
        title: compactPromptText(risk.title, 80),
        detail: compactPromptText(risk.detail, 220),
      })),
    },
    companyLibrary: companyContext,
    templates: templates.map((template) => ({
      name: template.name,
      sourceType: template.sourceType,
      text: template.text,
      outline: template.outline,
      extractionNote: template.extractionNote,
    })),
  };
  const messages = [
    {
      role: "system",
      content: "你是企业投标响应文件编制助手。根据提供的招标文件、响应模板和公司资料库编制一份中文 Markdown 响应文件草稿，供系统转换为正式 Word 排版。严格执行：1) 优先沿用上传模板的章节顺序、标题层级和表格类型；模板正文乱码时，必须按其 outline 的八部分顺序编制。2) 文档第一个标题用 # 项目名称响应文件（草稿）；每个一级部分用 #；子章节用 ## 或 ###。3) 所有项目基本信息、资格响应、人员配置、业绩、商务偏离、附件目录、待办清单等适合对照的信息，必须用 Markdown 表格，列名简洁。4) 段落保持正式投标文体，不写“模型”“资料库引用”“根据输入”等过程说明，不输出代码块。5) 只能依据提供材料，严禁捏造公司资质、业绩、人员姓名、金额、响应时限、承诺或招标要求；没有明确依据的内容写 [待补充：具体需要的材料或确认事项]。6) 资料中标注模拟、NOT VALID 或无效的证照、业绩，不得表述为已满足资格。7) 保留“材料与人工确认清单”章节，逐项列出未满足、部分满足、待补材料和待澄清事项。8) 结尾必须注明仅供内部审核，业务、法务和授权人确认后方可形成正式投标文件。直接输出 Markdown 正文。",
    },
    { role: "user", content: JSON.stringify(payload) },
  ];
  try {
    const response = await callConfiguredModel(messages, { maxTokens: 1100, timeoutMs: 180000 });
    const draft = response.content.trim();
    if (draft.length < 120) throw new Error("模型返回的响应文件内容过短。");
    return {
      status: "完成",
      model: response.model,
      draft,
      summary: `已基于 ${templates.length ? templates.map((template) => template.name).join("、") : "系统标准结构"} 和公司资料库生成 AI 响应文件草稿。`,
      templateNames: templates.map((template) => template.name),
      usedMaterials: companyContext.materials.map((document) => document.name),
      generatedAt: new Date().toISOString(),
    };
  } catch (error) {
    return {
      status: "降级",
      draft: fallbackDraft,
      summary: aiFallbackSummary(error, "draft"),
      errorMessage: String(error?.message || "AI 响应输出未完成"),
      templateNames: templates.map((template) => template.name),
      usedMaterials: companyContext.materials.map((document) => document.name),
      generatedAt: new Date().toISOString(),
    };
  }
}

async function regenerateAiDraft(workspace) {
  if (!workspace) throw new Error("未找到投标工作台。");
  const reasoning = workspace.reasoning || matchProject(workspace.project);
  reasoning.aiSemantic = await runAiSemanticComparison(workspace.project, reasoning);
  const fallbackDraft = buildDraft(workspace.project, reasoning);
  const aiDraft = await generateAiDraft(workspace.project, reasoning, reasoning.aiSemantic, fallbackDraft);
  workspace.reasoning = reasoning;
  workspace.draft = aiDraft.draft;
  workspace.aiDraft = aiDraft;
  workspace.draftVersion = Number(workspace.draftVersion || 0) + 1;
  workspace.workspaceStatus = "审核中";
  workspace.responseGeneratedAt = new Date().toISOString();
  workspace.agentRun = {
    ...(workspace.agentRun || {}),
    stage: "已使用当前模型重新生成响应文件",
    executionStatus: aiDraft.status === "完成" ? "完成" : "降级",
    modelOrRuleVersion: aiDraft.model || config.ai?.model || "未配置模型",
    finishedAt: workspace.responseGeneratedAt,
  };
  return workspace;
}

async function analyze(text, source = "本地文件", projectRef = {}) {
  const project = parseTender(text, source);
  project.source.projectId = projectRef.id || "";
  project.source.projectTitle = projectRef.title || project.projectName;
  const reasoning = matchProject(project);
  reasoning.aiSemantic = await runAiSemanticComparison(project, reasoning);
  const fallbackDraft = buildDraft(project, reasoning);
  const aiDraft = await generateAiDraft(project, reasoning, reasoning.aiSemantic, fallbackDraft);
  const workspace = {
    bidWorkspaceId: uid("workspace"),
    internalCode: `BID-${Date.now()}`,
    workspaceStatus: reasoning.decision === "建议参与" ? "资料准备中" : "待决策",
    bidDecision: reasoning.decision,
    owner: "投标负责人",
    project,
    reasoning,
    draft: aiDraft.draft,
    draftVersion: 1,
    aiDraft,
    createdAt: new Date().toISOString(),
    agentRun: {
      agentRunId: uid("run"),
      stage: "规则匹配 + AI 信息匹配 + AI 响应输出",
      executionStatus: reasoning.aiSemantic.status === "失败" || aiDraft.status === "失败" ? "部分成功" : "成功",
      modelOrRuleVersion: `${config.ai?.model || "未配置模型"} + governance-v${ontology.meta.version}`,
    },
  };
  workspaces.set(workspace.bidWorkspaceId, workspace);
  return { engine: "规则抽取 + 规则匹配 + AI 信息匹配", ontology: `${ontology.meta.name} v${ontology.meta.version}`, workspace, graph: buildGraph(project, reasoning), generatedAt: new Date().toISOString() };
}

function persistAutomationRuns() {
  fs.writeFileSync(automationRunPath, JSON.stringify(automationRuns.slice(-20), null, 2), "utf8");
}

function automationStatus() {
  const monitoring = config.automation?.monitoring || {};
  return {
    running: automationRunning,
    latest: automationRuns.length ? automationRuns[automationRuns.length - 1] : null,
    recent: automationRuns.slice(-5).reverse(),
    totalRuns: automationRuns.length,
    schedule: config.automation?.schedule || { enabled: false, time: "09:00", timezone: "Asia/Shanghai", action: "search" },
    monitoring: {
      enabled: monitoring.enabled !== false,
      intervalMinutes: Math.max(5, Math.min(1440, Number(monitoring.intervalMinutes || 30))),
      lastStartedAt: schedulerState.monitoringLastStartedAt || null,
      lastRunAt: schedulerState.monitoringLastRunAt || null,
      nextRunAt: schedulerState.monitoringNextRunAt || null,
      lastStatus: schedulerState.monitoringLastStatus || "",
    },
    sourceHealth: schedulerState.sourceHealth,
    scheduler: schedulerState,
  };
}

function projectFolder(projectId) {
  return String(projectId || "unassigned").replace(/[^a-zA-Z0-9_-]/g, "_").slice(0, 100) || "unassigned";
}

function projectFile(projectId, section, fileName) {
  const sectionName = section === "outputs" ? "outputs" : "source";
  const root = path.resolve(projectRoot, projectFolder(projectId), sectionName);
  fs.mkdirSync(root, { recursive: true });
  return safeFile(root, fileName);
}

function projectFileUrl(projectId, section, fileName) {
  return `/runtime/projects/${encodeURIComponent(projectFolder(projectId))}/${section}/${encodeURIComponent(fileName)}`;
}

function contentDisposition(fileName, inline = false) {
  const safeName = path.basename(String(fileName || "download"));
  const asciiName = safeName
    .replace(/[^\x20-\x7E]/g, "_")
    .replace(/["\\]/g, "_")
    .slice(0, 180) || "download";
  const encodedName = encodeURIComponent(safeName).replace(
    /[!'()*]/g,
    (character) => `%${character.charCodeAt(0).toString(16).toUpperCase()}`
  );
  return `${inline ? "inline" : "attachment"}; filename="${asciiName}"; filename*=UTF-8''${encodedName}`;
}

function artifactFileStem(value, fallback) {
  const normalized = String(value || "")
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 90);
  return normalized || fallback;
}

function recordProjectArtifacts(project, additions = {}) {
  const projectId = project.projectId || project.id || project.source?.projectId || project.tenderProjectId;
  const projectName = project.projectName || project.title || project.source?.projectTitle || "未命名项目";
  if (!projectId) return null;
  const documents = array(libraries.outputs.projects);
  const index = documents.findIndex((item) => item.projectId === projectId);
  const current = index >= 0 ? documents[index] : {
    projectId,
    projectName,
    sourceFiles: [],
    outputFiles: [],
    createdAt: new Date().toISOString(),
  };
  const next = {
    ...current,
    projectName,
    sourceFiles: uniqueFiles([...array(current.sourceFiles), ...array(additions.sourceFiles)]),
    outputFiles: uniqueFiles([...array(current.outputFiles), ...array(additions.outputFiles)]),
    workspaceId: additions.workspaceId || current.workspaceId || "",
    decision: additions.decision || current.decision || "",
    score: additions.score ?? current.score,
    status: additions.status || current.status || "已归档",
    updatedAt: new Date().toISOString(),
  };
  if (index >= 0) documents[index] = next;
  else documents.push(next);
  libraries.outputs.projects = documents;
  persistLibraries();
  return next;
}

function exportWorkspace(item, force = false) {
  if (!item.reasoning.gate.canExport && !force) {
    throw new Error(item.reasoning.gate.reason);
  }
  const projectId = item.project.source?.projectId || item.project.tenderProjectId || item.bidWorkspaceId;
  const parsedCode = String(item.project.projectCode || "").trim();
  const candidate = parsedCode !== "未识别" && parsedCode.length <= 80 && !/\s/.test(parsedCode)
    ? parsedCode
    : projectId;
  const base = `${artifactFileStem(candidate, item.bidWorkspaceId)}-response`;
  const docxName = `${base}.docx`;
  const markdownName = `${base}.md`;
  const jsonName = `${base}.json`;
  const generatedAt = new Date().toISOString();
  let docxGenerated = false;
  try {
    buildResponseDocx(projectFile(projectId, "outputs", docxName), item.draft, {
      title: item.project.projectName || "响应文件",
      subtitle: "投标响应文件（内部审核草稿）",
      company: company.company,
      serviceType: item.project.serviceType,
      tenderer: item.project.tendererName,
      projectCode: item.project.projectCode,
      sourceTemplate: array(item.aiDraft?.templateNames)[0] || "系统标准结构",
      projectName: item.project.projectName,
      decision: item.bidDecision,
      score: item.reasoning.score,
      templates: array(item.aiDraft?.templateNames).join("、") || "系统标准结构",
      materials: array(item.aiDraft?.usedMaterials).join("、") || "公司资料库",
      generatedAt,
    });
    docxGenerated = fs.existsSync(projectFile(projectId, "outputs", docxName));
  } catch (error) {
    console.warn("DOCX export fallback:", error.message);
  }
  fs.writeFileSync(projectFile(projectId, "outputs", markdownName), item.draft, "utf8");
  fs.writeFileSync(projectFile(projectId, "outputs", jsonName), JSON.stringify({
    exportPackageId: uid("export"),
    workspaceId: item.bidWorkspaceId,
    documentVersion: item.draftVersion,
    exportedAt: generatedAt,
    integrityCheckStatus: item.reasoning.gate.canExport ? "通过" : "强制导出-待人工复核",
    sourceProject: item.project,
    comparisons: item.reasoning.rows,
  }, null, 2), "utf8");
  item.workspaceStatus = "待递交";
  const files = [
    ...(docxGenerated ? [{ name: docxName, url: projectFileUrl(projectId, "outputs", docxName) }] : []),
    { name: markdownName, url: projectFileUrl(projectId, "outputs", markdownName) },
    { name: jsonName, url: projectFileUrl(projectId, "outputs", jsonName) },
  ];
  recordProjectArtifacts(item.project, {
    outputFiles: files,
    workspaceId: item.bidWorkspaceId,
    decision: item.bidDecision,
    score: item.reasoning.score,
    status: item.reasoning.gate.canExport ? "已完成" : "待人工复核",
  });
  registerLibraryDocument("outputs", {
    id: `export-${item.bidWorkspaceId}-v${item.draftVersion}`,
    name: `${item.project.projectName} · 响应文件导出包 v${item.draftVersion}`,
    type: "响应文件导出包",
    savedAt: generatedAt,
    source: item.project.tenderProjectId || item.bidWorkspaceId,
    files,
  });
  return { files };
}

function suggestionForReasoning(reasoning) {
  const blockers = reasoning.rows.filter((row) => row.status !== "满足");
  const highRisks = reasoning.risks.filter((risk) => risk.level === "高");
  if (reasoning.decision === "建议参与") {
    return {
      title: "建议参与",
      detail: highRisks.length
        ? `优先补齐 ${highRisks[0].title} 后推进报名与报价。`
      : "优先核验原始附件、报名条件和授权签章安排。",
    };
  }
  if (reasoning.decision === "人工评估") {
    return {
      title: "人工评估",
      detail: blockers.length
        ? `先确认或补齐：${blockers.slice(0, 2).map((row) => row.kind).join("、")}材料。`
        : "建议业务负责人结合预算、资源与服务边界确认是否参与。",
    };
  }
  return {
    title: "不建议参与",
    detail: blockers.length
      ? `当前存在${blockers.length}项未满足或待确认要求，建议暂不投入标书资源。`
      : "当前评分未达到参与阈值，建议保留监控并等待更合适项目。",
  };
}

function suggestionForWorkspace(workspace) {
  return suggestionForReasoning({
    ...workspace.reasoning,
    decision: workspace.bidDecision || workspace.reasoning.decision,
  });
}

function preliminaryMatchCatalogProject(item) {
  if (!item) throw new Error("未找到项目。");
  const summary = [
    item.analysisText || "",
    `项目名称：${item.title || ""}`,
    `项目类型：${item.category || ""}`,
    `项目区域：${item.region || ""}`,
    `投标截止：${item.bidDeadline || ""}`,
  ].filter(Boolean).join("\n");
  const project = parseTender(summary || item.title || "未命名项目", item.source || "实时公告");
  project.projectName = item.title || project.projectName;
  project.serviceType = item.category || project.serviceType;
  project.serviceLocation = item.region || project.serviceLocation;
  project.bidDeadline = item.bidDeadline || project.bidDeadline;
  project.source.projectId = item.id;
  project.source.projectTitle = item.title || project.projectName;
  const reasoning = matchProject(project);
  const suggestion = suggestionForReasoning(reasoning);
  item.matchScore = reasoning.score;
  item.matchDecision = reasoning.decision;
  item.matchSuggestion = suggestion.detail;
  item.matchStage = "公告预匹配";
  item.matchSummary = "评分依据公告摘要和当前公司画像；下载完整招标文件后需重新核验。";
  item.matchedAt = new Date().toISOString();
  item.responseStatus = item.downloadStatus === "已下载" ? (item.responseStatus || "待生成") : "待下载后生成";
  persistCatalog();
  return { item, project, reasoning, suggestion };
}

function workspaceForProject(projectId) {
  return [...workspaces.values()].reverse().find((workspace) => workspace.project.source?.projectId === projectId) || null;
}

function completedPiWorkspaceForProject(projectId) {
  return [...workspaces.values()].reverse().find((workspace) => (
    workspace.project.source?.projectId === projectId
    && workspace.agentRun?.executionStatus === "完成"
    && String(workspace.agentRun?.stage || "").includes("Pi 智能体")
  )) || null;
}

function attachPiAgentRun(workspace, agentRun, action) {
  workspace.agentRun = {
    agentRunId: agentRun.agentRunId,
    stage: action === "generate" ? "Pi 智能体：信息匹配与响应输出" : "Pi 智能体：信息匹配",
    executionStatus: "完成",
    modelOrRuleVersion: `${config.ai?.model || "未配置模型"} + Pi Agent + governance-v${ontology.meta.version}`,
    startedAt: agentRun.startedAt,
    finishedAt: agentRun.finishedAt,
    toolAudit: agentRun.toolAudit || [],
  };
  return workspace;
}

async function runPiProjectWorkflow(item, action, options = {}) {
  if (!item) throw new Error("未找到项目。");
  const projectId = item.id;
  const actionInstruction = action === "generate"
    ? "生成响应文件并导出"
    : "执行信息匹配";
  const agentResult = await runPiTenderAgent(
    `项目 ID 为 ${projectId}。请${actionInstruction}。必须使用项目工具完成，不要搜索其他项目，不要创建提醒。`,
    options,
  );
  const audit = array(agentResult.toolAudit);
  const analyzeStep = audit.find((entry) => entry.tool === "analyze_project" && entry.status === "completed" && entry.result?.workspaceId);
  if (!analyzeStep) {
    throw new Error("Pi 智能体未执行信息匹配工具，无法使用未核验的文字答复作为项目结论。");
  }
  const workspace = workspaces.get(analyzeStep.result.workspaceId);
  if (!workspace) throw new Error("Pi 智能体已完成匹配，但未找到对应工作台。");
  attachPiAgentRun(workspace, agentResult.agentRun, action);

  const exportStep = audit.find((entry) => entry.tool === "export_response_docx" && entry.status === "completed");
  if (action === "generate" && !exportStep) {
    const reason = audit.find((entry) => entry.tool === "export_response_docx" && entry.status === "failed")?.error;
    throw new Error(reason || "Pi 智能体未完成响应文件导出。请先处理高风险或待补材料。");
  }
  return {
    workspace,
    agentRun: agentResult.agentRun,
    files: array(exportStep?.result?.files),
    answer: agentResult.answer,
  };
}

async function matchCatalogProject(item, options = {}) {
  if (!item) throw new Error("未找到项目。");
  const existing = !options.forceRefresh ? completedPiWorkspaceForProject(item.id) : null;
  if (existing) {
    const suggestion = suggestionForWorkspace(existing);
    item.matchScore = existing.reasoning.score;
    item.matchDecision = existing.bidDecision;
    item.matchSuggestion = suggestion.detail;
    item.matchStage = "Pi 智能体信息匹配";
    item.workspaceId = existing.bidWorkspaceId;
    persistCatalog();
    return { item, workspace: existing, result: { workspace: existing }, download: null, suggestion, reused: true };
  }
  try {
    const piResult = await runPiProjectWorkflow(item, "match", options);
    const workspace = piResult.workspace;
    const suggestion = suggestionForWorkspace(workspace);
    item.matchScore = workspace.reasoning.score;
    item.matchDecision = workspace.bidDecision;
    item.matchSuggestion = suggestion.detail;
    item.matchStage = "Pi 智能体信息匹配";
    item.matchedAt = new Date().toISOString();
    item.workspaceId = workspace.bidWorkspaceId;
    item.agentRunId = piResult.agentRun.agentRunId;
    item.responseStatus = item.responseStatus || "待生成";
    item.downloadStatus = "已下载";
    item.downloadError = "";
    persistCatalog();
    recordProjectArtifacts(item, {
      workspaceId: workspace.bidWorkspaceId,
      decision: workspace.bidDecision,
      score: workspace.reasoning.score,
      status: workspace.reasoning.gate.canExport ? "已匹配待生成" : "待人工复核",
    });
    return { item, workspace, result: { workspace }, download: null, suggestion, agentRun: piResult.agentRun };
  } catch (agentError) {
    let download = null;
    if (item.downloadStatus !== "已下载" || !item.analysisText) {
      const account = accountForProject(item);
      const sessionId = account
        ? String(options.sessionIds?.[account.id] || options.sessionId || "")
        : String(options.sessionId || "");
      download = await saveDownload(item, { sessionId });
    }
    const result = await analyze(item.analysisText, item.source || "招标项目", { id: item.id, title: item.title });
    const workspace = result.workspace;
    workspace.agentRun = {
      ...workspace.agentRun,
      stage: "Pi 智能体未完成，已转模型/规则处理",
      executionStatus: "降级",
      agentError: String(agentError?.message || "Pi 智能体运行失败").slice(0, 1000),
    };
    const suggestion = suggestionForWorkspace(workspace);
    item.matchScore = workspace.reasoning.score;
    item.matchDecision = workspace.bidDecision;
    item.matchSuggestion = suggestion.detail;
    item.matchStage = "模型/规则降级匹配";
    item.matchedAt = new Date().toISOString();
    item.workspaceId = workspace.bidWorkspaceId;
    item.responseStatus = item.responseStatus || "待生成";
    item.downloadStatus = "已下载";
    item.downloadError = "";
    persistCatalog();
    recordProjectArtifacts(item, {
      workspaceId: workspace.bidWorkspaceId,
      decision: workspace.bidDecision,
      score: workspace.reasoning.score,
      status: workspace.reasoning.gate.canExport ? "已匹配待生成" : "待人工复核",
    });
    return { item, workspace, result, download, suggestion, agentFallback: String(agentError?.message || "") };
  }
}

function matchSortValue(item) {
  if (item.downloadStatus === "下载失败") return 9;
  if (item.matchDecision === "建议参与") return 1;
  if (item.matchDecision === "人工评估") return 2;
  if (item.matchDecision === "不建议参与") return 3;
  return 5;
}

async function batchMatchCatalog(ids, options = {}) {
  const requested = Array.isArray(ids) && ids.length
    ? ids.map((id) => catalog.find((item) => item.id === String(id))).filter(Boolean)
    : filteredCatalog(new URL("http://local/api/catalog?currentOnly=true"));
  const matched = [];
  const failed = [];
  for (const item of requested) {
    try {
      const result = await matchCatalogProject(item, options);
      matched.push({
        id: item.id,
        title: item.title,
        downloadStatus: item.downloadStatus,
        score: result.workspace.reasoning.score,
        decision: result.workspace.bidDecision,
        suggestion: result.suggestion.detail,
        workspaceId: result.workspace.bidWorkspaceId,
      });
    } catch (error) {
      failed.push({
        id: item.id,
        title: item.title,
        downloadStatus: item.downloadStatus || "下载失败",
        error: error.message || "项目下载或匹配失败",
        loginRequired: Boolean(error.details?.loginRequired),
        accountId: error.details?.accountId || "",
      });
    }
  }
  const sorted = requested.slice().sort((a, b) => {
    const rank = matchSortValue(a) - matchSortValue(b);
    if (rank) return rank;
    return Number(b.matchScore || -1) - Number(a.matchScore || -1);
  });
  return { matched, failed, items: sorted, processedAt: new Date().toISOString() };
}

async function generateCatalogResponse(item, options = {}) {
  try {
    const piResult = await runPiProjectWorkflow(item, "generate", options);
    const suggestion = suggestionForWorkspace(piResult.workspace);
    item.matchScore = piResult.workspace.reasoning.score;
    item.matchDecision = piResult.workspace.bidDecision;
    item.matchSuggestion = suggestion.detail;
    item.matchStage = "Pi 智能体信息匹配";
    item.workspaceId = piResult.workspace.bidWorkspaceId;
    item.agentRunId = piResult.agentRun.agentRunId;
    item.responseStatus = "Pi 智能体已生成";
    item.responseFiles = piResult.files;
    item.responseGeneratedAt = new Date().toISOString();
    persistCatalog();
    return {
      item,
      workspace: piResult.workspace,
      suggestion,
      files: piResult.files,
      forcedReview: !piResult.workspace.reasoning.gate.canExport,
      agentRun: piResult.agentRun,
    };
  } catch (agentError) {
    const matched = await matchCatalogProject(item, options);
    const exported = exportWorkspace(matched.workspace, true);
    item.responseStatus = matched.workspace.reasoning.gate.canExport ? "已生成-降级链路" : "已生成-待人工审核";
    item.responseFiles = exported.files;
    item.responseGeneratedAt = new Date().toISOString();
    persistCatalog();
    return {
      item,
      workspace: matched.workspace,
      suggestion: matched.suggestion,
      files: exported.files,
      forcedReview: !matched.workspace.reasoning.gate.canExport,
      agentFallback: String(agentError?.message || ""),
    };
  }
}

function automationSearchFilters() {
  return {
    // Leave service terms blank here so businessRelevance can apply the complete profile,
    // rather than narrowing every scheduled run to the first configured keyword.
    query: "",
    region: array(config.targetRegions)[0] || "",
    type: "",
    currentOnly: true,
  };
}

async function runScheduledSearch(trigger = "scheduled-search") {
  if (automationRunning) throw new Error("自动化流程正在运行，请稍候查看运行记录。");
  automationRunning = true;
  try {
    const search = await searchRealtime(automationSearchFilters());
    const run = {
      automationRunId: uid("search-schedule"),
      trigger,
      status: "搜索完成",
      startedAt: search.fetchedAt,
      finishedAt: new Date().toISOString(),
      candidateCount: search.items.length,
      completedCount: 0,
      reviewCount: 0,
      projects: [],
      errors: array(search.sources).filter((source) => source.status === "失败").map((source) => ({ id: source.sourceId, title: source.name, message: source.error || "抓取失败" })),
      search,
    };
    automationRuns.push(run);
    persistAutomationRuns();
    registerLibraryDocument("outputs", {
      id: run.automationRunId,
      name: `定时搜索产出 ${new Date(run.finishedAt).toLocaleString("zh-CN", { hour12: false })}`,
      type: "定时搜索回执",
      savedAt: run.finishedAt,
      source: `${search.collectedCount} 条抓取结果`,
      searchId: search.searchId,
      preview: JSON.stringify({ searchId: search.searchId, sources: search.sources, candidateCount: run.candidateCount, errors: run.errors }, null, 2),
    });
    return run;
  } finally {
    automationRunning = false;
  }
}

async function runAutomation(trigger = "manual") {
  if (automationRunning) throw new Error("自动化流程正在运行，请稍候查看运行记录。");
  automationRunning = true;
  const run = {
    automationRunId: uid("automation"),
    trigger,
    status: "运行中",
    startedAt: new Date().toISOString(),
    finishedAt: null,
    candidateCount: 0,
    completedCount: 0,
    reviewCount: 0,
    projects: [],
    errors: [],
  };
  automationRuns.push(run);
  persistAutomationRuns();
  try {
    const maxProjects = Math.max(1, Math.min(50, Number(config.automation?.maxProjects || 10)));
    try {
      run.search = await searchRealtime(automationSearchFilters());
    } catch (error) {
      run.search = {
        fetchedAt: new Date().toISOString(),
        collectedCount: 0,
        addedCount: 0,
        sources: [],
        error: error.message || "实时搜索未完成",
      };
      run.errors.push({ id: "realtime-search", title: "实时搜索", message: run.search.error });
    }
    const searchedItems = array(run.search?.items);
    const candidates = (searchedItems.length ? searchedItems : automationCatalog()).slice(0, maxProjects);
    run.candidateCount = candidates.length;
    run.candidateProjectIds = candidates.map((item) => item.id);
    for (const item of candidates) {
      try {
        const matched = preliminaryMatchCatalogProject(item);
        run.projects.push({
          id: item.id,
          title: item.title,
          download: { fileName: item.downloadFileName, downloadUrl: item.downloadUrl, status: item.downloadStatus || "未下载" },
          matchStage: item.matchStage,
          decision: matched.reasoning.decision,
          score: matched.reasoning.score,
          suggestion: matched.suggestion.detail,
          needsReview: true,
        });
        run.completedCount += 1;
        run.reviewCount += 1;
      } catch (error) {
        run.errors.push({ id: item.id, title: item.title, message: error.message || "项目处理失败" });
      }
    }
    run.status = run.errors.length && !run.completedCount ? "失败" : run.errors.length ? "部分完成" : "完成";
    run.finishedAt = new Date().toISOString();
    persistAutomationRuns();
    registerLibraryDocument("outputs", {
      id: run.automationRunId,
      name: `今日项目匹配结果 ${new Date(run.finishedAt).toLocaleString("zh-CN", { hour12: false })}`,
      type: "项目匹配运行记录",
      savedAt: run.finishedAt,
      source: `完成 ${run.completedCount} 个项目公告预匹配`,
      runStatus: run.status,
      preview: JSON.stringify({ status: run.status, searchId: run.search?.searchId, candidateCount: run.candidateCount, completedCount: run.completedCount, reviewCount: run.reviewCount, projects: run.projects, errors: run.errors }, null, 2),
    });
    return run;
  } finally {
    automationRunning = false;
    persistAutomationRuns();
  }
}

function scheduleClock(schedule) {
  try {
    const parts = new Intl.DateTimeFormat("zh-CN", {
      timeZone: schedule.timezone || "Asia/Shanghai",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    }).formatToParts(new Date()).reduce((result, item) => ({ ...result, [item.type]: item.value }), {});
    return { date: `${parts.year}-${parts.month}-${parts.day}`, time: `${parts.hour}:${parts.minute}` };
  } catch {
    const now = new Date();
    return { date: now.toISOString().slice(0, 10), time: now.toTimeString().slice(0, 5) };
  }
}

function checkScheduledAutomation() {
  const schedule = config.automation?.schedule;
  if (!schedule?.enabled || automationRunning) return;
  const clock = scheduleClock(schedule);
  if (clock.time !== schedule.time || schedulerState.lastScheduledDate === clock.date) return;
  schedulerState.lastScheduledDate = clock.date;
  persistSchedulerState();
  const scheduledTask = schedule.action === "workflow" ? runAutomation("scheduled") : runScheduledSearch("scheduled-search");
  scheduledTask.catch((error) => console.error(`定时任务启动失败：${error.message || error}`));
}

function monitoringConfig() {
  const monitoring = config.automation?.monitoring || {};
  return {
    enabled: monitoring.enabled !== false,
    intervalMinutes: Math.max(5, Math.min(1440, Number(monitoring.intervalMinutes || 30))),
  };
}

function scheduleNextMonitoringRun(from = Date.now()) {
  const monitoring = monitoringConfig();
  schedulerState.monitoringNextRunAt = new Date(from + monitoring.intervalMinutes * 60 * 1000).toISOString();
}

function checkContinuousMonitoring() {
  const monitoring = monitoringConfig();
  if (!monitoring.enabled || automationRunning) return;
  const nextRunAt = Date.parse(schedulerState.monitoringNextRunAt || "");
  if (!Number.isFinite(nextRunAt) || Date.now() >= nextRunAt) {
    schedulerState.monitoringLastStartedAt = new Date().toISOString();
    schedulerState.monitoringLastStatus = "运行中";
    scheduleNextMonitoringRun();
    persistSchedulerState();
    runScheduledSearch("continuous-monitoring")
      .then((run) => {
        schedulerState.monitoringLastRunAt = run.finishedAt || new Date().toISOString();
        schedulerState.monitoringLastStatus = run.errors?.length ? "部分站点失败" : "正常";
        persistSchedulerState();
      })
      .catch((error) => {
        schedulerState.monitoringLastRunAt = new Date().toISOString();
        schedulerState.monitoringLastStatus = `失败：${String(error?.message || "监控执行失败").slice(0, 160)}`;
        persistSchedulerState();
        console.error(`持续监控运行失败：${error.message || error}`);
      });
  }
}

setInterval(() => {
  checkScheduledAutomation();
  checkContinuousMonitoring();
}, 15000).unref();

function isTextDocument(name, mimeType = "") {
  return /^text\//i.test(mimeType)
    || /\.(txt|md|json|csv|xml|html?|ya?ml)$/i.test(String(name || ""));
}

function extractDocxText(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 22) return "";
  try {
    let eocd = -1;
    for (let offset = buffer.length - 22; offset >= Math.max(0, buffer.length - 65557); offset -= 1) {
      if (buffer.readUInt32LE(offset) === 0x06054b50) {
        eocd = offset;
        break;
      }
    }
    if (eocd < 0) return "";
    const centralSize = buffer.readUInt32LE(eocd + 12);
    const centralOffset = buffer.readUInt32LE(eocd + 16);
    const centralEnd = Math.min(buffer.length, centralOffset + centralSize);
    let cursor = centralOffset;
    while (cursor + 46 <= centralEnd && buffer.readUInt32LE(cursor) === 0x02014b50) {
      const compression = buffer.readUInt16LE(cursor + 10);
      const compressedSize = buffer.readUInt32LE(cursor + 20);
      const fileNameLength = buffer.readUInt16LE(cursor + 28);
      const extraLength = buffer.readUInt16LE(cursor + 30);
      const commentLength = buffer.readUInt16LE(cursor + 32);
      const localOffset = buffer.readUInt32LE(cursor + 42);
      const fileName = buffer.toString("utf8", cursor + 46, cursor + 46 + fileNameLength);
      if (fileName === "word/document.xml") {
        if (localOffset + 30 > buffer.length || buffer.readUInt32LE(localOffset) !== 0x04034b50) return "";
        const localNameLength = buffer.readUInt16LE(localOffset + 26);
        const localExtraLength = buffer.readUInt16LE(localOffset + 28);
        const start = localOffset + 30 + localNameLength + localExtraLength;
        const compressed = buffer.subarray(start, start + compressedSize);
        const xml = compression === 8 ? zlib.inflateRawSync(compressed).toString("utf8") : compressed.toString("utf8");
        return xml
          .replace(/<w:tab[^>]*\/>/g, "\t")
          .replace(/<\/w:p>/g, "\n")
          .replace(/<[^>]+>/g, "")
          .replace(/&amp;/g, "&").replace(/&lt;/g, "<").replace(/&gt;/g, ">")
          .replace(/&quot;/g, '"').replace(/&apos;/g, "'")
          .replace(/\n{3,}/g, "\n\n")
          .trim()
          .slice(0, 12000);
      }
      cursor += 46 + fileNameLength + extraLength + commentLength;
    }
  } catch {
    return "";
  }
  return "";
}

function extractUploadedText(name, content, storagePath = "") {
  const extension = path.extname(String(name || "")).toLowerCase();
  if (isTextDocument(name, "")) return Buffer.from(content).toString("utf8");
  if (extension === ".doc" || extension === ".docx") return extractDocxText(Buffer.from(content));
  if (extension === ".pdf") return extractedPdfText(storagePath);
  return "";
}

function saveLibraryDocument(key, payload) {
  const library = libraries[key];
  if (!library) throw new Error("未知资料库");
  const name = String(payload.name || "未命名资料").trim().replace(/[\\/:*?"<>|]/g, "_").slice(0, 100) || "未命名资料";
  const file = payload.file || null;
  const text = String(payload.text || "").trim();
  let content;
  let mimeType = String(file?.mimeType || payload.mimeType || "text/plain").slice(0, 160);
  if (file?.contentBase64) {
    const encoded = String(file.contentBase64);
    if (!/^[A-Za-z0-9+/=\r\n]+$/.test(encoded)) throw new Error("上传文件编码无效。");
    content = Buffer.from(encoded, "base64");
    if (!content.length || content.length > 5 * 1024 * 1024) throw new Error("单个上传文件需小于 5MB。");
  } else {
    if (text.length < 2) throw new Error("请上传文件或填写资料内容。");
    content = Buffer.from(text, "utf8");
  }
  const documentId = uid("library");
  const directory = path.join(libraryRoot, key);
  fs.mkdirSync(directory, { recursive: true });
  const fileName = `${documentId}-${name}`;
  const storedFile = safeFile(directory, fileName);
  fs.writeFileSync(storedFile, content);
  const preview = isTextDocument(name, mimeType)
    ? content.toString("utf8").slice(0, 12000)
    : /\.pdf$/i.test(name)
      ? extractedPdfText(`runtime/libraries/${key}/${fileName}`).slice(0, 12000)
      : /\.docx$/i.test(name)
        ? extractDocxText(content)
        : "";
  library.documents = [...array(library.documents), {
    id: documentId,
    name,
    type: payload.type || "资料文档",
    savedAt: new Date().toISOString(),
    storagePath: `runtime/libraries/${key}/${fileName}`,
    contentHash: hash(content),
    mimeType,
    size: content.length,
    preview,
  }];
  persistLibraries();
  if (key === "tenders" && isTextDocument(name, mimeType)) {
    const item = {
      id: documentId,
      title: name.replace(/\.(txt|md|json)$/i, ""),
      category: "资料库导入",
      region: "待识别",
      noticeType: "资料库导入",
      publishedDate: new Date().toISOString().slice(0, 10),
      source: "招标项目库",
      sourceUrl: "",
      dataOrigin: "library_upload",
      analysisText: content.toString("utf8"),
    };
    catalog.unshift(item);
    persistCatalog();
  }
  return library.documents.at(-1);
}

function deleteLibraryDocument(key, documentId) {
  const library = libraries[key];
  if (!library) throw new Error("未知资料库");
  if (documentId === "company-profile") throw new Error("公司基础信息由系统维护，不能删除。");
  const index = array(library.documents).findIndex((document) => document.id === documentId);
  if (index < 0) throw new Error("未找到要删除的资料。");
  const [document] = library.documents.splice(index, 1);
  const storagePath = String(document.storagePath || "");
  const expectedPrefix = `runtime/libraries/${key}/`;
  if (storagePath.startsWith(expectedPrefix)) {
    const target = path.resolve(ROOT, storagePath);
    const libraryDirectory = path.resolve(libraryRoot, key);
    if (target.startsWith(libraryDirectory + path.sep) && fs.existsSync(target)) fs.unlinkSync(target);
  }
  if (key === "tenders") {
    const catalogIndex = catalog.findIndex((item) => item.id === document.id);
    if (catalogIndex >= 0) {
      catalog.splice(catalogIndex, 1);
      persistCatalog();
    }
  }
  persistLibraries();
  return document;
}

function answerWorkspace(item, question) {
  let answer = `已基于“${item.project.projectName}”完成规则检索。`;
  if (question.includes("缺") || question.includes("材料")) {
    const missing = item.reasoning.rows.filter((row) => row.status !== "满足");
    answer = missing.length ? `待补充或确认：${missing.map((row) => row.requirement).join("；")}。` : "当前结构化匹配未发现缺失材料，但仍需核验附件。";
  } else if (question.includes("风险") || question.includes("注意")) {
    answer = item.reasoning.risks.map((risk) => `${risk.level}风险：${risk.title}`).join("；");
  } else if (question.includes("截止") || question.includes("时间")) {
    answer = `投标截止时间为${item.project.bidDeadline}，距离截止约${item.reasoning.remainingDays ?? "未识别"}天。`;
  } else if (question.includes("导出")) {
    answer = item.reasoning.gate.canExport ? "结构化导出闸门已通过，仍需人工审核签章。" : `暂不能直接导出：${item.reasoning.gate.reason}。`;
  }
  return { answer, sourceRefs: [item.project.tenderProjectId, item.bidWorkspaceId] };
}

function persistPiAgentRuns() {
  fs.writeFileSync(piAgentRunsPath, JSON.stringify(piAgentRuns.slice(-50), null, 2), "utf8");
}

function projectSummaryForAgent(item) {
  return {
    projectId: item.id,
    projectName: item.title,
    region: item.region || "待识别",
    serviceType: item.category || "待分类",
    noticeType: item.noticeType || "待识别",
    publishedDate: item.publishedDate || "",
    bidDeadline: item.bidDeadline || "未识别",
    source: item.source || "",
    sourceUrl: item.sourceUrl || "",
    dataOrigin: item.dataOrigin || "",
    downloadStatus: item.downloadStatus || "未下载",
    downloadError: item.downloadError || "",
    matchScore: item.matchScore ?? null,
    matchDecision: item.matchDecision || "",
    matchSuggestion: item.matchSuggestion || "",
    responseStatus: item.responseStatus || "",
  };
}

function workspaceSummaryForAgent(workspace) {
  return {
    workspaceId: workspace.bidWorkspaceId,
    projectName: workspace.project.projectName,
    decision: workspace.bidDecision,
    score: workspace.reasoning.score,
    remainingDays: workspace.reasoning.remainingDays,
    gate: workspace.reasoning.gate,
    risks: workspace.reasoning.risks,
    missingMaterials: workspace.reasoning.rows
      .filter((row) => row.status !== "满足")
      .map((row) => ({ requirement: row.requirement, status: row.status, evidence: row.evidence, risk: row.risk })),
    responseDraft: {
      status: workspace.aiDraft?.status || "已生成",
      version: workspace.draftVersion,
      summary: workspace.aiDraft?.summary || "已生成待人工审核的响应文件草稿。",
    },
  };
}

async function runPiTenderAgent(message, options = {}) {
  const run = {
    agentRunId: uid("pi-agent"),
    startedAt: new Date().toISOString(),
    finishedAt: null,
    status: "运行中",
    task: String(message || "").slice(0, 2000),
    model: config.ai?.model || "",
    toolAudit: [],
  };
  piAgentRuns.push(run);
  persistPiAgentRuns();
  try {
    const piModuleUrl = pathToFileURL(path.join(ROOT, "pi-tender-agent.mjs")).href;
    const { runTenderAgent } = await import(piModuleUrl);
    const result = await runTenderAgent({
      message,
      ai: {
        apiKey: String(config.ai?.apiKey || "").trim(),
        model: String(config.ai?.model || "").trim(),
        baseUrl: aiApiBaseUrl().toString().replace(/\/$/, ""),
      },
      sessionId: run.agentRunId,
      tools: {
        search: async (filters) => {
          const search = await searchRealtime(filters);
          return {
            searchId: search.searchId,
            fetchedAt: search.fetchedAt,
            sourceSummary: search.sources.map((source) => ({
              name: source.name,
              status: source.status,
              matchedCount: source.count,
              error: source.error || "",
            })),
            projects: search.items.slice(0, 12).map(projectSummaryForAgent),
          };
        },
        listCandidates: async (limit) => {
          const items = automationCatalog().slice(0, Math.max(1, Math.min(20, Number(limit) || 8)));
          return {
            total: automationCatalog().length,
            projects: items.map(projectSummaryForAgent),
            note: "仅返回通过当前业务画像、关键词、地区和排除词规则的项目。",
          };
        },
        analyzeProject: async (projectId) => {
          const item = catalog.find((project) => project.id === projectId);
          if (!item) throw new Error("未找到指定项目，请先调用候选项目工具获取 projectId。");
          const existing = completedPiWorkspaceForProject(item.id);
          if (existing) return { ...workspaceSummaryForAgent(existing), reusedExistingWorkspace: true };
          if (!item.analysisText) {
            const account = accountForProject(item);
            const sessionId = account
              ? String(options.sessionIds?.[account.id] || options.sessionId || "")
              : String(options.sessionId || "");
            await saveDownload(item, { sessionId });
          }
          const result = await analyze(item.analysisText || item.title, item.source || "智能体任务", { id: item.id, title: item.title });
          return workspaceSummaryForAgent(result.workspace);
        },
        createReminder: async (payload) => {
          const workspace = workspaces.get(payload.workspaceId);
          if (!workspace) throw new Error("未找到信息匹配工作台，不能创建提醒。");
          const reminder = createReminderRecord({
            ...payload,
            source: "Pi 招标业务智能体",
            message: payload.message || `项目“${workspace.project.projectName}”已完成信息匹配，结论：${workspace.bidDecision}，评分：${workspace.reasoning.score}。请人工复核风险和资料后决定是否参与。`,
          });
          return {
            reminderId: reminder.id,
            projectName: reminder.projectTitle,
            level: reminder.level,
            recipient: reminder.recipient,
            channel: reminder.channel,
            status: reminder.status,
            note: "已创建系统提醒记录；当前 Demo 未接入企业微信、钉钉、邮件或短信推送。",
          };
        },
        exportResponse: async (workspaceId, reviewOnly = false) => {
          const workspace = workspaces.get(workspaceId);
          if (!workspace) throw new Error("未找到响应文件工作台。");
          const exported = exportWorkspace(workspace, reviewOnly === true);
          return {
            workspaceId,
            projectName: workspace.project.projectName,
            status: workspace.reasoning.gate.canExport ? "已生成响应文件导出包" : "已生成待人工审核响应文件导出包",
            files: exported.files,
            note: workspace.reasoning.gate.canExport
              ? "导出结果仍须由业务、法务和授权人审核后方可用于递交。"
              : "当前存在风险或待补材料，文件仅为内部审核底稿，不可直接递交。",
          };
        },
      },
    });
    run.status = "完成";
    run.answer = result.answer.slice(0, 6000);
    run.toolAudit = result.toolAudit;
    run.workspaceIds = result.toolAudit
      .filter((entry) => entry.tool === "analyze_project" && entry.result?.workspaceId)
      .map((entry) => entry.result.workspaceId);
    run.finishedAt = new Date().toISOString();
    persistPiAgentRuns();
    return {
      ...result,
      agentRun: run,
      workspaces: run.workspaceIds.map((workspaceId) => workspaces.get(workspaceId)).filter(Boolean),
    };
  } catch (error) {
    run.status = "失败";
    run.error = String(error?.message || "Pi 智能体运行失败").slice(0, 2000);
    run.finishedAt = new Date().toISOString();
    persistPiAgentRuns();
    throw error;
  }
}

function filteredCatalog(url) {
  return catalog.filter((item) => matchesCatalogFilters(item, {
    query: url.searchParams.get("q"),
    region: url.searchParams.get("region"),
    type: url.searchParams.get("type"),
    currentOnly: url.searchParams.get("currentOnly"),
  }));
}

function automationCatalog() {
  const targetTypes = array(config.targetServiceTypes);
  const targetRegions = array(config.targetRegions).filter((region) => !/^(中国|全国)$/i.test(String(region || "").trim()));
  const includeKeywords = array(config.includeKeywords);
  const excludeKeywords = array(config.excludeKeywords);
  return catalog.filter((item) => {
    const haystack = normalizeSearchText(item);
    const typeMatch = !targetTypes.length || targetTypes.some((type) => haystack.includes(type));
    const regionMatch = !targetRegions.length || targetRegions.some((region) => haystack.includes(region));
    const includeMatch = !includeKeywords.length || includeKeywords.some((keyword) => haystack.includes(keyword));
    const excludeMatch = excludeKeywords.filter((keyword) => String(keyword || "").trim() && String(keyword).trim() !== "招标公告").some((keyword) => haystack.includes(keyword));
    return matchesCatalogFilters(item) && businessRelevance(item) && typeMatch && regionMatch && includeMatch && !excludeMatch;
  });
}

function aiFallbackSummary(error, kind) {
  const message = String(error?.message || "").replace(/\s+/g, " ").trim();
  if (/EACCES|ECONNREFUSED|ENETUNREACH|EHOSTUNREACH|ETIMEDOUT|无法连接/.test(message)) {
    return kind === "draft"
      ? `AI 响应输出未完成：${message || "模型服务不可达"}。已保留结构化响应文件草稿。`
      : `AI 信息匹配未完成：${message || "模型服务不可达"}。已使用规则结果继续完成信息匹配。`;
  }
  return kind === "draft"
    ? `AI 响应输出未完成，已保留结构化草稿：${message}`
    : `AI 信息匹配未完成，已使用规则结果继续完成分析：${message}`;
}

function safeFile(root, name) {
  const target = path.resolve(root, path.basename(name));
  if (!target.startsWith(path.resolve(root) + path.sep)) throw new Error("非法文件路径");
  return target;
}

function configuredSourceFor(item) {
  const sources = array(config.search?.sources);
  return sources.find((source) => source.id === item.sourceKey);
}

async function detailTextFor(item) {
  if (item.dataOrigin !== "realtime" || !item.sourceUrl) return "";
  const source = configuredSourceFor(item);
  if (!source) throw new Error("找不到该实时项目对应的数据源配置。");
  const listUrl = safeRemoteUrl(sourceUrl(source));
  const detailUrl = safeRemoteUrl(item.sourceUrl);
  if (!listUrl || !detailUrl || listUrl.hostname !== detailUrl.hostname) throw new Error("公告详情地址不属于已配置的数据源。");
  const { content, url } = await fetchRemoteText(detailUrl.toString(), normalizeSearchConfig(config.search).timeoutSeconds);
  const text = plainText(content).slice(0, 180000);
  if (text.length < 20) throw new Error("公告详情页未提取到可用正文。");
  return `实时详情页：${url}\n\n${text}`;
}

async function saveDownload(item, options = {}) {
  const fileName = `${item.id}.txt`;
  const sourceFiles = [];
  let detailText = "";
  let captureNote = item.dataOrigin === "realtime" ? "等待抓取公告详情" : "保存本地/演示文件";
  item.downloadStatus = "下载中";
  item.downloadError = "";
  item.downloadAttemptedAt = new Date().toISOString();
  persistCatalog();
  if (item.dataOrigin === "realtime") {
    const source = configuredSourceFor(item);
    if (!source) {
      item.downloadStatus = "下载失败";
      item.downloadError = "找不到该实时项目对应的数据源配置。";
      persistCatalog();
      throw downloadFailure(item.downloadError, { projectId: item.id });
    }
    const requiresLogin = sourceRequiresLogin(source);
    const account = accountForProject(item);
    if (requiresLogin && !account) {
      item.downloadStatus = "下载失败";
      item.downloadError = "当前平台需要登录，但尚未配置可用的平台账号。";
      persistCatalog();
      throw downloadFailure(item.downloadError, { loginRequired: true, projectId: item.id });
    }
    const session = requiresLogin ? authenticatedPlatformSession(options.sessionId, account.id) : null;
    if (requiresLogin && !session) {
      item.downloadStatus = "下载失败";
      item.downloadError = `请先登录${account.name}后再下载招标文件。`;
      item.loginRequired = true;
      persistCatalog();
      throw downloadFailure(item.downloadError, { loginRequired: true, accountId: account.id, accountName: account.name, projectId: item.id });
    }
    try {
      const detailUrl = safeRemoteUrl(item.sourceUrl);
      const detail = await fetchRemoteText(detailUrl.toString(), normalizeSearchConfig(config.search).timeoutSeconds);
      const text = plainText(detail.content).slice(0, 180000);
      if (text.length < 20) throw new Error("公告详情页未提取到可用正文。");
      detailText = `实时详情页：${detail.url}\n\n${text}`;
      const attachments = requiresLogin ? [] : extractPublicAttachmentUrls(detail.content, detail.url, source);
      const attachmentFiles = [];
      for (const attachment of attachments) {
        try {
          const fetched = await fetchRemoteFile(attachment.url, normalizeSearchConfig(config.search).timeoutSeconds, detailUrl.hostname);
          const attachmentName = attachmentFileName(fetched.url, fetched.disposition, attachmentFiles.length);
          const storedName = `${String(attachmentFiles.length + 1).padStart(2, "0")}-${attachmentName}`;
          fs.writeFileSync(projectFile(item.id, "source", storedName), fetched.content);
          attachmentFiles.push({
            name: storedName,
            url: projectFileUrl(item.id, "source", storedName),
            sourceUrl: fetched.url,
            contentType: fetched.contentType,
            size: fetched.content.length,
            label: attachment.label,
          });
        } catch (error) {
          attachmentFiles.push({
            name: attachment.label || "公开附件",
            sourceUrl: attachment.url,
            status: "下载失败",
            error: String(error?.message || "公开附件下载失败"),
          });
        }
      }
      sourceFiles.push(...attachmentFiles.filter((file) => file.url));
      captureNote = requiresLogin
        ? "已抓取登录后公告详情页"
        : `已抓取公开公告详情页${attachmentFiles.length ? `；公开附件 ${attachmentFiles.filter((file) => file.url).length} 个已保存` : "；未发现可直接下载的公开附件"}`;
      item.analysisText = detailText;
      item.detailFetchedAt = new Date().toISOString();
    } catch (error) {
      item.downloadStatus = "下载失败";
      item.downloadError = `公告详情抓取失败：${error.message || "未知错误"}`;
      item.downloadFailedAt = new Date().toISOString();
      item.manualUrl = item.sourceUrl || "";
      persistCatalog();
      throw downloadFailure(item.downloadError, { loginRequired: false, manualUrl: item.manualUrl, projectId: item.id });
    }
  }
  const content = `来源：${item.source}\n原始链接：${item.sourceUrl}\n抓取时间：${new Date().toISOString()}\n抓取状态：${captureNote}\n\n${detailText || item.analysisText || item.title}`;
  const sourceUrl = projectFileUrl(item.id, "source", fileName);
  const storagePath = `runtime/projects/${projectFolder(item.id)}/source/${fileName}`;
  fs.writeFileSync(projectFile(item.id, "source", fileName), content, "utf8");
  const existing = libraries.tenders.documents.find((document) => document.id === item.id);
  const tenderDocument = {
      id: item.id,
      name: item.title,
      projectId: item.id,
      projectName: item.title,
      category: item.category || "待分类",
      region: item.region || "待识别",
      type: "招标项目",
      savedAt: new Date().toISOString(),
      storagePath,
      contentHash: hash(content),
      source: item.source || "自动化流程",
      sourceFiles: [{ name: fileName, url: sourceUrl }, ...sourceFiles],
      downloadStatus: "已下载",
      downloadError: "",
  };
  if (existing) Object.assign(existing, tenderDocument);
  else libraries.tenders.documents = [...array(libraries.tenders.documents), tenderDocument];
  recordProjectArtifacts(item, { sourceFiles: tenderDocument.sourceFiles, status: "已下载待匹配" });
  item.downloadStatus = "已下载";
  item.downloadError = "";
  item.downloadedAt = new Date().toISOString();
  item.downloadUrl = sourceUrl;
  item.downloadFileName = fileName;
  item.sourceFiles = tenderDocument.sourceFiles;
  item.loginRequired = item.dataOrigin === "realtime" && sourceRequiresLogin(configuredSourceFor(item));
  persistCatalog();
  persistLibraries();
  return {
    fileName,
    contentHash: hash(content),
    downloadUrl: sourceUrl,
    files: tenderDocument.sourceFiles,
    status: "已下载",
    accountId: item.loginRequired ? accountForProject(item)?.id || "" : "",
    captureNote,
  };
}

function serve(res, pathname, options = {}) {
  const staticFiles = { "/": ["index.html", "text/html; charset=utf-8"], "/index.html": ["index.html", "text/html; charset=utf-8"], "/sample-tender.txt": ["sample-tender.txt", "text/plain; charset=utf-8"], "/ontology-governance.json": ["ontology-governance.json", "application/json; charset=utf-8"], "/company-profile.json": ["company-profile.json", "application/json; charset=utf-8"] };
  let root;
  let fileName;
  if (pathname.startsWith("/runtime/downloads/")) { root = downloadRoot; fileName = decodeURIComponent(pathname.slice(19)); }
  if (pathname.startsWith("/runtime/exports/")) { root = exportRoot; fileName = decodeURIComponent(pathname.slice(18)); }
  if (pathname.startsWith("/runtime/searches/")) { root = searchRunRoot; fileName = decodeURIComponent(pathname.slice(18)); }
  if (pathname.startsWith("/runtime/libraries/")) {
    root = libraryRoot;
    fileName = decodeURIComponent(pathname.slice(19));
    const parts = fileName.split("/").filter(Boolean);
    if (parts.length !== 2) return send(res, 404, "Not found");
    const directory = safeFile(root, parts[0]);
    root = directory;
    fileName = parts[1];
  }
  if (pathname.startsWith("/runtime/projects/")) {
    const parts = pathname.slice(18).split("/").map(decodeURIComponent);
    if (parts.length !== 3 || !["source", "outputs"].includes(parts[1])) return send(res, 404, "Not found");
    root = path.resolve(projectRoot, projectFolder(parts[0]), parts[1]);
    fileName = parts[2];
  }
  if (root) {
    const file = safeFile(root, fileName);
    if (!fs.existsSync(file)) return send(res, 404, "Not found");
    const extension = path.extname(file).toLowerCase();
    const contentType = extension === ".docx"
      ? "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
      : extension === ".pdf"
        ? "application/pdf"
        : extension === ".json"
          ? "application/json; charset=utf-8"
          : extension === ".txt"
            ? "text/plain; charset=utf-8"
            : "text/markdown; charset=utf-8";
    return send(res, 200, fs.readFileSync(file), contentType, { "Content-Disposition": contentDisposition(file, options.inline) });
  }
  const entry = staticFiles[pathname];
  if (!entry) return send(res, 404, "Not found");
  return send(res, 200, fs.readFileSync(path.join(ROOT, entry[0])), entry[1]);
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  try {
    if (req.method === "GET" && url.pathname === "/api/bootstrap") {
      const initialCatalogUrl = new URL("http://local/api/catalog?currentOnly=true");
      return sendJson(res, { ontology: { name: ontology.meta.name, version: ontology.meta.version, classes: ontology.classes.length, relations: ontology.relations.length, libraries: ontology.logicalLibraries, rules: ontology.businessRules }, company, config: publicConfig(), platformAuth: platformAuthSnapshot(), libraries: librarySnapshot(), reminders: reminderSnapshot(), catalog: filteredCatalog(initialCatalogUrl), automation: automationStatus(), piAgent: { enabled: config.ai?.enabled !== false, recentRuns: piAgentRuns.slice(-5).reverse().map(({ transcript, ...run }) => run) }, workspaces: [...workspaces.values()].map((item) => ({ id: item.bidWorkspaceId, title: item.project.projectName, status: item.workspaceStatus, decision: item.bidDecision })) });
    }
    if (req.method === "GET" && url.pathname === "/api/platform-auth/status") return sendJson(res, platformAuthSnapshot());
    if (req.method === "POST" && url.pathname === "/api/platform-auth/login") {
      const payload = JSON.parse(await readBody(req) || "{}");
      const account = config.platformAccounts.find((item) => item.id === String(payload.accountId || "")) || config.platformAccounts.find((item) => item.enabled !== false);
      if (!account || account.enabled === false) return sendJson(res, { error: "没有可用的平台账号，请先在配置中添加并启用账号。" }, 400);
      if (account.simulated) {
        if (account.username && String(payload.username || account.username).trim() !== account.username) return sendJson(res, { error: "演示平台账号用户名不匹配。" }, 401);
        if (account.password && String(payload.password === undefined ? account.password : payload.password) !== account.password) return sendJson(res, { error: "演示平台账号密码不匹配。" }, 401);
      } else {
        if (!account.loginUrl) return sendJson(res, { error: "该平台未配置登录地址。" }, 400);
        return sendJson(res, { error: "当前 Demo 仅支持演示平台模拟登录；真实平台需为该站点配置登录适配器。", loginUrl: account.loginUrl }, 501);
      }
      const session = {
        sessionId: uid("platform-session"),
        accountId: account.id,
        accountName: account.name,
        loggedInAt: new Date().toISOString(),
        expiresAt: new Date(Date.now() + 8 * 60 * 60 * 1000).toISOString(),
      };
      platformSessions.set(session.sessionId, session);
      return sendJson(res, { ok: true, session, platformAuth: platformAuthSnapshot(), message: account.simulated ? "演示平台模拟登录成功。" : "平台登录成功。" });
    }
    if (req.method === "GET" && url.pathname === "/api/agent/runs") {
      return sendJson(res, {
        runs: piAgentRuns.slice(-20).reverse().map(({ transcript, ...run }) => run),
        total: piAgentRuns.length,
      });
    }
    if (req.method === "GET" && url.pathname === "/api/reminders") return sendJson(res, reminderSnapshot());
    if (req.method === "POST" && url.pathname === "/api/reminders") {
      const payload = JSON.parse(await readBody(req));
      const reminder = createReminderRecord(payload);
      return sendJson(res, { reminder, reminders: reminderSnapshot() });
    }
    if (req.method === "GET" && url.pathname === "/api/libraries") return sendJson(res, librarySnapshot());
    if (req.method === "DELETE" && url.pathname.startsWith("/api/libraries/")) {
      const parts = url.pathname.slice("/api/libraries/".length).split("/").map(decodeURIComponent);
      if (parts.length !== 2) return sendJson(res, { error: "资料删除地址无效。" }, 400);
      const document = deleteLibraryDocument(parts[0], parts[1]);
      return sendJson(res, { document, libraries: librarySnapshot() });
    }
    if (req.method === "GET" && url.pathname.startsWith("/api/libraries/")) {
      const key = decodeURIComponent(url.pathname.slice("/api/libraries/".length));
      return sendJson(res, libraryDetail(key));
    }
    if (req.method === "POST" && url.pathname.startsWith("/api/libraries/")) {
      const key = decodeURIComponent(url.pathname.slice("/api/libraries/".length));
      const payload = JSON.parse(await readBody(req));
      return sendJson(res, { document: saveLibraryDocument(key, payload), libraries: librarySnapshot() });
    }
    if (req.method === "GET" && url.pathname === "/api/catalog") return sendJson(res, filteredCatalog(url));
    if (req.method === "POST" && url.pathname === "/api/search/realtime") {
      const payload = JSON.parse(await readBody(req) || "{}");
      const result = await searchRealtime({
        query: String(payload.query || "").trim(),
        region: String(payload.region || "").trim(),
        type: String(payload.type || "").trim(),
        currentOnly: payload.currentOnly === true,
      });
      return sendJson(res, { ...result, libraries: librarySnapshot() });
    }
    if (req.method === "POST" && url.pathname.startsWith("/api/catalog/") && url.pathname.endsWith("/read")) {
      const id = decodeURIComponent(url.pathname.slice("/api/catalog/".length, -"/read".length));
      const item = markTenderAsRead(id);
      return item
        ? sendJson(res, { item, reminders: reminderSnapshot() })
        : sendJson(res, { error: "未找到项目" }, 404);
    }
    if (req.method === "POST" && url.pathname.startsWith("/api/catalog/") && url.pathname.endsWith("/follow-up")) {
      const id = decodeURIComponent(url.pathname.slice("/api/catalog/".length, -"/follow-up".length));
      const payload = JSON.parse(await readBody(req) || "{}");
      const item = setTenderFollowUp(id, payload.followUp !== false, payload.followUpStatus);
      return item
        ? sendJson(res, { item })
        : sendJson(res, { error: "未找到项目" }, 404);
    }
    if (req.method === "GET" && url.pathname.startsWith("/api/catalog/")) {
      const item = catalog.find((entry) => entry.id === decodeURIComponent(url.pathname.slice(13)));
      return item ? sendJson(res, item) : sendJson(res, { error: "未找到项目" }, 404);
    }
    if (req.method === "POST" && url.pathname.startsWith("/api/catalog/") && url.pathname.endsWith("/download")) {
      const id = decodeURIComponent(url.pathname.slice(13, -9));
      const item = catalog.find((entry) => entry.id === id);
      if (!item) return sendJson(res, { error: "未找到项目" }, 404);
      const payload = JSON.parse(await readBody(req) || "{}");
      try {
        return sendJson(res, { item, download: await saveDownload(item, { sessionId: payload.sessionId }) });
      } catch (error) {
        return sendJson(res, { error: error.message, code: error.code || "DOWNLOAD_FAILED", detail: error.details || {}, item }, error.details?.loginRequired ? 401 : 502);
      }
    }
    if (req.method === "POST" && url.pathname === "/api/tenders/match") {
      const payload = JSON.parse(await readBody(req) || "{}");
      const result = await batchMatchCatalog(payload.projectIds, {
        sessionId: payload.sessionId,
        sessionIds: payload.sessionIds && typeof payload.sessionIds === "object" ? payload.sessionIds : {},
        forceRefresh: payload.forceRefresh === true,
      });
      return sendJson(res, { ...result, libraries: librarySnapshot() });
    }
    if (req.method === "POST" && url.pathname.startsWith("/api/tenders/") && url.pathname.endsWith("/generate-response")) {
      const id = decodeURIComponent(url.pathname.slice("/api/tenders/".length, -"/generate-response".length));
      const item = catalog.find((entry) => entry.id === id);
      if (!item) return sendJson(res, { error: "未找到项目" }, 404);
      const payload = JSON.parse(await readBody(req) || "{}");
      try {
        const result = await generateCatalogResponse(item, {
          sessionId: payload.sessionId,
          sessionIds: payload.sessionIds && typeof payload.sessionIds === "object" ? payload.sessionIds : {},
          forceRefresh: payload.forceRefresh === true,
        });
        return sendJson(res, { message: "响应文件已生成", ...result, libraries: librarySnapshot() });
      } catch (error) {
        return sendJson(res, { error: error.message, code: error.code || "RESPONSE_GENERATION_FAILED", detail: error.details || {}, item }, error.details?.loginRequired ? 401 : 502);
      }
    }
    if (req.method === "POST" && url.pathname === "/api/import") {
      const payload = JSON.parse(await readBody(req) || "{}");
      const originalName = String(payload.name || payload.file?.name || "本地导入招标文件").trim().replace(/[\\/:*?"<>|]/g, "_").slice(0, 120) || "本地导入招标文件";
      const uploadedFile = payload.file || null;
      let content;
      let fileName = originalName;
      let mimeType = String(uploadedFile?.mimeType || payload.mimeType || "").slice(0, 160);
      if (uploadedFile?.contentBase64) {
        const encoded = String(uploadedFile.contentBase64);
        if (!/^[A-Za-z0-9+/=\r\n]+$/.test(encoded)) return sendJson(res, { error: "上传文件编码无效。" }, 400);
        content = Buffer.from(encoded, "base64");
        if (!content.length || content.length > 20 * 1024 * 1024) return sendJson(res, { error: "招标文件需小于20MB。" }, 400);
      } else {
        const text = String(payload.text || "");
        if (text.trim().length < 20) return sendJson(res, { error: "文件内容至少需要20个字符。" }, 400);
        content = Buffer.from(text, "utf8");
        fileName = "招标文件.txt";
        mimeType = "text/plain";
      }
      const itemId = uid("local");
      const sourceFileName = `${itemId}-${fileName}`;
      const storagePath = `runtime/projects/${projectFolder(itemId)}/source/${sourceFileName}`;
      const storedPath = projectFile(itemId, "source", sourceFileName);
      fs.writeFileSync(storedPath, content);
      const extractedText = extractUploadedText(fileName, content, storagePath).replace(/\u0000/g, "").trim();
      if (extractedText.length < 20) {
        if (/\.(pdf|docx?)$/i.test(fileName)) {
          return sendJson(res, { error: `未能从“${fileName}”提取到足够文字。请确认文件不是扫描图片版，或先转换为可复制文字的 PDF/Word。`, parseStatus: "解析失败" }, 422);
        }
        return sendJson(res, { error: "文件内容至少需要20个字符。", parseStatus: "解析失败" }, 400);
      }
      const detectedProject = parseTender(extractedText, "本地上传");
      const structuredTitle = extractedText.match(/项目名称\s*[:：]\s*([^\r\n]+)/)?.[1]?.trim();
      const fileTitle = originalName.replace(/\.(pdf|docx?|txt|md|json)$/i, "").trim();
      const detectedTitle = structuredTitle
        || (detectedProject.projectName && detectedProject.projectName !== "未识别" ? detectedProject.projectName : "")
        || fileTitle
        || "本地导入招标文件";
      const detectedDeadline = extractBidDeadline(extractedText)?.value || detectedProject.bidDeadline;
      const detectedFileAccessWindow = extractDocumentAccessWindow(extractedText);
      const item = {
        id: itemId,
        title: detectedTitle,
        category: inferCategory(`${extractedText}\n${fileName}`, detectedProject.serviceType || "本地文件"),
        region: detectedProject.serviceLocation || "待识别",
        noticeType: "本地上传招标文件",
        publishedDate: new Date().toISOString().slice(0, 10),
        source: "本地上传",
        sourceUrl: "",
        dataOrigin: "local_upload",
        bidDeadline: detectedDeadline || "",
        fileAccessStart: detectedFileAccessWindow?.start || "",
        fileAccessEnd: detectedFileAccessWindow?.end || "",
        fileAccessEvidenceLabel: detectedFileAccessWindow?.evidenceLabel || "",
        deadlineStatus: "待人工复核",
        accessNote: detectedDeadline ? "已从上传文件初步识别投标截止时间，仍需人工核验原文件。" : "上传文件未识别到投标截止时间，请人工核验原文件。",
        downloadStatus: "已下载",
        downloadFileName: fileName,
        downloadUrl: projectFileUrl(itemId, "source", sourceFileName),
        fileType: path.extname(fileName).replace(".", "").toUpperCase() || "TXT",
        parseStatus: "解析完成",
        parsedAt: new Date().toISOString(),
        extractedTextLength: extractedText.length,
        analysisText: extractedText,
      };
      catalog.unshift(item);
      const document = {
        id: item.id,
        name: item.title,
        projectId: item.id,
        projectName: item.title,
        category: item.category,
        region: item.region,
        type: "招标项目",
        savedAt: new Date().toISOString(),
        storagePath,
        source: item.source,
        contentHash: hash(content),
        mimeType: mimeType || (path.extname(fileName).toLowerCase() === ".pdf" ? "application/pdf" : "application/octet-stream"),
        size: content.length,
        parseStatus: "解析完成",
        parsedTextLength: extractedText.length,
        preview: extractedText.slice(0, 12000),
        sourceFiles: [{ name: fileName, url: item.downloadUrl, mimeType: mimeType || "" }],
      };
      libraries.tenders.documents = [...array(libraries.tenders.documents), document];
      recordProjectArtifacts(item, { sourceFiles: document.sourceFiles, status: "已导入待匹配" });
      persistLibraries();
      persistCatalog();
      return sendJson(res, { item, file: { name: fileName, mimeType: document.mimeType, size: content.length, parseStatus: "解析完成", extractedTextLength: extractedText.length } });
    }
    if (req.method === "POST" && url.pathname === "/api/analyze") {
      const payload = JSON.parse(await readBody(req));
      if (!payload.text || payload.text.trim().length < 20) return sendJson(res, { error: "请先导入至少20个字符的招标文件内容。" }, 400);
      return sendJson(res, await analyze(payload.text, payload.source || "本地文件", payload.projectRef || {}));
    }
    if (req.method === "GET" && url.pathname === "/api/automation/status") return sendJson(res, automationStatus());
    if (req.method === "POST" && url.pathname === "/api/automation/search") return sendJson(res, { run: await runScheduledSearch("manual-search"), libraries: librarySnapshot() });
    if (req.method === "POST" && url.pathname === "/api/automation/run") {
      const run = await runAutomation("manual");
      const first = run.projects[0];
      const firstWorkspace = first ? workspaces.get(first.workspaceId) : null;
      const items = array(run.candidateProjectIds)
        .map((id) => catalog.find((item) => item.id === id))
        .filter(Boolean)
        .sort((a, b) => {
          const rank = matchSortValue(a) - matchSortValue(b);
          if (rank) return rank;
          return Number(b.matchScore || -1) - Number(a.matchScore || -1);
        });
      return sendJson(res, {
        run,
        items,
        libraries: librarySnapshot(),
        firstResult: firstWorkspace ? { engine: "规则抽取 + 规则匹配 + AI 信息匹配", ontology: `${ontology.meta.name} v${ontology.meta.version}`, workspace: firstWorkspace } : null,
      });
    }
    if (req.method === "GET" && url.pathname.startsWith("/api/workspaces/")) {
      const item = workspaces.get(decodeURIComponent(url.pathname.slice("/api/workspaces/".length)));
      return item ? sendJson(res, item) : sendJson(res, { error: "未找到投标工作台" }, 404);
    }
    if (req.method === "POST" && url.pathname.startsWith("/api/workspaces/") && url.pathname.endsWith("/regenerate-ai-draft")) {
      const workspaceId = decodeURIComponent(url.pathname.slice("/api/workspaces/".length, -"/regenerate-ai-draft".length));
      const item = workspaces.get(workspaceId);
      if (!item) return sendJson(res, { error: "未找到投标工作台" }, 404);
      const workspace = await regenerateAiDraft(item);
      const catalogItem = catalog.find((entry) => entry.id === workspace.project.source?.projectId);
      if (catalogItem) {
        catalogItem.responseStatus = workspace.aiDraft?.status === "完成" ? "AI 响应初稿已生成" : "AI 响应初稿待重试";
        catalogItem.responseGeneratedAt = workspace.responseGeneratedAt;
        catalogItem.workspaceId = workspace.bidWorkspaceId;
        catalogItem.draftVersion = workspace.draftVersion;
        persistCatalog();
      }
      return sendJson(res, {
        message: workspace.aiDraft?.status === "完成"
          ? "已重新调用大模型并生成响应文件草稿。"
          : "本次模型调用未成功，已保留规则草稿和真实错误信息。",
        generated: workspace.aiDraft?.status === "完成",
        error: workspace.aiDraft?.errorMessage || "",
        workspace,
        item: catalogItem || null,
      });
    }
    if (req.method === "POST" && url.pathname.startsWith("/api/workspaces/") && url.pathname.endsWith("/draft")) {
      const item = workspaces.get(decodeURIComponent(url.pathname.slice("/api/workspaces/".length, -"/draft".length)));
      if (!item) return sendJson(res, { error: "未找到投标工作台" }, 404);
      const payload = JSON.parse(await readBody(req));
      if (!payload.draft || payload.draft.trim().length < 20) return sendJson(res, { error: "响应文件草稿内容不能为空。" }, 400);
      item.draft = payload.draft; item.draftVersion += 1; item.workspaceStatus = "审核中";
      return sendJson(res, { workspace: item });
    }
    if (req.method === "POST" && url.pathname === "/api/export") {
      const payload = JSON.parse(await readBody(req));
      const item = workspaces.get(payload.workspaceId);
      if (!item) return sendJson(res, { error: "未找到投标工作台" }, 404);
      if (!item.reasoning.gate.canExport && payload.force !== true) return sendJson(res, { error: item.reasoning.gate.reason, gate: item.reasoning.gate }, 409);
      return sendJson(res, { message: "响应文件已生成", ...exportWorkspace(item, payload.force === true) });
    }
    if (req.method === "POST" && url.pathname === "/api/ask") {
      const payload = JSON.parse(await readBody(req)); const item = workspaces.get(payload.workspaceId);
      if (!item) return sendJson(res, { error: "未找到投标工作台" }, 404);
      return sendJson(res, answerWorkspace(item, String(payload.question || "")));
    }
    if (req.method === "POST" && url.pathname === "/api/agent/chat") {
      const payload = JSON.parse(await readBody(req));
      const message = String(payload.message || "").trim();
      if (!message) return sendJson(res, { error: "请输入要交给智能体的任务。" }, 400);
      if (/一键|自动化|全流程|开始运行|每日流程/.test(message)) {
        const run = await runAutomation("chat");
        const first = run.projects[0];
        const firstWorkspace = first ? workspaces.get(first.workspaceId) : null;
        return sendJson(res, {
          answer: `已完成一次“筛选 → 下载 → 信息匹配 → 响应输出”流程：候选 ${run.candidateCount} 个，完成 ${run.completedCount} 个，待人工复核 ${run.reviewCount} 个。`,
          action: "automation",
          run,
          firstResult: firstWorkspace ? { engine: "规则抽取 + 规则匹配 + AI 信息匹配", ontology: `${ontology.meta.name} v${ontology.meta.version}`, workspace: firstWorkspace } : null,
          sourceRefs: run.projects.map((item) => item.workspaceId),
        });
      }
      try {
        const result = await runPiTenderAgent(message);
        return sendJson(res, {
          answer: result.answer,
          action: "pi-agent",
          agentRun: result.agentRun,
          toolAudit: result.toolAudit,
          workspaces: result.workspaces,
          sourceRefs: result.toolAudit
            .map((entry) => entry.args?.workspaceId || entry.args?.projectId)
            .filter(Boolean),
        });
      } catch (piError) {
        const fallbackNote = `Pi 智能体暂不可用（${aiFallbackSummary(piError, "semantic")}），已切换为演示规则处理。`;
        const item = payload.workspaceId ? workspaces.get(payload.workspaceId) : null;
        if (item) return sendJson(res, { ...answerWorkspace(item, message), answer: `${fallbackNote} ${answerWorkspace(item, message).answer}`, action: "answer", agentFallback: { reason: String(piError?.message || "Pi 智能体运行失败") } });
        if (/资料库|知识库|公司资料|招标项目/.test(message)) {
          const summary = librarySnapshot().map((library) => `${library.name} ${library.documentCount} 份`).join("；");
          return sendJson(res, { answer: `${fallbackNote} 当前已接入：${summary}。资料会保存到 demo_v1/runtime/libraries，并在后续流程中作为可追溯依据。`, action: "answer", agentFallback: { reason: String(piError?.message || "Pi 智能体运行失败") }, sourceRefs: ["library-registry"] });
        }
        if (/模型|接口|大模型|API/.test(message)) {
          return sendJson(res, { answer: `${fallbackNote} 当前模型配置为 ${config.ai?.provider || "auto"} / ${config.ai?.model || "未设置"}，${config.ai?.enabled === false ? "未启用" : "已启用"}。可在“配置”中保存接口地址、模型和密钥。`, action: "answer", agentFallback: { reason: String(piError?.message || "Pi 智能体运行失败") }, sourceRefs: ["agent-config"] });
        }
        if (/定时|每天|每日/.test(message)) {
          const schedule = config.automation?.schedule || {};
          const action = schedule.action === "workflow" ? "执行搜索后的完整工作流" : "仅执行实时搜索并保存回执";
          return sendJson(res, { answer: `${fallbackNote} ${schedule.enabled ? `每日 ${schedule.time}（${schedule.timezone}）${action}。` : "每日定时任务当前未启用，可在智能体配置中打开。"}`, action: "answer", agentFallback: { reason: String(piError?.message || "Pi 智能体运行失败") }, sourceRefs: ["scheduler-config"] });
        }
        return sendJson(res, { answer: `${fallbackNote} 我可以帮你执行每日全流程、查看资料库状态、解释匹配风险，或读取当前响应文件工作台。`, action: "answer", agentFallback: { reason: String(piError?.message || "Pi 智能体运行失败") }, sourceRefs: ["ontology-agent"] });
      }
    }
    if (req.method === "POST" && url.pathname === "/api/ai/test") {
      const startedAt = Date.now();
      try {
        const result = await callConfiguredModel([
          { role: "system", content: "你是招投标智能体的模型连通性测试助手。请简洁回答。" },
          { role: "user", content: "请只回复：招投标智能体连接测试成功。" },
        ], { maxTokens: 80 });
        return sendJson(res, {
          ok: true,
          model: result.model,
          latencyMs: Date.now() - startedAt,
          answer: result.content,
          usage: result.usage,
        });
      } catch (error) {
        return sendJson(res, {
          ok: false,
          model: config.ai?.model || "",
          latencyMs: Date.now() - startedAt,
          message: aiFallbackSummary(error, "semantic"),
          detail: String(error?.message || "连接失败"),
        });
      }
    }
    if (req.method === "POST" && url.pathname === "/api/notifications/test") {
      const status = await sendWecomTestNotification();
      return sendJson(res, { ok: true, status, message: "企业微信测试提醒已发送，请在目标群中查看。" });
    }
    if (req.method === "POST" && url.pathname === "/api/config") {
      const payload = JSON.parse(await readBody(req));
      const nextSearch = payload.search === undefined ? config.search : normalizeSearchConfig(payload.search);
      const nextRetrieval = payload.retrieval === undefined ? config.retrieval : normalizeRetrievalConfig(payload.retrieval);
      config = { ...config, ...payload, search: nextSearch, retrieval: nextRetrieval, notifications: mergeNotificationConfig(config.notifications, payload.notifications), platformAccounts: payload.platformAccounts === undefined ? config.platformAccounts : mergePlatformAccounts(config.platformAccounts, payload.platformAccounts), scoreWeights: { ...config.scoreWeights, ...(payload.scoreWeights || {}) }, decisionThresholds: { ...config.decisionThresholds, ...(payload.decisionThresholds || {}) }, ai: { ...config.ai, ...(payload.ai || {}), apiKey: payload.ai?.apiKey || config.ai.apiKey }, automation: { ...config.automation, ...(payload.automation || {}), currentOnly: true, schedule: { ...config.automation.schedule, ...(payload.automation?.schedule || {}) }, monitoring: { ...config.automation.monitoring, ...(payload.automation?.monitoring || {}) } } };
      if (!/^\d{2}:\d{2}$/.test(config.automation.schedule.time)) config.automation.schedule.time = "09:00";
      if (!["search", "workflow"].includes(config.automation.schedule.action)) config.automation.schedule.action = "search";
      config.automation.monitoring.enabled = config.automation.monitoring.enabled !== false;
      config.automation.monitoring.intervalMinutes = Math.max(5, Math.min(1440, Number(config.automation.monitoring.intervalMinutes || 30)));
      if (payload.automation?.schedule || payload.automation?.monitoring) {
        schedulerState = { ...schedulerState, lastScheduledDate: null };
        scheduleNextMonitoringRun();
        persistSchedulerState();
      }
      fs.writeFileSync(configPath, JSON.stringify(config, null, 2), "utf8");
      return sendJson(res, { config: publicConfig(), platformAuth: platformAuthSnapshot() });
    }
    if (req.method === "GET") return serve(res, url.pathname, { inline: url.searchParams.get("preview") === "1" });
    return send(res, 404, "Not found");
  } catch (error) {
    return sendJson(res, { error: error.message || "服务处理失败" }, 500);
  }
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`招标智能体 v1 已启动：http://127.0.0.1:${PORT}`);
  setTimeout(() => {
    if (monitoringConfig().enabled && !schedulerState.monitoringLastRunAt) {
      schedulerState.monitoringNextRunAt = null;
      persistSchedulerState();
    }
    checkContinuousMonitoring();
  }, 1000).unref();
});
