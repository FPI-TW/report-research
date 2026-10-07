"""功能旗標 DB 讀取的假物件（不是測試檔：檔名不以 test_ 開頭）。

`app/services/feature_flags.py` 沒給 session_factory 時用模組層的 `SessionFactory` 讀覆寫與（只知道 UUID 時的）角色。
- `NoRowsSession`：任何查詢都回零列＝DB 沒有任何覆寫。`tests/conftest.py` 每題預設裝上它，既有測試在 registry
  預設（＝只看環境變數）下跑，不去讀 `REPORT_MARK_DB_URL` 指的庫（本機預設是生產庫）。
- `flag_rows(...)`：在範圍內把覆寫換成指定的列 `(key, enabled, allow_roles, allow_users)`，角色查詢回 `role`。
真的 SQL 由 tests/test_feature_flags_db.py 與 tests/test_usage_events_db.py 對 PostgreSQL 驗。
"""

from __future__ import annotations

import contextlib
from unittest import mock


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._rows[0][0] if self._rows else None


class NoRowsSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *_a, **_k):
        return _Result([])


def fake_factory(rows=(), role: str | None = None):
    """覆寫查詢回 rows；`research.app_user` 的角色查詢回 role（None＝查不到）。"""

    class _Session(NoRowsSession):
        async def execute(self, stmt, params=None):
            if "research.app_user" in str(stmt):
                return _Result([(role,)] if role else [])
            return _Result(rows)

    return _Session


@contextlib.contextmanager
def flag_rows(*rows, role: str | None = None):
    """DB 覆寫換成指定的列；進出都清快取。"""
    from app.services import feature_flags

    feature_flags.invalidate()
    with mock.patch.object(feature_flags, "SessionFactory", fake_factory(rows, role)):
        try:
            yield
        finally:
            feature_flags.invalidate()
