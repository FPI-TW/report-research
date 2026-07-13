# M2 Rerank 接入 — 設計 spec

> 里程碑 M2（`docs/IMPLEMENTATION_PLAN.md:88-101`）。藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 1（reranker 段）、`QA_REDESIGN.md`。依賴 M0（`retrieve_context` 插入點就緒）＋ M1（eval 基準線量增益/防回歸）。分支 `feat/m2-rerank`（疊於 M1）。

## Goal

在既有 dense+lexical 融合檢索後、選篇之前，加入 **BGE cross-encoder 重排**（`BAAI/bge-reranker-v2-m3`），把「進脈絡的片段更準」——直接拉高 Context Precision、間接穩住 Faithfulness。問答與研報兩路經 M0 的 `retrieve_context` **單點插入即共用**。零新依賴（FlagEmbedding 1.4.0 已裝、CPU torch）。以 M1 eval 對 `baseline-m0.json` 量前/後增益。

## 決策（已與使用者確認）

1. **排序整合＝同 tier 內重排**：保留 `tier`（2=完整詞組命中 > 1=全詞命中 > 0=純語意）為**硬性主鍵不變**（「字面命中永遠在語意之上」）；rerank 分數 sigmoid 正規化到 [0,1] 後**覆蓋被重排候選的 `fused`**，成為同 tier 內的相關度信號。多數召回為 tier 0（語意），故實際大量重排語意群。`select_reports`（M0 純函式）**完全不改**——其 `(best_tier, relevance_band(best_fused), recency, best_fused, report_id)` 排序鍵自然變成「tier 為主 → 同 tier 內 rerank band → 新近度 → rerank 分」。
2. **上線姿態＝預設開、保守候選上限**：問答重排 top~50（+~4-6s，相對生成 ~90s 為小字）、研報 top~120（已序列化於 `REPORT_SEMAPHORE`）。候選上限與開關 env 化；**fail-open**（模型載入/推論失敗或旗標關 → 跳過重排、回原融合序）。
3. **候選上限之外的尾段保 recall**：只對前 `top_m` 個候選（依現行 `(tier, fused)` 序）重排；`top_m` 之後的尾段候選**保留於 `scored`**（守 M2 驗收「Context Recall 不下降」），其 `fused` 壓縮到**嚴格低於最低重排分**的區間、保留相對序——確保 `select_reports` 仍能觸及僅由尾段代表的研報，又不會反超重排結果（同 tier 內）。

## 非目標（Out of Scope）

- 不改 `select_reports`／`build_context` 選篇邏輯（M6 的 MMR 才動）。
- 不改檢索頁分頁 `rank_reports` 路徑（不經 `retrieve_context`）。
- 不做 query planner / 多子查詢（研報藍圖 Phase 1 的另一半，屬 M6）。
- 不引入 rerank 結果快取（query+passage 相依、YAGNI）。
- 不改 `hybrid_search` 的融合/tier 邏輯本身。

## 資料流（`retrieve_context` 內）

```
embed_query_cached(question) → qvec
hybrid_search(session, question, qvec, k, dense_scan, **filters) → scored=[(tier, fused, ChunkRow), ...]  # ~數百候選，(tier,fused) 降序
  ── if rerank_top_m > 0 (fail-open) ──
     rerank_scored(question, scored, top_m=rerank_top_m, timer) →
        head = scored[:top_m]；tail = scored[top_m:]
        scores = rerank_scores(question, [row.content for _,_,row in head])   # sigmoid [0,1]，一次批次
        head' = [(tier, scores[i], row) for i,(tier,_,row) in enumerate(head)] # 覆蓋 fused、保 tier/row
        tail' = 壓縮 tail 的 fused 到 [0, min(scores)) 保序                     # 保 recall、不反超
        → head' + tail'
build_context(scored, max_reports, max_passages, max_chars, now) → (sources, context)  # 不變
```

生成/選篇端零改動；rerank 僅重寫 `scored` 的分數與序。fail-open 時 `rerank_scored` 回傳原 `scored` 逐字節不變。

## 模組單元

### `app/services/rerank.py` — 新增（比照 `embed.py` 落地慣例）

**模型單例（延遲載入、thread-locked、預設 HF cache）**
```python
import logging, threading
from app.config import get_settings

logger = logging.getLogger(__name__)
_model = None
_lock = threading.Lock()


def _get_model():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                from FlagEmbedding import FlagReranker
                _model = FlagReranker(get_settings().rerank_model, use_fp16=False)  # CPU
    return _model


def rerank_scores(query: str, passages: list[str]) -> list[float]:
    """回每個 passage 對 query 的相關度 ∈ [0,1]（sigmoid 正規化）。空 → []。
    compute_score 單一 pair 回 float、多 pair 回 list，統一成 list[float]。"""
    if not passages:
        return []
    model = _get_model()
    raw = model.compute_score([(query, p) for p in passages], normalize=True)
    if isinstance(raw, (int, float)):
        raw = [raw]
    return [float(s) for s in raw]
```

**整合函式（純邏輯、可注入 fake `rerank_scores` 單測）**
```python
def rerank_scored(question, scored, *, top_m, timer=None):
    """對 scored 前 top_m 個（依現行序）重排：rerank 分覆蓋 fused、tier 保留；
    尾段壓縮到嚴格低於最低重排分、保序。任何例外或退化 → 回原 scored（fail-open）。"""
    if not scored or top_m <= 0:
        return scored
    try:
        head = scored[:top_m]
        tail = scored[top_m:]
        scores = rerank_scores(question, [row.content for (_t, _f, row) in head])
        if len(scores) != len(head):
            return scored  # 形狀不符：fail-open
        reranked = [(tier, scores[i], row) for i, (tier, _f, row) in enumerate(head)]
        if tail:
            min_rr = min(scores) if scores else 0.0
            ceiling = min_rr * 0.99  # 嚴格低於最低重排分
            tf = [f for (_t, f, _r) in tail]
            lo, hi = min(tf), max(tf)
            span = hi - lo
            def _compress(f):
                return ceiling if span == 0 else ceiling * (f - lo) / span
            tail = [(tier, _compress(f), row) for (tier, f, row) in tail]
        if timer is not None:
            timer.mark("rerank")
        return reranked + tail
    except Exception:
        logger.warning("rerank failed, fall back to fused order", exc_info=True)
        return scored
```

- 尾段壓縮上界＝`min(重排分)×0.99`，故任一重排候選（≥min_rr）> 任一尾段候選（<min_rr），**同 tier 內重排永在尾段之上**；跨 tier 由 `tier` 主鍵決定（tier-1 尾段仍在 tier-0 重排之上＝符合「字面優先」）。`normalize=True` 的 sigmoid ∈ (0,1) 故 `min_rr>0`、`ceiling>0`。
- `reranked + tail` 順序讓 `select_reports` 逐 `scored` 蒐集片段時**重排片段先入**（每報告前 `max_passages` 段取到最相關者）；報告最終序仍由 `select_reports` 依 `best_fused`(=該報告最高重排分) 重排。

### `app/services/retrieval_pipeline.py` — `retrieve_context` 加插入點

新增參數 `rerank_top_m: int = 0`（0＝不重排；由呼叫端依 config 決定）。模組頂層加 `from app.services.rerank import rerank_scored`（與既有 `hybrid_search`/`build_context` 同為模組層 import；rerank.py 的模型載入是 `_get_model` 內延遲觸發，import 期零成本；rerank 不 import retrieval_pipeline/answer 故無循環）。插在 `hybrid_search` 之後、`build_context` 之前：
```python
async def retrieve_context(
    question, *, k, dense_scan, max_reports, max_passages, max_chars,
    filters=None, now=None, timer=None, rerank_top_m=0,
) -> tuple[list[Source], str]:
    ...
    async with SessionFactory() as session:
        scored = await hybrid_search(session, question, qvec, k=k, dense_scan=dense_scan, **filters)
    if timer is not None:
        timer.mark("retrieve")
    if rerank_top_m > 0:
        scored = rerank_scored(question, scored, top_m=rerank_top_m, timer=timer)
    return build_context(scored, max_reports=max_reports, max_passages=max_passages, max_chars=max_chars, now=now)
```
`rerank_top_m=0` 時**零行為改變**（byte-identical，parity 測鎖定）。

### 呼叫端接線（QA／研報各自帶 cap）

- `app/services/answer.py`：兩處 `retrieve_context`（首輪＋多輪，:802/:810 附近）傳 `rerank_top_m=ASK_RERANK_CANDIDATES if ASK_RERANK_ENABLED else 0`。模組常數 `ASK_RERANK_ENABLED`/`ASK_RERANK_CANDIDATES` 由 config 取。
- `app/services/report.py`：`retrieve_context`（:208）傳 `rerank_top_m=REPORT_RERANK_CANDIDATES if REPORT_RERANK_ENABLED else 0`。
- `_StageTimer` 已有 `rerank` 段（`rerank_scored` 內 `timer.mark("rerank")`）；qa_timing/研報計時自動含 `rerank_ms`。

### `app/config.py` — 新增設定（沿用 frozen dataclass + `_flag` 慣例）

`Settings` 加欄；`_load()` 加：
```python
ask_rerank_enabled=_flag("ASK_RERANK_ENABLED", "1"),
ask_rerank_candidates=int(os.getenv("ASK_RERANK_CANDIDATES", "50")),
report_rerank_enabled=_flag("REPORT_RERANK_ENABLED", "1"),
report_rerank_candidates=int(os.getenv("REPORT_RERANK_CANDIDATES", "120")),
rerank_model=os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
```

## 與既有選篇政策的互動（記錄、由 eval 驗證、env 可調）

`select_reports` 不改，但其 `best_fused` 對被重排研報現在＝**rerank 分**（非 dense fused），故下列既有旋鈕語義隨之轉為「基於 cross-encoder 相關度」——**合理且多半更佳，但門檻值以 dense 尺度校準過，可能需重調**（皆 env 可調、由 M1 eval 揭示）：
- `relevance_band`（0.10）：對重排分 [0,1] 分桶決定「同 band 內以新近度決勝」。
- `relevance_floor`（0.62）：tier-0 研報 `best_fused<floor`（超過 `min_reports`）被剔除——現變「cross-encoder 相關度下限」，可能過嚴/過鬆。
- `min_reports`/`max_stale`/過舊軟截斷：不受影響（key off tier/recency/report_date，非分數尺度）。

## 部署與相容

- **無 schema 變更；影響檢索/生成品質故合併後 restart `report-mark-web.service`**。
- **模型預暖**：`bge-reranker-v2-m3` 首載 ~600MB（HF 預設 cache）。比照 BGE-M3 需預暖（否則首個問答/研報付下載代價）——部署後跑一次暖機查詢或 ingest；`_get_model` 延遲載入不阻塞 import。
- 依賴零新增（FlagEmbedding 1.4.0 `FlagReranker` 已可用；CPU torch）。
- 市場代碼/findb 對齊：不受影響。

## 測試策略

- **`rerank_scores`（rerank.py）**：monkeypatch `_get_model` 回 fake（`compute_score` 回固定序列）——驗單一 pair(float)→[float]、多 pair→list[float]、空 passages→[]、`normalize=True` 傳入。零真模型。
- **`rerank_scored`（核心、注入 fake `rerank_scores`）**：
  - 同 tier 內依 rerank 分重排（給定 fake 分數序，驗 head 重排後 fused＝分數、tier/row 不變）。
  - 尾段壓縮嚴格低於 `min(重排分)`、保相對序；`reranked+tail` 併接。
  - **跨 tier 保序**：tier-1 尾段（未重排）仍排在 tier-0 重排之上（tier 主鍵）。
  - `top_m` 截取（`top_m<len(scored)`）與 `top_m>=len` 邊界。
  - **fail-open**：`rerank_scores` 拋例外 → 回原 `scored` **逐字節不變**（`is` 同物件或等值）；`top_m<=0`／空 `scored` → 直接回原。
  - 形狀不符（fake 回長度不對）→ fail-open。
- **`retrieve_context` 整合**：mock `hybrid_search`——`rerank_top_m=0` → 不呼叫 rerank、輸出與現況 byte-identical（parity）；`rerank_top_m>0` → 呼叫 `rerank_scored`、`timer` 記 `rerank` 段。**patch where used＝`retrieval_pipeline.rerank_scored`**（模組層 import 故名字綁在 retrieval_pipeline 命名空間；沿用 M0 教訓）。
- **接線（answer.py/report.py）**：`ASK_RERANK_ENABLED=0` → 傳 `rerank_top_m=0`（不重排）；開啟 → 傳對應 cap。以既有 flow 測法（mock `retrieve_context`）驗傳參，不打真模型。
- **config**：新欄預設值 parity（enabled 真值、candidates 50/120、model 字串）。
- 全套 `uv run pytest -q` 綠、零既有測試異動。
- **真模型端到端＝操作性（如 M1 Task 5）**：跑 `eval/run_ragas.py` 對同題集，rerank 開/關各一次 → 比 `baseline-m0.json`：**Context Precision 上升、Faithfulness 不降、n_errors 不因 rerank 增、Context Recall（既有 `scripts/eval_retrieval.py` 關鍵字 hit-rate）不降**；記 `rerank_ms` 與問答延遲增幅。產 `eval/baselines/baseline-m2.json`（commit）。首跑人工抽驗重排前後 top 片段是否更相關。

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| CPU reranker 拖慢問答熱路徑 | 保守 cap（top~50）、一次批次 `compute_score`；相對生成 ~90s 為小字；env 可調/可關 |
| 模型載入/推論失敗炸檢索 | `rerank_scored` try/except fail-open 回原序；`_get_model` 延遲載入 |
| 首載 ~600MB 拖首查 | 部署預暖（比照 BGE-M3） |
| `relevance_floor`/`band` 以 dense 尺度校準、對 rerank 分過嚴/鬆 | env 可調；M1 eval 前後對比揭示、必要時調門檻（不改 select_reports 邏輯） |
| 尾段壓縮致 recall 降 | 尾段保留於 scored（非丟棄），eval 的 Context Recall 關鍵字 harness 把關「不下降」 |
| rerank 分數尺度與 dense 混用（尾段） | 尾段壓縮到嚴格低於最低重排分，同 tier 內不反超；跨 tier 由 tier 主鍵決定 |
