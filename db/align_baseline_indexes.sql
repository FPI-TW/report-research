-- 既有庫對齊 Alembic baseline（revision 0001）的一次性修正。**stamp 前手動執行**，
-- 執行後 `make schema-check` 必須零 drift，才可 `make schema-stamp-baseline`。
--
-- 為什麼會有這些差異：導入 Alembic 前 db/schema.sql 只靠 `CREATE ... IF NOT EXISTS`，
-- 對既有庫既刪不掉東西、也改不了同名物件的定義。2026-10-05 的 drift 檢查在生產庫與
-- devdb 都找到下面三項（staging 由生產 pg_dump 還原，預期相同；以檢查結果為準）。
--
-- 冪等：重跑不會出錯、也不會動到已經正確的物件。兩支 DROP 用 CONCURRENTLY（不鎖寫入），
-- 所以**不可**包交易（不要加 psql -1）。
-- 套用：docker exec -i report-mark-postgres psql -U postgres -d research -v ON_ERROR_STOP=1 \
--         < db/align_baseline_indexes.sql

-- 1) baseline 已刪、既有庫還留著的重複索引（各被同表的 UNIQUE 約束最左前綴完整覆蓋，
--    理由見 db/schema.sql 的 report_signal／report_takeaway 段）
DROP INDEX CONCURRENTLY IF EXISTS research.idx_report_signal_report;
DROP INDEX CONCURRENTLY IF EXISTS research.idx_report_takeaway_report;

-- 2) idx_qa_log_conversation：既有庫是舊定義 (conversation_id, created_at)，baseline 是運算式
--    ((COALESCE(conversation_id, id)), created_at)。對話串查詢以運算式查，舊定義用不上。
--    只在定義不對時才刪（重跑時不得刪掉已經正確的索引）。qa_log 只有數百列，一般 DROP／CREATE
--    的鎖只有毫秒級，不值得為它做 CONCURRENTLY 的換名舞步。
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_indexes
        WHERE schemaname = 'research' AND indexname = 'idx_qa_log_conversation'
          AND indexdef NOT LIKE '%COALESCE(conversation_id, id)%'
    ) THEN
        DROP INDEX research.idx_qa_log_conversation;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_qa_log_conversation
    ON research.qa_log ((COALESCE(conversation_id, id)), created_at);
