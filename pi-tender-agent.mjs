import { Agent } from "@earendil-works/pi-agent-core";
import { Type, createModels, createProvider } from "@earendil-works/pi-ai";
import { openAICompletionsApi } from "@earendil-works/pi-ai/api/openai-completions.lazy";

const MAX_TOOL_TEXT_LENGTH = 12000;

function compactJson(value) {
  return JSON.stringify(value, null, 2).slice(0, MAX_TOOL_TEXT_LENGTH);
}

function messageText(message) {
  if (!message || !Array.isArray(message.content)) return "";
  return message.content
    .filter((block) => block?.type === "text")
    .map((block) => block.text)
    .join("")
    .trim();
}

function agentModel(ai) {
  return {
    id: ai.model,
    name: `招标业务模型 ${ai.model}`,
    api: "openai-completions",
    provider: "tender-business-llm",
    baseUrl: ai.baseUrl,
    reasoning: false,
    input: ["text"],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
    contextWindow: 128000,
    maxTokens: 6000,
  };
}

function makeTool(name, label, description, parameters, execute) {
  return {
    name,
    label,
    description,
    parameters,
    executionMode: "sequential",
    async execute(toolCallId, params, signal, onUpdate) {
      if (signal?.aborted) throw new Error("智能体任务已取消。");
      onUpdate?.({
        content: [{ type: "text", text: `${label}执行中...` }],
        details: { toolCallId, status: "running" },
      });
      const details = await execute(params);
      return {
        content: [{ type: "text", text: compactJson(details) }],
        details,
      };
    },
  };
}

export async function runTenderAgent({ message, ai, tools, sessionId }) {
  if (!ai?.apiKey || !ai?.model || !ai?.baseUrl) {
    throw new Error("请先在配置中保存可用的大模型接口、模型 ID 和 API Key。");
  }

  const models = createModels();
  const model = agentModel(ai);
  models.setProvider(createProvider({
    id: model.provider,
    name: "招标业务模型接口",
    baseUrl: ai.baseUrl,
    auth: {
      apiKey: {
        name: "招标业务模型 API Key",
        resolve: async () => ({ auth: { apiKey: ai.apiKey } }),
      },
    },
    models: [model],
    api: openAICompletionsApi(),
  }));

  const toolAudit = [];
  const agentTools = [
    makeTool(
      "search_tenders",
      "搜索招标项目",
      "按关键词、地区和服务类型执行已配置网站的实时搜索。仅用于劳务派遣、安保、人力资源、后勤等服务项目。",
      Type.Object({
        keywords: Type.Optional(Type.String({ description: "搜索关键词，例如劳务派遣或安保" })),
        region: Type.Optional(Type.String({ description: "地区，例如河南省或濮阳市" })),
        serviceType: Type.Optional(Type.String({ description: "服务类型，例如劳务派遣或保安服务" })),
      }),
      (params) => tools.search({
        query: String(params.keywords || "").trim(),
        region: String(params.region || "").trim(),
        type: String(params.serviceType || "").trim(),
        currentOnly: true,
      }),
    ),
    makeTool(
      "list_candidate_projects",
      "查看候选项目",
      "读取当前招标项目库中与业务画像相关的候选项目，返回项目 ID、名称、地区、服务类型、截止时间和原公告链接。",
      Type.Object({
        limit: Type.Optional(Type.Number({ minimum: 1, maximum: 20, description: "最多返回的项目数量" })),
      }),
      (params) => tools.listCandidates(Number(params.limit || 8)),
    ),
    makeTool(
      "analyze_project",
      "信息匹配与响应草稿",
      "下载或读取一个招标项目，提取要求，与公司资质、业绩、人员资料进行匹配，并生成待人工审核的响应文件草稿。",
      Type.Object({
        projectId: Type.String({ description: "候选项目 ID" }),
      }),
      (params) => tools.analyzeProject(String(params.projectId || "").trim()),
    ),
    makeTool(
      "create_business_reminder",
      "创建业务提醒",
      "为已经完成信息匹配的项目创建提醒记录。当前仅记录页面提醒，不会向外部消息系统发送。",
      Type.Object({
        workspaceId: Type.String({ description: "信息匹配产生的工作台 ID" }),
        recipient: Type.Optional(Type.String({ description: "接收人，例如张经理" })),
        level: Type.Optional(Type.String({ description: "低、中或高" })),
        message: Type.Optional(Type.String({ description: "提醒说明" })),
      }),
      (params) => tools.createReminder({
        workspaceId: String(params.workspaceId || "").trim(),
        recipient: String(params.recipient || "业务人员").trim(),
        level: String(params.level || "中").trim(),
        message: String(params.message || "").trim(),
      }),
    ),
    makeTool(
      "export_response_docx",
      "导出响应文件",
      "用户明确要求导出时，生成 Word、Markdown 和 JSON 响应文件包。闸门未通过时仅能导出“待人工审核”的内部底稿，不得表述为可直接递交的正式文件。",
      Type.Object({
        workspaceId: Type.String({ description: "待导出的工作台 ID" }),
        confirmed: Type.Boolean({ description: "仅当用户已经明确要求导出时设为 true" }),
        reviewOnly: Type.Optional(Type.Boolean({ description: "存在风险时设为 true，仅导出待人工审核的内部底稿" })),
      }),
      (params) => {
        if (params.confirmed !== true) throw new Error("尚未获得明确导出确认，不能生成正式导出包。");
        return tools.exportResponse(String(params.workspaceId || "").trim(), params.reviewOnly === true);
      },
    ),
  ];

  const agent = new Agent({
    initialState: {
      systemPrompt: [
        "你是“招标智策”的招标业务智能体，负责在受控业务工具之间编排任务。",
        "业务范围仅限劳务派遣、安保、人力资源服务和后勤服务相关项目。",
        "优先调用工具获取事实，不得编造项目、资格、业绩、人员、提醒发送状态或文件导出结果。",
        "标准流程是：搜索或读取候选项目 -> 信息匹配 -> 对建议参与或人工评估项目创建提醒 -> 根据用户明确要求导出响应文件。",
        "当用户指定 projectId 并要求“信息匹配”时，必须调用 analyze_project，不能只给出文字建议。",
        "当用户指定 projectId 并要求“生成响应文件”或“导出响应文件”时，必须先调用 analyze_project，再对得到的 workspaceId 调用 export_response_docx，并将 confirmed 设为 true。",
        "资格结论和响应文件都必须标注需要人工审核；存在高风险、待补材料或导出闸门不通过时，必须将 reviewOnly 设为 true，并明确该文件仅为内部审核底稿、不可直接递交。",
        "提醒工具目前只创建系统记录，不可表述为企业微信、钉钉、短信或邮件已经发送。",
        "最终答复用简洁中文列出已完成动作、项目结论、风险或待人工确认事项，以及可下载文件（如有）。",
      ].join("\n"),
      model,
      tools: agentTools,
      thinkingLevel: "low",
      messages: [],
    },
    streamFn: models.streamSimple.bind(models),
    getApiKey: () => ai.apiKey,
    sessionId,
    toolExecution: "sequential",
    beforeToolCall: async ({ toolCall }) => {
      if (!agentTools.some((tool) => tool.name === toolCall.name)) {
        return { block: true, reason: "该工具未被授权给招标业务智能体。", terminate: true };
      }
      return undefined;
    },
  });

  agent.subscribe((event) => {
    if (event.type === "tool_execution_start") {
      toolAudit.push({ tool: event.toolName, args: event.args, status: "started", at: new Date().toISOString() });
    }
    if (event.type === "tool_execution_end") {
      const item = toolAudit.findLast((entry) => entry.tool === event.toolName && entry.status === "started");
      if (item) {
        item.status = event.isError ? "failed" : "completed";
        item.completedAt = new Date().toISOString();
        item.error = event.isError ? messageText({ content: event.result?.content || [] }) : "";
        if (!event.isError && event.result?.details) item.result = event.result.details;
      }
    }
  });

  await agent.prompt(message);
  const assistantMessage = [...agent.state.messages].reverse().find((item) => item.role === "assistant");
  const answer = messageText(assistantMessage);
  if (!answer) {
    throw new Error(agent.state.errorMessage || "Pi 智能体未返回可用答复。");
  }
  return {
    answer,
    toolAudit,
    transcript: agent.state.messages,
    model: ai.model,
  };
}
