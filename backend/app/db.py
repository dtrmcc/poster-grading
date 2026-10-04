"""SQLite storage. The database file lives in DATA_DIR and is never committed."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Student(Base):
    __tablename__ = "student"
    __table_args__ = (UniqueConstraint("assignment_id", "group", "full_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[str] = mapped_column(String(100), index=True)
    group: Mapped[str] = mapped_column(String(20))
    full_name: Mapped[str] = mapped_column(String(200))
    other_name: Mapped[Optional[str]] = mapped_column(String(200))
    display_name: Mapped[str] = mapped_column(String(200))
    matric: Mapped[Optional[str]] = mapped_column(String(20))
    name_tokens: Mapped[list] = mapped_column(JSON, default=list)
    pseudonym: Mapped[str] = mapped_column(String(10))

    original_filename: Mapped[str] = mapped_column(Text)
    submission_id: Mapped[Optional[str]] = mapped_column(String(50))
    sha256: Mapped[str] = mapped_column(String(64))
    pages: Mapped[int] = mapped_column(default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)

    assessment: Mapped["Assessment"] = relationship(
        back_populates="student", uselist=False, cascade="all, delete-orphan"
    )


class Assessment(Base):
    __tablename__ = "assessment"

    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), primary_key=True)
    presentation_note: Mapped[str] = mapped_column(Text, default="")
    comment: Mapped[str] = mapped_column(Text, default="")
    marks: Mapped[dict] = mapped_column(JSON, default=dict)        # {dimension_id: "4.25"}
    override_letter: Mapped[Optional[str]] = mapped_column(String(3))
    override_reason: Mapped[Optional[str]] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    student: Mapped[Student] = relationship(back_populates="assessment")


class NoteImport(Base):
    """One uploaded presentation-note file for a group. Re-importing replaces it."""
    __tablename__ = "note_import"

    id: Mapped[int] = mapped_column(primary_key=True)
    assignment_id: Mapped[str] = mapped_column(String(100), index=True)
    group: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    imported_at: Mapped[datetime] = mapped_column(default=utcnow)
    applied_at: Mapped[Optional[datetime]] = mapped_column()

    blocks: Mapped[list["NoteBlock"]] = relationship(
        back_populates="note_import", cascade="all, delete-orphan", order_by="NoteBlock.position"
    )


class NoteBlock(Base):
    __tablename__ = "note_block"

    id: Mapped[int] = mapped_column(primary_key=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("note_import.id"), index=True)
    position: Mapped[int] = mapped_column()
    text: Mapped[str] = mapped_column(Text)
    suggested_student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("student.id"))
    suggestion_score: Mapped[Optional[float]] = mapped_column()
    suggestion_confident: Mapped[bool] = mapped_column(default=False)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("student.id"))
    ignored: Mapped[bool] = mapped_column(default=False)

    note_import: Mapped[NoteImport] = relationship(back_populates="blocks")


class AIRequest(Base):
    """Every payload prepared for an AI provider, and what came back (send log)."""
    __tablename__ = "ai_request"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), index=True)
    task: Mapped[str] = mapped_column(String(40))            # e.g. "suggest_marks"
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(200))
    payload: Mapped[dict] = mapped_column(JSON)               # exact request messages (de-identified)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    replacements: Mapped[int] = mapped_column(default=0)      # identifiers replaced locally
    status: Mapped[str] = mapped_column(String(20), default="previewed")  # previewed|sent|failed
    response_text: Mapped[Optional[str]] = mapped_column(Text)
    result: Mapped[Optional[dict]] = mapped_column(JSON)
    usage: Mapped[Optional[dict]] = mapped_column(JSON)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    sent_at: Mapped[Optional[datetime]] = mapped_column()


class Feedback(Base):
    """Current feedback text for a student (instructor-edited) and the last AI draft."""
    __tablename__ = "feedback"

    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), primary_key=True)
    poster: Mapped[str] = mapped_column(Text, default="")
    presentation: Mapped[str] = mapped_column(Text, default="")
    ai_poster: Mapped[Optional[str]] = mapped_column(Text)
    ai_presentation: Mapped[Optional[str]] = mapped_column(Text)
    ai_request_id: Mapped[Optional[int]] = mapped_column(ForeignKey("ai_request.id"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Export(Base):
    """An archived feedback PDF. Every export is kept (versioned)."""
    __tablename__ = "export"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), index=True)
    version: Mapped[int] = mapped_column()
    path: Mapped[str] = mapped_column(Text)            # relative to DATA_DIR
    sha256: Mapped[str] = mapped_column(String(64))
    letter: Mapped[Optional[str]] = mapped_column(String(3))
    total_marks: Mapped[Optional[str]] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Redaction(Base):
    """Boxes painted black on a student's poster image before it is sent to an AI model.
    Normalised coordinates per page; `auto` comes from the PDF text layer, `manual`
    from the instructor."""
    __tablename__ = "redaction"

    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), primary_key=True)
    auto: Mapped[list] = mapped_column(JSON, default=list)       # [[box, …] per page]
    manual: Mapped[list] = mapped_column(JSON, default=list)     # [[box, …] per page]
    has_text: Mapped[bool] = mapped_column(default=True)
    pdf_sha256: Mapped[str] = mapped_column(String(64), default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AIReveal(Base):
    """The instructor revealed hidden AI mark suggestions (blind marking).
    `own_marks` are the instructor's marks at that moment, before seeing the AI."""
    __tablename__ = "ai_reveal"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("student.id"), index=True)
    request_ids: Mapped[list] = mapped_column(JSON, default=list)
    own_marks: Mapped[dict] = mapped_column(JSON, default=dict)
    revealed_at: Mapped[datetime] = mapped_column(default=utcnow)


class Event(Base):
    """Append-only activity log (self-review now; research later, with ethics approval).
    Never updated or deleted by the app."""
    __tablename__ = "event"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    assignment_id: Mapped[Optional[str]] = mapped_column(String(100), index=True)
    student_id: Mapped[Optional[int]] = mapped_column(ForeignKey("student.id"), index=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Database:
    def __init__(self, data_dir: Path):
        data_dir.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(
            f"sqlite:///{data_dir / 'grading.sqlite3'}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(self.engine)
        self._session = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Iterator[Session]:
        with self._session() as s:
            yield s
