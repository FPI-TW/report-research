"""對外 API（`/external/*`）的 Bearer 金鑰認證、每分鐘限流與每日額度。

`web/server.py` 的 session middleware 對 `/external/` 前綴完全放行（不查 cookie、不發 cookie、
dev_mode 也不作用），認證**只**由這裡的 dependency 負責；反過來，`/external/` 以外的路徑一律
不認 Bearer。每條對外端點以 `Depends(require_api_client("<scope>"))` 宣告自己需要的 key scope。

檢查順序是刻意的：

1. 解析 `Authorization: Bearer <key>`：缺 → 401 `api_key_missing`；格式錯 → 401 `api_key_invalid`。
2. `deps.api_clients.resolve_key`（每次查 DB、不快取）：None → 401 `api_key_invalid`——停用、輪替掉的
   舊金鑰與不存在同一個回應，不讓呼叫端分辨；DB 例外 → 503。
3. scope 不在 `client.scopes` → 403 `api_scope_missing`。
4. 每分鐘限流（每個用戶端一個記憶體 token bucket，容量＝`rate_limit_per_min`）→ 429 `api_rate_limited`。
   在每日額度之前：被限流擋下的請求不碰 DB、也不吃額度。狀態是 per-process（web 單一 worker，
   見 `web/concurrency.py`），重啟即清空；`tests/conftest.py` 每題前後呼叫 `reset_state()`。
5. 每日額度 `deps.api_clients.consume_quota` → 429 `api_quota_exceeded`。額度以台北時間的日曆日計
   （`api_clients.TZ`），`Retry-After` 算到台北隔日 0 點（UTC 16:00），與實際重置時點一致。

401 一律帶 `WWW-Authenticate: Bearer`；429 一律帶 `Retry-After`（秒）。通過後用戶端放在
`request.state.api_client`。日誌只記 client id 與 `key_prefix`，**絕不記原始金鑰**（也不記 hash）。
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request

from app.services.api_clients import TZ as QUOTA_TZ
from web import deps
from web.errors import AppError

logger = logging.getLogger(__name__)

# 測試以 monkeypatch 換掉這兩個時鐘（限流用單調時鐘；額度重置用牆上時間）。
_monotonic = time.monotonic


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class _Bucket:
    capacity: int
    tokens: float
    updated: float


_BUCKETS: dict[int, _Bucket] = {}


def reset_state() -> None:
    """清空所有用戶端的限流狀態（測試每題前後呼叫）。"""
    _BUCKETS.clear()


def _take_token(client_id: int, per_min: int) -> float | None:
    """取一個 token；成功回 None，不足時回還要等幾秒。

    容量＝每分鐘上限、每秒補 per_min/60 個。管理員改了上限時容量立即跟著改，
    剩餘 token 夾到新容量內（調低不會讓舊的存量繼續放行）。
    """
    now = _monotonic()
    per_min = max(1, int(per_min))
    rate = per_min / 60.0
    bucket = _BUCKETS.get(client_id)
    if bucket is None:
        bucket = _BUCKETS[client_id] = _Bucket(capacity=per_min, tokens=float(per_min), updated=now)
    else:
        elapsed = max(0.0, now - bucket.updated)
        bucket.capacity = per_min
        bucket.tokens = min(float(per_min), bucket.tokens + elapsed * rate)
        bucket.updated = now
    if bucket.tokens >= 1.0:
        bucket.tokens -= 1.0
        return None
    return (1.0 - bucket.tokens) / rate


def seconds_until_quota_reset(now: datetime) -> int:
    """到下一個台北時間 0 點的秒數（無條件進位、至少 1）。`now` 必須帶時區。"""
    local = now.astimezone(QUOTA_TZ)
    next_midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), tzinfo=QUOTA_TZ)
    return max(1, math.ceil((next_midnight - local).total_seconds()))


def _unauthorized(code: str, message: str, *, invalid: bool) -> AppError:
    challenge = 'Bearer error="invalid_token"' if invalid else "Bearer"
    return AppError(401, code, message, headers={"WWW-Authenticate": challenge})


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if header is None or not header.strip():
        raise _unauthorized("api_key_missing", "缺少 API 金鑰（Authorization: Bearer <api_key>）", invalid=False)
    parts = header.split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1]:
        raise _unauthorized("api_key_invalid", "API 金鑰無效", invalid=True)
    return parts[1]


def require_api_client(scope: str):
    """回傳 FastAPI dependency：認證、scope、限流、額度都通過才回 `ApiClient`。"""

    async def dependency(request: Request):
        raw = _bearer_token(request)
        try:
            client = await deps.api_clients.resolve_key(raw)
        except Exception:
            logger.exception("對外 API 認證失敗：用戶端服務無法使用")
            raise AppError(503, "api_auth_unavailable", "認證服務暫時無法使用")
        if client is None:
            logger.warning("對外 API 金鑰無效 path=%s", request.url.path)
            raise _unauthorized("api_key_invalid", "API 金鑰無效", invalid=True)
        if scope not in client.scopes:
            logger.warning(
                "對外 API scope 不足 client_id=%s key_prefix=%s scope=%s", client.id, client.key_prefix, scope,
            )
            raise AppError(403, "api_scope_missing", f"這把 API 金鑰沒有 {scope} 權限")
        wait = _take_token(client.id, client.rate_limit_per_min)
        if wait is not None:
            logger.warning("對外 API 限流 client_id=%s key_prefix=%s", client.id, client.key_prefix)
            raise AppError(
                429, "api_rate_limited", "請求過於頻繁，請稍後再試",
                headers={"Retry-After": str(max(1, math.ceil(wait)))},
            )
        try:
            allowed, count = await deps.api_clients.consume_quota(client.id)
        except Exception:
            logger.exception("對外 API 額度計數失敗 client_id=%s key_prefix=%s", client.id, client.key_prefix)
            raise AppError(503, "api_auth_unavailable", "認證服務暫時無法使用")
        if not allowed:
            logger.warning(
                "對外 API 每日額度用罄 client_id=%s key_prefix=%s count=%s", client.id, client.key_prefix, count,
            )
            raise AppError(
                429, "api_quota_exceeded", "今日 API 額度已用完（台北時間 0 點重置）",
                headers={"Retry-After": str(seconds_until_quota_reset(_utcnow()))},
            )
        request.state.api_client = client
        logger.info(
            "對外 API 認證通過 client_id=%s key_prefix=%s scope=%s path=%s",
            client.id, client.key_prefix, scope, request.url.path,
        )
        return client

    # tests/test_external_auth.py 以此結構性檢查每條 /external/ 路由都掛了金鑰認證與它要的 scope。
    dependency.api_scope = scope
    return dependency
