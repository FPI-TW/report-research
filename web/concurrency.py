# web/concurrency.py
"""行程內併發閘門，以及「本服務只能單 worker」這個假設的守門。

## 為什麼需要守門

`/api/ask` 的併發上限是**模組級的 asyncio.Semaphore**，也就是
per-process 狀態。目前 `deploy/systemd/report-mark-web.service` 的 ExecStart 沒有
`--workers`，所以「一個行程＝一個上限」成立——但這個前提在此之前只寫在註解裡。
任何人為了吞吐量加上 `--workers 2`，同時會發生三件事，而且三件都不會有錯誤訊息：

* 實際併發上限翻倍（問答 3→6），而上限本來就是照單機 CPU 抓的。
* BGE-M3 與 cross-encoder 是 per-process 常駐，每個 worker 各載一份 → 記憶體翻倍。
* 問答忠實度抽查的背景任務上限（`ASK_FAITHFULNESS_MAX_INFLIGHT`）同樣是行程內
  狀態，多 worker 會讓同時的 judge 請求數翻倍。

所以這裡選擇 fail-closed：偵測得到多 worker 就拒絕啟動，讓改動的人當場看到原因，
而不是三個月後在「機器怎麼變慢了」裡回推。

## 偵測方式與它的邊界（重要）

**只在能正面讀到一個大於 1 的 worker 數時才擋**，讀不到就放行並記一行 log。來源：

* `sys.argv` 的 `--workers N` / `--workers=N`（uvicorn CLI）。多 worker 時 uvicorn
  走 multiprocessing spawn（`uvicorn/_subprocess.py`），而
  `multiprocessing.spawn.prepare()` 會把父行程的 `sys.argv` 原封還原到子行程
  （CPython `multiprocessing/spawn.py`：`sys_argv=sys.argv` → `sys.argv = data['sys_argv']`），
  所以**父行程與每個 worker 讀到的是同一份 argv**，守門在哪個行程跑都成立。
* `WEB_CONCURRENCY`：uvicorn `Config` 只在未指定 `--workers` 時才採用它，故這裡的
  優先序刻意相同（argv 有值就不看環境變數）。
* gunicorn 的 `-w` / `--workers`，以及 `GUNICORN_CMD_ARGS`。

刻意**不用** `multiprocessing.parent_process() is not None` 當判準：`--reload` 也走
子行程，那是單 worker 的開發模式，用它會把開發模式誤判成多 worker 直接擋掉。
也讀不到 gunicorn 設定檔（`-c gunicorn.conf.py`）裡的 workers——那要執行使用者的
設定檔，成本與風險不對等。這兩條是已知缺口，寫在這裡是為了不要被當成「已經全包」。
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)


def _to_int(text: str) -> int | None:
    try:
        return int(text.strip())
    except (TypeError, ValueError):
        return None


def _flag_value(tokens: list[str], flags: tuple[str, ...]) -> int | None:
    """從一串命令列 token 裡讀出 `--flag N` 或 `--flag=N` 的數值。讀不到回 None。"""
    for i, tok in enumerate(tokens):
        for flag in flags:
            if tok == flag:
                if i + 1 < len(tokens):
                    return _to_int(tokens[i + 1])
            elif tok.startswith(flag + "="):
                return _to_int(tok[len(flag) + 1 :])
    return None


def detect_worker_count(
    argv: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> int | None:
    """本行程所屬服務被要求跑幾個 worker。**讀不到回 None，不猜。**

    None 與 1 是不同的答案：None 是「這個啟動方式我不認得」，呼叫端據此放行；
    1 是「確定是單行程」。把讀不到當成 1 會讓守門在未知啟動方式下靜默失效，
    把讀不到當成多 worker 則會擋掉合法啟動——所以刻意讓它是三值的。
    """
    argv = list(sys.argv if argv is None else argv)
    env = dict(os.environ if environ is None else environ)

    is_gunicorn = bool(argv) and "gunicorn" in os.path.basename(argv[0]).lower()
    flags: tuple[str, ...] = ("--workers", "-w") if is_gunicorn else ("--workers",)

    explicit = _flag_value(argv[1:], flags)
    if explicit is None and is_gunicorn:
        explicit = _flag_value(env.get("GUNICORN_CMD_ARGS", "").split(), flags)
    if explicit is not None:
        return explicit

    # uvicorn 只在 --workers 未指定時才看 WEB_CONCURRENCY（uvicorn/config.py），照抄該優先序。
    concurrency = env.get("WEB_CONCURRENCY")
    if concurrency is not None:
        return _to_int(concurrency)
    return None


def assert_single_worker(
    argv: list[str] | None = None,
    environ: dict[str, str] | None = None,
) -> int | None:
    """多 worker 就拒絕啟動；回傳偵測到的 worker 數（None＝未偵測到）。"""
    workers = detect_worker_count(argv, environ)
    if workers is not None and workers > 1:
        raise RuntimeError(
            f"偵測到 {workers} 個 worker，但本服務的併發上限（/api/ask）與"
            "背景抽查上限都是行程內狀態，多 worker 會讓上限翻倍、模型記憶體翻倍。"
            "要提高吞吐請改動 web/concurrency.py 所述的設計，"
            "不要加 --workers。"
        )
    return workers


# 已建立的閘門，供啟動時把有效上限印出來（新增閘門會自動被涵蓋，不必再改 server.py）。
_GATES: list["ConcurrencyGate"] = []


def registered_gates() -> list["ConcurrencyGate"]:
    return list(_GATES)


class ConcurrencyGate:
    """帶「排隊可見性」的併發名額閘門。

    比裸 `asyncio.Semaphore` 多兩件事，兩件都是為了同一個症狀——**回應已經回了 200
    才開始排隊**（`async with semaphore` 寫在 async generator 內部，而 generator 要等
    StreamingResponse 開始串流才跑），於是第 4 個之後的使用者看到的是「連線建立但
    永遠沒有 token」，與伺服器卡死完全無法分辨：

    1. 知道現在有幾個人在排隊 → SSE 上先送一個 `queued` 事件，畫面才有話可說。
    2. 可選的排隊長度上限 → 滿載時能在**送出 200 之前**用 429 擋掉，而不是讓人在一條
       已經開好的 SSE 上等半小時。

    刻意**不支援** `async with`：舊寫法 `async with _ASK_SEMAPHORE` 直接套到本類別會
    是 AttributeError（大聲失敗），而不是靜默繞過排隊事件。
    """

    def __init__(self, capacity: int, *, name: str, max_queue: int = 0) -> None:
        self.name = name
        self.capacity = max(1, int(capacity))
        self.max_queue = max(0, int(max_queue))
        self._sem = asyncio.Semaphore(self.capacity)
        self._waiting = 0
        _GATES.append(self)

    @property
    def waiting(self) -> int:
        """目前卡在 acquire() 的請求數（不含已取得名額者）。"""
        return self._waiting

    def would_queue(self) -> bool:
        """現在取名額會不會排隊。

        回 False 之後**必須立刻 await acquire()、中間不可有任何其他 await**：兩者之間
        沒有暫停點時，`Semaphore.acquire()` 在未鎖住的情況下是同步取值、不會讓出事件
        迴圈，所以這個判斷不可能在中途過期。中間插入 await（例如先 yield 一個事件）
        就沒有這個保證了。
        """
        return self._sem.locked()

    def queue_full(self) -> bool:
        """排隊人數已達上限（`max_queue=0` ＝不限）。

        供 handler 在**回應送出之前**判斷是否直接回 429——SSE 一旦送出 200 就改不了
        status code，這是唯一能回 429 的位置。

        這個數字是近似值：計數在 generator 內才遞增，而 generator 要等回應開始串流才
        跑，所以同時湧入的一批請求可能全部通過檢查。**它是洩壓閥不是配額器**，不要
        拿它當精確限流用。
        """
        return self.max_queue > 0 and self._waiting >= self.max_queue

    def queue_event(self) -> dict[str, object]:
        """`queued` 事件的 payload。position 是「你排第幾個」，取事件當下的估計值。"""
        return {
            "scope": self.name,
            "position": self._waiting + 1,
            "capacity": self.capacity,
        }

    async def acquire(self) -> float:
        """取得一個名額，回傳實際等待秒數。必須與 `release()` 以 try/finally 成對。"""
        started = time.monotonic()
        self._waiting += 1
        try:
            await self._sem.acquire()
        finally:
            # 取消（使用者斷線時 generator 被 aclose）也要還原計數。漏了的話排隊上限會
            # 越縮越小，最後所有請求永遠 429——一個只在生產長時間累積後才發作的洩漏。
            self._waiting -= 1
        return time.monotonic() - started

    def release(self) -> None:
        self._sem.release()

    def describe(self) -> str:
        return (
            f"{self.name}(capacity={self.capacity}, "
            f"max_queue={self.max_queue if self.max_queue else '不限'})"
        )
