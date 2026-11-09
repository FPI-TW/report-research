"""寫建置資訊檔（`build_info.json`），給管理後台診斷頁與 `/metrics` 對帳版本（`app/services/build_info.py`）。

    # 主機（部署目錄）：從 git 讀，更新程式之後、重啟 web 之前跑一次（Makefile 的 build-info）
    uv run python scripts/build_info.py write
    # 容器映像：deploy/container/Dockerfile 在 build 時以 build arg 傳入（映像不帶 .git）
    python scripts/build_info.py write --source image --commit "$BUILD_COMMIT" --version "$BUILD_VERSION" \
        --tree-clean "$BUILD_TREE_CLEAN" --out /app/build_info.json

只用標準庫（容器 build 時在 venv 之外執行）。主機模式讀不到 git（不是 checkout、沒有 git）時印一行說明、
**不寫檔、退出 0**：這是給人看的資訊，不能擋部署。給了不合法的值（commit 不是 sha、版本含怪字元）則退出 2——
那是呼叫端寫錯，該當場看到。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import build_info  # noqa: E402

GIT_TIMEOUT = 20


def _git(*args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True,
                              timeout=GIT_TIMEOUT, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def from_git() -> dict | None:
    commit = _git("rev-parse", "HEAD")
    if not commit:
        return None
    version = _git("describe", "--tags", "--exact-match") or _git("describe", "--tags", "--always")
    status = _git("status", "--porcelain", "--untracked-files=normal")
    return {"source": "host", "commit": commit, "version": version or None,
            "tree_clean": None if status is None else status == ""}


def _tri(value: str | None) -> bool | None:
    v = (value or "").strip().lower()
    if v in ("1", "true", "yes", "clean"):
        return True
    if v in ("0", "false", "no", "dirty"):
        return False
    return None


def build(args: argparse.Namespace) -> dict | None:
    if args.source == "image":
        info = {"source": "image", "commit": (args.commit or "").strip().lower() or None,
                "version": (args.version or "").strip() or None, "tree_clean": _tri(args.tree_clean)}
    else:
        info = from_git()
        if info is None:
            return None
    info["built_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("write", help="寫建置資訊檔")
    w.add_argument("--source", choices=build_info.FILE_SOURCES, default="host")
    w.add_argument("--commit")
    w.add_argument("--version")
    w.add_argument("--tree-clean", help="1／0（空＝不知道）")
    w.add_argument("--out", type=Path, help=f"預設 {build_info.DEFAULT_PATH}（或 {build_info.BUILD_INFO_ENV}）")
    args = parser.parse_args(argv)

    info = build(args)
    if info is None:
        print("build_info：讀不到 git（不是 checkout 或沒有 git），不寫建置資訊檔", file=sys.stderr)
        return 0
    if build_info.parse(info) is None:
        print(f"build_info：值不合法，不寫：{json.dumps(info, ensure_ascii=False)}", file=sys.stderr)
        return 2
    out = args.out or build_info.info_path()
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(f"build_info：{out}（commit={info['commit']} version={info['version']} tree_clean={info['tree_clean']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
