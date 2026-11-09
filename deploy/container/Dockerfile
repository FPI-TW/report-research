# syntax=docker/dockerfile:1.7
# 廷豐智能研報（通用框架）的應用映像：一份映像、四個角色（web／worker／scheduler／migrate），
# 由 entrypoint 的第一個參數決定（deploy/container/entrypoint.py）。說明見 docs/CONTAINER_DEPLOYMENT.md。
#
# 建置（context 是 repo 根；.dockerignore 也在 repo 根）：
#   docker build -f deploy/container/Dockerfile -t report-mark:<版本> \
#     --build-arg BUILD_COMMIT="$(git rev-parse HEAD)" \
#     --build-arg BUILD_VERSION="$(git describe --tags --always)" \
#     --build-arg BUILD_TREE_CLEAN="$(test -z "$(git status --porcelain)" && echo 1 || echo 0)" .
# 三個 BUILD_* 是版本對帳用（管理後台診斷頁、/metrics 的 report_mark_build_info）；不給也能 build，只是顯示「建置時未提供」。
#
# 三條硬規則（tests/test_container_deploy.py 靜態對帳）：
# - 執行期零外連（除了設定的 LLM 端點）：嵌入與 rerank 權重在 build 時下載並封入，執行期 HF_HUB_OFFLINE=1；
#   supercronic、uv 也在 build 時取得並驗雜湊，執行期不下載任何東西。
# - 映像不含祕密：.env、data/、研報原檔都由 .dockerignore 擋在 context 外；設定一律在執行期以環境變數提供。
# - 非 root：固定 UID／GID 10001，掛載的 volume 要讓這個 UID 可寫。

ARG PYTHON_IMAGE=python:3.13-slim-trixie
ARG NODE_IMAGE=node:22-trixie-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.11.7

# ───── 1) 前端 ─────
FROM ${NODE_IMAGE} AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# vite 的 @docs 別名打包 repo 根的 docs/EXTERNAL_API.md（後台「API 用戶端」頁）。
COPY docs/ /src/docs/
# tsc 型別檢查要讀前後端共用的 SSE 事件契約（frontend/tsconfig.json 的 @fixtures 別名）。
COPY tests/fixtures/sse_events.json /src/tests/fixtures/sse_events.json
RUN npm run build

# ───── 2) uv（只取二進位） ─────
FROM ${UV_IMAGE} AS uv

# ───── 3) Python 相依＋模型權重 ─────
FROM ${PYTHON_IMAGE} AS python-deps
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app
# 只拿鎖檔：與 pyproject.toml／uv.lock 完全一致（--frozen），CPU-only torch 由 pyproject 的 index 設定決定。
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

FROM python-deps AS models
# 改 rerank 模型要用 build arg，執行期的 RERANK_MODEL 也要設成同一個值（離線模式下沒有封進來的模型載不到）。
ARG RERANK_MODEL=BAAI/bge-reranker-v2-m3
ENV HF_HOME=/opt/models/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    RERANK_MODEL=${RERANK_MODEL} \
    PATH=/app/.venv/bin:${PATH}
COPY app/ /app/app/
COPY deploy/container/fetch_models.py /app/deploy/container/fetch_models.py
RUN python deploy/container/fetch_models.py download \
 && rm -rf /opt/models/hf/hub/.locks /opt/models/hf/xet

# ───── 4) supercronic（排程角色；MIT） ─────
FROM ${PYTHON_IMAGE} AS supercronic
ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.51
ARG SUPERCRONIC_SHA256_AMD64=cc232108374e8e2b537045f2d5eaa1229f619db314c3ba6a14d38b35dc0aa123
ARG SUPERCRONIC_SHA256_ARM64=4ae22576a78223d8ccdf5f3b3660a5cbb5288416bf0ca96afc4a36d195932b94
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-amd64 /tmp/supercronic-amd64
ADD https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-arm64 /tmp/supercronic-arm64
RUN set -eu; \
    arch="${TARGETARCH:-amd64}"; \
    case "$arch" in amd64) sum="$SUPERCRONIC_SHA256_AMD64" ;; arm64) sum="$SUPERCRONIC_SHA256_ARM64" ;; \
      *) echo "不支援的架構：$arch" >&2; exit 1 ;; esac; \
    echo "$sum  /tmp/supercronic-$arch" | sha256sum -c -; \
    install -m 0755 "/tmp/supercronic-$arch" /usr/local/bin/supercronic

# ───── 5) 執行映像 ─────
FROM ${PYTHON_IMAGE} AS runtime
# postgresql-client：migrate 的全庫備份（pg_dump -Fc＋pg_restore -l）與排程的每日備份。trixie 的 17 版
# 可以 dump 16 版的伺服器（pg_dump 只往下相容）。util-linux 提供 flock／ionice（上傳 worker 與 sync 殼的鎖）。
RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client util-linux ca-certificates tini \
 && rm -rf /var/lib/apt/lists/*

ARG APP_UID=10001
ARG APP_GID=10001
RUN groupadd --system --gid ${APP_GID} app \
 && useradd --system --uid ${APP_UID} --gid ${APP_GID} --home-dir /home/app --create-home --shell /usr/sbin/nologin app

COPY --from=uv /uv /usr/local/bin/uv
COPY --from=supercronic /usr/local/bin/supercronic /usr/local/bin/supercronic
COPY --from=python-deps /app/.venv /app/.venv
COPY --from=models /opt/models /opt/models

WORKDIR /app
COPY --chown=root:root . /app
COPY --from=frontend /src/frontend/dist /app/frontend/dist

# data/ 與備份落點是 volume 掛載點；先建好並交給 app，空 volume 第一次掛上時會繼承這個擁有者。
RUN mkdir -p /app/data /app/backups /app/profile \
 && chown app:app /app/data /app/backups \
 && python -c "import compileall, sys; sys.exit(not compileall.compile_dir('/app/app', quiet=1))"

# 建置資訊（app/services/build_info.py）：映像不帶 .git，commit／版本／工作樹是否乾淨只能在 build 時由 arg 傳進來。
# 寫在 /app/build_info.json（不是 data/：那是 volume，會蓋掉映像裡的檔）。放在最後幾層，換 commit 只重做這之後的層。
ARG BUILD_COMMIT=
ARG BUILD_VERSION=
ARG BUILD_TREE_CLEAN=
RUN python scripts/build_info.py write --source image --out /app/build_info.json \
      --commit "${BUILD_COMMIT}" --version "${BUILD_VERSION}" --tree-clean "${BUILD_TREE_CLEAN}"

ARG RERANK_MODEL=BAAI/bge-reranker-v2-m3
# 執行期離線：HF 系列一律不連網（權重已在 /opt/models/hf）。UV_NO_SYNC：殼腳本裡的 `uv run` 直接用映像內的
# .venv，不重新解析或下載相依。HOME 指到可寫的使用者目錄（torch／transformers 會寫小快取）。
# 備份落點是 compose 掛進來的 /app/backups：scripts/db_backup.sh 要求它是掛載點（沒掛就失敗、不寫進容器自己的檔案系統）。
# LOG_FORMAT=json：web 的日誌是一行一個 JSON 物件到 stdout（app/logging_setup.py；欄位與遮罩見 docs/CONTAINER_DEPLOYMENT.md）。
# EXTRACTOR=pdfplumber：版面層抽取（docs/EXTRACTION.md）。程式預設是舊路徑 pypdf，systemd 主機靠 sync 的環境檔設它；
# 容器的連接器、上傳 worker 與回填都在這份映像裡跑，不設就靜默走 pypdf（示範 profile 的端到端測試逼出來的）。
ENV PATH=/app/.venv/bin:${PATH} \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/opt/models/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    RERANK_MODEL=${RERANK_MODEL} \
    UV_NO_SYNC=1 \
    UV_FROZEN=1 \
    UV_OFFLINE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    UV_CACHE_DIR=/tmp/uv-cache \
    UV=/usr/local/bin/uv \
    HOME=/home/app \
    REPORT_MARK_CONTAINER=1 \
    LOG_FORMAT=json \
    EXTRACTOR=pdfplumber \
    REPORT_MARK_BACKUP_DIR=/app/backups \
    REPORT_MARK_BACKUP_MOUNT=/app/backups \
    TZ=Asia/Taipei

USER app:app

# 建置期最後一道：以離線模式、非 root 身分真的載入一次兩個模型。少了任何權重檔，build 在這裡失敗。
RUN python deploy/container/fetch_models.py verify

EXPOSE 8097
# tini -g：SIGTERM 轉給整個行程群組，worker 迴圈底下正在跑的殼與批次也收得到（compose stop 時乾淨收尾）。
ENTRYPOINT ["/usr/bin/tini", "-g", "--", "python", "/app/deploy/container/entrypoint.py"]
CMD ["web"]
