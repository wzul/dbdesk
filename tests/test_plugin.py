"""DB Desk automated tests — backend helpers, storage, endpoints and agent tools.

Run with the Hermes Python (the module imports FastAPI + cryptography):

    ./tests/run.sh

No database server is required: dialect calls are mocked, storage is a temp
HERMES_HOME, and the endpoint functions are exercised directly.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN / "dashboard"))

import plugin_api as api  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def conn(kind: str = "postgres", **over):
    """A fully-shaped connection record for builders/tests."""
    base = {
        "id": "c1",
        "kind": kind,
        "name": "test",
        "group": "",
        "host": "127.0.0.1",
        "port": 5432,
        "dbname": "db",
        "user": "u",
        "password": "p",
        "password_enc": "",
        "sslmode": "disable",
        "readonly": False,
        "production": False,
        "ssh_host": "",
        "ssh_user": "root",
        "ssh_port": 22,
        "ssh_key": "",
        "db_labels": {},
    }
    base.update(over)
    return base


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        os.environ["HERMES_HOME"] = str(self.home)
        (self.home / "plugin-data" / "dbdesk").mkdir(parents=True)

    def tearDown(self) -> None:
        os.environ.pop("HERMES_HOME", None)
        self._tmp.cleanup()

    @staticmethod
    def conn(kind: str = "postgres", **over):
        return conn(kind, **over)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


class TestHelpers(unittest.TestCase):
    def test_multiple_statements(self):
        self.assertFalse(api._has_multiple_statements("select 1"))
        self.assertTrue(api._has_multiple_statements("select 1; select 2"))
        self.assertFalse(api._has_multiple_statements("select ';'"))
        self.assertFalse(api._has_multiple_statements("select $$a;b$$"))
        self.assertFalse(api._has_multiple_statements('select "a;b"'))

    def test_check_sql(self):
        self.assertEqual(api._check_sql(" select 1; "), "select 1")
        with self.assertRaises(HTTPException) as ctx:
            api._check_sql("   ")
        self.assertEqual(ctx.exception.status_code, 400)
        with self.assertRaises(HTTPException):
            api._check_sql("select 1; select 2")

    def test_literals(self):
        self.assertEqual(api._lit("O'Reilly"), "'O''Reilly'")
        self.assertEqual(api._ident('a"b'), '"a""b"')
        self.assertEqual(api._my_lit("a'b\\c"), "'a\\'b\\\\c'")
        self.assertEqual(api._my_ident("a`b"), "`a``b`")
        self.assertEqual(api._ch_lit("a'b\\c"), "'a\\'b\\\\c'")

    def test_where_clause(self):
        filters = [
            {"column": "name", "op": "eq", "value": "x"},
            {"column": "gone", "op": "isnull"},
        ]
        self.assertEqual(
            api._where_clause("postgres", filters),
            '"name" = \'x\' AND "gone" IS NULL',
        )
        self.assertEqual(
            api._where_clause("mysql", [{"column": "name", "op": "contains", "value": "x"}]),
            "LOWER(CAST(`name` AS CHAR)) LIKE LOWER('%x%')",
        )
        self.assertEqual(
            api._where_clause(
                "mysql",
                [{"column": "name", "op": "contains", "value": "x", "insensitive": False}],
            ),
            "CAST(`name` AS CHAR) LIKE BINARY '%x%'",
        )
        self.assertEqual(
            api._where_clause(
                "postgres",
                [{"column": "name", "op": "startswith", "value": "x", "insensitive": False}],
            ),
            '"name"::text LIKE \'x%\'',
        )
        self.assertEqual(
            api._where_clause(
                "clickhouse",
                [{"column": "name", "op": "contains", "value": "x", "insensitive": False}],
            ),
            "positionCaseSensitiveUTF8(toString(`name`), 'x') > 0",
        )
        self.assertEqual(
            api._where_clause("clickhouse", [{"column": "n", "op": "gt", "value": "5"}]),
            "`n` > '5'",
        )

    def test_like_escape(self):
        self.assertEqual(api._like_escape("50%_x"), "50\\%\\_x")

    def test_csv_from(self):
        csv_text = api._csv_from(
            [{"name": "a"}, {"name": "b"}],
            [[1, None], ["x,y", 'q"z']],
        )
        self.assertEqual(csv_text, 'a,b\n1,\n"x,y","q""z"\n')


# --------------------------------------------------------------------------- #
# vault + storage
# --------------------------------------------------------------------------- #


class TestVault(Base):
    def test_encrypt_roundtrip(self):
        token = api._encrypt("s3cret")
        self.assertTrue(token.startswith("v1:"))
        self.assertNotIn("s3cret", token)
        self.assertEqual(api._decrypt(token), "s3cret")
        self.assertEqual(api._decrypt(""), "")

    async def test_save_connection_encrypts(self):
        saved = await api.save_connection(
            {
                "kind": "postgres",
                "name": "PG",
                "host": "127.0.0.1",
                "port": 5432,
                "dbname": "db",
                "user": "u",
                "password": "hunter2",
            }
        )
        public = saved["connection"]
        self.assertNotIn("password", public)
        self.assertNotIn("password_enc", public)
        raw = json.loads((api._store_path()).read_text())["connections"][0]
        self.assertNotIn("password", raw)
        self.assertTrue(raw["password_enc"].startswith("v1:"))
        self.assertEqual(api._get(raw["id"])["password"], "hunter2")

    def test_plaintext_migration(self):
        path = api._store_path()
        path.write_text(
            json.dumps(
                {
                    "connections": [
                        {
                            "id": "c1",
                            "name": "legacy",
                            "dbname": "db",
                            "user": "u",
                            "password": "plain",
                        }
                    ]
                }
            )
        )
        conns = api._load()
        raw = json.loads(path.read_text())["connections"][0]
        self.assertNotIn("password", raw)
        self.assertEqual(api._decrypt(raw["password_enc"]), "plain")
        self.assertEqual(conns[0]["id"], "c1")

    async def test_merge_safe_update(self):
        first = await api.save_connection(
            {
                "kind": "postgres",
                "name": "PG",
                "group": "prod",
                "host": "127.0.0.1",
                "port": 5432,
                "dbname": "db",
                "user": "u",
                "password": "pw",
                "ssh_host": "example.com",
                "db_labels": {"db": "site.example"},
            }
        )
        cid = first["connection"]["id"]
        await api.save_connection(
            {"id": cid, "kind": "postgres", "name": "PG", "dbname": "db", "user": "u"}
        )
        merged = (await api.list_connections())["connections"][0]
        self.assertEqual(merged["group"], "prod")
        self.assertEqual(merged["ssh_host"], "example.com")
        self.assertEqual(merged["db_labels"], {"db": "site.example"})
        await api.save_connection(
            {"id": cid, "kind": "postgres", "name": "PG", "dbname": "db", "user": "u", "group": ""}
        )
        cleared = (await api.list_connections())["connections"][0]
        self.assertEqual(cleared["group"], "")


# --------------------------------------------------------------------------- #
# SQL builders
# --------------------------------------------------------------------------- #


class TestBuilders(unittest.TestCase):
    def test_update(self):
        sql, is_async = api._build_update(
            conn(), "public", "users", {"id": "1"}, {"name": "O'Reilly"}
        )
        self.assertEqual(
            sql,
            'UPDATE "public"."users" SET "name" = \'O\'\'Reilly\' WHERE "id" = \'1\'',
        )
        self.assertFalse(is_async)

    def test_update_clickhouse_async(self):
        sql, is_async = api._build_update(
            conn("clickhouse"), "db", "t", {"id": "1"}, {"v": "x"}
        )
        self.assertIn("ALTER TABLE `db`.`t` UPDATE", sql)
        self.assertTrue(is_async)

    def test_delete_null_pk(self):
        sql, _ = api._build_delete(conn(), "public", "t", {"id": None})
        self.assertIn('"id" IS NULL', sql)

    def test_insert(self):
        sql, _ = api._build_insert(
            conn("mysql"), "db", "t", {"a": "1", "b": None}
        )
        self.assertEqual(
            sql,
            "INSERT INTO `db`.`t` (`a`, `b`) VALUES ('1', NULL)",
        )

    def test_insert_multi(self):
        sql = api._build_insert_multi(
            conn(), "public", "t", ["a", "b"], [["1", None], ["2", "x"]]
        )
        self.assertEqual(
            sql,
            'INSERT INTO "public"."t" ("a", "b") VALUES\n  (\'1\', NULL),\n  (\'2\', \'x\')',
        )


# --------------------------------------------------------------------------- #
# query history + views
# --------------------------------------------------------------------------- #


class TestStores(Base):
    async def test_history_dedupe(self):
        conn = self.conn()
        api._record_history(conn, "select 1", {"rows": [], "duration_ms": 5})
        api._record_history(conn, "select 1", {"rows": [], "duration_ms": 6})
        history = api._load_queries()["history"]
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["duration_ms"], 6)

    async def test_saved_queries_endpoints(self):
        saved = await api.save_query(
            {"name": "q1", "sql": "select 1", "connection_id": "c1"}
        )
        self.assertEqual(len((await api.list_saved())["saved"]), 1)
        await api.delete_saved(saved["query"]["id"])
        self.assertEqual((await api.list_saved())["saved"], [])

    async def test_views_endpoints(self):
        saved = await api.save_view(
            {
                "name": "v1",
                "connection_id": "c1",
                "database": "db",
                "schema": "public",
                "table": "t",
                "filters": [{"column": "a", "op": "eq", "value": "1"}],
                "order_by": "a",
                "limit": 50,
            }
        )
        views = (await api.list_views())["views"]
        self.assertEqual(views[0]["name"], "v1")
        await api.delete_view(saved["view"]["id"])
        self.assertEqual((await api.list_views())["views"], [])


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #


class TestEndpoints(Base):
    async def test_connection_requires_db(self):
        with self.assertRaises(HTTPException) as ctx:
            await api.save_connection({"name": "x"})
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_test_connection_mocked(self):
        await api.save_connection(
            {
                "kind": "postgres",
                "name": "PG",
                "host": "127.0.0.1",
                "port": 5432,
                "dbname": "db",
                "user": "u",
                "password": "pw",
            }
        )
        cid = (await api.list_connections())["connections"][0]["id"]
        with mock.patch.object(api, "_test", return_value={"version": "16"}):
            result = await api.test_connection({"id": cid})
        self.assertEqual(result["server"]["version"], "16")

    async def test_query_passes_readonly(self):
        conn = self.conn(readonly=True)
        calls = {}

        def fake_query(conn_arg, sql, limit, readonly):
            calls.update(sql=sql, limit=limit, readonly=readonly)
            return {"columns": [], "rows": [], "truncated": False, "duration_ms": 1}

        with mock.patch.object(api, "_connect", lambda cid: dict(conn)):
            with mock.patch.object(api, "_query", side_effect=fake_query):
                await api.run_query({"connection_id": "c1", "sql": "select 1"})
        self.assertTrue(calls["readonly"])

    async def test_mutation_preview_does_not_run(self):
        conn = self.conn()
        body = {
            "connection_id": "c1",
            "schema": "public",
            "table": "t",
            "pk": {"id": "1"},
            "changes": {"a": "2"},
        }
        with mock.patch.object(api, "_connect", lambda cid: dict(conn)):
            with mock.patch.object(api, "_run_mutation") as run:
                preview = await api.update_row({**body, "preview": True})
                run.assert_not_called()
                self.assertIn("UPDATE", preview["sql"])
                await api.update_row(body)
                run.assert_called_once()

    async def test_read_only_refuses_preview(self):
        conn = self.conn(readonly=True)
        with mock.patch.object(api, "_connect", lambda cid: dict(conn)):
            with self.assertRaises(HTTPException) as ctx:
                await api.update_row(
                    {
                        "connection_id": "c1",
                        "schema": "public",
                        "table": "t",
                        "pk": {"id": "1"},
                        "changes": {"a": "2"},
                        "preview": True,
                    }
                )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_import_preview_and_validation(self):
        conn = self.conn()
        body = {
            "connection_id": "c1",
            "schema": "public",
            "table": "t",
            "columns": ["a", "b"],
            "rows": [["1", None], ["2", "x"]],
        }
        with mock.patch.object(api, "_connect", lambda cid: dict(conn)):
            preview = await api.import_rows({**body, "preview": True})
            self.assertEqual(preview["rows"], 2)
            self.assertIn("INSERT INTO", preview["sql"])
            with self.assertRaises(HTTPException):
                await api.import_rows({**body, "rows": [["only-one"]]})
            with self.assertRaises(HTTPException):
                await api.import_rows({**body, "rows": [["1", "2"]] * 5001})

    async def test_alter_rename_preview(self):
        conn = self.conn()
        with mock.patch.object(api, "_connect", lambda cid: dict(conn)):
            result = await api.alter_table(
                {
                    "connection_id": "c1",
                    "schema": "public",
                    "table": "t",
                    "action": "rename_column",
                    "name": "old",
                    "new_name": "new",
                    "preview": True,
                }
            )
        self.assertEqual(
            result["sql"], 'ALTER TABLE "public"."t" RENAME COLUMN "old" TO "new";'
        )

    async def test_index_rejects_clickhouse(self):
        conn = self.conn("clickhouse")
        with mock.patch.object(api, "_connect", lambda cid: dict(conn)):
            with self.assertRaises(HTTPException) as ctx:
                await api.table_index(
                    {
                        "connection_id": "c1",
                        "schema": "db",
                        "table": "t",
                        "action": "create",
                        "name": "idx",
                        "columns": ["a"],
                    }
                )
        self.assertEqual(ctx.exception.status_code, 400)

    async def test_index_create_sql(self):
        conn = self.conn()
        with mock.patch.object(api, "_connect", lambda cid: dict(conn)):
            result = await api.table_index(
                {
                    "connection_id": "c1",
                    "schema": "public",
                    "table": "t",
                    "action": "create",
                    "name": "idx",
                    "columns": ["a", "b"],
                    "unique": True,
                    "preview": True,
                }
            )
        self.assertEqual(
            result["sql"], 'CREATE UNIQUE INDEX "idx" ON "public"."t" ("a", "b");'
        )


# --------------------------------------------------------------------------- #
# MCP + audit
# --------------------------------------------------------------------------- #


class TestMcpAndAudit(Base):
    def test_mcp_table_shapes(self):
        columns, rows = api._mcp_table([{"id": 1, "name": "A"}, {"id": 2, "name": "B"}])
        self.assertEqual([column["name"] for column in columns], ["id", "name"])
        self.assertEqual(rows, [[1, "A"], [2, "B"]])

        columns, rows = api._mcp_table({"columns": [{"name": "a"}], "rows": [[1]]})
        self.assertEqual(columns, [{"name": "a"}])
        self.assertEqual(rows, [[1]])

        columns, rows = api._mcp_table("| id | name |\n|---|---|\n| 1 | A |")
        self.assertEqual([column["name"] for column in columns], ["id", "name"])
        self.assertEqual(rows, [["1", "A"]])

    def test_mcp_allowlist_gate(self):
        with mock.patch.object(api, "_mcp_allowlist", return_value=[]):
            with self.assertRaises(HTTPException) as ctx:
                api._mcp_call({"mcp_server": "demo", "mcp_tool": "query"}, "select 1")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_mcp_query_and_readonly_guard(self):
        conn = self.conn("mcp", mcp_server="demo", mcp_dialect="postgres", readonly=True)
        with mock.patch.object(api, "_mcp_call", return_value=[{"n": 1}]):
            result = api._query(conn, "select 1", 10, True)
        self.assertEqual(result["rows"], [[1]])
        with self.assertRaises(HTTPException) as ctx:
            api._query(conn, "delete from t", 10, True)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_audit_log(self):
        conn = self.conn(production=False)
        api._audit(conn, "update", "UPDATE t SET a = 1")
        api._audit(conn, "delete", "DELETE FROM t", rows=1)
        entries = api._read_audit(10)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["action"], "delete")
        self.assertEqual(entries[0]["rows"], 1)


# --------------------------------------------------------------------------- #
# transports (custom tunnel + HTTP gateway)
# --------------------------------------------------------------------------- #


class TestTransports(Base):
    def test_http_headers_parse(self):
        headers = api._http_headers(
            {
                "http_headers": "# comment\nCF-Access-Client-Id: abc\n"
                "CF-Access-Client-Secret: def\nMalformed\n"
            }
        )
        self.assertEqual(
            headers, {"CF-Access-Client-Id": "abc", "CF-Access-Client-Secret": "def"}
        )

    def test_http_query_sends_headers(self):
        connection = conn(
            "http",
            http_url="https://gw.example/query",
            http_method="POST",
            http_sql_field="sql",
            http_headers="CF-Access-Client-Id: abc",
            http_headers_raw="",
            http_dialect="postgres",
        )
        connection["http_headers"] = "CF-Access-Client-Id: abc"
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"columns":[{"name":"n"}],"rows":[[1]]}'

        def fake_urlopen(request, timeout=0):
            seen["url"] = request.full_url
            seen["headers"] = {key.lower(): value for key, value in request.header_items()}
            seen["data"] = request.data
            return Response()

        with mock.patch.object(api.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = api._http_query(connection, "select 1", 10)
        self.assertEqual(result["rows"], [[1]])
        self.assertEqual(seen["url"], "https://gw.example/query")
        self.assertEqual(seen["headers"]["cf-access-client-id"], "abc")
        self.assertIn(b"select 1", seen["data"])

    def test_http_clickhouse_raw_body(self):
        connection = conn(
            "http",
            http_url="https://cloud.example:8443/?default_format=JSON",
            http_method="POST",
            http_body_mode="raw",
            http_headers="CF-Access-Client-Id: abc",
            http_dialect="clickhouse",
            http_sql_field="sql",
        )
        connection["http_headers"] = "CF-Access-Client-Id: abc"
        seen = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return b'{"meta":[{"name":"n","type":"UInt8"}],"data":[{"n":1}]}'

        def fake_urlopen(request, timeout=0):
            seen["data"] = request.data
            seen["headers"] = {key.lower(): value for key, value in request.header_items()}
            return Response()

        with mock.patch.object(api.urllib.request, "urlopen", side_effect=fake_urlopen):
            result = api._http_query(connection, "select 1", 10)
        self.assertEqual(result["columns"], [{"name": "n", "type": "UInt8"}])
        self.assertEqual(result["rows"], [[1]])
        self.assertEqual(seen["data"], b"select 1")
        self.assertIn("text/plain", seen["headers"]["content-type"])

    async def test_http_requires_url(self):
        with self.assertRaises(HTTPException) as ctx:
            await api.save_connection(
                {"kind": "http", "name": "GW", "dbname": "app", "mcp_server": ""}
            )
        self.assertEqual(ctx.exception.status_code, 400)

    def test_command_tunnel_requires_port(self):
        with self.assertRaises(HTTPException) as ctx:
            api._command_tunnel({"id": "t", "host": "127.0.0.1", "port": 0, "tunnel_command": "true"})
        self.assertEqual(ctx.exception.status_code, 400)


# --------------------------------------------------------------------------- #
# approvals + bulk delete
# --------------------------------------------------------------------------- #


class TestApprovals(Base):
    async def test_production_write_needs_approval(self):
        conn = self.conn(production=True, readonly=False)
        api._save([conn])
        body = {
            "connection_id": "c1",
            "schema": "public",
            "table": "t",
            "pk": {"id": "1"},
            "changes": {"a": "1"},
        }
        with mock.patch.object(api, "_connect", lambda cid: dict(conn)):
            with mock.patch.object(api, "_run_mutation"):
                with self.assertRaises(HTTPException) as ctx:
                    await api.update_row(body)
                self.assertEqual(ctx.exception.status_code, 403)

                preview = await api.update_row({**body, "preview": True})
                with mock.patch.object(api, "_approval_notice"):
                    approval = (
                        await api.create_approval(
                            {
                                "connection_id": "c1",
                                "sql": preview["sql"],
                                "action": "update",
                                "schema": "public",
                                "table": "t",
                            }
                        )
                    )["approval"]
                await api.decide_approval(approval["id"], {"decision": "approve"})
                result = await api.update_row({**body, "approval_id": approval["id"]})
                self.assertTrue(result["ok"])
                with self.assertRaises(HTTPException):
                    await api.update_row({**body, "approval_id": approval["id"]})

    async def test_bulk_delete_preview(self):
        conn = self.conn(readonly=False)
        api._save([conn])
        with mock.patch.object(api, "_connect", lambda cid: dict(conn)):
            result = await api.delete_rows_bulk(
                {
                    "connection_id": "c1",
                    "schema": "public",
                    "table": "t",
                    "pks": [{"id": "1"}, {"id": "2"}],
                    "preview": True,
                }
            )
        self.assertEqual(result["rows"], 2)
        self.assertIn("DELETE FROM", result["sql"])


# --------------------------------------------------------------------------- #
# agent tools
# --------------------------------------------------------------------------- #


class TestAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("dbdesk_agent", PLUGIN / "__init__.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)
        cls.tools = {}

        class Ctx:
            def register_tool(self, **kwargs):
                cls.tools[kwargs["name"]] = kwargs["handler"]

        cls.mod.register(Ctx())

    def setUp(self):
        self.mod._API = mock.Mock()

    def test_tools_registered(self):
        self.assertEqual(set(self.tools), {"db_connections", "db_query"})

    def test_connections(self):
        self.mod._API._load.return_value = [
            {
                "id": "c1",
                "name": "PG",
                "kind": "postgres",
                "dbname": "db",
                "readonly": True,
                "production": True,
                "group": "prod",
            }
        ]
        payload = json.loads(self.tools["db_connections"]({}))
        self.assertEqual(payload["connections"][0]["id"], "c1")
        self.assertNotIn("password", payload["connections"][0])

    def test_query_success(self):
        self.mod._API._connect.return_value = {"id": "c1", "readonly": True}
        self.mod._API._check_sql.side_effect = lambda sql: sql
        self.mod._API.READ_SQL = re.compile(r"^(select|with)\b", re.I)
        self.mod._API._query.return_value = {
            "columns": [{"name": "n"}],
            "rows": [["1"]],
            "truncated": False,
            "duration_ms": 2,
        }
        payload = json.loads(
            self.tools["db_query"]({"connection_id": "c1", "sql": "select 1"})
        )
        self.assertEqual(payload["rows"], [["1"]])

    def test_query_refuses_write(self):
        self.mod._API._connect.return_value = {"id": "c1"}
        self.mod._API._check_sql.side_effect = lambda sql: sql
        self.mod._API.READ_SQL = re.compile(r"^(select|with)\b", re.I)
        payload = json.loads(
            self.tools["db_query"]({"connection_id": "c1", "sql": "delete from t"})
        )
        self.assertIn("read-only", payload["error"])

    def test_query_requires_args(self):
        payload = json.loads(self.tools["db_query"]({}))
        self.assertIn("required", payload["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
