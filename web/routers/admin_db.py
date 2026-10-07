"""進階 DB 與事件趨勢（`/api/admin/db/*`；Admin v2 DB lane）——Wave 0 只建空 router，端點由 lane 補。

授權：router 層掛 `authz.require_admin`＋`ops.read`。即時快照只查系統目錄、受 statement_timeout 約束；
趨勢讀 `research.db_stat_snapshot`（`scripts/db_snapshot.py`，逐時 30 天、每日 400 天）。`pg_stat_statements`
不寫進 migration，頁面在執行期偵測，不可用時顯示「未啟用或權限不足」、查詢文字截斷（使用者定案 13）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from web import authz

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("ops.read"))])
