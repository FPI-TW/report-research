"""M4a 受信任時效資料契約（app/services/trusted_market_data.py）測試。

全部用 fake provider 與注入的 now（決定性），不發真實 HTTP。安全語義：
任何失敗（無 provider、逾時、欄位缺漏、過舊、不在 allowlist、限流）都收斂為
TrustedDataUnavailable → 呼叫端安全婉拒；只有 CancelledError 原樣上拋。
"""

import asyncio
import hashlib
import sys
import unittest
from dataclasses import fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import trusted_market_data as tmd  # noqa: E402

NOW = datetime(2026, 7, 14, 6, 0, 0, tzinfo=timezone.utc)


def _hash(s: str = "payload") -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _spec(category="quote", **over):
    base = dict(
        name=f"fake-{category}",
        category=category,
        allowed_domains=("example.com",),
        max_age=timedelta(minutes=15),
        cache_ttl=timedelta(seconds=60),
        timeout=0.2,
        min_interval=1.0,
        exchange_tz="Asia/Taipei",
    )
    base.update(over)
    return tmd.ProviderSpec(**base)


def _point(category="quote", **over):
    base = dict(
        value="1085.00",
        unit="TWD",
        as_of=NOW - timedelta(minutes=1),
        published_at=None,
        url="https://example.com/quote/2330",
        source_type="exchange",
        profile_id=f"trusted-{category}",
        snapshot_ref=f"snapshot://trusted-{category}/payload",
        canonical_payload=b"payload",
        content_hash=_hash(),
        provider=f"fake-{category}",
        category=category,
        subject="台積電 2330",
    )
    base.update(over)
    return tmd.TrustedDataPoint(**base)


class _Provider:
    def __init__(self, point=None, exc=None, delay=0.0):
        self.point = point
        self.exc = exc
        self.delay = delay
        self.calls = 0

    async def fetch(self, query):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc is not None:
            raise self.exc
        return self.point


class _Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmd.clear_providers()

    def tearDown(self):
        tmd.clear_providers()


class FetchSuccessTests(_Base):
    async def test_success_each_category(self):
        for cat in ("quote", "filing", "rate"):
            with self.subTest(category=cat):
                tmd.clear_providers()
                tmd.register_provider(_spec(cat), _Provider(point=_point(cat)))
                point = await tmd.fetch_trusted(cat, "有效問題", now=NOW)
                self.assertEqual(point.category, cat)
                self.assertEqual(point.value, "1085.00")
                self.assertEqual(point.source_type, "exchange")

    async def test_no_provider_unavailable(self):
        with self.assertRaises(tmd.TrustedDataUnavailable):
            await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)

    async def test_disabled_flag_blocks_even_with_provider(self):
        tmd.register_provider(_spec(), _Provider(point=_point()))
        orig = tmd.TRUSTED_DATA_ENABLED
        tmd.TRUSTED_DATA_ENABLED = False
        try:
            with self.assertRaises(tmd.TrustedDataUnavailable):
                await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)
        finally:
            tmd.TRUSTED_DATA_ENABLED = orig


class ValidationTests(_Base):
    async def _expect_unavailable(self, point, spec=None):
        tmd.clear_providers()
        tmd.register_provider(spec or _spec(), _Provider(point=point))
        with self.assertRaises(tmd.TrustedDataUnavailable):
            await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)

    async def test_stale_data_rejected(self):
        await self._expect_unavailable(
            _point(as_of=NOW - timedelta(minutes=30))  # max_age 15 分鐘
        )

    async def test_future_as_of_rejected(self):
        """供應商時鐘或時區錯誤不得讓未來資料冒充即時行情。"""
        await self._expect_unavailable(
            _point(as_of=NOW + timedelta(minutes=1))
        )

    async def test_naive_as_of_rejected(self):
        await self._expect_unavailable(
            _point(as_of=datetime(2026, 7, 14, 5, 59))  # 無時區
        )

    async def test_domain_not_in_allowlist(self):
        await self._expect_unavailable(_point(url="https://notexample.com/q"))

    async def test_similar_domain_rejected(self):
        await self._expect_unavailable(_point(url="https://evil-example.com/q"))
        await self._expect_unavailable(
            _point(url="https://example.com.attacker.io/q")
        )

    async def test_subdomain_allowed(self):
        tmd.register_provider(_spec(), _Provider(
            point=_point(url="https://api.example.com/q")))
        point = await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)
        self.assertIn("api.example.com", point.url)

    async def test_non_http_scheme_rejected(self):
        await self._expect_unavailable(_point(url="ftp://example.com/q"))

    async def test_plain_http_rejected(self):
        """明文 http 可被中間人改寫，不得作為「受信任」來源。"""
        await self._expect_unavailable(_point(url="http://example.com/q"))

    async def test_missing_value_rejected(self):
        await self._expect_unavailable(_point(value=""))

    async def test_bad_content_hash_rejected(self):
        await self._expect_unavailable(_point(content_hash="not-a-hash"))
        await self._expect_unavailable(_point(content_hash=""))

    async def test_content_hash_mismatch_rejected(self):
        """格式合法但對不上 canonical_payload → 拒收。

        上一題只餵得進 _SHA256_RE 那道格式檢查，validate_point 真正重算 sha256 的
        那兩行**沒有任何測試覆蓋**——刪掉它們整套依然全綠，而它是「快照與宣稱的
        雜湊是同一份東西」的唯一保證。
        """
        await self._expect_unavailable(
            _point(content_hash=hashlib.sha256(b"different payload").hexdigest())
        )


class FailureModeTests(_Base):
    async def test_provider_exception_unavailable(self):
        tmd.register_provider(_spec(), _Provider(exc=RuntimeError("boom")))
        with self.assertRaises(tmd.TrustedDataUnavailable):
            await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)

    async def test_provider_returns_none_unavailable(self):
        tmd.register_provider(_spec(), _Provider(point=None))
        with self.assertRaises(tmd.TrustedDataUnavailable):
            await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)

    async def test_provider_timeout_unavailable(self):
        tmd.register_provider(_spec(timeout=0.05), _Provider(
            point=_point(), delay=0.5))
        with self.assertRaises(tmd.TrustedDataUnavailable):
            await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)

    async def test_cancellation_propagates_and_no_cache_residue(self):
        provider = _Provider(point=_point(), delay=1.0)
        tmd.register_provider(_spec(min_interval=0.0), provider)
        task = asyncio.create_task(
            tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)
        )
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        # 取消不得寫快取：之後再取必須重新打 provider
        provider.delay = 0.0
        await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)
        self.assertEqual(provider.calls, 2)


class CacheAndRateLimitTests(_Base):
    async def test_cache_hit_within_ttl(self):
        provider = _Provider(point=_point())
        tmd.register_provider(_spec(), provider)
        p1 = await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)
        p2 = await tmd.fetch_trusted(
            "quote", "台積電 收盤價", now=NOW + timedelta(seconds=30)
        )  # 正規化後同 key（空白差異）
        self.assertEqual(provider.calls, 1)
        self.assertEqual(p1, p2)

    async def test_cache_expires_after_ttl(self):
        provider = _Provider(point=_point())
        tmd.register_provider(_spec(cache_ttl=timedelta(seconds=60)), provider)
        await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)
        # TTL 過期後重打 provider；但回傳點的 as_of 已超過 max_age → 驗證擋下
        with self.assertRaises(tmd.TrustedDataUnavailable):
            await tmd.fetch_trusted(
                "quote", "台積電收盤價", now=NOW + timedelta(minutes=30)
            )
        self.assertEqual(provider.calls, 2)

    async def test_rate_limit_blocks_second_miss(self):
        provider = _Provider(point=_point())
        tmd.register_provider(_spec(min_interval=10.0), provider)
        await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)
        with self.assertRaises(tmd.TrustedDataUnavailable):
            await tmd.fetch_trusted("quote", "聯電收盤價", now=NOW)  # 不同 key、限流中
        self.assertEqual(provider.calls, 1)

    async def test_cache_hit_revalidates_age(self):
        """cache_ttl 與 max_age 是獨立旋鈕：TTL 未過但資料已超過最大年齡時，
        快取命中不得回過期值（審查 M4a-2）。"""
        provider = _Provider(point=_point())  # as_of = NOW-1min
        tmd.register_provider(
            _spec(cache_ttl=timedelta(hours=1), max_age=timedelta(minutes=15)),
            provider,
        )
        await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)
        # TTL 內（+20min），但資料年齡 21min > max_age 15min → 不得回快取；
        # 重打 provider 拿到同一過舊點 → 驗證擋下 → unavailable
        with self.assertRaises(tmd.TrustedDataUnavailable):
            await tmd.fetch_trusted(
                "quote", "台積電收盤價", now=NOW + timedelta(minutes=20)
            )
        self.assertEqual(provider.calls, 2)

    async def test_stale_failure_allowlist_each_category(self):
        """spec 驗收：每類時效題都有過期/provider 失敗/不在 allowlist 的測試。"""
        for cat in ("quote", "filing", "rate"):
            with self.subTest(category=cat, mode="stale"):
                tmd.clear_providers()
                tmd.register_provider(_spec(cat), _Provider(
                    point=_point(cat, as_of=NOW - timedelta(minutes=30))))
                with self.assertRaises(tmd.TrustedDataUnavailable):
                    await tmd.fetch_trusted(cat, "有效問題", now=NOW)
            with self.subTest(category=cat, mode="provider_failure"):
                tmd.clear_providers()
                tmd.register_provider(_spec(cat), _Provider(
                    exc=RuntimeError("down")))
                with self.assertRaises(tmd.TrustedDataUnavailable):
                    await tmd.fetch_trusted(cat, "有效問題", now=NOW)
            with self.subTest(category=cat, mode="allowlist"):
                tmd.clear_providers()
                tmd.register_provider(_spec(cat), _Provider(
                    point=_point(cat, url="https://evil.com/x")))
                with self.assertRaises(tmd.TrustedDataUnavailable):
                    await tmd.fetch_trusted(cat, "有效問題", now=NOW)

    async def test_rate_limit_allows_after_interval(self):
        provider = _Provider(point=_point())
        tmd.register_provider(_spec(min_interval=10.0), provider)
        await tmd.fetch_trusted("quote", "台積電收盤價", now=NOW)
        await tmd.fetch_trusted(
            "quote", "聯電收盤價", now=NOW + timedelta(seconds=11)
        )
        self.assertEqual(provider.calls, 2)


class InferCategoryTests(unittest.TestCase):
    def test_filing(self):
        self.assertEqual(tmd.infer_category("台積電最新財報公告"), "filing")
        self.assertEqual(tmd.infer_category("鴻海法說會結論"), "filing")

    def test_rate(self):
        self.assertEqual(tmd.infer_category("聯準會這次升息幾碼"), "rate")
        self.assertEqual(tmd.infer_category("央行利率決策結果"), "rate")

    def test_quote_default(self):
        self.assertEqual(tmd.infer_category("台積電今天收盤價"), "quote")
        self.assertEqual(tmd.infer_category("現在大盤多少點"), "quote")


class TrustedPointContractTests(unittest.TestCase):
    def test_snapshot_and_canonical_payload_are_required_contract_fields(self):
        names = {f.name for f in fields(tmd.TrustedDataPoint)}
        self.assertTrue({"profile_id", "snapshot_ref", "canonical_payload"} <= names)


if __name__ == "__main__":
    unittest.main()
