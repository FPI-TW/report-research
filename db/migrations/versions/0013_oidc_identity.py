"""OIDC 單一登入：帳號綁定公司 IdP 的身分（issuer, subject），session 記下登入方式與 IdP 是否做了多重驗證

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-09
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單（這裡沒有新表）。
#
# 編號：接在框架化 v1 的 0012（研報 doc_type）之後，維持單一 head。
#
# 規則在 app/services/accounts.py（`sso_login`）：
# - 一個 (oidc_issuer, oidc_subject) 只能綁一個帳號（UNIQUE）；一個帳號最多綁一個 IdP 身分（兩欄同一列）。
#   兩欄要嘛都有、要嘛都沒有（CHECK）。subject 是 IdP 給的不透明識別碼（OIDC Core §2 `sub`），不是帳號名稱。
# - 刪帳（`_purge_user`）清掉這兩欄：subject 連得回 IdP 的那個人，屬於可識別資料。
# - user_session.auth_method：這個 session 是怎麼登入的。SSO_ONLY 開啟時，本地密碼登入的 session 只在
#   帳號仍是緊急管理員時有效（`resolve_session`）；SSO 管理員的 TOTP 強制判斷看 idp_mfa（`web/authz.py`）。
# - user_session.idp_mfa：登入當下 IdP 的 ID token 是否表明做過多重驗證（amr／acr，判準在 app/services/oidc.py）。
#   本地密碼登入一律 false。
# app_user 與 user_session 本來就各在／不在備份清單裡（user_session 刻意不備份），這裡不改。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE research.app_user
    ADD COLUMN oidc_issuer  text,
    ADD COLUMN oidc_subject text,
    ADD CONSTRAINT app_user_oidc_identity_pair CHECK ((oidc_issuer IS NULL) = (oidc_subject IS NULL)),
    ADD CONSTRAINT app_user_oidc_identity_length
        CHECK (char_length(oidc_issuer) <= 512 AND char_length(oidc_subject) <= 255),
    ADD CONSTRAINT app_user_oidc_identity_key UNIQUE (oidc_issuer, oidc_subject);

ALTER TABLE research.user_session
    ADD COLUMN auth_method text NOT NULL DEFAULT 'password' CHECK (auth_method IN ('password', 'sso')),
    ADD COLUMN idp_mfa     boolean NOT NULL DEFAULT false,
    ADD CONSTRAINT user_session_idp_mfa_only_sso CHECK (auth_method = 'sso' OR NOT idp_mfa);
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
