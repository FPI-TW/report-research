"""服務觀測與批次執行紀錄（research.service_observation、research.job_execution）

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-06
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── 服務／主機觀測（時間序列）──────────────────────────────────────────────
-- 來源：scripts/collect_resource_usage.py 先寫本機 spool（data/ops_spool/observations-*.jsonl），
-- 再由 scripts/load_observations.py 冪等匯入。**DB 不是觀測的第一落點**：DB 掛掉的那段時間正是最需要
-- 觀測的時候，所以收集器不連 DB，DB 恢復後 loader 把 spool 補匯入。
--
-- 一列＝某個時點、某個對象（subject）的某個指標。數值型指標寫 value（cpu_pct、mem_used_pct、
-- psi_io_some_avg60…），狀態型指標寫 state（容器的 status、systemd 的 ActiveState），附帶屬性放 detail
-- （只掛在狀態列上）。scope：host（主機與檔案系統）、container（catalog 列的容器）、service（catalog 列的
-- systemd unit）。
--
-- 可重建性：遺失＝少一段歷史曲線，服務與告警都不受影響（告警只有 P5，不經 DB）。**刻意不列入**
-- scripts/db_backup.sh 的 BACKUP_TABLES。保留期與聚合是之後的事，這裡只保證索引撐得起
-- 「依對象（＋指標）＋時間」與「只依時間」（總覽、日後的保留期刪除）的查詢。
CREATE TABLE research.service_observation (
    id          bigserial PRIMARY KEY,
    observed_at timestamptz NOT NULL,
    host        text NOT NULL CHECK (char_length(host) BETWEEN 1 AND 255),
    scope       text NOT NULL CHECK (scope IN ('host', 'container', 'service')),
    subject     text NOT NULL CHECK (char_length(subject) BETWEEN 1 AND 200),
    metric      text NOT NULL CHECK (metric ~ '^[a-z][a-z0-9_]{0,63}$'),
    value       double precision,
    state       text CHECK (char_length(state) <= 64),
    detail      jsonb,
    CONSTRAINT service_observation_value_or_state CHECK (value IS NOT NULL OR state IS NOT NULL)
);
-- 自然鍵：同一份 spool 匯入兩次不得重複（loader 以 ON CONFLICT DO NOTHING 去重）。
-- 欄位順序同時服務「某對象某指標的時間範圍」查詢。
CREATE UNIQUE INDEX uq_service_observation_natural
    ON research.service_observation (subject, metric, observed_at, scope, host);
-- 「某對象全部指標的時間範圍」（管理頁的服務明細、主機頁）。
CREATE INDEX idx_service_observation_subject_time
    ON research.service_observation (subject, observed_at DESC);
-- 只看時間範圍（總覽）與之後的保留期刪除。
CREATE INDEX idx_service_observation_time
    ON research.service_observation (observed_at DESC);

-- ── 批次 oneshot 的每次執行 ─────────────────────────────────────────────────
-- 來源同上（spool 的 jobs-*.jsonl）。「跑過」的判準比照 scripts/verify_oneshot_ran.sh：
-- `Result=success`／`ExecMainStatus=0` 不是證據（從未執行過的 unit 也是這兩個值），必須有
-- InvocationID 與 ExecMainStartTimestamp。一列＝一次 invocation（主機＋unit＋InvocationID）：
--   running  收集器看到它正在跑；
--   finished 看到它結束（finished_at、result、exit_status 來自 systemd），之後不再變動；
--   lost     沒看到它結束、同一個 unit 卻已經有更晚開始的 invocation（oneshot 不會重疊執行，
--            所以它必定已經結束，只是結果不明：收集器停機、或兩次觀測之間跑完又被下一輪覆蓋）。
--            日後若補到它的 finished 紀錄（spool 亂序）仍會改成 finished。
--
-- 可重建性：這是觀測投影——systemd 只保留每個 unit 最近一次的屬性，更早的執行要靠 journald。遺失＝
-- 管理頁少了執行歷史，不影響任何批次的正確性；之後也會依保留期刪舊列。**刻意不列入備份**。
CREATE TABLE research.job_execution (
    id             bigserial PRIMARY KEY,
    host           text NOT NULL CHECK (char_length(host) BETWEEN 1 AND 255),
    unit           text NOT NULL CHECK (unit ~ '^[A-Za-z0-9][A-Za-z0-9@._:-]{0,200}\.service$'),
    service        text CHECK (char_length(service) <= 40),
    invocation_id  text NOT NULL CHECK (invocation_id ~ '^[0-9a-f]{32}$'),
    state          text NOT NULL CHECK (state IN ('running', 'finished', 'lost')),
    started_at     timestamptz NOT NULL,
    finished_at    timestamptz,
    result         text CHECK (char_length(result) <= 64),
    exit_status    integer,
    exec_main_code text CHECK (exec_main_code IN ('exited', 'killed', 'dumped')),
    first_seen_at  timestamptz NOT NULL,
    last_seen_at   timestamptz NOT NULL,
    CONSTRAINT job_execution_finished_has_end CHECK ((state = 'finished') = (finished_at IS NOT NULL)),
    CONSTRAINT uq_job_execution_invocation UNIQUE (host, unit, invocation_id)
);
-- 「某個 unit 的執行歷史」與 loader 判斷 lost（同主機同 unit 是否有更晚開始的 invocation）。
CREATE INDEX idx_job_execution_unit_started ON research.job_execution (unit, started_at DESC);
-- 只看時間範圍（管理頁的排程工作清單）。
CREATE INDEX idx_job_execution_started ON research.job_execution (started_at DESC);
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
