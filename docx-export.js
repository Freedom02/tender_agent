const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const REL = "http://schemas.openxmlformats.org/package/2006/relationships";
const CT = "http://schemas.openxmlformats.org/package/2006/content-types";
const CONTENT_WIDTH = 9360;

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

function cleanInline(value) {
  return String(value ?? "")
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/__(.+?)__/g, "$1")
    .replace(/`(.+?)`/g, "$1")
    .replace(/<br\s*\/?>/gi, "\n")
    .trim();
}

function run(text, options = {}) {
  const properties = ['<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei" w:cs="Calibri"/>'];
  if (options.bold) properties.push("<w:b/>");
  if (options.italic) properties.push("<w:i/>");
  if (options.color) properties.push(`<w:color w:val="${options.color}"/>`);
  if (options.size) properties.push(`<w:sz w:val="${options.size * 2}"/><w:szCs w:val="${options.size * 2}"/>`);
  const lines = String(text ?? "").split("\n");
  const body = lines.map((line, index) => `${index ? "<w:br/>" : ""}<w:t xml:space="preserve">${esc(line)}</w:t>`).join("");
  return `<w:r><w:rPr>${properties.join("")}</w:rPr>${body}</w:r>`;
}

function pageFieldRun() {
  return '<w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="separate"/></w:r><w:r><w:t>1</w:t></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r>';
}

function paragraph(text = "", style = "Normal", options = {}) {
  const props = [`<w:pStyle w:val="${style}"/>`];
  if (options.align) props.push(`<w:jc w:val="${options.align}"/>`);
  if (options.keepNext) props.push("<w:keepNext/>");
  if (options.pageBreakBefore) props.push("<w:pageBreakBefore/>");
  if (options.numId) props.push(`<w:numPr><w:ilvl w:val="0"/><w:numId w:val="${options.numId}"/></w:numPr>`);
  if (options.indent) props.push(`<w:ind w:left="${options.indent.left}" w:hanging="${options.indent.hanging || 0}"/>`);
  if (options.shading) props.push(`<w:shd w:val="clear" w:color="auto" w:fill="${options.shading}"/>`);
  const contents = options.raw ? String(text) : run(cleanInline(text), options);
  return `<w:p><w:pPr>${props.join("")}</w:pPr>${contents || run("")}</w:p>`;
}

function tableCell(text, width, options = {}) {
  const shading = options.header ? '<w:shd w:val="clear" w:color="auto" w:fill="F2F4F7"/>' : "";
  const content = String(text ?? "").split(/\n+/).filter(Boolean);
  const paragraphs = content.length
    ? content.map((line) => paragraph(line, "TableText", { bold: options.header, align: options.align })).join("")
    : paragraph("", "TableText");
  return `<w:tc><w:tcPr><w:tcW w:w="${width}" w:type="dxa"/>${shading}<w:tcMar><w:top w:w="80" w:type="dxa"/><w:start w:w="120" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/><w:end w:w="120" w:type="dxa"/></w:tcMar><w:vAlign w:val="center"/></w:tcPr>${paragraphs}</w:tc>`;
}

function calculateWidths(headers) {
  const weights = headers.map((header) => Math.max(1, Math.min(4, cleanInline(header).replace(/[^\u4e00-\u9fff]/g, "").length || cleanInline(header).length / 2 || 1)));
  const total = weights.reduce((sum, value) => sum + value, 0);
  const widths = weights.map((weight) => Math.max(1100, Math.floor(CONTENT_WIDTH * weight / total)));
  widths[widths.length - 1] += CONTENT_WIDTH - widths.reduce((sum, value) => sum + value, 0);
  return widths;
}

function docxTable(rows) {
  const columnCount = Math.max(...rows.map((row) => row.length), 1);
  const normalizedRows = rows.map((row) => Array.from({ length: columnCount }, (_, index) => cleanInline(row[index] || "")));
  const widths = calculateWidths(normalizedRows[0]);
  const grid = widths.map((width) => `<w:gridCol w:w="${width}"/>`).join("");
  const border = '<w:top w:val="single" w:sz="4" w:space="0" w:color="AAB4C3"/><w:left w:val="single" w:sz="4" w:space="0" w:color="AAB4C3"/><w:bottom w:val="single" w:sz="4" w:space="0" w:color="AAB4C3"/><w:right w:val="single" w:sz="4" w:space="0" w:color="AAB4C3"/><w:insideH w:val="single" w:sz="4" w:space="0" w:color="D7DEE8"/><w:insideV w:val="single" w:sz="4" w:space="0" w:color="D7DEE8"/>';
  const body = normalizedRows.map((row, rowIndex) => {
    const cells = row.map((cell, index) => tableCell(cell, widths[index], { header: rowIndex === 0 })).join("");
    return `<w:tr>${rowIndex === 0 ? "<w:trPr><w:tblHeader/></w:trPr>" : ""}${cells}</w:tr>`;
  }).join("");
  return `<w:tbl><w:tblPr><w:tblW w:w="${CONTENT_WIDTH}" w:type="dxa"/><w:tblInd w:w="120" w:type="dxa"/><w:tblLayout w:type="fixed"/><w:tblBorders>${border}</w:tblBorders><w:tblCellMar><w:top w:w="80" w:type="dxa"/><w:start w:w="120" w:type="dxa"/><w:bottom w:w="80" w:type="dxa"/><w:end w:w="120" w:type="dxa"/></w:tblCellMar></w:tblPr><w:tblGrid>${grid}</w:tblGrid>${body}</w:tbl>`;
}

function splitTableRow(line) {
  return String(line).trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function isTableDivider(line) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function markdownToXml(markdown, meta = {}) {
  const lines = String(markdown || "").replace(/\r\n/g, "\n").split("\n");
  const parts = [];
  let index = 0;
  let bodyLines = [];
  const flushBody = () => {
    const text = bodyLines.map((line) => line.trim()).filter(Boolean).join("\n");
    if (text) parts.push(paragraph(text, "Normal"));
    bodyLines = [];
  };

  while (index < lines.length) {
    const line = lines[index].trim();
    const next = lines[index + 1] || "";
    if (!line) {
      flushBody();
      index += 1;
      continue;
    }
    if (line.includes("|") && isTableDivider(next)) {
      flushBody();
      const tableRows = [splitTableRow(line)];
      index += 2;
      while (index < lines.length && lines[index].trim().includes("|")) {
        tableRows.push(splitTableRow(lines[index]));
        index += 1;
      }
      parts.push(docxTable(tableRows));
      continue;
    }
    const headingMatch = line.match(/^(#{1,4})\s+(.+)$/);
    if (headingMatch) {
      flushBody();
      const level = headingMatch[1].length;
      const text = cleanInline(headingMatch[2]);
      if (!(level === 1 && cleanInline(meta.title) === text)) parts.push(paragraph(text, `Heading${Math.min(level, 3)}`, { keepNext: true }));
      index += 1;
      continue;
    }
    if (/^>\s*/.test(line)) {
      flushBody();
      const quoteLines = [];
      while (index < lines.length && /^>\s*/.test(lines[index].trim())) {
        quoteLines.push(lines[index].trim().replace(/^>\s*/, "").replace(/\s{2,}$/, ""));
        index += 1;
      }
      parts.push(paragraph(quoteLines.join("\n"), "Notice", { shading: "F4F6F9" }));
      continue;
    }
    const bullet = line.match(/^[-*]\s+(.+)$/);
    if (bullet) {
      flushBody();
      parts.push(paragraph(bullet[1], "ListParagraph", { numId: 2 }));
      index += 1;
      continue;
    }
    const numbered = line.match(/^\d+[.)]\s+(.+)$/);
    if (numbered) {
      flushBody();
      parts.push(paragraph(numbered[1], "ListParagraph", { numId: 1 }));
      index += 1;
      continue;
    }
    if (/^---+$/.test(line)) {
      flushBody();
      index += 1;
      continue;
    }
    bodyLines.push(line);
    index += 1;
  }
  flushBody();
  return parts.join("");
}

function stylesXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="${W}">
  <w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei" w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:color w:val="182433"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="160" w:line="320" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="22"/></w:rPr><w:pPr><w:jc w:val="both"/><w:spacing w:after="160" w:line="320" w:lineRule="auto"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="44"/><w:b/><w:color w:val="0B2545"/></w:rPr><w:pPr><w:jc w:val="center"/><w:spacing w:before="1440" w:after="240" w:line="320"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="CoverEyebrow"><w:name w:val="Cover Eyebrow"/><w:basedOn w:val="Normal"/><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="24"/><w:b/><w:color w:val="B7791F"/></w:rPr><w:pPr><w:jc w:val="center"/><w:spacing w:before="1180" w:after="220"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Subtitle"><w:name w:val="Subtitle"/><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="24"/><w:color w:val="526275"/></w:rPr><w:pPr><w:jc w:val="center"/><w:spacing w:after="180"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="32"/><w:b/><w:color w:val="1D4E89"/></w:rPr><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="200" w:line="320"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="26"/><w:b/><w:color w:val="2E74B5"/></w:rPr><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="140" w:line="320"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:qFormat/><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei"/><w:sz w:val="24"/><w:b/><w:color w:val="1F4D78"/></w:rPr><w:pPr><w:keepNext/><w:spacing w:before="160" w:after="100" w:line="320"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:after="80" w:line="290" w:lineRule="auto"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="Notice"><w:name w:val="Notice"/><w:basedOn w:val="Normal"/><w:rPr><w:color w:val="526275"/><w:sz w:val="20"/></w:rPr><w:pPr><w:spacing w:before="80" w:after="120" w:line="280" w:lineRule="auto"/><w:ind w:left="180" w:right="180"/></w:pPr></w:style>
  <w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/><w:rPr><w:sz w:val="20"/></w:rPr><w:pPr><w:jc w:val="left"/><w:spacing w:after="0" w:line="280" w:lineRule="auto"/></w:pPr></w:style>
</w:styles>`;
}

function numberingXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:numbering xmlns:w="${W}">
  <w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="decimal"/><w:lvlText w:val="%1."/><w:lvlJc w:val="left"/><w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
  <w:abstractNum w:abstractNumId="1"><w:multiLevelType w:val="singleLevel"/><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="&#8226;"/><w:lvlJc w:val="left"/><w:rPr><w:rFonts w:ascii="Symbol" w:hAnsi="Symbol"/></w:rPr><w:pPr><w:tabs><w:tab w:val="num" w:pos="720"/></w:tabs><w:ind w:left="720" w:hanging="360"/></w:pPr></w:lvl></w:abstractNum>
  <w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num><w:num w:numId="2"><w:abstractNumId w:val="1"/></w:num>
</w:numbering>`;
}

function headerXml(meta = {}) {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr xmlns:w="${W}"><w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="4" w:space="5" w:color="C9D5E5"/></w:pBdr><w:spacing w:after="90"/></w:pPr>${run(`${meta.company || "投标人"} | ${meta.subtitle || "投标响应文件"}`, { size: 9, color: "718096" })}</w:p></w:hdr>`;
}

function footerXml() {
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="${W}"><w:p><w:pPr><w:jc w:val="both"/><w:spacing w:before="0" w:after="0"/></w:pPr>${run("内部审核草稿，未经业务、法务及授权人确认不得递交。", { size: 9, color: "718096" })}<w:r><w:tab/></w:r>${run("第 ", { size: 9, color: "718096" })}${pageFieldRun()}${run(" 页", { size: 9, color: "718096" })}</w:p></w:ftr>`;
}

function documentXml(markdown, meta = {}) {
  const projectDetails = [
    ["项目", "待填写内容"],
    ["项目名称", meta.projectName || "未命名项目"],
    ["采购人", meta.tenderer || "待从招标文件确认"],
    ["项目编号", meta.projectCode || "待从招标文件确认"],
    ["服务类型", meta.serviceType || "待从招标文件确认"],
    ["投标人", meta.company || "待确认"],
    ["使用模板", meta.sourceTemplate || "系统标准结构"],
  ];
  const outline = [
    ["部分", "章节", "主要内容"],
    ["第一部分", "投标函与授权", "投标函、法定代表人证明、授权委托书"],
    ["第二部分", "资格响应", "主体、资质、财务、信用、业绩和合规声明"],
    ["第三部分", "项目理解", "需求理解、难点分析、服务目标和价值"],
    ["第四部分", "运营服务方案", "招聘、派遣、后勤、外包、零工和数据服务"],
    ["第五部分", "项目组织与人员", "组织架构、岗位配置、简历和替补机制"],
    ["第六部分", "质量、安全与应急", "质量门禁、安全管理、隐私、投诉和应急预案"],
    ["第七部分", "商务报价与合同响应", "报价、成本组成、偏离和付款条件"],
    ["第八部分", "业绩与附件", "案例、评价、证照目录和其他证明材料"],
  ];
  const cover = [
    paragraph(meta.serviceType || "人力资源综合服务项目", "CoverEyebrow"),
    paragraph("投标响应文件", "Title"),
    paragraph(`${meta.company || "投标人"}响应版`, "Subtitle"),
    paragraph("内部审核草稿。本文件中的待填写字段、资质、业绩、人员、财务和信用材料均须以招标文件及有效原件为准。", "Notice", { shading: "FFF7E6" }),
    docxTable(projectDetails),
    paragraph(`匹配建议：${meta.decision || "待评估"}；匹配评分：${meta.score ?? "--"} / 100；生成时间：${meta.generatedAt || new Date().toISOString()}`, "Notice"),
    '<w:p><w:pPr><w:pageBreakBefore/></w:pPr></w:p>',
    paragraph("目录", "Heading1"),
    docxTable(outline),
    paragraph("编制提示：响应文件中的所有待填写字段应替换为真实信息。证照、业绩、人员、财务和信用材料应与投标主体一致，并按招标文件要求加盖公章或电子签章。", "Notice", { shading: "EDF4FB" }),
    '<w:p><w:pPr><w:pageBreakBefore/></w:pPr></w:p>',
  ];
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="${W}" xmlns:r="${R}"><w:body>${cover.join("")}${markdownToXml(markdown, meta)}<w:sectPr><w:headerReference w:type="default" r:id="rId2"/><w:footerReference w:type="default" r:id="rId3"/><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/><w:cols w:num="1"/></w:sectPr></w:body></w:document>`;
}

function coreXml(meta = {}) {
  const created = meta.generatedAt || new Date().toISOString();
  return `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>${esc(meta.title || "响应文件")}</dc:title><dc:subject>${esc(meta.subtitle || "投标响应文件")}</dc:subject><dc:creator>招标智策</dc:creator><cp:lastModifiedBy>招标智策</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">${esc(created)}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">${esc(created)}</dcterms:modified></cp:coreProperties>`;
}

function writePart(root, rel, content) {
  const target = path.join(root, rel);
  fs.mkdirSync(path.dirname(target), { recursive: true });
  fs.writeFileSync(target, content, "utf8");
}

function buildResponseDocx(outputPath, markdown, meta = {}) {
  const workRoot = fs.mkdtempSync(path.join(path.dirname(outputPath), ".docx-work-"));
  try {
    writePart(workRoot, "[Content_Types].xml", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="${CT}"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/><Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/></Types>`);
    writePart(workRoot, "_rels/.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="${REL}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/></Relationships>`);
    writePart(workRoot, "word/_rels/document.xml.rels", `<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="${REL}"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/><Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>`);
    writePart(workRoot, "word/document.xml", documentXml(markdown, meta));
    writePart(workRoot, "word/styles.xml", stylesXml());
    writePart(workRoot, "word/numbering.xml", numberingXml());
    writePart(workRoot, "word/header1.xml", headerXml(meta));
    writePart(workRoot, "word/footer1.xml", footerXml());
    writePart(workRoot, "docProps/core.xml", coreXml(meta));
    if (fs.existsSync(outputPath)) fs.unlinkSync(outputPath);
    const zipScript = [
      "Add-Type -AssemblyName System.IO.Compression.FileSystem",
      `$source = '${workRoot.replace(/'/g, "''")}'`,
      `$destination = '${outputPath.replace(/'/g, "''")}'`,
      "[System.IO.Compression.ZipFile]::CreateFromDirectory($source, $destination)",
    ].join("; ");
    execFileSync("powershell.exe", ["-NoProfile", "-Command", zipScript], { encoding: "utf8", windowsHide: true });
    return { outputPath };
  } finally {
    fs.rmSync(workRoot, { recursive: true, force: true });
  }
}

module.exports = { buildResponseDocx };
