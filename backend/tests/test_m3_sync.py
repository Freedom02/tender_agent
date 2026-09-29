from __future__ import annotations

import unittest

from backend.app.document_recognition.storage import FACT_LIST_KEYS
from backend.app.m3.client import M3Client
from backend.app.m3.sync import read_facts_from_m3, reuse_existing_products


class _FakeM3:
    """只记录 list_objects 的调用，不真正联网。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def list_objects(self, class_snake: str, limit: int = 100, where=None) -> list[dict]:
        self.calls.append(class_snake)
        return []


class _ExistingProductsM3(_FakeM3):
    """technical_product 返回两条库内既有产品（snake_case 行 + RID）。"""

    def __init__(self) -> None:
        super().__init__()
        self.rows = [
            {
                "id": "tenderhub_technical_product:prd-old-1",
                "product_name": "ThingJS-X数字孪生可视化系统[简称:ThingJS-X]V4.1",
                "aliases": "数字孪生基础平台、ThingJS-X",
            },
            {
                "id": "tenderhub_technical_product:prd-old-2",
                "product_name": "数字孪生基础课程",
                "aliases": "",
            },
        ]

    def list_objects(self, class_snake: str, limit: int = 100, where=None) -> list[dict]:
        self.calls.append(class_snake)
        if class_snake == "technical_product":
            return self.rows
        return []


class _BrokenM3(_FakeM3):
    def list_objects(self, class_snake: str, limit: int = 100, where=None) -> list[dict]:
        raise RuntimeError("M3 不可用")


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


class _SigninThenOkM3(M3Client):
    def __init__(self) -> None:
        super().__init__(host="m3.local", company="c", username="u", password="p")
        self.login_calls = 0
        self.post_calls = 0

    def login(self) -> bool:
        self.login_calls += 1
        self._logged_in = True
        return True

    def _post_mql(self, sql: str) -> dict:
        if not self._logged_in:
            self.login()
        self.post_calls += 1
        if self.post_calls == 1:
            return {"message": "/user/login", "status": "signin"}
        return {"message": [], "status": "ok"}


class M3ClientSessionTests(unittest.TestCase):
    def test_mql_relogs_once_when_session_expires(self) -> None:
        m3 = _SigninThenOkM3()

        resp = m3.mql("select * from tenderhub_organization limit 100;")

        self.assertEqual(resp["status"], "ok")
        self.assertEqual(m3.post_calls, 2)
        self.assertEqual(m3.login_calls, 2)


class ReuseExistingProductsTests(unittest.TestCase):
    """产品级防重：同名/同别名产品复用库内对象，参数外键改指库内 productId。"""

    @staticmethod
    def _facts(product_name: str, pid: str = "prd-new-1") -> dict:
        return {
            "organization": None,
            "technical_product": [{"productId": pid, "productName": product_name, "aliases": ""}],
            "technical_parameter": [
                {"parameterId": "prm-new-1", "productId": pid, "description": "参数A"},
                {"parameterId": "prm-new-2", "productId": pid, "description": "参数B"},
            ],
            "credential": [{"credentialId": "crd-new-1", "productId": pid}],
        }

    def test_exact_name_reuses_and_remaps_children(self) -> None:
        m3 = _ExistingProductsM3()
        warnings: list[str] = []
        facts = self._facts("ThingJS-X数字孪生可视化系统[简称:ThingJS-X]V4.1")

        reused = reuse_existing_products(m3, facts, warnings)

        self.assertEqual(len(reused), 1)
        self.assertEqual(reused[0]["reusedProductId"], "prd-old-1")
        # 产品行被移除，不重复写入
        self.assertEqual(facts["technical_product"], [])
        # 参数/证照外键全部改指库内对象
        self.assertTrue(all(p["productId"] == "prd-old-1" for p in facts["technical_parameter"]))
        self.assertEqual(facts["credential"][0]["productId"], "prd-old-1")
        self.assertTrue(any("复用库内对象" in w for w in warnings))

    def test_alias_match_reuses(self) -> None:
        m3 = _ExistingProductsM3()
        facts = self._facts("数字孪生基础平台", pid="prd-new-9")

        reused = reuse_existing_products(m3, facts, [])

        self.assertEqual(len(reused), 1)
        self.assertEqual(reused[0]["reusedProductId"], "prd-old-1")
        self.assertEqual(facts["technical_parameter"][0]["productId"], "prd-old-1")

    def test_new_product_is_kept(self) -> None:
        m3 = _ExistingProductsM3()
        facts = self._facts("智慧楼宇项目实战平台-楼宇数字孪生管理平台")

        reused = reuse_existing_products(m3, facts, [])

        self.assertEqual(reused, [])
        self.assertEqual(len(facts["technical_product"]), 1)
        self.assertEqual(facts["technical_parameter"][0]["productId"], "prd-new-1")

    def test_ambiguous_alias_is_not_reused(self) -> None:
        m3 = _ExistingProductsM3()
        # 两个库内产品共享同一个别名「平台」→ 归一化歧义，不复用
        m3.rows[0]["aliases"] = "平台"
        m3.rows[1]["aliases"] = "平台"
        warnings: list[str] = []
        facts = self._facts("平台")

        reused = reuse_existing_products(m3, facts, warnings)

        self.assertEqual(reused, [])
        self.assertEqual(len(facts["technical_product"]), 1)
        self.assertTrue(any("多个产品" in w for w in warnings))

    def test_m3_unavailable_falls_back_to_old_behavior(self) -> None:
        m3 = _BrokenM3()
        warnings: list[str] = []
        facts = self._facts("ThingJS-X数字孪生可视化系统[简称:ThingJS-X]V4.1")

        reused = reuse_existing_products(m3, facts, warnings)

        self.assertEqual(reused, [])
        self.assertEqual(len(facts["technical_product"]), 1)
        self.assertTrue(any("跳过产品级防重" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
