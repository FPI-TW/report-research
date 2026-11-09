"""分層回填的待補判斷（app/services/backfill_tiers.py）對真的 PostgreSQL 成立。

`tests/test_backfill_tiers.py` 只驗 SQL 字串與參數名；這裡驗它們真的跑得動、語意對：

- 層別：`report_date` 等於窗期起點算第二層、前一天與 NULL 算第三層。
- 待補：摘要／標題非 NULL、有 valid 摘錄列、有 valid 訊號列的不算待補；`rejected` 的摘錄列不算完成；
  跳過名單（`research.llm_task_failure`）判定要跳過的不算待補，換了 model 就又算。
- 挑候選只看摘要、標題、摘錄：只剩訊號待補的研報不排入。`exclude` 排掉本次處理過的。
- 查進度與估價的兩句也執行得動（估價只 EXPLAIN：它會撈內文前綴，在真實語料上很重）。

跑在 CI 的「schema 契約」job；本機沒有 DB 就 skip。**一律 rollback、絕不 commit**——本機預設連到的是
測試環境的真實資料庫。斷言只看自己塞的列（以 file_hash 辨識），不假設庫是空的；窗期起點設在 2099 年，
真實語料全部落在第三層，不影響第二層的斷言。
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import os
import sys
import unittest
import uuid
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import text

from app.services import backfill_tiers as bt
from app.services import llm_failures, store

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location("_visibility_db_bf",
                                               REPO_ROOT / "tests" / "test_report_visibility_db.py")
assert _spec is not None and _spec.loader is not None
vdb = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = vdb
_spec.loader.exec_module(vdb)

CUTOFF = date(2099, 1, 1)
MODELS = {t: f"bf-test-{t}" for t in bt.DERIVED_TASKS}
CODE = "ZQB9"


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


async def _ingest(session, name: str, file_hash: str, report_date: date | None, targets: list[str]) -> str:
    row = store.ReportRow(
        file_hash=file_hash, file_name=f"{name}.pdf", file_path=f"/nonexistent/{name}.pdf",
        market="TW", doc_type="research", confidence=1.0, stock_code=None, company_name=None,
        source="分層測試券商", report_date=report_date, report_type="個股",
        stock_targets=targets, instrument_types=["stock"], relates_stock=bool(targets),
        full_text=f"分層回填測試內文 {name}",
    )
    with vdb._NoCommit(session):
        return await store.upsert_report(session, row, [f"分層 {name}"], [vdb._vec(0)])


_TAKEAWAY = text(
    "INSERT INTO research.report_takeaway (id, report_id, ordinal, claim, text_sha256, extraction_version, "
    "extraction_status) VALUES (:id, CAST(:rid AS uuid), 1, '論點', 'x', 'test', :status)"
)
_SIGNAL = text(
    "INSERT INTO research.report_signal (id, report_id, market, instrument_code, broker, report_date, "
    "extraction_version, extraction_status) VALUES (:id, CAST(:rid AS uuid), 'TW', :code, '分層測試券商', "
    ":rdate, 'test', 'valid')"
)
_FAILURE = text(
    f"INSERT INTO {llm_failures.TABLE} (file_hash, task, reason, model, fail_count) "
    "VALUES (:h, :task, 'content_filter', :model, 1)"
)


def _wrap(sql: str) -> str:
    return f"SELECT * FROM ({sql}) q WHERE q.file_hash = ANY(CAST(:mine AS text[]))"


class BackfillTiersDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            return asyncio.run(vdb._with_session(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            _skip_or_raise(exc, f"DB 不可用（{type(exc).__name__}）")

    def test_tiers_pending_and_skip_list(self):
        tag = uuid.uuid4().hex
        h = {n: hashlib.sha256(f"bf-{n}-{tag}".encode()).hexdigest() for n in
             ("boundary", "before", "nulldate", "recent_done", "signal_only", "rejected_tk", "blocked")}

        async def fn(session):
            dates = {"boundary": CUTOFF, "before": CUTOFF - timedelta(days=1), "nulldate": None,
                     "recent_done": CUTOFF + timedelta(days=30), "signal_only": CUTOFF + timedelta(days=1),
                     "rejected_tk": CUTOFF + timedelta(days=2), "blocked": CUTOFF + timedelta(days=3)}
            rid = {}
            for name, file_hash in h.items():
                rid[name] = await _ingest(session, name, file_hash, dates[name], [CODE])
            # recent_done：四項都完成。signal_only：摘要、標題、摘錄完成，只剩訊號。
            for name in ("recent_done", "signal_only"):
                await session.execute(text("UPDATE research.research_report SET summary = 's', title = 't' "
                                           "WHERE id = CAST(:id AS uuid)"), {"id": rid[name]})
                await session.execute(_TAKEAWAY, {"id": uuid.uuid4(), "rid": rid[name], "status": "valid"})
            await session.execute(_SIGNAL, {"id": uuid.uuid4(), "rid": rid["recent_done"], "code": CODE,
                                            "rdate": dates["recent_done"]})
            # rejected_tk：摘錄只有 rejected 列＝還沒完成。
            await session.execute(_TAKEAWAY, {"id": uuid.uuid4(), "rid": rid["rejected_tk"], "status": "rejected"})
            # blocked：摘要被審查擋下過（同一 model 一次就跳過）；其他項照常待補。
            await session.execute(_FAILURE, {"h": h["blocked"], "task": "summary", "model": MODELS["summary"]})
            await session.flush()

            mine = sorted(h.values())
            params = {"cutoff": CUTOFF, "mine": mine, **bt.skip_params(MODELS)}
            out = {}
            for tier in (2, 3):
                sql = _wrap(bt.build_candidates_sql(tier, skip_blocked=True, limit=False))
                rows = (await session.execute(text(sql), params)).all()
                out[tier] = {next(n for n, v in h.items() if v == r.file_hash):
                             {t for t in bt.DERIVED_TASKS if getattr(r, f"need_{t}")} for r in rows}
                order = [r.file_hash for r in rows]
                out[f"order{tier}"] = [next(n for n, v in h.items() if v == fh) for fh in order]
            # 換了 model：跳過名單不適用，blocked 的摘要又算待補。
            other = {**params, **bt.skip_params({**MODELS, "summary": "another-model"})}
            rows = (await session.execute(text(_wrap(bt.build_candidates_sql(2, skip_blocked=True, limit=False))),
                                          other)).all()
            out["other_model"] = {r.file_hash for r in rows if r.need_summary}
            # exclude 與 limit
            sql = bt.build_candidates_sql(2, skip_blocked=True, limit=True, exclude=True)
            rows = (await session.execute(text(_wrap(sql)), {**params, "limit": 100000,
                                                               "exclude": [h["boundary"]]})).all()
            out["excluded"] = {r.file_hash for r in rows}
            status = (await session.execute(text(bt.build_status_sql(skip_blocked=True)), params)).all()
            out["status_tiers"] = {r.tier for r in status}
            await session.execute(text("EXPLAIN " + bt.build_estimate_sql(skip_blocked=True)),
                                  {**bt.skip_params(MODELS), "after": "", "batch": 10})
            await session.execute(text("EXPLAIN " + bt.build_estimate_sql(skip_blocked=False)),
                                  {"after": "", "batch": 10})
            return out

        out = self._run(fn)
        assert out is not None
        everything = set(bt.DERIVED_TASKS)
        self.assertEqual(out[2], {
            "boundary": everything,
            "rejected_tk": everything,
            "blocked": everything - {"summary"},
        })
        self.assertEqual(out[3], {"before": everything, "nulldate": everything - {"signal"}})
        # 新的先、NULL 日期最後。
        self.assertEqual(out["order2"], ["blocked", "rejected_tk", "boundary"])
        self.assertEqual(out["order3"], ["before", "nulldate"])
        self.assertEqual(out["other_model"], {h["boundary"], h["rejected_tk"], h["blocked"]})
        self.assertNotIn("recent_done", out[2])
        self.assertNotIn("signal_only", out[2])  # 只剩訊號：不排入
        self.assertEqual(len(out["excluded"]), 2)
        self.assertEqual(out["status_tiers"] & {2, 3}, {2, 3})


if __name__ == "__main__":
    unittest.main()
