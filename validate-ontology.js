const fs = require("fs");
const path = require("path");

const file = process.argv[2] || path.join(__dirname, "ontology-governance.json");
const model = JSON.parse(fs.readFileSync(file, "utf8"));
const errors = [];
const warnings = [];

function assert(condition, message) {
  if (!condition) errors.push(message);
}

function unique(items, label) {
  const values = items.map((item) => typeof item === "string" ? item : item.id);
  const duplicates = values.filter((value, index) => values.indexOf(value) !== index);
  assert(duplicates.length === 0, `${label}存在重复标识：${[...new Set(duplicates)].join(", ")}`);
}

assert(model.meta?.manual?.version === "V1.0", "未声明与《本体数据治理手册 V1.0》的对齐关系");
assert(model.governanceProfile?.layers?.length === 3, "治理模型必须包含三层");
assert(model.logicalLibraries?.length === 5, "必须配置五个逻辑库");

const classes = model.classes || [];
const classIds = new Set(classes.map((item) => item.id));
unique(classes, "类");
unique(model.relations || [], "关系");
unique(model.businessRules || [], "规则");
unique(model.competencyQuestions || [], "能力问题");

for (const item of classes) {
  assert(item.id && item.label && item.definition, `类定义不完整：${item.id || "(未命名)"}`);
  assert(Array.isArray(item.identity) && item.identity.length > 0, `类缺少身份标识：${item.id}`);
  assert(Array.isArray(item.keyAttributes) && item.keyAttributes.length > 0, `类缺少关键属性：${item.id}`);
  assert(item.keyAttributes.length <= model.governanceProfile.qualityTargets.maxKeyAttributesPerClass,
    `类的关键属性超过治理阈值：${item.id}`);
  assert(["L1", "L2", "L3"].includes(item.layer), `类引用未知层：${item.id}`);
}

for (const relation of model.relations || []) {
  const endpoints = `${relation.from}|${relation.to}`;
  for (const endpoint of endpoints.split("|")) {
    const base = endpoint.split(".")[0].split("|")[0];
    if (!classIds.has(base) && !endpoint.includes("TenderProject.requirements[]") && !endpoint.includes("reviewTargetRef")) {
      warnings.push(`关系端点未对应独立类（允许为内嵌对象）：${relation.id} -> ${endpoint}`);
    }
  }
  assert(relation.verb && relation.cardinality && relation.path, `关系定义不完整：${relation.id}`);
}

const owners = [];
for (const item of classes) owners.push(...(item.semanticOwner || []).map((name) => [name, item.id]));
const ownerMap = new Map();
for (const [name, owner] of owners) {
  if (ownerMap.has(name)) errors.push(`AI 语义层重复归属：${name} -> ${ownerMap.get(name)}, ${owner}`);
  ownerMap.set(name, owner);
}

const requiredLibraries = ["招标企业库", "招标文件库", "招标库", "标书库", "标书模板库"];
for (const name of requiredLibraries) {
  assert(model.logicalLibraries.some((item) => item.name === name), `缺少逻辑库：${name}`);
}

const requiredRules = ["BR01", "BR02", "BR03", "BR04", "BR05", "BR06", "BR07", "BR08", "BR09"];
for (const id of requiredRules) assert(model.businessRules.some((item) => item.id === id), `缺少治理规则：${id}`);

if (warnings.length) {
  console.warn("WARNINGS");
  for (const warning of warnings) console.warn(`- ${warning}`);
}
if (errors.length) {
  console.error("ERRORS");
  for (const error of errors) console.error(`- ${error}`);
  process.exit(1);
}

console.log(`Ontology valid: ${path.basename(file)}`);
console.log(`Classes: ${classes.length}; Relations: ${(model.relations || []).length}; Libraries: ${model.logicalLibraries.length}; Rules: ${(model.businessRules || []).length}`);
