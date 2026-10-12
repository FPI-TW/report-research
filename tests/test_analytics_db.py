"""使用分析的 SQL 對真的 PostgreSQL 成立（`app/services/analytics.py`＋`scripts/analytics_rollup.py`；revision 0011）。

- k=3 門檻：2 人的格子被抑制（開放詞彙連鍵都不回、只計 `suppressed_count`；市場保留鍵、數值與人數為 null），
  3 人的格子顯示；`qa_log.user_id` 為 NULL 的題目只算次數不算人數；`usage_daily` 跨日取各日人數的最大值（下限）。
- 序列化後的五份回應不含 user_id、帳號名稱、問題或答案文字。
- 忠實度只計現行 judge（`CURRENT_JUDGE_SQL`）：舊 judge（缺 judge_model＝haiku）與其他模型的分數不混入平均；
  彙總把 judge 寫進 dim，換 judge 後舊彙總不混入。
- 每晚彙總冪等（同一天重跑結果相同）、`--backfill` 只補缺的日子、不覆寫已保留的彙總；使用者刪掉歷史後即時統計
  變少、彙總段保留。即時＋彙總兩段的合併與 `range.spans`。
- 台北時間切日、延遲百分位與 nearest-rank 相同、停止列不計延遲；路由分布、上傳與稽核週量、活躍人數（問答 ∪ 計數）。

所有資料都落在 2000–2001 年（`today_` 參數固定在 2001-03-31），不碰真實日期的列。跑在 CI 的「schema 契約」job；
本機沒有 DB（或庫還沒套 revision 0011）就 skip。**一律 rollback、絕不 commit**——本機預設連到的是測試環境的真實資料庫；
彙總要 commit 的地方用綁在外層交易上、commit 只釋放 savepoint 的 session（同 tests/test_usage_events_db.py）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from app.services import analytics

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import analytics_rollup  # noqa: E402

TPE = timezone(timedelta(hours=8))
TODAY = date(2001, 3, 31)
D1 = date(2001, 3, 10)        # 即時段（live_since＝2001-01-01）
D2 = date(2001, 3, 11)
OLD = date(2000, 12, 15)      # 彙總段
JUDGE = "deepseek-flash"
PARAMS = analytics.Params(min_users=3, live_days=90, judge_model=JUDGE, faithfulness_min=0.9)


def _skip_or_raise(exc: Exception, why: str) -> None:
    if os.getenv("REPORT_MARK_REQUIRE_DB"):
        raise exc
    raise unittest.SkipTest(f"{why}：{exc}")


async def _in_rolled_back_transaction(fn):
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

    from app.services.db import DATABASE_URL

    eng = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
    try:
        async with eng.connect() as conn:
            outer = await conn.begin()

            def factory():
                return AsyncSession(bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint")

            try:
                return await fn(factory)
            finally:
                await outer.rollback()
    finally:
        await eng.dispose()


def _at(d: date, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=TPE)


class _World:
    """在一個 savepoint session 裡塞測試資料；記下所有使用者 id、帳號名、問題與答案文字供掃描。"""

    def __init__(self, session):
        self.s = session
        self.users: dict[str, str] = {}
        self.texts: list[str] = []
        self.reports: dict[str, str] = {}
        self.hashes: dict[str, str] = {}

    async def user(self, name: str) -> str:
        uid = str(uuid.uuid4())
        username = f"an_{name}_{uid[:8]}"
        await self.s.execute(text(
            "INSERT INTO research.app_user (id, username, password_hash, role) VALUES (:id, :u, 'x', 'user')"),
            {"id": uid, "u": username})
        self.users[name] = uid
        self.texts.append(username)
        return uid

    async def report(self, name: str, *, market: str | None, targets: list[str], title: str | None = None,
                     stock_code: str | None = None, company: str | None = None) -> str:
        rid = str(uuid.uuid4())
        fh = uuid.uuid4().hex + uuid.uuid4().hex
        await self.s.execute(text(
            "INSERT INTO research.research_report (id, file_hash, file_name, file_path, market, stock_targets, "
            "title, stock_code, company_name, report_date) VALUES (:id, :fh, :fn, '/x', :m, :t, :title, :sc, :cn, "
            ":rd)"), {"id": rid, "fh": fh, "fn": f"{name}.pdf", "m": market, "t": targets, "title": title,
                       "sc": stock_code, "cn": company, "rd": date(2001, 1, 1)})
        self.reports[name], self.hashes[name] = rid, fh
        return rid

    async def qa(self, when: datetime, *, user: str | None, cited=(), filters=None, latency=None, thinking=None,
                 feedback=None, evaluation=None, stopped=False) -> str:
        qid = str(uuid.uuid4())
        question, answer = f"秘密問題-{qid}", f"秘密答案-{qid}"
        self.texts += [question, answer]
        await self.s.execute(text(
            "INSERT INTO research.qa_log (id, question, answer, cited_report_ids, filters, latency_ms, thinking_ms, "
            "created_at, user_id, feedback, evaluation, stopped) VALUES (:id, :q, :a, CAST(:c AS uuid[]), "
            "CAST(:f AS jsonb), :lat, :th, :at, :uid, :fb, CAST(:ev AS jsonb), :stopped)"),
            {"id": qid, "q": question, "a": answer, "c": [self.reports[c] for c in cited] if cited else None,
             "f": json.dumps(filters) if filters is not None else None, "lat": latency, "th": thinking,
             "at": when, "uid": self.users[user] if user else None, "fb": feedback,
             "ev": json.dumps(evaluation) if evaluation is not None else None, "stopped": stopped})
        return qid


async def _seed(factory) -> _World:
    async with factory() as s:
        w = _World(s)
        for n in ("u1", "u2", "u3", "u4"):
            await w.user(n)
        await w.report("A", market="TW", targets=["2330"], title="報告甲", stock_code="2330", company="台積電")
        await w.report("B", market="US", targets=["AAPL"], title="報告乙")
        await w.report("C", market="HK", targets=["0700"], title="報告丙")
        # A／2330／TW：3 位不同使用者 → 顯示。
        for u in ("u1", "u2", "u3"):
            await w.qa(_at(D1), user=u, cited=["A"], filters={"path": "corpus_qa", "decided_by": "llm"})
        # B／AAPL／US：2 位、5 題 → 抑制（次數再多也一樣）。
        for u in ("u1", "u1", "u1", "u2", "u2"):
            await w.qa(_at(D1), user=u, cited=["B"], filters={"path": "overview", "decided_by": "overview"})
        # C／0700／HK：2 位具名＋3 題 NULL 使用者 → 仍然 2 人，抑制。
        for u in ("u3", "u4", None, None, None):
            await w.qa(_at(D2), user=u, cited=["C"], filters={"path": "advice_risk", "decided_by": "precheck"})
        await s.commit()
        return w


class AnalyticsDbTests(unittest.TestCase):
    def _run(self, fn):
        try:
            asyncio.run(_in_rolled_back_transaction(fn))
        except (unittest.SkipTest, AssertionError):
            raise
        except Exception as exc:
            msg = repr(exc)
            if any(k in msg for k in ("Connect", "connect", "refused", "does not exist", "Timeout")):
                _skip_or_raise(exc, "DB 不可用或尚未套 revision 0011")
            raise

    @staticmethod
    def _plan(since=D1, until=TODAY):
        return analytics.plan_range(since, until, today_=TODAY, live_days=PARAMS.live_days)

    def test_k_threshold_suppresses_two_and_shows_three(self):
        async def go(factory):
            await _seed(factory)
            async with factory() as s:
                out = await analytics.top(s, self._plan(), PARAMS, 20)
            (cell,) = out["reports"]["cells"]
            self.assertEqual((cell["value"], cell["users"], cell["suppressed"], cell["label"]), (3, 3, False, "報告甲"))
            self.assertEqual(out["reports"]["suppressed_count"], 2)   # B（2 人）與 C（2 人＋NULL）
            self.assertEqual([(c["key"], c["value"], c["label"]) for c in out["targets"]["cells"]],
                             [("2330", 3, "台積電")])
            self.assertEqual(out["targets"]["suppressed_count"], 2)
            markets = {c["key"]: c for c in out["markets"]["cells"]}
            self.assertEqual((markets["TW"]["value"], markets["TW"]["suppressed"]), (3, False))
            for key in ("US", "HK"):   # 固定詞彙：保留鍵、數值與人數為 null
                self.assertEqual((markets[key]["value"], markets[key]["users"], markets[key]["suppressed"]),
                                 (None, None, True))
            # 被抑制的排在最後、依鍵排序（不依被隱藏的數值）。
            self.assertEqual([c["key"] for c in out["markets"]["cells"]], ["TW", "HK", "US"])
            self.assertNotIn("CN", markets)   # 沒人用過的市場不補 0

        self._run(go)

    def test_usage_daily_lists_use_max_users_across_days(self):
        async def go(factory):
            w = await _seed(factory)
            async with factory() as s:
                rows = [  # (day, kind, subject, hits, users)
                    (D1, "reading", w.hashes["A"], 10, 3), (D1, "reading", w.hashes["B"], 40, 2),
                    (D2, "reading", w.hashes["B"], 40, 2), (D1, "reading", w.hashes["C"], 7, 3),
                    (D1, "reading", w.hashes["A"].replace("a", "b"), 1, 1),
                    (D1, "search", "TW", 9, 4), (D1, "search", "US", 9, 1), (D1, "search", "HK", 5, 3),
                    (D1, "reading", "", 90, 4), (D1, "search", "", 18, 4), (D2, "report_file", "", 3, 1),
                    (D1, "report_file", w.reports["C"], 3, 3),
                ]
                for d, k, sub, hits, users in rows:
                    await s.execute(text(
                        "INSERT INTO research.usage_daily (day, kind, subject, hits, users) "
                        "VALUES (:d, :k, :s, :h, :u)"), {"d": d, "k": k, "s": sub, "h": hits, "u": users})
                await s.commit()
                out = await analytics.top(s, self._plan(), PARAMS, 20)
                ov = await analytics.overview(s, self._plan(), PARAMS)
            self.assertEqual([(c["key"], c["value"], c["label"]) for c in out["reading"]["cells"]],
                             [(w.hashes["A"], 10, "報告甲"), (w.hashes["C"], 7, "報告丙")])
            # B 兩天各 2 人：合併後人數取最大值 2（不是 4），仍抑制；另一份 1 人。被藏 2 項，不需互補抑制。
            self.assertEqual((out["reading"]["suppressed_count"], out["reading"]["complementary_count"]), (2, 0))
            self.assertEqual([(c["key"], c["label"]) for c in out["report_file"]["cells"]],
                             [(w.reports["C"], "報告丙")])
            # 固定詞彙、恰好 1 格未達門檻（US）：再藏數值最小的可見格 HK，總量差推不回 US。
            search = {c["key"]: c for c in out["search_markets"]["cells"]}
            self.assertEqual((search["TW"]["value"], search["US"]["suppression_reason"],
                              search["HK"]["suppression_reason"], search["HK"]["value"]),
                             (9, "min_users", "complementary", None))
            self.assertEqual((ov["totals"]["reading"], ov["totals"]["search"], ov["totals"]["report_file"]),
                             (90, 18, 3))

        self._run(go)

    def test_complementary_suppression_when_exactly_one_cell_is_hidden(self):
        """固定詞彙（市場、路由類別）與開放詞彙（研報）各有恰好 1 格未達門檻：總量差不得唯一推回那一格。"""
        async def go(factory):
            async with factory() as s:
                w = _World(s)
                for n in ("u1", "u2", "u3", "u4"):
                    await w.user(n)
                await w.report("A", market="TW", targets=["2330"], title="報告甲")
                await w.report("B", market="US", targets=["AAPL"], title="報告乙")
                await w.report("C", market="HK", targets=["0700"], title="報告丙")
                for u in ("u1", "u2", "u3"):            # A／TW／overview：3 人 3 題
                    await w.qa(_at(D1), user=u, cited=["A"], filters={"path": "overview"})
                for u in ("u1", "u2", "u3", "u4"):      # B／US／corpus_qa：4 人 8 題
                    for _ in range(2):
                        await w.qa(_at(D1), user=u, cited=["B"], filters={"path": "corpus_qa"})
                for _ in range(5):                      # C／HK／advice_risk：1 人 5 題
                    await w.qa(_at(D1), user="u4", cited=["C"], filters={"path": "advice_risk"})
                await s.commit()
                top = await analytics.top(s, self._plan(), PARAMS, 20)
                routes = await analytics.routes(s, self._plan(), PARAMS)
            markets = {c["key"]: c for c in top["markets"]["cells"]}
            self.assertEqual({k: (c["value"], c["suppression_reason"]) for k, c in markets.items()},
                             {"US": (8, None), "TW": (None, "complementary"), "HK": (None, "min_users")})
            path = next(d for d in routes["distributions"] if d["name"] == "path")
            self.assertEqual({c["key"]: (c["value"], c["suppression_reason"]) for c in path["cells"]},
                             {"corpus_qa": (8, None), "overview": (None, "complementary"),
                              "advice_risk": (None, "min_users")})
            self.assertEqual((path["suppressed_count"], path["complementary_count"]), (1, 1))
            # 總數 16 減可見格 8＝8，是 overview＋advice_risk 的和：推不回 advice_risk 的 5。
            self.assertEqual(routes["questions"] - sum(c["value"] for c in path["cells"] if not c["suppressed"]), 8)
            self.assertEqual([c["key"] for c in path["cells"]], ["corpus_qa", "advice_risk", "overview"])
            # 開放詞彙：C 被藏、A 一併藏起（數值最小的可見項），只回 B。
            self.assertEqual([c["key"] for c in top["reports"]["cells"]], [w.reports["B"]])
            self.assertEqual((top["reports"]["suppressed_count"], top["reports"]["complementary_count"]), (1, 1))

        self._run(go)

    def test_responses_never_contain_user_ids_usernames_or_qa_text(self):
        async def go(factory):
            w = await _seed(factory)
            async with factory() as s:
                await s.execute(text(
                    "INSERT INTO research.usage_counter (user_id, day, kind, count) VALUES (:u, :d, 'reading', 2)"),
                    {"u": w.users["u4"], "d": D1})
                await s.commit()
                plan = self._plan(since=OLD)
                payloads = [
                    await analytics.overview(s, plan, PARAMS),
                    await analytics.top(s, plan, PARAMS, 100),
                    await analytics.routes(s, plan, PARAMS),
                    await analytics.quality(s, plan, PARAMS),
                    await analytics.operations(s, plan, PARAMS),
                ]
            # 端點回的就是這些 dict 經 pydantic 包裝（web/routers/admin_analytics.py），掃序列化結果。
            blob = "\n".join(json.dumps(p, ensure_ascii=False, default=str) for p in payloads)
            self.assertIn("2330", blob)   # 反向：掃描的確實是有內容的回應
            for secret in list(w.users.values()) + w.texts:
                self.assertNotIn(secret, blob)
            self.assertNotIn("user_id", blob)
            self.assertNotIn("username", blob)

        self._run(go)

    def test_quality_counts_only_current_judge(self):
        async def go(factory):
            async with factory() as s:
                w = _World(s)
                await w.user("u1")
                await w.qa(_at(D1), user="u1", evaluation={"faithfulness_score": 0.5, "judge_model": JUDGE})
                await w.qa(_at(D1), user="u1", evaluation={"faithfulness_score": 1.0, "judge_model": JUDGE})
                await w.qa(_at(D1), user="u1", evaluation={"degraded": True, "judge_model": JUDGE})
                # 舊 judge（缺 judge_model＝claude-haiku-4-5）與其他模型：分數極端，混入就會被看出來。
                await w.qa(_at(D1), user="u1", evaluation={"faithfulness_score": 0.0})
                await w.qa(_at(D1), user="u1", evaluation={"faithfulness_score": 0.0, "judge_model": "other"})
                await w.qa(_at(D1), user="u1", feedback="like")
                await w.qa(_at(D2), user="u1", feedback="dislike")
                await s.commit()
                live = await analytics.quality(s, self._plan(), PARAMS)
                # 同一天彙總後改讀彙總段：分數仍只算當時的現行 judge；換 judge 之後舊彙總一律不計分。
                await analytics.write_day(s, D1, await analytics.compute_daily(s, D1, D1, PARAMS))
                await s.commit()
                later = date(2001, 7, 1)   # D1 已離開即時窗期
                plan = analytics.plan_range(D1, D1, today_=later, live_days=90)
                rolled = await analytics.quality(s, plan, PARAMS)
                switched = await analytics.quality(s, plan, analytics.Params(judge_model="new-judge"))
            week = next(x for x in live["weeks"] if x["week_start"] == analytics.week_start(D1).isoformat())
            self.assertEqual((week["judge_checked"], week["degraded"], week["below_min"], week["score_n"]),
                             (3, 1, 1, 2))
            self.assertEqual(week["avg_score"], 0.75)
            self.assertEqual((week["checked_all"], live["other_judge_checked"]), (5, 2))
            self.assertEqual(sum(x["likes"] for x in live["weeks"]), 1)
            self.assertEqual(sum(x["dislikes"] for x in live["weeks"]), 1)
            (rw,) = rolled["weeks"]
            self.assertEqual((rw["avg_score"], rw["score_n"], rw["checked_all"]), (0.75, 2, 5))
            (sw,) = switched["weeks"]
            self.assertEqual((sw["avg_score"], sw["judge_checked"], switched["other_judge_checked"]), (None, 0, 5))

        self._run(go)

    def test_rollup_idempotent_backfill_and_deletion_keeps_rollup(self):
        async def go(factory):
            w = await _seed(factory)

            async def snapshot(day):
                async with factory() as s:
                    rows = (await s.execute(text(
                        "SELECT metric, dim, value, users FROM research.analytics_daily WHERE day = :d"),
                        {"d": day})).all()
                return sorted(tuple(r) for r in rows)

            first = await analytics.rollup_day(factory, D1, PARAMS, overwrite=True)
            a = await snapshot(D1)
            second = await analytics.rollup_day(factory, D1, PARAMS, overwrite=True)
            self.assertEqual(await snapshot(D1), a)
            self.assertEqual(first.rows, second.rows)
            self.assertIn(("rollup.computed", "", 1.0, None), a)
            self.assertIn(("hot.report", w.reports["A"], 3.0, 3), a)
            self.assertIn(("qa.questions", "", 8.0, 3), a)
            skipped = await analytics.rollup_day(factory, D1, PARAMS, overwrite=False)
            self.assertTrue(skipped.skipped)

            # --backfill 只補缺的日子：D1 已有，D2 與沒活動的日子補上標記；不重算 D1。
            await _delete_user_history(factory, w.users["u1"])
            args = argparse.Namespace(day=None, backfill=(TODAY - D1).days, force=False, dry_run=False)
            rc = await analytics_rollup._run(args, factory, PARAMS, TODAY)
            self.assertEqual(rc, 0)
            self.assertEqual(await snapshot(D1), a)   # u1 刪了歷史，已保留的匿名彙總不縮水
            async with factory() as s:
                self.assertEqual(await analytics.missing_days(s, D1, TODAY - timedelta(days=1)), [])
                live = await analytics.routes(s, self._plan(D1, D1), PARAMS)
                later = analytics.plan_range(D1, D1, today_=date(2001, 7, 1), live_days=90)
                kept = await analytics.routes(s, later, PARAMS)
            self.assertEqual(live["questions"], 4)    # 即時：8 題 − u1 的 4 題
            self.assertEqual(kept["questions"], 8)    # 彙總段：保留
            self.assertEqual(kept["range"]["spans"], [{"since": "2001-03-10", "until": "2001-03-10",
                                                       "source": "rollup"}])

            # --force 才覆寫已保留的日子。
            args.force = True
            self.assertEqual(await analytics_rollup._run(args, factory, PARAMS, TODAY), 0)
            self.assertIn(("qa.questions", "", 4.0, 2), await snapshot(D1))

        self._run(go)

    def test_overview_merges_rollup_and_live_and_cuts_days_in_taipei(self):
        async def go(factory):
            async with factory() as s:
                w = _World(s)
                for n in ("u1", "u2"):
                    await w.user(n)
                # 台北 3/10 23:30 屬於 D1；UTC 3/10 16:30（＝台北 3/11 00:30）屬於 D2。
                await w.qa(_at(D1, 23, 30), user="u1", latency=100)
                await w.qa(datetime(2001, 3, 10, 16, 30, tzinfo=timezone.utc), user="u2", latency=100)
                for i in range(1, 11):
                    await w.qa(_at(D2, 9), user="u1", latency=i * 10, thinking=i)
                await w.qa(_at(D2, 9), user="u1", latency=999_999, thinking=999_999, stopped=True)
                await w.qa(_at(OLD), user="u2", latency=5)
                await s.execute(text(
                    "INSERT INTO research.usage_counter (user_id, day, kind, count) VALUES (:u, :d, 'search', 1)"),
                    {"u": w.users["u2"], "d": D1})
                await s.commit()
                old_day = OLD + timedelta(days=1)   # 彙總段裡沒彙總過的日子
                await analytics.write_day(s, OLD, await analytics.compute_daily(s, OLD, OLD, PARAMS))
                await s.commit()
                plan = analytics.plan_range(OLD, D2, today_=TODAY, live_days=90)
                out = await analytics.overview(s, plan, PARAMS)
            self.assertEqual([x["source"] for x in out["range"]["spans"]], ["rollup", "live"])
            daily = {p["day"]: p for p in out["daily"]}
            self.assertEqual((daily[OLD.isoformat()]["source"], daily[OLD.isoformat()]["questions"]), ("rollup", 1))
            self.assertEqual((daily[old_day.isoformat()]["has_data"], daily[old_day.isoformat()]["questions"]),
                             (False, None))
            d1, d2 = daily[D1.isoformat()], daily[D2.isoformat()]
            self.assertEqual((d1["source"], d1["questions"], d1["askers"]), ("live", 1, 1))
            self.assertEqual(d1["active_users"], 2)        # 問答 u1 ∪ 計數 u2
            self.assertEqual((d2["questions"], d2["askers"]), (12, 2))
            # nearest-rank：10 個值的 p50＝第 5 個、p95＝第 10 個；停止列不計。D2 另有一題 100。
            self.assertEqual((d2["latency_p50_ms"], d2["latency_p95_ms"]), (60.0, 100.0))
            self.assertEqual((d2["thinking_p50_ms"], d2["thinking_p95_ms"]), (5.0, 10.0))
            self.assertEqual(out["totals"]["questions"], 14)
            self.assertEqual(out["totals"]["stopped"], 1)
            self.assertEqual(out["totals"]["distinct_users_live"], 2)
            self.assertEqual(out["latency"]["n"], 12)
            self.assertGreater(out["totals"]["missing_days"], 0)

        self._run(go)

    def test_routes_distribution_and_counters(self):
        async def go(factory):
            w = await _seed(factory)
            async with factory() as s:
                await w.qa(_at(D2), user="u1", filters={"path": "corpus_qa", "decided_by": "llm",
                                                        "llm_model": "deepseek-chat", "llm_truncated": True,
                                                        "invalid_citation_count": 2})
                await w.qa(_at(D2), user="u2", filters={"path": "corpus_qa", "llm_error": "overloaded",
                                                        "invalid_citation_count": "x"})
                await w.qa(_at(D2), user="u3", filters=["not", "an", "object"])
                await s.commit()
                out = await analytics.routes(s, self._plan(), PARAMS)
            dist = {d["name"]: {c["key"]: c for c in d["cells"]} for d in out["distributions"]}
            self.assertEqual((dist["path"]["corpus_qa"]["value"], dist["path"]["corpus_qa"]["users"]), (5, 3))
            for key in ("overview", "advice_risk"):
                self.assertTrue(dist["path"][key]["suppressed"])
            self.assertFalse(dist["decided_by"]["precheck"]["suppressed"])   # 系統詞彙不設門檻
            self.assertEqual(dist["decided_by"]["precheck"]["value"], 5)
            self.assertEqual(dist["llm_error"]["overloaded"]["value"], 1)
            self.assertEqual((out["questions"], out["llm_truncated"], out["invalid_citation_rows"],
                              out["invalid_citations"]), (16, 1, 1, 2))

        self._run(go)

    def test_operations_weekly_upload_and_audit_counts(self):
        async def go(factory):
            async with factory() as s:
                ins = text(
                    "INSERT INTO research.report_upload (file_hash, original_name, size_bytes, state, uploaded_at, "
                    "state_changed_at, decided_at, decision_reason, scan_signature) VALUES (:fh, 'x.pdf', 10, :st, "
                    ":up, :ch, :dec, :reason, :sig)")
                for st, reason, sig in (("published", None, None), ("rejected", "不相關", None),
                                        ("infected", None, "Eicar"), ("draft", None, None)):
                    await s.execute(ins, {"fh": uuid.uuid4().hex * 2, "st": st, "up": _at(D1), "ch": _at(D1),
                                          "dec": _at(D2) if st in ("published", "rejected") else None,
                                          "reason": reason, "sig": sig})
                for action in ("review.update", "review.update", "qa_content.read", "user.create"):
                    await s.execute(text(
                        "INSERT INTO research.admin_audit_log (action, target_type, created_at) "
                        "VALUES (:a, 'x', :at)"), {"a": action, "at": _at(D2)})
                await s.commit()
                out = await analytics.operations(s, self._plan(date(2001, 3, 5), date(2001, 3, 18)), PARAMS)
            week = next(x for x in out["weeks"] if x["week_start"] == "2001-03-05")
            self.assertEqual((week["uploads_received"], week["uploads_published"], week["uploads_rejected"],
                              week["uploads_infected"], week["reviews"], week["qa_content_reads"]), (4, 1, 1, 1, 2, 1))
            actions = {a["action"]: a["count"] for a in out["audit_actions"]}
            self.assertEqual((actions["review.update"], actions["qa_content.read"]), (2, 1))
            self.assertNotIn("user.create", actions)
            self.assertEqual([x["partial"] for x in out["weeks"]], [False, False])

        self._run(go)


async def _delete_user_history(factory, uid: str) -> None:
    """模擬使用者硬刪自己的問答歷史。"""
    async with factory() as s:
        await s.execute(text("DELETE FROM research.qa_log WHERE user_id = :u"), {"u": uid})
        await s.commit()


if __name__ == "__main__":
    unittest.main()
