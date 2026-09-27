# -*- coding: utf-8 -*-
"""把「企业材料事实」写透到 M3 图库：upsert 对象 + 重建关系边。

与 M3_workspace/generate_sql.py 的 DML 生成逻辑保持一致：
  - 对象 RID：`tenderhub_{class_snake}:{业务主键}`
  - 字段：CamelCase 属性 → snake_case，含主键字段（作为普通列冗余）
  - 关系边：方向 = REL_MAPPING 的 fromClass → toClass

本模块只做「写入 + 建边」，不负责删除；删除走 /api/ontology/classes/* 的 DELETE。
"""
from __future__ import annotations

import re
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


def reuse_existing_products(
    m3: M3Client, facts: dict[str, Any], warnings: list[str]
) -> list[dict[str, str]]:
    """Reuse unambiguous same-name products already stored in M3."""
    try:
        existing = read_facts_from_m3(m3, ("technical_product",)).get("technical_product", [])
    except Exception as exc:
        warnings.append(f"库内技术产品读取失败，本次跳过产品级防重：{str(exc)[:160]}")
        return []
    if not existing:
        return []

    def normalize(value: Any) -> str:
        return "".join(str(value or "").split()).lower()

    def aliases(value: Any) -> list[str]:
        return [part.strip() for part in re.split(r"[,，、;；/\s]+", str(value or "")) if part.strip()]

    exact: dict[str, str] = {}
    normalized: dict[str, set[str]] = {}
    for product in existing:
        product_id = str(product.get("productId") or "").strip()
        name = str(product.get("productName") or "").strip()
        if not product_id or not name:
            continue
        exact.setdefault(name, product_id)
        for token in [name, *aliases(product.get("aliases"))]:
            normalized.setdefault(normalize(token), set()).add(product_id)

    reused: list[dict[str, str]] = []
    ambiguous: list[str] = []
    kept: list[dict[str, Any]] = []
    for product in facts.get("technical_product", []):
        name = str(product.get("productName") or "").strip()
        product_id = str(product.get("productId") or "")
        matched_id = exact.get(name) if name else None
        if matched_id is None and name:
            candidates = normalized.get(normalize(name), set())
            if len(candidates) == 1:
                matched_id = next(iter(candidates))
            elif len(candidates) > 1:
                ambiguous.append(name)
        if not matched_id or not product_id:
            kept.append(product)
            continue
        for key in ("technical_parameter", "credential"):
            for row in facts.get(key, []):
                if str(row.get("productId") or "") == product_id:
                    row["productId"] = matched_id
        reused.append({"productName": name, "reusedProductId": matched_id})

    if facts.get("technical_product") is not None:
        facts["technical_product"] = kept
    if reused:
        warnings.append(
            f"技术产品 {len(reused)} 个与库内已有产品同名，已复用库内对象（不重复写入）："
            + "、".join(item["productName"] for item in reused)
        )
    if ambiguous:
        warnings.append(
            f"技术产品 {len(ambiguous)} 个在库内匹配到多个产品，未复用、按新产品写入："
            + "、".join(ambiguous)
        )
    return reused


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


def read_facts_from_m3(m3: M3Client, keys: tuple[str, ...] | None = None) -> dict[str, Any]:
    """从 M3 读取 facts，转回 CamelCase（供 profile/export/匹配等消费方使用）。

    list_objects 内部会自动登录；M3 不可用时抛 M3Error，由调用方决定兜底。

    keys 用于按需指定读取哪几类，避免每次都全量拉 8 条 MQL：
      - None（默认）→ 读全部 8 类（organization + 7 个事实类），等价旧行为；
      - ("qualification_certificate", "performance_case") → 只读 organization + 这两类。
    organization 总是读取（单对象、开销极小），故始终返回该键。
    """
    facts: dict[str, Any] = {"organization": None}
    orgs = m3.list_objects("organization", limit=1)
    if orgs:
        facts["organization"] = _row_to_camel("organization", orgs[0])

    target = FACT_LIST_KEYS if keys is None else ((keys,) if isinstance(keys, str) else tuple(keys))
    invalid = [key for key in target if key != "organization" and key not in FACT_LIST_KEYS]
    if invalid:
        raise ValueError(
            f"未知的 fact 类别：{', '.join(sorted(set(invalid)))}；"
            f"可用值：organization, {', '.join(FACT_LIST_KEYS)}"
        )
    for key in target:
        if key == "organization":
            continue
        facts[key] = [_row_to_camel(key, r) for r in m3.list_objects(key, limit=100000)]
    return facts


def read_organization_from_m3(m3: M3Client) -> dict[str, Any] | None:
    """从 M3 读取企业组织（CamelCase）；无记录返回 None。"""
    orgs = m3.list_objects("organization", limit=1)
    return _row_to_camel("organization", orgs[0]) if orgs else None
