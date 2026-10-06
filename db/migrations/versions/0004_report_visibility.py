"""研報可見性：管理員隱藏／恢復研報（research.report_visibility）

Revision ID: 0004
Revises: 0002
Create Date: 2026-10-06
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0004"
# 平行開發時從 0002 長出；整合時接到前一個 revision 之後（鏈必須線性，tests/test_schema_migrations.py）。
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── 研報可見性 ──────────────────────────────────────────────────────────────
-- 以 file_hash 為鍵，**刻意不掛在 research_report 上、也不設 FK**：store.upsert_report 重新入庫
-- 是「先刪後插、換新 report_id」，旗標若放在 research_report（或以 report_id 為鍵）會在下一次
-- 重新入庫時靜默消失，被隱藏的研報就悄悄回到檢索與問答裡。
--
-- 一列＝這份研報目前的可見性。恢復時不刪列而是 hidden=false（reason 換成恢復原因或 NULL），
-- 完整的隱藏／恢復歷史在 admin_audit_log。所有面向使用者的讀取路徑以
-- app/services/visibility.py 產生的 NOT EXISTS 片段排除 hidden=true 的研報
-- （tests/test_visibility_guard.py 守門）；批次（摘要、標題、摘錄、訊號）照常處理，恢復即生效。
--
-- updated_by 不設 FK：與 user_scope.granted_by 同理（帳號只停用不刪除；備份還原時表的順序不受 FK 牽制）。
-- 不可重建：列入 scripts/db_backup.sh 的 BACKUP_TABLES。
CREATE TABLE research.report_visibility (
    file_hash  text PRIMARY KEY CHECK (file_hash ~ '^[0-9a-f]{64}$'),
    hidden     boolean NOT NULL,
    reason     text CHECK (char_length(reason) <= 500),
    updated_by uuid,
    updated_at timestamptz NOT NULL DEFAULT now(),
    -- 隱藏一定要說明原因（管理頁與稽核追查的依據）；恢復可不填。
    CONSTRAINT report_visibility_hidden_needs_reason CHECK (NOT hidden OR btrim(coalesce(reason, '')) <> '')
);
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
