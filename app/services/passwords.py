"""密碼雜湊（Argon2id）：個別帳號的 `research.app_user.password_hash`。

只有三個動作——雜湊、驗證、要不要重算——刻意不碰 DB，讓帳號服務（`accounts.py`）與
建帳 CLI（`scripts/create_admin.py`）共用同一套參數，測試也不必連庫。

**參數**：用 argon2-cffi 的預設（RFC 9106 低記憶體建議：Argon2id、t=3、m=64 MiB、p=4）。
不自己調：調低是拿安全換登入速度，而本站一天登入次數以「人」計，不是瓶頸；調高則
每次登入都要多吃記憶體。預設值隨套件升版而演進時，`needs_rehash` 讓既有雜湊在下次
成功登入時就地升級，不必全員重設。

**帳號不存在時也要跑一次驗證**（`burn_verify`）：否則「帳號存在＋密碼錯」要算一次
Argon2（數十毫秒）、「帳號不存在」立刻回，回應時間就把帳號清單洩漏出去了。
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_HASHER = PasswordHasher()

# 密碼長度政策。下限：沒有複雜度規則（那只會逼出 `Password1!`），只要求夠長；上限：
# Argon2 本身吃得下任意長度，擋的是有人送幾 MB 的字串讓每次驗證都很貴。
MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 256

_DUMMY_HASH: str | None = None


def password_problem(password: str) -> str | None:
    """不符政策時回人看得懂的原因；符合回 None。建帳、重設密碼共用這一道。"""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"密碼至少 {MIN_PASSWORD_LENGTH} 個字元"
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"密碼最多 {MAX_PASSWORD_LENGTH} 個字元"
    if password.strip() != password:
        # 前後空白幾乎都是複製貼上帶進來的，登入時使用者不會記得要打。
        return "密碼前後不可有空白"
    return None


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """比對成功回 True；密碼錯、雜湊字串毀損都回 False（不拋例外）。"""
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """雜湊參數比目前的預設舊時回 True（登入成功後就地升級）。"""
    try:
        return _HASHER.check_needs_rehash(password_hash)
    except InvalidHashError:
        return False


def burn_verify(password: str) -> None:
    """對一個固定的假雜湊驗一次，讓「帳號不存在」與「密碼錯」花一樣久。"""
    global _DUMMY_HASH
    dummy = _DUMMY_HASH
    if dummy is None:
        dummy = _DUMMY_HASH = _HASHER.hash("not-a-real-password-for-timing-only")
    verify_password(dummy, password)
