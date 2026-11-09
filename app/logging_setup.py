"""集中的 logging 設定——沒有它，`logger.info(...)` 等於寫進黑洞。

**為什麼需要這一支**：uvicorn 的 `LOGGING_CONFIG` 只宣告 `uvicorn` /
`uvicorn.error` / `uvicorn.access` 三個 logger，**沒有 `root`**；而本 repo 至今
沒有任何 `basicConfig` / `dictConfig`。兩者相加的結果是 `app.services.*` 的
logger 落到 `logging.lastResort`（level=WARNING、格式只有裸訊息），於是
`logger.info(...)` 在呼叫點就被丟棄，WARNING 以上雖然印得出來卻沒有 level
與 logger 名稱。

實證（2026-07-29 查生產 journald 近 14 天）：`/api/ask` 有 10 次請求，
`answer.py` 的 `qa_timing`（分段耗時遙測）**0 筆**，`qa_agentic`（agentic 降級
旗標）同理。也就是說「分段耗時觀測」寫了好幾個里程碑，一行都沒能拿來排查。
對照組是 uvicorn 自己的 access log 正常流入——證明不是 journald 沒收到 stderr。

兩個實作上的要點：

1. **宣告 `root` 才是關鍵**。補 handler 給個別 logger 治標不治本，因為問題出在
   root 沒有 handler、effective level 又是 WARNING。
2. **刻意不宣告 uvicorn 的三個 logger**，並且 `disable_existing_loggers: False`
   ——uvicorn 在 import app 之前就套用過自己的 dictConfig，本設定在其後執行；
   若把它們一起宣告，會把現在唯一能用的 access log 改掉或關掉。

時間戳與 journald 的時間戳會重複一次。這是刻意的：`make serve`（非 systemd）
與被複製出去的日誌片段都需要自帶時間，多一個欄位換取「日誌行本身是自足的」。

## 結構化 JSON（`LOG_FORMAT=json`；容器映像的預設，systemd 主機維持 text）

給券商自己的日誌平台（ELK、Loki、Splunk）直接收：每行一個 JSON 物件寫到 **stdout**，固定欄位
`ts`（UTC、ISO-8601、毫秒）、`level`、`logger`、`request_id`（同 `rid=`，對得上錯誤回應的 `request_id`，
`web/errors.py`）、`message`；有例外時加 `exc_type` 與 `exc`（traceback）。**只有這幾個欄位**：
`extra=` 帶進來的屬性一律不輸出（白名單，不是黑名單）。

JSON 模式另外接管 uvicorn 的三個 logger（清掉它們自己的 handler、改由 root 輸出），否則同一個 stdout
會混著兩種格式。access log 改寫成 `method path HTTP/版本 status`：**去掉查詢字串與對端位址**。

遮罩（`redact`，套在 `message` 與 `exc` 上；text 模式維持原樣不變）：
- 名稱像祕密的環境變數之值、DB 密碼、`scheme://帳號:密碼@`（重用 `app/services/diagnostics.py` 的
  `secret_values`／`scrub`，判準同一份）、`Bearer <token>`；
- URL 的查詢字串（`/path?q=...` → `/path?[redacted]`）；
- SQLAlchemy 錯誤訊息的 `[parameters: ...]`（bind 參數可能是使用者的問題或查詢）。
問答原文與查詢字串本來就不進日誌（`web/request_log.py` 不記 query string、問答只記耗時與旗標）；
遮罩是防例外字串這類不是我們逐字組出來的文字。
"""

from __future__ import annotations

import json
import logging
import logging.config
import os
import re
import time
from datetime import datetime, timezone

_CONFIGURED = False

# `rid=`：發起這一行的 HTTP 請求（見 app/request_context.py）；批次與啟動期為 "-"。
_FORMAT = "%(asctime)s %(levelname)s %(name)s rid=%(request_id)s %(message)s"


class RequestIdFilter(logging.Filter):
    """把目前請求的關聯 id 蓋到每一筆 record 上。

    掛在 **handler** 而不是 logger：logger 的 filter 只作用於「直接對那個 logger 呼叫」的
    record，經 propagate 冒上來的不會過 root 的 filter；handler 的 filter 則每一筆都過。
    format 裡用了 `%(request_id)s`，漏蓋任何一筆都會在格式化時 KeyError。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        from app.request_context import current_request_id

        record.request_id = current_request_id()
        return True


REDACTED = "[redacted]"
_QUERY_STRING = re.compile(r"""(/[^\s?#"'<>]*)\?[^\s"'<>#]+""")
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_SQL_PARAMETERS = re.compile(r"\[parameters: [^\n]*")
# 祕密值清單的重算間隔（秒）：環境變數在行程內幾乎不變，每一行都掃一次 os.environ 沒有必要。
_SECRETS_TTL = 30.0
_secrets_cache: tuple[float, list[str]] = (0.0, [])


def _secret_values() -> list[str]:
    global _secrets_cache
    now = time.monotonic()
    at, values = _secrets_cache
    if values and now - at < _SECRETS_TTL:
        return values
    try:
        from app.services.diagnostics import secret_values

        values = secret_values(os.environ, db_url=os.environ.get("REPORT_MARK_DB_URL"))
    except Exception:  # noqa: BLE001 — 遮罩不能讓日誌整行消失；退回只做 regex 那幾項
        values = []
    _secrets_cache = (now, values)
    return values


def redact(text: str) -> str:
    """JSON 日誌的遮罩（模組 docstring「遮罩」）。純字串函式，供測試直接呼叫。"""
    if not text:
        return text
    try:
        from app.services.diagnostics import scrub

        text = scrub(text, _secret_values())
    except Exception:  # noqa: BLE001
        pass
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _SQL_PARAMETERS.sub(f"[parameters: {REDACTED}]", text)
    return _QUERY_STRING.sub(rf"\1?{REDACTED}", text)


def _access_message(record: logging.LogRecord) -> str | None:
    """uvicorn access log（args＝client, method, full_path, http_version, status）改寫成不含對端與查詢字串的一行。"""
    args = record.args
    if record.name != "uvicorn.access" or not isinstance(args, tuple) or len(args) != 5:
        return None
    _client, method, full_path, http_version, status = args
    path = str(full_path).split("?", 1)[0]
    return f"{method} {path} HTTP/{http_version} {status}"


class JsonFormatter(logging.Formatter):
    """一行一個 JSON 物件；欄位白名單見模組 docstring。"""

    FIELDS = ("ts", "level", "logger", "request_id", "message")

    def format(self, record: logging.LogRecord) -> str:
        message = _access_message(record)
        if message is None:
            try:
                message = record.getMessage()
            except Exception:  # noqa: BLE001 — 格式字串與參數對不上：寧可記原樣，也不要整行消失
                message = str(record.msg)
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc)
        out = {
            "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": redact(message),
        }
        if record.exc_info:
            exc = record.exc_info[1]
            out["exc_type"] = type(exc).__name__ if exc is not None else None
            out["exc"] = redact(self.formatException(record.exc_info))
        if record.stack_info:
            out["stack"] = redact(self.formatStack(record.stack_info))
        return json.dumps(out, ensure_ascii=False)


def logging_config(level: str, fmt: str = "text") -> dict:
    """回傳 dictConfig 內容（純函式，供測試直接檢查）。fmt＝`text`（stderr，現行格式）或 `json`（stdout）。"""
    if fmt == "json":
        return {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "filters": {"request_id": {"()": RequestIdFilter}},
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                    "formatter": "json",
                    "filters": ["request_id"],
                }
            },
            "root": {"handlers": ["stdout"], "level": level},
        }
    return {
        "version": 1,
        # uvicorn 的 logger 已在本設定之前建立；False 讓它們維持原狀。
        "disable_existing_loggers": False,
        "formatters": {"standard": {"format": _FORMAT}},
        "filters": {"request_id": {"()": RequestIdFilter}},
        "handlers": {
            "stderr": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stderr",
                "formatter": "standard",
                "filters": ["request_id"],
            }
        },
        # 只補 root——不列 uvicorn.access / uvicorn.error，避免蓋掉它們的既有設定。
        "root": {"handlers": ["stderr"], "level": level},
    }


def _route_uvicorn_to_root() -> None:
    """JSON 模式：uvicorn 的 logger 改由 root 的 JSON handler 輸出（模組 docstring）。

    刻意不寫進 dictConfig 的 `loggers` 鍵（`tests/test_logging_setup.py` 擋：text 模式宣告它們會蓋掉 access log）；
    這裡只在 JSON 模式、於 uvicorn 已套用自己的設定之後動它們。
    """
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True


def configure_logging(level: str | None = None, fmt: str | None = None) -> None:
    """套用設定（冪等；重複呼叫不會疊加 handler）。

    level 未給時取 `app.config` 的 `log_level`（環境變數 `LOG_LEVEL`，預設 INFO）；fmt 未給時取 `log_format`
    （`LOG_FORMAT`，預設 text）。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    if level is None or fmt is None:
        from app.config import get_settings

        settings = get_settings()
        level = level or settings.log_level
        fmt = fmt or settings.log_format
    logging.config.dictConfig(logging_config(level, fmt))
    if fmt == "json":
        _route_uvicorn_to_root()
    # httpx 在 INFO 會為每個請求記一行 `HTTP Request: POST <url>`：LLM 呼叫已有自己的
    # `llm_call` 結構化行（app/services/llm_http.py），這一行只是重複的噪音。刻意用
    # getLogger 設定而不寫進 dictConfig：`loggers` 鍵會被 tests/test_logging_setup.py 擋下
    # （宣告 logger 容易順手蓋掉 uvicorn 的設定）。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True


def _reset_for_tests() -> None:
    """僅供測試：清掉冪等旗標，讓下一次 configure_logging 會真的套用。"""
    global _CONFIGURED
    _CONFIGURED = False
