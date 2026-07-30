# web/routers/auth_pages.py
"""登入流程頁面路由：GET/POST /login、POST /logout。

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

**絕對不記密碼，連長度都不記**：長度會把暴力破解的搜尋空間直接縮小。帳號也不記
原樣字串——那個欄位很常被誤填成密碼——只記 `auth.username_matches()` 的布林結果。
"""
import logging
import time
from urllib.parse import quote

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, RedirectResponse

from web import auth, deps

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


@router.get("/login")
async def login_page(request: Request):
    nxt = _safe_next(request.query_params.get("next"))
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME), int(time.time())):
        return RedirectResponse(nxt, status_code=302)
    return _static_page("login.html")


@router.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
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
        return RedirectResponse(f"/login?error=locked{err_q}", status_code=303)
    if auth.check_credentials(username, password):
        auth.reset(ip)
        logger.info("登入成功 ip=%s peer=%s", ip, auth.peer_ip(request))
        resp = RedirectResponse(nxt, status_code=303)
        auth.set_session_cookie(resp, now, secure=auth.request_is_secure(request))
        return resp
    auth.record_failure(ip, now)
    logger.warning(
        "登入失敗 ip=%s 帳號相符=%s 視窗內失敗次數=%s",
        ip,
        auth.username_matches(username),
        auth.failure_count(ip, now),
    )
    return RedirectResponse(f"/login?error=1{err_q}", status_code=303)


@router.post("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    auth.clear_session_cookie(resp)
    return resp
