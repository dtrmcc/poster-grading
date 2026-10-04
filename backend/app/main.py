import csv
import hashlib
import io
import json
import re
import zipfile
from contextlib import asynccontextmanager
from datetime import timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import ai, deid, importer, notes, redact
from . import feedback as fb
from .db import (AIReveal, AIRequest, Assessment, Database, Event, Export, Feedback, NoteBlock,
                 NoteImport, Redaction, Student, utcnow)
from .rubric import Assignment, ScoreResult, load_all
from .settings import settings

_assignments: dict[str, Assignment] = {}
_db: Optional[Database] = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _db
    _assignments.clear()
    _assignments.update(load_all(settings.config_dir))
    _db = Database(settings.data_dir)
    yield
    _db.engine.dispose()


app = FastAPI(title="Poster Grading", version="0.2.0", lifespan=lifespan)


def db() -> Session:
    yield from _db.session()


def _get(assignment_id: str) -> Assignment:
    try:
        return _assignments[assignment_id]
    except KeyError:
        raise HTTPException(404, f"assignment {assignment_id} not found")


def _log(s: Session, type_: str, st: Optional[Student] = None, assignment_id: Optional[str] = None,
         **data) -> None:
    """Append to the activity log (committed together with the caller's change)."""
    s.add(Event(type=type_, student_id=st.id if st else None,
                assignment_id=st.assignment_id if st else assignment_id, data=data))


def _student_dir(s: Student) -> Path:
    return settings.data_dir / "assignments" / s.assignment_id / s.group / f"student-{s.id}"


# ---------------------------------------------------------------- assignments

def _vision_ready() -> bool:
    try:
        ai.provider_config(settings.ai_task_design_review, "vision")
        return True
    except ai.AIError:
        return False


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "assignments": len(_assignments),
        "together_configured": settings.together_configured(),
        "vision_configured": _vision_ready(),
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


# ---------------------------------------------------------------- import

@app.post("/api/assignments/{assignment_id}/import")
async def import_submissions(
    assignment_id: str,
    group: str = Form(...),
    files: list[UploadFile] = File(...),
    s: Session = Depends(db),
) -> dict:
    a = _get(assignment_id)
    if group not in a.assignment.groups:
        raise HTTPException(422, f"group {group} not in {a.assignment.groups}")

    result = {"added": [], "updated": [], "unchanged": [], "errors": []}
    used = s.scalars(select(Student.pseudonym).where(Student.assignment_id == assignment_id)).all()
    next_no = max((int(p[1:]) for p in used), default=0) + 1

    for f in sorted(files, key=lambda f: f.filename or ""):
        name = Path(f.filename or "").name
        if not name.lower().endswith(".pdf"):
            continue
        try:
            parsed = importer.parse_filename(name)
        except ValueError as e:
            result["errors"].append({"file": name, "error": str(e)})
            continue
        data = await f.read()
        digest = importer.sha256(data)
        st = s.scalar(select(Student).where(
            Student.assignment_id == assignment_id, Student.group == group,
            Student.full_name == parsed.full_name))
        if st and st.sha256 == digest:
            result["unchanged"].append(st.display_name)
            continue
        is_new = st is None
        if is_new:
            st = Student(assignment_id=assignment_id, group=group, full_name=parsed.full_name,
                         pseudonym=f"S{next_no:02d}", assessment=Assessment())
            next_no += 1
            s.add(st)
        st.other_name = parsed.other_name
        st.display_name = parsed.display_name
        st.matric = parsed.matric
        st.name_tokens = parsed.name_tokens
        st.original_filename = name
        st.submission_id = parsed.submission_id
        st.sha256 = digest
        s.flush()  # assigns st.id

        d = _student_dir(st)
        d.mkdir(parents=True, exist_ok=True)
        (d / "submission.pdf").write_bytes(data)
        try:
            r = importer.render_pdf(d / "submission.pdf", d / "pages")
        except Exception as e:  # corrupt or encrypted PDF
            s.rollback()
            result["errors"].append({"file": name, "error": f"cannot read PDF: {e}"})
            continue
        st.pages, st.text = r.pages, r.text
        s.commit()
        result["added" if is_new else "updated"].append(st.display_name)
        _log(s, "poster_imported", st, new=is_new, sha256=digest, filename=name)
        s.commit()
    return result


# ---------------------------------------------------------------- students

def _iso(dt) -> Optional[str]:
    """Timestamps are stored in UTC; SQLite returns them without a zone."""
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _naive(dt):
    return dt.replace(tzinfo=None) if dt else dt


def _latest_export(st: Student) -> Optional[Export]:
    s = Session.object_session(st)
    return s.scalar(select(Export).where(Export.student_id == st.id).order_by(Export.id.desc()))


def _status(a: Assignment, st: Student) -> str:
    asm = st.assessment
    marks = {k: v for k, v in (asm.marks or {}).items() if v not in (None, "")}
    if len(marks) == len(a.dimensions):
        ex = _latest_export(st)
        if ex:
            f = Session.object_session(st).get(Feedback, st.id)
            last_change = max(filter(None, [_naive(asm.updated_at), _naive(f.updated_at) if f else None]))
            if _naive(ex.created_at) >= last_change:
                return "exported"
        return "marked"
    if marks or asm.comment.strip() or asm.presentation_note.strip():
        return "in_progress"
    return "not_started"


@app.get("/api/assignments/{assignment_id}/students")
def list_students(assignment_id: str, s: Session = Depends(db)) -> list[dict]:
    a = _get(assignment_id)
    rows = s.scalars(select(Student).where(Student.assignment_id == assignment_id)
                     .order_by(Student.group, func.lower(Student.display_name))).all()
    return [{"id": st.id, "group": st.group, "name": st.display_name,
             "pseudonym": st.pseudonym, "status": _status(a, st),
             "title": notes.poster_title(st.text, st.name_tokens)} for st in rows]


def _load(student_id: int, s: Session) -> Student:
    st = s.get(Student, student_id)
    if not st:
        raise HTTPException(404, "student not found")
    return st


def _detail(st: Student) -> dict:
    a = _get(st.assignment_id)
    asm = st.assessment
    marks = {k: Decimal(v) for k, v in (asm.marks or {}).items() if v not in (None, "")}
    return {
        "id": st.id, "group": st.group, "name": st.display_name, "full_name": st.full_name,
        "other_name": st.other_name, "pseudonym": st.pseudonym,
        "original_filename": st.original_filename, "pages": st.pages,
        "has_text": bool(st.text.strip()),
        "status": _status(a, st),
        "assessment": {
            "presentation_note": asm.presentation_note, "comment": asm.comment,
            "marks": {k: str(v) for k, v in marks.items()},
            "override_letter": asm.override_letter, "override_reason": asm.override_reason,
            "updated_at": _iso(asm.updated_at) if asm.updated_at else None,
        },
        "score": a.score(marks).model_dump(mode="json"),
        "ai_marks": _latest_suggestion(st),
    }


def _latest_sent(st: Student, task: str) -> Optional[AIRequest]:
    s = Session.object_session(st)
    return s.scalar(select(AIRequest).where(
        AIRequest.student_id == st.id, AIRequest.task == task,
        AIRequest.status == "sent").order_by(AIRequest.id.desc()))


def _suggestion_raw(st: Student) -> Optional[dict]:
    """Latest text-based suggestion, with image-based dimensions (design) taken from the
    latest design review when that review produced a mark."""
    text = _latest_sent(st, "suggest_marks")
    image = _latest_sent(st, "design_review")
    if not (text and text.result) and not (image and image.result):
        return None
    a = _get(st.assignment_id)
    dims = {d["id"]: dict(d, source="text") for d in (text.result["dimensions"] if text and text.result else [])}
    if image and image.result:
        for d in image.result["dimensions"]:
            if d["marks"] is not None or d["id"] not in dims or dims[d["id"]]["marks"] is None:
                dims[d["id"]] = dict(d, source="image")
    used = [r for r in (text, image) if r and r.result]
    sent = max(_naive(r.sent_at) for r in used if r.sent_at)
    return {"model": " + ".join(dict.fromkeys(r.model for r in used)), "sent_at": _iso(sent),
            "request_ids": sorted(r.id for r in used),
            "dimensions": [dims[d.id] for d in a.dimensions if d.id in dims]}


def _revealed(st: Student, request_ids: list[int]) -> Optional[AIReveal]:
    s = Session.object_session(st)
    for r in s.scalars(select(AIReveal).where(AIReveal.student_id == st.id).order_by(AIReveal.id.desc())):
        if set(request_ids) <= set(r.request_ids):
            return r
    return None


def _latest_suggestion(st: Student) -> Optional[dict]:
    """What the UI may see. In blind mode the marks stay hidden until revealed."""
    raw = _suggestion_raw(st)
    if not raw:
        return None
    rev = _revealed(st, raw["request_ids"])
    if settings.blind_ai_marks and not rev:
        return {"hidden": True, "model": raw["model"], "sent_at": raw["sent_at"],
                "request_ids": raw["request_ids"]}
    return dict(raw, hidden=False, revealed_at=_iso(rev.revealed_at) if rev else None,
                own_marks_at_reveal=rev.own_marks if rev else None)


@app.post("/api/students/{student_id}/ai/reveal")
def reveal_ai_marks(student_id: int, s: Session = Depends(db)) -> dict:
    """Blind marking: record the instructor's own marks, then show the AI's."""
    st = _load(student_id, s)
    a = _get(st.assignment_id)
    raw = _suggestion_raw(st)
    if not raw:
        raise HTTPException(404, "no AI suggestion to reveal")
    if not _revealed(st, raw["request_ids"]):
        own = dict(st.assessment.marks or {})
        s.add(AIReveal(student_id=st.id, request_ids=raw["request_ids"], own_marks=own))
        _log(s, "ai_marks_revealed", st, request_ids=raw["request_ids"], own_marks=own,
             own_marks_complete=len(own) == len(a.dimensions),
             ai_marks={d["id"]: d["marks"] for d in raw["dimensions"]})
        s.commit()
    return _detail(st)


@app.get("/api/students/{student_id}")
def get_student(student_id: int, s: Session = Depends(db)) -> dict:
    return _detail(_load(student_id, s))


@app.get("/api/students/{student_id}/pages/{page}.jpg")
def get_page(student_id: int, page: int, s: Session = Depends(db)) -> FileResponse:
    st = _load(student_id, s)
    p = _student_dir(st) / "pages" / f"page-{page}.jpg"
    if not p.exists():
        raise HTTPException(404, "page not found")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@app.get("/api/students/{student_id}/pdf")
def get_pdf(student_id: int, s: Session = Depends(db)) -> FileResponse:
    st = _load(student_id, s)
    return FileResponse(_student_dir(st) / "submission.pdf", media_type="application/pdf",
                        content_disposition_type="inline", filename=f"{st.display_name}.pdf")


class AssessmentUpdate(BaseModel):
    presentation_note: Optional[str] = None
    comment: Optional[str] = None
    marks: Optional[dict[str, Optional[Decimal]]] = None
    # What the instructor did in the UI since the last save, e.g.
    # [{"type": "use_ai_mark", "dims": ["design"]}] — recorded in the activity log.
    actions: Optional[list[dict]] = None


def _ai_visibility(st: Student) -> str:
    raw = _suggestion_raw(st)
    if not raw:
        return "none"
    if not settings.blind_ai_marks or _revealed(st, raw["request_ids"]):
        return "revealed"
    return "hidden"


@app.put("/api/students/{student_id}/assessment")
def update_assessment(student_id: int, upd: AssessmentUpdate, s: Session = Depends(db)) -> dict:
    st = _load(student_id, s)
    a = _get(st.assignment_id)
    asm = st.assessment
    changes: dict = {}
    if upd.marks is not None:
        clean = {k: v for k, v in upd.marks.items() if v is not None}
        try:
            a.score(clean)  # validates range, step and dimension ids
        except ValueError as e:
            raise HTTPException(422, str(e))
        new = {k: format(v.normalize(), "f") for k, v in clean.items()}
        old = {k: format(Decimal(v).normalize(), "f") for k, v in (asm.marks or {}).items()}
        mark_changes = {d: {"old": old.get(d), "new": new.get(d)} for d in set(old) | set(new)
                        if old.get(d) != new.get(d)}
        if mark_changes:
            changes["marks"] = mark_changes
        asm.marks = new
    for field in ("presentation_note", "comment"):
        val = getattr(upd, field)
        if val is not None and val != getattr(asm, field):
            changes[field] = {"old": getattr(asm, field), "new": val}
            setattr(asm, field, val)
    if changes or upd.actions:
        _log(s, "assessment_changed", st, changes=changes, actions=upd.actions or [],
             ai_suggestion=_ai_visibility(st))
    s.commit()
    return _detail(st)


# ---------------------------------------------------------------- presentation notes

def _note_import_out(ni: NoteImport, s: Session) -> dict:
    return {
        "id": ni.id, "group": ni.group, "filename": ni.filename,
        "imported_at": _iso(ni.imported_at),
        "applied_at": _iso(ni.applied_at) if ni.applied_at else None,
        "blocks": [{
            "id": b.id, "position": b.position, "text": b.text,
            "suggested_student_id": b.suggested_student_id,
            "suggestion_score": b.suggestion_score,
            "suggestion_confident": b.suggestion_confident,
            "student_id": b.student_id, "ignored": b.ignored,
            "looks_absent": notes.looks_absent(b.text),
        } for b in ni.blocks],
    }


def _latest_import(assignment_id: str, group: str, s: Session) -> Optional[NoteImport]:
    return s.scalar(select(NoteImport).where(
        NoteImport.assignment_id == assignment_id, NoteImport.group == group))


@app.post("/api/assignments/{assignment_id}/notes/import")
async def import_notes(
    assignment_id: str,
    group: str = Form(...),
    file: UploadFile = File(...),
    s: Session = Depends(db),
) -> dict:
    a = _get(assignment_id)
    if group not in a.assignment.groups:
        raise HTTPException(422, f"group {group} not in {a.assignment.groups}")
    data = await file.read()
    try:
        blocks = notes.split_blocks(notes.read_lines(data))
    except Exception as e:
        raise HTTPException(422, f"cannot read Word file: {e}")
    if not blocks:
        raise HTTPException(422, "no text found in the file")

    students = s.scalars(select(Student).where(
        Student.assignment_id == assignment_id, Student.group == group)).all()
    sugg = notes.suggest(blocks, {st.id: st.text for st in students})

    old = _latest_import(assignment_id, group, s)
    if old:
        s.delete(old)
        s.flush()
    ni = NoteImport(assignment_id=assignment_id, group=group,
                    filename=Path(file.filename or "notes.docx").name, sha256=importer.sha256(data))
    for i, (text, sg) in enumerate(zip(blocks, sugg)):
        ni.blocks.append(NoteBlock(
            position=i + 1, text=text,
            suggested_student_id=sg.student_id if sg else None,
            suggestion_score=sg.score if sg else None,
            suggestion_confident=bool(sg and sg.confident),
            student_id=sg.student_id if sg else None,
            ignored=notes.looks_absent(text),
        ))
    s.add(ni)
    s.commit()
    return _note_import_out(ni, s)


@app.get("/api/assignments/{assignment_id}/notes/{group}")
def get_notes(assignment_id: str, group: str, s: Session = Depends(db)) -> Optional[dict]:
    _get(assignment_id)
    ni = _latest_import(assignment_id, group, s)
    return _note_import_out(ni, s) if ni else None


class BlockMatch(BaseModel):
    block_id: int
    student_id: Optional[int] = None
    ignored: bool = False


class ApplyNotes(BaseModel):
    matches: list[BlockMatch]
    overwrite: bool = False


@app.post("/api/assignments/{assignment_id}/notes/{group}/apply")
def apply_notes(assignment_id: str, group: str, req: ApplyNotes, s: Session = Depends(db)) -> dict:
    _get(assignment_id)
    ni = _latest_import(assignment_id, group, s)
    if not ni:
        raise HTTPException(404, "no notes imported for this group")
    blocks = {b.id: b for b in ni.blocks}
    students = {st.id: st for st in s.scalars(select(Student).where(
        Student.assignment_id == assignment_id, Student.group == group)).all()}

    for m in req.matches:
        b = blocks.get(m.block_id)
        if not b:
            raise HTTPException(422, f"block {m.block_id} not in this import")
        if m.student_id is not None and m.student_id not in students:
            raise HTTPException(422, f"student {m.student_id} not in {group}")
        b.ignored = m.ignored
        b.student_id = None if m.ignored else m.student_id

    # Gather text per student (a student may have more than one block)
    per_student: dict[int, list[str]] = {}
    for b in ni.blocks:
        if b.student_id and not b.ignored:
            per_student.setdefault(b.student_id, []).append(b.text)

    report = {"applied": [], "unchanged": [], "kept_existing": [], "unmatched_blocks": 0}
    report["unmatched_blocks"] = sum(1 for b in ni.blocks if not b.student_id and not b.ignored)
    for sid, texts in per_student.items():
        st = students[sid]
        new = "\n\n".join(texts)
        cur = st.assessment.presentation_note.strip()
        if cur == new.strip():
            report["unchanged"].append(st.display_name)
        elif cur and not req.overwrite:
            report["kept_existing"].append(st.display_name)
        else:
            st.assessment.presentation_note = new
            report["applied"].append(st.display_name)
    ni.applied_at = utcnow()
    _log(s, "notes_applied", assignment_id=assignment_id, group=group, overwrite=req.overwrite,
         matches=[{"block": b.position, "student_id": b.student_id, "ignored": b.ignored,
                   "suggested": b.suggested_student_id} for b in ni.blocks], report=report)
    s.commit()
    return report


# ---------------------------------------------------------------- AI: mark suggestion

def _deidentifier(assignment_id: str, s: Session) -> deid.Deidentifier:
    students = s.scalars(select(Student).where(Student.assignment_id == assignment_id)).all()
    return deid.Deidentifier(deid.roster_from_students(students))


@app.post("/api/students/{student_id}/ai/suggest-marks/preview")
def preview_suggest_marks(student_id: int, s: Session = Depends(db)) -> dict:
    """Build the exact de-identified payload and store it. Nothing is sent yet."""
    st = _load(student_id, s)
    a = _get(st.assignment_id)
    provider = settings.ai_task_grade_suggest
    try:
        _, _, model = ai.provider_config(provider)
    except ai.AIError as e:
        raise HTTPException(422, str(e))
    d = _deidentifier(st.assignment_id, s)
    poster = d.deidentify(st.text)
    note = d.deidentify(st.assessment.presentation_note)
    comment = d.deidentify(st.assessment.comment)
    messages = ai.build_suggest_messages(a, poster.text, note.text, comment.text)
    blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    warnings = []
    if not st.text.strip():
        warnings.append("The poster has no extractable text; only your note and comments will be used.")
    if not st.assessment.presentation_note.strip():
        warnings.append("No presentation note: delivery cannot be suggested.")
    if not st.assessment.comment.strip():
        warnings.append("No comments yet: poster design will probably not be suggested.")
    leftovers = d.residuals(blob)
    if leftovers:
        warnings.append("Possible name remnants after de-identification (check before sending): "
                        + ", ".join(leftovers))
    req = AIRequest(student_id=st.id, task="suggest_marks", provider=provider, model=model,
                    payload={"messages": messages}, payload_sha256=hashlib.sha256(blob.encode()).hexdigest(),
                    replacements=len(poster.replacements) + len(note.replacements) + len(comment.replacements))
    s.add(req)
    s.flush()
    _log(s, "ai_request_prepared", st, request_id=req.id, task=req.task, model=req.model,
         replacements=req.replacements)
    s.commit()
    return {"request_id": req.id, "provider": provider, "model": model,
            "messages": messages, "replacements": req.replacements, "warnings": warnings,
            "residuals": leftovers,
            "require_confirmation": settings.require_send_confirmation}


@app.post("/api/ai/requests/{request_id}/send")
def send_ai_request(request_id: int, s: Session = Depends(db)) -> dict:
    """Send a previously previewed payload, unchanged, and store the result."""
    req = s.get(AIRequest, request_id)
    if not req:
        raise HTTPException(404, "request not found")
    if req.status == "sent":
        raise HTTPException(409, "this request was already sent")
    blob = json.dumps(req.payload["messages"], ensure_ascii=False, sort_keys=True)
    if hashlib.sha256(blob.encode()).hexdigest() != req.payload_sha256:
        raise HTTPException(409, "payload changed after preview; not sent")
    st = _load(req.student_id, s)
    a = _get(st.assignment_id)
    req.sent_at = utcnow()
    schema = fb.FEEDBACK_SCHEMA if req.task == "draft_feedback" else ai.SUGGEST_SCHEMA
    try:
        out = ai.call_chat(req.provider, req.model, req.payload["messages"], schema=schema,
                           max_tokens=3000 if req.task == "draft_feedback" else 2500)
        req.response_text = out["content"]
        req.usage = out["usage"]
        parsed = ai.parse_json(out["content"])
        if req.task == "suggest_marks":
            dims = ai.validate_suggestion(a, parsed)
        elif req.task == "design_review":
            wanted = {d.id for d in ai.image_dimensions(a)}
            dims = [d for d in ai.validate_suggestion(a, parsed) if d["id"] in wanted]
        else:
            poster_fb = str(parsed.get("poster") or "").strip()
            pres_fb = str(parsed.get("presentation") or "").strip()
            if not poster_fb and not pres_fb:
                raise ai.AIError("the model returned empty feedback")
    except ai.AIError as e:
        req.status, req.error = "failed", str(e)
        _log(s, "ai_request_failed", st, request_id=req.id, task=req.task, error=str(e))
        s.commit()
        raise HTTPException(502, str(e))
    except httpx.HTTPError as e:
        req.status, req.error = "failed", f"network error: {e}"
        _log(s, "ai_request_failed", st, request_id=req.id, task=req.task, error=f"network: {e}")
        s.commit()
        raise HTTPException(502, f"could not reach {req.provider}: {e}")
    d = _deidentifier(st.assignment_id, s)   # names come back only on this machine
    if req.task in ("suggest_marks", "design_review"):
        for dim in dims:
            dim["rationale"] = d.reidentify(dim["rationale"])
            dim["evidence"] = [d.reidentify(e) for e in dim["evidence"]]
        req.result = {"dimensions": dims}
    else:
        poster_fb, pres_fb = d.reidentify(poster_fb), d.reidentify(pres_fb)
        req.result = {"poster": poster_fb, "presentation": pres_fb}
        f = _feedback_row(st, s)
        f.ai_poster, f.ai_presentation, f.ai_request_id = poster_fb, pres_fb, req.id
        if not f.poster.strip():
            f.poster = poster_fb
        if not f.presentation.strip():
            f.presentation = pres_fb
    req.status = "sent"
    _log(s, "ai_result_received", st, request_id=req.id, task=req.task, model=req.model,
         result=req.result, blind=settings.blind_ai_marks and req.task != "draft_feedback")
    s.commit()
    return _detail(st)


@app.get("/api/ai/log")
def ai_log(limit: int = 50, s: Session = Depends(db)) -> list[dict]:
    rows = s.scalars(select(AIRequest).order_by(AIRequest.id.desc()).limit(limit)).all()
    return [{"id": r.id, "student_id": r.student_id, "task": r.task, "provider": r.provider,
             "model": r.model, "status": r.status, "replacements": r.replacements,
             "created_at": _iso(r.created_at),
             "sent_at": _iso(r.sent_at) if r.sent_at else None,
             "usage": r.usage, "error": r.error} for r in rows]


# ---------------------------------------------------------------- feedback

def _feedback_row(st: Student, s: Session) -> Feedback:
    f = s.get(Feedback, st.id)
    if not f:
        f = Feedback(student_id=st.id, poster="", presentation="")
        s.add(f)
        s.flush()
    return f


def _complete_score(st: Student):
    a = _get(st.assignment_id)
    marks = {k: Decimal(v) for k, v in (st.assessment.marks or {}).items() if v not in (None, "")}
    sc = a.score(marks)
    if not sc.complete:
        raise HTTPException(422, "enter marks for every criterion first")
    return a, sc


def _export_out(e: Export) -> dict:
    return {"id": e.id, "version": e.version, "letter": e.letter, "total_marks": e.total_marks,
            "created_at": _iso(e.created_at), "url": f"/api/exports/{e.id}/pdf"}


def _feedback_out(st: Student, s: Session) -> dict:
    f = s.get(Feedback, st.id)
    exports = s.scalars(select(Export).where(Export.student_id == st.id).order_by(Export.id.desc())).all()
    return {
        "poster": f.poster if f else "", "presentation": f.presentation if f else "",
        "ai_poster": f.ai_poster if f else None, "ai_presentation": f.ai_presentation if f else None,
        "updated_at": _iso(f.updated_at) if f and f.updated_at else None,
        "exports": [_export_out(e) for e in exports],
        "status": _status(_get(st.assignment_id), st),
    }


@app.get("/api/students/{student_id}/feedback")
def get_feedback(student_id: int, s: Session = Depends(db)) -> dict:
    return _feedback_out(_load(student_id, s), s)


class FeedbackUpdate(BaseModel):
    poster: Optional[str] = None
    presentation: Optional[str] = None
    actions: Optional[list[dict]] = None   # e.g. [{"type": "use_ai_draft", "section": "poster"}]


@app.put("/api/students/{student_id}/feedback")
def update_feedback(student_id: int, upd: FeedbackUpdate, s: Session = Depends(db)) -> dict:
    st = _load(student_id, s)
    f = _feedback_row(st, s)
    changes = {}
    for field in ("poster", "presentation"):
        val = getattr(upd, field)
        if val is not None and val != getattr(f, field):
            changes[field] = {"old": getattr(f, field), "new": val}
            setattr(f, field, val)
    if changes or upd.actions:
        _log(s, "feedback_changed", st, changes=changes, actions=upd.actions or [],
             ai_draft_request_id=f.ai_request_id)
    s.commit()
    return _feedback_out(st, s)


@app.post("/api/students/{student_id}/ai/draft-feedback/preview")
def preview_draft_feedback(student_id: int, s: Session = Depends(db)) -> dict:
    st = _load(student_id, s)
    a, sc = _complete_score(st)
    provider = settings.ai_task_feedback_write
    try:
        _, _, model = ai.provider_config(provider)
    except ai.AIError as e:
        raise HTTPException(422, str(e))
    d = _deidentifier(st.assignment_id, s)
    poster = d.deidentify(st.text)
    note = d.deidentify(st.assessment.presentation_note)
    comment = d.deidentify(st.assessment.comment)
    messages = fb.build_feedback_messages(a, sc, poster.text, note.text, comment.text)
    blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    warnings = []
    if not st.assessment.comment.strip():
        warnings.append("You have not written comments yet; the poster feedback will rely on the poster text only.")
    if not st.assessment.presentation_note.strip():
        warnings.append("No presentation note: the presentation section will be left empty.")
    leftovers = d.residuals(blob)
    if leftovers:
        warnings.append("Possible name remnants after de-identification (check before sending): "
                        + ", ".join(leftovers))
    req = AIRequest(student_id=st.id, task="draft_feedback", provider=provider, model=model,
                    payload={"messages": messages}, payload_sha256=hashlib.sha256(blob.encode()).hexdigest(),
                    replacements=len(poster.replacements) + len(note.replacements) + len(comment.replacements))
    s.add(req)
    s.flush()
    _log(s, "ai_request_prepared", st, request_id=req.id, task=req.task, model=req.model,
         replacements=req.replacements)
    s.commit()
    return {"request_id": req.id, "task": "draft_feedback", "provider": provider, "model": model,
            "messages": messages, "replacements": req.replacements, "warnings": warnings,
            "residuals": leftovers, "require_confirmation": settings.require_send_confirmation}


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9 ._-]+", "", name).strip().replace(" ", "_") or "student"


@app.post("/api/students/{student_id}/export")
def export_feedback(student_id: int, s: Session = Depends(db)) -> dict:
    st = _load(student_id, s)
    a, sc = _complete_score(st)
    f = s.get(Feedback, st.id)
    if not f or not (f.poster.strip() or f.presentation.strip()):
        raise HTTPException(422, "write or draft the feedback first")
    version = (s.scalar(select(func.max(Export.version)).where(Export.student_id == st.id)) or 0) + 1
    now = utcnow()
    rel = Path("exports") / st.assignment_id / st.group / f"{_safe(st.display_name)}_feedback_v{version}.pdf"
    out = settings.data_dir / rel
    body = fb.build_html(a, st.display_name, st.group, sc, f.poster, f.presentation, now.astimezone())
    fb.render_pdf(body, out, footer=f"{a.assignment.course} · {st.display_name} · {st.group}")
    e = Export(student_id=st.id, version=version, path=str(rel), sha256=importer.sha256(out.read_bytes()),
               letter=sc.letter, total_marks=format(sc.total_marks.normalize(), "f"), created_at=now)
    s.add(e)
    _log(s, "feedback_exported", st, version=version, letter=sc.letter,
         total=format(sc.total_marks.normalize(), "f"), marks=dict(st.assessment.marks or {}),
         poster_feedback=f.poster, presentation_feedback=f.presentation,
         ai_poster_draft=f.ai_poster, ai_presentation_draft=f.ai_presentation)
    s.commit()
    return _feedback_out(st, s)


@app.get("/api/exports/{export_id}/pdf")
def get_export(export_id: int, download: bool = False, s: Session = Depends(db)) -> FileResponse:
    e = s.get(Export, export_id)
    if not e:
        raise HTTPException(404, "export not found")
    p = settings.data_dir / e.path
    return FileResponse(p, media_type="application/pdf", filename=p.name,
                        content_disposition_type="attachment" if download else "inline")


@app.get("/api/assignments/{assignment_id}/exports.zip")
def export_zip(assignment_id: str, group: Optional[str] = None, s: Session = Depends(db)) -> Response:
    """Latest feedback PDF for every student (optionally one group)."""
    _get(assignment_id)
    q = select(Student).where(Student.assignment_id == assignment_id)
    if group:
        q = q.where(Student.group == group)
    buf = io.BytesIO()
    n = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for st in s.scalars(q.order_by(Student.group, Student.display_name)).all():
            e = _latest_export(st)
            if e and (settings.data_dir / e.path).exists():
                z.write(settings.data_dir / e.path, f"{st.group}/{Path(e.path).name}")
                n += 1
    if not n:
        raise HTTPException(404, "no feedback PDFs exported yet")
    name = f"{assignment_id}_{group or 'all'}_feedback.zip"
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@app.get("/api/assignments/{assignment_id}/marks.csv")
def marks_csv(assignment_id: str, group: Optional[str] = None, s: Session = Depends(db)) -> Response:
    a = _get(assignment_id)
    q = select(Student).where(Student.assignment_id == assignment_id)
    if group:
        q = q.where(Student.group == group)
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["group", "name", "matric"] + [f"{d.name} (/{d.max_marks.normalize():f})" for d in a.dimensions]
               + [f"total (/{a.assignment.max_marks.normalize():f})", "percent", "grade", "status"])
    for st in s.scalars(q.order_by(Student.group, Student.display_name)).all():
        marks = {k: Decimal(v) for k, v in (st.assessment.marks or {}).items() if v not in (None, "")}
        sc = a.score(marks)
        w.writerow([st.group, st.display_name, st.matric or ""]
                   + [format(marks[d.id].normalize(), "f") if d.id in marks else "" for d in a.dimensions]
                   + [format(sc.total_marks.normalize(), "f") if sc.complete else "",
                      sc.rounded_percent if sc.complete else "", sc.letter or "", _status(a, st)])
    name = f"{assignment_id}_{group or 'all'}_marks.csv"
    return Response("\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ---------------------------------------------------------------- redaction & design review

def _redaction(st: Student, s: Session, refresh: bool = False) -> Redaction:
    """Auto boxes are (re)computed when the poster file changes; manual boxes are kept."""
    r = s.get(Redaction, st.id)
    if r and r.pdf_sha256 == st.sha256 and not refresh:
        return r
    found = redact.find_boxes(_student_dir(st) / "submission.pdf", _deidentifier(st.assignment_id, s))
    if not r:
        r = Redaction(student_id=st.id, manual=[[] for _ in found["pages"]])
        s.add(r)
    r.auto, r.has_text, r.pdf_sha256 = found["pages"], found["has_text"], st.sha256
    manual = list(r.manual or [])
    r.manual = (manual + [[] for _ in found["pages"]])[:len(found["pages"])]
    s.commit()
    return r


def _redaction_out(st: Student, r: Redaction) -> dict:
    return {"pages": [{"page": i + 1, "auto": r.auto[i], "manual": r.manual[i],
                       "original": f"/api/students/{st.id}/pages/{i + 1}.jpg"}
                      for i in range(len(r.auto))],
            "has_text": r.has_text}


@app.get("/api/students/{student_id}/redaction")
def get_redaction(student_id: int, s: Session = Depends(db)) -> dict:
    st = _load(student_id, s)
    return _redaction_out(st, _redaction(st, s))


class RedactionUpdate(BaseModel):
    manual: list[list[list[float]]]   # per page, list of [x0, y0, x1, y1] (0..1)


@app.put("/api/students/{student_id}/redaction")
def update_redaction(student_id: int, upd: RedactionUpdate, s: Session = Depends(db)) -> dict:
    st = _load(student_id, s)
    r = _redaction(st, s)
    if len(upd.manual) != len(r.auto):
        raise HTTPException(422, f"expected boxes for {len(r.auto)} page(s)")
    for page in upd.manual:
        for b in page:
            if not redact.valid_box(b):
                raise HTTPException(422, f"invalid box {b}")
    r.manual = upd.manual
    s.commit()
    return _redaction_out(st, r)


@app.post("/api/students/{student_id}/ai/design-review/preview")
def preview_design_review(student_id: int, s: Session = Depends(db)) -> dict:
    """Redact the poster image(s) locally and store the exact request. Nothing is sent."""
    import base64
    st = _load(student_id, s)
    a = _get(st.assignment_id)
    if not ai.image_dimensions(a):
        raise HTTPException(422, "this assignment has no image-based criteria")
    provider = settings.ai_task_design_review
    try:
        _, _, model = ai.provider_config(provider, "vision")
    except ai.AIError as e:
        raise HTTPException(422, str(e))
    r = _redaction(st, s)
    pdf = _student_dir(st) / "submission.pdf"
    images = []
    for i in range(len(r.auto)):
        jpg = redact.render_redacted(pdf, i + 1, r.auto[i] + r.manual[i])
        images.append(base64.b64encode(jpg).decode())
    d = _deidentifier(st.assignment_id, s)
    comment = d.deidentify(st.assessment.comment)
    messages = ai.build_design_messages(a, comment.text, images)
    blob = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    boxes = sum(len(p) for p in r.auto) + sum(len(p) for p in r.manual)
    warnings = []
    if not r.has_text:
        warnings.append("This poster is an image-only PDF: names could not be found automatically. "
                        "Cover names, matric numbers and photos with boxes before sending, or untick the image.")
    elif not any(r.auto):
        warnings.append("No names were found on the poster automatically. Check the image and add boxes if needed.")
    req = AIRequest(student_id=st.id, task="design_review", provider=provider, model=model,
                    payload={"messages": messages}, payload_sha256=hashlib.sha256(blob.encode()).hexdigest(),
                    replacements=len(comment.replacements) + boxes)
    s.add(req)
    s.flush()
    _log(s, "ai_request_prepared", st, request_id=req.id, task=req.task, model=req.model,
         replacements=req.replacements)
    s.commit()
    shown, n = ai.strip_images(messages)
    return {"request_id": req.id, "task": "design_review", "provider": provider, "model": model,
            "messages": shown, "images": [f"/api/ai/requests/{req.id}/images/{k + 1}.jpg" for k in range(n)],
            "replacements": req.replacements, "boxes": boxes, "has_text": r.has_text,
            "warnings": warnings, "residuals": [],
            "require_confirmation": settings.require_send_confirmation}


@app.get("/api/ai/requests/{request_id}/images/{n}.jpg")
def ai_request_image(request_id: int, n: int, s: Session = Depends(db)) -> Response:
    """The exact image bytes stored in a request (what is, or was, sent)."""
    req = s.get(AIRequest, request_id)
    if not req:
        raise HTTPException(404, "request not found")
    try:
        data = ai.image_bytes(req.payload["messages"], n)
    except KeyError:
        raise HTTPException(404, "image not found")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- activity log (read-only)

@app.get("/api/events")
def list_events(student_id: Optional[int] = None, type: Optional[str] = None, limit: int = 200,
                s: Session = Depends(db)) -> list[dict]:
    q = select(Event).order_by(Event.id.desc()).limit(min(limit, 5000))
    if student_id is not None:
        q = q.where(Event.student_id == student_id)
    if type:
        q = q.where(Event.type == type)
    return [{"id": e.id, "ts": _iso(e.ts), "student_id": e.student_id, "type": e.type, "data": e.data}
            for e in s.scalars(q)]
