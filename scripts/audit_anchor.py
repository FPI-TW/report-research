#!/usr/bin/env python3
"""稽核紀錄雜湊鏈的外部錨定：驗鏈 → 比對先前所有錨點 → 追加今天的鏈頭（DB 之外，NAS）。

用法：
    uv run python scripts/audit_anchor.py                  # 每日（report-mark-audit-anchor.timer）
    uv run python scripts/audit_anchor.py --verify-only    # 只驗鏈、不寫錨點（還原備份後用）
    uv run python scripts/audit_anchor.py --anchor-file PATH

**為什麼需要它**：`research.admin_audit_log` 的雜湊鏈（revision 0002）讓「改一列」必須連帶重算後面
整條鏈，但有 DB superuser 權限的人做得到。把每日的鏈頭（id, row_hash）寫到 DB 之外，下次再比對：
被整條重算過的鏈，舊錨點上那一列的 row_hash 一定對不上——這才是不必相信 DB 的證據。

錨點檔預設 `$REPORT_MARK_BACKUP_DIR/audit-anchors.jsonl`（與每日備份同一個 NAS 落點，由
/etc/default/report-mark-sync 提供）。落點不存在時**失敗而不是改寫本機**：與 pgdata 同一塊磁碟的
錨點和 DB 一起被改掉就沒有意義了（同 scripts/db_backup.sh 的理由）。

從較舊的備份還原之後，比還原點新的錨點會指向不存在的列而被判為不符——這是刻意的：還原本來
就該被看見。確認是還原造成的之後，把那幾行錨點移到旁邊的檔案留存，再讓 timer 繼續跑。

退出碼：0 正常；1 鏈斷了或與先前錨點不符（疑似竄改，OnFailure 會告警）；2 無法執行（DB 不可用、
錨點落點不存在）。

**狀態檔**（Admin v2）：每次正式執行（不含 `--verify-only`）結束時把結果寫進 `data/health/audit_anchor.json`
（`security_ops.write_anchor_status`：result ok／tamper／error、時刻、鏈頭 id、雜湊前 16 字、列數、比對過的錨點數、
一行訊息），給管理後台「安全」頁顯示「最後一次錨定」。狀態檔只是輔助顯示：寫不進去只印一行警告，**絕不改變退出碼**；
告警仍以 OnFailure 為準。錨點本身照舊只寫 NAS。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# 必須早於 app.services.db：它在 import 期就讀 REPORT_MARK_DB_URL。只補還不存在的鍵。
from web.env_loader import load_env_file  # noqa: E402

load_env_file(REPO_ROOT / ".env")

from app.services import accounts, security_ops  # noqa: E402

EXIT_OK, EXIT_TAMPER, EXIT_ERROR = 0, 1, 2
ANCHOR_NAME = "audit-anchors.jsonl"


def default_anchor_file(env=None) -> Path | None:
    env = os.environ if env is None else env
    base = (env.get("REPORT_MARK_BACKUP_DIR") or "").strip()
    return Path(base) / ANCHOR_NAME if base else None


def read_anchors(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} 第 {n} 行不是 JSON：{exc}") from exc
        if rec.get("head_id") is not None:
            out.append(rec)
    return out


def anchor_problems(anchors: list[dict], hashes: dict[int, str]) -> list[str]:
    """每個舊錨點的 (head_id, head_hash) 必須仍在 DB 裡、而且一字不差。"""
    problems = []
    for rec in anchors:
        aid = int(rec["head_id"])
        if aid not in hashes:
            problems.append(f"錨點 {rec.get('at')} 指向的稽核列 id={aid} 已不存在")
        elif hashes[aid] != rec.get("head_hash"):
            problems.append(f"錨點 {rec.get('at')} 的 id={aid} row_hash 不符（錨點 {rec.get('head_hash')}，"
                            f"DB {hashes[aid]}）")
    return problems


_RESULT_BY_EXIT = {EXIT_OK: "ok", EXIT_TAMPER: "tamper", EXIT_ERROR: "error"}


async def run(args, api=accounts, now=datetime.now) -> int:
    """跑一次錨定並（非 `--verify-only` 時）寫狀態檔。退出碼由 `_run` 決定，狀態檔不影響它。"""
    facts: dict = {}
    rc = await _run(args, api, now, facts)
    if not args.verify_only:
        security_ops.write_anchor_status({
            "result": _RESULT_BY_EXIT.get(rc, "error"), "exit_code": rc,
            "at": now().astimezone().isoformat(timespec="seconds"), **facts,
        })
    return rc


async def _run(args, api, now, facts: dict) -> int:
    try:
        status = await api.verify_audit_chain()
    except Exception as exc:
        print(f"無法驗證稽核鏈（DB 不可用或尚未套 revision 0002）：{exc!r}", file=sys.stderr)
        facts["message"] = f"無法驗證稽核鏈：{type(exc).__name__}"
        return EXIT_ERROR
    facts.update(head_id=status.head_id, head_hash_prefix=(status.head_hash or "")[:16] or None, total=status.total)
    if not status.ok:
        print(f"!! 稽核雜湊鏈斷裂：{status.total} 列中這些 id 對不上 {list(status.broken_ids)}", file=sys.stderr)
        facts["message"] = f"稽核雜湊鏈斷裂：{len(status.broken_ids)} 列對不上"
        return EXIT_TAMPER
    summary = f"稽核鏈完整：{status.total} 列，鏈頭 id={status.head_id} hash={(status.head_hash or '')[:16]}"
    if args.verify_only:
        print(summary)
        return EXIT_OK

    path = Path(args.anchor_file) if args.anchor_file else default_anchor_file()
    if path is None:
        print("沒有錨點落點：請設 REPORT_MARK_BACKUP_DIR（/etc/default/report-mark-sync）或 --anchor-file",
              file=sys.stderr)
        facts["message"] = "沒有錨點落點（REPORT_MARK_BACKUP_DIR 未設）"
        return EXIT_ERROR
    if not path.parent.is_dir():
        print(f"錨點落點 {path.parent} 不存在（NAS 未掛載？）——刻意不改寫到本機", file=sys.stderr)
        facts["message"] = "錨點落點不存在（NAS 未掛載？）"
        return EXIT_ERROR
    try:
        anchors = read_anchors(path)
    except ValueError as exc:
        print(f"!! {exc}", file=sys.stderr)
        facts["message"] = "錨點檔有讀不懂的行"
        return EXIT_TAMPER
    hashes = await api.audit_row_hashes(int(a["head_id"]) for a in anchors)
    problems = anchor_problems(anchors, hashes)
    if problems:
        print("!! 稽核紀錄與先前錨點不符（疑似整條重算或從舊備份還原）：", file=sys.stderr)
        for p in problems:
            print("   " + p, file=sys.stderr)
        facts.update(anchors_checked=len(anchors), message=f"{len(problems)} 個先前錨點與 DB 不符")
        return EXIT_TAMPER
    if status.head_id is not None:
        rec = {"at": now().astimezone().isoformat(timespec="seconds"), "head_id": status.head_id,
               "head_hash": status.head_hash, "total": status.total}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
    print(f"{summary}；先前 {len(anchors)} 個錨點全部相符；已追加錨點到 {path}")
    facts.update(anchors_checked=len(anchors), message=f"先前 {len(anchors)} 個錨點全部相符，已追加今天的錨點")
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--verify-only", action="store_true", help="只驗鏈，不讀寫錨點")
    ap.add_argument("--anchor-file", help=f"錨點檔（預設 $REPORT_MARK_BACKUP_DIR/{ANCHOR_NAME}）")
    return asyncio.run(run(ap.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
