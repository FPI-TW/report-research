"""`GET /metrics`：Prometheus 文字格式的應用層監控訊號（免登入，但只回答允許的對端；其餘 404）。

指標定義、標籤規則、存取控制與多行程的限制都在 `web/metrics.py` 的模組 docstring；指標清單與告警規則範例在
`docs/CONTAINER_DEPLOYMENT.md`「監控對接」。路徑必須與 `web/server.py` 的 `_AUTH_ALLOWLIST` 成對存在。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from web import metrics

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def prometheus_metrics(request: Request) -> Response:
    if not metrics.access_allowed(request):
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    await metrics.refresh_db_signals()
    return Response(metrics.render(), media_type=metrics.CONTENT_TYPE)
