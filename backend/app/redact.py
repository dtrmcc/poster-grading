"""Redact identifying details from poster images before they leave the Mac.

Automatic boxes come from the PDF's text layer: every place a student's name
(any variant, any classmate), a matriculation number or an e-mail address
appears is covered. Image-only posters have no text layer, so the instructor
draws boxes by hand (photos, logos and anything else can be covered the same way).

Boxes are stored normalised to the page (0..1), so they apply to any render size.
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from .deid import EMAIL_RE, MATRIC_RE, PHONE_RE, Deidentifier, _phrases, _single_words

SEND_LONG_SIDE = 1600       # pixels; enough for layout judgements, cheap to send
MAX_PAGES = 2               # posters are normally one page
PAD = 2.0                   # points of padding around each found box

Box = list[float]           # [x0, y0, x1, y1] normalised


def _norm(r: pymupdf.Rect, page: pymupdf.Rect) -> Box:
    r = pymupdf.Rect(r.x0 - PAD, r.y0 - PAD, r.x1 + PAD, r.y1 + PAD) & page
    return [round(r.x0 / page.width, 5), round(r.y0 / page.height, 5),
            round(r.x1 / page.width, 5), round(r.y1 / page.height, 5)]


def _search_terms(d: Deidentifier) -> list[str]:
    terms: set[str] = set()
    for e in d.roster:
        terms.update(p for p in _phrases(e) if " " in p or len(p) >= 5)
        terms.update(w for w in _single_words(e) if len(w) >= 3)
    return sorted(terms, key=len, reverse=True)


def find_boxes(pdf_path: Path, d: Deidentifier) -> dict:
    """{"has_text": bool, "pages": [[box, …] per page]} for the first MAX_PAGES pages."""
    terms = _search_terms(d)
    pages: list[list[Box]] = []
    has_text = False
    with pymupdf.open(pdf_path) as doc:
        for page in list(doc)[:MAX_PAGES]:
            pr = page.rect
            boxes: list[Box] = []
            words = page.get_text("words")       # (x0, y0, x1, y1, word, block, line, n)
            if words:
                has_text = True
            for t in terms:
                if " " in t:
                    hits = page.search_for(t)    # case-insensitive, across line breaks
                else:                            # single words: whole-word match only
                    hits = [pymupdf.Rect(w[:4]) for w in words
                            if re.sub(r"[^\w-]", "", w[4]).lower() == t.lower()]
                boxes += [_norm(h, pr) for h in hits]
            for w in words:
                if MATRIC_RE.search(w[4]) or EMAIL_RE.search(w[4]) or PHONE_RE.search(w[4]):
                    boxes.append(_norm(pymupdf.Rect(w[:4]), pr))
            # de-duplicate
            seen, uniq = set(), []
            for b in boxes:
                k = tuple(round(v, 3) for v in b)
                if k not in seen:
                    seen.add(k)
                    uniq.append(b)
            pages.append(uniq)
    return {"has_text": has_text, "pages": pages}


def render_redacted(pdf_path: Path, page_no: int, boxes: list[Box],
                    long_side: int = SEND_LONG_SIDE) -> bytes:
    """JPEG of one page (1-based) with all boxes painted solid black."""
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_no - 1]
        pr = page.rect
        zoom = long_side / max(pr.width, pr.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    # paint boxes on the raster itself, so nothing underneath survives
    for x0, y0, x1, y1 in boxes:
        r = pymupdf.IRect(int(x0 * pix.width) - 1, int(y0 * pix.height) - 1,
                          int(x1 * pix.width) + 2, int(y1 * pix.height) + 2) & pix.irect
        if not r.is_empty:
            pix.set_rect(r, (0, 0, 0))
    return pix.tobytes(output="jpeg", jpg_quality=82)


def page_count(pdf_path: Path) -> int:
    with pymupdf.open(pdf_path) as doc:
        return min(doc.page_count, MAX_PAGES)


def valid_box(b) -> bool:
    return (isinstance(b, (list, tuple)) and len(b) == 4
            and all(isinstance(v, (int, float)) for v in b)
            and 0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1)
