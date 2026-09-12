"""公司材料接口（模板收集模式，M3 单一事实源）。

导入（两步，预览-确认）：
  POST /api/materials/import/preview  → 解析 ZIP，暂存本地，返回 previewId + 摘要（不写 M3）
  GET  /api/materials/import/preview/{id} → 查看完整解析结果
  POST /api/materials/import/confirm  → 确认落库（upsert 进 M3，删暂存）
  DELETE /api/materials/import/preview/{id} → 放弃预览（删暂存）
  POST /api/materials/import          → 一步导入（直接解析 + upsert 进 M3，跳过预览）

读取 / 导出（均从 M3 读）：
  GET    /api/materials                → 本体全量（facts + relationships + profile）
  GET    /api/materials/profile        → 企业画像
  GET    /api/materials/export?fmt=    → md | json | xlsx | m3
  GET    /api/materials/evidence-files → 枚举某事实证据文件夹内的文件
"""
from __future__ import annotations

import base64
import json
import re
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..document_recognition.ontology_defs import (
    CLASS_SCHEMA, FACT_KEY_BY_CLASS as _FACT_KEY_BY_CLASS, REL_MAPPING, RUNTIME_FIELDS,
)
from ..document_recognition.storage import FACT_LIST_KEYS, RELATIONSHIPS, build_edges, build_profile
from ..importers import ExcelImporter
from ..m3.sync import build_m3_client, read_facts_from_m3, read_organization_from_m3, sync_facts_to_m3

# 8 个本体类的导出列：从 CLASS_SCHEMA（唯一来源）派生 = 本体字段 + 各类运行时辅助字段
_CLASS_FIELDS: dict[str, list[str]] = {
    cls: [n for n, _, _ in attrs] + sorted(RUNTIME_FIELDS.get(_FACT_KEY_BY_CLASS[cls], set()))
    for cls, attrs in CLASS_SCHEMA.items()
}

# 从 ontology_defs（唯一来源）派生的 schema，保持 materials.py 原有结构
_CLASS_SCHEMA: dict[str, dict[str, Any]] = {
    cls: {"showName": cls, "desc": "", "attrs": [(n, t, bool(p)) for n, t, p in attrs]}
    for cls, attrs in CLASS_SCHEMA.items()
}

_RELATIONSHIP_MAPPINGS: list[dict[str, str]] = [
    {
        "relationName": n, "fromClass": f, "toClass": t,
        "sourcePrimaryKeyField": sp, "sourceForeignKeyField": sf,
        "targetPrimaryKeyField": tp, "targetJoinField": tj,
    }
    for n, f, t, sp, sf, tp, tj in REL_MAPPING
]

# 受控词表（V0.3 重构：删除 materialGroup/capabilityType/ipType/certificationType，
# 新增 enterpriseSize/credentialType）
_CONTROLLED_VOCABULARIES: dict[str, list[str]] = {
    "serviceTypes": ["数字孪生", "数据智能", "企业架构", "本体数据治理", "AI 智能体", "智慧园区/城市", "数据中心与运维", "低空经济", "电力与能源", "智慧矿山", "数智咨询与项目交付"],
    "industries": ["智慧园区", "数据中心与运维", "职业教育与实训", "低空经济", "电力与能源", "智慧矿山", "企业数字化治理", "AI 企业应用"],
    "regions": ["全国", "北京市", "福建省", "福州市", "浙江省", "杭州市", "陕西省", "贵州省", "山东省", "四川省", "湖北省", "湖南省", "广东省", "江苏省", "上海市"],
    "enterpriseSize": ["中型企业", "小型企业", "微型企业"],
    "qualificationType": ["基本资质证书", "体系认证", "服务与研发资质", "行业许可证", "信用等级", "实验室认可", "荣誉与奖项", "会员与行业身份", "其他"],
    "evidenceType": ["审计报告", "银行资信", "纳税证明", "社保证明", "财务报表", "信用报告"],
    "productType": ["软件平台", "系统", "引擎", "模块", "服务包"],
    "credentialType": ["专利", "软件著作权", "商标", "作品登记", "检测报告", "产品认证", "标准符合性证明", "兼容性报告", "测评"],
    "acceptanceStatus": ["accepted", "ongoing", "pending"],
    "verificationStatus": ["未核验", "已核验有效", "已核验过期", "待人工确认"],
}


# 8 类字段 schema + 8 关系契约已收敛到 ontology_defs（见文件顶部 _CLASS_SCHEMA/_RELATIONSHIP_MAPPINGS 派生）


class MaterialPackagePayload(BaseModel):
    """模板包上传：ZIP（企业材料收集表.xlsx + 证明材料/ 文件夹）的 base64。"""
    fileBase64: str = ""
    fileName: str = ""


class ConfirmImportPayload(BaseModel):
    """确认导入：用预览返回的 previewId 落库。"""
    previewId: str = ""


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _preview_dir(store: Any) -> Path:
    return Path(store.runtime_root) / "_imports"


def _preview_summary(facts: dict[str, Any], stats: dict[str, int]) -> dict[str, Any]:
    """从 facts 生成轻量摘要（count + 前若干条样本），供预览界面展示，不返回完整 facts。"""
    org = facts.get("organization") or {}

    def names(key: str, field: str, limit: int = 30) -> dict[str, Any]:
        rows = facts.get(key) or []
        sample = [r.get(field, "") for r in rows if r.get(field)][:limit]
        return {"count": len(rows), "sample": sample}

    return {
        "company": org.get("organizationName", ""),
        "organizationType": org.get("organizationType", ""),
        "enterpriseSize": org.get("enterpriseSize", ""),
        "stats": stats,
        "qualification_certificate": names("qualification_certificate", "qualificationName"),
        "performance_case": names("performance_case", "caseName"),
        "staff_member": names("staff_member", "name"),
        "financial_evidence": names("financial_evidence", "evidenceType"),
        "technical_product": names("technical_product", "productName"),
        "technical_parameter": names("technical_parameter", "description"),
        "credential": names("credential", "credentialName"),
    }


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(v) for v in value)
    if isinstance(value, bool):
        return value
    if value == "":
        return ""
    return value


def _render_markdown(data: dict[str, Any]) -> str:
    """把本体对齐 JSON 渲染成人类可读的 Markdown 报告。"""
    profile = data.get("profile") or {}
    facts = data.get("facts") or {}
    lines: list[str] = ["# 公司材料识别结果", ""]

    lines.append("## 一、企业画像（聚合）")
    lines.append("")
    company = profile.get("company") or ""
    if company:
        lines.append(f"- **公司**：{company}")
    if profile.get("enterpriseSize"):
        lines.append(f"- **企业规模**：{profile['enterpriseSize']}")
    if profile.get("employeeCount"):
        lines.append(f"- **从业人员**：{profile['employeeCount']} 人")
    if profile.get("annualRevenueWan"):
        lines.append(f"- **营业收入**：{profile['annualRevenueWan']} 万元")
    if profile.get("totalAssetsWan"):
        lines.append(f"- **资产总额**：{profile['totalAssetsWan']} 万元")
    caps = profile.get("capabilities") or ""
    if caps:
        lines.append(f"- **能力清单**：{caps}")
    quals = profile.get("qualifications") or []
    if quals:
        lines.append(f"- **资质**：{'、'.join(quals)}")
    certs = profile.get("certificates") or []
    if certs:
        lines.append(f"- **证书**（{len(certs)} 份）：")
        for i, c in enumerate(certs, 1):
            seg = [c.get("name") or ""]
            if c.get("number"):
                seg.append(f"编号 {c['number']}")
            if c.get("validUntil"):
                seg.append(f"有效期至 {c['validUntil']}")
            if c.get("issuer"):
                seg.append(f"发证机关 {c['issuer']}")
            lines.append(f"  {i}. {'｜'.join(seg)}")
    perfs = profile.get("performances") or []
    if perfs:
        lines.append(f"- **业绩**（{len(perfs)} 项）：")
        for i, p in enumerate(perfs, 1):
            amt = f"（{p['amount']}）" if p.get("amount") else ""
            lines.append(f"  {i}. {p.get('name') or ''}{amt}")
    persons = profile.get("personnel") or []
    if persons:
        items = []
        for p in persons:
            role = p.get("role") or ""
            cnt = p.get("count")
            items.append(f"{role} {cnt} 名" if cnt else role)
        lines.append(f"- **人员**（{len(persons)} 个角色）：{'、'.join(items)}")
    fins = profile.get("financial_evidences") or []
    if fins:
        lines.append(f"- **财务证明**（{len(fins)} 份）：")
        for i, f in enumerate(fins, 1):
            lines.append(f"  {i}. {f.get('evidenceType') or '其他'}（{f.get('issuedBy') or ''}）")
    creds = profile.get("credentials") or []
    if creds:
        lines.append(f"- **证照凭证**（{len(creds)} 项）：")
        for i, c in enumerate(creds, 1):
            lines.append(f"  {i}. [{c.get('type') or '其他'}] {c.get('name') or ''}")

    lines.append("")
    lines.append("## 二、事实明细")
    lines.append("")
    for class_cn, key, name_field in [
        ("资质认证", "qualification_certificate", "qualificationName"),
        ("类似业绩", "performance_case", "caseName"),
        ("项目人员", "staff_member", "role"),
        ("财务证明", "financial_evidence", "evidenceType"),
        ("技术产品", "technical_product", "productName"),
        ("技术参数", "technical_parameter", "description"),
        ("证照凭证", "credential", "credentialName"),
    ]:
        rows = facts.get(key) or []
        lines.append(f"### {class_cn}（{len(rows)}）")
        for i, r in enumerate(rows, 1):
            name = r.get(name_field) or "未命名"
            ep = r.get("evidencePath") or ""
            lines.append(f"  {i}. {name}" + (f"（证据：{ep}）" if ep else ""))
        lines.append("")

    return "\n".join(lines)


def _render_xlsx(data: dict[str, Any]) -> bytes:
    """导出单文件多 Sheet 的 .xlsx：9 类分表 + relationship_mapping + controlled_vocabularies。"""
    from openpyxl import Workbook

    wb = Workbook()
    wb.remove(wb.active)
    facts = data.get("facts") or {}

    for class_name, key in _FACT_KEY_BY_CLASS.items():
        ws = wb.create_sheet(class_name)
        ws.append(_CLASS_FIELDS[class_name])
        value = facts.get(key)
        if isinstance(value, dict):
            rows = [value] if value else []
        else:
            rows = value or []
        for row in rows:
            ws.append([_cell(row.get(f, "")) for f in _CLASS_FIELDS[class_name]])

    ws = wb.create_sheet("relationship_mapping")
    ws.append(["relationName", "fromClass", "toClass", "desc"])
    for rel in data.get("relationships", []):
        ws.append([
            rel.get("relationName", ""),
            rel.get("fromClass", ""),
            rel.get("toClass", ""),
            rel.get("desc", ""),
        ])

    ws = wb.create_sheet("controlled_vocabularies")
    ws.append(["vocabName", "values"])
    for name, values in _CONTROLLED_VOCABULARIES.items():
        ws.append([name, " / ".join(values)])

    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _build_ontology_model(data: dict[str, Any]) -> dict[str, Any]:
    """生成 uino-02 格式的 ontology_model.json（类 + 字段 + 关系定义）。"""
    from datetime import datetime

    namespace = data.get("namespace", "/tenderhub")
    classes: list[dict[str, Any]] = []
    for class_name, schema in _CLASS_SCHEMA.items():
        attrs = [
            {
                "name": name,
                "showName": name,
                "type": m3_type,
                "isPK": is_pk,
                "bizzKey": is_pk,
                "desc": "",
            }
            for name, m3_type, is_pk in schema["attrs"]
        ]
        classes.append({
            "name": class_name,
            "showName": schema.get("showName", class_name),
            "desc": schema.get("desc", ""),
            "sourceTables": [class_name],
            "isCore": False,
            "attrs": attrs,
        })

    relations = [
        {
            "name": f"{rel['fromClass']}_to_{rel['toClass']}",
            "fromClass": rel["fromClass"],
            "toClass": rel["toClass"],
            "desc": rel["relationName"],
            "sourceFK": f"{rel['fromClass']}.{rel['sourceForeignKeyField']}",
        }
        for rel in _RELATIONSHIP_MAPPINGS
    ]

    return {
        "version": "1.0",
        "namespace": namespace,
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "generatedBy": "tender-agent-materials",
        "summary": {
            "totalClasses": len(classes),
            "totalRelations": len(relations),
            "totalAttributes": sum(len(c["attrs"]) for c in classes),
        },
        "classes": classes,
        "relations": relations,
    }


def _build_relationship_mapping(data: dict[str, Any]) -> dict[str, Any]:
    """生成 uino-04 认的关系连接契约 relationship_mapping.json。"""
    return {
        "version": "3.0",
        "namespace": data.get("namespace", "/tenderhub"),
        "relationships": _RELATIONSHIP_MAPPINGS,
    }


def _render_m3_package(data: dict[str, Any]) -> bytes:
    """导出 M3 治理包 zip：standardized/*.csv + ontology_model.json + relationship_mapping.json。"""
    import csv
    import io as _io

    facts = data.get("facts") or {}
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for class_name, key in _FACT_KEY_BY_CLASS.items():
            fields = [a[0] for a in _CLASS_SCHEMA[class_name]["attrs"]]
            sio = _io.StringIO()
            writer = csv.writer(sio)
            writer.writerow(fields)
            value = facts.get(key)
            rows = [value] if isinstance(value, dict) else (value or [])
            for row in rows:
                cells = []
                for f in fields:
                    v = row.get(f, "")
                    if isinstance(v, list):
                        v = "、".join(str(x) for x in v)
                    cells.append("" if v is None else v)
                writer.writerow(cells)
            zf.writestr(f"standardized/{class_name}.csv", sio.getvalue().encode("utf-8"))

        zf.writestr(
            "ontology_model.json",
            json.dumps(_build_ontology_model(data), ensure_ascii=False, indent=2).encode("utf-8"),
        )
        zf.writestr(
            "relationship_mapping.json",
            json.dumps(_build_relationship_mapping(data), ensure_ascii=False, indent=2).encode("utf-8"),
        )
    return buf.getvalue()


def create_router(store: Any) -> APIRouter:
    router = APIRouter(tags=["materials"])

    def _m3_data() -> dict[str, Any]:
        """从 M3 读取 8 类事实，组装成本体对齐结构（facts/relationships/edges/profile）。"""
        try:
            facts = read_facts_from_m3(build_m3_client(store))
        except Exception:
            facts = {"organization": None, **{key: [] for key in FACT_LIST_KEYS}}
        return {
            "namespace": "/tenderhub",
            "facts": facts,
            "relationships": RELATIONSHIPS,
            "edges": build_edges(facts),
            "profile": build_profile(facts),
        }

    @router.get("/api/materials")
    def list_materials() -> dict[str, Any]:
        return _m3_data()

    @router.get("/api/materials/profile")
    def get_profile() -> dict[str, Any]:
        return _m3_data()["profile"]

    @router.get("/api/materials/evidence-files")
    def list_evidence_files(path: str = "") -> dict[str, Any]:
        """枚举某事实证据文件夹内的文件。path = 事实的 evidencePath（如 runtime/libraries/company/证明材料/资质认证/3/）。

        返回按文件名开头数字排序的文件列表（1.png < 2.png < 10.png）。
        """
        if not path:
            return {"path": path, "files": []}
        root = Path(store.runtime_root).resolve()
        base = (root / path).resolve()
        if root != base and root not in base.parents:
            raise HTTPException(status_code=400, detail="非法路径")
        if not base.is_dir():
            return {"path": path, "files": []}

        def _num_key(p: Path) -> tuple[int, str]:
            m = re.match(r"(\d+)", p.stem)
            return (int(m.group(1)), p.stem) if m else (10**12, p.stem)

        files: list[dict[str, Any]] = []
        for p in sorted(base.iterdir(), key=_num_key):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            files.append({"name": p.name, "url": f"/runtime/{rel}", "size": p.stat().st_size})
        return {"path": path, "files": files}

    @router.get("/api/materials/export")
    def export_materials(fmt: str = "md") -> Response:
        data = _m3_data()
        if fmt == "json":
            return Response(
                content=json.dumps(data, ensure_ascii=False, indent=2),
                media_type="application/json; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="company-materials.json"'},
            )
        if fmt == "xlsx":
            return Response(
                content=_render_xlsx(data),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                headers={"Content-Disposition": 'attachment; filename="company-materials.xlsx"'},
            )
        if fmt == "m3":
            return Response(
                content=_render_m3_package(data),
                media_type="application/zip",
                headers={"Content-Disposition": 'attachment; filename="company-materials-m3.zip"'},
            )
        return Response(
            content=_render_markdown(data),
            media_type="text/markdown; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="company-materials.md"'},
        )

    @router.post("/api/materials/import")
    def import_material_package(payload: MaterialPackagePayload) -> dict[str, Any]:
        """一步导入：ZIP → 解析 → 直接 upsert 进 M3。

        本地不再落 company-materials.json，只保留证明材料文件（evidencePath 相对路径指向）。
        如需预览后再确认，使用 /api/materials/import/preview + confirm。
        """
        if not payload.fileBase64:
            raise HTTPException(status_code=400, detail="缺少 fileBase64（模板包 ZIP）")
        try:
            raw = base64.b64decode(payload.fileBase64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="fileBase64 解码失败") from exc

        importer = ExcelImporter(store.runtime_root)
        try:
            result = importer.import_zip(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        m3 = build_m3_client(store)
        facts = dict(result["facts"])
        # 组织缺失时从 M3 取回（避免边表悬空）；M3 连不上也不阻断导入
        if not facts.get("organization"):
            try:
                org = read_organization_from_m3(m3)
            except Exception:
                org = None
            if org:
                facts["organization"] = org
        m3_sync = sync_facts_to_m3(m3, facts)
        store._refresh_company_from_m3(force=True)

        return {
            "stats": result["stats"],
            "warnings": result["warnings"],
            "m3Sync": m3_sync,
            "profile": build_profile(facts),
        }

    # ---- 预览-确认模式：预览存本地暂存，不写 M3；确认后才 upsert ----

    @router.post("/api/materials/import/preview")
    def preview_import(payload: MaterialPackagePayload) -> dict[str, Any]:
        """预览导入（不落库）：解析 ZIP → 暂存到 runtime/_imports/ → 返回 previewId + 摘要。
        M3 不变。
        """
        if not payload.fileBase64:
            raise HTTPException(status_code=400, detail="缺少 fileBase64（模板包 ZIP）")
        try:
            raw = base64.b64decode(payload.fileBase64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="fileBase64 解码失败") from exc

        importer = ExcelImporter(store.runtime_root)
        try:
            result = importer.import_zip(raw)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        preview_id = uuid4().hex[:12]
        pdir = _preview_dir(store)
        pdir.mkdir(parents=True, exist_ok=True)
        record = {
            "previewId": preview_id,
            "createdAt": _now_iso(),
            "fileName": payload.fileName,
            "facts": result["facts"],
            "stats": result["stats"],
            "warnings": result["warnings"],
        }
        (pdir / f"{preview_id}.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {
            "previewId": preview_id,
            "stats": result["stats"],
            "warnings": result["warnings"],
            "summary": _preview_summary(result["facts"], result["stats"]),
        }

    @router.get("/api/materials/import/preview/{preview_id}")
    def get_preview_detail(preview_id: str) -> dict[str, Any]:
        """查看某次预览的完整解析结果（facts + stats + warnings）。"""
        path = _preview_dir(store) / f"{preview_id}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="预览不存在或已过期")
        record = json.loads(path.read_text(encoding="utf-8"))
        return {
            "previewId": record["previewId"],
            "createdAt": record["createdAt"],
            "facts": record["facts"],
            "stats": record["stats"],
            "warnings": record["warnings"],
        }

    @router.post("/api/materials/import/confirm")
    def confirm_import(payload: ConfirmImportPayload) -> dict[str, Any]:
        """确认导入：把预览暂存的 facts upsert 进 M3，完成后删除暂存。"""
        if not payload.previewId:
            raise HTTPException(status_code=400, detail="缺少 previewId")
        path = _preview_dir(store) / f"{payload.previewId}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="预览不存在或已过期")
        record = json.loads(path.read_text(encoding="utf-8"))

        m3 = build_m3_client(store)
        facts = dict(record["facts"])
        if not facts.get("organization"):
            try:
                org = read_organization_from_m3(m3)
            except Exception:
                org = None
            if org:
                facts["organization"] = org
        m3_sync = sync_facts_to_m3(m3, facts)
        path.unlink(missing_ok=True)
        store._refresh_company_from_m3(force=True)

        return {
            "stats": record["stats"],
            "warnings": record["warnings"],
            "m3Sync": m3_sync,
            "profile": build_profile(facts),
        }

    @router.delete("/api/materials/import/preview/{preview_id}")
    def discard_preview(preview_id: str) -> dict[str, Any]:
        """放弃预览：删除暂存，不写入 M3。"""
        path = _preview_dir(store) / f"{preview_id}.json"
        if not path.exists():
            raise HTTPException(status_code=404, detail="预览不存在或已过期")
        path.unlink(missing_ok=True)
        return {"discarded": preview_id}

    return router
