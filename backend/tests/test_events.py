"""Blind marking and the activity log. Invented names; no network."""
import json
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import ai

CONFIG = Path(__file__).resolve().parents[2] / "config" / "assignments"
AID = "als10a-2026-poster"
SUFFIX = "_Submitted file_trn-oid---14504-100000001.pdf"
MINE = {"delivery": 5, "understanding": 4, "literature": 5, "citations_examples": 4,
        "analysis": 3.5, "design": 3.5}


def _pdf(t):
    d = pymupdf.open(); p = d.new_page(); p.insert_text((50, 80), t); return d.tobytes()


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import main
    from app.settings import settings
    monkeypatch.setattr(settings, "config_dir", CONFIG)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "together_api_key", "k")
    monkeypatch.setattr(settings, "together_model_text", "text/model")
    monkeypatch.setattr(ai, "call_chat", lambda *a, **k: {"content": json.dumps({"dimensions": [
        {"id": "delivery", "marks": 5.5, "rationale": "r", "evidence": []},
        {"id": "design", "marks": 4, "rationale": "r", "evidence": []}]}), "usage": {}})
    with TestClient(main.app) as c:
        c.post(f"/api/assignments/{AID}/import", data={"group": "TG03"},
               files=[("files", ("NIE TAN MEI LING_Poster" + SUFFIX, _pdf("Sleep"), "application/pdf"))])
        c.sid = c.get(f"/api/assignments/{AID}/students").json()[0]["id"]
        yield c


def _events(c, **q):
    return list(reversed(c.get("/api/events", params=q).json()))


def test_blind_reveal_and_full_trail(client):
    sid = client.sid
    p = client.post(f"/api/students/{sid}/ai/suggest-marks/preview").json()
    d = client.post(f"/api/ai/requests/{p['request_id']}/send").json()
    assert d["ai_marks"]["hidden"] is True

    # instructor marks blind, then reveals
    client.put(f"/api/students/{sid}/assessment", json={"marks": MINE, "comment": "clear"})
    d = client.post(f"/api/students/{sid}/ai/reveal").json()
    assert d["ai_marks"]["hidden"] is False
    assert d["ai_marks"]["own_marks_at_reveal"]["design"] == "3.5"
    client.post(f"/api/students/{sid}/ai/reveal")                  # idempotent

    # instructor adopts the AI's design mark
    client.put(f"/api/students/{sid}/assessment",
               json={"marks": {**MINE, "design": 4}, "actions": [{"type": "use_ai_mark", "dims": ["design"]}]})

    ev = _events(client, student_id=sid)
    types = [e["type"] for e in ev]
    assert types == ["poster_imported", "ai_request_prepared", "ai_result_received",
                     "assessment_changed", "ai_marks_revealed", "assessment_changed"]
    first, reveal, adopt = ev[3], ev[4], ev[5]
    assert first["data"]["ai_suggestion"] == "hidden"
    assert first["data"]["changes"]["marks"]["design"] == {"old": None, "new": "3.5"}
    assert first["data"]["changes"]["comment"] == {"old": "", "new": "clear"}
    assert reveal["data"]["own_marks_complete"] is True and reveal["data"]["ai_marks"]["design"] == "4"
    assert adopt["data"]["ai_suggestion"] == "revealed"
    assert adopt["data"]["changes"] == {"marks": {"design": {"old": "3.5", "new": "4"}}}
    assert adopt["data"]["actions"] == [{"type": "use_ai_mark", "dims": ["design"]}]


def test_no_event_when_nothing_changes(client):
    sid = client.sid
    client.put(f"/api/students/{sid}/assessment", json={"comment": "x"})
    client.put(f"/api/students/{sid}/assessment", json={"comment": "x"})
    assert [e["type"] for e in _events(client, student_id=sid)].count("assessment_changed") == 1


def test_feedback_edits_and_export_logged(client, monkeypatch):
    sid = client.sid
    client.put(f"/api/students/{sid}/assessment", json={"marks": MINE})
    monkeypatch.setattr(ai, "call_chat", lambda *a, **k: {"content": json.dumps(
        {"poster": "AI poster text.", "presentation": "AI talk text."}), "usage": {}})
    p = client.post(f"/api/students/{sid}/ai/draft-feedback/preview").json()
    client.post(f"/api/ai/requests/{p['request_id']}/send")
    client.put(f"/api/students/{sid}/feedback", json={"poster": "My rewrite."})
    client.put(f"/api/students/{sid}/feedback", json={"poster": "AI poster text.",
                                                      "actions": [{"type": "use_ai_draft", "section": "poster"}]})
    client.post(f"/api/students/{sid}/export")
    ev = _events(client, student_id=sid)
    fb = [e for e in ev if e["type"] == "feedback_changed"]
    assert fb[0]["data"]["changes"]["poster"] == {"old": "AI poster text.", "new": "My rewrite."}
    assert fb[1]["data"]["actions"][0]["type"] == "use_ai_draft"
    ex = [e for e in ev if e["type"] == "feedback_exported"][0]["data"]
    assert ex["poster_feedback"] == "AI poster text." and ex["ai_poster_draft"] == "AI poster text."
    assert ex["letter"] and ex["version"] == 1


def test_blind_mode_can_be_switched_off(client, monkeypatch):
    from app.settings import settings
    monkeypatch.setattr(settings, "blind_ai_marks", False)
    p = client.post(f"/api/students/{client.sid}/ai/suggest-marks/preview").json()
    d = client.post(f"/api/ai/requests/{p['request_id']}/send").json()
    assert d["ai_marks"]["hidden"] is False and d["ai_marks"]["dimensions"]
