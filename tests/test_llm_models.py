"""app/services/llm_models.py：模型白名單、各任務預設表與 `resolve_model`。

釘住三件事：
1. `LLM_PROVIDER=claude_cli`（預設）下，每個任務解析出的名稱與遷移前各呼叫點寫死的字串**逐字
   相同**——包括各模組層常數，不只是表本身。
2. 三個 provider 的語意（`claude_only` 忽略白名單內的任務旋鈕、未知值退回 `claude_cli`）。
3. conftest 把所有模型旋鈕強制成空字串的防線還在，而且清單與 `TASK_ENV` 一致。
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

# 遷移前（2026-09-24，origin/main b3fb0e9）各呼叫點寫死的字串。改這張表＝換生產模型。
PRE_MIGRATION = {
    "ask_answer": "claude-sonnet-5",           # llm.py DEFAULT_MODEL
    "ask_web": "claude-sonnet-5",              # answer.py 時效網搜、主答開網搜（同 DEFAULT_MODEL）
    "ask_intent": "claude-haiku-4-5",          # config.py ASK_INTENT_MODEL 預設
    "ask_condense": "claude-haiku-4-5",        # 跟隨 intent
    "qa_planner": "claude-haiku-4-5",          # 跟隨 intent
    "ask_followup": "claude-haiku-4-5-20251001",  # followups.py
    "faithfulness": "claude-haiku-4-5",        # config.py FAITHFULNESS_MODEL 預設
    "eval_judge": "claude-haiku-4-5",          # eval/judge.py
    "tag": "claude-haiku-4-5",                 # tag_all_cli.py、sync_new_reports._tag_via_cli
    "summary": "claude-sonnet-5",
    "title": "claude-sonnet-5",
    "takeaway": "claude-sonnet-5",
    "signal": "claude-sonnet-5",
    "brief": "claude-sonnet-5",
}

EMPTY_KNOBS = {k: "" for k in lm.TASK_ENV.values()}


def _env(**extra: str) -> dict[str, str]:
    return {**EMPTY_KNOBS, "LLM_PROVIDER": "claude_cli", **extra}


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
    def test_tables_cover_every_task(self):
        self.assertEqual(set(lm.CLAUDE_DEFAULTS), set(lm.TASK_ENV))
        self.assertEqual(set(lm.DEEPSEEK_DEFAULTS), set(lm.TASK_ENV))
        self.assertEqual(set(PRE_MIGRATION), set(lm.TASK_ENV))
        self.assertEqual(len(lm.TASK_ENV), 14)

    def test_claude_table_equals_pre_migration_literals(self):
        self.assertEqual(lm.CLAUDE_DEFAULTS, PRE_MIGRATION)

    def test_deepseek_table_values_are_known(self):
        """DeepSeek 表裡每個名稱不是白名單就是 claude-*：打錯字在表裡就要紅。"""
        for task, model in lm.DEEPSEEK_DEFAULTS.items():
            self.assertTrue(lm.is_http_model(model) or lm.is_claude_model(model), (task, model))

    def test_deepseek_table_keeps_judges_and_web_on_claude(self):
        """judge 換了就是換量尺（等 PR-26/27 校準）；DeepSeek 網搜延後到 P9。"""
        for task in ("faithfulness", "eval_judge", "ask_web"):
            self.assertEqual(lm.DEEPSEEK_DEFAULTS[task], lm.CLAUDE_DEFAULTS[task], task)

    def test_deepseek_table_uses_flash_elsewhere(self):
        for task in set(lm.TASK_ENV) - {"faithfulness", "eval_judge", "ask_web"}:
            self.assertEqual(lm.DEEPSEEK_DEFAULTS[task], "deepseek-flash", task)

    def test_online_tasks_are_known(self):
        self.assertLessEqual(set(lm.ONLINE_TASKS), set(lm.TASK_ENV))
        self.assertNotIn("eval_judge", lm.ONLINE_TASKS)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        lm._LOGGED.clear()

    def test_default_provider_resolves_every_task_to_pre_migration(self):
        for task, expected in PRE_MIGRATION.items():
            self.assertEqual(lm.resolve_model(task, env=_env()), expected, task)
        # LLM_PROVIDER 完全沒設也一樣
        env = dict(EMPTY_KNOBS)
        self.assertEqual(lm.resolve_all(lm.TASK_ENV, env=env), PRE_MIGRATION)

    def test_task_knob_wins(self):
        env = _env(SUMMARY_MODEL="deepseek-flash")
        self.assertEqual(lm.resolve_model("summary", env=env), "deepseek-flash")
        self.assertEqual(lm.resolve_model("title", env=env), "claude-sonnet-5")

    def test_empty_or_blank_knob_counts_as_unset(self):
        for value in ("", "  "):
            self.assertEqual(lm.resolve_model("tag", env=_env(TAG_MODEL=value)), "claude-haiku-4-5")

    def test_knob_value_is_stripped(self):
        self.assertEqual(lm.resolve_model("tag", env=_env(TAG_MODEL=" deepseek-flash ")), "deepseek-flash")

    def test_override_replaces_env_lookup(self):
        env = _env(ASK_FOLLOWUP_MODEL="claude-sonnet-5")
        self.assertEqual(lm.resolve_model("ask_followup", override="deepseek-flash", env=env), "deepseek-flash")
        self.assertEqual(lm.resolve_model("ask_followup", override="", env=env), "claude-haiku-4-5-20251001")

    def test_deepseek_provider_uses_deepseek_table(self):
        env = _env(LLM_PROVIDER="deepseek")
        self.assertEqual(lm.resolve_all(lm.TASK_ENV, env=env), lm.DEEPSEEK_DEFAULTS)
        # 任務旋鈕仍然優先
        env["TITLE_MODEL"] = "claude-sonnet-5"
        self.assertEqual(lm.resolve_model("title", env=env), "claude-sonnet-5")

    def test_provider_is_case_and_space_insensitive(self):
        self.assertEqual(lm.provider(_env(LLM_PROVIDER=" DeepSeek ")), "deepseek")

    def test_claude_only_ignores_whitelisted_knobs(self):
        env = _env(LLM_PROVIDER="claude_only", SUMMARY_MODEL="deepseek-flash", TITLE_MODEL="claude-opus-5")
        with self.assertLogs("app.services.llm_models", "WARNING") as cm:
            self.assertEqual(lm.resolve_model("summary", env=env), "claude-sonnet-5")
        self.assertIn("SUMMARY_MODEL=deepseek-flash", "\n".join(cm.output))
        # Claude 名稱照用（那本來就走 CLI）
        self.assertEqual(lm.resolve_model("title", env=env), "claude-opus-5")
        # 沒設旋鈕的用 Claude 表
        self.assertEqual(lm.resolve_all(lm.TASK_ENV, env=_env(LLM_PROVIDER="claude_only")), PRE_MIGRATION)

    def test_claude_only_also_applies_to_override(self):
        env = _env(LLM_PROVIDER="claude_only")
        self.assertEqual(
            lm.resolve_model("ask_followup", override="deepseek-flash", env=env), "claude-haiku-4-5-20251001"
        )

    def test_claude_only_logs_warning_once(self):
        env = _env(LLM_PROVIDER="claude_only")
        with self.assertLogs("app.services.llm_models", "WARNING") as cm:
            lm.provider(env)
            lm.provider(env)
        self.assertEqual(len(cm.output), 1)
        self.assertIn("claude_only", cm.output[0])

    def test_unknown_provider_logs_error_and_falls_back_to_claude_cli(self):
        env = _env(LLM_PROVIDER="deepsek")
        with self.assertLogs("app.services.llm_models", "ERROR") as cm:
            self.assertEqual(lm.provider(env), "claude_cli")
        self.assertIn("deepsek", cm.output[0])
        self.assertEqual(lm.resolve_all(lm.TASK_ENV, env=env), PRE_MIGRATION)

    def test_unknown_task_raises(self):
        with self.assertRaises(KeyError):
            lm.resolve_model("summaries", env=_env())


class ModuleConstantsTests(unittest.TestCase):
    """各呼叫點實際讀到的常數（conftest 已把旋鈕清空、provider=claude_cli）。

    表對了不代表呼叫點接上了表：這裡逐一 import 真正的常數比對。
    """

    def test_online_constants(self):
        from app.config import get_settings
        from app.services import answer, followups, llm, scope_router
        from eval import judge

        s = get_settings()
        self.assertEqual(s.llm_provider, "claude_cli")
        self.assertEqual(llm.DEFAULT_MODEL, "claude-sonnet-5")
        self.assertEqual(s.ask_answer_model, "claude-sonnet-5")
        self.assertEqual(s.ask_web_model, "claude-sonnet-5")
        self.assertEqual(answer.ASK_WEB_MODEL, "claude-sonnet-5")
        self.assertEqual(scope_router.ROUTE_MODEL, "claude-haiku-4-5")
        self.assertEqual(scope_router.CONDENSE_MODEL, "claude-haiku-4-5")
        self.assertEqual(s.qa_planner_model, "claude-haiku-4-5")
        self.assertEqual(answer.FAITHFULNESS_MODEL, "claude-haiku-4-5")
        self.assertEqual(followups.FOLLOWUP_MODEL, "claude-haiku-4-5-20251001")
        self.assertEqual(judge.DEFAULT_JUDGE_MODEL, "claude-haiku-4-5")

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

        self.assertEqual(tag_all_cli.MODEL, "claude-haiku-4-5")
        self.assertEqual(sync_new_reports.TAG_MODEL, "claude-haiku-4-5")
        default = inspect.signature(sync_new_reports._tag_via_cli).parameters["model"].default
        self.assertEqual(default, "claude-haiku-4-5")
        self.assertEqual(generate_summaries.MODEL, "claude-sonnet-5")
        self.assertEqual(generate_titles.MODEL, "claude-sonnet-5")
        self.assertEqual(extract_takeaways.TAKEAWAY_MODEL_DEFAULT, "claude-sonnet-5")
        self.assertEqual(signal_extract.SIGNAL_MODEL_DEFAULT, "claude-sonnet-5")
        self.assertEqual(generate_brief.MODEL, "claude-sonnet-5")


class ConfigResolveTests(unittest.TestCase):
    def _load(self, **env: str):
        from app import config

        with mock.patch.dict(os.environ, _env(**env)):
            return config._load()

    def test_condense_and_planner_no_longer_follow_intent(self):
        """改寫前未設時跟著 ASK_INTENT_MODEL 走；現在各自查表，換路由模型不會靜默換掉另外兩個任務。"""
        s = self._load(ASK_INTENT_MODEL="claude-sonnet-5")
        self.assertEqual(s.ask_intent_model, "claude-sonnet-5")
        self.assertEqual(s.ask_condense_model, "claude-haiku-4-5")
        self.assertEqual(s.qa_planner_model, "claude-haiku-4-5")

    def test_settings_follow_provider(self):
        s = self._load(LLM_PROVIDER="deepseek")
        self.assertEqual(s.llm_provider, "deepseek")
        self.assertEqual(s.ask_answer_model, "deepseek-flash")
        self.assertEqual(s.ask_intent_model, "deepseek-flash")
        self.assertEqual(s.ask_web_model, "claude-sonnet-5")
        self.assertEqual(s.faithfulness_model, "claude-haiku-4-5")


class DiagnoseTests(unittest.TestCase):
    def _levels(self, findings):
        return [level for level, _ in findings]

    def test_all_claude_with_cli_present_is_quiet(self):
        out = lm.diagnose(PRE_MIGRATION, has_key=False, claude_path="/opt/bin/claude")
        self.assertEqual(self._levels(out), [logging.WARNING])
        self.assertIn("/opt/bin/claude", out[0][1])

    def test_missing_cli_is_error(self):
        out = lm.diagnose({"ask_answer": "claude-sonnet-5"}, has_key=True, claude_path=None, path_env="/usr/bin")
        self.assertEqual(self._levels(out), [logging.ERROR])
        self.assertIn("claude CLI 不在 PATH 上", out[0][1])
        self.assertIn("/usr/bin", out[0][1])

    def test_whitelisted_model_without_key_is_error(self):
        out = lm.diagnose({"ask_answer": "deepseek-flash"}, has_key=False, claude_path=None)
        self.assertEqual(self._levels(out), [logging.ERROR])
        self.assertIn("DEEPSEEK_API_KEY", out[0][1])
        self.assertIn("ask_answer=deepseek-flash", out[0][1])

    def test_whitelisted_model_with_key_needs_no_cli(self):
        self.assertEqual(lm.diagnose({"ask_answer": "deepseek-flash"}, has_key=True, claude_path=None), [])

    def test_web_task_on_deepseek_is_error(self):
        """DeepSeek 網搜延後到 P9：網搜任務解析到白名單名稱時，每一題開網搜的問答都會失敗。"""
        out = lm.diagnose({"ask_web": "deepseek-flash"}, has_key=True, claude_path=None)
        self.assertEqual(self._levels(out), [logging.ERROR])
        self.assertIn("ASK_WEB_MODEL=deepseek-flash", out[0][1])
        self.assertIn("網搜尚未支援", out[0][1])

    def test_unknown_names_are_error(self):
        for name in ("sonnet", "deepseek-flsh", "gpt-5"):
            out = lm.diagnose({"ask_intent": name}, has_key=True, claude_path="/x")
            self.assertEqual(self._levels(out), [logging.ERROR], name)
            self.assertIn("未知模型名", out[0][1])


class ConftestModelGuardTests(unittest.TestCase):
    """conftest 以**賦值**把全部模型旋鈕清空、provider 設 claude_cli、LLM_ENV_FILE 指到不存在的檔。

    與 `resolve_model` 的「空字串＝未設」同進同退（審查 L17）：只有前者，模組會拿到空字串的
    模型名；只有後者，部署目錄 `.env` 與執行者 shell 裡的旋鈕會滲進測試。
    """

    def test_runtime_values(self):
        for key in lm.TASK_ENV.values():
            self.assertEqual(os.environ.get(key), "", key)
        self.assertEqual(os.environ.get("LLM_PROVIDER"), "claude_cli")
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
