(() => {
  "use strict";

  const TODAY = new Date("2026-09-01T00:00:00");
  const ONTOLOGY_LABEL = "公司内部投标专员智能体本体治理版 v1.0.0";
  const company = {
    company: "濮阳人才服务有限公司",
    service_regions: ["河南省", "濮阳市"],
    qualifications: ["营业执照", "人力资源服务许可证", "劳务派遣经营许可证"],
    performances: [
      { name: "濮阳市某学校劳务派遣服务项目", industry: "事业单位", type: "劳务派遣", region: "濮阳市" },
      { name: "某市人民医院劳务派遣服务项目", industry: "医院", type: "劳务派遣", region: "河南省" }
    ],
    personnel: [
      { role: "项目经理", count: 2, experience_years: 5 },
      { role: "招聘专员", count: 8, experience_years: 2 },
      { role: "现场管理人员", count: 3, experience_years: 3 }
    ]
  };

  const sampleText = `项目名称：濮阳市某医院2026年度劳务派遣服务项目
项目编号：PY-HR-2026-018
招标人：濮阳市某医院
服务类型：劳务派遣
服务地点：河南省濮阳市
预算金额：人民币300万元/年
服务期限：12个月
投标截止时间：2026-09-08 09:30

一、投标人资格要求
1. 具有独立承担民事责任的能力，提供有效营业执照。
2. 具有有效的人力资源服务许可证和劳务派遣经营许可证。
3. 近三年承担过不少于3个类似医院或事业单位劳务派遣项目。
4. 拟派项目经理1名，具有3年以上人力资源服务项目管理经验。

二、服务要求
1. 负责人员招聘、入职、考勤、培训、薪酬核算和日常管理。
2. 提供驻场服务团队，建立投诉处理和应急响应机制。
3. 每月提交人员考勤、社保和服务质量报告。

三、评审与投标文件要求
1. 提交项目理解、人员配置、服务方案、质量保障和应急预案。
2. 未按要求提供资格证明材料的，视为无效投标。
3. 投标文件须在截止时间前递交，逾期递交将被否决。`;

  const catalog = [
    {
      id: "sample-hospital-labor",
      title: "医院劳务派遣服务项目（完整招标样本）",
      noticeType: "Demo 完整招标文件",
      source: "本地脱敏示例",
      publishedDate: "2026-09-01",
      analysisText: sampleText
    },
    {
      id: "sample-security",
      title: "医院保安服务项目（不匹配样本）",
      noticeType: "Demo 风险识别样本",
      source: "本地脱敏示例",
      publishedDate: "2026-09-01",
      analysisText: `项目名称：某医院保安服务项目
项目编号：DEMO-SEC-001
招标人：某市人民医院
服务类型：保安服务
服务地点：河南省濮阳市
预算金额：180万元/年
服务期限：12个月
投标截止时间：2026-09-20 09:00

一、资格要求
1. 提供有效营业执照和保安服务许可证。
2. 近三年承担过不少于2个医院保安服务项目。
3. 拟派项目经理1名，具有3年以上安保项目管理经验。

二、投标文件要求
1. 提交安保服务方案、人员配置、巡查方案和应急预案。
2. 未提供资格证明材料的，视为无效投标。`
    },
    {
      id: "sample-cleaning",
      title: "园区保洁服务项目（地域风险样本）",
      noticeType: "Demo 人工评估样本",
      source: "本地脱敏示例",
      publishedDate: "2026-09-01",
      analysisText: `项目名称：某产业园保洁服务项目
项目编号：DEMO-CLEAN-002
招标人：某产业园管理委员会
服务类型：保洁服务
服务地点：山东省济南市
预算金额：120万元/年
服务期限：24个月
投标截止时间：2026-09-25 09:00

一、资格要求
1. 提供有效营业执照。
2. 近三年承担过不少于2个类似保洁项目。
3. 拟派现场管理人员，具有3年以上项目管理经验。

二、投标文件要求
1. 提交服务方案、质量保障和应急预案。
2. 逾期递交将被否决。`
    }
  ];

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

  function extractRequirements(text) {
    const qualificationLines = linesContaining(text, ["营业执照", "许可证", "资质", "资格"]);
    const performanceLines = linesContaining(text, ["类似", "业绩"]);
    const personnelLines = linesContaining(text, ["项目经理", "人员", "团队"]);
    const technicalLines = linesContaining(text, ["服务方案", "应急", "质量", "培训", "考勤"]);
    const rejectionLines = linesContaining(text, ["废标", "无效投标", "否决", "逾期"]);
    const qualifications = ["营业执照", "人力资源服务许可证", "劳务派遣经营许可证", "保安服务许可证"]
      .filter((item) => text.includes(item));

    return {
      qualifications: qualifications.length ? qualifications : qualificationLines,
      performances: performanceLines.length ? [{
        text: performanceLines.join("；"),
        required_count: extractPerformanceCount(text)
      }] : [],
      personnel: personnelLines.length ? [{
        text: personnelLines.join("；"),
        role: text.includes("项目经理") ? "项目经理" : "项目人员"
      }] : [],
      technical: [...new Set(technicalLines)],
      rejection: [...new Set(rejectionLines)]
    };
  }

  function parseTender(text) {
    return {
      project_name: firstMatch(text, /项目名称[:：]\s*([^\r\n]+)/),
      project_code: firstMatch(text, /项目编号[:：]\s*([^\r\n]+)/),
      tenderer: firstMatch(text, /招标人[:：]\s*([^\r\n]+)/),
      service_type: firstMatch(text, /(?:服务类型|项目类型)[:：]\s*([^\r\n]+)/, detectType(text)),
      service_location: firstMatch(text, /服务地点[:：]\s*([^\r\n]+)/),
      budget: firstMatch(text, /(?:预算金额|最高限价)[:：]\s*([^\r\n]+)/),
      service_period: firstMatch(text, /(?:服务期限|服务期)[:：]\s*([^\r\n]+)/),
      bid_deadline: firstMatch(text, /投标截止时间[:：]\s*([^\r\n]+)/),
      requirements: extractRequirements(text)
    };
  }

  function daysUntil(value) {
    const normalized = String(value).replace(/年/g, "-").replace(/月/g, "-").replace(/日/g, "").replace(/\//g, "-");
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : Math.ceil((date.getTime() - TODAY.getTime()) / 86400000);
  }

  function reasonAbout(project) {
    const rows = [];
    const qualificationMap = {
      "营业执照": ["营业执照"],
      "人力资源服务许可证": ["人力资源服务许可证"],
      "劳务派遣经营许可证": ["劳务派遣经营许可证"],
      "保安服务许可证": ["保安服务许可证"]
    };
    for (const requirement of project.requirements.qualifications) {
      const possible = qualificationMap[requirement] || [requirement];
      const evidence = possible.find((item) => company.qualifications.includes(item));
      rows.push({
        kind: "资质",
        requirement,
        status: evidence ? "满足" : "缺失",
        evidence: evidence || "企业能力库未找到对应材料",
        risk: evidence ? "低" : "高"
      });
    }

    const performanceRequirement = project.requirements.performances[0];
    if (performanceRequirement) {
      const required = performanceRequirement.required_count || 0;
      const matching = company.performances.filter((item) => item.type === project.service_type || project.service_type === "人力资源服务");
      rows.push({
        kind: "业绩",
        requirement: performanceRequirement.text,
        status: required && matching.length < required ? "部分满足" : "满足",
        evidence: `企业能力库匹配到 ${matching.length} 个相关业绩，要求 ${required || "未识别"} 个`,
        risk: required && matching.length < required ? "高" : "中"
      });
    }

    const personnelRequirement = project.requirements.personnel[0];
    if (personnelRequirement) {
      const found = company.personnel.find((item) => item.role === personnelRequirement.role);
      const experience = personnelRequirement.text.match(/(\d+)\s*年以上/);
      const satisfied = found && (!experience || found.experience_years >= Number(experience[1]));
      rows.push({
        kind: "人员",
        requirement: personnelRequirement.text,
        status: satisfied ? "满足" : "部分满足",
        evidence: found ? `${found.role} ${found.count}名，经验${found.experience_years}年` : "企业能力库未找到对应人员",
        risk: satisfied ? "低" : "中"
      });
    }

    const regionMatch = company.service_regions.some((region) => project.service_location.includes(region));
    rows.push({
      kind: "地域",
      requirement: project.service_location,
      status: regionMatch ? "满足" : "待确认",
      evidence: `企业服务区域：${company.service_regions.join("、")}`,
      risk: regionMatch ? "低" : "中"
    });

    let score = 0;
    score += rows.filter((row) => row.kind === "资质" && row.status === "满足").length * 12;
    score += rows.filter((row) => row.kind === "业绩" && row.status === "满足").length * 20;
    score += rows.filter((row) => row.kind === "人员" && row.status === "满足").length * 12;
    score += regionMatch ? 10 : 0;
    score += project.service_type === "劳务派遣" ? 16 : 8;
    const remainingDays = daysUntil(project.bid_deadline);
    score += remainingDays === null || remainingDays >= 7 ? 10 : remainingDays >= 3 ? 5 : -5;
    score = Math.max(0, Math.min(100, score));

    const risks = [];
    if (rows.some((row) => row.status === "缺失")) risks.push({ level: "高", title: "存在资质缺失", detail: "缺失资质可能直接导致资格审查不通过。" });
    if (rows.some((row) => row.kind === "业绩" && row.status !== "满足")) risks.push({ level: "高", title: "类似业绩数量可能不足", detail: "需要补充符合行业和服务类型的业绩证明。" });
    if (project.requirements.rejection.length) risks.push({ level: "高", title: "存在否决或废标条款", detail: project.requirements.rejection.join("；") });
    if (remainingDays !== null && remainingDays < 7) risks.push({ level: "中", title: "准备时间较短", detail: `距离投标截止约 ${remainingDays} 天，需要尽快确认是否参与。` });
    if (!risks.length) risks.push({ level: "低", title: "暂未发现明显高风险", detail: "仍需人工核对原始招标文件和证明材料。" });

    return {
      rows,
      risks,
      score,
      decision: score >= 80 ? "建议参与" : score >= 60 ? "人工评估" : "不建议参与",
      remaining_days: remainingDays
    };
  }

  function buildGraph(project, reasoning) {
    const entities = [
      { id: "project-1", class: "Project", name: project.project_name },
      { id: "company-1", class: "Company", name: company.company },
      ...project.requirements.qualifications.map((name, index) => ({ id: `qualification-${index}`, class: "Qualification", name })),
      ...project.requirements.performances.map((item, index) => ({ id: `performance-${index}`, class: "Performance", name: item.text })),
      ...project.requirements.personnel.map((item, index) => ({ id: `personnel-${index}`, class: "Personnel", name: item.text })),
      ...reasoning.risks.map((item, index) => ({ id: `risk-${index}`, class: "Risk", name: item.title }))
    ];
    const triples = [];
    for (const entity of entities.filter((item) => item.class === "Qualification")) {
      triples.push({ subject: project.project_name, relation: "requiresQualification", object: entity.name });
      if (company.qualifications.includes(entity.name)) triples.push({ subject: company.company, relation: "ownsQualification", object: entity.name });
    }
    for (const entity of entities.filter((item) => item.class === "Performance")) triples.push({ subject: project.project_name, relation: "requiresPerformance", object: entity.name });
    for (const entity of entities.filter((item) => item.class === "Personnel")) triples.push({ subject: project.project_name, relation: "requiresPersonnel", object: entity.name });
    for (const entity of entities.filter((item) => item.class === "Risk")) triples.push({ subject: project.project_name, relation: "hasRisk", object: entity.name });
    triples.push({ subject: project.project_name, relation: "hasDecision", object: reasoning.decision });
    return { entities, triples };
  }

  function buildDraft(project, reasoning) {
    const missing = reasoning.rows.filter((row) => row.status !== "满足").map((row) => row.requirement);
    return `# ${project.project_name}

## 一、项目理解
本项目为${project.service_location}的${project.service_type}项目，服务期限为${project.service_period}。我方将围绕人员招聘、入职管理、考勤管理、薪酬与社保协同、服务质量和应急响应建立项目服务机制。

## 二、人员配置方案
我方拟配置项目经理、招聘及现场管理人员，建立项目负责人统一协调、岗位人员分工负责的服务架构。具体人员名单、证书和社保证明将在投标文件定稿前根据招标文件要求补充并由业务负责人审核。

## 三、服务质量保障方案
围绕招聘、入职、考勤、薪酬核算、投诉处理和月度报告建立闭环管理流程，按月形成服务质量记录，出现异常时由项目经理负责升级处理。

## 四、应急响应方案
针对人员临时缺岗、投诉、考勤异常和突发用工风险，建立事件登记、责任人分派、限时处理和复盘归档机制。

## 五、待补充和人工确认
${missing.length ? missing.map((item) => `- ${item}`).join("\n") : "- 暂未发现结构化缺失项，但仍需人工核对原文和附件。"}

> 本初稿由本体关系和规则分析生成，仅可作为投标文件编制底稿，不能直接提交。`;
  }

  function analyzeOffline(text) {
    const project = parseTender(text);
    const reasoning = reasonAbout(project);
    return {
      engine: "浏览器离线规则抽取 + 本体规则",
      ontology: ONTOLOGY_LABEL,
      project,
      reasoning,
      graph: buildGraph(project, reasoning),
      draft: buildDraft(project, reasoning),
      generated_at: new Date().toISOString()
    };
  }

  window.TenderDemo = { sampleText, catalog, analyzeOffline };
})();
