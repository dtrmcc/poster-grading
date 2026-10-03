from decimal import Decimal as D
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.rubric import load_all

CONFIG = Path(__file__).resolve().parents[2] / "config" / "assignments"
A = load_all(CONFIG)["als10a-2026-poster"]


def test_config_loads_and_is_consistent():
    assert A.assignment.max_marks == 30
    assert {d.id: d.max_marks for d in A.dimensions} == {
        "delivery": D(6), "understanding": D("4.5"), "literature": D(6),
        "citations_examples": D("4.5"), "analysis": D("4.5"), "design": D("4.5"),
    }


def test_worked_example():
    r = A.score({"delivery": D("5.5"), "understanding": D(4), "literature": D(5),
                 "citations_examples": D(4), "analysis": D("3.75"), "design": D(4)})
    assert r.complete and r.total_marks == D("26.25")
    assert r.percent == D("87.50") and r.rounded_percent == 88 and r.letter == "A-"


@pytest.mark.parametrize("pct,letter", [
    (D("94.5"), "A+"), (D("94.49"), "A"), (D(90), "A"), (D("89.9"), "A"),
    (D("84.5"), "A-"), (D(80), "B+"), (D(65), "C+"), (D(60), "C"),
    (D("54.5"), "D+"), (D(50), "D"), (D("49.4"), "F"), (D(0), "F"),
])
def test_letter_bands_round_half_up(pct, letter):
    assert A.letter_for_percent(pct) == letter


def test_dimension_letters():
    r = A.score({"understanding": D(4), "delivery": D(6)})
    by = {x.id: x for x in r.dimensions}
    assert by["understanding"].letter == "A-"     # 88.9%
    assert by["delivery"].letter == "A+"
    assert not r.complete and r.letter is None


@pytest.mark.parametrize("marks", [{"design": D("4.6")}, {"design": D("4.1")},
                                   {"design": D(-1)}, {"nope": D(1)}])
def test_invalid_marks_rejected(marks):
    with pytest.raises(ValueError):
        A.score(marks)


def test_api_score(monkeypatch):
    from app import main
    from app.settings import settings
    monkeypatch.setattr(settings, "config_dir", CONFIG)
    monkeypatch.setattr(settings, "data_dir", Path("/tmp/pg-test-data"))
    with TestClient(main.app) as c:
        assert c.get("/api/health").json()["assignments"] >= 1
        r = c.post("/api/assignments/als10a-2026-poster/score",
                   json={"marks": {"delivery": 6, "understanding": 4.5, "literature": 6,
                                   "citations_examples": 4.5, "analysis": 4.5, "design": 4.5}})
        assert r.status_code == 200 and r.json()["letter"] == "A+"
        assert c.post("/api/assignments/als10a-2026-poster/score",
                      json={"marks": {"design": 9}}).status_code == 422
