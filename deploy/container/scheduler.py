"""容器的排程角色：把框架該有的週期工作從 systemd timer 搬進容器（supercronic 讀 crontab）。

`deploy/systemd/*.timer` 每一支都必須在這裡有歸屬（`tests/test_container_deploy.py` 對帳，也對
`docs/CONTAINER_DEPLOYMENT.md` 的對照表）：

- `JOBS`：進框架的排程。排程時間與對應 timer 的 OnCalendar 相同（同一個測試逐支換算比對），在容器的
  `TZ`（預設 Asia/Taipei）下解讀。`default_enabled=False` 的要部署方明確開啟（理由寫在 note）。
  `unit=None` 的是**框架專屬**、沒有對應 timer 的工作（例如 `connectors`：廷豐主機的對應是 sync 殼，
  它屬主機層；`backfill-tiers`：首次導入的第三層背景補齊，由 profile 開），文件另有一張表對帳。
- `WORKER_UNITS`：改由常駐的 `worker` 服務（迴圈）取代的 timer。
- `HOST_LAYER`：屬廷豐主機層、不進框架的 timer（探針、事件、信標、NAS、LINE bot、主機監控匯入、來源同步）。

開關：`SCHEDULER_DISABLE`／`SCHEDULER_ENABLE`（逗號分隔的工作名）。名字打錯就拒絕啟動——拼錯的
停用名單等於沒停用，那正是這類旋鈕靜默失效的方式。

互斥：各工作沿用 repo 既有的鎖（LLM 批次的 `data/.claude_cli.lock`、上傳 worker 的整輪鎖等，都是
`data/` 底下的 flock）。web、worker、scheduler 掛同一個 data volume，flock 是核心層級、以 inode 為準，
跨容器照樣互斥。supercronic 預設不讓同一個工作重疊（上一輪還在跑就跳過這一次）。
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import Mapping

DISABLE_ENV = "SCHEDULER_DISABLE"
ENABLE_ENV = "SCHEDULER_ENABLE"


@dataclass(frozen=True)
class Job:
    name: str
    cron: str  # 分 時 日 月 週
    command: tuple[str, ...]
    unit: str | None  # 對應的 systemd timer；None＝框架專屬（沒有 timer）
    default_enabled: bool = True
    note: str = ""


def _py(script: str, *args: str) -> tuple[str, ...]:
    return ("python", f"scripts/{script}", *args)


JOBS: tuple[Job, ...] = (
    Job("backfill", "0 1 * * *",
        _py("backfill_extraction.py", "--extractor", "pdfplumber", "--max-minutes", "240"),
        "report-mark-backfill.timer", note="抽取版本回填（零 LLM；會載入嵌入模型）"),
    Job("analytics-rollup", "20 2 * * *", _py("analytics_rollup.py"), "report-mark-analytics-rollup.timer"),
    Job("backup", "30 3 * * *", ("bash", "scripts/db_backup.sh"), "report-mark-backup.timer",
        note="不可重建的表；容器裡直接連 DB（DB_BACKUP_VIA=direct），落點是備份 volume"),
    Job("audit-anchor", "15 4 * * *", _py("audit_anchor.py"), "report-mark-audit-anchor.timer",
        note="稽核雜湊鏈錨點寫在備份 volume"),
    Job("replay-deletions", "30 4 * * *", _py("replay_deletions.py"), "report-mark-replay-deletions.timer"),
    Job("security-retention", "45 4 * * *", _py("security_retention.py"), "report-mark-security-retention.timer"),
    Job("schema-check", "20 5 * * *", _py("schema_baseline.py", "scheduled"), "report-mark-schema-check.timer"),
    Job("db-audit", "45 8 * * *", _py("db_audit.py"), "report-mark-audit.timer"),
    Job("db-snapshot", "40 * * * *", _py("db_snapshot.py"), "report-mark-db-snapshot.timer"),
    Job("delete-accounts", "0 * * * *", _py("execute_deletions.py"), "report-mark-delete-accounts.timer",
        note="tombstone 寫在備份 volume"),
    Job("retrieval-regression", "40 7 * * *", _py("retrieval_regression.py", "check"),
        "report-mark-retrieval-regression.timer", default_enabled=False,
        note="要先對這個部署擷取一次基準（capture）；會載入嵌入模型"),
    Job("connectors", "*/15 * * * *", _py("connector_sync.py"), None,
        note="連接器輪詢入庫＋下游批次（sync 鏈第 4 段起）；各連接器依 profile 的 poll_minutes 決定到期，"
             "沒有啟用的連接器就是 no-op"),
    Job("backfill-tiers", "50 * * * *", _py("backfill_tiers.py", "--tier", "3", "--scheduled"), None,
        note="第三層背景補齊（首次導入的分層回填）；開關在 profile 的 [backfill].background_enabled（預設關＝no-op），"
             "每日 token 與每輪份數上限也在 profile。各批次照舊取 LLM 批次鎖"),
    Job("r2-reconcile", "0 7 * * 1", _py("reconcile_object_storage.py", "--dry-run", "--kind", "all"),
        "report-mark-r2-reconcile.timer", default_enabled=False,
        note="只在 OBJECT_STORAGE_MODE 不是 local 時有意義（物件儲存是外連，預設關）"),
)

WORKER_UNITS: dict[str, str] = {
    "report-mark-upload.timer": "常駐的 worker 服務（每 UPLOAD_WORKER_INTERVAL_SECONDS 秒一輪）",
}

HOST_LAYER: dict[str, str] = {
    "report-mark-sync.timer": "來源同步（NAS 掛載／R2 inbox）是廷豐的來源；框架的對應是 scheduler 的 connectors",
    "report-mark-freshness.timer": "判讀 sync 殼的心跳與下游批次，隨 sync 一起",
    "report-mark-beacon.timer": "主機信標",
    "report-mark-load-observations.timer": "主機監控 spool（資源取樣、P5 事件）匯入",
    "report-mark-rollup-observations.timer": "主機監控觀測的保留與聚合，隨監控匯入一起",
    "report-mark-health.timer": "web 探針（P5）",
    "report-mark-incident.timer": "事件處理（P5／Slack）",
    "report-mark-container-health.timer": "容器探針（P5）",
    "report-mark-container-incident.timer": "事件處理（P5／Slack）",
    "report-mark-edge-health.timer": "邊緣（nginx＋cloudflared）探針",
    "report-mark-edge-incident.timer": "事件處理（P5／Slack）",
    "report-mark-feed-health.timer": "來源主機信標探針",
    "report-mark-feed-incident.timer": "事件處理（P5／Slack）",
    "report-mark-host-health.timer": "主機資源探針",
    "report-mark-host-incident.timer": "事件處理（P5／Slack）",
    "report-mark-linebot-health.timer": "LINE bot 探針",
    "report-mark-linebot-incident.timer": "事件處理（P5／Slack）",
    "report-mark-security-health.timer": "安全事件探針（P5）",
    "report-mark-security-incident.timer": "事件處理（P5／Slack）",
}


class SchedulerConfigError(ValueError):
    pass


def _names(raw: str | None) -> set[str]:
    return {s.strip() for s in (raw or "").split(",") if s.strip()}


def selected_jobs(env: Mapping[str, str]) -> list[Job]:
    """依預設與兩個開關挑出要排的工作。未知名稱、同一個名字同時開又關都拒絕。"""
    known = {j.name for j in JOBS}
    disable, enable = _names(env.get(DISABLE_ENV)), _names(env.get(ENABLE_ENV))
    unknown = sorted((disable | enable) - known)
    if unknown:
        raise SchedulerConfigError(
            f"{DISABLE_ENV}／{ENABLE_ENV} 有不認得的工作名：{'、'.join(unknown)}（可用：{'、'.join(sorted(known))}）")
    both = sorted(disable & enable)
    if both:
        raise SchedulerConfigError(f"同一個工作同時列在 {DISABLE_ENV} 與 {ENABLE_ENV}：{'、'.join(both)}")
    return [j for j in JOBS if j.name not in disable and (j.default_enabled or j.name in enable)]


def render_crontab(jobs: list[Job]) -> str:
    lines = ["# 由 deploy/container/scheduler.py 產生；不要手改（容器重啟就重新產生）"]
    for j in jobs:
        lines.append(f"# {j.name}（取代 {j.unit}）" if j.unit else f"# {j.name}（框架專屬，沒有對應的 systemd timer）")
        lines.append(f"{j.cron} {shlex.join(j.command)}")
    return "\n".join(lines) + "\n"
