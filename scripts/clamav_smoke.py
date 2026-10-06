"""ClamAV 上線冒煙：PING、病毒碼年齡、EICAR 要 FOUND、一份正常 PDF 要 OK。零 LLM、不連 DB。

用法：make clamav-smoke [SMOKE_PDF=path/to/real.pdf]
      uv run python scripts/clamav_smoke.py [--pdf path/to/real.pdf]

走的是上傳 worker 會用的同一條路（`app/services/clamd.py` 的 `scan()`：先 VERSION 檢查病毒碼年齡、
再 zINSTREAM），所以病毒碼過舊時 EICAR 那一步也會失敗——這是刻意的，smoke 綠才代表上傳真的掃得了。

EICAR 測試字串在執行期拼出來、只存在記憶體：完整字串若以原樣存進 repo，開發機的防毒（Windows
Defender 會掃 WSL 掛出去的檔案）可能把這支檔案隔離掉，gitleaks 之類的掃描也可能誤報。
沒給 --pdf 時用程式內產生的最小合法 PDF（一頁、一行字）。

退出碼：0 全部符合預期；1 結果不符（EICAR 沒被攔、PDF 沒通過、病毒碼過舊）；2 clamd 連不上或回應異常。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import clamd  # noqa: E402


def eicar() -> bytes:
    # 標準 EICAR 測試字串（68 bytes），刻意拆段、執行期才接起來。
    parts = ("X5O!P%@AP[4\\PZX54", "(P^)7CC)7}$EIC", "AR-STANDARD-ANTI", "VIRUS-TEST-FILE!$H+H*")
    data = "".join(parts).encode("ascii")
    assert len(data) == 68
    return data


def minimal_pdf() -> bytes:
    """一頁、一行字的合法 PDF（xref 偏移量現算）。"""
    stream = b"BT /F1 12 Tf 72 720 Td (report-mark clamav smoke) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.7\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--pdf", type=Path, help="改用這份真實 PDF 驗「正常檔要 OK」")
    args = ap.parse_args(argv)
    cfg = clamd.ClamdConfig.from_settings()
    print(f"clamd {cfg.host}:{cfg.port}（逾時 {cfg.timeout:g}s、病毒碼上限 {cfg.max_signature_age}）")

    try:
        clamd.ping(cfg)
        engine = clamd.version(cfg)
    except clamd.ClamdError as exc:
        print(f"FAIL 連不上 clamd：{exc}（容器起來了嗎？首次啟動要先下載病毒碼數分鐘）")
        return 2
    age = engine.age()
    age_txt = f"{age.total_seconds() / 3600:.1f} 小時" if age is not None else "判斷不出來"
    print(f"版本     {engine.raw}（病毒碼年齡 {age_txt}）")

    failed = False
    eicar_result = clamd.scan(eicar(), cfg)
    if eicar_result.status == "found":
        print(f"OK   EICAR → FOUND {eicar_result.signature}")
    else:
        failed = True
        print(f"FAIL EICAR 應為 FOUND，實得 {eicar_result.status} "
              f"{eicar_result.kind or ''} {eicar_result.detail or ''}")

    pdf = args.pdf.read_bytes() if args.pdf else minimal_pdf()
    label = str(args.pdf) if args.pdf else "內建最小 PDF"
    pdf_result = clamd.scan(pdf, cfg)
    if pdf_result.passed:
        print(f"OK   {label}（{len(pdf)} bytes）→ OK")
    else:
        failed = True
        print(f"FAIL {label} 應為 OK，實得 {pdf_result.status} "
              f"{pdf_result.signature or pdf_result.kind or ''} {pdf_result.detail or ''}")

    if failed:
        transport = {"connection_refused", "connection_error", "timeout", "protocol"}
        if {eicar_result.kind, pdf_result.kind} & transport:
            return 2
        return 1
    print(f"PASS（scan_engine＝{eicar_result.engine.label}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
