"""資料來源連接器（`app/services/connectors/`）：側檔驗證、掃描與處理紀錄、三種連接器、profile `[connectors]`。

不連網、不連 DB：資料夾一律是 tempfile；S3 用假的 bucket（`FakeBucket`）與假的 boto3 client；plugin 寫在
tempfile 的 profile 目錄裡。入庫迴圈（重跑不重複入庫）在 tests/test_ingest_connectors.py。
"""
from __future__ import annotations

import json
import os
import tempfile
import textwrap
import time
import unittest
from datetime import date
from pathlib import Path

from app.profile import ProfileError, load_profile
from app.services import connectors
from app.services.connectors import base, s3, sidecar
from app.services.tagging import MarketTag

OLD = time.time() - 3600


def _touch(path: Path, data: bytes = b"%PDF-1.4\nx", *, mtime: float = OLD) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (mtime, mtime))
    return path


# ── 側檔 ────────────────────────────────────────────────────────────────


class SidecarValidationTests(unittest.TestCase):
    FULL = {
        "title": "台積電：先進封裝上修", "report_date": "2026-11-01", "source": "yuanta", "analysts": ["王小明"],
        "market": "TW", "instrument_types": ["equity"], "stock_targets": ["2330"], "futures_targets": ["台指期"],
        "rating": "買進", "target_price": 1350, "target_currency": "TWD",
    }

    def test_full_sidecar(self):
        meta = sidecar.validate(dict(self.FULL), "a.json")
        self.assertEqual(meta.report_date, date(2026, 11, 1))
        self.assertEqual(meta.stock_targets, ("2330",))
        self.assertEqual(meta.target_price, 1350.0)
        self.assertTrue(meta.skips_tagging())
        self.assertEqual(meta.to_json()["report_date"], "2026-11-01")
        self.assertEqual(set(meta.to_json()), set(self.FULL))

    def test_only_provided_fields_are_recorded(self):
        meta = sidecar.validate({"title": "x"}, "a.json")
        self.assertEqual(meta.to_json(), {"title": "x"})
        self.assertFalse(meta.skips_tagging())

    def test_empty_stock_targets_still_counts_as_provided(self):
        meta = sidecar.validate({"market": "MACRO", "stock_targets": []}, "a.json")
        self.assertTrue(meta.skips_tagging())

    def test_rejections(self):
        bad = [
            ({"titel": "x"}, "未知的欄位"),
            ({"market": "TWSE"}, "不是標準市場代碼"),
            ({"market": "tw"}, "不是標準市場代碼"),
            ({"report_date": "2026/11/01"}, "YYYY-MM-DD"),
            ({"report_date": "2026-02-30"}, "不是有效的日期"),
            ({"source": "元大"}, "小寫英數"),
            ({"title": ""}, "不可為空字串"),
            ({"title": 3}, "必須是字串"),
            ({"stock_targets": "2330"}, "字串陣列"),
            ({"instrument_types": ["stock"]}, "不在詞表內"),
            ({"futures_targets": ["小台"]}, "不在詞表內"),
            ({"target_price": -1}, "正數"),
            ({"target_price": True}, "正數"),
            ({"target_currency": "TWD"}, "沒有 target_price"),
            ({"target_price": 10, "target_currency": "twd"}, "三碼大寫"),
            ({"title": "a\x00b"}, "控制字元"),
        ]
        for data, reason in bad:
            with self.subTest(data=data):
                with self.assertRaisesRegex(sidecar.SidecarError, reason):
                    sidecar.validate(data, "a.json")

    def test_json_bytes(self):
        self.assertEqual(sidecar.parse_json_bytes('{"title": "標題"}'.encode(), "a.json").title, "標題")
        for raw in (b"{", b"[]", b"\xff\xfe", b" " * (sidecar.MAX_SIDECAR_BYTES + 1)):
            with self.subTest(raw=raw[:8]):
                with self.assertRaises(sidecar.SidecarError):
                    sidecar.parse_json_bytes(raw, "a.json")

    def test_manifest(self):
        raw = ("file,market,stock_targets,target_price\n"
               "券商A/a.pdf,TW,2330;2303,1000\n"
               "b.pdf,US,,\n").encode()
        m = sidecar.parse_manifest(raw)
        self.assertEqual(m.rows["券商A/a.pdf"].stock_targets, ("2330", "2303"))
        self.assertEqual(m.rows["券商A/a.pdf"].target_price, 1000.0)
        self.assertEqual(m.rows["b.pdf"].to_json(), {"market": "US"}, "空格子＝沒提供")

    def test_manifest_rejections(self):
        for raw, reason in (
            (b"name,market\na.pdf,TW\n", "第一欄必須是 file"),
            (b"file,colour\na.pdf,x\n", "未知的欄位"),
            (b"file,market\n../a.pdf,TW\n", "相對路徑"),
            (b"file,market\na.pdf,TW\na.pdf,US\n", "列了兩次"),
            (b"file,market\na.pdf,XX\n", "標準市場代碼"),
            (b"file,market\na.pdf,TW,extra\n", "欄位數"),
        ):
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(sidecar.SidecarError, reason):
                    sidecar.parse_manifest(raw)

    def test_apply_to_tag_overrides_field_by_field(self):
        llm = MarketTag(market="US", is_research=True, confidence=0.7, instrument_types=["equity"],
                        relates_stock=True, relates_futures=False, stock_targets=["AAPL"], futures_targets=[])
        meta = sidecar.validate({"market": "TW"}, "a.json")
        out = sidecar.apply_to_tag(llm, meta)
        self.assertEqual((out.market, out.stock_targets, out.confidence), ("TW", ["AAPL"], 0.7))

    def test_apply_to_tag_sidecar_market_marks_research(self):
        llm = MarketTag(market=None, is_research=False, confidence=0.4)
        out = sidecar.apply_to_tag(llm, sidecar.validate({"market": "TW"}, "a.json"))
        self.assertTrue(out.is_research)
        self.assertEqual(out.market, "TW")

    def test_apply_to_tag_without_llm(self):
        self.assertIsNone(sidecar.apply_to_tag(None, sidecar.validate({"market": "TW"}, "a.json")),
                          "只有 market、LLM 又失敗：照常走標註失敗")
        out = sidecar.apply_to_tag(None, sidecar.validate({"market": "TW", "stock_targets": ["2330"]}, "a.json"))
        self.assertEqual((out.market, out.is_research, out.relates_stock, out.stock_targets),
                         ("TW", True, True, ["2330"]))


# ── 掃描與處理紀錄 ────────────────────────────────────────────────────────


class ScanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_lists_documents_with_json_sidecars(self):
        _touch(self.root / "券商A" / "a.pdf")
        (self.root / "券商A" / "a.json").write_text('{"market": "TW"}', encoding="utf-8")
        _touch(self.root / "b.docx")
        _touch(self.root / "notes.txt")
        _touch(self.root / ".hidden.pdf")
        _touch(self.root / ".x.pdf.part")
        _touch(self.root / "c.pdf.part")
        cands = base.scan(self.root)
        self.assertEqual([c.rel for c in cands], ["b.docx", "券商A/a.pdf"])
        self.assertEqual(cands[1].metadata.market, "TW")
        self.assertIsNone(cands[0].metadata)

    def test_files_still_being_written_wait(self):
        _touch(self.root / "new.pdf", mtime=time.time())
        self.assertEqual(base.scan(self.root, settle_seconds=60), [])
        self.assertEqual(len(base.scan(self.root, settle_seconds=0)), 1)

    def test_invalid_sidecar_becomes_an_error_not_silence(self):
        _touch(self.root / "a.pdf")
        (self.root / "a.json").write_text('{"market": "TWSE"}', encoding="utf-8")
        (cand,) = base.scan(self.root)
        self.assertIsNone(cand.metadata)
        self.assertIn("不是標準市場代碼", cand.sidecar_error)

    def test_ambiguous_sidecar(self):
        _touch(self.root / "a.pdf")
        _touch(self.root / "a.docx")
        (self.root / "a.json").write_text('{"market": "TW"}', encoding="utf-8")
        self.assertTrue(all("不只一份" in c.sidecar_error for c in base.scan(self.root)))

    def test_manifest_and_json_conflict(self):
        _touch(self.root / "a.pdf")
        (self.root / "a.json").write_text('{"market": "TW"}', encoding="utf-8")
        (self.root / "_metadata.csv").write_text("file,market\na.pdf,US\n", encoding="utf-8")
        (cand,) = base.scan(self.root)
        self.assertIn("不猜", cand.sidecar_error)

    def test_manifest_rows_and_broken_manifest(self):
        _touch(self.root / "a.pdf")
        _touch(self.root / "b.pdf")
        (self.root / "_metadata.csv").write_text("file,market\na.pdf,US\n", encoding="utf-8")
        a, b = base.scan(self.root)
        self.assertEqual((a.metadata.market, b.metadata), ("US", None))
        (self.root / "_metadata.csv").write_text("file,market\na.pdf,XX\n", encoding="utf-8")
        self.assertTrue(all(c.sidecar_error for c in base.scan(self.root)), "壞的清單整份不採信")

    def test_fingerprint_changes_with_file_and_sidecar(self):
        pdf = _touch(self.root / "a.pdf")
        fp0 = base.scan(self.root)[0].fingerprint
        (self.root / "a.json").write_text('{"market": "TW"}', encoding="utf-8")
        fp1 = base.scan(self.root)[0].fingerprint
        (self.root / "a.json").write_text('{"market": "US"}', encoding="utf-8")
        fp2 = base.scan(self.root)[0].fingerprint
        _touch(pdf, b"%PDF-1.4\nchanged")
        fp3 = base.scan(self.root)[0].fingerprint
        self.assertEqual(len({fp0, fp1, fp2, fp3}), 4)

    def test_missing_root_is_empty(self):
        self.assertEqual(base.scan(self.root / "nope"), [])


class StateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_round_trip_and_done(self):
        _touch(self.dir / "src" / "a.pdf")
        (cand,) = base.scan(self.dir / "src")
        state = base.ConnectorState.load(self.dir / "state" / "x.json")
        self.assertFalse(state.is_done(cand))
        state.record(cand, "ingested", "h" * 64)
        state.last_poll = 123.0
        state.save()
        again = base.ConnectorState.load(self.dir / "state" / "x.json")
        self.assertTrue(again.is_done(cand))
        self.assertEqual(again.last_poll, 123.0)
        self.assertEqual(again.files["a.pdf"]["file_hash"], "h" * 64)

    def test_corrupt_state_is_an_error(self):
        path = self.dir / "x.json"
        path.write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            base.ConnectorState.load(path)

    def test_due(self):
        state = base.ConnectorState(path=self.dir / "x.json")
        self.assertTrue(state.due(180))
        state.last_poll = 1000.0
        self.assertFalse(state.due(180, now=1000.0 + 60 * 60))
        self.assertTrue(state.due(180, now=1000.0 + 180 * 60))


# ── 連接器 ──────────────────────────────────────────────────────────────


class FakeBucket:
    """S3 的最小替身：key → (bytes, mtime)。"""

    def __init__(self):
        self.objects: dict[str, tuple[bytes, int | None]] = {}
        self.fail: set[str] = set()
        self.downloads: list[str] = []

    def list_objects(self, prefix):
        return [(k, len(v[0])) for k, v in sorted(self.objects.items()) if k.startswith(prefix)]

    def download_to(self, key, target):
        if key in self.fail:
            raise s3.BucketError("boom")
        self.downloads.append(key)
        Path(target).write_bytes(self.objects[key][0])

    def head_object(self, key):
        mtime = self.objects[key][1]
        return {"Metadata": {"mtime": str(mtime)} if mtime else {}}


class S3ConnectorTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.landing = Path(self._tmp.name) / "landing"
        self.bucket = FakeBucket()

    def test_pulls_documents_sidecars_and_manifest_only(self):
        b = self.bucket
        b.objects["feed/券商A/a.pdf"] = (b"%PDF-a", 1_700_000_000)
        b.objects["feed/券商A/a.json"] = (b'{"market": "TW"}', None)
        b.objects["feed/_metadata.csv"] = (b"file,market\n", None)
        b.objects["feed/sub/_metadata.csv"] = (b"file,market\n", None)
        b.objects["feed/readme.txt"] = (b"x", None)
        b.objects["feed/../evil.pdf"] = (b"x", None)
        b.objects["other/b.pdf"] = (b"x", None)
        conn = s3.S3Connector("feed", "feed/", self.landing, b)
        report = conn.fetch()
        self.assertEqual(report.landed, 3)
        self.assertEqual(sorted(p.relative_to(self.landing).as_posix() for p in self.landing.rglob("*")
                                if p.is_file()), ["_metadata.csv", "券商A/a.json", "券商A/a.pdf"])
        self.assertEqual(int((self.landing / "券商A" / "a.pdf").stat().st_mtime), 1_700_000_000)
        self.assertTrue(any("不安全" in e for e in report.errors))

    def test_size_only_rerun_and_failures(self):
        self.bucket.objects["feed/a.pdf"] = (b"%PDF-a", None)
        self.bucket.objects["feed/b.pdf"] = (b"%PDF-b", None)
        self.bucket.fail = {"feed/b.pdf"}
        conn = s3.S3Connector("feed", "feed/", self.landing, self.bucket)
        first = conn.fetch()
        self.assertEqual((first.landed, first.failed), (1, 1))
        self.assertFalse((self.landing / ".b.pdf.part").exists())
        self.bucket.fail = set()
        second = conn.fetch()
        self.assertEqual((second.landed, second.failed), (1, 0), "已落地且大小相同的不再拉")
        self.assertEqual(self.bucket.downloads, ["feed/a.pdf", "feed/b.pdf"])

    def test_bucket_config_from_env(self):
        env = {"CONNECTOR_X_BUCKET": "b", "CONNECTOR_X_ACCESS_KEY_ID": "id",
               "CONNECTOR_X_SECRET_ACCESS_KEY": "fixed-test-secret-s3"}
        cfg = s3.BucketConfig.from_env("CONNECTOR_X", env)
        self.assertEqual((cfg.bucket, cfg.endpoint, cfg.region), ("b", None, "auto"))
        with self.assertRaisesRegex(s3.ConnectorConfigError, "CONNECTOR_X_SECRET_ACCESS_KEY"):
            s3.BucketConfig.from_env("CONNECTOR_X", {"CONNECTOR_X_BUCKET": "b", "CONNECTOR_X_ACCESS_KEY_ID": "id"})

    def test_s3_bucket_paginates_through_client(self):
        calls = []

        class Client:
            def list_objects_v2(self, **kw):
                calls.append(kw)
                if "ContinuationToken" not in kw:
                    return {"Contents": [{"Key": "p/a.pdf", "Size": 3}], "IsTruncated": True,
                            "NextContinuationToken": "t"}
                return {"Contents": [{"Key": "p/b.pdf", "Size": 4}], "IsTruncated": False}

            def head_object(self, **kw):
                raise RuntimeError("403")

        cfg = s3.BucketConfig("bkt", "id", "fixed-test-secret-s3", None, "auto")
        bucket = s3.S3Bucket(cfg, client=Client())
        self.assertEqual(bucket.list_objects("p/"), [("p/a.pdf", 3), ("p/b.pdf", 4)])
        self.assertEqual(calls[0], {"Bucket": "bkt", "Prefix": "p/"})
        with self.assertRaises(s3.BucketError):
            bucket.head_object("p/a.pdf")


PROFILE_HEAD = textwrap.dedent("""\
    [institution]
    name = "測試"
    product_name = "測試研報"
""")


class ProfileAndBuildTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def load(self, body: str):
        (self.dir / "profile.toml").write_text(PROFILE_HEAD + textwrap.dedent(body), encoding="utf-8")
        return load_profile(self.dir)

    def test_defaults_without_section(self):
        prof = self.load("")
        self.assertEqual(prof.connectors.enabled, ())
        self.assertEqual(dict(prof.connectors.sources), {})

    def test_all_kinds(self):
        (self.dir / "plugins").mkdir()
        (self.dir / "plugins" / "feed.py").write_text("def fetch(landing, params):\n    pass\n", encoding="utf-8")
        prof = self.load(f"""
            [connectors]
            enabled = ["inbox", "drop"]
            [connectors.sources.drop]
            kind = "folder"
            path = "{self.dir / 'drop'}"
            settle_seconds = 0
            [connectors.sources.inbox]
            kind = "s3"
            prefix = "reports/"
            env = "CONNECTOR_INBOX"
            poll_minutes = 30
            [connectors.sources.vendor]
            kind = "plugin"
            plugin = "feed"
            [connectors.sources.vendor.params]
            url = "https://example.invalid/feed"
            page_size = 50
        """)
        c = prof.connectors
        self.assertEqual([s.name for s in c.enabled_specs()], ["inbox", "drop"])
        self.assertEqual(c.sources["inbox"].poll_minutes, 30)
        self.assertTrue(str(c.sources["inbox"].landing).endswith("data/connectors/inbox"))
        self.assertEqual(c.sources["drop"].root, self.dir / "drop")
        self.assertEqual(c.sources["vendor"].plugin, self.dir.resolve() / "plugins" / "feed.py")
        self.assertEqual(dict(c.sources["vendor"].params), {"url": "https://example.invalid/feed", "page_size": 50})

    def test_rejections(self):
        cases = [
            ('[connectors]\nenabled = ["nope"]\n', "沒有這個連接器"),
            ('[connectors.sources.x]\nkind = "ftp"\n', "不支援的連接器類型"),
            ('[connectors.sources.x]\nkind = "folder"\n', "必填"),
            ('[connectors.sources.x]\nkind = "folder"\npath = "a"\nprefix = "p/"\n', "未知的鍵"),
            ('[connectors.sources.x]\nkind = "s3"\nprefix = "p"\nenv = "E"\n', "以 / 結尾"),
            ('[connectors.sources.x]\nkind = "s3"\nprefix = "p/"\nenv = "lower"\n', "大寫"),
            ('[connectors.sources.x]\nkind = "plugin"\nplugin = "missing"\n', "找不到 plugin 檔"),
            ('[connectors.sources.x]\nkind = "plugin"\nplugin = "../x"\n', "plugin 名稱"),
            ('[connectors.sources.X]\nkind = "folder"\npath = "a"\n', "連接器名稱"),
            ('[connectors.sources.x]\nkind = "folder"\npath = "a"\npoll_minutes = 1\n', "介於"),
            ('[connectors]\nenabled = ["x", "x"]\n[connectors.sources.x]\nkind = "folder"\npath = "a"\n', "重複"),
        ]
        for body, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ProfileError, reason):
                    self.load(body)

    def test_plugin_params_must_be_scalars(self):
        (self.dir / "plugins").mkdir()
        (self.dir / "plugins" / "feed.py").write_text("def fetch(landing, params):\n    pass\n", encoding="utf-8")
        with self.assertRaisesRegex(ProfileError, "只接受字串"):
            self.load('[connectors.sources.x]\nkind = "plugin"\nplugin = "feed"\n'
                      '[connectors.sources.x.params]\nnested = {a = 1}\n')

    def _plugin_spec(self, code: str):
        (self.dir / "plugins").mkdir(exist_ok=True)
        (self.dir / "plugins" / "feed.py").write_text(textwrap.dedent(code), encoding="utf-8")
        prof = self.load(f"""
            [connectors.sources.vendor]
            kind = "plugin"
            plugin = "feed"
            landing = "{self.dir / 'landing'}"
            [connectors.sources.vendor.params]
            n = 2
        """)
        return prof.connectors.sources["vendor"]

    def test_plugin_fetch_writes_into_landing(self):
        spec = self._plugin_spec("""
            from app.services.connectors.base import FetchReport

            def fetch(landing, params):
                for i in range(params["n"]):
                    (landing / f"r{i}.pdf").write_bytes(b"%PDF-1.4")
                return FetchReport(landed=params["n"])
        """)
        conn = connectors.build(spec)
        self.assertEqual(conn.fetch().landed, 2)
        self.assertEqual(sorted(p.name for p in (self.dir / "landing").iterdir()), ["r0.pdf", "r1.pdf"])

    def test_plugin_load_failures_are_explicit(self):
        for code, reason in (
            ("raise ImportError('vendor sdk missing')\n", "vendor sdk missing"),
            ("def fetch(:\n", "SyntaxError"),
            ("FETCH = 1\n", "缺 fetch"),
        ):
            with self.subTest(reason=reason):
                spec = self._plugin_spec(code)
                with self.assertRaisesRegex(connectors.ConnectorError, reason):
                    connectors.build(spec)

    def test_plugin_must_return_fetch_report(self):
        spec = self._plugin_spec("def fetch(landing, params):\n    return 3\n")
        with self.assertRaisesRegex(connectors.ConnectorError, "FetchReport"):
            connectors.build(spec).fetch()

    def test_only_the_named_plugin_file_is_loaded(self):
        (self.dir / "plugins").mkdir()
        (self.dir / "plugins" / "other.py").write_text("raise SystemExit('不該被載入')\n", encoding="utf-8")
        spec = self._plugin_spec("def fetch(landing, params):\n    pass\n")
        connectors.build(spec)  # other.py 沒被 import（否則 SystemExit）

    def test_folder_connector_missing_dir_is_a_failure(self):
        prof = self.load(f'[connectors.sources.d]\nkind = "folder"\npath = "{self.dir / "gone"}"\n')
        report = connectors.build(prof.connectors.sources["d"]).fetch()
        self.assertEqual(report.failed, 1)

    def test_s3_build_needs_env(self):
        prof = self.load('[connectors.sources.i]\nkind = "s3"\nprefix = "p/"\nenv = "CONNECTOR_I"\n')
        with self.assertRaisesRegex(connectors.ConnectorError, "CONNECTOR_I_BUCKET"):
            connectors.build(prof.connectors.sources["i"], environ={})
        env = {"CONNECTOR_I_BUCKET": "b", "CONNECTOR_I_ACCESS_KEY_ID": "i",
               "CONNECTOR_I_SECRET_ACCESS_KEY": "fixed-test-secret-i"}
        self.assertIsInstance(connectors.build(prof.connectors.sources["i"], environ=env), s3.S3Connector)


class TingfongProfileTests(unittest.TestCase):
    def test_tingfong_declares_current_sources_but_polls_nothing(self):
        prof = load_profile(Path(__file__).resolve().parents[1] / "profiles" / "tingfong")
        c = prof.connectors
        self.assertEqual(c.enabled, (), "廷豐的來源由主機層的 sync 殼執行，框架輪詢不啟用")
        self.assertEqual(c.sources["nas"].kind, "folder")
        self.assertEqual(c.sources["r2-inbox"].kind, "s3")
        self.assertEqual(c.sources["r2-inbox"].prefix, "inbox/", "與 scripts/r2_inbox.py 的 INBOX_PREFIX 一致")
        self.assertEqual(c.sources["nas"].path, c.sources["r2-inbox"].landing, "兩條都落在同一個本地鏡像")

    def test_r2_inbox_prefix_matches_script(self):
        from scripts import r2_inbox

        prof = load_profile(Path(__file__).resolve().parents[1] / "profiles" / "tingfong")
        self.assertEqual(prof.connectors.sources["r2-inbox"].prefix, r2_inbox.INBOX_PREFIX)
        self.assertEqual(prof.connectors.sources["r2-inbox"].landing, r2_inbox.LOCAL_MIRROR)


class SidecarJsonRoundTripTests(unittest.TestCase):
    def test_to_json_is_json_serialisable(self):
        meta = sidecar.validate(dict(SidecarValidationTests.FULL), "x")
        json.dumps(meta.to_json(), ensure_ascii=False)


if __name__ == "__main__":
    unittest.main()
