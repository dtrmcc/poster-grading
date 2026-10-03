"""Presentation notes: split a Word file into per-student blocks and suggest matches.

The notes are written live during presentations, one list per student:
a top-level list item (level 0) starts a new student; deeper levels and plain
paragraphs belong to the current student. Students are not named in the file,
so blocks are matched to students afterwards. Suggestions are computed locally
from word overlap between each block and each poster's text — no AI involved.
"""
from __future__ import annotations

import io
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Optional

import docx
from docx.oxml.ns import qn

LIST_STYLE_RE = re.compile(r"^List (Bullet|Number|Continue)(?: (\d))?$", re.IGNORECASE)
INDENT = {0: "• ", 1: "   – ", 2: "      · "}


@dataclass
class Line:
    level: Optional[int]   # None = not a list item
    text: str


def read_lines(data: bytes) -> list[Line]:
    d = docx.Document(io.BytesIO(data))
    lines: list[Line] = []
    for p in d.paragraphs:
        text = " ".join(p.text.split())
        if not text:
            continue
        level = None
        ppr = p._p.pPr
        if ppr is not None and ppr.numPr is not None:
            ilvl = ppr.numPr.find(qn("w:ilvl"))
            level = int(ilvl.get(qn("w:val"))) if ilvl is not None else 0
        else:
            m = LIST_STYLE_RE.match(p.style.name if p.style is not None else "")
            if m and m.group(1).lower() != "continue":
                level = int(m.group(2)) - 1 if m.group(2) else 0
        lines.append(Line(level, text))
    return lines


def split_blocks(lines: list[Line]) -> list[str]:
    """One block per top-level list item. Falls back to one block per paragraph
    when the document has no top-level list items."""
    if not any(l.level == 0 for l in lines):
        return [l.text for l in lines]
    blocks: list[list[str]] = []
    for l in lines:
        if l.level == 0 or not blocks:
            blocks.append([])
        lvl = l.level if l.level is not None else 1
        blocks[-1].append(INDENT.get(min(lvl, 2), INDENT[2]) + l.text)
    return ["\n".join(b) for b in blocks]


ABSENT_RE = re.compile(r"\b(medical leave|absent|did not present|no show|MC)\b", re.IGNORECASE)


def looks_absent(block: str) -> bool:
    return len(block) < 80 and bool(ABSENT_RE.search(block))


# ------------------------------------------------------------- suggestions

STOP = set("""
a about above after again against all also although am an and any are as at be because been
before being below between both but by can could did do does doing down during each even few
for from further good had has have having he her here hers him his how however i if in into
is it its itself just less like may me might more most much must my no nor not now of off on
once only or other our out over own quite rather really same she should so some such than that
the their them then there these they this those through to too under until up upon very was
way we well were what when where which while who whom why will with within would you your
student students poster presentation slide slides talk speech audience bit lot good very also
there point points research study topic minute minutes time
""".split())

WORD_RE = re.compile(r"[A-Za-z][A-Za-z\-']{2,}")


def _tokens(text: str) -> list[str]:
    out = []
    for w in WORD_RE.findall(text.lower()):
        w = w.strip("-'")
        if len(w) < 4 or w in STOP:
            continue
        # crude stemming so "feedbacks"/"feedback", "motivated"/"motivation" meet
        for suf in ("ations", "ation", "ings", "ing", "ies", "ed", "es", "s"):
            if w.endswith(suf) and len(w) - len(suf) >= 4:
                w = w[: -len(suf)]
                break
        out.append(w)
    return out


@dataclass
class Suggestion:
    student_id: int
    score: float
    confident: bool


def suggest(blocks: list[str], posters: dict[int, str]) -> list[Optional[Suggestion]]:
    """Greedy one-to-one assignment by keyword overlap weighted by IDF."""
    if not posters:
        return [None] * len(blocks)
    poster_tokens = {sid: set(_tokens(t)) for sid, t in posters.items()}
    df = Counter(tok for toks in poster_tokens.values() for tok in toks)
    n = len(posters)
    idf = {tok: math.log((n + 1) / (c + 0.5)) for tok, c in df.items()}

    scores: list[dict[int, float]] = []
    for b in blocks:
        bt = set(_tokens(b))
        scores.append({sid: sum(idf[t] for t in bt & pt) for sid, pt in poster_tokens.items()})

    pairs = sorted(
        ((s, bi, sid) for bi, row in enumerate(scores) for sid, s in row.items() if s > 0),
        reverse=True,
    )
    # Thresholds scale with class size: one shared word that appears on only one
    # poster is worth `unique`; require half of that to suggest, two such words
    # (and a clear lead over the runner-up) to call it strong.
    unique = math.log((n + 1) / 1.5)
    result: list[Optional[Suggestion]] = [None] * len(blocks)
    used: set[int] = set()
    for s, bi, sid in pairs:
        if result[bi] is not None or sid in used or s < 0.5 * unique or looks_absent(blocks[bi]):
            continue
        others = sorted((v for k, v in scores[bi].items() if k != sid), reverse=True)
        runner_up = others[0] if others else 0.0
        result[bi] = Suggestion(sid, round(s, 2), confident=s >= 2 * unique and s >= 1.6 * runner_up)
        used.add(sid)
    return result


def poster_title(text: str, avoid: list[str] = (), limit: int = 90) -> str:
    """First meaningful line of the poster text (usually the title), used to help
    recognise students. Lines containing the student's own name are skipped."""
    avoid_l = {a.lower() for a in avoid if len(a) > 2}
    for line in text.splitlines():
        line = " ".join(line.split())
        words = {w.lower() for w in re.findall(r"[A-Za-z]+", line)}
        if len(line) < 12 or words & avoid_l:
            continue
        if re.search(r"U\d{7}[A-Z]|national institute|nanyang|als10a|^tg\d", line, re.IGNORECASE):
            continue
        return line[:limit]
    return ""
