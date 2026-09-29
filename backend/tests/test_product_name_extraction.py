from __future__ import annotations

import unittest

from backend.app.tender_matching.document_models import (
    DocumentIR,
    DocumentSection,
    DocumentSegment,
    DocumentTable,
)
from backend.app.tender_matching.requirement_extractor import extract_requirements
from backend.app.tender_matching.section_classifier import classify_document


class ProductNameExtractionTests(unittest.TestCase):
    def _table(self) -> DocumentTable:
        return DocumentTable(
            tableId="TABLE-TECH",
            rows=[
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
                        {"text": "◆数字孪生应用实训平台（核心产品）"},
                        {"text": "1、平台必须支持三维场景加载。"},
                    ],
                },
            ],
        )

    def test_document_ir_path_keeps_product_metadata(self) -> None:
        segment = DocumentSegment(
            id="SEG-TECH",
            tableId="TABLE-TECH",
            rowIndex=2,
            sourceLocator="table row 2",
            contentType="table_row",
            text="1 | 数字孪生应用实训平台 | 1、平台必须支持三维场景加载。",
            cells=[
                {"text": "1"},
                {"text": "数字孪生应用实训平台"},
                {"text": "1、平台必须支持三维场景加载。"},
            ],
        )
        table = self._table()
        table.rows[1].cells[1].text = "数字孪生应用实训平台"
        document = DocumentIR(
            documentId="DOC-TECH",
            documentVersionId="DOC-TECH-V001",
            text=segment.text,
            segments=[segment],
            units=[segment],
            tables=[table],
            sections=[
                DocumentSection(
                    id="SEC-TECH",
                    sectionId="SEC-TECH",
                    sectionType="technical",
                    heading="第五章 技术要求",
                    sectionPath=["第五章 技术要求"],
                    segmentIds=["SEG-TECH"],
                )
            ],
        )

        result = extract_requirements(
            document,
            classifications=classify_document(document),
        )

        self.assertEqual(len(result.requirements), 1)
        self.assertEqual(result.requirements[0].product_name, "数字孪生应用实训平台")
        self.assertEqual(result.requirements[0].source_column, "品目名称")


if __name__ == "__main__":
    unittest.main()
