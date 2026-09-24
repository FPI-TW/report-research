"""每日簡報批次 → research.report_brief（一天一列；閱讀時零 LLM）

素材收集、窗期界定與變動判定全在 app/services/brief.py（純 SQL ＋ 純函式），本檔
只負責編排：取鎖 → 決定要不要跑 → 一次 `claude -p` → 落庫。

**一天只有一次 LLM 呼叫**，這是刻意的成本設計：摘要（覆蓋率 100%）與訊號都已經
批次產好，簡報要做的只是把既有素材組織成一段話，不需要重讀任何全文。

## 冪等與排程

沒有自己的 systemd timer——`scripts/sync_new_reports.sh` 每輪都會叫它，由本檔自己
判斷「今天要不要跑」：

- 當日已有列 → 直接 no-op 退出（唯一的冪等依據是 report_brief 的 UNIQUE(brief_date)）
- 本地時間未到 --after-hour（預設 9 時）→ 不跑。券商早報上午才進來，太早跑的簡報
  會漏掉當天大半內容，而一天只寫一列、寫了就不會再改
- 窗期內既無新研報也無評等變動 → **不寫列**，下一輪再看。寫一列「今天沒事」會讓
  當天稍晚真的有素材時再也補不上（UNIQUE 擋著），而「今天沒事」對讀者沒有價值

窗期是「上一份簡報的 window_end → 現在」，故不會有縫；沒有上一份時取最近 24 小時
（另有 --max-lookback-days 上限，避免久未執行後一次把幾週的東西全塞進 prompt）。

用法：
  uv run python scripts/generate_brief.py --dry-run     # 只印素材與判斷，不呼叫 LLM
  uv run python scripts/generate_brief.py               # 排程走的就是這條
  uv run python scripts/generate_brief.py --force       # 忽略時間閘與既有列，重寫當日
  uv run python scripts/generate_brief.py --date 2026-08-05

退出碼：0 正常（含「今天不用跑」）、1 產生失敗、2 模型設定或 LLM 帳號層級錯誤（未知模型名、
缺金鑰、DeepSeek 401／402／模型不存在、claude CLI 認證失效）、75 claude CLI 被其他批次佔用。
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
import uuid
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts._llm_env import load_llm_env, require_llm_key  # noqa: E402

# 必須在任何其他專案 import 之前：db.py 與各模型常數都在 import 期讀環境（scripts/_llm_env.py）。
load_llm_env()

from app.services import brief as brief_service  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402
from app.services.llm_models import TASK_BRIEF, is_http_model, resolve_model  # noqa: E402
from app.services.reading.queries import fetch_instrument_names  # noqa: E402
from app.services.zh_hant import to_traditional  # noqa: E402
from scripts._claude_cli import (  # noqa: E402
    CliNotFoundError,
    cli_auth_error,
    error_kind,
    record_usage,
    run_claude,
)
from scripts._claude_lock import claude_cli_lock_or_exit  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
FAIL_LOG = ROOT / "data" / "brief_failures.log"
# BRIEF_MODEL 旋鈕，未設時查 LLM_PROVIDER 的預設表（app/services/llm_models.py）；--model 可覆寫。
MODEL = resolve_model(TASK_BRIEF)

# 一次呼叫的逾時。素材是摘要不是全文，正常在一分鐘內回；給 300s 是留給 CLI 冷啟動
# 與偶發的長素材（NAS 一次倒進大量檔案的日子）。
CLI_TIMEOUT = 300

# 走 DeepSeek 時的輸出上限（第二版計畫 §8；CLI 路徑不讀）。
MAX_TOKENS = 8192

# 沒有前一份簡報時的預設回看窗，以及任何情況下的回看上限。
DEFAULT_LOOKBACK_HOURS = 24
MAX_LOOKBACK_DAYS = 7

# 券商早報多在上午進來；預設等到這個鐘點之後才產生當日簡報。
DEFAULT_AFTER_HOUR = 9


def build_cli_args(prompt: str, model: str) -> list[str]:
    """組 `claude -p` 的 argv（對齊其他批次）。

    `--setting-sources ""`＝不載入任何 settings 來源，連帶略過全域 hooks/plugins/
    CLAUDE.md——每次冷啟動載入它們正是磁碟小檔 I/O 的主因。輸出用 CLI 預設純文字：
    **不要加 `--output-format json`**，那會把回應包進一層 envelope。
    `--tools ""` 不開任何工具、`--strict-mcp-config` 不載任何 MCP，理由與位置限制同
    scripts/_claude_cli.py。
    """
    return ["claude", "-p", prompt.replace("\x00", ""), "--model", model,
            "--setting-sources", "", "--strict-mcp-config", "--tools", ""]


def call_cli(prompt: str, model: str, timeout: int = CLI_TIMEOUT) -> tuple[Optional[str], Optional[str]]:
    """呼叫 LLM，回 (text, None) 或 (None, 可辨識的失敗原因)。

    失敗原因必須分得出來：`claude` 不在 PATH（systemd 缺 PATH drop-in）與「這次逾時」
    的處置完全不同，寫成同一句「CLI 無回應」等於把環境問題偽裝成偶發失敗。

    DeepSeek 白名單的 model 交給 `scripts/_claude_cli.run_claude` 的 HTTP 路徑（與其他批次同一套
    錯誤分類：失敗回 `API[<kind>] …`，401／402／模型不存在拋 `LlmEnvironmentError`，main 以 rc=2
    收場）。CLI 路徑刻意**不**改用 `run_claude`：本檔的 CLI 版把 claude 不在 PATH 當成「這一天產生
    失敗」（rc=1），`run_claude` 會拋 `CliNotFoundError`（rc=2）——換過去會改變生產行為（rc=2 讓排程
    殼另外保留 hashes），不在這次遷移的範圍。

    **例外是 claude CLI 認證失效**（OAuth 過期等，`_claude_cli.cli_auth_error`）：拋 `LlmEnvironmentError`，
    main 以 rc=2 收場、不寫 brief_failures.log——與 DeepSeek 401 同一類（rc=2 本來就是「LLM 帳號層級
    錯誤」），不是「這一天產生失敗」。2026-09-23 起 CLI 永久停用，這時真正要修的通常是環境檔沒讓本段
    解析到 DeepSeek；記成 rc=1 的單日失敗會把它藏進每日雜訊裡。
    """
    if is_http_model(model):
        res = run_claude(prompt, model, timeout=timeout, max_tokens=MAX_TOKENS, meta={"task": TASK_BRIEF})
        return res.text, res.error
    t0 = time.monotonic()
    try:
        raw, error = _spawn_cli(prompt, model, timeout)
    except CliNotFoundError:
        record_usage(meta={"task": TASK_BRIEF}, backend="cli", model_req=model, prompt=prompt, kind="auth",
                     total_ms=int((time.monotonic() - t0) * 1000))
        raise
    # 用量記錄 CLI 路徑也寫（tokens 為 null），與 run_claude 同一份檔、同一組欄位
    kind = None if error is None else "timeout" if error.startswith("CLI 逾時") else "cli_error"
    record_usage(meta={"task": TASK_BRIEF}, backend="cli", model_req=model, prompt=prompt, kind=kind,
                 total_ms=int((time.monotonic() - t0) * 1000))
    return raw, error


def _spawn_cli(prompt: str, model: str, timeout: int) -> tuple[Optional[str], Optional[str]]:
    """spawn `claude -p`（簡報自己的 CLI 版，遷移前的 call_cli 本體；認證失效改為拋出，見 call_cli）。"""
    try:
        proc = subprocess.run(
            build_cli_args(prompt, model),
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd="/tmp",  # 避免載入專案 CLAUDE.md
        )
    except FileNotFoundError:
        return None, "`claude` CLI 不在 PATH（systemd 下請補 PATH drop-in）"
    except subprocess.TimeoutExpired:
        return None, f"CLI 逾時（{timeout}s 內未回應）"
    except Exception as exc:  # noqa: BLE001 - 失敗原因要能寫進 log
        return None, f"CLI 呼叫失敗：{type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        auth = cli_auth_error(proc.stdout, proc.stderr)
        if auth is not None:
            raise auth
        tail = (proc.stderr or "").strip().replace("\n", " ")[-200:]
        return None, f"CLI 退出碼 {proc.returncode}：{tail or '（無 stderr）'}"
    return proc.stdout, None


def record_failure(target: date_cls, reason: str, model: str = "") -> None:
    """`時間<TAB>簡報日期<TAB>原因<TAB>model` 一行。原因去掉 TAB／換行（欄位不能被切開）。

    第四欄 model 是審查低4 補的：`blocked_today` 據此判斷「今天這個 model 已被內容審查擋過」。
    """
    FAIL_LOG.parent.mkdir(parents=True, exist_ok=True)
    reason = " ".join(str(reason).split())
    with FAIL_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now(timezone.utc).isoformat()}\t{target}\t{reason}\t{model}\n")


def blocked_today(target: date_cls, model: str) -> bool:
    """`brief_failures.log` 裡同一個簡報日期、同一個 model 已有內容審查的紀錄 → True（審查低4）。

    簡報每輪 sync 都會被叫；被審查擋下時不寫列，下一輪窗期只是再往後延、素材是上一次的超集，
    幾乎一定再被擋——每 3 小時重打一次、每次付一次錢、每次再進告警鏈。同一天同一個 model 擋過
    就不再呼叫（rc 仍是 1，讓「今天沒有簡報」照樣看得見）；隔天、換 model、或 `--force` 會再試。
    讀不到檔一律當作沒有（照打）：這是省錢的閘，不是正確性的一部分。舊格式（三欄、沒有 model）
    的行不算。
    """
    try:
        lines = FAIL_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        cols = line.split("\t")
        if len(cols) >= 4 and cols[1] == str(target) and cols[3] == model and error_kind(cols[2]) == "content_filter":
            return True
    return False


def resolve_window(
    previous_end: Optional[datetime],
    now: datetime,
    max_lookback_days: int = MAX_LOOKBACK_DAYS,
) -> tuple[datetime, datetime]:
    """窗期＝上一份簡報的結尾 → 現在（無縫接續），但不回看超過上限。

    純函式：時間邏輯是這支腳本唯一容易錯又完全無聲的地方（錯了只是簡報少幾篇，
    沒有任何錯誤訊息），所以抽出來單測。
    """
    floor = now - timedelta(days=max_lookback_days)
    if previous_end is None:
        start = now - timedelta(hours=DEFAULT_LOOKBACK_HOURS)
    else:
        start = previous_end
    return max(start, floor), now


async def generate(args) -> int:
    now = datetime.now(timezone.utc)
    target = (
        date_cls.fromisoformat(args.date) if args.date else datetime.now().date()
    )

    if not args.force and datetime.now().hour < args.after_hour:
        print(
            f"[brief] 本地時間未到 {args.after_hour} 時 → 今天先不產生"
            "（券商早報多在上午進來）"
        )
        return 0

    async with SessionFactory() as session:
        existing = await brief_service.fetch_by_date(session, target)
        if existing and not args.force:
            print(f"[brief] {target} 已有簡報（{existing.report_count} 篇）→ no-op")
            return 0

        latest = await brief_service.fetch_latest(session)
        previous_end = latest.window_end if latest and latest.brief_date < target else None
        start, end = resolve_window(previous_end, now, args.max_lookback_days)

        reports = await brief_service.fetch_window_reports(session, start, end)
        total = await brief_service.count_window_reports(session, start, end)
        changes = await brief_service.fetch_signal_changes(session, start, end)
        if changes:
            names = await fetch_instrument_names(
                session, [(c.market, c.instrument_code) for c in changes]
            )
            changes = brief_service.with_instrument_names(changes, names)

    material = brief_service.build_material(reports, changes, total_reports=total)
    print(
        f"[brief] {target}｜窗期 {start.isoformat()} → {end.isoformat()}"
        f"｜研報 {total} 篇（入 prompt {len(reports)}）｜變動 {len(changes)} 筆"
    )

    if not reports and not changes:
        # 刻意不寫列：見模組 docstring。稍晚有素材時這一輪還補得回來。
        print("[brief] 窗期內無新研報也無評等變動 → 不寫列，下一輪再看")
        return 0

    if args.dry_run:
        print("─" * 60)
        print(material)
        return 0

    if not args.force and blocked_today(target, args.model):
        print(
            f"[brief] {target} 今日已被模型供應商的內容審查擋過（model={args.model}），略過、不再呼叫"
            "（要重試加 --force；處置見 docs/production_resilience.md「DeepSeek 批次的失敗處置」）",
            file=sys.stderr,
        )
        return 1

    prompt = brief_service.build_prompt(target, material)
    # **鎖只包住 CLI 呼叫本身**：排程每 3 小時叫本檔一次，但真正要呼叫 LLM 的只有
    # 一天一次。若照其他批次的慣例在 main 進入點取鎖，其餘七次 no-op 都會在訊號或
    # 標題批次執行中撞鎖 rc=75，於是 unit_failures 每天多七筆「失敗」——那個檔是
    # OnFailure 告警的落點，灌滿雜訊等於把它廢掉。
    with claude_cli_lock_or_exit("generate_brief"):
        raw, error = call_cli(prompt, args.model)
    if error:
        record_failure(target, error, args.model)
        if error_kind(error) == "content_filter":
            # 內容審查：一律標記、跳過、交人工（不改走 Claude）。不寫列；同一天同一個 model 之後的
            # 輪次由 blocked_today 略過、不再呼叫（隔天以新的窗期再試）。rc=1 讓排程殼記進
            # unit_failures（OnFailure 告警鏈）。
            print(
                f"[brief] {target} 觸發模型供應商的內容審查，本次跳過、不寫列"
                f"（今天不再重試，隔天以新的窗期再試）：{error}",
                file=sys.stderr,
            )
            return 1
        print(f"[brief] 產生失敗：{error}", file=sys.stderr)
        return 1

    markdown = brief_service.parse_brief(raw)
    if not markdown:
        record_failure(target, "模型輸出無法解析為簡報 markdown", args.model)
        print("[brief] 產生失敗：模型輸出無法解析", file=sys.stderr)
        return 1

    # 與其他五個寫入點一致：prompt 的「一律繁體中文」是機率性保證，確定性守門在這裡。
    markdown = to_traditional(markdown)

    async with SessionFactory() as session:
        await brief_service.upsert_brief(
            session,
            brief_id=str(uuid.uuid4()),
            brief_date=target,
            window_start=start,
            window_end=end,
            markdown=markdown,
            report_ids=[r.report_id for r in reports],
            report_count=total,
            signal_count=len(changes),
            model=args.model,
        )
        await session.commit()

    print(f"[brief] {target} 已寫入（{len(markdown)} 字）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="產生哪一天的簡報（預設今天，本地時區）")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument(
        "--after-hour",
        type=int,
        default=DEFAULT_AFTER_HOUR,
        help="本地時間到這個鐘點之後才產生（預設 9；--force 可繞過）",
    )
    ap.add_argument("--max-lookback-days", type=int, default=MAX_LOOKBACK_DAYS)
    ap.add_argument("--force", action="store_true", help="忽略時間閘與既有列，重寫當日")
    ap.add_argument("--dry-run", action="store_true", help="只印素材，不呼叫 LLM、不寫庫")
    args = ap.parse_args()
    # 模型與金鑰預檢排在 generate() 之前，也就在取鎖之前；--dry-run 不呼叫 LLM，不檢查。
    if not args.dry_run:
        require_llm_key({TASK_BRIEF: args.model})
    # 取鎖的位置在 generate() 內、只包住 CLI 呼叫（理由見該處註解）。
    try:
        return asyncio.run(generate(args))
    except CliNotFoundError as exc:
        # 環境／設定層級：不寫 brief_failures.log（那是「這一天產生失敗」的紀錄），以 rc=2 讓
        # 排程殼記進 unit_failures（與其他批次的 CliNotFoundError 同一個退出碼）。
        print(f"[brief] 中止：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
