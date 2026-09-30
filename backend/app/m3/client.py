# -*- coding: utf-8 -*-
"""M3 图数据库客户端：Cookie Session 登录 + MQL 执行 + 本体 CRUD。

连接信息来源优先级（从高到低）：
  1. 显式构造参数
  2. 环境变量 M3_HOST / M3_COMPANY / M3_USER / M3_PASSWORD
  3. agent-config.json 的 m3 段（由 build_m3_client 传入）

所有对象 RID 统一为 `<namespace>_<class_snake>:<business_id>`（如
`tenderhub_organization:org-001`），与建类 DDL 的 nickname、对象写入、关系端点保持一致。
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

UA = "Mozilla/5.0"
_ID_SAFE = re.compile(r"[A-Za-z0-9._-]+")
DEFAULT_HOST = os.environ.get("M3_HOST", "")
DEFAULT_COMPANY = os.environ.get("M3_COMPANY", "")
DEFAULT_USER = os.environ.get("M3_USER", "")
DEFAULT_PASSWORD = os.environ.get("M3_PASSWORD", "")


class M3Error(RuntimeError):
    """M3 交互异常（登录失败 / MQL 报错 / 非 JSON 响应）。"""


def _is_signin_response(resp: dict[str, Any]) -> bool:
    """M3 会话失效时常返回 {'message': '/user/login', 'status': 'signin'}。"""
    status = str(resp.get("status") or "").lower()
    message = str(resp.get("message") or "")
    return status == "signin" or "/user/login" in message


class M3Client:
    def __init__(
        self,
        host: str = "",
        company: str = "",
        username: str = "",
        password: str = "",
        namespace: str = "tenderhub",
    ) -> None:
        self.host = host or os.environ.get("M3_HOST", DEFAULT_HOST)
        self.company = company or os.environ.get("M3_COMPANY", DEFAULT_COMPANY)
        self.username = username or os.environ.get("M3_USER", DEFAULT_USER)
        self.password = password or os.environ.get("M3_PASSWORD", DEFAULT_PASSWORD)
        self.namespace = namespace

        self._jar = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._jar))
        self._lock = threading.Lock()
        self._logged_in = False

    # ------------------------------------------------------------------ 认证
    def login(self) -> bool:
        with self._lock:
            html = self._opener.open(
                urllib.request.Request(f"http://{self.host}/user/login", headers={"User-Agent": UA}),
                timeout=20,
            ).read().decode("utf-8", errors="replace")
            m = re.search(r'<meta name="_csrf" content="([^"]+)"', html)
            if not m:
                raise M3Error("登录页未找到 CSRF token")
            csrf = m.group(1)
            data = urllib.parse.urlencode(
                {"_csrf": csrf, "company": self.company, "user_name": self.username, "password": self.password}
            ).encode()
            req = urllib.request.Request(f"http://{self.host}/user/login", data=data, method="POST")
            req.add_header("User-Agent", UA)
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            req.add_header("X-Requested-With", "XMLHttpRequest")
            req.add_header("Origin", f"http://{self.host}")
            req.add_header("Referer", f"http://{self.host}/user/login")
            r = self._opener.open(req, timeout=20)
            r.read()
            self._logged_in = "user/login" not in r.geturl()
            return self._logged_in

    # ------------------------------------------------------------------ MQL
    def mql(self, sql: str) -> dict[str, Any]:
        """执行一条 MQL 并返回解析后的 JSON。会话失效时自动重登一次。"""
        resp = self._post_mql(sql)
        if _is_signin_response(resp):
            self._logged_in = False
            resp = self._post_mql(sql)
        if _is_signin_response(resp):
            raise M3Error("M3 会话已失效或登录失败，请检查 M3_HOST/M3_COMPANY/M3_USER/M3_PASSWORD 或在配置中更新 m3 账号")
        if resp.get("status") != "ok":
            raise M3Error(f"MQL 执行失败：{str(resp)[:300]}\n语句：{sql[:200]}")
        return resp

    def _post_mql(self, sql: str) -> dict[str, Any]:
        if not self._logged_in:
            if not self.login():
                raise M3Error("M3 登录失败（未跳转到 /home）")
        req = urllib.request.Request(f"http://{self.host}/mxobject/mql", data=sql.encode("utf-8"), method="POST")
        req.add_header("User-Agent", UA)
        req.add_header("Content-Type", "text/plain")
        req.add_header("Accept", "application/json")
        req.add_header("X-Requested-With", "XMLHttpRequest")
        req.add_header("Origin", f"http://{self.host}")
        try:
            with self._lock:
                r = self._opener.open(req, timeout=90)
                raw = r.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise M3Error(f"M3 HTTP {e.code}：{raw[:300]}\n语句：{sql[:200]}")
        except Exception as e:  # noqa: BLE001
            raise M3Error(f"M3 请求异常：{type(e).__name__}: {e}\n语句：{sql[:200]}") from e
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise M3Error(f"M3 返回非 JSON：{raw[:300]}") from e

    # ------------------------------------------------------------------ 工具
    def class_nickname(self, class_snake: str) -> str:
        return f"{self.namespace}_{class_snake}"

    def rid(self, class_snake: str, business_id: str) -> str:
        bid = str(business_id)
        if not _ID_SAFE.fullmatch(bid):
            raise M3Error(f"非法对象 ID：{bid!r}（仅允许字母/数字/._-）")
        return f"{self.class_nickname(class_snake)}:{bid}"

    @staticmethod
    def _messages(resp: dict[str, Any]) -> list[dict[str, Any]]:
        msg = resp.get("message", [])
        return [m for m in msg if isinstance(m, dict)]

    # ------------------------------------------------------------------ 对象 CRUD
    def list_objects(self, class_snake: str, limit: int = 100, where: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        nick = self.class_nickname(class_snake)
        sql = f"select * from {nick}"
        if where:
            clauses = []
            for k, v in where.items():
                if not _ID_SAFE.fullmatch(str(k)):
                    raise M3Error(f"非法字段名：{k!r}")
                clauses.append(f"{k} = {self._sql_literal(v)}")
            if clauses:
                sql += " where " + " and ".join(clauses)
        sql += f" limit {int(limit)};"
        return self._messages(self.mql(sql))

    def get_object(self, class_snake: str, business_id: str) -> dict[str, Any] | None:
        nick = self.class_nickname(class_snake)
        rid = self.rid(class_snake, business_id)
        rows = self._messages(self.mql(f"select * from {nick} where id = {self._sql_literal(rid)};"))
        return rows[0] if rows else None

    def count_objects(self, class_snake: str) -> int:
        nick = self.class_nickname(class_snake)
        rows = self._messages(self.mql(f"select count(1) from {nick};"))
        try:
            return int(rows[0].get("count", 0))
        except (IndexError, TypeError, ValueError):
            return 0

    def upsert_object(self, class_snake: str, fields: dict[str, Any], business_id: str) -> dict[str, Any]:
        """插入或更新对象。fields 为 snake_case 属性名→值；business_id 为业务主键。"""
        rid = self.rid(class_snake, business_id)
        cols = ["id"]
        vals = [self._sql_literal(rid)]
        for k, v in fields.items():
            cols.append(k)
            vals.append(self._sql_literal(v))
        self.mql(f"insert into /{self.namespace}/{class_snake}({', '.join(cols)}) values({', '.join(vals)});")
        return self.get_object(class_snake, business_id) or {"id": rid}

    def delete_object(self, class_snake: str, business_id: str) -> bool:
        rid = self.rid(class_snake, business_id)
        self.mql(f"delete from /{self.namespace}/{class_snake} where id = {self._sql_literal(rid)};")
        return self.get_object(class_snake, business_id) is None

    # ------------------------------------------------------------------ 关系 CRUD
    def create_edge(self, edge: str, src_rid: str, tgt_rid: str) -> dict[str, Any]:
        return self.mql(f"create ('{src_rid}')-[:{edge}]->('{tgt_rid}');")

    def delete_edge(self, edge: str, src_rid: str, tgt_rid: str) -> dict[str, Any]:
        return self.mql(f"delete ('{src_rid}')-[:{edge}]->('{tgt_rid}');")

    def match_edges(self, rid: str, edge: str | None = None, direction: str = "out") -> list[dict[str, Any]]:
        """查询对象的一跳关系。direction: out | in | both。"""
        if edge:
            pattern = f"[:{edge}]"
        else:
            pattern = "[*1]"
        if direction == "in":
            sql = f'match("{rid}")<-{pattern}-() with attr=(id,name,.*);'
        elif direction == "both":
            sql = f'match("{rid}")-{pattern}-() with attr=(id,name,.*);'
        else:
            sql = f'match("{rid}")-{pattern}->() with attr=(id,name,.*);'
        resp = self.mql(sql)
        graphs = [m.get("graph", {}) for m in self._messages(resp)]
        edges: list[dict[str, Any]] = []
        for g in graphs:
            edges.extend(e for e in g.get("edges", []) if isinstance(e, dict))
        return edges

    # ------------------------------------------------------------------ 字面量
    @staticmethod
    def _sql_literal(v: Any) -> str:
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (int, float)):
            return str(v)
        if isinstance(v, (list, tuple, dict)):
            return "'" + json.dumps(v, ensure_ascii=False).replace("'", "''") + "'"
        s = str(v)
        return "'" + s.replace("'", "''") + "'"
