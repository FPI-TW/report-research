"""research_report 加文件類型 doc_type（由 is_research 回填），is_research 保留並以 CHECK 同步

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-09
"""
# 慣例（tests/test_schema_migrations.py 守）：只寫 SQL，放在 UPGRADE_SQL；不用 autogenerate。
# 改 CHECK 約束要同步重生 db/expected_constraints.txt。
#
# 布林 is_research（false＝使用者端完全看不到）換成「文件類型＋每種類型啟用哪些功能」：類型存在這一欄，
# 功能對照在 profile 的 [doc_types]（app/profile.py、app/services/doc_types.py）。類型詞彙由 profile 決定，
# 所以這裡只限形狀、不列舉（新增類型不必寫 revision）。
#
# 回填：is_research true／NULL → 'research'，false → 'non_research'（tagging.DOC_TYPE_NON_RESEARCH：
# 標註器判為非研究檔，所有功能關閉，與改動前的 is_research=false 同語意）。baseline 已把 is_research
# 收斂成 NOT NULL，NULL 只是防呆：DEFAULT 'research' 本來就涵蓋它。
#
# is_research 刻意保留（v1 不刪）：還沒升級的程式（部署時 web 與 sync 不會同時重啟）、從舊備份還原的
# 資料都只認得它。兩欄由 CHECK 綁死成 is_research = (doc_type = 'research')——寫入路徑由 doc_type 推得
# （doc_types.legacy_is_research），舊版程式只會寫 is_research=true（入庫閘擋掉了非研究檔），落到
# DEFAULT 'research' 一樣通過。
#
# 鎖與時間：ADD COLUMN 帶常數 DEFAULT 在 PG11+ 只改目錄、不重寫表；回填只動 is_research=false 的列
# （實際上 0 列，入庫閘從不寫入非研究檔）；兩個 CHECK 各掃一次 heap 驗證；ANALYZE 只取樣 doc_type 一欄。
# 整段在同一筆交易、持有 ACCESS EXCLUSIVE 直到 commit；測試環境約 1.5 萬列的實測見 PR 說明（秒級以下）。
# 拿不到鎖時由 migration 連線的 lock_timeout（10s，app/services/schema_migrations.py）失敗重來，
# 不會排在長交易後面把所有讀取一起卡住。
#
# ANALYZE 是必要的，不是順手：新欄位在下一次 autovacuum analyze 之前沒有統計，planner 會把
# `r.doc_type IN ('research')` 估成預設的低選擇率，檢索（HNSW）、雷達與總覽的計畫可能因此改變。
from alembic import op

from app.services.schema_migrations import run_sql_script

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

UPGRADE_SQL = r"""
ALTER TABLE research.research_report
    ADD COLUMN doc_type text NOT NULL DEFAULT 'research';

UPDATE research.research_report SET doc_type = 'non_research' WHERE is_research IS FALSE;

ALTER TABLE research.research_report
    ADD CONSTRAINT research_report_doc_type_check
        CHECK (doc_type ~ '^[a-z][a-z0-9_]{0,31}$'),
    ADD CONSTRAINT research_report_doc_type_legacy_sync
        CHECK (is_research = (doc_type = 'research'));

ANALYZE research.research_report (doc_type);
"""


def upgrade() -> None:
    run_sql_script(op.get_bind(), UPGRADE_SQL)


def downgrade() -> None:
    raise NotImplementedError("只往前：回退請寫一個新的 revision")
