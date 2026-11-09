"""框架的導入排程（容器 scheduler 的 `connectors` 工作）：連接器輪詢入庫 → 下游批次，一輪一次。

廷豐主機的對應是 `scripts/sync_new_reports.sh`（來源段是 NAS／R2 inbox，第 3 段匯入、第 4 段起下游）。
這支把「來源＋匯入」換成 `scripts/ingest_connectors.py`，**下游照抄 sync 鏈第 4 段起的步驟與參數**：

1. `ingest_connectors.py --hashes-out <本輪 hashes>`：本輪成功入庫的 file_hash。
2. 本輪有新研報才跑：`generate_summaries`、`generate_titles`、`extract_takeaways`，都帶 `--hashes-file`
   （**不可改成 `--since-days`**：它濾的是 report_date，會漏掉大半新入庫的研報）。
3. `extract_signals --limit SYNC_SIGNAL_LIMIT`（預設 100）：跨全語料的積壓，每輪都排。**`--limit` 是安全機制**
   （不設上限會連續佔住批次鎖、把匯入弄停），所以這裡永遠帶、沒有不帶的路徑（`tests/test_connector_sync.py` 守）。
4. `generate_brief`：一天一次，腳本自己判斷（當日已有或未到時間即 no-op）。
5. `generate_titles --limit SYNC_TITLE_BACKLOG_LIMIT`（預設 60）＋`--exclude-hashes-file`：標題的歷史積壓。

各步驟是獨立的行程，照舊各自取 LLM 批次鎖（`scripts/_claude_lock.py`）、各自載入 LLM 環境；這支本身不呼叫 LLM。
下游步驟是 best-effort（失敗只記一行、照常往下跑），與 sync 殼相同。

**沒有到期的連接器就整輪不跑**（排程每 15 分鐘叫一次，各連接器依自己的 `poll_minutes` 決定到期與否）：
這樣訊號、標題積壓的節奏跟著輪詢間隔走（預設 180 分鐘＝sync 的 3 小時），而不是排程的 15 分鐘。
profile 沒有啟用任何連接器時也是 no-op。

匯入 rc=75（批次鎖被佔用）時本輪結束：落地的檔案留在落地資料夾、沒記進處理紀錄，下一輪自然再送
（不像 rsync 的 delta 只有一次）。下游以 rc=2（帳號／環境型）中止時，本輪 hashes 複製成
`<CONNECTOR_DATA_DIR>/hashes_retained_<時間>.txt` 並印出補跑指令（下一輪 hashes 會被覆寫）。

用法：
  uv run python scripts/connector_sync.py           # 排程用
  uv run python scripts/connector_sync.py --force   # 不管輪詢間隔
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LOCK_BUSY_RC = 75  # scripts/_claude_lock.py 的 EXIT_LOCK_BUSY
ENV_ABORT_RC = 2
DEFAULT_SIGNAL_LIMIT = 100  # 與 scripts/sync_new_reports.sh 的 SYNC_SIGNAL_LIMIT 預設相同
DEFAULT_TITLE_BACKLOG_LIMIT = 60  # 與 SYNC_TITLE_BACKLOG_LIMIT 預設相同
# 用到 --hashes-file 的下游：rc=2 時要保留本輪 hashes。
HASH_STEPS = ("generate_summaries", "generate_titles", "extract_takeaways")


@dataclass(frozen=True)
class Step:
    name: str
    argv: tuple[str, ...]


def _positive(env: Mapping[str, str], key: str, default: int) -> str:
    raw = (env.get(key) or "").strip()
    return raw if raw.isdigit() and int(raw) > 0 else str(default)


def _workers(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    raw = (env.get(key) or "").strip()
    return ("--workers", raw) if raw else ()


def downstream_steps(hashes: Path, has_new: bool, env: Mapping[str, str]) -> list[Step]:
    """sync 鏈第 4 段起的步驟（順序與參數同 scripts/sync_new_reports.sh）。"""
    py = sys.executable
    steps: list[Step] = []
    if has_new:
        for name, workers in (("generate_summaries", "SYNC_SUMMARY_WORKERS"), ("generate_titles", "SYNC_TITLE_WORKERS"),
                              ("extract_takeaways", "SYNC_TAKEAWAY_WORKERS")):
            steps.append(Step(name, (py, f"scripts/{name}.py", "--hashes-file", str(hashes),
                                     *_workers(env, workers))))
    steps.append(Step("extract_signals", (py, "scripts/extract_signals.py", "--limit",
                                          _positive(env, "SYNC_SIGNAL_LIMIT", DEFAULT_SIGNAL_LIMIT),
                                          *_workers(env, "SYNC_SIGNAL_WORKERS"))))
    steps.append(Step("generate_brief", (py, "scripts/generate_brief.py")))
    exclude = ("--exclude-hashes-file", str(hashes)) if has_new else ()
    steps.append(Step("generate_titles_backlog", (
        py, "scripts/generate_titles.py", "--limit",
        _positive(env, "SYNC_TITLE_BACKLOG_LIMIT", DEFAULT_TITLE_BACKLOG_LIMIT), *exclude,
        *_workers(env, "SYNC_TITLE_WORKERS"))))
    return steps


def _has_lines(path: Path) -> bool:
    try:
        return any(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    except OSError:
        return False


def run_round(*, data_dir: Path, enabled: bool, any_due: bool, force: bool, env: Mapping[str, str],
              run: Callable[[Sequence[str]], int], log: Callable[[str], None] = print,
              now: Callable[[], datetime] = datetime.now) -> int:
    """一輪：匯入 → 下游。回傳退出碼（匯入失敗非零；下游 best-effort 不影響）。"""
    if not enabled:
        log("profile 沒有啟用任何連接器（[connectors].enabled 為空）→ 不跑")
        return 0
    if not any_due and not force:
        log("沒有到輪詢時間的連接器 → 本輪不跑")
        return 0
    hashes = data_dir / f"round_hashes_{now():%Y%m%d_%H%M%S}.txt"
    ingest = [sys.executable, "scripts/ingest_connectors.py", "--hashes-out", str(hashes)]
    if force:
        ingest.append("--force")
    rc = run(ingest)
    log(f"連接器匯入 rc={rc}")
    if rc == LOCK_BUSY_RC:
        log("匯入未執行：批次鎖被另一支批次佔用；落地的檔案下一輪自然再送")
        hashes.unlink(missing_ok=True)
        return 0
    if rc != 0:
        log(f"匯入中止 rc={rc} → 本輪不跑下游（已入庫的篇若有，hashes 在 {hashes}.partial）")
        return 1
    has_new = _has_lines(hashes)
    retained: Path | None = None
    for step in downstream_steps(hashes, has_new, env):
        step_rc = run(list(step.argv))
        if step_rc == 0:
            continue
        log(f"{step.name} 非零退出 rc={step_rc}（best-effort，已略過）")
        if step_rc == ENV_ABORT_RC and step.name in HASH_STEPS and has_new and retained is None:
            retained = data_dir / f"hashes_retained_{hashes.stem.removeprefix('round_hashes_')}.txt"
            shutil.copyfile(hashes, retained)
            log(f"  本輪 hashes 已保留：{retained}；排除中止原因後依序補跑：")
            for name in HASH_STEPS:
                log(f"     python scripts/{name}.py --hashes-file {retained}")
    hashes.unlink(missing_ok=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="框架的導入排程：連接器輪詢入庫 → 下游批次")
    ap.add_argument("--force", action="store_true", help="不管輪詢間隔，所有啟用的連接器都跑")
    args = ap.parse_args(argv)

    from app.profile import get_profile
    from app.services.connectors import data_dir, due_specs

    specs = get_profile().connectors.enabled_specs()
    due, broken = due_specs(specs, force=args.force)
    directory = data_dir()
    directory.mkdir(parents=True, exist_ok=True)

    def run(cmd: Sequence[str]) -> int:
        print(f"$ {' '.join(cmd)}", flush=True)
        return subprocess.run(list(cmd), cwd=ROOT).returncode

    # 狀態檔壞掉的連接器也交給匯入那支去記失敗（計成異常），所以算進「要跑」。
    return run_round(data_dir=directory, enabled=bool(specs), any_due=bool(due or broken), force=args.force,
                     env=os.environ, run=run)


if __name__ == "__main__":
    sys.exit(main())
