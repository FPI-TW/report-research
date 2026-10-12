"""admin foundation：super admin、可授予的 scope、elevated session、稽核紀錄雜湊鏈與 append-only

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-06
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── super admin ─────────────────────────────────────────────────────────────
-- 只有 super admin 能授予／收回 scope 與 super 身分（app/services/accounts.py）。只在 role='admin'
-- 時有效：降級成 user 的帳號保留這一欄但不生效。
ALTER TABLE research.app_user ADD COLUMN is_super boolean NOT NULL DEFAULT false;
-- 既有管理員在 scope 出現之前本來就擁有全部權限（生產此時只有導入個別帳號時轉入的那一位），
-- 升為 super 才不會在升級當下失去任何能力，也確保至少有一位能授予 scope。
UPDATE research.app_user SET is_super = true WHERE role = 'admin';

-- ── 可授予的 scope ──────────────────────────────────────────────────────────
-- 管理員的預設 scope 由程式決定（accounts.ADMIN_DEFAULT_SCOPES），這張表只存「必須另外授予」的。
-- 詞彙改了要寫新 revision 改 CHECK，並同步 accounts.GRANTABLE_SCOPES。
-- 不可重建：與 app_user 一起列入 scripts/db_backup.sh 的 BACKUP_TABLES。
CREATE TABLE research.user_scope (
    user_id    uuid NOT NULL REFERENCES research.app_user (id) ON DELETE CASCADE,
    scope      text NOT NULL CHECK (scope IN ('qa_content.read', 'ops.operate')),
    granted_by uuid,
    granted_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, scope)
);

-- ── elevated session ────────────────────────────────────────────────────────
-- 重新驗證密碼後的 10 分鐘權限提升視窗（POST /api/admin/elevate）。綁在 session 上：
-- 另一個裝置、或登出再登入，都要重新驗證。
ALTER TABLE research.user_session ADD COLUMN elevated_until timestamptz;

-- ── 稽核紀錄：雜湊鏈＋只能新增 ────────────────────────────────────────────
-- 每列的 row_hash 涵蓋本列內容與前一列的 row_hash；改任何一列（或刪掉中間一列）都會讓之後的
-- 鏈對不上。DB superuser 仍可整條重算，所以 scripts/audit_anchor.py 每日把鏈頭寫到 DB 之外
-- 並比對先前的錨點——真正的防竄改證據在那裡，這張表的鏈只是讓竄改「必須留下痕跡」。
ALTER TABLE research.admin_audit_log ADD COLUMN prev_hash text, ADD COLUMN row_hash text;

-- 雜湊內容的唯一定義：觸發器與驗證查詢（app/services/audit.py）共用，避免兩邊各寫一份而漂移。
-- 欄位以 0x1F（unit separator）分隔、NULL 寫成空字串；created_at 一律轉 UTC 到微秒。
CREATE FUNCTION research.audit_row_hash(
    p_id bigint, p_actor uuid, p_action text, p_target_type text, p_target_id text,
    p_detail jsonb, p_created_at timestamptz, p_prev_hash text
) RETURNS text LANGUAGE sql STABLE AS $$
    SELECT encode(sha256(convert_to(concat_ws(E'\x1f',
        p_id::text, coalesce(p_actor::text, ''), p_action, p_target_type, coalesce(p_target_id, ''),
        p_detail::text, to_char(p_created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'),
        coalesce(p_prev_hash, '')
    ), 'UTF8')), 'hex')
$$;

-- 既有列依 id 補上鏈（必須早於下面的 append-only 觸發器）。
DO $$
DECLARE
    r record;
    prev text := NULL;
BEGIN
    FOR r IN SELECT * FROM research.admin_audit_log ORDER BY id LOOP
        UPDATE research.admin_audit_log
           SET prev_hash = prev,
               row_hash = research.audit_row_hash(r.id, r.actor_user_id, r.action, r.target_type,
                                                  r.target_id, r.detail, r.created_at, prev)
         WHERE id = r.id
        RETURNING row_hash INTO prev;
    END LOOP;
END $$;
ALTER TABLE research.admin_audit_log ALTER COLUMN row_hash SET NOT NULL;

-- 新增時：取全域 advisory lock（交易結束才放）後**重新取號**，讓 id 順序＝鏈的順序；
-- 否則兩筆並行交易的 id 先後與取得鎖的先後可能相反，驗證時以 id 排序就會對不上。
-- 重新取號會讓先前預取的號碼成為空號，bigserial 本來就不保證連號。
CREATE FUNCTION research.audit_chain_before_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    prev text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('research.admin_audit_log.chain'));
    NEW.id := nextval(pg_get_serial_sequence('research.admin_audit_log', 'id'));
    SELECT row_hash INTO prev FROM research.admin_audit_log ORDER BY id DESC LIMIT 1;
    NEW.prev_hash := prev;
    NEW.row_hash := research.audit_row_hash(NEW.id, NEW.actor_user_id, NEW.action, NEW.target_type,
                                            NEW.target_id, NEW.detail, NEW.created_at, prev);
    RETURN NEW;
END $$;
CREATE TRIGGER admin_audit_log_chain BEFORE INSERT ON research.admin_audit_log
    FOR EACH ROW EXECUTE FUNCTION research.audit_chain_before_insert();

-- 只能新增：UPDATE／DELETE／TRUNCATE 一律拒絕。還原備份走 pg_restore --disable-triggers，不受影響。
CREATE FUNCTION research.audit_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'research.admin_audit_log 只能新增（%）', TG_OP USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER admin_audit_log_append_only BEFORE UPDATE OR DELETE ON research.admin_audit_log
    FOR EACH ROW EXECUTE FUNCTION research.audit_append_only();
CREATE TRIGGER admin_audit_log_no_truncate BEFORE TRUNCATE ON research.admin_audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION research.audit_append_only();
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
