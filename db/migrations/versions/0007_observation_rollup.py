"""監控觀測的聚合表（research.service_observation_5m、research.service_observation_1h）

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-06
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── 監控觀測的保留與聚合 ─────────────────────────────────────────────────────
-- 保留期 90 天，越舊越粗（由 scripts/rollup_observations.py 每小時搬移；SQL 在 app/services/ops_rollup.py）：
--
--   0–24 小時   原始觀測 research.service_observation（收集器每 60 秒一筆）
--   24 小時–7 天 5 分鐘聚合 research.service_observation_5m
--   7–90 天     1 小時聚合 research.service_observation_1h
--   90 天以前   刪除（job_execution 同樣 90 天；incident／incident_event 不在此範圍，見 0006）
--
-- 為什麼是這三段：管理頁回頭看「剛剛發生什麼」要逐筆（24 小時內的事件要對得上 60 秒的取樣）；一週內的回顧
-- 以 5 分鐘為單位仍看得出尖峰的形狀（5 個取樣、保留 min／max），而且跟 P5 的去抖、loader 的匯入間隔同一個
-- 量級；一週以上看的是趨勢與容量（docs/CAPACITY.md 那一類問題），1 小時足夠，90 天每條序列 2160 點。
-- 列數量級（約 200 條序列）：原始每日約 29 萬列；5 分鐘每日約 5.8 萬列 × 6 天；1 小時每日約 4800 列 × 83 天。
--
-- 搬移是**原子的**：同一句 SQL 先 DELETE 原始（或較細的聚合）、再把被刪掉的那些列聚合後寫進較粗的表
-- （data-modifying CTE，同一個 snapshot、同一個交易），寫入失敗整句回滾、來源原封不動。所以任何一筆觀測在
-- 任何時刻只存在於一張表——查詢端把三張表聯集起來不會重複計數。遲到的資料（DB 掛掉期間 spool 積壓、恢復後
-- 才匯入的舊觀測）落在已經聚合過的桶時以合併方式寫入（ON CONFLICT 加總），不會蓋掉既有的桶。
--
-- 每一列＝一個桶 × 一條序列（host、scope、subject、metric）：
--   sample_count  桶內的觀測筆數（數值與狀態都算）
--   value_*       數值型指標的筆數、最小、最大、平均、最後一筆（value_count=0 時都是 NULL）
--   first_state／last_state／state_changes
--                 狀態型指標：桶內第一個與最後一個狀態、相鄰兩筆不同的次數（跨桶的轉換看相鄰兩桶的
--                 last_state 與 first_state）。只有 last_state 不夠：5 分鐘內 active → failed → active
--                 的閃斷會被「最後是 active」吃掉，state_changes 讓它留下痕跡。
--   last_detail   最後一個狀態列附帶的屬性（與原始表的 detail 同義）
--   first_at／last_at 桶內實際的第一筆與最後一筆觀測時間（合併與排序用）
--
-- 可重建性：聚合表是原始觀測的派生（原始還在的那一段隨時可由它重算），和原始表一樣只是監控投影——遺失＝
-- 少一段歷史曲線，服務與告警都不受影響（告警只有 P5，不經 DB）。**刻意不列入** scripts/db_backup.sh 的
-- BACKUP_TABLES，與 0005 的原始表同一個理由。
-- 桶的起點以 UTC 2000-01-01 為原點 date_bin，與 session 時區無關（date_trunc('hour') 遇到半小時時區會錯位）。
CREATE TABLE research.service_observation_5m (
    bucket_start  timestamptz NOT NULL,
    host          text NOT NULL CHECK (char_length(host) BETWEEN 1 AND 255),
    scope         text NOT NULL CHECK (scope IN ('host', 'container', 'service')),
    subject       text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 200),
    metric        text NOT NULL CHECK (metric ~ '^[a-z][a-z0-9_]{0,63}$'),
    sample_count  integer NOT NULL CHECK (sample_count >= 1),
    value_count   integer NOT NULL CHECK (value_count >= 0),
    value_min     double precision,
    value_max     double precision,
    value_avg     double precision,
    value_last    double precision,
    first_state   text CHECK (char_length(first_state) <= 64),
    last_state    text CHECK (char_length(last_state) <= 64),
    state_changes integer NOT NULL DEFAULT 0 CHECK (state_changes >= 0),
    last_detail   jsonb,
    first_at      timestamptz NOT NULL,
    last_at       timestamptz NOT NULL,
    CONSTRAINT service_observation_5m_pkey PRIMARY KEY (subject, metric, bucket_start, scope, host),
    CONSTRAINT service_observation_5m_counts CHECK (value_count <= sample_count),
    CONSTRAINT service_observation_5m_values CHECK ((value_count = 0) = (value_avg IS NULL)),
    CONSTRAINT service_observation_5m_span CHECK (
        bucket_start <= first_at AND first_at <= last_at AND last_at < bucket_start + interval '5 minutes')
);
-- 主鍵的欄位順序同原始表的自然鍵：服務「某對象（＋指標）的時間範圍」；這支索引服務只看時間（總覽、保留期刪除）。
CREATE INDEX idx_service_observation_5m_time ON research.service_observation_5m (bucket_start DESC);

CREATE TABLE research.service_observation_1h (
    bucket_start  timestamptz NOT NULL,
    host          text NOT NULL CHECK (char_length(host) BETWEEN 1 AND 255),
    scope         text NOT NULL CHECK (scope IN ('host', 'container', 'service')),
    subject       text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 200),
    metric        text NOT NULL CHECK (metric ~ '^[a-z][a-z0-9_]{0,63}$'),
    sample_count  integer NOT NULL CHECK (sample_count >= 1),
    value_count   integer NOT NULL CHECK (value_count >= 0),
    value_min     double precision,
    value_max     double precision,
    value_avg     double precision,
    value_last    double precision,
    first_state   text CHECK (char_length(first_state) <= 64),
    last_state    text CHECK (char_length(last_state) <= 64),
    state_changes integer NOT NULL DEFAULT 0 CHECK (state_changes >= 0),
    last_detail   jsonb,
    first_at      timestamptz NOT NULL,
    last_at       timestamptz NOT NULL,
    CONSTRAINT service_observation_1h_pkey PRIMARY KEY (subject, metric, bucket_start, scope, host),
    CONSTRAINT service_observation_1h_counts CHECK (value_count <= sample_count),
    CONSTRAINT service_observation_1h_values CHECK ((value_count = 0) = (value_avg IS NULL)),
    CONSTRAINT service_observation_1h_span CHECK (
        bucket_start <= first_at AND first_at <= last_at AND last_at < bucket_start + interval '1 hour')
);
CREATE INDEX idx_service_observation_1h_time ON research.service_observation_1h (bucket_start DESC);
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
