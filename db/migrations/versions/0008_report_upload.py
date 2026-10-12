"""研報上傳：發布狀態與上傳紀錄（research.report_visibility.publication、research.report_upload）

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-06
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt；新增不可重建的表要動備份清單。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
-- ── 發布狀態（草稿／已發布）─────────────────────────────────────────────────
-- 掛在既有的 report_visibility（以 file_hash 為鍵、刻意無 FK，見 0004）：上傳的研報入庫後先是草稿，
-- 管理員審過才發布。sync 從 NAS 進來的研報**沒有這一列**，等同已發布——「自動同步直接發布」零改動。
-- 讀取路徑只認 app/services/visibility.py 的片段：hidden 或 publication <> 'published' 都不可見
-- （tests/test_visibility_guard.py 守每條讀取 SQL 都帶片段）。
--
-- 為什麼不是 research_report 的欄位：store.upsert_report 先刪後插、換新 report_id，掛在那上面的
-- 草稿標記會在重新入庫時靜默消失，草稿就以「已發布」狀態漏出去。
-- published_at／published_by：草稿被發布的時刻與人（審核 API 寫入）。sync 進來、或只被隱藏過的列
-- 維持 NULL；「可以永久清除」的判準是 publication='draft' AND published_at IS NULL（清除函式與 SQL 兩道守門）。
-- published_by 不設 FK（同 updated_by）。
ALTER TABLE research.report_visibility
    ADD COLUMN publication  text NOT NULL DEFAULT 'published' CHECK (publication IN ('draft', 'published')),
    ADD COLUMN published_at timestamptz,
    ADD COLUMN published_by uuid;

-- ── 上傳紀錄 ────────────────────────────────────────────────────────────────
-- 一列＝管理員上傳的一個檔案，從進隔離區到發布／退回的整段歷程（狀態機由 Python 決定，
-- 詞彙在 app/services/uploads.py）：
--
--   quarantined ─認領─▶ scanning ─OK─▶ clean ─取 LLM 鎖─▶ processing ─▶ draft ─publish─▶ published
--   scanning ─FOUND─▶ infected（終態）；決定性錯誤重試用盡 ─▶ blocked（終態）
--   processing ─▶ failed／duplicate；draft、quarantined、clean、failed ─reject─▶ rejected
--
-- 以 file_hash 為鍵連到語料與 report_visibility，**不存 report_id**（重新入庫會換）；也不設 FK：
-- 語料可能還沒入庫（掃描中）或已被清除（退回），上傳紀錄本身要永久留著。
-- uploaded_by／decided_by 不設 FK（帳號只停用不刪除；備份還原不受表的順序牽制）。
-- failure_kind **刻意無 CHECK**（比照 review_state）：詞彙在 Python 常數，新增失敗類別不必寫 revision。
-- 不可重建（誰在何時上傳了什麼、掃描結果與病毒名、退回原因）：列入 scripts/db_backup.sh 的 BACKUP_TABLES。
CREATE TABLE research.report_upload (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    file_hash        text NOT NULL CHECK (file_hash ~ '^[0-9a-f]{64}$'),
    -- 清理後的原始檔名（parse_filename 靠它推券商與日期）；最短 "x.pdf"。
    original_name    text NOT NULL CHECK (char_length(original_name) BETWEEN 5 AND 255),
    size_bytes       bigint NOT NULL CHECK (size_bytes > 0),
    -- 瀏覽器的 File.lastModified；worker 以 os.utime 設回檔案，供 report_date 回退。
    client_mtime     timestamptz,
    uploaded_by      uuid,
    uploaded_at      timestamptz NOT NULL DEFAULT now(),
    state            text NOT NULL CHECK (state IN (
                         'quarantined', 'scanning', 'clean', 'infected', 'blocked',
                         'processing', 'draft', 'failed', 'duplicate', 'published', 'rejected')),
    -- 最後一次狀態轉換的時刻；scanning／processing 時兼作認領時刻。
    state_changed_at timestamptz NOT NULL DEFAULT now(),
    scan_attempts    integer NOT NULL DEFAULT 0,
    -- 例如 "ClamAV 1.4.x/<dbver>/<date>"：掃描當下的引擎與病毒碼版本。
    scan_engine      text,
    scan_signature   text,
    scanned_at       timestamptz,
    scan_last_error  text,
    process_attempts integer NOT NULL DEFAULT 0,
    failure_kind     text,
    failure_detail   text CHECK (char_length(failure_detail) <= 2000),
    processed_at     timestamptz,
    decided_by       uuid,
    decided_at       timestamptz,
    decision_reason  text CHECK (char_length(decision_reason) <= 500),
    purge_after      timestamptz,
    purged_at        timestamptz,
    -- 退回一定要說明原因（同 report_visibility 的隱藏）。
    CONSTRAINT report_upload_reject_needs_reason
        CHECK (state <> 'rejected' OR btrim(coalesce(decision_reason, '')) <> ''),
    -- 判定感染一定要記下病毒名（稽核與管理頁紅標的依據）。
    CONSTRAINT report_upload_infected_has_signature
        CHECK (state <> 'infected' OR scan_signature IS NOT NULL)
);
-- 同一個 file_hash 同時最多一筆進行中（尚未到終態、也還沒發布）的上傳：兩個管理員同時傳同一份檔，
-- 後到的 INSERT 撞這支索引（收檔 API 轉 409），不靠應用層先查再寫的競態。
CREATE UNIQUE INDEX idx_report_upload_active_hash
    ON research.report_upload (file_hash) WHERE state IN ('quarantined', 'scanning', 'clean', 'processing', 'draft');
-- worker 依狀態認領（最舊的先處理）、管理頁依狀態分頁籤。
CREATE INDEX idx_report_upload_state ON research.report_upload (state, uploaded_at);
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
