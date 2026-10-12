"""API 用戶端服務（app/services/api_clients.py）不連 DB 的部分：金鑰格式、雜湊、格式守門與驗證規則。

對真的 PostgreSQL 的行為（建立、停用、輪替、額度、cascade、稽核）在 `tests/test_api_clients_db.py`。
驗證規則一律在碰 DB 之前判斷：這裡把 `SessionFactory` 換成一碰就失敗的假物件，確保不合法的輸入
不會先開交易才被擋。
"""

from __future__ import annotations

import hashlib
import re
import unittest
from unittest import mock

from app.services import api_clients
from app.services.tagging import MARKETS


def _no_db():
    raise AssertionError("不合法的輸入不該碰到 DB")


class KeyFormatTests(unittest.TestCase):
    def test_generated_key_shape(self):
        raw, prefix, key_hash = api_clients.generate_key()
        self.assertRegex(prefix, r"^[0-9a-f]{8}$")
        self.assertTrue(raw.startswith(f"rmk_{prefix}_"))
        secret = raw[len(f"rmk_{prefix}_"):]
        self.assertRegex(secret, r"^[A-Za-z0-9_-]{43}$")
        self.assertEqual(key_hash, hashlib.sha256(raw.encode()).hexdigest())
        self.assertEqual(api_clients._parse_key(raw), prefix)

    def test_keys_are_unique(self):
        keys = {api_clients.generate_key()[0] for _ in range(50)}
        self.assertEqual(len(keys), 50)

    def test_hash_is_sha256_hex(self):
        self.assertEqual(api_clients.hash_key("abc"), hashlib.sha256(b"abc").hexdigest())
        self.assertRegex(api_clients.hash_key("rmk_x"), r"^[0-9a-f]{64}$")

    def test_hash_never_equals_raw_or_prefix(self):
        raw, prefix, key_hash = api_clients.generate_key()
        self.assertNotIn(raw, key_hash)
        self.assertNotEqual(prefix, key_hash[:8])

    def test_parse_rejects_malformed(self):
        raw, prefix, _ = api_clients.generate_key()
        secret = raw.split("_", 2)[2]
        # 大寫前綴的案例固定以 A 開頭：隨機 prefix 可能全是數字，`.upper()` 不變就成了一把合法的 key。
        bad = [
            None, 123, "", "rmk_", raw + "x", raw[:-1], " " + raw, raw + "\n",
            f"RMK_{prefix}_{secret}", f"rmk_A{prefix[1:].upper()}_{secret}", f"rmk_{prefix[:7]}_{secret}",
            f"xyz_{prefix}_{secret}", f"rmk_{prefix}_{secret[:-1]}!", f"Bearer {raw}",
        ]
        for value in bad:
            with self.subTest(value=value):
                self.assertIsNone(api_clients._parse_key(value))


class ResolveKeyGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_malformed_key_never_queries_db(self):
        with mock.patch.object(api_clients, "SessionFactory", _no_db):
            for value in ("", "nope", "rmk_12345678_short", None):
                self.assertIsNone(await api_clients.resolve_key(value))

    async def test_consume_quota_rejects_bad_id_without_db(self):
        with mock.patch.object(api_clients, "SessionFactory", _no_db):
            self.assertEqual(await api_clients.consume_quota(0), (False, 0))
            self.assertEqual(await api_clients.consume_quota(True), (False, 0))

    async def test_get_client_bad_id_is_none(self):
        with mock.patch.object(api_clients, "SessionFactory", _no_db):
            self.assertIsNone(await api_clients.get_client(-1))


class EntitlementValidationTests(unittest.TestCase):
    def norm(self, ents):
        return api_clients.normalize_entitlements(ents)

    def assertInvalid(self, ents):
        with self.assertRaises(api_clients.ApiClientError) as cm:
            self.norm(ents)
        self.assertEqual(cm.exception.code, "invalid_input")
        self.assertTrue(cm.exception.message)

    def test_market_required_and_non_empty(self):
        self.assertInvalid({})
        self.assertInvalid({"market": []})
        self.assertInvalid({"source": ["凱基"]})

    def test_market_codes_must_match_tagging(self):
        self.assertEqual(self.norm({"market": list(MARKETS)})["market"], tuple(sorted(MARKETS)))
        self.assertInvalid({"market": ["TW", "JP"]})
        self.assertInvalid({"market": ["tw"]})

    def test_strip_dedupe_sort(self):
        got = self.norm({"market": [" US", "TW", "US "], "source": ["  凱基 ", "元大", "凱基"]})
        self.assertEqual(got, {"market": ("TW", "US"), "source": ("元大", "凱基")})

    def test_empty_list_removes_dimension(self):
        got = self.norm({"market": ["TW"], "source": [], "report_type": None})
        self.assertEqual(got, {"market": ("TW",)})

    def test_blank_values_are_rejected_not_dropped(self):
        # 丟掉的話 ["  "] 會讓 source 從「限定」變成「不限」
        self.assertInvalid({"market": ["TW"], "source": ["  "]})
        self.assertInvalid({"market": [""]})

    def test_unknown_dimension(self):
        self.assertInvalid({"market": ["TW"], "broker": ["x"]})

    def test_value_types(self):
        self.assertInvalid({"market": "TW"})
        self.assertInvalid({"market": ["TW"], "source": "凱基"})
        self.assertInvalid({"market": ["TW"], "source": [1]})
        self.assertInvalid(["market", "TW"])

    def test_max_values_per_dimension(self):
        ok = [f"s{i}" for i in range(api_clients.ENTITLEMENT_MAX_VALUES)]
        self.assertEqual(len(self.norm({"market": ["TW"], "source": ok})["source"]), 200)
        self.assertInvalid({"market": ["TW"], "source": ok + ["extra"]})
        # 去重之後才算數
        self.assertEqual(len(self.norm({"market": ["TW"], "source": ok + ok})["source"]), 200)

    def test_output_order_follows_dimensions(self):
        got = self.norm({"instrument_type": ["股票"], "market": ["TW"], "source": ["a"]})
        self.assertEqual(list(got), ["market", "source", "instrument_type"])

    def test_dimension_vocabulary_matches_migration(self):
        from app.services.schema_migrations import schema_source_text

        sql = schema_source_text()
        dims = ", ".join(f"'{d}'" for d in api_clients.ENTITLEMENT_DIMENSIONS)
        self.assertIn(f"dimension IN ({dims})", sql)
        scopes = re.search(r"scopes <@ ARRAY\[([^\]]*)\]", sql)
        self.assertIsNotNone(scopes)
        self.assertEqual(set(re.findall(r"'([^']+)'", scopes.group(1))), set(api_clients.KEY_SCOPES))


class CreateValidationTests(unittest.IsolatedAsyncioTestCase):
    BASE = dict(actor_id=None, name="測試用戶端", scopes=["search"], rate_limit_per_min=60, daily_quota=1000,
                entitlements={"market": ["TW"]})

    async def assertInvalid(self, **overrides):
        with mock.patch.object(api_clients, "SessionFactory", _no_db):
            with self.assertRaises(api_clients.ApiClientError) as cm:
                await api_clients.create_client(**{**self.BASE, **overrides})
        self.assertEqual(cm.exception.code, "invalid_input")

    async def test_name_rules(self):
        await self.assertInvalid(name="")
        await self.assertInvalid(name="   ")
        await self.assertInvalid(name="x" * 101)
        await self.assertInvalid(name=None)

    async def test_scope_rules(self):
        await self.assertInvalid(scopes=["search", "admin"])
        await self.assertInvalid(scopes="search")

    async def test_limit_rules(self):
        for bad in (0, 6001, -1, True, 1.5, "60"):
            with self.subTest(rate=bad):
                await self.assertInvalid(rate_limit_per_min=bad)
        for bad in (0, 1_000_001, False, None):
            with self.subTest(quota=bad):
                await self.assertInvalid(daily_quota=bad)

    async def test_entitlement_rules(self):
        await self.assertInvalid(entitlements={})
        await self.assertInvalid(entitlements={"market": ["XX"]})

    async def test_note_rules(self):
        await self.assertInvalid(note="x" * (api_clients.NOTE_MAX + 1))
        await self.assertInvalid(note=5)


class UpdateValidationTests(unittest.IsolatedAsyncioTestCase):
    async def assertInvalid(self, **kwargs):
        with mock.patch.object(api_clients, "SessionFactory", _no_db):
            with self.assertRaises(api_clients.ApiClientError) as cm:
                await api_clients.update_client(actor_id=None, client_id=1, **kwargs)
        self.assertEqual(cm.exception.code, "invalid_input")

    async def test_nothing_to_change(self):
        await self.assertInvalid()

    async def test_field_rules(self):
        await self.assertInvalid(enabled="yes")
        await self.assertInvalid(scopes=["write"])
        await self.assertInvalid(rate_limit_per_min=0)
        await self.assertInvalid(daily_quota=2_000_000)

    async def test_bad_id_is_not_found(self):
        with mock.patch.object(api_clients, "SessionFactory", _no_db):
            for call in (
                api_clients.rotate_key(actor_id=None, client_id=0),
            ):
                with self.assertRaises(api_clients.ApiClientError) as cm:
                    await call
                self.assertEqual(cm.exception.code, "not_found")

    async def test_replace_entitlements_validates_first(self):
        with mock.patch.object(api_clients, "SessionFactory", _no_db):
            with self.assertRaises(api_clients.ApiClientError) as cm:
                await api_clients.replace_entitlements(actor_id=None, client_id=1, entitlements={"source": ["x"]})
        self.assertEqual(cm.exception.code, "invalid_input")


class ErrorTests(unittest.TestCase):
    def test_error_carries_code_and_message(self):
        exc = api_clients.ApiClientError("not_found", "API 用戶端不存在")
        self.assertEqual(exc.code, "not_found")
        self.assertEqual(exc.message, "API 用戶端不存在")
        self.assertEqual(str(exc), "API 用戶端不存在")


if __name__ == "__main__":
    unittest.main()
