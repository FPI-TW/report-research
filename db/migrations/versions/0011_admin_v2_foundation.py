"""Admin v2 共用資料表：用量計數、個人配額、登入事件、功能旗標、線上 LLM 用量、每日彙總與 DB 統計快照

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
#
# Admin v2 的 schema 一次建齊在這個 revision（Wave 0），五條平行 lane（Analytics、Security、Quota、
# Flags、DB／事件）都不再碰 migration；真的要修正由整合負責人分配 0012 以後的編號。
#
# 編號：開發時是 0009（接 0008），併回 main 時對外 API 的 0010 已部署到正式環境（接 0008），所以改號成
# 0011 接在 0010 之後、維持單一 head。不反過來讓 0010 接在 0009 之後：正式環境已 stamp 在 0010 卻沒有 v2 的表，
# 版本鏈會與實際的表不符，0009 永遠不會被套用。
#
# 八張表與刪帳、備份、保留期的對照（帶 user_id 的表，刪帳由 app/services/accounts.py 的
# `_purge_user` 在同一筆交易處理；`deletion_residue` 檢查同一組表）：
#
#   表                 user_id      刪帳          備份   保留期（旋鈕在 app/config.py）
#   usage_counter      有           刪除          否     USAGE_COUNTER_RETENTION_DAYS（400）
#   user_quota         有           刪除          是     永久（覆寫值本身）
#   auth_event         可為 NULL    **保留**      是     AUTH_EVENT_RETENTION_DAYS（至少 365）
#   feature_flag       —            —             是     永久
#   llm_usage_daily    可為 NULL    刪除          否     LLM_USAGE_DAILY_RETENTION_DAYS（400）
#   usage_daily        **不得有**   —（匿名）     是     永久（量小、不可重建）
#   analytics_daily    **不得有**   —（匿名）     是     永久
#   db_stat_snapshot   —            —             否     逐時 30 天、每日 400 天
#
# auth_event 刪帳時刻意保留（使用者定案 7）：它是安全事件，保留期優先；只有 UUID、事件、IP、UA，
# **從不存帳號名稱**（不存在的帳號名也不存——使用者常把密碼誤打進帳號欄）。UUID 本身在刪帳後只連到
# app_user 的 tombstone（可識別資料已清掉），與 admin_audit_log 保留 UUID 是同一條規則。
# 365 天後由保留期清除（清除工作由 Security lane 實作）。
#
# 不設 FK 的欄位（updated_by、session_id、auth_event.user_id）理由同前幾個 revision：帳號只停用、
# 刪除只清可識別資料而保留列；備份還原不受表的順序牽制。帶 FK 的三張（usage_counter、user_quota、
# llm_usage_daily）都是 user 的附屬資料，app_user 列永遠不會被刪，CASCADE 只是保險。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── 每人每日各類次數（配額＋活躍使用者）────────────────────────────────────
-- 一列＝某人在某個台北時間日曆日、某一類動作的次數。**不記主題**：哪一篇研報、搜什麼都不在這裡
-- （使用者定案 3：只留「人×日×類別」，不建「誰看了哪篇」）。
-- kind 的詞彙在 Python（app/services/usage_events.py 的 COUNTER_KINDS）；這裡只限形狀，新增類別
-- 不必寫 revision。兩種寫入者、各管各的 kind，不會重複計數：
--   - web 的 usage middleware（usage_events 每 60 秒 upsert）：reading、report_file、search。
--   - 配額服務（Quota lane，app/services/quota.py）：ask、export、upload，以單句原子 SQL
--     `INSERT … ON CONFLICT DO UPDATE SET count = count + 1 WHERE count < :limit RETURNING count` 檢查並遞增。
-- 配額不 COUNT qa_log：使用者可以硬刪自己的歷史，等於把配額歸零。
-- 遺失的代價只是當天配額歸零：不備份。
CREATE TABLE research.usage_counter (
    user_id    uuid NOT NULL REFERENCES research.app_user (id) ON DELETE CASCADE,
    day        date NOT NULL,
    kind       text NOT NULL CHECK (kind ~ '^[a-z][a-z_]{0,31}$'),
    count      integer NOT NULL DEFAULT 0 CHECK (count >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, day, kind)
);
-- 每日活躍使用者（依日、類別聚合）與保留期清除（day < 門檻）。
CREATE INDEX idx_usage_counter_day ON research.usage_counter (day, kind);

-- ── 個人配額覆寫 ────────────────────────────────────────────────────────────
-- 程式預設在 app/config.py（QUOTA_ASK_DAILY 100／QUOTA_EXPORT_DAILY 20／UPLOAD_DAILY_QUOTA 30）；這裡只存
-- 「這個人不一樣」的值。daily_limit NULL＝不限（只有 super admin 能設，規則在 Quota lane 的服務層）、
-- 0＝完全不能用。kind 是配額的固定三類（與 usage_counter 不同，這組是契約，改它要寫 revision）。
-- 不可重建（誰在何時為誰調了多少、理由）：列入 scripts/db_backup.sh 的 BACKUP_TABLES。
CREATE TABLE research.user_quota (
    user_id     uuid NOT NULL REFERENCES research.app_user (id) ON DELETE CASCADE,
    kind        text NOT NULL CHECK (kind IN ('ask', 'export', 'upload')),
    daily_limit integer CHECK (daily_limit IS NULL OR daily_limit >= 0),
    reason      text CHECK (char_length(reason) <= 500),
    updated_by  uuid,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, kind)
);

-- ── 登入與安全事件 ──────────────────────────────────────────────────────────
-- 登入成功、各種失敗原因、被限流、TOTP 第二步失敗、權限提升失敗、登出（詞彙：
-- app/services/accounts.py 的 AUTH_EVENT_TYPES；寫入只經 accounts.record_auth_event）。
-- event／reason 只限形狀、詞彙在 Python（比照 report_upload.failure_kind），新增事件類型不必寫 revision。
--   user_id     帳號存在時才有（不存在的帳號＝NULL）。**沒有帳號名稱欄位**——刻意的，見檔頭。
--   session_id  登出、權限提升等綁 session 的事件。
--   ip／user_agent  與 user_session 同形（text；UA 截到 300 字）。
--   count       被限流期間的請求只在記憶體計數、定期寫一列彙總（避免被攻擊時 DB 寫入放大），其餘恆為 1。
-- 保留至少 365 天並納入備份（使用者定案 7）；刪帳時不刪（見檔頭）。
CREATE TABLE research.auth_event (
    id          bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    event       text NOT NULL CHECK (event ~ '^[a-z][a-z_]*(\.[a-z][a-z_]*)?$' AND char_length(event) <= 64),
    reason      text CHECK (reason ~ '^[a-z][a-z_]{0,31}$'),
    user_id     uuid,
    session_id  uuid,
    ip          text CHECK (char_length(ip) <= 64),
    user_agent  text CHECK (char_length(user_agent) <= 300),
    count       integer NOT NULL DEFAULT 1 CHECK (count >= 1)
);
-- 時間線、保留期清除、「15 分鐘內全站失敗數」。
CREATE INDEX idx_auth_event_occurred ON research.auth_event (occurred_at);
-- 單一帳號的事件（「同一帳號連續失敗」、帳號頁的登入紀錄）。
CREATE INDEX idx_auth_event_user ON research.auth_event (user_id, occurred_at) WHERE user_id IS NOT NULL;
-- 可疑 IP 清單。
CREATE INDEX idx_auth_event_ip ON research.auth_event (ip, occurred_at) WHERE ip IS NOT NULL;

-- ── 功能旗標覆寫 ────────────────────────────────────────────────────────────
-- 旗標清單登記在程式裡（app/services/feature_flags.py 的 REGISTRY）；這張表只存覆寫值，registry 沒有的
-- key 一律忽略。實際值＝環境變數上限（能力是否安裝、是否允許）AND 這裡的覆寫（沒有列＝registry 預設）。
-- DB 遺失或讀取失敗時退回 registry 預設，所以**安全閘門一律不放在這裡**（管理員 TOTP 強制是環境變數
-- ADMIN_MFA_REQUIRED，不是旗標）。
-- 作用域只有三種：allow_roles 與 allow_users 都是 NULL＝全站；任一非 NULL＝只對列出的角色或使用者生效
-- （enabled 仍須為 true）。使用者只有十幾人，不做百分比放量。
-- 寫入需要 ops.operate＋已提升，同一筆交易寫稽核 flag.update（Flags lane）。
-- 不可重建：列入備份；還原後要人工確認旗標狀態（docs/production_resilience.md）。
CREATE TABLE research.feature_flag (
    key         text PRIMARY KEY CHECK (key ~ '^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$' AND char_length(key) <= 64),
    enabled     boolean NOT NULL,
    allow_roles text[] CHECK (allow_roles <@ ARRAY['admin', 'user']::text[]),
    allow_users uuid[] CHECK (cardinality(allow_users) <= 200),
    note        text CHECK (char_length(note) <= 500),
    updated_by  uuid,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- ── 線上 LLM 每日用量（歸因到人）──────────────────────────────────────────
-- web 行程的 DeepSeek 呼叫（問答、忠實度抽查等）經 llm_http 的 observer hook 進 usage_events 累加器，
-- 每 60 秒 upsert。**只有 metadata**：token、耗時、成敗、模型、任務；不記 question／answer（使用者定案 6）。
-- 批次的用量仍在 data/llm_usage.jsonl（LLM 用量頁合併兩個來源）。token 欄位與那份檔的彙總
-- （app/services/llm_usage.py 的 Totals）同名同義。cost 預留：寫入端目前不算費用（不內建價目表，理由同
-- llm_usage.py），維持 NULL。
-- user_id NULL＝無法歸因（開發模式免登入、或不在任何請求裡的呼叫）。唯一鍵以 COALESCE 把 NULL 併成一列
-- （不用 PG15 的 NULLS NOT DISTINCT：staging 的 RDS 版本不在本 repo 控制內）。
-- 刪帳時刪除該人的列；遺失只是歸因缺一段（與 jsonl 一致）：不備份。
CREATE TABLE research.llm_usage_daily (
    day                  date NOT NULL,
    user_id              uuid REFERENCES research.app_user (id) ON DELETE CASCADE,
    task                 text NOT NULL CHECK (char_length(task) BETWEEN 1 AND 64),
    model                text NOT NULL CHECK (char_length(model) BETWEEN 1 AND 64),
    calls                integer NOT NULL DEFAULT 0 CHECK (calls >= 0),
    failures             integer NOT NULL DEFAULT 0 CHECK (failures >= 0),
    prompt_hit_tokens    bigint NOT NULL DEFAULT 0 CHECK (prompt_hit_tokens >= 0),
    prompt_miss_tokens   bigint NOT NULL DEFAULT 0 CHECK (prompt_miss_tokens >= 0),
    completion_tokens    bigint NOT NULL DEFAULT 0 CHECK (completion_tokens >= 0),
    reasoning_tokens     bigint NOT NULL DEFAULT 0 CHECK (reasoning_tokens >= 0),
    calls_without_tokens integer NOT NULL DEFAULT 0 CHECK (calls_without_tokens >= 0),
    total_ms             bigint NOT NULL DEFAULT 0 CHECK (total_ms >= 0),
    cost                 numeric(14, 6) CHECK (cost IS NULL OR cost >= 0),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT llm_usage_daily_failures_le_calls CHECK (failures <= calls)
);
CREATE UNIQUE INDEX idx_llm_usage_daily_key ON research.llm_usage_daily
    (day, COALESCE(user_id, '00000000-0000-0000-0000-000000000000'::uuid), task, model);
-- 個人用量（配額頁）、刪帳時找該人的列。
CREATE INDEX idx_llm_usage_daily_user ON research.llm_usage_daily (user_id, day) WHERE user_id IS NOT NULL;

-- ── 閱讀、原檔、搜尋、問答的每日主題計數（匿名）─────────────────────────────
-- **沒有 user_id，也不得放任何可連回個人的值**（使用者定案 3、4：刪帳後保留的前提）。
--   kind     reading（/api/reading/{hash}）、report_file（/api/report/{id}/file）、search（/api/search）、
--            ask（/api/ask）；詞彙在 usage_events.DAILY_KINDS。
--   subject  ''＝該類別當天的總量；其餘是主題：reading 是 file_hash、report_file 是 report_id、
--            search 是市場篩選（只有 findb 市場代碼；**查詢字串永不記錄**）、ask 只有總量。
--   hits     請求數（只計回 200 的）。
--   users    不重複人數的**下限**：同一行程內以記憶體集合去重，web 重啟後以 GREATEST 合併，跨行程無法再去重。
--            Analytics 顯示主題格子時 users < 3 一律不顯示（k=3，使用者定案 2）。
-- 不可重建、量小：列入備份。
CREATE TABLE research.usage_daily (
    day        date NOT NULL,
    kind       text NOT NULL CHECK (kind ~ '^[a-z][a-z_]{0,31}$'),
    subject    text NOT NULL DEFAULT '' CHECK (char_length(subject) <= 128),
    hits       bigint NOT NULL DEFAULT 0 CHECK (hits >= 0),
    users      integer NOT NULL DEFAULT 0 CHECK (users >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (day, kind, subject)
);

-- ── 每晚彙總（匿名）───────────────────────────────────────────────────────
-- scripts/analytics_rollup.py（Analytics lane，零 LLM）每晚把前一天的指標寫進來；長期趨勢讀這張，
-- 即時頁面查最近 90 天的 qa_log。dim 是維度值（市場、路由類別、標的代碼…），**不得是使用者識別**。
-- users 是該格的不重複人數（k 門檻用；只有總量的指標可為 NULL）。重跑同一天＝覆寫（主鍵 upsert）。
-- 不可重建（qa_log 會被使用者硬刪、usage_daily 只到主題層）：列入備份。
CREATE TABLE research.analytics_daily (
    day         date NOT NULL,
    metric      text NOT NULL CHECK (metric ~ '^[a-z][a-z0-9_.]{0,63}$'),
    dim         text NOT NULL DEFAULT '' CHECK (char_length(dim) <= 128),
    value       double precision NOT NULL,
    users       integer CHECK (users IS NULL OR users >= 0),
    computed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (day, metric, dim)
);
-- 單一指標的趨勢（metric 為前綴、跨日）。
CREATE INDEX idx_analytics_daily_metric ON research.analytics_daily (metric, day);

-- ── DB 統計快照（監控統計，不是 DB dump）──────────────────────────────────
-- scripts/db_snapshot.py（DB lane）每小時寫一列 granularity='hour'，每日 rollup 一列 granularity='day'。
-- 逐時保留 30 天、每日保留 400 天（使用者定案 14）。stats 是系統目錄查詢結果（大小、dead tuple、連線、
-- 快取命中率…）的 JSON 物件。遺失只是趨勢圖缺一段：不備份。
CREATE TABLE research.db_stat_snapshot (
    id          bigserial PRIMARY KEY,
    taken_at    timestamptz NOT NULL DEFAULT now(),
    granularity text NOT NULL CHECK (granularity IN ('hour', 'day')),
    stats       jsonb NOT NULL CHECK (jsonb_typeof(stats) = 'object'),
    CONSTRAINT db_stat_snapshot_granularity_taken_at_key UNIQUE (granularity, taken_at)
);
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
