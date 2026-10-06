"""檢索回歸檢查（/api/admin/retrieval-regression）。限管理員＋`ops.read`，唯讀。

只讀 `scripts/retrieval_regression.py check`（timer 每日 07:40）最後一次寫的結果檔
（`data/health/retrieval_regression.json`）；判讀在 `app/services/retrieval_regression.py` 的 `section`。
**web 不跑檢索**：比對要載 BGE-M3、跑 18 題混合檢索，放進請求路徑等於讓管理頁跟問答搶 CPU 與記憶體。

服務函式經 `deps.retrieval_regression` 呼叫（測試的替換點）。
輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ValidationError

from web import authz, deps

logger = logging.getLogger(__name__)

router = APIRouter(dependencies=[Depends(authz.require_admin)])

_OPS_READ = Depends(authz.require_scope("ops.read"))

HealthStatus = Literal["ok", "warn", "fail", "unknown"]
# 與 app/services/retrieval_regression.py 的 OUTCOME_*／REASON_*／VERDICT_* 一致。
RegressionOutcome = Literal["ok", "degraded", "skipped", "error"]
RegressionReason = Literal[
    "db_unavailable", "low_memory", "sync_running", "no_baseline", "baseline_invalid", "incomparable",
    "dataset_invalid", "embed_failed", "query_failed", "unexpected",
]
RegressionVerdict = Literal["ok", "degraded", "incomparable"]


class RegressionReportRef(BaseModel):
    file_hash: str
    label: str | None = None


class RegressionQuestion(BaseModel):
    id: str
    question: str
    comparable: bool
    degraded: bool
    report_recall: float | None = None
    raw_report_recall: float | None = None
    chunk_recall: float | None = None
    rbo: float | None = None
    baseline_reports: int
    eligible_reports: int
    current_reports: int
    hidden_reports: int
    removed_reports: int
    excluded_new_reports: int
    lost_total: int
    gained_total: int
    lost: list[RegressionReportRef]
    gained: list[RegressionReportRef]


class RegressionBaseline(BaseModel):
    captured_at: str | None = None
    corpus_cutoff: str | None = None
    corpus_reports: int
    simulated_as_of: bool
    k: int
    dense_scan: int
    dataset_sha256: str | None = None


class RegressionThresholds(BaseModel):
    min_mean_recall: float
    min_question_recall: float
    max_degraded_questions: int


class RegressionSummary(BaseModel):
    verdict: RegressionVerdict
    questions: int
    comparable: int
    mean_report_recall: float | None = None
    mean_raw_report_recall: float | None = None
    mean_chunk_recall: float | None = None
    mean_rbo: float | None = None
    degraded_questions: int
    hidden_reports: int
    removed_reports: int
    excluded_new_reports: int


class RegressionComparison(BaseModel):
    finished_at: str
    duration_s: float | None = None
    baseline: RegressionBaseline | None = None
    dense_scan: int | None = None
    params_changed: bool
    thresholds: RegressionThresholds | None = None
    summary: RegressionSummary | None = None
    questions: list[RegressionQuestion]


class RetrievalRegressionResponse(BaseModel):
    status: HealthStatus
    available: bool
    unavailable_reason: str | None = None
    finished_at: str | None = None
    age_hours: float | None = None
    stale: bool
    exit_code: int | None = None
    outcome: RegressionOutcome | None = None
    reason: RegressionReason | None = None
    message: str | None = None
    comparison: RegressionComparison | None = None


# ── 輔助函式一律放在所有 @router.* 裝飾器之上 ──────────────────────────────


def _unavailable(reason: str) -> RetrievalRegressionResponse:
    return RetrievalRegressionResponse(status="unknown", available=False, unavailable_reason=reason, stale=False)


@router.get("/api/admin/retrieval-regression", response_model=RetrievalRegressionResponse, dependencies=[_OPS_READ])
async def get_retrieval_regression():
    """最後一次檢索回歸檢查的結果（讀檔）。還沒有結果檔時 `available=false`、`status=unknown`，不是錯誤。

    `status`：比對沒有劣化 → ok；劣化或無法比對 → fail；那次被略過（DB 連不上、記憶體不足、sync 在跑）→
    unknown；問答的 dense_scan 與基準不同、或最後一次真的比對過已超過 48 小時 → 至少 warn。
    略過或錯誤的那次不會蓋掉上一次的比對（`comparison.finished_at` 是那次比對的時間）。
    """
    data = await asyncio.to_thread(deps.retrieval_regression.section)
    try:
        return RetrievalRegressionResponse(**data)
    except ValidationError as exc:  # 結果檔被動過或版本錯配：當作讀不到，不回 500
        logger.warning("檢索回歸結果檔格式不符：%s", exc.errors()[:3])
        return _unavailable("corrupt:schema")
