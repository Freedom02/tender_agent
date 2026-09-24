from __future__ import annotations

import unittest

from backend.app.tender_matching.table_semantics import (
    build_table_row_semantics,
    semantics_for_segment,
)


class TableSemanticsTests(unittest.TestCase):
    def test_extracts_product_name_and_carries_it_to_continuation_rows(self) -> None:
        tables = [
            {
                "tableId": "TABLE-TECH",
                "rows": [
                    {
                        "rowIndex": 1,
                        "cells": [
                            {"text": "序号"},
                            {"text": "品目名称"},
                            {"text": "简要技术指标"},
                        ],
                    },
                    {
                        "rowIndex": 2,
                        "cells": [
                            {"text": "1"},
                            {"text": "◆数字孪生应用实训平台 （核心产品）"},
                            {"text": "支持三维场景"},
                        ],
                    },
                    {
                        "rowIndex": 3,
                        "cells": [
                            {"text": ""},
                            {"text": ""},
                            {"text": "支持模型导入"},
                        ],
                    },
                ],
            }
        ]

        semantics = build_table_row_semantics(tables)

        self.assertEqual(
            semantics[("TABLE-TECH", 2)],
            {
                "productName": "数字孪生应用实训平台",
                "targetType": "product",
                "sourceColumn": "品目名称",
            },
        )
        self.assertEqual(semantics[("TABLE-TECH", 3)]["productName"], "数字孪生应用实训平台")
        self.assertEqual(
            semantics_for_segment(
                {"tableId": "TABLE-TECH", "rowIndex": 3},
                semantics,
            )["targetType"],
            "product",
        )

    def test_does_not_treat_a_response_column_as_product_name(self) -> None:
        tables = [
            {
                "tableId": "TABLE-RESPONSE",
                "rows": [
                    {
                        "rowIndex": 1,
                        "cells": [
                            {"text": "序号"},
                            {"text": "技术和服务要求"},
                            {"text": "投标响应"},
                        ],
                    },
                    {
                        "rowIndex": 2,
                        "cells": [
                            {"text": "*"},
                            {"text": ""},
                            {"text": ""},
                        ],
                    },
                ],
            }
        ]

        self.assertEqual(build_table_row_semantics(tables), {})


if __name__ == "__main__":
    unittest.main()
