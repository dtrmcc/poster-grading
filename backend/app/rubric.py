"""Assignment configs (rubrics) and scoring.

An assignment is defined by a YAML file in config/assignments/. All arithmetic
uses Decimal so that e.g. 94.5% rounds half-up to 95 (Python's round() would
give 94 — banker's rounding).
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, model_validator

EvidenceSource = Literal[
    "poster", "poster_image", "presentation_note", "instructor_comment", "reference_check"
]


class AssignmentMeta(BaseModel):
    id: str
    course: str
    title: str
    groups: list[str]
    weight_in_course: Decimal
    max_marks: Decimal


class ScoringRules(BaseModel):
    mark_step: Decimal = Decimal("0.25")
    rounding: Literal["nearest_integer"] = "nearest_integer"


class GradeBand(BaseModel):
    letter: str
    min: Decimal


class Override(BaseModel):
    allowed: bool = True
    reason_required: bool = True


class Dimension(BaseModel):
    id: str
    name: str
    weight: Decimal
    max_marks: Decimal
    evidence: list[EvidenceSource]
    feedback_section: str
    guidance: Optional[str] = None
    descriptors: dict[str, Optional[str]]


class FeedbackSection(BaseModel):
    id: str
    title: str
    sources: list[EvidenceSource]


class FeedbackSpec(BaseModel):
    document_title: Optional[str] = None
    signed_by: Optional[str] = None
    sections: list[FeedbackSection]
    include_rubric: bool = True
    include_dimension_grades: bool = True
    show_marks_to_student: bool = False


class Assignment(BaseModel):
    assignment: AssignmentMeta
    scoring: ScoringRules = Field(default_factory=ScoringRules)
    grade_scale: list[GradeBand]
    override: Override = Field(default_factory=Override)
    dimensions: list[Dimension]
    feedback: FeedbackSpec

    @model_validator(mode="after")
    def _check_consistency(self) -> "Assignment":
        ids = [d.id for d in self.dimensions]
        if len(ids) != len(set(ids)):
            raise ValueError("dimension ids must be unique")
        if sum(d.weight for d in self.dimensions) != Decimal(100):
            raise ValueError("dimension weights must sum to 100")
        total = sum(d.max_marks for d in self.dimensions)
        if total != self.assignment.max_marks:
            raise ValueError(
                f"dimension max_marks sum to {total}, expected {self.assignment.max_marks}"
            )
        for d in self.dimensions:
            expected = self.assignment.max_marks * d.weight / 100
            if d.max_marks != expected:
                raise ValueError(f"{d.id}: max_marks {d.max_marks} != weight share {expected}")
            if d.max_marks % self.scoring.mark_step != 0:
                raise ValueError(f"{d.id}: max_marks not a multiple of mark_step")
        mins = [b.min for b in self.grade_scale]
        if mins != sorted(mins, reverse=True) or mins[-1] != 0:
            raise ValueError("grade_scale must be descending and end at min 0")
        section_ids = {s.id for s in self.feedback.sections}
        for d in self.dimensions:
            if d.feedback_section not in section_ids:
                raise ValueError(f"{d.id}: unknown feedback_section {d.feedback_section}")
        return self

    # ---- scoring -------------------------------------------------------

    def dimension(self, dim_id: str) -> Dimension:
        for d in self.dimensions:
            if d.id == dim_id:
                return d
        raise KeyError(dim_id)

    def letter_for_percent(self, percent: Decimal) -> str:
        rounded = percent.quantize(Decimal(1), rounding=ROUND_HALF_UP)
        for band in self.grade_scale:
            if rounded >= band.min:
                return band.letter
        return self.grade_scale[-1].letter

    def validate_mark(self, dim_id: str, marks: Decimal) -> None:
        d = self.dimension(dim_id)
        if marks < 0 or marks > d.max_marks:
            raise ValueError(f"{dim_id}: {marks} outside 0–{d.max_marks}")
        if marks % self.scoring.mark_step != 0:
            raise ValueError(f"{dim_id}: {marks} is not a multiple of {self.scoring.mark_step}")

    def score(self, marks: dict[str, Decimal]) -> "ScoreResult":
        unknown = set(marks) - {d.id for d in self.dimensions}
        if unknown:
            raise ValueError(f"unknown dimensions: {sorted(unknown)}")
        dims = []
        for d in self.dimensions:
            m = marks.get(d.id)
            if m is None:
                dims.append(DimensionScore(id=d.id, marks=None, max_marks=d.max_marks))
                continue
            m = Decimal(m)
            self.validate_mark(d.id, m)
            pct = m / d.max_marks * 100
            dims.append(
                DimensionScore(
                    id=d.id, marks=m, max_marks=d.max_marks,
                    percent=pct.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP),
                    letter=self.letter_for_percent(pct),
                )
            )
        complete = all(x.marks is not None for x in dims)
        total = sum((x.marks for x in dims if x.marks is not None), Decimal(0))
        result = ScoreResult(dimensions=dims, complete=complete, total_marks=total,
                             max_marks=self.assignment.max_marks)
        if complete:
            pct = total / self.assignment.max_marks * 100
            result.percent = pct.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            result.rounded_percent = int(pct.quantize(Decimal(1), rounding=ROUND_HALF_UP))
            result.letter = self.letter_for_percent(pct)
        return result


class DimensionScore(BaseModel):
    id: str
    marks: Optional[Decimal]
    max_marks: Decimal
    percent: Optional[Decimal] = None
    letter: Optional[str] = None


class ScoreResult(BaseModel):
    dimensions: list[DimensionScore]
    complete: bool
    total_marks: Decimal
    max_marks: Decimal
    percent: Optional[Decimal] = None
    rounded_percent: Optional[int] = None
    letter: Optional[str] = None


def load_assignment(path: Path) -> Assignment:
    with open(path, encoding="utf-8") as f:
        return Assignment.model_validate(yaml.safe_load(f))


def load_all(config_dir: Path) -> dict[str, Assignment]:
    out: dict[str, Assignment] = {}
    for p in sorted(config_dir.glob("*.yaml")):
        a = load_assignment(p)
        out[a.assignment.id] = a
    return out
