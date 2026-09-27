from __future__ import annotations

import unittest

from backend.app.bid_writing.template_reuse import (
    fill_format_item_without_template,
    source_table_for_section,
)


class FormatItemFallbackTests(unittest.TestCase):
    def test_new_format_item_id_uses_matching_source_table(self) -> None:
        section = {
            "sectionId": "economic-01",
            "title": "开标一览表",
            "itemType": "fixed_table",
            "responseType": "table",
        }
        tables = [
            {
                "id": "TABLE-009",
                "rows": [
                    {
                        "cells": [
                            {"text": "商品名称"},
                            {"text": "规格型号"},
                            {"text": "综合单价"},
                        ]
                    },
                    {"cells": [{"text": "合计人民币（小写）"}]},
                ],
            },
            {
                "id": "TABLE-010",
                "rows": [
                    {
                        "cells": [
                            {"text": "投标人名称"},
                            {"text": ""},
                        ]
                    },
                    {
                        "cells": [
                            {"text": "包号及名称"},
                            {"text": "投标报价（小写）"},
                        ]
                    },
                    {"cells": [{"text": "投标报价（大写）"}]},
                ],
            },
        ]

        self.assertEqual(source_table_for_section(section, tables)["id"], "TABLE-010")
        result = fill_format_item_without_template(
            section,
            {"title": "示例项目"},
            {},
            {"company": "示例公司"},
            tables=tables,
        )
        self.assertIn("# 开标一览表", result["content"])
        self.assertIn("投标报价（小写）", result["content"])
        self.assertTrue(result["filledTables"])

    def test_fixed_form_without_source_template_is_not_empty(self) -> None:
        result = fill_format_item_without_template(
            {
                "title": "投标函（格式）",
                "itemType": "fixed_form",
                "responseType": "fixed_form",
            },
            {"title": "示例项目"},
            {},
            {"company": "示例公司"},
            tables=[],
        )
        self.assertIn("示例项目", result["content"])
        self.assertIn("示例公司", result["content"])
        self.assertTrue(result["blanksToFill"])

    def test_fixed_form_does_not_borrow_an_unrelated_table(self) -> None:
        result = fill_format_item_without_template(
            {
                "title": "投标函",
                "itemType": "fixed_form",
                "responseType": "fixed_form",
            },
            {"title": "示例项目"},
            {},
            {"company": "示例公司"},
            tables=[
                {
                    "id": "TABLE-010",
                    "rows": [
                        {"cells": [{"text": "投标人名称"}, {"text": ""}]},
                        {"cells": [{"text": "投标报价（小写）"}]},
                    ],
                }
            ],
        )
        self.assertEqual(result["filledTables"], [])
        self.assertIn("请依据招标文件固定格式", result["content"])


if __name__ == "__main__":
    unittest.main()
