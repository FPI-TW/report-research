"""功能旗標管理（`/api/admin/flags*`；Admin v2 Flags lane）——Wave 0 只建空 router，端點由 lane 補。

授權：router 層掛 `authz.require_admin`＋`ops.read`（看旗標）；寫入另加 `ops.operate`（可授予）＋
`authz.require_elevated`，同交易寫稽核 `flag.update`，寫完 `feature_flags.invalidate()`（使用者定案 11、12）。
讀取核心在 `app/services/feature_flags.py`：實際值＝環境變數上限 AND DB 覆寫；安全閘門不是旗標。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from web import authz

router = APIRouter(dependencies=[Depends(authz.require_admin), Depends(authz.require_scope("ops.read"))])
