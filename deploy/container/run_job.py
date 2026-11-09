"""容器排程工作的包裝（`deploy/container/scheduler.py` 的 `render_crontab` 把每個工作包成這支）：

    python deploy/container/run_job.py <工作名> -- <原本的指令…>

照原樣執行指令、原樣傳回退出碼；結束時印一行 `job=<名> rc=<碼> elapsed_s=<秒>`（JSON 日誌模式下由平台收），
非零退出（撞鎖的 75 除外：那是「別人在跑、這次不跑」）就送通用 webhook 告警 `batch_failed`
（`app/services/alert_webhook.py`；沒設 ALERT_WEBHOOK_URL＝不送）。告警只帶工作名、退出碼與耗時——不帶指令的輸出
（可能含資料或祕密）。指令的 stdout／stderr 直接接到容器日誌，這裡不攔。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

EXIT_LOCK_BUSY = 75  # scripts/_claude_lock.py 與上傳 worker 的整輪鎖：撞鎖＝不跑，不是跑壞
EXIT_USAGE = 64


def _alert(name: str, rc: int, elapsed: float) -> None:
    try:
        from app.services import alert_webhook

        alert_webhook.notify(alert_webhook.EVENT_BATCH_FAILED, f"排程工作 {name} 失敗（退出碼 {rc}）", key=name,
                             details={"job": name, "exit_code": rc, "elapsed_s": round(elapsed, 1)})
    except Exception as exc:  # noqa: BLE001 — 告警失敗不改變工作的退出碼
        print(f"[run_job] webhook 告警失敗：{type(exc).__name__}", file=sys.stderr, flush=True)


def main(argv: list[str], *, runner=subprocess.call, alert=_alert) -> int:
    if len(argv) < 4 or argv[2] != "--":
        print("用法：run_job.py <工作名> -- <指令…>", file=sys.stderr)
        return EXIT_USAGE
    name, command = argv[1], argv[3:]
    t0 = time.monotonic()
    try:
        rc = runner(command)
    except OSError as exc:
        print(f"[run_job] 無法執行 {name}：{type(exc).__name__}", file=sys.stderr, flush=True)
        rc = 127
    elapsed = time.monotonic() - t0
    print(f"[run_job] job={name} rc={rc} elapsed_s={elapsed:.1f}", file=sys.stderr, flush=True)
    if rc not in (0, EXIT_LOCK_BUSY):
        alert(name, rc, elapsed)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
