"""事件投影：research.incident、research.incident_event（P5 狀態轉換的 DB 投影）

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
-- **這兩張表只是 projection，不是告警的真相來源。** 去重、提醒節奏、FIRING／RESOLVED 判定與 Slack
-- 投遞仍然只有 scripts/incident_handler.sh（P5）在做；它不碰 DB、不依賴 web，web 或 DB 掛掉時照樣告警。
-- P5 在每次**已落地**的狀態轉換（FIRING、REMINDER、ESCALATED、RESOLVED；監控失明是 kind=monitor_blind
-- 的同一套轉換）另寫一行本機 spool（data/ops_spool/incidents-*.jsonl），由 scripts/load_observations.py
-- 冪等匯入這裡；DB 掛掉期間的事件在恢復後補匯入。spool 寫入失敗時 P5 照常告警，這裡就少那一筆——
-- 所以這裡可能缺事件，P5 的狀態檔（data/.incidents*/）與 Slack 才是當下的真相。
--
-- incident_id＝`<host>:<component>:<first_seen epoch>`（P5 狀態檔的 first_seen，同一次事件的每一則轉換
-- 都帶同一個值）。component 在所有 P5 實例間唯一（tests/test_edge_health_probe.py 守），所以這個鍵不會撞。
--
-- status 由 loader 依 incident_event **重算**（與匯入順序無關，重匯不會漂移）：
--   firing    還沒收到 RESOLVED，同元件也沒有更晚開的事件
--   resolved  收到 RESOLVED（resolved_at＝那一則的時間）
--   lost      沒收到 RESOLVED，但同主機同元件已有更晚開的事件——P5 同一個元件同時只會有一個事件，
--             所以它必定已經結束，只是結束時間不明（RESOLVED 那一行沒寫進 spool、或狀態檔被人刪掉）。
--             日後補到它的 RESOLVED（spool 亂序）仍會改成 resolved。**不把不明當成 firing。**
-- severity／reason 取最近一則非 RESOLVED 事件的值；summary 取第一則 FIRING 的說明。
--
-- 不可重建（事故歷史；journald 有保留期、spool 匯入後即刪）：列入 scripts/db_backup.sh 的 BACKUP_TABLES。
-- 長度上限寫成 char_length 而不是 regex 的 {1,300}：PostgreSQL 的 regex 重複次數上限是 255，超過的
-- 寫法建表時不會報錯、要到第一次比對才失敗。
CREATE TABLE research.incident (
    incident_id   text PRIMARY KEY CHECK (incident_id ~ '^[A-Za-z0-9_.:-]+$' AND char_length(incident_id) <= 300),
    host          text NOT NULL CHECK (char_length(host) BETWEEN 1 AND 255),
    component     text NOT NULL CHECK (component ~ '^[A-Za-z0-9_.-]{1,64}$'),
    kind          text NOT NULL CHECK (kind IN ('service', 'monitor_blind')),
    probe_unit    text CHECK (char_length(probe_unit) <= 255),
    status        text NOT NULL CHECK (status IN ('firing', 'resolved', 'lost')),
    severity      text NOT NULL CHECK (severity IN ('CRITICAL', 'WARNING')),
    reason        text NOT NULL CHECK (char_length(reason) <= 200),
    summary       text CHECK (char_length(summary) <= 2000),
    opened_at     timestamptz NOT NULL,
    last_event_at timestamptz NOT NULL,
    resolved_at   timestamptz,
    event_count   integer NOT NULL DEFAULT 0 CHECK (event_count >= 0),
    CONSTRAINT incident_resolved_has_time CHECK ((status = 'resolved') = (resolved_at IS NOT NULL))
);
-- 管理頁的清單（新→舊）與依元件篩選；「同元件有沒有更晚開的事件」（判 lost）也走第二個。
CREATE INDEX idx_incident_opened ON research.incident (opened_at DESC);
CREATE INDEX idx_incident_component_opened ON research.incident (component, opened_at DESC);
-- 「目前還開著的事件」是最常問的一題，而進行中的列很少。
CREATE INDEX idx_incident_firing ON research.incident (opened_at DESC) WHERE status = 'firing';

-- 一列＝P5 一次已落地的狀態轉換。event_id 由 P5 寫 spool 時決定（`<incident_id>:<epoch>:<action>`），
-- 重匯以它去重。journal_* 只在 FIRING 與 RESOLVED：P5 當下以 journalctl 擷取的片段（FIRING＝事件前
-- 10 分鐘到當下，RESOLVED＝開場後 10 分鐘），有大小上限、loader 匯入前再遮掉形似祕密的片段；
-- **完整 log 仍以 journald 為準**，這裡只是事後回頭看時省一步。
CREATE TABLE research.incident_event (
    event_id          text PRIMARY KEY CHECK (event_id ~ '^[A-Za-z0-9_.:-]+$' AND char_length(event_id) <= 400),
    incident_id       text NOT NULL REFERENCES research.incident (incident_id) ON DELETE CASCADE,
    occurred_at       timestamptz NOT NULL,
    action            text NOT NULL CHECK (action IN ('FIRING', 'REMINDER', 'ESCALATED', 'RESOLVED')),
    severity          text NOT NULL CHECK (severity IN ('CRITICAL', 'WARNING', 'RESOLVED')),
    reason            text NOT NULL CHECK (char_length(reason) <= 200),
    status            text CHECK (char_length(status) <= 32),
    summary           text CHECK (char_length(summary) <= 2000),
    notified          boolean NOT NULL,
    journal_excerpt   text CHECK (octet_length(journal_excerpt) <= 262144),
    journal_truncated boolean NOT NULL DEFAULT false,
    journal_since     timestamptz,
    journal_until     timestamptz,
    journal_units     text CHECK (char_length(journal_units) <= 500),
    CONSTRAINT incident_event_resolved_severity CHECK ((action = 'RESOLVED') = (severity = 'RESOLVED'))
);
CREATE INDEX idx_incident_event_incident ON research.incident_event (incident_id, occurred_at);
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
