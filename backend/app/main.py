from contextlib import asynccontextmanager
from decimal import Decimal

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .rubric import Assignment, ScoreResult, load_all
from .settings import settings

_assignments: dict[str, Assignment] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    _assignments.clear()
    _assignments.update(load_all(settings.config_dir))
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Poster Grading", version="0.1.0", lifespan=lifespan)


def _get(assignment_id: str) -> Assignment:
    try:
        return _assignments[assignment_id]
    except KeyError:
        raise HTTPException(404, f"assignment {assignment_id} not found")


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "assignments": len(_assignments),
        "together_configured": settings.together_configured(),
        "require_send_confirmation": settings.require_send_confirmation,
    }


@app.get("/api/assignments")
def list_assignments() -> list[dict]:
    return [
        {"id": a.assignment.id, "course": a.assignment.course, "title": a.assignment.title,
         "groups": a.assignment.groups}
        for a in _assignments.values()
    ]


@app.get("/api/assignments/{assignment_id}", response_model=Assignment)
def get_assignment(assignment_id: str) -> Assignment:
    return _get(assignment_id)


class ScoreRequest(BaseModel):
    marks: dict[str, Decimal]


@app.post("/api/assignments/{assignment_id}/score", response_model=ScoreResult)
def score(assignment_id: str, req: ScoreRequest) -> ScoreResult:
    try:
        return _get(assignment_id).score(req.marks)
    except ValueError as e:
        raise HTTPException(422, str(e))
