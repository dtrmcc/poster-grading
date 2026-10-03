from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import importer, notes
from .db import Assessment, Database, NoteBlock, NoteImport, Student, utcnow
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


def _student_dir(s: Student) -> Path:
    return settings.data_dir / "assignments" / s.assignment_id / s.group / f"student-{s.id}"


# ---------------------------------------------------------------- assignments

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
    return result


# ---------------------------------------------------------------- students

def _status(a: Assignment, st: Student) -> str:
    asm = st.assessment
    marks = {k: v for k, v in (asm.marks or {}).items() if v not in (None, "")}
    if len(marks) == len(a.dimensions):
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
            "updated_at": asm.updated_at.isoformat() if asm.updated_at else None,
        },
        "score": a.score(marks).model_dump(mode="json"),
    }


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


@app.put("/api/students/{student_id}/assessment")
def update_assessment(student_id: int, upd: AssessmentUpdate, s: Session = Depends(db)) -> dict:
    st = _load(student_id, s)
    a = _get(st.assignment_id)
    asm = st.assessment
    if upd.marks is not None:
        clean = {k: v for k, v in upd.marks.items() if v is not None}
        try:
            a.score(clean)  # validates range, step and dimension ids
        except ValueError as e:
            raise HTTPException(422, str(e))
        asm.marks = {k: str(v) for k, v in clean.items()}
    if upd.presentation_note is not None:
        asm.presentation_note = upd.presentation_note
    if upd.comment is not None:
        asm.comment = upd.comment
    s.commit()
    return _detail(st)


# ---------------------------------------------------------------- presentation notes

def _note_import_out(ni: NoteImport, s: Session) -> dict:
    return {
        "id": ni.id, "group": ni.group, "filename": ni.filename,
        "imported_at": ni.imported_at.isoformat(),
        "applied_at": ni.applied_at.isoformat() if ni.applied_at else None,
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
    s.commit()
    return report
