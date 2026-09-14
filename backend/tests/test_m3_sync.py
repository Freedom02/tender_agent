from __future__ import annotations

import unittest

from backend.app.document_recognition.storage import FACT_LIST_KEYS
from backend.app.m3.sync import read_facts_from_m3


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

        # organization 始终读取
        self.assertIn("organization", facts)
        # 指定的两类被读取
        self.assertIn("qualification_certificate", facts)
        self.assertIn("performance_case", facts)
        # 未指定的类不出现
        self.assertNotIn("staff_member", facts)
        self.assertNotIn("credential", facts)
        # 实际发起的查询只含 organization + 指定两类
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
            read_facts_from_m3(m3, keys=("qualifications",))  # 匹配层 key，非 fact 类名


if __name__ == "__main__":
    unittest.main()
