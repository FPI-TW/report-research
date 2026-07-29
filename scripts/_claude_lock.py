"""claude CLI 的跨進程互斥鎖——**只給 scripts/ 底下的離線批次用**。

為什麼需要它
────────────
`claude` CLI 是跨進程共用資源，本 repo 有數支批次會 spawn 它（標註、摘要、標題、
摘錄、訊號擷取，以及增量匯入時的行內標註；權威清單見 tests/test_claude_lock.py
的 LOCKED_SCRIPTS）。多支同時跑會互搶，而症狀不是「壞掉」而是
**擷取被大量誤標 rejected**——資料沒壞、模型也沒壞，只是 CLI 被搶（2026-07 的實際
事故）。更麻煩的是它已經不只由人手動觸發：`report-mark-sync.timer` 每 3 小時跑
「增量匯入 → 摘要 → 摘錄」，此時有人手動敲 `make signals` 就撞車。光靠文件警語擋
不住排程，所以把規約機械化成鎖。

app/services/llm.py 絕對不可以取這個鎖
──────────────────────────────────────
那是 web 線上路徑（`/api/ask`、研報生成）的同一個 spawn 點。把它納入這個鎖，一輪
`tag_all_cli`（數小時）就會把線上問答整個鎖死——把「批次跑得慢一點」換成「服務中斷
數小時」。鎖的邊界刻意畫在「離線批次之間」，不是「所有 claude 呼叫」：線上路徑寧可
與批次互搶（頂多慢、頂多重試），也不能被批次擋在門外。`tests/test_claude_lock.py`
有一條靜態測試釘住這件事，避免日後有人「順手把 llm.py 也納進來」。

為什麼是 flock，不是 PID 檔 + kill -0
──────────────────────────────────────
`scripts/sync_new_reports.sh` 與 `scripts/resume_corpus.sh` 現有的自我重入鎖走的是
PID 檔模式，弱點是行程被 SIGKILL（OOM、逾時強殺、CI 取消）時鎖檔會留下來；更糟的是
PID 若被回收給另一個無關行程，`kill -0` 會成功，鎖就永遠解不開。`flock` 由 kernel
綁在開啟檔案描述子上，**行程無論怎麼死都會自動釋放**，殘留的鎖檔本身無害。代價是
鎖只在同一台主機有效（NFS 上語意不保證），而這些批次本來就只跑在這一台。

已實測 flock 在本專案所在的 `/mnt/c`（9p/drvfs）上真的有效，同進程異 fd 與跨進程
都會擋下——這件事不能想當然爾，部分網路檔案系統會讓 flock 退化成 no-op。

兩個講清楚的限制
────────────────
1. **鎖檔路徑相對於本檔所在的 repo**（`parents[1]/data/.claude_cli.lock`），所以從
   git worktree 跑批次不會與主 checkout 互斥。生產只從主 checkout 跑，實務上沒差；
   但要在 worktree 跑長批次前，請當作沒有鎖。
2. **payload 只是診斷資訊，不是鎖本身。** 持有者被強殺時來不及清空鎖檔，會留下一筆
   指向已結束行程的 payload；此時 kernel 早已放掉 flock，鎖是好的。`_read_holder`
   ＋ `_pid_alive` 因此會把這種殘留標示為「已結束」而不是當成持有者印出去。

逃生口
──────
設環境變數 `CLAUDE_LOCK_DISABLE=1` 可完全繞過取鎖（會在 stderr 印警告）。刻意**不**
放進 `.env.example`：批次是 `uv run python scripts/...` 直接跑、根本不載入 `.env`，
把它寫進去只會養出一個「以為關掉了其實沒關、或以為開著其實永久關掉」的誤解來源。
它是給「明知對方不會呼叫 CLI、但就是想跑」的當下用的一次性旗標。
"""

from __future__ import annotations

import errno
import fcntl
import json
import os
import sys
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "data" / ".claude_cli.lock"

DISABLE_ENV = "CLAUDE_LOCK_DISABLE"

# sysexits.h 的 EX_TEMPFAIL（暫時性失敗、稍後重試）。刻意不用 1：排程殼要能把
# 「CLI 被別的批次佔用」與「這支批次自己壞了」分開處理與呈報，混用 1 就分不出來。
EXIT_LOCK_BUSY = 75

_TRUTHY = {"1", "true", "yes", "on"}


class ClaudeCliBusyError(RuntimeError):
    """另一支批次正持有 claude CLI 鎖，本次不啟動。

    `holder` 是鎖檔裡的 {pid, script, started_at}；讀不到時為 None（見 _read_holder）。
    """

    def __init__(self, lock_path: Path, holder: dict[str, Any] | None) -> None:
        self.lock_path = lock_path
        self.holder = holder
        super().__init__(_busy_message(lock_path, holder))


def _lock_disabled() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in _TRUTHY


def _read_holder(lock_path: Path) -> dict[str, Any] | None:
    """讀鎖檔裡的持有者資訊。任何讀不到／解不出的情況一律回 None。

    取不到鎖的人並不持有鎖，所以這裡是無鎖讀取：可能讀到「對方剛 flock 成功、
    payload 還沒寫完」的空檔（視窗極小）。持有者資訊純粹是給人看的診斷，
    讀不到不影響「不准跑」這個結論，因此全程容錯而非拋錯。
    """
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _pid_alive(pid: Any) -> bool:
    """判斷 payload 裡記的 pid 是否還在。純粹用於「這筆診斷資訊可不可信」。

    PID 有回收問題，所以這個結果**不可**拿來當「鎖是否被持有」的依據——那個問題
    只有 flock 本身能回答，而這正是不採 PID 檔的理由。
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 行程存在，只是不屬於我們
    except OSError:
        return False
    return True


def _busy_message(lock_path: Path, holder: dict[str, Any] | None) -> str:
    if holder and _pid_alive(holder.get("pid")):
        who = holder.get("script") or "未知批次"
        detail = f"{who}（pid={holder.get('pid', '?')}，自 {holder.get('started_at') or '未知時間'} 起）"
    elif holder:
        # 讀到的 payload 屬於一個已經不在的行程。實測情境：上一個持有者被 SIGKILL/SIGTERM
        # 強殺，來不及清 payload；kernel 早已放掉 flock，此刻真正持有鎖的是「剛搶到、
        # payload 還沒寫完」的另一支批次。把死掉的 pid 當成持有者印出去只會誤導排查。
        detail = f"另一支批次（鎖檔殘留的是已結束的 {holder.get('script') or '批次'} pid={holder.get('pid', '?')}）"
    else:
        detail = "另一支批次（持有者資訊讀不到）"
    return (
        f"claude CLI 正被 {detail} 佔用，本次不啟動。\n"
        "多支批次併發搶 claude CLI 會讓擷取被大量誤標 rejected（資料與模型都沒壞，"
        "是 CLI 被搶），所以這裡選擇不跑，而不是跑出一批壞資料。\n"
        f"等對方結束後直接重跑即可——鎖是 flock，持有者行程一結束（含被 kill）就自動"
        f"釋放，不需要手動刪 {lock_path}。\n"
        f"確定對方不會呼叫 CLI 而要強行執行：設 {DISABLE_ENV}=1。"
    )


def _write_holder(fd: int, owner: str) -> None:
    """把 {pid, script, started_at} 寫進鎖檔，供取不到鎖的人辨識持有者。

    寫失敗刻意吞掉：鎖已經拿到了，診斷資訊寫不進去不該讓整批次倒掉。
    """
    payload = json.dumps(
        {
            "pid": os.getpid(),
            "script": owner,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        ensure_ascii=False,
    )
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload.encode("utf-8"))
        os.fsync(fd)
    except OSError:
        pass


@contextmanager
def claude_cli_lock(owner: str, lock_path: Path | None = None) -> Iterator[Path]:
    """取得 claude CLI 的獨佔權；取不到就拋 ClaudeCliBusyError。

    `owner` 是顯示給人看的批次名（慣例＝腳本檔名去掉 .py）。
    `lock_path` 只給測試覆寫，正式路徑一律用預設的 data/.claude_cli.lock。

    **在批次的 main 進入點取一次就好，不要放進 per-report 迴圈**：迴圈內取放會讓
    兩支批次交錯搶到鎖，等於沒鎖；而且每篇一次 open/flock/fsync 是白付的 I/O。
    """
    path = Path(lock_path) if lock_path is not None else LOCK_PATH

    if _lock_disabled():
        print(
            f"[claude-lock] 警告：{DISABLE_ENV} 已設，{owner} 不取鎖直接執行。"
            "若此刻另一支批次也在跑，兩邊會互搶 claude CLI，擷取可能被大量誤標 rejected。",
            file=sys.stderr,
            flush=True,
        )
        yield path
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            # EACCES/EAGAIN＝別人持有（POSIX 允許兩者擇一）。其他 errno 是真的壞了，
            # 例如檔案系統不支援 flock——那要讓它炸出來，不能默默當成「拿到鎖」。
            if exc.errno not in (errno.EACCES, errno.EAGAIN):
                raise
            raise ClaudeCliBusyError(path, _read_holder(path)) from exc

        _write_holder(fd, owner)
        try:
            yield path
        finally:
            # 先清空 payload 再解鎖：此刻仍持有鎖，不可能有別人已取得鎖而被我們抹掉。
            # 反過來留著陳舊 payload，只會讓下一個取不到鎖的人看到早已結束的持有者。
            try:
                os.ftruncate(fd, 0)
            except OSError:
                pass
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


@contextmanager
def claude_cli_lock_or_exit(owner: str, lock_path: Path | None = None) -> Iterator[Path]:
    """批次入口的統一寫法：取不到鎖就印出持有者並以 EXIT_LOCK_BUSY 結束。

    之所以做成共用 helper 而不是讓每支各寫一份 try/except：本專案已經在四份研報
    prompt 上吃過「手抄平行副本、改一處漏三處」的虧，退出碼與訊息格式屬同一類。
    """
    with ExitStack() as stack:
        try:
            path = stack.enter_context(claude_cli_lock(owner, lock_path))
        except ClaudeCliBusyError as exc:
            print(f"[claude-lock] {exc}", file=sys.stderr, flush=True)
            raise SystemExit(EXIT_LOCK_BUSY) from exc
        yield path
