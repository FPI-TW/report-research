# tests/test_resource_metrics.py
"""硬體用量取樣器與分析器的純函式測試（不需要 DB、不需要真實負載）。

這一支盯的是**四個錯了不會報錯、只會讓上雲選型的數字悄悄偏掉**的地方：

  1. 「服務合計」必須是「每筆樣本先加總、再取分位數」。寫成「各元件 p95 相加」
     一樣跑得出漂亮的報表，只是每個月多付一台機器的錢——沒有任何測試以外的
     東西會發現。
  2. cgroup 重建（unit 重啟）讓累計值歸零時，CPU 差分必須留空而不是記成負值
     或暴衝。一筆 -3000 核的離群值會污染整份分位數。
  3. `docker/buildx` 這種非容器子群不得被當成元件——它永遠是 0，會在報表裡
     多出一列假元件，讀者無從判斷那是「這個服務很閒」還是「量錯了」。
  4. 保留期修剪只能刪自己產生的檔（`resource-YYYYMMDD.jsonl`）。修剪邏輯誤刪
     旁邊的東西是這類常駐工具最不可接受的故障。
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import analyze_resource_usage as ana  # noqa: E402
from scripts import bench_load  # noqa: E402
from scripts import collect_resource_usage as col  # noqa: E402


def _write_cgroup(path: Path, cpu_usec: int, mem: int = 1024, io_r: int = 0, io_w: int = 0) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "cpu.stat").write_text(f"usage_usec {cpu_usec}\nuser_usec {cpu_usec}\n", encoding="utf-8")
    (path / "memory.current").write_text(f"{mem}\n", encoding="utf-8")
    (path / "memory.peak").write_text(f"{mem * 2}\n", encoding="utf-8")
    (path / "memory.stat").write_text(f"anon {mem // 2}\nfile {mem // 2}\n", encoding="utf-8")
    (path / "io.stat").write_text(
        f"8:0 rbytes={io_r} wbytes={io_w} rios=1 wios=2\n", encoding="utf-8"
    )
    (path / "pids.current").write_text("3\n", encoding="utf-8")


class QuantileTests(unittest.TestCase):
    def test_linear_interpolation_matches_numpy_default(self):
        xs = [1.0, 2.0, 3.0, 4.0]
        self.assertAlmostEqual(ana.pct(xs, 0.5), 2.5)
        self.assertAlmostEqual(ana.pct(xs, 0.0), 1.0)
        self.assertAlmostEqual(ana.pct(xs, 1.0), 4.0)
        self.assertAlmostEqual(ana.pct(xs, 0.25), 1.75)

    def test_empty_and_single(self):
        self.assertEqual(ana.pct([], 0.95), 0.0)
        self.assertEqual(ana.pct([7.0], 0.95), 7.0)

    def test_quantiles_reports_population_size(self):
        q = ana.quantiles([1.0, 2.0])
        self.assertEqual(q["n"], 2)
        self.assertEqual(q["max"], 2.0)


class RoundUpTests(unittest.TestCase):
    def test_rounds_up_to_purchasable_step(self):
        self.assertEqual(ana.round_up_to(3.4, ana.VCPU_STEPS), 4)
        self.assertEqual(ana.round_up_to(4.0, ana.VCPU_STEPS), 4)
        self.assertEqual(ana.round_up_to(4.1, ana.VCPU_STEPS), 8)

    def test_beyond_last_step_returns_last(self):
        self.assertEqual(ana.round_up_to(10_000, ana.VCPU_STEPS), ana.VCPU_STEPS[-1])


class ParseSinceTests(unittest.TestCase):
    def test_relative_forms(self):
        now = datetime.now().astimezone()
        self.assertLess(abs((now - ana.parse_since("24h")).total_seconds() - 86400), 5)
        self.assertLess(abs((now - ana.parse_since("7d")).total_seconds() - 7 * 86400), 5)

    def test_none_passes_through(self):
        self.assertIsNone(ana.parse_since(None))

    def test_bad_value_is_a_clear_error(self):
        with self.assertRaises(SystemExit):
            ana.parse_since("上禮拜")


class LoadRecordsTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _line(self, kind: str, ts: datetime, **extra) -> str:
        return json.dumps({"kind": kind, "ts": ts.isoformat(), **extra}, ensure_ascii=False)

    def test_truncated_line_is_skipped_not_fatal(self):
        """取樣器被 SIGKILL 會留下半行 JSON——那一行之前的資料全是有效的。"""
        t0 = datetime.now().astimezone()
        path = self.dir / "resource-20260828.jsonl"
        path.write_text(
            self._line("sample", t0, host={}, comp={})
            + "\n"
            + '{"kind":"sample","ts":"2026-08-2'
            + "\n"
            + self._line("sample", t0 + timedelta(seconds=20), host={}, comp={})
            + "\n",
            encoding="utf-8",
        )
        meta, samples, caps, bad = ana.load_records([path], None)
        self.assertEqual(len(samples), 2)
        self.assertEqual(bad, 1)

    def test_since_filters_and_output_is_sorted(self):
        t0 = datetime.now().astimezone()
        path = self.dir / "resource-20260828.jsonl"
        path.write_text(
            self._line("sample", t0, host={}, comp={})
            + "\n"
            + self._line("sample", t0 - timedelta(days=3), host={}, comp={})
            + "\n",
            encoding="utf-8",
        )
        _, samples, _, _ = ana.load_records([path], t0 - timedelta(hours=1))
        self.assertEqual(len(samples), 1)


class ServiceAggregationTests(unittest.TestCase):
    """本檔最重要的一條：分位數必須算在「每筆樣本的加總」上。"""

    def _samples(self):
        t0 = datetime.now().astimezone()
        # 兩個元件的尖峰刻意錯開：各自 max 都是 4.0，但同一時刻的合計永遠是 4.1。
        pairs = [(4.0, 0.1), (0.1, 4.0)] * 10
        out = []
        for i, (web, pg) in enumerate(pairs):
            out.append(
                {
                    "kind": "sample",
                    "ts": (t0 + timedelta(seconds=20 * i)).isoformat(),
                    "_ts": t0 + timedelta(seconds=20 * i),
                    "host": {"cpu_cores": web + pg + 0.5, "ncpu": 20, "mem_total": 8 << 30},
                    "comp": {
                        "web": {"cpu": web, "mem": 1 << 30, "anon": 1 << 30, "peak": 2 << 30, "pids": 5},
                        "report-mark-postgres": {
                            "cpu": pg, "mem": 1 << 30, "anon": 1 << 20, "peak": 2 << 30, "pids": 5,
                        },
                        "_init.scope": {"cpu": 0.5, "mem": 0, "anon": 0, "peak": 0, "pids": 1},
                    },
                }
            )
        return out

    def test_service_total_is_not_the_sum_of_component_maxima(self):
        r = ana.build_report({"ncpu": 20, "mem_total": 8 << 30, "interval": 20}, self._samples(), [], 0)
        comp_max_sum = sum(c["cpu"]["max"] for c in r["components"].values())
        self.assertAlmostEqual(comp_max_sum, 8.0, places=6)  # 4.0 + 4.0，錯誤寫法會得到這個
        self.assertAlmostEqual(r["service"]["cpu"]["max"], 4.1, places=6)
        self.assertLess(r["service"]["cpu"]["max"], comp_max_sum)

    def test_aggregate_components_are_excluded_from_service_total(self):
        """`_` 前綴是 cgroup 樹的第一層聚合，算進服務合計等於重複計數。"""
        r = ana.build_report({"ncpu": 20, "mem_total": 8 << 30, "interval": 20}, self._samples(), [], 0)
        self.assertNotIn("_init.scope", r["components"])
        self.assertIn("_init.scope", r["aggregates"])
        self.assertAlmostEqual(r["nonservice"]["cpu"]["p50"], 0.5, places=6)

    def test_online_and_batch_paths_are_sized_separately(self):
        """雲端會把線上與批次放在不同機器上，混算出來的 vCPU 對兩邊都是錯的。"""
        samples = self._samples()
        for sample in samples:
            sample["comp"]["sync"] = {
                "cpu": 10.0, "anon": 1 << 30, "mem": 1 << 30, "peak": 1 << 30, "pids": 40,
            }
        r = ana.build_report({"ncpu": 20, "mem_total": 8 << 30, "interval": 20}, samples, [], 0)
        sizing = r["sizing"]
        self.assertAlmostEqual(sizing["online_cpu"]["max"], 4.1, places=6)
        self.assertAlmostEqual(sizing["batch_cpu"]["max"], 10.0, places=6)
        # 服務合計仍然是兩者疊加——那條刻意保留，但報表會標明只在單機不分離時才看
        self.assertAlmostEqual(r["service"]["cpu"]["max"], 14.1, places=6)
        self.assertEqual(sizing["batch_active_share"], 1.0)

    def test_batch_active_share_is_zero_without_batch_components(self):
        r = ana.build_report({"ncpu": 20, "mem_total": 8 << 30, "interval": 20}, self._samples(), [], 0)
        self.assertEqual(r["sizing"]["batch_active_share"], 0.0)
        self.assertAlmostEqual(
            r["sizing"]["online_cpu"]["max"], r["service"]["cpu"]["max"], places=6
        )

    def test_growth_is_not_extrapolated_from_a_short_window(self):
        t0 = datetime.now().astimezone()
        caps = [
            {
                "kind": "capacity",
                "_ts": t0,
                "db": {"database_bytes": 100, "rows": {"qa_log": 1}, "tables": {}},
                "fs": [],
            },
            {
                "kind": "capacity",
                "_ts": t0 + timedelta(hours=1),
                "db": {"database_bytes": 200, "rows": {"qa_log": 3}, "tables": {}},
                "fs": [],
            },
        ]
        r = ana.build_report({"ncpu": 20, "mem_total": 8 << 30, "interval": 20}, self._samples(), caps, 0)
        self.assertFalse(r["growth"]["reliable"])
        self.assertFalse(r["sizing"]["growth_reliable"])
        self.assertEqual(r["growth"]["rows_delta"]["qa_log"], 2)

        caps[1]["_ts"] = t0 + timedelta(hours=8)
        r2 = ana.build_report({"ncpu": 20, "mem_total": 8 << 30, "interval": 20}, self._samples(), caps, 0)
        self.assertTrue(r2["growth"]["reliable"])


class ComponentDiscoveryTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self._orig = col.CGROUP_ROOT
        col.CGROUP_ROOT = self.root
        self.addCleanup(lambda: setattr(col, "CGROUP_ROOT", self._orig))

        _write_cgroup(self.root / "system.slice" / "report-mark-web.service", 1000)
        _write_cgroup(self.root / "system.slice" / "unrelated.service", 1000)
        _write_cgroup(self.root / "docker" / ("a" * 64), 2000)
        _write_cgroup(self.root / "docker" / "buildx", 3000)
        # 真實的 /sys/fs/cgroup/docker 自己也有 cpu.stat（它是頂層聚合）
        (self.root / "docker" / "cpu.stat").write_text("usage_usec 5000\n", encoding="utf-8")
        _write_cgroup(self.root / "init.scope", 4000)

    def test_unit_prefix_is_stripped_and_foreign_units_ignored(self):
        comps = col.discover_components({})
        self.assertIn("web", comps)
        self.assertNotIn("unrelated", comps)

    def test_non_container_docker_subgroup_is_not_a_component(self):
        comps = col.discover_components({})
        self.assertNotIn("container:buildx", comps)
        self.assertIn("container:" + "a" * 12, comps)

    def test_resolved_container_name_wins(self):
        comps = col.discover_components({"a" * 64: "report-mark-postgres"})
        self.assertIn("report-mark-postgres", comps)

    def test_top_level_aggregates_are_prefixed(self):
        comps = col.discover_components({})
        self.assertIn("_init.scope", comps)
        self.assertIn("_docker", comps)


class SamplerDeltaTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self._orig = col.CGROUP_ROOT
        col.CGROUP_ROOT = self.root
        self.addCleanup(lambda: setattr(col, "CGROUP_ROOT", self._orig))
        self.web = self.root / "system.slice" / "report-mark-web.service"

    def test_first_sample_is_withheld(self):
        """半個差分算出來的速率是錯的，而錯的第一筆會被分位數當成真實觀測。"""
        _write_cgroup(self.web, 1_000_000)
        s = col.Sampler(docker_bin=None)
        self.assertIsNone(s.sample())

    def test_cpu_delta_matches_usage_usec_over_wall_time(self):
        _write_cgroup(self.web, 1_000_000)
        s = col.Sampler(docker_bin=None)
        s.sample()
        time.sleep(0.3)
        _write_cgroup(self.web, 1_500_000)  # ＋0.5 秒 CPU
        rec = s.sample()
        self.assertIsNotNone(rec)
        # 用「速率 × dt ≈ 0.5 秒 CPU」而不是直接比速率：輸出的 dt 只保留兩位小數，
        # 短窗期下拿它回推速率的捨入誤差會大過被測邏輯本身。
        dt = rec["host"]["dt"]
        self.assertAlmostEqual(rec["comp"]["web"]["cpu"] * dt, 0.5, delta=0.03)

    def test_counter_reset_leaves_cpu_absent_instead_of_negative(self):
        """unit 重啟會讓 cgroup 重建、累計值歸零。"""
        _write_cgroup(self.web, 9_000_000)
        s = col.Sampler(docker_bin=None)
        s.sample()
        _write_cgroup(self.web, 5)  # 重建
        rec = s.sample()
        self.assertNotIn("cpu", rec["comp"]["web"])
        self.assertIn("mem", rec["comp"]["web"])  # 存量指標仍然有效


class WriterTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "metrics"
        self.addCleanup(self.tmp.cleanup)

    def test_writes_one_file_per_day_and_flushes(self):
        w = col.Writer(self.dir, retention_days=30)
        w.write({"kind": "meta", "ts": "x"})
        today = datetime.now().strftime("%Y%m%d")
        path = self.dir / f"resource-{today}.jsonl"
        self.assertTrue(path.exists())
        self.assertIn('"kind":"meta"', path.read_text(encoding="utf-8"))  # flush 過才讀得到
        w.close()

    def test_prune_only_touches_its_own_naming_scheme(self):
        w = col.Writer(self.dir, retention_days=1)
        old = self.dir / "resource-20200101.jsonl"
        foreign = self.dir / "keep-me.txt"
        odd = self.dir / "resource-notadate.jsonl"
        for f in (old, foreign, odd):
            f.write_text("x", encoding="utf-8")
        w._prune()
        self.assertFalse(old.exists())
        self.assertTrue(foreign.exists())
        self.assertTrue(odd.exists())
        w.close()

    def test_retention_zero_disables_pruning(self):
        w = col.Writer(self.dir, retention_days=0)
        old = self.dir / "resource-20200101.jsonl"
        old.write_text("x", encoding="utf-8")
        w._prune()
        self.assertTrue(old.exists())
        w.close()


class BenchMarginalCostTests(unittest.TestCase):
    """壓測換算的核心：**邊際**核心秒，不是總量。

    不扣掉閒置基線的話，請求數越少、單條成本被高估得越誇張——而「先量單條成本」
    正是這份量測最主要的用途，錯在這裡等於整個結論失效。
    """

    def _samples(self, start, count, cores, dt=5.0, anon=1 << 30):
        return [
            {
                "_ts": start + timedelta(seconds=dt * i),
                "host": {"dt": dt, "cpu_cores": cores + 0.5, "ncpu": 20},
                "comp": {
                    "web": {"cpu": cores * 0.8, "anon": anon, "mem": anon, "peak": anon, "pids": 5},
                    "report-mark-postgres": {
                        "cpu": cores * 0.2, "anon": 1 << 20, "mem": 1 << 20, "peak": 1 << 20, "pids": 5,
                    },
                },
            }
            for i in range(count)
        ]

    def _bench(self, start, end, ok=1):
        return {
            "kind": "bench",
            "endpoint": "ask",
            "concurrency": 1,
            "started": start.isoformat(),
            "ended": end.isoformat(),
            "summary": {"n": ok, "ok": ok, "errors": 0, "queued": 0},
        }

    def test_idle_baseline_is_subtracted(self):
        t0 = datetime.now().astimezone()
        base_start = t0 - timedelta(seconds=60)
        samples = self._samples(base_start, 12, cores=0.1) + self._samples(t0, 12, cores=2.1)
        b = ana.build_bench_section(self._bench(t0, t0 + timedelta(seconds=55)), samples)
        self.assertAlmostEqual(b["baseline_cores"], 0.1, places=6)
        self.assertAlmostEqual(b["total_core_seconds"], 2.1 * 60, places=3)
        self.assertAlmostEqual(b["marginal_core_seconds"], 2.0 * 60, places=3)
        self.assertAlmostEqual(b["core_seconds_per_request"], 120.0, places=3)

    def test_marginal_cost_is_split_by_component(self):
        t0 = datetime.now().astimezone()
        samples = self._samples(t0 - timedelta(seconds=60), 12, cores=0.0) + self._samples(
            t0, 12, cores=1.0
        )
        b = ana.build_bench_section(self._bench(t0, t0 + timedelta(seconds=55)), samples)
        by = b["marginal_core_seconds_by_component"]
        self.assertAlmostEqual(by["web"], 48.0, places=3)              # 0.8 核 × 60 秒
        self.assertAlmostEqual(by["report-mark-postgres"], 12.0, places=3)
        self.assertEqual(list(by)[0], "web")  # 由大到小排序

    def test_thin_window_is_flagged_not_silently_reported(self):
        t0 = datetime.now().astimezone()
        samples = self._samples(t0 - timedelta(seconds=10), 2, cores=0.1) + self._samples(
            t0, 2, cores=2.0
        )
        b = ana.build_bench_section(self._bench(t0, t0 + timedelta(seconds=5)), samples)
        self.assertFalse(b["reliable"])

    def test_missing_baseline_does_not_divide_by_zero(self):
        t0 = datetime.now().astimezone()
        b = ana.build_bench_section(
            self._bench(t0, t0 + timedelta(seconds=55)), self._samples(t0, 12, cores=1.0)
        )
        self.assertEqual(b["baseline_samples"], 0)
        self.assertEqual(b["baseline_cores"], 0.0)
        self.assertGreater(b["marginal_core_seconds"], 0)

    def test_batch_overlap_is_flagged_not_silently_folded_in(self):
        """2026-08-28 首次壓測正好撞上 12:00 的 sync，兩者各吃約 10 核。

        合計的「單條成本」在這種窗期是錯的，而它看起來完全正常——所以污染必須
        變成報表上的警語與 reliable=False，不能只留在數字裡。
        """
        t0 = datetime.now().astimezone()
        base = self._samples(t0 - timedelta(seconds=60), 12, cores=0.1)
        window = self._samples(t0, 12, cores=1.0)
        for sample in window:
            sample["comp"]["sync"] = {
                "cpu": 9.9, "anon": 1 << 30, "mem": 1 << 30, "peak": 1 << 30, "pids": 40,
            }
        b = ana.build_bench_section(self._bench(t0, t0 + timedelta(seconds=55)), base + window)
        self.assertIn("sync", b["contaminated_by"])
        self.assertFalse(b["reliable"])
        # 分元件仍要給得出乾淨的 web 數字——那是污染窗期唯一還能用的東西。
        # 48.0（窗期 0.8 核 × 60 秒）扣掉 web 自己的基線 4.8（0.08 核 × 60 秒）＝ 43.2。
        self.assertAlmostEqual(b["core_seconds_per_request_by_component"]["web"], 43.2, places=3)

    def test_idle_batch_does_not_trigger_the_flag(self):
        t0 = datetime.now().astimezone()
        samples = self._samples(t0 - timedelta(seconds=60), 12, cores=0.1) + self._samples(
            t0, 12, cores=1.0
        )
        b = ana.build_bench_section(self._bench(t0, t0 + timedelta(seconds=55)), samples)
        self.assertEqual(b["contaminated_by"], {})
        self.assertTrue(b["reliable"])

    def test_zero_successful_requests_yields_zero_not_crash(self):
        t0 = datetime.now().astimezone()
        bench = self._bench(t0, t0 + timedelta(seconds=55), ok=0)
        b = ana.build_bench_section(bench, self._samples(t0, 12, cores=1.0))
        self.assertEqual(b["core_seconds_per_request"], 0.0)


class BenchEnvParsingTests(unittest.TestCase):
    """壓測腳本讀 .env 是**唯讀**的；語意要與 web/env_loader.py 一致（不做 shell 展開）。"""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / ".env"
        self.addCleanup(self.tmp.cleanup)

    def test_quotes_stripped_comments_skipped_no_expansion(self):
        self.path.write_text(
            '# 註解\nA="v1"\nB=\'v2\'\nC=$HOME/x\n\nD=has=equals\n', encoding="utf-8"
        )
        env = bench_load.read_env_file(self.path)
        self.assertEqual(env["A"], "v1")
        self.assertEqual(env["B"], "v2")
        self.assertEqual(env["C"], "$HOME/x")
        self.assertEqual(env["D"], "has=equals")
        self.assertNotIn("#", env)

    def test_missing_file_is_empty_not_an_error(self):
        self.assertEqual(bench_load.read_env_file(self.path / "nope"), {})

    def test_reading_does_not_modify_the_file(self):
        original = "A=1\n"
        self.path.write_text(original, encoding="utf-8")
        bench_load.read_env_file(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), original)


class BenchOfflineModeTests(unittest.TestCase):
    def test_offline_mode_skips_login_and_writes_separate_result(self):
        result = {
            "kind": "rerank_micro", "legacy_median_s": 1.0, "current_median_s": 0.9,
            "max_abs_score_delta": 0.0, "ranking_identical": True,
        }
        with tempfile.TemporaryDirectory() as folder, \
             mock.patch.object(bench_load, "run_offline_rerank", return_value=result) as run, \
             mock.patch.object(bench_load, "resolve_credentials", side_effect=AssertionError("login")):
            rc = bench_load.main([
                "--offline-rerank", "--limit", "8", "--repeat", "2", "--torch-threads", "4",
                "--out-dir", folder,
            ])
            outputs = list(Path(folder).glob("bench-rerank-*.json"))
            self.assertEqual(len(outputs), 1)
            self.assertEqual(json.loads(outputs[0].read_text(encoding="utf-8")), result)
        self.assertEqual(rc, 0)
        run.assert_called_once_with(repeat=2, threads=4, pairs=8)

    def test_invalid_offline_limit_stops_before_model_load(self):
        with mock.patch.object(bench_load, "run_offline_rerank") as run:
            self.assertEqual(bench_load.main(["--offline-rerank", "--limit", "51"]), 2)
            self.assertEqual(bench_load.main(["--offline-rerank", "--limit", "0"]), 2)
        run.assert_not_called()


class CgroupParsingTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_missing_cgroup_returns_none(self):
        self.assertIsNone(col.cgroup_snapshot(self.dir / "nope"))

    def test_io_stat_sums_across_devices(self):
        path = self.dir / "cg"
        _write_cgroup(path, 10)
        (path / "io.stat").write_text(
            "8:0 rbytes=100 wbytes=200 rios=1 wios=2\n253:0 rbytes=50 wbytes=5 rios=1 wios=1\n",
            encoding="utf-8",
        )
        snap = col.cgroup_snapshot(path)
        self.assertEqual(snap["io_r"], 150)
        self.assertEqual(snap["io_w"], 205)

    def test_garbage_values_do_not_raise(self):
        path = self.dir / "cg"
        _write_cgroup(path, 10)
        (path / "memory.current").write_text("not-a-number\n", encoding="utf-8")
        (path / "io.stat").write_text("8:0 rbytes=abc wbytes=\n", encoding="utf-8")
        snap = col.cgroup_snapshot(path)
        self.assertEqual(snap["mem"], 0)
        self.assertEqual(snap["io_r"], 0)


# ── 監控 spool（Host／Container／Service 觀測與批次執行紀錄）──────────────────

INV_A = "a" * 32
INV_B = "b" * 32
PROD_CATALOG = Path(__file__).resolve().parents[1] / "deploy" / "ops" / "services.prod.toml"


def _props(**kw) -> dict[str, str]:
    """`systemctl show` 一個 unit 的屬性（預設：從未執行過——所有欄位都是預設值）。"""
    base = {
        "Id": "report-mark-sync.service", "LoadState": "loaded", "ActiveState": "inactive", "SubState": "dead",
        "Result": "success", "UnitFileState": "static", "NRestarts": "0", "ExecMainCode": "0",
        "ExecMainStatus": "0", "ExecMainStartTimestamp": "", "ExecMainStartTimestampMonotonic": "0",
        "ExecMainExitTimestamp": "", "ExecMainExitTimestampMonotonic": "0", "InvocationID": "",
    }
    base.update(kw)
    return base


def _running(inv=INV_A, start=1791270010, mono=1000) -> dict[str, str]:
    return _props(ActiveState="activating", SubState="start", InvocationID=inv,
                  ExecMainStartTimestamp=f"@{start}", ExecMainStartTimestampMonotonic=str(mono))


def _finished(inv=INV_A, start=1791270010, end=1791270100, mono=1000, result="success", status="0",
              active="inactive") -> dict[str, str]:
    return _props(ActiveState=active, InvocationID=inv, Result=result, ExecMainCode="1", ExecMainStatus=status,
                  ExecMainStartTimestamp=f"@{start}", ExecMainStartTimestampMonotonic=str(mono),
                  ExecMainExitTimestamp=f"@{end}", ExecMainExitTimestampMonotonic=str(mono + 90))


SYNC = {"name": "sync", "kind": "systemd", "tier": "important", "unit": "report-mark-sync.service",
        "timer": "report-mark-sync.timer", "container": None}


class CatalogTargetsTests(unittest.TestCase):
    """名單取自維運代理的同一份 catalog；讀不到只降級服務／容器觀測，不拋例外。"""

    def test_prod_catalog_lists_containers_and_timer_units(self):
        targets = {t["name"]: t for t in col.load_catalog_targets(PROD_CATALOG)}
        self.assertEqual(targets["postgres"]["container"], "report-mark-postgres")
        self.assertIsNone(targets["postgres"]["unit"])
        self.assertEqual(targets["sync"]["timer"], "report-mark-sync.timer")
        self.assertEqual(targets["audit"]["unit"], "report-mark-audit.service")
        self.assertIsNone(targets["metrics"]["timer"])

    def test_missing_or_broken_catalog_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "c.toml"
            broken.write_text("[[services]\nname=", encoding="utf-8")
            with mock.patch("sys.stderr"):
                self.assertEqual(col.load_catalog_targets(Path(tmp) / "nope.toml"), [])
                self.assertEqual(col.load_catalog_targets(broken), [])

    def test_option_like_and_unknown_entries_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.toml"
            path.write_text(
                '[[services]]\nname = "evil"\nkind = "systemd"\nunit = "--all"\n'
                '[[services]]\nname = "x"\nkind = "socket"\nunit = "x.socket"\n'
                '[[services]]\nname = "pg"\nkind = "container"\ncontainer = "report-mark-postgres"\n',
                encoding="utf-8",
            )
            self.assertEqual([t["name"] for t in col.load_catalog_targets(path)], ["pg"])


class SystemdShowParsingTests(unittest.TestCase):
    def test_blocks_are_keyed_by_id_not_order(self):
        """systemd 依自己的順序輸出屬性（Id 常在最後），多個 unit 以空行分段。"""
        text = ("Result=success\nActiveState=inactive\nId=report-mark-audit.service\n\n"
                "ActiveState=activating\nInvocationID=" + INV_A + "\nId=report-mark-sync.service\n")
        blocks = col.parse_show_blocks(text)
        self.assertEqual(blocks["report-mark-sync.service"]["ActiveState"], "activating")
        self.assertEqual(blocks["report-mark-audit.service"]["Result"], "success")

    def test_unix_timestamp_requires_the_at_form(self):
        self.assertEqual(col.unix_ts("@0").timestamp(), 0)
        self.assertIsNone(col.unix_ts(""))
        self.assertIsNone(col.unix_ts("Mon 2026-10-06 15:00:10 CST"))

    def test_systemctl_failure_is_empty_not_an_exception(self):
        with mock.patch.object(col.subprocess, "run", side_effect=OSError("nope")):
            self.assertEqual(col.systemd_show("systemctl", ["a.service"]), {})


class JobRecordTests(unittest.TestCase):
    """批次「跑過」的判準比照 verify_oneshot_ran.sh：Result=success／status 0 不是證據。"""

    def setUp(self):
        self.obs = col.Observer([SYNC], None, host="office-host")

    def _jobs(self, props):
        return self.obs.job_records(SYNC, props, "2026-10-06T15:00:00+08:00")

    def test_never_ran_unit_is_not_a_job_even_with_success(self):
        self.assertEqual(self._jobs(_props()), [])
        # 有啟動時戳卻沒有 InvocationID（被回收後重載的乾淨狀態）同樣不算
        self.assertEqual(self._jobs(_props(ExecMainStartTimestamp="@1", ExecMainStartTimestampMonotonic="5")), [])

    def test_running_then_finished_writes_one_line_per_state(self):
        first = self._jobs(_running())
        self.assertEqual([(j["state"], j["invocation_id"]) for j in first], [("running", INV_A)])
        self.assertIsNone(first[0]["finished_at"])
        self.assertIsNone(first[0]["result"])
        self.assertEqual(self._jobs(_running()), [], "狀態沒變不重寫")
        done = self._jobs(_finished())
        self.assertEqual(len(done), 1)
        j = done[0]
        self.assertEqual((j["state"], j["result"], j["exit_status"], j["exec_main_code"]),
                         ("finished", "success", 0, "exited"))
        self.assertEqual(datetime.fromisoformat(j["finished_at"]).timestamp(), 1791270100)
        self.assertEqual(datetime.fromisoformat(j["started_at"]).timestamp(), 1791270010)
        self.assertIsNotNone(datetime.fromisoformat(j["started_at"]).tzinfo, "時間一律帶時區")
        self.assertEqual(self._jobs(_finished()), [])

    def test_failed_run_keeps_systemd_result(self):
        [j] = self._jobs(_finished(result="exit-code", status="1", active="failed"))
        self.assertEqual((j["state"], j["result"], j["exit_status"]), ("finished", "exit-code", 1))

    def test_exit_before_start_is_a_cross_invocation_read_and_is_skipped(self):
        props = _finished()
        props["ExecMainExitTimestampMonotonic"] = "10"  # 上一次的完成時戳、這一次的啟動時戳
        self.assertEqual(self._jobs(props), [])

    def test_collector_does_not_invent_an_end_for_an_unseen_finish(self):
        """running 的 A 沒被看到結束、下一次 B 已經開始：只寫 B，A 交給 loader 判 lost（不捏造結束時間）。"""
        self._jobs(_running(INV_A))
        out = self._jobs(_running(INV_B, start=1791280000, mono=5000))
        self.assertEqual([(j["invocation_id"], j["state"]) for j in out], [(INV_B, "running")])


class _FakeRun:
    """依 argv[0]／子命令回不同的 subprocess.run 結果。"""

    def __init__(self, show: str = "", inspect: str = "", inspect_rc: int = 0, inspect_err: str = ""):
        self.show, self.inspect, self.inspect_rc, self.inspect_err = show, inspect, inspect_rc, inspect_err
        self.calls: list[list[str]] = []

    def __call__(self, argv, **kw):
        self.calls.append(list(argv))
        if argv[1] == "show":
            return mock.Mock(returncode=0, stdout=self.show, stderr="")
        return mock.Mock(returncode=self.inspect_rc, stdout=self.inspect, stderr=self.inspect_err)


def _show_text(*blocks: dict) -> str:
    return "\n\n".join("\n".join(f"{k}={v}" for k, v in b.items()) for b in blocks) + "\n"


class ObserverRecordsTests(unittest.TestCase):
    TARGETS = [
        {"name": "postgres", "kind": "container", "tier": "critical", "unit": None, "timer": None,
         "container": "report-mark-postgres"},
        {"name": "nginx", "kind": "container", "tier": "critical", "unit": None, "timer": None,
         "container": "deploy-nginx-1"},
        {"name": "web", "kind": "systemd", "tier": "critical", "unit": "report-mark-web.service", "timer": None,
         "container": None},
        SYNC,
        {"name": "health", "kind": "systemd", "tier": "important", "unit": "report-mark-health.service",
         "timer": "report-mark-health.timer", "container": None},
    ]

    def _records(self, fake: _FakeRun, sample=None):
        obs = col.Observer(self.TARGETS, "docker", host="office-host", fs_paths=[])
        with mock.patch.object(col.subprocess, "run", fake):
            return obs.records(sample)

    def _fake(self, **kw):
        inspect = json.dumps({"name": "/report-mark-postgres", "restart_count": 2,
                              "state": {"Status": "running", "ExitCode": 0, "OOMKilled": False,
                                        "StartedAt": "2026-10-01T00:00:00Z",
                                        "Health": {"Status": "healthy", "Log": [{"Output": "secret-ish"}]}}})
        show = _show_text(
            _props(Id="report-mark-web.service", ActiveState="active", SubState="running", NRestarts="3"),
            _finished() | {"Id": "report-mark-sync.service"},
            _finished(inv=INV_B) | {"Id": "report-mark-health.service"},
        )
        return _FakeRun(show=show, inspect=inspect, inspect_rc=1, inspect_err="Error: No such object", **kw)

    def test_full_round_has_host_container_service_and_job(self):
        sample = {"host": {"ncpu": 4, "cpu_cores": 1.0, "iowait_cores": 0.2, "disk_r": 10, "disk_w": 20,
                           "disk_iops": 3},
                  "comp": {"report-mark-postgres": {"cpu": 0.5, "mem": 1000}, "web": {"cpu": 0.25, "mem": 2000}}}
        recs = self._records(self._fake(), sample)
        by = {(r["type"], r.get("scope"), r.get("subject") or r.get("unit")): r for r in recs}

        host = by[("observation", "host", "host")]["metrics"]
        self.assertAlmostEqual(host["cpu_pct"], 25.0)
        self.assertAlmostEqual(host["iowait_pct"], 5.0)
        for key in ("mem_used_pct", "load1", "load5", "load15", "disk_read_bps", "disk_write_bps", "disk_iops"):
            self.assertIn(key, host)

        pg = by[("observation", "container", "postgres")]
        self.assertEqual(pg["states"], {"status": "running", "health": "healthy"})
        self.assertEqual(pg["metrics"], {"cpu_cores": 0.5, "mem_bytes": 1000.0, "restart_count": 2.0})
        self.assertNotIn("secret-ish", json.dumps(recs), "健康檢查輸出不得寫進 spool")
        self.assertEqual(by[("observation", "container", "nginx")]["states"], {"status": "missing"})

        web = by[("observation", "service", "web")]
        self.assertEqual(web["states"], {"active_state": "active"})
        self.assertEqual(web["detail"]["n_restarts"], 3)
        self.assertEqual(web["metrics"], {"cpu_cores": 0.25, "mem_bytes": 2000.0})

        jobs = [r for r in recs if r["type"] == "job"]
        self.assertEqual([j["unit"] for j in jobs], ["report-mark-sync.service"],
                         "每 2 分鐘的探針不算批次；沒有 timer 的常駐服務也不算")
        self.assertEqual(jobs[0]["service"], "sync")
        for r in recs:
            self.assertEqual(r["v"], 1)
            self.assertEqual(r["host"], "office-host")
            self.assertIsNotNone(datetime.fromisoformat(r["observed_at"]).tzinfo)
            for metric in (*r.get("metrics", {}), *r.get("states", {})):
                self.assertRegex(metric, r"^[a-z][a-z0-9_]{0,63}$")

    def test_docker_unavailable_skips_containers_but_keeps_services(self):
        fake = self._fake()
        fake.inspect, fake.inspect_err = "", "Cannot connect to the Docker daemon"
        recs = self._records(fake)
        self.assertFalse([r for r in recs if r.get("scope") == "container"])
        self.assertTrue([r for r in recs if r.get("scope") == "service"])

    def test_systemctl_unavailable_skips_services_but_keeps_host(self):
        fake = self._fake()
        fake.show = ""
        recs = self._records(fake)
        self.assertFalse([r for r in recs if r.get("scope") == "service" or r["type"] == "job"])
        self.assertTrue([r for r in recs if r.get("scope") == "host"])

    def test_container_state_is_cached_for_ttl(self):
        fake = self._fake()
        obs = col.Observer(self.TARGETS, "docker", host="h", fs_paths=[], container_ttl=3600)
        with mock.patch.object(col.subprocess, "run", fake):
            obs.records()
            obs.records()
        self.assertEqual(sum(1 for c in fake.calls if c[1] == "inspect"), 1)


class SpoolWriterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "spool"
        self.addCleanup(self.tmp.cleanup)

    def test_records_go_to_per_type_daily_files(self):
        w = col.SpoolWriter(self.dir)
        n = w.write([{"type": "observation", "x": 1}, {"type": "job", "x": 2}, {"type": "incident", "x": 3}])
        self.assertEqual(n, 2, "不認得的種類不寫")
        today = datetime.now().strftime("%Y%m%d")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()),
                         [f"jobs-{today}.jsonl", f"observations-{today}.jsonl"])
        line = (self.dir / f"observations-{today}.jsonl").read_text(encoding="utf-8")
        self.assertTrue(line.endswith("\n"))
        self.assertEqual(json.loads(line)["x"], 1)

    def test_prune_only_touches_its_own_naming_scheme(self):
        self.dir.mkdir(parents=True)
        old = [self.dir / "observations-20200101.jsonl", self.dir / "jobs-20200101.jsonl"]
        keep = [self.dir / "incidents-20200101.jsonl", self.dir / "observations-bad.jsonl",
                self.dir / ".loader-state.json"]
        for f in old + keep:
            f.write_text("x\n", encoding="utf-8")
        col.SpoolWriter(self.dir, retention_days=14).write([])
        self.assertFalse(any(f.exists() for f in old))
        self.assertTrue(all(f.exists() for f in keep))


class CollectorLoopSpoolTests(unittest.TestCase):
    """常駐模式：spool 有寫、--once 不寫、觀測那一段的故障拖不垮硬體取樣。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        col._STOP = False
        self.args = ["--interval", "0.3", "--duration", "1.6", "--observe-interval", "0.3", "--no-db",
                     "--docker-bin", "/nonexistent/docker", "--catalog", str(self.root / "none.toml"),
                     "--out-dir", str(self.root / "metrics"), "--spool-dir", str(self.root / "spool")]

    def test_loop_writes_observations_to_spool(self):
        with mock.patch("sys.stderr"):
            self.assertEqual(col.main(self.args), 0)
        files = list((self.root / "spool").glob("observations-*.jsonl"))
        self.assertEqual(len(files), 1)
        recs = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(r["subject"] == "host" and "cpu_pct" in r["metrics"] for r in recs))

    def test_observer_failure_does_not_stop_hardware_sampling(self):
        with mock.patch.object(col.Observer, "records", side_effect=RuntimeError("boom")), mock.patch("sys.stderr"):
            self.assertEqual(col.main(self.args), 0)
        metrics = list((self.root / "metrics").glob("resource-*.jsonl"))
        kinds = [json.loads(line)["kind"] for line in metrics[0].read_text(encoding="utf-8").splitlines()]
        self.assertIn("sample", kinds)

    def test_once_prints_observations_without_writing_spool(self):
        args = ["--once", "--interval", "0.1", "--no-db", "--docker-bin", "/nonexistent/docker",
                "--catalog", str(self.root / "none.toml"), "--spool-dir", str(self.root / "spool")]
        with mock.patch("builtins.print") as out, mock.patch("sys.stderr"):
            self.assertEqual(col.main(args), 0)
        printed = json.loads(out.call_args_list[-1].args[0])
        self.assertEqual(printed["observations"][0]["subject"], "host")
        self.assertFalse((self.root / "spool").exists())

    def test_default_spool_dir_follows_env(self):
        with mock.patch.dict("os.environ", {"OPS_SPOOL_DIR": "/x/y"}):
            self.assertEqual(col.default_spool_dir(), Path("/x/y"))
        with mock.patch.dict("os.environ", {"OPS_SPOOL_DIR": ""}):
            self.assertEqual(col.default_spool_dir(), col.REPO_ROOT / "data" / "ops_spool")


if __name__ == "__main__":
    unittest.main()
