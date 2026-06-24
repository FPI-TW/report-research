# 問答 RAG：更全面的引用 + 守住準度與新近度

- 日期：2026-06-24
- 範圍：問答模式的脈絡組裝（`app/services/answer.py` 的 `build_context` 與規模常數）。不動總覽路徑（`overview.py`，另一分支）、檢索頁、瀏覽模式。

## 背景與動機

使用者反映：問答**能引用的來源太少、分析不夠全面**，並要求**資料夠新、更準確**。

現況量測（qa_log 近 21 天 16 題、實際查詢探測）：

| 指標 | 現值 |
|------|------|
| 作答模型 | `claude-sonnet-4-6`（200k context，遠未用滿） |
| 每題顯示來源 | 平均 ~6.5 篇（上限 8） |
| LLM 實際引用 | 平均 ~4.7 篇（上限 8） |
| 延遲 | 平均 41s、p90 60s |
| `build_context` 上限 | `MAX_REPORTS=8`、`MAX_PASSAGES=3`、`MAX_CONTEXT_CHARS=9000`、`ASK_DENSE_SCAN=200` |

**根因**：來源少純粹是 `build_context` 上限設太保守（8 篇 / 9000 字），而非召回或模型限制——探測顯示一個典型查詢可召回 **150~250 篇**不同研報，且 Sonnet 4.6 的 context 還有大量餘裕。瓶頸只在 `build_context` 的截斷。

**關鍵發現（fused 分數壓縮）**：探測三個代表查詢，top20 的 `best_fused` 都密集落在 **0.70~0.74**（#1 與 #20 僅差 ~0.04），且上百篇都算「相關」。意涵：
- **廣問題**本就有大量相關研報，加到 15 篇可輕鬆填滿真正相關者（皆 >0.68）；相關度下限對它近乎無作用，靠 `max_reports` 上限即可。
- **窄問題**的分數會更快掉落，相關度下限才真正發揮「寧缺勿濫」。
- 故下限應設計成「**弱命中防護**」而非精準切刀，且需 **tier 感知**（有字面命中的 tier≥1 一律放行，只對純語意 tier 0 套 fused 門檻）。

## 設計決策（已與使用者拍板）

1. **加碼幅度＝中度**：來源 8→15、脈絡 9000→20000 字、每篇段落 3→4。延遲增約 +15~30s（估 ~60s），可接受。
2. **準度＝相關度下限（寧缺勿濫）**：低於門檻的弱相關研報不填進脈絡；窄問題只給夠相關的幾篇，不湊數。
3. **新近度＝沿用現有排序 + 限制過舊篇數**：保留剛調校驗證過的分層新近度排序與既有極舊軟截斷，另加「超過半年的研報最多 N 篇」上限，避免大脈絡被相關但過舊的研報塞滿。

## 現況架構（變更前）

`app/services/answer.py`：
- 模組常數 `MAX_REPORTS=8`、`MAX_PASSAGES_PER_REPORT=3`、`MAX_CONTEXT_CHARS=9000`、`ASK_DENSE_SCAN=200`、`RETRIEVAL_K=8`（皆 env 化）。
- `build_context(scored, *, max_reports, max_passages, max_chars, now, half_life_days)`：
  1. 把 chunk 依 `report_id` 聚合（首見即最佳 tier/fused，因 `scored` 已排序），每篇收至多 `max_passages` 段。
  2. 報告層排序鍵 `(best_tier, _relevance_band(best_fused), _recency_factor, best_fused, report_id)` 由高到低。
  3. 既有「極舊軟截斷」：`fresh_count = #(recency_factor ≥ ASK_FRESH_FACTOR=0.5)`；`cutoff_active = fresh_count ≥ ASK_MIN_FRESH_BEFORE_CUTOFF=2`；填充時 `if cutoff_active and recency_factor < ASK_STALE_FACTOR(0.1): continue`（極舊≈300天+，有≥2新時才丟）。
  4. 填充迴圈：依序取報告，每篇取 passages（受 `max_chars` 總字數約束），給連續編號 `[1..N]`，產 `Source` 清單與脈絡文字。
  5. 標記日期最新者 `is_latest`。
- `_recency_factor(report_date, now, half_life=90)` ∈ [0,1]：今天=1、半衰期前=0.5、無日期=0。

## 設計

### 1. 加碼規模（模組常數，全 env 可調）

| 常數 | 舊 | 新 |
|------|----|----|
| `ASK_MAX_REPORTS` | 8 | **15** |
| `ASK_MAX_PASSAGES` | 3 | **4** |
| `ASK_MAX_CONTEXT_CHARS` | 9000 | **20000** |
| `ASK_DENSE_SCAN` | 200 | **400** |
| `ASK_RETRIEVAL_K` | 8 | **15**（語意一致；問答路徑顯式傳 `dense_scan`，k 不參與截斷） |

`ASK_DENSE_SCAN` 提到 400：探測顯示 200 chunk 已召回 150+ 篇，但多為每篇 1 段；要讓 top15 篇各自湊到 4 段，需更深的掃描讓同篇其他 chunk 進入候選。

### 2. 相關度下限（準度，tier 感知、寧缺勿濫）

新增常數：
- `ASK_RELEVANCE_FLOOR = float(env "ASK_RELEVANCE_FLOOR", "0.62")`：純語意（tier 0）研報的 `best_fused` 最低門檻。
- `ASK_MIN_REPORTS = int(env "ASK_MIN_REPORTS", "3")`：保底篇數，不受下限限制。

在 `build_context` 排序後的填充迴圈中，對每篇報告計算「是否夠相關」：

```
keep_relevant = (info["best_tier"] >= 1) or (info["best_fused"] >= ASK_RELEVANCE_FLOOR)
```

- 前 `ASK_MIN_REPORTS` 篇（依排序）一律納入，避免邊界但合理的問題被餓死（最終仍由既有「無 context → NO_CONTEXT_MESSAGE」兜底）。
- 第 `ASK_MIN_REPORTS` 篇之後，只納入 `keep_relevant` 為真者；遇到不夠相關的就跳過該篇（繼續看後面是否有夠相關的，而非直接 break——因排序已大致由相關度遞減，但 stale-cap 可能讓較相關的較舊篇排後，故用 continue 不 break）。

因 fused 壓縮，`0.62` 對廣問題近乎全數放行（探測值皆 >0.68）、對窄問題剪尾。**門檻為起始值，實作時用 `eval_retrieval` + 窄問題實測校準**（窄問題舉例：冷門小型股的特定事件）。

### 3. 限制過舊篇數（新近度）

新增常數：
- `ASK_STALE_AGE_DAYS = int(env "ASK_STALE_AGE_DAYS", "180")`：界定「過舊」的年齡（天）。
- `ASK_MAX_STALE_REPORTS = int(env "ASK_MAX_STALE_REPORTS", "4")`：脈絡中過舊研報的篇數上限。

在填充迴圈中維護 `stale_used` 計數；對每篇報告：
```
age_days = (now_date - report_date).days   # 無日期視為 0（不算過舊）
is_stale = report_date is not None and age_days > ASK_STALE_AGE_DAYS
if is_stale and stale_used >= ASK_MAX_STALE_REPORTS:
    continue        # 過舊配額已滿 → 跳過，把槽留給較新的相關研報
# ...納入後：if is_stale: stale_used += 1
```

- 與既有「極舊軟截斷」並存且互補：極舊（~300天+，factor<0.1）在有≥2新時被既有邏輯丟棄；本上限再額外限制「180~300 天」這段中度過舊的篇數。
- 保底例外：若納入的非過舊研報不足，仍允許過舊研報補到 `ASK_MIN_REPORTS`（避免歷史性問題被餓死）。實作上把「過舊配額」檢查放在「保底篇數」之後。

### 4. 填充迴圈整合順序（單一迴圈，三道閘）

排序後對每篇報告，依序判定（`n` 為已納入數）：
1. **保底**：`n < ASK_MIN_REPORTS` → 直接嘗試納入（跳過下方相關度/過舊閘，僅受 `max_chars` 約束）。
2. **相關度下限**：否則 `keep_relevant` 為假 → `continue`。
3. **過舊配額**：`is_stale and stale_used >= ASK_MAX_STALE_REPORTS` → `continue`。
4. 取 passages（受 `max_chars`）；若有 → 納入、`n+=1`、`is_stale` 則 `stale_used+=1`。
5. `n >= max_reports` → break。

既有的排序、極舊軟截斷、`is_latest` 標記不變。

### 5. Prompt（輕調）

`SYSTEM_PROMPT` 維持既有 7 條（含新近度規則）；在第 2 條附近補一句鼓勵綜合：「**參考片段較多時，請綜合多篇研報、彼此佐證後再作答，並優先採用較新的研報**」。不加重型「逐篇交叉核對分歧」要求（使用者未選該選項）。

### 6. 錯誤處理 / 相容

- 全部為 `build_context` 內的純函式邏輯 + 常數；無新 I/O、無 schema 變更。
- 既有 `build_context` 的呼叫端（`answer_question`、測試）簽名不變（新增的常數有預設值，新行為靠新常數驅動）。
- 總覽路徑（`overview.py`）不經 `build_context`，完全不受影響。
- 全 env 可調：營運可即時回退（如 `ASK_MAX_REPORTS=8` 還原舊行為）。

## 測試

- **純函式**（`tests/test_answer.py`，沿用 `make_row` 造假 row）：
  - 相關度下限：tier 0、fused 低於門檻的多餘研報被剔除；tier≥1 一律保留；前 `ASK_MIN_REPORTS` 篇即使低於門檻仍納入。
  - 過舊上限：建構「>180 天」研報多篇，驗證納入數不超過 `ASK_MAX_STALE_REPORTS`；不足非過舊時保底仍納入。
  - 規模：`max_reports=15`/`max_passages=4`/`max_chars=20000` 生效（編號到 15、每篇至多 4 段、總字數受限）。
  - 既有 `BuildContextTests` 全綠（排序、保序、空 content、`is_latest` 不回歸）。
- **校準與評估**（離線，非單元測試）：用 `scripts/eval_retrieval.py` + `scripts/analyze_qa_log.py` 跑前後對比：平均引用數↑、引用中位年齡持平或↓、recency_pass_rate、延遲。據此校準 `ASK_RELEVANCE_FLOOR`/`ASK_STALE_*` 預設值。
- **Live 端到端**：對 2~3 個代表問題（廣／窄／歷史性）跑功能分支 `answer_question`，確認來源數變多、窄問題不被弱相關塞滿、延遲在可接受範圍。
- 程式品質：`uv run pytest`（black/ruff/mypy 若缺則略過）。

## 不在本次範圍（YAGNI / 後續）

- Cross-encoder 重排序器（re-ranker）——若校準後仍覺準度不足再評估。
- 多查詢 / 查詢分解（multi-query）——更全面但延遲倍增，本次先靠加碼 + 下限達標。
- 逐篇交叉核對分歧的重型 prompt（使用者未選）。
- 動態依問題寬窄自動調整來源數——先用靜態上限 + 相關度下限近似。
