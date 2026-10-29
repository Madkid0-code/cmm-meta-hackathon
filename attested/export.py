"""Export a built resume as Word (.docx) or PDF — stdlib only.

A tailored resume the seeker cannot download and edit is a screenshot,
not a deliverable. Both formats are generated from the resume's plain
markdown-ish text with zero third-party dependencies: .docx is a minimal
OOXML zip; the PDF is a minimal hand-laid-out text document.
"""
import io
import re
import zipfile
from xml.sax.saxutils import escape


def _blocks(md_text):
    """Split resume text into (kind, text) blocks: h1/h2/h3/bullet/para."""
    out = []
    for raw in md_text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if line.startswith("### "):
            out.append(("h3", line[4:].strip()))
        elif line.startswith("## "):
            out.append(("h2", line[3:].strip()))
        elif line.startswith("# "):
            out.append(("h1", line[2:].strip()))
        elif re.match(r"^\s*[-*] ", line):
            out.append(("bullet", re.sub(r"^\s*[-*] ", "", line).strip()))
        else:
            out.append(("para", line.strip()))
    return out


def _clean(text):
    return text.replace("**", "").replace("`", "")


# ---------------------------------------------------------------- .docx
_DOCX_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
    "<w:body>{body}</w:body></w:document>")
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    "</Types>")
_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    "</Relationships>")
_STYLES = {"h1": ("Title", 32), "h2": ("Heading1", 26), "h3": ("Heading2", 22),
           "para": (None, 21), "bullet": (None, 21)}


def to_docx(md_text):
    paras = []
    for kind, text in _blocks(md_text):
        style, half_points = _STYLES[kind]
        text = escape(_clean(text))
        prefix = "• " if kind == "bullet" else ""
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        paras.append(
            f"<w:p>{ppr}<w:r><w:rPr><w:sz w:val=\"{half_points}\"/></w:rPr>"
            f"<w:t xml:space=\"preserve\">{prefix}{text}</w:t></w:r></w:p>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", _CONTENT_TYPES)
        z.writestr("_rels/.rels", _RELS)
        z.writestr("word/document.xml", _DOCX_XML.format(body="".join(paras)))
    return buf.getvalue()


# ----------------------------------------------------------------- .pdf
def _pdf_escape(text):
    return (text.replace("\\", "\\\\").replace("(", "\\(")
            .replace(")", "\\)"))


def _wrap(text, width):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines or [""]


def to_pdf(md_text):
    """Minimal multi-page text PDF (Helvetica), laid out from the blocks."""
    pages, page = [], []

    def flush():
        if page:
            pages.append(list(page))
            page.clear()

    for kind, text in _blocks(md_text):
        text = _clean(text)
        if kind == "h1":
            lines, size, gap = _wrap(text, 38), 20, 10
        elif kind == "h2":
            lines, size, gap = _wrap(text, 48), 14, 8
        elif kind == "h3":
            lines, size, gap = _wrap(text, 55), 12, 6
        elif kind == "bullet":
            lines, size, gap = _wrap(text, 88), 10.5, 3
            lines = ["- " + lines[0]] + ["  " + l for l in lines[1:]]
        else:
            lines, size, gap = _wrap(text, 92), 10.5, 3
        for line in lines:
            page.append((size, line))
            if len(page) > 52:
                flush()
        page.append((gap, ""))
        if len(page) > 52:
            flush()
    flush()
    if not pages:
        pages = [[(10.5, "")]]

    objects, offsets = [], []

    def add(obj):
        objects.append(obj if isinstance(obj, bytes) else obj.encode("latin-1"))

    add("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    add(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>")
    for i, lines in enumerate(pages):
        add(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
            f"/BaseFont /Helvetica >> >> >> /Contents {4 + 2 * i} 0 R >>")
        stream = ["BT", "56 736 Td"]
        y = 736
        for size, line in lines:
            if line == "":
                y -= size
                stream.append(f"0 -{size} Td")
                continue
            safe = _pdf_escape(line).encode("latin-1", "replace").decode("latin-1")
            stream.append(f"/F1 {size} Tf ({safe}) Tj 0 -{size + 4} Td")
            y -= size + 4
        stream.append("ET")
        content = "\n".join(stream)
        add(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    for n, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode())
        out.write(obj)
        out.write(b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
              f"startxref\n{xref}\n%%EOF".encode())
    return out.getvalue()
