# -*- coding: utf-8 -*-
"""把「企业材料事实」写透到 M3 图库：upsert 对象 + 重建关系边。

与 M3_workspace/generate_sql.py 的 DML 生成逻辑保持一致：
  - 对象 RID：`tenderhub_{class_snake}:{业务主键}`
  - 字段：CamelCase 属性 → snake_case，含主键字段（作为普通列冗余）
  - 关系边：方向 = REL_MAPPING 的 fromClass → toClass

本模块只做「写入 + 建边」，不负责删除；删除走 /api/ontology/classes/* 的 DELETE。
"""
from __future__ import annotations

from typing import Any

from .client import M3Client, M3Error
from ..document_recognition.ontology_defs import CLASS_SCHEMA, camel_to_snake
from ..document_recognition.storage import FACT_LIST_KEYS, ID_FIELD_BY_KEY, build_edges


def _field_types(class_snake: str) -> dict[str, str]:
    """snake_case 字段名 → M3 类型（date/timestamp 需截断时间）。"""
    for cls, attrs in CLASS_SCHEMA.items():
        if camel_to_snake(cls) == class_snake:
            return {camel_to_snake(n): t for n, t, _ in attrs}
    return {}


def _clean_fields(class_snake: str, row: dict[str, Any], skip_id: str) -> dict[str, Any]:
    """CamelCase → snake_case，去掉 None/内部字段，date/timestamp 只留日期部分。"""
    types = _field_types(class_snake)
    out: dict[str, Any] = {}
    for k, v in row.items():
        if k == skip_id:
            continue
        if k.startswith("__"):
            continue
        if v is None or v == "":
            continue
        snake = camel_to_snake(k)
        if types.get(snake) in ("date", "timestamp") and isinstance(v, str):
            # M3 只认 yyyy-MM-dd（不认带时区的完整时间），与 generate_sql.py 对齐
            v = v.split("T")[0].split(" ")[0]
        out[snake] = v
    return out


def sync_facts_to_m3(m3: M3Client, facts: dict[str, Any]) -> dict[str, Any]:
    """把 8 类 facts 写透到 M3，返回 {objects, edges, warnings}。

    失败不抛异常（M3 不可用时不阻断导入），错误记入 warnings。
    """
    warnings: list[str] = []
    objects = 0
    edges = 0

    # 1) organization（单对象）
    org = facts.get("organization") or {}
    if org:
        try:
            org_id = org.get("organizationId") or "org-001"
            m3.upsert_object("organization", _clean_fields("organization", org, "organizationId"), str(org_id))
            objects += 1
        except Exception as e:
            warnings.append(f"organization 写入失败：{str(e)[:160]}")

    # 2) 7 个事实类（列表）
    for key in FACT_LIST_KEYS:
        id_field = ID_FIELD_BY_KEY[key]
        for row in facts.get(key, []):
            biz_id = row.get(id_field)
            if not biz_id:
                continue
            try:
                m3.upsert_object(key, _clean_fields(key, row, id_field), str(biz_id))
                objects += 1
            except Exception as e:
                warnings.append(f"{key}/{biz_id} 写入失败：{str(e)[:160]}")

    # 3) 关系边（与 build_edges 相同的确定性边表，方向 = fromClass → toClass）
    for e in build_edges(facts):
        from_snake = camel_to_snake(e["fromClass"])
        to_snake = camel_to_snake(e["toClass"])
        try:
            src_rid = m3.rid(from_snake, e["fromId"])
            tgt_rid = m3.rid(to_snake, e["toId"])
            m3.create_edge(e["relationName"], src_rid, tgt_rid)
            edges += 1
        except Exception as err:
            warnings.append(f"边 {e['relationName']} {e['fromId']}→{e['toId']} 失败：{str(err)[:160]}")

    return {"objects": objects, "edges": edges, "warnings": warnings}


def build_m3_client(store: Any) -> M3Client:
    """按 store.config["m3"] 构造 M3 客户端（缺省走 M3Client 默认连接信息）。"""
    cfg: dict[str, Any] = {}
    if store is not None and hasattr(store, "config"):
        cfg = dict(store.config.get("m3") or {})
    return M3Client(
        host=str(cfg.get("host") or ""),
        company=str(cfg.get("company") or ""),
        username=str(cfg.get("username") or cfg.get("userName") or ""),
        password=str(cfg.get("password") or ""),
        namespace=str(cfg.get("namespace") or "tenderhub"),
    )


def _row_to_camel(class_snake: str, row: dict[str, Any]) -> dict[str, Any]:
    """M3 行（snake_case + 内部字段/边字段）→ 业务 fact（CamelCase，仅本体字段）。"""
    out: dict[str, Any] = {}
    for cls, attrs in CLASS_SCHEMA.items():
        if camel_to_snake(cls) != class_snake:
            continue
        for name, _, _ in attrs:
            snake = camel_to_snake(name)
            if snake in row and row[snake] is not None:
                out[name] = row[snake]
        break
    return out


def read_facts_from_m3(m3: M3Client) -> dict[str, Any]:
    """从 M3 读取 8 类事实，转回 CamelCase（供 profile/export 等消费方使用）。

    list_objects 内部会自动登录；M3 不可用时抛 M3Error，由调用方决定兜底。
    """
    facts: dict[str, Any] = {"organization": None}
    orgs = m3.list_objects("organization", limit=1)
    if orgs:
        facts["organization"] = _row_to_camel("organization", orgs[0])
    for key in FACT_LIST_KEYS:
        facts[key] = [_row_to_camel(key, r) for r in m3.list_objects(key, limit=100000)]
    return facts


def read_organization_from_m3(m3: M3Client) -> dict[str, Any] | None:
    """从 M3 读取企业组织（CamelCase）；无记录返回 None。"""
    orgs = m3.list_objects("organization", limit=1)
    return _row_to_camel("organization", orgs[0]) if orgs else None
