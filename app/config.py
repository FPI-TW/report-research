"""集中設定：一次讀取 ASK_*/REPORT_* 環境變數。

各服務模組保留原常數名，改由 get_settings() 取值，避免同一 env 在多檔重複讀取
（如 ASK_DENSE_SCAN 原本 answer.py 與 report.py 各定義一次）。retrieval.py 的
非-env 字面量（BAND_WIDTH 等）不在此收斂範圍（見 M6）。
"""

import os
from dataclasses import dataclass


def _flag(name: str, default: str) -> bool:
    return os.getenv(name, default) not in ("0", "false", "False", "")


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
    ask_enable_web: bool
    # intent.py
    ask_intent_model: str
    ask_intent_timeout: float
    ask_condense_model: str
    ask_condense_timeout: float
    # report.py
    report_model: str
    report_deep_k: int
    report_max_reports: int
    report_max_passages: int
    report_max_context_chars: int
    report_timeout: float
    reports_dir: str
    report_enable_web: bool
    report_thin_coverage: int
    # report_gate.py
    report_min_cited: int
    report_long_answer_chars: int
    # rerank.py（M2）
    ask_rerank_enabled: bool
    ask_rerank_candidates: int
    ask_rerank_timeout: float
    report_rerank_enabled: bool
    report_rerank_candidates: int
    report_rerank_timeout: float
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
    # report 檢索增強 / query_planner（M6）—— M6 里程碑只在本區段內加鍵
    report_planner_model: str
    report_planner_timeout: float
    report_planner_max_subqueries: int
    report_fanout_concurrency: int
    report_subquery_dense_scan: int
    report_total_candidates: int
    report_mmr_enabled: bool
    report_mmr_lambda: float
    report_mmr_max_per_source: int
    report_mmr_max_per_month: int
    # 逐節生成 / report_writer（M7 里程碑）—— M7 里程碑只在本區段內加鍵
    report_sectioned_enabled: bool
    report_outline_timeout: float
    report_outline_max_subsections: int
    report_section_timeout: float
    report_section_max_reports: int
    report_section_max_passages: int
    report_section_max_context_chars: int
    report_section_rerank_candidates: int
    report_section_retry: int


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
        ask_intent_model=intent_model,
        ask_intent_timeout=float(os.getenv("ASK_INTENT_TIMEOUT", "20")),
        ask_condense_model=os.getenv("ASK_CONDENSE_MODEL", intent_model),
        ask_condense_timeout=float(os.getenv("ASK_CONDENSE_TIMEOUT", "20")),
        report_model=os.getenv("REPORT_MODEL", "claude-sonnet-5"),
        report_deep_k=int(os.getenv("REPORT_DEEP_K", "30")),
        report_max_reports=int(os.getenv("REPORT_MAX_REPORTS", "25")),
        report_max_passages=int(os.getenv("REPORT_MAX_PASSAGES", "6")),
        report_max_context_chars=int(os.getenv("REPORT_MAX_CONTEXT_CHARS", "40000")),
        report_timeout=float(os.getenv("REPORT_TIMEOUT", "600")),
        reports_dir=os.getenv("REPORTS_DIR", "data/reports"),
        report_enable_web=_flag("REPORT_ENABLE_WEB", "1"),
        report_thin_coverage=int(os.getenv("REPORT_THIN_COVERAGE", "8")),
        report_min_cited=int(os.getenv("REPORT_MIN_CITED", "3")),
        report_long_answer_chars=int(os.getenv("REPORT_LONG_ANSWER_CHARS", "400")),
        ask_rerank_enabled=_flag("ASK_RERANK_ENABLED", "1"),
        ask_rerank_candidates=int(os.getenv("ASK_RERANK_CANDIDATES", "50")),
        # per-path 逾時：prod 實測（20 核 CPU）50 對 ~34s、120 對 ~93s；預設須蓋過
        # 實測值 + 忙碌餘裕，否則 rerank 靜默 fail-open 形同全關（M1b 基準線 10/10 逾時）。
        ask_rerank_timeout=float(os.getenv("ASK_RERANK_TIMEOUT", "60")),
        report_rerank_enabled=_flag("REPORT_RERANK_ENABLED", "1"),
        report_rerank_candidates=int(os.getenv("REPORT_RERANK_CANDIDATES", "120")),
        report_rerank_timeout=float(os.getenv("REPORT_RERANK_TIMEOUT", "180")),
        rerank_model=os.getenv("RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
        trusted_data_enabled=_flag("TRUSTED_DATA_ENABLED", "1"),
        # agentic_qa / query_planner（M5）—— M5 里程碑只在本區段內加鍵
        qa_planner_model=os.getenv("QA_PLANNER_MODEL", intent_model),
        qa_planner_timeout=float(os.getenv("QA_PLANNER_TIMEOUT", "20")),
        qa_planner_max_subqueries=int(os.getenv("QA_PLANNER_MAX_SUBQUERIES", "3")),
        qa_max_rounds=int(os.getenv("QA_MAX_ROUNDS", "2")),
        qa_agentic_enabled=_flag("QA_AGENTIC_ENABLED", "1"),
        # 迴圈總逾時（秒）；不含第一輪檢索與最終作答串流，於 asyncio.wait_for 落實。
        qa_agentic_timeout=float(os.getenv("QA_AGENTIC_TIMEOUT", "90")),
        qa_subquery_max_reports=int(os.getenv("QA_SUBQUERY_MAX_REPORTS", "5")),
        # report 檢索增強 / query_planner（M6）—— M6 里程碑只在本區段內加鍵
        report_planner_model=os.getenv("REPORT_PLANNER_MODEL", intent_model),
        report_planner_timeout=float(os.getenv("REPORT_PLANNER_TIMEOUT", "30")),
        report_planner_max_subqueries=int(os.getenv("REPORT_PLANNER_MAX_SUBQUERIES", "8")),
        report_fanout_concurrency=int(os.getenv("REPORT_FANOUT_CONCURRENCY", "3")),
        # 子查詢掃描深度刻意低於呼叫端 dense_scan：原題恆用呼叫端值，子查詢走此淺掃
        report_subquery_dense_scan=int(os.getenv("REPORT_SUBQUERY_DENSE_SCAN", "200")),
        report_total_candidates=int(os.getenv("REPORT_TOTAL_CANDIDATES", "600")),
        report_mmr_enabled=_flag("REPORT_MMR_ENABLED", "1"),
        report_mmr_lambda=float(os.getenv("REPORT_MMR_LAMBDA", "0.7")),
        # 0＝不限額；source=None 不計入配額
        report_mmr_max_per_source=int(os.getenv("REPORT_MMR_MAX_PER_SOURCE", "6")),
        # 預設關：財報季主題天然集中同月，硬性月配額誤傷風險高
        report_mmr_max_per_month=int(os.getenv("REPORT_MMR_MAX_PER_MONTH", "0")),
        # 逐節生成 / report_writer（M7 里程碑）—— M7 里程碑只在本區段內加鍵
        report_sectioned_enabled=_flag("REPORT_SECTIONED_ENABLED", "1"),
        report_outline_timeout=float(os.getenv("REPORT_OUTLINE_TIMEOUT", "45")),
        report_outline_max_subsections=int(
            os.getenv("REPORT_OUTLINE_MAX_SUBSECTIONS", "5")
        ),
        # 逐節逾時／配額：刻意低於整份（25/6/40000/120），控 N 節串行延遲
        report_section_timeout=float(os.getenv("REPORT_SECTION_TIMEOUT", "150")),
        report_section_max_reports=int(os.getenv("REPORT_SECTION_MAX_REPORTS", "8")),
        report_section_max_passages=int(os.getenv("REPORT_SECTION_MAX_PASSAGES", "4")),
        report_section_max_context_chars=int(
            os.getenv("REPORT_SECTION_MAX_CONTEXT_CHARS", "12000")
        ),
        report_section_rerank_candidates=int(
            os.getenv("REPORT_SECTION_RERANK_CANDIDATES", "40")
        ),
        report_section_retry=int(os.getenv("REPORT_SECTION_RETRY", "1")),
    )


_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    global _SETTINGS
    if _SETTINGS is None:
        _SETTINGS = _load()
    return _SETTINGS
