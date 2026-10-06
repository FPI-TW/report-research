"""TOTP（RFC 6238）：30 秒一步、6 位數、HMAC-SHA1，驗證時允許前後各一步的時鐘誤差。

只用標準庫（hmac／hashlib／base64），刻意不新增相依：演算法只有十幾行，RFC 附錄有測試向量
（`tests/test_totp.py` 逐筆對照），而每多一個套件就多一份授權審查與升版負擔。

這裡只做純計算，不碰 DB。「同一個時間步不能用兩次」需要記住最後一次被接受的時間步
（`research.app_user.totp_last_step`），由 `app/services/accounts.py` 在鎖住帳號列的交易裡判斷：
`match_step` 只接受比 `last_step` 新的時間步。

參數為什麼是 SHA1／6 位／30 秒：這是 Google Authenticator、Microsoft Authenticator、1Password 等
驗證器 App 的共同預設，otpauth URI 不必帶額外參數，任何一個 App 掃了都能用。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

PERIOD_SECONDS = 30
DIGITS = 6
# 驗證時接受目前時間步的前後各幾步（手機與伺服器的時鐘誤差、輸入花的時間）。
WINDOW = 1
ISSUER = "廷豐智能研報"
_SECRET_BYTES = 20  # RFC 4226 建議 160 bits（SHA1 的輸出長度）


def generate_secret() -> str:
    """新的 base32 secret（大寫、不帶 '=' 補位，驗證器 App 都接受這種寫法）。"""
    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    s = "".join(secret.split()).upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    """RFC 4226 HOTP：HMAC-SHA1(key, counter) → dynamic truncation → 取最後 digits 位。"""
    mac = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** digits)).zfill(digits)


def _now() -> float:
    """目前時刻（獨立成函式讓測試能凍結／推進時鐘，而不必替換全域的 time.time）。"""
    return time.time()


def current_step(now: float | None = None) -> int:
    return int((_now() if now is None else now) // PERIOD_SECONDS)


def code_at(secret: str, step: int) -> str:
    return hotp(secret, step)


def normalize_code(code: str | None) -> str | None:
    """去掉空白（使用者常照 App 顯示的 `123 456` 打）；不是恰好 6 位數字就回 None。"""
    c = "".join((code or "").split())
    return c if len(c) == DIGITS and c.isascii() and c.isdigit() else None


def match_step(secret: str, code: str | None, *, last_step: int | None, now: float | None = None,
               window: int = WINDOW) -> int | None:
    """驗證碼符合 [目前−window, 目前＋window] 內、且比 last_step 新的某個時間步時回那個時間步，否則 None。

    逐一比對全部候選（不在第一個相符時提早結束），比對用 compare_digest：回應時間不透露是哪一步相符。
    """
    c = normalize_code(code)
    if c is None or not secret:
        return None
    try:
        _key(secret)
    except (ValueError, TypeError):
        return None
    cur = current_step(now)
    found: int | None = None
    for step in range(cur - window, cur + window + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, step), c) and found is None:
            found = step
    return found


def otpauth_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    """驗證器 App 用的 `otpauth://totp/...` URI（可貼上、也可自行轉成 QR code）。"""
    label = quote(f"{issuer}:{account}", safe="")
    query = urlencode({"secret": secret, "issuer": issuer, "algorithm": "SHA1", "digits": DIGITS,
                       "period": PERIOD_SECONDS}, quote_via=quote)
    return f"otpauth://totp/{label}?{query}"
