"""資料完整性稽核：一組對 DB 現況的斷言，違反即非零退出。

════════════════════════════════════════════════════════════════════════
為什麼需要它
════════════════════════════════════════════════════════════════════════
本 repo 的完整性保證幾乎全在「寫入端很小心」，而不在 DB 的約束裡——三張研報衍生
表刻意無 FK、`embedding` 可 NULL、`report_signal.market` 與 `research_report.market`
是兩份各自寫入的副本。這些設計都有理由，代價是**壞掉的方式全部是靜默的**：

  孤兒 `report_doc`     → 沒有任何讀取路徑會碰到它，永遠不會有人回報
  NULL `embedding`      → 檢索少回幾列（PR 之後改為跳過並記數，但仍是召回缺口）
  重複 `chunk_index`    → 閱讀頁錨定跳到錯的位置，看起來只是「引文對不上」
  `market` 兩邊不一致   → 雷達把訊號歸到錯的市場，數字看起來仍然合理
  `content_norm` 漂移   → 字面路命中率下降，而 dense 路仍有結果，所以不像壞了

`scripts/check_batch_freshness.py` 量的是「批次有沒有在前進」，這支量的是「已經
產出的資料有沒有互相矛盾」——兩個不同的問題，同一種失效型態（沒人會回報）。

════════════════════════════════════════════════════════════════════════
兩個刻意的設計
════════════════════════════════════════════════════════════════════════
**1. 只讀，一列都不改。** 稽核器自己去修等於在無人監督下改生產資料；而「修法」
幾乎都需要人決定（孤兒該刪還是該補回連結？重複 chunk 該刪哪一列？）。輸出給人看，
處置由人下。

**2. 分 error / warn 兩級，但**兩者都算失敗（退出碼 1）。分級只影響閱讀順序，
不影響退出碼——「warn 不算失敗」會在三個月內讓 warn 區永遠有東西、從此無人閱讀。

════════════════════════════════════════════════════════════════════════
成本
════════════════════════════════════════════════════════════════════════
`report_chunk` 有 ~57 萬列，其中幾條檢查是全表掃描（NULL embedding、重複
chunk_index），單次數十秒量級。所以：

- 走 `db.relax_statement_timeout()`（`SET LOCAL`，不會漏給下一個借用者）——
  引擎層的 60s statement_timeout 會把這些查詢砍掉，而被砍掉的稽核等於沒有稽核。
- `content_norm` 漂移檢查**取樣**而非全掃（`--norm-sample`，預設 500 列）：
  該欄是 GENERATED，漂移只可能來自「表達式定義被改過而既有列沒重算」，那種漂移
  是全域性的，取樣抓得到。**它與 `tests/test_content_norm_equivalence.py` 的分工**：
  測試驗「表達式定義與 Python 等價」，這裡驗「庫裡實際存的值與 Python 等價」——
  定義正確但既有列是舊定義算出來的，只有後者看得見。
- 刻意**不**在每次請求或每小時跑。設計上是每日一次（或改動 schema／重跑 ingest
  之後手動跑一次）。

用法：
  make db-audit
  uv run python scripts/db_audit.py --json
  uv run python scripts/db_audit.py --skip norm_drift      # 跳過取樣那條（最慢）

退出碼：0＝乾淨；1＝有發現；2＝DB 不可用（與 1 分開：處置不同，前者去看
`/healthz`，後者去看發現本身）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_UNKNOWN = 2

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"

DEFAULT_NORM_SAMPLE = 500


@dataclass(frozen=True)
class Check:
    """一條稽核斷言。`sql` 必須回單一整數＝違反的列數（0＝通過）。"""

    key: str
    label: str
    severity: str
    sql: str
    detail: str


# 順序即輸出順序：error 在前，且同級內把「會讓使用者看到錯東西」排在「只是殘留」之前。
CHECKS: tuple[Check, ...] = (
    Check(
        "durability_off",
        "耐久性寫入被關掉（fsync / full_page_writes / synchronous_commit）",
        SEVERITY_ERROR,
        "SELECT count(*) FROM ("
        "  SELECT 1 WHERE current_setting('fsync') = 'off'"
        "  UNION ALL"
        "  SELECT 1 WHERE current_setting('full_page_writes') = 'off'"
        "  UNION ALL"
        "  SELECT 1 WHERE current_setting('synchronous_commit') = 'off'"
        ") d",
        "`scripts/ingest_lowio.sh` 會關掉這三項來降 fsync I/O，並以 `trap ... EXIT` 還原。"
        "**但 trap 擋不住 SIGKILL**（OOM killer、`kill -9`、WSL 整個被收掉），"
        "而 `ALTER SYSTEM SET` 是寫進 `postgresql.auto.conf` 的——**重啟也不會恢復**。"
        "結果是 DB 無限期跑在 fsync=off：查詢完全正常、沒有任何症狀，但一次斷電或 DB "
        "崩潰就可能讓整個 pgdata 報廢。"
        "處置：`make restore-durability`（等同 `ALTER SYSTEM RESET` 三項 + reload）。"
        "這是整組檢查裡唯一「不修會失去全部資料」的一條，所以列在最前面。",
    ),
    Check(
        "null_embedding",
        "chunk 缺 embedding",
        SEVERITY_ERROR,
        "SELECT count(*) FROM research.report_chunk WHERE embedding IS NULL",
        "字面路會召回到它們而 dense 距離為 NULL。檢索端已改為跳過並記數，"
        "但那是降級不是修復——這些 chunk 對語意檢索完全不存在。"
        "處置：對相關報告重跑 ingest_all.py（`make normalize` 那種就地更新是陷阱，見 CLAUDE.md）。",
    ),
    Check(
        "duplicate_chunk_index",
        "同一報告有重複的 chunk_index",
        SEVERITY_ERROR,
        "SELECT count(*) FROM ("
        "  SELECT report_id, chunk_index FROM research.report_chunk"
        "  GROUP BY 1, 2 HAVING count(*) > 1"
        ") d",
        "閱讀頁以 (report_id, chunk_index) 定位命中段。重複會讓「跳到命中處」"
        "跳到另一段——症狀是引文對不上，看起來像錨定演算法有問題。"
        "成因通常是 ingest 中途失敗後重跑而沒有先清舊列。",
    ),
    Check(
        "signal_market_mismatch",
        "report_signal.market 與 research_report.market 不一致",
        SEVERITY_ERROR,
        "SELECT count(*) FROM research.report_signal s"
        "  JOIN research.research_report r ON r.id = s.report_id"
        " WHERE s.market IS DISTINCT FROM r.market",
        "兩份各自寫入的副本。不一致會讓雷達把訊號歸到錯的市場，"
        "而四分位與共識數字看起來仍然合理——這條是整組檢查裡最不可能被人眼發現的。",
    ),
    Check(
        "is_research_null",
        "is_research 仍有 NULL",
        SEVERITY_ERROR,
        "SELECT count(*) FROM research.research_report WHERE is_research IS NULL",
        "`db/schema.sql` 已收斂成 NOT NULL；還有 NULL 就代表這座庫沒套過最新的 schema。"
        "在那之前 `= true` 與 `IS NOT FALSE` 兩種寫法的母體不同（處置：`make schema`）。",
    ),
    Check(
        "chunkless_report",
        "有全文卻沒有任何 chunk 的研報",
        SEVERITY_WARN,
        "SELECT count(*) FROM research.research_report r"
        " WHERE r.full_text IS NOT NULL AND r.is_research IS NOT FALSE"
        "   AND NOT EXISTS (SELECT 1 FROM research.report_chunk c WHERE c.report_id = r.id)",
        "ingest 只跑了報告層、沒跑到分塊層。這些報告完全不會被檢索到，"
        "但在總覽分面與監控頁的計數裡都在——所以覆蓋率看起來是滿的。",
    ),
    Check(
        "orphan_report_doc",
        "孤兒 report_doc（對話串已刪）",
        SEVERITY_WARN,
        "SELECT count(*) FROM research.report_doc d"
        " WHERE d.conversation_id IS NOT NULL"
        "   AND NOT EXISTS ("
        "     SELECT 1 FROM research.qa_log q"
        "      WHERE COALESCE(q.conversation_id, q.id) = d.conversation_id)",
        "刻意無 FK，所以 DB 不會連刪。刪對話串現在會一起刪（見 answer.delete_conversation），"
        "這裡的數字是那個修正之前累積的殘留，含磁碟上的 PDF。",
    ),
    Check(
        "orphan_report_run",
        "孤兒 report_run（對話串已刪）",
        SEVERITY_WARN,
        "SELECT count(*) FROM research.report_run rn"
        " WHERE rn.conversation_id IS NOT NULL"
        "   AND NOT EXISTS ("
        "     SELECT 1 FROM research.qa_log q"
        "      WHERE COALESCE(q.conversation_id, q.id) = rn.conversation_id)",
        "同上。report_section 以 run_id 對它有 FK CASCADE，刪 run 會連帶清掉。",
    ),
    Check(
        "orphan_report_rendition",
        "孤兒 report_rendition（report_doc 已刪）",
        SEVERITY_WARN,
        "SELECT count(*) FROM research.report_rendition rr"
        " WHERE NOT EXISTS ("
        "   SELECT 1 FROM research.report_doc d WHERE d.id = rr.report_id)",
        "rendition 以 report_id 指向 report_doc（plain uuid，非 FK）。"
        "每列各有自己的 pdf_path，所以孤兒同時是磁碟殘留。",
    ),
    Check(
        "takeaway_sha_disagreement",
        "同一報告的 takeaway 存了不同的 text_sha256",
        SEVERITY_WARN,
        "SELECT count(*) FROM ("
        "  SELECT report_id FROM research.report_takeaway"
        "  GROUP BY 1 HAVING count(DISTINCT text_sha256) > 1"
        ") d",
        "同一份正典文字的 sha 每列各存一份，理應完全相同。不同代表這些摘錄是"
        "在**不同版本的全文**上抽的——舊的那批錨點會落在錯的位置，"
        "而降級判定（驗章不符即不可跳）只擋得住其中一半：與當前正典相符的那批照樣可跳。",
    ),
)

CHECK_KEYS = tuple(c.key for c in CHECKS)

_NORM_SAMPLE_SQL = """
SELECT content, content_norm
FROM research.report_chunk
WHERE content_norm IS NOT NULL
ORDER BY id
LIMIT :n
"""


@dataclass(frozen=True)
class Finding:
    key: str
    label: str
    severity: str
    count: int
    detail: str


def _norm_drift_finding(rows: list[tuple[str, str]]) -> Finding:
    """比對庫裡存的 `content_norm` 與 Python 的 `norm_for_match()`。

    這條不走 `CHECKS` 的「SQL 回一個 count」形狀，因為判準在 Python 端——SQL 沒有
    `norm_for_match`，而把它翻譯成 SQL 就等於再寫一份必須跟著漂移的副本
    （`db/schema.sql` 的 GENERATED 表達式已經是那份副本了，這裡要驗的正是它）。
    """
    from app.services.textnorm import norm_for_match

    bad = sum(1 for content, stored in rows if norm_for_match(content) != stored)
    return Finding(
        key="norm_drift",
        label=f"content_norm 與 norm_for_match() 不符（取樣 {len(rows)} 列）",
        severity=SEVERITY_ERROR,
        count=bad,
        detail=(
            "`content_norm` 是 GENERATED 欄位，所以不符只可能來自「表達式定義被改過而"
            "既有列沒重算」。後果是字面路命中率下降，而 dense 路仍有結果——"
            "所以不像壞了。取樣即可：這種漂移是全域性的。"
            "處置：`ALTER TABLE ... DROP COLUMN content_norm` 後重跑 schema（會重算全表，"
            "並重建 trgm GIN 索引，很慢，先確認沒對外服務）。"
        ),
    )


async def run_audit(
    session, *, skip: frozenset[str] = frozenset(), norm_sample: int = DEFAULT_NORM_SAMPLE
) -> list[Finding]:
    """跑所有未被 skip 的檢查，回傳**全部**結果（含 count=0 的）。

    回傳全部而不只是違反者：呼叫端要能印出「這條檢查跑過而且是乾淨的」。
    只回違反者會讓「檢查被 skip 掉」和「檢查通過」在輸出上長得一樣。
    """
    from app.services.db import relax_statement_timeout

    # 幾條是 57 萬列的全表掃描，引擎層的 60s statement_timeout 會把它們砍掉——
    # 而被砍掉的稽核等於沒有稽核。SET LOCAL 只活到本交易結束。
    await relax_statement_timeout(session)

    out: list[Finding] = []
    for check in CHECKS:
        if check.key in skip:
            continue
        row = (await session.execute(text(check.sql))).first()
        out.append(
            Finding(
                key=check.key,
                label=check.label,
                severity=check.severity,
                count=int(row[0]) if row else 0,
                detail=check.detail,
            )
        )
    if "norm_drift" not in skip and norm_sample > 0:
        rows = (
            await session.execute(text(_NORM_SAMPLE_SQL), {"n": norm_sample})
        ).all()
        out.append(_norm_drift_finding([(r[0], r[1]) for r in rows]))
    # error 先於 warn；同級內維持宣告順序（已依「使用者看到錯東西」排過）
    return sorted(out, key=lambda f: 0 if f.severity == SEVERITY_ERROR else 1)


def render(findings: list[Finding]) -> str:
    lines = ["=== 資料完整性稽核 ==="]
    hits = [f for f in findings if f.count > 0]
    for sev, title in ((SEVERITY_ERROR, "錯誤"), (SEVERITY_WARN, "警告")):
        group = [f for f in findings if f.severity == sev]
        if not group:
            continue
        lines.append(f"\n[{title}]")
        for f in group:
            mark = "×" if f.count > 0 else "○"
            lines.append(f"  {mark} {f.label}：{f.count}")
            if f.count > 0:
                for para in f.detail.split("。"):
                    if para.strip():
                        lines.append(f"      {para.strip()}。")
    if hits:
        n_err = sum(1 for f in hits if f.severity == SEVERITY_ERROR)
        lines.append(
            f"\n結論：{len(hits)} 項有發現（錯誤 {n_err}）。"
            "兩級都算失敗——分級只影響閱讀順序。"
        )
    else:
        lines.append(f"\n結論：{len(findings)} 項檢查全部乾淨。")
    return "\n".join(lines)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="DB 資料完整性稽核（唯讀）")
    p.add_argument(
        "--skip",
        action="append",
        default=[],
        metavar="KEY",
        help=f"跳過某條檢查，可重複。可用值：{', '.join(CHECK_KEYS)}, norm_drift",
    )
    p.add_argument(
        "--norm-sample",
        type=int,
        default=DEFAULT_NORM_SAMPLE,
        help=f"content_norm 漂移檢查的取樣列數（0＝跳過；預設 {DEFAULT_NORM_SAMPLE}）",
    )
    p.add_argument("--json", action="store_true", help="輸出 JSON 供程式讀取")
    return p.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    from app.services.db import SessionFactory

    try:
        async with SessionFactory() as session:
            findings = await run_audit(
                session,
                skip=frozenset(args.skip),
                norm_sample=args.norm_sample,
            )
    except Exception as exc:
        # 與「有發現」分開的退出碼：處置不同（去看 /healthz，不是去看發現本身）。
        msg = f"DB 不可用，無法稽核：{type(exc).__name__}: {exc}"
        if args.json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return EXIT_UNKNOWN

    if args.json:
        print(
            json.dumps(
                {"ok": True, "findings": [asdict(f) for f in findings]},
                ensure_ascii=False,
            )
        )
    else:
        print(render(findings))
    return EXIT_FINDINGS if any(f.count > 0 for f in findings) else EXIT_OK


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
