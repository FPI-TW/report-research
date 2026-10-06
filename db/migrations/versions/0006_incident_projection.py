"""事件投影：research.incident、research.incident_event（incident_handler.sh 狀態轉換的 DB 投影）

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-06
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── 事件投影 ────────────────────────────────────────────────────────────────
-- **這兩張表只是 projection，不是告警的真相來源。** 告警的去重、提醒節奏、RESOLVED 判定與 Slack
-- 投遞仍然只有 scripts/incident_handler.sh（P5）在做，它不碰 DB、不依賴 web——web 或 DB 掛掉時
-- 照樣能告警。P5 在每次已落地的狀態轉換（FIRING、提醒、升級、RESOLVED；監控失明是 kind=monitor_blind
-- 的同一套轉換）多寫一行 spool（data/ops_spool/incidents-*.jsonl），由 scripts/load_observations.py
-- 冪等匯入這裡；DB 掛掉期間的事件在恢復後補匯入。
--
-- incident_id＝`<host>:<component>:<first_seen epoch>`（P5 狀態檔的 first_seen，同一次事件的
-- 每一則轉換都帶同一個值）。incident 的 status／severity／last_event_at／event_count 由 loader 依
-- incident_event 重算（與匯入順序無關，重匯不會漂移）。
--
-- 不可重建（事故歷史；journald 有保留期、spool 匯入後即刪）：列入 scripts/db_backup.sh 的 BACKUP_TABLES。
CREATE TABLE research.incident (
    incident_id   text PRIMARY KEY CHECK (incident_id ~ '^[A-Za-z0-9_.:-]{1,300}$'),
    host          text NOT NULL CHECK (char_length(host) BETWEEN 1 AND 255),
    component     text NOT NULL CHECK (component ~ '^[A-Za-z0-9_.-]{1,64}$'),
    kind          text NOT NULL CHECK (kind IN ('service', 'monitor_blind')),
    status        text NOT NULL CHECK (status IN ('firing', 'resolved')),
    severity      text NOT NULL CHECK (severity IN ('CRITICAL', 'WARNING')),
    reason        text NOT NULL CHECK (char_length(reason) <= 200),
    summary       text CHECK (char_length(summary) <= 2000),
    opened_at     timestamptz NOT NULL,
    last_event_at timestamptz NOT NULL,
    resolved_at   timestamptz,
    event_count   integer NOT NULL DEFAULT 0 CHECK (event_count >= 0),
    CONSTRAINT incident_resolved_has_time CHECK ((status = 'resolved') = (resolved_at IS NOT NULL))
);
CREATE INDEX idx_incident_opened ON research.incident (opened_at DESC);
CREATE INDEX idx_incident_component_opened ON research.incident (component, opened_at DESC);
-- 「目前還開著的事件」是管理頁最常問的一題，進行中的列很少。
CREATE INDEX idx_incident_firing ON research.incident (opened_at DESC) WHERE status = 'firing';

-- 一列＝P5 一次已落地的狀態轉換。event_id 由 P5 在寫 spool 時決定（`<incident_id>:<epoch>:<action>`），
-- 重匯以它去重。journal_excerpt 只在 FIRING 那一則：P5 當下擷取的前 10 分鐘 journal（有大小上限、
-- loader 再遮掉形似祕密的片段）；完整 log 仍以 journald 為準。
CREATE TABLE research.incident_event (
    event_id          text PRIMARY KEY CHECK (event_id ~ '^[A-Za-z0-9_.:-]{1,400}$'),
    incident_id       text NOT NULL REFERENCES research.incident (incident_id) ON DELETE CASCADE,
    occurred_at       timestamptz NOT NULL,
    action            text NOT NULL CHECK (action IN ('FIRING', 'REMINDER', 'ESCALATED', 'RESOLVED')),
    severity          text NOT NULL CHECK (severity IN ('CRITICAL', 'WARNING', 'RESOLVED')),
    reason            text NOT NULL CHECK (char_length(reason) <= 200),
    status            text CHECK (char_length(status) <= 32),
    summary           text CHECK (char_length(summary) <= 2000),
    notified          boolean NOT NULL,
    journal_excerpt   text CHECK (octet_length(journal_excerpt) <= 262144),
    journal_truncated boolean NOT NULL DEFAULT false
);
CREATE INDEX idx_incident_event_incident ON research.incident_event (incident_id, occurred_at);
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
