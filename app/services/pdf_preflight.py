"""上傳 PDF 的入庫前檢查（Admin v1.5 上傳管線；上傳 worker 在**子行程**裡跑）。

不可信的 PDF 要在三道限制之下才能碰：`RLIMIT_AS`（虛擬記憶體上限）、牆鐘逾時（父行程
`subprocess.run(timeout=...)` 到期就 SIGKILL）、頁數上限。sync 的 NAS 檔是半可信來源所以原本沒做這層；
上傳是任何管理員帳號都能送進來的檔案，ClamAV 也抓不到「讓解析器吃光記憶體或 CPU」這一類。

子行程做兩件事，任何一件不通過就不入庫（上傳轉 `failed`，類別在 `app/services/uploads.py`）：

1. **結構檢查**（pypdf，不解碼內容串流）：
   - 加密（`is_encrypted`，含空密碼可開的）→ `encrypted`（設計決策 10）。
   - 頁數超過上限（300）→ `too_many_pages`。
   - 主動內容 → `active_content`（設計決策 9）：從 trailer 走遍可達的物件圖，字典鍵出現 `/JavaScript`、`/JS`、
     `/Launch`、`/EmbeddedFile(s)`、`/XFA`、`/RichMedia*`，或 `/S`、`/Type`、`/Subtype` 的值是
     `/JavaScript`、`/Launch`、`/EmbeddedFile`、`/RichMedia`。**只有 `/OpenAction` 放行**：它本身不是命中條件，
     但它指向的動作照樣被走到——`/OpenAction` 是 GoTo（翻到某頁）放行，是 JavaScript 照樣拒收。
     名稱以 pypdf 解碼後比對，`/J#61vaScript` 這類跳脫寫法一樣抓得到。
   - 走物件圖時任何解析例外、或物件數超過上限 → `extract_error`（fail-closed：這是安全閘門，
     「看不懂」不等於「沒有」）。
2. **試抽字**：通過結構檢查後，用與入庫相同的抽取器（`EXTRACTOR`）實際抽一次，丟掉結果，只為了證明它在
   同樣的記憶體與時間限制內抽得完。入庫核心 `scripts/_ingest_core.ingest_one` 會在 worker 主行程裡再抽一次
   （介面固定、不能換成子行程的結果），所以這一步把「解析器炸彈」擋在主行程之外。代價是每篇多抽一次字，
   上傳量很小，划算。
   - 子行程逾時 → `extract_timeout`（可由管理員重試）；吃滿記憶體（`MemoryError`）或被訊號砍掉 → `extract_error`。

子行程的環境是**白名單**（`child_env`）：不帶 DeepSeek 金鑰、R2 憑證、DB 連線字串與告警 webhook——
解析不可信檔案的行程什麼祕密都不該拿得到。它也不連 DB、不寫任何檔案（試抽字的 `.pdf` 符號連結放在自己的
暫存目錄；抽取器依副檔名分派，而隔離區的檔名一律是 `<upload_id>.bin`）。

用法（父行程）：`run_preflight(path)` → `PreflightResult`。子行程入口：`python -m app.services.pdf_preflight`。
"""

from __future__ import annotations

import argparse
import errno
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

MAX_PAGES = 300  # 設計 1.1：頁數上限（語料最大的研報遠低於此）
TIMEOUT_SECONDS = 300  # 設計 1.1：子行程牆鐘逾時
# 子行程的虛擬記憶體上限（預設值；生產讀 `UPLOAD_PREFLIGHT_MEMORY_MB`）。RLIMIT_AS 管的是位址空間而不是 RSS。
# 2026-10-07 以合成的密集文字 PDF（每頁約 2,700 字）實測 pdfplumber：50 頁 RSS 0.34 GB、100 頁 0.61 GB、
# 300 頁（頁數上限）1.68 GB——每頁約 5.5 MB；300 頁在 1536 MB 的上限下 ENOMEM、2048 MB 通過。
# pypdf 抽取器 300 頁只要約 60 MB。
MEMORY_LIMIT_MB = 2048
MAX_OBJECTS = 200_000  # 走物件圖的上限：再多就當作看不懂（fail-closed）

# 字典鍵命中即拒收。
ACTIVE_KEYS = frozenset({
    "/JavaScript", "/JS", "/Launch", "/EmbeddedFile", "/EmbeddedFiles", "/XFA",
    "/RichMedia", "/RichMediaContent", "/RichMediaSettings",
})
# 這些鍵的值（名稱）命中即拒收：動作類型（/S）與物件類型（/Type、/Subtype）。
ACTIVE_TYPE_KEYS = frozenset({"/S", "/Type", "/Subtype"})
ACTIVE_TYPE_VALUES = frozenset({"/JavaScript", "/Launch", "/EmbeddedFile", "/RichMedia"})

# 子行程環境白名單：只放抽字需要的。其餘（金鑰、憑證、DB、webhook）一律不帶。
_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR", "EXTRACTOR")
_ENV_PREFIXES = ("EXTRACTION_",)

# 結果類別（與 app/services/uploads.py 的 failure_kind 逐字相同；這裡不 import 它，讓子行程的 import 最小）。
KIND_ACTIVE_CONTENT = "active_content"
KIND_ENCRYPTED = "encrypted"
KIND_TOO_MANY_PAGES = "too_many_pages"
KIND_EXTRACT_ERROR = "extract_error"
KIND_EXTRACT_TIMEOUT = "extract_timeout"


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    kind: str | None = None  # 不通過時的 failure_kind
    detail: str | None = None
    pages: int | None = None
    chars: int | None = None  # 試抽字的字數（只供紀錄）


class _TooComplex(Exception):
    pass


def _walk_for_active(root) -> str | None:
    """從 `root`（trailer）走遍可達物件，回第一個命中的說明；沒有命中回 None。解析例外原樣拋出。"""
    from pypdf.generic import ArrayObject, DictionaryObject, IndirectObject, NameObject

    seen: set[tuple[int, int]] = set()
    stack = [root]
    visited = 0
    while stack:
        obj = stack.pop()
        if isinstance(obj, IndirectObject):
            key = (obj.idnum, obj.generation)
            if key in seen:
                continue
            seen.add(key)
            obj = obj.get_object()
        visited += 1
        if visited > MAX_OBJECTS:
            raise _TooComplex(f"物件超過 {MAX_OBJECTS} 個")
        if isinstance(obj, DictionaryObject):  # StreamObject 也是：只看字典，不解碼內容
            for k, v in obj.items():
                name = str(k)
                if name in ACTIVE_KEYS:
                    return f"含主動內容 {name}"
                if name in ACTIVE_TYPE_KEYS:
                    val = v.get_object() if isinstance(v, IndirectObject) else v
                    if isinstance(val, NameObject) and str(val) in ACTIVE_TYPE_VALUES:
                        return f"含主動內容 {name} {val}"
                if isinstance(v, (IndirectObject, DictionaryObject, ArrayObject)):
                    stack.append(v)
        elif isinstance(obj, ArrayObject):
            for v in obj:
                if isinstance(v, (IndirectObject, DictionaryObject, ArrayObject)):
                    stack.append(v)
    return None


def check_structure(path: Path, *, max_pages: int = MAX_PAGES) -> PreflightResult:
    """結構檢查（純函式；子行程裡呼叫，測試也直接呼叫）。順序：加密 → 頁數 → 主動內容。"""
    from pypdf import PdfReader

    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            return PreflightResult(False, KIND_ENCRYPTED, "PDF 已加密（不收加密檔，含空密碼可開的）")
        pages = len(reader.pages)
        if pages > max_pages:
            return PreflightResult(False, KIND_TOO_MANY_PAGES, f"{pages} 頁，超過上限 {max_pages} 頁", pages=pages)
        hit = _walk_for_active(reader.trailer)
    except _TooComplex as exc:
        return PreflightResult(False, KIND_EXTRACT_ERROR, f"PDF 結構過於複雜，無法確認沒有主動內容：{exc}")
    except MemoryError:
        raise
    except Exception as exc:  # noqa: BLE001 — 安全閘門：看不懂就不放行
        return PreflightResult(False, KIND_EXTRACT_ERROR, f"PDF 結構無法解析：{type(exc).__name__}: {exc}"[:500])
    if hit:
        return PreflightResult(False, KIND_ACTIVE_CONTENT, hit, pages=pages)
    return PreflightResult(True, pages=pages)


def _trial_extract(path: Path) -> int:
    """以入庫用的抽取器實際抽一次（結果丟掉），回字數。抽取器依副檔名分派，所以經 `.pdf` 符號連結。"""
    from app.services.extract import extract_text

    with tempfile.TemporaryDirectory(prefix="upload-preflight-") as tmp:
        link = Path(tmp) / "upload.pdf"
        os.symlink(path.resolve(), link)
        res = extract_text(link)
    return int(res.char_count or 0)


def _limit_memory(mb: int) -> None:
    import resource

    limit = int(mb) * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _child_main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="上傳 PDF 的入庫前檢查（由上傳 worker 以子行程呼叫）")
    ap.add_argument("path")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--memory-mb", type=int, default=MEMORY_LIMIT_MB)
    ap.add_argument("--no-extract", action="store_true", help="只做結構檢查（測試用）")
    args = ap.parse_args(argv)
    if args.memory_mb > 0:
        _limit_memory(args.memory_mb)
    path = Path(args.path)
    try:
        result = check_structure(path, max_pages=args.max_pages)
        if result.ok and not args.no_extract:
            chars = _trial_extract(path)
            result = PreflightResult(True, pages=result.pages, chars=chars)
    except MemoryError:
        result = PreflightResult(False, KIND_EXTRACT_ERROR, f"超過子行程記憶體上限 {args.memory_mb} MB")
    except OSError as exc:
        if exc.errno == errno.ENOMEM:
            result = PreflightResult(False, KIND_EXTRACT_ERROR, f"超過子行程記憶體上限 {args.memory_mb} MB")
        else:
            result = PreflightResult(False, KIND_EXTRACT_ERROR, f"試抽字失敗：{type(exc).__name__}: {exc}"[:500])
    except Exception as exc:  # noqa: BLE001 — 試抽字失敗：與入庫時一樣抽不出來
        result = PreflightResult(False, KIND_EXTRACT_ERROR, f"試抽字失敗：{type(exc).__name__}: {exc}"[:500])
    sys.stdout.write(json.dumps(result.__dict__, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    return 0


# ── 父行程 ──────────────────────────────────────────────────────────────


def child_env(source: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ if source is None else source
    out = {k: env[k] for k in _ENV_KEYS if k in env}
    out.update({k: v for k, v in env.items() if k.startswith(_ENV_PREFIXES)})
    out["PYTHONDONTWRITEBYTECODE"] = "1"
    return out


def run_preflight(
    path: Path,
    *,
    max_pages: int = MAX_PAGES,
    timeout: float = TIMEOUT_SECONDS,
    memory_mb: int = MEMORY_LIMIT_MB,
    trial_extract: bool = True,
    python: str | None = None,
) -> PreflightResult:
    """在子行程裡檢查 `path`。任何異常（逾時、被砍、輸出看不懂）都是不通過——安全閘門不 fail-open。"""
    argv = [python or sys.executable, "-m", "app.services.pdf_preflight", "--max-pages", str(max_pages),
            "--memory-mb", str(memory_mb)]
    if not trial_extract:
        argv.append("--no-extract")
    argv.append(str(path))
    try:
        proc = subprocess.run(
            argv, cwd=REPO_ROOT, env=child_env(), capture_output=True, timeout=timeout, close_fds=True,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return PreflightResult(False, KIND_EXTRACT_TIMEOUT, f"檢查與試抽字超過 {timeout:g} 秒")
    if proc.returncode != 0:
        how = f"signal {-proc.returncode}" if proc.returncode < 0 else f"rc={proc.returncode}"
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-1:] or [""]
        return PreflightResult(False, KIND_EXTRACT_ERROR, f"檢查子行程異常結束（{how}）{tail[0][:300]}")
    try:
        data = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
        ok = data["ok"] is True
        kind = None if ok else (data.get("kind") or KIND_EXTRACT_ERROR)
        return PreflightResult(ok, kind, data.get("detail"), data.get("pages"), data.get("chars"))
    except (ValueError, KeyError, IndexError, TypeError):
        return PreflightResult(False, KIND_EXTRACT_ERROR, "檢查子行程的輸出看不懂")


if __name__ == "__main__":
    raise SystemExit(_child_main())
