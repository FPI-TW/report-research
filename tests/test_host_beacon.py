"""主機信標（`app/services/host_beacon.py`）：發布端的組裝、監看端的驗證與判讀、兩端詞彙的對帳。

不連網：R2 以假物件代替（`get_status_object` 回 bytes 與 LastModified）。HTTP 契約在 tests/test_healthz_feed.py，
發布腳本在 tests/test_publish_host_beacon.py，sync 殼寫出的來源段結果檔在 tests/test_pipeline_heartbeat.py。
"""
from __future__ import annotations

import json
import os
import tempfile
import typing
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fake_ops_agent import POSTGRES, STATUS

from app import config
from app.services import host_beacon as hb
from app.services.object_storage import ObjectNotFound, ObjectStorageError
from web.routers import admin_ops

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
PUBLISHED = 1_791_000_000


def _sync(**kw) -> dict:
    base = {
        "source": "nas", "inbox_push": True, "timer_active": True,
        "round": {"state": "done", "started_epoch": PUBLISHED - 600, "updated_epoch": PUBLISHED - 500, "rc": 0},
        "feed": {"round": "20261012_150003", "epoch": PUBLISHED - 590, "result": "ok", "new_files": 3, "push": "ok"},
        "heartbeat_epoch": PUBLISHED - 400, "pending_replays": 0,
    }
    base.update(kw)
    return base


def _built(**sync_kw) -> dict:
    """`build` 以服務清單裡的 sync timer 為準覆寫 timer_active；這裡沒有服務清單，直接放回要測的值。"""
    sync = _sync(**sync_kw)
    beacon = hb.build(name="office", host="office-pc", published_epoch=PUBLISHED, sync=sync, services=[],
                      services_error=None)
    beacon["sync"]["timer_active"] = sync["timer_active"]
    return beacon


def _beacon(**sync_kw) -> dict:
    return hb.parse(hb.encode(_built(**sync_kw)))


def _eval(beacon: dict, age: float = 60) -> list[str]:
    return hb.evaluate(beacon, age_seconds=age, stale_minutes=45, feed_stale_hours=7)


class _Storage:
    enabled = True

    def __init__(self, data: bytes | Exception, modified: datetime = NOW - timedelta(minutes=3)):
        self.data, self.modified, self.keys = data, modified, []

    def get_status_object(self, key: str, *, max_bytes: int):
        self.keys.append((key, max_bytes))
        if isinstance(self.data, Exception):
            raise self.data
        return self.data, self.modified


class EvaluateTests(unittest.TestCase):
    def test_healthy_round_has_no_reasons(self):
        self.assertEqual(_eval(_beacon()), [])

    def test_no_new_files_is_healthy(self):
        """週末沒有新稿是常態：來源段有結論、推送是 none，不告警。"""
        feed = {"round": "20261012_150003", "epoch": PUBLISHED - 590, "result": "ok", "new_files": 0, "push": "none"}
        self.assertEqual(_eval(_beacon(feed=feed)), [])

    def test_stale_beacon_short_circuits(self):
        """信標過期時裡面都是舊聞：只回 stale，不把舊內容再判一輪。"""
        beacon = _beacon(inbox_push=False, pending_replays=2)
        self.assertEqual(_eval(beacon, age=45 * 60 + 1), ["stale"])
        self.assertEqual(_eval(beacon, age=45 * 60), ["push_disabled"])

    def test_each_rule(self):
        feed = _sync()["feed"]
        cases = {
            "timer_inactive": {"timer_active": False},
            "sync_stalled": {"feed": None},
            "source_failed": {"feed": {**feed, "result": "mount_failed", "push": "none"}},
            "push_disabled": {"inbox_push": False},
            "push_failed": {"feed": {**feed, "push": "failed"}},
            "round_aborted": {"round": {"state": "aborted", "started_epoch": PUBLISHED - 60, "updated_epoch": None,
                                        "rc": None}},
        }
        for state, kw in cases.items():
            with self.subTest(state=state):
                self.assertEqual(_eval(_beacon(**kw)), [state])
        self.assertEqual(_eval(_beacon(feed={**feed, "result": "rsync_failed"})), ["source_failed"])
        self.assertEqual(_eval(_beacon(pending_replays=1)), ["push_failed"])

    def test_feed_age_counts_from_publisher_clock_difference_plus_beacon_age(self):
        """來源段的年齡＝（發布時刻−結果時刻）＋信標年齡；發布端時鐘整體偏掉不影響。"""
        feed = {**_sync()["feed"], "epoch": PUBLISHED - 7 * 3600 + 120}
        self.assertEqual(_eval(_beacon(feed=feed), age=60), [])
        self.assertEqual(_eval(_beacon(feed=feed), age=180), ["sync_stalled"])
        # 結果比發布時刻還「新」（同一個時鐘不該發生）：差當 0，不變成負的年齡。
        self.assertEqual(_eval(_beacon(feed={**feed, "epoch": PUBLISHED + 999}), age=60), [])

    def test_multiple_reasons_keep_priority_order(self):
        beacon = _beacon(timer_active=False, feed=None, pending_replays=3,
                         round={"state": "aborted", "started_epoch": None, "updated_epoch": None, "rc": None})
        self.assertEqual(_eval(beacon), ["timer_inactive", "sync_stalled", "push_failed", "round_aborted"])

    def test_unknown_timer_state_is_not_an_alert(self):
        """維運代理問不到時 timer_active 是 None：不知道不等於停了（來源段結果照常判）。"""
        self.assertEqual(_eval(_beacon(timer_active=None)), [])

    def test_non_nas_source_only_judges_staleness(self):
        beacon = _beacon(source="r2-inbox", inbox_push=False, feed=None)
        self.assertEqual(_eval(beacon), [])
        self.assertEqual(_eval(beacon, age=3600), ["stale"])

    def test_import_failure_is_not_a_feed_alert(self):
        """匯入失敗（輪次 failed）不影響推給另一台的檔：那是來源那台自己的事。"""
        rnd = {"state": "failed", "started_epoch": PUBLISHED - 600, "updated_epoch": PUBLISHED - 100, "rc": 1}
        self.assertEqual(_eval(_beacon(round=rnd)), [])


class ParseTests(unittest.TestCase):
    def _raw(self, **over) -> bytes:
        beacon = hb.build(name="office", host="office-pc", published_epoch=PUBLISHED, sync=_sync(), services=[],
                          services_error=None)
        beacon.update(over)
        return json.dumps(beacon).encode()

    def test_rejects_what_it_cannot_read(self):
        for data in (b"", b"not json", b"[]", b"\xff\xfe", self._raw(v=2), self._raw(published_epoch="x"),
                     self._raw(published_epoch=-1), self._raw(published_epoch=True), self._raw(sync=None)):
            with self.subTest(data=data[:30]):
                with self.assertRaises(hb.BeaconError):
                    hb.parse(data)

    def test_unrecognised_values_fall_to_the_alerting_side(self):
        """bucket 裡的內容不是可信輸入：認不得的值不猜成正常。"""
        sync = _sync(source="tape", inbox_push="yes", timer_active="no", pending_replays=-4,
                     feed={"epoch": PUBLISHED, "result": "great", "push": "sure", "new_files": "many", "round": "../x"},
                     round={"state": "exploded", "rc": 9999, "started_epoch": "soon"})
        parsed = hb.parse(self._raw(sync=sync))["sync"]
        self.assertEqual(parsed["source"], "nas")
        self.assertIs(parsed["inbox_push"], False)
        self.assertIsNone(parsed["timer_active"])
        self.assertEqual(parsed["pending_replays"], 0)
        self.assertEqual(parsed["feed"], {"round": None, "epoch": PUBLISHED, "result": "rsync_failed", "new_files": 0,
                                          "push": "failed"})
        self.assertEqual(parsed["round"], {"state": "unknown", "started_epoch": None, "updated_epoch": None,
                                           "rc": None})

    def test_services_are_sanitised_and_capped(self):
        good = {"name": "web", "kind": "systemd", "tier": "critical", "summary": "running", "health": "ok",
                "health_reason": "執行中", "description": "x" * 500, "timer": None, "timer_active": None,
                "last_run_at": "2026-10-12T07:00:00+00:00", "next_run_at": None, "exit_status": 0}
        services = [good, {"name": "Bad Name"}, "junk", {**good, "name": "odd", "kind": "vm", "tier": "x",
                                                         "summary": "?", "health": "?", "exit_status": 999}]
        services += [{**good, "name": f"s{i}"} for i in range(hb.MAX_SERVICES + 5)]
        parsed = hb.parse(self._raw(services=services))["services"]
        self.assertEqual(len(parsed), hb.MAX_SERVICES - 2)  # 前 MAX_SERVICES 筆裡有兩筆被丟掉
        self.assertEqual(len(parsed[0]["description"]), 200)
        self.assertEqual({k: parsed[1][k] for k in ("name", "kind", "tier", "summary", "health", "exit_status")},
                         {"name": "odd", "kind": "systemd", "tier": "supporting", "summary": "unknown",
                          "health": "unknown", "exit_status": None})
        self.assertIsNone(hb.parse(self._raw(services=None))["services"])

    def test_encode_refuses_oversized_beacon(self):
        beacon = hb.build(name="office", host="h", published_epoch=PUBLISHED, sync=_sync(),
                          services=[{"name": "x", "description": "y" * 2000}] * 400, services_error=None)
        with self.assertRaises(hb.BeaconError):
            hb.encode(beacon)

    def test_object_key_rejects_unsafe_names(self):
        self.assertEqual(hb.object_key("office"), "beacons/office.json")
        for name in ("", "Office", "../x", "a/b", "a" * 41, "1abc"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    hb.object_key(name)


class CollectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.data = Path(self._tmp.name)

    def _write(self, name: str, **kv):
        (self.data / name).write_text("".join(f"{k}={v}\n" for k, v in kv.items()), encoding="utf-8")

    def test_empty_data_dir(self):
        sync = hb.collect_sync(self.data, {})
        self.assertEqual((sync["source"], sync["inbox_push"], sync["feed"], sync["heartbeat_epoch"],
                          sync["pending_replays"], sync["round"]["state"]), ("nas", False, None, None, 0, "unknown"))

    def test_reads_state_files(self):
        self._write("sync_round_state", state="done", started_at="2026-10-12T15:00:03+08:00", started_epoch=1791000000,
                    pid=4242, log="data/sync_run_20261012.log", signal="", rc=0, updated_at="2026-10-12T15:02:00+08:00")
        self._write(".sync_feed_state", round="20261012_150003", epoch=1791000060, result="ok", new_files=5, push="ok")
        self._write(".last_successful_sync", ts="x", epoch=1791000120, new_reports=5, pid=4242)
        (self.data / "inbox_delta_retained_20261011_120000.txt").write_text("a.pdf\n", encoding="utf-8")
        (self.data / "inbox_delta_retained_20261011_150000.txt").write_text("b.pdf\n", encoding="utf-8")
        sync = hb.collect_sync(self.data, {"SYNC_SOURCE": "nas", "SYNC_INBOX_PUSH": "1"})
        self.assertIs(sync["inbox_push"], True)
        self.assertEqual(sync["round"]["state"], "done")
        self.assertEqual(sync["round"]["started_epoch"], 1791000000)
        self.assertEqual(sync["round"]["rc"], 0)
        self.assertEqual(sync["round"]["updated_epoch"],
                         int(datetime.fromisoformat("2026-10-12T15:02:00+08:00").timestamp()))
        self.assertEqual(sync["feed"], {"round": "20261012_150003", "epoch": 1791000060, "result": "ok",
                                        "new_files": 5, "push": "ok"})
        self.assertEqual(sync["heartbeat_epoch"], 1791000120)
        self.assertEqual(sync["pending_replays"], 2)

    def test_running_round_whose_process_is_gone_is_aborted(self):
        self._write("sync_round_state", state="running", started_epoch=1791000000, pid=4242, rc="")
        self.assertEqual(hb.collect_sync(self.data, {}, alive=lambda _pid: True)["round"]["state"], "running")
        self.assertEqual(hb.collect_sync(self.data, {}, alive=lambda _pid: False)["round"]["state"], "aborted")

    def test_signalled_round_is_aborted(self):
        self._write("sync_round_state", state="signalled", started_epoch=1791000000, pid=4242, signal="TERM", rc=143)
        rnd = hb.collect_sync(self.data, {})["round"]
        self.assertEqual((rnd["state"], rnd["rc"]), ("aborted", 143))

    def test_garbage_in_state_files_does_not_become_ok(self):
        self._write(".sync_feed_state", round="$(rm -rf /)", epoch=1791000060, result="", new_files="x", push="")
        self.assertEqual(hb.collect_sync(self.data, {})["feed"],
                         {"round": None, "epoch": 1791000060, "result": "rsync_failed", "new_files": 0,
                          "push": "failed"})
        self._write(".sync_feed_state", result="ok")  # 沒有 epoch＝當作沒有這個檔
        self.assertIsNone(hb.collect_sync(self.data, {})["feed"])


class ProjectServicesTests(unittest.TestCase):
    def test_projection_and_sync_timer(self):
        sync_item = {
            "name": "sync", "kind": "systemd", "tier": "important", "target": "report-mark-sync.service",
            "timer": hb.SYNC_TIMER_UNIT, "description": "NAS 增量同步匯入", "summary": "idle",
            "systemd": {"type": "oneshot", "exec_main_code": "exited", "exec_main_status": 0,
                        "exec_main_exit_at": "2026-10-12T07:02:00+00:00"},
            "timer_state": {"unit": hb.SYNC_TIMER_UNIT, "active_state": "inactive",
                            "next_elapse_at": None, "last_trigger_at": "2026-10-12T07:00:00+00:00"},
        }
        rows = hb.project_services({"items": [STATUS, POSTGRES, sync_item, "junk", {"kind": "systemd"}]})
        self.assertEqual([r["name"] for r in rows], ["web", "postgres", "sync"])
        self.assertEqual((rows[0]["health"], rows[0]["timer_active"]), ("ok", None))
        self.assertEqual((rows[2]["health"], rows[2]["timer_active"], rows[2]["exit_status"], rows[2]["last_run_at"]),
                         ("ok", False, 0, "2026-10-12T07:02:00+00:00"))
        self.assertIs(hb.sync_timer_active(rows), False)
        self.assertIsNone(hb.sync_timer_active(rows[:2]))
        self.assertIsNone(hb.sync_timer_active(None))
        beacon = hb.build(name="office", host="h", published_epoch=PUBLISHED, sync=_sync(timer_active=None),
                          services=rows, services_error=None)
        self.assertIs(beacon["sync"]["timer_active"], False)
        self.assertEqual(_eval(hb.parse(hb.encode(beacon))), ["timer_inactive"])


class ReadTests(unittest.TestCase):
    def _read(self, storage, name="office", now=NOW):
        return hb.read(storage, name, stale_minutes=45, feed_stale_hours=7, now=now)

    def _data(self, **sync_kw) -> bytes:
        return hb.encode(_built(**sync_kw))

    def test_ok_uses_last_modified_for_age_and_maps_times(self):
        storage = _Storage(self._data())
        reading = self._read(storage)
        self.assertEqual((reading.state, reading.reasons, reading.age_seconds), ("ok", (), 180))
        self.assertEqual(storage.keys, [("beacons/office.json", hb.MAX_BYTES)])
        detail = reading.detail(stale_minutes=45, feed_stale_hours=7)
        self.assertEqual(detail["host"], "office-pc")
        self.assertEqual(detail["published_at"], (NOW - timedelta(minutes=3)).isoformat())
        # 來源段結果在發布前 590 秒：換算到監看端的時間軸＝LastModified − 590 秒，與發布端時鐘的絕對值無關。
        self.assertEqual(detail["sync"]["feed_at"], (NOW - timedelta(minutes=3, seconds=590)).isoformat())
        self.assertEqual((detail["sync"]["feed_result"], detail["sync"]["push"], detail["sync"]["new_files"]),
                         ("ok", "ok", 3))

    def test_alert_state_is_first_reason(self):
        reading = self._read(_Storage(self._data(pending_replays=1, timer_active=False)))
        self.assertEqual((reading.state, reading.reasons), ("timer_inactive", ("timer_inactive", "push_failed")))

    def test_stale_by_last_modified(self):
        reading = self._read(_Storage(self._data(), modified=NOW - timedelta(minutes=46)))
        self.assertEqual((reading.state, reading.age_seconds), ("stale", 46 * 60))

    def test_missing_invalid_unknown_disabled(self):
        self.assertEqual(self._read(_Storage(ObjectNotFound("beacons/office.json"))).state, "missing")
        invalid = self._read(_Storage(b"{}"))
        self.assertEqual((invalid.state, invalid.reasons), ("invalid", ("invalid",)))
        self.assertIsNotNone(invalid.error)
        unknown = self._read(_Storage(ObjectStorageError("R2 get_object failed: secret-ish detail")))
        self.assertEqual(unknown.state, "unknown")
        self.assertNotIn("secret-ish", unknown.error or "")
        storage = _Storage(self._data())
        self.assertEqual(self._read(storage, name="").state, "disabled")
        self.assertEqual(storage.keys, [])
        local = _Storage(self._data())
        local.enabled = False
        self.assertEqual(self._read(local).state, "unknown")
        self.assertEqual(local.keys, [])

    def test_detail_without_beacon_keeps_shape(self):
        detail = self._read(_Storage(ObjectNotFound("x"))).detail(stale_minutes=45, feed_stale_hours=7)
        self.assertEqual((detail["state"], detail["reasons"], detail["sync"], detail["services"]),
                         ("missing", ["missing"], None, None))
        admin_ops.OpsRemoteHost.model_validate(detail)


class VocabularyTests(unittest.TestCase):
    """路由的 Literal 是前端 zod enum 的來源（scripts/gen_admin_client.py）：必須與判讀模組逐字一致。"""

    def test_router_literals_match_module(self):
        self.assertEqual(typing.get_args(admin_ops.RemoteState), hb.STATES)
        self.assertEqual(typing.get_args(admin_ops.RemoteAlert), hb.ALERT_STATES)
        fields = admin_ops.OpsRemoteSync.model_fields
        self.assertEqual(typing.get_args(fields["source"].annotation), hb.SOURCES)
        self.assertEqual(typing.get_args(fields["round_state"].annotation), hb.ROUND_STATES)
        self.assertEqual(typing.get_args(typing.get_args(fields["feed_result"].annotation)[0]), hb.FEED_RESULTS)
        self.assertEqual(typing.get_args(typing.get_args(fields["push"].annotation)[0]), hb.PUSH_RESULTS)

    def test_states_are_probe_safe(self):
        """探針以 sed 取 state，只收小寫字母與底線。"""
        for state in hb.STATES:
            self.assertRegex(state, r"^[a-z_]{1,32}$")
        self.assertEqual(len(set(hb.STATES)), len(hb.STATES))

    def test_full_detail_validates_against_response_model(self):
        beacon = hb.build(name="office", host="office-pc", published_epoch=PUBLISHED, sync=_sync(),
                          services=hb.project_services({"items": [STATUS, POSTGRES]}), services_error=None)
        reading = hb.read(_Storage(hb.encode(beacon)), "office", stale_minutes=45, feed_stale_hours=7, now=NOW)
        model = admin_ops.OpsRemoteHost.model_validate(reading.detail(stale_minutes=45, feed_stale_hours=7))
        self.assertEqual([s.name for s in model.services or []], ["web", "postgres"])


class SettingsTests(unittest.TestCase):
    def _load(self, **env):
        keys = ("HOST_BEACON_NAME", "HOST_BEACON_WATCH", "HOST_BEACON_STALE_MINUTES", "HOST_BEACON_FEED_STALE_HOURS")
        with mock.patch.dict(os.environ, env):
            for key in keys:
                if key not in env:
                    os.environ.pop(key, None)
            return config._load()

    def test_defaults_are_off(self):
        s = self._load()
        self.assertEqual((s.host_beacon_name, s.host_beacon_watch, s.host_beacon_stale_minutes,
                          s.host_beacon_feed_stale_hours), ("", "", 45, 7))

    def test_invalid_names_disable_instead_of_guessing(self):
        s = self._load(HOST_BEACON_NAME="../etc", HOST_BEACON_WATCH=" Office ")
        self.assertEqual((s.host_beacon_name, s.host_beacon_watch), ("", "office"))

    def test_thresholds_have_floors(self):
        """門檻打太小會變成每一班都告警：低於下限退回預設。"""
        s = self._load(HOST_BEACON_STALE_MINUTES="5", HOST_BEACON_FEED_STALE_HOURS="1")
        self.assertEqual((s.host_beacon_stale_minutes, s.host_beacon_feed_stale_hours), (45, 7))


if __name__ == "__main__":
    unittest.main()
