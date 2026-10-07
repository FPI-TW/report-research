"""對外 API 用戶端（`research.api_client`／`api_client_entitlement`／`api_client_usage`，revision 0010）。

`/external/v1/*` 以 `Authorization: Bearer <api_key>` 認證、不走 session。一個用戶端一把金鑰；
本模組是金鑰、授權範圍（entitlement）、限流與每日額度設定的唯一規則所在——管理路由與 CLI 都經這裡，
驗證不在路由層另寫一份（比照 `accounts.py`）。

四個刻意的設計：

1. **DB 只存金鑰的 sha256**（`key_hash`）與可公開的 `key_prefix`。原始金鑰只在 `create_client`／`rotate_key`
   的回傳值出現一次，稽核 detail 只記 prefix。金鑰是 32 bytes 隨機值，不需要 Argon2 那種慢雜湊：
   慢雜湊防的是低熵的人選密碼被字典攻擊，這裡沒有那種東西，而每個請求都要驗一次。
2. **每次查 DB、不快取**（`resolve_key`）。停用與輪替要下一個請求就生效，與 `accounts.resolve_session` 同理。
3. **授權範圍是 allowlist、fail-closed**。`market` 必填且非空；其他維度沒有設定＝不限，所以「空清單」
   只代表移除該維度，不可能誤寫成「什麼都不給」以外的意思。庫裡若有用戶端缺了 `market`
   （只可能是直接改 DB），`resolve_key` 一律拒絕，不讓它退化成全市場。
4. **稽核與變更同一筆交易**（`accounts.record_audit`）。detail 可含名稱、prefix、變更前後的設定，
   絕不含原始金鑰或 `key_hash`。

每日額度以台北時間的日曆日計（`consume_quota`），被拒絕的請求也計入當天的計數。
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.services.accounts import record_audit
from app.services.db import SessionFactory
from app.services.tagging import MARKETS

logger = logging.getLogger(__name__)

# 金鑰能呼叫的端點（與 research.api_client.scopes 的 CHECK 逐字一致）。
KEY_SCOPES: frozenset[str] = frozenset({"search", "report.file"})
# 授權範圍的維度（與 research.api_client_entitlement.dimension 的 CHECK 逐字一致）。
ENTITLEMENT_DIMENSIONS: tuple[str, ...] = ("market", "source", "report_type", "instrument_type")

NAME_MAX = 100
NOTE_MAX = 1000
ENTITLEMENT_MAX_VALUES = 200
ENTITLEMENT_VALUE_MAX = 200
# 與 revision 0010 的 CHECK 範圍一致。
RATE_LIMIT_RANGE = (1, 6000)
DAILY_QUOTA_RANGE = (1, 1_000_000)

KEY_PREFIX = "rmk"
# rmk_<8 個小寫 hex>_<secrets.token_urlsafe(32)：43 個 base64url 字元>
_KEY_RE = re.compile(r"^rmk_([0-9a-f]{8})_([A-Za-z0-9_-]{43})$")
# 每日額度的「一天」：台北時間（UTC+8，無日光節約），同 llm_usage。
TZ = timezone(timedelta(hours=8))
# key_prefix 撞到既有的（8 個 hex，機率極低）時重新產生的次數。
_PREFIX_RETRIES = 5


@dataclass(frozen=True)
class ApiClient:
    id: int
    name: str
    key_prefix: str
    enabled: bool
    scopes: frozenset[str]
    rate_limit_per_min: int
    daily_quota: int
    entitlements: dict[str, tuple[str, ...]]  # 只含有設定的維度；market 一定存在且非空
    note: str | None
    created_at: datetime
    updated_at: datetime
    key_rotated_at: datetime | None
    last_used_at: datetime | None


class ApiClientError(Exception):
    """管理動作被拒絕。`code` 給機器判斷，`message` 是給人看的中文（路由層直接轉成 detail）。

    code：`invalid_input`（400）、`not_found`（404）、`name_taken`（409）。
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ── 金鑰 ────────────────────────────────────────────────────────────────


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """回 (raw, prefix, key_hash)。raw 只能交給管理員一次，不落庫、不進日誌與稽核。"""
    prefix = secrets.token_hex(4)
    raw = f"{KEY_PREFIX}_{prefix}_{secrets.token_urlsafe(32)}"
    return raw, prefix, hash_key(raw)


def _parse_key(raw) -> str | None:
    """格式正確時回 prefix，否則 None。"""
    if not isinstance(raw, str):
        return None
    m = _KEY_RE.fullmatch(raw)
    return m.group(1) if m else None


# ── 驗證 ────────────────────────────────────────────────────────────────


def _invalid(message: str) -> ApiClientError:
    return ApiClientError("invalid_input", message)


def _check_name(name) -> str:
    if not isinstance(name, str):
        raise _invalid("名稱必須是文字")
    cleaned = name.strip()
    if not 1 <= len(cleaned) <= NAME_MAX:
        raise _invalid(f"名稱需為 1–{NAME_MAX} 個字元")
    return cleaned


def _check_scopes(scopes) -> frozenset[str]:
    if isinstance(scopes, str) or not isinstance(scopes, Iterable):
        raise _invalid("scopes 必須是清單")
    wanted = frozenset(scopes)
    unknown = {s for s in wanted if s not in KEY_SCOPES}
    if unknown:
        raise _invalid(f"不支援的 scope：{'、'.join(sorted(map(str, unknown)))}")
    return wanted


def _check_int(value, label: str, bounds: tuple[int, int]) -> int:
    lo, hi = bounds
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise _invalid(f"{label}需為 {lo}–{hi} 的整數")
    return value


def _check_note(note) -> str | None:
    """空白（含空字串）＝沒有備註。"""
    if note is None:
        return None
    if not isinstance(note, str):
        raise _invalid("備註必須是文字")
    cleaned = note.strip()
    if len(cleaned) > NOTE_MAX:
        raise _invalid(f"備註最多 {NOTE_MAX} 個字元")
    return cleaned or None


def normalize_entitlements(entitlements) -> dict[str, tuple[str, ...]]:
    """驗證並正規化授權範圍：去頭尾空白、去重、排序；空清單＝移除該維度（不限）。

    `market` 必填、非空，值必須是 `tagging.MARKETS` 的代碼。空字串值一律拒絕（不靜默丟掉——
    丟掉之後若整個維度變空，就從「限定」變成「不限」）。
    """
    if not isinstance(entitlements, Mapping):
        raise _invalid("授權範圍必須是 {維度: [值, …]}")
    unknown = [k for k in entitlements if k not in ENTITLEMENT_DIMENSIONS]
    if unknown:
        raise _invalid(f"不支援的授權維度：{'、'.join(sorted(map(str, unknown)))}")
    out: dict[str, tuple[str, ...]] = {}
    for dim in ENTITLEMENT_DIMENSIONS:
        values = entitlements.get(dim)
        if values is None:
            continue
        if isinstance(values, str) or not isinstance(values, Iterable):
            raise _invalid(f"{dim} 必須是清單")
        cleaned: set[str] = set()
        for v in values:
            if not isinstance(v, str):
                raise _invalid(f"{dim} 的值必須是文字")
            s = v.strip()
            if not s:
                raise _invalid(f"{dim} 不能有空白的值")
            if len(s) > ENTITLEMENT_VALUE_MAX:
                raise _invalid(f"{dim} 的值最多 {ENTITLEMENT_VALUE_MAX} 個字元")
            cleaned.add(s)
        if len(cleaned) > ENTITLEMENT_MAX_VALUES:
            raise _invalid(f"{dim} 最多 {ENTITLEMENT_MAX_VALUES} 個值")
        if dim == "market":
            bad = sorted(cleaned - set(MARKETS))
            if bad:
                raise _invalid(f"不支援的市場代碼：{'、'.join(bad)}")
        if cleaned:
            out[dim] = tuple(sorted(cleaned))
    if not out.get("market"):
        raise _invalid("必須至少允許一個市場（market）")
    return out


def _valid_id(client_id) -> bool:
    return isinstance(client_id, int) and not isinstance(client_id, bool) and client_id > 0


# ── 讀取 ────────────────────────────────────────────────────────────────

_COLUMNS = (
    "id, name, key_prefix, enabled, scopes, rate_limit_per_min, daily_quota, note, "
    "created_at, updated_at, key_rotated_at, last_used_at"
)


async def _entitlements_for(session, ids: list[int]) -> dict[int, dict[str, tuple[str, ...]]]:
    out: dict[int, dict[str, list[str]]] = {i: {} for i in ids}
    if ids:
        rows = (await session.execute(
            text(
                "SELECT client_id, dimension, value FROM research.api_client_entitlement "
                "WHERE client_id = ANY(CAST(:ids AS bigint[])) ORDER BY client_id, dimension, value"
            ),
            {"ids": ids},
        )).all()
        for cid, dim, value in rows:
            out[cid].setdefault(dim, []).append(value)
    return {
        cid: {d: tuple(dims[d]) for d in ENTITLEMENT_DIMENSIONS if d in dims}
        for cid, dims in out.items()
    }


def _make_client(row, entitlements: dict[str, tuple[str, ...]]) -> ApiClient:
    return ApiClient(
        id=row[0], name=row[1], key_prefix=row[2], enabled=row[3], scopes=frozenset(row[4] or ()),
        rate_limit_per_min=row[5], daily_quota=row[6], entitlements=entitlements, note=row[7],
        created_at=row[8], updated_at=row[9], key_rotated_at=row[10], last_used_at=row[11],
    )


async def _load(session, client_id: int, *, lock: bool = False) -> ApiClient | None:
    row = (await session.execute(
        text(f"SELECT {_COLUMNS} FROM research.api_client WHERE id = :id" + (" FOR UPDATE" if lock else "")),
        {"id": client_id},
    )).first()
    if row is None:
        return None
    return _make_client(row, (await _entitlements_for(session, [row[0]]))[row[0]])


async def resolve_key(raw: str) -> ApiClient | None:
    """Bearer 金鑰 → 啟用中的用戶端；格式不符、查無、停用、缺 market 都回 None。每次查 DB、不快取。"""
    prefix = _parse_key(raw)
    if prefix is None:
        return None
    async with SessionFactory() as session:
        row = (await session.execute(
            text(f"SELECT {_COLUMNS} FROM research.api_client WHERE key_hash = :h AND key_prefix = :p"),
            {"h": hash_key(raw), "p": prefix},
        )).first()
        if row is None or not row[3]:
            return None
        client = _make_client(row, (await _entitlements_for(session, [row[0]]))[row[0]])
    if not client.entitlements.get("market"):
        logger.warning("API 用戶端 %s（%s）沒有任何 market 授權，拒絕請求", client.id, client.key_prefix)
        return None
    return client


async def consume_quota(client_id: int, *, today: date | None = None) -> tuple[bool, int]:
    """當日計數原子 +1 並更新 last_used_at；回 (計數 ≤ daily_quota, 計數)。用戶端不存在回 (False, 0)。"""
    if not _valid_id(client_id):
        return False, 0
    day = today or datetime.now(TZ).date()
    async with SessionFactory() as session:
        row = (await session.execute(
            text(
                "WITH touch AS ("
                " UPDATE research.api_client SET last_used_at = now() WHERE id = :id RETURNING daily_quota"
                "), bump AS ("
                " INSERT INTO research.api_client_usage (client_id, day, request_count)"
                " SELECT :id, :day, 1 FROM touch"
                " ON CONFLICT (client_id, day) DO UPDATE"
                " SET request_count = research.api_client_usage.request_count + 1"
                " RETURNING request_count"
                ") SELECT bump.request_count, touch.daily_quota FROM bump, touch"
            ),
            {"id": client_id, "day": day},
        )).first()
        await session.commit()
    if row is None:
        return False, 0
    count, quota = int(row[0]), int(row[1])
    return count <= quota, count


async def list_clients() -> list[ApiClient]:
    async with SessionFactory() as session:
        rows = (await session.execute(text(f"SELECT {_COLUMNS} FROM research.api_client ORDER BY id"))).all()
        ents = await _entitlements_for(session, [r[0] for r in rows])
    return [_make_client(r, ents[r[0]]) for r in rows]


async def get_client(client_id: int) -> ApiClient | None:
    if not _valid_id(client_id):
        return None
    async with SessionFactory() as session:
        return await _load(session, client_id)


# ── 寫入 ────────────────────────────────────────────────────────────────


def _is_unique_violation(exc: IntegrityError, constraint: str) -> bool:
    orig = getattr(exc, "orig", None)
    name = getattr(orig, "constraint_name", None) or getattr(getattr(orig, "__cause__", None), "constraint_name", None)
    return name == constraint or constraint in str(exc)


async def _insert_entitlements(session, client_id: int, entitlements: dict[str, tuple[str, ...]]) -> None:
    for dim, values in entitlements.items():
        await session.execute(
            text(
                "INSERT INTO research.api_client_entitlement (client_id, dimension, value) "
                "SELECT :id, :dim, v FROM unnest(CAST(:vals AS text[])) AS v"
            ),
            {"id": client_id, "dim": dim, "vals": list(values)},
        )


def _ent_detail(entitlements: dict[str, tuple[str, ...]]) -> dict[str, list[str]]:
    return {d: list(v) for d, v in entitlements.items()}


async def _require(session, client_id: int) -> ApiClient:
    if not _valid_id(client_id):
        raise ApiClientError("not_found", "API 用戶端不存在")
    client = await _load(session, client_id, lock=True)
    if client is None:
        raise ApiClientError("not_found", "API 用戶端不存在")
    return client


async def create_client(*, actor_id: str | None, name: str, scopes: Iterable[str], rate_limit_per_min: int,
                        daily_quota: int, entitlements: Mapping[str, Sequence[str]], note: str | None = None
                        ) -> tuple[ApiClient, str]:
    """建立用戶端並產生金鑰；回 (client, raw_key)。raw_key 只此一次。"""
    clean_name = _check_name(name)
    clean_scopes = _check_scopes(scopes)
    rate = _check_int(rate_limit_per_min, "每分鐘限流", RATE_LIMIT_RANGE)
    quota = _check_int(daily_quota, "每日額度", DAILY_QUOTA_RANGE)
    ents = normalize_entitlements(entitlements)
    clean_note = _check_note(note)
    for _ in range(_PREFIX_RETRIES):
        raw, prefix, key_hash = generate_key()
        try:
            async with SessionFactory() as session:
                cid = (await session.execute(
                    text(
                        "INSERT INTO research.api_client (name, key_prefix, key_hash, scopes, rate_limit_per_min, "
                        "daily_quota, note, created_by) "
                        "VALUES (:name, :prefix, :hash, CAST(:scopes AS text[]), :rate, :quota, :note, "
                        "CAST(:actor AS uuid)) RETURNING id"
                    ),
                    {"name": clean_name, "prefix": prefix, "hash": key_hash, "scopes": sorted(clean_scopes),
                     "rate": rate, "quota": quota, "note": clean_note, "actor": actor_id},
                )).scalar_one()
                await _insert_entitlements(session, cid, ents)
                await record_audit(session, actor_id=actor_id, action="api_client.create", target_type="api_client",
                                   target_id=str(cid), detail={
                                       "name": clean_name, "key_prefix": prefix, "scopes": sorted(clean_scopes),
                                       "rate_limit_per_min": rate, "daily_quota": quota,
                                       "entitlements": _ent_detail(ents),
                                   })
                client = await _load(session, cid)
                await session.commit()
        except IntegrityError as exc:
            if _is_unique_violation(exc, "api_client_name_key"):
                raise ApiClientError("name_taken", f"API 用戶端「{clean_name}」已存在") from exc
            if _is_unique_violation(exc, "api_client_key_prefix_key"):
                continue
            raise
        assert client is not None
        return client, raw
    raise RuntimeError("連續產生的金鑰 prefix 都與既有的重複")


async def update_client(*, actor_id: str | None, client_id: int, enabled: bool | None = None,
                        scopes: Iterable[str] | None = None, rate_limit_per_min: int | None = None,
                        daily_quota: int | None = None, note: str | None = None) -> ApiClient:
    """改設定；None＝不變更。note 傳空字串＝清除備註。值與現況相同的欄位不寫、不記稽核。"""
    wanted: dict[str, Any] = {}
    if enabled is not None:
        if not isinstance(enabled, bool):
            raise _invalid("enabled 必須是布林值")
        wanted["enabled"] = enabled
    if scopes is not None:
        wanted["scopes"] = _check_scopes(scopes)
    if rate_limit_per_min is not None:
        wanted["rate_limit_per_min"] = _check_int(rate_limit_per_min, "每分鐘限流", RATE_LIMIT_RANGE)
    if daily_quota is not None:
        wanted["daily_quota"] = _check_int(daily_quota, "每日額度", DAILY_QUOTA_RANGE)
    if note is not None:
        wanted["note"] = _check_note(note)
    if not wanted:
        raise _invalid("沒有要變更的欄位")
    async with SessionFactory() as session:
        before = await _require(session, client_id)
        changes = {k: v for k, v in wanted.items() if getattr(before, k) != v}
        if not changes:
            return before
        params = {
            "id": client_id,
            "enabled": changes.get("enabled", before.enabled),
            "scopes": sorted(changes.get("scopes", before.scopes)),
            "rate": changes.get("rate_limit_per_min", before.rate_limit_per_min),
            "quota": changes.get("daily_quota", before.daily_quota),
            "note": changes.get("note", before.note),
        }
        await session.execute(
            text(
                "UPDATE research.api_client SET enabled = :enabled, scopes = CAST(:scopes AS text[]), "
                "rate_limit_per_min = :rate, daily_quota = :quota, note = :note, updated_at = now() WHERE id = :id"
            ),
            params,
        )

        def _plain(v):
            return sorted(v) if isinstance(v, frozenset) else v

        detail = {
            "name": before.name, "key_prefix": before.key_prefix,
            "before": {k: _plain(getattr(before, k)) for k in changes},
            "after": {k: _plain(v) for k, v in changes.items()},
        }
        await record_audit(session, actor_id=actor_id, action="api_client.update", target_type="api_client",
                           target_id=str(client_id), detail=detail)
        client = await _load(session, client_id)
        await session.commit()
    assert client is not None
    return client


async def replace_entitlements(*, actor_id: str | None, client_id: int,
                               entitlements: Mapping[str, Sequence[str]]) -> ApiClient:
    """以完整清單取代授權範圍（不是增量）。"""
    ents = normalize_entitlements(entitlements)
    async with SessionFactory() as session:
        before = await _require(session, client_id)
        await session.execute(
            text("DELETE FROM research.api_client_entitlement WHERE client_id = :id"), {"id": client_id},
        )
        await _insert_entitlements(session, client_id, ents)
        await session.execute(
            text("UPDATE research.api_client SET updated_at = now() WHERE id = :id"), {"id": client_id},
        )
        await record_audit(session, actor_id=actor_id, action="api_client.entitlements", target_type="api_client",
                           target_id=str(client_id), detail={
                               "name": before.name, "key_prefix": before.key_prefix,
                               "before": _ent_detail(before.entitlements), "after": _ent_detail(ents),
                           })
        client = await _load(session, client_id)
        await session.commit()
    assert client is not None
    return client


async def rotate_key(*, actor_id: str | None, client_id: int) -> tuple[ApiClient, str]:
    """換一把新金鑰；舊金鑰下一個請求就失效。回 (client, raw_key)，raw_key 只此一次。"""
    if not _valid_id(client_id):
        raise ApiClientError("not_found", "API 用戶端不存在")
    for _ in range(_PREFIX_RETRIES):
        raw, prefix, key_hash = generate_key()
        try:
            async with SessionFactory() as session:
                before = await _require(session, client_id)
                await session.execute(
                    text(
                        "UPDATE research.api_client SET key_prefix = :prefix, key_hash = :hash, "
                        "key_rotated_at = now(), updated_at = now() WHERE id = :id"
                    ),
                    {"id": client_id, "prefix": prefix, "hash": key_hash},
                )
                await record_audit(session, actor_id=actor_id, action="api_client.rotate", target_type="api_client",
                                   target_id=str(client_id), detail={
                                       "name": before.name, "old_key_prefix": before.key_prefix,
                                       "key_prefix": prefix,
                                   })
                client = await _load(session, client_id)
                await session.commit()
        except IntegrityError as exc:
            if _is_unique_violation(exc, "api_client_key_prefix_key"):
                continue
            raise
        assert client is not None
        return client, raw
    raise RuntimeError("連續產生的金鑰 prefix 都與既有的重複")
