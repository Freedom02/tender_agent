from __future__ import annotations

import unittest

from backend.app.document_recognition.storage import FACT_LIST_KEYS
from backend.app.m3.sync import _row_to_camel, read_facts_from_m3


class _FakeM3:
    """只记录 list_objects 的调用，不真正联网。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_objects(self, class_snake: str, limit: int = 100, where=None) -> list[dict]:
        self.calls.append(class_snake)
        return []


class ReadFactsFromM3Tests(unittest.TestCase):
    def test_default_reads_all_fact_classes(self) -> None:
        m3 = _FakeM3()
        facts = read_facts_from_m3(m3)

        self.assertIn("organization", facts)
        for key in FACT_LIST_KEYS:
            self.assertIn(key, facts, f"默认读取应包含 {key}")
        self.assertIn("organization", m3.calls)

    def test_keys_selects_only_requested_fact_classes(self) -> None:
        m3 = _FakeM3()
        facts = read_facts_from_m3(m3, keys=("qualification_certificate", "performance_case"))

        self.assertIn("organization", facts)
        self.assertIn("qualification_certificate", facts)
        self.assertIn("performance_case", facts)
        self.assertNotIn("staff_member", facts)
        self.assertNotIn("credential", facts)
        self.assertEqual(
            sorted(m3.calls),
            sorted(["organization", "qualification_certificate", "performance_case"]),
        )

    def test_bare_string_key_is_accepted(self) -> None:
        m3 = _FakeM3()
        facts = read_facts_from_m3(m3, keys="qualification_certificate")
        self.assertIn("qualification_certificate", facts)
        self.assertNotIn("performance_case", facts)

    def test_invalid_key_raises(self) -> None:
        m3 = _FakeM3()
        with self.assertRaises(ValueError):
            read_facts_from_m3(m3, keys=("qualifications",))

    def test_row_to_camel_restores_business_id_from_m3_rid(self) -> None:
        product = _row_to_camel(
            "technical_product",
            {
                "id": "tenderhub_technical_product:prd-1",
                "product_name": "数字孪生平台",
            },
        )
        parameter = _row_to_camel(
            "technical_parameter",
            {
                "id": "tenderhub_technical_parameter:param-1",
                "product_id": "prd-1",
                "description": "支持三维场景加载",
            },
        )

        self.assertEqual(product["productId"], "prd-1")
        self.assertEqual(parameter["parameterId"], "param-1")
        self.assertEqual(parameter["productId"], "prd-1")


if __name__ == "__main__":
    unittest.main()
