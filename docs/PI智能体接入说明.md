# Pi 智能体接入说明

> 目标：在保留 Dify 接入代码的同时，先用本地 Pi 智能体跑通“上传招标文件 → 智能体解析 → 前端展示目录和条款 → 章节撰写”的链路。

## 1. 总体方案

当前后端已经支持通过环境变量切换智能体提供方：

```env
AGENT_PROVIDER=pi
```

切到 `pi` 后，原有前端入口不变：

```text
前端上传招标文件
  -> FastAPI 保存文件
  -> 本地解析 PDF/DOCX/TXT 为文本
  -> 调用 Pi RPC 智能体
  -> 标准化为 ParseResult
  -> 物化为项目、工作区、fineReview、bidDocument.plan
  -> 前端展示并进入目录编辑
```

Dify 的配置和代码仍保留，后续 Docker 或云端 Dify 准备好后，可以改回：

```env
AGENT_PROVIDER=dify
```

## 2. 安装 Pi

Pi 走 Node/npm，不需要 Docker。确认本机已有：

```powershell
node --version
npm --version
```

然后安装 Pi CLI。推荐先使用项目本地安装，避免污染全局环境：

```powershell
cd C:\Users\乔远\Desktop\tender_agent
npm install --save-dev @earendil-works/pi-coding-agent
```

如果希望全局命令可用：

```powershell
npm install -g @earendil-works/pi-coding-agent
```

如果 npm cache 权限报错，可先指定项目内缓存：

```powershell
npm config set cache C:\Users\乔远\Desktop\tender_agent\.npm-cache
```

## 3. 配置环境变量

在项目根目录 `.env` 中增加或修改：

```env
AGENT_PROVIDER=pi

PI_ENABLED=true
PI_COMMAND=pi
PI_PROVIDER=
PI_MODEL=
PI_API_KEY=
PI_API_TIMEOUT=180
PI_WORKING_DIR=C:\Users\乔远\Desktop\tender_agent
```

如果使用项目本地安装而不是全局安装，可以把 `PI_COMMAND` 指向本地可执行文件：

```env
PI_COMMAND=C:\Users\乔远\Desktop\tender_agent\node_modules\.bin\pi.cmd
```

`PI_PROVIDER`、`PI_MODEL`、`PI_API_KEY` 是否需要填写取决于你用的 Pi 模型供应商配置。若 Pi 已经在自身配置中保存了模型凭据，可以先留空。

## 4. 后端使用的 Pi 任务

### 4.1 招标文件解析

后端会把上传文件先解析为文本，再要求 Pi 只输出 JSON：

```json
{
  "projectMeta": {},
  "outline": [],
  "requirements": [],
  "evaluationItems": [],
  "riskItems": [],
  "warnings": []
}
```

该结果会继续复用现有 `cloud_agent.normalizers.normalize_parse_result` 和 `materializer`。

### 4.2 历史工作区目录生成

当 `AGENT_PROVIDER=pi` 时，用户在历史工作区点击“生成目录”，后端会优先调用 Pi 根据以下输入重新规划标书目录：

```text
project
fineReview
reasoning.cloudParse
project.document.sections
existing bidDocument.plan
```

Pi 只输出目录结构，不生成正文：

```json
{
  "plan": [
    {
      "sectionId": "t-02",
      "title": "技术方案",
      "source": "ai",
      "kind": "technical",
      "requirementIds": ["req-001"]
    }
  ],
  "warnings": []
}
```

如果 Pi 返回无效目录或调用失败，后端会自动回退到原来的本地目录生成逻辑。

### 4.3 章节撰写

当章节为：

```text
kind = technical
```

或：

```text
source = ai
```

后端会优先调用 Pi 章节撰写。Pi 需要返回：

```json
{
  "sectionId": "t-02",
  "title": "技术方案",
  "content": "Markdown 正文",
  "sourceRefs": [],
  "status": "generated",
  "warnings": []
}
```

如果 Pi 调用失败，后端会自动回退到现有本地章节生成逻辑，并在前端章节旁显示“本地回退”。

## 5. 联调步骤

1. 配置 `.env`：

```env
AGENT_PROVIDER=pi
PI_COMMAND=pi
```

2. 重启后端：

```powershell
cd C:\Users\乔远\Desktop\tender_agent
backend\.venv\Scripts\python.exe backend\run.py
```

3. 启动前端：

```powershell
cd C:\Users\乔远\Desktop\tender_agent\frontend
npm run dev
```

4. 打开：

```text
http://127.0.0.1:5173/
```

5. 在标书撰写页上传招标文件。

6. 等待任务状态到“目录已生成”。

7. 进入正文编辑，点击章节的“自动撰写 / 一键填充”。

8. 若章节显示“云端智能体”，表示 Pi 通道已成功生成。

## 6. 常见问题

### 未找到 Pi 命令

报错：

```text
未找到 Pi 命令
```

处理：

```powershell
npm install -g @earendil-works/pi-coding-agent
```

或把 `.env` 的 `PI_COMMAND` 指向：

```env
PI_COMMAND=C:\Users\乔远\Desktop\tender_agent\node_modules\.bin\pi.cmd
```

### Pi 返回内容不是合法 JSON

说明模型输出了说明文字或 Markdown 代码块。后端已经会容忍简单代码块，但如果仍失败，需要加强 Pi 模型/提示词的 JSON 约束。

### 章节没有走 Pi

检查：

```env
AGENT_PROVIDER=pi
PI_ENABLED=true
```

并确认章节满足：

```text
kind=technical 或 source=ai
```

### Dify 后续还能用吗

可以。改回：

```env
AGENT_PROVIDER=dify
```

并配置：

```env
DIFY_PARSE_WORKFLOW_KEY=
DIFY_SECTION_WORKFLOW_KEY=
```
