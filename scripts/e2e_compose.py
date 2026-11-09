"""容器端到端測試：以示範券商 profile（`profiles/demo`）把整套 compose 從空庫跑到問答，跑完拆掉。

不是 pytest 收集的測試（放在 `scripts/`，`uv run pytest` 不會跑它）：要 docker、要一份 build 好的映像、
要幾 GB 記憶體與十幾分鐘。CI 由獨立的 `.github/workflows/e2e.yml` 跑（不是必要檢查）。

流程（每一步失敗就停，最後一律 `docker compose down -v`，`--keep` 除外）：

1. 合成語料：`scripts/demo_corpus.py`（固定種子的虛構 PDF＋側檔，預期值寫在 `expected.json`）。
2. 獨立的 compose project（`-p e2e-<亂數>`、不衝突的埠、暫存目錄當 project directory 與備份落點），
   `deploy/compose/docker-compose.yml`＋`deploy/demo/docker-compose.demo.yml`（假 LLM＋moto S3）。
3. `run --rm migrate`（空庫）→ `create-admin --password-stdin` → 語料上傳到 S3（boto3）。
4. `scripts/connector_sync.py --force`（scheduler 角色的容器）→ `scripts/backfill_tiers.py --tier 3 --scheduled`。
5. 起 web，驗證：入庫份數、側檔欄位生效、錯誤側檔進失敗清單、摘要／摘錄／訊號有產出、分層回填、`/api/search`、
   `/api/ask` 的 SSE 走完且引用合法（假 LLM 刻意夾一個不存在的編號）、閱讀頁 `/text`、`/api/branding` 是示範品牌、
   未啟用的市場回 422、`/metrics`、`/readyz`。

用法：
  uv run python scripts/e2e_compose.py --build                 # 先 build 映像（重用 BuildKit 快取）再跑
  uv run python scripts/e2e_compose.py --image report-mark:e2e # 用已 build 好的映像
  uv run python scripts/e2e_compose.py --keep                  # 跑完不拆（自己 docker compose -p <名稱> down -v）
  E2E_LLM_PROVIDER=openai E2E_LLM_API_KEY=… E2E_LLM_MODEL=… uv run python scripts/e2e_compose.py --real-llm

`--real-llm`：疊 `deploy/demo/docker-compose.real-llm.yml`，LLM 設定取自 `E2E_LLM_PROVIDER`（預設 openai）、
`E2E_LLM_API_KEY`、`E2E_LLM_BASE_URL`（選填）、`E2E_LLM_MODEL`；金鑰只寫進暫存目錄的 `.env`（0600），不進 argv。
真的模型回答不是決定性的，所以「必須出現無效引用標記」這一項改成「引用只能落在來源編號內」。

退出碼：0 全部通過；1 有檢查失敗；2 環境不符（沒有 docker、映像不存在）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE_COMPOSE = ROOT / "deploy" / "compose" / "docker-compose.yml"
DEMO_COMPOSE = ROOT / "deploy" / "demo" / "docker-compose.demo.yml"
REAL_LLM_COMPOSE = ROOT / "deploy" / "demo" / "docker-compose.real-llm.yml"
DOCKERFILE = ROOT / "deploy" / "container" / "Dockerfile"
PROFILE_DIR = ROOT / "profiles" / "demo"
DEFAULT_IMAGE = "report-mark:e2e"
ADMIN_USER = "e2e-admin"
BUCKET = "demo-research"
PREFIX = "research/"
TAIPEI = timezone(timedelta(hours=8))
INVALID_CITATION = "（無效引用）"

EXIT_OK, EXIT_FAILED, EXIT_ENV = 0, 1, 2


def log(msg: str) -> None:
    print(f"[e2e {datetime.now():%H:%M:%S}] {msg}", flush=True)


class StepFailed(RuntimeError):
    pass


@dataclass
class Checks:
    results: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.results.append((name, bool(ok), detail))
        log(f"{'PASS' if ok else 'FAIL'} {name}{'：' + detail if detail else ''}")
        return bool(ok)

    @property
    def failed(self) -> list[str]:
        return [n for n, ok, _ in self.results if not ok]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# compose 的變數代換以 shell 環境優先於 project directory 的 .env：呼叫端環境裡同名的鍵（例如為了安全而設的
# REPORT_MARK_DB_URL）會蓋掉這裡寫的值，容器就連到別的地方去。交給 docker compose 的環境一律剔除這些鍵。
_SHADOWING_KEYS = ("REPORT_MARK_DB_URL", "COMPOSE_PROJECT_NAME", "COMPOSE_FILE", "COMPOSE_PROFILES")


def compose_env(env_values: dict, environ=None) -> dict:
    environ = os.environ if environ is None else environ
    return {k: v for k, v in environ.items()
            if k not in env_values and k not in _SHADOWING_KEYS and not k.startswith("DEMO_")}


class Compose:
    def __init__(self, project: str, work: Path, files: list[Path]) -> None:
        self.project = project
        self.env: dict | None = None  # write_env 之後設成 compose_env(...)
        self.base = ["docker", "compose", "-p", project, "--project-directory", str(work)]
        for f in files:
            self.base += ["-f", str(f)]

    def run(self, *args: str, input: str | None = None, timeout: float = 600, check: bool = True,
            capture: bool = False) -> subprocess.CompletedProcess:
        cmd = [*self.base, *args]
        log("$ docker compose " + " ".join(args))
        res = subprocess.run(cmd, input=input, text=True, timeout=timeout, capture_output=capture,
                             env=self.env if self.env is not None else compose_env({}))
        if check and res.returncode != 0:
            tail = ((res.stdout or "") + (res.stderr or ""))[-3000:] if capture else ""
            raise StepFailed(f"docker compose {' '.join(args[:3])} 退出碼 {res.returncode}\n{tail}")
        return res

    def psql(self, sql: str) -> str:
        res = self.run("exec", "-T", "postgres", "psql", "-U", "research", "-d", "research", "-At",
                       "-v", "ON_ERROR_STOP=1", "-c", sql, capture=True, timeout=120)
        return res.stdout.strip()

    def sh(self, service: str, script: str, *, running: bool = False) -> str:
        """在 service 裡跑一段 shell：`running` 用 exec 進執行中的容器，否則起一個一次性的（同一份 data volume）。"""
        head = ["exec", "-T", service] if running else ["run", "--rm", "-T", "--no-deps", service]
        return self.run(*head, "sh", "-c", script, capture=True, timeout=180).stdout


# ── 環境 ───────────────────────────────────────────────────────────────


def write_env(work: Path, *, image: str, web_port: int, s3_port: int, real_llm: bool, rerank: bool) -> dict:
    backups = work / "backups"
    backups.mkdir()
    backups.chmod(0o777)  # 容器裡是 UID 10001；暫存目錄不能 chown（不用 sudo），只好放寬
    values = {
        "REPORT_MARK_IMAGE": image,
        "TZ": "Asia/Taipei",
        "POSTGRES_PASSWORD": secrets.token_hex(16),
        "BACKUP_HOST_DIR": str(backups),
        "PROFILE_HOST_DIR": str(PROFILE_DIR),
        "REPORT_MARK_SESSION_SECRET": secrets.token_hex(32),
        "WEB_PUBLISH": f"127.0.0.1:{web_port}",
        "DEMO_S3_PUBLISH": f"127.0.0.1:{s3_port}",
        "DEMO_S3_BUCKET": BUCKET,
        "ASK_ENABLE_WEB": "0",
        "OBJECT_STORAGE_MODE": "local",
        "UPLOAD_ENABLED": "0",
        # 記憶體：web 常駐嵌入＋reranker；CI runner 與共用主機上預設關 rerank（--rerank 打開）。
        "ASK_RERANK_ENABLED": "1" if rerank else "0",
        "SCHEMA_GATE_WAIT_SECONDS": "180",
    }
    if real_llm:
        provider = os.getenv("E2E_LLM_PROVIDER") or "openai"
        key = os.getenv("E2E_LLM_API_KEY", "")
        model = os.getenv("E2E_LLM_MODEL", "")
        if not key or not model:
            raise StepFailed("--real-llm 需要 E2E_LLM_API_KEY 與 E2E_LLM_MODEL")
        values["LLM_PROVIDER"] = provider
        values["LLM_DEFAULT_MODEL"] = model
        base_url = os.getenv("E2E_LLM_BASE_URL", "")
        if provider == "openai_compatible":
            values.update(OPENAI_COMPATIBLE_API_KEY=key, OPENAI_COMPATIBLE_MODELS=model,
                          OPENAI_COMPATIBLE_BASE_URL=base_url)
        else:
            values["OPENAI_API_KEY" if provider == "openai" else f"{provider.upper()}_API_KEY"] = key
            if base_url:
                values["OPENAI_BASE_URL"] = base_url
    env = work / ".env"
    env.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
    env.chmod(0o600)
    return values


def upload_corpus(corpus: Path, s3_port: int) -> int:
    import boto3
    from botocore.config import Config

    s3 = boto3.client("s3", endpoint_url=f"http://127.0.0.1:{s3_port}", aws_access_key_id="moto",
                      aws_secret_access_key="moto", region_name="us-east-1",
                      config=Config(s3={"addressing_style": "path"}))
    s3.create_bucket(Bucket=BUCKET)
    n = 0
    for path in sorted(p for p in corpus.rglob("*") if p.is_file()):
        rel = path.relative_to(corpus).as_posix()
        extra = {"Metadata": {"mtime": str(int(path.stat().st_mtime))}}
        s3.upload_file(str(path), BUCKET, PREFIX + rel, ExtraArgs=extra)
        n += 1
    return n


# ── HTTP ───────────────────────────────────────────────────────────────


def parse_sse(raw: str) -> list[tuple[str, object]]:
    events = []
    for block in raw.split("\n\n"):
        name, data = None, []
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data.append(line[6:])
        if name:
            events.append((name, json.loads("\n".join(data)) if data else None))
    return events


def http_checks(c: Checks, compose: Compose, base: str, password: str, *, real_llm: bool) -> None:
    import httpx

    origin = {"Origin": base}
    # 逾時放寬：第一個檢索要冷啟動嵌入（CPU），共用主機或 CI runner 忙的時候會超過一分鐘。
    with httpx.Client(base_url=base, timeout=300, follow_redirects=False) as h:
        r = h.get("/readyz")
        c.check("/readyz 200", r.status_code == 200 and r.json() == {"status": "ok"}, f"{r.status_code} {r.text[:80]}")

        b = h.get("/api/branding").json()
        c.check("/api/branding 是示範品牌",
                b.get("institution_name") == "示範證券（虛構）" and b.get("product_name") == "研報洞察示範平台"
                and (b.get("colors") or {}).get("light", {}).get("accent_strong") == "#13857f"
                and bool(b.get("logo_light_url")), json.dumps(b, ensure_ascii=False)[:200])
        if b.get("favicon_url"):
            fav = h.get(b["favicon_url"])
            c.check("favicon 是 profile 的 SVG", fav.status_code == 200 and fav.headers.get("content-type", "")
                    .startswith("image/svg+xml"), str(fav.status_code))

        r = h.post("/login", data={"username": ADMIN_USER, "password": password, "next": "/app"}, headers=origin)
        c.check("登入", r.status_code in (302, 303) and "error" not in r.headers.get("location", ""),
                f"{r.status_code} {r.headers.get('location')}")

        m = h.get("/api/markets").json()
        c.check("/api/markets 只有啟用的子集", m.get("markets") == ["TW", "US", "MACRO"], str(m.get("markets")))

        r = h.get("/api/search", params={"q": "台積電 評等 目標價"})
        data = r.json() if r.status_code == 200 else {}
        c.check("/api/search 有結果", r.status_code == 200 and data.get("total", 0) > 0 and data.get("results"),
                f"{r.status_code} total={data.get('total')}")

        from scripts.demo_corpus import BROKERS

        names = {x.get("source") for x in data.get("results") or []}
        c.check("發行機構顯示 profile 名單的名稱", bool(names - {None}) and names <= set(BROKERS.values()) | {None},
                str(sorted(n for n in names if n)))

        if not real_llm:
            r = h.get("/api/radar/instruments", params={"market": "US"})
            codes = {x.get("instrument_code") for x in (r.json().get("items") or [])} if r.status_code == 200 else set()
            c.check("美股標的進雷達", r.status_code == 200 and "NVDA" in codes, f"{r.status_code} {sorted(codes)}")
        r = h.get("/api/brief/latest")
        c.check("/api/brief/latest 有簡報", r.status_code == 200 and r.json().get("status") == "ready",
                f"{r.status_code} {r.text[:80]}")

        r = h.get("/api/search", params={"q": "騰訊", "market": "HK"})
        c.check("未啟用的市場被拒（422 market_disabled）",
                r.status_code == 422 and r.json().get("code") == "market_disabled", f"{r.status_code} {r.text[:120]}")

        r = h.post("/api/ask", json={"question": "台積電最新的評等與目標價是多少？"}, headers=origin, timeout=300)
        events = parse_sse(r.text) if r.status_code == 200 else []
        kinds = [e for e, _ in events]
        sources = next((d for e, d in events if e == "sources"), []) or []
        answer = "".join(d for e, d in events if e == "token" and isinstance(d, str))
        done = next((d for e, d in events if e == "done"), None)
        if isinstance(done, dict) and done.get("answer"):
            answer = done["answer"]
        numbers = {s.get("n") for s in sources if isinstance(s, dict)}
        cited = {int(n) for n in re.findall(r"\[(\d+)\]", answer)}
        c.check("/api/ask SSE 走完（sources→token→done，followups 在 done 之後）",
                r.status_code == 200 and {"sources", "token", "done"} <= set(kinds) and "error" not in kinds,
                f"{r.status_code} events={kinds[:3]}…{kinds[-2:]} sources={len(sources)}")
        c.check("問答引用只落在來源編號內", bool(cited) and cited <= numbers,
                f"cited={sorted(cited)} sources={sorted(n for n in numbers if n is not None)}")
        if not real_llm:
            c.check("無效引用在串流中被替換", INVALID_CITATION in answer and "[99]" not in answer, answer[-80:])

        file_hash = compose.psql("SELECT file_hash FROM research.research_report WHERE market='TW' "
                                 "ORDER BY report_date DESC NULLS LAST LIMIT 1")
        r = h.get(f"/api/reading/{file_hash}/text")
        text = r.json().get("text", "") if r.status_code == 200 else ""
        c.check("閱讀頁 /text 可讀", r.status_code == 200 and "虛構" in text, f"{r.status_code} len={len(text)}")

    metrics = compose.sh("web", "python -c \"import urllib.request as u; "
                                "print(u.urlopen('http://127.0.0.1:8097/metrics', timeout=10).read().decode())\"",
                         running=True)
    c.check("/metrics（容器內 loopback）", "report_mark_http_requests_total" in metrics
            and "report_mark_build_info" in metrics, f"{len(metrics)} bytes")


# ── DB 與檔案 ──────────────────────────────────────────────────────────


def corpus_checks(c: Checks, compose: Compose, expected: dict, *, real_llm: bool = False) -> None:
    n = int(compose.psql("SELECT count(*) FROM research.research_report"))
    c.check("入庫份數", n == expected["ingested"], f"{n}／預期 {expected['ingested']}")

    rows = compose.psql("SELECT file_name, coalesce(title,''), coalesce(title_source,''), coalesce(source,''), "
                        "coalesce(market,''), array_to_string(stock_targets, ';') FROM research.research_report")
    by_name = {}
    for line in rows.splitlines():
        name, title, title_source, source, market, targets = line.split("|")
        by_name[name] = (title, title_source, source, market, targets)
    bad_titles = [f for f, t in expected["metadata_titles"].items()
                  if by_name.get(Path(f).name, ("", ""))[:2] != (t, "metadata")]
    c.check("側檔的 title 生效（title_source=metadata）", not bad_titles, f"不符：{bad_titles[:3]}")
    bad_sources = [f for f, s in expected["metadata_sources"].items() if by_name.get(Path(f).name, ("",) * 3)[2] != s]
    c.check("側檔的 source 生效", not bad_sources, f"不符：{bad_sources[:3]}")
    bad_markets = [f for f, m in expected["markets"].items() if by_name.get(Path(f).name, ("",) * 4)[3] != m]
    c.check("市場（側檔或標註）", not bad_markets, f"不符：{bad_markets[:3]}")
    # profile 的 [sources] 名單（沒有側檔的研報）與 [instruments] 代號格式（標註出的美股 ticker）。
    bad_fn_sources = [f for f, s in expected.get("filename_sources", {}).items()
                      if by_name.get(Path(f).name, ("",) * 3)[2] != s]
    c.check("沒有側檔的研報由 profile 名單從檔名推得發行機構", not bad_fn_sources, f"不符：{bad_fn_sources[:3]}")
    if not real_llm:  # 真實 LLM 照標註 prompt 只給 4 碼代碼，美股 ticker 不保證出現
        missing_targets = [f for f, code in expected.get("tagged_stock_targets", {}).items()
                           if code not in by_name.get(Path(f).name, ("",) * 5)[4].split(";")]
        c.check("標註出的個股代號依 profile 格式保留（含美股 ticker）", not missing_targets,
                f"缺：{missing_targets[:3]}")
    hk = [v for v in by_name.values() if v[3] == "HK"]
    c.check("未啟用市場的研報照常入庫（子集不是可見性）", len(hk) == 1, f"HK={len(hk)}")
    meta_n = int(compose.psql("SELECT count(*) FROM research.report_metadata"))
    with_sidecar = expected["ingested"] - len(expected["no_sidecar"])
    c.check("research.report_metadata 保存側檔", meta_n == with_sidecar, f"{meta_n}／預期 {with_sidecar}")

    failures = compose.sh("web", "cat /app/data/connectors/failures.log 2>/dev/null || true")
    missing = [f for f in expected["sidecar_invalid"] if f not in failures]
    c.check("錯誤側檔進失敗清單", not missing and failures.count("\n") >= len(expected["sidecar_invalid"]),
            f"缺：{missing}")
    stats_raw = compose.sh("web", "cat /app/data/connectors/last_stats 2>/dev/null || true")
    stats = dict(line.split("=", 1) for line in stats_raw.splitlines() if "=" in line)
    c.check("last_stats 的 sidecar_invalid", stats.get("sidecar_invalid") == str(len(expected["sidecar_invalid"])),
            " ".join(f"{k}={v}" for k, v in stats.items()))

    tier2 = int(compose.psql("SELECT count(*) FROM research.research_report WHERE summary IS NOT NULL "
                             "AND report_date >= current_date - interval '12 months'"))
    c.check("第二層有摘要（連接器排程）", tier2 > 0, f"{tier2} 份")
    takeaways = int(compose.psql("SELECT count(*) FROM research.report_takeaway WHERE quote_start IS NOT NULL"))
    c.check("摘錄有產出且錨定", takeaways > 0, f"{takeaways} 條已錨定")
    signals = int(compose.psql("SELECT count(*) FROM research.report_signal"))
    c.check("訊號有產出", signals > 0, f"{signals} 列")
    if not real_llm:
        us = set(compose.psql("SELECT DISTINCT instrument_code FROM research.report_signal WHERE market = 'US'")
                 .split())
        c.check("美股標的有訊號", set(expected.get("radar_us", [])) <= us, f"US={sorted(us)}")
    briefs = int(compose.psql("SELECT count(*) FROM research.report_brief"))
    c.check("每日簡報已產生（profile 的 [brief].after_hour=0）", briefs == 1, f"{briefs} 列")
    titles = int(compose.psql("SELECT count(*) FROM research.research_report WHERE title IS NOT NULL"))
    c.check("標題（側檔＋LLM）", titles >= len(expected["metadata_titles"]), f"{titles} 份有標題")


def tier3_check(c: Checks, compose: Compose) -> None:
    old = int(compose.psql("SELECT count(*) FROM research.research_report WHERE report_date < current_date - "
                           "interval '12 months'"))
    done = int(compose.psql("SELECT count(*) FROM research.research_report WHERE summary IS NOT NULL AND "
                            "report_date < current_date - interval '12 months'"))
    c.check("第三層由背景補齊補上摘要", old > 0 and done == old, f"{done}／{old}")


# ── 主流程 ─────────────────────────────────────────────────────────────


def build_image(image: str) -> None:
    def git(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=ROOT, text=True, capture_output=True).stdout.strip()

    clean = "1" if not git("status", "--porcelain") else "0"
    cmd = ["docker", "build", "-f", str(DOCKERFILE), "-t", image,
           "--build-arg", f"BUILD_COMMIT={git('rev-parse', 'HEAD')}",
           "--build-arg", f"BUILD_VERSION={git('describe', '--tags', '--always')}",
           "--build-arg", f"BUILD_TREE_CLEAN={clean}", str(ROOT)]
    log("$ " + " ".join(cmd[:5]) + " …")
    if subprocess.run(cmd).returncode != 0:
        raise StepFailed("docker build 失敗")


def run(args) -> int:
    if shutil.which("docker") is None:
        log("找不到 docker")
        return EXIT_ENV
    if args.build:
        build_image(args.image)
    if subprocess.run(["docker", "image", "inspect", args.image], capture_output=True).returncode != 0:
        log(f"映像 {args.image} 不存在（加 --build，或先 docker build）")
        return EXIT_ENV

    root = Path(args.work_dir) if args.work_dir else None
    if root:
        root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="report-mark-e2e-", dir=root))
    project = args.project or f"e2e-{secrets.token_hex(3)}"
    checks = Checks()
    web_port, s3_port = free_port(), free_port()
    files = [BASE_COMPOSE, DEMO_COMPOSE] + ([REAL_LLM_COMPOSE] if args.real_llm else [])
    compose = Compose(project, work, files)
    started = time.monotonic()
    log(f"project={project} work={work} web=127.0.0.1:{web_port} s3=127.0.0.1:{s3_port}")
    try:
        from scripts import demo_corpus

        anchor = date.fromisoformat(args.anchor_date) if args.anchor_date else datetime.now(TAIPEI).date()
        if args.corpus:
            corpus_root = Path(args.corpus)
        else:
            corpus_root = work / "demo"
            log("產生合成語料（weasyprint）…")
            demo_corpus.write(corpus_root, demo_corpus.plan(anchor), anchor, demo_corpus.DEFAULT_SEED)
        expected = json.loads((corpus_root / "expected.json").read_text(encoding="utf-8"))

        values = write_env(work, image=args.image, web_port=web_port, s3_port=s3_port, real_llm=args.real_llm,
                           rerank=args.rerank)
        compose.env = compose_env(values)
        compose.run("up", "-d", "--wait", "--wait-timeout", "300", "postgres", "s3", "fake-llm")
        compose.run("run", "--rm", "-T", "migrate", timeout=900)
        password = secrets.token_urlsafe(18)
        compose.run("run", "--rm", "-T", "web", "create-admin", "--username", ADMIN_USER, "--password-stdin",
                    input=password + "\n", timeout=600)
        n = upload_corpus(corpus_root / "corpus", s3_port)
        log(f"上傳 {n} 個物件到 s3://{BUCKET}/{PREFIX}")

        sync = compose.run("run", "--rm", "-T", "scheduler", "python", "scripts/connector_sync.py", "--force",
                           timeout=args.sync_timeout, capture=True, check=False)
        sync_out = (sync.stdout or "") + (sync.stderr or "")
        (work / "connector_sync.log").write_text(sync_out, encoding="utf-8")
        print(sync_out[-4000:], flush=True)
        checks.check("connector_sync.py --force 退出碼 0", sync.returncode == 0, f"rc={sync.returncode}")
        corpus_checks(checks, compose, expected, real_llm=args.real_llm)

        bf = compose.run("run", "--rm", "-T", "scheduler", "python", "scripts/backfill_tiers.py", "--tier", "3",
                         "--scheduled", timeout=args.sync_timeout, capture=True, check=False)
        print(((bf.stdout or "") + (bf.stderr or ""))[-2000:], flush=True)
        checks.check("backfill_tiers.py --tier 3 退出碼 0", bf.returncode == 0, f"rc={bf.returncode}")
        tier3_check(checks, compose)

        compose.run("up", "-d", "--wait", "--wait-timeout", "600", "web", timeout=900)
        http_checks(checks, compose, f"http://127.0.0.1:{web_port}", password, real_llm=args.real_llm)
    except Exception as exc:  # noqa: BLE001 — 任何例外都記成失敗項，照樣收尾（down -v）
        checks.check("流程", False, f"{type(exc).__name__}: {exc}")
        try:
            compose.run("logs", "--no-color", "--tail", "80", check=False, timeout=60)
        except subprocess.TimeoutExpired:
            pass
    finally:
        if args.keep:
            log(f"--keep：保留 project {project}（拆除：{' '.join(compose.base)} down -v）")
        else:
            compose.run("down", "-v", "--remove-orphans", check=False, timeout=300)
            shutil.rmtree(work, ignore_errors=True)

    elapsed = time.monotonic() - started
    passed = sum(ok for _, ok, _ in checks.results)
    log(f"結果：{passed}/{len(checks.results)} 通過，耗時 {elapsed / 60:.1f} 分")
    for name in checks.failed:
        log(f"  失敗：{name}")
    return EXIT_OK if not checks.failed and checks.results else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--image", default=os.getenv("E2E_IMAGE", DEFAULT_IMAGE))
    ap.add_argument("--build", action="store_true", help="先 docker build 映像（重用 BuildKit 快取）")
    ap.add_argument("--keep", action="store_true", help="跑完不 down -v（除錯用）")
    ap.add_argument("--real-llm", action="store_true", help="接真的 LLM（E2E_LLM_* 環境變數）")
    ap.add_argument("--rerank", action="store_true", help="web 開 rerank（多吃約 2 GB 記憶體）")
    ap.add_argument("--project", default=None, help="compose project 名稱（預設 e2e-<亂數>）")
    ap.add_argument("--work-dir", default=None, help="暫存目錄的上層（預設系統暫存目錄）")
    ap.add_argument("--corpus", default=None, help="已產生好的語料資料夾（demo_corpus.py --out 的那一層）")
    ap.add_argument("--anchor-date", default=None, help="合成語料的日期錨點（預設今天，台北時間）")
    ap.add_argument("--sync-timeout", type=int, default=2400, help="connector_sync／backfill 的逾時秒數")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
