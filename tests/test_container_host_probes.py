# tests/test_container_host_probes.py
"""容器與主機健康探針（`scripts/check_container_health.sh`、`scripts/check_host_health.sh`）的行為與契約。

兩支探針接到既有的 P5（`scripts/incident_handler.sh`）新實例，比照邊緣那一組的參數化方式。**依 tier 的
去抖做在探針裡**——P5 是生產 critical 告警唯一的 Slack 發送者，它的狀態機一行不動；探針把「還在確認期」
回成 3，由 P5 既有的 `INCIDENT_HOLD_EXIT_CODES=3`（hold：不開也不關）處理。這裡守：

- tier 去抖：critical 第一次確認就回 1；important 要連續 2 輪、supporting 連續 3 輪才回 9；之前回 3；
  中間有一輪正常就重新起算；兩次失敗相隔太久也重新起算；判不出來（docker 連不上、/proc 讀不到）連續 2 輪
  才回 4，而且不碰各項目的計數；記不住次數（狀態目錄寫不進去）時每筆失敗都算確認——寧可多吵也不靜默
- 端到端：探針的退出碼餵進 P5 的 fake systemctl，important 容器第一輪不開事件、第二輪才 FIRING WARNING；
  確認期不會關掉進行中的事件
- 契約：預設名單與 catalog 逐字一致、unit 的逾時與 SuccessExitStatus、incident 實例的參數、不依賴 Python

不連真的 docker：用假的 docker CLI（`DOCKER_BIN`）。主機探針用假的 /proc 樹（`HOST_PROC_ROOT`）。
"""
import os
import re
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_incident_handler import _Harness, last_emit  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTAINER_PROBE = REPO_ROOT / "scripts" / "check_container_health.sh"
HOST_PROBE = REPO_ROOT / "scripts" / "check_host_health.sh"
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
CATALOG = REPO_ROOT / "deploy" / "ops" / "services.prod.toml"

EXIT_OK, EXIT_CRITICAL, EXIT_PENDING, EXIT_TOOLING, EXIT_DEGRADED = 0, 1, 3, 4, 9


def _directives(path: Path, key: str) -> list[str]:
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            out.append(value.strip())
    return out


def parse(stdout: str) -> dict:
    line = [ln for ln in stdout.splitlines() if ln.startswith("ts=")][-1]
    return dict(kv.split("=", 1) for kv in line.split() if "=" in kv)


_FAKE_DOCKER = r"""#!/usr/bin/env bash
# 假 docker：`inspect --type container --format ... -- 名稱...`
dir="$(dirname "$0")"
n=$(( $(cat "$dir/calls" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$dir/calls"
mode="$(cat "$dir/mode" 2>/dev/null)"
case "$mode" in
  down) echo "Cannot connect to the Docker daemon at unix:///var/run/docker.sock" >&2; exit 1 ;;
  hang) sleep 30; exit 1 ;;
esac
# 第 n 次呼叫用 state.<n>，沒有就用 state；每行「容器 狀態 health」
f="$dir/state.$n"; [ -f "$f" ] || f="$dir/state"
seen_args=no; rc=0
for a in "$@"; do
  if [ "$seen_args" = yes ]; then
    line="$(grep -E "^$a " "$f" 2>/dev/null | head -1)"
    if [ -z "$line" ]; then echo "Error: No such object: $a" >&2; rc=1
    else set -- $line; echo "/$1 $2 ${3:-none}"; fi
  fi
  [ "$a" = "--" ] && seen_args=yes
done
exit $rc
"""


class _Docker:
    def __init__(self, root: Path):
        self.dir = root / "docker"
        self.dir.mkdir()
        self.bin = self.dir / "docker"
        self.bin.write_text(_FAKE_DOCKER, encoding="utf-8")
        self.bin.chmod(0o755)
        self.set({"report-mark-postgres": "running", "deploy-nginx-1": "running", "deploy-cloudflared-1": "running"})

    def set(self, states: dict, attempt: int | None = None, mode: str = ""):
        lines = "".join(f"{c} {s.replace(':', ' ')}\n" for c, s in states.items())
        (self.dir / (f"state.{attempt}" if attempt else "state")).write_text(lines, encoding="utf-8")
        (self.dir / "mode").write_text(mode, encoding="utf-8")
        for f in self.dir.glob("calls"):
            f.unlink()
        if attempt is None:
            for f in self.dir.glob("state.*"):
                f.unlink()

    @property
    def calls(self) -> int:
        f = self.dir / "calls"
        return int(f.read_text()) if f.exists() else 0


class ContainerProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.docker = _Docker(self.root)
        self.streaks = self.root / "streaks"

    def run_probe(self, **env_extra) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({"DOCKER_BIN": str(self.docker.bin), "HEALTH_STREAK_DIR": str(self.streaks),
                    "CONTAINER_HEALTH_RETRY_WAIT": "0", "CONTAINER_HEALTH_TIMEOUT": "5"})
        env.update(env_extra)
        return subprocess.run(["bash", str(CONTAINER_PROBE)], capture_output=True, text=True, env=env, timeout=60)

    def test_all_running_is_ok(self):
        p = self.run_probe()
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)
        self.assertEqual(parse(p.stdout)["status"], "ok")

    def test_critical_container_down_fires_on_first_confirmed_run(self):
        self.docker.set({"report-mark-postgres": "exited", "deploy-nginx-1": "running",
                         "deploy-cloudflared-1": "running"})
        p = self.run_probe()
        self.assertEqual(p.returncode, EXIT_CRITICAL)
        out = parse(p.stdout)
        self.assertEqual((out["status"], out["down"], out["attempts"]), ("down", "postgres:exited", "2"))
        self.assertEqual(self.docker.calls, 2, "單次執行內重試一次（蓋過 restarting 空窗）")

    def test_restart_window_within_one_run_is_not_a_failure(self):
        self.docker.set({"report-mark-postgres": "running", "deploy-nginx-1": "running",
                         "deploy-cloudflared-1": "running"})
        self.docker.set({"report-mark-postgres": "restarting", "deploy-nginx-1": "running",
                         "deploy-cloudflared-1": "running"}, attempt=1)
        p = self.run_probe()
        self.assertEqual(p.returncode, EXIT_OK)
        self.assertEqual(parse(p.stdout)["attempts"], "2")

    def test_unhealthy_and_missing_count_as_down_not_tooling(self):
        self.docker.set({"report-mark-postgres": "running:unhealthy", "deploy-nginx-1": "running"})
        p = self.run_probe()
        self.assertEqual(p.returncode, EXIT_CRITICAL)
        self.assertEqual(parse(p.stdout)["down"], "postgres:unhealthy,cloudflared:missing")

    def test_important_needs_two_consecutive_runs(self):
        targets = "pg=report-mark-postgres:critical edge=deploy-nginx-1:important"
        self.docker.set({"report-mark-postgres": "running", "deploy-nginx-1": "exited"})
        first = self.run_probe(CONTAINER_HEALTH_TARGETS=targets)
        self.assertEqual(first.returncode, EXIT_PENDING)
        self.assertEqual(parse(first.stdout)["pending"], "edge:exited:1/2")
        second = self.run_probe(CONTAINER_HEALTH_TARGETS=targets)
        self.assertEqual(second.returncode, EXIT_DEGRADED)
        self.assertEqual(parse(second.stdout)["down"], "edge:exited")

    def test_supporting_needs_three_and_a_healthy_run_resets(self):
        targets = "x=deploy-cloudflared-1:supporting"
        down = {"deploy-cloudflared-1": "exited"}
        up = {"deploy-cloudflared-1": "running"}
        codes = []
        for states in (down, down, up, down, down, down):
            self.docker.set(states)
            codes.append(self.run_probe(CONTAINER_HEALTH_TARGETS=targets).returncode)
        self.assertEqual(codes, [3, 3, 0, 3, 3, 9])

    def test_a_long_gap_restarts_the_streak(self):
        targets = "edge=deploy-nginx-1:important"
        self.docker.set({"deploy-nginx-1": "exited"})
        self.run_probe(CONTAINER_HEALTH_TARGETS=targets)
        state = self.streaks / "container.state"
        n, t = state.read_text().strip().split("=")[1].split(":")
        state.write_text(f"edge={n}:{int(t) - 3600}\n")
        self.assertEqual(self.run_probe(CONTAINER_HEALTH_TARGETS=targets).returncode, EXIT_PENDING,
                         "一小時前的那一筆不算「連續」")

    def test_confirm_counts_are_knobs(self):
        targets = "edge=deploy-nginx-1:important"
        self.docker.set({"deploy-nginx-1": "exited"})
        p = self.run_probe(CONTAINER_HEALTH_TARGETS=targets, HEALTH_CONFIRM_IMPORTANT="1")
        self.assertEqual(p.returncode, EXIT_DEGRADED)

    def test_docker_unavailable_is_debounced_tooling(self):
        self.docker.set({}, mode="down")
        first = self.run_probe()
        self.assertEqual(first.returncode, EXIT_PENDING)
        self.assertIn("docker_unavailable_unconfirmed", parse(first.stdout)["reason"])
        self.assertEqual(self.run_probe().returncode, EXIT_TOOLING)

    def test_tooling_round_neither_resets_nor_advances_container_streaks(self):
        targets = "edge=deploy-nginx-1:important"
        self.docker.set({"deploy-nginx-1": "exited"})
        self.assertEqual(self.run_probe(CONTAINER_HEALTH_TARGETS=targets).returncode, EXIT_PENDING)
        self.docker.set({}, mode="down")
        self.assertEqual(self.run_probe(CONTAINER_HEALTH_TARGETS=targets).returncode, EXIT_PENDING)
        self.docker.set({"deploy-nginx-1": "exited"})
        self.assertEqual(self.run_probe(CONTAINER_HEALTH_TARGETS=targets).returncode, EXIT_DEGRADED)

    def test_docker_not_found_is_tooling(self):
        p = self.run_probe(DOCKER_BIN=str(self.root / "nope"), HEALTH_CONFIRM_TOOLING="1")
        self.assertEqual(p.returncode, EXIT_TOOLING)
        self.assertIn("docker_not_found", parse(p.stdout)["reason"])

    def test_hung_docker_is_bounded(self):
        self.docker.set({}, mode="hang")
        t0 = time.monotonic()
        p = self.run_probe(CONTAINER_HEALTH_TIMEOUT="1", HEALTH_CONFIRM_TOOLING="1")
        self.assertLess(time.monotonic() - t0, 20)
        self.assertEqual(p.returncode, EXIT_TOOLING)

    def test_unwritable_streak_state_fails_loud(self):
        targets = "x=deploy-cloudflared-1:supporting"
        self.docker.set({"deploy-cloudflared-1": "exited"})
        p = self.run_probe(CONTAINER_HEALTH_TARGETS=targets, HEALTH_STREAK_DIR="/proc/nope/streaks")
        self.assertEqual(p.returncode, EXIT_DEGRADED, "記不住次數就每筆都算確認，不得永遠停在確認期")
        self.assertIn("streak_state_unwritable", parse(p.stdout)["reason"])

    def test_bad_target_list_is_immediate_tooling(self):
        for bad in ("pg=report-mark-postgres:urgent", "pg=-rm:critical", "p$g=x:critical"):
            with self.subTest(targets=bad):
                self.assertEqual(self.run_probe(CONTAINER_HEALTH_TARGETS=bad).returncode, EXIT_TOOLING)

    def test_streak_state_is_not_sourced(self):
        canary = self.root / "pwned"
        self.streaks.mkdir()
        (self.streaks / "container.state").write_text(f'$(touch {canary})=1:1\npostgres=`touch {canary}`\n')
        self.run_probe()
        self.assertFalse(canary.exists())


class _Proc:
    def __init__(self, root: Path):
        self.dir = root / "proc"
        (self.dir / "pressure").mkdir(parents=True)
        self.set()

    def set(self, avail_pct=50, psi_mem="0.00", psi_io="0.00", meminfo=True, psi=True):
        if meminfo:
            (self.dir / "meminfo").write_text(f"MemTotal: 1000000 kB\nMemAvailable: {avail_pct * 10000} kB\n")
        else:
            (self.dir / "meminfo").unlink(missing_ok=True)
        for name, v in (("memory", psi_mem), ("io", psi_io)):
            f = self.dir / "pressure" / name
            if psi:
                f.write_text(f"some avg10=0.00 avg60={v} avg300=0.00 total=1\n"
                             f"full avg10=0.00 avg60={v} avg300=0.00 total=1\n")
            else:
                f.unlink(missing_ok=True)


class HostProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = _Proc(self.root)

    def run_probe(self, **env_extra) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.update({"HOST_PROC_ROOT": str(self.proc.dir), "HEALTH_STREAK_DIR": str(self.root / "streaks"),
                    "HOST_HEALTH_PATHS": self.tmp.name, "HOST_DISK_MAX_PCT": "101"})
        env.update(env_extra)
        return subprocess.run(["bash", str(HOST_PROBE)], capture_output=True, text=True, env=env, timeout=60)

    def test_within_thresholds_is_ok(self):
        p = self.run_probe()
        self.assertEqual(p.returncode, EXIT_OK, p.stdout + p.stderr)
        out = parse(p.stdout)
        self.assertEqual((out["status"], out["mem_avail_pct"], out["psi_io_full_avg60"]), ("ok", "50", "0.00"))

    def test_disk_is_important_two_runs(self):
        codes = [self.run_probe(HOST_DISK_MAX_PCT="0").returncode for _ in range(2)]
        self.assertEqual(codes, [EXIT_PENDING, EXIT_DEGRADED])
        self.assertTrue(parse(self.run_probe(HOST_DISK_MAX_PCT="0").stdout)["breached"].startswith("disk:"))

    def test_memory_is_supporting_three_runs(self):
        self.proc.set(avail_pct=3)
        self.assertEqual([self.run_probe().returncode for _ in range(3)], [3, 3, 9])

    def test_psi_io_is_supporting_and_recovery_resets(self):
        codes = []
        for v in ("45.10", "45.10", "1.00", "45.10", "45.10", "45.10"):
            self.proc.set(psi_io=v)
            codes.append(self.run_probe().returncode)
        self.assertEqual(codes, [3, 3, 0, 3, 3, 9])
        self.assertEqual(parse(self.run_probe().stdout)["breached"], "psi_io_full_avg60:45.10")

    def test_psi_memory_threshold(self):
        self.proc.set(psi_mem="12.5")
        self.assertEqual([self.run_probe().returncode for _ in range(3)], [3, 3, 9])

    def test_missing_psi_is_skipped_not_a_failure(self):
        self.proc.set(psi=False)
        p = self.run_probe()
        self.assertEqual(p.returncode, EXIT_OK)
        self.assertEqual(parse(p.stdout)["psi_io_full_avg60"], "-")

    def test_unreadable_meminfo_is_debounced_tooling(self):
        self.proc.set(meminfo=False)
        self.assertEqual([self.run_probe().returncode for _ in range(2)], [EXIT_PENDING, EXIT_TOOLING])

    def test_confirmed_breach_is_reported_even_if_another_item_is_unreadable(self):
        self.proc.set(meminfo=False)
        p = self.run_probe(HOST_DISK_MAX_PCT="0", HEALTH_CONFIRM_IMPORTANT="1")
        self.assertEqual(p.returncode, EXIT_DEGRADED)

    def test_bad_knob_is_immediate_tooling(self):
        for knob, value in (("HOST_DISK_MAX_PCT", "ninety"), ("HOST_PSI_IO_FULL_MAX", "1.2.3"),
                            ("HOST_MEM_MIN_AVAIL_PCT", "-1")):
            with self.subTest(knob=knob):
                self.assertEqual(self.run_probe(**{knob: value}).returncode, EXIT_TOOLING)

    def test_unwritable_streak_state_fails_loud(self):
        self.proc.set(avail_pct=3)
        p = self.run_probe(HEALTH_STREAK_DIR="/proc/nope/streaks")
        self.assertEqual(p.returncode, EXIT_DEGRADED)


class TierDebounceThroughP5Tests(unittest.TestCase):
    """探針的退出碼餵進 P5（fake systemctl），驗端到端的告警形狀。P5 本身沒有任何去抖程式碼。"""

    def setUp(self):
        self.h = _Harness(webhook="http://example.invalid/hook")
        self.addCleanup(self.h.close)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.docker = _Docker(Path(self.tmp.name))

    def _round(self, states: dict, targets: str) -> dict:
        self.docker.set(states)
        env = dict(os.environ)
        env.update({"DOCKER_BIN": str(self.docker.bin), "HEALTH_STREAK_DIR": str(Path(self.tmp.name) / "s"),
                    "CONTAINER_HEALTH_RETRY_WAIT": "0", "CONTAINER_HEALTH_TARGETS": targets})
        code = subprocess.run(["bash", str(CONTAINER_PROBE)], capture_output=True, text=True, env=env,
                              timeout=60).returncode
        self.h.set_probe(code, result="success" if code in (0, 3) else "exit-code")
        p = self.h.run(INCIDENT_COMPONENT="container", INCIDENT_MONITOR_COMPONENT="container_monitor",
                       INCIDENT_HOLD_EXIT_CODES="3")
        return last_emit(p.stdout, "container")

    def test_important_container_opens_a_warning_only_on_the_second_run(self):
        targets = "edge=deploy-nginx-1:important"
        first = self._round({"deploy-nginx-1": "exited"}, targets)
        self.assertEqual((first["action"], first["status"]), ("noop", "held"))
        self.assertEqual(self.h.webhook_calls(), 0, "確認期不得通知")
        second = self._round({"deploy-nginx-1": "exited"}, targets)
        self.assertEqual((second["action"], second["severity"], second["reason"]),
                         ("firing", "WARNING", "probe_exit_9"))
        self.assertEqual(self.h.webhook_calls(), 1)
        healthy = self._round({"deploy-nginx-1": "running"}, targets)
        self.assertEqual(healthy["action"], "resolved")

    def test_critical_container_opens_critical_immediately(self):
        e = self._round({"report-mark-postgres": "exited", "deploy-nginx-1": "running",
                         "deploy-cloudflared-1": "running"},
                        "postgres=report-mark-postgres:critical")
        self.assertEqual((e["action"], e["severity"], e["reason"]), ("firing", "CRITICAL", "probe_exit_1"))

    def test_pending_round_does_not_resolve_an_open_incident(self):
        targets = "pg=report-mark-postgres:critical edge=deploy-nginx-1:important"
        self._round({"report-mark-postgres": "exited", "deploy-nginx-1": "running"}, targets)
        self.assertEqual(self.h.state_of("container")["state"], "FIRING")
        # PostgreSQL 回來了，但 nginx 剛開始抖：確認期＝hold，事件不關也不重開
        e = self._round({"report-mark-postgres": "running", "deploy-nginx-1": "exited"}, targets)
        self.assertEqual((e["action"], e["status"]), ("suppress", "held"))
        self.assertEqual(self.h.state_of("container")["state"], "FIRING")
        self.assertEqual(self.h.webhook_calls(), 1)

    def test_exit_9_is_the_only_new_code_and_others_are_unchanged(self):
        """9 以外（含未知碼）的分級與既有一致：沒有 9 那一行時 9 也是 WARNING（未知分支）。"""
        for code, severity, reason in ((9, "WARNING", "probe_exit_9"), (42, "WARNING", "probe_exit_unknown"),
                                       (1, "CRITICAL", "probe_exit_1"), (4, "WARNING", "probe_exit_4")):
            with self.subTest(code=code):
                h = _Harness(webhook="http://example.invalid/hook")
                self.addCleanup(h.close)
                h.set_probe(code)
                e = last_emit(h.run().stdout)
                self.assertEqual((e["action"], e["severity"], e["reason"]), ("firing", severity, reason))


class ProbeContractTests(unittest.TestCase):
    def test_probes_do_not_depend_on_python(self):
        for probe in (CONTAINER_PROBE, HOST_PROBE, REPO_ROOT / "scripts" / "_health_streak.sh"):
            code = "\n".join(ln for ln in probe.read_text(encoding="utf-8").splitlines()
                             if not ln.lstrip().startswith("#"))
            for word in ("python", "uv run", ".venv", "app.services"):
                with self.subTest(probe=probe.name, word=word):
                    self.assertNotIn(word, code)

    def test_default_container_targets_match_the_catalog(self):
        catalog = tomllib.loads(CATALOG.read_text(encoding="utf-8"))
        expected = sorted(f"{s['name']}={s['container']}:{s['tier']}" for s in catalog["services"]
                          if s["kind"] == "container")
        m = re.search(r'CONTAINER_HEALTH_TARGETS="\$\{CONTAINER_HEALTH_TARGETS:-([^}]*)\}"',
                      CONTAINER_PROBE.read_text(encoding="utf-8"))
        self.assertIsNotNone(m)
        self.assertEqual(sorted(m.group(1).split()), expected)

    def test_catalog_lists_the_four_new_units_read_only(self):
        catalog = tomllib.loads(CATALOG.read_text(encoding="utf-8"))
        by_name = {s["name"]: s for s in catalog["services"]}
        for name in ("container-health", "container-incident", "host-health", "host-incident"):
            with self.subTest(name=name):
                svc = by_name[name]
                self.assertEqual(svc["actions"], ["status", "logs"])
                self.assertTrue((SYSTEMD_DIR / svc["unit"]).is_file())
                self.assertTrue((SYSTEMD_DIR / svc["timer"]).is_file())
        names = [s["name"] for s in catalog["services"]]
        start = names.index("container-health")
        # 當初在檔尾追加的那一段：之後的 lane（例如 P8 的 rollup-observations）再接在它後面
        self.assertEqual(names[start:start + 4],
                         ["container-health", "container-incident", "host-health", "host-incident"],
                         "四項連續、依序（當初只在檔尾追加）")
        self.assertGreater(start, names.index("load-observations"))

    def test_probe_units_accept_pending_and_fit_in_the_timer_interval(self):
        for kind, worst in (("container", 5 + 2 * 10 + 15), ("host", 10)):
            service = SYSTEMD_DIR / f"report-mark-{kind}-health.service"
            timer = SYSTEMD_DIR / f"report-mark-{kind}-health.timer"
            with self.subTest(kind=kind):
                self.assertEqual(_directives(service, "SuccessExitStatus"), ["3"])
                self.assertEqual(_directives(service, "OnFailure"), [])
                timeout = int(_directives(service, "TimeoutStartSec")[0])
                self.assertGreater(timeout, worst)
                self.assertLess(timeout, 120)
                self.assertEqual(_directives(timer, "OnUnitActiveSec"), ["2min"])
                self.assertEqual(_directives(timer, "Persistent"), [])
                self.assertIn(f"scripts/check_{kind}_health.sh", _directives(service, "ExecStart")[0])

    def test_incident_units_point_at_their_probe_and_hold_on_pending(self):
        for kind in ("container", "host"):
            unit = SYSTEMD_DIR / f"report-mark-{kind}-incident.service"
            env = dict(v.split("=", 1) for v in _directives(unit, "Environment"))
            with self.subTest(kind=kind):
                self.assertEqual(env["INCIDENT_PROBE_UNIT"], f"report-mark-{kind}-health.service")
                self.assertEqual(env["INCIDENT_TIMER_UNIT"], f"report-mark-{kind}-health.timer")
                self.assertEqual(env["INCIDENT_COMPONENT"], kind)
                self.assertEqual(env["INCIDENT_MONITOR_COMPONENT"], f"{kind}_monitor")
                self.assertEqual(env["INCIDENT_STATE_DIR"],
                                 f"/home/kashionz/projects/report-mark/data/.incidents-{kind}")
                self.assertEqual(env["INCIDENT_HOLD_EXIT_CODES"].split(), ["3"])
                self.assertEqual(_directives(unit, "OnFailure"), [])
                self.assertIn("-/etc/report-mark/alert.env", _directives(unit, "EnvironmentFile"))
                self.assertIn("scripts/incident_handler.sh", _directives(unit, "ExecStart")[0])
                self.assertLess(int(_directives(unit, "TimeoutStartSec")[0]), 120)

    def test_streak_dir_is_gitignored_and_redirected_in_tests(self):
        self.assertIn("data/.health-streaks/", (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertTrue(os.environ.get("HEALTH_STREAK_DIR", "").startswith("/nonexistent/"))


if __name__ == "__main__":
    unittest.main()
