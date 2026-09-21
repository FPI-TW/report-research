# web/dev_mode.py
"""開發模式免登入（`DEV_NO_AUTH=1`）。

**本模組必須在 `load_env_file()` 之前被 import**（`web/server.py` 頂端已如此排），
理由見下方第 1 條。

存在理由：這個站的每一頁都在 deny-by-default 的 `require_login` 後面，所以任何
「看一下版面對不對」都得先登入。給 agent／自動化截圖用的旗標若做得隨便，就是把
一個對外站台的認證關掉——本專案的外部入口是 Cloudflare Tunnel ＋ nginx，而
**nginx 是 proxy 到本機的**，所以「只要求本機」單獨並不夠。放行需要三個條件同時成立：

1. **旗標在 `.env` 被載入之前就已存在於 `os.environ`**。`web/env_loader.py` 會把 repo 根
   `.env` 灌進 `os.environ`，而**這台機器的 repo root 就是部署目錄**（systemd 的
   `WorkingDirectory`）——若在 import `.env` 之後才讀這個旗標，任何人把
   `DEV_NO_AUTH=1` 寫進 `.env`（或不小心 commit 進去）就等於把生產站的登入永久關掉，
   而且**沒有任何錯誤訊息**。快照取在載入之前，`.env` 就物理上打不開這個開關；
   只有啟動命令列上的 `DEV_NO_AUTH=1 uv run ...` 才算數。這與 `SKIP_WARMUP` 是同一種
   設計（一次性啟動選擇，不是部署設定），但那個旗標最壞只是慢，這個是認證。
2. **TCP 對端是 loopback**。外網走 cloudflared → nginx → `host.docker.internal:8097`，
   對端是 Docker 橋接位址而不是 127.0.0.1。
3. **請求不帶任何反向代理 header**。第 2 條在別種部署（nginx 與 app 同機直連 127.0.0.1）
   會失效，所以另外看 `deploy/nginx.conf` 一定會注入的那組 header（`Host` 改寫、
   `X-Real-IP`、`X-Forwarded-For`、`X-Forwarded-Proto`、`X-Edge-Secret`）。
   帶了其中任何一個就代表這是經過邊緣進來的，一律不放行。

三條是 AND。少任何一條，這個檔案就不該存在。
"""

from __future__ import annotations

import ipaddress
import logging
import os

logger = logging.getLogger(__name__)

# 條件 1 的快照：模組 import 期取值，而本模組排在 load_env_file 之前。
# 之後再改 os.environ 也不生效——旗標必須在行程啟動前就決定。
_ENABLED: bool = os.environ.get("DEV_NO_AUTH") == "1"

# 條件 3：只要出現其中任何一個 header 就視為「經過代理」。這串是白名單的反面，
# 寧可誤判成「不放行」——誤判的代價是要登入，反過來的代價是站台開著。
_PROXY_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-host",
    "x-real-ip",
    "x-edge-secret",
    "forwarded",
    "cf-connecting-ip",
)


def enabled() -> bool:
    """旗標本身（僅供啟動橫幅與測試用；放行判定一律走 bypass_allowed）。"""
    return _ENABLED


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def bypass_allowed(request) -> bool:
    """這個請求可否免登入。旗標只是必要條件，見模組 docstring 的三條。"""
    if not _ENABLED:
        return False
    return is_direct_loopback(request)


def is_direct_loopback(request) -> bool:
    """請求是否為「本機直連、未經任何代理」——與 DEV_NO_AUTH 旗標無關的那幾個條件。

    獨立出來是因為 `/healthz/storage` 也需要同一個判定（只回答本機探針），而它不該被
    開發旗標牽動。經邊緣進來的請求一定帶代理 header、對端是 docker 網段、Host 是公開網域，
    三者任一即否決。
    """
    headers = request.headers
    if any(h in headers for h in _PROXY_HEADERS):
        return False
    peer = request.client.host if request.client else None
    if not _is_loopback(peer):
        return False
    # Host 也必須是本機：經過邊緣的請求會帶公開網域（nginx 的 proxy_set_header Host $host）。
    return _is_loopback(request.url.hostname)


def log_banner() -> None:
    """啟動時把狀態說出來。開著卻沒人知道，就是這種旗標最常見的失事方式。"""
    if _ENABLED:
        logger.warning(
            "DEV_NO_AUTH=1：本機直連的請求免登入（外部與經代理的請求仍需登入）。"
            "**這是開發用旗標，不要用在對外服務的行程上。**"
        )
