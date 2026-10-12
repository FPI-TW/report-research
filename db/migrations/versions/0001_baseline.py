"""baseline：導入 Alembic 當下 main 的完整 schema（db/schema.sql，已凍結）

Revision ID: 0001
Revises:
Create Date: 2026-10-05
"""
# 只接受空庫（新機器、CI、災難還原的新叢集）。既有庫不得 upgrade 到這裡，必須先證明零 drift
# 再 stamp：make schema-check → make schema-stamp-baseline（scripts/schema_baseline.py）。
from alembic import op

from app.services import schema_migrations as sm

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# baseline 的 SQL 是 db/schema.sql 本身（以 SHA-256 釘住），不複製一份進來。
UPGRADE_SQL = None


def upgrade() -> None:
    bind = op.get_bind()
    sm.assert_research_empty(bind)
    sm.run_sql_script(bind, sm.baseline_sql())


def downgrade() -> None:
    raise NotImplementedError("baseline 不提供回退：要清空請重建資料庫")
