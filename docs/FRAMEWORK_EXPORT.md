# 交付原始碼（框架打包）

框架以非專屬授權交付原始碼給其他券商時，交出去的是**框架**：`profiles/tingfong/`、廷豐主機層（`deploy/OWNERSHIP.toml` 的 `tingfong_host`）、只服務廷豐主機的文件、廷豐語料量出的評測資產，以及只驗那些東西的測試都不在裡面。打包工具是 `scripts/export_framework.py`（`make export`），交付清單是 `deploy/EXPORT.toml`。本檔與這三個檔案都只在源頭 repo，不隨交付物。

## 指令

```bash
make export                                                    # HEAD → data/export/，含驗證
make export EXPORT_ARGS="--commit <rev> --frontend"            # 指定 commit，另驗前端
uv run python scripts/export_framework.py --commit <rev> --license <檔>   # 換一份授權聲明
uv run python scripts/export_framework.py --no-verify          # 只打包
uv run python scripts/export_framework.py --check              # 只核對清單與 commit，不寫任何檔
```

退出碼：0 成功；1 驗證失敗（tarball 已產出，但不要交出去）；2 清單、參數或 git 錯誤（沒有產出）。

- **內容只取自 commit**（`git ls-tree`／`git cat-file`），不讀工作樹；工作樹有未 commit 的改動時印警告，那些改動不會進去。交付的版本一律先 commit（建議打 tag），再 `--commit <tag>`。
- **同一個 commit 重打逐位元組相同**：成員依路徑排序、mtime 一律是 commit 時間、uid／gid 為 0、gzip 標頭不帶時間。日後要確認某份交付物是哪一版，用它的 DELIVERY_INFO.json 的 `source_commit` 重打一次比 sha256。
- 輸出預設在 `data/export/`（不進版控、也在 `.dockerignore` 裡，不會被帶進映像的 build context）；`--out` 可改，例如 `/tmp`。

## 產出

| 檔案 | 內容 |
|---|---|
| `report-mark-framework-<commit 日期>-<sha12>.tar.gz` | 最上層一個同名目錄，裡面是交付的檔案、DELIVERY_INFO.json、`SHA256SUMS` |
| …tar.gz.sha256 | tarball 本身的 sha256 |
| …receipt.json | DELIVERY_INFO.json 的內容加上打包時間、tarball 雜湊、警告與每一步驗證的結果（不在 tarball 裡，留給源頭對帳） |

DELIVERY_INFO.json 記 `source_commit`、`source_tree`、`source_commit_date`、交付清單的 sha256、授權聲明的來源、檔案數、排除數與改寫過的檔案清單；`SHA256SUMS` 是交付物裡每個檔案（`SHA256SUMS` 自己除外）的 sha256，格式同 `sha256sum`，收件方可以 `sha256sum -c SHA256SUMS` 自行核對。

## 交付清單

每個受版控的檔案恰好被一條規則歸類，否則拒絕打包；`tests/test_export_manifest.py` 在 CI 守同一件事（新檔案沒歸類就紅、列了不存在的也紅）。

- `deploy/`、`scripts/`、`ops_agent/`：沿用 `deploy/OWNERSHIP.toml`，framework 進、tingfong_host 不進。維運代理整個交付：`ops_agent/` 與 `deploy/ops-agent/` 的框架範本（systemd unit、容器部署的 catalog、polkit 規則、compose override）歸 framework；廷豐兩台主機的代理 unit（`deploy/systemd/report-mark-ops-agent*.service`）、catalog（`deploy/ops/services.*.toml`）、polkit 規則（`deploy/polkit/`）與換寫主機路徑的 `deploy/install_units.sh` 歸主機層、不交付。
- 其他路徑：`deploy/EXPORT.toml` 的 `[paths]`。整個目錄同屬一邊的用目錄規則（`app/`、`web/`、`frontend/`、`db/`、`profiles/demo/` 進；`profiles/tingfong/`、`eval/baselines/` 不進），其餘逐檔列：repo 根的檔案、`.github/`、`docs/`、`eval/`、`tests/`。
- repo 根的授權聲明（LICENSE、LICENSE.md 或 LICENSE.txt）一律進，不必列；沒有就印警告。`--license <檔>` 以那份檔取代，交付物裡命名為 `LICENSE`。授權聲明不做禁用字樣掃描（要寫出著作權人）。

不交付的類別：廷豐的 profile；主機層的 systemd unit、主機的維運代理 unit 與 catalog、探針與事件、主機信標、sync 殼、NAS 掛載、對外邊緣、AWS 基礎設施；廷豐主機的現況與維運文件（`docs/DEPLOYMENT_STATUS.md`、`docs/production_resilience.md`、`docs/EXTERNAL_ACCESS.md`、`docs/LINEBOT_ALWAYS_ON.md`、`docs/nas_scheduled_sync_deployment.md`）；廷豐語料量出的評測基準線、抽取 golden set 與檢索前後比較結果；源頭 repo 歷史的 gitleaks 指紋（`.gitleaksignore`）；只驗上述東西的測試；本檔、`deploy/EXPORT.toml`、`deploy/OWNERSHIP.toml` 與打包工具。廷豐的凍結題集 `eval/ragas_questions.json` 與檢索標註集 `eval/queryset*.json` 也不交付：交付版的評測入口沒有預設題集，不帶 `--profile`（或 `--dataset`／`--queryset`）就拒絕並說明改帶 profile 的凍結題集；需要題集當素材的測試改用合成的 `tests/fixtures/eval_questions_synthetic.json`（虛構公司、只有問句，形狀與題數照題集契約）。

## 交付版的改寫

源頭 repo 裡同一個檔案同時服務框架與廷豐時，交付版只做兩種改寫，都在打包時對不上就拒絕（`tests/test_export_manifest.py` 也在 CI 先核一次）：

- **標記**：`[markers].files` 列出的檔案裡，以 `export-omit` 註解標記的範圍在交付版刪除。標記一律寫成該檔的註解，三種形式：
  - 單行：行尾的註解（`# …`、`// …`、HTML 註解；Markdown 表格列寫在最後的 `|` 之前）。
  - 區塊：獨立一行的 `export-omit:begin` 到獨立一行的 `export-omit:end`（含兩個標記行）。測試檔用它包住只驗廷豐或主機層的 class 與方法。
  - 行內片段（只用在 Markdown）：同一行裡成對的 HTML 註解 `export-omit:begin`…`export-omit:end` 之間的文字，渲染後看不見；用在句子或表格儲存格的一部分。程式碼區塊裡不能用 HTML 註解，改用逐字替換。

  標記不成對、巢狀、區塊標記沒有獨立成行，或出現 `export-omit` 字樣卻不是上述形式，一律拒絕；有標記的檔案沒列進 `[markers].files` 也拒絕（漏列＝標記原樣出貨）。
- **逐字替換**：`[[rewrite]]` 每條的 `old` 在該檔裡必須恰好出現一次，並寫明理由。用在交付版要換成別的值、而不是刪掉的地方，主要是預設 profile：

| 檔案 | 交付版 |
|---|---|
| `app/profile.py`、`eval/profile_eval.py` | `DEFAULT_PROFILE_DIR` 改成空字串：交付版沒有預設 profile，`PROFILE_DIR` 必填。沒設時 `resolve_profile_dir` 拋 ProfileError，訊息說出要設的變數與要指向的目錄（例如 `profiles/demo`）；web 的 lifespan、LLM 批次與評測入口的 `load_llm_env()`（rc=2）、容器 entrypoint 的每個角色都在第一步載入 profile，因此一律拒絕啟動（`tests/test_profile_required.py` 以清空預設驗同一套行為）。源頭 repo 照舊預設 `profiles/tingfong`（廷豐主機不設 `PROFILE_DIR`） |
| `tests/conftest.py` | 測試固定用 `tests/fixtures/profile_framework/`：只宣告機構名稱與一份測試用的小發行機構名單，其餘全是框架預設。源頭 repo 的測試釘的是預設 profile 的行為（廷豐的 profile 除了名稱、品牌與名單，宣告的就是框架預設），交付版的預設是示範 profile（每一項都刻意不同），所以改用這份 |
| `.env.example`、`deploy/compose/.env.example`、文件 | `PROFILE_DIR` 標成必填、範例值換成示範 profile；源頭文件以廷豐舉例的句子換成框架用語 |

## 驗證

預設在暫存目錄解開 tarball 後依序做，任一步失敗退出 1：

1. 雜湊對帳：`SHA256SUMS` 與實際檔案逐一相符、沒有多也沒有少。
2. 禁用字樣：`[verify].forbidden`（「廷豐」、tingfong，不分大小寫）不得出現在任何檔名或內容（二進位檔掃位元組）；例外列在 `[verify.allow]`，逐檔釘行數與理由。提示字樣 `[verify].advisory`（源頭的開發者帳號、GitHub 組織、主機的稱呼）只逐檔列出次數、不擋，交付前由人判斷。
3. `deploy/container/Dockerfile` 的 `COPY` 來源與 compose 檔以相對路徑掛載的主機路徑都在交付物裡。
4. 沒設 `PROFILE_DIR` 時 web 的 lifespan 拒絕啟動（訊息帶出變數名）；設成 `profiles/demo` 時載得起來、`web.server` import 得了。
5. `ruff check`。
6. 交付版自己的 pytest（`tests/*_db.py` 除外；`REPORT_MARK_DB_URL` 一律指向不存在的主機 `db.invalid`，不連任何真實資料庫）。用的是執行打包工具的那個 Python 環境（`uv run` 的 `.venv`），交付物的相依與鎖檔和源頭相同。
7. `--frontend`：在交付版的 `frontend/` 裡 `npm ci`、typecheck、build、vitest，並讓第 6 步的 SPA 測試對那份 build 驗證（`npm ci` 需要 npm registry 或本機快取）。沒帶時第 6 步設 `SKIP_SPA_TESTS=1`。

`--keep-work` 保留暫存目錄方便查看。

## 新增檔案或測試時

- 新檔案：在 `deploy/OWNERSHIP.toml`（`deploy/`、`scripts/`、`ops_agent/`）或 `deploy/EXPORT.toml` 歸類。整個目錄規則涵蓋的（例如 `app/`）不必列。
- 新測試：預設歸進交付物。只驗廷豐 profile 的值、廷豐主機層的檔案或廷豐評測資產的測試，整檔歸到 exclude，或在測試檔裡以 `export-omit` 區塊包住那幾個方法並把檔案加進 `[markers].files`。測試要用到某個 profile 的設定（市場子集、發行機構名單等）時，在 tempfile 建自己的 profile，或依賴 `tests/fixtures/profile_framework/`，不要依賴預設 profile 恰好是誰。
- 進交付物的文件提到廷豐主機的段落，以標記包住；提到不交付的檔案路徑時，交付版的 `tests/test_docs_contract.py` 會紅（它掃 living docs 的路徑存在性），在源頭 repo 跑 `make export` 就會看到。
- 交付版的 import 不得指向不交付的模組（`tests/test_export_manifest.py` 靜態檢查 `scripts.*`、`ops_agent.*` 與測試輔助模組）。
