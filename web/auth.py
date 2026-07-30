# web/auth.py
"""App 層登入認證:共用帳密驗證、hmac 簽章 session cookie、每 IP 失敗限流。

設定來自環境變數(沿用本專案 os.environ 慣例):
  REPORT_MARK_ACCESS_USERNAME / REPORT_MARK_ACCESS_PASSWORD  共用帳密(未設則 fail-closed 報錯)
  REPORT_MARK_SESSION_SECRET                                  cookie 簽章金鑰(未設則隨機,重啟登出所有人)
  REPORT_MARK_SESSION_EPOCH                                   全員登出開關:改成任何新值即讓所有既發 token 失效
  REPORT_MARK_EDGE_SECRET                                     邊緣 nginx 注入的共享祕密,與 CIDR 並存判定可信代理
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import logging
import os
import secrets
from dataclasses import dataclass

logger = logging.getLogger(__name__)

COOKIE_NAME = "tf_session"
SESSION_TTL = 7 * 24 * 3600  # 7 天;滑動到期由 middleware 每次回應刷新
# 絕對存活上限:滑動續期若沒有天花板,一個從未過期的 cookie 等於永久憑證——只要
# 使用者天天開站,7 天的 exp 每次請求都被推遠,竊得的 cookie 也一樣被推遠。
# 取 30 天的理由:本站是少數同事在用的研究工具,「每月重登一次」是可接受的摩擦,
# 而它剛好對齊「定期更換密碼」的節奏(換密碼已能即時撤銷,見 _identity_fingerprint);
# 這條上限是「密碼與金鑰都沒換、但 cookie 外流」情境下的最後止血點。
MAX_ABSOLUTE_TTL = 30 * 24 * 3600
MAX_FAILS = 5                # 視窗內允許的最大登入失敗次數
FAIL_WINDOW = 300            # 失敗計數視窗(秒)
MAX_TRACKED_IPS = max(1, int(os.environ.get("REPORT_MARK_MAX_TRACKED_FAIL_IPS", "4096")))

_USERNAME = os.environ.get("REPORT_MARK_ACCESS_USERNAME", "")
_PASSWORD = os.environ.get("REPORT_MARK_ACCESS_PASSWORD", "")
if not _USERNAME or not _PASSWORD:
    raise RuntimeError(
        "REPORT_MARK_ACCESS_USERNAME 與 REPORT_MARK_ACCESS_PASSWORD 必須設定(fail-closed)"
    )

_USERNAME_B = _USERNAME.encode()
_PASSWORD_B = _PASSWORD.encode()

# 共用帳號名稱（公開常數）：供前端在側欄底部顯示「目前登入帳號」。
# 本專案為單一共用帳號，session cookie 不帶個別身分，故此值對所有人相同。
ACCESS_USERNAME = _USERNAME

_SECRET = os.environ.get("REPORT_MARK_SESSION_SECRET", "")
if not _SECRET:
    _SECRET = secrets.token_hex(32)
    logger.warning("REPORT_MARK_SESSION_SECRET 未設定,已隨機產生(重啟將登出所有人)")

# 全員登出開關:值進入簽章訊息,所以改成任何新字串都會讓既發 token 一次失效。
# 存在的理由是「不想換密碼、也不想換簽章金鑰,但要把所有 session 踢掉」——
# 換金鑰同樣有效,但金鑰是機密、輪替流程比較重;這個旋鈕可以隨手 bump。
_SESSION_EPOCH = os.environ.get("REPORT_MARK_SESSION_EPOCH", "")

_TRUSTED_PROXY_CIDRS = os.environ.get(
    "REPORT_MARK_TRUSTED_PROXY_CIDRS",
    "127.0.0.1/32,::1/128",
)

# 邊緣 nginx 注入的共享祕密。CIDR 判定的弱點是它綁在 Docker→WSL 閘道 IP 上,而該
# IP 會隨 WSL 重開機漂移,一漂移就是全體外網登入被擋(2026-07-17 實際事故,症狀是
# /login?error=insecure)。祕密 header 不隨網段變動,故與 CIDR **並存(OR)**:兩者
# 任一成立即視為可信代理,滾動切換期間(先改 app 或先改 nginx)都不會把人擋在外面。
# 未設＝停用這條路徑(不是「誰都算可信」),行為完全退回 CIDR 判定。
_EDGE_SECRET = os.environ.get("REPORT_MARK_EDGE_SECRET", "")
EDGE_SECRET_HEADER = "x-edge-secret"


def _parse_networks(raw: str) -> tuple[ipaddress._BaseNetwork, ...]:
    networks = []
    for part in raw.split(","):
        cidr = part.strip()
        if not cidr:
            continue
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            logger.warning("忽略無效的 REPORT_MARK_TRUSTED_PROXY_CIDRS: %s", cidr)
    return tuple(networks)


_TRUSTED_PROXY_NETWORKS = _parse_networks(_TRUSTED_PROXY_CIDRS)


def _sign(msg: str) -> str:
    digest = hmac.new(_SECRET.encode(), msg.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _identity_fingerprint() -> str:
    """目前帳密的指紋(sha256 前 16 hex),用來讓「換密碼」等同「全員登出」。

    為什麼要有:帳密與簽章金鑰原本互相獨立,換掉密碼對既發 token 一點影響都沒有——
    也就是「懷疑密碼外流」時除了換金鑰之外沒有任何撤銷手段。把指紋放進**簽章訊息**
    (不是 token 本體)後,舊 token 的簽章對不上新的訊息,自然全部失效。

    刻意每次呼叫重算而非啟動時算一次:成本是一次短字串 sha256(微秒級),換來的是
    「憑證是唯一真相來源」——測試可以直接替換 _PASSWORD_B 驗證輪替行為,不會有
    一份啟動時複製的快取在旁邊說謊。
    """
    return hashlib.sha256(_USERNAME_B + b"\x00" + _PASSWORD_B).hexdigest()[:16]


# token 版本。格式為 `<ver>.<iat>.<exp>.<sig>`;v1 是舊的 `<exp>.<sig>`。
# 版本欄位的用途是**讓下一次格式變更不必再全員登出一次**:verify 認得版本就能
# 對舊格式明確拒絕(而不是靠簽章比對碰巧失敗),也能在需要時同時接受新舊兩版。
TOKEN_VERSION = 2


def _token_message(iat: int, exp: int) -> str:
    """簽章訊息:版本、簽發時刻、到期時刻、帳密指紋、登出 epoch。

    後兩項刻意**不放進 token 本體**——它們是伺服器端狀態,只要參與簽章就足以讓
    「換帳密」與「bump epoch」成為撤銷手段,同時 cookie 不會外洩任何密碼衍生值。
    分隔符用 `.` 不會有歧義:只有最後一欄(epoch)是自由字串,前面全是數字與 hex。
    """
    return f"{TOKEN_VERSION}.{iat}.{exp}.{_identity_fingerprint()}.{_SESSION_EPOCH}"


@dataclass(frozen=True)
class Session:
    """一個已驗章、未過期的 session。issued_at 是滑動續期必須沿用的原始簽發時刻。"""

    issued_at: int
    expires_at: int


def issue_token(now: int, *, issued_at: int | None = None) -> str:
    """簽發 `<ver>.<iat>.<exp>.<sig>`。

    issued_at 用於滑動續期:續期只推遲 exp,**iat 必須沿用原值**,否則絕對存活
    上限會被每次續期重置,等於沒有上限。
    """
    iat = now if issued_at is None else issued_at
    # exp 同時受滑動視窗與絕對上限夾擊,取小者;絕對上限另有 parse_token 再驗一次
    # (縱深防禦:即使日後有人繞過這裡直接組 token,驗證端仍會擋)。
    exp = min(now + SESSION_TTL, iat + MAX_ABSOLUTE_TTL)
    return f"{TOKEN_VERSION}.{iat}.{exp}.{_sign(_token_message(iat, exp))}"


def parse_token(token: str | None, now: int) -> Session | None:
    """驗章 + 未過期 + 未超過絕對上限,才回 Session;否則 None。"""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 4:
        _log_legacy_token(parts)
        return None
    ver_s, iat_s, exp_s, sig = parts
    # 只收正規十進位表示:int() 會接受 "1_0"、" 10"、"+10" 等變體,雖然簽章仍對得上
    # (訊息以解析後的整數重組),但讓同一個 session 有多種字面表示沒有好處。
    if not (ver_s.isdigit() and iat_s.isdigit() and exp_s.isdigit()):
        return None
    ver, iat, exp = int(ver_s), int(iat_s), int(exp_s)
    if ver != TOKEN_VERSION:
        logger.info("拒絕非現行版本的 session token(version=%s,現行=%s)", ver, TOKEN_VERSION)
        return None
    if not hmac.compare_digest(sig.encode(), _sign(_token_message(iat, exp)).encode()):
        return None
    if exp <= now:
        return None
    if iat + MAX_ABSOLUTE_TTL <= now:
        return None
    return Session(issued_at=iat, expires_at=exp)


def _log_legacy_token(parts: list[str]) -> None:
    """認得出來的舊格式明確記一筆;認不出來的雜訊靜默丟棄(避免被灌日誌)。"""
    if len(parts) == 2 and parts[0].isdigit():
        logger.info("拒絕舊版(v1)session token——格式已改為 <ver>.<iat>.<exp>.<sig>")


def verify_token(token: str | None, now: int) -> bool:
    """簽章正確、未過期且未超過絕對上限才視為有效。"""
    return parse_token(token, now) is not None


def check_credentials(username: str, password: str) -> bool:
    """常數時間比對帳號與密碼(先各算再 AND,不短路,避免時序側信道)。
    以 bytes 比對:compare_digest 對含非 ASCII 的 str 會丟 TypeError,
    統一編碼成 bytes,讓中文/任意字元帳密一律安全比對而非崩潰。"""
    u_ok = hmac.compare_digest((username or "").encode(), _USERNAME_B)
    p_ok = hmac.compare_digest((password or "").encode(), _PASSWORD_B)
    return u_ok and p_ok


def username_matches(username: str) -> bool:
    """只比帳號(常數時間)。供稽核日誌區分「打錯密碼」與「亂猜帳號的掃描流量」,
    不必把使用者送來的字串原樣寫進日誌——那個欄位很常被誤填成密碼。"""
    return hmac.compare_digest((username or "").encode(), _USERNAME_B)


def set_session_cookie(response, now: int, *, secure: bool, issued_at: int | None = None) -> None:
    """設定 session cookie。issued_at 由滑動續期端帶入原始簽發時刻(見 issue_token)。"""
    iat = now if issued_at is None else issued_at
    # cookie 的 max_age 也要吃絕對上限,否則瀏覽器會抱著一個伺服器早就拒收的
    # cookie,症狀變成「看起來還登著、每個請求卻被導回登入頁」。
    max_age = max(0, min(SESSION_TTL, iat + MAX_ABSOLUTE_TTL - now))
    response.set_cookie(
        COOKIE_NAME,
        issue_token(now, issued_at=iat),
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def clear_session_cookie(response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ───── 每 IP 失敗限流(in-memory,重啟即重置)─────
_FAILS: dict[str, list[int]] = {}
def _prune(ip: str, now: int) -> list[int]:
    fails = [t for t in _FAILS.get(ip, []) if t > now - FAIL_WINDOW]
    if fails:
        _FAILS[ip] = fails
    else:
        _FAILS.pop(ip, None)
    return fails


def _sweep_all(now: int) -> None:
    stale = [ip for ip in list(_FAILS) if not _prune(ip, now)]
    for ip in stale:
        _FAILS.pop(ip, None)
    overflow = len(_FAILS) - MAX_TRACKED_IPS
    if overflow <= 0:
        return
    # 若短時間冒出大量新 IP，保留最近有失敗紀錄者，其餘淘汰，避免 dict 無界成長。
    oldest = sorted(_FAILS.items(), key=lambda item: item[1][-1])[:overflow]
    for ip, _fails in oldest:
        _FAILS.pop(ip, None)
def is_locked(ip: str, now: int) -> bool:
    return len(_prune(ip, now)) >= MAX_FAILS


def record_failure(ip: str, now: int) -> None:
    _sweep_all(now)
    fails = _prune(ip, now)
    fails.append(now)
    _FAILS[ip] = fails
    if len(_FAILS) > MAX_TRACKED_IPS:
        _sweep_all(now)


def reset(ip: str) -> None:
    _FAILS.pop(ip, None)


def failure_count(ip: str, now: int) -> int:
    """視窗內的失敗次數(供稽核日誌把「第幾次」寫進去,不必去讀私有 dict)。"""
    return len(_prune(ip, now))


def peer_ip(request) -> str:
    """實際 TCP 對端位址(未經任何 header 還原)。

    與 client_ip 的差別正是排查外網登入被擋時要看的東西:被拒時真正需要知道的是
    「誰打進來的」——那個位址才是要不要放進 REPORT_MARK_TRUSTED_PROXY_CIDRS 的判準。
    """
    return request.client.host if request.client else "unknown"


def client_ip(request) -> str:
    """真實來源 IP:nginx 以 X-Real-IP 帶入還原後 IP;LAN 直連則用連線位址。
    外部無法偽造 X-Real-IP(nginx 以 $remote_addr 覆寫)。"""
    peer = peer_ip(request)
    forwarded = request.headers.get("x-real-ip")
    if forwarded and from_trusted_proxy(request):
        return forwarded
    return peer


def _edge_secret_ok(request) -> bool:
    """邊緣共享祕密比對(常數時間)。未設祕密時一律 False＝這條路徑停用。"""
    if not _EDGE_SECRET:
        return False
    presented = request.headers.get(EDGE_SECRET_HEADER) or ""
    return hmac.compare_digest(presented.encode(), _EDGE_SECRET.encode())


def from_trusted_proxy(request) -> bool:
    """這個請求是不是從可信反向代理進來的:祕密 header **或** 來源 IP 在 CIDR 內。

    刻意是 OR 而不是取代:改 nginx 與改 app 之間必然有時間差,只認其一的話那段
    窗口會把所有外網使用者擋在門外。
    """
    return _edge_secret_ok(request) or _is_trusted_proxy(peer_ip(request))


def _is_trusted_proxy(host: str | None) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    return any(addr in network for network in _TRUSTED_PROXY_NETWORKS)


def _host_is_local(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def request_is_secure(request) -> bool:
    if request.url.scheme == "https":
        return True
    if not from_trusted_proxy(request):
        return False
    proto = request.headers.get("x-forwarded-proto", "")
    return proto.lower() == "https"


def allow_insecure_local(request) -> bool:
    host = request.url.hostname
    peer = request.client.host if request.client else None
    return _host_is_local(host) or _host_is_local(peer)


def login_allowed(request) -> bool:
    return request_is_secure(request) or allow_insecure_local(request)
