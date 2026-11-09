"""部署 profile：一個機構（租戶）的非祕密產品設定——身分、品牌、prompt 參數、市場子集。

同一個 codebase 服務不同機構，差異只放在 `profiles/<name>/`：

- `profile.toml`：本模組讀取與驗證的設定檔（`tomllib`）。
- `assets/`：logo、favicon 等靜態檔，只能由 `profile.toml` 以檔名引用（`GET /api/branding/assets/{name}`
  也只交出被引用的檔，見 `web/routers/branding.py`）。

祕密與部署層設定（金鑰、DB URL、LLM 端點）**不放這裡**，照舊走環境變數。

## 載入

`get_profile()` 回傳 frozen dataclass `Profile`，第一次呼叫時讀 `PROFILE_DIR`（預設 `profiles/tingfong`，相對路徑
以 repo 根為基準）並快取；`reset_profile_cache()` 清掉快取（`tests/conftest.py` 每題前後各清一次）。web 在 lifespan
的第一步就呼叫它：設定錯了拒絕啟動，不等到第一個問答才炸。

`PROFILE_DIR` 刻意在這裡以 `os.getenv` 讀、不進 `app/config.py` 的 `Settings`，理由同 `llm_models.py` 的葉模組設計：
讀 profile 的有 `totp.py` 這種純計算模組、批次與評測入口（`eval/run_ragas.py` 經 `answer` 取系統提示），而
`get_settings()` 會連帶做 R2 設定的 fail-closed 驗證、並在 import 期快取。本模組只 import 標準函式庫與
`app.services.tagging`（標準市場代碼；它本身也只 import 標準函式庫），任何模組都能安全依賴它。

## 嚴格驗證（fail-closed）

未知 section、未知鍵、型別錯、引用不存在的 asset、市場子集含非標準代碼、`PROFILE_DIR` 不存在——一律拋
`ProfileError`，訊息說出檔案、鍵與原因。不做「認不得就忽略」：拼錯的鍵靜默退回預設，正是換了品牌卻沒生效、
而且沒有任何錯誤訊息的那種失事。

## 加新 section（後續 lane 的掛點）

1. 寫一個 frozen dataclass 與一個 parser：`def _parse_x(table: dict, ctx: _Ctx) -> X`，table 是該 section 的內容
   （section 缺席時是空 dict，parser 自己給預設），以 `ctx.reject_unknown`／`ctx.string` 等輔助驗證。
2. 用 `@_section("x")` 登記，並在 `Profile` **末尾**加一個有預設值的欄位（名稱同 section）。
3. parser 依登記順序執行，可以讀 `ctx.parsed` 裡已解析的 section（例如 `[prompt]` 的預設取自 `[institution]`）。

`tests/test_profile.py` 對帳登記表與 `Profile` 的欄位一一對應。

## 市場

市場代碼是框架固定的**標準市場代碼**（`app/services/tagging.py` 的 `MARKETS`，標註 prompt 與 `tests/test_tagging.py`
逐字釘住）。profile 只能選子集（`[markets].enabled`，順序＝使用者端選單的顯示順序）與改顯示名稱，不能新增代碼。
廷豐 profile 的代碼與 findb 對齊，是廷豐的跨 repo 契約，不是框架的。
"""

from __future__ import annotations

import hashlib
import os
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.services.tagging import MARKET_DISPLAY, MARKETS

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR_ENV = "PROFILE_DIR"
DEFAULT_PROFILE_DIR = "profiles/tingfong"
PROFILE_FILE = "profile.toml"
ASSETS_DIR = "assets"


class ProfileError(RuntimeError):
    """profile 設定錯誤。訊息一律帶檔案與鍵，讓維運不必讀原始碼就知道要改哪一行。"""


# ── 各 section 的資料形狀 ───────────────────────────────────────────────


@dataclass(frozen=True)
class Institution:
    name: str  # 機構名（例如免責聲明的「非○○之立場」）
    product_name: str  # 產品全名（頁面標題、側欄、登入頁）
    short_name: str  # 簡稱


@dataclass(frozen=True)
class Asset:
    name: str  # assets/ 底下的檔名（不含路徑）
    path: Path
    content_type: str
    sha256: str  # 內容雜湊：URL 版本鍵與 ETag


@dataclass(frozen=True)
class Branding:
    logo_light: Asset | None = None
    logo_dark: Asset | None = None
    favicon: Asset | None = None
    # 強調色覆寫（鍵見 ACCENT_TOKENS）；空＝沿用 frontend/src/styles/tokens.css 的預設。
    colors_light: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))
    colors_dark: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))

    def assets(self) -> dict[str, Asset]:
        """被 profile 引用的 asset（檔名 → Asset）；asset 端點只交出這幾個。"""
        return {a.name: a for a in (self.logo_light, self.logo_dark, self.favicon) if a is not None}


@dataclass(frozen=True)
class PromptParams:
    # prompt 不開放覆寫，只注入這幾個參數。system_name 是 prompt 裡「你是『○○』…」對本系統的稱謂，
    # system_name_en 是英文文案裡的同一個稱謂（廷豐 profile 刻意沿用中文名，與改動前逐字相同）。
    system_name: str
    system_name_en: str


@dataclass(frozen=True)
class Markets:
    enabled: tuple[str, ...]  # 使用者端啟用的標準市場代碼，順序＝顯示順序
    display_names: Mapping[str, str]  # 全部標準代碼 → 顯示名稱（未覆寫者取 tagging.MARKET_DISPLAY）

    def is_enabled(self, code: str | None) -> bool:
        return code in self.enabled

    def label(self, code: str) -> str:
        return self.display_names.get(code, code)


@dataclass(frozen=True)
class Security:
    totp_issuer: str  # 驗證器 App 顯示的發行者名稱（otpauth URI 的 issuer）


@dataclass(frozen=True)
class Profile:
    root: Path
    institution: Institution
    branding: Branding
    prompt: PromptParams
    markets: Markets
    security: Security
    # 後續 lane 的新 section 欄位一律加在這裡之後、帶預設值（例如 `x: X = field(default_factory=X)`）。


# ── 驗證輔助 ─────────────────────────────────────────────────────────────


@dataclass
class _Ctx:
    file: Path
    root: Path
    section: str = ""
    parsed: dict[str, Any] = field(default_factory=dict)

    def error(self, key: str, reason: str) -> ProfileError:
        where = f"[{self.section}].{key}" if self.section and key else (f"[{self.section}]" if self.section else key)
        return ProfileError(f"profile 設定錯誤：{self.file}：{where}：{reason}")

    def reject_unknown(self, table: Mapping[str, Any], allowed: set[str], *, prefix: str = "") -> None:
        unknown = sorted(k for k in table if k not in allowed)
        if unknown:
            raise self.error(prefix + unknown[0], f"未知的鍵（可用：{'、'.join(sorted(allowed)) or '無'}）")

    def table(self, table: Mapping[str, Any], key: str, *, key_label: str | None = None) -> dict[str, Any]:
        value = table.get(key, {})
        if not isinstance(value, dict):
            raise self.error(key_label or key, f"必須是表格（table），目前是 {_type_name(value)}")
        return value

    def string(self, table: Mapping[str, Any], key: str, *, default: str | None = None, max_len: int = 80,
               key_label: str | None = None) -> str:
        label = key_label or key
        if key not in table:
            if default is None:
                raise self.error(label, "必填")
            return default
        value = table[key]
        if not isinstance(value, str):
            raise self.error(label, f"必須是字串，目前是 {_type_name(value)}")
        cleaned = value.strip()
        if not cleaned:
            raise self.error(label, "不可為空字串")
        if len(cleaned) > max_len:
            raise self.error(label, f"最多 {max_len} 個字元")
        if any(ord(c) < 0x20 or ord(c) == 0x7F for c in cleaned):
            raise self.error(label, "不可含控制字元")
        return cleaned

    def string_list(self, table: Mapping[str, Any], key: str) -> list[str] | None:
        if key not in table:
            return None
        value = table[key]
        if not isinstance(value, list):
            raise self.error(key, f"必須是字串陣列，目前是 {_type_name(value)}")
        out: list[str] = []
        for i, v in enumerate(value):
            if not isinstance(v, str):
                raise self.error(f"{key}[{i}]", f"必須是字串，目前是 {_type_name(v)}")
            out.append(v)
        return out

    def asset(self, table: Mapping[str, Any], key: str) -> Asset | None:
        if key not in table:
            return None
        name = self.string(table, key, max_len=100)
        if not _ASSET_NAME.fullmatch(name):
            raise self.error(
                key, f"asset 必須是 {ASSETS_DIR}/ 底下的檔名（英數、點、底線、連字號；不含路徑）：{name!r}")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        content_type = ASSET_CONTENT_TYPES.get(ext)
        if content_type is None:
            allowed = "、".join(sorted(ASSET_CONTENT_TYPES))
            raise self.error(key, f"不支援的 asset 類型 {name!r}（可用副檔名：{allowed}）")
        assets_dir = (self.root / ASSETS_DIR).resolve()
        path = (assets_dir / name).resolve()
        if not path.is_relative_to(assets_dir) or not path.is_file():
            raise self.error(key, f"引用的 asset 不存在：{self.root / ASSETS_DIR / name}")
        data = path.read_bytes()
        if not data:
            raise self.error(key, f"引用的 asset 是空檔：{path}")
        return Asset(name=name, path=path, content_type=content_type, sha256=hashlib.sha256(data).hexdigest())


def _type_name(value: Any) -> str:
    return {str: "字串", int: "整數", float: "浮點數", bool: "布林", list: "陣列", dict: "表格"}.get(
        type(value), type(value).__name__
    )


_ASSET_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
ASSET_CONTENT_TYPES = {
    "png": "image/png",
    "svg": "image/svg+xml",
    "ico": "image/x-icon",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

# 強調色覆寫的鍵（profile.toml 的寫法）。對應的 CSS 變數由前端 `frontend/src/lib/branding.ts` 的
# ACCENT_CSS_VARS 決定（tests/test_profile.py 對帳兩邊的鍵）。值只收 hex 或 rgb()／rgba()，
# 因為它會被原樣寫進 <style>：任何其他寫法都可能是 CSS 注入。
ACCENT_TOKENS = frozenset({
    "accent_text", "accent_strong", "accent_hover", "accent_tint", "accent_line", "accent_panel",
    "on_accent", "accent_button",
})
_COLOR = re.compile(
    r"#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?"
    r"|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d{1,3})\s*)?\)"
)


# ── section 登記表 ───────────────────────────────────────────────────────

_Parser = Callable[[dict, _Ctx], Any]
_SECTIONS: dict[str, _Parser] = {}
_REQUIRED_SECTIONS = {"institution"}


def _section(name: str) -> Callable[[_Parser], _Parser]:
    def register(fn: _Parser) -> _Parser:
        if name in _SECTIONS:
            raise RuntimeError(f"profile section [{name}] 重複登記")
        _SECTIONS[name] = fn
        return fn
    return register


@_section("institution")
def _parse_institution(table: dict, ctx: _Ctx) -> Institution:
    ctx.reject_unknown(table, {"name", "product_name", "short_name"})
    name = ctx.string(table, "name", max_len=40)
    product_name = ctx.string(table, "product_name", max_len=60)
    return Institution(name=name, product_name=product_name,
                       short_name=ctx.string(table, "short_name", default=product_name, max_len=40))


@_section("branding")
def _parse_branding(table: dict, ctx: _Ctx) -> Branding:
    ctx.reject_unknown(table, {"logo_light", "logo_dark", "favicon", "colors"})
    logo_light = ctx.asset(table, "logo_light")
    logo_dark = ctx.asset(table, "logo_dark") or logo_light
    colors = ctx.table(table, "colors")
    ctx.reject_unknown(colors, {"light", "dark"}, prefix="colors.")
    parsed: dict[str, Mapping[str, str]] = {}
    for scheme in ("light", "dark"):
        values = ctx.table(colors, scheme, key_label=f"colors.{scheme}")
        ctx.reject_unknown(values, set(ACCENT_TOKENS), prefix=f"colors.{scheme}.")
        out: dict[str, str] = {}
        for key in sorted(values):
            value = ctx.string(values, key, max_len=40, key_label=f"colors.{scheme}.{key}")
            if not _COLOR.fullmatch(value):
                raise ctx.error(f"colors.{scheme}.{key}", f"色值只接受 #rrggbb、#rrggbbaa 或 rgb()/rgba()：{value!r}")
            out[key] = value
        parsed[scheme] = MappingProxyType(out)
    return Branding(logo_light=logo_light, logo_dark=logo_dark, favicon=ctx.asset(table, "favicon"),
                    colors_light=parsed["light"], colors_dark=parsed["dark"])


@_section("prompt")
def _parse_prompt(table: dict, ctx: _Ctx) -> PromptParams:
    ctx.reject_unknown(table, {"system_name", "system_name_en"})
    institution: Institution = ctx.parsed["institution"]
    system_name = ctx.string(table, "system_name", default=institution.short_name, max_len=40)
    return PromptParams(system_name=system_name,
                        system_name_en=ctx.string(table, "system_name_en", default=system_name, max_len=60))


@_section("markets")
def _parse_markets(table: dict, ctx: _Ctx) -> Markets:
    ctx.reject_unknown(table, {"enabled", "display_names"})
    enabled = ctx.string_list(table, "enabled")
    if enabled is None:
        enabled = list(MARKETS)
    if not enabled:
        raise ctx.error("enabled", "至少要啟用一個市場")
    standard = set(MARKETS)
    seen: set[str] = set()
    for i, code in enumerate(enabled):
        if code not in standard:
            raise ctx.error(f"enabled[{i}]", f"不是標準市場代碼：{code!r}（可用：{' '.join(MARKETS)}）")
        if code in seen:
            raise ctx.error(f"enabled[{i}]", f"重複的市場代碼：{code!r}")
        seen.add(code)
    overrides = ctx.table(table, "display_names")
    names = dict(MARKET_DISPLAY)
    for code in sorted(overrides):
        if code not in standard:
            raise ctx.error(f"display_names.{code}", f"不是標準市場代碼（可用：{' '.join(MARKETS)}）")
        names[code] = ctx.string(overrides, code, max_len=20, key_label=f"display_names.{code}")
    return Markets(enabled=tuple(enabled), display_names=MappingProxyType(names))


@_section("security")
def _parse_security(table: dict, ctx: _Ctx) -> Security:
    ctx.reject_unknown(table, {"totp_issuer"})
    institution: Institution = ctx.parsed["institution"]
    issuer = ctx.string(table, "totp_issuer", default=institution.product_name, max_len=60)
    # otpauth URI 的 label 以冒號分隔 issuer 與帳號（Key Uri Format）：issuer 帶冒號會讓驗證器 App 拆錯。
    if ":" in issuer:
        raise ctx.error("totp_issuer", "不可含冒號（otpauth label 以冒號分隔發行者與帳號）")
    return Security(totp_issuer=issuer)


# ── 載入與快取 ───────────────────────────────────────────────────────────


def resolve_profile_dir(raw: str | None = None) -> Path:
    """`PROFILE_DIR` 的實際路徑：空值＝預設，相對路徑以 repo 根為基準（與工作目錄無關）。"""
    value = (os.getenv(PROFILE_DIR_ENV, "") if raw is None else raw).strip() or DEFAULT_PROFILE_DIR
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def load_profile(profile_dir: Path | str) -> Profile:
    """讀取並驗證一個 profile 目錄；任何問題都拋 ProfileError（不快取，測試與工具直接用）。"""
    root = Path(profile_dir)
    if not root.is_dir():
        raise ProfileError(f"profile 設定錯誤：{PROFILE_DIR_ENV} 指向的目錄不存在：{root}")
    root = root.resolve()
    file = root / PROFILE_FILE
    if not file.is_file():
        raise ProfileError(f"profile 設定錯誤：缺少 {file}")
    try:
        with file.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError(f"profile 設定錯誤：{file}：TOML 語法錯誤：{exc}") from exc
    ctx = _Ctx(file=file, root=root)
    for key, value in data.items():
        if key not in _SECTIONS:
            raise ctx.error(key, f"未知的 section（可用：{'、'.join(_SECTIONS)}）")
        if not isinstance(value, dict):
            raise ctx.error(key, f"必須是 section（[{key}]），目前是 {_type_name(value)}")
    for name in sorted(_REQUIRED_SECTIONS - set(data)):
        raise ctx.error(name, f"缺少必填的 section [{name}]")
    for name, parse in _SECTIONS.items():
        ctx.section = name
        ctx.parsed[name] = parse(data.get(name, {}), ctx)
    return Profile(root=root, **ctx.parsed)


_PROFILE: Profile | None = None


def get_profile() -> Profile:
    """目前行程的 profile（第一次呼叫時依 `PROFILE_DIR` 載入並快取）。"""
    global _PROFILE
    if _PROFILE is None:
        _PROFILE = load_profile(resolve_profile_dir())
    return _PROFILE


def reset_profile_cache() -> None:
    """清掉快取；下一次 get_profile() 重新讀 `PROFILE_DIR`（測試用）。"""
    global _PROFILE
    _PROFILE = None


def section_fields() -> tuple[str, ...]:
    """`Profile` 上對應 section 的欄位名（扣掉 root），供對帳測試用。"""
    return tuple(f.name for f in fields(Profile) if f.name != "root")


def registered_sections() -> tuple[str, ...]:
    return tuple(_SECTIONS)
