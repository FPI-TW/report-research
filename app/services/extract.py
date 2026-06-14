"""文字抽取：PDF / docx → 純文字，並偵測掃描檔（無可抽文字）。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

try:
    import docx  # python-docx
except ImportError:  # pragma: no cover
    docx = None

# 低於此字元數視為掃描檔/空白（無 OCR → 後續跳過嵌入）
MIN_TEXT_CHARS = 100


@dataclass
class ExtractResult:
    file_hash: str
    text: str
    char_count: int
    scanned: bool
    language: str  # "zh" / "en" / "mixed"
    error: str | None = None


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _detect_language(text: str) -> str:
    sample = text[:4000]
    cjk = len(re.findall(r"[一-鿿]", sample))
    latin = len(re.findall(r"[A-Za-z]", sample))
    if cjk == 0 and latin == 0:
        return "unknown"
    if cjk > latin * 2:
        return "zh"
    if latin > cjk * 2:
        return "en"
    return "mixed"


def _extract_pdf(path: Path) -> str:
    reader = PdfReader(str(path))
    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def _extract_docx(path: Path) -> str:
    if docx is None:
        return ""
    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs)


def extract_text(path: Path) -> ExtractResult:
    file_hash = file_sha256(path)
    suffix = path.suffix.lower()
    try:
        if suffix == ".pdf":
            text = _extract_pdf(path)
        elif suffix in (".docx", ".doc"):
            text = _extract_docx(path)
        else:  # 圖片等：視為掃描檔
            text = ""
    except Exception as exc:  # 損毀檔不要中斷整批
        return ExtractResult(file_hash, "", 0, True, "unknown", error=str(exc))

    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    char_count = len(text)
    scanned = char_count < MIN_TEXT_CHARS
    return ExtractResult(
        file_hash=file_hash,
        text=text,
        char_count=char_count,
        scanned=scanned,
        language=_detect_language(text),
    )
