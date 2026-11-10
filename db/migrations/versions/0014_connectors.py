"""資料來源連接器：metadata 側檔（research.report_metadata）、對外上傳 scope 與上傳來源的 API 用戶端

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-10
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── metadata 側檔 ───────────────────────────────────────────────────────────
-- 一列＝一份研報入庫時採用的 metadata 側檔（app/services/connectors/sidecar.py 驗證過的欄位，只含提供方
-- 有給的鍵）。標題、報告日期、發行機構、市場、標的同時寫進 research_report 的既有欄位；評等、目標價、分析師
-- 沒有對應欄位，只保存在這裡（report_signal 刻意不由側檔覆寫）。
-- 以 file_hash 為鍵、刻意不設 FK（同 report_visibility）：store.upsert_report 先刪後插、換新 report_id，
-- 掛在 report_id 上會隨重新入庫靜默消失。側檔是人工提供的文字，原樣保存，不轉繁體。
-- 不可重建（來源端的側檔可能已刪）：列入 scripts/db_backup.sh 的 BACKUP_TABLES。
CREATE TABLE research.report_metadata (
    file_hash  text PRIMARY KEY CHECK (file_hash ~ '^[0-9a-f]{64}$'),
    connector  text NOT NULL CHECK (char_length(connector) BETWEEN 1 AND 64),
    origin     text NOT NULL CHECK (char_length(origin) BETWEEN 1 AND 1024),
    metadata   jsonb NOT NULL CHECK (jsonb_typeof(metadata) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ── 對外上傳 API 的 scope ─────────────────────────────────────────────────
-- 與 app/services/api_clients.py 的 KEY_SCOPES 逐字一致；約束名稱是 0010 建表時 PostgreSQL 自動命名的。
ALTER TABLE research.api_client DROP CONSTRAINT api_client_scopes_check;
ALTER TABLE research.api_client ADD CONSTRAINT api_client_scopes_check
    CHECK (scopes <@ ARRAY['search', 'report.file', 'report.upload']::text[]);

-- ── 上傳來源的 API 用戶端 ─────────────────────────────────────────────────
-- 經 POST /external/v1/uploads 收進來的上傳：uploaded_by 是 NULL（不是帳號），改記 API 用戶端。
-- 每日上傳配額依它計數。不設 FK（同 uploaded_by：用戶端刪除後上傳紀錄照樣保留，備份還原不受表的順序牽制）。
ALTER TABLE research.report_upload ADD COLUMN api_client_id bigint;
CREATE INDEX idx_report_upload_api_client ON research.report_upload (api_client_id, uploaded_at)
    WHERE api_client_id IS NOT NULL;
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
