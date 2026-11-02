"""Offline checks for KB parsing. Run: uv run python -m pytest -q"""

import io

import pytest

from app.parsers import ParseError, csv_chunks, parse


def _pdf(text: str) -> bytes:
    """Smallest valid one-page PDF with a text layer."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    body, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs):
        offsets.append(len(body))
        body += b"%d 0 obj\n%s\nendobj\n" % (i + 1, o)
    xref = b"xref\n0 6\n0000000000 65535 f \n" + b"".join(b"%010d 00000 n \n" % off for off in offsets)
    return body + xref + b"trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n%d\n%%%%EOF\n" % len(body)


def test_pdf_text_and_scanned_rejection():
    chunks, excerpt = parse("pdf", _pdf("Northwind builds retail analytics for thousands of stores worldwide."), "about")
    assert chunks[0].startswith("about") and "Northwind" in excerpt
    with pytest.raises(ParseError, match="no text layer"):
        parse("pdf", _pdf(""), "scan")
    with pytest.raises(ParseError, match="Not a valid PDF"):
        parse("pdf", b"hello", "fake")
    with pytest.raises(ParseError, match="Couldn't read this PDF"):
        parse("pdf", b"%PDF-1.4 truncated", "broken")


def test_docx_headings_become_sections():
    from docx import Document

    doc = Document()
    doc.add_heading("Benefits", level=2)
    doc.add_paragraph("Remote first, with a yearly learning budget.")
    buf = io.BytesIO()
    doc.save(buf)
    chunks, _ = parse("docx", buf.getvalue(), "handbook")
    assert any(c.startswith("handbook > Benefits") for c in chunks)


def test_csv_repeats_header_in_every_chunk():
    rows = "\n".join(f"Store {i},City {i},{'x' * 80}" for i in range(100))
    chunks = csv_chunks(f"store,city,notes\n{rows}", "stores")
    assert len(chunks) > 1
    assert all("Columns: store, city, notes" in c for c in chunks)


def test_binary_rejected_as_text():
    with pytest.raises(ParseError):
        parse("txt", b"\x00\x01binary", "x")
