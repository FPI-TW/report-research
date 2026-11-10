"""OIDC 單一登入的協定層：discovery、授權網址（Authorization Code＋PKCE）、換 token、ID token 驗證。

只做「這個人是 IdP 認證過的誰」；帳號（JIT、綁定、停用）與授權在 `app/services/accounts.py`（`sso_login`），
登入流程與 cookie 在 `web/routers/oidc_login.py`。設定是 `app.config.OidcSettings`（`OIDC_*`）。

刻意的設計：

1. **不自己寫 JWT 驗簽**：簽章、`alg` 白名單、`exp`／`iat`／`iss`／`aud`／`nonce` 交給 joserfc（BSD-3-Clause）。
   `alg` 只收非對稱演算法（`ALLOWED_ALGS`）——`none` 與 HS*（拿 client secret 當 HMAC 金鑰）一律拒絕；JWKS 也只
   匯入非對稱、用途是簽章的金鑰。
2. **金鑰輪替**：ID token 的 `kid` 不在快取的 JWKS 裡時重抓一次再驗（同一個行程至少隔 `JWKS_MIN_REFRESH_SECONDS`，
   免得一個亂帶 `kid` 的 token 就能讓我們一直打 IdP）。沒有 `kid` 時 joserfc 以金鑰類型挑。
3. **issuer 逐字比對**：設定的 `OIDC_ISSUER`、discovery 回的 `issuer`、ID token 的 `iss` 三者必須完全相同
   （OIDC Discovery §4.3、Core §3.1.3.7）。多租戶的 `{tenantid}` 樣板 issuer 不支援。
4. **時鐘誤差**：`OIDC_CLOCK_SKEW_SECONDS`（預設 120、上限 600）同時套在 `exp`、`iat`、`nbf` 與權限提升的
   `auth_time` 判斷。
5. **多重驗證的判準**（`ADMIN_MFA_REQUIRED` 對 SSO 管理員的依據，`Identity.idp_mfa`）：`OIDC_ASSUME_IDP_MFA`
   開啟（IdP 端已對這個應用程式強制 MFA）；或 ID token 的 `amr` 含 `OIDC_MFA_AMR` 任一值；或 `acr` 是
   `OIDC_MFA_ACR` 之一。只看 ID token，不看 access token、不打 userinfo。
6. **不連網的測試**：所有 HTTP 經模組層 `_transport`（測試換成 `httpx.MockTransport`）；discovery 與 JWKS 的
   模組級快取由 `tests/conftest.py` 每題前後 `reset_cache()`。
7. **祕密不進 log**：client secret、授權碼、token、code_verifier 都不出現在任何訊息與例外字串裡；IdP 的錯誤
   只留 OAuth 的 `error` 代碼（格式受限的短字串）。
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

import httpx
from joserfc import jwt
from joserfc.errors import ExpiredTokenError, InvalidKeyIdError, JoseError
from joserfc.jwk import KeySet

from app.config import OidcSettings

logger = logging.getLogger(__name__)

ALLOWED_ALGS: tuple[str, ...] = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512")
_ASYMMETRIC_KTY = frozenset({"RSA", "EC", "OKP"})
HTTP_TIMEOUT_SECONDS = 10.0
DISCOVERY_TTL_SECONDS = 3600.0
JWKS_TTL_SECONDS = 3600.0
JWKS_MIN_REFRESH_SECONDS = 10.0
# 權限提升時要求 IdP 重新驗證（prompt=login、max_age=0）；回來的 auth_time 必須在這個秒數（加時鐘誤差）之內。
REAUTH_MAX_AGE_SECONDS = 300
_MAX_RESPONSE_BYTES = 1024 * 1024
# 與 revision 0013 的 CHECK、accounts 的上限一致（OIDC Core §2：sub 不超過 255 個 ASCII 字元）。
MAX_SUBJECT_CHARS = 255
OAUTH_ERROR_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

# 測試注入點（httpx.MockTransport）；正式環境為 None＝真的連 IdP。
_transport: httpx.AsyncBaseTransport | None = None


class OidcError(Exception):
    """登入流程失敗。`reason` 是 auth_event 的 reason（`token`／`claims`／`domain`／`unavailable`）；
    訊息只進日誌，不含任何祕密。"""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class ProviderMetadata:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    token_auth_methods: tuple[str, ...]
    id_token_algs: tuple[str, ...]


@dataclass(frozen=True)
class Identity:
    """驗過的 ID token 裡、本系統要用的部分。"""

    issuer: str
    subject: str
    username: str
    idp_mfa: bool
    auth_time: int | None


@dataclass
class _Cache:
    metadata: ProviderMetadata | None = None
    metadata_at: float = 0.0
    jwks: KeySet | None = None
    jwks_at: float = 0.0
    key: tuple[str, str] | None = None  # (issuer, jwks_uri)：設定改了就不沿用舊快取


_cache = _Cache()


def reset_cache() -> None:
    """清掉 discovery 與 JWKS 快取（tests/conftest.py 每題前後呼叫）。"""
    global _cache
    _cache = _Cache()


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def new_pkce() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge)。verifier 是 86 個 unreserved 字元（RFC 7636 §4.1：43–128）。"""
    verifier = secrets.token_urlsafe(64)
    return verifier, _b64url(hashlib.sha256(verifier.encode("ascii")).digest())


def new_token() -> str:
    """state、nonce 用的隨機值（256 bits）。"""
    return secrets.token_urlsafe(32)


def _loopback(url: str) -> bool:
    return (urlsplit(url).hostname or "") in ("localhost", "127.0.0.1", "::1")


def _check_endpoint(name: str, url: Any, issuer: str) -> str:
    """discovery 給的端點必須是 https（issuer 本身是本機 http 時才放寬，給本機開發用）。"""
    if not isinstance(url, str) or not url:
        raise OidcError("unavailable", f"discovery 缺少 {name}")
    scheme = urlsplit(url).scheme
    if scheme != "https" and not (scheme == "http" and _loopback(issuer) and _loopback(url)):
        raise OidcError("unavailable", f"discovery 的 {name} 不是 https")
    return url


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=_transport, timeout=HTTP_TIMEOUT_SECONDS, follow_redirects=False,
                             headers={"Accept": "application/json"})


async def _get_json(url: str, what: str) -> dict:
    try:
        async with _client() as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        raise OidcError("unavailable", f"{what} 連線失敗：{type(exc).__name__}") from None
    if resp.status_code != 200:
        raise OidcError("unavailable", f"{what} 回 HTTP {resp.status_code}")
    if len(resp.content) > _MAX_RESPONSE_BYTES:
        raise OidcError("unavailable", f"{what} 回應過大")
    try:
        data = resp.json()
    except ValueError:
        raise OidcError("unavailable", f"{what} 不是 JSON") from None
    if not isinstance(data, dict):
        raise OidcError("unavailable", f"{what} 不是 JSON 物件")
    return data


async def metadata(settings: OidcSettings) -> ProviderMetadata:
    """讀 `<issuer>/.well-known/openid-configuration`（快取 `DISCOVERY_TTL_SECONDS`）。"""
    now = time.monotonic()
    cached = _cache.metadata
    if cached is not None and cached.issuer == settings.issuer and now - _cache.metadata_at < DISCOVERY_TTL_SECONDS:
        return cached
    url = settings.issuer.rstrip("/") + "/.well-known/openid-configuration"
    data = await _get_json(url, "discovery")
    if data.get("issuer") != settings.issuer:
        raise OidcError("unavailable", "discovery 的 issuer 與 OIDC_ISSUER 不一致（必須逐字相同）")
    methods = data.get("token_endpoint_auth_methods_supported") or ["client_secret_basic"]
    algs = data.get("id_token_signing_alg_values_supported") or ["RS256"]
    meta = ProviderMetadata(
        issuer=settings.issuer,
        authorization_endpoint=_check_endpoint("authorization_endpoint", data.get("authorization_endpoint"),
                                               settings.issuer),
        token_endpoint=_check_endpoint("token_endpoint", data.get("token_endpoint"), settings.issuer),
        jwks_uri=_check_endpoint("jwks_uri", data.get("jwks_uri"), settings.issuer),
        token_auth_methods=tuple(m for m in methods if isinstance(m, str)),
        id_token_algs=tuple(a for a in algs if isinstance(a, str) and a in ALLOWED_ALGS),
    )
    if not meta.id_token_algs:
        raise OidcError("unavailable", "IdP 宣告的 ID token 簽章演算法沒有一個在允許清單裡")
    _cache.metadata, _cache.metadata_at = meta, now
    return meta


async def _keyset(meta: ProviderMetadata, *, refresh: bool = False) -> KeySet:
    now = time.monotonic()
    key = (meta.issuer, meta.jwks_uri)
    fresh = _cache.jwks is not None and _cache.key == key and now - _cache.jwks_at < JWKS_TTL_SECONDS
    if fresh and not refresh:
        return _cache.jwks  # type: ignore[return-value]
    if refresh and _cache.jwks is not None and _cache.key == key and now - _cache.jwks_at < JWKS_MIN_REFRESH_SECONDS:
        return _cache.jwks
    data = await _get_json(meta.jwks_uri, "JWKS")
    keys = [k for k in data.get("keys") or () if isinstance(k, dict) and k.get("kty") in _ASYMMETRIC_KTY
            and k.get("use", "sig") == "sig"]
    if not keys:
        raise OidcError("unavailable", "JWKS 沒有可用的簽章金鑰")
    try:
        keyset = KeySet.import_key_set({"keys": keys})
    except (JoseError, ValueError, TypeError) as exc:
        raise OidcError("unavailable", f"JWKS 無法匯入：{type(exc).__name__}") from None
    _cache.jwks, _cache.jwks_at, _cache.key = keyset, now, key
    return keyset


async def authorization_url(settings: OidcSettings, *, state: str, nonce: str, code_challenge: str,
                            reauth: bool = False) -> str:
    """導去 IdP 的網址。`reauth`（權限提升）加 `prompt=login`、`max_age=0`，要求 IdP 真的重新驗證。"""
    meta = await metadata(settings)
    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "scope": " ".join(settings.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if reauth:
        params.update(prompt="login", max_age="0")
    sep = "&" if urlsplit(meta.authorization_endpoint).query else "?"
    return meta.authorization_endpoint + sep + urlencode(params)


def _client_auth(settings: OidcSettings, meta: ProviderMetadata, form: dict) -> dict:
    """依 IdP 宣告的方式帶 client 認證（RFC 6749 §2.3.1）；沒有 secret＝public client，只帶 client_id。"""
    headers: dict[str, str] = {}
    if not settings.client_secret:
        form["client_id"] = settings.client_id
    elif "client_secret_basic" in meta.token_auth_methods or "client_secret_post" not in meta.token_auth_methods:
        cred = f"{quote(settings.client_id, safe='')}:{quote(settings.client_secret, safe='')}"
        headers["Authorization"] = "Basic " + base64.b64encode(cred.encode()).decode("ascii")
    else:
        form["client_id"] = settings.client_id
        form["client_secret"] = settings.client_secret
    return headers


async def exchange_code(settings: OidcSettings, code: str, code_verifier: str) -> str:
    """以授權碼＋code_verifier 換 token，回 ID token（access token 用不到、不保留）。"""
    meta = await metadata(settings)
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": settings.redirect_uri,
            "code_verifier": code_verifier}
    headers = _client_auth(settings, meta, form)
    try:
        async with _client() as client:
            resp = await client.post(meta.token_endpoint, data=form, headers=headers)
    except httpx.HTTPError as exc:
        raise OidcError("unavailable", f"token 端點連線失敗：{type(exc).__name__}") from None
    try:
        data = resp.json() if len(resp.content) <= _MAX_RESPONSE_BYTES else None
    except ValueError:
        data = None
    if resp.status_code != 200:
        err = data.get("error") if isinstance(data, dict) else None
        code_s = err if isinstance(err, str) and OAUTH_ERROR_RE.fullmatch(err) else "-"
        raise OidcError("token", f"token 端點回 HTTP {resp.status_code} error={code_s}")
    id_token = data.get("id_token") if isinstance(data, dict) else None
    if not isinstance(id_token, str) or not id_token:
        raise OidcError("token", "token 回應沒有 id_token")
    return id_token


async def verify_id_token(settings: OidcSettings, id_token: str, *, nonce: str) -> dict:
    """驗簽（JWKS，未知 kid 重抓一次）與 iss／aud／azp／exp／iat／nbf／nonce；回 claims。"""
    meta = await metadata(settings)
    keyset = await _keyset(meta)
    try:
        try:
            token = jwt.decode(id_token, keyset, algorithms=list(meta.id_token_algs))
        except InvalidKeyIdError:
            token = jwt.decode(id_token, await _keyset(meta, refresh=True), algorithms=list(meta.id_token_algs))
        claims = dict(token.claims)
        registry = jwt.JWTClaimsRegistry(
            leeway=settings.clock_skew_seconds,
            iss={"essential": True, "value": settings.issuer},
            aud={"essential": True, "value": settings.client_id},
            sub={"essential": True},
            exp={"essential": True},
            iat={"essential": True},
            nonce={"essential": True, "value": nonce},
        )
        registry.validate(claims)
    except ExpiredTokenError:
        raise OidcError("token", "ID token 已過期") from None
    except JoseError as exc:
        raise OidcError("token", f"ID token 驗證失敗：{type(exc).__name__}") from None
    except (ValueError, TypeError) as exc:
        raise OidcError("token", f"ID token 格式錯誤：{type(exc).__name__}") from None
    aud = claims.get("aud")
    multi = isinstance(aud, list) and len(aud) > 1
    azp = claims.get("azp")
    if (multi or azp is not None) and azp != settings.client_id:
        raise OidcError("token", "ID token 的 azp 不是本系統")
    return claims


def is_mfa(settings: OidcSettings, claims: dict) -> bool:
    """ID token 是否表明做過多重驗證（模組 docstring 第 5 點）。"""
    if settings.assume_idp_mfa:
        return True
    amr = claims.get("amr")
    if isinstance(amr, list) and any(isinstance(v, str) and v in settings.mfa_amr for v in amr):
        return True
    acr = claims.get("acr")
    return isinstance(acr, str) and acr in settings.mfa_acr


def identity(settings: OidcSettings, claims: dict) -> Identity:
    """從驗過的 claims 取出 subject、帳號名稱（`OIDC_USERNAME_CLAIM`）並檢查網域（`OIDC_ALLOWED_DOMAINS`）。

    網域看帳號名稱 `@` 之後的部分（不分大小寫）；有設允許網域而名稱沒有 `@` 一律拒絕。
    """
    sub = claims.get("sub")
    name = claims.get(settings.username_claim)
    if not isinstance(sub, str) or not sub or not isinstance(name, str) or not name.strip():
        raise OidcError("claims", f"ID token 缺少 sub 或 {settings.username_claim}")
    if len(sub) > MAX_SUBJECT_CHARS:
        raise OidcError("claims", "ID token 的 sub 超過長度上限")
    name = name.strip()
    if settings.allowed_domains:
        domain = name.rpartition("@")[2].lower() if "@" in name else ""
        if domain not in settings.allowed_domains:
            raise OidcError("domain", "帳號不在允許的網域")
    auth_time = claims.get("auth_time")
    return Identity(issuer=settings.issuer, subject=sub, username=name, idp_mfa=is_mfa(settings, claims),
                    auth_time=auth_time if isinstance(auth_time, int) and not isinstance(auth_time, bool) else None)


def reauth_is_fresh(settings: OidcSettings, ident: Identity, *, now: float | None = None) -> bool:
    """權限提升：IdP 是否真的剛重新驗證過（`auth_time` 在 `REAUTH_MAX_AGE_SECONDS`＋時鐘誤差之內）。"""
    if ident.auth_time is None:
        return False
    current = time.time() if now is None else now
    return current - ident.auth_time <= REAUTH_MAX_AGE_SECONDS + settings.clock_skew_seconds
