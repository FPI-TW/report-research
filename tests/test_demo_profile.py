"""示範券商（profiles/demo）、合成語料與示範 compose 的靜態對帳。

示範 profile 的存在理由是證明框架不依賴廷豐的設定：這裡釘住它真的「每一項都不同」、合成語料的側檔組合真的
涵蓋連接器的各條路徑（含故意寫錯的那幾份），以及 compose override 把每一個 LLM 任務都指到假 LLM 宣告過的
模型名——少一個旋鈕，那個任務在端到端測試裡就會打到不存在的模型、以 config 失敗，而且多半被 fail-open 吞掉。
不跑 docker（那是 `scripts/e2e_compose.py` 的事）。
"""

from __future__ import annotations

import re
import sys
import unittest
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.profile import load_profile  # noqa: E402
from app.services.connectors import sidecar  # noqa: E402
from app.services.llm_models import TASK_ENV  # noqa: E402
from deploy.demo import fake_llm  # noqa: E402
from scripts import demo_corpus  # noqa: E402

DEMO = REPO_ROOT / "profiles" / "demo"
TINGFONG = REPO_ROOT / "profiles" / "tingfong"
DEMO_COMPOSE = REPO_ROOT / "deploy" / "demo" / "docker-compose.demo.yml"
REAL_LLM_COMPOSE = REPO_ROOT / "deploy" / "demo" / "docker-compose.real-llm.yml"
E2E_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "e2e.yml"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
ANCHOR = date(2026, 11, 10)


class DemoProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = load_profile(DEMO)
        cls.tf = load_profile(TINGFONG)

    def test_everything_differs_from_tingfong(self):
        d, t = self.demo, self.tf
        self.assertNotEqual(d.institution, t.institution)
        self.assertNotEqual(d.prompt, t.prompt)
        self.assertNotEqual(d.security.totp_issuer, t.security.totp_issuer)
        self.assertNotEqual({a.sha256 for a in d.branding.assets().values()},
                            {a.sha256 for a in t.branding.assets().values()})
        self.assertTrue(d.branding.colors_light and d.branding.colors_dark)  # 廷豐沿用預設色票
        self.assertLess(set(d.markets.enabled), set(t.markets.enabled))
        self.assertEqual(d.markets.enabled, ("TW", "US", "MACRO"))
        self.assertEqual([s.kind for s in d.connectors.enabled_specs()], ["s3"])
        self.assertEqual(t.connectors.enabled, ())
        self.assertTrue(d.backfill.background_enabled)
        self.assertFalse(t.backfill.background_enabled)
        self.assertLess(d.backfill.max_reports_per_round, t.backfill.max_reports_per_round)
        self.assertLess(d.backfill.daily_token_limit, t.backfill.daily_token_limit)

    def test_generic_assumptions_differ_from_tingfong(self):
        """fw-generic：代號格式、發行機構名單、簡報作息都是 profile 的設定；示範券商開美股 ticker、用自己的名單。"""
        d, t = self.demo, self.tf
        rule = d.instruments.normalize_code
        self.assertEqual([rule(v) for v in ("2330", "00878", "nvda", "BRK.B", "NVIDIA")],
                         ["2330", "00878", "NVDA", "BRK.B", None])
        self.assertEqual([t.instruments.normalize_code(v) for v in ("2330", "00878", "NVDA")], ["2330", None, None])
        self.assertEqual(dict(d.sources.display), demo_corpus.BROKERS)
        self.assertFalse(set(d.sources.display) & set(t.sources.display))
        self.assertEqual({s for _, s in d.sources.filename_tokens}, set(demo_corpus.BROKERS))
        self.assertEqual((d.brief.after_hour, t.brief.after_hour), (0, 9))

    def test_corpus_filenames_resolve_to_their_broker(self):
        """沒有側檔的研報靠檔名推得發行機構；檔名裡的美股 ticker 不被當成發行機構。"""
        from unittest.mock import patch

        from app import profile as P
        from app.services import filename

        with patch.object(filename, "get_profile", return_value=self.demo):
            for r in demo_corpus.plan(ANCHOR):
                with self.subTest(file=r.file):
                    self.assertEqual(filename.parse_filename(Path(r.file).name).source, r.broker)
            self.assertIsNone(filename.parse_filename("20251001_JPM_Chase.pdf").source)
        self.assertEqual(P.get_profile().sources, self.tf.sources)  # patch 只在區塊內

    def test_names_say_fictional(self):
        self.assertIn("虛構", self.demo.institution.name)

    def test_svg_assets_are_plain_geometry(self):
        for asset in self.demo.branding.assets().values():
            with self.subTest(asset=asset.name):
                text = asset.path.read_text(encoding="utf-8")
                root = ET.fromstring(text)
                self.assertTrue(root.tag.endswith("svg"))
                self.assertNotRegex(text, r"(?i)<script|<image|<foreignObject|href=|url\(|@import|<!DOCTYPE|<!ENTITY")


class DemoCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reports = demo_corpus.plan(ANCHOR)

    def test_deterministic(self):
        again = demo_corpus.plan(ANCHOR)
        self.assertEqual([r.__dict__ for r in again], [r.__dict__ for r in self.reports])
        r = self.reports[0]
        self.assertEqual(demo_corpus.render_html(r, demo_corpus.DEFAULT_SEED),
                         demo_corpus.render_html(r, demo_corpus.DEFAULT_SEED))

    def test_size_and_layout_features(self):
        self.assertTrue(20 <= len(self.reports) <= 40)
        page = demo_corpus.render_html(self.reports[0], demo_corpus.DEFAULT_SEED)
        for feature in ("@top-left", "@bottom-right", "counter(page)", "column-count: 2", "<table"):
            self.assertIn(feature, page)
        self.assertIn(demo_corpus.DISCLAIMER, page)
        self.assertIn("虛構研報", page)

    def test_sidecar_mix(self):
        kinds = {r.sidecar for r in self.reports}
        self.assertLessEqual({"full", "partial", "csv", "none", "invalid_market", "invalid_date", "invalid_field",
                              "invalid_json"}, kinds)

    def test_valid_sidecars_pass_and_broken_ones_are_rejected(self):
        for r in self.reports:
            raw = demo_corpus.sidecar_bytes(r)
            if raw is None:
                continue
            with self.subTest(file=r.file, kind=r.sidecar):
                if r.ingests:
                    meta = sidecar.parse_json_bytes(raw, r.file)
                    self.assertEqual(meta.skips_tagging(), r.sidecar == "full")
                else:
                    with self.assertRaises(sidecar.SidecarError):
                        sidecar.parse_json_bytes(raw, r.file)
        manifest = sidecar.parse_manifest(demo_corpus.manifest_bytes(self.reports))
        self.assertEqual(set(manifest.rows), {r.file for r in self.reports if r.sidecar == "csv"})

    def test_signal_subset_and_tiers_have_material(self):
        # extract_signals 的預設子集門檻：同一檔 ≥3 家券商、≥5 份（只算有 source 的入庫研報）。
        brokers, counts = defaultdict(set), defaultdict(int)
        for r in self.reports:
            if r.ingests and r.code and r.sidecar != "none":
                brokers[(r.market, r.code)].add(r.broker)
                counts[(r.market, r.code)] += 1
        subset = [k for k in counts if counts[k] >= 5 and len(brokers[k]) >= 3]
        self.assertGreaterEqual(len(subset), 2, subset)
        old = [r for r in self.reports if r.ingests and r.report_date < ANCHOR - timedelta(days=366)]
        self.assertGreaterEqual(len(old), 1)  # 第三層（背景補齊）有東西可補
        self.assertTrue(any(r.ingests and r.market not in ("TW", "US", "MACRO") for r in self.reports))

    def test_expected_values_follow_the_plan(self):
        exp = demo_corpus.expected(self.reports, ANCHOR)
        self.assertEqual(exp["ingested"] + len(exp["sidecar_invalid"]), exp["total_files"])
        self.assertEqual(len(exp["sidecar_invalid"]), 4)

    def test_body_feeds_the_fake_llm(self):
        """標註 stub 認得內文的市場與代號標記；訊號 stub 認得評等與目標價那一句。"""
        import random

        for r in self.reports:
            if r.market == "MACRO":
                continue
            body = "\n\n".join(demo_corpus.body_paragraphs(r, random.Random(0)))
            self.assertRegex(body, r"「[^」]+」評等，目標價")
            self.assertIn(demo_corpus._code_tag(r), body)


class DemoComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.demo = yaml.safe_load(DEMO_COMPOSE.read_text(encoding="utf-8"))
        cls.real = yaml.safe_load(REAL_LLM_COMPOSE.read_text(encoding="utf-8"))
        cls.env = cls.demo["x-demo-env"]

    def test_every_llm_task_points_at_a_declared_fake_model(self):
        declared = set(self.env["OPENAI_COMPATIBLE_MODELS"].split(","))
        self.assertEqual(declared, set(fake_llm.declared_models().split(",")))
        self.assertEqual(self.env["LLM_PROVIDER"], "openai_compatible")
        for task, knob in TASK_ENV.items():
            if task == "ask_web":  # 網搜沒有後端，保持關
                continue
            with self.subTest(task=task):
                self.assertEqual(self.env.get(knob), fake_llm.MODELS[task])

    def test_connector_env_matches_the_profile(self):
        spec = load_profile(DEMO).connectors.enabled_specs()[0]
        for suffix in ("BUCKET", "ACCESS_KEY_ID", "SECRET_ACCESS_KEY", "ENDPOINT", "REGION"):
            self.assertIn(f"{spec.env}_{suffix}", self.env)

    def test_app_roles_get_the_demo_env(self):
        for role in ("web", "worker", "scheduler", "migrate"):
            self.assertEqual(self.demo["services"][role]["environment"], self.env, role)

    def test_s3_server_is_moto_not_minio(self):
        self.assertTrue(self.demo["services"]["s3"]["image"].startswith("motoserver/moto:"))
        for path in (DEMO_COMPOSE, REAL_LLM_COMPOSE, E2E_WORKFLOW):
            self.assertNotRegex(re.sub(r"#.*", "", path.read_text(encoding="utf-8")), r"(?i)minio")

    def test_real_llm_override_resets_every_llm_knob(self):
        real_env = self.real["x-real-llm-env"]
        llm_keys = {k for k in self.env if not k.startswith("CONNECTOR_")}
        self.assertEqual(llm_keys, set(real_env))
        for role in ("web", "worker", "scheduler", "migrate"):
            self.assertEqual(self.real["services"][role]["environment"], real_env, role)


class E2EComposeEnvTests(unittest.TestCase):
    def test_shell_values_never_shadow_the_project_env(self):
        """compose 代換時 shell 環境優先於 .env：呼叫端為了安全設的 REPORT_MARK_DB_URL 曾讓容器連到 db.invalid。"""
        from scripts import e2e_compose

        environ = {"PATH": "/bin", "REPORT_MARK_DB_URL": "postgresql+asyncpg://nobody:x@db.invalid:5432/none",
                   "POSTGRES_PASSWORD": "fixed-test-secret-shell", "DEMO_S3_BUCKET": "x", "COMPOSE_FILE": "a.yml"}
        env = e2e_compose.compose_env({"POSTGRES_PASSWORD": "fixed-test-secret-env"}, environ)
        self.assertEqual(env, {"PATH": "/bin"})


class E2EWorkflowTests(unittest.TestCase):
    """e2e workflow 是獨立檔、不是必要檢查：不能動 ci.yml 的 job 名稱，也不能撞名。"""

    @classmethod
    def setUpClass(cls):
        cls.e2e = yaml.safe_load(E2E_WORKFLOW.read_text(encoding="utf-8"))
        cls.ci = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))

    def test_job_names_do_not_collide_with_required_checks(self):
        required = {job["name"] for job in self.ci["jobs"].values()}
        self.assertFalse({job.get("name") for job in self.e2e["jobs"].values()} & required)

    def test_branches_follow_ci(self):
        on_e2e, on_ci = self.e2e[True], self.ci[True]  # yaml 1.1 把 on 讀成 True
        self.assertEqual(on_e2e["pull_request"]["branches"], on_ci["pull_request"]["branches"])
        self.assertIn("workflow_dispatch", on_e2e)

    def test_real_llm_job_is_dispatch_only_and_skips_without_secret(self):
        text = E2E_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("E2E_LLM_API_KEY", text)
        self.assertIn("workflow_dispatch", text)


if __name__ == "__main__":
    unittest.main()
