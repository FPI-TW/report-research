# M4b 共用證據帳本地基 設計規格

日期：2026-07-14。藍圖：`docs/REPORT_GEN_REDESIGN.md` §3 Phase 0.5／Phase 2、`docs/QA_REDESIGN.md` §3-B、backlog：`docs/IMPLEMENTATION_PLAN.md` M4b。
分支：`feat/m4b-evidence-ledger`，疊於 `feat/m4a-trusted-data`（時效答案的外部來源需經帳本落地）。依賴 M0、M4a；M5／M7／M8 都依賴本里程碑。

## 目標

建立問答與研報**共用**的證據帳本資料契約：不可變 `evidence_id`、來源身分（corpus 的 report/chunk ID；外部的 URL、時間、內容雜湊）、序列化／去重／schema 驗證，以及「內部用 `evidence_id`、最後才渲染 `[n]`」的引用渲染 primitive。`qa_log` 與 `report_doc` 新增 `evidence_manifest` 欄位，寫入路徑開始落 manifest；歷史列以空 manifest 安全退化。

帳本只負責**來源身分、去重、持久化與引用渲染**；「主張是否被支持」是 M8 的責任——本階段不判斷真偽，存在引用不得被解讀為真實性。

## 非目標（YAGNI）

- 不改變既有 QA／研報的 `[n]` 生成方式（模型仍直接輸出 `[n]`）；placeholder 渲染（`[[ev:...]]`→`[n]`）是給 M5/M7 逐節生成用的 primitive，本階段不接入線上輸出流。
- 不回填歷史 `qa_log`／`report_doc` 列（NULL manifest = 空帳本語義）。
- 不做 claim→evidence 的映射欄位（M7 的 `claim_evidence`）與 grounding（M8）。
- 不做前端 UI 呈現。
- 不新增 REST 端點。

## 設計

### 1. `app/services/evidence.py`

#### 資料結構

```python
EVIDENCE_SCHEMA_VERSION = 1
Kind = Literal["corpus", "external"]

@dataclass(frozen=True)
class Evidence:
    evidence_id: str          # 內容定址、不可變（見下）
    kind: Kind
    # corpus 來源
    report_id: str | None = None
    chunk_id: str | None = None      # 現階段 context 為報告級（None）；M6/M7 可帶 chunk 級
    file_name: str | None = None
    market: str | None = None
    report_date: str | None = None   # ISO 字串
    # 外部來源
    url: str | None = None
    title: str | None = None
    source_type: str | None = None   # "web" | "exchange" | "official" | ...
    published_at: str | None = None  # ISO 字串
    retrieved_at: str | None = None  # ISO 字串（取得時間）
    content_hash: str | None = None  # sha256 hex（外部來源內容雜湊；可 None＝未知）
```

#### `evidence_id`（不可變、內容定址）

- corpus：`sha256("corpus\x00" + report_id + "\x00" + (chunk_id or ""))[:16]`
- external：`sha256("external\x00" + url + "\x00" + (content_hash or ""))[:16]`

同一來源在問答與研報、跨 session 都得到**同一個 id**（「共用帳本」的關鍵語義）；去重天然成立。id 一經寫入 manifest 永不變更。

#### `EvidenceLedger`

```python
class EvidenceLedger:
    def add_corpus(self, *, report_id, chunk_id=None, file_name=None,
                   market=None, report_date=None) -> Evidence      # 重複來源回同一 Evidence
    def add_external(self, *, url, title=None, source_type="web",
                     published_at=None, retrieved_at=None, content_hash=None) -> Evidence
    def get(self, evidence_id) -> Evidence | None
    def merge(self, other) -> None                                  # 併入去重（M7 多節組裝用）
    def to_manifest(self) -> dict      # {"schema_version": 1, "evidence": [ ... ]}
    @classmethod
    def from_manifest(cls, obj) -> "EvidenceLedger"                 # 嚴格：invalid → EvidenceValidationError
    @staticmethod
    def load(obj) -> "EvidenceLedger"  # 寬鬆讀取：None/{}/缺欄/壞形狀 → 空帳本（歷史列安全退化）
```

- `validate_manifest(obj) -> list[str]`：schema_version 支援、kind 合法、corpus 必有 `report_id`、external 必有 `http(s)` URL、`evidence_id` 與識別欄位一致（防手改）。
- 外部來源寫入契約：**只有 M4a adapter（`TrustedDataPoint`）與受控研報 Web 流程（`[EXT_SOURCES]`／`## 外部參考（網路）` 解析）可產生 external evidence**；提供 `from_trusted_point(point) -> Evidence` 與 `from_ext_source(dict, retrieved_at) -> Evidence` 兩個受控建構器，其他路徑不得自行拼 external dict。

#### 引用渲染 primitive（M5/M7 用）

```python
EV_PLACEHOLDER_RE = re.compile(r"\[\[ev:([0-9a-f]{8,64})\]\]")

def render_citations(text: str, ledger: EvidenceLedger) -> RenderedCitations:
    # 依「首次出現序」分配 1..N；同一 evidence 全文恆同號；
    # 未知 id 的 placeholder 移除並計數（不得漏內部 token 到輸出）。
    # 回 (rendered_text, ordered: list[Evidence], number_of: dict[evidence_id, int], n_unknown)
```

多節重編穩定性語義：模型逐節只寫 `[[ev:<id>]]`；不論節次如何重排／重寫，`render_citations` 對最終組裝文只跑一次，`[n]` 由首次出現序決定且全文一致——**編號穩定性來自 id 不變 + 單次最終渲染**，這正是驗收「`[n]` 在多節重編後仍穩定」的機制。

「每個 claim／KPI／chart 只能指向已分配 evidence」的資料契約由 `render_citations` 的 unknown 計數與 `validate_manifest` 共同把關；M7 組裝時 unknown 必須為 0（本階段先提供 primitive 與測試）。

### 2. Schema（`db/schema.sql`，冪等）

```sql
-- M4b：證據帳本 manifest（{"schema_version":1,"evidence":[...]}；NULL＝舊列，空帳本語義）
ALTER TABLE research.qa_log     ADD COLUMN IF NOT EXISTS evidence_manifest jsonb;
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS evidence_manifest jsonb;
```

### 3. 寫入路徑接線

- `answer.py::_log_qa(..., evidence_manifest: dict | None = None)`：INSERT 增列（None → NULL）。
  - 主 RAG 路徑：由 `sources`（corpus）＋ `ext_sources`（external，經 `from_ext_source`）建帳本。
  - M4a 時效答案路徑：`from_trusted_point(point)` 建帳本。
  - 婉拒／無脈絡等終端路徑：不帶 manifest（NULL）。
- `report.py::persist_report_doc(..., evidence_manifest)`：由 `sources`（corpus）＋ `parse_external_refs(markdown)`（解析 `## 外部參考（網路）` 節的 `- [標題](網址)` 行，確定性、可測）建帳本。
- 讀取路徑本階段不變（`history_item`、`fetch_report_doc` 不新增欄位輸出）；M5/M7 需要時只透過 `EvidenceLedger.load()` 取用，**不需知道資料庫欄位細節**。

## 測試

`tests/test_evidence.py`：

- corpus／external 序列化↔反序列化 round-trip；`schema_version` 寫入。
- 重複來源去重（同 report_id 兩次 add → 同一 Evidence、len 不變；同 URL 同 hash 亦然）。
- 舊資料／歷史列：`load(None)`、`load({})`、`load(壞形狀)` → 空帳本不拋錯；`from_manifest(壞形狀)` → 明確錯誤。
- `evidence_id` 決定性：跨 ledger、跨順序同 id；id 篡改被 `validate_manifest` 抓到。
- `render_citations`：首次出現序編號、同 id 恆同號、多節重排後單次渲染編號穩定、未知 id 移除且計數。
- `from_trusted_point`／`from_ext_source` 欄位映射。

接線測試：

- `tests/test_answer.py` 增：主 RAG 路徑 `_log_qa` 收到含 corpus evidence 的 manifest；時效答案含 external evidence。
- `tests/test_report.py` 增：`persist_report_doc` INSERT 含 manifest；`parse_external_refs` 解析正確；無外部參考節 → 純 corpus manifest。

## 驗收清單

- [ ] corpus、外部、重複來源與舊資料（空 manifest）的序列化／反序列化測試全綠。
- [ ] `[n]` 渲染在多節重編後穩定（單次最終渲染 + id 不變）。
- [ ] M5/M7 只需 `evidence.py` 公開介面即可讀寫帳本（schema 欄位細節封裝於 `_log_qa`／`persist_report_doc`）。
- [ ] `make schema` 冪等；歷史列讀取零回歸。
- [ ] Commit：`feat(證據): 建立共用 evidence ledger`。
