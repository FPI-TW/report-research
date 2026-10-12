"""`scripts/load_observations.py`：監控 spool → DB 的進度、清理與失敗語意（不連 DB）。

SQL 對真 DB 的冪等與 lost 判定在 `tests/test_ops_monitoring_db.py`；這裡用假 session 驗 loader 本身：
- DB 不可用：rc=2，spool 的資料檔與進度檔原封不動，下一輪從同一個位置重讀；
- 成功：進度只在 commit 之後前進，同一份 spool 第二輪不再送出任何列；
- 只碰自己的檔：別的命名規則的檔不讀不刪；自己的檔裡不認得的紀錄（type 與檔案種類不符、未知版本）
  不匯入，而且那個檔不會被清理刪除；
- 事件（P5 的 incidents-*.jsonl＋journal/ 片段）：片段隨事件一起送出（遮掉祕密），commit 之後才刪片段檔；
  DB 不可用時事件檔與片段原封不動；孤兒片段只在沒有任何事件行引用、而且夠舊時才刪；
- 寫到一半的最後一行不讀；舊日檔讀完、安靜夠久才刪；檔案被換掉從頭讀；鎖被持有 rc=75。
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import load_observations as lo  # noqa: E402

TODAY = "20261006"
OLD = "20261005"
INV = "c" * 32


def _obs(subject="host", metric="cpu_pct", value=12.5, ts="2026-10-05T10:00:00+08:00", **kw) -> dict:
    rec = {"v": 1, "type": "observation", "host": "office-host", "observed_at": ts, "scope": "host",
           "subject": subject, "metrics": {metric: value}, "states": {}}
    rec.update(kw)
    return rec


def _job(state="finished", **kw) -> dict:
    rec = {"v": 1, "type": "job", "host": "office-host", "unit": "report-mark-sync.service", "service": "sync",
           "invocation_id": INV, "state": state, "started_at": "2026-10-05T09:00:00+08:00",
           "finished_at": "2026-10-05T09:10:00+08:00" if state == "finished" else None,
           "result": "success" if state == "finished" else None, "exit_status": 0, "exec_main_code": "exited",
           "observed_at": "2026-10-05T09:11:00+08:00"}
    rec.update(kw)
    return rec


def _lines(*recs) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs)


class _Session:
    def __init__(self, log):
        self.log = log

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.log.append("commit")


class _Args:
    def __init__(self, spool, dry_run=False, max_bytes=lo.DEFAULT_MAX_BYTES):
        self.spool_dir = str(spool)
        self.dry_run = dry_run
        self.max_bytes = max_bytes


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.spool = Path(self.tmp.name) / "spool"
        self.spool.mkdir()
        self.calls: list[dict] = []
        self.log: list[str] = []

    async def _fake_import(self, session, *, observations, jobs, events=None):
        self.calls.append({"observations": list(observations), "jobs": list(jobs), "events": list(events or [])})
        return lo.ops_monitoring.ImportStats(observations=len(observations), jobs=len(jobs),
                                             events=len(events or []))

    def _run(self, *, factory=None, today=TODAY, **kw):
        factory = factory or (lambda: _Session(self.log))
        with mock.patch.object(lo.ops_monitoring, "import_records", self._fake_import), \
                mock.patch("builtins.print"):
            return lo.run(_Args(self.spool, **kw), session_factory=factory, today=today)

    def _write(self, name, text, *, age=None):
        path = self.spool / name
        with path.open("a", encoding="utf-8") as fh:
            fh.write(text)
        if age is not None:
            t = time.time() - age
            os.utime(path, (t, t))
        return path

    def _snapshot(self):
        return {p.relative_to(self.spool).as_posix(): p.read_bytes()
                for p in sorted(self.spool.rglob("*")) if p.is_file() and p.name != lo.LOCK_FILE}

    def test_missing_spool_dir_is_nothing_to_do(self):
        args = _Args(self.spool / "nope")
        with mock.patch("builtins.print"):
            self.assertEqual(lo.run(args), lo.EXIT_OK)

    def test_db_unavailable_keeps_spool_and_progress_untouched(self):
        self._write(f"observations-{TODAY}.jsonl", _lines(_obs(), _obs(subject="fs:/")))
        self._write(f"jobs-{OLD}.jsonl", _lines(_job()), age=3600)
        before = self._snapshot()

        def broken():
            raise ConnectionRefusedError("db down")

        with mock.patch("sys.stderr"):
            self.assertEqual(self._run(factory=broken), lo.EXIT_DB)
        self.assertEqual(self._snapshot(), before, "DB 不可用時 spool 原封不動（也沒有寫進度檔）")
        # 恢復後整份補匯入
        self.assertEqual(self._run(), lo.EXIT_OK)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.calls[0]["observations"]), 2)
        self.assertEqual(len(self.calls[0]["jobs"]), 1)

    def test_commit_failure_also_keeps_progress(self):
        self._write(f"observations-{TODAY}.jsonl", _lines(_obs()))

        class _FailingCommit(_Session):
            async def commit(self):
                raise RuntimeError("serialization failure")

        with mock.patch("sys.stderr"):
            self.assertEqual(self._run(factory=lambda: _FailingCommit(self.log)), lo.EXIT_DB)
        self.assertFalse((self.spool / lo.STATE_FILE).exists())

    def test_second_run_sends_nothing_new(self):
        path = self._write(f"observations-{TODAY}.jsonl", _lines(_obs()))
        self.assertEqual(self._run(), lo.EXIT_OK)
        self.assertEqual(self._run(), lo.EXIT_OK)
        self.assertEqual(len(self.calls), 1, "沒有新資料就不連 DB")
        state = json.loads((self.spool / lo.STATE_FILE).read_text(encoding="utf-8"))
        self.assertEqual(state["files"][path.name]["offset"], path.stat().st_size)
        self._write(path.name, _lines(_obs(ts="2026-10-06T10:01:00+08:00")))
        self._run()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.calls[1]["observations"]), 1, "只送新增的那一行")

    def test_partial_last_line_waits_for_its_newline(self):
        path = self._write(f"observations-{TODAY}.jsonl", _lines(_obs()) + '{"v":1,"type":"obs')
        self._run()
        self.assertEqual(len(self.calls[0]["observations"]), 1)
        self._write(path.name, 'ervation"}\n')  # 補完的那一行（不合格）＋下一行
        self._write(path.name, _lines(_obs(ts="2026-10-06T10:02:00+08:00")))
        self._run()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(len(self.calls[1]["observations"]), 1)

    def test_foreign_files_are_never_read_or_deleted(self):
        foreign = self._write(f"alerts-{OLD}.jsonl", _lines({"v": 1, "type": "incident_event", "x": 1}),
                              age=86400)
        (self.spool / "journal").mkdir()
        not_ours = self._write("journal/abc.txt", "line\n", age=30 * 86400)
        self._write(f"observations-{OLD}.jsonl", _lines(_obs()), age=86400)
        before = {p: p.read_bytes() for p in (foreign, not_ours)}
        self.assertEqual(self._run(), lo.EXIT_OK)
        self.assertEqual({p: p.read_bytes() for p in (foreign, not_ours)}, before)
        self.assertFalse((self.spool / f"observations-{OLD}.jsonl").exists(), "自己的舊日檔匯入後刪掉")
        state = json.loads((self.spool / lo.STATE_FILE).read_text(encoding="utf-8"))
        self.assertNotIn(foreign.name, state["files"])

    def test_unknown_records_in_own_files_are_kept_not_imported(self):
        path = self._write(
            f"observations-{OLD}.jsonl",
            _lines(_obs(), {"v": 1, "type": "incident_event", "event_id": "x"}, _obs(v=2), _obs(subject="fs:/")),
            age=86400,
        )
        before = path.read_bytes()
        self.assertEqual(self._run(), lo.EXIT_OK)
        self.assertEqual([r["subject"] for r in self.calls[0]["observations"]], ["host", "fs:/"])
        self.assertTrue(path.exists(), "有不認得的紀錄的檔不得被清理刪除")
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(self._run(), lo.EXIT_OK)
        self.assertEqual(len(self.calls), 1, "進度照常前進，不重複匯入")
        self.assertTrue(path.exists(), "下一輪仍然記得要保留")

    def test_old_files_are_deleted_only_when_fully_read_and_quiet(self):
        today = self._write(f"observations-{TODAY}.jsonl", _lines(_obs()), age=86400)
        quiet = self._write(f"jobs-{OLD}.jsonl", _lines(_job()), age=3600)
        busy = self._write(f"observations-{OLD}.jsonl", _lines(_obs()), age=10)
        self.assertEqual(self._run(), lo.EXIT_OK)
        self.assertTrue(today.exists(), "今天的檔還會被寫，不刪")
        self.assertFalse(quiet.exists())
        self.assertTrue(busy.exists(), "剛被寫過（午夜前後那一批）先不刪")
        os.utime(busy, (time.time() - 3600,) * 2)
        self._run()
        self.assertFalse(busy.exists())
        self.assertEqual(len(self.calls), 1, "刪檔那一輪沒有重複匯入")

    def test_replaced_file_is_reread_from_start(self):
        path = self._write(f"observations-{TODAY}.jsonl", _lines(_obs(), _obs(subject="a")))
        self._run()
        path.unlink()
        self._write(path.name, _lines(_obs(subject="b")))
        self._run()
        self.assertEqual([r["subject"] for r in self.calls[1]["observations"]], ["b"])

    def test_invalid_and_malformed_lines_are_skipped(self):
        self._write(f"jobs-{TODAY}.jsonl",
                    "not json\n[]\n" + _lines(_job(invocation_id="xyz"), _job(state="lost"), _job()))
        self._run()
        self.assertEqual(len(self.calls[0]["jobs"]), 1)

    def test_max_bytes_splits_the_backlog_across_rounds(self):
        line = _lines(_obs())
        self._write(f"observations-{OLD}.jsonl", line * 3, age=3600)
        self._write(f"observations-{TODAY}.jsonl", line * 3)
        self._run(max_bytes=len(line) * 4)
        self.assertEqual(len(self.calls[0]["observations"]), 4)
        self._run(max_bytes=len(line) * 4)
        self.assertEqual(len(self.calls[1]["observations"]), 2)

    def test_dry_run_neither_connects_nor_advances(self):
        self._write(f"observations-{TODAY}.jsonl", _lines(_obs()))

        def never():
            raise AssertionError("dry-run 不得連 DB")

        self.assertEqual(self._run(factory=never, dry_run=True), lo.EXIT_OK)
        self.assertFalse((self.spool / lo.STATE_FILE).exists())

    def test_concurrent_run_exits_75(self):
        with open(self.spool / lo.LOCK_FILE, "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with mock.patch("sys.stderr"):
                self.assertEqual(self._run(), lo.EXIT_LOCKED)

    def test_default_spool_dir_follows_env(self):
        with mock.patch.dict("os.environ", {"OPS_SPOOL_DIR": "/x/y"}):
            self.assertEqual(lo.default_spool_dir(), Path("/x/y"))

    def test_conftest_points_spool_away_from_the_deploy_dir(self):
        self.assertEqual(os.environ.get("OPS_SPOOL_DIR"), os.devnull)
        with mock.patch("builtins.print"):
            self.assertEqual(lo.main([]), lo.EXIT_OK)


FIRST = 1791252000  # 2026-10-06T10:00:00+08:00


def _event(action="FIRING", *, comp="web", first=FIRST, at=FIRST, journal=None, **kw) -> dict:
    iid = f"office-host:{comp}:{first}"
    rec = {"v": 1, "type": "incident_event", "event_id": f"{iid}:{at}:{action}", "incident_id": iid,
           "host": "office-host", "component": comp, "kind": "service", "probe_unit": "report-mark-health.service",
           "action": action, "severity": "RESOLVED" if action == "RESOLVED" else "CRITICAL",
           "incident_severity": "CRITICAL", "reason": "probe_exit_1" if action != "RESOLVED" else "healthy",
           "status": "web_incident" if action != "RESOLVED" else "ok", "summary": "探針回報失敗",
           "notified": True, "occurred_at": "2026-10-05T10:00:00+08:00", "first_seen_at": "2026-10-05T10:00:00+08:00",
           "journal_file": journal, "journal_bytes": 0, "journal_truncated": False,
           "journal_since": "2026-10-05T09:50:00+08:00" if journal else None,
           "journal_until": "2026-10-05T10:00:00+08:00" if journal else None,
           "journal_units": "report-mark-health.service" if journal else None}
    rec.update(kw)
    return rec


class IncidentLoaderTests(unittest.TestCase):
    """事件 spool：片段隨事件送出、commit 後才刪；DB 掛掉時原封不動；孤兒判定保守。"""

    # 共用 LoaderTests 的夾具（不繼承，免得把它的測試再跑一次）
    setUp = LoaderTests.setUp
    _fake_import = LoaderTests._fake_import
    _run = LoaderTests._run
    _write = LoaderTests._write
    _snapshot = LoaderTests._snapshot

    def _journal(self, name, text, *, age=None):
        (self.spool / "journal").mkdir(exist_ok=True)
        return self._write(f"journal/{name}", text, age=age)

    def test_events_are_imported_with_their_journal_and_the_excerpt_file_is_removed_after_commit(self):
        j = self._journal("web-1-1-FIRING.log", "a\nAPI_KEY=sk-abcdefghijklmnopqrstu\nb\n")
        self._write(f"incidents-{TODAY}.jsonl", _lines(_event(journal="journal/web-1-1-FIRING.log"),
                                                       _event("REMINDER", at=FIRST + 1800)))
        self.assertEqual(self._run(), lo.EXIT_OK)
        events = self.calls[0]["events"]
        self.assertEqual([e["action"] for e in events], ["FIRING", "REMINDER"])
        self.assertIn("<redacted>", events[0]["journal_excerpt"])
        self.assertNotIn("sk-abcdefghijklmnop", events[0]["journal_excerpt"])
        self.assertIsNone(events[1]["journal_excerpt"])
        self.assertFalse(j.exists(), "commit 之後片段檔刪掉（正本在 journald）")
        self.assertEqual(self._run(), lo.EXIT_OK)
        self.assertEqual(len(self.calls), 1, "同一份事件 spool 第二輪不再送出")

    def test_db_unavailable_keeps_events_and_journals_untouched(self):
        self._journal("web-1-1-FIRING.log", "boom\n", age=7200)
        self._write(f"incidents-{OLD}.jsonl", _lines(_event(journal="journal/web-1-1-FIRING.log")), age=86400)
        before = self._snapshot()

        def broken():
            raise ConnectionRefusedError("db down")

        with mock.patch("sys.stderr"):
            self.assertEqual(self._run(factory=broken), lo.EXIT_DB)
        self.assertEqual(self._snapshot(), before, "DB 不可用時事件檔、片段與進度都原封不動")
        self.assertEqual(self._run(), lo.EXIT_OK)
        self.assertEqual(self.calls[0]["events"][0]["journal_excerpt"], "boom\n")
        self.assertFalse((self.spool / f"incidents-{OLD}.jsonl").exists(), "匯入完的舊日事件檔才刪")

    def test_orphan_journals_are_removed_only_when_unreferenced_and_old(self):
        old_orphan = self._journal("web-1-2-FIRING.log", "x\n", age=7200)
        young_orphan = self._journal("web-1-3-FIRING.log", "x\n", age=60)
        stale_tmp = self._journal(".web-1-4-FIRING.log.tmp.123", "x\n", age=7200)
        # 被尚未讀到的事件行引用（這一輪的位元組預算只讀得到第一個檔）：再舊也不刪
        pending = self._journal("web-1-5-FIRING.log", "x\n", age=7200)
        first = _lines(_event(at=FIRST + 1))
        self._write(f"incidents-{OLD}.jsonl", first, age=86400)
        self._write(f"incidents-{TODAY}.jsonl", _lines(_event(at=FIRST + 2, journal="journal/web-1-5-FIRING.log")))
        self._run(max_bytes=len(first.encode("utf-8")))
        self.assertFalse(old_orphan.exists())
        self.assertFalse(stale_tmp.exists())
        self.assertTrue(young_orphan.exists(), "P5 可能剛寫完片段、還沒寫那一行")
        self.assertTrue(pending.exists(), "還沒匯入的事件行引用的片段不得被當孤兒刪掉")
        self._run()
        self.assertFalse(pending.exists())
        self.assertEqual(self.calls[1]["events"][0]["journal_excerpt"], "x\n")

    def test_journal_reference_cannot_escape_the_spool(self):
        outside = Path(self.tmp.name) / "secret.log"
        outside.write_text("do not read\n", encoding="utf-8")
        self._write(f"incidents-{TODAY}.jsonl", _lines(_event(journal="journal/../../secret.log")))
        self._run()
        self.assertIsNone(self.calls[0]["events"][0]["journal_excerpt"])
        self.assertTrue(outside.exists())

    def test_invalid_utf8_in_an_event_line_is_replaced_not_dropped(self):
        raw = json.dumps(_event(), ensure_ascii=False).encode("utf-8").replace("探針".encode(), b"\xe6\x8e")
        (self.spool / f"incidents-{TODAY}.jsonl").write_bytes(raw + b"\n")
        self._run()
        self.assertEqual(len(self.calls[0]["events"]), 1)

    def test_invalid_event_lines_are_skipped(self):
        self._write(f"incidents-{TODAY}.jsonl", _lines(
            _event(severity="CRITICAL", action="RESOLVED"),          # RESOLVED 必須配 RESOLVED
            _event(event_id="other:1:FIRING"),                       # event_id 不屬於這個事件
            _event(kind="nope"), _event(first_seen_at="2026-10-05T10:00:00"),  # 沒時區
            _event(),
        ))
        self._run()
        self.assertEqual(len(self.calls[0]["events"]), 1)


class HandlerLoaderFormatTests(unittest.TestCase):
    """P5 真的寫出來的事件行與片段，loader 的驗證必須全部接受（兩端同一份格式）。"""

    def test_handler_records_pass_loader_validation(self):
        from test_incident_handler import _Harness, _install_fake_journalctl

        h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(h.close)
        _install_fake_journalctl(h, "2026-10-06T10:00:00+08:00 host web[1]: password=hunter2 boom\n")
        spool = h.root / "spool"
        for code in (1, 1, 0):
            h.set_probe(code, result="exit-code" if code else "success")
            h.run(INCIDENT_SPOOL_DIR=str(spool), INCIDENT_REMINDER_SECONDS="0")
        h.set_timer(timer_enabled="disabled")
        h.run(INCIDENT_SPOOL_DIR=str(spool))
        batch = lo.collect(spool, {}, lo.DEFAULT_MAX_BYTES)
        self.assertEqual(batch.rejected, 0)
        self.assertEqual(batch.malformed, 0)
        self.assertEqual([(e["component"], e["action"]) for e in batch.events],
                         [("web", "FIRING"), ("web", "REMINDER"), ("web", "RESOLVED"), ("monitor", "FIRING")])
        firing = batch.events[0]
        self.assertIn("password=<redacted>", firing["journal_excerpt"])
        self.assertIsNotNone(firing["journal_since"])
        self.assertEqual(firing["kind"], "service")
        self.assertEqual(batch.events[-1]["kind"], "monitor_blind")
        self.assertEqual(len({e["incident_id"] for e in batch.events[:3]}), 1)


class CollectorLoaderFormatTests(unittest.TestCase):
    """收集器寫出來的紀錄，loader 的驗證必須全部接受（兩端同一份格式）。"""

    def test_collector_records_pass_loader_validation(self):
        from scripts import collect_resource_usage as col

        target = {"name": "sync", "kind": "systemd", "tier": "important", "unit": "report-mark-sync.service",
                  "timer": "report-mark-sync.timer", "container": None}
        obs = col.Observer([target], None, host="office-host", fs_paths=["/"])
        props = {"Id": "report-mark-sync.service", "ActiveState": "inactive", "InvocationID": INV,
                 "Result": "success", "ExecMainCode": "1", "ExecMainStatus": "0",
                 "ExecMainStartTimestamp": "@1791270010", "ExecMainStartTimestampMonotonic": "1000",
                 "ExecMainExitTimestamp": "@1791270100", "ExecMainExitTimestampMonotonic": "2000"}
        with mock.patch.object(col, "systemd_show", return_value={"report-mark-sync.service": props}):
            recs = obs.records({"host": {"ncpu": 2, "cpu_cores": 0.5}, "comp": {}})
        kinds = {r["type"] for r in recs}
        self.assertEqual(kinds, {"observation", "job"})
        for rec in recs:
            if rec["type"] == "observation":
                rows = lo.ops_monitoring.observation_rows(rec)
                self.assertTrue(rows, rec)
            else:
                self.assertIsNotNone(lo.ops_monitoring.job_row(rec), rec)


if __name__ == "__main__":
    unittest.main()
