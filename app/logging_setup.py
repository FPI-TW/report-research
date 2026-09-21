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
"""

from __future__ import annotations

import logging
import logging.config

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


def logging_config(level: str) -> dict:
    """回傳 dictConfig 內容（純函式，供測試直接檢查）。"""
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


def configure_logging(level: str | None = None) -> None:
    """套用設定（冪等；重複呼叫不會疊加 handler）。

    level 未給時取 `app.config` 的 `log_level`（環境變數 `LOG_LEVEL`，預設 INFO）。
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    if level is None:
        from app.config import get_settings

        level = get_settings().log_level
    logging.config.dictConfig(logging_config(level))
    _CONFIGURED = True


def _reset_for_tests() -> None:
    """僅供測試：清掉冪等旗標，讓下一次 configure_logging 會真的套用。"""
    global _CONFIGURED
    _CONFIGURED = False
