# web/routers/oidc_login.py
"""OIDC 單一登入的路由：GET /login/oidc（導去 IdP）、GET /login/oidc/callback（IdP 導回）。

協定（discovery、PKCE、換 token、ID token 驗證）在 `app/services/oidc.py`；帳號（JIT、綁定、停用、只允許 SSO）
在 `app/services/accounts.py`（經 `deps.accounts`，測試換成假帳號庫）。這裡只接起流程與 cookie：

- `OIDC_ISSUER` 沒設時兩條路由都回 404，登入頁也不顯示「以公司帳號登入」——行為與沒有這個功能時一樣。
- 兩條都在 `web/server.py` 的認證白名單（還沒登入才需要它們）。都是 GET：IdP 導回是頂層導覽，`web/csrf.py`
  只檢查會改狀態的方法；跨站偽造的防線是 state（綁在只有發起者瀏覽器才有的 `tf_oidc` cookie 上）與 nonce。
- 與 POST /login 同一套 HTTPS 檢查（`auth.login_allowed`）。不計入每 IP 失敗限流：SSO 沒有可以猜的密碼。
- 成功時發的 session 與本地登入完全相同（`user_session`、`tf_session` cookie、每個請求查 DB），只多記
  `auth_method='sso'` 與 IdP 是否做了多重驗證（`idp_mfa`；`ADMIN_MFA_REQUIRED` 對 SSO 管理員看它，見 `web/authz.py`）。
- **權限提升**（`?intent=elevate`）：SSO 帳號沒有本地密碼可以重新驗證，改成回 IdP 重新登入（`prompt=login`、
  `max_age=0`），回來的 `auth_time` 夠新、而且是同一個 IdP 身分、同一個瀏覽器 session，才延長提升視窗
  （`accounts.elevate_sso_session`）。
- 事件（`research.auth_event`，fail-open）：成功 `login.success`（reason `sso`）、失敗 `login.sso_failure`
  （reason 見 `accounts.AUTH_EVENT_TYPES` 的註解）、權限提升 `elevate.success`／`elevate.failure`。沒有有效暫時憑證
  與 IdP 回 error 的請求只在記憶體計數（`security_ops.SSO_*_TALLY`），被灌時不放大成 DB 寫入。
  **不記任何 token、授權碼或 IdP 給的帳號名稱**；JIT 建帳與綁定的稽核在 `accounts.sso_login`。
"""

from __future__ import annotations

import hmac
import logging
import time
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config import get_settings
from app.services import oidc, security_ops
from web import auth, deps
from web.routers import auth_pages

logger = logging.getLogger(__name__)

router = APIRouter()

# sso_login 的拒絕原因 → 登入頁的錯誤代碼（login.html 的 ERRORS）。停用沿用本地登入的訊息。
_ACCOUNT_ERRORS = {"disabled": "disabled"}


def _settings():
    oidc_settings = get_settings().oidc
    if not oidc_settings.enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    return oidc_settings


def _next_query(nxt: str) -> str:
    return "&next=" + quote(nxt, safe="") if nxt != "/" else ""


def _login_error(code: str, nxt: str) -> RedirectResponse:
    resp = RedirectResponse(f"/login?error={code}{_next_query(nxt)}", status_code=303)
    auth.clear_oidc_cookie(resp)
    return resp


async def _event(event: str, request: Request, **fields) -> None:
    await security_ops.record_event(deps.accounts, event, ip=auth.client_ip(request),
                                    user_agent=request.headers.get("user-agent"), **fields)


async def _session_user(request: Request, now: int):
    """目前瀏覽器的 session 與使用者（權限提升用）；沒有或 DB 不認回 (None, None)。DB 錯誤往上拋。"""
    session = auth.parse_token(request.cookies.get(auth.COOKIE_NAME), now)
    if session is None:
        return None, None
    return session, await deps.accounts.resolve_session(session.session_id)


@router.get("/login/oidc")
async def oidc_start(request: Request):
    """產生 state、nonce、PKCE，簽進 `tf_oidc` cookie，導去 IdP 的授權端點。"""
    settings = _settings()
    now = int(time.time())
    ip = auth.client_ip(request)
    nxt = auth_pages._safe_next(request.query_params.get("next"))
    intent = "elevate" if request.query_params.get("intent") == "elevate" else "login"
    if not auth.login_allowed(request):
        await security_ops.note_throttled(deps.accounts, security_ops.INSECURE_TALLY, ip)
        logger.warning("SSO 登入遭拒：非 HTTPS 且非本機 peer=%s", auth.peer_ip(request))
        return _login_error("insecure", nxt)
    session_id = None
    if intent == "elevate":
        try:
            session, user = await _session_user(request, now)
        except Exception:
            logger.exception("SSO 權限提升：session 查驗失敗")
            return RedirectResponse(nxt, status_code=303)
        if session is None or user is None:
            return RedirectResponse(f"/login?next={quote(nxt, safe='')}" if nxt != "/" else "/login", status_code=303)
        if user.auth_method != "sso":
            # 本地密碼登入的 session 用 POST /api/me/elevate；這裡沒有東西可以做。
            return RedirectResponse(nxt, status_code=303)
        session_id = session.session_id
    state, nonce = oidc.new_token(), oidc.new_token()
    verifier, challenge = oidc.new_pkce()
    try:
        url = await oidc.authorization_url(settings, state=state, nonce=nonce, code_challenge=challenge,
                                           reauth=intent == "elevate")
    except oidc.OidcError as exc:
        logger.error("SSO 無法開始：%s", exc)
        return _login_error("sso_unavailable", nxt)
    pending = auth.OidcPending(state=state, nonce=nonce, code_verifier=verifier, next=nxt, intent=intent,
                               session_id=session_id, expires_at=now + auth.OIDC_TTL)
    resp = RedirectResponse(url, status_code=302)
    auth.set_oidc_cookie(resp, now, pending, secure=auth.request_is_secure(request))
    return resp


@router.get("/login/oidc/callback")
async def oidc_callback(request: Request):
    """IdP 導回：驗 state → 換 token → 驗 ID token → 找出或建立帳號 → 發 session。"""
    settings = _settings()
    now = int(time.time())
    ip = auth.client_ip(request)
    await security_ops.flush_tallies(deps.accounts)
    if not auth.login_allowed(request):
        await security_ops.note_throttled(deps.accounts, security_ops.INSECURE_TALLY, ip)
        logger.warning("SSO 登入遭拒：非 HTTPS 且非本機 peer=%s", auth.peer_ip(request))
        return _login_error("insecure", "/")
    pending = auth.parse_oidc_token(request.cookies.get(auth.OIDC_COOKIE_NAME), now)
    state = request.query_params.get("state") or ""
    if pending is None or not hmac.compare_digest(state.encode(), pending.state.encode()):
        await security_ops.note_throttled(deps.accounts, security_ops.SSO_STATE_TALLY, ip)
        logger.warning("SSO 登入遭拒：登入流程的暫時憑證不存在、已逾時或 state 不符 ip=%s", ip)
        return _login_error("sso", "/")
    nxt = pending.next
    idp_error = request.query_params.get("error")
    if idp_error is not None:
        await security_ops.note_throttled(deps.accounts, security_ops.SSO_IDP_ERROR_TALLY, ip)
        shown = idp_error if oidc.OAUTH_ERROR_RE.fullmatch(idp_error) else "-"
        logger.warning("SSO 登入失敗：IdP 回 error=%s ip=%s", shown, ip)
        return _login_error("sso", nxt)
    code = request.query_params.get("code") or ""
    try:
        if not code:
            raise oidc.OidcError("token", "callback 沒有授權碼")
        id_token = await oidc.exchange_code(settings, code, pending.code_verifier)
        claims = await oidc.verify_id_token(settings, id_token, nonce=pending.nonce)
        ident = oidc.identity(settings, claims)
    except oidc.OidcError as exc:
        if exc.reason == "unavailable":
            logger.error("SSO 登入失敗：IdP 無法使用（%s）ip=%s", exc, ip)
            return _login_error("sso_unavailable", nxt)
        await _event("login.sso_failure", request, reason=exc.reason)
        logger.warning("SSO 登入失敗 reason=%s：%s ip=%s", exc.reason, exc, ip)
        return _login_error("sso", nxt)
    if pending.intent == "elevate":
        return await _finish_elevation(request, pending, ident, now)
    session_id = None
    try:
        result = await deps.accounts.sso_login(ident.issuer, ident.subject, ident.username)
        if result.user is not None and result.user.id is not None:
            session_id = await deps.accounts.create_session(
                result.user.id, max_age_seconds=auth.MAX_ABSOLUTE_TTL, ip=ip,
                user_agent=request.headers.get("user-agent"), auth_method="sso", idp_mfa=ident.idp_mfa,
            )
    except Exception:
        logger.exception("SSO 登入失敗：帳號服務無法使用 ip=%s", ip)
        return _login_error("unavailable", nxt)
    if result.user is None or session_id is None:
        await _event("login.sso_failure", request, reason=result.reason, user_id=result.user_id)
        logger.warning("SSO 登入遭拒 reason=%s user_id=%s ip=%s", result.reason, result.user_id, ip)
        return _login_error(_ACCOUNT_ERRORS.get(result.reason, "sso_account"), nxt)
    await _event("login.success", request, reason="sso", user_id=result.user.id, session_id=session_id)
    logger.info("登入成功（SSO）ip=%s peer=%s user=%s role=%s idp_mfa=%s jit=%s linked=%s", ip,
                auth.peer_ip(request), result.user.username, result.user.role, ident.idp_mfa, result.created,
                result.linked)
    resp = RedirectResponse(nxt, status_code=303)
    auth.clear_oidc_cookie(resp)
    auth.set_session_cookie(resp, now, session_id=session_id, secure=auth.request_is_secure(request))
    return resp


async def _finish_elevation(request: Request, pending: auth.OidcPending, ident: oidc.Identity, now: int):
    """權限提升的後半段。不論成敗都導回 next；結果由前端重新取 /api/me 的 elevated_until 得知。"""
    settings = get_settings().oidc
    resp = RedirectResponse(pending.next, status_code=303)
    auth.clear_oidc_cookie(resp)
    try:
        session, user = await _session_user(request, now)
        if session is None or user is None or session.session_id != pending.session_id:
            await _event("elevate.failure", request, reason="sso_session",
                         user_id=getattr(user, "id", None))
            logger.warning("SSO 權限提升遭拒：瀏覽器的 session 已失效或與發起時不同")
            return resp
        if not oidc.reauth_is_fresh(settings, ident, now=now):
            await _event("elevate.failure", request, reason="sso_stale_auth", user_id=user.id,
                         session_id=session.session_id)
            logger.warning("SSO 權限提升遭拒：IdP 沒有重新驗證（auth_time 缺少或太舊）user=%s", user.username)
            return resp
        until = await deps.accounts.elevate_sso_session(session.session_id, issuer=ident.issuer,
                                                         subject=ident.subject)
    except Exception:
        logger.exception("SSO 權限提升失敗：帳號服務無法使用")
        return resp
    if until is None:
        await _event("elevate.failure", request, reason="sso_identity", user_id=user.id,
                     session_id=session.session_id)
        logger.warning("SSO 權限提升遭拒：IdP 回來的不是這個帳號綁定的身分 user=%s", user.username)
        return resp
    await _event("elevate.success", request, reason="sso", user_id=user.id, session_id=session.session_id)
    logger.info("權限提升（SSO）user=%s", user.username)
    return resp
