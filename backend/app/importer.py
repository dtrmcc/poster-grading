"""Import student submissions.

Submission filenames exported from the LMS look like:

    NIE <FULL NAME>[ (<OTHER NAME>)]_<student's own filename>_Submitted file_trn-oid---<id>.pdf

Everything parsed here is identifying data and stays in the local database.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pymupdf

FILENAME_RE = re.compile(
    r"^NIE (?P<name>[^_]+?)(?: \((?P<other>[^)]*)\))?_(?P<rest>.*?)"
    r"(?:_Submitted file_trn-oid---(?P<sid>[\d-]+))?\.pdf$",
    re.IGNORECASE,
)
MATRIC_RE = re.compile(r"(?<![A-Za-z0-9])U\d{7}[A-Z](?![A-Za-z0-9])", re.IGNORECASE)

# Long side of rendered page images, in pixels.
RENDER_LONG_SIDE = 3200


@dataclass
class ParsedName:
    full_name: str                 # as in the LMS, upper case
    other_name: Optional[str]      # e.g. Chinese name in pinyin
    display_name: str              # title case for the UI
    submission_id: Optional[str]
    matric: Optional[str]
    name_tokens: list[str] = field(default_factory=list)  # for de-identification later


def _title(s: str) -> str:
    return " ".join(w.capitalize() if w.isupper() or w.islower() else w for w in s.split())


def parse_filename(filename: str) -> ParsedName:
    m = FILENAME_RE.match(filename.strip())
    if not m:
        raise ValueError(f"unrecognised submission filename: {filename!r}")
    full = " ".join(m.group("name").split())
    other = " ".join(m.group("other").split()) if m.group("other") else None
    matric = MATRIC_RE.search(filename)
    tokens = {t for t in re.split(r"[\s,()]+", f"{full} {other or ''}") if len(t) > 1}
    return ParsedName(
        full_name=full,
        other_name=other,
        display_name=_title(full),
        submission_id=m.group("sid"),
        matric=matric.group(0).upper() if matric else None,
        name_tokens=sorted(tokens),
    )


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass
class RenderedPdf:
    pages: int
    text: str


def render_pdf(pdf_path: Path, out_dir: Path) -> RenderedPdf:
    """Render each page to JPEG (page-1.jpg, …) and extract text."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("page-*.jpg"):
        old.unlink()
    texts = []
    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            rect = page.rect
            zoom = RENDER_LONG_SIDE / max(rect.width, rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
            (out_dir / f"page-{i}.jpg").write_bytes(pix.tobytes(output="jpeg", jpg_quality=85))
            texts.append(page.get_text("text"))
        return RenderedPdf(pages=doc.page_count, text="\n\f\n".join(texts))
