"""集中設定：一次讀取 ASK_*/REPORT_* 環境變數。

各服務模組保留原常數名，改由 get_settings() 取值，避免同一 env 在多檔重複讀取
（如 ASK_DENSE_SCAN 原本 answer.py 與 report.py 各定義一次）。retrieval.py 的
非-env 字面量（BAND_WIDTH 等）不在此收斂範圍（見 M6）。
"""

import logging
import os
from dataclasses import dataclass


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default) not in ("0", "false", "False", "")


_LOG_LEVELS = ("CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG")


def _log_level(name: str, default: str) -> str:
    """log level 名稱；未知值退回預設並警告。

    刻意不讓 typo 靜默生效：打錯成 `LOG_LEVEL=info0` 若原樣傳進 dictConfig 會拋
    ValueError，而它發生在 web/server.py 的 import 期——app 起不來且訊息晦澀。
    """
    v = (os.getenv(name, default) or "").strip().upper()
    if v not in _LOG_LEVELS:
        logging.getLogger(__name__).warning(
            "%s=%r 不是合法 log level（可用：%s），退回 %s",
            name, v, "/".join(_LOG_LEVELS), default,
        )
        return default
    return v


_EXTRACTORS = ("pypdf", "pdfplumber")
_OBJECT_STORAGE_MODES = ("local", "hybrid", "r2")


def _extractor(name: str, default: str) -> str:
    """抽取器名稱；未知值退回預設並警告。

    理由：`EXTRACTOR=pdfplumbr` 這種 typo 若靜默生效，生產會在
    沒有任何訊息的情況下切回或切走一條抽取路徑，而兩條路徑的 full_text 不同、
    extraction_version 也不同——之後的回填會把它們當成兩個版本各自處理。
    """
    v = (os.getenv(name, default) or "").strip().lower()
    if v not in _EXTRACTORS:
        logging.getLogger(__name__).warning(
            "%s=%r 不是合法抽取器（可用：%s），退回 %s",
            name, v, "/".join(_EXTRACTORS), default,
        )
        return default
    return v


def _object_storage_mode() -> str:
    """物件儲存模式；啟用 R2 時缺任一必要設定即拒絕啟動。"""
    mode = (os.getenv("OBJECT_STORAGE_MODE", "local") or "").strip().lower()
    if mode not in _OBJECT_STORAGE_MODES:
        raise ValueError(f"OBJECT_STORAGE_MODE 必須是 {'|'.join(_OBJECT_STORAGE_MODES)}，目前為 {mode!r}")
    if mode != "local":
        missing = [
            key for key in ("R2_ENDPOINT_URL", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")
            if not (os.getenv(key) or "").strip()
        ]
        if missing:
            raise ValueError(f"OBJECT_STORAGE_MODE={mode} 需要設定：{', '.join(missing)}")
    return mode


def _r2_presign_ttl() -> int:
    """Private presigned links are bearer credentials and must never outlive one hour."""
    raw = os.getenv("R2_PRESIGN_TTL_SECONDS", "3600")
    try:
        ttl = int(raw)
    except ValueError as exc:
        raise ValueError("R2_PRESIGN_TTL_SECONDS 必須是 1..3600 的整數") from exc
    if not 1 <= ttl <= 3600:
        raise ValueError("R2_PRESIGN_TTL_SECONDS 必須介於 1..3600 秒")
    return ttl



def _faithfulness_min() -> float:
    """數值主張支持率門檻。新名 FAITHFULNESS_MIN 優先；缺值時退回舊名 REPORT_FAITHFULNESS_MIN。"""
    v = os.getenv("FAITHFULNESS_MIN")
    if v is None or not v.strip():
        v = os.getenv("REPORT_FAITHFULNESS_MIN", "0.9")
    return float(v)

@dataclass(frozen=True)
class Settings:
    # ASK_*（answer.py）
    ask_max_reports: int
    ask_max_passages: int
    ask_max_context_chars: int
    ask_retrieval_k: int
    ask_dense_scan: int
    ask_recency_weight: float
    ask_recency_half_life_days: float
    ask_relevance_band: float
    ask_band_eps: float
    ask_fresh_factor: float
    ask_stale_factor: float
    ask_min_fresh_before_cutoff: int
    ask_relevance_floor: float
    ask_min_reports: int
    ask_stale_age_days: int
    ask_max_stale_reports: int
    # 使用者可否在問答開啟網搜（伺服器端總閘；關掉即使前端送 web=true 也不生效）
    ask_enable_web: bool
    # 開啟網搜那一輪的主 LLM 逾時：網搜會讓單題多花數十秒，沿用 llm.py 的 120s
    # 預設會在「搜到一半」被砍斷，症狀是答案無聲截斷。不開網搜的路徑不受影響。
    ask_web_timeout: float
    # intent.py
    ask_intent_model: str
    ask_intent_timeout: float
    ask_condense_model: str
    ask_condense_timeout: float
    # 私有 Cloudflare R2（local 預設不建立 client，也不需要 boto3/credentials）
    object_storage_mode: str
    r2_endpoint_url: str
    r2_bucket: str
    r2_access_key_id: str
    r2_secret_access_key: str
    r2_presign_ttl_seconds: int
    # EXTRACTOR（extract.py，E1a）：pypdf＝現況；pdfplumber＝版面層。預設維持 pypdf，
    # E1d 才由 sync 鏈的環境檔切換（docs/EXTRACTION.md §9）。
    extractor: str
    # EXTRACTION_REVIEW_MIN（store.needs_review，E1b）：quality_score 低於此值標 needs_review。
    # 只標記不擋（§4.2「一律入庫，只標記不擋」）；pages_failed 非空也標，與分數無關。
    extraction_review_min: float
    # EXTRACTION_REVIEW_MIN_COVERAGE／EXTRACTION_REVIEW_MAX_GARBLED（v4，docs/EXTRACTION.md §5）：
    # 總分是加權平均、單一嚴重缺陷會被稀釋，所以逐項再看 layout_coverage 與 garbled_ratio。
    # 預設 0.30／0.02 是抽樣量出來的（coverage p5 之下、v3 全庫亂碼 >2% 僅 27 篇）。
    extraction_review_min_coverage: float
    extraction_review_max_garbled: float
    # rerank.py（M2）
    ask_rerank_enabled: bool
    ask_rerank_candidates: int
    ask_rerank_timeout: float
    rerank_model: str
    # trusted_market_data.py（M4a）
    trusted_data_enabled: bool
    # agentic_qa / query_planner（M5）—— M5 里程碑只在本區段內加鍵
    qa_planner_model: str
    qa_planner_timeout: float
    qa_planner_max_subqueries: int
    qa_max_rounds: int
    qa_agentic_enabled: bool
    qa_agentic_timeout: float
    qa_subquery_max_reports: int

    # 忠實度查核 / faithfulness（M8 里程碑）—— M8 里程碑只在本區段內加鍵
    ask_faithfulness_enabled: bool
    faithfulness_min: float              # 數值主張支持率門檻：監控頁與離線評測的「低於門檻」判準
    ask_faithfulness_sample_rate: float  # 問答：含數字答案的查核抽樣率（0..1）
    faithfulness_model: str
    faithfulness_timeout: float
    ask_faithfulness_timeout: float      # 問答抽查專用；見下方註解
    ask_faithfulness_max_inflight: int   # 同時在背景跑的問答抽查上限；超過即跳過該次抽查

    # 執行期可觀測性
    log_level: str

    # 觀點雷達目錄（web/routers/radar.py）的回應快取秒數；0＝停用
    radar_catalog_cache_ttl: float

    # DB 連線池與逾時（app/services/db.py）—— 本區段只放 DB_* 旋鈕
    db_pool_size: int
    db_max_overflow: int
    db_pool_timeout: float
    db_pool_recycle: int
    db_statement_timeout_ms: int
    db_idle_tx_timeout_ms: int
    db_maintenance_statement_timeout_ms: int

    # 嵌入模型的執行緒紀律（app/services/embed.py）
    embed_max_concurrency: int
    embed_torch_threads: int


def _load() -> Settings:
    intent_model = os.getenv("ASK_INTENT_MODEL", "claude-haiku-4-5")
    return Settings(
        ask_max_reports=int(os.getenv("ASK_MAX_REPORTS", "15")),
        ask_max_passages=int(os.getenv("ASK_MAX_PASSAGES", "4")),
        ask_max_context_chars=int(os.getenv("ASK_MAX_CONTEXT_CHARS", "20000")),
        ask_retrieval_k=int(os.getenv("ASK_RETRIEVAL_K", "15")),
        ask_dense_scan=int(os.getenv("ASK_DENSE_SCAN", "400")),
        ask_recency_weight=float(os.getenv("ASK_RECENCY_WEIGHT", "0.06")),
        ask_recency_half_life_days=float(os.getenv("ASK_RECENCY_HALF_LIFE_DAYS", "90")),
        ask_relevance_band=float(os.getenv("ASK_RELEVANCE_BAND", "0.10")),
        ask_band_eps=float(os.getenv("ASK_BAND_EPS", "0.03")),
        ask_fresh_factor=float(os.getenv("ASK_FRESH_FACTOR", "0.5")),
        ask_stale_factor=float(os.getenv("ASK_STALE_FACTOR", "0.1")),
        ask_min_fresh_before_cutoff=int(os.getenv("ASK_MIN_FRESH_BEFORE_CUTOFF", "2")),
        ask_relevance_floor=float(os.getenv("ASK_RELEVANCE_FLOOR", "0.62")),
        ask_min_reports=int(os.getenv("ASK_MIN_REPORTS", "3")),
        ask_stale_age_days=int(os.getenv("ASK_STALE_AGE_DAYS", "180")),
        ask_max_stale_reports=int(os.getenv("ASK_MAX_STALE_REPORTS", "4")),
        ask_enable_web=_flag("ASK_ENABLE_WEB", "1"),
        ask_web_timeout=float(os.getenv("ASK_WEB_TIMEOUT", "240")),
        ask_intent_model=intent_model,
        ask_intent_timeout=float(os.getenv("ASK_INTENT_TIMEOUT", "20")),
        ask_condense_model=os.getenv("ASK_CONDENSE_MODEL", intent_model),
        ask_condense_timeout=float(os.getenv("ASK_CONDENSE_TIMEOUT", "20")),
        object_storage_mode=_object_storage_mode(),
        r2_endpoint_url=os.getenv("R2_ENDPOINT_URL", "").strip(),
        r2_bucket=os.getenv("R2_BUCKET", "").strip(),
        r2_access_key_id=os.getenv("R2_ACCESS_KEY_ID", "").strip(),
        r2_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", "").strip(),
        r2_presign_ttl_seconds=_r2_presign_ttl(),
        extractor=_extractor("EXTRACTOR", "pypdf"),
        extraction_review_min=float(os.getenv("EXTRACTION_REVIEW_MIN", "0.6")),
        extraction_review_min_coverage=float(os.getenv("EXTRACTION_REVIEW_MIN_COVERAGE", "0.30")),
        extraction_review_max_garbled=float(os.getenv("EXTRACTION_REVIEW_MAX_GARBLED", "0.02")),
        ask_rerank_enabled=_flag("ASK_RERANK_ENABLED", "1"),
        ask_rerank_candidates=int(os.getenv("ASK_RERANK_CANDIDATES", "50")),
        # per-path 逾時：prod 實測（20 核 CPU）50 對 ~34s、120 對 ~93s；預設須蓋過
        # 實測值 + 忙碌餘裕，否則 rerank 靜默 fail-open 形同全關（M1b 基準線 10/10 逾時）。
        ask_rerank_timeout=float(os.getenv("ASK_RERANK_TIMEOUT", "60")),
        rerank_model=os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
        trusted_data_enabled=_flag("TRUSTED_DATA_ENABLED", "1"),
        # agentic_qa / query_planner（M5）—— M5 里程碑只在本區段內加鍵
        qa_planner_model=os.getenv("QA_PLANNER_MODEL", intent_model),
        # 20 秒過緊：prod 實測 claude CLI 光冷啟動的 ttft 就約 10s（每次呼叫都重付
        # ~24K token 系統提示），規劃 prompt 比分類長，於是每一題都逾時 →
        # LLMUnavailableError → agentic 永遠 degraded，M5 形同關閉。
        qa_planner_timeout=float(os.getenv("QA_PLANNER_TIMEOUT", "45")),
        qa_planner_max_subqueries=int(os.getenv("QA_PLANNER_MAX_SUBQUERIES", "3")),
        qa_max_rounds=int(os.getenv("QA_MAX_ROUNDS", "2")),
        qa_agentic_enabled=_flag("QA_AGENTIC_ENABLED", "1"),
        # 迴圈總逾時（秒）；不含第一輪檢索與最終作答串流，於 asyncio.wait_for 落實。
        qa_agentic_timeout=float(os.getenv("QA_AGENTIC_TIMEOUT", "90")),
        qa_subquery_max_reports=int(os.getenv("QA_SUBQUERY_MAX_REPORTS", "5")),
        # 忠實度查核 / faithfulness（M8 里程碑）
        ask_faithfulness_enabled=_flag("ASK_FAITHFULNESS_ENABLED", "1"),
        # 新名 FAITHFULNESS_MIN；讀不到時退回舊名 REPORT_FAITHFULNESS_MIN（研報 PDF 功能移除前的
        # 鍵名，生產環境檔可能還設著）。讀者只有監控頁 `_FAITHFULNESS_MIN` 與
        # scripts/eval_faithfulness.py，都是「低於門檻」的判準。
        faithfulness_min=_faithfulness_min(),
        ask_faithfulness_sample_rate=float(
            os.getenv("ASK_FAITHFULNESS_SAMPLE_RATE", "1.0")
        ),
        # 生產忠實度 judge。**刻意不再沿用 ASK_INTENT_MODEL**（DeepSeek 遷移 PR-07）：
        # 先前未設時跟著 intent 走，而生產環境檔沒有覆寫——只要哪天把路由模型換掉，
        # judge 就在同一刻被靜默換掉，監控卡上的分數從此是另一把尺量的，卻沒有任何
        # 記號。預設字串與改動前的實際值相同（intent 預設 claude-haiku-4-5），所以
        # 本改動不改變生產實際用的 judge。空字串視同未設（`or`）。
        # 換 judge 時連帶看 app/services/judge_schema.py：讀分數的三處只計現行 judge。
        faithfulness_model=os.getenv("FAITHFULNESS_MODEL") or "claude-haiku-4-5",
        # 沒有現行呼叫端：問答抽查用下面那顆 ask_faithfulness_timeout，
        # scripts/eval_faithfulness.py 只讀 qa_log、不呼叫 LLM。保留是為了 check_faithfulness
        # 的其他呼叫者（目前沒有），以及下方「問答那顆必須比它大」的測試基準。
        faithfulness_timeout=float(os.getenv("FAITHFULNESS_TIMEOUT", "60")),
        # 問答抽查的逾時與 `faithfulness_timeout` 分開：後者是 judge 單次呼叫的通用逾時，
        # 而問答抽查的實測需求遠超 60 秒：
        # 2026-08-21 以生產原始輸入量到 ground 單次 48–142 秒（payload 15–19k 字），
        # 60 秒必然砍掉其中一題。抽查已改成背景任務、不佔 `/api/ask` 名額，所以這裡
        # 放寬是零使用者成本。
        ask_faithfulness_timeout=float(os.getenv("ASK_FAITHFULNESS_TIMEOUT", "240")),
        # 抽查改成背景任務後就不再受 `/api/ask` 的併發閘保護：每次抽查 spawn 一個
        # `claude` CLI 跑 48–142 秒，抽樣率預設 1.0，連續問答時背景行程數會無上界地
        # 累積。這裡給它自己的上限——超過就**跳過該次抽查**而不是排隊：抽查本來就是
        # 抽樣的 best-effort，少查一題與抽樣率沒抽中是同一件事，排隊反而會讓抽查對象
        # 與抽查時間脫節。
        ask_faithfulness_max_inflight=max(0, int(os.getenv("ASK_FAITHFULNESS_MAX_INFLIGHT", "2"))),
        log_level=_log_level("LOG_LEVEL", "INFO"),
        # 目錄每次請求要跑兩次三層 CTE（清單＋facets，含對 stock_targets 全表 unnest），帶 stance
        # 時還要全量取回算共識；而 report_signal 每 3 小時才由批次更新一次。60 秒的舊資料
        # 在這個更新頻率下看不出來，換到的是雷達頁落地、換頁、切市場都不必重算。
        radar_catalog_cache_ttl=max(0.0, float(os.getenv("RADAR_CATALOG_CACHE_TTL", "60"))),
        # ── DB 連線池與逾時（app/services/db.py）─────────────────────────────
        # 池是 **per-process**：生產 web 是單 worker（report-mark-web.service 的
        # ExecStart 沒有 --workers），批次腳本各自是獨立行程、各自一個池。
        #
        # 上限 = pool_size + max_overflow = 20。與 PG 的 max_connections 的關係：
        # DB 跑 pgvector/pgvector:pg16 官方映像，Makefile 的 docker run 沒有帶任何
        # postgresql.conf 覆寫 ⇒ max_connections=100、superuser_reserved_connections=3
        # ⇒ 一般角色可用 97。web 20 ＋ 同步鏈三支批次（各自單執行緒、同時只開一個
        # session）＝ 26，離 97 還很遠。要加 uvicorn --workers 或提高任何併發閘之前，
        # 先把「worker 數 × 20 ＋ 批次」重算一次。
        db_pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
        # 為何上限取 20 而不是沿用 SQLAlchemy 預設的 5+10=15：有併發閘的路徑只有
        # /api/ask(3)；/api/search、雷達、閱讀頁、監控頁**完全沒有併發閘**，剩下的
        # 17 條就是留給它們的突發量。pool_size 只留 5 條常駐，其餘走 overflow 用完即關，
        # 不讓閒置連線長期佔著 PG 的 backend 記憶體。
        db_max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "15")),
        # BGE-M3 的 model.encode 是 CPU-bound，而每個 web 呼叫端都經 asyncio.to_thread
        # 丟進預設執行緒池（min(32, cpu+4) 條）。`/api/search`／雷達／閱讀頁**完全沒有
        # 併發閘**，所以同時進 encode 的執行緒數沒有上界，而 torch 自己還會再開
        # intra-op 執行緒——CPU-only 推論下這是嚴重超額訂閱，每一條都變慢。
        # 預設 1＝序列化。CPU-bound 工作序列化不損總吞吐（反而因為少了搶核而變快），
        # 代價只是併發請求的尾延遲，而那本來就被超額訂閱吃掉了。
        embed_max_concurrency=int(os.getenv("EMBED_MAX_CONCURRENCY", "1")),
        # 0＝不設（沿用 torch 預設＝實體核心數）。與上面那個閘配合：併發限 1 時讓
        # torch 用滿核心是對的；若把併發開大，這裡就該同步調小，否則兩層相乘。
        embed_torch_threads=int(os.getenv("EMBED_TORCH_THREADS", "0")),
        # 取不到連線＝池已滿；在單 worker、上限 20 的前提下這已經是異常狀態。
        # 預設的 30 秒只是把使用者的等待拉長，最後回的還是同一個 500。
        db_pool_timeout=float(os.getenv("DB_POOL_TIMEOUT", "10")),
        # pool_pre_ping 只在 checkout 當下驗一次；recycle 讓長期閒置的連線在被 DB
        # 或中介（Docker NAT）默默砍掉之前就先主動汰換。
        db_pool_recycle=int(os.getenv("DB_POOL_RECYCLE", "1800")),
        # 這條才是真正的耗盡防線：沒有 statement_timeout 時，單一失控查詢可以無上限
        # 佔住一條連線（雷達目錄與 overview 分面在現規模下都是全表掃描）。
        # **刻意不取架構檢視建議的 15000**：本專案沒有壓測數據，而有幾支查詢天生偏慢
        # （雷達目錄的兩次全表 unnest、overview 一題掃 7 次同一母體、閱讀頁 similar
        # 拉高 ef_search 的 HNSW 掃描），15s 有把正常功能打掛的實質風險。60s 比任何
        # 已知查詢高一個數量級，仍舊把「無上限」變成有上限。
        db_statement_timeout_ms=int(os.getenv("DB_STATEMENT_TIMEOUT_MS", "60000")),
        # **預設 0（關）是刻意的，不是漏設。** 批次匯入會在交易開著的情況下做長時間
        # 的非 DB 工作：scripts/sync_new_reports.py 先 report_exists() 開了交易，接著
        # 才 spawn claude CLI 標註（硬逾時 150s）與 BGE-M3 嵌入（大檔可達數分鐘），
        # 中間完全沒有 commit。設了這個值＝生產每 3 小時一次的同步會把報告靜默丟進
        # FAIL_LOG。web 行程沒有這個形態（2026-07-29 的 AST 複驗：44 個
        # `async with SessionFactory()` 區塊沒有一個含 yield 或 LLM 串流），所以要開就
        # 只在 web 的 .env 開——批次腳本不讀 repo 根的 .env（sync unit 走
        # /etc/default/report-mark-sync），這個切分是天然的。
        db_idle_tx_timeout_ms=int(os.getenv("DB_IDLE_TX_TIMEOUT_MS", "0")),
        # 維運長查詢的豁免值，配 db.relax_statement_timeout() 使用（SET LOCAL，只影響
        # 當前交易）。0＝不限。存在的理由：ANALYZE research.report_chunk 要在 70 萬列
        # × vector(1024) 上抽樣，可能久於上面的 60s，而它是匯入流程的最後一步——被
        # 砍掉時前面的資料都已 commit，症狀只是 planner 統計靜默過期。
        db_maintenance_statement_timeout_ms=int(
            os.getenv("DB_MAINTENANCE_STATEMENT_TIMEOUT_MS", "0")
        ),
    )


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = _load()
    return _SETTINGS
