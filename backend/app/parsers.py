"""File/HTML -> text -> chunks (PLAN §4.3 step 3-4). CPU-bound, so ingest runs parse() in a
ProcessPoolExecutor; everything here must be top-level and picklable."""

import csv
import io

from guru.kb import CHUNK_CHARS, chunk

EXCERPT_CHARS = 2000


class ParseError(Exception):
    """A readable reason shown in the UI (bad file, scanned PDF, empty page...)."""


def parse(kind: str, data: bytes, title: str) -> tuple[list[str], str]:
    """Returns (chunks, excerpt). Re-checks file type by magic bytes, not just the extension."""
    if kind == "csv":
        text = _text(data)
        return csv_chunks(text, title), text[:EXCERPT_CHARS]
    text = {"pdf": _pdf, "docx": _docx, "html": _html}.get(kind, _text)(data)
    if not text.strip():
        raise ParseError("No text found in this source.")
    return chunk(text, title), text[:EXCERPT_CHARS]


def _text(data: bytes) -> str:
    if b"\x00" in data[:8192]:
        raise ParseError("This doesn't look like a text file (binary content).")
    return data.decode("utf-8-sig", errors="replace")


def _pdf(data: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    if not data.startswith(b"%PDF-"):
        raise ParseError("Not a valid PDF file.")
    try:
        pages = [(p.extract_text() or "").strip() for p in PdfReader(io.BytesIO(data)).pages]
    except PyPdfError as e:
        raise ParseError(f"Couldn't read this PDF (damaged or encrypted): {e}") from None
    text = "\n\n".join(p for p in pages if p)
    if len(text) < 20 * len(pages):  # under ~20 characters per page: images, not text
        raise ParseError("This PDF has no text layer (scanned image?). OCR isn't supported.")
    return text


def _docx(data: bytes) -> str:
    from docx import Document

    if not data.startswith(b"PK\x03\x04"):
        raise ParseError("Not a valid DOCX file.")
    doc = Document(io.BytesIO(data))
    out = []
    for p in doc.paragraphs:
        style = (p.style.name or "") if p.style is not None else ""
        if style.startswith("Heading") and style[-1:].isdigit():
            out.append(f"\n{'#' * int(style[-1])} {p.text}\n")  # headings drive chunk() sections
        elif p.text.strip():
            out.append(p.text)
    for t in doc.tables:
        out.append("\n".join(" | ".join(c.text.strip() for c in row.cells) for row in t.rows))
    return "\n\n".join(out)


def _html(data: bytes) -> str:
    import trafilatura

    text = trafilatura.extract(data.decode("utf-8", errors="replace"), output_format="markdown", include_tables=True)
    if not text:
        raise ParseError("Couldn't extract readable text from this page.")
    return text


def csv_chunks(text: str, title: str) -> list[str]:
    """Rows packed up to CHUNK_CHARS, repeating the header in every chunk so each is self-describing."""
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2:
        raise ParseError("The CSV needs a header row and at least one data row.")
    header = ", ".join(rows[0])
    prefix = f"{title}\nColumns: {header}\n"
    chunks, buf = [], []
    for row in rows[1:]:
        line = ", ".join(f"{h}: {v}" for h, v in zip(rows[0], row) if v.strip())
        if buf and len(prefix) + sum(len(b) + 1 for b in buf) + len(line) > CHUNK_CHARS:
            chunks.append(prefix + "\n".join(buf))
            buf = []
        buf.append(line)
    if buf:
        chunks.append(prefix + "\n".join(buf))
    return chunks
