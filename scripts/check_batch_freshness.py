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

判斷邏輯（`assess`、`assess_pipeline`、`exit_code`、`fetch_latest` 與常數）在
`app/services/batch_freshness.py`，管理後台的資料健康頁（`GET /api/admin/data-health`）以同一組函式
即時判讀；這支只剩 CLI（參數、輸出、DB 不可用的分流）。

退出碼：0＝全部新鮮（或被抑制／關閉）；1＝至少一項停更；2＝查不到（DB 不可用）。
2 與 1 分開是因為處置不同：前者要去看 DB／`/healthz`，後者要去看批次日誌。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.batch_freshness import (  # noqa: E402  — 判斷邏輯與管理後台共用
    ASSETS,
    DEFAULT_PIPELINE_HOURS,
    DEFAULT_THRESHOLDS,
    EXIT_OK,
    EXIT_STALE,
    EXIT_UNKNOWN,
    EXIT_UPSTREAM_STALE,
    LATEST_SQL,
    STATE_DISABLED,
    STATE_FRESH,
    STATE_STALE,
    STATE_SUPPRESSED,
    STATE_UPSTREAM_STALE,
    Finding,
    assess,
    assess_pipeline,
    exit_code,
    fetch_latest,
    has_stale,
    has_upstream_stale,
    read_heartbeat,
)

# 管線心跳的位置。測試會改這個名字（`cbf.HEARTBEAT_PATH = tmp`），所以 `run()` 明確把它傳給
# `assess_pipeline`，不依賴共用模組裡的預設值。
HEARTBEAT_PATH = Path(__file__).resolve().parents[1] / "data" / ".last_successful_sync"

__all__ = [
    "ASSETS", "DEFAULT_PIPELINE_HOURS", "DEFAULT_THRESHOLDS", "EXIT_OK", "EXIT_STALE", "EXIT_UNKNOWN",
    "EXIT_UPSTREAM_STALE", "HEARTBEAT_PATH", "LATEST_SQL", "STATE_DISABLED", "STATE_FRESH", "STATE_STALE",
    "STATE_SUPPRESSED", "STATE_UPSTREAM_STALE", "Finding", "assess", "assess_pipeline", "exit_code",
    "fetch_latest", "fmt_report", "has_stale", "has_upstream_stale", "parse_args", "read_heartbeat", "run",
    "thresholds_from_args",
]


_STATE_MARK = {
    STATE_FRESH: "OK  ",
    STATE_STALE: "STALE",
    STATE_SUPPRESSED: "skip",
    STATE_DISABLED: "off ",
    STATE_UPSTREAM_STALE: "UPSTR",
}


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


async def run(thresholds: dict[str, int], json_out: bool, pipeline_hours: int) -> int:
    now = datetime.now(timezone.utc)

    # **管線心跳先判，而且不碰 DB。** 順序是刻意的：DB 不可用時仍然要能回答
    # 「管線最近有沒有跑完」——那兩件事的處置不同，把它綁在 DB 之後會讓
    # 「DB 掛了」同時掩蓋「管線也停了」。
    pipeline = assess_pipeline(now, pipeline_hours, HEARTBEAT_PATH)

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
