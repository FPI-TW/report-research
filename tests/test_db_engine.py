"""DB 引擎組裝：池上界與查詢逾時。

這些值全部只在 `create_async_engine` 被呼叫的**那一瞬間**生效（引擎是模組級的，
建好就固定），所以測試的做法是攔截那一次呼叫、檢查它收到的 kwargs——不連 DB，
也不能連（本機沒有 psql，CI 也沒有 pgvector 容器）。

反面來說這也是這組測試唯一能守的東西：它守得住「參數有沒有被送進去」，守不住
「送進去之後 PG 是否照做」。後者要真的連 DB 才驗得到，屬待驗。
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

import sqlalchemy.ext.asyncio as sa_async

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import app.config as config  # noqa: E402
import app.services.db as dbmod  # noqa: E402

# 重載前必須先從 os.environ 清掉的鍵：pytest 行程可能已經帶著某些值進來，
# 不清就驗不出「預設值」。
_DB_ENV_KEYS = (
    "DB_POOL_SIZE",
    "DB_MAX_OVERFLOW",
    "DB_POOL_TIMEOUT",
    "DB_POOL_RECYCLE",
    "DB_STATEMENT_TIMEOUT_MS",
    "DB_IDLE_TX_TIMEOUT_MS",
    "DB_MAINTENANCE_STATEMENT_TIMEOUT_MS",
)


def _reload_db_capturing(env: dict[str, str] | None = None) -> dict:
    """在受控 env 下重載 app.services.db，回傳 create_async_engine 收到的 (url, kwargs)。

    `db.py` 寫的是 `from sqlalchemy.ext.asyncio import create_async_engine`，所以
    要 patch 的是**來源模組**的屬性——重載時那行 from-import 會重新綁定到 patch 上。
    """
    captured: dict = {}

    def _fake_create(url, **kwargs):
        captured["url"] = url
        captured["kwargs"] = kwargs
        return mock.MagicMock(name="fake_engine")

    with mock.patch.dict(os.environ, env or {}, clear=False):
        for key in _DB_ENV_KEYS:
            if key not in (env or {}):
                os.environ.pop(key, None)
        prev_settings = config._SETTINGS
        config._SETTINGS = None
        try:
            with mock.patch.object(sa_async, "create_async_engine", _fake_create):
                importlib.reload(dbmod)
        finally:
            config._SETTINGS = prev_settings
    return captured


class _RecordingSession:
    """只記下被 execute 的 SQL 字面值；relax_statement_timeout 不需要真的 DB。"""

    def __init__(self):
        self.executed: list[str] = []

    async def execute(self, clause, *args, **kwargs):
        self.executed.append(str(clause))
        return None


class EngineKwargsTests(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        # 本檔會把模組級的 engine/SessionFactory 換成 mock 版；跑完必須還原成真的，
        # 否則同一個 pytest session 裡後面才 import app.services.db 的測試會拿到 mock。
        config._SETTINGS = None
        importlib.reload(dbmod)

    def test_pool_bounds_are_explicit(self):
        """池的四個參數都必須顯式送出——少任何一個就退回 SQLAlchemy 預設，
        而「預設是多少」正是這次要從註解裡拿掉的不確定性。"""
        kwargs = _reload_db_capturing()["kwargs"]
        for key in ("pool_size", "max_overflow", "pool_timeout", "pool_recycle"):
            self.assertIn(key, kwargs, f"{key} 未送進 create_async_engine")
        self.assertTrue(kwargs["pool_pre_ping"])

    def test_pool_defaults(self):
        kwargs = _reload_db_capturing()["kwargs"]
        self.assertEqual(kwargs["pool_size"], 5)
        self.assertEqual(kwargs["max_overflow"], 15)
        self.assertEqual(kwargs["pool_timeout"], 10.0)
        self.assertEqual(kwargs["pool_recycle"], 1800)

    def test_pool_ceiling_fits_postgres_max_connections(self):
        """池上限是 per-process。PG 跑官方 pgvector:pg16 且無 conf 覆寫
        ⇒ max_connections=100、superuser_reserved 3 ⇒ 一般角色 97 條。
        web(1 process) ＋ 同步鏈三支批次都容得下才算合格。"""
        kwargs = _reload_db_capturing()["kwargs"]
        ceiling = kwargs["pool_size"] + kwargs["max_overflow"]
        self.assertLessEqual(ceiling + 3 * 2, 97)
        # 也不能小到連既有的併發閘都餵不飽：ask 3 ＋ 研報 fanout 3 ＋ 記帳 1。
        self.assertGreaterEqual(ceiling, 7)

    def test_statement_timeout_present_and_bounded(self):
        """沒有 statement_timeout ⇒ 單一慢查詢可無上限佔住一條連線。
        這是本次改動真正要解掉的那條因果（不是「併發打滿」）。"""
        kwargs = _reload_db_capturing()["kwargs"]
        server_settings = kwargs["connect_args"]["server_settings"]
        self.assertEqual(server_settings["statement_timeout"], "60000")
        # asyncpg 的 startup 參數只吃字串，型別錯會在連線時才炸。
        for value in server_settings.values():
            self.assertIsInstance(value, str)

    def test_idle_in_transaction_timeout_is_declared_but_off_by_default(self):
        """預設關是刻意的：sync_new_reports.py 先 report_exists() 開了交易，才去
        呼叫 LLM 標註（150s）與跑嵌入（大檔數分鐘），中間不 commit。設了它＝
        生產同步把報告靜默丟進 FAIL_LOG。鍵仍必須送出，讓部署端可以只在 web 開。"""
        kwargs = _reload_db_capturing()["kwargs"]
        server_settings = kwargs["connect_args"]["server_settings"]
        self.assertIn("idle_in_transaction_session_timeout", server_settings)
        self.assertEqual(server_settings["idle_in_transaction_session_timeout"], "0")

    def test_env_overrides_every_knob(self):
        captured = _reload_db_capturing(
            {
                "DB_POOL_SIZE": "9",
                "DB_MAX_OVERFLOW": "1",
                "DB_POOL_TIMEOUT": "2.5",
                "DB_POOL_RECYCLE": "60",
                "DB_STATEMENT_TIMEOUT_MS": "12345",
                "DB_IDLE_TX_TIMEOUT_MS": "67890",
            }
        )
        kwargs = captured["kwargs"]
        self.assertEqual(kwargs["pool_size"], 9)
        self.assertEqual(kwargs["max_overflow"], 1)
        self.assertEqual(kwargs["pool_timeout"], 2.5)
        self.assertEqual(kwargs["pool_recycle"], 60)
        server_settings = kwargs["connect_args"]["server_settings"]
        self.assertEqual(server_settings["statement_timeout"], "12345")
        self.assertEqual(
            server_settings["idle_in_transaction_session_timeout"], "67890"
        )

    def test_db_url_env_still_wins(self):
        """REPORT_MARK_DB_URL 是集中化之前就存在的例外，不得被這次改動吃掉。"""
        captured = _reload_db_capturing(
            {"REPORT_MARK_DB_URL": "postgresql+asyncpg://u:p@example:1/x"}
        )
        self.assertEqual(captured["url"], "postgresql+asyncpg://u:p@example:1/x")


class RelaxStatementTimeoutTests(unittest.TestCase):
    """維運豁免：一刀切的 statement_timeout 會砍掉匯入最後那句 ANALYZE。"""

    @classmethod
    def tearDownClass(cls):
        config._SETTINGS = None
        importlib.reload(dbmod)

    def _run_relax(self, env: dict[str, str] | None = None) -> str:
        session = _RecordingSession()
        with mock.patch.dict(os.environ, env or {}, clear=False):
            for key in _DB_ENV_KEYS:
                if key not in (env or {}):
                    os.environ.pop(key, None)
            prev = config._SETTINGS
            config._SETTINGS = None
            try:
                asyncio.run(dbmod.relax_statement_timeout(session))
            finally:
                config._SETTINGS = prev
        self.assertEqual(len(session.executed), 1)
        return session.executed[0]

    def test_uses_set_local_not_set(self):
        """SET 是 session 級，而 SQLAlchemy 的 reset_on_return='rollback' 不還原 GUC
        ⇒ 豁免會跟著池化連線流到下一個借用者。只有 SET LOCAL 是交易範圍的。"""
        sql = self._run_relax()
        self.assertRegex(sql, r"(?i)^\s*SET\s+LOCAL\s+statement_timeout\s*=")

    def test_default_is_unbounded(self):
        self.assertRegex(self._run_relax(), r"=\s*0\s*$")

    def test_maintenance_value_is_overridable(self):
        sql = self._run_relax({"DB_MAINTENANCE_STATEMENT_TIMEOUT_MS": "300000"})
        self.assertRegex(sql, r"=\s*300000\s*$")

    def test_value_is_coerced_to_int(self):
        """SET 不吃 bind 參數，只能字串內插，所以整條路徑上不得有任何非整數能抵達
        SQL 字串。攔截點有兩層（config 的 int() 與 relax 自己的 int()），這裡只斷言
        結果：髒值一律 ValueError，不會被拼進去。"""
        with self.assertRaises(ValueError):
            self._run_relax({"DB_MAINTENANCE_STATEMENT_TIMEOUT_MS": "0; DROP TABLE x"})


class AnalyzeCallSitesTests(unittest.TestCase):
    """ANALYZE research.report_chunk 的每個呼叫點都必須先放寬逾時。

    為什麼用靜態掃描而不是行為測試：這三支都是長跑批次腳本，跑一次要數小時且會
    真的寫 DB。而漏掉任何一處的症狀是「統計靜默過期」——沒有例外、沒有日誌，
    只有檢索計畫慢慢變差。這種缺陷只有靜態守門攔得到。
    """

    # normalize_chunks.py 刻意不列入：它是已知陷阱腳本（會抹掉段落結構），
    # 把它加進這份清單等於暗示它可以跑。
    SCRIPTS = ("ingest_all.py", "run_ingest.py", "sync_new_reports.py")

    def test_every_analyze_is_preceded_by_relax(self):
        for name in self.SCRIPTS:
            path = REPO_ROOT / "scripts" / name
            lines = path.read_text(encoding="utf-8").splitlines()
            hits = [
                i
                for i, line in enumerate(lines)
                if re.search(r"ANALYZE\s+research\.report_chunk", line)
            ]
            self.assertTrue(hits, f"{name} 找不到 ANALYZE 呼叫點（是不是改名了？）")
            for i in hits:
                window = "\n".join(lines[max(0, i - 6) : i])
                self.assertIn(
                    "relax_statement_timeout(",
                    window,
                    f"{name}:{i + 1} 的 ANALYZE 前面沒有 relax_statement_timeout()",
                )


if __name__ == "__main__":
    unittest.main()
