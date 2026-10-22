# 廷豐智能研報 對外 API 說明（v1）

以 HTTP GET 檢索研報、取得研報原檔的下載連結。回應一律是 JSON（UTF-8）。

- **站台網址**：`{BASE_URL}`，以下路徑都接在它後面。
- **版本**：路徑帶 `/external/v1/`。v1 之內只會新增端點或欄位，不會移除欄位或改變既有欄位的意思；請忽略看不懂的新欄位。
- **時間格式**：日期是 `YYYY-MM-DD`；時刻是 ISO 8601 UTC，例如 `2026-10-22T08:30:00Z`。

## 快速開始

```bash
export API_KEY='<管理員交付的金鑰>'
curl -sS -H "Authorization: Bearer $API_KEY" \
  "{BASE_URL}/external/v1/search?q=AI%20伺服器&limit=5"
```

## 認證

每個請求都要帶這個標頭：

```
Authorization: Bearer <API 金鑰>
```

- 金鑰以 `rmk_` 開頭，由管理員建立後交付，**只會顯示一次**；遺失時請管理員輪替（換發新金鑰，舊金鑰立即失效）。
- 金鑰等同密碼：只放在伺服器端（環境變數或祕密管理服務），不要寫進網頁前端、App、網址或 log。
- 管理員停用或輪替金鑰後，下一個請求就會收到 401。
- 這組 API 不使用 cookie，與網站的登入狀態無關。

## 權限與資料範圍

每把金鑰有兩項設定，由管理員決定：

| 設定 | 說明 |
|---|---|
| 權限 | 能呼叫哪些端點：`search`（研報檢索）、`report.file`（研報原檔連結）。缺少權限回 403 |
| 資料範圍 | 能看到哪些研報：依市場（必設）、券商、報告類型、商品類型限定。範圍外的研報對這把金鑰而言等於不存在 |

檢索時帶的篩選條件只能在資料範圍內**再縮小**、不能擴大：篩選值不在資料範圍內時，回傳空結果而不是錯誤。

## 限流與每日額度

| 限制 | 超過時 | 何時恢復 |
|---|---|---|
| 每分鐘請求數 | 429 `api_rate_limited` | 等 `Retry-After` 標頭的秒數 |
| 每日請求數（台北時間的日曆日） | 429 `api_quota_exceeded` | 台北時間 0 點（UTC 16:00）重置，`Retry-After` 是距重置的秒數 |

上限依金鑰而定，請向管理員確認。通過每分鐘限流的請求不論成功與否都計入每日額度。收到 429 請依 `Retry-After` 等待後再送，不要立刻重送。

## 端點

### GET /external/v1/search

以自然語言或關鍵字檢索研報，回傳符合的研報清單。結果是研報層級的資訊，不含內文段落；要看內文請取原檔。

需要權限：`search`

| 參數 | 位置 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `q` | query | 是 | — | 查詢文字，1–500 字 |
| `limit` | query | 否 | `10` | 每頁篇數，1–20 |
| `page` | query | 否 | `1` | 頁碼，從 1 起算；超過最後一頁回傳空的 `results` |
| `sort` | query | 否 | `relevance` | 排序：`relevance`（相關度）、`date_desc`（新到舊）、`date_asc`（舊到新） |
| `market` | query | 否 | — | 只看某個市場，例如 `TW`、`US` |
| `source` | query | 否 | — | 只看某家券商，值是券商代碼（同回應裡的 `source`），例如 `yuanta`、`kgi` |
| `report_type` | query | 否 | — | 只看某種報告類型，值同回應裡的 `report_type`，例如 `速報`、`報告` |
| `instrument_type` | query | 否 | — | 只看某種商品類型，例如 `equity`、`etf` |

篩選參數每個只能帶一個值。

回應範例：

```json
{
  "query": "AI 伺服器",
  "total": 37,
  "page": 1,
  "limit": 5,
  "results": [
    {
      "report_id": "3f6c1e2a-8b4d-4c1e-9a7f-2d5b6c7e8f90",
      "title": "伺服器供應鏈：GB300 出貨上修",
      "file_name": "20261020_yuanta_伺服器產業.pdf",
      "market": "TW",
      "source": "yuanta",
      "source_name": "元大",
      "report_date": "2026-10-20",
      "report_type": "報告",
      "instrument_types": ["equity"],
      "summary": "上修 2027 年 AI 伺服器出貨預估……",
      "score": 0.0312,
      "file_url": "https://…",
      "file_url_expires_at": "2026-10-22T08:40:00Z"
    }
  ]
}
```

| 欄位 | 說明 |
|---|---|
| `query` | 這次的查詢文字 |
| `total` | 符合的研報總篇數 |
| `page`、`limit` | 這次的頁碼與每頁篇數 |
| `results[].report_id` | 研報 ID，取原檔連結時使用 |
| `results[].title` | 標題，可能是 `null`（這時顯示 `file_name`） |
| `results[].file_name` | 原始檔名 |
| `results[].market` | 市場代碼 |
| `results[].source` | 券商代碼（篩選 `source` 用這個值） |
| `results[].source_name` | 券商的顯示名稱 |
| `results[].report_date` | 報告日期，可能是 `null` |
| `results[].report_type` | 報告類型，可能是 `null` |
| `results[].instrument_types` | 商品類型清單，可能是 `null` |
| `results[].summary` | 摘要，可能是 `null` |
| `results[].score` | 相關度分數，越大越相關；只能在同一次查詢內比較 |
| `results[].file_url` | 原檔下載連結（短效）。只有金鑰有 `report.file` 權限時才有這個欄位；暫時無法產生時是 `null`，可改用下一個端點再取一次 |
| `results[].file_url_expires_at` | `file_url` 的到期時刻 |

### GET /external/v1/reports/{report_id}/file-url

取得一篇研報原檔（PDF 或 Word）的短效下載連結。

需要權限：`report.file`

| 參數 | 位置 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `report_id` | path | 是 | — | 研報 ID（檢索結果的 `report_id`） |

回應範例：

```json
{
  "report_id": "3f6c1e2a-8b4d-4c1e-9a7f-2d5b6c7e8f90",
  "file_url": "https://…",
  "expires_at": "2026-10-22T08:40:00Z"
}
```

| 欄位 | 說明 |
|---|---|
| `report_id` | 研報 ID |
| `file_url` | 原檔下載連結，直接 GET 即可下載，不需要帶金鑰 |
| `expires_at` | 連結到期時刻（有效期由站台設定，預設 10 分鐘），過期請重新呼叫這個端點 |

下載連結持有即可下載：請在需要時才取、拿到就用，不要長期保存或轉給第三方。

## 錯誤

錯誤回應的格式：

```json
{ "detail": "API 金鑰無效", "code": "api_key_invalid", "request_id": "5b0e…" }
```

程式請以 `code` 判斷（固定不變）；`detail` 是給人看的說明，文字可能調整。回報問題時請附上 `request_id`（回應標頭 `x-request-id` 也有同一個值）。

| HTTP | `code` | 意思 | 建議處理 |
|---|---|---|---|
| 401 | `api_key_missing` | 沒帶 `Authorization` 標頭 | 補上金鑰 |
| 401 | `api_key_invalid` | 金鑰格式不對、不存在、已停用或已被輪替 | 向管理員確認金鑰 |
| 403 | `api_scope_missing` | 金鑰沒有這個端點需要的權限 | 向管理員申請權限 |
| 404 | `report_not_found` | 查無此研報（含不在資料範圍內、`report_id` 格式不對） | 確認 `report_id` 來自檢索結果 |
| 404 | `original_not_found` | 這篇研報沒有可提供的原檔 | 不需重試 |
| 422 | `validation_error` | 參數不符規定（例如 `limit` 大於 20），`detail` 是逐欄的錯誤清單 | 修正參數 |
| 429 | `api_rate_limited` | 超過每分鐘上限 | 依 `Retry-After` 等待後重試 |
| 429 | `api_quota_exceeded` | 今日額度已用完 | 台北時間 0 點後再試 |
| 503 | `api_auth_unavailable` | 認證服務暫時無法使用 | 稍後重試 |
| 503 | `api_client_misconfigured` | 金鑰的設定有誤 | 聯絡管理員 |
| 503 | `original_unavailable` | 原檔暫時無法提供 | 稍後重試 |
| 503 | `unavailable` | 服務暫時無法使用 | 稍後重試 |

重試原則：429 與 503 可以重試（依 `Retry-After`，沒有就逐次拉長間隔）；401、403、404、422 重試也不會成功。網路閘道層的錯誤（例如 502、504）回應可能不是 JSON，請一併當成可重試的暫時錯誤處理。

## 範例：Python

```python
import os
import time

import requests

BASE_URL = "{BASE_URL}"
HEADERS = {"Authorization": f"Bearer {os.environ['API_KEY']}"}


def search(q, **params):
    while True:
        r = requests.get(f"{BASE_URL}/external/v1/search", params={"q": q, **params}, headers=HEADERS, timeout=30)
        if r.status_code == 429 and r.json().get("code") == "api_rate_limited":
            time.sleep(int(r.headers.get("Retry-After", "5")))
            continue
        r.raise_for_status()
        return r.json()


def file_url(report_id):
    r = requests.get(f"{BASE_URL}/external/v1/reports/{report_id}/file-url", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["file_url"]


for item in search("AI 伺服器", limit=5)["results"]:
    print(item["report_date"], item["title"] or item["file_name"])
```
