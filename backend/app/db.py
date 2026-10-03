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
