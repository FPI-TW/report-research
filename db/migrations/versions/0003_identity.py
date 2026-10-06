"""identity：TOTP 兩步驟驗證、帳號刪除排程與 tombstone 欄位

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-06
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── TOTP（RFC 6238，app/services/totp.py）────────────────────────────────────
-- 每位使用者自己開關。totp_secret 是 base32 字串：開啟流程先寫入 secret（totp_enabled 仍為 false），
-- 使用者輸入一次正確的驗證碼才把 totp_enabled 設為 true。totp_last_step 是最後一次被接受的
-- 30 秒時間步：同一個時間步（以及更早的）的碼不能再用一次，擋住偷看／重放。
-- secret 以明文存放（app_user 已在備份裡、備份檔本來就要當機密看待）；理由見 accounts.py。
ALTER TABLE research.app_user
    ADD COLUMN totp_secret    text,
    ADD COLUMN totp_enabled   boolean NOT NULL DEFAULT false,
    ADD COLUMN totp_last_step bigint,
    ADD CONSTRAINT app_user_totp_secret_when_enabled CHECK (NOT totp_enabled OR totp_secret IS NOT NULL);

-- ── 刪除後的 tombstone ──────────────────────────────────────────────────────
-- 帳號被刪除時 app_user 這一列保留（UUID 仍被 admin_audit_log、review_state.reviewer_user_id、
-- user_scope.granted_by 指著），但可識別與可登入的資料全部清掉，並蓋上 deleted_at。
-- 已刪除的帳號不可能是啟用中的。
ALTER TABLE research.app_user
    ADD COLUMN deleted_at timestamptz,
    ADD CONSTRAINT app_user_deleted_is_disabled CHECK (deleted_at IS NULL OR NOT enabled);

-- ── 帳號刪除排程 ────────────────────────────────────────────────────────────
-- 管理員提出刪除＝立即停用＋撤銷所有 session＋排程 execute_after（24 小時後）執行；在那之前可以
-- 取消（cancelled_at）。執行（scripts/execute_deletions.py）在同一筆交易刪掉該使用者的 qa_log、
-- 指向那些 qa_log 的 review_state、user_scope、user_session，清掉 app_user 的可識別資料並蓋
-- executed_at。prior_enabled 是提出當下的啟用狀態：取消時還原。
-- 不可重建：加入 scripts/db_backup.sh 的 BACKUP_TABLES（還原後尚未執行的排程不能消失）。
-- 已執行的刪除另有 DB 之外的 tombstone（$REPORT_MARK_BACKUP_DIR/account-tombstones.jsonl），
-- 從舊備份還原後由 scripts/replay_deletions.py 重新刪除——備份裡的 qa_log 不會因此復活。
CREATE TABLE research.account_deletion (
    id            bigserial PRIMARY KEY,
    user_id       uuid NOT NULL REFERENCES research.app_user (id),
    requested_by  uuid,
    requested_at  timestamptz NOT NULL DEFAULT now(),
    execute_after timestamptz NOT NULL,
    prior_enabled boolean NOT NULL,
    cancelled_at  timestamptz,
    cancelled_by  uuid,
    executed_at   timestamptz,
    CONSTRAINT account_deletion_cancelled_or_executed CHECK (cancelled_at IS NULL OR executed_at IS NULL)
);
-- 同一個帳號同時最多一筆尚未結束（未取消、未執行）的排程。
CREATE UNIQUE INDEX idx_account_deletion_pending_user
    ON research.account_deletion (user_id) WHERE cancelled_at IS NULL AND executed_at IS NULL;
CREATE INDEX idx_account_deletion_due
    ON research.account_deletion (execute_after) WHERE cancelled_at IS NULL AND executed_at IS NULL;
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
