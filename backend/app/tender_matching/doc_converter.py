"""Optional legacy .doc -> .docx conversion.

Two pluggable converters, tried in order:

1. Microsoft Word COM (``pywin32``) — free on a Windows box that already has
   Office installed; produces the highest-fidelity ``.docx`` including tables.
2. LibreOffice headless (``soffice``) — free, no Office license, server-safe.

The order is controlled by the ``TENDER_DOC_CONVERTER`` env var
(``auto`` / ``word`` / ``libreoffice`` / ``off``); the default ``auto`` tries
Word first, then LibreOffice.

Both are best-effort.  When neither succeeds the caller falls back to the
``legacy_doc`` text-only parser, so the optional dependency can never break
document ingestion.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_WORD_FILETYPE_DOCX = 16  # wdFormatXMLDocument

_LIBREOFFICE_SOFFICE_CANDIDATES = (
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    "/usr/bin/soffice",
    "/usr/local/bin/soffice",
)


def _find_soffice() -> str:
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in _LIBREOFFICE_SOFFICE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return ""


_CONVERTER_ENV = "TENDER_DOC_CONVERTER"


def _converter_modes() -> list[str]:
    """Return the ordered converter modes to attempt, driven by env var.

    ``TENDER_DOC_CONVERTER``:
    - ``auto`` (default / unset) -> ``["word", "libreoffice"]``
    - ``word`` / ``word_com``  -> ``["word"]``
    - ``libreoffice`` / ``soffice`` -> ``["libreoffice"]``
    - ``off`` / ``none`` / ``disabled`` -> ``[]``
    """
    raw = (os.environ.get(_CONVERTER_ENV) or "auto").strip().lower()
    if raw in {"word", "word_com", "wordcom"}:
        return ["word"]
    if raw in {"libreoffice", "lo", "soffice"}:
        return ["libreoffice"]
    if raw in {"off", "none", "disabled", "0", "false"}:
        return []
    return ["word", "libreoffice"]


def _convert_with_word(doc_path: Path, docx_path: Path) -> bool:
    """Convert via a fresh Word.Application instance (Word 2010+)."""
    try:
        import pythoncom
        import win32com.client
    except Exception:
        return False
    coinit_ok = False
    try:
        try:
            coinit_ok = pythoncom.CoInitialize() == 0  # S_OK == 0
        except Exception:
            return False
        word = None
        try:
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            try:
                word.DisplayAlerts = 0
            except Exception:
                pass
            document = word.Documents.Open(
                str(doc_path),
                ReadOnly=True,
                AddToRecentFiles=False,
            )
            try:
                saver = getattr(document, "SaveAs2", None) or document.SaveAs
                saver(str(docx_path), FileFormat=_WORD_FILETYPE_DOCX)
            finally:
                try:
                    document.Close(False)
                except Exception:
                    pass
            return docx_path.is_file() and docx_path.stat().st_size > 0
        finally:
            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass
    except Exception:
        return False
    finally:
        if coinit_ok:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _convert_with_libreoffice(doc_path: Path, out_dir: Path) -> Path | None:
    soffice = _find_soffice()
    if not soffice:
        return None
    profile_dir = out_dir / f"lo_profile_{os.getpid()}"
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        soffice,
        "--headless",
        "--norestore",
        "--convert-to",
        "docx",
        "--outdir",
        str(out_dir),
        f"-env:UserInstallation=file:///{profile_dir.as_posix()}",
        str(doc_path),
    ]
    try:
        subprocess.run(
            command,
            timeout=90.0,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except Exception:
        return None
    docx_path = out_dir / (doc_path.stem + ".docx")
    if docx_path.is_file() and docx_path.stat().st_size > 0:
        return docx_path
    return None


def convert_doc_to_docx(content: bytes) -> tuple[bytes | None, str]:
    """Convert ``.doc`` bytes to ``.docx`` bytes, best-effort.

    Returns ``(docx_bytes, method)`` where method is ``"word_com"`` or
    ``"libreoffice"``; otherwise ``(None, reason)``.
    """
    if len(content) < 32 or content[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        return None, "not_ole_doc"
    modes = _converter_modes()
    if not modes:
        return None, "converter_disabled"
    tmp_dir = Path(tempfile.mkdtemp(prefix="tender_doc_convert_"))
    try:
        doc_path = tmp_dir / "input.doc"
        doc_path.write_bytes(content)

        for mode in modes:
            if mode == "word":
                docx_path = tmp_dir / "converted.docx"
                if _convert_with_word(doc_path, docx_path):
                    return docx_path.read_bytes(), "word_com"
            elif mode == "libreoffice":
                out_dir = tmp_dir / "lo_out"
                out_dir.mkdir(parents=True, exist_ok=True)
                libreoffice_docx = _convert_with_libreoffice(doc_path, out_dir)
                if libreoffice_docx is not None:
                    return libreoffice_docx.read_bytes(), "libreoffice"

        return None, "no_converter"
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
