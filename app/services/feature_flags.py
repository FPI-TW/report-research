"""功能旗標的讀取核心（Admin v2）：程式內 registry × 環境變數上限 × DB 覆寫（`research.feature_flag`）。

**語意（使用者定案 11）：實際值＝環境變數上限 AND DB 政策。**

- 上限（ceiling）是環境變數（經 `app/config.py`）：代表「能力已安裝／允許」。上限關閉時，DB 怎麼設都是關。
- DB 只存覆寫值；沒有那一列＝registry 預設。registry 沒登記的 key 一律忽略（不會因為 DB 多一列就長出新旗標）。
- 覆寫的作用域只有三種：`allow_roles` 與 `allow_users` 都是 NULL＝全站；任一非 NULL＝只對列出的角色或使用者
  生效（`enabled` 仍須為 true）。沒有身分（背景工作、批次）時，有作用域的覆寫一律視為關。
- **DB 讀取失敗退回 registry 預設**（上限照樣 AND）。所以這裡**刻意不放任何安全閘門**：DB 遺失時旗標會退回
  預設，安全邊界不能因此被放寬。管理員 TOTP 強制是環境變數 `ADMIN_MFA_REQUIRED`（`web/authz.py`），
  `DEV_NO_AUTH`、`REPORT_MARK_*`、CSRF、模型與儲存設定、併發閘、上傳的安全上限也都不在這裡（設計 §1.4）。

**快取（定案 12）**：DB 覆寫整張讀進來快取 `FEATURE_FLAG_CACHE_SECONDS`（5）秒；同一行程的寫入（Flags lane 的
`admin_flags`）寫完呼叫 `invalidate()` 立即生效（lifespan 保證單一 worker，所以「同一行程」＝全站）。讀取失敗的
結論也快取同樣久，DB 掛掉時不會每個請求都去撞。這不違反「auth 不快取」：旗標本來就排除所有安全邊界。

給各 lane 的介面（讀取）：

    await feature_flags.is_enabled("qa.agentic", user)        # bool；user 可省略（背景工作）
    await feature_flags.snapshot(user)                        # {key: FlagState}，給 GET /api/features 與管理頁
    feature_flags.invalidate()                                # 寫入後立即失效
    feature_flags.evaluate(spec, ceiling=..., override=..., user=...)   # 純函式（真值表測試）

寫入（含 `ops.operate`＋已提升＋同交易稽核 `flag.update`）由 Flags lane 加在這個模組；registry 的鍵與預設值改了
要同步前端（`/api/features` 的 zod）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import text

from app.config import Settings, get_settings
from app.services.db import SessionFactory

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FlagSpec:
    key: str
    description: str
    default: bool  # DB 沒有覆寫（或讀不到 DB）時的政策值；最後仍與上限 AND
    ceiling_env: str  # 上限來自哪個環境變數（顯示與文件用）
    ceiling: Callable[[Settings], bool]


@dataclass(frozen=True)
class Override:
    """`research.feature_flag` 的一列。作用域 None＝不限。"""

    enabled: bool
    allow_roles: frozenset[str] | None = None
    allow_users: frozenset[str] | None = None


@dataclass(frozen=True)
class FlagState:
    key: str
    effective: bool
    ceiling: bool
    default: bool
    override: Override | None
    source: str  # default（沒有覆寫）／db（套用了覆寫）／fallback（DB 讀取失敗，用 registry 預設）


def _spec(key, description, default, env, ceiling) -> tuple[str, FlagSpec]:
    return key, FlagSpec(key, description, default, env, ceiling)


# 旗標清單（唯一定義）。預設值的原則：fail-open 的派生功能預設 true（不設 DB 時行為與 v1 完全相同，
# 由環境變數決定）；會改變使用者可見行為的營運開關預設 false。
REGISTRY: dict[str, FlagSpec] = dict([
    _spec("ask.web_search", "問答網路搜尋（前後端同一個來源；網搜後端完成前環境變數維持 0）", True,
          "ASK_ENABLE_WEB", lambda s: s.ask_enable_web),
    _spec("uploads.intake", "研報上傳收檔（環境變數＝ClamAV 與 worker 已安裝；旗標＝可以暫停收檔）", True,
          "UPLOAD_ENABLED", lambda s: s.upload_enabled),
    _spec("qa.agentic", "問答 agentic 補查（fail-open 的降級開關）", True,
          "QA_AGENTIC_ENABLED", lambda s: s.qa_agentic_enabled),
    _spec("qa.faithfulness", "問答忠實度抽查（fail-open 的降級開關）", True,
          "ASK_FAITHFULNESS_ENABLED", lambda s: s.ask_faithfulness_enabled),
    _spec("ask.rerank", "問答 rerank（fail-open 的降級開關）", True,
          "ASK_RERANK_ENABLED", lambda s: s.ask_rerank_enabled),
    _spec("trusted_data", "可信市場資料（fail-open 的降級開關）", True,
          "TRUSTED_DATA_ENABLED", lambda s: s.trusted_data_enabled),
    _spec("quota.enforce", "每人每日配額正式阻擋（關＝影子模式：只計數、記錄本來會擋）", False,
          "QUOTA_ENFORCE", lambda s: s.quota_enforce),
])


def evaluate(spec: FlagSpec, *, ceiling: bool, override: Override | None, user=None) -> bool:
    """實際值＝上限 AND 政策。政策：沒有覆寫＝registry 預設；覆寫關＝關；覆寫開且無作用域＝開；有作用域＝看身分。"""
    if not ceiling:
        return False
    if override is None:
        return spec.default
    if not override.enabled:
        return False
    if override.allow_roles is None and override.allow_users is None:
        return True
    if user is None:
        return False
    if override.allow_roles is not None and getattr(user, "role", None) in override.allow_roles:
        return True
    uid = getattr(user, "id", None)
    return override.allow_users is not None and uid is not None and str(uid).lower() in override.allow_users


# ── DB 覆寫的快取 ─────────────────────────────────────────────────────────────

_cache: tuple[float, dict[str, Override], bool] | None = None  # (讀取時刻 monotonic, 覆寫, 是否讀取成功)
_LOAD_SQL = "SELECT key, enabled, allow_roles, allow_users FROM research.feature_flag"


def invalidate() -> None:
    """寫入之後呼叫：下一次讀取一定重新查 DB。tests/conftest.py 也每題前後呼叫。"""
    global _cache
    _cache = None


def _parse_rows(rows) -> dict[str, Override]:
    out: dict[str, Override] = {}
    for key, enabled, roles, users in rows:
        if key not in REGISTRY:
            continue  # registry 沒登記的 key 一律忽略
        out[key] = Override(
            enabled=bool(enabled),
            allow_roles=frozenset(str(r) for r in roles) if roles is not None else None,
            allow_users=frozenset(str(u).lower() for u in users) if users is not None else None,
        )
    return out


async def _load(session_factory) -> tuple[dict[str, Override], bool]:
    try:
        async with session_factory() as session:
            rows = (await session.execute(text(_LOAD_SQL))).all()
        return _parse_rows(rows), True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("功能旗標：讀取 research.feature_flag 失敗，暫用 registry 預設", exc_info=True)
        return {}, False


async def overrides(*, session_factory=None) -> tuple[dict[str, Override], bool]:
    """目前的 DB 覆寫（快取 `FEATURE_FLAG_CACHE_SECONDS` 秒）與是否讀取成功。"""
    global _cache
    ttl = get_settings().feature_flag_cache_seconds
    cached = _cache
    if cached is not None and time.monotonic() - cached[0] < ttl:
        return cached[1], cached[2]
    # 刻意不加鎖：快取過期的那一瞬間最多多查幾次一個極小的表；asyncio.Lock 綁 event loop，
    # 模組級的鎖在多個 loop（測試、腳本）之間反而會出錯。
    data, ok = await _load(session_factory or SessionFactory)
    _cache = (time.monotonic(), data, ok)
    return data, ok


async def snapshot(user=None, *, session_factory=None) -> dict[str, FlagState]:
    """所有登記旗標對這個身分的有效值與來源。"""
    data, ok = await overrides(session_factory=session_factory)
    settings = get_settings()
    out: dict[str, FlagState] = {}
    for key, spec in REGISTRY.items():
        ceiling = bool(spec.ceiling(settings))
        ov = data.get(key) if ok else None
        out[key] = FlagState(
            key=key, effective=evaluate(spec, ceiling=ceiling, override=ov, user=user), ceiling=ceiling,
            default=spec.default, override=ov, source="fallback" if not ok else ("db" if ov is not None else "default"),
        )
    return out


async def is_enabled(key: str, user=None, *, session_factory=None) -> bool:
    """單一旗標的有效值。未登記的 key 拋 KeyError（程式錯誤，不是「關」）。"""
    spec = REGISTRY[key]
    data, ok = await overrides(session_factory=session_factory)
    return evaluate(spec, ceiling=bool(spec.ceiling(get_settings())), override=data.get(key) if ok else None,
                    user=user)
