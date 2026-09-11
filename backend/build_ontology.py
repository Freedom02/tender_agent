# -*- coding: utf-8 -*-
"""本体建模（一次性）：把 9 类 + 16 关系的静态 schema 生成 M3 工作区本体文件。

仅在本体变更时运行（新增/修改类或关系、改字段、改命名空间）。
**不跑 xlsx 解析**，因此不依赖任何数据。

输出：
  {project}/M3_DataGov_Output/02_ontology_modeling/ontology_model.json
  {project}/M3_DataGov_Output/02_ontology_modeling/relationship_mapping.json

用法：
  python build_ontology.py --project-dir "E:\\...\\M3_workspace"
"""
import argparse
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent))

from app.document_recognition.ontology_defs import (  # noqa: E402
    CLASS_CN, CLASS_SCHEMA, NAMESPACE, REL_MAPPING, RELATIONSHIPS,
    attr_cn, camel_to_snake,
)


def build_ontology_model():
    classes = []
    for cls, attrs_schema in CLASS_SCHEMA.items():
        cn_name, cn_desc = CLASS_CN[cls]
        attrs = [
            {
                "attrName": camel_to_snake(name),
                "attrNameCn": attr_cn(name),
                "dataType": m3_type,
                "isPrimaryKey": bool(is_pk),
                "description": attr_cn(name),
            }
            for name, m3_type, is_pk in attrs_schema
        ]
        classes.append({
            "className": camel_to_snake(cls),
            "classNameCn": cn_name,
            "description": cn_desc,
            "attributes": attrs,
        })

    relationships = []
    for rel_name, from_cls, to_cls, _spk, _sfk, _tpk, _tjoin in REL_MAPPING:
        cn_desc = next((r["desc"] for r in RELATIONSHIPS if r["relationName"] == rel_name), rel_name)
        relationships.append({
            "edge_name": rel_name,
            "fromClass": camel_to_snake(from_cls),
            "toClass": camel_to_snake(to_cls),
            "description": cn_desc,
        })

    return {
        "version": "1.0",
        "namespace": NAMESPACE,
        "classes": classes,
        "relationships": relationships,
    }


def build_relationship_mapping():
    return {
        "version": "3.0",
        "namespace": NAMESPACE,
        "relationships": [
            {
                "relationName": rel_name,
                "fromClass": camel_to_snake(from_cls),
                "toClass": camel_to_snake(to_cls),
                "sourcePrimaryKeyField": camel_to_snake(spk),
                "sourceForeignKeyField": camel_to_snake(sjoin),
                "targetPrimaryKeyField": camel_to_snake(tpk),
                "targetJoinField": camel_to_snake(tjoin),
            }
            for rel_name, from_cls, to_cls, spk, sjoin, tpk, tjoin in REL_MAPPING
        ],
    }


def main():
    parser = argparse.ArgumentParser(description="本体建模（一次性）")
    parser.add_argument("--project-dir", required=True, help="M3 工作区路径")
    args = parser.parse_args()

    out = Path(args.project_dir) / "M3_DataGov_Output"
    modeling_dir = out / "02_ontology_modeling"
    modeling_dir.mkdir(parents=True, exist_ok=True)

    ontology_model = build_ontology_model()
    (modeling_dir / "ontology_model.json").write_text(
        json.dumps(ontology_model, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== ontology_model.json ==\n   {len(ontology_model['classes'])} 类, "
          f"{len(ontology_model['relationships'])} 关系")

    rel_map = build_relationship_mapping()
    (modeling_dir / "relationship_mapping.json").write_text(
        json.dumps(rel_map, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"== relationship_mapping.json ==\n   {len(rel_map['relationships'])} 条连接契约")

    print(f"\n完成！本体文件 → {modeling_dir}")


if __name__ == "__main__":
    main()
