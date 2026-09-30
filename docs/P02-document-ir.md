# P02 文档解析与 DocumentIR

> 状态：已完成，待阶段验收
> 日期：2026-09-19
> Python：项目虚拟环境中的 `python`

## 本阶段目标

在不破坏现有字典接口的前提下，为 PDF、DOCX、DOC、XLSX 和纯文本解析结果增加统一的 Pydantic `DocumentIR` 边界。后续章节识别、切分、分类、要求抽取、匹配和写标都消费这一份结构化解析产物。

## 已完成

- [x] 新增 `backend/app/tender_matching/document_models.py`。
- [x] 定义 `DocumentIR`、`DocumentPage`、`DocumentSegment`、`DocumentTable`、`DocumentSection`、`SourceLocator` 和 `ParseDiagnostics`。
- [x] 保留文件哈希、文档 ID、版本 ID、格式、MIME、全文、页、段、单元、表格、章节、元数据和 warning。
- [x] 新增 `parse_document_ir()` 统一解析入口。
- [x] 新增 `Store.extract_document_ir()` typed 入口。
- [x] 保持 `Store.extract_document_content()` 及现有 `parse_*()` 返回字典，旧调用方无需迁移。
- [x] 增加 `DocumentIR.model_dump(mode="json", by_alias=True)` 的 JSON 产物路径。
- [x] 在 `backend/requirements.txt` 显式声明 Pydantic 2 主版本范围。
- [x] 增加 DOCX 表格、PDF 页对象、旧接口兼容和 JSON round-trip 测试。

## 验证结果

### 单元和回归测试

```powershell
python -m pytest -q backend\tests
```

结果：`147 passed, 22 subtests passed`

### 编译检查

```powershell
python -m compileall -q backend scripts
```

结果：通过。

### 真实样本检查

本地样本目录中的 9 份文档全部成功转换为 `DocumentIR`（该样本集不随仓库提供）：

- 2 份 PDF：分别识别 45 页和 93 页，生成 1627 和 2702 个文本块，保留每个文本块的页码和 bbox。
- 5 份 DOCX 招标/响应文件：保留段落、表格、单元格和章节。
- 1 份旧版 DOC：保留 legacy-doc 解析结果。
- 文本型 PDF 的页面尺寸、块级定位和文本质量已进入 `DocumentIR`；空白/扫描页会记录 `isScan=true`、`ocrStatus=required` 和 warning。

## 当前边界

- `DocumentIR` 已经是新的 typed 边界，但旧 Store/API 仍返回字典，迁移会在后续阶段逐步进行。
- DOCX 暂无物理页码，模型允许 `pages=[]`，不伪造页码。
- 旧 `parse_pdf()` 仍使用 `pypdf`，保证旧接口稳定；新的 `parse_document_ir()` 优先使用 PyMuPDF 文本块适配器。
- 当前未安装 OCR 引擎；扫描页已被识别并进入待 OCR 状态，后续可接入可选 OCR 适配器。
- P02 不调用 LLM，不做章节分类、企业匹配、前端展示或 WorkBuddy 接入。

## 下一阶段 P03

重点改进章节树和大章节边界：

1. 为 PDF 增加稳定文本块和来源坐标的可选适配。
2. 重新验证 DOCX/DOC/PDF 的标题识别、父子层级和目录过滤。
3. 明确“标题只负责边界，分类另行处理”的职责边界。
4. 用真实样本统计章节召回、误识别和未分类内容比例。
