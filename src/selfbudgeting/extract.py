"""PDF -> plain text. Isolated in its own module because this is the piece
most likely to need swapping (pdfplumber vs pymupdf/fitz) if one gives
cleaner text for Sberbank's layout -- parsers only ever see the resulting
string, never the PDF itself."""
from __future__ import annotations

from pathlib import Path


def extract_text(pdf_path: Path) -> str:
    import pdfplumber

    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)
