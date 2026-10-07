# web/routers/auth_pages.py
"""登入流程頁面路由：GET/POST /login、POST /logout，以及目前登入身分 GET /api/me。

從 web/server.py 拆出（收尾）。認證的 deny-by-default middleware（require_login）
與其 _auth_allowed 白名單仍在 server.py——middleware 必須註冊在 app 上、且要包住
所有 Mount，不可降級為 router dependency（否則 /static、/app/assets 失去保護）。
本模組只承載登入流程的「路由」，共用的認證原語走 web.auth。

**登入稽核**：成功／失敗／限流鎖定／非 HTTPS 遭拒四種結果各記一行。在此之前
`web/auth.py` 與本模組對登入結果零 log——暴力破解在 journald 完全無痕跡，而
2026-07-17 那次外網全體登入被擋（`?error=insecure`，信任代理 CIDR 隨 WSL 重開機
漂掉）也只能靠猜，因為沒有任何一行說得出「是誰打進來、被哪一關擋下」。

等級的取捨（`app/logging_setup.py` 上線後 root 預設 INFO，兩種等級都送得進
journald，所以這是政策選擇不是技術限制）：**異常事件走 WARNING、常規事件走
INFO**。失敗／鎖定／遭拒是異常，值得在把 `LOG_LEVEL` 調成 WARNING 的環境裡仍然
留存；登入成功是每天都會發生的常規事件，用 WARNING 會讓「警告」這個等級失去意義。
**代價寫在這裡**：`LOG_LEVEL=WARNING` 會失去「誰在何時登入」的那一半稽核，只剩
攻擊面那一半（`.env.example` 的 LOG_LEVEL 註解同步寫了這條）。

**絕對不記密碼，連長度都不記**：長度會把暴力破解的搜尋空間直接縮小。失敗時帳號也
不記原樣字串——那個欄位很常被誤填成密碼——只記「帳號是否存在」的布林結果；成功時
帳號已證實是帳號，才記名稱。

帳號與 session 都在 DB（`deps.accounts`）：登入成功＝開一個 `user_session`，登出＝
撤銷那一個 session（其他裝置不受影響）。帳號服務掛掉時導向 `?error=unavailable`，
不記成登入失敗、也不計入限流——那不是使用者打錯。

**兩步驟驗證（TOTP）**：密碼正確但帳號開了 TOTP 時不發 session，改發 5 分鐘、path=/login 的
簽章暫時憑證（`web.auth.set_mfa_cookie`）並導向 `/login?step=totp`；同一個 POST /login 帶
`step=totp` 與 `code` 完成第二步。第二步也走同一套 HTTPS 檢查與每 IP 失敗限流，驗證碼錯一次
算一次失敗。暫時憑證不可重放（指紋綁最後使用的時間步，見 `accounts.mfa_fingerprint`），
仍留著的話只會讓第二步失敗、要求重新輸入密碼。路徑仍只有 /login，認證白名單不必變。
"""
import logging
import time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, RedirectResponse

from app.services.accounts import User
from web import auth, authz, deps

logger = logging.getLogger(__name__)

router = APIRouter()


def _safe_next(raw: str | None) -> str:
    """只接受同源相對路徑：必須以單一 '/' 開頭，拒絕 //、/\\、schema URL、控制字元（含 CRLF）。否則回 '/'。"""
    if not raw or not raw.startswith("/"):
        return "/"
    if raw.startswith("//") or raw.startswith("/\\"):
        return "/"
    if "://" in raw:
        return "/"
    # 拒絕所有 C0 控制字元（含 CR/LF/TAB/NUL）與 DEL，使白名單自身完備（縱深防禦）
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in raw):
        return "/"
    return raw


def _static_page(name: str) -> FileResponse:
    """回傳 HTML 殼，並標記 no-cache（瀏覽器每次重新向伺服器取用，改版後同事免強制重整即見新版）。

    註：route-level 的 FileResponse 不會對 If-None-Match/If-Modified-Since 做 304 短路，
    每次都回 200 全量 body（HTML 約數十 KB，區網成本可忽略）。/static 下的 css/js 由
    _NoCacheStatic（StaticFiles）服務，才有條件式 304 重新驗證。
    """
    return FileResponse(deps.STATIC_DIR / name, headers={"Cache-Control": "no-cache"})


async def _cookie_user(request: Request) -> User | None:
    """cookie 對應的使用者（簽章有效且 DB 仍認可）；任何失敗都當作沒登入。

    只看簽章不夠：已撤銷的 cookie 簽章照樣有效，登入頁若據此導向首頁，middleware 又會
    把它導回登入頁——無限迴圈。
    """
    session = auth.parse_token(request.cookies.get(auth.COOKIE_NAME), int(time.time()))
    if session is None:
        return None
    try:
        return await deps.accounts.resolve_session(session.session_id)
    except Exception:
        logger.warning("登入頁 session 查驗失敗（視為未登入）", exc_info=True)
        return None


async def _login_totp_step(request: Request, code: str, nxt: str, err_q: str, now: int, ip: str):
    """POST /login 的第二步（step=totp）。憑暫時憑證找回帳號、驗驗證碼，通過才發 session。"""
    pending = auth.parse_mfa_token(request.cookies.get(auth.MFA_COOKIE_NAME), now)
    if pending is None:
        logger.warning("登入第二步遭拒：暫時憑證不存在、簽章不符或已逾時 ip=%s", ip)
        resp = RedirectResponse(f"/login?error=expired{err_q}", status_code=303)
        auth.clear_mfa_cookie(resp)
        return resp
    user_id, fingerprint = pending
    session_id = None
    try:
        user = await deps.accounts.complete_totp_login(user_id, fingerprint, code)
        if user is not None and user.id is not None:
            session_id = await deps.accounts.create_session(
                user.id, max_age_seconds=auth.MAX_ABSOLUTE_TTL, ip=ip,
                user_agent=request.headers.get("user-agent"),
            )
    except Exception:
        logger.exception("登入第二步失敗：帳號服務無法使用 ip=%s", ip)
        return RedirectResponse(f"/login?step=totp&error=unavailable{err_q}", status_code=303)
    if user is None or session_id is None:
        auth.record_failure(ip, now)
        logger.warning("登入第二步失敗（驗證碼錯誤、已用過或暫時憑證已失效）ip=%s 視窗內失敗次數=%s",
                       ip, auth.failure_count(ip, now))
        return RedirectResponse(f"/login?step=totp&error=totp{err_q}", status_code=303)
    auth.reset(ip)
    logger.info("登入成功（兩步驟驗證）ip=%s peer=%s user=%s role=%s",
                ip, auth.peer_ip(request), user.username, user.role)
    resp = RedirectResponse(nxt, status_code=303)
    auth.clear_mfa_cookie(resp)
    auth.set_session_cookie(resp, now, session_id=session_id, secure=auth.request_is_secure(request))
    return resp


@router.get("/login")
async def login_page(request: Request):
    nxt = _safe_next(request.query_params.get("next"))
    if await _cookie_user(request) is not None:
        return RedirectResponse(nxt, status_code=302)
    return _static_page("login.html")


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
    step: str = Form(""),
    code: str = Form(""),
):
    nxt = _safe_next(next)
    err_q = "&next=" + quote(nxt, safe="") if nxt != "/" else ""
    now = int(time.time())
    ip = auth.client_ip(request)
    if not auth.login_allowed(request):
        # peer 與 x-forwarded-proto 是這條分支唯一有用的線索：要判斷該不該把某個
        # 位址加進 REPORT_MARK_TRUSTED_PROXY_CIDRS（或改用 X-Edge-Secret），
        # 得先知道 uvicorn 實際看到的對端是誰。
        logger.warning(
            "登入遭拒：非 HTTPS 且非本機 peer=%s host=%s x-forwarded-proto=%s",
            auth.peer_ip(request),
            request.url.hostname,
            request.headers.get("x-forwarded-proto", ""),
        )
        return RedirectResponse(f"/login?error=insecure{err_q}", status_code=303)
    if auth.is_locked(ip, now):
        logger.warning("登入遭限流鎖定 ip=%s 視窗內失敗次數=%s", ip, auth.failure_count(ip, now))
        locked_step = "step=totp&" if step == "totp" else ""
        return RedirectResponse(f"/login?{locked_step}error=locked{err_q}", status_code=303)
    if step == "totp":
        return await _login_totp_step(request, code, nxt, err_q, now, ip)
    try:
        result = await deps.accounts.authenticate(username, password)
        session_id = None
        if result.user is not None and result.user.id is not None:
            session_id = await deps.accounts.create_session(
                result.user.id,
                max_age_seconds=auth.MAX_ABSOLUTE_TTL,
                ip=ip,
                user_agent=request.headers.get("user-agent"),
            )
    except Exception:
        logger.exception("登入失敗：帳號服務無法使用 ip=%s", ip)
        return RedirectResponse(f"/login?error=unavailable{err_q}", status_code=303)
    if result.reason == "totp_required" and result.challenge is not None:
        # 還沒登入成功：不重設失敗計數，也只記 user_id（帳號名稱留到真正登入成功那一行）。
        logger.info("登入第一步通過，等待驗證碼 ip=%s user_id=%s", ip, result.challenge.user_id)
        resp = RedirectResponse(f"/login?step=totp{err_q}", status_code=303)
        auth.set_mfa_cookie(resp, now, user_id=result.challenge.user_id,
                            fingerprint=result.challenge.fingerprint, secure=auth.request_is_secure(request))
        return resp
    if result.user is not None and session_id is not None:
        auth.reset(ip)
        logger.info(
            "登入成功 ip=%s peer=%s user=%s role=%s",
            ip, auth.peer_ip(request), result.user.username, result.user.role,
        )
        resp = RedirectResponse(nxt, status_code=303)
        auth.set_session_cookie(
            resp, now, session_id=session_id, secure=auth.request_is_secure(request),
        )
        return resp
    auth.record_failure(ip, now)
    if result.reason == "disabled":
        # 只有密碼正確才會走到這裡，所以告訴對方「已停用」不會洩漏給亂猜的人。
        logger.warning("登入遭拒：帳號已停用 ip=%s 視窗內失敗次數=%s", ip, auth.failure_count(ip, now))
        return RedirectResponse(f"/login?error=disabled{err_q}", status_code=303)
    logger.warning(
        "登入失敗 ip=%s 帳號存在=%s 視窗內失敗次數=%s",
        ip,
        result.reason == "bad_password",
        auth.failure_count(ip, now),
    )
    return RedirectResponse(f"/login?error=1{err_q}", status_code=303)


@router.post("/logout")
async def logout(request: Request):
    session = auth.parse_token(request.cookies.get(auth.COOKIE_NAME), int(time.time()))
    if session is not None:
        try:
            await deps.accounts.revoke_session(session.session_id)
        except Exception:
            # cookie 照樣清掉；DB 那一列會在絕對存活上限到期後失效。
            logger.warning("登出時撤銷 session 失敗", exc_info=True)
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(resp)
    return resp


@router.get("/api/me")
async def me(user: User = Depends(authz.current_user)):
    """目前登入的身分。前端據此顯示帳號名稱與決定要不要露出管理頁入口——
    那只是顯示；管理端點的授權一律由後端 `authz.require_admin` 判斷。

    `mfa_enrollment_required`：管理員 TOTP 強制開啟、而這位管理員還沒開 TOTP（管理端點此時一律 403
    `mfa_enrollment_required`）。前端管理後台據此改顯示 TOTP 設定（`AdminShell`）。"""
    return {
        "id": user.id, "username": user.username, "role": user.role,
        "is_super": user.is_super, "scopes": sorted(user.scopes),
        "elevated_until": user.elevated_until.isoformat() if user.is_elevated else None,
        "totp_enabled": user.totp_enabled,
        "mfa_enrollment_required": authz.mfa_enrollment_required(user),
    }
