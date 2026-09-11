# -*- coding: utf-8 -*-
"""生成「企业材料收集表.xlsx」模板：9 个 sheet + 下拉词表 + 示例行 + 填写说明。"""

from __future__ import annotations

import datetime
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side, numbers
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

TAG = Path(__file__).resolve().parent.parent  # tender-agent/tender-agent（项目根）
OUT = TAG / "templates" / "企业材料收集表.xlsx"
OUT.parent.mkdir(parents=True, exist_ok=True)

HEADER_FILL = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
HEADER_FONT = Font(name="微软雅黑", bold=True, color="FFFFFF", size=11)
SAMPLE_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")
THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),
    top=Side(style="thin"), bottom=Side(style="thin"),
)
NORMAL_FONT = Font(name="微软雅黑", size=10)
LABEL_FONT = Font(name="微软雅黑", bold=True, color="1F4E79", size=10)
NOTE_FONT = Font(name="微软雅黑", italic=True, color="808080", size=9)


def _set_header_row(ws, headers: list[str], col_widths: list[int] | None = None):
    """写表头行（第1行），加样式，冻结首行。"""
    for c, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = THIN_BORDER
    if col_widths:
        for c, w in enumerate(col_widths, 1):
            ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}1"


def _sample_row(ws, row: int, ncols: int, values: list):
    """写示例行（黄色底）。"""
    for c, v in enumerate(values, 1):
        cell = ws.cell(row=row, column=c, value=v)
        cell.font = Font(name="微软雅黑", size=9, italic=True, color="808080")
        cell.fill = SAMPLE_FILL
        cell.border = THIN_BORDER
    # 填充剩余列
    for c in range(len(values) + 1, ncols + 1):
        cell = ws.cell(row=row, column=c)
        cell.fill = SAMPLE_FILL
        cell.border = THIN_BORDER


def _make_dv(cell_range: str, options: str, ws) -> DataValidation:
    """创建列表验证。"""
    dv = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True)
    dv.error = "请从下拉菜单中选择"
    dv.errorTitle = "无效值"
    ws.add_data_validation(dv)
    dv.add(cell_range)
    return dv


# ================================================================
# Sheet 0：填写说明
# ================================================================
def write_instructions(wb: Workbook):
    ws = wb.active
    ws.title = "填写说明"
    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 80

    lines = [
        ("", ""),
        ("", "企业材料收集模板 — 填写说明"),
        ("", ""),
        ("", "📋 概述"),
        ("", "本工作簿包含 9 个 sheet，请按顺序填写："),
        ("", "  ① 企业基本信息 — 填 1 家企业的基本情况和能力范围"),
        ("", "  ② 资质认证 — 填所有资质证书/许可证等，一行一条"),
        ("", "  ③ 类似业绩 — 填所有历史项目案例，一行一条"),
        ("", "  ④ 项目人员 — 填可投入项目的人员，一行一人"),
        ("", "  ⑤ 财务证明 — 填审计报告/银行资信/社保证明等"),
        ("", "  ⑥ 技术产品 — 填所有产品/系统/服务，一行一个"),
        ("", "  ⑦ 技术参数 — 填产品的关键参数指标，一行一条"),
        ("", "  ⑧ 证照凭证 — 填产品认证/专利/软著/商标等，一行一条"),
        ("", ""),
        ("", "📁 证明材料（重要）"),
        ("", "收集表外，请同时准备一个「证明材料」文件夹，结构如下："),
        ("", "  证明材料/"),
        ("", "    ├── 资质认证/          ← 文件夹名 = sheet 名"),
        ("", "    │   └── 3/             ← 序号 = 该行「序号」（3、4、5…）"),
        ("", "    │       ├── 1.png      ← 这条资质的第 1 页"),
        ("", "    │       ├── 2.png      ← 第 2 页"),
        ("", "    │       └── 3.pdf      ← 也可以是 PDF 等任意格式"),
        ("", "    ├── 类似业绩/"),
        ("", "    ├── 项目人员/"),
        ("", "    ├── 财务证明/"),
        ("", "    └── 技术产品/ 技术参数/ 证照凭证/"),
        ("", ""),
        ("", "⚠ 命名规则："),
        ("", "  • 一个 sheet 行 = 一个文件夹，文件夹名 = 该行「序号」（写数字 3，不要写 003）"),
        ("", "  • 文件夹内文件按 1、2、3… 命名（1.png、2.png、3.pdf），不用补零"),
        ("", "  • 顺序由系统按数字排：100 张、1000 张都不会乱"),
        ("", "  • 格式不限：PDF、PNG、JPG 等都可以，也可以混放"),
        ("", ""),
        ("", "📝 填写提示"),
        ("", "  • 带 ▼ 的列：必须从下拉菜单选择，不要手输"),
        ("", "  • 日期格式：YYYY-MM-DD（如 2023-05-20）"),
        ("", "  • 金额单位：合同金额/营收/总资产均已标注单位（合同金额为元；营业收入/资产总额为万元）"),
        ("", "  • 能力清单/范围：多值用顿号「、」分隔，如「数字孪生、智慧城市、AI 智能体」"),
        ("", "  • 示例行（黄色底）：仅供参考，填写时请覆盖为真实数据"),
        ("", ""),
        ("", "📦 提交方式"),
        ("", "  将 Excel 文件 + 证明材料文件夹打包为 ZIP，通过系统上传页面提交。"),
        ("", f"  模板版本：V1.1　生成日期：{datetime.date.today().isoformat()}"),
    ]
    for i, (_, text) in enumerate(lines, 1):
        c = ws.cell(row=i, column=2, value=text)
        c.font = NOTE_FONT if text.startswith("  •") else LABEL_FONT if not text.strip() else NORMAL_FONT
        if text.startswith("企业材料收集模板") or text.startswith("📋") or text.startswith("📁") or text.startswith("📝") or text.startswith("📦"):
            c.font = LABEL_FONT


# ================================================================
# Sheet 1：企业基本信息（竖排：字段｜值｜说明）
# ================================================================
ENTERPRISE_FIELDS = [
    ("企业全称", "", "营业执照上的公司全称", None),
    ("企业类型", "投标供应商", "purchaser=采购人 / agency=代理机构 / bidder=投标供应商", "采购人,代理机构,投标供应商"),
    ("统一社会信用代码", "", "18 位", None),
    ("法定代表人", "", "", None),
    ("注册地址", "", "", None),
    ("联系方式", "", "电话/邮箱", None),
    ("官网", "", "https://...", None),
    ("从业人员数", "", "单位：人；如 350", None),
    ("营业收入(万元)", "", "上一年度；如 5000.00", None),
    ("资产总额(万元)", "", "上一年度；如 8000.00", None),
    ("企业规模", "小型企业", "下拉选：中型/小型/微型", "中型企业,小型企业,微型企业"),
    ("企业描述", "", "1-2 句话概述企业经营范围与核心业务", None),
    ("能力清单", "", "核心能力，多值用「、」分隔", None),
    ("服务范围", "", "提供的服务类型", None),
    ("产品范围", "", "主要产品线", None),
    ("区域范围", "", "覆盖区域，如「全国、北京市、西南地区」", None),
    ("行业范围", "", "覆盖行业，如「政务、能源、交通」", None),
    ("管理制度/承诺", "", "ISMS 方针、质量承诺等", None),
]

def write_enterprise(wb: Workbook):
    ws = wb.create_sheet("企业基本信息")
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 45
    ws.column_dimensions["C"].width = 38

    # 表头
    for c, h in enumerate(["字段", "填写值", "说明/示例"], 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = HEADER_FONT; cell.fill = HEADER_FILL; cell.alignment = Alignment(horizontal="center"); cell.border = THIN_BORDER
    ws.freeze_panes = "A2"

    for r, (label, default, hint, dv_opts) in enumerate(ENTERPRISE_FIELDS, 2):
        ws.cell(row=r, column=1, value=label).font = Font(name="微软雅黑", bold=True, size=10)
        ws.cell(row=r, column=2, value=default).font = NORMAL_FONT
        ws.cell(row=r, column=3, value=hint).font = NOTE_FONT
        for c in range(1, 4):
            ws.cell(row=r, column=c).alignment = Alignment(vertical="center", wrap_text=True)
            ws.cell(row=r, column=c).border = THIN_BORDER

        if dv_opts:
            dv = DataValidation(type="list", formula1=f'"{dv_opts}"', allow_blank=True)
            dv.error = "请从下拉菜单中选择"
            ws.add_data_validation(dv)
            dv.add(f"B{r}")

    ws.row_dimensions[1].height = 22


# ================================================================
# Sheet 2-8：事实类（横排表头）
# ================================================================
FACT_SHEETS = [
    {
        "name": "资质认证",
        "headers": ["序号", "资质类型▼", "资质名称", "证书编号", "等级", "发证机构",
                     "认证范围", "颁发日期", "有效期起", "有效期止", "状态▼", "备注"],
        "widths": [6, 22, 28, 18, 8, 16, 20, 12, 12, 12, 10, 16],
        "dvs": {
            "B": "基本资质证书,体系认证,服务与研发资质,行业许可证,信用等级,实验室认可,荣誉与奖项,会员与行业身份,其他",
            "K": "有效,已过期,即将到期",
        },
        "sample": ["1", "体系认证",
                   "ISO9001 质量管理体系认证证书", "02423Q00123R6S", "A", "中国质量认证中心",
                   "数字孪生平台的设计、开发与运维", "2023-05-20", "2023-05-20", "2026-05-19", "有效",
                   "获证日期以发证机构官网可查为准，有效期 3 年"],
    },
    {
        "name": "类似业绩",
        "headers": ["序号", "案例名称", "客户名称", "行业", "地区", "服务范围",
                     "合同金额(元)", "币种", "签订日期", "完成日期", "验收状态▼", "角色▼", "联系人", "备注"],
        "widths": [6, 24, 18, 12, 10, 22, 14, 6, 12, 12, 10, 8, 10, 16],
        "dvs": {
            "K": "accepted,ongoing,pending",
            "L": "主承,联合体,分包",
        },
        "sample": ["1", "XX市智慧园区数字孪生平台建设项目",
                   "XX市大数据局", "政务/城市管理", "贵州省贵阳市", "平台设计、开发、部署、培训",
                   18500000.00, "CNY", "2024-03-01", "2024-10-15", "accepted", "主承",
                   "张处长", "合同关键页见证明材料 类似业绩/1/1.png"],
    },
    {
        "name": "项目人员",
        "headers": ["序号", "姓名", "岗位", "人员类型▼", "职称", "专业证书",
                     "从业年限", "入职日期", "专业特长", "在职状态▼", "简历摘要", "备注"],
        "widths": [6, 8, 10, 10, 8, 18, 8, 12, 16, 8, 24, 14],
        "dvs": {
            "D": "管理,技术,操作,服务",
            "J": "在职,离职,借调",
        },
        "sample": ["1", "张三", "项目经理", "管理", "高级工程师",
                   "PMP、高级信息系统项目管理师", 10, "2019-08-01", "数字孪生、GIS、智慧城市",
                   "在职", "曾任 XX 等多个千万级数字孪生项目项目经理；获 PMP 等证书。", ""],
    },
    {
        "name": "财务证明",
        "headers": ["序号", "证明类型▼", "期间起", "期间止", "出具机构",
                     "出具日期", "有效期止", "营收(元)", "总资产(元)", "信用等级", "状态▼", "备注"],
        "widths": [6, 16, 12, 12, 18, 12, 12, 14, 14, 10, 8, 14],
        "dvs": {
            "B": "审计报告,银行资信,纳税证明,社保证明,财务报表,信用报告",
            "K": "有效,已过期",
        },
        "sample": ["1", "审计报告", "2024-01-01", "2024-12-31",
                   "XX 会计师事务所", "2025-03-15", "2025-12-31",
                   85000000.00, 120000000.00, "A", "有效", ""],
    },
    {
        "name": "技术产品",
        "headers": ["序号", "产品名称", "别名", "产品类型▼", "分类", "品牌",
                     "型号", "描述", "适用场景", "备注"],
        "widths": [6, 20, 14, 14, 12, 10, 12, 30, 24, 14],
        "dvs": {"D": "软件平台,系统,引擎,模块,服务包"},
        "sample": ["1", "ThingJS-X 数字孪生可视化平台",
                   "ThingJS 平台", "软件平台", "数字孪生/可视化",
                   "UINO 优锘", "V5.2",
                   "低代码数字孪生可视化开发平台，支持二三维一体化渲染、IoT 数据接入、多终端发布",
                   "智慧园区、智慧城市、数据中心运维", ""],
    },
    {
        "name": "技术参数",
        "headers": ["序号", "参数描述", "所属产品名▼", "备注"],
        "widths": [6, 40, 22, 14],
        "dvs": {},
        "sample": ["1", "支持最大并发 500 路视频流实时渲染，帧率 ≥30fps",
                   "ThingJS-X 数字孪生可视化平台",
                   "检测报告见 技术参数/1/1.png"],
    },
    {
        "name": "证照凭证",
        "headers": ["序号", "凭证类型▼", "凭证名称", "登记号", "发证机构",
                     "权利人", "标准号", "取得日期", "有效期止", "关联产品名", "备注"],
        "widths": [6, 22, 26, 18, 18, 14, 16, 12, 12, 22, 14],
        "dvs": {
            "B": "专利,软件著作权,商标,作品登记,检测报告,产品认证,标准符合性证明,兼容性报告,测评",
        },
        "sample": ["1", "软件著作权",
                   "ThingJS-X 数字孪生可视化平台 V5.2", "2024SR0000123",
                   "国家版权局", "北京优锘科技股份有限公司", "",
                   "2024-06-01", "", "ThingJS-X 数字孪生可视化平台",
                   "登记后永久有效，故有效期止留空"],
    },
]


def write_fact_sheet(wb: Workbook, cfg: dict):
    ws = wb.create_sheet(cfg["name"])
    headers = cfg["headers"]
    _set_header_row(ws, headers, cfg["widths"])

    # 示例行
    _sample_row(ws, 2, len(headers), cfg["sample"])

    # 关键行（3-52 = 50行）设边框
    for r in range(3, 53):
        for c in range(1, len(headers) + 1):
            ws.cell(row=r, column=c).border = THIN_BORDER

    # 下拉验证
    for col_letter, opts in cfg["dvs"].items():
        _make_dv(f"{col_letter}2:{col_letter}52", opts, ws)

    ws.row_dimensions[1].height = 22


# ================================================================
# 生成
# ================================================================
def main():
    wb = Workbook()
    write_instructions(wb)
    write_enterprise(wb)
    for cfg in FACT_SHEETS:
        write_fact_sheet(wb, cfg)

    wb.save(OUT)
    print(f"✅ 模板已生成：{OUT}")
    print(f"   {len(wb.sheetnames)} 个 sheet：{', '.join(wb.sheetnames)}")


if __name__ == "__main__":
    main()