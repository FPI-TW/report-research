"""對外 API 用戶端：金鑰、授權範圍與每日用量（research.api_client／api_client_entitlement／api_client_usage）

Revision ID: 0010
Revises: 0008
Create Date: 2026-10-07
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0010"
down_revision = "0008"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── API 用戶端 ──────────────────────────────────────────────────────────────
-- 一列＝一個以 `Authorization: Bearer <api_key>` 呼叫 /external/v1/* 的用戶端（規則在 app/services/api_clients.py）。
-- 一個用戶端一把金鑰：DB 只存 key_hash（sha256 hex）與可公開的 key_prefix（管理頁辨識、日誌對照用），
-- 原始金鑰只在建立與輪替時回給管理員一次。輪替＝直接換掉 prefix 與 hash，舊金鑰下一個請求就失效。
-- scopes 是金鑰能呼叫的端點（詞彙 api_clients.KEY_SCOPES，與這裡的 CHECK 逐字一致）。
-- created_by 不設 FK（同 admin_audit_log.actor_user_id：帳號只停用不刪列，備份還原不受表的順序牽制）。
-- 不可重建（用戶端設定、限流與額度、誰建立的）：列入 scripts/db_backup.sh 的 BACKUP_TABLES。
CREATE TABLE research.api_client (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name               text NOT NULL UNIQUE CHECK (char_length(name) BETWEEN 1 AND 100),
    key_prefix         text NOT NULL UNIQUE,
    key_hash           text NOT NULL UNIQUE,
    enabled            boolean NOT NULL DEFAULT true,
    scopes             text[] NOT NULL DEFAULT '{}' CHECK (scopes <@ ARRAY['search', 'report.file']::text[]),
    rate_limit_per_min integer NOT NULL DEFAULT 60 CHECK (rate_limit_per_min BETWEEN 1 AND 6000),
    daily_quota        integer NOT NULL DEFAULT 1000 CHECK (daily_quota BETWEEN 1 AND 1000000),
    note               text,
    created_by         uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    key_rotated_at     timestamptz,
    last_used_at       timestamptz
);

-- ── 授權範圍（allowlist）──────────────────────────────────────────────────
-- 一列＝某個維度允許的一個值。維度沒有任何列＝不限；market 由服務層要求必填且非空。
-- 值的詞彙（市場代碼等）由 Python 驗證、這裡只擋空字串：新增市場代碼不必寫 revision。
-- 不可重建：列入 BACKUP_TABLES。
CREATE TABLE research.api_client_entitlement (
    client_id bigint NOT NULL REFERENCES research.api_client (id) ON DELETE CASCADE,
    dimension text NOT NULL CHECK (dimension IN ('market', 'source', 'report_type', 'instrument_type')),
    value     text NOT NULL CHECK (btrim(value) <> ''),
    PRIMARY KEY (client_id, dimension, value)
);

-- ── 每日用量 ────────────────────────────────────────────────────────────────
-- 每日額度的計數器：api_clients.consume_quota 以單一 INSERT … ON CONFLICT DO UPDATE 原子遞增。
-- 刻意不備份：遺失的代價只是當天額度重新起算。
CREATE TABLE research.api_client_usage (
    client_id     bigint NOT NULL REFERENCES research.api_client (id) ON DELETE CASCADE,
    day           date NOT NULL,
    request_count integer NOT NULL DEFAULT 0,
    PRIMARY KEY (client_id, day)
);

-- ── 可授予的管理 scope 加入 api_clients.manage ─────────────────────────────
-- 與 app/services/accounts.py 的 GRANTABLE_SCOPES 逐字一致；約束名稱是 0002 建表時 PostgreSQL 自動命名的。
ALTER TABLE research.user_scope DROP CONSTRAINT user_scope_scope_check;
ALTER TABLE research.user_scope ADD CONSTRAINT user_scope_scope_check
    CHECK (scope IN ('qa_content.read', 'ops.operate', 'api_clients.manage'));
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
