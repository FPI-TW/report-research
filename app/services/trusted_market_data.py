"""M4a 受信任時效資料契約：time_sensitive 路由唯一合法的外部資料入口。

設計原則（spec: docs/superpowers/specs/2026-07-14-m4a-trusted-data-design.md）：
- Claude 只決定「是否需要時效資料」（M4 路由已完成）；不能自由挑選網頁或繞過 allowlist。
- registry 預設為空 → 執行期行為與 M4 相同（安全婉拒）。真實 provider 依本契約
  於部署時註冊；測試一律 fake provider。
- 任何失敗（無 provider、逾時、欄位缺漏、資料過舊、不在 allowlist、限流）收斂為
  TrustedDataUnavailable → 呼叫端安全婉拒。只有 CancelledError 原樣上拋（取消傳播）。
- 快取與速率限制以注入的 now(datetime) 判斷，測試完全決定性、不需 monkeypatch 時鐘。
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from urllib.parse import urlparse

from app.config import get_settings
from app.services.textnorm import norm_for_match

Category = Literal["quote", "filing", "rate"]

QUOTE: Category = "quote"
FILING: Category = "filing"
RATE: Category = "rate"

_CATEGORIES: tuple[Category, ...] = (QUOTE, FILING, RATE)

TRUSTED_DATA_ENABLED = get_settings().trusted_data_enabled


class TrustedDataUnavailable(Exception):
    """統一失敗出口：呼叫端唯一合法反應是安全婉拒（沿用 M4 文案），不得分支繞過。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class TrustedQuery:
    category: Category
    question: str
    symbol: str | None = None


@dataclass(frozen=True)
class TrustedDataPoint:
    """唯一可進入時效答案與 evidence ledger 的結構；對外網頁自由文字不得混入。"""

    value: str
    unit: str | None
    as_of: datetime          # 資料截至時間，必須 tz-aware
    published_at: datetime | None
    url: str                 # 必須落在 provider allowlist 網域
    source_type: str         # 來源性質：exchange / official / regulator ...
    profile_id: str          # 核准來源 profile；不可由模型自由指定
    snapshot_ref: str        # provider 已保存的不可變原始內容快照參照
    canonical_payload: bytes # 用於重算 content_hash 的 canonical 原始 payload
    content_hash: str        # canonical payload 的 sha256 hex
    provider: str
    category: Category
    subject: str             # 標的/主題描述（供答案顯示）


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    category: Category
    allowed_domains: tuple[str, ...]
    max_age: timedelta       # 最大資料年齡：quote 分鐘級、filing/rate 天級
    cache_ttl: timedelta
    timeout: float           # provider fetch 逾時（秒）
    min_interval: float      # 兩次真實 fetch 的最小間隔（秒；速率限制）
    exchange_tz: str         # IANA 時區（顯示與稽核用）


class TrustedProvider(Protocol):
    async def fetch(self, query: TrustedQuery) -> TrustedDataPoint | None: ...


# 每 category 至多一個 provider（v1 簡化）；預設空＝安全婉拒
_registry: dict[Category, tuple[ProviderSpec, TrustedProvider]] = {}
# (category, norm_for_match(question)) → (point, expires_at)
_cache: dict[tuple[str, str], tuple[TrustedDataPoint, datetime]] = {}
# provider name → 下次允許真實 fetch 的時間
_next_allowed_at: dict[str, datetime] = {}


def register_provider(spec: ProviderSpec, provider: TrustedProvider) -> None:
    if spec.category not in _CATEGORIES:
        raise ValueError(f"unknown category: {spec.category}")
    _registry[spec.category] = (spec, provider)


def clear_providers() -> None:
    """清空 registry 與所有內部狀態（測試隔離用）。"""
    _registry.clear()
    _cache.clear()
    _next_allowed_at.clear()


def available_categories() -> tuple[Category, ...]:
    return tuple(c for c in _CATEGORIES if c in _registry)


_FILING_TERMS = ("公告", "財報", "法說", "年報", "季報", "財務報告")
_RATE_TERMS = ("利率", "央行", "聯準會", "fed", "fomc", "升息", "降息", "基點")


def infer_category(question: str) -> Category:
    """確定性 category 推斷（僅用於挑 provider）。推錯的後果只是找不到
    provider → 安全婉拒，不會產生錯誤資料，故容許寬鬆預設 quote。"""
    q = (question or "").lower()
    if any(t in q for t in _FILING_TERMS):
        return FILING
    if any(t in q for t in _RATE_TERMS):
        return RATE
    return QUOTE


def _domain_allowed(url: str, allowed: tuple[str, ...]) -> bool:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host:
        return False
    return any(host == d or host.endswith("." + d)
               for d in (dd.lower() for dd in allowed))


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def validate_point(
    point: TrustedDataPoint, spec: ProviderSpec, now: datetime
) -> str | None:
    """純函式驗證：通過回 None，否則回拒收原因。欄位缺漏、來源不在 allowlist、
    資料過舊都不得進入答案。"""
    if not point.value:
        return "missing value"
    if not point.source_type:
        return "missing source_type"
    if not point.profile_id:
        return "missing profile_id"
    if not point.snapshot_ref:
        return "missing snapshot_ref"
    if not isinstance(point.canonical_payload, bytes):
        return "canonical_payload must be bytes"
    if not point.content_hash or not _SHA256_RE.match(point.content_hash):
        return "invalid content_hash"
    if hashlib.sha256(point.canonical_payload).hexdigest() != point.content_hash:
        return "content_hash does not match canonical_payload"
    if not _domain_allowed(point.url, spec.allowed_domains):
        return "url not in allowlist"
    if point.as_of is None or point.as_of.tzinfo is None:
        return "as_of must be tz-aware"
    if point.as_of > now:
        return "as_of is in future"
    if now - point.as_of > spec.max_age:
        return "data too old"
    return None


async def fetch_trusted(
    category: Category,
    question: str,
    *,
    symbol: str | None = None,
    now: datetime | None = None,
) -> TrustedDataPoint:
    """取得一筆已驗證的受信任時效資料；失敗一律 TrustedDataUnavailable。

    順序：總開關 → provider → 快取 → 速率限制 → fetch(逾時) → 驗證 → 寫快取。
    CancelledError 原樣上拋且不寫快取（取消傳播）。
    """
    now = now or datetime.now(timezone.utc)
    if not TRUSTED_DATA_ENABLED:
        raise TrustedDataUnavailable("trusted data disabled")
    entry = _registry.get(category)
    if entry is None:
        raise TrustedDataUnavailable(f"no provider for category: {category}")
    spec, provider = entry

    key = (category, norm_for_match(question or ""))
    cached = _cache.get(key)
    if cached is not None and now < cached[1]:
        # 快取命中仍須重驗（尤其資料年齡）：cache_ttl 與 max_age 是兩個獨立旋鈕，
        # TTL 未過但資料已超過最大年齡時不得回過期值——安全語義優先於快取效益。
        if validate_point(cached[0], spec, now) is None:
            return cached[0]
        del _cache[key]

    nxt = _next_allowed_at.get(spec.name)
    if nxt is not None and now < nxt:
        raise TrustedDataUnavailable("rate limited")
    _next_allowed_at[spec.name] = now + timedelta(seconds=spec.min_interval)

    query = TrustedQuery(category=category, question=question, symbol=symbol)
    try:
        point = await asyncio.wait_for(provider.fetch(query), timeout=spec.timeout)
    except TimeoutError:
        raise TrustedDataUnavailable("provider timeout") from None
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001 — provider 任何失敗都收斂為 unavailable
        raise TrustedDataUnavailable(
            f"provider error: {type(e).__name__}"
        ) from e
    if point is None:
        raise TrustedDataUnavailable("provider returned no data")

    reason = validate_point(point, spec, now)
    if reason is not None:
        raise TrustedDataUnavailable(reason)

    _cache[key] = (point, now + spec.cache_ttl)
    return point
