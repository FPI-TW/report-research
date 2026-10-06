"""統一錯誤格式：`{"detail": ..., "code": "...", "request_id": "..."}`。

`detail` 保持 FastAPI 原本的意義（多半是給人看的中文訊息；422 是欄位錯誤清單），既有前端
照讀不誤；新增的 `code` 是給程式判斷的穩定字串（例如 `elevation_required`、`already_running`），
`request_id` 對得上 `RequestLogMiddleware` 寫進 journal 的那一行，回報問題時直接拿它查 log。

用法：
- 一般情況照舊 `raise HTTPException(status_code, detail)`，code 由狀態碼推得（`DEFAULT_CODES`）。
- 需要特定 code 時 `raise AppError(status, code, message)`。要多帶給程式判斷的欄位（例如上傳重複時的
  `file_hash`）用 `extra=`：併進回應的頂層，但蓋不掉 `detail`／`code`／`request_id`。
- middleware 自己回的 JSON（認證 401／503、CSRF 403）用 `error_response()`，形狀一致。
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import request_context

DEFAULT_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "bad_gateway",
    503: "unavailable",
    504: "timeout",
}


class AppError(Exception):
    """帶穩定 code 的 HTTP 錯誤。message 是給人看的中文（放進 detail）。"""

    def __init__(self, status_code: int, code: str, message: str, *, headers: dict[str, str] | None = None,
                 extra: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers
        self.extra = extra


def error_body(status_code: int, detail, code: str | None = None) -> dict:
    return {
        "detail": detail,
        "code": code or DEFAULT_CODES.get(status_code, "error"),
        "request_id": request_context.current_request_id(),
    }


def error_response(status_code: int, detail, code: str | None = None,
                   headers: dict[str, str] | None = None, extra: dict | None = None) -> JSONResponse:
    body = {**(extra or {}), **error_body(status_code, detail, code)}
    return JSONResponse(body, status_code=status_code, headers=headers)


async def _app_error(_request: Request, exc: AppError) -> JSONResponse:
    return error_response(exc.status_code, exc.message, exc.code, exc.headers, exc.extra)


async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return error_response(exc.status_code, exc.detail, headers=getattr(exc, "headers", None))


async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    # detail 維持 FastAPI 原本的欄位錯誤清單（jsonable：exc.errors() 可能含不可序列化的 ctx）。
    from fastapi.encoders import jsonable_encoder

    return error_response(422, jsonable_encoder(exc.errors()))


def install(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
