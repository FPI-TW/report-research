"""對外 API 端點行為（`web/routers/external.py`）：搜尋與短效原檔連結。

走 HTTP 層（`TestClient`），假的 `deps.api_clients`／`hybrid_search`／DB／物件儲存（`tests/external_fakes.py`）。
認證閘門本身在 `tests/test_external_auth.py`；真的 SQL（可見性、草稿、entitlement）在
`tests/test_external_api_db.py`。
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone

os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

import external_fakes as fx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.services import original_file_url  # noqa: E402
from app.services.entitlement import Entitlement  # noqa: E402
from app.services.object_storage import ObjectNotFound, ObjectStorageError  # noqa: E402
from app.services.textnorm import clean_text  # noqa: E402
from web import deps  # noqa: E402
from web.routers import external  # noqa: E402
from web.server import app  # noqa: E402

RID_A = "11111111-1111-4111-8111-111111111111"
RID_B = "22222222-2222-4222-8222-222222222222"
FIXED_NOW = datetime(2026, 10, 7, 8, 0, 0, tzinfo=timezone.utc)


class _Case(unittest.TestCase):
    def setUp(self):
        self.api = fx.FakeApiClients()
        self.reports = {
            RID_A: fx.Report(RID_A, "a" * 64, market="TW"),
            RID_B: fx.Report(RID_B, "b" * 64, file_name="b.docx", market="US", source="ms"),
        }
        for r in self.reports.values():
            r.object_key = f"originals/{r.file_hash[:2]}/{r.file_hash}{os.path.splitext(r.file_name)[1]}"
        self.storage = fx.FakeStorage(self.reports)
        self._orig = {
            name: getattr(deps, name)
            for name in ("api_clients", "hybrid_search", "embed_query_cached", "SessionFactory")
        }
        self._orig_storage = original_file_url.get_object_storage
        self._orig_now = external._utcnow
        deps.api_clients = self.api
        self.search_calls: list[dict] = []
        self.sql_log: list = []
        self.hits: list = []  # hybrid_search 回傳的列（測試自行決定命中哪些研報）

        async def _hybrid(session, q, qvec, **kw):
            self.search_calls.append(kw)
            ent = kw["entitlement"]
            # 模擬 SQL 下推：只回 entitlement 內的命中
            return [
                (1, 0.9 - i * 0.1, row) for i, row in enumerate(self.hits)
                if ent.allows(market=row.market, source=row.source, report_type=row.report_type,
                              instrument_types=row.instrument_types)
            ]

        deps.hybrid_search = _hybrid
        deps.embed_query_cached = lambda q: [0.0]
        deps.SessionFactory = lambda: fx.FakeSession(self.reports, self.sql_log)
        original_file_url.get_object_storage = lambda: self.storage
        external._utcnow = lambda: FIXED_NOW
        self.client = TestClient(app, follow_redirects=False)

    def tearDown(self):
        for name, value in self._orig.items():
            setattr(deps, name, value)
        original_file_url.get_object_storage = self._orig_storage
        external._utcnow = self._orig_now

    def get(self, url, key):
        return self.client.get(url, headers={"Authorization": f"Bearer {key}"})


class SearchTests(_Case):
    def test_search_passes_client_entitlement_and_shapes_results(self):
        key = self.api.add(fx.make_client(1, scopes=("search",), entitlements={"market": ("TW",)}))
        self.hits = [fx.chunk_row(self.reports[RID_A]), fx.chunk_row(self.reports[RID_B])]
        r = self.get("/external/v1/search?q=台積電", key)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self.search_calls[0]["entitlement"], Entitlement(markets=("TW",)))
        body = r.json()
        self.assertEqual(body["total"], 1)
        (item,) = body["results"]
        self.assertEqual(item["report_id"], RID_A)
        self.assertEqual(item["market"], "TW")
        self.assertEqual(item["report_date"], "2026-10-01")
        self.assertEqual(item["instrument_types"], ["stock"])
        self.assertAlmostEqual(item["score"], 0.9)
        self.assertEqual(item["passages"][0]["chunk_index"], 0)
        self.assertEqual(item["passages"][0]["content"], clean_text("台積電 營收 成長"))  # 清理後內文
        # 沒有 report.file scope：完全不附 file_url（不是 null）
        self.assertNotIn("file_url", item)
        self.assertNotIn("file_url_expires_at", item)
        # 不回雷達欄位
        for banned in ("target_price", "stock_targets", "futures_targets", "rating"):
            self.assertNotIn(banned, item)
        self.assertEqual(self.storage.presigns, [])

    def test_cross_client_entitlement_is_each_clients_own(self):
        a = self.api.add(fx.make_client(1, entitlements={"market": ("TW",)}))
        b = self.api.add(fx.make_client(2, entitlements={"market": ("US",), "source": ("ms",)}))
        self.hits = [fx.chunk_row(self.reports[RID_A]), fx.chunk_row(self.reports[RID_B])]
        ra = self.get("/external/v1/search?q=x", a).json()
        rb = self.get("/external/v1/search?q=x", b).json()
        self.assertEqual(self.search_calls[0]["entitlement"].markets, ("TW",))
        self.assertEqual(self.search_calls[1]["entitlement"], Entitlement(markets=("US",), sources=("ms",)))
        self.assertEqual([x["report_id"] for x in ra["results"]], [RID_A])
        self.assertEqual([x["report_id"] for x in rb["results"]], [RID_B])

    def test_request_filters_narrow_but_never_widen(self):
        key = self.api.add(fx.make_client(1, entitlements={"market": ("TW", "US"), "source": ("kgi",)}))
        r = self.get("/external/v1/search?q=x&market=US&report_type=個股&instrument_type=stock", key)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.search_calls[-1]["entitlement"], Entitlement(
            markets=("US",), sources=("kgi",), report_types=("個股",), instrument_types=("stock",),
        ))
        # 不在授權內的過濾值：交集為空，直接回空、不打檢索也不嵌入
        n = len(self.search_calls)
        for qs in ("market=HK", "source=ms", "market=CN&source=kgi"):
            with self.subTest(qs=qs):
                r = self.get(f"/external/v1/search?q=x&{qs}", key)
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.json()["total"], 0)
                self.assertEqual(r.json()["results"], [])
        self.assertEqual(len(self.search_calls), n)

    def test_narrow_entitlement_unit(self):
        ent = Entitlement(markets=("TW",), instrument_types=("stock",))
        self.assertIsNone(external.narrow_entitlement(
            ent, market=None, source=None, report_type=None, instrument_type="futures"))
        self.assertEqual(
            external.narrow_entitlement(ent, market=None, source="kgi", report_type=None, instrument_type=None),
            Entitlement(markets=("TW",), sources=("kgi",), instrument_types=("stock",)),
        )
        self.assertEqual(
            external.narrow_entitlement(ent, market=None, source=None, report_type=None, instrument_type=None),
            ent,
        )

    def test_parameter_bounds(self):
        key = self.api.add(fx.make_client(1))
        for qs in ("limit=21", "limit=0", "passages=4", "passages=0", "offset=-1", "sort=random", "q=" + "x" * 501):
            with self.subTest(qs=qs):
                url = f"/external/v1/search?{qs}" if qs.startswith("q=") else f"/external/v1/search?q=x&{qs}"
                self.assertEqual(self.get(url, key).status_code, 422)

    def test_pagination_and_passage_limit(self):
        key = self.api.add(fx.make_client(1, scopes=("search",)))
        many = [fx.Report(f"{i:08d}-1111-4111-8111-111111111111", f"{i:064x}") for i in range(5)]
        self.hits = [fx.chunk_row(r, chunk_index=j) for r in many for j in range(3)]
        body = self.get("/external/v1/search?q=x&limit=2&offset=1&passages=1&sort=relevance", key).json()
        self.assertEqual(body["total"], 5)
        self.assertEqual(body["offset"], 1)
        self.assertEqual(body["limit"], 2)
        self.assertEqual(len(body["results"]), 2)
        self.assertTrue(all(len(x["passages"]) == 1 for x in body["results"]))

    def test_file_urls_attached_with_scope_without_head_and_fail_open(self):
        key = self.api.add(fx.make_client(1, entitlements={"market": ("TW", "US")}))
        self.hits = [fx.chunk_row(self.reports[RID_A]), fx.chunk_row(self.reports[RID_B])]
        self.storage.presign_fail_keys = {self.reports[RID_B].object_key}
        body = self.get("/external/v1/search?q=x", key).json()
        by_id = {x["report_id"]: x for x in body["results"]}
        self.assertTrue(by_id[RID_A]["file_url"].startswith("https://signed.example.test/"))
        self.assertEqual(by_id[RID_A]["file_url_expires_at"], "2026-10-07T08:10:00Z")
        self.assertIsNone(by_id[RID_B]["file_url"])  # 產生失敗只影響那一筆
        self.assertIsNone(by_id[RID_B]["file_url_expires_at"])
        self.assertEqual(self.storage.heads, [])  # verify_object=False：不逐筆 HEAD
        self.assertEqual(
            sorted((k, kw["ttl_seconds"]) for k, kw in self.storage.presigns),
            sorted((self.reports[r].object_key, 600) for r in (RID_A, RID_B)),
        )
        # 指標查詢帶的是用戶端自己的 entitlement 與可見性條件
        sql, params = self.sql_log[-1]
        self.assertIn("report_visibility", sql)
        self.assertEqual(params["ent_markets"], ["TW", "US"])

    def test_wrong_pointer_in_search_yields_null_not_error(self):
        key = self.api.add(fx.make_client(1))
        self.reports[RID_A].object_key = "originals/zz/other.pdf"
        self.hits = [fx.chunk_row(self.reports[RID_A])]
        r = self.get("/external/v1/search?q=x", key)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["results"][0]["file_url"])
        self.assertEqual(self.storage.presigns, [])

    def test_pointer_query_failure_is_fail_open(self):
        key = self.api.add(fx.make_client(1))
        self.hits = [fx.chunk_row(self.reports[RID_A])]

        class _Broken(fx.FakeSession):
            async def execute(self, *a, **k):
                raise RuntimeError("db down")

        deps.SessionFactory = lambda: _Broken(self.reports)
        r = self.get("/external/v1/search?q=x", key)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["results"][0]["file_url"])


class FileUrlTests(_Case):
    def url(self, rid):
        return f"/external/v1/reports/{rid}/file-url"

    def test_file_url_presigns_with_external_ttl_after_head(self):
        key = self.api.add(fx.make_client(1, scopes=("report.file",)))
        r = self.get(self.url(RID_A), key)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["cache-control"], "no-store")
        body = r.json()
        self.assertEqual(body["report_id"], RID_A)
        self.assertEqual(body["expires_at"], "2026-10-07T08:10:00Z")  # now + 600 秒
        obj = self.reports[RID_A].object_key
        self.assertEqual(self.storage.heads, [obj])
        self.assertEqual(self.storage.presigns, [(obj, {"filename": "a.pdf", "inline": True, "ttl_seconds": 600})])
        self.assertEqual(body["file_url"], f"https://signed.example.test/{obj}?ttl=600")

    def test_expires_at_tracks_real_clock(self):
        external._utcnow = self._orig_now
        key = self.api.add(fx.make_client(1, scopes=("report.file",)))
        before = datetime.now(timezone.utc)
        body = self.get(self.url(RID_A), key).json()
        expires = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00"))
        self.assertLess(abs((expires - before).total_seconds() - 600), 5)

    def test_other_clients_report_is_404(self):
        a = self.api.add(fx.make_client(1, scopes=("report.file",), entitlements={"market": ("TW",)}))
        b = self.api.add(fx.make_client(2, scopes=("report.file",), entitlements={"market": ("US",)}))
        self.assertEqual(self.get(self.url(RID_B), b).status_code, 200)
        r = self.get(self.url(RID_B), a)
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["code"], "report_not_found")
        sql, params = self.sql_log[-1]
        self.assertIn("report_visibility", sql)
        self.assertEqual(params["ent_markets"], ["TW"])

    def test_missing_hidden_and_malformed_are_same_404(self):
        key = self.api.add(fx.make_client(1, scopes=("report.file",)))
        self.reports[RID_A].hidden = True
        for rid in (RID_A, "33333333-3333-4333-8333-333333333333", "not-a-uuid"):
            with self.subTest(rid=rid):
                r = self.get(self.url(rid), key)
                self.assertEqual(r.status_code, 404)
                self.assertEqual(r.json()["code"], "report_not_found")
        self.assertEqual(self.storage.heads, [])

    def test_storage_outcomes(self):
        key = self.api.add(fx.make_client(1, scopes=("report.file",)))
        self.storage.head_exc = ObjectNotFound("x")
        r = self.get(self.url(RID_A), key)
        self.assertEqual((r.status_code, r.json()["code"]), (404, "original_not_found"))
        self.storage.head_exc = ObjectStorageError("down")
        r = self.get(self.url(RID_A), key)
        self.assertEqual((r.status_code, r.json()["code"]), (503, "original_unavailable"))
        self.storage.head_exc = None
        self.reports[RID_A].object_key = "originals/zz/other.pdf"
        r = self.get(self.url(RID_A), key)
        self.assertEqual((r.status_code, r.json()["code"]), (503, "original_unavailable"))
        self.assertEqual(self.storage.presigns, [])

    def test_local_mode_never_mints(self):
        key = self.api.add(fx.make_client(1, scopes=("report.file",)))
        self.storage.enabled = False
        r = self.get(self.url(RID_A), key)
        self.assertEqual((r.status_code, r.json()["code"]), (404, "original_not_found"))
        self.assertEqual(self.storage.heads + self.storage.presigns, [])


if __name__ == "__main__":
    unittest.main()
