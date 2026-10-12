"""依賴圖的判讀（`app/services/ops_topology.py`）：節點健康分類、外部依賴的探針判讀、down 的傳播與層級。

純函式：輸入是代理 `list` 的結果（含 `depends_on` 與 `externals`），不連 DB、不問代理。
"""

from __future__ import annotations

import unittest
from pathlib import Path

from app.services.ops_topology import build_topology, external_health, service_health

CHECKED_AT = "2026-10-06T12:00:00Z"


def _svc(name, summary="running", deps=(), kind="systemd", timer=None, type_="simple", tier="important", **systemd):
    item = {"name": name, "kind": kind, "tier": tier, "target": f"report-mark-{name}.service", "timer": timer,
            "actions": ["status"], "description": name, "summary": summary, "error": None,
            "depends_on": list(deps), "container": None, "timer_state": None,
            "systemd": {"type": type_, "active_state": "active", **systemd} if kind == "systemd" else None}
    return item


def _probe(name, code, exited="2026-10-06T11:58:00Z", summary="idle", exec_code="exited"):
    return _svc(name, summary=summary, timer=f"report-mark-{name}.timer", type_="oneshot",
                exec_main_code=exec_code, exec_main_status=code, exec_main_exit_at=exited, active_state="inactive")


def _ext(name, probe=None, down=(), degraded=(), ok=(0,), deps=(), tier="critical"):
    return {"name": name, "kind": "external", "tier": tier, "depends_on": list(deps), "probe": probe,
            "ok_exit_codes": list(ok), "degraded_exit_codes": list(degraded), "down_exit_codes": list(down),
            "description": name}


def _listing(items, externals=()):
    return {"environment": "production", "host": "h", "checked_at": CHECKED_AT, "items": list(items),
            "externals": list(externals)}


class ServiceHealthTests(unittest.TestCase):
    def test_resident_vs_scheduled(self):
        self.assertEqual(service_health(_svc("web", "running"))[0], "ok")
        self.assertEqual(service_health(_svc("web", "idle")), ("down", "常駐服務沒有在跑"))
        self.assertEqual(service_health(_svc("web", "transitioning"))[0], "degraded")
        self.assertEqual(service_health(_svc("web", "failed"))[0], "down")
        self.assertEqual(service_health(_svc("web", "not_found"))[0], "down")
        self.assertEqual(service_health({**_svc("web", "unknown"), "error": "systemctl show 逾時"}),
                         ("unknown", "systemctl show 逾時"))
        self.assertEqual(service_health(_svc("web", "unknown")), ("unknown", "狀態查不到"))
        sync = dict(timer="report-mark-sync.timer", type_="oneshot")
        self.assertEqual(service_health(_svc("sync", "idle", **sync)), ("ok", "排程待命"))
        self.assertEqual(service_health(_svc("sync", "transitioning", **sync)), ("ok", "執行中"))
        self.assertEqual(service_health(_svc("sync", "failed", **sync)), ("down", "最後一次執行失敗"))
        # 沒有 timer 但 Type=oneshot（例如 dev-smoke）：idle 也是待命
        self.assertEqual(service_health(_svc("smoke", "idle", type_="oneshot"))[0], "ok")
        self.assertEqual(service_health(_svc("pg", "idle", kind="container"))[0], "down")


class ExternalHealthTests(unittest.TestCase):
    from datetime import datetime, timezone

    NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    R2 = _ext("r2", probe="health", down=(6,))
    LLM = _ext("deepseek", probe="health", down=(8,), degraded=(7,))

    def test_exit_codes(self):
        self.assertEqual(external_health(self.R2, _probe("health", 0), self.NOW)[:2], ("ok", "探針 health 退出碼 0"))
        self.assertEqual(external_health(self.R2, _probe("health", 6), self.NOW)[0], "down")
        self.assertEqual(external_health(self.LLM, _probe("health", 7), self.NOW)[0], "degraded")
        self.assertEqual(external_health(self.LLM, _probe("health", 8), self.NOW)[0], "down")

    def test_masked_or_other_codes_are_unknown_not_ok(self):
        """web 探針 8 會蓋住 6：R2 此時判斷不出來，絕不能顯示正常。"""
        health, reason, _ = external_health(self.R2, _probe("health", 8), self.NOW)
        self.assertEqual(health, "unknown")
        self.assertIn("判斷不出", reason)
        self.assertEqual(external_health(self.R2, _probe("health", 1), self.NOW)[0], "unknown")

    def test_no_probe_missing_running_stale(self):
        self.assertEqual(external_health(_ext("nas"), None, self.NOW)[:2], ("unknown", "未監控（沒有探針）"))
        self.assertIn("不在服務清單", external_health(self.R2, None, self.NOW)[1])
        self.assertIn("未安裝", external_health(self.R2, _probe("health", 0, summary="not_found"), self.NOW)[1])
        self.assertIn("正在執行", external_health(self.R2, _probe("health", 0, summary="transitioning"), self.NOW)[1])
        self.assertIn("還沒有結果", external_health(self.R2, _probe("health", None), self.NOW)[1])
        self.assertIn("還沒有結果", external_health(self.R2, _probe("health", 6, exec_code="killed"), self.NOW)[1])
        stale = external_health(self.R2, _probe("health", 6, exited="2026-10-06T11:00:00Z"), self.NOW)
        self.assertEqual(stale[0], "unknown")
        self.assertIn("30 分鐘", stale[1])
        self.assertEqual(external_health(self.R2, _probe("health", 6, exited=None), self.NOW)[0], "unknown")


class BuildTopologyTests(unittest.TestCase):
    def _prod_like(self, postgres="running", web_probe=0):
        items = [
            _svc("web", deps=["postgres", "r2", "deepseek"], tier="critical"),
            _svc("postgres", postgres, kind="container", tier="critical"),
            _svc("nginx", deps=["web"], kind="container", tier="critical"),
            _svc("sync", "idle", deps=["postgres", "nas", "r2"], timer="report-mark-sync.timer", type_="oneshot"),
            _probe("health", web_probe),
            _svc("metrics"),
        ]
        externals = [_ext("r2", probe="health", down=(6,)), _ext("deepseek", probe="health", down=(8,)),
                     _ext("nas", tier="important"), _ext("public-edge", deps=["nginx"])]
        return build_topology(_listing(items, externals))

    def test_healthy_graph(self):
        g = self._prod_like()
        self.assertEqual(g["down"], [])
        self.assertEqual(g["affected"], [])
        self.assertEqual(g["root_causes"], [])
        nodes = {n["name"]: n for n in g["nodes"]}
        self.assertEqual(nodes["postgres"]["layer"], 0)
        self.assertEqual(nodes["web"]["layer"], 1)
        self.assertEqual(nodes["nginx"]["layer"], 2)
        self.assertEqual(nodes["public-edge"]["layer"], 3)
        self.assertEqual(nodes["postgres"]["dependents"], ["sync", "web"])
        self.assertEqual(nodes["nas"]["health"], "unknown")
        self.assertEqual(nodes["r2"]["health"], "ok")
        self.assertEqual(nodes["r2"]["kind"], "external")
        self.assertIsNone(nodes["r2"]["summary"])
        self.assertEqual(nodes["r2"]["observed_at"], "2026-10-06T11:58:00Z")
        self.assertIn({"dependent": "web", "dependency": "postgres", "broken": False}, g["edges"])
        self.assertEqual(len(g["edges"]), 3 + 1 + 3 + 1)
        # 依層級、tier、名稱排序
        self.assertEqual([n["name"] for n in g["nodes"]][:3], ["deepseek", "postgres", "r2"])

    def test_upstream_failure_propagates_transitively(self):
        g = self._prod_like(postgres="failed")
        self.assertEqual(g["down"], ["postgres"])
        self.assertEqual(g["root_causes"], ["postgres"])
        self.assertEqual(g["affected"], ["nginx", "public-edge", "sync", "web"])
        nodes = {n["name"]: n for n in g["nodes"]}
        self.assertEqual(nodes["public-edge"]["impacted_by"], ["postgres"])
        self.assertFalse(nodes["postgres"]["affected"])
        self.assertFalse(nodes["metrics"]["affected"])
        broken = {(e["dependent"], e["dependency"]) for e in g["edges"] if e["broken"]}
        self.assertEqual(broken, {("web", "postgres"), ("sync", "postgres"), ("nginx", "web"),
                                  ("public-edge", "nginx")})

    def test_root_causes_exclude_nodes_whose_upstream_is_down(self):
        items = [_svc("web", "failed", deps=["postgres"]), _svc("postgres", "failed", kind="container"),
                 _svc("nginx", "failed", deps=["web"], kind="container")]
        g = build_topology(_listing(items))
        self.assertEqual(g["down"], ["nginx", "postgres", "web"])
        self.assertEqual(g["root_causes"], ["postgres"])
        nodes = {n["name"]: n for n in g["nodes"]}
        self.assertEqual(nodes["nginx"]["impacted_by"], ["postgres", "web"])

    def test_external_down_via_probe_propagates(self):
        g = self._prod_like(web_probe=6)
        self.assertEqual(g["down"], ["r2"])
        self.assertEqual(g["affected"], ["nginx", "public-edge", "sync", "web"])
        nodes = {n["name"]: n for n in g["nodes"]}
        self.assertEqual(nodes["deepseek"]["health"], "unknown")  # 6 可能蓋住 7（餘額低）：不當成正常
        g8 = self._prod_like(web_probe=8)
        nodes8 = {n["name"]: n for n in g8["nodes"]}
        self.assertEqual((nodes8["deepseek"]["health"], nodes8["r2"]["health"]), ("down", "unknown"))

    def test_degraded_and_unknown_do_not_propagate(self):
        items = [_svc("web", deps=["pg"]), _svc("pg", "transitioning", kind="container")]
        g = build_topology(_listing(items, [_ext("nas")]))
        self.assertEqual(g["affected"], [])

    def test_defensive_against_bad_input(self):
        """舊代理（沒有 depends_on／externals）、指向不存在的節點、自我依賴、環：不拋例外、不無限迴圈。"""
        old = build_topology({"environment": "production", "host": "h", "checked_at": CHECKED_AT,
                              "items": [{k: v for k, v in _svc("web").items() if k != "depends_on"}]})
        self.assertEqual((old["edges"], old["nodes"][0]["layer"]), ([], 0))
        items = [_svc("a", "failed", deps=["b", "ghost", "a", "b"]), _svc("b", deps=["a"])]
        g = build_topology(_listing(items, [_ext("a")]))
        nodes = {n["name"]: n for n in g["nodes"]}
        self.assertEqual(nodes["a"]["depends_on"], ["b"])
        self.assertEqual(nodes["a"]["kind"], "systemd")  # 同名的外部依賴被忽略
        self.assertEqual(nodes["b"]["impacted_by"], ["a"])
        self.assertEqual(nodes["a"]["impacted_by"], [])
        self.assertEqual(g["root_causes"], ["a"])  # 上游（b）沒有 down

    def test_repo_catalogs_build_without_errors(self):
        """真的三份 catalog 走一遍（狀態全部假裝正常）：每個節點都在、邊數與 catalog 一致。"""
        from ops_agent.catalog import load_catalog

        root = Path(__file__).resolve().parents[1] / "deploy" / "ops"
        for toml in ("services.prod.toml", "services.staging.toml", "services.dev.toml"):
            with self.subTest(toml=toml):
                cat = load_catalog(root / toml, resolve_user=lambda _n: 4242)
                items = [{**s.public(), "summary": "running", "error": None, "systemd": None, "container": None,
                          "timer_state": None} for s in cat.services]
                g = build_topology(_listing(items, [e.public() for e in cat.externals]))
                self.assertEqual(len(g["nodes"]), len(cat.services) + len(cat.externals))
                self.assertEqual(len(g["edges"]), sum(len(n.depends_on) for n in (*cat.services, *cat.externals)))
                self.assertEqual(g["down"], [])


if __name__ == "__main__":
    unittest.main()
