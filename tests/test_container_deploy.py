# tests/test_container_deploy.py
"""容器部署（docs/CONTAINER_DEPLOYMENT.md）的靜態契約與 entrypoint 邏輯。不 build 映像、不起容器、不連 DB。

守的是「壞了不會自己出聲」的幾件事：
- 映像不帶祕密與資料（.dockerignore）、執行期離線（HF_HUB_OFFLINE=1）、非 root、相依與鎖檔一致。
- 每一支 systemd timer 都有歸屬，框架排程的時間與 timer 相同，文件的對照表與程式清單一致。
- web／worker／scheduler 共用同一個 data volume（LLM 批次鎖與上傳 worker 整輪鎖是 data/ 底下的 flock）。
- schema 閘門只放行「版本一致」，migrate 在備份與確認都成立之前絕不升級。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from deploy.container import entrypoint as ep  # noqa: E402
from deploy.container import scheduler as sch  # noqa: E402
from scripts import schema_baseline as sb  # noqa: E402

DOCKERFILE = REPO_ROOT / "deploy" / "container" / "Dockerfile"
DOCKERIGNORE = REPO_ROOT / ".dockerignore"
COMPOSE = REPO_ROOT / "deploy" / "compose" / "docker-compose.yml"
COMPOSE_ENV_EXAMPLE = REPO_ROOT / "deploy" / "compose" / ".env.example"
DOC = REPO_ROOT / "docs" / "CONTAINER_DEPLOYMENT.md"
SYSTEMD = REPO_ROOT / "deploy" / "systemd"
BACKUP_SH = REPO_ROOT / "scripts" / "db_backup.sh"


def _runtime_stage(text: str) -> str:
    """Dockerfile 最後一個 stage（執行映像）的內容。"""
    return text[text.rindex("\nFROM "):]


# ───────────────────────── 映像 ─────────────────────────

class DockerignoreTests(unittest.TestCase):
    def setUp(self):
        self.lines = [ln.strip() for ln in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
                      if ln.strip() and not ln.lstrip().startswith("#")]

    def test_secrets_and_data_are_excluded(self):
        for pattern in ("**/.env", "**/.env.*", "data/", "研報自動匯入/", "backups/", "**/*.dump", ".git/",
                        ".venv/", "frontend/node_modules/", "tests/"):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, self.lines)

    def test_only_known_reinclusions(self):
        """`!` 會把東西放回 context：只准範本與前端 build 需要的 SSE 契約。"""
        self.assertEqual(sorted(ln for ln in self.lines if ln.startswith("!")),
                         ["!**/.env.example", "!tests/fixtures/sse_events.json"])


class DockerfileTests(unittest.TestCase):
    def setUp(self):
        self.text = DOCKERFILE.read_text(encoding="utf-8")
        self.runtime = _runtime_stage(self.text)

    def test_runtime_is_offline_and_backups_go_to_the_mount(self):
        for env in ("HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "UV_OFFLINE=1", "UV_NO_SYNC=1",
                    "REPORT_MARK_BACKUP_DIR=/app/backups", "REPORT_MARK_BACKUP_MOUNT=/app/backups"):
            with self.subTest(env=env):
                self.assertIn(env, self.runtime)

    def test_models_are_fetched_at_build_and_verified_offline(self):
        self.assertIn("fetch_models.py download", self.text)
        verify = self.runtime.index("fetch_models.py verify")
        self.assertGreater(verify, self.runtime.index("USER app"), "離線載入要以非 root 身分驗證")
        self.assertGreater(verify, self.runtime.index("HF_HUB_OFFLINE=1"))

    def test_non_root_fixed_uid(self):
        self.assertRegex(self.text, r"ARG APP_UID=10001")
        self.assertRegex(self.runtime, r"\nUSER app:app\n")

    def test_dependencies_follow_lockfile(self):
        self.assertIn("uv sync --frozen --no-dev", self.text)
        self.assertIn("COPY pyproject.toml uv.lock ./", self.text)

    def test_no_env_file_or_data_copied(self):
        for line in self.text.splitlines():
            if line.strip().startswith(("COPY", "ADD")):
                with self.subTest(line=line):
                    self.assertNotRegex(line, r"(^|\s|/)\.env(\s|$)")
                    self.assertNotRegex(line, r"\sdata/")

    def test_no_secret_values_in_env(self):
        for m in re.finditer(r"^\s*([A-Z0-9_]*(?:KEY|SECRET|PASSWORD|TOKEN)[A-Z0-9_]*)=", self.text, re.M):
            self.fail(f"Dockerfile 不得寫入祕密：{m.group(1)}")

    def test_supercronic_is_pinned_and_checksummed(self):
        self.assertIn("sha256sum -c -", self.text)
        self.assertRegex(self.text, r"SUPERCRONIC_SHA256_AMD64=[0-9a-f]{64}")
        self.assertRegex(self.text, r"SUPERCRONIC_SHA256_ARM64=[0-9a-f]{64}")

    def test_entrypoint_and_default_role(self):
        self.assertIn('"/app/deploy/container/entrypoint.py"', self.runtime)
        self.assertIn('CMD ["web"]', self.runtime)

    def test_rerank_default_matches_config(self):
        """封進映像的 rerank 模型＝程式預設；兩邊不同時，離線的執行期載不到預設模型。"""
        config = (REPO_ROOT / "app" / "config.py").read_text(encoding="utf-8")
        default = re.search(r'os\.getenv\("RERANK_MODEL", "([^"]+)"\)', config).group(1)
        args = re.findall(r"ARG RERANK_MODEL=(\S+)", self.text)
        self.assertTrue(args)
        self.assertEqual(set(args), {default})


# ───────────────────────── compose ─────────────────────────

class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        self.services = self.doc["services"]

    def _volume_targets(self, name: str) -> dict[str, str]:
        out = {}
        for v in self.services[name].get("volumes", []):
            if isinstance(v, str):
                src, target = v.split(":")[:2]
            else:
                src, target = v["source"], v["target"]
            out[target] = src
        return out

    def test_services(self):
        self.assertEqual(set(self.services), {"postgres", "web", "worker", "scheduler", "migrate", "clamav"})
        for role in ("web", "worker", "scheduler", "migrate"):
            with self.subTest(role=role):
                self.assertEqual(self.services[role]["command"], [role])

    def test_roles_share_one_data_volume(self):
        """flock 互斥的前提：同一個 named volume 掛在同一個路徑。"""
        for role in ("web", "worker", "scheduler", "migrate"):
            with self.subTest(role=role):
                self.assertEqual(self._volume_targets(role).get("/app/data"), "data")
        self.assertIn("data", self.doc["volumes"])

    def test_host_mounts_are_not_auto_created(self):
        for role in ("web", "worker", "scheduler", "migrate"):
            for v in self.services[role]["volumes"]:
                if isinstance(v, dict) and v.get("type") == "bind":
                    with self.subTest(role=role, target=v["target"]):
                        self.assertIs(v["bind"]["create_host_path"], False)

    def test_one_shot_and_optional_services_are_profiled(self):
        self.assertEqual(self.services["migrate"]["profiles"], ["migrate"])
        self.assertEqual(self.services["migrate"]["restart"], "no")
        self.assertEqual(self.services["clamav"]["profiles"], ["clamav"])
        for role in ("postgres", "web", "worker", "scheduler"):
            self.assertNotIn("profiles", self.services[role])

    def test_postgres_matches_current_major_and_is_not_published(self):
        image = self.services["postgres"]["image"]
        self.assertRegex(image, r"^pgvector/pgvector:\d+\.\d+\.\d+-pg16$")
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        self.assertIn("pgvector/pgvector:pg16", makefile, "現行主機部署的 PostgreSQL 主版本變了，compose 要跟著改")
        self.assertNotIn("ports", self.services["postgres"])

    def test_web_publishes_loopback_by_default_and_checks_readyz(self):
        self.assertEqual(self.services["web"]["ports"], ["${WEB_PUBLISH:-127.0.0.1:8097}:8097"])
        self.assertIn("/readyz", " ".join(self.services["web"]["healthcheck"]["test"]))

    def test_does_not_touch_the_edge_compose(self):
        edge = yaml.safe_load((REPO_ROOT / "deploy" / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(set(edge["services"]), {"nginx", "cloudflared"})

    def test_env_example_has_required_keys_without_values(self):
        text = COMPOSE_ENV_EXAMPLE.read_text(encoding="utf-8")
        values = dict(re.findall(r"^([A-Z0-9_]+)=(.*)$", text, re.M))
        for key in ("POSTGRES_PASSWORD", "REPORT_MARK_SESSION_SECRET", "OPENAI_API_KEY"):
            with self.subTest(key=key):
                self.assertIn(key, values)
                self.assertEqual(values[key], "", "範本不得帶祕密值")
        for key in ("BACKUP_HOST_DIR", "PROFILE_HOST_DIR", "REPORT_MARK_IMAGE"):
            self.assertTrue(values.get(key), key)
        self.assertEqual(values["ASK_ENABLE_WEB"], "0")
        self.assertEqual(values["OBJECT_STORAGE_MODE"], "local")
        self.assertEqual(values["UPLOAD_ENABLED"], "0")

    def test_compose_env_file_is_gitignored(self):
        rc = subprocess.run(["git", "check-ignore", "-q", "deploy/compose/.env"], cwd=REPO_ROOT).returncode
        self.assertEqual(rc, 0, "deploy/compose/.env 必須被 .gitignore 擋住")


# ───────────────────────── 排程 ─────────────────────────

def _on_calendar_to_cron(spec: str) -> str:
    """repo 裡用到的 OnCalendar 形狀 → cron。不認得就拋錯（新形狀要在這裡補，而不是被略過）。"""
    spec = spec.strip()
    if spec == "hourly":
        return "0 * * * *"
    m = re.fullmatch(r"(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun) )?\*-\*-\* (\d{2}|\*):(\d{2})(?:/\d+)?:00", spec)
    if not m or "/" in spec:
        raise ValueError(f"不認得的 OnCalendar：{spec}")
    dow = {"Mon": "1", "Tue": "2", "Wed": "3", "Thu": "4", "Fri": "5", "Sat": "6", "Sun": "0"}.get(m.group(1), "*")
    hour = "*" if m.group(2) == "*" else str(int(m.group(2)))
    return f"{int(m.group(3))} {hour} * * {dow}"


def _timers() -> dict[str, str]:
    out = {}
    for p in sorted(SYSTEMD.glob("*.timer")):
        cal = re.findall(r"^OnCalendar=(.+)$", p.read_text(encoding="utf-8"), re.M)
        out[p.name] = cal[0] if cal else ""
    return out


class SchedulerInventoryTests(unittest.TestCase):
    def test_every_timer_has_exactly_one_home(self):
        timers = set(_timers())
        homes = [j.unit for j in sch.JOBS if j.unit] + list(sch.WORKER_UNITS) + list(sch.HOST_LAYER)
        self.assertEqual(len(homes), len(set(homes)), "同一支 timer 被歸到兩個地方")
        self.assertEqual(set(homes), timers, "deploy/systemd 的 timer 與 scheduler.py 的歸屬不一致")

    def test_job_schedules_match_their_timers(self):
        timers = _timers()
        for job in sch.JOBS:
            if job.unit is None:
                continue
            with self.subTest(job=job.name):
                self.assertEqual(job.cron, _on_calendar_to_cron(timers[job.unit]))

    def test_job_commands_point_at_existing_scripts(self):
        for job in sch.JOBS:
            script = next(a for a in job.command if a.startswith("scripts/"))
            with self.subTest(job=job.name):
                self.assertTrue((REPO_ROOT / script).is_file(), script)

    def test_job_commands_match_the_units(self):
        """容器裡的指令與 unit 的 ExecStart 跑同一支腳本、同一組參數。"""
        for job in sch.JOBS:
            if job.unit is None:
                continue
            service = (SYSTEMD / job.unit.replace(".timer", ".service")).read_text(encoding="utf-8")
            exec_line = next(ln for ln in service.splitlines() if ln.startswith("ExecStart="))
            tail = " ".join(job.command[1:])
            with self.subTest(job=job.name):
                self.assertIn(tail, exec_line)

    def test_doc_table_matches_inventory(self):
        text = DOC.read_text(encoding="utf-8")
        rows = re.findall(r"^\| `(report-mark-[\w-]+\.timer)` \| ([^|]+?) \| ([^|]+?) \|$", text, re.M)
        by_unit = {u: (home.strip(), detail.strip()) for u, home, detail in rows}
        self.assertEqual(len(rows), len(by_unit), "文件對照表有重複的 timer")
        self.assertEqual(set(by_unit), set(_timers()), "文件對照表與 deploy/systemd 的 timer 不一致")
        for job in (j for j in sch.JOBS if j.unit):
            home, detail = by_unit[job.unit]
            with self.subTest(job=job.name):
                self.assertEqual(home, "scheduler")
                self.assertTrue(detail.startswith(f"`{job.name}`（{'預設開' if job.default_enabled else '預設關'}）"),
                                detail)
        for unit in sch.WORKER_UNITS:
            self.assertEqual(by_unit[unit][0], "worker")
        for unit in sch.HOST_LAYER:
            self.assertEqual(by_unit[unit][0], "廷豐主機層")

    def test_framework_only_jobs_are_documented(self):
        """沒有對應 timer 的框架專屬工作，列在文件的另一張表（`| \`工作名\` | 排程 | 說明 |`）。"""
        text = DOC.read_text(encoding="utf-8")
        rows = dict(re.findall(r"^\| `([a-z][\w-]*)` \| `([^`]+)` \| [^|]+ \|$", text, re.M))
        own = {j.name: j for j in sch.JOBS if j.unit is None}
        self.assertTrue(own, "預期至少有 connectors 這個框架專屬工作")
        self.assertEqual(set(rows), set(own), "文件的框架專屬排程表與 scheduler.py 不一致")
        for name, job in own.items():
            with self.subTest(job=name):
                self.assertEqual(rows[name], job.cron)


class SchedulerSelectionTests(unittest.TestCase):
    def test_defaults(self):
        names = [j.name for j in sch.selected_jobs({})]
        self.assertIn("backup", names)
        self.assertNotIn("retrieval-regression", names)
        self.assertNotIn("r2-reconcile", names)
        self.assertIn("connectors", names)
        # 第三層背景補齊排進 crontab，開關在 profile（--scheduled＋[backfill].background_enabled，預設關）。
        self.assertIn("backfill-tiers", names)

    def test_background_backfill_is_governed_by_the_profile(self):
        job = next(j for j in sch.JOBS if j.name == "backfill-tiers")
        self.assertIsNone(job.unit)
        self.assertEqual(job.command, ("python", "scripts/backfill_tiers.py", "--tier", "3", "--scheduled"))

    def test_enable_and_disable(self):
        names = [j.name for j in sch.selected_jobs({sch.ENABLE_ENV: "retrieval-regression",
                                                     sch.DISABLE_ENV: "backfill, db-audit"})]
        self.assertIn("retrieval-regression", names)
        self.assertNotIn("backfill", names)
        self.assertNotIn("db-audit", names)

    def test_unknown_name_is_refused(self):
        with self.assertRaises(sch.SchedulerConfigError):
            sch.selected_jobs({sch.DISABLE_ENV: "bakup"})

    def test_same_name_in_both_is_refused(self):
        with self.assertRaises(sch.SchedulerConfigError):
            sch.selected_jobs({sch.DISABLE_ENV: "backup", sch.ENABLE_ENV: "backup"})

    def test_crontab_lines(self):
        tab = sch.render_crontab([sch.JOBS[0]])
        lines = [ln for ln in tab.splitlines() if ln and not ln.startswith("#")]
        self.assertEqual(lines, ["0 1 * * * python scripts/backfill_extraction.py --extractor pdfplumber "
                                 "--max-minutes 240"])


# ───────────────────────── schema 閘門與 migrate ─────────────────────────

def _vr(status, db_revision=None, expected_head="0011", pending=(), message="msg"):
    return sb.VersionResult(status, expected_head, db_revision, list(pending), message)


class GateVerdictTests(unittest.TestCase):
    def test_only_ok_passes(self):
        self.assertEqual(ep.gate_verdict(_vr("ok", "0011"))[0], 0)
        for status, rc in (("behind", 1), ("ahead", 2), ("unversioned", 2), ("ambiguous", 2), ("error", 2),
                           ("db_unavailable", 3)):
            with self.subTest(status=status):
                self.assertEqual(ep.gate_verdict(_vr(status))[0], rc)

    def test_behind_tells_the_operator_to_run_migrate(self):
        self.assertIn("docker compose run --rm migrate", ep.gate_verdict(_vr("behind", "0010"))[1])

    def test_retries_only_while_db_is_unavailable(self):
        results = iter([_vr("db_unavailable"), _vr("db_unavailable"), _vr("ok", "0011")])
        sleeps = []
        rc, _msg, _res = ep.wait_for_schema(lambda: next(results), wait_s=60, sleep=sleeps.append,
                                            clock=lambda: 0.0)
        self.assertEqual(rc, 0)
        self.assertEqual(len(sleeps), 2)

    def test_gives_up_after_deadline(self):
        clock = iter([0.0, 0.0, 100.0])
        rc, _msg, _res = ep.wait_for_schema(lambda: _vr("db_unavailable"), wait_s=10, sleep=lambda s: None,
                                            clock=lambda: next(clock))
        self.assertEqual(rc, 3)

    def test_behind_is_not_retried(self):
        calls = []
        rc, _msg, _res = ep.wait_for_schema(lambda: calls.append(1) or _vr("behind", "0010"), wait_s=60,
                                            sleep=lambda s: None, clock=lambda: 0.0)
        self.assertEqual((rc, len(calls)), (1, 1))


class ConfigProblemTests(unittest.TestCase):
    URL = {"REPORT_MARK_DB_URL": "postgresql+asyncpg://u:p@postgres:5432/research"}

    def test_db_url_required_for_every_role(self):
        for role in ep.ROLES:
            with self.subTest(role=role):
                self.assertTrue(ep.config_problems(role, {}))

    def test_web_requires_real_session_secret(self):
        for secret in ("", "請填入長隨機字串", "short", "change-me"):
            with self.subTest(secret=secret):
                self.assertTrue(ep.config_problems("web", {**self.URL, "REPORT_MARK_SESSION_SECRET": secret}))
        ok = {**self.URL, "REPORT_MARK_SESSION_SECRET": "fixed-test-secret-" + "a" * 32}
        self.assertEqual(ep.config_problems("web", ok), [])
        self.assertEqual(ep.config_problems("worker", self.URL), [])


class _Ops:
    """migrate 的假動作：記錄呼叫順序。"""

    def __init__(self, results, *, has_objects=False, dump_ok=True, upgrade_rc=0):
        self.results = list(results)
        self.calls: list[str] = []
        self.has_objects, self.dump_ok, self.upgrade_rc = has_objects, dump_ok, upgrade_rc
        self.dump_dir: Path | None = None

    def ops(self) -> ep.MigrateOps:
        def check():
            self.calls.append("check")
            return self.results.pop(0)

        def dump(d, size, timeout):
            self.calls.append("dump")
            self.dump_dir = d
            return sb.DumpResult(self.dump_ok, "dump ok" if self.dump_ok else "dump failed")

        def upgrade():
            self.calls.append("upgrade")
            return self.upgrade_rc

        return ep.MigrateOps(identity=lambda: "postgres:5432/research", check=check,
                             research_has_objects=lambda: self.has_objects, database_size=lambda: 1024,
                             dump=dump, upgrade=upgrade)


class MigrateTests(unittest.TestCase):
    CONFIRM = {"REPORT_MARK_MIGRATE_CONFIRM": "postgres:5432/research"}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {"REPORT_MARK_BACKUP_DIR": self.tmp.name, ep.GATE_WAIT_ENV: "0"}

    def tearDown(self):
        self.tmp.cleanup()

    def run_migrate(self, fake, env=None):
        return ep.run_migrate({**self.env, **(env or {})}, fake.ops(), sleep=lambda s: None)

    def test_already_at_head_does_nothing(self):
        fake = _Ops([_vr("ok", "0011")])
        self.assertEqual(self.run_migrate(fake), 0)
        self.assertEqual(fake.calls, ["check"])

    def test_empty_database_is_first_install_without_backup_or_confirm(self):
        fake = _Ops([_vr("unversioned"), _vr("ok", "0011")])
        self.assertEqual(self.run_migrate(fake), 0)
        self.assertEqual(fake.calls, ["check", "upgrade", "check"])

    def test_unmanaged_existing_database_is_refused(self):
        fake = _Ops([_vr("unversioned")], has_objects=True)
        self.assertEqual(self.run_migrate(fake, self.CONFIRM), 2)
        self.assertNotIn("upgrade", fake.calls)

    def test_behind_without_confirm_refuses_before_dumping(self):
        fake = _Ops([_vr("behind", "0010", pending=["0011"])])
        self.assertEqual(self.run_migrate(fake), 2)
        self.assertEqual(fake.calls, ["check"])

    def test_behind_with_wrong_confirm_refuses(self):
        fake = _Ops([_vr("behind", "0010")])
        self.assertEqual(self.run_migrate(fake, {"REPORT_MARK_MIGRATE_CONFIRM": "localhost:5436/research"}), 2)
        self.assertNotIn("dump", fake.calls)

    def test_behind_backs_up_then_upgrades_then_verifies(self):
        fake = _Ops([_vr("behind", "0010", pending=["0011"]), _vr("ok", "0011")])
        self.assertEqual(self.run_migrate(fake, self.CONFIRM), 0)
        self.assertEqual(fake.calls, ["check", "dump", "upgrade", "check"])
        self.assertEqual(fake.dump_dir, Path(self.tmp.name) / "migrate")

    def test_failed_backup_blocks_upgrade(self):
        fake = _Ops([_vr("behind", "0010")], dump_ok=False)
        self.assertEqual(self.run_migrate(fake, self.CONFIRM), 1)
        self.assertNotIn("upgrade", fake.calls)

    def test_failed_upgrade_is_reported(self):
        fake = _Ops([_vr("behind", "0010")], upgrade_rc=4)
        self.assertEqual(self.run_migrate(fake, self.CONFIRM), 4)

    def test_still_behind_after_upgrade_is_an_error(self):
        fake = _Ops([_vr("unversioned"), _vr("behind", "0010")])
        self.assertEqual(self.run_migrate(fake), 2)

    def test_ahead_and_ambiguous_are_refused(self):
        for status in ("ahead", "ambiguous", "error"):
            fake = _Ops([_vr(status, "0099")])
            with self.subTest(status=status):
                self.assertEqual(self.run_migrate(fake, self.CONFIRM), 2)
                self.assertEqual(fake.calls, ["check"])

    def test_db_unavailable(self):
        fake = _Ops([_vr("db_unavailable")])
        self.assertEqual(self.run_migrate(fake), 3)


_PW = "fixed-test-secret-" + "dbpw"


class MainTests(unittest.TestCase):
    ENV = {"REPORT_MARK_DB_URL": f"postgresql+asyncpg://research:{_PW}@postgres:5432/research",
           "REPORT_MARK_SESSION_SECRET": "fixed-test-secret-" + "b" * 32}

    def setUp(self):
        self.execs = []

    def _exec(self, file, args, env):
        self.execs.append((file, list(args), dict(env)))

    def test_web_execs_uvicorn_after_gate(self):
        rc = ep.main(["entrypoint", "web"], self.ENV, execvpe=self._exec, gate=lambda env: 0)
        self.assertEqual(rc, 0)
        self.assertEqual(self.execs[0][1], ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", "8097"])

    def test_gate_failure_never_starts(self):
        rc = ep.main(["entrypoint", "web"], self.ENV, execvpe=self._exec, gate=lambda env: 1)
        self.assertEqual(rc, 1)
        self.assertEqual(self.execs, [])

    def test_missing_config_never_reaches_gate(self):
        gate = mock.Mock(return_value=0)
        rc = ep.main(["entrypoint", "web"], {}, execvpe=self._exec, gate=gate)
        self.assertEqual(rc, 2)
        gate.assert_not_called()

    def test_unknown_command_passes_through_without_gate(self):
        gate = mock.Mock(return_value=0)
        ep.main(["entrypoint", "bash", "-c", "true"], {}, execvpe=self._exec, gate=gate)
        self.assertEqual(self.execs[0][:2], ("bash", ["bash", "-c", "true"]))
        gate.assert_not_called()

    def test_scheduler_writes_crontab_and_backup_env(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ep, "CRONTAB_PATH", Path(d) / "crontab"):
            rc = ep.main(["entrypoint", "scheduler"], self.ENV, execvpe=self._exec, gate=lambda env: 0)
            tab = (Path(d) / "crontab").read_text(encoding="utf-8")
        self.assertEqual(rc, 0)
        file, args, env = self.execs[0]
        self.assertEqual(file, "supercronic")
        self.assertIn("scripts/db_backup.sh", tab)
        self.assertEqual(env["DB_BACKUP_VIA"], "direct")
        self.assertEqual((env["PGHOST"], env["PGUSER"], env["PGDATABASE"], env["PGPASSWORD"]),
                         ("postgres", "research", "research", _PW))
        self.assertNotIn(_PW, " ".join(args), "密碼不得進 argv")

    def test_scheduler_with_bad_job_name_refuses(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ep, "CRONTAB_PATH", Path(d) / "crontab"):
            rc = ep.main(["entrypoint", "scheduler"], {**self.ENV, sch.ENABLE_ENV: "nope"},
                         execvpe=self._exec, gate=lambda env: 0)
        self.assertEqual(rc, 2)
        self.assertEqual(self.execs, [])

    def test_worker_runs_rounds(self):
        runner = mock.Mock(return_value=SimpleNamespace(returncode=0))
        ep.run_worker({ep.WORKER_INTERVAL_ENV: "10"}, runner=runner, sleep=lambda s: None, rounds=2)
        self.assertEqual(runner.call_count, 2)
        self.assertTrue(runner.call_args.args[0][1].endswith("scripts/process_uploads.sh"))


class LibpqEnvTests(unittest.TestCase):
    def test_conversion(self):
        pw = "fixed-test-secret-" + "pw"
        env = sb.libpq_env(f"postgresql+asyncpg://u:{pw}@db.example:6543/research?ssl=verify-full")
        self.assertEqual(env, {"PGHOST": "db.example", "PGPORT": "6543", "PGUSER": "u", "PGDATABASE": "research",
                               "PGPASSWORD": pw, "PGSSLMODE": "verify-full"})

    def test_no_password_no_key(self):
        self.assertNotIn("PGPASSWORD", sb.libpq_env("postgresql+asyncpg://u@h/db"))


# ───────────────────────── 每日備份的直接連線模式 ─────────────────────────

class BackupDirectModeTests(unittest.TestCase):
    """容器裡沒有 docker：DB_BACKUP_VIA=direct 時直接跑 pg_isready／pg_dump（假二進位），不碰 docker。"""

    def _fake_bin(self, d: Path, name: str, body: str) -> None:
        p = d / name
        p.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
        p.chmod(0o755)

    def test_direct_mode_dumps_without_docker(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            bindir, dest = root / "bin", root / "backups"
            bindir.mkdir()
            dest.mkdir()
            args_log = root / "pg_dump.args"
            self._fake_bin(bindir, "pg_isready", "exit 0\n")
            self._fake_bin(bindir, "pg_dump",
                           f'echo "$@" > "{args_log}"\nprintf "PGDMP"\nhead -c 4096 /dev/zero\n')
            env = {k: v for k, v in os.environ.items() if not k.startswith(("REPORT_MARK_BACKUP", "PG"))}
            env.update({
                "PATH": f"{bindir}:{env.get('PATH', '/usr/bin:/bin')}",
                "DB_BACKUP_VIA": "direct", "DOCKER_BIN": "/bin/false", "PGDATABASE": "research",
                "REPORT_MARK_BACKUP_MOUNT": "/", "REPORT_MARK_BACKUP_DIR": str(dest),
                "REPORT_MARK_BACKUP_DEFAULTS": "/nonexistent-on-purpose",
            })
            got = subprocess.run(["bash", str(BACKUP_SH)], capture_output=True, text=True, timeout=60, env=env)
            dumps = list((dest / "daily").glob("*.dump"))
            args = args_log.read_text(encoding="utf-8")
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertEqual(len(dumps), 1)
        self.assertIn("-t research.qa_log", args)
        self.assertIn("-d research", args)

    def test_unknown_mode_is_refused(self):
        env = {**os.environ, "DB_BACKUP_VIA": "ssh", "DOCKER_BIN": "/bin/false",
               "REPORT_MARK_BACKUP_DEFAULTS": "/nonexistent-on-purpose"}
        got = subprocess.run(["bash", str(BACKUP_SH)], capture_output=True, text=True, timeout=60, env=env)
        self.assertNotEqual(got.returncode, 0)
        self.assertIn("DB_BACKUP_VIA", got.stderr)


if __name__ == "__main__":
    unittest.main()
