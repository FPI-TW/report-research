"""測試用的小 PDF（pypdf 在記憶體裡組，不落地任何真實研報）：正常、主動內容各型、加密、頁數。

給 `tests/test_pdf_preflight.py` 與 `tests/test_upload_worker_db.py` 用。文字頁用標準 14 字型（Helvetica），
pypdf 與 pdfplumber 都抽得出字。
"""

from __future__ import annotations

import io

from pypdf import PdfWriter
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)

TEXT = "Semiconductor outlook remains constructive; we reiterate Buy on TSMC (2330). "


def _name(v: str) -> NameObject:
    return NameObject(v)


def _text_page(w: PdfWriter, lines: int = 30, marker: str = ""):
    page = w.add_blank_page(612, 792)
    font = DictionaryObject({_name("/Type"): _name("/Font"), _name("/Subtype"): _name("/Type1"),
                             _name("/BaseFont"): _name("/Helvetica")})
    page[_name("/Resources")] = DictionaryObject(
        {_name("/Font"): DictionaryObject({_name("/F1"): w._add_object(font)})})
    stream = DecodedStreamObject()
    body = "".join(f"BT /F1 10 Tf 40 {760 - 14 * i} Td ({TEXT}) Tj ET\n" for i in range(lines))
    if marker:  # 讓每一份測試檔的內容（與 SHA-256）都不同
        body += f"BT /F1 6 Tf 40 20 Td ({marker}) Tj ET\n"
    stream.set_data(body.encode())
    page[_name("/Contents")] = w._add_object(stream)
    return page


def _writer(pages: int = 1, marker: str = "") -> PdfWriter:
    w = PdfWriter()
    for _ in range(pages):
        _text_page(w, marker=marker)
    return w


def _bytes(w: PdfWriter) -> bytes:
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _action(kind: str, **extra) -> DictionaryObject:
    d = DictionaryObject({_name("/S"): _name(kind)})
    for k, v in extra.items():
        d[_name(f"/{k}")] = v
    return d


def plain(pages: int = 1, marker: str = "") -> bytes:
    return _bytes(_writer(pages, marker))


def open_action_goto() -> bytes:
    """只有 /OpenAction（翻到第一頁）：放行。"""
    w = _writer()
    w._root_object[_name("/OpenAction")] = ArrayObject([w.pages[0].indirect_reference, _name("/Fit")])
    return _bytes(w)


def open_action_js() -> bytes:
    """/OpenAction 指向 JavaScript：不因為是 OpenAction 就放行。"""
    w = _writer()
    w._root_object[_name("/OpenAction")] = w._add_object(_action("/JavaScript", JS=TextStringObject("app.alert(1)")))
    return _bytes(w)


def page_aa_js(marker: str = "") -> bytes:
    """頁面的 additional actions（/AA）帶 JavaScript。"""
    w = _writer(marker=marker)
    w.pages[0][_name("/AA")] = DictionaryObject({_name("/O"): _action("/JavaScript", JS=TextStringObject("x"))})
    return _bytes(w)


def launch() -> bytes:
    """連結註解的動作是 /Launch（開外部程式）。"""
    w = _writer()
    annot = DictionaryObject({
        _name("/Type"): _name("/Annot"), _name("/Subtype"): _name("/Link"),
        _name("/Rect"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]),
        _name("/A"): _action("/Launch", F=TextStringObject("calc.exe")),
    })
    w.pages[0][_name("/Annots")] = ArrayObject([w._add_object(annot)])
    return _bytes(w)


def embedded_file() -> bytes:
    w = _writer()
    w.add_attachment("payload.txt", b"hello")
    return _bytes(w)


def xfa() -> bytes:
    w = _writer()
    w._root_object[_name("/AcroForm")] = DictionaryObject({
        _name("/Fields"): ArrayObject(), _name("/XFA"): ArrayObject(),
    })
    return _bytes(w)


def rich_media() -> bytes:
    w = _writer()
    annot = DictionaryObject({
        _name("/Type"): _name("/Annot"), _name("/Subtype"): _name("/RichMedia"),
        _name("/Rect"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(10), NumberObject(10)]),
    })
    w.pages[0][_name("/Annots")] = ArrayObject([w._add_object(annot)])
    return _bytes(w)


def encrypted(password: str = "") -> bytes:
    """加密（預設空的使用者密碼：任何閱讀器都打得開，照樣拒收）。"""
    w = _writer()
    w.encrypt(user_password=password, owner_password="owner")
    return _bytes(w)
