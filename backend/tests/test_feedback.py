"""Feedback drafting, editing, PDF export, zip and CSV. Invented names; no network."""
import csv
import io
import json
import zipfile
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import ai

CONFIG = Path(__file__).resolve().parents[2] / "config" / "assignments"
AID = "als10a-2026-poster"
SUFFIX = "_Submitted file_trn-oid---14504-100000001.pdf"
FULL = {"delivery": 5.5, "understanding": 4, "literature": 5,
        "citations_examples": 4, "analysis": 3.75, "design": 4}


def _pdf(text):
    doc = pymupdf.open(); page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(40, 40, 560, 800), text, fontsize=11)
    return doc.tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import main
    from app.settings import settings
    monkeypatch.setattr(settings, "config_dir", CONFIG)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "together_api_key", "test-key")
    monkeypatch.setattr(settings, "together_model_text", "test/model")
    with TestClient(main.app) as c:
        c.post(f"/api/assignments/{AID}/import", data={"group": "TG06"}, files=[
            ("files", ("NIE TAN MEI LING_Poster" + SUFFIX, _pdf("Sleep and GPA\nTan Mei Ling"), "application/pdf"))])
        c.sid = c.get(f"/api/assignments/{AID}/students").json()[0]["id"]
        yield c


def test_draft_requires_complete_marks(client):
    r = client.post(f"/api/students/{client.sid}/ai/draft-feedback/preview")
    assert r.status_code == 422 and "every criterion" in r.json()["detail"]


def test_draft_edit_export_flow(client, monkeypatch, tmp_path):
    sid = client.sid
    client.put(f"/api/students/{sid}/assessment", json={
        "marks": FULL, "comment": "Mei Ling's question is clear; layout crowded.",
        "presentation_note": "• Engaging, some humour"})

    p = client.post(f"/api/students/{sid}/ai/draft-feedback/preview").json()
    blob = json.dumps(p["messages"])
    assert "Mei Ling" not in blob and "Tan Mei Ling" not in blob
    assert "OVERALL GRADE: A-" in blob

    def fake(provider, model, messages, schema=None, max_tokens=0):
        assert schema and "poster" in schema["properties"]
        return {"content": json.dumps({"poster": "Your question is clear.\n\nConsider spacing.",
                                       "presentation": "You engaged the audience."}), "usage": {}}

    monkeypatch.setattr(ai, "call_chat", fake)
    client.post(f"/api/ai/requests/{p['request_id']}/send")
    f = client.get(f"/api/students/{sid}/feedback").json()
    assert f["poster"].startswith("Your question") and f["presentation"] == "You engaged the audience."

    # instructor edits; a second AI draft does not overwrite edited text
    client.put(f"/api/students/{sid}/feedback", json={"poster": "Edited by me."})
    p2 = client.post(f"/api/students/{sid}/ai/draft-feedback/preview").json()
    client.post(f"/api/ai/requests/{p2['request_id']}/send")
    f = client.get(f"/api/students/{sid}/feedback").json()
    assert f["poster"] == "Edited by me." and f["ai_poster"].startswith("Your question")

    # export → versioned PDF, status exported
    f = client.post(f"/api/students/{sid}/export").json()
    assert f["status"] == "exported" and f["exports"][0]["version"] == 1
    pdf = client.get(f["exports"][0]["url"])
    assert pdf.headers["content-type"] == "application/pdf"
    doc = pymupdf.open(stream=pdf.content, filetype="pdf")
    text = "".join(pg.get_text() for pg in doc)
    assert "Tan Mei Ling" in text and "Edited by me." in text and "Overall grade: A-" in text
    assert "Presentation delivery" in text and "Page 1 of" in text
    assert "26.25" not in text                      # marks hidden from students by default

    # editing after export → back to "marked"; export again → v2
    client.put(f"/api/students/{sid}/feedback", json={"presentation": "Changed."})
    assert client.get(f"/api/students/{sid}/feedback").json()["status"] == "marked"
    f = client.post(f"/api/students/{sid}/export").json()
    assert [e["version"] for e in f["exports"]] == [2, 1]

    z = zipfile.ZipFile(io.BytesIO(client.get(f"/api/assignments/{AID}/exports.zip").content))
    assert z.namelist() == ["TG06/Tan_Mei_Ling_feedback_v2.pdf"]

    rows = list(csv.reader(io.StringIO(client.get(f"/api/assignments/{AID}/marks.csv").text.lstrip("﻿"))))
    assert rows[1][:2] == ["TG06", "Tan Mei Ling"] and rows[1][-4:] == ["26.25", "88", "A-", "exported"]


def test_export_needs_feedback(client):
    client.put(f"/api/students/{client.sid}/assessment", json={"marks": FULL})
    r = client.post(f"/api/students/{client.sid}/export")
    assert r.status_code == 422


def test_zip_empty_is_404(client):
    assert client.get(f"/api/assignments/{AID}/exports.zip").status_code == 404
