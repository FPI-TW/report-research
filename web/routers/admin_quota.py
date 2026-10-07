"""個人配額管理（`/api/admin/quota*`；Admin v2 Quota lane）——Wave 0 只建空 router，端點由 lane 補。

授權：router 層掛 `authz.require_admin`＋`accounts.manage`；寫入另加 `authz.require_elevated`，設成不限
（`user_quota.daily_limit` NULL）只有 super admin（規則寫在服務層 `app/services/quota.py`，CLI 也受約束）；
同交易寫稽核 `quota.update`。配額值：`QUOTA_ASK_DAILY`（100）、`QUOTA_EXPORT_DAILY`（20）、`UPLOAD_DAILY_QUOTA`
（30），先影子模式（`QUOTA_ENFORCE`＋旗標 `quota.enforce`，使用者定案 5）。計數在 `usage_counter`，不 COUNT qa_log。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from web import authz

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("accounts.manage"))])
