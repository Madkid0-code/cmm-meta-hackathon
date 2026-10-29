"""Read an uploaded resume file into plain text — stdlib only.

The Safety Check can only audit text, so an uploaded resume (.docx,
.pdf, .txt, .md) is converted here, on the server, with no third-party
parsers. Extraction is deliberately plain: DOCX paragraph text, PDF
text-showing operators from inflated streams, or the file as-is.
"""
import io
import re
import zipfile
import zlib
from xml.sax.saxutils import unescape

MAX_UPLOAD_BYTES = 3 * 1024 * 1024


def _from_docx(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return unescape(xml)


def _pdf_unescape(s):
    return (s.replace("\\(", "(").replace("\\)", ")")
             .replace("\\\\", "\\").replace("\\n", "\n"))


def _from_pdf(data):
    texts = []
    for m in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        chunk = m.group(1)
        try:
            chunk = zlib.decompress(chunk)
        except Exception:
            pass  # uncompressed stream: use as-is
        for t in re.findall(rb"\((.*?)(?<!\\)\)\s*Tj", chunk, re.S):
            texts.append(_pdf_unescape(t.decode("latin-1", "replace")))
        for arr in re.findall(rb"\[(.*?)\]\s*TJ", chunk, re.S):
            parts = re.findall(rb"\((.*?)(?<!\\)\)", arr, re.S)
            if parts:
                texts.append("".join(
                    _pdf_unescape(p.decode("latin-1", "replace"))
                    for p in parts))
    return "\n".join(texts)


def extract_text(filename, data):
    """Return the file's text, or raise ValueError with a user-facing
    message. `data` is the raw uploaded bytes."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("That file is too large (max 3 MB) — "
                         "paste the resume text instead.")
    name = (filename or "").lower()
    try:
        if name.endswith(".docx"):
            text = _from_docx(data)
        elif name.endswith(".pdf"):
            text = _from_pdf(data)
        elif name.endswith((".txt", ".md")):
            text = data.decode("utf-8", "replace")
        else:
            raise ValueError("Please upload a .docx, .pdf, .txt or .md "
                             "file — or paste the resume text instead.")
    except ValueError:
        raise
    except Exception:
        raise ValueError("I couldn't read that file — "
                         "paste the resume text instead.")
    text = text.strip()
    if len(text) < 40:
        raise ValueError("I couldn't find readable text in that file — "
                         "paste the resume text instead.")
    return text
