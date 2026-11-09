# 廷豐智能研報 對外 API 說明（v1）

檢索研報、取得研報原檔的下載連結，以及上傳研報 PDF。回應一律是 JSON（UTF-8）。

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
| 權限 | 能呼叫哪些端點：`search`（研報檢索）、`report.file`（研報原檔連結）、`report.upload`（上傳研報與查上傳狀態）。缺少權限回 403 |
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

### POST /external/v1/uploads

上傳一份研報 PDF。檔案先經病毒掃描與內容檢查、整理入庫，再由管理員審核發布；發布之前檢索查不到它。回應是這筆上傳的紀錄，之後以 `upload_id` 查進度。

需要權限：`report.upload`

| 參數 | 位置 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `filename` | query | 是 | — | 原始檔名，副檔名必須是 `.pdf`；檔名裡的日期與券商會用來整理研報，請保留原樣 |
| `last_modified` | query | 否 | — | 檔案的最後修改時刻（Unix 毫秒），檔名沒有日期時用來推報告日期 |

請求本體是 PDF 檔案的位元組本身（不是 multipart 表單），標頭帶 `Content-Type: application/pdf`。單檔上限由站台設定（預設 25 MB），超過回 413。

```bash
curl -sS -X POST -H "Authorization: Bearer $API_KEY" -H "Content-Type: application/pdf" \
  --data-binary @20261020_券商_伺服器產業.pdf \
  "{BASE_URL}/external/v1/uploads?filename=20261020_券商_伺服器產業.pdf"
```

回應（202）：

```json
{
  "upload_id": "8a1f0c2e-5b7d-4e3a-9c61-0f2d4b6a8e10",
  "file_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "file_name": "20261020_券商_伺服器產業.pdf",
  "size_bytes": 1832410,
  "status": "processing",
  "uploaded_at": "2026-10-22T08:30:00Z",
  "updated_at": "2026-10-22T08:30:00Z"
}
```

| 欄位 | 說明 |
|---|---|
| `upload_id` | 上傳 ID，查進度時使用 |
| `file_hash` | 檔案內容的 SHA-256；同一份內容不會收第二次 |
| `file_name` | 整理後的檔名 |
| `size_bytes` | 檔案大小（位元組） |
| `status` | 進度：`processing`（掃描與整理中）、`pending_review`（等管理員審核）、`published`（已發布，可以檢索到）、`rejected`（未通過檢查或審核） |
| `uploaded_at` | 收到的時刻 |
| `updated_at` | `status` 最後變動的時刻 |

同一份檔案（內容相同）已經收過，回 409 `upload_duplicate`；不必重送。每日上傳份數另有上限（台北時間的日曆日），超過回 429 `upload_quota_exceeded`。

### GET /external/v1/uploads/{upload_id}

查一筆上傳目前的進度。只查得到同一把金鑰上傳的紀錄。

需要權限：`report.upload`

| 參數 | 位置 | 必填 | 預設 | 說明 |
|---|---|---|---|---|
| `upload_id` | path | 是 | — | 上傳 ID（上傳回應的 `upload_id`） |

回應的欄位與上傳回應相同（`upload_id`、`file_hash`、`file_name`、`size_bytes`、`status`、`uploaded_at`、`updated_at`）。進度通常在幾分鐘內走到 `pending_review`，之後等管理員審核；請以分鐘為間隔查詢，不要密集輪詢。

## 錯誤

錯誤回應的格式：

```json
{ "detail": "API 金鑰無效", "code": "api_key_invalid", "request_id": "5b0e…" }
```

程式請以 `code` 判斷（固定不變）；`detail` 是給人看的說明，文字可能調整。回報問題時請附上 `request_id`（回應標頭 `x-request-id` 也有同一個值）。

| HTTP | `code` | 意思 | 建議處理 |
|---|---|---|---|
| 400 | `invalid_filename` | 檔名不合法（清掉路徑與控制字元後沒有主檔名） | 修正 `filename` |
| 400 | `invalid_request` | 請求格式不對（例如 `Content-Length` 不是數字） | 修正請求 |
| 401 | `api_key_missing` | 沒帶 `Authorization` 標頭 | 補上金鑰 |
| 401 | `api_key_invalid` | 金鑰格式不對、不存在、已停用或已被輪替 | 向管理員確認金鑰 |
| 403 | `api_scope_missing` | 金鑰沒有這個端點需要的權限 | 向管理員申請權限 |
| 404 | `report_not_found` | 查無此研報（含不在資料範圍內、`report_id` 格式不對） | 確認 `report_id` 來自檢索結果 |
| 404 | `original_not_found` | 這篇研報沒有可提供的原檔 | 不需重試 |
| 404 | `upload_not_found` | 查無此上傳（含不是這把金鑰上傳的、`upload_id` 格式不對） | 確認 `upload_id` 來自上傳回應 |
| 409 | `upload_duplicate` | 同一份檔案已經收過 | 不需重送 |
| 413 | `upload_too_large` | 檔案超過單檔上限 | 不需重試 |
| 415 | `upload_not_pdf` | 不是 PDF（副檔名、`Content-Type` 或檔案內容不符，或檔案是空的） | 改送 PDF |
| 422 | `upload_rejected` | 這份檔案先前已被拒收 | 不需重試 |
| 422 | `validation_error` | 參數不符規定（例如 `limit` 大於 20），`detail` 是逐欄的錯誤清單 | 修正參數 |
| 429 | `api_rate_limited` | 超過每分鐘上限 | 依 `Retry-After` 等待後重試 |
| 429 | `api_quota_exceeded` | 今日額度已用完 | 台北時間 0 點後再試 |
| 429 | `upload_quota_exceeded` | 今日上傳份數已達上限 | 台北時間 0 點後再試 |
| 503 | `api_auth_unavailable` | 認證服務暫時無法使用 | 稍後重試 |
| 503 | `api_client_misconfigured` | 金鑰的設定有誤 | 聯絡管理員 |
| 503 | `original_unavailable` | 原檔暫時無法提供 | 稍後重試 |
| 503 | `upload_busy` | 站台處理中的上傳已滿 | 稍後重試 |
| 503 | `upload_unavailable` | 上傳暫時無法使用（站台未開放上傳或暫停收件） | 稍後重試 |
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


def upload(path):
    with open(path, "rb") as fh:
        r = requests.post(f"{BASE_URL}/external/v1/uploads", params={"filename": os.path.basename(path)},
                          data=fh, headers={**HEADERS, "Content-Type": "application/pdf"}, timeout=120)
    if r.status_code == 409:
        return None  # 已經收過
    r.raise_for_status()
    return r.json()["upload_id"]


for item in search("AI 伺服器", limit=5)["results"]:
    print(item["report_date"], item["title"] or item["file_name"])
```
