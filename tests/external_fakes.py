"""對外 API 測試共用的假物件：`deps.api_clients`、DB session、物件儲存與檢索列。

`tests/test_external_auth.py` 與 `tests/test_external_api.py` 共用。全部不連網、不連 DB：

- `FakeApiClients`：記憶體金鑰表，`resolve_key` 每次查表（停用、輪替立即生效，同真的實作），
  `consume_quota` 以 `daily_quota` 計數（被拒的請求也計入，同真的實作）。
- `FakeSession`：只認得 `web/routers/external.py` 的兩條指標查詢，依綁定參數以
  `Entitlement.allows` 模擬 SQL 的 entitlement 條件，並排除 `hidden` 的研報。真的 SQL 行為由
  `tests/test_external_api_db.py` 對 PostgreSQL 驗。
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone

from app.services.api_clients import ApiClient
from app.services.entitlement import Entitlement
from app.services.rows import ChunkRow

_NOW = datetime(2026, 10, 7, tzinfo=timezone.utc)


def make_key() -> str:
    return f"rmk_{secrets.token_hex(4)}_{secrets.token_urlsafe(32)}"


def make_client(client_id: int = 1, *, scopes=("search", "report.file"), rate: int = 600, quota: int = 1000,
                entitlements=None, enabled: bool = True) -> ApiClient:
    return ApiClient(
        id=client_id, name=f"用戶端{client_id}", key_prefix=f"{client_id:08x}", enabled=enabled,
        scopes=frozenset(scopes), rate_limit_per_min=rate, daily_quota=quota,
        entitlements=entitlements or {"market": ("TW",)}, note=None,
        created_at=_NOW, updated_at=_NOW, key_rotated_at=None, last_used_at=None,
    )


class FakeApiClients:
    """`deps.api_clients` 的替身（只有對外認證用到的兩個函式）。"""

    def __init__(self):
        self.keys: dict[str, int] = {}
        self.clients: dict[int, ApiClient] = {}
        self.usage: dict[int, int] = {}
        self.resolve_calls = 0
        self.fail_resolve = False

    def add(self, client: ApiClient) -> str:
        key = make_key()
        self.clients[client.id] = client
        self.keys[key] = client.id
        return key

    def disable(self, client_id: int) -> None:
        self.clients[client_id] = replace(self.clients[client_id], enabled=False)

    def rotate(self, client_id: int) -> str:
        self.keys = {k: v for k, v in self.keys.items() if v != client_id}
        key = make_key()
        self.keys[key] = client_id
        return key

    async def resolve_key(self, raw):
        self.resolve_calls += 1
        if self.fail_resolve:
            raise RuntimeError("db down")
        cid = self.keys.get(raw)
        client = self.clients.get(cid) if cid is not None else None
        if client is None or not client.enabled:
            return None
        return client

    async def consume_quota(self, client_id: int, *, today=None):
        client = self.clients.get(client_id)
        if client is None:
            return False, 0
        self.usage[client_id] = self.usage.get(client_id, 0) + 1
        count = self.usage[client_id]
        return count <= client.daily_quota, count


@dataclass
class Report:
    report_id: str
    file_hash: str
    file_name: str = "a.pdf"
    market: str = "TW"
    source: str | None = "kgi"
    report_type: str | None = "個股"
    instrument_types: tuple[str, ...] = ("stock",)
    hidden: bool = False
    object_key: str | None = field(default=None)

    def __post_init__(self):
        if self.object_key is None:
            self.object_key = f"originals/{self.file_hash[:2]}/{self.file_hash}.pdf"


def chunk_row(report: Report, *, chunk_index: int = 0, content: str = "台積電 營收 成長") -> ChunkRow:
    return ChunkRow(
        chunk_id=f"c-{report.report_id}-{chunk_index}", report_id=report.report_id, file_hash=report.file_hash,
        file_name=report.file_name, title=f"{report.file_name} 標題", market=report.market, source=report.source,
        summary="摘要", report_date=date(2026, 10, 1), report_type=report.report_type,
        instrument_types=list(report.instrument_types), relates_stock=True, relates_futures=False,
        stock_targets=["2330"], futures_targets=None, chunk_index=chunk_index, content=content, distance=0.1,
    )


def _ent_from_params(params: dict) -> Entitlement:
    return Entitlement(
        markets=tuple(params["ent_markets"]),
        sources=tuple(params["ent_sources"]) if "ent_sources" in params else None,
        report_types=tuple(params["ent_report_types"]) if "ent_report_types" in params else None,
        instrument_types=tuple(params["ent_instrument_types"]) if "ent_instrument_types" in params else None,
    )


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class FakeSession:
    def __init__(self, reports: dict[str, Report], log: list | None = None):
        self.reports = reports
        self.log = log if log is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _visible(self, rid: str, ent: Entitlement) -> Report | None:
        r = self.reports.get(rid)
        if r is None or r.hidden:
            return None
        if not ent.allows(market=r.market, source=r.source, report_type=r.report_type,
                          instrument_types=r.instrument_types):
            return None
        return r

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        params = params or {}
        self.log.append((sql, params))
        assert "report_visibility" in sql, "對外查詢必須帶可見性條件"
        ent = _ent_from_params(params)
        if "ANY(CAST(:ids" in sql:
            rows = []
            for rid in params["ids"]:
                r = self._visible(rid, ent)
                if r is not None:
                    rows.append((r.report_id, r.file_name, r.file_hash, r.object_key))
            return _Result(rows)
        r = self._visible(params["id"], ent)
        return _Result([] if r is None else [(r.file_name, r.file_hash, r.object_key)])


class FakeStorage:
    enabled = True
    mode = "r2"

    def __init__(self, reports: dict[str, Report] | None = None, *, head_exc=None, presign_fail_keys=()):
        self.by_key = {r.object_key: r for r in (reports or {}).values()}
        self.head_exc = head_exc
        self.presign_fail_keys = set(presign_fail_keys)
        self.heads: list[str] = []
        self.presigns: list[tuple[str, dict]] = []

    def head_object(self, key):
        self.heads.append(key)
        if self.head_exc is not None:
            raise self.head_exc
        r = self.by_key[key]
        return {"Metadata": {"sha256": r.file_hash}}

    def presign_get(self, key, **kwargs):
        from app.services.object_storage import ObjectStorageError

        self.presigns.append((key, kwargs))
        if key in self.presign_fail_keys:
            raise ObjectStorageError("presign failed")
        return f"https://signed.example.test/{key}?ttl={kwargs.get('ttl_seconds')}"
