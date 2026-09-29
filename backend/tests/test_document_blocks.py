"""documentBlocks 的 units/segments 单一来源约定。

documentBlocks 只持久化 units（叶子单元）；segments 是 units 中“非表格单元格”
的子集，该关系由 document_parser 各分支的构造保证，因此不再重复落盘，
读取时按需还原。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.store import Store
from backend.app.tender_matching.document_models import (
    derive_segments_from_units,
    document_ir_from_parsed,
    document_segments,
    strip_derivable_segments,
)


def _unit(unit_id: str, content_type: str) -> dict[str, object]:
    return {
        "id": unit_id,
        "text": f"{unit_id} 正文",
        "contentType": content_type,
        "order": 0,
    }


class DocumentBlocksSingleSourceTests(unittest.TestCase):
    def test_derive_segments_filters_table_cells_only(self) -> None:
        # docx 分支：units = segments ∪ 表格单元格单元
        units = [
            _unit("SEG-000001", "paragraph"),
            _unit("SEG-000002", "table_row"),
            _unit("SEG-000003", "table_cell"),
            _unit("SEG-000004", "table_cell_paragraph"),
        ]

        derived = derive_segments_from_units(units)

        self.assertEqual(
            [item["id"] for item in derived],
            ["SEG-000001", "SEG-000002"],
        )

    def test_derive_segments_keeps_everything_when_units_are_a_copy(self) -> None:
        # pdf/文本分支：units = segments.copy()
        units = [_unit("SEG-000001", "paragraph"), _unit("SEG-000002", "paragraph")]

        self.assertEqual(len(derive_segments_from_units(units)), 2)

    def test_document_segments_prefers_persisted_then_derives(self) -> None:
        persisted = {"segments": [_unit("SEG-000009", "paragraph")], "units": []}
        self.assertEqual(
            [item["id"] for item in document_segments(persisted)],
            ["SEG-000009"],
        )

        derived = {
            "units": [
                _unit("SEG-000001", "paragraph"),
                _unit("SEG-000002", "table_cell"),
            ]
        }
        self.assertEqual(
            [item["id"] for item in document_segments(derived)],
            ["SEG-000001"],
        )

    def test_strip_derivable_segments_drops_only_when_units_present(self) -> None:
        stripped = strip_derivable_segments(
            {
                "segments": [_unit("SEG-000001", "paragraph")],
                "units": [_unit("SEG-000001", "paragraph")],
            }
        )
        self.assertNotIn("segments", stripped)
        self.assertTrue(stripped["units"])

        # units 为空说明该分支没产出叶子单元，保留 segments 以免丢正文。
        kept = strip_derivable_segments(
            {"segments": [_unit("SEG-000001", "paragraph")], "units": []}
        )
        self.assertIn("segments", kept)

    def test_document_ir_reconstructs_segments_from_units(self) -> None:
        document = document_ir_from_parsed(
            {
                "text": "正文",
                "units": [
                    _unit("SEG-000001", "paragraph"),
                    _unit("SEG-000002", "table_row"),
                    _unit("SEG-000003", "table_cell"),
                ],
            }
        )

        self.assertEqual(
            [segment.id for segment in document.segments],
            ["SEG-000001", "SEG-000002"],
        )
        self.assertEqual(document.diagnostics.segment_count, 2)
        self.assertEqual(document.diagnostics.unit_count, 3)

    def test_document_ir_keeps_persisted_segments_for_old_bundles(self) -> None:
        document = document_ir_from_parsed(
            {
                "text": "正文",
                "segments": [_unit("SEG-000001", "paragraph")],
                "units": [
                    _unit("SEG-000001", "paragraph"),
                    _unit("SEG-000002", "table_cell"),
                ],
            }
        )

        self.assertEqual([segment.id for segment in document.segments], ["SEG-000001"])

    def test_save_parse_bundle_persists_units_without_segments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            Store(root).save_parse_bundle(
                {
                    "parseBundleId": "parse-001",
                    "schemaVersion": "1.0",
                    "provider": "local",
                    "status": "completed",
                    "projectId": "p-01",
                    "workspaceId": "w-01",
                    "sourceFile": {"filename": "tender.docx"},
                    "documentBlocks": {
                        "text": "正文",
                        "segments": [_unit("SEG-000001", "paragraph")],
                        "units": [
                            _unit("SEG-000001", "paragraph"),
                            _unit("SEG-000002", "table_cell"),
                        ],
                    },
                }
            )

            path = (
                root / "runtime" / "projects" / "p-01" / "parse" / "parse-bundle.json"
            )
            blocks = json.loads(path.read_text(encoding="utf-8"))["documentBlocks"]

            self.assertNotIn("segments", blocks)
            self.assertEqual(len(blocks["units"]), 2)


if __name__ == "__main__":
    unittest.main()
