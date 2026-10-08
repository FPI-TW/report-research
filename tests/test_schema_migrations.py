# tests/test_schema_migrations.py
"""Alembic migration 的靜態契約（不連 DB）。

守的是「導入 migration 工具之後最容易無聲壞掉」的幾件事：

1. **baseline 被改**。`db/schema.sql` 是 revision 0001，已 stamp 的庫不會重跑它——改了 baseline
   等於讓空庫與既有庫長得不一樣，而 alembic 完全不會察覺。以 SHA-256 釘住。
2. **revision 鏈分岔**。平行 worktree 各自從同一個 head 長出 revision，合併後就是兩個 head，
   `alembic upgrade head` 直接失敗。合併前就要紅。
3. **revision 不寫 SQL 常數**。`schema_source_text()` 靠 `UPGRADE_SQL` 把 baseline＋各 revision
   串成一份原文，既有的靜態契約測試（索引、生成欄運算式）掃的是它。
4. **守門規則**：對已有資料的庫做變更必須逐字確認目標（本機預設庫是測試環境的真實資料庫）。
"""
import hashlib
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import schema_migrations as sm  # noqa: E402


class BaselineFrozenTests(unittest.TestCase):
    def test_baseline_sha_is_pinned(self):
        digest = hashlib.sha256(sm.BASELINE_PATH.read_bytes()).hexdigest()
        self.assertEqual(
            digest, sm.BASELINE_SHA256,
            "db/schema.sql 是凍結的 baseline（revision 0001）。schema 變更請寫新 revision："
            "uv run alembic revision --rev-id 00NN -m '...'，不要改 baseline 或這個雜湊。",
        )

    def test_baseline_sql_refuses_tampered_file(self):
        orig = sm.BASELINE_SHA256
        try:
            sm.BASELINE_SHA256 = "0" * 64
            with self.assertRaises(sm.MigrationGuardError):
                sm.baseline_sql()
        finally:
            sm.BASELINE_SHA256 = orig

    def test_baseline_header_says_frozen(self):
        head = sm.BASELINE_PATH.read_text(encoding="utf-8").splitlines()[:6]
        self.assertTrue(any("已凍結" in line for line in head), "db/schema.sql 檔頭必須寫明它是凍結的 baseline")


class RevisionChainTests(unittest.TestCase):
    def setUp(self):
        self.script = sm._script_directory()

    def test_single_head(self):
        heads = self.script.get_heads()
        self.assertEqual(len(heads), 1, f"revision 鏈分岔成 {heads}：整合時把 down_revision 改成線性")

    def test_chain_starts_at_baseline(self):
        chain = sm.revision_chain()
        self.assertEqual(chain[0].revision, sm.BASELINE_REVISION)
        self.assertIsNone(chain[0].down_revision)

    def test_revision_ids_are_four_digits_and_match_filenames(self):
        for rev in sm.revision_chain():
            self.assertRegex(rev.revision, r"^\d{4}$", f"{rev.path}：revision id 一律四位數（--rev-id）")
            self.assertTrue(Path(rev.path).name.startswith(rev.revision + "_"), rev.path)

    def test_non_baseline_revisions_define_upgrade_sql(self):
        for rev in sm.revision_chain()[1:]:
            sql = getattr(rev.module, "UPGRADE_SQL", None)
            self.assertIsInstance(sql, str, f"{rev.path} 必須定義字串常數 UPGRADE_SQL")
            self.assertTrue(sql.strip(), f"{rev.path} 的 UPGRADE_SQL 是空的")

    def test_schema_source_text_contains_baseline(self):
        text = sm.schema_source_text()
        self.assertIn("CREATE TABLE IF NOT EXISTS research.research_report", text)

    def test_baseline_revision_guards_non_empty_db(self):
        src = (REPO_ROOT / "db" / "migrations" / "versions" / "0001_baseline.py").read_text(encoding="utf-8")
        self.assertLess(src.index("assert_research_empty"), src.index("run_sql_script"),
                        "0001 必須先確認空庫才套 baseline（既有庫要走 drift 驗證＋stamp）")


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _FakeBind:
    def __init__(self, has_objects):
        self.has_objects = has_objects

    def exec_driver_sql(self, sql):
        return _FakeResult(self.has_objects)


class AssertResearchEmptyTests(unittest.TestCase):
    def test_refuses_when_objects_exist(self):
        with self.assertRaises(sm.MigrationGuardError) as cm:
            sm.assert_research_empty(_FakeBind(True))
        self.assertIn("schema-stamp-baseline", str(cm.exception))

    def test_passes_on_empty(self):
        sm.assert_research_empty(_FakeBind(False))


class TargetIdentityTests(unittest.TestCase):
    def test_strips_credentials_and_query(self):
        self.assertEqual(
            sm.target_identity("postgresql+asyncpg://postgres:secret@LocalHost:5436/research?ssl=verify-full"),
            "localhost:5436/research",
        )

    def test_default_port(self):
        self.assertEqual(sm.target_identity("postgresql+asyncpg://u:p@db.example/research"), "db.example:5432/research")

    def test_identity_never_contains_password(self):
        self.assertNotIn("secret", sm.target_identity("postgresql+asyncpg://u:secret@h:1/d"))

    def test_protected_includes_local_production_and_env(self):
        targets = sm.protected_targets({sm.PROTECTED_ENV: " RDS.example:5432/research , "})
        self.assertIn("localhost:5436/research", targets)
        self.assertIn("rds.example:5432/research", targets)


class GuardProblemTests(unittest.TestCase):
    ID = "localhost:5436/research"

    def test_empty_db_needs_no_confirm(self):
        self.assertIsNone(sm.guard_problem(identity=self.ID, has_state=False, mutating=True, confirm=None))

    def test_read_only_needs_no_confirm(self):
        self.assertIsNone(sm.guard_problem(identity=self.ID, has_state=True, mutating=False, confirm=None))

    def test_existing_db_requires_exact_confirm(self):
        msg = sm.guard_problem(identity=self.ID, has_state=True, mutating=True, confirm=None)
        self.assertIn(f"{sm.CONFIRM_ENV}={self.ID}", msg)
        self.assertIsNotNone(sm.guard_problem(identity=self.ID, has_state=True, mutating=True,
                                              confirm="localhost:5437/research"))
        self.assertIsNone(sm.guard_problem(identity=self.ID, has_state=True, mutating=True, confirm=f" {self.ID} "))


class EnvAndToolingWiringTests(unittest.TestCase):
    def setUp(self):
        self.env = (REPO_ROOT / "db" / "migrations" / "env.py").read_text(encoding="utf-8")

    def test_env_loads_dotenv_before_db_module(self):
        self.assertLess(self.env.index("load_env_file(REPO_ROOT"), self.env.index("from app.services.db import"))

    def test_env_uses_shared_guard_and_settings(self):
        self.assertIn("sm.guard_problem(", self.env)
        self.assertIn("sm.MIGRATION_SERVER_SETTINGS", self.env)
        self.assertIn("command is None or command in sm.MUTATING_COMMANDS", self.env)

    def test_migration_settings_disable_statement_timeout_but_keep_lock_timeout(self):
        self.assertEqual(sm.MIGRATION_SERVER_SETTINGS["statement_timeout"], "0")
        self.assertNotEqual(sm.MIGRATION_SERVER_SETTINGS.get("lock_timeout", "0"), "0")

    def test_alembic_ini_has_no_connection_string(self):
        ini = (REPO_ROOT / "alembic.ini").read_text(encoding="utf-8")
        self.assertNotRegex(ini, r"(?m)^sqlalchemy\.url\s*=", "連線字串只來自 REPORT_MARK_DB_URL")

    def test_make_schema_uses_alembic_not_raw_psql(self):
        mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        block = re.search(r"(?ms)^schema: .*?(?=^\S)", mk).group(0)
        self.assertIn("alembic upgrade head", block)
        self.assertNotIn("< db/schema.sql", block)


if __name__ == "__main__":
    unittest.main()
