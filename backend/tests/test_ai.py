"""De-identification and AI mark-suggestion tests. All names are invented; no network."""
import json
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from app import ai
from app.deid import Deidentifier, RosterEntry

CONFIG = Path(__file__).resolve().parents[2] / "config" / "assignments"
AID = "als10a-2026-poster"
SUFFIX = "_Submitted file_trn-oid---14504-100000001.pdf"

ROSTER = [
    RosterEntry("S01", "Tan Mei Ling", "TAN MEI LING", None, None, []),
    RosterEntry("S02", "Lim Wei Jie", "LIM WEI JIE", "LIN WEIJIE", "U1234567X", []),
    RosterEntry("S03", "Low Jia Hui", "LOW JIA HUI", None, None, []),
    RosterEntry("S04", "Nur Aisyah Binte Ismail", "NUR AISYAH BINTE ISMAIL", None, None, []),
    RosterEntry("S05", "Chan Jia Rui, Linus", "CHAN JIA RUI, LINUS", None, None, []),
]


@pytest.mark.parametrize("text,expected", [
    ("TAN MEI LING", "[S01]"),
    ("By Mei Ling Tan.", "By [S01]."),
    ("tan mei ling", "[S01]"),
    ("Thanks to Wei Jie", "Thanks to [S02]"),
    ("Linus Chan presented", "[S05] presented"),
    ("Lin Weijie (U1234567X)", "[S02] ([ID])"),
    ("Aisyah's poster", "[S04]'s poster"),
    ("Nur Aisyah Binte Ismail", "[S04]"),
    ("Low Jia Hui argued", "[S03] argued"),
    ("mail me at a.b@e.ntu.edu.sg or 9123 4567", "mail me at [EMAIL] or [PHONE]"),
])
def test_deidentify(text, expected):
    assert Deidentifier(ROSTER).deidentify(text).text == expected


@pytest.mark.parametrize("text", [
    "Students with low motivation",            # 'Low' is a surname but also a word
    "Schools in Hong Kong and China",
    "Tan et al. (2020) found",                 # ambiguous surname alone is kept
    "Nur and Siti are common names",           # common name parts alone are kept
    "The sample of 120 students in 2024",
])
def test_deidentify_leaves_ordinary_text(text):
    assert Deidentifier(ROSTER).deidentify(text).text == text


def test_residuals_catch_other_cases():
    d = Deidentifier(ROSTER)
    assert d.residuals("see weijie.blog and xmeilingx") == ["Weijie"]
    assert d.deidentify("meiling.tan@e.ntu.edu.sg").text == "[EMAIL]"
    assert d.deidentify("by meiling and WeiJie").text == "by [S01] and [S02]"
    assert d.residuals("nothing here, low motivation") == []


def test_reidentify():
    d = Deidentifier(ROSTER)
    assert d.reidentify("[S02] cites [S04]; [S99] unknown") == \
        "Lim Wei Jie cites Nur Aisyah Binte Ismail; [S99] unknown"


def test_validate_suggestion_snaps_and_clamps():
    from app.rubric import load_all
    a = load_all(CONFIG)[AID]
    out = ai.validate_suggestion(a, {"dimensions": [
        {"id": "delivery", "marks": 5.6, "rationale": "r", "evidence": ["e"]},
        {"id": "design", "marks": 9, "rationale": "r", "evidence": []},
        {"id": "analysis", "marks": None, "rationale": "not enough", "evidence": []},
        {"id": "bogus", "marks": 1, "rationale": "x", "evidence": []},
    ]})
    by = {d["id"]: d for d in out}
    assert by["delivery"]["marks"] == "5.5"
    assert by["design"]["marks"] == "4.5"
    assert by["analysis"]["marks"] is None
    assert by["literature"]["marks"] is None and "No suggestion" in by["literature"]["rationale"]
    assert "bogus" not in by


def test_parse_json_strips_thinking():
    assert ai.parse_json('<think>hmm</think>\n```json\n{"dimensions": []}\n```') == {"dimensions": []}


# ------------------------------------------------------------- API flow

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
        c.post(f"/api/assignments/{AID}/import", data={"group": "TG03"}, files=[
            ("files", ("NIE TAN MEI LING_Poster" + SUFFIX,
                       _pdf("Sleep and GPA\nTan Mei Ling U7654321Z\nWith thanks to Wei Jie."),
                       "application/pdf")),
            ("files", ("NIE LIM WEI JIE (LIN WEIJIE)_A1" + SUFFIX, _pdf("AI feedback"), "application/pdf")),
        ])
        yield c


def test_preview_is_deidentified_and_send_stores_result(client, monkeypatch):
    roster = {s["name"]: s for s in client.get(f"/api/assignments/{AID}/students").json()}
    sid, ps = roster["Tan Mei Ling"]["id"], roster["Tan Mei Ling"]["pseudonym"]
    other = roster["Lim Wei Jie"]["pseudonym"]
    client.put(f"/api/students/{sid}/assessment",
               json={"presentation_note": "• Mei Ling was engaging", "comment": "Layout clear"})

    p = client.post(f"/api/students/{sid}/ai/suggest-marks/preview").json()
    blob = json.dumps(p["messages"])
    for leak in ["Tan Mei Ling", "Mei Ling", "Wei Jie", "U7654321Z", "TAN MEI LING"]:
        assert leak not in blob, leak
    assert f"[{ps}]" in blob and f"[{other}]" in blob and p["replacements"] >= 4

    sent = {}

    def fake_call(provider, model, messages, schema=None, max_tokens=2500):
        sent["messages"] = messages
        return {"content": json.dumps({"dimensions": [
            {"id": "delivery", "marks": 5.25, "rationale": f"[{ps}] engaged the audience.",
             "evidence": [f"[{ps}] was engaging"]},
            {"id": "design", "marks": None, "rationale": "No image.", "evidence": []},
        ]}), "usage": {"prompt_tokens": 1000, "completion_tokens": 200}}

    monkeypatch.setattr(ai, "call_chat", fake_call)
    d = client.post(f"/api/ai/requests/{p['request_id']}/send").json()
    assert sent["messages"] == p["messages"]                      # exactly what was previewed
    dims = {x["id"]: x for x in d["ai_marks"]["dimensions"]}
    assert dims["delivery"]["marks"] == "5.25"
    assert dims["delivery"]["rationale"] == "Tan Mei Ling engaged the audience."  # re-identified locally
    assert dims["design"]["marks"] is None
    assert d["assessment"]["marks"] == {}                          # AI never sets marks by itself

    assert client.post(f"/api/ai/requests/{p['request_id']}/send").status_code == 409
    log = client.get("/api/ai/log").json()
    assert log[0]["status"] == "sent" and log[0]["usage"]["prompt_tokens"] == 1000


def test_failed_call_is_logged(client, monkeypatch):
    sid = client.get(f"/api/assignments/{AID}/students").json()[0]["id"]
    p = client.post(f"/api/students/{sid}/ai/suggest-marks/preview").json()

    def boom(*a, **k):
        raise ai.AIError("together returned 401: bad key")

    monkeypatch.setattr(ai, "call_chat", boom)
    r = client.post(f"/api/ai/requests/{p['request_id']}/send")
    assert r.status_code == 502 and "401" in r.json()["detail"]
    assert client.get("/api/ai/log").json()[0]["status"] == "failed"


def test_preview_refused_without_key(client, monkeypatch):
    from app.settings import settings
    monkeypatch.setattr(settings, "together_api_key", "__REPLACE_ME__together_api_key__")
    sid = client.get(f"/api/assignments/{AID}/students").json()[0]["id"]
    r = client.post(f"/api/students/{sid}/ai/suggest-marks/preview")
    assert r.status_code == 422 and "key" in r.json()["detail"]


def test_call_chat_request_shape_and_fallback(monkeypatch):
    from app.settings import settings
    monkeypatch.setattr(settings, "together_api_key", "k")
    monkeypatch.setattr(settings, "together_model_text", "m")
    seen = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        assert request.headers["authorization"] == "Bearer k"
        assert request.url.path.endswith("/chat/completions")
        if "response_format" in body:
            return __import__("httpx").Response(400, text="response_format not supported")
        return __import__("httpx").Response(200, json={
            "choices": [{"message": {"content": '{"dimensions": []}'}, "finish_reason": "stop"}],
            "usage": {"total_tokens": 5}})

    import httpx
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    out = ai.call_chat("together", "m", [{"role": "user", "content": "hi"}], schema=ai.SUGGEST_SCHEMA)
    assert out["content"] == '{"dimensions": []}'
    assert "response_format" in seen[0] and "response_format" not in seen[1]
