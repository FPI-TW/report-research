# tests/test_reading_api.py
"""閱讀頁三端點 API 測試（TestClient + monkeypatch fetch；串接 + 驗證 + 空狀態）。"""
import hashlib
import os
import sys
import unittest
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from app.services.filename import source_display  # noqa: E402
from app.services.radar.types import DimensionStance, EpsEstimate, Signal  # noqa: E402
from app.services.reading.queries import DocRow, SimilarRow, TakeawayRow  # noqa: E402
from app.services.textnorm import clean_extracted  # noqa: E402
from web import deps, server  # noqa: E402
from web.routers import reading as reading_router  # noqa: E402

# 服務綁定（SessionFactory、fetch_*）在 web.deps；READING_TEXT_MAX_CHARS 是 reading
# 組的設定常數，已隨路由搬到 web.routers.reading（_visible_chars 從該模組讀它）。
# 故覆寫時按符號選模組，兩者不可混淆。
_ON_READING = {"READING_TEXT_MAX_CHARS"}


def _dep_mod(name):
    return reading_router if name in _ON_READING else deps

HASH = "a" * 64
OTHER_HASH = "c" * 64

# 刻意帶 CJK 字元間空白（PDF 抽字的實況）：正典文字 = clean_extracted(full_text)
# 與 full_text 不同，故此 fixture 能證明端點真的有做清理、而不是直接吐 full_text。
RAW_TEXT = "台 積 電 第 三 季 營 收 創 高。\n\n毛 利 率 上 修 至 五 成。"
CANONICAL = clean_extracted(RAW_TEXT)
SHA = hashlib.sha256(CANONICAL.encode("utf-8")).hexdigest()

# report_chunk.content 是「清理後」的文字（ingest 走 chunk_text(clean_extracted(raw))），
# 故它不帶 full_text 的 CJK 間空白 —— 見 reading/anchor.py 模組 docstring 的事實一/二。
CHUNK_CONTENT = "毛利率上修至五成。"


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        raise AssertionError("端點不應直接打 DB（fetch 已被 monkeypatch）")


def _doc(full_text=RAW_TEXT, file_path="/nonexistent/daiwa-8046.pdf",
         title="基板漲價超預期，重申買進"):
    return DocRow(
        report_id="rep-1", file_hash=HASH, file_name="daiwa-8046.pdf",
        title=title,
        file_path=file_path, market="TW", source="daiwa",
        report_date=date(2026, 7, 11), report_type="個股報告", summary="摘要",
        instrument_types=["equity"], stock_targets=["8046"], futures_targets=[],
        full_text=full_text,
    )


def _takeaway(ordinal=1, text_sha256=SHA, quote_start=0, quote_end=8):
    return TakeawayRow(
        ordinal=ordinal, claim=f"論點{ordinal}", quote="台積電第三季",
        quote_start=quote_start, quote_end=quote_end, anchor_method="normalized",
        text_sha256=text_sha256,
    )


def _signal():
    return Signal(
        id="sig-1", report_id="rep-1", market="TW", instrument_code="8046",
        broker="daiwa", report_date=date(2026, 7, 11), rating_raw="Buy",
        rating_normalized="buy", target_price=2444.0, target_currency="TWD",
        target_horizon="12M", target_price_evidence="TP 證據",
        eps=(EpsEstimate(fiscal_year=2026, period="FY", currency="TWD",
                         unit="per_share", value=66.4, evidence="e"),),
        thesis={"outlook": DimensionStance(stance="positive", summary="s", evidence="e")},
        extraction_status="valid", file_name="daiwa-8046.pdf",
    )


def _similar(matched=9, total=12, title="另一篇的內部標題"):
    return SimilarRow(
        file_hash=OTHER_HASH, file_name="other.pdf", title=title,
        market="TW", source="kgi",
        report_date=date(2026, 7, 1), summary="另一篇摘要",
        matched_probes=matched, total_probes=total, score=5.4,
    )


def _authed_client():
    client = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
    r = client.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return client


class ReadingApiBase(unittest.TestCase):
    def setUp(self):
        self._orig = {
            k: getattr(_dep_mod(k), k)
            for k in (
                "SessionFactory", "fetch_doc", "fetch_takeaways", "fetch_signals",
                "fetch_instrument_names", "fetch_similar", "fetch_chunk_content",
                "READING_TEXT_MAX_CHARS",
            )
        }
        deps.SessionFactory = lambda: _FakeSession()
        # 預設：一篇有全文、無摘錄、無訊號、查不到 chunk 的報告
        self._set(
            fetch_doc=self._async(_doc()),
            fetch_takeaways=self._async([]),
            fetch_signals=self._async([]),
            fetch_instrument_names=self._async({}),
            fetch_similar=self._async([]),
            fetch_chunk_content=self._async(None),
        )

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(_dep_mod(k), k, v)

    def _set(self, **fns):
        for name, fn in fns.items():
            setattr(_dep_mod(name), name, fn)

    @staticmethod
    def _async(value):
        async def fn(*a, **k):
            return value
        return fn


class ValidationTests(ReadingApiBase):
    def test_unauthenticated_401(self):
        client = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
        self.assertEqual(client.get(f"/api/reading/{HASH}").status_code, 401)

    def test_invalid_hash_422(self):
        client = _authed_client()
        for bad in ("abc", "z" * 64, "A" * 64, "a" * 63, "a" * 65):
            with self.subTest(bad=bad):
                self.assertEqual(client.get(f"/api/reading/{bad}").status_code, 422)

    def test_invalid_hash_422_on_all_three_endpoints(self):
        client = _authed_client()
        self.assertEqual(client.get("/api/reading/nope").status_code, 422)
        self.assertEqual(client.get("/api/reading/nope/text").status_code, 422)
        self.assertEqual(client.get("/api/reading/nope/similar").status_code, 422)

    def test_invalid_hash_never_hits_db(self):
        def boom(*a, **k):
            raise AssertionError("非法 hash 不應查 DB")
        self._set(fetch_doc=boom)
        self.assertEqual(_authed_client().get("/api/reading/nope").status_code, 422)

    def test_unknown_hash_404_on_all_three_endpoints(self):
        self._set(fetch_doc=self._async(None))
        client = _authed_client()
        self.assertEqual(client.get(f"/api/reading/{HASH}").status_code, 404)
        self.assertEqual(client.get(f"/api/reading/{HASH}/text").status_code, 404)
        self.assertEqual(client.get(f"/api/reading/{HASH}/similar").status_code, 404)


class ReadingDocShapeTests(ReadingApiBase):
    def test_happy_path_200(self):
        self._set(fetch_takeaways=self._async([_takeaway()]),
                  fetch_signals=self._async([_signal()]))
        r = _authed_client().get(f"/api/reading/{HASH}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["report_id"], "rep-1")
        self.assertEqual(body["file_hash"], HASH)
        self.assertEqual(body["file_name"], "daiwa-8046.pdf")
        # 報頭顯示的是內部標題（檔名多為券商流水號）；批次沒跑到的報告則為 None
        self.assertEqual(body["title"], "基板漲價超預期，重申買進")
        self.assertEqual(body["market"], "TW")
        self.assertEqual(body["report_type"], "個股報告")  # 前端報頭會顯示
        self.assertEqual(body["source"], "daiwa")
        self.assertEqual(body["source_display"], source_display("daiwa"))
        self.assertEqual(body["report_date"], "2026-07-11")
        self.assertEqual(body["stock_targets"], ["8046"])
        self.assertTrue(body["is_pdf"])
        self.assertFalse(body["has_file"])  # 路徑不存在
        self.assertEqual(body["text_state"], "ok")
        self.assertEqual(body["text_chars"], len(CANONICAL))
        self.assertEqual(body["text_sha256"], SHA)

    def test_title_absent_is_null_not_error(self):
        # 標題是漸進補的：批次還沒跑到的報告 title 為 NULL，頁面照常（前端回退檔名）
        self._set(fetch_doc=self._async(_doc(title=None)))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertIsNone(body["title"])
        self.assertEqual(body["file_name"], "daiwa-8046.pdf")

    def test_doc_never_includes_full_text(self):
        # 契約：閱讀頁骨架不含全文（絕大多數研報直接內嵌 PDF，全文另走 /text）
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertNotIn("text", body)
        self.assertNotIn("full_text", body)

    def test_non_pdf_file_is_not_pdf(self):
        self._set(fetch_doc=self._async(_doc(file_path="/nonexistent/a.docx")))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertFalse(body["is_pdf"])

    def test_has_file_true_when_path_exists(self):
        self._set(fetch_doc=self._async(_doc(file_path=str(Path(__file__).resolve()))))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertTrue(body["has_file"])

    def test_null_full_text_is_missing_not_an_error(self):
        # full_text 為 NULL ≠ 錯誤：該篇只是沒有可讀文字（只能看 PDF）
        self._set(fetch_doc=self._async(_doc(full_text=None)))
        r = _authed_client().get(f"/api/reading/{HASH}")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["text_state"], "missing")
        self.assertEqual(body["text_chars"], 0)
        self.assertIsNone(body["text_sha256"])

    def test_blank_full_text_is_missing(self):
        self._set(fetch_doc=self._async(_doc(full_text="   \n\n  ")))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertEqual(body["text_state"], "missing")
        self.assertEqual(body["text_chars"], 0)
        self.assertIsNone(body["text_sha256"])


class SignalsStateTests(ReadingApiBase):
    def test_no_signals_state_none(self):
        # 全語料僅 0.68% 有訊號；前端據此整區不進 DOM（不是空框、不是骨架）
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertEqual(body["signals_state"], "none")
        self.assertEqual(body["signals"], [])

    def test_with_signals_state_available(self):
        self._set(fetch_signals=self._async([_signal()]),
                  fetch_instrument_names=self._async({("TW", "8046"): "南亞電路板"}))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertEqual(body["signals_state"], "available")
        sig = body["signals"][0]
        # 一份研報可能對多檔標的有訊號：代號與名稱是辨識這張卡在講誰的唯一依據
        self.assertEqual(sig["instrument_code"], "8046")
        self.assertEqual(sig["instrument_name"], "南亞電路板")
        self.assertEqual(sig["rating_normalized"], "buy")
        self.assertEqual(sig["target_price"], 2444.0)
        self.assertEqual(sig["broker_display"], source_display("daiwa"))
        # radar 存 int、契約要 str
        self.assertEqual(sig["eps_estimates"][0]["fiscal_year"], "2026")
        self.assertEqual(sig["eps_estimates"][0]["value"], 66.4)
        self.assertEqual(sig["thesis"][0]["key"], "outlook")
        self.assertEqual(sig["thesis"][0]["stance"], "positive")

    def test_name_lookup_keyed_by_market_and_code(self):
        # 名稱查詢要拿到的是 (market, code) 對，不是只有 code——同代號跨市場撞號時
        # 錯的鍵會靜靜取到別的市場那檔的公司名。
        seen = {}

        async def spy(session, keys):
            seen["keys"] = list(keys)
            return {}

        self._set(fetch_signals=self._async([_signal()]), fetch_instrument_names=spy)
        self.assertEqual(_authed_client().get(f"/api/reading/{HASH}").status_code, 200)
        self.assertEqual(seen["keys"], [("TW", "8046")])

    def test_missing_name_is_null_not_error(self):
        # 名稱由檔名解析而來、解析不出就沒有：前端據此只顯示代號（比照 title → file_name）
        self._set(fetch_signals=self._async([_signal()]),
                  fetch_instrument_names=self._async({}))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertEqual(body["signals"][0]["instrument_code"], "8046")
        self.assertIsNone(body["signals"][0]["instrument_name"])

    def test_no_signals_asks_for_no_names(self):
        # 99.3% 的研報沒有訊號：那條路徑不該為了名稱多打一次 DB
        seen = {}

        async def spy(session, keys):
            seen["keys"] = list(keys)
            return {}

        self._set(fetch_instrument_names=spy)
        self.assertEqual(_authed_client().get(f"/api/reading/{HASH}").status_code, 200)
        self.assertEqual(seen["keys"], [])


class TakeawayTests(ReadingApiBase):
    def test_takeaways_returned_in_order(self):
        self._set(fetch_takeaways=self._async([_takeaway(1), _takeaway(2)]))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertEqual([t["ordinal"] for t in body["takeaways"]], [1, 2])
        self.assertEqual(body["takeaways"][0]["quote_start"], 0)
        self.assertEqual(body["takeaways"][0]["anchor_method"], "normalized")

    def test_stale_sha_degrades_to_unjumpable(self):
        # 擷取當時的正典文字 sha 與現在的不符 → offset 已漂移 → 不給跳，
        # 但條目與引文照常顯示。寧可不能跳，也不要跳到錯的地方。
        self._set(fetch_takeaways=self._async([_takeaway(text_sha256="f" * 64)]))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        t = body["takeaways"][0]
        self.assertEqual(t["claim"], "論點1")
        self.assertEqual(t["quote"], "台積電第三季")
        self.assertIsNone(t["quote_start"])
        self.assertIsNone(t["quote_end"])
        self.assertIsNone(t["anchor_method"])

    def test_missing_text_degrades_all_anchors(self):
        self._set(fetch_doc=self._async(_doc(full_text=None)),
                  fetch_takeaways=self._async([_takeaway()]))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertIsNone(body["takeaways"][0]["quote_start"])

    def test_unanchored_takeaway_still_listed(self):
        self._set(fetch_takeaways=self._async(
            [_takeaway(quote_start=None, quote_end=None)]
        ))
        body = _authed_client().get(f"/api/reading/{HASH}").json()
        self.assertEqual(len(body["takeaways"]), 1)
        self.assertIsNone(body["takeaways"][0]["quote_start"])

    def test_offsets_outside_truncation_degrade_to_unjumpable(self):
        # 錨點落在 /text 根本回不到的範圍 → 骨架端點就收回 offset。
        # 規則是「寧可沒有座標，也不要給指向讀者手上沒有的文字的座標」——
        # 收回一律在後端做，消費端不自行判斷截斷。
        # （前端引文跳轉已於 2026-08-03 移除，故這條目前守的是契約而非畫面行為。）
        self._set(fetch_takeaways=self._async([_takeaway(quote_start=0, quote_end=8)]),
                  READING_TEXT_MAX_CHARS=5)
        t = _authed_client().get(f"/api/reading/{HASH}").json()["takeaways"][0]
        self.assertEqual(t["claim"], "論點1")        # 條目照常顯示
        self.assertEqual(t["quote"], "台積電第三季")  # 引文照常顯示
        self.assertIsNone(t["quote_start"])
        self.assertIsNone(t["quote_end"])
        self.assertIsNone(t["anchor_method"])

    def test_offsets_kept_when_inside_truncation(self):
        self._set(fetch_takeaways=self._async([_takeaway(quote_start=0, quote_end=8)]),
                  READING_TEXT_MAX_CHARS=len(CANONICAL))
        t = _authed_client().get(f"/api/reading/{HASH}").json()["takeaways"][0]
        self.assertEqual(t["quote_start"], 0)
        self.assertEqual(t["quote_end"], 8)
        self.assertEqual(t["anchor_method"], "normalized")

    def test_offset_ending_exactly_at_truncation_is_kept(self):
        # 邊界：quote_end == 可見字元數 ＝ 最後一個字剛好看得到 → 仍可跳
        self._set(fetch_takeaways=self._async([_takeaway(quote_start=0, quote_end=8)]),
                  READING_TEXT_MAX_CHARS=8)
        t = _authed_client().get(f"/api/reading/{HASH}").json()["takeaways"][0]
        self.assertEqual(t["quote_end"], 8)

    def test_doc_and_text_agree_on_jumpability_under_truncation(self):
        # 真正的不變量：骨架標為可跳的錨點，一定要落在 /text 真的回得出來的文字裡。
        # 兩個端點各自判斷截斷就會分岔（本案即是：/text 的 _chunk_anchor 有收、
        # 骨架的 takeaway 沒收）。
        self._set(fetch_takeaways=self._async([_takeaway(quote_start=0, quote_end=8)]),
                  READING_TEXT_MAX_CHARS=5)
        client = _authed_client()
        t = client.get(f"/api/reading/{HASH}").json()["takeaways"][0]
        body = client.get(f"/api/reading/{HASH}/text").json()
        self.assertTrue(body["truncated"])
        self.assertEqual(len(body["text"]), 5)
        # 8 > 5：這條摘錄指向讀者拿不到的文字 → 骨架必須已經收回它的錨點
        self.assertIsNone(t["quote_start"])


class ReadingTextTests(ReadingApiBase):
    def test_text_200_is_canonical_not_full_text(self):
        r = _authed_client().get(f"/api/reading/{HASH}/text")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # 正典文字＝clean_extracted(full_text)，不是 full_text
        self.assertEqual(body["text"], CANONICAL)
        self.assertNotEqual(body["text"], RAW_TEXT)
        self.assertEqual(body["file_hash"], HASH)
        self.assertEqual(body["text_chars"], len(CANONICAL))
        self.assertFalse(body["truncated"])

    def test_sha256_matches_doc_endpoint(self):
        # 兩者相符是「摘錄與全文同源」的唯一驗章 → 這條契約壞掉＝所有錨點靜默失效
        client = _authed_client()
        doc_sha = client.get(f"/api/reading/{HASH}").json()["text_sha256"]
        text_body = client.get(f"/api/reading/{HASH}/text").json()
        self.assertEqual(doc_sha, text_body["text_sha256"])
        self.assertEqual(
            text_body["text_sha256"],
            hashlib.sha256(text_body["text"].encode("utf-8")).hexdigest(),
        )

    def test_missing_text_404(self):
        self._set(fetch_doc=self._async(_doc(full_text=None)))
        self.assertEqual(
            _authed_client().get(f"/api/reading/{HASH}/text").status_code, 404
        )

    def test_truncation_keeps_full_text_sha_and_chars(self):
        # 截斷只影響顯示：takeaway 錨點是對「完整正典文字」算的，若回截斷版的 sha，
        # 驗章會一律失敗、該篇錨點全數被收回。超出範圍的錨點另由後端收回（見骨架端點）。
        self._set(READING_TEXT_MAX_CHARS=5)
        body = _authed_client().get(f"/api/reading/{HASH}/text").json()
        self.assertTrue(body["truncated"])
        self.assertEqual(body["text"], CANONICAL[:5])
        self.assertEqual(body["text_chars"], len(CANONICAL))  # 完整長度
        self.assertEqual(body["text_sha256"], SHA)            # 完整正典文字的 sha
        self.assertNotEqual(
            body["text_sha256"],
            hashlib.sha256(body["text"].encode("utf-8")).hexdigest(),
        )

    def test_exact_boundary_not_truncated(self):
        self._set(READING_TEXT_MAX_CHARS=len(CANONICAL))
        body = _authed_client().get(f"/api/reading/{HASH}/text").json()
        self.assertFalse(body["truncated"])
        self.assertEqual(body["text"], CANONICAL)


class ChunkAnchorTests(ReadingApiBase):
    """?chunk=N → 回該段在正典文字上的字元區間（閱讀頁「跳到命中那一段」的資料源）。"""

    def test_no_chunk_param_no_offsets_and_no_chunk_query(self):
        def boom(*a, **k):
            raise AssertionError("沒帶 ?chunk 就不該查 chunk")

        self._set(fetch_chunk_content=boom)
        body = _authed_client().get(f"/api/reading/{HASH}/text").json()
        self.assertIsNone(body["chunk_start"])
        self.assertIsNone(body["chunk_end"])

    def test_anchored_offsets_slice_back_to_the_chunk(self):
        # 錨定的唯一驗收標準：拿 offset 去切正典文字，切出來就是那個 chunk
        self._set(fetch_chunk_content=self._async(CHUNK_CONTENT))
        body = _authed_client().get(f"/api/reading/{HASH}/text?chunk=1").json()
        self.assertIsNotNone(body["chunk_start"])
        self.assertEqual(
            body["text"][body["chunk_start"]:body["chunk_end"]], CHUNK_CONTENT
        )

    def test_chunk_index_and_report_id_forwarded(self):
        seen = {}

        async def fake(session, report_id, chunk_index):
            seen.update(report_id=report_id, chunk_index=chunk_index)
            return CHUNK_CONTENT

        self._set(fetch_chunk_content=fake)
        _authed_client().get(f"/api/reading/{HASH}/text?chunk=4")
        # chunk 以 report_id（非 file_hash）查：chunk 表以 report_id 為鍵
        self.assertEqual(seen, {"report_id": "rep-1", "chunk_index": 4})

    def test_unknown_chunk_is_not_an_error(self):
        # 連結可能來自重新 ingest 前的檢索結果 → 查無此 chunk。不高亮，但頁面照常
        self._set(fetch_chunk_content=self._async(None))
        r = _authed_client().get(f"/api/reading/{HASH}/text?chunk=999")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["chunk_start"])

    def test_unanchorable_chunk_is_not_an_error(self):
        self._set(fetch_chunk_content=self._async("這段話完全不在這篇研報的全文裡。"))
        r = _authed_client().get(f"/api/reading/{HASH}/text?chunk=1")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIsNone(body["chunk_start"])
        self.assertIsNone(body["chunk_end"])
        self.assertEqual(body["text"], CANONICAL)  # 文字照給

    def test_offsets_outside_truncation_are_dropped(self):
        # offset 是對「完整正典文字」算的；回的 text 被截斷時，落在範圍外的錨點會
        # 指向讀者手上根本沒有的文字 → 收回為 None（寧可不高亮，也不指錯位置）
        self._set(fetch_chunk_content=self._async(CHUNK_CONTENT),
                  READING_TEXT_MAX_CHARS=5)
        body = _authed_client().get(f"/api/reading/{HASH}/text?chunk=1").json()
        self.assertTrue(body["truncated"])
        self.assertIsNone(body["chunk_start"])
        self.assertIsNone(body["chunk_end"])

    def test_offsets_kept_when_still_inside_truncation(self):
        self._set(fetch_chunk_content=self._async(CHUNK_CONTENT),
                  READING_TEXT_MAX_CHARS=len(CANONICAL))
        body = _authed_client().get(f"/api/reading/{HASH}/text?chunk=1").json()
        self.assertFalse(body["truncated"])
        self.assertEqual(
            body["text"][body["chunk_start"]:body["chunk_end"]], CHUNK_CONTENT
        )

    def test_negative_chunk_422(self):
        self.assertEqual(
            _authed_client().get(f"/api/reading/{HASH}/text?chunk=-1").status_code, 422
        )


class SimilarTests(ReadingApiBase):
    def test_similar_200(self):
        self._set(fetch_similar=self._async([_similar()]))
        r = _authed_client().get(f"/api/reading/{HASH}/similar")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["file_hash"], HASH)
        item = body["items"][0]
        self.assertEqual(item["file_hash"], OTHER_HASH)
        self.assertEqual(item["file_name"], "other.pdf")
        self.assertEqual(item["title"], "另一篇的內部標題")
        self.assertEqual(item["source_display"], source_display("kgi"))
        self.assertEqual(item["report_date"], "2026-07-01")
        self.assertEqual(item["matched_probes"], 9)  # 「9/12 段相符」
        self.assertEqual(item["total_probes"], 12)

    def test_empty_similar_200(self):
        body = _authed_client().get(f"/api/reading/{HASH}/similar").json()
        self.assertEqual(body["items"], [])

    def test_limit_forwarded(self):
        seen = {}

        async def fake(session, report_id, **kw):
            seen.update(kw)
            seen["report_id"] = report_id
            return []

        self._set(fetch_similar=fake)
        _authed_client().get(f"/api/reading/{HASH}/similar?limit=3")
        self.assertEqual(seen["limit"], 3)
        # 以 report_id（非 file_hash）查相似：向量表以 report_id 為鍵
        self.assertEqual(seen["report_id"], "rep-1")

    def test_invalid_limit_422(self):
        client = _authed_client()
        self.assertEqual(
            client.get(f"/api/reading/{HASH}/similar?limit=0").status_code, 422
        )
        self.assertEqual(
            client.get(f"/api/reading/{HASH}/similar?limit=99").status_code, 422
        )


if __name__ == "__main__":
    unittest.main()
