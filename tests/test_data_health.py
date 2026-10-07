"""資料健康的共用判讀與結果檔（app/services/data_health.py），以及兩支腳本把結果寫進結果檔。

不連 DB、不碰 R2：稽核用假 session＋替換 `run_audit`，對帳沿用 tests/test_reconcile_object_storage.py 的假儲存。
結果檔一律寫到 tempfile（`DATA_HEALTH_DIR`）；conftest 預設把它導向不存在的目錄（本檔最後一組測試釘住）。
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app.services import data_health as dh

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import db_audit  # noqa: E402

NOW = datetime(2026, 10, 6, 4, 0, tzinfo=timezone.utc)


class _TmpDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "health"
        patcher = mock.patch.dict(os.environ, {"DATA_HEALTH_DIR": str(self.dir)})
        patcher.start()
        self.addCleanup(patcher.stop)


class ResultFileTests(_TmpDir):
    def test_roundtrip_adds_metadata(self):
        self.assertTrue(dh.write_result(dh.RESULT_DB_AUDIT, {"exit_code": 0, "findings": []}, now=NOW))
        data, why = dh.read_result(dh.RESULT_DB_AUDIT)
        self.assertIsNone(why)
        self.assertEqual(data["exit_code"], 0)
        self.assertEqual(data["name"], dh.RESULT_DB_AUDIT)
        self.assertEqual(data["schema_version"], dh.SCHEMA_VERSION)
        self.assertEqual(data["written_at"], NOW.isoformat())
        # 原子寫入：目錄裡只有結果檔，沒有殘留的暫存檔。
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["db_audit.json"])

    def test_missing_is_not_an_error(self):
        self.assertEqual(dh.read_result(dh.RESULT_R2_RECONCILE), (None, "missing"))

    def test_corrupt_and_foreign_files_are_rejected(self):
        self.dir.mkdir(parents=True)
        (self.dir / "db_audit.json").write_text("{not json", encoding="utf-8")
        self.assertTrue(dh.read_result(dh.RESULT_DB_AUDIT)[1].startswith("corrupt"))
        (self.dir / "db_audit.json").write_text(json.dumps({"name": "r2_reconcile"}), encoding="utf-8")
        self.assertEqual(dh.read_result(dh.RESULT_DB_AUDIT), (None, "corrupt:shape"))

    def test_oversized_file_is_not_read(self):
        self.dir.mkdir(parents=True)
        (self.dir / "db_audit.json").write_text("x" * (dh.MAX_RESULT_BYTES + 1), encoding="utf-8")
        self.assertEqual(dh.read_result(dh.RESULT_DB_AUDIT), (None, "too_large"))

    def test_write_failure_is_fail_open(self):
        with mock.patch.dict(os.environ, {"DATA_HEALTH_DIR": "/nonexistent/report-mark-data-health-test"}), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertFalse(dh.write_result(dh.RESULT_DB_AUDIT, {"exit_code": 0}))
        self.assertIn("不影響本次結果", err.getvalue())

    def test_unknown_name_is_rejected(self):
        with self.assertRaises(ValueError):
            dh.result_path("../etc/passwd")


class VerdictTests(unittest.TestCase):
    def test_warn_counts_as_failure(self):
        self.assertTrue(dh.audit_failed([{"severity": "warn", "count": 1}]))
        self.assertTrue(dh.audit_failed([db_audit.Finding("k", "l", db_audit.SEVERITY_WARN, 2, "d")]))
        self.assertFalse(dh.audit_failed([{"severity": "error", "count": 0}]))
        self.assertFalse(dh.audit_failed([]))

    def test_audit_status(self):
        self.assertEqual(dh.audit_status(None), dh.STATUS_UNKNOWN)
        self.assertEqual(dh.audit_status({"error": "DB 不可用（OSError）", "findings": None}), dh.STATUS_UNKNOWN)
        self.assertEqual(dh.audit_status({"findings": [{"count": 0}]}), dh.STATUS_OK)
        self.assertEqual(dh.audit_status({"findings": [{"count": 0}, {"count": 3}]}), dh.STATUS_FAIL)

    def test_reconcile_status(self):
        clean = dict.fromkeys(dh.RECONCILE_FAIL_KEYS + dh.RECONCILE_WARN_KEYS, 0)
        self.assertEqual(dh.reconcile_status(None), dh.STATUS_UNKNOWN)
        self.assertEqual(dh.reconcile_status({"mode": "local"}), dh.STATUS_OK)
        self.assertEqual(dh.reconcile_status({"mode": "r2", "exit_code": 0, "stats": clean}), dh.STATUS_OK)
        self.assertEqual(dh.reconcile_status({"mode": "r2", "exit_code": 0, "stats": {**clean, "orphans": 2}}),
                         dh.STATUS_WARN)
        self.assertEqual(dh.reconcile_status({"mode": "r2", "exit_code": 1, "stats": {**clean, "unkeyed": 1}}),
                         dh.STATUS_FAIL)
        self.assertEqual(dh.reconcile_status({"mode": "r2", "exit_code": 0}), dh.STATUS_UNKNOWN)

    def test_staleness_follows_schedule(self):
        fresh = (NOW - timedelta(hours=20)).isoformat()
        old = (NOW - timedelta(hours=60)).isoformat()
        self.assertFalse(dh.is_stale(dh.RESULT_DB_AUDIT, fresh, NOW))
        self.assertTrue(dh.is_stale(dh.RESULT_DB_AUDIT, old, NOW))
        self.assertFalse(dh.is_stale(dh.RESULT_R2_RECONCILE, old, NOW))  # 每週一次，60 小時還不算過期
        self.assertTrue(dh.is_stale(dh.RESULT_R2_RECONCILE, (NOW - timedelta(days=10)).isoformat(), NOW))
        # 讀不到或在未來：不能讓壞掉的時鐘把燈變綠。
        self.assertTrue(dh.is_stale(dh.RESULT_DB_AUDIT, None, NOW))
        self.assertTrue(dh.is_stale(dh.RESULT_DB_AUDIT, (NOW + timedelta(hours=5)).isoformat(), NOW))

    def test_worst(self):
        self.assertEqual(dh.worst([dh.STATUS_OK, dh.STATUS_WARN, dh.STATUS_UNKNOWN]), dh.STATUS_WARN)
        self.assertEqual(dh.worst([dh.STATUS_OK, dh.STATUS_FAIL]), dh.STATUS_FAIL)
        self.assertEqual(dh.worst([]), dh.STATUS_UNKNOWN)


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _audit_args(**kw):
    return argparse.Namespace(skip=kw.get("skip", []), norm_sample=kw.get("norm_sample", 500), json=False)


class DbAuditWritesResultTests(_TmpDir):
    def _run(self, *, findings=None, boom: Exception | None = None, args=None):
        import app.services.db as db

        async def fake_run_audit(session, *, skip, norm_sample):
            return findings

        def factory():
            if boom is not None:
                raise boom
            return _Session()

        out = io.StringIO()
        with mock.patch.object(db, "SessionFactory", factory), \
             mock.patch.object(db_audit, "run_audit", fake_run_audit), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = asyncio.run(db_audit._main(args or _audit_args()))
        return rc, dh.read_result(dh.RESULT_DB_AUDIT)[0]

    def test_warn_only_run_is_failure_in_rc_and_result(self):
        findings = [
            db_audit.Finding("null_embedding", "缺 embedding", db_audit.SEVERITY_ERROR, 0, "說明"),
            db_audit.Finding("chunkless_report", "沒有 chunk", db_audit.SEVERITY_WARN, 4, "說明"),
        ]
        rc, result = self._run(findings=findings, args=_audit_args(skip=["norm_drift"]))
        self.assertEqual(rc, db_audit.EXIT_FINDINGS)
        self.assertEqual(result["exit_code"], db_audit.EXIT_FINDINGS)
        self.assertFalse(result["ok"])
        self.assertIsNone(result["error"])
        self.assertEqual(result["skipped"], ["norm_drift"])
        self.assertEqual([(f["key"], f["count"]) for f in result["findings"]],
                         [("null_embedding", 0), ("chunkless_report", 4)])
        self.assertEqual(dh.audit_status(result), dh.STATUS_FAIL)

    def test_clean_run(self):
        rc, result = self._run(findings=[db_audit.Finding("k", "l", db_audit.SEVERITY_ERROR, 0, "d")])
        self.assertEqual(rc, db_audit.EXIT_OK)
        self.assertTrue(result["ok"])
        self.assertEqual(dh.audit_status(result), dh.STATUS_OK)

    def test_db_failure_records_type_only(self):
        rc, result = self._run(boom=OSError("connect to secret-host:5432 failed"))
        self.assertEqual(rc, db_audit.EXIT_UNKNOWN)
        self.assertEqual(result["exit_code"], db_audit.EXIT_UNKNOWN)
        self.assertIsNone(result["findings"])
        self.assertIn("OSError", result["error"])
        self.assertNotIn("secret-host", json.dumps(result, ensure_ascii=False))
        self.assertEqual(dh.audit_status(result), dh.STATUS_UNKNOWN)

    def test_unwritable_result_does_not_change_rc(self):
        with mock.patch.dict(os.environ, {"DATA_HEALTH_DIR": "/nonexistent/report-mark-data-health-test"}):
            rc, _ = self._run(findings=[db_audit.Finding("k", "l", db_audit.SEVERITY_WARN, 1, "d")])
        self.assertEqual(rc, db_audit.EXIT_FINDINGS)


class ReconcileWritesResultTests(_TmpDir):
    def setUp(self):
        super().setUp()
        import test_reconcile_object_storage as trs

        self.trs = trs

    def _run(self, rows, storage, *, limit=0):
        with contextlib.redirect_stdout(io.StringIO()):
            rc, _out = asyncio.run(self.trs._run_with(rows, storage, "all", limit))
        return rc, dh.read_result(dh.RESULT_R2_RECONCILE)[0]

    def test_missing_and_orphan_are_counted_and_sampled(self):
        digest = hashlib.sha256(b"original").hexdigest()
        key = f"originals/{digest[:2]}/{digest}.pdf"
        orphan = "originals/ff/orphan.pdf"
        from app.services.object_storage import ObjectNotFound

        storage = self.trs._Storage({key: ObjectNotFound(key)}, {}, {orphan})
        rc, result = self._run(
            [{"kind": "original", "id": "r1", "hash": digest, "name": "a.pdf", "path": None, "key": key}], storage)
        self.assertEqual(rc, 0)
        self.assertEqual(result["mode"], "r2")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["orphan_scan"], "done")
        self.assertEqual((result["stats"]["missing"], result["stats"]["orphans"]), (1, 1))
        self.assertEqual(result["issues"], [{"type": "missing", "ref": key}, {"type": "orphan", "ref": orphan}])
        self.assertEqual(result["issues_total"], 2)
        self.assertTrue(result["dry_run"])
        self.assertEqual(dh.reconcile_status(result), dh.STATUS_WARN)

    def test_limited_run_skips_orphan_scan_and_fails_on_unkeyed(self):
        storage = self.trs._Storage({}, {}, set())
        rc, result = self._run(
            [{"kind": "original", "id": "r9", "hash": "a" * 64, "name": "a.pdf", "path": None, "key": None}],
            storage, limit=1)
        self.assertEqual(rc, 1)
        self.assertEqual(result["orphan_scan"], "skipped")
        self.assertEqual(result["issues"], [{"type": "unkeyed", "ref": "id=r9"}])
        self.assertEqual(dh.reconcile_status(result), dh.STATUS_FAIL)

    def test_issue_samples_are_capped(self):
        storage = self.trs._Storage({}, {}, {f"originals/ff/o{i}.pdf" for i in range(dh.MAX_RECONCILE_ISSUES + 7)})
        _rc, result = self._run([], storage)
        self.assertEqual(len(result["issues"]), dh.MAX_RECONCILE_ISSUES)
        self.assertEqual(result["issues_total"], dh.MAX_RECONCILE_ISSUES + 7)
        self.assertEqual(result["stats"]["orphans"], dh.MAX_RECONCILE_ISSUES + 7)

    def test_local_mode_writes_ok_result(self):
        class _Local:
            enabled = False

        rc, result = self._run([], _Local())
        self.assertEqual(rc, 0)
        self.assertEqual(result["mode"], "local")
        self.assertEqual(dh.reconcile_status(result), dh.STATUS_OK)


class ConftestGuardTests(unittest.TestCase):
    def test_conftest_redirects_result_dir_by_assignment(self):
        """`data/health/` 落在部署目錄：測試寫進去會讓管理頁顯示假的稽核／對帳結果。"""
        conftest = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        self.assertIn('os.environ["DATA_HEALTH_DIR"] = ', conftest)
        self.assertFalse(str(dh.results_dir()).startswith(str(REPO_ROOT)))


if __name__ == "__main__":
    unittest.main()
