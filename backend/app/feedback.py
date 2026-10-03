"""Student feedback: AI drafting prompt and PDF rendering.

The AI only drafts. The instructor edits and approves the text; the PDF is built
locally from the approved text, the final marks and the rubric.
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Optional

import pymupdf

from .rubric import Assignment, ScoreResult

FEEDBACK_SCHEMA = {
    "type": "object",
    "properties": {"poster": {"type": "string"}, "presentation": {"type": "string"}},
    "required": ["poster", "presentation"],
}

SYSTEM_FEEDBACK = """You draft written feedback from a university lecturer to a pre-service \
teacher on an academic poster and a short in-class poster presentation. The lecturer will edit \
your draft before it is given to the student.

Write two sections:
- "poster": feedback on the poster itself (understanding of the topic, use and integration of \
literature, citations and examples, rigour of reasoning/analysis, organisation and design). Base \
it mainly on the INSTRUCTOR COMMENTS; use the POSTER TEXT only for concrete, accurate details. \
About 150-220 words.
- "presentation": feedback on the in-class presentation, based ONLY on the PRESENTATION NOTE \
(the lecturer's live observations). About 80-140 words. If there is no note, return an empty string.

Style:
- Address the student directly as "you". Do not use names or placeholders such as [S07].
- Warm, professional and specific. Start with genuine strengths, then give two or three concrete, \
actionable suggestions. Plain paragraphs, no headings, no bullet points.
- Be consistent with the grades given: do not praise as excellent what was graded as average, \
and do not be harsh about what was graded highly.
- Do not mention marks, numbers of marks, letter grades or percentages.
- Do not invent observations that are not supported by the sources. Timestamps in the note \
(e.g. "0327" or "3:49") are talk durations or times; mention timing only if the note comments on it.
- British/Singapore English spelling.
- Respond ONLY with JSON: {"poster": "<text>", "presentation": "<text>"}"""


def band(letter: Optional[str]) -> Optional[str]:
    return letter[0] if letter else None


def build_feedback_messages(a: Assignment, score: ScoreResult, poster_text: str, note: str,
                            comment: str) -> list[dict]:
    dims = {d.id: d for d in a.dimensions}
    lines = [f"ASSIGNMENT: {a.assignment.course} — {a.assignment.title}",
             f"OVERALL GRADE: {score.letter}", "", "GRADES BY DIMENSION (with the rubric descriptor for that grade):"]
    for ds in score.dimensions:
        d = dims[ds.id]
        desc = d.descriptors.get(band(ds.letter) or "") or ""
        lines.append(f"- {d.name} [{d.feedback_section} section]: {ds.letter} — {desc}")
    pt = poster_text.strip()
    if len(pt) > 12000:
        pt = pt[:12000] + "\n[…truncated]"
    lines += ["", "=== INSTRUCTOR COMMENTS ===", comment.strip() or "(none)",
              "", "=== PRESENTATION NOTE (lecturer's live observations) ===", note.strip() or "(none)",
              "", "=== POSTER TEXT ===", pt or "(no extractable text)"]
    return [{"role": "system", "content": SYSTEM_FEEDBACK},
            {"role": "user", "content": "\n".join(lines)}]


# ------------------------------------------------------------------ PDF

CSS = """
body { font-family: sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1d1d1f; }
h1 { font-size: 15pt; color: #2f5d8a; margin: 0 0 4pt 0; }
h2 { font-size: 12pt; color: #2f5d8a; margin: 16pt 0 5pt 0; }
h3 { font-size: 10.5pt; margin: 10pt 0 3pt 0; }
p { margin: 0 0 7pt 0; }
.meta { color: #555; font-size: 9.5pt; }
.grade { font-size: 13pt; font-weight: bold; }
table { border-collapse: collapse; width: 100%; }
th, td { border: 1px solid #b8b8b8; padding: 4pt 5pt; vertical-align: top; font-size: 9.5pt; }
th { background-color: #eef2f6; text-align: left; }
td.c { text-align: center; white-space: nowrap; }
.small { font-size: 8.5pt; color: #555; }
table.rubric td, table.rubric th { font-size: 8.5pt; line-height: 1.3; padding: 3pt 4pt; }
td.hit { background-color: #dce8f4; font-weight: bold; }
"""


def _paras(text: str) -> str:
    blocks = [b.strip() for b in text.replace("\r", "").split("\n\n") if b.strip()]
    return "".join(f"<p>{html.escape(b).replace(chr(10), '<br/>')}</p>" for b in blocks) or "<p>—</p>"


def build_html(a: Assignment, student_name: str, group: str, score: ScoreResult,
               poster_fb: str, presentation_fb: str, when: datetime) -> list[str]:
    """Returns HTML parts; each part starts on a new page (feedback, then rubric)."""
    e = html.escape
    f = a.feedback
    show_marks = f.show_marks_to_student
    title = f.document_title or f"{a.assignment.course} — {a.assignment.title}: Feedback"
    dims = {d.id: d for d in a.dimensions}
    out = [f"<h1>{e(title)}</h1>",
           f"<p class='meta'><b>Student:</b> {e(student_name)} &nbsp;&nbsp; <b>Group:</b> {e(group)}"
           f" &nbsp;&nbsp; <b>Date:</b> {when:%d %B %Y}</p>",
           f"<p class='grade'>Overall grade: {e(score.letter or '—')}"
           + (f" &nbsp;({score.total_marks.normalize():f} / {score.max_marks:f})" if show_marks else "")
           + "</p>"]
    if f.include_dimension_grades:
        out.append("<h2>Assessment by criterion</h2><table><tr><th>Criterion</th><th>Grade</th>"
                   + ("<th>Marks</th>" if show_marks else "") + "<th>Rubric descriptor</th></tr>")
        for ds in score.dimensions:
            d = dims[ds.id]
            desc = d.descriptors.get(band(ds.letter) or "") or ""
            marks = (f"<td class='c'>{ds.marks.normalize():f} / {d.max_marks.normalize():f}</td>"
                     if show_marks and ds.marks is not None else ("<td></td>" if show_marks else ""))
            out.append(f"<tr><td>{e(d.name)}</td><td class='c'>{e(ds.letter or '—')}</td>{marks}"
                       f"<td>{e(desc)}</td></tr>")
        out.append("</table>")
    sections = {s.id: s.title for s in f.sections}
    out.append(f"<h2>{e(sections.get('poster', 'Feedback on your poster'))}</h2>{_paras(poster_fb)}")
    out.append(f"<h2>{e(sections.get('presentation', 'Feedback on your presentation'))}</h2>"
               f"{_paras(presentation_fb)}")
    if f.signed_by:
        out.append(f"<p style='margin-top:14pt'>{e(f.signed_by)}</p>")
    parts = ["".join(out)]
    if f.include_rubric:
        got = {ds.id: band(ds.letter) for ds in score.dimensions}
        r = [f"<h2>Rubric: {e(a.assignment.title)}</h2>",
             "<p class='small'>The shaded cell in each row is the grade band you received.</p>",
             "<table class='rubric'><tr><th>Criterion</th><th>A+ to A-</th><th>B+ to B-</th>"
             "<th>C+ to C</th><th>D+ to D</th><th>F</th></tr>"]
        for d in a.dimensions:
            cells = "".join(
                f"<td{' class=hit' if got.get(d.id) == b else ''}>{e(d.descriptors.get(b) or '')}</td>"
                for b in "ABCDF")
            r.append(f"<tr><td><b>{e(d.name)}</b></td>{cells}</tr>")
        r.append("</table>")
        parts.append("".join(r))
    return parts


def render_pdf(html_parts: list[str], out_path: Path, footer: str) -> int:
    """Render HTML parts to an A4 PDF (each part on a new page) with page numbers."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.pdf")
    writer = pymupdf.DocumentWriter(str(tmp))
    page = pymupdf.paper_rect("a4")
    where = page + (54, 50, -54, -56)
    for part in html_parts:
        story = pymupdf.Story(html=part, user_css=CSS)
        more = 1
        while more:
            dev = writer.begin_page(page)
            more, _ = story.place(where)
            story.draw(dev)
            writer.end_page()
    writer.close()
    doc = pymupdf.open(tmp)
    n = doc.page_count
    for i, p in enumerate(doc, start=1):
        p.insert_text((54, page.height - 30), footer, fontsize=7.5, color=(0.4, 0.4, 0.4))
        label = f"Page {i} of {n}"
        p.insert_text((page.width - 54 - pymupdf.get_text_length(label, fontsize=7.5), page.height - 30),
                      label, fontsize=7.5, color=(0.4, 0.4, 0.4))
    doc.set_metadata({"title": footer, "creator": "Poster Grading"})
    doc.save(out_path, garbage=3, deflate=True)
    doc.close()
    tmp.unlink(missing_ok=True)
    return n
