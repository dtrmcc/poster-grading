"""Poster-image redaction and design review. Invented names; no network."""
import base64
import io
import json
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import ai, redact
from app.deid import Deidentifier, RosterEntry

CONFIG = Path(__file__).resolve().parents[2] / "config" / "assignments"
AID = "als10a-2026-poster"
SUFFIX = "_Submitted file_trn-oid---14504-100000001.pdf"

ROSTER = [RosterEntry("S01", "Tan Mei Ling", "TAN MEI LING", None, None, []),
          RosterEntry("S02", "Lim Wei Jie", "LIM WEI JIE", "LIN WEIJIE", None, [])]


def _poster(path=None):
    doc = pymupdf.open()
    p = doc.new_page(width=842, height=1191)
    p.insert_text((60, 80), "Sleep and Academic Performance", fontsize=28)
    p.insert_text((60, 120), "Tan Mei Ling  U1234567X  meiling@e.ntu.edu.sg", fontsize=14)
    p.insert_text((60, 600), "Thanks to Wei Jie for feedback. Low motivation was common.", fontsize=12)
    data = doc.tobytes()
    if path:
        Path(path).write_bytes(data)
    return data


def test_find_boxes_covers_names_matric_email(tmp_path):
    pdf = tmp_path / "p.pdf"
    _poster(pdf)
    found = redact.find_boxes(pdf, Deidentifier(ROSTER))
    assert found["has_text"] and len(found["pages"]) == 1
    boxes = found["pages"][0]
    with pymupdf.open(pdf) as doc:
        page = doc[0]
        def covered(text):
            for r in page.search_for(text):
                cx = (r.x0 + r.x1) / 2 / page.rect.width
                cy = (r.y0 + r.y1) / 2 / page.rect.height
                if not any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes):
                    return False
            return True
        assert covered("Tan Mei Ling") and covered("U1234567X")
        assert covered("meiling@e.ntu.edu.sg") and covered("Wei Jie")
        assert not covered("Sleep and Academic")       # title untouched
        assert not covered("motivation")


def test_render_redacted_paints_black(tmp_path):
    pdf = tmp_path / "p.pdf"
    _poster(pdf)
    box = [0.05, 0.08, 0.6, 0.11]
    jpg = redact.render_redacted(pdf, 1, [box])
    pix = pymupdf.Pixmap(jpg)
    assert max(pix.width, pix.height) == redact.SEND_LONG_SIDE
    x = int(0.3 * pix.width); y = int(0.095 * pix.height)
    assert sum(pix.pixel(x, y)) < 40                   # black inside the box


def test_image_only_pdf_has_no_text(tmp_path):
    pdf = tmp_path / "img.pdf"
    src = pymupdf.open(stream=_poster(), filetype="pdf")
    png = src[0].get_pixmap(dpi=40).tobytes("png")
    doc = pymupdf.open(); p = doc.new_page(); p.insert_image(p.rect, stream=png); doc.save(pdf)
    found = redact.find_boxes(pdf, Deidentifier(ROSTER))
    assert not found["has_text"] and found["pages"] == [[]]


def test_valid_box():
    assert redact.valid_box([0, 0, 0.5, 0.5])
    assert not redact.valid_box([0.5, 0, 0.4, 1]) and not redact.valid_box([0, 0, 1.2, 1])


# ------------------------------------------------------------- API

@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import main
    from app.settings import settings
    monkeypatch.setattr(settings, "config_dir", CONFIG)
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "together_api_key", "k")
    monkeypatch.setattr(settings, "together_model_text", "text/model")
    monkeypatch.setattr(settings, "together_model_vision", "vision/model")
    with TestClient(main.app) as c:
        c.post(f"/api/assignments/{AID}/import", data={"group": "TG03"},
               files=[("files", ("NIE TAN MEI LING_Poster" + SUFFIX, _poster(), "application/pdf"))])
        c.sid = c.get(f"/api/assignments/{AID}/students").json()[0]["id"]
        yield c


def test_redaction_api_and_design_review(client, monkeypatch):
    sid = client.sid
    r = client.get(f"/api/students/{sid}/redaction").json()
    assert r["has_text"] and len(r["pages"]) == 1 and len(r["pages"][0]["auto"]) >= 3

    r = client.put(f"/api/students/{sid}/redaction", json={"manual": [[[0.1, 0.5, 0.3, 0.6]]]}).json()
    assert r["pages"][0]["manual"] == [[0.1, 0.5, 0.3, 0.6]]
    assert client.put(f"/api/students/{sid}/redaction", json={"manual": [[[0.5, 0, 0.1, 1]]]}).status_code == 422

    client.put(f"/api/students/{sid}/assessment", json={"comment": "Mei Ling's layout is crowded."})
    p = client.post(f"/api/students/{sid}/ai/design-review/preview").json()
    assert p["model"] == "vision/model" and len(p["images"]) == 1
    assert "Mei Ling" not in json.dumps(p["messages"]) and "[image 1]" in p["messages"][1]["content"]
    img = client.get(p["images"][0])
    assert img.headers["content-type"] == "image/jpeg"
    pix = pymupdf.Pixmap(img.content)                  # manual box is black in the sent image
    assert sum(pix.pixel(int(0.2 * pix.width), int(0.55 * pix.height))) < 40

    sent = {}

    def fake(provider, model, messages, schema=None, max_tokens=0):
        sent["model"] = model
        sent["has_image"] = any(isinstance(m["content"], list) and
                                any(c.get("type") == "image_url" for c in m["content"]) for m in messages)
        return {"content": json.dumps({"dimensions": [
            {"id": "design", "marks": 3.6, "rationale": "Clear sections but dense text.", "evidence": ["two columns"]},
            {"id": "delivery", "marks": 6, "rationale": "should be ignored", "evidence": []}]}), "usage": {}}

    monkeypatch.setattr(ai, "call_chat", fake)
    client.post(f"/api/ai/requests/{p['request_id']}/send")
    d = client.post(f"/api/students/{sid}/ai/reveal").json()
    assert sent == {"model": "vision/model", "has_image": True}
    dims = {x["id"]: x for x in d["ai_marks"]["dimensions"]}
    assert list(dims) == ["design"] and dims["design"]["marks"] == "3.5" and dims["design"]["source"] == "image"

    # a later text suggestion keeps the image-based design mark
    def fake_text(provider, model, messages, schema=None, max_tokens=0):
        return {"content": json.dumps({"dimensions": [
            {"id": "delivery", "marks": 5, "rationale": "ok", "evidence": []},
            {"id": "design", "marks": None, "rationale": "no image", "evidence": []}]}), "usage": {}}
    monkeypatch.setattr(ai, "call_chat", fake_text)
    t = client.post(f"/api/students/{sid}/ai/suggest-marks/preview").json()
    d = client.post(f"/api/ai/requests/{t['request_id']}/send").json()
    assert d["ai_marks"]["hidden"] is True                     # a new suggestion is hidden again
    d = client.post(f"/api/students/{sid}/ai/reveal").json()
    dims = {x["id"]: x for x in d["ai_marks"]["dimensions"]}
    assert dims["design"]["marks"] == "3.5" and dims["delivery"]["marks"] == "5"
    assert d["ai_marks"]["model"] == "text/model + vision/model"


def test_design_review_needs_vision_model(client, monkeypatch):
    from app.settings import settings
    monkeypatch.setattr(settings, "together_model_vision", "")
    r = client.post(f"/api/students/{client.sid}/ai/design-review/preview")
    assert r.status_code == 422 and "vision" in r.json()["detail"]
    assert client.get("/api/health").json()["vision_configured"] is False
