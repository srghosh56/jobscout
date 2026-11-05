"""CV upload parsing. CVs are personal data: processed in memory only, never logged,
never written to disk."""

from __future__ import annotations

import io
import re

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_BYTES = 2 * 1024 * 1024
MAX_PAGES = 10
MAX_CHARS = 20000


class CvParseError(ValueError):
    pass


def extract_text(filename: str, data: bytes, content_type: str | None = None) -> str:
    if len(data) > MAX_BYTES:
        raise CvParseError(f"file too large (max {MAX_BYTES // 1024 // 1024} MB)")
    is_pdf = (
        data[:5] == b"%PDF-"
        or (content_type or "").endswith("pdf")
        or filename.lower().endswith(".pdf")
    )
    if is_pdf:
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise CvParseError("encrypted PDFs are not supported")
            pages = reader.pages[:MAX_PAGES]
            text = "\n".join((p.extract_text() or "") for p in pages)
        except PdfReadError as exc:
            raise CvParseError("could not read the PDF") from exc
    else:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
    text = re.sub(r"[ \t]+", " ", text).strip()
    if len(text) < 50:
        raise CvParseError("could not find enough text in the CV (is it a scanned image?)")
    return text[:MAX_CHARS]


EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
PHONE = re.compile(r"(?:\+|00)?\d[\d\s/()-]{7,}\d")
URL = re.compile(r"https?://\S+|www\.\S+|linkedin\.com/\S+")


def scrub_pii(text: str) -> str:
    """Remove obvious contact details before the text is sent to an LLM."""
    text = EMAIL.sub("[email]", text)
    text = URL.sub("[url]", text)
    return PHONE.sub("[phone]", text)
