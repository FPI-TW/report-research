"""Security Operations（`/api/admin/security/*`；Admin v2 Security lane）——Wave 0 只建空 router，端點由 lane 補。

授權：router 層掛 `authz.require_admin`＋`audit.read`（高風險操作時間線、登入事件、TOTP 採用率）。
session 清單與撤銷單一 session 另加 `accounts.manage`，撤銷再加 `authz.require_elevated`；服務層已在
`app/services/accounts.py`（`list_sessions`、`admin_revoke_session`，同交易寫稽核 `session.admin_revoke`）。
登入事件經 `accounts.record_auth_event` 寫 `research.auth_event`（不存帳號名稱，刪帳時保留、至少 365 天）。
不做帳號鎖定與 app 層 IP 封鎖（使用者定案 8），既有的每 IP 失敗限流（`web/auth.py`）保留不動。
`/healthz/security`（本機直連、狀態型告警）在 `web/routers/health.py`。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from web import authz

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("audit.read"))])
