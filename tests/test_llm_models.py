"""app/services/llm_models.py：模型白名單、預設表與 `resolve_model`。

釘住四件事：
1. 預設表每一列都是白名單名稱（網搜那列刻意是空字串＝沒有後端），模組層常數真的接上了表。
2. `LLM_PROVIDER` 只剩 `deepseek`：沒設、空字串、大小寫都解析成它；PR-M 退役的 `claude_cli`／`claude_only`
   與拼錯的值記 ERROR 並當成 deepseek（線上容錯；批次拒跑在 tests/test_llm_env_loading.py）。
3. 啟動自檢（`diagnose`）：白名單外的名稱（含 claude-*）、缺金鑰、網搜總閘開著都要說出來。
4. conftest 把所有模型旋鈕強制成空字串、provider 強制成 deepseek 的防線還在，而且清單與 `TASK_ENV` 一致。
"""
from __future__ import annotations

import ast
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm_models as lm  # noqa: E402

EMPTY_KNOBS = {k: "" for k in lm.TASK_ENV.values()}


def _env(**extra: str) -> dict[str, str]:
    return {**EMPTY_KNOBS, "LLM_PROVIDER": "deepseek", **extra}


# 預設表的期望值：網搜以外全是 deepseek-flash（第二版計畫 §8、D6、PR-26/27）。改這張表＝換生產模型。
EXPECTED_DEFAULTS = {task: "deepseek-flash" for task in lm.TASK_ENV}
EXPECTED_DEFAULTS["ask_web"] = ""


class LeafModuleTests(unittest.TestCase):
    def test_imports_only_stdlib(self):
        """config 與 llm_http 都依賴它；它往外 import 任何東西都可能在 import 期形成循環（審查 L2）。"""
        tree = ast.parse((REPO_ROOT / "app" / "services" / "llm_models.py").read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                roots.add("app" if node.level > 0 else (node.module or "").split(".")[0])
        self.assertEqual(roots - set(sys.stdlib_module_names) - {"__future__"}, set())

    def test_llm_http_reexports_the_same_objects(self):
        from app.services import llm_http

        self.assertIs(llm_http.HTTP_MODELS, lm.HTTP_MODELS)
        self.assertIs(llm_http.is_http_model, lm.is_http_model)

    def test_config_imports_llm_models_not_llm_http(self):
        tree = ast.parse((REPO_ROOT / "app" / "config.py").read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        project = {m for m in mods if m and m.split(".")[0] in {"app", "web", "scripts", "eval"}}
        self.assertEqual(project, {"app.services.llm_models"})


class CliAuthErrorPatternTests(unittest.TestCase):
    """`looks_like_cli_auth_error`：批次（整批中止）與線上（kind=auth）共用的 CLI 認證失效樣式。"""

    def test_positives(self):
        for text in (
            "Failed to authenticate: OAuth session expired and could not be refreshed",
            "failed to authenticate. API Error: 401",
            "OAuth token has expired. Please obtain a new token",
            "OAuth session was revoked",
            "Invalid API key · Please run /login",
            "Not logged in · Please run /login",
            'API Error: 401 {"type":"error","error":{"type":"authentication_error"}}',
        ):
            with self.subTest(text=text):
                self.assertTrue(lm.looks_like_cli_auth_error(text))

    def test_negatives(self):
        for text in (None, "", "API Error: 529 Overloaded", "HTTP 401", "Unauthorized", "Credit balance is too low",
                     "usage: unknown flag", "OAuth 是一種授權協定"):
            with self.subTest(text=text):
                self.assertFalse(lm.looks_like_cli_auth_error(text))

    def test_only_scans_the_head(self):
        """長回答後段談到 API 金鑰不算：認證錯誤訊息都很短、在最前面。"""
        self.assertFalse(lm.looks_like_cli_auth_error("x" * 1000 + "Invalid API key"))
        self.assertTrue(lm.looks_like_cli_auth_error("x" * 100 + "Invalid API key"))


class TableTests(unittest.TestCase):
    def test_table_covers_every_task(self):
        self.assertEqual(set(lm.DEEPSEEK_DEFAULTS), set(lm.TASK_ENV))
        self.assertEqual(len(lm.TASK_ENV), 14)

    def test_table_values(self):
        self.assertEqual(lm.DEEPSEEK_DEFAULTS, EXPECTED_DEFAULTS)

    def test_every_non_web_default_is_whitelisted(self):
        """打錯字在表裡就要紅：PR-M 起白名單外的名稱沒有 backend，會讓整個任務 config 失敗。"""
        for task, model in lm.DEEPSEEK_DEFAULTS.items():
            if task == "ask_web":
                continue
            with self.subTest(task=task):
                self.assertTrue(lm.is_http_model(model), model)

    def test_web_has_no_model(self):
        """網搜沒有後端（claude CLI 已移除、DeepSeek 網搜延後到 P9）：預設是空字串，不是 Claude 名稱。"""
        self.assertEqual(lm.DEEPSEEK_DEFAULTS["ask_web"], "")
        self.assertFalse(lm.is_http_model(lm.DEEPSEEK_DEFAULTS["ask_web"]))

    def test_claude_table_and_symbols_are_gone(self):
        for name in ("CLAUDE_DEFAULTS", "PROVIDER_CLAUDE_CLI", "PROVIDER_CLAUDE_ONLY", "is_claude_model"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(lm, name), name)

    def test_online_tasks_are_known(self):
        self.assertLessEqual(set(lm.ONLINE_TASKS), set(lm.TASK_ENV))
        self.assertNotIn("eval_judge", lm.ONLINE_TASKS)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        lm._LOGGED.clear()

    def test_every_task_resolves_to_the_table(self):
        for task, expected in EXPECTED_DEFAULTS.items():
            self.assertEqual(lm.resolve_model(task, env=_env()), expected, task)

    def test_default_provider_is_the_only_provider(self):
        self.assertEqual(lm.DEFAULT_PROVIDER, "deepseek")
        self.assertEqual(lm.PROVIDERS, ("deepseek",))
        for env in (dict(EMPTY_KNOBS), {**EMPTY_KNOBS, "LLM_PROVIDER": ""}, {**EMPTY_KNOBS, "LLM_PROVIDER": "  "}):
            with self.subTest(env=env.get("LLM_PROVIDER")):
                self.assertEqual(lm.provider(env), "deepseek")
                self.assertEqual(lm.resolve_all(lm.TASK_ENV, env=env), EXPECTED_DEFAULTS)

    def test_unset_provider_in_process_env_resolves_to_deepseek(self):
        """走真正的 os.environ（呼叫點不傳 env）：暫時移除 conftest 強制的 LLM_PROVIDER。"""
        with mock.patch.dict(os.environ, EMPTY_KNOBS):
            os.environ.pop("LLM_PROVIDER", None)
            self.assertEqual(lm.provider(), "deepseek")
            self.assertEqual(lm.resolve_model("summary"), "deepseek-flash")
            self.assertEqual(lm.resolve_model("faithfulness"), "deepseek-flash")
            self.assertEqual(lm.resolve_model("ask_web"), "")
        self.assertEqual(os.environ.get("LLM_PROVIDER"), "deepseek", "patch.dict 結束後還原")

    def test_task_knob_wins(self):
        """任務旋鈕原樣採用（就算不在白名單：解析層不擋，交給呼叫層以 config 失敗、自檢與批次預檢說出來）。"""
        env = _env(SUMMARY_MODEL="deepseek-v4-pro", TITLE_MODEL="claude-sonnet-5")
        self.assertEqual(lm.resolve_model("summary", env=env), "deepseek-v4-pro")
        self.assertEqual(lm.resolve_model("title", env=env), "claude-sonnet-5")
        self.assertEqual(lm.resolve_model("tag", env=env), "deepseek-flash")

    def test_empty_or_blank_knob_counts_as_unset(self):
        for value in ("", "  "):
            self.assertEqual(lm.resolve_model("tag", env=_env(TAG_MODEL=value)), "deepseek-flash")

    def test_knob_value_is_stripped(self):
        self.assertEqual(lm.resolve_model("tag", env=_env(TAG_MODEL=" deepseek-v4-pro ")), "deepseek-v4-pro")

    def test_override_replaces_env_lookup(self):
        env = _env(ASK_FOLLOWUP_MODEL="deepseek-v4-pro")
        self.assertEqual(lm.resolve_model("ask_followup", override="deepseek-v4-flash", env=env), "deepseek-v4-flash")
        self.assertEqual(lm.resolve_model("ask_followup", override="", env=env), "deepseek-flash")

    def test_provider_is_case_and_space_insensitive(self):
        self.assertEqual(lm.provider(_env(LLM_PROVIDER=" DeepSeek ")), "deepseek")

    def test_retired_providers_log_error_and_resolve_to_deepseek(self):
        """PR-M 退役的值：web 記 ERROR（說得出「已退役」）、照常以 deepseek 解析——**不是** Claude 表。"""
        for value in ("claude_cli", "claude_only", " Claude_Only "):
            with self.subTest(value=value):
                lm._LOGGED.clear()
                env = _env(LLM_PROVIDER=value, SUMMARY_MODEL="deepseek-v4-pro")
                with self.assertLogs("app.services.llm_models", "ERROR") as cm:
                    self.assertEqual(lm.provider(env), "deepseek")
                self.assertIn("已隨 claude CLI 退役", cm.output[0])
                self.assertEqual(lm.resolve_model("summary", env=env), "deepseek-v4-pro")  # 旋鈕不再被忽略
                self.assertEqual(lm.resolve_model("title", env=env), "deepseek-flash")
        self.assertEqual(lm.RETIRED_PROVIDERS, frozenset({"claude_cli", "claude_only"}))

    def test_resolve_model_reports_retired_provider(self):
        """各模組在 import 期經 resolve_model 解析：退役值的 ERROR 要在那裡就說出來，不只在自檢。"""
        with self.assertLogs("app.services.llm_models", "ERROR") as cm:
            lm.resolve_model("tag", env=_env(LLM_PROVIDER="claude_only"))
        self.assertIn("claude_only", cm.output[0])

    def test_provider_error_is_logged_once(self):
        env = _env(LLM_PROVIDER="claude_only")
        with self.assertLogs("app.services.llm_models", "ERROR") as cm:
            lm.provider(env)
            lm.provider(env)
        self.assertEqual(len(cm.output), 1)

    def test_unknown_provider_logs_error_and_falls_back_to_default(self):
        env = _env(LLM_PROVIDER="deepsek")
        with self.assertLogs("app.services.llm_models", "ERROR") as cm:
            self.assertEqual(lm.provider(env), "deepseek")
        self.assertIn("deepsek", cm.output[0])
        self.assertNotIn("退役", cm.output[0])
        self.assertEqual(lm.resolve_all(lm.TASK_ENV, env=env), EXPECTED_DEFAULTS)

    def test_unknown_task_raises(self):
        with self.assertRaises(KeyError):
            lm.resolve_model("summaries", env=_env())


class ModuleConstantsTests(unittest.TestCase):
    """各呼叫點實際讀到的常數（conftest 已把旋鈕清空、provider=deepseek）。

    表對了不代表呼叫點接上了表：這裡逐一 import 真正的常數比對。
    """

    def test_online_constants(self):
        from app.config import get_settings
        from app.services import answer, followups, llm, scope_router
        from eval import judge

        s = get_settings()
        self.assertEqual(s.llm_provider, "deepseek")
        self.assertEqual(llm.DEFAULT_MODEL, "deepseek-flash")
        self.assertEqual(s.ask_answer_model, "deepseek-flash")
        self.assertEqual(s.ask_web_model, "")
        self.assertEqual(answer.ASK_WEB_MODEL, "")
        self.assertEqual(scope_router.ROUTE_MODEL, "deepseek-flash")
        self.assertEqual(scope_router.CONDENSE_MODEL, "deepseek-flash")
        self.assertEqual(s.qa_planner_model, "deepseek-flash")
        self.assertEqual(answer.FAITHFULNESS_MODEL, "deepseek-flash")
        self.assertEqual(followups.FOLLOWUP_MODEL, "deepseek-flash")
        self.assertEqual(judge.DEFAULT_JUDGE_MODEL, "deepseek-flash")

    def test_batch_constants(self):
        import inspect

        from app.services import signal_extract
        from scripts import (
            extract_takeaways,
            generate_brief,
            generate_summaries,
            generate_titles,
            sync_new_reports,
            tag_all_cli,
        )

        self.assertEqual(tag_all_cli.MODEL, "deepseek-flash")
        self.assertEqual(sync_new_reports.TAG_MODEL, "deepseek-flash")
        default = inspect.signature(sync_new_reports._tag_via_cli).parameters["model"].default
        self.assertEqual(default, "deepseek-flash")
        self.assertEqual(generate_summaries.MODEL, "deepseek-flash")
        self.assertEqual(generate_titles.MODEL, "deepseek-flash")
        self.assertEqual(extract_takeaways.TAKEAWAY_MODEL_DEFAULT, "deepseek-flash")
        self.assertEqual(signal_extract.SIGNAL_MODEL_DEFAULT, "deepseek-flash")
        self.assertEqual(generate_brief.MODEL, "deepseek-flash")


class ConfigResolveTests(unittest.TestCase):
    def _load(self, **env: str):
        from app import config

        with mock.patch.dict(os.environ, _env(**env)):
            return config._load()

    def test_condense_and_planner_no_longer_follow_intent(self):
        """改寫前未設時跟著 ASK_INTENT_MODEL 走；現在各自查表，換路由模型不會靜默換掉另外兩個任務。"""
        s = self._load(ASK_INTENT_MODEL="deepseek-v4-pro")
        self.assertEqual(s.ask_intent_model, "deepseek-v4-pro")
        self.assertEqual(s.ask_condense_model, "deepseek-flash")
        self.assertEqual(s.qa_planner_model, "deepseek-flash")

    def test_settings_follow_the_table(self):
        s = self._load()
        self.assertEqual(s.llm_provider, "deepseek")
        self.assertEqual(s.ask_answer_model, "deepseek-flash")
        self.assertEqual(s.ask_intent_model, "deepseek-flash")
        self.assertEqual(s.ask_web_model, "")
        self.assertEqual(s.faithfulness_model, "deepseek-flash")


class DiagnoseTests(unittest.TestCase):
    def _levels(self, findings):
        return [level for level, _ in findings]

    def test_default_table_with_key_is_quiet(self):
        resolved = {t: lm.DEEPSEEK_DEFAULTS[t] for t in lm.ONLINE_TASKS}
        self.assertEqual(lm.diagnose(resolved, has_key=True), [])

    def test_default_table_without_key(self):
        """沒金鑰（web 啟動缺 DEEPSEEK_API_KEY）：一則 ERROR 列出任務；網搜（空字串）不列、也不算白名單外。"""
        resolved = {t: lm.DEEPSEEK_DEFAULTS[t] for t in lm.ONLINE_TASKS}
        out = lm.diagnose(resolved, has_key=False)
        self.assertEqual(self._levels(out), [logging.ERROR])
        self.assertIn("DEEPSEEK_API_KEY 為空", out[0][1])
        self.assertIn("ask_answer=deepseek-flash", out[0][1])
        self.assertIn("faithfulness=deepseek-flash", out[0][1])
        self.assertNotIn("ask_web", out[0][1])

    def test_non_whitelisted_names_are_error(self):
        """PR-M：claude-* 與打錯字、CLI 別名同一類——不在白名單＝每次呼叫都 config 失敗。"""
        for name in ("claude-sonnet-5", "claude-haiku-4-5", "sonnet", "deepseek-flsh", "gpt-5", ""):
            with self.subTest(name=name):
                out = lm.diagnose({"ask_intent": name}, has_key=True)
                self.assertEqual(self._levels(out), [logging.ERROR], name)
                self.assertIn("不在 DeepSeek 白名單", out[0][1])
                self.assertIn(f"ask_intent={name}", out[0][1])

    def test_web_task_is_never_classified(self):
        """網搜任務解析到什麼都一樣（沒有後端）：不因它記「白名單外」或「缺金鑰」。"""
        for name in ("", "deepseek-flash", "claude-sonnet-5"):
            with self.subTest(name=name):
                self.assertEqual(lm.diagnose({"ask_web": name}, has_key=False), [])

    def test_web_gate_on_is_error(self):
        out = lm.diagnose({"ask_answer": "deepseek-flash"}, has_key=True, web_enabled=True)
        self.assertEqual(self._levels(out), [logging.ERROR])
        self.assertIn("ASK_ENABLE_WEB", out[0][1])
        self.assertIn("網搜沒有後端", out[0][1])

    def test_whitelisted_model_without_key_is_error(self):
        out = lm.diagnose({"ask_answer": "deepseek-flash"}, has_key=False)
        self.assertEqual(self._levels(out), [logging.ERROR])
        self.assertIn("DEEPSEEK_API_KEY", out[0][1])
        self.assertIn("ask_answer=deepseek-flash", out[0][1])

    def test_no_claude_path_parameter(self):
        """PR-M：自檢不再查 claude 在不在 PATH（`claude_path` 參數隨 CLI 移除）。"""
        import inspect

        self.assertNotIn("claude_path", inspect.signature(lm.diagnose).parameters)


class ConftestModelGuardTests(unittest.TestCase):
    """conftest 以**賦值**把全部模型旋鈕清空、provider 設 deepseek（擋住部署目錄 `.env` 或 shell 裡的退役值，
    理由見 conftest）、LLM_ENV_FILE 指到不存在的檔。

    與 `resolve_model` 的「空字串＝未設」同進同退（審查 L17）：只有前者，模組會拿到空字串的
    模型名；只有後者，部署目錄 `.env` 與執行者 shell 裡的旋鈕會滲進測試。
    """

    def test_runtime_values(self):
        for key in lm.TASK_ENV.values():
            self.assertEqual(os.environ.get(key), "", key)
        self.assertEqual(os.environ.get("LLM_PROVIDER"), "deepseek")
        self.assertEqual(os.environ.get("LLM_ENV_FILE"), "/nonexistent/report-mark-llm")
        self.assertFalse(Path(os.environ["LLM_ENV_FILE"]).exists())

    def test_conftest_assigns_every_knob(self):
        """靜態守門：新增任務旋鈕卻漏改 conftest，或把賦值改回 setdefault，都在這裡紅。"""
        tree = ast.parse((REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))
        assigned: set[str] = set()
        loop_keys: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "environ"
                    ):
                        if isinstance(target.slice, ast.Constant):
                            assigned.add(target.slice.value)
            elif isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
                body_assigns_environ = any(
                    isinstance(b, ast.Assign)
                    and isinstance(b.targets[0], ast.Subscript)
                    and isinstance(b.targets[0].value, ast.Attribute)
                    and b.targets[0].value.attr == "environ"
                    and isinstance(b.value, ast.Constant)
                    and b.value.value == ""
                    for b in node.body
                )
                if body_assigns_environ:
                    loop_keys |= {e.value for e in node.iter.elts if isinstance(e, ast.Constant)}
        self.assertLessEqual({"LLM_PROVIDER", "LLM_ENV_FILE"}, assigned)
        self.assertEqual(loop_keys, set(lm.TASK_ENV.values()))


if __name__ == "__main__":
    unittest.main()
