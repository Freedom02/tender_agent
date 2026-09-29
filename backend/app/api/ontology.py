# -*- coding: utf-8 -*-
"""M3 本体 CRUD 路由：8 类 + 8 关系的增删改查。

所有 `class_name` 参数使用 snake_case（如 `organization`、`qualification_certificate`）。
对象标识 `object_id` 为业务主键（RID 冒号后的部分，如 `org-001`）。
关系名 `rel_name` 为 edge 短名（如 `holds`、`certified_by`）。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..m3.client import M3Client, M3Error
from ..document_recognition.ontology_defs import CLASS_CN, CLASS_SCHEMA, REL_MAPPING, ATTR_CN, camel_to_snake

# 关系描述（从 RELATIONSHIPS 提取，或直接用关系名）
_REL_DESC: dict[str, str] = {}
try:
    from ..document_recognition.storage import RELATIONSHIPS  # noqa: F401

    for r in RELATIONSHIPS:  # type: ignore[attr-defined]
        _REL_DESC[r["relationName"]] = r.get("desc", r["relationName"])
except Exception:
    pass

_VALID_CLASSES = {camel_to_snake(cls) for cls in CLASS_SCHEMA}
_VALID_RELS = {r[0] for r in REL_MAPPING}

# ------------------------------------------------------------------ Pydantic
class UpsertObjectPayload(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict, description="snake_case 属性→值，必须含 pk 字段")


class UpdateObjectPayload(BaseModel):
    fields: dict[str, Any] = Field(default_factory=dict, description="部分更新字段（snake_case），不含名放原值")


class CreateEdgePayload(BaseModel):
    source_class: str = Field(...)
    source_id: str = Field(...)
    target_class: str = Field(...)
    target_id: str = Field(...)


class DeleteEdgePayload(BaseModel):
    source_class: str = Field(...)
    source_id: str = Field(...)
    target_class: str = Field(...)
    target_id: str = Field(...)


# ------------------------------------------------------------------ helpers
def _validate_class(class_name: str) -> None:
    if class_name not in _VALID_CLASSES:
        raise HTTPException(404, f"未知类 '{class_name}'，有效类：{', '.join(sorted(_VALID_CLASSES))}")


def _validate_rel(rel_name: str) -> None:
    if rel_name not in _VALID_RELS:
        raise HTTPException(404, f"未知关系 '{rel_name}'，有效关系：{', '.join(sorted(_VALID_RELS))}")


def _snake_fields(class_name: str) -> set[str]:
    """返回该类的 snake_case 字段名集合。"""
    for cls, attrs in CLASS_SCHEMA.items():
        if camel_to_snake(cls) == class_name:
            return {camel_to_snake(n) for n, _, _ in attrs}
    return set()


# ------------------------------------------------------------------ router
def create_router(store: Any = None) -> APIRouter:
    router = APIRouter(prefix="/api/ontology", tags=["本体 CRUD"])

    # 单一 M3 客户端（复用会话）；连接信息可来自 store.config["m3"]
    m3_cfg: dict[str, Any] = {}
    if store is not None and hasattr(store, "config"):
        m3_cfg = dict(store.config.get("m3") or {})
    _client = M3Client(
        host=str(m3_cfg.get("host") or ""),
        company=str(m3_cfg.get("company") or ""),
        username=str(m3_cfg.get("username") or m3_cfg.get("userName") or ""),
        password=str(m3_cfg.get("password") or ""),
        namespace=str(m3_cfg.get("namespace") or "tenderhub"),
    )

    def client() -> M3Client:
        return _client

    # ===================================================== 元信息
    @router.get("/meta")
    def meta() -> dict[str, Any]:
        """返回 8 类 + 8 关系的 schema 元信息。"""
        classes_out = []
        for cls, attrs in CLASS_SCHEMA.items():
            snake = camel_to_snake(cls)
            cn_name, cn_desc = CLASS_CN.get(cls, (snake, ""))
            attrs_out = [
                {"name": camel_to_snake(n), "cn": ATTR_CN.get(n, n), "type": t, "isPk": bool(pk)}
                for n, t, pk in attrs
            ]
            classes_out.append({"name": snake, "cnName": cn_name, "description": cn_desc, "attrs": attrs_out})
        rels_out = [
            {"name": rn, "fromClass": camel_to_snake(fc), "toClass": camel_to_snake(tc),
             "description": _REL_DESC.get(rn, rn)}
            for rn, fc, tc, *_ in REL_MAPPING
        ]
        return {"classes": classes_out, "relations": rels_out}

    # ===================================================== 汇总（企业材料页首屏）
    @router.get("/summary")
    def summary() -> dict[str, Any]:
        """8 类计数汇总（前端企业材料页 tab 徽标用；M3 不可用返回 0）。"""
        counts: dict[str, int] = {}
        failed: list[str] = []
        for cls in CLASS_SCHEMA:
            snake = camel_to_snake(cls)
            try:
                counts[snake] = client().count_objects(snake)
            except M3Error:
                counts[snake] = 0
                failed.append(snake)
        return {
            "namespace": client().namespace,
            "classes": len(CLASS_SCHEMA),
            "relations": len(REL_MAPPING),
            "counts": counts,
            "unavailable": failed,
        }

    # ===================================================== 对象查询
    @router.get("/classes/{class_name}")
    def list_objects(class_name: str, limit: int = 100) -> list[dict[str, Any]]:
        _validate_class(class_name)
        try:
            return client().list_objects(class_name, limit=limit)
        except M3Error as e:
            raise HTTPException(502, f"M3 错误：{e}")

    @router.get("/classes/{class_name}/{object_id}")
    def get_object(class_name: str, object_id: str) -> dict[str, Any]:
        _validate_class(class_name)
        try:
            obj = client().get_object(class_name, object_id)
        except M3Error as e:
            raise HTTPException(502, f"M3 错误：{e}")
        if not obj:
            raise HTTPException(404, f"对象不存在：{class_name}/{object_id}")
        return obj

    # ===================================================== 对象写入
    @router.post("/classes/{class_name}")
    def create_object(class_name: str, payload: UpsertObjectPayload) -> dict[str, Any]:
        """创建对象。fields 须包含 pk 字段（如 organization_id='org-001'）。"""
        _validate_class(class_name)
        fields = payload.fields
        # 找 pk 字段
        pk_field = None
        valid_fields = _snake_fields(class_name)
        for cls, attrs in CLASS_SCHEMA.items():
            if camel_to_snake(cls) == class_name:
                pk_field = next((camel_to_snake(n) for n, _, is_pk in attrs if is_pk), None)
                break
        if not pk_field:
            raise HTTPException(400, f"类 {class_name} 无主键定义")
        biz_id = fields.get(pk_field)
        if not biz_id:
            raise HTTPException(400, f"缺少主键字段 '{pk_field}'")

        # 只保留有效字段
        clean: dict[str, Any] = {}
        for k, v in fields.items():
            if k in valid_fields and k != pk_field:
                clean[k] = v
        try:
            return client().upsert_object(class_name, clean, str(biz_id))
        except M3Error as e:
            raise HTTPException(502, f"M3 错误：{e}")

    @router.put("/classes/{class_name}/{object_id}")
    def update_object(class_name: str, object_id: str, payload: UpdateObjectPayload) -> dict[str, Any]:
        """部分更新：先 fetch 当前对象，合并字段后全量 upsert（避免重置 M3 内置 name）。"""
        _validate_class(class_name)
        try:
            existing = client().get_object(class_name, object_id)
        except M3Error as e:
            raise HTTPException(502, f"M3 读取错误：{e}")
        if not existing:
            raise HTTPException(404, f"对象不存在：{class_name}/{object_id}")

        # 合并：客户端传的覆盖现有值
        merged = dict(existing)
        merged.update(payload.fields)

        # 过滤掉 M3 内部字段（_*、day、class 等），只保留业务字段
        valid_fields = _snake_fields(class_name)
        pk_field = None
        for cls, attrs in CLASS_SCHEMA.items():
            if camel_to_snake(cls) == class_name:
                pk_field = next((camel_to_snake(n) for n, _, is_pk in attrs if is_pk), None)
                break
        clean: dict[str, Any] = {}
        for k, v in merged.items():
            if k in valid_fields and k != pk_field:
                clean[k] = v
        try:
            return client().upsert_object(class_name, clean, object_id)
        except M3Error as e:
            raise HTTPException(502, f"M3 错误：{e}")

    # 类名 → 中文名（删除时的依赖提示用）
    _CLASS_CN_BY_SNAKE: dict[str, str] = {
        camel_to_snake(cls): cn for cls, (cn, _) in CLASS_CN.items()
    }

    @router.delete("/classes/{class_name}/{object_id}")
    def delete_object(class_name: str, object_id: str) -> dict[str, Any]:
        """删除对象（级联删除所有关系边）。

        技术产品删前检查参数/证照依赖；删后清理其证据文件夹。
        """
        _validate_class(class_name)

        # ── 依赖检查：仅 technical_product 查参数与证照引用（外键 RESTRICT）──
        if class_name == "technical_product":
            deps: list[str] = []
            for dep_class in ("technical_parameter", "credential"):
                try:
                    found = client().list_objects(dep_class, limit=500, where={"product_id": object_id})
                except M3Error:
                    continue
                if found:
                    cn = _CLASS_CN_BY_SNAKE.get(dep_class, dep_class)
                    deps.append(f"{cn}: {len(found)} 条")
            if deps:
                raise HTTPException(
                    status_code=409,
                    detail=f"该产品下还有关联数据：{'；'.join(deps)}。请先逐一删除关联记录，再删除产品。",
                )

        # ── 删前读取证据路径（删完对象就拿不到了）──
        evidence = ""
        try:
            obj = client().get_object(class_name, object_id)
            evidence = (obj or {}).get("evidence_path") or (obj or {}).get("evidencePath", "")
        except M3Error:
            pass

        # ── 删 M3 对象（级联删边）──
        try:
            ok = client().delete_object(class_name, object_id)
        except M3Error as e:
            raise HTTPException(502, f"M3 错误：{e}")

        # ── 删证据文件夹（路径必须落在 runtime_root 下，防穿越）──
        evidence_deleted = False
        if evidence and store is not None:
            try:
                root = Path(store.runtime_root).resolve()
                base = (root / evidence).resolve()
                if base != root and base.is_relative_to(root) and base.is_dir():
                    shutil.rmtree(base, ignore_errors=True)
                    evidence_deleted = True
            except Exception:
                pass

        return {
            "deleted": ok,
            "class": class_name,
            "id": object_id,
            "evidenceDeleted": evidence_deleted,
            "evidencePath": evidence or None,
        }

    # ===================================================== 对象关系查询
    @router.get("/objects/{class_name}/{object_id}/relations")
    def object_relations(class_name: str, object_id: str, edge: str = "", direction: str = "both") -> dict[str, Any]:
        """查询对象的一跳关系。direction: out|in|both。"""
        _validate_class(class_name)
        try:
            m3 = client()
            rid = m3.rid(class_name, object_id)
            edges = m3.match_edges(rid, edge=edge or None, direction=direction)
        except M3Error as e:
            raise HTTPException(502, f"M3 错误：{e}")
        return {"class": class_name, "id": object_id, "rid": rid, "direction": direction, "edges": edges}

    # ===================================================== 关系写入
    @router.post("/relations/{rel_name}")
    def create_edge(rel_name: str, payload: CreateEdgePayload) -> dict[str, Any]:
        """创建一条关系边。"""
        _validate_rel(rel_name)
        _validate_class(payload.source_class)
        _validate_class(payload.target_class)
        try:
            m3 = client()
            src_rid = m3.rid(payload.source_class, payload.source_id)
            tgt_rid = m3.rid(payload.target_class, payload.target_id)
            m3.create_edge(rel_name, src_rid, tgt_rid)
        except M3Error as e:
            raise HTTPException(502, f"M3 错误：{e}")
        return {"created": True, "edge": rel_name, "source": f"{payload.source_class}/{payload.source_id}", "target": f"{payload.target_class}/{payload.target_id}"}

    @router.delete("/relations/{rel_name}")
    def delete_edge(rel_name: str, payload: DeleteEdgePayload) -> dict[str, Any]:
        """M3 不提供单条边删除语法。边随源/目标对象级联删除。
        请使用 DELETE /api/ontology/classes/{sourceClass}/{sourceId} 删除源对象（会级联删除其所有边）。
        或用 POST /api/ontology/relations/{rel_name} 幂等重建所需边。
        """
        raise HTTPException(501, "M3 不直接支持单条边删除。请删除源对象（级联删边）或重新创建所需边（幂等）。")

    return router