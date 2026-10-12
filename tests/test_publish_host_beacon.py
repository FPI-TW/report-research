"""信標發布腳本（`scripts/publish_host_beacon.py`）：退出碼、寫到哪個 key、維運代理問不到時照樣發布。

不連網：R2 以假物件代替，維運代理用 tests/fake_ops_agent.py 的假 socket。信標的內容與判讀在 tests/test_host_beacon.py。
"""
from __future__ import annotations

import dataclasses
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from fake_ops_agent import FakeOpsAgent

from app.config import get_settings
from app.services import host_beacon
from app.services.object_storage import ObjectStorageError

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import publish_host_beacon as pub  # noqa: E402


class _Storage:
    def __init__(self, *, enabled: bool = True, fail: bool = False):
        self.enabled, self.fail, self.puts = enabled, fail, []

    def put_status_object(self, key: str, data: bytes) -> None:
        if self.fail:
            raise ObjectStorageError("R2 put_object failed: boom")
        self.puts.append((key, data))


class PublishTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)
        (self.data / ".sync_feed_state").write_text(
            "round=20261012_150003\nepoch=1791000060\nresult=ok\nnew_files=2\npush=ok\n", encoding="utf-8")

    def _main(self, *argv, name="office", storage=None, agent=True):
        storage = storage or _Storage()
        settings = dataclasses.replace(get_settings(), host_beacon_name=name)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pub, "get_settings", return_value=settings), \
                mock.patch.object(pub, "get_object_storage", return_value=storage), \
                mock.patch.object(pub, "DATA_DIR", self.data), \
                mock.patch.dict("os.environ", {"SYNC_SOURCE": "nas", "SYNC_INBOX_PUSH": "1"}), \
                mock.patch.object(sys, "argv", ["publish_host_beacon.py", *argv]), \
                redirect_stdout(out), redirect_stderr(err):
            if agent:
                with FakeOpsAgent() as fake, fake.installed():
                    rc = pub.main()
            else:
                with mock.patch.object(pub.ops_client, "default_client",
                                       return_value=pub.ops_client.OpsClient("/nonexistent/agent.sock", "production")):
                    rc = pub.main()
        return rc, storage, out.getvalue(), err.getvalue()

    def test_not_configured_does_nothing(self):
        rc, storage, _, err = self._main(name="")
        self.assertEqual((rc, storage.puts), (0, []))
        self.assertIn("HOST_BEACON_NAME", err)

    def test_publishes_to_named_key(self):
        rc, storage, _, err = self._main()
        self.assertEqual(rc, 0, err)
        ((key, data),) = storage.puts
        self.assertEqual(key, "beacons/office.json")
        beacon = host_beacon.parse(data)
        self.assertEqual((beacon["name"], beacon["sync"]["inbox_push"], beacon["sync"]["feed"]["push"]),
                         ("office", True, "ok"))
        self.assertEqual([s["name"] for s in beacon["services"]], ["web", "postgres"])
        self.assertIsNone(beacon["services_error"])

    def test_agent_unavailable_still_publishes(self):
        """來源段的判斷靠 data/ 的狀態檔，不靠代理：代理問不到不擋發布。"""
        rc, storage, _, _ = self._main(agent=False)
        self.assertEqual(rc, 0)
        beacon = host_beacon.parse(storage.puts[0][1])
        self.assertIsNone(beacon["services"])
        self.assertIn("維運代理", beacon["services_error"])
        self.assertEqual(beacon["sync"]["feed"]["result"], "ok")
        self.assertIsNone(beacon["sync"]["timer_active"])

    def test_dry_run_prints_and_does_not_write(self):
        rc, storage, out, _ = self._main("--dry-run")
        self.assertEqual((rc, storage.puts), (0, []))
        self.assertEqual(json.loads(out)["v"], host_beacon.SCHEMA_VERSION)

    def test_local_storage_is_rc2_and_write_failure_is_rc1(self):
        rc, storage, _, err = self._main(storage=_Storage(enabled=False))
        self.assertEqual((rc, storage.puts), (2, []))
        self.assertIn("OBJECT_STORAGE_MODE=local", err)
        rc, _, _, err = self._main(storage=_Storage(fail=True))
        self.assertEqual(rc, 1)
        self.assertIn("發布失敗", err)

    def test_does_not_read_repo_env_or_load_models(self):
        """批次不讀 repo 根 .env（信標要回報 sync 實際的設定），也不載檢索／嵌入模組。"""
        src = (REPO_ROOT / "scripts" / "publish_host_beacon.py").read_text(encoding="utf-8")
        code = "\n".join(line for line in src.splitlines() if line.startswith(("import ", "from ")))
        for word in ("env_loader", "embedding", "retrieval", "answer", "torch"):
            self.assertNotIn(word, code)


if __name__ == "__main__":
    unittest.main()
