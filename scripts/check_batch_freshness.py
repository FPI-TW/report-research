"""批次停更偵測器：純 SQL 量各派生資產的「最新產出」，過期就以非零退出。

════════════════════════════════════════════════════════════════════════
為什麼需要一支獨立的偵測器（不能靠 systemd 的 OnFailure）
════════════════════════════════════════════════════════════════════════
`scripts/sync_new_reports.sh` 刻意把摘要／標題／摘錄三段設成 best-effort
（`|| RC=$?` 之後只 `log` 一行、不 `exit`）。**那個設計本身是對的**——摘要失敗
不該擋住下一輪匯入。代價是這三段連續失敗永遠不會讓 unit 進 `failed`，於是
`OnFailure=` 一次都不會觸發。

實際後果：2026-07 量到 takeaway 停更 8 天、signal 停更 12 天，而覆蓋率量測是
事故**之後**才補上的。停更的症狀是閱讀頁優雅降級、整區不進 DOM——「最新研報
靜默少一個功能」，沒有人會回報。

所以這裡量的是**結果**而不是過程：不管是鎖撞了、`claude` 不在 PATH、timer 沒
跑、還是 NAS 沒掛上，只要派生資產不再前進就會紅。

════════════════════════════════════════════════════════════════════════
兩個刻意的設計
════════════════════════════════════════════════════════════════════════
**1. 語料閘（`corpus`）用來抑制連鎖假警報。**
三支批次都只吃「本輪新入庫」的研報，所以沒有新研報時它們一行都不會產出——那是
正確行為，不是故障。因此當語料本身在同一個窗期內沒有前進時，派生資產的過期一律
判為 `suppressed`。少了這一層，一個長假就會讓三個資產同時亮紅。

**2. `signal` 的預設門檻是 0（不告警）——它的停更在原理上無法與正常區分。**
訊號只來自高覆蓋子集（2026-07-17 實測 99/14,575＝0.68%）。排程（每 3 小時
`extract_signals --limit 15`）把積壓跑完之後，`max(created_at)` 就不再前進，而那與
「這段時間沒有合格研報」**完全無法區分**——設任何門檻都會在積壓耗盡的那天開始每日
假警報，而永遠紅的告警會在兩週內被當成背景噪音（本專案已有這個教訓）。

真正該偵測的「排程壞了」走另一條路：sync 殼對 `extract_signals` 非零退出會呼叫
`record_unit_failure`，`/api/progress` 的 `unit_failures` 讀它。也就是**這個資產靠
失敗記錄而非新鮮度**——`tests/test_batch_freshness.py` 有一條測試釘住那個記錄呼叫，
拿掉它訊號就變成完全沒有偵測。

新鮮度仍然會印出來，只是不當成失敗。真的想開就 `--signal-days 14`（但先想清楚
積壓耗盡後怎麼辦）。

════════════════════════════════════════════════════════════════════════
刻意不做
════════════════════════════════════════════════════════════════════════
- 不呼叫 LLM、不載入 embedding 模型、不寫任何表（只有一次四個 `max()` 的查詢）。
- 不回報「跑到哪」。那次事故的失效模式是「停了沒人知道」，不是「不知道進度」；
  統一 heartbeat 檔是另一個題目，不夾帶進來。
- 不自己送通知。unit 宣告 `OnFailure=report-mark-alert@%n.service` 就接上既有的
  告警鏈（journal ERROR ＋ `data/unit_failures.log` ＋ opt-in webhook），零新管道。

用法：
  uv run python scripts/check_batch_freshness.py
  uv run python scripts/check_batch_freshness.py --takeaway-days 5 --signal-days 14
  uv run python scripts/check_batch_freshness.py --json

退出碼：0＝全部新鮮（或被抑制／關閉）；1＝至少一項停更；2＝查不到（DB 不可用）。
2 與 1 分開是因為處置不同：前者要去看 DB／`/healthz`，後者要去看批次日誌。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

EXIT_OK = 0
EXIT_STALE = 1
EXIT_UNKNOWN = 2
# **上游（管線本身）停跑**。刻意與 EXIT_STALE 分流：資產停更的處置是「去看那支批次」，
# 管線沒跑完的處置是「去看 sync 殼與 claude 鎖」，兩者完全不同。
# report-mark-freshness.service 沒有宣告 SuccessExitStatus（已驗），所以 3 會如實
# 觸發 OnFailure——**新增這個碼之前必須確認這件事**，否則會被靜默吞掉。
EXIT_UPSTREAM_STALE = 3

# 管線心跳：由 scripts/sync_new_reports.sh 在**完整成功**時原子寫入。
# 量的是「管線執行新鮮度」，不是「資料新鮮度」——後者才是下面四個 max(created_at)。
HEARTBEAT_PATH = Path(__file__).resolve().parents[1] / "data" / ".last_successful_sync"
# SLA 由實際排程回推，**不是硬編 12 小時**：
#   - timer 是 `OnCalendar=*-*-* 00/3:00:00`＝每 3 小時，帶 Persistent=true
#   - 單輪最壞可長達約 2.5 小時（訊號擷取 100 份實測約 47 分、最壞約 113 分，
#     加上 rsync／匯入／摘要／標題／摘錄）
#   - 長輪會讓下一次觸發撞 PID lock 而 exit 0（不更新心跳）
# 一次長輪 ＋ 一次撞鎖跳過 ＋ 一個週期的餘裕 ＝ 3 個週期 ＝ 9 小時。
# 2026-08-16..19 生產實測間隔多為 3.0h，另有 10.57h／16.26h 兩個缺口——那兩個是
# 08-18 的真實中斷，**應該被報出來**，不是要被門檻容忍掉。
DEFAULT_PIPELINE_HOURS = 9

# 語料閘：與三支批次實際處理的母體同一組條件（有全文、非行政檔）。用全表
# `max(created_at)` 會被行政/活動檔拉新，於是「連續幾天只進非研報」會讓抑制失效、
# 三個資產一起假紅。
_ELIGIBLE = "full_text IS NOT NULL AND is_research IS NOT FALSE"

# 一次往返取四個時間戳。全部是小表或 14k 列的 seq scan，一天跑一次不需要索引。
LATEST_SQL = f"""
SELECT
  (SELECT max(created_at) FROM research.research_report WHERE {_ELIGIBLE})
    AS corpus,
  (SELECT max(created_at) FROM research.research_report
     WHERE summary IS NOT NULL AND {_ELIGIBLE})                     AS summary,
  (SELECT max(created_at) FROM research.report_takeaway)            AS takeaway,
  (SELECT max(created_at) FROM research.report_signal)              AS signal
"""

# 順序即輸出順序：語料在最前，因為其餘三項的判讀都以它為前提。
ASSETS: tuple[tuple[str, str], ...] = (
    ("corpus", "語料入庫"),
    ("summary", "報告摘要"),
    ("takeaway", "重點摘錄"),
    ("signal", "觀點訊號"),
)

DEFAULT_THRESHOLDS: dict[str, int] = {
    # 語料本身停更由 sync unit 的 OnFailure 負責（匯入段失敗會 exit 1 → unit 變紅），
    # 這裡預設只印不告警：NAS 供稿有連假空窗，硬設門檻會變成日曆的假警報。
    "corpus": 0,
    # 摘要與摘錄都掛在每 3 小時的同步鏈上，正常一天內就會動。3 天容得下週末。
    "summary": 3,
    "takeaway": 3,
    # 0＝不告警。理由見模組 docstring：這張表沒有排程產生者。
    "signal": 0,
}

STATE_FRESH = "fresh"
STATE_STALE = "stale"
STATE_SUPPRESSED = "suppressed"
STATE_DISABLED = "disabled"
STATE_UPSTREAM_STALE = "upstream_stale"

# 對外的四個狀態（給人與後續工具看的封閉詞彙）：
#   PASS          全部在門檻內且管線有跑完                     → rc 0
#   EXPECTED_SKIP 門檻 0（關閉）或被語料閘抑制——**不影響 rc**  → rc 不變
#   UPSTREAM_STALE 管線本身沒跑完（心跳缺席／過期／損毀）      → rc 3
#   FAIL          派生資產停更                                 → rc 1


@dataclass(frozen=True)
class Finding:
    asset: str
    label: str
    state: str
    latest: str | None       # ISO-8601，None＝從未產出
    age_days: float | None
    threshold_days: int
    detail: str


def _as_utc(value: datetime | None) -> datetime | None:
    """把 DB 時間戳統一成 aware UTC。

    欄位是 `timestamptz`，asyncpg 一律回 aware；但假 session 與 `--json` 反序列化
    可能餵進 naive 值，而 aware/naive 相減會 `TypeError` ——在告警器裡炸掉等於
    「偵測器自己壞了卻沒人知道」，正是這支腳本要消除的故障型態。
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _age_days(now: datetime, value: datetime | None) -> float | None:
    if value is None:
        return None
    return (now - value).total_seconds() / 86400.0


def assess(
    now: datetime,
    latest: dict[str, datetime | None],
    thresholds: dict[str, int],
) -> list[Finding]:
    """把四個時間戳判成四筆 Finding（純函式，測試不需要 DB）。

    判定順序刻意如此：關閉 → 語料閘 → 從未產出 → 過期。把語料閘放在「從未產出」
    之前，是因為空語料（新機器、重建中）不該被讀成「批次壞了」。
    """
    now = _as_utc(now) or now
    norm = {k: _as_utc(v) for k, v in latest.items()}
    corpus_latest = norm.get("corpus")
    corpus_age = _age_days(now, corpus_latest)

    out: list[Finding] = []
    for asset, label in ASSETS:
        threshold = int(thresholds.get(asset, 0))
        value = norm.get(asset)
        age = _age_days(now, value)
        iso = value.isoformat() if value is not None else None

        def _f(state: str, detail: str) -> Finding:
            return Finding(asset, label, state, iso, age, threshold, detail)

        if threshold <= 0:
            out.append(_f(STATE_DISABLED, "未設門檻（只列出，不告警）"))
            continue

        # 語料閘只套用在派生資產上；語料自己沒有上游可以抑制它。
        if asset != "corpus":
            if corpus_latest is None:
                out.append(_f(STATE_SUPPRESSED, "語料為空，批次無事可做"))
                continue
            if corpus_age is not None and corpus_age > threshold:
                out.append(
                    _f(
                        STATE_SUPPRESSED,
                        f"同窗期內無新研報入庫（語料已 {corpus_age:.1f} 天未前進）",
                    )
                )
                continue

        if value is None:
            out.append(_f(STATE_STALE, "從未產出過"))
            continue
        if age is not None and age > threshold:
            out.append(_f(STATE_STALE, f"已 {age:.1f} 天未產出（門檻 {threshold} 天）"))
            continue
        out.append(_f(STATE_FRESH, f"{age:.1f} 天前產出" if age is not None else "—"))
    return out


def has_stale(findings: list[Finding]) -> bool:
    return any(f.state == STATE_STALE for f in findings)


_STATE_MARK = {
    STATE_FRESH: "OK  ",
    STATE_STALE: "STALE",
    STATE_SUPPRESSED: "skip",
    STATE_DISABLED: "off ",
    STATE_UPSTREAM_STALE: "UPSTR",
}


def read_heartbeat(path: Path | None = None) -> tuple[int | None, str | None]:
    """讀心跳的 epoch。回傳 (epoch, 錯誤原因)；兩者恰有一個為 None。

    **逐鍵解析，絕不 source／eval／json.load**：這個檔由 shell 寫入、落在磁碟上，
    是外部輸入。也刻意不接受 `ts=` 當備援——那是給人看的，機器只信 `epoch=`，
    避免時區與格式解析成為第二個失效面。
    """
    path = path or HEARTBEAT_PATH
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return None, "從未成功跑完（心跳檔不存在）"
    except OSError as exc:
        return None, f"心跳檔讀取失敗（{type(exc).__name__}）"
    for line in raw.splitlines():
        key, _, value = line.partition("=")
        if key.strip() != "epoch":
            continue
        value = value.strip()
        if not value or not value.isdigit():   # 空、字母、負號一律當損毀
            return None, "心跳格式損毀（epoch 非整數）"
        return int(value), None
    return None, "心跳格式損毀（缺 epoch）"


def assess_pipeline(now: datetime, hours: int, path: Path | None = None) -> Finding:
    """管線執行新鮮度。**不經語料閘，也不因 DB 狀態改變**。

    語料閘的用意是「沒有新稿時別怪派生批次」，但管線有沒有跑完與有沒有新稿無關——
    0 篇新研報只要完整跑完也會更新心跳。把它放進閘內會製造一個致命的抑制：
    連假期間管線整個停掉會被讀成「本來就沒事做」。
    """
    now = _as_utc(now) or now
    epoch, err = read_heartbeat(path)
    if epoch is None:
        return Finding("pipeline", "管線執行", STATE_UPSTREAM_STALE, None, None, hours, err or "未知")
    last = datetime.fromtimestamp(epoch, tz=timezone.utc)
    age_h = (now - last).total_seconds() / 3600.0
    iso = last.isoformat()
    # 未來時間戳＝時鐘異常或狀態檔被動過。**不當成新鮮**：那會讓一個壞掉的時鐘
    # 永久抑制告警，而抑制是這裡最危險的失效方向。
    if age_h < 0:
        return Finding(
            "pipeline", "管線執行", STATE_UPSTREAM_STALE, iso, age_h / 24.0, hours,
            f"心跳時間在未來 {abs(age_h):.1f} 小時（時鐘異常或檔案被動過）",
        )
    if age_h > hours:
        return Finding(
            "pipeline", "管線執行", STATE_UPSTREAM_STALE, iso, age_h / 24.0, hours,
            f"已 {age_h:.1f} 小時沒有完整成功（門檻 {hours} 小時）",
        )
    return Finding("pipeline", "管線執行", STATE_FRESH, iso, age_h / 24.0, hours,
                   f"{age_h:.1f} 小時前完整成功")


def has_upstream_stale(findings: list[Finding]) -> bool:
    return any(f.state == STATE_UPSTREAM_STALE for f in findings)


def exit_code(findings: list[Finding]) -> int:
    """rc 優先序：上游 > 資產 > 正常。

    上游優先是因果關係決定的：管線沒跑完時，派生資產「停更」只是症狀，
    先報症狀會讓人去查錯的地方。EXPECTED_SKIP（disabled／suppressed）永不影響 rc。
    """
    if has_upstream_stale(findings):
        return EXIT_UPSTREAM_STALE
    if has_stale(findings):
        return EXIT_STALE
    return EXIT_OK


def fmt_report(findings: list[Finding], now: datetime) -> str:
    lines = [f"=== batch freshness @ {now.isoformat(timespec='seconds')} ==="]
    for f in findings:
        latest = f.latest or "（無）"
        lines.append(f"  [{_STATE_MARK.get(f.state, '?'):5}] {f.label:6} {latest}  {f.detail}")
    lines.append("")
    if has_upstream_stale(findings):
        why = "；".join(f.detail for f in findings if f.state == STATE_UPSTREAM_STALE)
        lines.append(f"UPSTREAM_STALE：管線本身沒有完整跑完（{why}）")
        lines.append("  → 檢查 report-mark-sync.service、data/sync_run_*.log 與 claude 鎖")
        lines.append("  → 派生資產若同時顯示 STALE，那是症狀不是原因")
    elif has_stale(findings):
        names = "、".join(f.label for f in findings if f.state == STATE_STALE)
        lines.append(f"FAIL：{names} 停更 → 檢查 data/sync_run_*.log 與 data/unit_failures.log")
    else:
        lines.append("PASS：全部在門檻內，且管線最近有完整跑完。")
    return "\n".join(lines)


async def fetch_latest(session) -> dict[str, datetime | None]:
    row = (await session.execute(text(LATEST_SQL))).first()
    if row is None:      # 四個純量子查詢一律回一列；防的是假 session 回 None
        return {asset: None for asset, _ in ASSETS}
    return {"corpus": row[0], "summary": row[1], "takeaway": row[2], "signal": row[3]}


async def run(thresholds: dict[str, int], json_out: bool, pipeline_hours: int) -> int:
    now = datetime.now(timezone.utc)

    # **管線心跳先判，而且不碰 DB。** 順序是刻意的：DB 不可用時仍然要能回答
    # 「管線最近有沒有跑完」——那兩件事的處置不同，把它綁在 DB 之後會讓
    # 「DB 掛了」同時掩蓋「管線也停了」。
    pipeline = assess_pipeline(now, pipeline_hours)

    from app.services.db import SessionFactory

    try:
        async with SessionFactory() as session:
            latest = await fetch_latest(session)
    except Exception as exc:                       # noqa: BLE001 — 任何連不上都算查不到
        payload = {"error": f"{type(exc).__name__}: {exc}", "findings": [asdict(pipeline)]}
        if json_out:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(fmt_report([pipeline], now))
            print(f"查不到批次新鮮度（DB 不可用）：{payload['error']}", file=sys.stderr)
        # 刻意仍回 EXIT_UNKNOWN：DB 不可用的處置與其他狀態不同，這個分流是既有設計。
        # 管線那筆已經印在報告裡，不會因此消失。
        return EXIT_UNKNOWN

    findings = [pipeline, *assess(now, latest, thresholds)]
    if json_out:
        print(
            json.dumps(
                {
                    "now": now.isoformat(),
                    "stale": has_stale(findings),
                    "upstream_stale": has_upstream_stale(findings),
                    "findings": [asdict(f) for f in findings],
                },
                ensure_ascii=False,
            )
        )
    else:
        print(fmt_report(findings, now))
    return exit_code(findings)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="偵測管線與派生資產是否停更（0＝PASS／1＝資產停更／2＝DB 查不到／3＝管線停跑）"
    )
    for asset, label in ASSETS:
        p.add_argument(
            f"--{asset}-days",
            type=int,
            default=DEFAULT_THRESHOLDS[asset],
            metavar="N",
            help=f"{label} 容許的最大停更天數，0＝不告警"
            f"（預設 {DEFAULT_THRESHOLDS[asset]}）",
        )
    p.add_argument("--json", action="store_true", help="輸出 JSON（供後續接監控）")
    p.add_argument(
        "--pipeline-hours",
        type=int,
        default=DEFAULT_PIPELINE_HOURS,
        help=f"管線多久沒有完整成功即 UPSTREAM_STALE，單位小時（預設 {DEFAULT_PIPELINE_HOURS}）",
    )
    return p.parse_args(argv)


def thresholds_from_args(args: argparse.Namespace) -> dict[str, int]:
    return {asset: int(getattr(args, f"{asset}_days")) for asset, _ in ASSETS}


if __name__ == "__main__":
    _args = parse_args()
    raise SystemExit(
        asyncio.run(run(thresholds_from_args(_args), _args.json, _args.pipeline_hours))
    )
