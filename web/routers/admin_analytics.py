"""Analytics（`/api/admin/analytics/*`；Admin v2 Analytics lane）——Wave 0 只建空 router，端點由 lane 補。

授權：router 層掛 `authz.require_admin`＋`analytics.read`（管理員預設 scope，不可授予）；每條新端點照
`tests/test_authz.py` 的結構檢查自動繼承。**只出彙總**：標的、研報、市場這類可能指向個人的格子，不重複人數
少於 `ANALYTICS_MIN_USERS`（3）一律不顯示；沒有任何依使用者拆分的視圖；問答原文仍只能走待複核的
`qa_content.read` 逐筆路徑（使用者定案 2、3）。資料來源：最近 `ANALYTICS_LIVE_WINDOW_DAYS` 天的 qa_log、
`usage_daily`、`analytics_daily`（`scripts/analytics_rollup.py`）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from web import authz

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("analytics.read"))])
