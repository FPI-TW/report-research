"""`scripts/db_snapshot.py` 的失敗語意、import 守門與 `report-mark-db-snapshot.{service,timer}` 的靜態守門（不連 DB）。

快照的 SQL（逐時寫入、每日彙總、保留期刪除）對真 DB 的驗證在 tests/test_db_insights_db.py。這裡釘：
- 退出碼：正常 0（有 commit）、DB 不可用 2、其他失敗 1（有 rollback、沒有 commit）；`--dry-run` 不寫、不 commit。
- 子行程 import 腳本後**沒有載入**檢索、嵌入、LLM 模組與 torch（主機記憶體緊；設計 §6 的守門）。
- unit：OnFailure 走 report-mark-alert@、SuccessExitStatus 只放行 2、跑的是這支腳本；timer 不在整點（錯開 sync 的
  每 3 小時整點）也不在 :20（監控聚合）、不補跑。
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import db_snapshot as ds  # noqa: E402

SYSTEMD = REPO_ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD / "report-mark-db-snapshot.service"
TIMER = SYSTEMD / "report-mark-db-snapshot.timer"


def _directives(path: Path, key: str) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s.startswith("#") or "=" not in s:
            continue
        name, _, value = s.partition("=")
        if name.strip() == key:
            out.append(value.strip())
    return out


class _Session:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _Engine:
    async def dispose(self):
        return None


def _run(args, run_snapshot=None, collect=None):
    session = _Session()
    out, err = io.StringIO(), io.StringIO()
    patches = []
    if run_snapshot is not None:
        patches.append(mock.patch.object(ds.db_insights, "run_snapshot", run_snapshot))
    if collect is not None:
        patches.append(mock.patch.object(ds.db_insights, "collect_overview", collect))
    for p in patches:
        p.start()
    try:
        with redirect_stdout(out), redirect_stderr(err):
            rc = ds.run(args, engine=_Engine(), session_factory=lambda: session)
    finally:
        for p in patches:
            p.stop()
    return rc, session, out.getvalue(), err.getvalue()


class ExitCodeTests(unittest.TestCase):
    def test_ok_commits(self):
        async def fake(session, *, now):
            return ds.db_insights.SnapshotResult(inserted=True)

        rc, session, out, _ = _run(Namespace(dry_run=False), run_snapshot=fake)
        self.assertEqual((rc, session.commits, session.rollbacks), (0, 1, 0))
        self.assertIn("已寫入", out)

    def test_sql_failure_is_rc1_and_rolls_back(self):
        async def fake(session, *, now):
            raise RuntimeError("CHECK violation")

        rc, session, _, err = _run(Namespace(dry_run=False), run_snapshot=fake)
        self.assertEqual((rc, session.commits, session.rollbacks), (1, 0, 1))
        self.assertIn("rollback", err)

    def test_db_unavailable_is_rc2(self):
        async def fake(session, *, now):
            raise ConnectionRefusedError("no db")

        rc, session, _, err = _run(Namespace(dry_run=False), run_snapshot=fake)
        self.assertEqual((rc, session.commits), (2, 0))
        self.assertIn("DB 不可用", err)

    def test_dry_run_writes_nothing(self):
        async def collect(session, *, now):
            return {"generated_at": now.isoformat(), "database": {"error": None, "size_bytes": 1}}

        async def must_not_run(session, *, now):
            raise AssertionError("dry-run 不得寫入")

        rc, session, out, _ = _run(Namespace(dry_run=True), run_snapshot=must_not_run, collect=collect)
        self.assertEqual((rc, session.commits, session.rollbacks), (0, 0, 1))
        payload = json.loads(out.split(" ", 1)[1])
        self.assertEqual(payload["gauges"], {"db_size_bytes": 1})


class ImportGuardTests(unittest.TestCase):
    def test_script_does_not_load_retrieval_embedding_llm_or_torch(self):
        code = (
            "import importlib.util, json, sys\n"
            "spec = importlib.util.spec_from_file_location('db_snapshot', 'scripts/db_snapshot.py')\n"
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
            "print(json.dumps(sorted(sys.modules)))\n"
        )
        env = {**os.environ, "REPORT_MARK_DB_URL": "postgresql+asyncpg://nobody:x@127.0.0.1:45999/none"}
        res = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, env=env, capture_output=True, text=True,
                             timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        loaded = set(json.loads(res.stdout.strip().splitlines()[-1]))
        forbidden_roots = {"torch", "FlagEmbedding", "transformers", "sentence_transformers"}
        forbidden = {
            "app.services.retrieval", "app.services.retrieval_pipeline", "app.services.embed", "app.services.rerank",
            "app.services.store", "app.services.answer", "app.services.llm", "app.services.llm_http",
        }
        self.assertFalse({m for m in loaded if m.split(".")[0] in forbidden_roots})
        self.assertFalse(loaded & forbidden, sorted(loaded & forbidden))


class UnitFileTests(unittest.TestCase):
    def test_service_failure_semantics(self):
        self.assertEqual(_directives(SERVICE, "OnFailure"), ["report-mark-alert@%n.service"])
        self.assertEqual(_directives(SERVICE, "SuccessExitStatus"), ["2"])
        self.assertEqual(_directives(SERVICE, "Type"), ["oneshot"])
        (exec_start,) = _directives(SERVICE, "ExecStart")
        self.assertIn("scripts/db_snapshot.py", exec_start)
        self.assertIn("HOME=/home/kashionz", _directives(SERVICE, "Environment"))

    def test_timer_staggered_from_sync_and_rollup(self):
        (cal,) = _directives(TIMER, "OnCalendar")
        self.assertRegex(cal, r"^\*-\*-\* \*:(\d\d):00$")
        minute = int(cal.split(":")[1])
        self.assertNotIn(minute, (0, 20, 30), "整點是 sync（每 3 小時）、:20 是監控聚合、:30 有其他批次")
        self.assertEqual(_directives(TIMER, "Persistent"), [])


if __name__ == "__main__":
    unittest.main()
