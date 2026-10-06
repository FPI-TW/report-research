"""管理後台 CSV 匯出的共用格式：防公式注入、UTF-8 BOM、串流回應與檔名。

- **公式注入**：試算表會把 `=`、`+`、`-`、`@` 開頭（以及 tab／CR 開頭）的儲存格當公式執行。字串值以這些字元
  開頭時前面加一個 `'`（OWASP 的建議做法），試算表就當純文字顯示。數字型別（int／float）不加——負數仍是數字。
- **BOM**：Excel 開 UTF-8 CSV 沒有 BOM 會把中文顯示成亂碼。
- **串流**：資料先由路由層一次取完（有筆數上限）並寫完稽核，這裡只負責逐批編碼送出，不在送出途中再查 DB。
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Iterator, Sequence
from datetime import date, datetime, timedelta, timezone

from fastapi.responses import StreamingResponse

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")
BOM = "﻿"
_CHUNK_ROWS = 500
# 檔名的日期用台北時間（UTC+8，無日光節約）：管理員看到的是「今天」的匯出。
_TZ = timezone(timedelta(hours=8))


class CsvResponse(StreamingResponse):
    """讓 OpenAPI 標示 `text/csv`（`scripts/gen_admin_client.py` 依此產生下載網址而不是 JSON client）。"""

    media_type = "text/csv"


def safe_cell(value) -> str:
    """一個儲存格的文字；字串以公式字元開頭時加 `'` 前綴。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    text = str(value)
    return "'" + text if text.startswith(FORMULA_PREFIXES) else text


def _encode(columns: Sequence[str], rows: Iterable[dict]) -> Iterator[bytes]:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    buf.write(BOM)
    writer.writerow(columns)
    pending = 0
    for row in rows:
        writer.writerow([safe_cell(row.get(c)) for c in columns])
        pending += 1
        if pending >= _CHUNK_ROWS:
            yield buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate()
            pending = 0
    if buf.tell():
        yield buf.getvalue().encode("utf-8")


def filename(kind: str, now: datetime | None = None) -> str:
    day = (now or datetime.now(timezone.utc)).astimezone(_TZ).strftime("%Y%m%d")
    return f"report-mark-{kind}-{day}.csv"


def csv_response(kind: str, columns: Sequence[str], rows: Sequence[dict], *, truncated: bool,
                 now: datetime | None = None) -> CsvResponse:
    return CsvResponse(
        _encode(columns, rows),
        headers={
            "Content-Disposition": f'attachment; filename="{filename(kind, now)}"',
            "Cache-Control": "no-store",
            "X-Export-Rows": str(len(rows)),
            "X-Export-Truncated": "true" if truncated else "false",
        },
    )
