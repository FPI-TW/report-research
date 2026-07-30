"""M4b 共用證據帳本（app/services/evidence.py）測試。

覆蓋：corpus/external 序列化 round-trip、去重、evidence_id 決定性與篡改偵測、
歷史列（空/壞 manifest）安全退化、[n] 渲染的首次出現序與穩定性、受控建構器。
"""

import hashlib
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import evidence as ev  # noqa: E402
from app.services.trusted_market_data import TrustedDataPoint  # noqa: E402


def _ledger_with_corpus():
    led = ev.EvidenceLedger()
    e1 = led.add_corpus(report_id="r-1", file_name="a.pdf", market="TW",
                        report_date="2026-06-01")
    e2 = led.add_corpus(report_id="r-2", file_name="b.pdf", market="US",
                        report_date="2026-05-01")
    return led, e1, e2


class LedgerBasicsTests(unittest.TestCase):
    def test_corpus_roundtrip(self):
        led, e1, e2 = _ledger_with_corpus()
        manifest = led.to_manifest()
        self.assertEqual(manifest["schema_version"], ev.EVIDENCE_SCHEMA_VERSION)
        restored = ev.EvidenceLedger.from_manifest(manifest)
        self.assertEqual(list(restored), [e1, e2])

    def test_external_roundtrip(self):
        led = ev.EvidenceLedger()
        e = led.add_external(
            url="https://example.com/x", title="新聞", source_type="web",
            profile_id="controlled-research-web",
            published_at="2026-07-01T00:00:00+00:00",
            retrieved_at="2026-07-14T06:00:00+00:00",
            snapshot_ref="snapshot://controlled-research-web/x",
            content_hash=hashlib.sha256(b"x").hexdigest(),
            canonical_payload=b"x",
        )
        restored = ev.EvidenceLedger.from_manifest(led.to_manifest())
        self.assertEqual(list(restored), [e])
        self.assertEqual(list(restored)[0].kind, "external")

    def test_corpus_dedup_same_report(self):
        led = ev.EvidenceLedger()
        e1 = led.add_corpus(report_id="r-1")
        e2 = led.add_corpus(report_id="r-1", file_name="ignored-later.pdf")
        self.assertEqual(len(led), 1)
        self.assertEqual(e1.evidence_id, e2.evidence_id)
        self.assertEqual(e1, e2)  # 首次登錄勝（不可變）

    def test_external_dedup_same_url(self):
        led = ev.EvidenceLedger()
        kwargs = dict(
            url="https://example.com/x", profile_id="controlled-research-web",
            snapshot_ref="snapshot://controlled-research-web/x",
            content_hash=hashlib.sha256(b"x").hexdigest(), canonical_payload=b"x",
        )
        led.add_external(**kwargs)
        led.add_external(**kwargs)
        self.assertEqual(len(led), 1)

    def test_chunk_level_ids_differ_from_report_level(self):
        led = ev.EvidenceLedger()
        a = led.add_corpus(report_id="r-1")
        b = led.add_corpus(report_id="r-1", chunk_id="c-9")
        self.assertNotEqual(a.evidence_id, b.evidence_id)
        self.assertEqual(len(led), 2)

    def test_evidence_id_deterministic_across_ledgers_and_order(self):
        led1 = ev.EvidenceLedger()
        led2 = ev.EvidenceLedger()
        a1 = led1.add_corpus(report_id="r-1")
        led1.add_corpus(report_id="r-2")
        led2.add_corpus(report_id="r-2")
        a2 = led2.add_corpus(report_id="r-1")
        self.assertEqual(a1.evidence_id, a2.evidence_id)

    def test_merge_dedups(self):
        led1, _, _ = _ledger_with_corpus()
        led2 = ev.EvidenceLedger()
        led2.add_corpus(report_id="r-2")  # 與 led1 相同身分
        led2.add_external(
            url="https://example.com/x", profile_id="controlled-research-web",
            snapshot_ref="snapshot://controlled-research-web/x",
            content_hash=hashlib.sha256(b"x").hexdigest(), canonical_payload=b"x",
        )
        led1.merge(led2)
        self.assertEqual(len(led1), 3)  # r-1, r-2, external


class ValidationTests(unittest.TestCase):
    def test_validate_ok(self):
        led, _, _ = _ledger_with_corpus()
        self.assertEqual(ev.validate_manifest(led.to_manifest()), [])

    def test_tampered_id_detected(self):
        led, _, _ = _ledger_with_corpus()
        manifest = led.to_manifest()
        manifest["evidence"][0]["evidence_id"] = "deadbeefdeadbeef"
        errors = ev.validate_manifest(manifest)
        self.assertTrue(any("mismatch" in e for e in errors))
        with self.assertRaises(ev.EvidenceValidationError):
            ev.EvidenceLedger.from_manifest(manifest)

    def test_bad_kind_and_missing_fields(self):
        manifest = {
            "schema_version": 1,
            "evidence": [
                {"evidence_id": "x", "kind": "magic"},
                {"evidence_id": "y", "kind": "corpus"},           # 缺 report_id
                {"evidence_id": "z", "kind": "external", "url": "ftp://x"},
            ],
        }
        errors = ev.validate_manifest(manifest)
        self.assertGreaterEqual(len(errors), 3)

    def test_unsupported_schema_version(self):
        errors = ev.validate_manifest({"schema_version": 99, "evidence": []})
        self.assertTrue(errors)


class LenientLoadTests(unittest.TestCase):
    def test_legacy_rows_degrade_to_empty(self):
        for obj in (None, {}, [], "garbage", {"schema_version": 99},
                    {"schema_version": 1, "evidence": "not-a-list"}):
            with self.subTest(obj=obj):
                led = ev.EvidenceLedger.load(obj)
                self.assertEqual(len(led), 0)

    def test_load_valid_manifest(self):
        led, e1, _ = _ledger_with_corpus()
        restored = ev.EvidenceLedger.load(led.to_manifest())
        self.assertEqual(restored.get(e1.evidence_id), e1)


class RenderCitationsTests(unittest.TestCase):
    def test_first_use_order_and_repeat_stability(self):
        led, e1, e2 = _ledger_with_corpus()
        text = (
            f"論點甲[[ev:{e2.evidence_id}]]。"
            f"論點乙[[ev:{e1.evidence_id}]][[ev:{e2.evidence_id}]]。"
        )
        out = ev.render_citations(text, led)
        self.assertEqual(out.text, "論點甲[1]。論點乙[2][1]。")
        self.assertEqual(out.number_of[e2.evidence_id], 1)
        self.assertEqual(out.number_of[e1.evidence_id], 2)
        self.assertEqual([e.evidence_id for e in out.ordered],
                         [e2.evidence_id, e1.evidence_id])
        self.assertEqual(out.n_unknown, 0)

    def test_unknown_id_removed_and_counted(self):
        led, e1, _ = _ledger_with_corpus()
        text = f"已知[[ev:{e1.evidence_id}]]，未知[[ev:aaaabbbbccccdddd]]。"
        out = ev.render_citations(text, led)
        self.assertEqual(out.text, "已知[1]，未知。")
        self.assertEqual(out.n_unknown, 1)

    def test_malformed_placeholders_never_leak(self):
        """非法形狀（大寫 hex、非 hex、過短、空 id）也不得漏內部 token 到輸出，
        且計入 n_unknown（審查 M4b-1：模型抄寫 id 漂移是 M5/M7 的常見失效模式）。"""
        led, e1, _ = _ledger_with_corpus()
        text = (
            f"合法[[ev:{e1.evidence_id}]]、"
            "大寫[[ev:ABCD1234EF567890]]、非hex[[ev:xyz-123]]、"
            "過短[[ev:abc]]、空[[ev:]]。"
        )
        out = ev.render_citations(text, led)
        self.assertEqual(out.text, "合法[1]、大寫、非hex、過短、空。")
        self.assertNotIn("[[ev:", out.text)
        self.assertEqual(out.n_unknown, 4)

    def test_multi_section_reassembly_single_render_is_consistent(self):
        # M7 語義：各節只寫 evidence_id 佔位；不論節次如何重排，最終「單次」
        # 渲染內同一 evidence 恆同號、編號連續無空洞。
        led, e1, e2 = _ledger_with_corpus()
        sec_a = f"A 節[[ev:{e1.evidence_id}]]。"
        sec_b = f"B 節[[ev:{e2.evidence_id}]][[ev:{e1.evidence_id}]]。"
        out1 = ev.render_citations(sec_a + sec_b, led)
        out2 = ev.render_citations(sec_b + sec_a, led)
        for out in (out1, out2):
            nums = sorted(out.number_of.values())
            self.assertEqual(nums, [1, 2])
        # e1 兩處佔位在每次渲染內恆同號：正序時 e1=[1]（2 次）、重排後 e1=[2]（2 次）
        self.assertEqual(out1.text.count("[1]"), 2)
        self.assertEqual(out2.text.count("[2]"), 2)


class ControlledConstructorsTests(unittest.TestCase):
    def test_from_trusted_point(self):
        point = TrustedDataPoint(
            value="1085.00", unit="TWD",
            as_of=datetime(2026, 7, 14, 5, 59, tzinfo=timezone.utc),
            published_at=datetime(2026, 7, 14, 5, 30, tzinfo=timezone.utc),
            url="https://example.com/quote/2330", source_type="exchange",
            profile_id="trusted-quote", snapshot_ref="snapshot://trusted-quote/p",
            canonical_payload=b"p",
            content_hash=hashlib.sha256(b"p").hexdigest(),
            provider="fake-quote", category="quote", subject="台積電 2330",
        )
        e = ev.from_trusted_point(point, retrieved_at="2026-07-14T06:00:00+00:00")
        self.assertEqual(e.kind, "external")
        self.assertEqual(e.source_type, "exchange")
        self.assertEqual(e.url, "https://example.com/quote/2330")
        self.assertEqual(e.content_hash, point.content_hash)
        self.assertEqual(e.profile_id, point.profile_id)
        self.assertEqual(e.snapshot_ref, point.snapshot_ref)
        self.assertEqual(e.published_at, "2026-07-14T05:30:00+00:00")

    def test_from_ext_source_requires_verified_adapter_metadata(self):
        e = ev.from_ext_source({
            "title": "新聞", "url": "https://e.com/a", "profile_id": "controlled-web",
            "snapshot_ref": "snapshot://controlled-web/a", "canonical_payload": b"a",
            "content_hash": hashlib.sha256(b"a").hexdigest(),
        }, retrieved_at="2026-07-14T06:00:00+00:00")
        self.assertEqual(e.source_type, "web")
        self.assertEqual(e.retrieved_at, "2026-07-14T06:00:00+00:00")

    def test_from_ext_source_rejects_non_http(self):
        with self.assertRaises(ValueError):
            ev.from_ext_source({"url": "javascript:alert(1)"}, retrieved_at=None)


class ManifestBuilderTests(unittest.TestCase):
    def test_manifest_from_answer(self):
        sources = [
            {"n": 1, "report_id": "r-1", "file_name": "a.pdf", "market": "TW",
             "report_date": "2026-06-01", "is_latest": True},
            {"n": 2, "report_id": "r-2", "file_name": "b.pdf", "market": None,
             "report_date": None, "is_latest": False},
        ]
        ext = [
            {"title": "新聞", "url": "https://e.com/a"},
            {"title": "壞的", "url": "not-a-url"},  # 無效外部來源跳過（fail-open）
        ]
        manifest = ev.manifest_from_answer(
            sources, ext, retrieved_at="2026-07-14T06:00:00+00:00"
        )
        kinds = [d["kind"] for d in manifest["evidence"]]
        self.assertEqual(kinds, ["corpus", "corpus"])
        self.assertEqual(ev.validate_manifest(manifest), [])

    def test_manifest_from_answer_empty_returns_none(self):
        self.assertIsNone(ev.manifest_from_answer([], [], retrieved_at=None))

    def test_unverified_model_external_source_is_not_evidence(self):
        """只有模型回傳的標題與網址沒有取得證據，不能進入帳本。"""
        manifest = ev.manifest_from_answer(
            [],
            [{"title": "模型自稱來源", "url": "https://attacker.example/a"}],
            retrieved_at="2026-07-14T06:00:00+00:00",
        )
        self.assertIsNone(manifest)


if __name__ == "__main__":
    unittest.main()
