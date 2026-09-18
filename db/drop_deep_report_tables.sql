-- 深度研報生成功能已移除（2026-09）。db/schema.sql 只 CREATE IF NOT EXISTS，對既有庫
-- 是 no-op，所以四張已無程式讀寫的表要由人手動執行本檔清掉。
--
-- 順序依相依：report_section 以 FK 指向 report_run（ON DELETE CASCADE）；
-- report_rendition / report_doc 之間是 plain uuid 反向連結、無 FK，先刪葉再刪根。
-- 執行前先確認備份不需要這四張（scripts/db_backup.sh 從未涵蓋它們）。
--
--   psql "$REPORT_MARK_DB_URL" -f db/drop_deep_report_tables.sql

DROP TABLE IF EXISTS research.report_rendition;
DROP TABLE IF EXISTS research.report_section;
DROP TABLE IF EXISTS research.report_run;
DROP TABLE IF EXISTS research.report_doc;
