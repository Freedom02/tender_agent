# 招投标智能体 v2

面向企业业务员和投标团队的招标项目发现、企业资料匹配和响应文件辅助工作台。

当前仓库是可运行 Demo，新的运行入口为：

- 前端：Vue 3 + TypeScript + Vite + Element Plus
- 后端：Python + FastAPI

## 文档

- [项目文件清单](docs/PROJECT_FILE_CATALOG.md)：目录、逐文件职责、依赖关系和修改边界
- [开发文档](docs/DEVELOPMENT_GUIDE.md)：启动方式、接口、数据流、三层匹配架构和分阶段计划
- [UI设计规范](UI设计规范.md)
- [模块协作说明](模块协作说明.md)

## 目录

- `frontend/`：前端工作台
- `backend/`：FastAPI 后端
- `runtime/`：运行时数据、上传文件和导出文件，不提交 Git
- `company-profile.json`：脱敏企业能力样例
- `ontology-governance.json`：招投标本体与治理规则
- `demo-data/`：脱敏演示项目数据
- `docs/`：项目文件清单和开发文档

## 启动方式

### 1. 后端

```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8793
```

### 2. 前端

另开一个 PowerShell：

```powershell
cd frontend
npm install
npm run dev
```

前端默认访问 `http://127.0.0.1:5173`，并通过 Vite 代理访问后端。

## 当前 Demo 功能

- 招标项目检索、筛选和公开网站监控
- 自定义 HTML/JSON 网站基础抓取
- 本地 TXT、PDF、DOCX 文件导入和文本分析
- 资质、业绩、人员和地域的演示匹配
- 匹配评分、风险提示和投标建议
- 标书响应底稿编辑与保存
- Markdown、JSON、DOCX 响应底稿导出
- 企业资料库文件保存和预览
- 站内提醒、企业微信 Webhook 测试
- 配置保存、定时监控和智能体问答入口

## 重要限制

当前 Demo 尚未实现 OCR、Excel/图片完整解析、条款级证据页码、多公司数据隔离和真正的三层匹配引擎。预置网站和项目数据主要用于演示，必须回到原始网站核验；系统不做自动登录、验证码处理、付费文件获取、自动报名或自动提交投标。

“下载项目”当前保存的是公告文本快照，不应理解为自动下载完整招标文件。匹配分数和建议只用于辅助业务判断，不能替代人工、法务或项目负责人审核。

## 验证命令

```powershell
git diff --check
python -m compileall backend
cd frontend
npm run build
```
