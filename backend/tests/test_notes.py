"""Presentation-note import tests. All content is invented."""
import io
from pathlib import Path

import docx
import pymupdf
import pytest
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from fastapi.testclient import TestClient

from app import notes

CONFIG = Path(__file__).resolve().parents[2] / "config" / "assignments"
AID = "als10a-2026-poster"
SUFFIX = "_Submitted file_trn-oid---14504-100000001.pdf"


def _docx(lines):
    """lines: list of (level|None, text). Level is written as direct numPr, like Word does."""
    d = docx.Document()
    for level, text in lines:
        p = d.add_paragraph(text, style="List Paragraph")
        if level is not None:
            ppr = p._p.get_or_add_pPr()
            num = OxmlElement("w:numPr")
            ilvl = OxmlElement("w:ilvl"); ilvl.set(qn("w:val"), str(level))
            nid = OxmlElement("w:numId"); nid.set(qn("w:val"), "1")
            num.append(ilvl); num.append(nid); ppr.append(num)
    buf = io.BytesIO(); d.save(buf)
    return buf.getvalue()


NOTE = [
    (0, "Very interactive with the audience; local context on sleep"),
    (None, "Sleep hours linked to GPA"),
    (None, "Some humour"),
    (0, "AI generated feedback 0327"),
    (1, "Speech a bit fast"),
    (1, "Findings on self-correction of errors are useful"),
    (0, "Medical leave"),
    (0, "Translanguaging in English lessons 3:49"),
    (1, "Good positionality"),
    (2, "MOE tensions discussed"),
]


def test_split_blocks_by_top_level_items():
    blocks = notes.split_blocks(notes.read_lines(_docx(NOTE)))
    assert len(blocks) == 4
    assert blocks[0].splitlines() == [
        "• Very interactive with the audience; local context on sleep",
        "   – Sleep hours linked to GPA", "   – Some humour"]
    assert "      · MOE tensions discussed" in blocks[3]
    assert notes.looks_absent(blocks[2]) and not notes.looks_absent(blocks[0])


def test_split_without_lists_falls_back_to_paragraphs():
    blocks = notes.split_blocks(notes.read_lines(_docx([(None, "one"), (None, "two")])))
    assert blocks == ["one", "two"]


def test_poster_title_skips_names():
    text = "Tan Mei Ling\nU1234567X\nSleep Quality and Academic Performance\nIntro"
    assert notes.poster_title(text, ["TAN", "MEI", "LING"]) == "Sleep Quality and Academic Performance"


def _pdf(text):
    doc = pymupdf.open(); page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(40, 40, 560, 800), text, fontsize=11)
    return doc.tobytes()


POSTERS = {
    "NIE TAN MEI LING_Poster": "Sleep Quality and GPA\nHow sleep hours relate to GPA among "
                               "university students in a local context.",
    "NIE LIM WEI JIE_Poster": "AI Generated Feedback and Error Self-Correction\nDoes AI "
                              "feedback help students with self-correction of writing errors?",
    "NIE NUR AISYAH BINTE ISMAIL_Poster": "Translanguaging in Secondary English Lessons\n"
                                          "Teacher positionality, MOE policy and translanguaging.",
    "NIE GOH KAI MING_Poster": "Music Practice and Enjoyment\nEnjoyment sustains motivation "
                               "to learn an instrument.",
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import main
    from app.settings import settings
    monkeypatch.setattr(settings, "config_dir", CONFIG)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(main.app) as c:
        c.post(f"/api/assignments/{AID}/import", data={"group": "TG03"},
               files=[("files", (n + SUFFIX, _pdf(t), "application/pdf")) for n, t in POSTERS.items()])
        yield c


def _import(c, data=None):
    return c.post(f"/api/assignments/{AID}/notes/import", data={"group": "TG03"},
                  files={"file": ("TG03 Presentation Note.docx", data or _docx(NOTE),
                                  "application/octet-stream")})


def test_import_suggest_and_apply(client):
    students = {s["name"]: s["id"] for s in client.get(f"/api/assignments/{AID}/students").json()}
    r = _import(client).json()
    blocks = r["blocks"]
    assert len(blocks) == 4
    assert blocks[0]["suggested_student_id"] == students["Tan Mei Ling"]
    assert blocks[1]["suggested_student_id"] == students["Lim Wei Jie"]
    assert blocks[2]["ignored"] and blocks[2]["suggested_student_id"] is None
    assert blocks[3]["suggested_student_id"] == students["Nur Aisyah Binte Ismail"]

    # instructor confirms suggestions, apply
    matches = [{"block_id": b["id"], "student_id": b["student_id"], "ignored": b["ignored"]}
               for b in blocks]
    rep = client.post(f"/api/assignments/{AID}/notes/TG03/apply", json={"matches": matches}).json()
    assert len(rep["applied"]) == 3 and rep["unmatched_blocks"] == 0
    note = client.get(f"/api/students/{students['Lim Wei Jie']}").json()["assessment"]["presentation_note"]
    assert note.startswith("• AI generated feedback 0327")

    # instructor edits a note; re-applying does not overwrite unless asked
    client.put(f"/api/students/{students['Lim Wei Jie']}/assessment", json={"presentation_note": "edited"})
    rep = client.post(f"/api/assignments/{AID}/notes/TG03/apply", json={"matches": matches}).json()
    assert rep["kept_existing"] == ["Lim Wei Jie"] and len(rep["unchanged"]) == 2
    rep = client.post(f"/api/assignments/{AID}/notes/TG03/apply",
                      json={"matches": matches, "overwrite": True}).json()
    assert rep["applied"] == ["Lim Wei Jie"]

    # the saved matching can be reopened
    again = client.get(f"/api/assignments/{AID}/notes/TG03").json()
    assert again["applied_at"] and [b["student_id"] for b in again["blocks"]] == \
        [b["student_id"] for b in blocks]


def test_two_blocks_for_one_student_are_joined(client):
    sid = client.get(f"/api/assignments/{AID}/students").json()[0]["id"]
    blocks = _import(client).json()["blocks"]
    matches = [{"block_id": blocks[0]["id"], "student_id": sid},
               {"block_id": blocks[3]["id"], "student_id": sid}]
    client.post(f"/api/assignments/{AID}/notes/TG03/apply", json={"matches": matches})
    note = client.get(f"/api/students/{sid}").json()["assessment"]["presentation_note"]
    assert note.count("• ") == 2


def test_reimport_replaces_previous(client):
    _import(client)
    r = _import(client, _docx([(0, "only one block")])).json()
    assert len(r["blocks"]) == 1
    assert len(client.get(f"/api/assignments/{AID}/notes/TG03").json()["blocks"]) == 1


def test_bad_file_and_wrong_student(client):
    assert _import(client, b"not a docx").status_code == 422
    blocks = _import(client).json()["blocks"]
    r = client.post(f"/api/assignments/{AID}/notes/TG03/apply",
                    json={"matches": [{"block_id": blocks[0]["id"], "student_id": 9999}]})
    assert r.status_code == 422
