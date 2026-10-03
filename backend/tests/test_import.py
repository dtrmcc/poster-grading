"""Import and assessment tests. All names below are invented."""
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.importer import parse_filename

CONFIG = Path(__file__).resolve().parents[2] / "config" / "assignments"
AID = "als10a-2026-poster"
SUFFIX = "_Submitted file_trn-oid---14504-100000001.pdf"


@pytest.mark.parametrize("fname,full,other,display,matric", [
    ("NIE TAN MEI LING_Poster" + SUFFIX, "TAN MEI LING", None, "Tan Mei Ling", None),
    ("NIE LIM WEI JIE (LIN WEIJIE)_U1234567X Assignment 1" + SUFFIX,
     "LIM WEI JIE", "LIN WEIJIE", "Lim Wei Jie", "U1234567X"),
    ("NIE GOH KAI MING, DARREN_How does X affect Y_ final" + SUFFIX,
     "GOH KAI MING, DARREN", None, "Goh Kai Ming, Darren", None),
    ("NIE PRIYA D-O RAMAN_poster (2)" + SUFFIX, "PRIYA D-O RAMAN", None, "Priya D-o Raman", None),
    ("NIE NUR AISYAH BINTE ISMAIL_Research_Proposal_21 September" + SUFFIX,
     "NUR AISYAH BINTE ISMAIL", None, "Nur Aisyah Binte Ismail", None),
])
def test_parse_filename(fname, full, other, display, matric):
    p = parse_filename(fname)
    assert (p.full_name, p.other_name, p.display_name, p.matric) == (full, other, display, matric)
    assert p.submission_id == "14504-100000001"


def test_parse_rejects_other_files():
    with pytest.raises(ValueError):
        parse_filename("Rubric.pdf")


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=842, height=1191)  # A3 portrait
    page.insert_text((72, 100), text, fontsize=24)
    return doc.tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import main
    from app.settings import settings
    monkeypatch.setattr(settings, "config_dir", CONFIG)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    with TestClient(main.app) as c:
        yield c


def _upload(c, group, files):
    return c.post(f"/api/assignments/{AID}/import", data={"group": group},
                  files=[("files", (n, b, "application/pdf")) for n, b in files])


def test_import_list_and_assess(client):
    files = [("NIE TAN MEI LING_Poster" + SUFFIX, _pdf("Sleep and grades")),
             ("NIE LIM WEI JIE (LIN WEIJIE)_A1" + SUFFIX, _pdf("Feedback and AI")),
             ("Rubric.pdf", _pdf("rubric")),
             ("notes.docx", b"ignored")]
    r = _upload(client, "TG03", files).json()
    assert sorted(r["added"]) == ["Lim Wei Jie", "Tan Mei Ling"]
    assert len(r["errors"]) == 1 and r["errors"][0]["file"] == "Rubric.pdf"

    # re-import identical files: unchanged, pseudonyms stable
    r2 = _upload(client, "TG03", files[:2]).json()
    assert sorted(r2["unchanged"]) == ["Lim Wei Jie", "Tan Mei Ling"]

    students = client.get(f"/api/assignments/{AID}/students").json()
    assert [s["name"] for s in students] == ["Lim Wei Jie", "Tan Mei Ling"]
    assert sorted(s["pseudonym"] for s in students) == ["S01", "S02"]
    assert all(s["status"] == "not_started" for s in students)

    sid = students[1]["id"]
    d = client.get(f"/api/students/{sid}").json()
    assert d["pages"] == 1 and d["has_text"]
    assert client.get(f"/api/students/{sid}/pages/1.jpg").headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/students/{sid}/pdf").status_code == 200

    d = client.put(f"/api/students/{sid}/assessment",
                   json={"comment": "Clear question.", "marks": {"delivery": 5.5}}).json()
    assert d["status"] == "in_progress" and d["assessment"]["marks"] == {"delivery": "5.5"}

    full = {"delivery": 5.5, "understanding": 4, "literature": 5,
            "citations_examples": 4, "analysis": 3.75, "design": 4}
    d = client.put(f"/api/students/{sid}/assessment", json={"marks": full}).json()
    assert d["status"] == "marked" and d["score"]["letter"] == "A-"
    assert d["assessment"]["comment"] == "Clear question."   # untouched by partial update

    assert client.put(f"/api/students/{sid}/assessment",
                      json={"marks": {"design": 4.1}}).status_code == 422


def test_reimport_changed_file_keeps_assessment(client):
    name = "NIE TAN MEI LING_Poster" + SUFFIX
    _upload(client, "TG06", [(name, _pdf("v1"))])
    sid = client.get(f"/api/assignments/{AID}/students").json()[0]["id"]
    client.put(f"/api/students/{sid}/assessment", json={"comment": "keep me"})
    r = _upload(client, "TG06", [(name, _pdf("v2 revised"))]).json()
    assert r["updated"] == ["Tan Mei Ling"]
    assert client.get(f"/api/students/{sid}").json()["assessment"]["comment"] == "keep me"


def test_unknown_group_rejected(client):
    assert _upload(client, "TG99", [("NIE A B_x" + SUFFIX, _pdf("x"))]).status_code == 422
